/* Teammate entry/registration visual language adapted from 181b99a613b943eed661ec8bc0f35bf9b71814d0.
   Common server is the only patient store. No clinical form or messages are persisted locally. */
'use strict';
const $ = id => document.getElementById(id);
const query = new URLSearchParams(location.search);
const store = {get(k) {try {return localStorage.getItem(k);} catch (_) {return null;}}, set(k,v) {try {localStorage.setItem(k,v);} catch (_) {}}};
let server = normalizeServer(query.get('server') || (location.pathname.startsWith('/patient/') ? location.origin : store.get('patient.server') || 'http://127.0.0.1:18019'));
let pid = query.get('pid') || '';
let generation = 0, patient = null, conversation = null, policies = [], polling = false, busy = false;
let eventCursor = 0, messageCursor = 0, items = new Map(), pendingMessage = null, pendingAttempted = false, candidateData = null, lastCandidateAt = 0;
const ACTIVE = new Set(['CONFIRMED','IN_TRANSIT']);
const TERMINAL = new Set(['ARRIVED','COMPLETED','CANCELLED']);
const states = {WAITING:'병원 탐색 중',CONFIRMED:'목적지 확정',IN_TRANSIT:'이송 중',ARRIVED:'병원 도착',COMPLETED:'진료·이송 종료',CANCELLED:'이송 취소',NOT_STARTED:'아직 시작하지 않음',RUNNING:'자동 문의 중',QUEUED:'문의 준비 중',PAUSED:'일시정지',AWAITING_CONFIRMATION:'수용 응답 확보 · 사람 확정 대기',EXHAUSTED:'자동 탐색 한도 종료',ERROR:'오류 · 담당자 확인 필요',MONITORING:'확정 조건 감시 중',RECHECK_REQUIRED:'현재 병원의 조건 재확인 필요',SEARCHING:'대체병원 문의 중',AWAITING_CHANGE:'대체 수용 확보 · 사람 변경 대기',AWAITING_POSITION:'최신 관측 위치 필요',PENDING:'요청 전달됨',CHECKING:'병원 확인 중',ACCEPTED:'수용 가능 응답',DECLINED:'수용 불가 응답',EXPIRED:'응답 만료',OPEN:'진행 중',CLOSED:'종료'};
const label = s => states[s] || s || '미확인';
const esc = value => String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const at = value => value ? new Date(value*1000).toLocaleTimeString('ko-KR',{hour:'2-digit',minute:'2-digit',second:'2-digit'}) : '미확인';
const resourceLabel = r => ({ed_bed:'진료 자리',special_team:'전문팀'}[r] || r);
function normalizeServer(value) {try {const u = new URL(value); if (!['http:','https:'].includes(u.protocol) || u.username || u.password || u.search || u.hash) throw Error(); return u.origin + u.pathname.replace(/\/$/,'');} catch (_) {return 'http://127.0.0.1:18019';}}
function announce(message,error=false) {const target = $(error?'error':'notice'); target.textContent=message; target.hidden=!message; if(error) $('notice').hidden=true;}
function showScreen(name) {for(const screen of ['home','intake','transport']) $(`${screen}-screen`).hidden=screen!==name;}
function setBusy(value) {
  busy=value;
  for(const control of document.querySelectorAll('#resume-form input,#resume-form button,#server-form input,#server-form button,#start-intake,#intake-back,#submit-intake,#discard-message'))control.disabled=value;
  $('home-link').setAttribute('aria-disabled',String(value));
}
function resetPatientContext() {
  patient=null;conversation=null;items.clear();eventCursor=0;messageCursor=0;pendingMessage=null;pendingAttempted=false;candidateData=null;lastCandidateAt=0;
  $('position-lat').value='';$('position-lon').value='';$('message-body').value='';$('chat-items').replaceChildren();$('inquiries').replaceChildren();
  $('patient-summary').textContent='서버에서 이송 건을 조회하는 중입니다.';$('transport-controls').hidden=true;$('cancel-form').hidden=true;
  $('emergency-warning').hidden=true;$('workflow-state').textContent='조회 중';
  for(const control of ['auto-start','auto-pause','inquiry-policy','send-message'])$(control).disabled=true;
  renderCandidates();
}
function path(suffix='') {return `/api/patients/${encodeURIComponent(pid)}${suffix}`;}
async function api(endpoint,method='GET',body) {
  let response;
  try {response=await fetch(server+endpoint,{method,cache:'no-store',headers:body===undefined?{}:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});} catch (_) {throw new Error('서버 연결 실패 · 서버 실행과 주소를 확인하세요.');}
  let result; try {result=await response.json();} catch (_) {throw new Error('서버가 JSON 응답을 반환하지 않았습니다. 연결 주소를 확인하세요.');}
  if(!response.ok) {const error=new Error(result.message || result.error || `HTTP ${response.status}`); error.code=result.error; error.status=response.status; throw error;} return result;
}
function saveRoute() {const url=new URL(location.href); url.search=''; if(pid) url.searchParams.set('pid',pid); if(server!==location.origin) url.searchParams.set('server',server); history.replaceState(null,'',url); store.set('patient.server',server); if(pid) store.set('patient.pid',pid); $('server-base').value=server;}
async function initializeServer() {
  const connectedServer=server;
  $('server-base').value=server;
  try {
    const [hospitalData,policyData]=await Promise.all([api('/api/hospitals'),api('/api/inquiry-policies')]);
    if(connectedServer!==server)return;
    const hospitals=Array.isArray(hospitalData)?hospitalData:(hospitalData.hospitals || []);
    const caps=[...new Set(hospitals.flatMap(h=>h.capabilities || []))].sort();
    $('capabilities').innerHTML=caps.length?caps.map(c=>`<label class="check"><input type="checkbox" name="capability" value="${esc(c)}"> ${esc(c)}</label>`).join(''):'등록 진료 분야가 없습니다. 미확인으로 전달합니다.';
    policies=policyData.policies || [];
    const selected=$('inquiry-policy').value;
    $('inquiry-policy').innerHTML=policies.map(p=>`<option value="${esc(p.name)}">${esc(p.label || p.name)}${p.experimental?' · 실험 정책':''}</option>`).join('');
    $('inquiry-policy').value=policies.some(p=>p.name===selected)?selected:'fixed_3';
    $('connection-status').textContent=`공통 서버 연결됨 · 병원 ${hospitals.length}곳. 등록 병원 자료를 확인해 치료 분야를 표시합니다.`;
  } catch(error) {if(connectedServer!==server)return;$('connection-status').textContent='연결 실패'; $('capabilities').textContent='진료 분야를 읽지 못했습니다. 서버 연결 설정을 확인해주세요.'; announce(error.message,true);}
}
function openPatient(id) {
  if(!/^[\w-]+$/.test(id)) {announce('서버 환자 ID에는 영문·숫자·밑줄·하이픈만 사용할 수 있습니다.',true);return;}
  generation++; pid=id;resetPatientContext();
  $('resume-pid').value=id;saveRoute();showScreen('transport');refresh(true);
}
async function refresh(includeCandidates=false) {
  if(!pid || polling)return; polling=true;const current=generation;const endpoint=path();
  try {
    const next=await api(endpoint); if(current!==generation)return; patient=next;renderPatient();
    await readConversation(current,endpoint);
    if(current!==generation)return;
    if(includeCandidates || (!TERMINAL.has(patient.state) && Date.now()-lastCandidateAt>15000)) await refreshCandidates(current,endpoint);
  } catch(error) {if(current===generation)announce(error.message,true);} finally {polling=false;}
}
async function readConversation(current=generation,endpoint=path()) {
  let more=true;
  while(more) {
    const data=await api(`${endpoint}/conversation?event_cursor=${eventCursor}&message_cursor=${messageCursor}&limit=200`);
    if(current!==generation)return;
    conversation=data;
    for(const item of data.items || [])items.set(item.id,item);
    eventCursor=data.cursors.event_id;messageCursor=data.cursors.message_id;
    renderConversation(); more=!!data.has_more;
  }
}
async function refreshCandidates(current=generation,endpoint=path()) {
  if(!patient || TERMINAL.has(patient.state)) {candidateData=null;renderCandidates();return;}
  const data=await api(endpoint+'/candidates'); if(current!==generation)return;candidateData=data;lastCandidateAt=Date.now();renderCandidates();
}
function publicDemoNotice(extra) {
  return extra?.demo_mode==='public_data_local_workflow'?'<div class="notice" data-public-demo-notice="true">실제 병원 정보 조회 시연입니다. 수용 문의·채팅은 이 서버에만 저장되며 실제 병원에 전달되지 않습니다.</div>':'';
}
function renderPatient() {
  $('patient-id').textContent=`이송 건 ${patient.id} · 개인정보는 URL에 포함하지 않습니다.`;
  $('emergency-warning').hidden=!patient.call_119;
  const consciousness={alert:'명료',impaired:'의식 저하',unresponsive:'반응 없음',unknown:'미확인'}[patient.consciousness] || '미확인';
  const breathing={normal:'정상으로 확인',abnormal:'비정상',absent:'호흡 없음',unknown:'미확인'}[patient.breathing] || '미확인';
  const source={unknown:'미확인',self:'자기신고',expert:'전문가 확인'}[patient.priority_source] || '미확인';
  $('patient-summary').innerHTML=`${publicDemoNotice(patient.extra)}<h2 data-patient-state="${esc(patient.state)}">${esc(label(patient.state))}</h2><p>${esc(patient.symptoms || '증상 미입력')}</p><dl class="summary-grid"><div><dt>환자</dt><dd>${esc(patient.name || '이름 미확인')} · ${patient.age===null?'나이 미확인':esc(patient.age)+'세'}</dd></div><div><dt>의식 · 호흡</dt><dd>${esc(consciousness)} · ${esc(breathing)}</dd></div><div><dt>우선순위</dt><dd>${esc(source)}${patient.priority_source!=='unknown'?' · '+esc(patient.priority):''}</dd></div><div><dt>필요 진료 분야</dt><dd>${esc((patient.required_capabilities || []).join(', ') || '미확인')}</dd></div><div><dt>필요 자원</dt><dd>${esc(Object.entries(patient.resource_demands || {}).map(([r,n])=>`${resourceLabel(r)} ${n}`).join(', '))}</dd></div><div><dt>현재 목적지</dt><dd>${esc(patient.destination?.name || '미확정')}</dd></div></dl>`;
  const initial=patient.inquiry_workflow || {};
  $('workflow-state').dataset.state=initial.state || 'NOT_STARTED';$('workflow-state').textContent=label(initial.state || 'NOT_STARTED');
  $('workflow-detail').textContent=initial.latest_decision?`최근 문의 판단: ${initial.latest_decision.reason} · ${at(initial.latest_decision.at)}. 정책은 수용을 확정하지 않습니다.`:'자동 문의를 시작하거나 아래 후보에 직접 문의할 수 있습니다.';
  $('auto-start').disabled=busy || patient.state!=='WAITING';$('auto-pause').disabled=busy || patient.state!=='WAITING' || !initial.enabled;
  $('inquiry-policy').disabled=busy || patient.state!=='WAITING' || !!initial.enabled;
  if(initial.policy_name && [...$('inquiry-policy').options].some(o=>o.value===initial.policy_name))$('inquiry-policy').value=initial.policy_name;
  renderInquiries();renderCandidates();
  const active=ACTIVE.has(patient.state);$('transport-controls').hidden=!active;
  $('destination-summary').textContent=patient.destination?`${patient.destination.name}${patient.eta_at?' · 예상 도착 '+at(patient.eta_at):''}${patient.needs_adjustment?' · 병원에서 조정이 필요하다고 판단했습니다.':''}`:'';
  $('depart-button').hidden=patient.state!=='CONFIRMED';$('depart-button').disabled=busy;
  const review=patient.transport_review || {};
  $('transport-review-state').textContent=label(review.state || 'NOT_STARTED');$('transport-review-state').dataset.state=review.state || 'NOT_STARTED';
  $('monitor-enable').disabled=busy || !!review.enabled;$('monitor-pause').disabled=busy || !review.enabled;
  $('transport-review-evidence').innerHTML=(review.evidence?.issues || []).map(issue=>`<p>${esc(issueText(issue))}</p>`).join('');
  const alternative=patient.transport_inquiry_workflow || {};
  $('alternative-workflow').textContent=`대체 문의: ${label(alternative.state || 'NOT_STARTED')}${alternative.state==='AWAITING_POSITION'?' · 아래에서 지금 관측한 위치를 입력해 갱신하세요.':''}`;
  for(const axis of ['lat','lon']) if(document.activeElement!==$('position-'+axis) && !$('position-'+axis).value)$('position-'+axis).value=patient[axis] ?? '';
  $('position-status').textContent=`서버의 위치 관측: ${at(patient.location_updated_at)} · 위도 ${patient.lat ?? '미확인'}, 경도 ${patient.lon ?? '미확인'}`;
  $('cancel-form').hidden=TERMINAL.has(patient.state);$('closed-state').hidden=!TERMINAL.has(patient.state);$('closed-state').textContent='이송 건이 종료되었습니다. 기록은 조회할 수 있습니다.';
  $('cancel-button').disabled=busy;
}
function issueText(i) {
  if(i.kind==='resource_shortfall')return `${resourceLabel(i.resource_type)} 조건 재확인: 총량 ${i.capacity}, 실제 점유 ${i.occupied}, 확정 수요 ${i.committed}`;
  if(i.kind==='resource_stale')return `${resourceLabel(i.resource_type)} 정보가 오래되어 재확인이 필요합니다.`;
  if(i.kind==='resource_unknown')return `${resourceLabel(i.resource_type)} 상태가 미확인으로 변경되었습니다.`;
  if(i.kind==='restriction')return `수용 제한 재확인: ${i.scope} · ${i.reason}`;
  if(i.kind==='capability_missing'||i.kind==='capability_unknown')return `진료 분야 재확인: ${(i.capabilities||[]).join(', ')}`;
  return '환자의 필요 조건이 변경되어 병원 재확인이 필요합니다.';
}
function canInquire(hid) {return patient && (patient.state==='WAITING'||(ACTIVE.has(patient.state)&&patient.needs_adjustment)) && hid!==patient.destination_hospital_id;}
function candidateTravelText(travel) {
  const seconds=travel?.duration_seconds;
  if(typeof seconds!=='number'||!Number.isFinite(seconds)||seconds<0)return '이동시간 미확인';
  const method=travel.provider==='kakao'?'도로 이동 예상':travel.provider==='synthetic'?'거리 기반 추정':'이동시간 추정';
  return `${method} 약 ${Math.ceil(seconds/60)}분`;
}
function observationTime(value) {
  return typeof value==='number'&&Number.isFinite(value)&&value>0?new Date(value*1000).toLocaleString('ko-KR',{timeZone:'Asia/Seoul'}):'미확인';
}
function publicBedDetails(snapshots) {
  const snapshot=(snapshots||[]).find(s=>s.endpoint==='nmc_rltm_beds');
  if(!snapshot)return '';
  const bed=(snapshot.data?.resources||[]).find(r=>r.resource_type==='ed_bed');
  const count=bed?.reported_available;
  const reported=typeof count==='number'&&Number.isInteger(count)&&count>=0;
  const state=snapshot.stale===true?' · 10분 이상 경과':snapshot.observation_time_unknown===true?' · 관측 시각 미확인':'';
  return `<div class="public-observation small"><strong>국립중앙의료원 공개 관측</strong><p>응급실 가용 병상: ${reported?esc(count)+'개':'미확인'}<br>관측 ${esc(observationTime(snapshot.source_observed_at))}${esc(state)}<br>수집 ${esc(observationTime(snapshot.fetched_at))}</p><p>공공 API 관측값이며, 운영 자원 총량이나 이 환자의 수용 확답이 아닙니다.</p></div>`;
}
function renderCandidates() {
  if(!candidateData) {$('candidates').textContent=TERMINAL.has(patient?.state)?'종료된 이송 건입니다.':'후보를 조회하는 중입니다.';return;}
  $('candidates').innerHTML=candidateData.candidates.length?candidateData.candidates.map(c=>`<article class="hospital-card" data-hospital-id="${esc(c.hospital_id)}"><h3>${esc(c.name)}</h3><p class="small">${esc(c.address||'주소 미확인')}<br>병원 연락처: ${esc(c.phone||'미확인')}</p><p class="small">${esc(candidateTravelText(c.travel))} · 운영 정보 갱신 ${at(c.last_updated_at)}</p>${publicBedDetails(c.public_data)}<ul>${[...(c.reasons||[]).filter(r=>!r.startsWith('이동 약 ')),...(c.checks||[])].map(r=>`<li>${esc(r)}</li>`).join('')}</ul>${c.auto_inquiry_eligible===false?'<p class="small">확인된 자원 부족으로 자동 문의 대상에서 제외됩니다. 담당자는 실제 조건을 재확인할 수 있습니다.</p>':''}<button data-action="inquire" data-hid="${esc(c.hospital_id)}" ${busy||!canInquire(c.hospital_id)?'disabled':''}>이 병원에 수용 문의</button></article>`).join(''):'조건에 맞는 후보가 없습니다. 담당자가 치료 조건과 탐색 범위를 다시 확인해야 합니다.';
  $('excluded-candidates').textContent=candidateData.excluded.length?`확인된 조건 불일치·제한으로 제외: ${candidateData.excluded.map(c=>`${c.name || c.hospital_id} (${c.reason==='NO_COMPATIBLE_CAPABILITY'?'필요 진료 분야 불일치':c.reason==='RESTRICTED'?'수용 제한':c.reason})`).join(', ')}`:'';
}
function renderInquiries() {
  const focused=document.activeElement?.classList.contains('question-answer')?{id:document.activeElement.id,start:document.activeElement.selectionStart,end:document.activeElement.selectionEnd}:null;
  const drafts=new Map([...document.querySelectorAll('.question-answer')].map(el=>[el.dataset.iid,el.value]));
  $('inquiries').innerHTML=patient.inquiries?.length?patient.inquiries.map(q=>{
    const canConfirm=q.usable && (patient.state==='WAITING'||(ACTIVE.has(patient.state)&&!!patient.needs_adjustment&&q.hospital_id!==patient.destination_hospital_id));
    const verb=patient.state==='WAITING'?'이 병원으로 목적지 확정':'이 대체병원으로 목적지 변경';
    return `<article class="inquiry-card" data-inquiry-id="${q.id}" data-state="${esc(q.state)}" data-overdue="${!!q.overdue}"><h3>${esc(q.hospital_name)}</h3><p>${esc(label(q.state))}${q.overdue?' · 미응답 강조 (거절로 간주하지 않음)':''}</p>${q.reason?`<p class="small">불가 사유: ${esc(q.reason)}</p>`:''}<p class="small">문의 ${q.attempts}회 · ${q.elapsed_seconds}초 경과${q.expires_at?' · 응답 유효 시각 '+at(q.expires_at):''}</p>${q.question?`<p>추가 질문: ${esc(q.question)}</p>${q.answer?`<p class="small">전달한 답변: ${esc(q.answer)}</p>`:`<form class="question-form" data-iid="${q.id}"><label for="answer-${q.id}">병원 질문에 답변</label><input id="answer-${q.id}" class="question-answer" data-iid="${q.id}" maxlength="2000" value="${esc(drafts.get(String(q.id))||'')}" required><button type="submit" ${busy||TERMINAL.has(patient.state)?'disabled':''}>답변 전달</button></form>`}`:''}<div class="inquiry-actions">${canConfirm?`<button data-action="${patient.state==='WAITING'?'confirm':'change'}" data-iid="${q.id}" ${busy?'disabled':''}>${verb}</button>`:''}${q.can_retry&&!TERMINAL.has(patient.state)?`<button class="secondary" data-action="retry" data-iid="${q.id}" ${busy?'disabled':''}>재문의</button>`:''}${['PENDING','CHECKING','ACCEPTED'].includes(q.state)&&!TERMINAL.has(patient.state)?`<button class="secondary" data-action="cancel-inquiry" data-iid="${q.id}" ${busy?'disabled':''}>이 문의 취소</button>`:''}</div></article>`;
  }).join(''):'아직 보낸 문의가 없습니다.';
  if(focused && $(focused.id)) {$(focused.id).focus({preventScroll:true});$(focused.id).setSelectionRange(focused.start,focused.end);}
}
function sortedItems() {return [...items.values()].sort((a,b)=>{for(let i=0;i<3;i++){const diff=(a.order_key?.[i]||0)-(b.order_key?.[i]||0);if(diff)return diff;}return String(a.id).localeCompare(String(b.id));});}
function renderConversation() {
  if(!conversation)return;
  const box=$('chat-items');const closeToBottom=box.scrollHeight-box.clientHeight-box.scrollTop<70;
  const markup=sortedItems().map(i=>`<article class="chat-entry" data-item-id="${esc(i.id)}" data-kind="${esc(i.kind)}" data-role="${esc(i.sender_role)}"><div class="meta">${esc(i.sender_name || '시스템')} · ${at(i.at)}</div><div>${esc(i.text || i.body || '')}</div></article>`).join('');
  if(box.innerHTML!==markup){box.innerHTML=markup;if(closeToBottom)box.scrollTop=box.scrollHeight;}
  const session=conversation.active_hospital_session;
  $('chat-participant').textContent=session?`시스템 · 이송자 · ${session.hospital_name}`:'시스템 · 이송자';
  const closed=!conversation.can_send;
  $('chat-status').textContent=closed?'대화가 종료되어 읽기 전용입니다.':session?'현재 참여 병원과 대화합니다.':'병원 확정 전 자유기록입니다. 이후 병원에 자동 공개되지는 않습니다.';
  if(pendingMessage)$('chat-status').textContent+=' 전송 결과가 확인되지 않은 요청이 있습니다. 재시도는 원래 대상과 내용으로 전송합니다.';
  $('message-body').disabled=busy || closed || !!pendingMessage;
  $('send-message').disabled=busy || (closed && !pendingMessage);
  $('send-message').textContent=pendingMessage?'원래 요청으로 재시도':'메시지 보내기';$('discard-message').hidden=!pendingMessage;
}
async function action(fn,message) {
  if(busy)return;setBusy(true);const current=generation;$('error').hidden=true;if(patient)renderPatient();renderConversation();
  try {await fn();if(current!==generation)return;if(message)announce(message);await refresh(true);} catch(error) {if(current===generation)announce(`${error.message}${error.status===409?' · 최신 상태를 확인한 뒤 다시 판단하세요.':''}`,true);await refresh(false);} finally {setBusy(false);if(current===generation&&patient){renderPatient();renderConversation();}}
}
async function submitMessage(event) {
  event.preventDefault();if(busy||!conversation)return;
  if(!pendingMessage){
    const body=$('message-body').value.trim();if(!body)return;
    pendingMessage={body,client_message_id:crypto.randomUUID(),hospital_session_id:conversation.active_hospital_session?.id ?? null,sender_name:'이송자'};pendingAttempted=false;
  }
  const outgoing=pendingMessage; const endpoint=path(),current=generation;
  await action(async()=>{
    // Exact retry is allowed even after closure; the server first checks an existing message ID.
    // A fresh transmission checks the observed session; mismatch never rewrites the payload.
    if(!pendingAttempted) {
      const latest=await api(endpoint+'/conversation?event_cursor=0&message_cursor=0&limit=1');
      if(current!==generation)return;
      const latestSession=latest.active_hospital_session?.id ?? null;
      if(latestSession!==outgoing.hospital_session_id)throw Object.assign(new Error('참여 병원이 변경되었습니다. 원래 요청을 취소하고 새 참여 병원을 확인한 뒤 새 메시지로 보내주세요.'),{code:'STALE_SESSION'});
    }
    pendingAttempted=true;
    const result=await api(endpoint+'/messages','POST',outgoing);if(current!==generation)return;
    items.set(result.message.id,result.message);
    pendingMessage=null;pendingAttempted=false;$('message-body').value='';renderConversation();
  },'메시지를 전달했습니다.');
}
$('start-intake').addEventListener('click',()=>{if(!busy)showScreen('intake');});
$('intake-back').addEventListener('click',()=>{if(!busy)showScreen('home');});
$('home-link').addEventListener('click',event=>{event.preventDefault();if(busy)return;generation++;pid='';resetPatientContext();saveRoute();showScreen('home');});
$('priority-source').addEventListener('change',()=>{$('priority').disabled=$('priority-source').value==='unknown';if($('priority').disabled)$('priority').value='';});
$('resume-form').addEventListener('submit',event=>{event.preventDefault();if(!busy)openPatient($('resume-pid').value.trim());});
$('server-form').addEventListener('submit',async event=>{event.preventDefault();if(busy)return;const raw=$('server-base').value.trim();if(!/^https?:\/\//.test(raw)){announce('http 또는 https 서버 주소를 입력하세요.',true);return;}generation++;server=normalizeServer(raw);resetPatientContext();saveRoute();await initializeServer();if(pid)refresh(true);});
$('intake-form').addEventListener('submit',async event=>{
  event.preventDefault();if(busy)return;const intakeGeneration=generation,intakeServer=server;setBusy(true);
  try {
    const source=$('priority-source').value,priority=$('priority').value;
    if(source==='expert'&&!priority)throw new Error('전문가 평가를 선택했다면 확인된 우선순위를 선택하세요. 미확인이면 출처를 미확인으로 두세요.');
    const payload={reporter_role:$('reporter-role').value,consent:$('consent').checked,name:$('patient-name').value.trim()||null,age:$('age').value===''?null:Number($('age').value),contact:$('contact').value.trim()||null,symptoms:$('symptoms').value.trim(),consciousness:$('consciousness').value,breathing:$('breathing').value,danger_signs:$('danger-signs').value.split(',').map(x=>x.trim()).filter(Boolean),priority:priority?Number(priority):null,priority_source:source,required_capabilities:[...document.querySelectorAll('[name=capability]:checked')].map(el=>el.value),resource_demands:{ed_bed:1,...($('special-team').checked?{special_team:1}:{})},lat:Number($('intake-lat').value),lon:Number($('intake-lon').value)};
    const created=await api('/api/patients','POST',payload);
    if(intakeGeneration!==generation||intakeServer!==server)return;
    $('intake-form').reset();$('priority').disabled=true;openPatient(created.id);announce('공통 서버에 저장했습니다. 병원 문의는 아직 시작하지 않았습니다.');
  } catch(error){if(intakeGeneration===generation&&intakeServer===server)announce(error.message,true);} finally {setBusy(false);}
});
$('refresh-all').addEventListener('click',()=>refresh(true));
$('refresh-candidates').addEventListener('click',()=>action(()=>refreshCandidates(),'후보 병원 정보를 갱신했습니다.'));
$('auto-start').addEventListener('click',()=>action(()=>api(path('/inquiry-workflow'),'POST',{enabled:true,policy_name:$('inquiry-policy').value}),'선택한 정책으로 자동 문의를 시작했습니다.'));
$('auto-pause').addEventListener('click',()=>action(()=>api(path('/inquiry-workflow'),'POST',{enabled:false}),'자동 문의를 일시정지했습니다.'));
$('candidates').addEventListener('click',event=>{const button=event.target.closest('[data-action=inquire]');if(button)action(()=>api(path('/inquiries'),'POST',{hospital_ids:[button.dataset.hid]}),'병원에 수용 문의를 전달했습니다.');});
$('inquiries').addEventListener('submit',event=>{const form=event.target.closest('.question-form');if(!form)return;event.preventDefault();const answer=form.querySelector('input').value.trim();if(answer)action(()=>api(path(`/inquiries/${form.dataset.iid}/answer`),'POST',{answer}),'질문 답변을 전달했습니다.');});
$('inquiries').addEventListener('click',event=>{
  const button=event.target.closest('[data-action]');if(!button)return;const id=Number(button.dataset.iid);const suffix={confirm:'/confirm',change:'/change-destination',retry:`/inquiries/${id}/retry`,'cancel-inquiry':`/inquiries/${id}/cancel`}[button.dataset.action];
  if(!suffix)return;
  const body=['confirm','change'].includes(button.dataset.action)?{inquiry_id:id}:(button.dataset.action==='cancel-inquiry'?{reason:'이송자가 문의를 취소함'}:{});
  action(()=>api(path(suffix),'POST',body),button.dataset.action==='change'?'사람의 판단으로 대체병원 목적지를 변경했습니다.':button.dataset.action==='confirm'?'목적지를 확정했습니다. 아직 출발하지 않았습니다.':'문의 상태를 갱신했습니다.');
});
$('depart-button').addEventListener('click',()=>action(()=>api(path('/depart'),'POST',{}),'이송 출발을 확정했습니다.'));
$('monitor-enable').addEventListener('click',()=>action(()=>api(path('/transport-review'),'POST',{enabled:true,policy_name:$('inquiry-policy').value || 'fixed_3'}),'수용 조건 감시를 활성화했습니다.'));
$('monitor-pause').addEventListener('click',()=>action(()=>api(path('/transport-review'),'POST',{enabled:false}),'감시와 자동 대체 문의를 일시정지했습니다.'));
$('position-form').addEventListener('submit',event=>{event.preventDefault();action(()=>api(path('/transport-position'),'PATCH',{lat:Number($('position-lat').value),lon:Number($('position-lon').value),measured_at:Math.floor(Date.now()/1000)}),'직접 관측한 위치를 갱신했습니다.');});
$('cancel-form').addEventListener('submit',event=>{event.preventDefault();const reason=$('cancel-reason').value.trim();if(reason)action(()=>api(path('/cancel'),'POST',{reason}),'이송을 취소했습니다. 기록은 읽기 전용으로 남습니다.');});
$('message-form').addEventListener('submit',submitMessage);
$('discard-message').addEventListener('click',()=>{pendingMessage=null;pendingAttempted=false;renderConversation();refresh(false);announce('실패한 전송 요청을 취소했습니다. 참여 병원을 확인한 뒤 새 메시지로 보내세요.');});
$('resume-pid').value=store.get('patient.pid')||'';
showScreen(pid?'transport':'home');initializeServer();if(pid)openPatient(pid);
setInterval(()=>{if(pid&&!busy&&!document.hidden)refresh(false);},3000);
