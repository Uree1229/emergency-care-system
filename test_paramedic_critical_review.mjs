import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {buildCriticalReview,planCriticalCorrection,classifyCriticalReply} from './paramedic-ui/critical-review.mjs';

const build=(fields={},field_metadata={})=>buildCriticalReview({fields,field_metadata});
const get=(fields,id,metadata={})=>build(fields,metadata).find(item=>item.id===id);
const clone=value=>JSON.parse(JSON.stringify(value));

test('empty, missing and unrelated fields do not imply any finding or absence',()=>{
  for(const session of [null,undefined,{}, {fields:{}},{fields:{age:47,sex:'male',pre_ktas:2}},{fields:null}])assert.deepEqual(buildCriticalReview(session),[]);
});
test('all nine structured critical groups are emitted in a deterministic order, without source mutation',()=>{
  const session={id:'VS-SYNTHETIC',version:3,fields:{systolic_bp:120,diastolic_bp:80,pulse:90,spo2:97,temperature:36.5,respiratory_rate:20,glucose:100,consciousness:'alert',breathing:'normal',symptoms:['흉통'],denied_symptoms:['구토'],uncertain_symptoms:['출혈']},field_metadata:{pulse:{evidence:['synthetic pulse 90']}}};
  const before=clone(session),items=buildCriticalReview(session);
  assert.deepEqual(items.map(item=>item.id),['blood_pressure','pulse','spo2','temperature','respiratory_rate','glucose','consciousness','breathing','symptom_polarity']);
  assert.deepEqual(session,before);assert.ok(items.every(item=>item.editable&&item.confirmable));
  items.at(-1).values.symptoms.push('mutated only in returned item');assert.deepEqual(session,before);
});
for(const [field,value,expected]of [
  ['pulse',90,'맥박 분당 90회'],['spo2',97,'산소포화도 97퍼센트'],['temperature',36.5,'체온 36.5도'],
  ['respiratory_rate',20,'호흡수 분당 20회'],['glucose',100,'혈당 100밀리그램 퍼 데시리터'],
  ['systolic_bp',120,'수축기 혈압 120 밀리미터 수은주. 이완기 혈압 미확인'],
  ['diastolic_bp',80,'수축기 혈압 미확인. 이완기 혈압 80 밀리미터 수은주'],
])test(`structured ${field} is read literally without inferring another measurement`,()=>{assert.equal(build({[field]:value})[0].text,expected);});
test('blood pressure pair is one atomic item with the requested clear labels',()=>{
  const item=get({systolic_bp:120,diastolic_bp:80},'blood_pressure');
  assert.equal(item.text,'수축기 혈압 120, 이완기 혈압 80 밀리미터 수은주');assert.deepEqual(item.fields,['systolic_bp','diastolic_bp']);
});
for(const field of ['systolic_bp','diastolic_bp','pulse','spo2','temperature','respiratory_rate','glucose'])test(`zero ${field} stays a recorded value rather than becoming missing`,()=>{
  const item=build({[field]:0})[0];assert.equal(item.currentValues[field],0);assert.match(item.text,/0/);assert.equal(item.confirmable,true);
});
for(const [field,value,text]of [
  ['consciousness','alert','의식 명료'],['consciousness','impaired','의식 저하'],['consciousness','unresponsive','의식 반응 없음'],['consciousness','unknown','의식 미확인'],
  ['breathing','normal','호흡 정상'],['breathing','abnormal','호흡 비정상'],['breathing','absent','호흡 없음'],['breathing','unknown','호흡 미확인'],
])test(`enum ${field}=${value} preserves its exact meaning`,()=>assert.equal(build({[field]:value})[0].text,text));
test('null numeric/enum values are unknown, never zero, normal, or absent breathing',()=>{
  assert.equal(get({pulse:null},'pulse').text,'맥박 미확인');assert.deepEqual(get({pulse:null},'pulse').currentValues,{});
  assert.equal(get({breathing:null},'breathing').text,'호흡 미확인');assert.doesNotMatch(get({breathing:null},'breathing').text,/정상|없음/);
});
for(const [field,value]of [['pulse','90'],['spo2',false],['temperature',Infinity],['respiratory_rate',NaN],['glucose',{}],['breathing','none'],['consciousness',true]])test(`invalid structured ${field} is not spoken as a verified clinical value`,()=>{
  const item=build({[field]:value})[0];assert.match(item.text,/값 확인 필요/);assert.equal(item.confirmable,false);assert.ok(item.invalidFields.includes(field));assert.deepEqual(item.currentValues,{});
});
test('positive, denied and uncertain symptoms are one item with explicitly different readback',()=>{
  const item=get({symptoms:['흉통'],denied_symptoms:['구토'],uncertain_symptoms:['출혈']},'symptom_polarity');
  assert.equal(item.text,'확인된 증상 흉통. 구토 없음. 출혈 미확인');assert.deepEqual(item.fields,['symptoms','denied_symptoms','uncertain_symptoms']);
});
for(const [field,expected]of [['symptoms','확인된 증상 목록이 비어 있습니다'],['denied_symptoms','없다고 확인한 증상 목록이 비어 있습니다'],['uncertain_symptoms','미확인·의심 증상 목록이 비어 있습니다']])test(`an explicit empty ${field} is never generalized to all symptoms absent`,()=>{
  const item=get({[field]:[]},'symptom_polarity');assert.equal(item.text,expected);assert.doesNotMatch(item.text,/모든.*없|증상 없음/);assert.deepEqual(item.values,{[field]:[]});
});
test('an absent denied list does not manufacture any denial from a positive list',()=>{const item=get({symptoms:['흉통']},'symptom_polarity');assert.equal(item.text,'확인된 증상 흉통');assert.equal(Object.hasOwn(item.values,'denied_symptoms'),false);});
for(const value of ['구토',null,[null],['']])test(`malformed symptom list ${JSON.stringify(value)} cannot be confirmed`,()=>{const item=get({denied_symptoms:value},'symptom_polarity');assert.equal(item.confirmable,false);assert.match(item.text,/값 확인 필요/);});
test('contradictory symptom polarities are identified without speaking them both as current truth',()=>{
  const item=get({symptoms:['출혈'],denied_symptoms:['출혈']},'symptom_polarity');assert.equal(item.confirmable,false);assert.match(item.text,/분류가 충돌/);assert.doesNotMatch(item.text,/출혈 없음/);
});
for(const [field,value]of [['pulse',90],['breathing','normal'],['denied_symptoms',['구토']]])test(`needs_clarification ${field} keeps the previous value separate and prevents confirmation`,()=>{
  const item=build({[field]:value},{[field]:{needs_clarification:true,clarification_evidence:['synthetic change']}})[0];
  assert.equal(item.confirmable,false);assert.equal(item.needsClarification,true);assert.deepEqual(item.currentValues,{});assert.deepEqual(item.previousValues,{[field]:value});
  assert.match(item.text,/재확인 필요/);assert.doesNotMatch(item.text,/90|호흡 정상|구토 없음/);
});
test('only the flagged half of a blood pressure pair is withheld from current readback',()=>{
  const item=get({systolic_bp:120,diastolic_bp:80},'blood_pressure',{systolic_bp:{needs_clarification:true}});
  assert.equal(item.text,'수축기 혈압 재확인 필요. 이완기 혈압 80 밀리미터 수은주');assert.deepEqual(item.currentValues,{diastolic_bp:80});assert.deepEqual(item.previousValues,{systolic_bp:120});
});
test('a clarification flag with no stored value still prompts review and never fills the missing value',()=>{
  const item=build({}, {pulse:{needs_clarification:true}})[0];assert.equal(item.text,'맥박 재확인 필요');assert.deepEqual(item.values,{});assert.equal(item.confirmable,false);
});
test('metadata without a clarification flag does not invent an absent measurement',()=>assert.deepEqual(build({}, {pulse:{evidence:['synthetic old source']}}),[]));
test('inherited fields and metadata are not treated as server-provided observations',()=>assert.deepEqual(build(Object.create({pulse:90}),Object.create({pulse:{needs_clarification:true}})),[]));
test('the signature changes for current value, clarification, original evidence and observation changes',()=>{
  const base=get({pulse:90},'pulse',{pulse:{evidence:['synthetic source'],observed_at:1}}).signature;
  for(const item of [get({pulse:91},'pulse',{pulse:{evidence:['synthetic source'],observed_at:1}}),get({pulse:90},'pulse',{pulse:{evidence:['synthetic new source'],observed_at:1}}),get({pulse:90},'pulse',{pulse:{evidence:['synthetic source'],observed_at:2}}),get({pulse:90},'pulse',{pulse:{evidence:['synthetic source'],observed_at:1,needs_clarification:true}})])assert.notEqual(item.signature,base);
});
test('unrelated measurements, global version, utterance history and metadata do not invalidate an item signature',()=>{
  const one={id:'VS-SYNTHETIC',version:1,fields:{pulse:90,temperature:36},field_metadata:{pulse:{evidence:['synthetic pulse']},temperature:{evidence:['synthetic temp']}}};
  const two=clone(one);two.version=10;two.fields.temperature=37;two.utterances=[{transcript:'synthetic unrelated history'}];two.field_metadata.temperature.evidence=['changed unrelated source'];
  assert.equal(buildCriticalReview(one).find(item=>item.id==='pulse').signature,buildCriticalReview(two).find(item=>item.id==='pulse').signature);
});
test('signature is stable across metadata object key order but distinguishes number/string and missing/null',()=>{
  assert.equal(get({pulse:90},'pulse',{pulse:{source:'voice',evidence:['synthetic']}}).signature,get({pulse:90},'pulse',{pulse:{evidence:['synthetic'],source:'voice'}}).signature);
  assert.notEqual(get({pulse:90},'pulse').signature,get({pulse:'90'},'pulse').signature);
  assert.notEqual(get({systolic_bp:120},'blood_pressure').signature,get({systolic_bp:120,diastolic_bp:null},'blood_pressure').signature);
});
test('any symptom polarity group evidence/value change invalidates that group alone',()=>{
  const first=build({pulse:90,symptoms:['흉통'],denied_symptoms:['구토']});const second=build({pulse:90,symptoms:['흉통'],denied_symptoms:['구토']},{uncertain_symptoms:{evidence:['synthetic source change']}});
  assert.notEqual(first.find(i=>i.id==='symptom_polarity').signature,second.find(i=>i.id==='symptom_polarity').signature);assert.equal(first.find(i=>i.id==='pulse').signature,second.find(i=>i.id==='pulse').signature);
});

test('blood pressure correction prepares one atomic two-field patch and leaves inputs unchanged',()=>{
  const item=get({systolic_bp:120,diastolic_bp:80},'blood_pressure'),before=clone(item),values={systolic_bp:'110',diastolic_bp:'70'};
  assert.deepEqual(planCriticalCorrection(item,values),{changes:{systolic_bp:110,diastolic_bp:70},unset_fields:[]});assert.deepEqual(item,before);assert.deepEqual(values,{systolic_bp:'110',diastolic_bp:'70'});
});
test('partial correction preserves omitted companion fields and emits only actual changes',()=>{
  const item=get({systolic_bp:120,diastolic_bp:80},'blood_pressure');assert.deepEqual(planCriticalCorrection(item,{systolic_bp:110}),{changes:{systolic_bp:110},unset_fields:[]});assert.deepEqual(planCriticalCorrection(item,{systolic_bp:'120'}),{changes:{},unset_fields:[]});
});
for(const raw of ['', '  ', '미확인',null])test(`numeric ${JSON.stringify(raw)} explicitly unsets instead of becoming zero`,()=>assert.deepEqual(planCriticalCorrection(get({pulse:90},'pulse'),{pulse:raw}),{changes:{},unset_fields:['pulse']}));
test('blanking a missing companion produces no invented unset or numeric zero',()=>assert.deepEqual(planCriticalCorrection(get({systolic_bp:120},'blood_pressure'),{diastolic_bp:''}),{changes:{},unset_fields:[]}));
for(const raw of [0,'0',' 0 ',36.5,'36.5','-1','1e3'])test(`finite numeric edit ${JSON.stringify(raw)} is passed on without medical threshold inference`,()=>{
  const result=planCriticalCorrection(get({temperature:20},'temperature'),{temperature:raw});assert.deepEqual(result,{changes:{temperature:Number(raw)},unset_fields:[]});
});
for(const raw of [NaN,Infinity,-Infinity,true,{},[],undefined,'90 bpm','0x10','one'])test(`numeric edit rejects malformed type ${String(raw)}`,()=>assert.throws(()=>planCriticalCorrection(get({pulse:90},'pulse'),{pulse:raw})));
test('explicitly re-entering a flagged previous value sends a real patch so the server can clear its flag',()=>{
  const item=get({pulse:90},'pulse',{pulse:{needs_clarification:true}});assert.deepEqual(planCriticalCorrection(item,{pulse:'90'}),{changes:{pulse:90},unset_fields:[]});
});
test('explicitly blanking a flagged but value-less field still emits unset to clear its clarification metadata',()=>{
  const item=build({}, {pulse:{needs_clarification:true}})[0];assert.deepEqual(planCriticalCorrection(item,{pulse:'미확인'}),{changes:{},unset_fields:['pulse']});
});
for(const raw of ['unknown','미확인'])test(`enum ${raw} is explicit unknown, distinct from deleting the record`,()=>assert.deepEqual(planCriticalCorrection(get({breathing:'normal'},'breathing'),{breathing:raw}),{changes:{breathing:'unknown'},unset_fields:[]}));
test('blank enum removes the field while normal/absent retain their meanings',()=>{
  const item=get({breathing:'normal'},'breathing');assert.deepEqual(planCriticalCorrection(item,{breathing:''}),{changes:{},unset_fields:['breathing']});assert.deepEqual(planCriticalCorrection(item,{breathing:'absent'}),{changes:{breathing:'absent'},unset_fields:[]});
});
for(const [field,value]of [['consciousness','normal'],['breathing','alert'],['breathing','없음'],['breathing',false]])test(`cross-enum or implicit value ${field}/${value} is rejected`,()=>assert.throws(()=>planCriticalCorrection(build({[field]:'unknown'})[0],{[field]:value})));
test('symptom correction atomically moves a finding between positive and denied lists',()=>{
  const item=get({symptoms:['출혈'],denied_symptoms:['구토'],uncertain_symptoms:['흉통']},'symptom_polarity'),before=clone(item);
  const result=planCriticalCorrection(item,{symptoms:[],denied_symptoms:['구토','출혈'],uncertain_symptoms:['흉통']});
  assert.deepEqual(result,{changes:{symptoms:[],denied_symptoms:['구토','출혈']},unset_fields:[]});assert.deepEqual(item,before);
});
test('comma/newline manual list editing accepts only explicit tokens without guessing a polarity',()=>{
  const item=get({symptoms:[]},'symptom_polarity');assert.deepEqual(planCriticalCorrection(item,{symptoms:' 흉통, 두통\n 발열 '}),{changes:{symptoms:['흉통','두통','발열']},unset_fields:[]});
});
test('blank list removes its record while [] explicitly retains an empty list',()=>{
  const item=get({denied_symptoms:['구토']},'symptom_polarity');assert.deepEqual(planCriticalCorrection(item,{denied_symptoms:''}),{changes:{},unset_fields:['denied_symptoms']});assert.deepEqual(planCriticalCorrection(item,{denied_symptoms:[]}),{changes:{denied_symptoms:[]},unset_fields:[]});
});
for(const values of [{denied_symptoms:['출혈']},{uncertain_symptoms:['출혈']},{symptoms:['구토']},{symptoms:['출혈'],uncertain_symptoms:['출혈']}])test(`contradictory atomic symptom edit fails: ${JSON.stringify(values)}`,()=>{
  const item=get({symptoms:['출혈'],denied_symptoms:['구토']},'symptom_polarity');assert.throws(()=>planCriticalCorrection(item,values),/동시에/);
});
for(const raw of [['구토','구토'],[null],[''],false,42,'구토,'])test(`invalid or duplicate list edit is not silently normalized away: ${JSON.stringify(raw)}`,()=>assert.throws(()=>planCriticalCorrection(get({symptoms:[]},'symptom_polarity'),{symptoms:raw})));
for(const field of ['age','serverOrigin','confirmed','patient_id','pre_ktas','denied_symptoms'])test(`single-item correction cannot mutate unrelated ${field}`,()=>assert.throws(()=>planCriticalCorrection(get({pulse:90},'pulse'),{[field]:1})));
test('a forged item field allowlist cannot expand the real supported group',()=>{
  const item=get({pulse:90},'pulse');item.fields=['age'];assert.throws(()=>planCriticalCorrection(item,{age:47}));assert.throws(()=>planCriticalCorrection({...item,id:'age'},{age:47}));
});
test('unsafe prototype keys, accessors and non-plain correction objects are rejected before use',()=>{
  const item=get({pulse:90},'pulse'),getter={};Object.defineProperty(getter,'pulse',{enumerable:true,get(){throw Error('getter must not run');}});
  for(const values of [JSON.parse('{"__proto__":{}}'),getter,[],null,new Date()])assert.throws(()=>planCriticalCorrection(item,values));
});
test('a plain correction object from a browser VM realm is supported',()=>assert.deepEqual(planCriticalCorrection(get({pulse:90},'pulse'),vm.runInNewContext('({pulse:"91"})')),{changes:{pulse:91},unset_fields:[]}));

for(const phrase of ['네','예','응','맞아','맞아요','맞습니다','확인했어','확인했어요','확인했습니다','네 맞아요','예 맞습니다',' 맞아요. ','예。'])test(`dedicated confirmation context accepts exact affirmative: ${phrase}`,()=>assert.equal(classifyCriticalReply(phrase),'confirm'));
for(const phrase of ['아니','아니요','아니오','아니야','틀렸어','틀렸어요','틀렸습니다','수정','수정할게요','수정하겠습니다','고칠게요','아니요.'])test(`dedicated confirmation context accepts exact correction: ${phrase}`,()=>assert.equal(classifyCriticalReply(phrase),'correct'));
for(const phrase of ['네 혈압90','맞지않아요','맞지 않아요','맞아요라고 환자가 말함','종료','중단','병원 추천해줘','맥박 90 맞아요','아니요 혈압은 80','네 그런데 호흡이 없어요','환자가 네라고 했어요','맞아요?','맞아요...','','모르겠어요',null,undefined,42])test(`clinical additions, quotations, negations and controls never blanket-confirm: ${String(phrase)}`,()=>assert.equal(classifyCriticalReply(phrase),'unknown'));
