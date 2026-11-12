import test from 'node:test';
import assert from 'node:assert/strict';
import {HospitalOperations,HospitalOperationsPanel,epochKst,localInput} from '../../hospital-ui/operations.mjs';

const context={server:'http://127.0.0.1:8000',hospital:'H1',handler:'desk one',version:1};
const memory=()=>{const values=new Map();return {getItem:k=>values.get(k)??null,setItem:(k,v)=>values.set(k,v),removeItem:k=>values.delete(k)};};
function setup(handler,{storage=memory(),monotonic=()=>0}={}){let id=0;const requests=[];const client=new HospitalOperations({storage,makeId:()=>`id-${++id}`,monotonic,
 request:async(ctx,path,opts={})=>{const call={ctx:{...ctx},path,method:opts.method||'GET',body:opts.body?JSON.parse(opts.body):null};requests.push(call);return handler(call);}});client.select(context);client.setPatient('P1');return {client,requests,storage};}
const record=(extra={})=>({id:1,patient_id:'P1',hospital_id:'H1',author_role:'paramedic',actor_id:'crew',client_record_id:'call1',reported_outcome:'can_accept',confirmation_status:'unconfirmed',call_started_at:100,call_ended_at:110,recorded_at:111,note:'note',...extra});
const inbox=records=>({scope:{hospital_id:'H1'},records,has_more:false});
const activity=(extra={})=>({id:3,patient_id:'P1',hospital_id:'H1',actor_id:'hospital-id-1',client_activity_id:'work1',kind:'request_review',state:'OPEN',started_at:100,ended_at:null,completed_seconds:0,open_elapsed_seconds:10,...extra});

test('uncertain external registration retains exact request id and body across reload and handler change',async()=>{
 const shared=memory();const first=setup(()=>{throw Error('response lost');},{storage:shared});
 await assert.rejects(first.client.registerExternal({resource_demands:{ed_bed:2},note:'fixture'}),e=>e.pending);
 const retry=setup(call=>({external_arrival:{id:2,hospital_id:'H1'},duplicate:true}),{storage:shared});retry.client.select({...context,handler:'desk two'});
 const result=await retry.client.registerExternal({resource_demands:{ed_bed:99},note:'changed'});
 assert.deepEqual(retry.requests[0].body,first.requests[0].body);assert.equal(result.value.duplicate,true);assert.equal(retry.client.pending('external-register'),null);
});
test('double click submits only one request and retains the same activity id',async()=>{
 let done;const {client,requests}=setup(()=>new Promise(resolve=>{done=resolve;}));
 const a=client.startWork('request_review'),b=client.startWork('request_review');
 assert.equal(requests.length,1);done({activity:activity()});await Promise.all([a,b]);assert.equal(requests[0].body.kind,'request_review');
});
test('same call and same confirmation retries keep original content and decision',async()=>{
 let fail=true;const {client,requests}=setup(()=>{if(fail)throw Error('timeout');return {record:record()};});
 await assert.rejects(client.createCall({note:'original',call_started_at:100,call_ended_at:110,reported_outcome:'can_accept',counterparty_role:'paramedic'}));
 fail=false;await client.createCall({note:'changed'});assert.deepEqual(requests[0].body,requests[1].body);
 fail=true;await assert.rejects(client.confirmCall(record(),'dispute','old reason'));fail=false;await client.confirmCall(record(),'confirm','');
 assert.deepEqual(requests[2].body,requests[3].body);assert.equal(requests[3].body.decision,'dispute');
 assert.ok(requests.every(r=>r.path.includes('call-records')));assert.ok(requests.every(r=>!r.path.includes('/respond')));
});
test('definitive 409 does not auto replay and allows a new explicit corrected request',async()=>{
 let fail=true;const {client,requests}=setup(()=>{if(fail){const e=Error('conflict');e.status=409;throw e;}return {record:record()};});
 await assert.rejects(client.createCall({note:'before'}),e=>!e.pending&&e.status===409);assert.equal(requests.length,1);
 assert.equal(client.pending('call:P1'),null);fail=false;await client.createCall({note:'corrected'});
 assert.notEqual(requests[0].body.client_record_id,requests[1].body.client_record_id);
});
test('external discharge posts only selected entry with its displayed version, never aggregate resource writes',async()=>{
 const {client,requests}=setup(()=>({external_arrival:{id:8,hospital_id:'H1',state:'DISCHARGED'},duplicate:false}));
 await client.dischargeExternal({id:8,hospital_id:'H1',state:'ACTIVE',version:4});
 assert.equal(requests[0].path,'/api/hospitals/H1/external-arrivals/8/discharge');assert.deepEqual(requests[0].body,{expected_version:4,handled_by:'desk one'});
 assert.throws(()=>client.dischargeExternal({id:9,hospital_id:'H2',state:'ACTIVE',version:1}));
 assert.throws(()=>client.dischargeExternal({id:9,hospital_id:'H1',state:'LEGACY',version:1}));assert.equal(requests.length,1);
});
test('an old hospital response cannot replace the new hospital inbox',async()=>{
 let done;const {client}=setup(()=>new Promise(resolve=>{done=resolve;}));const request=client.loadCalls();
 client.select({...context,hospital:'H2',version:2});done(inbox([record()]));assert.equal(await request,false);assert.deepEqual(client.calls.records,[]);
});
test('newer inbox filter wins over older same-hospital request',async()=>{
 const pending=[];const {client}=setup(()=>new Promise(resolve=>pending.push(resolve)));
 const old=client.loadCalls({status:'all'}),fresh=client.loadCalls({status:'unconfirmed'});
 pending[1](inbox([record({id:2})]));await fresh;pending[0](inbox([record({id:1})]));assert.equal(await old,false);assert.equal(client.calls.records[0].id,2);
});
test('mismatched hospital records and mutation results are rejected',async()=>{
 const {client}=setup(call=>call.method==='GET'?inbox([record({hospital_id:'H2'})]):{record:record({hospital_id:'H2'})});
 await assert.rejects(client.loadCalls(),/다른 병원/);assert.deepEqual(client.calls.records,[]);
 await assert.rejects(client.createCall({note:'test'}),/다른 병원/);assert.ok(client.pending('call:P1'));
});
test('work display uses monotonic elapsed time and completed values stay fixed',async()=>{
 let mono=1000;const {client}=setup(()=>({}),{monotonic:()=>mono});client.workFetchedMono=mono;
 mono+=4500;assert.equal(client.elapsed(activity()),14);assert.equal(client.elapsed(activity({state:'CLOSED',completed_seconds:7})),7);
 mono=-100;assert.equal(client.elapsed(activity()),10);
});
test('open work resumes from server data and an acknowledged start retry is reconciled after reload',async()=>{
 const shared=memory();const first=setup(()=>{throw Error('lost');},{storage:shared});await assert.rejects(first.client.startWork('request_review'));
 const saved=first.client.pending('work-start:P1');const {client}=setup(call=>{
  const scope={hospital_id:'H1',patient_id:'P1'};
  return call.path.includes('/summary')?{scope,totals:{open_activity_count:1}}:{scope,activities:[activity({client_activity_id:saved.payload.client_activity_id})]};
 },{storage:shared});await client.loadWork();assert.equal(client.work.activities[0].state,'OPEN');assert.equal(client.pending('work-start:P1'),null);
});
test('stop refuses another actor and never sends client clocks or call duration',async()=>{
 const {client,requests}=setup(()=>({activity:activity({state:'CLOSED'})}));
 assert.throws(()=>client.stopWork(activity({actor_id:'other'})),/현재 담당자/);
 await client.stopWork(activity());assert.deepEqual(requests[0].body,{actor_id:'hospital-id-1'});assert.match(requests[0].path,/work-activities\/3\/stop$/);
});
test('storage failure stops mutation before sending an unrecoverable request',async()=>{
 const s=memory();const {client,requests}=setup(()=>({}),{storage:s});s.setItem=()=>{throw Error('quota');};
 await assert.rejects(client.registerExternal({resource_demands:{ed_bed:1}}),/저장할 수 없습니다/);assert.equal(requests.length,0);
});
test('KST input conversion is stable independently of computer local timezone',()=>{
 assert.equal(epochKst('2026-10-30T09:00:00'),1793318400);assert.equal(localInput(1793318400),'2026-10-30T09:00:00');assert.ok(Number.isNaN(epochKst('invalid')));
});
test('render escapes call contents and separates record confirmation from acceptance',()=>{
 const {client}=setup(()=>({}));client.calls=inbox([record({note:'<img src=x onerror=alert(1)>'})]);
 const root={innerHTML:'',addEventListener(){},insertAdjacentHTML(_,html){this.innerHTML+=html;}};const external={...root};
 const panel=new HospitalOperationsPanel({root,externalRoot:external,client,context:()=>context,notice(){}});panel.render();
 assert.ok(root.innerHTML.includes('&lt;img'));assert.ok(!root.innerHTML.includes('<img'));assert.ok(root.innerHTML.includes('통화 내용 확인은 수용 응답이 아닙니다.'));
 assert.ok(root.innerHTML.includes('통화 기록 내용 확인'));assert.ok(root.innerHTML.includes('수용 가능하다고 전달받음'));
});
test('background refresh never replaces a focused date input node and defers new records until idle',async()=>{
 const source=[record()];const {client}=setup(call=>call.path.includes('data-provenance')?{hospitals:[]}:
  call.path.includes('/summary')?{scope:{hospital_id:'H1',patient_id:'P1'},totals:{open_activity_count:0}}:
  call.path.includes('work-activities')?{scope:{hospital_id:'H1',patient_id:'P1'},activities:[]}:inbox([...source]));
 const input={tagName:'INPUT',type:'datetime-local',value:'2026-10-30T15:20',isConnected:true};let writes=0;
 const root={ownerDocument:{activeElement:input},contains:node=>node===input,addEventListener(){},querySelectorAll:()=>[]};
 const panel=new HospitalOperationsPanel({root,externalRoot:{addEventListener(){}},client,context:()=>context,notice(){}});
 panel.render=()=>{writes++;input.isConnected=false;};panel.backgroundSignature=panel.readSignature();
 await panel.refresh({background:true});assert.equal(writes,0);assert.equal(input.isConnected,true);assert.equal(input.value,'2026-10-30T15:20');assert.equal(client.calls.records.length,1);
 root.ownerDocument.activeElement=null;await panel.refresh({background:true});assert.equal(writes,1);
 await panel.refresh({background:true});assert.equal(writes,1);
 await panel.refresh();assert.equal(writes,2); // explicit refresh is immediate
});
