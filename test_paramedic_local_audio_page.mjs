import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import fs from 'node:fs';
import {setImmediate as nextTurn} from 'node:timers/promises';
import {VoiceClient,normalizeServer} from './paramedic-ui/voice-client.mjs';
import {captureDescription,localAudioTestMetadata} from './paramedic-ui/speech-test-context.mjs';
import {pcmInputDiagnostics,newSignalStats,collectSignalStats,signalQuality} from './paramedic-ui/audio-input-profile.mjs';
import {encodeWav} from './paramedic-ui/speech.mjs';
import {DRAFT_HANDOFF_KEY,writeDraftHandoff,readDraftHandoff,clearDraftHandoff} from './paramedic-ui/draft-handoff.mjs';
import {MainFollowup,patientNavigation} from './paramedic-ui/main-followup.mjs';
import {MainTreatmentReview} from './paramedic-ui/main-treatment-review.mjs';
import {MainRecovery,findMainRecovery,emptyMainInput,MAIN_RECOVERY_PREFIX} from './paramedic-ui/main-recovery.mjs';

// Runs actual app.js and VoiceClient with a simulated DOM/capture/server.
// WAV bytes and download objects are synthetic and remain in memory. There is
// no microphone, physical audio playback, STT, real HTTP, DB or file download.
const source=fs.readFileSync(new URL('./paramedic-ui/app.js',import.meta.url),'utf8').replace(/^import .*;\r?\n/gm,'');
const html=fs.readFileSync(new URL('./paramedic-ui/legacy.html',import.meta.url),'utf8');
const idEntries=[...html.matchAll(/<(\w+)[^>]*\bid="([^"]+)"[^>]*>/g)];
const flush=async()=>{for(let i=0;i<8;i++)await nextTurn();};

class Element {
  constructor(tag='div'){
    this.tagName=tag;this.children=[];this.textContent='';this.disabled=false;this.hidden=false;
    this.checked=false;this._value='';this.selectedIndex=0;this.selected=false;this.dataset={};
    this.attributes={};this.listeners={};this.classList={toggle(){}};
  }
  get options(){return this.children;}
  get selectedOptions(){return this.children.filter(c=>c.selected);}
  get value(){return this.tagName==='select'&&this.children.length?this.children[this.selectedIndex]?.value:this._value;}
  set value(value){value=String(value);const index=this.tagName==='select'?this.children.findIndex(c=>c.value===value):-1;if(index>=0)this.selectedIndex=index;else this._value=value;}
  append(...children){this.children.push(...children);}
  add(option){this.children.push(option);}
  replaceChildren(...children){this.children=[...children];this.selectedIndex=0;}
  setAttribute(key,value){this.attributes[key]=value;}
  addEventListener(name,callback){(this.listeners[name]??=[]).push(callback);}
  dispatch(name,event={}){for(const callback of this.listeners[name]||[])callback(event);}
  click(){this.dispatch('click');}
  querySelector(selector){const value=/^\[value="([^"]+)"\]$/.exec(selector)?.[1];return this.children.find(c=>c.value===value)||null;}
  querySelectorAll(selector){
    const descendants=this.children.flatMap(c=>[c,...c.querySelectorAll('*')]);
    if(selector==='*')return descendants;
    if(selector==='[data-field]')return descendants.filter(c=>c.dataset.field);
    return descendants.filter(c=>selector.split(',').includes(c.tagName));
  }
}

async function harness({browser=true,search='',initialStorage={},restoredSession=null,sessionGetStatus=200,allowClinicalWrites=false,expectConnected=true,storageWriteFailureKey=null,lostWriteResponses={},patientState='WAITING',conflictingApply=false,suggestionAvailable=false,initialCommittedWrites=[],followupAfterAnswer=null}={}){
  const elements=Object.fromEntries(idEntries.map(m=>[m[2],new Element(m[1])]));
  for(const m of idEntries)if(/\shidden(?:\s|>)/.test(m[0]))elements[m[2]].hidden=true;
  class Option extends Element{constructor(text,value){super('option');this.textContent=text;this.value=value;}}
  for(const m of html.matchAll(/<select[^>]*\bid="([^"]+)"[^>]*>([\s\S]*?)<\/select>/g)){
    elements[m[1]].replaceChildren(...[...m[2].matchAll(/<option[^>]*\bvalue="([^"]*)"[^>]*>([^<]*)<\/option>/g)].map(o=>new Option(o[2],o[1])));
  }
  const calls=[],captures=[],objects=new Map(),revoked=[],downloads=[],events={},timers=[],intervals=new Set(),storage=initialStorage instanceof Map?initialStorage:new Map(Object.entries(initialStorage)),historyReplacements=[];
  let requestSequence=0,objectSequence=0,sessionSequence=0;
  const makeId=()=>`fake-request-${++requestSequence}`;
  const response=data=>({ok:true,status:200,json:async()=>data});
  const existingPatient={id:'P-SYNTHETIC-HANDOFF',version:1,state:patientState,lat:37,lon:127,destination_hospital_id:patientState==='WAITING'?null:'H-SYNTHETIC'};
  const committedWrites=new Map(initialCommittedWrites),losses={...lostWriteResponses};
  let serverSession=restoredSession;
  const treatmentProposal=(mode=suggestionAvailable?'suggested':'keep')=>({session_id:serverSession.id,session_version:serverSession.version,patient_version:existingPatient.version,token:'a'.repeat(64),selection:{mode},current:{treatment_profile_id:'TR_KEEP',required_capabilities:['응급의학'],resource_demands:{ed_bed:1,special_team:1},priority:4,priority_source:'expert'},proposed:{treatment_profile_id:mode==='keep'?'TR_KEEP':'TR_SUGGESTED',required_capabilities:['응급의학'],resource_demands:{ed_bed:1,special_team:1},priority:4,priority_source:'expert'},changes:mode==='keep'?[]:[{field:'treatment_profile_id',previous:'TR_KEEP',current:'TR_SUGGESTED'}],source_changes:[{field:'stemi_suspected',previous:'no',current:'yes',removed:false,needs_clarification:false}],reductions:[],unsupported_solver_resources:[],blockers:serverSession.confirmed?[]:['VOICE_CONFIRMATION_REQUIRED'],can_apply:serverSession.confirmed});
  const fetchImpl=async(url,options)=>{
    const parsed=new URL(url),path=parsed.pathname,call={path,query:parsed.searchParams,method:options.method,body:options.body?JSON.parse(options.body):null};calls.push(call);
    if(path==='/api/voice/capabilities'&&options.method==='GET')return response({speech:{configured:false}});
    if(path==='/api/voice/terms'&&options.method==='GET')return response({supported_fields:restoredSession?['age']:[],readback_fields:[],terms:{}});
    if(path==='/api/hospitals'&&options.method==='GET')return response([]);
    if(path==='/api/voice/sessions'&&options.method==='POST'){
      serverSession={id:`VS-FAKE-LOCAL-${++sessionSequence}`,version:1,state:'DRAFT',fields:{},field_metadata:{},warnings:[],utterances:[],confirmed:false,patient_id:call.body.patient_id||null,can_undo:false};
      return response({session:serverSession});
    }
    if(/^\/api\/voice\/sessions\/[^/]+$/.test(path)&&options.method==='GET'){
      if(sessionGetStatus!==200)return {ok:false,status:sessionGetStatus,json:async()=>({message:'Synthetic restore failure'})};
      return response({session:serverSession});
    }
    if(allowClinicalWrites&&path.endsWith('/treatment-review')&&options.method==='GET')return response({review:treatmentProposal()});
    if(allowClinicalWrites&&path.endsWith('/treatment-review/preview')&&options.method==='POST')return response({review:treatmentProposal(call.body.selection.mode)});
    const writeAction=path.split('/').at(-1);
    if(allowClinicalWrites&&['transcripts','confirm','submit','apply','answers'].includes(writeAction)&&options.method==='POST'){
      const identity=path+':'+call.body.client_request_id;
      if(committedWrites.has(identity))return response({...committedWrites.get(identity),duplicate:true});
      if(writeAction==='apply'&&conflictingApply){conflictingApply=false;serverSession={...serverSession,version:serverSession.version+1};existingPatient.version++;return {ok:false,status:409,json:async()=>({error:'TREATMENT_REVIEW_STALE',message:'합성 충돌'})};}
      serverSession={...serverSession,version:serverSession.version+1,...(writeAction==='confirm'?{confirmed:true}:['submit','apply'].includes(writeAction)?{state:'SUBMITTED'}:{})};
      if(writeAction==='answers')serverSession.followup=followupAfterAnswer||{items:[{id:call.body.question_id,fields:['pulse'],askable:false,answer_status:call.body.unavailable?'unavailable':'answered'}],complete:true,unanswered_count:0};
      if(['submit','apply'].includes(writeAction))existingPatient.version=2;
      const result={session:serverSession,...(['submit','apply'].includes(writeAction)?{patient:{...existingPatient}}:{}),...(writeAction==='answers'?{answer:{accepted:true,unavailable:call.body.unavailable===true}}:{})};
      committedWrites.set(identity,result);
      if(losses[writeAction]>0){losses[writeAction]--;throw Error('Synthetic committed write response lost');}
      return response(result);
    }
    if(allowClinicalWrites&&path.endsWith('/history')&&options.method==='GET')return response({revisions:[]});
    if(path==='/api/patients/P-SYNTHETIC-HANDOFF'&&options.method==='GET')return response(existingPatient);
    if(path==='/api/patients/P-SYNTHETIC-HANDOFF/candidates'&&options.method==='GET')return response({candidates:[]});
    throw Error(`Forbidden API in local audio test: ${options.method} ${path}`);
  };
  class TestClient extends VoiceClient{constructor(options){super({...options,fetchImpl,makeId});}}
  const samples=new Float32Array([0,0.25,-0.25,0.125]);
  const wav=encodeWav(samples,16000),audio={audio_base64:Buffer.from(wav).toString('base64'),content_type:'audio/wav'};
  class FakeCapture{
    static capabilities(){return {browser,recording:true,tts:false};}
    constructor(callbacks){this.callbacks=callbacks;this.active=false;this.starts=[];this.stops=0;this.aborts=0;this.inputDiagnostics=null;captures.push(this);}
    async start(mode,options){
      this.starts.push({mode,options});assert.equal(mode,'azure','local_wav must request PCM, never browser STT');
      const flags={noiseSuppression:false,echoCancellation:true,autoGainControl:false,channelCount:1,sampleRate:48000};
      this.inputDiagnostics=pcmInputDiagnostics(options.audioProfile,{getSupportedConstraints:()=>Object.fromEntries(Object.keys(flags).map(k=>[k,true]))},
        {getAudioTracks:()=>[{getSettings:()=>({...flags,deviceId:'private-device'})}]});
      const stats=newSignalStats();collectSignalStats(stats,samples);this.inputDiagnostics.quality=signalQuality(stats,16000);
      this.active=true;this.callbacks.onState('recording');
    }
    async stop(){this.stops++;this.active=false;this.callbacks.onState('idle');return audio;}
    abort(){this.aborts++;if(this.active){this.active=false;this.callbacks.onState('idle');}}
    stopSpeaking(){}
    speak(){throw Error('No TTS expected');}
  }
  class Blob{constructor(parts,options){this.parts=parts;this.type=options.type;}}
  const createElement=tag=>{
    const el=new Element(tag);
    if(tag==='a')el.click=()=>{assert.ok(objects.has(el.href));downloads.push({url:el.href,filename:el.download,blob:objects.get(el.href)});};
    return el;
  };
  const location={search,origin:'http://127.0.0.1:18042',pathname:'/paramedic/',hash:''};
  class TestURL extends URL{static createObjectURL(blob){const url=`blob:fake-local-${++objectSequence}`;objects.set(url,blob);return url;}static revokeObjectURL(url){revoked.push(url);}}
  // This API/capture harness keeps the legacy flat DOM; staged DOM behavior has its own focused test.
  const context=vm.createContext({initStagedReview:()=>({active:false,update(){}}),canReturnToConversation:()=>false,VoiceClient:TestClient,requestId:makeId,normalizeServer,VoiceCapture:FakeCapture,captureDescription,localAudioTestMetadata,readDraftHandoff,clearDraftHandoff,MainFollowup,patientNavigation,MainTreatmentReview,MainRecovery,findMainRecovery,emptyMainInput,TextEncoder,
    document:{getElementById:id=>{assert.ok(elements[id],`Unknown element ${id}`);return elements[id];},createElement,querySelectorAll:()=>[]},
    window:{addEventListener:(name,callback)=>{events[name]=callback;},history:{replaceState(state,title,value){historyReplacements.push(value);const next=new URL(value,location.origin);location.search=next.search;location.pathname=next.pathname;location.hash=next.hash;}}},
    sessionStorage:{get length(){return storage.size;},key:index=>[...storage.keys()][index]??null,getItem:key=>storage.has(key)?storage.get(key):null,setItem:(key,value)=>{if(key===storageWriteFailureKey)throw Error('Synthetic storage denied');storage.set(key,value);},removeItem:key=>storage.delete(key)},
    location,URLSearchParams,Option,Blob,Uint8Array,
    URL:TestURL,
    atob:value=>Buffer.from(value,'base64').toString('binary'),
    setTimeout:callback=>timers.push(callback),setInterval:callback=>{intervals.add(callback);return callback;},clearInterval:callback=>intervals.delete(callback),console});
  vm.runInContext(source,context,{filename:'app.js'});await flush();
  const el=id=>elements[id];if(expectConnected)assert.equal(el('connection-status').textContent,'서버 연결됨');
  const click=async id=>{assert.equal(el(id).disabled,false,`${id} is enabled`);el(id).click();await flush();};
  const select=(id,value)=>{el(id).value=value;el(id).dispatch('change');};
  const local=()=>select('speech-provider','local_wav');
  return {el,capture:captures[0],calls,objects,revoked,downloads,events,intervals,click,select,local,storage,historyReplacements,location,committedWrites,serverSnapshot:()=>structuredClone(serverSession),
    runTimers:()=>{for(const timer of timers.splice(0))timer();}};
}

function assertNoAudioOrClinicalWrites(calls){
  assert.deepEqual(calls.filter(c=>c.method!=='GET').map(c=>c.path),calls.filter(c=>c.path==='/api/voice/sessions').map(c=>c.path));
  assert.ok(calls.every(c=>['/api/voice/capabilities','/api/voice/terms','/api/hospitals','/api/voice/sessions'].includes(c.path)));
  assert.equal(calls.filter(c=>/\/audio|\/transcripts|\/submit|\/patients/.test(c.path)).length,0);
}
async function startExplicitEmptyDraft(h){
  assert.equal(h.calls.filter(c=>c.method!=='GET').length,0,'startup never automatically creates a draft');
  assert.equal(h.el('record-button').disabled,true,'a draft is required before capture');
  await h.click('new-case');
  assert.equal(h.calls.filter(c=>c.method!=='GET').length,1,'one explicit draft creation is the only write');
}

test('local WAV stays available without Azure credentials and PCM controls follow the selected route',async()=>{
  const h=await harness();
  await startExplicitEmptyDraft(h);
  assert.equal(h.el('speech-provider').value,'browser');assert.equal(h.el('audio-input-profile').disabled,true);
  assert.equal(h.el('speech-provider').querySelector('[value="azure"]').disabled,true);
  assert.equal(h.el('speech-provider').querySelector('[value="local_wav"]').disabled,false);
  h.local();assert.equal(h.el('record-button').disabled,false);assert.equal(h.el('audio-input-profile').disabled,false);
  await h.click('record-button');assert.equal(h.capture.active,true);assert.equal(h.el('audio-input-profile').disabled,true);
  await h.click('record-button');assert.equal(h.capture.active,false);assert.equal(h.el('audio-input-profile').disabled,false);
  h.select('speech-provider','browser');assert.equal(h.el('audio-input-profile').disabled,true);
  assert.equal(h.calls.length,7);assertNoAudioOrClinicalWrites(h.calls);
});

const HANDOFF_ORIGIN='http://127.0.0.1:18042';
const handoffSession=overrides=>({id:'VS-SYNTHETIC-HANDOFF',version:3,state:'DRAFT',fields:{age:47},field_metadata:{},warnings:[],utterances:[],confirmed:false,patient_id:null,can_undo:false,...overrides});
function handoffStorage({overrides={},now=Date.now(),existing={}}={}){
  const storage=new Map(Object.entries(existing));
  writeDraftHandoff({setItem:(key,value)=>storage.set(key,value)}, {serverOrigin:HANDOFF_ORIGIN,actorId:'QUICK-VOICE-SYNTHETIC',sessionId:'VS-SYNTHETIC-HANDOFF',version:3,unappliedText:'',intent:'recommend',...overrides},{now});
  return storage;
}
const oldMainKeys={'paramedic.voice.actor':'main-actor','paramedic.voice.session':'VS-MAIN-SYNTHETIC','paramedic.voice.server':HANDOFF_ORIGIN,'unrelated-key':'keep'};
function assertNoRestoreWrites(h){assert.equal(h.calls.filter(call=>call.method!=='GET').length,0);assert.equal(h.calls.filter(call=>/\/patients|\/submit|\/transcripts|\/audio|\/confirm/.test(call.path)).length,0);}

test('handoff restores the exact server draft with GET only, then replaces saved identifiers and clears only its reference',async()=>{
  const storage=handoffStorage({existing:oldMainKeys});
  const h=await harness({search:'?voice_handoff=1',initialStorage:storage,restoredSession:handoffSession()});
  assertNoRestoreWrites(h);
  const sync=h.calls.find(call=>call.path==='/api/voice/sessions/VS-SYNTHETIC-HANDOFF');
  assert.equal(sync.query.get('actor_id'),'QUICK-VOICE-SYNTHETIC');
  assert.equal(h.el('recommendation-prep').hidden,false);assert.equal(h.el('recommendation-prep').dataset.state,'ready');
  assert.equal(h.el('session-meta').textContent,'새 환자 초안 · 버전 3');
  assert.equal(storage.get('paramedic.voice.actor'),'QUICK-VOICE-SYNTHETIC');
  assert.equal(storage.get('paramedic.voice.session'),'VS-SYNTHETIC-HANDOFF');
  assert.equal(storage.get('paramedic.voice.server'),HANDOFF_ORIGIN);
  assert.equal(storage.has(DRAFT_HANDOFF_KEY),false);assert.equal(storage.get('unrelated-key'),'keep');
  assert.equal(h.el('consent').checked,false);assert.equal(h.el('lat').value,'');assert.equal(h.el('submit-button').disabled,true);
});

test('handoff pending tail is preserved exactly in the review input and is never automatically written or confirmed',async()=>{
  const tail='  synthetic review line\nsecond line  ';
  const h=await harness({search:'?voice_handoff=1',initialStorage:handoffStorage({overrides:{unappliedText:tail}}),restoredSession:handoffSession()});
  assert.equal(h.el('transcript-input').value,tail);assert.equal(h.el('handoff-pending-note').hidden,false);
  assert.match(h.el('notice').textContent,/아직 미반영/);assert.equal(h.el('confirm-button').disabled,true);
  assert.equal(h.el('record-button').disabled,true);assert.equal(h.el('sample-button').disabled,true);
  assert.equal(h.el('connect-server').disabled,true,'reconnect cannot erase the unreviewed RAM-only tail');
  assertNoRestoreWrites(h);
  await h.click('discard-handoff-text');assert.equal(h.el('transcript-input').value,'');
  assert.equal(h.el('handoff-pending-note').hidden,true);assert.equal(h.el('confirm-button').disabled,false);
  assert.equal(h.el('connect-server').disabled,false);
  assertNoRestoreWrites(h);
});

test('pending text is sent only after the explicit existing sentence-apply button',async()=>{
  const tail='synthetic pending review text';
  const h=await harness({search:'?voice_handoff=1',initialStorage:handoffStorage({overrides:{unappliedText:tail}}),restoredSession:handoffSession(),allowClinicalWrites:true});
  assertNoRestoreWrites(h);await h.click('send-transcript');
  const writes=h.calls.filter(call=>call.method!=='GET');
  assert.equal(writes.length,1);assert.equal(writes[0].path,'/api/voice/sessions/VS-SYNTHETIC-HANDOFF/transcripts');
  assert.equal(writes[0].body.text,tail);assert.equal(writes[0].body.actor_id,'QUICK-VOICE-SYNTHETIC');
  assert.equal(h.el('handoff-pending-note').hidden,true);
  assert.equal(h.calls.some(call=>/\/submit|\/patients/.test(call.path)),false);
});

test('lost transcript response retry confirms the same committed text once and releases the pending-tail controls',async()=>{
  const tail='synthetic pending review text';
  const h=await harness({search:'?voice_handoff=1',initialStorage:handoffStorage({overrides:{unappliedText:tail}}),restoredSession:handoffSession(),allowClinicalWrites:true,lostWriteResponses:{transcripts:1}});
  await h.click('send-transcript');
  assert.equal(h.el('retry-request').hidden,false);assert.equal(h.el('confirm-button').disabled,true);
  assert.equal(h.el('handoff-pending-note').hidden,false);
  await h.click('retry-request');
  const attempts=h.calls.filter(call=>call.path.endsWith('/transcripts'));
  assert.equal(attempts.length,2);assert.deepEqual(attempts[0].body,attempts[1].body);
  assert.equal(h.el('session-meta').textContent,'새 환자 초안 · 버전 4','idempotent retry does not add a second revision');
  assert.equal(h.el('handoff-pending-note').hidden,true);assert.equal(h.el('retry-request').hidden,true);
  assert.equal(h.el('confirm-button').disabled,false);assert.equal(h.el('record-button').disabled,false);
  assert.equal(h.calls.some(call=>/\/submit|\/patients/.test(call.path)),false);
});

test('retrying committed original text preserves a user-edited review tail until its own explicit apply',async()=>{
  const original='synthetic original pending line',edited='synthetic edited pending line';
  const h=await harness({search:'?voice_handoff=1',initialStorage:handoffStorage({overrides:{unappliedText:original}}),restoredSession:handoffSession(),allowClinicalWrites:true,lostWriteResponses:{transcripts:1}});
  await h.click('send-transcript');h.el('transcript-input').value=edited;
  await h.click('retry-request');
  const attempts=h.calls.filter(call=>call.path.endsWith('/transcripts'));
  assert.equal(attempts.length,2);assert.equal(attempts[1].body.text,original);assert.deepEqual(attempts[0].body,attempts[1].body);
  assert.equal(h.el('transcript-input').value,edited);assert.equal(h.el('handoff-pending-note').hidden,false);
  assert.equal(h.el('confirm-button').disabled,true);assert.equal(h.el('record-button').disabled,true);
  assert.match(h.el('notice').textContent,/다른 문장은 아직 미반영/);
  await h.click('send-transcript');
  const finalAttempts=h.calls.filter(call=>call.path.endsWith('/transcripts'));
  assert.equal(finalAttempts.length,3);assert.equal(finalAttempts[2].body.text,edited);
  assert.notEqual(finalAttempts[2].body.client_request_id,finalAttempts[0].body.client_request_id);
  assert.equal(h.el('handoff-pending-note').hidden,true);assert.equal(h.el('confirm-button').disabled,false);
});

test('handoff origin mismatch fails before networking and preserves all original saved keys',async()=>{
  const storage=handoffStorage({existing:oldMainKeys,overrides:{serverOrigin:'http://other.test'}}),before=new Map(storage);
  const h=await harness({search:'?voice_handoff=1',initialStorage:storage,expectConnected:false});
  assert.equal(h.calls.length,0);assert.deepEqual(storage,before);
  assert.equal(h.el('recommendation-prep').dataset.state,'error');assert.equal(h.el('handoff-new-case').hidden,false);
  assert.equal(h.el('record-button').disabled,true);assert.equal(h.el('load-patient').disabled,true);
  assert.equal(h.historyReplacements.length,0);
});

test('expired handoff cannot supply text or create a replacement draft',async()=>{
  const storage=handoffStorage({now:Date.now()-600_001,existing:oldMainKeys,overrides:{unappliedText:'synthetic expired text'}}),before=new Map(storage);
  const h=await harness({search:'?voice_handoff=1',initialStorage:storage,expectConnected:false});
  assert.equal(h.calls.length,0);assert.equal(h.el('transcript-input').value,'');assert.deepEqual(storage,before);
  assert.match(h.el('error').textContent,/유효시간/);assert.equal(h.historyReplacements.length,0);
});

test('404 restore and reconnect never fall through to blank create or overwrite original identifiers',async()=>{
  const storage=handoffStorage({existing:oldMainKeys}),before=new Map(storage);
  const h=await harness({search:'?voice_handoff=1',initialStorage:storage,sessionGetStatus:404,expectConnected:false});
  assertNoRestoreWrites(h);assert.deepEqual(storage,before);
  assert.match(h.el('session-meta').textContent,/새 빈 초안을 만들지 않았습니다/);
  await h.click('connect-server');assertNoRestoreWrites(h);assert.deepEqual(storage,before);
  assert.equal(h.historyReplacements.length,0);
});

for(const [description,overrides]of [['older version',{version:2}],['wrong session ID',{id:'VS-SYNTHETIC-OTHER'}]])test(`restore rejects a ${description} without consuming handoff or replacing saved keys`,async()=>{
  const storage=handoffStorage({existing:oldMainKeys}),before=new Map(storage);
  const h=await harness({search:'?voice_handoff=1',initialStorage:storage,restoredSession:handoffSession(overrides),expectConnected:false});
  assertNoRestoreWrites(h);assert.deepEqual(storage,before);
  assert.equal(h.el('recommendation-prep').dataset.state,'error');assert.match(h.el('error').textContent,/ID 또는 버전/);
  assert.equal(h.historyReplacements.length,0);
});

for(const overrides of [{state:'SUBMITTED'},{actor_id:'OTHER-SYNTHETIC-ACTOR'}])test(`restore refuses a mismatching draft state or returned actor: ${JSON.stringify(overrides)}`,async()=>{
  const storage=handoffStorage({existing:oldMainKeys}),before=new Map(storage);
  const h=await harness({search:'?voice_handoff=1',initialStorage:storage,restoredSession:handoffSession(overrides),expectConnected:false});
  assertNoRestoreWrites(h);assert.deepEqual(storage,before);assert.equal(h.historyReplacements.length,0);
  assert.match(h.el('error').textContent,/상태 또는 작성자/);
});

test('success accepts a newer draft and removes only the handoff query parameter so refresh resumes normally',async()=>{
  const storage=handoffStorage();
  const h=await harness({search:'?voice_handoff=1&server='+encodeURIComponent(HANDOFF_ORIGIN)+'&keep=1',initialStorage:storage,restoredSession:handoffSession({version:4})});
  assertNoRestoreWrites(h);assert.equal(h.historyReplacements.length,1);
  assert.equal(new URLSearchParams(h.location.search).has('voice_handoff'),false);
  assert.equal(new URLSearchParams(h.location.search).get('keep'),'1');
  assert.equal(new URLSearchParams(h.location.search).get('server'),HANDOFF_ORIGIN);
  const refreshed=await harness({search:h.location.search,initialStorage:storage,restoredSession:handoffSession({version:4})});
  assertNoRestoreWrites(refreshed);assert.equal(refreshed.el('session-meta').textContent,'새 환자 초안 · 버전 4');
  assert.equal(refreshed.el('recommendation-prep').hidden,true);
});

test('without the explicit handoff query a stored reference is ignored and an empty draft requires an explicit new-record action',async()=>{
  const storage=handoffStorage(),raw=storage.get(DRAFT_HANDOFF_KEY);
  const h=await harness({initialStorage:storage});
  assert.equal(h.calls.filter(call=>call.method==='POST').length,0);
  assert.equal(h.calls.some(call=>call.path==='/api/voice/sessions/VS-SYNTHETIC-HANDOFF'),false);
  assert.equal(storage.get(DRAFT_HANDOFF_KEY),raw);assert.equal(h.el('recommendation-prep').hidden,true);
  await startExplicitEmptyDraft(h);
  assert.equal(h.calls.filter(call=>call.method==='POST').length,1);assert.equal(storage.get(DRAFT_HANDOFF_KEY),raw);
});

test('after failed restoration only an explicit new-record button creates one fresh draft',async()=>{
  const storage=handoffStorage({existing:oldMainKeys}),raw=storage.get(DRAFT_HANDOFF_KEY);
  const h=await harness({search:'?voice_handoff=1',initialStorage:storage,sessionGetStatus:404,expectConnected:false});
  assertNoRestoreWrites(h);await h.click('handoff-new-case');
  const writes=h.calls.filter(call=>call.method!=='GET');
  assert.equal(writes.length,1);assert.equal(writes[0].path,'/api/voice/sessions');assert.equal(writes[0].body.actor_id,'main-actor');
  assert.equal(storage.get('paramedic.voice.actor'),'main-actor');assert.equal(storage.get('paramedic.voice.session'),'VS-FAKE-LOCAL-1');
  assert.equal(storage.get(DRAFT_HANDOFF_KEY),raw,'failed reference remains intact for the originating page');
  assert.equal(h.el('recommendation-prep').hidden,true);assert.equal(h.historyReplacements.length,1);
});

test('failed persistence rolls back previous identity keys and keeps the handoff reference and URL',async()=>{
  const storage=handoffStorage({existing:oldMainKeys}),before=new Map(storage);
  const h=await harness({search:'?voice_handoff=1',initialStorage:storage,restoredSession:handoffSession(),storageWriteFailureKey:'paramedic.voice.session',expectConnected:false});
  assertNoRestoreWrites(h);assert.deepEqual(storage,before);assert.equal(h.historyReplacements.length,0);
  assert.equal(h.el('recommendation-prep').dataset.state,'error');assert.match(h.el('error').textContent,/저장하지 못했습니다/);
});

test('a restored existing-patient draft uses explicit treatment review/apply and keeps general submit hidden',async()=>{
  const h=await harness({search:'?voice_handoff=1',initialStorage:handoffStorage(),restoredSession:handoffSession({patient_id:'P-SYNTHETIC-HANDOFF'}),allowClinicalWrites:true});
  assert.equal(h.calls.filter(call=>call.method!=='GET').length,0);
  assert.equal(h.calls.filter(call=>call.path==='/api/patients/P-SYNTHETIC-HANDOFF').length,1);
  assert.equal(h.calls.some(call=>call.path.endsWith('/candidates')),false);assert.equal(h.el('refresh-candidates').disabled,true);
  assert.equal(h.el('workflow-step').textContent,'02 정보 확인·수정');
  await h.click('confirm-button');assert.equal(h.calls.some(call=>call.path.endsWith('/candidates')),false);
  assert.equal(h.el('submit-button').hidden,true);assert.equal(h.el('submit-button').disabled,true);assert.equal(h.el('treatment-apply').disabled,true);
  h.el('treatment-confirm').checked=true;h.el('treatment-confirm').dispatch('change');await h.click('treatment-apply');
  const apply=h.calls.find(call=>call.path.endsWith('/treatment-review/apply'));
  assert.equal(apply.body.review_token,'a'.repeat(64));assert.equal(apply.body.expected_patient_version,1);assert.equal(apply.body.confirmed,true);assert.equal(h.calls.some(call=>call.path.endsWith('/submit')),false);assert.equal(h.calls.filter(call=>call.path.endsWith('/candidates')).length,1);
  assert.equal(h.el('refresh-candidates').disabled,false);
  const link=new URL(h.el('patient-progress-link').href);assert.equal(link.searchParams.get('pid'),'P-SYNTHETIC-HANDOFF');assert.equal(link.searchParams.get('actor_id'),'QUICK-VOICE-SYNTHETIC');assert.equal(link.origin,HANDOFF_ORIGIN);
});

test('lost treatment-apply response retries exactly and unlocks candidates after commit verification',async()=>{
  const h=await harness({search:'?voice_handoff=1',initialStorage:handoffStorage(),restoredSession:handoffSession({patient_id:'P-SYNTHETIC-HANDOFF'}),allowClinicalWrites:true,lostWriteResponses:{apply:1}});
  await h.click('confirm-button');h.el('treatment-confirm').checked=true;h.el('treatment-confirm').dispatch('change');await h.click('treatment-apply');
  assert.equal(h.el('retry-request').hidden,false);assert.equal(h.el('refresh-candidates').disabled,true);
  assert.equal(h.calls.some(call=>call.path.endsWith('/candidates')),false);
  await h.click('retry-request');
  const attempts=h.calls.filter(call=>call.path.endsWith('/treatment-review/apply'));
  assert.equal(attempts.length,2);assert.deepEqual(attempts[0].body,attempts[1].body);assert.equal(attempts[1].body.review_token,'a'.repeat(64));
  assert.equal(h.el('session-meta').textContent,'환자 P-SYNTHETIC-HANDOFF · 버전 5');
  assert.equal(h.calls.filter(call=>call.path.endsWith('/candidates')).length,1);
  assert.equal(h.el('refresh-candidates').disabled,false);assert.equal(h.el('retry-request').hidden,true);
  assert.equal(h.calls.filter(call=>call.path==='/api/voice/sessions'&&call.method==='POST').length,0);
});

test('reconnecting an unsubmitted existing-patient draft preserves the candidate gate and explicit new case resets the flow',async()=>{
  const h=await harness({search:'?voice_handoff=1',initialStorage:handoffStorage(),restoredSession:handoffSession({patient_id:'P-SYNTHETIC-HANDOFF'})});
  await h.click('connect-server');
  assert.equal(h.calls.some(call=>call.path.endsWith('/candidates')),false);assert.equal(h.el('refresh-candidates').disabled,true);
  assert.equal(h.el('workflow-step').textContent,'02 정보 확인·수정');
  await h.click('new-case');
  assert.equal(h.el('recommendation-prep').hidden,true);assert.equal(h.el('workflow-step').textContent,'01 음성으로 기록');
  assert.equal(h.calls.filter(call=>call.path==='/api/voice/sessions'&&call.method==='POST').length,1);
});

test('incoming same-patient link creates no blank draft and only explicit load binds that patient and actor',async()=>{
  const h=await harness({search:'?pid=P-SYNTHETIC-HANDOFF&actor_id=CREW-FROM-PROGRESS'});
  assert.equal(h.el('resume-patient').value,'P-SYNTHETIC-HANDOFF');assert.equal(h.calls.filter(c=>c.method==='POST').length,0);
  await h.click('load-patient');const created=h.calls.find(c=>c.path==='/api/voice/sessions');assert.equal(created.body.patient_id,'P-SYNTHETIC-HANDOFF');assert.equal(created.body.actor_id,'CREW-FROM-PROGRESS');
  assert.equal(h.el('main-treatment').hidden,false);assert.equal(h.el('submit-button').hidden,true);assert.equal(h.calls.filter(c=>c.path==='/api/patients'&&c.method==='POST').length,0);
});
test('invalid actor query shows recovery guidance before any server call',async()=>{
  const h=await harness({search:'?pid=P-SYNTHETIC-HANDOFF&actor_id='+encodeURIComponent('unsafe actor'),expectConnected:false});assert.equal(h.calls.length,0);assert.match(h.el('error').textContent,/작업자 ID/);
});
test('confirmed patient keeps general submit closed and links the same origin/patient/actor to enroute review',async()=>{
  const h=await harness({search:'?pid=P-SYNTHETIC-HANDOFF&actor_id=CREW-FROM-PROGRESS',patientState:'IN_TRANSIT'});await h.click('load-patient');
  assert.equal(h.el('main-treatment').hidden,true);assert.equal(h.el('submit-button').hidden,true);assert.equal(h.el('patient-change-link').hidden,false);
  const u=new URL(h.el('patient-change-link').href);assert.equal(u.origin,HANDOFF_ORIGIN);assert.equal(u.pathname,'/patient/transport-change.html');assert.equal(u.searchParams.get('pid'),'P-SYNTHETIC-HANDOFF');assert.equal(u.searchParams.get('actor_id'),'CREW-FROM-PROGRESS');assert.equal(u.searchParams.get('hid'),'H-SYNTHETIC');
});
test('main question unmeasured button uses offered question and preserves previous measured value',async()=>{
  const q={id:'pulse',fields:['pulse'],question:'맥박은 얼마인가요?',askable:true};
  const h=await harness({search:'?voice_handoff=1',initialStorage:handoffStorage(),restoredSession:handoffSession({fields:{pulse:90},followup:{items:[q],next_question:q,unanswered_count:1}}),allowClinicalWrites:true});
  assert.equal(h.el('main-question-text').textContent,q.question);await h.click('main-answer-unavailable');const answer=h.calls.find(c=>c.path.endsWith('/followup/answers'));assert.equal(answer.body.question_id,'pulse');assert.equal(answer.body.unavailable,true);assert.equal(answer.body.profile,'voice_extended_v1');assert.match(h.el('main-followup-status').textContent,/미측정·확인 불가 1개/);assert.equal(h.el('main-followup').hidden,true);assert.equal(h.calls.some(c=>/submit|treatment-review\/apply/.test(c.path)),false);
});

for(const variant of [
  {name:'accepted answer',input:'90',button:'main-answer-send',expected:''},
  {name:'unavailable answer',input:'미측정',button:'main-answer-unavailable',expected:''},
  {name:'separately edited input',input:'별도로 편집한 합성 답변',button:'main-answer-unavailable',expected:'별도로 편집한 합성 답변'}
])test(`reloaded exact followup retry clears only the matching committed ${variant.name} before showing the next question`,async()=>{
  const q={id:'pulse',fields:['pulse'],question:'맥박은 얼마인가요?',askable:true};
  const next={id:'spo2',fields:['spo2'],question:'산소포화도는 얼마인가요?',askable:true};
  const first=await harness({search:'?voice_handoff=1',initialStorage:handoffStorage(),restoredSession:handoffSession({fields:{pulse:90},followup:{items:[q],next_question:q,unanswered_count:1}}),allowClinicalWrites:true,lostWriteResponses:{answers:1},followupAfterAnswer:{items:[next],next_question:next,unanswered_count:1}});
  first.el('main-answer').value=variant.input;first.el('main-answer').dispatch('input');await first.click(variant.button);
  const original=first.calls.find(c=>c.path.endsWith('/followup/answers'));
  assert.ok(original);assert.equal(first.el('retry-request').hidden,false);
  const restored=await harness({initialStorage:first.storage,restoredSession:first.serverSnapshot(),allowClinicalWrites:true,initialCommittedWrites:first.committedWrites});
  assert.equal(restored.calls.filter(c=>c.method!=='GET').length,0,'reload only reads, never retries by itself');
  assert.equal(restored.el('main-answer').value,variant.input);
  await restored.click('retry-request');
  const retry=restored.calls.filter(c=>c.path.endsWith('/followup/answers'));
  assert.equal(retry.length,1);assert.deepEqual(retry[0].body,original.body,'retry retains original question, utterance and request IDs');
  assert.equal(restored.el('main-question-text').textContent,next.question);
  assert.equal(restored.el('main-answer').value,variant.expected);
  assert.equal(restored.el('session-meta').textContent,'새 환자 초안 · 버전 4','duplicate retry does not add a second revision');
  assert.equal(restored.el('retry-request').hidden,true);
  assert.equal(restored.calls.filter(c=>c.path==='/api/voice/sessions'&&c.method==='POST').length,0);
});
for(const action of ['new-case','load-patient'])test(`${action} clears the prior case answer and treatment reason before storing the next case`,async()=>{
  const q={id:'pulse',fields:['pulse'],question:'맥박은 얼마인가요?',askable:true};
  const h=await harness({search:'?voice_handoff=1',initialStorage:handoffStorage(),restoredSession:handoffSession({followup:{items:[q],next_question:q,unanswered_count:1}})});
  const previousSession=h.serverSnapshot().id;
  h.el('main-answer').value='이전 환자의 합성 답변 105';h.el('main-answer').dispatch('input');
  h.el('treatment-reason').value='이전 환자에 대한 검토 사유';
  if(action==='load-patient')h.el('resume-patient').value='P-SYNTHETIC-HANDOFF';
  await h.click(action);
  const nextSession=h.serverSnapshot().id;assert.notEqual(nextSession,previousSession);
  assert.equal(h.el('main-answer').value,'');assert.equal(h.el('treatment-reason').value,'');
  const records=[...h.storage.entries()].filter(([key])=>key.startsWith(MAIN_RECOVERY_PREFIX)).map(([,raw])=>JSON.parse(raw));
  assert.equal(records.find(r=>r.sessionId===previousSession).ui.answer,'이전 환자의 합성 답변 105','old scoped input remains in its own recovery record');
  assert.equal(records.find(r=>r.sessionId===nextSession).ui.answer,'','next case checkpoint cannot acquire the prior answer');
  assert.equal(h.calls.filter(c=>c.path.endsWith('/followup/answers')).length,0,'case navigation never submits an answer');
  const creates=h.calls.filter(c=>c.method==='POST'&&c.path==='/api/voice/sessions');assert.equal(creates.length,1);
  assert.equal(creates[0].body.patient_id,action==='load-patient'?'P-SYNTHETIC-HANDOFF':undefined);
});

test('same-case reload restores an unsent answer without writing or carrying a transient treatment reason',async()=>{
  const q={id:'pulse',fields:['pulse'],question:'맥박은 얼마인가요?',askable:true};
  const first=await harness({search:'?voice_handoff=1',initialStorage:handoffStorage(),restoredSession:handoffSession({followup:{items:[q],next_question:q,unanswered_count:1}})});
  first.el('main-answer').value='같은 환자의 미전송 답변 105';first.el('main-answer').dispatch('input');
  first.el('treatment-reason').value='전송하지 않은 임시 검토 사유';
  const restored=await harness({initialStorage:first.storage,restoredSession:first.serverSnapshot()});
  assert.equal(restored.serverSnapshot().id,first.serverSnapshot().id);
  assert.equal(restored.el('main-answer').value,'같은 환자의 미전송 답변 105');
  assert.equal(restored.el('treatment-reason').value,'','clinical justification still requires a deliberate current review');
  assert.equal(restored.calls.filter(c=>c.method!=='GET').length,0);
});

test('409 apply reloads latest patient/draft/token and clears prior explicit checks',async()=>{
  const h=await harness({search:'?voice_handoff=1',initialStorage:handoffStorage(),restoredSession:handoffSession({patient_id:'P-SYNTHETIC-HANDOFF'}),allowClinicalWrites:true,conflictingApply:true});
  await h.click('confirm-button');h.el('treatment-confirm').checked=true;h.el('treatment-confirm').dispatch('change');await h.click('treatment-apply');
  assert.equal(h.el('treatment-confirm').checked,false);assert.equal(h.el('treatment-apply').disabled,true);assert.match(h.el('treatment-status').textContent,/충돌 후/);assert.ok(h.calls.filter(c=>c.path.endsWith('/treatment-review')&&c.method==='GET').length>=2);assert.equal(h.calls.some(c=>c.path.endsWith('/submit')),false);
});

for(const trigger of ['treatment-load','confirm-button'])test(`explicit Keep remains selected and applied after ${trigger} when GET defaults to suggested`,async()=>{
  const h=await harness({search:'?voice_handoff=1',initialStorage:handoffStorage(),restoredSession:handoffSession({patient_id:'P-SYNTHETIC-HANDOFF',confirmed:trigger==='treatment-load'}),allowClinicalWrites:true,suggestionAvailable:true});
  h.select('treatment-mode','keep');h.el('treatment-confirm').checked=true;h.el('treatment-reduction').checked=true;await h.click(trigger);
  assert.equal(h.el('treatment-mode').value,'keep');assert.equal(h.el('treatment-confirm').checked,false);assert.equal(h.el('treatment-reduction').checked,false);assert.equal(h.el('treatment-apply').disabled,true);
  const preview=h.calls.filter(c=>c.path.endsWith('/treatment-review/preview')).at(-1);assert.deepEqual(preview.body.selection,{mode:'keep'});assert.match(h.el('treatment-detail').textContent,/TR_KEEP/);assert.doesNotMatch(h.el('treatment-detail').textContent,/TR_SUGGESTED/);
  assert.match(h.el('treatment-detail').textContent,/진료 자리 \(ed_bed\): 1/);assert.match(h.el('treatment-detail').textContent,/전문팀 \(special_team\): 1/);assert.match(h.el('treatment-detail').textContent,/심전도 ST 상승 \(stemi_suspected\): 없음 \(no\) → 확인됨 \(yes\)/);assert.doesNotMatch(h.el('treatment-detail').textContent,/"resource_demands"|\{"ed_bed"/);
  h.el('treatment-confirm').checked=true;h.el('treatment-confirm').dispatch('change');await h.click('treatment-apply');assert.deepEqual(h.calls.find(c=>c.path.endsWith('/treatment-review/apply')).body.selection,{mode:'keep'});
});
test('initial unavailable server suggestion changes displayed choice to Keep with explanation and no preview error',async()=>{
  const h=await harness({search:'?voice_handoff=1',initialStorage:handoffStorage(),restoredSession:handoffSession({patient_id:'P-SYNTHETIC-HANDOFF'}),allowClinicalWrites:true});
  assert.equal(h.el('treatment-mode').value,'suggested');await h.click('confirm-button');assert.equal(h.el('treatment-mode').value,'keep');assert.match(h.el('treatment-status').textContent,/제안이 없어/);assert.equal(h.calls.some(c=>c.path.endsWith('/treatment-review/preview')),false);assert.equal(h.el('error').hidden,true);
});

test('actual app local route returns paired WAV/settings downloads without any audio or clinical API write',async()=>{
  const h=await harness({browser:false});
  await startExplicitEmptyDraft(h);
  assert.equal(h.el('speech-provider').value,'local_wav');h.select('audio-input-profile','noise_suppressed');
  await h.click('record-button');await h.click('record-button');
  assert.equal(h.capture.starts.length,1);assert.equal(h.capture.starts[0].mode,'azure');
  assert.equal(h.capture.starts[0].options.audioProfile,'noise_suppressed');
  assert.equal(h.el('audio-test-download').hidden,false);assert.equal(h.el('audio-test-metadata').hidden,false);
  const wavBlob=h.objects.get(h.el('audio-test-download').href);
  assert.equal(wavBlob.type,'audio/wav');assert.equal(Buffer.from(wavBlob.parts[0]).subarray(0,4).toString(),'RIFF');
  await h.click('audio-test-metadata');const exported=h.downloads.at(-1);
  assert.equal(exported.blob.type,'application/json');const metadata=JSON.parse(exported.blob.parts.join(''));
  assert.match(metadata.recording_id,/^AUDIO-fake-request-\d+$/);
  assert.equal(metadata.wav_filename,h.el('audio-test-download').download);
  assert.equal(metadata.wav_filename,metadata.recording_id+'.wav');
  assert.equal(metadata.audio_input.profile,'noise_suppressed');
  assert.equal(metadata.audio_input.requested.noiseSuppression,true);assert.equal(metadata.audio_input.applied.noiseSuppression,false);
  assert.equal(metadata.audio_sent_to_server,false);assert.equal(metadata.audio_sent_to_stt,false);
  assert.equal(metadata.reference_verified,false);assert.equal(metadata.speech_recognition_accuracy_measured,false);
  assert.equal(metadata.source,'unverified_microphone_audio');assert.doesNotMatch(JSON.stringify(metadata),/private-device|deviceId|actor_id|session_id/);
  assert.match(h.el('notice').textContent,/오디오 서버 전송은 실행하지 않았습니다/);
  assert.equal(h.calls.length,7);assertNoAudioOrClinicalWrites(h.calls);
  h.runTimers();assert.ok(h.revoked.includes(exported.url));
});

test('recording again and starting a new case revoke prior WAV URLs and clear download metadata',async()=>{
  const h=await harness({browser:false});await startExplicitEmptyDraft(h);await h.click('record-button');await h.click('record-button');
  const first=h.el('audio-test-download').href;await h.click('record-button');
  assert.ok(h.revoked.includes(first));assert.equal(h.el('audio-test-download').hidden,true);assert.equal(h.el('audio-test-metadata').hidden,true);
  const before=h.downloads.length;h.el('audio-test-metadata').click();assert.equal(h.downloads.length,before,'cleared metadata cannot be downloaded');
  await h.click('record-button');const second=h.el('audio-test-download').href;assert.notEqual(second,first);
  await h.click('new-case');assert.ok(h.revoked.includes(second));
  assert.equal(h.el('audio-test-download').hidden,true);assert.equal(h.el('audio-test-metadata').hidden,true);
  h.el('audio-test-metadata').click();assert.equal(h.downloads.length,before);
  assert.equal(h.calls.filter(c=>c.method==='POST').length,2,'only the two explicitly requested new empty drafts');
  assertNoAudioOrClinicalWrites(h.calls);
});

test('pagehide clears saved URLs/metadata and releases an active local capture',async()=>{
  const h=await harness({browser:false});await startExplicitEmptyDraft(h);await h.click('record-button');await h.click('record-button');
  const old=h.el('audio-test-download').href;h.events.pagehide();
  assert.ok(h.revoked.includes(old));assert.equal(h.el('audio-test-download').hidden,true);assert.equal(h.el('audio-test-metadata').hidden,true);
  h.el('audio-test-metadata').click();assert.equal(h.downloads.length,0);
  await h.click('record-button');assert.equal(h.capture.active,true);h.events.pagehide();
  assert.equal(h.capture.active,false);assert.equal(h.intervals.size,0);
  assert.equal(h.calls.length,7);assertNoAudioOrClinicalWrites(h.calls);
});
