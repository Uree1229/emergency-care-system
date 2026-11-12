import test from 'node:test';
import assert from 'node:assert/strict';
import {
  PRACTICE_KEY,createPracticeAttempt,emptyPracticeDataset,appendPracticeAttempt,
  attachPracticeAudio,annotatePracticeAttempt,importPracticeJSON,exportPracticeJSON,
  exportPracticeCSV,mergePracticeDatasets,readPracticeDataset,writePracticeDataset,
} from '../../paramedic-ui/command-practice-data.mjs';

// Metadata-only regression fixtures. No Blob, microphone, DB or network.
const NOW=1_800_000_000_000,HASH='0123456789abcdef'.repeat(4);
const clone=value=>JSON.parse(JSON.stringify(value));
const attempt=(id='A',extra={})=>createPracticeAttempt({id,source:'human_voice',trialNo:1,rawText:'합성 연습 문장 종료',fragments:['합성 연습 문장 종료'],status:'ok',timing:{startedAt:NOW-1000,endedAt:NOW},referenceText:'합성 연습 문장 종료',referenceConfirmed:true,expectedAction:'stop',...extra},{now:NOW});
const book=(...values)=>values.reduce((value,item)=>appendPracticeAttempt(value,item),emptyPracticeDataset());
const metadata=(extra={})=>({status:'stored',contentType:'audio/webm;codecs=opus',extension:'webm',byteLength:1234,sha256:HASH,startedAt:NOW-1000,endedAt:NOW,errorCode:'',...extra});
const absent=(status='not_recorded',extra={})=>metadata({status,contentType:'',extension:'',byteLength:0,sha256:null,startedAt:null,endedAt:null,...extra});
const invalid=(fn,code='INVALID_PRACTICE_DATA')=>assert.throws(fn,error=>error.code===code);
const attach=(meta=metadata())=>attachPracticeAudio(book(attempt()),'A',meta);

test('legacy transcript-only v1 JSON remains unchanged until audio is explicitly attached',()=>{
  const original=book(attempt()),before=JSON.stringify(original),roundtrip=JSON.parse(exportPracticeJSON(original));
  assert.equal(roundtrip.schema,1);assert.equal(roundtrip.actualAudioSaved,false);assert.equal(Object.hasOwn(roundtrip.attempts[0],'audio'),false);
  assert.equal(exportPracticeJSON(original),before);assert.equal(exportPracticeCSV(original).split('\r\n')[0].includes('audioStatus'),false);
  assert.equal(JSON.stringify(original),before);
});

test('attaching stored metadata upgrades only the audio contract and detaches caller objects',()=>{
  const original=book(attempt()),before=clone(original),descriptor=metadata(),next=attachPracticeAudio(original,'A',descriptor);
  assert.equal(next.schema,2);assert.equal(next.actualAudioSaved,true);assert.equal(next.attempts[0].actualAudioSaved,true);
  assert.deepEqual(next.attempts[0].audio,descriptor);assert.deepEqual(original,before);
  descriptor.byteLength=999;assert.equal(next.attempts[0].audio.byteLength,1234);
  const stripped={...next.attempts[0]};delete stripped.audio;stripped.actualAudioSaved=false;assert.deepEqual(stripped,original.attempts[0]);
});

for(const status of ['stored','memory_only','missing','failed','unavailable','empty','not_recorded','interrupted'])test('audio state '+status+' has the correct durable-storage flags',()=>{
  const descriptor=['stored','memory_only','missing'].includes(status)?metadata({status}):absent(status,{errorCode:status==='failed'?'AUDIO_FAILED':''});
  const result=attach(descriptor);assert.equal(result.schema,2);assert.equal(result.actualAudioSaved,status==='stored');
  assert.equal(result.attempts[0].actualAudioSaved,status==='stored');assert.deepEqual(result.attempts[0].audio,descriptor);
});

test('aggregate saved flag follows all patients while missing metadata retains recovery descriptors',()=>{
  let result=book(attempt('A'),attempt('B'));result=attachPracticeAudio(result,'A',metadata());result=attachPracticeAudio(result,'B',metadata());
  result=attachPracticeAudio(result,'A',metadata({status:'missing',errorCode:'AUDIO_BYTES_MISSING'}));
  assert.equal(result.actualAudioSaved,true);assert.equal(result.attempts[0].actualAudioSaved,false);assert.equal(result.attempts[0].audio.sha256,HASH);
  assert.equal(result.attempts[0].audio.byteLength,1234);result=attachPracticeAudio(result,'B',metadata({status:'missing'}));assert.equal(result.actualAudioSaved,false);
});

test('manual simulation accepts no-recording metadata and rejects all recording states',()=>{
  const original=book(attempt('A',{source:'manual_simulation',stopReason:'manual'})),before=clone(original);
  const allowed=attachPracticeAudio(original,'A',absent());assert.equal(allowed.actualAudioSaved,false);assert.equal(allowed.attempts[0].source,'manual_simulation');
  for(const status of ['stored','memory_only','missing','failed','unavailable','empty','interrupted'])invalid(()=>attachPracticeAudio(original,'A',status==='stored'||status==='memory_only'?metadata({status}):absent(status)));
  assert.deepEqual(original,before);
});

test('unknown attempt ID cannot attach audio to another record',()=>{
  const original=book(attempt()),before=clone(original);invalid(()=>attachPracticeAudio(original,'OTHER',metadata()),'PRACTICE_ATTEMPT_NOT_FOUND');assert.deepEqual(original,before);
});

test('JSON exports descriptors without binary; import preserves historical metadata for UI reconciliation',()=>{
  const original=attach(),raw=exportPracticeJSON(original),imported=importPracticeJSON(raw);
  assert.equal(imported.schema,2);assert.equal(imported.attempts[0].imported,true);assert.equal(imported.actualAudioSaved,true);
  assert.deepEqual(imported.attempts[0].audio,original.attempts[0].audio);assert.equal(Object.hasOwn(imported.attempts[0].audio,'blob'),false);
  assert.equal(Object.hasOwn(imported.attempts[0].audio,'audio_base64'),false);assert.equal(exportPracticeJSON(original),raw);
  // actualAudioSaved records historical verified browser persistence. JSON
  // contains no audio file; UI/ZIP must separately reconcile current IDB bytes.
  const missing=attachPracticeAudio(imported,'A',{...imported.attempts[0].audio,status:'missing',errorCode:'AUDIO_BYTES_MISSING'});
  assert.equal(missing.actualAudioSaved,false);assert.equal(missing.attempts[0].audio.sha256,HASH);assert.equal(imported.actualAudioSaved,true);
});

test('v2 local storage roundtrip preserves flags and descriptors without changing the key or deleting data',()=>{
  const values=new Map(),store={getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,value)};
  const saved=writePracticeDataset(store,attach(),{expectedRaw:null}),loaded=readPracticeDataset(store);
  assert.equal(values.size,1);assert.ok(values.has(PRACTICE_KEY));assert.equal(loaded.status,'valid');assert.deepEqual(loaded.dataset,saved.dataset);
  assert.equal(loaded.dataset.actualAudioSaved,true);assert.equal(loaded.dataset.attempts[0].audio.sha256,HASH);
});

test('merging v1 and v2 retains source records, upgrades schema and deduplicates identical metadata',()=>{
  const legacy=book(attempt('OLD')),incoming=importPracticeJSON(exportPracticeJSON(attach())),before=clone(legacy),merged=mergePracticeDatasets(legacy,incoming);
  assert.equal(merged.schema,2);assert.equal(merged.actualAudioSaved,true);assert.deepEqual(merged.attempts.map(item=>[item.id,item.imported]),[['OLD',false],['A',true]]);
  assert.deepEqual(legacy,before);assert.deepEqual(mergePracticeDatasets(merged,incoming),merged);
  const other=mergePracticeDatasets(attach(),importPracticeJSON(exportPracticeJSON(book(attempt('LEGACY-NEW')))));
  assert.equal(other.schema,2);assert.equal(other.attempts[0].actualAudioSaved,true);assert.equal(Object.hasOwn(other.attempts[1],'audio'),false);
});

test('same-ID audio conflict rejects a whole import instead of silently replacing a recording descriptor',()=>{
  const original=attach(),before=clone(original),incoming=clone(original);incoming.attempts[0].audio.sha256='f'.repeat(64);
  incoming.attempts.unshift(attempt('NEW-PREFIX'));invalid(()=>mergePracticeDatasets(original,incoming),'PRACTICE_ID_CONFLICT');assert.deepEqual(original,before);
});

test('annotation updates intended answers but cannot edit audio, source, captured text or saved flags',()=>{
  const original=attach(),before=clone(original),changed=annotatePracticeAttempt(original,'A',{expectedAction:null,referenceConfirmed:false,notes:'합성 검토'});
  assert.deepEqual(changed.attempts[0].audio,original.attempts[0].audio);assert.equal(changed.actualAudioSaved,true);assert.equal(changed.attempts[0].rawText,original.attempts[0].rawText);
  assert.deepEqual(changed.attempts[0].parserResult,original.attempts[0].parserResult);assert.equal(changed.attempts[0].expectedAction,null);assert.deepEqual(original,before);
  for(const values of [{audio:absent()},{actualAudioSaved:false},{source:'manual_simulation'},{rawText:'changed'}])invalid(()=>annotatePracticeAttempt(original,'A',values));
});

test('v2 CSV adds explicit audio columns while preserving source/provenance and legacy column prefix',()=>{
  const legacyHeader=exportPracticeCSV(book(attempt())).split('\r\n')[0],csv=exportPracticeCSV(attach()),[header,row]=csv.split('\r\n');
  assert.ok(header.startsWith(legacyHeader));assert.ok(header.endsWith(',"audioStatus","audioContentType","audioBytes","audioSHA256"'));
  assert.ok(row.endsWith(',"stored","audio/webm;codecs=opus","1234","'+HASH+'"'));assert.match(row,/"human_voice"/);
});

test('schema and aggregate flags cannot contradict the per-attempt audio state',()=>{
  for(const edit of [value=>{value.schema=1;},value=>{value.actualAudioSaved=false;},value=>{value.attempts[0].actualAudioSaved=false;},value=>{value.attempts[0].audio.status='memory_only';}]){
    const value=attach();edit(value);invalid(()=>exportPracticeJSON(value));
  }
  const noAudio=book(attempt());noAudio.attempts[0].actualAudioSaved=true;noAudio.actualAudioSaved=true;invalid(()=>exportPracticeJSON(noAudio));
});

for(const [label,descriptor,code]of [
  ['null',null],['array',[]],['unknown field',metadata({blob:'not allowed'})],['unknown status',metadata({status:'uploaded'})],
  ['negative bytes',metadata({byteLength:-1})],['fractional bytes',metadata({byteLength:1.5})],['string bytes',metadata({byteLength:'1234'})],
  ['oversized bytes',metadata({byteLength:20*1024*1024+1}),'PRACTICE_LIMIT'],
  ['uppercase hash',metadata({sha256:'A'.repeat(64)})],['short hash',metadata({sha256:'a'.repeat(63)})],['nonhex hash',metadata({sha256:'g'.repeat(64)})],
  ['stored without hash',metadata({sha256:null})],['stored without bytes',metadata({byteLength:0})],['stored without MIME',metadata({contentType:'',extension:''})],
  ['unsupported MIME',metadata({contentType:'text/plain',extension:'txt'})],['MIME/extension mismatch',metadata({extension:'mp4'})],
  ['MIME newline',metadata({contentType:'audio/webm\r\nInjected: true'})],['extension without MIME',absent('not_recorded',{extension:'webm'})],
  ['reversed times',metadata({startedAt:NOW,endedAt:NOW-1})],['negative time',metadata({startedAt:-1})],['fractional time',metadata({endedAt:NOW+0.5})],['string time',metadata({startedAt:'yesterday'})],
])test('invalid audio descriptor rejects without touching original: '+label,()=>{
  const original=book(attempt()),before=clone(original);invalid(()=>attachPracticeAudio(original,'A',descriptor),code);assert.deepEqual(original,before);
});

test('all required descriptor fields are required and accessor/symbol properties are rejected',()=>{
  for(const key of Object.keys(metadata())){const descriptor=metadata();delete descriptor[key];invalid(()=>attach(descriptor));}
  const accessor=metadata();Object.defineProperty(accessor,'status',{get:()=> 'stored',enumerable:true});invalid(()=>attach(accessor));
  const symbolic=metadata();symbolic[Symbol('audio')]='hidden';invalid(()=>attach(symbolic));
});

test('supported native MIME parameters, size boundary and optional unknown capture times survive metadata export',()=>{
  for(const [contentType,extension]of [['audio/webm;codecs=opus','webm'],['audio/ogg;codecs=opus','ogg'],['audio/mp4;codecs=mp4a.40.2','mp4']]){
    const result=attach(metadata({contentType,extension,byteLength:20*1024*1024,startedAt:null,endedAt:null}));
    assert.equal(result.attempts[0].audio.contentType,contentType);assert.equal(result.attempts[0].audio.byteLength,20*1024*1024);assert.equal(result.actualAudioSaved,true);
    assert.deepEqual(JSON.parse(exportPracticeJSON(result)).attempts[0].audio,result.attempts[0].audio);
  }
});
