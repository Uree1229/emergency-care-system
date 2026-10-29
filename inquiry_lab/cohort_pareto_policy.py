"""Fullmenu scores with every owner's predicted probability guarded."""
from copy import deepcopy
import hashlib
from pathlib import Path
from . import cohort_fullmenu_policy as original,cohort_pareto_context as context,cohort_pareto_model as model

NAME='eat_cohort_pareto_l15_f'
BASE_POLICY='eat_cohort_fullmenu_l15_f'
BASE_SOURCE_SHA256='86835a44678ad372c4b9853623cc50ab78a61200a30b3ab6226af5f1d77215fe'
if hashlib.sha256(Path(original.__file__).read_bytes()).hexdigest()!=BASE_SOURCE_SHA256:
    raise ValueError('frozen Pareto Fullmenu policy foundation differs')
HORIZON=original.HORIZON
JOINT=original.JOINT
D=original.D
TIERS=(4,2,1)
PROBABILITY_EPSILON=1e-12
WAIT_EPSILON=1e-9
RULE='intact_Fullmenu_scores_filtered_by_every_owner_probability_nonregression'
OBJECTIVE='global tier 4,2,1 success sums within1e-12, then capped wait within1e-9 among owner-admissible rows; prefer original embedded Stoploss actual index then unchanged Fullmenu row order'
SCOPE='complete current WAITING/H-positive scored inventory; unchanged exhaustive Fullmenu proposal, activation, source scores and actual constraints; require every owner final predicted probability to match embedded Stoploss actual row within existing tolerance; same tier/wait selector anchored to Stoploss, current pair only; no realized-success guarantee'


def spec(name=NAME):
    if name!=NAME:raise ValueError('the fixed pareto candidate is '+NAME)
    value=original.spec(BASE_POLICY);value.update(name=NAME,label='EAT l15 f, per-owner predicted probability guard')
    return value


def decide(name,*,now,candidates,observations=(),pending=(),patient_pending=(),max_new,last_contact_at=None,time_context):
    spec(name)
    value=original.decide(BASE_POLICY,now=now,candidates=candidates,observations=observations,pending=pending,
        patient_pending=patient_pending,max_new=max_new,last_contact_at=last_contact_at,time_context=time_context)
    value['evidence']['policy']=NAME
    value['evidence']['cohort_pareto']=dict(schema_version=1,mode='horizon_fallback',evaluated=False,intervened=False)
    return value


def _decision(proposal,hospitals,metrics):
    value=deepcopy(proposal);value['send']=list(hospitals);value['reason']='cohort_pareto_current_only'
    value['evidence']['policy']=NAME;value['evidence']['sent_hospitals']=list(hospitals)
    value['evidence']['planned_expected_cost']=round(metrics['total_cost_seconds'],3)
    for row in value['evidence']['candidate_plan']:row['planned_offset_seconds']=0 if row['hospital'] in hospitals else None
    return value


def decide_joint(name,*,members,joint_context,tail_members,priority_context,owner_context):
    spec(name);native=context.validate_context(joint_context)
    context.validate_tail_members(native,tail_members,members=members)
    priority=context.validate_priority_context(priority_context,joint_context=native,members=members,tail_members=tail_members)
    owners=context.validate_owner_context(owner_context,joint_context=native,members=members,tail_members=tail_members,priority_context=priority)
    proposal=original.decide_joint(BASE_POLICY,members=context.base_members_projection(members),
        joint_context=context.base_context_projection(native),tail_members=context.base_members_projection(tail_members),
        priority_context=priority,owner_context=context.base_owner_projection(owners))
    old=proposal['evidence'];evaluated=old['evaluated'];base_index=old['selected_index'];selected=base_index
    anchor=old['base_selected_index'];guard=None;selection=None
    if anchor!=old['original_stoploss_proposal']['evidence']['selected_index']:
        raise ValueError('original embedded Stoploss actual index differs')
    if evaluated:
        guard=model.guard_rows(old['row_scores'],baseline_index=anchor,owner_inventory=owners['inventory'],
            menu_indices=old['menu_indices'],feasible_row_indices=old['feasible_row_indices'])
        admissible=set(guard['admissible_row_indices'])
        scores=[row for row in old['row_scores'] if row['row_index'] in admissible]
        selection=D._select(scores,anchor);selected=selection['selected_index']
    elif old['menu_indices'] or old['row_scores']:raise ValueError('unevaluated Fullmenu unexpectedly has score rows')
    intervened=selected!=base_index
    if intervened:
        joint=original.model.parent_views(old['original_stoploss_proposal'])['joint']
        chosen=next(row for row in old['row_scores'] if row['row_index']==selected)
        decisions=[_decision(p,send,chosen['member_metrics'][i]['metrics']) for i,(p,send) in
            enumerate(zip(joint['baseline_proposals'],joint['combinations'][selected]['hospitals']))]
    else:decisions=deepcopy(proposal['decisions'])
    by_priority={context._key(p['patient_id']):p['effective_priority'] for p in priority['patients']}
    for i,decision in enumerate(decisions):
        decision['evidence']['policy']=NAME
        decision['evidence']['cohort_pareto']=dict(schema_version=1,mode='pair_cohort_pareto',evaluated=evaluated,
            intervened=intervened,member_index=i,base_selected_index=base_index,stoploss_anchor_index=anchor,
            selected_index=selected,effective_priority=by_priority[context._key(members[i]['patient_id'])])
    evidence=dict(schema_version=1,rule=RULE,base_policy=BASE_POLICY,base_source_sha256=BASE_SOURCE_SHA256,
        identity_projection=dict(source_policy=NAME,calculation_policy=BASE_POLICY,
            fields=['joint_context.eligible[].job.policy_name','members[].workflow_snapshot.job.policy_name',
                'tail_members[].workflow_snapshot.job.policy_name','owner_context.inventory[].job.policy_name'],
            copied_only=True,public_identity_unchanged=True,foreign_base_owner_label='__foreign_cohort_fullmenu__'),
        joint_context=native,priority_context=priority,owner_context=owners,original_fullmenu_proposal=proposal,
        evaluated=evaluated,owner_probability_guard=guard,selection=selection,base_selected_index=base_index,
        stoploss_anchor_index=anchor,selected_index=selected,intervened=intervened,tiers=list(TIERS),
        tolerances=dict(probability=PROBABILITY_EPSILON,wait_seconds=WAIT_EPSILON),objective=OBJECTIVE,scope=SCOPE)
    return dict(decisions=decisions,evidence=evidence)
