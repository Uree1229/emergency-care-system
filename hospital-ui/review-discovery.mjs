// 병원 메인에서 이 병원에 온 임상변경 검토 대기를 발견하고 같은 환자의 검토 화면으로 잇는다.
// 판단(유지·조정·반려)은 기존 검토 화면이 맡는다. 여기서는 조회 결과를 검증해 보여 주기만 한다.
export const LIST_LIMIT = 100;   // 서버 list_reviews의 최신 건수 제한

const RESOURCE = { ed_bed: '응급병상', special_team: '전문팀' };
const FIELD = { age: '나이', consciousness: '의식', breathing: '호흡', priority: '우선순위', priority_source: '우선순위 출처', symptoms: '증상', onset_at: '발생 시각' };
export const BLOCKERS = {
  TRANSPORT_ENDED: '이송이 끝났습니다',
  DESTINATION_CHANGED: '목적지가 바뀌었습니다',
  PATIENT_CHANGED: '환자정보가 바뀌었습니다',
  VOICE_DRAFT_CHANGED: '변경안 초안이 바뀌었습니다',
  VOICE_CONFIRMATION_REQUIRED: '이송자의 초안 확인이 필요합니다',
  CATALOG_CHANGED: '치료조건 기준이 바뀌었습니다',
  UNSUPPORTED_SOLVER_RESOURCE: '현재 모델 지원 외 자원이 있어 자동 반영할 수 없습니다',
  REVIEW_CLOSED: '이미 종료된 검토입니다',
};

const VALUE = {
  consciousness: { alert: '명료', impaired: '저하', unresponsive: '무반응', unknown: '미확인' },
  breathing: { normal: '정상', abnormal: '이상', absent: '없음', unknown: '미확인' },
};
const text = (value) => Array.isArray(value) ? value.join(', ') || '없음' : value == null ? '미확인' : String(value);
const resources = (demands) => Object.entries(demands || {}).map(([k, n]) => `${RESOURCE[k] || k} ${n}`).join(' · ') || '없음';
const isObject = (value) => !!value && typeof value === 'object' && !Array.isArray(value);
const validReview = (r) => isObject(r) && typeof r.id === 'string' && r.id && typeof r.patient_id === 'string' && r.patient_id
  && typeof r.state === 'string' && Array.isArray(r.blockers) && r.blockers.every((b) => typeof b === 'string')
  && typeof r.can_decide === 'boolean' && typeof r.monitoring_enabled === 'boolean' && isObject(r.proposal);

// 응답 전체를 검증한다. 다른 병원 항목이나 형식 오류가 하나라도 있으면 목록을 쓰지 않는다.
export function readReviewList(data, hospital) {
  if (!isObject(data) || !Array.isArray(data.reviews)) return { error: '검토 목록 응답 형식을 확인할 수 없습니다.' };
  if (data.reviews.some((r) => !validReview(r))) return { error: '검토 목록 응답 형식을 확인할 수 없습니다.' };
  if (data.reviews.some((r) => r.hospital_id !== hospital)) return { error: '다른 병원의 검토가 포함된 응답이라 표시하지 않았습니다.' };
  return { pending: data.reviews.filter((r) => r.state === 'PENDING'), truncated: data.reviews.length >= LIST_LIMIT };
}

export function changeLines(proposal) {
  const lines = [];
  const before = proposal.current || {}, after = proposal.proposed || {};
  if (text(before.required_capabilities) !== text(after.required_capabilities)) lines.push(`필요 진료: ${text(before.required_capabilities)} → ${text(after.required_capabilities)}`);
  if (resources(before.resource_demands) !== resources(after.resource_demands)) lines.push(`필요 자원: ${resources(before.resource_demands)} → ${resources(after.resource_demands)}`);
  for (const [field, change] of Object.entries(proposal.observation_changes || {})) {
    const label = (value) => VALUE[field]?.[value] ?? text(value);
    lines.push(`${FIELD[field] || field}: ${label(change?.previous)} → ${label(change?.proposed)}`);
  }
  if ((proposal.reductions || []).length) lines.push('감소하거나 빠지는 치료조건이 있습니다');
  return lines.length ? lines : ['치료조건 변화 없음 · 관찰 기록만 바뀜'];
}

// 기존 검토 화면의 진입 계약은 pid·hid다. 이송자용 actor_id에 병원 작업자를 넣지 않는다.
export function reviewLink(server, hospital, patient) {
  try {
    const url = new URL('/patient/transport-change.html', server);
    if (!/^https?:$/.test(url.protocol)) return null;
    url.searchParams.set('pid', patient); url.searchParams.set('hid', hospital);
    return url.href;
  } catch { return null; }
}

export function reviewPanelMarkup({ pending, error, truncated, checkedAt, server, hospital }, esc) {
  const items = (pending || []).map((r) => {
    const link = reviewLink(server, hospital, r.patient_id);
    const blockers = r.blockers.map((b) => `<li>${esc(BLOCKERS[b] || b)}</li>`).join('');
    const status = r.can_decide ? (r.monitoring_enabled ? '판단 가능' : '이송자 감시 꺼짐 · 유지·조정 불가, 반려만 가능') : '재검토 필요 · 반려만 가능';
    return `<li class="review-alert" data-review-id="${esc(r.id)}">
      <div class="review-alert-head"><b>${esc(r.patient_id)}</b><span class="tag t-mid">검토 대기</span><span class="note">${esc(status)}</span></div>
      <ul class="review-alert-changes">${changeLines(r.proposal).map((line) => `<li>${esc(line)}</li>`).join('')}</ul>
      ${blockers ? `<ul class="review-alert-blockers" aria-label="판단 전 확인 사항">${blockers}</ul>` : ''}
      <div class="row"><button type="button" class="btn-small" data-review-patient="${esc(r.patient_id)}">환자 보기</button>${link ? `<a class="btn-small review-alert-link" href="${esc(link)}" target="_blank" rel="noopener">검토 화면 열기</a>` : ''}</div>
    </li>`;
  }).join('');
  const notes = [
    error ? `<p class="note review-alert-error" role="status">${esc(error)}${checkedAt ? ` 아래는 ${esc(checkedAt)}에 조회한 목록입니다.` : ''}</p>` : '',
    truncated ? '<p class="note">서버가 최신 100건만 반환합니다. 더 오래된 대기 건은 이 목록에 없을 수 있습니다.</p>' : '',
  ].join('');
  return `<div class="review-alerts-head"><h2>임상변경 검토 대기 <span class="count">${(pending || []).length}</span></h2><span class="note">판단은 검토 화면에서 합니다. 승인·조정만으로 목적지는 바뀌지 않습니다.</span></div>${notes}${items ? `<ul class="review-alert-list">${items}</ul>` : ''}`;
}
