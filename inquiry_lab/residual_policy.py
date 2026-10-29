"""One factor: estimated residual work ahead of a new EAT inquiry.

Only the head of the currently visible unresolved queue is aged. Every visible
follower retains one full mean job. This is an inference under the original
one-worker belief, not detection of an actual completion or a free worker.
"""
import math

from server import inquiry_policies
from . import eat_policy as original
from .eat_policy import (Beliefs, expected_cost, _lognorm_cdf, _masses,
    _no_accept_by, _shift)

NAME = 'eat_residual_l15_f'
BASE_POLICY = 'eat_l15_f'
RESIDUAL_RULE = 'head_residual_plus_full_followers'
_LOG_SQRT_2PI = .5 * math.log(2.0 * math.pi)
_SQRT2 = math.sqrt(2.0)


def spec(name=NAME):
    if name != NAME:
        raise ValueError('the fixed residual candidate is ' + NAME)
    value=original.spec(BASE_POLICY)
    value.update(name=NAME,label='EAT l15 f, head residual queue work')
    return value


def _log_normal_survival(z):
    """log(P(Z>z)); use the Gaussian tail expansion only on erfc underflow."""
    value=.5 * math.erfc(z / _SQRT2)
    if value > 0.:
        return math.log(value)
    # erfc underflows only in the far positive tail. At that point the terms
    # omitted after these six corrections are below double precision.
    inverse=1. / (z*z)
    correction=1. + inverse * (-1. + inverse * (3. + inverse * (-15. +
        inverse * (105. + inverse * (-945. + inverse * 10395.)))))
    return -.5*z*z - math.log(z) - _LOG_SQRT_2PI + math.log(correction)


def _logadd(left,right):
    if left == -math.inf:return right
    if right == -math.inf:return left
    high=max(left,right)
    return high + math.log1p(math.exp(min(left,right)-high))


def head_expected_remaining(mu,sigma,reply_probability,age_seconds):
    """E[(S-age)+ | no reply], with the unchanged lognormal/reply beliefs.

    No reply can mean an unfinished responsive job or a silently finished job.
    Therefore the denominator is (1-r)+r*P(S>age), not merely P(S>age).
    The stop-loss integral is evaluated in log space to retain the late tail
    when r is one or close to one; no hard timeout or added threshold is used.
    """
    values=(mu,sigma,reply_probability,age_seconds)
    if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in values) \
            or sigma <= 0 or not 0 <= reply_probability <= 1 or age_seconds < 0:
        raise ValueError('finite lognormal, reply and nonnegative age values required')
    logmean=mu + sigma*sigma/2.
    mean=math.exp(logmean)
    if age_seconds == 0:return mean
    logage=math.log(age_seconds)
    z=(logage-mu)/sigma
    logtail=_log_normal_survival(z)
    left=logmean + _log_normal_survival(z-sigma)
    right=logage + logtail
    # Strict positivity follows from the non-degenerate lognormal stop loss.
    difference=right-left
    if difference >= 0:
        raise ArithmeticError('stop-loss subtraction lost its positive tail')
    logstoploss=left + math.log(-math.expm1(difference))
    r=float(reply_probability)
    logdenominator=_logadd(math.log1p(-r) if r < 1. else -math.inf,
        math.log(r)+logtail if r > 0. else -math.inf)
    result=math.exp(logstoploss-logdenominator)
    if not math.isfinite(result):
        raise ValueError('residual work exceeds finite floating-point range')
    return result


def _queue_wait_estimate(hospital,now,rows,history,beliefs,workers):
    """Use only the existing normalized public queue and terminal replies."""
    ordered=sorted(rows,key=lambda row:(row['submitted_at'],type(row['patient']).__name__,str(row['patient'])))
    mean=beliefs.mean_service(hospital)
    mu,sigma,r=beliefs.mu(hospital),beliefs.sigma,beliefs.reply(hospital)
    record=dict(hospital=hospital,queue_count=len(ordered),assumed_workers=workers,
        head=None,prior_terminal_reply=None,estimated_start_at=None,age_seconds=None,
        service_mu=mu,service_sigma=sigma,mean_service_seconds=mean,reply_probability=r,
        head_expected_remaining_seconds=0.,queued_behind_count=0,estimated_wait_seconds=0.,
        original_full_wait_seconds=len(ordered)*mean/workers)
    if not ordered:return 0.,record
    head=ordered[0]
    terminals=[row for row in history if row['hospital']==hospital
        and row['outcome'] in ('accept','decline') and row['submitted_at'] < head['submitted_at']]
    prior=max(terminals,key=lambda row:(row['observed_at'],row['submitted_at'],row['outcome']),default=None)
    started=max(head['submitted_at'],prior['observed_at'] if prior is not None else head['submitted_at'])
    age=max(0.,now-started)
    remaining=head_expected_remaining(mu,sigma,r,age)
    wait=(len(ordered)*mean/workers if age == 0 else
        (remaining+(len(ordered)-1)*mean)/workers)
    record.update(head=dict(head),prior_terminal_reply=dict(prior) if prior is not None else None,
        estimated_start_at=started,age_seconds=age,head_expected_remaining_seconds=remaining,
        queued_behind_count=len(ordered)-1,estimated_wait_seconds=wait)
    return wait,record


def decide(name, *, now, candidates, observations=(), pending=(), patient_pending=(), max_new,
           last_contact_at=None):
    """서버 정책과 같은 인수로 문의할 병원과 근거를 반환한다. 입력은 변경하지 않는다."""
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

    beliefs = Beliefs(history, now, config)
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
        # 같은 초에 들어간 다른 문의의 FIFO 순서는 정책이 모르므로 절반을 앞선 것으로 본다.
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
        wait, residual_queue_records[h] = _queue_wait_estimate(
            h, now, queue.get(h, ()), history, beliefs, workers)
        cdf = lambda x: _lognorm_cdf(x - wait, mu, sigma)
        return row['travel_seconds'], _masses(cdf, beliefs.reply(h) * beliefs.accept(h), cells, step)

    def inquiry_cost(row):
        lam = config['lambda_seconds']
        if config['congestion_aware']:
            lam *= 1 + len(queue.get(row['hospital'], ()))
        return lam

    # 관측 제한(180초)을 넘긴 자기 문의도 늦은 수용 가능성을 사후확률로 계산에 남긴다.
    # 동시 문의 수에서는 V2·V3와 같이 180초 이내 문의만 센다.
    current = [pending_item(row) for row in own]
    stale = sum(1 for row in own if now - row['submitted_at'] > config['observation_timeout_seconds'])
    pool = ranked[:min(config['max_candidates_evaluated'], max_new)]
    residual_queue_records = {}
    items = {row['hospital']: new_item(row) for row in pool}
    lams = {row['hospital']: inquiry_cost(row) for row in pool}
    room = min(max_new, config['max_concurrent'] - (len(own) - stale))

    def plan_cost(plan):
        """plan: 병원 -> 지금부터 몇 칸 뒤 문의(0=지금). 문의비용은 그때까지 수용이 없을 때만 낸다."""
        chosen = [(c, h) for h, c in plan.items() if c is not None]
        all_items = current + [_shift(items[h], c) if c else items[h] for c, h in chosen]
        value = expected_cost(all_items, cells, step, horizon, failure)
        for c, h in chosen:
            value += lams[h] * (_no_accept_by(all_items, c) if c else 1.0)
        return value

    base = expected_cost(current, cells, step, horizon, failure)
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
    # 안전 규칙: 180초 이내 대기 문의가 하나도 없는데 지금 보낼 곳도 없으면, 계획에서 가장
    # 비용이 낮아지는 한 곳을 지금 문의한다. 수용 가능성이 낮다고 추정돼도 멈추지 않는다.
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
                    candidate_plan=scored, sent_hospitals=list(send))
    evidence['residual_queue'] = dict(schema_version=1, rule=RESIDUAL_RULE,
        hospitals=[residual_queue_records[row['hospital']] for row in pool])
    return dict(send=send, reason=reason, evidence=evidence,
                next_review_at=now + policy['review_seconds'])
