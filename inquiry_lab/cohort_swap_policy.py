"""Accepted Tailwait with one guarded, current-owner H offset exchange."""
from copy import deepcopy
import hashlib
import math
from pathlib import Path
from server import inquiry_policies
from . import cohort_tailwait_policy as original, cohort_swap_context as context, cohort_swap_model as model

NAME = 'eat_cohort_swap_l15_f'
BASE_POLICY = original.NAME
BASE_SOURCE_SHA256 = '2fd6914a26c05651a79569395b30c8281881d5a72edbb11b08b85f33caa3a4a5'
MODEL_SOURCE_SHA256 = '471c57d532ca4c635f3bf5555b12295a39ffbd098f27023b5b5bb891ac4429bb'
if hashlib.sha256(Path(original.__file__).read_bytes()).hexdigest() != BASE_SOURCE_SHA256:
    raise ValueError('frozen swap Tailwait foundation differs')
if hashlib.sha256(Path(model.__file__).read_bytes()).hexdigest() != MODEL_SOURCE_SHA256:
    raise ValueError('frozen swap scoring model differs')
SOURCE_SHA256 = dict(tailwait_policy=BASE_SOURCE_SHA256, horizon_policy=model.H_SOURCE_SHA256, swap_model=MODEL_SOURCE_SHA256)
HORIZON = original.HORIZON
JOINT = original.JOINT
RULE = 'contacted_pending_owner_single_atomic_H_offset_exchange'
SCOPE = 'Tailwait first-contact joint and singleton paths copied; only contacted own-pending fallback evaluated; current-owner P/W/J/Q forecast guards, not other-owner or actual-outcome guarantees; no future-cap correction or new observations'


def spec(name=NAME):
    if name != NAME: raise ValueError('the fixed swap candidate is '+NAME)
    value = original.spec(BASE_POLICY)
    value.update(name=NAME, label='EAT l15 f, one guarded H offset exchange in follow-up')
    return value


def valid_last_contact(value, now):
    return type(value) in (int,float) and math.isfinite(value) and value <= now


def decide(name, *, now, candidates, observations=(), pending=(), patient_pending=(), max_new,
           last_contact_at=None, time_context):
    spec(name)
    inputs = dict(now=now, candidates=deepcopy(list(candidates)), observations=deepcopy(list(observations)),
        pending=deepcopy(list(pending)), patient_pending=deepcopy(list(patient_pending)), max_new=max_new,
        last_contact_at=last_contact_at, time_context=deepcopy(time_context))
    parent = original.decide(BASE_POLICY, **inputs)
    own = inquiry_policies._pending(inputs['patient_pending'], now, own=True)
    evaluated = valid_last_contact(last_contact_at, now) and bool(own)
    projection = model.evaluate(inputs, parent) if evaluated else None
    selected = None if projection is None else projection['selection']['selected_trial_index']
    value = deepcopy(parent)
    if selected is not None:
        plan = projection['selected_plan']
        value['send'] = [h for h, offset in plan.items() if offset == 0]
        value['evidence']['sent_hospitals'] = list(value['send'])
        for row in value['evidence']['candidate_plan']:
            row['planned_offset_seconds'] = plan[row['hospital']]
        value['evidence']['planned_expected_cost'] = round(projection['selected_metrics']['total_cost_seconds'],3)
    value['evidence']['policy'] = NAME
    value['evidence']['cohort_swap'] = dict(schema_version=1, mode='horizon_fallback', rule=RULE,
        base_source_sha256=BASE_SOURCE_SHA256, model_horizon_source_sha256=model.H_SOURCE_SHA256,
        source_sha256=deepcopy(SOURCE_SHA256),
        evaluated=evaluated, normalized_own_pending_count=len(own), contacted_owner=valid_last_contact(last_contact_at,now),
        original_tailwait_proposal=parent, swap_projection=projection, selected_trial_index=selected,
        send_changed=value['send'] != parent['send'],
        plan_changed=value['evidence']['candidate_plan'] != parent['evidence']['candidate_plan'],
        next_review_changed=False, scope=SCOPE)
    return value


def decide_joint(name, *, members, joint_context, tail_members, priority_context, owner_context):
    spec(name); native=context.validate_context(joint_context)
    context.validate_tail_members(native,tail_members,members=members)
    priority=context.validate_priority_context(priority_context,joint_context=native,members=members,tail_members=tail_members)
    owners=context.validate_owner_context(owner_context,joint_context=native,members=members,tail_members=tail_members,priority_context=priority)
    parent=original.decide_joint(BASE_POLICY,members=context.base_members_projection(members),joint_context=context.base_context_projection(native),
        tail_members=context.base_members_projection(tail_members),priority_context=priority,owner_context=context.base_owner_projection(owners))
    decisions=deepcopy(parent['decisions'])
    for index,decision in enumerate(decisions):
        decision['evidence']['policy']=NAME
        decision['evidence']['cohort_swap']=dict(schema_version=1,mode='pair_copied_tailwait',evaluated=False,
            member_index=index,send_changed=False,plan_changed=False,next_review_changed=False)
    evidence=dict(schema_version=1,rule=RULE,base_source_sha256=BASE_SOURCE_SHA256,model_horizon_source_sha256=model.H_SOURCE_SHA256,
        source_sha256=deepcopy(SOURCE_SHA256),
        joint_context=native,priority_context=priority,owner_context=owners,original_tailwait_proposal=parent,evaluated=False,copied_tailwait_group=True,
        identity_projection=dict(source_policy=NAME,calculation_policy=BASE_POLICY,copied_only=True,
            fields=['joint_context.eligible[].job.policy_name','members[].workflow_snapshot.job.policy_name',
                'tail_members[].workflow_snapshot.job.policy_name','owner_context.inventory[].job.policy_name'],
            public_identity_unchanged=True,foreign_base_owner_label='__foreign_cohort_tailwait__'),scope=SCOPE)
    return dict(decisions=decisions,evidence=evidence)
