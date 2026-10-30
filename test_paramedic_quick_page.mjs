import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import fs from 'node:fs';
import {setImmediate as nextTurn} from 'node:timers/promises';
import {VoiceClient} from './paramedic-ui/voice-client.mjs';
import {QuickSession} from './paramedic-ui/quick-session.mjs';
import {chooseAnswer, answerVocabulary} from './paramedic-ui/answer-match.mjs';

// Event-lifecycle unit harness with a FAKE capture object and a fake server.
// It is NOT a browser/STT accuracy test and NOT an actual microphone test.
const pageSource=fs.readFileSync(new URL('./paramedic-ui/quick-test.js',import.meta.url),'utf8').replace(/^import .*;\r?\n/gm,'');
const pageHtml=fs.readFileSync(new URL('./paramedic-ui/quick-test.html',import.meta.url),'utf8');
const ids=[...pageHtml.matchAll(/\bid="([^"]+)"/g)].map(m=>m[1]);
// Elements that start hidden in the markup start hidden in the harness too.
const startsHidden=new Set([...pageHtml.matchAll(/<[^>]*\bid="([^"]+)"[^>]*>/g)].filter(m=>/\shidden[\s>]/.test(m[0])).map(m=>m[1]));
const response=(data,status=200)=>({ok:status<400,status,json:async()=>data});
const draft=(version=1,fields={},extra={})=>({id:'VS-PAGE',version,state:'DRAFT',fields,field_metadata:{},warnings:[],speak_text:'47세 남성',latest_changes:[],...extra});
const question=(id,text,fields,reason='missing')=>({id,fields,question:text,answer_hint:'예: '+text,reason,askable:true});
const plan=(next,extra={})=>({profile:'voice_extended_v1',items:next?[next]:[],next_question:next,complete:!next,collection_complete:!next,unanswered_count:next?1:0,unresolved_count:next?1:0,...extra});
const V2={voice_revision:'voice-v2-20261030',transcript_modes:['dictation','command','auto'],speech_context_hints:[]};
const flush=async()=>{for(let i=0;i<4;i++)await nextTurn();};
const textOf=e=>e.children.length?e.children.map(textOf).join(' '):e.textContent;
// The conversation is chronological: crew bubbles (is-user) and app bubbles (is-app) are appended.
const users=el=>el('quick-history').children.filter(c=>c.className.includes('is-user'));
const lastUser=el=>users(el).at(-1);
const metaOf=entry=>textOf(entry.children[1]);
const BP=question('blood_pressure','혈압의 수축기와 이완기 값을 함께 알려 주세요.',['systolic_bp','diastolic_bp']);

class Element {
  constructor(tag='div'){this.tagName=tag;this.textContent='';this.children=[];this.value='';this.hidden=false;this.disabled=false;this.checked=false;this.className='';this.attributes={};this.classList={toggle(){}};}
  append(...children){this.children.push(...children);}
  prepend(...children){this.children.unshift(...children);}
  replaceChildren(...children){this.children=[...children];}
  setAttribute(key,value){this.attributes[key]=value;}
}

function harness(handler,{capabilities=V2,terms={speech_recognition_hints:[]}}={}) {
  const elements=Object.fromEntries(ids.map(id=>[id,new Element()])), calls=[], meta=[], captures=[], intervals=[], clock={now:1234};
  elements['followup-autoread'].checked=true;
  for(const id of startsHidden)elements[id].hidden=true;
  let requestSequence=0;
  const fakeFetch=async(url,options)=>{
    // Read-only server checks at page load are tracked apart from draft requests.
    if(url.endsWith('/api/voice/capabilities')){meta.push(url);return response(capabilities);}
    if(url.endsWith('/api/voice/terms')){meta.push(url);return response(terms);}
    const call={url,method:options.method,body:options.body?JSON.parse(options.body):null};calls.push(call);
    if(handler)return handler(call,calls.length);
    return response({session:call.url.endsWith('/transcripts')?draft(2,{age:47,sex:'male'}):draft()});
  };
  class TestClient extends VoiceClient {
    constructor(options){super({...options,fetchImpl:fakeFetch,makeId:()=>`request-${++requestSequence}`});}
  }
  class FakeCapture {
    static capabilities(){return {browser:true,tts:true};}
    constructor(callbacks){this.callbacks=callbacks;this.active=false;this.spoken=[];this.starts=0;this.startOptions=[];this.phrases=[];this.phraseHistory=[];this.phraseStatus='none';captures.push(this);}
    setPhrases(list){this.phrases=list;this.phraseHistory.push(Array.from(list));this.phraseStatus=list.length?'unsupported':'none';return list;}
    async start(mode,options){this.starts++;this.startOptions.push(options);this.active=true;this.callbacks.onState('listening');}
    async stop(){this.active=false;this.callbacks.onState('idle');}
    abort(){if(!this.active)return;this.active=false;this.callbacks.onState('idle');}
    stopSpeaking(){}
    // Mirrors VoiceCapture: feedback while the microphone is open is refused.
    speak(text){if(this.active)throw Object.assign(new Error('마이크 입력을 먼저 종료한 뒤 안내 음성을 재생하세요.'),{code:'capture-active'});this.spoken.push(text);if(this.speakError)throw this.speakError;}
    final(text,alternatives){this.callbacks.onFinal(text,alternatives);}
    end(){this.active=false;this.callbacks.onState('idle');}
    fail(){this.active=false;this.callbacks.onState('idle');this.callbacks.onError(new Error('recognition network failure'));}
  }
  const events={};
  const context=vm.createContext({VoiceCapture:FakeCapture,VoiceClient:TestClient,QuickSession,chooseAnswer,answerVocabulary,
    requestId:()=>`page-${++requestSequence}`,location:{origin:'http://127.0.0.1:8000'},
    document:{getElementById:id=>{assert.ok(elements[id],`unknown element ${id}`);return elements[id];},
      querySelector:()=>new Element(),createElement:tag=>new Element(tag)},
    window:{addEventListener:(name,callback)=>{events[name]=callback;}},
    performance:{now:()=>clock.now},queueMicrotask,
    setInterval:callback=>intervals.push(callback),clearInterval:id=>{intervals[id-1]=null;},console});
  vm.runInContext(pageSource,context,{filename:'quick-test.js'});
  const el=id=>elements[id];
  const hold=async(id,{ms=1000,say=[]}={})=>{
    await el(id).onpointerdown({button:0,isPrimary:true,pointerId:1});
    // An array entry is one final result with recognizer alternatives, best first.
    for(const text of say)Array.isArray(text)?captures[0].final(text[0],text):captures[0].final(text);
    clock.now+=ms;
    await el(id).onpointerup({pointerId:1});await flush();
  };
  const tick=()=>{for(const callback of intervals)callback?.();};
  return {el,capture:captures[0],calls,meta,events,clock,hold,tick};
}

test('multiple final segments auto-send one combined utterance when recognition ends',async()=>{
  const {el,capture,calls}=harness();
  await el('quick-record').onclick();
  capture.final('47세 남성 환자입니다.');capture.final('혈압 120에 80. 구토 없음.');
  assert.equal(calls.length,0);
  assert.equal(el('quick-transcript').textContent,'47세 남성 환자입니다. 혈압 120에 80. 구토 없음.');
  capture.end();await flush();
  assert.equal(calls.length,2);
  assert.equal(calls[1].body.text,'47세 남성 환자입니다. 혈압 120에 80. 구토 없음.');
  assert.equal(calls[1].body.is_final,true);
  assert.equal(calls[1].body.mode,'auto');
  assert.equal(el('quick-history-count').textContent,'1회');
  assert.equal(el('quick-count').textContent,'12개 중 2개');
  assert.equal(el('quick-transcript').textContent,'','the live bubble clears once the utterance is in the conversation');
  assert.equal(lastUser(el).children[0].textContent,'47세 남성 환자입니다. 혈압 120에 80. 구토 없음.');
  assert.equal(el('quick-record').disabled,false);
});

test('explicit stop and onend callback cannot double-send the same utterance',async()=>{
  const {el,capture,calls}=harness();
  await el('quick-record').onclick();capture.final('47세 남성.');
  await el('quick-record').onclick();await flush();
  capture.end();await flush();
  assert.equal(calls.filter(c=>c.url.endsWith('/transcripts')).length,1);
  assert.equal(el('quick-history-count').textContent,'1회');
});

test('recognition failure after a final fragment preserves raw text and never edits server draft',async()=>{
  const {el,capture,calls}=harness();
  await el('quick-record').onclick();capture.final('47세 남성.');capture.fail();await flush();
  assert.equal(calls.length,0);
  assert.equal(el('quick-transcript').textContent,'47세 남성.');
  assert.match(el('quick-error').textContent,/network failure/);
  assert.equal(el('quick-record').disabled,false);
});

test('cancel and pagehide never submit fragments or leave capture active',async()=>{
  for(const byPagehide of [false,true]){
    const {el,capture,calls,events}=harness();
    await el('quick-record').onclick();capture.final('47세 남성.');
    if(byPagehide)events.pagehide();else el('quick-cancel').onclick();
    await flush();assert.equal(capture.active,false);assert.equal(calls.length,0);
  }
});

test('stop keeps record, modes, new and retry blocked until a slow server response finishes',async()=>{
  let release;
  const gate=new Promise(resolve=>{release=resolve;});
  const {el,capture,calls}=harness(async call=>{
    if(call.url.endsWith('/transcripts')){await gate;return response({session:draft(2,{age:47})});}
    return response({session:draft()});
  });
  await el('quick-record').onclick();capture.final('47세 남성.');
  const stopping=el('quick-record').onclick();await flush();
  assert.equal(el('quick-record').disabled,true);
  for(const id of ['mode-auto','mode-dictation','quick-new','followup-record','followup-unavailable'])assert.equal(el(id).disabled,true,id);
  assert.equal(el('quick-retry').hidden,true);
  await el('quick-record').onclick();el('quick-new').onclick();
  assert.equal(calls.length,2);
  release();await stopping;await flush();
  assert.equal(el('quick-record').disabled,false);
  assert.equal(el('quick-new').disabled,false);
  assert.equal(el('quick-history-count').textContent,'1회');
});

test('uncertain write exposes retry and keeps new input blocked until exact retry resolves',async()=>{
  let retried=false,release;
  const gate=new Promise(resolve=>{release=resolve;});
  const {el,capture,calls}=harness(async(call,n)=>{
    if(n===1)return response({session:draft()});
    if(n===2)throw Error('response lost');
    retried=true;await gate;return response({session:draft(2,{age:47})});
  });
  await el('quick-record').onclick();capture.final('47세 남성.');capture.end();await flush();
  assert.equal(el('quick-record').disabled,true);
  assert.equal(el('quick-new').disabled,true);
  assert.equal(el('quick-retry').hidden,false);
  const retry=el('quick-retry').onclick();await flush();
  assert.equal(retried,true);
  assert.equal(el('quick-record').disabled,true);
  assert.equal(el('quick-retry').hidden,true);
  release();await retry;await flush();
  assert.deepEqual(calls[1],calls[2]);
  assert.equal(el('quick-history-count').textContent,'1회');
  assert.equal(el('quick-record').disabled,false);
});

test('TTS failure after a review command leaves successful processing and history intact',async()=>{
  let version=1;
  const {el,capture,calls}=harness(call=>{
    if(call.url.endsWith('/sessions'))return response({session:draft()});
    version++;
    return response({session:draft(version,{age:47}),...(call.body.mode==='command'?{command:{action:'review'},speak_text:'47세 남성'}:{})});
  });
  await el('quick-record').onclick();capture.final('47세 남성.');capture.end();await flush();
  el('mode-command').onclick();
  capture.speakError=new Error('voice unavailable');
  await el('quick-record').onclick();capture.final('정리해줘');capture.end();await flush();
  assert.equal(calls.length,3);
  assert.equal(calls[2].body.mode,'command');
  assert.equal(el('quick-history-count').textContent,'2회');
  assert.match(el('quick-error').textContent,/읽어주기/);
  assert.equal(el('quick-state').textContent,'처리 완료');
});

test('push-to-talk: a held press sends one auto utterance on release; its trailing click is ignored',async()=>{
  const {el,capture,calls,hold}=harness();
  await hold('quick-record',{say:['47세 남성.','혈압 120에 80.']});
  assert.equal(capture.active,false);
  assert.deepEqual(calls.map(c=>c.url.split('/').pop()),['sessions','transcripts']);
  assert.equal(calls[1].body.text,'47세 남성. 혈압 120에 80.');
  assert.equal(calls[1].body.mode,'auto');
  await el('quick-record').onclick();await flush();
  assert.equal(capture.active,false);
  assert.equal(capture.starts,1);
});

test('push-to-talk: a press shorter than 0.4 s records nothing and explains how to hold',async()=>{
  const {el,capture,calls,hold}=harness();
  await hold('quick-record',{ms:200,say:['혈압']});
  await el('quick-record').onclick();await flush();
  assert.equal(capture.active,false);
  assert.equal(capture.starts,1);
  assert.equal(calls.length,0);
  assert.match(el('quick-interim').textContent,/짧게 눌렀습니다/);
});

test('push-to-talk: Space held on the keyboard ignores auto-repeat and sends once on keyup',async()=>{
  const {el,capture,calls,clock}=harness();
  const key=(repeat=false)=>({key:' ',repeat,preventDefault(){this.prevented=true;}});
  const down=key();
  await el('quick-record').onkeydown(down);
  await el('quick-record').onkeydown(key(true));
  capture.final('맥박 92회.');clock.now+=900;
  const up=key();await el('quick-record').onkeyup(up);await flush();
  assert.equal(down.prevented&&up.prevented,true);
  assert.equal(capture.starts,1);
  assert.equal(calls.filter(c=>c.url.endsWith('/transcripts')).length,1);
});

test('push-to-talk: recording still held after 30 s stops and sends automatically',async()=>{
  const {el,capture,calls,clock,tick}=harness();
  await el('quick-record').onpointerdown({button:0,isPrimary:true,pointerId:1});
  capture.final('47세 남성. 흉통이 있습니다.');
  clock.now+=29_000;tick();await flush();
  assert.equal(capture.active,true);
  clock.now+=1_000;tick();await flush();
  assert.equal(capture.active,false);
  assert.equal(calls.filter(c=>c.url.endsWith('/transcripts')).length,1);
  assert.match(el('quick-interim').textContent,/30초/);
  await el('quick-record').onpointerup({pointerId:1});await flush();
  assert.equal(calls.filter(c=>c.url.endsWith('/transcripts')).length,1);
});

test('latest_changes shows previous → current with direction, and only for a new version',async()=>{
  let n=0;
  const {el,calls,hold}=harness(call=>{
    if(call.url.endsWith('/sessions'))return response({session:draft()});
    n++;
    if(n===1)return response({session:draft(2,{systolic_bp:120,diastolic_bp:80},{latest_changes:[
      {field:'diastolic_bp',previous:null,current:80,change_kind:'dictation'},{field:'systolic_bp',previous:null,current:120,change_kind:'dictation'}]})});
    if(n===2)return response({session:draft(3,{systolic_bp:100,diastolic_bp:80},{latest_changes:[
      {field:'systolic_bp',previous:120,current:100,change_kind:'observation',direction:'decreased'}]})});
    return response({session:draft(3,{systolic_bp:100,diastolic_bp:80},{latest_changes:[
      {field:'systolic_bp',previous:120,current:100,change_kind:'observation',direction:'decreased'}]}),command:{action:'review'},speak_text:'혈압 100에 80'});
  });
  await hold('quick-record',{say:['혈압 120에 80.']});
  assert.equal(el('quick-changes').hidden,false);
  assert.equal(textOf(el('change-items')),'새로 기록 2개: 수축기 혈압, 이완기 혈압');
  await hold('quick-record',{say:['혈압이 지금 100에 80까지 떨어졌어.']});
  assert.equal(textOf(el('change-items')),'수축기 혈압 120 mmHg → 100 mmHg ▼ 감소');
  assert.equal(el('changes-kind').textContent,'· 상태 변화 관찰');
  await hold('quick-record',{say:['정리해줘.']});
  assert.equal(calls.length,4);
  assert.equal(el('quick-changes').hidden,true,'an unchanged version must not re-show old changes');
});

test('a value-less change report shows the earlier value as unconfirmed, never as the current value',async()=>{
  let n=0;
  const flagged={needs_clarification:true,clarification_evidence:['혈압이 지금 떨어졌어']};
  const {el,hold}=harness(call=>{
    if(call.url.endsWith('/sessions'))return response({session:draft()});
    n++;
    if(n===1)return response({session:draft(2,{systolic_bp:120,diastolic_bp:80})});
    return response({session:draft(3,{systolic_bp:120,diastolic_bp:80},{field_metadata:{systolic_bp:flagged,diastolic_bp:flagged},
      followup:plan(question('blood_pressure','자동으로 확정하기 어려운 내용이 있습니다. 혈압 값을 알려 주세요.',['systolic_bp','diastolic_bp'],'needs_clarification'))})});
  });
  await hold('quick-record',{say:['혈압 120에 80.']});
  await hold('quick-record',{say:['혈압이 지금 떨어졌어.']});
  const items=el('change-items').children.map(textOf);
  assert.deepEqual(items,['수축기 혈압 이전 관찰값 120 mmHg 현재 값 미확인 · 재확인 필요','이완기 혈압 이전 관찰값 80 mmHg 현재 값 미확인 · 재확인 필요']);
  const card=el('quick-fields').children.find(c=>c.children[0].textContent.startsWith('수축기 혈압'));
  assert.equal(card.className,'quick-field needs-clarification');
  assert.equal(card.children[0].textContent,'수축기 혈압 · 재확인 필요');
  assert.equal(card.children[1].textContent,'120 mmHg (이전 관찰값)');
  assert.equal(el('followup-reason').textContent,'재확인 필요');
});

test('the server next question is shown and read once, only after the microphone has closed',async()=>{
  const consciousness=question('consciousness','의식 상태를 알려 주세요.',['consciousness']);
  let version=1;
  const {el,capture,hold}=harness(call=>{
    if(call.url.endsWith('/sessions'))return response({session:draft()});
    version++;
    return response({session:draft(version,{age:47},{followup:plan(consciousness,{unanswered_count:3})})});
  });
  await hold('quick-record',{say:['47세 남성.']});
  assert.equal(el('followup-card').hidden,false);
  assert.equal(el('followup-question').textContent,'의식 상태를 알려 주세요.');
  assert.equal(el('followup-progress').textContent,'남은 3');
  assert.deepEqual(capture.spoken,['의식 상태를 알려 주세요.']);
  assert.equal(el('quick-error').hidden,true);
  await hold('quick-record',{say:['흉통이 있습니다.']});
  assert.deepEqual(capture.spoken,['의식 상태를 알려 주세요.'],'an unchanged question is not repeated');
  el('followup-speak').onclick();
  assert.equal(capture.spoken.length,2);
  el('followup-autoread').checked=false;
  await hold('quick-record',{say:['구토 없음.']});
  assert.equal(capture.spoken.length,2);
});

test('a held answer goes to followup/answers with the offered question id and the version it was shown at',async()=>{
  const pulse=question('pulse','맥박을 알려 주세요.',['pulse']);
  const {el,capture,calls,hold}=harness((call,n)=>{
    if(n===1)return response({session:draft()});
    if(call.url.endsWith('/transcripts'))return response({session:draft(2,{age:47},{followup:plan(BP)})});
    return response({session:draft(3,{age:47,systolic_bp:100,diastolic_bp:80},{followup:plan(pulse),latest_changes:[
      {field:'diastolic_bp',previous:null,current:80,change_kind:'followup_answer'},{field:'systolic_bp',previous:null,current:100,change_kind:'followup_answer'}]}),
      followup:plan(pulse),answer:{question_id:'blood_pressure',accepted:true,unavailable:false,contextual_answer:true,applied_fields:['diastolic_bp','systolic_bp'],ignored_fields:[]},
      speak_text:'맥박을 알려 주세요.',tts:'client',operational_effect:'draft_only'});
  });
  await hold('quick-record',{say:['47세 남성.']});
  await hold('followup-record',{say:['100에 80']});
  const answer=calls[2];
  assert.equal(answer.url,'http://127.0.0.1:8000/api/voice/sessions/VS-PAGE/followup/answers');
  assert.deepEqual(answer.body,{question_id:'blood_pressure',profile:'voice_extended_v1',utterance_id:answer.body.utterance_id,
    text:'100에 80',actor_id:answer.body.actor_id,client_request_id:answer.body.client_request_id,expected_version:2});
  assert.notEqual(answer.body.utterance_id,calls[1].body.utterance_id);
  assert.match(el('quick-feedback').textContent,/수축기 혈압, 이완기 혈압 \(질문 문맥으로 해석\)/);
  assert.equal(el('followup-question').textContent,'맥박을 알려 주세요.');
  assert.deepEqual(capture.spoken,[BP.question,'맥박을 알려 주세요.']);
  assert.match(lastUser(el).children[1].children[0].textContent,/확인 질문 답변 · 수축기 혈압·이완기 혈압/);
});

test('an answer that is not understood is set aside for review instead of being asked again',async()=>{
  const retryBP=question('blood_pressure','자동으로 확정하기 어려운 내용이 있습니다. 혈압의 수축기와 이완기 값을 함께 알려 주세요.',['systolic_bp','diastolic_bp'],'needs_clarification');
  const {el,capture,calls,hold}=harness((call,n)=>{
    if(n===1)return response({session:draft()});
    if(call.url.endsWith('/transcripts'))return response({session:draft(2,{age:47},{followup:plan(BP)})});
    return response({session:draft(3,{age:47},{followup:plan(retryBP)}),followup:plan(retryBP),
      answer:{question_id:'blood_pressure',accepted:false,unavailable:false,contextual_answer:false,applied_fields:[],ignored_fields:['pulse']},speak_text:retryBP.question});
  });
  await hold('quick-record',{say:['47세 남성.']});
  await hold('followup-record',{say:['맥박 92']});
  assert.equal(calls.length,3);
  assert.match(el('quick-feedback').textContent,/보류하고 다음 질문으로/);
  assert.match(el('quick-feedback').textContent,/맥박 값은 반영하지 않았습니다/);
  assert.equal(el('followup-card').hidden,true);
  assert.equal(el('followup-deferred').hidden,false);
  assert.equal(el('followup-deferred-text').textContent,'검토 때 확인 1개 · 수축기 혈압·이완기 혈압 “맥박 92”');
  assert.deepEqual([el('followup-done').hidden,el('followup-done').textContent],[true,''],'the deferred note replaces the done note');
  const notice='알아듣지 못한 답은 보류했습니다. 검토할 때 확인해 주세요.';
  assert.deepEqual(capture.spoken,[BP.question,notice],'the deferred question is not read again');
  assert.equal(el('quick-count').textContent,'12개 중 1개');
  el('followup-deferred-retry').onclick();
  assert.equal(el('followup-card').hidden,false);
  assert.equal(el('followup-question').textContent,retryBP.question);
  assert.equal(el('followup-deferred').hidden,true);
  assert.deepEqual(capture.spoken,[BP.question,notice,retryBP.question]);
});

test('a spoken "not measured" heard as 비 측정 is sent as unavailable, with the words exactly as heard',async()=>{
  const glucose=question('glucose','현재 측정한 혈당을 알려 주세요.',['glucose']);
  const {el,calls,hold}=harness((call,n)=>{
    if(n===1)return response({session:draft()});
    if(call.url.endsWith('/transcripts'))return response({session:draft(2,{age:47},{followup:plan(glucose)})});
    const done=plan(null,{items:[{...glucose,answer_status:'unavailable',askable:false}],complete:false,collection_complete:true,unresolved_count:1});
    return response({session:draft(3,{age:47},{followup:done}),followup:done,answer:{question_id:'glucose',accepted:true,unavailable:true,applied_fields:[],ignored_fields:[]}});
  });
  await hold('quick-record',{say:['47세 남성.']});
  await hold('followup-record',{say:['비 측정']});
  assert.deepEqual([calls[2].body.text,calls[2].body.unavailable,calls[2].body.question_id],['비 측정',true,'glucose']);
  assert.match(metaOf(lastUser(el)),/미측정 뜻으로 보고 보냄/);
  assert.equal(el('followup-deferred').hidden,true);
});

test('answers ask for several hypotheses, bias toward the question options, and send the one that fits',async()=>{
  const rate=question('respiratory_rate','현재 호흡수를 알려 주세요.',['respiratory_rate']);
  const {el,capture,calls,hold}=harness((call,n)=>{
    if(n===1)return response({session:draft()});
    if(call.url.endsWith('/transcripts'))return response({session:draft(2,{age:47},{followup:plan(rate)})});
    return response({session:draft(3,{age:47,respiratory_rate:20},{followup:plan(null)}),followup:plan(null),
      answer:{question_id:'respiratory_rate',accepted:true,unavailable:false,applied_fields:['respiratory_rate'],ignored_fields:[]}});
  });
  await hold('quick-record',{say:['47세 남성.']});
  assert.equal(capture.startOptions[0],undefined);
  assert.equal(capture.phraseHistory.at(-1).includes('미측정'),false,'dictation keeps the vocabulary-only hints');
  await hold('followup-record',{say:[['분당 입회','분당 20회','분당 21회']]});
  assert.deepEqual({...capture.startOptions[1]},{alternatives:5});
  assert.ok(['호흡수','분당','미측정'].every(word=>capture.phraseHistory.at(-1).includes(word)));
  assert.ok(capture.phraseHistory.at(-1).every(word=>!/[0-9]/.test(word)));
  assert.deepEqual([calls[2].body.text,Object.hasOwn(calls[2].body,'unavailable')],['분당 20회',false]);
  assert.equal(lastUser(el).children[0].textContent,'분당 20회');
  assert.match(metaOf(lastUser(el)),/인식 후보 2순위 사용\(1순위 “분당 입회”\)/);
});

test('a question that was not understood is skipped over so the next offered question can be answered',async()=>{
  const rate=question('respiratory_rate','현재 호흡수를 알려 주세요.',['respiratory_rate']);
  const glucose=question('glucose','현재 측정한 혈당을 알려 주세요.',['glucose']);
  const both=plan(rate,{items:[rate,glucose],unanswered_count:2});
  const {el,capture,calls,hold}=harness((call,n)=>{
    if(n===1)return response({session:draft()});
    if(call.url.endsWith('/transcripts'))return response({session:draft(2,{age:47},{followup:both})});
    if(call.body.question_id==='respiratory_rate')return response({session:draft(3,{age:47},{followup:both}),followup:both,
      answer:{question_id:'respiratory_rate',accepted:false,unavailable:false,applied_fields:[],ignored_fields:[]}});
    const left=plan(rate,{items:[rate],unanswered_count:1});
    return response({session:draft(4,{age:47,glucose:105},{followup:left}),followup:left,
      answer:{question_id:'glucose',accepted:true,unavailable:false,applied_fields:['glucose'],ignored_fields:[]}});
  });
  await hold('quick-record',{say:['47세 남성.']});
  await hold('followup-record',{say:['분당 시']});
  assert.equal(el('followup-question').textContent,glucose.question);
  assert.equal(el('followup-progress').textContent,'남은 1 · 보류 1');
  assert.deepEqual(capture.spoken,[rate.question,glucose.question]);
  await hold('followup-record',{say:['105']});
  assert.equal(calls[3].body.question_id,'glucose');
  assert.equal(calls[3].body.expected_version,3);
  assert.equal(el('followup-card').hidden,true);
  assert.match(el('followup-deferred-text').textContent,/호흡수 “분당 시”/);
  assert.deepEqual(capture.spoken.slice(2),['알아듣지 못한 답은 보류했습니다. 검토할 때 확인해 주세요.'],'the deferred question is not read back');
});

test('the unavailable button sends unavailable:true and no value, and stops asking without filling anything',async()=>{
  const {el,calls,hold}=harness((call,n)=>{
    if(n===1)return response({session:draft()});
    if(call.url.endsWith('/transcripts'))return response({session:draft(2,{age:47},{followup:plan(BP)})});
    const done=plan(null,{items:[{...BP,reason:'needs_clarification',answer_status:'unavailable',askable:false}],complete:false,collection_complete:true,unresolved_count:1});
    const flagged={needs_clarification:true,clarification_evidence:['미측정']};
    return response({session:draft(3,{age:47},{followup:done,field_metadata:{systolic_bp:flagged,diastolic_bp:flagged}}),followup:done,
      answer:{question_id:'blood_pressure',accepted:true,unavailable:true,contextual_answer:false,applied_fields:[],ignored_fields:[]},speak_text:'확인 질문을 마쳤습니다.'});
  });
  await hold('quick-record',{say:['47세 남성.']});
  await el('followup-unavailable').onclick();await flush();
  assert.equal(calls[2].body.unavailable,true);
  assert.equal(calls[2].body.text,'미측정');
  assert.equal(calls[2].body.question_id,'blood_pressure');
  assert.equal(el('followup-card').hidden,true);
  assert.equal(el('followup-done').hidden,false);
  assert.match(el('followup-done').textContent,/1개는 0이나 정상으로 채우지 않았습니다/);
  assert.match(el('quick-feedback').textContent,/미측정·확인 불가로 남겼습니다/);
  assert.equal(el('quick-count').textContent,'12개 중 1개');
  assert.deepEqual(el('change-items').children.map(textOf),['수축기 혈압 이전 값 없음 미측정 · 확인 불가로 남김','이완기 혈압 이전 값 없음 미측정 · 확인 불가로 남김']);
  const cards=el('quick-fields').children.map(c=>c.children.slice(0,2).map(e=>e.textContent).join(' / '));
  assert.deepEqual(cards.slice(1),['수축기 혈압 · 미측정·확인 불가 / 값 없음','이완기 혈압 · 미측정·확인 불가 / 값 없음']);
});

test('a 409 answer re-reads the latest draft and question instead of retrying the stale one',async()=>{
  const pulse=question('pulse','맥박을 알려 주세요.',['pulse']);
  const {el,calls,hold}=harness((call,n)=>{
    if(n===1)return response({session:draft()});
    if(call.url.endsWith('/transcripts'))return response({session:draft(2,{age:47},{followup:plan(BP)})});
    if(call.method==='GET')return response({session:draft(3,{age:47,systolic_bp:110,diastolic_bp:70},{followup:plan(pulse)})});
    return response({error:'VOICE_QUESTION_CHANGED',message:'이미 해결됐거나 현재 제공되지 않는 질문입니다.'},409);
  });
  await hold('quick-record',{say:['47세 남성.']});
  await hold('followup-record',{say:['100에 80']});
  assert.deepEqual(calls.map(c=>c.method),['POST','POST','POST','GET']);
  assert.match(el('quick-error').textContent,/최신 초안과 질문을 다시 불러왔습니다/);
  assert.equal(el('followup-question').textContent,'맥박을 알려 주세요.');
  assert.equal(el('quick-retry').hidden,true);
  assert.equal(el('followup-record').disabled,false);
});

test('a server without voice V2 is reported and auto mode falls back to dictation',async()=>{
  const {el,calls,meta,hold}=harness(undefined,{capabilities:{voice_revision:'voice-v1',transcript_modes:['dictation','command']}});
  await flush();
  assert.deepEqual(meta.map(u=>u.split('/').pop()),['capabilities','terms']);
  assert.match(el('server-status').textContent,/버전이 다릅니다\(voice-v1\)/);
  assert.equal(el('mode-auto').disabled,true);
  await hold('quick-record',{say:['47세 남성.']});
  assert.equal(calls[1].body.mode,'dictation');
});

test('speech hints: only vocabulary reaches the recognizer, value phrases are excluded and reported',async()=>{
  const {el,capture}=harness(undefined,{capabilities:{...V2,speech_context_hints:['의식 명료','혈압','혈압 백이십에 팔십','체온 37.2']},
    terms:{speech_recognition_hints:['맥박','구토 없음','혈압','호흡곤란','호흡 정상']}});
  await flush();
  assert.deepEqual(Array.from(capture.phrases),['혈압','맥박','호흡곤란']);
  assert.match(el('hint-status').textContent,/지원하지 않아 일반 인식/);
  assert.match(el('hint-status').textContent,/5개.*제외/);
});

test('the voice test page never calls submit, patient registration or hospital inquiry endpoints',async()=>{
  let version=1;
  const {el,calls,hold}=harness(call=>{
    if(call.url.endsWith('/sessions'))return response({session:draft()});
    version++;
    const text=call.body.text;
    if(text==='환자 정보로 전송')return response({session:draft(version-1,{age:47},{followup:plan(BP)}),command:{action:'submit'},operational_effect:'explicit_submit_api_required',next_endpoint:'/api/voice/sessions/VS-PAGE/submit',requested_mode:'auto',effective_mode:'command'});
    if(text==='확인')return response({session:draft(version-1,{age:47},{followup:plan(BP)}),command:{action:'confirm'},operational_effect:'confirmation_requires_tap',speak_text:'확인은 화면에서 눌러 주세요.',requested_mode:'auto',effective_mode:'command'});
    if(call.url.endsWith('/followup/answers'))return response({session:draft(version,{age:47,systolic_bp:120,diastolic_bp:80},{followup:plan(null)}),followup:plan(null),answer:{question_id:'blood_pressure',accepted:true,unavailable:false,applied_fields:['diastolic_bp','systolic_bp'],ignored_fields:[]},speak_text:'확인 질문을 마쳤습니다.'});
    return response({session:draft(version,{age:47},{followup:plan(BP)}),requested_mode:'auto',effective_mode:'dictation'});
  });
  await hold('quick-record',{say:['47세 남성.']});
  await hold('quick-record',{say:['환자 정보로 전송']});
  assert.match(el('quick-feedback').textContent,/전송하지 않습니다/);
  await hold('quick-record',{say:['확인']});
  assert.match(el('quick-feedback').textContent,/화면에서 눌러/);
  await hold('followup-record',{say:['120에 80']});
  assert.equal(calls.length,5);
  for(const call of calls)assert.match(call.url,/\/api\/voice\/sessions(\/VS-PAGE\/(transcripts|followup\/answers))?$/);
  assert.ok(calls.every(c=>!/submit|patients|inquir|hospital/.test(c.url)));
  assert.match(users(el)[1].children[1].children[0].textContent,/자동 판단 → 명령/);
});

test('conversation: the asked question appears before the answer, commands reply as app bubbles',async()=>{
  const consciousness=question('consciousness','현재 환자의 의식 상태를 알려 주세요.',['consciousness']);
  let version=1;
  const {el,hold}=harness(call=>{
    if(call.url.endsWith('/sessions'))return response({session:draft()});
    version++;
    if(call.url.endsWith('/followup/answers'))return response({session:draft(version,{age:47,consciousness:'alert'},{followup:plan(null)}),followup:plan(null),
      answer:{question_id:'consciousness',accepted:true,unavailable:false,contextual_answer:true,applied_fields:['consciousness'],ignored_fields:[]}});
    if(call.body.text==='방금 거 취소')return response({session:draft(version,{age:47},{followup:plan(null)}),command:{action:'undo'},requested_mode:'auto',effective_mode:'command'});
    return response({session:draft(version,{age:47},{followup:plan(consciousness)}),requested_mode:'auto',effective_mode:'dictation'});
  });
  await hold('quick-record',{say:['47세 남성.']});
  await hold('followup-record',{say:['명료']});
  await hold('quick-record',{say:['방금 거 취소']});
  const thread=el('quick-history').children.map(c=>[c.className.includes('is-user')?'crew':'app',c.children[0].textContent]);
  assert.deepEqual(thread,[['crew','47세 남성.'],['app',consciousness.question],['crew','명료'],['crew','방금 거 취소'],['app','직전 변경을 되돌렸습니다.']]);
  assert.match(metaOf(users(el)[1]),/반영: 의식 \(질문 문맥으로 해석\)/);
  assert.match(metaOf(users(el)[2]),/자동 판단 → 명령 처리: 되돌리기/);
});

test('summary chips: changed, asking, unavailable and missing items are told apart',async()=>{
  const rate=question('respiratory_rate','현재 호흡수를 알려 주세요.',['respiratory_rate']);
  const glucose={...question('glucose','혈당을 알려 주세요.',['glucose']),answer_status:'unavailable',askable:false};
  let n=0;
  const {el,hold}=harness(call=>{
    if(call.url.endsWith('/sessions'))return response({session:draft()});
    n++;
    const followup=plan(rate,{items:[rate,glucose],unanswered_count:1,unresolved_count:2});
    if(n===1)return response({session:draft(2,{age:47,sex:'male',symptoms:['흉통'],systolic_bp:120,diastolic_bp:80},{followup})});
    return response({session:draft(3,{age:47,sex:'male',symptoms:['흉통'],systolic_bp:100,diastolic_bp:70},{followup,latest_changes:[
      {field:'diastolic_bp',previous:80,current:70,change_kind:'observation',direction:'decreased'},{field:'systolic_bp',previous:120,current:100,change_kind:'observation',direction:'decreased'}]})});
  });
  await hold('quick-record',{say:['47세 남성, 흉통, 혈압 120에 80.']});
  await hold('quick-record',{say:['혈압이 지금 100에 70까지 떨어졌어.']});
  const chips=Object.fromEntries(el('summary-chips').children.map(c=>[c.textContent,c.className]));
  assert.equal(chips['47세 남성'],'qv-sum-chip is-filled');
  assert.equal(chips['흉통 · 발생 ?'],'qv-sum-chip is-filled is-complaint');
  assert.equal(chips['혈압 100/70 ▼'],'qv-sum-chip is-changed');
  assert.equal(chips['호흡수 질문 중'],'qv-sum-chip is-asking');
  assert.equal(chips['혈당 미측정'],'qv-sum-chip is-unavailable');
  assert.equal(chips['맥박 —'],'qv-sum-chip is-missing');
  assert.equal(el('quick-count').textContent,'12개 중 4개');
  assert.match(metaOf(lastUser(el)),/상태 변화 관찰 · 혈압 120\/80 → 100\/70 ▼/);
});

test('option chips answer an enumerated question with one tap through the same answer API',async()=>{
  const consciousness=question('consciousness','현재 환자의 의식 상태를 알려 주세요.',['consciousness']);
  const {el,calls,hold}=harness((call,n)=>{
    if(n===1)return response({session:draft()});
    if(call.url.endsWith('/transcripts'))return response({session:draft(2,{age:47},{followup:plan(consciousness)})});
    return response({session:draft(3,{age:47,consciousness:'impaired'},{followup:plan(null)}),followup:plan(null),
      answer:{question_id:'consciousness',accepted:true,unavailable:false,contextual_answer:true,applied_fields:['consciousness'],ignored_fields:[]}});
  });
  await hold('quick-record',{say:['47세 남성.']});
  assert.deepEqual(el('followup-options').children.map(b=>b.textContent),['명료','저하','반응 없음']);
  assert.equal(el('followup-options').hidden,false);
  await el('followup-options').children[1].onclick();await flush();
  assert.deepEqual([calls[2].url.split('/').slice(-2).join('/'),calls[2].body.text,calls[2].body.question_id,Object.hasOwn(calls[2].body,'unavailable')],['followup/answers','저하','consciousness',false]);
  assert.equal(lastUser(el).children[0].textContent,'[버튼] 저하');
  assert.equal(el('followup-card').hidden,true);
});

test('with a question showing, the side button records general input to transcripts, not an answer',async()=>{
  const rate=question('respiratory_rate','현재 호흡수를 알려 주세요.',['respiratory_rate']);
  let version=1;
  const {el,calls,hold}=harness(call=>{
    if(call.url.endsWith('/sessions'))return response({session:draft()});
    version++;
    return response({session:draft(version,{age:47},{followup:plan(rate)})});
  });
  await hold('quick-record',{say:['47세 남성.']});
  assert.equal(el('quick-record').textContent,'다른 내용');
  assert.equal(el('followup-record').textContent,'누른 채로 답하기');
  assert.equal(el('followup-record').disabled,false);
  await hold('quick-record',{say:['구토는 없습니다.']});
  assert.deepEqual(calls.map(c=>c.url.split('/').pop()),['sessions','transcripts','transcripts']);
  assert.equal(calls[2].body.mode,'auto');
});

test('settings and full-detail panels toggle, and the server chip reports the voice revision',async()=>{
  const {el}=harness();
  await flush();
  assert.deepEqual([el('server-chip').textContent,el('server-chip').className],['음성 V2','qv-server is-ok']);
  assert.equal(el('settings').hidden,true);
  el('settings-toggle').onclick();
  assert.deepEqual([el('settings').hidden,el('settings-toggle').attributes['aria-expanded']],[false,'true']);
  el('summary-toggle').onclick();
  assert.deepEqual([el('summary-detail').hidden,el('summary-toggle').textContent],[false,'접기']);
  el('summary-toggle').onclick();
  assert.deepEqual([el('summary-detail').hidden,el('summary-toggle').textContent],[true,'전체 보기 ›']);
  const old=harness(undefined,{capabilities:{voice_revision:'voice-v1',transcript_modes:['dictation','command']}});
  await flush();
  assert.deepEqual([old.el('server-chip').textContent,old.el('server-chip').className],['버전 다름','qv-server is-warn']);
});
