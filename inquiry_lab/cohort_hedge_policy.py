"""The intact D menu plus complete public owners' static H-derived hedges."""
from copy import deepcopy
import hashlib
from pathlib import Path
from . import cohort_dynamic_policy as original,cohort_hedge_context as context,cohort_hedge_model as model

NAME='eat_cohort_hedge_l15_f'
BASE_POLICY='eat_cohort_dynamic_l15_f'
BASE_SOURCE_SHA256='62ef7fbba3ee90015f800aae7fb38f820955c7b92cd46d35df216c9a54afccfb'
if hashlib.sha256(Path(original.__file__).read_bytes()).hexdigest()!=BASE_SOURCE_SHA256:raise ValueError('frozen owner-hedge foundation differs')
HORIZON=original.HORIZON
JOINT=original.JOINT
TIERS=(4,2,1)
PROBABILITY_EPSILON=1e-12
WAIT_EPSILON=1e-9
RULE='intact_D_menu_plus_complete_waiting_owner_local_origin_H_hedges'
OBJECTIVE='global tier 4,2,1 success sums within 1e-12, then capped wait within 1e-9; prefer D actual index then D menu order'
SCOPE='complete current WAITING/H-positive roster; first D cohort reference metrics plus residual owner-local static hedges; no future events, owner-future contention or actual future H agreement'


def spec(name=NAME):
    if name!=NAME:raise ValueError('the fixed owner-hedge candidate is '+NAME)
    value=original.spec(BASE_POLICY);value.update(name=NAME,label='EAT l15 f, public owner hedge cohort score')
    return value


def decide(name,*,now,candidates,observations=(),pending=(),patient_pending=(),max_new,last_contact_at=None,time_context):
    spec(name)
    value=original.decide(BASE_POLICY,now=now,candidates=candidates,observations=observations,pending=pending,
        patient_pending=patient_pending,max_new=max_new,last_contact_at=last_contact_at,time_context=time_context)
    value['evidence']['policy']=NAME
    value['evidence']['cohort_hedge']=dict(schema_version=1,mode='horizon_fallback',evaluated=False,intervened=False)
    return value


def _decision(proposal,hospitals,metrics):
    value=deepcopy(proposal);value['send']=list(hospitals);value['reason']='cohort_hedge_current_only'
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
        joint_context=context.base_context_projection(native),tail_members=context.base_members_projection(tail_members),priority_context=priority)
    evaluated=proposal['evidence']['evaluated'];base_index=proposal['evidence']['selected_index'];selected=base_index
    menu=deepcopy(proposal['evidence']['menu_indices']);origins=[];scores=[];selection=None
    if evaluated:
        first_ids={context._key(pid) for pid in owners['first_cohort_patient_ids']}
        residual=[o for o in owners['inventory'] if context._key(o['patient']['id']) not in first_ids]
        origins=[model.freeze_owner(o) for o in residual]
        if not 1<=len(menu)<=3:raise ValueError('owner hedge left intact D menu')
        by_index={r['row_index']:r for r in proposal['evidence']['row_scores']}
        for index in menu:
            first=deepcopy(by_index[index]);pending=first['projection']['final_pending']
            owner_scores=[model.score_owner(owner,origin,pending) for owner,origin in zip(residual,origins)]
            metrics=deepcopy(first['member_metrics'])+[dict(patient_id=s['patient_id'],effective_priority=s['effective_priority'],metrics=deepcopy(s['metrics'])) for s in owner_scores]
            if sorted((context._key(m['patient_id']) for m in metrics))!=sorted(context._key(o['patient']['id']) for o in owners['inventory']):raise ValueError('complete score partition differs')
            scores.append(dict(row_index=index,first_cohort_score=first,owner_scores=owner_scores,member_metrics=metrics,
                tier_probabilities={str(t):sum((m['metrics']['selection_probability'] for m in metrics if m['effective_priority']==t),0.0) for t in TIERS},
                sum_capped_wait_seconds=sum((m['metrics']['capped_wait_seconds'] for m in metrics),0.0)))
        selection=original._select(scores,base_index);selected=selection['selected_index']
    elif menu:raise ValueError('unevaluated D unexpectedly has a menu')
    intervened=selected!=base_index
    if intervened:
        joint=original._joint(proposal['evidence']['original_cohort_success_proposal'])['evidence']
        chosen=next(row for row in scores if row['row_index']==selected)
        decisions=[_decision(p,send,chosen['member_metrics'][i]['metrics']) for i,(p,send) in
            enumerate(zip(joint['baseline_proposals'],joint['combinations'][selected]['hospitals']))]
    else:decisions=deepcopy(proposal['decisions'])
    by_priority={context._key(p['patient_id']):p['effective_priority'] for p in priority['patients']}
    for i,decision in enumerate(decisions):
        decision['evidence']['policy']=NAME
        decision['evidence']['cohort_hedge']=dict(schema_version=1,mode='pair_cohort_hedge',evaluated=evaluated,
            intervened=intervened,member_index=i,base_selected_index=base_index,selected_index=selected,
            effective_priority=by_priority[context._key(members[i]['patient_id'])])
    evidence=dict(schema_version=1,rule=RULE,base_policy=BASE_POLICY,base_source_sha256=BASE_SOURCE_SHA256,
        identity_projection=dict(source_policy=NAME,calculation_policy=BASE_POLICY,
            fields=['joint_context.eligible[].job.policy_name','members[].workflow_snapshot.job.policy_name','tail_members[].workflow_snapshot.job.policy_name'],
            copied_only=True,public_identity_unchanged=True),joint_context=native,priority_context=priority,owner_context=owners,
        original_dynamic_proposal=proposal,evaluated=evaluated,menu_indices=menu,owner_origins=origins,row_scores=scores,
        selection=selection,base_selected_index=base_index,selected_index=selected,intervened=intervened,
        tiers=list(TIERS),tolerances=dict(probability=PROBABILITY_EPSILON,wait_seconds=WAIT_EPSILON),objective=OBJECTIVE,scope=SCOPE)
    return dict(decisions=decisions,evidence=evidence)
