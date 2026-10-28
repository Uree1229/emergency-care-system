"""Fresh cases for safe early use of existing offers and resource reports.

All thirty-one preceding truth/report environments are retained unchanged at
new seeds. Eight additional environments exercise the dominance check and its
negative controls. No case enables the additional resource-change channel.

Development seeds: 27101-27103. Validation seeds: 28101-28106.
Structural tests use small seeds 7 and 8, never a reserved sweep seed.
"""
from __future__ import annotations

from copy import deepcopy

from .total_wait_scenarios import (
    BASIC, SCARCE, FAMILIES as ORIGINAL_FAMILIES, build_total_wait_scenarios,
)


DEVELOPMENT_SEEDS = (27101, 27102, 27103)
VALIDATION_SEEDS = (28101, 28102, 28103, 28104, 28105, 28106)
ADVERSARIAL_FAMILIES = (
    'dominant_near_slow_other',
    'ample_cohort_dominant_offer',
    'nearer_offer_pending',
    'competing_scarce_pending',
    'dominant_offer_unknown',
    'dominant_offer_stale',
    'future_outage_after_early_offer',
    'expiring_dominant_offer',
)
FAMILIES = ORIGINAL_FAMILIES + ADVERSARIAL_FAMILIES

_LABELS = {
    'dominant_near_slow_other': '최단 후보 수용 확보·먼 병원 회신 대기 제거',
    'ample_cohort_dominant_offer': '현재 환자 모두를 감당하는 자원·공동 조기 재검토',
    'nearer_offer_pending': '먼 병원 먼저 회신·가까운 후보 대기 보존',
    'competing_scarce_pending': '이미 등록된 희소 환자 문의 중·조기 선점 금지',
    'dominant_offer_unknown': '최단 병원 수용에도 수량 미확인·조기 배정 금지',
    'dominant_offer_stale': '최단 병원 수용에도 수량 노후·기존 재확인 필요',
    'future_outage_after_early_offer': '조기 수용 후 미래 자원 중단·변경 위험 대조',
    'expiring_dominant_offer': '다음 분에 만료되는 수용·조기 판단 유효기간 대조',
}
_ASSUMPTIONS = (
    '기존 정보 활용 개선은 별도 합성 실험이며 운영 서버·실제 병원·팀원 화면을 변경하지 않는다.',
    '모든 비교 분기는 추가 자원 변화 보고를 끄며 기존 초기 보고·주기 피드·재확인 계약만 사용한다.',
    '새 규칙은 현재 수신한 환자별 수용과 자원 보고·현재 등록 환자 수요만 사용한다.',
    '회신 예정시각·미래 병상 해제·미래 자원 중단·미발생 환자는 조기 재검토의 입력이 아니다.',
    '최단 후보라도 수량 누락·노후·경합이 있으면 기존 대기 절차를 유지하며 이미 만료된 수용을 사용하지 않는다.',
    '조기 확정 후 발생하는 외생 조건 변화는 같은 기존 알림·재탐색 절차로 처리하고 위험을 함께 평가한다.',
)


def _adversarial(seed, family):
    case = build_total_wait_scenarios(seed, ['fresh_control'])[0]
    case['label'] = _LABELS[family]
    case['patients'] = case['patients'][:1]
    patient = case['patients'][0]
    patient.update(required_capabilities=[BASIC], resource_demands={'ed_bed': 1})
    case['response_environment'] = {'*': {
        'NEAR': dict(delay=1, silent=False),
        'FAR': dict(delay=5, silent=False),
    }}
    case['experiment'] = dict(
        seed=seed, patient_count=1, clinical_values='synthetic',
        identity_source='synthetic', routing='synthetic_fixed_coordinates',
        timing_unit='integer_minute', common_inquiry_settings=dict(k=3, wait=2, notify=True),
        existing_info_adversarial_intent=family,
        additional_resource_change_channel_enabled=False,
        nearest_hospital_id='NEAR', first_near_reply_minute=1,
        first_far_reply_minute=5,
    )

    if family == 'dominant_near_slow_other':
        case['experiment']['intent'] = 'closest_accepted_option_with_sufficient_current_reports'

    elif family == 'ample_cohort_dominant_offer':
        template = build_total_wait_scenarios(seed, ['fresh_control'])[0]
        case['patients'] = deepcopy(template['patients'][:2])
        for patient in case['patients']:
            patient.update(required_capabilities=[BASIC], resource_demands={'ed_bed': 1})
        case['experiment'].update(patient_count=2, current_registered_ed_bed_demand=2,
                                  near_reported_ed_bed_capacity=2,
                                  intent='reported_capacity_covers_entire_registered_cohort')

    elif family == 'nearer_offer_pending':
        case['response_environment']['*'].update(
            NEAR=dict(delay=2, silent=False), FAR=dict(delay=1, silent=False))
        case['experiment'].update(first_near_reply_minute=2, first_far_reply_minute=1,
                                  intent='faster_reply_is_not_a_dominant_route')

    elif family == 'competing_scarce_pending':
        template = build_total_wait_scenarios(seed, ['fresh_control'])[0]
        scarce = deepcopy(template['patients'][2])
        patient['priority'] = 2
        scarce.update(required_capabilities=[BASIC, SCARCE],
                      resource_demands={'ed_bed': 1, 'special_team': 1}, priority=4)
        case['patients'].append(scarce)
        case['hospitals'][0]['capacity']['ed_bed'] = 1
        case['response_environment'][scarce['id']] = {'NEAR': dict(delay=2, silent=False)}
        case['experiment'].update(patient_count=2, scarce_patient_id=scarce['id'],
                                  scarce_patient_first_reply_minute=2,
                                  current_registered_ed_bed_demand=2,
                                  near_reported_ed_bed_capacity=1,
                                  intent='registered_pending_demand_prevents_early_bed_capture')

    elif family in ('dominant_offer_unknown', 'dominant_offer_stale'):
        observation = case['observation_config']
        observation.update(feed_interval_minutes=0, confirmation_delay_minutes=4)
        if family == 'dominant_offer_unknown':
            observation['initial_reports'] = []
        else:
            observation['initial_reports'] = [dict(
                hospital=hospital['id'], observed_at=-11,
                resources={resource: dict(capacity=quantity, used=0)
                           for resource, quantity in hospital['capacity'].items()})
                for hospital in case['hospitals']]
        case['experiment'].update(first_existing_confirmation_minute=4,
                                  intent='acceptance_does_not_refresh_resource_quantities')

    elif family == 'future_outage_after_early_offer':
        case['hospitals'][0]['capacity']['ed_bed'] = 1
        case['exogenous_events'] = [dict(type='resource_unavailable', t=2, until=10,
                                       hospital='NEAR', resource='ed_bed')]
        case['experiment'].update(physical_outage_minute=2, physical_restore_minute=10,
                                  intent='early_commit_does_not_predict_future_outage')

    elif family == 'expiring_dominant_offer':
        case['accept_ttl_minutes'] = 1
        case['experiment'].update(first_near_offer_expiry_minute=2,
                                  intent='current_valid_offer_may_be_used_before_expiry')
    else:
        raise AssertionError('unhandled existing-information family')
    return case


def build_existing_info_scenarios(seed, families=None):
    """Return detached environments without family-order random coupling."""
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
            raise ValueError('unknown existing-information family')
        if len(set(selected)) != len(selected):
            raise ValueError('duplicate existing-information family')

    from .map_replay import validate_scenario

    originals = {case['family']: case for case in build_total_wait_scenarios(
        seed, [family for family in selected if family in ORIGINAL_FAMILIES])}
    cases = []
    for family in selected:
        case = originals[family] if family in originals else _adversarial(seed, family)
        case.update(id=f'existing-info-{family}-{seed}', family=family,
                    existing_info_family=family)
        case['experiment'].update(
            existing_info_family=family, patient_count=len(case['patients']),
            comparison_scope='same_existing_reporting_contract_early_offer_and_event_review',
            additional_resource_change_channel_enabled=False,
            prior_total_wait_family=family if family in originals else 'fresh_control',
        )
        case['assumptions'].extend(_ASSUMPTIONS)
        validate_scenario(case)
        cases.append(case)
    return deepcopy(cases)
