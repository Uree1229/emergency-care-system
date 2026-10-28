"""Execute only the first two subsets of a public initial slot allocation."""
from copy import deepcopy
import hashlib
from pathlib import Path
from . import cohort_tailwait_policy as original,cohort_startslots_context as context,cohort_startslots_model as model

NAME='eat_cohort_startslots_l15_f'
BASE_POLICY='eat_cohort_tailwait_l15_f'
BASE_SOURCE_SHA256='2fd6914a26c05651a79569395b30c8281881d5a72edbb11b08b85f33caa3a4a5'
if hashlib.sha256(Path(original.__file__).read_bytes()).hexdigest()!=BASE_SOURCE_SHA256:raise ValueError('frozen start-slot Tailwait parent differs')
HORIZON=original.HORIZON
JOINT=original.JOINT
RULE='simultaneous_empty_queue_first_roster_initial_review_slot_matching'
SCOPE='only 4..16 captured first owners sharing a workflow start and empty public queue; Tailwait retained as calculation reference, not a probability/wait floor for changed cardinalities; only current native first pair committed in unchanged FIFO order; later groups re-evaluate normally with no reservations; no actual success/wait/arrival guarantee'


def spec(name=NAME):
    if name!=NAME:raise ValueError('the fixed start-slot candidate is '+NAME)
    value=original.spec(BASE_POLICY);value.update(name=NAME,label='EAT l15 f, initial public review-slot matching')
    return value


def decide(name,*,now,candidates,observations=(),pending=(),patient_pending=(),max_new,last_contact_at=None,time_context):
    spec(name)
    value=original.decide(BASE_POLICY,now=now,candidates=candidates,observations=observations,pending=pending,
        patient_pending=patient_pending,max_new=max_new,last_contact_at=last_contact_at,time_context=time_context)
    value['evidence']['policy']=NAME;value['evidence']['cohort_startslots']=dict(schema_version=1,mode='horizon_fallback',evaluated=False,intervened=False,original_H_plan_reference_only=False)
    return value


def _decision(proposal,hospitals,metrics):
    value=deepcopy(proposal);value['send']=list(hospitals);value['reason']='cohort_startslots_current_pair_only'
    value['evidence']['policy']=NAME;value['evidence']['sent_hospitals']=list(hospitals)
    value['evidence']['planned_expected_cost']=round(metrics['total_cost_seconds'],3)
    for row in value['evidence']['candidate_plan']:row['planned_offset_seconds']=0 if row['hospital'] in hospitals else None
    return value


def decide_joint(name,*,members,joint_context,tail_members,priority_context,owner_context):
    spec(name);native=context.validate_context(joint_context)
    context.validate_tail_members(native,tail_members,members=members)
    priority=context.validate_priority_context(priority_context,joint_context=native,members=members,tail_members=tail_members)
    owners=context.validate_owner_context(owner_context,joint_context=native,members=members,tail_members=tail_members,priority_context=priority)
    proposal=original.decide_joint(BASE_POLICY,members=context.base_members_projection(members),joint_context=context.base_context_projection(native),
        tail_members=context.base_members_projection(tail_members),priority_context=priority,owner_context=context.base_owner_projection(owners))
    roster=members+tail_members;eligible,reason=model.scope_members(roster);allocation=None;baselines=[];evaluated=False
    if eligible:
        baselines=[HORIZON.decide(HORIZON.NAME,**deepcopy(m['inputs'])) for m in roster]
        allocation=model.allocate_slots(roster,baseline_proposals=baselines,priority_context=priority)
        evaluated=allocation['coverage_complete']
    if evaluated:
        decisions=[_decision(p,allocation['planned_sends'][i],allocation['final_owner_metrics'][i]['metrics']) for i,p in enumerate(baselines[:2])]
    else:decisions=deepcopy(proposal['decisions'])
    changed=[d['send']!=p['send'] for d,p in zip(decisions,proposal['decisions'])];intervened=any(changed)
    for i,d in enumerate(decisions):
        d['evidence']['policy']=NAME;d['evidence']['cohort_startslots']=dict(schema_version=1,mode='pair_cohort_startslots',
            scope_eligible=eligible,evaluated=evaluated,intervened=intervened,member_index=i,send_changed=changed[i],
            original_H_plan_reference_only=evaluated,
            original_Tailwait_send=deepcopy(proposal['decisions'][i]['send']),planned_current_send=deepcopy(d['send']))
    evidence=dict(schema_version=1,rule=RULE,base_policy=BASE_POLICY,base_source_sha256=BASE_SOURCE_SHA256,
        matching_source_sha256=deepcopy(model.SOURCE_SHA256),joint_context=native,priority_context=priority,owner_context=owners,
        identity_projection=dict(source_policy=NAME,calculation_policy=BASE_POLICY,
            fields=['joint_context.eligible[].job.policy_name','members[].workflow_snapshot.job.policy_name','tail_members[].workflow_snapshot.job.policy_name','owner_context.inventory[].job.policy_name'],
            copied_only=True,public_identity_unchanged=True,foreign_base_owner_label='__foreign_cohort_tailwait__'),
        original_tailwait_proposal=proposal,scope_eligible=eligible,scope_reason=reason,evaluated=evaluated,allocation=allocation,
        public_H_baseline_proposals=baselines,current_pair_only_execution=True,future_assignments_committed=False,
        old_probability_wait_guards_protect_novel_counts=False,intervened=intervened,scope=SCOPE)
    return dict(decisions=decisions,evidence=evidence)
