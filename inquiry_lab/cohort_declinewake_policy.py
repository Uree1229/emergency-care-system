"""Accepted Tailwait mathematics with an explicit public-decline extra review."""
from copy import deepcopy
import hashlib
from pathlib import Path
from . import cohort_tailwait_policy as original,cohort_declinewake_context as context

NAME='eat_cohort_declinewake_l15_f'
BASE_POLICY=original.NAME
BASE_SOURCE_SHA256='2fd6914a26c05651a79569395b30c8281881d5a72edbb11b08b85f33caa3a4a5'
if hashlib.sha256(Path(original.__file__).read_bytes()).hexdigest()!=BASE_SOURCE_SHA256:raise ValueError('frozen decline-wake Tailwait foundation differs')
HORIZON=original.HORIZON
JOINT=original.JOINT
RULE='extra_review_after_unconsumed_public_own_decline_without_postponing_existing_review'
SCOPE='unchanged Tailwait first groups and H mathematics; only current public own terminal-decline wake may insert an earlier fallback review; actual review remains no later than its existing schedule; no actual success/wait improvement guarantee'


def spec(name=NAME):
    if name!=NAME:raise ValueError('the fixed decline-wake candidate is '+NAME)
    value=original.spec(BASE_POLICY);value.update(name=NAME,label='EAT l15 f, public terminal-decline wake')
    return value


def decide(name,*,now,candidates,observations=(),pending=(),patient_pending=(),max_new,last_contact_at=None,time_context,wake_context=None):
    spec(name)
    parent=original.decide(BASE_POLICY,now=now,candidates=candidates,observations=observations,pending=pending,
        patient_pending=patient_pending,max_new=max_new,last_contact_at=last_contact_at,time_context=time_context)
    wake=None if wake_context is None else context.validate_wake_context(wake_context,now=now,patient_id=time_context['patient_id'])
    value=deepcopy(parent)
    if wake is not None:
        if wake['capture_snapshot']['job_version']!=time_context['job_version'] or wake['event_prefix_max']!=time_context['event_prefix_max'] or wake['decision_prefix_max']!=time_context['decision_prefix_max']:
            raise ValueError('wake and original H public application prefixes differ')
        value['next_review_at']=min(wake['captured_job']['next_evaluation_at'],parent['next_review_at'])
    value['evidence']['policy']=NAME
    value['evidence']['cohort_declinewake']=dict(schema_version=1,mode='horizon_fallback',rule=RULE,base_source_sha256=BASE_SOURCE_SHA256,
        triggered=wake is not None,wake_context=wake,original_tailwait_proposal=parent,
        parent_next_review_at=parent['next_review_at'],actual_next_review_at=value['next_review_at'],
        sent_hospitals_unchanged=value['send']==parent['send'],model_or_objective_changed=False,
        next_review_clipped=value['next_review_at']!=parent['next_review_at'],scope=SCOPE)
    return value


def decide_joint(name,*,members,joint_context,tail_members,priority_context,owner_context):
    spec(name);native=context.validate_context(joint_context)
    context.validate_tail_members(native,tail_members,members=members)
    priority=context.validate_priority_context(priority_context,joint_context=native,members=members,tail_members=tail_members)
    owners=context.validate_owner_context(owner_context,joint_context=native,members=members,tail_members=tail_members,priority_context=priority)
    parent=original.decide_joint(BASE_POLICY,members=context.base_members_projection(members),joint_context=context.base_context_projection(native),
        tail_members=context.base_members_projection(tail_members),priority_context=priority,owner_context=context.base_owner_projection(owners))
    decisions=deepcopy(parent['decisions'])
    for index,value in enumerate(decisions):
        value['evidence']['policy']=NAME
        value['evidence']['cohort_declinewake']=dict(schema_version=1,mode='pair_copied_tailwait',triggered=False,member_index=index,model_or_objective_changed=False)
    return dict(decisions=decisions,evidence=dict(schema_version=1,rule=RULE,base_source_sha256=BASE_SOURCE_SHA256,
        joint_context=native,priority_context=priority,owner_context=owners,original_tailwait_proposal=parent,
        identity_projection=dict(source_policy=NAME,calculation_policy=BASE_POLICY,copied_only=True,
            fields=['joint_context.eligible[].job.policy_name','members[].workflow_snapshot.job.policy_name',
                'tail_members[].workflow_snapshot.job.policy_name','owner_context.inventory[].job.policy_name'],
            public_identity_unchanged=True,foreign_base_owner_label='__foreign_cohort_tailwait__'),
        triggered=False,copied_tailwait_group=True,scope=SCOPE))
