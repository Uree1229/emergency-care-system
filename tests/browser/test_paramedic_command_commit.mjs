import test from 'node:test';
import assert from 'node:assert/strict';
import {setImmediate as tick} from 'node:timers/promises';
import {CommandCommitGate,COMMAND_COMMIT_LIMITS} from '../../paramedic-ui/command-commit.mjs';

const ORIGIN=1793420000000;
function fixture(options={}){
  let now=ORIGIN,next=0;const timers=new Map(),saved=new Map(),commits=[],events=[],cleared=[];
  const setTimer=(callback,delay)=>{const id=++next;timers.set(id,{callback,due:now+delay,delay});saved.set(id,callback);return id;};
  const clearTimer=id=>{cleared.push(id);timers.delete(id);};
  const gate=new CommandCommitGate({now:()=>now,setTimer,clearTimer,onCommit:value=>commits.push(structuredClone(value)),onEvent:value=>events.push(structuredClone(value)),...options});
  return {gate,timers,saved,commits,events,cleared,setNow:value=>{now=value;},advance(ms){const target=now+ms;let turns=0;while(true){if(++turns>10000)throw Error('Synthetic timer loop');const first=[...timers].sort((a,b)=>a[1].due-b[1].due||a[0]-b[0])[0];if(!first||first[1].due>target)break;now=first[1].due;timers.delete(first[0]);first[1].callback();}now=target;},fireOld(id){saved.get(id)?.();}};
}
const types=gate=>gate.snapshot().events.map(event=>event.type);
const decisions=gate=>gate.snapshot().events.filter(event=>['commit','would_commit'].includes(event.type));
const finishEvents=gate=>gate.snapshot().events.filter(event=>event.type==='finish');
const invalid=fn=>assert.throws(fn,cause=>cause.code==='INVALID_COMMAND_COMMIT');

test('default policy is explicitly experimental metadata and an empty detached trace',()=>{
  const {gate}=fixture();assert.deepEqual(gate.snapshot(),{schema:1,policy:'deferred',waitMs:800,events:[],dropped:0,limited:false});assert.deepEqual(COMMAND_COMMIT_LIMITS,{rawText:16000,events:600,bytes:60000,text:1000,interimText:240});assert.equal(gate.rawText,'');assert.equal(gate.pending,null);assert.equal(gate.committed,false);assert.equal(gate.disposed,false);
});
for(const config of [{mode:'automatic'},{mode:null},{waitMs:-1},{waitMs:0.5},{waitMs:10001},{waitMs:'800'},{waitMs:NaN},{waitMs:Infinity},{now:1},{now:()=>NaN},{setTimer:null},{clearTimer:null},{onCommit:true},{onEvent:'callback'}])test(`invalid configuration fails before capture/control work ${Object.keys(config).join(',')}`,()=>invalid(()=>new CommandCommitGate(config)));

test('a candidate alone waits for the complete quiet window rather than stopping on its first final',()=>{
  const f=fixture();f.gate.final('종료');assert.equal(f.commits.length,0);assert.deepEqual(f.gate.pending,{trigger:{action:'stop',prefix:'',command:'종료',suffix:'',index:0,end:2},rawText:'종료',finalCount:1,deadlineAtMs:800,blocked:false});f.advance(799);assert.equal(f.commits.length,0);f.advance(1);assert.deepEqual(f.commits,[{trigger:{action:'stop',prefix:'',command:'종료',suffix:'',index:0,end:2},rawText:'종료',finalCount:1,atMs:800,reason:'quiet_window'}]);assert.equal(f.gate.pending,null);assert.equal(f.gate.committed,true);assert.equal(f.timers.size,0);assert.deepEqual(types(f.gate),['final','candidate','rearm','commit']);
});
test('every new final resets the deadline and commits the newest complete suffix only',()=>{
  const f=fixture();f.gate.final('종료');const old=[...f.saved.keys()][0];f.advance(600);f.gate.final('맥박 90');assert.equal(f.gate.pending.deadlineAtMs,1400);f.fireOld(old);assert.equal(f.commits.length,0);f.advance(799);assert.equal(f.commits.length,0);f.advance(1);assert.equal(f.commits.length,1);assert.equal(f.commits[0].rawText,'종료 맥박 90');assert.equal(f.commits[0].trigger.suffix,'맥박 90');assert.equal(f.commits[0].finalCount,2);
});
test('the specific split final 종료 then 시각과 cancels the candidate without executing or discarding either fragment',async()=>{
  const f=fixture();f.gate.final('종료');f.advance(300);f.gate.final('시각과 발생 시각은 다릅니다.');assert.equal(f.gate.pending,null);assert.equal(f.gate.rawText,'종료 시각과 발생 시각은 다릅니다.');f.advance(2000);assert.equal(f.commits.length,0);const result=await f.gate.end();assert.ok(result.events.some(event=>event.type==='cancel'&&event.reason==='context_changed'));assert.equal(decisions(f.gate).length,0);
});
for(const [first,tail]of [['종료','하지 마'],['중단','시각도 미확인'],['병원 추천해줘','라고 환자가 말했습니다'],['새 환자 시작','하지 마세요'],['이전 환자로 돌아가','라는 문구를 읽었습니다']])test(`later final context retracts an earlier candidate: ${first} / ${tail}`,async()=>{
  const f=fixture();f.gate.final(first);assert.ok(f.gate.pending);f.advance(200);f.gate.final(tail);f.advance(2000);await f.gate.end();assert.equal(f.commits.length,0);assert.equal(f.gate.rawText,first+' '+tail);assert.equal(decisions(f.gate).length,0);
});
for(const [action,command]of [['stop','녹음 종료'],['recommend','병원 추천해줘'],['new_patient','새 환자 시작'],['previous_patient','이전 환자로 돌아가'],['handoff_summary','인계 요약해줘']])test(`a genuine later ${action} can rearm after a non-command time phrase, with exact original spans`,()=>{
  const f=fixture(),prefix='종료 시각과 발생 시각은 다릅니다.';f.gate.final('종료');f.advance(100);f.gate.final('시각과 발생 시각은 다릅니다.');f.gate.final(command+' 맥박 92');f.advance(800);assert.equal(f.commits.length,1);const result=f.commits[0];assert.equal(result.trigger.action,action);assert.equal(result.trigger.prefix,prefix);assert.equal(result.trigger.command,command);assert.equal(result.trigger.suffix,'맥박 92');assert.equal(result.rawText.slice(result.trigger.index,result.trigger.end),command);assert.equal(result.rawText,prefix+' '+command+' 맥박 92');assert.equal(result.finalCount,3);
});
test('nonempty interim pauses candidate commit even when its meaning would preserve the same command',()=>{
  const f=fixture();f.gate.final('종료');f.advance(400);f.gate.interim('맥박 90');assert.equal(f.gate.pending.blocked,true);assert.equal(f.gate.pending.deadlineAtMs,null);f.advance(5000);assert.equal(f.commits.length,0);assert.equal(f.gate.rawText,'종료');
});
test('interim time context can remove a candidate, and clearing that interim cannot resurrect it',async()=>{
  const f=fixture();f.gate.final('종료');f.advance(400);f.gate.interim('시각과 발생 시각은 다릅니다');assert.equal(f.gate.pending,null);f.gate.interim('');f.advance(5000);assert.equal(f.commits.length,0);const result=await f.gate.end();assert.ok(result.events.some(event=>event.type==='cancel'&&event.reason==='interim_context'));assert.ok(result.events.some(event=>event.reason==='unresolved_interim'));assert.equal(f.gate.rawText,'종료');
});
test('interim negation then empty interim does not rearm an earlier recommendation',async()=>{
  const f=fixture();f.gate.final('병원 추천해줘');f.gate.interim('라고 보호자가 말함');f.gate.interim('');await f.gate.flush();f.advance(800);assert.equal(f.commits.length,0);assert.equal(f.gate.rawText,'병원 추천해줘');assert.equal(f.gate.committed,false);
});
test('a fresh nonempty final clears the interim block and starts a fresh quiet window',()=>{
  const f=fixture();f.gate.final('종료');f.advance(400);f.gate.interim('맥박 90');f.gate.interim('');f.advance(1000);assert.equal(f.commits.length,0);f.gate.final('맥박 90');assert.equal(f.gate.pending.blocked,false);f.advance(799);assert.equal(f.commits.length,0);f.advance(1);assert.equal(f.commits.length,1);assert.equal(f.commits[0].rawText,'종료 맥박 90');assert.equal(f.commits[0].finalCount,2);
});
test('empty final is not fresh context and cannot clear an unresolved interim',async()=>{
  const f=fixture();f.gate.final('종료');f.gate.interim('하지 마');f.gate.interim('');f.gate.final('');f.gate.final(' \t ');await f.gate.flush();assert.equal(f.commits.length,0);assert.equal(f.gate.rawText,'종료');assert.equal(f.gate.snapshot().events.filter(event=>event.reason==='empty_final').length,2);
});
test('ordinary empty interim updates after a final do not reset or pause its valid deadline',()=>{
  const f=fixture();f.gate.final('녹음 종료');f.advance(300);f.gate.interim('');f.gate.interim('  ');assert.equal(f.gate.pending.deadlineAtMs,800);f.advance(500);assert.equal(f.commits.length,1);assert.equal(f.commits[0].atMs,800);
});
test('interim alone cannot invent a final command, even when a caller ends or flushes capture',async()=>{
  const f=fixture();f.gate.interim('병원 추천해줘');assert.equal(f.gate.pending,null);assert.equal(f.gate.rawText,'');await f.gate.end();assert.equal(f.commits.length,0);assert.equal(f.gate.committed,false);
});
test('interim first then its actual final is included exactly once in command raw text',()=>{
  const f=fixture();f.gate.interim('병원 추천해줘');f.gate.final('병원 추천해줘');f.advance(800);assert.equal(f.commits.length,1);assert.equal(f.commits[0].rawText,'병원 추천해줘');assert.equal(f.commits[0].finalCount,1);
});

test('natural end waits only for the remainder of the existing deadline and returns the decided snapshot',async()=>{
  const f=fixture();f.gate.final('종료');f.advance(500);let resolved=false;const promise=f.gate.end().then(value=>{resolved=true;return value;});assert.equal(f.commits.length,0);assert.equal(resolved,false);f.advance(299);await Promise.resolve();assert.equal(resolved,false);f.advance(1);const result=await promise;assert.equal(f.commits.length,1);assert.equal(f.commits[0].atMs,800);assert.equal(result.events.at(-1).type,'commit');assert.equal(finishEvents(f.gate).length,1);assert.equal(finishEvents(f.gate)[0].reason,'natural');
});
test('repeated end is the same pending promise and neither restarts the timer nor logs duplicate finish boundaries',async()=>{
  const f=fixture();f.gate.final('종료');f.advance(600);const a=f.gate.end(),b=f.gate.end();assert.equal(a,b);assert.equal(f.gate.pending.deadlineAtMs,800);f.advance(200);await a;assert.equal(f.commits.length,1);assert.equal(finishEvents(f.gate).length,1);
});
test('a final received while natural end is waiting invalidates the old deadline and uses the newest text',async()=>{
  const f=fixture();f.gate.final('종료');f.advance(500);let ended=false;const promise=f.gate.end().then(()=>{ended=true;});f.gate.final('혈압 120');f.advance(799);await Promise.resolve();assert.equal(ended,false);f.advance(1);await promise;assert.equal(f.commits[0].atMs,1300);assert.equal(f.commits[0].trigger.suffix,'혈압 120');
});
test('new non-command context while end waits settles without any later automatic action',async()=>{
  const f=fixture();f.gate.final('종료');const promise=f.gate.end();f.gate.final('시각과 발생 시각이 다릅니다');const result=await promise;f.advance(5000);assert.equal(f.commits.length,0);assert.equal(result.events.at(-1).type,'cancel');f.gate.final('병원 추천해줘');f.advance(800);assert.equal(f.commits.length,0);
});
test('natural end with an unconfirmed interim cancels immediately rather than treating cleared interim as a final',async()=>{
  const f=fixture();f.gate.final('종료');f.gate.interim('맥박 90');f.gate.interim('');const result=await f.gate.end();assert.equal(f.timers.size,0);assert.equal(f.commits.length,0);assert.ok(result.events.some(event=>event.reason==='unresolved_interim'));assert.equal(f.gate.rawText,'종료');
});
test('an interim arriving during an end wait settles the promise safely instead of leaving a hanging paused end',async()=>{
  const f=fixture();f.gate.final('종료');const promise=f.gate.end();f.gate.interim('추가 말');await promise;assert.equal(f.timers.size,0);assert.equal(f.commits.length,0);
});
test('end without any candidate returns promptly, with one finish and no timer or action',async()=>{
  const f=fixture();f.gate.final('연습용 관찰입니다');const result=await f.gate.end();assert.equal(result.events.filter(event=>event.type==='finish').length,1);assert.equal(result.events.at(-1).reason,'no_candidate');assert.equal(f.timers.size,0);assert.equal(f.commits.length,0);
});
test('manual flush commits synchronously from the drained final text, then resolves a promise',async()=>{
  const f=fixture();f.gate.final('혈압 120.');f.gate.final('녹음 종료');const result=f.gate.flush('button');assert.ok(result instanceof Promise);assert.equal(f.commits.length,1);assert.equal(f.commits[0].reason,'button');assert.equal(f.commits[0].trigger.prefix,'혈압 120.');assert.equal(f.commits[0].finalCount,2);assert.equal(f.timers.size,0);assert.equal((await result).events.at(-1).type,'commit');
});
test('flush after late negative/time finals cannot use a stale candidate from before the recorder drain',async()=>{
  const f=fixture();f.gate.final('종료');f.gate.final('시각과 발생 시각을 구분합니다');await f.gate.flush();f.advance(800);assert.equal(f.commits.length,0);assert.equal(f.gate.rawText,'종료 시각과 발생 시각을 구분합니다');
});
test('flush with unresolved interim cancels and does not manufacture a final or execute the preceding control',async()=>{
  const f=fixture();f.gate.final('종료');f.gate.interim('하지 마');const result=await f.gate.flush();assert.equal(f.commits.length,0);assert.ok(result.events.some(event=>event.type==='cancel'&&event.reason==='unresolved_interim'));assert.equal(f.gate.rawText,'종료');
});
test('end then explicit flush shares the promise, commits once, and records one finish boundary',async()=>{
  const f=fixture();f.gate.final('종료');const ended=f.gate.end(),flushed=f.gate.flush('button');assert.equal(ended,flushed);await flushed;await f.gate.flush();await f.gate.end();f.advance(800);assert.equal(f.commits.length,1);assert.equal(finishEvents(f.gate).length,1);assert.equal(decisions(f.gate).length,1);
});

test('immediate mode keeps the former first-final behavior as an explicitly logged comparison policy',()=>{
  const f=fixture({mode:'immediate'});f.gate.final('종료');assert.equal(f.commits.length,1);assert.equal(f.commits[0].reason,'immediate');f.gate.final('시각과 발생 시각');assert.equal(f.commits.length,1);assert.equal(f.gate.rawText,'종료 시각과 발생 시각');assert.equal(f.timers.size,0);assert.equal(decisions(f.gate).length,1);
});
test('observe uses the deferred quiet rule but logs would_commit without calling any control callback',async()=>{
  const f=fixture({mode:'observe'});f.gate.final('종료');assert.equal(decisions(f.gate).length,0);f.advance(800);assert.equal(f.commits.length,0);assert.equal(f.gate.committed,true);assert.deepEqual(decisions(f.gate).map(event=>event.type),['would_commit']);await f.gate.end();assert.equal(finishEvents(f.gate).length,1);
});
test('observe flush also remains observation-only and latches one decision for the whole recording',async()=>{
  const f=fixture({mode:'observe'});f.gate.final('병원 추천해줘');await f.gate.flush();f.gate.final('새 환자 시작');f.advance(800);await f.gate.end();assert.equal(f.commits.length,0);assert.equal(decisions(f.gate).length,1);assert.equal(decisions(f.gate)[0].action,'recommend');assert.equal(f.gate.rawText,'병원 추천해줘 새 환자 시작');
});
test('all policies keep one decision even when late finals continue arriving during recorder stop',async()=>{
  for(const mode of ['deferred','immediate','observe']){const f=fixture({mode});f.gate.final('종료');f.advance(800);f.gate.final('병원 추천해줘');f.gate.final('새 환자 시작');await f.gate.flush();await f.gate.end();f.advance(800);assert.equal(decisions(f.gate).length,1,mode);assert.equal(f.commits.length,mode==='observe'?0:1);assert.equal(f.gate.rawText,'종료 병원 추천해줘 새 환자 시작');assert.equal(finishEvents(f.gate).length,1);}
});
test('cancel settles a pending end and invalidates even a callback that fires after clearTimer',async()=>{
  const f=fixture();f.gate.final('종료');const old=[...f.saved.keys()][0],waiting=f.gate.end();const cancelled=f.gate.cancel('patient_changed');await waiting;f.fireOld(old);f.advance(10000);assert.equal(f.commits.length,0);assert.equal(f.timers.size,0);assert.ok(cancelled.events.some(event=>event.type==='cancel'&&event.reason==='patient_changed'));assert.equal(finishEvents(f.gate).length,1);
});
test('dispose is terminal, settles pending work and prevents any later input from executing or rewriting gate raw text',async()=>{
  const f=fixture();f.gate.final('종료');const promise=f.gate.end(),first=f.gate.dispose();assert.equal(f.gate.disposed,true);f.gate.final('병원 추천해줘');f.gate.interim('그만');await f.gate.flush();await promise;f.advance(10000);assert.equal(f.commits.length,0);assert.equal(f.gate.rawText,'종료');assert.deepEqual(f.gate.dispose(),first);
});
test('repeated cancellation has no duplicate terminal events or side effects',()=>{
  const f=fixture();f.gate.final('종료');const a=f.gate.cancel('button_cancel'),b=f.gate.cancel('button_cancel');assert.deepEqual(a,b);assert.equal(finishEvents(f.gate).length,1);assert.equal(types(f.gate).filter(type=>type==='cancel').length,1);assert.equal(f.timers.size,0);
});

test('onCommit sees the already-latched state and logged decision during synchronous end/flush/cancel reentry',async()=>{
  let gate;const callbacks=[],f=fixture({onCommit:payload=>{callbacks.push(payload);assert.equal(gate.committed,true);assert.equal(decisions(gate).length,1);void gate.end();void gate.flush();gate.cancel('callback_cleanup');}});gate=f.gate;gate.final('종료');f.advance(800);await gate.end();assert.equal(callbacks.length,1);assert.equal(decisions(gate).length,1);assert.equal(finishEvents(gate).length,1);assert.equal(f.timers.size,0);
});
test('onEvent candidate cancellation prevents arming or automatic execution',()=>{
  const f=fixture();f.gate._onEvent=event=>{if(event.type==='candidate')f.gate.cancel('observer_cancel');};f.gate.final('종료');f.advance(10000);assert.equal(f.commits.length,0);assert.equal(f.timers.size,0);
});
test('a reentrant flush during the final event uses the newly reparsed context rather than stale earlier command',async()=>{
  const f=fixture();f.gate.final('종료');f.gate._onEvent=event=>{if(event.type==='final'&&event.text.startsWith('시각과'))void f.gate.flush();};f.gate.final('시각과 발생 시각');await f.gate.end();assert.equal(f.commits.length,0);assert.equal(f.gate.rawText,'종료 시각과 발생 시각');
});
test('a reentrant flush during nonempty interim cannot execute before the unresolved-interim block is applied',async()=>{
  const f=fixture();f.gate.final('종료');f.gate._onEvent=event=>{if(event.type==='interim'&&event.text)void f.gate.flush();};f.gate.interim('하지 마');await f.gate.end();assert.equal(f.commits.length,0);
});
test('a reentrant end from cancellation notification receives the complete terminal event pair',async()=>{
  const f=fixture();let promise;f.gate._onEvent=event=>{if(event.type==='cancel')promise=f.gate.end();};f.gate.final('종료');f.gate.cancel('page_exit');const result=await promise;assert.equal(result.events.filter(event=>event.type==='finish').length,1);assert.equal(result.events.filter(event=>event.type==='cancel').length,1);
});
test('callback exceptions and rejected promises cannot change control latching or strand completion',async()=>{
  const f=fixture({onCommit:()=>Promise.reject(Error('test callback')),onEvent:()=>{throw Error('test observer');}});f.gate.final('종료');f.advance(800);await f.gate.end();await tick();assert.equal(f.gate.committed,true);assert.equal(decisions(f.gate).length,1);assert.equal(f.timers.size,0);
});
test('snapshot, pending and callback objects are detached from internal control state',()=>{
  const f=fixture();f.gate.final('종료');const pending=f.gate.pending;pending.trigger.action='recommend';pending.rawText='changed';const snapshot=f.gate.snapshot();snapshot.events[0].text='changed';snapshot.events.push({type:'commit'});f.advance(800);assert.equal(f.commits[0].trigger.action,'stop');assert.equal(f.commits[0].rawText,'종료');assert.equal(f.gate.snapshot().events[0].text,'종료');
});
test('synchronous test timers cannot leave an orphan timer after they already committed',async()=>{
  let clears=0,calls=0;const gate=new CommandCommitGate({now:()=>ORIGIN,setTimer:callback=>{callback();return 'already-fired';},clearTimer:()=>clears++,onCommit:()=>calls++});gate.final('종료');await gate.end();assert.equal(calls,1);assert.ok(clears>=1);assert.equal(gate.pending,null);assert.equal(decisions(gate).length,1);
});
test('a failed scheduler closes honestly and never performs an immediate fallback action',async()=>{
  let count=0;const gate=new CommandCommitGate({now:()=>ORIGIN,setTimer(){throw Error('timer');},clearTimer(){},onCommit:()=>count++});gate.final('종료');const result=await gate.end();assert.equal(count,0);assert.ok(result.events.some(event=>event.reason==='timer_failed'));
});
test('a broken clearTimer cannot revive an invalidated earlier-generation callback',()=>{
  let old;const f=fixture({clearTimer(){throw Error('timer clear');}});f.gate.final('종료');old=[...f.saved.keys()][0];f.gate.cancel();f.fireOld(old);assert.equal(f.commits.length,0);
});

test('relative arrival timestamps remain nonnegative and monotonic when the injected wall clock goes backward',()=>{
  const f=fixture();f.gate.final('연습');f.setNow(ORIGIN+100);f.gate.interim('다음');f.setNow(ORIGIN-200);f.gate.interim('');f.setNow(ORIGIN+50);f.gate.final('녹음 종료');const values=f.gate.snapshot().events.map(event=>event.atMs);assert.equal(values[0],0);assert.ok(values.every((value,index)=>Number.isInteger(value)&&value>=0&&(!index||value>=values[index-1])));f.gate.cancel();
});
test('zero-delay remains a scheduled deferred policy and the configured upper delay is accepted without inventing a validated optimum',()=>{
  const f=fixture({waitMs:0});f.gate.final('종료');assert.equal(f.commits.length,0);f.advance(0);assert.equal(f.commits.length,1);const upper=fixture({waitMs:10000});upper.gate.final('종료');upper.advance(9999);assert.equal(upper.commits.length,0);upper.advance(1);assert.equal(upper.commits.length,1);
});
for(const method of ['final','interim'])for(const value of [null,undefined,1,{},[]])test(`${method} rejects non-text input without creating or running a control`,()=>{const f=fixture();invalid(()=>f.gate[method](value));assert.equal(f.gate.rawText,'');assert.equal(f.commits.length,0);assert.deepEqual(f.gate.snapshot().events,[]);});
for(const method of ['end','flush','cancel'])test(`${method} rejects unsafe/unbounded reason metadata`,()=>{const f=fixture();for(const reason of ['',null,'종료 확인','x'.repeat(81),'event<script>'])invalid(()=>f.gate[method](reason));assert.equal(f.commits.length,0);assert.deepEqual(f.gate.snapshot().events,[]);});

test('final/interim event text is explicitly shortened while full final raw stays intact',()=>{
  const f=fixture(),full='가'.repeat(1200);f.gate.final(full);f.gate.interim('나'.repeat(400));const [final,...rest]=f.gate.snapshot().events;assert.equal(final.text.length,1000);assert.equal(final.textTruncated,true);const interim=rest.find(event=>event.type==='interim');assert.equal(interim.text.length,240);assert.equal(interim.textTruncated,true);assert.equal(f.gate.rawText,full);f.gate.cancel();
});
test('trace text shortening does not cut a supplementary Unicode character in half',()=>{
  const f=fixture();f.gate.final('a'.repeat(999)+'😀tail');const event=f.gate.snapshot().events[0];assert.equal(event.text.length,999);assert.equal(event.textTruncated,true);assert.equal(f.gate.rawText,'a'.repeat(999)+'😀tail');f.gate.cancel();
});
test('interim trace reserve keeps a later final, decision and finish available while honestly counting dropped events',async()=>{
  const f=fixture();f.gate.final('연습');for(let index=0;index<1400;index++)f.gate.interim('임시'.repeat(120));f.gate.final('녹음 종료');f.advance(800);await f.gate.end();const result=f.gate.snapshot();assert.ok(result.dropped>0);assert.ok(result.events.length<=600);assert.ok(new TextEncoder().encode(JSON.stringify(result)).byteLength<=60000);assert.ok(result.events.some(event=>event.type==='commit'));assert.ok(result.events.some(event=>event.type==='finish'));assert.equal(f.commits.length,1);
});
test('event-count capacity drops honestly without changing final text or preventing a real decision',async()=>{
  const f=fixture();for(let index=0;index<800;index++)f.gate.final('가');f.gate.final('녹음 종료');f.advance(800);await f.gate.end();const result=f.gate.snapshot();assert.equal(result.events.length,600);assert.ok(result.dropped>0);assert.equal(f.commits.length,1);assert.equal(f.gate.rawText,Array(800).fill('가').join(' ')+' 녹음 종료');
});
test('exact 16k final aggregate is preserved, while overflow explicitly limits and permanently disables automatic action',async()=>{
  const f=fixture(),first='가'.repeat(16000);f.gate.final(first);assert.equal(f.gate.rawText,first);assert.equal(f.gate.snapshot().limited,false);f.gate.final('종료');const result=await f.gate.end();assert.equal(result.limited,true);assert.equal(f.gate.rawText,first);assert.ok(result.events.some(event=>event.type==='cancel'&&event.reason==='text_limit'));f.advance(10000);assert.equal(f.commits.length,0);assert.equal(first.length,16000);
});
test('an oversized single final is bounded and cancelled rather than throwing away the parent-owned original',async()=>{
  const f=fixture(),original='종료 '+'가'.repeat(20000);f.gate.final(original);await f.gate.flush();assert.equal(f.gate.snapshot().limited,true);assert.equal(f.gate.rawText,original.slice(0,16000));assert.equal(original.length,20003);assert.equal(f.commits.length,0);
});
test('an oversized unresolved interim is also bounded and cannot execute an earlier final control',async()=>{
  const f=fixture();f.gate.final('종료');f.gate.interim('가'.repeat(20000));await f.gate.end();assert.equal(f.gate.snapshot().limited,true);assert.equal(f.commits.length,0);assert.equal(f.gate.rawText,'종료');assert.equal(f.timers.size,0);
});
test('gate-only use needs no DOM, recorder, API, patient storage or clinical write capability',async()=>{
  const f=fixture({mode:'observe'});f.gate.final('종료');f.gate.final('시각과 발생 시각');const result=await f.gate.end();assert.equal(f.commits.length,0);assert.deepEqual(Object.keys(result),['schema','policy','waitMs','events','dropped','limited']);assert.equal(f.gate.rawText,'종료 시각과 발생 시각');
});
test('parent capture-boundary marks are distinct from decisions and remain available during latched stop drain',()=>{
  const f=fixture();f.gate.final('종료');f.advance(800);f.gate.mark('stop_requested');f.advance(20);f.gate.mark('recognizer_end');f.gate.mark('stop_drained');const marks=f.gate.snapshot().events.filter(event=>event.type==='capture');assert.deepEqual(marks,[{type:'capture',atMs:800,reason:'stop_requested'},{type:'capture',atMs:820,reason:'recognizer_end'},{type:'capture',atMs:820,reason:'stop_drained'}]);assert.equal(f.commits.length,1);assert.equal(decisions(f.gate).length,1);
});
test('late capture-boundary marks after cancellation or disposal cannot mutate a frozen attempt trace',()=>{
  for(const method of ['cancel','dispose']){const f=fixture();f.gate.final('종료');f.gate[method]();const before=f.gate.snapshot();f.gate.mark('recognizer_end');assert.deepEqual(f.gate.snapshot(),before);assert.equal(f.commits.length,0);}const f=fixture();invalid(()=>f.gate.mark('real hardware success'));assert.deepEqual(f.gate.snapshot().events,[]);
});
test('capture marks use the same honest trace-cap drop accounting without pretending a stop succeeded',()=>{
  const f=fixture();for(let index=0;index<800;index++)f.gate.final('가');const before=f.gate.snapshot(),after=f.gate.mark('stop_requested');assert.equal(after.events.length,before.events.length);assert.equal(after.dropped,before.dropped+1);assert.equal(f.commits.length,0);assert.equal(f.gate.rawText,Array(800).fill('가').join(' '));f.gate.cancel();
});
