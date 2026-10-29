"""Pinned H planner with conservative own-contact occupancy at every planned wave.

Only the no-terminal-observation plan-feasibility domain changes. Immediate
execution was already capped; this removes optimistic future over-cap forecasts.
Replies can free actual slots, so this is not a physical no-reply prediction.
"""
import hashlib
import math
from pathlib import Path

from server import inquiry_policies
from . import residual_policy as original, horizon_context, horizon_policy as foundation
from .eat_policy import Beliefs, _lognorm_cdf, _no_accept_by

NAME = 'eat_cohort_wavecap_horizon_l15_f'
FOUNDATION_SOURCE_SHA256 = '5061be48175098ce6a30be2af0f0721852e2d67d8bd75ab475a202bcee3ce387'
if hashlib.sha256(Path(foundation.__file__).read_bytes()).hexdigest() != FOUNDATION_SOURCE_SHA256:
    raise ValueError('frozen wave-cap H foundation differs')
WAVECAP_RULE = 'own_normalized_pending_plus_all_same_plan_contacts_inclusive_180_at_every_wave'
WAVECAP_SCOPE = 'conservative no-terminal-observation branch of eligible normalized patient_pending only; real future replies may free slots; no actual success or waiting-time guarantee'
BASE_POLICY = 'eat_residual_l15_f'
HORIZON_RULE = 'explicit_public_lab_observation_end'
RESIDUAL_RULE = original.RESIDUAL_RULE
_queue_wait_estimate = original._queue_wait_estimate
SOURCE_SHA256 = {
    'residual_policy': '8cf391ab5062de374d3c843edd3f6b1be98ac281df9217acc0538cbd98b838ef',
    'eat_policy': 'fc6a7e72dfaf8b632af0ab436baf30adeb0e6ec800262e29a69dd3064e8474ca',
}
for _key, _module in (('residual_policy', original), ('eat_policy', original.original)):
    if hashlib.sha256(Path(_module.__file__).read_bytes()).hexdigest() != SOURCE_SHA256[_key]:
        raise ValueError('frozen horizon foundation source differs: ' + _key)


def spec(name=NAME):
    if name != NAME:
        raise ValueError('the fixed public horizon candidate is ' + NAME)
    value = original.spec(BASE_POLICY)
    value.update(name=NAME, label='EAT l15 f, every-wave own-contact forecast cap')
    return value


def cell_edges(horizon, step):
    """Actual elapsed bin ends, including an inclusive partial final bin."""
    if type(horizon) is not int or horizon <= 0 or type(step) is not int or step <= 0:
        raise ValueError('positive integer horizon and grid required')
    return [min(k * step, horizon) for k in range(1, (horizon + step - 1) // step + 1)]


def masses_at_edges(cdf, total, edges):
    """The original probability-mass rule evaluated at actual elapsed edges."""
    out, previous = [], 0.0
    for edge in edges:
        value = cdf(edge)
        out.append(max(0.0, (value - previous) * total))
        previous = value
    return out


def bounded_expected_cost(items, edges, failure_penalty):
    """Original arrival objective, using each actual bin midpoint and final H."""
    items = sorted(items, key=lambda it: it[0])
    survival = [1.0] * len(items)
    total = 0.0
    left = 0
    for k, right in enumerate(edges):
        alive = 1.0
        for s in survival:
            alive *= s
        if alive <= 1e-12:
            return total
        mid = (left + right) / 2.0
        none_nearer = 1.0
        for i, (travel, masses) in enumerate(items):
            s = survival[i]
            hazard = masses[k] / s if s > 1e-12 else 0.0
            hazard = min(1.0, hazard)
            if hazard > 0.0:
                total += alive * none_nearer * hazard * (mid + travel)
                none_nearer *= 1.0 - hazard
        for i, (_, masses) in enumerate(items):
            survival[i] = max(0.0, survival[i] - masses[k])
        left = right
    fail = 1.0
    for s in survival:
        fail *= s
    return total + fail * (edges[-1] + failure_penalty)


def plan_occupancy(plan, own, now, timeout=180):
    """Seconds, not grid-cell indices; no terminal observation is invented."""
    times = sorted({s for s in plan.values() if s is not None})
    return [dict(offset_seconds=s,
        original_fresh_count=sum(0 <= now+s-q['submitted_at'] <= timeout for q in own),
        planned_fresh_count=sum(t is not None and t <= s and s-t <= timeout for t in plan.values()),
        total_fresh_count=sum(0 <= now+s-q['submitted_at'] <= timeout for q in own)
            +sum(t is not None and t <= s and s-t <= timeout for t in plan.values())) for s in times]


def recorded_occupancy(inputs, output):
    config = spec()['config']
    own = inquiry_policies._pending(inputs['patient_pending'], inputs['now'], own=True)
    plan = {r['hospital']: r['planned_offset_seconds'] for r in output['evidence']['candidate_plan']}
    return plan_occupancy(plan, own, inputs['now'], config['observation_timeout_seconds'])


def force_initial_plan(plan, pool, feasible, plan_cost):
    """Original no-fresh-pending safety action, with every-wave feasibility."""
    trials = [(row, {**plan, row['hospital']: 0}) for row in pool]
    trials = [(row, trial) for row, trial in trials if feasible(trial)]
    reset = not trials
    if reset:
        trials = [(row, {h: 0 if h == row['hospital'] else None for h in plan}) for row in pool]
        if not all(feasible(trial) for _, trial in trials):
            raise AssertionError('no-fresh-pending single-contact safety plan must be feasible')
    row, trial = min(trials, key=lambda pair: (plan_cost(pair[1]), pair[0]['travel_seconds']))
    return row, trial, reset


def decide(name, *, now, candidates, observations=(), pending=(), patient_pending=(), max_new,
           last_contact_at=None, time_context):
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
    context = horizon_context.validate_context(time_context, now=now)
    if own and {row['patient'] for row in own} != {context['patient_id']}:
        raise ValueError('time_context patient differs from patient_pending')
    horizon = context['evaluation_end_at'] - now
    contact_window = min(context['auto_inquiry_deadline_at'], context['evaluation_end_at']) - now
    edges = cell_edges(horizon, step)
    cells = len(edges)
    shifts = [None] + sorted({int(round(d / step)) for d in config['plan_offsets_seconds']
                              if d < contact_window})
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
        return row['travel_seconds'], masses_at_edges(cdf, total, edges)

    def new_item(row):
        h = row['hospital']
        mu, sigma = beliefs.mu(h), beliefs.sigma
        load = len(queue.get(h, ()))
        wait, residual_queue_records[h] = _queue_wait_estimate(
            h, now, queue.get(h, ()), history, beliefs, workers)
        cdf = lambda x: _lognorm_cdf(x - wait, mu, sigma)
        total = beliefs.reply(h) * beliefs.accept(h)
        new_distributions[h] = (row['travel_seconds'], cdf, total)
        return row['travel_seconds'], masses_at_edges(cdf, total, edges)

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
    new_distributions = {}
    shifted_items = {}
    items = {row['hospital']: new_item(row) for row in pool}
    lams = {row['hospital']: inquiry_cost(row) for row in pool}
    room = min(max_new, config['max_concurrent'] - (len(own) - stale))

    def planned_item(hospital, shift):
        if not shift:
            return items[hospital]
        key = (hospital, shift)
        if key not in shifted_items:
            travel, cdf, total = new_distributions[hospital]
            offset = shift * step
            shifted_items[key] = (travel, masses_at_edges(
                lambda x: cdf(x - offset), total, edges))
        return shifted_items[key]

    def plan_cost(plan):
        """plan: 병원 -> 지금부터 몇 칸 뒤 문의(0=지금). 문의비용은 그때까지 수용이 없을 때만 낸다."""
        chosen = [(c, h) for h, c in plan.items() if c is not None]
        all_items = current + [planned_item(h, c) for c, h in chosen]
        value = bounded_expected_cost(all_items, edges, failure)
        for c, h in chosen:
            value += lams[h] * (_no_accept_by(all_items, c) if c else 1.0)
        return value

    def occupancy(plan):
        seconds = {h: None if c is None else c*step for h,c in plan.items()}
        return plan_occupancy(seconds, own, now, config['observation_timeout_seconds'])

    def feasible(plan):
        return all(row['total_fresh_count'] <= config['max_concurrent'] for row in occupancy(plan))

    forced_plan_reset = False
    base = bounded_expected_cost(current, edges, failure)
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
                if not feasible(trial):
                    continue
                value = plan_cost(trial)
                if value < best_cost - 1e-9:
                    plan, best_cost, improved = trial, value, True
        if not improved:
            break
    send = [row['hospital'] for row in pool if plan[row['hospital']] == 0]
    # 안전 규칙: 180초 이내 대기 문의가 하나도 없는데 지금 보낼 곳도 없으면, 계획에서 가장
    # 비용이 낮아지는 한 곳을 지금 문의한다. 수용 가능성이 낮다고 추정돼도 멈추지 않는다.
    if contact_window > 0 and len(own) == stale and not send and pool and room > 0:
        forced, plan, forced_plan_reset = force_initial_plan(plan, pool, feasible, plan_cost)
        best_cost = plan_cost(plan)
        send = [forced['hospital']]
    scored = [dict(hospital=row['hospital'],
                   planned_offset_seconds=None if plan[row['hospital']] is None else plan[row['hospital']] * step,
                   inquiry_cost=lams[row['hospital']],
                   reply_probability=round(beliefs.reply(row['hospital']), 4),
                   accept_probability=round(beliefs.accept(row['hospital']), 4),
                   queue_length=len(queue.get(row['hospital'], ())),
                   travel_seconds=row['travel_seconds']) for row in pool]

    if contact_window <= 0:
        reason = 'automatic_inquiry_deadline'
    elif max_new == 0:
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
    evidence['time_horizon'] = dict(schema_version=1, rule=HORIZON_RULE,
        source_sha256=dict(SOURCE_SHA256), context=context,
        observation_remaining_seconds=horizon, new_contact_window_seconds=contact_window,
        cell_edges_seconds=list(edges),
        cell_representatives_seconds=[(left + right) / 2.0
            for left, right in zip([0, *edges[:-1]], edges)],
        allowed_plan_offsets_seconds=[shift * step for shift in shifts if shift is not None],
        pending_reply_scope='retain A-after replies through inclusive lab H; A stops only new inquiries',
        evaluation_scope='fixed lab measurement only; no live pending-reply expiry at A')
    if not feasible(plan):
        raise AssertionError('final every-wave forecast cap violated')
    evidence['wavecap_plan'] = dict(schema_version=1, rule=WAVECAP_RULE,
        foundation_source_sha256=FOUNDATION_SOURCE_SHA256, maximum_concurrent=config['max_concurrent'],
        observation_timeout_seconds=config['observation_timeout_seconds'], own_scope='normalized eligible patient_pending',
        occupancy=occupancy(plan), forced_plan_reset=forced_plan_reset,
        unrounded_planned_expected_cost=best_cost, scope=WAVECAP_SCOPE)
    return dict(send=send, reason=reason, evidence=evidence,
                next_review_at=now + policy['review_seconds'])
