import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {RECOVERY_KEY,writeDraftRecovery,readDraftRecovery} from '../../paramedic-ui/draft-recovery.mjs';
import {PATIENT_RECORDS_KEY,emptyBook,readPatientRecords,writePatientRecords,createPatientRecord,updatePatientCheckpoint,selectPatientRecord,removePatientRecord} from '../../paramedic-ui/patient-records.mjs';

const NOW=1_800_000_000_000,TTL=43_200_000,ORIGIN='http://127.0.0.1:18042';
const clone=value=>JSON.parse(JSON.stringify(value));
function memory(initial={}){const data=new Map(Object.entries(initial));let writes=0;return {data,get writes(){return writes;},getItem:key=>data.has(key)?data.get(key):null,setItem:(key,value)=>{writes++;data.set(key,String(value));},removeItem:key=>data.delete(key)};}
function checkpoint(actorId='ACTOR-A',sessionId='VS-A',version=3,{now=NOW-100,manualText='',recoveryText='',pending=null}={}){
  const storage=memory();writeDraftRecovery(storage,{serverOrigin:ORIGIN,actorId,sessionId,version,pending,recoveryText,manualText,mode:'auto',recordingMode:'toggle'},{now});return storage.getItem(RECOVERY_KEY);
}
function one(){return updatePatientCheckpoint(createPatientRecord(emptyBook(ORIGIN),'NOTE-A',{now:NOW-200}),'NOTE-A',checkpoint(),{now:NOW-100});}
function two(){return updatePatientCheckpoint(createPatientRecord(one(),'NOTE-B',{now:NOW-50}),'NOTE-B',checkpoint('ACTOR-B','VS-B',2,{now:NOW-40}),{now:NOW-40});}
function storeBook(book){return memory({[PATIENT_RECORDS_KEY]:JSON.stringify(book)});}
function rejectWrite(book){const storage=memory({[PATIENT_RECORDS_KEY]:'previous-synthetic-raw'});assert.throws(()=>writePatientRecords(storage,book,{expectedRaw:'previous-synthetic-raw',now:NOW}));assert.equal(storage.getItem(PATIENT_RECORDS_KEY),'previous-synthetic-raw');assert.equal(storage.writes,0);}

test('the notebook key and empty envelope have the exact public contract',()=>{
  assert.equal(PATIENT_RECORDS_KEY,'paramedic.voice.patient-records.v1');assert.deepEqual(emptyBook('HTTP://LOCALHOST:80/'),{schema:1,serverOrigin:'http://localhost',activeId:null,previousId:null,nextNumber:1,records:[]});
});
test('first create assigns number one and the caller-provided ID without creating a clinical checkpoint',()=>{
  const original=emptyBook(ORIGIN),result=createPatientRecord(original,'NOTE-A',{now:NOW});assert.deepEqual(original,emptyBook(ORIGIN));
  assert.deepEqual(result,{schema:1,serverOrigin:ORIGIN,activeId:'NOTE-A',previousId:null,nextNumber:2,records:[{id:'NOTE-A',number:1,checkpoint:null,createdAt:NOW,updatedAt:NOW}]});
});
test('creating another patient moves active to previous and never copies the earlier checkpoint',()=>{
  const original=one(),before=clone(original),result=createPatientRecord(original,'NOTE-B',{now:NOW});
  assert.equal(result.activeId,'NOTE-B');assert.equal(result.previousId,'NOTE-A');assert.equal(result.records[1].checkpoint,null);assert.equal(result.records[1].number,2);assert.equal(result.nextNumber,3);assert.deepEqual(original,before);
});
test('select returns an independent book and changes only the selection pointers',()=>{
  const original=two(),before=clone(original),result=selectPatientRecord(original,'NOTE-A');assert.equal(result.activeId,'NOTE-A');assert.equal(result.previousId,'NOTE-B');assert.deepEqual(result.records,original.records);assert.deepEqual(original,before);result.records[0].number=999;assert.deepEqual(original,before);
});
test('selecting the already active patient preserves previous and does not touch timestamps',()=>{const original=two();assert.deepEqual(selectPatientRecord(original,'NOTE-B'),original);});
test('deleting active patient leaves no automatically selected replacement patient',()=>{
  const original=two(),result=removePatientRecord(original,'NOTE-B');assert.equal(result.activeId,null);assert.equal(result.previousId,null);assert.equal(result.records[0].id,'NOTE-A');assert.equal(result.nextNumber,3);assert.equal(original.activeId,'NOTE-B');
});
test('deleting previous leaves the current active patient unchanged',()=>{const result=removePatientRecord(two(),'NOTE-A');assert.equal(result.activeId,'NOTE-B');assert.equal(result.previousId,null);assert.equal(result.records[0].id,'NOTE-B');});
test('deleting an unrelated inactive patient preserves both remaining pointers',()=>{
  let book=createPatientRecord(two(),'NOTE-C',{now:NOW});book=selectPatientRecord(book,'NOTE-A');const result=removePatientRecord(book,'NOTE-B');assert.equal(result.activeId,'NOTE-A');assert.equal(result.previousId,'NOTE-C');assert.equal(result.records.length,2);
});
test('patient numbers are never recycled after deletion, including an empty book',()=>{
  let book=removePatientRecord(removePatientRecord(two(),'NOTE-B'),'NOTE-A');assert.equal(book.records.length,0);assert.equal(book.nextNumber,3);book=createPatientRecord(book,'NOTE-C',{now:NOW});assert.equal(book.records[0].number,3);assert.equal(book.nextNumber,4);
});
test('updating a checkpoint affects its owning record only and preserves the exact raw string',()=>{
  const original=two(),before=clone(original),raw=checkpoint('ACTOR-A','VS-A',4,{now:NOW,manualText:'  synthetic\nreview  '});const result=updatePatientCheckpoint(original,'NOTE-A',raw,{now:NOW});
  assert.equal(result.records[0].checkpoint,raw);assert.equal(result.records[0].updatedAt,NOW);assert.equal(result.records[0].createdAt,NOW-200);assert.deepEqual(result.records[1],original.records[1]);assert.equal(result.activeId,original.activeId);assert.deepEqual(original,before);
});
test('an empty record permits an empty checkpoint but never creates an actor or server session',()=>{
  const book=createPatientRecord(emptyBook(ORIGIN),'NOTE-A',{now:NOW});assert.deepEqual(updatePatientCheckpoint(book,'NOTE-A',null,{now:NOW}),book);
});
test('initial pending create can progress from null session/version zero to the same actor known session',()=>{
  let book=createPatientRecord(emptyBook(ORIGIN),'NOTE-A',{now:NOW-200});book=updatePatientCheckpoint(book,'NOTE-A',checkpoint('ACTOR-A',null,0),{now:NOW-100});
  const result=updatePatientCheckpoint(book,'NOTE-A',checkpoint('ACTOR-A','VS-A',1,{now:NOW}),{now:NOW});assert.equal(JSON.parse(result.records[0].checkpoint).sessionId,'VS-A');
});
for(const [actor,session,version]of [['ACTOR-OTHER','VS-A',4],['ACTOR-A','VS-OTHER',4],['ACTOR-A',null,0],['ACTOR-A','VS-A',2]])test(`own note identity/version cannot be rebound to ${actor}/${session}/${version}`,()=>{
  const original=one(),before=clone(original);assert.throws(()=>updatePatientCheckpoint(original,'NOTE-A',checkpoint(actor,session,version,{now:NOW}),{now:NOW}));assert.deepEqual(original,before);
});
test('clearing a known checkpoint requires explicit record removal rather than silently forgetting its identity',()=>assert.throws(()=>updatePatientCheckpoint(one(),'NOTE-A',null,{now:NOW})));
test('same version exact checkpoint refresh and higher version are allowed for the same identity',()=>{
  const book=one();assert.equal(updatePatientCheckpoint(book,'NOTE-A',book.records[0].checkpoint,{now:NOW}).records[0].updatedAt,NOW);assert.equal(JSON.parse(updatePatientCheckpoint(book,'NOTE-A',checkpoint('ACTOR-A','VS-A',4,{now:NOW}),{now:NOW}).records[0].checkpoint).version,4);
});
test('expired checkpoints are retained verbatim while strict recovery refuses automatic restore',()=>{
  const old=NOW-TTL-1,raw=checkpoint('ACTOR-EXPIRED','VS-EXPIRED',1,{now:old});let book=createPatientRecord(emptyBook(ORIGIN),'NOTE-A',{now:old});book=updatePatientCheckpoint(book,'NOTE-A',raw,{now:NOW});
  const storage=memory(),written=writePatientRecords(storage,book,{expectedRaw:null,now:NOW}),read=readPatientRecords(storage,ORIGIN,{now:NOW});assert.equal(read.status,'valid');assert.equal(read.book.records[0].checkpoint,raw);assert.equal(read.raw,written.raw);assert.equal(readDraftRecovery({getItem:()=>raw},ORIGIN,{now:NOW}).status,'expired');
});
test('the exact 128 KiB raw checkpoint limit is accepted and one extra byte is rejected',()=>{
  const base=checkpoint(),raw=base+' '.repeat(131072-new TextEncoder().encode(base).byteLength);const book=createPatientRecord(emptyBook(ORIGIN),'NOTE-A',{now:NOW-200});
  const result=updatePatientCheckpoint(book,'NOTE-A',raw,{now:NOW});assert.equal(result.records[0].checkpoint,raw);assert.throws(()=>updatePatientCheckpoint(book,'NOTE-A',raw+' ',{now:NOW}));assert.equal(book.records[0].checkpoint,null);
});
test('twenty empty notes fit and the twenty-first is rejected without auto eviction or mutation',()=>{
  let book=emptyBook(ORIGIN);for(let i=0;i<20;i++)book=createPatientRecord(book,'NOTE-'+i,{now:NOW});const before=clone(book);assert.throws(()=>createPatientRecord(book,'NOTE-20',{now:NOW}),error=>error.code==='PATIENT_RECORDS_LIMIT');assert.deepEqual(book,before);assert.equal(writePatientRecords(memory(),book,{expectedRaw:null,now:NOW}).book.records.length,20);
});
test('the 1 MiB notebook limit counts UTF8 bytes, never silently truncates or evicts records',()=>{
  let book=emptyBook(ORIGIN);for(let i=0;i<20;i++)book=createPatientRecord(book,'NOTE-'+i,{now:NOW-200});
  let rejected=false;for(let i=0;i<20;i++){
    const raw=checkpoint('ACTOR-'+i,'VS-'+i,1,{manualText:'가'.repeat(16000),recoveryText:'나'.repeat(16000)}),before=clone(book);
    try{book=updatePatientCheckpoint(book,'NOTE-'+i,raw,{now:NOW});}
    catch(error){assert.equal(error.code,'PATIENT_RECORDS_LIMIT');assert.deepEqual(book,before);rejected=true;break;}
  }
  assert.equal(rejected,true);assert.equal(book.records.length,20);assert.ok(JSON.stringify(book).length<1048576);assert.ok(new TextEncoder().encode(JSON.stringify(book)).byteLength<=1048576);
});
for(const kind of ['actor','session'])test(`duplicate ${kind} across patient checkpoints is rejected, including another note with a different identity component`,()=>{
  const book=two();book.records[1].checkpoint=kind==='actor'?checkpoint('ACTOR-A','VS-DIFFERENT',1):checkpoint('ACTOR-DIFFERENT','VS-A',1);rejectWrite(book);assert.equal(readPatientRecords(storeBook(book),ORIGIN,{now:NOW}).status,'invalid');
});
test('expired checkpoints still participate in cross-note actor/session uniqueness checks',()=>{
  const book=two();book.records[1].checkpoint=checkpoint('ACTOR-A','VS-OTHER',1,{now:NOW-TTL-1});rejectWrite(book);
});
test('multiple unsent records may have null sessions when their actors are different',()=>{
  const book=two();book.records[0].checkpoint=checkpoint('ACTOR-A',null,0);book.records[1].checkpoint=checkpoint('ACTOR-B',null,0);assert.equal(writePatientRecords(memory(),book,{expectedRaw:null,now:NOW}).book.records.length,2);
});
for(const operation of ['select','remove','update'])test(`unknown ${operation} record ID is explicit failure with no book mutation`,()=>{
  const book=two(),before=clone(book);assert.throws(()=>operation==='select'?selectPatientRecord(book,'NOTE-NOT-FOUND'):operation==='remove'?removePatientRecord(book,'NOTE-NOT-FOUND'):updatePatientCheckpoint(book,'NOTE-NOT-FOUND',null,{now:NOW}),error=>error.code==='PATIENT_RECORD_NOT_FOUND');assert.deepEqual(book,before);
});
test('duplicate record ID creation is rejected independently of patient numbering',()=>assert.throws(()=>createPatientRecord(two(),'NOTE-A',{now:NOW})));
for(const id of ['', 'A', 'NOTE-', 'NOTE--A', 'NOTE-a/b', 'NOTE-한글', 'NOTE-'+ 'x'.repeat(124)])test(`invalid patient notebook ID ${id} is rejected`,()=>assert.throws(()=>createPatientRecord(emptyBook(ORIGIN),id,{now:NOW})));
test('the exact 128-character NOTE ID boundary and safe ASCII punctuation are accepted',()=>{
  assert.equal(createPatientRecord(emptyBook(ORIGIN),'NOTE-'+ 'x'.repeat(123),{now:NOW}).activeId.length,128);assert.equal(createPatientRecord(emptyBook(ORIGIN),'NOTE-A_b.c:d@e-f',{now:NOW}).activeId,'NOTE-A_b.c:d@e-f');
});
test('new notebook writes require an explicit expectedRaw null and return detached normalized data',()=>{
  const storage=memory(),book=one(),written=writePatientRecords(storage,book,{expectedRaw:null,now:NOW});assert.equal(storage.getItem(PATIENT_RECORDS_KEY),written.raw);assert.deepEqual(JSON.parse(written.raw),written.book);written.book.records[0].number=999;assert.equal(readPatientRecords(storage,ORIGIN,{now:NOW}).book.records[0].number,1);assert.equal(book.records[0].number,1);
});
test('missing expectedRaw is rejected before touching storage',()=>{const storage=memory();assert.throws(()=>writePatientRecords(storage,emptyBook(ORIGIN),{now:NOW}));assert.equal(storage.writes,0);});
test('a successful existing notebook write compares the exact raw and preserves independent notes',()=>{
  const storage=memory(),first=writePatientRecords(storage,one(),{expectedRaw:null,now:NOW});const second=writePatientRecords(storage,createPatientRecord(first.book,'NOTE-B',{now:NOW}),{expectedRaw:first.raw,now:NOW});assert.equal(second.book.records.length,2);assert.equal(second.book.records[0].checkpoint,first.book.records[0].checkpoint);assert.equal(storage.writes,2);
});
for(const expectedRaw of [null,'stale raw',''])test(`stale expected raw ${JSON.stringify(expectedRaw)} never overwrites another tab`,()=>{
  const storage=memory({[PATIENT_RECORDS_KEY]:'newer-raw'});assert.throws(()=>writePatientRecords(storage,one(),{expectedRaw,now:NOW}),error=>error.code==='STALE_PATIENT_RECORDS');assert.equal(storage.getItem(PATIENT_RECORDS_KEY),'newer-raw');assert.equal(storage.writes,0);
});
test('quota errors keep the previous durable envelope and expose storage failure rather than success',()=>{
  const previous=JSON.stringify(one()),storage=memory({[PATIENT_RECORDS_KEY]:previous});storage.setItem=()=>{throw Error('QuotaExceededError');};assert.throws(()=>writePatientRecords(storage,two(),{expectedRaw:previous,now:NOW}),error=>error.code==='PATIENT_RECORDS_STORAGE_UNAVAILABLE');assert.equal(storage.getItem(PATIENT_RECORDS_KEY),previous);
});
test('unavailable or denied read/write storage is explicit and never mistaken for an empty notebook',()=>{
  for(const storage of [null,{}, {getItem(){throw Error('denied');},setItem(){throw Error('must not write');}}]){assert.equal(readPatientRecords(storage,ORIGIN,{now:NOW}).status,'invalid');assert.throws(()=>writePatientRecords(storage,one(),{expectedRaw:null,now:NOW}),error=>error.code==='PATIENT_RECORDS_STORAGE_UNAVAILABLE');}
});
test('missing notebook is absent and does not create or change any browser key',()=>{const storage=memory({other:'keep'});assert.deepEqual(readPatientRecords(storage,ORIGIN,{now:NOW}),{status:'absent',book:null,raw:null,message:'No patient notebook is saved.'});assert.equal(storage.writes,0);assert.deepEqual(Object.fromEntries(storage.data),{other:'keep'});});
test('read preserves the original raw, never consumes entries and returns independent books',()=>{
  const book=two(),raw=' '+JSON.stringify(book)+'\n',storage=memory({[PATIENT_RECORDS_KEY]:raw}),before=new Map(storage.data),first=readPatientRecords(storage,ORIGIN,{now:NOW});assert.equal(first.status,'valid');assert.equal(first.raw,raw);first.book.records[0].checkpoint=null;assert.equal(readPatientRecords(storage,ORIGIN,{now:NOW}).book.records[0].checkpoint,book.records[0].checkpoint);assert.deepEqual(storage.data,before);assert.equal(storage.writes,0);
});
test('legacy recovery, handoff, normal actor/session and other keys remain untouched until root explicitly migrates them',()=>{
  const other={[RECOVERY_KEY]:'legacy recovery','paramedic.voice.handoff.v1':'handoff','paramedic.voice.actor':'main actor','paramedic.voice.session':'main session',other:'keep'},storage=memory(other);writePatientRecords(storage,one(),{expectedRaw:null,now:NOW});readPatientRecords(storage,ORIGIN,{now:NOW});for(const [key,value]of Object.entries(other))assert.equal(storage.getItem(key),value);
});
test('malformed saved envelopes return invalid with their exact raw for an explicit guarded replacement',()=>{
  for(const raw of ['', '{bad','null','[]','1','x'.repeat(1048577)]){const storage=memory({[PATIENT_RECORDS_KEY]:raw}),result=readPatientRecords(storage,ORIGIN,{now:NOW});assert.equal(result.status,'invalid');assert.equal(result.book,null);assert.equal(result.raw,raw);assert.equal(storage.getItem(PATIENT_RECORDS_KEY),raw);}
});
for(const mutate of [b=>b.schema=2,b=>b.activeId='NOTE-UNKNOWN',b=>b.previousId='NOTE-UNKNOWN',b=>b.previousId=b.activeId,b=>b.nextNumber=0,b=>b.nextNumber=2,b=>b.records[1].id='NOTE-A',b=>b.records[1].number=1,b=>b.records[0].number=0,b=>b.records[0].createdAt=NOW+1,b=>b.records[0].updatedAt=NOW+1,b=>b.records[0].createdAt=NOW,b=>b.extra=true,b=>b.records[0].clinicalSnapshot={},b=>delete b.records[0].checkpoint])test(`strict envelope/record schema rejects mutation ${mutate.toString()}`,()=>{const book=two();mutate(book);rejectWrite(book);});
for(const raw of ['{bad','null','[]',checkpoint('ACTOR-FUTURE','VS-FUTURE',1,{now:NOW+1}),JSON.stringify({...JSON.parse(checkpoint()),serverOrigin:'http://other.test'}),JSON.stringify({...JSON.parse(checkpoint()),rawAudio:'forbidden'})])test(`malformed, future or cross-origin checkpoint is rejected (${raw.length} chars)`,()=>{const book=createPatientRecord(emptyBook(ORIGIN),'NOTE-A',{now:NOW-200});assert.throws(()=>updatePatientCheckpoint(book,'NOTE-A',raw,{now:NOW}));assert.equal(book.records[0].checkpoint,null);});
for(const origin of ['http://other.test','https://127.0.0.1:18042','http://127.0.0.1:18043',ORIGIN+'/path'])test(`read enforces exact notebook origin ${origin}`,()=>{const result=readPatientRecords(storeBook(one()),origin,{now:NOW});assert.equal(result.status,'invalid');assert.equal(result.book,null);});
for(const origin of ['https://example.test/path','https://example.test/?q=1','https://user:pass@example.test','https://example.test/#','ftp://example.test','//example.test',' https://example.test'])test(`unsafe empty notebook origin is rejected ${origin}`,()=>assert.throws(()=>emptyBook(origin)));
for(const now of [-1,NaN,Infinity,'0',Number.MAX_SAFE_INTEGER])test(`invalid notebook clock ${String(now)} cannot write, create or restore`,()=>{assert.throws(()=>createPatientRecord(emptyBook(ORIGIN),'NOTE-A',{now}));assert.throws(()=>writePatientRecords(memory(),one(),{expectedRaw:null,now}));assert.equal(readPatientRecords(storeBook(one()),ORIGIN,{now}).status,'invalid');});
test('non-plain, accessor, sparse, symbol and unknown-key objects cannot be silently transformed into a notebook',()=>{
  const getter=one();Object.defineProperty(getter,'records',{enumerable:true,get(){throw Error('must not invoke');}});
  const sparse=one();sparse.records.length=2;
  const symbol=one();symbol[Symbol('hidden')]=true;
  const accessor=one();Object.defineProperty(accessor.records,0,{enumerable:true,get(){throw Error('must not invoke');}});
  for(const book of [getter,sparse,symbol,accessor,{...one(),records:new Date()},JSON.parse(JSON.stringify(one()).replace('"schema":1','"schema":1,"__proto__":{}'))])rejectWrite(book);
});
test('plain UI books from another JavaScript realm are accepted',()=>{
  const book=vm.runInNewContext('('+JSON.stringify(one())+')');assert.equal(writePatientRecords(memory(),book,{expectedRaw:null,now:NOW}).book.records[0].id,'NOTE-A');
});
test('stored retry request order, clinical text and idempotency IDs remain byte-for-byte inside the checkpoint',()=>{
  const raw=checkpoint('ACTOR-PENDING','VS-PENDING',1,{pending:{request:{path:'/api/voice/sessions/VS-PENDING/transcripts',method:'POST',body:{text:'  synthetic pending line  ',mode:'dictation',utterance_id:'U-SYNTHETIC',is_final:true,actor_id:'ACTOR-PENDING',client_request_id:'R-SYNTHETIC',expected_version:1}},job:{text:'  synthetic pending line  ',mode:'dictation',utteranceId:'U-SYNTHETIC',phase:'write'},attempt:{target:'transcript',mode:'dictation',questionId:null,questionText:'',apiText:'  synthetic pending line  ',sourceText:'synthetic original',excludedText:'',unappliedBefore:'',manual:false,copiedFrom:'',beforeVersion:1,beforeFlagged:[],trigger:null}}});
  let book=createPatientRecord(emptyBook(ORIGIN),'NOTE-PENDING',{now:NOW-200});book=updatePatientCheckpoint(book,'NOTE-PENDING',raw,{now:NOW});const storage=memory();writePatientRecords(storage,book,{expectedRaw:null,now:NOW});assert.equal(readPatientRecords(storage,ORIGIN,{now:NOW}).book.records[0].checkpoint,raw);
});
test('a real pending create checkpoint retains its null session, original request ID and unwritten clinical job',()=>{
  const raw=checkpoint('ACTOR-CREATE',null,0,{pending:{request:{path:'/api/voice/sessions',method:'POST',body:{actor_id:'ACTOR-CREATE',client_request_id:'R-CREATE-SYNTHETIC'}},job:{text:'synthetic first clinical line',mode:'auto',utteranceId:'U-CREATE-SYNTHETIC',phase:'create'},attempt:{target:'transcript',mode:'auto',questionId:null,questionText:'',apiText:'synthetic first clinical line',sourceText:'synthetic original first line',excludedText:'',unappliedBefore:'',manual:false,copiedFrom:'',beforeVersion:0,beforeFlagged:[],trigger:null}}});
  let book=createPatientRecord(emptyBook(ORIGIN),'NOTE-CREATE',{now:NOW-200});book=updatePatientCheckpoint(book,'NOTE-CREATE',raw,{now:NOW});book=createPatientRecord(book,'NOTE-EMPTY',{now:NOW});book=selectPatientRecord(book,'NOTE-CREATE');
  const storage=memory();writePatientRecords(storage,book,{expectedRaw:null,now:NOW});const read=readPatientRecords(storage,ORIGIN,{now:NOW});
  assert.equal(read.book.records[0].checkpoint,raw);assert.equal(read.book.records[1].checkpoint,null);assert.equal(JSON.parse(read.book.records[0].checkpoint).sessionId,null);assert.equal(JSON.parse(read.book.records[0].checkpoint).pending.request.body.client_request_id,'R-CREATE-SYNTHETIC');
});
test('errors expose neither synthetic clinical text nor server actor/session identifiers',()=>{
  const book=one();book.records[0].checkpoint=JSON.stringify({...JSON.parse(checkpoint()),manualText:'SYNTHETIC_PRIVATE_CLINICAL',actorId:'BAD/ACTOR'});const result=readPatientRecords(storeBook(book),ORIGIN,{now:NOW});assert.equal(result.status,'invalid');assert.doesNotMatch(result.message,/SYNTHETIC_PRIVATE_CLINICAL|ACTOR-A|VS-A/);
});
