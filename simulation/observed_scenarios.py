"""병원별 관측 응답 정책을 위한 결과 독립적인 합성 입력.

기관 식별자·이름·좌표만 기준 입력에서 복사한다. 검토 인원, 서비스시간,
환자, 치료조건, 배경 업무는 실제 병원 운영자료가 아니다.
"""
from __future__ import annotations

import copy
import math

from .workload_scenarios import _new_base


FAMILIES = ('burst_w1', 'burst_w2', 'streaming_w1', 'streaming_w2',
            'shifting_background_w1', 'shifting_background_w2',
            'scarce_stream', 'totalshortage')
_LABELS = {
    'burst_w1': '동시 발생 · 담당 1명', 'burst_w2': '동시 발생 · 담당 2명',
    'streaming_w1': '시간차 발생 · 담당 1명', 'streaming_w2': '시간차 발생 · 담당 2명',
    'shifting_background_w1': '배경 업무 변화 · 담당 1명',
    'shifting_background_w2': '배경 업무 변화 · 담당 2명',
    'scarce_stream': '시간차 발생 · 희소 조건', 'totalshortage': '총자원 부족 대조',
}


def _background_jobs(value, family):
    """외생 일정은 정책이나 실행 결과를 읽지 않고 생성한다."""
    start = min(p['search_start_t'] for p in value['patients'])
    validation = value['split'] == 'validation'
    jobs = []
    for j, hospital in enumerate(value['hospitals']):
        for wave in range(2):
            jobs.append(dict(t=max(0, start - 2 + wave * (7 if validation else 6)),
                             hospital=hospital['id'], service_minutes=2 + (j + wave) % 3 + validation))
    if family.startswith('shifting_background'):
        # The first half of hospitals are busier in the later phase; the second
        # half are busier initially. Neither schedule is exposed to the policy.
        half = max(1, len(value['hospitals']) // 2)
        shift = 43 if validation else 28
        spacing = 9 if validation else 8
        for j, hospital in enumerate(value['hospitals']):
            phase = shift if j < half else 0
            for wave in range(4):
                jobs.append(dict(t=start + phase + wave * spacing,
                                 hospital=hospital['id'], service_minutes=6 + j % 3 + validation))
    return sorted(jobs, key=lambda job: (job['t'], job['hospital']))


def build_observed_scenarios(base_scenario, split='development', nonresponse_levels=(0, .2)):
    """8개 계열 × 무응답 조건을 반환한다. 입력과 각 반환값은 서로 독립적이다."""
    levels = list(nonresponse_levels)
    if not levels or len(set(levels)) != len(levels):
        raise ValueError('무응답 조건은 하나 이상이며 중복이 없어야 합니다.')
    if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x)
           or not 0 <= x <= 1 for x in levels):
        raise ValueError('무응답 확률은 0~1의 유한한 수여야 합니다.')
    count = 12 if split == 'development' else 16
    common = _new_base(base_scenario, split, 'inquiry_workload', count)
    common.update(family='observed_inquiry', max_minutes=260 if split == 'development' else 330,
                  timeout_minutes=16 if split == 'development' else 19,
                  retry_minutes=4, accept_ttl_minutes=12)
    common['assumptions'] = [
        '기관 식별자·이름·좌표만 공개 자료이며 환자·역량·자원·문의 서비스시간·인원·업무는 합성값이다.',
        '문의 정책은 자신이 보낸 문의의 경과시간과 실제 받은 회신·타임아웃만 관측한다. 실제 인원·대기열·미래 응답·외생 일정은 제공하지 않는다.',
        '일반 계열은 각 병원의 치료 자원을 환자 수 이상으로 두어 문의 검토 부하를 분리한다. 이는 실제 병원 용량을 의미하지 않는다.',
        '희소 계열은 특정 환자의 조건을 한 병원만 충족한다. 부족 계열은 전체 병상 3개를 관찰 종료 이후까지 점유한다.',
        '모든 환자의 현장과 구급차 출발지는 같은 기준 위치다. 새로운 지역·임상 환경을 대표하지 않는다.',
        '우선순위 1·2·4와 치료 점유시간은 실험값이며 임상 중증도나 실제 치료·예후를 뜻하지 않는다.',
        '업무 일정과 무응답 조건은 결과를 보기 전에 고정한다. 담당 1명/2명 대조는 처리 인원만 다르다.',
    ]
    common['experiment'].update(iteration='observed_inquiry_v2', family='observed_inquiry',
        generation_selected_from_outcomes=False, clinical_values='synthetic',
        future_information_available_to_policy=False, policy_has_true_queue_state=False,
        response_delay_interpretation='synthetic_review_service_duration',
        new_input_settings=['patient_count', 'stream_spacing', 'hospital_service_heterogeneity',
                            'shifting_background_work', 'timeout', 'observation_horizon'])
    template = base_scenario['patients'][0]
    phase = {key: template.get(key, template.get('occur_t', 0) if key in ('occur_t', 'dispatch_t') else 0)
             for key in ('occur_t', 'dispatch_t', 'ambulance_arrive_t', 'search_start_t')}
    result = []
    for family in FAMILIES:
        source = copy.deepcopy(common)
        source['case_kind'] = family
        source['experiment']['case_kind'] = family
        source['inquiry_queue']['workers'] = 2 if family.endswith('_w2') else 1
        for index, patient in enumerate(source['patients']):
            offset = index // (4 if split == 'development' else 3) if family.startswith('burst') else index * (5 if split == 'development' else 6)
            for key, value in phase.items():
                patient[key] = value + offset
            patient['treatment_minutes'] = (20, 28, 35)[index % 3] + (5 if split == 'validation' else 0)
        for hospital in source['hospitals']:
            hospital['capacity'] = dict(ed_bed=count, special_team=count, imaging_unit=count)
        # Persistent hospital differences plus limited patient variation allow
        # observations to carry information without revealing service durations.
        source['response_environment'] = {
            patient['id']: {hospital['id']: dict(
                delay=1 + (j % 4) + (i % 3 == 0) + (split == 'validation'), silent=False)
                for j, hospital in enumerate(source['hospitals'])}
            for i, patient in enumerate(source['patients'])}
        source['inquiry_queue']['background_jobs'] = _background_jobs(source, family)
        source['experiment'].update(patient_count=count,
            background_schedule='fixed_before_runs_not_policy_commit_or_result',
            sufficient_treatment_capacity=family != 'totalshortage',
            purpose='병원별 관측과 문의 경과시간이 문의 부담·치료 시작을 개선하는지 비교한다.')
        if family == 'scarce_stream':
            source['hospitals'][0]['capabilities'].append('가상 희소치료조건')
            ids = []
            for index in range(3, count, 4):
                patient = source['patients'][index]
                patient['required_capabilities'] = ['가상 기본진료', '가상 희소치료조건']
                patient['resource_demands'] = dict(ed_bed=1, special_team=1)
                patient['priority'] = 4
                patient['label'] += ' · 희소 조건'
                ids.append(patient['id'])
            source['experiment'].update(scarce_patient_ids=ids,
                scarce_hospital_id=source['hospitals'][0]['id'])
        if family == 'totalshortage':
            for index, hospital in enumerate(source['hospitals']):
                hospital['capacity']['ed_bed'] = 1 if index < 3 else 0
            for patient in source['patients']:
                patient['treatment_minutes'] = source['max_minutes'] + 60
            source['experiment']['total_structural_slots'] = 3
        for probability in levels:
            value = copy.deepcopy(source)
            value.update(id=f'observed_{family}_{split}_nr{round(probability * 1000):03d}',
                         label=f'병원별 관측 검증 · {_LABELS[family]} · 무응답 {probability:.0%} · {split}',
                         description='결과를 보기 전에 정의한 병원별 응답·문의 경과시간 정책의 합성 대조 입력.',
                         experiment_nonresponse=probability)
            value['experiment']['nonresponse_probability'] = probability
            result.append(value)
    if len({s['id'] for s in result}) != len(result):
        raise ValueError('무응답 확률로 만든 ID가 충돌했습니다.')
    return result
