import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {setImmediate as nextTurn} from 'node:timers/promises';

// Actual page + actual VoiceCapture; only browser DOM/recognition/storage and
// downloads are fake. These synthetic fixtures measure no real STT accuracy.
const pageURL=new URL('../../paramedic-ui/command-practice.js',import.meta.url);
const html=fs.readFileSync(new URL('../../paramedic-ui/command-practice.html',import.meta.url),'utf8');
let source=fs.readFileSync(pageURL,'utf8');
const bindings={};
for(const match of source.matchAll(/^import\s*\{([^}]+)\}\s*from\s*['"]([^'"]+)['"];?\r?$/gm)){
  const module=await import(new URL(match[2],pageURL));
  for(const name of match[1].split(',').map(item=>item.trim()).filter(Boolean)){
    assert.ok(Object.hasOwn(module,name),'Actual module binding '+name);bindings[name]=module[name];
  }
  source=source.replace(match[0],'');
}
assert.doesNotMatch(source,/^import\s/m);
assert.equal(Object.hasOwn(bindings,'VoiceClient'),false);
const copy=value=>JSON.parse(JSON.stringify(value));
const flush=async()=>{await nextTurn();await nextTurn();};

class Element{
  constructor(tag='div'){this.tagName=tag.toUpperCase();this.textContent='';this.children=[];this.value='';this.hidden=false;this.disabled=false;this.checked=false;this.readOnly=false;this.attributes={};this.files=[];}
  append(...items){this.children.push(...items);}
  replaceChildren(...items){this.children=[...items];}
  setAttribute(name,value){this.attributes[name]=String(value);}
  focus(){this.focused=true;}
  select(){this.selected=true;}
  async emit(name){return this['on'+name]?.({target:this,currentTarget:this,preventDefault(){}});}
  async click(){return this.emit('click');}
}
function memoryStorage(){
  const values=new Map();return {values,failWrites:false,getItem:key=>values.get(key)??null,
    setItem(key,value){if(this.failWrites)throw Error('Synthetic quota error');values.set(key,String(value));},removeItem:key=>values.delete(key)};
}
function harness(t,{storage=memoryStorage()}={}){
  const elements={},events={},recognizers=[],downloads=[],blobs=new Map(),requests=[];
  let clock=Date.now(),timerId=0;const timers=new Map();
  class TestDate extends Date{static now(){return clock;}}
  const schedule=(callback,delay=0)=>{const id=++timerId;timers.set(id,{at:clock+delay,callback});return id;};
  for(const tag of html.matchAll(/<([a-z][a-z0-9-]*)\b([^>]*\bid="([^"]+)"[^>]*)>/gi)){
    const el=new Element(tag[1]);elements[tag[3]]=el;
    for(const name of ['hidden','checked','readonly','disabled'])if(new RegExp('\\s'+name+'(?:[\\s=>]|$)').test(tag[2]))el[name==='readonly'?'readOnly':name]=true;
    for(const attribute of tag[2].matchAll(/\b([\w-]+)="([^"]*)"/g))if(['value','type'].includes(attribute[1]))el[attribute[1]]=attribute[2];
  }
  for(const select of html.matchAll(/<select\b[^>]*\bid="([^"]+)"[^>]*>([\s\S]*?)<\/select>/g)){
    const options=[...select[2].matchAll(/<option\b([^>]*)>([\s\S]*?)<\/option>/g)];
    const first=options.find(option=>/\sselected(?:[\s=>]|$)/.test(option[1]))||options[0];
    if(first)elements[select[1]].value=first[1].match(/\bvalue="([^"]*)"/)?.[1]??first[2].trim();
  }
  class Recognition{
    constructor(){this.results=[];this.stops=0;this.aborts=0;recognizers.push(this);}
    start(){queueMicrotask(()=>this.onstart?.());}
    stop(){this.stops++;}
    abort(){this.aborts++;}
    final(text){const result=[{transcript:text}];result.isFinal=true;const index=this.results.length;this.results.push(result);this.onresult?.({resultIndex:index,results:this.results});}
    interim(text){const result=[{transcript:text}];result.isFinal=false;this.onresult?.({resultIndex:this.results.length,results:[...this.results,result]});}
    end(){this.onend?.();}
    fail(code){this.onerror?.({error:code});}
  }
  const navigator={userAgent:'Synthetic regression fixture; no actual microphone',mediaDevices:{getUserMedia(){throw Error('Actual microphone forbidden');}}};
  const window={SpeechRecognition:Recognition,navigator,addEventListener(name,callback){(events[name]??=[]).push(callback);}};
  const oldWindow=Object.getOwnPropertyDescriptor(globalThis,'window'),oldFetch=globalThis.fetch;
  const forbiddenFetch=async(...args)=>{requests.push(args);throw Error('Practice must not fetch any API');};
  Object.defineProperty(globalThis,'window',{value:window,writable:true,configurable:true});globalThis.fetch=forbiddenFetch;
  class DownloadURL extends URL{static createObjectURL(blob){const url='blob:practice-'+blobs.size;blobs.set(url,blob);return url;}static revokeObjectURL(){}}
  const document={getElementById:id=>{assert.ok(elements[id],'Actual DOM ID '+id);return elements[id];},createElement:tag=>{
    const el=new Element(tag);if(tag==='a')el.click=()=>{assert.ok(blobs.has(el.href));downloads.push({filename:el.download,blob:blobs.get(el.href)});};return el;
  }};
  const context=vm.createContext({...bindings,document,window,navigator,localStorage:storage,fetch:forbiddenFetch,
    URL:DownloadURL,Blob,Date:TestDate,queueMicrotask,console,setInterval:callback=>callback,clearInterval(){},setTimeout:schedule,clearTimeout:id=>timers.delete(id)});
  vm.runInContext(source,context,{filename:'command-practice.js'});
  const read=expression=>vm.runInContext(expression,context),el=id=>elements['practice-'+id];
  const h={el,read,storage,recognizers,downloads,requests,data:()=>copy(read('dataset')),last:()=>copy(read('dataset.attempts.at(-1)')),
    async click(id){assert.equal(el(id).disabled,false,'Enabled user control '+id);await el(id).click();},
    async input(id,value){assert.equal(el(id).disabled,false,'Enabled input '+id);el(id).value=value;await el(id).emit('input');},
    async custom(reference='',expected='unlabelled'){el('custom-enabled').checked=true;await el('custom-enabled').emit('change');await h.input('reference',reference);el('expected').value=expected;},
    async start(){await h.click('record');assert.equal(read('capture.active'),true);return recognizers.at(-1);},
    async finish(rec){const pending=h.click('record');assert.equal(rec.stops,1);rec.end();await pending;await flush();},
    async simulate(text){await h.input('manual',text);await h.click('simulate');await flush();},
    async advance(ms){const target=clock+ms;let count=0;while(true){const next=[...timers].filter(([,timer])=>timer.at<=target).sort((a,b)=>a[1].at-b[1].at)[0];if(!next)break;assert.ok(++count<1000,'Bounded virtual timers');timers.delete(next[0]);clock=next[1].at;next[1].callback();await flush();}clock=target;await flush();},
    async event(name){for(const callback of events[name]||[])await callback({preventDefault(){}});await flush();},
  };
  t.after(async()=>{try{await h.event('pagehide');assert.equal(requests.length,0,'No UI or imported module API requests');}finally{globalThis.fetch=oldFetch;if(oldWindow)Object.defineProperty(globalThis,'window',oldWindow);else delete globalThis.window;}});
  return h;
}

test('button stop drains late final, preserves prefix/suffix, and performs no command action',async t=>{
  const h=harness(t);await h.custom('합성 환자입니다 병원 추천해줘 맥박 92','recommend');const rec=await h.start();
  rec.final('합성 환자입니다 병원 추천해줘');assert.equal(rec.stops,0,'Auto-stop defaults off');
  const stopping=h.click('record');rec.final('맥박 92');rec.end();await stopping;await flush();
  const item=h.last();assert.equal(item.source,'human_voice');assert.equal(item.rawText,'합성 환자입니다 병원 추천해줘 맥박 92');
  assert.deepEqual(item.fragments,['합성 환자입니다 병원 추천해줘','맥박 92']);
  assert.deepEqual([item.parserResult.action,item.parserResult.prefix,item.parserResult.suffix],['recommend','합성 환자입니다','맥박 92']);
  assert.equal(item.actualAudioSaved,false);assert.equal(item.stopReason,'button');assert.equal(h.data().attempts.length,1);
});

test('deferred auto-stop cancels on later negative context while recognition continues',async t=>{
  const h=harness(t);await h.custom('종료 하지 마','none');h.el('autostop').checked=true;const rec=await h.start();
  rec.final('종료');assert.equal(rec.stops,0);await h.advance(300);rec.interim('하지 마');await h.advance(1000);assert.equal(rec.stops,0);rec.final('하지 마');rec.end();await flush();
  assert.equal(h.last().rawText,'종료 하지 마');assert.equal(h.last().provisionalTriggerText,'종료');
  assert.equal(h.last().parserResult,null);assert.equal(h.last().expectedAction,null);assert.equal(h.last().stopReason,'natural');
  assert.equal(h.last().commandTrace.events.some(event=>event.type==='commit'),false);
});

test('immediate comparison preserves the original stop-and-drain baseline explicitly',async t=>{
  const h=harness(t);await h.custom('종료 하지 마','none');h.el('autostop').checked=true;h.el('command-policy').value='immediate';const rec=await h.start();
  rec.final('종료');assert.equal(rec.stops,1);rec.final('하지 마');rec.end();await flush();
  assert.equal(h.last().rawText,'종료 하지 마');assert.equal(h.last().commandTrace.policy,'immediate');assert.equal(h.last().stopReason,'trigger');
});

for(const [command,action]of [['녹음 종료','stop'],['병원 추천해줘','recommend'],['새 환자 시작','new_patient'],['이전 환자로 돌아가','previous_patient'],['인계 요약해줘','handoff_summary']])test(`deferred command commits once after the quiet window: ${action}`,async t=>{
  const h=harness(t);await h.custom(command,action);h.el('autostop').checked=true;const rec=await h.start();rec.final(command);
  await h.advance(799);assert.equal(rec.stops,0);await h.advance(1);assert.equal(rec.stops,1);rec.end();await flush();
  const item=h.last(),commits=item.commandTrace.events.filter(event=>event.type==='commit');assert.equal(commits.length,1);assert.equal(commits[0].action,action);assert.equal(commits[0].atMs,800);assert.equal(item.commandTrace.events.some(event=>event.type==='capture'&&event.reason==='stop_requested'),true);
});

test('observe records hypothetical early stop without losing late time context',async t=>{
  const h=harness(t);await h.custom('종료 시각과 발생 시각은 다릅니다','none');h.el('command-policy').value='observe';h.el('autostop').checked=true;const rec=await h.start();
  rec.final('종료');await h.advance(1200);assert.equal(rec.stops,0);rec.final('시각과 발생 시각은 다릅니다');rec.end();await flush();
  const item=h.last();assert.equal(item.parserResult,null);assert.equal(item.commandTrace.policy,'observe');assert.equal(item.commandTrace.events.filter(event=>event.type==='would_commit').length,1);assert.equal(item.autoStop,false);
});

test('pending timer cannot stop a later recording after abort',async t=>{
  const h=harness(t);h.el('autostop').checked=true;let rec=await h.start();rec.final('종료');await h.advance(300);await h.click('abort');await flush();
  rec=await h.start();rec.final('합성 관찰입니다');await h.advance(1000);assert.equal(rec.stops,0);await h.finish(rec);assert.equal(h.data().attempts.length,2);
});

test('a natural recognizer end waits for the pending decision before saving',async t=>{
  const h=harness(t);h.el('autostop').checked=true;const rec=await h.start();rec.final('종료');rec.end();await flush();assert.equal(h.data().attempts.length,0);
  await h.advance(800);assert.equal(h.data().attempts.length,1);assert.equal(h.last().commandTrace.events.filter(event=>event.type==='commit').length,1);assert.equal(rec.stops,0,'Recognizer had already ended; gate commit is distinct from a stop request');
});

test('observe uses the same quiet window after natural end instead of committing early',async t=>{
  const h=harness(t);h.el('command-policy').value='observe';const rec=await h.start();rec.final('종료');await h.advance(100);rec.end();await flush();assert.equal(h.data().attempts.length,0);
  await h.advance(699);assert.equal(h.data().attempts.length,0);await h.advance(1);assert.equal(h.last().commandTrace.events.find(event=>event.type==='would_commit').atMs,800);assert.equal(rec.stops,0);
});

for(const policy of ['deferred','observe'])for(const wait of ['', '5001','800.5'])test(`invalid wait is rejected before a microphone attempt: ${policy}/${wait}`,async t=>{
  const h=harness(t);h.el('command-policy').value=policy;h.el('command-wait').value=wait;await h.click('record');
  assert.equal(h.recognizers.length,0);assert.equal(h.data().attempts.length,0);assert.equal(h.read('active'),null);assert.match(h.el('error').textContent,/100~5000/);
});
test('zero wait is used only by the explicitly selected immediate comparison',async t=>{
  const h=harness(t);h.el('command-policy').value='immediate';h.el('command-wait').value='';h.el('autostop').checked=true;const rec=await h.start();rec.final('종료');assert.equal(rec.stops,1);rec.end();await flush();assert.equal(h.last().commandTrace.waitMs,0);
});

test('idle before recognition error records failure and retains preceding final',async t=>{
  const h=harness(t);const rec=await h.start();rec.final('합성 관찰 맥박 92');rec.fail('network');await flush();
  assert.equal(h.last().status,'error');assert.equal(h.last().errorCode,'network');assert.equal(h.last().rawText,'합성 관찰 맥박 92');
  assert.equal(h.data().attempts.length,1);assert.equal(h.el('counts-failed').textContent,'1');
});

test('interim-only natural end is retained as no_match rather than accepted final',async t=>{
  const h=harness(t);const rec=await h.start();rec.interim('병원 추천해줘');rec.end();await flush();
  assert.equal(h.last().status,'no_match');assert.equal(h.last().rawText,'');assert.deepEqual(h.last().fragments,[]);assert.equal(h.last().parserResult,null);
});

test('explicit abort retains known finals once and rejects saved late recognizer callbacks',async t=>{
  const h=harness(t);const rec=await h.start();rec.final('합성 관찰 혈압 120에 80');const late=rec.onresult;
  await h.click('abort');await flush();late({resultIndex:1,results:[...rec.results,Object.assign([{transcript:'새 환자 시작'}],{isFinal:true})]});await flush();
  assert.equal(h.last().status,'aborted');assert.equal(h.last().stopReason,'aborted');assert.equal(h.last().rawText,'합성 관찰 혈압 120에 80');assert.equal(h.data().attempts.length,1);
});

test('pagehide synchronously saves known finals as an interrupted attempt',async t=>{
  const h=harness(t);const rec=await h.start();rec.final('합성 관찰 호흡 정상');await h.event('pagehide');
  const item=h.last();assert.equal(item.status,'aborted');assert.equal(item.stopReason,'page_exit');assert.equal(item.rawText,'합성 관찰 호흡 정상');
  const stored=JSON.parse(h.storage.getItem(bindings.PRACTICE_KEY));assert.equal(stored.attempts[0].id,item.id);assert.equal(rec.aborts,1);
});

test('independent annotation changes intended label without editing raw or parser outcome',async t=>{
  const h=harness(t);await h.custom('합성 정답: 병원 추천해줘','recommend');await h.simulate('종료');const before=h.last();
  h.el('annotation-action').value='none';h.el('annotation-verified').checked=true;await h.input('annotation-note','합성 불일치 확인');await h.click('annotation-save');
  const after=h.last();assert.equal(after.expectedAction,null);assert.equal(after.referenceConfirmed,true);assert.equal(after.notes,'합성 불일치 확인');
  assert.equal(after.rawText,before.rawText);assert.deepEqual(after.fragments,before.fragments);assert.deepEqual(after.parserResult,before.parserResult);
  assert.equal(after.parserResult.action,'stop');assert.equal(after.source,'manual_simulation');assert.equal(h.el('counts-human').textContent,'0');
});

test('blank free reference stays unverified and whitespace manual input stays no_match',async t=>{
  const h=harness(t);await h.custom('');await h.simulate('   ');
  assert.equal(h.last().referenceText,'');assert.equal(h.last().rawText,'   ');assert.equal(h.last().status,'no_match');
  assert.equal(Object.hasOwn(h.last(),'expectedAction'),false);assert.equal(h.last().referenceConfirmed,false);
  assert.equal(h.el('verify-next').disabled,true);assert.equal(h.el('annotation-verified').disabled,true);
});

test('storage failure keeps completed raw in memory, blocks new trials, and retries same record',async t=>{
  const h=harness(t);h.storage.failWrites=true;await h.simulate('합성 실패 보존 문장');const item=h.last();
  assert.equal(h.read('storageFailed'),true);assert.equal(h.el('simulate').disabled,true);assert.equal(h.storage.getItem(bindings.PRACTICE_KEY),null);
  assert.equal(item.rawText,'합성 실패 보존 문장');h.storage.failWrites=false;await h.click('save-retry');
  const saved=JSON.parse(h.storage.getItem(bindings.PRACTICE_KEY));assert.equal(saved.attempts.length,1);assert.equal(saved.attempts[0].id,item.id);assert.equal(h.read('storageFailed'),false);
});

test('new batch requires current JSON download receipt and annotation invalidates it',async t=>{
  const h=harness(t);await h.simulate('합성 기록');assert.equal(h.el('new-batch').disabled,true);
  await h.click('json');const exported=JSON.parse(await h.downloads.at(-1).blob.text());assert.deepEqual(exported,h.data());
  h.el('export-confirmed').checked=true;await h.el('export-confirmed').emit('change');assert.equal(h.el('new-batch').disabled,false);
  await h.input('annotation-note','수정 후 재백업 필요');await h.click('annotation-save');assert.equal(h.el('new-batch').disabled,true);assert.equal(h.el('export-confirmed').checked,false);
  await h.click('json');h.el('export-confirmed').checked=true;await h.el('export-confirmed').emit('change');await h.click('new-batch');
  assert.equal(h.data().attempts.length,0);assert.equal(exported.attempts.length,1);assert.equal(JSON.parse(h.storage.getItem(bindings.PRACTICE_KEY)).attempts.length,0);
});

test('new batch storage failure restores previous records rather than clearing memory',async t=>{
  const h=harness(t);await h.simulate('합성 보존 기록');const before=h.data();await h.click('json');
  h.el('export-confirmed').checked=true;await h.el('export-confirmed').emit('change');h.storage.failWrites=true;await h.click('new-batch');
  assert.deepEqual(h.data(),before);assert.equal(h.read('storageFailed'),true);assert.deepEqual(JSON.parse(h.storage.getItem(bindings.PRACTICE_KEY)),before);
});

test('independently labelled clinical, reported and first-command fixtures keep intended answers separate',async t=>{
  const h=harness(t);
  // Literal gold labels are authored here, never derived from parser results.
  const gold=[['CPR 종료 후 자발순환 확인',null],['산소 공급 중단 시각 14시',null],['환자가 병원 추천해줘라고 말함',null],['“새 환자 시작”이라는 말',null],['종료 하지 마',null],['병원 추천해줘 라고 환자가 말함',null],['이전 환자로 돌아가 병원 추천해줘','previous_patient']];
  for(const [text,expected]of gold){await h.custom(text,expected===null?'none':expected);await h.simulate(text);assert.equal(h.last().expectedAction,expected);assert.equal(h.last().parserResult?.action??null,expected);}
  assert.equal(h.data().attempts.length,gold.length);assert.equal(h.recognizers.length,0);assert.equal(h.el('raw').readOnly,true);
});
