"""Independent saved-record audit of two registered first-inquiry decisions.

Only stdlib and SHA-bound independent auditors run. H proposals are checked
but their coordinate-descent choices remain source-bound. The new finite
membership table, its constraints and winner are independently enumerated.
Physical SQLite atomicity is established by frozen adapter source/rollback
tests; ordinary saved journal rows alone cannot prove transaction boundaries.
"""
from collections import Counter
from copy import deepcopy
import hashlib
from itertools import combinations
import json
import math
from pathlib import Path

from . import horizon_audit as horizon

HORIZON_AUDITOR_SHA256 = 'b86c5f3e1891f295004e76c461de05f5248fb662518e3baabf21c532a0a8d5bb'
BASE_SOURCE_SHA256 = '5061be48175098ce6a30be2af0f0721852e2d67d8bd75ab475a202bcee3ce387'
ROOT = Path(__file__).resolve().parents[1]
if hashlib.sha256(Path(horizon.__file__).read_bytes()).hexdigest() != HORIZON_AUDITOR_SHA256:
    raise ValueError('frozen independent H auditor differs')
if hashlib.sha256((ROOT/'inquiry_lab/horizon_policy.py').read_bytes()).hexdigest() != BASE_SOURCE_SHA256:
    raise ValueError('frozen H foundation differs')
parent = horizon.parent
JointFirstAuditError = horizon.HorizonAuditError
require, number, EPOCH = horizon.require, horizon.number, horizon.EPOCH
NAME = 'eat_joint_first_l15_f'
BASE_POLICY = horizon.NAME
RULE = 'joint_first_same_count_current_only'
WAIT_EPSILON = COST_EPSILON = 1e-9
PROBABILITY_EPSILON = 1e-12
INPUT_KEYS = horizon.INPUT_KEYS | {'time_context'}
CALL_KEYS = horizon.CALL_KEYS | {'workflow_snapshot'}
JOB_FIELDS = horizon.JOB_KEYS | {'next_evaluation_at'}
PATIENT_FIELDS = {'id', 'state', 'version'}
JOINT_CONTEXT_KEYS = {'schema_version', 'now', 'event_prefix_max', 'decision_prefix_max', 'eligible', 'pair_patient_ids'}
NEW_INSTRUMENTATION = dict(original_runner='inquiry_lab.runner._branch',
    actual_workflow='inquiry_lab.joint_first_runner.run_once', record_only=False, epoch=EPOCH,
    public_time_context_added=True, current_registered_first_contact_context_added=True,
    group_atomic_commit=True, maximum_group_size=2, eligibility_order='started_at,patient_id',
    base_horizon_preserved_for_fallback=True)


def _integer(value, label, minimum=0):
    return horizon._integer(value, label, minimum)


def _pid(value):
    require(type(value) in (int, str) and not (type(value) is str and not value), 'invalid patient identity')
    return type(value), value


def _same(actual, wanted, path='record'):
    """Exact discrete schema with tight independent floating-point checks."""
    if type(wanted) is dict:
        require(type(actual) is dict and set(actual) == set(wanted), 'fields differ: '+path)
        for key in wanted:
            _same(actual[key], wanted[key], path+'.'+str(key))
    elif type(wanted) is list:
        require(type(actual) is list and len(actual) == len(wanted), 'list length differs: '+path)
        for index, (left, right) in enumerate(zip(actual, wanted)):
            _same(left, right, path+'['+str(index)+']')
    elif type(wanted) is float:
        require(type(actual) in (int, float) and math.isfinite(actual)
            and math.isclose(actual, wanted, rel_tol=1e-12, abs_tol=1e-9), 'number differs: '+path)
    else:
        require(type(actual) is type(wanted) and actual == wanted, 'value differs: '+path)


def context_from_registry(now, event_prefix, decision_prefix, registry):
    """Independent public eligibility, with completeness checked separately."""
    _integer(now, 'current clock'); _integer(event_prefix, 'event prefix'); _integer(decision_prefix, 'decision prefix')
    require(type(registry) is dict and set(registry) == {'patients', 'jobs', 'inquiries', 'decisions'},
        'unknown or incomplete public registry snapshot')
    people, jobs = {}, {}
    for row in registry['patients']:
        require(type(row) is dict and set(row) == PATIENT_FIELDS, 'invalid public patient projection')
        key = _pid(row['id']); require(key not in people, 'duplicate public patient')
        _integer(row['version'], 'public patient version', 1); people[key] = row
    for row in registry['jobs']:
        require(type(row) is dict and set(row) == JOB_FIELDS, 'invalid public job projection')
        key = _pid(row['patient_id']); require(key not in jobs and key in people, 'duplicate or orphan public job')
        for field in ('version', 'started_at', 'deadline_at', 'next_evaluation_at'):
            _integer(row[field], 'public job '+field, 1 if field == 'version' else 0)
        require(type(row['enabled']) is int and row['enabled'] in (0, 1), 'invalid public enabled flag')
        jobs[key] = row
    contacted, decided = Counter(), Counter(); decision_ids = set()
    for row in registry['inquiries']:
        require(type(row) is dict and set(row) == {'patient_id'} and _pid(row['patient_id']) in people,
            'invalid public inquiry projection')
        contacted[_pid(row['patient_id'])] += 1
    for row in registry['decisions']:
        require(type(row) is dict and set(row) == {'id', 'at', 'patient_id'}, 'invalid public decision projection')
        _integer(row['id'], 'public decision id', 1); _integer(row['at'], 'public decision clock')
        require(row['id'] <= decision_prefix and row['id'] not in decision_ids and row['at'] <= now
            and _pid(row['patient_id']) in people, 'future/duplicate/orphan public decision')
        decision_ids.add(row['id']); decided[_pid(row['patient_id'])] += 1
    eligible = []
    for key, patient in people.items():
        job = jobs.get(key)
        if job is None or contacted[key] or decided[key]:
            continue
        if (patient['state'] == 'WAITING' and job['enabled'] == 1 and job['policy_name'] == NAME
                and job['started_at'] <= now and job['next_evaluation_at'] <= now < job['deadline_at']):
            require(job['started_at'] < job['deadline_at'], 'invalid eligible workflow interval')
            eligible.append(dict(patient=deepcopy(patient), job=deepcopy(job), inquiry_count=0, decision_count=0))
    eligible.sort(key=lambda row: (row['job']['started_at'], (0, row['patient']['id'])
        if type(row['patient']['id']) is int else (1, row['patient']['id'])))
    return dict(schema_version=1, now=now, event_prefix_max=event_prefix, decision_prefix_max=decision_prefix,
        eligible=eligible, pair_patient_ids=[r['patient']['id'] for r in eligible[:2]] if len(eligible) >= 2 else [])


def audit_registry(events, decisions, registry, now, event_prefix, decision_prefix):
    """Bind the complete public roster to current committed journal prefixes.

    State/version reconstruction covers the frozen lab's creation, confirmation
    and cancellation paths. Silent workflow next-review/version updates remain
    captured public-row provenance, not a claim of event-only reconstruction.
    """
    context = context_from_registry(now, event_prefix, decision_prefix, registry)
    visible = [e for e in events if e['id'] <= event_prefix]
    require(all(type(e['at']) is int and e['at'] <= now for e in visible), 'future event inside committed registry prefix')
    births = [e for e in visible if e['type'] == 'patient_created']
    require(len({_pid(e['patient_id']) for e in births}) == len(births), 'duplicate public patient creation')
    people = {_pid(p['id']): p for p in registry['patients']}
    require(set(people) == {_pid(e['patient_id']) for e in births}, 'public roster omitted or invented registered patient')
    for birth in births:
        key = _pid(birth['patient_id']); state, version = 'WAITING', 1
        for event in visible:
            if event.get('patient_id') is None or _pid(event['patient_id']) != key:
                continue
            if event['id'] <= birth['id']:
                continue
            if event['type'] == 'destination_confirmed': state, version = 'CONFIRMED', version+1
            elif event['type'] == 'patient_cancelled': state, version = 'CANCELLED', version+1
            elif event['type'] == 'patient_updated': version += 1
        require(people[key]['state'] == state and people[key]['version'] == version,
            'public patient state/version differs from frozen lab journal transitions')
    jobs = {_pid(j['patient_id']): j for j in registry['jobs']}
    workflow_owners = {_pid(e['patient_id']) for e in visible if e['type'] == 'inquiry_workflow_started'
        or e['type'] == 'inquiry_workflow_paused' and 'deadline_seconds' in parent._data(e)}
    require(set(jobs) == workflow_owners, 'public job roster omitted or invented workflow')
    for key, job in jobs.items():
        starts = [e for e in visible if e.get('patient_id') is not None and _pid(e['patient_id']) == key
            and (e['type'] == 'inquiry_workflow_started' or e['type'] == 'inquiry_workflow_paused'
                 and 'deadline_seconds' in parent._data(e))]
        latest = max(starts, key=lambda e: e['id']); duration = parent._data(latest)['deadline_seconds']
        _integer(duration, 'public workflow duration', 1)
        require(job['started_at'] == latest['at'] and job['deadline_at'] == latest['at']+duration,
            'public workflow A differs from latest creation/restart')
        enabled = 0; policy = None
        for event in visible:
            if event.get('patient_id') is None or _pid(event['patient_id']) != key: continue
            if event['type'] == 'inquiry_workflow_started': enabled = 1
            elif event['type'] in ('inquiry_workflow_paused', 'inquiry_workflow_exhausted',
                    'destination_confirmed', 'patient_cancelled'): enabled = 0
            if event['type'] in ('inquiry_workflow_started', 'inquiry_workflow_paused', 'inquiry_policy_changed'):
                policy = parent._data(event).get('policy_name', policy)
        require(job['enabled'] == enabled and job['policy_name'] == policy,
            'public workflow enabled/policy differs from public lifecycle')
        if people[key]['state'] != 'WAITING':
            require(job['enabled'] == 0, 'finished public patient still marked enabled')
    inquiry_ids = {}
    for event in visible:
        if event['type'] in ('inquiry_sent', 'inquiry_retried'):
            iid = parent._data(event).get('inquiry_id'); _integer(iid, 'public inquiry id', 1)
            owner = _pid(event['patient_id'])
            require(iid not in inquiry_ids or inquiry_ids[iid] == owner, 'inquiry identity changed owner')
            inquiry_ids[iid] = owner
    require(Counter(inquiry_ids.values()) == Counter(_pid(q['patient_id']) for q in registry['inquiries']),
        'public inquiry roster differs from all prior query identities')
    expected_decisions = [{k: row[k] for k in ('id', 'at', 'patient_id')}
        for row in decisions if row['id'] <= decision_prefix]
    _same(registry['decisions'], expected_decisions, 'public decision prefix')
    queried = set(inquiry_ids.values()); decided = {_pid(d['patient_id']) for d in expected_decisions}
    for key, job in jobs.items():
        if job['enabled'] and key not in queried and key not in decided:
            require(job['next_evaluation_at'] == job['started_at'],
                'untouched first workflow fabricated a silent future review time')
            lifecycle = [e for e in visible if e.get('patient_id') is not None
                and _pid(e['patient_id']) == key and e['type'] in ('inquiry_workflow_started',
                    'inquiry_workflow_paused', 'inquiry_policy_changed', 'inquiry_workflow_exhausted')]
            # Before any inquiry or decision, none of the workflow's silent
            # pending/ready updates can run. Public lifecycle changes are the
            # only possible version increments after initial insertion.
            require(job['version'] == len(lifecycle), 'untouched first workflow version lacks public lifecycle support')
    return context


def _cost(items, bins, horizon_seconds, failure_penalty):
    ordered = sorted(items, key=lambda row: row[0]); remaining = [1.]*len(ordered); cost = 0.
    for index, (left, right) in enumerate(bins):
        if math.prod(remaining) <= 1e-12: return cost
        after = [max(0., remaining[i]-row[1][index]) for i, row in enumerate(ordered)]
        for i, (travel, masses) in enumerate(ordered):
            if remaining[i] > 1e-12:
                cost += masses[index]*math.prod(after[:i])*math.prod(remaining[i+1:])*((left+right)/2+travel)
        remaining = after
    return cost+math.prod(remaining)*(horizon_seconds+failure_penalty)


def member_model(inputs, proposal):
    horizon.audit_horizon(inputs, proposal)
    model = horizon.model_from_inputs(inputs); beliefs = parent._beliefs(inputs['observations'], inputs['now'])
    hospitals = []
    for candidate, queue in zip(model['pool'], model['residual_rows']):
        hid = candidate['hospital']; reply, accept, mu, sigma = beliefs(hid)
        hospitals.append(dict(hospital=hid, travel_seconds=candidate['travel_seconds'], service_mu=mu,
            service_sigma=sigma, reply_probability=reply, accept_probability=accept,
            mean_service_seconds=math.exp(mu+.5*sigma*sigma), assumed_workers=1,
            residual_wait_seconds=queue['estimated_wait_seconds'], inquiry_cost_seconds=15))
    return dict(horizon_seconds=model['horizon'], cell_edges_seconds=[right for _, right in model['bins']],
        hospitals=hospitals, failure_penalty_seconds=1500)


def member_metrics(model, selected, extra_wait):
    bins = horizon.intervals(model['horizon_seconds']); rows = {r['hospital']: r for r in model['hospitals']}
    items, inquiry_cost = [], 0.
    for hid in selected:
        row = rows[hid]; wait = row['residual_wait_seconds']+extra_wait.get(hid, 0.)
        cdf = lambda x, wait=wait, row=row: parent._cdf(x-wait, row['service_mu'], row['service_sigma'])
        items.append((row['travel_seconds'], horizon.masses(cdf, row['reply_probability']*row['accept_probability'], bins)))
        inquiry_cost += row['inquiry_cost_seconds']
    failure = math.prod(max(0., 1-sum(masses)) for _, masses in items)
    arrival = _cost(items, bins, model['horizon_seconds'], 1500)
    return dict(capped_wait_seconds=_cost([(0, masses) for _, masses in items], bins, model['horizon_seconds'], 0),
        selection_probability=1-failure, failure_probability=failure,
        arrival_failure_cost_seconds=arrival, immediate_contact_cost_seconds=inquiry_cost,
        total_cost_seconds=arrival+inquiry_cost)


def combination(models, selected):
    earlier = {r['hospital']: r for r in models[0]['hospitals']}
    extra = {hid: earlier[hid]['mean_service_seconds']/earlier[hid]['assumed_workers']
        for hid in selected[1] if hid in selected[0]}
    metrics = [member_metrics(models[0], selected[0], {}), member_metrics(models[1], selected[1], extra)]
    return dict(hospitals=[list(x) for x in selected], member_metrics=metrics,
        later_member_extra_wait_seconds=extra, sum_capped_wait_seconds=sum(m['capped_wait_seconds'] for m in metrics))


def enumerate_pairs(models, baseline_sends):
    counts = [len(s) for s in baseline_sends]; pools = [[r['hospital'] for r in m['hospitals']] for m in models]
    choices = [list(combinations(pool, k)) for pool, k in zip(pools, counts)]
    baseline = combination(models, baseline_sends); table = []; baseline_index = None
    for first in choices[0]:
        for second in choices[1]:
            row = combination(models, [first, second])
            row['feasible'] = all(m['selection_probability'] >= b['selection_probability']-PROBABILITY_EPSILON
                and m['total_cost_seconds'] <= b['total_cost_seconds']+COST_EPSILON
                for m, b in zip(row['member_metrics'], baseline['member_metrics']))
            row['strict_wait_improvement'] = baseline['sum_capped_wait_seconds']-row['sum_capped_wait_seconds'] > WAIT_EPSILON
            if row['hospitals'] == baseline_sends: baseline_index = len(table)
            table.append(row)
    require(baseline_index is not None and 0 < len(table) <= 400, 'missing baseline or excessive finite pair table')
    feasible = [i for i, row in enumerate(table) if row['feasible']]
    require(bool(feasible), 'baseline pair omitted from feasible set')
    minimum = min(table[i]['sum_capped_wait_seconds'] for i in feasible)
    best = next(i for i in feasible if table[i]['sum_capped_wait_seconds'] <= minimum+WAIT_EPSILON)
    applied = baseline['sum_capped_wait_seconds']-table[best]['sum_capped_wait_seconds'] > WAIT_EPSILON
    return dict(cardinalities=counts, candidate_pools=pools, baseline_combination=baseline, combinations=table,
        baseline_index=baseline_index, minimum_feasible_wait_seconds=minimum,
        best_index=best, selected_index=best if applied else baseline_index, applied=applied)


def time_context_from_prefix(events, inputs, member, event_prefix, decision_prefix, committed_decision=None):
    """Joint identity version of public A/H reconstruction, with no producer."""
    now = inputs['now']; pid = member['patient_id']; source = member['workflow_snapshot']
    require(type(source) is dict and set(source) == {'apply_snapshot', 'job'}, 'missing checked member snapshot')
    snap, job = source['apply_snapshot'], source['job']
    require(type(snap) is dict and set(snap) == horizon.APPLY_KEYS and type(job) is dict
        and set(job) == horizon.JOB_KEYS, 'unknown checked snapshot/job fields')
    require(_pid(snap['patient_id']) == _pid(pid) == _pid(job['patient_id'])
        and type(snap['now']) is int and 0 <= snap['now'] <= now
        and job['policy_name'] == NAME and type(job['enabled']) is int and job['enabled'] == 1,
        'checked member identity/clock/active policy differs')
    for field in ('job_version', 'patient_version', 'revision'):
        _integer(snap[field], 'checked '+field, 0 if field == 'revision' else 1)
    require(type(job['version']) is int and job['version'] == snap['job_version'], 'checked job version differs')
    if committed_decision is not None:
        require(type(committed_decision['job_version']) is int
            and committed_decision['job_version'] == snap['job_version'], 'committed member job version differs')
    visible = [e for e in events if e['id'] <= event_prefix and e.get('patient_id') is not None
        and _pid(e['patient_id']) == _pid(pid)]
    require(all(type(e['at']) is int and 0 <= e['at'] <= now for e in visible
        if e['type'] in ('patient_created', 'inquiry_workflow_started')), 'future member public boundary event')
    births = [e for e in visible if e['type'] == 'patient_created']
    starts = [e for e in visible if e['type'] == 'inquiry_workflow_started']
    require(len(births) == 1 and starts, 'member creation/workflow-start prefix missing')
    birth, start = births[0], max(starts, key=lambda e: e['id']); data = parent._data(start)
    duration = _integer(data['deadline_seconds'], 'member automatic duration', 1)
    restarted = data.get('restarted', False)
    require(type(restarted) is bool and birth['id'] < start['id'] and birth['at'] <= start['at'] <= now
        and job['started_at'] == start['at'] and job['deadline_at'] == start['at']+duration
        and data['policy_name'] == NAME and now < min(job['deadline_at'], birth['at']+300),
        'member public soft A/hard H or current workflow source differs')
    wanted = dict(patient_id=pid, job_version=job['version'], event_prefix_max=event_prefix,
        decision_prefix_max=decision_prefix, auto_inquiry_deadline_at=job['deadline_at'],
        evaluation_end_at=birth['at']+300, source=dict(
            workflow={key: job[key] for key in ('patient_id', 'version', 'started_at', 'deadline_at', 'enabled')},
            workflow_start_event=dict(id=start['id'], at=start['at'], type='inquiry_workflow_started',
                patient_id=pid, deadline_seconds=duration, restarted=restarted),
            patient_created_event=dict(id=birth['id'], at=birth['at'], type='patient_created', patient_id=pid),
            evaluation_protocol=dict(name='fixed_lab_patient_lifetime', duration_seconds=300)))
    _same(inputs['time_context'], wanted, 'member public time context')
    return wanted


def audit_pair(members, joint_context, outputs, evidence):
    """Rebuild the finite pair table and exact selected decision transformation."""
    require(type(members) is list and len(members) == 2 and type(joint_context) is dict
        and set(joint_context) == JOINT_CONTEXT_KEYS and len(joint_context['pair_patient_ids']) == 2,
        'joint policy needs exactly two current members')
    require(type(outputs) is list and len(outputs) == 2 and type(evidence) is dict,
        'joint output coverage differs')
    proposals = evidence['baseline_proposals']
    require(type(proposals) is list and len(proposals) == 2, 'two original H proposals missing')
    for index, member in enumerate(members):
        require(type(member) is dict and set(member) == {'patient_id', 'inputs', 'workflow_snapshot'}
            and _pid(member['patient_id']) == _pid(joint_context['pair_patient_ids'][index]),
            'member identity/order differs from current eligible pair')
        inputs = member['inputs']
        require(type(inputs) is dict and set(inputs) == INPUT_KEYS and not horizon._contains_private(inputs)
            and inputs['now'] == joint_context['now'] and not inputs['patient_pending']
            and inputs['last_contact_at'] is None and type(inputs['max_new']) is int and inputs['max_new'] == 10,
            'joint first member is not an untouched common-prefix public input')
        _same(inputs['time_context']['event_prefix_max'], joint_context['event_prefix_max'], 'member event prefix')
        _same(inputs['time_context']['decision_prefix_max'], joint_context['decision_prefix_max'], 'member decision prefix')
        row = joint_context['eligible'][index]; snap = member['workflow_snapshot']['apply_snapshot']
        job = member['workflow_snapshot']['job']
        require(snap['job_version'] == row['job']['version'] == job['version']
            and snap['patient_version'] == row['patient']['version']
            and all(job[k] == row['job'][k] for k in horizon.JOB_KEYS),
            'member checked snapshot differs from eligible public row')
    for key in ('observations', 'pending'):
        _same(members[0]['inputs'][key], members[1]['inputs'][key], 'shared '+key)
    require(members[0]['workflow_snapshot']['apply_snapshot']['revision']
        == members[1]['workflow_snapshot']['apply_snapshot']['revision'], 'members used different planning revision')
    models = [member_model(m['inputs'], proposal) for m, proposal in zip(members, proposals)]
    expected = enumerate_pairs(models, [p['send'] for p in proposals])
    wanted = dict(schema_version=1, rule=RULE, base_policy=BASE_POLICY, base_source_sha256=BASE_SOURCE_SHA256,
        joint_context=joint_context, baseline_proposals=proposals, model_inputs=models, **expected,
        tolerances=dict(wait_seconds=WAIT_EPSILON, cost_seconds=COST_EPSILON, probability=PROBABILITY_EPSILON),
        tie_rule='first original-pool combination within 1e-9 of global feasible minimum',
        scope='current-only immediate inquiries; H future plans set counts only; no future feasibility guarantee',
        externality='one earlier new job adds its belief mean / assumed workers to later shared hospital')
    _same(evidence, wanted, 'independent finite-pair evidence')
    selected = expected['combinations'][expected['selected_index']]; baseline = expected['baseline_combination']
    for index, (proposal, output) in enumerate(zip(proposals, outputs)):
        result = deepcopy(proposal); hospitals = selected['hospitals'][index]
        result['send'] = list(hospitals)
        result['reason'] = 'joint_first_current_only' if expected['applied'] else 'joint_first_baseline_current_only'
        ev = result['evidence']; ev['policy'] = NAME; ev['sent_hospitals'] = list(hospitals)
        ev['planned_expected_cost'] = round(selected['member_metrics'][index]['total_cost_seconds'], 3)
        for row in ev['candidate_plan']:
            row['planned_offset_seconds'] = 0 if row['hospital'] in hospitals else None
        ev['joint_first'] = dict(schema_version=1, mode='pair_current_only', member_index=index,
            current_only=True, applied=expected['applied'], baseline_send=list(proposal['send']),
            selected_send=list(hospitals), baseline_metrics=baseline['member_metrics'][index],
            selected_metrics=selected['member_metrics'][index],
            constraint_reference='common-prefix simultaneous immediate-only H sends')
        _same(output, result, 'committed current-only member output')
    return dict(joint_groups_checked=1, joint_combinations_checked=len(expected['combinations']),
        joint_groups_applied=int(expected['applied']), joint_members_checked=2)


GROUP_KEYS = {'id', 'now', 'event_prefix_max', 'decision_prefix_max', 'revision', 'context',
    'registry_snapshot', 'members', 'outputs', 'evidence', 'db_decision_ids', 'validated_before_write'}


def _audit_phase_order(calls):
    """At one runner clock all group calls precede its fallback calls."""
    fallback_seen_at = set()
    for call in calls:
        _integer(call['at'], 'recorded policy relative clock')
        if 'joint_group_id' in call:
            require(call['at'] not in fallback_seen_at, 'joint group followed a same-clock fallback call')
        else:
            fallback_seen_at.add(call['at'])


def _audit_calls(scenario, branch):
    events, calls, decisions = branch['db_events'], branch['policy_calls'], branch['db_decisions']
    groups = branch['joint_groups']; specs = {p['id']: p for p in scenario['patients']}
    require(type(events) is list and type(calls) is list and type(decisions) is list
        and len(calls) == len(decisions) and type(groups) is list, 'call/decision/group inventory differs')
    _audit_phase_order(calls)
    require(all(type(e['id']) is int and e['id'] > 0 for e in events)
        and [e['id'] for e in events] == sorted({e['id'] for e in events}), 'event IDs not unique and ordered')
    event_ids = {e['id'] for e in events}; sent = [e for e in events if e['type'] == 'inquiry_sent']
    group_map = {}; checks = Counter()
    for index, group in enumerate(groups, 1):
        require(type(group) is dict and set(group) == GROUP_KEYS and type(group['id']) is int and group['id'] == index,
            'unknown, missing or reordered joint-group fields')
        for key in ('now', 'event_prefix_max', 'decision_prefix_max', 'revision'):
            _integer(group[key], 'group '+key)
        require(type(group['validated_before_write']) is bool and group['validated_before_write'],
            'group lacks all-member validation before first write')
        require(group['event_prefix_max'] in event_ids and group['db_decision_ids']
            == [group['decision_prefix_max']+1, group['decision_prefix_max']+2],
            'joint decisions not the next two recorded decisions at common prefix')
        wanted = audit_registry(events, decisions, group['registry_snapshot'], group['now'],
            group['event_prefix_max'], group['decision_prefix_max'])
        _same(group['context'], wanted, 'complete current public eligibility')
        require(len(wanted['pair_patient_ids']) == 2, 'group created without two eligible current patients')
        checks.update(audit_pair(group['members'], wanted, group['outputs'], group['evidence']))
        for member in group['members']:
            require(member['workflow_snapshot']['apply_snapshot']['revision'] == group['revision'],
                'member was not captured at common planning revision')
        group_map[group['id']] = group
    declared, actual, group_calls = Counter(), Counter(), Counter()
    previous = 0; last_prefix = 0
    for call, decision in zip(calls, decisions):
        grouped = 'joint_group_id' in call
        expected_keys = CALL_KEYS | {'joint_group_id', 'joint_member_index'} if grouped else CALL_KEYS
        require(type(call) is dict and set(call) == expected_keys, 'unknown or missing policy-call fields')
        pid, at, inputs, output = call['patient_id'], call['at'], call['inputs'], call['output']
        prefix = call['db_event_prefix_max']; now = EPOCH+at
        require(pid in specs and type(at) is int and specs[pid]['at'] <= at < specs[pid]['at']+300,
            'policy used future or out-of-window patient')
        require(type(prefix) is int and prefix >= last_prefix and prefix in event_ids,
            'invalid or reordered committed event prefix')
        last_prefix = prefix
        require(type(inputs) is dict and set(inputs) == INPUT_KEYS and not horizon._contains_private(inputs)
            and inputs['now'] == now, 'private, future or unknown policy input')
        require(call['name'] == branch['policy'] == decision['policy_name'] == NAME
            and decision['patient_id'] == pid and decision['at'] == now and type(decision['id']) is int
            and decision['id'] == call['db_decision_id'] > previous, 'committed policy identity/clock differs')
        _same(output['send'], decision['hospital_ids'], 'committed sends')
        _same(output['reason'], decision['reason'], 'committed reason')
        _same(output['evidence'], decision['evidence'], 'committed evidence')
        if grouped:
            gid, index = call['joint_group_id'], call['joint_member_index']
            require(type(gid) is int and gid in group_map and type(index) is int and index in (0, 1),
                'unknown joint-group or member index')
            group = group_map[gid]; member = group['members'][index]
            require(group['now'] == now and prefix == group['event_prefix_max']
                and decision['id'] == group['db_decision_ids'][index]
                and previous == group['decision_prefix_max']+index,
                'group decisions were interleaved or used different common prefixes')
            _same(member, dict(patient_id=pid, inputs=inputs, workflow_snapshot=call['workflow_snapshot']), 'linked group member')
            _same(output, group['outputs'][index], 'linked group output')
            dprefix = group['decision_prefix_max']; group_calls[gid, index] += 1
        else:
            member = dict(patient_id=pid, inputs=inputs, workflow_snapshot=call['workflow_snapshot'])
            dprefix = previous
            require(output['evidence'].get('joint_first') == dict(schema_version=1, mode='horizon_fallback', current_only=False),
                'ungrouped decision changed the original H fallback')
            normalized = deepcopy(output); normalized['evidence'].pop('joint_first')
            normalized['evidence']['policy'] = BASE_POLICY
            checks.update(horizon.audit_horizon(inputs, normalized))
        context = time_context_from_prefix(events, inputs, member, prefix, dprefix, decision)
        require(context['evaluation_end_at'] == EPOCH+specs[pid]['at']+300,
            'public H differs from registered scenario patient')
        previous = decision['id']
        history, pending, own, _ = horizon._ledger(events, now, pid, prefix)
        travel = specs[pid]['travel_seconds']
        expected_own = [dict(row, travel_seconds=travel[row['hospital']]) for row in own if row['hospital'] in travel]
        require(inputs['observations'] == history and inputs['pending'] == pending
            and inputs['patient_pending'] == expected_own, 'common-prefix public observation ledger differs')
        earlier = [e for e in sent if e['id'] <= prefix and e['patient_id'] == pid]
        contacted = {e['hospital_id'] for e in earlier}
        require(type(inputs['max_new']) is int and inputs['max_new'] == 10-len(contacted)
            and inputs['last_contact_at'] == max((e['at'] for e in earlier if e['at'] <= now), default=None),
            'original contact budget/history differs')
        ranked = horizon._ranked(inputs['candidates'])
        require({r['hospital'] for r in ranked} == set(travel)-contacted
            and all(r['travel_seconds'] == travel[r['hospital']] for r in ranked),
            'currently eligible uncontacted offers/travel differ from frozen lab')
        require(type(output['send']) is list and len(set(output['send'])) == len(output['send'])
            and len(output['send']) <= inputs['max_new'] and set(output['send']) <= set(travel)-contacted
            and sum(now-r['submitted_at'] <= 180 for r in expected_own)+len(output['send']) <= 4,
            'contact count/candidate or original EAT concurrency constraint differs')
        for hid in output['send']:
            matching = [e for e in sent if e['patient_id'] == pid and e['hospital_id'] == hid
                and e['at'] == now and e['id'] > prefix]
            require(len(matching) == 1, 'decision lacks unique subsequent inquiry send')
            declared[matching[0]['id']] += 1
    require(group_calls == Counter({(gid, index): 1 for gid in group_map for index in (0, 1)}),
        'joint members/committed calls not covered exactly once')
    for group in groups:
        send_ids = [[e['id'] for e in sent if e['patient_id'] == m['patient_id']
            and e['id'] > group['event_prefix_max'] and e['at'] == group['now']]
            for m in group['members']]
        if all(send_ids):
            require(max(send_ids[0]) < min(send_ids[1]), 'new shared FIFO order differs from modeled member order')
    for event in sent:
        if event['patient_id'] in specs: actual[event['id']] += 1
    require(declared == actual, 'actual inquiries not covered exactly once by saved policy calls')
    return dict(policy_calls=len(calls), **dict(checks))


def audit_branch(scenario, branch):
    if branch.get('policy') != NAME:
        return horizon.audit_branch(scenario, branch)
    try:
        environment = hashlib.sha256(json.dumps(scenario, ensure_ascii=False, sort_keys=True,
            separators=(',', ':')).encode()).hexdigest()
        require(branch['environment_sha256'] == environment and branch['rule'] == 'immediate',
            'joint scenario binding or confirmation intervention differs')
        _same(branch['instrumentation'], NEW_INSTRUMENTATION, 'joint adapter provenance')
        checks = _audit_calls(scenario, branch); accounting = horizon._audit_trace_fixed(scenario, branch)
        total_j = sum(1800 if p['failed'] else p['estimated_arrival_seconds'] for p in branch['patients'])
        return dict(passed=True, **checks, **accounting, total_arrival_failure_cost_seconds=total_j,
            mean_arrival_failure_cost_seconds=total_j/len(branch['patients']) if branch['patients'] else None,
            finite_pair_table_independently_enumerated=True, public_registry_independently_reconstructed=True,
            H_coordinate_descent_independently_reexecuted=False, physical_transaction_boundary_from_raw_proved=False,
            atomicity_evidence_scope='all-member checked common snapshots, linked two next decisions and ordered subsequent sends are audited; physical atomicity requires frozen adapter source and second-member rollback tests',
            silent_workflow_field_scope='next-review/version remain captured public job provenance; zero-journal active review equals latest start; member versions link actual snapshots to committed decisions',
            scope='registered due WAITING zero-journal pair and complete committed public roster; common A/H/offer ledger; fixed per-person H proposal counts; every current-only pair, shared inferred workload, feasibility and winner; subsequent H math; actual FIFO/all-patient W/J/failure/contact/work')
    except (KeyError, TypeError, IndexError, AttributeError, OverflowError, StopIteration) as error:
        raise JointFirstAuditError('malformed saved joint-first artifact') from error


def audit_replay(replay):
    require(type(replay) is dict and replay['schema_version'] == 1 and type(replay['branches']) is list,
        'invalid complete replay')
    require(replay['arms'] == [[b['policy'], b['rule']] for b in replay['branches']]
        and len({tuple(a) for a in replay['arms']}) == len(replay['arms']) > 0, 'arm inventory differs')
    require(all(b['environment_sha256'] == replay['environment_sha256']
        and b['snapshot_sha256'] == replay['initial_snapshot_sha256'] for b in replay['branches']),
        'paired initial snapshots/environment differ')
    checked = [audit_branch(replay['scenario'], b) for b in replay['branches']]
    return dict(passed=True, branches=len(checked), branch_audits=checked, new_executions=0)
