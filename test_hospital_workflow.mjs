import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {HospitalWorkflowClient,HospitalInputReviewClient,planningModel} from './hospital-ui/workflow-state.mjs';
import {HospitalWorkflowPanel,planningMarkup} from './hospital-ui/workflow-panel.mjs';

const context={server:'http://fixture:18048',hospital:'H1',handler:'desk',patient:'P1',version:1};
const memory=()=>{const data=new Map();return {getItem:key=>data.get(key)??null,setItem:(key,value)=>data.set(key,value),removeItem:key=>data.delete(key)};};
const event=(id,extra={})=>({event_id:id,type:'inquiry_closed',at:100+id,patient_id:'P1',hospital_id:'H1',summary:'문의가 종료됐습니다.',delivered_at:null,acknowledged_at:null,...extra});
const envelope=(ctx,items,extra={})=>({scope:{role:'hospital',hospital_id:ctx.hospital,patient_id:ctx.patient,actor_id:ctx.actor},items,has_more:false,next_after_id:items.at(-1)?.event_id??0,...extra});
function setup(handler,{storage=memory(),visible=()=>true,maxPages=10}={}){
  let n=0;const calls=[];const client=new HospitalWorkflowClient({storage,visible,maxPages,makeId:()=>`test-${++n}`,request:async(ctx,path,opts={})=>{
    const call={ctx:{...ctx},path,method:opts.method||'GET',body:opts.body?JSON.parse(opts.body):null};calls.push(call);return handler(call);
  }});client.select(context);return {client,calls,storage};
}
const review=(extra={})=>({patient_id:'P1',patient_version:3,purpose:'allocation_input_display_only',blocking:false,status:'NO_REVIEW_FLAGS',checked_at:90,items:[],...extra});
const planning=(extra={})=>({patient_id:'P1',status:'READY',revision:12,created_at:90,expires_at:200,input_review:review(),availability:{worker_state:'RUNNING'},result:{status:'DESTINATION_HELD',hospital_id:'H1',destination_locked:true,input_review:review(),warnings:[]},...extra});
const patient={id:'P1',version:3,state:'IN_TRANSIT'};

test('planning uses separately queried current input guidance and preserves destination lock',()=>{
  const p=planning();p.result.input_review=review({items:[{level:'review',label:'과거 안내',reason:'저장 이력'}]});
  p.input_review=review({items:[{level:'info',label:'혈압',reason:'확인 불가'}],status:'INFORMATION_ONLY'});
  const m=planningModel({patient,planning:p,currentReview:p.input_review,hospital:'H1',now:100});assert.equal(m.state,'READY');assert.equal(m.inputReview.items[0].label,'혈압');
  const html=planningMarkup(m);assert.match(html,/확정 목적지 잠금 유지/);assert.match(html,/선택 관찰 참고/);assert.doesNotMatch(html,/과거 안내/);assert.match(html,/자동 수용·예약·목적지 변경을 실행하지 않습니다/);
});
test('STALE or patient mismatch clears prior recommendation without hiding current guidance',()=>{
  for(const p of [planning({status:'STALE'}),planning({expires_at:99}),planning({patient_id:'OTHER'}),planning({result:{...planning().result,input_review:review({patient_version:2})}})]){
    const m=planningModel({patient,planning:p,hospital:'H1',now:100});assert.equal(m.result,null);assert.notEqual(m.state,'READY');
  }
  assert.equal(planningModel({patient,planning:planning({status:'STALE'}),currentReview:review(),hospital:'H1',now:100}).inputReview.patient_version,3);
});
test('worker-disabled QUEUED is not falsely shown as computing; unsupported reason is visible',()=>{
  const m=planningModel({patient,planning:planning({status:'QUEUED',result:null,availability:{worker_state:'DISABLED'}}),hospital:'H1',now:100});
  assert.match(planningMarkup(m),/추천 없음 · 배정 워커 확인 필요/);assert.doesNotMatch(planningMarkup(m),/계산 중/);
  const unsupported=planning();unsupported.result.status='NEEDS_REVIEW';unsupported.result.warnings=['UNSUPPORTED_RESOURCE'];
  assert.match(planningMarkup(planningModel({patient,planning:unsupported,hospital:'H1',now:100})),/진료 자리와 전문팀만 계산/);
});
test('foreign hospital result and mismatched top-level review are not displayed as current',()=>{
  const p=planning();p.result.hospital_id='H2';p.input_review=review({patient_id:'OTHER'});
  const m=planningModel({patient,planning:p,currentReview:p.input_review,hospital:'H1',now:100});assert.equal(m.result,null);assert.equal(m.inputReview,null);
  assert.match(planningMarkup(m),/현재 환자 버전의 확인 안내를 아직 조회하지 못했습니다/);
});
test('NO_REVIEW_FLAGS means only no current-policy flags and user text is escaped',()=>{
  const m=planningModel({patient,planning:planning(),currentReview:review(),hospital:'H1',now:100});assert.match(planningMarkup(m),/현재 정책의 재확인 표시 없음/);assert.doesNotMatch(planningMarkup(m),/모든 정보 확인 완료/);
  m.inputReview.items=[{level:'review',label:'<script>',reason:'<img src=x onerror=alert(1)>'}];assert.doesNotMatch(planningMarkup(m),/<img src=x/);assert.match(planningMarkup(m),/&lt;script&gt;/);
});
test('all event pages are read and GET alone creates no delivery or acknowledgement',async()=>{
  const {client,calls}=setup(call=>{const after=Number(new URL(call.path,'http://fixture').searchParams.get('after_id'));const items=Array.from({length:Math.min(100,250-after)},(_,i)=>event(after+i+1));return envelope(call.ctx,items,{has_more:after+items.length<250,next_after_id:after+items.length});});
  await client.refresh();assert.equal(client.items.size,250);assert.equal(client.cursor,250);assert.equal(calls.length,3);assert.ok(calls.every(c=>c.method==='GET'));
});
test('unacknowledged restore is a separate query without positive stream cursor',async()=>{
  const {client,calls}=setup(call=>new URL(call.path,'http://fixture').searchParams.get('unacknowledged_only')==='true'?envelope(call.ctx,[event(1)],{next_after_id:null,has_more:true}):envelope(call.ctx,[],{next_after_id:800}));
  client.cursor=800;await client.refresh({restore:true});assert.ok(client.items.has(1));assert.equal(client.cursor,800);
  const query=new URL(calls[0].path,'http://fixture').searchParams;assert.equal(query.get('after_id'),null);assert.equal(query.get('unacknowledged_only'),'true');assert.equal(client.restorationHasMore,true);
});
test('hidden or not-rendered events cannot be marked delivered and ack requires explicit action',async()=>{
  let visible=false;const {client,calls}=setup(call=>({event:event(1,{delivered_at:150,acknowledged_at:call.path.endsWith('acknowledge')?151:null})}),{visible:()=>visible});
  client.merge(event(1));assert.equal(client.markRendered([1]),false);assert.equal((await client.receipt('delivered',1)).skipped,true);
  visible=true;assert.equal((await client.receipt('delivered',1)).skipped,true);client.markRendered([1]);await client.receipt('delivered',1);
  await assert.rejects(client.receipt('acknowledge',1),/직접 눌러/);assert.equal(calls.length,1);await client.receipt('acknowledge',1,{explicit:true});assert.equal(calls.length,2);
  assert.deepEqual(calls[0].body,{actor_id:client.context.actor});assert.ok(calls.every(c=>c.path.startsWith('/api/hospitals/H1/patients/P1/important-events/')));
});
test('uncertain receipt retry preserves exact actor, path and body across reload',async()=>{
  const storage=memory();const first=setup(()=>{throw Error('response lost');},{storage});first.client.merge(event(1));first.client.markRendered([1]);await assert.rejects(first.client.receipt('delivered',1));
  const next=setup(call=>({event:event(1,{delivered_at:150}),duplicate:true}),{storage});next.client.merge(event(1));next.client.markRendered([1]);await next.client.receipt('delivered',1);
  assert.deepEqual(next.calls[0],first.calls[0]);assert.equal(next.client.pending('delivered',1),null);
});
test('late GET from another patient, hospital, handler or server cannot update selection',async()=>{
  for(const changed of [{patient:'P2'},{hospital:'H2'},{handler:'other'},{server:'http://other'}]){
    let done;const {client}=setup(call=>new Promise(resolve=>{done=()=>resolve(envelope(call.ctx,[event(1)]));}));
    const task=client.refresh();client.select({...context,...changed});done();assert.equal(await task,false);assert.equal(client.items.size,0);assert.equal(client.cursor,0);
  }
});
test('foreign payload scope is rejected before any event is merged',async()=>{
  const {client}=setup(call=>envelope({...call.ctx,hospital:'H2'},[event(1)]));await assert.rejects(client.refresh(),/범위/);assert.equal(client.items.size,0);
});
test('late receipt response never contaminates another patient and double click sends once',async()=>{
  let done;const {client,calls}=setup(()=>new Promise(resolve=>{done=()=>resolve({event:event(1,{delivered_at:150})});}));
  client.merge(event(1));client.markRendered([1]);const a=client.receipt('delivered',1),b=client.receipt('delivered',1);assert.equal(calls.length,1);
  client.select({...context,patient:'P2'});done();assert.equal((await a).stale,true);await b;assert.equal(client.items.size,0);
});
test('old GET cannot undo a successful acknowledgement and definite 409 is not auto replayed',async()=>{
  const {client,calls}=setup(()=>{const error=Error('conflict');error.status=409;throw error;});
  client.merge(event(1,{delivered_at:140,acknowledged_at:150}));client.merge(event(1));assert.equal(client.items.get(1).acknowledged_at,150);
  client.merge(event(2,{delivered_at:140}));client.markRendered([2]);await assert.rejects(client.receipt('acknowledge',2,{explicit:true}),e=>e.status===409);assert.equal(client.pending('acknowledge',2),null);assert.equal(calls.length,1);
});
test('invalid cursor does not silently skip or loop pages',async()=>{
  const {client,calls}=setup(call=>envelope(call.ctx,[event(1)],{has_more:true,next_after_id:0}));await assert.rejects(client.refresh(),/커서/);assert.equal(calls.length,1);assert.equal(client.cursor,0);
});
test('storage failure blocks writes instead of changing actor identity silently',()=>{
  const client=new HospitalWorkflowClient({request:()=>assert.fail('no network'),storage:{getItem:()=>null,setItem:()=>{throw Error('blocked');}},makeId:()=>1});
  assert.throws(()=>client.select(context),/저장/);assert.equal(client.context,null);assert.throws(()=>client.select(context),/저장/);
});

function fakePanel({contextReader=()=>context,identityEditing=()=>false,snapshotReader=()=>({patient,planning:planning(),showPlanning:true}),requestHandler}={}){
  const scheduled=[],calls=[],listeners=new Map();let actorId=0;
  const rect={top:20,bottom:80,left:10,right:500,height:60};const element={dataset:{workflowEvent:'1'},isConnected:true,getBoundingClientRect:()=>rect};
  const list={scrollTop:0,getBoundingClientRect:()=>({top:0,bottom:300,left:0,right:600}),querySelectorAll:()=>[element]};
  const eventRoot={hidden:false,isConnected:true,innerHTML:'',addEventListener:()=>{},getClientRects:()=>[{}],querySelector:s=>s==='#hospitalImportantEventList'?list:null};
  const planningRoot={hidden:false,innerHTML:''};const document={visibilityState:'visible',addEventListener:(name,fn)=>listeners.set(name,fn)};const window={innerHeight:800,innerWidth:1000,addEventListener:()=>{}};
  const panel=new HospitalWorkflowPanel({planningRoot,eventRoot,document,window,storage:memory(),context:contextReader,identityEditing,snapshot:snapshotReader,makeId:()=>++actorId,requestPaint:fn=>scheduled.push(fn),request:async(ctx,path,opts)=>{calls.push({path,opts});return requestHandler?requestHandler(ctx,path,opts):{event:event(1,{delivered_at:150})};}});
  panel.client.select(context);panel.client.merge(event(1));return {panel,document,scheduled,calls,rect,planningRoot};
}
test('WAITING inquiry without a planning row reads current review on selection and periodic refresh',async()=>{
  const guidance=review({status:'REVIEW_REQUIRED',items:[{level:'review',label:'우선순위',reason:'전문가 확인 전'}]});
  const x=fakePanel({snapshotReader:()=>({patient:{...patient,state:'WAITING'},showPlanning:true}),requestHandler:(ctx,path)=>path.endsWith('/input-review')?{patient_id:'P1',patient_version:3,patient_state:'WAITING',input_review:guidance}:envelope(ctx,[])});
  await x.panel.refresh();assert.match(x.planningRoot.innerHTML,/전문가 확인 전/);assert.match(x.planningRoot.innerHTML,/병원용 추천 정보 없음/);assert.doesNotMatch(x.planningRoot.innerHTML,/현재 계산 결과/);
  await x.panel.refresh();assert.equal(x.calls.filter(call=>call.path==='/api/patients/P1/input-review').length,2);
});
test('late current-review reads cannot cross patient, version, server, hospital, handler or actor scope',async()=>{
  for(const changed of [{patient:'P2'},{server:'http://other'},{hospital:'H2'},{handler:'other'},{actor:'other'},{version:2},{patientVersion:4}]){
    let finish;const client=new HospitalInputReviewClient({request:()=>new Promise(resolve=>{finish=resolve;})});client.select(context,patient);
    const task=client.refresh();await Promise.resolve();const next={...context,...changed};const nextPatient={...patient,id:next.patient,version:changed.patientVersion??patient.version};client.select(next,nextPatient);
    finish({patient_id:'P1',patient_version:3,patient_state:'IN_TRANSIT',input_review:review()});assert.equal(await task,false);assert.equal(client.review,null);
  }
});
test('current-review envelope and inner version must match; read errors clear prior guidance without planning fallback',async()=>{
  let value={patient_id:'P1',patient_version:3,patient_state:'IN_TRANSIT',input_review:review()};
  const client=new HospitalInputReviewClient({request:()=>{if(value instanceof Error)throw value;return value;}});client.select(context,patient);assert.equal(await client.refresh(),true);
  for(const invalid of [{patient_id:'OTHER'},{patient_version:2},{input_review:review({patient_id:'OTHER'})},{input_review:review({patient_version:2})}]){
    value={patient_id:'P1',patient_version:3,patient_state:'IN_TRANSIT',input_review:review(),...invalid};assert.equal(await client.refresh(),false);assert.equal(client.review,null);
  }
  value={patient_id:'P1',patient_version:3,patient_state:'IN_TRANSIT',input_review:review()};await client.refresh();value=Error('offline');assert.equal(await client.refresh(),false);assert.equal(client.review,null);
  const model=planningModel({patient,planning:planning(),currentReview:client.review,currentReviewError:client.error,hospital:'H1',now:100});assert.equal(model.inputReview,null);assert.match(planningMarkup(model),/아직 조회하지 못했습니다/);assert.match(planningMarkup(model),/다시 불러오세요/);
});
test('panel writes delivered only after paint and visible matching DOM, never on initial render',async()=>{
  const x=fakePanel();x.panel.renderEvents();assert.equal(x.calls.length,0);x.document.visibilityState='hidden';x.scheduled.shift()();assert.equal(x.calls.length,0);
  x.document.visibilityState='visible';x.panel.afterPaint();x.scheduled.at(-1)();await new Promise(setImmediate);assert.equal(x.calls.length,1);assert.match(x.calls[0].path,/\/1\/delivered$/);assert.equal(x.panel.client.items.get(1).delivered_at,150);
});
test('offscreen items are not delivered merely because their HTML exists',()=>{
  const x=fakePanel();x.rect.top=900;x.rect.bottom=960;x.panel.renderEvents();x.scheduled.shift()();assert.equal(x.calls.length,0);
});
test('handler DOM value is read on sync even if no change event was dispatched',()=>{
  const input={value:''};const x=fakePanel({contextReader:()=>({...context,handler:input.value.trim()})});
  x.panel.sync();assert.equal(x.panel.client.context.actor,null);
  input.value='workflow-desk';x.panel.sync();
  assert.equal(x.panel.client.context.handler,'workflow-desk');assert.ok(x.panel.client.context.actor);assert.doesNotMatch(x.panel.eventRoot.innerHTML,/담당자 입력 필요/);
  const previous=x.panel.client.context.actor;input.value='another-desk';x.panel.sync();assert.notEqual(x.panel.client.context.actor,previous);assert.equal(x.panel.client.items.size,0);
});
test('editing the handler suppresses delivery until blur and a fresh visible paint',async()=>{
  let editing=true;const x=fakePanel({identityEditing:()=>editing});x.panel.renderEvents();x.scheduled.shift()();assert.equal(x.calls.length,0);
  editing=false;x.panel.afterPaint();x.scheduled.at(-1)();await new Promise(setImmediate);assert.equal(x.calls.length,1);
});
test('one failed delivery remains visible across another delivery, GET and ACK until its server receipt is observed',async()=>{
  let firstConfirmed=false,thirdAcknowledged=false;
  const x=fakePanel({requestHandler:(ctx,path,opts)=>{
    if(path.endsWith('/input-review'))return {patient_id:'P1',patient_version:3,input_review:review()};
    if(opts?.method==='POST'){
      if(path.endsWith('/1/delivered'))throw Error('first response lost');
      if(path.endsWith('/3/acknowledge')){thirdAcknowledged=true;return {event:event(3,{delivered_at:150,acknowledged_at:151})};}
      return {event:event(2,{delivered_at:150})};
    }
    return envelope(ctx,[event(1,{delivered_at:firstConfirmed?150:null}),event(2,{delivered_at:150}),event(3,{delivered_at:150,acknowledged_at:thirdAcknowledged?151:null})]);
  }});
  x.panel.client.merge(event(2));x.panel.client.merge(event(3,{delivered_at:150}));x.panel.client.markRendered([1,2,3]);
  await x.panel.delivered(1,x.panel.client.generation);assert.ok(x.panel.receiptErrors.has('delivered:1'));assert.ok(x.panel.client.pending('delivered',1));
  await x.panel.delivered(2,x.panel.client.generation);await x.panel.refresh();await x.panel.acknowledge(3);
  assert.ok(x.panel.receiptErrors.has('delivered:1'));assert.ok(x.panel.deliveryFailures.has(1));assert.match(x.panel.eventRoot.innerHTML,/사건 #1 표시 수신 기록을 전송하지 못했습니다/);
  firstConfirmed=true;await x.panel.refresh();assert.equal(x.panel.receiptErrors.has('delivered:1'),false);assert.equal(x.panel.deliveryFailures.has(1),false);assert.equal(x.panel.client.pending('delivered',1),null);
});
test('failed ACK is specific to its event and clears only after the same exact retry succeeds',async()=>{
  let failFirst=true;const acknowledged=new Set();
  const x=fakePanel({requestHandler:(ctx,path,opts)=>{
    if(path.endsWith('/input-review'))return {patient_id:'P1',patient_version:3,input_review:review()};
    if(opts?.method==='POST'){
      const id=Number(path.match(/\/(\d+)\/acknowledge$/)?.[1]);
      if(id===1&&failFirst)throw Error('first ACK response lost');
      acknowledged.add(id);return {event:event(id,{delivered_at:150,acknowledged_at:151})};
    }
    return envelope(ctx,[1,2].map(id=>event(id,{delivered_at:150,acknowledged_at:acknowledged.has(id)?151:null})));
  }});
  x.panel.client.merge(event(1,{delivered_at:150}));x.panel.client.merge(event(2,{delivered_at:150}));x.panel.client.markRendered([1,2]);
  await x.panel.acknowledge(1);assert.ok(x.panel.receiptErrors.has('acknowledge:1'));
  await x.panel.acknowledge(2);await x.panel.refresh();assert.ok(x.panel.receiptErrors.has('acknowledge:1'));assert.match(x.panel.eventRoot.innerHTML,/사건 #1 확인 기록 전송을 완료하지 못했습니다/);
  failFirst=false;await x.panel.acknowledge(1);assert.equal(x.panel.receiptErrors.size,0);
  const attempts=x.calls.filter(call=>call.path.endsWith('/1/acknowledge'));assert.equal(attempts.length,2);assert.deepEqual(attempts[0],attempts[1]);
});
test('hospital HTML has minimal new hooks while operations module remains separate',()=>{
  const html=readFileSync(new URL('./hospital-ui/index.html',import.meta.url),'utf8');
  new Function(html.split('<script>')[1].split('</script>')[0]);
  for(const id of ['hospitalImportantEvents','hospitalWorkflowPlanning','operationsDetail','externalOperations'])assert.ok(html.includes(`id="${id}"`));
  assert.match(html,/import\('\.\/operations\.mjs'\)/);assert.match(html,/import\('\.\/workflow-panel\.mjs'\)/);
  assert.ok(html.includes("handler:$('handler').value.trim()"));assert.ok(html.includes("$('handler').addEventListener('blur'"));assert.doesNotMatch(html,/workflowHandler/);
});
