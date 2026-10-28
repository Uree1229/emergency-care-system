"""The intact Followplan menu with chronological mean future-queue progression."""
from copy import deepcopy
import hashlib
from pathlib import Path
from . import cohort_followplan_policy as original,cohort_advance_context as context,cohort_advance_model as model

NAME='eat_cohort_advance_l15_f'
BASE_POLICY='eat_cohort_followplan_l15_f'
BASE_SOURCE_SHA256='835c700739170a690ddce4cf26395861d300d950ad370dc292550a55d5362cb4'
if hashlib.sha256(Path(original.__file__).read_bytes()).hexdigest()!=BASE_SOURCE_SHA256:
    raise ValueError('frozen first-advance policy foundation differs')
HORIZON=original.HORIZON
JOINT=original.JOINT
D=original.original.original.original
TIERS=(4,2,1)
PROBABILITY_EPSILON=1e-12
WAIT_EPSILON=1e-9
RULE='intact_Followplan_menu_frozen_first_future_plans_with_chronological_mean_progression'
OBJECTIVE='global tier 4,2,1 success sums within 1e-12, then capped wait within 1e-9; prefer Followplan actual index then original D menu order'
SCOPE='complete current WAITING/H-positive roster; initial CDFs, full future plan membership/times and residual scores fixed; one chronological mean first-future-work pass, approximate stochastic residuals; current pair applied, no actual capacity release, future action, reservation or outcome guarantee'


def spec(name=NAME):
    if name!=NAME:raise ValueError('the fixed first-advance candidate is '+NAME)
    value=original.spec(BASE_POLICY);value.update(name=NAME,label='EAT l15 f, chronological mean future queues')
    return value


def decide(name,*,now,candidates,observations=(),pending=(),patient_pending=(),max_new,last_contact_at=None,time_context):
    spec(name)
    value=original.decide(BASE_POLICY,now=now,candidates=candidates,observations=observations,pending=pending,
        patient_pending=patient_pending,max_new=max_new,last_contact_at=last_contact_at,time_context=time_context)
    value['evidence']['policy']=NAME
    value['evidence']['cohort_advance']=dict(schema_version=1,mode='horizon_fallback',evaluated=False,intervened=False)
    return value


def _decision(proposal,hospitals,metrics):
    value=deepcopy(proposal);value['send']=list(hospitals);value['reason']='cohort_advance_current_only'
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
    menu=deepcopy(old['menu_indices']);scores=[];selection=None
    if evaluated:
        if not 1<=len(menu)<=3:raise ValueError('first-advance left intact Followplan menu')
        hedge=old['original_recourse_proposal']['evidence']['original_hedge_proposal']
        dynamic=hedge['evidence']['original_dynamic_proposal']
        joint=D._joint(dynamic['evidence']['original_cohort_success_proposal'])['evidence']
        by_index={r['row_index']:r for r in old['row_scores']}
        for index in menu:
            parent=deepcopy(by_index[index]);first=model.score_first_cohort(parent['first_cohort_future'])
            residual=deepcopy(parent['owner_scores'])
            metrics=[dict(patient_id=s['patient_id'],effective_priority=s['effective_priority'],metrics=deepcopy(s['metrics']))
                for s in first['patient_scores']+residual]
            if sorted(context._key(m['patient_id']) for m in metrics)!=sorted(context._key(o['patient']['id']) for o in owners['inventory']):
                raise ValueError('complete first-advance score partition differs')
            scores.append(dict(row_index=index,base_row_score=parent,first_cohort_advance=first,
                owner_scores=residual,member_metrics=metrics,
                tier_probabilities={str(t):sum((m['metrics']['selection_probability'] for m in metrics if m['effective_priority']==t),0.0) for t in TIERS},
                sum_capped_wait_seconds=sum((m['metrics']['capped_wait_seconds'] for m in metrics),0.0)))
        selection=D._select(scores,base_index);selected=selection['selected_index']
    elif menu:raise ValueError('unevaluated Followplan unexpectedly has a menu')
    intervened=selected!=base_index
    if intervened:
        chosen=next(row for row in scores if row['row_index']==selected)
        decisions=[_decision(p,send,chosen['member_metrics'][i]['metrics']) for i,(p,send) in
            enumerate(zip(joint['baseline_proposals'],joint['combinations'][selected]['hospitals']))]
    else:decisions=deepcopy(proposal['decisions'])
    by_priority={context._key(p['patient_id']):p['effective_priority'] for p in priority['patients']}
    for i,decision in enumerate(decisions):
        decision['evidence']['policy']=NAME
        decision['evidence']['cohort_advance']=dict(schema_version=1,mode='pair_cohort_advance',evaluated=evaluated,
            intervened=intervened,member_index=i,base_selected_index=base_index,selected_index=selected,
            effective_priority=by_priority[context._key(members[i]['patient_id'])])
    evidence=dict(schema_version=1,rule=RULE,base_policy=BASE_POLICY,base_source_sha256=BASE_SOURCE_SHA256,
        identity_projection=dict(source_policy=NAME,calculation_policy=BASE_POLICY,
            fields=['joint_context.eligible[].job.policy_name','members[].workflow_snapshot.job.policy_name',
                'tail_members[].workflow_snapshot.job.policy_name','owner_context.inventory[].job.policy_name'],
            copied_only=True,public_identity_unchanged=True,foreign_base_owner_label='__foreign_cohort_followplan__'),
        joint_context=native,priority_context=priority,owner_context=owners,original_followplan_proposal=proposal,
        evaluated=evaluated,menu_indices=menu,row_scores=scores,selection=selection,
        base_selected_index=base_index,selected_index=selected,intervened=intervened,
        tiers=list(TIERS),tolerances=dict(probability=PROBABILITY_EPSILON,wait_seconds=WAIT_EPSILON),objective=OBJECTIVE,scope=SCOPE)
    return dict(decisions=decisions,evidence=evidence)
