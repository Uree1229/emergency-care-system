/* Main-page, same-tab write-ahead recovery. Nothing here dispatches HTTP. */
import {normalizeServer, requestId} from './voice-client.mjs';

export const MAIN_RECOVERY_PREFIX='paramedic.voice.main-recovery.v1.';
const ORDER_KEY='paramedic.voice.main-order.v1';
export const MAIN_RECOVERY_TTL=12*60*60*1000;
export const MAIN_RECOVERY_MAX_BYTES=4*1024*1024;
const ID=/^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$/;
const clone=value=>JSON.parse(JSON.stringify(value));
const fail=message=>{const error=new Error(message);error.code='MAIN_RECOVERY_BLOCKED';throw error;};
const bytes=value=>new TextEncoder().encode(value).byteLength;
function identifier(value,nullable=false){if(nullable&&value===null)return;if(typeof value!=='string'||!ID.test(value))fail('복원 식별자가 올바르지 않습니다.');}
function plain(value){return !!value&&typeof value==='object'&&!Array.isArray(value)&&Object.prototype.toString.call(value)==='[object Object]';}
function integer(value,min=0){if(!Number.isSafeInteger(value)||value<min)fail('복원 버전 또는 시각이 올바르지 않습니다.');}
function text(value,max=16000){if(typeof value!=='string'||value.length>max)fail('복원 문장의 길이 또는 형식이 올바르지 않습니다.');}
function json(value){
  let count=0;const seen=new Set();
  function visit(item,depth){
    if(++count>16000||depth>16)fail('복원 데이터가 너무 복잡합니다.');
    if(item===null||typeof item==='boolean')return;
    if(typeof item==='number'){if(!Number.isFinite(item))fail('복원 숫자가 올바르지 않습니다.');return;}
    if(typeof item==='string'){if(item.length>MAIN_RECOVERY_MAX_BYTES)fail('복원 내용이 너무 큽니다.');return;}
    if(typeof item!=='object'||seen.has(item)||(!Array.isArray(item)&&!plain(item)))fail('복원 데이터가 올바르지 않습니다.');
    seen.add(item);
    for(const key of Reflect.ownKeys(item)){
      if(key==='length'&&Array.isArray(item))continue;
      const d=Object.getOwnPropertyDescriptor(item,key);
      if(typeof key!=='string'||['__proto__','constructor','prototype'].includes(key)||!d?.enumerable||!Object.hasOwn(d,'value'))fail('복원 데이터 속성이 올바르지 않습니다.');
      visit(d.value,depth+1);
    }
    seen.delete(item);
  }
  visit(value,0);
}
export function emptyMainInput(){return {transcript:'',command:'',answer:'',mode:'auto',changeKind:'correction',fields:{},dirty:false,baseVersion:0,lat:'',lon:'',requiredCapabilities:[],consent:false};}
function validateUI(ui){
  if(!plain(ui)||!plain(ui.fields))fail('복원 입력 형식이 올바르지 않습니다.');
  for(const key of ['transcript','command','answer'])text(ui[key]);
  for(const key of ['lat','lon'])text(ui[key],128);
  if(!['auto','dictation','command'].includes(ui.mode)||!['correction','observation'].includes(ui.changeKind)||typeof ui.dirty!=='boolean'||typeof ui.consent!=='boolean')fail('복원 입력 상태가 올바르지 않습니다.');
  integer(ui.baseVersion);
  if(Object.keys(ui.fields).length>128)fail('복원 항목이 너무 많습니다.');
  for(const [key,value]of Object.entries(ui.fields)){identifier(key);text(value);}
  if(!Array.isArray(ui.requiredCapabilities)||ui.requiredCapabilities.length>128)fail('복원 진료 항목이 올바르지 않습니다.');
  for(const value of ui.requiredCapabilities)text(value,256);
}
const BODY_KEYS={
  transcripts:['text','mode','utterance_id','is_final'],commands:['text'],
  fields:['changes','unset_fields','change_kind'],undo:[],confirm:['confirmed'],
  submit:['context','start_inquiry'],audio:['audio_base64','content_type','utterance_id','mode'],
  'followup/answers':['question_id','profile','utterance_id','text','unavailable'],
  'treatment-review/preview':['expected_patient_version','selection'],
  'treatment-review/apply':['expected_patient_version','review_token','selection','confirmed','allow_reduction','reason']
};
export function validateMainRequest(request,record){
  json(request);
  if(!plain(request)||Object.keys(request).sort().join(',')!=='body,method,path'||!plain(request.body))fail('저장한 요청 형식이 올바르지 않습니다.');
  const b=request.body;identifier(b.actor_id);identifier(b.client_request_id);
  if(b.actor_id!==record.actorId)fail('저장한 요청의 작업자가 다릅니다.');
  if(request.path==='/api/voice/sessions'){
    if(request.method!=='POST'||record.sessionId!==null||record.version!==0||Object.keys(b).some(k=>!['actor_id','client_request_id','patient_id'].includes(k)))fail('새 초안 요청의 범위가 다릅니다.');
    if((b.patient_id??null)!==record.patientId)fail('새 초안 요청의 환자가 다릅니다.');
  }else{
    identifier(record.sessionId);
    const base='/api/voice/sessions/'+encodeURIComponent(record.sessionId)+'/';
    const action=request.path.startsWith(base)?request.path.slice(base.length):'';
    if(!Object.hasOwn(BODY_KEYS,action)||request.method!==(action==='fields'?'PATCH':'POST'))fail('복원할 수 없는 요청 경로 또는 방법입니다.');
    if(Object.keys(b).some(k=>!['actor_id','client_request_id','expected_version',...BODY_KEYS[action]].includes(k)))fail('복원할 수 없는 요청 항목입니다.');
    integer(b.expected_version,1);if(b.expected_version>record.version)fail('저장한 요청 버전이 초안보다 앞섭니다.');
    if(['transcripts','commands','followup/answers'].includes(action)){text(b.text,4000);if(!b.text.trim())fail('저장한 요청 원문이 비었습니다.');}
    if(['transcripts','audio','followup/answers'].includes(action))identifier(b.utterance_id);
    if(action==='transcripts'&&(!['auto','dictation','command'].includes(b.mode)||b.is_final!==true))fail('최종 인식문 형식이 올바르지 않습니다.');
    if(action==='followup/answers'){identifier(b.question_id);if(b.profile!=='voice_extended_v1'||(b.unavailable!==undefined&&b.unavailable!==true))fail('질문 답변 형식이 올바르지 않습니다.');}
  }
  return clone(request);
}
function validate(record,{pageOrigin,serverOrigin,actorId,patientId,sessionId}={}){
  json(record);
  if(!plain(record)||record.schema!==1)fail('저장한 복원 형식을 지원하지 않습니다.');
  for(const key of ['pageOrigin','serverOrigin'])if(normalizeServer(record[key])!==record[key])fail('복원 서버 주소가 올바르지 않습니다.');
  identifier(record.actorId);if(record.actorId.length>80)fail('복원 작업자 ID가 너무 깁니다.');
  identifier(record.patientId,true);identifier(record.sessionId,true);identifier(record.owner);
  integer(record.version);integer(record.revision,1);integer(record.order,1);integer(record.savedAt);integer(record.expiresAt);
  if(record.expiresAt!==record.savedAt+MAIN_RECOVERY_TTL||!Number.isSafeInteger(record.expiresAt)||(!record.sessionId&&record.version!==0)||(record.sessionId&&record.version<1)||!['none','saved','pending','terminal_error'].includes(record.outcome))fail('복원 상태가 올바르지 않습니다.');
  validateUI(record.ui);
  if(record.pending!==null)validateMainRequest(record.pending,record);
  if(pageOrigin!==undefined&&record.pageOrigin!==normalizeServer(pageOrigin))fail('현재 화면 원점이 저장한 기록과 다릅니다.');
  if(serverOrigin!==undefined&&record.serverOrigin!==normalizeServer(serverOrigin))fail('현재 API 서버가 저장한 기록과 다릅니다.');
  if(actorId!==undefined&&record.actorId!==actorId)fail('현재 작업자가 저장한 기록과 다릅니다.');
  if(patientId!==undefined&&record.patientId!==patientId)fail('현재 환자가 저장한 기록과 다릅니다.');
  if(sessionId!==undefined&&record.sessionId!==sessionId)fail('현재 초안이 저장한 기록과 다릅니다.');
  return record;
}
function read(storage,key){try{return storage.getItem(key);}catch(_){fail('브라우저 임시 저장소를 읽지 못했습니다. 서버 쓰기를 중단했습니다.');}}
function parse(raw){if(typeof raw!=='string'||bytes(raw)>MAIN_RECOVERY_MAX_BYTES)fail('복원 저장본이 없거나 4 MiB 제한을 넘었습니다.');let r;try{r=JSON.parse(raw);}catch(_){fail('저장한 복원 기록을 읽지 못했습니다.');}return validate(r);}
export function findMainRecovery(storage,scope,{now=Date.now()}={}){
  integer(now);let keys;
  try{keys=Array.from({length:storage.length},(_,i)=>storage.key(i)).filter(k=>k?.startsWith(MAIN_RECOVERY_PREFIX));}catch(_){fail('브라우저 임시 저장소를 사용할 수 없습니다.');}
  if(keys.length>32)fail('보존한 기록이 32개를 넘었습니다. 현재 탭의 기록을 확인하세요.');
  const candidates=[];
  for(const key of keys){
    const raw=read(storage,key),record=parse(raw);
    if(record.pageOrigin!==normalizeServer(scope.pageOrigin)||record.serverOrigin!==normalizeServer(scope.serverOrigin)||record.actorId!==scope.actorId)continue;
    const possiblyCommitted=record.patientId===null&&record.sessionId&&record.pending?.path==='/api/voice/sessions/'+encodeURIComponent(record.sessionId)+'/submit';
    if(scope.patientId!==undefined&&record.patientId!==scope.patientId&&!possiblyCommitted)continue;
    if(scope.sessionId!==undefined&&record.sessionId!==scope.sessionId)continue;
    if(record.savedAt>now)fail('저장한 기록의 시각이 현재 기기보다 앞섭니다.');
    candidates.push({key,raw,record,expired:now>=record.expiresAt});
  }
  candidates.sort((a,b)=>(Number(b.record.patientId===scope.patientId)-Number(a.record.patientId===scope.patientId))||b.record.order-a.record.order);
  return candidates[0]||null;
}

export class MainRecovery {
  constructor({storage,pageOrigin,serverOrigin,actorId,patientId=null,selected=null,makeId=requestId,now=Date.now}){
    this.storage=storage;this.now=now;this.owner=makeId();identifier(this.owner);
    if(!selected){let count=0;try{for(let i=0;i<storage.length;i++)if(storage.key(i)?.startsWith(MAIN_RECOVERY_PREFIX))count++;}catch(_){fail('브라우저 임시 저장소를 사용할 수 없습니다.');}if(count>=32)fail('보존한 기록이 32개입니다. 현재 탭의 기록을 확인한 뒤 새 탭에서 새 작업을 시작하세요.');}
    this.key=selected?.key||MAIN_RECOVERY_PREFIX+makeId();
    this.raw=selected?.raw??null;this.record=selected?clone(selected.record):{schema:1,pageOrigin:normalizeServer(pageOrigin),serverOrigin:normalizeServer(serverOrigin),actorId,patientId,sessionId:null,version:0,ui:emptyMainInput(),pending:null,outcome:'none',owner:this.owner,revision:0,order:0,savedAt:0,expiresAt:MAIN_RECOVERY_TTL};
    if(selected){
      validate(this.record,{pageOrigin,serverOrigin,actorId});
      const submit=this.record.sessionId&&this.record.pending?.path==='/api/voice/sessions/'+encodeURIComponent(this.record.sessionId)+'/submit';
      if(this.record.patientId!==patientId&&!(submit&&this.record.patientId===null&&patientId!==null))fail('현재 환자가 저장한 기록과 다릅니다.');
      if(selected.expired)fail('임시 저장본의 12시간 유효기간이 지났습니다. 보존본을 확인한 뒤 명시적으로 새 기록을 시작하세요.');
    }
    this._save(this.record); // Claim only this record; previous page owners now fail closed.
  }
  _save(candidate){
    if(read(this.storage,this.key)!==this.raw)fail('같은 탭의 다른 화면이 기록을 변경했습니다. 서버 요청을 보내지 않았습니다. 새로고침하여 현재 범위를 다시 확인하세요.');
    const now=this.now();integer(now);
    const previousOrder=read(this.storage,ORDER_KEY),order=previousOrder===null?1:Number(previousOrder)+1;
    if(previousOrder!==null&&(!/^[1-9][0-9]*$/.test(previousOrder)||Number(previousOrder)<this.record.order))fail('임시 저장 순서가 변경되었습니다. 서버 요청을 보내지 않았습니다.');
    integer(order,1);
    const next={...clone(candidate),owner:this.owner,revision:this.record.revision+1,order,savedAt:now,expiresAt:now+MAIN_RECOVERY_TTL};
    validate(next);
    const raw=JSON.stringify(next);if(bytes(raw)>MAIN_RECOVERY_MAX_BYTES)fail('임시 저장본이 4 MiB 제한을 넘었습니다. 원문과 현재 화면을 유지하고 서버 요청을 보내지 않았습니다.');
    try{this.storage.setItem(ORDER_KEY,String(order));if(read(this.storage,ORDER_KEY)!==String(order))fail('저장 순서 경합');this.storage.setItem(this.key,raw);}catch(_){fail('브라우저 임시 저장에 실패했습니다. 현재 화면을 닫지 말고 저장 권한·용량을 확인하세요. 서버 요청을 보내지 않았습니다.');}
    if(read(this.storage,this.key)!==raw)fail('브라우저 저장 경합을 확인했습니다. 서버 요청을 보내지 않았습니다.');
    this.raw=raw;this.record=next;return clone(next);
  }
  update({session,ui,pending=this.record.pending,outcome=this.record.outcome}={}){
    const r=clone(this.record);
    if(session){
      if(session.actor_id!==undefined&&session.actor_id!==r.actorId)fail('서버 초안 작성자가 다릅니다.');
      if(r.sessionId&&session.id!==r.sessionId)fail('다른 초안으로 복원 기록을 바꿀 수 없습니다.');
      if(r.patientId&&session.patient_id!==r.patientId)fail('다른 환자로 복원 기록을 바꿀 수 없습니다.');
      if(!['DRAFT','SUBMITTED'].includes(session.state))fail('서버 초안 상태를 확인할 수 없습니다.');
      identifier(session.id);integer(session.version,1);if(session.version<r.version)fail('서버 초안 버전이 저장본보다 이전입니다.');
      r.sessionId=session.id;r.version=session.version;r.patientId=session.patient_id??null;
    }
    if(ui!==undefined)r.ui=clone(ui);
    r.pending=pending===null?null:clone(pending);r.outcome=outcome;
    return this._save(r);
  }
  beforeWrite(request,{session,ui}={}){
    const candidate={...this.record,sessionId:session?.id??this.record.sessionId,version:session?.version??this.record.version};
    validateMainRequest(request,candidate);
    return this.update({session,ui,pending:request,outcome:'pending'});
  }
  assertSession(session){
    const submit=this.record.sessionId&&this.record.pending?.path==='/api/voice/sessions/'+encodeURIComponent(this.record.sessionId)+'/submit';
    const committed=submit&&this.record.patientId===null&&session?.state==='SUBMITTED'&&typeof session.patient_id==='string'&&ID.test(session.patient_id);
    if(!session||session.id!==this.record.sessionId||!Number.isSafeInteger(session.version)||session.version<this.record.version||!['DRAFT','SUBMITTED'].includes(session.state)||(session.actor_id!==undefined&&session.actor_id!==this.record.actorId)||((session.patient_id??null)!==this.record.patientId&&!committed))fail('서버 초안의 ID·작성자·환자·버전이 보존한 기록과 일치하지 않습니다. 새 빈 초안으로 바꾸지 않았습니다.');
    return session;
  }
}
