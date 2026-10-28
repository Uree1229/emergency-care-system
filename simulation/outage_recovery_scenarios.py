"""Fresh same-information cases for preserving unfinished patient inquiries.

The thirty-nine frozen existing-information environments keep their physical
events, observations, response times and arrival preparation unchanged. Nine
new cases exercise retaining an original inquiry or an unexpired patient offer
without using future events, adding resource reports, or changing movement.

Development seeds: 29101-29103. Validation seeds: 30101-30106.
Structural tests use only small seeds 7 and 8.
"""
from __future__ import annotations

from copy import deepcopy

from .existing_info_scenarios import (
    BASIC, SCARCE, FAMILIES as ORIGINAL_FAMILIES, build_existing_info_scenarios,
)


DEVELOPMENT_SEEDS = (29101, 29102, 29103)
VALIDATION_SEEDS = (30101, 30102, 30103, 30104, 30105, 30106)
ADVERSARIAL_FAMILIES = (
    'notify_before_backup_reply',
    'notify_after_backup_reply',
    'expired_backup_after_notify',
    'silent_backup_timeout',
    'late_backup_timeout',
    'competing_backup_capacity',
    'restore_before_failed_arrival',
    'unverified_backup_resources',
    'outage_at_arrival_boundary',
)
FAMILIES = ORIGINAL_FAMILIES + ADVERSARIAL_FAMILIES

_LABELS = {
    'notify_before_backup_reply': '기존 대체 문의 회신 전 변경 알림·원 문의 보존',
    'notify_after_backup_reply': '기존 대체 수용 회신 후 변경 알림·유효 응답 보존',
    'expired_backup_after_notify': '변경 알림 전에 만료된 대체 수용·재사용 금지',
    'silent_backup_timeout': '기존 대체 문의 무응답·원래 시간 초과 유지',
    'late_backup_timeout': '병원 문의 대기열의 늦은 회신·시간 초과 부활 금지',
    'competing_backup_capacity': '영향 환자 둘의 대체 병상 하나·공동 경합 유지',
    'restore_before_failed_arrival': '도착 전 원 병원 빠른 복구·기존 이동 유지',
    'unverified_backup_resources': '대체 병원 수량 노후·누락·수용만으로 확정 금지',
    'outage_at_arrival_boundary': '도착과 병상 중단 동시 발생·도착 후 전원 절차 유지',
}
_ASSUMPTIONS = (
    '돌발 중단 회복은 별도 합성 실험이며 운영 서버·API·팀원 화면을 변경하지 않는다.',
    '모든 분기에서 추가 자원 변화 보고를 끄고 기존 초기 보고·주기 피드·재확인 계약을 유지한다.',
    '보존하는 것은 이미 전송한 환자별 문의와 실제 수신한 유효 수용이며 새 수량 보고가 아니다.',
    '환자별 변경 알림은 대상 환자·병원·자원·원인만 전달하며 미래 복구 시각이나 수량을 전달하지 않는다.',
    '기존 이동과 도착 후 전원 준비시간을 유지하며 미래 중단을 미리 알거나 알림만으로 정지하지 않는다.',
    '기존 시간 초과·수용 만료·수량 확인·공동 자원 경합·최종 자원 충돌 검사를 유지한다.',
)


def _new_case(seed, family):
    case = build_existing_info_scenarios(seed, ['future_outage_after_early_offer'])[0]
    case['label'] = _LABELS[family]
    case['experiment'] = dict(
        seed=seed, patient_count=1, clinical_values='synthetic',
        identity_source='synthetic', routing='synthetic_fixed_coordinates',
        timing_unit='integer_minute',
        common_inquiry_settings=dict(k=3, wait=2, notify=True),
        outage_recovery_adversarial_intent=family,
        additional_resource_change_channel_enabled=False,
        original_near_reply_minute=1, original_far_reply_minute=5,
        physical_outage_minute=2, physical_restore_minute=10,
        transfer_preparation_minutes=3,
    )
    # Make the old default explicit only in new cases. Inherited cases below
    # retain their exact prior representation and timings.
    case['transfer_preparation_minutes'] = 3

    if family == 'notify_before_backup_reply':
        case['experiment']['intent'] = 'original_unfinished_inquiry_remains_a_pending_job'

    elif family == 'notify_after_backup_reply':
        case['response_environment']['*']['FAR']['delay'] = 2
        case['exogenous_events'][0].update(t=3, until=12)
        case['experiment'].update(original_far_reply_minute=2,
            physical_outage_minute=3, physical_restore_minute=12,
            intent='received_unexpired_patient_offer_remains_available_for_normal_reassessment')

    elif family == 'expired_backup_after_notify':
        # Static geometry extends the original NEAR leg so the t=4 alert is
        # received before arrival. Neither its duration nor the outage enters
        # the policy as future clinical information.
        case['hospitals'][0]['lat'] = 35.195
        case['response_environment']['*']['FAR']['delay'] = 2
        case['accept_ttl_minutes'] = 1
        case['exogenous_events'][0].update(t=4, until=12)
        case['experiment'].update(original_far_reply_minute=2,
            original_far_offer_expiry_minute=3,
            physical_outage_minute=4, physical_restore_minute=12,
            intent='expired_backup_requires_a_new_patient_acceptance')

    elif family == 'silent_backup_timeout':
        case['timeout_minutes'] = 4
        case['response_environment']['*']['FAR'].update(delay=5, silent=True)
        case['experiment'].update(original_far_reply_minute=None,
            original_far_timeout_minute=4,
            intent='silence_preserves_original_timeout_without_an_invented_offer')

    elif family == 'late_backup_timeout':
        case['hospitals'][0]['lat'] = 35.195
        case['timeout_minutes'] = 4
        case['response_environment']['*']['FAR']['delay'] = 3
        case['inquiry_queue'] = dict(enabled=True, workers=1,
            background_jobs=[dict(t=0, hospital='FAR', service_minutes=4)])
        case['experiment'].update(original_far_reply_minute=None,
            original_far_timeout_minute=4, original_far_late_reply_minute=7,
            original_far_service_minutes=3, background_far_work_minutes=4,
            intent='finished_hospital_work_after_timeout_cannot_resurrect_the_original_inquiry')

    elif family == 'competing_backup_capacity':
        source = build_existing_info_scenarios(seed, ['ample_cohort_dominant_offer'])[0]
        case['patients'] = deepcopy(source['patients'])
        case['hospitals'][0]['capacity']['ed_bed'] = 2
        case['hospitals'][1]['capacity']['ed_bed'] = 1
        case['exogenous_events'] = [dict(type='resource_unavailable', t=2, until=10,
                                        hospital='NEAR', resource='ed_bed') for _ in range(2)]
        case['experiment'].update(patient_count=2, original_near_beds=2,
            backup_far_beds=1, affected_registered_patients=2,
            intent='two_valid_patient_offers_do_not_create_two_physical_backup_beds')

    elif family == 'restore_before_failed_arrival':
        case['exogenous_events'][0]['until'] = 3
        case['experiment'].update(physical_restore_minute=3,
            intent='received_alert_must_not_force_pause_or_abandon_a_leg_that_can_still_succeed')

    elif family == 'unverified_backup_resources':
        patient = case['patients'][0]
        patient.update(required_capabilities=[BASIC, SCARCE],
                       resource_demands={'ed_bed': 1, 'special_team': 1})
        far = case['hospitals'][1]
        far['capabilities'] = [BASIC, SCARCE]
        near = case['hospitals'][0]
        case['observation_config'].update(feed_interval_minutes=0,
            confirmation_missing_resources=['ed_bed', 'special_team'],
            initial_reports=[
                dict(hospital='NEAR', observed_at=0,
                     resources={r: dict(capacity=q, used=0)
                                for r, q in near['capacity'].items()}),
                dict(hospital='FAR', observed_at=-11,
                     resources={'ed_bed': dict(capacity=far['capacity']['ed_bed'], used=0),
                                'special_team': None}),
            ])
        # The disabled channel retains the same missing mask. Its existence
        # in the input cannot act as a new reporting source in any branch.
        case['resource_update_config']['missing_resources'] = ['ed_bed', 'special_team']
        case['experiment'].update(backup_ed_bed_observed_at=-11,
            backup_special_team_reported=False,
            intent='patient_acceptance_does_not_refresh_stale_or_missing_resource_quantities')

    elif family == 'outage_at_arrival_boundary':
        # This base geometry realizes a three-minute NEAR leg. An early
        # departure at t=1 reaches NEAR at t=4, the physical change minute.
        # The old notification contract excludes arrive_t <= t; the ordinary
        # arrival_unable event remains the first failed-arrival evidence.
        case['exogenous_events'][0].update(t=4, until=12)
        case['experiment'].update(physical_outage_minute=4, physical_restore_minute=12,
            early_near_planned_arrival_minute=4,
            intent='failed_arrival_is_observed_without_a_prearrival_patient_notification')

    else:
        raise AssertionError('unhandled outage-recovery family')
    return case


def build_outage_recovery_scenarios(seed, families=None):
    """Return detached environments with independent family selection order."""
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
            raise ValueError('unknown outage-recovery family')
        if len(set(selected)) != len(selected):
            raise ValueError('duplicate outage-recovery family')

    from .map_replay import validate_scenario

    originals = {case['family']: case for case in build_existing_info_scenarios(
        seed, [family for family in selected if family in ORIGINAL_FAMILIES])}
    cases = []
    for family in selected:
        case = originals[family] if family in originals else _new_case(seed, family)
        case.update(id=f'outage-recovery-{family}-{seed}', family=family,
                    outage_recovery_family=family)
        case['experiment'].update(outage_recovery_family=family,
            patient_count=len(case['patients']),
            comparison_scope='same_existing_information_preserved_patient_inquiries_and_live_offers',
            additional_resource_change_channel_enabled=False,
            prior_existing_info_family=family if family in originals
                else 'future_outage_after_early_offer')
        case['assumptions'].extend(_ASSUMPTIONS)
        validate_scenario(case)
        cases.append(case)
    return deepcopy(cases)
