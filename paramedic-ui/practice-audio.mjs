/** Local practice audio only. MediaRecorder bytes are never transcoded or sent. */
import {normalizeCommandTrace} from './command-trace.mjs';
export const PRACTICE_AUDIO_DB='paramedic-command-practice-audio-v1';
export const PRACTICE_AUDIO_STORE='recordings';
export const PRACTICE_AUDIO_LIMITS=Object.freeze({durationMs:180000,trialBytes:20*1024*1024,totalBytes:150*1024*1024,stopTimeoutMs:5000});
export const PRACTICE_AUDIO_CONSTRAINTS=Object.freeze({echoCancellation:false,noiseSuppression:false,autoGainControl:false});
const CANDIDATES=['audio/webm;codecs=opus','audio/ogg;codecs=opus','audio/mp4;codecs=mp4a.40.2','audio/webm','audio/ogg','audio/mp4'];
const ID=/^[A-Za-z0-9][A-Za-z0-9._:@-]*$/u,HASH=/^[a-f0-9]{64}$/u;
const SNAPSHOT_FIELDS=['id','source','imported','trialNo','pairId','caseId','caseSnapshot','referenceText','referenceConfirmed','rawText','fragments','status','errorCode','stopReason','timing','speakerId','device','browser','condition','autoStop','provisionalTriggerText','notes','parserResult','actualAudioSaved'];
const own=(value,key)=>Object.hasOwn(value,key);
const bytes=value=>new TextEncoder().encode(value).byteLength;
function error(code,message='브라우저의 연습 음성 보관을 확인해야 합니다. 원문과 현재 화면을 유지하세요.'){const result=new Error(message);result.code=code;return result;}
function invalid(){throw error('INVALID_PRACTICE_AUDIO','연습 음성의 ID·파일·연결 정보를 확인하세요.');}
function id(value){if(typeof value!=='string'||value.length>128||!ID.test(value))invalid();return value;}
function integer(value){if(!Number.isSafeInteger(value)||value<0)invalid();return value;}
function plain(value){
  if(!value||typeof value!=='object'||Array.isArray(value))return false;
  const proto=Object.getPrototypeOf(value),ctor=proto&&Object.getOwnPropertyDescriptor(proto,'constructor');
  return proto===null||(Object.getPrototypeOf(proto)===null&&typeof ctor?.value==='function'&&ctor.value.name==='Object');
}
function shape(value,required,optional=[]){
  if(!plain(value)||required.some(key=>!own(value,key)))invalid();
  for(const key of Reflect.ownKeys(value)){const d=Object.getOwnPropertyDescriptor(value,key);if(typeof key!=='string'||![...required,...optional].includes(key)||!d?.enumerable||!own(d,'value'))invalid();}
}
function dense(value,max=10000){
  if(!Array.isArray(value)||value.length>max)invalid();const keys=Reflect.ownKeys(value);if(keys.length!==value.length+1)invalid();
  for(const key of keys)if(key!=='length'){const d=Object.getOwnPropertyDescriptor(value,key);if(typeof key!=='string'||!/^(0|[1-9]\d*)$/u.test(key)||!d?.enumerable||!own(d,'value'))invalid();}
}
function mime(value){
  if(typeof value!=='string'||value.length>150)invalid();const contentType=value.trim().toLowerCase();
  const match=/^audio\/(webm|ogg|mp4)(?:\s*;\s*codecs\s*=\s*"?[a-z0-9.,_-]+"?)?$/u.exec(contentType);
  if(!match)invalid();return {contentType,extension:match[1]};
}
function isBlob(value){return !!value&&Object.prototype.toString.call(value)==='[object Blob]'&&typeof value.arrayBuffer==='function'&&Number.isSafeInteger(value.size)&&typeof value.type==='string';}
function blobInfo(blob){
  if(!isBlob(blob)||blob.size<=0)invalid();if(blob.size>PRACTICE_AUDIO_LIMITS.trialBytes)throw error('PRACTICE_AUDIO_LIMIT','음성 파일이 시도당 20MiB 한도를 넘었습니다.');
  return {...mime(blob.type),byteLength:blob.size};
}
function times(startedAt=null,endedAt=null){
  if(startedAt===null&&endedAt===null)return {startedAt:null,endedAt:null};integer(startedAt);integer(endedAt);if(endedAt<startedAt)invalid();return {startedAt,endedAt};
}
function captureCode(value){if(typeof value!=='string'||!/^[A-Za-z0-9_-]{0,80}$/u.test(value))invalid();return value;}
function jsonSnapshot(value,attemptId){
  shape(value,SNAPSHOT_FIELDS,['expectedAction','commandTrace']);
  const commandTrace=own(value,'commandTrace')?normalizeCommandTrace(value.commandTrace):null;
  let nodes=0;const seen=new Set();
  function visit(item,depth=0){
    if(++nodes>12000||depth>12)invalid();
    if(item===null||typeof item==='boolean')return item;
    if(typeof item==='string'){if(item.length>16000)invalid();return item;}
    if(typeof item==='number'){if(!Number.isFinite(item))invalid();return item;}
    if(typeof item!=='object'||seen.has(item))invalid();seen.add(item);let result;
    if(Array.isArray(item)){dense(item,item===value.commandTrace?.events?600:256);result=item.map(child=>visit(child,depth+1));}
    else{if(!plain(item))invalid();const keys=Reflect.ownKeys(item);if(keys.length>64)invalid();result={};for(const key of keys){const d=Object.getOwnPropertyDescriptor(item,key);if(typeof key!=='string'||['__proto__','prototype','constructor'].includes(key)||!d?.enumerable||!own(d,'value'))invalid();Object.defineProperty(result,key,{value:visit(d.value,depth+1),enumerable:true,writable:true,configurable:true});}}
    seen.delete(item);return result;
  }
  const result=visit(value);
  if(commandTrace)result.commandTrace=commandTrace;
  if(!plain(result)||result.id!==attemptId||result.source!=='human_voice'||result.actualAudioSaved!==false||typeof result.rawText!=='string'||!Array.isArray(result.fragments))invalid();
  for(const fragment of result.fragments)if(typeof fragment!=='string')invalid();
  shape(result.timing,['startedAt','endedAt']);times(result.timing.startedAt,result.timing.endedAt);
  if(bytes(JSON.stringify(result))>128*1024)throw error('PRACTICE_AUDIO_LIMIT','음성과 연결할 시도 자료가 너무 큽니다.');return result;
}
function stable(value){if(Array.isArray(value))return value.map(stable);if(plain(value))return Object.fromEntries(Object.keys(value).sort().map(key=>[key,stable(value[key])]));return value;}
const same=(left,right)=>JSON.stringify(stable(left))===JSON.stringify(stable(right));
function deferred(){let resolve;const promise=new Promise(done=>{resolve=done;});return {promise,resolve};}
function stopTracks(stream){try{for(const track of stream?.getTracks?.()||[])try{track.stop();}catch{}}catch{}}

/** Separate browser microphone capture; it cannot guarantee the STT service's stream. */
export class PracticeAudioCapture {
  constructor({env=globalThis.window||globalThis,now=Date.now,onState=null,onLimit=null}={}){
    if(!env||typeof now!=='function'||(onState!==null&&typeof onState!=='function')||(onLimit!==null&&typeof onLimit!=='function'))invalid();
    this.env=env;this.now=now;this.onState=onState;this.onLimit=onLimit;this._session=null;this._generation=0;this._last=null;
  }
  get active(){return !!this._session;}
  get state(){return this._session?.state??'idle';}
  _notify(name,...args){try{this[name]?.(...args);}catch{}}
  _current(session){return this._session===session&&session.generation===this._generation&&!session.finished;}
  _clock(){return integer(this.now());}
  _later(fn,ms){return (this.env.setTimeout||globalThis.setTimeout).call(this.env,fn,ms);}
  _clear(timer){if(timer!==null)(this.env.clearTimeout||globalThis.clearTimeout).call(this.env,timer);}
  _state(session,state){session.state=state;this._notify('onState',state,{attemptId:session.attemptId});}
  start(attemptId){
    id(attemptId);if(this.active)return Promise.resolve({attemptId,status:'failed',errorCode:'AUDIO_ALREADY_ACTIVE'});
    const session={attemptId,generation:++this._generation,state:'requesting',startedAt:null,endedAt:null,stream:null,recorder:null,chunks:[],byteLength:0,contentType:'',errorCode:'',reason:'natural',finished:false,stopping:false,limited:false,timer:null,startTimer:null,stopTimer:null,start:deferred(),done:deferred()};
    this._session=session;this._last=null;this._state(session,'requesting');if(this._current(session))void this._acquire(session);return session.start.promise;
  }
  async _acquire(session){
    try{
      const Recorder=this.env.MediaRecorder,media=this.env.navigator?.mediaDevices;
      if(typeof Recorder!=='function'||typeof media?.getUserMedia!=='function'){this._finish(session,'unavailable','AUDIO_UNAVAILABLE');return;}
      let requested='';if(typeof Recorder.isTypeSupported==='function'){requested=CANDIDATES.find(type=>{try{return Recorder.isTypeSupported(type);}catch{return false;}})||'';if(!requested){this._finish(session,'unavailable','AUDIO_CODEC_UNAVAILABLE');return;}}
      const stream=await media.getUserMedia({audio:{...PRACTICE_AUDIO_CONSTRAINTS},video:false});
      if(!this._current(session)){stopTracks(stream);return;}session.stream=stream;
      const recorder=new Recorder(stream,requested?{mimeType:requested}:{});session.recorder=recorder;
      if(recorder.mimeType)session.contentType=mime(recorder.mimeType).contentType;
      else if(requested)session.contentType=mime(requested).contentType;
      recorder.onstart=()=>{
        if(!this._current(session)||session.stopping)return;
        this._clear(session.startTimer);session.startTimer=null;
        session.startedAt=this._clock();this._state(session,'recording');if(!this._current(session)||session.stopping)return;session.start.resolve({attemptId:session.attemptId,status:'recording',contentType:session.contentType,startedAt:session.startedAt,errorCode:''});
        session.timer=this._later(()=>this._limit(session,'time_limit'),PRACTICE_AUDIO_LIMITS.durationMs);
      };
      recorder.ondataavailable=event=>{
        if(!this._current(session))return;const chunk=event?.data;
        if(!isBlob(chunk)||chunk.size<0){session.errorCode='AUDIO_INVALID_CHUNK';void this.stop({reason:'error'});return;}if(!chunk.size)return;
        try{if(chunk.type){const kind=mime(chunk.type);if(session.contentType&&mime(session.contentType).extension!==kind.extension)throw Error();if(!session.contentType)session.contentType=kind.contentType;}}
        catch{session.errorCode='AUDIO_MIME_MISMATCH';void this.stop({reason:'error'});return;}
        if(session.byteLength+chunk.size>PRACTICE_AUDIO_LIMITS.trialBytes){session.errorCode='AUDIO_BYTE_LIMIT';session.chunks=[];session.byteLength=0;this._limit(session,'byte_limit');return;}
        if(session.errorCode==='AUDIO_BYTE_LIMIT')return;session.chunks.push(chunk);session.byteLength+=chunk.size;
      };
      recorder.onerror=()=>{if(this._current(session)){session.errorCode='AUDIO_RECORDER_ERROR';void this.stop({reason:'error'});}};
      recorder.onstop=()=>{if(this._current(session))this._finish(session);};
      for(const track of stream.getTracks?.()||[])track.addEventListener?.('ended',()=>{if(this._current(session)&&!session.stopping){session.errorCode='AUDIO_TRACK_ENDED';void this.stop({reason:'error'});}});
      session.startTimer=this._later(()=>{if(this._current(session)&&session.startedAt===null)this._finish(session,'failed','AUDIO_START_TIMEOUT');},PRACTICE_AUDIO_LIMITS.stopTimeoutMs);
      recorder.start(250);
    }catch(cause){if(this._current(session))this._finish(session,'failed',cause?.name==='NotAllowedError'?'AUDIO_PERMISSION_DENIED':cause?.name==='NotFoundError'?'AUDIO_DEVICE_UNAVAILABLE':'AUDIO_START_FAILED');}
  }
  _limit(session,reason){
    if(!this._current(session)||session.limited)return;session.limited=true;void this.stop({reason});this._notify('onLimit',reason,{attemptId:session.attemptId});
  }
  stop({reason='button'}={}){
    if(typeof reason!=='string'||reason.length>80)invalid();const session=this._session;
    if(!session)return Promise.resolve(this._last??{attemptId:null,status:'unavailable',blob:null,contentType:'',extension:'',byteLength:0,startedAt:null,endedAt:null,errorCode:'AUDIO_NOT_STARTED',reason});
    if(session.stopping)return session.done.promise;session.stopping=true;session.reason=reason;this._clear(session.timer);session.timer=null;this._state(session,'stopping');
    if(!this._current(session))return session.done.promise;
    if(!session.recorder){this._finish(session,'aborted','AUDIO_CANCELLED');return session.done.promise;}
    session.stopTimer=this._later(()=>{if(this._current(session))this._finish(session,'failed','AUDIO_STOP_TIMEOUT');},PRACTICE_AUDIO_LIMITS.stopTimeoutMs);
    try{if(session.recorder.state!=='inactive')session.recorder.stop();else if(session.startedAt===null)this._finish(session,'aborted','AUDIO_CANCELLED');}
    catch{this._finish(session,'failed','AUDIO_STOP_FAILED');}return session.done.promise;
  }
  abort(){return this.stop({reason:'aborted'});}
  cancel(){const session=this._session;if(!session)return this._last;session.chunks=[];session.byteLength=0;session.reason='cancelled';this._finish(session,'aborted','AUDIO_CANCELLED');return this._last;}
  _finish(session,forcedStatus=null,errorCode=''){
    if(!this._current(session))return;session.finished=true;this._clear(session.timer);this._clear(session.startTimer);this._clear(session.stopTimer);
    const recorder=session.recorder;if(recorder){recorder.onstart=recorder.ondataavailable=recorder.onerror=recorder.onstop=null;try{if(recorder.state!=='inactive')recorder.stop();}catch{}}
    stopTracks(session.stream);session.endedAt=this._clock();let blob=null,contentType='',extension='',status=forcedStatus;
    if(errorCode)session.errorCode=errorCode;
    if(session.chunks.length&&forcedStatus!=='aborted'){
      try{const kind=mime(session.contentType),BlobClass=this.env.Blob||globalThis.Blob;blob=new BlobClass(session.chunks,{type:kind.contentType});const info=blobInfo(blob);contentType=info.contentType;extension=info.extension;}
      catch{session.errorCode=session.errorCode||'AUDIO_FILE_INVALID';blob=null;}
    }
    if(!status)status=session.errorCode?'failed':blob?'ready':'empty';
    const result=Object.freeze({attemptId:session.attemptId,status,blob,contentType,extension,byteLength:blob?.size??0,startedAt:session.startedAt,endedAt:session.endedAt,errorCode:session.errorCode,reason:session.reason});
    this._session=null;this._last=result;session.chunks=[];
    session.start.resolve({attemptId:session.attemptId,status:status==='unavailable'?'unavailable':'failed',errorCode:session.errorCode||'AUDIO_STOPPED_BEFORE_START'});
    session.done.resolve(result);this._notify('onState',status,{attemptId:session.attemptId,errorCode:session.errorCode});
  }
}

export async function describePracticeAudio(attemptId,blob,{startedAt=null,endedAt=null,captureErrorCode='',crypto=globalThis.crypto}={}){
  id(attemptId);const info=blobInfo(blob),range=times(startedAt,endedAt),warning=captureCode(captureErrorCode);if(typeof crypto?.subtle?.digest!=='function')throw error('AUDIO_HASH_UNAVAILABLE','음성 파일의 SHA-256을 확인할 수 없습니다.');
  let sha256;try{const buffer=await blob.arrayBuffer();if(buffer.byteLength!==info.byteLength)invalid();const digest=await crypto.subtle.digest('SHA-256',buffer);sha256=Array.from(new Uint8Array(digest),value=>value.toString(16).padStart(2,'0')).join('');if(!HASH.test(sha256))throw Error();}
  catch(cause){if(cause?.code)throw cause;throw error('AUDIO_HASH_FAILED','음성 파일의 SHA-256 확인에 실패했습니다.');}
  return {attemptId,contentType:info.contentType,extension:info.extension,byteLength:info.byteLength,sha256,...range,savedAt:null,captureErrorCode:warning};
}
function descriptor(value,attemptId,blob){
  shape(value,['attemptId','contentType','extension','byteLength','sha256','startedAt','endedAt','savedAt','captureErrorCode']);if(value.attemptId!==attemptId)invalid();
  const info=blobInfo(blob);if(value.contentType!==info.contentType||value.extension!==info.extension||value.byteLength!==info.byteLength||!HASH.test(value.sha256))invalid();
  times(value.startedAt,value.endedAt);integer(value.savedAt);captureCode(value.captureErrorCode);return {...value};
}
function row(value){
  shape(value,['attemptId','blob','descriptor','snapshot']);const attemptId=id(value.attemptId),info=descriptor(value.descriptor,attemptId,value.blob),snapshot=jsonSnapshot(value.snapshot,attemptId);
  return {attemptId,blob:value.blob,descriptor:info,snapshot};
}
function request(request){return new Promise((resolve,reject)=>{request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(error('AUDIO_STORAGE_UNAVAILABLE'));});}
function complete(tx){return new Promise((resolve,reject)=>{tx.oncomplete=()=>resolve();tx.onabort=tx.onerror=()=>reject(error('AUDIO_STORAGE_UNAVAILABLE'));});}

/** Immutable Blob + original attempt snapshot, committed together in one IDB row. */
export function makePracticeAudioStore({indexedDB=globalThis.indexedDB,crypto=globalThis.crypto,now=Date.now}={}){
  if(typeof now!=='function')invalid();let opened=null;
  async function open(){
    if(opened)return opened;
    opened=new Promise((resolve,reject)=>{
      if(typeof indexedDB?.open!=='function'){reject(error('AUDIO_STORAGE_UNAVAILABLE'));return;}
      let opening;try{opening=indexedDB.open(PRACTICE_AUDIO_DB,1);}catch{reject(error('AUDIO_STORAGE_UNAVAILABLE'));return;}
      let settled=false;const fail=code=>{if(!settled){settled=true;reject(error(code));}};
      opening.onupgradeneeded=()=>{try{if(!opening.result.objectStoreNames.contains(PRACTICE_AUDIO_STORE))opening.result.createObjectStore(PRACTICE_AUDIO_STORE,{keyPath:'attemptId'});}catch{try{opening.transaction?.abort();}catch{}fail('AUDIO_STORAGE_UNAVAILABLE');}};
      opening.onerror=()=>fail('AUDIO_STORAGE_UNAVAILABLE');opening.onblocked=()=>fail('AUDIO_DATABASE_BLOCKED');
      opening.onsuccess=()=>{const db=opening.result;if(settled){try{db.close();}catch{}return;}settled=true;db.onversionchange=()=>{try{db.close();}catch{}opened=null;};resolve(db);};
    });
    try{return await opened;}catch(cause){opened=null;throw cause;}
  }
  async function readAll(){const db=await open();let tx;try{tx=db.transaction(PRACTICE_AUDIO_STORE,'readonly');const done=complete(tx),reading=request(tx.objectStore(PRACTICE_AUDIO_STORE).getAll());const [values]=await Promise.all([reading,done]);dense(values);return values;}catch(cause){throw cause?.code?cause:error('AUDIO_STORAGE_UNAVAILABLE');}}
  async function verify(value){const checked=row(value),actual=await describePracticeAudio(checked.attemptId,checked.blob,{startedAt:checked.descriptor.startedAt,endedAt:checked.descriptor.endedAt,captureErrorCode:checked.descriptor.captureErrorCode,crypto});if(actual.sha256!==checked.descriptor.sha256)throw error('AUDIO_INTEGRITY_FAILED','보관된 음성 파일의 연결 정보 또는 SHA-256이 일치하지 않습니다.');return checked;}
  return {
    async put(attemptId,blob,snapshot,{startedAt=null,endedAt=null,captureErrorCode=''}={}){
      id(attemptId);const frozen=jsonSnapshot(snapshot,attemptId),info=await describePracticeAudio(attemptId,blob,{startedAt,endedAt,captureErrorCode,crypto}),candidate={attemptId,blob,descriptor:{...info,savedAt:integer(now())},snapshot:frozen},db=await open();let chosen=candidate,tx;
      try{
        tx=db.transaction(PRACTICE_AUDIO_STORE,'readwrite');const done=complete(tx),objectStore=tx.objectStore(PRACTICE_AUDIO_STORE),all=objectStore.getAll();let problem=null;
        all.onsuccess=()=>{
          try{
            dense(all.result);let total=0,existing=null;
            for(const value of all.result){const current=row(value);total+=current.descriptor.byteLength;if(current.attemptId===attemptId)existing=current;}
            if(existing){if(existing.descriptor.sha256!==info.sha256||existing.descriptor.contentType!==info.contentType||existing.descriptor.byteLength!==info.byteLength||existing.descriptor.startedAt!==info.startedAt||existing.descriptor.endedAt!==info.endedAt||existing.descriptor.captureErrorCode!==info.captureErrorCode||!same(existing.snapshot,frozen))throw error('AUDIO_ID_CONFLICT','같은 시도 ID에 다른 음성 또는 원문이 보관되어 있습니다. 기존 파일은 유지합니다.');chosen=existing;}
            else{if(total+info.byteLength>PRACTICE_AUDIO_LIMITS.totalBytes)throw error('PRACTICE_AUDIO_LIMIT','브라우저 음성 보관 150MiB 한도를 넘었습니다. 먼저 ZIP으로 보관하세요.');objectStore.add(candidate);}
          }catch(cause){problem=cause;try{tx.abort();}catch{}}
        };
        all.onerror=()=>{problem=error('AUDIO_STORAGE_UNAVAILABLE');};
        try{await done;}catch(cause){throw problem||cause;}
      }catch(cause){throw cause?.code?cause:error('AUDIO_STORAGE_UNAVAILABLE');}
      const stored=await this.get(attemptId);if(!stored||!same(stored.descriptor,chosen.descriptor)||!same(stored.snapshot,frozen))throw error('AUDIO_READBACK_FAILED','음성과 원문을 보관한 뒤 다시 확인하지 못했습니다. 현재 파일을 유지하고 ZIP으로 보관하세요.');return {...stored.descriptor};
    },
    async get(attemptId){
      id(attemptId);const db=await open();let tx;try{tx=db.transaction(PRACTICE_AUDIO_STORE,'readonly');const done=complete(tx),reading=request(tx.objectStore(PRACTICE_AUDIO_STORE).get(attemptId));const [value]=await Promise.all([reading,done]);if(value===undefined)return null;const checked=await verify(value);if(checked.attemptId!==attemptId)throw error('AUDIO_INTEGRITY_FAILED');return checked;}
      catch(cause){throw cause?.code?cause:error('AUDIO_STORAGE_UNAVAILABLE');}
    },
    async list(){const values=await readAll(),result=[],ids=new Set();for(const value of values){const checked=await verify(value);if(ids.has(checked.attemptId))throw error('AUDIO_INTEGRITY_FAILED');ids.add(checked.attemptId);result.push(checked);}return result;},
    async deleteMany(entries){
      dense(entries,500);const selected=new Map();for(const entry of entries){shape(entry,['attemptId','sha256']);id(entry.attemptId);if(!HASH.test(entry.sha256)||selected.has(entry.attemptId))invalid();selected.set(entry.attemptId,entry.sha256);}if(!selected.size)return {deleted:0,missing:0};
      // Hash the current bytes before opening the deleting transaction. The
      // transaction below rechecks every expected identity before any delete.
      for(const [attemptId,sha256]of selected){const current=await this.get(attemptId);if(current&&current.descriptor.sha256!==sha256)throw error('AUDIO_DELETE_CONFLICT','내려받은 음성과 현재 보관 파일이 다릅니다. 파일을 삭제하지 않았습니다.');}
      const db=await open();let tx;try{
        tx=db.transaction(PRACTICE_AUDIO_STORE,'readwrite');const done=complete(tx),objectStore=tx.objectStore(PRACTICE_AUDIO_STORE),found=[],missing=[];let remaining=selected.size,problem=null;
        for(const [attemptId,sha256]of selected){const reading=objectStore.get(attemptId);reading.onsuccess=()=>{try{if(reading.result===undefined)missing.push(attemptId);else{const checked=row(reading.result);if(checked.attemptId!==attemptId||checked.descriptor.sha256!==sha256)throw error('AUDIO_DELETE_CONFLICT','내려받은 음성과 현재 보관 파일이 다릅니다. 파일을 삭제하지 않았습니다.');found.push(attemptId);}if(--remaining===0)for(const key of found)objectStore.delete(key);}catch(cause){problem=cause;try{tx.abort();}catch{}}};reading.onerror=()=>{problem=error('AUDIO_STORAGE_UNAVAILABLE');};}
        try{await done;}catch(cause){throw problem||cause;}return {deleted:found.length,missing:missing.length};
      }catch(cause){throw cause?.code?cause:error('AUDIO_STORAGE_UNAVAILABLE');}
    },
  };
}
