"""At most three current rows, each followed by row-specific frozen CS.

All projections use the current public clock. They evaluate a reference-policy
continuation, not the new policy's guaranteed later actions. Already-queried
owners remain public pending load and are outside the scored first cohort.
"""
from copy import deepcopy
import hashlib
from pathlib import Path

from . import cohort_success_policy as original,cohort_dynamic_context as context

NAME='eat_cohort_dynamic_l15_f'
BASE_POLICY='eat_cohort_success_l15_f'
BASE_SOURCE_SHA256='5c9e41de5fb5f83451639dd41505998d31c2551868d6ad9cc231ac20205b304b'
if hashlib.sha256(Path(original.__file__).read_bytes()).hexdigest()!=BASE_SOURCE_SHA256:
    raise ValueError('frozen dynamic cohort foundation differs')
JOINT=original.JOINT
HORIZON=original.HORIZON
RULE='three_reference_rows_with_row_specific_same_clock_CS_continuation'
TIERS=(4,2,1)
PROBABILITY_EPSILON=1e-12
WAIT_EPSILON=1e-9
OBJECTIVE='global tier 4,2,1 success sums within 1e-12, then capped wait within 1e-9; prefer CS index then menu order'
SCOPE='current eligible first-contact cohort only; frozen CS reference continuation; no clock advance or already-queried-owner score; actual new-policy later actions may differ'


def spec(name=NAME):
    if name!=NAME:raise ValueError('the fixed dynamic cohort candidate is '+NAME)
    value=original.spec(BASE_POLICY)
    value.update(name=NAME,label='EAT l15 f, row-specific reference cohort continuation')
    return value


def decide(name,*,now,candidates,observations=(),pending=(),patient_pending=(),max_new,last_contact_at=None,time_context):
    spec(name)
    result=original.decide(BASE_POLICY,now=now,candidates=candidates,observations=observations,pending=pending,
        patient_pending=patient_pending,max_new=max_new,last_contact_at=last_contact_at,time_context=time_context)
    result['evidence']['policy']=NAME
    result['evidence']['cohort_dynamic']=dict(schema_version=1,mode='horizon_fallback',evaluated=False,intervened=False)
    return result


def _joint(proposal):
    return proposal['evidence']['original_budget_proposal']['evidence']['original_tail_proposal']['evidence']['original_joint_proposal']


def _append(ledger,members,sends,*,now,kind,step_index,path):
    rows=[]
    for mi,(member,hospitals) in enumerate(zip(members,sends)):
        for si,hospital in enumerate(hospitals):
            inquiry=dict(hospital=hospital,patient=member['patient_id'],submitted_at=now)
            ledger.append(deepcopy(inquiry))
            rows.append(dict(inquiry=inquiry,source=dict(kind=kind,step_index=step_index,member_index=mi,
                send_index=si,proposal_path=path.format(member=mi,send=si))))
    return rows


def _score(row_index,menu_position,hospitals,*,members,tail_members,joint_context,priority_context,current_models):
    now=joint_context['now'];ledger=deepcopy(members[0]['inputs']['pending']);initial_pending=deepcopy(ledger)
    values=[];priorities={(type(r['patient_id']),r['patient_id']):r['effective_priority'] for r in priority_context['patients']}
    def collect(people,metrics):
        for member,metric in zip(people,metrics):
            pid=member['patient_id'];values.append(dict(patient_id=pid,
                effective_priority=priorities[type(pid),pid],metrics=deepcopy(metric)))
    current=JOINT._combination(current_models,hospitals);collect(members,current['member_metrics'])
    start_path=('original_cohort_success_proposal.evidence.original_budget_proposal.evidence.'
        'original_tail_proposal.evidence.original_joint_proposal.evidence.combinations['+str(row_index)+'].hospitals[{member}][{send}]')
    initial=_append(ledger,members,hospitals,now=now,kind='current_pair_row',step_index=None,path=start_path)
    prefix='row_scores['+str(menu_position)+'].projection.'
    steps=[]
    for step_index in range(len(tail_members)//2):
        start=2*step_index
        remaining=deepcopy(tail_members[start:])
        for member in remaining:member['inputs']['pending']=deepcopy(ledger)
        pair,tail=remaining[:2],remaining[2:]
        suffix=deepcopy(joint_context);suffix['eligible']=suffix['eligible'][start+2:]
        suffix['pair_patient_ids']=[m['patient_id'] for m in pair]
        raw_priority=[{k:v for k,v in row.items() if k!='effective_priority'} for row in priority_context['patients'][start+2:]]
        priority=original.context.build_priority_context(joint_context=suffix,members=pair,tail_members=tail,priority_rows=raw_priority)
        proposal=original.decide_joint(BASE_POLICY,members=pair,joint_context=suffix,tail_members=tail,priority_context=priority)
        models=[JOINT._model(m['inputs'],p) for m,p in zip(pair,_joint(proposal)['evidence']['baseline_proposals'])]
        sends=[d['send'] for d in proposal['decisions']]
        combined=JOINT._combination(models,sends);collect(pair,combined['member_metrics'])
        appends=_append(ledger,pair,sends,now=now,kind='reference_CS_selected',step_index=step_index,
            path=prefix+'steps['+str(step_index)+'].proposal.decisions[{member}].send[{send}]')
        steps.append(dict(step_index=step_index,source_tail_member_indices=[start,start+1],joint_context=suffix,
            priority_context=priority,members=pair,tail_members=tail,proposal=proposal,appended_inquiries=appends))
    singleton=None
    if len(tail_members)%2:
        member=deepcopy(tail_members[-1]);member['inputs']['pending']=deepcopy(ledger)
        proposal=HORIZON.decide(HORIZON.NAME,**deepcopy(member['inputs']))
        model=JOINT._model(member['inputs'],proposal)
        collect([member],[JOINT._metrics(model,proposal['send'],{})])
        appends=_append(ledger,[member],[proposal['send']],now=now,kind='reference_H_singleton',step_index=len(steps),
            path=prefix+'singleton.proposal.send[{send}]')
        singleton=dict(member=member,proposal=proposal,appended_inquiries=appends)
    return dict(row_index=row_index,member_metrics=values,
        tier_probabilities={str(tier):sum((m['metrics']['selection_probability'] for m in values
            if m['effective_priority']==tier),0.0) for tier in TIERS},
        sum_capped_wait_seconds=sum((m['metrics']['capped_wait_seconds'] for m in values),0.0),
        projection=dict(current_pair_send=deepcopy(hospitals),initial_pending=initial_pending,
            initial_appended_inquiries=initial,steps=steps,singleton=singleton,final_pending=deepcopy(ledger)))


def _select(scores,base_index):
    survivors=list(scores);filters=[]
    for tier in TIERS:
        maximum=max(r['tier_probabilities'][str(tier)] for r in survivors)
        survivors=[r for r in survivors if r['tier_probabilities'][str(tier)]>=maximum-PROBABILITY_EPSILON]
        filters.append(dict(tier=tier,maximum_probability=maximum,surviving_row_indices=[r['row_index'] for r in survivors]))
    minimum=min(r['sum_capped_wait_seconds'] for r in survivors)
    survivors=[r for r in survivors if r['sum_capped_wait_seconds']<=minimum+WAIT_EPSILON]
    indices=[r['row_index'] for r in survivors];selected=base_index if base_index in indices else indices[0]
    return dict(priority_filters=filters,minimum_wait_seconds=minimum,wait_surviving_row_indices=indices,
        base_selected_tied=base_index in indices,selected_index=selected)


def _decision(proposal,hospitals,metrics):
    result=deepcopy(proposal);result['send']=list(hospitals);result['reason']='cohort_dynamic_current_only'
    evidence=result['evidence'];evidence['policy']=NAME;evidence['sent_hospitals']=list(hospitals)
    evidence['planned_expected_cost']=round(metrics['total_cost_seconds'],3)
    for row in evidence['candidate_plan']:row['planned_offset_seconds']=0 if row['hospital'] in hospitals else None
    return result


def decide_joint(name,*,members,joint_context,tail_members,priority_context):
    spec(name);native=context.validate_context(joint_context)
    context.validate_tail_members(native,tail_members,members=members)
    priority=context.validate_priority_context(priority_context,joint_context=native,members=members,tail_members=tail_members)
    cs_members=context.base_members_projection(members);cs_tails=context.base_members_projection(tail_members)
    cs_context=context.base_context_projection(native)
    proposal=original.decide_joint(BASE_POLICY,members=cs_members,joint_context=cs_context,
        tail_members=cs_tails,priority_context=priority)
    evaluated=proposal['evidence']['evaluated'];base_index=proposal['evidence']['selected_index']
    joint=_joint(proposal);table=joint['evidence']['combinations'];menu=[];scores=[];selection=None;selected=base_index
    if evaluated:
        budget=proposal['evidence']['original_budget_proposal']
        for index in (base_index,proposal['evidence']['base_budget_selected_index'],
                budget['evidence']['original_tail_proposal']['evidence']['actual_selected_index']):
            if index not in menu:menu.append(index)
        if len(menu)>3 or any(not table[i]['feasible'] for i in menu):raise ValueError('dynamic menu left original feasible rows')
        models=[JOINT._model(m['inputs'],p) for m,p in zip(cs_members,joint['evidence']['baseline_proposals'])]
        for position,index in enumerate(menu):
            scores.append(_score(index,position,table[index]['hospitals'],members=cs_members,tail_members=cs_tails,
                joint_context=cs_context,priority_context=priority,current_models=models))
        selection=_select(scores,base_index);selected=selection['selected_index']
    intervened=selected!=base_index
    if intervened:
        chosen=next(s for s in scores if s['row_index']==selected)
        decisions=[_decision(p,send,chosen['member_metrics'][i]['metrics']) for i,(p,send) in
            enumerate(zip(joint['evidence']['baseline_proposals'],table[selected]['hospitals']))]
    else:decisions=deepcopy(proposal['decisions'])
    priorities={(type(r['patient_id']),r['patient_id']):r['effective_priority'] for r in priority['patients']}
    for i,decision in enumerate(decisions):
        pid=members[i]['patient_id'];decision['evidence']['policy']=NAME
        decision['evidence']['cohort_dynamic']=dict(schema_version=1,mode='pair_cohort_dynamic',evaluated=evaluated,
            intervened=intervened,member_index=i,base_selected_index=base_index,selected_index=selected,
            effective_priority=priorities[type(pid),pid])
    evidence=dict(schema_version=1,rule=RULE,base_policy=BASE_POLICY,base_source_sha256=BASE_SOURCE_SHA256,
        identity_projection=dict(source_policy=NAME,calculation_policy=BASE_POLICY,
            fields=['joint_context.eligible[].job.policy_name','members[].workflow_snapshot.job.policy_name',
                'tail_members[].workflow_snapshot.job.policy_name'],copied_only=True,public_identity_unchanged=True),
        joint_context=native,priority_context=priority,original_cohort_success_proposal=proposal,evaluated=evaluated,
        menu_indices=menu,row_scores=scores,selection=selection,base_selected_index=base_index,selected_index=selected,
        intervened=intervened,tiers=list(TIERS),tolerances=dict(probability=PROBABILITY_EPSILON,wait_seconds=WAIT_EPSILON),
        objective=OBJECTIVE,scope=SCOPE)
    return dict(decisions=decisions,evidence=evidence)
