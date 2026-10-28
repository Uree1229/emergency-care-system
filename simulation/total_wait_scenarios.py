"""Fresh synthetic environments for current-change notification and review.

The twenty-six frozen tie-stability environments are retained at new seeds.
Five further cases distinguish avoidable information/retry delay from the
physical queue that a single bed cannot remove. Notifications describe only
the capacity/used changes already observed by the environment, never planned
release times. All branches share the same notification-source contract.

Development seeds: 25101-25103. Validation seeds: 26101-26106.
Structural tests use seeds 7 and 8, never a reserved sweep seed.
"""
from __future__ import annotations

from copy import deepcopy

from .stable_c1_scenarios import (
    BASIC, SCARCE, FAMILIES as ORIGINAL_FAMILIES, build_stable_c1_scenarios,
)


DEVELOPMENT_SEEDS = (25101, 25102, 25103)
VALIDATION_SEEDS = (26101, 26102, 26103, 26104, 26105, 26106)
ADVERSARIAL_FAMILIES = (
    'release_long_retry',
    'release_before_offer_expiry',
    'release_notification_delay',
    'periodic_release_long_retry',
    'intrinsic_single_hospital_queue',
)
FAMILIES = ORIGINAL_FAMILIES + ADVERSARIAL_FAMILIES

_LABELS = {
    'release_long_retry': '병상 해제 뒤 긴 재문의 간격·현재 변화 통지',
    'release_before_offer_expiry': '수용 회신 유효기간 중 병상 중단·해제',
    'release_notification_delay': '병상 해제 통지 3분 지연·미래 정보 금지',
    'periodic_release_long_retry': '같은 지연 피드에서 병상 해제 수신 후 재문의',
    'intrinsic_single_hospital_queue': '대안 없는 단일 병상·물리적 대기 대조',
}
_ASSUMPTIONS = (
    '전체 대기 감소는 새 별도 합성 실험이며 운영 서버·실제 병원·팀원 화면을 변경하지 않는다.',
    '자원 변화 통지의 입력 계약은 모든 분기에 동일하며 실제 수신 여부와 사건 재검토를 별도 비교한다.',
    '추가 통지는 현재 발생한 용량·사용량 변화만 전달하며 미래 퇴실·복구·처치 종료 시각을 전달하지 않는다.',
    '기존 자원 피드와 재확인에서 지속 누락한 자원은 추가 통지에서도 누락하며 병원 무응답을 우회하지 않는다.',
    '통지 지연·지속 누락·물리적 병상 부족을 남겨 두고 전체 환자의 대기와 처치 도달을 함께 비교한다.',
    '자원 통지는 환자별 수용 응답을 대신하거나 만료된 응답을 되살리는 근거가 아니다.',
)


def _resource_update_contract(case, *, delay_minutes=0):
    observation = case['observation_config']
    # A new source must not silently resolve a field deliberately withheld by
    # either of the existing hospital reporting channels in this experiment.
    masked = set(observation.get('feed_missing_resources', ()))
    masked.update(observation.get('confirmation_missing_resources', ()))
    return dict(delay_minutes=delay_minutes, missing_resources=sorted(masked),
                silent=observation.get('confirmation_silent', False))


def _adversarial(seed, family):
    case = build_stable_c1_scenarios(seed, ['fresh_control'])[0]
    case['label'] = _LABELS[family]
    case['patients'] = case['patients'][:1]
    patient = case['patients'][0]
    patient.update(required_capabilities=[BASIC], resource_demands={'ed_bed': 1})
    case['experiment'] = dict(
        seed=seed, patient_count=1, clinical_values='synthetic',
        identity_source='synthetic', routing='synthetic_fixed_coordinates',
        timing_unit='integer_minute', common_inquiry_settings=dict(k=3, wait=2, notify=True),
        total_wait_adversarial_intent=family,
    )
    observation = case['observation_config']
    observation.update(feed_interval_minutes=0, feed_delay_minutes=0,
                       confirmation_delay_minutes=1, confirmation_silent=False,
                       feed_missing_resources=[], confirmation_missing_resources=[],
                       feed_delay_by_source_minute={})
    near = case['hospitals'][0]
    near['capacity']['ed_bed'] = 1

    if family in ('release_long_retry', 'release_notification_delay', 'periodic_release_long_retry'):
        case['hospitals'] = [near]
        near['initial_occupancy'] = [dict(until=6, demand={'ed_bed': 1})]
        case['response_environment'] = {'*': {'NEAR': dict(delay=1, silent=False)}}
        case['retry_minutes'] = 12
        # The last report is explicitly from the previous minute. It is an
        # older observation, not a claim that the t=0 physical bed is empty.
        observation['initial_reports'] = [dict(
            hospital='NEAR', observed_at=-1,
            resources={resource: dict(capacity=count, used=0)
                       for resource, count in near['capacity'].items()})]
        delay = 3 if family == 'release_notification_delay' else 0
        case['resource_update_config'] = _resource_update_contract(case, delay_minutes=delay)
        case['experiment'].update(
            physical_release_minute=6, notification_delay_minutes=delay,
            earliest_release_notification_minute=6 + delay,
            initial_report_observation_minute=-1,
            first_patient_reply_minute=1, declared_retry_minutes=12,
            delay_intent='known_current_release_must_not_be_predicted',
        )
        if family == 'periodic_release_long_retry':
            observation.update(feed_interval_minutes=1, feed_delay_minutes=2)
            case['experiment'].update(
                periodic_feed_interval_minutes=1, periodic_feed_delay_minutes=2,
                first_full_report_delivery_minute=2,
                first_post_release_feed_delivery_minute=8,
                delay_intent='same_received_periodic_report_with_or_without_event_review',
            )

    elif family == 'release_before_offer_expiry':
        case['response_environment'] = {'*': {
            'NEAR': dict(delay=1, silent=False), 'FAR': dict(delay=5, silent=False)}}
        case['exogenous_events'] = [dict(type='resource_unavailable', t=2, until=4,
                                      hospital='NEAR', resource='ed_bed')]
        case['accept_ttl_minutes'] = 5
        case['experiment'].update(
            first_near_offer_minute=1, first_near_offer_expiry_minute=6,
            physical_outage_minute=2, physical_release_minute=4,
            earliest_wait_decision_minute=3,
            first_far_offer_minute=5,
            delay_intent='current_negative_and_recovery_reports_with_valid_offer',
        )

    elif family == 'intrinsic_single_hospital_queue':
        source = build_stable_c1_scenarios(seed, ['fresh_control'])[0]
        case['patients'] = deepcopy(source['patients'])
        for patient in case['patients']:
            patient.update(required_capabilities=[BASIC], resource_demands={'ed_bed': 1})
        case['hospitals'] = [near]
        case['response_environment'] = {'*': {'NEAR': dict(delay=1, silent=False)}}
        observation['feed_interval_minutes'] = 1
        case['max_minutes'] = 120
        case['experiment'].update(
            patient_count=3, only_hospital_id='NEAR', ed_bed_capacity=1,
            delay_intent='one_bed_physical_queue_cannot_be_removed_by_tie_order',
        )
    else:
        raise AssertionError('unhandled total-wait family')
    return case


def build_total_wait_scenarios(seed, families=None):
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
            raise ValueError('unknown total-wait family')
        if len(set(selected)) != len(selected):
            raise ValueError('duplicate total-wait family')

    from .map_replay import validate_scenario

    originals = {case['family']: case for case in build_stable_c1_scenarios(
        seed, [family for family in selected if family in ORIGINAL_FAMILIES])}
    cases = []
    for family in selected:
        case = originals[family] if family in originals else _adversarial(seed, family)
        case.setdefault('resource_update_config', _resource_update_contract(case))
        case.update(id=f'total-wait-{family}-{seed}', family=family,
                    total_wait_family=family)
        case['experiment'].update(
            total_wait_family=family, patient_count=len(case['patients']),
            comparison_scope='same_truth_environment_current_resource_change_and_event_review',
            prior_stable_c1_family=family if family in originals else 'fresh_control',
        )
        case['assumptions'].extend(_ASSUMPTIONS)
        validate_scenario(case)
        cases.append(case)
    return deepcopy(cases)
