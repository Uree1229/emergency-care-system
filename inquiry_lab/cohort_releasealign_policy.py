"""Advance a full own-contact review only at its known age-limit release."""
from copy import deepcopy
import hashlib
import math
from pathlib import Path

from server import inquiry_policies
from . import cohort_tailwait_policy as original, cohort_releasealign_context as context

NAME = 'eat_cohort_releasealign_l15_f'
BASE_POLICY = original.NAME
BASE_SOURCE_SHA256 = '2fd6914a26c05651a79569395b30c8281881d5a72edbb11b08b85f33caa3a4a5'
if hashlib.sha256(Path(original.__file__).read_bytes()).hexdigest() != BASE_SOURCE_SHA256:
    raise ValueError('frozen release-align Tailwait foundation differs')
HORIZON, JOINT = original.HORIZON, original.JOINT
MAX_CONCURRENT, TIMEOUT = 4, 180
RULE = 'align_full_own_contact_slot_review_to_first_public_inclusive_timeout_release'
SCOPE = 'only contacted ungrouped fallback with exactly four normalized fresh own inquiries and empty original H send; advance only the next review to the first known integer age>180 before A/H; original send, plan, metrics, pending CDF and first Tailwait groups preserved; real replies may arrive earlier; no outcome guarantee'


def spec(name=NAME):
    if name != NAME:
        raise ValueError('the fixed release-align candidate is ' + NAME)
    value = original.spec(BASE_POLICY)
    value.update(name=NAME, label='EAT l15 f, known own-contact release review alignment')
    return value


def timing_hint(inputs, parent):
    now = inputs['now']
    own = inquiry_policies._pending(inputs['patient_pending'], now, own=True)
    fresh = [q for q in own if now-q['submitted_at'] <= TIMEOUT]
    contact = inputs['last_contact_at']
    contacted = type(contact) in (int, float) and math.isfinite(contact) and contact <= now
    earliest = min((math.floor(q['submitted_at']+TIMEOUT)+1 for q in fresh), default=None)
    cutoff = min(inputs['time_context']['auto_inquiry_deadline_at'], inputs['time_context']['evaluation_end_at'])
    evaluated = bool(contacted and len(fresh) == MAX_CONCURRENT and not parent['send']
        and inputs['candidates'] and inputs['max_new'] > 0)
    selected = parent['next_review_at']
    if evaluated and now < earliest < selected and earliest < cutoff:
        selected = earliest
    return dict(schema_version=1, mode='horizon_fallback', rule=RULE,
        base_source_sha256=BASE_SOURCE_SHA256, evaluated=evaluated,
        intervened=selected != parent['next_review_at'], contacted_owner=contacted,
        normalized_own_pending_count=len(own), fresh_own_pending_count=len(fresh),
        maximum_concurrent=MAX_CONCURRENT, observation_timeout_seconds=TIMEOUT,
        earliest_slot_release_at=earliest, original_next_review_at=parent['next_review_at'],
        selected_next_review_at=selected, latest_allowed_new_inquiry_at=cutoff,
        original_tailwait_proposal=deepcopy(parent), scope=SCOPE)


def decide(name, *, now, candidates, observations=(), pending=(), patient_pending=(),
           max_new, last_contact_at=None, time_context):
    spec(name)
    inputs = dict(now=now, candidates=deepcopy(list(candidates)), observations=deepcopy(list(observations)),
        pending=deepcopy(list(pending)), patient_pending=deepcopy(list(patient_pending)), max_new=max_new,
        last_contact_at=last_contact_at, time_context=deepcopy(time_context))
    parent = original.decide(BASE_POLICY, **inputs)
    tag = timing_hint(inputs, parent)
    value = deepcopy(parent)
    value['next_review_at'] = tag['selected_next_review_at']
    value['evidence']['policy'] = NAME
    value['evidence']['cohort_releasealign'] = tag
    return value


def decide_joint(name, *, members, joint_context, tail_members, priority_context, owner_context):
    spec(name)
    native = context.validate_context(joint_context)
    context.validate_tail_members(native, tail_members, members=members)
    priority = context.validate_priority_context(priority_context, joint_context=native, members=members, tail_members=tail_members)
    owners = context.validate_owner_context(owner_context, joint_context=native, members=members,
        tail_members=tail_members, priority_context=priority)
    parent = original.decide_joint(BASE_POLICY, members=context.base_members_projection(members),
        joint_context=context.base_context_projection(native), tail_members=context.base_members_projection(tail_members),
        priority_context=priority, owner_context=context.base_owner_projection(owners))
    decisions = deepcopy(parent['decisions'])
    for index, decision in enumerate(decisions):
        decision['evidence']['policy'] = NAME
        decision['evidence']['cohort_releasealign'] = dict(schema_version=1, mode='pair_copied_tailwait',
            evaluated=False, intervened=False, member_index=index, next_review_changed=False)
    evidence = dict(schema_version=1, rule=RULE, base_policy=BASE_POLICY, base_source_sha256=BASE_SOURCE_SHA256,
        joint_context=native, priority_context=priority, owner_context=owners, original_tailwait_proposal=parent,
        evaluated=False, copied_tailwait_group=True,
        identity_projection=dict(source_policy=NAME, calculation_policy=BASE_POLICY, copied_only=True,
            fields=['joint_context.eligible[].job.policy_name', 'members[].workflow_snapshot.job.policy_name',
                'tail_members[].workflow_snapshot.job.policy_name', 'owner_context.inventory[].job.policy_name'],
            public_identity_unchanged=True, foreign_base_owner_label='__foreign_cohort_tailwait__'), scope=SCOPE)
    return dict(decisions=decisions, evidence=evidence)
