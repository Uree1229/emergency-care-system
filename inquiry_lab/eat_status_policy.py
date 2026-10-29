"""EAT-R: 병원의 '현재 문의 응대 가능/불가' 상태 표시로 병원별 회신 확률을 정하는 EAT 변형(5차).

1차 잠금 정책 `eat_policy.decide`(eat_l15_f)의 계산을 그대로 옮기고, 병원별 회신 확률을 관측으로
갱신할 때의 출발점만 바꾼다. 표시가 있는 병원은 공통 회신 추정 대신 P(회신 | 표시)를 출발점으로 쓰고,
신호 강도만큼(가상 표본 s개) 더 믿는다. P(회신 | 표시)는 개발 데이터로 센 값을 잠근다.
표시가 없는 병원은 1차 EAT와 계산이 같다(test로 확인). 정책은 표시의 정확도(품질)를 모른다.

서버 연동 시에는 병원 웹이 저장한 응대 상태를 후보 조회 결과에 붙여 넘기면 된다. 실험에서는 실행기가
시나리오마다 set_context로 넣는다.
"""
import math
import re

from server import inquiry_policies

from . import eat_policy

PREFIX = 'eatr_'
STRENGTH_GRID = (4, 16)
# s: 모든 품질을 합친 보정(pooled)으로 표시를 평균적 신뢰도만큼 믿는다.
# t: 정확한 표시만으로 센 보정(trusted)으로 표시를 늘 정확하다고 믿는다.
_CONTEXT = dict(status={}, calibration=None)


def variant_name(strength, trusted=False):
    return f'{PREFIX}{"t" if trusted else "s"}{strength}'


def variant_grid():
    return tuple(variant_name(s) for s in STRENGTH_GRID) + (variant_name(16, trusted=True),)


def set_context(status, calibration):
    _CONTEXT['status'] = dict(status)
    _CONTEXT['calibration'] = dict(calibration) if calibration else None


def clear_context():
    _CONTEXT['status'], _CONTEXT['calibration'] = {}, None


def spec(name):
    match = re.fullmatch(PREFIX + r'([st])(\d+)', name) if isinstance(name, str) else None
    if match is None or int(match.group(2)) <= 0:
        raise ValueError('EAT-R policy name must look like eatr_s4 or eatr_t16')
    source = 'trusted' if match.group(1) == 't' else 'pooled'
    base = eat_policy.spec('eat_l15_f')
    base['config'].update(status_strength=float(match.group(2)), status_calibration=source)
    base.update(name=name, label=f'EAT-R (응대 가능 상태 표시, {source} 보정, 강도 {match.group(2)})')
    return base


class StatusBeliefs(eat_policy.Beliefs):
    def __init__(self, observations, now, config, targets):
        super().__init__(observations, now, config)
        self._targets = targets
        self._strength = config['status_strength']

    def reply(self, h):
        if h not in self._targets:
            return super().reply(h)
        c = self._replies.get(h, (0.0, 0.0))
        weight = self._priors['hospital_shrink_reply'] + self._strength
        return eat_policy._shrink(c[0], c[1], self._targets[h], weight)


def _targets(config):
    calibration = _CONTEXT['calibration']
    if not calibration:
        return {}
    table = calibration[config['status_calibration']]
    return {hid: table[status]['reply_probability'] for hid, status in _CONTEXT['status'].items()
            if status in table}


def decide(name, *, now, candidates, observations=(), pending=(), patient_pending=(), max_new,
           last_contact_at=None):
    """eat_policy.decide와 같은 계산. 병원별 회신 확률의 출발점만 상태 표시로 정한다."""
    policy = spec(name)
    config = policy['config']
    if type(now) is not int or now < 0:
        raise ValueError('now must be an integer >= 0')
    if type(max_new) is not int or max_new < 0:
        raise ValueError('max_new must be an integer >= 0')
    ranked = inquiry_policies._candidates(candidates)
    window = config['history_window_seconds']
    history = inquiry_policies._observations(observations, now, window)
    unresolved = inquiry_policies._pending(pending, now)
    own = inquiry_policies._pending(patient_pending, now, own=True)
    keys = {(r['hospital'], r['patient'], r['submitted_at']) for r in unresolved}
    if any((r['hospital'], r['patient'], r['submitted_at']) not in keys for r in own):
        raise ValueError('patient_pending must be a subset of pending')
    if len({r['patient'] for r in own}) > 1:
        raise ValueError('patient_pending must describe one patient')
    if last_contact_at is not None and (type(last_contact_at) not in (int, float)
                                        or not math.isfinite(last_contact_at) or last_contact_at > now):
        last_contact_at = None

    targets = _targets(config)
    beliefs = StatusBeliefs(history, now, config, targets)
    step = config['grid_seconds']
    known_starts = [r['submitted_at'] for r in own]
    if last_contact_at is not None:
        known_starts.append(last_contact_at)
    elapsed = now - min(known_starts) if known_starts else 0
    horizon = max(step * 3, policy['deadline_seconds'] - elapsed)
    cells = max(3, int(math.ceil(horizon / step)))
    shifts = [None] + sorted({int(round(d / step)) for d in config['plan_offsets_seconds']
                              if d < horizon})
    failure = config['failure_penalty_seconds']
    workers = config['assumed_workers']
    _lognorm_cdf, _masses = eat_policy._lognorm_cdf, eat_policy._masses

    queue = {}
    for row in unresolved:
        queue.setdefault(row['hospital'], []).append(row)
    last_reply = {}
    for row in history:
        if row['outcome'] != 'timeout':
            last_reply.setdefault(row['hospital'], []).append(row)

    def pending_item(row):
        h, submitted = row['hospital'], row['submitted_at']
        mu, sigma = beliefs.mu(h), beliefs.sigma
        r, a = beliefs.reply(h), beliefs.accept(h)
        ahead = sum(1.0 if q['submitted_at'] < submitted else 0.5
                    for q in queue.get(h, ())
                    if q['submitted_at'] <= submitted and q['patient'] != row['patient'])
        if ahead > 0:
            wait = ahead * beliefs.mean_service(h) / workers
            cdf = lambda x: _lognorm_cdf(x - wait, mu, sigma)
            total = r * a
        else:
            freed = [o['observed_at'] for o in last_reply.get(h, ()) if o['submitted_at'] <= submitted]
            started = max([submitted, *freed])
            spent = max(0.0, now - started)
            f0 = _lognorm_cdf(spent, mu, sigma)
            alive = max(1e-9, 1.0 - f0)
            reply_left = r * alive / max(1e-9, 1.0 - r * f0)
            cdf = lambda x: (_lognorm_cdf(spent + x, mu, sigma) - f0) / alive
            total = reply_left * a
        return row['travel_seconds'], _masses(cdf, total, cells, step)

    def new_item(row):
        h = row['hospital']
        mu, sigma = beliefs.mu(h), beliefs.sigma
        load = len(queue.get(h, ()))
        wait = load * beliefs.mean_service(h) / workers
        cdf = lambda x: _lognorm_cdf(x - wait, mu, sigma)
        return row['travel_seconds'], _masses(cdf, beliefs.reply(h) * beliefs.accept(h), cells, step)

    def inquiry_cost(row):
        lam = config['lambda_seconds']
        if config['congestion_aware']:
            lam *= 1 + len(queue.get(row['hospital'], ()))
        return lam

    current = [pending_item(row) for row in own]
    stale = sum(1 for row in own if now - row['submitted_at'] > config['observation_timeout_seconds'])
    pool = ranked[:min(config['max_candidates_evaluated'], max_new)]
    items = {row['hospital']: new_item(row) for row in pool}
    lams = {row['hospital']: inquiry_cost(row) for row in pool}
    room = min(max_new, config['max_concurrent'] - (len(own) - stale))

    def plan_cost(plan):
        chosen = [(c, h) for h, c in plan.items() if c is not None]
        all_items = current + [eat_policy._shift(items[h], c) if c else items[h] for c, h in chosen]
        value = eat_policy.expected_cost(all_items, cells, step, horizon, failure)
        for c, h in chosen:
            value += lams[h] * (eat_policy._no_accept_by(all_items, c) if c else 1.0)
        return value

    base = eat_policy.expected_cost(current, cells, step, horizon, failure)
    plan = {row['hospital']: None for row in pool}
    best_cost = plan_cost(plan)
    for _ in range(config['plan_passes']):
        improved = False
        for row in pool:
            h = row['hospital']
            for offset in shifts:
                if offset == plan[h]:
                    continue
                if offset == 0 and plan[h] != 0 and sum(c == 0 for c in plan.values()) >= room:
                    continue
                trial = dict(plan)
                trial[h] = offset
                value = plan_cost(trial)
                if value < best_cost - 1e-9:
                    plan, best_cost, improved = trial, value, True
        if not improved:
            break
    send = [row['hospital'] for row in pool if plan[row['hospital']] == 0]
    if len(own) == stale and not send and pool and room > 0:
        forced = min(pool, key=lambda row: (plan_cost({**plan, row['hospital']: 0}), row['travel_seconds']))
        plan = {**plan, forced['hospital']: 0}
        best_cost = plan_cost(plan)
        send = [forced['hospital']]
    scored = [dict(hospital=row['hospital'],
                   planned_offset_seconds=None if plan[row['hospital']] is None else plan[row['hospital']] * step,
                   inquiry_cost=lams[row['hospital']],
                   reply_probability=round(beliefs.reply(row['hospital']), 4),
                   accept_probability=round(beliefs.accept(row['hospital']), 4),
                   queue_length=len(queue.get(row['hospital'], ())),
                   travel_seconds=row['travel_seconds']) for row in pool]

    if max_new == 0:
        reason = 'contact_budget'
    elif send:
        reason = 'eat_initial' if not own else 'eat_hedge'
    elif not ranked:
        reason = 'no_candidates'
    elif room <= 0:
        reason = 'eat_concurrency_cap'
    else:
        reason = 'eat_wait'
    evidence = dict(evaluated_at=now, policy=name, horizon_seconds=horizon,
                    lambda_seconds=config['lambda_seconds'],
                    congestion_aware=config['congestion_aware'],
                    expected_cost_without_new=round(base, 3),
                    planned_expected_cost=round(best_cost, 3),
                    reply_probability_global=round(beliefs.reply_global, 4),
                    accept_probability_global=round(beliefs.accept_global, 4),
                    service_median_seconds=round(math.exp(beliefs.mu_global), 2),
                    history_used_count=len(history), own_waiting_count=len(own) - stale,
                    own_observation_timed_out_count=stale,
                    unresolved_inquiry_count=len(unresolved),
                    candidate_plan=scored, sent_hospitals=list(send),
                    status_signal=dict(sorted(_CONTEXT['status'].items())))
    return dict(send=send, reason=reason, evidence=evidence,
                next_review_at=now + policy['review_seconds'])


def install():
    from . import harness
    harness.install(['eat_l15_f'])
    for name in variant_grid():
        inquiry_policies._POLICIES.setdefault(name, spec(name))
    current = inquiry_policies.decide
    if not getattr(current, '_eat_status', False):
        def wrapped(name, **kwargs):
            if name.startswith(PREFIX):
                return decide(name, **kwargs)
            return current(name, **kwargs)
        wrapped._eat_status = True
        inquiry_policies.decide = wrapped


def uninstall():
    from . import harness
    for name in variant_grid():
        inquiry_policies._POLICIES.pop(name, None)
    harness.uninstall()
    clear_context()
