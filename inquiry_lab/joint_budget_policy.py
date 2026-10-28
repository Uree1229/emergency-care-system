"""Review one frozen TAIL veto using paired first-contact count projections.

The two virtual ledgers add immediate inquiries at the unchanged public clock.
Frozen JOINT supplies each later pair; there are no virtual replies, selections,
future arrivals, time progression or recursive TAIL/budget decisions.
"""
from copy import deepcopy
import hashlib
from pathlib import Path

from . import joint_tail_policy as original, joint_budget_context, joint_tail_context

NAME = 'eat_joint_budget_l15_f'
BASE_POLICY = 'eat_joint_tail_l15_f'
RULE = 'cancel_tail_veto_when_a_later_paired_first_contact_count_declines'
BASE_SOURCE_SHA256 = '77a683c08dec240d5a18d333d37f8fc7fae53ca62b6e3f73877c50cdf1365e07'
PROJECTION_POLICY = 'eat_joint_first_l15_f'
PROJECTION_SOURCE_SHA256 = 'c176b2c5e4fd4ccb459229efb86661ad6120c8f39525414352738e40ee7f6dcc'
for _module,_expected in ((original,BASE_SOURCE_SHA256),(original.original,PROJECTION_SOURCE_SHA256)):
    if hashlib.sha256(Path(_module.__file__).read_bytes()).hexdigest() != _expected:
        raise ValueError('frozen joint-budget foundation differs')

PROJECTION_RULE = ('fixed eligible[2:] paired in order; simultaneous H counts within each frozen JOINT pair; '
    'append that pair selected immediate inquiries before the next pair; fixed now, sources and versions')
SINGLETON_RULE = 'capture and original TAIL guard retained; final odd singleton excluded only from count projection'


def spec(name=NAME):
    if name != NAME:
        raise ValueError('the fixed joint-budget candidate is ' + NAME)
    value = original.spec(BASE_POLICY)
    value.update(name=NAME,label='EAT l15 f, review tail veto with paired first-contact budgets')
    return value


def decide(name, *, now, candidates, observations=(), pending=(), patient_pending=(), max_new,
           last_contact_at=None, time_context):
    """Unpaired and subsequent decisions retain the complete H fallback."""
    spec(name)
    result = original.decide(BASE_POLICY,now=now,candidates=candidates,observations=observations,
        pending=pending,patient_pending=patient_pending,max_new=max_new,
        last_contact_at=last_contact_at,time_context=time_context)
    result['evidence']['policy'] = NAME
    result['evidence']['joint_budget'] = dict(schema_version=1,mode='horizon_fallback',projection_evaluated=False)
    return result


def _append(ledger, members, sends, *, now, kind, step_index, proposal_path):
    """Append each immediate inquiry once, retaining its proposal location."""
    appended = []
    for member_index,(member,hospitals) in enumerate(zip(members,sends)):
        for send_index,hospital in enumerate(hospitals):
            inquiry = dict(hospital=hospital,patient=member['patient_id'],submitted_at=now)
            ledger.append(deepcopy(inquiry))
            appended.append(dict(inquiry=inquiry,source=dict(kind=kind,step_index=step_index,
                member_index=member_index,send_index=send_index,
                proposal_path=proposal_path.format(member=member_index,send=send_index))))
    return appended


def _project(path, *, context, members, tail_members, original_joint):
    """Use frozen JOINT arithmetic over copied public inputs at one clock."""
    joint_context = joint_tail_context.base_context_projection(
        joint_budget_context.base_context_projection(context))
    pair = joint_tail_context.base_members_projection(joint_budget_context.base_members_projection(members))
    tails = joint_tail_context.base_members_projection(joint_budget_context.base_members_projection(tail_members))
    ledger = deepcopy(pair[0]['inputs']['pending'])
    initial_pending = deepcopy(ledger)
    prefix = 'original_tail_proposal.evidence.original_joint_proposal.'
    if path == 'baseline':
        sends = [decision['send'] for decision in original_joint['evidence']['baseline_proposals']]
        source = prefix+'evidence.baseline_proposals[{member}].send[{send}]'
    else:
        sends = [decision['send'] for decision in original_joint['decisions']]
        source = prefix+'decisions[{member}].send[{send}]'
    initial = _append(ledger,pair,sends,now=context['now'],kind='current_pair_'+path,
        step_index=None,proposal_path=source)
    paired = len(tails)//2
    steps = []
    for step_index in range(paired):
        start = 2*step_index
        projected_members = deepcopy(tails[start:start+2])
        for member in projected_members:
            member['inputs']['pending'] = deepcopy(ledger)
        projected_context = deepcopy(joint_context)
        projected_context['eligible'] = projected_context['eligible'][start+2:]
        projected_context['pair_patient_ids'] = [member['patient_id'] for member in projected_members]
        proposal = original.original.decide_joint(PROJECTION_POLICY,members=projected_members,
            joint_context=projected_context)
        appends = _append(ledger,projected_members,[decision['send'] for decision in proposal['decisions']],
            now=context['now'],kind='projected_pair_selected',step_index=step_index,
            proposal_path='projections.'+path+'.steps['+str(step_index)+'].proposal.decisions[{member}].send[{send}]')
        steps.append(dict(step_index=step_index,source_tail_member_indices=[start,start+1],
            joint_context=projected_context,members=projected_members,proposal=proposal,
            appended_inquiries=appends))
    return dict(path=path,initial_pending=initial_pending,initial_appended_inquiries=initial,
        paired_patient_ids=[[member['patient_id'] for member in tails[2*i:2*i+2]] for i in range(paired)],
        excluded_singleton_patient_ids=[tails[-1]['patient_id']] if len(tails)%2 else [],
        steps=steps,final_pending=deepcopy(ledger))


def decide_joint(name, *, members, joint_context, tail_members):
    spec(name)
    context = joint_budget_context.validate_context(joint_context)
    joint_budget_context.validate_tail_members(context,tail_members,members=members)
    proposal = original.decide_joint(BASE_POLICY,
        members=joint_budget_context.base_members_projection(members),
        joint_context=joint_budget_context.base_context_projection(context),
        tail_members=joint_budget_context.base_members_projection(tail_members))
    evaluated = proposal['evidence']['vetoed']
    projections = dict(baseline=None,winner=None)
    comparisons = []
    joint = proposal['evidence']['original_joint_proposal']
    if evaluated:
        for path in projections:
            projections[path] = _project(path,context=context,members=members,
                tail_members=tail_members,original_joint=joint)
        for baseline,winner in zip(projections['baseline']['steps'],projections['winner']['steps']):
            for member_index,member in enumerate(baseline['members']):
                b = baseline['proposal']['evidence']['cardinalities'][member_index]
                w = winner['proposal']['evidence']['cardinalities'][member_index]
                comparisons.append(dict(patient_id=member['patient_id'],projection_step=baseline['step_index'],
                    member_index=member_index,baseline_k=b,winner_k=w,declined=b<w))
    declining = [row['patient_id'] for row in comparisons if row['declined']]
    cancelled = bool(declining)
    selected_index = joint['evidence']['selected_index'] if cancelled else proposal['evidence']['actual_selected_index']
    source_decisions = joint['decisions'] if cancelled else proposal['decisions']
    decisions = deepcopy(source_decisions)
    for decision in decisions:
        decision['evidence']['policy'] = NAME
        decision['evidence']['joint_budget'] = dict(schema_version=1,mode='pair_paired_count_budget_guard',
            projection_evaluated=evaluated,original_tail_vetoed=proposal['evidence']['vetoed'],
            cancelled_tail_veto=cancelled,declining_patients=list(declining),actual_selected_index=selected_index)
    evidence = dict(schema_version=1,rule=RULE,base_policy=BASE_POLICY,base_source_sha256=BASE_SOURCE_SHA256,
        projection_policy=PROJECTION_POLICY,projection_source_sha256=PROJECTION_SOURCE_SHA256,
        identity_projection=dict(source_policy=NAME,calculation_policy=BASE_POLICY,
            fields=['joint_context.eligible[].job.policy_name','members[].workflow_snapshot.job.policy_name',
                'tail_members[].workflow_snapshot.job.policy_name'],copied_only=True,public_identity_unchanged=True),
        joint_context=context,original_tail_proposal=proposal,projection_evaluated=evaluated,
        projections=projections,paired_count_comparisons=comparisons,declining_patients=declining,
        cancelled_tail_veto=cancelled,actual_selected_index=selected_index,
        projection_rule=PROJECTION_RULE,singleton_rule=SINGLETON_RULE,
        scope='same-clock public-input inquiry arithmetic only; no future replies, selections or outcome rollout')
    return dict(decisions=decisions,evidence=evidence)
