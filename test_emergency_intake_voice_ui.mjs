// 합성 DOM/음성 이벤트 검사이며 실제 마이크·STT 정확도를 측정하지 않는다.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {mountVoiceIntake} from './emergency-app/static/voice-intake.mjs';
import {mountVoiceFlow} from './emergency-app/static/voice-intake-flow.mjs';
import {VoiceIntakeStore,VOICE_TTL_MS} from './emergency-app/static/voice-intake-core.mjs';
const flow='11111111-1111-4111-8111-111111111111',other='22222222-2222-4222-8222-222222222222';
const memory=()=>{const data=new Map();return {data,getItem:k=>data.get(k)??null,setItem:(k,v)=>data.set(k,String(v)),removeItem:k=>data.delete(k)};};
class El{
  constructor(tag='div'){this.tagName=tag;this.children=[];this.listeners={};this.dataset={};this.style={};this.attributes={};this.value='';this.checked=false;this.disabled=false;this.hidden=false;this.textContent='';this.classList={data:new Set(),add:n=>this.classList.data.add(n),remove:n=>this.classList.data.delete(n),contains:n=>this.classList.data.has(n)};}
  append(...nodes){this.children.push(...nodes);}appendChild(child){this.append(child);return child;}replaceChildren(...nodes){this.children=[...nodes];this.textContent='';}
  addEventListener(type,fn){(this.listeners[type]??=[]).push(fn);}
  fire(type,event={}){event.preventDefault??=()=>{event.defaultPrevented=true;};for(const fn of this.listeners[type]||[])fn(event);return event;}
  setAttribute(k,v){this.attributes[k]=String(v);}getAttribute(k){return this.attributes[k]??null;}
  querySelector(selector){const name=selector.match(/^\[name="(.*)"\]$/)?.[1];return walk(this).find(el=>el.name===name)||null;}
}
const walk=el=>el.children.flatMap(child=>[child,...walk(child)]);
const text=el=>[el.textContent,...el.children.map(text)].join(' ');
function dom(ids){
  const els=new Map(ids.map(id=>{const el=new El();el.id=id;return [id,el];}));
  return {els,document:{getElementById:id=>els.get(id)||[...els.values()].flatMap(walk).find(el=>el.id===id)||null,createElement:tag=>new El(tag),querySelectorAll:selector=>selector==='[data-voice-intake-link]'?[...els.values()].filter(el=>Object.hasOwn(el.dataset,'voiceIntakeLink')):[]}};
}
const baseRecord=()=>({version:2,flow_token:flow,created_at:100000,updated_at:100000,patient_id:null,transcript:'  당뇨는 없고 가슴 통증도 없습니다.\n',conditions:'확인 필요',areas:'확인 필요',symptoms:'확인 필요',onset:'확인 필요',confirmed:true});
function voicePage({storage=memory(),speech=false,url='http://localhost/voice-intake',now=()=>100000}={}){
  const {document,els}=dom(['transcript','status','next','start','stop','analyze','clear','confirm','voice-confirm','conditions','areas','symptoms','onset','result','alert','review-note','bound-patient']);
  const engines=[];
  class Speech{
    constructor(){engines.push(this);}start(){this.onstart?.();}stop(){this.onend?.();}abort(){this.onend?.();}
    result(parts){this.onresult?.({resultIndex:0,results:parts.map(([transcript,isFinal])=>Object.assign([{transcript}],{isFinal}))});}
  }
  const window={location:{href:url},history:{replaceState(a,b,url){window.location.href=String(url);}},...(speech?{SpeechRecognition:Speech}:{})};
  let count=0;const page=mountVoiceIntake({document,window,storage,now,makeToken:()=>++count===1?flow:other});
  return {page,storage,document,els,window,engines,$:id=>document.getElementById(id)};
}
function flowPage({storage,patient='',select=false,token=flow,now=()=>100000,restoredPayload='',restoredFlag=''}={}){
  const {document}=dom(['voice-intake-summary','common-connect-form','voice-top-link',...(select?['voice-patient-select']:[])]);
  const root=document.getElementById('voice-intake-summary');root.dataset={flowToken:token,patientId:patient,...(select?{bindSelect:'voice-patient-select'}:{})};
  document.getElementById('voice-top-link').dataset.voiceIntakeLink='';
  if(restoredPayload||restoredFlag)for(const [name,value] of [['voice_payload',restoredPayload],['voice_confirmed',restoredFlag]]){const field=new El('input');field.name=name;field.value=value;document.getElementById('common-connect-form').appendChild(field);}
  if(select)document.getElementById('voice-patient-select').value=patient;
  const page=mountVoiceFlow({document,storage,now});
  return {page,document,root,form:document.getElementById('common-connect-form'),$:id=>document.getElementById(id)};
}
test('텍스트 원문과 표시 정보는 명시 확인까지 전달하지 않으며 사용자가 수정할 수 있다',()=>{
  const p=voicePage();const raw='  당뇨가 있고 30분 전부터 가슴이 아프고 숨쉬기 힘들어요.\n';p.$('transcript').value=raw;p.$('analyze').fire('click');
  assert.equal(p.page.store.load(flow).transcript,raw);assert.equal(p.$('symptoms').value,'흉통, 호흡곤란');assert.equal(p.$('next').getAttribute('aria-disabled'),'true');
  p.$('confirm').fire('click');assert.equal(p.page.store.load(flow).confirmed,false);
  p.$('symptoms').value='환자 진술: 가슴 통증';p.$('symptoms').fire('input');p.$('voice-confirm').checked=true;p.$('confirm').fire('click');
  assert.equal(p.page.store.load(flow).symptoms,'환자 진술: 가슴 통증');assert.equal(p.page.store.load(flow).confirmed,true);
  assert.equal(p.$('next').getAttribute('aria-disabled'),'false');assert.match(p.$('next').href,/flow_token=/);
  p.$('transcript').value='직접 바꾼 원문';p.$('transcript').fire('input');assert.equal(p.page.store.load(flow).confirmed,false);assert.equal(p.$('next').getAttribute('aria-disabled'),'true');
});
test('저장소가 실패하면 확인·다음 이동을 성공으로 표시하지 않는다',()=>{
  const p=voicePage({storage:{getItem:()=>null,setItem(){throw new Error('QuotaExceeded');},removeItem(){}}});
  p.$('transcript').value='가슴이 아파요';p.$('analyze').fire('click');p.$('voice-confirm').checked=true;p.$('confirm').fire('click');
  assert.equal(p.$('next').getAttribute('aria-disabled'),'true');assert.match(p.$('status').textContent,/보관.*못|전달하지 않/);
  assert.equal(p.$('next').fire('click').defaultPrevented,true);
});
test('이전 전역 기록과 다른 흐름은 자동으로 불러오지 않는다',()=>{
  const storage=memory();storage.setItem('emergencyVoiceIntake',JSON.stringify(baseRecord()));new VoiceIntakeStore({storage,now:()=>100000}).save({...baseRecord(),flow_token:other});
  const p=voicePage({storage});assert.equal(p.$('transcript').value,'');assert.equal(p.page.token,flow);
});
test('확인 뒤 TTL이 지나면 같은 페이지에서도 다음 이동을 차단한다',()=>{
  let now=100000;const p=voicePage({now:()=>now});p.$('transcript').value='가슴이 아파요';p.$('analyze').fire('click');p.$('voice-confirm').checked=true;p.$('confirm').fire('click');
  now+=VOICE_TTL_MS;assert.equal(p.$('next').fire('click').defaultPrevented,true);assert.equal(p.$('next').getAttribute('aria-disabled'),'true');
});
test('가짜 연속 음성 결과의 interim 교체·final 재통지를 중복 문장으로 누적하지 않는다',()=>{
  const p=voicePage({speech:true});p.$('start').fire('click');const engine=p.engines[0];
  engine.result([['가슴이',false]]);engine.result([['가슴이 아파요',true]]);engine.result([['가슴이 아파요',true],['숨이 차요',false]]);engine.result([['가슴이 아파요',true],['숨이 차요',true]]);
  assert.equal(p.$('transcript').value,'가슴이 아파요 숨이 차요');assert.equal(p.$('transcript').readOnly,true);
  p.$('stop').fire('click');assert.equal(p.$('transcript').readOnly,false);assert.equal(p.$('start').disabled,false);
  p.$('analyze').fire('click');assert.equal(p.page.store.load(flow).transcript,'가슴이 아파요 숨이 차요');
});
test('초기화 후 늦게 도착한 음성 결과가 새 흐름을 되살리지 않는다',()=>{
  const p=voicePage({speech:true});p.$('start').fire('click');const old=p.engines[0];old.result([['과거 녹음',true]]);p.$('clear').fire('click');old.result([['이전 녹음 잔여',true]]);
  assert.equal(p.page.token,other);assert.equal(p.$('transcript').value,'');assert.equal(p.$('next').getAttribute('aria-disabled'),'true');
});
test('마이크 오류 후 텍스트 입력과 재시작을 허용하고 오류 전 late 결과를 무시한다',()=>{
  const p=voicePage({speech:true});p.$('start').fire('click');const old=p.engines[0];old.onerror({error:'not-allowed'});old.result([['잘못 도착한 녹음',true]]);
  assert.match(p.$('status').textContent,/not-allowed/);assert.equal(p.$('transcript').value,'');assert.equal(p.$('start').disabled,false);assert.equal(p.$('transcript').readOnly,false);
});
test('내용 확인과 환자 대상 확인은 별개이며 첫 체크 전에는 자동 바인딩하지 않는다',()=>{
  const storage=memory(),store=new VoiceIntakeStore({storage,now:()=>100000});store.save(baseRecord());const p=flowPage({storage,patient:'1',select:true});
  assert.equal(store.load(flow).patient_id,null);assert.equal(p.form.querySelector('[name="voice_payload"]').value,'');
  assert.equal(p.$('voice-patient-confirm').checked,false);p.$('voice-patient-confirm').checked=true;p.$('voice-patient-confirm').fire('change');
  assert.equal(store.load(flow).patient_id,'1');assert.equal(p.form.querySelector('[name="voice_confirmed"]').value,'yes');
  assert.equal(JSON.parse(p.form.querySelector('[name="voice_payload"]').value).transcript,baseRecord().transcript);
  p.$('voice-patient-select').value='2';p.$('voice-patient-select').fire('change');
  assert.equal(p.form.querySelector('[name="voice_payload"]').value,'');assert.doesNotMatch(text(p.root),/당뇨는 없고/);assert.match(text(p.root),/다른 환자/);
});
test('선택 환자 없는 초기 화면은 원문만 확인하며 환자 체크를 활성화하지 않는다',()=>{
  const storage=memory(),store=new VoiceIntakeStore({storage,now:()=>100000});store.save(baseRecord());const p=flowPage({storage,select:true});
  assert.equal(p.$('voice-patient-confirm').disabled,true);assert.equal(store.load(flow).patient_id,null);assert.equal(p.form.querySelector('[name="voice_payload"]').value,'');
});
test('미등록 환자 0과 다음 단계도 명시 대상 확인을 거쳐 전송한다',()=>{
  const storage=memory(),store=new VoiceIntakeStore({storage,now:()=>100000});store.save(baseRecord());const p=flowPage({storage,patient:'0'});
  p.$('voice-patient-confirm').checked=true;p.$('voice-patient-confirm').fire('change');assert.equal(store.load(flow).patient_id,'0');
  const next=flowPage({storage,patient:'0'});assert.equal(next.form.querySelector('[name="voice_confirmed"]').value,'yes');
  const otherPatient=flowPage({storage,patient:'1'});assert.equal(otherPatient.form.querySelector('[name="voice_payload"]').value,'');assert.doesNotMatch(text(otherPatient.root),/당뇨는 없고/);
});
test('확인 전 기록·다른 흐름·변조·저장 실패는 전달 payload를 만들지 않는다',()=>{
  const storage=memory(),store=new VoiceIntakeStore({storage,now:()=>100000});store.save({...baseRecord(),confirmed:false});
  const unconfirmed=flowPage({storage,patient:'1'});assert.equal(unconfirmed.$('voice-patient-confirm').disabled,true);assert.equal(unconfirmed.form.querySelector('[name="voice_payload"]').value,'');
  const newFlow=flowPage({storage,patient:'1',token:other});assert.doesNotMatch(text(newFlow.root),/당뇨는 없고/);
  store.save(baseRecord());storage.setItem=()=>{throw new Error('QuotaExceeded');};const failed=flowPage({storage,patient:'1'});failed.$('voice-patient-confirm').checked=true;failed.$('voice-patient-confirm').fire('change');
  assert.equal(failed.form.querySelector('[name="voice_payload"]').value,'');assert.equal(store.load(flow).patient_id,null);assert.match(text(failed.root),/보관|전달하지 않/);
});
test('최종 submit 때 TTL·대상·확인 상태를 다시 읽어 stale hidden을 비운다',()=>{
  const storage=memory();let now=100000;const store=new VoiceIntakeStore({storage,now:()=>now});store.save(baseRecord());store.bind(flow,'1');const p=flowPage({storage,patient:'1',now:()=>now});
  assert.equal(p.form.querySelector('[name="voice_confirmed"]').value,'yes');now+=VOICE_TTL_MS;assert.equal(p.form.fire('submit').defaultPrevented,true);assert.equal(p.form.querySelector('[name="voice_payload"]').value,'');assert.equal(p.form.querySelector('[name="voice_confirmed"]').value,'');
  assert.equal(p.form.fire('submit').defaultPrevented,true,'제외 선택 전에는 두 번째 submit도 차단');p.$('voice-exclude').fire('click');assert.notEqual(p.form.fire('submit').defaultPrevented,true);
});
for(const field of ['transcript','symptoms'])test(`확정한 ${field}를 비우는 정상 편집도 durable 확인을 해제한다`,()=>{
  const storage=memory(),store=new VoiceIntakeStore({storage,now:()=>100000});store.save(baseRecord());store.bind(flow,'1');
  const p=voicePage({storage,url:`http://localhost/voice-intake?flow_token=${flow}&patient_id=1`});
  p.$(field).value='';p.$(field).fire('input');assert.equal(store.load(flow).confirmed,false);assert.equal(store.payload(flow,'1'),null);
  const next=flowPage({storage,patient:'1'});assert.equal(next.form.querySelector('[name="voice_payload"]').value,'');
});
test('편집 전에 확정을 해제할 수 없으면 실제 입력과 새 녹음을 막는다',()=>{
  const storage=memory(),store=new VoiceIntakeStore({storage,now:()=>100000});store.save(baseRecord());store.bind(flow,'1');
  const p=voicePage({storage,speech:true,url:`http://localhost/voice-intake?flow_token=${flow}&patient_id=1`});const original=p.$('transcript').value;
  storage.setItem=()=>{throw new Error('SecurityError');};storage.removeItem=()=>{throw new Error('SecurityError');};
  assert.equal(p.$('transcript').fire('beforeinput').defaultPrevented,true);assert.equal(p.$('transcript').readOnly,true);assert.equal(p.$('transcript').value,original,'취소한 편집은 적용하지 않음');
  p.$('start').fire('click');assert.equal(p.engines.length,0);assert.equal(p.$('next').getAttribute('aria-disabled'),'true');assert.match(p.$('status').textContent,/보관.*못/);
});
test('환자 context가 다른 입력 링크는 기존 원문을 불러오지 않고 새 흐름으로 분리한다',()=>{
  const storage=memory(),store=new VoiceIntakeStore({storage,now:()=>100000});store.save(baseRecord());store.bind(flow,'1');
  const p=voicePage({storage,url:`http://localhost/voice-intake?flow_token=${flow}&patient_id=2`});
  assert.equal(p.page.token,other);assert.equal(p.$('transcript').value,'');assert.equal(store.load(flow).patient_id,'1');assert.match(p.$('status').textContent,/다른 환자/);
  const same=voicePage({storage,url:`http://localhost/voice-intake?flow_token=${flow}&patient_id=1`});assert.match(same.$('bound-patient').textContent,/환자 1/);
});
test('상단 음성 링크도 다른 환자로 전환하면 기존 flow를 재사용하지 않는다',()=>{
  const storage=memory(),store=new VoiceIntakeStore({storage,now:()=>100000});store.save(baseRecord());store.bind(flow,'1');const p=flowPage({storage,patient:'1',select:true});
  assert.match(p.$('voice-top-link').href,/patient_id=1/);p.$('voice-patient-select').value='2';p.$('voice-patient-select').fire('change');assert.equal(p.$('voice-top-link').href,'/voice-intake');
  assert.doesNotMatch(text(p.root),/당뇨는 없고/);
});
test('화면과 hidden은 같은 단일 읽기에서 만들고 바뀐 원문은 submit 후 명시 재확인한다',()=>{
  const storage=memory(),store=new VoiceIntakeStore({storage,now:()=>100000});store.save(baseRecord());const first=store.bind(flow,'1'),second={...first,transcript:'다른 페이지에서 새로 확인한 원문'};
  let reads=0;storage.getItem=()=>JSON.stringify(++reads===1?first:second);
  const p=flowPage({storage,patient:'1'});assert.equal(reads,1);assert.match(text(p.root),/당뇨는 없고/);assert.equal(JSON.parse(p.form.querySelector('[name="voice_payload"]').value).transcript,first.transcript);
  assert.equal(p.form.fire('submit').defaultPrevented,true);assert.match(text(p.root),/다른 페이지/);assert.equal(p.form.querySelector('[name="voice_payload"]').value,'');
  p.$('voice-reconfirm').fire('click');assert.equal(JSON.parse(p.form.querySelector('[name="voice_payload"]').value).transcript,second.transcript);assert.notEqual(p.form.fire('submit').defaultPrevented,true);
});
test('POST 오류 복원폼의 기존 첨부가 없어졌어도 명시 제외 전에는 재접수하지 않는다',()=>{
  const storage=memory(),old={...baseRecord(),patient_id:'1'};
  const p=flowPage({storage,patient:'1',restoredPayload:JSON.stringify(old),restoredFlag:'yes'});
  assert.equal(p.form.fire('submit').defaultPrevented,true);assert.ok(p.$('voice-exclude'));p.$('voice-exclude').fire('click');assert.notEqual(p.form.fire('submit').defaultPrevented,true);assert.equal(p.form.querySelector('[name="voice_payload"]').value,'');
});
test('처음부터 음성 정보 없는 수동 흐름은 제출을 막지 않는다',()=>{
  const p=flowPage({storage:memory(),patient:'1',token:''});assert.notEqual(p.form.fire('submit').defaultPrevented,true);
});
test('실제 HTML은 모듈 진입점과 편집·확인 제어를 빠짐없이 제공한다',()=>{
  const html=readFileSync(new URL('./emergency-app/templates/voice_intake.html',import.meta.url),'utf8');
  for(const id of ['transcript','status','next','start','stop','analyze','clear','confirm','voice-confirm','conditions','areas','symptoms','onset','result','alert','review-note','bound-patient'])assert.equal((html.match(new RegExp(`id="${id}"`,'g'))||[]).length,1,id);
  assert.match(html,/<script type="module" src="\/assets\/voice-intake.mjs"><\/script>/);assert.doesNotMatch(html,/#result\s*\{display:none/);
});
test('표시값과 원문의 HTML은 실행 가능한 마크업으로 넣지 않는다',()=>{
  const storage=memory();const store=new VoiceIntakeStore({storage,now:()=>100000});store.save({...baseRecord(),transcript:'<img src=x onerror=alert(1)>',symptoms:'<script>bad()</script>'});store.bind(flow,'1');
  const p=flowPage({storage,patient:'1'});assert.match(text(p.root),/<img/);assert.equal(walk(p.root).some(el=>el.tagName==='script'||el.tagName==='img'),false);
});
