"""Fresh-seed synthetic cases for objective-preserving C1 tie stability.

The twenty-two frozen response-confirmation environments are reused exactly
at new seeds. Four structural cases exercise request chronology, patient and
hospital identity ties, and a greedy-suboptimal choice that must not replace
the better C1 plan. Only current reported inputs reach an allocation policy.

Development seeds: 23101-23103. Validation seeds: 24101-24106.
Structural tests use seeds 7 and 8, never a reserved sweep seed.
"""
from __future__ import annotations

from copy import deepcopy

from .responsive_confirmation_scenarios import (
    BASIC, SCARCE, FAMILIES as ORIGINAL_FAMILIES,
    build_responsive_confirmation_scenarios,
)


DEVELOPMENT_SEEDS = (23101, 23102, 23103)
VALIDATION_SEEDS = (24101, 24102, 24103, 24104, 24105, 24106)
ADVERSARIAL_FAMILIES = (
    'tie_older_request_priority',
    'tie_same_request_identity',
    'tie_equal_distance_hospital_identity',
    'greedy_suboptimal_scarce_option',
)
FAMILIES = ORIGINAL_FAMILIES + ADVERSARIAL_FAMILIES

_LABELS = {
    'tie_older_request_priority': '같은 우선순위·이동비용에서 오래된 문의 우선',
    'tie_same_request_identity': '같은 문의 시각·환자 입력 역순에서 ID 동률 기준',
    'tie_equal_distance_hospital_identity': '동일 거리의 합성 병원·병원 입력 역순',
    'greedy_suboptimal_scarce_option': '탐욕 배정이 열세인 희소 후보·더 좋은 C1 보존',
}
_ASSUMPTIONS = (
    'C1 동률 안정성은 별도 합성 실험이며 운영 서버와 팀원 화면을 변경하지 않는다.',
    '기존 두 목적값이 정확히 같은 경우에만 검증된 우선순위·문의시각·ID 기반 탐욕안을 선택한다.',
    '탐욕안의 두 목적값이 열세이면 더 좋은 C1 결과를 유지하며 세 번째 전역 최적화를 주장하지 않는다.',
    '동률 기준은 이미 요청한 환자와 현재 유효 수용 응답·보고 자원·이동시간만 사용한다.',
    '대안 없는 조건의 개별 환자 지연과 보호·선행 재확인의 편익 및 비용을 함께 평가한다.',
)


def _adversarial(seed, family):
    # This base supplies deterministic synthetic geometry/treatment variation,
    # without carrying its missing-resource or future-event assumptions here.
    case = build_responsive_confirmation_scenarios(seed, ['fresh_control'])[0]
    case['label'] = _LABELS[family]
    case['patients'] = case['patients'][:2]
    for patient in case['patients']:
        patient.update(required_capabilities=[BASIC], resource_demands={'ed_bed': 1})
    case['experiment'] = dict(
        seed=seed, patient_count=2, clinical_values='synthetic',
        identity_source='synthetic', routing='synthetic_fixed_coordinates',
        timing_unit='integer_minute', common_inquiry_settings=dict(k=3, wait=2, notify=True),
        stable_adversarial_intent=family,
    )

    if family in ('tie_older_request_priority', 'tie_same_request_identity'):
        near = deepcopy(case['hospitals'][0])
        near['capacity']['ed_bed'] = 1
        case['hospitals'] = [near]
        case['response_environment'] = {'*': {'NEAR': dict(delay=1, silent=False)}}
        case['experiment'].update(scarce_hospital_id='NEAR', alternative_hospital_id=None)

        if family == 'tie_older_request_priority':
            # P02 is older despite lexically following P01. Patient-specific
            # fixed reply delays align both offered choices in the t=2 batch.
            newer = next(patient for patient in case['patients'] if patient['id'] == 'P01')
            for key in ('occur_t', 'dispatch_t', 'ambulance_arrive_t', 'search_start_t'):
                newer[key] = 1
            case['response_environment'] = {
                '*': {'NEAR': dict(delay=1, silent=False)},
                'P02': {'NEAR': dict(delay=2, silent=False)},
            }
            case['experiment'].update(expected_first_tie_patient_id='P02',
                                      initial_offers_align_at=2,
                                      tie_intent='older_request_before_patient_identity')
        else:
            case['patients'].reverse()
            case['experiment'].update(expected_first_tie_patient_id='P01',
                                      initial_offers_align_at=1,
                                      tie_intent='patient_identity_independent_of_input_order')

    elif family == 'tie_equal_distance_hospital_identity':
        # Two logical synthetic institutions intentionally share coordinates;
        # this is a cost-tie fixture, not a claim about real hospital locations.
        template = deepcopy(case['hospitals'][0])
        hospitals = []
        for hospital_id in ('Z_EQ', 'A_EQ'):
            hospital = deepcopy(template)
            hospital.update(id=hospital_id, name=f'합성 동거리 병원 {hospital_id}')
            hospital['capacity']['ed_bed'] = 1
            hospitals.append(hospital)
        case['hospitals'] = hospitals
        case['patients'].reverse()
        case['response_environment'] = {'*': {
            hospital['id']: dict(delay=1, silent=False) for hospital in hospitals}}
        case['experiment'].update(expected_first_tie_patient_id='P01',
                                  expected_first_tie_hospital_id='A_EQ',
                                  tie_intent='hospital_identity_after_equal_travel')

    elif family == 'greedy_suboptimal_scarce_option':
        for hospital in case['hospitals']:
            hospital['capacity']['ed_bed'] = 1
        scarce = next(patient for patient in case['patients'] if patient['id'] == 'P02')
        scarce.update(required_capabilities=[BASIC, SCARCE],
                      resource_demands={'ed_bed': 1, 'special_team': 1})
        case['experiment'].update(
            flexible_patient_ids=['P01'], team_patient_id='P02',
            scarce_hospital_id='NEAR', alternative_hospital_id='FAR',
            expected_greedy_first_batch_unassigned_priority=4,
            expected_c1_first_batch_unassigned_priority=0,
            tie_intent='strictly_better_c1_must_not_be_overwritten',
        )
    else:
        raise AssertionError('unhandled stable C1 family')
    return case


def build_stable_c1_scenarios(seed, families=None):
    """Return detached cases in requested order with unchanged prior inputs."""
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
            raise ValueError('unknown stable C1 family')
        if len(set(selected)) != len(selected):
            raise ValueError('duplicate stable C1 family')

    from .map_replay import validate_scenario

    originals = {case['family']: case for case in build_responsive_confirmation_scenarios(
        seed, [family for family in selected if family in ORIGINAL_FAMILIES])}
    cases = []
    for family in selected:
        case = originals[family] if family in originals else _adversarial(seed, family)
        case.update(id=f'stable-c1-{family}-{seed}', family=family,
                    information_family=family, opportunity_family=family,
                    responsive_confirmation_family=family, stable_c1_family=family)
        case['experiment'].update(
            information_family=family, opportunity_family=family,
            responsive_confirmation_family=family, stable_c1_family=family,
            patient_count=len(case['patients']),
            comparison_scope='same_truth_environment_objective_preserving_c1_tie_stability',
            prior_responsive_family=family if family in originals else 'fresh_control',
        )
        case['assumptions'].extend(_ASSUMPTIONS)
        validate_scenario(case)
        cases.append(case)
    return deepcopy(cases)
