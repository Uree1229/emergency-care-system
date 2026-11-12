import test from 'node:test';
import assert from 'node:assert/strict';
import {VoiceClient, normalizeServer} from '../../paramedic-ui/voice-client.mjs';
import {QuickSession} from '../../paramedic-ui/quick-session.mjs';

function setup(handler) {
  const calls=[];let id=0;
  const client=new VoiceClient({server:'http://127.0.0.1:8000',actorId:'crew-test',makeId:()=>`key-${++id}`,
    fetchImpl:async(url,request)=>{const call={url,method:request.method,body:request.body?JSON.parse(request.body):null};calls.push(call);return handler(call,calls.length);}});
  return {client,calls};
}
const response=(data,status=200)=>({ok:status<400,status,json:async()=>data});
const session=version=>({id:'VS-1',version,state:'DRAFT',fields:{}});

test('queued final utterances use the previous response version rather than an early snapshot',async()=>{
  let version=1;
  const {client,calls}=setup(async call=>{await new Promise(r=>setTimeout(r,5));assert.equal(call.body.expected_version,version);return response({session:session(++version)});});
  client.session=session(1);
  await Promise.all([client.write('transcripts',{text:'first'}),client.write('transcripts',{text:'second'})]);
  assert.deepEqual(calls.map(c=>c.body.expected_version),[1,2]);assert.equal(client.session.version,3);
});
test('uncertain network result blocks new edits and retry preserves original key/body/version',async()=>{
  let first=true;
  const {client,calls}=setup(call=>{if(first){first=false;throw Error('response lost');}return response({session:session(2)});});
  client.session=session(1);
  await assert.rejects(client.write('fields',{changes:{age:60}}),e=>e.uncertain);
  await assert.rejects(client.write('fields',{changes:{age:61}}),e=>e.code==='PENDING_REQUEST');
  assert.equal(calls.length,1);
  await client.retry();assert.deepEqual(calls[0],calls[1]);assert.equal(client.pending,null);
});
test('409 clears pending but does not replace expected_version and automatically reapply stale fields',async()=>{
  const {client,calls}=setup(()=>response({error:'VOICE_VERSION_CONFLICT',message:'reload'},409));
  client.session=session(1);
  await assert.rejects(client.write('fields',{changes:{age:61}}),e=>e.status===409);
  assert.equal(client.pending,null);assert.equal(calls.length,1);assert.equal(client.session.version,1);
});
test('historical duplicate snapshot is refreshed before another edit',async()=>{
  const {client,calls}=setup(call=>call.method==='GET'?response({session:session(5)}):response({session:session(2),duplicate:true}));
  client.session=session(1);await client.write('fields',{changes:{age:61}});
  assert.equal(client.session.version,5);assert.equal(calls[1].method,'GET');
});

test('lost response and failed duplicate refresh keep the exact job until fresh GET succeeds',async()=>{
  const committed=new Map();let writes=0,refreshes=0;
  const {client,calls}=setup(call=>{
    if(call.method==='GET'){
      if(++refreshes===1)throw Error('synthetic refresh network failure');
      return response({session:session(5)});
    }
    const key=call.body.client_request_id;
    if(!committed.has(key)){
      writes++;
      committed.set(key,{session:session(2)});
      throw Error('synthetic lost response after commit');
    }
    return response({...committed.get(key),duplicate:true});
  });
  const original=session(1);client.session=original;
  const flow=new QuickSession(client,()=> 'synthetic-utterance');
  await assert.rejects(flow.apply('synthetic clinical note','dictation'),e=>e.uncertain);
  const pending=client.pending,job=flow.job;
  await assert.rejects(flow.retry(),e=>e.uncertain&&e.code==='DUPLICATE_REFRESH_REQUIRED');
  assert.equal(client.pending,pending,'the original write remains pending');
  assert.equal(flow.job,job,'QuickSession must not discard a committed-but-unrefreshed job');
  assert.equal(client.session,original,'a historical duplicate is never presented as the current draft');
  await assert.rejects(client.write('fields',{changes:{pulse:91}}),e=>e.code==='PENDING_REQUEST');
  assert.equal(calls.length,3,'new edits are blocked without another request');
  await flow.retry();
  assert.deepEqual(calls[0],calls[1]);assert.deepEqual(calls[0],calls[3]);
  assert.equal(writes,1,'idempotent retries do not commit another utterance');
  assert.equal(refreshes,2);assert.equal(client.session.version,5);
  assert.equal(client.pending,null);assert.equal(flow.job,null);
});

for(const [name,failedRefresh] of [
  ['HTTP 404',()=>response({error:'VOICE_NOT_FOUND'},404)],
  ['HTTP 503',()=>response({error:'SERVER_UNAVAILABLE'},503)],
  ['invalid JSON',()=>({ok:true,status:200,json:async()=>{throw Error('bad json');}})],
  ['missing session',()=>response({})],
  ['wrong session',()=>response({session:{...session(5),id:'VS-other'}})],
  ['invalid version',()=>response({session:{...session(5),version:0}})],
])test(`duplicate refresh ${name} keeps the original request pending and can be retried`,async()=>{
  let refreshes=0;
  const {client,calls}=setup(call=>call.method==='GET'?
    ++refreshes===1?failedRefresh():response({session:session(5)}):
    response({session:session(2),duplicate:true}));
  const original=session(1);client.session=original;
  await assert.rejects(client.write('transcripts',{text:'synthetic note',utterance_id:'synthetic-id'}),
    e=>e.uncertain&&e.code==='DUPLICATE_REFRESH_REQUIRED');
  assert.ok(client.pending);assert.equal(client.session,original);
  await client.retry();
  assert.deepEqual(calls[0],calls[2]);assert.equal(client.pending,null);assert.equal(client.session.version,5);
});

test('a duplicate create with a failed refresh keeps its original request and no guessed session',async()=>{
  let refreshes=0;
  const {client,calls}=setup(call=>call.method==='GET'?
    ++refreshes===1?response({error:'SERVER_UNAVAILABLE'},503):response({session:session(3)}):
    response({session:session(1),duplicate:true}));
  await assert.rejects(client.create(),e=>e.uncertain);
  assert.ok(client.pending);assert.equal(client.session,null);
  await client.retry();
  assert.deepEqual(calls[0],calls[2]);assert.equal(client.pending,null);assert.equal(client.session.version,3);
});
test('patient create retry does not generate a second request id',async()=>{
  let first=true;
  const {client,calls}=setup(()=>{if(first){first=false;throw Error('lost create');}return response({session:session(1)});});
  await assert.rejects(client.create());await client.retry();assert.deepEqual(calls[0].body,calls[1].body);
});
test('submit waits for explicit confirmation caller and passes no hidden auto inquiry setting',async()=>{
  const {client,calls}=setup(()=>response({session:{...session(2),state:'SUBMITTED'}}));
  client.session={...session(2),confirmed:true};
  await client.write('submit',{context:{consent:true,lat:35,lon:126},start_inquiry:false});
  assert.equal(calls[0].body.start_inquiry,false);assert.equal(calls[0].body.expected_version,2);
  await assert.rejects(client.write('fields',{changes:{age:60}}));
});
test('server address disallows embedded credentials and non HTTP schemes',()=>{
  assert.equal(normalizeServer('http://localhost:18031/'),'http://localhost:18031');
  for(const value of ['file:///tmp','https://user:pass@example.com','http://example.com/x','http://example.com/?x=1'])assert.throws(()=>normalizeServer(value));
});

test('beforeWrite checkpoints a cloned pending request synchronously before network dispatch',async()=>{
  const events=[],snapshots=[];
  const {client,calls}=setup(call=>{events.push('network');return response({session:session(2)});});
  client.session=session(1);
  client.beforeWrite=request=>{
    events.push('checkpoint');snapshots.push(structuredClone(request));
    assert.ok(client.pending);assert.equal(client.hasQueuedRequests,true);
    request.body.text='a hook cannot mutate the actual request';
  };
  await client.write('transcripts',{text:'synthetic original',utterance_id:'synthetic-utterance',is_final:true,mode:'dictation'});
  assert.deepEqual(events,['checkpoint','network']);
  assert.equal(calls[0].body.text,'synthetic original');
  assert.deepEqual(snapshots[0].body,calls[0].body);
  assert.equal(client.pending,null);assert.equal(client.hasQueuedRequests,false);
});

test('a failed write-ahead checkpoint sends no API request and permits only an exact retry',async()=>{
  let available=false;const checkpoints=[];
  const {client,calls}=setup(()=>response({session:session(2)}));
  client.session=session(1);
  client.beforeWrite=request=>{checkpoints.push(request);if(!available)throw Error('synthetic storage quota failure');};
  await assert.rejects(client.write('fields',{changes:{age:60}}),e=>e.code==='RECOVERY_CHECKPOINT_FAILED'&&e.uncertain);
  const pending=structuredClone(client.pending);
  assert.equal(calls.length,0);assert.equal(client.session.version,1);
  await assert.rejects(client.write('fields',{changes:{age:61}}),e=>e.code==='PENDING_REQUEST');
  available=true;await client.retry();
  assert.equal(calls.length,1);assert.deepEqual(calls[0].body,pending.body);
  assert.deepEqual(checkpoints[0],checkpoints[1]);assert.equal(client.pending,null);
});

test('an asynchronous beforeWrite hook fails closed instead of dispatching without a durable checkpoint',async()=>{
  let calls=0;
  const client=new VoiceClient({server:'http://127.0.0.1:8000',actorId:'crew-test',makeId:()=> 'checkpoint-id',
    beforeWrite:()=>Promise.resolve(),fetchImpl:async()=>{calls++;return response({session:session(1)});}});
  await assert.rejects(client.create(),e=>e.code==='RECOVERY_CHECKPOINT_FAILED'&&e.uncertain);
  assert.equal(calls,0);assert.ok(client.pending);assert.equal(client.session,null);
});

for(const [method,status,kind] of [
  ['GET',200,'reachable'],['POST',201,'reachable'],['GET',401,'reachable'],['POST',403,'reachable'],
  ['PATCH',409,'reachable'],['POST',422,'reachable'],['GET',500,'service_error'],['POST',503,'service_error'],
])test(`API observer reports ${method} HTTP ${status} exactly once as ${kind}`,async()=>{
  const observed=[];
  const client=new VoiceClient({server:'http://127.0.0.1:8000',actorId:'PRIVATE_ACTOR',onApiResult:info=>observed.push(info),
    fetchImpl:async()=>response(status<400?{privateResult:'PRIVATE_RAW'}:{error:'PRIVATE_CODE',message:'PRIVATE_MESSAGE'},status)});
  const operation=client.api('/PRIVATE_PATH?actor_id=PRIVATE_ACTOR',method,{privateBody:'PRIVATE_RAW'});
  if(status<400)assert.deepEqual(await operation,{privateResult:'PRIVATE_RAW'});
  else await assert.rejects(operation,e=>e.status===status&&e.code==='PRIVATE_CODE'&&e.uncertain===(method!=='GET'&&status>=500));
  assert.deepEqual(observed,[{method,status,kind}]);
  assert.deepEqual(Object.keys(observed[0]).sort(),['kind','method','status']);
  assert.doesNotMatch(JSON.stringify(observed),/PRIVATE|actor|session|path|url|body|message/u);
});

for(const [method,status] of [['GET',200],['POST',200],['GET',503],['PATCH',403]])test(`API observer classifies ${method} malformed JSON HTTP ${status} without a second event`,async()=>{
  const observed=[];
  const client=new VoiceClient({server:'http://127.0.0.1:8000',actorId:'crew-test',onApiResult:info=>observed.push(info),
    fetchImpl:async()=>({status,ok:status<400,json:async()=>{throw Error('synthetic parser failure');}})});
  await assert.rejects(client.api('/api/test',method),e=>e.code==='INVALID_RESPONSE'&&e.status===status&&e.uncertain===(method!=='GET'));
  assert.deepEqual(observed,[{method,status,kind:'invalid_response'}]);
});

for(const method of ['GET','POST','PATCH'])test(`API observer classifies a rejected ${method} fetch once without changing uncertainty`,async()=>{
  const observed=[];
  const client=new VoiceClient({server:'http://127.0.0.1:8000',actorId:'crew-test',onApiResult:info=>observed.push(info),
    fetchImpl:async()=>{throw Error('synthetic network failure');}});
  await assert.rejects(client.api('/api/test',method),e=>e.code==='NETWORK'&&e.status===0&&e.uncertain===(method!=='GET'));
  assert.deepEqual(observed,[{method,status:0,kind:'network_error'}]);
});

for(const asyncFailure of [false,true])test(`a ${asyncFailure?'rejected asynchronous':'throwing synchronous'} observer cannot change result, pending or serialized exact retry`,async()=>{
  const observed=[];let failed=false;
  const {client,calls}=setup(call=>{
    if(!failed){failed=true;throw Error('synthetic lost write response');}
    return response({session:session(2)});
  });
  client.session=session(1);
  client.onApiResult=info=>{observed.push(info);if(asyncFailure)return Promise.reject(Error('observer failure'));throw Error('observer failure');};
  await assert.rejects(client.write('fields',{changes:{pulse:91}}),e=>e.code==='NETWORK'&&e.uncertain);
  const pending=client.pending;await client.retry();
  assert.deepEqual(calls[0],calls[1]);assert.equal(client.pending,null);assert.equal(client.session.version,2);
  assert.deepEqual(observed,[{method:'POST',status:0,kind:'network_error'},{method:'POST',status:200,kind:'reachable'}]);
  assert.equal(pending.body.expected_version,1);await new Promise(resolve=>setImmediate(resolve));
});

test('a throwing observer cannot replace a definite API error or keep its request pending',async()=>{
  const {client,calls}=setup(()=>response({error:'VOICE_VERSION_CONFLICT',message:'synthetic conflict'},409));client.session=session(1);
  client.onApiResult=()=>{throw Error('observer failure');};
  await assert.rejects(client.write('fields',{changes:{pulse:91}}),e=>e.code==='VOICE_VERSION_CONFLICT'&&e.status===409&&!e.uncertain);
  assert.equal(calls.length,1);assert.equal(client.pending,null);assert.equal(client.session.version,1);
});

test('checkpoint failure emits no API observer event because nothing was dispatched',async()=>{
  const observed=[];let calls=0;
  const client=new VoiceClient({server:'http://127.0.0.1:8000',actorId:'crew-test',makeId:()=> 'checkpoint-observer',
    beforeWrite:()=>{throw Error('synthetic quota failure');},onApiResult:info=>observed.push(info),
    fetchImpl:async()=>{calls++;return response({session:session(1)});}});
  await assert.rejects(client.create(),e=>e.code==='RECOVERY_CHECKPOINT_FAILED'&&e.uncertain);
  assert.equal(calls,0);assert.deepEqual(observed,[]);assert.ok(client.pending);
});

test('duplicate POST and failed fresh GET emit their own ordered events while retaining the exact pending request',async()=>{
  const observed=[];let writes=0,gets=0;
  const {client,calls}=setup(call=>{
    if(call.method==='GET')return ++gets===1?response({error:'UNAVAILABLE'},503):response({session:session(5)});
    if(++writes===1)throw Error('synthetic response loss');
    return response({session:session(2),duplicate:true});
  });
  client.session=session(1);client.onApiResult=info=>observed.push(info);
  await assert.rejects(client.write('transcripts',{text:'synthetic note',utterance_id:'synthetic-utterance'}),e=>e.uncertain);
  const pending=client.pending;
  await assert.rejects(client.retry(),e=>e.code==='DUPLICATE_REFRESH_REQUIRED'&&e.uncertain);
  assert.equal(client.pending,pending);assert.equal(client.session.version,1);
  await client.retry();
  assert.deepEqual(calls[0],calls[1]);assert.deepEqual(calls[0],calls[3]);
  assert.deepEqual(observed,[{method:'POST',status:0,kind:'network_error'},
    {method:'POST',status:200,kind:'reachable'},{method:'GET',status:503,kind:'service_error'},
    {method:'POST',status:200,kind:'reachable'},{method:'GET',status:200,kind:'reachable'}]);
  assert.equal(client.pending,null);assert.equal(client.session.version,5);
});

test('API observer constructor input accepts only null or a function',()=>{
  const options={server:'http://127.0.0.1:8000',actorId:'crew-test',fetchImpl:async()=>response({})};
  for(const value of ['',false,0,{},[],Promise.resolve()])assert.throws(()=>new VoiceClient({...options,onApiResult:value}));
  assert.doesNotThrow(()=>new VoiceClient({...options,onApiResult:null}));
  assert.doesNotThrow(()=>new VoiceClient({...options,onApiResult:()=>{}}));
});

test('a local body serialization failure emits no communication event before fetch is attempted',async()=>{
  const observed=[];let calls=0;const body={};body.self=body;
  const client=new VoiceClient({server:'http://127.0.0.1:8000',actorId:'crew-test',onApiResult:info=>observed.push(info),
    fetchImpl:async()=>{calls++;return response({});}});
  await assert.rejects(client.api('/api/test','POST',body),e=>e.code==='NETWORK'&&e.uncertain,'retain the legacy API error contract');
  assert.equal(calls,0);assert.deepEqual(observed,[]);
});
