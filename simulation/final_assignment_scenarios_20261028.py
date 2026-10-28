"""New synthetic distribution for a preregistered R0/R1/H1 comparison.

The generator never runs an allocation policy or selects inputs by outcomes.
Acceptance is endogenous to the existing replay engine's capabilities and
currently available resources. Its response hook supports delay/silence only;
there is deliberately no unused Bernoulli ``accept`` field.

The main track is an explicitly specified synthetic distribution, not an
estimate of the frequency of situations in real emergency services. The old
hand-crafted nine families are available only through the separate stress API.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import math

from .sim import rng_for

VERSION = 'final_assignment_scenarios_20261028_v1'
STRATA = ('routine_single', 'routine_stream', 'surge')
SEED_NAMESPACES = {
    'development': 'final_assignment_20261028_dev',
    'validation': 'final_assignment_20261028_validation',
    'unit': 'final_assignment_20261028_unit',
}
BASIC = '합성 기본진료'
SPECIALTIES = ('합성 전문 A', '합성 전문 B', '합성 전문 C')
CENTER = (35.17, 126.85)
COMMON_INQUIRY_SETTINGS = dict(k=3, wait=2, notify=True)
DISTRIBUTION = {
    'version': VERSION,
    'track': 'main',
    'strata': {
        'routine_single': dict(patient_count=[1, 1], arrival='one occurrence'),
        'routine_stream': dict(patient_count=[3, 8], arrival='sequential independent gaps 2..8 minutes'),
        'surge': dict(patient_count=[12, 20], arrival='equal mixture of simultaneous or 2..4 waves with gaps 1..4 minutes'),
    },
    'hospital_count_uniform_inclusive': [5, 10],
    'hospital_radial_offset_degrees_uniform': [0.015, 0.11],
    'hospital_angle_uniform_radians': [-math.pi, math.pi],
    'hospital_ed_bed_uniform_inclusive': [1, 6],
    'hospital_special_team_uniform_inclusive': [0, 3],
    'hospital_specialty_independent_probability': 0.6,
    'patient_specialty_required_probability': 0.6,
    'patient_special_team_required_probability': 0.3,
    'patient_priority_values': [4, 2, 1],
    'patient_priority_probabilities': [0.25, 0.45, 0.30],
    'patient_location_offset_degrees_uniform': [-0.012, 0.012],
    'scenario_initial_resource_occupancy_probability_uniform': [0.2, 0.7],
    'initial_occupancy_release_minutes_uniform_inclusive': [5, 90],
    'patient_resource_holding_minutes_uniform_inclusive': [15, 60],
    'response_silence_probability': 0.2,
    'response_delay_minutes_uniform_inclusive': [1, 5],
    'precomputed_attempts_per_pair': 64,
    'response_attempts_after_last': 'repeat last precomputed attempt, as in existing replay contract',
    'acceptance_rule': 'existing Hospital.can_accept(capabilities,current free resources); no probabilistic accept override',
    'inquiry_queue_enabled': False,
    'main_capacity_outages_or_external_arrivals': False,
    'observation_minutes': 180,
    'simulation_time_resolution': 'integer minute',
    'common_inquiry_settings': deepcopy(COMMON_INQUIRY_SETTINGS),
    'clinical_and_region_representativeness_claimed': False,
}
ASSUMPTIONS = (
    '모든 입력은 합성이며 실제 병원·환자·지역의 발생 빈도나 임상 예후를 재현하지 않는다.',
    '배정 규칙을 실행하거나 성과를 확인해 입력을 선택하지 않는다. seed와 stratum만 입력 생성에 사용한다.',
    '모든 정책에 동일한 환자·기관·자원·초기 점유·잠재 회신을 주되 배정 이후 실제 자원과 회신은 달라질 수 있다.',
    '회신은 환자·기관·시도별 고정된 미응답 여부와 1~5분 처리 지연을 사용한다. 수용 여부는 기존 엔진의 현재 역량·잔여 자원 검사로 결정한다.',
    '자원 구조는 주 평가 동안 고정한다. 초기 점유와 치료 점유의 해제는 기존 물리 엔진을 따른다. 외생 중단과 유입은 주 평가에 넣지 않는다.',
    '주 평가의 검토 대기열은 끈다. 문의 정책은 모든 분기에서 고정3곳·첫 수용 후 최대2분 추가 대기·변경 알림 켜기로 같다.',
    '이동은 합성 좌표의 기존 직선거리 기반 추정이며 외부 도로·지도·교통 API를 호출하지 않는다.',
    '미래 환자·잠재 회신·초기 점유의 미래 해제시각은 환경 데이터이며 배정 정책의 공개 입력이 아니다.',
    '기존에 의도적으로 만든 희소 후보 함정과 장거리 우회 사례는 별도 stress track으로만 보고하고 main 평균에 합산하지 않는다.',
)


def _seed(value):
    if type(value) is not int or value < 0:
        raise ValueError('seed must be a nonnegative exact integer')
    return value


def seed_for(split, stratum, index):
    """Derive disjoint, large seeds without exposing or inspecting outcomes."""
    if type(split) is not str or split not in SEED_NAMESPACES:
        raise ValueError('unknown split')
    if type(stratum) is not str or stratum not in STRATA + ('stress',):
        raise ValueError('unknown stratum')
    if type(index) is not int or index < 0:
        raise ValueError('index must be a nonnegative exact integer')
    message = f'{SEED_NAMESPACES[split]}|{stratum}|{index}'.encode('utf-8')
    return (1 << 256) + int.from_bytes(hashlib.sha256(message).digest(), 'big')


def _arrival_offsets(random, stratum, count):
    if stratum == 'routine_single':
        return [0], dict(mode='single')
    if stratum == 'routine_stream':
        offsets = [0]
        for _ in range(count - 1):
            offsets.append(offsets[-1] + random.randint(2, 8))
        return offsets, dict(mode='stream', gap_range=[2, 8])
    if random.random() < 0.5:
        return [0] * count, dict(mode='simultaneous')
    waves = random.randint(2, 4)
    times = [0]
    for _ in range(waves - 1):
        times.append(times[-1] + random.randint(1, 4))
    # Allocate patients to waves without privileging priority, candidate width,
    # hospital ID or a policy's decision order. Every selected wave is nonempty.
    assignments = list(range(waves)) + [random.randrange(waves) for _ in range(count - waves)]
    random.shuffle(assignments)
    return [times[index] for index in assignments], dict(mode='waves', wave_times=times)


def _hospitals(random, count, occupancy):
    values = []
    for index in range(count):
        angle = random.uniform(-math.pi, math.pi)
        radius = random.uniform(0.015, 0.11)
        caps = [BASIC] + [cap for cap in SPECIALTIES if random.random() < 0.6]
        capacity = dict(ed_bed=random.randint(1, 6), special_team=random.randint(0, 3))
        active = []
        for resource, units in capacity.items():
            for _ in range(units):
                if random.random() < occupancy:
                    active.append(dict(until=random.randint(5, 90), demand={resource: 1}))
        values.append(dict(id=f'SYN-H{index + 1:02d}', name=f'합성 병원 {index + 1}',
            lat=CENTER[0] + radius * math.sin(angle),
            lon=CENTER[1] + radius * math.cos(angle), capabilities=caps,
            capacity=capacity, initial_occupancy=active,
            identity_source='synthetic', clinical_data_source='synthetic'))
    return values


def _patients(random, stratum, count):
    offsets, arrival = _arrival_offsets(random, stratum, count)
    values = []
    for index, occurrence in enumerate(offsets):
        caps = [BASIC]
        if random.random() < 0.6:
            caps.append(random.choice(SPECIALTIES))
        demand = dict(ed_bed=1)
        if random.random() < 0.3:
            demand['special_team'] = 1
        location = [CENTER[0] + random.uniform(-0.012, 0.012), CENTER[1] + random.uniform(-0.012, 0.012)]
        origin = [location[0] + random.uniform(-0.008, 0.008), location[1] + random.uniform(-0.008, 0.008)]
        values.append(dict(id=f'P{index + 1:02d}', label=f'합성 환자 {index + 1}',
            location=location, ambulance_origin=origin, occur_t=occurrence,
            dispatch_t=occurrence + 1, ambulance_arrive_t=occurrence + 4,
            search_start_t=occurrence + 6, required_capabilities=caps,
            resource_demands=demand, priority=random.choices((4, 2, 1), weights=(.25, .45, .30))[0],
            treatment_minutes=random.randint(15, 60),
            clinical_data_source='synthetic', dispatch_timing_source='synthetic_fixed'))
    return values, arrival


def _responses(seed, stratum, patients, hospitals):
    environment = {}
    for patient in patients:
        environment[patient['id']] = {}
        for hospital in hospitals:
            random = rng_for(seed, VERSION, 'potential_response', stratum, patient['id'], hospital['id'])
            environment[patient['id']][hospital['id']] = dict(attempts=[
                dict(delay=random.randint(1, 5), silent=random.random() < 0.2)
                for _ in range(DISTRIBUTION['precomputed_attempts_per_pair'])])
    return environment


def build_case(stratum, seed):
    """Generate one validated main-track input; never execute an allocation."""
    if type(stratum) is not str or stratum not in STRATA:
        raise ValueError('unknown main stratum')
    _seed(seed)
    random = rng_for(seed, VERSION, 'main', stratum)
    count_range = DISTRIBUTION['strata'][stratum]['patient_count']
    count = random.randint(*count_range)
    hospital_count = random.randint(5, 10)
    occupancy = random.uniform(0.2, 0.7)
    hospitals = _hospitals(random, hospital_count, occupancy)
    patients, arrival = _patients(random, stratum, count)
    value = dict(id=f'final-assignment-main-{stratum}-{seed}',
        label=f'주 평가 · {stratum} · seed {seed}',
        description='명시된 합성 분포의 정책 독립 입력; 성과별 사례 선택 없음',
        seed=seed, family=stratum, stratum=stratum, evaluation_track='main', synthetic=True,
        online_allocation_comparison=True, hospitals=hospitals, patients=patients,
        exogenous_events=[], response_environment=_responses(seed, stratum, patients, hospitals),
        inquiry_queue=dict(enabled=False), max_minutes=180, timeout_minutes=6,
        retry_minutes=3, accept_ttl_minutes=10, transfer_preparation_minutes=3,
        nonresponse=0.2, assumptions=list(ASSUMPTIONS),
        experiment=dict(generator_version=VERSION, evaluation_track='main', stratum=stratum,
            clinical_values='synthetic', identity_source='synthetic',
            common_inquiry_settings=deepcopy(COMMON_INQUIRY_SETTINGS),
            scenario_initial_occupancy_probability=occupancy, arrival_generation=arrival,
            distribution=deepcopy(DISTRIBUTION), outcome_selection_used=False))
    from .map_replay import validate_scenario
    validate_scenario(value)
    return value


def build_stress_cases(seed):
    """Keep all old structural stress families, separate from main aggregates."""
    _seed(seed)
    from .online_allocation_scenarios import build_online_scenarios
    values = build_online_scenarios(seed)
    for value in values:
        value['id'] = 'final-assignment-stress-' + value['id']
        value['label'] = '별도 스트레스 · ' + value['label']
        value['evaluation_track'] = 'stress'
        value['experiment'].update(evaluation_track='stress', main_aggregate_allowed=False,
            inherited_generator='simulation.online_allocation_scenarios.build_online_scenarios',
            inherited_generator_behavior_changed=False, outcome_selection_used=False)
    return values
