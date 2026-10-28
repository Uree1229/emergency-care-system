"""EAT-J: 여러 환자가 동시에 검색할 때 병원당 동시 미처리 문의 수에 상한을 두는 EAT 변형(7차).

여러 환자가 같은 가까운 병원에 동시에 여러 곳씩 물으면, 병원 FIFO 대기열에 문의가 몰려 뒤에 들어간
환자의 회신이 늦어진다. 1차 EAT는 자기 문의가 다른 환자를 늦추는 비용(외부효과)을 고려하지 않는다.
EAT-J는 1차 잠금 정책 `eat_policy.decide`(eat_l15_f)의 계산을 그대로 옮기고 한 가지만 더한다.

  모든 환자의 미해결 문의를 합쳐 이미 c건 이상 걸려 있는 병원에는 '지금' 문의하지 않는다.
  (나중 시각의 계획에는 넣을 수 있고, 다음 판단 때 다시 본다.)

혼자 검색하는 환자는 자기 문의가 서로 다른 병원으로 가므로 상한에 거의 걸리지 않는다.
대기 중인 문의가 하나도 없으면 상한과 무관하게 한 곳은 반드시 문의한다(안전 규칙, 덜 붐비는 병원 우선).
상한이 매우 크면 1차 EAT와 계산이 같다(test로 확인).
"""
import math
import re

from server import inquiry_policies

from . import eat_policy

PREFIX = 'eatj_'
CAP_GRID = (1, 2, 3)


def variant_name(cap):
    return f'{PREFIX}c{cap}'


def variant_grid():
    return tuple(variant_name(c) for c in CAP_GRID)


def spec(name):
    match = re.fullmatch(PREFIX + r'c(\d+)', name) if isinstance(name, str) else None
    if match is None or int(match.group(1)) <= 0:
        raise ValueError('EAT-J policy name must look like eatj_c2')
    base = eat_policy.spec('eat_l15_f')
    base['config'].update(hospital_inflight_cap=int(match.group(1)))
    base.update(name=name, label=f'EAT-J (병원당 동시 미처리 문의 상한 {match.group(1)}건)')
    return base


def decide(name, *, now, candidates, observations=(), pending=(), patient_pending=(), max_new,
           last_contact_at=None):
    """eat_policy.decide와 같은 계산. 상한에 걸린 병원은 '지금' 문의 후보에서 뺀다."""
    policy = spec(name)
    config = policy['config']
    cap = config['hospital_inflight_cap']
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

    beliefs = eat_policy.Beliefs(history, now, config)
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
    capped = {row['hospital'] for row in pool if len(queue.get(row['hospital'], ())) >= cap}

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
                if offset == 0 and plan[h] != 0 and (h in capped or sum(c == 0 for c in plan.values()) >= room):
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
        # 안전 규칙: 상한에 걸리지 않은 병원을 먼저, 모두 걸렸으면 대기열이 가장 짧은 병원을 문의한다.
        if capped:
            forced = min(pool, key=lambda row: (row['hospital'] in capped, len(queue.get(row['hospital'], ())),
                                                plan_cost({**plan, row['hospital']: 0}), row['travel_seconds']))
        else:   # 상한에 걸린 병원이 없으면 1차 EAT와 같은 기준
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
                    hospital_inflight_cap=cap, capped_hospitals=sorted(capped))
    return dict(send=send, reason=reason, evidence=evidence,
                next_review_at=now + policy['review_seconds'])


def install():
    from . import harness
    harness.install(['eat_l15_f', 'eat_l45_f'])
    for name in variant_grid():
        inquiry_policies._POLICIES.setdefault(name, spec(name))
    current = inquiry_policies.decide
    if not getattr(current, '_eat_coord', False):
        def wrapped(name, **kwargs):
            if name.startswith(PREFIX):
                return decide(name, **kwargs)
            return current(name, **kwargs)
        wrapped._eat_coord = True
        inquiry_policies.decide = wrapped


def uninstall():
    from . import harness
    for name in variant_grid():
        inquiry_policies._POLICIES.pop(name, None)
    harness.uninstall()
