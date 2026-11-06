import {normalizeSelection,recordKey,buildRoleURL,deriveStages,SnapshotPoller} from './flow-console-state.mjs';

const $=id=>document.getElementById(id),roles=['patient','voice','hospital'];
const labels={DRAFT:'기록 중',WAITING:'문의 대기',CONFIRMED:'목적지 확정',IN_TRANSIT:'이송 중',ARRIVED:'도착',COMPLETED:'완료',CANCELLED:'취소',PENDING:'대기',CHECKING:'확인 중',ACCEPTED:'수용',DECLINED:'불가',EXPIRED:'만료'};
const validId=value=>typeof value==='string'&&/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/.test(value);
const clock=epoch=>Number.isFinite(epoch)?new Intl.DateTimeFormat('ko-KR',{timeZone:'Asia/Seoul',hourCycle:'h23',hour:'2-digit',minute:'2-digit',second:'2-digit'}).format(epoch*1000):'—';
const el=(tag,text,className)=>{const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(className)node.className=className;return node;};
const frames=Object.fromEntries(roles.map(role=>[role,{element:$(role+'-frame'),key:null,document:null,dirty:new Map(),before:new WeakMap(),submitted:null,observer:null}]));
let selection={kind:'new',id:null},hospitalId=null,latest=null,initialized=false,focused=null,freshVoiceRequested=false;
let runtime=null,runtimeAt=0,runtimeFailed=false,selectionNotice='',feedSignatures=new Map();
const params=new URLSearchParams(location.search);
try{if(params.has('pid'))selection=normalizeSelection({kind:'patient',id:params.get('pid')});else if(params.has('sid'))selection=normalizeSelection({kind:'draft',id:params.get('sid')});if(params.has('hid')){if(!validId(params.get('hid')))throw Error('병원 주소를 확인하세요.');hospitalId=params.get('hid');}}catch(error){notice(error.message,true);}

function notice(message='',error=false){$('console-notice').hidden=!message;$('console-notice').textContent=message;$('console-notice').classList.toggle('is-error',error);}
function valueOf(input){return input.type==='checkbox'||input.type==='radio'?String(input.checked):input.value;}
function visible(node){return !!node&&!node.hidden&&!node.closest('[hidden]')&&node.getClientRects().length>0;}
function versionToken(doc){return (doc.getElementById('quick-session')||doc.getElementById('session-meta'))?.textContent||'';}
function activeMicrophone(doc){return !!doc.querySelector('[id="quick-record"][aria-pressed="true"],[id="followup-record"][aria-pressed="true"],[id="record-button"][aria-pressed="true"],[id="main-answer-record"][aria-pressed="true"]')||['checking','speaking','listening','confirming','navigating'].includes(doc.getElementById('critical-conversation-status')?.dataset.phase);}
function frameBlock(role){
  const entry=frames[role],doc=entry.document;if(!doc)return '';
  if(activeMicrophone(doc))return '음성 입력을 먼저 마쳐 주세요.';
  if(/정보 정리 중|저장 결과를 확인/.test(doc.getElementById('quick-state')?.textContent||''))return '입력 저장이 끝날 때까지 기다려 주세요.';
  if(doc.getElementById('refresh-session')?.disabled&&/버전/.test(doc.getElementById('session-meta')?.textContent||'')&&!entry.dirty.size)return '현재 작업이 끝날 때까지 기다려 주세요.';
  for(const id of ['quick-retry','retry-request','retry-send','discard-message'])if(visible(doc.getElementById(id)))return '결과가 미확인인 요청을 먼저 확인해 주세요.';
  for(const [input,before] of entry.dirty){if(!input.isConnected||!visible(input)||valueOf(input)===before)entry.dirty.delete(input);}
  return entry.dirty.size?'작성 중인 내용을 먼저 저장해 주세요.':'';
}
function mayChange(except=null){for(const role of roles){if(role===except)continue;const message=frameBlock(role);if(message){selectionNotice=`${{patient:'환자',voice:'구급대',hospital:'병원'}[role]} · ${message}`;notice(selectionNotice);return false;}}return true;}
function overlay(role,message,error=false){const box=$(role+'-empty');box.replaceChildren(el('span',message));box.hidden=!message;box.classList.toggle('is-error',error);}

// Only visual chrome is compacted. Errors, warnings, consent and action controls remain.
const compactCSS={
  patient:`body{background:#fff!important}body>header.topbar,body>footer{display:none!important}main{max-width:none!important;padding:12px!important}.landing{box-shadow:none!important;margin:0!important}.landing>.icon,.landing>h1,.landing>p:first-of-type{display:none!important}.workspace-heading h1{font-size:17px!important}.card{padding:12px!important;box-shadow:none!important}h2{font-size:15px!important}.workspace-grid{display:block!important}.conversation{margin-top:12px!important}.server-settings{margin-top:18px!important}#home-screen .emergency-button{font-size:15px!important}.small{line-height:1.55}`,
  voice:`body{background:#fff!important}.qv-app{max-width:none!important;padding:10px!important;min-height:100vh!important}.qv-header{padding:0 0 10px!important}.qv-header h1{font-size:17px!important}.qv-thread{padding:8px 0!important}.qv-summary{margin:0 0 10px!important}.qv-patients{margin-top:8px!important}.qv-critical{margin-top:10px!important}.app-layout{display:block!important}.sidebar{display:none!important}.main-content{padding:12px!important;max-width:none!important}.page-header h1{font-size:20px!important}.page-description,.eyebrow,.demo-banner,.session-bar[aria-label="같은 탭의 기록 복구"],.panel .section-description,.main-content>footer,.page-footer{display:none!important}.workspace-grid{display:block!important}.panel{padding:12px!important;margin-bottom:12px!important}.field-grid{grid-template-columns:1fr 1fr!important}.field-flow .stage-navigation{position:static!important}`,
  hospital:`body{background:#fff!important}body>header{padding:9px 12px!important;gap:8px!important}body>header h1{display:none!important}body>header label{font-size:11px!important}body>header #server{max-width:150px!important}.connection-panel{padding:8px 12px!important}.connection-panel #serverContext,.connection-panel #localDemoNote{display:none!important}.workspace-tabs{padding:7px 10px!important;gap:5px!important}.workspace-tabs button{font-size:11px!important;padding:7px 9px!important}main{padding:10px!important;gap:10px!important}h2{font-size:14px!important}h3{font-size:13px!important}.empty{padding:16px!important}#resourceHelp,#restrictionHelp,#operationsHelp{font-size:11px!important}`
};
function childPatient(role,doc){
  try{
    const targets=[frames[role].element.contentWindow.location.href,doc.getElementById('patient-progress-link')?.href];
    for(const target of targets){if(!target)continue;const url=new URL(target,location.origin),pid=url.searchParams.get('pid');if(url.origin===location.origin&&url.pathname.startsWith('/patient/')&&validId(pid))return pid;}
  }catch{/* Unrelated/cross-origin links never become selected records. */}return null;
}
function pendingView(except=null){
  renderData({selected:null});$('case-state').textContent='조회 중';
  for(const role of roles)if(role!==except)overlay(role,'연결 중');
}
function followPatient(pid,role){
  if(selection.kind==='patient'&&selection.id===pid)return;
  if(!mayChange(role))return;
  const entry=frames[role];if(entry.document&&activeMicrophone(entry.document))return;
  entry.dirty.clear();selection=normalizeSelection({kind:'patient',id:pid});selectionNotice='';
  // The source frame just completed this registration. Keep its document intact.
  entry.key=`${recordKey(selection)}|${role==='hospital'?hospitalId||'':''}`;
  entry.boundRecord=recordKey(selection);
  pendingView(role);changeQuery();void poller.setContext({selection,hospitalId});
}
function followDraft(sid){
  if(selection.kind==='draft'&&selection.id===sid)return;
  if(!mayChange('voice'))return;
  selection=normalizeSelection({kind:'draft',id:sid});frames.voice.key=`${recordKey(selection)}|`;
  frames.voice.boundRecord=recordKey(selection);
  pendingView('voice');changeQuery();void poller.setContext({selection,hospitalId});
}
function observeFrame(role){
  const entry=frames[role],frame=entry.element;entry.observer?.disconnect();entry.dirty.clear();entry.before=new WeakMap();entry.submitted=null;
  let doc;try{doc=frame.contentDocument;if(!doc||frame.contentWindow.location.origin!==location.origin)return;}catch{return;}
  entry.document=doc;if(frame.contentWindow.location.href==='about:blank')return;
  const style=doc.createElement('style');style.dataset.flowConsole='compact';style.textContent=compactCSS[role];doc.head?.append(style);overlay(role,'');
  if(role==='voice'&&selection.kind==='patient'&&doc.getElementById('load-patient')){
    const original=doc.getElementById('load-patient'),button=doc.createElement('button');
    button.type='button';button.id='console-load-patient';button.className='button button-secondary button-small';button.textContent='환자 기록 연결';
    button.addEventListener('click',()=>{if(!original.disabled)original.click();});
    (doc.querySelector('.header-actions')||doc.querySelector('.page-header'))?.append(button);
    const sync=()=>{if(button.disabled!==original.disabled)button.disabled=original.disabled;button.hidden=(doc.getElementById('session-meta')?.textContent||'').includes(`환자 ${selection.id}`);};
    new MutationObserver(sync).observe(doc.body,{subtree:true,childList:true,characterData:true,attributes:true,attributeFilter:['disabled']});sync();
  }
  const ignored=input=>['server','server-base','server-url','handler','hospital','workflow-actor'].includes(input.id)||input.closest('.server-settings,.workflow-actor');
  doc.addEventListener('focusin',event=>{const input=event.target;if(input.matches?.('input,textarea,select')&&!ignored(input))entry.before.set(input,valueOf(input));});
  doc.addEventListener('input',event=>{const input=event.target;if(!input.matches?.('input,textarea,select')||ignored(input))return;if(!entry.dirty.has(input))entry.dirty.set(input,entry.before.get(input)??valueOf(input));if(valueOf(input)===entry.dirty.get(input))entry.dirty.delete(input);});
  doc.addEventListener('click',event=>{if(event.target.closest?.('button,input[type="submit"]'))entry.submitted={token:versionToken(doc),inputs:new Map([...entry.dirty.keys()].map(input=>[input,valueOf(input)]))};},true);
  const inspect=()=>{
    if(entry.document!==doc||frame.contentDocument!==doc||entry.boundRecord!==recordKey(selection))return;
    const submission=entry.submitted;
    if(submission&&submission.token!==versionToken(doc)){for(const [input,value] of submission.inputs)if(!input.isConnected||valueOf(input)===value)entry.dirty.delete(input);entry.submitted=null;}
    if(role==='voice'&&freshVoiceRequested&&selection.kind==='new'){
      const button=doc.getElementById('quick-new');if(button&&!button.disabled&&!activeMicrophone(doc)){freshVoiceRequested=false;button.click();}return;
    }
    const pid=childPatient(role,doc);if(pid)followPatient(pid,role);
    if(role==='voice'&&!activeMicrophone(doc)&&doc.getElementById('field-next')?.disabled===false){
      const sid=/기록 ID:\s*(VS-[A-Za-z0-9_-]+)/.exec(doc.getElementById('quick-storage-details')?.textContent||'')?.[1];
      if(sid&&validId(sid))followDraft(sid);
    }
  };
  entry.inspect=inspect;
  entry.observer=new MutationObserver(inspect);entry.observer.observe(doc.body,{childList:true,subtree:true,characterData:true,attributes:true,attributeFilter:['hidden','disabled']});
  inspect();
  if(role==='hospital')doc.getElementById('hospital')?.addEventListener('change',event=>{const hid=event.target.value;if(validId(hid)){hospitalId=hid;$('hospital-select').value=hid;entry.key=`${recordKey(selection)}|${hid}`;changeQuery();void poller.setContext({selection,hospitalId});}});
}
for(const role of roles)frames[role].element.addEventListener('load',()=>observeFrame(role));

function roleLinks(data){
  const chosen=data.selected,owner=chosen?.owner_actor_id||'CONSOLE-CREW';
  const routing=normalizeSelection({kind:selection.kind,id:selection.id});
  for(const role of roles){
    const entry=frames[role],key=`${recordKey(selection)}|${role==='hospital'?hospitalId||'':''}`;
    try{
      const url=buildRoleURL(role,{origin:location.origin,selection:routing,hospitalId,ownerActorId:owner,voiceSessionId:chosen?.voice_session_id,version:chosen?.draft?.version});
      $(role+'-open').href=url;$(role+'-open').removeAttribute('aria-disabled');
      if(entry.key===key){entry.boundRecord=recordKey(selection);overlay(role,'');continue;}
      entry.key=key;entry.boundRecord=recordKey(selection);entry.document=null;entry.observer?.disconnect();entry.dirty.clear();overlay(role,'연결 중');entry.element.src=url;
    }catch(error){if(entry.key!==key){entry.key=key;entry.boundRecord=recordKey(selection);entry.document=null;entry.observer?.disconnect();entry.dirty.clear();entry.element.src='about:blank';}$(role+'-open').setAttribute('aria-disabled','true');overlay(role,error.code==='NO_PATIENT'?'등록 후 연결':error.message);}
  }
}
function changeQuery(){const query=new URLSearchParams();if(selection.kind==='patient')query.set('pid',selection.id);if(selection.kind==='draft')query.set('sid',selection.id);if(hospitalId)query.set('hid',hospitalId);history.replaceState(null,'',location.pathname+(query.size?'?'+query:''));}
function records(data){return [{key:'new',selection:{kind:'new',id:null},label:'새 이송'},...data.patients.map(item=>({key:'patient:'+item.id,selection:{kind:'patient',id:item.id},label:`${item.age==null?'나이 미확인':item.age+'세'} · ${labels[item.state]||item.state} · ${item.id.slice(-6)}`})),...data.drafts.map(item=>({key:'draft:'+item.id,selection:{kind:'draft',id:item.id},label:`기록 · ${item.age==null?'나이 미확인':item.age+'세'} · ${item.id.slice(-6)}`}))];}
function options(select,items,key){
  const signature=JSON.stringify(items);if(select.dataset.options!==signature){const previous=select.value;select.replaceChildren(...items.map(item=>{const option=el('option',item.label);option.value=item.key;return option;}));select.dataset.options=signature;select.value=items.some(item=>item.key===previous)?previous:key;}
  if(!select.matches(':focus'))select.value=key;
}
function feed(id,items,render){
  const target=$(id),signature=JSON.stringify(items);if(feedSignatures.get(id)===signature)return;feedSignatures.set(id,signature);
  const bottom=target.scrollHeight-target.scrollTop-target.clientHeight<35,scroll=target.scrollTop;
  target.replaceChildren(...(items.length?items.map(render):[el('p',id==='chat-items'?'대화 없음':'기록 없음','empty')]));
  target.scrollTop=bottom?target.scrollHeight:scroll;
}
function renderData(data){
  const chosen=data.selected,patient=chosen?.patient,draft=chosen?.draft;
  $('case-state').textContent=labels[patient?.state]||(draft?'기록 중':'새 이송');
  $('case-summary').textContent=patient?`${patient.age==null?'나이 미확인':patient.age+'세'} · ${patient.symptoms||'증상 미확인'}`:draft?`${draft.age==null?'나이 미확인':draft.age+'세'} · ${draft.sex==='male'?'남성':draft.sex==='female'?'여성':'성별 미확인'}`:'입력 대기';
  const stages=deriveStages(chosen).stages;$('workflow-steps').replaceChildren(...stages.map((stage,index)=>{const item=el('li');item.dataset.step=stage.id;item.className='is-'+stage.status;if(stage.status==='current')item.setAttribute('aria-current','step');item.append(el('span',stage.status==='done'?'✓':String(index+1),'step-mark'),el('span',['기록','등록','문의','수용','확정','이송','도착'][index]));return item;}));
  const counts=new Map();for(const inquiry of chosen?.inquiries||[])counts.set(inquiry.state,(counts.get(inquiry.state)||0)+1);
  $('inquiry-items').hidden=!counts.size;$('inquiry-items').replaceChildren(...[...counts].map(([state,count])=>el('span',`${labels[state]||state} ${count}`)));
  const dialogue=[...(chosen?.messages||[]).map(item=>({...item,key:'message:'+item.id,time:item.created_at,who:item.sender_role==='hospital'?(item.hospital_name||'병원'):'환자·이송자',body:item.body})),...(chosen?.inquiries||[]).flatMap(item=>[...(item.question?[{key:'question:'+item.id,time:item.responded_at||item.created_at,who:item.hospital_name||'병원',body:item.question}]:[]),...(item.answer?[{key:'answer:'+item.id,time:item.responded_at||item.created_at,who:'환자·이송자',body:item.answer}]:[])])].sort((a,b)=>(a.time??0)-(b.time??0)||a.key.localeCompare(b.key));
  feed('chat-items',dialogue,item=>{const row=el('article'),meta=el('div',undefined,'item-meta');meta.append(el('strong',item.who),el('time',clock(item.time)));row.append(meta,el('div',item.body||'','item-body'));return row;});
  feed('event-items',chosen?.events||[],item=>{const row=el('article'),meta=el('div',undefined,'item-meta');meta.append(el('time',clock(item.at)),el('strong',item.summary||'업무 기록'));if(item.hospital_name)meta.append(el('span',item.hospital_name));row.append(meta);return row;});
}
function initialChoice(data){
  if(selection.kind!=='new')return selection;
  const patient=data.patients.find(item=>!['ARRIVED','COMPLETED','CANCELLED'].includes(item.state))||data.patients[0];
  if(patient)return {kind:'patient',id:patient.id};
  const draft=data.drafts.find(item=>item.age!=null||item.version>1)||data.drafts[0];
  return draft?{kind:'draft',id:draft.id}:{kind:'new',id:null};
}
async function readJSON(url,signal){
  const controller=new AbortController(),cancel=()=>controller.abort();signal?.addEventListener('abort',cancel,{once:true});if(signal?.aborted)cancel();
  const timer=setTimeout(cancel,8000);
  try{const response=await fetch(url,{method:'GET',cache:'no-store',signal:controller.signal});let data;try{data=await response.json();}catch{throw Error('서버 응답을 확인할 수 없습니다.');}if(!response.ok)throw Object.assign(Error(data.message||'서버 연결을 확인하세요.'),{code:data.error,status:response.status});return data;}
  finally{clearTimeout(timer);signal?.removeEventListener('abort',cancel);}
}
const poller=new SnapshotPoller({delay:3500,
  async fetchSnapshot(context,{signal}){
    const query=new URLSearchParams();if(context.selection.kind==='patient')query.set('pid',context.selection.id);if(context.selection.kind==='draft')query.set('sid',context.selection.id);
    const tasks=[readJSON('/api/flow-console'+(query.size?'?'+query:''),signal)];
    if(Date.now()-runtimeAt>12000)tasks.push(readJSON('/api/common-runtime',signal).catch(error=>{if(error.status===404)return readJSON('/api/team-runtime',signal);throw error;}).then(value=>{runtime=value;runtimeAt=Date.now();runtimeFailed=false;}).catch(error=>{if(error.name!=='AbortError'){runtimeFailed=true;runtimeAt=Date.now();}}));
    const [data]=await Promise.all(tasks);if(data.readonly!==true||data.scope!=='synthetic_local_observer'||!Array.isArray(data.patients)||!Array.isArray(data.drafts)||!Array.isArray(data.hospitals))throw Error('관제 자료의 형식을 확인하세요.');return data;
  },
  onSnapshot(data,context){
    latest=data;
    if(!initialized){initialized=true;selection=normalizeSelection(initialChoice(data));hospitalId=hospitalId||data.selected?.patient?.destination_hospital_id||data.hospitals[0]?.id||null;changeQuery();if(recordKey(selection)!==recordKey(context.selection)){void poller.setContext({selection,hospitalId});return;}}
    if(data.selected?.kind==='patient'&&selection.kind==='draft'){followPatient(data.selected.patient.id,'voice');return;}
    const list=records(data);if(!list.some(item=>item.key===recordKey(selection)))list.push({key:recordKey(selection),selection,label:selection.id?.slice(-6)||'새 이송'});
    options($('record-select'),list,recordKey(selection));options($('hospital-select'),data.hospitals.map(item=>({key:item.id,label:item.name.replace(/^가상\s*/,'')})),hospitalId);
    $('server-dot').dataset.state=runtimeFailed?'error':'online';$('server-status').textContent=runtimeFailed?'서버 상태 확인 필요':runtime?.mode==='voice-only'?'음성 모드':'연결됨';$('updated-at').textContent=clock(data.checked_at);$('updated-at').dateTime=new Date(data.checked_at*1000).toISOString();
    renderData(data);roleLinks(data);if(!selectionNotice)notice('');
  },
  onError(error){$('server-dot').dataset.state='offline';$('server-status').textContent='연결 끊김';notice(error.message,true);}
});
function apply(){
  if(!latest||!mayChange())return;
  const item=records(latest).find(item=>item.key===$('record-select').value);if(!item){notice('기록을 다시 선택하세요.');return;}
  const hid=$('hospital-select').value;if(!latest.hospitals.some(item=>item.id===hid)){notice('병원을 다시 선택하세요.');return;}
  const changed=recordKey(item.selection)!==recordKey(selection);selection=normalizeSelection(item.selection);hospitalId=hid;selectionNotice='';notice('');
  if(selection.kind==='new')freshVoiceRequested=true;
  if(changed)pendingView();changeQuery();void poller.setContext({selection,hospitalId});
  if(!changed)roleLinks(latest);
  if(!changed&&freshVoiceRequested)frames.voice.inspect?.();
}
$('apply-context').onclick=apply;
$('refresh-console').onclick=()=>{void poller.refresh();};
for(const button of document.querySelectorAll('[data-focus]'))button.onclick=()=>{
  const role=button.dataset.focus;if(focused!==role&&roles.some(other=>other!==role&&frames[other].document&&activeMicrophone(frames[other].document))){notice('음성 입력을 먼저 마쳐 주세요.');return;}
  focused=focused===role?null:role;document.body.classList.toggle('has-focus',!!focused);for(const name of roles)$(name+'-panel').classList.toggle('is-focused',focused===name);for(const control of document.querySelectorAll('[data-focus]'))control.setAttribute('aria-expanded',String(control.dataset.focus===focused));
};
for(const button of document.querySelectorAll('[data-panel]'))button.onclick=()=>{const role=button.dataset.panel;if(!mayChange(role))return;for(const name of roles)$(name+'-panel').classList.toggle('is-active',name===role);for(const control of document.querySelectorAll('[data-panel]')){const active=control===button;control.setAttribute('aria-selected',String(active));control.tabIndex=active?0:-1;}};
addEventListener('keydown',event=>{if(event.key==='Escape'&&focused)document.querySelector(`[data-focus="${focused}"]`)?.click();});
addEventListener('visibilitychange',()=>{void poller.setVisible(!document.hidden);});
addEventListener('pagehide',()=>{poller.stop();for(const role of roles)frames[role].observer?.disconnect();});
void poller.start({selection,hospitalId});
