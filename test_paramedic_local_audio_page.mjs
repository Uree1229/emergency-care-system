import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import fs from 'node:fs';
import {setImmediate as nextTurn} from 'node:timers/promises';
import {VoiceClient,normalizeServer} from './paramedic-ui/voice-client.mjs';
import {captureDescription,localAudioTestMetadata} from './paramedic-ui/speech-test-context.mjs';
import {pcmInputDiagnostics,newSignalStats,collectSignalStats,signalQuality} from './paramedic-ui/audio-input-profile.mjs';
import {encodeWav} from './paramedic-ui/speech.mjs';

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

async function harness({browser=true}={}){
  const elements=Object.fromEntries(idEntries.map(m=>[m[2],new Element(m[1])]));
  for(const m of idEntries)if(/\shidden(?:\s|>)/.test(m[0]))elements[m[2]].hidden=true;
  class Option extends Element{constructor(text,value){super('option');this.textContent=text;this.value=value;}}
  for(const m of html.matchAll(/<select[^>]*\bid="([^"]+)"[^>]*>([\s\S]*?)<\/select>/g)){
    elements[m[1]].replaceChildren(...[...m[2].matchAll(/<option[^>]*\bvalue="([^"]*)"[^>]*>([^<]*)<\/option>/g)].map(o=>new Option(o[2],o[1])));
  }
  const calls=[],captures=[],objects=new Map(),revoked=[],downloads=[],events={},timers=[],intervals=new Set(),storage=new Map();
  let requestSequence=0,objectSequence=0,sessionSequence=0;
  const makeId=()=>`fake-request-${++requestSequence}`;
  const response=data=>({ok:true,status:200,json:async()=>data});
  const fetchImpl=async(url,options)=>{
    const path=new URL(url).pathname,call={path,method:options.method,body:options.body?JSON.parse(options.body):null};calls.push(call);
    if(path==='/api/voice/capabilities'&&options.method==='GET')return response({speech:{configured:false}});
    if(path==='/api/voice/terms'&&options.method==='GET')return response({supported_fields:[],readback_fields:[],terms:{}});
    if(path==='/api/hospitals'&&options.method==='GET')return response([]);
    if(path==='/api/voice/sessions'&&options.method==='POST')return response({session:{id:`VS-FAKE-LOCAL-${++sessionSequence}`,
      version:1,state:'DRAFT',fields:{},field_metadata:{},warnings:[],utterances:[],confirmed:false,patient_id:null,can_undo:false}});
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
  const context=vm.createContext({VoiceClient:TestClient,requestId:makeId,normalizeServer,VoiceCapture:FakeCapture,captureDescription,localAudioTestMetadata,
    document:{getElementById:id=>{assert.ok(elements[id],`Unknown element ${id}`);return elements[id];},createElement,querySelectorAll:()=>[]},
    window:{addEventListener:(name,callback)=>{events[name]=callback;}},
    sessionStorage:{getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value)},
    location:{search:'',origin:'http://127.0.0.1:18042'},URLSearchParams,Option,Blob,Uint8Array,
    URL:{createObjectURL(blob){const url=`blob:fake-local-${++objectSequence}`;objects.set(url,blob);return url;},revokeObjectURL:url=>revoked.push(url)},
    atob:value=>Buffer.from(value,'base64').toString('binary'),
    setTimeout:callback=>timers.push(callback),setInterval:callback=>{intervals.add(callback);return callback;},clearInterval:callback=>intervals.delete(callback),console});
  vm.runInContext(source,context,{filename:'app.js'});await flush();
  const el=id=>elements[id];assert.equal(el('connection-status').textContent,'서버 연결됨');
  const click=async id=>{assert.equal(el(id).disabled,false,`${id} is enabled`);el(id).click();await flush();};
  const select=(id,value)=>{el(id).value=value;el(id).dispatch('change');};
  const local=()=>select('speech-provider','local_wav');
  return {el,capture:captures[0],calls,objects,revoked,downloads,events,intervals,click,select,local,
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
