import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto,createHash} from 'node:crypto';
import {setImmediate as tick} from 'node:timers/promises';
import {PracticeAudioCapture,makePracticeAudioStore,describePracticeAudio,PRACTICE_AUDIO_DB,PRACTICE_AUDIO_STORE,PRACTICE_AUDIO_LIMITS,PRACTICE_AUDIO_CONSTRAINTS} from './paramedic-ui/practice-audio.mjs';
import {createPracticeAttempt} from './paramedic-ui/command-practice-data.mjs';

const NOW=1793410000000;
const copy=value=>structuredClone(value);
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
const snapshot=(id='P-01',extra={})=>createPracticeAttempt({id,source:'human_voice',trialNo:1,rawText:'종료',fragments:['종료'],status:'ok',timing:{startedAt:NOW-2000,endedAt:NOW},...extra},{now:NOW});
const audio=(text='unaltered browser bytes',type='audio/webm;codecs=opus')=>new Blob([text],{type});
const rejects=(promise,code)=>assert.rejects(promise,cause=>cause.code===code);
function captureFixture(options={}){
  const instances=[],tracks=[],timers=new Map(),requests=[];let time=NOW,nextTimer=0;
  const track=()=>{const value={stops:0,listeners:{},stop(){this.stops++;},addEventListener(name,callback){this.listeners[name]=callback;},end(){this.listeners.ended?.();}};tracks.push(value);return value;};
  const stream=()=>{const owned=[track()];return {getTracks:()=>owned,getAudioTracks:()=>owned};};
  class Recorder{
    static isTypeSupported(type){return options.types?options.types.includes(type):type==='audio/webm;codecs=opus';}
    constructor(input,settings){if(options.constructThrows)throw Error();this.stream=input;this.requested=settings;this.mimeType=options.actualMime??settings.mimeType??'';this.state='inactive';this.stops=0;instances.push(this);}
    start(timeslice){if(options.startThrows)throw Error();this.timeslice=timeslice;this.state='recording';if(!options.startPending)queueMicrotask(()=>this.onstart?.());}
    stop(){this.stops++;if(options.stopThrows)throw Error();this.state='inactive';if(options.stopNever)return;queueMicrotask(()=>{for(const blob of options.finalChunks||[])this.ondataavailable?.({data:blob});this.onstop?.();});}
    data(blob){this.ondataavailable?.({data:blob});}
    fail(){this.onerror?.({error:new Error('failure')});}
    end(){this.state='inactive';this.onstop?.();}
  }
  const media={getUserMedia(constraints){requests.push(copy(constraints));if(options.permission)return options.permission.promise;if(options.permissionError)return Promise.reject(options.permissionError);return Promise.resolve(stream());}};
  const env={MediaRecorder:Recorder,navigator:{mediaDevices:media},Blob,setTimeout(fn,ms){const key=++nextTimer;timers.set(key,{fn,ms});return key;},clearTimeout(key){timers.delete(key);}};
  const states=[],limits=[],capture=new PracticeAudioCapture({env,now:()=>time,onState:(...args)=>states.push(args),onLimit:(...args)=>limits.push(args)});
  return {capture,env,instances,tracks,timers,requests,states,limits,stream,setTime:value=>{time=value;},fire(ms){for(const[key,timer]of [...timers])if(timer.ms===ms){timers.delete(key);timer.fn();}},async settle(){await tick();}};
}

/** Isolated fake IDB: serial transactions, queued requests and atomic rollback. */
function fakeIDB(){
  const control={rows:new Map(),transactions:[],opens:[],created:[],queue:[],active:null,exists:false,failOpen:false,blocked:false,failWrite:false,failCommit:false,ignoreWrite:false,failRead:false,failDelete:false,readTransform:null,closes:0};
  function dispatch(){if(control.active||!control.queue.length)return;const tx=control.queue.shift();control.active=tx;tx.active=true;tx.working=new Map([...control.rows].map(([key,value])=>[key,copy(value)]));for(const op of tx.ops.splice(0))tx.run(op);tx.check();}
  function transaction(storeName,mode){
    assert.equal(storeName,PRACTICE_AUDIO_STORE);const tx={mode,active:false,finished:false,pending:0,ops:[],working:null,completion:false,
      abort(){if(this.finished)return;this.finished=true;queueMicrotask(()=>{this.onabort?.();control.active=null;dispatch();});},
      check(){if(!this.active||this.finished||this.pending||this.completion)return;this.completion=true;setImmediate(()=>{this.completion=false;if(this.finished||this.pending)return;if(control.failCommit&&mode==='readwrite'){this.abort();return;}this.finished=true;if(mode==='readwrite'&&!control.ignoreWrite)control.rows=this.working;this.oncomplete?.();control.active=null;dispatch();});},
      run(op){queueMicrotask(()=>{if(this.finished)return;try{if(control.failRead&&['get','getAll'].includes(op.kind))throw Error('ReadError');let result;
        if(op.kind==='get')result=this.working.get(op.key);else if(op.kind==='getAll')result=[...this.working.values()];else if(op.kind==='add'){if(control.failWrite)throw Error('QuotaExceededError');if(this.working.has(op.value.attemptId))throw Error('ConstraintError');this.working.set(op.value.attemptId,copy(op.value));result=op.value.attemptId;}
        else if(op.kind==='delete'){if(control.failDelete)throw Error('DeleteError');this.working.delete(op.key);}
        if(control.readTransform&&['get','getAll'].includes(op.kind))result=control.readTransform(op.kind,op.key,result,mode);op.request.result=copy(result);op.request.onsuccess?.();
      }catch(cause){op.request.error=cause;op.request.onerror?.();this.abort();}finally{this.pending--;this.check();}});},
      enqueue(kind,key,value){if(this.finished)throw Error('TransactionInactiveError');const request={result:undefined,error:null};this.pending++;const op={kind,key,value,request};if(this.active)this.run(op);else this.ops.push(op);return request;},
      objectStore(name){assert.equal(name,PRACTICE_AUDIO_STORE);return {get:key=>this.enqueue('get',key),getAll:()=>this.enqueue('getAll'),add:value=>this.enqueue('add',null,value),delete:key=>this.enqueue('delete',key)};}};
    control.transactions.push(tx);control.queue.push(tx);queueMicrotask(dispatch);return tx;
  }
  const db={objectStoreNames:{contains:()=>control.exists},createObjectStore(name,settings){control.exists=true;control.created.push({name,settings});},transaction,close(){control.closes++;}};
  const indexedDB={open(name,version){control.opens.push({name,version});const request={result:db};queueMicrotask(()=>{if(control.failOpen){request.onerror?.();return;}if(control.blocked){request.onblocked?.();request.onsuccess?.();return;}if(!control.exists)request.onupgradeneeded?.();request.onsuccess?.();});return request;}};
  const store=makePracticeAudioStore({indexedDB,crypto:webcrypto,now:()=>NOW});return {indexedDB,store,control,db};
}

test('local audio limits, database and requested capture constraints are explicit',()=>{
  assert.equal(PRACTICE_AUDIO_DB,'paramedic-command-practice-audio-v1');assert.equal(PRACTICE_AUDIO_STORE,'recordings');assert.deepEqual(PRACTICE_AUDIO_LIMITS,{durationMs:180000,trialBytes:20971520,totalBytes:157286400,stopTimeoutMs:5000});assert.deepEqual(PRACTICE_AUDIO_CONSTRAINTS,{echoCancellation:false,noiseSuppression:false,autoGainControl:false});
});
test('capture waits for MediaRecorder start, requests separate no-DSP audio and returns exact unmodified bytes',async()=>{
  const f=captureFixture({finalChunks:[audio(' final')]}),start=await f.capture.start('P-01');assert.equal(start.status,'recording');assert.equal(f.capture.active,true);assert.equal(f.capture.state,'recording');assert.equal(f.instances[0].timeslice,250);assert.deepEqual(f.requests,[{audio:{echoCancellation:false,noiseSuppression:false,autoGainControl:false},video:false}]);
  f.instances[0].data(audio('first'));f.setTime(NOW+2500);const result=await f.capture.stop({reason:'button'});assert.equal(result.status,'ready');assert.equal(result.contentType,'audio/webm;codecs=opus');assert.equal(result.extension,'webm');assert.equal(await result.blob.text(),'first final');assert.equal(result.byteLength,11);assert.equal(result.startedAt,NOW);assert.equal(result.endedAt,NOW+2500);assert.equal(result.reason,'button');assert.equal(f.tracks[0].stops,1);assert.equal(f.capture.active,false);assert.equal(f.timers.size,0);
});
test('stop is idempotent while final chunks arrive and never stops a replacement session',async()=>{
  const f=captureFixture({finalChunks:[audio('end')]});await f.capture.start('P-01');const left=f.capture.stop({reason:'button'}),right=f.capture.stop({reason:'aborted'});assert.equal(left,right);const a=await left;assert.equal(a.reason,'button');assert.equal(f.instances[0].stops,1);assert.equal(await f.capture.stop(),a);await f.capture.start('P-02');f.instances[0].onstop?.();assert.equal(f.capture.active,true);await f.capture.abort();
});
test('graceful abort preserves partial audio rather than silently discarding the recorded attempt',async()=>{
  const f=captureFixture({finalChunks:[audio('last')]});await f.capture.start('P-01');f.instances[0].data(audio('partial-'));const result=await f.capture.abort();assert.equal(result.status,'ready');assert.equal(result.reason,'aborted');assert.equal(await result.blob.text(),'partial-last');assert.equal(f.tracks[0].stops,1);
});
test('permission cancellation settles immediately and every late stream is stopped without launching a recorder',async()=>{
  const permission=deferred(),f=captureFixture({permission}),starting=f.capture.start('P-old');assert.equal(f.capture.active,true);const stopped=await f.capture.stop({reason:'aborted'});assert.equal(stopped.status,'aborted');assert.equal((await starting).status,'failed');assert.equal(f.capture.active,false);const stream=f.stream();permission.resolve(stream);await f.settle();assert.equal(f.tracks[0].stops,1);assert.equal(f.instances.length,0);
});
test('late permission from cancelled generation cannot capture or stop the new attempt',async()=>{
  const permission=deferred(),f=captureFixture({permission}),old=f.capture.start('P-old');f.capture.cancel();assert.equal((await old).errorCode,'AUDIO_CANCELLED');f.env.navigator.mediaDevices.getUserMedia=async()=>f.stream();assert.equal((await f.capture.start('P-new')).status,'recording');const oldStream=f.stream();permission.resolve(oldStream);await f.settle();assert.equal(f.tracks[1].stops,1);assert.equal(f.tracks[0].stops,0);assert.equal(f.capture.active,true);f.instances[0].data(audio('new'));assert.equal((await f.capture.stop()).attemptId,'P-new');
});
test('cancel immediately releases active tracks and ignores queued recorder events',async()=>{
  const f=captureFixture({stopNever:true});await f.capture.start('P-01');const recorder=f.instances[0],late=recorder.ondataavailable;recorder.data(audio('discard'));const result=f.capture.cancel();assert.equal(result.status,'aborted');assert.equal(result.blob,null);assert.equal(f.capture.active,false);assert.equal(f.tracks[0].stops,1);late({data:audio('late')});assert.equal((await f.capture.stop()).status,'aborted');
});
test('a recorder that never announces start times out without leaving a permission-owned mic active',async()=>{
  const f=captureFixture({startPending:true}),starting=f.capture.start('P-01');await f.settle();f.fire(5000);const result=await starting;assert.equal(result.status,'failed');assert.equal(result.errorCode,'AUDIO_START_TIMEOUT');assert.equal(f.tracks[0].stops,1);assert.equal(f.capture.active,false);assert.equal(f.timers.size,0);
});
test('timer methods retain their browser environment receiver',async()=>{
  const f=captureFixture({finalChunks:[audio('data')]}),set=f.env.setTimeout,clear=f.env.clearTimeout;f.env.setTimeout=function(...args){assert.equal(this,f.env);return set(...args);};f.env.clearTimeout=function(...args){assert.equal(this,f.env);return clear(...args);};await f.capture.start('P-01');assert.equal((await f.capture.stop()).status,'ready');
});
for(const unavailable of ['recorder','media','codec'])test(`unsupported ${unavailable} returns unavailable without requesting a microphone`,async()=>{
  const f=captureFixture(unavailable==='codec'?{types:[]}:{});if(unavailable==='recorder')f.env.MediaRecorder=null;if(unavailable==='media')f.env.navigator.mediaDevices=null;const value=await f.capture.start('P-01');assert.equal(value.status,'unavailable');assert.equal(f.requests.length,0);assert.equal(f.capture.active,false);
});
for(const [name,code]of [['NotAllowedError','AUDIO_PERMISSION_DENIED'],['NotFoundError','AUDIO_DEVICE_UNAVAILABLE'],['NotReadableError','AUDIO_START_FAILED']])test(`permission/device failure ${name} is explicit and never claims a file exists`,async()=>{
  const cause=new Error();cause.name=name;const f=captureFixture({permissionError:cause}),value=await f.capture.start('P-01');assert.equal(value.status,'failed');assert.equal(value.errorCode,code);const last=await f.capture.stop();assert.equal(last.blob,null);assert.equal(last.byteLength,0);assert.equal(f.capture.active,false);
});
for(const option of ['constructThrows','startThrows'])test(`${option} releases the acquired track and settles failed start`,async()=>{
  const f=captureFixture({[option]:true});assert.equal((await f.capture.start('P-01')).status,'failed');assert.equal(f.tracks[0].stops,1);assert.equal(f.capture.active,false);
});
test('a second start cannot overwrite an in-progress attempt or request another stream',async()=>{
  const f=captureFixture();await f.capture.start('P-01');const second=await f.capture.start('P-02');assert.equal(second.errorCode,'AUDIO_ALREADY_ACTIVE');assert.equal(f.requests.length,1);f.instances[0].data(audio('first'));assert.equal((await f.capture.stop()).attemptId,'P-01');
});
test('empty chunks produce empty, with no fake ready or saved state',async()=>{
  const f=captureFixture({finalChunks:[new Blob([],{type:'audio/webm'})]});await f.capture.start('P-01');const result=await f.capture.stop();assert.equal(result.status,'empty');assert.equal(result.blob,null);assert.equal(result.byteLength,0);
});
test('natural MediaRecorder stop also flushes and releases without a button',async()=>{
  const f=captureFixture();await f.capture.start('P-01');f.instances[0].data(audio('native'));f.instances[0].end();assert.equal(f.capture.active,false);const result=await f.capture.stop();assert.equal(result.status,'ready');assert.equal(await result.blob.text(),'native');assert.equal(result.reason,'natural');assert.equal(f.tracks[0].stops,1);
});
test('180-second limit calls the sandbox limit callback once and retains final bytes',async()=>{
  const f=captureFixture({finalChunks:[audio('bounded')]});await f.capture.start('P-01');f.setTime(NOW+180000);f.fire(180000);const result=await f.capture.stop();assert.equal(result.status,'ready');assert.equal(result.reason,'time_limit');assert.equal(f.limits.length,1);assert.equal(f.limits[0][0],'time_limit');assert.equal(await result.blob.text(),'bounded');assert.equal(f.timers.size,0);
});
test('byte-limit overflow rejects rather than exporting silently truncated recording bytes',async()=>{
  const f=captureFixture();await f.capture.start('P-01');f.instances[0].data(new Blob([new Uint8Array(PRACTICE_AUDIO_LIMITS.trialBytes+1)],{type:'audio/webm'}));const result=await f.capture.stop();assert.equal(result.status,'failed');assert.equal(result.errorCode,'AUDIO_BYTE_LIMIT');assert.equal(result.blob,null);assert.equal(result.byteLength,0);assert.equal(f.limits[0][0],'byte_limit');
});
test('exact byte boundary is accepted as an unmodified Blob',async()=>{
  const f=captureFixture();await f.capture.start('P-01');const blob=new Blob([new Uint8Array(PRACTICE_AUDIO_LIMITS.trialBytes)],{type:'audio/webm'});f.instances[0].data(blob);const result=await f.capture.stop();assert.equal(result.status,'ready');assert.equal(result.byteLength,PRACTICE_AUDIO_LIMITS.trialBytes);
});
test('missing stop event times out, frees the mic and never claims a successful file',async()=>{
  const f=captureFixture({stopNever:true});await f.capture.start('P-01');f.instances[0].data(audio('partial'));const stopping=f.capture.stop();f.fire(5000);const result=await stopping;assert.equal(result.status,'failed');assert.equal(result.errorCode,'AUDIO_STOP_TIMEOUT');assert.equal(f.tracks[0].stops,1);assert.equal(f.capture.active,false);assert.equal(f.timers.size,0);
});
test('stop exception settles failed and releases microphone',async()=>{
  const f=captureFixture({stopThrows:true});await f.capture.start('P-01');const result=await f.capture.stop();assert.equal(result.status,'failed');assert.equal(result.errorCode,'AUDIO_STOP_FAILED');assert.equal(f.tracks[0].stops,1);
});
test('recorder failure and externally ended track are separate from successful finalization',async()=>{
  const f=captureFixture();await f.capture.start('P-01');f.instances[0].data(audio('partial'));f.instances[0].fail();assert.equal((await f.capture.stop()).errorCode,'AUDIO_RECORDER_ERROR');
  await f.capture.start('P-02');f.tracks[1].end();const result=await f.capture.stop();assert.equal(result.status,'failed');assert.equal(result.errorCode,'AUDIO_TRACK_ENDED');
});
test('mixed container chunks cannot be mislabeled as the requested MIME',async()=>{
  const f=captureFixture();await f.capture.start('P-01');f.instances[0].data(audio('ogg','audio/ogg'));const result=await f.capture.stop();assert.equal(result.status,'failed');assert.equal(result.errorCode,'AUDIO_MIME_MISMATCH');
});
test('invalid chunk objects and unknown actual MIME cannot become fabricated ready audio',async()=>{
  const f=captureFixture();await f.capture.start('P-01');f.instances[0].data({size:1,type:'audio/webm'});assert.equal((await f.capture.stop()).errorCode,'AUDIO_INVALID_CHUNK');
  const g=captureFixture({actualMime:'',finalChunks:[new Blob(['unknown'])]});delete g.env.MediaRecorder.isTypeSupported;await g.capture.start('P-02');const result=await g.capture.stop();assert.equal(result.status,'failed');assert.equal(result.errorCode,'AUDIO_FILE_INVALID');assert.equal(result.blob,null);
});
for(const [type,extension]of [['audio/ogg;codecs=opus','ogg'],['audio/mp4;codecs=mp4a.40.2','mp4']])test(`${type} capture uses the matching actual extension`,async()=>{
  const f=captureFixture({types:[type],finalChunks:[audio('bytes',type)]});await f.capture.start('P-01');const result=await f.capture.stop();assert.equal(result.contentType,type);assert.equal(result.extension,extension);assert.equal(result.blob.type,type);
});
test('no static MIME support falls back only to the actual recorder/chunk type, not an invented webm label',async()=>{
  const f=captureFixture({actualMime:'audio/ogg',finalChunks:[audio('ogg','audio/ogg')]});delete f.env.MediaRecorder.isTypeSupported;assert.equal((await f.capture.start('P-01')).status,'recording');const result=await f.capture.stop();assert.equal(result.contentType,'audio/ogg');assert.equal(result.extension,'ogg');
});
test('unsafe IDs are rejected before recording or permission prompts',()=>{
  const f=captureFixture();for(const value of ['',null,'../other','P with space'])assert.throws(()=>f.capture.start(value),cause=>cause.code==='INVALID_PRACTICE_AUDIO');assert.equal(f.requests.length,0);
});
test('throwing observer callbacks cannot leak tracks or change audio results',async()=>{
  const f=captureFixture({finalChunks:[audio('data')]});f.capture.onState=()=>{throw Error('UI');};f.capture.onLimit=()=>{throw Error('UI');};assert.equal((await f.capture.start('P-01')).status,'recording');f.fire(180000);assert.equal((await f.capture.stop()).status,'ready');assert.equal(f.tracks[0].stops,1);
});
test('cancellation inside requesting observer cannot open a microphone after the attempt was cancelled',async()=>{
  const f=captureFixture();f.capture.onState=state=>{if(state==='requesting')f.capture.cancel();};assert.equal((await f.capture.start('P-01')).status,'failed');assert.equal(f.requests.length,0);assert.equal(f.capture.active,false);assert.equal(f.timers.size,0);
});
test('cancellation inside recording observer cannot leave a late duration timer or resolve a false recording start',async()=>{
  const f=captureFixture();f.capture.onState=state=>{if(state==='recording')f.capture.cancel();};assert.equal((await f.capture.start('P-01')).status,'failed');assert.equal(f.capture.active,false);assert.equal(f.tracks[0].stops,1);assert.equal(f.timers.size,0);
});
test('cancellation inside stopping observer cannot schedule an orphan stop timeout or touch a new generation',async()=>{
  const f=captureFixture();await f.capture.start('P-01');f.capture.onState=state=>{if(state==='stopping')f.capture.cancel();};assert.equal((await f.capture.stop()).status,'aborted');assert.equal(f.capture.active,false);assert.equal(f.timers.size,0);assert.equal(f.tracks[0].stops,1);
});

test('memory descriptor matches independent SHA-256 and contains no claim of durable storage',async()=>{
  const result=await describePracticeAudio('P-01',audio('abc'),{startedAt:NOW,endedAt:NOW+1000,crypto:webcrypto});assert.deepEqual(result,{attemptId:'P-01',contentType:'audio/webm;codecs=opus',extension:'webm',byteLength:3,sha256:createHash('sha256').update('abc').digest('hex'),startedAt:NOW,endedAt:NOW+1000,savedAt:null,captureErrorCode:''});
});
test('unknown capture time stays null and is never replaced with STT or current time',async()=>{
  const result=await describePracticeAudio('P-01',audio(),{crypto:webcrypto});assert.equal(result.startedAt,null);assert.equal(result.endedAt,null);assert.equal(result.savedAt,null);
});
test('memory descriptor retains an incomplete-capture warning without changing or legitimizing the audio bytes',async()=>{
  const blob=audio('partial'),value=await describePracticeAudio('P-01',blob,{captureErrorCode:'AUDIO_STOP_TIMEOUT',crypto:webcrypto});assert.equal(value.captureErrorCode,'AUDIO_STOP_TIMEOUT');assert.equal(value.savedAt,null);assert.equal(value.sha256,createHash('sha256').update('partial').digest('hex'));assert.equal(await blob.text(),'partial');
});
test('capture warning accepts only bounded anonymous error codes',async()=>{
  for(const captureErrorCode of ['private patient text','x'.repeat(81),null,3,'<script>'])await rejects(describePracticeAudio('P-01',audio(),{captureErrorCode,crypto:webcrypto}),'INVALID_PRACTICE_AUDIO');
});
for(const type of ['text/plain','video/webm','audio/wav','audio/mpeg','application/octet-stream','audio/mp4;evil=raw'])test(`unsupported file type ${type} cannot acquire a misleading audio descriptor`,async()=>{
  await rejects(describePracticeAudio('P-01',audio('x',type),{crypto:webcrypto}),'INVALID_PRACTICE_AUDIO');
});
test('empty files, invalid time pairs and missing digest cannot be reported available',async()=>{
  await rejects(describePracticeAudio('P-01',new Blob([],{type:'audio/webm'}),{crypto:webcrypto}),'INVALID_PRACTICE_AUDIO');
  await rejects(describePracticeAudio('P-01',audio(),{startedAt:NOW,endedAt:null,crypto:webcrypto}),'INVALID_PRACTICE_AUDIO');await rejects(describePracticeAudio('P-01',audio(),{startedAt:NOW,endedAt:NOW-1,crypto:webcrypto}),'INVALID_PRACTICE_AUDIO');await rejects(describePracticeAudio('P-01',audio(),{crypto:null}),'AUDIO_HASH_UNAVAILABLE');
});
test('hash rejection and invalid digest length are explicit and leave the original Blob intact',async()=>{
  const blob=audio('original');for(const crypto of [{subtle:{digest:async()=>{throw Error();}}},{subtle:{digest:async()=>new ArrayBuffer(2)}}])await rejects(describePracticeAudio('P-01',blob,{crypto}),'AUDIO_HASH_FAILED');assert.equal(await blob.text(),'original');
});

test('durable put stores Blob and original metadata atomically and verifies full bytes on readback',async()=>{
  const f=fakeIDB(),blob=audio('durable'),original=snapshot(),before=copy(original);const info=await f.store.put('P-01',blob,original,{startedAt:NOW-3000,endedAt:NOW-500});assert.equal(info.sha256,createHash('sha256').update('durable').digest('hex'));assert.equal(info.savedAt,NOW);assert.equal(info.startedAt,NOW-3000);assert.equal(info.endedAt,NOW-500);const row=await f.store.get('P-01');assert.equal(await row.blob.text(),'durable');assert.deepEqual(row.snapshot,before);assert.deepEqual(row.descriptor,info);assert.equal(row.snapshot.timing.startedAt,NOW-2000);assert.deepEqual(original,before);assert.deepEqual(f.control.created,[{name:'recordings',settings:{keyPath:'attemptId'}}]);assert.deepEqual(f.control.opens,[{name:PRACTICE_AUDIO_DB,version:1}]);assert.ok(f.control.transactions.some(tx=>tx.mode==='readwrite'));
});
test('read return snapshots are detached and cannot rewrite stored original metadata',async()=>{
  const f=fakeIDB();await f.store.put('P-01',audio(),snapshot());const row=await f.store.get('P-01');row.snapshot.rawText='modified';row.descriptor.sha256='0'.repeat(64);row.snapshot.fragments.push('extra');const fresh=await f.store.get('P-01');assert.equal(fresh.snapshot.rawText,'종료');assert.deepEqual(fresh.snapshot.fragments,['종료']);assert.notEqual(fresh.descriptor.sha256,'0'.repeat(64));
});
test('same immutable file and metadata retry is idempotent and preserves original savedAt',async()=>{
  const f=fakeIDB(),first=await f.store.put('P-01',audio(),snapshot());const second=await f.store.put('P-01',audio(),snapshot());assert.deepEqual(second,first);assert.equal(f.control.rows.size,1);
});
test('partial-file warning survives a newly constructed store, get/list and later metadata recovery',async()=>{
  const f=fakeIDB(),original=snapshot();const first=await f.store.put('P-01',audio('partial'),original,{startedAt:NOW-1000,endedAt:NOW,captureErrorCode:'AUDIO_RECORDER_ERROR'});const reloaded=makePracticeAudioStore({indexedDB:f.indexedDB,crypto:webcrypto,now:()=>NOW+5000});const row=await reloaded.get('P-01');assert.equal(row.descriptor.captureErrorCode,'AUDIO_RECORDER_ERROR');assert.deepEqual(row.snapshot,original);assert.equal(row.snapshot.errorCode,'');assert.equal(row.snapshot.actualAudioSaved,false);assert.equal((await reloaded.list())[0].descriptor.captureErrorCode,'AUDIO_RECORDER_ERROR');assert.deepEqual(await reloaded.put('P-01',audio('partial'),original,{startedAt:NOW-1000,endedAt:NOW,captureErrorCode:'AUDIO_RECORDER_ERROR'}),first);
});
test('an exact-byte retry cannot silently clear or change the immutable incomplete-capture warning',async()=>{
  const f=fakeIDB();await f.store.put('P-01',audio('partial'),snapshot(),{captureErrorCode:'AUDIO_STOP_TIMEOUT'});for(const captureErrorCode of ['', 'AUDIO_RECORDER_ERROR'])await rejects(f.store.put('P-01',audio('partial'),snapshot(),{captureErrorCode}),'AUDIO_ID_CONFLICT');assert.equal((await f.store.get('P-01')).descriptor.captureErrorCode,'AUDIO_STOP_TIMEOUT');
});
for(const [label,blob,extra,times]of [['file',audio('different'),{},{}],['metadata',audio(),{notes:'later annotation'},{}],['raw',audio(),{rawText:'중단'},{}],['capture time',audio(),{},{startedAt:NOW-1000,endedAt:NOW}]])test(`same attempt ID with different ${label} is rejected without overwriting`,async()=>{
  const f=fakeIDB();await f.store.put('P-01',audio(),snapshot());const before=copy(f.control.rows.get('P-01'));await rejects(f.store.put('P-01',blob,snapshot('P-01',extra),times),'AUDIO_ID_CONFLICT');assert.deepEqual(f.control.rows.get('P-01'),before);
});
test('metadata key order does not break an exact idempotent retry',async()=>{
  const f=fakeIDB(),first=snapshot();await f.store.put('P-01',audio(),first);const reordered=Object.fromEntries(Object.entries(first).reverse());assert.equal((await f.store.put('P-01',audio(),reordered)).attemptId,'P-01');assert.equal(f.control.rows.size,1);
});
test('different source/id or an already promoted snapshot cannot attach a microphone file to another attempt',async()=>{
  const f=fakeIDB();for(const value of [snapshot('P-other'),snapshot('P-01',{source:'manual_simulation'}),{...snapshot(),actualAudioSaved:true}])await rejects(f.store.put('P-01',audio(),value),'INVALID_PRACTICE_AUDIO');assert.equal(f.control.rows.size,0);assert.equal(f.control.opens.length,0);
});
test('snapshot rejects functions, cyclic/audio payloads and accessor metadata before opening IDB',async()=>{
  const f=fakeIDB(),cycle=snapshot();cycle.self=cycle;await rejects(f.store.put('P-01',audio(),cycle),'INVALID_PRACTICE_AUDIO');await rejects(f.store.put('P-01',audio(),{...snapshot(),audio:audio()}),'INVALID_PRACTICE_AUDIO');await rejects(f.store.put('P-01',audio(),{...snapshot(),callback(){}}),'INVALID_PRACTICE_AUDIO');let called=false;const getter=snapshot();Object.defineProperty(getter,'notes',{enumerable:true,get(){called=true;return 'x';}});await rejects(f.store.put('P-01',audio(),getter),'INVALID_PRACTICE_AUDIO');assert.equal(called,false);assert.equal(f.control.opens.length,0);
});
test('a partial or unknown-field snapshot cannot be called a durable full attempt association',async()=>{
  const f=fakeIDB();await rejects(f.store.put('P-01',audio(),{id:'P-01',source:'human_voice',actualAudioSaved:false,rawText:'종료',fragments:['종료'],timing:{startedAt:NOW,endedAt:NOW}}),'INVALID_PRACTICE_AUDIO');await rejects(f.store.put('P-01',audio(),{...snapshot(),rawAudio:'unwanted'}),'INVALID_PRACTICE_AUDIO');assert.equal(f.control.rows.size,0);
});
test('a quota failure or transaction abort cannot leave a file without its snapshot',async()=>{
  for(const fault of ['failWrite','failCommit']){const f=fakeIDB();await f.store.put('P-01',audio('old'),snapshot());f.control[fault]=true;const blob=audio('new'),original=snapshot('P-02');await rejects(f.store.put('P-02',blob,original),'AUDIO_STORAGE_UNAVAILABLE');assert.equal(f.control.rows.size,1);assert.equal(await f.control.rows.get('P-01').blob.text(),'old');assert.equal(await blob.text(),'new');assert.equal(original.id,'P-02');}
});
test('silent ignored IDB write fails readback rather than claiming durability',async()=>{
  const f=fakeIDB();f.control.ignoreWrite=true;await rejects(f.store.put('P-01',audio(),snapshot()),'AUDIO_READBACK_FAILED');assert.equal(f.control.rows.size,0);
});
test('readback failure after commit leaves an inspectable row and original caller data available',async()=>{
  const f=fakeIDB();f.control.readTransform=(kind,key,value,mode)=>mode==='readonly'&&kind==='get'?undefined:value;await rejects(f.store.put('P-01',audio(),snapshot()),'AUDIO_READBACK_FAILED');assert.equal(f.control.rows.size,1);f.control.readTransform=null;assert.equal((await f.store.get('P-01')).attemptId,'P-01');
});
test('missing audio stays missing and read/list never automatically create or delete entries',async()=>{
  const f=fakeIDB();assert.equal(await f.store.get('P-none'),null);assert.deepEqual(await f.store.list(),[]);assert.equal(f.control.rows.size,0);assert.ok(f.control.transactions.every(tx=>tx.mode==='readonly'));
});
test('get and list recompute hash, so altered Blob bytes cannot masquerade as the original recording',async()=>{
  const f=fakeIDB();await f.store.put('P-01',audio('first'),snapshot());const stored=f.control.rows.get('P-01');stored.blob=audio('other');await rejects(f.store.get('P-01'),'AUDIO_INTEGRITY_FAILED');await rejects(f.store.list(),'AUDIO_INTEGRITY_FAILED');assert.equal(f.control.rows.size,1);
});
for(const [label,mutate]of [['hash',row=>row.descriptor.sha256='0'.repeat(64)],['size',row=>row.descriptor.byteLength++],['id',row=>row.attemptId='P-other'],['mime',row=>row.descriptor.contentType='audio/ogg'],['extension',row=>row.descriptor.extension='ogg'],['snapshot',row=>row.snapshot.id='P-other']])test(`corrupt stored ${label} is rejected without cleanup or data repair`,async()=>{
  const f=fakeIDB();await f.store.put('P-01',audio(),snapshot());mutate(f.control.rows.get('P-01'));await assert.rejects(f.store.get('P-01'));assert.equal(f.control.rows.size,1);
});
test('list includes old-batch rows and their snapshots without silently merging or deleting any record',async()=>{
  const f=fakeIDB();await f.store.put('P-old',audio('old'),snapshot('P-old'));await f.store.put('P-new',audio('new','audio/ogg'),snapshot('P-new'));const rows=await f.store.list();assert.deepEqual(rows.map(row=>row.attemptId),['P-old','P-new']);assert.equal(await rows[0].blob.text(),'old');assert.equal(rows[1].descriptor.extension,'ogg');assert.equal(f.control.rows.size,2);
});
test('two queued same-ID writes serialize and the second cannot replace the first',async()=>{
  const f=fakeIDB(),results=await Promise.allSettled([f.store.put('P-01',audio('first'),snapshot()),f.store.put('P-01',audio('other'),snapshot())]);assert.equal(results.filter(value=>value.status==='fulfilled').length,1);assert.equal(results.find(value=>value.status==='rejected').reason.code,'AUDIO_ID_CONFLICT');assert.equal(f.control.rows.size,1);
});
test('150MiB store cap preserves earlier files and does not silently evict old batches',async()=>{
  const f=fakeIDB(),blob=new Blob([new Uint8Array(PRACTICE_AUDIO_LIMITS.trialBytes)],{type:'audio/webm'});for(let index=0;index<7;index++)await f.store.put('P-'+index,blob,snapshot('P-'+index));await rejects(f.store.put('P-8',blob,snapshot('P-8')),'PRACTICE_AUDIO_LIMIT');assert.equal(f.control.rows.size,7);assert.equal(f.control.rows.get('P-0').descriptor.byteLength,20971520);
});
test('per-file byte cap and missing IndexedDB fail before a false saved descriptor can escape',async()=>{
  const f=fakeIDB();await rejects(f.store.put('P-01',new Blob([new Uint8Array(PRACTICE_AUDIO_LIMITS.trialBytes+1)],{type:'audio/webm'}),snapshot()),'PRACTICE_AUDIO_LIMIT');assert.equal(f.control.opens.length,0);const unavailable=makePracticeAudioStore({indexedDB:null,crypto:webcrypto});await rejects(unavailable.put('P-01',audio(),snapshot()),'AUDIO_STORAGE_UNAVAILABLE');
});
test('blocked/failed database open is explicit; a late opened blocked connection is closed',async()=>{
  const f=fakeIDB();f.control.blocked=true;await rejects(f.store.get('P-01'),'AUDIO_DATABASE_BLOCKED');assert.equal(f.control.closes,1);f.control.blocked=false;f.control.failOpen=true;await rejects(f.store.list(),'AUDIO_STORAGE_UNAVAILABLE');
});
test('versionchange closes only the database connection and later reads reopen without deleting entries',async()=>{
  const f=fakeIDB();await f.store.put('P-01',audio(),snapshot());f.db.onversionchange();assert.equal(f.control.closes,1);assert.equal((await f.store.get('P-01')).attemptId,'P-01');assert.equal(f.control.opens.length,2);
});
test('explicit cleanup deletes only backed-up expected hashes and treats already missing rows as idempotent',async()=>{
  const f=fakeIDB(),a=await f.store.put('P-01',audio('first'),snapshot()),b=await f.store.put('P-02',audio('other'),snapshot('P-02'));assert.deepEqual(await f.store.deleteMany([{attemptId:'P-01',sha256:a.sha256},{attemptId:'P-missing',sha256:b.sha256}]),{deleted:1,missing:1});assert.equal(f.control.rows.size,1);assert.ok(f.control.rows.has('P-02'));assert.deepEqual(await f.store.deleteMany([{attemptId:'P-01',sha256:a.sha256}]),{deleted:0,missing:1});
});
test('one expected-hash conflict aborts cleanup of every selected row',async()=>{
  const f=fakeIDB(),a=await f.store.put('P-01',audio('first'),snapshot());await f.store.put('P-02',audio('other'),snapshot('P-02'));await rejects(f.store.deleteMany([{attemptId:'P-01',sha256:a.sha256},{attemptId:'P-02',sha256:'0'.repeat(64)}]),'AUDIO_DELETE_CONFLICT');assert.equal(f.control.rows.size,2);assert.ok(f.control.rows.has('P-01'));assert.ok(f.control.rows.has('P-02'));
});
test('cleanup verifies current bytes, so a stale unchanged descriptor cannot authorize deletion of corrupted audio',async()=>{
  const f=fakeIDB(),a=await f.store.put('P-01',audio('first'),snapshot()),b=await f.store.put('P-02',audio('other'),snapshot('P-02'));f.control.rows.get('P-02').blob=audio('alter');await rejects(f.store.deleteMany([{attemptId:'P-01',sha256:a.sha256},{attemptId:'P-02',sha256:b.sha256}]),'AUDIO_INTEGRITY_FAILED');assert.equal(f.control.rows.size,2);
});
test('cleanup transaction failure rolls back every deletion and never erases unrelated recordings',async()=>{
  const f=fakeIDB(),a=await f.store.put('P-01',audio('first'),snapshot()),b=await f.store.put('P-02',audio('other'),snapshot('P-02'));f.control.failDelete=true;await rejects(f.store.deleteMany([{attemptId:'P-01',sha256:a.sha256},{attemptId:'P-02',sha256:b.sha256}]),'AUDIO_STORAGE_UNAVAILABLE');assert.equal(f.control.rows.size,2);
});
test('cleanup requires unique explicit IDs and full expected SHA-256; empty cleanup does nothing',async()=>{
  const f=fakeIDB();assert.deepEqual(await f.store.deleteMany([]),{deleted:0,missing:0});assert.equal(f.control.opens.length,0);for(const selected of [[{attemptId:'P-01'}],[{attemptId:'P-01',sha256:'bad'}],[{attemptId:'P-01',sha256:'0'.repeat(64)},{attemptId:'P-01',sha256:'0'.repeat(64)}]])await rejects(f.store.deleteMany(selected),'INVALID_PRACTICE_AUDIO');
});
test('audio helpers do not require API, patient storage, cloud speech or operating-system clipboard access',async()=>{
  const f=fakeIDB();await f.store.put('P-01',audio(),snapshot());assert.equal((await f.store.get('P-01')).snapshot.actualAudioSaved,false);assert.ok(f.control.opens.every(value=>value.name===PRACTICE_AUDIO_DB));
});
