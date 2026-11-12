import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {webcrypto,createHash} from 'node:crypto';
import {setImmediate as nextTurn} from 'node:timers/promises';
import {createPracticeAttempt} from '../../paramedic-ui/command-practice-data.mjs';
import {describePracticeAudio} from '../../paramedic-ui/practice-audio.mjs';

// Actual page, capture/store modules and pinned RNNoise WASM. Browser audio
// decoding, MediaRecorder and IDB are synthetic; this does not measure STT.
const pageURL=new URL('../../paramedic-ui/noise-compare.js',import.meta.url),html=fs.readFileSync(new URL('../../paramedic-ui/noise-compare.html',import.meta.url),'utf8');
let source=fs.readFileSync(pageURL,'utf8');const bindings={};
for(const match of source.matchAll(/^import\s*\{([^}]+)\}\s*from\s*['"]([^'"]+)['"];?\r?$/gm)){
  const module=await import(new URL(match[2],pageURL));for(const name of match[1].split(',').map(value=>value.trim()))bindings[name]=module[name];source=source.replace(match[0],'');
}
assert.doesNotMatch(source,/^import /m);
const sha=bytes=>createHash('sha256').update(bytes).digest('hex'),copy=value=>JSON.parse(JSON.stringify(value));
const flush=async()=>{await nextTurn();await nextTurn();};
const deferred=()=>{let resolve;return {promise:new Promise(done=>{resolve=done;}),resolve:value=>resolve(value)};};
const wave=(length=1440)=>Float32Array.from({length},(_,i)=>Math.sin(i*.07)*.16+(i%13===0?.02:0));
class Element{
  constructor(tag='div',settings={}){this.tagName=tag.toUpperCase();this.children=[];this.value='';this.textContent='';this.disabled=false;this.hidden=false;this.checked=false;this.files=[];this.settings=settings;this.src='';this.loads=0;this.pauses=0;}
  append(...values){this.children.push(...values);}replaceChildren(...values){this.children=[...values];}
  removeAttribute(name){if(name==='src')this.src='';else delete this[name];}pause(){this.pauses++;}play(){throw Error('Automatic playback forbidden');}
  load(){this.loads++;if(this.src&&this.preload==='metadata'&&!this.settings.holdMetadata)queueMicrotask(()=>{if(!this.src)return;this.duration=this.settings.metadataDuration??.03;this.onloadedmetadata?.();});}
}
function harness(t,settings={}){
  const elements={},events={},downloads=[],objectURLs=new Map(),revoked=[],requests=[],decodeCalls=[],contexts=[],recorders=[],tracks=[],idbOperations=[],timers=new Set();let sequence=0;
  for(const tag of html.matchAll(/<([a-z][a-z0-9-]*)\b([^>]*\bid="([^"]+)"[^>]*)>/gi)){
    const element=new Element(tag[1],settings);elements[tag[3]]=element;for(const key of ['hidden','disabled','checked'])element[key]=new RegExp('\\s'+key+'(?:[\\s=>]|$)').test(tag[2]);
  }
  class BrowserURL extends URL{static createObjectURL(blob){const key='blob:noise-test-'+(++sequence);objectURLs.set(key,blob);return key;}static revokeObjectURL(key){revoked.push(key);objectURLs.delete(key);}}
  const document={getElementById:id=>{assert.ok(elements[id],'Actual DOM id '+id);return elements[id];},createElement:tag=>{const element=new Element(tag,settings);if(tag==='a')element.click=()=>{assert.ok(objectURLs.has(element.href));downloads.push({name:element.download,blob:objectURLs.get(element.href)});};return element;}};
  class AudioContext{
    constructor(options){this.sampleRate=options.sampleRate;this.closed=false;contexts.push(this);}
    async decodeAudioData(bytes){decodeCalls.push(Buffer.from(bytes));if(settings.decodeGate)await settings.decodeGate.promise;if(settings.decodeFails)throw Error('Synthetic decoder failure');const channels=settings.channels||[wave()];return {sampleRate:settings.decodedRate||48000,length:settings.decodedLength||channels[0].length,numberOfChannels:channels.length,getChannelData:i=>channels[i]};}
    async close(){this.closed=true;}
  }
  class OfflineAudioContext{
    constructor(channels,length,rate){this.length=length;this.sampleRate=rate;this.destination={};}
    createBuffer(channels,length,rate){const data=new Float32Array(length);return {sampleRate:rate,getChannelData:()=>data};}
    createBufferSource(){const owner=this;return {connect(){},start(){owner.input=this.buffer;}};}
    async startRendering(){const input=this.input.getChannelData(0),output=new Float32Array(this.length);for(let i=0;i<output.length;i++)output[i]=input[Math.min(input.length-1,Math.floor(i*input.length/output.length))];return {getChannelData:()=>output};}
  }
  class Recorder{
    static isTypeSupported(type){return type==='audio/webm;codecs=opus';}
    constructor(stream,options){this.stream=stream;this.mimeType=options.mimeType;this.state='inactive';recorders.push(this);}
    start(){this.state='recording';queueMicrotask(()=>this.onstart?.());}
    stop(){this.state='inactive';queueMicrotask(()=>{this.ondataavailable?.({data:new Blob(['one synthetic capture'],{type:this.mimeType})});this.onstop?.();});}
  }
  const navigator={mediaDevices:{async getUserMedia(constraints){requests.push({kind:'microphone',constraints});const track={stops:0,listeners:{},stop(){this.stops++;},addEventListener(name,handler){this.listeners[name]=handler;},end(){this.listeners.ended?.();}};tracks.push(track);return {getTracks:()=>[track]};}}};
  const indexedDB={open(){idbOperations.push({type:'open'});const request={result:{objectStoreNames:{contains:()=>true},transaction(name,mode){idbOperations.push({name,mode});assert.equal(mode,'readonly','Page must never write the practice database');const tx={objectStore(){return {getAll(){const reading={};queueMicrotask(()=>{reading.result=settings.rows||[];reading.onsuccess?.();queueMicrotask(()=>tx.oncomplete?.());});return reading;}};}};return tx;}}};queueMicrotask(()=>request.onsuccess?.());return request;}};
  const window={AudioContext,OfflineAudioContext,MediaRecorder:Recorder,navigator,Blob,setTimeout,clearTimeout,addEventListener:(name,handler)=>events[name]=handler};
  const old={};for(const [key,value]of Object.entries({window,indexedDB,fetch:async(...args)=>{requests.push({kind:'fetch',args});throw Error('All API/network calls forbidden');}})){old[key]=Object.getOwnPropertyDescriptor(globalThis,key);Object.defineProperty(globalThis,key,{value,writable:true,configurable:true});}
  const schedule=(fn,ms)=>{const timer=setTimeout(()=>{timers.delete(timer);fn();},ms);timers.add(timer);return timer;};
  const crypto={subtle:{async digest(...args){if(settings.digestGate)await settings.digestGate.promise;return webcrypto.subtle.digest(...args);}}};
  const context=vm.createContext({...bindings,document,window,navigator,indexedDB,crypto,Blob,URL:BrowserURL,TextEncoder,Uint8Array,Uint32Array,Float32Array,DataView,ArrayBuffer,AbortController,Date,Math,console,setTimeout:schedule,clearTimeout:timer=>{timers.delete(timer);clearTimeout(timer);}});
  vm.runInContext(source,context,{filename:'actual:noise-compare.js'});const read=expression=>vm.runInContext(expression,context),el=id=>elements['noise-'+id];
  const h={read,el,downloads,objectURLs,revoked,requests,decodeCalls,contexts,recorders,tracks,idbOperations,
    async click(id){assert.equal(el(id).disabled,false,'Enabled user button '+id);return await el(id).onclick();},
    async select(blob=new Blob(['synthetic encoded original'],{type:'audio/webm'})){return await read('selectInput')(blob);},
    async process(){return await h.click('process');},
    async event(name){await events[name]?.();await flush();},
    result:()=>read('result'),input:()=>read('input'),data:()=>copy(read('metadata()')),
  };
  t.after(async()=>{try{await h.event('pagehide');assert.equal(requests.filter(item=>item.kind==='fetch').length,0,'No UI or actual RNNoise module fetch');assert.equal(objectURLs.size,0,'All object URLs released');}finally{for(const timer of timers)clearTimeout(timer);for(const key of Object.keys(old)){if(old[key])Object.defineProperty(globalThis,key,old[key]);else delete globalThis[key];}}});return h;
}
function zipEntries(blobBytes){
  const bytes=new Uint8Array(blobBytes),view=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength),eocd=bytes.length-22;assert.equal(view.getUint32(eocd,true),0x06054b50);const count=view.getUint16(eocd+10,true);let offset=view.getUint32(eocd+16,true);const entries=new Map();
  for(let i=0;i<count;i++){assert.equal(view.getUint32(offset,true),0x02014b50);const size=view.getUint32(offset+24,true),length=view.getUint16(offset+28,true),local=view.getUint32(offset+42,true),name=new TextDecoder().decode(bytes.slice(offset+46,offset+46+length));assert.equal(view.getUint32(local,true),0x04034b50);const start=local+30+view.getUint16(local+26,true)+view.getUint16(local+28,true);assert.equal(entries.has(name),false);entries.set(name,bytes.slice(start,start+size));offset+=46+length+view.getUint16(offset+30,true)+view.getUint16(offset+32,true);}
  return entries;
}

test('initial page needs an explicit input; no mic, playback, IDB or API is started',t=>{const h=harness(t);assert.equal(h.el('process').disabled,true);assert.equal(h.el('duration-override').checked,false);assert.equal(h.el('results').hidden,true);assert.equal(h.requests.length,0);assert.equal(h.idbOperations.length,0);assert.equal(h.downloads.length,0);});
test('actual RNNoise produces paired equal-length WAVs from one decoded input and preserves original bytes',async t=>{
  const h=harness(t),blob=new Blob(['unaltered encoded bytes'],{type:'audio/webm'});Object.defineProperty(blob,'name',{value:'PRIVATE-PATIENT-NAME.webm'});await h.select(blob);assert.equal(h.downloads.length,0);assert.equal(await h.process(),true);
  const value=h.result(),meta=h.data();assert.equal(h.decodeCalls.length,1);assert.equal(meta.algorithm.engine,'RNNoise');assert.equal(meta.algorithm.inputSamples,1440);assert.equal(meta.algorithm.outputSamples,1440);assert.equal(meta.conversion.samples,1440);assert.equal(meta.conversion.originalEncodedSampleRate,null);assert.equal(meta.conversion.decoderMayResample,true);assert.equal(meta.sttAutomaticallyRun,false);
  assert.equal(value.rawWav.size,44+1440*2);assert.equal(value.processedWav.size,value.rawWav.size);assert.equal(meta.source.sha256,sha(Buffer.from('unaltered encoded bytes')));assert.equal(await h.input().blob.text(),'unaltered encoded bytes');assert.equal(JSON.stringify(meta).includes('PRIVATE-PATIENT'),false);assert.equal(h.contexts[0].closed,true);assert.equal(h.el('results').hidden,false);
});
test('known long input is rejected by metadata before decoder allocation; no silent crop',async t=>{const h=harness(t,{metadataDuration:181});await h.select();assert.equal(await h.process(),false);assert.equal(h.decodeCalls.length,0);assert.equal(h.result(),null);assert.match(h.el('error').textContent,/180초/);assert.ok(h.input());});
test('unknown duration requires explicit opt-in and records that uncertainty',async t=>{const h=harness(t,{metadataDuration:Infinity});await h.select();assert.equal(await h.process(),false);assert.equal(h.decodeCalls.length,0);assert.match(h.el('error').textContent,/길이/);h.el('duration-override').checked=true;assert.equal(await h.process(),true);assert.equal(h.data().durationPreflight.status,'unknown_explicitly_allowed');});
test('decoded duration is checked again when container metadata is misleading',async t=>{const h=harness(t,{decodedLength:48000*181});await h.select();assert.equal(await h.process(),false);assert.equal(h.decodeCalls.length,1);assert.equal(h.result(),null);assert.match(h.el('error').textContent,/180초/);});
test('stereo downmix and non-48k resampling feed the same shared PCM to both paths',async t=>{const left=Float32Array.from({length:441},()=>.3),right=Float32Array.from({length:441},()=>-.1),h=harness(t,{channels:[left,right],decodedRate:44100,metadataDuration:.01});await h.select();assert.equal(await h.process(),true);const data=h.data();assert.equal(data.conversion.decodedChannels,2);assert.equal(data.conversion.decodedSampleRate,44100);assert.equal(data.conversion.samples,480);assert.equal(data.algorithm.inputSamples,480);assert.equal(data.algorithm.outputSamples,480);assert.ok(Math.abs(data.metrics.raw.rms-.1)<1e-6);});
test('shared amplitude clipping is reported, not hidden as normalized input',async t=>{const h=harness(t,{channels:[Float32Array.from([1.4,-1.2,.1])],metadataDuration:3/48000});await h.select();assert.equal(await h.process(),true);const data=h.data();assert.equal(data.conversion.clippedInputSamples,2);assert.equal(data.metrics.raw.clippedSamples,2);assert.match(data.conversion.amplitudePolicy,/no_gain_normalization/);});
test('cancel during decode prevents stale processing output from being installed',async t=>{const decodeGate=deferred(),h=harness(t,{decodeGate});await h.select();const pending=h.process();await flush();assert.equal(h.decodeCalls.length,1);await h.click('cancel');decodeGate.resolve();assert.equal(await pending,false);assert.equal(h.result(),null);assert.equal(h.el('results').hidden,true);assert.ok(h.input());});
test('cancel reaches the actual RNNoise processor and cannot install its later output',async t=>{const h=harness(t,{channels:[wave(480*500)],metadataDuration:5});await h.select();const pending=h.process();for(let i=0;i<30&&!h.read('job?.processor');i++)await flush();assert.ok(h.read('job?.processor'),'Actual pinned processor initialized');await h.click('cancel');assert.equal(await pending,false);assert.equal(h.result(),null);assert.ok(h.input());assert.equal(h.el('results').hidden,true);});
test('replacement input wins over an older asynchronous decode',async t=>{const decodeGate=deferred(),h=harness(t,{decodeGate});await h.select(new Blob(['old source'],{type:'audio/webm'}));const pending=h.process();await flush();await h.select(new Blob(['new source'],{type:'audio/ogg'}));decodeGate.resolve();assert.equal(await pending,false);assert.equal(await h.input().blob.text(),'new source');assert.equal(h.result(),null);});
test('decoder errors retain original source and show a failure instead of fabricated output',async t=>{const h=harness(t,{decodeFails:true});await h.select();assert.equal(await h.process(),false);assert.ok(h.input());assert.equal(h.result(),null);assert.match(h.el('error').textContent,/decoder failure/);});
test('empty and oversized source bytes are rejected before decoding',async t=>{const h=harness(t);assert.equal(await h.select(new Blob([])),false);assert.equal(h.input(),null);assert.equal(await h.select(new Blob([new Uint8Array(20*1024*1024+1)],{type:'audio/webm'})),false);assert.equal(h.decodeCalls.length,0);assert.match(h.el('error').textContent,/20MiB/);});
test('manual comparison uses one independent reference and does not invoke ASR',async t=>{const h=harness(t);await h.select();await h.process();h.el('reference').value='합성 관찰. 병원 추천해줘';h.el('raw-transcript').value='합성 관찰. 종료';h.el('processed-transcript').value='합성 관찰. 병원 추천해줘';await h.click('compare-text');const data=h.data();assert.equal(data.annotations.comparison.referenceCommand.action,'recommend');assert.equal(data.annotations.comparison.raw.command.action,'stop');assert.equal(data.annotations.comparison.processed.exactNormalizedMatch,true);assert.equal(data.annotations.comparison.raw.exactNormalizedMatch,false);assert.match(h.el('text-result').textContent,/자동 STT/);assert.equal(h.requests.length,0);});
test('ZIP contains exact original bytes, both WAVs and independently hashable metadata',async t=>{
  const original=Buffer.from('original ZIP fixture'),h=harness(t);await h.select(new Blob([original],{type:'audio/webm'}));await h.process();h.el('pair').value='PAIR-01';h.el('speaker').value='S01';await h.click('zip');assert.equal(h.downloads.length,1);const entries=zipEntries(await h.downloads[0].blob.arrayBuffer());assert.deepEqual([...entries.keys()].sort(),['README.txt','comparison.json','manifest.json','original-source.webm','raw-48k-mono.wav','rnnoise-48k-mono.wav'].sort());assert.deepEqual(Buffer.from(entries.get('original-source.webm')),original);
  const manifest=JSON.parse(new TextDecoder().decode(entries.get('manifest.json'))),data=JSON.parse(new TextDecoder().decode(entries.get('comparison.json')));assert.equal(data.annotations.pairId,'PAIR-01');assert.equal(data.source.sha256,sha(original));for(const entry of manifest.files){assert.equal(entries.get(entry.path).length,entry.byteLength);assert.equal(sha(entries.get(entry.path)),entry.sha256);}assert.equal(manifest.files.length,5);assert.equal(data.audio.raw.sha256,sha(entries.get('raw-48k-mono.wav')));assert.equal(data.audio.processed.sha256,sha(entries.get('rnnoise-48k-mono.wav')));
});
test('JSON has metadata only and rejects identifying-shaped IDs before download',async t=>{const h=harness(t);await h.select();await h.process();h.el('speaker').value='실명 입력';assert.equal(await h.click('json'),false);assert.equal(h.downloads.length,0);h.el('speaker').value='S02';await h.click('json');const data=JSON.parse(await h.downloads[0].blob.text());assert.equal(data.sttAutomaticallyRun,false);assert.equal(Object.hasOwn(data,'blob'),false);assert.equal(Object.hasOwn(data.source,'filename'),false);assert.equal(Object.hasOwn(data.source,'attemptId'),false);});
test('practice source is verified/read-only and its private identifier is not exported',async t=>{
  const blob=new Blob(['stored capture'],{type:'audio/webm'}),id='PRIVATE-ORIGINAL-ATTEMPT',now=1793410000000,snapshot=createPracticeAttempt({id,source:'human_voice',trialNo:1,rawText:'관찰',fragments:['관찰'],status:'ok',referenceText:'원래 연습 참조문',timing:{startedAt:now-1000,endedAt:now}},{now}),descriptor={...await describePracticeAudio(id,blob,{crypto:webcrypto,startedAt:now-1000,endedAt:now}),savedAt:now};
  const h=harness(t,{rows:[{attemptId:id,blob,descriptor,snapshot}]});h.el('reference').value='사용자가 작성한 참조문';await h.click('practice-refresh');h.el('practice-select').value='1';h.el('practice-select').onchange();await h.click('practice-load');assert.equal(h.el('reference').value,'사용자가 작성한 참조문');assert.match(h.el('reference-status').textContent,/이전 공통 참조문/);await h.process();assert.equal(h.data().source.kind,'practice');assert.equal(JSON.stringify(h.data()).includes(id),false);assert.ok(h.idbOperations.some(operation=>operation.mode==='readonly'));
});
test('one explicit microphone capture becomes the sole shared A/B input',async t=>{const h=harness(t);assert.equal(h.requests.length,0);await h.click('record');assert.equal(h.requests.length,1);assert.deepEqual(h.requests[0].constraints,{audio:{echoCancellation:false,noiseSuppression:false,autoGainControl:false},video:false});await h.click('record-stop');assert.equal(h.recorders.length,1);assert.equal(h.tracks[0].stops,1);assert.equal(await h.input().blob.text(),'one synthetic capture');await h.process();assert.equal(h.data().source.kind,'microphone');assert.equal(h.decodeCalls.length,1);assert.equal(h.idbOperations.length,0);});
test('external track end automatically recovers partial bytes with warning and restores buttons',async t=>{const h=harness(t);await h.click('record');h.tracks[0].end();for(let i=0;i<100&&!h.input();i++)await flush();assert.ok(h.input(),'Terminated helper result was recovered');assert.equal(h.read('micBusy'),false);assert.equal(h.el('record').disabled,false);assert.equal(h.el('record-stop').disabled,true);assert.equal(h.input().warning,'AUDIO_TRACK_ENDED');assert.equal(await h.input().blob.text(),'one synthetic capture');assert.match(h.el('status').textContent,/먼저 종료/);assert.equal(h.recorders.length,1);assert.equal(h.requests.length,1);await h.process();assert.equal(h.data().source.warning,'AUDIO_TRACK_ENDED');});
test('recorder error recovers exact partial bytes once; stale owner cannot replace a new capture',async t=>{const h=harness(t);await h.click('record');h.recorders[0].onerror();for(let i=0;i<100&&!h.input();i++)await flush();assert.ok(h.input());assert.equal(h.input().warning,'AUDIO_RECORDER_ERROR');assert.equal(await h.input().blob.text(),'one synthetic capture');assert.equal(h.read('micBusy'),false);assert.equal(h.el('record').disabled,false);const oldOwner=h.read('micOwner');await h.click('record');assert.notEqual(h.read('micOwner'),oldOwner);assert.equal(await h.read('selectInput')(new Blob(['stale capture'],{type:'audio/webm'}),{kind:'microphone',expectedMicOwner:oldOwner}),false);assert.equal(h.input(),null);assert.equal(h.read('micBusy'),true);await h.click('record-stop');assert.equal(h.input().warning,'');assert.equal(await h.input().blob.text(),'one synthetic capture');assert.equal(h.recorders.length,2);assert.equal(h.requests.length,2);});
test('pagehide cancels decode and clears object URLs without applying late results',async t=>{const decodeGate=deferred(),h=harness(t,{decodeGate});await h.select();const pending=h.process();await flush();await h.event('pagehide');decodeGate.resolve();assert.equal(await pending,false);assert.equal(h.result(),null);assert.equal(h.objectURLs.size,0);});
test('BFCache restore rebuilds retained players without recording, processing, playback or export',async t=>{
  const h=harness(t);await h.select();await h.process();const result=h.result(),oldURLs=[...h.objectURLs.keys()];
  await h.event('pagehide');assert.equal(h.objectURLs.size,0);assert.equal(h.el('original-player').src,'');
  await h.event('pageshow');assert.equal(h.read('closed'),false);assert.equal(h.result(),result);assert.equal(h.objectURLs.size,3);
  for(const id of ['original-player','raw-player','processed-player'])assert.ok(h.objectURLs.has(h.el(id).src));
  assert.ok(oldURLs.every(url=>!h.objectURLs.has(url)));assert.equal(h.decodeCalls.length,1);assert.equal(h.requests.length,0);assert.equal(h.idbOperations.length,0);assert.equal(h.downloads.length,0);
  assert.equal(h.el('process').disabled,false);assert.equal(h.el('record').disabled,false);
  assert.equal(await h.select(new Blob(['new input after Back'],{type:'audio/webm'})),true);assert.equal(await h.process(),true);assert.equal(h.result().input.sha256,sha(Buffer.from('new input after Back')));
});
test('a pre-navigation decoder cannot overwrite a restored page with a new selection',async t=>{
  const decodeGate=deferred(),settings={decodeGate},h=harness(t,settings);await h.select(new Blob(['old audio'],{type:'audio/webm'}));const pending=h.process();await flush();
  await h.event('pagehide');await h.event('pageshow');settings.decodeGate=null;
  await h.select(new Blob(['restored page audio'],{type:'audio/ogg'}));assert.equal(await h.process(),true);const result=h.result(),message=h.el('status').textContent;
  decodeGate.resolve();assert.equal(await pending,false);assert.equal(h.result(),result);assert.equal(h.el('status').textContent,message);assert.equal(h.result().input.sha256,sha(Buffer.from('restored page audio')));
});
test('an export awaiting hashes before navigation cannot download after BFCache restore',async t=>{
  const settings={},h=harness(t,settings);await h.select();await h.process();settings.digestGate=deferred();
  const pending=h.click('zip');await flush();assert.equal(h.read('exporting'),true);
  await h.event('pagehide');await h.event('pageshow');assert.equal(h.read('exporting'),false);
  settings.digestGate.resolve();settings.digestGate=null;assert.equal(await pending,false);assert.equal(h.downloads.length,0);
  assert.equal(await h.click('zip'),true);assert.equal(h.downloads.length,1);
});
test('navigation ends a microphone capture and restore requires a new explicit start',async t=>{
  const h=harness(t);await h.click('record');assert.equal(h.requests.length,1);
  await h.event('pagehide');await h.event('pageshow');assert.equal(h.read('micBusy'),false);assert.equal(h.read('audioCapture.active'),false);assert.equal(h.tracks[0].stops,1);
  assert.equal(h.input(),null);assert.equal(h.requests.length,1);assert.equal(h.downloads.length,0);assert.equal(h.el('record').disabled,false);assert.equal(h.el('record-stop').disabled,true);
  await h.click('record');assert.equal(h.requests.length,2);await h.click('record-stop');assert.ok(h.input());
});
