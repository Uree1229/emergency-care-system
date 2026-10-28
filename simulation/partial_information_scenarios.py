"""Synthetic resource-observation cases for a separate information experiment.

The transport truth is kept in the standard replay fields. Reporting delay,
missing fields and confirmation behavior are separate observation_config
inputs to the new replay subclass. Every perfect/observed comparison must use
the same returned truth case; a missing report never changes real capacity.

Development seeds 17101-17103 and validation seeds 18101-18106 are reserved.
Structural tests should use small seeds such as 7, rather than these holdouts.
"""
from __future__ import annotations

from copy import deepcopy

from .sim import rng_for


DEVELOPMENT_SEEDS = (17101, 17102, 17103)
VALIDATION_SEEDS = (18101, 18102, 18103, 18104, 18105, 18106)
FAMILIES = (
    'fresh_control',
    'missing_recheck',
    'missing_unresolved',
    'stale_shortage_recovery',
    'delayed_optimistic_change',
    'delayed_pessimistic_recovery',
    'partial_team_missing',
    'out_of_order',
)
RESOURCE_KEYS = ('ed_bed', 'special_team')
BASIC = '합성 기본진료'
SCARCE = '합성 희소진료'
SCENE = (35.17, 126.85)

_LABELS = {
    'fresh_control': '지연·누락 없는 자원 관측 대조',
    'missing_recheck': '자원 보고 없음·재확인으로 해소',
    'missing_unresolved': '자원 보고 없음·재확인도 무응답',
    'stale_shortage_recovery': '오래된 부족 보고·현재 자원 재확인',
    'delayed_optimistic_change': '수용 후 자원 변화·낙관적 보고 지연',
    'delayed_pessimistic_recovery': '자원 복구·비관적 보고 지연',
    'partial_team_missing': '병상 정보 있음·전문팀 정보만 누락',
    'out_of_order': '이전 보고가 최신 보고보다 늦게 도착',
}
_ASSUMPTIONS = (
    '병원·좌표·환자·자원·회신·처치시간은 전부 합성 설정이며 실제 기관이나 개인정보를 사용하지 않는다.',
    '경로는 기존 합성 거리·시간 추정이다. 외부 API·실시간 교통·구급차 우선 통행을 사용하지 않는다.',
    '모든 시간·지연·유효기간은 정수 분 단위이다. 우선순위 4·2·1은 임상 중증도가 아닌 실험 가중치다.',
    'truth 자원과 observation_config의 보고 상태는 구분한다. None·보고 없음은 실제 용량 0을 뜻하지 않는다.',
    '같은 사례의 완전 관측·불완전 관측 분기는 동일한 잠재 환자·문의 회신·외생 자원 변화·이동 조건을 사용한다.',
    '문의 3곳·첫 수용 뒤 추가 대기 2분·변경 알림을 고정한다. V3 문의 정책이나 실제 운영 서버를 변경하지 않는다.',
    '보고되지 않은 상태·미래 보고의 도착시각·복구 예정시각·미래 회신은 정책 입력이 아니다.',
    '재확인 보고는 환경이 관측 시점의 실제 상태로 생성하며, 실제 확정은 별도의 충돌 검사 결과로 기록한다.',
)


def _initial_report(hospital, *, observed_at=0, full=False, missing=()):
    """Construct a report, not a mutation of the hospital truth ledger."""
    return dict(
        hospital=hospital['id'], observed_at=observed_at,
        resources={resource: None if resource in missing else dict(
            capacity=quantity, used=quantity if full else 0)
            for resource, quantity in hospital['capacity'].items()},
    )


def _build(seed, family):
    random = rng_for(seed, 'partial_information_scenario', family)
    duration = random.randint(24, 33)
    hospitals = [
        dict(id='NEAR', name='합성 병원 NEAR', lat=35.18, lon=126.85,
             capabilities=[BASIC, SCARCE], capacity={'ed_bed': 2, 'special_team': 1},
             identity_source='synthetic', clinical_data_source='synthetic'),
        dict(id='FAR', name='합성 병원 FAR', lat=35.205 + random.uniform(0, .006), lon=126.85,
             capabilities=[BASIC], capacity={'ed_bed': 2, 'special_team': 1},
             identity_source='synthetic', clinical_data_source='synthetic'),
    ]
    common = dict(
        location=list(SCENE), occur_t=0, dispatch_t=0,
        ambulance_origin=list(SCENE), ambulance_arrive_t=0, search_start_t=0,
        priority=4, treatment_minutes=duration, clinical_data_source='synthetic',
        dispatch_timing_source='synthetic_fixed',
    )
    patients = [
        dict(deepcopy(common), id='P01', label='합성 일반 환자 1',
             required_capabilities=[BASIC], resource_demands={'ed_bed': 1}),
        dict(deepcopy(common), id='P02', label='합성 일반 환자 2',
             required_capabilities=[BASIC], resource_demands={'ed_bed': 1}),
        dict(deepcopy(common), id='P03', label='합성 전문팀 필요 환자',
             required_capabilities=[BASIC, SCARCE],
             resource_demands={'ed_bed': 1, 'special_team': 1}),
    ]
    observation = dict(
        feed_interval_minutes=1, feed_delay_minutes=0,
        feed_missing_resources=[], confirmation_delay_minutes=1,
        confirmation_silent=False, confirmation_missing_resources=[],
        feed_delay_by_source_minute={},
    )
    replies = {'NEAR': {'delay': 1, 'silent': False},
               'FAR': {'delay': 1, 'silent': False}}
    events = []
    extra = dict(
        flexible_patient_ids=['P01', 'P02'], team_patient_id='P03',
        scarce_hospital_id='NEAR', alternative_hospital_id='FAR',
    )

    if family in ('missing_recheck', 'missing_unresolved'):
        observation.update(feed_interval_minutes=0, initial_reports=[])
        observation['confirmation_silent'] = family == 'missing_unresolved'

    elif family == 'stale_shortage_recovery':
        observation.update(feed_interval_minutes=0, initial_reports=[
            _initial_report(hospitals[0], observed_at=-11, full=True),
            _initial_report(hospitals[1]),
        ])
        extra.update(stale_source_minute=-11, near_truth_initial_used=0)

    elif family == 'delayed_optimistic_change':
        # A pending FAR reply keeps the first NEAR acceptance from becoming an
        # immediate decision. The team disappears at t=2, between reply t=1
        # and the shared first-acceptance wait deadline t=3.
        patients[2]['required_capabilities'] = [BASIC]
        observation.update(feed_interval_minutes=1, feed_delay_minutes=5)
        replies['FAR']['delay'] = 5
        events = [dict(type='resource_unavailable', t=2, until=35,
                       hospital='NEAR', resource='special_team')]
        extra.update(first_near_reply_minute=1, earliest_wait_decision_minute=3,
                     resource_change_minute=2, confirmation_can_reveal_change=True)

    elif family == 'delayed_pessimistic_recovery':
        # The numeric resource report starts full. The hospital has actually
        # recovered before it gives the patient-specific acceptance at t=3.
        hospitals[0]['initial_occupancy'] = [
            dict(until=2, demand=deepcopy(hospitals[0]['capacity'])),
        ]
        observation.update(feed_interval_minutes=1, feed_delay_minutes=6)
        replies['NEAR']['delay'] = 3
        replies['FAR']['delay'] = 5
        extra.update(truth_recovery_minute=2, first_near_reply_minute=3,
                     first_post_recovery_feed_delivery_minute=8)

    elif family == 'partial_team_missing':
        observation.update(feed_interval_minutes=0, initial_reports=[
            _initial_report(hospital, missing=('special_team',)) for hospital in hospitals
        ])
        extra.update(missing_resource='special_team',
                     patients_requiring_missing_resource=['P03'],
                     patients_without_missing_resource=['P01', 'P02'])

    elif family == 'out_of_order':
        hospitals[0]['initial_occupancy'] = [
            dict(until=3, demand=deepcopy(hospitals[0]['capacity'])),
        ]
        observation.update(feed_interval_minutes=1, feed_delay_minutes=0,
                           feed_delay_by_source_minute={'1': 8, '4': 0})
        replies['NEAR']['delay'] = 4
        replies['FAR']['delay'] = 5
        extra.update(older_source_minute=1, older_delivery_minute=9,
                     newer_source_minute=4, newer_delivery_minute=4)

    value = dict(
        id=f'information-{family}-{seed}', label=_LABELS[family],
        family=family, information_family=family, seed=seed, synthetic=True,
        hospitals=hospitals, patients=patients, exogenous_events=events,
        response_environment={'*': replies}, observation_config=observation,
        max_minutes=60, timeout_minutes=6, retry_minutes=3, accept_ttl_minutes=10,
        nonresponse=0, transfer_preparation_minutes=3, assumptions=list(_ASSUMPTIONS),
        experiment=dict(
            information_family=family, seed=seed, patient_count=len(patients),
            comparison_scope='same_truth_environment_perfect_and_observed_views',
            clinical_values='synthetic', identity_source='synthetic',
            routing='synthetic_fixed_coordinates', timing_unit='integer_minute',
            common_inquiry_settings=dict(k=3, wait=2, notify=True), **extra,
        ),
    )
    return value


def build_information_scenarios(seed, families=None):
    """Build deterministic, independent cases in requested family order.

    An omitted initial_reports field means the engine publishes its initial
    truth snapshot at source minute 0. An explicit [] means no initial report.
    Selecting a family subset does not change that family's random stream.
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
            raise ValueError('unknown partial information family')
        if len(set(selected)) != len(selected):
            raise ValueError('duplicate partial information family')
    # Keep module imports acyclic while the separate replay subclass is built.
    from .map_replay import validate_scenario
    cases = [_build(seed, family) for family in selected]
    for case in cases:
        validate_scenario(case)
    return deepcopy(cases)
