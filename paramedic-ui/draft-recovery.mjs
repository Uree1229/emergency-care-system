/** Explicitly retained text and retry references only; no audio or clinical auto-apply. */
import {normalizeCorrection,validateWorkflowRecovery,WORKFLOW_PHASES,REVIEW_FIELDS} from './quick-session.mjs';
import {normalizeRecognitionAlternatives} from './recognition-context.mjs';
export const RECOVERY_KEY = 'paramedic.voice.quick-recovery.v1';
const TTL_MS = 12 * 60 * 60 * 1000;
const MAX_BYTES = 128 * 1024;
const MODES = ['auto', 'dictation', 'command'];
const ID = /^[A-Za-z0-9][A-Za-z0-9._:@-]*$/;
const TOP = ['serverOrigin','actorId','sessionId','version','pending','recoveryText','manualText','mode','recordingMode'];
const ATTEMPT = ['target','mode','questionId','questionText','apiText','sourceText','excludedText','unappliedBefore','manual','copiedFrom','beforeVersion','beforeFlagged','trigger'];

function fail(){throw new Error('Invalid draft recovery data.');}
function plain(value){
  if(!value||typeof value!=='object'||Array.isArray(value))return false;
  const proto=Object.getPrototypeOf(value);
  const constructor=proto&&Object.getOwnPropertyDescriptor(proto,'constructor');
  return proto===null||(Object.getPrototypeOf(proto)===null&&typeof constructor?.value==='function'&&constructor.value.name==='Object');
}
function keys(value,required,optional=[]){
  if(!plain(value))fail();
  const own=Object.keys(value),allowed=new Set([...required,...optional]);
  if(required.some(key=>!Object.hasOwn(value,key))||own.some(key=>!allowed.has(key)))fail();
}
function identifier(value,max=128){if(typeof value!=='string'||value.length>max||!ID.test(value))fail();}
function text(value,max,nonempty=false){if(typeof value!=='string'||value.length>max||(nonempty&&!value.trim()))fail();}
function integer(value,min=0){if(!Number.isSafeInteger(value)||value<min)fail();}
function time(now){integer(now);if(!Number.isSafeInteger(now+TTL_MS))fail();}
function origin(value){
  if(typeof value!=='string'||value.length>2048||/[\s\\?#]/u.test(value)||!/^https?:\/\/[^/]+\/?$/iu.test(value))fail();
  let parsed;try{parsed=new URL(value);}catch(_){fail();}
  if(!['http:','https:'].includes(parsed.protocol)||parsed.username||parsed.password||parsed.pathname!=='/'||parsed.search||parsed.hash||parsed.origin==='null')fail();
  return parsed.origin;
}

// Reject non-JSON values before stringify can silently omit or transform them.
function boundedJSON(value){
  const ancestors=new Set();let nodes=0;
  function visit(item,depth){
    if(++nodes>4096||depth>12)fail();
    if(item===null||typeof item==='boolean')return;
    if(typeof item==='string'){if(item.length>16000)fail();return;}
    if(typeof item==='number'){if(!Number.isFinite(item))fail();return;}
    if(typeof item!=='object'||ancestors.has(item))fail();
    const array=Array.isArray(item);
    if(!array&&!plain(item))fail();
    const names=Reflect.ownKeys(item);
    if(names.some(key=>typeof key!=='string'))fail();
    if(array&&(item.length>4096||names.length!==item.length+1))fail();
    ancestors.add(item);
    for(const name of names){
      if(array&&name==='length')continue;
      const descriptor=Object.getOwnPropertyDescriptor(item,name);
      if(!descriptor?.enumerable||!Object.hasOwn(descriptor,'value')||
          ['__proto__','prototype','constructor'].includes(name)||/(?:audio|blob|wav|pcm|stream|samples|device)/iu.test(name))fail();
      if(array&&!/^(0|[1-9]\d*)$/u.test(name))fail();
      visit(descriptor.value,depth+1);
    }
    ancestors.delete(item);
  }
  visit(value,0);
}
function validateAttempt(attempt,job,payload){
  keys(attempt,ATTEMPT);
  const correct=job.phase==='correct';
  const workflow=WORKFLOW_PHASES.includes(job.phase);
  if(!['transcript','answer','correction','workflow'].includes(attempt.target)||!MODES.includes(attempt.mode)||typeof attempt.manual!=='boolean')fail();
  if(attempt.questionId!==null)identifier(attempt.questionId);
  text(attempt.questionText,2000);text(attempt.apiText,4000,!correct&&!workflow);
  for(const field of ['sourceText','excludedText','unappliedBefore','copiedFrom'])text(attempt[field],16000);
  integer(attempt.beforeVersion);if(attempt.beforeVersion>payload.version)fail();
  if(!Array.isArray(attempt.beforeFlagged)||attempt.beforeFlagged.length>64)fail();
  for(const field of attempt.beforeFlagged)identifier(field,80);
  if(attempt.trigger!==null){
    keys(attempt.trigger,['action','command']);
    if(!['stop','recommend','new_patient','previous_patient','handoff_summary'].includes(attempt.trigger.action))fail();
    text(attempt.trigger.command,100,true);
  }
  if(workflow){
    if(attempt.target!=='workflow'||attempt.mode!=='dictation'||attempt.questionId!==null||attempt.questionText!==''||
      attempt.apiText!==''||attempt.sourceText!==''||attempt.excludedText!==''||attempt.unappliedBefore!==''||
      attempt.manual!==false||attempt.copiedFrom!==''||attempt.trigger!==null)fail();
    return;
  }
  if(correct){
    if(attempt.target!=='correction'||attempt.mode!=='dictation'||attempt.questionId!==null||attempt.questionText!==''||
        attempt.apiText!==''||attempt.sourceText!==''||attempt.trigger!==null||attempt.manual!==false||attempt.copiedFrom!=='')fail();
  }else if(attempt.apiText!==job.text)fail();
  if(correct)return;
  if(job.phase==='answer'){
    if(attempt.target!=='answer'||attempt.questionId!==job.questionId)fail();
  }else if(attempt.target!=='transcript'||attempt.questionId!==null||attempt.mode!==job.mode)fail();
}
function validatePending(pending,payload){
  if(pending===null)return;
  keys(pending,['request','job','attempt']);
  const {request,job,attempt}=pending;
  if(WORKFLOW_PHASES.includes(job?.phase)){
    validateWorkflowRecovery(request,job,{actorId:payload.actorId,sessionId:payload.sessionId,version:payload.version});
    validateAttempt(attempt,job,payload);return;
  }
  keys(request,['path','method','body']);
  if(typeof request.path!=='string')fail();
  const create=request.path==='/api/voice/sessions';
  const base='/api/voice/sessions/'+encodeURIComponent(payload.sessionId||'');
  const transcript=request.path===base+'/transcripts';
  const answer=request.path===base+'/followup/answers';
  const correct=request.path===base+'/fields';
  if(!create&&!transcript&&!answer&&!correct||request.method!==(correct?'PATCH':'POST'))fail();
  if(create){
    if(payload.sessionId!==null||payload.version!==0)fail();
    keys(request.body,['actor_id','client_request_id']);
  }else{
    if(payload.sessionId===null)fail();
    keys(request.body,correct?['actor_id','client_request_id','expected_version','changes','unset_fields','change_kind']:transcript?['actor_id','client_request_id','expected_version','text','mode','utterance_id','is_final']:['actor_id','client_request_id','expected_version','question_id','profile','utterance_id','text'],correct?[]:[...(answer?['unavailable']:[]),'recognition_alternatives']);
    integer(request.body.expected_version,1);if(request.body.expected_version>payload.version)fail();
    if(correct){
      normalizeCorrection({changes:request.body.changes,unset_fields:request.body.unset_fields});
      if(request.body.change_kind!=='correction')fail();
    }else{text(request.body.text,4000,true);identifier(request.body.utterance_id);}
    if(transcript&&(!MODES.includes(request.body.mode)||request.body.is_final!==true))fail();
    if(answer){
      identifier(request.body.question_id);
      if(request.body.profile!=='voice_extended_v1'||(Object.hasOwn(request.body,'unavailable')&&request.body.unavailable!==true))fail();
    }
  }
  identifier(request.body.actor_id,80);identifier(request.body.client_request_id);
  if(request.body.actor_id!==payload.actorId)fail();
  keys(job,correct?['phase','changes','unsetFields']:answer?['text','questionId','utteranceId','phase']:['text','mode','utteranceId','phase'],correct?[]:['recognitionAlternatives']);
  if(job.phase!==(create?'create':correct?'correct':answer?'answer':'write'))fail();
  if(correct){
    normalizeCorrection({changes:job.changes,unset_fields:job.unsetFields});
    if(JSON.stringify(job.changes)!==JSON.stringify(request.body.changes)||JSON.stringify(job.unsetFields)!==JSON.stringify(request.body.unset_fields))fail();
  }else{text(job.text,4000,true);identifier(job.utteranceId);
    if(Object.hasOwn(job,'recognitionAlternatives'))normalizeRecognitionAlternatives(job.text,job.recognitionAlternatives);
    if(!create&&JSON.stringify(job.recognitionAlternatives)!==JSON.stringify(request.body.recognition_alternatives))fail();}
  if(answer){identifier(job.questionId);if(job.questionId!==request.body.question_id)fail();}
  else if(!correct&&!MODES.includes(job.mode))fail();
  if(create&&job.mode==='command')fail();
  if(!create&&!correct&&(job.text!==request.body.text||job.utteranceId!==request.body.utterance_id))fail();
  if(transcript&&job.mode!==request.body.mode)fail();
  validateAttempt(attempt,job,payload);
}
function validateWorkflowUI(ui,payload){
  keys(ui,['sessionId','version','step','lat','lon','consent','requiredCapabilities','editor','editorDirty']);
  if(ui.sessionId!==null)identifier(ui.sessionId);
  integer(ui.version);
  if(ui.sessionId!==payload.sessionId||ui.version>payload.version||(ui.sessionId===null&&ui.version!==0)||(ui.sessionId!==null&&ui.version===0))fail();
  if(![null,'review','submit','hospital'].includes(ui.step)||typeof ui.consent!=='boolean'||typeof ui.editorDirty!=='boolean')fail();
  text(ui.lat,128);text(ui.lon,128);
  if(!Array.isArray(ui.requiredCapabilities)||ui.requiredCapabilities.length>128||new Set(ui.requiredCapabilities).size!==ui.requiredCapabilities.length)fail();
  for(const item of ui.requiredCapabilities){text(item,256,true);if(!item.trim()||/[\u0000-\u001f\u007f]/.test(item))fail();}
  keys(ui.editor,[],REVIEW_FIELDS);
  for(const value of Object.values(ui.editor))text(value,16000);
}
function normalized(payload,stored=false){
  boundedJSON(payload);
  keys(payload,stored?[...TOP,'createdAt','expiresAt']:TOP,['workflowUI']);
  identifier(payload.actorId,80);
  integer(payload.version);
  if(payload.sessionId===null){if(payload.version!==0)fail();}
  else{identifier(payload.sessionId);if(payload.version===0)fail();}
  text(payload.recoveryText,16000);text(payload.manualText,16000);
  if(!MODES.includes(payload.mode)||!['toggle','hold'].includes(payload.recordingMode))fail();
  const serverOrigin=origin(payload.serverOrigin);
  validatePending(payload.pending,payload);
  if(Object.hasOwn(payload,'workflowUI'))validateWorkflowUI(payload.workflowUI,payload);
  // Roundtrip makes an independent snapshot without reordering the retry body.
  const copy=JSON.parse(JSON.stringify(payload));
  return {...copy,serverOrigin};
}
function serialized(payload){
  const raw=JSON.stringify(payload);
  if(new TextEncoder().encode(raw).byteLength>MAX_BYTES)throw new Error('Draft recovery exceeds the 128 KiB storage limit. Previous recovery was kept.');
  return raw;
}

export function writeDraftRecovery(storage,payload,{now=Date.now()}={}){
  time(now);
  const value={...normalized(payload),createdAt:now,expiresAt:now+TTL_MS};
  const raw=serialized(value);
  try{
    if(!storage||typeof storage.setItem!=='function')throw new Error('Unavailable storage');
    storage.setItem(RECOVERY_KEY,raw);
  }catch(_){throw new Error('Draft recovery could not be saved. Keep this page open and check browser storage.');}
  return value;
}

export function readDraftRecovery(storage,expectedOrigin,{now=Date.now()}={}){
  const invalid=message=>({status:'invalid',payload:null,message});
  let expected,raw;
  try{time(now);expected=origin(expectedOrigin);}catch(_){return invalid('Recovery origin or timestamp is invalid.');}
  try{
    if(!storage||typeof storage.getItem!=='function')throw new Error('Unavailable storage');
    raw=storage.getItem(RECOVERY_KEY);
  }catch(_){return invalid('Recovery storage is unavailable.');}
  if(raw===null)return {status:'absent',payload:null,message:'No saved draft recovery.'};
  if(typeof raw!=='string'||!raw||raw.length>MAX_BYTES||new TextEncoder().encode(raw).byteLength>MAX_BYTES)return invalid('Saved recovery is invalid or too large.');
  let payload;
  try{
    payload=normalized(JSON.parse(raw),true);
    integer(payload.createdAt);integer(payload.expiresAt);
    if(payload.serverOrigin!==expected||!Number.isSafeInteger(payload.createdAt+TTL_MS)||payload.expiresAt!==payload.createdAt+TTL_MS||payload.createdAt>now)fail();
  }catch(_){return invalid('Saved recovery schema, origin or lifetime is invalid.');}
  if(now>=payload.expiresAt)return {status:'expired',payload:null,message:'Saved draft recovery has expired.'};
  return {status:'valid',payload,message:'Saved text and retry reference are ready for explicit recovery.'};
}

export function clearDraftRecovery(storage){
  try{if(!storage||typeof storage.removeItem!=='function')return false;storage.removeItem(RECOVERY_KEY);return true;}catch(_){return false;}
}
