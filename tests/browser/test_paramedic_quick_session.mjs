import test from 'node:test';
import assert from 'node:assert/strict';
import {VoiceClient} from '../../paramedic-ui/voice-client.mjs';
import {QuickSession,normalizeCorrection,CORRECTION_FIELDS,normalizeReviewFields,normalizeSubmitContext,REVIEW_FIELDS} from '../../paramedic-ui/quick-session.mjs';
import vm from 'node:vm';

const response = (data, status=200) => ({ok:status<400,status,json:async()=>data});
const session = (version, fields={}) => ({id:'VS-QUICK',version,state:'DRAFT',fields});

function setup(handler) {
  const calls=[];let requestSequence=0,utteranceSequence=0;
  const client=new VoiceClient({server:'http://127.0.0.1:8000',actorId:'QUICK-VOICE-TEST',
    makeId:()=>`request-${++requestSequence}`,
    fetchImpl:async(url,options)=>{
      const call={url,method:options.method,body:options.body?JSON.parse(options.body):null};
      calls.push(call);return handler(call,calls.length);
    }});
  const flow=new QuickSession(client,()=>`utterance-${++utteranceSequence}`);
  return {client,flow,calls};
}

test('a combined long utterance creates one draft and sends one final dictation unchanged',async()=>{
  const text=['47세 남성 환자입니다.','20분 전부터 가슴 통증이 있습니다.',
    '의식은 명료하고 호흡은 정상입니다.','혈압 120에 80, 맥박 92회, 산소포화도 97퍼센트입니다.',
    '구토와 출혈은 없습니다.'].join(' ');
  const {client,flow,calls}=setup((call,n)=>response({session:session(n,{age:47,sex:'male'})}));
  const result=await flow.apply(text);
  assert.equal(calls.length,2);
  assert.equal(calls[0].url,'http://127.0.0.1:8000/api/voice/sessions');
  assert.equal(calls[1].url,'http://127.0.0.1:8000/api/voice/sessions/VS-QUICK/transcripts');
  assert.deepEqual(calls[1].body,{text,mode:'dictation',utterance_id:'utterance-1',is_final:true,
    actor_id:'QUICK-VOICE-TEST',client_request_id:'request-2',expected_version:1});
  assert.equal(result.session.version,2);
  assert.equal(client.session.version,2);
  assert.equal(flow.job,null);
});

test('empty, oversized and invalid-mode inputs make no API calls',async()=>{
  const {flow,calls}=setup(()=>{throw Error('must not call fetch');});
  for(const text of ['', '   ', '가'.repeat(4001)])await assert.rejects(flow.apply(text));
  await assert.rejects(flow.apply('47세 남성','other'));
  await assert.rejects(flow.apply('혈압 110에 70으로 수정해','command'));
  assert.equal(calls.length,0);
  assert.equal(flow.job,null);
});

test('exactly 4000 characters is allowed in a final dictation',async()=>{
  const {client,flow,calls}=setup(()=>response({session:session(2)}));
  client.session=session(1);
  await flow.apply('가'.repeat(4000));
  assert.equal(calls.length,1);
  assert.equal(calls[0].body.text.length,4000);
});

test('correction and submit commands use transcripts only and never register or submit patients',async()=>{
  const {client,flow,calls}=setup(call=>response({session:session(call.body.expected_version+1),
    command:{action:call.body.text==='환자 정보로 전송'?'submit':'edit'},
    operational_effect:'explicit_submit_api_required'}));
  client.session=session(1,{age:47});
  await flow.apply('혈압 110에 70으로 수정해','command');
  await flow.apply('환자 정보로 전송','command');
  assert.equal(calls.length,2);
  assert.ok(calls.every(c=>c.url.endsWith('/transcripts')));
  assert.deepEqual(calls.map(c=>c.body.mode),['command','command']);
  assert.deepEqual(calls.map(c=>c.body.expected_version),[1,2]);
  assert.ok(calls.every(c=>!Object.hasOwn(c.body,'context')));
});

test('uncertain create retry preserves the utterance and continues after the same create request',async()=>{
  const {client,flow,calls}=setup((call,n)=>{
    if(n===1)throw Error('create response lost');
    if(n===2)return response({session:session(1),duplicate:true});
    if(call.method==='GET')return response({session:session(1)});
    return response({session:session(2,{age:47})});
  });
  const text='47세 남성. 혈압 120에 80. 구토 없음.';
  await assert.rejects(flow.apply(text),e=>e.uncertain);
  assert.equal(flow.job.phase,'create');
  const preserved=structuredClone(flow.job);
  await assert.rejects(flow.apply('다른 문장'));
  assert.equal(calls.length,1);
  await flow.retry();
  assert.deepEqual(calls[0],calls[1]);
  assert.equal(calls[2].method,'GET');
  assert.equal(calls[3].body.text,preserved.text);
  assert.equal(calls[3].body.utterance_id,preserved.utteranceId);
  assert.equal(calls[3].body.expected_version,1);
  assert.equal(client.pending,null);
  assert.equal(flow.job,null);
});

test('uncertain transcript retry preserves exact request and applies only one server mutation',async()=>{
  const seen=new Map();let mutations=0;
  const {client,flow,calls}=setup(call=>{
    if(call.method==='GET')return response({session:session(2,{systolic_bp:110})});
    const key=call.body.client_request_id;
    if(seen.has(key))return response({...seen.get(key),duplicate:true});
    mutations++;
    const result={session:session(2,{systolic_bp:110})};seen.set(key,result);
    throw Error('server applied correction but response was lost');
  });
  client.session=session(1,{systolic_bp:120});
  await assert.rejects(flow.apply('수축기 혈압 110으로 수정해','command'),e=>e.uncertain);
  assert.equal(flow.job.phase,'write');
  await flow.retry();
  assert.deepEqual(calls[0],calls[1]);
  assert.equal(mutations,1);
  assert.equal(calls.length,3);
  assert.equal(client.session.fields.systolic_bp,110);
  assert.equal(flow.job,null);
  assert.equal(client.pending,null);
  await assert.rejects(flow.retry());
});

test('definite API failure clears job so a corrected utterance can be tried',async()=>{
  const {client,flow,calls}=setup((call,n)=>n===1?
    response({error:'VOICE_COMMAND_UNRESOLVED',message:'항목과 값을 다시 말하세요'},422):
    response({session:session(2,{pulse:92})}));
  client.session=session(1);
  await assert.rejects(flow.apply('값 수정해','command'),e=>e.status===422);
  assert.equal(flow.job,null);
  assert.equal(client.pending,null);
  await flow.apply('맥박 92로 수정해','command');
  assert.equal(calls.length,2);
  assert.notEqual(calls[0].body.utterance_id,calls[1].body.utterance_id);
  assert.equal(calls[1].body.expected_version,1);
});

test('an unresolved in-flight operation blocks a second long utterance',async()=>{
  let complete;
  const gate=new Promise(resolve=>{complete=resolve;});
  const {client,flow,calls}=setup(async()=>{await gate;return response({session:session(2)});});
  client.session=session(1);
  const first=flow.apply('47세 남성. 흉통 있음.');
  await assert.rejects(flow.apply('32세 여성. 복통 있음.'));
  complete();await first;
  assert.equal(calls.length,1);
  assert.equal(flow.job,null);
});

const BP = {id:'blood_pressure',fields:['systolic_bp','diastolic_bp'],question:'혈압의 수축기와 이완기 값을 함께 알려 주세요.'};

test('auto mode is sent unchanged and may create the first draft, unlike explicit command mode',async()=>{
  const {flow,calls}=setup((call,n)=>response({session:session(n,{age:47})}));
  await flow.apply('47세 남성. 혈압이 지금 100에 70까지 떨어졌어.','auto');
  assert.deepEqual(calls.map(c=>c.url.split('/').pop()),['sessions','transcripts']);
  assert.equal(calls[1].body.mode,'auto');
  assert.equal(calls[1].body.text,'47세 남성. 혈압이 지금 100에 70까지 떨어졌어.');
});

test('answers need a draft, an offered question and 1-4000 characters before any API call',async()=>{
  const {client,flow,calls}=setup(()=>{throw Error('must not call fetch');});
  await assert.rejects(flow.answer(BP,'100에 80'),/환자 상태를 먼저/);
  client.session=session(2);
  await assert.rejects(flow.answer(null,'100에 80'),/확인 질문/);
  await assert.rejects(flow.answer({},'100에 80'),/확인 질문/);
  for(const text of ['', '  ', '가'.repeat(4001)])await assert.rejects(flow.answer(BP,text));
  assert.equal(calls.length,0);
  assert.equal(flow.job,null);
});

test('an answer posts only the offered question id, the profile, a new utterance id and the shown version',async()=>{
  const {client,flow,calls}=setup(call=>response({session:session(call.body.expected_version+1),
    answer:{question_id:'blood_pressure',accepted:true,unavailable:Boolean(call.body.unavailable),applied_fields:[],ignored_fields:[]}}));
  client.session=session(2,{age:47});
  await flow.answer(BP,'100에 80');
  await flow.answer(BP,'미측정',{unavailable:true});
  assert.ok(calls.every(c=>c.url==='http://127.0.0.1:8000/api/voice/sessions/VS-QUICK/followup/answers'));
  assert.deepEqual(calls[0].body,{question_id:'blood_pressure',profile:'voice_extended_v1',utterance_id:'utterance-1',text:'100에 80',
    actor_id:'QUICK-VOICE-TEST',client_request_id:'request-1',expected_version:2});
  assert.equal(Object.hasOwn(calls[0].body,'unavailable'),false);
  assert.deepEqual([calls[1].body.unavailable,calls[1].body.expected_version,calls[1].body.utterance_id],[true,3,'utterance-2']);
  assert.equal(flow.job,null);
});

test('an uncertain answer is retried with the identical request and then clears the job',async()=>{
  const {client,flow,calls}=setup((call,n)=>{
    if(n===1)throw Error('answer response lost');
    if(call.method==='GET')return response({session:session(3,{systolic_bp:100,diastolic_bp:80})});
    return response({session:session(3),duplicate:true,answer:{accepted:true}});
  });
  client.session=session(2);
  await assert.rejects(flow.answer(BP,'100에 80'),e=>e.uncertain);
  assert.equal(flow.job.phase,'answer');
  await assert.rejects(flow.apply('맥박 92회','auto'));
  await assert.rejects(flow.answer(BP,'110에 70'));
  assert.equal(calls.length,1);
  const result=await flow.retry();
  assert.deepEqual(calls[0],calls[1]);
  assert.equal(calls[2].method,'GET');
  assert.equal(result.answer.accepted,true);
  assert.equal(client.session.fields.systolic_bp,100);
  assert.equal(flow.job,null);
  assert.equal(client.pending,null);
});

test('a definite answer conflict clears the job so the refreshed question can be answered',async()=>{
  const {client,flow,calls}=setup((call,n)=>n===1?
    response({error:'VOICE_QUESTION_CHANGED',message:'이미 해결됐거나 현재 제공되지 않는 질문입니다.'},409):
    response({session:session(3),answer:{accepted:true}}));
  client.session=session(2);
  await assert.rejects(flow.answer(BP,'100에 80'),e=>e.status===409&&e.code==='VOICE_QUESTION_CHANGED'&&!e.uncertain);
  assert.equal(flow.job,null);
  assert.equal(client.pending,null);
  await flow.answer({id:'pulse',fields:['pulse']},'92회');
  assert.equal(calls[1].body.question_id,'pulse');
  assert.notEqual(calls[0].body.utterance_id,calls[1].body.utterance_id);
});

test('exportRecovery returns detached JSON-safe pending and job copies without audio',async()=>{
  const {client,flow}=setup(()=>{throw Error('synthetic response loss');});
  assert.deepEqual(flow.exportRecovery(),{pending:null,job:null});
  client.session=session(1);
  await assert.rejects(flow.apply('synthetic pending note','dictation'));
  const saved=flow.exportRecovery();
  assert.deepEqual(saved,{pending:client.pending,job:flow.job});
  assert.equal(JSON.stringify(saved.pending.body),JSON.stringify(client.pending.body));
  saved.pending.body.text='changed copy';saved.job.text='changed copy';
  assert.equal(client.pending.body.text,'synthetic pending note');assert.equal(flow.job.text,'synthetic pending note');
  client.pending.body.audio_base64='must never be checkpointed';
  assert.throws(()=>flow.exportRecovery());
});

test('restore create retries the original create then checkpoints the original utterance in write phase',async()=>{
  const source=setup(()=>{throw Error('synthetic create response loss');});
  await assert.rejects(source.flow.apply('synthetic new draft note','auto'));
  const saved=source.flow.exportRecovery();
  const target=setup(call=>response({session:session(call.url.endsWith('/transcripts')?2:1)}));
  target.client.makeId=()=> 'resumed-transcript-request';
  target.flow.makeId=()=>{throw Error('restoration must not generate another utterance id');};
  const checkpoints=[];
  target.client.beforeWrite=()=>checkpoints.push(target.flow.exportRecovery());
  target.flow.restoreRecovery(saved);
  assert.equal(target.calls.length,0,'restoration itself never dispatches a request');
  await target.flow.retry();
  assert.deepEqual(target.calls[0].body,saved.pending.body);
  assert.equal(target.calls[1].body.text,saved.job.text);
  assert.equal(target.calls[1].body.utterance_id,saved.job.utteranceId);
  assert.equal(target.calls[1].body.mode,'auto');
  assert.deepEqual(checkpoints.map(c=>c.job.phase),['create','write']);
  assert.equal(checkpoints[1].pending.body.text,saved.job.text);
  assert.equal(target.client.pending,null);assert.equal(target.flow.job,null);
});

for(const kind of ['write','answer'])test(`restore ${kind} uses the saved request unchanged after server GET`,async()=>{
  const source=setup(()=>{throw Error('synthetic response loss');});source.client.session=session(2);
  await assert.rejects(kind==='write'?source.flow.apply('synthetic pending note','dictation'):
    source.flow.answer(BP,'100에 80',{unavailable:true}));
  const saved=source.flow.exportRecovery();
  const target=setup(call=>call.method==='GET'?response({session:session(5)}):
    response({session:session(3),duplicate:true,...(kind==='answer'?{answer:{accepted:true}}:{})}));
  target.client.makeId=target.flow.makeId=()=>{throw Error('no request or utterance ids may be regenerated');};
  await target.client.sync('VS-QUICK');
  const before=target.calls.length;
  target.flow.restoreRecovery(saved);assert.equal(target.calls.length,before);
  saved.pending.body.text='caller mutation after restore';saved.job.text='caller mutation after restore';
  await target.flow.retry();
  assert.deepEqual(target.calls[1].body,source.client.pending.body);
  assert.equal(target.calls[1].body.expected_version,2,'the fresher session does not change the saved version');
  assert.equal(target.client.session.version,5);assert.equal(target.flow.job,null);assert.equal(target.client.pending,null);
});

test('restore retains an unsent checkpoint-failed job and retries only after checkpointing succeeds',async()=>{
  const source=setup(()=>{throw Error('network must not be called');});source.client.session=session(1);
  source.client.beforeWrite=()=>{throw Error('synthetic quota failure');};
  await assert.rejects(source.flow.apply('synthetic unsent note'),e=>e.uncertain);
  assert.equal(source.calls.length,0);
  const target=setup(()=>response({session:session(2)}));target.client.session=session(1);
  let checkpoint;
  target.client.beforeWrite=()=>{checkpoint=target.flow.exportRecovery();};
  target.flow.restoreRecovery(source.flow.exportRecovery());await target.flow.retry();
  assert.equal(target.calls.length,1);assert.deepEqual(target.calls[0].body,checkpoint.pending.body);
});

test('checkpoint failure between successful create and transcript dispatch preserves the write-phase request',async()=>{
  const source=setup(()=>response({session:session(1)}));
  const phases=[];
  source.client.beforeWrite=()=>{
    const saved=source.flow.exportRecovery();phases.push(saved.job.phase);
    if(saved.job.phase==='write')throw Error('synthetic transcript checkpoint quota failure');
  };
  await assert.rejects(source.flow.apply('synthetic original note','auto'),e=>e.uncertain&&e.code==='RECOVERY_CHECKPOINT_FAILED');
  assert.deepEqual(phases,['create','write']);assert.equal(source.calls.length,1);
  assert.equal(source.flow.job.phase,'write');assert.equal(source.client.session.version,1);
  const saved=source.flow.exportRecovery();
  const target=setup(()=>response({session:session(2)}));target.client.session=session(1);
  target.flow.restoreRecovery(saved);await target.flow.retry();
  assert.equal(target.calls.length,1);assert.deepEqual(target.calls[0].body,saved.pending.body);
  assert.equal(target.calls[0].body.utterance_id,'utterance-1');assert.equal(target.calls[0].body.client_request_id,'request-2');
});

test('create recovery rejects command mode, unknown fields and invalid identifiers before any HTTP request',()=>{
  for(const change of [s=>{s.job.mode='command';},s=>{s.pending.body.patient_id='not-allowed';},
    s=>{s.pending.body.client_request_id='bad request';},s=>{s.job.phase='write';}]){
    const {flow,calls}=setup(()=>{throw Error('must not dispatch');});
    const saved={pending:{path:'/api/voice/sessions',method:'POST',body:{actor_id:'QUICK-VOICE-TEST',client_request_id:'saved-request'}},
      job:{text:'synthetic note',mode:'dictation',utteranceId:'saved-utterance',phase:'create'}};
    change(saved);assert.throws(()=>flow.restoreRecovery(saved));assert.equal(calls.length,0);
  }
});

test('a restored stale request is never regenerated or silently reapplied after a definite conflict',async()=>{
  const source=setup(()=>{throw Error('synthetic response loss');});source.client.session=session(1);
  await assert.rejects(source.flow.apply('synthetic note'));
  const saved=source.flow.exportRecovery();
  const target=setup(()=>response({error:'VOICE_VERSION_CONFLICT'},409));target.client.session=session(5);
  target.flow.restoreRecovery(saved);
  await assert.rejects(target.flow.retry(),e=>e.status===409&&!e.uncertain);
  assert.equal(target.calls.length,1);assert.deepEqual(target.calls[0].body,saved.pending.body);
  assert.equal(target.client.session.version,5);assert.equal(target.client.pending,null);assert.equal(target.flow.job,null);
});

const pendingWrite=()=>({
  pending:{path:'/api/voice/sessions/VS-QUICK/transcripts',method:'POST',body:{
    text:'synthetic note',mode:'dictation',utterance_id:'saved-utterance',is_final:true,
    actor_id:'QUICK-VOICE-TEST',client_request_id:'saved-request',expected_version:2}},
  job:{text:'synthetic note',mode:'dictation',utteranceId:'saved-utterance',phase:'write'},
});

for(const [name,change] of [
  ['another actor',s=>{s.pending.body.actor_id='other-actor';}],
  ['another session',s=>{s.pending.path='/api/voice/sessions/VS-OTHER/transcripts';}],
  ['wrong phase',s=>{s.job.phase='create';}],
  ['changed job text',s=>{s.job.text='changed';}],
  ['changed request text',s=>{s.pending.body.text='changed';}],
  ['changed mode',s=>{s.job.mode='auto';}],
  ['changed utterance id',s=>{s.job.utteranceId='other-utterance';}],
  ['wrong method',s=>{s.pending.method='PUT';}],
  ['unsupported endpoint',s=>{s.pending.path='/api/patients';}],
  ['audio in body',s=>{s.pending.body.audio_base64='not allowed';}],
  ['extra job field',s=>{s.job.audio='not allowed';}],
  ['extra request field',s=>{s.pending.secret='not allowed';}],
  ['extra snapshot field',s=>{s.session={};}],
  ['missing job',s=>{s.job=null;}],
  ['missing pending',s=>{s.pending=null;}],
  ['bad request id',s=>{s.pending.body.client_request_id='bad request';}],
  ['zero expected version',s=>{s.pending.body.expected_version=0;}],
  ['future expected version',s=>{s.pending.body.expected_version=6;}],
  ['non-final transcript',s=>{s.pending.body.is_final=false;}],
  ['oversized text',s=>{s.job.text=s.pending.body.text='가'.repeat(4001);}],
  ['undefined field',s=>{s.job.unexpected=undefined;}],
])test(`restore rejects incoherent recovery: ${name}`,()=>{
  const {client,flow,calls}=setup(()=>{throw Error('must not dispatch');});client.session=session(5);
  const saved=pendingWrite();change(saved);
  assert.throws(()=>flow.restoreRecovery(saved));
  assert.equal(flow.job,null);assert.equal(client.pending,null);assert.equal(calls.length,0);
});

test('restore requires the current matching server session for a write and no session for create',()=>{
  const {client,flow,calls}=setup(()=>{throw Error('must not dispatch');});
  assert.throws(()=>flow.restoreRecovery(pendingWrite()));
  client.session={...session(5),id:'VS-OTHER'};assert.throws(()=>flow.restoreRecovery(pendingWrite()));
  client.session={...session(5),actor_id:'other-actor'};assert.throws(()=>flow.restoreRecovery(pendingWrite()));
  client.session=session(5);
  const create={pending:{path:'/api/voice/sessions',method:'POST',body:{actor_id:'QUICK-VOICE-TEST',client_request_id:'saved-request'}},
    job:{text:'synthetic note',mode:'dictation',utteranceId:'saved-utterance',phase:'create'}};
  assert.throws(()=>flow.restoreRecovery(create));assert.equal(calls.length,0);
});

test('restore validates answer profile, question and utterance against its saved job',()=>{
  for(const key of ['profile','question_id','utterance_id','text','unavailable']){
    const {client,flow,calls}=setup(()=>{throw Error('must not dispatch');});client.session=session(5);
    const saved={pending:{path:'/api/voice/sessions/VS-QUICK/followup/answers',method:'POST',body:{
      question_id:'blood_pressure',profile:'voice_extended_v1',utterance_id:'saved-utterance',text:'100에 80',
      actor_id:'QUICK-VOICE-TEST',client_request_id:'saved-request',expected_version:2}},
      job:{text:'100에 80',questionId:'blood_pressure',utteranceId:'saved-utterance',phase:'answer'}};
    saved.pending.body[key]=key==='unavailable'?false:'changed';
    assert.throws(()=>flow.restoreRecovery(saved),key);assert.equal(calls.length,0);
  }
});

test('restore rejects a nonempty flow or a queued client operation and never starts automatic writes',async()=>{
  const {client,flow,calls}=setup(()=>{throw Error('must not dispatch');});client.session=session(5);
  flow.job=pendingWrite().job;assert.throws(()=>flow.restoreRecovery(pendingWrite()));flow.job=null;
  client.pending=pendingWrite().pending;assert.throws(()=>flow.restoreRecovery(pendingWrite()));client.pending=null;
  let release;const gate=new Promise(resolve=>{release=resolve;});
  const queued=client.serial(()=>gate);
  assert.throws(()=>flow.restoreRecovery(pendingWrite()));release();await queued;
  assert.deepEqual(flow.restoreRecovery({pending:null,job:null}),{pending:null,job:null});
  assert.equal(calls.length,0);
});

test('correct sends one atomic draft-only PATCH with no utterance, confirmation or patient operation',async()=>{
  const {client,flow,calls}=setup(()=>response({session:session(4,{systolic_bp:110,diastolic_bp:70}),operational_effect:'draft_only'}));
  client.session=session(3);flow.makeId=()=>{throw Error('a correction has no utterance id');};
  let checkpoint;client.beforeWrite=()=>{checkpoint=flow.exportRecovery();};
  await flow.correct({changes:{systolic_bp:110,diastolic_bp:70,consciousness:'unknown'},unset_fields:['glucose']});
  assert.equal(calls.length,1);assert.equal(calls[0].method,'PATCH');
  assert.equal(calls[0].url,'http://127.0.0.1:8000/api/voice/sessions/VS-QUICK/fields');
  assert.deepEqual(calls[0].body,{changes:{systolic_bp:110,diastolic_bp:70,consciousness:'unknown'},unset_fields:['glucose'],
    change_kind:'correction',actor_id:'QUICK-VOICE-TEST',client_request_id:'request-1',expected_version:3});
  assert.deepEqual(checkpoint.job,{phase:'correct',changes:calls[0].body.changes,unsetFields:['glucose']});
  assert.equal(checkpoint.pending.method,'PATCH');assert.equal(flow.job,null);assert.equal(client.pending,null);
});

test('lost correction response is retried with the exact PATCH and commits only once',async()=>{
  const committed=new Map();let mutations=0;
  const {client,flow,calls}=setup(call=>{
    if(call.method==='GET')return response({session:session(5,{pulse:91})});
    const key=call.body.client_request_id;
    if(committed.has(key))return response({...committed.get(key),duplicate:true});
    mutations++;committed.set(key,{session:session(4,{pulse:91}),operational_effect:'draft_only'});
    throw Error('synthetic correction response loss after commit');
  });
  client.session=session(3);
  await assert.rejects(flow.correct({changes:{pulse:91}}),e=>e.uncertain);
  const saved=flow.exportRecovery();assert.equal(saved.job.phase,'correct');
  await assert.rejects(flow.apply('another note'));await assert.rejects(flow.correct({changes:{pulse:92}}));
  await flow.retry();assert.deepEqual(calls[0],calls[1]);assert.equal(mutations,1);
  assert.equal(client.session.version,5);assert.equal(flow.job,null);assert.equal(client.pending,null);
});

test('correction checkpoint failure is unsent and its exact request survives reload recovery',async()=>{
  const source=setup(()=>{throw Error('must not reach network');});source.client.session=session(3);
  source.client.beforeWrite=()=>{throw Error('synthetic quota failure');};
  await assert.rejects(source.flow.correct({changes:{symptoms:['흉통'],denied_symptoms:['구토']},unset_fields:['spo2']}),e=>e.uncertain);
  assert.equal(source.calls.length,0);const saved=source.flow.exportRecovery();
  const target=setup(()=>response({session:session(4),operational_effect:'draft_only'}));target.client.session=session(5);
  target.client.makeId=target.flow.makeId=()=>{throw Error('must retain original request');};
  target.flow.restoreRecovery(saved);assert.equal(target.calls.length,0);
  saved.job.changes.symptoms.push('기침');saved.pending.body.changes.symptoms.push('기침');
  await target.flow.retry();assert.equal(target.calls.length,1);
  assert.deepEqual(target.calls[0].body,source.client.pending.body);assert.equal(target.calls[0].body.expected_version,3);
});

test('definite correction conflict clears the job without regenerating or silently reapplying it',async()=>{
  const {client,flow,calls}=setup(()=>response({error:'VOICE_VERSION_CONFLICT'},409));client.session=session(3);
  await assert.rejects(flow.correct({unset_fields:['breathing']}),e=>e.status===409&&!e.uncertain);
  assert.equal(calls.length,1);assert.equal(flow.job,null);assert.equal(client.pending,null);
  assert.equal(client.session.version,3);await assert.rejects(flow.retry());
});

test('correction requires an editable draft and cannot create or submit one',async()=>{
  const {client,flow,calls}=setup(()=>{throw Error('must not reach network');});
  await assert.rejects(flow.correct({changes:{pulse:91}}));
  client.session={...session(3),state:'SUBMITTED'};await assert.rejects(flow.correct({changes:{pulse:91}}));
  assert.equal(calls.length,0);assert.equal(flow.job,null);
});

test('correction whitelist contains exactly the twelve reviewed clinical fields, excluding age and severity',()=>{
  assert.deepEqual([...CORRECTION_FIELDS].sort(),['systolic_bp','diastolic_bp','pulse','spo2','temperature','respiratory_rate','glucose',
    'consciousness','breathing','symptoms','denied_symptoms','uncertain_symptoms'].sort());
  const value={changes:{pulse:0,spo2:0,temperature:20,respiratory_rate:0,glucose:0,consciousness:'unknown',breathing:'unknown',
    symptoms:[],denied_symptoms:[],uncertain_symptoms:[]},unset_fields:['systolic_bp','diastolic_bp']};
  assert.deepEqual(normalizeCorrection(value),{changes:value.changes,unsetFields:value.unset_fields});
  assert.deepEqual(normalizeCorrection(vm.runInNewContext('('+JSON.stringify(value)+')')),normalizeCorrection(value));
});

for(const [name,input] of [
  ['empty',{}],['empty lists',{changes:{},unset_fields:[]}],
  ['unknown age',{changes:{age:47}}],['severity',{changes:{pre_ktas:2}}],['patient props',{changes:{patient_id:'P-1'}}],
  ['unknown metadata',{changes:{pulse:91},consent:true}],['invalid changes array',{changes:[]}],
  ['null observation',{changes:{pulse:null}}],['numeric string',{changes:{pulse:'91'}}],['boolean',{changes:{pulse:false}}],
  ['NaN',{changes:{temperature:NaN}}],['Infinity',{changes:{spo2:Infinity}}],['integer fraction',{changes:{respiratory_rate:12.5}}],
  ['SBP low',{changes:{systolic_bp:0}}],['SBP high',{changes:{systolic_bp:351}}],['DBP high',{changes:{diastolic_bp:251}}],
  ['pulse high',{changes:{pulse:351}}],['spo2 high',{changes:{spo2:100.1}}],['temperature low',{changes:{temperature:19.9}}],
  ['temperature high',{changes:{temperature:45.1}}],['RR high',{changes:{respiratory_rate:101}}],['glucose high',{changes:{glucose:1501}}],
  ['inverted BP',{changes:{systolic_bp:80,diastolic_bp:90}}],['equal BP',{changes:{systolic_bp:80,diastolic_bp:80}}],
  ['unknown consciousness',{changes:{consciousness:'명료'}}],['unknown breathing',{changes:{breathing:'yes'}}],
  ['symptom string',{changes:{symptoms:'흉통'}}],['unsupported symptom',{changes:{symptoms:['임의 증상']}}],
  ['duplicate symptom',{changes:{symptoms:['흉통','흉통']}}],['conflicting symptoms',{changes:{symptoms:['흉통'],denied_symptoms:['흉통']}}],
  ['unknown unset',{unset_fields:['age']}],['duplicate unset',{unset_fields:['pulse','pulse']}],
  ['changed and unset',{changes:{pulse:91},unset_fields:['pulse']}],['string unset',{unset_fields:'pulse'}],
])test(`correction validation rejects ${name} before network`,async()=>{
  const {client,flow,calls}=setup(()=>{throw Error('must not reach network');});client.session=session(3);
  assert.throws(()=>normalizeCorrection(input));await assert.rejects(flow.correct(input));
  assert.equal(calls.length,0);assert.equal(flow.job,null);
});

for(const changes of [
  {systolic_bp:350,diastolic_bp:250},{pulse:350},{spo2:100},{temperature:45},{respiratory_rate:100},{glucose:1500},
  {spo2:97.5,temperature:37.25,glucose:105.5},{consciousness:'alert',breathing:'normal'},
  {consciousness:'impaired',breathing:'abnormal'},{consciousness:'unresponsive',breathing:'absent'},
  {symptoms:['흉통','호흡곤란','복통','두통','어지럼','구토','오심','발열','기침','객혈','출혈','경련','실신','마비','의식저하','설사','요통','발진','외상','부종','심계항진']},
])test(`correction preserves a server-valid boundary or enum: ${JSON.stringify(changes)}`,()=>{
  const result=normalizeCorrection({changes});assert.deepEqual(result,{changes,unsetFields:[]});assert.notEqual(result.changes,changes);
});

for(const [name,mutate] of [
  ['POST instead of PATCH',s=>{s.pending.method='POST';}],['observation kind',s=>{s.pending.body.change_kind='observation';}],
  ['different job change',s=>{s.job.changes.pulse=92;}],['different unset',s=>{s.job.unsetFields=[];}],
  ['unknown field',s=>{s.job.changes.age=s.pending.body.changes.age=47;}],['extra utterance',s=>{s.job.utteranceId='U-other';}],
  ['unknown actor',s=>{s.pending.body.actor_id='OTHER';}],['wrong session',s=>{s.pending.path='/api/voice/sessions/VS-other/fields';}],
])test(`restored correction rejects ${name}`,()=>{
  const {client,flow,calls}=setup(()=>{throw Error('must not reach network');});client.session=session(5);
  const saved={pending:{path:'/api/voice/sessions/VS-QUICK/fields',method:'PATCH',body:{changes:{pulse:91},unset_fields:['glucose'],
    change_kind:'correction',actor_id:'QUICK-VOICE-TEST',client_request_id:'saved-correction',expected_version:3}},
    job:{phase:'correct',changes:{pulse:91},unsetFields:['glucose']}};
  mutate(saved);assert.throws(()=>flow.restoreRecovery(saved));assert.equal(calls.length,0);assert.equal(flow.job,null);
});

const submitContext=()=>({consent:true,lat:37.5051,lon:126.9573,required_capabilities:['응급의학'],resource_demands:{ed_bed:1}});
const invokeWorkflow=(flow,phase)=>phase==='confirm'?flow.confirm():phase==='submit'?flow.submit(submitContext()):flow.reviewFields({changes:{age:47,avpu:'A',consciousness:'alert',pre_ktas:'unclassified'},unset_fields:['glucose']});

test('one client and session continue from speech through reviewed fields, explicit confirmation and registration',async()=>{
  const {flow,client,calls}=setup((call,n)=>{
    if(call.url.endsWith('/api/voice/sessions'))return response({session:session(1)});
    if(call.url.endsWith('/transcripts'))return response({session:session(2,{age:47})});
    if(call.url.endsWith('/fields'))return response({session:session(3,{age:47,avpu:'A',pre_ktas:'unclassified'})});
    if(call.url.endsWith('/confirm'))return response({session:{...session(3,{age:47,avpu:'A',pre_ktas:'unclassified'}),confirmed:true}});
    return response({session:{...session(3,{age:47,avpu:'A',pre_ktas:'unclassified'}),state:'SUBMITTED',patient_id:'P-ONE'},patient:{id:'P-ONE'}});
  });
  await flow.apply('47세 남성');await flow.reviewFields({changes:{avpu:'A',pre_ktas:'unclassified'}});await flow.confirm();const result=await flow.submit(submitContext());
  assert.equal(calls.length,5);assert.equal(result.patient.id,'P-ONE');assert.equal(client.session.state,'SUBMITTED');
  assert.deepEqual(calls.slice(1).map(call=>call.body.expected_version),[1,2,3,3]);
  assert.ok(calls.every(call=>call.url.endsWith('/api/voice/sessions')||call.url.includes('/VS-QUICK/')));
  assert.deepEqual(calls.at(-1).body.context,submitContext());assert.equal(calls.at(-1).body.start_inquiry,false);
  assert.equal(flow.job,null);assert.equal(client.pending,null);
});

for(const phase of ['confirm','submit','review-fields'])test(`workflow ${phase} has a valid synchronous checkpoint before network dispatch`,async()=>{
  const {client,flow,calls}=setup(()=>response({session:{...session(4),confirmed:true}}));client.session={...session(3,{age:47}),confirmed:true};let checkpoint;
  client.beforeWrite=()=>{checkpoint=flow.exportRecovery();};await invokeWorkflow(flow,phase);
  assert.equal(checkpoint.job.phase,phase);assert.deepEqual(checkpoint.pending.body,calls[0].body);assert.equal(checkpoint.pending.body.expected_version,3);
});

for(const phase of ['confirm','submit','review-fields'])test(`lost ${phase} response restores exact request after GET-first and requires an explicit idempotent retry`,async()=>{
  let applied=null,mutations=0;
  const fresh={...session(5,{age:47}),confirmed:true,...(phase==='submit'?{state:'SUBMITTED',patient_id:'P-ONE'}:{})};
  const handler=call=>{
    if(call.method==='GET')return response({session:fresh});
    if(applied){assert.deepEqual(call.body,applied);return response({session:fresh,duplicate:true,...(phase==='submit'?{patient:{id:'P-ONE'}}:{})});}
    mutations++;applied=structuredClone(call.body);throw Error('server committed but response was lost');
  };
  const first=setup(handler);first.client.session={...session(3,{age:47}),confirmed:true};
  await assert.rejects(invokeWorkflow(first.flow,phase),error=>error.uncertain);
  const saved=first.flow.exportRecovery();assert.equal(saved.job.phase,phase);
  const reloaded=setup(handler);await reloaded.client.sync('VS-QUICK');reloaded.flow.restoreRecovery(saved);
  assert.equal(reloaded.calls.length,1,'restoration never posts');assert.equal(reloaded.calls[0].method,'GET');
  await reloaded.flow.retry();assert.deepEqual(reloaded.calls[1].body,applied);assert.equal(reloaded.calls[1].body.expected_version,3);
  assert.equal(reloaded.calls[2].method,'GET');assert.equal(mutations,1);assert.equal(reloaded.flow.job,null);assert.equal(reloaded.client.pending,null);
  assert.equal(reloaded.client.session.state,fresh.state);
});

test('workflow new writes reject missing/submitted sessions and registration requires explicit current confirmation',async()=>{
  const {client,flow,calls}=setup(()=>{throw Error('must not dispatch');});
  await assert.rejects(flow.confirm());await assert.rejects(flow.reviewFields({changes:{age:47}}));await assert.rejects(flow.submit(submitContext()));
  client.session=session(3,{age:47});await assert.rejects(flow.submit(submitContext()));
  client.session={...session(3,{age:47}),confirmed:true,state:'SUBMITTED',patient_id:'P-ONE'};
  await assert.rejects(flow.confirm());await assert.rejects(flow.reviewFields({changes:{age:48}}));await assert.rejects(flow.submit(submitContext()));
  client.session={...session(3,{age:47}),confirmed:true,patient_id:'P-ONE'};await assert.rejects(flow.submit(submitContext()));
  assert.equal(calls.length,0);assert.equal(flow.job,null);
});

test('full review supports all observation and R1 fields without expanding the narrow correction API',()=>{
  const changes={age:47,age_group:'40대',sex:'male',symptoms:['흉통'],denied_symptoms:['구토'],uncertain_symptoms:[],
    consciousness:'alert',breathing:'unknown',systolic_bp:120,diastolic_bp:80,pulse:92,spo2:97,temperature:37.2,respiratory_rate:20,glucose:105,onset_at:1_800_000_000,
    avpu:'A',chief_complaint:'cardiac',age_category:'adult',pre_ktas:'unclassified',stemi_suspected:'unmeasured',injury_region:'limb',labor:'no',pregnancy_weeks:20,
    seizure_ongoing:'no',bleeding_risk:'no',high_risk_mechanism:'no',pain_score:0,gcs:15};
  assert.equal(REVIEW_FIELDS.length,29);assert.deepEqual(normalizeReviewFields({changes}),{changes,unsetFields:[]});
  assert.deepEqual(normalizeReviewFields(vm.runInNewContext('('+JSON.stringify({changes})+')')),normalizeReviewFields({changes}));
  assert.throws(()=>normalizeCorrection({changes:{age:47,pre_ktas:2}}));
  assert.deepEqual(normalizeReviewFields({unset_fields:['age','pre_ktas']}),{changes:{},unsetFields:['age','pre_ktas']});
});

for(const changes of [{age:'47'},{age:true},{age:131},{age_group:'47대'},{onset_at:Infinity},{pre_ktas:'2'},{pre_ktas:0},{gcs:2},{pain_score:10.5},
  {pregnancy_weeks:46},{avpu:'A',consciousness:'impaired'},{age:47,age_group:'50대'},{symptoms:['흉통'],denied_symptoms:['흉통']},{priority:4},{patient_id:'P-OTHER'}])
test(`full review rejects unsafe or inconsistent edit ${JSON.stringify(changes)} before dispatch`,async()=>{
  const {client,flow,calls}=setup(()=>{throw Error('must not dispatch');});client.session=session(3);
  await assert.rejects(flow.reviewFields({changes}));assert.equal(calls.length,0);assert.equal(flow.job,null);
});

for(const mutate of [ctx=>ctx.consent=false,ctx=>ctx.lat='37.5',ctx=>ctx.lat=NaN,ctx=>ctx.lon=181,ctx=>ctx.resource_demands.ed_bed=2,
  ctx=>ctx.resource_demands.icu=1,ctx=>ctx.start_inquiry=true,ctx=>ctx.required_capabilities=[''],ctx=>ctx.required_capabilities=['응급의학','응급의학'],ctx=>ctx.required_capabilities=[false]])
test(`registration context is bounded and never smuggles inquiry or condition changes (${String(mutate)})`,async()=>{
  const {client,flow,calls}=setup(()=>{throw Error('must not dispatch');});client.session={...session(3,{age:47}),confirmed:true};
  const context=submitContext();mutate(context);assert.throws(()=>normalizeSubmitContext(context));await assert.rejects(flow.submit(context));assert.equal(calls.length,0);assert.equal(flow.job,null);
});

for(const [name,mutate] of [
  ['changed context',s=>s.job.context.lat=38],['automatic inquiry',s=>s.pending.body.start_inquiry=true],['wrong actor',s=>s.pending.body.actor_id='OTHER'],
  ['wrong session',s=>s.pending.path='/api/voice/sessions/VS-OTHER/submit'],['future version',s=>s.pending.body.expected_version=6],
  ['unknown phase',s=>s.job.phase='patient-create'],['unapproved job field',s=>s.job.start_inquiry=true],['changed method',s=>s.pending.method='PATCH']])
test(`workflow recovery rejects ${name} and never replaces an existing pending write`,()=>{
  const {client,flow,calls}=setup(()=>{throw Error('must not dispatch');});client.session={...session(5,{age:47}),state:'SUBMITTED',patient_id:'P-ONE'};
  const saved={pending:{path:'/api/voice/sessions/VS-QUICK/submit',method:'POST',body:{actor_id:'QUICK-VOICE-TEST',client_request_id:'R-SAVE',expected_version:3,context:submitContext(),start_inquiry:false}},job:{phase:'submit',context:submitContext()}};
  mutate(saved);assert.throws(()=>flow.restoreRecovery(saved));assert.equal(client.pending,null);assert.equal(flow.job,null);assert.equal(calls.length,0);
});
