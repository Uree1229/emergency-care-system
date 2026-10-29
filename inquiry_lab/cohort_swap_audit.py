"""Independent accepted-Tailwait current-owner atomic offset-swap audit.

The full original H plan and every bounded exchange metric are reconstructed.
Tailwait joint groups and all public-source/owner/FIFO checks stay unchanged.
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
NAME='eat_cohort_swap_l15_f'
BASE_POLICY=foundation.NAME
BASE_SOURCE_SHA256='2fd6914a26c05651a79569395b30c8281881d5a72edbb11b08b85f33caa3a4a5'
if hashlib.sha256((ROOT/'inquiry_lab/cohort_tailwait_policy.py').read_bytes()).hexdigest()!=BASE_SOURCE_SHA256:
    raise ValueError('frozen Tailwait producer source differs')
NEW_INSTRUMENTATION=dict(
    original_runner='inquiry_lab.runner._branch',
    actual_workflow='inquiry_lab.cohort_swap_runner.run_once',
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
    single_atomic_fallback_offset_exchange=True,
    initial_Tailwait_joint_and_singleton_unchanged=True,
    fallback_swap_scope='previously contacted owner with normalized eligible own pending',
    parent_guard_flags_scope='unchanged Tailwait joint groups only; fallback swaps use current-owner P/W/J/Q guards',
    planned_and_immediate_counts_and_offset_histogram_preserved=True,
    original_H_future_cap_relaxation_unchanged=True,
    actual_immediate_concurrency_limit_unchanged=True)

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


MODEL_RULE='single_atomic_immediate_to_nonimmediate_offset_exchange_under_owner_P_W_J_Q_Pareto_guards'
MODEL_SCOPE='same public origin and frozen H distributions; only one current-owner exchange; original offset histogram, planned count and immediate count preserved; no future-cap correction, other-owner nonregression, actual-success or waiting-time guarantee'


def plan_summary(plan):
    offsets=sorted({t for t in plan.values() if t is not None})
    return dict(offset_histogram=[dict(offset_seconds=None,count=sum(t is None for t in plan.values()))]
        +[dict(offset_seconds=t,count=sum(v==t for v in plan.values())) for t in offsets],
        planned_count=sum(t is not None for t in plan.values()),immediate_count=sum(t==0 for t in plan.values()),
        planned_hospitals=[h for h,t in plan.items() if t is not None])


def public_swap_model(inputs,proposal):
    clean=normalized_inputs(inputs);m=horizon.model_from_inputs(clean);now=clean['now']
    beliefs=parent._beliefs(clean['observations'],now);queue={};replies={};own=[]
    for row in clean['pending']:queue.setdefault(row['hospital'],[]).append(row)
    for row in clean['observations']:
        if row['outcome']!='timeout':replies.setdefault(row['hospital'],[]).append(row)
    for source,(_,mass) in zip(clean['patient_pending'],m['current']):
        h,submitted=source['hospital'],source['submitted_at'];r,a,mu,sigma=beliefs(h)
        ahead=sum(1. if q['submitted_at']<submitted else .5 for q in queue.get(h,[])
            if q['submitted_at']<=submitted and q['patient']!=source['patient'])
        if ahead>0:
            mode='queued';wait=ahead*math.exp(mu+.5*sigma*sigma);spent=f0=0.;total=r*a
        else:
            mode='residual';wait=0.;freed=[q['observed_at'] for q in replies.get(h,[]) if q['submitted_at']<=submitted]
            spent=max(0.,now-max([submitted,*freed]));f0=parent._cdf(spent,mu,sigma)
            alive=max(1e-9,1-f0);total=r*alive/max(1e-9,1-r*f0)*a
        own.append(dict(hospital=h,travel_seconds=source['travel_seconds'],mode=mode,wait_seconds=wait,
            spent_seconds=spent,f0=f0,total_probability=total,service_mu=mu,service_sigma=sigma,masses=mass))
    candidates=[]
    for row,record in zip(m['pool'],m['residual_rows']):
        h=row['hospital'];r,a,mu,sigma=beliefs(h)
        if record['age_seconds'] is not None:record=dict(record,age_seconds=max(0.,now-record['estimated_start_at']))
        candidates.append(dict(hospital=h,travel_seconds=row['travel_seconds'],residual_wait_seconds=record['estimated_wait_seconds'],
            service_mu=mu,service_sigma=sigma,total_probability=r*a,inquiry_cost_seconds=15,residual_queue_record=record))
    plan={row['hospital']:row['planned_offset_seconds'] for row in proposal['evidence']['candidate_plan']}
    require(list(plan)==[r['hospital'] for r in candidates] and all(t is None or type(t) is int and t in m['offsets'] for t in plan.values()),
        'complete original H pool/order/offsets required')
    require(proposal['send']==[h for h,t in plan.items() if t==0],'original immediate sends differ from plan')
    return dict(schema_version=1,horizon_source_sha256=HORIZON_SOURCE_SHA256,now=now,
        cell_edges_seconds=[right for _,right in m['bins']],grid_seconds=5,failure_penalty_seconds=1500,
        normalized_own_pending=clean['patient_pending'],own_items=own,candidates=candidates,original_plan=plan,
        original_plan_summary=plan_summary(plan),scope=MODEL_SCOPE)


def swap_metrics(model,plan):
    require(list(plan)==[r['hospital'] for r in model['candidates']],'complete scored pool/order required')
    edges=model['cell_edges_seconds'];bins=list(zip([0,*edges[:-1]],edges));items=[(r['travel_seconds'],r['masses']) for r in model['own_items']];selected=[]
    for row in model['candidates']:
        offset=plan[row['hospital']]
        if offset is None:continue
        cdf=lambda x,row=row,offset=offset:parent._cdf(x-offset-row['residual_wait_seconds'],row['service_mu'],row['service_sigma'])
        items.append((row['travel_seconds'],horizon.masses(cdf,row['total_probability'],bins)))
        selected.append((offset,row['inquiry_cost_seconds']))
    failure=math.prod(max(0.,1-sum(mass)) for _,mass in items)
    wait=joint._cost([(0,mass) for _,mass in items],bins,edges[-1],0)
    arrival=horizon.expected_cost(items,bins,edges[-1]);contact=0.
    for offset,lam in selected:contact+=lam*math.prod(max(0.,1-sum(mass[:offset//5])) for _,mass in items) if offset else lam
    return dict(selection_probability=1-failure,failure_probability=failure,capped_wait_seconds=wait,
        arrival_failure_cost_seconds=arrival,planned_contact_cost_seconds=contact,total_cost_seconds=arrival+contact)


def choose_trials(trials):
    admitted=[r for r in trials if r['admitted']]
    if not admitted:return dict(admissible_trial_indices=[],wait_band_trial_indices=[],cost_band_trial_indices=[],selected_trial_index=None,baseline_fallback=True)
    best=min(r['metrics']['capped_wait_seconds'] for r in admitted)
    waiting=[r for r in admitted if r['metrics']['capped_wait_seconds']<=best+EPSILON]
    bestcost=min(r['metrics']['total_cost_seconds'] for r in waiting)
    final=[r for r in waiting if r['metrics']['total_cost_seconds']<=bestcost+EPSILON]
    return dict(admissible_trial_indices=[r['trial_index'] for r in admitted],wait_band_trial_indices=[r['trial_index'] for r in waiting],
        cost_band_trial_indices=[r['trial_index'] for r in final],selected_trial_index=final[0]['trial_index'],baseline_fallback=False)


def evaluate_swaps(inputs,proposal):
    model=public_swap_model(inputs,proposal);baseline=model['original_plan'];before=swap_metrics(model,baseline);trials=[]
    for i,outgoing in enumerate(model['candidates']):
        u=outgoing['hospital']
        if baseline[u]!=0:continue
        for j,incoming in enumerate(model['candidates']):
            v=incoming['hospital']
            if baseline[v]==0:continue
            plan=deepcopy(baseline);plan[u],plan[v]=plan[v],plan[u];summary=plan_summary(plan)
            require(all(summary[k]==model['original_plan_summary'][k] for k in ('offset_histogram','planned_count','immediate_count')),
                'atomic swap changed histogram/contact cardinalities')
            value=swap_metrics(model,plan)
            checks=dict(selection_probability_nonregression=value['selection_probability']>=before['selection_probability']-PROBABILITY_EPSILON,
                capped_wait_strict_improvement=value['capped_wait_seconds']<before['capped_wait_seconds']-EPSILON,
                arrival_cost_nonregression=value['arrival_failure_cost_seconds']<=before['arrival_failure_cost_seconds']+EPSILON,
                planned_contact_cost_nonregression=value['planned_contact_cost_seconds']<=before['planned_contact_cost_seconds']+EPSILON)
            trials.append(dict(trial_index=len(trials),outgoing_pool_index=i,incoming_pool_index=j,outgoing_hospital=u,incoming_hospital=v,
                original_incoming_offset_seconds=baseline[v],plan=plan,plan_summary=summary,
                planned_hospital_set_preserved=set(summary['planned_hospitals'])==set(model['original_plan_summary']['planned_hospitals']),
                metrics=value,checks=checks,admitted=all(checks.values()),rejection_reasons=[k for k,v in checks.items() if not v]))
    require(len(trials)<=9,'bounded fixed-pool one-exchange inventory exceeded9')
    selection=choose_trials(trials);index=selection['selected_trial_index']
    return dict(schema_version=1,rule=MODEL_RULE,scope=MODEL_SCOPE,model=model,baseline_metrics=before,trials=trials,selection=selection,
        selected_plan=deepcopy(baseline if index is None else trials[index]['plan']),selected_metrics=deepcopy(before if index is None else trials[index]['metrics']),
        tolerances=dict(probability=PROBABILITY_EPSILON,seconds=EPSILON),one_exchange_only=True,original_future_cap_relaxation_unchanged=True)


MODEL_SOURCE_SHA256='471c57d532ca4c635f3bf5555b12295a39ffbd098f27023b5b5bb891ac4429bb'
SOURCE_SHA256=dict(tailwait_policy=BASE_SOURCE_SHA256,horizon_policy=HORIZON_SOURCE_SHA256,swap_model=MODEL_SOURCE_SHA256)
RULE='contacted_pending_owner_single_atomic_H_offset_exchange'
SCOPE='Tailwait first-contact joint and singleton paths copied; only contacted own-pending fallback evaluated; current-owner P/W/J/Q forecast guards, not other-owner or actual-outcome guarantees; no future-cap correction or new observations'
require(hashlib.sha256((ROOT/'inquiry_lab/cohort_swap_model.py').read_bytes()).hexdigest()==MODEL_SOURCE_SHA256,'current swap model source differs')


def audit_fallback(inputs,output):
    tag=output['evidence']['cohort_swap'];original=tag['original_tailwait_proposal']
    _,checked=audit_original_H(inputs,original);clean=normalized_inputs(inputs)
    contacted=clean['last_contact_at'] is not None;evaluated=contacted and bool(clean['patient_pending'])
    projection=evaluate_swaps(inputs,original) if evaluated else None
    selected=None if projection is None else projection['selection']['selected_trial_index']
    value=deepcopy(original)
    if selected is not None:
        plan=projection['selected_plan'];value['send']=[h for h,t in plan.items() if t==0]
        value['evidence']['sent_hospitals']=list(value['send'])
        for row in value['evidence']['candidate_plan']:row['planned_offset_seconds']=plan[row['hospital']]
        value['evidence']['planned_expected_cost']=round(projection['selected_metrics']['total_cost_seconds'],3)
    value['evidence']['policy']=NAME
    value['evidence']['cohort_swap']=dict(schema_version=1,mode='horizon_fallback',rule=RULE,base_source_sha256=BASE_SOURCE_SHA256,
        model_horizon_source_sha256=HORIZON_SOURCE_SHA256,source_sha256=deepcopy(SOURCE_SHA256),evaluated=evaluated,
        normalized_own_pending_count=len(clean['patient_pending']),contacted_owner=contacted,original_tailwait_proposal=original,
        swap_projection=projection,selected_trial_index=selected,send_changed=value['send']!=original['send'],
        plan_changed=value['evidence']['candidate_plan']!=original['evidence']['candidate_plan'],next_review_changed=False,scope=SCOPE)
    precise_same(output,value,'complete independent atomic-swap fallback')
    return dict(checked,swap_fallback_calls_checked=1,swap_original_H_searches_checked=1,swap_evaluated_fallbacks_checked=int(evaluated),
        swap_copied_fallbacks_checked=int(not evaluated),swap_trials_checked=0 if projection is None else len(projection['trials']),
        swap_admitted_trials_checked=0 if projection is None else sum(r['admitted'] for r in projection['trials']),
        swap_selected_exchanges_checked=int(selected is not None),swap_send_changes_checked=int(value['send']!=original['send']),
        swap_plan_changes_checked=int(value['evidence']['candidate_plan']!=original['evidence']['candidate_plan']),swap_next_review_changes_checked=0)


def audit_pair(members,context,tails,outputs,evidence,priority_context,owner_context):
    original=evidence['original_tailwait_proposal']
    require(type(original) is dict and set(original)=={'decisions','evidence'},'intact copied Tailwait group required')
    checked=foundation.audit_pair([alias_member(m) for m in members],alias_context(context),[alias_member(m) for m in tails],
        original['decisions'],original['evidence'],priority_context,alias_owners(owner_context))
    wanted=deepcopy(original['decisions'])
    for i,d in enumerate(wanted):
        d['evidence']['policy']=NAME;d['evidence']['cohort_swap']=dict(schema_version=1,mode='pair_copied_tailwait',
            evaluated=False,member_index=i,send_changed=False,plan_changed=False,next_review_changed=False)
    precise_same(outputs,wanted,'exact unchanged Tailwait group actions')
    expected=dict(schema_version=1,rule=RULE,base_source_sha256=BASE_SOURCE_SHA256,model_horizon_source_sha256=HORIZON_SOURCE_SHA256,
        source_sha256=deepcopy(SOURCE_SHA256),joint_context=context,priority_context=priority_context,owner_context=owner_context,
        original_tailwait_proposal=original,evaluated=False,copied_tailwait_group=True,
        identity_projection=dict(source_policy=NAME,calculation_policy=BASE_POLICY,copied_only=True,
            fields=['joint_context.eligible[].job.policy_name','members[].workflow_snapshot.job.policy_name','tail_members[].workflow_snapshot.job.policy_name','owner_context.inventory[].job.policy_name'],
            public_identity_unchanged=True,foreign_base_owner_label='__foreign_cohort_tailwait__'),scope=SCOPE)
    precise_same(evidence,expected,'complete copied Tailwait group evidence')
    return dict(checked,swap_groups_copied_checked=1,swap_group_members_copied_checked=2)


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
        return dict(passed=True,**checks,**accounting,all_atomic_swap_trials_independently_rebuilt=True,current_owner_P_W_J_Q_guards_independently_checked=True,original_offset_histogram_and_contact_counts_preserved=True,original_H_future_cap_relaxation_unchanged=True,other_owner_nonregression_guaranteed=False,original_H_baseline_independently_reconstructed=True,accepted_Tailwait_joint_groups_copied=True,realized_success_wait_arrival_guaranteed=False,total_arrival_failure_cost_seconds=total_j,
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

