"""Same Pareto protection; predicted capped wait is the first objective."""
from copy import deepcopy
import hashlib
from pathlib import Path
from . import cohort_pareto_policy as original,cohort_waitpareto_context as context,cohort_waitpareto_model as model

NAME='eat_cohort_waitpareto_l15_f'
BASE_POLICY='eat_cohort_pareto_l15_f'
BASE_SOURCE_SHA256='f61ef3ddd7e795508082419e8494bff225f916cf29b8a56c8b3a24017dd68122'
if hashlib.sha256(Path(original.__file__).read_bytes()).hexdigest()!=BASE_SOURCE_SHA256:
    raise ValueError('frozen Waitpareto parent policy differs')
HORIZON=original.HORIZON
JOINT=original.JOINT
TIERS=(4,2,1)
PROBABILITY_EPSILON=1e-12
WAIT_EPSILON=1e-9
RULE='intact_Pareto_guard_and_scores_with_wait_first_selection'
OBJECTIVE='among unchanged Pareto-admitted rows minimize total capped wait within1e-9, then tier4,2,1 probability bands within1e-12, then original embedded Stoploss actual index and unchanged source row order'
SCOPE='unchanged complete parent Pareto proposal, owner-probability guard, Fullmenu scores, activation and actual constraints; reorder selector objectives only; baseline wait bound follows from admitted Stoploss row without an extra cap; current pair only, no realized success or waiting guarantee'


def spec(name=NAME):
    if name!=NAME:raise ValueError('the fixed waitpareto candidate is '+NAME)
    value=original.spec(BASE_POLICY);value.update(name=NAME,label='EAT l15 f, protected wait-first selection')
    return value


def decide(name,*,now,candidates,observations=(),pending=(),patient_pending=(),max_new,last_contact_at=None,time_context):
    spec(name)
    value=original.decide(BASE_POLICY,now=now,candidates=candidates,observations=observations,pending=pending,
        patient_pending=patient_pending,max_new=max_new,last_contact_at=last_contact_at,time_context=time_context)
    value['evidence']['policy']=NAME
    value['evidence']['cohort_waitpareto']=dict(schema_version=1,mode='horizon_fallback',evaluated=False,intervened=False)
    return value


def _decision(proposal,hospitals,metrics):
    value=deepcopy(proposal);value['send']=list(hospitals);value['reason']='cohort_waitpareto_current_only'
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
    anchor=old['stoploss_anchor_index'];selection=None;full=old['original_fullmenu_proposal']['evidence']
    if anchor!=full['base_selected_index'] or anchor!=full['original_stoploss_proposal']['evidence']['selected_index']:
        raise ValueError('original embedded Stoploss actual anchor differs')
    if evaluated:
        guard=old['owner_probability_guard']
        if guard['baseline_row_index']!=anchor:raise ValueError('unchanged Pareto guard anchor differs')
        selection=model.select_protected_wait(full['row_scores'],admissible_row_indices=guard['admissible_row_indices'],
            baseline_index=anchor,owner_inventory=owners['inventory'],menu_indices=full['menu_indices'],
            feasible_row_indices=full['feasible_row_indices'])
        selected=selection['selected_index']
    elif old['owner_probability_guard'] is not None or full['row_scores'] or full['menu_indices']:
        raise ValueError('unevaluated Pareto unexpectedly has a guard or score rows')
    intervened=selected!=base_index
    if intervened:
        joint=original.original.model.parent_views(full['original_stoploss_proposal'])['joint']
        chosen=next(row for row in full['row_scores'] if row['row_index']==selected)
        decisions=[_decision(p,send,chosen['member_metrics'][i]['metrics']) for i,(p,send) in
            enumerate(zip(joint['baseline_proposals'],joint['combinations'][selected]['hospitals']))]
    else:decisions=deepcopy(proposal['decisions'])
    by_priority={context._key(p['patient_id']):p['effective_priority'] for p in priority['patients']}
    for i,decision in enumerate(decisions):
        decision['evidence']['policy']=NAME
        decision['evidence']['cohort_waitpareto']=dict(schema_version=1,mode='pair_cohort_waitpareto',evaluated=evaluated,
            intervened=intervened,member_index=i,base_selected_index=base_index,stoploss_anchor_index=anchor,
            selected_index=selected,effective_priority=by_priority[context._key(members[i]['patient_id'])])
    evidence=dict(schema_version=1,rule=RULE,base_policy=BASE_POLICY,base_source_sha256=BASE_SOURCE_SHA256,
        identity_projection=dict(source_policy=NAME,calculation_policy=BASE_POLICY,
            fields=['joint_context.eligible[].job.policy_name','members[].workflow_snapshot.job.policy_name',
                'tail_members[].workflow_snapshot.job.policy_name','owner_context.inventory[].job.policy_name'],
            copied_only=True,public_identity_unchanged=True,foreign_base_owner_label='__foreign_cohort_pareto__'),
        joint_context=native,priority_context=priority,owner_context=owners,original_pareto_proposal=proposal,
        evaluated=evaluated,protected_wait_selection=selection,base_selected_index=base_index,
        stoploss_anchor_index=anchor,selected_index=selected,intervened=intervened,tiers=list(TIERS),
        tolerances=dict(probability=PROBABILITY_EPSILON,wait_seconds=WAIT_EPSILON),objective=OBJECTIVE,scope=SCOPE)
    return dict(decisions=decisions,evidence=evidence)
