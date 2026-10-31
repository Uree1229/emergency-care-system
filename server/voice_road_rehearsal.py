"""Isolated voice/allocation lifecycle using a frozen Kakao road snapshot.

python -m server.voice_road_rehearsal --output .codex-build/voice-road-new

Only coordinates are sent to Kakao. All patients, hospitals, decisions and
controlled elapsed times are fixtures; no existing database is opened.
"""
import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import time
import traceback

from . import allocation_baseline
from . import voice_allocation_rehearsal as base
from simulation.kakao_routes import KakaoRouteProvider
from simulation.road_routes import RoadRouteError, point_along, prefix_route, distance_meters

ROOT = Path(__file__).resolve().parents[1]
REVISION = 'voice-road-rehearsal-20261030-v1'
ORIGIN = (35.16, 126.85)
HOSPITALS = ((35.161, 126.85), (35.171, 126.85), (35.181, 126.85))
CASES = ('normal_arrival', 'enroute_resource_keep', 'enroute_resource_reroute',
         'no_hospital_response')


def _key(origin, destination):
    return tuple(round(float(x), 5) for x in (*origin, *destination))


class SnapshotEstimator:
    """Precollect explicitly; actual API/worker execution cannot fetch or fallback."""
    def __init__(self, provider):
        self.provider = provider
        self.routes = {}
        self.used = set()

    def collect(self, origin, destination):
        key = _key(origin, destination)
        if key not in self.routes:
            route = self.provider.route(key[:2], key[2:])
            if route.get('provider') != 'kakao' or route.get('geometry_kind') != 'road':
                raise RoadRouteError('A Kakao road route is required; fallback refused.')
            if route.get('ambulance_priority') is not False:
                raise RoadRouteError('Ordinary-car provenance is required.')
            self.routes[key] = copy.deepcopy(route)
        return copy.deepcopy(self.routes[key])

    def route(self, origin, destination):
        key = _key(origin, destination)
        if key not in self.routes:
            raise RoadRouteError('Coordinate pair is outside the frozen road snapshot.')
        self.used.add(key)
        return copy.deepcopy(self.routes[key])

    @staticmethod
    def connector_seconds(route):
        return sum(c['distance_meters'] / (10000 / 3600)
                   for c in route.get('connectors', []) if not c.get('included_in_duration'))

    def estimate(self, origin, destination):
        route = self.route(origin, destination)
        connector = self.connector_seconds(route)
        seconds = max(60, math.ceil((route['duration_seconds'] + connector) / 60) * 60)
        return dict(duration_seconds=seconds, distance_meters=round(route['distance_meters']),
                    provider='kakao', scenario='frozen_ordinary_car_snapshot_with_explicit_connectors',
                    fetched_at=route['fetched_at'], road_duration_seconds=route['duration_seconds'],
                    connector_seconds=connector, ambulance_priority=False)

    def prepare(self):
        for destination in HOSPITALS:
            self.collect(ORIGIN, destination)
        # The new observation is an explicit midpoint on the originally fetched
        # road. Rerouting uses this observation before any replacement inquiry.
        midpoint = tuple(round(x, 5) for x in point_along(self.routes[_key(ORIGIN, HOSPITALS[0])], .5))
        for destination in HOSPITALS:
            self.collect(midpoint, destination)
        return midpoint


class RoadFlow(base.Flow):
    def __init__(self, directory, snapshot):
        super().__init__(directory)
        self.snapshot = snapshot
        self.leg_origin = ORIGIN
        self.leg_started_at = None
        self.road_legs = []
        self.snapshots = []
        self.last_arrival_at = None

    def _open(self):
        super()._open()
        # Both the HTTP handler and manual workers hold this estimator instance.
        # Replace only the isolated fixture's method; frozen sources stay intact.
        self.estimator.estimate = self.snapshot.estimate
        return self

    def call(self, method, path, body=None, expected=200):
        # Explicit fixture clock ticks make intermediate states reviewable.
        # These ticks are scripted API pacing, not measured human work time.
        self.now += 1
        result = super().call(method, path, body, expected)
        if method == 'POST' and path.endswith('/arrive') and expected == 200:
            self.last_arrival_at = self.now
        from .service import patient_view
        self.snapshots.append(dict(at=self.now, patients=[patient_view(self.conn, row['id'], self.now)
            for row in self.conn.execute('SELECT id FROM patients ORDER BY id').fetchall()]))
        return result

    def depart(self, inquiry):
        super().depart(inquiry)
        self.leg_started_at = self.now

    def arrive(self, hid):
        destination = HOSPITALS[int(hid[-1]) - 1]
        estimate = self.snapshot.estimate(self.leg_origin, destination)
        self.now = max(self.now, self.leg_started_at + estimate['duration_seconds'])
        super().arrive(hid)
        route = self.snapshot.route(self.leg_origin, destination)
        self.road_legs.append(dict(origin=list(self.leg_origin), destination=list(destination),
            source_depart_at=self.leg_started_at, source_arrive_at=self.last_arrival_at, hospital=hid,
            geometry=route['geometry'], geometry_kind='road', provider='kakao', route=route,
            kind='transport', ended_by='arrival_confirmed', estimate=estimate))


def road_reroute(flow, midpoint):
    flow.depart(flow.setup_accepted())
    previous = flow.review()['review']
    initial = flow.snapshot.estimate(ORIGIN, HOSPITALS[0])
    flow.now += max(1, initial['duration_seconds'] // 2)
    observed_at = flow.now
    flow.call('PATCH', f'/api/patients/{flow.pid}/transport-position',
              dict(lat=midpoint[0], lon=midpoint[1], measured_at=observed_at))
    current = flow.loss()
    rejected = flow.decision('adjust', previous, expected=409)
    flow.require('old_transport_approval_cannot_adjust_destination',
        rejected['error'] == 'TRANSPORT_REVIEW_CONFLICT'
        and flow.patient()['destination_hospital_id'] == 'E2E-H1')
    flow.decision('adjust', current)
    flow.tick_transport()
    pending = flow.inquiry('E2E-H2')
    rejected = flow.call('POST', f'/api/patients/{flow.pid}/change-destination',
        dict(inquiry_id=pending['id'], lat=midpoint[0], lon=midpoint[1]), expected=409)
    flow.require('pending_alternative_cannot_be_destination', rejected['error'] == 'NOT_ACCEPTED')
    accepted = flow.accept('E2E-H2')
    flow.tick_transport()
    flow.require('road_alternative_waits_for_human_change',
        flow.review()['workflow']['state'] == 'AWAITING_CHANGE'
        and flow.patient()['destination_hospital_id'] == 'E2E-H1')
    changed = flow.call('POST', f'/api/patients/{flow.pid}/change-destination', {'inquiry_id': accepted['id']})
    flow.require('road_reroute_uses_observed_current_position_and_new_acceptance',
        changed['destination_hospital_id'] == 'E2E-H2' and changed['state'] == 'IN_TRANSIT')
    original_route = flow.snapshot.route(ORIGIN, HOSPITALS[0])
    route = prefix_route(original_route, .5)
    route['connectors'] = [c for c in original_route.get('connectors', []) if c.get('endpoint') == 'origin']
    # Rounding the scripted observation to the backend's five decimals adds a
    # short explicit connector, rather than silently changing road geometry.
    route['connectors'].append(dict(endpoint='destination', geometry=[route['geometry'][-1], list(midpoint)],
        geometry_kind='offroad_connector', included_in_duration=False,
        distance_meters=distance_meters(route['geometry'][-1], midpoint),
        source='coordinate_rounding_under_one_meter'))
    flow.road_legs.append(dict(origin=list(ORIGIN), destination=list(midpoint), hospital='E2E-H1',
        source_depart_at=flow.leg_started_at, source_arrive_at=observed_at,
        observed_current_position=list(midpoint), progress_fraction=.5,
        geometry=route['geometry'], geometry_kind='road', provider='kakao', route=route,
        kind='transport', ended_by='position_observed',
        interpolation='uniform_road_distance_not_gps_measurement'))
    flow.leg_origin, flow.leg_started_at = midpoint, flow.now
    flow.arrive('E2E-H2')


def road_no_response(flow):
    flow.create()
    flow.tick_inquiry()
    flow.require('no_acceptance_means_no_assignment', not flow.plan()['result']['confirmable_now'])
    flow.now += 61
    flow.tick_inquiry()
    # The start occurs after voice API clock ticks. Use the recorded job
    # deadline, rather than the parent fixture's fixed NOW+301 shortcut.
    flow.now = max(flow.now, flow.workflow()['deadline_at'] + 1)
    flow.tick_inquiry()
    flow.require('deadline_does_not_invent_hospital_response',
        flow.workflow()['state'] == 'EXHAUSTED'
        and all(q['state'] in ('PENDING', 'CHECKING') for q in flow.patient()['inquiries'])
        and flow.patient()['destination_hospital_id'] is None)


def _hashes():
    files = set(base.SOURCE_FILES) | {'simulation/kakao_routes.py', 'simulation/road_routes.py',
                                    'server/voice_road_rehearsal.py'}
    return {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in sorted(files)}


def run(output, *, provider=None):
    baseline = allocation_baseline.verify()
    if not baseline['ok']:
        raise ValueError('Frozen server baseline does not match.')
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    before = _hashes()
    for name in before:
        target = output/'sources'/name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT/name).read_bytes())
    protocol = dict(revision=REVISION, planned_cases=list(CASES), inquiry_policy='fixed_3',
        solver_budget_seconds=3, baseline_manifest_sha256=baseline['manifest_sha256'],
        source_hashes=before, origin=list(ORIGIN), hospital_coordinates=[list(h) for h in HOSPITALS],
        routing='kakao_ordinary_car_snapshot', connector_model_kmh=10,
        speech_mode='synthetic_transcripts_not_microphone_or_stt',
        arrival_clock='not_before_minute_rounded_route_eta',
        midpoint_rule='half_road_distance_on_first_hospital_route_before_outcomes',
        mode='actual_http_and_manual_worker_cycles')
    (output/'protocol.json').write_text(json.dumps(protocol, ensure_ascii=False, indent=2), encoding='utf-8')
    report = dict(revision=REVISION, synthetic=True, actual_speech_count=0, actual_hospital_requests=0,
        existing_business_db_accessed=False, controlled_clock=True, solver_budget_seconds=3,
        baseline_before=baseline, planned_cases=list(CASES), travel_provider='kakao_snapshot',
        inquiry_policy='fixed_3', cases=[], passed=0, failed=0, source_hashes_before=before)
    snapshot = SnapshotEstimator(provider or KakaoRouteProvider(cache_dir=output/'route-cache'))
    try:
        midpoint = snapshot.prepare()
        for name in CASES:
            flow = RoadFlow(output/name, snapshot)
            item = dict(name=name)
            started = time.perf_counter()
            try:
                with flow:
                    if name == 'enroute_resource_reroute':
                        road_reroute(flow, midpoint)
                    elif name == 'no_hospital_response':
                        road_no_response(flow)
                    else:
                        getattr(base, name)(flow)
                    item.update(final_state=flow.patient()['state'], **{'pass': True})
            except Exception:
                item.update(error=traceback.format_exc(), **{'pass': False})
            item.update(steps=flow.steps, requests=flow.requests, solver_runs=flow.solves,
                        road_legs=flow.road_legs, verification_wall_seconds=time.perf_counter()-started)
            observations = dict(snapshots=flow.snapshots,
                trajectories=[dict(patient=flow.pid, legs=flow.road_legs)] if flow.pid else [],
                routing_summary=dict(provider='kakao', mode='frozen_kakao_snapshot',
                    live_traffic=False, ambulance_priority=False),
                assumptions=['일반 자동차 도로 스냅샷과 합성 환자·음성 텍스트·병원·자원·사람 역할 응답.',
                    '도로상 중간 위치는 거리 비례 보간한 합성 관찰이며 실제 GPS가 아니다.',
                    'API 사이 1초 진행은 통제 시계이며 실제 입력·검토시간이 아니다.'])
            if flow.directory.is_dir():
                (flow.directory/'replay_observations.json').write_text(
                    json.dumps(observations, ensure_ascii=False, indent=2), encoding='utf-8')
            report['cases'].append(item)
            report['passed' if item['pass'] else 'failed'] += 1
    except RoadRouteError as error:
        # Error text from the provider is sanitized; no headers/key/raw body.
        report['routing_error'] = str(error)
    routes = [dict(key=list(key), used_by_server=key in snapshot.used, route=route)
              for key, route in sorted(snapshot.routes.items())]
    (output/'routes.json').write_text(json.dumps(routes, ensure_ascii=False, indent=2), encoding='utf-8')
    after = _hashes()
    report.update(source_hashes_after=after, sources_stable=before == after,
        baseline_after=allocation_baseline.verify(), collected_route_count=len(routes),
        used_route_count=len(snapshot.used), external_requests=getattr(snapshot.provider, 'requests_completed', None),
        routes_sha256=hashlib.sha256((output/'routes.json').read_bytes()).hexdigest())
    report['pass'] = (report['passed'] == len(CASES) and report['failed'] == 0
                      and report['sources_stable'] and report['baseline_after']['ok'])
    (output/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    lines = ['# 최신 음성 흐름과 카카오 도로 연결 검증', '',
        f'- 전체 검증: {"PASS" if report["pass"] else "FAIL"}',
        f'- {report["passed"]}개 통과 / {report["failed"]}개 실패, 실제 도로 경로 {len(routes)}개 수집.',
        '- 실제 HTTP·문의·3초 CP-SAT·사람 역할 수용/확정·도로상 관찰위치 대체 문의를 사용한다.',
        '- 음성 인식문·환자·병원·자원·담당자 판단은 합성이다. 실제 마이크·GPS·병원 전송은 포함하지 않는다.',
        '- 도로 ETA는 조회 시점 일반 자동차 예상값이다. 접속 구간은 시속 10km 합성 추정이며 분 단위로 올림한다.',
        '- 도착 사건은 경로 ETA 이후에 호출한다. 회신·조작 소요시간은 사람의 실측이 아니다.',
        '- 좌표 쌍을 실행 전에 수집하고 실행 중 외부 조회·합성 대체를 금지한다.', '',
        '| 사례 | 검증 | 최종 상태 |', '|---|---|---|']
    for item in report['cases']:
        lines.append(f'| {item["name"]} | {"PASS" if item["pass"] else "FAIL"} | {item.get("final_state", "-")} |')
    if report.get('routing_error'):
        lines += ['', '도로 입력 미확보: '+report['routing_error']]
    (output/'report.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, help='New isolated output directory.')
    parser.add_argument('--route-cache', type=Path, help='Explicit existing Kakao cache, with normal age validation.')
    parser.add_argument('--offline', action='store_true', help='Refuse network; require unexpired cached routes.')
    args = parser.parse_args()
    provider = KakaoRouteProvider(cache_dir=args.route_cache or Path(args.output)/'route-cache', offline=args.offline)
    result = run(args.output, provider=provider)
    print(json.dumps({k: result[k] for k in ('pass', 'passed', 'failed', 'collected_route_count', 'used_route_count')}, ensure_ascii=False))
    return 0 if result['pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
