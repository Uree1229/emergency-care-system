"""One soft veto preserving a current tail owner's last zero-pending option.

The frozen joint-first winner is computed unchanged under an explicit copied
policy-name projection. Only its winner is checked, once. Public pending-count
zero does not identify idle workers, capacity, reservations or future replies.
"""
from copy import deepcopy
import hashlib
from pathlib import Path

from server import inquiry_policies
from . import joint_first_policy as original, joint_tail_context

NAME = 'eat_joint_tail_l15_f'
BASE_POLICY = 'eat_joint_first_l15_f'
RULE = 'veto_joint_winner_on_last_public_zero_pending_option_loss'
BASE_SOURCE_SHA256 = 'c176b2c5e4fd4ccb459229efb86661ad6120c8f39525414352738e40ee7f6dcc'
if hashlib.sha256(Path(original.__file__).read_bytes()).hexdigest() != BASE_SOURCE_SHA256:
    raise ValueError('frozen joint-tail foundation differs')


def spec(name=NAME):
    if name != NAME:
        raise ValueError('the fixed joint-tail candidate is ' + NAME)
    value = original.spec(BASE_POLICY)
    value.update(name=NAME,label='EAT l15 f, preserve current tail zero-pending inquiry option')
    return value


def decide(name, *, now, candidates, observations=(), pending=(), patient_pending=(), max_new,
           last_contact_at=None, time_context):
    """Unpaired and subsequent decisions retain the complete H fallback."""
    spec(name)
    result = original.decide(BASE_POLICY,now=now,candidates=candidates,observations=observations,
        pending=pending,patient_pending=patient_pending,max_new=max_new,
        last_contact_at=last_contact_at,time_context=time_context)
    result['evidence']['policy'] = NAME
    result['evidence']['joint_tail'] = dict(schema_version=1,mode='horizon_fallback',guard_evaluated=False)
    return result


def _projected_counts(pending, pair_send):
    counts = {}
    for row in pending:
        counts[row['hospital']] = counts.get(row['hospital'],0)+1
    for send in pair_send:
        for hospital in send:
            counts[hospital] = counts.get(hospital,0)+1
    return counts


def _baseline_decision(evidence, index):
    """Construct an actual baseline output separately from immutable winner."""
    decision = deepcopy(evidence['baseline_proposals'][index])
    baseline = evidence['combinations'][evidence['baseline_index']]
    hospitals = baseline['hospitals'][index]
    decision['send'] = list(hospitals)
    decision['reason'] = 'joint_tail_veto_baseline_current_only'
    e = decision['evidence']
    e['policy'] = NAME
    e['sent_hospitals'] = list(hospitals)
    e['planned_expected_cost'] = round(baseline['member_metrics'][index]['total_cost_seconds'],3)
    for row in e['candidate_plan']:
        row['planned_offset_seconds'] = 0 if row['hospital'] in hospitals else None
    e['joint_first'] = dict(schema_version=1,mode='pair_current_only',member_index=index,
        current_only=True,applied=False,baseline_send=list(hospitals),selected_send=list(hospitals),
        baseline_metrics=deepcopy(evidence['baseline_combination']['member_metrics'][index]),
        selected_metrics=deepcopy(baseline['member_metrics'][index]),
        constraint_reference='common-prefix simultaneous immediate-only H sends')
    return decision


def decide_joint(name, *, members, joint_context, tail_members):
    spec(name)
    context = joint_tail_context.validate_context(joint_context)
    opportunities = joint_tail_context.validate_tail_members(context,tail_members,members=members)
    projected_context = joint_tail_context.base_context_projection(context)
    projected_members = joint_tail_context.base_members_projection(members)
    proposal = original.decide_joint(BASE_POLICY,members=projected_members,joint_context=projected_context)
    evaluated = proposal['evidence']['applied']
    checks = []
    if evaluated:
        pending = inquiry_policies._pending(members[0]['inputs']['pending'],context['now'])
        baseline_send = [row['send'] for row in proposal['evidence']['baseline_proposals']]
        winner_send = [row['send'] for row in proposal['decisions']]
        baseline_counts = _projected_counts(pending,baseline_send)
        winner_counts = _projected_counts(pending,winner_send)
        for opportunity in opportunities:
            hospitals = opportunity['hospitals']
            baseline_zero = [h for h in hospitals if baseline_counts.get(h,0)==0]
            winner_zero = [h for h in hospitals if winner_counts.get(h,0)==0]
            checks.append(dict(patient_id=opportunity['patient_id'],candidate_pool=list(hospitals),
                baseline_pending_counts={h:baseline_counts.get(h,0) for h in hospitals},
                winner_pending_counts={h:winner_counts.get(h,0) for h in hospitals},
                baseline_zero_pending_hospitals=baseline_zero,winner_zero_pending_hospitals=winner_zero,
                triggered=bool(baseline_zero) and not winner_zero))
    triggered = [row['patient_id'] for row in checks if row['triggered']]
    vetoed = evaluated and bool(triggered)
    selected_index = proposal['evidence']['baseline_index'] if vetoed else proposal['evidence']['selected_index']
    decisions = []
    for index,decision in enumerate(proposal['decisions']):
        actual = _baseline_decision(proposal['evidence'],index) if vetoed else deepcopy(decision)
        actual['evidence']['policy'] = NAME
        actual['evidence']['joint_tail'] = dict(schema_version=1,mode='pair_tail_guard',
            guard_evaluated=evaluated,vetoed=vetoed,triggered_tail_patients=list(triggered),
            original_joint_applied=proposal['evidence']['applied'],selected_index=selected_index)
        decisions.append(actual)
    evidence = dict(schema_version=1,rule=RULE,base_policy=BASE_POLICY,base_source_sha256=BASE_SOURCE_SHA256,
        identity_projection=dict(source_policy=NAME,calculation_policy=BASE_POLICY,
            fields=['joint_context.eligible[].job.policy_name','members[].workflow_snapshot.job.policy_name'],
            copied_only=True,public_identity_unchanged=True),
        joint_context=context,original_joint_proposal=proposal,guard_evaluated=evaluated,
        tail_checks=checks,triggered_tail_patients=triggered,vetoed=vetoed,actual_selected_index=selected_index,
        tail_pool_rule='unchanged normalized nearest min(6,max_new) current candidate pool',
        pending_scope='current unresolved public inquiries plus each proposed immediate pair inquiry once',
        zero_scope='public pending count only; not idle workers, beds or future availability',
        fallback_rule='whole current pair baseline; no second-best search; unchanged when original applied=False')
    return dict(decisions=decisions,evidence=evidence)
