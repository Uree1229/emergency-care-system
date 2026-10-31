import {workflowLink} from './workflow-state.mjs';
const escape = value => String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const time = value => Number.isFinite(value) ? new Date(value*1000).toLocaleString('ko-KR',{timeZone:'Asia/Seoul'}) : '미확인';
const statuses = {READY:'현재 추천',STALE:'이전 추천 만료 · 재조회 필요',QUEUED:'새 추천 대기',ERROR:'추천 계산 오류',NOT_APPLICABLE:'이송 종료 · 추천 대상 아님',UNAVAILABLE:'추천 조회 전 또는 연결 확인 필요'};
const workers = {RUNNING:'가동 중',STARTING:'시작 중',ERROR:'계산 오류',STALLED:'처리 지연',DISABLED:'비활성',STOPPED:'중단',STOPPING:'종료 중',UNKNOWN:'가동 여부 미확인'};
const plans = {RECOMMENDED:'수용 응답을 바탕으로 계산한 배정안',UNASSIGNED:'현재 모델로 배정안을 만들지 못함',DESTINATION_HELD:'현재 목적지 유지 · 계획 재평가',NEEDS_REVIEW:'담당자 조건 확인 필요'};
const codes = {TREATMENT_CONFIRMATION_REQUIRED:'환자별 치료조건의 수용 가능 여부 확인 필요',PRIORITY_UNVERIFIED:'전문가 우선순위 미확인',NO_VALID_ACCEPTED_HOSPITAL:'유효한 수용 응답 없음',NO_ELIGIBLE_ACCEPTED_HOSPITAL:'수용 응답은 있으나 계산 입력 조건 확인 필요',OPERATING_RESOURCE_MISSING:'운영 자원 미등록',OPERATING_RESOURCE_UNKNOWN:'운영 자원 미확인',OPERATING_RESOURCE_STALE:'운영 정보 오래됨',UNSUPPORTED_RESOURCE:'계획 모델 지원 밖 자원',RESOURCE_CAPACITY_INSUFFICIENT:'확인된 운영 자원 부족',LOCATION_REQUIRED:'위치 입력 확인 필요'};
export function renderWorkflow(doc, state) {
  const $ = id => doc.getElementById(id), plan = state.plan;
  $('workflow-actor-error').textContent = state.actorError || '';
  $('workflow-actor').setAttribute?.('aria-invalid',String(!!state.actorInvalid));
  $('workflow-actor-save').disabled = !!state.busy || !!state.actorInvalid || state.hasPatientId === false;
  $('recommendation-state').textContent = statuses[plan.status] || `추천 상태: ${plan.status}`;
  $('recommendation-state').dataset.state = plan.status;
  const availability = plan.availability;
  $('planner-status').textContent = `배정 워커: ${workers[availability.worker_state] || availability.worker_state}${availability.reason_code ? ' · '+availability.reason_code : ''}`;
  $('replan-button').disabled = !state.context || !state.patient || state.busy || state.replanning || availability.accepting_requests === false || plan.status === 'NOT_APPLICABLE';
  $('replan-button').textContent = state.replanning ? '재계산 요청 중…' : '추천 재계산 요청';
  $('recommendation-error').textContent = state.recommendationError;
  const result = plan.result;
  const hospitalName=result && state.patient?.inquiries?.find(q=>q.hospital_id===result.hospital_id)?.hospital_name;
  $('recommendation-result').innerHTML = result ? `<p><strong>${escape(plans[result.status] || result.status)}</strong></p>
    <p>${result.hospital_id ? escape(hospitalName || '병원 ID '+result.hospital_id) : '추천 병원 미확정'}${result.inquiry_id ? ' · 수용 문의 #'+escape(result.inquiry_id) : ''}</p>
    <p class="small">계산 ${escape(time(plan.created_at))} · 유효 기한 ${escape(time(plan.expires_at))} · 계산 버전 ${escape(plan.revision)}</p>
    ${result.planned_start_at != null ? `<p class="small">모델상 치료 시작 ${escape(time(result.planned_start_at))} · 실험 설정으로 계산한 값이며 실제 치료 약속이 아닙니다.</p>` : ''}
    ${result.destination_locked ? '<p>기존 목적지는 유지됩니다. 추천으로 목적지가 자동 변경되지 않습니다.</p>' : ''}
    <ul class="small">${[...new Set([...(result.warnings || []),...(result.eligibility_reasons || []).flatMap(x=>(x.codes || []).map(c=>c+':'+x.hospital_id))])].map(code=>`<li>${escape(codes[code] || (codes[String(code).split(':')[0]] ? codes[String(code).split(':')[0]] + (String(code).includes(':') ? ' · ' + String(code).split(':').slice(1).join(':') : '') : code))}</li>`).join('')}</ul>` : '<p class="small">현재 사용할 수 있는 추천 내용이 없습니다. 병원 문의와 유효한 수용 응답의 수동 확정은 계속 사용할 수 있습니다.</p>';
  const guidance = state.inputReview;
  $('input-review-state').textContent = guidance ? ({REVIEW_REQUIRED:'배정 입력 재확인 필요',INFORMATION_ONLY:'배정 입력 참고 항목',NO_REVIEW_FLAGS:'현재 정책의 재확인 표시 없음'}[guidance.status] || guidance.status) : '현재 입력 확인 항목 조회 전';
  $('input-review-items').innerHTML = (guidance?.items || []).map(item=>`<li><strong>${escape(item.label || item.field)}</strong> · ${escape(item.reason || item.state)}</li>`).join('');
  $('input-review-error').textContent = state.reviewError;
  const link = state.context && workflowLink(state.context.server,state.context.pid,state.context.actor);
  $('patient-voice-link').hidden = !link;
  if (link) { $('patient-voice-link').href = link; $('patient-voice-link').textContent = ['CONFIRMED','IN_TRANSIT'].includes(state.patient?.state) ? '같은 이송의 상태 변경·재확인 검토' : '같은 환자 정보 음성 수정'; }
  const active=['CONFIRMED','IN_TRANSIT'].includes(state.patient?.state);
  $('patient-change-link').hidden = !link || !active;
  if(link&&active){const change=new URL(state.context.server.replace(/\/$/,'')+'/patient/transport-change.html');change.search=new URLSearchParams({pid:state.context.pid,actor_id:state.context.actor,hid:state.patient.destination_hospital_id || ''}).toString();$('patient-change-link').href=change.href;}
  const unacknowledged = state.events.filter(e=>e.acknowledged_at == null).length;
  $('important-event-status').textContent = `미확인 ${unacknowledged}건 · 알림 확인은 수용·목적지 판단과 별개입니다.${state.recoveryHasMore ? ' 확인 후 다음 미확인 기록을 불러옵니다.' : ''}`;
  $('important-event-error').textContent = state.eventError;
  const focused = doc.activeElement?.dataset?.ackEvent;
  const markup = state.events.map(item=>`<article class="workflow-event ${item.acknowledged_at == null ? 'unacknowledged' : ''}" data-workflow-event="${item.event_id}">
    <p>${escape(item.summary || item.type)}</p><p class="small">사건 ${escape(time(item.at))}<br>화면 수신 ${escape(time(item.delivered_at))} · 사용자 확인 ${escape(time(item.acknowledged_at))}</p>
    <button type="button" class="secondary" data-ack-event="${item.event_id}" ${item.delivered_at == null || item.acknowledged_at != null || state.acknowledging.has(item.event_id) ? 'disabled' : ''}>${item.acknowledged_at != null ? '확인 기록됨' : state.acknowledging.has(item.event_id) ? '확인 기록 중…' : '알림 확인'}</button></article>`).join('') || '<p class="small">현재 표시할 중요 사건이 없습니다.</p>';
  if ($('important-events').innerHTML !== markup) $('important-events').innerHTML = markup;
  if (focused) $('important-events').querySelector(`[data-ack-event="${Number(focused)}"]`)?.focus({preventScroll:true});
}
export function observeVisibleEvents(doc, controller) {
  let observer = null, scheduled = false;
  function scan() {
    if (scheduled) return; scheduled = true; const token = controller.context;
    requestAnimationFrame(()=>requestAnimationFrame(()=>{
      scheduled = false;
      if (doc.hidden || !controller.current(token)) return;
      const clip=doc.getElementById('important-events').getBoundingClientRect();
      const ids = [...doc.querySelectorAll('[data-workflow-event]')].filter(node=>{
        const r = node.getBoundingClientRect(); return node.isConnected && r.width>0 && r.height>0 && r.bottom>Math.max(0,clip.top) && r.top<Math.min(innerHeight,clip.bottom) && r.right>Math.max(0,clip.left) && r.left<Math.min(innerWidth,clip.right);
      }).map(node=>Number(node.dataset.workflowEvent));
      controller.markVisible(ids,token);
    }));
  }
  return {
    update() {
      observer?.disconnect();
      if (typeof IntersectionObserver !== 'undefined') {
        observer = new IntersectionObserver(entries=>{if(entries.some(e=>e.isIntersecting))scan();});
        for (const node of doc.querySelectorAll('[data-workflow-event]')) observer.observe(node);
      }
      scan();
    }, scan,
  };
}
