// 환자·보호자 진술의 보조 표시용 규칙이다. 진단, 중증도 또는 치료 조건을 만들지 않는다.
export const VOICE_TTL_MS=2*60*60*1000;
export const MAX_TRANSCRIPT=8000;
const PREFIX='emergencyVoiceIntake.v2:';
export const validFlowToken=value=>typeof value==='string'&&/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
export const validPatientId=value=>typeof value==='string'&&/^(0|[1-9]\d{0,15})$/.test(value);
export const DISPLAY_FIELDS=['conditions','areas','symptoms','onset'];
const unknown='확인 필요';
const rules={
  conditions:[['당뇨병',/당뇨/],['고혈압',/고혈압/],['천식',/천식/],['심장질환 언급',/심장병|심부전|협심증|심근경색/],['뇌졸중 언급',/뇌졸중|중풍/],['뇌전증 언급',/간질|뇌전증/],['알레르기',/알레르기/]],
  areas:[['가슴',/가슴|흉부|흉통/],['머리',/머리|두통/],['복부',/배가|배에|복부|복통|아랫배|윗배/],['허리',/허리/],['등',/등이|등에|등쪽/],['목',/목이|목에|목쪽/],['팔',/팔이|팔에|팔쪽/],['다리',/다리가|다리에|다리쪽/]],
  symptoms:[['흉통',/가슴.{0,10}(아프|아파|통증|답답|조이)|흉통/],['호흡곤란',/숨.{0,12}(힘들|차|막히|못\s*쉬|안\s*쉬)|호흡곤란|숨쉬기\s*힘/],['출혈',/피가\s*나|피가\s*많이|출혈|피를\s*흘/],['어지러움',/어지럽|어지러|현기증/],['구토',/토했|토해|구토/],['발열',/열이\s*나|고열|발열/],['의식 저하',/의식(?:이)?\s*없|기절|쓰러졌|반응(?:이)?\s*없/],['복통',/배.{0,8}(아프|아파)|복통/],['두통',/머리.{0,8}(아프|아파)|두통/],['마비',/마비|움직이지\s*못/],['경련',/경련|발작/]],
};
const timePattern=/(?:약\s*)?\d+\s*(?:분|시간|일|주)\s*전|오늘\s*(?:아침|오전|오후|저녁|새벽)|어제|방금|조금\s*전/;
// '의식 없음' 같은 증상 자체의 부재 표현만 가린다. 그 뒤 '없지 않다' 등은 여전히 보류한다.
const semanticAbsence=/의식(?:이)?\s*없|반응(?:이)?\s*없|숨.{0,8}(?:못\s*쉬|안\s*쉬)|움직이지\s*못/g;
// '방금 기절했습니다' 등 한정된 직전 관측은 현재 의식 판정과 구별해 보존한다.
const recentObservation=/^\s*(?:방금|지금\s*막)\s*(?:환자(?:가|는)\s*)?(?:기절(?:을)?\s*했(?:습니다|어요)|쓰러졌(?:습니다|어요)|경련(?:을|이)?\s*(?:했|발생했)(?:습니다|어요)|출혈(?:이)?\s*(?:발생했|났)(?:습니다|어요))\s*$/;
const caution=/(?:없|않|아니|아닌|아님|아닙|아닐|아냐|안\s*(?:아프|나|하|있|보이)|모르|미확인|불확실|의심|가능성|추정|같(?:아|습|다|네|은|았|을)|듯|일까요|인가요|일지도|수도|예전|과거|어제|지난|오늘\s*(?:아침|오전|오후|저녁|새벽)|(?:분|시간|일|주)\s*전(?!부터)|전에는|전엔|아팠|있었|없었|했었|았었|었었|나았|괜찮아졌|멎었|멈췄|사라졌|끝났|호전|해소|회복|돌아왔|부인|부정|배제|정상|무관|무증상|여부|가정|만약|예문|예시|테스트|입력|선택|추출|기록|표시|명령|적어|써\s*주|확인해|물(?:어|었|으)|(?:있|생기|하|아프|아파|나|오|쉬|되|보이|느끼|토하)면|다면|경우|연락(?:하|해)|알려(?:\s*주|\s*줘)|보호자|어머니|아버지|엄마|아빠|가족|남편|아내|누나|언니|오빠|동생|자녀|아들|딸|["'“”‘’「」])/;
export function extractVoiceDisplay(transcript){
  const values={conditions:new Set(),areas:new Set(),symptoms:new Set()},times=[];
  let reviewNeeded=false;
  for(const clause of String(transcript).split(/[.!?。！？;\n]+|하지만|그러나|그런데|지금은|현재는/).filter(s=>s.trim())){
    const recent=recentObservation.test(clause);
    if(caution.test(clause.replace(semanticAbsence,'증상'))||(!recent&&/했|었|았|졌|났/.test(clause))){reviewNeeded=true;continue;}
    for(const field of ['conditions','symptoms'])for(const [label,re] of rules[field])if(re.test(clause))values[field].add(label+(recent&&field==='symptoms'?' (직전 관측)':''));
    if(/아프|아파|통증|답답|조이|다쳤|상처|출혈|부러|저림|마비|욱신|쑤시/.test(clause))for(const [label,re] of rules.areas)if(re.test(clause))values.areas.add(label);
    const time=clause.match(timePattern);if(time)times.push(time[0]);
  }
  const display=Object.fromEntries(Object.entries(values).map(([key,set])=>[key,[...set].join(', ')||unknown]));
  display.onset=times[0]||unknown;
  return {...display,reviewNeeded,alert:/흉통|호흡곤란|의식 저하|출혈/.test(display.symptoms)};
}
export function newFlowToken(cryptoSource=globalThis.crypto){
  if(cryptoSource?.randomUUID)return cryptoSource.randomUUID();
  if(!cryptoSource?.getRandomValues)throw new Error('보안 난수를 사용할 수 없어 음성 문진 흐름을 시작하지 못했습니다.');
  const bytes=cryptoSource.getRandomValues(new Uint8Array(16));bytes[6]=(bytes[6]&15)|64;bytes[8]=(bytes[8]&63)|128;
  const hex=[...bytes].map(n=>n.toString(16).padStart(2,'0')).join('');
  return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
}
const copyRecord=r=>({version:2,flow_token:r.flow_token,created_at:r.created_at,updated_at:r.updated_at,patient_id:r.patient_id,transcript:r.transcript,...Object.fromEntries(DISPLAY_FIELDS.map(k=>[k,r[k]])),confirmed:r.confirmed});
export function validVoiceRecord(r,flow,now){
  return !!r&&r.version===2&&validFlowToken(flow)&&r.flow_token===flow
    &&Number.isFinite(r.created_at)&&r.created_at<=now&&now-r.created_at<VOICE_TTL_MS
    &&Number.isFinite(r.updated_at)&&r.updated_at>=r.created_at&&r.updated_at<=now
    &&(r.patient_id===null||validPatientId(r.patient_id))&&typeof r.confirmed==='boolean'
    &&typeof r.transcript==='string'&&(!r.confirmed||r.transcript.trim().length>0)&&r.transcript.length<=MAX_TRANSCRIPT
    &&DISPLAY_FIELDS.every(k=>typeof r[k]==='string'&&(!r.confirmed||r[k].trim().length>0)&&r[k].length<=1000);
}
export class VoiceStoreError extends Error{
  constructor(){super('음성 문진을 브라우저에 보관하거나 확인하지 못했습니다. 음성 정보는 전달하지 않습니다. 저장소를 확인한 뒤 다시 시도하세요.');this.name='VoiceStoreError';}
}
export class VoiceIntakeStore{
  constructor({storage,now=()=>Date.now()}={}){this.storage=storage;this.now=now;}
  key(flow){return PREFIX+flow;}
  load(flow){
    if(!validFlowToken(flow)||!this.storage)return null;
    let r;try{r=JSON.parse(this.storage.getItem(this.key(flow)));}catch{return null;}
    if(!validVoiceRecord(r,flow,this.now())){try{this.storage.removeItem(this.key(flow));}catch{}return null;}
    return copyRecord(r);
  }
  save(record){
    const r=copyRecord(record);if(!validVoiceRecord(r,r.flow_token,this.now()))throw new VoiceStoreError();
    const raw=JSON.stringify(r);
    try{this.storage.setItem(this.key(r.flow_token),raw);if(this.storage.getItem(this.key(r.flow_token))!==raw)throw new Error('read-back mismatch');}
    catch{throw new VoiceStoreError();}
    return r;
  }
  bind(flow,patient,expected=null){
    const record=this.load(flow);
    if(!record||!validPatientId(patient)||record.patient_id!==null&&record.patient_id!==patient)throw new VoiceStoreError();
    if(expected&&JSON.stringify(record)!==JSON.stringify(expected))throw new VoiceStoreError();
    return this.save({...record,patient_id:patient,updated_at:this.now()});
  }
  remove(flow){try{this.storage.removeItem(this.key(flow));return this.storage.getItem(this.key(flow))===null;}catch{return false;}}
  payload(flow,patient){const r=this.load(flow);return r?.confirmed&&validPatientId(patient)&&r.patient_id===patient?r:null;}
}
