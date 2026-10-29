"""All original feasible JOINT rows under the intact Stoploss score/objective."""
from copy import deepcopy
import hashlib
from pathlib import Path
from . import cohort_stoploss_policy as original,cohort_fullmenu_context as context,cohort_fullmenu_model as model

NAME='eat_cohort_fullmenu_l15_f'
BASE_POLICY='eat_cohort_stoploss_l15_f'
BASE_SOURCE_SHA256='ae32dc4017c942f88192c654a86d519d4e23b7a6a2ed3262d824cf92ccf83b79'
if hashlib.sha256(Path(original.__file__).read_bytes()).hexdigest()!=BASE_SOURCE_SHA256:
    raise ValueError('frozen Fullmenu Stoploss policy foundation differs')
HORIZON=original.HORIZON
JOINT=original.JOINT
D=original.D
TIERS=(4,2,1)
PROBABILITY_EPSILON=1e-12
WAIT_EPSILON=1e-9
RULE='intact_Stoploss_rows_plus_all_other_original_feasible_joint_rows'
OBJECTIVE='global tier 4,2,1 success sums within 1e-12, then capped wait within 1e-9; prefer Stoploss actual index then original Stoploss menu order followed by added original-table order'
SCOPE='complete current WAITING/H-positive roster; unchanged parent Stoploss proposal and menu rows; exhaustively score every remaining feasible original JOINT row with the same frozen stages and objective, one row at a time; compact added-row Dynamic projection and final metrics; unchanged public inputs, feasibility, contact cardinalities and actual capacity; current pair only, no outcome guarantee'


def spec(name=NAME):
    if name!=NAME:raise ValueError('the fixed fullmenu candidate is '+NAME)
    value=original.spec(BASE_POLICY);value.update(name=NAME,label='EAT l15 f, full feasible Stoploss menu')
    return value


def decide(name,*,now,candidates,observations=(),pending=(),patient_pending=(),max_new,last_contact_at=None,time_context):
    spec(name)
    value=original.decide(BASE_POLICY,now=now,candidates=candidates,observations=observations,pending=pending,
        patient_pending=patient_pending,max_new=max_new,last_contact_at=last_contact_at,time_context=time_context)
    value['evidence']['policy']=NAME
    value['evidence']['cohort_fullmenu']=dict(schema_version=1,mode='horizon_fallback',evaluated=False,intervened=False)
    return value


def _decision(proposal,hospitals,metrics):
    value=deepcopy(proposal);value['send']=list(hospitals);value['reason']='cohort_fullmenu_current_only'
    value['evidence']['policy']=NAME;value['evidence']['sent_hospitals']=list(hospitals)
    value['evidence']['planned_expected_cost']=round(metrics['total_cost_seconds'],3)
    for row in value['evidence']['candidate_plan']:row['planned_offset_seconds']=0 if row['hospital'] in hospitals else None
    return value


def decide_joint(name,*,members,joint_context,tail_members,priority_context,owner_context):
    spec(name);native=context.validate_context(joint_context)
    context.validate_tail_members(native,tail_members,members=members)
    priority=context.validate_priority_context(priority_context,joint_context=native,members=members,tail_members=tail_members)
    owners=context.validate_owner_context(owner_context,joint_context=native,members=members,tail_members=tail_members,priority_context=priority)
    base_members=context.base_members_projection(members);base_tails=context.base_members_projection(tail_members)
    base_context=context.base_context_projection(native)
    proposal=original.decide_joint(BASE_POLICY,members=base_members,joint_context=base_context,tail_members=base_tails,
        priority_context=priority,owner_context=context.base_owner_projection(owners))
    old=proposal['evidence'];evaluated=old['evaluated'];base_index=old['selected_index'];selected=base_index
    parent_menu=deepcopy(old['menu_indices']);menu=[];added=[];feasible=[];scores=[];selection=None;table_count=0
    if evaluated:
        joint=model.parent_views(proposal)['joint']
        table_count=len(joint['combinations'])
        menu,added,feasible=model.full_feasible_menu(parent_menu,joint['combinations'])
        if [r['row_index'] for r in old['row_scores']]!=parent_menu or base_index not in parent_menu:
            raise ValueError('complete parent Stoploss score menu or selected anchor differs')
        scores=deepcopy(old['row_scores'])
        prepared=model.prepare(proposal,members=base_members,tail_members=base_tails,
            joint_context=base_context,priority_context=priority)
        for position in range(len(parent_menu),len(menu)):
            scores.append(model.score_added_row(menu[position],position,prepared))
        selection=D._select(scores,base_index);selected=selection['selected_index']
    elif parent_menu or old['row_scores']:raise ValueError('unevaluated Stoploss unexpectedly has score rows')
    intervened=selected!=base_index
    if intervened:
        chosen=next(row for row in scores if row['row_index']==selected)
        decisions=[_decision(p,send,chosen['member_metrics'][i]['metrics']) for i,(p,send) in
            enumerate(zip(joint['baseline_proposals'],joint['combinations'][selected]['hospitals']))]
    else:decisions=deepcopy(proposal['decisions'])
    by_priority={context._key(p['patient_id']):p['effective_priority'] for p in priority['patients']}
    for i,decision in enumerate(decisions):
        decision['evidence']['policy']=NAME
        decision['evidence']['cohort_fullmenu']=dict(schema_version=1,mode='pair_cohort_fullmenu',evaluated=evaluated,
            intervened=intervened,member_index=i,base_selected_index=base_index,selected_index=selected,
            effective_priority=by_priority[context._key(members[i]['patient_id'])])
    evidence=dict(schema_version=1,rule=RULE,base_policy=BASE_POLICY,base_source_sha256=BASE_SOURCE_SHA256,
        scoring_source_sha256=deepcopy(model.SOURCE_SHA256),
        identity_projection=dict(source_policy=NAME,calculation_policy=BASE_POLICY,
            fields=['joint_context.eligible[].job.policy_name','members[].workflow_snapshot.job.policy_name',
                'tail_members[].workflow_snapshot.job.policy_name','owner_context.inventory[].job.policy_name'],
            copied_only=True,public_identity_unchanged=True,foreign_base_owner_label='__foreign_cohort_stoploss__'),
        joint_context=native,priority_context=priority,owner_context=owners,original_stoploss_proposal=proposal,
        evaluated=evaluated,parent_menu_indices=parent_menu,menu_indices=menu,added_row_indices=added,
        feasible_row_indices=feasible,row_scores=scores,selection=selection,
        coverage=dict(original_joint_row_count=table_count,parent_menu_row_count=len(parent_menu),
            evaluated_row_count=len(scores),expanded_row_count=len(added),feasible_row_count=len(feasible)),
        base_selected_index=base_index,selected_index=selected,intervened=intervened,
        tiers=list(TIERS),tolerances=dict(probability=PROBABILITY_EPSILON,wait_seconds=WAIT_EPSILON),objective=OBJECTIVE,scope=SCOPE)
    return dict(decisions=decisions,evidence=evidence)
