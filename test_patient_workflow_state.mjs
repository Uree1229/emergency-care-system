import test from 'node:test';
import assert from 'node:assert/strict';
import {PatientWorkflow, recommendationView, validActor, workflowLink, resolveActor} from './patient-ui/workflow-state.mjs';

const context = (pid='A',server='http://127.0.0.1:18033',actor='crew-one') => ({pid,server,actor});
const patient = (id='A',version=1,state='WAITING') => ({id,version,state,inquiries:[]});
const event = (id, overrides={}) => ({event_id:id,patient_id:'A',at:10,type:'inquiry_question',summary:'질문',delivered_at:null,acknowledged_at:null,...overrides});
const page = (items,next=0,more=false,scope=context()) => ({items,next_after_id:next,has_more:more,scope:{patient_id:scope.pid,actor_id:scope.actor,role:'paramedic'}});
const ready = () => ({status:'READY',revision:4,created_at:10,expires_at:200,result:{status:'RECOMMENDED',hospital_id:'H1',inquiry_id:1},availability:{worker_state:'RUNNING',accepting_requests:true}});
const deferred = () => {let resolve;const promise=new Promise(r=>resolve=r);return {resolve,promise};};
function fixture(request,isVisible=()=>true) {const state=new PatientWorkflow({request,isVisible,now:()=>100});state.setContext(context());state.setPatient(patient());return state;}

test('freshness and worker availability are separate; stale payload never retains a plan',()=>{
  const r=ready();r.availability={worker_state:'DISABLED',accepting_requests:false};
  assert.equal(recommendationView(r,patient(),100).result.hospital_id,'H1');
  assert.equal(recommendationView(r,patient(),201).status,'STALE');
  assert.equal(recommendationView(r,patient(),201).result,null);
  assert.equal(recommendationView({...r,status:'STALE'},patient(),100).result,null);
  assert.equal(recommendationView(r,patient('A',1,'ARRIVED'),100).status,'NOT_APPLICABLE');
});

test('scope identity and URL retain current patient/actor/server without creating patients',()=>{
  for(const id of ['', ' bad', 'bad\nactor', 'crew\n', 'a'.repeat(81),'홍길동','crew one','_crew','@crew'])assert.equal(validActor(id),false);
  assert.equal(validActor('crew-one'),true);
  assert.equal(validActor('0Crew._:@-one'),true);assert.equal(validActor('a'.repeat(80)),true);
  const url=new URL(workflowLink('https://example.test/base','A','crew-one'));
  assert.equal(url.pathname,'/base/paramedic/');assert.equal(url.searchParams.get('pid'),'A');
  assert.equal(url.searchParams.get('actor_id'),'crew-one');assert.equal(url.searchParams.get('server'),'https://example.test/base');
});

test('invalid explicit and saved actor values are preserved without generating another identity',()=>{
  let generated=0;const generate=()=>{generated++;return 'crew-generated';};
  for(const value of ['홍길동','crew one','',' crew-one']) {
    assert.equal(resolveActor(value,'crew-saved',generate),value);
    assert.equal(resolveActor(null,value,generate),value);
    assert.equal(workflowLink('http://127.0.0.1:18048','A',value),null);
  }
  assert.equal(generated,0);
  assert.equal(resolveActor(null,'crew-saved',generate),'crew-saved');assert.equal(generated,0);
  assert.equal(resolveActor(null,null,generate),'crew-generated');assert.equal(generated,1);
});

test('invalid actor scopes cannot save, deliver, acknowledge or replan under a replacement identity',async()=>{
  const calls=[];const state=fixture(async(...args)=>{calls.push(args);});
  state.setContext(context('A','http://127.0.0.1:18048','홍길동'));state.setPatient(patient());
  await state.refresh({recover:true});await state.markVisible([1]);await state.acknowledge(1);await state.replan();
  assert.equal(state.context,null);assert.deepEqual(calls,[]);
});

test('late recommendation and input guidance from another server/patient are discarded',async()=>{
  const d=deferred();const state=fixture(()=>d.promise);const pending=state.refreshPlan();
  state.setContext(context('B','http://127.0.0.1:18034'));state.setPatient(patient('B'));
  d.resolve({...ready(),patient_id:'A',patient_version:1,input_review:{status:'NO_REVIEW_FLAGS'}});await pending;
  assert.equal(state.context.pid,'B');assert.equal(state.snapshot().plan.result,null);assert.equal(state.inputReview,null);
});

test('patient version change invalidates cached plan and pending old-version response',async()=>{
  const d=deferred();const state=fixture(()=>d.promise);state.recommendation=ready();const pending=state.refreshPlan();
  state.setPatient(patient('A',2));assert.equal(state.snapshot().plan.result,null);
  d.resolve({...ready(),patient_id:'A',patient_version:1,input_review:{status:'NO_REVIEW_FLAGS'}});await pending;
  assert.equal(state.snapshot().plan.result,null);
});

test('plan polling is GET only, errors discard old results, and disabled replan cannot POST',async()=>{
  const calls=[];let fail=false;const state=fixture(async(s,path,method='GET')=>{
    calls.push({path,method});if(fail)throw Error('offline');
    return path==='/recommendation'?ready():{patient_id:'A',patient_version:1,input_review:{status:'REVIEW_REQUIRED',items:[]}};
  });
  await state.refreshPlan();assert.equal(state.inputReview.status,'REVIEW_REQUIRED');assert.ok(calls.every(c=>c.method==='GET'));
  state.recommendation.availability.accepting_requests=false;assert.equal(await state.replan(),false);
  fail=true;await state.refreshPlan();assert.equal(state.snapshot().plan.result,null);assert.match(state.recommendationError,/offline/);
});

test('explicit replan handles 409 then refreshes; it does not confirm or replan during polling',async()=>{
  const calls=[];const state=fixture(async(s,path,method='GET')=>{
    calls.push({path,method});if(path==='/replan')throw Object.assign(Error('disabled'),{status:409});
    return path==='/recommendation'?ready():{patient_id:'A',patient_version:1,input_review:{status:'NO_REVIEW_FLAGS'}};
  });
  await state.refreshPlan();await state.replan();assert.match(state.recommendationError,/최신 상태/);
  assert.equal(calls.filter(c=>c.method==='POST').length,1);assert.equal(calls.find(c=>c.method==='POST').path,'/replan');
});

test('important stream reads every page, merges IDs, and never delivers on GET',async()=>{
  const calls=[];const state=fixture(async(s,path,method='GET')=>{
    calls.push({path,method});const after=new URLSearchParams(path.split('?')[1]).get('after_id');
    return after==='0'?page([event(1)],1,true):page([event(1),event(3)],3,false);
  });
  await state.refreshEvents();assert.equal(state.events.size,2);assert.equal(state.cursor,3);
  assert.equal(calls.length,2);assert.ok(calls.every(c=>c.method==='GET'));
});

test('unacknowledged recovery never mixes cursor and preserves receipts from older replies',async()=>{
  const calls=[];const state=fixture(async(s,path)=>{
    calls.push(path);return path.includes('unacknowledged_only')?page([event(1)],null,true):page([event(1,{delivered_at:11,acknowledged_at:12})],1,false);
  });
  await state.refreshEvents({recover:true});const recovery=calls.find(p=>p.includes('unacknowledged_only'));
  assert.equal(new URLSearchParams(recovery.split('?')[1]).has('after_id'),false);
  assert.equal(state.events.get(1).acknowledged_at,12);assert.equal(state.recoveryHasMore,true);
});

test('hidden rows cannot deliver or acknowledge; visible delivery precedes user acknowledgement',async()=>{
  let visible=false;const calls=[];const state=fixture(async(s,path,method='GET',body)=>{
    calls.push({s,path,method,body});return path.includes('?')?page([],1):{event:event(1,{delivered_at:11,acknowledged_at:path.endsWith('acknowledge')?12:null})};
  },()=>visible);
  state.events.set(1,event(1));await state.markVisible([1]);await state.acknowledge(1);assert.equal(calls.length,0);
  visible=true;assert.equal(await state.acknowledge(1),false);await state.markVisible([1]);
  assert.equal(calls[0].path,'/important-events/1/delivered');assert.equal(state.events.get(1).acknowledged_at,null);
  await state.acknowledge(1);assert.equal(calls[1].path,'/important-events/1/acknowledge');
  assert.equal(state.events.get(1).acknowledged_at,12);assert.ok(calls.filter(c=>c.method==='POST').every(c=>c.body.actor_id==='crew-one'));
  assert.ok(calls.every(c=>!c.path.includes('/confirm')&&!c.path.includes('change-destination')));
});

test('failed delivery is not retried in render loops; next explicit refresh permits retry',async()=>{
  let posts=0;const state=fixture(async(s,path,method='GET')=>{
    if(method==='POST'){posts++;throw Error('offline');}return page([event(1)],1);
  });state.events.set(1,event(1));await state.markVisible([1]);await state.markVisible([1]);assert.equal(posts,1);
  await state.refreshEvents();await state.markVisible([1]);assert.equal(posts,2);
});

test('acknowledgement failure remains visible after successful GET recovery until receipt is resolved',async()=>{
  let accepted=false;const state=fixture(async(s,path,method='GET')=>{
    if(method==='POST')throw Object.assign(Error('conflict'),{status:409});
    return page([event(1,{delivered_at:11,acknowledged_at:accepted?12:null})],1);
  });
  state.events.set(1,event(1,{delivered_at:11}));await state.acknowledge(1);
  assert.match(state.snapshot().eventError,/확인 기록 미완료/);assert.equal(state.events.get(1).acknowledged_at,null);
  await state.refreshEvents();assert.match(state.snapshot().eventError,/확인 기록 미완료/);
  accepted=true;await state.refreshEvents({recover:true});assert.equal(state.snapshot().eventError,'');
});

test('patient switch during delivery cannot publish old receipts or send remaining old rows',async()=>{
  const d=deferred();const calls=[];const state=fixture(async(s,path)=>{calls.push({s,path});return d.promise;});
  state.events.set(1,event(1));state.events.set(2,event(2));const pending=state.markVisible([1,2]);
  state.setContext(context('B'));d.resolve({event:event(1,{delivered_at:11})});await pending;
  assert.equal(state.events.size,0);assert.equal(calls.length,1);assert.equal(calls[0].s.pid,'A');
});

test('wrong scope and nonprogressing pagination are rejected without a cursor loop',async()=>{
  const wrong=fixture(async()=>page([event(1)],1,false,context('B')));await wrong.refreshEvents();assert.match(wrong.eventError,/범위/);
  let count=0;const state=fixture(async()=>{count++;return page([],0,true);});await state.refreshEvents();assert.equal(count,1);assert.match(state.eventError,/진행/);
});
