"""Independent public same-clock first-contact budget projection audit.

The frozen TAIL proposal is checked intact. Counterfactual H coordinate
searches and JOINT pairs are reconstructed separately from current public
inputs; virtual jobs never acquire actual journal IDs, versions or replies.
"""
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from . import joint_tail_audit as base
from . import joint_reselect_audit as controls

BASE_AUDITOR_SHA256 = '434915f00fff3d16468960ac08c173bb9159c76d7ef17560dad4e233e0aae750'
BASE_SOURCE_SHA256 = '77a683c08dec240d5a18d333d37f8fc7fae53ca62b6e3f73877c50cdf1365e07'
CONTROLS_AUDITOR_SHA256 = '2b129b870e853deb502977fec438c1727d67663b39ad9be437f4a25801fe3eb3'
ROOT = Path(__file__).resolve().parents[1]
if hashlib.sha256(Path(base.__file__).read_bytes()).hexdigest() != BASE_AUDITOR_SHA256:
    raise ValueError('frozen tail independent auditor differs')
if hashlib.sha256((ROOT/'inquiry_lab/joint_tail_policy.py').read_bytes()).hexdigest() != BASE_SOURCE_SHA256:
    raise ValueError('frozen tail producer source differs')
if hashlib.sha256(Path(controls.__file__).read_bytes()).hexdigest() != CONTROLS_AUDITOR_SHA256:
    raise ValueError('frozen rejected-A independent control auditor differs')
NAME = 'eat_joint_budget_l15_f'
BASE_POLICY = base.NAME
RULE = 'cancel_tail_veto_when_a_later_paired_first_contact_count_declines'
PROJECTION_POLICY = 'eat_joint_first_l15_f'
PROJECTION_SOURCE_SHA256 = 'c176b2c5e4fd4ccb459229efb86661ad6120c8f39525414352738e40ee7f6dcc'
PROJECTION_RULE = ('fixed eligible[2:] paired in order; simultaneous H counts within each frozen JOINT pair; '
    'append that pair selected immediate inquiries before the next pair; fixed now, sources and versions')
SINGLETON_RULE = 'capture and original TAIL guard retained; final odd singleton excluded only from count projection'
parent, horizon, joint = base.parent, base.horizon, base.base
JointBudgetAuditError = base.JointTailAuditError
require, number, EPOCH = base.require, base.number, base.EPOCH
_same, _pid, _integer = base._same, base._pid, base._integer
INPUT_KEYS, CALL_KEYS, GROUP_KEYS = base.INPUT_KEYS, base.CALL_KEYS, base.GROUP_KEYS
NEW_INSTRUMENTATION = dict(base.NEW_INSTRUMENTATION,
    actual_workflow='inquiry_lab.joint_budget_runner.run_once', projected_paired_contact_budget_veto_review=True)
NEW_INSTRUMENTATION.pop('winner_only_opportunity_veto')


def first_contact_search(inputs):
    """Reexecute frozen H coordinate search using independent stdlib costs.

This specialization is deliberately limited to captured zero-journal owners.
All arrays use seconds; the frozen five-second grid makes the allowed start
seconds exactly equivalent to the producer's cell-index loop.
"""
    require(type(inputs) is dict and set(inputs) == INPUT_KEYS and not horizon._contains_private(inputs),
        'unknown or private first-contact projection inputs')
    require(inputs['patient_pending'] == [] and inputs['last_contact_at'] is None,
        'independent H search requires a first-contact owner')
    require(type(inputs['now']) is int and type(inputs['max_new']) is int and 0 <= inputs['max_new'] <= 10,
        'invalid first-contact clock or budget')
    context = inputs['time_context']; now = inputs['now']
    require(now < min(context['auto_inquiry_deadline_at'], context['evaluation_end_at']),
        'projected first contact is beyond current public A/H')
    model = horizon.model_from_inputs(inputs)
    require(model['current'] == [] and model['fresh'] == 0 and model['room'] == min(inputs['max_new'], 4),
        'first-contact specialization changed H pending room')
    pool = [r['hospital'] for r in model['pool']]
    options = [None]+list(model['offsets'])
    offsets = {h: None for h in pool}; cost = horizon.plan_cost(model, offsets)
    passes = 0
    for _ in range(2):
        passes += 1; improved = False
        for hospital in pool:
            for offset in options:
                if offset == offsets[hospital]:
                    continue
                if offset == 0 and offsets[hospital] != 0 and sum(d == 0 for d in offsets.values()) >= model['room']:
                    continue
                trial = dict(offsets); trial[hospital] = offset
                candidate_cost = horizon.plan_cost(model, trial)
                if candidate_cost < cost-1e-9:
                    offsets, cost, improved = trial, candidate_cost, True
        if not improved:
            break
    send = [h for h in pool if offsets[h] == 0]
    forced = False
    if not send and pool and model['room'] > 0:
        # Retain every other chosen future offset. The safety fallback can
        # raise the objective and uses stable original-pool order after cost
        # and travel ties, exactly as the frozen H rule specifies.
        by_hospital = {r['hospital']: r for r in model['pool']}
        chosen = min(pool, key=lambda h: (horizon.plan_cost(model, dict(offsets, **{h: 0})),
            by_hospital[h]['travel_seconds']))
        offsets = dict(offsets, **{chosen: 0}); cost = horizon.plan_cost(model, offsets)
        send = [chosen]; forced = True
    return dict(offsets=offsets, send=send, cost=cost, passes=passes, forced=forced)


def audit_first_contact_search(inputs, proposal):
    """A legal recorded plan is insufficient: its chosen offsets must match."""
    checked = horizon.audit_horizon(inputs, proposal)
    expected = first_contact_search(inputs)
    actual = {r['hospital']: r['planned_offset_seconds'] for r in proposal['evidence']['candidate_plan']}
    _same(actual, expected['offsets'], 'independent projected H coordinate-search offsets')
    _same(proposal['send'], expected['send'], 'independent projected H immediate count/order')
    require(proposal['evidence']['planned_expected_cost'] == round(expected['cost'], 3),
        'independent projected H optimum utility differs')
    return dict(checked, projected_H_searches_independently_reexecuted=1)


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
    require(value['workflow_snapshot']['job']['policy_name'] == NAME, 'member job has the wrong native policy')
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


def _virtual_appends(ledger, members, sends, now, kind, step_index, path):
    """A virtual inquiry references a proposal location, never a DB event ID."""
    rows = []
    for member_index, (member, send) in enumerate(zip(members, sends)):
        for send_index, hospital in enumerate(send):
            inquiry = dict(hospital=hospital, patient=member['patient_id'], submitted_at=now)
            ledger.append(deepcopy(inquiry))
            rows.append(dict(inquiry=inquiry, source=dict(kind=kind, step_index=step_index,
                member_index=member_index, send_index=send_index,
                proposal_path=path.format(member=member_index, send=send_index))))
    return rows


def audit_projection(path, recorded, members, context, tails, original_joint):
    """Rebuild the exact suffix and both H searches before each JOINT table."""
    require(type(recorded) is dict and set(recorded) == {'path', 'initial_pending',
        'initial_appended_inquiries', 'paired_patient_ids', 'excluded_singleton_patient_ids', 'steps', 'final_pending'},
        'unknown or incomplete paired projection')
    first_context = base.alias_context(alias_context(context))
    pair = [base.alias_member(alias_member(m)) for m in members]
    first_tails = [base.alias_member(alias_member(m)) for m in tails]
    ledger = deepcopy(members[0]['inputs']['pending']); initial_pending = deepcopy(ledger)
    prefix = 'original_tail_proposal.evidence.original_joint_proposal.'
    if path == 'baseline':
        sends = [p['send'] for p in original_joint['evidence']['baseline_proposals']]
        source = prefix+'evidence.baseline_proposals[{member}].send[{send}]'
    else:
        require(path == 'winner', 'unknown projection path')
        sends = [p['send'] for p in original_joint['decisions']]
        source = prefix+'decisions[{member}].send[{send}]'
    initial = _virtual_appends(ledger, pair, sends, context['now'], 'current_pair_'+path, None, source)
    count = len(tails)//2
    require(type(recorded['steps']) is list and len(recorded['steps']) == count,
        'projected paired suffix omitted or added patients')
    steps, counters = [], Counter()
    for step_index in range(count):
        start = 2*step_index; saved = recorded['steps'][step_index]
        require(type(saved) is dict and set(saved) == {'step_index', 'source_tail_member_indices',
            'joint_context', 'members', 'proposal', 'appended_inquiries'}, 'unknown projected step fields')
        projected_members = deepcopy(first_tails[start:start+2])
        for member in projected_members:
            member['inputs']['pending'] = deepcopy(ledger)
        projected_context = deepcopy(first_context)
        projected_context['eligible'] = projected_context['eligible'][start+2:]
        projected_context['pair_patient_ids'] = [m['patient_id'] for m in projected_members]
        _same(saved['members'], projected_members, 'projected public source and virtual-only pending')
        _same(saved['joint_context'], projected_context, 'projected fixed-clock ordered suffix')
        proposal = saved['proposal']
        require(type(proposal) is dict and set(proposal) == {'decisions', 'evidence'},
            'missing complete projected JOINT proposal')
        checked = joint.audit_pair(projected_members, projected_context, proposal['decisions'], proposal['evidence'])
        for member, plan in zip(projected_members, proposal['evidence']['baseline_proposals']):
            audit_first_contact_search(member['inputs'], plan)
            counters['projected_H_searches_independently_reexecuted'] += 1
        counters['projected_pairs_checked'] += 1
        counters['projected_joint_combinations_checked'] += checked['joint_combinations_checked']
        appends = _virtual_appends(ledger, projected_members, [d['send'] for d in proposal['decisions']],
            context['now'], 'projected_pair_selected', step_index,
            'projections.'+path+'.steps['+str(step_index)+'].proposal.decisions[{member}].send[{send}]')
        steps.append(dict(step_index=step_index, source_tail_member_indices=[start, start+1],
            joint_context=projected_context, members=projected_members, proposal=proposal,
            appended_inquiries=appends))
    wanted = dict(path=path, initial_pending=initial_pending, initial_appended_inquiries=initial,
        paired_patient_ids=[[m['patient_id'] for m in first_tails[2*i:2*i+2]] for i in range(count)],
        excluded_singleton_patient_ids=[tails[-1]['patient_id']] if len(tails)%2 else [],
        steps=steps, final_pending=ledger)
    _same(recorded, wanted, 'complete independently rebuilt hypothetical ledger')
    return dict(counters)


def audit_pair(members, context, tails, outputs, evidence):
    """Keep the original TAIL proposal intact and audit the binary decision."""
    require(type(evidence) is dict and type(evidence['original_tail_proposal']) is dict
        and set(evidence['original_tail_proposal']) == {'decisions', 'evidence'},
        'missing intact original TAIL proposal')
    original = evidence['original_tail_proposal']
    checked = base.audit_pair([alias_member(m) for m in members], alias_context(context),
        [alias_member(m) for m in tails], original['decisions'], original['evidence'])
    validate_tail_members(members, context, tails)
    evaluated = original['evidence']['vetoed']; joint_proposal = original['evidence']['original_joint_proposal']
    comparisons = []; projections = dict(baseline=None, winner=None); counters = Counter()
    if evaluated:
        require(type(evidence['projections']) is dict and set(evidence['projections']) == {'baseline', 'winner'},
            'paired budget comparison needs both fixed source branches')
        for path in projections:
            projections[path] = evidence['projections'][path]
            counters.update(audit_projection(path, projections[path], members, context, tails, joint_proposal))
        for index, (baseline, winner) in enumerate(zip(projections['baseline']['steps'], projections['winner']['steps'])):
            for member_index, member in enumerate(baseline['members']):
                before = baseline['proposal']['evidence']['cardinalities'][member_index]
                after = winner['proposal']['evidence']['cardinalities'][member_index]
                comparisons.append(dict(patient_id=member['patient_id'], projection_step=index,
                    member_index=member_index, baseline_k=before, winner_k=after, declined=before < after))
    declining = [c['patient_id'] for c in comparisons if c['declined']]
    cancelled = bool(declining)
    selected = joint_proposal['evidence']['selected_index'] if cancelled else original['evidence']['actual_selected_index']
    wanted = dict(schema_version=1, rule=RULE, base_policy=BASE_POLICY, base_source_sha256=BASE_SOURCE_SHA256,
        projection_policy=PROJECTION_POLICY, projection_source_sha256=PROJECTION_SOURCE_SHA256,
        identity_projection=dict(source_policy=NAME, calculation_policy=BASE_POLICY,
            fields=['joint_context.eligible[].job.policy_name', 'members[].workflow_snapshot.job.policy_name',
                'tail_members[].workflow_snapshot.job.policy_name'], copied_only=True, public_identity_unchanged=True),
        joint_context=context, original_tail_proposal=original, projection_evaluated=evaluated,
        projections=projections, paired_count_comparisons=comparisons, declining_patients=declining,
        cancelled_tail_veto=cancelled, actual_selected_index=selected,
        projection_rule=PROJECTION_RULE, singleton_rule=SINGLETON_RULE,
        scope='same-clock public-input inquiry arithmetic only; no future replies, selections or outcome rollout')
    _same(evidence, wanted, 'independent paired-contact budget veto review')
    require(type(outputs) is list and len(outputs) == 2, 'actual budget-guard output coverage differs')
    allowed = joint_proposal['decisions'] if cancelled else original['decisions']
    for index, decision in enumerate(allowed):
        actual = deepcopy(decision); actual['evidence']['policy'] = NAME
        actual['evidence']['joint_budget'] = dict(schema_version=1, mode='pair_paired_count_budget_guard',
            projection_evaluated=evaluated, original_tail_vetoed=original['evidence']['vetoed'],
            cancelled_tail_veto=cancelled, declining_patients=declining, actual_selected_index=selected)
        _same(outputs[index], actual, 'actual original baseline-or-winner committed decision')
    checked = dict(checked, original_tail_groups_applied=checked['joint_groups_applied'],
        joint_groups_applied=int(joint_proposal['evidence']['applied'] and (not evaluated or cancelled)))
    return dict(checked, **dict(counters), paired_budget_veto_reviews=int(evaluated),
        paired_budget_veto_cancellations=int(cancelled), paired_budget_members_checked=len(comparisons))


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
                and output['evidence'].get('joint_budget') == dict(schema_version=1, mode='horizon_fallback', projection_evaluated=False),
                'ungrouped decision changed the original H fallback')
            normalized = deepcopy(output); normalized['evidence'].pop('joint_first')
            normalized['evidence'].pop('joint_tail')
            normalized['evidence'].pop('joint_budget')
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
        return controls.audit_branch(scenario, branch)
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
            projected_first_contact_H_coordinate_search_independently_reexecuted=True,
            paired_count_comparisons_independently_reconstructed=True,
            actual_budget_outputs_and_sends_independently_audited=True,
            tail_opportunity_scope='zero unresolved public inquiry count after proposed immediate pair; not free workers, beds or a success guarantee',
            H_coordinate_descent_independently_reexecuted=False,
            H_coordinate_descent_scope='actual H baseline proposals and fallback remain source-bound; projected zero-journal H searches independently reexecuted',
            physical_transaction_boundary_from_raw_proved=False,
            atomicity_evidence_scope='all-member checked common snapshots, linked two next decisions and ordered subsequent sends are audited; physical atomicity requires frozen adapter source and second-member rollback tests',
            silent_workflow_field_scope='next-review/version remain captured public job provenance; zero-journal active review equals latest start; member versions link actual snapshots to committed decisions',
            scope='complete current pair and prospective tail roster/versions/offers/prefixes; intact frozen joint proposal under explicit name-only input projection; same-clock source-bound paired suffix, independently selected projected H plans/JOINT rows and binary veto cancellation; original seven/H inputs; actual committed sends/FIFO/all-patient W/J/failure/contact/work')
    except (KeyError, TypeError, IndexError, AttributeError, OverflowError, StopIteration) as error:
        raise JointBudgetAuditError('malformed saved joint-budget artifact') from error


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
