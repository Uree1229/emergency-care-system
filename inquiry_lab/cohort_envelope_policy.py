"""Full feasible rows, uniformly rescored with the local queue envelope."""
from copy import deepcopy
import hashlib
from pathlib import Path
from . import cohort_waitpareto_policy as original,cohort_envelope_context as context,cohort_envelope_model as model
from . import cohort_pareto_model as protection,cohort_waitpareto_model as selector

NAME='eat_cohort_envelope_l15_f'
BASE_POLICY='eat_cohort_waitpareto_l15_f'
BASE_SOURCE_SHA256='60f565d5696a9e87316e61b6da3d1e7932a7d1e79ec44f57ded68c8e942afaf9'
if hashlib.sha256(Path(original.__file__).read_bytes()).hexdigest()!=BASE_SOURCE_SHA256:
    raise ValueError('frozen envelope Waitpareto foundation differs')
HORIZON=original.HORIZON
JOINT=original.JOINT
TIERS=(4,2,1)
PROBABILITY_EPSILON=1e-12
WAIT_EPSILON=1e-9
RULE='uniform_queue_envelope_rescore_then_unchanged_owner_guard_and_wait_first_selection'
OBJECTIVE='uniformly rescore every feasible row including original Stoploss actual anchor, unchanged per-owner probability floor within1e-12, minimum total capped-wait band within1e-9 then tier4,2,1 probability bands, original Stoploss anchor and source order'
SCOPE='complete intact Waitpareto proposal retained; same activation, feasible menu, initial/residual scores, frozen future plans and fixed F0; only local random-baseline/deterministic-prior envelope changes, followed by unchanged guard law on uniformly new scores and unchanged wait selector; current pair only, no realized outcome guarantee'


def spec(name=NAME):
    if name!=NAME:raise ValueError('the fixed envelope candidate is '+NAME)
    value=original.spec(BASE_POLICY);value.update(name=NAME,label='EAT l15 f, conditional queue envelope')
    return value


def decide(name,*,now,candidates,observations=(),pending=(),patient_pending=(),max_new,last_contact_at=None,time_context):
    spec(name)
    value=original.decide(BASE_POLICY,now=now,candidates=candidates,observations=observations,pending=pending,
        patient_pending=patient_pending,max_new=max_new,last_contact_at=last_contact_at,time_context=time_context)
    value['evidence']['policy']=NAME
    value['evidence']['cohort_envelope']=dict(schema_version=1,mode='horizon_fallback',evaluated=False,intervened=False)
    return value


def _decision(proposal,hospitals,metrics):
    value=deepcopy(proposal);value['send']=list(hospitals);value['reason']='cohort_envelope_current_only'
    value['evidence']['policy']=NAME;value['evidence']['sent_hospitals']=list(hospitals)
    value['evidence']['planned_expected_cost']=round(metrics['total_cost_seconds'],3)
    for row in value['evidence']['candidate_plan']:row['planned_offset_seconds']=0 if row['hospital'] in hospitals else None
    return value


def _prepare(full,members,tails,native,priority):
    ms=members;ts=tails;joint_context=native
    for projection in (context,original.context,original.original.context,original.original.original.context):
        ms=projection.base_members_projection(ms);ts=projection.base_members_projection(ts)
        joint_context=projection.base_context_projection(joint_context)
    return model.fullmenu.prepare(full['original_stoploss_proposal'],members=ms,tail_members=ts,
        joint_context=joint_context,priority_context=priority)


def decide_joint(name,*,members,joint_context,tail_members,priority_context,owner_context):
    spec(name);native=context.validate_context(joint_context)
    context.validate_tail_members(native,tail_members,members=members)
    priority=context.validate_priority_context(priority_context,joint_context=native,members=members,tail_members=tail_members)
    owners=context.validate_owner_context(owner_context,joint_context=native,members=members,tail_members=tail_members,priority_context=priority)
    proposal=original.decide_joint(BASE_POLICY,members=context.base_members_projection(members),
        joint_context=context.base_context_projection(native),tail_members=context.base_members_projection(tail_members),
        priority_context=priority,owner_context=context.base_owner_projection(owners))
    old=proposal['evidence'];evaluated=old['evaluated'];base_index=old['selected_index'];selected=base_index
    pareto=old['original_pareto_proposal']['evidence'];full=pareto['original_fullmenu_proposal']['evidence']
    anchor=old['stoploss_anchor_index'];scores=[];guard=None;selection=None
    if anchor!=pareto['stoploss_anchor_index'] or anchor!=full['base_selected_index'] or anchor!=full['original_stoploss_proposal']['evidence']['selected_index']:
        raise ValueError('original embedded Stoploss actual anchor differs')
    if evaluated:
        prepared=_prepare(full,members,tail_members,native,priority)
        scores=[model.rescore_row(row,prepared) for row in full['row_scores']]
        guard=protection.guard_rows(scores,baseline_index=anchor,owner_inventory=owners['inventory'],
            menu_indices=full['menu_indices'],feasible_row_indices=full['feasible_row_indices'])
        selection=selector.select_protected_wait(scores,admissible_row_indices=guard['admissible_row_indices'],
            baseline_index=anchor,owner_inventory=owners['inventory'],menu_indices=full['menu_indices'],feasible_row_indices=full['feasible_row_indices'])
        selected=selection['selected_index']
    elif full['row_scores'] or full['menu_indices'] or old['protected_wait_selection'] is not None:
        raise ValueError('unevaluated Waitpareto unexpectedly has score rows or a selection')
    intervened=selected!=base_index
    if intervened:
        joint=model.fullmenu.parent_views(full['original_stoploss_proposal'])['joint']
        chosen=next(row for row in scores if row['row_index']==selected)
        decisions=[_decision(p,send,chosen['member_metrics'][i]['metrics']) for i,(p,send) in
            enumerate(zip(joint['baseline_proposals'],joint['combinations'][selected]['hospitals']))]
    else:decisions=deepcopy(proposal['decisions'])
    by_priority={context._key(p['patient_id']):p['effective_priority'] for p in priority['patients']}
    for i,decision in enumerate(decisions):
        decision['evidence']['policy']=NAME
        decision['evidence']['cohort_envelope']=dict(schema_version=1,mode='pair_cohort_envelope',evaluated=evaluated,
            intervened=intervened,member_index=i,base_selected_index=base_index,stoploss_anchor_index=anchor,
            selected_index=selected,effective_priority=by_priority[context._key(members[i]['patient_id'])])
    evidence=dict(schema_version=1,rule=RULE,base_policy=BASE_POLICY,base_source_sha256=BASE_SOURCE_SHA256,
        scoring_source_sha256=deepcopy(model.SOURCE_SHA256),
        identity_projection=dict(source_policy=NAME,calculation_policy=BASE_POLICY,
            fields=['joint_context.eligible[].job.policy_name','members[].workflow_snapshot.job.policy_name',
                'tail_members[].workflow_snapshot.job.policy_name','owner_context.inventory[].job.policy_name'],
            copied_only=True,public_identity_unchanged=True,foreign_base_owner_label='__foreign_cohort_waitpareto__'),
        joint_context=native,priority_context=priority,owner_context=owners,original_waitpareto_proposal=proposal,
        evaluated=evaluated,new_row_scores=scores,owner_probability_guard=guard,protected_wait_selection=selection,
        base_selected_index=base_index,stoploss_anchor_index=anchor,selected_index=selected,intervened=intervened,
        tiers=list(TIERS),tolerances=dict(probability=PROBABILITY_EPSILON,wait_seconds=WAIT_EPSILON),objective=OBJECTIVE,scope=SCOPE)
    return dict(decisions=decisions,evidence=evidence)
