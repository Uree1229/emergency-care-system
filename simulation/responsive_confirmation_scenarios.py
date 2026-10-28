"""Fresh synthetic cases for response-aware resource confirmation.

The frozen opportunity experiment's sixteen families are retained unchanged
apart from descriptive identifiers. Six further environment cases separate
early confirmation, bounded cold waits and recovery watches. No case gives a
policy the future report delivery time, outage restoration or future patient.

Development seeds: 21101-21103. Validation seeds: 22101-22106.
Structural tests use seeds 7 and 8, never a reserved sweep seed.
"""
from __future__ import annotations

from copy import deepcopy

from .opportunity_scenarios import (
    BASIC, SCARCE, FAMILIES as ORIGINAL_FAMILIES, build_opportunity_scenarios,
)


DEVELOPMENT_SEEDS = (21101, 21102, 21103)
VALIDATION_SEEDS = (22101, 22102, 22103, 22104, 22105, 22106)
ADVERSARIAL_FAMILIES = (
    'slow_confirmation_three',
    'slow_confirmation_eight',
    'persistent_partial_slow',
    'expiry_before_slow_confirmation',
    'watch_outage_recovery',
    'future_patient_not_yet_active',
)
FAMILIES = ORIGINAL_FAMILIES + ADVERSARIAL_FAMILIES

_LABELS = {
    'slow_confirmation_three': '전문팀 재확인 3분·선행 확인과 제한 대기',
    'slow_confirmation_eight': '전문팀 재확인 8분·긴 무정보 대기 비용',
    'persistent_partial_slow': '재확인 8분 후에도 전문팀 수량 누락',
    'expiry_before_slow_confirmation': '재확인 8분·환자 수용 회신이 먼저 만료',
    'watch_outage_recovery': '재확인 중 전문팀 중단·복구 뒤 현재 보고 확인',
    'future_patient_not_yet_active': '전문팀 필요 환자가 12분 뒤 발생·미래 환자 정보 금지',
}

_ASSUMPTIONS = (
    '응답 대응 재확인은 별도 합성 실험이며 운영 서버와 팀원 화면을 변경하지 않는다.',
    '빠른 재확인·제한 대기·자원 감시 분기는 동일한 잠재 병원 응답과 자원 환경을 사용한다.',
    '환자 수용 응답과 자원 수량 확인은 별개이며 자원 확인으로 만료된 환자 수용 응답을 연장하지 않는다.',
    '수용 응답 전 선행 자원 재확인 요청은 자원 확인 완료나 환자 배정 확정을 뜻하지 않는다.',
    '자원 감시는 유한한 상한과 병원별 재시도 간격을 따르며 자원 수량 문의가 증가할 수 있다.',
    '감시 뒤 환자 문의 재개 여부는 현재 수신한 유효한 자원 보고로만 결정한다.',
    '전문팀 확인의 도착 예정시각과 복구 예정시각은 환경 자료이며 정책에 전달하지 않는다.',
    '미래 환자는 발생·문의 시작 시각 전에 자원 재확인이나 병상 보호의 근거로 사용하지 않는다.',
    '재확인 요청 병합·재시도 간격·대기 상한은 사례별 설정 없이 동일한 잠근 실행 설정으로 비교한다.',
)

def _adversarial(seed, family):
    case = build_opportunity_scenarios(seed, ['partial_team_missing'])[0]
    case['label'] = _LABELS[family]
    config = case['observation_config']
    experiment = case['experiment']
    experiment['responsive_adversarial_intent'] = family

    if family == 'slow_confirmation_three':
        config['confirmation_delay_minutes'] = 3
    elif family == 'slow_confirmation_eight':
        config['confirmation_delay_minutes'] = 8
    elif family == 'persistent_partial_slow':
        config.update(confirmation_delay_minutes=8,
                      confirmation_missing_resources=['special_team'])
        experiment['resource_confirmation_resolves_missing_team'] = False
    elif family == 'expiry_before_slow_confirmation':
        config['confirmation_delay_minutes'] = 8
        case['accept_ttl_minutes'] = 1
        experiment['offer_expires_before_first_resource_reply'] = True
    elif family == 'watch_outage_recovery':
        config['confirmation_delay_minutes'] = 3
        case['exogenous_events'] = [dict(
            type='resource_unavailable', t=1, until=8,
            hospital='NEAR', resource='special_team',
        )]
        experiment.update(resource_outage_minute=1, resource_recovery_minute=8,
                          periodic_feed_enabled=False)
    elif family == 'future_patient_not_yet_active':
        config['confirmation_delay_minutes'] = 3
        waiting = next(p for p in case['patients'] if p['id'] == 'P03')
        for key in ('occur_t', 'dispatch_t', 'ambulance_arrive_t', 'search_start_t'):
            waiting[key] = 12
        experiment.update(future_patient_id='P03', future_patient_start_minute=12)
    else:
        raise AssertionError('unhandled responsive confirmation family')
    return case


def build_responsive_confirmation_scenarios(seed, families=None):
    """Return detached cases in requested order without family-order randomness.

    The sixteen previous families retain their exact environment inputs at the
    new seed. Additional families modify only supported observation timing,
    missing quantities, offer lifetime, exogenous outage or patient chronology.
    All policy settings belong to the separate runner, never to these cases.
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
            raise ValueError('unknown responsive confirmation family')
        if len(set(selected)) != len(selected):
            raise ValueError('duplicate responsive confirmation family')

    from .map_replay import validate_scenario

    originals = {case['family']: case for case in build_opportunity_scenarios(
        seed, [family for family in selected if family in ORIGINAL_FAMILIES])}
    cases = []
    for family in selected:
        case = originals[family] if family in originals else _adversarial(seed, family)
        case.update(id=f'responsive-confirmation-{family}-{seed}', family=family,
                    information_family=family, opportunity_family=family,
                    responsive_confirmation_family=family)
        case['experiment'].update(
            information_family=family, opportunity_family=family,
            responsive_confirmation_family=family,
            patient_count=len(case['patients']),
            comparison_scope='same_truth_environment_confirmation_timing_and_recovery_watch',
            prior_opportunity_family=family if family in originals else 'partial_team_missing',
        )
        case['assumptions'].extend(_ASSUMPTIONS)
        validate_scenario(case)
        cases.append(case)
    return deepcopy(cases)
