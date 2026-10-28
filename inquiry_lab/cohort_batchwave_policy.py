"""Return an actual whole-roster batch preserving one frozen reference wave."""
from copy import deepcopy
import hashlib
from pathlib import Path
from . import cohort_tailwait_policy as original,cohort_batchwave_context as context,cohort_batchwave_model as model

NAME='eat_cohort_batchwave_l15_f'
BASE_POLICY='eat_cohort_tailwait_l15_f'
BASE_SOURCE_SHA256='2fd6914a26c05651a79569395b30c8281881d5a72edbb11b08b85f33caa3a4a5'
if hashlib.sha256(Path(original.__file__).read_bytes()).hexdigest()!=BASE_SOURCE_SHA256:raise ValueError('frozen batch-wave Tailwait foundation differs')
HORIZON=original.HORIZON
JOINT=original.JOINT
RULE='whole_first_roster_batch_preserving_Stoploss_anchor_frozen_CS_reference_query_sets'
SCOPE='empty common queue and same-start first roster4..16; one original Stoploss-anchor Dynamic/CS reference, all its query memberships/counts preserved; primary match changes actual whole-roster submission order, no actual Tailwait continuation or old P/W-domain guarantee; all other paths copied accepted Tailwait'


def spec(name=NAME):
    if name!=NAME:raise ValueError('the fixed batch-wave candidate is '+NAME)
    value=original.spec(BASE_POLICY);value.update(name=NAME,label='EAT l15 f, preserved-reference whole first-wave batch')
    return value


def decide(name,*,now,candidates,observations=(),pending=(),patient_pending=(),max_new,last_contact_at=None,time_context):
    spec(name);value=original.decide(BASE_POLICY,now=now,candidates=candidates,observations=observations,pending=pending,
        patient_pending=patient_pending,max_new=max_new,last_contact_at=last_contact_at,time_context=time_context)
    value['evidence']['policy']=NAME;value['evidence']['cohort_batchwave']=dict(schema_version=1,mode='horizon_fallback',evaluated=False,intervened=False,original_reference_plan_reference_only=False)
    return value


def _decision(reference):
    value=deepcopy(reference['reference_proposal']);send=deepcopy(reference['reference_send'])
    value['send']=send;value['reason']='cohort_batchwave_preserved_reference_first_wave'
    value['evidence']['policy']=NAME;value['evidence']['sent_hospitals']=deepcopy(send)
    for row in value['evidence']['candidate_plan']:row['planned_offset_seconds']=0 if row['hospital'] in send else None
    return value


def decide_joint(name,*,members,joint_context,tail_members,priority_context,owner_context):
    spec(name);native=context.validate_context(joint_context)
    context.validate_tail_members(native,tail_members,members=members)
    priority=context.validate_priority_context(priority_context,joint_context=native,members=members,tail_members=tail_members)
    owners=context.validate_owner_context(owner_context,joint_context=native,members=members,tail_members=tail_members,priority_context=priority)
    proposal=original.decide_joint(BASE_POLICY,members=context.base_members_projection(members),joint_context=context.base_context_projection(native),
        tail_members=context.base_members_projection(tail_members),priority_context=priority,owner_context=context.base_owner_projection(owners))
    roster=members+tail_members;eligible,reason=model.scope_members(roster);reference=None;allocation=None;evaluated=False
    if eligible:
        reference=model.build_batch_reference(proposal,members=members,tail_members=tail_members,joint_context=native,priority_context=priority)
        allocation=model.allocate_reference(reference,roster=roster,priority_context=priority);evaluated=allocation['coverage_complete']
    if evaluated:decisions=[_decision(r) for r in reference['owner_references']]
    else:decisions=deepcopy(proposal['decisions'])
    execution=list(range(len(roster))) if evaluated else [0,1]
    current_changed=[decisions[i]['send']!=proposal['decisions'][i]['send'] for i in range(2)]
    for i,d in enumerate(decisions):
        d['evidence']['policy']=NAME;d['evidence']['cohort_batchwave']=dict(schema_version=1,
            mode='whole_roster_preserved_reference' if evaluated else 'pair_parent_copy',scope_eligible=eligible,evaluated=evaluated,
            member_index=i,execution_member_indices=execution,original_reference_plan_reference_only=evaluated,
            reference_kind=None if not evaluated else reference['owner_references'][i]['kind'],
            original_Tailwait_current_pair_reference_only=evaluated,intervened=evaluated)
    dispatch=[] if not evaluated else deepcopy(allocation['dispatch_order'])
    evidence=dict(schema_version=1,rule=RULE,base_policy=BASE_POLICY,base_source_sha256=BASE_SOURCE_SHA256,
        reference_source_sha256=deepcopy(model.SOURCE_SHA256),joint_context=native,priority_context=priority,owner_context=owners,
        identity_projection=dict(source_policy=NAME,calculation_policy=BASE_POLICY,
            fields=['joint_context.eligible[].job.policy_name','members[].workflow_snapshot.job.policy_name','tail_members[].workflow_snapshot.job.policy_name','owner_context.inventory[].job.policy_name'],
            copied_only=True,public_identity_unchanged=True,foreign_base_owner_label='__foreign_cohort_tailwait__'),
        original_tailwait_proposal=proposal,scope_eligible=eligible,scope_reason=reason,reference_evaluated=eligible,
        evaluated=evaluated,batch_evaluated=evaluated,reference=reference,allocation=allocation,execution_member_indices=execution,
        dispatch_order=dispatch,actual_entire_first_roster_commit=evaluated,reference_send_sets_and_counts_preserved=evaluated,
        actual_Tailwait_continuation_claim=False,old_probability_wait_guards_protect_novel_batch=False,
        current_pair_send_changed=current_changed,intervened=evaluated,scope=SCOPE)
    return dict(decisions=decisions,evidence=evidence,execution_member_indices=execution,dispatch_order=dispatch)
