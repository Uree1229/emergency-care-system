"""문의 처리 대기열과 수용 조건 변경을 분리하는 합성 실험 입력.

기관 식별자와 좌표만 기준 입력에서 복사한다. 환자, 역량, 자원, 문의 서비스
시간, 고정 외생 업무와 자원 변경 사건은 실제 기관 운영자료가 아니다.
개발 입력과 별도 검증 입력을 미리 구분하며 어느 정책의 승리도 보장하지 않는다.
"""
from __future__ import annotations

import copy


_BASIC = '가상 기본진료'
_IMAGING = '가상 영상치료조건'
_TEAM = '가상 전문팀치료조건'
_SCARCE = '가상 희소치료조건'

_ASSUMPTIONS = [
    '기관 식별자·이름·좌표 외 환자, 치료조건, 자원, 업무량, 응답시간은 합성 실험값이다.',
    '우선순위 1·2·4는 실험 가중치이며 임상 중증도나 임상 허용시간을 뜻하지 않는다.',
    '각 환자에게 별도 구급차가 출동한다. 모든 환자의 현장·구급차 출발지는 기준 입력과 같다.',
    '문의 처리 대기열의 서비스 시간은 합성 업무 점유시간이다. 실제 병원 응답 분포 추정값이 아니다.',
    '고정 외생 업무와 자원 변경 사건은 모든 정책에서 같은 시각에 발생하며 선택된 병원에 맞춰 바꾸지 않는다.',
    '정책에는 문의 전에 잠재 응답이나 아직 발생하지 않은 외생 사건을 제공하지 않는다.',
    '수용 조건 변경 실험은 문의 처리 대기열을 끈다. 문의량 효과와 변경 대응 효과를 별도로 비교한다.',
    '검증 입력은 환자 수·발생 간격·서비스 시간·초기 용량이 다르다. 실제 현장 외적 타당성을 보장하지 않는다.',
]


def _new_base(base_scenario: dict, split: str, family: str, count: int) -> dict:
    if split not in ('development', 'validation'):
        raise ValueError("split은 'development' 또는 'validation'이어야 합니다.")
    if not base_scenario.get('patients'):
        raise ValueError('기준 시나리오에 환자 1명 이상이 필요합니다.')
    if len(base_scenario.get('hospitals', [])) < 4:
        raise ValueError('문의 부하 시나리오에는 병원 4곳 이상이 필요합니다.')

    value = copy.deepcopy(base_scenario)
    for key in ('id', 'label', 'description', 'policies', 'experiment_environment'):
        value.pop(key, None)
    value.update(synthetic=True, family=family, split=split,
                 max_minutes=180 if split == 'development' else 210,
                 timeout_minutes=15 if split == 'development' else 18,
                 retry_minutes=4, accept_ttl_minutes=12,
                 nonresponse=0, exogenous_events=[])
    # The inherited assumptions describe the older fixed R0/R1 comparison; they
    # are not the specification of this new queue or change-response experiment.
    value['assumptions'] = list(_ASSUMPTIONS)
    value['experiment'] = dict(
        family=family, split=split, patient_count=count, clinical_values='synthetic',
        allocation_comparison='experiment_runner_defines_policies',
        future_information_available_to_policy=False,
        scene_geometry='same_scene_and_ambulance_origin_as_base',
    )

    template = copy.deepcopy(base_scenario['patients'][0])
    template.pop('arrival_assessment_adds_capabilities', None)
    # Independent field dictionaries avoid accidental mutation between patients.
    value['patients'] = []
    for index in range(count):
        offset = index // 2 if split == 'development' else (index * 2) // 3
        patient = copy.deepcopy(template)
        patient.update(
            id=f'P{index + 1:02d}', label=f'가상 환자 {index + 1}',
            priority=(1, 2, 4)[index % 3],
            required_capabilities=[_BASIC], resource_demands={'ed_bed': 1},
            treatment_minutes=(25, 35, 45)[index % 3],
        )
        for field in ('occur_t', 'dispatch_t', 'ambulance_arrive_t', 'search_start_t'):
            default = template.get('occur_t', 0) if field in ('occur_t', 'dispatch_t') else 0
            patient[field] = template.get(field, default) + offset
        if index % 4 == 0:
            patient['required_capabilities'].append(_TEAM)
            patient['resource_demands']['special_team'] = 1
        elif index % 4 == 2:
            patient['required_capabilities'].append(_IMAGING)
            patient['resource_demands']['imaging_unit'] = 1
        value['patients'].append(patient)

    for index, hospital in enumerate(value['hospitals']):
        capacity = 4 if split == 'development' else 3
        hospital['capacity'] = dict(ed_bed=capacity, special_team=3, imaging_unit=2)
        hospital['capabilities'] = [_BASIC]
        if index % 2 == 0 or index == 1:
            hospital['capabilities'].append(_TEAM)
        if index % 2 == 0 or index == 3:
            hospital['capabilities'].append(_IMAGING)
        hospital['clinical_data_source'] = 'synthetic'
        hospital.pop('initial_occupancy', None)

    value['response_environment'] = {
        p['id']: {
            h['id']: dict(delay=1 + (i + j) % 3 if split == 'development'
                          else 2 + (2 * i + j) % 3, silent=False)
            for j, h in enumerate(value['hospitals'])
        }
        for i, p in enumerate(value['patients'])
    }
    value['inquiry_queue'] = dict(enabled=family == 'inquiry_workload', workers=1,
                                  background_jobs=[])
    return value


def _background_jobs(value: dict) -> list[dict]:
    """Policy-independent work at every hospital, including unqueried hospitals."""
    start = min(p['search_start_t'] for p in value['patients'])
    validation_extra = 1 if value['split'] == 'validation' else 0
    return [
        dict(t=max(0, start - 1 + wave * 4), hospital=h['id'],
             service_minutes=2 + (index + wave) % 3 + validation_extra)
        for wave in range(2)
        for index, h in enumerate(value['hospitals'])
    ]


def _workload_scenarios(base_scenario: dict, split: str) -> list[dict]:
    count = 8 if split == 'development' else 10
    common = _new_base(base_scenario, split, 'inquiry_workload', count)
    common['inquiry_queue']['background_jobs'] = _background_jobs(common)
    common['experiment'].update(
        response_delay_interpretation='queue_service_time_base',
        inquiry_background='two_fixed_jobs_per_hospital_same_across_policies',
        controls='work_sufficient_w1_vs_w2_differ_only_in_worker_count',
    )

    one_worker = copy.deepcopy(common)
    one_worker.update(
        id=f'work_sufficient_w1_{split}', label=f'문의 부하 · 충분한 자원 · 담당 1명 · {split}',
        description='치료 자원은 충분하다. 비슷한 현장의 문의가 가까운 병원에 몰릴 때 처리 대기열을 비교한다.',
    )
    one_worker['experiment']['purpose'] = '자원 부족을 배제하고 문의 처리 부하와 시간·문의량의 상충을 관찰한다.'

    two_workers = copy.deepcopy(one_worker)
    two_workers.update(id=f'work_sufficient_w2_{split}',
                       label=f'문의 부하 · 충분한 자원 · 담당 2명 · {split}')
    two_workers['inquiry_queue']['workers'] = 2

    scarce = copy.deepcopy(common)
    scarce.update(
        id=f'work_scarce_w1_{split}', label=f'문의 부하 · 희소 조건 · 담당 1명 · {split}',
        description='환자 한 명에게 첫 병원만 가진 합성 치료조건이 필요하다. 일반 문의도 같은 병원의 처리 자원을 쓴다.',
    )
    scarce['hospitals'][0]['capabilities'].append(_SCARCE)
    patient = scarce['patients'][-1]
    patient['required_capabilities'] = [_BASIC, _SCARCE]
    patient['resource_demands'] = {'ed_bed': 1, 'special_team': 1}
    patient['priority'] = 4
    patient['label'] += ' · 희소 조건'
    scarce['experiment'].update(purpose='문의 분산이 희소 조건 환자의 확인 지연에 미치는 영향도 측정한다.',
                                scarce_patient_id=patient['id'],
                                scarce_hospital_id=scarce['hospitals'][0]['id'])

    shortage = copy.deepcopy(common)
    shortage.update(
        id=f'work_shortage_w1_{split}', label=f'문의 부하 · 총자원 부족 대조 · {split}',
        description='관찰 시간에 비해 치료 점유가 길고 총 병상은 3개뿐이다. 문의 최적화도 자원을 만들 수 없다.',
    )
    for index, hospital in enumerate(shortage['hospitals']):
        hospital['capacity']['ed_bed'] = 1 if index < 3 else 0
    for patient in shortage['patients']:
        patient['treatment_minutes'] = shortage['max_minutes'] + 30
    shortage['experiment'].update(purpose='불완전한 평균시간 개선과 실제 수용 범위 개선을 구분하는 부족 대조군.',
                                  total_structural_slots=3)
    shortage['assumptions'].append('총자원 부족 대조군의 긴 점유시간은 관찰 중 반환을 막는 합성 스트레스 값이다.')
    return [one_worker, two_workers, scarce, shortage]


def _change_scenarios(base_scenario: dict, split: str) -> list[dict]:
    count = 6 if split == 'development' else 9
    common = _new_base(base_scenario, split, 'condition_change', count)
    # Use a more spread-out stream than the workload family, while preserving
    # every patient's dispatch/search phase offsets from the original input.
    template = base_scenario['patients'][0]
    spacing = 2 if split == 'development' else 3
    for index, patient in enumerate(common['patients']):
        for field in ('occur_t', 'dispatch_t', 'ambulance_arrive_t', 'search_start_t'):
            default = template.get('occur_t', 0) if field in ('occur_t', 'dispatch_t') else 0
            patient[field] = template.get(field, default) + spacing * index
    first_hospital = common['hospitals'][0]
    first_hospital['capacity'].update(ed_bed=3 if split == 'development' else 2, special_team=1)
    # Responses are independent of the event and of the selected destination.
    # They make the nearest hospital relevant without giving the policy future
    # outage times or an artificial guarantee that any patient is en route then.
    for entries in common['response_environment'].values():
        for index, hospital in enumerate(common['hospitals']):
            entries[hospital['id']]['delay'] = min(3, index + 1) + (split == 'validation')
    change_t = template['search_start_t'] + (2 if split == 'development' else 3)
    common['assumptions'].append(
        '조건 변경 시각은 기본 합성 이동시간·응답시간에서 이송 중 변화 기능을 시험하도록 미리 정한 값이다. '
        '정책의 배정·도착 결과나 관측 성과를 보고 시각·병원을 변경하지 않는다.')
    common['experiment'].update(
        response_delay_interpretation='response_delay_without_queue',
        event_time_source='fixed_offset_from_first_search_not_policy_commit_or_route',
        event_t=change_t, event_hospital=first_hospital['id'],
        controls='same_patients_resources_and_responses_only_exogenous_events_differ',
    )

    none = copy.deepcopy(common)
    none.update(id=f'change_none_{split}', label=f'조건 변경 · 외생 변화 없음 · {split}',
                description='조건 변화 실험의 대조 입력. 문의 처리 대기열 없이 배정 후 관리 비용을 비교한다.')

    short = copy.deepcopy(common)
    short.update(id=f'change_transient_{split}', label=f'조건 변경 · 짧은 일시 중단 · {split}',
                 description='첫 병원 전문팀이 정해진 시각에 잠시 중단된다. 기다림과 불필요한 목적지 변경을 함께 평가한다.')
    short['exogenous_events'] = [dict(t=change_t, type='resource_unavailable',
        hospital=first_hospital['id'], resource='special_team',
        until=change_t + (6 if split == 'development' else 9))]

    persistent = copy.deepcopy(common)
    persistent.update(id=f'change_persistent_{split}', label=f'조건 변경 · 지속 중단 · {split}',
                      description='같은 전문팀 중단이 오래 지속된다. 상태 변경의 영향 판단과 대체 수용 확인 지연을 비교한다.')
    persistent['exogenous_events'] = [dict(t=change_t, type='resource_unavailable',
        hospital=first_hospital['id'], resource='special_team',
        until=change_t + (55 if split == 'development' else 70))]

    external = copy.deepcopy(common)
    external.update(id=f'change_external_{split}', label=f'조건 변경 · 외부 환자 도착 · {split}',
                    description='시스템 밖 환자 한 명이 첫 병원의 전문팀 자원을 점유한다. 기존 이송에 대한 영향은 실행 결과로 판단한다.')
    external['exogenous_events'] = [dict(t=change_t, type='external_arrival',
        hospital=first_hospital['id'], demand={'ed_bed': 1, 'special_team': 1},
        duration=40 if split == 'development' else 50)]
    return [none, short, persistent, external]


def build_workload_scenarios(base_scenario: dict, split: str = 'development') -> list[dict]:
    """문의 부하 4개 + 조건 변화 4개 입력을 새 객체로 반환한다.

    기준 병원 순서는 현장으로부터의 예상 이동시간 순서로 간주한다. 새 위치나
    경로 조회는 만들지 않는다. 개발과 검증은 입력 생성 시 분리하며 정책이나
    관측 성과를 보고 사건 시각·대상 병원을 변경하지 않는다.
    """
    scenarios = _workload_scenarios(base_scenario, split) + _change_scenarios(base_scenario, split)
    for scenario in scenarios:
        scenario['case_kind'] = scenario['id'].removesuffix(f'_{split}')
        scenario['experiment']['case_kind'] = scenario['case_kind']
    return scenarios
