import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {
  PRACTICE_KEY,PRACTICE_CASES,PRACTICE_LIMITS,PRACTICE_ACTIONS,
  selectPracticeCases,createPracticeAttempt,emptyPracticeDataset,appendPracticeAttempt,
  annotatePracticeAttempt,scorePracticeAttempts,readPracticeDataset,writePracticeDataset,
  importPracticeJSON,mergePracticeDatasets,exportPracticeJSON,exportPracticeCSV,
} from '../../paramedic-ui/command-practice-data.mjs';

const NOW=1793400000000;
const copy=value=>JSON.parse(JSON.stringify(value));
function input(extra={}){return {id:'P-01',source:'human_voice',trialNo:1,rawText:'종료',fragments:['종료'],status:'ok',timing:{startedAt:NOW-1000,endedAt:NOW},referenceText:'종료',referenceConfirmed:true,expectedAction:'stop',...extra};}
const attempt=extra=>createPracticeAttempt(input(extra),{now:NOW});
const dataset=(...attempts)=>({schema:1,purpose:'synthetic_command_practice',actualAudioSaved:false,attempts});
function storage(initial={}){
  const values=new Map(Object.entries(initial)),calls=[];
  return {values,calls,getItem(key){calls.push(['get',key]);return values.get(key)??null;},setItem(key,value){calls.push(['set',key,value]);values.set(key,value);},removeItem(key){calls.push(['remove',key]);values.delete(key);}};
}
function invalid(fn,code='INVALID_PRACTICE_DATA'){assert.throws(fn,error=>error.code===code);}
function csvRows(raw){
  const rows=[];let row=[],field='',quote=false;const text=raw.replace(/^\uFEFF/u,'');
  for(let i=0;i<text.length;i++){const ch=text[i];if(quote){if(ch==='"'){if(text[i+1]==='"'){field+='"';i++;}else quote=false;}else field+=ch;}
    else if(ch==='"')quote=true;else if(ch===','){row.push(field);field='';}else if(ch==='\r'&&text[i+1]==='\n'){row.push(field);rows.push(row);row=[];field='';i++;}else field+=ch;}
  assert.equal(quote,false);assert.equal(field,'');return rows;
}

test('dedicated key and bounded local schema cannot collide with patient recovery keys',()=>{
  assert.equal(PRACTICE_KEY,'paramedic.voice.command-practice.v1');assert.deepEqual(PRACTICE_ACTIONS,['stop','recommend','new_patient','previous_patient','handoff_summary']);
  assert.deepEqual(PRACTICE_LIMITS,{attempts:500,bytes:4194304,attemptBytes:131072,text:16000,fragments:256,notes:2000});
  assert.deepEqual(emptyPracticeDataset(),{schema:1,purpose:'synthetic_command_practice',actualAudioSaved:false,attempts:[]});
});
test('96 frozen case labels are authored independently and contain every command and negative family',()=>{
  assert.equal(PRACTICE_CASES.length,96);assert.ok(Object.isFrozen(PRACTICE_CASES));assert.ok(PRACTICE_CASES.every(Object.isFrozen));assert.equal(new Set(PRACTICE_CASES.map(value=>value.id)).size,96);
  assert.deepEqual(PRACTICE_CASES.find(value=>value.id==='STOP-01'),{id:'STOP-01',family:'stop',title:'짧은 종료',referenceText:'종료',expectedAction:'stop'});
  for(const family of ['stop','recommend','new_patient','previous_patient','handoff_summary','negation','quoted_reported','clinical_overlap','multiple','late_final','long'])assert.ok(PRACTICE_CASES.some(value=>value.family===family));
});
for(const [id,reference,expectedAction]of [
  ['CLIN-01','CPR 종료 후 자발순환 확인',null],['CLIN-02','산소 공급 중단 시각 14시',null],
  ['QUOTE-02','“새 환자 시작”이라는 말',null],['QUOTE-03','환자가 병원 추천해줘라고 말함',null],
  ['LATE-01','종료 하지 마',null],['LATE-02','병원 추천해줘 라고 환자가 말함',null],
  ['MULTI-01','종료 병원 추천해줘','stop'],['MULTI-02','병원 추천해줘 새 환자 시작','recommend'],
])test(`case ${id} has the independent exact intended action`,()=>{
  const row=PRACTICE_CASES.find(value=>value.id===id);assert.equal(row.referenceText,reference);assert.equal(row.expectedAction,expectedAction);
});
test('long prompts span commands, no-command clinical language and hesitation without copying clinical records',()=>{
  const rows=PRACTICE_CASES.filter(value=>value.family==='long');assert.equal(rows.length,40);assert.ok(rows.every(value=>value.referenceText.length>100));assert.ok(rows.every(value=>/연습|합성/u.test(value.referenceText)));assert.ok(rows.some(value=>value.expectedAction===null));
});
test('filters operate on fixed family, action-none alias and substring search',()=>{
  assert.equal(selectPracticeCases({group:'stop'}).length,6);assert.ok(selectPracticeCases({action:'none'}).every(value=>value.expectedAction===null));
  assert.deepEqual(selectPracticeCases({search:'CPR 종료 후'}).map(value=>value.id),['CLIN-01','LONG-03','LONG-20','LONG-28','LONG-38']);
  assert.deepEqual(selectPracticeCases({family:'multiple',action:'stop'}).map(value=>value.id),['MULTI-01']);
});
test('failed retry selection is explicit and an empty failed list cannot silently start the whole bank',()=>{
  assert.deepEqual(selectPracticeCases({failedCaseIds:[]}),[]);assert.deepEqual(selectPracticeCases({failedCaseIds:['REC-02','STOP-01']}).map(value=>value.id),['STOP-01','REC-02']);assert.equal(selectPracticeCases({failedCaseIds:undefined}).length,96);
});
test('seeded shuffle is repeatable, does not drop cases and cannot mutate the bank',()=>{
  const before=JSON.stringify(PRACTICE_CASES),left=selectPracticeCases({shuffleSeed:42}),right=selectPracticeCases({shuffleSeed:42});assert.deepEqual(left,right);assert.notDeepEqual(left.map(value=>value.id),selectPracticeCases().map(value=>value.id));assert.equal(new Set(left.map(value=>value.id)).size,96);left[0].title='changed';assert.equal(JSON.stringify(PRACTICE_CASES),before);
});
for(const options of [{group:3},{action:'diagnose'},{search:null},{failedCaseIds:['bad id']},{shuffleSeed:-1},{shuffleSeed:0x100000000},{shuffleSeed:1.5}])test(`filter rejects malformed values ${JSON.stringify(options)}`,()=>invalid(()=>selectPracticeCases(options)));

test('minimal creation classifies final text and supplies explicit synthetic-only defaults',()=>{
  const result=createPracticeAttempt({id:'P-a',source:'human_voice',trialNo:1,rawText:'종료',fragments:['종료'],status:'ok',timing:{startedAt:NOW,endedAt:NOW}},{now:NOW});
  assert.equal(result.parserResult.action,'stop');assert.equal(result.imported,false);assert.equal(result.actualAudioSaved,false);assert.equal(result.referenceConfirmed,false);assert.ok(!Object.hasOwn(result,'expectedAction'));assert.equal(result.caseSnapshot,null);assert.equal(result.caseId,'');assert.equal(result.autoStop,false);assert.equal(result.provisionalTriggerText,null);assert.equal(result.stopReason,'natural');
});
test('case snapshot keeps original expectation while top-level intended label is independent from prediction',()=>{
  const original=copy(PRACTICE_CASES[0]);const result=attempt({caseId:original.id,caseSnapshot:original,expectedAction:'recommend'});assert.equal(result.expectedAction,'recommend');assert.equal(result.caseSnapshot.expectedAction,'stop');assert.equal(result.parserResult.action,'stop');assert.deepEqual(original,PRACTICE_CASES[0]);
});
test('free custom reference is optional and never receives an inferred no-command expected label',()=>{
  const value=input({referenceText:'',referenceConfirmed:false});delete value.expectedAction;const result=createPracticeAttempt(value,{now:NOW});assert.ok(!Object.hasOwn(result,'expectedAction'));assert.equal(scorePracticeAttempts([result]).unlabelled,1);assert.equal(scorePracticeAttempts([result]).verified,0);
});
test('undefined expected label is omitted while explicit null is a verified no-command label',()=>{
  const absent=attempt({expectedAction:undefined}),negative=attempt({id:'P-02',rawText:'CPR 종료 후 확인',fragments:['CPR 종료 후 확인'],referenceText:'CPR 종료 후 확인',expectedAction:null});assert.ok(!Object.hasOwn(absent,'expectedAction'));assert.ok(Object.hasOwn(negative,'expectedAction'));assert.equal(negative.expectedAction,null);assert.equal(negative.parserResult,null);assert.equal(scorePracticeAttempts([negative]).matched,1);
});
test('verbatim confirmation needs a nonempty independent reference and cannot use the hypothesis as an implicit reference',()=>{
  invalid(()=>attempt({referenceText:'',referenceConfirmed:true}));invalid(()=>attempt({referenceText:' \n ',referenceConfirmed:true}));const result=attempt({rawText:'중단',referenceText:'종료',referenceConfirmed:false});assert.equal(result.rawText,'중단');assert.equal(result.referenceText,'종료');assert.equal(scorePracticeAttempts([result]).verified,0);
});
test('late negative final replaces only final prediction, preserving the earlier trigger text and every fragment',()=>{
  const value=attempt({rawText:'종료 하지 마',fragments:['종료','하지 마'],referenceText:'종료 하지 마',expectedAction:null,autoStop:true,provisionalTriggerText:'종료',stopReason:'trigger'});assert.equal(value.parserResult,null);assert.equal(value.provisionalTriggerText,'종료');assert.deepEqual(value.fragments,['종료','하지 마']);assert.equal(value.rawText,'종료 하지 마');
});
test('late reported speech prevents recommendation classification without deleting what was first heard',()=>{
  const value=attempt({rawText:'병원 추천해줘 라고 환자가 말함',fragments:['병원 추천해줘','라고 환자가 말함'],referenceText:'병원 추천해줘 라고 환자가 말함',expectedAction:null,autoStop:true,provisionalTriggerText:'병원 추천해줘'});assert.equal(value.parserResult,null);assert.equal(value.provisionalTriggerText,'병원 추천해줘');
});
test('middle command preserves both original prefix and suffix and selects the first command only',()=>{
  const value=attempt({rawText:'혈압 120. 병원 추천해줘 맥박 92. 종료',fragments:['혈압 120.','병원 추천해줘','맥박 92. 종료']});assert.equal(value.parserResult.action,'recommend');assert.equal(value.parserResult.prefix,'혈압 120.');assert.equal(value.parserResult.suffix,'맥박 92. 종료');assert.equal(value.rawText.slice(value.parserResult.index,value.parserResult.end),'병원 추천해줘');
});
for(const [status,stopReason,errorCode]of [['error','error','network'],['no_match','natural',''],['aborted','aborted',''],['aborted','page_exit','']])test(`${status}/${stopReason} retains raw text, fragments, provenance and failure metadata`,()=>{
  const value=attempt({status,stopReason,errorCode,rawText:'먼저 들린 문장',fragments:['먼저 들린 문장'],expectedAction:undefined});assert.equal(value.status,status);assert.equal(value.errorCode,errorCode);assert.equal(value.rawText,'먼저 들린 문장');assert.deepEqual(value.fragments,['먼저 들린 문장']);assert.equal(value.source,'human_voice');
});
test('whitespace and Unicode are preserved without normalizing, trimming or truncating original text',()=>{
  const raw='  연습용\r\n문장  👩🏽‍⚕️ e\u0301  종료  ';const value=attempt({rawText:raw,fragments:['  연습용\r\n문장',' 👩🏽‍⚕️ e\u0301  종료  '],referenceText:raw,notes:'\n 원문 메모 \t'});assert.equal(value.rawText,raw);assert.equal(value.notes,'\n 원문 메모 \t');assert.deepEqual(value.fragments,['  연습용\r\n문장',' 👩🏽‍⚕️ e\u0301  종료  ']);
});
test('manual simulation remains manual and never advertises captured or saved audio',()=>{
  const value=attempt({source:'manual_simulation'});assert.equal(value.source,'manual_simulation');assert.equal(value.stopReason,'manual');assert.equal(value.actualAudioSaved,false);assert.equal(scorePracticeAttempts([value]).human,0);assert.equal(scorePracticeAttempts([value]).manual,1);
});
test('an empty final cannot be marked ok to inflate verified no-command accuracy',()=>{
  invalid(()=>attempt({rawText:'',fragments:[],expectedAction:null}));invalid(()=>attempt({rawText:' \n ',fragments:[],expectedAction:null}));
  const failed=attempt({rawText:'',fragments:[],expectedAction:null,status:'no_match'});assert.equal(scorePracticeAttempts([failed]).mismatched,1);
});
test('all structured condition fields and anonymous metadata survive JSON roundtrip',()=>{
  const value=attempt({speakerId:'S01_A',pairId:'PAIR-1',device:'USB microphone',browser:'Browser/1',condition:{noiseKind:'siren',mask:'yes',distanceCm:32.5,style:'hesitation'},notes:'합성 시험'});const restored=importPracticeJSON(exportPracticeJSON(dataset(value))).attempts[0];assert.deepEqual(restored.condition,value.condition);assert.equal(restored.speakerId,'S01_A');assert.equal(restored.pairId,'PAIR-1');assert.equal(restored.device,'USB microphone');assert.equal(restored.browser,'Browser/1');assert.equal(restored.notes,'합성 시험');
});
test('creation and every pure dataset operation leave their inputs unchanged and detach nested arrays',()=>{
  const original=input(),before=copy(original),value=createPracticeAttempt(original,{now:NOW});assert.deepEqual(original,before);value.fragments.push('later');assert.deepEqual(original.fragments,['종료']);
  const first=attempt(),book=dataset(first),raw=JSON.stringify(book),next=appendPracticeAttempt(book,attempt({id:'P-02'}));next.attempts[0].fragments.push('later');assert.equal(JSON.stringify(book),raw);assert.equal(first.fragments.length,1);
});
test('cross-realm JSON-shaped objects are accepted without exact Object.prototype identity',()=>{
  const foreign=vm.runInNewContext('('+JSON.stringify(input())+')');const value=createPracticeAttempt(foreign,{now:NOW});assert.equal(value.parserResult.action,'stop');assert.equal(value.id,'P-01');
});

const malformed=[
  ['unknown source',{source:'imported'}],['human promotion',{source:'synthetic_corruption'}],['unknown status',{status:'success'}],
  ['unknown stop reason',{stopReason:'auto_send'}],['zero trial',{trialNo:0}],['fractional trial',{trialNo:1.5}],['bad id',{id:'with space'}],
  ['identified speaker',{speakerId:'홍길동'}],['bad pair id',{pairId:'PAIR 1'}],['nontext raw',{rawText:1}],['raw overflow',{rawText:'a'.repeat(16001)}],
  ['notes overflow',{notes:'a'.repeat(2001)}],['unknown expected action',{expectedAction:'dispatch'}],['fake boolean',{referenceConfirmed:'yes'}],
  ['audio field',{audio:'base64'}],['blob field',{blob:{}}],['raw hypothesis edit field',{hypothesis:'edited'}],['preset parser',{parserResult:null}],['locally imported flag',{imported:true}],
  ['case without snapshot',{caseId:'STOP-01'}],['case mismatch',{caseId:'STOP-01',caseSnapshot:{...PRACTICE_CASES[1]}}],
  ['unsafe browser overflow',{browser:'x'.repeat(513)}],['future timestamp',{timing:{startedAt:NOW,endedAt:NOW+1}}],
  ['reversed timestamps',{timing:{startedAt:NOW,endedAt:NOW-1}}],['NaN timestamp',{timing:{startedAt:NaN,endedAt:NOW}}],
  ['extra timing key',{timing:{startedAt:NOW,endedAt:NOW,duration:1}}],['null fragment',{fragments:[null]}],['nonarray fragments',{fragments:'종료'}],
  ['fragment overflow',{fragments:Array(257).fill('a')}],['big fragment',{fragments:['a'.repeat(16001)]}],
  ['invalid noise',{condition:{noiseKind:'unknown',mask:'unknown',distanceCm:null,style:'normal'}}],
  ['invalid mask',{condition:{noiseKind:'quiet',mask:true,distanceCm:null,style:'normal'}}],
  ['invalid style',{condition:{noiseKind:'quiet',mask:'no',distanceCm:null,style:'clinical'}}],
  ['negative distance',{condition:{noiseKind:'quiet',mask:'no',distanceCm:-1,style:'normal'}}],
  ['text distance',{condition:{noiseKind:'quiet',mask:'no',distanceCm:'20',style:'normal'}}],
  ['extra condition',{condition:{noiseKind:'quiet',mask:'no',distanceCm:null,style:'normal',snr:20}}],
];
for(const [label,extra]of malformed)test(`strict creation rejects ${label}`,()=>assert.throws(()=>attempt(extra)));
test('text and fragment exact boundaries are accepted without silent truncation',()=>{
  assert.equal(attempt({rawText:'a'.repeat(16000),fragments:[]}).rawText.length,16000);assert.equal(attempt({fragments:Array(256).fill('')}).fragments.length,256);assert.equal(attempt({browser:'x'.repeat(512)}).browser.length,512);
});
test('dense array validation rejects sparse holes, non-index properties and accessor fragments',()=>{
  const sparse=Array(2);sparse[1]='a';invalid(()=>attempt({fragments:sparse}));const extra=['a'];extra.note='extra';invalid(()=>attempt({fragments:extra}));let invoked=false;const getter=['a'];Object.defineProperty(getter,'0',{enumerable:true,get(){invoked=true;return 'a';}});invalid(()=>attempt({fragments:getter}));assert.equal(invoked,false);
});
test('top-level getters, symbols, class instances and prototype injection are rejected without execution',()=>{
  let invoked=false;const value=input();Object.defineProperty(value,'source',{enumerable:true,get(){invoked=true;return 'human_voice';}});invalid(()=>createPracticeAttempt(value,{now:NOW}));assert.equal(invoked,false);
  const symbolic=input();symbolic[Symbol('audio')]='raw';invalid(()=>createPracticeAttempt(symbolic,{now:NOW}));invalid(()=>createPracticeAttempt(Object.assign(new Date(),input()),{now:NOW}));
  invalid(()=>importPracticeJSON('{"schema":1,"purpose":"synthetic_command_practice","actualAudioSaved":false,"attempts":[],"__proto__":{"polluted":true}}'));assert.equal({}.polluted,undefined);
});
test('one attempt has an independent UTF8 limit even when every individual field is in range',()=>{
  const huge=input({fragments:Array(9).fill('한'.repeat(16000)),rawText:'종료'});invalid(()=>createPracticeAttempt(huge,{now:NOW}),'PRACTICE_LIMIT');
});

test('annotation changes only independent expectation, verbatim confirmation and notes',()=>{
  const value=attempt({caseId:'STOP-01',caseSnapshot:{...PRACTICE_CASES[0]},referenceConfirmed:false}),original=dataset(value),before=JSON.stringify(original);
  const updated=annotatePracticeAttempt(original,value.id,{expectedAction:null,referenceConfirmed:true,notes:'실제 의도는 명령 없음'}).attempts[0];assert.equal(updated.expectedAction,null);assert.equal(updated.referenceConfirmed,true);assert.equal(updated.notes,'실제 의도는 명령 없음');assert.equal(updated.caseSnapshot.expectedAction,'stop');assert.deepEqual(updated.parserResult,value.parserResult);assert.equal(updated.rawText,value.rawText);assert.equal(updated.source,value.source);assert.equal(JSON.stringify(original),before);
});
test('annotation can explicitly remove a label while keeping the immutable original case label',()=>{
  const result=annotatePracticeAttempt(dataset(attempt({caseId:'STOP-01',caseSnapshot:{...PRACTICE_CASES[0]}})),'P-01',{expectedAction:undefined});assert.ok(!Object.hasOwn(result.attempts[0],'expectedAction'));assert.equal(result.attempts[0].caseSnapshot.expectedAction,'stop');assert.equal(scorePracticeAttempts(result.attempts).unlabelled,1);
});
for(const forbidden of [{rawText:'edited'},{source:'human_voice'},{caseSnapshot:null},{parserResult:null},{fragments:[]},{imported:false},{actualAudioSaved:true}])test(`annotation cannot edit original data ${Object.keys(forbidden)[0]}`,()=>invalid(()=>annotatePracticeAttempt(dataset(attempt()),'P-01',forbidden)));
test('unknown annotation id and malformed annotation values are explicit errors',()=>{
  invalid(()=>annotatePracticeAttempt(dataset(attempt()),'P-missing',{}),'PRACTICE_ATTEMPT_NOT_FOUND');invalid(()=>annotatePracticeAttempt(dataset(attempt()),'P-01',{referenceConfirmed:'yes'}));invalid(()=>annotatePracticeAttempt(dataset(attempt()),'P-01',{notes:'x'.repeat(2001)}));
});

test('empty score has no accuracy denominator or invented successes',()=>{
  const result=scorePracticeAttempts([]);assert.equal(result.total,0);assert.equal(result.accuracy,null);assert.equal(result.verified,0);assert.deepEqual(result.coverage,[]);assert.deepEqual(result.statusCounts,{ok:0,no_match:0,error:0,aborted:0});
});
test('source-specific verified accuracy includes failed labelled trials and never counts a failed no-command as a correct rejection',()=>{
  const rows=[attempt(),attempt({id:'P-02',source:'manual_simulation',expectedAction:'recommend'}),attempt({id:'P-03',status:'error',errorCode:'network'}),attempt({id:'P-04',status:'no_match',rawText:'',fragments:[],expectedAction:null}),attempt({id:'P-05',status:'aborted',expectedAction:'stop'})];
  const result=scorePracticeAttempts(rows);assert.equal(result.total,5);assert.equal(result.human,4);assert.equal(result.manual,1);assert.equal(result.failed,3);assert.equal(result.verified,5);assert.equal(result.matched,1);assert.equal(result.mismatched,4);assert.equal(result.accuracy,0.2);assert.deepEqual(result.statusCounts,{ok:2,no_match:1,error:1,aborted:1});assert.equal(result.bySource.human_voice.accuracy,0.25);assert.equal(result.bySource.manual_simulation.accuracy,0);
});
test('unlabelled and unconfirmed records remain in totals but never enter command accuracy',()=>{
  const rows=[attempt({expectedAction:undefined}),attempt({id:'P-02',referenceConfirmed:false}),attempt({id:'P-03'})];const result=scorePracticeAttempts(rows);assert.equal(result.total,3);assert.equal(result.unlabelled,1);assert.equal(result.unconfirmed,1);assert.equal(result.verified,1);assert.equal(result.matched,1);assert.equal(result.accuracy,1);
});
test('coverage reports custom/family counts with independent failure, human and manual totals',()=>{
  const rows=[attempt({caseId:'STOP-01',caseSnapshot:{...PRACTICE_CASES[0]}}),attempt({id:'P-02',caseId:'STOP-01',caseSnapshot:{...PRACTICE_CASES[0]},source:'manual_simulation',status:'error'}),attempt({id:'P-03',expectedAction:undefined})];const score=scorePracticeAttempts(rows);assert.deepEqual(score.coverage.map(value=>value.family),['custom','stop']);const stop=score.coverage[1];assert.equal(stop.total,2);assert.equal(stop.human,1);assert.equal(stop.manual,1);assert.equal(stop.failed,1);assert.equal(stop.verified,2);assert.equal(stop.mismatched,1);
});
test('score rejects duplicate attempt ids instead of inflating the denominator',()=>invalid(()=>scorePracticeAttempts([attempt(),attempt()])));

test('JSON import preserves original source and every raw field, and records imported provenance separately',()=>{
  const value=dataset(attempt(),attempt({id:'P-02',source:'manual_simulation',status:'error',rawText:'부분 인식'})),raw=exportPracticeJSON(value),restored=importPracticeJSON(raw);assert.equal(restored.attempts[0].source,'human_voice');assert.equal(restored.attempts[1].source,'manual_simulation');assert.ok(restored.attempts.every(value=>value.imported));assert.equal(restored.attempts[1].rawText,'부분 인식');assert.equal(value.attempts[0].imported,false);assert.equal(scorePracticeAttempts(restored.attempts).imported,2);assert.equal(scorePracticeAttempts(restored.attempts).human,1);
});
test('reexporting imports does not erase imported provenance or promote manual results to human speech',()=>{
  const first=importPracticeJSON(exportPracticeJSON(dataset(attempt({source:'manual_simulation'}))));const second=importPracticeJSON(exportPracticeJSON(first));assert.equal(second.attempts[0].source,'manual_simulation');assert.equal(second.attempts[0].imported,true);assert.equal(scorePracticeAttempts(second.attempts).human,0);
});
test('import rejects fabricated parser actions, source spans, prefix/suffix and null results inconsistent with final raw text',()=>{
  for(const mutate of [value=>value.parserResult.action='recommend',value=>value.parserResult.index=1,value=>value.parserResult.prefix='edited',value=>value.parserResult=null]){const book=dataset(attempt());mutate(book.attempts[0]);invalid(()=>importPracticeJSON(JSON.stringify(book)));}
});
test('malformed JSON, unknown schemas, audio payloads and duplicate ids cannot mutate existing data',()=>{
  for(const raw of ['', 'not json','null','[]',JSON.stringify({...emptyPracticeDataset(),schema:3}),JSON.stringify({...emptyPracticeDataset(),audio:'raw'}),JSON.stringify(dataset(attempt(),attempt()))])invalid(()=>importPracticeJSON(raw));
});
test('merge accepts unique imports and exact duplicate imports are a no-op even when imported flag differs',()=>{
  const base=dataset(attempt()),incoming=importPracticeJSON(exportPracticeJSON(dataset(attempt(),attempt({id:'P-02',source:'manual_simulation'})))),before=JSON.stringify(base);const merged=mergePracticeDatasets(base,incoming);assert.equal(merged.attempts.length,2);assert.equal(merged.attempts[0].imported,false);assert.equal(merged.attempts[1].imported,true);assert.equal(JSON.stringify(base),before);assert.deepEqual(mergePracticeDatasets(merged,incoming),merged);
});
test('same id with changed label, raw text or notes rejects the entire merge without losing either input',()=>{
  const base=dataset(attempt()),incoming=importPracticeJSON(exportPracticeJSON(dataset(attempt({id:'P-02'}),attempt({expectedAction:'recommend'})))),before=JSON.stringify(base),other=JSON.stringify(incoming);invalid(()=>mergePracticeDatasets(base,incoming),'PRACTICE_ID_CONFLICT');assert.equal(JSON.stringify(base),before);assert.equal(JSON.stringify(incoming),other);
});
test('500 records fit; record 501 and oversized merge are all-or-nothing without auto eviction',()=>{
  const rows=Array.from({length:500},(_,index)=>attempt({id:'P-'+index,trialNo:index+1})),book=dataset(...rows);assert.equal(importPracticeJSON(exportPracticeJSON(book)).attempts.length,500);const before=JSON.stringify(book);invalid(()=>appendPracticeAttempt(book,attempt({id:'P-501'})),'PRACTICE_LIMIT');invalid(()=>mergePracticeDatasets(book,importPracticeJSON(exportPracticeJSON(dataset(attempt({id:'P-501'}))))),'PRACTICE_LIMIT');assert.equal(JSON.stringify(book),before);assert.equal(book.attempts[0].id,'P-0');
});
test('UTF8 dataset cap is checked before store/import/export and never evicts earlier attempts',()=>{
  const rows=Array.from({length:100},(_,index)=>attempt({id:'P-'+index,rawText:'한'.repeat(16000),fragments:[]}));const oversized=dataset(...rows),store=storage({[PRACTICE_KEY]:'old'});invalid(()=>exportPracticeJSON(oversized),'PRACTICE_LIMIT');invalid(()=>writePracticeDataset(store,oversized,{expectedRaw:'old'}),'PRACTICE_LIMIT');assert.equal(store.values.get(PRACTICE_KEY),'old');assert.ok(!store.calls.some(call=>call[0]==='set'));invalid(()=>importPracticeJSON(' '.repeat(PRACTICE_LIMITS.bytes+1)));
});

test('storage read is non-destructive and reports absent/valid/invalid distinctly',()=>{
  const absent=storage();assert.deepEqual(readPracticeDataset(absent),{status:'absent',dataset:null,raw:null,message:'저장된 명령 연습 자료가 없습니다.'});assert.ok(absent.calls.every(call=>call[0]==='get'));
  const store=storage({[PRACTICE_KEY]:exportPracticeJSON(dataset(attempt()))});assert.equal(readPracticeDataset(store).status,'valid');assert.ok(store.calls.every(call=>call[0]==='get'));
  const broken=storage({[PRACTICE_KEY]:'broken'}),result=readPracticeDataset(broken);assert.equal(result.status,'invalid');assert.equal(result.dataset,null);assert.equal(result.raw,'broken');assert.equal(broken.values.get(PRACTICE_KEY),'broken');assert.ok(broken.calls.every(call=>call[0]==='get'));
});
test('unavailable storage read is explicit and does not fabricate an empty durable dataset',()=>{
  for(const store of [null,{}, {getItem(){throw Error('denied');}}]){const result=readPracticeDataset(store);assert.equal(result.status,'invalid');assert.equal(result.dataset,null);assert.equal(result.raw,null);}
});
test('successful dedicated-key write verifies readback and preserves every patient/other key',()=>{
  const other={'paramedic.voice.patient-records.v1':'notebook','paramedic.voice.quick-recovery.v1':'recovery','paramedic.voice.actor':'actor','unrelated':'value'},store=storage(other),book=dataset(attempt()),before=JSON.stringify(book);const written=writePracticeDataset(store,book,{expectedRaw:null});assert.equal(written.raw,store.values.get(PRACTICE_KEY));assert.deepEqual(written.dataset,book);assert.equal(JSON.stringify(book),before);for(const[key,value]of Object.entries(other))assert.equal(store.values.get(key),value);assert.deepEqual(store.calls.map(call=>[call[0],call[1]]),[['get',PRACTICE_KEY],['set',PRACTICE_KEY],['get',PRACTICE_KEY]]);
});
test('expected raw is mandatory and stale storage cannot overwrite another tab',()=>{
  const store=storage({[PRACTICE_KEY]:'newer'});invalid(()=>writePracticeDataset(store,emptyPracticeDataset()));invalid(()=>writePracticeDataset(store,emptyPracticeDataset(),{expectedRaw:null}),'STALE_PRACTICE_DATA');assert.equal(store.values.get(PRACTICE_KEY),'newer');assert.ok(!store.calls.some(call=>call[0]==='set'));
});
test('quota failure leaves earlier saved dataset untouched and latest memory remains exportable',()=>{
  const previous=exportPracticeJSON(dataset(attempt())),next=dataset(attempt(),attempt({id:'P-02'})),store={getItem:()=>previous,setItem(){throw new Error('QuotaExceededError');}};invalid(()=>writePracticeDataset(store,next,{expectedRaw:previous}),'PRACTICE_STORAGE_UNAVAILABLE');assert.equal(next.attempts.length,2);assert.equal(importPracticeJSON(exportPracticeJSON(next)).attempts.length,2);
});
test('silent ignored writes and unavailable readback are not reported as durable success',()=>{
  const ignored={getItem:()=>null,setItem(){}};invalid(()=>writePracticeDataset(ignored,emptyPracticeDataset(),{expectedRaw:null}),'PRACTICE_STORAGE_UNAVAILABLE');let reads=0;const unreadable={getItem(){if(reads++)throw Error('denied');return null;},setItem(){}};invalid(()=>writePracticeDataset(unreadable,emptyPracticeDataset(),{expectedRaw:null}),'PRACTICE_STORAGE_UNAVAILABLE');
});
test('schema failure happens before any store operation and preserves invalid raw storage',()=>{
  const store=storage({[PRACTICE_KEY]:'old'});invalid(()=>writePracticeDataset(store,{...emptyPracticeDataset(),patientId:2},{expectedRaw:'old'}));assert.deepEqual(store.calls,[]);assert.equal(store.values.get(PRACTICE_KEY),'old');
});

test('CSV preserves original multiline quotes and fragments as RFC4180 cells, with a UTF8 BOM',()=>{
  const raw='연습용, "종료"\r\n말함',value=attempt({rawText:raw,fragments:['연습용, "종료"','말함'],notes:'첫줄\n둘째줄'}),csv=exportPracticeCSV(dataset(value));assert.ok(csv.startsWith('\uFEFF'));const [header,row]=csvRows(csv),cell=name=>row[header.indexOf(name)];assert.equal(cell('rawText'),raw);assert.equal(cell('fragments'),JSON.stringify(value.fragments));assert.equal(cell('notes'),'첫줄\n둘째줄');assert.equal(cell('actualAudioSaved'),'false');assert.equal(cell('source'),'human_voice');
});
for(const payload of ['=1+1','+SUM(1,2)','-1+2','@SUM(A1)',' \t=HYPERLINK("x")','\r\n@malicious','\uFEFF=SUM(A1)'])test(`CSV formula protection covers every free-text raw/reference/notes cell ${JSON.stringify(payload)}`,()=>{
  const value=attempt({rawText:payload,fragments:[payload],referenceText:payload,notes:payload,device:payload,browser:payload}),[header,row]=csvRows(exportPracticeCSV(dataset(value)));for(const name of ['rawText','referenceText','notes','device','browser'])assert.ok(row[header.indexOf(name)].startsWith("'"),name);assert.equal(value.rawText,payload);
});
test('CSV distinguishes unlabelled from no-command and includes original and annotated expected labels',()=>{
  const book=dataset(attempt({expectedAction:undefined}),attempt({id:'P-02',expectedAction:null,caseId:'STOP-01',caseSnapshot:{...PRACTICE_CASES[0]}})),[header,a,b]=csvRows(exportPracticeCSV(book));assert.equal(a[header.indexOf('expectedAction')],'unlabelled');assert.equal(b[header.indexOf('expectedAction')],'no_command');assert.equal(b[header.indexOf('caseExpectedAction')],'stop');
});
test('pure helpers require no API, media, clipboard or patient storage capability',()=>{
  const store=storage({'paramedic.voice.patient-records.v1':'safe'});const rows=selectPracticeCases({group:'stop'});const value=attempt({caseId:rows[0].id,caseSnapshot:rows[0]});const written=writePracticeDataset(store,appendPracticeAttempt(emptyPracticeDataset(),value),{expectedRaw:null});assert.equal(scorePracticeAttempts(written.dataset.attempts).matched,1);assert.equal(store.values.get('paramedic.voice.patient-records.v1'),'safe');assert.ok(store.calls.every(call=>call[1]===PRACTICE_KEY));
});
