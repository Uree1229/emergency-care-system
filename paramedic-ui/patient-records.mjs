/** One durable notebook envelope. Record selection never restores or writes a server draft. */
import {readDraftRecovery,RECOVERY_KEY} from './draft-recovery.mjs';

export const PATIENT_RECORDS_KEY='paramedic.voice.patient-records.v1';
const MAX_RECORDS=20,MAX_BYTES=1024*1024,MAX_CHECKPOINT_BYTES=128*1024;
const NOTE_ID=/^NOTE-[A-Za-z0-9][A-Za-z0-9._:@-]*$/;
const TTL_MS=12*60*60*1000;
const STRUCTURAL_NOW=Number.MAX_SAFE_INTEGER-TTL_MS;
const copy=value=>JSON.parse(JSON.stringify(value));
function problem(code,message){const error=new Error(message);error.code=code;return error;}
function invalid(){throw problem('INVALID_PATIENT_RECORDS','Patient notebook data is invalid.');}
function limit(){throw problem('PATIENT_RECORDS_LIMIT','Patient notebook capacity exceeded. Existing records were kept.');}
function integer(value,min=0){if(!Number.isSafeInteger(value)||value<min)invalid();}
function checkNow(now){integer(now);if(!Number.isSafeInteger(now+TTL_MS))invalid();}
function plain(value){
  if(!value||typeof value!=='object'||Array.isArray(value))return false;
  const proto=Object.getPrototypeOf(value),ctor=proto&&Object.getOwnPropertyDescriptor(proto,'constructor');
  return proto===null||(Object.getPrototypeOf(proto)===null&&typeof ctor?.value==='function'&&ctor.value.name==='Object');
}
function shape(value,allowed){
  if(!plain(value))invalid();
  const keys=Reflect.ownKeys(value);
  if(keys.length!==allowed.length||allowed.some(key=>!Object.hasOwn(value,key)))invalid();
  for(const key of keys){const descriptor=Object.getOwnPropertyDescriptor(value,key);if(typeof key!=='string'||!allowed.includes(key)||!descriptor?.enumerable||!Object.hasOwn(descriptor,'value'))invalid();}
}
function origin(value){
  if(typeof value!=='string'||value.length>2048||/[\s\\?#]/u.test(value)||!/^https?:\/\/[^/]+\/?$/iu.test(value))invalid();
  let url;try{url=new URL(value);}catch(_){invalid();}
  if(!['http:','https:'].includes(url.protocol)||url.username||url.password||url.pathname!=='/'||url.search||url.hash||url.origin==='null')invalid();
  return url.origin;
}
function noteId(value){if(typeof value!=='string'||value.length>128||!NOTE_ID.test(value))invalid();}
function checkpointIdentity(raw,serverOrigin,now){
  if(raw===null)return null;
  if(typeof raw!=='string'||!raw||raw.length>MAX_CHECKPOINT_BYTES||new TextEncoder().encode(raw).byteLength>MAX_CHECKPOINT_BYTES)invalid();
  const result=readDraftRecovery({getItem:key=>key===RECOVERY_KEY?raw:null},serverOrigin,{now:now??STRUCTURAL_NOW});
  if(!['valid','expired'].includes(result.status))invalid();
  // Expired entries remain inspectable references, never an automatic restore.
  const value=result.payload||JSON.parse(raw);
  return {actorId:value.actorId,sessionId:value.sessionId,version:value.version};
}
function serialized(book){const raw=JSON.stringify(book);if(new TextEncoder().encode(raw).byteLength>MAX_BYTES)limit();return raw;}
function normalizeBook(book,now){
  if(now!==undefined)checkNow(now);
  shape(book,['schema','serverOrigin','activeId','previousId','nextNumber','records']);
  if(book.schema!==1)invalid();
  const serverOrigin=origin(book.serverOrigin);integer(book.nextNumber,1);
  if(!Array.isArray(book.records))invalid();
  if(book.records.length>MAX_RECORDS)limit();
  const arrayKeys=Reflect.ownKeys(book.records);
  if(arrayKeys.length!==book.records.length+1||arrayKeys.some(key=>typeof key!=='string'||(key!=='length'&&!/^(0|[1-9]\d*)$/u.test(key))))invalid();
  for(const key of arrayKeys)if(key!=='length'){const descriptor=Object.getOwnPropertyDescriptor(book.records,key);if(!descriptor?.enumerable||!Object.hasOwn(descriptor,'value'))invalid();}
  const ids=new Set(),numbers=new Set(),actors=new Set(),sessions=new Set();
  const records=[];
  for(const record of book.records){
    shape(record,['id','number','checkpoint','createdAt','updatedAt']);noteId(record.id);integer(record.number,1);integer(record.createdAt);integer(record.updatedAt);
    if(ids.has(record.id)||numbers.has(record.number)||record.number>=book.nextNumber||record.createdAt>record.updatedAt||(now!==undefined&&record.updatedAt>now))invalid();
    ids.add(record.id);numbers.add(record.number);
    const identity=checkpointIdentity(record.checkpoint,serverOrigin,now);
    if(identity){
      if(actors.has(identity.actorId)||(identity.sessionId!==null&&sessions.has(identity.sessionId)))invalid();
      actors.add(identity.actorId);if(identity.sessionId!==null)sessions.add(identity.sessionId);
    }
    records.push({id:record.id,number:record.number,checkpoint:record.checkpoint,createdAt:record.createdAt,updatedAt:record.updatedAt});
  }
  for(const pointer of [book.activeId,book.previousId])if(pointer!==null){noteId(pointer);if(!ids.has(pointer))invalid();}
  if(book.activeId!==null&&book.activeId===book.previousId)invalid();
  const result={schema:1,serverOrigin,activeId:book.activeId,previousId:book.previousId,nextNumber:book.nextNumber,records};
  serialized(result);return result;
}
function locate(book,id){noteId(id);const record=book.records.find(entry=>entry.id===id);if(!record)throw problem('PATIENT_RECORD_NOT_FOUND','The selected patient record does not exist.');return record;}

export function emptyBook(serverOrigin){return {schema:1,serverOrigin:origin(serverOrigin),activeId:null,previousId:null,nextNumber:1,records:[]};}

export function readPatientRecords(storage,expectedOrigin,{now=Date.now()}={}){
  let raw=null,expected;
  const failed=message=>({status:'invalid',book:null,raw:typeof raw==='string'?raw:null,message});
  try{checkNow(now);expected=origin(expectedOrigin);}catch(_){return failed('Patient notebook origin or timestamp is invalid.');}
  try{if(!storage||typeof storage.getItem!=='function')throw Error('Unavailable storage');raw=storage.getItem(PATIENT_RECORDS_KEY);}catch(_){return failed('Patient notebook storage is unavailable.');}
  if(raw===null)return {status:'absent',book:null,raw:null,message:'No patient notebook is saved.'};
  if(typeof raw!=='string'||!raw||raw.length>MAX_BYTES||new TextEncoder().encode(raw).byteLength>MAX_BYTES)return failed('Saved patient notebook is invalid or too large.');
  try{const book=normalizeBook(JSON.parse(raw),now);if(book.serverOrigin!==expected)invalid();return {status:'valid',book,raw,message:'Patient records are ready for explicit selection.'};}
  catch(_){return failed('Saved patient notebook schema, identity or origin is invalid.');}
}

/** A synchronous stale-value check, not an atomic compare-and-swap across tabs. */
export function writePatientRecords(storage,book,{expectedRaw,now=Date.now()}={}){
  if(expectedRaw!==null&&typeof expectedRaw!=='string')invalid();
  const normalized=normalizeBook(book,now),raw=serialized(normalized);
  try{
    if(!storage||typeof storage.getItem!=='function'||typeof storage.setItem!=='function')throw Error('Unavailable storage');
    if(storage.getItem(PATIENT_RECORDS_KEY)!==expectedRaw)throw problem('STALE_PATIENT_RECORDS','Patient records changed in another tab. Existing records were kept.');
    storage.setItem(PATIENT_RECORDS_KEY,raw);
  }catch(error){if(error.code==='STALE_PATIENT_RECORDS')throw error;throw problem('PATIENT_RECORDS_STORAGE_UNAVAILABLE','Patient notebook could not be saved. Keep this page open and check browser storage.');}
  return {book:normalized,raw};
}

export function createPatientRecord(book,id,{now=Date.now()}={}){
  const next=normalizeBook(book,now);noteId(id);
  if(next.records.some(entry=>entry.id===id))invalid();
  if(next.records.length>=MAX_RECORDS||next.nextNumber>=Number.MAX_SAFE_INTEGER)limit();
  next.records.push({id,number:next.nextNumber++,checkpoint:null,createdAt:now,updatedAt:now});
  next.previousId=next.activeId;next.activeId=id;return normalizeBook(next,now);
}

export function updatePatientCheckpoint(book,id,raw,{now=Date.now()}={}){
  const next=normalizeBook(book,now),record=locate(next,id);
  const previous=checkpointIdentity(record.checkpoint,next.serverOrigin,now),current=checkpointIdentity(raw,next.serverOrigin,now);
  if(previous&&(!current||current.actorId!==previous.actorId||(previous.sessionId!==null&&current.sessionId!==previous.sessionId)||(previous.sessionId===current.sessionId&&current.version<previous.version)))invalid();
  record.checkpoint=raw;record.updatedAt=now;return normalizeBook(next,now);
}

export function selectPatientRecord(book,id){
  const next=normalizeBook(book);locate(next,id);
  if(next.activeId!==id){next.previousId=next.activeId;next.activeId=id;}
  return next;
}

export function removePatientRecord(book,id){
  const next=normalizeBook(book);locate(next,id);next.records=next.records.filter(record=>record.id!==id);
  if(next.activeId===id){next.activeId=null;next.previousId=null;}
  else if(next.previousId===id)next.previousId=null;
  return normalizeBook(next);
}
