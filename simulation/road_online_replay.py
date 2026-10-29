"""Fresh five-policy replay on public hospital locations and road snapshots.

This is a separate demonstration dataset, not a remapping of the locked online
validation traces. Real names/coordinates and ordinary-car road estimates are
inputs to each policy before inquiry, assignment, movement and reassignment.
Patients, clinical capabilities, resource quantities and replies stay synthetic.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path

from .kakao_routes import KakaoRouteProvider, snapshot_summary
from .map_replay import ROOT, RouteSnapshot, load_public_hospitals, validate_scenario
from .online_allocation_engine import DEFAULT_ONLINE_POLICIES, run_online_replay
from .road_routes import RoadRouteError, RoadRouteProvider


SOURCE_KIND = 'road_online_allocation_comparison'
OUTPUT = ROOT / 'simulation/viewer/road_online_allocation_data.json'
BASIC = '가상 응급진료'
SCARCE = '가상 희소치료조건'
ADVANCED = '가상 추가치료'
SCENE = [37.12, 127.07]
AMBULANCE_ORIGIN = [37.09, 127.064]
FAMILIES = ('simultaneous', 'change-enroute', 'transfer-after-arrival')

ASSUMPTIONS = [
    '경기남부의 실제 병원 이름·좌표는 국립중앙의료원 공개 API를 저장한 data/public.db에서 읽는다. 기관에 문의를 보내지 않는다.',
    '환자 발생·진료 역량·병상·전문팀·수용 응답·자원 변화·처치시간은 가상 설정이다. 해당 병원의 실제 진료 가능 여부를 뜻하지 않는다.',
    '선택한 제공자의 일반 자동차 도로 경로와 예상시간을 입력부터 사용해 다섯 정책을 새로 실행했다. 기존 합성 검증 결과에 도로를 덧씌운 자료가 아니다.',
    '도로 입력은 캐시해 모든 분기가 공유한다. 실시간 교통, 구급차 우선통행, 실제 119 출동시간을 반영하지 않는다.',
    '도로와 병원 좌표 사이 접속 구간은 시속 10km 추정이며 점선으로 표시한다. 전체 이동시간은 분 단위로 올림하고 구간 내 위치는 누적거리 비례로 보간한다.',
    '각 가상 환자에게 독립된 구급차가 같은 출발지에서 출동한다고 가정한다. 현장처치 3분은 실험 설정이다.',
    'R0·R1·R2·R3·C1은 문의 3곳·첫 수용 후 추가 응답 대기 2분·변경 알림 켜기를 공유한다. 실제 수용 응답은 해당 시각 자원 상태에 따라 달라진다.',
    '정책은 이미 받은 유효 수용 응답과 현재 위치·자원만 사용한다. 미래 환자·응답·자원 변화·도착 평가 조건은 정책에 제공하지 않는다.',
    'CP-SAT 최적성은 현재 유효 응답이 있는 같은 분의 배정 묶음에만 해당한다. 전체 이송 과정의 최적성이나 임상 효과를 뜻하지 않는다.',
    '목적지 변경·전원은 동일한 합성 처리 규칙으로 재생한다. 실제 현장에서는 병원 수용 확인과 의료진·구급대 판단이 필요하다.',
    '고정된 설명용 3사례이며 성과에 따라 선정한 사례가 아니다. 기존 잠금된 합성 검증군과 별도이고 현행체계 대비 성능 검증 자료가 아니다.',
]


def routing_assumptions(routing):
    assumptions = list(ASSUMPTIONS)
    if getattr(routing.provider, 'provider_name', 'osrm') == 'kakao':
        assumptions.extend([
            '도로·이동시간은 카카오모빌리티 자동차 길찾기 TIME 옵션의 조회 시점 스냅샷이다. 브라우저 재생 중 실시간 교통을 갱신하지 않으며 구급차 우선통행 시간도 아니다.',
            '경로 수집시각은 각 이동구간 fetched_at에 기록한다. 시뮬레이션의 분은 가상 경과시간이며 API 조회시각의 교통을 미래 전 구간에 예측한 것이 아니다.',
        ])
    else:
        assumptions.append('도로·이동시간은 OSRM 일반 자동차 도로 스냅샷이며 실시간 교통 정보가 아니다.')
    return assumptions


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _patient(index, scene, ambulance_origin, dispatch_route, *, special=True,
             scarce=False, priority=4, duration=25):
    arrive = 1 + max(1, math.ceil(dispatch_route['duration_seconds'] / 60))
    return dict(id=f'ROAD-P{index:02d}', label=f'가상 환자 {index}',
                location=list(scene), occur_t=0, dispatch_t=1,
                ambulance_origin=list(ambulance_origin), ambulance_arrive_t=arrive,
                search_start_t=arrive + 3,
                dispatch_timing_source='route_estimate_plus_synthetic_connector',
                required_capabilities=[BASIC] + ([SCARCE] if scarce else []),
                resource_demands={'ed_bed': 1, **({'special_team': 1} if special else {})},
                priority=priority, treatment_minutes=duration,
                clinical_data_source='synthetic')


def build_road_scenarios(routing, db_path=ROOT / 'data/public.db', hospitals=None):
    """Read identity data only; derive road times before constructing scenarios.

    An injected catalog/router supports deterministic offline unit tests. The
    production caller uses an explicitly selected provider with no fallback.
    """
    if routing.provider is None:
        raise ValueError('A road routing provider is required for this dataset.')
    catalog = deepcopy(hospitals if hospitals is not None else load_public_hospitals(db_path, count=3))
    if len(catalog) < 3:
        raise ValueError('At least three public hospital locations are required.')
    catalog = catalog[:3]
    # Use the provider's snapped start so the hypothetical incident is on the road
    # network. This is a chosen scenario location, not an actual patient record.
    first_route = routing.route(SCENE, [catalog[0]['lat'], catalog[0]['lon']])
    scene = list(first_route['geometry'][0])
    dispatch_seed = routing.route(AMBULANCE_ORIGIN, scene)
    ambulance_origin = list(dispatch_seed['geometry'][0])
    dispatch = routing.route(ambulance_origin, scene)
    catalog.sort(key=lambda h: (routing.route(scene, [h['lat'], h['lon']])['duration_seconds'], h['id']))
    for hospital in catalog:
        hospital.update(capabilities=[BASIC], capacity={'ed_bed': 3, 'special_team': 3},
                        identity_source='data/public.db (NMC)', clinical_data_source='synthetic')
    base = dict(seed=20261014, synthetic=True, online_allocation_comparison=True,
                real_road_allocation=True, source_experiment=SOURCE_KIND,
                hospitals=catalog, patients=[], exogenous_events=[],
                max_minutes=120, transfer_preparation_minutes=3,
                timeout_minutes=6, retry_minutes=3, accept_ttl_minutes=10,
                travel_error_range=[1.0, 1.0], nonresponse=0,
                assumptions=routing_assumptions(routing),
                experiment=dict(kind=SOURCE_KIND, purpose='fixed_explanatory_demonstrations',
                    clinical_values='synthetic', identity_source='data/public.db (NMC)',
                    routing=f"{getattr(routing.provider, 'provider_name', 'osrm')}_road_estimate_snapshot",
                    common_inquiry_settings=dict(k=3, wait=2, notify=True),
                    scenario_selection='three_predefined_scenarios_not_selected_by_outcome'))
    simultaneous = deepcopy(base)
    simultaneous.update(id='road-online-simultaneous', label='[실제 도로 · 5개 정책] 동시 발생과 희소 자원',
        description='같은 도로 지점에서 가상 환자 5명이 발생한다. 한 환자만 첫 병원의 가상 희소 역량이 필요하다.')
    for hospital, capacity in zip(simultaneous['hospitals'], (1, 2, 2)):
        hospital['capacity'] = {'ed_bed': capacity, 'special_team': capacity}
    simultaneous['hospitals'][0]['capabilities'].append(SCARCE)
    simultaneous['patients'] = [_patient(i, scene, ambulance_origin, dispatch,
        scarce=i == 5, priority=priority, duration=150)
        for i, priority in enumerate((1, 2, 2, 1, 4), start=1)]
    simultaneous['assumptions'].append('150분 자원 점유는 관찰 중 반환을 막는 경합 스트레스 설정이며 실제 치료시간이 아니다.')

    change = deepcopy(base)
    change.update(id='road-online-change-enroute', label='[실제 도로 · 5개 정책] 이송 중 조건 변경',
        description='수용 후 첫 병원의 가상 전문팀이 사용 불가가 된다. 필요한 환자만 도로상의 현재 위치에서 대체병원을 찾는다.')
    # A distinct, predefined northern scene gives enough road travel for a
    # changed condition to be discovered and a replacement reply to arrive
    # before the first destination. It does not alter the provider's estimate.
    change_scene = list(routing.route([37.18, 127.04],
        [catalog[0]['lat'], catalog[0]['lon']])['geometry'][0])
    change_origin = list(routing.route([37.19, 127.03], change_scene)['geometry'][0])
    change_dispatch = routing.route(change_origin, change_scene)
    # Bind the predefined changed condition to this scene's nearest road-ETA
    # hospital, not the differently located simultaneous scene's ordering.
    change['hospitals'].sort(key=lambda h: (
        routing.route(change_scene, [h['lat'], h['lon']])['duration_seconds'], h['id']))
    change['hospitals'][0]['capacity']['special_team'] = 1
    change['experiment']['changed_hospital_rule'] = 'minimum_road_eta_from_change_scene_before_replay'
    change['patients'] = [_patient(1, change_scene, change_origin, change_dispatch),
                          _patient(2, change_scene, change_origin, change_dispatch, special=False)]
    change_t = change['patients'][0]['search_start_t'] + 2
    change['exogenous_events'] = [dict(t=change_t, type='resource_unavailable',
        hospital=change['hospitals'][0]['id'], resource='special_team', until=change_t + 80)]

    transfer = deepcopy(base)
    transfer.update(id='road-online-transfer-after-arrival', label='[실제 도로 · 5개 정책] 도착 후 추가 치료와 전원',
        description='가상 도착 평가에서 추가 진료가 필요하다고 확인된다. 새 수용 확인 후 실제 도로 경로로 전원한다.')
    transfer['hospitals'][2]['capabilities'].append(ADVANCED)
    transfer['patients'] = [_patient(1, scene, ambulance_origin, dispatch)]
    transfer['patients'][0]['arrival_assessment_adds_capabilities'] = [ADVANCED]
    transfer['assumptions'].append('추가 진료 조건은 도착 평가에서만 공개하며 배정 전에 어떤 정책도 알 수 없다.')
    scenarios = [simultaneous, change, transfer]
    for scenario in scenarios:
        scenario['family'] = scenario['id'].removeprefix('road-online-')
        scenario['response_environment'] = {
            patient['id']: {hospital['id']: dict(delay=1, silent=False)
                            for hospital in scenario['hospitals']}
            for patient in scenario['patients']}
        validate_scenario(scenario)
    return scenarios


def generate_road_online_replay(scenarios, routing, policies=None):
    """Rerun policies using road estimates, rejecting any implicit fallback."""
    if routing.provider is None:
        raise ValueError('A road routing provider is required for this dataset.')
    replays = []
    for scenario in scenarios:
        if not scenario.get('real_road_allocation'):
            raise ValueError('Only explicitly real-road scenarios can be exported here.')
        replay = run_online_replay(deepcopy(scenario), policies=policies, routing=routing)
        replay['routing_summary'] = snapshot_summary(routing, scenario_only=True)
        summary = replay['routing_summary']
        if summary['fallback_routes'] or summary['synthetic_routes']:
            raise RoadRouteError('Real-road replay contains a synthetic fallback; export refused.')
        replay['source_experiment'] = SOURCE_KIND
        replay['scenario']['source_experiment'] = SOURCE_KIND
        for branch in replay['branches']:
            branch['source_experiment'] = SOURCE_KIND
        replay['scenario']['source'] = dict(module='simulation.road_online_replay',
            selection_rule='three_predefined_scenarios_not_selected_by_outcome',
            input_sha256=_digest(scenario), validation_dataset=False)
        replays.append(replay)
    return dict(schema_version=2, source_kind=SOURCE_KIND,
                generated_at=datetime.now(timezone.utc).isoformat(),
                synthetic_clinical_data=True, real_hospital_identity=True,
                real_road_estimates=True, validation_dataset=False,
                policies=deepcopy(policies if policies is not None else list(DEFAULT_ONLINE_POLICIES)),
                routing_summary=snapshot_summary(routing),
                limitations=routing_assumptions(routing), scenarios=replays)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--db', type=Path, default=ROOT / 'data/public.db')
    parser.add_argument('--output', type=Path, default=OUTPUT)
    parser.add_argument('--provider', choices=('auto', 'kakao', 'osrm'), default='auto',
                        help='auto uses Kakao when KAKAO_REST_KEY exists, otherwise OSRM. No failure fallback.')
    parser.add_argument('--route-cache', type=Path, default=None)
    parser.add_argument('--offline', action='store_true', help='Use only available cache entries; Kakao entries must be fresh.')
    args = parser.parse_args(argv)
    provider_name = args.provider
    if provider_name == 'auto':
        provider_name = 'kakao' if os.environ.get('KAKAO_REST_KEY', '').strip() else 'osrm'
    if provider_name == 'kakao':
        provider = KakaoRouteProvider(cache_dir=args.route_cache, offline=args.offline)
    else:
        provider = RoadRouteProvider(cache_dir=args.route_cache, offline=args.offline, allow_synthetic_fallback=False)
    routing = RouteSnapshot(provider)
    try:
        scenarios = build_road_scenarios(routing, db_path=args.db)
        payload = generate_road_online_replay(scenarios, routing)
    except RoadRouteError as error:
        parser.exit(1, f'도로 경로를 확보하지 못해 새 시연 자료를 저장하지 않았습니다: {error}\n')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    staged = args.output.with_suffix('.tmp')
    staged.write_text(json.dumps(payload, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    staged.replace(args.output)
    print(json.dumps(dict(output=str(args.output), scenarios=len(payload['scenarios']),
        branches=sum(len(row['branches']) for row in payload['scenarios']),
        routing=snapshot_summary(routing), requests_completed=getattr(provider, 'requests_completed', None)), ensure_ascii=False))


if __name__ == '__main__':
    main()
