import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {spawnSync} from 'node:child_process';
import {buildPracticeAudioBundle,PRACTICE_BUNDLE_LIMITS} from '../../paramedic-ui/practice-audio-bundle.mjs';
import * as practiceData from '../../paramedic-ui/command-practice-data.mjs';
const {createPracticeAttempt,emptyPracticeDataset,appendPracticeAttempt,exportPracticeJSON}=practiceData;

// Small explicit synthetic byte fixtures, not an actual microphone recording.
const NOW=1_800_000_000_000;
const digest=value=>createHash('sha256').update(value).digest('hex');
const voice=(id='CP-A',extra={})=>createPracticeAttempt({id,source:'human_voice',trialNo:1,rawText:'합성 연습 문장 종료',fragments:['합성 연습 문장 종료'],status:'ok',timing:{startedAt:NOW-1000,endedAt:NOW},...extra},{now:NOW});
const book=(...attempts)=>attempts.reduce((data,attempt)=>appendPracticeAttempt(data,attempt),emptyPracticeDataset());
const asset=(attemptId='CP-A',bytes=new Uint8Array([0x1a,0x45,0xdf,0xa3,0,1,2,255]),extra={})=>({attemptId,availability:'available',blob:new Blob([bytes],{type:'audio/webm;codecs=opus'}),size:bytes.length,byteLength:bytes.length,sha256:digest(bytes),contentType:'audio/webm;codecs=opus',extension:'webm',snapshot:voice(attemptId),durable:false,...extra});
const reject=async(fn,code='INVALID_PRACTICE_AUDIO')=>assert.rejects(fn,error=>error.code===code);

function readZIP(bytes){
  // Independent standard-library ZIP reader verifies the archive, central/local
  // entries and CRC32. It runs from stdin only; no files/DB/network are created.
  const script="import sys,io,zipfile,json,base64\nz=zipfile.ZipFile(io.BytesIO(sys.stdin.buffer.read()))\nassert z.testzip() is None\nprint(json.dumps([{'name':i.filename,'method':i.compress_type,'flags':i.flag_bits,'crc':i.CRC,'size':i.file_size,'data':base64.b64encode(z.read(i)).decode()} for i in z.infolist()]))";
  const result=spawnSync('python',['-c',script],{input:Buffer.from(bytes),encoding:'utf8',windowsHide:true,maxBuffer:8*1024*1024});
  assert.equal(result.status,0,result.stderr||'Python zipfile interoperability');
  return JSON.parse(result.stdout).map(file=>({...file,bytes:Buffer.from(file.data,'base64')}));
}

test('stored ZIP interoperates with Python zipfile and preserves exact audio, JSON and CSV',async()=>{
  const dataset=book(voice()),entry=asset(),before=exportPracticeJSON(dataset),result=await buildPracticeAudioBundle(dataset,[entry],{now:NOW});
  assert.equal(result.blob.type,'application/zip');assert.equal(result.filename,'command-practice-'+NOW+'.zip');
  const files=readZIP(await result.blob.arrayBuffer()),byName=new Map(files.map(file=>[file.name,file]));
  assert.equal(files[0].name,'manifest.json');assert.ok(files.every(file=>file.method===0&&file.flags===0x0800));
  assert.equal(byName.get('dataset-original.json').bytes.toString('utf8'),before);assert.match(byName.get('attempts.csv').bytes.toString('utf8'),/actualAudioSaved/);
  const enriched=JSON.parse(byName.get('dataset.json').bytes.toString('utf8'));assert.equal(enriched.schema,2);assert.equal(enriched.attempts[0].audio.status,'memory_only');
  assert.equal(enriched.attempts[0].actualAudioSaved,false);assert.equal(enriched.attempts[0].audio.sha256,entry.sha256);
  const manifest=JSON.parse(byName.get('manifest.json').bytes.toString('utf8'));assert.deepEqual(manifest,result.manifest);
  const row=manifest.audio[0];assert.equal(row.attemptId,'CP-A');assert.equal(row.availability,'available');
  assert.deepEqual(byName.get(row.path).bytes,Buffer.from(await entry.blob.arrayBuffer()));assert.equal(row.sha256,digest(byName.get(row.path).bytes));
  assert.equal(row.mime,'audio/webm;codecs=opus');assert.equal(manifest.actualAudioIncluded,true);
  for(const file of manifest.files){assert.equal(file.size,byName.get(file.path).bytes.length);assert.equal(file.sha256,digest(byName.get(file.path).bytes));}
  assert.equal(exportPracticeJSON(dataset),before,'Input remains untouched');assert.equal(dataset.actualAudioSaved,false);
  assert.match(byName.get('README.txt').bytes.toString(),/dataset-original.json preserves/);
});

test('legacy and manual trials have no fabricated bytes and remain explicitly not recorded',async()=>{
  const dataset=book(voice('OLD'),voice('MANUAL',{source:'manual_simulation',stopReason:'manual'}));
  const result=await buildPracticeAudioBundle(dataset,[],{now:NOW}),files=readZIP(await result.blob.arrayBuffer());
  assert.equal(result.manifest.actualAudioIncluded,false);assert.deepEqual(result.manifest.audio.map(row=>[row.attemptId,row.availability,row.path,row.sha256]),[['OLD','not_recorded',null,null],['MANUAL','not_recorded',null,null]]);
  assert.equal(files.filter(file=>file.name.startsWith('audio/')).length,0);
});

test('missing and read errors are represented rather than synthesized as silent recordings',async()=>{
  const result=await buildPracticeAudioBundle(book(voice('MISSING'),voice('ERROR')),[{attemptId:'MISSING',availability:'missing'},{attemptId:'ERROR',availability:'error',errorCode:'AUDIO_STORE_UNAVAILABLE'}],{now:NOW});
  assert.deepEqual(result.manifest.audio.map(row=>[row.availability,row.size,row.errorCode]),[['missing',0,null],['error',0,'AUDIO_STORE_UNAVAILABLE']]);
  assert.equal(readZIP(await result.blob.arrayBuffer()).filter(file=>file.name.startsWith('audio/')).length,0);
});

test('IDs containing reserved path characters are encoded into a local fixed audio directory',async()=>{
  const id='CP:part@x',result=await buildPracticeAudioBundle(book(voice(id)),[asset(id)],{now:NOW});
  assert.equal(result.manifest.audio[0].path,'audio/0001-CP%3Apart%40x.webm');
  assert.ok(readZIP(await result.blob.arrayBuffer()).every(file=>!file.name.startsWith('/')&&!file.name.includes('..')&&!file.name.includes('\\')));
});

for(const [label,mutate]of [
  ['orphan ID',entry=>({...entry,attemptId:'OTHER'})],['duplicate ID',entry=>[entry,entry]],
  ['size mismatch',entry=>({...entry,size:entry.size+1})],['byteLength mismatch',entry=>({...entry,byteLength:entry.byteLength+1})],['hash mismatch',entry=>({...entry,sha256:'0'.repeat(64)})],
  ['malformed hash',entry=>({...entry,sha256:'not-a-hash'})],['MIME mismatch',entry=>({...entry,contentType:'audio/ogg'})],
  ['extension mismatch',entry=>({...entry,extension:'../../evil'})],['unsupported MIME',entry=>({...entry,blob:new Blob(['data'],{type:'text/html'})})],
  ['no available Blob',entry=>({...entry,blob:null})],['missing capture snapshot',entry=>({...entry,snapshot:undefined})],['changed capture snapshot',entry=>({...entry,snapshot:{...entry.snapshot,rawText:'다른 합성 원문'}})],['zero bytes',entry=>({...entry,blob:new Blob([],{type:'audio/webm'}),size:0})],
  ['unrecognized availability',entry=>({...entry,availability:'saved'})],['bytes attached to missing',entry=>({...entry,availability:'missing'})],
])test('inconsistent audio rejects whole bundle: '+label,async()=>{
  const input=mutate(asset());await reject(()=>buildPracticeAudioBundle(book(voice()),Array.isArray(input)?input:[input],{now:NOW}));
});

test('manual source cannot be relabelled as an available microphone recording',async()=>{
  await reject(()=>buildPracticeAudioBundle(book(voice('CP-A',{source:'manual_simulation',stopReason:'manual'})),[asset()],{now:NOW}));
});

test('Blob read errors reject without substituting fabricated or truncated binary data',async()=>{
  class Unreadable extends Blob{async arrayBuffer(){throw Error('Synthetic unreadable audio');}}
  const entry=asset('CP-A',new Uint8Array([1]));entry.blob=new Unreadable([new Uint8Array([1])],{type:entry.contentType});
  await assert.rejects(()=>buildPracticeAudioBundle(book(voice()),[entry],{now:NOW}),/Synthetic unreadable audio/);
});

test('Blob size and actual bytes must agree',async()=>{
  class Truncated extends Blob{async arrayBuffer(){return new ArrayBuffer(0);}}
  const entry=asset('CP-A',new Uint8Array([1]));entry.blob=new Truncated([new Uint8Array([1])],{type:entry.contentType});
  await reject(()=>buildPracticeAudioBundle(book(voice()),[entry],{now:NOW}));
});

test('large audio is bounded before its bytes are read',async()=>{
  let read=false;class Oversized extends Blob{get size(){return PRACTICE_BUNDLE_LIMITS.audioFileBytes+1;}async arrayBuffer(){read=true;throw Error('Must not read');}}
  const entry={attemptId:'CP-A',availability:'available',blob:new Oversized(['x'],{type:'audio/webm'}),snapshot:voice()};
  await reject(()=>buildPracticeAudioBundle(book(voice()),[entry],{now:NOW}),'PRACTICE_BUNDLE_LIMIT');assert.equal(read,false);
});

test('core dataset validation rejects extra audio payloads rather than hiding them in JSON',async()=>{
  const dataset=book(voice());dataset.audio='untrusted payload';await assert.rejects(()=>buildPracticeAudioBundle(dataset,[],{now:NOW}),error=>error.code==='INVALID_PRACTICE_DATA');
});

test('durable assets produce stored flags while missing bytes clear stored claims in export copy only',async()=>{
  const entry=asset('CP-A',new Uint8Array([1,2,3]),{durable:true});
  const available=await buildPracticeAudioBundle(book(voice()),[entry],{now:NOW}),files=readZIP(await available.blob.arrayBuffer());
  const stored=JSON.parse(files.find(file=>file.name==='dataset.json').bytes.toString());assert.equal(stored.actualAudioSaved,true);assert.equal(stored.attempts[0].audio.status,'stored');
  const missing=await buildPracticeAudioBundle(stored,[],{now:NOW}),missingFiles=readZIP(await missing.blob.arrayBuffer());
  const restored=JSON.parse(missingFiles.find(file=>file.name==='dataset.json').bytes.toString());
  assert.equal(restored.actualAudioSaved,false);assert.equal(restored.attempts[0].audio.status,'missing');assert.equal(restored.attempts[0].audio.sha256,entry.sha256);
  assert.equal(restored.attempts[0].audio.byteLength,entry.byteLength);assert.equal(missing.manifest.audio[0].availability,'missing');assert.equal(missing.manifest.actualAudioIncluded,false);
  assert.equal(stored.actualAudioSaved,true,'Caller dataset is not rewritten');
});

test('capture association allows annotation/import changes but rejects changed clinical/source context',async()=>{
  const before=voice(),entry=asset(),after={...before,notes:'사후 검토',referenceConfirmed:true,referenceText:'합성 연습 문장 종료',expectedAction:null,imported:true};
  // referenceText is immutable; put the same reference in the original capture.
  entry.snapshot.referenceText=after.referenceText;
  await buildPracticeAudioBundle(book(after),[entry],{now:NOW});
  for(const change of [{source:'manual_simulation'},{condition:{...after.condition,noiseKind:'siren'}},{timing:{startedAt:NOW-2000,endedAt:NOW}},{device:'Different microphone'}]){
    await reject(()=>buildPracticeAudioBundle(book({...after,...change}),[entry],{now:NOW}));
  }
});

test('indexed names keep case-sensitive attempt IDs distinct on Windows extraction',async()=>{
  const result=await buildPracticeAudioBundle(book(voice('CP-A'),voice('cp-a')),[asset('CP-A'),asset('cp-a')],{now:NOW});
  const names=readZIP(await result.blob.arrayBuffer()).filter(file=>file.name.startsWith('audio/')).map(file=>file.name.toLowerCase());
  assert.equal(new Set(names).size,2);assert.equal(result.manifest.audio[1].path,'audio/0002-cp-a.webm');
});

test('existing expected hash/size descriptors cannot be silently rebound to different audio',async()=>{
  const original=book(voice()),entry=asset();
  const metadata={status:'missing',contentType:entry.contentType,extension:entry.extension,byteLength:entry.byteLength,sha256:entry.sha256,startedAt:NOW-1000,endedAt:NOW,errorCode:'AUDIO_BYTES_MISSING'};
  for(const changed of [{sha256:'0'.repeat(64)},{byteLength:entry.byteLength+1},{contentType:'audio/ogg;codecs=opus',extension:'ogg'}]){
    const dataset=practiceData.attachPracticeAudio(original,'CP-A',{...metadata,...changed});
    await reject(()=>buildPracticeAudioBundle(dataset,[entry],{now:NOW}));
  }
});

test('native MP4 MIME keeps MP4 extension and no transcoding takes place',async()=>{
  const bytes=new Uint8Array([0,0,0,8,0x66,0x74,0x79,0x70]);
  const entry=asset('CP-A',bytes,{blob:new Blob([bytes],{type:'audio/mp4;codecs=mp4a.40.2'}),contentType:'audio/mp4;codecs=mp4a.40.2',extension:'mp4'});
  const result=await buildPracticeAudioBundle(book(voice()),[entry],{now:NOW}),row=result.manifest.audio[0];
  assert.equal(row.path,'audio/0001-CP-A.mp4');assert.equal(row.mime,entry.contentType);assert.equal(row.sha256,digest(bytes));
  assert.deepEqual(readZIP(await result.blob.arrayBuffer()).find(file=>file.name===row.path).bytes,Buffer.from(bytes));
});

test('available partial recording retains exact bytes, hash and capture warning regardless of persistence',async()=>{
  const bytes=new Uint8Array([0x1a,0x45,0xdf,0xa3,1]),entry=asset('CP-A',bytes),warning='AUDIO_RECORDER_ERROR';
  const original=practiceData.attachPracticeAudio(book(voice()),'CP-A',{status:'memory_only',contentType:entry.contentType,extension:entry.extension,byteLength:bytes.length,sha256:entry.sha256,startedAt:NOW-1000,endedAt:NOW,errorCode:warning});
  for(const durable of [false,true]){
    const result=await buildPracticeAudioBundle(original,[{...entry,durable}],{now:NOW}),files=readZIP(await result.blob.arrayBuffer()),row=result.manifest.audio[0];
    const dataset=JSON.parse(files.find(file=>file.name==='dataset.json').bytes.toString());
    assert.equal(row.availability,'available');assert.equal(row.included,true);assert.equal(row.errorCode,warning);assert.equal(row.sha256,digest(bytes));
    assert.deepEqual(files.find(file=>file.name===row.path).bytes,Buffer.from(bytes));
    assert.equal(dataset.attempts[0].audio.errorCode,warning);assert.equal(dataset.attempts[0].audio.status,durable?'stored':'memory_only');assert.equal(dataset.actualAudioSaved,durable);
    assert.match(files.find(file=>file.name==='README.txt').bytes.toString(),/Partial recordings are included/);
  }
});

test('partial capture warning must be a safe explicit error code rather than arbitrary text',async()=>{
  const entry=asset(),original=practiceData.attachPracticeAudio(book(voice()),'CP-A',{status:'memory_only',contentType:entry.contentType,extension:entry.extension,byteLength:entry.byteLength,sha256:entry.sha256,startedAt:NOW-1000,endedAt:NOW,errorCode:'unsafe warning with spaces'});
  await reject(()=>buildPracticeAudioBundle(original,[entry],{now:NOW}));
});

test('fixed timestamps make repeated exports byte-identical and no fetch is needed',async()=>{
  const previous=globalThis.fetch;let calls=0;globalThis.fetch=()=>{calls++;throw Error('Network forbidden');};
  try{const dataset=book(voice()),entry=asset(),a=await buildPracticeAudioBundle(dataset,[entry],{now:NOW}),b=await buildPracticeAudioBundle(dataset,[entry],{now:NOW});
    assert.deepEqual(Buffer.from(await a.blob.arrayBuffer()),Buffer.from(await b.blob.arrayBuffer()));assert.equal(calls,0);
  }finally{globalThis.fetch=previous;}
});
