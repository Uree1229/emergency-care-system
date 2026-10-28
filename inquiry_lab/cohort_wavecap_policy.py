"""Copy accepted Tailwait; repair only over-cap contacted-owner H forecasts."""
from copy import deepcopy
import hashlib
import math
from pathlib import Path
from server import inquiry_policies
from . import cohort_tailwait_policy as original, cohort_wavecap_context as context
from . import cohort_wavecap_horizon as horizon

NAME = 'eat_cohort_wavecap_l15_f'
BASE_POLICY = original.NAME
BASE_SOURCE_SHA256 = '2fd6914a26c05651a79569395b30c8281881d5a72edbb11b08b85f33caa3a4a5'
HORIZON_SOURCE_SHA256 = 'fcb5351bf6af1352af9fbbfcdd3ee2a50506b147b5ca05f2846fbe693203bcf9'
SOURCE_SHA256 = dict(tailwait_policy=BASE_SOURCE_SHA256, horizon_policy=horizon.FOUNDATION_SOURCE_SHA256,
    wavecap_horizon=HORIZON_SOURCE_SHA256)
for _module, _digest in ((original, BASE_SOURCE_SHA256), (horizon, HORIZON_SOURCE_SHA256)):
    if hashlib.sha256(Path(_module.__file__).read_bytes()).hexdigest() != _digest:
        raise ValueError('frozen wave-cap policy foundation differs')
HORIZON = original.HORIZON
JOINT = original.JOINT
RULE = 'repair_only_original_H_over_cap_future_plan_for_contacted_pending_owner'
SCOPE = 'Tailwait first-contact groups and uncontacted singleton fallback copied; only contacted own-pending fallback over-cap forecasts are replanned under conservative no-terminal-observation occupancy; real replies may free slots; no actual success or waiting-time guarantee'


def spec(name=NAME):
    if name != NAME:
        raise ValueError('the fixed wave-cap candidate is ' + NAME)
    value = original.spec(BASE_POLICY)
    value.update(name=NAME, label='EAT l15 f, feasible own-contact waves in follow-up H plans')
    return value


def _last_contact(value, now):
    return value if type(value) in (int, float) and math.isfinite(value) and value <= now else None


def _inputs(*, now, candidates, observations, pending, patient_pending, max_new, last_contact_at, time_context):
    # Match H's iterable interface, but materialize once before the two pure
    # planners so a generator cannot change the correction's public input.
    return dict(now=now, candidates=deepcopy(list(candidates)), observations=deepcopy(list(observations)),
        pending=deepcopy(list(pending)), patient_pending=deepcopy(list(patient_pending)), max_new=max_new,
        last_contact_at=last_contact_at, time_context=deepcopy(time_context))


def decide(name, *, now, candidates, observations=(), pending=(), patient_pending=(), max_new,
           last_contact_at=None, time_context):
    spec(name)
    inputs = _inputs(now=now, candidates=candidates, observations=observations, pending=pending,
        patient_pending=patient_pending, max_new=max_new, last_contact_at=last_contact_at, time_context=time_context)
    parent = original.decide(BASE_POLICY, **inputs)
    own = inquiry_policies._pending(inputs['patient_pending'], now, own=True)
    contact = _last_contact(last_contact_at, now)
    config = horizon.spec()['config']
    before = horizon.recorded_occupancy(inputs, parent)
    violates = any(row['total_fresh_count'] > config['max_concurrent'] for row in before)
    evaluated = contact is not None and bool(own) and violates
    corrected = horizon.decide(horizon.NAME, **inputs) if evaluated else None
    value = deepcopy(parent)
    if corrected is not None:
        for key in ('send', 'reason', 'next_review_at'):
            value[key] = deepcopy(corrected[key])
        value['evidence'].update({k: deepcopy(v) for k, v in corrected['evidence'].items() if k != 'wavecap_plan'})
    after = horizon.recorded_occupancy(inputs, value)
    if evaluated and any(row['total_fresh_count'] > config['max_concurrent'] for row in after):
        raise AssertionError('corrected forecast still exceeds own-contact cap')
    value['evidence']['policy'] = NAME
    value['evidence']['cohort_wavecap'] = dict(schema_version=1, mode='horizon_fallback', rule=RULE,
        source_sha256=deepcopy(SOURCE_SHA256), evaluated=evaluated,
        contacted_owner=contact is not None, normalized_own_pending_count=len(own),
        original_plan_over_cap=violates, original_occupancy=before, resulting_occupancy=after,
        maximum_concurrent=config['max_concurrent'], observation_timeout_seconds=config['observation_timeout_seconds'],
        original_tailwait_proposal=parent, corrected_horizon_proposal=corrected,
        send_changed=value['send'] != parent['send'],
        plan_changed=value['evidence']['candidate_plan'] != parent['evidence']['candidate_plan'],
        next_review_changed=value['next_review_at'] != parent['next_review_at'], scope=SCOPE)
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
        decision['evidence']['cohort_wavecap'] = dict(schema_version=1, mode='pair_copied_tailwait',
            evaluated=False, member_index=index, send_changed=False, plan_changed=False, next_review_changed=False)
    evidence = dict(schema_version=1, rule=RULE, source_sha256=deepcopy(SOURCE_SHA256),
        joint_context=native, priority_context=priority, owner_context=owners, original_tailwait_proposal=parent,
        evaluated=False, copied_tailwait_group=True,
        identity_projection=dict(source_policy=NAME, calculation_policy=BASE_POLICY, copied_only=True,
            fields=['joint_context.eligible[].job.policy_name', 'members[].workflow_snapshot.job.policy_name',
                'tail_members[].workflow_snapshot.job.policy_name', 'owner_context.inventory[].job.policy_name'],
            public_identity_unchanged=True, foreign_base_owner_label='__foreign_cohort_tailwait__'), scope=SCOPE)
    return dict(decisions=decisions, evidence=evidence)
