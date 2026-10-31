import {assertTransportRetryScope,hospitalDecisionActor,resetHospitalApprovalChecks} from './transport-retry.mjs';
const $=id=>document.getElementById(id);
const params=new URLSearchParams(location.search);
$('patientId').value=params.get('pid')||'';$('hospitalId').value=params.get('hid')||'';
const entryActor=params.get('actor_id');
const validActor=value=>typeof value==='string'&&/^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,79}$/.test(value);
if(entryActor!==null)$('actorId').value=validActor(entryActor)?entryActor:'';
let patient=null,session=null,proposal=null,reviews=[],busy=false,pending=null,revision=0;
const key='enroute-review-pending:'+location.origin;
try{pending=JSON.parse(sessionStorage.getItem(key)||'null');}catch{}
const status=(text,error=false)=>{$('status').textContent=text;$('status').classList.toggle('error',error);};
const resourceNames={ed_bed:'응급실 자리',special_team:'전문팀'};
const states={CONFIRMED:'목적지 확정',IN_TRANSIT:'이송 중',ARRIVED:'도착',PENDING:'병원 검토 대기',SUPERSEDED:'새 변경안으로 대체',REJECTED:'반려·기존 정보 유지',APPLIED_KEEP:'변경 반영·수용 유지',APPLIED_ADJUST:'변경 반영·대체 병원 검토'};
const fields={age:'나이',consciousness:'의식',breathing:'호흡',priority:'우선순위',priority_source:'우선순위 출처',symptoms:'증상',onset_at:'발생 시각'};
const resourceText=resources=>Object.entries(resources||{}).map(([k,v])=>`${resourceNames[k]||k} ${v}`).join(' · ')||'미확인';
const valueText=value=>Array.isArray(value)?value.join(' · '):value==null?'미확인':String(value);
function observations(extra){const v=extra?.vitals||{};return [['맥박',v.hr],['혈압(수축기)',v.sbp],['혈압(이완기)',v.dbp],['산소포화도',v.spo2],['체온',v.temperature],['호흡수',v.rr]].filter(([,v])=>v!=null).map(([k,v])=>`${k}: ${v}`).join(' · ')||'추가 수치 관찰 없음';}
function conditions(c){return `필요 진료: ${(c.required_capabilities||[]).join(' · ')||'미확인'}\n필요 자원: ${resourceText(c.resource_demands)}`;}
function proposalText(p){return `현재 치료조건\n${conditions(p.current)}\n\n검토할 치료조건\n${conditions(p.proposed)}\n\n새 관찰\n${observations(p.proposed_observations)}\n${Object.entries(p.observation_changes||{}).map(([k,v])=>`${fields[k]||k}: ${valueText(v.previous)} → ${valueText(v.proposed)}`).join('\n')}\n\n${p.reductions.length?'감소하거나 빠지는 치료조건이 있습니다. 별도 확인이 필요합니다.':'감소하는 치료조건 없음'}${p.unsupported_solver_resources.length?'\n현재 계산 모델 지원 외 자원이 있습니다. 요구를 보존하고 별도 검토합니다.':''}`;}
function textBlock(id,text){$(id).hidden=false;$(id).textContent=text;}
const scope=()=>({pid:$('patientId').value.trim(),hid:$('hospitalId').value.trim(),actor:$('actorId').value.trim()});
function updatePatientLink(){
  const current=scope(),url=new URL('/patient/',location.origin);
  if(current.pid)url.searchParams.set('pid',current.pid);
  if(validActor(current.actor))url.searchParams.set('actor_id',current.actor);
  $('patientLink').href=url.href;
}
function clearContext(){revision++;patient=session=proposal=null;reviews=[];$('draft').hidden=$('proposal').hidden=true;$('reviews').replaceChildren();$('requestConfirm').checked=false;}
for(const id of ['patientId','hospitalId','actorId'])$(id).addEventListener('input',()=>{clearContext();updatePatientLink();});
$('hospitalActor').addEventListener('input',()=>{
  resetHospitalApprovalChecks($('reviews'));
  try{
    if(hospitalDecisionActor(pending)!==null)assertTransportRetryScope(pending,scope(),$('hospitalActor').value);
    status('병원 작업자가 바뀌었습니다. 현재 병원의 담당자 역할로 변경 내용과 수용 가능 여부를 다시 확인하세요.');
  }catch(error){status(error.message,true);}
});
updatePatientLink();
if(entryActor!==null&&!validActor(entryActor))status('링크의 작업자 ID를 확인할 수 없습니다. 작업자 ID를 입력한 뒤 계속하세요.',true);
const currentScope=()=>JSON.stringify(scope());
function pendingDisplay(){ $('retry').hidden=!pending; }
pendingDisplay();
async function api(path,method='GET',body){
  const response=await fetch(path,{method,headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined});
  const data=await response.json();
  if(!response.ok){const error=new Error(`${response.status}: ${data.message||data.error}`);error.status=response.status;throw error;}
  return data;
}
async function write(path,body){
  if(pending)throw new Error('응답을 받지 못한 요청이 있습니다. 같은 ID로 재시도하거나 원래 이송을 다시 조회하세요.');
  pending={path,body,scope:scope()};sessionStorage.setItem(key,JSON.stringify(pending));pendingDisplay();
  return replayPending();
}
async function replayPending(){
  if(!pending)throw new Error('재시도할 요청이 없습니다.');
  assertTransportRetryScope(pending,scope(),$('hospitalActor').value);
  try{
    const result=await api(pending.path,'POST',pending.body);
    pending=null;sessionStorage.removeItem(key);pendingDisplay();return result;
  }catch(error){
    if(error.status&&error.status<500){pending=null;sessionStorage.removeItem(key);pendingDisplay();}
    throw error;
  }
}
function common(s){return {actor_id:scope().actor,client_request_id:crypto.randomUUID(),...(s?{expected_version:s.version}:{})};}
function showSession(){
  proposal=null;$('proposal').hidden=true;$('requestConfirm').checked=false;
  if(session){$('sessionSummary').textContent=`초안 버전 ${session.version} · ${session.confirmed?'현재 버전 확인 완료':'내용 확인 필요'}`;textBlock('draft',session.speak_text);}
}
async function loadPatient(){
  const origin=currentScope();const p=await api(`/api/patients/${encodeURIComponent(scope().pid)}`);
  if(origin!==currentScope())return;
  patient=p;$('hospitalId').value=p.destination_hospital_id||'';
  $('patientSummary').textContent=`${p.id} · ${states[p.state]||p.state} · 정보 버전 ${p.version} · 현재 목적지 ${p.destination_hospital_id||'미정'}`;
  $('hospitalLink').href='/hospital/?hid='+encodeURIComponent(p.destination_hospital_id||'');
  textBlock('currentConditions',`${conditions(p)}\n${observations(p.extra)}\n${p.needs_adjustment?'병원 조정 요청 있음 · 기존 목적지 유지':'현재 목적지 유지'}`);
}
async function refreshReviews(){
  const s=currentScope(),hid=scope().hid;if(!hid)throw new Error('현재 병원 ID가 필요합니다.');
  const data=await api(`/api/hospitals/${encodeURIComponent(hid)}/transport-change-reviews`);
  if(s!==currentScope())return;reviews=data.reviews;renderReviews();
}
function renderReviews(){
  $('reviews').replaceChildren();
  for(const r of reviews.filter(r=>!scope().pid||r.patient_id===scope().pid)){
    const card=document.createElement('article');card.className='review';card.dataset.reviewId=r.id;
    const title=document.createElement('h3');title.textContent=`${r.patient_id} · ${states[r.state]||r.state}`;card.append(title);
    const facts=document.createElement('pre');facts.textContent=`현재 병원 ${r.hospital_id}\n${proposalText(r.proposal)}${r.blockers.length?'\n\n'+(r.blockers.includes('UNSUPPORTED_SOLVER_RESOURCE')?'지원 외 자원이 있어 자동 반영을 보류합니다. 담당자의 별도 검토가 필요합니다.':r.state==='PENDING'?'정보가 변경돼 최신 내용으로 재검토가 필요합니다.':'이 검토는 종료됐습니다.'):''}`;card.append(facts);
    if(r.state==='PENDING'){
      const note=document.createElement('input');note.placeholder='병원의 검토 사유';note.setAttribute('aria-label','병원의 검토 사유');card.append(note);
      const label=document.createElement('label');label.className='checks';const ack=document.createElement('input');ack.type='checkbox';ack.dataset.hospitalDecisionConfirm='true';label.append(ack,'현재 병원의 담당자 역할로 새 관찰·치료조건과 수용 가능 여부를 확인했습니다.');card.append(label);
      for(const [decision,text] of [['keep','변경 반영·수용 유지'],['adjust','변경 반영·대체 병원 탐색'],['reject','변경안 반려·기존 정보 유지']]){
        const button=document.createElement('button');button.textContent=text;button.dataset.decision=decision;
        button.disabled=decision!=='reject'&&!r.can_decide;
        button.addEventListener('click',()=>perform(async()=>{
          if(!ack.checked||!note.value.trim())throw new Error('변경 내용 확인과 병원 검토 사유가 필요합니다.');
          if(decision!=='reject'&&!r.monitoring_enabled)throw new Error('현재 이송의 조건 감시를 먼저 활성화한 뒤 목록을 다시 조회하세요.');
          const result=await write(`/api/hospitals/${encodeURIComponent(r.hospital_id)}/transport-change-reviews/${encodeURIComponent(r.id)}/decision`,
            {actor_id:$('hospitalActor').value.trim(),client_request_id:crypto.randomUUID(),
             expected_review_version:r.version,expected_patient_version:r.current_patient_version,
             decision_token:r.decision_token,decision,confirmed:true,reason:note.value.trim()});
          status(`병원 판단 저장: ${states[result.review.state]||result.review.state}. 목적지는 자동으로 바뀌지 않았습니다.`);
          await loadPatient();await refreshReviews();
        }));card.append(button);
      }
    }$('reviews').append(card);
  }
  if(!$('reviews').children.length)$('reviews').textContent='이 범위의 변경 검토가 없습니다.';
}
async function perform(action){
  if(busy)return;busy=true;const controls=[...document.querySelectorAll('button,input,select,textarea')];
  const disabled=controls.map(e=>e.disabled);controls.forEach(e=>e.disabled=true);
  try{await action();}catch(error){status(error.message+(error.status===409?' 최신 환자·검토 목록을 조회한 뒤 다시 확인하세요.':''),true);}
  finally{controls.forEach((e,i)=>e.disabled=disabled[i]);busy=false;pendingDisplay();}
}
const on=(id,action)=>$(id).addEventListener('click',()=>perform(action));
on('load',async()=>{clearContext();await loadPatient();await refreshReviews();status('현재 이송과 병원 검토 목록을 조회했습니다.');});
on('newDraft',async()=>{if(!patient||!['CONFIRMED','IN_TRANSIT'].includes(patient.state))throw new Error('확정·이송 중인 환자를 먼저 연결하세요.');session=(await write('/api/voice/sessions',commonWithPatient())).session;showSession();status('현재 환자정보로 새 초안을 연결했습니다.');});
function commonWithPatient(){return {...common(),patient_id:patient.id};}
on('transcript',async()=>{if(!session)throw new Error('새 초안을 먼저 연결하세요.');const text=$('observation').value.trim();if(!text)throw new Error('새 관찰 내용을 입력하세요.');session=(await write(`/api/voice/sessions/${session.id}/transcripts`,{...common(session),utterance_id:crypto.randomUUID(),text,is_final:true,mode:'auto'})).session;showSession();status('인식문을 초안에 추가했습니다. 환자정보에는 아직 반영되지 않았습니다.');});
on('confirmDraft',async()=>{if(!session)throw new Error('연결된 초안이 없습니다.');session=(await write(`/api/voice/sessions/${session.id}/confirm`,{...common(session),confirmed:true})).session;showSession();status('현재 초안 내용을 확인했습니다.');});
on('preview',async()=>{if(!session)throw new Error('연결된 초안이 없습니다.');proposal=(await api(`/api/voice/sessions/${session.id}/transport-change-review/preview`,'POST',{...common(session),selection:{mode:$('selection').value}})).review;textBlock('proposal',proposalText(proposal));$('requestConfirm').checked=false;status('변경 전후를 조회했습니다. 현재 정보와 목적지는 유지됩니다.');});
on('request',async()=>{if(!session||!proposal||!$('requestConfirm').checked)throw new Error('현재 변경안을 조회하고 전송 내용을 확인하세요.');if(proposal.selection.mode!==$('selection').value)throw new Error('선택이 바뀌었습니다. 변경안을 다시 조회하세요.');const result=await write(`/api/voice/sessions/${session.id}/transport-change-review/request`,{...common(session),expected_patient_version:proposal.patient_version,review_token:proposal.token,selection:proposal.selection,confirmed:true,allow_reduction:$('reduce').checked,reason:$('requestReason').value.trim()});status(`변경안 ${result.review.id} 전송. 병원 판단 전까지 기존 정보가 유지됩니다.`);await loadPatient();await refreshReviews();});
on('enableMonitor',async()=>{if(!patient||!$('monitorConfirm').checked)throw new Error('이송 연결과 감시 활성화 확인이 필요합니다.');await api(`/api/patients/${encodeURIComponent(patient.id)}/transport-review`,'POST',{enabled:true});await loadPatient();await refreshReviews();status('이송 조건 감시를 활성화했습니다.');});
on('refreshReviews',async()=>{await loadPatient();await refreshReviews();status('최신 환자정보와 검토 목록을 조회했습니다.');});
on('retry',async()=>{const result=await replayPending();const sid=result.session?.id||result.review?.session_id;
  session=sid?(await api(`/api/voice/sessions/${encodeURIComponent(sid)}?actor_id=${encodeURIComponent(scope().actor)}`)).session:null;
  proposal=null;if(session)showSession();else $('draft').hidden=$('proposal').hidden=true;
  await loadPatient();await refreshReviews();status('원래 요청의 저장 결과와 최신 초안을 확인했습니다.');});
for(const id of ['selection','observation'])$(id).addEventListener('input',()=>{$('requestConfirm').checked=false;});
window.addEventListener('pageshow',()=>{if(pending){try{assertTransportRetryScope(pending,scope(),$('hospitalActor').value);status('응답 유실 요청이 보존돼 있습니다. 원래 범위에서 같은 ID로 재시도하세요.');}catch(error){status(error.message,true);}}});
