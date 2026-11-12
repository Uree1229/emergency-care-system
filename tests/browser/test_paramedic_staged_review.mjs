import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {initStagedReview,canReturnToConversation} from '../../paramedic-ui/staged-review.mjs';
import {emptyBook,createPatientRecord,updatePatientCheckpoint,PATIENT_RECORDS_KEY} from '../../paramedic-ui/patient-records.mjs';
import {writeDraftRecovery,RECOVERY_KEY} from '../../paramedic-ui/draft-recovery.mjs';

// Synthetic DOM checks of presentation and navigation guards; no microphone or clinical validation.
class Element {
  constructor(tag='div',id='',className=''){
    Object.assign(this,{tagName:tag.toUpperCase(),id,className,children:[],parentNode:null,hidden:false,open:false,disabled:false,dataset:{},attributes:{},events:{},textContent:''});
    this.classList={add:(name)=>this.toggleClass(name,true),toggle:(name,yes)=>this.toggleClass(name,yes)};
  }
  toggleClass(name,yes){const names=new Set(this.className.split(/\s+/).filter(Boolean));if(yes)names.add(name);else names.delete(name);this.className=[...names].join(' ');}
  removeChild(child){this.children.splice(this.children.indexOf(child),1);child.parentNode=null;}
  append(...children){for(const child of children){child.parentNode?.removeChild(child);this.children.push(child);child.parentNode=this;}}
  insertBefore(child,before){if(!before){this.append(child);return;}assert.equal(before.parentNode,this);child.parentNode?.removeChild(child);this.children.splice(this.children.indexOf(before),0,child);child.parentNode=this;}
  after(child){this.parentNode.insertBefore(child,this.parentNode.children[this.parentNode.children.indexOf(this)+1]);}
  setAttribute(name,value){this.attributes[name]=value;}
  removeAttribute(name){delete this.attributes[name];}
  addEventListener(name,callback){(this.events[name]??=[]).push(callback);}
  click(){const event={prevented:false,stopped:false,preventDefault(){this.prevented=true;},stopPropagation(){this.stopped=true;}};for(const callback of this.events.click||[])callback(event);return event;}
  matches(selector){return selector[0]==='#'?this.id===selector.slice(1):selector[0]==='.'?this.className.split(/\s+/).includes(selector.slice(1)):selector==='[data-step]'?!!this.dataset.step:this.tagName===selector.toUpperCase();}
  querySelectorAll(selector){const nodes=this.children.flatMap(child=>[child,...child.querySelectorAll('*')]);if(selector==='*')return nodes;const parts=selector.split(' ');return nodes.filter(node=>node.matches(parts.at(-1))&&(parts.length===1||node.closest(parts[0])!==null));}
  querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
  closest(selector){return this.matches(selector)?this:this.parentNode?.closest(selector)||null;}
  scrollIntoView(){this.scrolled=true;}
}
function fixture(search='?flow=field'){
  const body=new Element('body'),doc={body,defaultView:{location:{search,pathname:'/paramedic/'}},createElement:tag=>new Element(tag),getElementById:id=>body.querySelector('#'+id),querySelector:selector=>body.querySelector(selector),querySelectorAll:selector=>body.querySelectorAll(selector)};
  const add=(parent,tag,id='',className='')=>{const el=new Element(tag,id,className);parent.append(el);return el;};
  const shell=add(body,'div','','app-shell'),sidebar=add(shell,'aside','','sidebar'),oldNav=add(sidebar,'li');oldNav.dataset.step='input';
  const main=add(shell,'main','main-content'),header=add(main,'header','','page-header');add(header,'span','workflow-step');
  const session=add(main,'section','','session-bar');add(session,'div','session-meta');
  const recovery=add(main,'section','','session-bar');add(recovery,'p','main-recovery-status');add(recovery,'textarea','main-recovery-copy').hidden=true;
  const grid=add(main,'div','','workspace-grid'),input=add(grid,'section','input-section'),review=add(grid,'section','review-section');
  add(input,'div','','transcript-heading');add(input,'textarea','transcript-input');add(input,'p','transcript-help');add(input,'button','send-transcript');add(main,'button','discard-handoff-text');
  add(review,'section','main-followup');add(review,'section','main-changes');add(review,'div','','readback-section');const actions=add(review,'div','review-confirm-actions');const confirm=add(actions,'button','confirm-button');confirm.disabled=true;
  const submit=add(grid,'section','submit-section');add(submit,'button','submit-button').disabled=true;add(submit,'div','patient-result');const links=add(submit,'div','patient-navigation');links.hidden=true;add(links,'a','patient-progress-link');add(links,'a','patient-handoff-exchange-link');add(submit,'section','main-treatment');
  const hospital=add(grid,'section','hospital-section');add(hospital,'div','','panel-heading');add(hospital,'div','candidate-list');
  const history=add(main,'section','','history-panel');add(history,'div','history-list');add(main,'footer','','page-footer');
  let back=0;const api=initStagedReview({document:doc,onBackToConversation(){back++;}});
  return {doc,api,get:id=>doc.getElementById(id),sidebar,review,submit,hospital,recovery,sourceInput:doc.getElementById('transcript-input'),confirm,back:()=>back};
}
const draft=(extra={})=>({id:'VS-A',version:2,state:'DRAFT',confirmed:false,fields:{pulse:90},...extra});
const visiblePanels=f=>['review-section','submit-section','hospital-section'].filter(id=>!f.get(id).hidden);

test('field mode shows one review panel and retains original controls in collapsed settings',()=>{
  const f=fixture();f.api.update({session:draft()});
  assert.deepEqual(visiblePanels(f),['review-section']);assert.equal(f.get('input-section').hidden,true);
  assert.equal(f.sidebar.closest('#staged-settings')?.open,false);assert.equal(f.recovery.closest('#staged-settings')?.open,false);
  assert.equal(f.get('confirm-button'),f.confirm);assert.equal(f.confirm.disabled,true);assert.equal(f.get('submit-button').disabled,true);
});
test('explicit server confirmation and patient registration advance, while manual review survives repeated renders',()=>{
  const f=fixture();f.api.update({session:draft()});assert.equal(f.api.select('submit'),false);
  f.api.update({session:draft({confirmed:true})});assert.deepEqual(visiblePanels(f),['submit-section']);
  assert.equal(f.api.select('review'),true);f.api.update({session:draft({confirmed:true})});assert.deepEqual(visiblePanels(f),['review-section']);
  f.api.update({session:draft({confirmed:true,patient_id:'P-A'}),patient:{id:'P-A'}});assert.deepEqual(visiblePanels(f),['hospital-section']);
  assert.equal(f.get('patient-progress-link').closest('#hospital-section'),f.hospital);assert.equal(f.get('patient-handoff-exchange-link').closest('#hospital-section'),f.hospital);
});
test('losing confirmation returns from registration to review and patient switches clear the selected stage',()=>{
  const f=fixture();f.api.update({session:draft({confirmed:true})});f.api.update({session:draft({version:3})});assert.deepEqual(visiblePanels(f),['review-section']);
  f.api.update({session:draft({confirmed:true})});f.api.update({session:draft({id:'VS-B'})});assert.deepEqual(visiblePanels(f),['review-section']);
});
test('pending dirty handoff failure and recovery errors block navigation without changing write-control guards',()=>{
  for(const flag of ['pending','dirty','handoffLoading','handoffFailed','recoveryError','recoveryConflict','locked','unapplied']){
    const f=fixture();f.api.update({session:draft({confirmed:true}),patient:{id:'P-A'},[flag]:true});
    assert.equal(f.api.select('input'),false,flag);assert.equal(f.api.select('review'),false,flag);assert.equal(f.back(),0,flag);
    assert.equal(f.get('patient-progress-link').click().prevented,true,flag);assert.equal(f.get('confirm-button').disabled,true,flag);
  }
});
test('urgent original text and recovery records stay outside collapsed settings and the current panel',()=>{
  for(const flag of ['pending','unapplied','recoveryError','recoveryConflict']){
    const f=fixture();f.sourceInput.value='명령 뒤의 보존 원문';f.api.update({session:draft({confirmed:true}),[flag]:true});
    const source=f.get('staged-source');assert.equal(source.open,true);assert.equal(source.parentNode.id,'main-content',flag);assert.equal(f.get('transcript-input'),f.sourceInput);assert.equal(f.sourceInput.value,'명령 뒤의 보존 원문');
    if(flag!=='unapplied')assert.equal(f.recovery.parentNode.id,'main-content',flag);
  }
});
test('back requires an explicit safe user action and stage updates perform no navigation',()=>{
  const f=fixture();f.api.update({session:draft(),canReturnToConversation:true});assert.equal(f.back(),0);assert.equal(f.api.select('input'),true);assert.equal(f.back(),1);
});
test('registered or unmatched patient contexts cannot return to another active quick record',()=>{
  for(const state of [{patient:{id:'P-A'}},{session:draft({patient_id:'P-A'})},{}]){
    const f=fixture();f.api.update({session:draft(),canReturnToConversation:true,...state,...(!Object.keys(state).length?{canReturnToConversation:false}:{})});
    assert.equal(f.api.select('input'),false);assert.equal(f.back(),0);
  }
  const origin='http://ui.synthetic',actorId='CREW-A',map=new Map(),tab=new Map([['paramedic.voice.session','VS-A'],['paramedic.voice.actor',actorId],['paramedic.voice.server',origin]]);
  const patientStorage={getItem:key=>map.get(key)??null,setItem:(key,value)=>map.set(key,value)},tabStorage={getItem:key=>tab.get(key)??null};
  writeDraftRecovery(patientStorage,{serverOrigin:origin,actorId,sessionId:'VS-A',version:2,pending:null,recoveryText:'',manualText:'',mode:'auto',recordingMode:'toggle'});
  const book=updatePatientCheckpoint(createPatientRecord(emptyBook(origin),'NOTE-A'),'NOTE-A',map.get(RECOVERY_KEY));map.set(PATIENT_RECORDS_KEY,JSON.stringify(book));
  const args={fromVoiceHandoff:true,session:draft({actor_id:actorId}),actorId,serverOrigin:origin,pageOrigin:origin,tabStorage,patientStorage};
  assert.equal(canReturnToConversation(args),true);
  for(const override of [{fromVoiceHandoff:false},{session:draft({patient_id:'P-A'})},{session:draft({id:'VS-B'})},{actorId:'CREW-B'},{serverOrigin:'http://other.synthetic'},{pageOrigin:'http://other.synthetic'},{patientStorage:{getItem(){throw Error('unavailable');}}}])assert.equal(canReturnToConversation({...args,...override}),false);
  tab.set('paramedic.voice.session','VS-OTHER');assert.equal(canReturnToConversation(args),false);tab.set('paramedic.voice.session','VS-A');
  map.set(PATIENT_RECORDS_KEY,JSON.stringify(createPatientRecord(book,'NOTE-B')));assert.equal(canReturnToConversation(args),false);
  map.set(PATIENT_RECORDS_KEY,'invalid');assert.equal(canReturnToConversation(args),false);
});
test('full view wins over either field or handoff query and does not rearrange the existing DOM',()=>{
  for(const query of ['?view=full&flow=field','?view=full&voice_handoff=1','']){
    const f=fixture(query);assert.equal(f.api.active,false);f.api.update({session:draft()});assert.equal(f.doc.getElementById('staged-settings'),null);assert.equal(f.sidebar.parentNode.className,'app-shell');
  }
  assert.equal(fixture('?voice_handoff=1').api.active,true);
});
test('entry router marker also enables stages for explicit saved patient routes',()=>{
  const f=fixture('');f.doc.documentElement={dataset:{fieldEntry:'true'}};
  const api=initStagedReview({document:f.doc});assert.equal(api.active,true);api.update({session:draft()});assert.deepEqual(visiblePanels(f),['review-section']);
});
test('markup contains one original confirmation control below review and retains existing handoff navigation implementation',()=>{
  const html=readFileSync(new URL('../../paramedic-ui/legacy.html',import.meta.url),'utf8'),app=readFileSync(new URL('../../paramedic-ui/app.js',import.meta.url),'utf8');
  assert.equal([...html.matchAll(/id="confirm-button"/g)].length,1);assert.ok(html.indexOf('id="confirm-button"')>html.indexOf('id="fields-form"'));assert.ok(html.indexOf('id="confirm-button"')<html.indexOf('id="submit-section"'));
  assert.match(app,/start_inquiry:false/);assert.match(app,/patient-handoff-exchange-link/);assert.match(app,/exchangeUrl\.search=new URLSearchParams/);
});
