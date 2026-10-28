"""Independent finite-table selection with public tail-opportunity constraints.

The intact tail proposal (including its original joint table) is audited as a
proposal. The constrained winner and actual committed outputs are then checked
separately. No producer, runner, solver or hidden-state model is imported.
"""
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from . import joint_tail_audit as base

BASE_AUDITOR_SHA256 = '434915f00fff3d16468960ac08c173bb9159c76d7ef17560dad4e233e0aae750'
BASE_SOURCE_SHA256 = '77a683c08dec240d5a18d333d37f8fc7fae53ca62b6e3f73877c50cdf1365e07'
ROOT = Path(__file__).resolve().parents[1]
if hashlib.sha256(Path(base.__file__).read_bytes()).hexdigest() != BASE_AUDITOR_SHA256:
    raise ValueError('frozen tail independent auditor differs')
if hashlib.sha256((ROOT/'inquiry_lab/joint_tail_policy.py').read_bytes()).hexdigest() != BASE_SOURCE_SHA256:
    raise ValueError('frozen tail producer source differs')
NAME = 'eat_joint_reselect_l15_f'
BASE_POLICY = base.NAME
RULE = 'global_pair_minimum_with_public_tail_option_constraints'
parent, horizon = base.parent, base.horizon
JointReselectAuditError = base.JointTailAuditError
require, number, EPOCH = base.require, base.number, base.EPOCH
_same, _pid, _integer = base._same, base._pid, base._integer
INPUT_KEYS, CALL_KEYS, GROUP_KEYS = base.INPUT_KEYS, base.CALL_KEYS, base.GROUP_KEYS
WAIT_EPSILON = 1e-9
NEW_INSTRUMENTATION = dict(base.NEW_INSTRUMENTATION,
    actual_workflow='inquiry_lab.joint_reselect_runner.run_once',
    tail_safe_global_reselection=True)
NEW_INSTRUMENTATION.pop('winner_only_opportunity_veto')


def _project_name(name):
    return BASE_POLICY if name == NAME else '__other_policy__:'+str(name)


def alias_context(context):
    value = deepcopy(context)
    for row in value['eligible']:
        require(row['job']['policy_name'] == NAME, 'eligible public job has the wrong native policy')
        row['job']['policy_name'] = BASE_POLICY
    return value


def alias_member(member):
    value = deepcopy(member)
    require(value['workflow_snapshot']['job']['policy_name'] == NAME,
        'member job has the wrong native policy')
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


def constrained_table(members, tails, original_joint_evidence):
    """Check each independently verified joint row against each public tail."""
    pending = members[0]['inputs']['pending']
    baseline_sends = original_joint_evidence['baseline_combination']['hospitals']
    pools = [(t['patient_id'], [r['hospital'] for r in
        horizon._ranked(t['inputs']['candidates'])[:min(6, t['inputs']['max_new'])]]) for t in tails]

    def zeros(pool, sends):
        return [h for h in pool if sum(q['hospital'] == h for q in pending)
            + sum(h in send for send in sends) == 0]

    baseline_zero = {pid: zeros(pool, baseline_sends) for pid, pool in pools}
    rows = []
    for index, row in enumerate(original_joint_evidence['combinations']):
        blocked = [pid for pid, pool in pools if baseline_zero[pid] and not zeros(pool, row['hospitals'])]
        rows.append(dict(index=index, joint_feasible=row['feasible'], tail_feasible=not blocked,
            feasible=row['feasible'] and not blocked, blocked_tail_patients=blocked))
    feasible = [r['index'] for r in rows if r['feasible']]
    baseline_index = original_joint_evidence['baseline_index']
    require(baseline_index in feasible, 'unchanged baseline is absent from constrained feasible set')
    table = original_joint_evidence['combinations']
    minimum = min(table[i]['sum_capped_wait_seconds'] for i in feasible)
    best = next(i for i in feasible if table[i]['sum_capped_wait_seconds'] <= minimum+WAIT_EPSILON)
    tied = table[baseline_index]['sum_capped_wait_seconds'] <= minimum+WAIT_EPSILON
    selected = baseline_index if tied else best
    applied = table[baseline_index]['sum_capped_wait_seconds']-table[selected]['sum_capped_wait_seconds'] > WAIT_EPSILON
    return dict(row_feasibility=rows, minimum_feasible_wait_seconds=minimum, best_index=best,
        baseline_best_tied=tied, selected_index=selected if applied else baseline_index, applied=applied)


def selected_tail_checks(members, tails, joint, selected_index):
    pending = members[0]['inputs']['pending']
    baseline = joint['baseline_combination']['hospitals']
    selected = joint['combinations'][selected_index]['hospitals']
    result = []
    for tail in tails:
        pool = [r['hospital'] for r in horizon._ranked(tail['inputs']['candidates'])[:min(6, tail['inputs']['max_new'])]]
        def counts(sends):
            return {h: sum(q['hospital'] == h for q in pending)+sum(h in s for s in sends) for h in pool}
        before, after = counts(baseline), counts(selected)
        zero_before = [h for h in pool if before[h] == 0]
        zero_after = [h for h in pool if after[h] == 0]
        result.append(dict(patient_id=tail['patient_id'], candidate_pool=pool,
            baseline_pending_counts=before, selected_pending_counts=after,
            baseline_zero_pending_hospitals=zero_before, selected_zero_pending_hospitals=zero_after,
            triggered=bool(zero_before) and not zero_after))
    return result


def audit_pair(members, context, tails, outputs, evidence):
    """Audit both nested proposals intact, then the independently selected row."""
    require(type(evidence) is dict and type(evidence['original_tail_proposal']) is dict
        and set(evidence['original_tail_proposal']) == {'decisions', 'evidence'},
        'missing intact original tail proposal')
    original = evidence['original_tail_proposal']
    checked = base.audit_pair([alias_member(m) for m in members], alias_context(context),
        [alias_member(m) for m in tails], original['decisions'], original['evidence'])
    validate_tail_members(members, context, tails)
    joint = original['evidence']['original_joint_proposal']['evidence']
    selection = constrained_table(members, tails, joint)
    selected_index, applied = selection['selected_index'], selection['applied']
    checks = selected_tail_checks(members, tails, joint, selected_index)
    opportunities = [dict(patient_id=t['patient_id'], hospitals=[r['hospital'] for r in
        horizon._ranked(t['inputs']['candidates'])[:min(6, t['inputs']['max_new'])]]) for t in tails]
    wanted = dict(schema_version=1, rule=RULE, base_policy=BASE_POLICY, base_source_sha256=BASE_SOURCE_SHA256,
        identity_projection=dict(source_policy=NAME, calculation_policy=BASE_POLICY,
            fields=['joint_context.eligible[].job.policy_name', 'members[].workflow_snapshot.job.policy_name',
                'tail_members[].workflow_snapshot.job.policy_name'], copied_only=True, public_identity_unchanged=True),
        joint_context=context, original_tail_proposal=original, tail_opportunities=opportunities,
        **selection, selected_tail_checks=checks, wait_epsilon_seconds=WAIT_EPSILON,
        tie_rule='baseline when best-tied; otherwise first original-pool row within 1e-9 of global feasible minimum',
        feasibility_rule='unchanged original pair P/cost feasibility and no elimination of a tail last public zero-pending option',
        scope='all original rows; current public options only; no changes to counts, model, grouping, order or H fallback')
    _same(evidence, wanted, 'independent all-row tail-safe selection')
    require(type(outputs) is list and len(outputs) == 2, 'actual constrained output coverage differs')
    selected = joint['combinations'][selected_index]
    for index, proposal in enumerate(joint['baseline_proposals']):
        actual = deepcopy(proposal); hospitals = selected['hospitals'][index]
        actual['send'] = list(hospitals)
        actual['reason'] = 'joint_reselect_current_only' if applied else 'joint_reselect_baseline_current_only'
        ev = actual['evidence']; ev['policy'] = NAME; ev['sent_hospitals'] = list(hospitals)
        ev['planned_expected_cost'] = round(selected['member_metrics'][index]['total_cost_seconds'], 3)
        for row in ev['candidate_plan']:
            row['planned_offset_seconds'] = 0 if row['hospital'] in hospitals else None
        ev['joint_first'] = dict(schema_version=1, mode='pair_current_only', member_index=index,
            current_only=True, applied=applied,
            baseline_send=list(joint['baseline_combination']['hospitals'][index]), selected_send=list(hospitals),
            baseline_metrics=joint['baseline_combination']['member_metrics'][index],
            selected_metrics=selected['member_metrics'][index],
            constraint_reference='common-prefix simultaneous immediate-only H sends')
        ev['joint_reselect'] = dict(schema_version=1, mode='pair_tail_safe_reselection', member_index=index,
            selected_index=selected_index, baseline_index=joint['baseline_index'],
            original_joint_selected_index=joint['selected_index'],
            original_tail_selected_index=original['evidence']['actual_selected_index'], tail_safe=True, applied=applied)
        _same(outputs[index], actual, 'actual reselected committed decision')
    checked = dict(checked, original_tail_groups_applied=checked['joint_groups_applied'],
        joint_groups_applied=int(applied))
    return dict(checked, constrained_rows_checked=len(selection['row_feasibility']),
        constrained_rows_feasible=sum(r['feasible'] for r in selection['row_feasibility']),
        tail_reselected_groups=int(selected_index != original['evidence']['actual_selected_index']),
        selected_tail_checks_evaluated=len(checks))


def audit_member_provenance(scenario, events, member, event_prefix, decision_prefix):
    return base.audit_member_provenance(scenario, _alias_events(events), alias_member(member),
        event_prefix, decision_prefix)


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
        checks.update(audit_pair(group['members'], wanted, group['tail_members'], group['outputs'], group['evidence']))
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
                and output['evidence'].get('joint_reselect') == dict(schema_version=1, mode='horizon_fallback', reselection_evaluated=False),
                'ungrouped decision changed the original H fallback')
            normalized = deepcopy(output); normalized['evidence'].pop('joint_first')
            normalized['evidence'].pop('joint_tail')
            normalized['evidence'].pop('joint_reselect')
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
            all_joint_rows_tail_feasibility_independently_checked=True,
            actual_reselected_outputs_and_sends_independently_audited=True,
            tail_opportunity_scope='zero unresolved public inquiry count after proposed immediate pair; not free workers, beds or a success guarantee',
            H_coordinate_descent_independently_reexecuted=False, physical_transaction_boundary_from_raw_proved=False,
            atomicity_evidence_scope='all-member checked common snapshots, linked two next decisions and ordered subsequent sends are audited; physical atomicity requires frozen adapter source and second-member rollback tests',
            silent_workflow_field_scope='next-review/version remain captured public job provenance; zero-journal active review equals latest start; member versions link actual snapshots to committed decisions',
            scope='complete current pair and prospective tail roster/versions/offers/prefixes; intact frozen joint proposal under explicit name-only input projection; global constrained finite-table minimum and actual selected row; original seven/H inputs; actual committed sends/FIFO/all-patient W/J/failure/contact/work')
    except (KeyError, TypeError, IndexError, AttributeError, OverflowError, StopIteration) as error:
        raise JointReselectAuditError('malformed saved joint-reselect artifact') from error


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
