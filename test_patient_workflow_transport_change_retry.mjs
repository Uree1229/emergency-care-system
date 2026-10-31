import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {assertTransportRetryScope,hospitalDecisionActor,resetHospitalApprovalChecks} from './patient-ui/transport-retry.mjs';

const scope={pid:'P-SYNTHETIC',hid:'H01',actor:'crew-synthetic'};
const decision=()=>({path:'/api/hospitals/H01/transport-change-reviews/ER-synthetic/decision',
  scope:{...scope},body:{actor_id:'hospital-original',client_request_id:'same-request',
    expected_review_version:1,expected_patient_version:3,decision_token:'synthetic-token',
    decision:'adjust',confirmed:true,reason:'synthetic review'}});
const request=()=>({path:'/api/voice/sessions/VS-synthetic/transport-change-review/request',
  scope:{...scope},body:{actor_id:scope.actor,client_request_id:'same-crew-request',expected_version:2}});

test('legacy v1 hospital decision binds retry to original body actor without changing the request',()=>{
  const pending=JSON.parse(JSON.stringify(decision())),before=JSON.stringify(pending);
  assert.equal(hospitalDecisionActor(pending),'hospital-original');
  assert.throws(()=>assertTransportRetryScope(pending,scope,'hospital-other'),error=>{
    assert.equal(error.code,'LOCAL_TRANSPORT_RETRY_SCOPE');assert.equal(error.status,undefined);
    assert.match(error.message,/hospital-original/);return true;
  });
  assert.equal(JSON.stringify(pending),before);assert.equal(Object.hasOwn(pending.scope,'hospitalActor'),false);
  assert.equal(assertTransportRetryScope(pending,scope,'hospital-original'),true);
  assert.equal(JSON.stringify(pending),before);
});
test('restored pending does not adopt a reload default or replacement hospital actor',()=>{
  const pending=JSON.parse(JSON.stringify(decision()));
  for(const value of ['enroute-hospital-demo','hospital-other',''])assert.throws(()=>assertTransportRetryScope(pending,scope,value),/원래 병원 작업자 ID/);
  assert.equal(assertTransportRetryScope(pending,scope,' hospital-original '),true);
});
test('patient, destination hospital and crew identity remain independently bound',()=>{
  for(const changed of [{pid:'P-OTHER'},{hid:'H02'},{actor:'crew-other'}]){
    assert.throws(()=>assertTransportRetryScope(decision(),{...scope,...changed},'hospital-original'),/원래 이송·병원·구급대/);
  }
});
test('crew request retry is independent of hospitalActor but retains its crew scope',()=>{
  const pending=request(),before=JSON.stringify(pending);
  assert.equal(hospitalDecisionActor(pending),null);
  for(const value of ['hospital-original','hospital-other',''])assert.equal(assertTransportRetryScope(pending,scope,value),true);
  assert.equal(JSON.stringify(pending),before);
  assert.throws(()=>assertTransportRetryScope(pending,{...scope,actor:'crew-other'},'hospital-original'),/구급대/);
});
test('malformed legacy hospital actor fails locally without guessing another identity',()=>{
  for(const value of [undefined,null,42,'','hospital with space']){
    const pending=decision();pending.body.actor_id=value;
    assert.throws(()=>assertTransportRetryScope(pending,scope,'hospital-original'),error=>error.code==='LOCAL_TRANSPORT_RETRY_SCOPE'&&!error.status);
  }
});
test('validation also accepts frozen original pending objects without mutation',()=>{
  const pending=decision();Object.freeze(pending.body);Object.freeze(pending.scope);Object.freeze(pending);
  assert.equal(assertTransportRetryScope(pending,scope,'hospital-original'),true);
});

function page(pending,{hospitalActor='hospital-other',succeed=false}={}){
  const elements=new Map(),windowEvents=new Map(),requests=[],memory=new Map();
  const key='enroute-review-pending:http://fixture.test';memory.set(key,JSON.stringify(pending));
  function element(id){
    if(elements.has(id))return elements.get(id);
    const handlers=new Map(),value={id,value:'',checked:false,hidden:false,disabled:false,dataset:{},children:[],
      classList:{toggle(){}},addEventListener:(name,fn)=>{if(!handlers.has(name))handlers.set(name,[]);handlers.get(name).push(fn);},
      async emit(name){for(const fn of handlers.get(name)||[])await fn({target:value});},
      replaceChildren(){this.children=[];},append(...rows){this.children.push(...rows);},
      querySelectorAll:()=>[]};elements.set(id,value);return value;
  }
  const approval={checked:true,disabled:false,dataset:{hospitalDecisionConfirm:'true'}};
  element('hospitalActor').value=hospitalActor;element('requestConfirm').checked=true;
  element('reviews').querySelectorAll=selector=>selector==='[data-hospital-decision-confirm]'?[approval]:[];
  const environment={URL,URLSearchParams,location:{origin:'http://fixture.test',search:'?pid=P-SYNTHETIC&hid=H01&actor_id=crew-synthetic'},
    document:{getElementById:element,querySelectorAll:()=>[...elements.values(),approval],createElement:tag=>element('created-'+tag)},
    window:{addEventListener:(name,fn)=>windowEvents.set(name,fn)},
    sessionStorage:{getItem:k=>memory.get(k)??null,setItem:(k,v)=>memory.set(k,v),removeItem:k=>memory.delete(k)},
    crypto:{randomUUID:()=>assert.fail('retry must not generate a new request ID')},
    fetch:async(path,opts)=>{
      requests.push({path,method:opts.method,body:opts.body?JSON.parse(opts.body):null});
      if(!succeed)throw Error('synthetic lost response');
      const result=opts.method==='POST'?{session:{id:'VS-synthetic'},duplicate:true}
        :path.startsWith('/api/voice/')?{session:{id:'VS-synthetic',version:2,confirmed:true,speak_text:'synthetic'}}
        :path.startsWith('/api/patients/')?{id:scope.pid,state:'IN_TRANSIT',version:4,destination_hospital_id:'H01',resource_demands:{ed_bed:1}}
        :{reviews:[]};
      return {ok:true,status:200,json:async()=>result};
    },assertTransportRetryScope,hospitalDecisionActor,resetHospitalApprovalChecks};
  const source=readFileSync(new URL('./patient-ui/transport-change.mjs',import.meta.url),'utf8');
  const program=source.replace(/^import[^\n]+from '\.\/transport-retry\.mjs';\r?\n/,'');
  assert.notEqual(program,source,'actual UI must import the retry helper');vm.runInNewContext(program,environment);
  return {elements,element,approval,requests,memory,key,windowEvents};
}
test('actual UI hospital actor input resets hospital approvals and preserves crew confirmation',async()=>{
  const p=page(decision());await p.element('hospitalActor').emit('input');
  assert.equal(p.approval.checked,false);assert.equal(p.element('requestConfirm').checked,true);
  assert.match(p.element('status').textContent,/hospital-original/);assert.equal(p.requests.length,0);
});
test('actual UI restored decision blocks the displayed wrong hospital actor before HTTP and preserves pending',async()=>{
  const pending=decision(),p=page(pending);await p.windowEvents.get('pageshow')();await p.element('retry').emit('click');
  assert.equal(p.requests.length,0);assert.match(p.element('status').textContent,/원래 병원 작업자 ID는 hospital-original/);
  assert.equal(p.memory.get(p.key),JSON.stringify(pending));assert.equal(p.element('retry').hidden,false);
});
test('actual UI returning to the original hospital actor retries exact stored body and clears successful pending',async()=>{
  const pending=decision(),p=page(pending,{succeed:true});await p.element('retry').emit('click');assert.equal(p.requests.length,0);
  p.element('hospitalActor').value='hospital-original';await p.element('hospitalActor').emit('input');await p.element('retry').emit('click');
  const posts=p.requests.filter(row=>row.method==='POST');assert.equal(posts.length,1);assert.equal(posts[0].path,pending.path);assert.deepEqual(posts[0].body,pending.body);
  assert.equal(p.memory.has(p.key),false);assert.equal(p.element('retry').hidden,true);assert.match(p.element('status').textContent,/원래 요청의 저장 결과/);
});
test('actual UI crew request retry ignores the separately displayed hospital actor',async()=>{
  const pending=request(),p=page(pending,{hospitalActor:'unrelated-hospital',succeed:true});await p.element('retry').emit('click');
  const posts=p.requests.filter(row=>row.method==='POST');assert.equal(posts.length,1);assert.equal(posts[0].path,pending.path);assert.deepEqual(posts[0].body,pending.body);
  assert.equal(p.memory.has(p.key),false);
});
