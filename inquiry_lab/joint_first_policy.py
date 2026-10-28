"""Current-only, same-count coordination for two registered first decisions.

H supplies each person's simultaneous common-prefix contact count. Exhaustive
membership choice changes the coordination objective explicitly; model guards
do not promise observed success preservation or future plan feasibility.
"""
from copy import deepcopy
import hashlib
from itertools import combinations
from pathlib import Path

from server import inquiry_policies
from . import horizon_policy as original, horizon_context, joint_first_context

NAME = 'eat_joint_first_l15_f'
BASE_POLICY = 'eat_horizon_l15_f'
RULE = 'joint_first_same_count_current_only'
WAIT_EPSILON = 1e-9
COST_EPSILON = 1e-9
PROBABILITY_EPSILON = 1e-12
BASE_SOURCE_SHA256 = '5061be48175098ce6a30be2af0f0721852e2d67d8bd75ab475a202bcee3ce387'
if hashlib.sha256(Path(original.__file__).read_bytes()).hexdigest() != BASE_SOURCE_SHA256:
    raise ValueError('frozen joint-first horizon foundation differs')


def spec(name=NAME):
    if name != NAME:
        raise ValueError('the fixed joint-first candidate is ' + NAME)
    value = original.spec(BASE_POLICY)
    value.update(name=NAME, label='EAT l15 f, joint first inquiries at fixed per-person counts')
    return value


def decide(name, *, now, candidates, observations=(), pending=(), patient_pending=(), max_new,
           last_contact_at=None, time_context):
    """All unpaired and subsequent decisions use unchanged H calculation."""
    spec(name)
    result = original.decide(BASE_POLICY, now=now, candidates=candidates, observations=observations,
        pending=pending, patient_pending=patient_pending, max_new=max_new,
        last_contact_at=last_contact_at, time_context=time_context)
    result['evidence']['policy'] = NAME
    result['evidence']['joint_first'] = dict(schema_version=1, mode='horizon_fallback', current_only=False)
    return result


def _model(inputs, proposal):
    config = spec()['config']
    now = inputs['now']
    ranked = inquiry_policies._candidates(inputs['candidates'])
    pool = ranked[:min(config['max_candidates_evaluated'], inputs['max_new'])]
    history = inquiry_policies._observations(inputs['observations'], now, config['history_window_seconds'])
    pending = inquiry_policies._pending(inputs['pending'], now)
    beliefs = original.Beliefs(history, now, config)
    edges = proposal['evidence']['time_horizon']['cell_edges_seconds']
    hospitals = []
    for row in pool:
        hospital = row['hospital']
        queue = [item for item in pending if item['hospital'] == hospital]
        wait, _ = original._queue_wait_estimate(hospital, now, queue, history, beliefs, config['assumed_workers'])
        lam = config['lambda_seconds'] * (1+len(queue) if config['congestion_aware'] else 1)
        hospitals.append(dict(hospital=hospital, travel_seconds=row['travel_seconds'],
            service_mu=beliefs.mu(hospital), service_sigma=beliefs.sigma,
            reply_probability=beliefs.reply(hospital), accept_probability=beliefs.accept(hospital),
            mean_service_seconds=beliefs.mean_service(hospital), assumed_workers=config['assumed_workers'],
            residual_wait_seconds=wait, inquiry_cost_seconds=lam))
    assert [r['hospital'] for r in hospitals] == [r['hospital'] for r in proposal['evidence']['candidate_plan']]
    return dict(horizon_seconds=edges[-1], cell_edges_seconds=list(edges), hospitals=hospitals,
        failure_penalty_seconds=config['failure_penalty_seconds'])


def _metrics(model, selected, extra_wait):
    items, cost = [], 0.0
    by_hospital = {row['hospital']:row for row in model['hospitals']}
    for hospital in selected:
        row = by_hospital[hospital]
        wait = row['residual_wait_seconds'] + extra_wait.get(hospital, 0.0)
        cdf = lambda x, row=row, wait=wait: original._lognorm_cdf(
            x-wait, row['service_mu'], row['service_sigma'])
        masses = original.masses_at_edges(cdf,
            row['reply_probability']*row['accept_probability'], model['cell_edges_seconds'])
        items.append((row['travel_seconds'], masses))
        cost += row['inquiry_cost_seconds']
    fail = 1.0
    for _, masses in items:
        fail *= max(0.0, 1.0-sum(masses))
    wait = original.bounded_expected_cost([(0, masses) for _,masses in items],
        model['cell_edges_seconds'], 0)
    arrival = original.bounded_expected_cost(items, model['cell_edges_seconds'],
        model['failure_penalty_seconds'])
    return dict(capped_wait_seconds=wait, selection_probability=1.0-fail,
        failure_probability=fail, arrival_failure_cost_seconds=arrival,
        immediate_contact_cost_seconds=cost, total_cost_seconds=arrival+cost)


def _combination(models, selected):
    earlier = {row['hospital']:row for row in models[0]['hospitals']}
    overlap = [hospital for hospital in selected[1] if hospital in selected[0]]
    extra = {hospital:earlier[hospital]['mean_service_seconds']/earlier[hospital]['assumed_workers']
        for hospital in overlap}
    metrics = [_metrics(models[0], selected[0], {}), _metrics(models[1], selected[1], extra)]
    return dict(hospitals=[list(selected[0]), list(selected[1])], member_metrics=metrics,
        later_member_extra_wait_seconds=extra,
        sum_capped_wait_seconds=sum(row['capped_wait_seconds'] for row in metrics))


def _validate_members(members, context):
    if type(members) is not list or len(members) != 2 or len(context['pair_patient_ids']) != 2:
        raise ValueError('a joint first decision requires exactly two eligible public members')
    expected_inputs = {'now', 'candidates', 'observations', 'pending', 'patient_pending',
        'max_new', 'last_contact_at', 'time_context'}
    for index, member in enumerate(members):
        if type(member) is not dict or set(member) != {'patient_id', 'inputs', 'workflow_snapshot'}:
            raise ValueError('joint member contains missing or unknown fields')
        row = context['eligible'][index]
        pid = context['pair_patient_ids'][index]
        if type(member['patient_id']) is not type(pid) or member['patient_id'] != pid:
            raise ValueError('joint member is outside public workflow order')
        inputs = member['inputs']
        if type(inputs) is not dict or set(inputs) != expected_inputs or inputs['now'] != context['now']:
            raise ValueError('joint member original public inputs are incomplete or stale')
        if inputs['patient_pending'] or inputs['last_contact_at'] is not None:
            raise ValueError('joint first member already has an inquiry')
        source = member['workflow_snapshot']
        if type(source) is not dict or set(source) != {'apply_snapshot', 'job'}:
            raise ValueError('joint member workflow snapshot is missing')
        snapshot, job = source['apply_snapshot'], source['job']
        if type(snapshot) is not dict or set(snapshot) != {'patient_id','job_version','patient_version','revision','now'}:
            raise ValueError('joint member apply snapshot is incomplete')
        if type(job) is not dict or set(job) != {'patient_id','version','started_at','deadline_at','enabled','policy_name'}:
            raise ValueError('joint member job projection is incomplete')
        for key in ('job_version','patient_version','revision','now'):
            minimum = 1 if key in ('job_version','patient_version') else 0
            if type(snapshot[key]) is not int or snapshot[key] < minimum:
                raise ValueError('joint member snapshot clock/version is invalid')
        if (type(snapshot['patient_id']) is not type(pid) or type(job['patient_id']) is not type(pid) or
                snapshot['patient_id'] != pid or job['patient_id'] != pid or
                snapshot['job_version'] != row['job']['version'] or job['version'] != row['job']['version'] or
                snapshot['patient_version'] != row['patient']['version'] or snapshot['now'] > context['now'] or
                any(type(job[key]) is not type(row['job'][key]) or job[key] != row['job'][key] for key in job)):
            raise ValueError('joint member public snapshot identity differs')
        horizon_context.validate_context(inputs['time_context'], now=context['now'], patient_id=pid,
            job_version=job['version'], event_prefix_max=context['event_prefix_max'],
            decision_prefix_max=context['decision_prefix_max'])
        if inputs['time_context']['auto_inquiry_deadline_at'] != job['deadline_at']:
            raise ValueError('joint member automatic-inquiry boundary differs')
    # A common committed prefix must expose the same global public ledger.
    for key in ('observations', 'pending'):
        if members[0]['inputs'][key] != members[1]['inputs'][key]:
            raise ValueError('joint members do not share one current public ledger')
    if members[0]['workflow_snapshot']['apply_snapshot']['revision'] != members[1]['workflow_snapshot']['apply_snapshot']['revision']:
        raise ValueError('joint members do not share one planning revision')


def decide_joint(name, *, members, joint_context):
    spec(name)
    context = joint_first_context.validate_context(joint_context)
    _validate_members(members, context)
    proposals = [original.decide(BASE_POLICY, **deepcopy(member['inputs'])) for member in members]
    models = [_model(member['inputs'], proposal) for member,proposal in zip(members,proposals)]
    baseline_selected = [proposal['send'] for proposal in proposals]
    counts = [len(selected) for selected in baseline_selected]
    pools = [[row['hospital'] for row in model['hospitals']] for model in models]
    choices = [list(combinations(pool, count)) for pool,count in zip(pools,counts)]
    baseline = _combination(models, baseline_selected)
    table, baseline_index = [], None
    for first in choices[0]:
        for second in choices[1]:
            row = _combination(models, [first,second])
            row['feasible'] = all(
                current['selection_probability'] >= reference['selection_probability']-PROBABILITY_EPSILON and
                current['total_cost_seconds'] <= reference['total_cost_seconds']+COST_EPSILON
                for current,reference in zip(row['member_metrics'],baseline['member_metrics']))
            row['strict_wait_improvement'] = baseline['sum_capped_wait_seconds']-row['sum_capped_wait_seconds'] > WAIT_EPSILON
            if [list(first),list(second)] == baseline_selected:
                baseline_index = len(table)
            table.append(row)
    assert baseline_index is not None and len(table) <= 400
    feasible = [index for index,row in enumerate(table) if row['feasible']]
    minimum = min(table[index]['sum_capped_wait_seconds'] for index in feasible)
    best_index = next(index for index in feasible if table[index]['sum_capped_wait_seconds'] <= minimum+WAIT_EPSILON)
    applied = baseline['sum_capped_wait_seconds']-table[best_index]['sum_capped_wait_seconds'] > WAIT_EPSILON
    selected_index = best_index if applied else baseline_index
    selected = table[selected_index]
    decisions = []
    for index,(proposal,member) in enumerate(zip(proposals,members)):
        decision = deepcopy(proposal)
        hospitals = selected['hospitals'][index]
        decision['send'] = list(hospitals)
        decision['reason'] = 'joint_first_current_only' if applied else 'joint_first_baseline_current_only'
        evidence = decision['evidence']
        evidence['policy'] = NAME
        evidence['sent_hospitals'] = list(hospitals)
        evidence['planned_expected_cost'] = round(selected['member_metrics'][index]['total_cost_seconds'],3)
        for row in evidence['candidate_plan']:
            row['planned_offset_seconds'] = 0 if row['hospital'] in hospitals else None
        evidence['joint_first'] = dict(schema_version=1, mode='pair_current_only', member_index=index,
            current_only=True, applied=applied, baseline_send=list(baseline_selected[index]),
            selected_send=list(hospitals), baseline_metrics=deepcopy(baseline['member_metrics'][index]),
            selected_metrics=deepcopy(selected['member_metrics'][index]),
            constraint_reference='common-prefix simultaneous immediate-only H sends')
        decisions.append(decision)
    evidence = dict(schema_version=1, rule=RULE, base_policy=BASE_POLICY,
        base_source_sha256=BASE_SOURCE_SHA256, joint_context=context,
        baseline_proposals=deepcopy(proposals), model_inputs=models,
        cardinalities=counts, candidate_pools=pools, baseline_combination=baseline,
        combinations=table, baseline_index=baseline_index, minimum_feasible_wait_seconds=minimum,
        best_index=best_index, selected_index=selected_index, applied=applied,
        tolerances=dict(wait_seconds=WAIT_EPSILON,cost_seconds=COST_EPSILON,probability=PROBABILITY_EPSILON),
        tie_rule='first original-pool combination within 1e-9 of global feasible minimum',
        scope='current-only immediate inquiries; H future plans set counts only; no future feasibility guarantee',
        externality='one earlier new job adds its belief mean / assumed workers to later shared hospital')
    return dict(decisions=decisions, evidence=evidence)
