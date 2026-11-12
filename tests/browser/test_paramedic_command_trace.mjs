import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {normalizeCommandTrace} from '../../paramedic-ui/command-trace.mjs';
import {createPracticeAttempt,emptyPracticeDataset,appendPracticeAttempt,annotatePracticeAttempt,exportPracticeJSON,importPracticeJSON,exportPracticeCSV,readPracticeDataset,writePracticeDataset} from '../../paramedic-ui/command-practice-data.mjs';
import {buildPracticeAudioBundle} from '../../paramedic-ui/practice-audio-bundle.mjs';

const NOW=1793400000000,copy=value=>JSON.parse(JSON.stringify(value));
const trace=()=>({schema:1,policy:'deferred',waitMs:800,events:[
  {type:'final',atMs:100,text:'종료',textTruncated:false},
  {type:'candidate',atMs:100,action:'stop',command:'종료',index:0,end:2,reason:'final'},
  {type:'interim',atMs:300,text:'시각과 발생 시각은 다릅니다',textTruncated:false},
  {type:'cancel',atMs:300,reason:'interim_context'},
  {type:'final',atMs:500,text:'시각과 발생 시각은 다릅니다',textTruncated:false},
  {type:'capture',atMs:1200,reason:'recognizer_end'},
  {type:'finish',atMs:1200,reason:'natural'},
],dropped:0,limited:false});
function attempt(commandTrace=trace()){
  const text='종료 시각과 발생 시각은 다릅니다';
  return createPracticeAttempt({id:'TRACE-01',source:'human_voice',trialNo:1,rawText:text,fragments:['종료','시각과 발생 시각은 다릅니다'],status:'ok',expectedAction:null,referenceText:text,referenceConfirmed:true,timing:{startedAt:NOW-2000,endedAt:NOW},commandTrace},{now:NOW});
}
const book=value=>appendPracticeAttempt(emptyPracticeDataset(),value);

test('trace adds v2 observations without relabelling the final text or claiming audio',()=>{
  const source=trace(),value=attempt(source),dataset=book(value);source.events[0].text='edited';
  assert.equal(dataset.schema,2);assert.equal(value.commandTrace.events[0].text,'종료');assert.equal(value.parserResult,null);
  assert.equal(value.actualAudioSaved,false);assert.equal(dataset.actualAudioSaved,false);assert.equal(Object.hasOwn(value,'audio'),false);
});
test('JSON and storage readback preserve interim/decision/request timestamps separately from final fragments',()=>{
  const original=book(attempt()),raw=exportPracticeJSON(original),imported=importPracticeJSON(raw);
  assert.deepEqual(imported.attempts[0].commandTrace,original.attempts[0].commandTrace);assert.deepEqual(imported.attempts[0].fragments,['종료','시각과 발생 시각은 다릅니다']);
  const values=new Map(),storage={getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,value)};
  writePracticeDataset(storage,original,{expectedRaw:null});assert.deepEqual(readPracticeDataset(storage).dataset,original);
});
test('annotation changes only the independent answer and leaves the frozen event observation intact',()=>{
  const original=book(attempt()),updated=annotatePracticeAttempt(original,'TRACE-01',{expectedAction:'stop',referenceConfirmed:false,notes:'다르게 말함'});
  assert.deepEqual(updated.attempts[0].commandTrace,original.attempts[0].commandTrace);
  assert.throws(()=>annotatePracticeAttempt(original,'TRACE-01',{commandTrace:trace()}));
});
test('CSV includes trace summaries only when present and retains legacy columns',()=>{
  const csv=exportPracticeCSV(book(attempt()));assert.match(csv,/"commandPolicy","commandWaitMs","commandEventCount","commandDropped","commandTraceLimited"/);
  assert.match(csv,/"deferred","800","7","0","false"\r\n$/);
  const legacy=attempt();delete legacy.commandTrace;const old=book(legacy);assert.equal(old.schema,1);assert.doesNotMatch(exportPracticeCSV(old),/commandPolicy/);
});
test('legacy schema1 cannot smuggle a new trace but remains unchanged without one',()=>{
  const legacy=attempt();delete legacy.commandTrace;const old=book(legacy),raw=exportPracticeJSON(old);
  assert.equal(exportPracticeJSON(importPracticeJSON(raw)).includes('commandTrace'),false);
  assert.throws(()=>exportPracticeJSON({...old,attempts:[attempt()]}));
});
test('an included recording retains its exact trial trace in dataset JSON and source snapshot',async()=>{
  const value=attempt(),dataset=book(value),blob=new Blob(['synthetic-byte-association-only'],{type:'audio/webm'});
  const sha256=createHash('sha256').update(Buffer.from(await blob.arrayBuffer())).digest('hex');
  const asset={attemptId:value.id,availability:'available',blob,sha256,snapshot:copy(value),durable:false};
  const exported=await buildPracticeAudioBundle(dataset,[asset],{now:NOW});assert.equal(exported.manifest.audio[0].included,true);
  asset.snapshot.commandTrace.events[0].atMs=99;await assert.rejects(buildPracticeAudioBundle(dataset,[asset],{now:NOW}),/일치/);
});
test('a bounded omitted/truncated trace remains explicitly incomplete',()=>{
  const value=trace();value.dropped=17;value.events[2].textTruncated=true;value.limited=true;assert.deepEqual(normalizeCommandTrace(value),value);
});
for(const [name,mutate]of [
  ['unknown metadata',value=>value.cloudToken='forbidden'],
  ['unknown event',value=>value.events[0].type='execute_patient'],
  ['negative time',value=>value.events[0].atMs=-1],
  ['time reversal',value=>value.events[2].atMs=1],
  ['fractional time',value=>value.events[0].atMs=0.5],
  ['invalid action',value=>value.events[1].action='diagnose'],
  ['half span',value=>delete value.events[1].end],
  ['reversed span',value=>value.events[1].end=-1],
  ['oversized span',value=>value.events[1].end=16001],
  ['oversized event text',value=>value.events[0].text='가'.repeat(1001)],
  ['reason with payload',value=>value.events[3].reason='raw private text'],
  ['negative omitted count',value=>value.dropped=-1],
  ['fractional wait',value=>value.waitMs=800.5],
  ['negative wait',value=>value.waitMs=-1],
  ['oversized wait',value=>value.waitMs=10001],
  ['unknown policy',value=>value.policy='medical_autopilot'],
  ['non-boolean limited',value=>value.limited='false'],
  ['too many events',value=>value.events=Array.from({length:601},()=>({type:'final',atMs:0,text:'가'}))],
  ['oversized JSON trace',value=>value.events=Array.from({length:60},()=>({type:'final',atMs:0,text:'가'.repeat(1000)}))],
])test(`malformed command timing data rejects atomically: ${name}`,()=>{
  const value=trace();mutate(value);assert.throws(()=>normalizeCommandTrace(value),error=>error.code==='INVALID_COMMAND_TRACE');
});
test('observation mode can record one hypothetical decision but cannot claim a real commit',()=>{
  const value=trace();value.policy='observe';value.events.push({type:'would_commit',atMs:1300,action:'stop'});assert.equal(normalizeCommandTrace(value).events.at(-1).type,'would_commit');
  value.events.at(-1).type='commit';assert.throws(()=>normalizeCommandTrace(value));
});
test('two decisions in one trial are rejected rather than counted twice',()=>{
  const value=trace();value.events.push({type:'commit',atMs:1300,action:'stop'},{type:'commit',atMs:1400,action:'recommend'});assert.throws(()=>normalizeCommandTrace(value));
});
test('trace getters and sparse arrays are rejected without reading supplied accessors',()=>{
  let reads=0;const value=trace();Object.defineProperty(value,'events',{enumerable:true,get(){reads++;return [];}});assert.throws(()=>normalizeCommandTrace(value));assert.equal(reads,0);
  const sparse=trace();delete sparse.events[1];assert.throws(()=>normalizeCommandTrace(sparse));
});
