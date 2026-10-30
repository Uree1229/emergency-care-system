import test from 'node:test';
import assert from 'node:assert/strict';
import {VoiceClient} from './paramedic-ui/voice-client.mjs';
import {QuickSession} from './paramedic-ui/quick-session.mjs';

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
