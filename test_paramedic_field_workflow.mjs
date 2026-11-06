import test from 'node:test';
import assert from 'node:assert/strict';
import {initFieldWorkflow} from './paramedic-ui/field-workflow.mjs';

class Element{
  constructor(tag,doc){this.tagName=tag.toUpperCase();this.doc=doc;this.children=[];this.events={};this.value='';this.checked=false;this.disabled=false;this.hidden=false;this.textContent='';this.dataset={};this.attributes={};this.classList={add(){},remove(){},toggle(){}};}
  set id(value){this._id=value;this.doc.nodes.set(value,this);}get id(){return this._id;}
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(...nodes){this.children=[...nodes];}
  addEventListener(name,listener){this.events[name]=listener;}
  setAttribute(key,value){this.attributes[key]=value;}
  removeAttribute(key){delete this.attributes[key];}
  get selectedOptions(){return this.children.filter(c=>c.selected);}
}
const clone=value=>JSON.parse(JSON.stringify(value));
const registered=()=>({id:'VS-A',actor_id:'CREW-A',version:2,state:'SUBMITTED',confirmed:true,patient_id:'P-A',fields:{age:47},field_metadata:{}});
const flush=async()=>{for(let n=0;n<5;n++)await new Promise(resolve=>setImmediate(resolve));};
const text=element=>[element.textContent,...element.children.map(text)].join(' ');
function fixture({apiHook,flowHook,session:initial,canConfirm=()=>true}={}){
  const doc={nodes:new Map(),createElement(tag){return new Element(tag,this);},defaultView:{navigator:{}}},host=doc.createElement('main');
  const calls=[],steps=[],errors=[],busy=[];let blocked=false,epoch=1,recordId='NOTE-A',changes=0;
  const patient={id:'P-A',version:1,state:'WAITING',lat:37.5,lon:126.95};
  const client={server:'http://127.0.0.1:18999',actorId:'CREW-A',pending:null,session:initial||{id:'VS-A',actor_id:'CREW-A',version:2,state:'DRAFT',confirmed:false,fields:{age:47,sex:'male',symptoms:['흉통'],pulse:92},field_metadata:{},warnings:[]},async api(path){calls.push({method:'GET',path});const answer=await apiHook?.(path);if(answer!==undefined)return answer;
    if(path==='/api/voice/terms')return {supported_fields:['age','sex','symptoms','consciousness','breathing','systolic_bp','diastolic_bp','pulse','spo2','temperature','onset_at']};
    if(path==='/api/voice/capabilities')return {r1_fields:['avpu','pre_ktas','chief_complaint','age_category']};
    if(path==='/api/hospitals')return [{id:'H01',capabilities:['응급의학','심장내과']}];
    if(path==='/api/patients/P-A')return clone(patient);
    if(path==='/api/patients/P-A/candidates')return {candidates:[{name:'가상 병원',address:'가상 주소',travel:{duration_seconds:120},reasons:['예시'],checks:['수용 확인 필요']}]};
    throw Error('unexpected GET '+path);
  }};
  const flow={async reviewFields(body){calls.push({method:'PATCH',operation:'review-fields',body:clone(body)});const r=await flowHook?.('review-fields',body);if(r!==undefined)return r;Object.assign(client.session.fields,body.changes);for(const key of body.unset_fields)delete client.session.fields[key];client.session.version++;client.session.confirmed=false;return {session:clone(client.session)};},async confirm(){calls.push({method:'POST',operation:'confirm'});const r=await flowHook?.('confirm');if(r!==undefined)return r;client.session.confirmed=true;return {session:clone(client.session)};},async submit(context){calls.push({method:'POST',operation:'submit',body:clone(context)});const r=await flowHook?.('submit',context);if(r!==undefined)return r;client.session.patient_id='P-A';client.session.state='SUBMITTED';return {session:clone(client.session),patient:clone(patient)};}};
  const getContext=()=>({client,flow,actor:client.actorId,epoch,recordId,session:client.session});
  const controller=initFieldWorkflow({document:doc,host,getContext,isBlocked:()=>blocked,canConfirm,onBusy:value=>busy.push(value),onError:error=>errors.push(error),onChange:()=>changes++,onStep:value=>steps.push(value)});
  const el=id=>doc.nodes.get('fw-'+id);
  const click=async id=>{const target=el(id);assert.ok(target,id);if(!target.disabled)return target.events.click?.({preventDefault(){},stopPropagation(){}});};
  const input=(id,value)=>{const target=el(id);target.value=value;target.events.input?.();};
  return {controller,client,flow,calls,steps,errors,busy,host,el,click,input,doc,getContext,changePatient(){epoch++;recordId='NOTE-B';client.session={id:'VS-B',actor_id:'CREW-A',version:1,state:'DRAFT',confirmed:false,fields:{age:70},field_metadata:{}};},setBlocked:value=>blocked=value,get changes(){return changes;}};
}

test('a server without handoff exchange keeps hospital progress usable and hides the unavailable destination',async()=>{
  const f=fixture({session:registered()});
  f.controller.open('hospital');await flush();
  assert.equal(f.el('handoff').hidden,true);
  assert.equal(f.el('links').hidden,false);
  assert.match(f.el('progress').href,/pid=P-A/);
  assert.ok(f.calls.every(call=>call.method==='GET'));
});

test('handoff exchange becomes available only after the current server advertises support',async()=>{
  const f=fixture({session:registered(),apiHook:async path=>path==='/api/voice/capabilities'?{handoff_exchange_revision:'supported'}:undefined});
  assert.equal(f.el('handoff').hidden,true);
  f.controller.open('hospital');await flush();
  assert.equal(f.el('handoff').hidden,false);
  assert.match(f.el('handoff').href,/\/handoff-exchange\.html\?/);
  assert.match(f.el('handoff').href,/sid=VS-A/);
  assert.ok(f.calls.every(call=>call.method==='GET'));
});

test('opening review only reads references and never creates/confirms/registers a patient',async()=>{
  const f=fixture();assert.equal(f.controller.open('review'),true);await flush();
  assert.equal(f.host.hidden,false);assert.equal(f.el('review').hidden,false);assert.equal(f.el('registration').hidden,true);
  assert.ok(f.calls.every(call=>call.method==='GET'));assert.equal(f.el('field-breathing').value,'');assert.equal(f.el('field-pre_ktas').value,'');assert.equal(f.el('consent').checked,false);
  assert.equal(f.controller.selectStep('submit'),false);assert.equal(f.controller.selectStep('hospital'),false);
});

test('edits preserve unknowns and save a full review delta only after the save action',async()=>{
  const f=fixture();f.controller.open('review');await flush();
  f.input('field-age','48');f.input('field-pre_ktas','2');f.input('field-pulse','');
  assert.equal(f.controller.hasUnapplied(),true);assert.equal(f.el('confirm').disabled,true);
  assert.equal(f.calls.filter(c=>c.method!=='GET').length,0);await f.click('save');
  assert.deepEqual(f.calls.find(c=>c.operation==='review-fields').body,{changes:{age:48,pre_ktas:2},unset_fields:['pulse']});
  assert.equal(f.client.session.fields.age,48);assert.equal(f.client.session.fields.breathing,undefined);assert.equal(f.controller.hasUnapplied(),false);assert.equal(f.controller.getStage(),'review');
});

test('dirty edit survives a newer session and requires explicit discard instead of overwriting',async()=>{
  const f=fixture();f.controller.open('review');await flush();f.input('field-pulse','100');
  f.client.session.version++;f.client.session.fields.pulse=80;f.controller.update();
  assert.equal(f.el('field-pulse').value,'100');assert.equal(f.controller.state().editorStale,true);assert.equal(f.el('save').disabled,true);
  await f.click('discard');assert.equal(f.el('field-pulse').value,'80');assert.equal(f.controller.hasUnapplied(),false);assert.equal(f.calls.filter(c=>c.method!=='GET').length,0);
});

test('explicit confirmation advances to registration, consent and valid coordinates gate submit',async()=>{
  const f=fixture();f.controller.open('review');await flush();await f.click('confirm');
  assert.equal(f.controller.getStage(),'submit');assert.equal(f.el('submit').disabled,true);assert.deepEqual(f.calls.filter(c=>c.method!=='GET').map(c=>c.operation),['confirm']);
  f.input('lat','91');f.input('lon','126.95');f.el('consent').checked=true;f.el('consent').events.change();assert.equal(f.el('submit').disabled,true);
  f.input('lat','37.5');assert.equal(f.el('submit').disabled,false);
  const required=f.el('required-capabilities');required.children.find(c=>c.value==='응급의학').selected=true;required.events.change();
  await f.click('submit');await flush();
  assert.deepEqual(f.calls.find(c=>c.operation==='submit').body,{consent:true,lat:37.5,lon:126.95,required_capabilities:['응급의학'],resource_demands:{ed_bed:1}});
  assert.equal(f.controller.getStage(),'hospital');assert.equal(f.el('submit').hidden,true);assert.match(text(f.el('candidates')),/가상 병원/);
  assert.match(f.el('progress').href,/pid=P-A/);assert.match(f.el('handoff').href,/sid=VS-A/);assert.ok(f.calls.every(c=>!c.path?.includes('/inquiries')));
  const count=f.calls.filter(c=>c.operation==='submit').length;await f.click('submit');assert.equal(f.calls.filter(c=>c.operation==='submit').length,count);
});

test('a failed registration preserves all form input and never guesses success',async()=>{
  const f=fixture({flowHook:async operation=>{if(operation==='submit')throw Error('저장 결과 미확인');}});f.controller.open('review');await flush();await f.click('confirm');f.input('lat','37.5');f.input('lon','126.95');f.el('consent').checked=true;f.el('consent').events.change();await f.click('submit');
  assert.equal(f.controller.getStage(),'submit');assert.equal(f.el('lat').value,'37.5');assert.equal(f.el('consent').checked,true);assert.equal(f.client.session.patient_id,undefined);assert.equal(f.errors.length,1);
});

test('stale reference and registration replies cannot repaint another patient',async()=>{
  let release;const pending=new Promise(resolve=>release=resolve);
  const f=fixture({apiHook:async path=>{if(path==='/api/hospitals')return pending;}});f.controller.open('review');f.changePatient();f.controller.update();release([{capabilities:['이전 환자 전용']}]);await flush();
  assert.equal(f.el('required-capabilities').children.length,0);assert.equal(f.controller.getStage(),null);
  let releaseSubmit;const f2=fixture({flowHook:async operation=>{if(operation==='submit')return new Promise(resolve=>releaseSubmit=resolve);}});f2.controller.open('review');await flush();await f2.click('confirm');f2.input('lat','37.5');f2.input('lon','126.95');f2.el('consent').checked=true;f2.el('consent').events.change();const work=f2.click('submit');f2.changePatient();f2.controller.update();releaseSubmit({session:{id:'VS-A',actor_id:'CREW-A',state:'SUBMITTED'},patient:{id:'P-A'}});await work;assert.equal(f2.controller.getStage(),null);assert.equal(f2.controller.state().patient,null);
});

test('form export and reload restoration retain coordinates and version-bound edits without writing',async()=>{
  const f=fixture();f.controller.open('review');await flush();f.input('field-pulse','105');f.input('lat','37.50');
  const ui=f.controller.exportUIState();const restored=fixture();assert.equal(restored.controller.restoreUIState(ui),true);await flush();
  assert.equal(restored.el('lat').value,'37.50');assert.equal(restored.el('field-pulse').value,'105');assert.equal(restored.controller.hasUnapplied(),true);assert.ok(restored.calls.every(c=>c.method==='GET'));
  restored.client.session.version=3;restored.controller.update();assert.equal(restored.el('save').disabled,true);assert.equal(restored.el('field-pulse').value,'105');
  assert.equal(restored.controller.restoreUIState({...ui,sessionId:'VS-OTHER'}),false);
});

test('submitted same-patient session restores hospital information with no new draft or write',async()=>{
  const f=fixture({session:{id:'VS-A',actor_id:'CREW-A',version:2,state:'SUBMITTED',confirmed:true,patient_id:'P-A',fields:{age:47},field_metadata:{}}});
  assert.equal(f.controller.open('hospital'),true);await flush();assert.match(text(f.el('patient')),/P-A/);assert.equal(f.el('submit').hidden,true);assert.ok(f.calls.every(c=>c.method==='GET'));
  f.setBlocked(true);f.controller.update();assert.equal(f.el('progress').attributes['aria-disabled'],'true');assert.equal(f.controller.close(),false);
});

test('restored unpopulated R1 edits become visible when reference metadata arrives',async()=>{
  const f=fixture();const ui={sessionId:'VS-A',version:2,step:'review',lat:'',lon:'',consent:false,requiredCapabilities:['보관된 진료'],editor:{pre_ktas:'2'},editorDirty:true};
  assert.equal(f.controller.restoreUIState(ui),true);await flush();
  assert.equal(f.el('field-pre_ktas').value,'2');assert.equal(f.controller.hasUnapplied(),true);
  assert.ok(f.el('required-capabilities').selectedOptions.some(option=>option.value==='보관된 진료'));
});

test('external raw-text disposition blocks confirmation and registration while retaining review, editing and back navigation',async()=>{
  let allowed=false;const f=fixture({canConfirm:()=>allowed});
  assert.equal(f.controller.open('review'),true);await flush();assert.equal(f.el('confirm').disabled,true);
  f.input('field-age','48');assert.equal(f.el('save').disabled,false);await f.click('save');
  assert.equal(f.client.session.fields.age,48);assert.equal(f.el('confirm').disabled,true);
  assert.equal(f.controller.close(),true);assert.equal(f.controller.open('review'),true);
  f.client.session.confirmed=true;f.controller.update();assert.equal(f.controller.selectStep('submit'),true);
  f.input('lat','37.5');f.input('lon','126.95');f.el('consent').checked=true;f.el('consent').events.change();
  assert.equal(f.el('submit').disabled,true);assert.equal(f.controller.state().canSubmit,false);
  assert.deepEqual(f.calls.filter(call=>call.method!=='GET').map(call=>call.operation),['review-fields']);
  allowed=true;f.controller.update();assert.equal(f.el('submit').disabled,false);assert.equal(f.controller.state().canSubmit,true);
});

test('a hanging previous-patient hospital GET does not block new reads or clear the new owner loading state',async()=>{
  let releaseA,releaseB;const waitA=new Promise(resolve=>releaseA=resolve),waitB=new Promise(resolve=>releaseB=resolve);
  const f=fixture({session:{id:'VS-A',actor_id:'CREW-A',version:2,state:'SUBMITTED',confirmed:true,patient_id:'P-A',fields:{age:47},field_metadata:{}},apiHook:async path=>{
    if(path==='/api/patients/P-A')return waitA;
    if(path==='/api/patients/P-B')return waitB;
    if(path==='/api/patients/P-B/candidates')return {candidates:[{name:'새 환자 병원',travel:{duration_seconds:300},checks:[],reasons:[]}]};
  }});
  f.controller.open('hospital');await flush();assert.equal(f.controller.state().busy,true);
  f.changePatient();Object.assign(f.client.session,{state:'SUBMITTED',confirmed:true,patient_id:'P-B'});f.controller.update();f.controller.open('hospital');await flush();
  assert.ok(f.calls.some(call=>call.path==='/api/patients/P-B'),'new owner starts without waiting for old GET');
  releaseA({id:'P-A',version:1,state:'WAITING'});await flush();assert.equal(f.controller.state().busy,true,'old finally cannot clear the new request');
  assert.equal(f.controller.state().patient,null);
  releaseB({id:'P-B',version:1,state:'WAITING'});await flush();assert.equal(f.controller.state().busy,false);assert.equal(f.controller.state().patient.id,'P-B');assert.match(text(f.el('candidates')),/새 환자 병원/);
  assert.ok(f.calls.every(call=>call.method==='GET'));
});

test('old reference-load completion cannot clear the replacement owner request or overwrite its catalog',async()=>{
  let count=0,releaseA,releaseB;const waitA=new Promise(resolve=>releaseA=resolve),waitB=new Promise(resolve=>releaseB=resolve);
  const f=fixture({apiHook:async path=>{if(path==='/api/voice/terms')return ++count===1?waitA:waitB;}});
  f.controller.open('review');f.changePatient();f.controller.update();f.controller.open('review');await flush();assert.equal(count,2,'new owner metadata starts while old request is hanging');
  releaseA({supported_fields:['glucose']});await flush();f.controller.open('review');await flush();assert.equal(count,2,'old finally leaves current request marked in flight');
  assert.equal(f.el('field-glucose'),undefined,'old catalog is never rendered');
  releaseB({supported_fields:['temperature']});await flush();assert.ok(f.el('field-temperature'));assert.equal(f.el('field-glucose'),undefined);assert.ok(f.calls.every(call=>call.method==='GET'));
});
