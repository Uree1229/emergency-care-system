"""Same-information inquiry/allocation inputs at a 15-second review resolution.

The preceding 48 outage-recovery cases are returned byte-for-value unchanged.
Eight additional cases combine inquiry delay, hospital-local FIFO work and
physical resource competition. Their times remain real minutes; only the
``response_matrix`` service delays may use quarter-minute values. Historical
inquiry timestamps are relative real seconds, before the common time origin.

Development seeds 31101-31103 and validation seeds 32101-32106 are reserved.
Scenario tests use only small seeds 7 and 8, without executing either holdout.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import math
import random

from .map_replay import validate_scenario
from .outage_recovery_scenarios import (
    FAMILIES as ORIGINAL_FAMILIES,
    build_outage_recovery_scenarios,
)
from .partial_information_scenarios import BASIC, SCARCE, SCENE


DEV_SEEDS = (31101, 31102, 31103)
DEVELOPMENT_SEEDS = DEV_SEEDS
VALIDATION_SEEDS = (32101, 32102, 32103, 32104, 32105, 32106)
ADDITIONAL_FAMILIES = (
    'scarce_options_staggered_replies',
    'silent_first_options',
    'shared_fifo_burst',
    'late_reply_deadline',
    'recent_history_reversal',
    'sparse_history_window_boundary',
    'selection_arrival_tradeoff',
    'outage_during_v3_hedge',
)
FAMILIES = ORIGINAL_FAMILIES + ADDITIONAL_FAMILIES

_LABELS = {
    'scarce_options_staggered_replies': '회신 시차와 희소 선택지·환자 두 명의 공동 경합',
    'silent_first_options': '가까운 두 후보 무응답·남은 후보의 수용 확인',
    'shared_fifo_burst': '환자 여덟 명 동시 발생·병원별 검토자 한 명의 FIFO 혼잡',
    'late_reply_deadline': '원래 문의 기한 뒤 검토 완료·늦은 회신 부활 금지',
    'recent_history_reversal': '최근 이력 안에서 회신 경향 반전·과거 속도 의존 점검',
    'sparse_history_window_boundary': '이력 표본 부족과 15분 창 경계·관측 근거 분리',
    'selection_arrival_tradeoff': '먼 병원 빠른 회신·빠른 선정과 빠른 도착의 절충',
    'outage_during_v3_hedge': '짧은 수용 회신 뒤 병상 중단·공통 알림과 회복',
}
_ASSUMPTIONS = (
    '병원·환자·역량·자원·회신·처치시간은 합성 입력이며 임상 성과를 뜻하지 않는다.',
    '시간은 실제 분 단위이며 문의 재평가만 15초 간격이다. 이동·준비·자원 점유시간을 축소하지 않는다.',
    '병원별 FIFO 검토 작업은 정책과 무관한 잠재 환경이다. 미응답·취소 후에도 이미 제출한 작업은 계속된다.',
    '초기 문의 이력은 공통 시작 전 실제 수신한 과거 회신만 나타내며 상대 초 단위이다.',
    '회신 예정시각·검토자 수·잠재 작업시간·미래 병상 복구시각은 문의·배정 정책의 입력이 아니다.',
    '환자별 수용과 자원 수량을 구분하고 원래 문의 기한·수용 만료·최종 충돌 검사를 유지한다.',
    '추가 자원 변화 보고는 사용하지 않으며 초기 보고·주기 피드·기존 재확인만 공통으로 사용한다.',
    '선정·최종 도착·처치 시작과 미처치를 구분하여 평균 이득과 개인별 지연을 함께 평가한다.',
)


def _rng(seed, family, *keys):
    material = '\x1f'.join(map(str, (seed, family, *keys))).encode('utf-8')
    return random.Random(int.from_bytes(sha256(material).digest(), 'big'))


def _hospitals(seed, family):
    rows = []
    for index in range(4):
        offset = .01 + .009 * index
        jitter = 0 if index == 0 else _rng(seed, family, 'hospital', index).uniform(-.0005, .0005)
        rows.append(dict(
            id=f'H{index + 1:02d}', name=f'합성 공동실험 병원 {index + 1}',
            lat=round(SCENE[0] + offset + jitter, 6), lon=SCENE[1],
            capabilities=[BASIC], capacity={'ed_bed': 3, 'special_team': 1},
            identity_source='synthetic', clinical_data_source='synthetic',
        ))
    return rows


def _patient(seed, family, index=0, *, priority=4, scarce=False):
    pid = f'P{index + 1:02d}'
    return dict(
        id=pid, label=f'합성 공동실험 환자 {index + 1}',
        location=list(SCENE), ambulance_origin=list(SCENE),
        occur_t=0, dispatch_t=0, ambulance_arrive_t=0, search_start_t=0,
        priority=priority,
        treatment_minutes=_rng(seed, family, pid, 'treatment').randint(12, 18),
        required_capabilities=[BASIC, SCARCE] if scarce else [BASIC],
        resource_demands={'ed_bed': 1, 'special_team': 1} if scarce else {'ed_bed': 1},
        clinical_data_source='synthetic', dispatch_timing_source='synthetic_fixed',
    )


def _history(hospital, observed_times, delays):
    return [dict(hospital=hospital, patient=f'HISTORY-{hospital}-{index + 1}',
                 submitted_at=observed - delay, observed_at=observed,
                 outcome='reply')
            for index, (observed, delay) in enumerate(zip(observed_times, delays))]


def _new_case(seed, family):
    hospitals = _hospitals(seed, family)
    patients = [_patient(seed, family)]
    matrix = {'*': {h['id']: dict(delay=.5 + .25 * index, silent=False)
                    for index, h in enumerate(hospitals)}}
    queue = dict(enabled=True, workers=1, background_jobs=[])
    history, events, details = [], [], {}
    timeout, limit = 3, 60

    if family == 'scarce_options_staggered_replies':
        patients = [_patient(seed, family), _patient(seed, family, 1, scarce=True)]
        hospitals[0]['capabilities'] = [BASIC, SCARCE]
        for h in hospitals:
            h['capacity']['ed_bed'] = 1
        matrix['*'].update(H01=dict(delay=.25, silent=False),
                           H02=dict(delay=.5, silent=False))
        details.update(flexible_patient='P01', limited_patient='P02',
                       limited_patient_only_hospital='H01',
                       physical_beds_per_hospital=1,
                       intent='inquiry_timing_must_not_invent_a_second_scarce_bed')

    elif family == 'silent_first_options':
        matrix['*'].update(H01=dict(delay=.5, silent=True),
                           H02=dict(delay=.75, silent=True),
                           H03=dict(delay=.25, silent=False),
                           H04=dict(delay=.5, silent=False))
        details.update(silent_hospitals=['H01', 'H02'],
                       responsive_hospitals=['H03', 'H04'],
                       intent='bounded_expansion_requires_real_received_acceptance')

    elif family == 'shared_fifo_burst':
        patients = [_patient(seed, family, index, priority=(4, 2, 1)[index % 3])
                    for index in range(8)]
        # The same patient/hospital/attempt work is used in every policy branch.
        matrix = {p['id']: {
            h['id']: dict(delay=_rng(seed, family, p['id'], h['id'], 'service').choice(
                (.5, .75, 1)), silent=False) for h in hospitals} for p in patients}
        details.update(simultaneous_patients=8, workers_per_hospital=1,
                       physical_beds_per_hospital=3,
                       intent='submitted_work_retains_global_load_after_other_acceptance')

    elif family == 'late_reply_deadline':
        queue['background_jobs'] = [dict(t=0, hospital='H01', service_minutes=3)]
        matrix['*'].update(H01=dict(delay=.25, silent=False),
                           H02=dict(delay=.5, silent=False))
        limit = 30
        details.update(background_hospital='H01', background_work_minutes=3,
                       original_service_minutes=.25, original_timeout_minutes=3,
                       intent='hospital_work_can_finish_after_the_sender_deadline_without_reviving_an_offer')

    elif family == 'recent_history_reversal':
        history.extend(_history('H01', (-600, -540, -120, -60), (15, 15, 120, 135)))
        history.extend(_history('H02', (-600, -540, -120, -60), (120, 135, 15, 15)))
        matrix['*'].update(H01=dict(delay=2.25 + .25 * _rng(seed, family, 'slow').randint(0, 1), silent=False),
                           H02=dict(delay=.25, silent=False),
                           H03=dict(delay=.75, silent=False))
        details.update(formerly_fast_hospital='H01', currently_fast_hospital='H02',
                       all_history_within_seconds=900,
                       intent='a_short_window_still_contains_changed_recent_response_regimes')

    elif family == 'sparse_history_window_boundary':
        history.extend(_history('H01', (-30,), (15,)))
        history.extend(_history('H02', (-900, -899), (120, 120)))
        matrix['*'].update(H01=dict(delay=2, silent=False),
                           H02=dict(delay=.25, silent=False),
                           H03=dict(delay=.75, silent=False))
        details.update(single_sample_hospital='H01',
                       history_window_boundary_seconds=-900,
                       just_inside_window_seconds=-899,
                       intent='insufficient_samples_and_exact_window_boundary_cannot_create_future_evidence')

    elif family == 'selection_arrival_tradeoff':
        hospitals = hospitals[:2]
        matrix = {'*': {'H01': dict(delay=1.75, silent=False),
                        'H02': dict(delay=.25, silent=False)}}
        details.update(nearest_hospital='H01', fast_reply_hospital='H02',
                       intent='earlier_selection_can_still_have_a_later_physical_arrival')

    elif family == 'outage_during_v3_hedge':
        hospitals = hospitals[:3]
        for h in hospitals:
            h['capacity']['ed_bed'] = 1
        matrix = {'*': {'H01': dict(delay=.25, silent=False),
                        'H02': dict(delay=.5, silent=False),
                        'H03': dict(delay=.75, silent=False)}}
        events = [dict(type='resource_unavailable', t=1, until=10,
                       hospital='H01', resource='ed_bed')]
        details.update(physical_outage_minute=1, physical_restore_minute=10,
                       transfer_preparation_minutes=3,
                       intent='a_fast_offer_does_not_reveal_the_later_outage_or_its_recovery')

    else:
        raise AssertionError('unhandled joint-policy family')

    value = dict(
        id=f'joint-policy-{family}-{seed}', family=family, joint_policy_family=family,
        label=_LABELS[family], seed=seed, synthetic=True,
        hospitals=hospitals, patients=patients,
        exogenous_events=events, response_environment={}, response_matrix=matrix,
        inquiry_queue=queue, initial_inquiry_history=sorted(
            history, key=lambda row: (row['observed_at'], row['submitted_at'],
                                      row['hospital'], row['patient'])),
        observation_config=dict(feed_interval_minutes=1, feed_delay_minutes=0,
            feed_missing_resources=[], confirmation_delay_minutes=1,
            confirmation_silent=False, confirmation_missing_resources=[],
            feed_delay_by_source_minute={}),
        resource_update_config=dict(delay_minutes=0, missing_resources=[], silent=False),
        max_minutes=limit, timeout_minutes=timeout, retry_minutes=3,
        accept_ttl_minutes=10, transfer_preparation_minutes=3,
        nonresponse=0, travel_error_range=[1.0, 1.0], assumptions=list(_ASSUMPTIONS),
        experiment=dict(seed=seed, patient_count=len(patients),
            joint_policy_family=family, comparison_scope='same_information_inquiry_and_allocation_cross_comparison',
            clinical_values='synthetic', identity_source='synthetic',
            routing='synthetic_fixed_coordinates', timing_unit='real_minutes',
            review_resolution_seconds=15, history_timing_unit='relative_seconds',
            additional_resource_change_channel_enabled=False, **details),
    )
    validate_scenario(value)
    _validate_extra_inputs(value)
    return value


def _validate_extra_inputs(case):
    hospitals = {row['id'] for row in case['hospitals']}
    patients = {row['id'] for row in case['patients']}
    for pid, entries in case['response_matrix'].items():
        if pid != '*' and pid not in patients:
            raise ValueError('unknown response-matrix patient')
        for hid, entry in entries.items():
            if hid not in hospitals:
                raise ValueError('unknown response-matrix hospital')
            rows = entry.get('attempts', [entry])
            for row in rows:
                delay = row.get('delay')
                if type(delay) not in (int, float) or not math.isfinite(delay) or delay <= 0 \
                        or not math.isclose(delay * 4, round(delay * 4)):
                    raise ValueError('response delays must be positive quarter minutes')
                if type(row.get('silent')) is not bool:
                    raise ValueError('response silence must be explicit boolean')
    for row in case['initial_inquiry_history']:
        if row['hospital'] not in hospitals or not isinstance(row['patient'], str):
            raise ValueError('invalid historical inquiry identity')
        sent, observed = row['submitted_at'], row['observed_at']
        if any(type(t) not in (int, float) or not math.isfinite(t) for t in (sent, observed)) \
                or not sent <= observed <= 0:
            raise ValueError('initial history must be received before the common origin')
        if row['outcome'] not in ('reply', 'accept', 'decline', 'timeout'):
            raise ValueError('invalid historical inquiry outcome')


def make_scenario(seed, family):
    """Build one detached case; inherited cases keep their complete old value."""
    if type(seed) is not int or seed < 0:
        raise ValueError('seed must be a nonnegative integer')
    if not isinstance(family, str) or family not in FAMILIES:
        raise ValueError('unknown joint-policy family')
    if family in ORIGINAL_FAMILIES:
        return deepcopy(build_outage_recovery_scenarios(seed, [family])[0])
    return deepcopy(_new_case(seed, family))


def build_joint_policy_scenarios(seed, families=None):
    """Build all 56 cases, or an independently ordered distinct subset."""
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
            raise ValueError('unknown or duplicate joint-policy family')
    return [make_scenario(seed, family) for family in selected]
