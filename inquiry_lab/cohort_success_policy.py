"""Priority-tier model success first, then wait, at frozen B cancellations.

Only current pair membership is applied. Prospective first-contact sends form
a fixed copied continuation template, not a promised future H/JOINT decision.
"""
from copy import deepcopy
import hashlib
from pathlib import Path

from . import joint_budget_policy as original, cohort_success_context as context
from . import joint_budget_context, joint_tail_context

NAME='eat_cohort_success_l15_f'
BASE_POLICY='eat_joint_budget_l15_f'
BASE_SOURCE_SHA256='5707df3632d99ae453fe1e1ecb3dcc6fc3882c729e9a9119f4e791d107e2808a'
if hashlib.sha256(Path(original.__file__).read_bytes()).hexdigest()!=BASE_SOURCE_SHA256:
    raise ValueError('frozen cohort-success budget foundation differs')
JOINT=original.original.original
HORIZON=JOINT.original
RULE='priority_tier_success_then_wait_with_fixed_budget_winner_continuation'
TIERS=(4,2,1)
PROBABILITY_EPSILON=1e-12
WAIT_EPSILON=1e-9
OBJECTIVE='global tier 4,2,1 success-probability sums within 1e-12, then global capped wait within 1e-9; B row then original table order'
SCOPE='current eligible first-contact cohort only; fixed continuation approximation; existing pair P/cost feasibility retained; no outcome or future action guarantee'
TEMPLATE_SCOPE='copied B winner immediate sends; singleton H optimized once at reference final pending; candidate ledgers change models only'


def spec(name=NAME):
    if name!=NAME:raise ValueError('the fixed cohort-success candidate is '+NAME)
    value=original.spec(BASE_POLICY)
    value.update(name=NAME,label='EAT l15 f, priority-tier cohort success before wait')
    return value


def decide(name,*,now,candidates,observations=(),pending=(),patient_pending=(),max_new,last_contact_at=None,time_context):
    spec(name)
    result=original.decide(BASE_POLICY,now=now,candidates=candidates,observations=observations,pending=pending,
        patient_pending=patient_pending,max_new=max_new,last_contact_at=last_contact_at,time_context=time_context)
    result['evidence']['policy']=NAME
    result['evidence']['cohort_success']=dict(schema_version=1,mode='horizon_fallback',evaluated=False,intervened=False)
    return result


def _first_members(members):
    return joint_tail_context.base_members_projection(joint_budget_context.base_members_projection(
        context.base_members_projection(members)))


def _append(ledger,members,sends,now):
    for member,hospitals in zip(members,sends):
        for hospital in hospitals:
            ledger.append(dict(hospital=hospital,patient=member['patient_id'],submitted_at=now))


def _template(budget,tail_members):
    projection=budget['evidence']['projections']['winner']
    steps=[]
    for step in projection['steps']:
        steps.append(dict(step_index=step['step_index'],source_budget_projection_step_index=step['step_index'],
            members=deepcopy(step['members']),
            horizon_proposals=deepcopy(step['proposal']['evidence']['baseline_proposals']),
            sends=[list(d['send']) for d in step['proposal']['decisions']]))
    singleton=None
    if len(tail_members)%2:
        member=_first_members(tail_members[-1:])[0]
        member['inputs']['pending']=deepcopy(projection['final_pending'])
        proposal=HORIZON.decide(HORIZON.NAME,**deepcopy(member['inputs']))
        singleton=dict(member=member,horizon_proposal=proposal,send=list(proposal['send']))
    return dict(source_path='original_budget_proposal.evidence.projections.winner',paired_steps=steps,
        singleton=singleton,reference_final_pending=deepcopy(projection['final_pending']),scope=TEMPLATE_SCOPE)


def _score(row_index,hospitals,*,members,current_models,template,priorities,now):
    current=JOINT._combination(current_models,hospitals)
    values=[]
    def collect(people,metrics):
        for member,metric in zip(people,metrics):
            pid=member['patient_id']
            values.append(dict(patient_id=pid,effective_priority=priorities[type(pid),pid],metrics=deepcopy(metric)))
    collect(members,current['member_metrics'])
    ledger=deepcopy(members[0]['inputs']['pending'])
    _append(ledger,members,hospitals,now)
    for step in template['paired_steps']:
        people=deepcopy(step['members'])
        for member in people:member['inputs']['pending']=deepcopy(ledger)
        models=[JOINT._model(member['inputs'],proposal) for member,proposal in zip(people,step['horizon_proposals'])]
        combo=JOINT._combination(models,step['sends'])
        collect(people,combo['member_metrics'])
        _append(ledger,people,step['sends'],now)
    if template['singleton'] is not None:
        saved=template['singleton'];member=deepcopy(saved['member'])
        member['inputs']['pending']=deepcopy(ledger)
        model=JOINT._model(member['inputs'],saved['horizon_proposal'])
        collect([member],[JOINT._metrics(model,saved['send'],{})])
    return dict(row_index=row_index,member_metrics=values,
        tier_probabilities={str(tier):sum((r['metrics']['selection_probability'] for r in values
            if r['effective_priority']==tier),0.0) for tier in TIERS},
        sum_capped_wait_seconds=sum((r['metrics']['capped_wait_seconds'] for r in values),0.0))


def _select(scores,base_index):
    survivors=list(scores);filters=[]
    for tier in TIERS:
        maximum=max(row['tier_probabilities'][str(tier)] for row in survivors)
        survivors=[row for row in survivors if row['tier_probabilities'][str(tier)]>=maximum-PROBABILITY_EPSILON]
        filters.append(dict(tier=tier,maximum_probability=maximum,
            surviving_row_indices=[row['row_index'] for row in survivors]))
    minimum=min(row['sum_capped_wait_seconds'] for row in survivors)
    survivors=[row for row in survivors if row['sum_capped_wait_seconds']<=minimum+WAIT_EPSILON]
    indices=[row['row_index'] for row in survivors]
    selected=base_index if base_index in indices else indices[0]
    return dict(priority_filters=filters,minimum_wait_seconds=minimum,
        wait_surviving_row_indices=indices,base_budget_tied=base_index in indices,selected_index=selected)


def _decision(proposal,hospitals,metrics):
    result=deepcopy(proposal)
    result['send']=list(hospitals);result['reason']='cohort_success_current_only'
    evidence=result['evidence'];evidence['policy']=NAME;evidence['sent_hospitals']=list(hospitals)
    evidence['planned_expected_cost']=round(metrics['total_cost_seconds'],3)
    for row in evidence['candidate_plan']:
        row['planned_offset_seconds']=0 if row['hospital'] in hospitals else None
    return result


def decide_joint(name,*,members,joint_context,tail_members,priority_context):
    spec(name)
    native=context.validate_context(joint_context)
    context.validate_tail_members(native,tail_members,members=members)
    priority=context.validate_priority_context(priority_context,joint_context=native,members=members,tail_members=tail_members)
    budget=original.decide_joint(BASE_POLICY,members=context.base_members_projection(members),
        joint_context=context.base_context_projection(native),tail_members=context.base_members_projection(tail_members))
    evaluated=budget['evidence']['cancelled_tail_veto']
    base_index=budget['evidence']['actual_selected_index']
    selected=base_index;template=None;scores=[];selection=None
    priorities={(type(row['patient_id']),row['patient_id']):row['effective_priority'] for row in priority['patients']}
    joint=budget['evidence']['original_tail_proposal']['evidence']['original_joint_proposal']
    if evaluated:
        template=_template(budget,tail_members)
        first=_first_members(members)
        current_models=[JOINT._model(member['inputs'],proposal) for member,proposal in
            zip(first,joint['evidence']['baseline_proposals'])]
        for row_index,row in enumerate(joint['evidence']['combinations']):
            if row['feasible']:
                scores.append(_score(row_index,row['hospitals'],members=first,current_models=current_models,
                    template=template,priorities=priorities,now=native['now']))
        selection=_select(scores,base_index);selected=selection['selected_index']
    intervened=selected!=base_index
    if intervened:
        chosen=next(score for score in scores if score['row_index']==selected)
        hospitals=joint['evidence']['combinations'][selected]['hospitals']
        decisions=[_decision(proposal,send,chosen['member_metrics'][i]['metrics']) for i,(proposal,send) in
            enumerate(zip(joint['evidence']['baseline_proposals'],hospitals))]
    else:decisions=deepcopy(budget['decisions'])
    for index,decision in enumerate(decisions):
        pid=members[index]['patient_id'];decision['evidence']['policy']=NAME
        decision['evidence']['cohort_success']=dict(schema_version=1,mode='pair_cohort_success',
            evaluated=evaluated,intervened=intervened,member_index=index,base_budget_selected_index=base_index,
            selected_index=selected,effective_priority=priorities[type(pid),pid])
    evidence=dict(schema_version=1,rule=RULE,base_policy=BASE_POLICY,base_source_sha256=BASE_SOURCE_SHA256,
        identity_projection=dict(source_policy=NAME,calculation_policy=BASE_POLICY,
            fields=['joint_context.eligible[].job.policy_name','members[].workflow_snapshot.job.policy_name',
                'tail_members[].workflow_snapshot.job.policy_name'],copied_only=True,public_identity_unchanged=True),
        joint_context=native,priority_context=priority,original_budget_proposal=budget,evaluated=evaluated,
        continuation_template=template,row_scores=scores,selection=selection,base_budget_selected_index=base_index,
        selected_index=selected,intervened=intervened,tiers=list(TIERS),
        tolerances=dict(probability=PROBABILITY_EPSILON,wait_seconds=WAIT_EPSILON),objective=OBJECTIVE,scope=SCOPE)
    return dict(decisions=decisions,evidence=evidence)
