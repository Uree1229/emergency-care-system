"""Independent fixed-initial queue/horizon membership shield on new first pairs.

Only the declared hard-zero first-wave membership guard changes. The complete
frozen Pairscope proof remains separately checked; no producer model, policy,
future event, actual outcome or extra scoring call is imported.
"""
from collections import Counter
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
from . import cohort_pairscope_audit as foundation
control_audit=foundation

ROOT=Path(__file__).resolve().parents[1]
if hashlib.sha256(Path(foundation.__file__).read_bytes()).hexdigest()!='a7a108c18d180b2c16258f78a0c866d64732c65fa1e14b7f33d6b8d8fa5248e7':
    raise ValueError('frozen independent Pairscope auditor differs')
base=foundation.base
parent,horizon,joint=foundation.parent,foundation.horizon,foundation.joint
numeric=foundation.numeric
dynamic=foundation.dynamic
require,precise_same,exact_copy=foundation.require,foundation.precise_same,foundation.exact_copy
_pid,_integer,EPOCH=foundation._pid,foundation._integer,foundation.EPOCH
INPUT_KEYS,CALL_KEYS,GROUP_KEYS=foundation.INPUT_KEYS,foundation.CALL_KEYS,foundation.GROUP_KEYS
NAME='eat_cohort_pairshield_l15_f'
BASE_POLICY=foundation.NAME
BASE_SOURCE_SHA256='193127b8019a34c4dc7736d9dce3c11e260d09ef0214660cda00a52709ddf1da'
if hashlib.sha256((ROOT/'inquiry_lab/cohort_pairscope_policy.py').read_bytes()).hexdigest()!=BASE_SOURCE_SHA256:
    raise ValueError('frozen Pairscope producer source differs')
NEW_INSTRUMENTATION=dict(control_audit.NEW_INSTRUMENTATION,
    actual_workflow='inquiry_lab.cohort_pairshield_runner.run_once',hard_zero_first_wave_membership_guard=True)


def _project_name(name):
    if name==NAME:return BASE_POLICY
    return '__foreign_cohort_pairscope__' if name==BASE_POLICY else name


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
wait_selector=foundation.foundation.foundation
SCORING_SOURCE_SHA256={
 'cohort_pairscope_model':'497d54ab3774b7f01e53e2f7aeff0bf852414ae26cd6a556f95140eb181ffdbb',
 'cohort_pairscope_policy':'193127b8019a34c4dc7736d9dce3c11e260d09ef0214660cda00a52709ddf1da',
 'cohort_waitpareto_model':'730e39ee0333fec00d7d6f4421dfc4c05cb9e387137625bf593958066e2110e9',
 'cohort_pareto_model':'3292bae7a0c2d0017e7fa39a80adc60819e4f23879b48e2d871975aeb343be91'}
for module,digest in SCORING_SOURCE_SHA256.items():
 require(hashlib.sha256((ROOT/'inquiry_lab'/(module+'.py')).read_bytes()).hexdigest()==digest,'frozen shield source differs: '+module)
GUARD_RULE='retain_removed_baseline_and_reject_added_candidate_queued_H_boundary_zero_memberships'
GUARD_SCOPE='current first pair in newly activated Pairscope groups only; exact stored queued wait>=own H and every stored initial mass==0; retain baseline boundary hospital contacts and reject newly added candidate boundary contacts; shared membership classification may change freely; no new numeric threshold, CDF law or physical-zero guarantee'
SELECT_RULE='unchanged_wait_selector_on_explicit_probability_and_support_intersection_subdomain'
SELECT_SCOPE='validate complete original probability-admitted source first, then explicitly project the intersection with support-admitted rows; unchanged wait-first selector on that labeled subdomain; original JOINT feasibility and score values are never reclassified'
RULE='new_Pairscope_groups_only_with_bidirectional_queued_H_boundary_membership_support'
OBJECTIVE='preserve full original P-admitted rows and immutable scores; intersect with H-boundary membership support, then unchanged wait-first bands and priority/Stoploss-anchor ties on an explicit labeled subdomain'
SCOPE='only parent newly_activated exact-two first-pair groups; inherited active, inactive tail and singleton fallback copied; removed-baseline-zero and added-candidate-zero memberships rejected, shared membership class changes allowed; exact public own H and stored zero masses, no probability threshold or physical-zero/success guarantee'


def _indices(values):
 require(type(values) is list and all(type(i) is int and i>=0 for i in values) and len(values)==len(set(values)),'complete unique typed row indices required')


def _owner_support(person):
 pid=person['patient_id'];base.owner_key(pid);edges=person['cell_edges_seconds']
 require(type(edges) is list and bool(edges) and all(type(x) in (int,float) and math.isfinite(x) and x>0 for x in edges) and all(b>a for a,b in zip(edges,edges[1:])),'complete positive increasing owner H grid required')
 horizon=edges[-1];items=person['fixed_initial_items'];require(type(items) is list,'complete initial descriptor list required');seen=set();records=[]
 for item in items:
  hospital=item['hospital'];key=base.owner_key(hospital);wait=item['wait_seconds'];mode=item['mode'];masses=item['masses']
  require(key not in seen,'duplicate typed initial hospital');seen.add(key)
  require(mode in ('queued','residual') and type(wait) in (int,float) and math.isfinite(wait) and wait>=0,'valid initial mode and wait required')
  require(type(masses) is list and len(masses)==len(edges) and all(type(x) in (int,float) and math.isfinite(x) and x>=0 for x in masses),'complete finite stored initial masses required')
  zero=all(x==0 for x in masses)
  records.append(dict(hospital=hospital,mode=mode,wait_seconds=wait,horizon_seconds=horizon,stored_mass_count=len(masses),all_stored_masses_zero=zero,queued_H_boundary_zero=mode=='queued' and wait>=horizon and zero))
 return dict(patient_id=pid,cell_edges_seconds=deepcopy(edges),horizon_seconds=horizon,initial_hospital_support=records)


def guard_rows(row_scores,*,baseline_index,current_pair_patient_ids,menu_indices,feasible_row_indices):
 require(type(row_scores) is list and 1<=len(row_scores)<=400,'complete bounded source score rows required');_indices(menu_indices);_indices(feasible_row_indices)
 require(len(menu_indices)==len(row_scores) and set(menu_indices)==set(feasible_row_indices),'complete original feasible coverage required')
 require(type(baseline_index) is int and baseline_index in menu_indices,'original Stoploss anchor required')
 require(type(current_pair_patient_ids) is list and len(current_pair_patient_ids)==2,'complete current pair required')
 keys=[base.owner_key(pid) for pid in current_pair_patient_ids];require(len(set(keys))==2,'distinct typed current pair required');indexed={}
 for row in row_scores:
  index=row['row_index'];require(type(index) is int and index>=0 and index not in indexed,'unique typed source row required')
  people=row['first_cohort_envelope']['patient_scores'];require(type(people) is list and len(people)==2,'complete first-wave current pair required');owners={}
  for person in people:
   key=base.owner_key(person['patient_id']);require(key not in owners,'duplicate typed first owner');owners[key]=_owner_support(person)
  require(set(owners)==set(keys),'current first owner partition differs');indexed[index]=owners
 require(list(indexed)==menu_indices,'complete source row order differs');baseline=indexed[baseline_index];checks=[];admitted=[];rejected=[]
 for index,owners in indexed.items():
  comparisons=[]
  for key,pid in zip(keys,current_pair_patient_ids):
   old=baseline[key];new=owners[key];require(old['cell_edges_seconds']==new['cell_edges_seconds'],'owner H grid differs across rows')
   previous=old['initial_hospital_support'];current=new['initial_hospital_support'];require(len(previous)==len(current),'original H cardinality differs')
   old_by={base.owner_key(x['hospital']):x for x in previous};new_by={base.owner_key(x['hospital']):x for x in current}
   removed=[x['hospital'] for x in previous if base.owner_key(x['hospital']) not in new_by and x['queued_H_boundary_zero']]
   added=[x['hospital'] for x in current if base.owner_key(x['hospital']) not in old_by and x['queued_H_boundary_zero']]
   shared=[dict(hospital=x['hospital'],baseline_boundary_zero=x['queued_H_boundary_zero'],candidate_boundary_zero=new_by[base.owner_key(x['hospital'])]['queued_H_boundary_zero']) for x in previous if base.owner_key(x['hospital']) in new_by and x['queued_H_boundary_zero']!=new_by[base.owner_key(x['hospital'])]['queued_H_boundary_zero']]
   comparisons.append(dict(patient_id=pid,horizon_seconds=old['horizon_seconds'],baseline_initial_hospitals=[x['hospital'] for x in previous],candidate_initial_hospitals=[x['hospital'] for x in current],baseline_boundary_hospitals=[x['hospital'] for x in previous if x['queued_H_boundary_zero']],candidate_boundary_hospitals=[x['hospital'] for x in current if x['queued_H_boundary_zero']],removed_baseline_boundary_hospitals=removed,added_candidate_boundary_hospitals=added,shared_boundary_status_changes=shared,admissible=not removed and not added))
  allowed=all(x['admissible'] for x in comparisons);require(index!=baseline_index or allowed,'baseline failed own support law');(admitted if allowed else rejected).append(index)
  checks.append(dict(row_index=index,is_baseline=index==baseline_index,owner_checks=comparisons,admissible=allowed))
 return dict(schema_version=1,rule=GUARD_RULE,source_sha256=deepcopy(SCORING_SOURCE_SHA256),baseline_row_index=baseline_index,current_pair_patient_ids=list(current_pair_patient_ids),baseline_owner_support=[baseline[key] for key in keys],row_checks=checks,admissible_row_indices=admitted,rejected_row_indices=rejected,checked_row_count=len(row_scores),current_owner_count=2,baseline_always_admissible=baseline_index in admitted,scope=GUARD_SCOPE)


def select_supported_wait(row_scores,*,baseline_index,owner_inventory,menu_indices,feasible_row_indices,probability_admissible_row_indices,support_admissible_row_indices):
 _indices(support_admissible_row_indices)
 original=wait_selector.select_protected_wait(row_scores,admissible_row_indices=probability_admissible_row_indices,baseline_index=baseline_index,owner_inventory=owner_inventory,menu_indices=menu_indices,feasible_row_indices=feasible_row_indices)
 baseline=next(row for row in row_scores if row['row_index']==baseline_index);ids=[p['patient_id'] for p in baseline['first_cohort_envelope']['patient_scores']]
 support=guard_rows(row_scores,baseline_index=baseline_index,current_pair_patient_ids=ids,menu_indices=menu_indices,feasible_row_indices=feasible_row_indices)
 require(support_admissible_row_indices==support['admissible_row_indices'],'support-admitted set differs from complete law')
 domain=[i for i in menu_indices if i in set(probability_admissible_row_indices) and i in set(support_admissible_row_indices)];require(baseline_index in domain,'baseline omitted from projected intersection')
 rows=[r for r in row_scores if r['row_index'] in set(domain)]
 selected=wait_selector.select_protected_wait(rows,admissible_row_indices=domain,baseline_index=baseline_index,owner_inventory=owner_inventory,menu_indices=domain,feasible_row_indices=domain)
 return dict(schema_version=1,rule=SELECT_RULE,source_sha256=deepcopy(SCORING_SOURCE_SHA256),original_menu_indices=list(menu_indices),original_feasible_row_indices=list(feasible_row_indices),probability_admissible_row_indices=list(probability_admissible_row_indices),support_admissible_row_indices=list(support_admissible_row_indices),projected_domain_row_indices=domain,original_probability_domain_selection=original,unchanged_selector=selected,baseline_in_domain=True,original_JOINT_rows_reclassified=False,selected_index=selected['selected_index'],scope=SELECT_SCOPE)


def audit_pair(members,context,tails,outputs,evidence,priority_context,owner_context):
 require(type(evidence) is dict and type(evidence.get('original_pairscope_proposal')) is dict and set(evidence['original_pairscope_proposal'])=={'decisions','evidence'},'missing intact Pairscope parent')
 original=evidence['original_pairscope_proposal']
 checked=foundation.audit_pair([alias_member(m) for m in members],alias_context(context),[alias_member(m) for m in tails],original['decisions'],original['evidence'],priority_context,alias_owners(owner_context))
 priorities=audit_priority_context(priority_context,context,members,tails);old=original['evidence'];evaluated=old['evaluated'];shield=old['newly_activated'];base_index=old['selected_index'];selected=base_index;anchor=old['stoploss_anchor_index']
 require(type(evaluated) is bool and type(shield) is bool,'typed original activation required')
 require(not shield or (evaluated and not old['inherited_evaluated'] and tails==[]),'shield extends beyond newly activated no-tail first pairs')
 guard=None;selection=None
 counts=Counter({key:0 for key in ('pairshield_groups_guarded','pairshield_groups_inherited','pairshield_rows_checked','pairshield_removed_zero_vetoes','pairshield_added_zero_vetoes','pairshield_baseline_checks','pairshield_interventions','pairshield_initial_sources_checked','pairshield_support_rows_admitted','pairshield_support_rows_rejected','pairshield_projected_domain_rows_checked')})
 if shield:
  for row in old['new_row_scores']:
   people={base.owner_key(p['patient_id']):p for p in row['first_cohort_envelope']['patient_scores']};sends=row['dynamic_row_projection']['projection']['current_pair_send']
   for index,member in enumerate(members):
    person=people[base.owner_key(member['patient_id'])];horizon=member['inputs']['time_context']['evaluation_end_at']-member['inputs']['now']
    require(person['cell_edges_seconds'][-1]==horizon,'fixed initial H differs from captured public current owner')
    require([base.owner_key(item['hospital']) for item in person['fixed_initial_items']]==[base.owner_key(h) for h in sends[index]],'fixed initial memberships differ from original current-pair sends')
    counts['pairshield_initial_sources_checked']+=1
  guard=guard_rows(old['new_row_scores'],baseline_index=anchor,current_pair_patient_ids=[m['patient_id'] for m in members],menu_indices=old['new_menu_indices'],feasible_row_indices=old['new_feasible_row_indices'])
  selection=select_supported_wait(old['new_row_scores'],baseline_index=anchor,owner_inventory=owner_context['inventory'],menu_indices=old['new_menu_indices'],feasible_row_indices=old['new_feasible_row_indices'],probability_admissible_row_indices=old['owner_probability_guard']['admissible_row_indices'],support_admissible_row_indices=guard['admissible_row_indices'])
  precise_same(selection['original_probability_domain_selection'],old['protected_wait_selection'],'unchanged full source P-domain selection');selected=selection['selected_index']
  counts.update(pairshield_groups_guarded=1,pairshield_rows_checked=guard['checked_row_count'],pairshield_baseline_checks=1,
   pairshield_removed_zero_vetoes=sum(len(o['removed_baseline_boundary_hospitals']) for r in guard['row_checks'] for o in r['owner_checks']),
   pairshield_added_zero_vetoes=sum(len(o['added_candidate_boundary_hospitals']) for r in guard['row_checks'] for o in r['owner_checks']),
   pairshield_support_rows_admitted=len(guard['admissible_row_indices']),pairshield_support_rows_rejected=len(guard['rejected_row_indices']),pairshield_projected_domain_rows_checked=len(selection['projected_domain_row_indices']))
 else:counts['pairshield_groups_inherited']=1
 intervened=selected!=base_index;counts['pairshield_interventions']=int(intervened)
 wanted=dict(schema_version=1,rule=RULE,base_policy=BASE_POLICY,base_source_sha256=BASE_SOURCE_SHA256,scoring_source_sha256=deepcopy(SCORING_SOURCE_SHA256),
  identity_projection=dict(source_policy=NAME,calculation_policy=BASE_POLICY,fields=['joint_context.eligible[].job.policy_name','members[].workflow_snapshot.job.policy_name','tail_members[].workflow_snapshot.job.policy_name','owner_context.inventory[].job.policy_name'],copied_only=True,public_identity_unchanged=True,foreign_base_owner_label='__foreign_cohort_pairscope__'),
  joint_context=context,priority_context=priority_context,owner_context=owner_context,original_pairscope_proposal=original,evaluated=evaluated,shield_evaluated=shield,mean_queue_support_guard=guard,protected_support_selection=selection,base_selected_index=base_index,stoploss_anchor_index=anchor,selected_index=selected,intervened=intervened,tiers=[4,2,1],tolerances=dict(probability=1e-12,wait_seconds=1e-9),objective=OBJECTIVE,scope=SCOPE)
 precise_same(evidence,wanted,'complete independent first-wave boundary membership shield');require(type(outputs) is list and len(outputs)==2,'actual shield output pair differs')
 env=old['original_envelope_proposal']['evidence'];full=env['original_waitpareto_proposal']['evidence']['original_pareto_proposal']['evidence']['original_fullmenu_proposal']['evidence'];table=foundation.fullmenu.parent_views(full['original_stoploss_proposal'])['joint']
 chosen=next((r for r in old['new_row_scores'] if r['row_index']==selected),None)
 for index in range(2):
  actual=deepcopy(original['decisions'][index])
  if intervened:
   sends=table['combinations'][selected]['hospitals'][index];actual=deepcopy(table['baseline_proposals'][index]);actual['send']=list(sends);actual['reason']='cohort_pairshield_current_only';ev=actual['evidence'];ev['sent_hospitals']=list(sends);ev['planned_expected_cost']=round(chosen['member_metrics'][index]['metrics']['total_cost_seconds'],3)
   for row in ev['candidate_plan']:row['planned_offset_seconds']=0 if row['hospital'] in sends else None
  actual['evidence']['policy']=NAME;actual['evidence']['cohort_pairshield']=dict(schema_version=1,mode='pair_cohort_pairshield',evaluated=evaluated,shield_evaluated=shield,intervened=intervened,member_index=index,base_selected_index=base_index,stoploss_anchor_index=anchor,selected_index=selected,effective_priority=priorities[_pid(members[index]['patient_id'])])
  precise_same(outputs[index],actual,'independently reconstructed shield current output')
 checked=dict(checked,original_Pairscope_groups_applied=checked['joint_groups_applied'],joint_groups_applied=int(selected!=table['baseline_index']))
 return dict(checked,**dict(counts))



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
            require(output['evidence'].get('cohort_pairshield') == dict(schema_version=1, mode='horizon_fallback', evaluated=False, intervened=False), 'late fallback changed stoploss scope')
            require(output['evidence'].get('cohort_pairscope') == dict(schema_version=1, mode='horizon_fallback', evaluated=False, intervened=False), 'late fallback changed parent Pairscope scope')
            require(output['evidence'].get('cohort_envelope') == dict(schema_version=1, mode='horizon_fallback', evaluated=False, intervened=False), 'late fallback changed parent Envelope scope')
            require(output['evidence'].get('cohort_waitpareto') == dict(schema_version=1, mode='horizon_fallback', evaluated=False, intervened=False), 'late fallback changed parent Waitpareto scope')
            require(output['evidence'].get('cohort_pareto') == dict(schema_version=1, mode='horizon_fallback', evaluated=False, intervened=False), 'late fallback changed parent Pareto scope')
            require(output['evidence'].get('cohort_fullmenu') == dict(schema_version=1, mode='horizon_fallback', evaluated=False, intervened=False), 'late fallback changed original Fullmenu scope')
            require(output['evidence'].get('cohort_stoploss') == dict(schema_version=1, mode='horizon_fallback', evaluated=False, intervened=False), 'late fallback changed parent Stoploss scope')
            normalized = deepcopy(output); normalized['evidence'].pop('cohort_pairshield'); normalized['evidence'].pop('cohort_pairscope'); normalized['evidence'].pop('cohort_envelope'); normalized['evidence'].pop('cohort_waitpareto'); normalized['evidence'].pop('cohort_pareto'); normalized['evidence'].pop('cohort_fullmenu'); normalized['evidence'].pop('cohort_stoploss'); normalized['evidence'].pop('cohort_followplan'); normalized['evidence'].pop('cohort_recourse')
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
            original_Waitpareto_proposal_independently_checked=True,
            original_Envelope_proposal_independently_checked=True,
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
            scope='intact independently audited Pairscope proposal; only newly activated first-pair rows receive the exact declared fixed-initial wait>=H/all-zero-mass membership guard; shared hospitals may cross the boundary; full parent all-owner P admission verified before explicit support/P intersection projection and unchanged wait selector; parent scores, feasibility, active/non-new/fallback actions, caps and FIFO retained; no physical zero or realized-success guarantee')
    except (KeyError,TypeError,IndexError,AttributeError,OverflowError,StopIteration) as error:
        raise base.CohortHedgeAuditError('malformed saved cohort-pairshield artifact') from error


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

