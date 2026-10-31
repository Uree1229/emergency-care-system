/** Deterministic saved-record summary. No assessment, network, clipboard or storage. */
const FIELDS=['age','age_group','sex','onset_at','consciousness','breathing','symptoms','denied_symptoms','uncertain_symptoms','systolic_bp','diastolic_bp','pulse','spo2','temperature','respiratory_rate','glucose'];
const LABELS={age:'나이',age_group:'연령대',sex:'성별',onset_at:'증상 발생 시각',consciousness:'의식',breathing:'호흡',symptoms:'확인된 증상',denied_symptoms:'부정 증상',uncertain_symptoms:'미확인·의심 증상',systolic_bp:'수축기 혈압',diastolic_bp:'이완기 혈압',pulse:'맥박',spo2:'산소포화도',temperature:'체온',respiratory_rate:'호흡수',glucose:'혈당'};
const NUMBERS=new Set(['age','systolic_bp','diastolic_bp','pulse','spo2','temperature','respiratory_rate','glucose']);
const LISTS=['symptoms','denied_symptoms','uncertain_symptoms'];
const ENUMS={sex:{male:'남성',female:'여성',unknown:'미확인'},consciousness:{alert:'명료',impaired:'저하',unresponsive:'반응 없음',unknown:'미확인'},breathing:{normal:'정상',abnormal:'비정상',absent:'없음',unknown:'미확인'}};
const REVIEW_LABELS={blood_pressure:'혈압',pulse:'맥박',spo2:'산소포화도',temperature:'체온',respiratory_rate:'호흡수',glucose:'혈당',consciousness:'의식',breathing:'호흡',symptom_polarity:'증상·부정·미확인'};
const CHANGE_LABELS={observation:'관찰 변화',correction:'입력 수정',dictation:'기록 갱신',undo:'되돌림',followup_answer:'질문 답변 기록'};
const OFFSET=9*60*60*1000;
const own=(object,key)=>Object.hasOwn(object,key);
const object=value=>!!value&&typeof value==='object'&&!Array.isArray(value);
const identifier=value=>typeof value==='string'&&/^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$/u.test(value);
function canonical(value,seen=new Set()){
  if(value===null)return ['null'];if(value===undefined)return ['undefined'];
  if(typeof value!=='object')return [typeof value,String(value)];
  if(seen.has(value))throw Error('Hospital handoff requires JSON record data.');
  seen.add(value);const result=Array.isArray(value)?['array',value.map(item=>canonical(item,seen))]:['object',Object.keys(value).sort().map(key=>[key,canonical(value[key],seen)])];seen.delete(value);return result;
}
const equal=(a,b)=>JSON.stringify(canonical(a))===JSON.stringify(canonical(b));
function fingerprint(value){
  const bytes=new TextEncoder().encode(JSON.stringify(canonical(value))),mask=(1n<<64n)-1n;
  let left=14695981039346656037n,right=7809847782465536322n;
  for(const byte of bytes){left=((left^BigInt(byte))*1099511628211n)&mask;right=((right^BigInt(byte))*14029467366897019727n)&mask;}
  return 'hospital-v1-'+left.toString(16).padStart(16,'0')+right.toString(16).padStart(16,'0');
}
function humanTime(ms){return new Date(ms+OFFSET).toISOString().replace('T',' ').replace(/\.\d{3}Z$/u,' KST');}
function epoch(value,now){
  if(typeof value!=='number'||!Number.isFinite(value)||value<0||!Number.isSafeInteger(value*1000)||value*1000>now)return null;
  try{return humanTime(value*1000);}catch(_){return null;}
}
const list=value=>Array.isArray(value)&&value.every(token=>typeof token==='string'&&!!token.trim()&&token.length<=200&&!/[\r\n]/u.test(token));
function derived(meta){return (own(meta,'derived')&&!['',null,false,undefined].includes(meta.derived))||/^(?:r1|derived|rule|inferred|algorithm)(?:$|[_:-])/iu.test(String(meta.source||''));}
function baseline(meta){return meta.change_kind==='baseline'||meta.source==='patient_record'||meta.baseline===true;}
function display(field,value,now){
  if(value===null||value===undefined)return {kind:'unknown',text:'미확인'};
  if(NUMBERS.has(field)){
    if(typeof value!=='number'||!Number.isFinite(value))return {kind:'invalid',text:'값 형식 미확인'};
    const unit=field==='age'?'세':['systolic_bp','diastolic_bp'].includes(field)?'mmHg':['pulse','respiratory_rate'].includes(field)?'회/분':field==='spo2'?'%':field==='temperature'?'℃':'mg/dL';
    return {kind:'known',text:`${value} ${unit}`};
  }
  if(field==='age_group')return typeof value==='string'&&/^(?:0|[1-9]0|1[0-2]0)대$/u.test(value)?{kind:'known',text:value}:{kind:'invalid',text:'값 형식 미확인'};
  if(field==='onset_at'){const time=epoch(value,now);return time?{kind:'known',text:time}:{kind:'invalid',text:'발생 시각 미확인'};}
  if(own(ENUMS,field))return typeof value==='string'&&own(ENUMS[field],value)?{kind:value==='unknown'?'unknown':'known',text:ENUMS[field][value]}:{kind:'invalid',text:'값 형식 미확인'};
  if(!list(value))return {kind:'invalid',text:'목록 형식 미확인'};
  if(!value.length)return {kind:'empty',text:'빈 목록으로 기록됨'};
  if(field==='symptoms')return {kind:'known',text:value.join(', ')};
  return {kind:field==='uncertain_symptoms'?'uncertain':'known',text:value.map(token=>`${token} ${field==='denied_symptoms'?'없음':'미확인'}`).join(', ')};
}
function conflictingSymptoms(fields){
  const seen=new Map(),bad=new Set();
  for(const field of LISTS)for(const value of list(fields[field])?fields[field]:[]){const token=value.trim();if(seen.has(token)&&seen.get(token)!==field){bad.add(field);bad.add(seen.get(token));}else seen.set(token,field);}
  return bad;
}
function observationTime(meta,now){
  if(!own(meta,'observed_at')||meta.observed_at===null)return '';
  const shown=epoch(meta.observed_at,now);return shown?' · 관찰 입력 시각 '+shown:' · 관찰 입력 시각 미확인';
}
function verifiedChanges(session,fields,metadata,now,collisions){
  const raw=session?.latest_changes;if(!Array.isArray(raw)||!raw.length||raw.length>64)return [];
  let revisionTime,kind;const seen=new Set(),result=[];
  for(const change of raw){
    if(!object(change)||typeof change.field!=='string'||!own(change,'previous')||!own(change,'current')||change.previous===undefined||change.current===undefined||!own(CHANGE_LABELS,change.change_kind)||!epoch(change.recorded_at,now))return [];
    if(kind===undefined){kind=change.change_kind;revisionTime=change.recorded_at;}
    else if(kind!==change.change_kind||revisionTime!==change.recorded_at)return [];
    if(seen.has(change.field))return [];seen.add(change.field);
    if(!FIELDS.includes(change.field))continue;
    const field=change.field,meta=object(metadata[field])?metadata[field]:{};
    if(derived(meta)||baseline(meta)||meta.needs_clarification===true||collisions.has(field))continue;
    if(kind!=='undo'&&((own(meta,'change_kind')&&meta.change_kind!==kind)||(typeof meta.observed_at==='number'&&epoch(meta.observed_at,now)&&meta.observed_at!==revisionTime)))continue;
    if(change.current===null?own(fields,field):!own(fields,field)||!equal(fields[field],change.current))return [];
    if(equal(change.previous,change.current))continue;
    const before=change.previous===null?{kind:'missing',text:'미기록'}:display(field,change.previous,now),after=change.current===null?{kind:'missing',text:'값 비움'}:display(field,change.current,now);
    if(before.kind==='invalid'||after.kind==='invalid'||(meta.removed===true&&change.current!==null))continue;
    result.push({field,previous:change.previous,current:change.current,change_kind:kind,recorded_at:revisionTime,line:`${CHANGE_LABELS[kind]} · ${LABELS[field]}: ${before.text} → ${after.text} · 기록 시각 ${epoch(revisionTime,now)}`});
  }
  return result;
}

export function buildHospitalHandoff(session,{patientLabel='환자01',reviewPendingIds=[],hasUnapplied=false,hasPendingRequest=false,now=Date.now()}={}){
  if(typeof patientLabel!=='string'||!patientLabel.trim()||patientLabel.length>100||/[\r\n]/u.test(patientLabel)||!Array.isArray(reviewPendingIds)||reviewPendingIds.some(id=>typeof id!=='string'||!id||id.length>80)||typeof hasUnapplied!=='boolean'||typeof hasPendingRequest!=='boolean'||!Number.isSafeInteger(now)||now<0)throw Error('Hospital handoff options are invalid.');
  let generated;try{generated=humanTime(now);}catch(_){throw Error('Hospital handoff timestamp is invalid.');}
  const fields=object(session?.fields)?session.fields:{},metadata=object(session?.field_metadata)?session.field_metadata:{};
  const sessionId=identifier(session?.id)?session.id:null,version=Number.isSafeInteger(session?.version)&&session.version>0?session.version:null;
  const observations=[],uncertain=[],collisions=conflictingSymptoms(fields);let hasContent=false;
  for(const field of FIELDS){
    const present=own(fields,field),meta=object(metadata[field])?metadata[field]:{},label=LABELS[field];
    if(derived(meta)){if(present||meta.needs_clarification===true)uncertain.push(`${label}: 파생 항목으로 직접 관찰에서 제외`);continue;}
    if(meta.removed===true){uncertain.push(`${label}: 삭제된 기록 · 현재 미확인`);continue;}
    if(!present&&['age','age_group'].includes(field)&&own(fields,field==='age'?'age_group':'age'))continue;
    if(collisions.has(field)){uncertain.push(`${label}: 증상 분류가 충돌하여 재확인 필요`);hasContent=true;continue;}
    const shown=display(field,fields[field],now);
    if(meta.needs_clarification===true||baseline(meta)){
      const prefix=baseline(meta)?'이전 기록':'이전 관찰';
      uncertain.push(`${label}: ${meta.needs_clarification===true?'재확인 필요 · ':''}${present&&shown.kind!=='invalid'?`${prefix} ${shown.text} · `:''}현재 미확인${observationTime(meta,now)}`);hasContent=true;continue;
    }
    if(!present){uncertain.push(`${label}: 미기록`);continue;}
    hasContent=true;
    const line=`${label}: ${shown.text}${observationTime(meta,now)}`;
    if(shown.kind==='known')observations.push(line);else uncertain.push(line);
  }
  const changes=verifiedChanges(session,fields,metadata,now,collisions),pendingIds=[...new Set(reviewPendingIds)].sort();
  if(changes.length)hasContent=true;
  const review=[`저장 상태: ${session?.state==='DRAFT'?'초안(DRAFT)':session?.state==='SUBMITTED'?'등록 반영됨(SUBMITTED)':'미확인'}`,
    pendingIds.length?'입력 내용 복창 확인 대기: '+pendingIds.map(id=>REVIEW_LABELS[id]||'기타 검토 항목').join(', '):'입력 내용 복창 확인 대기: 없음',
    hasUnapplied?'미반영 내용 있음 · 이 요약에는 포함하지 않음':'미반영 내용 표시: 없음',
    hasPendingRequest?'저장 결과 확인 대기 요청 있음 · 최신 저장 결과 확인 필요':'저장 결과 확인 대기 요청: 없음'];
  const sections=[
    {id:'patient',title:'환자·저장본',lines:[patientLabel,`요약 생성 시각: ${generated}`,`기록 ID: ${sessionId||'미확인'} · 초안 버전: ${version??'미확인'}`]},
    {id:'observations',title:'저장된 관찰',lines:observations.length?observations:['저장된 직접 관찰 없음']},
    {id:'uncertain',title:'미확인·재확인',lines:uncertain.length?uncertain:['표시할 미확인 항목 없음']},
    {id:'changes',title:'최근 기록 변경',lines:changes.length?changes.map(change=>change.line):['현재 저장본과 일치하는 최근 변경 기록 없음']},
    {id:'review',title:'검토 상태',lines:review},
  ];
  const signature=fingerprint({patientLabel,sessionId,version,state:session?.state,fields:FIELDS.map(field=>[field,own(fields,field),fields[field],own(metadata,field)?metadata[field]:undefined]),changes,reviewPendingIds:pendingIds,hasUnapplied,hasPendingRequest,content:sections.filter(section=>section.id!=='patient')});
  return {text:sections.map(section=>'['+section.title+']\n'+section.lines.join('\n')).join('\n\n'),sections,signature,sessionId,version,hasContent};
}
