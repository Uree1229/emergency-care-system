"""Independent reference-set preserving, whole-cohort dispatch audit.

The intact Tailwait pair is a calculation reference.  The new batch executes
all first owners at a single validated public prefix.  Its inquiry submission
order is deliberately new; frozen FIFO processing itself is not rewritten.
Only independently pinned audit helpers and public inputs are used here.
"""
from collections import Counter
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
from . import cohort_startslots_audit as previous_audit

control_audit=previous_audit
foundation=previous_audit.foundation
ROOT=Path(__file__).resolve().parents[1]
require=foundation.require
require(hashlib.sha256(Path(previous_audit.__file__).read_bytes()).hexdigest()==
    'e1871c3be58b01dad25e3029992a3eef8937dd4efe8290e7a3e3b4c41baaa757',
    'frozen independent Startslots auditor differs')
base=foundation.base
parent,horizon,joint=foundation.parent,foundation.horizon,foundation.joint
dynamic=foundation.dynamic
fullmenu=foundation.fullmenu
precise_same,exact_copy=foundation.precise_same,foundation.exact_copy
_pid,_integer,EPOCH=foundation._pid,foundation._integer,foundation.EPOCH
INPUT_KEYS,CALL_KEYS=foundation.INPUT_KEYS,foundation.CALL_KEYS
NAME='eat_cohort_batchwave_l15_f'
BASE_POLICY=foundation.NAME
BASE_SOURCE_SHA256='2fd6914a26c05651a79569395b30c8281881d5a72edbb11b08b85f33caa3a4a5'
require(hashlib.sha256((ROOT/'inquiry_lab/cohort_tailwait_policy.py').read_bytes()).hexdigest()==BASE_SOURCE_SHA256,
    'frozen Tailwait producer source differs')
NEW_INSTRUMENTATION=dict(foundation.NEW_INSTRUMENTATION,
    actual_workflow='inquiry_lab.cohort_batchwave_runner.run_once',maximum_group_size=16,
    exhaustive_feasible_current_rows=False,per_owner_predicted_probability_nonregression=False,
    protected_wait_first_selection=False,tail_cohort_owner_wait_nonregression=False,
    whole_cohort_preserved_reference_batch=True,preserved_reference_first_wave_batch=True,
    actual_entire_first_roster_commit=True,current_pair_only_execution=False,
    native_query_submission_order_preserved=False,actual_hospital_FIFO_unchanged=True,
    reference_tail_CS_is_not_actual_Tailwait_continuation=True,
    parent_guard_flags_scope='copied paths or embedded Tailwait reference only; not new batch path',
    current_batch_path_original_JOINT_domain=False,current_batch_path_original_probability_wait_guards=False)


def _project_name(name):
    if name==NAME:return BASE_POLICY
    return '__foreign_cohort_tailwait__' if name==BASE_POLICY else name


def alias_context(context):
    value=deepcopy(context)
    for row in value['eligible']:
        require(row['job']['policy_name']==NAME,'current eligible job has wrong native batchwave policy')
        row['job']['policy_name']=BASE_POLICY
    return value


def alias_member(member):
    value=deepcopy(member)
    require(value['workflow_snapshot']['job']['policy_name']==NAME,'current member has wrong native batchwave policy')
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


scope_members=previous_audit.scope_members
cover_slots=previous_audit.cover_slots
EPSILON=previous_audit.EPSILON
PUBLIC_WORKERS=previous_audit.PUBLIC_WORKERS
PUBLIC_POOL_LIMIT=previous_audit.PUBLIC_POOL_LIMIT
TIERS=previous_audit.TIERS


def _digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def _stoploss_reference(proposal):
    """The immutable reference pair, never the new batch's future replay."""
    shield=proposal['evidence']['original_pairshield_proposal']['evidence']
    scope=shield['original_pairscope_proposal']['evidence']
    envelope=scope['original_envelope_proposal']['evidence']
    wait=envelope['original_waitpareto_proposal']['evidence']
    pareto=wait['original_pareto_proposal']['evidence']
    full=pareto['original_fullmenu_proposal']['evidence']
    return full['original_stoploss_proposal']


def audit_reference_dynamic(recorded,parent_proposal,*,members,tail_members,joint_context,priority_context):
    """Rebuild the single new D projection using frozen independent CS laws."""
    stoploss=_stoploss_reference(parent_proposal)
    prepared=fullmenu.prepare(stoploss,members=members,tail_members=tail_members,
        joint_context=joint_context,priority_context=priority_context)
    anchor=stoploss['evidence']['selected_index']
    table=prepared['independent_table']['combinations']
    require(type(anchor) is int and 0<=anchor<len(table) and table[anchor]['feasible'],
        'reference anchor must be the original independently feasible Stoploss actual row')
    row,checks=dynamic.audit_row(recorded,anchor,0,table[anchor]['hospitals'],
        prepared['current_models'],prepared['dynamic_members'],prepared['dynamic_context'],
        prepared['dynamic_tails'],priority_context)
    return prepared,anchor,row,checks


REFERENCE_SOURCE_SHA256={
    'cohort_dynamic_policy':'62ef7fbba3ee90015f800aae7fb38f820955c7b92cd46d35df216c9a54afccfb',
    'cohort_fullmenu_model':'8185c654bb7402d67708b14bd151572b50950cc36d639c0b5153fa37eececaf1',
    'cohort_startslots_model':'6ebaa7635c718e69c5d99a9d45537447aeff47b7e123f6b2066ed2269244f299'}
for module,digest in REFERENCE_SOURCE_SHA256.items():
    require(hashlib.sha256((ROOT/'inquiry_lab'/(module+'.py')).read_bytes()).hexdigest()==digest,
        'frozen batch-wave reference source differs: '+module)
MODEL_RULE='preserve_single_Stoploss_anchor_frozen_CS_reference_wave_primary_cover_then_all_remaining_queries'
MODEL_SCOPE='empty common public queue, 4..16 simultaneous first owners; one same-clock Dynamic reference at original Stoploss actual anchor, current pair plus frozen CS-reference tails/singleton; preserve every reference query set and count, not an actual Tailwait continuation; match one primary within each preserved set using public H worker budget, then submit every remaining query; entire roster is actual atomic batch, no old probability/wait floor or actual success/wait/arrival guarantee'
DISPATCH_RULE='matched primaries in higher effective priority then native owner order; all remaining preserved queries in native owner and reference send order; actual hospital FIFO unchanged'
RULE='whole_first_roster_batch_preserving_Stoploss_anchor_frozen_CS_reference_query_sets'
SCOPE='empty common queue and same-start first roster4..16; one original Stoploss-anchor Dynamic/CS reference, all its query memberships/counts preserved; primary match changes actual whole-roster submission order, no actual Tailwait continuation or old P/W-domain guarantee; all other paths copied accepted Tailwait'


def _source_sha(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode()).hexdigest()


def audit_reference(recorded,parent_proposal,*,members,tail_members,joint_context,priority_context):
    require(type(recorded) is dict,'complete newly computed CS reference required')
    prepared,anchor,row,checks=audit_reference_dynamic(recorded['dynamic_reference_row'],parent_proposal,
        members=members,tail_members=tail_members,joint_context=joint_context,priority_context=priority_context)
    joint_evidence=prepared['views']['joint'];table=prepared['independent_table']['combinations']
    require(anchor==parent_proposal['evidence']['stoploss_anchor_index'],'Tailwait and original actual Stoploss anchors differ')
    records=[]
    def append(people,proposals,sends,kind,step):
        require(len(people)==len(proposals)==len(sends),'complete reference owner proposal partition required')
        for index,(person,proposal,send) in enumerate(zip(people,proposals,sends)):
            require(type(send) is list and len(send)==len(set(send)),'unique reference initial sends required')
            records.append(dict(owner_index=len(records),patient_id=person['patient_id'],kind=kind,
                step_index=step,member_index=index,reference_send=deepcopy(send),
                reference_cardinality=len(send),reference_proposal=deepcopy(proposal)))
    append(members,joint_evidence['baseline_proposals'],row['projection']['current_pair_send'],
        'original_Stoploss_anchor_current_pair',None)
    for step in row['projection']['steps']:
        append(step['members'],step['proposal']['decisions'],[d['send'] for d in step['proposal']['decisions']],
            'frozen_CS_reference_tail_pair',step['step_index'])
    single=row['projection']['singleton']
    if single is not None:
        append([single['member']],[single['proposal']],[single['proposal']['send']],
            'frozen_H_reference_singleton',len(row['projection']['steps']))
    require([base.owner_key(r['patient_id']) for r in records]==
        [base.owner_key(m['patient_id']) for m in members+tail_members],
        'reference omitted, invented or reordered a captured first owner')
    wanted=dict(schema_version=1,rule=MODEL_RULE,source_sha256=deepcopy(REFERENCE_SOURCE_SHA256),scope=MODEL_SCOPE,
        stoploss_actual_anchor_index=anchor,original_JOINT_anchor_feasible=table[anchor]['feasible'],
        original_JOINT_anchor_hospitals=deepcopy(table[anchor]['hospitals']),
        original_Stoploss_proposal_sha256=_source_sha(_stoploss_reference(parent_proposal)),
        calculation_identity=dynamic.BASE_POLICY,
        identity_projection_fields=['joint_context.eligible[].job.policy_name','members_and_tail[].workflow_snapshot.job.policy_name'],
        dynamic_reference_row=row,dynamic_reference_row_sha256=_source_sha(recorded['dynamic_reference_row']),
        owner_references=records,actual_Tailwait_continuation_claim=False,residual_owner_replanning=False)
    precise_same(recorded,wanted,'independently reconstructed one-anchor complete CS reference')
    return wanted,checks


def allocate_reference(reference,*,roster,priority_context,common_prefix_H_proposals):
    """Reconstruct restricted primary coverage and every preserved dispatch edge."""
    eligible,reason=scope_members(roster);require(eligible,'batch allocation outside public scope: '+reason)
    records=reference['owner_references'];n=len(roster)
    require(type(records) is list and len(records)==n and type(common_prefix_H_proposals) is list
        and len(common_prefix_H_proposals)==n,'complete reference and common-prefix H inventories required')
    keys=[base.owner_key(m['patient_id']) for m in roster]
    require(len(set(keys))==n and [base.owner_key(r['patient_id']) for r in records]==keys,
        'complete unique typed reference roster required')
    priorities={base.owner_key(p['patient_id']):p['effective_priority'] for p in priority_context['patients']}
    require(len(priority_context['patients'])==n and set(priorities)==set(keys)
        and all(type(t) is int and t in TIERS for t in priorities.values()),'complete typed public batch priorities required')
    models=[joint.member_model(m['inputs'],p) for m,p in zip(roster,common_prefix_H_proposals)]
    edges=[];hospitals=[];metrics=[]
    for index,(member,record,model) in enumerate(zip(roster,records,models)):
        send=record['reference_send'];pool=[r['hospital'] for r in model['hospitals']]
        require(type(record['owner_index']) is int and record['owner_index']==index
            and type(record['reference_cardinality']) is int and type(send) is list
            and len(set(send))==len(send) and record['reference_cardinality']==len(send)
            and set(send)<=set(pool) and type(member['inputs']['max_new']) is int
            and len(send)<=min(4,member['inputs']['max_new']),
            'original reference membership/cardinality or unchanged contact budget differs')
        require(len(pool)<=PUBLIC_POOL_LIMIT and all(r['residual_wait_seconds']==0
            and r['assumed_workers']==PUBLIC_WORKERS for r in model['hospitals']),
            'empty common-prefix public H queue/pool/nominal workers differs')
        for pool_index,hospital in enumerate(pool):
            if hospital not in send:continue
            if hospital not in hospitals:hospitals.append(hospital)
            value=joint.member_metrics(model,[hospital],{})
            edges.append(dict(owner_index=index,patient_id=member['patient_id'],hospital=hospital,
                pool_index=pool_index,reference_send_index=send.index(hospital),
                total_cost_seconds=value['total_cost_seconds'],metrics=value))
        metrics.append(dict(owner_index=index,patient_id=member['patient_id'],reference_send=deepcopy(send),
            reference_cardinality=len(send),common_prefix_uncontended_metrics=joint.member_metrics(model,send,{})))
    order=sorted(range(n),key=lambda i:(-priorities[keys[i]],i))
    capacities=[dict(hospital=h,capacity=PUBLIC_WORKERS) for h in hospitals]
    coverage=cover_slots(edges,owner_count=n,hospital_capacities=capacities,owner_order=order) if capacities else dict(
        schema_version=1,algorithm='no_preserved_reference_primary_slots',flow=0,required_flow=n,
        complete=False,assignment=[],augmentations=[])
    dispatch=[]
    if coverage['complete']:
        primary={r['owner_index']:r['hospital'] for r in coverage['assignment']}
        def append(index,hospital,phase):
            dispatch.append(dict(dispatch_index=len(dispatch),phase=phase,owner_index=index,
                patient_id=roster[index]['patient_id'],hospital=hospital,
                reference_send_index=records[index]['reference_send'].index(hospital)))
        for index in order:append(index,primary[index],'matched_primary')
        for index,record in enumerate(records):
            for hospital in record['reference_send']:
                if hospital!=primary[index]:append(index,hospital,'remaining_preserved_reference')
        expected={(i,h) for i,r in enumerate(records) for h in r['reference_send']}
        require(len(dispatch)==len(expected) and {(d['owner_index'],d['hospital']) for d in dispatch}==expected,
            'primary/secondary dispatch partition dropped or duplicated an original reference inquiry')
    return dict(schema_version=1,rule=MODEL_RULE,source_sha256=deepcopy(REFERENCE_SOURCE_SHA256),scope=MODEL_SCOPE,
        public_assumed_workers=PUBLIC_WORKERS,slot_capacity_source='frozen_H_config.assumed_workers_for_primary_wave_only',
        common_prefix_H_proposals=deepcopy(common_prefix_H_proposals),owner_order=order,primary_edges=edges,
        coverage=coverage,coverage_complete=coverage['complete'],reference_owner_metrics=metrics,
        dispatch_rule=DISPATCH_RULE,dispatch_order=dispatch,all_reference_queries_preserved=coverage['complete'],
        secondary_queries_may_queue_normally=True,whole_wave_metrics_are_uncontended_reference_only=True,
        old_probability_wait_guards_applicable=False)


def audit_pair(members,context,tails,outputs,evidence,priority_context,owner_context):
    require(type(evidence) is dict and type(evidence.get('original_tailwait_proposal')) is dict
        and set(evidence['original_tailwait_proposal'])=={'decisions','evidence'},
        'intact original Tailwait calculation reference required')
    original=evidence['original_tailwait_proposal']
    checked=foundation.audit_pair([alias_member(m) for m in members],alias_context(context),
        [alias_member(m) for m in tails],original['decisions'],original['evidence'],priority_context,alias_owners(owner_context))
    audit_priority_context(priority_context,context,members,tails)
    roster=members+tails;eligible,reason=scope_members(roster);reference=None;allocation=None;evaluated=False
    names=('batchwave_groups_scope_eligible','batchwave_groups_scope_ineligible','batchwave_groups_evaluated',
        'batchwave_groups_coverage_incomplete','batchwave_groups_copied','batchwave_reference_owners_checked',
        'batchwave_reference_queries_checked','batchwave_public_H_proposals_checked','batchwave_primary_edges_checked',
        'batchwave_augmentations_checked','batchwave_residual_certificates_checked','batchwave_planned_owners_covered',
        'batchwave_actual_members_checked','batchwave_primary_dispatches_checked','batchwave_secondary_dispatches_checked',
        'batchwave_current_pair_send_changes','batchwave_interventions','batchwave_reference_H_searches_checked',
        'batchwave_reference_CS_calls_checked','batchwave_reference_CS_menus_evaluated',
        'batchwave_singleton_H_searches_checked','batchwave_score_members_checked')
    counters=Counter({name:0 for name in names})
    if eligible:
        counters['batchwave_groups_scope_eligible']=1
        reference,reference_checks=audit_reference(evidence['reference'],original,members=members,
            tail_members=tails,joint_context=context,priority_context=priority_context)
        counters.update({key.replace('dynamic_','batchwave_',1):value for key,value in reference_checks.items()})
        allocation=allocate_reference(reference,roster=roster,priority_context=priority_context,
            common_prefix_H_proposals=evidence['allocation']['common_prefix_H_proposals'])
        evaluated=allocation['coverage_complete'];coverage=allocation['coverage']
        counters.update(batchwave_reference_owners_checked=len(roster),
            batchwave_reference_queries_checked=sum(r['reference_cardinality'] for r in reference['owner_references']),
            batchwave_public_H_proposals_checked=len(roster),batchwave_primary_edges_checked=len(allocation['primary_edges']),
            batchwave_augmentations_checked=len(coverage['augmentations']),batchwave_planned_owners_covered=coverage['flow'],
            batchwave_residual_certificates_checked=int(coverage['algorithm']!='no_preserved_reference_primary_slots'))
        counters['batchwave_groups_evaluated' if evaluated else 'batchwave_groups_coverage_incomplete']=1
    else:counters['batchwave_groups_scope_ineligible']=1
    execution=list(range(len(roster))) if evaluated else [0,1]
    if evaluated:
        wanted_outputs=[]
        for record in reference['owner_references']:
            value=deepcopy(record['reference_proposal']);send=deepcopy(record['reference_send'])
            value['send']=send;value['reason']='cohort_batchwave_preserved_reference_first_wave'
            value['evidence']['policy']=NAME;value['evidence']['sent_hospitals']=deepcopy(send)
            for row in value['evidence']['candidate_plan']:row['planned_offset_seconds']=0 if row['hospital'] in send else None
            wanted_outputs.append(value)
        counters.update(batchwave_actual_members_checked=len(roster),batchwave_primary_dispatches_checked=len(roster),
            batchwave_secondary_dispatches_checked=len(allocation['dispatch_order'])-len(roster))
    else:
        wanted_outputs=deepcopy(original['decisions']);counters['batchwave_groups_copied']=1
    changed=[wanted_outputs[i]['send']!=original['decisions'][i]['send'] for i in range(2)]
    for index,value in enumerate(wanted_outputs):
        value['evidence']['policy']=NAME
        value['evidence']['cohort_batchwave']=dict(schema_version=1,
            mode='whole_roster_preserved_reference' if evaluated else 'pair_parent_copy',scope_eligible=eligible,
            evaluated=evaluated,member_index=index,execution_member_indices=execution,
            original_reference_plan_reference_only=evaluated,
            reference_kind=None if not evaluated else reference['owner_references'][index]['kind'],
            original_Tailwait_current_pair_reference_only=evaluated,intervened=evaluated)
    precise_same(outputs,wanted_outputs,'independent preserved-reference whole-cohort outputs')
    wanted=dict(schema_version=1,rule=RULE,base_policy=BASE_POLICY,base_source_sha256=BASE_SOURCE_SHA256,
        reference_source_sha256=deepcopy(REFERENCE_SOURCE_SHA256),joint_context=context,priority_context=priority_context,
        owner_context=owner_context,identity_projection=dict(source_policy=NAME,calculation_policy=BASE_POLICY,
            fields=['joint_context.eligible[].job.policy_name','members[].workflow_snapshot.job.policy_name',
                'tail_members[].workflow_snapshot.job.policy_name','owner_context.inventory[].job.policy_name'],
            copied_only=True,public_identity_unchanged=True,foreign_base_owner_label='__foreign_cohort_tailwait__'),
        original_tailwait_proposal=original,scope_eligible=eligible,scope_reason=reason,reference_evaluated=eligible,
        evaluated=evaluated,batch_evaluated=evaluated,reference=reference,allocation=allocation,
        execution_member_indices=execution,dispatch_order=[] if not evaluated else deepcopy(allocation['dispatch_order']),
        actual_entire_first_roster_commit=evaluated,reference_send_sets_and_counts_preserved=evaluated,
        actual_Tailwait_continuation_claim=False,old_probability_wait_guards_protect_novel_batch=False,
        current_pair_send_changed=changed,intervened=evaluated,scope=SCOPE)
    precise_same(evidence,wanted,'complete independent batchwave evidence')
    counters['batchwave_current_pair_send_changes']=sum(changed);counters['batchwave_interventions']=int(evaluated)
    checked=dict(checked,original_Tailwait_reference_groups_applied=checked['joint_groups_applied'],
        joint_groups_applied=int(evaluated))
    return dict(checked,**dict(counters))


def audit_fallback(inputs,output):
    ev=output['evidence']
    require(ev.get('cohort_batchwave')==dict(schema_version=1,mode='horizon_fallback',
        evaluated=False,intervened=False,original_reference_plan_reference_only=False),
        'late fallback changed batchwave scope')
    require(ev.get('joint_tail')==dict(schema_version=1,mode='horizon_fallback',guard_evaluated=False)
        and ev.get('joint_first')==dict(schema_version=1,mode='horizon_fallback',current_only=False)
        and ev.get('joint_budget')==dict(schema_version=1,mode='horizon_fallback',projection_evaluated=False),
        'late fallback changed original joint scope')
    normalized=deepcopy(output)
    for key in ('cohort_success','cohort_dynamic','cohort_hedge','cohort_recourse','cohort_followplan',
            'cohort_stoploss','cohort_fullmenu','cohort_pareto','cohort_waitpareto','cohort_envelope',
            'cohort_pairscope','cohort_pairshield','cohort_tailwait'):
        require(ev.get(key)==dict(schema_version=1,mode='horizon_fallback',evaluated=False,intervened=False),
            'late fallback changed inherited scope: '+key)
        normalized['evidence'].pop(key)
    for key in ('cohort_batchwave','joint_first','joint_tail','joint_budget'):normalized['evidence'].pop(key)
    normalized['evidence']['policy']=horizon.NAME
    return horizon.audit_horizon(inputs,normalized)


def audit_dispatch_events(group,events,inquiries):
    """Bind global dispatch to actual insertion IDs, not native decision order."""
    roster=group['members']+group['tail_members'];execution=group['execution_member_indices']
    selected=[roster[i] for i in execution];keys={base.owner_key(m['patient_id']) for m in selected}
    sent=[e for e in events if e['type']=='inquiry_sent' and e['id']>group['event_prefix_max']
        and e['at']==group['now'] and base.owner_key(e['patient_id']) in keys]
    require(len(sent)==sum(len(d['send']) for d in group['outputs']),
        'batch send events omit, repeat or add a preserved query')
    by_inquiry={q['id']:q for q in inquiries}
    inquiry_ids=[]
    for event in sent:
        data=parent._data(event);iid=data['inquiry_id']
        require(type(iid) is int and iid in by_inquiry,'actual inquiry send lacks a typed committed inquiry ID')
        row=by_inquiry[iid]
        require(base.owner_key(row['patient_id'])==base.owner_key(event['patient_id'])
            and row['hospital_id']==event['hospital_id'] and row['created_at']==group['now'],
            'batch send event and first inquiry insertion identity/clock differ')
        inquiry_ids.append(iid)
    require(inquiry_ids==sorted(set(inquiry_ids)),'global dispatch is not unique increasing actual inquiry insertion order')
    if group['evidence']['batch_evaluated']:
        expected=group['evidence']['dispatch_order']
        require(type(group['dispatch_inquiry_ids']) is list and all(type(i) is int for i in group['dispatch_inquiry_ids'])
            and group['dispatch_inquiry_ids']==inquiry_ids,'actual batch dispatch insertion-ID capture differs')
        require(group['database_write_phases']==['native_decisions','ordered_queries','native_job_updates'],
            'new batch write-phase declaration differs')
    else:
        expected=[dict(patient_id=m['patient_id'],hospital=h) for m,d in zip(selected,group['outputs']) for h in d['send']]
        require(group['dispatch_inquiry_ids']==[] and group['database_write_phases']==['legacy_native_pair_members'],
            'copied pair changed original write phases or declared batch insertion IDs')
    require(len(expected)==len(sent) and all(base.owner_key(a['patient_id'])==base.owner_key(b['patient_id'])
        and a['hospital']==b['hospital_id'] for a,b in zip(expected,sent)),
        'actual global primary/secondary inquiry order differs from declared preserved-reference dispatch')
    updates=[e for e in events if e['type']=='inquiry_workflow_batch' and e['id']>group['event_prefix_max']
        and e['at']==group['now'] and base.owner_key(e['patient_id']) in keys]
    require(len(updates)==sum(bool(d['send']) for d in group['outputs']),
        'execution lacks one native workflow update event per nonempty actual member')
    nonempty=[(m,d) for m,d in zip(selected,group['outputs']) if d['send']]
    require(all(base.owner_key(event['patient_id'])==base.owner_key(member['patient_id'])
        for event,(member,decision) in zip(updates,nonempty)),'workflow update events differ from native execution order')
    source_jobs={base.owner_key(row['patient_id']):row['job'] for row in group['owner_source_snapshot']['rows']}
    for event,(member,decision) in zip(updates,nonempty):
        value=parent._data(event);job=source_jobs[base.owner_key(member['patient_id'])]
        require(type(job) is dict and type(job['round_no']) is int and job['round_no']>=0,
            'actual workflow update needs its captured full source job round')
        require(value['hospital_ids']==decision['send'] and value['round_no']==job['round_no']+1
            and value['next_evaluation_at']==decision['next_review_at'] and value['policy_name']==NAME
            and value['reason']==decision['reason'],'actual workflow update event differs from one preserved member decision')
    if group['evidence']['batch_evaluated'] and sent and updates:
        require(max(e['id'] for e in sent)<min(e['id'] for e in updates),
            'whole-batch workflow updates occurred before all preserved queries were submitted')
    return dict(batchwave_actual_dispatch_events_checked=len(sent),batchwave_actual_job_update_events_checked=len(updates))


GROUP_KEYS=foundation.GROUP_KEYS|{'execution_mode','execution_member_indices','dispatch_order',
    'dispatch_inquiry_ids','database_write_phases'}
_same=base._same
_audit_phase_order=base._audit_phase_order
time_context_from_prefix=audit_time_context


def audit_execution_identity(group,event_ids):
    roster=group['members']+group['tail_members'];batch=group['evidence']['batch_evaluated']
    execution=list(range(len(roster))) if batch else [0,1]
    require(type(batch) is bool and group['execution_mode']==('batchwave' if batch else 'copied_pair')
        and type(group['execution_member_indices']) is list
        and all(type(i) is int for i in group['execution_member_indices'])
        and group['execution_member_indices']==execution,
        'new whole-roster/copy execution mode or native membership differs')
    require(group['event_prefix_max'] in event_ids and type(group['db_decision_ids']) is list
        and all(type(d) is int for d in group['db_decision_ids'])
        and group['db_decision_ids']==list(range(group['decision_prefix_max']+1,
            group['decision_prefix_max']+len(execution)+1)),
        'actual batch decisions not the next N native records at common prefix')
    exact_copy(group['dispatch_order'],group['evidence']['dispatch_order'],'raw/evidence dispatch order')
    require(group['execution_member_indices']==group['evidence']['execution_member_indices'],
        'raw/evidence execution membership differs')
    return roster,execution


def audit_linked_call(group,call,decision,previous):
    index=call['joint_member_index'];execution=group['execution_member_indices']
    require(type(index) is int and index in execution,'unknown typed native batch member index')
    position=execution.index(index);roster=group['members']+group['tail_members'];member=roster[index]
    require(group['now']==EPOCH+call['at'] and call['db_event_prefix_max']==group['event_prefix_max']
        and decision['id']==group['db_decision_ids'][position]
        and previous==group['decision_prefix_max']+position,
        'batch records were interleaved or used different common prefixes')
    _same(member,dict(patient_id=call['patient_id'],inputs=call['inputs'],workflow_snapshot=call['workflow_snapshot']),
        'linked native batch member')
    exact_copy(call['output'],group['outputs'][position],'linked native batch output')
    return member,group['decision_prefix_max']


def _audit_calls(scenario,branch):
    events,calls,decisions=branch['db_events'],branch['policy_calls'],branch['db_decisions']
    groups=branch['joint_groups'];specs={p['id']:p for p in scenario['patients']}
    require(type(events) is list and type(calls) is list and type(decisions) is list
        and len(calls)==len(decisions) and type(groups) is list,'complete call/decision/batch inventory differs')
    _audit_phase_order(calls)
    require(all(type(e['id']) is int and e['id']>0 for e in events)
        and [e['id'] for e in events]==sorted({e['id'] for e in events}),'event IDs not unique and ordered')
    event_ids={e['id'] for e in events};sent=[e for e in events if e['type']=='inquiry_sent']
    group_map={};checks=Counter();expected_calls=Counter()
    for index,group in enumerate(groups,1):
        require(type(group) is dict and set(group)==GROUP_KEYS and type(group['id']) is int
            and group['id']==index,'unknown, missing or reordered batch-group fields')
        for key in ('now','event_prefix_max','decision_prefix_max','revision'):_integer(group[key],'batch '+key)
        require(type(group['validated_before_write']) is bool and group['validated_before_write'],
            'batch lacks all-owner validation before first write')
        wanted=audit_registry(events,decisions,group['registry_snapshot'],group['now'],
            group['event_prefix_max'],group['decision_prefix_max'])
        _same(group['context'],wanted,'complete current public eligibility')
        require(len(wanted['pair_patient_ids'])==2,'group created without original current reference pair')
        roster,execution=audit_execution_identity(group,event_ids)
        checks.update(audit_pair(group['members'],wanted,group['tail_members'],group['outputs'],
            group['evidence'],group['priority_context'],group['owner_context']))
        count,history=audit_owner_provenance(scenario,events,group['registry_snapshot'],
            group['owner_context'],group['owner_source_snapshot'])
        checks['owner_public_provenance_checked']+=count;checks['owner_full_source_profiles_checked']+=count
        checks['historical_source_profiles_checked']+=history['historical_owners_verified']
        audit_priority_context(group['priority_context'],wanted,group['members'],group['tail_members'],lab_defaults=True)
        for tail in group['tail_members']:
            checks['tail_public_provenance_checked']+=audit_member_provenance(scenario,events,tail,
                group['event_prefix_max'],group['decision_prefix_max'])
        for member in roster:
            require(member['workflow_snapshot']['apply_snapshot']['revision']==group['revision'],
                'member source was not captured at common planning revision')
        checks.update(audit_dispatch_events(group,events,branch['db_inquiries']))
        group_map[group['id']]=group
        expected_calls.update({(group['id'],i):1 for i in execution})
    declared,actual,group_calls=Counter(),Counter(),Counter();previous=0;last_prefix=0
    for call,decision in zip(calls,decisions):
        grouped='joint_group_id' in call
        require(type(call) is dict and set(call)==(CALL_KEYS|{'joint_group_id','joint_member_index'} if grouped else CALL_KEYS),
            'unknown or missing committed policy-call fields')
        pid,at,inputs,output=call['patient_id'],call['at'],call['inputs'],call['output']
        prefix=call['db_event_prefix_max'];now=EPOCH+at
        require(pid in specs and type(at) is int and specs[pid]['at']<=at<specs[pid]['at']+300,
            'policy used future or out-of-window patient')
        require(type(prefix) is int and prefix>=last_prefix and prefix in event_ids,
            'invalid or reordered committed event prefix');last_prefix=prefix
        require(type(inputs) is dict and set(inputs)==INPUT_KEYS and not horizon._contains_private(inputs)
            and inputs['now']==now,'private, future or unknown policy input')
        require(call['name']==branch['policy']==decision['policy_name']==NAME and decision['patient_id']==pid
            and decision['at']==now and type(decision['id']) is int and decision['id']==call['db_decision_id']>previous,
            'committed policy identity/clock differs')
        for key,field in (('send','hospital_ids'),('reason','reason'),('evidence','evidence')):
            exact_copy(output[key],decision[field],'committed '+key)
        if grouped:
            gid,index=call['joint_group_id'],call['joint_member_index']
            require(type(gid) is int and gid in group_map and type(index) is int
                and index in group_map[gid]['execution_member_indices'],'unknown batch ID or typed native member index')
            group=group_map[gid];member,dprefix=audit_linked_call(group,call,decision,previous)
            group_calls[gid,index]+=1
        else:
            member=dict(patient_id=pid,inputs=inputs,workflow_snapshot=call['workflow_snapshot']);dprefix=previous
            checks.update(audit_fallback(inputs,output))
        context=time_context_from_prefix(events,inputs,member,prefix,dprefix,decision)
        require(context['evaluation_end_at']==EPOCH+specs[pid]['at']+300,
            'public H differs from registered patient observation interval');previous=decision['id']
        history,pending,own,_=horizon._ledger(events,now,pid,prefix);travel=specs[pid]['travel_seconds']
        expected_own=[dict(row,travel_seconds=travel[row['hospital']]) for row in own if row['hospital'] in travel]
        require(inputs['observations']==history and inputs['pending']==pending and inputs['patient_pending']==expected_own,
            'common-prefix public observation ledger differs')
        earlier=[e for e in sent if e['id']<=prefix and e['patient_id']==pid];contacted={e['hospital_id'] for e in earlier}
        require(type(inputs['max_new']) is int and inputs['max_new']==10-len(contacted)
            and inputs['last_contact_at']==max((e['at'] for e in earlier if e['at']<=now),default=None),
            'original contact budget/history differs')
        ranked=horizon._ranked(inputs['candidates'])
        require({r['hospital'] for r in ranked}==set(travel)-contacted
            and all(r['travel_seconds']==travel[r['hospital']] for r in ranked),
            'currently eligible uncontacted offers/travel differ from frozen lab')
        require([r['hospital'] for r in ranked]==sorted(set(travel)-contacted,key=lambda h:(travel[h],h))
            and all(c.get('resources_enough') is True for c in inputs['candidates']),
            'actual candidate order/resource eligibility differs from frozen public lab construction')
        require(type(output['send']) is list and len(set(output['send']))==len(output['send'])
            and len(output['send'])<=inputs['max_new'] and set(output['send'])<=set(travel)-contacted
            and sum(now-r['submitted_at']<=180 for r in expected_own)+len(output['send'])<=4,
            'actual query count/candidate or original EAT concurrency constraint differs')
        for hospital in output['send']:
            matches=[e for e in sent if e['patient_id']==pid and e['hospital_id']==hospital and e['at']==now and e['id']>prefix]
            require(len(matches)==1,'decision lacks a unique subsequent inquiry send');declared[matches[0]['id']]+=1
    require(group_calls==expected_calls,'actual N batch/copy members and committed calls not covered exactly once')
    for event in sent:
        if event['patient_id'] in specs:actual[event['id']]+=1
    require(declared==actual,'actual inquiries not covered exactly once by committed policy calls')
    return dict(policy_calls=len(calls),**dict(checks))



def _embedded_reference_assertions():
    return dict(
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


def audit_branch(scenario,branch):
    if branch.get('policy')!=NAME:
        view=dict(schema_version=1,scenario=scenario,arms=[[branch['policy'],branch['rule']]],branches=[branch],
            environment_sha256=branch['environment_sha256'],initial_snapshot_sha256=branch['snapshot_sha256'])
        return control_audit.audit_replay(view)['branch_audits'][0]
    try:
        require(branch['environment_sha256']==_digest(scenario) and branch['rule']=='immediate',
            'bound public environment or confirmation rule differs')
        exact_copy(branch['instrumentation'],NEW_INSTRUMENTATION,'new batch adapter source contract')
        checks=_audit_calls(scenario,branch);accounting=horizon._audit_trace_fixed(scenario,branch)
        total=sum(1800 if p['failed'] else p['estimated_arrival_seconds'] for p in branch['patients'])
        return dict(passed=True,**checks,**accounting,total_arrival_failure_cost_seconds=total,
            mean_arrival_failure_cost_seconds=total/len(branch['patients']) if branch['patients'] else None,
            embedded_Tailwait_reference_assertions=_embedded_reference_assertions(),
            parent_guard_flags_scope='copied paths or embedded Tailwait reference only; not new batch path',
            original_Tailwait_proposal_independently_checked=True,
            single_Stoploss_anchor_CS_reference_independently_rebuilt=True,
            reference_tail_CS_is_not_actual_Tailwait_continuation=True,
            all_reference_send_memberships_and_counts_independently_checked=True,
            actual_entire_first_roster_commit_independently_checked=True,
            current_pair_only_execution_checked=False,current_batch_path_original_JOINT_domain=False,
            current_batch_path_original_probability_wait_guards=False,
            whole_cohort_preserved_reference_batch_independently_checked=True,
            native_query_submission_order_preserved=False,actual_hospital_FIFO_unchanged=True,
            native_N_decisions_and_common_prefix_calls_independently_checked=True,
            primary_then_native_secondary_inquiry_ID_order_independently_checked=True,
            public_H_assumed_workers_not_latent_staffing=True,
            primary_wave_budget_only_secondary_may_queue=True,
            mincost_augmentation_and_reduced_edge_certificate_checked=True,
            exact_minimum_cost_claimed=False,certificate_tolerance_is_per_residual_edge=True,
            actual_outputs_sends_and_all_patient_FIFO_checked=True,
            integer_occupancy_and_contact_budget_independently_checked=True,
            captured_waiting_owner_provenance_and_stale_checks_retained=True,
            physical_transaction_boundary_from_raw_proved=False,
            atomicity_evidence_scope='linked common-prefix N records and inquiry events plus captured pre-write validation; physical rollback/one-update guarantees require the frozen new adapter and its synthetic failure tests',
            realized_success_wait_arrival_guaranteed=False,
            scope='all native batch outputs, source prefixes, original reference query memberships/counts, caps and actual FIFO checked; parent guards protect only copies or embedded reference; whole same-start empty-queue roster is actually committed under a deliberately new inquiry submission order; no realized outcome guarantee')
    except (KeyError,TypeError,IndexError,AttributeError,OverflowError,StopIteration) as error:
        raise base.CohortHedgeAuditError('malformed saved cohort-batchwave artifact') from error


def audit_replay(replay):
    require(type(replay) is dict and replay['schema_version']==1 and type(replay['branches']) is list,
        'invalid complete replay')
    require(replay['arms']==[[b['policy'],b['rule']] for b in replay['branches']]
        and len({tuple(a) for a in replay['arms']})==len(replay['arms'])>0,'complete arm inventory differs')
    require(all(b['environment_sha256']==replay['environment_sha256']
        and b['snapshot_sha256']==replay['initial_snapshot_sha256'] for b in replay['branches']),
        'paired initial environment differs')
    if not any(b.get('policy')==NAME for b in replay['branches']):return control_audit.audit_replay(replay)
    controls=[b for b in replay['branches'] if b.get('policy')!=NAME];control_proof=None;by_policy={}
    if controls:
        view=dict(replay,branches=controls,arms=[[b['policy'],b['rule']] for b in controls])
        control_proof=control_audit.audit_replay(view)
        by_policy={(b['policy'],b['rule']):proof for b,proof in zip(controls,control_proof['branch_audits'])}
    checked=[audit_branch(replay['scenario'],branch) if branch['policy']==NAME
        else by_policy[branch['policy'],branch['rule']] for branch in replay['branches']]
    return dict(passed=True,branches=len(checked),branch_audits=checked,new_executions=0,
        preserved_parent_audit_proof=control_proof)
