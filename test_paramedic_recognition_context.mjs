import test from 'node:test';
import assert from 'node:assert/strict';
import {buildRecognitionContext,normalizeRecognitionAlternatives,reportVocabulary,RECOGNITION_CONTEXT_REVISION} from './paramedic-ui/recognition-context.mjs';
import {VoiceClient} from './paramedic-ui/voice-client.mjs';
import {QuickSession} from './paramedic-ui/quick-session.mjs';
import {RECOVERY_KEY,writeDraftRecovery,readDraftRecovery} from './paramedic-ui/draft-recovery.mjs';

const response=(data,status=200)=>({ok:status<400,status,json:async()=>data});
const session=version=>({id:'VS-CONTEXT',actor_id:'CONTEXT-CREW',version,state:'DRAFT',fields:{},field_metadata:{}});
const memory=()=>{const map=new Map();return {getItem:key=>map.get(key)??null,setItem:(key,value)=>map.set(key,value),removeItem:key=>map.delete(key)};};
const variants=['맥박 90회, 체온 37.2도','맥박 십구 회, 체온 삼십칠 점 이도'];
const top='맥박 19회, 체온 37.2도';

test('one-fragment substitutions preserve the original whole report and never create a Cartesian guess',()=>{
  const parts=['혈압 백이십에 팔십.','맥박 십구 회.','체온 삼십칠 점 이도.'];
  const result=buildRecognitionContext(parts,[[parts[0],'혈압 120에 80.'],[parts[1],'맥박 90회.'],[parts[2],'체온 37.2도.']]);
  assert.equal(result.status,'ready');assert.equal(result.text,parts.join(' '));
  assert.deepEqual(result.alternatives,['혈압 120에 80. 맥박 십구 회. 체온 삼십칠 점 이도.','혈압 백이십에 팔십. 맥박 90회. 체온 삼십칠 점 이도.','혈압 백이십에 팔십. 맥박 십구 회. 체온 37.2도.']);
  assert.ok(!result.alternatives.includes('혈압 120에 80. 맥박 90회. 체온 37.2도.'));
});
test('same hypotheses are deduplicated without changing number words, punctuation or top text',()=>{
  const result=buildRecognitionContext([top],[[top,variants[0],variants[0],variants[1]]]);
  assert.equal(result.text,top);assert.deepEqual(result.alternatives,variants);
});
test('a single provided hypothesis is not advertised as verified agreement',()=>{
  assert.deepEqual(buildRecognitionContext([top],[[top]]),{status:'single_hypothesis',text:top,alternatives:[]});
});
test('a lower hypothesis containing a screen control command forces manual review instead of selecting it',()=>{
  const result=buildRecognitionContext(['맥박 90회'],[['맥박 90회','병원 추천해줘']]);
  assert.equal(result.status,'review_required');assert.equal(result.reason,'control_alternative');
});
test('explicit commands preserve a single or identical hypothesis but block any differing numeric or control candidate',()=>{
  const top='혈압 110에 70으로 수정해';
  assert.equal(buildRecognitionContext([top],[[top]],{command:true}).status,'command_path');
  assert.equal(buildRecognitionContext([top],[[top,top]],{command:true}).status,'command_path');
  for(const other of ['혈압 140에 90으로 수정해','혈압 백십에 칠십으로 수정해','병원 추천해줘']){
    const result=buildRecognitionContext([top],[[top,other]],{command:true});
    assert.equal(result.status,'review_required');assert.equal(result.reason,'command_alternative');assert.equal(result.text,top);
  }
  assert.equal(buildRecognitionContext([top],[['different top']],{command:true}).status,'review_required');
});
test('unclipped UI control words retain legacy single text but never hide numerical disagreement in observe mode',()=>{
  const top='맥박 90회 녹음 종료';
  assert.equal(buildRecognitionContext([top],[[top]]).status,'command_path');
  assert.equal(buildRecognitionContext([top],[[top,'맥박 19회 녹음 종료']]).status,'review_required');
});
test('17 variants fail closed rather than silently omitting the last disagreement',()=>{
  const parts=Array.from({length:5},(_,i)=>'항목'+i);
  const result=buildRecognitionContext(parts,parts.map(part=>[part,...Array.from({length:4},(_,i)=>part+' 후보'+i)]));
  assert.equal(result.status,'review_required');assert.equal(result.text,parts.join(' '));assert.deepEqual(result.alternatives,[]);
});
test('too many hypotheses from one fragment, mismatched top or missing alignment fail closed',()=>{
  for(const choices of [[['다른 원문']],[],[[top,'a','b','c','d','e']]])assert.equal(buildRecognitionContext([top],choices).status,'review_required');
});
test('20000 total characters and 4000 per candidate are enforced before requests',()=>{
  const raw='a'.repeat(4000),valid=Array.from({length:4},(_,i)=>String(i)+'b'.repeat(3999));
  assert.equal(normalizeRecognitionAlternatives(raw,valid).length,4);
  assert.throws(()=>normalizeRecognitionAlternatives(raw,[...valid,'more']));
  assert.throws(()=>normalizeRecognitionAlternatives(raw,['b'.repeat(4001)]));
  assert.equal(buildRecognitionContext([raw],[[raw,...valid,'more']]).status,'review_required');
});
test('candidate validators reject empty, duplicate, coercible or accessor data without calling getters',()=>{
  for(const value of [[],[top],[''],['x','x'],[null],[1],Array(1)])assert.throws(()=>normalizeRecognitionAlternatives(top,value));
  let reads=0;const value=[];Object.defineProperty(value,'0',{get(){reads++;return 'x';},enumerable:true});
  assert.throws(()=>normalizeRecognitionAlternatives(top,value));assert.equal(reads,0);
});
test('medical report context supplies labels and units rather than normal values or previous patient values',()=>{
  const result=reportVocabulary(['혈압 120에 80','의식 명료','구토 없음','체온 37.2','나이','산소포화도']);
  assert.ok(['혈압','수축기','이완기','맥박','산소포화도','퍼센트','혈당'].every(word=>result.includes(word)));
  assert.ok(result.every(word=>!/[0-9]|정상|명료|없음/.test(word)));
  assert.equal(RECOGNITION_CONTEXT_REVISION,'voice-recognition-context-20261103-v1');
});

test('QuickSession passes detached context with original top text and never posts invalid candidates',async()=>{
  const calls=[];let id=0;const client=new VoiceClient({server:'http://localhost',actorId:'CONTEXT-CREW',makeId:()=>String(++id),fetchImpl:async(url,options)=>{calls.push(JSON.parse(options.body));return response({session:session(calls.length)});}});
  const flow=new QuickSession(client,()=> 'UT-CONTEXT');const source=[...variants];
  await flow.apply(top,'dictation',{recognitionAlternatives:source});
  source[0]='mutated';assert.equal(calls[1].text,top);assert.deepEqual(calls[1].recognition_alternatives,variants);
  const count=calls.length;await assert.rejects(flow.apply(top,'dictation',{recognitionAlternatives:[top]}));assert.equal(calls.length,count);
});

test('context survives create response loss, write response loss, reload and exact retry with no regenerated candidate',async()=>{
  const calls=[];let createLost=true,writeLost=true,current=null,seq=0;
  const cached=new Map();
  const fetchImpl=async(url,options)=>{
    const body=options.body?JSON.parse(options.body):null;calls.push({url,method:options.method,body});
    if(options.method==='GET')return response({session:current});
    const key=body.client_request_id;
    if(cached.has(key))return response({...cached.get(key),duplicate:true});
    if(url.endsWith('/api/voice/sessions')){current=session(1);const data={session:current};cached.set(key,data);if(createLost){createLost=false;throw Error('create response lost');}return response(data);}
    current=session(2);const data={session:current};cached.set(key,data);if(writeLost){writeLost=false;throw Error('write response lost');}return response(data);
  };
  const makeClient=()=>new VoiceClient({server:'http://localhost',actorId:'CONTEXT-CREW',makeId:()=> 'R-'+(++seq),fetchImpl});
  const c1=makeClient(),f1=new QuickSession(c1,()=> 'UT-CONTEXT');await assert.rejects(f1.apply(top,'dictation',{recognitionAlternatives:variants}));
  const first=f1.exportRecovery();assert.equal(first.job.phase,'create');assert.deepEqual(first.job.recognitionAlternatives,variants);
  const c2=makeClient(),f2=new QuickSession(c2);f2.restoreRecovery(first);await assert.rejects(f2.retry());
  const second=f2.exportRecovery();assert.equal(second.job.phase,'write');assert.deepEqual(second.pending.body.recognition_alternatives,variants);
  const c3=makeClient();c3.session=current;const f3=new QuickSession(c3);f3.restoreRecovery(second);await f3.retry();
  const posts=calls.filter(call=>call.method==='POST'&&call.url.endsWith('/transcripts'));
  assert.equal(posts.length,2);assert.deepEqual(posts[0].body,posts[1].body);assert.equal(posts[1].body.text,top);assert.deepEqual(posts[1].body.recognition_alternatives,variants);
  const changed=JSON.parse(JSON.stringify(second));changed.job.recognitionAlternatives[0]='different';assert.throws(()=>new QuickSession(c3).restoreRecovery(changed));
});

test('followup uses the original numeric hypothesis and exact alternatives across response-loss recovery',async()=>{
  let fail=true;const calls=[];let seq=0;const client=new VoiceClient({server:'http://localhost',actorId:'CONTEXT-CREW',makeId:()=> 'R-'+(++seq),fetchImpl:async(url,options)=>{calls.push(JSON.parse(options.body));if(fail){fail=false;throw Error('lost');}return response({session:session(2)});}});
  client.session=session(1);const flow=new QuickSession(client,()=> 'UT-CONTEXT');
  await assert.rejects(flow.answer({id:'pulse'},'19',{recognitionAlternatives:['90']}));
  const saved=flow.exportRecovery(),next=new QuickSession(new VoiceClient({server:'http://localhost',actorId:'CONTEXT-CREW',fetchImpl:client.fetchImpl}));next.client.session=session(1);next.restoreRecovery(saved);await next.retry();
  assert.deepEqual(calls[0],calls[1]);assert.equal(calls[0].text,'19');assert.deepEqual(calls[0].recognition_alternatives,['90']);assert.equal(calls[0].unavailable,undefined);
});

test('durable quick recovery retains the exact context body/job and rejects a mismatched copy',()=>{
  const base={serverOrigin:'http://localhost',actorId:'CONTEXT-CREW',sessionId:'VS-CONTEXT',version:1,recoveryText:'',manualText:'',mode:'auto',recordingMode:'toggle'};
  const job={text:top,mode:'auto',utteranceId:'UT-CONTEXT',phase:'write',recognitionAlternatives:variants};
  const request={path:'/api/voice/sessions/VS-CONTEXT/transcripts',method:'POST',body:{text:top,mode:'auto',utterance_id:'UT-CONTEXT',is_final:true,recognition_alternatives:variants,actor_id:'CONTEXT-CREW',client_request_id:'R-CONTEXT',expected_version:1}};
  const attempt={target:'transcript',mode:'auto',questionId:null,questionText:'',apiText:top,sourceText:top,excludedText:'',unappliedBefore:'',manual:false,copiedFrom:'',beforeVersion:1,beforeFlagged:[],trigger:null};
  const payload={...base,pending:{request,job,attempt}},store=memory();writeDraftRecovery(store,payload,{now:1000});
  assert.equal(readDraftRecovery(store,'http://localhost',{now:1000}).status,'valid');assert.deepEqual(readDraftRecovery(store,'http://localhost',{now:1000}).payload.pending.request,request);
  const before=store.getItem(RECOVERY_KEY);payload.pending.job.recognitionAlternatives=['other'];assert.throws(()=>writeDraftRecovery(store,payload,{now:1001}));assert.equal(store.getItem(RECOVERY_KEY),before);
});


test('matching screen controls permit only the actual prefix alternatives and preserve an identical suffix',()=>{
  const top='맥박 19회 녹음 종료 체온 37도',trigger={action:'stop',command:'녹음 종료',prefix:'맥박 19회',suffix:'체온 37도'};
  assert.deepEqual(buildRecognitionContext([top],[[top,'맥박 90회 녹음 종료 체온 37도']],{trigger}),{status:'ready',text:'맥박 19회',alternatives:['맥박 90회']});
  for(const candidate of ['맥박 90회 병원 추천해줘 체온 37도','맥박 90회 녹음 종료 체온 38도','맥박 90회'])assert.equal(buildRecognitionContext([top],[[top,candidate]],{trigger}).status,'review_required');
});
