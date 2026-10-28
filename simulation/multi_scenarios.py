"""도로 입력을 재사용하는 다수 환자 비교 시나리오.

실제 기관 이름과 좌표만 ``base_scenario``에서 이어받는다. 아래의 진료 역량,
자원 용량, 우선순위, 문의 응답과 치료 점유시간은 전부 합성 실험값이다.
특정 정책이 이기는 결과를 보장하지 않으며, 자원 부족 반례도 함께 제공한다.
"""
from __future__ import annotations

import copy


COMPARISON_POLICIES = (
    dict(id='multi_first', label='R0 · 첫 수용 순서', rule='R0', k=3, wait=2, notify=True),
    dict(id='multi_arrival', label='R1 · 예상 도착 우선', rule='R1', k=3, wait=2, notify=True),
    dict(id='multi_competition', label='R2 · 자원 경쟁 고려', rule='R2', k=3, wait=2, notify=True),
    dict(id='multi_joint', label='R3 · 다수 환자 동시 계산', rule='R3', k=3, wait=2, notify=True),
)

_BASIC = '가상 응급진료'
_SPECIAL = '가상 희소치료조건'

_ASSUMPTIONS = [
    '같은 현장에서 환자 5명이 발생하는 합성 사례다. 각 환자에게 별도 구급차가 출동한다고 가정한다.',
    '기관 이름·좌표와 도로 경로만 공개 자료이며, 아래 역량·자원·응답은 실제 기관의 운영 현황이 아니다.',
    '우선순위 1·2·4는 실험 가중치다. 임상 중증도 분류나 임상 허용시간을 뜻하지 않는다.',
    'R0~R3의 동시 문의 수 3곳·추가 응답 대기 2분·변경 알림 설정을 같게 유지한다.',
    '병원 응답 환경은 환자·병원별로 고정한다. 문의하기 전 미래 응답은 정책에 전달하지 않는다.',
    'R3는 같은 분에 준비된 소규모 묶음을 열거한다. 전체 이송과정이나 미래까지의 전역 최적해가 아니다.',
    '자원 사용 불가 사건·도착 후 추가 치료 요구는 이 묶음에 넣지 않아 배정 규칙의 비교 범위를 제한한다.',
]


def _new_base(base_scenario):
    if not base_scenario.get('patients'):
        raise ValueError('기준 시나리오에 환자 1명 이상이 필요합니다.')
    if len(base_scenario.get('hospitals', [])) < 3:
        raise ValueError('다수 환자 시나리오에는 병원 3곳 이상이 필요합니다.')
    value = copy.deepcopy(base_scenario)
    value.pop('id', None)
    value.pop('label', None)
    value.pop('description', None)
    value['synthetic'] = True
    value['max_minutes'] = 120
    value['exogenous_events'] = []
    value['policies'] = copy.deepcopy(list(COMPARISON_POLICIES))
    value['nonresponse'] = 0
    value['assumptions'] = [line for line in value.get('assumptions', [])
                            if not line.startswith('병렬 R0/R1의 문의 수와 대기는 같다.')]
    value['assumptions'].extend(_ASSUMPTIONS)
    value['experiment'] = dict(
        family='multi_patient_assignment',
        patient_count=5,
        comparison_scope='same_inquiry_settings_different_assignment_rules',
        clinical_values='synthetic',
        ambulance_fleet='five_independent_ambulances_same_origin',
        purpose='미배정·도착시간·문의 수와 우선순위별 차이를 함께 비교한다.',
    )
    for h in value['hospitals']:
        h['capacity'] = {'ed_bed': 1, 'special_team': 1}
        h['capabilities'] = [_BASIC]
        h['clinical_data_source'] = 'synthetic'
        h.pop('initial_occupancy', None)
    patient = copy.deepcopy(value['patients'][0])
    patient.pop('arrival_assessment_adds_capabilities', None)
    value['patients'] = []
    for index, priority in enumerate((1, 2, 2, 1, 4), start=1):
        p = copy.deepcopy(patient)
        p.update(id=f'P{index:02d}', label=f'가상 환자 {index}', priority=priority,
                 required_capabilities=[_BASIC], resource_demands={'ed_bed': 1, 'special_team': 1},
                 treatment_minutes=25)
        value['patients'].append(p)
    _set_responses(value)
    return value


def _set_responses(value, delay=2):
    # No wildcard entry: every patient gets an explicit copy of the same potential environment.
    value['response_environment'] = {
        p['id']: {h['id']: dict(delay=delay, silent=False) for h in value['hospitals']}
        for p in value['patients']
    }


def build_multi_scenarios(base_scenario: dict) -> list[dict]:
    """기준 도로·출동 입력을 바꾸지 않고 4개 고정 시나리오를 반환한다.

    기준 병원 순서는 현재 위치에서의 이동시간 순서로 간주한다. 생성기에서
    ``build_default_scenarios(..., routing=...)[0]``을 전달하면 도로 기준 정렬을
    유지한다. 추가 환자의 위치/구급차 출발지를 복제해 새 경로 조회가 필요 없다.
    """
    common = _new_base(base_scenario)

    simultaneous = copy.deepcopy(common)
    simultaneous.update(
        id='multi_simultaneous', label='다수 환자 · 동시 자원 경쟁',
        description='같은 시각에 환자 5명이 같은 후보에 문의한다. 첫 3곳의 합성 자원으로 모두 수용 가능하다.',
    )
    for h, capacity in zip(simultaneous['hospitals'][:3], (1, 2, 2)):
        h['capacity'] = {'ed_bed': capacity, 'special_team': capacity}
    simultaneous['experiment'].update(
        purpose='동일 시각에 도착한 수용 응답에서 환자 순서와 우선순위 가중 도착시간을 비교한다.',
        first_batch_capacity=5,
        response_mode='all_response_same_delay',
    )

    specialized = copy.deepcopy(common)
    specialized.update(
        id='multi_specialized', label='다수 환자 · 희소 치료조건',
        description='환자 5 중 1명은 첫 병원만 가진 합성 역량이 필요하다. 일반 환자도 그 병원의 자원을 쓴다.',
    )
    specialized['hospitals'][0]['capabilities'].append(_SPECIAL)
    specialized['patients'][-1]['required_capabilities'].append(_SPECIAL)
    specialized['patients'][-1]['label'] = '가상 환자 5 · 희소 치료조건'
    specialized['experiment'].update(
        purpose='선택지가 많은 환자에게 대안을 주어 희소 역량이 필요한 환자의 지연을 줄이는지 비교한다.',
        scarce_patient_id='P05', scarce_hospital_id=specialized['hospitals'][0]['id'],
        response_mode='all_response_same_delay',
    )

    uncertain = copy.deepcopy(common)
    uncertain.update(
        id='multi_uncertain_responses', label='다수 환자 · 응답 지연과 무응답',
        description='가까운 병원 응답은 환자별로 일부 지연되거나 오지 않는다. 뒤쪽 후보는 더 빨리 응답한다.',
    )
    # This case separates response-order effects from immediate first-batch resource exhaustion.
    for h in uncertain['hospitals'][:3]:
        h['capacity'] = {'ed_bed': 2, 'special_team': 2}
    for i, p in enumerate(uncertain['patients']):
        for j, h in enumerate(uncertain['hospitals']):
            uncertain['response_environment'][p['id']][h['id']] = dict(
                delay=(4, 1, 3)[j] if j < 3 else 1,
                silent=(j < 3 and j == i % 3),
            )
    uncertain['experiment'].update(
        purpose='느리거나 없는 응답에서 문의 확대·배정 지연과 이동시간의 상충을 확인한다.',
        first_batch_capacity=6,
        response_mode='patient_hospital_specific_delay_and_silence',
    )

    insufficient = copy.deepcopy(common)
    insufficient.update(
        id='multi_insufficient_capacity', label='다수 환자 · 총자원 부족 반례',
        description='환자 5명에 비해 합성 자원은 전체 2자리뿐이고 관찰 중 반환되지 않는다. 없는 자원은 만들 수 없다.',
        max_minutes=80,
    )
    for i, h in enumerate(insufficient['hospitals']):
        available = 1 if i < 2 else 0
        h['capacity'] = {'ed_bed': available, 'special_team': available}
    for p in insufficient['patients']:
        p['treatment_minutes'] = 180
    _set_responses(insufficient, delay=1)
    insufficient['experiment'].update(
        purpose='총자원이 부족하면 모든 정책에 미배정·관찰 미완료가 남는 한계를 함께 보여준다.',
        total_structural_slots=2,
        response_mode='all_response_same_delay',
    )
    insufficient['assumptions'].append(
        '합성 치료 점유시간 180분은 관찰 중 자원 반환을 막는 스트레스 설정이며 실제 처치시간이 아니다.')

    return [simultaneous, specialized, uncertain, insufficient]
