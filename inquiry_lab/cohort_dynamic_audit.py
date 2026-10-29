"""Independent bounded row-specific reference-continuation audit.

Frozen CS is checked intact. Scores are reconstructed from public distributions,
not trusted recorded floating-point values. Priority snapshots are row/version
provenance: old public events do not contain the priority values themselves.
"""
from collections import Counter
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path

from . import cohort_success_audit as base

ROOT = Path(__file__).resolve().parents[1]
BASE_AUDITOR_SHA256 = 'aa48a5497a515957fee6a2e9f83938cf833b9a109b4b694589355c64ecc46ace'
BASE_SOURCE_SHA256 = '5c9e41de5fb5f83451639dd41505998d31c2551868d6ad9cc231ac20205b304b'
if hashlib.sha256(Path(base.__file__).read_bytes()).hexdigest() != BASE_AUDITOR_SHA256:
    raise ValueError('frozen CS independent auditor differs')
if hashlib.sha256((ROOT/'inquiry_lab/cohort_success_policy.py').read_bytes()).hexdigest() != BASE_SOURCE_SHA256:
    raise ValueError('frozen CS producer source differs')
NAME = 'eat_cohort_dynamic_l15_f'
BASE_POLICY = base.NAME
RULE = 'three_reference_rows_with_row_specific_same_clock_CS_continuation'
OBJECTIVE = 'global tier 4,2,1 success sums within 1e-12, then capped wait within 1e-9; prefer CS index then menu order'
SCOPE = 'current eligible first-contact cohort only; frozen CS reference continuation; no clock advance or already-queried-owner score; actual new-policy later actions may differ'
parent, horizon, joint = base.parent, base.horizon, base.joint
CohortDynamicAuditError = base.CohortSuccessAuditError
require, number, EPOCH = base.require, base.number, base.EPOCH
_same, _pid, _integer = base._same, base._pid, base._integer
INPUT_KEYS, CALL_KEYS = base.INPUT_KEYS, base.CALL_KEYS
GROUP_KEYS = base.GROUP_KEYS
PRIORITY_SOURCE = 'captured current patient priority fields with common snapshot provenance'
P_EPSILON = 1e-12
W_EPSILON = 1e-9
NEW_INSTRUMENTATION = dict(base.NEW_INSTRUMENTATION,
    actual_workflow='inquiry_lab.cohort_dynamic_runner.run_once',
    row_specific_reference_continuation_menu=True)
NEW_INSTRUMENTATION.pop('finite_cohort_success_first_menu')


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
    return base.audit_priority_context(priority, alias_context(context), [alias_member(m) for m in members],
        [alias_member(m) for m in tails], lab_defaults=lab_defaults)


public_model = base.public_model
precise_same = base.precise_same
exact_copy = base.exact_copy


def menu_indices(original):
    if not original['evidence']['evaluated']:
        return []
    budget = original['evidence']['original_budget_proposal']['evidence']
    indices = [original['evidence']['selected_index'], budget['actual_selected_index'],
        budget['original_tail_proposal']['evidence']['actual_selected_index']]
    return list(dict.fromkeys(indices))


def _nested_joint(proposal):
    return proposal['evidence']['original_budget_proposal']['evidence']['original_tail_proposal']['evidence']['original_joint_proposal']


def _collect(values, people, metrics, priorities):
    for member, metric in zip(people, metrics):
        require(0 <= metric['selection_probability'] <= 1 and metric['capped_wait_seconds'] >= 0,
            'invalid conditional current-only distribution')
        values.append(dict(patient_id=member['patient_id'], effective_priority=priorities[_pid(member['patient_id'])],
            metrics=metric))


def _appends(ledger, members, sends, now, kind, step, path):
    result = []
    for index, (member, send) in enumerate(zip(members, sends)):
        for send_index, hid in enumerate(send):
            inquiry = dict(hospital=hid, patient=member['patient_id'], submitted_at=now)
            require(not any(row['hospital'] == hid and _pid(row['patient']) == _pid(member['patient_id'])
                for row in ledger), 'virtual inquiry duplicated an original or preceding attempt')
            ledger.append(deepcopy(inquiry))
            result.append(dict(inquiry=inquiry, source=dict(kind=kind, step_index=step, member_index=index,
                send_index=send_index, proposal_path=path.format(member=index, send=send_index))))
    return result


def audit_row(recorded, row_index, menu_position, hospitals, models, members, context, tails, priority):
    require(type(recorded) is dict and set(recorded) == {'row_index', 'member_metrics', 'tier_probabilities',
        'sum_capped_wait_seconds', 'projection'}, 'missing or unknown dynamic score row')
    projection = recorded['projection']
    require(type(projection) is dict and set(projection) == {'current_pair_send', 'initial_pending',
        'initial_appended_inquiries', 'steps', 'singleton', 'final_pending'}, 'missing conditional ledger projection')
    priorities = audit_priority_context(priority, context, members, tails)
    now = context['now']; values = []; counters = Counter()
    current = joint.combination(models, hospitals)
    _collect(values, members, current['member_metrics'], priorities)
    ledger = deepcopy(members[0]['inputs']['pending']); initial_pending = deepcopy(ledger)
    initial = _appends(ledger, members, hospitals, now, 'current_pair_row', None,
        'original_cohort_success_proposal.evidence.original_budget_proposal.evidence.original_tail_proposal.evidence.original_joint_proposal.evidence.combinations['+str(row_index)+'].hospitals[{member}][{send}]')
    steps = []
    require(type(projection['steps']) is list and len(projection['steps']) == len(tails)//2,
        'conditional reference calls omitted or added a registered pair')
    for step_index, recorded_step in enumerate(projection['steps']):
        require(type(recorded_step) is dict and set(recorded_step) == {'step_index', 'source_tail_member_indices',
            'joint_context', 'priority_context', 'members', 'tail_members', 'proposal', 'appended_inquiries'},
            'missing or unknown conditional CS step')
        start = 2*step_index
        projected_members = [alias_member(m) for m in tails[start:start+2]]
        projected_tails = [alias_member(m) for m in tails[start+2:]]
        for member in projected_members+projected_tails:
            member['inputs']['pending'] = deepcopy(ledger)
        projected_context = alias_context(context)
        projected_context['eligible'] = projected_context['eligible'][start+2:]
        projected_context['pair_patient_ids'] = [m['patient_id'] for m in projected_members]
        projected_priority = deepcopy(priority); projected_priority['patients'] = projected_priority['patients'][start+2:]
        for key, wanted in [('members', projected_members), ('tail_members', projected_tails),
                ('joint_context', projected_context), ('priority_context', projected_priority)]:
            exact_copy(recorded_step[key], wanted, 'conditional CS '+key)
        proposal = recorded_step['proposal']
        require(type(proposal) is dict and set(proposal) == {'decisions', 'evidence'}, 'missing intact conditional CS proposal')
        checked = base.audit_pair(projected_members, projected_context, projected_tails,
            proposal['decisions'], proposal['evidence'], projected_priority)
        for member, plan in zip(projected_members, _nested_joint(proposal)['evidence']['baseline_proposals']):
            base.base.audit_first_contact_search(member['inputs'], plan)
            counters['dynamic_reference_H_searches_checked'] += 1
        counters['dynamic_reference_CS_calls_checked'] += 1
        counters['dynamic_reference_CS_menus_evaluated'] += checked['cohort_success_menus_evaluated']
        sends = [list(d['send']) for d in proposal['decisions']]
        metrics = joint.combination([public_model(m['inputs']) for m in projected_members], sends)['member_metrics']
        _collect(values, projected_members, metrics, priorities)
        appended = _appends(ledger, projected_members, sends, now, 'reference_CS_selected', step_index,
            'row_scores['+str(menu_position)+'].projection.steps['+str(step_index)+'].proposal.decisions[{member}].send[{send}]')
        steps.append(dict(step_index=step_index, source_tail_member_indices=[start, start+1],
            joint_context=projected_context, priority_context=projected_priority, members=projected_members,
            tail_members=projected_tails, proposal=proposal, appended_inquiries=appended))
    singleton = None
    if len(tails)%2:
        require(type(projection['singleton']) is dict and set(projection['singleton'])
            == {'member', 'proposal', 'appended_inquiries'}, 'missing conditional singleton')
        member = alias_member(tails[-1]); member['inputs']['pending'] = deepcopy(ledger)
        exact_copy(projection['singleton']['member'], member, 'conditional singleton source')
        proposal = projection['singleton']['proposal']
        base.base.audit_first_contact_search(member['inputs'], proposal)
        counters['dynamic_singleton_H_searches_checked'] += 1
        _collect(values, [member], [joint.member_metrics(public_model(member['inputs']), proposal['send'], {})], priorities)
        appended = _appends(ledger, [member], [proposal['send']], now, 'reference_H_singleton', len(steps),
            'row_scores['+str(menu_position)+'].projection.singleton.proposal.send[{send}]')
        singleton = dict(member=member, proposal=proposal, appended_inquiries=appended)
    wanted = dict(row_index=row_index, member_metrics=values,
        tier_probabilities={str(tier): sum((r['metrics']['selection_probability'] for r in values
            if r['effective_priority'] == tier), 0.) for tier in (4, 2, 1)},
        sum_capped_wait_seconds=sum((r['metrics']['capped_wait_seconds'] for r in values), 0.),
        projection=dict(current_pair_send=[list(s) for s in hospitals], initial_pending=initial_pending,
            initial_appended_inquiries=initial, steps=steps, singleton=singleton, final_pending=ledger))
    precise_same(recorded, wanted, 'independent row-specific reference continuation')
    counters['dynamic_score_members_checked'] += len(values)
    return wanted, counters


def audit_pair(members, context, tails, outputs, evidence, priority_context):
    require(type(evidence) is dict and type(evidence['original_cohort_success_proposal']) is dict
        and set(evidence['original_cohort_success_proposal']) == {'decisions', 'evidence'}, 'missing intact frozen CS proposal')
    original = evidence['original_cohort_success_proposal']
    checked = base.audit_pair([alias_member(m) for m in members], alias_context(context), [alias_member(m) for m in tails],
        original['decisions'], original['evidence'], priority_context)
    validate_tail_members(members, context, tails)
    priorities = audit_priority_context(priority_context, context, members, tails)
    evaluated = original['evidence']['evaluated']; base_index = original['evidence']['selected_index']
    indices = menu_indices(original); selected = base_index; scores = []; selection = None
    counters = Counter({key: 0 for key in ('dynamic_reference_H_searches_checked', 'dynamic_reference_CS_calls_checked',
        'dynamic_reference_CS_menus_evaluated', 'dynamic_singleton_H_searches_checked', 'dynamic_score_members_checked')})
    original_joint = _nested_joint(original); plans = original_joint['evidence']['baseline_proposals']
    models = [public_model(m['inputs']) for m in members]
    table = joint.enumerate_pairs(models, [p['send'] for p in plans])
    require(type(evidence['row_scores']) is list and len(evidence['row_scores']) == len(indices), 'bounded row menu coverage differs')
    for position, (index, recorded) in enumerate(zip(indices, evidence['row_scores'])):
        require(type(index) is int and 0 <= index < len(table['combinations']) and table['combinations'][index]['feasible'],
            'current row is outside the original independent feasible menu')
        score, row_counters = audit_row(recorded, index, position, table['combinations'][index]['hospitals'],
            models, members, context, tails, priority_context)
        scores.append(score); counters.update(row_counters)
    if evaluated:
        selection = base.global_staged_choice(scores, base_index)
        selection['base_selected_tied'] = selection.pop('base_budget_tied')
        selected = selection['selected_index']
    intervened = selected != base_index
    wanted = dict(schema_version=1, rule=RULE, base_policy=BASE_POLICY, base_source_sha256=BASE_SOURCE_SHA256,
        identity_projection=dict(source_policy=NAME, calculation_policy=BASE_POLICY,
            fields=['joint_context.eligible[].job.policy_name', 'members[].workflow_snapshot.job.policy_name',
                'tail_members[].workflow_snapshot.job.policy_name'], copied_only=True, public_identity_unchanged=True),
        joint_context=context, priority_context=priority_context, original_cohort_success_proposal=original,
        evaluated=evaluated, menu_indices=indices, row_scores=scores, selection=selection, base_selected_index=base_index,
        selected_index=selected, intervened=intervened, tiers=[4,2,1],
        tolerances=dict(probability=P_EPSILON, wait_seconds=W_EPSILON), objective=OBJECTIVE, scope=SCOPE)
    precise_same(evidence, wanted, 'bounded dynamic priority selection')
    require(type(outputs) is list and len(outputs) == 2, 'actual dynamic output coverage differs')
    for index in range(2):
        actual = deepcopy(original['decisions'][index])
        if intervened:
            chosen = table['combinations'][selected]; send = chosen['hospitals'][index]
            actual = deepcopy(plans[index]); actual['send'] = list(send); actual['reason'] = 'cohort_dynamic_current_only'
            ev = actual['evidence']; ev['sent_hospitals'] = list(send)
            ev['planned_expected_cost'] = round(chosen['member_metrics'][index]['total_cost_seconds'], 3)
            for row in ev['candidate_plan']:
                row['planned_offset_seconds'] = 0 if row['hospital'] in send else None
        actual['evidence']['policy'] = NAME
        actual['evidence']['cohort_dynamic'] = dict(schema_version=1, mode='pair_cohort_dynamic', evaluated=evaluated,
            intervened=intervened, member_index=index, base_selected_index=base_index, selected_index=selected,
            effective_priority=priorities[_pid(members[index]['patient_id'])])
        precise_same(outputs[index], actual, 'actual separately rebuilt dynamic decision')
    checked = dict(checked, original_CS_groups_applied=checked['joint_groups_applied'],
        joint_groups_applied=int(selected != table['baseline_index']))
    return dict(checked, **dict(counters), dynamic_menus_evaluated=int(evaluated), dynamic_rows_checked=len(scores),
        dynamic_interventions=int(intervened))


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
                and output['evidence'].get('cohort_success') == dict(schema_version=1, mode='horizon_fallback', evaluated=False, intervened=False)
                and output['evidence'].get('cohort_dynamic') == dict(schema_version=1, mode='horizon_fallback', evaluated=False, intervened=False),
                'ungrouped decision changed the original H fallback')
            normalized = deepcopy(output); normalized['evidence'].pop('joint_first')
            normalized['evidence'].pop('joint_tail')
            normalized['evidence'].pop('joint_budget')
            normalized['evidence'].pop('cohort_success')
            normalized['evidence'].pop('cohort_dynamic')
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
            intact_original_CS_proposal_independently_audited=True,
            row_specific_reference_CS_proposals_independently_audited=True,
            conditional_reference_CS_current_H_searches_independently_reexecuted=True,
            all_current_and_continuation_score_models_independently_rebuilt=True,
            tier_success_global_staged_selection_independently_recomputed=True,
            priority_values_reconstructed_from_event_payloads=False,
            priority_scope='captured priority rows bind complete current cohort identities, versions, clock, prefix and revision; frozen lab payload independently implies unknown/None/effective4; general live values require frozen capture/prewrite checks and rollback tests',
            projected_first_contact_H_coordinate_search_independently_reexecuted=True,
            paired_count_comparisons_independently_reconstructed=True,
            actual_dynamic_outputs_and_sends_independently_audited=True,
            tail_opportunity_scope='zero unresolved public inquiry count after proposed immediate pair; not free workers, beds or a success guarantee',
            H_coordinate_descent_independently_reexecuted=False,
            H_coordinate_descent_scope='actual original CS current H baseline and fallback remain source-bound; each conditional reference-CS current H baseline, its projected first contacts and per-row singleton are independently reexecuted; no clock advance or native-dynamic recursive optimization',
            physical_transaction_boundary_from_raw_proved=False,
            atomicity_evidence_scope='all-member checked common snapshots, linked two next decisions and ordered subsequent sends are audited; physical atomicity requires frozen adapter source and second-member rollback tests',
            silent_workflow_field_scope='next-review/version remain captured public job provenance; zero-journal active review equals latest start; member versions link actual snapshots to committed decisions',
            scope='complete current first-contact cohort and priorities; intact original CS proposal; at most3 source-derived rows and separately rebuilt same-clock conditional frozen-CS suffixes and own selected-send metrics; reference-policy actions can differ from later native actions; already-queried owners outside score; actual all-patient FIFO/W/J/failure/contact/work audited')
    except (KeyError, TypeError, IndexError, AttributeError, OverflowError, StopIteration) as error:
        raise CohortDynamicAuditError('malformed saved cohort-dynamic artifact') from error


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
