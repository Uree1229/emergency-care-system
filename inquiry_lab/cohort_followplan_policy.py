"""The intact Recourse menu, scored through each first patient's future H plan."""
from copy import deepcopy
import hashlib
from pathlib import Path
from . import cohort_recourse_policy as original,cohort_followplan_context as context,cohort_followplan_model as model

NAME='eat_cohort_followplan_l15_f'
BASE_POLICY='eat_cohort_recourse_l15_f'
BASE_SOURCE_SHA256='191591bc184ae3643c97d0c801800bae507675f94d603423f7553e00e317b961'
if hashlib.sha256(Path(original.__file__).read_bytes()).hexdigest()!=BASE_SOURCE_SHA256:
    raise ValueError('frozen first-followplan policy foundation differs')
HORIZON=original.HORIZON
JOINT=original.JOINT
TIERS=(4,2,1)
PROBABILITY_EPSILON=1e-12
WAIT_EPSILON=1e-9
RULE='intact_Recourse_menu_plus_first_patient_static_next_review_H_continuation'
OBJECTIVE='global tier 4,2,1 success sums within 1e-12, then capped wait within 1e-9; prefer Recourse actual index then original D menu order'
SCOPE='complete current WAITING/H-positive roster; initial first-cohort sends and fixed F0 work unchanged; separate hypothetical local H continuations for first patients, unchanged residual scores; no actual future-action or outcome guarantee'


def spec(name=NAME):
    if name!=NAME:raise ValueError('the fixed first-followplan candidate is '+NAME)
    value=original.spec(BASE_POLICY);value.update(name=NAME,label='EAT l15 f, first-patient next-review continuation')
    return value


def decide(name,*,now,candidates,observations=(),pending=(),patient_pending=(),max_new,last_contact_at=None,time_context):
    spec(name)
    value=original.decide(BASE_POLICY,now=now,candidates=candidates,observations=observations,pending=pending,
        patient_pending=patient_pending,max_new=max_new,last_contact_at=last_contact_at,time_context=time_context)
    value['evidence']['policy']=NAME
    value['evidence']['cohort_followplan']=dict(schema_version=1,mode='horizon_fallback',evaluated=False,intervened=False)
    return value


def _decision(proposal,hospitals,metrics):
    value=deepcopy(proposal);value['send']=list(hospitals);value['reason']='cohort_followplan_current_only'
    value['evidence']['policy']=NAME;value['evidence']['sent_hospitals']=list(hospitals)
    value['evidence']['planned_expected_cost']=round(metrics['total_cost_seconds'],3)
    for row in value['evidence']['candidate_plan']:row['planned_offset_seconds']=0 if row['hospital'] in hospitals else None
    return value


def decide_joint(name,*,members,joint_context,tail_members,priority_context,owner_context):
    spec(name);native=context.validate_context(joint_context)
    context.validate_tail_members(native,tail_members,members=members)
    priority=context.validate_priority_context(priority_context,joint_context=native,members=members,tail_members=tail_members)
    owners=context.validate_owner_context(owner_context,joint_context=native,members=members,tail_members=tail_members,priority_context=priority)
    bm=context.base_members_projection(members)
    proposal=original.decide_joint(BASE_POLICY,members=bm,joint_context=context.base_context_projection(native),
        tail_members=context.base_members_projection(tail_members),priority_context=priority,owner_context=context.base_owner_projection(owners))
    old=proposal['evidence'];evaluated=old['evaluated'];base_index=old['selected_index'];selected=base_index
    menu=deepcopy(old['menu_indices']);scores=[];selection=None
    if evaluated:
        if not 1<=len(menu)<=3:raise ValueError('first-followplan left intact Recourse menu')
        hedge=old['original_hedge_proposal'];dynamic=hedge['evidence']['original_dynamic_proposal']
        joint=original.original.original._joint(dynamic['evidence']['original_cohort_success_proposal'])['evidence']
        owner_by={context._key(o['patient']['id']):o for o in hedge['evidence']['owner_context']['inventory']}
        by_index={r['row_index']:r for r in old['row_scores']}
        for index in menu:
            base=deepcopy(by_index[index]);first_D=base['base_row_score']['first_cohort_score'];initial=base['first_cohort']
            reviews=model.first_review_sources(first_D,members=bm,current_baseline_proposals=joint['baseline_proposals'])
            first=[]
            for review in reviews:
                pid=review['patient_id'];jobs=[j for j in initial['jobs'] if context._key(j['patient_id'])==context._key(pid)]
                first.append(model.score_first_patient(owner_by[context._key(pid)],initial_jobs=jobs,
                    row_pending=first_D['projection']['final_pending'],all_jobs=initial['jobs'],next_review_at=review['next_review_at']))
            residual=deepcopy(base['owner_scores'])
            metrics=[dict(patient_id=s['patient_id'],effective_priority=s['effective_priority'],metrics=deepcopy(s['metrics'])) for s in first+residual]
            if sorted(context._key(m['patient_id']) for m in metrics)!=sorted(context._key(o['patient']['id']) for o in owners['inventory']):
                raise ValueError('complete first-followplan score partition differs')
            scores.append(dict(row_index=index,base_row_score=base,first_review_sources=reviews,first_cohort_future=first,
                owner_scores=residual,member_metrics=metrics,
                tier_probabilities={str(t):sum((m['metrics']['selection_probability'] for m in metrics if m['effective_priority']==t),0.0) for t in TIERS},
                sum_capped_wait_seconds=sum((m['metrics']['capped_wait_seconds'] for m in metrics),0.0)))
        selection=original.original.original._select(scores,base_index);selected=selection['selected_index']
    elif menu:raise ValueError('unevaluated Recourse unexpectedly has a menu')
    intervened=selected!=base_index
    if intervened:
        chosen=next(row for row in scores if row['row_index']==selected)
        decisions=[_decision(p,send,chosen['member_metrics'][i]['metrics']) for i,(p,send) in
            enumerate(zip(joint['baseline_proposals'],joint['combinations'][selected]['hospitals']))]
    else:decisions=deepcopy(proposal['decisions'])
    by_priority={context._key(p['patient_id']):p['effective_priority'] for p in priority['patients']}
    for i,decision in enumerate(decisions):
        decision['evidence']['policy']=NAME
        decision['evidence']['cohort_followplan']=dict(schema_version=1,mode='pair_cohort_followplan',evaluated=evaluated,
            intervened=intervened,member_index=i,base_selected_index=base_index,selected_index=selected,
            effective_priority=by_priority[context._key(members[i]['patient_id'])])
    evidence=dict(schema_version=1,rule=RULE,base_policy=BASE_POLICY,base_source_sha256=BASE_SOURCE_SHA256,
        identity_projection=dict(source_policy=NAME,calculation_policy=BASE_POLICY,
            fields=['joint_context.eligible[].job.policy_name','members[].workflow_snapshot.job.policy_name',
                'tail_members[].workflow_snapshot.job.policy_name','owner_context.inventory[].job.policy_name'],
            copied_only=True,public_identity_unchanged=True,foreign_base_owner_label='__foreign_cohort_recourse__'),
        joint_context=native,priority_context=priority,owner_context=owners,original_recourse_proposal=proposal,
        evaluated=evaluated,menu_indices=menu,row_scores=scores,selection=selection,
        base_selected_index=base_index,selected_index=selected,intervened=intervened,
        tiers=list(TIERS),tolerances=dict(probability=PROBABILITY_EPSILON,wait_seconds=WAIT_EPSILON),objective=OBJECTIVE,scope=SCOPE)
    return dict(decisions=decisions,evidence=evidence)
