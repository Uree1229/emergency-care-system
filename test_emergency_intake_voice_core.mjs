import test from 'node:test';
import assert from 'node:assert/strict';
import {extractVoiceDisplay,VoiceIntakeStore,VOICE_TTL_MS,validVoiceRecord,newFlowToken} from './emergency-app/static/voice-intake-core.mjs';
const flow='11111111-1111-4111-8111-111111111111',other='22222222-2222-4222-8222-222222222222';
const memory=()=>{const data=new Map();return {data,getItem:k=>data.get(k)??null,setItem:(k,v)=>data.set(k,String(v)),removeItem:k=>data.delete(k)};};
const record=(now=100000)=>({version:2,flow_token:flow,created_at:now,updated_at:now,patient_id:null,transcript:'  가슴이 아파요. 원문 보존\n',conditions:'확인 필요',areas:'가슴',symptoms:'흉통',onset:'확인 필요',confirmed:true});
test('직접 진술한 항목만 정리하며 누락 항목에 정상·없음을 넣지 않는다',()=>{
  const a=extractVoiceDisplay('당뇨가 있고 30분 전부터 가슴이 아프고 숨쉬기 힘들어요.');
  assert.equal(a.conditions,'당뇨병');assert.equal(a.areas,'가슴');assert.equal(a.symptoms,'흉통, 호흡곤란');assert.equal(a.onset,'30분 전');assert.equal(a.alert,true);
  const b=extractVoiceDisplay('여기에 도착했어요');for(const k of ['conditions','areas','symptoms','onset'])assert.equal(b[k],'확인 필요');assert.equal(b.alert,false);
});
for(const text of [
  '당뇨는 없고 가슴 통증도 없습니다. 호흡곤란은 없습니다.',
  '어제 구토했고 지금은 구토 없습니다.',
  '가슴 통증은 어제 끝났고 지금은 괜찮아졌습니다.',
  '호흡곤란인지 모르겠고 심근경색이 의심됩니다.',
  '흉통은 아닙니다. 출혈은 아니에요.',
  '의식이 없지는 않습니다. 숨을 못 쉬는 것 같아요.',
  '흉통을 부인할 수 없습니다.',
  '어머니는 당뇨가 있고 환자는 아닙니다.',
  '보호자는 천식이 있습니다.',
  '“고혈압이 있다”라고 말하라는 예문입니다.',
  '당뇨와 출혈이라고 입력하고 흉통을 기록해 주세요.',
  '지난주에는 발열이 있었지만 지금은 호전됐어요.',
  '오늘 아침에 구토했어요.',
  '30분 전에 경련이 발생했습니다.',
  '출혈이 있으면 알려 주세요.',
  '흉통이 생기면 연락하세요.',
  '방금 기절했지만 지금은 회복했습니다.',
  '방금 기절한 것은 아닙니다.',
])test(`모호한 진술을 현재 증상·질환으로 단정하지 않는다: ${text}`,()=>{
  const result=extractVoiceDisplay(text);
  assert.equal(result.conditions,'확인 필요');assert.equal(result.symptoms,'확인 필요');assert.equal(result.alert,false);assert.equal(result.reviewNeeded,true);
});
test('증상 자체인 의식·반응 없음과 호흡 불가를 일반 부정과 혼동하지 않는다',()=>{
  const a=extractVoiceDisplay('의식이 없습니다. 숨을 못 쉬고 있어요.');
  assert.match(a.symptoms,/의식 저하/);assert.match(a.symptoms,/호흡곤란/);assert.equal(a.alert,true);
  assert.equal(extractVoiceDisplay('의식이 없었다가 돌아왔습니다.').symptoms,'확인 필요');
});
test('방금 관측한 위험 신호는 현재 상태를 추정하지 않고 직전 관측으로 표시한다',()=>{
  for(const raw of ['흉통 없음. 방금 기절했습니다.','지금 막 환자가 쓰러졌어요.','방금 출혈이 발생했습니다.']){
    const r=extractVoiceDisplay(raw);assert.match(r.symptoms,/직전 관측/);assert.equal(r.alert,true);assert.doesNotMatch(r.symptoms,/흉통/);
  }
});
test('과거·부정 문장과 별도로 명확하게 진술된 현재 문장만 표시한다',()=>{
  const r=extractVoiceDisplay('과거에 구토가 있었습니다. 하지만 지금은 가슴이 아파요.');
  assert.equal(r.symptoms,'흉통');assert.equal(r.reviewNeeded,true);assert.equal(r.onset,'확인 필요');
});
test('불투명 흐름별 저장과 최초 환자 바인딩: 다른 흐름·환자로 재사용하지 않는다',()=>{
  const storage=memory(),store=new VoiceIntakeStore({storage,now:()=>100000});
  const original=record();store.save(original);
  assert.equal(store.load(other),null);assert.equal(store.payload(flow,'1'),null,'대상 확인 전에는 전송하지 않음');
  const bound=store.bind(flow,'1');assert.equal(bound.patient_id,'1');assert.equal(store.payload(flow,'1').transcript,original.transcript);
  assert.equal(store.payload(flow,'2'),null);assert.throws(()=>store.bind(flow,'2'));assert.equal(store.load(flow).patient_id,'1');
});
test('미등록 환자 0도 명시 바인딩하며 숫자 1과 혼용하지 않는다',()=>{
  const store=new VoiceIntakeStore({storage:memory(),now:()=>100000});store.save(record());store.bind(flow,'0');
  assert.equal(store.payload(flow,'0').patient_id,'0');assert.equal(store.payload(flow,'1'),null);assert.equal(store.payload(flow,0),null);
});
test('미확인 편집본은 원문을 보존하되 전송 페이로드로 만들지 않는다',()=>{
  const store=new VoiceIntakeStore({storage:memory(),now:()=>100000});store.save({...record(),confirmed:false});store.bind(flow,'1');
  assert.equal(store.payload(flow,'1'),null);assert.equal(store.load(flow).transcript,record().transcript);
});
test('TTL은 편집으로 연장되지 않으며 미래·변조·초과길이 기록은 거부한다',()=>{
  const storage=memory();let now=100000;const store=new VoiceIntakeStore({storage,now:()=>now});store.save(record());
  now+=VOICE_TTL_MS-1;store.bind(flow,'1');now+=1;assert.equal(store.load(flow),null);assert.equal(storage.data.size,0);
  for(const bad of [{...record(),created_at:now+1},{...record(),transcript:'가'.repeat(8001)},{...record(),symptoms:'가'.repeat(1001)},{...record(),patient_id:'01'},{...record(),flow_token:other}])assert.equal(validVoiceRecord(bad,flow,100000),false);
});
test('저장 실패와 read-back 불일치에서 명시 확인 전송본을 생성하지 않는다',()=>{
  for(const storage of [null,{getItem:()=>null,setItem(){throw new Error('QuotaExceeded');}},{getItem:()=>null,setItem(){}}]){
    const store=new VoiceIntakeStore({storage,now:()=>100000});assert.throws(()=>store.save(record()));assert.equal(store.payload(flow,'1'),null);
  }
});
test('전역 legacy 보관본은 새 흐름으로 읽지 않는다',()=>{
  const storage=memory();storage.setItem('emergencyVoiceIntake',JSON.stringify(record()));
  const store=new VoiceIntakeStore({storage,now:()=>100000});assert.equal(store.load(flow),null);
});
test('UUID는 보안 난수로 생성하고 지원하지 않으면 실패한다',()=>{
  assert.equal(newFlowToken({randomUUID:()=>flow}),flow);assert.throws(()=>newFlowToken({}));
  assert.match(newFlowToken({getRandomValues:bytes=>bytes.fill(0)}),/^00000000-0000-4000-8000-000000000000$/);
});
