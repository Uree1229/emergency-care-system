/** Structured draft readback only. No diagnosis, clinical threshold, storage or API action. */
const SYMPTOMS=['symptoms','denied_symptoms','uncertain_symptoms'];
const ENUMS={consciousness:{alert:'의식 명료',impaired:'의식 저하',unresponsive:'의식 반응 없음',unknown:'의식 미확인'},breathing:{normal:'호흡 정상',abnormal:'호흡 비정상',absent:'호흡 없음',unknown:'호흡 미확인'}};
const GROUPS=[
  {id:'blood_pressure',label:'혈압',fields:['systolic_bp','diastolic_bp'],kind:'number'},
  {id:'pulse',label:'맥박',fields:['pulse'],kind:'number'},
  {id:'spo2',label:'산소포화도',fields:['spo2'],kind:'number'},
  {id:'temperature',label:'체온',fields:['temperature'],kind:'number'},
  {id:'respiratory_rate',label:'호흡수',fields:['respiratory_rate'],kind:'number'},
  {id:'glucose',label:'혈당',fields:['glucose'],kind:'number'},
  {id:'consciousness',label:'의식',fields:['consciousness'],kind:'enum'},
  {id:'breathing',label:'호흡',fields:['breathing'],kind:'enum'},
  {id:'symptom_polarity',label:'증상·부정·미확인',fields:SYMPTOMS,kind:'symptoms'},
];
const LABELS={systolic_bp:'수축기 혈압',diastolic_bp:'이완기 혈압',pulse:'맥박',spo2:'산소포화도',temperature:'체온',respiratory_rate:'호흡수',glucose:'혈당',consciousness:'의식',breathing:'호흡',symptoms:'확인된 증상',denied_symptoms:'없다고 확인한 증상',uncertain_symptoms:'미확인·의심 증상'};
const own=(object,key)=>Object.hasOwn(object,key);
const record=value=>!!value&&typeof value==='object'&&!Array.isArray(value);
function copy(value,seen=new Set()){
  if(!value||typeof value!=='object')return value;
  if(seen.has(value))throw new Error('Circular critical review data.');
  seen.add(value);
  const result=Array.isArray(value)?value.map(v=>copy(v,seen)):Object.fromEntries(Object.keys(value).map(key=>[key,copy(value[key],seen)]));
  seen.delete(value);return result;
}
function stable(value){
  if(value===undefined)return ['undefined'];
  if(value===null)return ['null'];
  if(Array.isArray(value))return ['array',value.map(stable)];
  if(typeof value==='object')return ['object',Object.keys(value).sort().map(key=>[key,stable(value[key])])];
  return [typeof value,String(value)];
}
const same=(a,b)=>JSON.stringify(stable(a))===JSON.stringify(stable(b));
const validList=value=>Array.isArray(value)&&value.every(item=>typeof item==='string'&&!!item.trim());
// Unknown is a missing observation, never a reported zero, denial or normal state.
const unknownValue=(field,values,kind)=>!own(values,field)||values[field]===null||values[field]===undefined||(kind==='enum'&&values[field]==='unknown');
function unavailableField(session,metadata,field,questionId){
  return (own(metadata,field)&&record(metadata[field])&&own(metadata[field],'followup_unavailable_for')&&metadata[field].followup_unavailable_for===questionId)||
    (Array.isArray(session?.followup?.items)&&session.followup.items.some(item=>record(item)&&own(item,'id')&&item.id===questionId&&own(item,'answer_status')&&item.answer_status==='unavailable'));
}
function conflicts(values){
  const seen=new Map(),conflicting=new Set();
  for(const field of SYMPTOMS)for(const symptom of validList(values[field])?values[field]:[]){
    const token=symptom.trim();
    if(seen.has(token)&&seen.get(token)!==field){conflicting.add(field);conflicting.add(seen.get(token));}
    else seen.set(token,field);
  }
  return conflicting;
}
function invalid(field,value,kind){
  if(value===null||value===undefined)return kind==='symptoms';
  if(kind==='number')return typeof value!=='number'||!Number.isFinite(value);
  if(kind==='enum')return typeof value!=='string'||!own(ENUMS[field],value);
  return !validList(value);
}
function numericText(field,value){
  if(['systolic_bp','diastolic_bp'].includes(field))return `${LABELS[field]} ${value} 밀리미터 수은주`;
  if(field==='pulse'||field==='respiratory_rate')return `${LABELS[field]} 분당 ${value}회`;
  if(field==='spo2')return `산소포화도 ${value}퍼센트`;
  if(field==='temperature')return `체온 ${value}도`;
  return `혈당 ${value}밀리그램 퍼 데시리터`;
}
function fieldText(field,values,flagged,invalidFields,kind){
  if(flagged.includes(field))return `${LABELS[field]} 재확인 필요`;
  if(invalidFields.includes(field))return `${LABELS[field]} 값 확인 필요`;
  const value=values[field];
  if(unknownValue(field,values,kind))return `${LABELS[field]} 모름·미측정`;
  if(kind==='number')return numericText(field,value);
  if(kind==='enum')return ENUMS[field][value];
  if(!value.length)return `${LABELS[field]} 목록이 비어 있습니다`;
  if(field==='symptoms')return '확인된 증상 '+value.join(', ');
  return value.map(symptom=>`${symptom} ${field==='denied_symptoms'?'없음':'미확인'}`).join(', ');
}

export function buildCriticalReview(session){
  const values=record(session?.fields)?session.fields:{},metadata=record(session?.field_metadata)?session.field_metadata:{};
  const items=[];
  for(const group of GROUPS){
    const questionId=group.kind==='symptoms'?'symptoms':group.id;
    const present=group.fields.filter(field=>own(values,field));
    const unavailableFields=group.fields.filter(field=>unknownValue(field,values,group.kind)&&unavailableField(session,metadata,field,questionId));
    const clarificationFields=group.fields.filter(field=>own(metadata,field)&&metadata[field]?.needs_clarification===true&&!unavailableFields.includes(field));
    if(!present.some(field=>!unknownValue(field,values,group.kind))&&!clarificationFields.length)continue;
    const itemValues=Object.fromEntries(present.map(field=>[field,copy(values[field])]));
    const invalidFields=present.filter(field=>!unknownValue(field,values,group.kind)&&invalid(field,values[field],group.kind));
    const collision=group.kind==='symptoms'?conflicts(itemValues):new Set();
    for(const field of collision)if(!invalidFields.includes(field))invalidFields.push(field);
    const currentValues=Object.fromEntries(present.filter(field=>!clarificationFields.includes(field)&&!invalidFields.includes(field)&&!unknownValue(field,values,group.kind)).map(field=>[field,copy(values[field])]));
    const previousValues=Object.fromEntries(present.filter(field=>clarificationFields.includes(field)).map(field=>[field,copy(values[field])]));
    let text;
    if(group.id==='blood_pressure'&&group.fields.every(field=>own(currentValues,field)))text=`수축기 혈압 ${values.systolic_bp}, 이완기 혈압 ${values.diastolic_bp} 밀리미터 수은주`;
    else if(collision.size)text='증상 기록의 긍정·부정·미확인 분류가 충돌하여 재확인이 필요합니다';
    else text=(group.kind==='symptoms'?group.fields.filter(field=>present.includes(field)||clarificationFields.includes(field)):group.fields).map(field=>fieldText(field,itemValues,clarificationFields,invalidFields,group.kind)).join('. ');
    const signature='critical-v1:'+JSON.stringify(stable({id:group.id,fields:group.fields.map(field=>({field,present:own(values,field),value:values[field],metadata:own(metadata,field)?metadata[field]:undefined})),...(unavailableFields.length?{unavailableFields}:{})}));
    items.push({id:group.id,label:group.label,text,signature,fields:[...group.fields],editable:true,values:itemValues,currentValues,previousValues,clarificationFields,needsClarification:clarificationFields.length>0,invalidFields,confirmable:!clarificationFields.length&&!invalidFields.length});
  }
  return items;
}

function correctionError(){throw new Error('수정할 항목과 입력값을 확인하세요. 다른 항목은 함께 수정할 수 없습니다.');}
function plainData(value){
  if(!record(value))correctionError();
  const proto=Object.getPrototypeOf(value),constructor=proto&&Object.getOwnPropertyDescriptor(proto,'constructor');
  if(proto!==null&&(Object.getPrototypeOf(proto)!==null||constructor?.value?.name!=='Object'))correctionError();
  for(const key of Reflect.ownKeys(value)){
    const descriptor=Object.getOwnPropertyDescriptor(value,key);
    if(typeof key!=='string'||!descriptor?.enumerable||!own(descriptor,'value'))correctionError();
  }
}
function parseNumber(value){
  if(value===null||(typeof value==='string'&&['','미확인'].includes(value.trim())))return {unset:true};
  if(typeof value==='string'){
    const trimmed=value.trim();if(!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/iu.test(trimmed))correctionError();
    value=Number(trimmed);
  }
  if(typeof value!=='number'||!Number.isFinite(value))correctionError();
  return {value};
}
function parseEnum(field,value){
  if(value===null||(typeof value==='string'&&!value.trim()))return {unset:true};
  if(typeof value!=='string')correctionError();
  value=value.trim();if(value==='미확인')value='unknown';
  if(!own(ENUMS[field],value))correctionError();return {value};
}
function parseList(value){
  if(value===null||(typeof value==='string'&&['','미확인'].includes(value.trim())))return {unset:true};
  if(typeof value==='string')value=value.split(/[,\n]/u);
  if(!Array.isArray(value)||value.some(token=>typeof token!=='string'||!token.trim()))correctionError();
  const tokens=value.map(token=>token.trim());
  if(new Set(tokens).size!==tokens.length)correctionError();return {value:tokens};
}

/** Prepare one atomic item correction. The server retains clinical range validation. */
export function planCriticalCorrection(item,values){
  if(!record(item))correctionError();
  const group=GROUPS.find(entry=>entry.id===item.id);
  if(!group||!Array.isArray(item.fields)||!same(item.fields,group.fields))correctionError();
  plainData(values);plainData(item.values);
  if(Object.keys(values).some(field=>!group.fields.includes(field))||Object.keys(item.values).some(field=>!group.fields.includes(field)))correctionError();
  const proposed=copy(item.values),changes={},unset_fields=[];
  for(const [field,raw]of Object.entries(values)){
    const parsed=group.kind==='number'?parseNumber(raw):group.kind==='enum'?parseEnum(field,raw):parseList(raw);
    if(parsed.unset){if(own(item.values,field)||item.clarificationFields?.includes(field))unset_fields.push(field);delete proposed[field];}
    else{proposed[field]=parsed.value;if(!own(item.values,field)||!same(item.values[field],parsed.value)||item.clarificationFields?.includes(field))changes[field]=copy(parsed.value);}
  }
  if(group.kind==='symptoms'&&conflicts(proposed).size)throw new Error('같은 증상을 있음·없음·미확인에 동시에 둘 수 없습니다. 세 목록을 함께 검토하세요.');
  return {changes,unset_fields};
}

const CONFIRM=new Set(['네','예','응','맞아','맞아요','맞습니다','확인했어','확인했어요','확인했습니다','네 맞아요','예 맞습니다']);
const CORRECT=new Set(['아니','아니요','아니오','아니야','틀렸어','틀렸어요','틀렸습니다','수정','수정할게요','수정하겠습니다','고칠게요']);
/** Only the dedicated confirmation UI may use this exact phrase classifier. */
export function classifyCriticalReply(text){
  if(typeof text!=='string')return 'unknown';
  const phrase=text.trim().replace(/[.。]$/u,'').trim();
  return CONFIRM.has(phrase)?'confirm':CORRECT.has(phrase)?'correct':'unknown';
}
