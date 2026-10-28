"""One factor: exhaustively choose a currently executable EAT inquiry batch.

The frozen residual policy supplies the same public beliefs and queue model.
Only its future-offset coordinate plan is replaced: at most six visible
candidates and four free inquiry slots produce at most 57 immediate subsets.
"""
from itertools import combinations
import math

from server import inquiry_policies
from . import residual_policy as original
from .residual_policy import (Beliefs, expected_cost, _lognorm_cdf, _masses,
    _no_accept_by, _shift, _queue_wait_estimate, RESIDUAL_RULE)

NAME = 'eat_batch_l15_f'
BASE_POLICY = 'eat_residual_l15_f'
BATCH_RULE = 'enumerate_current_feasible_batches'


def spec(name=NAME):
    if name != NAME:
        raise ValueError('the fixed immediate-batch candidate is ' + NAME)
    value=original.spec(BASE_POLICY)
    value.update(name=NAME,label='EAT l15 f, residual queue and executable batches')
    return value


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
        """Every chosen inquiry executes now; no later free slot is assumed."""
        chosen = [h for h, offset in plan.items() if offset == 0]
        value = expected_cost(current + [items[h] for h in chosen], cells, step, horizon, failure)
        return value + sum(lams[h] for h in chosen)

    base = expected_cost(current, cells, step, horizon, failure)
    maximum_subset_size = max(0, min(len(pool), room))
    hospital_order = [row['hospital'] for row in pool]
    subsets = []
    for size in range(maximum_subset_size + 1):
        for subset in combinations(hospital_order, size):
            trial = {h: 0 if h in subset else None for h in hospital_order}
            subsets.append(dict(hospitals=list(subset), cost=plan_cost(trial),
                selected_before_safety=False))
    minimum_cost = min(row['cost'] for row in subsets)
    # Define epsilon ties against the global minimum, not an order-dependent
    # chain of almost-equal comparisons. Enumeration gives fewer inquiries,
    # then original pool-combination order, among costs within 1e-9 of it.
    optimal = next(row for row in subsets if row['cost'] <= minimum_cost + 1e-9)
    optimal['selected_before_safety'] = True
    plan = {h: 0 if h in optimal['hospitals'] else None for h in hospital_order}
    best_cost = optimal['cost']
    send = list(optimal['hospitals'])
    forced_batch = False
    safety_hospital = None
    # 안전 규칙: 180초 이내 대기 문의가 하나도 없는데 지금 보낼 곳도 없으면, 계획에서 가장
    # 비용이 낮아지는 한 곳을 지금 문의한다. 수용 가능성이 낮다고 추정돼도 멈추지 않는다.
    if len(own) == stale and not send and pool and room > 0:
        forced = min(pool, key=lambda row: (plan_cost({**plan, row['hospital']: 0}), row['travel_seconds']))
        plan = {**plan, forced['hospital']: 0}
        best_cost = plan_cost(plan)
        send = [forced['hospital']]
        forced_batch = True
        safety_hospital = forced['hospital']
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
    evidence['batch'] = dict(schema_version=1, rule=BATCH_RULE, room=room,
        pool=hospital_order, maximum_subset_size=maximum_subset_size, subsets=subsets,
        evaluated_count=len(subsets), minimum_cost=minimum_cost,
        optimal_subset=list(optimal['hospitals']), optimal_cost=optimal['cost'],
        selected=list(send), selected_cost=best_cost, forced=forced_batch,
        safety_hospital=safety_hospital)
    return dict(send=send, reason=reason, evidence=evidence,
                next_review_at=now + policy['review_seconds'])
