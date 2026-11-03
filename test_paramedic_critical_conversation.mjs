import test from 'node:test';
import assert from 'node:assert/strict';
import {CriticalConversation,classifyConversationReply} from './paramedic-ui/critical-conversation.mjs';

// Injected audio/context only: no microphone, TTS playback, browser, storage or server.
const clone=value=>JSON.parse(JSON.stringify(value));
const initialContext=()=>({patientEpoch:1,actorId:'SYNTHETIC-CREW',sessionId:'VS-SYNTHETIC',version:2,state:'DRAFT',patientId:null});
const item=(id='pulse',extra={})=>({id,label:id==='pulse'?'맥박':'혈압',text:id==='pulse'?'맥박 분당 90회':'수축기 120, 이완기 80',signature:'synthetic-'+id,confirmable:true,...extra});
const answer=text=>({text,finals:[text]});
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
async function until(predicate){for(let i=0;i<100;i++){if(predicate())return;await Promise.resolve();}assert.fail('Expected asynchronous boundary did not occur');}
function fixture({items=[item()],replies=['맞아요','다음 단계'],capabilities={tts:true,browser:true},onState}={}) {
  const h={context:initialContext(),items:clone(items),replies:[...replies],events:[],states:[],acknowledged:[],editors:[],navigations:[]};
  let speaking=false,listening=false;
  h.audio={
    capabilities:()=>capabilities,
    async speak(text){assert.equal(listening,false);assert.equal(speaking,false);speaking=true;h.events.push({kind:'speak',text});try{await h.speakHook?.(text);}finally{speaking=false;}},
    async listen(){assert.equal(speaking,false);assert.equal(listening,false);listening=true;h.events.push({kind:'listen'});try{
      if(h.listenHook)return await h.listenHook();
      const next=h.replies.shift();if(next instanceof Error)throw next;
      return typeof next==='string'?answer(next):next;
    }finally{listening=false;}},
    abort(reason){h.events.push({kind:'abort',reason});h.abortHook?.(reason);},
    dispose(){h.events.push({kind:'dispose'});}
  };
  h.controller=new CriticalConversation({
    getContext:()=>clone(h.context),getPending:()=>clone(h.items),audio:h.audio,
    async acknowledge(target,context){
      h.acknowledged.push({item:clone(target),context:clone(context),active:h.controller.active});
      if(h.ackHook)return await h.ackHook(target,context);
      assert.equal(h.items[0].signature,target.signature);h.items.shift();return true;
    },
    openEditor(target,context){h.editors.push({item:target,context,active:h.controller.active});h.editorHook?.(target,context);},
    goNext(context){h.navigations.push({context,active:h.controller.active});return h.nextHook?.(context);},
    onState(state){h.states.push(state);onState?.(state,h);}
  });
  return h;
}

test('two exact replies confirm one item each after TTS and only explicit next navigates',async()=>{
  const h=fixture({items:[item('blood_pressure'),item()],replies:['맞아요','네 맞아요','다음 단계']});
  const state=await h.controller.start();
  assert.deepEqual(h.acknowledged.map(row=>row.item.id),['blood_pressure','pulse']);
  assert(h.acknowledged.every(row=>row.active===true));
  assert.equal(h.navigations.length,1);assert.equal(h.navigations[0].active,false);
  assert.equal(h.editors.length,0);assert.equal(state.phase,'navigating');assert.equal(state.active,false);
  assert.deepEqual(h.events.filter(e=>['speak','listen'].includes(e.kind)).map(e=>e.kind),['speak','listen','speak','listen','speak','listen']);
  assert.equal(h.states.findLast(s=>s.phase==='navigating').progress.confirmed,2);
  const navIndex=h.events.findLastIndex(e=>e.kind==='abort');
  assert.equal(h.events.slice(navIndex+1).length,0);
});

test('a pending TTS promise never opens the microphone',async()=>{
  const h=fixture(),gate=deferred();h.speakHook=()=>gate.promise;
  const run=h.controller.start();await until(()=>h.events.some(e=>e.kind==='speak'));
  assert.equal(h.events.filter(e=>e.kind==='listen').length,0);assert.equal(h.acknowledged.length,0);
  gate.resolve();await run;assert.equal(h.acknowledged.length,1);
});

test('completed listen is required; interim-like text without finals cannot acknowledge',async()=>{
  const h=fixture({replies:[{text:'맞아요',finals:[]}]});await h.controller.start();
  assert.equal(h.acknowledged.length,0);assert.equal(h.controller.state.errorCode,'INVALID_RESPONSE');
});

test('text/finals disagreement or split conflicting finals cannot confirm',async()=>{
  for(const value of [{text:'맞아요',finals:['아니요']},{text:'맞아요 아니요',finals:['맞아요','아니요']},{text:'맞아요',finals:['맞아요',null]}]){
    const h=fixture({replies:[value]});await h.controller.start();assert.equal(h.acknowledged.length,0);assert.equal(h.navigations.length,0);assert.equal(h.controller.active,false);
  }
});

test('unknown reply pauses and only explicit start resumes without losing current item',async()=>{
  const h=fixture({replies:['맞아요 다음 단계','맞아요','다음 단계']});await h.controller.start();
  assert.equal(h.controller.state.phase,'paused');assert.equal(h.controller.state.heard,'맞아요 다음 단계');
  assert.equal(h.acknowledged.length,0);assert.equal(h.items.length,1);
  const listens=h.events.filter(e=>e.kind==='listen').length;await Promise.resolve();assert.equal(h.events.filter(e=>e.kind==='listen').length,listens);
  await h.controller.start();assert.equal(h.acknowledged.length,1);assert.equal(h.navigations.length,1);
});

test('repeat rereads the same item without acknowledging or skipping it',async()=>{
  const h=fixture({replies:['다시 읽어줘','맞아요','다음 단계']});await h.controller.start();
  const reads=h.events.filter(e=>e.kind==='speak');assert.equal(reads.length,3);assert(reads[0].text.startsWith(item().text));assert(reads[0].text.includes('맞으면 맞아요'));
  assert.equal(reads[1].text,`${item().text}. 맞나요?`);assert.equal(h.acknowledged.length,1);
});

test('correction reply releases controller lock before opening exactly the same item editor',async()=>{
  const h=fixture({replies:['수정할게요']});await h.controller.start();
  assert.equal(h.controller.state.phase,'needs_correction');assert.equal(h.acknowledged.length,0);assert.equal(h.navigations.length,0);
  assert.equal(h.editors.length,1);assert.equal(h.editors[0].active,false);assert.equal(h.editors[0].item.id,'pulse');
});

test('unconfirmable item opens its editor without listening, acknowledgement or navigation',async()=>{
  const h=fixture({items:[item('pulse',{confirmable:false})]});await h.controller.start();
  assert.equal(h.events.filter(e=>['speak','listen'].includes(e.kind)).length,0);assert.equal(h.acknowledged.length,0);assert.equal(h.editors.length,1);assert.equal(h.editors[0].active,false);
});

test('all-confirmed completion still needs explicit next; yes alone remains on screen',async()=>{
  const h=fixture({items:[],replies:['네']});await h.controller.start();
  assert.equal(h.acknowledged.length,0);assert.equal(h.navigations.length,0);assert.equal(h.controller.state.phase,'paused');assert.equal(h.controller.state.stage,'next');
});

test('repeat of completion prompt and a later explicit next cause one navigation only',async()=>{
  const h=fixture({items:[],replies:['다시 읽어주세요','상세 검토로 이동']});await h.controller.start();
  assert.equal(h.navigations.length,1);const reads=h.events.filter(e=>e.kind==='speak');assert.equal(reads.length,2);assert.equal(reads[0].text,reads[1].text);
});

test('spoken stop at item and next stage never confirms or navigates',async()=>{
  for(const items of [[item()],[]]){const h=fixture({items,replies:['그만']});await h.controller.start();assert.equal(h.controller.state.phase,'stopped');assert.equal(h.acknowledged.length,0);assert.equal(h.navigations.length,0);}
});

test('stop invalidates delayed TTS, and a late callback cannot open the microphone',async()=>{
  const h=fixture(),gate=deferred();h.speakHook=()=>gate.promise;h.abortHook=()=>gate.reject(Object.assign(Error('aborted'),{code:'ABORTED'}));
  const run=h.controller.start();await until(()=>h.events.some(e=>e.kind==='speak'));h.controller.stop();gate.resolve();await run;
  assert.equal(h.controller.state.phase,'stopped');assert.equal(h.controller.active,false);assert.equal(h.events.filter(e=>e.kind==='listen').length,0);assert.equal(h.acknowledged.length,0);
});

test('stop invalidates delayed listen even when its old promise resolves with an exact yes',async()=>{
  const h=fixture(),gate=deferred();h.listenHook=()=>gate.promise;
  const run=h.controller.start();await until(()=>h.events.some(e=>e.kind==='listen'));h.controller.stop();gate.resolve(answer('맞아요'));await run;
  assert.equal(h.controller.state.phase,'stopped');assert.equal(h.acknowledged.length,0);assert.equal(h.navigations.length,0);
});

for(const [key,value] of Object.entries({patientEpoch:2,actorId:'OTHER-CREW',sessionId:'VS-OTHER',version:3,state:'SUBMITTED',patientId:'P-REGISTERED'})){
  test(`context change during TTS (${key}) invalidates before listening`,async()=>{
    const h=fixture(),gate=deferred();h.speakHook=()=>gate.promise;
    const run=h.controller.start();await until(()=>h.events.some(e=>e.kind==='speak'));h.context[key]=value;gate.resolve();await run;
    assert.equal(h.acknowledged.length,0);assert.equal(h.events.filter(e=>e.kind==='listen').length,0);assert.equal(h.controller.state.phase,'paused');
  });
}

test('item signature change or reordered pending during listen cannot be acknowledged',async()=>{
  for(const mutate of [h=>{h.items[0].signature='changed';},h=>{h.items.unshift(item('blood_pressure'));}]){
    const h=fixture(),gate=deferred();h.listenHook=()=>gate.promise;
    const run=h.controller.start();await until(()=>h.events.some(e=>e.kind==='listen'));mutate(h);gate.resolve(answer('맞아요'));await run;
    assert.equal(h.acknowledged.length,0);assert.equal(h.controller.state.errorCode,'STALE_ITEM');
  }
});

test('fresh-sync refusal or changed context after acknowledgement never continues',async()=>{
  for(const kind of ['refused','version']){
    const h=fixture();h.ackHook=async()=>{if(kind==='version'){h.items.shift();h.context.version++;return true;}return false;};
    await h.controller.start();assert.equal(h.acknowledged.length,1);assert.equal(h.navigations.length,0);assert.equal(h.events.filter(e=>e.kind==='listen').length,1);assert.equal(h.controller.state.phase,'paused');
  }
});

test('an affirmative callback without removing pending cannot repeat forever',async()=>{
  const h=fixture();h.ackHook=async()=>true;await h.controller.start();
  assert.equal(h.acknowledged.length,1);assert.equal(h.controller.state.errorCode,'CONFIRMATION_NOT_APPLIED');assert.equal(h.navigations.length,0);
});

test('other pending signatures cannot change silently while acknowledgement resolves',async()=>{
  const h=fixture({items:[item('blood_pressure'),item()]});h.ackHook=async()=>{h.items.shift();h.items[0].signature='mutated';return true;};await h.controller.start();
  assert.equal(h.controller.state.errorCode,'STALE_ITEM');assert.equal(h.acknowledged.length,1);assert.equal(h.events.filter(e=>e.kind==='listen').length,1);
});

test('TTS and recognition errors or silence pause without acknowledgement/restart',async()=>{
  for(const [boundary,code] of [['speak','TTS_ERROR'],['listen','RECOGNITION_ERROR'],['listen','RECOGNITION_TIMEOUT'],['listen','EMPTY']]){
    const h=fixture();h[boundary+'Hook']=async()=>{throw Object.assign(Error('synthetic audio failure'),{code});};await h.controller.start();
    assert.equal(h.controller.state.phase,'paused');assert.equal(h.controller.state.errorCode,code);assert.equal(h.acknowledged.length,0);assert.equal(h.navigations.length,0);assert.equal(h.events.filter(e=>e.kind==='listen').length,boundary==='speak'?0:1);
  }
});

test('unsupported audio, invalid identity and duplicate pending all fail before IO',async()=>{
  const bads=[fixture({capabilities:{tts:false,browser:true}}),fixture({capabilities:{tts:true,browser:false}}),fixture({items:[item(),item()]})];
  const invalid=fixture();invalid.context.actorId='';bads.push(invalid);
  for(const h of bads){await h.controller.start();assert.equal(h.events.filter(e=>['speak','listen'].includes(e.kind)).length,0);assert.equal(h.acknowledged.length,0);assert.equal(h.controller.state.phase,'paused');}
});

test('a second start while active keeps the same run and does not overlap audio',async()=>{
  const h=fixture(),gate=deferred();h.speakHook=()=>gate.promise;
  const run=h.controller.start();await until(()=>h.events.some(e=>e.kind==='speak'));const result=await h.controller.start();
  assert.equal(result.active,true);assert.equal(h.events.filter(e=>e.kind==='speak').length,1);gate.resolve();await run;assert.equal(h.acknowledged.length,1);
});

test('stop from a state subscriber prevents the immediately following audio or callback',async()=>{
  for(const phase of ['speaking','listening','confirming','navigating']){
    const h=fixture({onState:(state,owner)=>{if(state.phase===phase)owner.controller.stop();}});await h.controller.start();
    assert.equal(h.controller.state.phase,'stopped');assert.equal(h.navigations.length,0);
    if(phase!=='navigating')assert.equal(h.acknowledged.length,0);
  }
});

test('new pending before navigation and context change in inactive editor state block callbacks',async()=>{
  const h=fixture({items:[],replies:['다음 단계'],onState:(state,owner)=>{if(state.phase==='navigating')owner.items.push(item());}});
  await h.controller.start();assert.equal(h.navigations.length,0);assert.equal(h.controller.state.errorCode,'PENDING_CHANGED');
  const edit=fixture({replies:['아니요'],onState:(state,owner)=>{if(state.phase==='needs_correction')owner.context.actorId='OTHER';}});
  await edit.controller.start();assert.equal(edit.editors.length,0);assert.equal(edit.controller.state.errorCode,'STALE_CONTEXT');
});

test('copies and throwing rendering callbacks cannot mutate or abort workflow state',async()=>{
  const h=fixture({onState:state=>{state.progress.confirmed=999;if(state.item)state.item.signature='wrong';throw Error('paint');}});
  const before=h.controller.state;before.progress.total=9;assert.equal(h.controller.state.progress.total,0);
  await h.controller.start();assert.equal(h.acknowledged.length,1);assert.equal(h.navigations.length,1);assert.equal(h.controller.state.progress.confirmed,1);
});

test('navigation failure remains inactive and never starts more audio',async()=>{
  const h=fixture({items:[],replies:['계속']});h.nextHook=()=>{throw Error('synthetic navigation failure');};await h.controller.start();
  assert.equal(h.navigations.length,1);assert.equal(h.controller.active,false);assert.equal(h.controller.state.phase,'paused');assert.equal(h.events.filter(e=>e.kind==='listen').length,1);
});

test('an explicit false navigation callback pauses instead of claiming a completed move',async()=>{
  const h=fixture({items:[],replies:['다음 단계']});
  h.nextHook=async()=>false;
  await h.controller.start();assert.equal(h.controller.state.phase,'paused');assert.equal(h.controller.state.errorCode,'NAVIGATION_FAILED');
  assert.equal(h.controller.active,false);assert.equal(h.events.filter(e=>e.kind==='listen').length,1);
});

test('reply vocabulary is exact, stage-specific, and rejects quotes, negation and extra commands',()=>{
  for(const phrase of ['맞아요','맞습니다','네','예','네 맞아요','확인했습니다',' 맞아요. '])assert.equal(classifyConversationReply(phrase),'confirm');
  for(const phrase of ['아니요','틀렸어요','수정할게요'])assert.equal(classifyConversationReply(phrase),'correct');
  for(const phrase of ['다시 읽어줘','다시 읽어주세요','다시 듣기'])assert.equal(classifyConversationReply(phrase),'repeat');
  for(const phrase of ['중지','중단','그만','여기까지','멈춰'])assert.equal(classifyConversationReply(phrase),'stop');
  for(const phrase of ['다음 단계','다음 단계로','계속','상세 검토로 이동']){assert.equal(classifyConversationReply(phrase),'unknown');assert.equal(classifyConversationReply(phrase,'next'),'next');}
  for(const phrase of ['맞아요','네','예','네 맞아요'])assert.equal(classifyConversationReply(phrase,'next'),'unknown');
  for(const phrase of ['맞아요 아니요','맞아요 다음 단계','“맞아요”','"네"','맞아요?','맞지 않아요','아니요 맞아요','다음 단계 취소','현재 말은 맞아요입니다','다시 읽어줘 그리고 다음 단계','',null,12]){
    assert.equal(classifyConversationReply(phrase),'unknown');assert.equal(classifyConversationReply(phrase,'next'),'unknown');
  }
});
