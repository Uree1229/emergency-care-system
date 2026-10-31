import test from 'node:test';
import assert from 'node:assert/strict';
import {renderWorkflow, observeVisibleEvents} from './patient-ui/workflow-view.mjs';
import {recommendationView} from './patient-ui/workflow-state.mjs';

function documentFixture() {
  const ids=['recommendation-state','planner-status','replan-button','recommendation-error','recommendation-result',
    'input-review-state','input-review-items','input-review-error','patient-voice-link','patient-change-link',
    'important-event-status','important-event-error','important-events','workflow-actor-error','workflow-actor','workflow-actor-save'];
  const nodes=Object.fromEntries(ids.map(id=>[id,{textContent:'',innerHTML:'',dataset:{},hidden:false,disabled:false,querySelector:()=>null}]));
  return {nodes,doc:{getElementById:id=>nodes[id],activeElement:null}};
}
function state(data,patient={id:'A',state:'WAITING'}) {
  return {context:{server:'http://127.0.0.1:18048',pid:'A',actor:'crew-one'},patient,plan:recommendationView(data,patient,100),
    recommendationError:'',reviewError:'',inputReview:{status:'NO_REVIEW_FLAGS',items:[]},events:[],eventError:'',
    acknowledging:new Set(),replanning:false,recoveryHasMore:false};
}

test('view clears old recommendation content on STALE and keeps disabled worker status separate',()=>{
  const {nodes,doc}=documentFixture();
  const data={status:'READY',created_at:50,expires_at:200,revision:3,result:{status:'RECOMMENDED',hospital_id:'H1',inquiry_id:7},
    availability:{worker_state:'DISABLED',accepting_requests:false,reason_code:'PLANNER_DISABLED'}};
  renderWorkflow(doc,state(data));assert.match(nodes['recommendation-result'].innerHTML,/H1/);assert.equal(nodes['replan-button'].disabled,true);
  renderWorkflow(doc,state({...data,status:'STALE'}));assert.doesNotMatch(nodes['recommendation-result'].innerHTML,/H1|#7/);
  assert.match(nodes['planner-status'].textContent,/비활성/);
  assert.match(nodes['input-review-state'].textContent,/현재 정책의 재확인 표시 없음/);
});

test('invalid actor displays explicit correction instructions and disables scoped links and apply',()=>{
  const {nodes,doc}=documentFixture();const s=state(null);s.context=null;s.actorInvalid=true;s.actorError='한글과 공백은 사용할 수 없습니다.';
  renderWorkflow(doc,s);assert.match(nodes['workflow-actor-error'].textContent,/한글과 공백/);
  assert.equal(nodes['workflow-actor-save'].disabled,true);assert.equal(nodes['patient-voice-link'].hidden,true);
  assert.equal(nodes['patient-change-link'].hidden,true);assert.equal(nodes['replan-button'].disabled,true);
});

test('view escapes server-controlled text and uses scoped same-patient links',()=>{
  const {nodes,doc}=documentFixture();const s=state({status:'READY',expires_at:200,result:{status:'RECOMMENDED',hospital_id:'<img onerror=x>',warnings:['<script>x</script>']}});
  s.patient={id:'A',state:'IN_TRANSIT',destination_hospital_id:'H1'};
  s.inputReview={status:'REVIEW_REQUIRED',items:[{label:'<img>',reason:'<script>bad</script>'}]};
  renderWorkflow(doc,s);
  assert.doesNotMatch(nodes['recommendation-result'].innerHTML,/<img|<script/);
  assert.doesNotMatch(nodes['input-review-items'].innerHTML,/<img|<script/);
  const link=new URL(nodes['patient-change-link'].href);assert.equal(link.searchParams.get('pid'),'A');
  assert.equal(link.searchParams.get('actor_id'),'crew-one');assert.equal(link.searchParams.get('hid'),'H1');
  assert.equal(nodes['patient-change-link'].hidden,false);
});

test('delivered rows are limited to actual viewport and scroll-box exposure after two paints',()=>{
  const previous={raf:globalThis.requestAnimationFrame,height:globalThis.innerHeight,width:globalThis.innerWidth};
  const callbacks=[];globalThis.requestAnimationFrame=fn=>callbacks.push(fn);globalThis.innerHeight=500;globalThis.innerWidth=500;
  try {
    const token={},calls=[];
    const node=(id,top)=>({dataset:{workflowEvent:String(id)},isConnected:true,getBoundingClientRect:()=>({top,bottom:top+30,left:20,right:100,width:80,height:30})});
    const rows=[node(1,30),node(2,150),node(3,600)];
    const doc={hidden:false,getElementById:()=>({getBoundingClientRect:()=>({top:20,bottom:100,left:10,right:200})}),querySelectorAll:()=>rows};
    const controller={context:token,current:t=>t===token,markVisible:(ids,t)=>calls.push({ids,t})};
    const observer=observeVisibleEvents(doc,controller);observer.update();assert.equal(calls.length,0);
    callbacks.shift()();assert.equal(calls.length,0);callbacks.shift()();assert.deepEqual(calls[0].ids,[1]);
    observer.scan();doc.hidden=true;while(callbacks.length)callbacks.shift()();assert.equal(calls.length,1);
  } finally {globalThis.requestAnimationFrame=previous.raf;globalThis.innerHeight=previous.height;globalThis.innerWidth=previous.width;}
});
