"""공통 시나리오를 기존 문의/배정 규칙으로 재생할 지도용 사건 JSON 생성기.

실제 기관의 이름/좌표는 public.db에서, 도로 경로/자동차 추정시간은 OSRM에서
읽는다. 환자, 자원, 응답, 현장처치시간은 합성 실험 설정이다. 실제 119/지역 시스템의 완전한 복제나 임상
결과를 뜻하지 않는다. 기존 Simulation의 문의/수용/선택 엔진을 재사용하며,
119 도착 전 공통 구간, 이동 leg, 도착 후 전원 절차만 추가한다.
"""
from __future__ import annotations

import argparse
import copy
from contextlib import closing
import json
import math
from pathlib import Path
import sqlite3

from server.travel import synthetic_estimate
from .inquiry_queue import InquiryQueue
from .road_routes import (RoadRouteProvider, RoadRouteError, distance_meters,
                          point_along, prefix_route, synthetic_route)
from .sim import CONFIG, Hospital, Patient, Policy, Simulation, rng_for


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICIES = (
    dict(id='sequential', label='순차 문의 · 첫 수용', rule='R0', k=1, wait=0, notify=True),
    dict(id='parallel_first', label='병렬 문의 · 첫 수용 순서', rule='R0', k=3, wait=2, notify=True),
    dict(id='parallel_arrival', label='병렬 문의 · 예상 도착 우선', rule='R1', k=3, wait=2, notify=True),
)

ASSUMPTIONS = [
    '실제 기관 이름과 좌표를 사용하는 합성 시연이다. 환자·역량·자원·응답·출동시간은 실제 운영자료가 아니다.',
    '도로 경로 대신 직선거리 × 1.3, 시속 40km의 추정 이동시간과 직선 보간을 사용한다.',
    '정책에는 현재까지 발생한 사건과 문의에 대한 응답만 제공한다. 미래 응답·자원 변화는 제공하지 않는다.',
    '응답 환경은 환자·병원·문의 회차별로 고정한다. 실제 수용 여부는 그 시점의 자원 상태에 따라 달라질 수 있다.',
    '모든 기본 분기는 동일한 변경 알림과 도착 후 전원 규칙을 사용한다. 현행 체계에 알림이 없다는 가정이 아니다.',
    '병렬 R0/R1의 문의 수와 대기는 같다. 순차 분기는 문의 수와 대기도 달라 정책 묶음의 비교이다.',
    '도착 후 치료 불가 시 설정된 준비시간 뒤 대체 문의·수용 확인·전원을 한다. 실제 안정화/전원 지침의 재현은 아니다.',
    '치료 시작/종료는 합성 자원 점유의 시작/종료이며 환자 회복·생존·실제 예후를 뜻하지 않는다.',
    'R1은 규칙 기반 비교 후보이다. 솔버·최적성 증명·기존 체계 대비 개선 검증을 뜻하지 않는다.',
]


class RouteSnapshot:
    """한 생성 작업의 모든 분기에 같은 도로 입력을 제공한다. 프레임별 API 호출은 없다."""

    def __init__(self, provider=None, connector_speed_kmh=10):
        self.provider = provider
        self.connector_speed_kmh = connector_speed_kmh
        self.routes = {}
        self.used_keys = set()

    def route(self, origin, destination):
        key = tuple(round(float(x), 6) for p in (origin, destination) for x in p)
        self.used_keys.add(key)
        if key not in self.routes:
            if self.provider is None:
                route = synthetic_route(origin, destination)
                # Keep the legacy synthetic model's minute rounding for regression comparisons.
                route.update(synthetic_estimate(origin, destination))
            else:
                route = self.provider.route(origin, destination)
            self.routes[key] = route
        # Exact endpoints can differ by less than the six-decimal cache resolution.
        route = dict(self.routes[key])
        road = route['geometry']
        if route['geometry_kind'] == 'road':
            connectors = []
            for endpoint, a, b in (('origin', origin, road[0]),
                                   ('destination', road[-1], destination)):
                gap = distance_meters(a, b)
                if gap > 0.05:
                    connectors.append(dict(endpoint=endpoint, geometry=[list(a), list(b)],
                                           geometry_kind='offroad_connector', distance_meters=gap,
                                           included_in_duration=True,
                                           duration_seconds=gap / (self.connector_speed_kmh / 3.6)))
            connector_seconds = sum(c['duration_seconds'] for c in connectors)
            route.update(motion_geometry=[list(origin)] + road + [list(destination)],
                         connectors=connectors, road_duration_seconds=route['duration_seconds'],
                         connector_duration_seconds=connector_seconds,
                         duration_seconds=route['duration_seconds'] + connector_seconds,
                         road_distance_meters=route['distance_meters'],
                         distance_meters=route['distance_meters'] + sum(c['distance_meters'] for c in connectors),
                         connector_speed_kmh=self.connector_speed_kmh)
        else:
            route['motion_geometry'] = [list(origin), list(destination)]
        return route

    def summary(self, scenario_only=False):
        values = [self.routes[k] for k in self.used_keys] if scenario_only else list(self.routes.values())
        roads = [r for r in values if r['geometry_kind'] == 'road']
        gaps = [c['distance_meters'] for r in roads for c in r.get('connectors', [])]
        return dict(provider='osrm' if self.provider is not None else 'synthetic',
                    routes_evaluated=len(values), road_routes=len(roads),
                    fallback_routes=len(values) - len(roads) if self.provider is not None else 0,
                    synthetic_routes=len(values) - len(roads), scope='scenario' if scenario_only else 'generation',
                    max_snap_distance_meters=max(gaps, default=0),
                    large_snap_count=sum(gap > 500 for gap in gaps),
                    connector_speed_kmh=self.connector_speed_kmh,
                    live_traffic=False, ambulance_priority=False,
                    timing_resolution='ceil_to_minutes',
                    progress_model='uniform_distance_proportional_duration')


def _route_fields(route):
    return {key: copy.deepcopy(value) for key, value in route.items()}


def load_public_hospitals(db_path=ROOT / 'data/public.db', count=8):
    """공개 DB를 읽기 전용으로 열고 이름/좌표만 가져온다. 오류 시 합성 기관으로 대체하지 않는다."""
    path = Path(db_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f'공개 병원 DB가 없습니다: {path}')
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        rows = [dict(row) for row in connection.execute('SELECT id,name,lat,lon FROM hospitals ORDER BY id')]
    rows = [r for r in rows if r['lat'] is not None and r['lon'] is not None
            and -90 <= r['lat'] <= 90 and -180 <= r['lon'] <= 180]
    if len(rows) < count:
        raise ValueError(f'유효한 좌표가 있는 공개 병원이 {len(rows)}개입니다. {count}개가 필요합니다.')
    # 수원·오산·평택의 공간적으로 구분되는 기관. 기관별 실제 임상 역량을 선택 기준으로 쓰지 않는다.
    selected_ids = ['NMC-A2100024', 'NMC-A2100301', 'NMC-A2100309', 'NMC-A2100046',
                    'NMC-A2100002', 'NMC-A2100020', 'NMC-A2100032', 'NMC-A2100045']
    by_id = {h['id']: h for h in rows}
    selected = [by_id[hid] for hid in selected_ids if hid in by_id]
    selected.extend(h for h in rows if h['id'] not in {x['id'] for x in selected})
    return selected[:count]


def _point(value, field):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f'{field}: [위도, 경도]가 필요합니다.')
    lat, lon = map(float, value)
    if not math.isfinite(lat) or not math.isfinite(lon) or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError(f'{field}: 유효하지 않은 좌표입니다.')
    return lat, lon


def validate_scenario(scenario):
    if not scenario.get('hospitals') or not scenario.get('patients'):
        raise ValueError('hospitals와 patients가 필요합니다.')
    if not isinstance(scenario.get('max_minutes', 180), int) or scenario.get('max_minutes', 180) < 1:
        raise ValueError('관찰 종료시각은 양의 정수여야 합니다.')
    ids = [h['id'] for h in scenario['hospitals']]
    if len(ids) != len(set(ids)):
        raise ValueError('병원 ID가 중복됩니다.')
    for h in scenario['hospitals']:
        _point((h['lat'], h['lon']), f"병원 {h['id']}")
        for n in h.get('capacity', {}).values():
            if not isinstance(n, int) or n < 0:
                raise ValueError('자원 용량은 0 이상의 정수여야 합니다.')
    patients = scenario['patients']
    if len({p['id'] for p in patients}) != len(patients):
        raise ValueError('환자 ID가 중복됩니다.')
    for p in patients:
        _point(p['location'], f"환자 {p['id']}")
        _point(p['ambulance_origin'], f"환자 {p['id']} 구급차 출발지")
        times = [p.get('occur_t', 0), p.get('dispatch_t', p.get('occur_t', 0)),
                 p['ambulance_arrive_t'], p['search_start_t']]
        if any(not isinstance(t, int) or t < 0 for t in times) or times != sorted(times):
            raise ValueError('발생·출동·119 도착·문의 시작 시각은 순서대로 0 이상의 정수여야 합니다.')
        if p.get('priority', 4) not in (1, 2, 4):
            raise ValueError('실험 우선순위는 1, 2, 4 중 하나여야 합니다.')
        if p.get('treatment_minutes', 30) <= 0:
            raise ValueError('합성 치료 점유시간은 양수여야 합니다.')
        if any(not isinstance(n, int) or n <= 0 for n in p.get('resource_demands', {}).values()):
            raise ValueError('환자 자원 수요는 양의 정수여야 합니다.')
    for event in scenario.get('exogenous_events', []):
        if event.get('hospital') is not None and event['hospital'] not in ids:
            raise ValueError('외생 사건의 병원 ID를 찾을 수 없습니다.')
        if event['type'] not in ('resource_unavailable', 'external_arrival'):
            raise ValueError('지원하지 않는 외생 사건입니다.')
        if not isinstance(event.get('t'), int) or event['t'] < 0:
            raise ValueError('외생 사건 시각은 0 이상의 정수여야 합니다.')
        if event['type'] == 'resource_unavailable' and event['until'] <= event['t']:
            raise ValueError('자원 복구 시각은 사용 불가 발생 이후여야 합니다.')
    queue = scenario.get('inquiry_queue', {})
    if queue.get('enabled'):
        InquiryQueue(ids, workers=queue.get('workers', 1))
        for job in queue.get('background_jobs', []):
            if job.get('hospital') not in ids:
                raise ValueError('문의 대기열 외생업무의 병원 ID를 찾을 수 없습니다.')
            if not isinstance(job.get('t'), int) or job['t'] < 0:
                raise ValueError('문의 대기열 외생업무 시각은 0 이상의 정수여야 합니다.')
            if not isinstance(job.get('service_minutes'), int) or job['service_minutes'] < 1:
                raise ValueError('문의 대기열 외생업무 소요시간은 양의 정수여야 합니다.')


class ReplaySimulation(Simulation):
    """기존 엔진의 문의/응답/배정을 재사용하는 지도 trace 확장."""

    def __init__(self, scenario, policy, routing=None):
        self.scenario = scenario
        self.routing = routing or RouteSnapshot()
        self.patient_specs = {p['id']: p for p in scenario['patients']}
        self.structural_capacities = {h['id']: dict(h.get('capacity', {})) for h in scenario['hospitals']}
        hospitals = [Hospital(h['id'], (h['lat'], h['lon']), set(h.get('capabilities', [])),
                              dict(h.get('capacity', {}))) for h in scenario['hospitals']]
        for h, spec in zip(hospitals, scenario['hospitals']):
            h.active = [[a['until'], dict(a['demand'])] for a in spec.get('initial_occupancy', [])]
        patients = [Patient(p['id'], p['search_start_t'], tuple(p['location']),
                            set(p.get('required_capabilities', [])), dict(p.get('resource_demands', {'ed_bed': 1})),
                            p.get('priority', 4), p.get('treatment_minutes', 30)) for p in scenario['patients']]
        self.legs = {p.id: [] for p in patients}
        self.failed_hospitals = {p.id: set() for p in patients}
        self.transfers = {p.id: 0 for p in patients}
        self.transfer_at = {}
        self.transfer_origins = {}
        self.assessed = set()
        self.resource_frames = {}
        self.decision_batches = []
        queue = scenario.get('inquiry_queue', {})
        self.inquiry_queue = InquiryQueue([h.id for h in hospitals], queue.get('workers', 1)) if queue.get('enabled') else None
        self.background_jobs = sorted(copy.deepcopy(queue.get('background_jobs', [])), key=lambda job: job['t']) if self.inquiry_queue else []
        self.inquiry_frames = []
        self._review_events = {}
        self._previous_inquiry_frame = None
        self.risk_frames = []
        self.risk_patients = {}
        self.risk_ever = set()
        self.invalid_transit_minutes = {p.id: 0 for p in patients}
        outages, walkins = [], []
        for n, event in enumerate(scenario.get('exogenous_events', [])):
            if event['type'] == 'resource_unavailable':
                outages.append((event['t'], event['until'], event['hospital'], event['resource']))
            elif event['type'] == 'external_arrival':
                hospital = next(h for h in hospitals if h.id == event['hospital'])
                external = Patient(f'EXTERNAL-{n}', event['t'], hospital.loc, set(),
                                   dict(event.get('demand', {'ed_bed': 1})), 4,
                                   event.get('duration', 40), external=True)
                walkins.append((event['t'], hospital.id, external))
        cfg = dict(CONFIG, horizon=1, tail=scenario.get('max_minutes', 180), arrival_rate=0,
                   walkin_rate=0, outage_rate=0, timeout=scenario.get('timeout_minutes', 6),
                   retry_wait=scenario.get('retry_minutes', 3),
                   accept_ttl=scenario.get('accept_ttl_minutes', 10))
        super().__init__(scenario.get('seed', 17), policy, cfg,
                         world=(hospitals, patients, sorted(walkins, key=lambda w: w[0])),
                         record=True, response_fn=self._response, outages=sorted(outages))

    def _enqueue_review(self, hid, t, pid, attempt, service, silent=False, kind='inquiry'):
        job = self.inquiry_queue.enqueue(hid, t, pid, attempt, service, kind=kind, silent=silent)
        # Scheduled times stay inside the environment. Events are released only
        # when their simulated time is reached, never at submission time.
        for at, event_type in ((job['start_t'], 'review_started'), (job['finish_t'], 'review_completed')):
            self._review_events.setdefault(at, []).append(dict(type=event_type, patient=pid,
                hospital=hid, kind=kind, attempt=attempt))
        return job

    def before_tick(self, t):
        while self.background_jobs and self.background_jobs[0]['t'] == t:
            job = self.background_jobs.pop(0)
            self._enqueue_review(job['hospital'], t, None, 0, job['service_minutes'], kind='background')

    def schedule_ask(self, p, hid, attempt, t):
        if self.inquiry_queue is None:
            return super().schedule_ask(p, hid, attempt, t)
        silent, service = self._response(p, hid, attempt)
        job = self._enqueue_review(hid, t, p.id, attempt, service, silent=silent)
        deadline = t + self.cfg['timeout']
        late = job['finish_t'] > deadline
        if late and not silent:
            self._review_events.setdefault(job['finish_t'], []).append(dict(type='late_reply',
                patient=p.id, hospital=hid, attempt=attempt, ignored=True))
        # A response after the sender's timeout cannot resurrect a cancelled ask.
        # The hospital's work continues; there is no automatic queue cancellation.
        return dict(hid=hid, at=deadline if silent else min(deadline, job['finish_t']),
                    silent=silent or late)

    def after_tick(self, t):
        for event in self._review_events.pop(t, []):
            kind = event.pop('type')
            self.ev(t, kind, **event)
        if self.inquiry_queue is not None:
            frame = self.inquiry_queue.snapshot(t)
            if frame != self._previous_inquiry_frame:
                self.inquiry_frames.append(dict(t=t, hospitals=frame))
                self._previous_inquiry_frame = frame
        # Integrate the previous minute's affected in-transit patients. The
        # counter is patient-minutes, not a clinical harm or outcome estimate.
        for pid in self.risk_patients:
            self.invalid_transit_minutes[pid] += 1
        affected = {}
        for h in self.hospitals.values():
            for resource, capacity in h.cap.items():
                excess = h.used(resource) + h.expected(resource) + h.waiting(resource) - capacity
                for pid in sorted(h.committed, key=lambda pid: (-self.patients[pid].arrive_t, pid)):
                    if excess <= 0:
                        break
                    p = self.patients[pid]
                    if p.state == 'TRANSIT' and p.demand.get(resource, 0):
                        affected[pid] = h.id
                        excess -= p.demand[resource]
        for pid, hid in affected.items():
            if self.risk_patients.get(pid) != hid:
                self.ev(t, 'resource_condition_risk', patient=pid, hospital=hid)
        for pid, hid in self.risk_patients.items():
            if affected.get(pid) != hid:
                self.ev(t, 'resource_condition_resolved', patient=pid, hospital=hid)
        if affected != self.risk_patients or affected:
            self.risk_frames.append(dict(t=t, patients=sorted(affected),
                invalid_transit_minutes=sum(self.invalid_transit_minutes.values())))
        self.risk_patients = affected
        self.risk_ever.update(affected)

    def _response(self, p, hid, attempt):
        env = self.scenario.get('response_environment', {})
        entry = env.get(p.id, {}).get(hid, env.get('*', {}).get(hid, {}))
        entries = entry.get('attempts')
        if entries:
            entry = entries[min(attempt - 1, len(entries) - 1)]
        if entry:
            return bool(entry.get('silent', False)), max(1, int(entry.get('delay', 2)))
        # 같은 키의 잠재 응답은 정책에 관계없이 같다. 문의 전에 정책으로 전달하지 않는다.
        r = rng_for(self.seed, 'map_response', p.id, hid, attempt)
        return r.random() < self.scenario.get('nonresponse', 0), r.randint(1, 3)

    def travel(self, p, hid, loc, leg=0):
        """정책에 제공하는 이동시간 추정값. 실제 이동 오차를 포함하지 않는다."""
        route = self.routing.route(loc, self.hospitals[hid].loc)
        return max(1, math.ceil(route['duration_seconds'] / 60))

    def position(self, p, t):
        if p.state != 'TRANSIT' or not self.legs[p.id]:
            return p.loc
        leg = self.legs[p.id][-1]
        ratio = (t - leg['depart_t']) / max(1, leg['arrive_t'] - leg['depart_t'])
        return tuple(point_along(leg.get('motion_geometry', leg['geometry']), ratio))

    def candidates(self, p, loc, exclude=()):
        # 일시 점유/사용 불가는 영구적인 역량 부재와 다르다. 현재 가용량이 0이어도
        # 등록 역량이 맞으면 문의 후보에 남긴다. 응답 시점의 can_accept가 수용을 결정하며,
        # 복구 예정시각이나 아직 발생하지 않은 사건은 후보 정렬에 사용하지 않는다.
        candidates = [h.id for h in self.hospitals.values() if h.id not in exclude and p.caps <= h.caps
                      and all(self.structural_capacities[h.id].get(r, 0) >= q for r, q in p.demand.items())]
        return sorted(candidates, key=lambda hid: (self.travel(p, hid, loc), hid))

    def send_asks(self, p, t, exclude=()):
        super().send_asks(p, t, exclude=set(exclude) | self.failed_hospitals[p.id])

    def resolve_asks(self, p, t):
        super().resolve_asks(p, t)
        # 기본 엔진은 SEARCH의 다음 batch만 자동 전송한다. 이송 중 영향 알림을 받은
        # 환자도 첫 대체 문의가 실패하면 남은 후보를 계속 확인한다. 전부 문의했으면
        # send_asks가 설정하는 retry_at까지 기다린 뒤 다시 시도한다.
        if p.state == 'TRANSIT' and p.notified and not p.asks and not p.valid \
                and (p.retry_at is None or p.retry_at <= t):
            p.retry_at = None
            self.send_asks(p, t, exclude={p.dest})

    def resolve_batch(self, t):
        if not self.ready:
            return
        ready = list(self.ready)
        space = math.prod(len(options) + 1 for _, options in ready)
        before = getattr(self.policy.rule, 'fallbacks', 0)
        super().resolve_batch(t)
        self.decision_batches.append(dict(t=t, patients=[p.id for p, _ in ready],
            candidate_combinations=space,
            method='greedy_fallback' if getattr(self.policy.rule, 'fallbacks', 0) > before else 'exact_enumeration',
            scope='same_minute_ready_patients_only',
            assignments={p.id: p.dest if p.state == 'TRANSIT' and p.depart_t == t else None
                         for p, _ in ready}))

    def _close_leg(self, p, t, loc, ended_by):
        if self.legs[p.id] and self.legs[p.id][-1].get('ended_by') is None:
            leg = self.legs[p.id][-1]
            ratio = max(0, min(1, (t - leg['depart_t']) / max(1, leg['arrive_t'] - leg['depart_t'])))
            if ratio < 1:
                motion = leg['motion_geometry']
                motion_length = sum(distance_meters(a, b) for a, b in zip(motion, motion[1:]))
                road = leg['geometry']
                road_length = sum(distance_meters(a, b) for a, b in zip(road, road[1:]))
                origin_gap = distance_meters(motion[0], road[0])
                covered = motion_length * ratio
                road_fraction = max(0, min(1, (covered - origin_gap) / road_length)) if road_length else 0
                # Keep true road geometry and estimated access connectors separate.
                cut = prefix_route(dict(geometry=motion, duration_seconds=leg['duration_seconds'],
                                        distance_meters=leg['distance_meters']), ratio)
                leg['motion_geometry'] = cut['geometry']
                leg['geometry'] = prefix_route(dict(geometry=road, duration_seconds=0,
                                                    distance_meters=0), road_fraction)['geometry']
                kept = []
                for connector in leg.get('connectors', []):
                    length = connector['distance_meters']
                    offset = 0 if connector['endpoint'] == 'origin' else origin_gap + road_length
                    fraction = max(0, min(1, (covered - offset) / length)) if length else 0
                    if fraction:
                        piece = prefix_route(dict(connector, duration_seconds=connector['duration_seconds']), fraction)
                        kept.append(piece)
                leg['connectors'] = kept
                leg['duration_seconds'] = cut['duration_seconds']
                leg['distance_meters'] = cut['distance_meters']
                if leg.get('geometry_kind') == 'road':
                    leg['planned_road_distance_meters'] = leg['road_distance_meters']
                    leg['planned_road_duration_seconds'] = leg['road_duration_seconds']
                    leg['road_distance_meters'] *= road_fraction
                    leg['road_duration_seconds'] *= road_fraction
                    leg['connector_duration_seconds'] = sum(c['duration_seconds'] for c in kept)
                    leg['snapped_endpoints'] = [leg['geometry'][0], leg['geometry'][-1]]
                    leg['requested_endpoints'] = [leg['origin'], list(loc)]
                leg['truncated_fraction'] = ratio
            leg['arrive_t'], leg['destination'], leg['ended_by'] = t, list(loc), ended_by

    def commit(self, p, hid, t, redirect=False):
        # Selection uses estimates only. The movement duration is realized after the decision.
        loc = self.position(p, t)
        if redirect:
            self._close_leg(p, t, loc, 'redirect')
            self.hospitals[p.dest].committed.pop(p.id, None)
            p.redirects += 1
            self.ev(t, 'redirect', patient=p.id, hospital=hid, previous=p.dest)
        transfer = p.id in self.transfer_origins and p.state == 'SEARCH'
        movement_kind = 'transfer' if transfer else 'transport'
        if redirect and self.legs[p.id]:
            movement_kind = self.legs[p.id][-1]['kind']
        if transfer:
            self.transfers[p.id] += 1
            self.ev(t, 'transfer_depart', patient=p.id, hospital=hid,
                    previous=self.transfer_origins.pop(p.id))
        route = self.routing.route(loc, self.hospitals[hid].loc)
        estimate = max(1, math.ceil(route['duration_seconds'] / 60))
        low, high = self.scenario.get('travel_error_range', [1.0, 1.0])
        factor = rng_for(self.seed, 'map_travel', p.id, hid, len(self.legs[p.id])).uniform(low, high)
        duration = max(1, math.ceil(estimate * factor))
        p.state, p.dest, p.origin, p.depart_t = 'TRANSIT', hid, loc, t
        p.arrive_t = t + duration
        p.first_arrive_plan = p.first_arrive_plan if p.first_arrive_plan is not None else p.arrive_t
        p.t_commit = p.t_commit if p.t_commit is not None else t
        p.asks, p.notified = [], False
        p.valid, p.first_valid_t = {}, None
        self.hospitals[hid].committed[p.id] = p.demand
        self.legs[p.id].append(dict(_route_fields(route), kind=movement_kind, depart_t=t,
                                   arrive_t=p.arrive_t, planned_arrive_t=p.arrive_t, origin=list(loc),
                                   destination=list(self.hospitals[hid].loc), hospital=hid, ended_by=None,
                                   estimated_minutes=estimate, realized_travel_factor=factor,
                                   planned_duration_seconds=route['duration_seconds']))
        self.ev(t, 'commit', patient=p.id, hospital=hid, eta=p.arrive_t,
                estimated_eta=t + estimate, transfer=transfer)

    def start_treatments(self, h, t):
        for p in list(h.queue):
            if p.external:
                continue
            self._close_leg(p, t, h.loc, 'arrival')
            spec = self.patient_specs[p.id]
            if p.id not in self.assessed:
                self.assessed.add(p.id)
                additional = set(spec.get('arrival_assessment_adds_capabilities', []))
                if additional:
                    p.caps.update(additional)
                    self.ev(t, 'condition_update', patient=p.id, hospital=h.id,
                            required_capabilities=sorted(p.caps), reason='synthetic_arrival_assessment')
            if not h.compatible(p) or not h.fits_now(p.demand):
                # All branches use this same transfer rule, independent of notification policy.
                h.queue.remove(p)
                p.state = 'TRANSFER_WAIT'
                self.failed_hospitals[p.id].add(h.id)
                self.transfer_origins[p.id] = h.id
                self.transfer_at[p.id] = t + self.scenario.get('transfer_preparation_minutes', 3)
                p.final_arrive_t = None  # A failed first arrival is not the final treatment hospital.
                self.ev(t, 'arrival_unable', patient=p.id, hospital=h.id,
                        reason='required_capability' if not p.caps <= h.caps else 'resource_capacity',
                        transfer_search_at=self.transfer_at[p.id])
        for p in self.patients.values():
            if p.state == 'TRANSFER_WAIT' and p.dest == h.id and self.transfer_at[p.id] <= t:
                p.state, p.loc = 'SEARCH', h.loc
                p.asks, p.valid, p.tried, p.first_valid_t, p.retry_at = [], {}, set(), None, None
                self.ev(t, 'transfer_search', patient=p.id, hospital=h.id)
                self.send_asks(p, t)
        super().start_treatments(h, t)
        # The engine calls this for every hospital at each minute. Replace the
        # same-minute frame until the last hospital has finished, then export
        # only changed frames. This includes releases without a separate event.
        self.resource_frames[t] = [dict(id=g.id, capacity=dict(g.cap),
            used={r: g.used(r) for r in g.cap}, committed={r: g.expected(r) for r in g.cap},
            queued={r: g.waiting(r) for r in g.cap},
            queue_count=len(g.queue)) for g in self.hospitals.values()]

    def run_replay(self):
        prefix, dispatch = [], {}
        end = self.cfg['horizon'] + self.cfg['tail']
        for pid, p in self.patient_specs.items():
            occur, sent, arrived, search = (p.get('occur_t', 0), p.get('dispatch_t', p.get('occur_t', 0)),
                                            p['ambulance_arrive_t'], p['search_start_t'])
            for t, kind in ((occur, 'patient_occurs'), (sent, 'ambulance_dispatched'),
                            (arrived, 'ambulance_arrive'), (arrived, 'scene_care_start'),
                            (search, 'scene_care_complete')):
                if t < end:
                    prefix.append(dict(t=t, type=kind, patient=pid))
            route = self.routing.route(p['ambulance_origin'], p['location'])
            dispatch[pid] = dict(_route_fields(route), kind='dispatch', depart_t=sent, arrive_t=arrived,
                                 origin=list(p['ambulance_origin']), destination=list(p['location']),
                                 hospital=None, planned_arrive_t=arrived, ended_by='arrival',
                                 timing_source=p.get('dispatch_timing_source', 'scenario_fixed'))
        metrics = super().run()
        # perf_counter belongs to performance measurement, not deterministic replay data.
        metrics.pop('decision_ms_mean', None)
        if metrics['unassigned'] == metrics['patients']:
            metrics['time_to_commit'] = None
        metrics['transfers'] = sum(self.transfers.values())
        observation_end = self.cfg['horizon'] + self.cfg['tail'] - 1
        metrics['travel_minutes'] = sum(max(0, min(leg['arrive_t'], observation_end) - leg['depart_t'])
                                        for legs in self.legs.values() for leg in legs)
        metrics['dispatch_travel_minutes'] = sum(max(0, min(p['ambulance_arrive_t'], observation_end)
                                                     - p.get('dispatch_t', p.get('occur_t', 0)))
                                                 for p in self.patient_specs.values())
        metrics['time_origin'] = 'search_start_t (119 도착 후 현장처치 완료)'
        completion = []
        outcomes = []
        for p in self.patients.values():
            completed = p.t_start + p.duration if p.t_start is not None else None
            if completed is not None and completed < end:
                completion.append(dict(t=completed, type='treatment_complete', patient=p.id, hospital=p.dest))
            if completed is not None and completed < end:
                state = 'COMPLETE'
            elif p.t_start is not None:
                state = 'INCOMPLETE'
            elif p.t_commit is None:
                state = 'UNASSIGNED'
            else:
                state = 'NOT_TREATED'
            if state != 'COMPLETE':
                completion.append(dict(t=end - 1, type='observation_end', patient=p.id, hospital=p.dest,
                                       state=state, reason='observation_limit'))
            outcomes.append(dict(id=p.id, state=state, destination_hospital_id=p.dest,
                                 priority=p.priority, occur_t=self.patient_specs[p.id].get('occur_t', 0),
                                 search_start_t=self.patient_specs[p.id]['search_start_t'],
                                 simulation_state=p.state,
                                 first_commit_t=p.t_commit, final_arrive_t=p.final_arrive_t,
                                 treatment_start_t=p.t_start,
                                 treatment_complete_t=completed if completed is not None and completed < end else None,
                                 transfers=self.transfers[p.id], redirects=p.redirects, inquiries=p.inquiries,
                                 final_required_capabilities=sorted(p.caps)))
        arrived = [p.final_arrive_t - self.patient_specs[p.id].get('occur_t', 0)
                   for p in self.patients.values() if p.final_arrive_t is not None]
        metrics['occur_to_final_arrival_mean'] = sum(arrived) / len(arrived) if arrived else None
        metrics['observation_end_t'] = end - 1
        metrics['completed'] = sum(p['state'] == 'COMPLETE' for p in outcomes)
        metrics['high_priority_unassigned'] = sum(p['priority'] == 4 and p['first_commit_t'] is None for p in outcomes)
        metrics['high_priority_untreated'] = sum(p['priority'] == 4 and p['treatment_start_t'] is None for p in outcomes)
        metrics['joint_batches'] = len(self.decision_batches)
        metrics['joint_max_batch_size'] = max((len(b['patients']) for b in self.decision_batches), default=0)
        metrics['commit_max'] = max((p['first_commit_t'] - p['search_start_t'] for p in outcomes
                                     if p['first_commit_t'] is not None), default=None)
        metrics['occur_to_final_arrival_max'] = max(arrived, default=None)
        metrics['resource_invalid_transit_minutes'] = sum(self.invalid_transit_minutes.values())
        metrics['resource_condition_affected_patients'] = len(self.risk_ever)
        queue_report = self.inquiry_queue.report(observation_end) if self.inquiry_queue is not None else None
        if queue_report is not None:
            jobs = [j for j in self.inquiry_queue.jobs if j['kind'] == 'inquiry' and j['submitted_t'] <= observation_end]
            elapsed_wait = sum(max(0, min(observation_end, j['start_t']) - j['submitted_t']) for j in jobs)
            metrics['inquiry_queue_wait_mean'] = elapsed_wait / len(jobs) if jobs else None
            metrics['inquiry_peak_waiting'] = max((sum(h['waiting'] for h in f['hospitals'])
                                                 for f in self.inquiry_frames), default=0)
            metrics['inquiry_service_minutes'] = sum(max(0, min(observation_end, j['finish_t']) - j['start_t']) for j in jobs)
            metrics['inquiry_unfinished_jobs'] = sum(j['finish_t'] > observation_end for j in jobs)
            metrics['inquiry_late_replies'] = sum(e['type'] == 'late_reply' for e in self.log)
        frames, previous = [], None
        for t, frame in sorted(self.resource_frames.items()):
            if frame != previous:
                frames.append(dict(t=t, hospitals=frame))
                previous = frame
        # Stable sort keeps dispatch/common events ahead of engine events at the same minute.
        events = sorted(prefix + self.log + completion, key=lambda e: e['t'])
        return dict(events=events, trajectories=[dict(patient=pid, legs=[dispatch[pid]] + legs)
                                                for pid, legs in self.legs.items()],
                    metrics=metrics, patient_outcomes=outcomes,
                    resource_snapshots=frames, decision_batches=self.decision_batches,
                    inquiry_snapshots=self.inquiry_frames, inquiry_queue_report=queue_report,
                    resource_risk_snapshots=self.risk_frames)


def generate_replay(scenario, policies=None, routing=None, dispatch_timing='scenario'):
    """고정 입력을 분기마다 복사해 재생한다. 전달받은 scenario는 변경하지 않는다."""
    validate_scenario(scenario)
    frozen = copy.deepcopy(scenario)
    routing = routing or RouteSnapshot()
    routing.used_keys.clear()
    if dispatch_timing not in ('scenario', 'route'):
        raise ValueError('dispatch_timing은 scenario 또는 route여야 합니다.')
    if dispatch_timing == 'route':
        for patient in frozen['patients']:
            care_minutes = patient['search_start_t'] - patient['ambulance_arrive_t']
            route = routing.route(patient['ambulance_origin'], patient['location'])
            arrived = patient.get('dispatch_t', patient.get('occur_t', 0)) + max(1, math.ceil(route['duration_seconds'] / 60))
            patient['ambulance_arrive_t'] = arrived
            patient['search_start_t'] = arrived + care_minutes
            patient['dispatch_timing_source'] = 'route_estimate_plus_synthetic_connector'
    if routing.provider is not None:
        assumptions = frozen.setdefault('assumptions', [])
        assumptions[:] = [a for a in assumptions if '도로 경로 대신 직선거리' not in a]
        assumptions.append('OSRM의 일반 자동차 도로 경로·예상시간을 고정 캐시해 모든 분기에 공유한다. 실시간 교통·구급차 우선주행 시간은 아니다.')
        assumptions.append('도로와 입력 좌표 사이 접속 구간은 점선·시속 10km 추정으로 따로 표시한다. 이동시간은 분 단위로 올림하며 구간 내 위치는 누적거리 비례로 보간한다.')
        if any(p.get('dispatch_timing_source') != 'route_estimate_plus_synthetic_connector'
               for p in frozen['patients']):
            assumptions.append('이 사용자 시나리오는 출동·119 도착 시각을 입력값으로 유지한다. 출동 도로 형상과 별개로 그 구간 재생시간은 OSRM 예상시간이 아니다. --dispatch-timing route로 재산정할 수 있다.')
    branches = []
    for spec in policies or frozen.get('policies') or DEFAULT_POLICIES:
        spec = dict(spec)
        branch_id, label = spec.pop('id'), spec.pop('label')
        policy = Policy(**spec, name=branch_id)
        sim = ReplaySimulation(copy.deepcopy(frozen), policy, routing=routing)
        branch = sim.run_replay()
        branch.update(id=branch_id, label=label, policy=policy.spec())
        branches.append(branch)
    duration = max((e['t'] for b in branches for e in b['events']), default=0)
    return dict(schema_version=2, scenario=frozen, branches=branches, duration_minutes=duration,
                routing_summary=routing.summary(scenario_only=True))


def build_default_scenarios(db_path=ROOT / 'data/public.db', routing=None):
    hospitals = load_public_hospitals(db_path)
    scene = [37.12, 127.07]
    routing = routing or RouteSnapshot()
    hospitals.sort(key=lambda h: (routing.route(scene, (h['lat'], h['lon']))['duration_seconds'], h['id']))
    for h in hospitals:
        h.update(capacity={'ed_bed': 2, 'special_team': 1}, capabilities=['가상 응급진료'],
                 clinical_data_source='synthetic', identity_source='data/public.db (NMC)')
    # Added capability is a synthetic assessment scenario, unrelated to these institutions' real service levels.
    hospitals[-1]['capabilities'].append('가상 추가치료')
    response_env = {'*': {h['id']: dict(delay=2, silent=False) for h in hospitals}}
    for i, delay in enumerate((3, 1, 2)):
        response_env['*'][hospitals[i]['id']]['delay'] = delay
    patient = dict(id='P01', label='가상 환자 A', location=scene, occur_t=0, dispatch_t=1,
                   ambulance_origin=[37.09, 127.04], ambulance_arrive_t=7, search_start_t=10,
                   required_capabilities=['가상 응급진료'], resource_demands={'ed_bed': 1, 'special_team': 1},
                   priority=4, treatment_minutes=25)
    if routing.provider is not None:
        dispatch = routing.route(patient['ambulance_origin'], scene)
        patient['ambulance_arrive_t'] = patient['dispatch_t'] + max(1, math.ceil(dispatch['duration_seconds'] / 60))
        patient['search_start_t'] = patient['ambulance_arrive_t'] + 3
        patient['dispatch_timing_source'] = 'route_estimate_plus_synthetic_connector'
    base = dict(seed=17, synthetic=True, assumptions=list(ASSUMPTIONS), hospitals=hospitals,
                patients=[patient], exogenous_events=[], response_environment=response_env,
                transfer_preparation_minutes=3, max_minutes=180)
    normal = copy.deepcopy(base)
    normal.update(id='normal', label='일반 배정: 첫 응답과 도착시간',
                  description='같은 응답을 보고 첫 수용 순서와 예상 도착 우선 규칙을 비교한다.')
    change = copy.deepcopy(base)
    change.update(id='change_enroute', label='이송 중 자원 변화',
                  description='가까운 병원의 전문팀이 일시 사용 불가가 된다. 모든 분기가 같은 변경 알림을 받는다.')
    change['exogenous_events'] = [dict(t=patient['search_start_t'] + 4, type='resource_unavailable', hospital=hospitals[0]['id'],
                                       resource='special_team', until=patient['search_start_t'] + 60)]
    transfer = copy.deepcopy(base)
    transfer.update(id='transfer_after_arrival', label='도착 후 추가 치료 필요 · 전원',
                    description='도착 평가에서 추가 치료 필요가 드러나는 합성 사례. 기존 병원에서 새 수용 확인 후 전원한다.')
    transfer['patients'][0]['arrival_assessment_adds_capabilities'] = ['가상 추가치료']
    transfer['assumptions'].append('추가 치료 필요는 도착 시 평가로 확인되며, 그 전에는 어떤 정책에도 공개하지 않는다.')
    unavailable = copy.deepcopy(base)
    unavailable.update(id='no_capacity', label='모든 병원에 자원이 없는 경우',
                       description='모든 병원의 전문팀이 관찰 종료까지 사용 불가다. 알고리즘도 없는 자원을 만들 수 없다.')
    unavailable['max_minutes'] = 60
    unavailable['exogenous_events'] = [dict(t=0, type='resource_unavailable', hospital=h['id'],
                                            resource='special_team', until=200) for h in hospitals]
    return [normal, change, transfer, unavailable]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'simulation/viewer/map_replay_data.json')
    parser.add_argument('--db', type=Path, default=ROOT / 'data/public.db')
    parser.add_argument('--scenario', type=Path, help='사용자 고정 시나리오 JSON: 단일 객체 또는 {scenarios:[입력 객체]}')
    parser.add_argument('--suite', choices=('all', 'single', 'multi'), default='all',
                        help='기본 사례: 단일 환자, 다수 환자 또는 모두')
    parser.add_argument('--experiment-replays', type=Path,
                        help='반복 실험에서 내보낸 개선·악화 사례의 입력도 같은 엔진으로 재생성해 추가')
    parser.add_argument('--synthetic-experiment-replays', type=Path, action='append', default=[],
                        help='합성 이동 실험 예시를 같은 이동 모드로 앞에 추가. 여러 파일이면 옵션을 반복한다.')
    parser.add_argument('--routing', choices=('osrm', 'synthetic'), default='osrm',
                        help='도로 경로·시간 수집. synthetic은 명시적인 직선 추정 비교 모드')
    parser.add_argument('--route-cache', type=Path, default=ROOT / 'simulation/route_cache')
    parser.add_argument('--offline', action='store_true', help='도로 캐시만 사용하며 외부 API 호출하지 않음')
    parser.add_argument('--allow-synthetic-fallback', action='store_true',
                        help='도로 조회 실패 시 직선 추정으로 명시 표시. 기본값은 실패 시 생성 중단')
    parser.add_argument('--dispatch-timing', choices=('scenario', 'route'), default='scenario',
                        help='사용자 시나리오 출동시각 유지/도로 추정시간으로 재계산. 기본 사례는 도로 추정시간 사용')
    args = parser.parse_args(argv)
    provider = RoadRouteProvider(cache_dir=args.route_cache, offline=args.offline,
                                 allow_synthetic_fallback=args.allow_synthetic_fallback) if args.routing == 'osrm' else None
    routing = RouteSnapshot(provider)
    try:
        if args.scenario:
            value = json.loads(args.scenario.read_text(encoding='utf-8'))
            scenarios = value.get('scenarios', [value]) if isinstance(value, dict) else value
        else:
            single = build_default_scenarios(args.db, routing=routing)
            if args.suite == 'single':
                scenarios = single
            else:
                from .multi_scenarios import build_multi_scenarios
                multi = build_multi_scenarios(single[0])
                scenarios = multi + single if args.suite == 'all' else multi
        if args.experiment_replays:
            exported = json.loads(args.experiment_replays.read_text(encoding='utf-8'))
            for item in exported['scenarios']:
                extra = copy.deepcopy(item['scenario'])
                selected = extra.get('experiment_selection', {})
                selection = selected.get('selection')
                if selection in ('best_observed', 'worst_observed'):
                    kind = '관측된 최대 개선' if selection == 'best_observed' else '관측된 최대 악화'
                    extra['label'] = extra['label'].replace(selection, kind)
                    rule = next((p['rule'] for p in extra.get('policies', []) if p['id'] == selected['policy_id']), selected['policy_id'])
                    extra['description'] += f" 개발 비교에서 골라낸 설명용 사례: {rule}의 R0 대비 같은 환자 평균 도착 차이 {selected['difference']:+.2f}분. 대표 사례가 아니다."
                scenarios.append(extra)
        if len({s['id'] for s in scenarios}) != len(scenarios):
            parser.error('시나리오 ID가 중복됩니다.')
        payload = dict(schema_version=2, scenarios=[generate_replay(s, routing=routing,
                        dispatch_timing=args.dispatch_timing) for s in scenarios])
        if args.synthetic_experiment_replays:
            extras = []
            for replay_file in args.synthetic_experiment_replays:
                exported = json.loads(replay_file.read_text(encoding='utf-8'))
                extras.extend(copy.deepcopy(item['scenario']) for item in exported['scenarios'])
            ids = [s['id'] for s in scenarios + extras]
            if len(ids) != len(set(ids)):
                parser.error('추가 실험 시나리오 ID가 중복됩니다.')
            # Preserve the route mode in which the reported differences were
            # measured. Replacing it with OSRM could change the selected result.
            synthetic = RouteSnapshot()
            payload['scenarios'][:0] = [generate_replay(s, routing=synthetic) for s in extras]
    except RoadRouteError as error:
        parser.exit(1, f'도로 경로를 확보하지 못했습니다: {error} 기존 재생 JSON은 유지됩니다.\n')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    staged = args.output.with_suffix('.tmp')
    staged.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    staged.replace(args.output)
    print(f"지도 재생 JSON 저장: {args.output} / 시나리오 {len(payload['scenarios'])}개 / 분기 {sum(len(s['branches']) for s in payload['scenarios'])}개")
    print('이동 모델:', json.dumps(routing.summary(), ensure_ascii=False))


if __name__ == '__main__':
    main()
