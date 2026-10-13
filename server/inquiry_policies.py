"""초기 문의 정책의 순수 계산 계약. DB·서비스·미래 응답 일정은 참조하지 않는다.

observed_v2는 시뮬레이션의 관측 기반 순위 계산을 초 단위 입력에
연결한다. 점수는 경험적 비용이며 수용 확률이나 임상 예후 예측값이 아니다.
정책은 문의할 병원만 제안하고 회신·목적지 확정·기존 문의를 변경하지 않는다.
"""
from collections.abc import Mapping
from copy import deepcopy
import math

from simulation.observed_inquiry import plan_inquiries
from . import inquiry_v3


POLICY_NAMES = ('fixed_1', 'fixed_3', 'fixed_5', 'observed_v2', 'observed_v3')
V3_EXPERIMENT_NAMES = inquiry_v3.V3_EXPERIMENT_NAMES
OBSERVATION_TIMEOUT_SECONDS = 180
_OBSERVED_CONFIG = {
    'window_seconds': 1800,
    'prior_reply_seconds': 180,
    'min_samples': 2,
    'congestion_seconds_per_unresolved': 60,
    'hedge_after_seconds': 120,
    'hedge_margin_seconds': 0,
    'max_new_per_review': 1,
    'timeout_penalty_seconds': 360,
    'max_concurrent': 3,
    'observation_timeout_seconds': OBSERVATION_TIMEOUT_SECONDS,
}
_POLICIES = {
    **{f'fixed_{size}': dict(name=f'fixed_{size}', label=f'고정 {size}곳 문의', version='1',
                            batch_size=size, wait_seconds=60, review_seconds=60,
                            max_contacts=10, deadline_seconds=300, experimental=True,
                            config={'observation_timeout_seconds': OBSERVATION_TIMEOUT_SECONDS})
       for size in (1, 3, 5)},
    'observed_v2': dict(name='observed_v2', label='관측 기반 V2', version='1',
                        batch_size=1, wait_seconds=120, review_seconds=15,
                        max_contacts=10, deadline_seconds=300, experimental=True,
                        config=_OBSERVED_CONFIG),
}
_POLICIES.update(inquiry_v3.policy_specs(_OBSERVED_CONFIG))


def spec(name):
    """등록된 정책의 독립적인 명세. 오타·비문자열을 조용히 대체하지 않는다."""
    if not isinstance(name, str) or name not in _POLICIES:
        raise ValueError('unknown inquiry policy; expected ' + ', '.join(POLICY_NAMES))
    return deepcopy(_POLICIES[name])


def registry():
    return {'policies': [spec(name) for name in POLICY_NAMES]}


def _integer(value, name, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError(f'{name} must be an integer >= {minimum}')
    return value


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _hospital(value):
    if not isinstance(value, str) or not value:
        raise ValueError('hospital must be a nonempty string')
    return value


def _candidates(rows):
    result, seen = [], set()
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError('candidate must be a mapping')
        hospital = _hospital(row.get('hospital_id'))
        travel = row.get('travel')
        seconds = travel.get('duration_seconds') if isinstance(travel, Mapping) else None
        if not _number(seconds):
            raise ValueError('candidate travel.duration_seconds must be finite and >= 0')
        if hospital in seen:
            raise ValueError('candidate hospitals must be unique')
        seen.add(hospital)
        result.append(dict(hospital=hospital, travel_seconds=seconds,
                           resources_enough=row.get('resources_enough')))
    return result


def _observations(rows, now, window):
    result = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        submitted, observed = row.get('submitted_at'), row.get('observed_at')
        # 미래·불명확한 관측은 병원 이름이나 outcome을 해석하기 전에 버린다.
        if (not _number(submitted) or not _number(observed) or submitted > now or
                observed < submitted or not now - window < observed <= now):
            continue
        hospital, outcome = row.get('hospital'), row.get('outcome')
        if not isinstance(hospital, str) or not hospital or outcome not in ('reply', 'accept', 'decline', 'timeout'):
            continue
        result.append(dict(hospital=hospital, submitted_at=submitted, observed_at=observed,
                           outcome=outcome))
    return result


def _pending(rows, now, own=False):
    result = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        submitted = row.get('submitted_at')
        if not _number(submitted) or submitted > now:
            continue
        hospital, patient = row.get('hospital'), row.get('patient')
        if (not isinstance(hospital, str) or not hospital or
                isinstance(patient, bool) or not isinstance(patient, (str, int))):
            continue
        record = dict(hospital=hospital, patient=patient, submitted_at=submitted)
        if own:
            travel = row.get('travel_seconds')
            if not _number(travel):
                raise ValueError('patient_pending travel_seconds must be finite and >= 0')
            record['travel_seconds'] = travel
        result[(hospital, patient, submitted)] = record
    return list(result.values())


def _fresh_history(rows, now, window):
    """V3는 오래된 행의 병원·결과를 해석하기 전에 시각으로 분리한다."""
    fresh, excluded = [], 0
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        submitted, observed = row.get('submitted_at'), row.get('observed_at')
        if (not _number(submitted) or not _number(observed) or submitted > now
                or observed < submitted or observed > now):
            continue
        if observed <= now - window:
            # 제외 수는 시각이 유효한 과거 행의 수다. 사용하지 않는
            # hospital/outcome 내용과 미래 입력은 판단 근거에 섞지 않는다.
            excluded += 1
            continue
        fresh.append(row)
    return _observations(fresh, now, window), excluded


def _original_seconds(minute_value, origin):
    value = round(origin + minute_value * 60, 6)
    return int(value) if value == int(value) else value


def decide(name, *, now, candidates, observations=(), pending=(), patient_pending=(), max_new,
           last_contact_at=None):
    """입력을 변경하지 않고 문의 후보와 근거를 반환한다.

    candidates는 치료 조건과 미문의 여부를 확인한 정렬 목록이다.
    observations: hospital/submitted_at/observed_at/outcome.
    pending: hospital/patient/submitted_at. patient_pending은 그 부분집합에
    travel_seconds를 추가한다. 시각·이동시간은 초다. 고정 정책의 60초 대기와
    환자·병원 상태 재검사는 호출자가 담당한다.
    """
    policy = spec(name)
    _integer(now, 'now')
    _integer(max_new, 'max_new')
    ranked = _candidates(candidates)
    if 'prior_reply_seconds' not in policy['config']:
        send = [row['hospital'] for row in ranked[:min(policy['batch_size'], max_new)]]
        return dict(send=send,
                    reason='fixed_wave' if send else 'contact_budget' if max_new == 0 else 'no_candidates',
                    next_review_at=now + policy['review_seconds'],
                    evidence=dict(evaluated_at=now, policy=name, batch_size=policy['batch_size'],
                                  candidate_order=[row['hospital'] for row in ranked],
                                  sent_hospitals=list(send)))

    if name == 'observed_v2':
        return _observed_decision(policy, now=now, ranked=ranked, observations=observations,
                                  pending=pending, patient_pending=patient_pending, max_new=max_new)

    # V3에는 이미 관측한 유효한 이력만 전달한다. 미래·손상 입력은
    # 제외 횟수에도 섞지 않으며 상세 근거에 타 환자 ID를 남기지 않는다.
    if last_contact_at is not None:
        if not _number(last_contact_at):
            raise ValueError('last_contact_at must be finite and >= 0')
        if last_contact_at > now:
            last_contact_at = None
    fresh, excluded = _fresh_history(observations, now, policy['config']['history_window_seconds'])
    own = _pending(patient_pending, now, own=True)
    result = _observed_decision(policy, now=now, ranked=ranked, observations=fresh,
                                pending=pending, patient_pending=own, max_new=max_new)
    return inquiry_v3.bounded_wait(result, now=now, config=policy['config'], own=own,
                                  history_used_count=len(fresh),
                                  history_excluded_count=excluded,
                                  max_new=max_new, last_contact_at=last_contact_at)


def _observed_decision(policy, *, now, ranked, observations, pending, patient_pending, max_new):
    """V2 비용·근거 형식을 보존한 공통 관측 계산. V3도 이 함수를 사용한다."""
    name, config = policy['name'], policy['config']
    history = _observations(observations, now, config['window_seconds'])
    unresolved = _pending(pending, now)
    own = _pending(patient_pending, now, own=True)
    ledger_keys = {(row['hospital'], row['patient'], row['submitted_at']) for row in unresolved}
    if any((row['hospital'], row['patient'], row['submitted_at']) not in ledger_keys for row in own):
        raise ValueError('patient_pending must be a subset of pending')
    if len({row['patient'] for row in own}) > 1:
        raise ValueError('patient_pending must describe one patient')
    # 180초를 넘긴 요청은 이 환자의 정책 대기 집합에서만 제외한다. 글로벌
    # 미해결 부하와 실제 문의는 남긴다. 정확히 180초인 요청은 아직 대기 중이다.
    waiting = [row for row in own if now - row['submitted_at'] <= config['observation_timeout_seconds']]
    oldest_submission = min((row['submitted_at'] for row in [*history, *unresolved]), default=now)
    # sim의 t는 정수 분이다. 원점을 최소 1분 앞에 두어 초 단위와 1분 guard를 유지한다.
    t = math.ceil((now - oldest_submission) / 60) + 1
    origin = now - t * 60
    minute = lambda seconds: (seconds - origin) / 60
    sim_config = dict(window=config['window_seconds'] // 60,
                      prior_reply=config['prior_reply_seconds'] / 60,
                      min_samples=config['min_samples'],
                      congestion_weight=config['congestion_seconds_per_unresolved'] / 60,
                      hedge_after=config['hedge_after_seconds'] / 60,
                      hedge_margin=config['hedge_margin_seconds'] / 60,
                      max_new_per_review=config['max_new_per_review'],
                      timeout_penalty=config['timeout_penalty_seconds'] / 60)
    result = plan_inquiries(sim_config, t=t,
        candidates=[dict(hospital=row['hospital'], travel_minutes=row['travel_seconds'] / 60)
                    for row in ranked],
        observations=[dict(hospital=row['hospital'], submitted_t=minute(row['submitted_at']),
                           observed_t=minute(row['observed_at']), outcome=row['outcome']) for row in history],
        pending=[dict(hospital=row['hospital'], patient=row['patient'], submitted_t=minute(row['submitted_at']))
                 for row in unresolved],
        patient_pending=[dict(hospital=row['hospital'], patient=row['patient'],
                              submitted_t=minute(row['submitted_at']), travel_minutes=row['travel_seconds'] / 60)
                         for row in waiting],
        max_k=config['max_concurrent'])
    send = list(result['send'][:max_new])
    reason = 'contact_budget' if max_new == 0 else result['reason']
    evidence = deepcopy(result['evidence'])
    evidence.pop('evaluated_t', None)
    evidence.update(evaluated_at=now, policy=name, window_seconds=config['window_seconds'],
                    hedge_after_seconds=config['hedge_after_seconds'],
                    observation_timeout_seconds=config['observation_timeout_seconds'],
                    unresolved_inquiry_count=len(unresolved),
                    observation_timed_out_pending_count=len(own) - len(waiting),
                    oldest_pending_age_seconds=max((now - row['submitted_at'] for row in waiting), default=None),
                    sent_hospitals=send)
    for score in evidence['pending_scores']:
        score['submitted_at'] = _original_seconds(score.pop('submitted_t'), origin)
        score['age_seconds'] = now - score['submitted_at']
    return dict(send=send, reason=reason, evidence=evidence,
                next_review_at=now + policy['review_seconds'])
