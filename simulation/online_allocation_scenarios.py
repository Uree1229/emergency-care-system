"""Fully synthetic, preregistered cases for online allocation comparisons.

These inputs extend the frozen-offer allocation benchmark through dispatch,
inquiries, movement and (where specified) reassignment/transfer. No hospital
database, real hospital identity or network service is read. Coordinates are
fixed fictional locations around 35.17/126.85; use ``RouteSnapshot()`` for the
synthetic travel model. Treatment durations and priority weights are experiment
parameters, not clinical standards or patient outcomes.

Potential replies belong to the environment. The scenario includes them so the
same keyed environment can be replayed, but an allocation policy must receive
only replies already delivered by the simulation engine.
"""
from __future__ import annotations

from copy import deepcopy

from .sim import rng_for


DEVELOPMENT_SEEDS = tuple(range(10101, 10107))
VALIDATION_SEEDS = tuple(range(11101, 11113))
FAMILIES = (
    'delayed_replies',
    'scarce_greedy_trap',
    'scarce_long_detour',
    'staggered_singleton',
    'large_simultaneous',
    'resource_change_enroute',
    'arrival_assessment_transfer',
    'no_capacity_control',
    'abundant_control',
)
RESOURCE_KEYS = ('ed_bed', 'special_team')
BASIC = '합성 기본진료'
SCARCE = '합성 희소진료'
ADVANCED = '합성 추가진료'
SCENE = (35.17, 126.85)
AMBULANCE_ORIGIN = (35.165, 126.84)

_LABELS = {
    'delayed_replies': ('먼 병원 첫 회신 · 가까운 병원 추가 회신', '먼 병원은 1분, 가까운 병원은 3분 뒤 답한다. 같은 추가 대기 안에서 첫 회신과 도착 우선의 차이를 비교한다.'),
    'scarce_greedy_trap': ('희소 역량 · 순차 선택 함정', '일반 환자는 두 병원에 갈 수 있고 다른 환자는 첫 병원만 이용할 수 있다.'),
    'scarce_long_detour': ('희소 역량 · 장거리 대안의 상충', '희소 병원을 남기려면 일반 환자가 먼 대안으로 가야 한다. 평균 이동시간만으로 평가하지 않는다.'),
    'staggered_singleton': ('시간차 발생 · 공동 배정 이점 없는 대조', '앞 환자의 자원 점유가 끝난 뒤 다음 환자가 발생해 공동 배정의 이점이 제한된다.'),
    'large_simultaneous': ('대규모 동시 환자 · 열거 한도 초과', '환자 14~16명이 동시에 발생한다. 일부는 첫 병원만 가능한 합성 역량을 필요로 한다.'),
    'resource_change_enroute': ('이송 중 전문팀 사용 불가', '두 환자 중 전문팀을 필요로 하는 환자만 영향받는다. 모든 비교 분기에 같은 사건과 알림을 적용한다.'),
    'arrival_assessment_transfer': ('도착 평가 후 추가 진료 · 전원', '추가 진료 필요는 도착 평가에서만 드러난다. 이전에는 어느 정책에도 알려 주지 않는다.'),
    'no_capacity_control': ('전체 자원 없음 · 불가능 대조', '관찰 구간 전체에 구조적으로 필요한 자원이 없다. 어떤 알고리즘도 자원을 만들 수 없다.'),
    'abundant_control': ('충분한 자원 · 같은 이동시간 대조', '모든 후보의 이동시간과 응답시간이 같고 각 병원이 전체 환자를 받을 수 있다.'),
}

_ASSUMPTIONS = [
    '기관 이름·좌표·환자·자원·응답·출동시각은 전부 합성 입력이다. 실제 지역이나 의료기관의 운영 자료가 아니다.',
    '고정 좌표의 직선거리 × 1.3, 시속 40km 추정 이동시간을 분 단위로 올림한다. 도로·교통·구급차 우선 통행은 반영하지 않는다.',
    '실험 우선순위 1·2·4와 치료 점유시간은 합성 설정이다. 임상 중증도, 골든타임, 회복·생존·예후를 뜻하지 않는다.',
    '배정 규칙만 비교할 때 동시 문의 3곳·추가 응답 대기 2분·변경 알림 켜기를 모든 정책에서 동일하게 유지한다.',
    '미래 환자·잠재 회신·자원 변화·도착 평가의 추가 진료 조건은 정책 입력이 아니다. 이미 관측한 상태와 도착한 회신만 판단에 사용한다.',
    '같은 seed·환자·병원·문의 회차에 같은 잠재 응답을 사용한다. 실제 수용 여부는 회신 시점의 자원 상태를 함께 확인한다.',
    '공동 배정과 CP-SAT의 최적성은 같은 시점의 현재 수용 응답과 자원에 대한 목적값에 한정된다. 전체 이송 과정의 전역 최적성은 아니다.',
    '확정 후 목적지 변경과 도착 후 전원은 공통 합성 처리 규칙이다. 현행 체계의 부재 또는 실제 임상 효과를 가정하지 않는다.',
    '관찰 종료까지 배정·최종 도착·치료 시작이 안 된 환자를 별도로 센다. 도착한 환자의 평균만으로 개선을 주장하지 않는다.',
]


def _hospital(index, *, capacity=1, location=None, capabilities=None):
    # Fixed endpoints keep paired comparisons on the same synthetic routes.
    locations = ((35.18, 126.85), (35.205, 126.85), (35.235, 126.85))
    lat, lon = locations[index - 1] if location is None else location
    return dict(id=f'FICT-H{index:02d}', name=f'합성 병원 {index}', lat=lat, lon=lon,
                capabilities=list(capabilities or [BASIC]),
                capacity={'ed_bed': capacity, 'special_team': capacity},
                identity_source='synthetic', clinical_data_source='synthetic')


def _patient(index, *, search_t=6, priority=4, duration=25, special=True, scarce=False):
    demands = {'ed_bed': 1}
    if special:
        demands['special_team'] = 1
    return dict(id=f'P{index:02d}', label=f'합성 환자 {index}', location=list(SCENE),
                occur_t=search_t - 6, dispatch_t=search_t - 5,
                ambulance_origin=list(AMBULANCE_ORIGIN),
                ambulance_arrive_t=search_t - 2, search_start_t=search_t,
                required_capabilities=[BASIC] + ([SCARCE] if scarce else []),
                resource_demands=demands, priority=priority, treatment_minutes=duration,
                clinical_data_source='synthetic', dispatch_timing_source='synthetic_fixed')


def _replies(seed, family, patients, hospitals, first_delay):
    """Precompute independent keyed potential replies, without running a policy."""
    environment = {}
    for patient in patients:
        environment[patient['id']] = {}
        for hospital in hospitals:
            random = rng_for(seed, 'online_allocation_reply', family, patient['id'], hospital['id'])
            environment[patient['id']][hospital['id']] = dict(attempts=[
                dict(delay=first_delay[hospital['id']] if isinstance(first_delay, dict) else first_delay,
                     silent=False),
                dict(delay=random.randint(1, 3), silent=False),
                dict(delay=random.randint(1, 3), silent=False),
            ])
    return environment


def _build_family(seed, family):
    random = rng_for(seed, 'online_allocation_scenario', family)
    delay = random.randint(1, 2)
    hospitals = [_hospital(i) for i in range(1, 4)]
    patients = [_patient(i, duration=random.randint(18, 28)) for i in range(1, 5)]
    events = []
    maximum = 90
    extra = {}

    if family == 'delayed_replies':
        patients = patients[:3]
        hospitals = [_hospital(i, capacity=len(patients)) for i in range(1, 4)]
        for patient in patients:
            patient['priority'] = random.choice((1, 2, 4))
        delay = {'FICT-H01': 3, 'FICT-H02': 2, 'FICT-H03': 1}
        extra.update(first_response_hospital_id='FICT-H03',
                     nearest_hospital_id='FICT-H01',
                     first_attempt_delays_minutes=dict(delay))

    elif family in ('scarce_greedy_trap', 'scarce_long_detour'):
        hospitals[0]['capabilities'].append(SCARCE)
        hospitals[2]['capacity'] = {'ed_bed': 0, 'special_team': 0}
        patients = [_patient(1, duration=120), _patient(2, duration=120, scarce=True)]
        extra.update(scarce_patient_id='P02', scarce_hospital_id='FICT-H01',
                     flexible_patient_id='P01', alternative_hospital_id='FICT-H02')
        if family == 'scarce_long_detour':
            hospitals[1].update(lat=35.40, lon=126.85)
            extra['tradeoff'] = 'scarce patient access versus flexible patient travel'

    elif family == 'staggered_singleton':
        patients = [_patient(i + 1, search_t=6 + 23 * i, duration=8,
                             priority=random.choice((1, 2, 4))) for i in range(4)]
        maximum = 100
        extra['expected_joint_batch_size'] = 1

    elif family == 'large_simultaneous':
        count = random.randint(14, 16)
        hospitals = [_hospital(i, capacity=5) for i in range(1, 4)]
        hospitals[0]['capabilities'].append(SCARCE)
        patients = [_patient(i, duration=120, scarce=i > count - 4)
                    for i in range(1, count + 1)]
        extra.update(first_batch_patient_count=count, scarce_patient_count=4,
                     theoretical_combinations=(4 ** (count - 4)) * (2 ** 4))

    elif family == 'resource_change_enroute':
        hospitals = [_hospital(1, capacity=3, location=(35.215, 126.85)),
                     _hospital(2, capacity=3, location=(35.25, 126.85)),
                     _hospital(3, capacity=3, location=(35.285, 126.85))]
        # The replay event removes exactly one unit. Keep one team so that this
        # event really invalidates the affected patient's current destination.
        hospitals[0]['capacity']['special_team'] = 1
        patients = [_patient(1, duration=20), _patient(2, duration=20, special=False)]
        delay = 1
        events = [dict(t=9, type='resource_unavailable', hospital='FICT-H01',
                       resource='special_team', until=80)]
        extra.update(affected_patient_id='P01', unaffected_patient_id='P02')

    elif family == 'arrival_assessment_transfer':
        patients = [_patient(1, duration=20)]
        hospitals[2]['capabilities'].append(ADVANCED)
        patients[0]['arrival_assessment_adds_capabilities'] = [ADVANCED]
        extra['additional_need_observed_at'] = 'arrival_assessment_only'

    elif family == 'no_capacity_control':
        for hospital in hospitals:
            hospital['capacity'] = {'ed_bed': 0, 'special_team': 0}
        patients = [_patient(i) for i in range(1, 4)]
        maximum = 60

    elif family == 'abundant_control':
        patients = [_patient(i, duration=random.randint(18, 28),
                             priority=random.choice((1, 2, 4))) for i in range(1, 5)]
        hospitals = [_hospital(i, capacity=len(patients), location=(35.19, 126.85))
                     for i in range(1, 4)]
        extra['co_located_hospitals_for_equal_travel_control'] = True

    label, description = _LABELS[family]
    value = dict(id=f'online-{family}-{seed}', label=f'{label} · seed {seed}',
                 description=description, seed=seed, family=family,
                 synthetic=True, online_allocation_comparison=True,
                 hospitals=hospitals, patients=patients, exogenous_events=events,
                 response_environment=_replies(seed, family, patients, hospitals, delay),
                 max_minutes=maximum, transfer_preparation_minutes=3,
                 timeout_minutes=6, retry_minutes=3, accept_ttl_minutes=10,
                 nonresponse=0, assumptions=list(_ASSUMPTIONS),
                 experiment=dict(family=family, seed=seed, patient_count=len(patients),
                     comparison_scope='same_online_inquiries_different_allocation_rules',
                     clinical_values='synthetic', identity_source='synthetic',
                     routing='synthetic_fixed_coordinates',
                     common_inquiry_settings=dict(k=3, wait=2, notify=True),
                     treatment_duration_source='simulation_parameter_not_clinical',
                     **extra))
    if family in ('scarce_greedy_trap', 'scarce_long_detour', 'large_simultaneous'):
        value['assumptions'].append(
            '120분 자원 점유는 관찰 중 자원 반환을 막는 스트레스 설정이다. 실제 치료시간을 뜻하지 않는다.')
    return value


def build_online_scenarios(seed, families=None):
    """Return independent deterministic map-replay inputs in requested order.

    Development seeds 10101–10106 and validation seeds 11101–11112 are reserved
    for separate experiment phases. Structural tests should use tiny seeds.
    Selecting a subset never changes that family's random stream or input.
    """
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise ValueError('seed must be a nonnegative integer')
    if families is None:
        selected = list(FAMILIES)
    else:
        if isinstance(families, (str, bytes)):
            raise ValueError('families must be an iterable of family names')
        try:
            selected = list(families)
        except TypeError as exc:
            raise ValueError('families must be an iterable of family names') from exc
        if any(not isinstance(family, str) or family not in FAMILIES for family in selected):
            raise ValueError('unknown online allocation family')
        if len(selected) != len(set(selected)):
            raise ValueError('duplicate online allocation family')
    # Local import keeps the scenario module usable while replay subclasses are
    # being imported; validation itself performs no file or network access.
    from .map_replay import validate_scenario
    values = [_build_family(seed, family) for family in selected]
    for value in values:
        validate_scenario(value)
    return deepcopy(values)
