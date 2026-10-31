import test from 'node:test';
import assert from 'node:assert/strict';
import {VoiceClient} from './paramedic-ui/voice-client.mjs';
import {MainFollowup,patientNavigation} from './paramedic-ui/main-followup.mjs';
import {MainTreatmentReview} from './paramedic-ui/main-treatment-review.mjs';

const patient={id:'P-SYNTHETIC',state:'WAITING',version:7};
const question={id:'pulse',question:'맥박은 얼마인가요?',fields:['pulse'],askable:true};
function fixture({loss=false,rejected=false,suggestionAvailable=true}={}){
  let n=0,lost=false;
  const calls=[],session={id:'VS-SYNTHETIC',actor_id:'CREW-SYNTHETIC',version:3,state:'DRAFT',confirmed:true,patient_id:patient.id,fields:{pulse:90},followup:{items:[question],next_question:question,unanswered_count:1}};
  const proposal=()=>({token:'a'.repeat(64),session_id:session.id,session_version:session.version,patient_version:patient.version,selection:{mode:suggestionAvailable?'suggested':'keep'},current:{resource_demands:{ed_bed:1,special_team:1}},proposed:{resource_demands:{ed_bed:1}},reductions:[{field:'resource_demands',reduced:{special_team:{previous:1,current:0}}}],can_apply:true,blockers:[]});
  const committed=new Map();
  const client=new VoiceClient({server:'http://127.0.0.1:18999',actorId:session.actor_id,makeId:()=>`ID-${++n}`,fetchImpl:async(url,opt)=>{
    const path=new URL(url).pathname,body=opt.body?JSON.parse(opt.body):null;calls.push({path,method:opt.method,body});let data;
    if(opt.method==='GET'&&path.endsWith('/treatment-review'))data={review:proposal()};
    else if(path.endsWith('/followup/answers')){
      if(committed.has(body.client_request_id))data={...committed.get(body.client_request_id),duplicate:true};
      else {session.version++;session.latest_changes=[];data={session:structuredClone(session),answer:{accepted:!rejected,unavailable:body.unavailable===true}};committed.set(body.client_request_id,data);if(loss&&!lost){lost=true;throw Error('synthetic response lost');}}
    }else if(opt.method==='GET')data={session:structuredClone(session)};
    else if(path.endsWith('/preview'))data={review:{...proposal(),selection:body.selection}};
    else if(path.endsWith('/apply')){session.state='SUBMITTED';data={session:structuredClone(session),patient:{...patient,version:8},reevaluation:{status:'QUEUED'}};}
    else throw Error('unexpected request');
    return {ok:true,status:200,json:async()=>data};
  }});client.session=structuredClone(session);return {client,calls,session,proposal};
}

test('followup shares exact profile/versioned answer endpoint and unmeasured does not manufacture zero',async()=>{
  const f=fixture(),flow=new MainFollowup(f.client);flow.observe();await flow.answer(flow.current(),'미측정');
  const c=f.calls[0];assert.match(c.path,/\/followup\/answers$/);assert.equal(c.body.profile,'voice_extended_v1');assert.equal(c.body.expected_version,3);assert.equal(c.body.unavailable,true);assert.equal(c.body.text,'미측정');assert.equal(f.client.session.fields.pulse,90);
  assert.equal(f.calls.some(c=>/submit|apply|confirm/.test(c.path)),false);
});
test('unparsed answer is preserved as deferred rather than repeated or falsely complete',async()=>{
  const f=fixture({rejected:true}),flow=new MainFollowup(f.client);flow.observe();await flow.answer(question,'이해되지 않는 합성 답변');
  assert.equal(flow.current(),null);assert.equal(flow.deferred.get('pulse'),'이해되지 않는 합성 답변');flow.retryDeferred();assert.equal(flow.current().id,'pulse');
});
test('local skip changes no clinical state and stale question sends nothing',async()=>{
  const f=fixture(),flow=new MainFollowup(f.client);flow.observe();flow.defer();assert.equal(f.calls.length,0);assert.equal(f.client.session.fields.pulse,90);
  await assert.rejects(flow.answer(question,'80'),/변경/);assert.equal(f.calls.length,0);
});
test('lost answer retains one original request and exact retry through shared QuickSession',async()=>{
  const f=fixture({loss:true}),flow=new MainFollowup(f.client);flow.observe();await assert.rejects(flow.answer(question,'80'));
  assert.ok(flow.flow.job);assert.ok(f.client.pending);await flow.flow.retry();const writes=f.calls.filter(c=>c.method==='POST');assert.equal(writes.length,2);assert.deepEqual(writes[0].body,writes[1].body);assert.equal(flow.flow.job,null);assert.equal(f.client.pending,null);
});
test('latest change evidence is kept only across newer versions of the same draft',()=>{
  const f=fixture(),flow=new MainFollowup(f.client);flow.observe();f.client.session={...f.client.session,version:4,latest_changes:[{field:'pulse',previous:90,current:110,direction:'increased'}]};flow.observe();assert.equal(flow.changes[0].previous,90);flow.observe();assert.equal(flow.changes[0].current,110);f.client.session={...f.client.session,id:'VS-OTHER'};flow.observe();assert.equal(flow.changes.length,0);assert.equal(flow.deferred.size,0);
});
test('treatment read/preview preserves binding and requires two explicit confirmations plus reduction',async()=>{
  const f=fixture(),model=new MainTreatmentReview(f.client);await model.load(patient);assert.equal(f.calls[0].method,'GET');await model.preview(patient,'keep');assert.deepEqual(model.review.selection,{mode:'keep'});assert.equal(f.client.session.state,'DRAFT');
  await assert.rejects(model.apply(patient),/명시/);await assert.rejects(model.apply(patient,{confirmed:true}),/감소/);assert.equal(f.calls.filter(c=>c.path.endsWith('/apply')).length,0);
  const r=model.review;await model.apply(patient,{confirmed:true,allowReduction:true,reason:'합성 검토'});const body=f.calls.at(-1).body;assert.equal(body.review_token,r.token);assert.equal(body.expected_patient_version,7);assert.equal(body.expected_version,3);assert.deepEqual(body.selection,{mode:'keep'});assert.equal(body.allow_reduction,true);assert.equal(model.review,null);
});
test('stale draft or patient versions prevent applying an old proposal',async()=>{
  for(const change of ['patient','session']){const f=fixture(),model=new MainTreatmentReview(f.client);await model.load(patient);const p={...patient};if(change==='patient')p.version++;else f.client.session.version++;await assert.rejects(model.apply(p,{confirmed:true,allowReduction:true}),/버전/);assert.equal(f.calls.length,1);}
});
test('confirmed/in-transit/other-patient scopes cannot use WAITING treatment apply',async()=>{
  for(const override of [{state:'CONFIRMED'},{state:'IN_TRANSIT'},{id:'OTHER'}]){const f=fixture(),model=new MainTreatmentReview(f.client);await assert.rejects(model.load({...patient,...override}),/같은 환자/);assert.equal(f.calls.length,0);}
});
test('same-patient navigation stays at the selected API origin and carries no clinical text/draft',()=>{
  const u=new URL(patientNavigation('http://127.0.0.1:18046','P-1','CREW-1'));assert.equal(u.origin,'http://127.0.0.1:18046');assert.equal(u.pathname,'/patient/');assert.deepEqual([...u.searchParams],[['pid','P-1'],['actor_id','CREW-1']]);
  assert.throws(()=>patientNavigation('https://host.invalid/path','P-1','CREW-1'));assert.throws(()=>patientNavigation('https://host.invalid','P/1','CREW-1'));assert.throws(()=>patientNavigation('https://host.invalid','P-1','CREW-1','/other'));
});
test('explicit Keep survives GET defaulting to suggested and the applied selection stays Keep',async()=>{
  const f=fixture(),model=new MainTreatmentReview(f.client);await model.load(patient,'keep');assert.equal(f.calls[0].method,'GET');assert.match(f.calls[1].path,/\/preview$/);assert.deepEqual(f.calls[1].body.selection,{mode:'keep'});assert.deepEqual(model.review.selection,{mode:'keep'});
  await model.apply(patient,{confirmed:true,allowReduction:true});assert.deepEqual(f.calls.at(-1).body.selection,{mode:'keep'});
});
test('unavailable initial suggestion accepts server Keep without trying an invalid suggested preview',async()=>{
  const f=fixture({suggestionAvailable:false}),model=new MainTreatmentReview(f.client);await model.load(patient,'suggested');assert.equal(model.review.selection.mode,'keep');assert.match(model.notice,/제안이 없어/);assert.equal(f.calls.length,1);assert.equal(f.calls[0].method,'GET');
});
