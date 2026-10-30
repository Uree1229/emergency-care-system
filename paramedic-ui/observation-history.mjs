/** Read-only projection of complete, session-scoped revision history. */
import {buildHospitalHandoff} from './hospital-handoff.mjs';

const FIELDS=['age','age_group','sex','onset_at','consciousness','breathing','symptoms','denied_symptoms','uncertain_symptoms','systolic_bp','diastolic_bp','pulse','spo2','temperature','respiratory_rate','glucose'];
const LABELS={age:'나이',age_group:'연령대',sex:'성별',onset_at:'증상 발생 시각',consciousness:'의식',breathing:'호흡',symptoms:'확인된 증상',denied_symptoms:'부정 증상',uncertain_symptoms:'미확인·의심 증상',systolic_bp:'수축기 혈압',diastolic_bp:'이완기 혈압',pulse:'맥박',spo2:'산소포화도',temperature:'체온',respiratory_rate:'호흡수',glucose:'혈당'};
const KINDS={observation:'관찰 변화 입력',dictation:'기록 입력',correction:'입력 수정',undo:'되돌림',followup_answer:'질문 답변'};
const REQUIRED=['id','session_id','version','kind','recorded_at','before_fields','after_fields','before_metadata','after_metadata','warnings'];
const own=(value,key)=>Object.hasOwn(value,key);
function fail(){const error=Error('환자 상태 변화 이력을 확인할 수 없습니다. 최신 저장 기록을 다시 불러오세요.');error.code='INVALID_OBSERVATION_HISTORY';throw error;}
function plain(value){
  if(!value||typeof value!=='object'||Array.isArray(value))return false;
  const proto=Object.getPrototypeOf(value),ctor=proto&&Object.getOwnPropertyDescriptor(proto,'constructor');
  return proto===null||(Object.getPrototypeOf(proto)===null&&typeof ctor?.value==='function'&&ctor.value.name==='Object');
}
function json(value,ancestors=new Set(),depth=0){
  if(depth>32)fail();
  if(value===null||typeof value==='string'||typeof value==='boolean')return;
  if(typeof value==='number'){if(!Number.isFinite(value))fail();return;}
  if(typeof value!=='object'||ancestors.has(value)||(!Array.isArray(value)&&!plain(value)))fail();
  const names=Reflect.ownKeys(value);
  if(Array.isArray(value)&&(names.length!==value.length+1||names.some(key=>typeof key!=='string'||(key!=='length'&&!/^(0|[1-9]\d*)$/u.test(key)))))fail();
  ancestors.add(value);
  for(const key of names){
    if(Array.isArray(value)&&key==='length')continue;
    const descriptor=Object.getOwnPropertyDescriptor(value,key);
    if(typeof key!=='string'||!descriptor?.enumerable||!own(descriptor,'value')||['__proto__','constructor','prototype'].includes(key))fail();
    json(descriptor.value,ancestors,depth+1);
  }
  ancestors.delete(value);
}
function map(value){if(!plain(value))fail();json(value);}
function positive(value){if(!Number.isSafeInteger(value)||value<=0)fail();}
function identifier(value){if(typeof value!=='string'||!/^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$/u.test(value))fail();}
function required(value,keys){if(!plain(value))fail();for(const key of keys){const descriptor=Object.getOwnPropertyDescriptor(value,key);if(!descriptor?.enumerable||!own(descriptor,'value'))fail();}}
function stable(value){if(value===null)return ['null'];if(Array.isArray(value))return ['array',value.map(stable)];if(typeof value==='object')return ['object',Object.keys(value).sort().map(key=>[key,stable(value[key])])];return [typeof value,String(value)];}
const equal=(left,right)=>JSON.stringify(stable(left))===JSON.stringify(stable(right));
const copy=value=>JSON.parse(JSON.stringify(value));
function fingerprint(value){
  let first=14695981039346656037n,second=7809847782465536322n;const mask=(1n<<64n)-1n;
  for(const byte of new TextEncoder().encode(JSON.stringify(stable(value)))){first=((first^BigInt(byte))*1099511628211n)&mask;second=((second^BigInt(byte))*14029467366897019727n)&mask;}
  return 'history-v1-'+first.toString(16).padStart(16,'0')+second.toString(16).padStart(16,'0');
}
function source(session){
  required(session,['id','version','state','fields','field_metadata']);identifier(session.id);positive(session.version);
  if(!['DRAFT','SUBMITTED'].includes(session.state))fail();map(session.fields);map(session.field_metadata);
  return {id:session.id,version:session.version,state:session.state,fields:session.fields,field_metadata:session.field_metadata};
}
function kst(ms){return new Date(ms+9*60*60*1000).toISOString().replace('T',' ').replace(/\.\d{3}Z$/u,' KST');}
function time(value,now){if(!Number.isSafeInteger(value)||value<0||!Number.isSafeInteger(value*1000)||value*1000>now)fail();try{return kst(value*1000);}catch(_){fail();}}

export function historySnapshotSignature(session){return fingerprint(source(session));}

function metadataState(meta){
  meta=plain(meta)?meta:{};
  return {clarification:meta.needs_clarification===true,removed:meta.removed===true,derived:(own(meta,'derived')&&!['',null,false].includes(meta.derived))||/^(?:r1|derived|rule|inferred|algorithm)(?:$|[_:-])/iu.test(String(meta.source||'')),baseline:meta.change_kind==='baseline'||meta.source==='patient_record'||meta.baseline===true,unavailable:typeof meta.followup_unavailable_for==='string'&&!!meta.followup_unavailable_for,review:meta.review_required===true};
}
function snapshotLines(fields,metadata,now){
  const displayMetadata=copy(metadata);
  for(const field of FIELDS)if(metadataState(displayMetadata[field]).unavailable)displayMetadata[field]={...displayMetadata[field],needs_clarification:true};
  const result=buildHospitalHandoff({fields,field_metadata:displayMetadata},{now});
  const all=result.sections.filter(section=>['observations','uncertain'].includes(section.id)).flatMap(section=>section.lines);
  return Object.fromEntries(FIELDS.map(field=>{
    const prefix=LABELS[field]+': ',meta=metadataState(metadata[field]);
    let line=all.find(text=>text.startsWith(prefix))?.slice(prefix.length)||'미기록';
    if(meta.unavailable)line+=' · 미측정·확인 불가로 보류';
    if(meta.review)line+=' · 해석 검토 필요';
    return [field,line];
  }));
}
function changesOf(revision,now){
  const before=snapshotLines(revision.before_fields,revision.before_metadata,now),after=snapshotLines(revision.after_fields,revision.after_metadata,now),changes=[];
  for(const field of FIELDS){
    const beforePresent=own(revision.before_fields,field),afterPresent=own(revision.after_fields,field),beforeState=metadataState(revision.before_metadata[field]),afterState=metadataState(revision.after_metadata[field]);
    if(beforeState.derived&&afterState.derived)continue;
    const valueChanged=beforePresent!==afterPresent||(beforePresent&&!equal(revision.before_fields[field],revision.after_fields[field]));
    if(valueChanged||!equal(beforeState,afterState))changes.push(`${LABELS[field]}: ${before[field]} → ${after[field]}`);
  }
  return changes.length?changes:['관찰 값·재확인 상태 변경 없음'];
}

export function buildObservationHistory(session,response,{now=Date.now(),limit=20}={}){
  const current=source(session),sourceSignature=fingerprint(current);
  if(!Number.isSafeInteger(now)||now<0||!Number.isSafeInteger(limit)||limit<1||limit>20)fail();
  try{kst(now);}catch(_){fail();}
  required(response,['session_id','revisions']);if(response.session_id!==current.id||!Array.isArray(response.revisions))fail();
  const rows=response.revisions;
  const names=Reflect.ownKeys(rows);if(names.length!==rows.length+1||names.some(key=>typeof key!=='string'||(key!=='length'&&!/^(0|[1-9]\d*)$/u.test(key))))fail();
  for(const key of names)if(key!=='length'){const descriptor=Object.getOwnPropertyDescriptor(rows,key);if(!descriptor?.enumerable||!own(descriptor,'value'))fail();}
  let previous=null;
  for(let index=0;index<rows.length;index++){
    const row=rows[index];required(row,REQUIRED);positive(row.id);positive(row.version);
    if(row.session_id!==current.id||row.version!==index+2||row.version>current.version||!own(KINDS,row.kind))fail();
    time(row.recorded_at,now);map(row.before_fields);map(row.after_fields);map(row.before_metadata);map(row.after_metadata);
    if(!Array.isArray(row.warnings)||row.warnings.some(warning=>typeof warning!=='string'))fail();json(row.warnings);
    if(previous&&(row.id<=previous.id||row.recorded_at<previous.recorded_at||!equal(previous.after_fields,row.before_fields)))fail();
    previous=row;
  }
  if(rows.length){if(previous.version!==current.version||!equal(previous.after_fields,current.fields))fail();}
  else if(current.version!==1)fail();
  // Do not require last metadata equality: legacy GET hydration can add unavailable flags.
  const entries=rows.slice(-limit).reverse().map(row=>({id:row.id,version:row.version,kindLabel:KINDS[row.kind],recordedAt:time(row.recorded_at,now),changes:changesOf(row,now)}));
  return {sourceSignature,total:rows.length,shown:entries.length,entries,hasEntries:entries.length>0};
}
