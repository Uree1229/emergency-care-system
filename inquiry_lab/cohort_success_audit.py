"""Independent finite fixed-continuation cohort success score audit.

Frozen B is checked intact. Scores are reconstructed from public distributions,
not trusted recorded floating-point values. Priority snapshots are row/version
provenance: old public events do not contain the priority values themselves.
"""
from collections import Counter
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path

from . import joint_budget_audit as base

ROOT = Path(__file__).resolve().parents[1]
BASE_AUDITOR_SHA256 = '40f5a87e0a80ae15fb33330815440ae29f41c9bee5eba0c7314111233d4ada63'
BASE_SOURCE_SHA256 = '5707df3632d99ae453fe1e1ecb3dcc6fc3882c729e9a9119f4e791d107e2808a'
if hashlib.sha256(Path(base.__file__).read_bytes()).hexdigest() != BASE_AUDITOR_SHA256:
    raise ValueError('frozen B independent auditor differs')
if hashlib.sha256((ROOT/'inquiry_lab/joint_budget_policy.py').read_bytes()).hexdigest() != BASE_SOURCE_SHA256:
    raise ValueError('frozen B producer source differs')
NAME = 'eat_cohort_success_l15_f'
BASE_POLICY = base.NAME
RULE = 'priority_tier_success_then_wait_with_fixed_budget_winner_continuation'
OBJECTIVE = 'global tier 4,2,1 success-probability sums within 1e-12, then global capped wait within 1e-9; B row then original table order'
SCOPE = 'current eligible first-contact cohort only; fixed continuation approximation; existing pair P/cost feasibility retained; no outcome or future action guarantee'
TEMPLATE_SCOPE = 'copied B winner immediate sends; singleton H optimized once at reference final pending; candidate ledgers change models only'
parent, horizon, joint = base.parent, base.horizon, base.joint
CohortSuccessAuditError = base.JointBudgetAuditError
require, number, EPOCH = base.require, base.number, base.EPOCH
_same, _pid, _integer = base._same, base._pid, base._integer
INPUT_KEYS, CALL_KEYS = base.INPUT_KEYS, base.CALL_KEYS
GROUP_KEYS = base.GROUP_KEYS | {'priority_context'}
PRIORITY_SOURCE = 'captured current patient priority fields with common snapshot provenance'
P_EPSILON = 1e-12
W_EPSILON = 1e-9
NEW_INSTRUMENTATION = dict(base.NEW_INSTRUMENTATION,
    actual_workflow='inquiry_lab.cohort_success_runner.run_once',
    public_patient_priority_context_added=True, finite_cohort_success_first_menu=True)
NEW_INSTRUMENTATION.pop('projected_paired_contact_budget_veto_review')


def _project_name(name):
    return BASE_POLICY if name == NAME else '__other_policy__:'+str(name)


def alias_context(context):
    value = deepcopy(context)
    for row in value['eligible']:
        require(row['job']['policy_name'] == NAME, 'eligible public job has wrong native policy')
        row['job']['policy_name'] = BASE_POLICY
    return value


def alias_member(member):
    value = deepcopy(member)
    require(value['workflow_snapshot']['job']['policy_name'] == NAME, 'member job has wrong native policy')
    value['workflow_snapshot']['job']['policy_name'] = BASE_POLICY
    return value


def _alias_events(events):
    value = deepcopy(events)
    for event in value:
        if event['type'] in ('inquiry_workflow_started', 'inquiry_workflow_paused', 'inquiry_policy_changed'):
            data = parent._data(event)
            if 'policy_name' in data:
                event['data'] = dict(data, policy_name=_project_name(data['policy_name']))
    return value


def audit_registry(events, decisions, registry, now, event_prefix, decision_prefix):
    projected = deepcopy(registry)
    for job in projected['jobs']:
        job['policy_name'] = _project_name(job['policy_name'])
    context = base.audit_registry(_alias_events(events), decisions, projected, now, event_prefix, decision_prefix)
    for row in context['eligible']:
        row['job']['policy_name'] = NAME
    return context


def audit_time_context(events, inputs, member, event_prefix, decision_prefix, decision=None):
    return base.audit_time_context(_alias_events(events), inputs, alias_member(member),
        event_prefix, decision_prefix, decision)


def validate_tail_members(members, context, tails):
    return base.validate_tail_members([alias_member(m) for m in members], alias_context(context),
        [alias_member(m) for m in tails])


def audit_member_provenance(scenario, events, member, event_prefix, decision_prefix):
    return base.audit_member_provenance(scenario, _alias_events(events), alias_member(member),
        event_prefix, decision_prefix)


def audit_priority_context(priority, context, members, tails, *, lab_defaults=False):
    require(type(priority) is dict and set(priority) == {'schema_version', 'now', 'event_prefix_max',
        'decision_prefix_max', 'revision', 'source', 'patients'}, 'unknown or incomplete priority context')
    require(type(priority['schema_version']) is int and priority['schema_version'] == 1
        and priority['source'] == PRIORITY_SOURCE, 'priority context version/source differs')
    for key in ('now', 'event_prefix_max', 'decision_prefix_max'):
        require(type(priority[key]) is int and priority[key] == context[key], 'priority common clock/prefix differs')
    require(type(priority['revision']) is int and priority['revision']
        == members[0]['workflow_snapshot']['apply_snapshot']['revision'], 'priority planning revision differs')
    all_members = members+tails
    require(type(priority['patients']) is list and len(priority['patients']) == len(all_members),
        'priority snapshot omitted or invented current cohort owners')
    result = {}
    for row, member in zip(priority['patients'], all_members):
        require(type(row) is dict and set(row) == {'patient_id', 'patient_version', 'reported_priority',
            'priority_source', 'effective_priority'}, 'priority patient fields differ')
        require(_pid(row['patient_id']) == _pid(member['patient_id'])
            and type(row['patient_version']) is int and row['patient_version']
            == member['workflow_snapshot']['apply_snapshot']['patient_version'], 'priority identity/version differs')
        value, source = row['reported_priority'], row['priority_source']
        require((value is None or type(value) is int and value in (4, 2, 1))
            and type(source) is str and source in ('expert', 'self', 'unknown'),
            'invalid captured public priority values')
        effective = value if source == 'expert' and value in (4, 2, 1) else 4
        require(type(row['effective_priority']) is int and row['effective_priority'] == effective,
            'priority rule differs from public service contract')
        if lab_defaults:
            require(value is None and source == 'unknown' and effective == 4,
                'frozen lab payload has no reported priority and normalizes every owner to4')
        result[_pid(member['patient_id'])] = effective
    return result


def public_model(inputs):
    """Build no-own first-contact distributions without rerunning H search."""
    require(inputs['patient_pending'] == [] and inputs['last_contact_at'] is None,
        'fixed continuation owner is outside first-contact cohort')
    model = horizon.model_from_inputs(inputs)
    beliefs = parent._beliefs(inputs['observations'], inputs['now'])
    hospitals = []
    for candidate, queue in zip(model['pool'], model['residual_rows']):
        hid = candidate['hospital']; reply, accept, mu, sigma = beliefs(hid)
        hospitals.append(dict(hospital=hid, travel_seconds=candidate['travel_seconds'], service_mu=mu,
            service_sigma=sigma, reply_probability=reply, accept_probability=accept,
            mean_service_seconds=math.exp(mu+.5*sigma*sigma), assumed_workers=1,
            residual_wait_seconds=queue['estimated_wait_seconds'], inquiry_cost_seconds=15))
    return dict(horizon_seconds=model['horizon'], cell_edges_seconds=[right for _, right in model['bins']],
        hospitals=hospitals, failure_penalty_seconds=1500)


def global_staged_choice(rows, b_index):
    """Globally anchored tie bands; never an order-dependent pairwise tie."""
    require(type(rows) is list and bool(rows), 'missing finite score menu')
    remaining = [r['row_index'] for r in rows]
    by_index = {r['row_index']: r for r in rows}
    require(len(by_index) == len(rows) and b_index in remaining, 'B row absent from feasible score menu')
    filters = []
    for tier in (4, 2, 1):
        maximum = max(by_index[i]['tier_probabilities'][str(tier)] for i in remaining)
        require(math.isfinite(maximum), 'invalid priority success sum')
        remaining = [i for i in remaining if by_index[i]['tier_probabilities'][str(tier)] >= maximum-P_EPSILON]
        filters.append(dict(tier=tier, maximum_probability=maximum, surviving_row_indices=list(remaining)))
    minimum = min(by_index[i]['sum_capped_wait_seconds'] for i in remaining)
    tied = [i for i in remaining if by_index[i]['sum_capped_wait_seconds'] <= minimum+W_EPSILON]
    selected = b_index if b_index in tied else tied[0]
    return dict(priority_filters=filters, minimum_wait_seconds=minimum,
        wait_surviving_row_indices=tied, base_budget_tied=b_index in tied, selected_index=selected)


def precise_same(actual, wanted, label='cohort evidence'):
    """Score probabilities are checked more tightly than the decision band.

    Final selection always uses freshly reconstructed values, even where
    recorded legacy floats passed the parent's looser display tolerance.
    """
    if type(wanted) is dict:
        require(type(actual) is dict and set(actual) == set(wanted), 'fields differ: '+label)
        for key in wanted:
            precise_same(actual[key], wanted[key], label+'.'+str(key))
    elif type(wanted) is list:
        require(type(actual) is list and len(actual) == len(wanted), 'list length differs: '+label)
        for index, (left, right) in enumerate(zip(actual, wanted)):
            precise_same(left, right, label+'['+str(index)+']')
    elif type(wanted) is float:
        probability = ('probabilit' in label)
        tolerance = 1e-13 if probability else 1e-10
        require(type(actual) in (int, float) and math.isfinite(actual)
            and math.isclose(actual, wanted, rel_tol=0, abs_tol=tolerance), 'number differs: '+label)
    else:
        require(type(actual) is type(wanted) and actual == wanted, 'value differs: '+label)


def exact_copy(actual, wanted, label):
    require(json.dumps(actual, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
        == json.dumps(wanted, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':')),
        'source copy differs: '+label)


def first_member(member):
    return base.base.alias_member(base.alias_member(alias_member(member)))


def audit_template(recorded, budget, tails):
    require(type(recorded) is dict and set(recorded) == {'source_path', 'paired_steps', 'singleton',
        'reference_final_pending', 'scope'}, 'unknown or missing fixed continuation template')
    projection = budget['evidence']['projections']['winner']
    steps = []
    for step in projection['steps']:
        steps.append(dict(step_index=step['step_index'], source_budget_projection_step_index=step['step_index'],
            members=deepcopy(step['members']), horizon_proposals=deepcopy(step['proposal']['evidence']['baseline_proposals']),
            sends=[list(d['send']) for d in step['proposal']['decisions']]))
    singleton = None
    if len(tails)%2:
        require(type(recorded['singleton']) is dict and set(recorded['singleton'])
            == {'member', 'horizon_proposal', 'send'}, 'missing fixed singleton template')
        member = first_member(tails[-1]); member['inputs']['pending'] = deepcopy(projection['final_pending'])
        exact_copy(recorded['singleton']['member'], member, 'singleton fixed reference inputs')
        proposal = recorded['singleton']['horizon_proposal']
        base.audit_first_contact_search(member['inputs'], proposal)
        singleton = dict(member=member, horizon_proposal=proposal, send=list(proposal['send']))
    wanted = dict(source_path='original_budget_proposal.evidence.projections.winner', paired_steps=steps,
        singleton=singleton, reference_final_pending=deepcopy(projection['final_pending']), scope=TEMPLATE_SCOPE)
    exact_copy(recorded, wanted, 'complete B continuation template')
    return wanted


def append_immediate(ledger, members, sends, now):
    for member, send in zip(members, sends):
        for hid in send:
            ledger.append(dict(hospital=hid, patient=member['patient_id'], submitted_at=now))


def score_row(index, hospitals, members, current_models, template, priorities, now):
    """Conditional fixed sends; all changed-ledger CDFs are rebuilt per row."""
    values = []
    def collect(people, metrics):
        for member, metric in zip(people, metrics):
            require(0 <= metric['selection_probability'] <= 1 and metric['capped_wait_seconds'] >= 0,
                'invalid conditional selection distribution')
            values.append(dict(patient_id=member['patient_id'], effective_priority=priorities[_pid(member['patient_id'])],
                metrics=metric))
    current = joint.combination(current_models, hospitals)
    collect(members, current['member_metrics'])
    ledger = deepcopy(members[0]['inputs']['pending']); append_immediate(ledger, members, hospitals, now)
    for step in template['paired_steps']:
        people = deepcopy(step['members'])
        for member in people:
            member['inputs']['pending'] = deepcopy(ledger)
        models = [public_model(member['inputs']) for member in people]
        combo = joint.combination(models, step['sends'])
        collect(people, combo['member_metrics']); append_immediate(ledger, people, step['sends'], now)
    if template['singleton'] is not None:
        singleton = template['singleton']; member = deepcopy(singleton['member'])
        member['inputs']['pending'] = deepcopy(ledger)
        collect([member], [joint.member_metrics(public_model(member['inputs']), singleton['send'], {})])
    return dict(row_index=index, member_metrics=values,
        tier_probabilities={str(tier): sum((r['metrics']['selection_probability'] for r in values
            if r['effective_priority'] == tier), 0.) for tier in (4, 2, 1)},
        sum_capped_wait_seconds=sum((r['metrics']['capped_wait_seconds'] for r in values), 0.))


def audit_pair(members, context, tails, outputs, evidence, priority_context):
    require(type(evidence) is dict and type(evidence['original_budget_proposal']) is dict
        and set(evidence['original_budget_proposal']) == {'decisions', 'evidence'}, 'missing intact frozen B proposal')
    budget = evidence['original_budget_proposal']
    checked = base.audit_pair([alias_member(m) for m in members], alias_context(context),
        [alias_member(m) for m in tails], budget['decisions'], budget['evidence'])
    validate_tail_members(members, context, tails)
    priorities = audit_priority_context(priority_context, context, members, tails)
    evaluated = budget['evidence']['cancelled_tail_veto']; b_index = budget['evidence']['actual_selected_index']
    selected, template, scores, selection = b_index, None, [], None
    original_joint = budget['evidence']['original_tail_proposal']['evidence']['original_joint_proposal']
    baseline_proposals = original_joint['evidence']['baseline_proposals']
    # This independent enumeration is also the score's current-row source.
    # Never use original_joint's recorded metric floats, even after old _same.
    current_models = [public_model(m['inputs']) for m in members]
    recomputed = joint.enumerate_pairs(current_models, [p['send'] for p in baseline_proposals])
    if evaluated:
        template = audit_template(evidence['continuation_template'], budget, tails)
        for index, row in enumerate(recomputed['combinations']):
            if row['feasible']:
                scores.append(score_row(index, row['hospitals'], members, current_models, template, priorities, context['now']))
        selection = global_staged_choice(scores, b_index); selected = selection['selected_index']
    intervened = selected != b_index
    wanted = dict(schema_version=1, rule=RULE, base_policy=BASE_POLICY, base_source_sha256=BASE_SOURCE_SHA256,
        identity_projection=dict(source_policy=NAME, calculation_policy=BASE_POLICY,
            fields=['joint_context.eligible[].job.policy_name', 'members[].workflow_snapshot.job.policy_name',
                'tail_members[].workflow_snapshot.job.policy_name'], copied_only=True, public_identity_unchanged=True),
        joint_context=context, priority_context=priority_context, original_budget_proposal=budget,
        evaluated=evaluated, continuation_template=template, row_scores=scores, selection=selection,
        base_budget_selected_index=b_index, selected_index=selected, intervened=intervened, tiers=[4,2,1],
        tolerances=dict(probability=P_EPSILON, wait_seconds=W_EPSILON), objective=OBJECTIVE, scope=SCOPE)
    precise_same(evidence, wanted, 'independent cohort score selection')
    require(type(outputs) is list and len(outputs) == 2, 'actual cohort output coverage differs')
    for index in range(2):
        actual = deepcopy(budget['decisions'][index])
        if intervened:
            chosen = recomputed['combinations'][selected]
            actual = deepcopy(baseline_proposals[index]); send = chosen['hospitals'][index]
            actual['send'] = list(send); actual['reason'] = 'cohort_success_current_only'
            ev = actual['evidence']; ev['sent_hospitals'] = list(send)
            ev['planned_expected_cost'] = round(chosen['member_metrics'][index]['total_cost_seconds'], 3)
            for row in ev['candidate_plan']:
                row['planned_offset_seconds'] = 0 if row['hospital'] in send else None
        actual['evidence']['policy'] = NAME
        actual['evidence']['cohort_success'] = dict(schema_version=1, mode='pair_cohort_success', evaluated=evaluated,
            intervened=intervened, member_index=index, base_budget_selected_index=b_index, selected_index=selected,
            effective_priority=priorities[_pid(members[index]['patient_id'])])
        precise_same(outputs[index], actual, 'actual separately rebuilt cohort decision')
    checked = dict(checked, original_budget_groups_applied=checked['joint_groups_applied'],
        joint_groups_applied=int(selected != recomputed['baseline_index']))
    return dict(checked, cohort_priority_contexts_checked=1, cohort_success_menus_evaluated=int(evaluated),
        cohort_success_rows_checked=len(scores), cohort_success_interventions=int(intervened),
        cohort_singleton_H_searches_checked=int(evaluated and template['singleton'] is not None),
        cohort_score_members_checked=sum(len(r['member_metrics']) for r in scores))


_audit_phase_order = base._audit_phase_order
time_context_from_prefix = audit_time_context


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
        checks.update(audit_pair(group['members'], wanted, group['tail_members'], group['outputs'], group['evidence'], group['priority_context']))
        audit_priority_context(group['priority_context'], wanted, group['members'], group['tail_members'], lab_defaults=True)
        for tail in group['tail_members']:
            checks['tail_public_provenance_checked'] += audit_member_provenance(scenario, events, tail,
                group['event_prefix_max'], group['decision_prefix_max'])
        for member in group['members']+group['tail_members']:
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
            require(output['evidence'].get('joint_tail') == dict(schema_version=1, mode='horizon_fallback', guard_evaluated=False)
                and output['evidence'].get('joint_first') == dict(schema_version=1, mode='horizon_fallback', current_only=False)
                and output['evidence'].get('joint_budget') == dict(schema_version=1, mode='horizon_fallback', projection_evaluated=False)
                and output['evidence'].get('cohort_success') == dict(schema_version=1, mode='horizon_fallback', evaluated=False, intervened=False),
                'ungrouped decision changed the original H fallback')
            normalized = deepcopy(output); normalized['evidence'].pop('joint_first')
            normalized['evidence'].pop('joint_tail')
            normalized['evidence'].pop('joint_budget')
            normalized['evidence'].pop('cohort_success')
            normalized['evidence']['policy'] = horizon.NAME
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
        require([r['hospital'] for r in ranked] == sorted(set(travel)-contacted, key=lambda h: (travel[h], h))
            and all(c.get('resources_enough') is True for c in inputs['candidates']),
            'actual candidate order/resource eligibility differs from frozen public lab construction')
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
        return base.audit_branch(scenario, branch)
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
            intact_original_joint_proposal_independently_audited=True,
            intact_original_tail_proposal_independently_audited=True,
            intact_original_budget_proposal_independently_audited=True,
            all_current_and_continuation_score_models_independently_rebuilt=True,
            tier_success_global_staged_selection_independently_recomputed=True,
            priority_values_reconstructed_from_event_payloads=False,
            priority_scope='captured priority rows bind complete current cohort identities, versions, clock, prefix and revision; frozen lab payload independently implies unknown/None/effective4; general live values require frozen capture/prewrite checks and rollback tests',
            projected_first_contact_H_coordinate_search_independently_reexecuted=True,
            paired_count_comparisons_independently_reconstructed=True,
            actual_cohort_outputs_and_sends_independently_audited=True,
            tail_opportunity_scope='zero unresolved public inquiry count after proposed immediate pair; not free workers, beds or a success guarantee',
            H_coordinate_descent_independently_reexecuted=False,
            H_coordinate_descent_scope='actual H baseline proposals and fallback remain source-bound; B projection and singleton first-contact searches independently reexecuted; fixed continuation rows rebuild distributions without per-row H reoptimization',
            physical_transaction_boundary_from_raw_proved=False,
            atomicity_evidence_scope='all-member checked common snapshots, linked two next decisions and ordered subsequent sends are audited; physical atomicity requires frozen adapter source and second-member rollback tests',
            silent_workflow_field_scope='next-review/version remain captured public job provenance; zero-journal active review equals latest start; member versions link actual snapshots to committed decisions',
            scope='complete current first-contact cohort with captured priorities and intact frozen B proposal; independently rebuilt conditional current and fixed-continuation CDF metrics, tier-success global staged filters and separate actual H-baseline outputs; future action divergence and already-queried patients outside model scope; actual all-patient FIFO/W/J/failure/contact/work audited')
    except (KeyError, TypeError, IndexError, AttributeError, OverflowError, StopIteration) as error:
        raise CohortSuccessAuditError('malformed saved cohort-success artifact') from error


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
