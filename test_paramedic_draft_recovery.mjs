import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {RECOVERY_KEY,writeDraftRecovery,readDraftRecovery,clearDraftRecovery} from './paramedic-ui/draft-recovery.mjs';

const NOW=1_800_000_000_000,TTL=43_200_000,ORIGIN='http://127.0.0.1:18042',MAX_BYTES=131072;
const clone=value=>JSON.parse(JSON.stringify(value));
function memory(initial={}){const data=new Map(Object.entries(initial));return {data,getItem:key=>data.has(key)?data.get(key):null,setItem:(key,value)=>data.set(key,String(value)),removeItem:key=>data.delete(key)};}
function payload(){return {serverOrigin:ORIGIN,actorId:'QUICK-SYNTHETIC',sessionId:'VS-SYNTHETIC',version:3,pending:null,recoveryText:'',manualText:'',mode:'auto',recordingMode:'toggle'};}
function attempt(overrides={}){return {target:'transcript',mode:'auto',questionId:null,questionText:'',apiText:'synthetic line',sourceText:'synthetic original line',excludedText:'',unappliedBefore:'',manual:false,copiedFrom:'',beforeVersion:3,beforeFlagged:[],trigger:null,...overrides};}
function transcript(){return {...payload(),pending:{request:{path:'/api/voice/sessions/VS-SYNTHETIC/transcripts',method:'POST',body:{text:'synthetic line',mode:'auto',utterance_id:'U-SYNTHETIC',is_final:true,actor_id:'QUICK-SYNTHETIC',client_request_id:'R-SYNTHETIC',expected_version:3}},job:{text:'synthetic line',mode:'auto',utteranceId:'U-SYNTHETIC',phase:'write'},attempt:attempt()}};}
function create(){return {...payload(),sessionId:null,version:0,pending:{request:{path:'/api/voice/sessions',method:'POST',body:{actor_id:'QUICK-SYNTHETIC',client_request_id:'R-CREATE-SYNTHETIC'}},job:{text:'synthetic line',mode:'auto',utteranceId:'U-SYNTHETIC',phase:'create'},attempt:attempt({beforeVersion:0})}};}
function answer(){return {...payload(),pending:{request:{path:'/api/voice/sessions/VS-SYNTHETIC/followup/answers',method:'POST',body:{question_id:'Q-SYNTHETIC',profile:'voice_extended_v1',utterance_id:'U-ANSWER-SYNTHETIC',text:'synthetic answer',actor_id:'QUICK-SYNTHETIC',client_request_id:'R-ANSWER-SYNTHETIC',expected_version:3,unavailable:true}},job:{text:'synthetic answer',questionId:'Q-SYNTHETIC',utteranceId:'U-ANSWER-SYNTHETIC',phase:'answer'},attempt:attempt({target:'answer',questionId:'Q-SYNTHETIC',questionText:'synthetic question?',apiText:'synthetic answer'})}};}
function correction(){return {...payload(),pending:{request:{path:'/api/voice/sessions/VS-SYNTHETIC/fields',method:'PATCH',body:{changes:{systolic_bp:110,diastolic_bp:70,symptoms:['흉통'],denied_symptoms:['구토']},unset_fields:['glucose','spo2'],change_kind:'correction',actor_id:'QUICK-SYNTHETIC',client_request_id:'R-CORRECTION-SYNTHETIC',expected_version:3}},job:{phase:'correct',changes:{systolic_bp:110,diastolic_bp:70,symptoms:['흉통'],denied_symptoms:['구토']},unsetFields:['glucose','spo2']},attempt:attempt({target:'correction',mode:'dictation',apiText:'',sourceText:''})}};}
function saved(value=payload(),overrides={}){return {...value,createdAt:NOW,expiresAt:NOW+TTL,...overrides};}
function rawStorage(value){return memory({[RECOVERY_KEY]:typeof value==='string'?value:JSON.stringify(value)});}
function assertRejected(value){const storage=memory({[RECOVERY_KEY]:'previous-synthetic-recovery'});assert.throws(()=>writeDraftRecovery(storage,value,{now:NOW}));assert.equal(storage.getItem(RECOVERY_KEY),'previous-synthetic-recovery');}

test('a minimal saved reference has the exact public shape, twelve-hour lifetime, and independent identity',()=>{
  assert.equal(RECOVERY_KEY,'paramedic.voice.quick-recovery.v1');const storage=memory(),input=payload();
  const result=writeDraftRecovery(storage,input,{now:NOW});
  assert.deepEqual(result,saved());assert.notEqual(result,input);assert.deepEqual(JSON.parse(storage.getItem(RECOVERY_KEY)),saved());
  assert.equal(Object.hasOwn(input,'createdAt'),false);
});
for(const [name,make]of [['create',create],['transcript',transcript],['answer',answer]])test(`pending ${name} preserves the original serialized request, job and attempt without aliasing`,()=>{
  const input=make(),before=JSON.stringify(input.pending.request),storage=memory();
  const written=writeDraftRecovery(storage,input,{now:NOW});
  assert.equal(JSON.stringify(written.pending.request),before);
  input.pending.job.text='mutated after saving';written.pending.attempt.sourceText='mutated return';
  const read=readDraftRecovery(storage,ORIGIN,{now:NOW});
  assert.equal(read.status,'valid');assert.equal(JSON.stringify(read.payload.pending.request),before);
  assert.equal(read.payload.pending.attempt.sourceText,'synthetic original line');
});
test('whitespace, line endings, control characters and Unicode clinical text roundtrip unchanged',()=>{
  const raw=' \t synthetic\r\n가나다 😀\u0000  ',input=transcript();
  input.recoveryText=raw;input.manualText=raw;input.pending.request.body.text=raw;input.pending.job.text=raw;input.pending.attempt.apiText=raw;
  const storage=memory();writeDraftRecovery(storage,input,{now:NOW});const result=readDraftRecovery(storage,ORIGIN,{now:NOW}).payload;
  assert.equal(result.recoveryText,raw);assert.equal(result.manualText,raw);assert.equal(result.pending.request.body.text,raw);
});
test('cross-realm plain UI objects are accepted while preserving a stable body order',()=>{
  const input=vm.runInNewContext('('+JSON.stringify(transcript())+')'),storage=memory();
  const result=writeDraftRecovery(storage,input,{now:NOW});
  assert.equal(JSON.stringify(result.pending.request.body),JSON.stringify(input.pending.request.body));
});
test('origin canonicalization retains the root and strips no user data into an unexpected host',()=>{
  const input=payload();input.serverOrigin='HTTP://LOCALHOST:80/';const storage=memory();
  assert.equal(writeDraftRecovery(storage,input,{now:NOW}).serverOrigin,'http://localhost');
  assert.equal(readDraftRecovery(storage,'http://localhost/',{now:NOW}).status,'valid');
});
for(const value of ['https://example.test/path','https://example.test/?q=1','https://example.test/?','https://example.test/#','https://user:pass@example.test','ftp://example.test','//example.test',' https://example.test','https://example.test\\path'])test(`reject unsafe recovery origin ${value}`,()=>{const input=payload();input.serverOrigin=value;assertRejected(input);});
for(const [field,max]of [['actorId',80],['sessionId',128]])test(`${field} obeys server ASCII identifier grammar and the exact length boundary`,()=>{
  const input=payload();input[field]='a'.repeat(max);writeDraftRecovery(memory(),input,{now:NOW});
  input[field]='A-b_c.d:e@f';writeDraftRecovery(memory(),input,{now:NOW});
  for(const value of ['','a'.repeat(max+1),'.leading','-leading',' a','a/b','한글']){input[field]=value;assertRejected(input);}
});
for(const [sessionId,version]of [[null,1],['VS-SYNTHETIC',0],['VS-SYNTHETIC',-1],['VS-SYNTHETIC',1.5],['VS-SYNTHETIC','3']])test(`session/version invariant rejects ${sessionId}/${version}`,()=>{assertRejected({...payload(),sessionId,version});});
test('an unsent first recording may have no server session with version zero',()=>{const input={...payload(),sessionId:null,version:0,recoveryText:'synthetic unsent line'};assert.deepEqual(writeDraftRecovery(memory(),input,{now:NOW}),saved(input));});
for(const field of ['recoveryText','manualText'])test(`${field} preserves 16000 characters including text too long for one API request, and rejects 16001`,()=>{
  const input=payload();input[field]='r'.repeat(16000);const result=writeDraftRecovery(memory(),input,{now:NOW});assert.equal(result[field],input[field]);input[field]+='x';assertRejected(input);
});
test('exactly 128 KiB of serialized UTF8 is accepted and one extra byte leaves the prior snapshot intact',()=>{
  const input=transcript();input.recoveryText='r'.repeat(16000);input.manualText='m'.repeat(16000);input.pending.attempt.sourceText='가'.repeat(16000);input.pending.attempt.excludedText='나'.repeat(16000);
  const remaining=MAX_BYTES-new TextEncoder().encode(JSON.stringify(saved(input))).byteLength;
  assert.ok(remaining>0&&remaining<16000);input.pending.attempt.copiedFrom='c'.repeat(remaining);
  const storage=memory();writeDraftRecovery(storage,input,{now:NOW});const raw=storage.getItem(RECOVERY_KEY);assert.equal(new TextEncoder().encode(raw).byteLength,MAX_BYTES);
  assert.equal(readDraftRecovery(storage,ORIGIN,{now:NOW}).status,'valid');
  input.pending.attempt.copiedFrom+='x';assert.throws(()=>writeDraftRecovery(storage,input,{now:NOW}),/128 KiB/);assert.equal(storage.getItem(RECOVERY_KEY),raw);
});
for(const field of ['rawAudio','audio_base64','blob','patient','successfulRecommendationReady','createdAt','expiresAt'])test(`unapproved top-level ${field} never reaches storage`,()=>{assertRejected({...payload(),[field]:'synthetic'});});
for(const field of ['serverOrigin','actorId','sessionId','version','pending','recoveryText','manualText','mode','recordingMode'])test(`missing required ${field} is rejected`,()=>{const input=payload();delete input[field];assertRejected(input);});
for(const [mode,recordingMode]of [['invalid','toggle'],['auto','invalid'],['AUTO','hold'],['dictation',null]])test(`invalid mode pair ${mode}/${recordingMode} is rejected`,()=>assertRejected({...payload(),mode,recordingMode}));
test('read is non-destructive and returns a new independent snapshot on every access',()=>{
  const storage=rawStorage(saved(transcript())),before=new Map(storage.data),first=readDraftRecovery(storage,ORIGIN,{now:NOW});
  first.payload.pending.request.body.text='mutated read';assert.equal(readDraftRecovery(storage,ORIGIN,{now:NOW}).payload.pending.request.body.text,'synthetic line');assert.deepEqual(storage.data,before);
});
test('TTL is valid immediately before twelve hours and expired at the boundary without consuming text',()=>{
  const storage=rawStorage(saved()),before=storage.getItem(RECOVERY_KEY);
  assert.equal(readDraftRecovery(storage,ORIGIN,{now:NOW+TTL-1}).status,'valid');
  const result=readDraftRecovery(storage,ORIGIN,{now:NOW+TTL});assert.equal(result.status,'expired');assert.equal(result.payload,null);assert.equal(storage.getItem(RECOVERY_KEY),before);
});
for(const patch of [{createdAt:NOW+1,expiresAt:NOW+TTL+1},{expiresAt:NOW+TTL+1},{createdAt:-1},{expiresAt:'later'}])test(`malformed or future lifetime is invalid: ${JSON.stringify(patch)}`,()=>{const result=readDraftRecovery(rawStorage(saved(payload(),patch)),ORIGIN,{now:NOW});assert.equal(result.status,'invalid');assert.equal(result.payload,null);});
for(const expected of ['http://127.0.0.1:18043','https://127.0.0.1:18042','http://other.test','http://127.0.0.1:18042/path'])test(`read requires exact canonical server origin: ${expected}`,()=>{const result=readDraftRecovery(rawStorage(saved()),expected,{now:NOW});assert.equal(result.status,'invalid');assert.equal(result.payload,null);});
for(const raw of ['', '{bad', 'null','[]','1','x'.repeat(MAX_BYTES+1)])test(`malformed/oversized raw entry fails closed (${raw.length} chars)`,()=>{const result=readDraftRecovery(rawStorage(raw),ORIGIN,{now:NOW});assert.equal(result.status,'invalid');assert.equal(result.payload,null);});
test('absent is reserved for a missing key and blocked storage is invalid',()=>{
  assert.equal(readDraftRecovery(memory(),ORIGIN,{now:NOW}).status,'absent');
  for(const storage of [null,{}, {getItem(){throw Error('denied');}}, {getItem(){return undefined;}}])assert.equal(readDraftRecovery(storage,ORIGIN,{now:NOW}).status,'invalid');
});
test('quota/denied write errors explicitly fail and preserve the previous recovery',()=>{
  const storage=memory({[RECOVERY_KEY]:'previous'});storage.setItem=()=>{throw Error('QuotaExceededError');};assert.throws(()=>writeDraftRecovery(storage,payload(),{now:NOW}),/could not be saved/);assert.equal(storage.getItem(RECOVERY_KEY),'previous');
  for(const unavailable of [null,{}])assert.throws(()=>writeDraftRecovery(unavailable,payload(),{now:NOW}),/could not be saved/);
});
test('all operations preserve unrelated normal-session, handoff and application keys',()=>{
  const other={'paramedic.voice.actor':'main-actor','paramedic.voice.session':'VS-MAIN','paramedic.voice.server':'http://main.test','paramedic.voice.handoff.v1':'other handoff',other:'keep'},storage=memory(other);
  writeDraftRecovery(storage,transcript(),{now:NOW});readDraftRecovery(storage,ORIGIN,{now:NOW});assert.equal(storage.data.size,6);assert.equal(clearDraftRecovery(storage),true);assert.deepEqual(Object.fromEntries(storage.data),other);
});
test('clear is explicit, idempotent, safe on denied storage, and never calls clear-all',()=>{
  const storage=memory();let clearAll=false;storage.clear=()=>{clearAll=true;};assert.equal(clearDraftRecovery(storage),true);assert.equal(clearAll,false);
  for(const blocked of [null,{}, {removeItem(){throw Error('denied');}}])assert.equal(clearDraftRecovery(blocked),false);
});
test('invalid clocks cannot write or restore and never erase an existing snapshot',()=>{for(const now of [-1,NaN,Infinity,'0',Number.MAX_SAFE_INTEGER]){assert.throws(()=>writeDraftRecovery(memory(),payload(),{now}));assert.equal(readDraftRecovery(rawStorage(saved()),ORIGIN,{now}).status,'invalid');}});

for(const [name,mutate]of [
  ['arbitrary external URL',v=>v.pending.request.path='https://other.test/api/voice/sessions'],
  ['submit endpoint',v=>v.pending.request.path='/api/voice/sessions/VS-SYNTHETIC/submit'],
  ['audio endpoint',v=>v.pending.request.path='/api/voice/sessions/VS-SYNTHETIC/audio'],
  ['another session',v=>v.pending.request.path='/api/voice/sessions/VS-OTHER/transcripts'],
  ['query suffix',v=>v.pending.request.path+='?redirect=1'],
  ['non-POST verb',v=>v.pending.request.method='GET'],
  ['another actor',v=>v.pending.request.body.actor_id='OTHER-ACTOR'],
  ['future expected version',v=>v.pending.request.body.expected_version=4],
  ['zero expected version',v=>v.pending.request.body.expected_version=0],
  ['missing request ID',v=>delete v.pending.request.body.client_request_id],
  ['unsafe request ID',v=>v.pending.request.body.client_request_id='bad/id'],
  ['oversize utterance ID',v=>v.pending.request.body.utterance_id='a'.repeat(129)],
  ['interim transcript',v=>v.pending.request.body.is_final=false],
  ['unapproved request field',v=>v.pending.request.headers={Authorization:'synthetic'}],
  ['unapproved body field',v=>v.pending.request.body.audio_base64='synthetic'],
  ['API text beyond limit',v=>v.pending.request.body.text='x'.repeat(4001)],
  ['body/job text mismatch',v=>v.pending.job.text='different synthetic'],
  ['body/job utterance mismatch',v=>v.pending.job.utteranceId='OTHER-UTTERANCE'],
  ['body/job mode mismatch',v=>v.pending.job.mode='command'],
  ['wrong job phase',v=>v.pending.job.phase='create'],
  ['missing attempt',v=>delete v.pending.attempt],
  ['unknown pending field',v=>v.pending.extra=true],
  ['unknown job field',v=>v.pending.job.extra=true],
])test(`pending rejects ${name} without replacing an older snapshot`,()=>{const input=transcript();mutate(input);assertRejected(input);});
test('a newer known draft version preserves the old expected_version for the same idempotent retry',()=>{const input=transcript();input.version=5;const result=writeDraftRecovery(memory(),input,{now:NOW});assert.equal(result.pending.request.body.expected_version,3);assert.equal(result.version,5);});
test('URL-encoded server session IDs and maximum request/utterance IDs retain their exact request path',()=>{
  const input=transcript();input.actorId='A-b_c.d:e@f';input.sessionId='VS:test@synthetic';
  input.pending.request.path='/api/voice/sessions/VS%3Atest%40synthetic/transcripts';input.pending.request.body.actor_id=input.actorId;
  input.pending.request.body.client_request_id='r'.repeat(128);input.pending.request.body.utterance_id='u'.repeat(128);input.pending.job.utteranceId='u'.repeat(128);
  assert.equal(writeDraftRecovery(memory(),input,{now:NOW}).pending.request.path,input.pending.request.path);
});
test('all independent attempt field boundaries accept their exact permitted limits',()=>{
  const input=transcript();input.pending.request.body.text='t'.repeat(4000);input.pending.job.text='t'.repeat(4000);input.pending.attempt.apiText='t'.repeat(4000);
  input.pending.attempt.questionText='q'.repeat(2000);input.pending.attempt.sourceText='s'.repeat(16000);input.pending.attempt.excludedText='e'.repeat(16000);
  input.pending.attempt.unappliedBefore='u'.repeat(16000);input.pending.attempt.copiedFrom='c'.repeat(16000);
  input.pending.attempt.beforeFlagged=Array.from({length:64},(_,i)=>'field'+i);input.pending.attempt.trigger={action:'stop',command:'s'.repeat(100)};
  const result=writeDraftRecovery(memory(),input,{now:NOW});assert.deepEqual(result.pending.attempt,input.pending.attempt);
});
test('create recovery cannot smuggle a patient ID or refer to an already known session',()=>{const patient=create();patient.pending.request.body.patient_id='P-SYNTHETIC';assertRejected(patient);const known=create();known.sessionId='VS-KNOWN';known.version=1;assertRejected(known);});
for(const [name,mutate]of [
  ['question ID mismatch',v=>v.pending.job.questionId='Q-OTHER'],
  ['question/profile mismatch',v=>v.pending.request.body.profile='other_profile'],
  ['false unavailable flag',v=>v.pending.request.body.unavailable=false],
  ['answer marked transcript',v=>v.pending.attempt.target='transcript'],
  ['attempt question mismatch',v=>v.pending.attempt.questionId='Q-OTHER'],
])test(`followup recovery rejects ${name}`,()=>{const input=answer();mutate(input);assertRejected(input);});
for(const [name,mutate]of [
  ['missing projection key',v=>delete v.pending.attempt.sourceText],
  ['unknown projection key',v=>v.pending.attempt.pick={}],
  ['apiText mismatch',v=>v.pending.attempt.apiText='different'],
  ['future beforeVersion',v=>v.pending.attempt.beforeVersion=4],
  ['oversize question text',v=>v.pending.attempt.questionText='q'.repeat(2001)],
  ['oversize source',v=>v.pending.attempt.sourceText='s'.repeat(16001)],
  ['too many flagged fields',v=>v.pending.attempt.beforeFlagged=Array(65).fill('age')],
  ['unsafe flagged field',v=>v.pending.attempt.beforeFlagged=['age/redirect']],
  ['nonboolean manual',v=>v.pending.attempt.manual='true'],
  ['unsafe trigger action',v=>v.pending.attempt.trigger={action:'submit',command:'synthetic'}],
  ['oversize trigger',v=>v.pending.attempt.trigger={action:'recommend',command:'r'.repeat(101)}],
  ['unknown trigger field',v=>v.pending.attempt.trigger={action:'stop',command:'synthetic',extra:true}],
])test(`attempt projection rejects ${name}`,()=>{const input=transcript();mutate(input);assertRejected(input);});
test('valid trigger metadata and answer optional unavailable omission are preserved without running anything',()=>{
  const input=transcript();input.pending.attempt.trigger={action:'recommend',command:'synthetic recommendation'};assert.deepEqual(writeDraftRecovery(memory(),input,{now:NOW}).pending.attempt.trigger,input.pending.attempt.trigger);
  const optional=answer();delete optional.pending.request.body.unavailable;assert.equal(Object.hasOwn(writeDraftRecovery(memory(),optional,{now:NOW}).pending.request.body,'unavailable'),false);
});
test('malformed nested data loaded from storage is invalid and cannot reveal a partial clinical payload',()=>{
  const input=saved(transcript());input.pending.request.body.actor_id='OTHER-ACTOR';const storage=rawStorage(input),before=storage.getItem(RECOVERY_KEY);
  const result=readDraftRecovery(storage,ORIGIN,{now:NOW});assert.equal(result.status,'invalid');assert.equal(result.payload,null);assert.equal(storage.getItem(RECOVERY_KEY),before);
});
test('functions, undefined, dates, typed audio arrays, accessors, cycles and prototype keys are never silently serialized',()=>{
  const cyclic=payload();cyclic.pending=cyclic;
  const accessor=payload();Object.defineProperty(accessor,'manualText',{enumerable:true,get(){throw Error('must not invoke');}});
  const symbol=payload();symbol[Symbol('hidden')]=1;
  const proto=JSON.parse(JSON.stringify(payload()).replace('"pending":null','"pending":null,"__proto__":{}'));
  for(const invalid of [{...payload(),manualText:undefined},{...payload(),manualText:()=>''},{...payload(),manualText:new Date()},{...payload(),manualText:new Uint8Array([1,2])},cyclic,accessor,symbol,proto])assertRejected(invalid);
});
test('messages do not expose synthetic raw text or actor/session identifiers',()=>{
  const input=saved(transcript());input.pending.request.body.actor_id='OTHER-ACTOR';input.recoveryText='SYNTHETIC_PRIVATE_RAW_TEXT';
  const result=readDraftRecovery(rawStorage(input),ORIGIN,{now:NOW});assert.equal(result.payload,null);assert.doesNotMatch(result.message,/SYNTHETIC_PRIVATE_RAW_TEXT|QUICK-SYNTHETIC|VS-SYNTHETIC/);
});

test('structured correction recovery retains the exact original PATCH and no synthetic transcript',()=>{
  const input=correction(),before=JSON.stringify(input.pending.request),storage=memory();
  writeDraftRecovery(storage,input,{now:NOW});
  input.pending.job.changes.systolic_bp=120;
  const read=readDraftRecovery(storage,ORIGIN,{now:NOW});assert.equal(read.status,'valid');
  assert.equal(JSON.stringify(read.payload.pending.request),before);
  assert.deepEqual(read.payload.pending.job,{phase:'correct',changes:{systolic_bp:110,diastolic_bp:70,symptoms:['흉통'],denied_symptoms:['구토']},unsetFields:['glucose','spo2']});
  assert.equal(read.payload.pending.attempt.target,'correction');assert.equal(read.payload.pending.attempt.apiText,'');
  assert.equal(read.payload.pending.attempt.sourceText,'');assert.equal(read.payload.pending.attempt.trigger,null);
});

test('correction may preserve an older expected version and unrelated unapplied text without marking it as submitted',()=>{
  const input=correction();input.version=5;input.recoveryText='synthetic unapplied recording';input.manualText='synthetic manual note';
  input.pending.attempt.excludedText='synthetic trailing line';input.pending.attempt.unappliedBefore='synthetic prior line';
  const result=writeDraftRecovery(memory(),input,{now:NOW});assert.equal(result.pending.request.body.expected_version,3);
  assert.equal(result.recoveryText,input.recoveryText);assert.equal(result.manualText,input.manualText);
  assert.equal(result.pending.attempt.excludedText,input.pending.attempt.excludedText);
});

for(const [name,mutate] of [
  ['POST method',v=>{v.pending.request.method='POST';}],['unknown field',v=>{v.pending.request.body.changes.age=v.pending.job.changes.age=47;}],
  ['observation change kind',v=>{v.pending.request.body.change_kind='observation';}],['wrong job phase',v=>{v.pending.job.phase='write';}],
  ['body/job changes mismatch',v=>{v.pending.job.changes.systolic_bp=120;}],['body/job unset mismatch',v=>{v.pending.job.unsetFields=['glucose'];}],
  ['conflicting symptoms',v=>{v.pending.request.body.changes.denied_symptoms=['흉통'];v.pending.job.changes.denied_symptoms=['흉통'];}],
  ['invalid measurement',v=>{v.pending.request.body.changes.systolic_bp=0;v.pending.job.changes.systolic_bp=0;}],
  ['empty correction',v=>{v.pending.request.body.changes=v.pending.job.changes={};v.pending.request.body.unset_fields=v.pending.job.unsetFields=[];}],
  ['duplicate unset',v=>{v.pending.request.body.unset_fields=v.pending.job.unsetFields=['glucose','glucose'];}],
  ['change also unset',v=>{v.pending.request.body.unset_fields=v.pending.job.unsetFields=['systolic_bp'];}],
  ['unknown symptom',v=>{v.pending.request.body.changes.symptoms=v.pending.job.changes.symptoms=['임의 증상'];}],
  ['missing known session',v=>{v.sessionId=null;v.version=0;}],['future expected version',v=>{v.pending.request.body.expected_version=4;}],
  ['another actor',v=>{v.pending.request.body.actor_id='OTHER';}],['transcript attempt',v=>{v.pending.attempt.target='transcript';}],
  ['command mode',v=>{v.pending.attempt.mode='command';}],['question ID',v=>{v.pending.attempt.questionId='Q-OTHER';}],
  ['question text',v=>{v.pending.attempt.questionText='not a followup';}],['synthetic API text',v=>{v.pending.attempt.apiText='not a transcript';}],
  ['source transcript',v=>{v.pending.attempt.sourceText='not a recording';}],['manual recording',v=>{v.pending.attempt.manual=true;}],
  ['copied source',v=>{v.pending.attempt.copiedFrom='not a recording';}],['trigger action',v=>{v.pending.attempt.trigger={action:'recommend',command:'synthetic'};}],
  ['utterance ID',v=>{v.pending.request.body.utterance_id='not allowed';}],['patient submit context',v=>{v.pending.request.body.context={consent:true};}],
])test(`correction recovery rejects ${name} without erasing the previous snapshot`,()=>{const input=correction();mutate(input);assertRejected(input);});

for(const [action,command] of [['new_patient','새 환자 시작'],['new_patient','다음 환자 시작해 주세요'],
  ['previous_patient','이전 환자로 돌아가'],['previous_patient','이전 환자 기록으로 돌아가주세요']]){
  for(const make of [transcript,answer])test(`pending ${make.name} preserves ${action} metadata and the unchanged old-patient request`,()=>{
    const input=make(),original=JSON.stringify(input.pending.request),storage=memory();
    input.pending.attempt.trigger={action,command};input.pending.attempt.excludedText='synthetic old-patient trailing line';
    writeDraftRecovery(storage,input,{now:NOW});const result=readDraftRecovery(storage,ORIGIN,{now:NOW});
    assert.equal(result.status,'valid');assert.deepEqual(result.payload.pending.attempt.trigger,{action,command});
    assert.equal(JSON.stringify(result.payload.pending.request),original);
    assert.equal(result.payload.pending.attempt.excludedText,'synthetic old-patient trailing line');
    assert.equal(result.payload.actorId,input.actorId);assert.equal(result.payload.sessionId,input.sessionId);
  });
}

for(const [name,trigger] of [
  ['unknown switch',{action:'switch_patient',command:'새 환자 시작'}],
  ['close patient',{action:'close_patient',command:'새 환자 시작'}],
  ['automatically submit',{action:'submit',command:'새 환자 시작'}],
  ['wrong action type',{action:7,command:'이전 환자로 돌아가'}],
  ['missing command',{action:'new_patient'}],
  ['empty command',{action:'previous_patient',command:''}],
  ['oversized command',{action:'new_patient',command:'x'.repeat(101)}],
  ['hidden target session',{action:'previous_patient',command:'이전 환자로 돌아가',sessionId:'VS-OTHER'}],
])test(`patient-switch recovery rejects ${name} without modifying the old recovery`,()=>{
  const input=transcript();input.pending.attempt.trigger=trigger;assertRejected(input);
});

test('patient-switch actions remain disallowed for structured correction attempts',()=>{
  for(const action of ['new_patient','previous_patient']){
    const input=correction();input.pending.attempt.trigger={action,command:'synthetic patient action'};assertRejected(input);
  }
});

for(const [make,command] of [[transcript,'인계 요약해줘'],[answer,'병원 인계용 요약해 주세요'],[create,'인계 요약 보여줘']])test(`pending ${make.name} preserves dedicated handoff_summary metadata without changing the original API request`,()=>{
  const input=make(),original=JSON.stringify(input.pending.request),storage=memory();
  input.pending.attempt.trigger={action:'handoff_summary',command};input.pending.attempt.excludedText='synthetic unapplied tail';
  writeDraftRecovery(storage,input,{now:NOW});const result=readDraftRecovery(storage,ORIGIN,{now:NOW});
  assert.equal(result.status,'valid');assert.deepEqual(result.payload.pending.attempt.trigger,{action:'handoff_summary',command});
  assert.equal(JSON.stringify(result.payload.pending.request),original);
  assert.equal(result.payload.pending.attempt.excludedText,'synthetic unapplied tail');
});

for(const [name,trigger] of [
  ['automatic clipboard',{action:'copy_handoff',command:'인계 요약해줘'}],
  ['automatic hospital transmission',{action:'send_handoff',command:'인계 요약해줘'}],
  ['extra copy flag',{action:'handoff_summary',command:'인계 요약해줘',copy:true}],
  ['extra destination',{action:'handoff_summary',command:'인계 요약해줘',hospitalId:'H-other'}],
  ['missing phrase',{action:'handoff_summary'}],['oversized phrase',{action:'handoff_summary',command:'x'.repeat(101)}],
])test(`handoff recovery rejects ${name} and preserves the previous snapshot`,()=>{
  const input=transcript();input.pending.attempt.trigger=trigger;assertRejected(input);
});

test('structured correction cannot carry a handoff_summary action',()=>{
  const input=correction();input.pending.attempt.trigger={action:'handoff_summary',command:'인계 요약해줘'};assertRejected(input);
});
