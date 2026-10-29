"""Independent wait-first selection inside the intact Pareto admissible set.

Only the objective ordering changes. The complete frozen Pareto/Fullmenu score
and owner-guard proof remains separately checked; no producer model, policy,
future event, actual outcome or extra scoring call is imported.
"""
from collections import Counter
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
from . import cohort_pareto_audit as foundation
control_audit=foundation

ROOT=Path(__file__).resolve().parents[1]
if hashlib.sha256(Path(foundation.__file__).read_bytes()).hexdigest()!='a67d752aa3c679542c7eefcf4ad4b94ec74de74247c68c51e6c6559446291b88':
    raise ValueError('frozen independent Pareto auditor differs')
base=foundation.base
parent,horizon,joint=foundation.parent,foundation.horizon,foundation.joint
numeric=foundation.numeric
dynamic=foundation.dynamic
require,precise_same,exact_copy=foundation.require,foundation.precise_same,foundation.exact_copy
_pid,_integer,EPOCH=foundation._pid,foundation._integer,foundation.EPOCH
INPUT_KEYS,CALL_KEYS,GROUP_KEYS=foundation.INPUT_KEYS,foundation.CALL_KEYS,foundation.GROUP_KEYS
NAME='eat_cohort_waitpareto_l15_f'
BASE_POLICY=foundation.NAME
BASE_SOURCE_SHA256='f61ef3ddd7e795508082419e8494bff225f916cf29b8a56c8b3a24017dd68122'
if hashlib.sha256((ROOT/'inquiry_lab/cohort_pareto_policy.py').read_bytes()).hexdigest()!=BASE_SOURCE_SHA256:
    raise ValueError('frozen Pareto producer source differs')
NEW_INSTRUMENTATION=dict(control_audit.NEW_INSTRUMENTATION,
    actual_workflow='inquiry_lab.cohort_waitpareto_runner.run_once',protected_wait_first_selection=True)


def _project_name(name):
    if name==NAME:return BASE_POLICY
    return '__foreign_cohort_pareto__' if name==BASE_POLICY else name


def alias_context(context):
    value=deepcopy(context)
    for row in value['eligible']:
        require(row['job']['policy_name']==NAME,'current eligible job has wrong native followplan policy')
        row['job']['policy_name']=BASE_POLICY
    return value


def alias_member(member):
    value=deepcopy(member)
    require(value['workflow_snapshot']['job']['policy_name']==NAME,'current member has wrong native followplan policy')
    value['workflow_snapshot']['job']['policy_name']=BASE_POLICY
    return value


def alias_owners(owners):
    value=deepcopy(owners)
    for row in value['inventory']:
        if row['job'] is not None:row['job']['policy_name']=_project_name(row['job']['policy_name'])
    return value


def alias_events(events):
    value=deepcopy(events)
    for event in value:
        if event['type'] in ('inquiry_workflow_started','inquiry_workflow_paused','inquiry_policy_changed'):
            data=parent._data(event)
            if 'policy_name' in data:event['data']=dict(data,policy_name=_project_name(data['policy_name']))
    return value


def alias_registry(registry):
    value=deepcopy(registry)
    for row in value['jobs']:row['policy_name']=_project_name(row['policy_name'])
    return value


def alias_source(source):
    value=deepcopy(source);value['events']=alias_events(value['events'])
    for row in value['rows']:
        if row['job'] is not None:row['job']['policy_name']=_project_name(row['job']['policy_name'])
    return value


def audit_registry(events,decisions,registry,now,event_prefix,decision_prefix):
    value=foundation.audit_registry(alias_events(events),decisions,alias_registry(registry),now,event_prefix,decision_prefix)
    for row in value['eligible']:row['job']['policy_name']=NAME
    return value


def audit_priority_context(priority,context,members,tails,*,lab_defaults=False):
    return foundation.audit_priority_context(priority,alias_context(context),[alias_member(m) for m in members],
        [alias_member(m) for m in tails],lab_defaults=lab_defaults)


def audit_time_context(events,inputs,member,event_prefix,decision_prefix,decision=None):
    return foundation.audit_time_context(alias_events(events),inputs,alias_member(member),event_prefix,decision_prefix,decision)


def audit_owner_provenance(scenario,events,registry,owners,source):
    return foundation.audit_owner_provenance(scenario,alias_events(events),alias_registry(registry),alias_owners(owners),alias_source(source))


def audit_member_provenance(scenario,events,member,event_prefix,decision_prefix):
    return foundation.audit_member_provenance(scenario,alias_events(events),alias_member(member),event_prefix,decision_prefix)



PROBABILITY_EPSILON=1e-12
WAIT_EPSILON=1e-9
SELECT_SOURCE_SHA256={
    'cohort_pareto_model':'3292bae7a0c2d0017e7fa39a80adc60819e4f23879b48e2d871975aeb343be91',
    'cohort_pareto_policy':'f61ef3ddd7e795508082419e8494bff225f916cf29b8a56c8b3a24017dd68122',
    'cohort_dynamic_policy':'62ef7fbba3ee90015f800aae7fb38f820955c7b92cd46d35df216c9a54afccfb',
}
for module,digest in SELECT_SOURCE_SHA256.items():
    require(hashlib.sha256((ROOT/'inquiry_lab'/(module+'.py')).read_bytes()).hexdigest()==digest,
        'frozen protected-wait selector source differs: '+module)
SELECT_RULE='minimum_wait_band_then_priority_probability_bands_with_Stoploss_anchor'
SELECT_SCOPE='unchanged complete Fullmenu scores and Pareto-admitted rows; minimize total capped wait within existing1e-9 band, then existing tier4,2,1 probability bands, original embedded Stoploss anchor and source order; baseline wait bound follows from baseline admissibility, no extra wait cap or new guard; no realized outcome guarantee'
RULE='intact_Pareto_guard_and_scores_with_wait_first_selection'
OBJECTIVE='among unchanged Pareto-admitted rows minimize total capped wait within1e-9, then tier4,2,1 probability bands within1e-12, then original embedded Stoploss actual index and unchanged source row order'
SCOPE='unchanged complete parent Pareto proposal, owner-probability guard, Fullmenu scores, activation and actual constraints; reorder selector objectives only; baseline wait bound follows from admitted Stoploss row without an extra cap; current pair only, no realized success or waiting guarantee'


def select_protected_wait(row_scores,*,admissible_row_indices,baseline_index,owner_inventory,menu_indices,feasible_row_indices):
    """Rebuild the unchanged guard, then stage wait before tier probabilities."""
    checked=foundation.guard_rows(row_scores,baseline_index=baseline_index,owner_inventory=owner_inventory,
        menu_indices=menu_indices,feasible_row_indices=feasible_row_indices)
    require(type(admissible_row_indices) is list and all(type(i) is int and i>=0 for i in admissible_row_indices)
            and len(admissible_row_indices)==len(set(admissible_row_indices))
            and admissible_row_indices==checked['admissible_row_indices'],
        'protected-wait selector changed or omitted the original Pareto admissible set')
    by={r['row_index']:r for r in row_scores}
    for row in row_scores:
        wait=row['sum_capped_wait_seconds'];probabilities=row['tier_probabilities']
        require(type(wait) in (int,float) and math.isfinite(wait) and wait>=0,'finite nonnegative row capped wait required')
        require(type(probabilities) is dict and set(probabilities)=={'4','2','1'},'complete original tier probability sums required')
        for member in row['member_metrics']:
            value=member['metrics']['capped_wait_seconds']
            require(type(value) in (int,float) and math.isfinite(value) and value>=0,'finite nonnegative owner capped wait required')
        for tier in (4,2,1):
            probability=probabilities[str(tier)]
            require(type(probability) in (int,float) and math.isfinite(probability) and probability>=0,
                'finite nonnegative aggregate tier probability required')
            total=sum((m['metrics']['selection_probability'] for m in row['member_metrics'] if m['effective_priority']==tier),0.0)
            require(probability==total,'tier probability sum differs from intact owner metrics')
        expected_wait=sum((m['metrics']['capped_wait_seconds'] for m in row['member_metrics']),0.0)
        require(wait==expected_wait,'row capped wait differs from intact owner metrics')
    require(baseline_index in admissible_row_indices,'Stoploss anchor is absent from protected candidates')
    minimum=min(by[i]['sum_capped_wait_seconds'] for i in admissible_row_indices)
    wait_survivors=[i for i in admissible_row_indices if by[i]['sum_capped_wait_seconds']<=minimum+WAIT_EPSILON]
    remaining=list(wait_survivors);filters=[]
    for tier in (4,2,1):
        maximum=max(by[i]['tier_probabilities'][str(tier)] for i in remaining)
        remaining=[i for i in remaining if by[i]['tier_probabilities'][str(tier)]>=maximum-PROBABILITY_EPSILON]
        filters.append(dict(tier=tier,maximum_probability=maximum,surviving_row_indices=list(remaining)))
    tied=baseline_index in remaining;selected=baseline_index if tied else remaining[0]
    baseline_wait=by[baseline_index]['sum_capped_wait_seconds'];selected_wait=by[selected]['sum_capped_wait_seconds']
    nonregression=selected_wait<=baseline_wait+WAIT_EPSILON
    require(nonregression,'wait-first choice worsens the admitted Stoploss baseline beyond existing tolerance')
    return dict(schema_version=1,rule=SELECT_RULE,source_sha256=deepcopy(SELECT_SOURCE_SHA256),
        probability_epsilon=PROBABILITY_EPSILON,wait_epsilon=WAIT_EPSILON,baseline_row_index=baseline_index,
        baseline_sum_capped_wait_seconds=baseline_wait,admissible_row_indices=list(admissible_row_indices),
        minimum_wait_seconds=minimum,wait_surviving_row_indices=wait_survivors,priority_filters=filters,
        final_surviving_row_indices=remaining,base_selected_tied=tied,selected_index=selected,
        selected_sum_capped_wait_seconds=selected_wait,selected_wait_within_baseline_tolerance=nonregression,
        additional_baseline_wait_cap_applied=False,scope=SELECT_SCOPE)


def audit_pair(members,context,tails,outputs,evidence,priority_context,owner_context):
    require(type(evidence) is dict and type(evidence.get('original_pareto_proposal')) is dict
        and set(evidence['original_pareto_proposal'])=={'decisions','evidence'},'missing intact Pareto proposal')
    original=evidence['original_pareto_proposal']
    checked=foundation.audit_pair([alias_member(m) for m in members],alias_context(context),[alias_member(m) for m in tails],
        original['decisions'],original['evidence'],priority_context,alias_owners(owner_context))
    priorities=audit_priority_context(priority_context,context,members,tails)
    old=original['evidence'];evaluated=old['evaluated'];base_index=old['selected_index'];selected=base_index
    anchor=old['stoploss_anchor_index'];full=old['original_fullmenu_proposal']['evidence'];selection=None
    require(anchor==full['base_selected_index']==full['original_stoploss_proposal']['evidence']['selected_index'],
        'protected wait anchor differs from original Stoploss actual row')
    counts=Counter({key:0 for key in ('waitpareto_rows_checked','waitpareto_admissible_rows_checked',
        'waitpareto_groups_evaluated','waitpareto_groups_unevaluated','waitpareto_selected_wait_nonregression_checked',
        'waitpareto_guard_unchanged_checked','waitpareto_stoploss_anchor_selected')})
    if evaluated:
        require(type(old['owner_probability_guard']) is dict,'missing intact Pareto guard')
        selection=select_protected_wait(full['row_scores'],admissible_row_indices=old['owner_probability_guard']['admissible_row_indices'],
            baseline_index=anchor,owner_inventory=owner_context['inventory'],menu_indices=full['menu_indices'],feasible_row_indices=full['feasible_row_indices'])
        selected=selection['selected_index']
        counts.update(waitpareto_rows_checked=len(full['row_scores']),
            waitpareto_admissible_rows_checked=len(selection['admissible_row_indices']),waitpareto_groups_evaluated=1,
            waitpareto_selected_wait_nonregression_checked=1,waitpareto_guard_unchanged_checked=1,
            waitpareto_stoploss_anchor_selected=int(selected==anchor))
    else:
        require(old['owner_probability_guard'] is None and full['menu_indices']==[] and full['row_scores']==[],
            'unevaluated Pareto invented protected-wait candidates')
        counts['waitpareto_groups_unevaluated']+=1
    intervened=selected!=base_index
    wanted=dict(schema_version=1,rule=RULE,base_policy=BASE_POLICY,base_source_sha256=BASE_SOURCE_SHA256,
        identity_projection=dict(source_policy=NAME,calculation_policy=BASE_POLICY,
            fields=['joint_context.eligible[].job.policy_name','members[].workflow_snapshot.job.policy_name',
                'tail_members[].workflow_snapshot.job.policy_name','owner_context.inventory[].job.policy_name'],
            copied_only=True,public_identity_unchanged=True,foreign_base_owner_label='__foreign_cohort_pareto__'),
        joint_context=context,priority_context=priority_context,owner_context=owner_context,original_pareto_proposal=original,
        evaluated=evaluated,protected_wait_selection=selection,base_selected_index=base_index,stoploss_anchor_index=anchor,
        selected_index=selected,intervened=intervened,tiers=[4,2,1],
        tolerances=dict(probability=1e-12,wait_seconds=1e-9),objective=OBJECTIVE,scope=SCOPE)
    precise_same(evidence,wanted,'complete independent protected wait-first choice')
    require(type(outputs) is list and len(outputs)==2,'actual protected-wait output pair differs')
    table=foundation.foundation.parent_views(full['original_stoploss_proposal'])['joint']
    chosen=next((r for r in full['row_scores'] if r['row_index']==selected),None)
    for index in range(2):
        actual=deepcopy(original['decisions'][index])
        if intervened:
            hospitals=table['combinations'][selected]['hospitals'][index]
            actual=deepcopy(table['baseline_proposals'][index]);actual['send']=list(hospitals);actual['reason']='cohort_waitpareto_current_only'
            ev=actual['evidence'];ev['sent_hospitals']=list(hospitals);ev['planned_expected_cost']=round(chosen['member_metrics'][index]['metrics']['total_cost_seconds'],3)
            for row in ev['candidate_plan']:row['planned_offset_seconds']=0 if row['hospital'] in hospitals else None
        actual['evidence']['policy']=NAME
        actual['evidence']['cohort_waitpareto']=dict(schema_version=1,mode='pair_cohort_waitpareto',evaluated=evaluated,
            intervened=intervened,member_index=index,base_selected_index=base_index,stoploss_anchor_index=anchor,
            selected_index=selected,effective_priority=priorities[_pid(members[index]['patient_id'])])
        precise_same(outputs[index],actual,'independently reconstructed protected-wait current output')
    checked=dict(checked,original_Pareto_groups_applied=checked['joint_groups_applied'],
        joint_groups_applied=int(selected!=table['baseline_index']))
    return dict(checked,**dict(counts),waitpareto_interventions=int(intervened))


_same=base._same
_audit_phase_order=base._audit_phase_order
time_context_from_prefix=audit_time_context


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
        checks.update(audit_pair(group['members'], wanted, group['tail_members'], group['outputs'], group['evidence'], group['priority_context'], group['owner_context']))
        count, history_proof = audit_owner_provenance(scenario, events, group['registry_snapshot'], group['owner_context'], group['owner_source_snapshot'])
        checks['owner_public_provenance_checked'] += count
        checks['owner_full_source_profiles_checked'] += count
        checks['historical_source_profiles_checked'] += history_proof['historical_owners_verified']
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
        exact_copy(output['send'], decision['hospital_ids'], 'committed sends')
        exact_copy(output['reason'], decision['reason'], 'committed reason')
        exact_copy(output['evidence'], decision['evidence'], 'committed evidence')
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
            exact_copy(output, group['outputs'][index], 'linked group output')
            dprefix = group['decision_prefix_max']; group_calls[gid, index] += 1
        else:
            member = dict(patient_id=pid, inputs=inputs, workflow_snapshot=call['workflow_snapshot'])
            dprefix = previous
            require(output['evidence'].get('joint_tail') == dict(schema_version=1, mode='horizon_fallback', guard_evaluated=False)
                and output['evidence'].get('joint_first') == dict(schema_version=1, mode='horizon_fallback', current_only=False)
                and output['evidence'].get('joint_budget') == dict(schema_version=1, mode='horizon_fallback', projection_evaluated=False)
                and output['evidence'].get('cohort_success') == dict(schema_version=1, mode='horizon_fallback', evaluated=False, intervened=False)
                and output['evidence'].get('cohort_dynamic') == dict(schema_version=1, mode='horizon_fallback', evaluated=False, intervened=False)
                and output['evidence'].get('cohort_hedge') == dict(schema_version=1, mode='horizon_fallback', evaluated=False, intervened=False),
                'ungrouped decision changed the original H fallback')
            require(output['evidence'].get('cohort_recourse') == dict(schema_version=1, mode='horizon_fallback', evaluated=False, intervened=False), 'late fallback changed recourse scope')
            require(output['evidence'].get('cohort_followplan') == dict(schema_version=1, mode='horizon_fallback', evaluated=False, intervened=False), 'late fallback changed followplan scope')
            require(output['evidence'].get('cohort_waitpareto') == dict(schema_version=1, mode='horizon_fallback', evaluated=False, intervened=False), 'late fallback changed stoploss scope')
            require(output['evidence'].get('cohort_pareto') == dict(schema_version=1, mode='horizon_fallback', evaluated=False, intervened=False), 'late fallback changed parent Pareto scope')
            require(output['evidence'].get('cohort_fullmenu') == dict(schema_version=1, mode='horizon_fallback', evaluated=False, intervened=False), 'late fallback changed original Fullmenu scope')
            require(output['evidence'].get('cohort_stoploss') == dict(schema_version=1, mode='horizon_fallback', evaluated=False, intervened=False), 'late fallback changed parent Stoploss scope')
            normalized = deepcopy(output); normalized['evidence'].pop('cohort_waitpareto'); normalized['evidence'].pop('cohort_pareto'); normalized['evidence'].pop('cohort_fullmenu'); normalized['evidence'].pop('cohort_stoploss'); normalized['evidence'].pop('cohort_followplan'); normalized['evidence'].pop('cohort_recourse')
            normalized['evidence'].pop('joint_first')
            normalized['evidence'].pop('joint_tail')
            normalized['evidence'].pop('joint_budget')
            normalized['evidence'].pop('cohort_success')
            normalized['evidence'].pop('cohort_dynamic')
            normalized['evidence'].pop('cohort_hedge')
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


def audit_branch(scenario,branch):
    if branch.get('policy')!=NAME:
        view=dict(schema_version=1,scenario=scenario,arms=[[branch['policy'],branch['rule']]],branches=[branch],
            environment_sha256=branch['environment_sha256'],initial_snapshot_sha256=branch['snapshot_sha256'])
        return control_audit.audit_replay(view)['branch_audits'][0]
    try:
        environment=hashlib.sha256(json.dumps(scenario,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        require(branch['environment_sha256']==environment and branch['rule']=='immediate','bound public environment or confirmation rule differs')
        exact_copy(branch['instrumentation'],NEW_INSTRUMENTATION,'new adapter source contract')
        checks=_audit_calls(scenario,branch);accounting=horizon._audit_trace_fixed(scenario,branch)
        total_j=sum(1800 if p['failed'] else p['estimated_arrival_seconds'] for p in branch['patients'])
        return dict(passed=True,**checks,**accounting,total_arrival_failure_cost_seconds=total_j,
            mean_arrival_failure_cost_seconds=total_j/len(branch['patients']) if branch['patients'] else None,
            original_Hedge_proposal_independently_checked=True,original_D_CS_continuation_intact=True,
            original_Recourse_proposal_independently_checked=True,
            original_Followplan_proposal_independently_checked=True,
            original_Stoploss_proposal_independently_checked=True,
            original_Fullmenu_proposal_independently_checked=True,
            original_Pareto_proposal_independently_checked=True,
            protected_wait_first_selection_independently_checked=True,
            original_Pareto_admissible_set_exactly_preserved=True,
            selected_predicted_wait_within_Stoploss_baseline_tolerance=True,
            additional_baseline_wait_cap_applied=False,
            every_owner_final_predicted_probability_guard_independently_checked=True,
            Stoploss_actual_row_baseline_and_tie_anchor_independently_checked=True,
            guard_adds_no_scoring_or_outcome_inputs=True,
            realized_success_identity_preservation_guaranteed=False,
            original_activation_gate_independently_preserved=True,
            every_original_feasible_row_in_activated_groups_independently_checked=True,
            compact_added_row_D_references_and_full_scoring_pipeline_independently_rebuilt=True,
            fixed_F0_one_pass_expected_work_independently_rebuilt=True,
            first_patient_static_next_review_continuation_independently_rebuilt=True,
            frozen_first_future_plans_shared_work_independently_rebuilt=True,
            fixed_F0_future_survival_and_corrected_contact_cost_independently_checked=True,
            chronological_mean_queue_completion_independently_checked=True,
            fixed_F0_uses_mean_preserving_current_head_stoploss=True,
            current_denominator_fixed_without_future_silence_conditioning=True,
            shifted_thinned_mean_identity_and_Jensen_bounds_independently_checked=True,
            exact_baseline_only_under_declared_shifted_thinned_head_law=True,
            log_arithmetic_sensitivity_allowance_independently_checked=True,
            numerical_allowance_scope='engineering 32-ULP operand envelopes propagated through log-stoploss sensitivity; not a certified transcendental interval',
            exact_stochastic_residual_expectation_claimed=False,
            hypothetical_descriptors_separate_from_committed_source=True,
            actual_pending_and_own_CDF_unchanged=True,integer_occupancy_and_contact_budget_independently_checked=True,
            actual_outputs_sends_and_all_patient_FIFO_checked=True,physical_transaction_boundary_from_raw_proved=False,
            atomicity_evidence_scope='complete actual source captures and prefix/version checks; physical atomicity needs frozen adapter and rollback tests',
            scope='intact independently audited Pareto proposal with unchanged Fullmenu scores, complete owner guard, coverage and activation; unchanged admitted rows minimized by predicted capped wait first within1e-9, then tier4/2/1 probability bands within1e-12, Stoploss anchor and source order; baseline predicted-wait bound follows without a new cap; original provenance, actual caps, late H and FIFO retained; no realized-success or wait guarantee')
    except (KeyError,TypeError,IndexError,AttributeError,OverflowError,StopIteration) as error:
        raise base.CohortHedgeAuditError('malformed saved cohort-waitpareto artifact') from error


def audit_replay(replay):
    require(type(replay) is dict and replay['schema_version']==1 and type(replay['branches']) is list,'invalid complete replay')
    require(replay['arms']==[[b['policy'],b['rule']] for b in replay['branches']]
        and len({tuple(a) for a in replay['arms']})==len(replay['arms'])>0,'complete arm inventory differs')
    require(all(b['environment_sha256']==replay['environment_sha256']
        and b['snapshot_sha256']==replay['initial_snapshot_sha256'] for b in replay['branches']), 'paired initial environment differs')
    if not any(b.get('policy')==NAME for b in replay['branches']):return control_audit.audit_replay(replay)
    controls=[b for b in replay['branches'] if b.get('policy')!=NAME];control_proof=None;by_policy={}
    if controls:
        view=dict(replay,branches=controls,arms=[[b['policy'],b['rule']] for b in controls])
        control_proof=control_audit.audit_replay(view)
        by_policy={(b['policy'],b['rule']):p for b,p in zip(controls,control_proof['branch_audits'])}
    checked=[audit_branch(replay['scenario'],b) if b['policy']==NAME else by_policy[b['policy'],b['rule']] for b in replay['branches']]
    return dict(passed=True,branches=len(checked),branch_audits=checked,new_executions=0,
        preserved_parent_audit_proof=control_proof)

