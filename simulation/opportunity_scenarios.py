"""Fresh-seed synthetic cases for bounded allocation opportunity protection.

The first eight families reuse the frozen information experiment's environment
with new seeds. The remaining families isolate the cost of preserving a fresh,
known bed while a patient-specific offer lacks another required resource count.
None of these cases changes the allocation rule or provides future information
to a policy. Confirmation timing and hidden truth belong to the environment.

Development seeds: 19101-19103. Unused validation seeds: 20101-20106.
Structural tests use small seeds such as 7 and do not run the holdout sweep.
"""
from __future__ import annotations

from copy import deepcopy

from .partial_information_scenarios import (
    BASIC, SCARCE, FAMILIES as ORIGINAL_FAMILIES,
    build_information_scenarios,
)


DEVELOPMENT_SEEDS = (19101, 19102, 19103)
VALIDATION_SEEDS = (20101, 20102, 20103, 20104, 20105, 20106)
ADVERSARIAL_FAMILIES = (
    'partial_team_unresolved',
    'delayed_team_confirmation',
    'confirmation_beyond_grace',
    'waiting_priority_high',
    'waiting_priority_low',
    'multiple_waiting_patients',
    'no_alternative_hospital',
    'expiring_waiting_offer',
)
FAMILIES = ORIGINAL_FAMILIES + ADVERSARIAL_FAMILIES

_LABELS = {
    'partial_team_unresolved': '전문팀 수량 재확인도 누락·보호 무한 연장 금지',
    'delayed_team_confirmation': '전문팀 재확인 2분 지연·제한 대기',
    'confirmation_beyond_grace': '전문팀 재확인 5분 지연·보호 기한 이후 해제',
    'waiting_priority_high': '확인 대기 환자의 우선순위가 더 높음',
    'waiting_priority_low': '확인 대기 환자의 우선순위가 더 낮음',
    'multiple_waiting_patients': '전문팀 확인 대기 환자 2명의 병상 경쟁',
    'no_alternative_hospital': '대체 병원 없는 유연 환자와 확인 대기 환자',
    'expiring_waiting_offer': '자원 재확인 전 수용 회신 만료·자동 연장 금지',
}

_ASSUMPTIONS = (
    '병상 기회 보호는 새 별도 합성 실험이며 운영 서버·실제 환자 배정 기능을 변경하지 않는다.',
    '확인 대기 환자의 수용 회신이 유효하고, 병상은 최신 보고로 확인되며, 다른 필수 자원만 누락·노후한 경우를 비교한다.',
    '확인 대기 환자의 미래 자원 회신·복구·이동 결과를 배정 정책에 제공하지 않는다.',
    '보호 시간·우선순위 제한·대체 병원 조건은 모든 사례에 같은 잠근 실험 설정을 적용하며 이 파일에서 튜닝하지 않는다.',
    '재확인 실패·긴 지연·낮은 우선순위·복수 대기·대안 없음·수용 만료를 포함하여 편익과 비용을 함께 평가한다.',
)


def _adversarial(seed, family):
    # Starting from a fixed public structural family keeps the hospital map,
    # route generation, reply policy and treatment distributions comparable.
    case = build_information_scenarios(seed, ['partial_team_missing'])[0]
    case['label'] = _LABELS[family]
    config = case['observation_config']
    patients = case['patients']
    experiment = case['experiment']
    experiment['adversarial_intent'] = family

    if family == 'partial_team_unresolved':
        config['confirmation_missing_resources'] = ['special_team']
        experiment['resource_confirmation_resolves_missing_team'] = False
    elif family == 'delayed_team_confirmation':
        config['confirmation_delay_minutes'] = 2
    elif family == 'confirmation_beyond_grace':
        config['confirmation_delay_minutes'] = 5
    elif family in ('waiting_priority_high', 'waiting_priority_low'):
        waiting_high = family == 'waiting_priority_high'
        for patient in patients[:2]:
            patient['priority'] = 1 if waiting_high else 4
        patients[2]['priority'] = 4 if waiting_high else 1
        experiment['waiting_patient_priority'] = patients[2]['priority']
        experiment['flexible_patient_priority'] = patients[0]['priority']
    elif family == 'multiple_waiting_patients':
        second = deepcopy(patients[2])
        second.update(id='P04', label='합성 전문팀 필요 환자 2')
        patients.append(second)
        near = next(h for h in case['hospitals'] if h['id'] == 'NEAR')
        near['capacity'].update(ed_bed=3, special_team=2)
        for report in config['initial_reports']:
            if report['hospital'] == 'NEAR':
                report['resources']['ed_bed']['capacity'] = 3
        experiment['team_patient_ids'] = ['P03', 'P04']
        experiment['patients_requiring_missing_resource'] = ['P03', 'P04']
    elif family == 'no_alternative_hospital':
        case['hospitals'] = [h for h in case['hospitals'] if h['id'] == 'NEAR']
        config['initial_reports'] = [r for r in config['initial_reports'] if r['hospital'] == 'NEAR']
        del case['response_environment']['*']['FAR']
        experiment['alternative_hospital_id'] = None
    elif family == 'expiring_waiting_offer':
        case['accept_ttl_minutes'] = 1
        config['confirmation_delay_minutes'] = 2
        experiment['offer_expires_before_first_resource_reply'] = True
    else:
        raise AssertionError('unhandled opportunity family')
    return case


def build_opportunity_scenarios(seed, families=None):
    """Return independent synthetic cases in the requested family order.

    Original-family truth, reply and observation inputs are identical to
    build_information_scenarios(seed) aside from descriptive experiment fields.
    Selecting a subset never changes another family's random stream.
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
            raise ValueError('unknown opportunity family')
        if len(set(selected)) != len(selected):
            raise ValueError('duplicate opportunity family')
    from .map_replay import validate_scenario

    original = {case['family']: case for case in
                build_information_scenarios(seed, [f for f in selected if f in ORIGINAL_FAMILIES])}
    cases = []
    for family in selected:
        case = original[family] if family in original else _adversarial(seed, family)
        case.update(id=f'opportunity-{family}-{seed}', family=family,
                    information_family=family, opportunity_family=family)
        case['experiment'].update(
            information_family=family, opportunity_family=family,
            patient_count=len(case['patients']),
            comparison_scope='same_truth_environment_baseline_and_bounded_opportunity_protection',
            prior_information_family=family if family in ORIGINAL_FAMILIES else 'partial_team_missing',
        )
        case['assumptions'].extend(_ASSUMPTIONS)
        validate_scenario(case)
        cases.append(case)
    return deepcopy(cases)
