import test from 'node:test';
import assert from 'node:assert/strict';
import {MainRecovery,findMainRecovery,emptyMainInput,MAIN_RECOVERY_PREFIX,MAIN_RECOVERY_TTL,MAIN_RECOVERY_MAX_BYTES} from './paramedic-ui/main-recovery.mjs';
import {VoiceClient} from './paramedic-ui/voice-client.mjs';

class Storage {
  constructor(){this.map=new Map();this.fail=false;this.race=false;}
  get length(){return this.map.size;}
  key(i){return [...this.map.keys()][i]??null;}
  getItem(key){return this.map.get(key)??null;}
  setItem(key,value){if(this.fail)throw Error('synthetic quota');this.map.set(key,this.race?value+' ':value);}
}
const scope={pageOrigin:'http://ui.synthetic',serverOrigin:'http://api.synthetic',actorId:'CREW-SYNTHETIC'};
const session=(extra={})=>({id:'VS-SYNTHETIC',actor_id:scope.actorId,patient_id:null,version:2,state:'DRAFT',fields:{pulse:90},...extra});
const transcript=()=>({path:'/api/voice/sessions/VS-SYNTHETIC/transcripts',method:'POST',body:{utterance_id:'U-SYNTHETIC',text:'합성 맥박 105',is_final:true,mode:'dictation',actor_id:scope.actorId,client_request_id:'REQUEST-SYNTHETIC',expected_version:2}});
function fixture(extra={}){
  const storage=extra.storage||new Storage();let id=0;let now=1000;
  const make=options=>new MainRecovery({...scope,storage,makeId:()=>`ID-${++id}`,now:()=>now,...options});
  const manager=make(extra);return {storage,manager,make,setNow:value=>now=value};
}
function select(storage,extra={}){return findMainRecovery(storage,{...scope,...extra},{now:1000});}
function clientFixture({response='loss',storageFailure=false}={}){
  const f=fixture(),calls=[];f.manager.update({session:session()});
  const client=new VoiceClient({server:scope.serverOrigin,actorId:scope.actorId,makeId:()=> 'REQUEST-SYNTHETIC',
    beforeWrite:request=>f.manager.beforeWrite(request,{session:client.session,ui:{...emptyMainInput(),transcript:'합성 맥박 105'}}),
    fetchImpl:async(url,options)=>{calls.push({url,...options});if(response==='loss')throw Error('synthetic response lost');return {ok:response!=='409',status:response==='409'?409:200,json:async()=>response==='409'?{error:'VERSION_CONFLICT',message:'synthetic stale'}:{session:session({version:3}),duplicate:false}};}});
  client.session=session();f.storage.fail=storageFailure;return {...f,client,calls};
}

test('write-ahead record preserves exact path method body order request/version/utterance IDs and raw input',()=>{
  const f=fixture(),request=transcript(),before=JSON.stringify(request),ui={...emptyMainInput(),transcript:'원문 그대로\n정정과 부정',command:'수정해',baseVersion:2};
  f.manager.beforeWrite(request,{session:session(),ui});const stored=select(f.storage).record;
  assert.equal(JSON.stringify(stored.pending),before);assert.equal(stored.ui.transcript,ui.transcript);assert.equal(stored.version,2);
  request.body.text='later mutation';assert.equal(stored.pending.body.text,'합성 맥박 105');
});
test('restoring current record performs no HTTP and only returns the original pending request',()=>{
  const f=fixture();f.manager.beforeWrite(transcript(),{session:session()});const selected=select(f.storage),restored=f.make({selected});
  assert.deepEqual(restored.record.pending,transcript());assert.equal(restored.record.sessionId,'VS-SYNTHETIC');assert.equal(restored.record.outcome,'pending');
});
test('same actor and patient lookup survives A to B to A without overwriting the other records',()=>{
  const f=fixture({patientId:'P-A'});f.manager.update({session:session({patient_id:'P-A'})});
  const b=f.make({actorId:'CREW-B',patientId:'P-B'});b.update({session:session({id:'VS-B',actor_id:'CREW-B',patient_id:'P-B'})});
  const otherPatient=f.make({patientId:'P-C'});otherPatient.update({session:session({id:'VS-C',patient_id:'P-C'})});
  assert.equal(select(f.storage,{patientId:'P-A'}).record.sessionId,'VS-SYNTHETIC');
  assert.equal(select(f.storage,{actorId:'CREW-B',patientId:'P-B'}).record.sessionId,'VS-B');
  assert.equal(select(f.storage,{patientId:'P-C'}).record.sessionId,'VS-C');assert.equal(select(f.storage,{patientId:'P-B'}),null);
});
test('different API/page origin actor patient and session cannot be silently selected',()=>{
  const f=fixture({patientId:'P-A'});f.manager.update({session:session({patient_id:'P-A'})});
  for(const changed of [{serverOrigin:'http://other.synthetic'},{pageOrigin:'http://other-ui.synthetic'},{actorId:'OTHER'},{patientId:'OTHER'},{sessionId:'OTHER'}])assert.equal(select(f.storage,changed),null);
});
test('owner claim invalidates an older page instance before it can update a shared same-tab record',()=>{
  const f=fixture();f.manager.update({session:session()});const selected=select(f.storage);f.make({selected});
  const before=f.storage.getItem(f.manager.key);assert.throws(()=>f.manager.beforeWrite(transcript(),{session:session()}),/다른 화면/);assert.equal(f.storage.getItem(f.manager.key),before);
});
test('quota failure blocks the HTTP write, preserves original pending in memory and leaves the old durable record intact',async()=>{
  const f=clientFixture({storageFailure:true}),before=f.storage.getItem(f.manager.key);
  await assert.rejects(f.client.write('transcripts',{text:'합성 맥박 105',mode:'dictation',utterance_id:'U-SYNTHETIC',is_final:true}),error=>error.code==='RECOVERY_CHECKPOINT_FAILED');
  assert.equal(f.calls.length,0);assert.equal(f.client.pending.body.client_request_id,'REQUEST-SYNTHETIC');assert.equal(f.storage.getItem(f.manager.key),before);
});
test('write-readback contention blocks HTTP even if storage.setItem itself succeeded',async()=>{
  const f=clientFixture();f.storage.race=true;
  await assert.rejects(f.client.write('transcripts',{text:'합성',mode:'dictation',utterance_id:'U-SYNTHETIC',is_final:true}),error=>error.code==='RECOVERY_CHECKPOINT_FAILED');assert.equal(f.calls.length,0);
});
test('network failure reload then explicit retry sends the exact original request without a new ID',async()=>{
  const f=clientFixture();await assert.rejects(f.client.write('transcripts',{text:'합성 맥박 105',mode:'dictation',utterance_id:'U-SYNTHETIC',is_final:true}));
  const selected=select(f.storage),restored=f.make({selected}),calls=[];
  const next=new VoiceClient({server:scope.serverOrigin,actorId:scope.actorId,makeId:()=>assert.fail('retry generated a new ID'),beforeWrite:r=>restored.beforeWrite(r,{session:next.session}),fetchImpl:async(url,o)=>{calls.push({url,...o});return {ok:true,status:200,json:async()=>({session:session({version:3})})};}});
  next.session=session();next.pending=structuredClone(restored.record.pending);assert.equal(calls.length,0);await next.retry();
  assert.equal(calls.length,1);assert.equal(calls[0].body,f.calls[0].body);assert.equal(calls[0].method,f.calls[0].method);assert.equal(calls[0].url,f.calls[0].url);
  restored.update({session:next.session,pending:next.pending,outcome:'saved'});assert.equal(select(f.storage).record.pending,null);
});
test('definitive 409 clears the durable pending before another reload instead of resurrecting it',async()=>{
  const f=clientFixture({response:'409'});await assert.rejects(f.client.write('transcripts',{text:'합성',mode:'dictation',utterance_id:'U-SYNTHETIC',is_final:true}),error=>error.status===409);
  assert.equal(f.client.pending,null);f.manager.update({session:f.client.session,pending:f.client.pending,outcome:'terminal_error'});
  assert.equal(select(f.storage).record.pending,null);assert.equal(select(f.storage).record.outcome,'terminal_error');
});
test('duplicate refresh GET failure retains exact pending and never marks historical snapshot current',async()=>{
  const f=fixture();f.manager.update({session:session()});const calls=[];
  const client=new VoiceClient({server:scope.serverOrigin,actorId:scope.actorId,makeId:()=> 'R-DUPLICATE',beforeWrite:r=>f.manager.beforeWrite(r,{session:client.session}),fetchImpl:async(url,o)=>{calls.push(o.method);if(o.method==='GET')throw Error('synthetic GET loss');return {ok:true,status:200,json:async()=>({duplicate:true,session:session({version:3})})};}});client.session=session();
  await assert.rejects(client.write('confirm',{confirmed:true}),error=>error.code==='DUPLICATE_REFRESH_REQUIRED');assert.deepEqual(calls,['POST','GET']);assert.ok(client.pending);assert.equal(client.session.version,2);assert.equal(select(f.storage).record.pending.body.client_request_id,'R-DUPLICATE');
});
test('only pending new-patient submit may recover a committed null to patient ID transition',()=>{
  const f=fixture();f.manager.update({session:session()});const request={path:'/api/voice/sessions/VS-SYNTHETIC/submit',method:'POST',body:{actor_id:scope.actorId,client_request_id:'R-SUBMIT',expected_version:2,context:{consent:true,lat:35,lon:126},start_inquiry:false}};
  f.manager.beforeWrite(request,{session:session()});const committed=session({version:3,state:'SUBMITTED',patient_id:'P-NEW'});assert.equal(f.manager.assertSession(committed),committed);
  assert.equal(select(f.storage,{patientId:'P-NEW'}).record.sessionId,'VS-SYNTHETIC');
  f.manager.update({session:committed,pending:request});assert.equal(f.manager.record.patientId,'P-NEW');
  const normal=fixture();normal.manager.update({session:session()});assert.throws(()=>normal.manager.assertSession(committed),/일치/);
  assert.throws(()=>f.manager.assertSession({...committed,patient_id:'P-OTHER'}),/일치/);
});
test('server session scope and backwards versions are rejected without modifying stored input',()=>{
  const f=fixture({patientId:'P-A'});f.manager.update({session:session({patient_id:'P-A'})});const before=f.storage.getItem(f.manager.key);
  for(const s of [session({id:'VS-OTHER',patient_id:'P-A'}),session({actor_id:'OTHER',patient_id:'P-A'}),session({patient_id:'P-B'}),session({version:1,patient_id:'P-A'})])assert.throws(()=>f.manager.assertSession(s));
  assert.equal(f.storage.getItem(f.manager.key),before);
});
test('stale local field edits retain their base version independently of a newer server descriptor',()=>{
  const f=fixture(),ui={...emptyMainInput(),dirty:true,baseVersion:2,fields:{pulse:'105'}};f.manager.update({session:session(),ui});f.manager.update({session:session({version:4})});
  const r=select(f.storage).record;assert.equal(r.ui.baseVersion,2);assert.equal(r.version,4);assert.equal(r.ui.fields.pulse,'105');
});
test('TTL expires at 12 hours and a malformed saved record is never silently discarded',()=>{
  const f=fixture();const raw=f.storage.getItem(f.manager.key),end=1000+MAIN_RECOVERY_TTL;
  assert.equal(findMainRecovery(f.storage,scope,{now:end-1}).expired,false);assert.equal(findMainRecovery(f.storage,scope,{now:end}).expired,true);
  assert.throws(()=>f.make({selected:findMainRecovery(f.storage,scope,{now:end})}),/12시간/);assert.equal(f.storage.getItem(f.manager.key),raw);
  f.storage.map.set(f.manager.key,'{broken');assert.throws(()=>select(f.storage),/읽지/);assert.equal(f.storage.getItem(f.manager.key),'{broken');
});
test('unsupported paths methods foreign actors and changed create patient scope fail before storage changes',()=>{
  const f=fixture();f.manager.update({session:session()});const before=f.storage.getItem(f.manager.key);
  for(const r of [{...transcript(),path:'/api/patients/P/confirm'},{...transcript(),method:'DELETE'},{...transcript(),body:{...transcript().body,actor_id:'OTHER'}},{...transcript(),body:{...transcript().body,expected_version:3}}])assert.throws(()=>f.manager.beforeWrite(r,{session:session()}));
  assert.equal(f.storage.getItem(f.manager.key),before);
  const create=fixture({patientId:'P-A'});assert.throws(()=>create.manager.beforeWrite({path:'/api/voice/sessions',method:'POST',body:{actor_id:scope.actorId,client_request_id:'R-CREATE',patient_id:'P-B'}}),/환자/);
});
test('all existing main write endpoints accept canonical request shapes without rebuilding their bodies',()=>{
  const f=fixture();f.manager.update({session:session()});
  const cases=[['commands',{text:'합성 명령'}],['fields',{changes:{pulse:105},unset_fields:[],change_kind:'correction'}],['undo',{}],['confirm',{confirmed:true}],['submit',{context:{consent:true,lat:35,lon:126},start_inquiry:false}],['audio',{audio_base64:'U1lOVEhFVElD',content_type:'audio/wav',utterance_id:'U-AUDIO',mode:'dictation'}],['followup/answers',{question_id:'spo2',profile:'voice_extended_v1',utterance_id:'U-ANSWER',text:'미측정',unavailable:true}],['treatment-review/preview',{expected_patient_version:7,selection:{mode:'keep'}}],['treatment-review/apply',{expected_patient_version:7,review_token:'synthetic',selection:{mode:'keep'},confirmed:true,allow_reduction:true,reason:'합성'}]];
  for(const [action,body]of cases){const request={path:'/api/voice/sessions/VS-SYNTHETIC/'+action,method:action==='fields'?'PATCH':'POST',body:{...body,actor_id:scope.actorId,client_request_id:'R-'+action.replaceAll('/','-'),expected_version:2}};f.manager.beforeWrite(request,{session:session()});assert.equal(JSON.stringify(f.manager.record.pending),JSON.stringify(request));}
});
test('synthetic audio is present only while pending and is removed after success or definitive failure',()=>{
  for(const outcome of ['saved','terminal_error']){const f=fixture();f.manager.update({session:session()});const audio={path:'/api/voice/sessions/VS-SYNTHETIC/audio',method:'POST',body:{actor_id:scope.actorId,client_request_id:'R-AUDIO',expected_version:2,audio_base64:'U1lOVEhFVElD',content_type:'audio/wav',utterance_id:'U-AUDIO',mode:'dictation'}};f.manager.beforeWrite(audio,{session:session()});assert.ok(f.storage.getItem(f.manager.key).includes('audio_base64'));f.manager.update({pending:null,outcome});assert.equal(f.storage.getItem(f.manager.key).includes('audio_base64'),false);assert.equal(Object.hasOwn(f.manager.record,'fields'),false);}
});
test('oversize input and unavailable storage preserve the previous record without writing over it',()=>{
  const f=fixture(),before=f.storage.getItem(f.manager.key);
  assert.throws(()=>f.manager.update({ui:{...emptyMainInput(),transcript:'x'.repeat(MAIN_RECOVERY_MAX_BYTES)}}));assert.equal(f.storage.getItem(f.manager.key),before);
  assert.throws(()=>findMainRecovery(null,scope));
});
test('a 4 MiB synthetic audio body exceeds the full-record bound and blocks dispatch without logging audio',async()=>{
  const f=clientFixture(),before=f.storage.getItem(f.manager.key);
  await assert.rejects(f.client.write('audio',{audio_base64:'A'.repeat(MAIN_RECOVERY_MAX_BYTES),content_type:'audio/wav',utterance_id:'U-SYNTHETIC',mode:'dictation'}),error=>error.code==='RECOVERY_CHECKPOINT_FAILED');
  assert.equal(f.calls.length,0);assert.equal(f.storage.getItem(f.manager.key),before);
});
test('non-JSON hidden properties and unsupported data cannot silently change request body semantics',()=>{
  const f=fixture();f.manager.update({session:session()});for(const value of [NaN,Infinity,undefined,()=>{}]){const request=transcript();request.body.text=value;assert.throws(()=>f.manager.beforeWrite(request,{session:session()}));}
  const request=transcript();Object.defineProperty(request.body,'hidden',{value:'not serialized'});assert.throws(()=>f.manager.beforeWrite(request,{session:session()}));
});
test('record limit blocks a new record while preserving all existing scopes',()=>{
  const f=fixture();for(let i=1;i<32;i++)f.make({actorId:'CREW-'+i});const before=[...f.storage.map];assert.throws(()=>f.make({actorId:'CREW-33'}),/32개/);assert.deepEqual([...f.storage.map],before);assert.equal([...f.storage.map.keys()].filter(k=>k.startsWith(MAIN_RECOVERY_PREFIX)).length,32);
});
test('same-millisecond saved scopes select the latest intent, not the highest old record revision',()=>{
  const f=fixture({patientId:'P-A'});for(let i=0;i<6;i++)f.manager.update({session:session({patient_id:'P-A'})});
  const next=f.make({patientId:'P-A'});next.update({session:session({id:'VS-NEW',patient_id:'P-A'})});
  assert.equal(select(f.storage,{patientId:'P-A'}).record.sessionId,'VS-NEW');
});
