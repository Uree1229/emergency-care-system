"""Adversarial inputs for registered-demand-aware first inquiry selection.

The 56 preceding joint-policy scenarios remain complete value-for-value copies.
Eleven additional environments exercise alternatives that are structurally
possible but can be silent, late, unverified, distant or expired. Potential
service times and future arrivals remain environment inputs, never observations
that an inquiry selector may consume.

Development 33101-33103 and validation 34101-34106 are reserved. Structural
tests use only small seeds 7 and 8 and do not execute a replay or a holdout.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import random

from .joint_policy_scenarios import (
    FAMILIES as ORIGINAL_FAMILIES,
    _validate_extra_inputs,
    make_scenario as make_joint_policy_scenario,
)
from .map_replay import validate_scenario
from .partial_information_scenarios import BASIC, SCARCE, SCENE


DEV_SEEDS = (33101, 33102, 33103)
DEVELOPMENT_SEEDS = DEV_SEEDS
VALIDATION_SEEDS = (34101, 34102, 34103, 34104, 34105, 34106)
ADDITIONAL_FAMILIES = (
    'scarce_alternative_silent',
    'scarce_alternative_late_deadline',
    'scarce_alternative_unknown',
    'scarce_alternative_stale',
    'scarce_distant_accepted_alternative',
    'scarce_late_request',
    'scarce_priority_reversal',
    'scarce_single_candidate_competition',
    'scarce_contact_concurrency_limits',
    'scarce_alternative_offer_expiry',
    'scarce_reported_bed_shortage',
)
FAMILIES = ORIGINAL_FAMILIES + ADDITIONAL_FAMILIES

_LABELS = {
    'scarce_alternative_silent': '희소 경합·유연한 환자의 대안 병원 무응답',
    'scarce_alternative_late_deadline': '희소 경합·대안 회신이 원 문의 기한 뒤 완료',
    'scarce_alternative_unknown': '희소 경합·대안 병상 수량 미확인',
    'scarce_alternative_stale': '희소 경합·대안 병상 보고가 11분 오래됨',
    'scarce_distant_accepted_alternative': '희소 경합·먼 대안의 빠른 수용과 물리 이동 절충',
    'scarce_late_request': '유연한 환자 문의 뒤 희소 환자 발생·미래 수요 사용 금지',
    'scarce_priority_reversal': '유연한 환자가 더 높은 우선순위·강제 양보 금지',
    'scarce_single_candidate_competition': '두 환자 모두 단일 병원만 가능·대안 없음',
    'scarce_contact_concurrency_limits': '후보 열두 곳·미해결 문의와 접촉 예산 한도',
    'scarce_alternative_offer_expiry': '희소 경합·대안 수용 1분 만료·만료 부활 금지',
    'scarce_reported_bed_shortage': '희소 병상 현재 부족 보고·실제 초기 점유',
}
_ASSUMPTIONS = (
    '병원·환자·역량·병상·회신·처치시간은 합성 입력이며 실제 임상 성과가 아니다.',
    '현재 실제 등록된 환자 요청·수신 자원 보고·원 문의 원장만 문의 선택 근거로 허용한다.',
    '미발생 환자·잠재 회신 지연·무응답 여부·FIFO 완료시각·미래 점유 해소는 정책 입력이 아니다.',
    '시간은 실제 분, 문의 재평가는 15초이며 이동·처치·전원 준비 3분을 축소하지 않는다.',
    '추가 자원 변화 보고 없이 기존 초기 보고·주기 피드·재확인만 사용한다.',
    '구조적으로 가능한 대안과 현재 유효 수용을 구분하고 원 문의 기한·수용 만료를 유지한다.',
    '단일 후보 보호의 전체 이득과 유연한 환자의 개인 지연을 함께 공개한다.',
    '먼 대안 유효 수용·예산 포화·만료 상태는 분기 원시 기록에서 실제 도달 여부를 따로 확인한다.',
)


def _rng(seed, family, *keys):
    material = '\x1f'.join(map(str, (seed, family, *keys))).encode('utf-8')
    return random.Random(int.from_bytes(sha256(material).digest(), 'big'))


def _report(hospital, *, observed_at=0, missing=False, full=False):
    return dict(hospital=hospital['id'], observed_at=observed_at,
        resources={resource: None if missing and resource == 'ed_bed' else dict(
            capacity=capacity, used=capacity if full else 0)
            for resource, capacity in hospital['capacity'].items()})


def _new_case(seed, family):
    # Only a new detached environment is changed; the inherited builder and
    # its input values are never modified or patched.
    case = make_joint_policy_scenario(seed, 'scarce_options_staggered_replies')
    case.update(id=f'scarce-delay-{family}-{seed}', family=family,
                scarce_delay_family=family, label=_LABELS[family], seed=seed)
    case.pop('joint_policy_family', None)
    hospitals = deepcopy(case['hospitals'][:2])
    patients = deepcopy(case['patients'])
    for patient in patients:
        patient['treatment_minutes'] = _rng(seed, family, patient['id'], 'treatment').randint(12, 18)
    matrix = {'*': {'H01': dict(delay=.25, silent=False),
                    'H02': dict(delay=.5, silent=False)}}
    queue = dict(enabled=True, workers=1, background_jobs=[])
    observation = dict(feed_interval_minutes=1, feed_delay_minutes=0,
        feed_missing_resources=[], confirmation_delay_minutes=1,
        confirmation_silent=False, confirmation_missing_resources=[],
        feed_delay_by_source_minute={})
    details = dict(flexible_patient='P01', limited_patient='P02',
        limited_patient_only_hospital='H01', physical_beds_per_hospital=1)
    limit, ttl = 60, 10

    if family == 'scarce_alternative_silent':
        matrix['*']['H02']['silent'] = True
        details.update(intent='a_structural_alternative_is_not_a_received_acceptance')

    elif family == 'scarce_alternative_late_deadline':
        queue['background_jobs'] = [dict(t=0, hospital='H02', service_minutes=3)]
        matrix['*']['H02']['delay'] = .25
        details.update(intent='original_deadline_survives_private_fifo_work',
                       private_background_minutes=3, original_timeout_minutes=3)

    elif family == 'scarce_alternative_unknown':
        observation.update(feed_interval_minutes=0, confirmation_silent=True,
            initial_reports=[_report(hospitals[0]), _report(hospitals[1], missing=True)])
        details.update(intent='unverified_alternative_counts_cannot_be_filled_as_free',
                       unverified_resource='ed_bed', unverified_hospital='H02')

    elif family == 'scarce_alternative_stale':
        observation.update(feed_interval_minutes=0, confirmation_silent=True,
            initial_reports=[_report(hospitals[0]), _report(hospitals[1], observed_at=-11)])
        details.update(intent='stale_alternative_counts_remain_confirmation_targets',
                       stale_source_minute=-11, stale_hospital='H02')

    elif family == 'scarce_distant_accepted_alternative':
        hospitals[1].update(lat=round(SCENE[0] + .065 +
            _rng(seed, family, 'distance').uniform(0, .002), 6), lon=SCENE[1])
        matrix['*'].update(H01=dict(delay=.75, silent=False),
                           H02=dict(delay=.25, silent=False))
        details.update(intent='a_fast_distant_acceptance_has_real_route_cost',
                       distant_hospital='H02', state_requires_raw_branch_verification=True)

    elif family == 'scarce_late_request':
        for key in ('occur_t', 'dispatch_t', 'ambulance_arrive_t', 'search_start_t'):
            patients[1][key] = 1
        details.update(intent='a_patient_first_observed_at_one_cannot_change_time_zero_inquiry',
                       later_request_minute=1)

    elif family == 'scarce_priority_reversal':
        patients[0]['priority'], patients[1]['priority'] = 4, 2
        details.update(intent='lower_priority_limited_demand_cannot_force_higher_priority_to_yield')

    elif family == 'scarce_single_candidate_competition':
        hospitals = hospitals[:1]
        matrix = {'*': {'H01': dict(delay=.25, silent=False)}}
        patients[0]['required_capabilities'] = [BASIC, SCARCE]
        patients[0]['resource_demands'] = dict(ed_bed=1, special_team=1)
        details.update(intent='no_compatible_alternative_may_be_invented',
                       both_patients_single_candidate=True)

    elif family == 'scarce_contact_concurrency_limits':
        template = hospitals[1]
        for index in range(2, 12):
            hospital = deepcopy(template)
            hospital.update(id=f'H{index + 1:02d}',
                name=f'합성 접촉한도 병원 {index + 1}',
                lat=round(SCENE[0] + .01 + .009 * index, 6))
            hospitals.append(hospital)
        matrix = {'*': {hospital['id']: dict(delay=.25, silent=hospital['id'] != 'H12')
                         for hospital in hospitals}}
        details.update(intent='candidate_pressure_cannot_bypass_concurrency_or_episode_budget',
                       structural_candidates_for_flexible=12, episode_contact_limit=10,
                       state_requires_raw_branch_verification=True)

    elif family == 'scarce_alternative_offer_expiry':
        matrix['*'].update(H01=dict(delay=.75, silent=False),
                           H02=dict(delay=.25, silent=False))
        ttl = 1
        details.update(intent='short_offer_lifetime_cannot_be_renewed_by_protection',
                       offer_lifetime_minutes=1, state_requires_raw_branch_verification=True)

    elif family == 'scarce_reported_bed_shortage':
        hospitals[0]['initial_occupancy'] = [dict(until=4,
            demand=deepcopy(hospitals[0]['capacity']))]
        details.update(intent='a_fresh_known_shortage_cannot_reserve_an_available_bed',
                       actual_initial_occupancy_until=4)

    else:
        raise AssertionError('unhandled scarce-delay family')

    case.update(hospitals=hospitals, patients=patients, exogenous_events=[],
        response_environment={}, response_matrix=matrix, inquiry_queue=queue,
        observation_config=observation, max_minutes=limit,
        timeout_minutes=3, retry_minutes=3, accept_ttl_minutes=ttl,
        transfer_preparation_minutes=3, initial_inquiry_history=[],
        travel_error_range=[1.0, 1.0], assumptions=list(_ASSUMPTIONS),
        experiment=dict(seed=seed, patient_count=len(patients), scarce_delay_family=family,
            comparison_scope='same_information_registered_demand_first_inquiry',
            clinical_values='synthetic', identity_source='synthetic',
            routing='synthetic_fixed_coordinates', timing_unit='real_minutes',
            review_resolution_seconds=15, history_timing_unit='relative_seconds',
            additional_resource_change_channel_enabled=False, **details))
    validate_scenario(case)
    _validate_extra_inputs(case)
    return case


def make_scenario(seed, family):
    """Return one detached environment without changing inherited 56 inputs."""
    if type(seed) is not int or seed < 0:
        raise ValueError('seed must be a nonnegative integer')
    if not isinstance(family, str) or family not in FAMILIES:
        raise ValueError('unknown scarce-delay family')
    if family in ORIGINAL_FAMILIES:
        return deepcopy(make_joint_policy_scenario(seed, family))
    return deepcopy(_new_case(seed, family))


def build_scarce_delay_scenarios(seed, families=None):
    """Build all 67 cases, or a detached ordered distinct family selection."""
    if type(seed) is not int or seed < 0:
        raise ValueError('seed must be a nonnegative integer')
    if families is None:
        selected = list(FAMILIES)
    else:
        if isinstance(families, (str, bytes)):
            raise ValueError('families must be a distinct iterable of family names')
        try:
            selected = list(families)
        except TypeError as exc:
            raise ValueError('families must be a distinct iterable of family names') from exc
        if any(not isinstance(family, str) or family not in FAMILIES for family in selected) \
                or len(selected) != len(set(selected)):
            raise ValueError('unknown or duplicate scarce-delay family')
    return [make_scenario(seed, family) for family in selected]
