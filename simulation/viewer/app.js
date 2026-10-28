'use strict';

// A trace viewer: policy decisions and results come from the simulation JSON.
// It does not make allocation decisions or infer clinical suitability in the browser.
const $ = (id) => document.getElementById(id);
const NS = 'http://www.w3.org/2000/svg';
const COLORS = ['#267db4', '#b77c27', '#7763b0', '#278670', '#bc5951'];
const state = { scenarios: [], selected: null, time: 0, duration: 1, playing: false, speed: 60, patient: 'all', lastFrame: 0, zoom: 1, pan: [0, 0], geography: null, roads: null };
const eventNames = { patient_occurs: '환자 발생', ambulance_dispatched: '119 출동', ambulance_arrive: '119 현장 도착', scene_care_start: '현장 평가·처치 시작', scene_care_complete: '현장 평가 완료 · 탐색 시작', request: '환자 치료조건 전달', ask: '수용 문의', accept: '수용 가능 응답', decline: '수용 불가 응답', timeout: '응답 대기시간 초과', commit: '병원 수용 확정', redirect: '이송 중 목적지 변경', arrive: '병원 도착', treatment_start: '치료 시작', treatment_complete: '치료 완료', notify: '조건 변경 알림', resource_unavailable: '자원 사용 불가 발생', resource_restored: '자원 사용 가능 복구', restored: '자원 복구', arrival_unable: '도착 후 치료조건 불충족', condition_update: '환자 치료조건 추가 확인', transfer_search: '전원 병원 탐색', transfer_depart: '병원 간 전원 출발', search_expand: '탐색 범위 확대', expand: '탐색 범위 확대', recheck: '수용 조건 재확인', no_candidate: '확인 가능한 후보 없음', no_compatible: '필수조건을 충족하는 후보 없음', accepted: '수용 가능 응답', rejected: '수용 불가 응답' };
Object.assign(eventNames, { review_queued: '문의 검토 대기', review_started: '문의 검토 시작', review_completed: '문의 검토 완료', late_reply: '기한 이후 응답 · 배정에 미반영', no_response: '병원 회신 없음', resource_condition_risk: '이송 중 자원조건 재확인 필요', resource_condition_resolved: '이송 중 자원 부족 상태 해제' });
eventNames.inquiry_policy = '동시 문의 수 판단';
eventNames.inquiry_feedback = '회신 관측';
Object.assign(eventNames, { review_cancelled: '검토 시작 전 문의 취소', reply_suppressed: '취소 후 회신 · 배정에 미반영', selection_deadline: '병원 선정 기한 종료 · 미선정' });
const serverCase = () => current().server_inquiry_only === true;
const transportCase = () => current().server_transport_replay === true;
const recordedCase = () => serverCase() || transportCase();
const onlineCase = () => current().online_allocation_comparison === true;
const realRoadCase = () => current().real_road_allocation === true;
const syntheticLocations = () => recordedCase() || (onlineCase() && !realRoadCase());
let projection = null, basemapMode = 'osm', basemapTimer = null, basemapEpoch = 0;
let mapBranch = 'all', mapDetails = false;
let mapExpanded = false, mapPanelAnchor = null, patientSelectorAnchor = null, mapScrollPosition = null;
const mapTiles = new Map();
Object.assign(eventNames, { depart: '이송 출발', resource_update: '병원 자원 갱신', external_arrival: '시스템 밖 환자 도착', acceptance_kept: '병원 재확인 · 기존 수용 유지', conditions_restored: '수용 조건 복구', alternative_requested: '병원 조정 요청 · 대체 탐색', alternative_ready: '대체 수용 확보 · 사람의 변경 대기', alternative_exhausted: '대체 탐색 종료 · 담당자 확인 필요', inquiry_closed: '문의 종료', transport_position_updated: '현재 위치 관측', transport_baseline_captured: '수용 당시 조건 저장', transport_monitor_started: '수용 조건 모니터링 시작', transport_monitor_completed: '수용 조건 모니터링 종료', transport_inquiry_started: '대체 문의 시작', transport_inquiry_decision: '대체 문의 후보 판단', transport_inquiry_batch: '대체 문의 전송', transport_inquiry_waiting: '대체 문의 대기', adjustment_needed: '병원 조정 필요 판단', hospital_keeps: '병원 수용 유지 판단', patient_redirected_away: '기존 병원에 목적지 변경 통보' });
const serverObserved = (policy) => /^(observed|v3)/.test(policy?.server_policy?.name ?? '');
const inquiryReasons = { cold_start: '응답 이력 수집 중', slow_replies: '회신 지연 관측', fast_replies: '빠른 회신 관측', timeouts_without_slow_replies: '미응답 관측 · 후보 추가 확인', aged_pending: '오래 대기 중인 문의 관측' };
const observedInquiryReasons = { initial_best: '관측 비용이 가장 낮은 후보에 먼저 문의', hedge_better_candidate: '기다리는 문의보다 비용이 낮은 후보 추가', wait_for_hedge: '추가 문의 전 최소 관측시간 대기', pending_preferred: '진행 중인 문의의 예상 비용이 더 낮음', concurrency_cap: '동시 문의 상한 도달', no_candidates: '추가로 문의할 후보 없음', bounded_wait_expansion: '추가 문의 대기 상한 도달 · 다음 후보 문의', fixed_wave: '고정 규모로 다음 후보 묶음 문의' };
const KEY_EVENT_TYPES = new Set(['patient_occurs', 'request', 'ambulance_arrive', 'scene_care_complete', 'ask', 'accept', 'accepted', 'decline', 'rejected', 'commit', 'no_response', 'selection_deadline', 'redirect', 'arrive', 'transfer_search', 'transfer_depart', 'treatment_start', 'treatment_complete']);
['depart', 'resource_update', 'external_arrival', 'recheck', 'acceptance_kept', 'conditions_restored', 'alternative_requested', 'alternative_ready', 'alternative_exhausted'].forEach(type => KEY_EVENT_TYPES.add(type));
const PRIORITY_TIERS = [4, 2, 1];
const priorityOrderNames = { none: '기본 처리 순서', requests: '같은 분 신규 문의를 4→2→1군 순서로 제출', all: '탐색 시작 환자의 문의·회신·재검토를 4→2→1군 순서로 처리' };
const svg = (name, attrs = {}, text = null) => { const n = document.createElementNS(NS, name); for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v); if (text != null) n.textContent = text; return n; };
const node = (tag, cls, text = null) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
const number = (v, fallback = 0) => Number.isFinite(Number(v)) ? Number(v) : fallback;
const when = (e) => number(e.t ?? e.time ?? e.minute);
const idOf = (x) => typeof x === 'object' && x ? String(x.id ?? '') : String(x ?? '');
const patientId = (e) => idOf(e.patient ?? e.patient_id);
const hospitalId = (e) => idOf(e.hospital ?? e.hospital_id);
const typeOf = (e) => String(e.type ?? e.event ?? '');
const clock = (t) => { const sec = Math.max(0, Math.round(number(t) * 60)); return `${String(Math.floor(sec / 60)).padStart(2, '0')}:${String(sec % 60).padStart(2, '0')}`; };
const minutes = (t) => Number.isFinite(Number(t)) && t != null ? `${Number(t).toFixed(1)}분` : '—';
const resultTime = (t, decimals = 1) => Number.isFinite(Number(t)) && t != null ? recordedCase() ? `${(Number(t) * 60).toFixed(decimals)}초` : minutes(t) : '—';
const current = () => state.selected?.scenario ?? {};
const patients = () => current().patients ?? [];
const hospitals = () => current().hospitals ?? [];
const branches = () => state.selected?.branches ?? [];
const priorityPolicyEnabled = (policy) => (policy?.priority_order != null && policy.priority_order !== 'none') || policy?.high_priority_hedge_after != null || policy?.rule === 'R4';
const priorityComparisonEnabled = () => current().family === 'priority_protection' || branches().some(b => priorityPolicyEnabled(b.policy));
const hospitalById = (id) => hospitals().find(h => String(h.id) === String(id));
const hospitalName = (id) => hospitalById(id)?.name ?? (id || '미정');
const seen = (branch) => branch.events.filter(e => when(e) <= state.time + 1e-7);
const locOf = (obj) => Array.isArray(obj?.location) ? obj.location : [number(obj?.lat), number(obj?.lon)];
let project = () => [500, 360];
const geometryCache = new WeakMap();
const keyEventCache = new WeakMap();

// Route coordinates are [latitude, longitude], unlike GeoJSON coordinates.
// Interpolate by cumulative road distance so bends do not change vehicle speed.
function legGeometry(leg) {
  if (geometryCache.has(leg)) return geometryCache.get(leg);
  const validPoints = values => (Array.isArray(values) ? values : []).filter(p => Array.isArray(p) && p.length >= 2 && Number.isFinite(p[0]) && Number.isFinite(p[1])).map(p => [p[0], p[1]]);
  const points = validPoints(leg.geometry);
  const motion = validPoints(leg.motion_geometry);
  const road = leg.geometry_kind === 'road' && points.length >= 2;
  const path = motion.length >= 2 ? motion : road ? points : validPoints([leg.origin, leg.destination]);
  const cumulative = [0];
  for (let i = 1; i < path.length; i++) {
    const a = path[i - 1], b = path[i], rad = Math.PI / 180;
    const dLat = (b[0] - a[0]) * rad, dLon = (b[1] - a[1]) * rad;
    const h = Math.sin(dLat / 2) ** 2 + Math.cos(a[0] * rad) * Math.cos(b[0] * rad) * Math.sin(dLon / 2) ** 2;
    cumulative.push(cumulative.at(-1) + 6371000 * 2 * Math.atan2(Math.sqrt(h), Math.sqrt(Math.max(0, 1 - h))));
  }
  const same = (a, b) => a && b && Math.abs(a[0] - b[0]) < 1e-8 && Math.abs(a[1] - b[1]) < 1e-8;
  const roadStart = road ? path.findIndex(p => same(p, points[0])) : -1;
  const roadEnd = road ? path.findLastIndex(p => same(p, points.at(-1))) : -1;
  const segmentKinds = path.map((_, i) => !road ? 'synthetic' : roadStart >= 0 && roadEnd >= roadStart && i > roadStart && i <= roadEnd ? 'road' : 'connector');
  const hasConnector = segmentKinds.some((kind, i) => kind === 'connector' && i > 0 && cumulative[i] - cumulative[i - 1] > .01);
  const result = { points: path, cumulative, total: cumulative.at(-1) ?? 0, road, segmentKinds, hasConnector };
  geometryCache.set(leg, result);
  return result;
}

function routeParts(leg, fraction) {
  const geometry = legGeometry(leg), progress = routeProgress(leg, fraction), groups = [];
  for (let i = 1; i < progress.prefix.length; i++) {
    const kind = geometry.segmentKinds[i] ?? 'synthetic';
    if (geometry.cumulative[i] - geometry.cumulative[i - 1] < .01) continue;
    const last = groups.at(-1);
    if (last?.kind === kind) last.points.push(progress.prefix[i]);
    else groups.push({ kind, points: [progress.prefix[i - 1], progress.prefix[i]] });
  }
  return groups;
}

function routeProgress(leg, fraction) {
  const geometry = legGeometry(leg), points = geometry.points;
  if (!points.length) return { point: null, prefix: [] };
  const ratio = Math.min(1, Math.max(0, number(fraction)));
  if (ratio >= 1) return { point: points.at(-1), prefix: points };
  if (!geometry.total || ratio <= 0) return { point: points[0], prefix: [points[0]] };
  const distance = geometry.total * ratio;
  let i = 1;
  while (i < points.length - 1 && geometry.cumulative[i] < distance) i++;
  const length = geometry.cumulative[i] - geometry.cumulative[i - 1];
  const local = length ? (distance - geometry.cumulative[i - 1]) / length : 0;
  const a = points[i - 1], b = points[i];
  const point = [a[0] + (b[0] - a[0]) * local, a[1] + (b[1] - a[1]) * local];
  return { point, prefix: [...points.slice(0, i), point] };
}

function routingInfo() {
  const legs = branches().flatMap(b => b.trajectories.flatMap(tr => tr.legs ?? []));
  const roadCount = legs.filter(l => legGeometry(l).road).length;
  const summary = state.selected?.routing_summary ?? current().routing_summary ?? {};
  const fallbackCount = legs.length - roadCount;
  const evaluatedFallbackCount = number(summary.fallback_routes, fallbackCount);
  const connectorCount = legs.filter(l => legGeometry(l).hasConnector).length;
  const providers = [...new Set(legs.filter(l => legGeometry(l).road).map(l => l.provider).filter(Boolean))];
  return { roadCount, fallbackCount, evaluatedFallbackCount, connectorCount, total: legs.length, summary, providers };
}

const roadProviderLabel = () => routingInfo().providers.includes('kakao') ? '카카오 길찾기' : 'OSRM';

function routingDescription() {
  const info = routingInfo();
  if (!info.roadCount) return '구급차는 합성 이동시간에 맞춘 직선 추정 경로를 따릅니다. 실제 도로 경로나 교통을 재현하지 않습니다.';
  return `구급차는 ${roadProviderLabel()} 도로 경로를 따릅니다.${info.connectorCount ? ` 현장·병원 좌표와 도로 사이의 접속은 점선 추정 구간이며 ${number(info.summary.connector_speed_kmh, 10)}km/h로 시간을 추가합니다.` : ''}${info.fallbackCount ? ` 이동 경로를 확보하지 못한 ${info.fallbackCount}개 구간은 점선 직선 추정으로 표시합니다.` : ''}${info.evaluatedFallbackCount > 0 ? ` 후보 탐색까지 포함한 경로 조회 중 ${info.evaluatedFallbackCount}건은 추정값으로 대체됐습니다.` : ''} 저장한 조회 시점의 일반 자동차 예상시간이며 긴급차량 우선통행이나 현재 교통의 지속 갱신을 재현하지 않습니다.`;
}

function normalise(raw) {
  const list = raw.scenarios ?? (raw.scenario ? [raw] : []);
  return list.map((item, idx) => ({ ...item, routing_summary: item.routing_summary ?? item.scenario?.routing_summary ?? raw.routing_summary, scenario: { ...item.scenario, id: item.scenario?.id ?? `scenario_${idx}` }, branches: (item.branches ?? []).map((b, i) => ({ ...b, id: b.id ?? `B${i}`, displayId: `A${i + 1}`, label: b.label ?? b.id ?? `알고리즘 ${i + 1}`, color: COLORS[i % COLORS.length], events: [...(b.events ?? [])].sort((a, z) => when(a) - when(z)), trajectories: b.trajectories ?? [], patient_outcomes: b.patient_outcomes ?? [] })) }));
}

function prepareScenarioOptions() {
  const groups = [
    ['지도 이송·조건 변경 실험', state.scenarios.filter(s => !s.scenario.server_inquiry_only && !s.scenario.server_transport_replay && !s.scenario.online_allocation_comparison)],
    ['서버 문의 정책 검증 기록', state.scenarios.filter(s => s.scenario.server_inquiry_only === true)],
    ['서버 조건 변경 처리 기록', state.scenarios.filter(s => s.scenario.server_transport_replay === true)],
    ['CP-SAT 전체 이송 정책 비교 · 합성 좌표', state.scenarios.filter(s => s.scenario.online_allocation_comparison === true && !s.scenario.real_road_allocation)],
    ['실제 도로 · 5개 배정 정책 시연', state.scenarios.filter(s => s.scenario.real_road_allocation === true)]
  ];
  $('scenario-select').replaceChildren(...groups.filter(([, items]) => items.length).map(([label, items]) => {
    const group = node('optgroup'); group.label = label;
    group.append(...items.map(s => { const option = node('option', '', s.scenario.label ?? s.scenario.id); option.value = s.scenario.id; return option; }));
    return group;
  }));
}

async function readJson(path) { const response = await fetch(path, { cache: 'no-store' }); if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`); return response.json(); }

async function initialise() {
  try {
    const data = await readJson('./map_replay_data.json');
    state.scenarios = normalise(data);
    // 서버 문의 정책 검증 기록 변환본(simulation/server_replay_adapter.py). 없으면 기존 사례만 재생한다.
    try { state.scenarios.push(...normalise(await readJson('./server_replay_data.json'))); } catch (error) { /* optional */ }
    try { state.scenarios.push(...normalise(await readJson('./transport_replay_data.json'))); } catch (error) { /* optional */ }
    try { state.scenarios.push(...normalise(await readJson('./online_allocation_data.json'))); } catch (error) { /* optional */ }
    try { state.scenarios.push(...normalise(await readJson('./road_online_allocation_data.json'))); } catch (error) { /* optional */ }
    if (!state.scenarios.length) throw new Error('재생할 시나리오가 없습니다.');
    $('scenario-total').textContent = `${state.scenarios.length} CASES`;
    prepareScenarioOptions();
    $('scenario-select').disabled = false;
    $('play').disabled = false;
    $('timeline').disabled = false;
    const requested = new URLSearchParams(location.search).get('scenario');
    const defaultScenario = state.scenarios.find(s => s.scenario.real_road_allocation)?.scenario.id ?? state.scenarios[0].scenario.id;
    selectScenario(state.scenarios.some(s => s.scenario.id === requested) ? requested : defaultScenario);
    loadGeography();
  } catch (error) {
    $('load-error').hidden = false;
    $('load-error').textContent = `재생 데이터를 불러오지 못했습니다. 로컬 실행 명령으로 뷰어를 열고 map_replay_data.json 생성 여부를 확인해 주세요. (${error.message})`;
    $('scenario-title').textContent = '시뮬레이션 데이터가 필요합니다';
    $('scenario-description').textContent = '파일을 직접 여는 대신 프로젝트의 로컬 서버를 사용해 주세요.';
  }
}

async function loadGeography() {
  try { state.geography = await readJson('./assets/regional-boundaries.geojson'); } catch (error) { $('map-layer-status').textContent = '배경 파일 없음 · 실제 병원 좌표'; }
  renderGeography();
}

function selectScenario(id) {
  const selected = state.scenarios.find(s => String(s.scenario.id) === String(id));
  if (!selected) return false;
  state.selected = selected;
  mapBranch = 'all';
  basemapEpoch++; mapTiles.clear(); $('basemap-layer').replaceChildren();
  state.playing = false;
  state.time = 0;
  state.patient = 'all';
  const last = Math.max(0, ...selected.branches.flatMap(b => b.events.map(when)), ...selected.branches.flatMap(b => b.trajectories.flatMap(tr => tr.legs.map(l => number(l.arrive_t)))));
  state.duration = Math.max(1, number(selected.duration_minutes, last) || last);
  $('scenario-select').value = String(id);
  $('scenario-title').textContent = selected.scenario.label ?? String(id);
  $('scenario-description').textContent = selected.scenario.summary_description ?? selected.scenario.description ?? '';
  $('patient-count').textContent = `${patients().length}명`;
  $('branch-count').textContent = `${branches().length} ${transportCase() ? 'RECORD' : 'POLICIES'}`;
  document.querySelector('.workspace').classList.toggle('is-multi', patients().length > 1);
  document.querySelector('.workspace').classList.toggle('is-server-case', recordedCase() || onlineCase());
  $('hospital-count').textContent = `${hospitals().length}개 병원`;
  $('seed-label').textContent = `SEED ${selected.scenario.seed ?? '—'}`;
  $('timeline').max = state.duration;
  $('timeline').step = recordedCase() ? 'any' : '0.05';
  $('timeline-start').textContent = recordedCase() ? '환자 정보 접수' : '환자 발생';
  $('timeline-middle').textContent = transportCase() ? '조건 변경·재확인·사람의 목적지 변경' : serverCase() ? '서버 문의·회신·병원 선정' : '119 도착 후 알고리즘 분기';
  $('replay-map').setAttribute('aria-label', transportCase() ? '합성 위치 관측과 서버 수용 조건 변경 처리 지도' : serverCase() ? '합성 병원 위치와 서버 문의 정책별 예상 이송 지도' : '실제 병원 위치와 알고리즘별 구급차 이동 지도');
  if (onlineCase()) $('replay-map').setAttribute('aria-label', realRoadCase() ? '실제 병원 위치와 도로에서 CP-SAT 포함 5개 배정 정책을 비교하는 가상 이송 시연 지도' : '합성 병원 위치와 CP-SAT 포함 5개 배정 정책의 전체 이송 지도');
  $('duration').textContent = clock(state.duration);
  const all = node('option', '', '전체 환자'); all.value = 'all';
  $('patient-select').replaceChildren(all, ...patients().map(p => { const o = node('option', '', `${p.id} · ${p.label ?? (p.required_capabilities ?? []).join(', ') ?? '환자'}`); o.value = p.id; return o; }));
  const claims = [...(selected.scenario.assumptions ?? [])];
  const defaults = serverCase() ? ['서버 문의 정책 검증 기록을 재계산 없이 재생합니다. 병원·환자·회신 조건·이동시간은 모두 합성값이며 병원 위치는 실제 병원이 아닙니다.', '서버 실험은 문의·회신·선정까지 다룹니다. 도착은 예상 도착시각이며 치료와 전원은 기록하지 않았습니다.', '같은 사례를 정책별로 독립 처리한 기록입니다. 이 사례는 유형별 첫 검증 seed로 고정 선택했으며 대표 효과가 아닙니다.'] : ['실제 수집한 병원 위치를 사용합니다. 표시된 진료 역량·운영 자원·환자·응답·장애 사건은 가상 설정입니다.', routingDescription(), '같은 환자 발생과 외생 사건을 각 정책에 적용합니다. 결과는 이 시나리오 안에서의 비교이며 현장 개선율을 뜻하지 않습니다.'];
  if (transportCase()) defaults.splice(0, defaults.length, '격리된 가상 DB에서 실제 서버 기능을 실행한 사건·상태 기록입니다. 병원·환자·자원·시각·위치는 대본상 합성 입력입니다.', '점선 이동은 기록기에 입력한 위치 관측 사이의 보간입니다. 실제 도로·차량 운행·의학적 경과를 재현하지 않습니다.', '대체 수용 응답만으로 목적지가 바뀌지 않습니다. 서버의 사람에 의한 변경 성공 사건 이후에만 새 병원으로 표시합니다.', '기능 흐름 시연이며 알고리즘 개선율 비교가 아닙니다. 도착은 대본상 확인 입력이며 치료·전원·환자 예후는 미기록입니다.');
  if (onlineCase()) defaults.splice(0, defaults.length, '병원·좌표·환자·자원·출동·응답·이동·치료는 전부 합성 입력입니다. 실제 지리·임상 경과를 재현하지 않습니다.', '5개 배정 규칙의 문의 설정과 잠재 회신 환경은 같습니다. 앞선 배정으로 자원이 달라져 실제 수용 회신까지 같지는 않습니다.', 'CP-SAT의 OPTIMAL은 그 시점의 준비 환자 배정에만 적용됩니다. 전체 이송 과정의 최적성을 뜻하지 않습니다.', '각 조건에서 가장 낮은 검증 seed를 결과와 무관하게 선택했습니다. 전체 평균 효과는 별도 보고서를 확인하세요.');
  if (realRoadCase()) defaults.splice(0, defaults.length, `실제 병원 이름·위치와 ${roadProviderLabel()} 도로 경로·예상시간을 사용해 별도로 실행한 시연입니다. 환자·병원의 역량·자원·수용 응답·진료 경과는 가상 설정이며 실제 병원 상황이 아닙니다.`, routingDescription(), '같은 도로 이동 조건과 잠재 응답 환경에서 5개 정책을 재실행했습니다. 앞선 배정으로 자원이 달라지면 이후 수용 응답도 달라집니다.', 'CP-SAT의 OPTIMAL은 현재 배정 묶음에만 적용됩니다. 별도 도로 시연이며 기존 합성 검증 결과를 대체하거나 현장 효과를 증명하지 않습니다.');
  if (priorityComparisonEnabled()) defaults.push('우선순위 4·2·1은 합성 실험군이며 임상 등급이 아닙니다. 미발생 환자는 정책에 제공하지 않으며, 이미 제출한 FIFO 문의와 기존 이송을 취소하거나 빼앗지 않습니다.');
  if (branches().some(b => b.policy?.rule === 'R4')) defaults.push('R4는 같은 분에 유효한 수용 응답을 확보해 확정 준비가 된 환자만 함께 비교합니다. 미래 환자나 미래 회신을 이용하는 전체 기간의 최적 배정은 아닙니다.');
  $('assumptions').replaceChildren(...defaults.map(t => node('li', '', t)));
  document.querySelector('.branch-note').textContent = `의료진 판단을 재현하지 않습니다. 각 분기는 동일한 가상 사건을 독립적으로 처리한 기록입니다. 적응형 정책의 문의 수와 근거는 재생 시각까지 관측한 회신·대기 기록으로 표시합니다.${priorityComparisonEnabled() ? ' 우선순위는 실험군이며, 이미 제출한 FIFO 문의와 기존 이송은 취소하지 않습니다. R4는 같은 분의 확정 준비 환자만 비교합니다.' : ''}`;
  if (transportCase()) document.querySelector('.branch-note').textContent = '실제 서버 처리 결과 한 건을 재생합니다. 시나리오는 서로 다른 기능 조건이며 우열 비교군이 아닙니다. 진행 상태는 재생 시각까지 저장된 스냅샷만 사용합니다.';
  if (onlineCase()) document.querySelector('.branch-note').textContent = '같은 잠재 응답과 외부 사건의 정책 비교입니다. R0/R1/R2는 환자별로 확정하고 R3/C1은 같은 분의 준비 환자를 함께 확정합니다. 배정 결과로 이후 회신이 달라질 수 있습니다. 계산시간은 별도 측정하며 분 단위 모의 시간에 더하지 않습니다.';
  const routing = routingInfo();
  $('environment-description').textContent = transportCase() ? '합성 위치 · 서버 조건 변경 처리 기록' : serverCase() ? '합성 병원 · 직선 추정 · 서버 문의 정책 기록' : routing.roadCount ? '실제 병원 위치 · 도로 경로 · 가상 상황' : '실제 병원 위치 · 직선 추정 · 가상 상황';
  if (onlineCase()) $('environment-description').textContent = realRoadCase() ? `실제 병원·${roadProviderLabel()} 경로 · 가상 상황` : '합성 위치·상황 · CP-SAT 전체 이송 비교';
  $('route-key-label').textContent = routing.roadCount ? '도로 이송' : '직선 추정';
  $('fallback-route-key').hidden = !routing.roadCount || !(routing.fallbackCount || routing.connectorCount || routing.evaluatedFallbackCount);
  document.getElementById('additional-assumptions')?.remove();
  const extra = node('details'); extra.id = 'additional-assumptions'; extra.append(node('summary', '', `시나리오별 세부 설정 ${claims.length}개`));
  const list = node('ul'); list.append(...claims.map(t => node('li', '', typeof t === 'string' ? t : JSON.stringify(t)))); extra.append(list); $('assumptions').after(extra);
  let summary = $('patient-summary'); if (!summary) { summary = node('p', 'patient-summary'); summary.id = 'patient-summary'; document.querySelector('.patient-selector').after(summary); }
  summary.textContent = patients().map(p => recordedCase()
    ? `${p.id} · ${p.label ?? '합성 환자'} / 필요 역량: ${(p.required_capabilities ?? []).join(', ')} / +${clock(p.search_start_t)} 정보 접수·탐색 시작 · 119 출동 과정은 미기록`
    : `${p.id} · ${p.label ?? '가상 환자'} / 필요 역량: ${(p.required_capabilities ?? []).join(', ')} / 신고 +${number(p.dispatch_t)}분 출동 · +${number(p.ambulance_arrive_t)}분 현장 도착 · +${number(p.search_start_t)}분 탐색`).join('\n');
  prepareProjection();
  resetMap();
  renderGeography();
  prepareBranches();
  prepareResources();
  render();
  return true;
}

function prepareProjection() {
  const coords = [...hospitals().map(locOf), ...patients().flatMap(p => [locOf(p), ...(p.ambulance_origin ? [p.ambulance_origin] : [])]), ...branches().flatMap(b => b.trajectories.flatMap(tr => tr.legs.flatMap(l => legGeometry(l).points)))].filter(p => p?.length >= 2 && p.every(Number.isFinite));
  projection = window.ReplayBasemap.createProjection(coords, syntheticLocations());
  project = projection.project;
  renderScale();
}

function renderScale() {
  if (!projection) return;
  const map = $('replay-map'), viewport = window.ReplayBasemap.visibleSvgBounds(map.clientWidth, map.clientHeight);
  const kmPerRadian = 6371 * Math.cos(projection.middleLat * Math.PI / 180);
  const unitsPerKm = projection.factor * state.zoom / kmPerRadian;
  const scaleKm = [.001, .002, .005, .01, .02, .05, .1, .2, .5, 1, 2, 5, 10, 20, 50, 100].filter(km => km * unitsPerKm * viewport.scale <= 105).at(-1) ?? .001;
  const scaleLength = scaleKm * unitsPerKm;
  const scale = svg('g', { transform: `translate(${viewport.left + viewport.width - 140 / viewport.scale} ${viewport.top + viewport.height - 36 / viewport.scale})` });
  scale.append(svg('path', { d: `M0 -5 V0 H${scaleLength} V-5`, fill: 'none', stroke: '#5c7568', 'stroke-width': 1.5 }), svg('text', { x: scaleLength / 2, y: -8, 'text-anchor': 'middle', fill: '#5c7568', 'font-size': 9 }, scaleKm < 1 ? `${scaleKm * 1000} m` : `${scaleKm} km`));
  $('scale-layer').replaceChildren(scale);
}

function geometryPath(geometry) {
  if (!geometry) return '';
  const line = (points, closed = false) => points.map((c, i) => { const p = project([c[1], c[0]]); return `${i ? 'L' : 'M'}${p[0].toFixed(2)},${p[1].toFixed(2)}`; }).join(' ') + (closed ? ' Z' : '');
  if (geometry.type === 'LineString') return line(geometry.coordinates);
  if (geometry.type === 'MultiLineString') return geometry.coordinates.map(c => line(c)).join(' ');
  if (geometry.type === 'Polygon') return geometry.coordinates.map(c => line(c, true)).join(' ');
  if (geometry.type === 'MultiPolygon') return geometry.coordinates.flatMap(p => p.map(c => line(c, true))).join(' ');
  return '';
}

function renderGeography() {
  const base = $('geography-layer'); base.replaceChildren();
  const boundaries = syntheticLocations() ? [] : state.geography?.features ?? [];
  boundaries.forEach((f, i) => { const path = geometryPath(f.geometry); if (path) base.append(svg('path', { d: path, fill: ['#e5e9df', '#e9ece2', '#eceee5', '#e3e9df'][i % 4], stroke: '#c7cebe', 'stroke-width': 1, 'fill-rule': 'evenodd', 'vector-effect': 'non-scaling-stroke' })); });
  (syntheticLocations() ? [] : state.roads?.features ?? []).forEach(f => { const highway = f.properties?.highway ?? ''; const major = /motorway|trunk/.test(highway); base.append(svg('path', { d: geometryPath(f.geometry), fill: 'none', stroke: major ? '#d5c6a3' : '#d1d6bf', 'stroke-width': major ? 2.3 : 1.1, 'stroke-linecap': 'round', 'vector-effect': 'non-scaling-stroke' })); });
  const routing = routingInfo();
  $('map-layer-status').textContent = recordedCase() ? '합성 병원 위치 · 직선 추정 이동' : `${boundaries.length ? '실제 행정경계' : '실제 병원 좌표'}${state.roads?.features?.length ? ' · 주요도로' : ''}${routing.roadCount ? ` · 도로 ${routing.roadCount}구간${routing.evaluatedFallbackCount ? ' · 조회 일부 추정' : ''}` : ' · 직선 추정'}`;
  if (routing.roadCount) $('map-layer-status').textContent = `${roadProviderLabel()} 도로 경로${routing.evaluatedFallbackCount ? ' · 일부 추정' : ''}`;
  if (onlineCase() && !realRoadCase()) $('map-layer-status').textContent = '합성 좌표 · 직선 추정 이동';
  const attribution = document.querySelector('.map-attribution');
  const sourceLink = node('a', '', 'geoBoundaries / citypopulation.de'); sourceLink.href = 'https://www.geoboundaries.org/index.html#citation'; sourceLink.target = '_blank'; sourceLink.rel = 'noopener noreferrer';
  const licenseLink = node('a', '', 'CC BY 3.0'); licenseLink.href = 'https://creativecommons.org/licenses/by/3.0/'; licenseLink.target = '_blank'; licenseLink.rel = 'noopener noreferrer';
  attribution.replaceChildren(document.createTextNode('경계: '), sourceLink, document.createTextNode(' · '), licenseLink, document.createTextNode(' · 2020'));
  if (routing.roadCount) {
    const osm = node('a', '', '© OpenStreetMap contributors'); osm.href = 'https://www.openstreetmap.org/copyright'; osm.target = '_blank'; osm.rel = 'noopener noreferrer';
    const osrm = node('a', '', 'OSRM'); osrm.href = 'https://project-osrm.org/'; osrm.target = '_blank'; osrm.rel = 'noopener noreferrer';
    const editMap = node('a', '', '지도 오류 수정'); editMap.href = 'https://www.openstreetmap.org/fixthemap'; editMap.target = '_blank'; editMap.rel = 'noopener noreferrer';
    if (routing.providers.includes('kakao')) {
      const kakao = node('a', '', '경로 © Kakao Mobility'); kakao.href = 'https://developers.kakaomobility.com/'; kakao.target = '_blank'; kakao.rel = 'noopener noreferrer'; attribution.append(document.createTextNode(' · '), kakao);
    } else attribution.append(document.createTextNode(' · 도로: '), osm, document.createTextNode(' / '), osrm);
    attribution.append(document.createTextNode(' · '), editMap);
  } else attribution.append(document.createTextNode(' · 직선 추정 경로'));
  if (syntheticLocations()) attribution.textContent = '합성 좌표·직선 경로 · 실제 지리 재현 아님';
  else if (basemapMode === 'osm') {
    const osm = node('a', '', '지도 © OpenStreetMap contributors'); osm.href = 'https://www.openstreetmap.org/copyright'; osm.target = '_blank'; osm.rel = 'noopener noreferrer';
    attribution.prepend(osm, document.createTextNode(' · '));
  }
  const labels = [{ name: '수원', lat: 37.26, lon: 127.0 }, { name: '용인', lat: 37.25, lon: 127.19 }, { name: '화성', lat: 37.15, lon: 126.90 }, { name: '오산', lat: 37.15, lon: 127.06 }, { name: '평택', lat: 36.99, lon: 127.06 }, { name: '안성', lat: 37.01, lon: 127.24 }, { name: '아산', lat: 36.82, lon: 126.95 }, { name: '천안', lat: 36.82, lon: 127.16 }];
  $('region-label-layer').replaceChildren(...(syntheticLocations() ? [] : labels).map(l => { const [x, y] = project([l.lat, l.lon]); return svg('text', { x, y, class: 'region-label', 'text-anchor': 'middle' }, l.name); }));
  scheduleBasemap(true);
}

function scheduleBasemap(immediate = false) {
  clearTimeout(basemapTimer);
  if (immediate) renderBasemap();
  else basemapTimer = setTimeout(renderBasemap, 140);
}

function updateBasemapStatus() {
  const notice = $('basemap-status');
  if (syntheticLocations() || basemapMode !== 'osm') { notice.hidden = true; return; }
  const tiles = [...mapTiles.values()];
  const failed = tiles.filter(tile => tile.dataset.loaded === 'error').length;
  const loading = tiles.some(tile => !tile.dataset.loaded);
  notice.hidden = !loading && !failed;
  notice.textContent = failed ? '일부 지도 배경을 불러오지 못했습니다. 경로 재생은 계속됩니다.' : '지도 배경 불러오는 중…';
}

function renderBasemap() {
  if (!projection || !state.selected) return;
  const enabled = !syntheticLocations() && basemapMode === 'osm';
  $('basemap-select').disabled = syntheticLocations();
  $('basemap-select').value = enabled ? 'osm' : 'plain';
  $('basemap-select').title = syntheticLocations() ? '가상 좌표 사례에는 실제 지도 배경을 사용하지 않습니다.' : '지도 배경은 인터넷 연결이 필요합니다.';
  $('map-shell').dataset.basemap = enabled ? 'osm' : 'plain';
  if (!enabled) { basemapEpoch++; mapTiles.clear(); $('basemap-layer').replaceChildren(); updateBasemapStatus(); return; }
  const map = $('replay-map'), viewport = window.ReplayBasemap.visibleSvgBounds(map.clientWidth, map.clientHeight);
  const tiles = window.ReplayBasemap.visibleTiles(projection, viewport, state.zoom, state.pan);
  const keys = new Set(tiles.map(tile => tile.key)), epoch = basemapEpoch;
  for (const [key, image] of mapTiles) if (!keys.has(key)) { image.remove(); mapTiles.delete(key); }
  for (const tile of tiles) {
    if (mapTiles.has(tile.key)) continue;
    const image = svg('image', { x: tile.left, y: tile.top, width: tile.size + .02, height: tile.size + .02, preserveAspectRatio: 'none', 'data-tile': tile.key });
    const finish = status => { if (epoch !== basemapEpoch || mapTiles.get(tile.key) !== image) return; image.dataset.loaded = status; updateBasemapStatus(); };
    image.addEventListener('load', () => finish('true'), { once: true });
    image.addEventListener('error', () => finish('error'), { once: true });
    mapTiles.set(tile.key, image); $('basemap-layer').append(image);
    // Native browser requests retain Referer/User-Agent and normal HTTP caching.
    // Only tiles intersecting the current viewport are loaded, with no prefetch.
    image.setAttribute('href', `https://tile.openstreetmap.org/${tile.z}/${tile.x}/${tile.y}.png`);
  }
  updateBasemapStatus();
}

function prepareBranches() {
  const all = node('button', 'policy-key', '전체'); all.dataset.branch = 'all'; all.setAttribute('aria-pressed', String(mapBranch === 'all'));
  all.addEventListener('click', () => selectMapBranch('all'));
  $('map-policy-legend').replaceChildren(all, ...branches().map(b => {
    const key = node('button', 'policy-key'); key.style.setProperty('--branch-color', b.color);
    key.dataset.branch = b.id; key.setAttribute('aria-pressed', String(mapBranch === b.id));
    key.append(node('i'), node('b', '', b.displayId)); key.title = b.label; key.setAttribute('aria-label', `${b.displayId} ${b.label}의 지도만 보기`);
    key.addEventListener('click', () => selectMapBranch(b.id)); return key;
  }));
  $('branch-cards').replaceChildren(...branches().map((b, i) => {
    const card = node('article', 'branch-card'); card.style.setProperty('--branch-color', b.color); card.dataset.branch = b.id;
    const head = node('div', 'branch-card-top'); head.append(node('span', 'branch-id', b.displayId), node('h3', '', b.label));
    const p = b.policy ?? {}; const ranking = p.inquiry_ranking === 'recent_load' ? ` · 최근 문의 분산 · 가중치 ${p.load_weight ?? '—'} · 최근 ${p.load_window ?? '—'}분` : '';
    const inquirySize = p.observed_inquiry ? `병원별 관측 회신·대기시간으로 추가 문의 판단 · 최대 ${p.k ?? '—'}곳 · ${p.observed_inquiry.hedge_after ?? '—'}분 후 추가 검토 · 과거 관측만 사용` : p.adaptive_inquiry ? '관측 응답에 따라 동시 문의 수 조절' : `동시 ${p.k ?? '—'}곳`;
    const description = p.transport_record ? p.description : p.server_policy ? `서버 문의 정책 ${p.server_policy.name} · ${p.server_policy.description} · 수용 응답 확보 후 확정` : `규칙 ${p.rule ?? '—'} · ${inquirySize} · 확정 대기 ${p.wait ?? '—'}분${ranking}${p.notify === false ? ' · 변경 알림 끔' : ''}`;
    const phase = node('div', 'branch-state'); phase.append(node('i', 'state-dot'), node('span', 'phase', '발생 대기'));
    const stats = node('div', 'branch-stats'); ['문의', '확정', transportCase() ? '목적지 변경' : serverCase() ? '미회신' : '전원'].forEach((label, j) => { const s = node('span', '', label); const v = node('strong', `stat-${j}`, '0'); s.append(v); stats.append(s); });
    const progress = node('div', 'branch-progress');
    progress.setAttribute('aria-label', '현재 시각 기준 환자 집계');
    const patientList = node('ul', 'branch-patients');
    patientList.setAttribute('aria-label', `${b.label} 환자별 진행`);
    const inquiryDecision = node('div', 'branch-inquiry-decision'); inquiryDecision.hidden = !(p.adaptive_inquiry || p.observed_inquiry || serverObserved(p));
    inquiryDecision.setAttribute('aria-label', '현재 시각까지 관측한 정보로 내린 문의 수 판단');
    const settings = node('details', 'branch-settings'); settings.append(node('summary', '', '배정·문의 정책'), node('p', 'branch-policy', description));
    const patientDetails = node('details', 'branch-patient-details'); patientDetails.hidden = patients().length < 2;
    patientDetails.append(node('summary', '', `환자별 진행 보기 · ${patients().length}명`), patientList);
    const status = node('div', 'branch-status-row'); status.append(phase, stats);
    const detail = node('details', 'branch-detail'); detail.open = patients().length < 2 && i === 0;
    if (onlineCase() && p.rule === 'C1') detail.open = true;
    detail.append(node('summary', '', '진행 상황 · 판단 근거'), node('p', 'branch-destination', '아직 병원이 정해지지 않았습니다'), progress, inquiryDecision, patientDetails, node('p', 'branch-latest', '환자 발생 전'), settings);
    card.append(head, status, detail);
    const allocation = node('div', 'branch-inquiry-decision branch-allocation-decision');
    allocation.hidden = !onlineCase(); allocation.setAttribute('aria-label', '현재 시각까지의 배정 계산');
    detail.append(allocation);
    if (priorityPolicyEnabled(p)) {
      const policyParts = [];
      if (p.priority_order && p.priority_order !== 'none') policyParts.push(priorityOrderNames[p.priority_order] ?? p.priority_order);
      if (p.high_priority_hedge_after != null) policyParts.push(`4군 추가 문의 검토 ${minutes(p.high_priority_hedge_after)} 후`);
      if (p.rule === 'R4') policyParts.push('R4: 같은 분의 확정 준비 환자 공동 배정 · 4→2→1군 미배정 수 우선 최소화');
      const policyNote = node('p', 'branch-priority-policy', policyParts.join(' · '));
      policyNote.append(node('small', '', '4·2·1은 실험군입니다. 이미 제출한 FIFO 문의와 기존 이송은 취소하지 않습니다.'));
      card.querySelector('.branch-policy').after(policyNote);
    }
    return card;
  }));
}

function phaseFor(branch, patient) {
  if (transportCase()) {
    const snapshot = transportSnapshot(branch)?.patients?.find(p => String(p.id) === String(patient.id));
    if (!snapshot) return { label: '정보 접수 대기', hospital: null };
    const review = snapshot.transport_review?.state;
    const job = snapshot.transport_reassignment?.state;
    const base = ({ WAITING: '후보 탐색·응답 대기', CONFIRMED: '수용 확정·출발 대기', IN_TRANSIT: '병원 이송 중', ARRIVED: '도착 확인 기록', COMPLETED: '이송 종료', CANCELLED: '이송 취소' })[snapshot.state] ?? snapshot.state;
    const label = snapshot.state !== 'IN_TRANSIT' ? base : review === 'RECHECK_REQUIRED' ? '병원에 수용 조건 재확인 필요' : job === 'AWAITING_CHANGE' ? '대체 수용 확보·사람의 변경 대기' : snapshot.needs_adjustment ? (job === 'EXHAUSTED' ? '대체 탐색 종료·담당자 확인 필요' : '기존 목적지 유지·대체병원 확인 중') : base;
    return { label, hospital: snapshot.destination_hospital_id, monitor: review, alternative: job };
  }
  if (state.time < number(patient.occur_t)) return { label: serverCase() ? '정보 접수 대기' : '발생 대기', hospital: null };
  if (!serverCase()) {
    if (state.time < number(patient.dispatch_t, number(patient.occur_t))) return { label: '환자 발생·출동 대기', hospital: null };
    if (state.time < number(patient.ambulance_arrive_t)) return { label: '119 출동 중', hospital: null };
    if (state.time < number(patient.search_start_t, patient.ambulance_arrive_t)) return { label: '현장 평가·처치', hospital: null };
  }
  let phase = { label: '후보 탐색·응답 대기', hospital: null };
  for (const e of seen(branch).filter(e => patientId(e) === String(patient.id))) {
    const type = typeOf(e); const hid = hospitalId(e);
    if (type === 'ask') { if (!phase.label.includes('이송') && !phase.label.includes('치료') && !phase.label.includes('전원')) phase.label = '병원 응답 대기'; }
    if (type === 'accept' || type === 'accepted') { if (!phase.hospital) phase.label = '수용 응답 평가'; }
    if (type === 'commit') phase = { label: serverCase() ? '예상 이송 중' : e.transfer ? '병원 간 전원 중' : '병원 이송 중', hospital: hid };
    if (type === 'redirect') phase = { label: '대체병원 이송 중', hospital: hid || phase.hospital };
    if (type === 'notify' || type === 'recheck') phase.label = '수용 조건 재확인';
    if (type === 'arrive') phase = { label: serverCase() ? '예상 도착시각 도달' : '병원 도착', hospital: hid || phase.hospital };
    if (type === 'arrival_unable') phase = { label: '치료조건 재확인', hospital: hid || phase.hospital };
    if (type === 'transfer_search') phase.label = '전원 병원 탐색';
    if (type === 'transfer_depart') phase = { label: '병원 간 전원 중', hospital: hid || phase.hospital };
    if (type === 'treatment_start') phase = { label: '치료 중', hospital: hid || phase.hospital };
    if (type === 'treatment_complete') phase = { label: '치료 완료', hospital: hid || phase.hospital };
    if (type === 'no_compatible' || type === 'no_candidate') phase = { label: '조건 충족 후보 없음', hospital: null };
    if (type === 'observation_end') phase = { label: ({ UNASSIGNED: '관찰 종료·미배정', NOT_TREATED: '관찰 종료·치료 미시작', INCOMPLETE: '관찰 종료·치료 중' })[e.state] ?? '관찰 종료', hospital: hid || phase.hospital };
  }
  return phase;
}

eventNames.observation_end = '관찰 종료';
function describe(e) {
  const pid = patientId(e), hid = hospitalId(e);
  const title = serverCase() && typeOf(e) === 'arrive' ? '예상 병원 도착' : e.server_type === 'selection_deadline' || typeOf(e) === 'selection_deadline' ? '병원 선정 기한 종료 · 미선정' : serverCase() && typeOf(e) === 'patient_occurs' ? '환자 정보 접수·탐색 시작' : eventNames[typeOf(e)] ?? typeOf(e).replaceAll('_', ' ');
  const prefix = pid ? `${pid} · ` : e.kind === 'background' ? '고정 외생 업무 · ' : '';
  return `${prefix}${title}${hid ? ` → ${hospitalName(hid)}` : ''}`;
}
function inquiryPolicyDecision(branch) { return seen(branch).filter(e => typeOf(e) === 'inquiry_policy' && (state.patient === 'all' || patientId(e) === state.patient)).at(-1) ?? null; }
function observedInquiryDecision(e) { return e?.policy_kind === 'observed'; }
function inquiryDecisionTitle(e) { return observedInquiryDecision(e) ? `${e.open_requests == null ? '진행 중 문의 수 미저장' : `진행 중 ${number(e.open_requests)}곳`} · 이번 추가 ${(e.send ?? []).length}곳` : `동시 문의 한도 ${number(e.limit)}곳`; }
function inquiryDecisionReason(e) { return (observedInquiryDecision(e) ? observedInquiryReasons : inquiryReasons)[e.reason] ?? e.reason ?? '관측 정보 반영'; }
function inquiryEvidence(e) {
  const data = e.evidence ?? {}, parts = [];
  if (e.patient_priority != null) parts.push(`실험 우선순위 ${number(e.patient_priority)}군`);
  if (e.effective_hedge_after != null) parts.push(`이 환자 추가 문의 검토 ${minutes(e.effective_hedge_after)} 후`);
  if (e.priority_order && e.priority_order !== 'none') parts.push(priorityOrderNames[e.priority_order] ?? e.priority_order);
  if (observedInquiryDecision(e)) {
    if (serverCase() && !Object.keys(data).length) return '이 판단의 상세 근거는 원본 실험에 미저장';
    if (data.global_reply_samples != null) parts.push(`최근 전체 회신 ${number(data.global_reply_samples)}건`);
    if (data.global_median_reply_minutes != null) parts.push(`관측 중앙값 ${minutes(data.global_median_reply_minutes)}`);
    if (data.oldest_pending_age_minutes != null) parts.push(`현재 가장 오래된 문의 ${minutes(data.oldest_pending_age_minutes)}`);
    return parts.join(' · ');
  }
  if (data.median_reply_minutes != null && Number.isFinite(Number(data.median_reply_minutes))) parts.push(`관측 회신 중앙값 ${minutes(data.median_reply_minutes)}`);
  if (data.reply_samples != null) parts.push(`회신 ${number(data.reply_samples)}건`);
  if (data.timeout_samples != null) parts.push(`시간 초과 ${number(data.timeout_samples)}건`);
  if (data.aged_pending_count != null) parts.push(`장기 대기 ${number(data.aged_pending_count)}건`);
  return parts.join(' · ');
}
function detail(e) {
  if (transportCase()) {
    const parts = [e.server_event_id != null ? `서버 사건 #${e.server_event_id} · ${e.server_type}` : '재생 관찰 경계', e.actor ? `처리: ${e.actor}` : '', e.reason ?? ''];
    if (e.evidence?.length) parts.push(e.evidence.map(transportIssue).join(' / '));
    if (e.server_type === 'resource_updated') parts.push(`${resourceNames[e.payload.resource_type] ?? e.payload.resource_type} · 용량 ${e.payload.capacity} · 점유 ${e.payload.occupied}`);
    return parts.filter(Boolean).join(' · ');
  }
  if (typeOf(e) === 'inquiry_policy') return [inquiryDecisionTitle(e), inquiryDecisionReason(e), inquiryEvidence(e)].filter(Boolean).join(' · ');
  if (typeOf(e) === 'inquiry_feedback') return `${e.outcome === 'reply' ? '회신 도착' : '응답 기한 경과'} · 문의 후 ${minutes(number(e.observed_t, when(e)) - number(e.submitted_t))} · 수용 확정과 별개인 관측 기록`;
  const value = e.reason ?? e.detail ?? e.message ?? e.failure_reason ?? e.resource ?? ''; return typeof value === 'string' ? value : JSON.stringify(value);
}

function currentCounts(branch, relevant = patients()) {
  const events = seen(branch), ids = new Set(relevant.map(p => String(p.id)));
  const occurred = relevant.filter(p => number(p.occur_t) <= state.time);
  const committed = new Set(events.filter(e => typeOf(e) === 'commit' && ids.has(patientId(e))).map(patientId));
  const completed = new Set(events.filter(e => typeOf(e) === 'treatment_complete' && ids.has(patientId(e))).map(patientId));
  const started = new Set(events.filter(e => typeOf(e) === 'treatment_start' && ids.has(patientId(e))).map(patientId));
  const highWaiting = occurred.filter(p => number(p.priority) === 4 && number(p.search_start_t) <= state.time && !committed.has(String(p.id)));
  const priorityWaitingCounts = Object.fromEntries(PRIORITY_TIERS.map(tier => [tier,
    occurred.filter(p => number(p.priority) === tier && number(p.search_start_t) <= state.time && !started.has(String(p.id))).length]));
  return { occurred: occurred.length, committed: committed.size, completed: completed.size, highWaiting: highWaiting.length, priorityWaitingCounts };
}

function priorityUntreatedCounts(branch, relevant = patients()) {
  // Final outcomes must never leak into the in-progress counters.
  if (state.time < state.duration) return null;
  const ids = new Set(relevant.map(p => String(p.id)));
  const allIncluded = patients().every(p => ids.has(String(p.id)));
  const outcomes = branch.patient_outcomes.filter(p => ids.has(String(p.id)));
  return Object.fromEntries(PRIORITY_TIERS.map(tier => [tier,
    tier === 4 && allIncluded && branch.metrics?.high_priority_untreated != null ? number(branch.metrics.high_priority_untreated)
      : outcomes.filter(p => number(p.priority ?? patients().find(input => String(input.id) === String(p.id))?.priority) === tier && p.treatment_start_t == null).length]));
}

function renderBranches() {
  branches().forEach(b => {
    const card = [...$('branch-cards').children].find(c => c.dataset.branch === String(b.id));
    const events = seen(b);
    const inquiryDecision = inquiryPolicyDecision(b), decisionBox = card.querySelector('.branch-inquiry-decision');
    if (b.policy?.adaptive_inquiry || b.policy?.observed_inquiry || serverObserved(b.policy)) {
      decisionBox.replaceChildren(node('strong', '', inquiryDecision ? inquiryDecisionTitle(inquiryDecision) : '문의 수 판단 대기'));
      if (inquiryDecision) {
        decisionBox.append(node('span', '', `${clock(when(inquiryDecision))} · ${patientId(inquiryDecision)} · ${inquiryDecisionReason(inquiryDecision)}`));
        const evidence = inquiryEvidence(inquiryDecision); if (evidence) decisionBox.append(node('small', '', evidence));
        if (observedInquiryDecision(inquiryDecision)) {
          const data = inquiryDecision.evidence ?? {}, candidate = data.candidate_scores?.[0], pending = data.pending_scores?.[0];
          if (candidate) {
            decisionBox.append(node('small', 'inquiry-candidate', `후보 ${hospitalName(candidate.hospital)} · 추정 회신 ${minutes(candidate.estimated_reply_minutes)} · 이동 ${minutes(candidate.travel_minutes)} · 비용 ${minutes(candidate.score_minutes)}`));
            const history = data.hospitals?.find(h => h.hospital === candidate.hospital);
            if (history) decisionBox.append(node('small', 'inquiry-hospital-evidence', `이 병원 회신 ${number(history.reply_samples)}건 · 시간 초과 ${number(history.timeout_samples)}건 · 추정 근거: ${{ hospital: '병원별 관측', global: '전체 관측', prior: '실험 기본값' }[history.reply_estimate_source] ?? '관측 기록'}`));
          }
          if (pending) decisionBox.append(node('small', 'inquiry-pending', `진행 중 ${hospitalName(pending.hospital)} · 경과 ${minutes(pending.age_minutes)} · 남은 회신 비용 ${minutes(pending.remaining_cost_minutes)} · 비용 ${minutes(pending.score_minutes)}`));
          if ((inquiryDecision.send ?? []).length) decisionBox.append(node('small', 'inquiry-sent', `이번 문의: ${inquiryDecision.send.map(hospitalName).join(' · ')}`));
          decisionBox.append(node('small', 'inquiry-estimate-note', '과거 회신·현재 대기로 계산한 비교 비용이며 수용 여부와 도착시각을 보장하지 않습니다.'));
        }
      } else decisionBox.append(node('small', '', '재생 시각까지 기록된 판단이 없습니다.'));
    }
    const relevant = patients().filter(p => state.patient === 'all' || String(p.id) === state.patient);
    card.querySelector('.branch-patient-details > summary').textContent = `환자별 진행 보기 · ${relevant.length}명`;
    const phases = relevant.map(p => ({ ...phaseFor(b, p), id: p.id }));
    const names = [...new Set(phases.map(p => p.label))];
    card.querySelector('.phase').textContent = names.length === 1 ? names[0] : recordedCase() ? `${phases.filter(p => p.hospital).length}/${phases.length}명 수용 확정` : `${phases.filter(p => p.label === '치료 완료').length}/${phases.length}명 치료 완료`;
    const dests = [...new Set(phases.map(p => p.hospital).filter(Boolean))];
    card.querySelector('.branch-destination').textContent = dests.length ? dests.map(hospitalName).join(' · ') : '아직 병원이 정해지지 않았습니다';
    const counts = currentCounts(b, relevant);
    const progress = [
      [`확정 ${counts.committed}/${counts.occurred}명`, ''],
      ...(recordedCase() ? [] : [[`높은 우선순위 미확정 ${counts.highWaiting}명`, counts.highWaiting ? 'needs-attention' : ''], [`완료 ${counts.completed}명`, '']])
    ];
    if (priorityComparisonEnabled()) {
      const end = state.time >= state.duration;
      const treatmentCounts = end ? priorityUntreatedCounts(b, relevant) : counts.priorityWaitingCounts;
      for (const tier of PRIORITY_TIERS) progress.push([
        `${end ? '치료 미시작' : '첫 치료 시작 대기'} ${tier}군 ${treatmentCounts[tier]}명`,
        tier === 4 && treatmentCounts[tier] ? 'needs-attention priority-waiting' : 'priority-waiting'
      ]);
    }
    const risk = resourceRiskSnapshot(b);
    if (risk) {
      const relevantIds = new Set(relevant.map(p => String(p.id)));
      const affected = (risk.patients ?? []).filter(id => relevantIds.has(String(id))).length;
      progress.push([`자원조건 재확인 ${affected}명`, affected ? 'needs-attention' : '']);
    }
    const queue = inquirySnapshot(b);
    if (queue && inquiryEnabled()) {
      const total = inquiryTotals(queue);
      progress.push([`문의 처리 ${total.processing} · 대기 ${total.waiting}건`, total.waiting ? 'queue-attention' : '']);
    }
    card.querySelector('.branch-progress').replaceChildren(...progress.map(([label, cls]) => node('span', cls, label)));
    card.querySelector('.branch-patients').hidden = patients().length < 2;
    card.querySelector('.branch-patients').replaceChildren(...relevant.map(patient => {
      const status = phaseFor(b, patient), li = node('li'); li.dataset.patient = patient.id;
      const id = node('button', 'patient-focus', `${patient.id}${number(patient.priority) === 4 ? ' · 높음' : ''}`);
      id.title = `${patient.id}만 지도에 표시`; id.addEventListener('click', () => selectPatient(String(patient.id)));
      const text = node('span', 'patient-current'); text.append(node('strong', '', status.label), node('small', '', status.hospital ? hospitalName(status.hospital) : '목적지 미정'));
      li.append(id, text); return li;
    }));
    card.querySelector('.stat-0').textContent = events.filter(e => typeOf(e) === 'ask').length;
    card.querySelector('.stat-1').textContent = new Set(events.filter(e => typeOf(e) === 'commit').map(patientId)).size;
    card.querySelector('.stat-2').textContent = events.filter(e => typeOf(e) === (transportCase() ? 'redirect' : serverCase() ? 'no_response' : 'transfer_depart')).length;
    const latest = events.filter(e => state.patient === 'all' || !patientId(e) || patientId(e) === state.patient).at(-1);
    card.querySelector('.branch-latest').textContent = latest ? `${clock(when(latest))}  ${describe(latest)}` : serverCase() ? '환자 정보 접수 전' : '환자 발생 전';
    const allocationBox = card.querySelector('.branch-allocation-decision');
    if (onlineCase()) {
      const decisions = b.allocation_decisions ?? [], decision = decisions.filter(d => number(d.t) <= state.time + 1e-7).at(-1);
      allocationBox.replaceChildren(node('strong', '', decision ? `${clock(decision.t)} · ${(decision.inputs?.patients ?? []).length}명 배정 판단` : '배정 계산 전'));
      if (decision) {
        const record = (b.solver_records ?? []).find(r => r.decision_index === decisions.indexOf(decision));
        const names = { cp_sat: 'CP-SAT', cp_sat_fallback: 'CP-SAT · 순차 대체', exact_enumeration: '정확 열거', greedy_fallback: '순차 대체', first_accept: '첫 수용 순서', nearest_current_offer: '예상 도착 우선', competition_aware: '자원 경합 고려', sequential: '환자별 선택', single: '환자별 선택' };
        allocationBox.append(node('span', '', `${names[decision.method] ?? decision.method ?? decision.rule}${record ? ` · ${record.status}` : ''}`));
        if (record) allocationBox.append(node('small', '', record.optimal_proven ? '현재 배정 묶음의 두 목적 최적성 증명 · 전체 이송의 최적성 아님' : `현재 묶음 최적성 미증명${record.fallback ? ' · 검증된 대체안 사용' : ''}`));
      }
    }
  });
}

function trajectoryPosition(tr, patient) {
  if (state.time < number(patient?.occur_t)) return null;
  const legs = [...(tr?.legs ?? [])].sort((a, b) => number(a.depart_t) - number(b.depart_t));
  let point = patient?.ambulance_origin ?? patient?.location;
  for (const leg of legs) {
    if (state.time < number(leg.depart_t)) break;
    if (state.time >= number(leg.arrive_t)) point = routeProgress(leg, 1).point ?? leg.destination;
    else { const ratio = Math.min(1, Math.max(0, (state.time - number(leg.depart_t)) / Math.max(.0001, number(leg.arrive_t) - number(leg.depart_t)))); point = routeProgress(leg, ratio).point ?? leg.origin; break; }
  }
  return point;
}

function renderMap() {
  // Keep map symbols legible when the shared SVG is fitted into a small panel.
  const map = $('replay-map'), fit = Math.min(map.clientWidth / 1000, map.clientHeight / 720);
  const symbolScale = Math.min(2.8, Math.max(1, 1 / Math.max(.1, fit))) / state.zoom;
  $('map-shell').dataset.details = String(mapDetails);
  $('map-detail-toggle').setAttribute('aria-pressed', String(mapDetails));
  $('map-detail-toggle').textContent = mapDetails ? '핵심만 보기' : '상세 표시';
  document.querySelector('.details-key').hidden = !mapDetails;
  const routes = [], inquiries = [], markers = [], ambulanceMarkers = [], patientMarkers = [];
  const statuses = new Map();
  const outages = new Map();
  if (branches()[0]) seen(branches()[0]).forEach(e => { if (typeOf(e) === 'resource_unavailable') outages.set(hospitalId(e), e); if (['resource_restored', 'restored'].includes(typeOf(e))) outages.delete(hospitalId(e)); });
  if (transportCase()) for (const p of transportSnapshot(branches()[0])?.patients ?? []) {
    if (['RECHECK_REQUIRED', 'SEARCHING', 'AWAITING_CHANGE', 'EXHAUSTED'].includes(p.transport_review?.state)) outages.set(p.destination_hospital_id, { type: 'transport_review_pending' });
  }
  branches().forEach((b, bi) => {
    if (mapBranch !== 'all' && mapBranch !== b.id) return;
    const events = seen(b);
    const responses = new Map();
    const responseTypes = serverCase() ? ['ask', 'accept', 'decline', 'accepted', 'rejected', 'timeout', 'no_response', 'reply_suppressed', 'review_cancelled'] : ['ask', 'accept', 'decline', 'accepted', 'rejected', 'timeout'];
    events.filter(e => state.patient === 'all' || patientId(e) === state.patient || !patientId(e)).forEach(e => { const hid = hospitalId(e); if (hid && responseTypes.includes(typeOf(e))) responses.set(`${patientId(e)}:${hid}`, e); });
    for (const e of responses.values()) {
      const hid = hospitalId(e); if (!statuses.has(hid)) statuses.set(hid, []); statuses.get(hid).push({ color: b.color, type: typeOf(e), branch: b.id, index: bi, t: when(e) });
      if (!mapDetails) continue;
      const alreadyCommitted = events.some(x => patientId(x) === patientId(e) && typeOf(x) === 'commit' && (serverCase() || when(x) >= when(e)));
      const pending = serverCase() ? ['ask', 'no_response'].includes(typeOf(e)) : typeOf(e) === 'ask';
      const selectionEnded = serverCase() && events.some(x => patientId(x) === patientId(e) && (typeOf(x) === 'observation_end' || typeOf(x) === 'selection_deadline' || x.server_type === 'selection_deadline'));
      if (transportCase()) {
        const closed = events.some(x => x.inquiry_id === e.inquiry_id && when(x) >= when(e) && ['accept', 'decline', 'inquiry_closed', 'redirect', 'commit'].includes(typeOf(x)));
        if (typeOf(e) !== 'ask' || closed) continue;
      } else if (!pending || alreadyCommitted || selectionEnded || (!serverCase() && state.time - when(e) > number(b.policy?.timeout, 10))) continue;
      const patient = patients().find(p => String(p.id) === patientId(e)); const hospital = hospitalById(hid); if (!patient || !hospital) continue;
      const trajectory = b.trajectories.find(tr => String(tr.patient) === String(patient.id));
      const start = project(trajectoryPosition(trajectory, patient) ?? patient.location), finish = project(locOf(hospital));
      inquiries.push(svg('path', { d: `M${start} L${finish}`, fill: 'none', stroke: b.color, 'stroke-width': 1.25, 'stroke-dasharray': '5 5', opacity: .55, 'vector-effect': 'non-scaling-stroke' }));
    }
    b.trajectories.forEach(tr => {
      const patient = patients().find(p => String(p.id) === String(tr.patient)); if (!patient || (state.patient !== 'all' && String(patient.id) !== state.patient)) return;
      const phase = phaseFor(b, patient);
      const sharedDispatch = state.time < number(patient.search_start_t, patient.ambulance_arrive_t);
      if (state.time < number(patient.dispatch_t, patient.occur_t) || (sharedDispatch && bi > 0 && mapBranch === 'all')) return;
      const moveColor = sharedDispatch ? '#36796c' : b.color;
      for (const leg of tr.legs ?? []) {
        if (state.time < number(leg.depart_t)) continue;
        const ratio = Math.min(1, Math.max(0, (state.time - number(leg.depart_t)) / Math.max(.0001, number(leg.arrive_t) - number(leg.depart_t))));
        if (leg.kind !== 'dispatch' || bi === 0 || mapBranch !== 'all') for (const part of routeParts(leg, ratio)) {
          const path = part.points.map((point, i) => `${i ? 'L' : 'M'}${project(point)}`).join(' ');
          const road = part.kind === 'road';
          const route = svg('path', { d: path, fill: 'none', stroke: leg.kind === 'dispatch' ? '#36796c' : b.color, 'stroke-width': leg.kind === 'dispatch' ? 1.8 : 3.5, 'stroke-dasharray': road ? '' : '7 4', opacity: leg.kind === 'dispatch' ? .5 : .9, 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'vector-effect': 'non-scaling-stroke', 'data-route-kind': part.kind, 'data-branch': b.id });
          route.append(svg('title', {}, road ? `실제 도로 경로 · ${leg.provider ?? 'OSRM'}${leg.road_distance_meters != null ? ` · ${(Number(leg.road_distance_meters) / 1000).toFixed(1)} km` : ''}` : part.kind === 'connector' ? '현장·병원 좌표와 도로 사이의 접속 구간 · 직선 추정' : transportCase() ? '대본상 합성 위치 관측 사이의 보간 · 실제 운행 경로가 아님' : serverCase() ? '서버 실험의 합성 이동시간을 표시하는 직선 경로' : onlineCase() && !realRoadCase() ? '합성 입력의 직선 추정 이동 · 도로 조회를 사용하지 않은 실험' : '직선 추정 경로 · 도로 경로를 확보하지 못한 구간'));
          routes.push(route);
        }
      }
      const position = trajectoryPosition(tr, patient); if (!position) return;
      const [x, y] = project(position);
      const ox = sharedDispatch || mapBranch !== 'all' ? 0 : (bi - (branches().length - 1) / 2) * 20; const oy = sharedDispatch ? -12 : bi % 2 ? 10 : -9;
      const g = svg('g', { transform: `translate(${x + ox * symbolScale} ${y + oy * symbolScale}) scale(${symbolScale})` });
      const shortId = `P${patients().findIndex(p => p.id === patient.id) + 1}`;
      g.append(svg('circle', { cx: -ox, cy: -oy, r: 2.2, fill: moveColor, stroke: '#fff', 'stroke-width': 1 }), svg('path', { d: `M${-ox} ${-oy} L0 0`, stroke: moveColor, 'stroke-width': .8, opacity: .5 }), svg('rect', { x: -8, y: -6, width: 17, height: 12, rx: 3, fill: moveColor, stroke: '#fff', 'stroke-width': 1.8 }), svg('path', { d: 'M-3 0 H3 M0 -3 V3', stroke: '#fff', 'stroke-width': 1.7 }), svg('text', { x: 0, y: -11, 'text-anchor': 'middle', fill: moveColor, class: 'ambulance-label' }, `${sharedDispatch ? '119' : b.displayId}${mapDetails || state.patient !== 'all' ? ` · ${shortId}` : ''}`), svg('title', {}, `${sharedDispatch ? '공통 119 출동' : b.label} / ${patient.id} / ${phase.label}\n작은 점은 도로상의 모의 위치이며 차량 아이콘은 겹침 방지를 위해 떨어뜨려 표시합니다.`));
      g.dataset.patient = patient.id; g.dataset.branch = b.id;
      ambulanceMarkers.push({ element: g, x, y, patient: patient.id, branch: b.id, displayId: b.displayId, sharedDispatch, color: moveColor });
    });
  });
  const usedHospitals = new Set(branches().flatMap(b => seen(b).map(hospitalId)).filter(Boolean));
  hospitals().forEach(h => {
    const [x, y] = project(locOf(h)); const active = statuses.get(String(h.id)) ?? []; const g = svg('g', { transform: `translate(${x} ${y}) scale(${symbolScale})`, class: 'hospital-marker', 'data-hospital': h.id, tabindex: 0, role: 'img', 'aria-label': h.name });
    const outage = outages.get(String(h.id));
    const outageLabel = outage?.type === 'transport_review_pending' ? '해당 병원으로 이송 중인 환자의 수용 조건 확인 필요' : '현재 가상 자원 장애 발생';
    if (outage) g.append(svg('circle', { r: 12, fill: '#f9e2d9', stroke: '#d46e53', 'stroke-width': 1.3, 'stroke-dasharray': '3 2', opacity: .9 }));
    g.append(svg('rect', { x: -5, y: -5, width: 10, height: 10, rx: 2, fill: outage ? '#fff1e9' : '#fff', stroke: outage ? '#c65d47' : usedHospitals.has(String(h.id)) ? '#607c70' : '#a3b8aa', 'stroke-width': 1.1 }), svg('path', { d: 'M-2.8 0 H2.8 M0 -2.8 V2.8', stroke: outage ? '#c65d47' : '#678376', 'stroke-width': 1.2 }), svg('title', {}, `${h.name} (${h.id})\n${syntheticLocations() ? '합성 병원 위치 / 이송 모형' : '실제 위치 / 치료 역량·자원은 시연용 가정'}${outage ? `\n${outageLabel}` : ''}`));
    const unique = new Map([...active].sort((a, z) => a.t - z.t).map(a => [a.branch, a]));
    if (mapDetails) [...unique.values()].forEach(a => { const cx = -9 + a.index * 9; const responseColor = ['accept', 'accepted'].includes(a.type) ? '#25896b' : ['decline', 'rejected'].includes(a.type) ? '#ce6554' : serverCase() && ['no_response', 'review_cancelled', 'reply_suppressed'].includes(a.type) ? '#9aa9a0' : '#c3a34c'; g.append(svg('circle', { cx, cy: 12, r: 3.4, fill: responseColor, stroke: a.color, 'stroke-width': 1.2 })); });
    const isDestination = branches().filter(b => mapBranch === 'all' || b.id === mapBranch).some(b => patients().filter(p => state.patient === 'all' || String(p.id) === state.patient).some(p => phaseFor(b, p).hospital === h.id));
    if (usedHospitals.has(String(h.id)) && (mapDetails || isDestination)) g.append(svg('text', { x: 9, y: -7, class: 'hospital-label' }, mapDetails ? h.name : h.name.replace(/^의료법인[^병]*재단/, '')));
    g.addEventListener('pointerenter', event => showHospital(h, active, event)); g.addEventListener('pointerleave', hideTooltip); g.addEventListener('focus', event => showHospital(h, active, event)); g.addEventListener('blur', hideTooltip);
    markers.push(g);
  });
  const patientGroups = [];
  patients().filter(p => (state.patient === 'all' || String(p.id) === state.patient) && state.time >= number(p.occur_t)).forEach(p => {
    const point = project(locOf(p));
    const group = patientGroups.find(g => Math.hypot(g.point[0] - point[0], g.point[1] - point[1]) < 28 * symbolScale);
    if (group) group.patients.push(p); else patientGroups.push({ point, patients: [p] });
  });
  patientGroups.forEach(group => {
    const [x, y] = group.point, list = group.patients, g = svg('g', { transform: `translate(${x} ${y}) scale(${symbolScale})`, class: 'patient-origin', 'data-patients': list.map(p => p.id).join(',') });
    const label = list.length > 1 ? `환자 ${list.length}명 발생지` : '환자 발생지';
    const labelWidth = list.length > 1 ? 116 : 94;
    const viewport = window.ReplayBasemap.visibleSvgBounds(map.clientWidth, map.clientHeight);
    const labelLeft = x * state.zoom + state.pan[0] + (20 + labelWidth) * symbolScale * state.zoom > viewport.left + viewport.width - 12 / viewport.scale;
    const labelX = labelLeft ? -labelWidth - 20 : 20;
    g.append(svg('circle', { r: 23, fill: '#F1592A', opacity: .14 }), svg('circle', { r: 5, fill: '#F1592A', stroke: '#fff', 'stroke-width': 2 }));
    g.append(svg('path', { d: 'M0 0 C-4 -6 -15 -17 -15 -28 A15 15 0 1 1 15 -28 C15 -17 4 -6 0 0 Z', fill: '#DF4B21', stroke: '#fff', 'stroke-width': 2.5, class: 'patient-pin' }));
    g.append(list.length > 1 ? svg('text', { x: 0, y: -24, fill: '#fff', 'text-anchor': 'middle', 'font-size': 12, 'font-weight': 800 }, String(list.length)) : svg('path', { d: 'M-5 -28 H5 M0 -33 V-23', stroke: '#fff', 'stroke-width': 2.5 }));
    g.append(svg('rect', { x: labelX, y: -44, width: labelWidth, height: 25, rx: 4, fill: '#fffefa', stroke: '#DF4B21', 'stroke-width': 1 }), svg('text', { x: labelX + 8, y: -27, class: 'patient-origin-label' }, label), svg('title', {}, `${label} (현재 이동 위치와 구분)\n${list.map(p => `${p.id} · ${p.label ?? '가상 환자'}`).join('\n')}`));
    patientMarkers.push(g);
  });
  const vehicleGroups = [];
  ambulanceMarkers.forEach(marker => {
    const group = state.patient === 'all' && patients().length > 1 ? vehicleGroups.find(g => g.branch === marker.branch && g.sharedDispatch === marker.sharedDispatch && Math.hypot(g.x - marker.x, g.y - marker.y) < 18 * symbolScale) : null;
    if (group) group.members.push(marker); else vehicleGroups.push({ ...marker, members: [marker] });
  });
  const vehicles = vehicleGroups.map(group => {
    const g = group.element;
    if (group.members.length > 1) {
      const labels = group.members.map(m => m.patient);
      g.dataset.patient = labels.join(',');
      g.querySelector('text').textContent = `${group.sharedDispatch ? '119' : group.displayId} · ${labels.length}명`;
      g.querySelector('title').textContent = `${group.sharedDispatch ? '공통 119 출동' : group.displayId} / 가까이 겹친 구급차 ${labels.length}대: ${labels.join(', ')}. 환자를 선택하면 개별 경로를 표시합니다.`;
      g.append(svg('circle', { cx: 9, cy: -6, r: 5.8, fill: '#fff', stroke: group.color, 'stroke-width': 1.2 }), svg('text', { x: 9, y: -3.5, 'text-anchor': 'middle', fill: group.color, 'font-size': 7, 'font-weight': 800 }, labels.length));
    }
    return g;
  });
  $('route-layer').replaceChildren(...routes); $('inquiry-layer').replaceChildren(...inquiries); $('hospital-layer').replaceChildren(...markers); $('patient-layer').replaceChildren(...patientMarkers); $('ambulance-layer').replaceChildren(...vehicles);
  $('map-focus-patient').disabled = patientMarkers.length === 0;
}

function showHospital(h, responses, event) {
  const tip = $('map-tooltip'); tip.replaceChildren(node('strong', '', h.name), node('div', '', `${h.id} · ${syntheticLocations() ? '합성 병원 위치 / 이송 모형' : '실제 위치 / 운영 자원은 가상'}`));
  const caps = h.capabilities ?? []; if (caps.length) tip.append(node('div', '', `가정한 진료 역량: ${caps.join(', ')}`));
  if (h.capacity) tip.append(node('div', '', `가상 자원: ${Object.entries(h.capacity).map(([k, v]) => `${k} ${v}`).join(' · ')}`));
  const unique = new Map([...responses].sort((a, z) => a.t - z.t).map(r => [r.branch, r])); for (const r of unique.values()) tip.append(node('div', '', `${r.branch}: ${eventNames[r.type] ?? r.type}`));
  const box = $('map-shell').getBoundingClientRect(); const x = event.clientX ? event.clientX - box.left : box.width / 2; const y = event.clientY ? event.clientY - box.top : box.height / 2;
  tip.style.left = `${Math.min(Math.max(8, x + 13), Math.max(8, box.width - 268))}px`; tip.style.top = `${Math.min(Math.max(8, y + 10), Math.max(8, box.height - 170))}px`; tip.hidden = false;
}
function hideTooltip() { $('map-tooltip').hidden = true; }

function renderResults() {
  if (transportCase()) { renderTransportResults(); return; }
  const complete = state.time >= state.duration - .001;
  $('results-status').textContent = complete ? '전체 재생 완료 · 최종 결과' : '현재 시각까지의 결과 · 최종 수치는 완료 후 표시';
  const header = node('tr'); header.append(node('th', '', '비교 항목'));
  branches().forEach(b => { const th = node('th', '', `${b.displayId} · ${b.label}`); th.style.color = b.color; header.append(th); });
  $('results-table').querySelector('thead').replaceChildren(header);
  const rows = [
    ['문의 건수', (b, events) => `${events.filter(e => typeOf(e) === 'ask').length}건`],
    ['수용 확정 환자', (b, events) => `${new Set(events.filter(e => typeOf(e) === 'commit').map(patientId)).size} / ${patients().length}명`],
    ['수용 확정률', (b, events) => `${(100 * new Set(events.filter(e => typeOf(e) === 'commit').map(patientId)).size / Math.max(1, patients().length)).toFixed(0)}%`],
    ['높은 우선순위 미확정', (b) => `${currentCounts(b).highWaiting}명`],
    ['첫 수용 확정 평균¹', (b, events) => { const values = patients().map(p => { const event = events.find(e => typeOf(e) === 'commit' && patientId(e) === String(p.id)); return event ? when(event) - number(p.occur_t) : null; }).filter(v => v != null); return values.length ? resultTime(values.reduce((a, z) => a + z, 0) / values.length) : '—'; }],
    ['탐색→첫 확정 평균²', (b, events) => { const values = patients().map(p => { const event = events.find(e => typeOf(e) === 'commit' && patientId(e) === String(p.id)); return event ? when(event) - number(p.search_start_t) : null; }).filter(v => v != null); return values.length ? resultTime(values.reduce((a, z) => a + z, 0) / values.length) : '—'; }],
    ['탐색→첫 확정 최장²', (b, events) => { const values = patients().map(p => { const event = events.find(e => typeOf(e) === 'commit' && patientId(e) === String(p.id)); return event ? when(event) - number(p.search_start_t) : null; }).filter(v => v != null); return values.length ? resultTime(Math.max(...values), 0) : '—'; }],
    ['최종병원 도착 평균¹', (b) => complete ? resultTime(b.metrics?.occur_to_final_arrival_mean ?? meanOutcomes(b, 'final_arrive_t')) : '완료 후 표시'],
    ['최종병원 도착 최장¹', (b) => complete ? resultTime(maxOutcomes(b, 'final_arrive_t'), 0) : '완료 후 표시'],
    ['치료 시작 평균¹', (b) => complete ? minutes(meanOutcomes(b, 'treatment_start_t')) : '완료 후 표시'],
    ['이송 중 목적지 변경', (b, events) => `${events.filter(e => typeOf(e) === 'redirect').length}건`],
    ['병원 간 전원', (b, events) => `${events.filter(e => typeOf(e) === 'transfer_depart').length}건`],
    ['치료 완료 환자', (b, events) => `${new Set(events.filter(e => typeOf(e) === 'treatment_complete').map(patientId)).size} / ${patients().length}명`],
    ['관찰 종료 시 미배정', (b) => complete ? `${b.patient_outcomes.filter(p => p.state === 'UNASSIGNED').length}명` : '완료 후 표시'],
    ['관찰 종료 시 치료 미시작', (b) => complete ? `${b.patient_outcomes.filter(p => ['UNASSIGNED', 'NOT_TREATED'].includes(p.state)).length}명` : '완료 후 표시']
  ];
  if (serverCase()) {
    const hidden = new Set(['높은 우선순위 미확정', '치료 시작 평균¹', '이송 중 목적지 변경', '병원 간 전원', '치료 완료 환자', '관찰 종료 시 치료 미시작']);
    for (let i = rows.length - 1; i >= 0; i--) if (hidden.has(rows[i][0])) rows.splice(i, 1);
    rows.forEach(row => { row[0] = row[0].replace('최종병원 도착', '예상 도착'); });
    rows.push(['검토 시작 전 취소', (b, events) => `${events.filter(e => typeOf(e) === 'review_cancelled').length}건`],
      ['취소 후 미반영 회신', (b, events) => `${events.filter(e => typeOf(e) === 'reply_suppressed').length}건`],
      ['회신 없음(수용 불가와 구분)', (b, events) => `${events.filter(e => typeOf(e) === 'no_response').length}건`]);
  }
  if (priorityComparisonEnabled()) for (const tier of PRIORITY_TIERS) rows.push(
    [`${tier}군 관찰 종료 시 치료 미시작`, (b) => state.time >= state.duration ? `${priorityUntreatedCounts(b)[tier]}명` : '완료 후 표시']
  );
  if (inquiryEnabled()) rows.push(
    ['요청당 처리 대기 평균³', (b) => complete ? resultTime(b.metrics?.inquiry_queue_wait_mean) : '완료 후 표시'],
    ['동시 검토 대기 최다³', (b) => complete ? metricUnit(b, 'inquiry_peak_waiting', '건') : '완료 후 표시'],
    ['문의 검토 누적 작업³', (b) => complete ? metricUnit(b, 'inquiry_service_minutes', '인력·분', 1) : '완료 후 표시']
  );
  if (branches().some(b => b.metrics?.resource_invalid_transit_minutes != null)) rows.push(
    ['자원조건 불충족 이송 누적⁴', (b) => complete ? metricUnit(b, 'resource_invalid_transit_minutes', '환자·분', 1) : '완료 후 표시']
  );
  if (onlineCase()) rows.push(
    ['관찰 종료 시 미도착', b => complete ? `${b.online_summary?.not_arrived ?? '—'}명` : '완료 후 표시'],
    ['전체 환자 선정 제한 경과 평균²', b => complete ? minutes(b.online_summary?.selection_capped_mean_minutes) : '완료 후 표시'],
    ['전체 환자 도착 제한 경과 평균¹', b => complete ? minutes(b.online_summary?.arrival_capped_mean_minutes) : '완료 후 표시'],
    ['알림→목적지 변경 평균(변경 건)', b => complete ? minutes(b.online_summary?.notify_to_redirect_mean_minutes) : '완료 후 표시'],
    ['알림 후 목적지 변경 미발생', b => complete ? `${b.online_summary?.notification_without_redirect ?? '—'}건` : '완료 후 표시'],
    ['CP-SAT 현재 배치 증명/실행', b => b.policy?.rule === 'C1' ? `${(b.solver_records ?? []).filter(r => number(r.t) <= state.time + 1e-7 && r.optimal_proven).length} / ${(b.solver_records ?? []).filter(r => number(r.t) <= state.time + 1e-7).length}건` : '—'],
    ['CP-SAT 순차 대체', b => b.policy?.rule === 'C1' ? `${(b.solver_records ?? []).filter(r => number(r.t) <= state.time + 1e-7 && r.fallback).length}건` : '—']
  );
  $('results-table').querySelector('tbody').replaceChildren(...rows.map(([label, value]) => { const row = node('tr'); row.append(node('th', '', label)); branches().forEach(b => row.append(node('td', '', value(b, seen(b))))); return row; }));
  const routing = routingInfo();
  const routeNote = routing.roadCount ? ` 이동은 일반 차량의 도로 예상시간${routing.connectorCount ? '과 도로 접속 구간의 추정시간' : ''}을 사용하며 긴급차량 실제 운행시간이 아닙니다.${routing.evaluatedFallbackCount ? ` 경로 조회 ${routing.evaluatedFallbackCount}건은 추정값으로 대체됐습니다.` : ''}` : ' 이동 경로와 시간은 직선 추정 모형입니다.';
  if (serverCase()) { document.querySelector('.result-note').textContent = '¹ 환자 정보 접수 기준 경과시간. ² 서버 실험에서는 환자 정보가 접수된 시점에 탐색을 시작합니다. 시간 평균·최장은 선정된 환자만 포함하므로 미배정 수와 함께 봅니다. ³ 검토 대기·작업은 서버 실험의 합성 FIFO 모델입니다. 대기 평균은 검토 시작 전 취소된 문의의 대기까지 포함한 모든 접수 문의 기준입니다. 예상 도착은 선정 시각과 합성 이동시간의 합이며, 현장 개선율이나 현행 시스템 재현 결과가 아닙니다.'; return; }
  document.querySelector('.result-note').textContent = `¹ 환자 발생 기준 경과시간. ² 현장 평가 완료 후 병원 탐색 시작 기준 경과시간. 시간 평균·최장은 해당 사건이 기록된 환자만 포함하므로 미배정·치료 미시작 수와 함께 비교합니다. 높은 우선순위는 실험 설정값 4이며 임상 등급이 아닙니다.${inquiryEnabled() ? ' ³ 처리 대기 평균과 누적 작업은 환자 문의만 집계합니다. 대기 최다와 현재 처리 표에는 고정 외생 업무도 포함됩니다. 대기·작업 시간은 관찰 종료까지 실제 경과한 합성 시간입니다.' : ''}${branches().some(b => b.metrics?.resource_invalid_transit_minutes != null) ? ' ⁴ 이송 중 목적지 자원이 부족한 상태의 누적 환자×분이며 실제 임상 위해나 예후를 뜻하지 않습니다.' : ''}${routeNote} 치료 시작·완료는 가상 사건이며, 실제 병원 선정·치료 성과 또는 현행 시스템 재현 결과가 아닙니다.`;
  if (onlineCase()) document.querySelector('.result-note').textContent += ' 제한 경과 평균은 사건이 없으면 관찰 종료까지의 시간을 넣어 전체 환자로 계산합니다. 실제 미도착 시각을 추정한 값이 아닙니다. CP-SAT 증명은 현재 묶음에만 적용됩니다. 알림 후 변경 미발생은 자원 복구·기존 이송 유지 등을 포함하므로 실패 건수가 아닙니다.';
}
function metricUnit(branch, key, unit, decimals = 0) { const value = branch.metrics?.[key]; return value != null && Number.isFinite(Number(value)) ? `${Number(value).toFixed(decimals)}${unit}` : '—'; }
function meanOutcomes(branch, key) { const vals = branch.patient_outcomes.filter(p => p[key] != null && Number.isFinite(Number(p[key]))).map(p => Number(p[key]) - number(patients().find(patient => String(patient.id) === String(p.id))?.occur_t)); return vals.length ? vals.reduce((a, z) => a + z, 0) / vals.length : null; }
function maxOutcomes(branch, key) { const values = branch.patient_outcomes.filter(p => p[key] != null && Number.isFinite(Number(p[key]))).map(p => Number(p[key]) - number(patients().find(patient => String(patient.id) === String(p.id))?.occur_t)); return values.length ? Math.max(...values) : null; }

const resourceNames = { ed: '응급실 자리', ed_bed: '응급실 자리', special_team: '전문팀', imaging_unit: '가상 영상 자원', ventilator: '인공호흡기', icu: '중환자실 자리' };
function resourceSnapshot(branch) { return (branch.resource_snapshots ?? []).filter(s => number(s.t) <= state.time + 1e-7).at(-1) ?? null; }
function observedSnapshot(values) { return (values ?? []).reduce((latest, frame) => number(frame.t) <= state.time + 1e-7 && (!latest || number(frame.t) >= number(latest.t)) ? frame : latest, null); }
function inquirySnapshot(branch) { return observedSnapshot(branch.inquiry_snapshots); }
function resourceRiskSnapshot(branch) { return observedSnapshot(branch.resource_risk_snapshots); }
function transportSnapshot(branch) { return observedSnapshot(branch.transport_snapshots); }
function transportIssue(issue) {
  if (issue.kind === 'resource_shortfall') return `${resourceNames[issue.resource_type] ?? issue.resource_type}: 점유 ${issue.occupied} + 도착 예정 ${issue.committed} / 용량 ${issue.capacity} · ${issue.shortfall} 부족`;
  return issue.message ?? issue.kind ?? issue.type ?? JSON.stringify(issue);
}
const transportReviewNames = { MONITORING: '조건 감시 중', RECHECK_REQUIRED: '병원 재확인 필요', SEARCHING: '대체 탐색 중', AWAITING_CHANGE: '사람의 변경 대기', EXHAUSTED: '대체 탐색 종료', COMPLETED: '종료', DISABLED: '미사용', PAUSED: '중지' };
const transportJobNames = { RUNNING: '대체 문의 중', AWAITING_CHANGE: '대체 수용 확보', AWAITING_POSITION: '최신 위치 필요', EXHAUSTED: '추가 후보 없음·기한 종료', STOPPED: '종료', IDLE: '미시작', DISABLED: '미사용' };
function renderTransportResults() {
  const complete = state.time >= state.duration - .001;
  $('results-status').textContent = complete ? '시연 관찰 종료 · 서버 처리 결과' : '현재 시각까지의 서버 처리 기록';
  const header = node('tr'); header.append(node('th', '', '기록 항목'), node('th', '', '실제 서버 처리'));
  $('results-table').querySelector('thead').replaceChildren(header);
  const branch = branches()[0], events = seen(branch), count = type => events.filter(e => typeOf(e) === type).length;
  const uniq = type => new Set(events.filter(e => typeOf(e) === type).map(patientId)).size;
  const rows = [
    ['수용 확정', `${uniq('commit')} / ${patients().length}명`],
    ['재확인 대상으로 식별된 환자', `${uniq('recheck')}명`],
    ['병원의 기존 수용 유지 확인', `${count('acceptance_kept')}건`],
    ['대체병원 문의', `${events.filter(e => typeOf(e) === 'ask' && events.some(x => patientId(x) === patientId(e) && typeOf(x) === 'alternative_requested' && when(x) <= when(e))).length}건`],
    ['대체 수용 확보 · 변경 대기 기록', `${count('alternative_ready')}건`],
    ['사람이 확정한 목적지 변경', `${count('redirect')}건`],
    ['도착 확인 입력', `${uniq('arrive')} / ${patients().length}명`],
    ['관찰 종료 시 도착 미기록', complete ? `${patients().length - uniq('arrive')}명` : '관찰 종료 후 표시'],
    ['치료·전원·예후', '미기록'],
  ];
  $('results-table').querySelector('tbody').replaceChildren(...rows.map(([name, value]) => { const tr = node('tr'); tr.append(node('th', '', name), node('td', '', value)); return tr; }));
  document.querySelector('.result-note').textContent = '가상 입력을 실제 서버 기능으로 처리한 기능 시연입니다. 도착은 대본상 확인 입력이며 위치는 합성 관측값의 보간입니다. 알고리즘 간 성능 비교, 현행 체계 재현 또는 임상 효과 검증 결과가 아닙니다. 목적지는 대체 수용 확보 후 사람이 변경한 사건에서만 바뀝니다.';
}
function renderTransportStatus() {
  $('transport-section').hidden = !transportCase();
  if (!transportCase()) return;
  const branch = branches()[0], snapshot = transportSnapshot(branch);
  $('transport-status-time').textContent = `현재 ${clock(state.time)} · 기록 시각 ${snapshot ? clock(snapshot.t) : '접수 전'}`;
  const rows = (snapshot?.patients ?? []).filter(p => state.patient === 'all' || String(p.id) === state.patient);
  $('transport-table').querySelector('tbody').replaceChildren(...rows.map(p => {
    const tr = node('tr'); tr.dataset.patient = p.id;
    const phase = phaseFor(branch, p), review = p.transport_review ?? {}, job = p.transport_reassignment ?? {};
    const issues = (review.evidence?.issues ?? []).map(transportIssue).join(' / ');
    tr.append(node('th', '', p.id), node('td', '', hospitalName(p.destination_hospital_id)), node('td', '', phase.label), node('td', '', transportReviewNames[review.state] ?? review.state ?? '미사용'), node('td', '', transportJobNames[job.state] ?? job.state ?? '미시작'), node('td', '', issues || '기록된 위험 없음'));
    return tr;
  }));
}
function inquiryEnabled() { return current().inquiry_queue?.enabled !== false && branches().some(b => b.inquiry_snapshots?.length); }
function inquiryTotals(snapshot) { return (snapshot?.hospitals ?? []).reduce((total, h) => { for (const key of Object.keys(total)) total[key] += number(h[key]); return total; }, { waiting: 0, processing: 0, workers: 0, completed: 0, submitted: 0, queue_wait_total_minutes: 0, service_total_minutes: 0 }); }
function prepareResources() {
  const keys = [...new Set(hospitals().flatMap(h => Object.keys(h.capacity ?? {})))];
  const select = $('resource-select');
  select.replaceChildren(...keys.map(key => { const option = node('option', '', resourceNames[key] ?? key); option.value = key; return option; }));
  if (keys.includes('special_team')) select.value = 'special_team';
}
function renderResources() {
  const hasSnapshots = branches().some(b => b.resource_snapshots?.length);
  $('resource-section').hidden = !hasSnapshots;
  if (!hasSnapshots) return;
  const key = $('resource-select').value;
  const snapshots = branches().map(resourceSnapshot);
  const head = node('tr'); head.append(node('th', '', '병원 · 가상 자원'));
  branches().forEach(b => { const th = node('th', '', `${b.displayId} · ${b.label}`); th.style.color = b.color; head.append(th); });
  $('resource-table').querySelector('thead').replaceChildren(head);
  $('resource-table').querySelector('tbody').replaceChildren(...hospitals().map(h => {
    const row = node('tr'); row.append(node('th', '', h.name));
    branches().forEach((b, i) => {
      const snapshot = snapshots[i]?.hospitals?.find(item => String(item.id) === String(h.id));
      const cell = node('td', 'resource-cell');
      if (!snapshot || snapshot.capacity?.[key] == null) { cell.textContent = '—'; row.append(cell); return; }
      const capacity = number(snapshot.capacity[key]), used = number(snapshot.used?.[key]), committed = number(snapshot.committed?.[key]), queued = number(snapshot.queued?.[key]);
      const free = capacity - used - committed - queued;
      cell.append(node('strong', free < 0 ? 'needs-attention' : '', `치료 ${used} + 이송 ${committed}${queued ? ` + 대기 ${queued}` : ''} / 용량 ${capacity}`), node('small', free < 0 ? 'needs-attention' : '', free < 0 ? `조건 재확인 필요 · ${-free} 초과` : `남음 ${free}`));
      cell.title = `시각 ${clock(snapshots[i].t)} · 도착 후 대기 ${number(snapshot.queue_count)}명. 치료에는 초기 점유와 시스템 밖 유입이 포함될 수 있습니다.`;
      row.append(cell);
    });
    return row;
  }));
}

function renderInquiryQueues() {
  $('inquiry-section').hidden = !inquiryEnabled();
  if (!inquiryEnabled()) return;
  const snapshots = branches().map(inquirySnapshot);
  $('inquiry-status').textContent = `현재 ${clock(state.time)} · ${serverCase() ? '서버 환자 문의 기록' : '고정 외생 업무 포함'}`;
  $('inquiry-note').textContent = serverCase()
    ? '각 병원의 처리 중·대기·검토 완료 건수를 원본 서버 실험 기록으로 표시합니다. 검토 시작 전 취소된 문의는 대기열에서 제거되지만, 이미 검토 중인 문의는 처리 시간을 모두 소모합니다. 취소 뒤 회신은 배정에 반영되지 않습니다. 검토 시간과 담당자 수는 합성 설정입니다.'
    : '각 병원의 처리 중·대기·검토 완료 건수를 현재 시각 기준으로 표시합니다. 고정 외생 업무도 포함합니다. 검토 시간과 담당자 수는 합성 설정이며, 다른 병원에서 수용이 확정돼도 이미 보낸 문의의 검토는 자동 취소하지 않는 모형입니다.';
  const header = node('tr'); header.append(node('th', '', '병원 · 가상 담당자'));
  branches().forEach(b => { const th = node('th', '', `${b.displayId} · ${b.label}`); th.style.color = b.color; header.append(th); });
  $('inquiry-table').querySelector('thead').replaceChildren(header);
  const rows = hospitals().map(h => {
    const row = node('tr'); row.append(node('th', '', h.name));
    branches().forEach((branch, index) => {
      const snapshot = snapshots[index], hospital = snapshot?.hospitals?.find(item => String(item.hospital) === String(h.id));
      const cell = node('td', 'queue-cell');
      if (!hospital) cell.textContent = '—';
      else {
        cell.append(node('strong', '', `처리 ${number(hospital.processing)} / 담당 ${number(hospital.workers)}명`),
          node('span', number(hospital.waiting) ? 'queue-attention' : '', `대기 ${number(hospital.waiting)}건`),
          node('small', '', `검토 완료 ${number(hospital.completed)} / 접수 ${number(hospital.submitted)}건`));
        cell.title = `기록 시각 ${clock(snapshot.t)} · 누적 대기 ${number(hospital.queue_wait_total_minutes).toFixed(1)}건·분 · 누적 검토 ${number(hospital.service_total_minutes).toFixed(1)}인력·분`;
      }
      row.append(cell);
    });
    return row;
  });
  const totalRow = node('tr', 'queue-total'); totalRow.append(node('th', '', '현재 전체 합계'));
  branches().forEach((branch, index) => {
    const snapshot = snapshots[index], total = inquiryTotals(snapshot), cell = node('td', 'queue-cell');
    if (!snapshot) cell.textContent = '—';
    else cell.append(node('strong', '', `처리 ${total.processing} · 대기 ${total.waiting}건`),
      node('small', '', `검토 완료 ${total.completed} / 접수 ${total.submitted}건`),
      node('small', '', `누적 검토 ${total.service_total_minutes.toFixed(1)}인력·분`));
    totalRow.append(cell);
  });
  $('inquiry-table').querySelector('tbody').replaceChildren(...rows, totalRow);
}

function renderLog() {
  const events = branches().flatMap(b => seen(b).filter(e => state.patient === 'all' || !patientId(e) || patientId(e) === state.patient).map(e => ({ ...e, branchId: b.displayId, color: b.color }))).sort((a, z) => when(z) - when(a));
  $('event-count').textContent = `${events.length}건`;
  if (!events.length) { $('event-log').replaceChildren(node('li', 'empty-state', '재생하면 문의와 응답, 이동 기록을 시간순으로 확인할 수 있습니다.')); return; }
  $('event-log').replaceChildren(...events.slice(0, 30).map(e => { const li = node('li'); const time = node('time', '', clock(when(e))); const badge = node('span', 'event-branch', e.branchId); badge.style.setProperty('--branch-color', e.color); const text = node('span', 'event-text', describe(e)); const reason = detail(e); if (reason) text.append(node('small', '', reason)); li.append(time, badge, text); return li; }));
}

function keyEvents() {
  if (!state.selected) return [];
  let filters = keyEventCache.get(state.selected);
  if (!filters) { filters = new Map(); keyEventCache.set(state.selected, filters); }
  if (filters.has(state.patient)) return filters.get(state.patient);
  const grouped = new Map();
  branches().forEach(branch => branch.events.forEach(event => {
    if (state.patient !== 'all' && patientId(event) !== state.patient) return;
    if (!KEY_EVENT_TYPES.has(typeOf(event)) && event.server_type !== 'selection_deadline' && !(typeOf(event) === 'inquiry_policy' && observedInquiryDecision(event))) return;
    const t = when(event);
    if (t < 0 || t > state.duration) return;
    if (!grouped.has(t)) grouped.set(t, { t, events: [] });
    grouped.get(t).events.push({ event, branchId: branch.displayId });
  }));
  const events = [...grouped.values()].sort((a, b) => a.t - b.t);
  filters.set(state.patient, events);
  return events;
}

function keyEventNeighbours() {
  const events = keyEvents();
  return { previous: events.findLast(e => e.t < state.time - 1e-7), next: events.find(e => e.t > state.time + 1e-7), current: events.findLast(e => e.t <= state.time + 1e-7) };
}

function jumpKeyEvent(direction) {
  const neighbours = keyEventNeighbours(), target = direction < 0 ? neighbours.previous : neighbours.next;
  if (!target) return getState();
  state.playing = false;
  return seek(target.t);
}

function renderEventNavigation() {
  const { previous, next, current: event } = keyEventNeighbours();
  $('previous-event').disabled = !previous;
  $('next-event').disabled = !next;
  $('event-time').textContent = event ? clock(event.t) : '—';
  if (!event) { $('event-summary').textContent = state.patient === 'all' ? '기록된 문의·회신·선정 시점으로 이동' : `${state.patient}의 첫 핵심 사건 전`; $('event-summary').title = ''; return; }
  const descriptions = [...new Set(event.events.map(({ event, branchId }) => `${branchId} ${describe(event)}${typeOf(event) === 'inquiry_policy' ? ` · ${inquiryDecisionReason(event)}` : ''}`))];
  const summary = descriptions.slice(0, 2).join(' / ') + (descriptions.length > 2 ? ` · 외 ${descriptions.length - 2}건` : '');
  $('event-summary').textContent = summary;
  $('event-summary').title = descriptions.join('\n');
}

function render() {
  if (!state.selected) return;
  $('current-time').textContent = clock(state.time); $('timeline').value = state.time; $('timeline').setAttribute('aria-valuetext', `경과 ${clock(state.time)}`); $('play').textContent = state.playing ? 'Ⅱ 일시정지' : state.time >= state.duration ? '▶ 다시 재생' : '▶ 재생';
  $('timeline').style.setProperty('--played', `${100 * state.time / state.duration}%`);
  $('playback-state').textContent = state.playing ? '재생 중' : state.time >= state.duration ? '재생 완료' : state.time > 0 ? '일시정지' : '재생 준비';
  $('playback-state').dataset.playing = String(state.playing);
  $('play').setAttribute('aria-pressed', String(state.playing));
  renderBranches(); renderMap(); renderResults(); renderResources(); renderInquiryQueues(); renderTransportStatus(); renderLog(); renderEventNavigation();
}
function seek(t) { state.time = Math.min(state.duration, Math.max(0, number(t))); if (state.time >= state.duration) state.playing = false; hideTooltip(); render(); return getState(); }
function getState() { return { time: state.time, duration: state.duration, scenarioId: current().id, playing: state.playing, patient: state.patient, branchStates: branches().map(b => ({ id: b.id, label: b.label, patients: patients().map(p => ({ id: p.id, ...phaseFor(b, p) })), ...currentCounts(b), priorityUntreatedCounts: priorityUntreatedCounts(b), resourceSnapshotTime: resourceSnapshot(b)?.t ?? null, inquirySnapshotTime: inquirySnapshot(b)?.t ?? null, inquiryCurrent: inquiryTotals(inquirySnapshot(b)), resourceRiskSnapshotTime: resourceRiskSnapshot(b)?.t ?? null, resourceRiskPatients: resourceRiskSnapshot(b)?.patients ?? [], inquiryPolicyDecision: inquiryPolicyDecision(b), inquiryPolicyDecisionTime: inquiryPolicyDecision(b)?.t ?? null, inquiries: seen(b).filter(e => typeOf(e) === 'ask').length, transfers: seen(b).filter(e => typeOf(e) === 'transfer_depart').length, redirects: seen(b).filter(e => typeOf(e) === 'redirect').length })) }; }
function selectPatient(id) { state.patient = id; $('patient-select').value = id; render(); }
function setMapExpanded(expanded) {
  if (mapExpanded === expanded) return;
  const panel = $('map-panel'), dialog = $('map-expanded-dialog');
  const selector = $('patient-select').closest('.patient-selector'), button = $('map-expand');
  if (expanded) {
    mapScrollPosition = [window.scrollX, window.scrollY];
    mapPanelAnchor = document.createComment('original-map-panel'); panel.before(mapPanelAnchor);
    patientSelectorAnchor = document.createComment('original-patient-selector'); selector.before(patientSelectorAnchor);
    $('map-patient-slot').append(selector); $('map-patient-slot').hidden = false;
    dialog.append(panel); mapExpanded = true;
    document.body.classList.add('is-map-expanded');
    dialog.showModal();
    button.textContent = '작게 보기'; button.setAttribute('aria-expanded', 'true');
    button.focus({ preventScroll: true });
  } else {
    mapExpanded = false;
    dialog.close();
    patientSelectorAnchor.replaceWith(selector); patientSelectorAnchor = null;
    $('map-patient-slot').hidden = true;
    mapPanelAnchor.replaceWith(panel); mapPanelAnchor = null;
    document.body.classList.remove('is-map-expanded');
    button.textContent = '크게 보기'; button.setAttribute('aria-expanded', 'false');
    button.focus({ preventScroll: true });
    if (mapScrollPosition) window.scrollTo({ left: mapScrollPosition[0], top: mapScrollPosition[1], behavior: 'instant' });
    mapScrollPosition = null;
  }
  // Keep the same projection, pan, replay time and policy/patient selections.
  // The existing ResizeObserver refreshes tiles and symbols for the new size.
  hideTooltip();
}
function selectMapBranch(id) {
  if (id !== 'all' && !branches().some(b => b.id === id)) return;
  mapBranch = id;
  document.querySelectorAll('#map-policy-legend button').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.branch === id)));
  renderMap();
}
function focusPatients() {
  const observed = patients().filter(p => number(p.occur_t) <= state.time && (state.patient === 'all' || String(p.id) === state.patient));
  const points = observed.map(locOf);
  for (const b of branches().filter(b => mapBranch === 'all' || b.id === mapBranch)) for (const p of observed) {
    if (state.time < number(p.dispatch_t, p.occur_t)) continue;
    const tr = b.trajectories.find(tr => String(tr.patient) === String(p.id));
    const position = tr && trajectoryPosition(tr, p); if (position) points.push(position);
  }
  if (!points.length) return;
  const xy = points.map(project), xs = xy.map(p => p[0]), ys = xy.map(p => p[1]);
  const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
  const map = $('replay-map'), viewport = window.ReplayBasemap.visibleSvgBounds(map.clientWidth, map.clientHeight);
  state.zoom = Math.min(4, Math.max(.75, Math.min(viewport.width * .58 / Math.max(1, maxX - minX), viewport.height * .58 / Math.max(1, maxY - minY))));
  state.pan = [500 - (minX + maxX) / 2 * state.zoom, 360 - (minY + maxY) / 2 * state.zoom];
  applyTransform(); renderMap(); hideTooltip();
}
function resetMap() { state.zoom = 1; state.pan = [0, 0]; applyTransform(); if (state.selected) renderMap(); }
function applyTransform() { $('map-viewport').setAttribute('transform', `translate(${state.pan[0]} ${state.pan[1]}) scale(${state.zoom})`); renderScale(); scheduleBasemap(); }

$('scenario-select').addEventListener('change', e => selectScenario(e.target.value));
$('patient-select').addEventListener('change', e => selectPatient(e.target.value));
$('resource-select').addEventListener('change', renderResources);
$('basemap-select').addEventListener('change', e => { basemapMode = e.target.value; basemapEpoch++; mapTiles.clear(); $('basemap-layer').replaceChildren(); renderGeography(); });
$('play').addEventListener('click', () => { if (state.time >= state.duration) state.time = 0; state.playing = !state.playing; state.lastFrame = performance.now(); render(); });
$('reset').addEventListener('click', () => { state.playing = false; seek(0); });
$('finish').addEventListener('click', () => { state.playing = false; seek(state.duration); });
$('previous-event').addEventListener('click', () => jumpKeyEvent(-1));
$('next-event').addEventListener('click', () => jumpKeyEvent(1));
$('timeline').addEventListener('input', e => { state.playing = false; seek(e.target.value); });
$('speed').addEventListener('change', e => { state.speed = number(e.target.value, 12); });
$('fit-map').addEventListener('click', resetMap);
$('map-focus-patient').addEventListener('click', focusPatients);
$('map-detail-toggle').addEventListener('click', () => { mapDetails = !mapDetails; renderMap(); });
$('map-expand').addEventListener('click', () => setMapExpanded(!mapExpanded));
$('map-expanded-dialog').addEventListener('cancel', e => { e.preventDefault(); setMapExpanded(false); });
$('map-expanded-dialog').addEventListener('close', () => { if (mapExpanded) setMapExpanded(false); });
function zoomMap(multiplier) {
  const previous = state.zoom; state.zoom = Math.min(7, Math.max(.75, previous * multiplier));
  const ratio = state.zoom / previous;
  state.pan = [500 - (500 - state.pan[0]) * ratio, 360 - (360 - state.pan[1]) * ratio];
  applyTransform(); renderMap(); hideTooltip();
}
$('zoom-in').addEventListener('click', () => zoomMap(1.3));
$('zoom-out').addEventListener('click', () => zoomMap(1 / 1.3));

const navigation = [...document.querySelectorAll('.topnav a')];
navigation[0]?.classList.add('is-active');
const sectionObserver = new IntersectionObserver(entries => {
  for (const entry of entries) if (entry.isIntersecting) navigation.forEach(link => link.classList.toggle('is-active', link.hash === `#${entry.target.id}`));
}, { rootMargin: '-10% 0px -65% 0px' });
navigation.forEach(link => { const section = document.querySelector(link.hash); if (section) sectionObserver.observe(section); });
document.querySelector('.brand').addEventListener('click', e => { e.preventDefault(); state.playing = false; seek(0); });
const mapSvg = $('replay-map'); let drag = null;
function svgPoint(e) { const p = mapSvg.createSVGPoint(); p.x = e.clientX; p.y = e.clientY; return p.matrixTransform(mapSvg.getScreenCTM().inverse()); }
mapSvg.addEventListener('wheel', e => { e.preventDefault(); const p = svgPoint(e); const old = state.zoom; state.zoom = Math.min(7, Math.max(.75, old * Math.exp(-e.deltaY * .0015))); const ratio = state.zoom / old; state.pan = [p.x - (p.x - state.pan[0]) * ratio, p.y - (p.y - state.pan[1]) * ratio]; applyTransform(); renderMap(); hideTooltip(); }, { passive: false });
mapSvg.addEventListener('pointerdown', e => { if (e.button !== 0) return; const p = svgPoint(e); drag = { x: p.x, y: p.y, pan: [...state.pan] }; mapSvg.setPointerCapture(e.pointerId); mapSvg.classList.add('dragging'); hideTooltip(); });
mapSvg.addEventListener('pointermove', e => { if (!drag) return; const p = svgPoint(e); state.pan = [drag.pan[0] + p.x - drag.x, drag.pan[1] + p.y - drag.y]; applyTransform(); });
const stopDrag = () => { drag = null; mapSvg.classList.remove('dragging'); };
mapSvg.addEventListener('pointerup', stopDrag); mapSvg.addEventListener('pointercancel', stopDrag);
window.addEventListener('keydown', e => { if (['INPUT', 'SELECT', 'BUTTON', 'TEXTAREA'].includes(document.activeElement?.tagName)) return; if (e.code === 'Space' && state.selected) { e.preventDefault(); $('play').click(); } if (e.code === 'ArrowRight') seek(state.time + 1); if (e.code === 'ArrowLeft') seek(state.time - 1); });

let lastPaint = 0;
function tick(now) { if (state.playing && state.selected) { const dt = Math.min(.25, (now - (state.lastFrame || now)) / 1000); state.time = Math.min(state.duration, state.time + dt * state.speed / 60); if (state.time >= state.duration) state.playing = false; if (now - lastPaint > 85 || !state.playing) { render(); lastPaint = now; } } state.lastFrame = now; requestAnimationFrame(tick); }
window.replayDebug = { seek, getState, selectScenario, selectPatient, jumpKeyEvent, getMapState: () => ({ synthetic: syntheticLocations(), mode: $('map-shell').dataset.basemap, zoom: state.zoom, pan: [...state.pan], branch: mapBranch, details: mapDetails, expanded: mapExpanded, tileKeys: [...mapTiles.keys()], loadedTiles: [...mapTiles.values()].filter(tile => tile.dataset.loaded === 'true').length, failedTiles: [...mapTiles.values()].filter(tile => tile.dataset.loaded === 'error').length }), project: point => project(point), get keyEventTimes() { return keyEvents().map(e => e.t); }, get scenarioIds() { return state.scenarios.map(s => s.scenario.id); } };
new ResizeObserver(() => { if (state.selected) { renderMap(); renderScale(); scheduleBasemap(); } }).observe($('replay-map'));
initialise(); requestAnimationFrame(tick);
