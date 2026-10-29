"""Apply the H-boundary membership shield only to newly evaluated first pairs."""
from copy import deepcopy
import hashlib
from pathlib import Path
from . import cohort_pairscope_policy as original,cohort_pairshield_context as context,cohort_pairshield_model as model

NAME='eat_cohort_pairshield_l15_f'
BASE_POLICY='eat_cohort_pairscope_l15_f'
BASE_SOURCE_SHA256='193127b8019a34c4dc7736d9dce3c11e260d09ef0214660cda00a52709ddf1da'
if hashlib.sha256(Path(original.__file__).read_bytes()).hexdigest()!=BASE_SOURCE_SHA256:
    raise ValueError('frozen pair-shield Pairscope policy differs')
HORIZON=original.HORIZON
JOINT=original.JOINT
TIERS=(4,2,1)
PROBABILITY_EPSILON=1e-12
WAIT_EPSILON=1e-9
RULE='new_Pairscope_groups_only_with_bidirectional_queued_H_boundary_membership_support'
OBJECTIVE='preserve full original P-admitted rows and immutable scores; intersect with H-boundary membership support, then unchanged wait-first bands and priority/Stoploss-anchor ties on an explicit labeled subdomain'
SCOPE='only parent newly_activated exact-two first-pair groups; inherited active, inactive tail and singleton fallback copied; removed-baseline-zero and added-candidate-zero memberships rejected, shared membership class changes allowed; exact public own H and stored zero masses, no probability threshold or physical-zero/success guarantee'


def spec(name=NAME):
    if name!=NAME:raise ValueError('the fixed pair-shield candidate is '+NAME)
    value=original.spec(BASE_POLICY);value.update(name=NAME,label='EAT l15 f, first-wave H-boundary membership shield')
    return value


def decide(name,*,now,candidates,observations=(),pending=(),patient_pending=(),max_new,last_contact_at=None,time_context):
    spec(name)
    value=original.decide(BASE_POLICY,now=now,candidates=candidates,observations=observations,pending=pending,
        patient_pending=patient_pending,max_new=max_new,last_contact_at=last_contact_at,time_context=time_context)
    value['evidence']['policy']=NAME
    value['evidence']['cohort_pairshield']=dict(schema_version=1,mode='horizon_fallback',evaluated=False,intervened=False)
    return value


def _decision(proposal,hospitals,metrics):
    value=deepcopy(proposal);value['send']=list(hospitals);value['reason']='cohort_pairshield_current_only'
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
    old=proposal['evidence'];evaluated=old['evaluated'];shield=old['newly_activated'];base_index=old['selected_index'];selected=base_index;anchor=old['stoploss_anchor_index']
    if type(evaluated) is not bool or type(shield) is not bool:raise ValueError('typed original activation required')
    if shield and (not evaluated or old['inherited_evaluated'] or tail_members!=[]):raise ValueError('shield applies only to newly evaluated no-tail first pairs')
    guard=None;selection=None
    if shield:
        guard=model.guard_rows(old['new_row_scores'],baseline_index=anchor,current_pair_patient_ids=[m['patient_id'] for m in members],
            menu_indices=old['new_menu_indices'],feasible_row_indices=old['new_feasible_row_indices'])
        selection=model.select_supported_wait(old['new_row_scores'],baseline_index=anchor,owner_inventory=owners['inventory'],
            menu_indices=old['new_menu_indices'],feasible_row_indices=old['new_feasible_row_indices'],
            probability_admissible_row_indices=old['owner_probability_guard']['admissible_row_indices'],support_admissible_row_indices=guard['admissible_row_indices'])
        if selection['original_probability_domain_selection']!=old['protected_wait_selection']:
            raise ValueError('unchanged original probability-domain selection differs')
        selected=selection['selected_index']
    intervened=selected!=base_index
    if intervened:
        env=old['original_envelope_proposal']['evidence'];full=env['original_waitpareto_proposal']['evidence']['original_pareto_proposal']['evidence']['original_fullmenu_proposal']['evidence']
        joint=original.model.fullmenu.parent_views(full['original_stoploss_proposal'])['joint']
        chosen=next(row for row in old['new_row_scores'] if row['row_index']==selected)
        decisions=[_decision(p,send,chosen['member_metrics'][i]['metrics']) for i,(p,send) in enumerate(zip(joint['baseline_proposals'],joint['combinations'][selected]['hospitals']))]
    else:decisions=deepcopy(proposal['decisions'])
    priorities={context._key(p['patient_id']):p['effective_priority'] for p in priority['patients']}
    for i,decision in enumerate(decisions):
        decision['evidence']['policy']=NAME
        decision['evidence']['cohort_pairshield']=dict(schema_version=1,mode='pair_cohort_pairshield',evaluated=evaluated,
            shield_evaluated=shield,intervened=intervened,member_index=i,base_selected_index=base_index,stoploss_anchor_index=anchor,
            selected_index=selected,effective_priority=priorities[context._key(members[i]['patient_id'])])
    evidence=dict(schema_version=1,rule=RULE,base_policy=BASE_POLICY,base_source_sha256=BASE_SOURCE_SHA256,scoring_source_sha256=deepcopy(model.SOURCE_SHA256),
        identity_projection=dict(source_policy=NAME,calculation_policy=BASE_POLICY,
            fields=['joint_context.eligible[].job.policy_name','members[].workflow_snapshot.job.policy_name','tail_members[].workflow_snapshot.job.policy_name','owner_context.inventory[].job.policy_name'],
            copied_only=True,public_identity_unchanged=True,foreign_base_owner_label='__foreign_cohort_pairscope__'),
        joint_context=native,priority_context=priority,owner_context=owners,original_pairscope_proposal=proposal,
        evaluated=evaluated,shield_evaluated=shield,mean_queue_support_guard=guard,protected_support_selection=selection,
        base_selected_index=base_index,stoploss_anchor_index=anchor,selected_index=selected,intervened=intervened,
        tiers=list(TIERS),tolerances=dict(probability=PROBABILITY_EPSILON,wait_seconds=WAIT_EPSILON),objective=OBJECTIVE,scope=SCOPE)
    return dict(decisions=decisions,evidence=evidence)
