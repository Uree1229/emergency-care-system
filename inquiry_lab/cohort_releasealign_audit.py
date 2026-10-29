"""Independent accepted-Tailwait public own-slot review-timing audit.

Only the next review clock may advance; original sends, plan and metrics stay
exact. Tailwait joint groups and public-source/owner/FIFO checks are retained.
Only pinned independent auditors and public data are used; no producer import.
"""
from collections import Counter
from collections.abc import Mapping
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
from . import cohort_tailwait_audit as foundation
control_audit=foundation

ROOT=Path(__file__).resolve().parents[1]
if hashlib.sha256(Path(foundation.__file__).read_bytes()).hexdigest()!='013522bed48b392e1d3d583542994972cc0c13893978c9c04fda8fe1fb3bc0bb':
    raise ValueError('frozen independent Tailwait auditor differs')
base=foundation.base
parent,horizon,joint=foundation.parent,foundation.horizon,foundation.joint
numeric=foundation.numeric
dynamic=foundation.dynamic
require,precise_same,exact_copy=foundation.require,foundation.precise_same,foundation.exact_copy
_pid,_integer,EPOCH=foundation._pid,foundation._integer,foundation.EPOCH
INPUT_KEYS,CALL_KEYS,GROUP_KEYS=foundation.INPUT_KEYS,foundation.CALL_KEYS,foundation.GROUP_KEYS
NAME='eat_cohort_releasealign_l15_f'
BASE_POLICY=foundation.NAME
BASE_SOURCE_SHA256='2fd6914a26c05651a79569395b30c8281881d5a72edbb11b08b85f33caa3a4a5'
if hashlib.sha256((ROOT/'inquiry_lab/cohort_tailwait_policy.py').read_bytes()).hexdigest()!=BASE_SOURCE_SHA256:
    raise ValueError('frozen Tailwait producer source differs')
NEW_INSTRUMENTATION=dict(
    original_runner='inquiry_lab.runner._branch',
    actual_workflow='inquiry_lab.cohort_releasealign_runner.run_once',
    record_only=False, epoch=EPOCH, public_time_context_added=True,
    current_registered_first_contact_context_added=True, public_tail_first_contact_context_added=True,
    public_patient_priority_context_added=True, row_specific_reference_continuation_menu=True,
    public_complete_waiting_owner_context_added=True, origin_clock_owner_hedge_surrogate=True, group_atomic_commit=True,
    first_contact_virtual_queued_work_cancellation_model=True,
    first_contact_static_H_continuation_added=True,
    first_future_interowner_workahead=True,
    first_future_queue_elapsed_service_advance=True,
    first_future_mean_preserving_stoploss_baseline=True,
    exhaustive_feasible_current_rows=True,
    per_owner_predicted_probability_nonregression=True,
    protected_wait_first_selection=True,
    expected_future_queue_envelope=True,
    no_tail_first_pair_evaluation=True,
    hard_zero_first_wave_membership_guard=True,
    tail_cohort_owner_wait_nonregression=True,
    existing_pending_and_integer_concurrency_unchanged=True,
    maximum_group_size=2, eligibility_order='started_at,patient_id',
    base_horizon_preserved_for_fallback=False,
    known_own_contact_slot_release_review_alignment=True,
    grouped_initial_Tailwait_calculation_unchanged=True,
    first_unpaired_H_fallback_unchanged=True,
    fallback_H_send_and_plan_unchanged=True,
    timing_scope='contacted ungrouped fallback, full fresh own count4, empty original send and remaining candidates',
    parent_guard_flags_scope='unchanged Tailwait joint calculations; follow-up H sends/plans copied, only next-review time may align with known slot release')

def _project_name(name):
    if name==NAME:return BASE_POLICY
    return '__foreign_cohort_tailwait__' if name==BASE_POLICY else name


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





EPSILON=1e-9
PROBABILITY_EPSILON=1e-12
HORIZON_SOURCE_SHA256='5061be48175098ce6a30be2af0f0721852e2d67d8bd75ab475a202bcee3ce387'
require(hashlib.sha256((ROOT/'inquiry_lab/horizon_policy.py').read_bytes()).hexdigest()==HORIZON_SOURCE_SHA256,'frozen original H source differs')
def _nonnegative(value):
    return type(value) in (int,float) and math.isfinite(value) and value>=0

def normalize_pending(rows,now,*,own=False):
    result={}
    for row in rows:
        if not isinstance(row,Mapping):continue
        submitted=row.get('submitted_at')
        if not _nonnegative(submitted) or submitted>now:continue
        hospital,patient=row.get('hospital'),row.get('patient')
        if not isinstance(hospital,str) or not hospital or isinstance(patient,bool) or not isinstance(patient,(str,int)):continue
        record=dict(hospital=hospital,patient=patient,submitted_at=submitted)
        if own:
            travel=row.get('travel_seconds');require(_nonnegative(travel),'valid own pending travel required')
            record['travel_seconds']=travel
        result[hospital,patient,submitted]=record
    return list(result.values())

def normalized_inputs(inputs):
    value=deepcopy(inputs);now=value['now'];_integer(now,'public current clock')
    value['pending']=normalize_pending(value['pending'],now)
    value['patient_pending']=normalize_pending(value['patient_pending'],now,own=True)
    visible=[]
    for row in value['observations']:
        if not isinstance(row,Mapping):continue
        submitted,observed=row.get('submitted_at'),row.get('observed_at')
        if not _nonnegative(submitted) or not _nonnegative(observed) or submitted>now or observed<submitted or not now-1800<observed<=now:continue
        hospital,outcome=row.get('hospital'),row.get('outcome')
        if not isinstance(hospital,str) or not hospital or outcome not in ('reply','accept','decline','timeout'):continue
        visible.append(dict(hospital=hospital,submitted_at=submitted,observed_at=observed,outcome=outcome))
    value['observations']=visible
    last=value['last_contact_at']
    if last is not None and (type(last) not in (int,float) or not math.isfinite(last) or last>now):last=None
    value['last_contact_at']=last
    return value

def _H_view(proposal):
    require(type(proposal) is dict and set(proposal)=={'send','reason','evidence','next_review_at'},
        'intact original Tailwait fallback proposal required')
    value=deepcopy(proposal);ev=value['evidence'];require(ev['policy']==BASE_POLICY,'original fallback policy identity differs')
    special=dict(joint_tail=dict(schema_version=1,mode='horizon_fallback',guard_evaluated=False),
        joint_first=dict(schema_version=1,mode='horizon_fallback',current_only=False),
        joint_budget=dict(schema_version=1,mode='horizon_fallback',projection_evaluated=False))
    for key in ('cohort_success','cohort_dynamic','cohort_hedge','cohort_recourse','cohort_followplan',
            'cohort_stoploss','cohort_fullmenu','cohort_pareto','cohort_waitpareto','cohort_envelope',
            'cohort_pairscope','cohort_pairshield','cohort_tailwait'):
        special[key]=dict(schema_version=1,mode='horizon_fallback',evaluated=False,intervened=False)
    for key,wanted in special.items():exact_copy(ev.pop(key),wanted,'original inactive fallback marker '+key)
    ev['policy']=horizon.NAME
    return value


def original_H_search(inputs):
    clean=normalized_inputs(inputs);model=horizon.model_from_inputs(clean)
    pool=[r['hospital'] for r in model['pool']];options=[None]+list(model['offsets'])
    plan={h:None for h in pool};cost=horizon.plan_cost(model,plan);passes=0;forced=False
    for _ in range(2):
        passes+=1;improved=False
        for h in pool:
            for offset in options:
                if offset==plan[h]:continue
                if offset==0 and plan[h]!=0 and sum(t==0 for t in plan.values())>=model['room']:continue
                trial=dict(plan);trial[h]=offset;value=horizon.plan_cost(model,trial)
                if value<cost-EPSILON:plan,cost,improved=trial,value,True
        if not improved:break
    send=[h for h in pool if plan[h]==0]
    window=min(clean['time_context']['auto_inquiry_deadline_at'],clean['time_context']['evaluation_end_at'])-clean['now']
    if window>0 and model['fresh']==0 and not send and pool and model['room']>0:
        by={r['hospital']:r for r in model['pool']}
        h=min(pool,key=lambda h:(horizon.plan_cost(model,dict(plan,**{h:0})),by[h]['travel_seconds']))
        plan=dict(plan,**{h:0});cost=horizon.plan_cost(model,plan);send=[h];forced=True
    return dict(offsets=plan,send=send,cost=cost,passes=passes,forced=forced)


def audit_original_H(inputs,parent_proposal):
    value=_H_view(parent_proposal);clean=normalized_inputs(inputs)
    checked=horizon.audit_horizon(clean,value);expected=original_H_search(clean)
    exact_copy({r['hospital']:r['planned_offset_seconds'] for r in value['evidence']['candidate_plan']},
        expected['offsets'],'independent original H final plan')
    exact_copy(value['send'],expected['send'],'independent original H immediate query order')
    require(value['evidence']['planned_expected_cost']==round(expected['cost'],3),'independent original H objective differs')
    return expected,checked


RULE='align_full_own_contact_slot_review_to_first_public_inclusive_timeout_release'
SCOPE='only contacted ungrouped fallback with exactly four normalized fresh own inquiries and empty original H send; advance only the next review to the first known integer age>180 before A/H; original send, plan, metrics, pending CDF and first Tailwait groups preserved; real replies may arrive earlier; no outcome guarantee'


def audit_fallback(inputs,output):
    tag=output['evidence']['cohort_releasealign'];original=tag['original_tailwait_proposal']
    _,checked=audit_original_H(inputs,original);clean=normalized_inputs(inputs);now=clean['now']
    own=clean['patient_pending'];fresh=[q for q in own if 0<=now-q['submitted_at']<=180]
    contacted=clean['last_contact_at'] is not None
    evaluated=contacted and len(fresh)==4 and original['send']==[] and bool(clean['candidates']) and clean['max_new']>0
    release=min((math.floor(q['submitted_at']+180)+1 for q in fresh),default=None)
    end=min(clean['time_context']['auto_inquiry_deadline_at'],clean['time_context']['evaluation_end_at'])
    selected=original['next_review_at']
    if evaluated and now<release<selected and release<end:selected=release
    changed=selected!=original['next_review_at'];value=deepcopy(original);value['next_review_at']=selected
    value['evidence']['policy']=NAME
    value['evidence']['cohort_releasealign']=dict(schema_version=1,mode='horizon_fallback',rule=RULE,
        base_source_sha256=BASE_SOURCE_SHA256,evaluated=evaluated,intervened=changed,contacted_owner=contacted,
        normalized_own_pending_count=len(own),fresh_own_pending_count=len(fresh),maximum_concurrent=4,
        observation_timeout_seconds=180,earliest_slot_release_at=release,original_next_review_at=original['next_review_at'],
        selected_next_review_at=selected,latest_allowed_new_inquiry_at=end,original_tailwait_proposal=original,scope=SCOPE)
    precise_same(output,value,'complete release-aligned review-only fallback')
    return dict(checked,releasealign_fallback_calls_checked=1,releasealign_original_H_searches_checked=1,
        releasealign_scope_eligible_checked=int(evaluated),releasealign_advanced_reviews_checked=int(changed),
        releasealign_copied_reviews_checked=int(not changed),releasealign_fresh_own_records_checked=len(fresh),
        releasealign_send_changes_checked=0,releasealign_plan_changes_checked=0)


def audit_pair(members,context,tails,outputs,evidence,priority_context,owner_context):
    original=evidence['original_tailwait_proposal']
    require(type(original) is dict and set(original)=={'decisions','evidence'},'intact copied Tailwait group required')
    checked=foundation.audit_pair([alias_member(m) for m in members],alias_context(context),[alias_member(m) for m in tails],
        original['decisions'],original['evidence'],priority_context,alias_owners(owner_context))
    wanted=deepcopy(original['decisions'])
    for i,d in enumerate(wanted):
        d['evidence']['policy']=NAME;d['evidence']['cohort_releasealign']=dict(schema_version=1,mode='pair_copied_tailwait',
            evaluated=False,intervened=False,member_index=i,next_review_changed=False)
    precise_same(outputs,wanted,'exact unchanged Tailwait group actions')
    expected=dict(schema_version=1,rule=RULE,base_policy=BASE_POLICY,base_source_sha256=BASE_SOURCE_SHA256,
        joint_context=context,priority_context=priority_context,owner_context=owner_context,original_tailwait_proposal=original,
        evaluated=False,copied_tailwait_group=True,identity_projection=dict(source_policy=NAME,calculation_policy=BASE_POLICY,
            copied_only=True,fields=['joint_context.eligible[].job.policy_name','members[].workflow_snapshot.job.policy_name',
                'tail_members[].workflow_snapshot.job.policy_name','owner_context.inventory[].job.policy_name'],
            public_identity_unchanged=True,foreign_base_owner_label='__foreign_cohort_tailwait__'),scope=SCOPE)
    precise_same(evidence,expected,'complete copied Tailwait group evidence')
    return dict(checked,releasealign_groups_copied_checked=1,releasealign_group_members_copied_checked=2)


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
            checks.update(audit_fallback(inputs,output))
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
        return dict(passed=True,**checks,**accounting,strict_integer_inclusive180_release_bound_independently_checked=True,review_only_change_independently_checked=True,original_sends_plan_metrics_and_pending_CDF_unchanged=True,other_owner_nonregression_guaranteed=False,original_H_baseline_independently_reconstructed=True,accepted_Tailwait_joint_groups_copied=True,realized_success_wait_arrival_guaranteed=False,total_arrival_failure_cost_seconds=total_j,
            mean_arrival_failure_cost_seconds=total_j/len(branch['patients']) if branch['patients'] else None,
            original_Hedge_proposal_independently_checked=True,original_D_CS_continuation_intact=True,
            original_Recourse_proposal_independently_checked=True,
            original_Followplan_proposal_independently_checked=True,
            original_Stoploss_proposal_independently_checked=True,
            original_Fullmenu_proposal_independently_checked=True,
            original_Pareto_proposal_independently_checked=True,
            original_Waitpareto_proposal_independently_checked=True,
            original_Envelope_proposal_independently_checked=True,
            original_Pairshield_proposal_independently_checked=True,
            inherited_active_tail_owner_wait_guard_independently_checked=True,
            every_scored_owner_wait_compared_with_Stoploss_anchor=True,
            original_Pairscope_proposal_independently_checked=True,
            first_contact_zero_mass_membership_guard_independently_checked=True,
            declared_queue_horizon_boundary_not_physical_zero=True,
            all_current_pair_membership_changes_checked=True,
            shared_hospital_boundary_changes_not_vetoed=True,
            full_parent_probability_admission_validated_before_projection=True,
            support_guard_candidate_subdomain_explicit=True,
            original_JOINT_feasibility_not_rewritten=True,
            new_activation_exactly_no_tail_first_pair=True,
            all_residual_owners_frozen_at_same_public_prefix=True,
            inherited_active_groups_unchanged=True,
            one_tail_inactive_groups_unchanged=True,
            expected_future_queue_envelope_independently_checked=True,
            envelope_exact_only_for_declared_random_C0_deterministic_T=True,
            all_feasible_rows_and_anchor_uniformly_rescored=True,
            fixed_F0_not_recomputed_from_new_waits=True,
            physical_C0_prior_dependency_modeled=False,
            global_trajectory_queue_nonregression_claimed=False,
            local_envelope_no_numeric_clamp=True,
            protected_wait_first_selection_independently_checked=True,
            per_owner_probability_guard_reapplied_to_rescored_anchor=True,
            selected_predicted_wait_within_Stoploss_baseline_tolerance=True,
            additional_baseline_wait_cap_applied=False,
            every_owner_final_predicted_probability_guard_independently_checked=True,
            Stoploss_actual_row_baseline_and_tie_anchor_independently_checked=True,
            score_repair_adds_no_outcome_inputs=True,
            realized_success_identity_preservation_guaranteed=False,
            original_active_groups_preserved=True,
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
            scope=SCOPE,parent_guard_flags_scope=NEW_INSTRUMENTATION['parent_guard_flags_scope'])
    except (KeyError,TypeError,IndexError,AttributeError,OverflowError,StopIteration) as error:
        raise base.CohortHedgeAuditError('malformed saved cohort-tailwait artifact') from error


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

