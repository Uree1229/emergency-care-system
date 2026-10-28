"""Independent public initial-slot matching and current-pair execution audit.

The intact Tailwait proposal is a calculation reference. Novel contact counts
are outside its old probability/wait guard domain. Only SHA-bound independent
auditors and public data are used; no producer or latent outcome is imported.
"""
from collections import Counter
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
NAME='eat_cohort_startslots_l15_f'
BASE_POLICY=foundation.NAME
BASE_SOURCE_SHA256='2fd6914a26c05651a79569395b30c8281881d5a72edbb11b08b85f33caa3a4a5'
if hashlib.sha256((ROOT/'inquiry_lab/cohort_tailwait_policy.py').read_bytes()).hexdigest()!=BASE_SOURCE_SHA256:
    raise ValueError('frozen Tailwait producer source differs')
NEW_INSTRUMENTATION=dict(control_audit.NEW_INSTRUMENTATION,
    actual_workflow='inquiry_lab.cohort_startslots_runner.run_once',
    exhaustive_feasible_current_rows=False,per_owner_predicted_probability_nonregression=False,
    protected_wait_first_selection=False,tail_cohort_owner_wait_nonregression=False,
    initial_cohort_review_slot_matching=True,parent_guard_flags_scope='copied paths or embedded Tailwait reference only; not new slot path',
    current_slot_path_original_JOINT_domain=False,current_slot_path_original_probability_wait_guards=False)


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
TIERS=(4,2,1)
PUBLIC_WORKERS=1
PUBLIC_POOL_LIMIT=6
MATCHING_SOURCE_SHA256={'joint_first_policy':'c176b2c5e4fd4ccb459229efb86661ad6120c8f39525414352738e40ee7f6dcc','horizon_policy':'5061be48175098ce6a30be2af0f0721852e2d67d8bd75ab475a202bcee3ce387'}
for module,digest in MATCHING_SOURCE_SHA256.items():
 require(hashlib.sha256((ROOT/'inquiry_lab'/(module+'.py')).read_bytes()).hexdigest()==digest,'frozen public slot source differs: '+module)
MODEL_RULE='initial_public_worker_slot_minimum_cost_owner_coverage_then_positive_marginal_extras'
MODEL_SCOPE='empty common public queue, 4..16 simultaneous first-contact owners; cover every owner once if feasible using fixed H assumed workers; additional contacts only in remaining slots up to original H immediate cardinalities and public limits; zero added queued work within that belief budget; current pair only is executed, no reservations, probability floor or actual success/wait/arrival guarantee'
TIE_RULE='coverage shortest augmentations visit higher effective priority then native owner order and original H pool order; extras use greatest positive marginal cost decrease beyond inherited1e-9, then higher priority/native owner/pool order'
RULE='simultaneous_empty_queue_first_roster_initial_review_slot_matching'
SCOPE='only 4..16 captured first owners sharing a workflow start and empty public queue; Tailwait retained as calculation reference, not a probability/wait floor for changed cardinalities; only current native first pair committed in unchanged FIFO order; later groups re-evaluate normally with no reservations; no actual success/wait/arrival guarantee'
CERTIFICATE_SCOPE='declared shortest augmentations and per-residual-edge reduced cost >= -existing1e-9; a simple cycle can accumulate at most node_count times that comparison allowance; not an exact arithmetic minimum-cost certificate'


def scope_members(members):
 if type(members) is not list or not 4<=len(members)<=16:return False,'first_roster_size_outside_4_to_16'
 starts=[m['workflow_snapshot']['job']['started_at'] for m in members]
 if any(type(x) is not int for x in starts) or len(set(starts))!=1:return False,'different_public_workflow_start_clocks'
 if any(m['inputs']['pending'] for m in members):return False,'nonempty_common_public_pending'
 if any(m['inputs']['patient_pending'] or m['inputs']['last_contact_at'] is not None for m in members):return False,'not_first_contact'
 return True,'simultaneous_empty_queue_first_roster'


def cover_slots(edge_rows,*,owner_count,hospital_capacities,owner_order):
 """Independent flat-arc residual network, declared epsilon shortest paths."""
 require(type(owner_count) is int and 1<=owner_count<=16,'bounded typed owner demand required')
 require(type(owner_order) is list and all(type(i) is int for i in owner_order) and sorted(owner_order)==list(range(owner_count)),'complete typed owner tie order required')
 require(type(hospital_capacities) is list and bool(hospital_capacities),'public hospital budgets required');hospitals=[];budgets={}
 for row in hospital_capacities:
  h=row['hospital'];cap=row['capacity'];require(type(h) is str and bool(h) and h not in budgets and type(cap) is int and cap>=1,'unique hospital and positive public integer budget required');hospitals.append(h);budgets[h]=cap
 require(type(edge_rows) is list,'complete source edge list required');seen=set();edges=[]
 for row in edge_rows:
  i=row['owner_index'];h=row['hospital'];cost=row['total_cost_seconds'];require(type(cost) in (int,float) and math.isfinite(cost),'finite typed contact cost required')
  require(type(i) is int and 0<=i<owner_count and h in budgets and (i,h) not in seen,'unique bounded owner-hospital edge required');seen.add((i,h));edges.append(dict(owner_index=i,hospital=h,total_cost_seconds=float(cost)))
 nodes=['source']+['owner:'+str(i) for i in owner_order]+['hospital:'+str(i) for i in range(len(hospitals))]+['sink'];lookup={n:i for i,n in enumerate(nodes)};outgoing=[[] for n in nodes];arcs=[];forward=[]
 def add(u,v,capacity,cost,kind,owner=None,hospital=None):
  k=len(arcs);arcs.extend([dict(u=u,v=v,capacity=capacity,cost=cost,reverse_arc=k+1,reverse=False,kind=kind,owner_index=owner,hospital=hospital),dict(u=v,v=u,capacity=0,cost=-cost,reverse_arc=k,reverse=True,kind=kind,owner_index=owner,hospital=hospital)])
  outgoing[u].append(k);outgoing[v].append(k+1);forward.append((k,capacity))
 source=0;sink=len(nodes)-1
 for i in owner_order:add(source,lookup['owner:'+str(i)],1,0.,'owner_demand',owner=i)
 for i in owner_order:
  for edge in edges:
   if edge['owner_index']==i:add(lookup['owner:'+str(i)],lookup['hospital:'+str(hospitals.index(edge['hospital']))],1,edge['total_cost_seconds'],'owner_hospital',owner=i,hospital=edge['hospital'])
 for j,h in enumerate(hospitals):add(lookup['hospital:'+str(j)],sink,budgets[h],0.,'hospital_budget',hospital=h)
 augmentations=[];flow=0
 while flow<owner_count:
  distances=[math.inf]*len(nodes);predecessor=[None]*len(nodes);distances[source]=0.
  for iteration in range(len(nodes)-1):
   changed=False
   for u in range(len(nodes)):
    if math.isfinite(distances[u]):
     for k in outgoing[u]:
      edge=arcs[k];v=edge['v'];candidate=distances[u]+edge['cost']
      if edge['capacity']>0 and candidate<distances[v]-EPSILON:distances[v]=candidate;predecessor[v]=k;changed=True
   if not changed:break
  if predecessor[sink] is None:break
  current=sink;path=[];visited=set()
  while current!=source:
   require(current not in visited and predecessor[current] is not None,'finite acyclic residual augmentation witness required');visited.add(current);k=predecessor[current];edge=arcs[k]
   require(edge['v']==current and edge['capacity']>=1,'augmentation exceeds residual capacity')
   path.append(dict(from_node=nodes[edge['u']],to_node=nodes[current],kind=edge['kind'],owner_index=edge['owner_index'],hospital=edge['hospital'],reverse=edge['reverse'],cost_seconds=edge['cost'],residual_capacity_before=edge['capacity']))
   edge['capacity']-=1;arcs[edge['reverse_arc']]['capacity']+=1;current=edge['u']
  path.reverse();flow+=1;augmentations.append(dict(augmentation_index=len(augmentations),flow_after=flow,unit_flow=1,shortest_distance_seconds=distances[sink],distances=[None if not math.isfinite(x) else x for x in distances],path=path,path_cost_seconds=sum((v['cost_seconds'] for v in path),0.)))
 assignments=[]
 for k,capacity in forward:
  edge=arcs[k]
  if edge['kind']=='owner_hospital' and capacity-edge['capacity']==1:assignments.append(dict(owner_index=edge['owner_index'],hospital=edge['hospital'],total_cost_seconds=edge['cost']))
 assignments.sort(key=lambda r:r['owner_index'])
 require(len(assignments)==flow and len({r['owner_index'] for r in assignments})==flow,'unit owner coverage conservation differs')
 used=Counter(a['hospital'] for a in assignments);require(all(used[h]<=budgets[h] for h in hospitals),'public hospital matching budget exceeded')
 potentials=[0.]*len(nodes)
 for iteration in range(len(nodes)-1):
  changed=False
  for u in range(len(nodes)):
   for k in outgoing[u]:
    edge=arcs[k];v=edge['v'];candidate=potentials[u]+edge['cost']
    if edge['capacity']>0 and candidate<potentials[v]-EPSILON:potentials[v]=candidate;changed=True
  if not changed:break
 reduced=[arcs[k]['cost']+potentials[u]-potentials[arcs[k]['v']] for u in range(len(nodes)) for k in outgoing[u] if arcs[k]['capacity']>0]
 require(all(x>=-EPSILON for x in reduced),'per-residual-edge epsilon certificate failed')
 return dict(schema_version=1,algorithm='unit_demand_shortest_residual_augmenting_paths',epsilon=EPSILON,node_order=nodes,owner_order=list(owner_order),hospital_capacities=deepcopy(hospital_capacities),edges=edges,augmentations=augmentations,flow=flow,required_flow=owner_count,complete=flow==owner_count,assignment=assignments,total_edge_cost_seconds=sum((r['total_cost_seconds'] for r in assignments),0.),residual_potentials=potentials,minimum_residual_reduced_cost_seconds=min(reduced,default=0.),residual_edges_above_negative_epsilon=True,maximum_simple_cycle_epsilon_allowance_seconds=len(nodes)*EPSILON,exact_minimum_cost_certificate=False,certificate_scope=CERTIFICATE_SCOPE)


def allocate_slots(members,*,baseline_proposals,priority_context):
 eligible,reason=scope_members(members);require(eligible,'slot allocation outside disclosed public scope: '+reason)
 require(type(baseline_proposals) is list and len(baseline_proposals)==len(members),'complete public H baseline list required')
 ids=[m['patient_id'] for m in members];keys=[base.owner_key(pid) for pid in ids];require(len(set(keys))==len(keys),'unique typed first-contact owners required')
 priorities={base.owner_key(p['patient_id']):p['effective_priority'] for p in priority_context['patients']}
 require(len(priority_context['patients'])==len(ids) and set(priorities)==set(keys) and all(type(t) is int and t in TIERS for t in priorities.values()),'complete public first-owner priorities required')
 models=[joint.member_model(m['inputs'],p) for m,p in zip(members,baseline_proposals)];order=sorted(range(len(ids)),key=lambda i:(-priorities[keys[i]],i));hospitals=[];edge_rows=[];records=[];limits=[]
 for i,(m,p,model) in enumerate(zip(members,baseline_proposals,models)):
  send=p['send'];pool=[r['hospital'] for r in model['hospitals']];maximum=m['inputs']['max_new'];require(type(maximum) is int and maximum>=0 and len(set(send))==len(send) and set(send)<=set(pool) and len(send)<=min(4,maximum),'original H immediate cardinality/public budget differs')
  require(len(pool)<=PUBLIC_POOL_LIMIT,'frozen public H pool bound exceeded');limit=min(len(send),maximum,4);limits.append(limit)
  for pi,h in enumerate(pool):
   descriptor=next(row for row in model['hospitals'] if row['hospital']==h)
   require(descriptor['residual_wait_seconds']==0 and descriptor['assumed_workers']==PUBLIC_WORKERS,'public empty queue or source-bound nominal worker budget differs')
   if h not in hospitals:hospitals.append(h)
   metrics=joint.member_metrics(model,[h],{})
   if limit>0:edge_rows.append(dict(owner_index=i,patient_id=ids[i],hospital=h,pool_index=pi,total_cost_seconds=metrics['total_cost_seconds'],metrics=metrics))
  records.append(dict(owner_index=i,patient_id=ids[i],effective_priority=priorities[keys[i]],original_H_immediate_send=deepcopy(send),original_H_immediate_cardinality=len(send),contact_limit=limit,candidate_pool=pool,baseline_uncontended_metrics=joint.member_metrics(model,send,{})))
 capacities=[dict(hospital=h,capacity=PUBLIC_WORKERS) for h in hospitals]
 cover=cover_slots(edge_rows,owner_count=len(members),hospital_capacities=capacities,owner_order=order) if capacities else dict(schema_version=1,algorithm='no_public_candidate_slots',flow=0,required_flow=len(members),complete=False,assignment=[],augmentations=[])
 result=dict(schema_version=1,rule=MODEL_RULE,source_sha256=deepcopy(MATCHING_SOURCE_SHA256),scope=MODEL_SCOPE,tie_rule=TIE_RULE,public_assumed_workers=PUBLIC_WORKERS,slot_capacity_source='frozen_H_config.assumed_workers',owner_order=order,owner_records=records,one_contact_edges=edge_rows,coverage=cover,coverage_complete=cover['complete'],extras=[],extra_passes=[],planned_sends=None,final_owner_metrics=None,original_probability_wait_guards_applicable=False,current_pair_only_execution=True,remaining_assignments_are_reservations=False)
 if not cover['complete']:return result
 sends=[[] for m in members];used={h:0 for h in hospitals}
 for row in cover['assignment']:sends[row['owner_index']].append(row['hospital']);used[row['hospital']]+=1
 cache={}
 def metric(i,subset):
  key=(i,tuple(subset))
  if key not in cache:cache[key]=joint.member_metrics(models[i],subset,{})
  return cache[key]
 extras=[];passes=[]
 while True:
  options=[];selected=None
  for i in order:
   if len(sends[i])>=limits[i]:continue
   before=metric(i,sends[i])
   for pi,h in enumerate(records[i]['candidate_pool']):
    if h in sends[i] or used[h]>=PUBLIC_WORKERS:continue
    after=metric(i,sends[i]+[h]);gain=before['total_cost_seconds']-after['total_cost_seconds']
    options.append(dict(owner_index=i,patient_id=ids[i],hospital=h,pool_index=pi,marginal_cost_decrease_seconds=gain,before_metrics=before,after_metrics=after,capacity_before=PUBLIC_WORKERS-used[h],strictly_positive=gain>EPSILON))
    if gain>EPSILON and (selected is None or gain>selected['marginal_cost_decrease_seconds']+EPSILON):selected=dict(extra_index=len(extras),owner_index=i,patient_id=ids[i],hospital=h,pool_index=pi,marginal_cost_decrease_seconds=gain,before_metrics=before,after_metrics=after,capacity_before=PUBLIC_WORKERS-used[h])
  passes.append(dict(pass_index=len(passes),candidate_evaluations=options,selected=deepcopy(selected)))
  if selected is None:break
  i=selected['owner_index'];h=selected['hospital'];sends[i].append(h);used[h]+=1;extras.append(selected)
 sends=[[h for h in records[i]['candidate_pool'] if h in selected] for i,selected in enumerate(sends)]
 require(all(1<=len(selected)<=limits[i] and len(set(selected))==len(selected) for i,selected in enumerate(sends)),'coverage/contact cardinality bound differs')
 require(all(used[h]<=PUBLIC_WORKERS for h in hospitals),'whole advisory plan exceeded nominal public slots')
 final=[dict(owner_index=i,patient_id=ids[i],effective_priority=priorities[keys[i]],planned_send=deepcopy(sends[i]),metrics=metric(i,sends[i])) for i in range(len(ids))]
 result.update(extras=extras,extra_passes=passes,planned_sends=sends,final_owner_metrics=final,hospital_slot_usage=[dict(hospital=h,used=used[h],capacity=PUBLIC_WORKERS) for h in hospitals])
 return result


def audit_pair(members,context,tails,outputs,evidence,priority_context,owner_context):
 require(type(evidence) is dict and type(evidence.get('original_tailwait_proposal')) is dict and set(evidence['original_tailwait_proposal'])=={'decisions','evidence'},'intact original Tailwait calculation reference required')
 original=evidence['original_tailwait_proposal']
 checked=foundation.audit_pair([alias_member(m) for m in members],alias_context(context),[alias_member(m) for m in tails],original['decisions'],original['evidence'],priority_context,alias_owners(owner_context))
 audit_priority_context(priority_context,context,members,tails)
 roster=members+tails;eligible,reason=scope_members(roster);baselines=[];allocation=None;evaluated=False
 names=('startslots_groups_scope_eligible','startslots_groups_scope_ineligible','startslots_groups_evaluated','startslots_groups_coverage_incomplete','startslots_groups_copied','startslots_owner_models_checked','startslots_public_H_proposals_checked','startslots_one_contact_edges_checked','startslots_augmentations_checked','startslots_residual_certificates_checked','startslots_extra_passes_checked','startslots_extra_candidates_checked','startslots_extra_contacts_selected','startslots_planned_owners_covered','startslots_current_members_checked','startslots_current_send_changes','startslots_interventions','startslots_current_matching_contacts_checked')
 counters=Counter({name:0 for name in names})
 if eligible:
  counters['startslots_groups_scope_eligible']=1;baselines=evidence['public_H_baseline_proposals']
  allocation=allocate_slots(roster,baseline_proposals=baselines,priority_context=priority_context);evaluated=allocation['coverage_complete'];coverage=allocation['coverage']
  counters.update(startslots_owner_models_checked=len(roster),startslots_public_H_proposals_checked=len(roster),startslots_one_contact_edges_checked=len(allocation['one_contact_edges']),startslots_augmentations_checked=len(coverage['augmentations']),startslots_residual_certificates_checked=int(coverage['algorithm']!='no_public_candidate_slots'),startslots_planned_owners_covered=coverage['flow'])
  if evaluated:
   counters.update(startslots_groups_evaluated=1,startslots_extra_passes_checked=len(allocation['extra_passes']),startslots_extra_candidates_checked=sum(len(p['candidate_evaluations']) for p in allocation['extra_passes']),startslots_extra_contacts_selected=len(allocation['extras']),startslots_current_members_checked=2,startslots_current_matching_contacts_checked=sum(len(p) for p in allocation['planned_sends'][:2]))
  else:counters['startslots_groups_coverage_incomplete']=1
 else:counters['startslots_groups_scope_ineligible']=1
 if not evaluated:counters['startslots_groups_copied']=1
 wanted_outputs=[]
 for i in range(2):
  if evaluated:
   value=deepcopy(baselines[i]);sends=allocation['planned_sends'][i];value['send']=list(sends);value['reason']='cohort_startslots_current_pair_only';ev=value['evidence'];ev['sent_hospitals']=list(sends);ev['planned_expected_cost']=round(allocation['final_owner_metrics'][i]['metrics']['total_cost_seconds'],3)
   for row in ev['candidate_plan']:row['planned_offset_seconds']=0 if row['hospital'] in sends else None
  else:value=deepcopy(original['decisions'][i])
  wanted_outputs.append(value)
 changed=[new['send']!=old['send'] for new,old in zip(wanted_outputs,original['decisions'])];intervened=any(changed)
 for i,value in enumerate(wanted_outputs):
  value['evidence']['policy']=NAME;value['evidence']['cohort_startslots']=dict(schema_version=1,mode='pair_cohort_startslots',scope_eligible=eligible,evaluated=evaluated,intervened=intervened,member_index=i,send_changed=changed[i],original_H_plan_reference_only=evaluated,original_Tailwait_send=deepcopy(original['decisions'][i]['send']),planned_current_send=deepcopy(value['send']))
 precise_same(outputs,wanted_outputs,'independent public slot current-pair actions')
 wanted=dict(schema_version=1,rule=RULE,base_policy=BASE_POLICY,base_source_sha256=BASE_SOURCE_SHA256,matching_source_sha256=deepcopy(MATCHING_SOURCE_SHA256),joint_context=context,priority_context=priority_context,owner_context=owner_context,identity_projection=dict(source_policy=NAME,calculation_policy=BASE_POLICY,fields=['joint_context.eligible[].job.policy_name','members[].workflow_snapshot.job.policy_name','tail_members[].workflow_snapshot.job.policy_name','owner_context.inventory[].job.policy_name'],copied_only=True,public_identity_unchanged=True,foreign_base_owner_label='__foreign_cohort_tailwait__'),original_tailwait_proposal=original,scope_eligible=eligible,scope_reason=reason,evaluated=evaluated,allocation=allocation,public_H_baseline_proposals=baselines,current_pair_only_execution=True,future_assignments_committed=False,old_probability_wait_guards_protect_novel_counts=False,intervened=intervened,scope=SCOPE)
 precise_same(evidence,wanted,'complete independent startslot evidence')
 counters['startslots_current_send_changes']=sum(changed);counters['startslots_interventions']=int(intervened)
 checked=dict(checked,original_Tailwait_reference_groups_applied=checked['joint_groups_applied'],joint_groups_applied=int(intervened))
 return dict(checked,**dict(counters))



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
            require(output['evidence'].get('cohort_startslots') == dict(schema_version=1, mode='horizon_fallback', evaluated=False, intervened=False, original_H_plan_reference_only=False), 'late fallback changed startslots scope')
            require(output['evidence'].get('cohort_tailwait') == dict(schema_version=1, mode='horizon_fallback', evaluated=False, intervened=False), 'late fallback changed tailwait scope')
            require(output['evidence'].get('cohort_pairshield') == dict(schema_version=1, mode='horizon_fallback', evaluated=False, intervened=False), 'late fallback changed stoploss scope')
            require(output['evidence'].get('cohort_pairscope') == dict(schema_version=1, mode='horizon_fallback', evaluated=False, intervened=False), 'late fallback changed parent Pairscope scope')
            require(output['evidence'].get('cohort_envelope') == dict(schema_version=1, mode='horizon_fallback', evaluated=False, intervened=False), 'late fallback changed parent Envelope scope')
            require(output['evidence'].get('cohort_waitpareto') == dict(schema_version=1, mode='horizon_fallback', evaluated=False, intervened=False), 'late fallback changed parent Waitpareto scope')
            require(output['evidence'].get('cohort_pareto') == dict(schema_version=1, mode='horizon_fallback', evaluated=False, intervened=False), 'late fallback changed parent Pareto scope')
            require(output['evidence'].get('cohort_fullmenu') == dict(schema_version=1, mode='horizon_fallback', evaluated=False, intervened=False), 'late fallback changed original Fullmenu scope')
            require(output['evidence'].get('cohort_stoploss') == dict(schema_version=1, mode='horizon_fallback', evaluated=False, intervened=False), 'late fallback changed parent Stoploss scope')
            normalized = deepcopy(output); normalized['evidence'].pop('cohort_startslots'); normalized['evidence'].pop('cohort_tailwait'); normalized['evidence'].pop('cohort_pairshield'); normalized['evidence'].pop('cohort_pairscope'); normalized['evidence'].pop('cohort_envelope'); normalized['evidence'].pop('cohort_waitpareto'); normalized['evidence'].pop('cohort_pareto'); normalized['evidence'].pop('cohort_fullmenu'); normalized['evidence'].pop('cohort_stoploss'); normalized['evidence'].pop('cohort_followplan'); normalized['evidence'].pop('cohort_recourse')
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
        embedded_reference_assertions=dict(
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
            scope='intact independently audited Pairshield proposal; only inherited-active groups with nonempty first-contact tails receive all-owner predicted capped-wait nonregression against the embedded Stoploss actual row within existing1e-9; original full P admission verified before explicit P/wait intersection and unchanged selector; every owner, immutable scores and original feasibility retained; new no-tail, inactive and singleton copied; no realized outcome guarantee')
        return dict(passed=True,**checks,**accounting,total_arrival_failure_cost_seconds=total_j,
            mean_arrival_failure_cost_seconds=total_j/len(branch['patients']) if branch['patients'] else None,
            embedded_Tailwait_reference_assertions=embedded_reference_assertions,
            parent_guard_flags_scope='copied paths or embedded Tailwait reference only; not new slot path',
            original_Tailwait_proposal_independently_checked=True,
            current_slot_path_original_JOINT_domain=False,current_slot_path_original_probability_wait_guards=False,
            initial_cohort_review_slot_matching_independently_checked=True,
            public_H_assumed_workers_not_latent_staffing=True,
            mincost_augmentation_and_reduced_edge_certificate_checked=True,
            exact_minimum_cost_claimed=False,certificate_tolerance_is_per_residual_edge=True,
            current_pair_only_execution_checked=True,future_assignments_committed=False,
            actual_outputs_sends_and_all_patient_FIFO_checked=True,
            integer_occupancy_and_contact_budget_independently_checked=True,
            captured_waiting_owner_provenance_and_stale_checks_retained=True,
            realized_success_wait_arrival_guaranteed=False,
            scope='native outputs and caps/FIFO checked; old Tailwait proof and every old guard assertion apply only to copied paths or the embedded calculation reference; novel initial-slot contacts have no old JOINT/P/W floor; coverage is an advisory whole-roster plan and only current native pair is committed')
    except (KeyError,TypeError,IndexError,AttributeError,OverflowError,StopIteration) as error:
        raise base.CohortHedgeAuditError('malformed saved cohort-startslots artifact') from error


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

