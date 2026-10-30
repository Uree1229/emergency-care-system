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

// Runs actual app.js and VoiceClient with a simulated DOM/capture/server.
// WAV bytes and download objects are synthetic and remain in memory. There is
// no microphone, physical audio playback, STT, real HTTP, DB or file download.
const source=fs.readFileSync(new URL('./paramedic-ui/app.js',import.meta.url),'utf8').replace(/^import .*;\r?\n/gm,'');
const html=fs.readFileSync(new URL('./paramedic-ui/index.html',import.meta.url),'utf8');
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
  set value(value){const index=this.tagName==='select'?this.children.findIndex(c=>c.value===value):-1;if(index>=0)this.selectedIndex=index;else this._value=value;}
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

async function harness({browser=true,search='',initialStorage={},restoredSession=null,sessionGetStatus=200,allowClinicalWrites=false,expectConnected=true,storageWriteFailureKey=null,lostWriteResponses={}}={}){
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
  const existingPatient={id:'P-SYNTHETIC-HANDOFF',version:1,state:'WAITING',lat:37,lon:127};
  const committedWrites=new Map(),losses={...lostWriteResponses};
  let serverSession=restoredSession;
  const fetchImpl=async(url,options)=>{
    const parsed=new URL(url),path=parsed.pathname,call={path,query:parsed.searchParams,method:options.method,body:options.body?JSON.parse(options.body):null};calls.push(call);
    if(path==='/api/voice/capabilities'&&options.method==='GET')return response({speech:{configured:false}});
    if(path==='/api/voice/terms'&&options.method==='GET')return response({supported_fields:restoredSession?['age']:[],readback_fields:[],terms:{}});
    if(path==='/api/hospitals'&&options.method==='GET')return response([]);
    if(path==='/api/voice/sessions'&&options.method==='POST'){
      serverSession={id:`VS-FAKE-LOCAL-${++sessionSequence}`,version:1,state:'DRAFT',fields:{},field_metadata:{},warnings:[],utterances:[],confirmed:false,patient_id:null,can_undo:false};
      return response({session:serverSession});
    }
    if(/^\/api\/voice\/sessions\/[^/]+$/.test(path)&&options.method==='GET'){
      if(sessionGetStatus!==200)return {ok:false,status:sessionGetStatus,json:async()=>({message:'Synthetic restore failure'})};
      return response({session:serverSession});
    }
    const writeAction=path.split('/').at(-1);
    if(allowClinicalWrites&&['transcripts','confirm','submit'].includes(writeAction)&&options.method==='POST'){
      const identity=path+':'+call.body.client_request_id;
      if(committedWrites.has(identity))return response({...committedWrites.get(identity),duplicate:true});
      serverSession={...serverSession,version:serverSession.version+1,...(writeAction==='confirm'?{confirmed:true}:writeAction==='submit'?{state:'SUBMITTED'}:{})};
      const result={session:serverSession,...(writeAction==='submit'?{patient:{...existingPatient,version:2}}:{})};
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
  const context=vm.createContext({VoiceClient:TestClient,requestId:makeId,normalizeServer,VoiceCapture:FakeCapture,captureDescription,localAudioTestMetadata,readDraftHandoff,clearDraftHandoff,
    document:{getElementById:id=>{assert.ok(elements[id],`Unknown element ${id}`);return elements[id];},createElement,querySelectorAll:()=>[]},
    window:{addEventListener:(name,callback)=>{events[name]=callback;},history:{replaceState(state,title,value){historyReplacements.push(value);const next=new URL(value,location.origin);location.search=next.search;location.pathname=next.pathname;location.hash=next.hash;}}},
    sessionStorage:{getItem:key=>storage.has(key)?storage.get(key):null,setItem:(key,value)=>{if(key===storageWriteFailureKey)throw Error('Synthetic storage denied');storage.set(key,value);},removeItem:key=>storage.delete(key)},
    location,URLSearchParams,Option,Blob,Uint8Array,
    URL:{createObjectURL(blob){const url=`blob:fake-local-${++objectSequence}`;objects.set(url,blob);return url;},revokeObjectURL:url=>revoked.push(url)},
    atob:value=>Buffer.from(value,'base64').toString('binary'),
    setTimeout:callback=>timers.push(callback),setInterval:callback=>{intervals.add(callback);return callback;},clearInterval:callback=>intervals.delete(callback),console});
  vm.runInContext(source,context,{filename:'app.js'});await flush();
  const el=id=>elements[id];if(expectConnected)assert.equal(el('connection-status').textContent,'서버 연결됨');
  const click=async id=>{assert.equal(el(id).disabled,false,`${id} is enabled`);el(id).click();await flush();};
  const select=(id,value)=>{el(id).value=value;el(id).dispatch('change');};
  const local=()=>select('speech-provider','local_wav');
  return {el,capture:captures[0],calls,objects,revoked,downloads,events,intervals,click,select,local,storage,historyReplacements,location,
    runTimers:()=>{for(const timer of timers.splice(0))timer();}};
}

function assertNoAudioOrClinicalWrites(calls){
  assert.deepEqual(calls.filter(c=>c.method!=='GET').map(c=>c.path),calls.filter(c=>c.path==='/api/voice/sessions').map(c=>c.path));
  assert.ok(calls.every(c=>['/api/voice/capabilities','/api/voice/terms','/api/hospitals','/api/voice/sessions'].includes(c.path)));
  assert.equal(calls.filter(c=>/\/audio|\/transcripts|\/submit|\/patients/.test(c.path)).length,0);
}

test('local WAV stays available without Azure credentials and PCM controls follow the selected route',async()=>{
  const h=await harness();
  assert.equal(h.el('speech-provider').value,'browser');assert.equal(h.el('audio-input-profile').disabled,true);
  assert.equal(h.el('speech-provider').querySelector('[value="azure"]').disabled,true);
  assert.equal(h.el('speech-provider').querySelector('[value="local_wav"]').disabled,false);
  h.local();assert.equal(h.el('record-button').disabled,false);assert.equal(h.el('audio-input-profile').disabled,false);
  await h.click('record-button');assert.equal(h.capture.active,true);assert.equal(h.el('audio-input-profile').disabled,true);
  await h.click('record-button');assert.equal(h.capture.active,false);assert.equal(h.el('audio-input-profile').disabled,false);
  h.select('speech-provider','browser');assert.equal(h.el('audio-input-profile').disabled,true);
  assert.equal(h.calls.length,4);assertNoAudioOrClinicalWrites(h.calls);
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

test('without the explicit handoff query a stored reference is ignored and normal startup stays unchanged',async()=>{
  const storage=handoffStorage(),raw=storage.get(DRAFT_HANDOFF_KEY);
  const h=await harness({initialStorage:storage});
  assert.equal(h.calls.filter(call=>call.method==='POST').length,1);
  assert.equal(h.calls.some(call=>call.path==='/api/voice/sessions/VS-SYNTHETIC-HANDOFF'),false);
  assert.equal(storage.get(DRAFT_HANDOFF_KEY),raw);assert.equal(h.el('recommendation-prep').hidden,true);
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

test('a restored draft for an existing patient cannot fetch stale candidates until explicit review and submit',async()=>{
  const h=await harness({search:'?voice_handoff=1',initialStorage:handoffStorage(),restoredSession:handoffSession({patient_id:'P-SYNTHETIC-HANDOFF'}),allowClinicalWrites:true});
  assert.equal(h.calls.filter(call=>call.method!=='GET').length,0);
  assert.equal(h.calls.filter(call=>call.path==='/api/patients/P-SYNTHETIC-HANDOFF').length,1);
  assert.equal(h.calls.some(call=>call.path.endsWith('/candidates')),false);assert.equal(h.el('refresh-candidates').disabled,true);
  assert.equal(h.el('workflow-step').textContent,'02 정보 확인·수정');
  await h.click('confirm-button');assert.equal(h.calls.some(call=>call.path.endsWith('/candidates')),false);
  await h.click('submit-button');
  const submit=h.calls.find(call=>call.path.endsWith('/submit'));
  assert.equal(submit.body.start_inquiry,false);assert.equal(h.calls.filter(call=>call.path.endsWith('/candidates')).length,1);
  assert.equal(h.el('refresh-candidates').disabled,false);
});

test('lost submit response retry unlocks candidates only after the committed explicit submit is verified',async()=>{
  const h=await harness({search:'?voice_handoff=1',initialStorage:handoffStorage(),restoredSession:handoffSession({patient_id:'P-SYNTHETIC-HANDOFF'}),allowClinicalWrites:true,lostWriteResponses:{submit:1}});
  await h.click('confirm-button');await h.click('submit-button');
  assert.equal(h.el('retry-request').hidden,false);assert.equal(h.el('refresh-candidates').disabled,true);
  assert.equal(h.calls.some(call=>call.path.endsWith('/candidates')),false);
  await h.click('retry-request');
  const attempts=h.calls.filter(call=>call.path.endsWith('/submit'));
  assert.equal(attempts.length,2);assert.deepEqual(attempts[0].body,attempts[1].body);assert.equal(attempts[1].body.start_inquiry,false);
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

test('actual app local route returns paired WAV/settings downloads without any audio or clinical API write',async()=>{
  const h=await harness({browser:false});
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
  assert.equal(h.calls.length,4);assertNoAudioOrClinicalWrites(h.calls);
  h.runTimers();assert.ok(h.revoked.includes(exported.url));
});

test('recording again and starting a new case revoke prior WAV URLs and clear download metadata',async()=>{
  const h=await harness({browser:false});await h.click('record-button');await h.click('record-button');
  const first=h.el('audio-test-download').href;await h.click('record-button');
  assert.ok(h.revoked.includes(first));assert.equal(h.el('audio-test-download').hidden,true);assert.equal(h.el('audio-test-metadata').hidden,true);
  const before=h.downloads.length;h.el('audio-test-metadata').click();assert.equal(h.downloads.length,before,'cleared metadata cannot be downloaded');
  await h.click('record-button');const second=h.el('audio-test-download').href;assert.notEqual(second,first);
  await h.click('new-case');assert.ok(h.revoked.includes(second));
  assert.equal(h.el('audio-test-download').hidden,true);assert.equal(h.el('audio-test-metadata').hidden,true);
  h.el('audio-test-metadata').click();assert.equal(h.downloads.length,before);
  assert.equal(h.calls.filter(c=>c.method==='POST').length,2,'only the initial and explicitly requested new empty drafts');
  assertNoAudioOrClinicalWrites(h.calls);
});

test('pagehide clears saved URLs/metadata and releases an active local capture',async()=>{
  const h=await harness({browser:false});await h.click('record-button');await h.click('record-button');
  const old=h.el('audio-test-download').href;h.events.pagehide();
  assert.ok(h.revoked.includes(old));assert.equal(h.el('audio-test-download').hidden,true);assert.equal(h.el('audio-test-metadata').hidden,true);
  h.el('audio-test-metadata').click();assert.equal(h.downloads.length,0);
  await h.click('record-button');assert.equal(h.capture.active,true);h.events.pagehide();
  assert.equal(h.capture.active,false);assert.equal(h.intervals.size,0);
  assert.equal(h.calls.length,4);assertNoAudioOrClinicalWrites(h.calls);
});
