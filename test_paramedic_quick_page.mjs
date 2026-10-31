import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import fs from 'node:fs';
import {setImmediate as nextTurn} from 'node:timers/promises';
import {VoiceClient} from './paramedic-ui/voice-client.mjs';
import {QuickSession} from './paramedic-ui/quick-session.mjs';
import {chooseAnswer, answerVocabulary} from './paramedic-ui/answer-match.mjs';
import {RECORDING_LIMITS, findVoiceTrigger} from './paramedic-ui/recording-control.mjs';
import {CommandCommitGate} from './paramedic-ui/command-commit.mjs';
import {writeDraftHandoff,readDraftHandoff,DRAFT_HANDOFF_KEY} from './paramedic-ui/draft-handoff.mjs';
import {RECOVERY_KEY,writeDraftRecovery,readDraftRecovery,clearDraftRecovery} from './paramedic-ui/draft-recovery.mjs';
import {buildCriticalReview,planCriticalCorrection,classifyCriticalReply} from './paramedic-ui/critical-review.mjs';
import {PATIENT_RECORDS_KEY,emptyBook,readPatientRecords,writePatientRecords,createPatientRecord,updatePatientCheckpoint,selectPatientRecord,removePatientRecord} from './paramedic-ui/patient-records.mjs';
import {buildHospitalHandoff} from './paramedic-ui/hospital-handoff.mjs';
import {buildObservationHistory,historySnapshotSignature} from './paramedic-ui/observation-history.mjs';
import {buildConnectionStatus} from './paramedic-ui/connection-status.mjs';

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
const guardClocks=new Set();
// Legacy flow assertions wait through the actual gate's 800ms quiet window in
// virtual time. Guard-specific cases disable this and advance the same clock
// explicitly; the production CommandCommitGate is never replaced by a fake.
const flush=async()=>{for(let i=0;i<4;i++)await nextTurn();for(const settle of guardClocks)settle();for(let i=0;i<4;i++)await nextTurn();};
const textOf=e=>e.children.length?e.children.map(textOf).join(' '):e.textContent;
// The conversation is chronological: crew bubbles (is-user) and app bubbles (is-app) are appended.
const users=el=>el('quick-history').children.filter(c=>c.className.includes('is-user'));
const lastUser=el=>users(el).at(-1);
const metaOf=entry=>textOf(entry.children[1]);
const BP=question('blood_pressure','혈압의 수축기와 이완기 값을 함께 알려 주세요.',['systolic_bp','diastolic_bp']);
const criticalCard=(h,id)=>h.el('critical-items').children.find(card=>card.attributes['data-critical-id']===id);
const criticalButton=(h,id,action)=>criticalCard(h,id).children.at(-1).children.find(button=>button.attributes['data-critical-action']===action);
const criticalInput=(h,field)=>h.el('critical-inputs').children.flatMap(label=>label.children).find(input=>input.attributes['data-critical-field']===field);

class Element {
  constructor(tag='div'){this.tagName=tag;this.textContent='';this.children=[];this.value='';this.hidden=false;this.disabled=false;this.checked=false;this.className='';this.attributes={};this.classList={toggle(){}};}
  append(...children){this.children.push(...children);}
  prepend(...children){this.children.unshift(...children);}
  replaceChildren(...children){this.children=[...children];}
  setAttribute(key,value){this.attributes[key]=value;}
  focus(){this.focused=true;}
  select(){this.selected=true;}
  setSelectionRange(start,end){this.selection=[start,end];}
}
let harnessSequence=0;

function harness(handler,{capabilities=V2,capabilityHandler,networkOnline=true,terms={speech_recognition_hints:[]},storageError=false,recoveryStore=new Map(),recoveryWriteError=false,criticalAutoread=false,clipboardError=false,clipboardImpl,autoGuardAdvance=true}={}) {
  const instance=++harnessSequence;
  const elements=Object.fromEntries(ids.map(id=>[id,new Element()])), calls=[], meta=[], captures=[], intervals=[],timeouts=[],timed=new Map(), clock={now:1234},downloads=[],blobs=new Map();
  elements['quick-command-policy'].value='deferred';
  const schedule=(callback,ms=0)=>{const id=timeouts.push(callback);timed.set(id,{callback,at:clock.now+ms,ms});return id;};
  const clear=id=>{timeouts[id-1]=null;timed.delete(id);};
  const advance=ms=>{clock.now+=ms;for(let i=0;i<100;i++){const due=[...timed].filter(([,timer])=>timer.at<=clock.now).sort((a,b)=>a[1].at-b[1].at)[0];if(!due)break;clear(due[0]);due[1].callback();}};
  guardClocks.add(()=>{if(autoGuardAdvance&&[...timed.values()].some(timer=>timer.ms<=800))advance(800);});
  class DownloadURL extends URL{static createObjectURL(blob){const url='blob:quick-test-'+blobs.size;blobs.set(url,blob);return url;}static revokeObjectURL(url){blobs.delete(url);}}
  elements['followup-autoread'].checked=true;
  // Legacy flow cases isolate followup TTS; dedicated tests enable the new
  // critical-first default explicitly (the real checkbox is checked in HTML).
  elements['critical-autoread'].checked=criticalAutoread;
  for(const id of startsHidden)elements[id].hidden=true;
  let requestSequence=0,defaultCreates=0;
  const fakeFetch=async(url,options)=>{
    // Read-only server checks at page load are tracked apart from draft requests.
    if(url.endsWith('/api/voice/capabilities')){meta.push(url);return capabilityHandler?capabilityHandler({url,method:options.method},meta.length):response(capabilities);}
    if(url.endsWith('/api/voice/terms')){meta.push(url);return response(terms);}
    const call={url,method:options.method,body:options.body?JSON.parse(options.body):null};calls.push(call);
    if(handler)return handler(call,calls.length);
    const sid=/\/api\/voice\/sessions\/([^/?]+)/.exec(url)?.[1];
    if(call.method==='POST'&&url.endsWith('/api/voice/sessions'))defaultCreates++;
    const id=sid||('VS-PAGE'+(defaultCreates>1?'-'+defaultCreates:''));
    return response({session:call.url.endsWith('/transcripts')?draft(2,{age:47,sex:'male'},{id}):draft(1,{},{id})});
  };
  class TestClient extends VoiceClient {
    constructor(options){super({...options,fetchImpl:fakeFetch,makeId:()=>`request-${instance}-${++requestSequence}`});}
  }
  // VM literals have a different Object.prototype from imported modules. Real
  // browser modules share a realm; round-trip this JSON-only boundary here.
  class TestQuickSession extends QuickSession{restoreRecovery(value){return super.restoreRecovery(JSON.parse(JSON.stringify(value)));}}
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
  const events={},stored=new Map(),navigations=[],copied=[];
  const navigator={onLine:networkOnline,clipboard:{writeText:async text=>{if(clipboardError)throw Error('clipboard unavailable');if(clipboardImpl)return clipboardImpl(text);copied.push(text);}}};
  const sessionStorage={getItem:k=>stored.get(k)??null,setItem(k,v){if(storageError)throw Error('storage unavailable');stored.set(k,v);},removeItem:k=>stored.delete(k)};
  const localStorage={getItem:k=>recoveryStore.get(k)??null,setItem(k,v){if(recoveryWriteError)throw Error('local storage quota');recoveryStore.set(k,v);},removeItem:k=>recoveryStore.delete(k)};
  const checkpointStorage={getItem:()=>{const raw=recoveryStore.get(PATIENT_RECORDS_KEY);if(!raw)return recoveryStore.get(RECOVERY_KEY)??null;const book=JSON.parse(raw);return book.records.find(record=>record.id===book.activeId)?.checkpoint??null;}};
  const context=vm.createContext({VoiceCapture:FakeCapture,VoiceClient:TestClient,QuickSession:TestQuickSession,chooseAnswer,answerVocabulary,RECORDING_LIMITS,findVoiceTrigger,CommandCommitGate,writeDraftHandoff,sessionStorage,
    RECOVERY_KEY,writeDraftRecovery,readDraftRecovery,clearDraftRecovery,localStorage,
    buildCriticalReview,planCriticalCorrection,classifyCriticalReply,
    PATIENT_RECORDS_KEY,emptyBook,readPatientRecords,writePatientRecords,createPatientRecord,updatePatientCheckpoint,selectPatientRecord,removePatientRecord,
    buildHospitalHandoff,buildObservationHistory,historySnapshotSignature,buildConnectionStatus,navigator,
    requestId:()=>`page-${instance}-${++requestSequence}`,location:{origin:'http://127.0.0.1:8000',assign:url=>navigations.push(url)},
    document:{getElementById:id=>{assert.ok(elements[id],`unknown element ${id}`);return elements[id];},
      querySelector:()=>new Element(),createElement:tag=>{const element=new Element(tag);if(tag==='a')element.click=()=>downloads.push({filename:element.download,blob:blobs.get(element.href)});return element;}},
    window:{addEventListener:(name,callback)=>{events[name]=callback;}},
    performance:{now:()=>clock.now},queueMicrotask,
    setInterval:callback=>intervals.push(callback),clearInterval:id=>{intervals[id-1]=null;},setTimeout:schedule,clearTimeout:clear,Blob,URL:DownloadURL,console});
  vm.runInContext(pageSource,context,{filename:'quick-test.js'});
  const el=id=>elements[id];
  const hold=async(id,{ms=1000,say=[]}={})=>{
    el('recording-hold').onclick();
    await el(id).onpointerdown({button:0,isPrimary:true,pointerId:1});
    // An array entry is one final result with recognizer alternatives, best first.
    for(const text of say)Array.isArray(text)?captures[0].final(text[0],text):captures[0].final(text);
    clock.now+=ms;
    await el(id).onpointerup({pointerId:1});await flush();
  };
  const tick=()=>{for(const callback of intervals)callback?.();};
  return {el,capture:captures[0],calls,meta,events,clock,hold,tick,stored,navigations,sessionStorage,recoveryStore,localStorage,checkpointStorage,copied,advance,downloads,read:expression=>vm.runInContext(expression,context),expireCheck:()=>timeouts.filter(Boolean).at(-1)?.()};
}

function patientServer({before,after,followup}={}){
  const sessions=new Map(),cached=new Map();let sequence=0;
  const copy=value=>JSON.parse(JSON.stringify(value));
  const handler=async call=>{
    const early=before?await before(call):undefined;if(early!==undefined)return early;
    const key=call.body?call.body.actor_id+'|'+call.body.client_request_id:null;
    if(key&&cached.has(key))return response({...copy(cached.get(key)),duplicate:true});
    const sid=/\/api\/voice\/sessions\/([^/?]+)/.exec(call.url)?.[1];let session;
    if(call.method==='GET'){session=sessions.get(sid);if(!session)return response({error:'not found'},404);return response({session:copy(session)});}
    if(call.url.endsWith('/api/voice/sessions')){session=draft(1,{},{id:'VS-PATIENT-'+(++sequence),actor_id:call.body.actor_id,utterances:[],...(followup?{followup}: {})});sessions.set(session.id,session);}
    else{
      session=sessions.get(sid);if(!session||session.actor_id!==call.body.actor_id)return response({error:'wrong owner'},403);
      if(session.version!==call.body.expected_version)return response({error:'VERSION_CONFLICT'},409);
      const old=copy(session),fields={...session.fields};
      if(call.method==='PATCH'){Object.assign(fields,call.body.changes);for(const field of call.body.unset_fields)delete fields[field];}
      else{if(call.body.text==='환자 A 정보')Object.assign(fields,{age:47,pulse:92});
        if(call.body.text==='환자 B 정보')Object.assign(fields,{age:61,pulse:105});
        if(call.body.text==='환자 C 정보')Object.assign(fields,{age:72,pulse:88});
        if(call.body.text==='A 추가 정보')Object.assign(fields,{breathing:'normal'});}
      session={...old,version:old.version+1,fields,field_metadata:{},utterances:call.method==='PATCH'?old.utterances:[...old.utterances,{transcript:call.body.text}]};sessions.set(sid,session);
    }
    const data={session:copy(session)};if(key)cached.set(key,copy(data));
    if(after)await after(call,data);return response(data);
  };
  return {handler,sessions};
}
const notebook=h=>readPatientRecords(h.localStorage,'http://127.0.0.1:8000').book;
const notePayload=(h,id)=>readDraftRecovery({getItem:()=>notebook(h).records.find(record=>record.id===id).checkpoint},'http://127.0.0.1:8000').payload;
const selectNote=async(h,id)=>{h.el('patient-select').value=id;await h.el('patient-switch').onclick();await flush();};
const speak=async(h,text)=>{await h.el('quick-record').onclick();h.capture.final(text);if(h.capture.active)await h.el('quick-record').onclick();await flush();};

test('command gate: default waits a real virtual 800ms before a single prefix save and action',async()=>{
  const h=harness(undefined,{autoGuardAdvance:false});assert.equal(h.el('quick-command-policy').value,'deferred');
  await h.el('quick-record').onclick();h.capture.final('47세 남성 병원 추천해줘 맥박 92');
  assert.equal(h.capture.active,true);assert.equal(h.calls.length,0);assert.match(h.el('quick-command-status').textContent,/후보 확인 중/);
  h.advance(799);await flush();assert.equal(h.calls.length,0);assert.equal(h.capture.active,true);
  h.advance(1);await flush();assert.equal(h.capture.active,false);assert.equal(h.calls[1].body.text,'47세 남성');
  assert.equal(h.el('recording-excluded-text').textContent,'맥박 92');assert.equal(h.el('recommendation-prep').hidden,false);
  const events=h.read('lastCommandTrace.trace.events');assert.equal(events.filter(event=>event.type==='commit').length,1);assert.equal(events.find(event=>event.type==='commit').atMs,800);
  assert.deepEqual(Array.from(events.filter(event=>event.type==='capture'),event=>event.reason),['stop_requested','recognizer_end','stop_drained']);
  assert.ok(events.filter(event=>event.type==='capture').every(event=>event.atMs>=800),'Capture marks are callback/request receipt times');
  h.advance(5000);h.capture.end();await flush();assert.equal(h.calls.length,2);
});

test('command gate: final time context cancels early stop and a manual end saves the entire observation',async()=>{
  const h=harness(undefined,{autoGuardAdvance:false});await h.el('quick-record').onclick();
  h.capture.final('합성 혈압 120 종료');h.advance(300);h.capture.final('시각과 증상 시작 시각은 다릅니다');
  h.advance(1000);await flush();assert.equal(h.capture.active,true);assert.equal(h.calls.length,0);
  await h.el('quick-record').onclick();await flush();assert.equal(h.calls[1].body.text,'합성 혈압 120 종료 시각과 증상 시작 시각은 다릅니다');
  assert.equal(h.el('recording-excluded').hidden,true);assert.equal(h.read('lastCommandTrace.trace.events.filter(e=>e.type==="commit").length'),0);
});

test('command gate: late final negation before the deadline does not stop and remains normal dictation',async()=>{
  const h=harness(undefined,{autoGuardAdvance:false});await h.el('quick-record').onclick();h.capture.final('혈압 120 종료');
  h.advance(400);h.capture.final('하지 마');h.advance(900);await flush();assert.equal(h.capture.active,true);assert.equal(h.calls.length,0);
  await h.el('quick-record').onclick();await flush();assert.equal(h.calls[1].body.text,'혈압 120 종료 하지 마');
  assert.equal(h.read('lastCommandTrace.trace.events.filter(e=>e.type==="commit").length'),0);
});

test('command gate: unresolved interim and empty interim cannot confirm or save a candidate',async()=>{
  const h=harness(undefined,{autoGuardAdvance:false});await h.el('quick-record').onclick();h.capture.final('혈압 120 종료');
  h.capture.callbacks.onInterim('하지 마');h.capture.callbacks.onInterim('');h.advance(1000);await flush();
  assert.equal(h.capture.active,true);assert.equal(h.calls.length,0);h.capture.end();await flush();
  assert.equal(h.calls.length,0);assert.equal(h.el('recording-excluded-text').textContent,'혈압 120 종료');
  assert.equal(h.el('quick-state').textContent,'명령 확인 보류');assert.match(h.el('quick-command-status').textContent,/미확정/);
});

test('command gate: unresolved negation during postcommit stop drain suppresses saving and action',async()=>{
  const h=harness(undefined,{autoGuardAdvance:false});let stops=0;
  h.capture.stop=async()=>{
    stops++;h.capture.callbacks.onInterim('하지 마');
    // Real VoiceCapture clears the interim before idle; clearing is not a final.
    h.capture.callbacks.onInterim('');h.capture.active=false;h.capture.callbacks.onState('idle');
  };
  await h.el('quick-record').onclick();h.capture.final('합성 혈압 120 병원 추천해줘 맥박 92');
  h.advance(800);await flush();assert.equal(stops,1);assert.equal(h.capture.active,false);
  assert.equal(h.calls.length,0);assert.equal(h.navigations.length,0);assert.equal(h.el('recommendation-prep').hidden,true);
  assert.equal(h.el('recording-excluded-text').textContent,'합성 혈압 120 병원 추천해줘 맥박 92');assert.equal(h.el('quick-state').textContent,'명령 확인 보류');
  assert.equal(h.read('attempt.trigger'),null);assert.equal(h.read('lastCommandTrace.trace.events.filter(e=>e.type==="commit").length'),1);
  assert.equal(h.read('lastCommandTrace.outcome.actionSuppressed'),true);assert.equal(h.read('lastCommandTrace.outcome.finished'),true);
  assert.equal(h.read('lastCommandTrace.trace.events.filter(e=>e.type==="interim"&&e.text==="하지 마").length'),1);
});

test('command gate: a fresh final resolves interim blocking and starts a fresh quiet window',async()=>{
  const h=harness(undefined,{autoGuardAdvance:false});await h.el('quick-record').onclick();h.capture.final('혈압 120 종료');
  h.capture.callbacks.onInterim('맥박');h.capture.callbacks.onInterim('');h.advance(2000);await flush();assert.equal(h.calls.length,0);
  h.capture.final('맥박 92');h.advance(799);await flush();assert.equal(h.calls.length,0);
  h.advance(1);await flush();assert.equal(h.calls[1].body.text,'혈압 120');assert.equal(h.el('recording-excluded-text').textContent,'맥박 92');
});

test('command gate: natural recognition end waits for the original pending quiet deadline',async()=>{
  const h=harness(undefined,{autoGuardAdvance:false});await h.el('quick-record').onclick();h.capture.final('47세 남성 병원 추천해줘');
  h.advance(300);h.capture.end();await flush();assert.equal(h.calls.length,0);assert.equal(h.el('quick-record').disabled,true);
  h.advance(499);await flush();assert.equal(h.calls.length,0);h.advance(1);await flush();
  assert.equal(h.calls[1].body.text,'47세 남성');assert.equal(h.el('recommendation-prep').hidden,false);assert.equal(h.read('stopping'),false);
});

test('command gate: explicit button end flushes only after final stop-drain text has been received',async()=>{
  const h=harness(undefined,{autoGuardAdvance:false});h.capture.stop=async()=>{h.capture.final('해 주세요');h.capture.active=false;h.capture.callbacks.onState('idle');};
  await h.el('quick-record').onclick();h.capture.final('혈압 120 종료');h.advance(100);await h.el('quick-record').onclick();await flush();
  assert.equal(h.calls[1].body.text,'혈압 120');assert.equal(h.read('attempt.stopCommand'),'종료 해 주세요');assert.match(h.el('recording-ended').textContent,/버튼으로 종료/);
  assert.equal(h.read('lastCommandTrace.trace.events.find(e=>e.type==="commit").reason'),'button');
});

test('command gate: button-only mode never runs a client command and preserves its words',async()=>{
  const h=harness(undefined,{autoGuardAdvance:false});h.el('quick-command-policy').value='observe';h.el('quick-command-policy').onchange();
  await h.el('quick-record').onclick();h.capture.final('47세 남성 병원 추천해줘');h.advance(800);await flush();
  assert.equal(h.capture.active,true);assert.equal(h.calls.length,0);await h.el('quick-record').onclick();await flush();
  assert.equal(h.calls[1].body.text,'47세 남성 병원 추천해줘');assert.equal(h.el('recommendation-prep').hidden,true);
  assert.equal(h.read('lastCommandTrace.trace.events.filter(e=>e.type==="commit").length'),0);assert.equal(h.read('lastCommandTrace.trace.events.filter(e=>e.type==="would_commit").length'),1);
});

test('command gate: cancel, speech error and pagehide suppress all old candidate timers',async()=>{
  for(const action of ['cancel','error','pagehide']){
    const h=harness(undefined,{autoGuardAdvance:false});await h.el('quick-record').onclick();h.capture.final('47세 남성 병원 추천해줘');
    if(action==='cancel')h.el('quick-cancel').onclick();else if(action==='error')h.capture.fail();else h.events.pagehide();
    h.advance(2000);await flush();assert.equal(h.calls.length,0,action);assert.equal(h.capture.active,false);assert.equal(h.el('recommendation-prep').hidden,true);
    assert.equal(h.read('lastCommandTrace.trace.events.filter(e=>e.type==="commit").length'),0);
  }
});

test('command gate: a cancelled old patient timer cannot save or change the next patient',async()=>{
  const h=harness(undefined,{autoGuardAdvance:false});await h.el('quick-record').onclick();h.capture.final('첫 환자 병원 추천해줘');h.el('quick-cancel').onclick();
  const old=notebook(h).activeId;await h.el('patient-new').onclick();await flush();const next=notebook(h).activeId;assert.notEqual(old,next);
  h.advance(2000);await flush();assert.equal(notebook(h).activeId,next);assert.equal(h.calls.length,0);assert.equal(h.el('quick-command-export').disabled,true);
});

test('command gate: JSON download is explicit, keeps source and event timing, and exports no clinical IDs',async()=>{
  const h=harness(undefined,{autoGuardAdvance:false});assert.equal(h.downloads.length,0);await h.el('quick-record').onclick();h.capture.final('합성 혈압 120 종료');
  h.advance(800);await flush();const writes=h.calls.length;assert.equal(h.downloads.length,0);
  assert.equal(h.el('quick-command-export').disabled,false);h.el('quick-command-export').onclick();
  assert.equal(h.downloads.length,1);const data=JSON.parse(await h.downloads[0].blob.text());
  assert.deepEqual(data.sourceFinals,['합성 혈압 120 종료']);assert.equal(data.trace.policy,'deferred');assert.equal(data.experimental,true);assert.equal(data.actualAudioSaved,false);
  assert.equal(data.trace.events.find(event=>event.type==='commit').atMs,800);assert.equal(data.condition.inputRoute,'browser_managed');
  assert.doesNotMatch(JSON.stringify(data),/actorId|actor_id|sessionId|patientId|client_request_id|http:\/\//);assert.equal(h.calls.length,writes);
});

test('patients A and B keep separate actors, sessions, clinical fields and source history across switches',async()=>{
  const server=patientServer(),h=harness(server.handler);await speak(h,'환자 A 정보');const a=notebook(h).activeId,first=notePayload(h,a);
  await h.el('patient-new').onclick();assert.match(h.el('patient-current').textContent,/02/);assert.equal(h.calls.filter(c=>c.method==='POST').length,2);
  await speak(h,'환자 B 정보');const b=notebook(h).activeId,second=notePayload(h,b);
  assert.notEqual(first.actorId,second.actorId);assert.notEqual(first.sessionId,second.sessionId);assert.equal(notebook(h).records.length,2);
  const writes=h.calls.filter(c=>c.method!=='GET').length;await selectNote(h,a);
  assert.match(h.el('patient-current').textContent,/01/);assert.match(textOf(h.el('summary-chips')),/47/);assert.doesNotMatch(textOf(h.el('quick-history')),/환자 B 정보/);
  await h.el('patient-previous').onclick();await flush();assert.match(h.el('patient-current').textContent,/02/);assert.match(textOf(h.el('summary-chips')),/61/);
  assert.equal(h.calls.filter(c=>c.method!=='GET').length,writes);assert.equal(h.capture.active,false);
});

test('manual text, unsent speech and dirty editor proposals stay with their original patient',async()=>{
  const server=patientServer(),h=harness(server.handler);await speak(h,'환자 A 정보');const a=notebook(h).activeId;
  h.el('recording-review').value='A 미반영 수동 메모';h.el('recording-review').oninput();
  criticalButton(h,'pulse','edit').onclick();criticalInput(h,'pulse').value='130';criticalInput(h,'pulse').oninput();
  await h.el('patient-new').onclick();assert.equal(h.el('recording-review').value,'');assert.equal(h.el('critical-editor').hidden,true);
  await speak(h,'환자 B 정보');await selectNote(h,a);
  assert.match(h.el('recording-review').value,/A 미반영 수동 메모/);assert.match(h.el('recording-review').value,/맥박: 130/);
  assert.equal(server.sessions.get(notePayload(h,a).sessionId).fields.pulse,92);
});

test('voice new-patient saves the prefix first and keeps command suffix on the old patient',async()=>{
  const server=patientServer(),h=harness(server.handler);await speak(h,'환자 A 정보 새 환자 시작 A 전환 뒤 추가 메모');
  const book=notebook(h),a=book.records.find(record=>record.number===1);assert.equal(book.records.length,2);assert.match(h.el('patient-current').textContent,/02/);
  assert.equal(h.calls.filter(c=>c.url.endsWith('/transcripts')).length,1);assert.equal(h.calls.find(c=>c.url.endsWith('/transcripts')).body.text,'환자 A 정보');
  assert.equal(notePayload(h,a.id).recoveryText,'A 전환 뒤 추가 메모');assert.equal(h.el('recording-excluded').hidden,true);
  await selectNote(h,a.id);assert.equal(h.el('recording-excluded-text').textContent,'A 전환 뒤 추가 메모');
});

test('command-only patient navigation never creates an empty server draft or repeats a command',async()=>{
  const server=patientServer(),h=harness(server.handler);await speak(h,'새 환자 시작');assert.equal(h.calls.length,0);assert.equal(notebook(h).records.length,2);
  const current=notebook(h).activeId;await speak(h,'이전 환자로 돌아가');assert.equal(h.calls.length,0);assert.notEqual(notebook(h).activeId,current);
  h.capture.end();await flush();assert.equal(notebook(h).records.length,2);
});

test('voice return to the previous patient selects the saved draft without writing it',async()=>{
  const server=patientServer(),h=harness(server.handler);await speak(h,'환자 A 정보');const a=notebook(h).activeId;
  await h.el('patient-new').onclick();await speak(h,'환자 B 정보');const writes=h.calls.filter(c=>c.method!=='GET').length;
  await speak(h,'이전 환자로 돌아가');assert.equal(notebook(h).activeId,a);assert.match(textOf(h.el('summary-chips')),/47/);
  assert.equal(h.calls.filter(c=>c.method!=='GET').length,writes);
});

test('an uncertain prefix save blocks all patient changes until exact retry succeeds',async()=>{
  let failed=false;const server=patientServer({after:call=>{if(call.url.endsWith('/transcripts')&&!failed){failed=true;throw Error('response lost');}}});
  const h=harness(server.handler);await speak(h,'환자 A 정보 새 환자 시작');const a=notebook(h).activeId,original=h.calls.find(c=>c.url.endsWith('/transcripts')).body;
  await h.el('patient-new').onclick();h.el('patient-remove').onclick();assert.equal(notebook(h).activeId,a);assert.equal(notebook(h).records.length,1);
  await h.el('quick-retry').onclick();await flush();assert.equal(notebook(h).records.length,2);assert.notEqual(notebook(h).activeId,a);
  assert.deepEqual(h.calls.filter(c=>c.url.endsWith('/transcripts'))[1].body,original);assert.equal(server.sessions.values().next().value.utterances.length,1);
});

test('reload restores the active patient and pending command in its original record before a single switch',async()=>{
  let failed=false;const recoveryStore=new Map(),server=patientServer({after:call=>{if(call.url.endsWith('/transcripts')&&!failed){failed=true;throw Error('response lost');}}});
  const first=harness(server.handler,{recoveryStore});await speak(first,'환자 A 정보 새 환자 시작');const a=notebook(first).activeId;
  const next=harness(server.handler,{recoveryStore});await flush();assert.equal(notebook(next).activeId,a);assert.equal(next.calls.some(c=>c.method==='POST'),false);
  await next.el('quick-retry').onclick();await flush();assert.equal(notebook(next).records.length,2);assert.notEqual(notebook(next).activeId,a);
  assert.equal(server.sessions.values().next().value.utterances.length,1);
});

test('target GET failure keeps the source patient, selection pointer and source input intact',async()=>{
  let rejectSid=null;const server=patientServer({before:call=>call.method==='GET'&&call.url.includes(rejectSid||'__never__')?response({error:'unavailable'},503):undefined});
  const h=harness(server.handler);await speak(h,'환자 A 정보');const a=notebook(h).activeId;await h.el('patient-new').onclick();await speak(h,'환자 B 정보');const b=notebook(h).activeId;
  rejectSid=notePayload(h,a).sessionId;h.el('recording-review').value='B 연결 실패 전 메모';h.el('recording-review').oninput();await selectNote(h,a);
  assert.equal(notebook(h).activeId,b);assert.match(h.el('patient-current').textContent,/02/);assert.equal(h.el('recording-review').value,'B 연결 실패 전 메모');
});

test('stale critical callbacks cannot read, edit or confirm for another patient',async()=>{
  const server=patientServer(),h=harness(server.handler);await speak(h,'환자 A 정보');const stale=['read','edit','confirm','voice'].map(action=>criticalButton(h,'pulse',action).onclick);
  await h.el('patient-new').onclick();await speak(h,'환자 B 정보');const calls=h.calls.length,spoken=h.capture.spoken.length;
  for(const callback of stale)await callback();await flush();assert.equal(h.calls.length,calls);assert.equal(h.capture.spoken.length,spoken);assert.equal(h.el('critical-editor').hidden,true);
  assert.match(h.el('patient-current').textContent,/02/);assert.match(h.el('critical-summary').textContent,/1개 대기/);
});

test('identical values and identical questions do not let the old patient callbacks confirm or answer for the new patient',async()=>{
  const followup=plan(question('breathing','호흡 상태는?',['breathing'])),server=patientServer({followup}),h=harness(server.handler);
  await speak(h,'환자 A 정보');const oldConfirm=criticalButton(h,'pulse','confirm').onclick,oldAnswer=h.el('followup-options').children[0].onclick;
  await h.el('patient-new').onclick();await speak(h,'환자 A 정보');const before=h.calls.length;
  await oldConfirm();await oldAnswer();await flush();assert.equal(h.calls.length,before);assert.match(h.el('critical-summary').textContent,/1개 대기/);
  assert.match(h.el('patient-current').textContent,/02/);assert.equal(h.el('critical-editor').hidden,true);
});

test('removing one local patient preserves all other checkpoints and all server records',async()=>{
  const server=patientServer(),h=harness(server.handler);await speak(h,'환자 A 정보');const a=notebook(h).activeId;await h.el('patient-new').onclick();await speak(h,'환자 B 정보');
  h.el('patient-remove').onclick();assert.equal(notebook(h).records.length,1);assert.equal(notebook(h).records[0].id,a);assert.equal(notebook(h).activeId,null);
  assert.equal(server.sessions.size,2);assert.equal(h.el('quick-record').disabled,true);await selectNote(h,a);assert.match(textOf(h.el('summary-chips')),/47/);
});

test('a quota failure prevents switching and preserves the active record and unsubmitted text',async()=>{
  const server=patientServer(),h=harness(server.handler);await speak(h,'환자 A 정보');const id=notebook(h).activeId;
  h.el('recording-review').value='보관해야 할 A 메모';h.el('recording-review').oninput();const raw=h.recoveryStore.get(PATIENT_RECORDS_KEY);
  h.localStorage.setItem=()=>{throw Error('quota');};await h.el('patient-new').onclick();assert.equal(h.recoveryStore.get(PATIENT_RECORDS_KEY),raw);
  assert.equal(notebook(h).activeId,id);assert.equal(h.el('recording-review').value,'보관해야 할 A 메모');assert.match(h.el('patient-current').textContent,/01/);
});

test('a storage implementation silently ignoring writes cannot authorize a network write',async()=>{
  const h=harness();h.localStorage.setItem=()=>{};await speak(h,'환자 A 정보');
  assert.equal(h.calls.length,0);assert.equal(h.el('quick-retry').hidden,false);assert.match(h.el('recovery-status').textContent,/저장 실패/);
});

test('single-draft migration preserves the exact request, writes the notebook first and removes the legacy key last',async()=>{
  const server=patientServer(),seed=harness(server.handler);await speak(seed,'환자 A 정보');const payload=seed.checkpointStorage.getItem(),recoveryStore=new Map([[RECOVERY_KEY,payload]]);
  const next=harness(server.handler,{recoveryStore});await flush();assert.equal(recoveryStore.has(RECOVERY_KEY),false);assert.equal(notebook(next).records.length,1);
  assert.equal(next.checkpointStorage.getItem(),payload);assert.equal(next.calls.filter(c=>c.method==='POST').length,0);assert.match(textOf(next.el('summary-chips')),/47/);
});

test('migration storage failure leaves the legacy checkpoint untouched and never creates a replacement draft',async()=>{
  const server=patientServer(),seed=harness(server.handler);await speak(seed,'환자 A 정보');const payload=seed.checkpointStorage.getItem(),recoveryStore=new Map([[RECOVERY_KEY,payload]]);
  const next=harness(server.handler,{recoveryStore,recoveryWriteError:true});await flush();assert.equal(recoveryStore.get(RECOVERY_KEY),payload);
  assert.equal(recoveryStore.has(PATIENT_RECORDS_KEY),false);assert.equal(next.calls.length,0);await next.el('patient-new').onclick();assert.equal(next.calls.length,0);
});

test('a blocked or expired target never replaces the source patient or creates another server draft',async()=>{
  const server=patientServer(),h=harness(server.handler);await speak(h,'환자 A 정보');const a=notebook(h).activeId;await h.el('patient-new').onclick();await speak(h,'환자 B 정보');const b=notebook(h).activeId;
  const book=notebook(h),entry=book.records.find(record=>record.id===a),p=JSON.parse(entry.checkpoint);
  p.createdAt=Date.now()-13*60*60*1000;p.expiresAt=p.createdAt+12*60*60*1000;entry.checkpoint=JSON.stringify(p);
  const raw=h.recoveryStore.get(PATIENT_RECORDS_KEY);writePatientRecords(h.localStorage,book,{expectedRaw:raw});
  const next=harness(server.handler,{recoveryStore:h.recoveryStore});await flush();const writes=next.calls.filter(c=>c.method!=='GET').length;
  await selectNote(next,a);assert.equal(notebook(next).activeId,b);assert.match(next.el('patient-current').textContent,/02/);
  assert.equal(next.calls.filter(c=>c.method!=='GET').length,writes);assert.match(next.el('quick-error').textContent,/12시간/);
});

test('a concurrent notebook change during target GET cannot activate the stale target response',async()=>{
  let gateSid=null,release;const gate=new Promise(resolve=>{release=resolve;});
  const server=patientServer({before:call=>call.method==='GET'&&call.url.includes(gateSid||'__never__')?gate:undefined});
  const h=harness(server.handler);await speak(h,'환자 A 정보');const a=notebook(h).activeId;await h.el('patient-new').onclick();await speak(h,'환자 B 정보');
  gateSid=notePayload(h,a).sessionId;h.el('patient-select').value=a;const pending=h.el('patient-switch').onclick();
  h.recoveryStore.set(PATIENT_RECORDS_KEY,'another tab notebook');release(response({session:server.sessions.get(gateSid)}));await pending;await flush();
  assert.match(h.el('patient-current').textContent,/02/);assert.equal(h.recoveryStore.get(PATIENT_RECORDS_KEY),'another tab notebook');assert.equal(h.el('quick-record').disabled,true);
});

test('invalid legacy data is preserved until the explicit local reset and cannot be overwritten by new-patient',async()=>{
  const recoveryStore=new Map([[RECOVERY_KEY,'{broken']]),h=harness(undefined,{recoveryStore});await flush();
  await h.el('patient-new').onclick();assert.equal(recoveryStore.get(RECOVERY_KEY),'{broken');assert.equal(h.calls.length,0);
  h.el('patient-reset-invalid').onclick();assert.equal(recoveryStore.has(RECOVERY_KEY),false);assert.equal(h.calls.length,0);assert.equal(h.el('quick-record').disabled,false);
});

async function criticalHarness(fields,{metadata={},recoveryStore=new Map(),criticalAutoread=true,before,load=true}={}){
  let session=draft(),patches=0;const copy=value=>JSON.parse(JSON.stringify(value));
  const h=harness(async call=>{
    const result=before?await before(call,{session:copy(session),patches}):undefined;if(result!==undefined)return result;
    if(call.method==='PATCH'){
      patches++;if(call.body.expected_version!==session.version)return response({error:'VERSION_CONFLICT'},409);
      const nextFields={...session.fields,...call.body.changes};for(const field of call.body.unset_fields)delete nextFields[field];
      session=draft(session.version+1,nextFields,{field_metadata:{},utterances:session.utterances||[]});
    }else if(call.url.endsWith('/transcripts'))session=draft(2,fields,{field_metadata:metadata,utterances:[{transcript:'합성 입력'}]});
    return response({session:copy(session)});
  },{recoveryStore,criticalAutoread});
  h.setSession=value=>{session=copy(value);};h.getSession=()=>copy(session);
  if(load){await h.el('quick-record').onclick();h.capture.final('합성 입력');await h.el('quick-record').onclick();await flush();}
  return h;
}

test('handoff preview is built from the current saved draft by GET and excludes raw and derived clinical guesses',async()=>{
  const h=await criticalHarness({age:47,sex:'male',pulse:92,breathing:'unknown',denied_symptoms:['구토'],uncertain_symptoms:['출혈'],pre_ktas:1,stemi_suspected:true});
  h.el('recording-review').value='복사되면 안 되는 미반영 개인메모';h.el('recording-review').oninput();
  const before=h.calls.filter(c=>c.method!=='GET').length;await h.el('handoff-generate').onclick();await flush();
  const text=h.el('handoff-text').value;assert.match(text,/환자 01/);assert.match(text,/47 세/);assert.match(text,/맥박: 92/);
  assert.match(text,/호흡: 미확인/);assert.match(text,/구토 없음/);assert.match(text,/출혈 미확인/);assert.match(text,/미반영 내용 있음/);
  assert.doesNotMatch(text,/개인메모|pre_ktas|STEMI|stemi/);assert.equal(h.calls.filter(c=>c.method!=='GET').length,before);
  assert.equal(h.copied.length,0);assert.equal(h.el('handoff-copy').disabled,false);
});

test('copy and read each verify the same preview by GET without clinical writes',async()=>{
  const h=await criticalHarness({pulse:92,breathing:'absent'});await h.el('handoff-generate').onclick();const preview=h.el('handoff-text').value;
  const before=h.calls.length,spoken=h.capture.spoken.length;await h.el('handoff-copy').onclick();await h.el('handoff-read').onclick();await flush();
  assert.deepEqual(h.copied,[preview]);assert.equal(h.capture.spoken.length,spoken+1);assert.equal(h.capture.spoken.at(-1),preview);
  assert.equal(h.calls.slice(before).every(call=>call.method==='GET'),true);assert.equal(h.capture.active,false);
  assert.equal(h.calls.some(call=>call.url.endsWith('/confirm')||call.url.endsWith('/submit')),false);
});

test('clipboard failure offers a real selection fallback and never reports successful copying',async()=>{
  const server=patientServer(),h=harness(server.handler,{clipboardError:true});await speak(h,'환자 A 정보');await h.el('handoff-generate').onclick();
  const preview=h.el('handoff-text').value;await h.el('handoff-copy').onclick();await flush();
  assert.equal(h.copied.length,0);assert.equal(h.el('handoff-text').selected,true);assert.equal(h.el('handoff-text').focused,true);
  assert.deepEqual(h.el('handoff-text').selection,[0,preview.length]);assert.match(h.el('handoff-status').textContent,/직접 복사/);
  assert.doesNotMatch(h.el('handoff-status').textContent,/복사했습니다/);
});

test('remote changes stop copy/read and require an explicitly regenerated summary',async()=>{
  const h=await criticalHarness({pulse:92});await h.el('handoff-generate').onclick();h.setSession(draft(3,{pulse:130}));
  await h.el('handoff-copy').onclick();await flush();assert.equal(h.copied.length,0);assert.equal(h.el('handoff-text').value,'');
  assert.equal(h.el('handoff-copy').disabled,true);await h.el('handoff-generate').onclick();assert.match(h.el('handoff-text').value,/맥박: 130/);
  const spoken=h.capture.spoken.length;h.setSession(draft(4,{pulse:140}));await h.el('handoff-read').onclick();await flush();
  assert.equal(h.capture.spoken.length,spoken);assert.equal(h.el('handoff-text').value,'');
});

test('new local unapplied content and completed readback review invalidate an earlier preview',async()=>{
  const h=await criticalHarness({pulse:92});await h.el('handoff-generate').onclick();
  h.el('recording-review').value='아직 반영하지 않은 메모';h.el('recording-review').oninput();
  assert.equal(h.el('handoff-text').value,'');assert.equal(h.el('handoff-copy').disabled,true);
  await h.el('handoff-generate').onclick();assert.match(h.el('handoff-text').value,/복창 확인 대기: 맥박/);
  await h.el('critical-all').onclick();await flush();assert.equal(h.el('handoff-text').value,'');
  await h.el('handoff-generate').onclick();assert.match(h.el('handoff-text').value,/복창 확인 대기: 없음/);
});

test('a held clipboard request blocks changing patients or starting recording until it settles',async()=>{
  let release;const pending=new Promise(resolve=>{release=resolve;});const server=patientServer(),requests=[];
  const h=harness(server.handler,{clipboardImpl:text=>{requests.push(text);return pending;}});await speak(h,'환자 A 정보');await h.el('handoff-generate').onclick();
  const owner=notebook(h).activeId,copy=h.el('handoff-copy').onclick();await flush();assert.equal(requests.length,1);
  await h.el('patient-new').onclick();await h.el('quick-record').onclick();assert.equal(notebook(h).activeId,owner);assert.equal(h.capture.active,false);
  release();await copy;await flush();assert.match(h.el('handoff-status').textContent,/복사했습니다/);
});

test('switching patients clears the preview and old copy/read/generate callbacks cannot act on the new patient',async()=>{
  const server=patientServer(),h=harness(server.handler);await speak(h,'환자 A 정보');await h.el('handoff-generate').onclick();
  const stale=[h.el('handoff-copy').onclick,h.el('handoff-read').onclick,h.el('handoff-generate').onclick];
  await h.el('patient-new').onclick();assert.equal(h.el('handoff-text').value,'');await speak(h,'환자 B 정보');await h.el('handoff-generate').onclick();
  const before=h.calls.length,spoken=h.capture.spoken.length;for(const action of stale)await action();await flush();
  assert.equal(h.calls.length,before);assert.equal(h.capture.spoken.length,spoken);assert.equal(h.copied.length,0);
  assert.match(h.el('handoff-text').value,/환자 02/);assert.match(h.el('handoff-text').value,/61 세/);assert.doesNotMatch(h.el('handoff-text').value,/47 세/);
});

test('an envelope change during a summary GET never publishes a stale patient preview',async()=>{
  let gate=false,release;const pending=new Promise(resolve=>{release=resolve;});
  const h=await criticalHarness({pulse:92},{before:call=>gate&&call.method==='GET'?pending:undefined});gate=true;
  const generating=h.el('handoff-generate').onclick();h.recoveryStore.set(PATIENT_RECORDS_KEY,'other tab notebook');release(response({session:h.getSession()}));await generating;await flush();
  assert.equal(h.el('handoff-text').value,'');assert.equal(h.copied.length,0);assert.equal(h.el('handoff-copy').disabled,true);
});

test('wrong patient responses and unavailable GET cannot generate or export a summary',async()=>{
  for(const responseMode of ['sid','actor','state','patient','offline']){
    let fault=false;const h=await criticalHarness({pulse:92},{before:call=>fault&&call.method==='GET'?
      responseMode==='offline'?response({error:'offline'},503):response({session:draft(2,{pulse:92},{...(responseMode==='sid'?{id:'VS-OTHER'}:responseMode==='actor'?{actor_id:'OTHER'}:responseMode==='state'?{state:'SUBMITTED'}:{patient_id:'P-OTHER'})})}):undefined});
    fault=true;await h.el('handoff-generate').onclick();await flush();assert.equal(h.el('handoff-text').value,'');assert.equal(h.el('handoff-copy').disabled,true);assert.equal(h.copied.length,0);
  }
});

test('previous flagged values remain outside current observations in the handoff preview',async()=>{
  const h=await criticalHarness({systolic_bp:120,diastolic_bp:80,pulse:92},{metadata:{systolic_bp:{needs_clarification:true},diastolic_bp:{needs_clarification:true}}});
  await h.el('handoff-generate').onclick();const text=h.el('handoff-text').value;
  const observations=text.split('[저장된 관찰]')[1].split('[미확인·재확인]')[0];assert.doesNotMatch(observations,/120|80/);
  const uncertain=text.split('[미확인·재확인]')[1].split('[최근 기록 변경]')[0];assert.match(uncertain,/이전 관찰 120/);assert.match(uncertain,/이전 관찰 80/);assert.match(uncertain,/현재 미확인/);
});

test('voice handoff command saves its prefix then opens a preview while trailing notes remain unapplied',async()=>{
  const server=patientServer(),h=harness(server.handler);await speak(h,'환자 A 정보 인계 요약해줘 아직 확인하지 않은 추가 메모');
  assert.equal(h.calls.filter(call=>call.url.endsWith('/transcripts')).length,1);assert.equal(h.calls.find(call=>call.url.endsWith('/transcripts')).body.text,'환자 A 정보');
  assert.match(h.el('handoff-text').value,/47 세/);assert.match(h.el('handoff-text').value,/미반영 내용 있음/);
  assert.doesNotMatch(h.el('handoff-text').value,/아직 확인하지 않은 추가 메모/);assert.equal(h.copied.length,0);
});

test('a command-only handoff request creates no empty server draft and summary without facts cannot be copied',async()=>{
  const h=harness();await speak(h,'인계 요약해줘');assert.equal(h.calls.length,0);assert.equal(h.copied.length,0);
  const empty=await criticalHarness({});await empty.el('handoff-generate').onclick();assert.equal(empty.el('handoff-copy').disabled,true);assert.equal(empty.el('handoff-read').disabled,true);
});

test('a network-uncertain note must finish the original request before generating its handoff',async()=>{
  let failed=false;const server=patientServer({after:call=>{if(call.url.endsWith('/transcripts')&&!failed){failed=true;throw Error('response lost');}}}),h=harness(server.handler);
  await speak(h,'환자 A 정보 인계 요약해줘');assert.equal(h.el('handoff-text').value,'');assert.equal(h.el('quick-retry').hidden,false);
  await h.el('quick-retry').onclick();await flush();assert.match(h.el('handoff-text').value,/47 세/);assert.equal(server.sessions.values().next().value.utterances.length,1);assert.equal(h.copied.length,0);
});

test('critical readback speaks structured values and distinct negative/unknown states once with the microphone closed',async()=>{
  const h=await criticalHarness({systolic_bp:120,diastolic_bp:80,breathing:'absent',denied_symptoms:['구토'],uncertain_symptoms:['출혈']});
  assert.equal(h.capture.spoken.length,1);const spoken=h.capture.spoken[0];
  assert.match(spoken,/수축기 혈압 120/);assert.match(spoken,/이완기 혈압 80/);assert.match(spoken,/호흡 없음/);
  assert.match(spoken,/구토 없음/);assert.match(spoken,/출혈 미확인/);assert.doesNotMatch(spoken,/출혈 없음/);
  assert.equal(h.capture.active,false);assert.match(h.el('critical-summary').textContent,/3개 대기/);
  assert.equal(h.calls.filter(c=>c.url.endsWith('/confirm')||c.url.endsWith('/submit')).length,0);
});

test('marking an item or all items checks the displayed server version by GET and never clinically confirms a draft',async()=>{
  const h=await criticalHarness({pulse:92,breathing:'unknown'});const writes=h.calls.filter(c=>c.method!=='GET').length;
  await criticalButton(h,'pulse','confirm').onclick();await flush();
  assert.match(textOf(criticalCard(h,'pulse')),/입력 내용 확인됨/);assert.match(h.el('critical-summary').textContent,/1개 대기/);
  await h.el('critical-all').onclick();await flush();assert.match(h.el('critical-summary').textContent,/확인됨/);
  assert.equal(h.calls.filter(c=>c.method!=='GET').length,writes);assert.equal(h.getSession().confirmed,undefined);
});

test('a server change during confirmation does not mark the old values as checked',async()=>{
  const h=await criticalHarness({pulse:92});h.setSession(draft(3,{pulse:130}));await h.el('critical-all').onclick();await flush();
  assert.match(h.el('critical-summary').textContent,/1개 대기/);assert.match(textOf(criticalCard(h,'pulse')),/130/);
  assert.doesNotMatch(textOf(criticalCard(h,'pulse')),/입력 내용 확인됨/);assert.match(h.el('quick-error').textContent,/갱신/);
});

test('a freshness response with a changed owner or clinical state never grants a local confirmation',async()=>{
  for(const extra of [{state:'SUBMITTED'},{patient_id:'P-OTHER'},{actor_id:'OTHER'}]){
    const h=await criticalHarness({pulse:92});h.setSession(draft(2,{pulse:92},extra));await h.el('critical-all').onclick();await flush();
    assert.match(h.el('critical-summary').textContent,/1개 대기/);assert.doesNotMatch(textOf(criticalCard(h,'pulse')),/입력 내용 확인됨/);
    assert.equal(h.el('quick-record').disabled,true);assert.equal(h.calls.some(c=>c.url.endsWith('/confirm')),false);
  }
});

test('readback failures and failed freshness GET do not imply confirmation',async()=>{
  const h=await criticalHarness({pulse:92},{before:call=>call.method==='GET'?response({error:'offline'},503):undefined});
  h.capture.speakError=Error('speaker failed');h.el('critical-read').onclick();assert.match(h.el('quick-error').textContent,/읽지 못/);
  await criticalButton(h,'pulse','confirm').onclick();await flush();assert.match(h.el('critical-summary').textContent,/1개 대기/);
});

test('single-item correction sends a narrow atomic PATCH and requires confirmation again',async()=>{
  const h=await criticalHarness({systolic_bp:120,diastolic_bp:80,pulse:92});await h.el('critical-all').onclick();
  criticalButton(h,'blood_pressure','edit').onclick();criticalInput(h,'systolic_bp').value='90';criticalInput(h,'systolic_bp').oninput();
  criticalInput(h,'diastolic_bp').value='60';criticalInput(h,'diastolic_bp').oninput();await h.el('critical-save').onclick();await flush();
  const patch=h.calls.find(c=>c.method==='PATCH');assert.deepEqual(patch.body.changes,{systolic_bp:90,diastolic_bp:60});
  assert.deepEqual(patch.body.unset_fields,[]);assert.equal(patch.body.change_kind,'correction');assert.equal(patch.body.expected_version,2);
  assert.equal(h.getSession().fields.pulse,92);assert.equal(h.el('critical-editor').hidden,true);
  assert.match(textOf(criticalCard(h,'blood_pressure')),/복창 확인 대기/);assert.match(h.el('critical-summary').textContent,/1개 대기/);
});

test('missing is not zero and an explicit unknown breathing correction is not absence',async()=>{
  const h=await criticalHarness({pulse:92,breathing:'absent'});criticalButton(h,'breathing','edit').onclick();
  criticalInput(h,'breathing').value='unknown';await h.el('critical-save').onclick();await flush();
  assert.equal(h.getSession().fields.breathing,'unknown');assert.match(textOf(criticalCard(h,'breathing')),/호흡 미확인/);
  criticalButton(h,'pulse','edit').onclick();criticalInput(h,'pulse').value='';criticalInput(h,'pulse').oninput();
  await h.el('critical-save').onclick();await flush();assert.equal(h.getSession().fields.pulse,undefined);
  assert.deepEqual(h.calls.filter(c=>c.method==='PATCH').at(-1).body.unset_fields,['pulse']);
});

test('unclear earlier values are not spoken as current or confirmable, and explicit same-value correction resolves review state',async()=>{
  const h=await criticalHarness({pulse:92},{metadata:{pulse:{needs_clarification:true,evidence:['이전 값']}}});
  assert.doesNotMatch(h.capture.spoken[0],/92/);assert.match(h.capture.spoken[0],/재확인/);
  assert.equal(criticalButton(h,'pulse','confirm').disabled,true);assert.equal(h.el('critical-all').disabled,true);
  criticalButton(h,'pulse','edit').onclick();assert.equal(criticalInput(h,'pulse').value,'');
  criticalInput(h,'pulse').value='92';criticalInput(h,'pulse').oninput();await h.el('critical-save').onclick();await flush();
  assert.deepEqual(h.calls.find(c=>c.method==='PATCH').body.changes,{pulse:92});assert.equal(criticalButton(h,'pulse','confirm').disabled,false);
  assert.match(h.el('critical-summary').textContent,/1개 대기/);
});

test('a symptom polarity correction updates only the three related lists and rejects contradictions',async()=>{
  const h=await criticalHarness({symptoms:['흉통'],denied_symptoms:['구토'],uncertain_symptoms:['출혈'],pulse:92});
  criticalButton(h,'symptom_polarity','edit').onclick();criticalInput(h,'symptoms').value='흉통, 출혈';
  criticalInput(h,'uncertain_symptoms').value='출혈';await h.el('critical-save').onclick();await flush();assert.equal(h.calls.some(c=>c.method==='PATCH'),false);
  criticalInput(h,'uncertain_symptoms').value='';await h.el('critical-save').onclick();await flush();
  const patch=h.calls.find(c=>c.method==='PATCH');assert.deepEqual(patch.body.changes,{symptoms:['흉통','출혈']});
  assert.deepEqual(patch.body.unset_fields,['uncertain_symptoms']);assert.equal(h.getSession().fields.pulse,92);
});

test('dedicated spoken yes confirms after button stop, and no opens only that item editor',async()=>{
  const h=await criticalHarness({pulse:92});const writes=h.calls.filter(c=>c.method!=='GET').length;
  await criticalButton(h,'pulse','voice').onclick();h.capture.final('맞아요');
  await criticalButton(h,'pulse','voice').onclick();await flush();
  assert.match(textOf(criticalCard(h,'pulse')),/입력 내용 확인됨/);assert.equal(h.calls.filter(c=>c.method!=='GET').length,writes);
  await criticalButton(h,'pulse','voice').onclick();h.capture.final('아니요');await criticalButton(h,'pulse','voice').onclick();await flush();
  assert.equal(h.el('critical-editor').hidden,false);assert.equal(h.calls.filter(c=>c.method!=='GET').length,writes);
  assert.match(h.el('critical-summary').textContent,/1개 대기/);h.el('critical-cancel').onclick();assert.match(h.el('critical-summary').textContent,/1개 대기/);
});

test('extra clinical words in a confirmation are never blanket-confirmed or sent and survive reload for review',async()=>{
  const recoveryStore=new Map(),h=await criticalHarness({pulse:92},{recoveryStore});const writes=h.calls.filter(c=>c.method!=='GET').length;
  await criticalButton(h,'pulse','voice').onclick();h.capture.final('네 맥박 130으로 변경');h.events.pagehide();
  const next=harness(()=>response({session:h.getSession()}),{recoveryStore});await flush();
  assert.equal(h.calls.filter(c=>c.method!=='GET').length,writes);assert.equal(next.calls.filter(c=>c.method!=='GET').length,0);
  assert.match(next.el('recording-excluded-text').textContent,/네 맥박 130으로 변경/);assert.match(next.el('critical-summary').textContent,/1개 대기/);
});

test('an unsaved editor survives reload as review text without automatically patching',async()=>{
  const recoveryStore=new Map(),h=await criticalHarness({pulse:92},{recoveryStore});criticalButton(h,'pulse','edit').onclick();
  criticalInput(h,'pulse').value='130';criticalInput(h,'pulse').oninput();
  const next=harness(()=>response({session:h.getSession()}),{recoveryStore});await flush();
  assert.match(next.el('recording-review').value,/맥박: 130/);assert.equal(next.calls.some(c=>c.method==='PATCH'),false);
});

test('a real-DOM style bad numeric input cannot turn into an unset request',async()=>{
  const h=await criticalHarness({pulse:92});criticalButton(h,'pulse','edit').onclick();const input=criticalInput(h,'pulse');
  input.value='';input.validity={badInput:true};await h.el('critical-save').onclick();await flush();
  assert.equal(h.calls.some(c=>c.method==='PATCH'),false);assert.equal(h.getSession().fields.pulse,92);
  input.validity={badInput:false};input.value='1e';await h.el('critical-save').onclick();await flush();
  assert.equal(h.calls.some(c=>c.method==='PATCH'),false);
});

test('409 deleting the last important field keeps the stale editor visible and cancellable',async()=>{
  const h=await criticalHarness({pulse:92});criticalButton(h,'pulse','edit').onclick();criticalInput(h,'pulse').value='130';criticalInput(h,'pulse').oninput();
  h.setSession(draft(3,{}));await h.el('critical-save').onclick();await flush();
  assert.equal(h.el('critical-panel').hidden,false);assert.equal(h.el('critical-editor').hidden,false);
  h.el('critical-cancel').onclick();assert.equal(h.el('critical-panel').hidden,true);assert.equal(h.el('quick-record').disabled,false);
});

test('recommendation handoff waits for critical review and remains separate from clinical confirmation',async()=>{
  const h=await criticalHarness({pulse:92});await h.el('quick-record').onclick();h.capture.final('병원 추천해줘');await flush();
  assert.equal(h.el('recommendation-prep').hidden,false);assert.equal(h.el('recommendation-continue').disabled,true);
  h.el('recommendation-continue').onclick();assert.equal(h.navigations.length,0);
  await h.el('critical-all').onclick();await flush();assert.equal(h.el('recommendation-continue').disabled,false);
  h.el('recommendation-continue').onclick();assert.equal(h.navigations.length,1);
  assert.equal(h.calls.some(c=>c.url.endsWith('/confirm')||c.url.endsWith('/submit')),false);
});

test('a correction committed before response loss restores its exact PATCH and never auto-confirms the corrected value',async()=>{
  const recoveryStore=new Map();let h;
  h=await criticalHarness({pulse:92},{recoveryStore,before:call=>{
    if(call.method==='PATCH'){h.setSession(draft(3,{pulse:130}));throw Error('lost correction response');}
  }});
  criticalButton(h,'pulse','edit').onclick();criticalInput(h,'pulse').value='130';criticalInput(h,'pulse').oninput();
  await h.el('critical-save').onclick();await flush();const original=h.calls.find(c=>c.method==='PATCH').body;
  const next=harness(call=>response({session:h.getSession(),...(call.method==='PATCH'?{duplicate:true}:{})}),{recoveryStore,criticalAutoread:true});await flush();
  assert.equal(next.calls.some(c=>c.method==='PATCH'),false);assert.equal(next.el('quick-retry').hidden,false);
  await next.el('quick-retry').onclick();await flush();assert.deepEqual(next.calls.find(c=>c.method==='PATCH').body,original);
  assert.match(next.el('critical-summary').textContent,/1개 대기/);assert.doesNotMatch(textOf(criticalCard(next,'pulse')),/입력 내용 확인됨/);
  assert.equal(readDraftRecovery(next.checkpointStorage,'http://127.0.0.1:8000').payload.pending,null);
});

test('a correction checkpoint failure sends no PATCH and preserves its review input',async()=>{
  const h=await criticalHarness({pulse:92});criticalButton(h,'pulse','edit').onclick();criticalInput(h,'pulse').value='130';criticalInput(h,'pulse').oninput();
  h.localStorage.setItem=()=>{throw Error('quota exceeded');};await h.el('critical-save').onclick();await flush();
  assert.equal(h.calls.some(c=>c.method==='PATCH'),false);assert.equal(criticalInput(h,'pulse').value,'130');
  assert.equal(h.el('quick-retry').hidden,false);assert.match(h.el('recovery-status').textContent,/저장 실패/);
});

test('late words during spoken verification prevent a blanket yes confirmation',async()=>{
  const h=await criticalHarness({pulse:92});const before=h.calls.length;
  h.capture.stop=async()=>{h.capture.final('아니요');h.capture.active=false;h.capture.callbacks.onState('idle');};
  await criticalButton(h,'pulse','voice').onclick();h.capture.final('맞아요');await criticalButton(h,'pulse','voice').onclick();await flush();
  assert.equal(h.calls.length,before);assert.match(h.el('critical-summary').textContent,/1개 대기/);
  assert.match(h.el('recording-excluded-text').textContent,/맞아요 아니요/);
});

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

test('reload restores the same saved draft by GET without recording, writing or replaying commands',async()=>{
  const recoveryStore=new Map(),saved=draft(2,{age:47},{utterances:[{transcript:'47세 남성.'}]});
  const server=call=>response({session:call.url.endsWith('/transcripts')||call.method==='GET'?saved:draft()});
  const first=harness(server,{recoveryStore});await first.el('quick-record').onclick();first.capture.final('47세 남성. 병원 추천해줘');await flush();
  const actorId=readDraftRecovery(first.checkpointStorage,'http://127.0.0.1:8000').payload.actorId;
  const next=harness(server,{recoveryStore});await flush();
  assert.equal(next.capture.starts,0);assert.equal(next.calls.length,1);assert.equal(next.calls[0].method,'GET');
  assert.ok(next.calls[0].url.includes(encodeURIComponent(actorId)));assert.match(next.el('quick-state').textContent,/복원 완료/);
  assert.equal(next.el('recommendation-prep').hidden,true);assert.match(next.el('quick-storage-details').textContent,/버전: 2/);
  await next.el('quick-record').onclick();next.capture.final('맥박 92.');await next.el('quick-record').onclick();await flush();
  assert.equal(next.calls.at(-1).body.actor_id,actorId);assert.equal(next.calls.at(-1).body.expected_version,2);
});

test('confirmed but unsent speech survives pagehide and reload as manual review text, never an automatic write',async()=>{
  const recoveryStore=new Map(),first=harness(undefined,{recoveryStore});
  await first.el('quick-record').onclick();first.capture.final('혈압 80에 50.');first.events.pagehide();
  const next=harness(undefined,{recoveryStore});await flush();
  assert.equal(first.calls.length,0);assert.equal(next.calls.length,0);assert.equal(next.capture.starts,0);
  assert.equal(next.el('recording-excluded-text').textContent,'혈압 80에 50.');assert.equal(next.el('recording-excluded').hidden,false);
});

test('manual text is preserved exactly across reload including whitespace and over-API-limit review text',async()=>{
  const recoveryStore=new Map(),first=harness(undefined,{recoveryStore}),text='  검토 전\n'+ '가'.repeat(4100)+'  ';
  first.el('recording-review').value=text;first.el('recording-review').oninput();
  const next=harness(undefined,{recoveryStore});await flush();
  assert.equal(next.el('recording-review').value,text);assert.equal(next.calls.length,0);
  assert.equal(next.el('recording-review-apply').disabled,true);
});

test('reload of a committed transcript with a lost response retries exactly once with the original body',async()=>{
  const recoveryStore=new Map(),saved=draft(2,{age:47},{utterances:[{transcript:'47세 남성.'}]});
  const first=harness(call=>{if(call.url.endsWith('/transcripts'))throw Error('lost response');return response({session:draft()});},{recoveryStore});
  await first.el('quick-record').onclick();first.capture.final('47세 남성. 병원 추천해줘');await flush();
  const original=first.calls.at(-1).body;
  const next=harness(call=>response({session:saved,...(call.method==='POST'?{duplicate:true}:{})}),{recoveryStore});await flush();
  assert.equal(next.calls.filter(c=>c.method==='POST').length,0);assert.equal(next.el('quick-retry').hidden,false);
  assert.equal(next.el('recommendation-prep').hidden,true);
  await next.el('quick-retry').onclick();await flush();
  assert.deepEqual(next.calls.find(c=>c.method==='POST').body,original);
  assert.equal(next.el('recommendation-prep').hidden,false);
  assert.equal(readDraftRecovery(next.checkpointStorage,'http://127.0.0.1:8000').payload.pending,null);
});

test('a lost create response restores without a GET or empty replacement and continues its original utterance only on retry',async()=>{
  const recoveryStore=new Map(),first=harness(()=>{throw Error('lost create response');},{recoveryStore});
  await first.el('quick-record').onclick();first.capture.final('47세 남성.');await first.el('quick-record').onclick();await flush();
  const stored=readDraftRecovery(first.checkpointStorage,'http://127.0.0.1:8000').payload;
  assert.equal(stored.sessionId,null);assert.equal(stored.pending.job.phase,'create');
  const next=harness(undefined,{recoveryStore});await flush();assert.equal(next.calls.length,0);
  await next.el('quick-retry').onclick();await flush();
  assert.deepEqual(next.calls[0].body,first.calls[0].body);assert.equal(next.calls[1].body.text,'47세 남성.');
  assert.equal(next.calls[1].body.utterance_id,stored.pending.job.utteranceId);
});

test('restore GET failure preserves the checkpoint and blocks input without making a replacement draft',async()=>{
  const recoveryStore=new Map(),first=harness(undefined,{recoveryStore});await first.hold('quick-record',{say:['47세 남성.']});
  const raw=recoveryStore.get(PATIENT_RECORDS_KEY),next=harness(()=>response({error:'missing'},404),{recoveryStore});await flush();
  assert.equal(next.calls.length,1);assert.equal(next.calls[0].method,'GET');assert.equal(next.el('quick-record').disabled,true);
  assert.equal(recoveryStore.get(PATIENT_RECORDS_KEY),raw);assert.equal(next.el('recovery-retry').hidden,false);
  next.el('patient-remove').onclick();assert.equal(JSON.parse(recoveryStore.get(PATIENT_RECORDS_KEY)).records.length,0);assert.equal(next.calls.length,1);
});

test('storage quota failure blocks HTTP before sending and retains the original request for retry',async()=>{
  const h=harness(undefined,{recoveryWriteError:true});await h.el('quick-record').onclick();h.capture.final('47세 남성.');
  await h.el('quick-record').onclick();await flush();assert.equal(h.calls.length,0);assert.equal(h.el('quick-retry').hidden,false);
  assert.match(h.el('recovery-status').textContent,/저장 실패/);
});

test('stale tabs cannot overwrite or clear a checkpoint updated by a newer tab',async()=>{
  const recoveryStore=new Map(),old=harness(undefined,{recoveryStore});old.el('recording-review').value='첫 문장';old.el('recording-review').oninput();
  const next=harness(undefined,{recoveryStore});await flush();next.el('recording-review').value='새 탭 문장';next.el('recording-review').oninput();
  const latest=recoveryStore.get(PATIENT_RECORDS_KEY);
  old.el('recording-review').value='옛 탭 수정';old.el('recording-review').oninput();
  assert.equal(recoveryStore.get(PATIENT_RECORDS_KEY),latest);old.el('quick-new').onclick();assert.equal(recoveryStore.get(PATIENT_RECORDS_KEY),latest);
  assert.equal(old.calls.length,0);assert.equal(old.el('quick-record').disabled,true);
});

test('storage events stop an active stale recorder without saving or clearing the newer checkpoint',async()=>{
  const h=harness();await h.el('quick-record').onclick();h.capture.final('47세 남성.');
  h.recoveryStore.set(PATIENT_RECORDS_KEY,'new owner checkpoint');h.events.storage({key:PATIENT_RECORDS_KEY,newValue:'new owner checkpoint'});await flush();
  assert.equal(h.capture.active,false);assert.equal(h.calls.length,0);assert.equal(h.recoveryStore.get(PATIENT_RECORDS_KEY),'new owner checkpoint');
});

test('expired or malformed recovery never silently starts an empty draft',async()=>{
  for(const raw of ['{broken',JSON.stringify({serverOrigin:'https://other.test'})]){
    const recoveryStore=new Map([[RECOVERY_KEY,raw]]),h=harness(undefined,{recoveryStore});await flush();
    assert.equal(h.calls.length,0);assert.equal(h.el('quick-record').disabled,true);assert.equal(recoveryStore.get(RECOVERY_KEY),raw);
  }
});

test('reload preserves a pending followup answer and retries against its original question and version',async()=>{
  const recoveryStore=new Map(),q=question('consciousness','의식 상태는?',['consciousness']);
  const saved=draft(2,{age:47},{followup:plan(q)});
  const first=harness(call=>{if(call.url.endsWith('/followup/answers'))throw Error('lost answer');return response({session:saved});},{recoveryStore});
  await first.hold('quick-record',{say:['47세 남성.']});first.el('recording-toggle').onclick();
  await first.el('followup-record').onclick();first.capture.final('명료');await first.el('followup-record').onclick();await flush();
  const original=first.calls.at(-1).body;
  const next=harness(call=>response({session:saved,...(call.method==='POST'?{answer:{accepted:true,applied_fields:['consciousness']}}:{})}),{recoveryStore});await flush();
  assert.equal(next.calls.filter(c=>c.method==='POST').length,0);
  await next.el('quick-retry').onclick();await flush();assert.deepEqual(next.calls.find(c=>c.method==='POST').body,original);
  assert.equal(readDraftRecovery(next.checkpointStorage,'http://127.0.0.1:8000').payload.pending,null);
});

test('a concurrent checkpoint change during restore GET keeps the new checkpoint and blocks editing',async()=>{
  const recoveryStore=new Map(),first=harness(undefined,{recoveryStore});await first.hold('quick-record',{say:['47세 남성.']});
  let release;const gate=new Promise(resolve=>{release=resolve;});const next=harness(()=>gate,{recoveryStore});
  recoveryStore.set(PATIENT_RECORDS_KEY,'other tab update');release(response({session:draft(2,{age:47})}));await flush();
  assert.equal(next.el('quick-record').disabled,true);assert.equal(recoveryStore.get(PATIENT_RECORDS_KEY),'other tab update');
  assert.equal(next.calls.filter(c=>c.method==='POST').length,0);
});

test('patient-linked or submitted server records cannot replace a quick voice draft',async()=>{
  for(const extra of [{patient_id:'P-OTHER'},{state:'SUBMITTED'},{id:'VS-OTHER'},{actor_id:'OTHER'}]){
    const recoveryStore=new Map(),first=harness(undefined,{recoveryStore});await first.hold('quick-record',{say:['47세 남성.']});
    const raw=recoveryStore.get(PATIENT_RECORDS_KEY),next=harness(()=>response({session:draft(2,{age:47},extra)}),{recoveryStore});await flush();
    assert.equal(next.el('quick-record').disabled,true);assert.equal(recoveryStore.get(PATIENT_RECORDS_KEY),raw);
    assert.equal(next.calls.filter(c=>c.method==='POST').length,0);
  }
});

test('explicit stop and onend callback cannot double-send the same utterance',async()=>{
  const {el,capture,calls}=harness();
  await el('quick-record').onclick();capture.final('47세 남성.');
  await el('quick-record').onclick();await flush();
  capture.end();await flush();
  assert.equal(calls.filter(c=>c.url.endsWith('/transcripts')).length,1);
  assert.equal(el('quick-history-count').textContent,'1회');
});

test('toggle is the default: pointer release/click does not stop, second pointer gesture stops once',async()=>{
  const {el,capture,calls}=harness();
  assert.equal(el('recording-toggle').attributes['aria-pressed'],'true');
  const down={button:0,isPrimary:true,pointerId:7},up={pointerId:7};
  await el('quick-record').onpointerdown(down);capture.final('혈압12080 맥박92');
  await el('quick-record').onpointerup(up);await el('quick-record').onclick();
  assert.equal(capture.active,true);assert.equal(calls.length,0);
  await el('quick-record').onpointerdown(down);await el('quick-record').onpointerup(up);await el('quick-record').onclick();await flush();
  assert.equal(capture.active,false);assert.equal(capture.starts,1);
  assert.equal(calls.filter(c=>c.url.endsWith('/transcripts')).length,1);
  assert.match(el('recording-ended').textContent,/버튼으로 종료/);
});

test('toggle Space/Enter ignore repeats and synthetic clicks; each gesture acts only once',async()=>{
  for(const key of [' ','Enter']){
    const {el,capture,calls}=harness();const event=repeat=>({key,repeat,preventDefault(){}});
    await el('quick-record').onkeydown(event(false));await el('quick-record').onkeydown(event(true));
    capture.final('맥박 92');await el('quick-record').onkeyup(event(false));await el('quick-record').onclick();
    assert.equal(capture.active,true);assert.equal(capture.starts,1);
    await el('quick-record').onkeydown(event(false));await el('quick-record').onkeyup(event(false));await el('quick-record').onclick();await flush();
    assert.equal(capture.active,false);assert.equal(capture.starts,1);assert.equal(calls.filter(c=>c.url.endsWith('/transcripts')).length,1);
  }
});

test('toggle allows 180 seconds while hold retains the 30 second limit',async()=>{
  const {el,capture,calls,clock,tick}=harness();
  await el('quick-record').onclick();capture.final('47세 남성');
  clock.now+=30_000;tick();await flush();assert.equal(capture.active,true);
  clock.now+=149_000;tick();await flush();assert.equal(capture.active,true);
  clock.now+=1_000;tick();await flush();assert.equal(capture.active,false);
  assert.equal(calls.filter(c=>c.url.endsWith('/transcripts')).length,1);
  assert.match(el('quick-interim').textContent,/180초/);assert.match(el('recording-ended').textContent,/180초 시간 한도/);
});

test('lost keyboard/pointer focus releases gesture state; toggle continues and hold cancels',async()=>{
  for(const holdMode of [false,true]){
    const {el,capture,calls,events}=harness();if(holdMode)el('recording-hold').onclick();
    const key={key:'Enter',repeat:false,preventDefault(){}};
    await el('quick-record').onkeydown(key);capture.final('맥박 92');el('quick-record').onblur();await flush();
    assert.equal(capture.active,!holdMode);assert.equal(calls.length,0);
    if(!holdMode){await el('quick-record').onkeydown(key);await el('quick-record').onkeyup(key);await flush();assert.equal(capture.active,false);assert.equal(calls.length,2);}
    else{
      await el('quick-record').onpointerdown({button:0,isPrimary:true,pointerId:9});capture.final('혈압 120에 80.');
      el('quick-record').onlostpointercapture();await flush();assert.equal(capture.active,false);assert.equal(calls.length,0);
      await el('quick-record').onpointerdown({button:0,isPrimary:true,pointerId:9});events.blur();await flush();assert.equal(capture.active,false);
    }
  }
});

test('only confirmed control words stop; raw final source keeps the command and body excludes it',async()=>{
  const {el,capture,calls}=harness();
  await el('quick-record').onclick();capture.callbacks.onInterim('혈압 12080 그만');await flush();
  assert.equal(capture.active,true);assert.equal(calls.length,0);
  capture.final('혈압12080 맥박92 그만');await flush();
  assert.equal(capture.active,false);assert.equal(calls[1].body.text,'혈압12080 맥박92');
  assert.equal(lastUser(el).children[0].textContent,'혈압12080 맥박92 그만');
  assert.match(textOf(lastUser(el).children[2]),/확정 인식 원문: 혈압12080 맥박92 그만/);
  assert.match(textOf(lastUser(el).children[2]),/API 전송 본문: 혈압12080 맥박92/);
  assert.match(el('recording-ended').textContent,/음성 명령 “그만”/);
  capture.final('늦은 결과');capture.end();await flush();assert.equal(calls.length,2);
});

test('command-only finals, including a command split across finals, create no API draft or write',async()=>{
  for(const segments of [['그만'],['종료'],['녹음 종료'],['녹음','종료'],['음성','입력 종료']]){
    const {el,capture,calls}=harness();await el('quick-record').onclick();
    for(const text of segments)capture.final(text);await flush();
    assert.equal(capture.active,false);assert.equal(calls.length,0,segments.join('|'));
    assert.match(textOf(lastUser(el)),/종료 명령만 있어 API에 보내지 않았습니다/);
  }
});

test('mid-sentence stop saves only the prefix in both toggle and hold, keeping the suffix for review',async()=>{
  for(const command of ['중단','종료','녹음 종료'])for(const holdMode of [false,true]){
    const {el,capture,calls}=harness();if(holdMode)el('recording-hold').onclick();
    if(holdMode)await el('quick-record').onpointerdown({button:0,isPrimary:true,pointerId:1});else await el('quick-record').onclick();
    capture.final(`47세 남성. ${command} 맥박 92입니다.`);await flush();
    assert.equal(capture.active,false);assert.equal(calls[1].body.text,'47세 남성.');
    assert.equal(el('recording-excluded-text').textContent,'맥박 92입니다.');
    assert.equal(el('recording-excluded').hidden,false);assert.equal(el('quick-state').textContent,'저장 후 중단');
    assert.equal(calls.filter(c=>c.url.endsWith('/transcripts')).length,1);
  }
});

test('recommendation waits for the successful write and requires a separate continue action',async()=>{
  let release;const delayed=new Promise(resolve=>{release=resolve;});
  const h=harness(call=>call.url.endsWith('/transcripts')?delayed:response({session:draft()}));
  await h.el('quick-record').onclick();h.capture.final('47세 남성. 병원 추천해줘 혈압 120에 80.');await flush();
  assert.equal(h.calls[1].body.text,'47세 남성.');
  assert.equal(h.el('recommendation-prep').hidden,true);assert.equal(h.navigations.length,0);
  h.el('recommendation-continue').onclick();assert.equal(h.stored.size,0);
  release(response({session:draft(2,{age:47}, {utterances:[{transcript:'47세 남성.'}]})}));await flush();
  assert.equal(h.el('recommendation-prep').hidden,false);assert.equal(h.navigations.length,0);
  h.el('recommendation-continue').onclick();
  assert.deepEqual(h.navigations,['./?voice_handoff=1']);
  const result=readDraftHandoff(h.sessionStorage,'http://127.0.0.1:8000');
  assert.equal(result.status,'valid');assert.equal(result.payload.sessionId,'VS-PAGE');assert.equal(result.payload.version,2);
  assert.equal(result.payload.unappliedText,'혈압 120에 80.');assert.equal(result.payload.intent,'recommend');
  assert.ok(result.payload.actorId.startsWith('QUICK-VOICE-'));
  assert.equal(h.calls.length,2,'continuation creates no patient, confirmation, inquiry or candidate request');
});

test('recommendation split across final segments preserves the original input mode',async()=>{
  const {el,capture,calls}=harness();el('mode-dictation').onclick();await el('quick-record').onclick();
  capture.final('47세 남성. 병원');capture.final('추천해줘 맥박 90.');await flush();
  assert.equal(calls[1].body.text,'47세 남성.');assert.equal(calls[1].body.mode,'dictation');
  assert.equal(el('recommendation-prep').hidden,false);assert.equal(el('recording-excluded-text').textContent,'맥박 90.');
});

test('first valid command runs once and a later command remains unapplied text',async()=>{
  for(const [input,ready,tail]of [
    ['47세 남성. 중단 병원 추천해줘',false,'병원 추천해줘'],
    ['47세 남성. 병원 추천해줘 종료',true,'종료']]){
    const {el,capture,calls}=harness();await el('quick-record').onclick();capture.final(input);await flush();
    assert.equal(el('recommendation-prep').hidden,!ready);assert.equal(el('recording-excluded-text').textContent,tail);
    capture.end();await flush();assert.equal(calls.length,2);
  }
});

test('recommend-only speech makes no empty draft, and uses the existing draft on a later request',async()=>{
  const h=harness();await h.el('quick-record').onclick();h.capture.final('병원 추천해줘');await flush();
  assert.equal(h.calls.length,0);assert.equal(h.el('recommendation-prep').hidden,true);assert.equal(h.el('quick-state').textContent,'환자 정보 필요');
  await h.el('quick-record').onclick();h.capture.final('47세 남성.');await h.el('quick-record').onclick();await flush();
  const before=h.calls.length;
  await h.el('quick-record').onclick();h.capture.final('병원 추천해줘');await flush();
  assert.equal(h.calls.length,before);assert.equal(h.el('recommendation-prep').hidden,false);
});

test('uncertain recommendation write keeps action pending until the same request is retried',async()=>{
  let count=0;const h=harness(call=>{
    if(!call.url.endsWith('/transcripts'))return response({session:draft()});
    if(++count===1)throw new Error('response lost after commit');
    return response({session:draft(2,{age:47})});
  });
  await h.el('quick-record').onclick();h.capture.final('47세 남성. 병원 추천해줘');await flush();
  assert.equal(h.el('recommendation-prep').hidden,true);assert.equal(h.el('quick-retry').hidden,false);
  await h.el('quick-retry').onclick();await flush();
  assert.equal(h.el('recommendation-prep').hidden,false);
  assert.deepEqual(h.calls[1].body,h.calls[2].body,'same utterance ID and expected version');
  const messages=h.el('quick-history').children.filter(c=>textOf(c).includes('앞 내용을 저장했습니다.'));
  assert.equal(messages.length,1);assert.equal(h.navigations.length,0);
});

test('409 during a trigger does not execute the recommendation after reading the latest draft',async()=>{
  const h=harness(call=>call.url.endsWith('/transcripts')?response({error:'changed'},409):response({session:draft()}));
  await h.el('quick-record').onclick();h.capture.final('47세 남성. 병원 추천해줘');await flush();
  assert.equal(h.el('recommendation-prep').hidden,true);assert.equal(h.stored.size,0);assert.equal(h.navigations.length,0);
  assert.ok(h.calls.some(c=>c.method==='GET'));assert.ok(!h.calls.some(c=>c.url.endsWith('/submit')));
});

test('a later definitive failure invalidates an older ready action and keeps its unsaved prefix',async()=>{
  for(const status of [400,409]){
    let transcripts=0;
    const h=harness(call=>call.url.endsWith('/transcripts')&&++transcripts===2?response({error:'rejected'},status):response({session:draft(2,{age:47})}));
    await h.el('quick-record').onclick();h.capture.final('47세 남성 병원 추천해줘');await flush();
    assert.equal(h.el('recommendation-prep').hidden,false);
    await h.el('quick-record').onclick();h.capture.final('맥박 140 병원 추천해줘 혈압 80에 50');await flush();
    assert.equal(h.el('recommendation-prep').hidden,true);assert.equal(h.el('recommendation-continue').disabled,true);
    assert.equal(h.el('recording-excluded-text').textContent,'맥박 140 혈압 80에 50');
    h.el('recommendation-continue').onclick();assert.equal(h.navigations.length,0);
    await h.el('quick-record').onclick();h.capture.final('병원 추천해줘');await flush();
    h.el('recommendation-continue').onclick();
    assert.equal(readDraftHandoff(h.sessionStorage,'http://127.0.0.1:8000').payload.unappliedText,'맥박 140 혈압 80에 50');
  }
});

test('an uncertain write followed by a definitive retry failure keeps the unsaved prefix and tail',async()=>{
  for(const status of [400,409]){
    let transcripts=0;
    const h=harness(call=>{
      if(call.url.endsWith('/transcripts')){if(++transcripts===1)throw Error('connection lost');return response({error:'not saved'},status);}
      return response({session:draft()});
    });
    await h.el('quick-record').onclick();h.capture.final('맥박 140 병원 추천해줘 혈압 80에 50');await flush();
    assert.equal(h.el('recording-excluded-text').textContent,'혈압 80에 50','uncertain prefix remains owned by exact retry');
    await h.el('quick-retry').onclick();await flush();
    assert.equal(h.el('recording-excluded-text').textContent,'맥박 140 혈압 80에 50');
    assert.equal(h.el('recommendation-prep').hidden,true);
    if(status===409)assert.ok(h.calls.some(c=>c.method==='GET'));
    await h.el('quick-record').onclick();h.capture.final('병원 추천해줘');await flush();h.el('recommendation-continue').onclick();
    assert.equal(readDraftHandoff(h.sessionStorage,'http://127.0.0.1:8000').payload.unappliedText,'맥박 140 혈압 80에 50');
  }
});

test('unaccepted followup answer still stores its raw prefix before recommendation preparation',async()=>{
  const q=question('consciousness','의식 상태는?',['consciousness']);
  const h=harness(call=>call.url.endsWith('/followup/answers')?
    response({session:draft(3,{age:47},{followup:plan(q),utterances:[{transcript:'잘 모르겠음'}]}),answer:{accepted:false}}):
    response({session:draft(2,{age:47},{followup:plan(q)})}));
  await h.hold('quick-record',{say:['47세 남성.']});
  h.el('recording-toggle').onclick();await h.el('followup-record').onclick();h.capture.final('잘 모르겠음 병원 추천해줘');await flush();
  const call=h.calls.at(-1);assert.ok(call.url.endsWith('/followup/answers'));assert.equal(call.body.text,'잘 모르겠음');
  assert.equal(call.body.question_id,'consciousness');assert.equal(h.el('recommendation-prep').hidden,false);
});

test('suffix and delayed finals persist into handoff, including across subsequent recordings',async()=>{
  const h=harness();h.capture.stop=async()=>{
    h.capture.final('호흡 정상.');h.capture.active=false;h.capture.callbacks.onState('idle');
  };
  await h.el('quick-record').onclick();h.capture.final('47세 남성 병원 추천해줘 맥박 92.');await flush();
  assert.equal(h.el('recording-excluded-text').textContent,'맥박 92. 호흡 정상.');
  h.capture.stop=async()=>{h.capture.active=false;h.capture.callbacks.onState('idle');};
  await h.el('quick-record').onclick();h.capture.final('체온 37도.');await h.el('quick-record').onclick();await flush();
  assert.equal(h.el('recording-excluded-text').textContent,'맥박 92. 호흡 정상.');
  h.el('recommendation-continue').onclick();
  assert.equal(readDraftHandoff(h.sessionStorage,'http://127.0.0.1:8000').payload.unappliedText,'맥박 92. 호흡 정상.');
});

test('late final negation or reported speech invalidates a provisional command before any write or action',async()=>{
  for(const [first,late]of [
    ['혈압 120에 80 종료','하지 마'],
    ['혈압 120에 80 병원 추천해줘','라고 환자가 말함'],
    ['혈압 120에 80 종료','라고 합니다. 병원 추천해줘']]){
    const h=harness();h.capture.stop=async()=>{h.capture.final(late);h.capture.active=false;h.capture.callbacks.onState('idle');};
    await h.el('quick-record').onclick();h.capture.final(first);await flush();
    assert.equal(h.calls.length,0);assert.equal(h.el('recommendation-prep').hidden,true);assert.equal(h.navigations.length,0);
    assert.equal(h.el('recording-excluded-text').textContent,first+' '+late);
    assert.equal(h.el('quick-state').textContent,'명령 실행 안 함');assert.match(h.el('recording-ended').textContent,/실행 안 함/);
  }
});

test('a polite stop completion arriving during drain remains a valid command without leaking its ending into recovery',async()=>{
  const h=harness();h.capture.stop=async()=>{h.capture.final('해 주세요');h.capture.active=false;h.capture.callbacks.onState('idle');};
  await h.el('quick-record').onclick();h.capture.final('혈압 120에 80 종료');await flush();
  assert.equal(h.calls[1].body.text,'혈압 120에 80');assert.equal(h.el('quick-state').textContent,'저장 후 중단');
  assert.equal(h.el('recording-excluded').hidden,true);assert.match(h.el('recording-ended').textContent,/종료 해 주세요/);
});

test('manual unapplied edits are transferred for review without auto-writing or losing the original suffix',async()=>{
  const h=harness();await h.el('quick-record').onclick();h.capture.final('47세 남성 병원 추천해줘 맥박 92.');await flush();
  h.el('recording-excluded-copy').onclick();h.el('recording-review').value='맥박 95로 확인.';
  h.el('recommendation-continue').onclick();
  const payload=readDraftHandoff(h.sessionStorage,'http://127.0.0.1:8000').payload;
  assert.equal(payload.unappliedText,'맥박 92.\n\n맥박 95로 확인.');assert.equal(h.calls.length,2);
});

test('reviewing a copied suffix removes it from the unapplied list only after successful manual save',async()=>{
  const h=harness();await h.el('quick-record').onclick();h.capture.final('47세 남성 병원 추천해줘 맥박 92.');await flush();
  h.el('recording-excluded-copy').onclick();await h.el('recording-review-apply').onclick();await flush();
  assert.equal(h.el('recording-excluded').hidden,true);
  h.el('recommendation-continue').onclick();assert.equal(readDraftHandoff(h.sessionStorage,'http://127.0.0.1:8000').payload.unappliedText,'');
});

test('storage failure or too much unapplied text leaves the screen and draft intact',async()=>{
  for(const storageError of [true,false]){
    const h=harness(undefined,{storageError});await h.el('quick-record').onclick();
    h.capture.final('47세 남성 병원 추천해줘');await flush();
    h.el('recording-review').value=storageError?'미검토 메모':'가'.repeat(4001);
    h.el('recommendation-continue').onclick();
    assert.equal(h.navigations.length,0);assert.equal(h.el('recommendation-prep').hidden,false);
    assert.equal(h.el('quick-error').hidden,false);assert.equal(h.calls.length,2);
  }
});

test('interim recommendation, clinical stops and quoted recommendations never run actions',async()=>{
  const h=harness();await h.el('quick-record').onclick();h.capture.callbacks.onInterim('병원 추천해줘');
  for(const text of ['CPR 중단 시각 14시.','산소 공급 종료.','보호자가 병원 추천해줘라고 말했습니다.'])h.capture.final(text);
  await flush();assert.equal(h.capture.active,true);assert.equal(h.calls.length,0);assert.equal(h.el('recommendation-prep').hidden,true);
  await h.el('quick-record').onclick();await flush();assert.equal(h.calls.length,2);
});

test('new draft explicitly resets recommendation preparation and displayed unapplied text',async()=>{
  const h=harness();await h.el('quick-record').onclick();h.capture.final('47세 남성 병원 추천해줘 맥박 92.');await flush();
  h.el('quick-new').onclick();assert.equal(h.el('recommendation-prep').hidden,true);assert.equal(h.el('recording-excluded').hidden,true);
  h.el('recommendation-continue').onclick();assert.equal(h.navigations.length,0);
});

test('a control command split across final segments preserves all earlier clinical text exactly once',async()=>{
  const {el,capture,calls}=harness();await el('quick-record').onclick();
  capture.final('혈압 120에 80.');capture.final('맥박 92. 녹음');capture.final('종료');await flush();
  assert.equal(calls[1].body.text,'혈압 120에 80. 맥박 92.');assert.equal(calls.length,2);
  assert.equal(lastUser(el).children[0].textContent,'혈압 120에 80. 맥박 92. 녹음 종료');
});

test('negative, quoted, partial words and split clinical endings remain clinical text',async()=>{
  for(const segments of [['처치','종료'],['환자가','그만'],['종료하지 마'],['환자가 그만이라고 했습니다'],['그만큼 아픕니다'],['CPR 종료'],['종료시각 14시']]){
    const {el,capture,calls}=harness();await el('quick-record').onclick();
    for(const text of segments)capture.final(text);await flush();assert.equal(capture.active,true,segments.join('|'));
    await el('quick-record').onclick();await flush();assert.equal(calls[1].body.text,segments.join(' '));
  }
});

test('manual button stop drains delayed finals; voice stop freezes body and exposes delayed finals for review',async()=>{
  for(const voiceStop of [false,true]){
    const {el,capture,calls,advance}=harness();let endStop;
    capture.stop=async()=>{await new Promise(resolve=>{endStop=resolve;});capture.active=false;capture.callbacks.onState('idle');};
    await el('quick-record').onclick();capture.final('혈압 120에 80.');
    let stopped;if(voiceStop){capture.final('녹음 종료');assert.equal(endStop,undefined,'Candidate waits for quiet confirmation');advance(800);}else stopped=el('quick-record').onclick();
    capture.final('맥박 92.');assert.equal(calls.length,0);
    endStop();await stopped;await flush();
    assert.equal(calls[1].body.text,voiceStop?'혈압 120에 80.':'혈압 120에 80. 맥박 92.');
    if(voiceStop){
      assert.equal(el('recording-excluded').hidden,false);assert.equal(el('recording-excluded-text').textContent,'맥박 92.');
      assert.match(textOf(lastUser(el).children[2]),/종료 명령 뒤 확정되어 본문에 넣지 않은 내용: 맥박 92/);
      el('recording-excluded-copy').onclick();assert.equal(el('recording-review').value,'맥박 92.');assert.equal(calls.length,2);
      await el('recording-review-apply').onclick();await flush();assert.equal(calls.length,3);
      assert.equal(calls[2].body.text,'맥박 92.');assert.equal(calls[2].body.mode,'dictation');
      assert.match(metaOf(lastUser(el)),/직접 입력/);assert.equal(el('recording-review-apply').disabled,true);
      await el('recording-review-apply').onclick();assert.equal(calls.length,3);
    }
  }
});

test('4000 characters stop and send once, excess characters remain visible without any API write',async()=>{
  for(const length of [4000,4001]){
    const {el,capture,calls}=harness();await el('quick-record').onclick();capture.final('가'.repeat(length));await flush();
    assert.equal(capture.active,false);
    if(length===4000){assert.equal(calls.length,2);assert.equal(calls[1].body.text.length,4000);}
    else{assert.equal(calls.length,0);assert.match(el('quick-error').textContent,/4000자/);assert.equal(lastUser(el).children[0].textContent.length,4001);}
  }
});

test('synchronous/asynchronous start and stop failures always release capture without a draft write',async()=>{
  for(const failure of ['sync-start','async-start','sync-stop','async-stop']){
    const {el,capture,calls}=harness();
    if(failure==='sync-start')capture.start=()=>{capture.active=true;throw Error('start sync failed');};
    if(failure==='async-start')capture.start=async()=>{capture.active=true;throw Error('start async failed');};
    await el('quick-record').onclick();
    if(failure.endsWith('stop')){
      capture.final('혈압 120에 80.');
      capture.stop=failure==='sync-stop'?()=>{throw Error('stop sync failed');}:async()=>{throw Error('stop async failed');};
      await el('quick-record').onclick();
    }
    await flush();assert.equal(capture.active,false,failure);assert.equal(calls.length,0);assert.equal(el('quick-record').disabled,false);assert.match(el('quick-error').textContent,/failed/);
  }
});

test('recording mode and manual input cannot change while recording or while a request is busy',async()=>{
  const {el,capture}=harness();await el('quick-record').onclick();
  el('recording-hold').onclick();el('mode-dictation').onclick();
  assert.equal(el('recording-toggle').attributes['aria-pressed'],'true');assert.equal(el('mode-auto').attributes['aria-pressed'],'true');
  assert.equal(el('recording-review').disabled,true);assert.equal(el('recording-review-apply').disabled,true);
  el('recording-review').value='임의 입력';await el('recording-review-apply').onclick();
  assert.equal(capture.active,true);el('quick-cancel').onclick();await flush();assert.equal(capture.active,false);
});

test('manual text validates 1..4000 characters and applies dictionary text once without stopword handling',async()=>{
  const {el,calls}=harness();
  el('recording-review').value=' ';await el('recording-review-apply').onclick();assert.equal(calls.length,0);
  el('recording-review').value='가'.repeat(4001);await el('recording-review-apply').onclick();assert.equal(calls.length,0);
  el('recording-review').value='환자가 그만이라고 했습니다. 혈압 120에 80.';el('recording-review').oninput();
  await el('recording-review-apply').onclick();await flush();assert.equal(calls.length,2);assert.equal(calls[1].body.mode,'dictation');
  assert.equal(calls[1].body.text,el('recording-review').value);assert.match(metaOf(lastUser(el)),/직접 입력/);
  await el('recording-review-apply').onclick();assert.equal(calls.length,2);
  el('quick-new').onclick();el('recording-review').value='혈압 120에 80.';await el('recording-review-apply').onclick();await flush();assert.equal(calls.length,4,'a new draft permits a new manual utterance');
});

test('recognition failure after a final fragment preserves raw text and never edits server draft',async()=>{
  const {el,capture,calls}=harness();
  await el('quick-record').onclick();capture.final('47세 남성.');capture.fail();await flush();
  assert.equal(calls.length,0);
  assert.equal(el('quick-transcript').textContent,'47세 남성.');
  assert.match(el('quick-error').textContent,/network failure/);
  assert.equal(el('quick-record').disabled,false);
});

test('a saved transcript with no extracted fields is shown as stored, with its actual server receipt',async()=>{
  const {el,capture}=harness(call=>response({session:call.url.endsWith('/transcripts')?
    draft(2,{}, {utterances:[{transcript:'잘 들리지 않은 설명',mode:'dictation'}]}):draft(1,{}, {utterances:[]})}));
  assert.match(el('quick-storage-status').textContent,/아직 서버에 전송한 발화가 없습니다/);
  await el('quick-record').onclick();capture.final('잘 들리지 않은 설명');capture.end();await flush();
  assert.equal(el('quick-count').textContent,'원문 저장됨 · 항목 확인 필요');
  assert.match(el('quick-storage-status').textContent,/저장 발화 1건/);
  assert.match(el('quick-storage-status').textContent,/원문은 저장됐고/);
  assert.equal(el('quick-storage-receipt').hidden,false);
  assert.match(el('quick-storage-details').textContent,/서버: http:\/\/127\.0\.0\.1:8000/);
  assert.match(el('quick-storage-details').textContent,/기록 ID: VS-PAGE/);
  assert.match(el('quick-storage-details').textContent,/초안 버전: 2/);
});

test('a created draft and an uncertain transcript write do not claim that a transcript was saved',async()=>{
  const {el,capture}=harness((call,n)=>{
    if(n===1)return response({session:draft(1,{}, {utterances:[]})});
    throw Error('response lost');
  });
  await el('quick-record').onclick();capture.final('47세 남성');capture.end();await flush();
  assert.match(el('quick-storage-status').textContent,/저장 발화 0건/);
  assert.match(el('quick-storage-status').textContent,/이번 입력의 저장 결과를 확인/);
  assert.doesNotMatch(el('quick-storage-status').textContent,/원문은 저장됐고/);
  assert.equal(el('quick-retry').hidden,false);
});

test('a successful retry updates the server receipt, and a new record clears only the displayed receipt',async()=>{
  let writes=0;
  const {el,capture}=harness(call=>{
    if(call.url.endsWith('/sessions'))return response({session:draft(1,{}, {utterances:[]})});
    if(++writes===1)throw Error('response lost');
    return response({session:draft(2,{age:47}, {utterances:[{transcript:'47세 남성'}]})});
  });
  await el('quick-record').onclick();capture.final('47세 남성');capture.end();await flush();
  await el('quick-retry').onclick();await flush();
  assert.match(el('quick-storage-status').textContent,/저장 발화 1건/);
  assert.doesNotMatch(el('quick-storage-status').textContent,/저장 결과를 확인/);
  el('quick-new').onclick();
  assert.equal(el('quick-storage-receipt').hidden,true);
  assert.match(el('quick-storage-status').textContent,/아직 서버에 전송한 발화가 없습니다/);
  assert.equal(writes,2);
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
  el('recording-hold').onclick();
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
  el('recording-hold').onclick();
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

// Synthetic revision rows mirror the production history shape; no audio or DB.
function observationHistoryServer(){
  const revisions=new Map(),previous=new Map();let rowId=100;
  const copy=value=>JSON.parse(JSON.stringify(value));
  function remember(session,kind='dictation'){
    const before=previous.get(session.id);
    if(before&&session.version>before.version){
      const rows=revisions.get(session.id)||[];
      rows.push({id:rowId+=3,session_id:session.id,version:session.version,kind,recorded_at:1_700_000_000+session.version,
        before_fields:copy(before.fields),after_fields:copy(session.fields),before_metadata:copy(before.field_metadata),after_metadata:copy(session.field_metadata),warnings:[]});
      revisions.set(session.id,rows);
    }
    previous.set(session.id,copy(session));
  }
  const server=patientServer({
    before:call=>{if(call.method==='GET'&&/\/history\?/.test(call.url)){
      const sid=/\/sessions\/([^/]+)\//.exec(call.url)[1];return response({session_id:sid,revisions:copy(revisions.get(sid)||[])});
    }},
    after:(call,data)=>remember(data.session,call.method==='PATCH'?'correction':'dictation'),
  });
  function advance(sid,fields,kind='observation'){
    const old=server.sessions.get(sid),next={...old,version:old.version+1,fields:copy(fields)};server.sessions.set(sid,next);remember(next,kind);return next;
  }
  return {...server,revisions,advance};
}
const historyText=h=>textOf(h.el('observation-history-list'));

test('observation history: fetches three read-only endpoints and displays saved revisions newest first',async()=>{
  const server=observationHistoryServer(),h=harness(server.handler);await flush();
  assert.equal(h.el('observation-history-panel').hidden,true);
  await speak(h,'환자 A 정보');await speak(h,'A 추가 정보');
  const start=h.calls.length;await h.el('observation-history-load').onclick();
  assert.deepEqual(h.calls.slice(start).map(call=>[call.method,call.url.includes('/history?')]),[['GET',false],['GET',true],['GET',false]]);
  assert.equal(h.el('observation-history-panel').open,true);
  assert.equal(h.el('observation-history-list').children.length,2);
  assert.match(textOf(h.el('observation-history-list').children[0]),/버전 3/);
  assert.match(textOf(h.el('observation-history-list').children[1]),/버전 2/);
  assert.match(historyText(h),/맥박.*92/);
  assert.doesNotMatch(historyText(h),/환자 A 정보|A 추가 정보/);
  assert.match(h.el('observation-history-status').textContent,/전체 2건 중 최근 2건/);
});

test('observation history: empty initial draft does not fabricate a baseline revision',async()=>{
  const h=harness(call=>response(call.url.includes('/history?')?{session_id:'VS-PAGE',revisions:[]}:{session:draft(1,{})}));
  await flush();await speak(h,'구조화되지 않은 가상 시험 문장');await h.el('observation-history-load').onclick();
  assert.equal(h.el('observation-history-list').children.length,0);assert.match(h.el('observation-history-status').textContent,/변경 이력이 없습니다/);
});

test('observation history: a lower server version is rejected instead of replacing a known saved record',async()=>{
  const server=observationHistoryServer(),h=harness(server.handler);await flush();await speak(h,'환자 A 정보');
  server.sessions.values().next().value.version=1;await h.el('observation-history-load').onclick();
  assert.equal(h.el('observation-history-list').children.length,0);assert.match(h.el('observation-history-status').textContent,/표시하지 못/);
});

test('observation history: accepts a newer saved snapshot before loading and refreshes current summary',async()=>{
  const server=observationHistoryServer(),h=harness(server.handler);await flush();await speak(h,'환자 A 정보');
  const sid=server.sessions.keys().next().value;server.advance(sid,{age:47,pulse:110},'observation');
  await h.el('observation-history-load').onclick();
  assert.match(historyText(h),/92.*110/);assert.match(h.el('observation-history-status').textContent,/버전 3/);
  assert.ok(h.el('summary-chips').children.some(chip=>chip.textContent.includes('110')));
});

test('observation history: a new local save invalidates old cards; unapplied manual text never enters history',async()=>{
  const server=observationHistoryServer(),h=harness(server.handler);await flush();await speak(h,'환자 A 정보');await h.el('observation-history-load').onclick();
  h.el('recording-review').value='UNAPPLIED-ONLY 999';h.el('recording-review').oninput();
  assert.doesNotMatch(historyText(h),/UNAPPLIED|999/);
  await speak(h,'A 추가 정보');assert.equal(h.el('observation-history-list').children.length,0);
  assert.match(h.el('observation-history-status').textContent,/다시 조회/);
});

test('observation history: patient switch and old callbacks cannot show the prior patient',async()=>{
  const server=observationHistoryServer(),h=harness(server.handler);await flush();await speak(h,'환자 A 정보');await h.el('observation-history-load').onclick();
  const staleLoad=h.el('observation-history-load').onclick;
  await h.el('patient-new').onclick();assert.equal(h.el('observation-history-list').children.length,0);
  await speak(h,'환자 B 정보');const before=h.calls.length;await staleLoad();assert.equal(h.calls.length,before);
  await h.el('observation-history-load').onclick();
  assert.match(historyText(h),/105/);assert.doesNotMatch(historyText(h),/92/);
  assert.match(h.el('observation-history-status').textContent,/환자 02/);
});

test('observation history: a held history response blocks switching and recording until consistency check completes',async()=>{
  const server=observationHistoryServer();let release;
  const held=new Promise(resolve=>release=resolve);
  const h=harness(async call=>{const result=await server.handler(call);if(call.url.includes('/history?'))await held;return result;});
  await flush();await speak(h,'환자 A 정보');const loading=h.el('observation-history-load').onclick();await flush();
  assert.equal(h.el('patient-new').disabled,true);assert.equal(h.el('quick-record').disabled,true);
  await h.el('patient-new').onclick();assert.equal(notebook(h).records.length,1);
  release();await loading;assert.match(historyText(h),/92/);
});

test('observation history: a saved revision arriving between reads discards the entire mixed preview',async()=>{
  const server=observationHistoryServer(),h=harness(async call=>{
    const result=await server.handler(call);
    if(call.url.includes('/history?')){const sid=server.sessions.keys().next().value;server.advance(sid,{age:47,pulse:110});}
    return result;
  });await flush();await speak(h,'환자 A 정보');await h.el('observation-history-load').onclick();
  assert.equal(h.el('observation-history-list').children.length,0);
  assert.match(h.el('observation-history-status').textContent,/조회 중 저장본이 변경/);
});

test('observation history: same-version field tampering between reads is rejected',async()=>{
  const server=observationHistoryServer(),h=harness(async call=>{
    const result=await server.handler(call);if(call.url.includes('/history?'))server.sessions.values().next().value.fields.pulse=999;
    return result;
  });await flush();await speak(h,'환자 A 정보');await h.el('observation-history-load').onclick();
  assert.equal(h.el('observation-history-list').children.length,0);assert.match(h.el('observation-history-status').textContent,/조회 중 저장본이 변경/);
});

test('observation history: cross-tab notebook changes while loading prevent rendering',async()=>{
  const server=observationHistoryServer();let h;
  h=harness(async call=>{const result=await server.handler(call);if(call.url.includes('/history?'))h.recoveryStore.set(PATIENT_RECORDS_KEY,h.recoveryStore.get(PATIENT_RECORDS_KEY)+' ');return result;});
  await flush();await speak(h,'환자 A 정보');await h.el('observation-history-load').onclick();
  assert.equal(h.el('observation-history-list').children.length,0);assert.match(h.el('observation-history-status').textContent,/다른 탭/);
});

for(const fault of ['wrong-session','bad-revision','offline'])test(`observation history: ${fault} clears any previous preview`,async()=>{
  const server=observationHistoryServer();let broken=false;
  const h=harness(async call=>{
    if(broken&&call.url.includes('/history?')){
      if(fault==='offline')throw Error('synthetic offline');
      const sid=server.sessions.keys().next().value;
      return response(fault==='wrong-session'?{session_id:'VS-OTHER',revisions:[]}:{session_id:sid,revisions:[{version:999}]});
    }
    return server.handler(call);
  });await flush();await speak(h,'환자 A 정보');await h.el('observation-history-load').onclick();assert.match(historyText(h),/92/);
  broken=true;await h.el('observation-history-load').onclick();
  assert.equal(h.el('observation-history-list').children.length,0);assert.match(h.el('observation-history-status').textContent,/표시하지 못/);
});

const connectionLines=h=>textOf(h.el('connection-lines'));

test('connection guidance: healthy API and unknown browser storage are independent; manual check is GET-only',async()=>{
  const h=harness();await flush();assert.match(h.el('connection-title').textContent,/서버 응답 확인됨/);
  assert.match(connectionLines(h),/임시 저장 상태는 아직 확인되지/);
  const before=h.meta.length;await h.el('connection-check').onclick();
  assert.equal(h.meta.length,before+1);assert.equal(h.calls.length,0);assert.equal(h.recoveryStore.has(PATIENT_RECORDS_KEY),false);
  assert.match(connectionLines(h),/입력 저장 완료는.*별도|입력 저장 완료는.*확인/);
});

test('connection guidance: offline/online events never send or retry and never claim server failure from a hint alone',async()=>{
  const h=harness();await flush();const reads=h.meta.length,calls=h.calls.length;
  h.events.offline();assert.match(connectionLines(h),/브라우저는 오프라인/);assert.match(connectionLines(h),/API 응답은 아직 확인되지/);
  assert.doesNotMatch(connectionLines(h),/서버 API 응답을 받지 못했습니다/);
  h.events.online();assert.match(connectionLines(h),/브라우저는 온라인/);assert.match(h.el('connection-title').textContent,/확인 전/);
  assert.equal(h.meta.length,reads);assert.equal(h.calls.length,calls);
});

test('connection guidance: a local API can respond while the browser reports offline',async()=>{
  const h=harness(undefined,{networkOnline:false});await flush();
  assert.match(h.el('connection-title').textContent,/서버 응답 확인됨/);assert.match(connectionLines(h),/브라우저는 오프라인/);
  await h.el('connection-check').onclick();assert.match(h.el('connection-title').textContent,/서버 응답 확인됨/);
});

test('connection guidance: lost committed response stays pending through health checks and online events; exact retry stores once',async()=>{
  let lost=true;const server=patientServer({after:(call)=>{if(lost&&call.url.endsWith('/transcripts')){lost=false;throw Error('synthetic response lost after commit');}}});
  const h=harness(server.handler);await flush();await speak(h,'환자 A 정보');
  const first=h.calls.find(call=>call.url.endsWith('/transcripts')),pending=notePayload(h,notebook(h).activeId).pending;
  assert.match(h.el('connection-title').textContent,/저장 결과 확인 필요/);assert.match(connectionLines(h),/같은 요청 재시도/);
  assert.match(connectionLines(h),/브라우저 임시 복구본 저장/);
  const writes=h.calls.length;h.events.online();await h.el('connection-check').onclick();
  assert.equal(h.calls.length,writes);assert.deepEqual(notePayload(h,notebook(h).activeId).pending,pending);
  assert.match(h.el('connection-title').textContent,/저장 결과 확인 필요/);
  await h.el('quick-retry').onclick();await flush();
  const transcripts=h.calls.filter(call=>call.url.endsWith('/transcripts'));assert.equal(transcripts.length,2);assert.deepEqual(transcripts[1],first);
  assert.equal(server.sessions.values().next().value.utterances.length,1);assert.equal(h.el('quick-retry').hidden,true);
  assert.doesNotMatch(h.el('connection-title').textContent,/저장 결과 확인 필요/);
});

test('connection guidance: failed write-ahead storage is not labelled as a network outage and sends no draft request',async()=>{
  const h=harness(undefined,{recoveryWriteError:true});await flush();await speak(h,'47세 남성');
  assert.equal(h.calls.length,0);assert.match(h.el('connection-title').textContent,/브라우저 임시 저장 확인 필요/);
  assert.match(connectionLines(h),/임시 저장에 실패/);assert.doesNotMatch(connectionLines(h),/서버 API 응답을 받지 못했습니다/);
  assert.doesNotMatch(connectionLines(h),/최근 브라우저 임시 복구본 저장을 확인/);
});

test('connection guidance: recognition failure keeps API reachability separate and opens manual input without overwrite or send',async()=>{
  const h=harness();await flush();h.el('recording-review').value='작성 중인 가상 메모';h.el('recording-review').oninput();
  await h.el('quick-record').onclick();h.capture.fail();await flush();
  assert.match(h.el('connection-title').textContent,/음성 입력 확인 필요/);assert.match(connectionLines(h),/서버 API 응답을 받았습니다/);
  const before=h.calls.length;h.el('connection-manual').onclick();
  assert.equal(h.el('settings').hidden,false);assert.equal(h.el('recording-manual').open,true);
  assert.equal(h.el('recording-review').value,'작성 중인 가상 메모');assert.equal(h.calls.length,before);
  assert.match(h.el('recording-review-status').textContent,/기존 입력은 유지/);
  await h.el('quick-record').onclick();assert.doesNotMatch(h.el('connection-title').textContent,/음성 입력 확인 필요/);h.capture.fail();
});

test('connection guidance: pending manual review is read-only and never copies a pending transcript into a second write',async()=>{
  const server=patientServer({after:call=>{if(call.url.endsWith('/transcripts'))throw Error('synthetic lost response');}});
  const h=harness(server.handler);await flush();await speak(h,'환자 A 정보');const before=h.calls.length;
  h.el('connection-manual').onclick();assert.equal(h.el('recording-review').value,'');assert.equal(h.el('recording-review').disabled,true);
  assert.match(h.el('recording-review-status').textContent,/대기 요청/);assert.match(connectionLines(h),/대기 요청 결과를 먼저 확인/);
  assert.equal(h.calls.length,before);
});

for(const status of [401,403,404,409])test(`connection guidance: HTTP ${status} proves a response but keeps request errors visible`,async()=>{
  let broken=false;const h=harness(undefined,{capabilityHandler:()=>broken?response({error:'SYNTHETIC_REQUEST_ERROR'},status):response(V2)});
  await flush();broken=true;await h.el('connection-check').onclick();
  assert.match(h.el('connection-title').textContent,/서버 응답 있음.*요청 확인 필요/);assert.match(connectionLines(h),new RegExp('HTTP '+status));
  assert.doesNotMatch(connectionLines(h),/서버 API 응답을 받지 못했습니다/);assert.equal(h.calls.length,0);
});

for(const fault of ['network','server','json','shape'])test(`connection guidance: manual ${fault} failure is classified without creating a draft`,async()=>{
  let broken=false;const h=harness(undefined,{capabilityHandler:()=>{
    if(!broken)return response(V2);if(fault==='network')throw Error('synthetic fetch failure');
    if(fault==='server')return response({error:'SYNTHETIC_SERVICE_FAILURE'},503);
    if(fault==='json')return {ok:true,status:200,json:async()=>{throw Error('synthetic bad JSON');}};
    return response({});
  }});await flush();broken=true;await h.el('connection-check').onclick();
  assert.match(h.el('connection-title').textContent,fault==='network'?/서버 연결 확인 필요/:fault==='server'?/서버 처리 오류 확인 필요/:/서버 응답 형식 확인 필요/);
  assert.equal(h.calls.length,0);assert.equal(h.el('connection-check').disabled,false);
});

for(const caps of [null,[],{},'wrong',false])test(`connection guidance: malformed startup capabilities ${JSON.stringify(caps)} is handled without an unhandled page error`,async()=>{
  const h=harness(undefined,{capabilities:caps});await flush();
  assert.match(h.el('connection-title').textContent,/서버 응답 형식 확인 필요/);assert.match(h.el('server-status').textContent,/응답 형식이 맞지/);
  assert.equal(h.calls.length,0);assert.equal(h.meta.length,1);
});

test('connection guidance: version mismatch is reachable and distinct from a connection outage',async()=>{
  const h=harness(undefined,{capabilities:{voice_revision:'voice-old',transcript_modes:['dictation']}});await flush();await h.el('connection-check').onclick();
  assert.match(h.el('connection-title').textContent,/음성 기능 버전 확인 필요/);assert.match(connectionLines(h),/API 응답을 받았습니다/);
  assert.doesNotMatch(connectionLines(h),/API 응답을 받지 못했습니다/);
});

test('connection guidance: a timed-out probe releases controls and late success cannot overwrite the failure',async()=>{
  let held=false,resolve;const pending=new Promise(done=>resolve=done);
  const h=harness(undefined,{capabilityHandler:()=>held?pending:response(V2)});await flush();held=true;
  const check=h.el('connection-check').onclick();await flush();assert.equal(h.el('connection-check').disabled,true);
  h.expireCheck();await check;assert.equal(h.el('connection-check').disabled,false);assert.match(h.el('connection-title').textContent,/서버 연결 확인 필요/);
  resolve(response(V2));await flush();assert.match(h.el('connection-title').textContent,/서버 연결 확인 필요/);
});

test('connection guidance: changing patient cancels a held probe and ignores its old capabilities',async()=>{
  let held=false,resolve;const pending=new Promise(done=>resolve=done),server=patientServer();
  const h=harness(server.handler,{capabilityHandler:()=>held?pending:response(V2)});await flush();await speak(h,'환자 A 정보');held=true;
  const check=h.el('connection-check').onclick();await flush();await h.el('patient-new').onclick();await check;
  assert.equal(h.el('connection-check').disabled,false);assert.match(h.el('patient-current').textContent,/환자 02/);
  resolve(response({voice_revision:'STALE-PATIENT-CAPS',transcript_modes:[]}));await flush();
  assert.equal(h.el('server-chip').textContent,'음성 V2');assert.doesNotMatch(connectionLines(h),/버전이 이 화면과 일치하지/);
});

test('connection guidance: notebook changes and offline events invalidate held checks without auto retry',async()=>{
  let held=false,resolve;const pending=new Promise(done=>resolve=done),server=patientServer();
  const h=harness(server.handler,{capabilityHandler:()=>held?pending:response(V2)});await flush();await speak(h,'환자 A 정보');held=true;
  const check=h.el('connection-check').onclick();await flush();const before=h.calls.length,reads=h.meta.length;
  h.events.offline();await check;resolve(response(V2));await flush();
  assert.match(connectionLines(h),/브라우저는 오프라인/);assert.match(connectionLines(h),/API 응답은 아직 확인되지/);
  assert.equal(h.meta.length,reads);assert.equal(h.calls.length,before);
});

test('connection guidance: cross-tab raw notebook replacement prevents a held response from updating capabilities',async()=>{
  let held=false,resolve;const pending=new Promise(done=>resolve=done),server=patientServer();
  const h=harness(server.handler,{capabilityHandler:()=>held?pending:response(V2)});await flush();await speak(h,'환자 A 정보');held=true;
  const check=h.el('connection-check').onclick();await flush();h.recoveryStore.set(PATIENT_RECORDS_KEY,h.recoveryStore.get(PATIENT_RECORDS_KEY)+' ');
  resolve(response({voice_revision:'STALE-CAPS',transcript_modes:[]}));await check;
  assert.equal(h.el('server-chip').textContent,'음성 V2');assert.doesNotMatch(connectionLines(h),/버전이 이 화면과 일치하지/);
});

test('connection guidance: startup capabilities are checked for the restored client after a delayed draft GET',async()=>{
  const server=patientServer(),first=harness(server.handler);await flush();await speak(first,'환자 A 정보');
  let release;const gate=new Promise(resolve=>release=resolve);
  const restored=harness(async call=>{if(call.method==='GET')await gate;return server.handler(call);},{recoveryStore:first.recoveryStore,
    capabilities:{voice_revision:'voice-old',transcript_modes:['dictation']}});
  await flush();assert.equal(restored.meta.length,0);
  release();await flush();assert.equal(restored.el('server-chip').textContent,'버전 다름');assert.equal(restored.el('mode-auto').disabled,true);
  assert.match(restored.el('quick-session').textContent,/버전 2/);assert.match(restored.el('connection-title').textContent,/음성 기능 버전 확인 필요/);
});
