"""우선순위 보호 정책의 결과 독립적인 합성 대조 입력.

공개 기관의 식별자·이름·좌표만 유지한다. 환자, 역량, 치료 자원, 문의 검토
업무와 외생 사건은 합성값이다. 정책의 배정 결과를 보고 조건을 바꾸지 않는다.
"""
from __future__ import annotations

import copy
import math

from .workload_scenarios import _new_base


FAMILIES = (
    'sufficient_burst_w1', 'sufficient_burst_w2', 'mixed_priority_scarcity',
    'staggered_priority', 'scarce_capability', 'shifting_workload',
    'resource_change', 'total_shortage',
)
_LABELS = {
    'sufficient_burst_w1': '충분한 자원 · 동시 발생 · 담당 1명',
    'sufficient_burst_w2': '충분한 자원 · 동시 발생 · 담당 2명',
    'mixed_priority_scarcity': '동시 혼합 우선순위 · 자원 부족',
    'staggered_priority': '낮은 우선순위 먼저 · 높은 우선순위 후발',
    'scarce_capability': '높은 우선순위 · 희소 역량',
    'shifting_workload': '시간차 발생 · 병원별 배경 업무 변화',
    'resource_change': '시간차 발생 · 고정 외생 자원 변화',
    'total_shortage': '총자원 부족 대조',
}
_BASIC = '가상 기본진료'
_TEAM = '가상 전문팀치료조건'
_SCARCE = '가상 희소치료조건'
# Small local offsets diversify scenes without changing public hospital points.
_SCENE_OFFSETS = (
    (0, 0), (.004, 0), (0, .004), (-.004, 0), (0, -.004),
    (.003, .003), (-.003, .003), (.003, -.003), (-.003, -.003),
)


def _phase(template):
    return {key: template.get(key, template.get('occur_t', 0)
                             if key in ('occur_t', 'dispatch_t') else 0)
            for key in ('occur_t', 'dispatch_t', 'ambulance_arrive_t', 'search_start_t')}


def _set_phases(value, phase, offsets):
    for patient, offset in zip(value['patients'], offsets):
        for key, original in phase.items():
            patient[key] = original + offset


def _distribute_beds(value, total):
    """Distribute a fixed total across all source hospitals without picking a winner."""
    count = len(value['hospitals'])
    quotient, remainder = divmod(total, count)
    for index, hospital in enumerate(value['hospitals']):
        hospital['capacity']['ed_bed'] = quotient + (index < remainder)


def _background_jobs(value, shifting=False):
    start = min(p['search_start_t'] for p in value['patients'])
    validation = value['split'] == 'validation'
    jobs = [dict(t=max(0, start - 2 + wave * (7 if validation else 6)),
                 hospital=hospital['id'], service_minutes=2 + (index + wave) % 3 + validation)
            for wave in range(2) for index, hospital in enumerate(value['hospitals'])]
    if shifting:
        half = max(1, len(value['hospitals']) // 2)
        for index, hospital in enumerate(value['hospitals']):
            offset = (43 if validation else 28) if index < half else 0
            for wave in range(4):
                jobs.append(dict(t=start + offset + wave * (9 if validation else 8),
                                 hospital=hospital['id'],
                                 service_minutes=6 + index % 3 + validation))
    return sorted(jobs, key=lambda job: (job['t'], job['hospital']))


def build_priority_scenarios(base_scenario, split='development', nonresponse_levels=(0, .2)):
    """8개 계열 × 무응답 조건을 새 객체로 반환한다.

    시간·좌표·자원·배경 업무는 실행 전에 결정된다. 후발 고우선순위 계열은
    미래 환자를 알지 못하는 정책의 한계를 함께 평가하며, 그 환자를 위한
    사전 자원 비축을 요구하거나 우선순위 정책의 승리를 보장하지 않는다.
    """
    levels = list(nonresponse_levels)
    if not levels or len(set(levels)) != len(levels):
        raise ValueError('무응답 조건은 하나 이상이며 중복이 없어야 합니다.')
    if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x)
           or not 0 <= x <= 1 for x in levels):
        raise ValueError('무응답 확률은 0~1의 유한한 수여야 합니다.')
    validation = split == 'validation'
    count = 16 if validation else 12
    common = _new_base(base_scenario, split, 'inquiry_workload', count)
    common.update(family='priority_protection', max_minutes=300 if validation else 240,
                  timeout_minutes=19 if validation else 16, retry_minutes=4,
                  accept_ttl_minutes=12)
    common['inquiry_queue']['enabled'] = True
    common['assumptions'] = [
        '기관 식별자·이름·좌표만 공개 자료이며 환자·치료 역량·자원·문의 서비스시간·인원·업무·사건은 합성값이다.',
        '우선순위 1·2·4와 치료 점유시간은 실험값이며 임상 분류·허용시간·예후를 뜻하지 않는다.',
        '각 환자에게 별도 구급차가 출동하며 현장과 출발지는 기준 위치 주변의 고정된 작은 좌표 오프셋으로 분산한다.',
        '정책은 이미 발생한 환자와 실제로 받은 회신·타임아웃·현재까지 경과시간만 사용한다. 미발생 환자·미래 회신·외생 일정·실제 대기열은 제공하지 않는다.',
        '후발 고우선순위 환자는 미리 알려주지 않는다. 아직 발생하지 않은 환자를 위한 자원 비축이나 모든 높은 우선순위 환자의 수용을 보장하지 않는다.',
        '충분한 자원 계열은 문의 부하를 분리한다. 나머지 계열은 치료 자원 경쟁과 문의 처리 부하를 함께 포함한다.',
        '혼합 부족 계열과 총부족 대조는 일부 점유를 관찰 종료 이후까지 유지한다. 평균시간 개선과 미치료 환자 감소를 구분한다.',
        '담당 1명/2명 대조는 문의 처리 인원만 다르다. 무응답과 배경 업무·자원 변화의 시각은 모든 정책에 동일하다.',
        '외생 사건은 검색 시작시각에 고정 오프셋을 더해 생성하며 실제 배정·도착·개선 결과를 보고 병원이나 시각을 바꾸지 않는다.',
        '이 입력은 실제 119 체계나 특정 병원의 운영을 재현하지 않으며 개발·검증 차이는 외적 타당성의 증거가 아니다.',
    ]
    common['experiment'].update(
        iteration='priority_protection_v3', family='priority_protection',
        generation_selected_from_outcomes=False, clinical_values='synthetic',
        future_information_available_to_policy=False, policy_has_true_queue_state=False,
        known_future_patients_available_to_policy=False,
        pre_reservation_for_unborn_patients=False,
        scene_geometry='fixed_small_offsets_from_base_scene_and_ambulance_origin',
        response_delay_interpretation='synthetic_review_service_duration',
        background_schedule='fixed_before_runs_not_policy_commit_or_result',
        new_input_settings=['priority_mixture', 'treatment_capacity_competition',
                            'spatial_offsets', 'staggered_priority', 'exogenous_resource_changes'],
    )
    template = base_scenario['patients'][0]
    phase = _phase(template)
    for index, patient in enumerate(common['patients']):
        lat, lon = _SCENE_OFFSETS[index % len(_SCENE_OFFSETS)]
        patient['location'] = [template['location'][0] + lat, template['location'][1] + lon]
        patient['ambulance_origin'] = [template['ambulance_origin'][0] + lat,
                                       template['ambulance_origin'][1] + lon]
        patient['priority'] = (4, 1, 2, 4, 2, 1)[index % 6]
        patient['treatment_minutes'] = (48, 60, 72)[index % 3] + (12 if validation else 0)
    for hospital in common['hospitals']:
        hospital['capacity'] = dict(ed_bed=2, special_team=1, imaging_unit=1)
    common['response_environment'] = {
        patient['id']: {hospital['id']: dict(
            delay=1 + (j % 4) + (i % 3 == 0) + validation, silent=False)
            for j, hospital in enumerate(common['hospitals'])}
        for i, patient in enumerate(common['patients'])}

    result = []
    for family in FAMILIES:
        source = copy.deepcopy(common)
        source['case_kind'] = family
        source['experiment']['case_kind'] = family
        source['inquiry_queue']['workers'] = 2 if family == 'sufficient_burst_w2' else 1
        source['inquiry_queue']['background_jobs'] = _background_jobs(source, family == 'shifting_workload')
        _set_phases(source, phase, [i * (5 if validation else 4) for i in range(count)])
        source['experiment'].update(
            sufficient_treatment_capacity=family.startswith('sufficient_burst'),
            purpose='높은 우선순위군의 미치료·지연을 보호하면서 문의 부담과 전체 시간을 비교한다.')

        if family.startswith('sufficient_burst'):
            _set_phases(source, phase, [i // (3 if validation else 4) for i in range(count)])
            for hospital in source['hospitals']:
                hospital['capacity'] = dict(ed_bed=count, special_team=count, imaging_unit=count)

        elif family == 'mixed_priority_scarcity':
            _set_phases(source, phase, [0] * count)
            for index, patient in enumerate(source['patients']):
                patient.update(priority=(1, 4, 2, 4)[index % 4],
                               required_capabilities=[_BASIC], resource_demands={'ed_bed': 1},
                               treatment_minutes=source['max_minutes'] + 60)
            _distribute_beds(source, count // 2)
            source['experiment']['total_structural_slots'] = count // 2

        elif family == 'staggered_priority':
            half = count // 2
            later = 24 if validation else 18
            offsets = [i * 2 if i < half else later + (i - half) * 3 for i in range(count)]
            _set_phases(source, phase, offsets)
            for index, patient in enumerate(source['patients']):
                patient.update(priority=(1, 2)[index % 2] if index < half else 4,
                               required_capabilities=[_BASIC], resource_demands={'ed_bed': 1},
                               treatment_minutes=80 if validation else 65)
            _distribute_beds(source, half)
            source['experiment'].update(total_structural_slots=half,
                high_priority_first_offset=later,
                purpose='후발 환자를 모르는 온라인 정책의 한계와 발생 이후의 우선순위 대응을 함께 측정한다.')

        elif family == 'scarce_capability':
            _set_phases(source, phase, [i * (4 if validation else 3) for i in range(count)])
            ids = []
            for index, patient in enumerate(source['patients']):
                patient.update(required_capabilities=[_BASIC], resource_demands={'ed_bed': 1})
                if index % 4 == 3:
                    patient.update(priority=4, required_capabilities=[_BASIC, _SCARCE],
                                   resource_demands={'ed_bed': 1, 'special_team': 1})
                    patient['label'] += ' · 희소 조건'
                    ids.append(patient['id'])
            for hospital in source['hospitals'][:2]:
                hospital['capabilities'].append(_SCARCE)
                hospital['capacity'].update(ed_bed=2, special_team=1)
            source['experiment'].update(scarce_patient_ids=ids,
                scarce_hospital_ids=[hospital['id'] for hospital in source['hospitals'][:2]])

        elif family == 'resource_change':
            _set_phases(source, phase, [i * (4 if validation else 3) for i in range(count)])
            for index, patient in enumerate(source['patients']):
                if index % 3 == 0:
                    patient.update(priority=4, required_capabilities=[_BASIC, _TEAM],
                                   resource_demands={'ed_bed': 1, 'special_team': 1})
            first = source['hospitals'][0]['id']
            start = min(p['search_start_t'] for p in source['patients'])
            stop_t = start + (11 if validation else 8)
            external_t = start + (24 if validation else 18)
            source['exogenous_events'] = [
                dict(t=stop_t, type='resource_unavailable', hospital=first,
                     resource='special_team', until=stop_t + (52 if validation else 40)),
                dict(t=external_t, type='external_arrival', hospital=first,
                     demand={'ed_bed': 1, 'special_team': 1}, duration=70 if validation else 55),
            ]
            source['experiment'].update(
                event_time_source='fixed_offset_from_first_search_not_policy_commit_or_route',
                event_hospital=first, event_offsets=[11, 24] if validation else [8, 18])

        elif family == 'total_shortage':
            _set_phases(source, phase, [i // 3 for i in range(count)])
            _distribute_beds(source, 3)
            for patient in source['patients']:
                patient.update(required_capabilities=[_BASIC], resource_demands={'ed_bed': 1},
                               treatment_minutes=source['max_minutes'] + 60)
            source['experiment']['total_structural_slots'] = 3

        for probability in levels:
            value = copy.deepcopy(source)
            value.update(id=f'priority_{family}_{split}_nr{round(probability * 1000):03d}',
                         label=f'우선순위 보호 검증 · {_LABELS[family]} · 무응답 {probability:.0%} · {split}',
                         description='실행 결과를 보기 전에 정의한 문의 부하·자원 경쟁·우선순위 보호의 합성 대조 입력.',
                         experiment_nonresponse=probability)
            value['experiment']['nonresponse_probability'] = probability
            result.append(value)
    if len({value['id'] for value in result}) != len(result):
        raise ValueError('무응답 확률로 만든 ID가 충돌했습니다.')
    return result
