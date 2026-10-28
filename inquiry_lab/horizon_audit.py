"""Independent audit of explicit lab H and soft automatic-inquiry A.

Only standard-library arithmetic and SHA-bound independent parent auditors run.
No producer policy, server, runner, context builder or solver is imported. The
recorded coordinate-descent plan is checked, not searched again. In particular,
A limits new starts; it does not expire pending replies. H is the separately
declared lab cancellation/measurement boundary, including replies exactly at H.
"""
from collections import Counter
import hashlib
import json
import math
from pathlib import Path

from . import residual_audit as residual

ROOT = Path(__file__).resolve().parents[1]
RESIDUAL_AUDITOR_SHA256 = '528eddf407442d1489ccf26c58d14cf5cb4da6225f3560b60457b5611dd61191'
SOURCE_RESIDUAL_SHA256 = '8cf391ab5062de374d3c843edd3f6b1be98ac281df9217acc0538cbd98b838ef'
SOURCE_SHA256 = dict(residual_policy=SOURCE_RESIDUAL_SHA256,
    eat_policy='fc6a7e72dfaf8b632af0ab436baf30adeb0e6ec800262e29a69dd3064e8474ca')
if hashlib.sha256(Path(residual.__file__).read_bytes()).hexdigest() != RESIDUAL_AUDITOR_SHA256:
    raise ValueError('frozen independent residual auditor differs')
if hashlib.sha256((ROOT / 'inquiry_lab/residual_policy.py').read_bytes()).hexdigest() != SOURCE_RESIDUAL_SHA256:
    raise ValueError('frozen residual foundation source differs')
if hashlib.sha256((ROOT / 'inquiry_lab/eat_policy.py').read_bytes()).hexdigest() != SOURCE_SHA256['eat_policy']:
    raise ValueError('frozen EAT foundation source differs')

parent = residual.parent
HorizonAuditError = residual.ResidualAuditError
require, number, EPOCH = residual.require, residual.number, residual.EPOCH
NAME = 'eat_horizon_l15_f'
RESIDUAL_NAME = residual.NAME
BASE_POLICY = residual.BASE_POLICY
INPUT_KEYS = residual.INPUT_KEYS
CALL_KEYS = residual.CALL_KEYS
_ledger, _ranked, _contains_private = residual._ledger, residual._ranked, residual._contains_private
_audit_trace_fixed = residual._audit_trace_fixed
OFFSETS = (0, 15, 30, 60, 90, 120, 180)
RULE = 'explicit_public_lab_observation_end'
NEW_INSTRUMENTATION = dict(original_runner='inquiry_lab.runner._branch',
    actual_workflow='server.inquiry_workflow.run_once', record_only=False, epoch=EPOCH,
    original_policy_inputs_unchanged=True, public_time_context_added=True,
    current_patient_source='snapshot.patient_id via ContextVar around unchanged _apply',
    current_job_source='committed workflow row equal to _matches-checked applying job',
    evaluation_end_source='committed patient_created.at + frozen lab 300 seconds')
CONTEXT_KEYS = {'patient_id', 'job_version', 'event_prefix_max', 'decision_prefix_max',
                'auto_inquiry_deadline_at', 'evaluation_end_at', 'source'}
JOB_KEYS = {'patient_id', 'version', 'started_at', 'deadline_at', 'enabled', 'policy_name'}
APPLY_KEYS = {'patient_id', 'job_version', 'patient_version', 'revision', 'now'}
CORE_EVIDENCE_KEYS = {'evaluated_at', 'policy', 'horizon_seconds', 'lambda_seconds',
    'congestion_aware', 'expected_cost_without_new', 'planned_expected_cost',
    'reply_probability_global', 'accept_probability_global', 'service_median_seconds',
    'history_used_count', 'own_waiting_count', 'own_observation_timed_out_count',
    'unresolved_inquiry_count', 'candidate_plan', 'sent_hospitals', 'residual_queue'}


def _integer(value, label, minimum=0):
    require(type(value) is int and value >= minimum, 'invalid ' + label)
    return value


def context_from_prefix(events, now, patient, job_version, event_prefix, decision_prefix, job):
    """Rebuild A/H from the committed public journal, version from the job.

    Job version cannot be derived from events alone: some workflow version
    increments have no journal event. It must also agree with the actual apply
    snapshot and subsequently committed decision, checked by audit_context.
    """
    _integer(now, 'current clock'); _integer(job_version, 'job version', 1)
    _integer(event_prefix, 'event prefix'); _integer(decision_prefix, 'decision prefix')
    require(type(patient) is str and bool(patient), 'invalid current patient')
    require(type(job) is dict and set(job) == JOB_KEYS and job['patient_id'] == patient
        and type(job['enabled']) is int and job['enabled'] == 1
        and job['policy_name'] == NAME and job['version'] == job_version,
        'current enabled workflow identity/version/policy differs')
    for key in ('version', 'started_at', 'deadline_at'):
        _integer(job[key], 'workflow ' + key, 1 if key == 'version' else 0)
    visible = [e for e in events if e['id'] <= event_prefix and e.get('patient_id') == patient]
    require(all(type(e['at']) is int and 0 <= e['at'] <= now for e in visible
        if e['type'] in ('patient_created', 'inquiry_workflow_started')),
        'future patient-creation or workflow-start event inside committed prefix')
    created = [e for e in visible if e['type'] == 'patient_created']
    starts = [e for e in visible if e['type'] == 'inquiry_workflow_started']
    require(len(created) == 1 and bool(starts), 'current patient creation or workflow start missing')
    birth = created[0]; start = max(starts, key=lambda e: e['id'])
    data = parent._data(start)
    duration = _integer(data.get('deadline_seconds'), 'public automatic inquiry duration', 1)
    restarted = data.get('restarted', False)
    require(type(restarted) is bool and birth['id'] < start['id']
        and birth['at'] <= start['at'] <= now and job['started_at'] == start['at']
        and job['deadline_at'] == start['at'] + duration,
        'workflow clock differs from latest public start/restart')
    if 'policy_name' in data:
        require(data['policy_name'] == NAME, 'latest start policy differs from current workflow')
    horizon_end = birth['at'] + 300
    require(now < horizon_end, 'policy evaluated outside declared lab observation horizon')
    return dict(patient_id=patient, job_version=job_version, event_prefix_max=event_prefix,
        decision_prefix_max=decision_prefix, auto_inquiry_deadline_at=job['deadline_at'],
        evaluation_end_at=horizon_end, source=dict(
            workflow={key: job[key] for key in ('patient_id', 'version', 'started_at', 'deadline_at', 'enabled')},
            workflow_start_event=dict(id=start['id'], at=start['at'], type='inquiry_workflow_started',
                patient_id=patient, deadline_seconds=duration, restarted=restarted),
            patient_created_event=dict(id=birth['id'], at=birth['at'], type='patient_created', patient_id=patient),
            evaluation_protocol=dict(name='fixed_lab_patient_lifetime', duration_seconds=300)))


def audit_context(inputs, patient, events, event_prefix, previous_decision, decision, workflow_snapshot):
    context = inputs['time_context']
    require(type(workflow_snapshot) is dict and set(workflow_snapshot) == {'apply_snapshot', 'job'},
        'missing or unknown checked workflow snapshot fields')
    snap, job = workflow_snapshot['apply_snapshot'], workflow_snapshot['job']
    require(type(snap) is dict and set(snap) == APPLY_KEYS and snap['patient_id'] == patient
        and type(snap['now']) is int and 0 <= snap['now'] <= inputs['now'],
        'actual apply snapshot patient/clock differs')
    for key in ('job_version', 'patient_version', 'revision'):
        _integer(snap[key], 'apply ' + key, 0 if key == 'revision' else 1)
    require(type(decision['job_version']) is int and decision['job_version'] == snap['job_version']
        and type(previous_decision) is int and 0 <= previous_decision < decision['id'],
        'checked apply job version differs from subsequently committed decision')
    wanted = context_from_prefix(events, inputs['now'], patient, snap['job_version'],
        event_prefix, previous_decision, job)
    require(type(context) is dict and set(context) == CONTEXT_KEYS and context == wanted,
        'time context differs from current patient/job or public committed prefix')
    require(type(context['job_version']) is int and type(context['event_prefix_max']) is int
        and type(context['decision_prefix_max']) is int,
        'public time context identity/version types differ')
    return 1


def intervals(horizon):
    """Actual positive 5-second intervals, with the final interval clipped."""
    number(horizon, 'remaining lab horizon')
    require(horizon > 0, 'nonpositive remaining lab horizon')
    edges = [min(5 * k, horizon) for k in range(1, math.ceil(horizon / 5) + 1)]
    return [(0 if k == 0 else edges[k-1], right) for k, right in enumerate(edges)]


def masses(cdf, total, bins, offset=0):
    """CDF is evaluated at each true edge minus the inquiry-start offset."""
    return [max(0., (cdf(right-offset)-cdf(left-offset))*total) for left, right in bins]


def expected_cost(items, bins, horizon):
    """Independent disjoint first-bin/winner events, including terminal loss."""
    ordered = sorted(items, key=lambda row: row[0]); remaining = [1.] * len(ordered); cost = 0.
    for cell, (left, right) in enumerate(bins):
        if math.prod(remaining) <= 1e-12:
            return cost
        after = [max(0., remaining[i]-item[1][cell]) for i, item in enumerate(ordered)]
        for i, (travel, probability) in enumerate(ordered):
            if remaining[i] <= 1e-12:
                continue
            winner = probability[cell] * math.prod(after[:i]) * math.prod(remaining[i+1:])
            cost += winner * ((left+right)/2 + travel)
        remaining = after
    return cost + math.prod(remaining) * (horizon+1500)


def model_from_inputs(inputs):
    """Unchanged R beliefs, pending distribution and head-residual queue math."""
    now = inputs['now']; context = inputs['time_context']; horizon = context['evaluation_end_at']-now
    bins = intervals(horizon); own = inputs['patient_pending']; history = inputs['observations']
    beliefs = parent._beliefs(history, now); queue = {}; replies = {}; current = []
    for row in inputs['pending']:
        queue.setdefault(row['hospital'], []).append(row)
    for row in history:
        if row['outcome'] != 'timeout':
            replies.setdefault(row['hospital'], []).append(row)
    for row in own:
        hid, submitted = row['hospital'], row['submitted_at']; r, a, mu, sigma = beliefs(hid)
        ahead = sum(1. if other['submitted_at'] < submitted else .5
            for other in queue.get(hid, []) if other['submitted_at'] <= submitted and other['patient'] != row['patient'])
        if ahead > 0:
            wait = ahead*math.exp(mu+.5*sigma*sigma)
            cdf = lambda x, mu=mu, sigma=sigma, wait=wait: parent._cdf(x-wait, mu, sigma)
            total = r*a
        else:
            freed = [q['observed_at'] for q in replies.get(hid, []) if q['submitted_at'] <= submitted]
            spent = max(0., now-max([submitted, *freed])); f0 = parent._cdf(spent, mu, sigma)
            alive = max(1e-9, 1-f0); total = r*alive/max(1e-9, 1-r*f0)*a
            cdf = lambda x, mu=mu, sigma=sigma, spent=spent, f0=f0, alive=alive: (
                parent._cdf(spent+x, mu, sigma)-f0)/alive if x > 0 else 0.
        current.append((row['travel_seconds'], masses(cdf, total, bins)))
    core = {key: inputs[key] for key in INPUT_KEYS}
    residual_rows = residual.residual_rows(core); pool = _ranked(inputs['candidates'])[:min(6, inputs['max_new'])]
    candidates = {}
    for row, record in zip(pool, residual_rows):
        hid = row['hospital']; r, a, mu, sigma = beliefs(hid); wait = record['estimated_wait_seconds']
        candidates[hid] = dict(travel=row['travel_seconds'], total=r*a, mu=mu, sigma=sigma, wait=wait)
    fresh = sum(now-row['submitted_at'] <= 180 for row in own)
    return dict(horizon=horizon, bins=bins, pool=pool, residual_rows=residual_rows,
        current=current, candidates=candidates, fresh=fresh, room=min(inputs['max_new'], 4-fresh),
        offsets=[offset for offset in OFFSETS if now+offset < min(context['auto_inquiry_deadline_at'], context['evaluation_end_at'])])


def item_at(model, hospital, offset):
    row = model['candidates'][hospital]
    cdf = lambda x: parent._cdf(x-row['wait'], row['mu'], row['sigma'])
    return row['travel'], masses(cdf, row['total'], model['bins'], offset)


def plan_cost(model, offsets):
    chosen = [(h, d) for h, d in offsets.items() if d is not None]
    items = list(model['current']) + [item_at(model, h, d) for h, d in chosen]
    value = expected_cost(items, model['bins'], model['horizon'])
    for _, offset in chosen:
        # Legal delayed starts are whole original 15-second offsets. Therefore
        # all earlier bins are complete original 5-second cells.
        survival = math.prod(max(0., 1-sum(m[:offset//5])) for _, m in items) if offset else 1.
        value += 15*survival
    return value


def audit_horizon(inputs, output):
    """Validate the recorded plan and utility without choosing a new plan."""
    require(type(inputs) is dict and set(inputs) == INPUT_KEYS | {'time_context'}
        and not _contains_private(inputs), 'unknown or private horizon inputs')
    require(type(inputs['time_context']) is dict and set(inputs['time_context']) == CONTEXT_KEYS,
        'unknown or missing public time-context fields')
    require(type(output) is dict and set(output) == {'send', 'reason', 'evidence', 'next_review_at'},
        'unknown or missing horizon output fields')
    evidence = output['evidence']; model = model_from_inputs(inputs)
    require(type(evidence) is dict and set(evidence) == CORE_EVIDENCE_KEYS | {'time_horizon'},
        'horizon correction combined an unknown intervention')
    core = {key: inputs[key] for key in INPUT_KEYS}
    residual_view = dict(output, evidence=dict(evidence, policy=RESIDUAL_NAME))
    checked = residual.audit_residual(core, residual_view)
    context = inputs['time_context']; remaining = model['horizon']
    window = min(context['auto_inquiry_deadline_at'], context['evaluation_end_at'])-inputs['now']
    wanted_time = dict(schema_version=1, rule=RULE, source_sha256=dict(SOURCE_SHA256), context=context,
        observation_remaining_seconds=remaining, new_contact_window_seconds=window,
        cell_edges_seconds=[right for _, right in model['bins']],
        cell_representatives_seconds=[(left+right)/2 for left, right in model['bins']],
        allowed_plan_offsets_seconds=model['offsets'],
        pending_reply_scope='retain A-after replies through inclusive lab H; A stops only new inquiries',
        evaluation_scope='fixed lab measurement only; no live pending-reply expiry at A')
    require(type(evidence['time_horizon']) is dict and evidence['time_horizon'] == wanted_time
        and type(evidence['time_horizon']['schema_version']) is int,
        'time boundaries, clipped cells, original offsets or scope differ')
    plan = evidence['candidate_plan']; offsets = {}; beliefs = parent._beliefs(inputs['observations'], inputs['now'])
    require(type(plan) is list and len(plan) == len(model['pool']), 'horizon planning pool differs')
    for actual, row in zip(plan, model['pool']):
        hid = row['hospital']; r, a, _, _ = beliefs(hid)
        require(type(actual) is dict and set(actual) == {'hospital', 'planned_offset_seconds',
            'inquiry_cost', 'reply_probability', 'accept_probability', 'queue_length', 'travel_seconds'},
            'unknown planned-candidate fields')
        offset = actual['planned_offset_seconds']
        require(actual['hospital'] == hid and (offset is None or type(offset) is int and offset in model['offsets'])
            and actual['travel_seconds'] == row['travel_seconds'] and actual['inquiry_cost'] == 15
            and actual['reply_probability'] == round(r, 4) and actual['accept_probability'] == round(a, 4)
            and type(actual['queue_length']) is int
            and actual['queue_length'] == sum(p['hospital'] == hid for p in inputs['pending']),
            'candidate order, public beliefs/cost or legal start offset differs')
        offsets[hid] = offset
    send = [hid for hid, offset in offsets.items() if offset == 0]
    require(type(output['send']) is list and output['send'] == send
        and len(send) <= max(0, model['room']) and output['next_review_at'] == inputs['now']+15,
        'immediate sends, fresh concurrency cap or original review timing differ')
    if model['fresh'] == 0 and window > 0 and model['pool'] and model['room'] > 0:
        require(bool(send), 'original no-fresh-pending safety rule was omitted')
    if window <= 0:
        reason = 'automatic_inquiry_deadline'
    elif inputs['max_new'] == 0:
        reason = 'contact_budget'
    elif send:
        reason = 'eat_initial' if not inputs['patient_pending'] else 'eat_hedge'
    elif not inputs['candidates']:
        reason = 'no_candidates'
    elif model['room'] <= 0:
        reason = 'eat_concurrency_cap'
    else:
        reason = 'eat_wait'
    global_r, global_a, global_mu, _ = beliefs(object())
    require(output['reason'] == reason and evidence['evaluated_at'] == inputs['now']
        and evidence['policy'] == NAME and evidence['horizon_seconds'] == remaining
        and evidence['lambda_seconds'] == 15 and evidence['congestion_aware'] is False
        and evidence['expected_cost_without_new'] == round(expected_cost(model['current'], model['bins'], remaining), 3)
        and evidence['planned_expected_cost'] == round(plan_cost(model, offsets), 3)
        and evidence['reply_probability_global'] == round(global_r, 4)
        and evidence['accept_probability_global'] == round(global_a, 4)
        and evidence['service_median_seconds'] == round(math.exp(global_mu), 2)
        and evidence['history_used_count'] == len(inputs['observations'])
        and evidence['own_waiting_count'] == model['fresh']
        and evidence['own_observation_timed_out_count'] == len(inputs['patient_pending'])-model['fresh']
        and evidence['unresolved_inquiry_count'] == len(inputs['pending']),
        'recorded horizon, plan utility, beliefs, reason or counts differ')
    for key in ('history_used_count', 'own_waiting_count', 'own_observation_timed_out_count', 'unresolved_inquiry_count'):
        _integer(evidence[key], key)
    return dict(horizon_calls_checked=1, residual_queue_candidates_checked=checked,
        partial_final_cells_checked=int(remaining % 5 != 0),
        soft_deadline_distinct_calls_checked=int(context['auto_inquiry_deadline_at'] != context['evaluation_end_at']))


def _audit_calls_horizon(scenario, branch):
    events, calls, decisions = branch['db_events'], branch['policy_calls'], branch['db_decisions']
    require(type(events) is list and type(calls) is list and type(decisions) is list
        and len(calls) == len(decisions), 'policy-call/DB-decision coverage differs')
    require(all(type(e['id']) is int and e['id'] > 0 for e in events)
        and [e['id'] for e in events] == sorted({e['id'] for e in events}),
        'DB event identities not unique and ordered')
    event_ids = {e['id'] for e in events}; specs = {p['id']: p for p in scenario['patients']}
    sent_events = [e for e in events if e['type'] == 'inquiry_sent']
    actual, declared, checks = Counter(), Counter(), Counter(); previous_decision = 0; last_prefix = 0
    for call, decision in zip(calls, decisions):
        require(type(call) is dict and set(call) == CALL_KEYS | {'workflow_snapshot'},
            'unknown or missing candidate call/provenance fields')
        pid, at = call['patient_id'], call['at']; inputs, output = call['inputs'], call['output']
        prefix = call['db_event_prefix_max']
        require(pid in specs and type(at) is int and specs[pid]['at'] <= at < specs[pid]['at']+300,
            'policy ran outside current patient lab search window')
        require(type(prefix) is int and prefix >= last_prefix and (prefix == 0 or prefix in event_ids),
            'invalid or reordered committed event prefix')
        last_prefix = prefix; now = EPOCH+at
        require(type(inputs) is dict and set(inputs) == INPUT_KEYS | {'time_context'}
            and not _contains_private(inputs), 'unknown, private or future policy inputs')
        require(inputs['now'] == now and call['name'] == branch['policy'] == decision['policy_name'] == NAME
            and decision['at'] == now and decision['patient_id'] == pid
            and type(decision['id']) is int and decision['id'] == call['db_decision_id'] > previous_decision,
            'actual committed decision identity/clock differs from policy call')
        require(output['send'] == decision['hospital_ids'] and output['reason'] == decision['reason']
            and output['evidence'] == decision['evidence'], 'recorded output differs from committed DB decision')
        checks['public_time_contexts_checked'] += audit_context(inputs, pid, events, prefix,
            previous_decision, decision, call['workflow_snapshot'])
        previous_decision = decision['id']
        context = inputs['time_context']
        require(context['evaluation_end_at'] == EPOCH+specs[pid]['at']+300
            and now < context['auto_inquiry_deadline_at'],
            'public registration/hard lab H or actual soft-stop workflow call differs')
        history, pending, own, _ = _ledger(events, now, pid, prefix)
        travel = specs[pid]['travel_seconds']
        expected_own = [dict(row, travel_seconds=travel[row['hospital']]) for row in own if row['hospital'] in travel]
        require(inputs['observations'] == history and inputs['pending'] == pending
            and inputs['patient_pending'] == expected_own, 'policy ledger differs from received committed prefix')
        earlier = [e for e in sent_events if e['id'] <= prefix and e['patient_id'] == pid]
        contacted = {e['hospital_id'] for e in earlier}
        require(type(inputs['max_new']) is int and inputs['max_new'] == 10-len(contacted)
            and inputs['last_contact_at'] == max((e['at'] for e in earlier if e['at'] <= now), default=None),
            'contact budget or last-contact clock differs from public sends')
        ranked = _ranked(inputs['candidates'])
        require({row['hospital'] for row in ranked} == set(travel)-contacted
            and all(row['travel_seconds'] == travel[row['hospital']] for row in ranked),
            'uncontacted candidate universe/travel differs from original scenario offers')
        require(type(output['send']) is list and len(output['send']) == len(set(output['send']))
            and len(output['send']) <= inputs['max_new']
            and all(hid in set(travel)-contacted for hid in output['send']),
            'duplicate, previously contacted or unavailable immediate inquiry')
        require(sum(now-row['submitted_at'] <= 180 for row in expected_own)+len(output['send']) <= 4,
            'original EAT fresh concurrency cap exceeded')
        for hid in output['send']:
            matching = [e for e in sent_events if e['patient_id'] == pid and e['hospital_id'] == hid
                and e['at'] == now and e['id'] > prefix]
            require(len(matching) == 1, 'policy send lacks unique subsequent committed inquiry')
            declared[matching[0]['id']] += 1
        checks.update(audit_horizon(inputs, output))
    for event in sent_events:
        if event['patient_id'] in specs:
            actual[event['id']] += 1
    require(declared == actual, 'actual patient inquiries not covered once by recorded decisions')
    return dict(policy_calls=len(calls), **dict(checks))


def audit_branch(scenario, branch):
    """Audit full recorded workflow; frozen comparison branches use their auditor."""
    if branch.get('policy') != NAME:
        return residual.audit_branch(scenario, branch)
    try:
        environment = hashlib.sha256(json.dumps(scenario, ensure_ascii=False, sort_keys=True,
            separators=(',', ':')).encode('utf-8')).hexdigest()
        require(branch['environment_sha256'] == environment, 'scenario/environment binding differs')
        require(branch['rule'] == 'immediate', 'horizon candidate combined a confirmation intervention')
        require(branch['instrumentation'] == NEW_INSTRUMENTATION,
            'explicit time-context adapter/checking provenance differs')
        calls = _audit_calls_horizon(scenario, branch)
        accounting = _audit_trace_fixed(scenario, branch)
        # Parent trace audit independently checks every patient and actual
        # queue/reply/confirmation/cancellation, including work after cancellation.
        total_j = sum(1800 if p['failed'] else p['estimated_arrival_seconds'] for p in branch['patients'])
        return dict(passed=True, **calls, **accounting,
            total_arrival_failure_cost_seconds=total_j,
            mean_arrival_failure_cost_seconds=total_j/len(branch['patients']) if branch['patients'] else None,
            policy_kernel_independently_reexecuted=False,
            public_time_context_independently_reconstructed=True,
            residual_queue_math_independently_reconstructed=True,
            recorded_plan_cost_independently_reconstructed=True,
            job_version_scope='actual checked apply snapshot equals captured enabled job and committed decision; silent version increments are not reconstructed from events',
            optimality_claimed=False,
            scope='committed current-patient creation/start prefixes establish lab hard H and soft automatic A; actual apply identity/version linkage; clipped/shifted CDFs, partial-cell costs, residual queue and recorded plan; actual FIFO/replies/all-patient W/J/contact/work accounting; coordinate descent source-bound, not rerun')
    except (KeyError, TypeError, IndexError, AttributeError, OverflowError, StopIteration) as error:
        raise HorizonAuditError('malformed saved explicit-horizon artifact') from error


def audit_replay(replay):
    require(type(replay) is dict and replay['schema_version'] == 1 and type(replay['branches']) is list,
        'invalid complete raw replay')
    require(replay['arms'] == [[b['policy'], b['rule']] for b in replay['branches']]
        and len({tuple(a) for a in replay['arms']}) == len(replay['arms']) > 0,
        'replay arm inventory or order differs')
    require(all(b['environment_sha256'] == replay['environment_sha256']
        and b['snapshot_sha256'] == replay['initial_snapshot_sha256'] for b in replay['branches']),
        'paired branches did not share initial snapshot/environment')
    checked = [audit_branch(replay['scenario'], b) for b in replay['branches']]
    return dict(passed=True, branches=len(checked), branch_audits=checked, new_executions=0)

