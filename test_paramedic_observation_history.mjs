import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {historySnapshotSignature,buildObservationHistory} from './paramedic-ui/observation-history.mjs';

const SID='VS-SYNTHETIC-HISTORY',T=1_700_000_000,NOW=(T+100)*1000,STAMP='2023-11-15 07:13:20 KST';
const clone=value=>JSON.parse(JSON.stringify(value));
function fixture(steps=[{fields:{pulse:90}}],initial={fields:{pulse:80},metadata:{}}){
  const rows=[];let fields=clone(initial.fields||{}),metadata=clone(initial.metadata||{});
  for(const [index,step]of steps.entries()){
    const nextFields=clone(step.fields),nextMetadata=clone(step.metadata||{});
    rows.push({id:10+index*3,session_id:SID,version:index+2,kind:step.kind||'observation',recorded_at:step.time??T+index,before_fields:clone(fields),after_fields:clone(nextFields),before_metadata:clone(metadata),after_metadata:clone(nextMetadata),warnings:step.warnings||[]});fields=nextFields;metadata=nextMetadata;
  }
  return {session:{id:SID,version:rows.length+1,state:'DRAFT',fields:clone(fields),field_metadata:clone(metadata)},response:{session_id:SID,revisions:rows}};
}
const run=(data,options={})=>buildObservationHistory(data.session,data.response,{now:NOW,...options});
function rejected(data){const before=clone(data);assert.throws(()=>run(data),error=>error.code==='INVALID_OBSERVATION_HISTORY');assert.deepEqual(data,before);}

test('the exact result and row contract binds the live snapshot and formats record input time in KST',()=>{
  const data=fixture(),result=run(data);
  assert.deepEqual(Object.keys(result),['sourceSignature','total','shown','entries','hasEntries']);assert.equal(result.sourceSignature,historySnapshotSignature(data.session));assert.equal(result.total,1);assert.equal(result.shown,1);assert.equal(result.hasEntries,true);
  assert.deepEqual(result.entries,[{id:10,version:2,kindLabel:'관찰 변화 입력',recordedAt:STAMP,changes:['맥박: 80 회/분 → 90 회/분']}]);assert.match(result.sourceSignature,/^history-v1-[0-9a-f]{32}$/);
});
test('entries are newest-first but accept globally non-contiguous SQL IDs and same-second records',()=>{
  const data=fixture([{fields:{pulse:90},time:T},{fields:{pulse:91},time:T},{fields:{pulse:92},time:T}]);const result=run(data);assert.deepEqual(result.entries.map(row=>row.id),[16,13,10]);assert.deepEqual(result.entries.map(row=>row.version),[4,3,2]);assert.equal(new Set(result.entries.map(row=>row.recordedAt)).size,1);
});
test('source and response are immutable, and result arrays are detached',()=>{
  const data=fixture(),before=clone(data),result=run(data);result.entries[0].changes.push('mutated result only');assert.deepEqual(data,before);assert.equal(run(data).entries[0].changes.length,1);
});
test('version-one baseline sessions legitimately have no revisions, even with saved baseline fields',()=>{
  const data=fixture([],{fields:{age:47,pulse:90},metadata:{pulse:{source:'patient_record',change_kind:'baseline'}}});assert.deepEqual(run(data),{sourceSignature:historySnapshotSignature(data.session),total:0,shown:0,entries:[],hasEntries:false});
});
test('an empty ordinary version-one session also has an empty history',()=>assert.equal(run(fixture([],{fields:{},metadata:{}})).hasEntries,false));
test('default display limit is twenty without silently truncating total history',()=>{
  const data=fixture(Array.from({length:25},(_,index)=>({fields:{pulse:80+index}}))),result=run(data);assert.equal(result.total,25);assert.equal(result.shown,20);assert.equal(result.entries[0].version,26);assert.equal(result.entries.at(-1).version,7);
});
test('explicit smaller limits still validate complete history and report the full total',()=>{
  const data=fixture(Array.from({length:4},(_,index)=>({fields:{pulse:81+index}}))),result=run(data,{limit:2});assert.equal(result.total,4);assert.equal(result.shown,2);assert.deepEqual(result.entries.map(row=>row.version),[5,4]);
});
test('corruption outside the displayed last twenty records is not skipped',()=>{
  const data=fixture(Array.from({length:25},(_,index)=>({fields:{pulse:80+index}})));data.response.revisions[0].recorded_at=T+101;rejected(data);
});
for(const [kind,label]of [['observation','관찰 변화 입력'],['dictation','기록 입력'],['correction','입력 수정'],['undo','되돌림'],['followup_answer','질문 답변']])test(`revision kind ${kind} is explicitly labeled ${label}`,()=>{
  const result=run(fixture([{fields:{pulse:90},kind}]));assert.equal(result.entries[0].kindLabel,label);assert.doesNotMatch(JSON.stringify(result.entries),/호전|악화|응급도|KTAS|새 측정/);
});
for(const [field,before,after,expected]of [
  ['systolic_bp',120,100,'수축기 혈압: 120 mmHg → 100 mmHg'],['diastolic_bp',80,60,'이완기 혈압: 80 mmHg → 60 mmHg'],['pulse',90,100,'맥박: 90 회/분 → 100 회/분'],['spo2',96,97,'산소포화도: 96 % → 97 %'],['temperature',36.5,37,'체온: 36.5 ℃ → 37 ℃'],['respiratory_rate',20,22,'호흡수: 20 회/분 → 22 회/분'],['glucose',100,110,'혈당: 100 mg/dL → 110 mg/dL'],['age',47,48,'나이: 47 세 → 48 세'],['age_group','40대','50대','연령대: 40대 → 50대'],
])test(`${field} history preserves literal old/new values and units`,()=>assert.deepEqual(run(fixture([{fields:{[field]:after}}],{fields:{[field]:before},metadata:{}})).entries[0].changes,[expected]));
for(const field of ['pulse','spo2','temperature','respiratory_rate','glucose','systolic_bp','diastolic_bp','age'])test(`${field} zero is not confused with missing`,()=>{
  const result=run(fixture([{fields:{[field]:0}}],{fields:{},metadata:{}}));assert.match(result.entries[0].changes[0],/미기록 → 0 /);
});
test('initial positive symptoms, explicit denials and uncertain symptoms remain distinct',()=>{
  const result=run(fixture([{fields:{symptoms:['흉통'],denied_symptoms:['구토'],uncertain_symptoms:['출혈']}}],{fields:{},metadata:{}}));assert.deepEqual(result.entries[0].changes,['확인된 증상: 미기록 → 흉통','부정 증상: 미기록 → 구토 없음','미확인·의심 증상: 미기록 → 출혈 미확인']);
});
for(const [field,label]of [['symptoms','확인된 증상'],['denied_symptoms','부정 증상'],['uncertain_symptoms','미확인·의심 증상']])test(`empty ${field} is not reported as all symptoms absent`,()=>{
  const result=run(fixture([{fields:{[field]:[]}}],{fields:{[field]:['구토']},metadata:{}}));assert.match(result.entries[0].changes[0],/빈 목록으로 기록됨/);assert.doesNotMatch(result.entries[0].changes[0],/모든 증상 없음/);assert.ok(result.entries[0].changes[0].startsWith(label+':'));
});
test('normal, absent and unknown breathing remain separate historical states',()=>{
  const data=fixture([{fields:{breathing:'absent'}},{fields:{breathing:'unknown'}}],{fields:{breathing:'normal'},metadata:{}});assert.deepEqual(run(data).entries.map(row=>row.changes[0]),['호흡: 없음 → 미확인','호흡: 정상 → 없음']);
});
test('removing a value is a deletion/unknown status rather than a zero or negative finding',()=>{
  const result=run(fixture([{fields:{},metadata:{pulse:{removed:true}},kind:'correction'}]));assert.deepEqual(result.entries[0].changes,['맥박: 80 회/분 → 삭제된 기록 · 현재 미확인']);
});
test('a flagged preserved value is explicitly previous observation with current value unknown',()=>{
  const result=run(fixture([{fields:{pulse:80},metadata:{pulse:{needs_clarification:true}}}]));assert.deepEqual(result.entries[0].changes,['맥박: 80 회/분 → 재확인 필요 · 이전 관찰 80 회/분 · 현재 미확인']);
});
test('metadata-only clarification removal is visible even when both numeric snapshots are equal',()=>{
  const result=run(fixture([{fields:{pulse:80},kind:'correction'}],{fields:{pulse:80},metadata:{pulse:{needs_clarification:true}}}));assert.deepEqual(result.entries[0].changes,['맥박: 재확인 필요 · 이전 관찰 80 회/분 · 현재 미확인 → 80 회/분']);
});
test('a clarification-only missing field never invents a prior observation',()=>{
  const result=run(fixture([{fields:{},metadata:{pulse:{needs_clarification:true}}}],{fields:{},metadata:{}}));assert.deepEqual(result.entries[0].changes,['맥박: 미기록 → 재확인 필요 · 현재 미확인']);
});
test('baseline patient-record provenance is historical until explicitly re-entered',()=>{
  const result=run(fixture([{fields:{pulse:80},kind:'correction'}],{fields:{pulse:80},metadata:{pulse:{source:'patient_record',change_kind:'baseline'}}}));assert.deepEqual(result.entries[0].changes,['맥박: 이전 기록 80 회/분 · 현재 미확인 → 80 회/분']);
});
test('derived values are excluded as direct observations even when they change',()=>{
  const result=run(fixture([{fields:{consciousness:'impaired',pre_ktas:2},metadata:{consciousness:{derived:'r1_reconcile'}}}],{fields:{consciousness:'alert',pre_ktas:3},metadata:{consciousness:{derived:'r1_reconcile'}}}));assert.deepEqual(result.entries[0].changes,['관찰 값·재확인 상태 변경 없음']);assert.doesNotMatch(JSON.stringify(result.entries),/pre_ktas|명료|저하|응급도/);
});
test('a derived-to-direct provenance change does not present the earlier derived value as a fact',()=>{
  const result=run(fixture([{fields:{consciousness:'alert'},kind:'correction'}],{fields:{consciousness:'alert'},metadata:{consciousness:{derived:'r1_reconcile'}}}));assert.deepEqual(result.entries[0].changes,['의식: 파생 항목으로 직접 관찰에서 제외 → 명료']);
});
test('unavailable-only legacy status is displayed as unresolved even without its old clarification bit',()=>{
  const result=run(fixture([{fields:{pulse:80},metadata:{pulse:{followup_unavailable_for:'pulse'}},kind:'followup_answer'}]));assert.match(result.entries[0].changes[0],/이전 관찰 80 회\/분 · 현재 미확인 · 미측정·확인 불가로 보류/);
});
test('review-required semantic interpretation status is visible without quoting its raw interpretation note',()=>{
  const result=run(fixture([{fields:{pulse:80},metadata:{pulse:{review_required:true,interpretation_notes:['PRIVATE_NOTE']}},kind:'followup_answer'}]));assert.match(result.entries[0].changes[0],/해석 검토 필요/);assert.doesNotMatch(JSON.stringify(result),/PRIVATE_NOTE/);
});
test('a same-value timestamp/evidence-only revision has an explicit no-value-or-review-change row',()=>{
  const result=run(fixture([{fields:{pulse:80},metadata:{pulse:{observed_at:T,evidence:['PRIVATE_NEW']}},kind:'correction'}],{fields:{pulse:80},metadata:{pulse:{observed_at:T-1,evidence:['PRIVATE_OLD']}}}));assert.deepEqual(result.entries[0].changes,['관찰 값·재확인 상태 변경 없음']);
});
test('undo restores older observation metadata at a new record-input timestamp, not a new measurement',()=>{
  const result=run(fixture([{fields:{pulse:80},metadata:{pulse:{observed_at:T-100,change_kind:'dictation'}},kind:'undo'}],{fields:{pulse:90},metadata:{pulse:{observed_at:T-10}}}));assert.equal(result.entries[0].recordedAt,STAMP);assert.equal(result.entries[0].kindLabel,'되돌림');assert.match(result.entries[0].changes[0],/관찰 입력 시각 2023-11-15 07:11:40 KST/);assert.doesNotMatch(JSON.stringify(result.entries),/새 측정|호전|악화/);
});
test('revision kind is authoritative even when _observation stored field metadata kind observation for dictation',()=>{
  const result=run(fixture([{fields:{pulse:90},metadata:{pulse:{change_kind:'observation',observed_at:T}},kind:'dictation'}]));assert.equal(result.entries[0].kindLabel,'기록 입력');assert.match(result.entries[0].changes[0],/80 회\/분 → 90 회\/분/);
});
test('onset is its saved occurrence time, distinct from the entry record time',()=>{
  const result=run(fixture([{fields:{onset_at:T-60}}],{fields:{},metadata:{}}));assert.equal(result.entries[0].recordedAt,STAMP);assert.deepEqual(result.entries[0].changes,['증상 발생 시각: 미기록 → 2023-11-15 07:12:20 KST']);
});
test('raw transcripts, evidence, warnings and derived/R1 extras are never returned in rows or signatures',()=>{
  const data=fixture([{fields:{pulse:90,pre_ktas:2},metadata:{pulse:{evidence:['PRIVATE_EVIDENCE']}},warnings:['PRIVATE_WARNING']}]);data.session.utterances=[{transcript:'PRIVATE_TRANSCRIPT'}];data.response.revisions[0].raw_transcript='PRIVATE_EXTRA';const result=run(data);assert.doesNotMatch(JSON.stringify(result),/PRIVATE_|pre_ktas|KTAS|호전|악화/);
});
test('last live metadata may contain legacy unavailable hydration absent from the revision snapshot',()=>{
  const data=fixture();data.session.field_metadata={pulse:{followup_unavailable_for:'pulse'}};assert.equal(run(data).sourceSignature,historySnapshotSignature(data.session));assert.equal(run(data).hasEntries,true);
});
test('legacy hydrated before-metadata can differ from previous after-metadata without breaking the fields chain',()=>{
  const data=fixture([{fields:{pulse:80}},{fields:{pulse:90},kind:'followup_answer'}]);data.response.revisions[1].before_metadata={pulse:{followup_unavailable_for:'pulse'}};assert.equal(run(data).entries.length,2);assert.match(run(data).entries[0].changes[0],/확인 불가로 보류 → 90/);
});
test('epoch zero is a legitimate record timestamp and is rendered in fixed KST',()=>{
  const data=fixture([{fields:{pulse:90},time:0}]);assert.equal(run(data,{now:0}).entries[0].recordedAt,'1970-01-01 09:00:00 KST');
});

for(const [name,mutate]of [
  ['wrong response SID',d=>d.response.session_id='VS-OTHER'],['wrong row SID',d=>d.response.revisions[0].session_id='VS-OTHER'],['wrong current fields',d=>d.session.fields.pulse=91],['empty noninitial history',d=>d.response.revisions=[]],['future recorded time',d=>d.response.revisions[0].recorded_at=T+101],['negative recorded time',d=>d.response.revisions[0].recorded_at=-1],['fractional recorded time',d=>d.response.revisions[0].recorded_at=T+0.5],['string recorded time',d=>d.response.revisions[0].recorded_at=String(T)],['zero SQL ID',d=>d.response.revisions[0].id=0],['string SQL ID',d=>d.response.revisions[0].id='10'],['wrong first version',d=>d.response.revisions[0].version=1],['future version',d=>d.response.revisions[0].version=3],['unknown kind',d=>d.response.revisions[0].kind='diagnosis'],['missing snapshot',d=>delete d.response.revisions[0].after_fields],['array fields',d=>d.response.revisions[0].after_fields=[]],['null metadata',d=>d.response.revisions[0].after_metadata=null],['nonstring warnings',d=>d.response.revisions[0].warnings=[1]],['object warnings',d=>d.response.revisions[0].warnings={}],
])test(`history rejects ${name} rather than silently skipping it`,()=>{const data=fixture();mutate(data);rejected(data);});
for(const [name,mutate]of [
  ['duplicate SQL ID',d=>d.response.revisions[1].id=10],['decreasing SQL ID',d=>d.response.revisions[1].id=9],['duplicate version',d=>d.response.revisions[1].version=2],['version gap',d=>d.response.revisions[1].version=4],['decreasing clock',d=>d.response.revisions[1].recorded_at=T-1],['broken field snapshot chain',d=>d.response.revisions[1].before_fields.pulse=999],['missing last revision',d=>d.response.revisions.pop()],
])test(`multiple revisions reject ${name}`,()=>{const data=fixture([{fields:{pulse:90}},{fields:{pulse:91}}]);mutate(data);rejected(data);});
for(const [name,mutate]of [
  ['unsafe session ID',s=>s.id='VS/bad'],['bad state',s=>s.state='UNKNOWN'],['zero version',s=>s.version=0],['fractional version',s=>s.version=1.5],['missing fields',s=>delete s.fields],['array metadata',s=>s.field_metadata=[]],['nonJSON saved field',s=>s.fields.pulse=NaN],
])test(`invalid live source ${name} cannot produce a signature or bind history`,()=>{const data=fixture();mutate(data.session);assert.throws(()=>historySnapshotSignature(data.session));assert.throws(()=>run(data));});
for(const limit of [0,21,-1,1.5,'20',null])test(`invalid display limit ${String(limit)} is explicit failure`,()=>assert.throws(()=>run(fixture(),{limit})));
for(const now of [-1,NaN,Infinity,'0',1e16])test(`invalid current clock ${String(now)} is explicit failure`,()=>assert.throws(()=>run(fixture(),{now})));
test('sparse, accessor and symbol-bearing arrays cannot hide corruption outside displayed rows',()=>{
  const sparse=fixture();sparse.response.revisions.length=2;
  const getter=fixture();Object.defineProperty(getter.response.revisions,0,{enumerable:true,get(){throw Error('must not invoke');}});
  const symbol=fixture();symbol.response.revisions[Symbol('hidden')]=1;
  for(const data of [sparse,getter,symbol])assert.throws(()=>run(data));
});
test('unsafe nested JSON/prototype/accessor fields are rejected without invoking record getters',()=>{
  const malicious=fixture();malicious.response.revisions[0].before_fields=JSON.parse('{"__proto__":{}}');
  const getter=fixture();Object.defineProperty(getter.response.revisions[0],'after_fields',{enumerable:true,get(){throw Error('must not invoke');}});
  const date=fixture();date.response.revisions[0].before_metadata={pulse:new Date()};
  for(const data of [malicious,getter,date])assert.throws(()=>run(data));
});
test('ordinary cross-realm browser objects remain valid',()=>{
  const data=vm.runInNewContext('('+JSON.stringify(fixture())+')');assert.equal(run(data).shown,1);
});
test('signature includes all saved values, metadata, version, state and SID without echoing metadata raw text',()=>{
  const data=fixture(),base=historySnapshotSignature(data.session);const variations=[{...data.session,id:'VS-OTHER'},{...data.session,version:3},{...data.session,state:'SUBMITTED'},{...data.session,fields:{pulse:91}},{...data.session,fields:{...data.session.fields,pre_ktas:2}},{...data.session,field_metadata:{pulse:{evidence:['PRIVATE_CHANGED']}}}];
  for(const item of variations){const signature=historySnapshotSignature(item);assert.notEqual(signature,base);assert.doesNotMatch(signature,/PRIVATE_|VS-OTHER|pulse/);}
});
test('unrelated raw utterances, confirmation fields, updated_at and warnings do not alter source signature',()=>{
  const data=fixture(),base=historySnapshotSignature(data.session);data.session.utterances=[{transcript:'PRIVATE_RAW'}];data.session.updated_at=T+10;data.session.confirmed=true;data.session.confirmed_at=T;data.session.warnings=['PRIVATE_WARNING'];assert.equal(historySnapshotSignature(data.session),base);
});
test('canonical metadata/field key ordering and passage of generation time do not falsely stale the snapshot',()=>{
  const data=fixture([{fields:{pulse:90,spo2:97},metadata:{pulse:{source:'voice',evidence:['synthetic']}}}]);const alternate={...data.session,fields:{spo2:97,pulse:90},field_metadata:{pulse:{evidence:['synthetic'],source:'voice'}}};assert.equal(historySnapshotSignature(alternate),historySnapshotSignature(data.session));assert.equal(run(data).sourceSignature,run(data,{now:NOW+10000}).sourceSignature);
});
test('generic errors do not expose private transcript/evidence values',()=>{
  const data=fixture();data.response.revisions[0].session_id='PRIVATE_WRONG_SID';let error;try{run(data);}catch(caught){error=caught;}assert.ok(error);assert.doesNotMatch(error.message,/PRIVATE_|VS-SYNTHETIC|pulse|80/);
});
