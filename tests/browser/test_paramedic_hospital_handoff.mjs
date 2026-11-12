import test from 'node:test';
import assert from 'node:assert/strict';
import {buildHospitalHandoff} from '../../paramedic-ui/hospital-handoff.mjs';

const NOW=1_700_000_100_000,T=1_700_000_000,STAMP='2023-11-15 07:13:20 KST';
const session=(fields={},field_metadata={},latest_changes=[])=>({id:'VS-SYNTHETIC',version:3,state:'DRAFT',fields,field_metadata,latest_changes});
const summary=(fields={},metadata={},changes=[],opts={})=>buildHospitalHandoff(session(fields,metadata,changes),{now:NOW,...opts});
const lines=(value,id)=>value.sections.find(section=>section.id===id).lines;
const change=(field,previous,current,change_kind='observation',recorded_at=T)=>({field,previous,current,change_kind,recorded_at});
const clone=value=>JSON.parse(JSON.stringify(value));

test('the exact result/section contract includes a fixed-offset creation time and source identifiers',()=>{
  const result=summary({pulse:90});assert.deepEqual(Object.keys(result),['text','sections','signature','sessionId','version','hasContent']);
  assert.deepEqual(result.sections.map(section=>section.id),['patient','observations','uncertain','changes','review']);
  assert.deepEqual(lines(result,'patient'),['환자01','요약 생성 시각: 2023-11-15 07:15:00 KST','기록 ID: VS-SYNTHETIC · 초안 버전: 3']);
  assert.equal(result.sessionId,'VS-SYNTHETIC');assert.equal(result.version,3);assert.equal(result.hasContent,true);assert.match(result.signature,/^hospital-v1-[0-9a-f]{32}$/);
});
test('empty or absent sessions expose no observations and do not imply normality or absence',()=>{
  for(const input of [null,undefined,{}, {fields:null},{fields:{unrelated:'synthetic'}}]){
    const result=buildHospitalHandoff(input,{now:NOW});assert.equal(result.hasContent,false);assert.deepEqual(lines(result,'observations'),['저장된 직접 관찰 없음']);assert.ok(lines(result,'uncertain').includes('호흡: 미기록'));assert.doesNotMatch(lines(result,'uncertain').join('\n'),/호흡: 정상|호흡: 없음/);
  }
});
test('source data remains untouched and returned section arrays are independent between builds',()=>{
  const input=session({pulse:90,denied_symptoms:['구토']},{pulse:{evidence:['synthetic source']}}),before=clone(input),first=buildHospitalHandoff(input,{now:NOW});
  first.sections[0].lines.push('mutated result only');assert.deepEqual(input,before);assert.equal(buildHospitalHandoff(input,{now:NOW}).sections[0].lines.length,3);
});
for(const [field,value,expected]of [
  ['age',47,'나이: 47 세'],['age_group','60대','연령대: 60대'],['systolic_bp',120,'수축기 혈압: 120 mmHg'],['diastolic_bp',80,'이완기 혈압: 80 mmHg'],
  ['pulse',90,'맥박: 90 회/분'],['spo2',97,'산소포화도: 97 %'],['temperature',36.5,'체온: 36.5 ℃'],['respiratory_rate',20,'호흡수: 20 회/분'],['glucose',100,'혈당: 100 mg/dL'],
])test(`saved ${field} has literal units without clinical interpretation`,()=>assert.deepEqual(lines(summary({[field]:value}),'observations'),[expected]));
for(const field of ['age','systolic_bp','diastolic_bp','pulse','spo2','temperature','respiratory_rate','glucose'])test(`zero ${field} remains recorded rather than being converted to missing`,()=>{
  const result=summary({[field]:0});assert.equal(lines(result,'observations').length,1);assert.match(lines(result,'observations')[0],/: 0 /);
});
for(const [field,value,expected]of [
  ['sex','male','성별: 남성'],['sex','female','성별: 여성'],['consciousness','alert','의식: 명료'],['consciousness','impaired','의식: 저하'],['consciousness','unresponsive','의식: 반응 없음'],
  ['breathing','normal','호흡: 정상'],['breathing','abnormal','호흡: 비정상'],['breathing','absent','호흡: 없음'],
])test(`saved enum ${field}/${value} preserves its explicit meaning`,()=>assert.deepEqual(lines(summary({[field]:value}),'observations'),[expected]));
for(const [field,label]of [['sex','성별'],['consciousness','의식'],['breathing','호흡']])test(`explicit unknown ${field} is separated from missing, normal and none`,()=>{
  const result=summary({[field]:'unknown'});assert.ok(lines(result,'uncertain').includes(label+': 미확인'));assert.deepEqual(lines(result,'observations'),['저장된 직접 관찰 없음']);assert.equal(result.hasContent,true);
});
test('age-group-only input never fabricates an exact age, and exact age never manufactures an age group',()=>{
  assert.equal(lines(summary({age_group:'60대'}),'uncertain').some(line=>line.startsWith('나이:')),false);assert.equal(lines(summary({age:47}),'uncertain').some(line=>line.startsWith('연령대:')),false);
});
for(const value of ['0대','120대'])test(`a saved boundary age group ${value} is displayed literally`,()=>assert.ok(lines(summary({age_group:value}),'observations').includes('연령대: '+value)));
for(const [field,value]of [['pulse','90'],['spo2',false],['temperature',Infinity],['respiratory_rate',NaN],['glucose',{}],['age_group','60'],['sex','normal'],['breathing','alert']])test(`invalid structured ${field} is kept out of saved observations`,()=>{
  const result=summary({[field]:value});assert.deepEqual(lines(result,'observations'),['저장된 직접 관찰 없음']);assert.match(lines(result,'uncertain').join('\n'),/값 형식 미확인/);
});
test('null does not become zero, absent breathing or a positive symptom',()=>{
  const result=summary({pulse:null,breathing:null,symptoms:null});assert.ok(lines(result,'uncertain').includes('맥박: 미확인'));assert.ok(lines(result,'uncertain').includes('호흡: 미확인'));assert.deepEqual(lines(result,'observations'),['저장된 직접 관찰 없음']);
});
test('positive, denied and uncertain symptoms retain three separate meanings',()=>{
  const result=summary({symptoms:['흉통'],denied_symptoms:['구토'],uncertain_symptoms:['출혈']});
  assert.ok(lines(result,'observations').includes('확인된 증상: 흉통'));assert.ok(lines(result,'observations').includes('부정 증상: 구토 없음'));assert.ok(lines(result,'uncertain').includes('미확인·의심 증상: 출혈 미확인'));
});
for(const [field,label]of [['symptoms','확인된 증상'],['denied_symptoms','부정 증상'],['uncertain_symptoms','미확인·의심 증상']])test(`an explicit empty ${field} never means every symptom is absent`,()=>{
  const result=summary({[field]:[]});assert.ok(lines(result,'uncertain').includes(label+': 빈 목록으로 기록됨'));assert.doesNotMatch(result.text,/모든 증상 없음|증상: 없음/);assert.deepEqual(lines(result,'observations'),['저장된 직접 관찰 없음']);
});
test('an absent denied list produces only a missing-list notice, never an invented denial',()=>{
  const result=summary({symptoms:['흉통']});assert.ok(lines(result,'uncertain').includes('부정 증상: 미기록'));assert.doesNotMatch(result.text,/흉통 없음/);
});
test('conflicting symptom polarities are withheld from current observations',()=>{
  const result=summary({symptoms:['출혈'],denied_symptoms:['출혈']});assert.equal(lines(result,'observations').some(line=>line.includes('출혈')),false);assert.ok(lines(result,'uncertain').filter(line=>line.includes('분류가 충돌')).length===2);assert.doesNotMatch(result.text,/출혈 없음/);
});
for(const value of ['구토',[null],[''],['a\nprivate raw']])test(`malformed symptom list ${JSON.stringify(value)} is not repeated as a fact`,()=>assert.deepEqual(lines(summary({denied_symptoms:value}),'observations'),['저장된 직접 관찰 없음']));
test('needs_clarification numeric values are explicitly previous observations with current value unknown',()=>{
  const result=summary({pulse:90},{pulse:{needs_clarification:true,observed_at:T,evidence:['PRIVATE_EVIDENCE']}});
  assert.deepEqual(lines(result,'observations'),['저장된 직접 관찰 없음']);assert.ok(lines(result,'uncertain').includes('맥박: 재확인 필요 · 이전 관찰 90 회/분 · 현재 미확인 · 관찰 입력 시각 '+STAMP));assert.doesNotMatch(result.text,/PRIVATE_EVIDENCE/);
});
test('one flagged blood-pressure component never suppresses or mislabels the other saved component',()=>{
  const result=summary({systolic_bp:120,diastolic_bp:80},{systolic_bp:{needs_clarification:true}});assert.deepEqual(lines(result,'observations'),['이완기 혈압: 80 mmHg']);assert.ok(lines(result,'uncertain').includes('수축기 혈압: 재확인 필요 · 이전 관찰 120 mmHg · 현재 미확인'));
});
test('a value-less clarification field still appears as unresolved without inventing a previous value',()=>{
  const result=summary({}, {pulse:{needs_clarification:true}});assert.ok(lines(result,'uncertain').includes('맥박: 재확인 필요 · 현재 미확인'));assert.equal(result.hasContent,true);assert.doesNotMatch(result.text,/이전 관찰 undefined|맥박: 0/);
});
test('flagged denied symptoms are historical denials rather than present denials',()=>{
  const result=summary({denied_symptoms:['구토']},{denied_symptoms:{needs_clarification:true}});assert.deepEqual(lines(result,'observations'),['저장된 직접 관찰 없음']);assert.ok(lines(result,'uncertain').includes('부정 증상: 재확인 필요 · 이전 관찰 구토 없음 · 현재 미확인'));
});
test('removed metadata suppresses an accidentally retained value',()=>{
  const result=summary({pulse:90},{pulse:{removed:true}});assert.deepEqual(lines(result,'observations'),['저장된 직접 관찰 없음']);assert.ok(lines(result,'uncertain').includes('맥박: 삭제된 기록 · 현재 미확인'));assert.doesNotMatch(result.text,/90/);
});
for(const metadata of [{derived:'r1_reconcile'},{source:'algorithm'},{source:'r1_parser'}])test(`derived supported fields are not promoted to direct observations: ${JSON.stringify(metadata)}`,()=>{
  const result=summary({consciousness:'alert'},{consciousness:metadata});assert.deepEqual(lines(result,'observations'),['저장된 직접 관찰 없음']);assert.ok(lines(result,'uncertain').includes('의식: 파생 항목으로 직접 관찰에서 제외'));assert.equal(result.hasContent,false);
});
test('unsupported R1 fields, raw transcripts, baseline summary, recommendations and assessment are absent',()=>{
  const input=session({pulse:90,pre_ktas:2,stemi_suspected:true,chief_complaint:'PRIVATE_R1'},{});input.utterances=[{transcript:'PRIVATE_RAW_TRANSCRIPT'}];input.baseline_summary={symptoms:'PRIVATE_BASELINE'};input.speak_text='PRIVATE_READBACK';input.manualText='PRIVATE_UNSAVED';input.recommendation='PRIVATE_HOSPITAL';
  const result=buildHospitalHandoff(input,{now:NOW,hasUnapplied:true});assert.doesNotMatch(result.text,/PRIVATE_|pre_ktas|STEMI|SBAR|호전|악화|추천 병원/);assert.doesNotMatch(result.signature,/PRIVATE_|pre_ktas/);
});
test('baseline patient-record values are explicitly historical, never current observations',()=>{
  const result=summary({pulse:90},{pulse:{source:'patient_record',change_kind:'baseline',observed_at:null}});assert.deepEqual(lines(result,'observations'),['저장된 직접 관찰 없음']);assert.ok(lines(result,'uncertain').includes('맥박: 이전 기록 90 회/분 · 현재 미확인'));
});
test('onset uses its saved epoch in seconds, not the summary-generation time',()=>{
  const result=summary({onset_at:T});assert.deepEqual(lines(result,'observations'),['증상 발생 시각: '+STAMP]);assert.doesNotMatch(lines(result,'observations')[0],/07:15:00/);
});
test('observation input time is a readable fixed-KST timestamp without epoch implementation details',()=>{
  const result=summary({pulse:90},{pulse:{observed_at:T}});assert.deepEqual(lines(result,'observations'),['맥박: 90 회/분 · 관찰 입력 시각 '+STAMP]);assert.doesNotMatch(result.text,/epoch|\.000\+09:00/);
});
test('fractional timestamp precision remains in the signature while its display omits milliseconds',()=>{
  const first=summary({onset_at:T+0.125}),second=summary({onset_at:T+0.5});assert.deepEqual(lines(first,'observations'),['증상 발생 시각: 2023-11-15 07:13:20 KST']);assert.deepEqual(lines(second,'observations'),lines(first,'observations'));assert.notEqual(first.signature,second.signature);
});
test('epoch zero is an actual timestamp rather than a falsy missing value',()=>assert.deepEqual(lines(summary({onset_at:0}),'observations'),['증상 발생 시각: 1970-01-01 09:00:00 KST']));
for(const value of [T+101,-1,'1700000000',Infinity,NaN,Number.MAX_SAFE_INTEGER])test(`invalid or future onset ${String(value)} is unknown`,()=>{
  const result=summary({onset_at:value});assert.deepEqual(lines(result,'observations'),['저장된 직접 관찰 없음']);assert.ok(lines(result,'uncertain').includes('증상 발생 시각: 발생 시각 미확인'));
});
test('future observation input time does not become a current timestamp or erase the saved numeric value',()=>assert.deepEqual(lines(summary({pulse:90},{pulse:{observed_at:T+101}}),'observations'),['맥박: 90 회/분 · 관찰 입력 시각 미확인']));
test('missing observation input time is not filled from now or updated_at',()=>{
  const input=session({pulse:90});input.updated_at=T;const result=buildHospitalHandoff(input,{now:NOW});assert.deepEqual(lines(result,'observations'),['맥박: 90 회/분']);
});
for(const [kind,label]of [['observation','관찰 변화'],['correction','입력 수정'],['dictation','기록 갱신'],['undo','되돌림'],['followup_answer','질문 답변 기록']])test(`${kind} latest revision receives only its own record-change label`,()=>{
  const result=summary({pulse:100},{},[change('pulse',90,100,kind)]);assert.deepEqual(lines(result,'changes'),[`${label} · 맥박: 90 회/분 → 100 회/분 · 기록 시각 ${STAMP}`]);if(kind!=='observation')assert.doesNotMatch(lines(result,'changes')[0],/관찰 변화/);assert.doesNotMatch(result.text,/호전|악화/);
});
test('a new value previously absent is labeled missing before, not numeric zero before',()=>assert.deepEqual(lines(summary({pulse:90},{},[change('pulse',null,90,'dictation')]),'changes'),['기록 갱신 · 맥박: 미기록 → 90 회/분 · 기록 시각 '+STAMP]));
test('an actual deletion is a record removal rather than a zero measurement',()=>{
  const result=summary({}, {pulse:{removed:true}},[change('pulse',90,null,'correction')]);assert.deepEqual(lines(result,'changes'),['입력 수정 · 맥박: 90 회/분 → 값 비움 · 기록 시각 '+STAMP]);assert.equal(result.hasContent,true);
});
test('denied symptom changes retain polarity and an empty list is never generalized',()=>{
  const result=summary({denied_symptoms:[]},{},[change('denied_symptoms',['구토'],[],'correction')]);assert.deepEqual(lines(result,'changes'),['입력 수정 · 부정 증상: 구토 없음 → 빈 목록으로 기록됨 · 기록 시각 '+STAMP]);
});
test('matching multiple entries from one revision are shown together',()=>{
  const result=summary({pulse:90,spo2:97},{},[change('pulse',80,90),change('spo2',96,97)]);assert.equal(lines(result,'changes').length,2);
});
for(const [name,changes]of [
  ['stale current',[change('pulse',80,89)]],['mixed revision kind',[change('pulse',80,90),change('spo2',96,97,'correction')]],['mixed revision time',[change('pulse',80,90),change('spo2',96,97,'observation',T-1)]],['duplicate field',[change('pulse',80,90),change('pulse',70,90)]],['untrusted kind',[change('pulse',80,90,'inference')]],['future revision',[change('pulse',80,90,'observation',T+101)]],['missing record time',[{field:'pulse',previous:80,current:90,change_kind:'observation'}]],['undefined prior',[change('pulse',undefined,90)]],['undefined current',[change('pulse',80,undefined)]],
])test(`untrusted ${name} change log is not presented as a verified change`,()=>assert.deepEqual(lines(summary({pulse:90,spo2:97},{},changes),'changes'),['현재 저장본과 일치하는 최근 변경 기록 없음']));
test('a null latest current cannot claim deletion when the field is still present',()=>assert.deepEqual(lines(summary({pulse:null},{},[change('pulse',80,null)]),'changes'),['현재 저장본과 일치하는 최근 변경 기록 없음']));
test('reconfirming an unchanged numeric value may have no latest_changes while still showing the saved observation',()=>{
  const result=summary({pulse:90},{pulse:{source:'manual',observed_at:T}},[]);assert.equal(lines(result,'observations').length,1);assert.deepEqual(lines(result,'changes'),['현재 저장본과 일치하는 최근 변경 기록 없음']);
});
test('flagged or derived current values are not promoted through the changes section',()=>{
  for(const meta of [{needs_clarification:true},{derived:'r1_reconcile'}])assert.deepEqual(lines(summary({pulse:90},{pulse:meta},[change('pulse',80,90)]),'changes'),['현재 저장본과 일치하는 최근 변경 기록 없음']);
});
test('R1 changes from the same revision do not leak into an otherwise valid direct observation change',()=>{
  const result=summary({pulse:90,pre_ktas:2},{},[change('pulse',80,90),change('pre_ktas',3,2)]);assert.equal(lines(result,'changes').length,1);assert.doesNotMatch(result.text,/pre_ktas|응급도/);
});
test('direction labels are ignored; only literal values identify the record change',()=>{
  const raw=change('pulse',80,90);raw.direction='PRIVATE_INFERRED_WORSENING';assert.doesNotMatch(summary({pulse:90},{},[raw]).text,/PRIVATE_|증가|감소|악화|호전/);
});
test('a current value with newer observation metadata cannot revive an older cached change',()=>{
  const result=summary({pulse:90},{pulse:{observed_at:T+1,change_kind:'correction'}},[change('pulse',80,90)]);assert.deepEqual(lines(result,'changes'),['현재 저장본과 일치하는 최근 변경 기록 없음']);assert.equal(lines(result,'observations').length,1);
});
test('undo may restore older observation metadata while retaining its own explicit rollback label',()=>{
  const result=summary({pulse:90},{pulse:{observed_at:T-100,change_kind:'dictation'}},[change('pulse',100,90,'undo')]);assert.deepEqual(lines(result,'changes'),['되돌림 · 맥박: 100 회/분 → 90 회/분 · 기록 시각 '+STAMP]);
});
test('review state reports only local input review and unsaved/pending presence without their raw content',()=>{
  const result=summary({pulse:90},{},[],{reviewPendingIds:['pulse','blood_pressure'],hasUnapplied:true,hasPendingRequest:true});
  assert.deepEqual(lines(result,'review'),['저장 상태: 초안(DRAFT)','입력 내용 복창 확인 대기: 혈압, 맥박','미반영 내용 있음 · 이 요약에는 포함하지 않음','저장 결과 확인 대기 요청 있음 · 최신 저장 결과 확인 필요']);assert.doesNotMatch(result.text,/확진|최종 확인 완료/);
});
test('a submitted session is a saved registration state, never a hospital acceptance assertion',()=>{
  const input=session({pulse:90});input.state='SUBMITTED';const result=buildHospitalHandoff(input,{now:NOW});assert.ok(lines(result,'review').includes('저장 상태: 등록 반영됨(SUBMITTED)'));assert.doesNotMatch(result.text,/수용 확정|병원 도착/);
});
test('unknown review IDs receive generic labels and cannot inject raw/private text into the summary',()=>{
  const result=summary({pulse:90},{},[],{reviewPendingIds:['PRIVATE_REVIEW_TOKEN']});assert.ok(lines(result,'review').includes('입력 내용 복창 확인 대기: 기타 검토 항목'));assert.doesNotMatch(result.text,/PRIVATE_REVIEW_TOKEN/);
});
test('same underlying data has the same signature when only generation time changes',()=>{
  const input=session({pulse:90,onset_at:T},{pulse:{observed_at:T}});const first=buildHospitalHandoff(input,{now:NOW}),later=buildHospitalHandoff(input,{now:NOW+10000});assert.equal(first.signature,later.signature);assert.notEqual(first.text,later.text);
});
test('saved values, metadata evidence, latest change, review and unsaved/request flags each invalidate the signature',()=>{
  const input=session({pulse:90},{pulse:{evidence:['synthetic original']}}),base=buildHospitalHandoff(input,{now:NOW}).signature;
  const values=clone(input);values.fields.pulse=91;const evidence=clone(input);evidence.field_metadata.pulse.evidence=['PRIVATE_NEW_EVIDENCE'];const changed=clone(input);changed.latest_changes=[change('pulse',80,90)];
  for(const candidate of [buildHospitalHandoff(values,{now:NOW}),buildHospitalHandoff(evidence,{now:NOW}),buildHospitalHandoff(changed,{now:NOW}),buildHospitalHandoff(input,{now:NOW,reviewPendingIds:['pulse']}),buildHospitalHandoff(input,{now:NOW,hasUnapplied:true}),buildHospitalHandoff(input,{now:NOW,hasPendingRequest:true})]){assert.notEqual(candidate.signature,base);assert.doesNotMatch(candidate.text,/PRIVATE_NEW_EVIDENCE/);}
});
test('raw utterance changes and unsupported R1 data cannot change the saved-observation signature',()=>{
  const one=session({pulse:90}),two=clone(one);two.utterances=[{transcript:'PRIVATE_RAW'}];two.fields.pre_ktas=2;two.field_metadata.pre_ktas={evidence:['PRIVATE_R1']};two.baseline_summary={symptoms:'PRIVATE_BASELINE'};
  assert.equal(buildHospitalHandoff(one,{now:NOW}).signature,buildHospitalHandoff(two,{now:NOW}).signature);
});
test('metadata key order and review-ID duplicate ordering do not produce false stale signatures',()=>{
  const a=summary({pulse:90},{pulse:{evidence:['synthetic'],source:'voice'}},[],{reviewPendingIds:['pulse','blood_pressure','pulse']}),b=summary({pulse:90},{pulse:{source:'voice',evidence:['synthetic']}},[],{reviewPendingIds:['blood_pressure','pulse']});assert.equal(a.signature,b.signature);
});
test('patient label, session ID/version/state changes invalidate the signature without exposing unrecognized identifiers',()=>{
  const input=session({pulse:90}),base=buildHospitalHandoff(input,{now:NOW}).signature;
  for(const candidate of [{...input,id:'VS-OTHER'},{...input,version:4},{...input,state:'SUBMITTED'}])assert.notEqual(buildHospitalHandoff(candidate,{now:NOW}).signature,base);
  assert.notEqual(buildHospitalHandoff(input,{now:NOW,patientLabel:'환자02'}).signature,base);
  const invalid=buildHospitalHandoff({...input,id:'bad\nPRIVATE_ID',version:0},{now:NOW});assert.equal(invalid.sessionId,null);assert.equal(invalid.version,null);assert.doesNotMatch(invalid.text,/PRIVATE_ID/);
});
for(const options of [{now:NaN},{now:Infinity},{now:-1},{now:1e16},{patientLabel:'bad\nlabel'},{patientLabel:''},{reviewPendingIds:'pulse'},{reviewPendingIds:[null]},{hasUnapplied:'yes'},{hasPendingRequest:1}])test(`invalid options are explicit errors: ${JSON.stringify(options)}`,()=>assert.throws(()=>buildHospitalHandoff(session({pulse:90}),{now:NOW,...options})));
