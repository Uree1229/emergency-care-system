"""One simultaneous offset exchange in a frozen, complete public H plan.

This scores the same H distributions and costs. It does not run a new planner,
predict actual future replies, advance the clock, or repair H's future-cap
relaxation. Only the current fallback owner's predicted metrics are guarded.
"""
from copy import deepcopy
import hashlib
import math
from pathlib import Path
from server import inquiry_policies
from . import horizon_policy as H, horizon_context

H_SOURCE_SHA256 = '5061be48175098ce6a30be2af0f0721852e2d67d8bd75ab475a202bcee3ce387'
if hashlib.sha256(Path(H.__file__).read_bytes()).hexdigest() != H_SOURCE_SHA256:
    raise ValueError('frozen swap H model foundation differs')
PROBABILITY_EPSILON = 1e-12
WAIT_EPSILON = 1e-9
RULE = 'single_atomic_immediate_to_nonimmediate_offset_exchange_under_owner_P_W_J_Q_Pareto_guards'
SCOPE = 'same public origin and frozen H distributions; only one current-owner exchange; original offset histogram, planned count and immediate count preserved; no future-cap correction, other-owner nonregression, actual-success or waiting-time guarantee'


def plan_summary(plan):
    offsets = sorted({v for v in plan.values() if v is not None})
    histogram = [dict(offset_seconds=None, count=sum(v is None for v in plan.values()))]
    histogram += [dict(offset_seconds=v, count=sum(x == v for x in plan.values())) for v in offsets]
    return dict(offset_histogram=histogram, planned_count=sum(v is not None for v in plan.values()),
        immediate_count=sum(v == 0 for v in plan.values()), planned_hospitals=[h for h,v in plan.items() if v is not None])


def build_model(inputs, proposal):
    config = H.spec()['config']; now = inputs['now']
    context = horizon_context.validate_context(inputs['time_context'], now=now)
    horizon = context['evaluation_end_at'] - now
    edges = H.cell_edges(horizon, config['grid_seconds'])
    history = inquiry_policies._observations(inputs['observations'], now, config['history_window_seconds'])
    pending = inquiry_policies._pending(inputs['pending'], now)
    own = inquiry_policies._pending(inputs['patient_pending'], now, own=True)
    beliefs = H.Beliefs(history, now, config)
    pool = inquiry_policies._candidates(inputs['candidates'])[:min(config['max_candidates_evaluated'], inputs['max_new'])]
    queue = {}
    for row in pending: queue.setdefault(row['hospital'], []).append(row)
    last_reply = {}
    for row in history:
        if row['outcome'] != 'timeout': last_reply.setdefault(row['hospital'], []).append(row)
    own_items = []
    for row in own:
        h, submitted = row['hospital'], row['submitted_at']; mu, sigma = beliefs.mu(h), beliefs.sigma
        r, a = beliefs.reply(h), beliefs.accept(h)
        ahead = sum(1.0 if q['submitted_at'] < submitted else 0.5 for q in queue.get(h, ())
            if q['submitted_at'] <= submitted and q['patient'] != row['patient'])
        if ahead > 0:
            wait = ahead * beliefs.mean_service(h) / config['assumed_workers']
            cdf = lambda x, wait=wait, mu=mu, sigma=sigma: H._lognorm_cdf(x-wait, mu, sigma)
            total = r*a; mode = 'queued'; spent = f0 = 0.0
        else:
            freed = [o['observed_at'] for o in last_reply.get(h, ()) if o['submitted_at'] <= submitted]
            spent = max(0.0, now-max([submitted, *freed])); f0 = H._lognorm_cdf(spent, mu, sigma)
            alive = max(1e-9, 1.0-f0); reply_left = r*alive/max(1e-9, 1.0-r*f0)
            cdf = lambda x, spent=spent, mu=mu, sigma=sigma, f0=f0, alive=alive: (H._lognorm_cdf(spent+x, mu, sigma)-f0)/alive
            total = reply_left*a; mode = 'residual'; wait = 0.0
        own_items.append(dict(hospital=h, travel_seconds=row['travel_seconds'], mode=mode, wait_seconds=wait,
            spent_seconds=spent, f0=f0, total_probability=total, service_mu=mu, service_sigma=sigma,
            masses=H.masses_at_edges(cdf, total, edges)))
    candidates = []
    for row in pool:
        h = row['hospital']
        wait, record = H._queue_wait_estimate(h, now, queue.get(h, ()), history, beliefs, config['assumed_workers'])
        lam = config['lambda_seconds'] * (1+len(queue.get(h, ())) if config['congestion_aware'] else 1)
        candidates.append(dict(hospital=h, travel_seconds=row['travel_seconds'], residual_wait_seconds=wait,
            service_mu=beliefs.mu(h), service_sigma=beliefs.sigma, total_probability=beliefs.reply(h)*beliefs.accept(h),
            inquiry_cost_seconds=lam, residual_queue_record=record))
    rows = proposal['evidence']['candidate_plan']
    if [r['hospital'] for r in rows] != [r['hospital'] for r in candidates]:
        raise ValueError('complete original H pool or order differs')
    window = min(context['auto_inquiry_deadline_at'], context['evaluation_end_at'])-now
    allowed = sorted({int(round(d/config['grid_seconds']))*config['grid_seconds'] for d in config['plan_offsets_seconds'] if d < window})
    plan = {row['hospital']: row['planned_offset_seconds'] for row in rows}
    if any(v is not None and (type(v) is not int or v not in allowed) for v in plan.values()):
        raise ValueError('complete original H plan has an invalid offset')
    if proposal['send'] != [h for h,offset in plan.items() if offset == 0]:
        raise ValueError('original H immediate sends differ from complete plan')
    return dict(schema_version=1, horizon_source_sha256=H_SOURCE_SHA256, now=now,
        cell_edges_seconds=edges, grid_seconds=config['grid_seconds'], failure_penalty_seconds=config['failure_penalty_seconds'],
        normalized_own_pending=own, own_items=own_items, candidates=candidates, original_plan=plan,
        original_plan_summary=plan_summary(plan), scope=SCOPE)


def metrics(model, plan):
    if list(plan) != [row['hospital'] for row in model['candidates']]:
        raise ValueError('scored plan pool or order differs')
    edges = model['cell_edges_seconds']; items = [(row['travel_seconds'], row['masses']) for row in model['own_items']]
    selected = []
    for row in model['candidates']:
        offset = plan[row['hospital']]
        if offset is None: continue
        wait = row['residual_wait_seconds']
        cdf = lambda x, row=row, wait=wait, offset=offset: H._lognorm_cdf(x-offset-wait, row['service_mu'], row['service_sigma'])
        mass = H.masses_at_edges(cdf, row['total_probability'], edges)
        items.append((row['travel_seconds'], mass)); selected.append((offset, row['inquiry_cost_seconds']))
    failure = math.prod(max(0.0, 1.0-sum(mass)) for _,mass in items)
    wait = H.bounded_expected_cost([(0, mass) for _,mass in items], edges, 0)
    arrival = H.bounded_expected_cost(items, edges, model['failure_penalty_seconds'])
    contact = 0.0
    for offset, lam in selected:
        contact += lam*(H._no_accept_by(items, offset//model['grid_seconds']) if offset else 1.0)
    return dict(selection_probability=1.0-failure, failure_probability=failure, capped_wait_seconds=wait,
        arrival_failure_cost_seconds=arrival, planned_contact_cost_seconds=contact, total_cost_seconds=arrival+contact)


def choose(trials):
    admitted = [row for row in trials if row['admitted']]
    if not admitted:
        return dict(admissible_trial_indices=[], wait_band_trial_indices=[], cost_band_trial_indices=[],
            selected_trial_index=None, baseline_fallback=True)
    minimum = min(row['metrics']['capped_wait_seconds'] for row in admitted)
    waiting = [row for row in admitted if row['metrics']['capped_wait_seconds'] <= minimum+WAIT_EPSILON]
    cost = min(row['metrics']['total_cost_seconds'] for row in waiting)
    final = [row for row in waiting if row['metrics']['total_cost_seconds'] <= cost+WAIT_EPSILON]
    return dict(admissible_trial_indices=[r['trial_index'] for r in admitted], wait_band_trial_indices=[r['trial_index'] for r in waiting],
        cost_band_trial_indices=[r['trial_index'] for r in final], selected_trial_index=final[0]['trial_index'], baseline_fallback=False)


def guard_metrics(before, value):
    return dict(selection_probability_nonregression=value['selection_probability'] >= before['selection_probability']-PROBABILITY_EPSILON,
        capped_wait_strict_improvement=value['capped_wait_seconds'] < before['capped_wait_seconds']-WAIT_EPSILON,
        arrival_cost_nonregression=value['arrival_failure_cost_seconds'] <= before['arrival_failure_cost_seconds']+WAIT_EPSILON,
        planned_contact_cost_nonregression=value['planned_contact_cost_seconds'] <= before['planned_contact_cost_seconds']+WAIT_EPSILON)


def evaluate(inputs, proposal):
    model = build_model(inputs, proposal); baseline = model['original_plan']; before = metrics(model, baseline)
    candidates = model['candidates']; trials = []
    for i, outgoing in enumerate(candidates):
        u = outgoing['hospital']
        if baseline[u] != 0: continue
        for j, incoming in enumerate(candidates):
            v = incoming['hospital']
            if baseline[v] == 0: continue
            plan = deepcopy(baseline); plan[u], plan[v] = plan[v], plan[u]
            summary = plan_summary(plan)
            for key in ('offset_histogram', 'planned_count', 'immediate_count'):
                if summary[key] != model['original_plan_summary'][key]:
                    raise AssertionError('atomic exchange changed the original plan shape')
            value = metrics(model, plan)
            checks = guard_metrics(before, value)
            trials.append(dict(trial_index=len(trials), outgoing_pool_index=i, incoming_pool_index=j, outgoing_hospital=u,
                incoming_hospital=v, original_incoming_offset_seconds=baseline[v], plan=plan, plan_summary=summary,
                planned_hospital_set_preserved=set(summary['planned_hospitals']) == set(model['original_plan_summary']['planned_hospitals']),
                metrics=value, checks=checks, admitted=all(checks.values()), rejection_reasons=[key for key,passed in checks.items() if not passed]))
    if len(trials) > 9:
        raise AssertionError('fixed pool6 atomic exchange inventory exceeds nine')
    selection = choose(trials); index = selection['selected_trial_index']
    return dict(schema_version=1, rule=RULE, scope=SCOPE, model=model, baseline_metrics=before, trials=trials,
        selection=selection, selected_plan=deepcopy(baseline if index is None else trials[index]['plan']),
        selected_metrics=deepcopy(before if index is None else trials[index]['metrics']),
        tolerances=dict(probability=PROBABILITY_EPSILON, seconds=WAIT_EPSILON),
        one_exchange_only=True, original_future_cap_relaxation_unchanged=True)
