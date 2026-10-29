"""Extend complete-owner evaluation to otherwise inactive no-tail first pairs."""
from copy import deepcopy
import hashlib
from pathlib import Path
from . import cohort_envelope_policy as original,cohort_pairscope_context as context,cohort_pairscope_model as model

NAME='eat_cohort_pairscope_l15_f'
BASE_POLICY='eat_cohort_envelope_l15_f'
BASE_SOURCE_SHA256='6e4f54f71ad33fc37293fa4d5d389ae35209b46f4b187c0eff0aaa9650db3fc4'
if hashlib.sha256(Path(original.__file__).read_bytes()).hexdigest()!=BASE_SOURCE_SHA256:
    raise ValueError('frozen pair-scope Envelope policy differs')
HORIZON=original.HORIZON
JOINT=original.JOINT
TIERS=(4,2,1)
PROBABILITY_EPSILON=1e-12
WAIT_EPSILON=1e-9
RULE='inherited_Envelope_or_exact_two_eligible_first_contacts_with_complete_owner_guard'
OBJECTIVE='new no-tail rows retain original JOINT feasibility and cardinalities; uniform frozen Envelope scores, unchanged per-owner probability guard within1e-12, minimum-wait band within1e-9 then tier4,2,1 and original Stoploss actual anchor; inherited-active rows/actions untouched'
SCOPE='single activation-scope extension to exact-two eligible first contacts, not only two waiting owners; all captured residual owners protected with fresh original-prefix origins; inherited active and one-tail paths retained, current pair actions only; no future inputs or realized success guarantee'


def spec(name=NAME):
    if name!=NAME:raise ValueError('the fixed pair-scope candidate is '+NAME)
    value=original.spec(BASE_POLICY);value.update(name=NAME,label='EAT l15 f, complete-owner no-tail pair scope')
    return value


def decide(name,*,now,candidates,observations=(),pending=(),patient_pending=(),max_new,last_contact_at=None,time_context):
    spec(name)
    value=original.decide(BASE_POLICY,now=now,candidates=candidates,observations=observations,pending=pending,
        patient_pending=patient_pending,max_new=max_new,last_contact_at=last_contact_at,time_context=time_context)
    value['evidence']['policy']=NAME
    value['evidence']['cohort_pairscope']=dict(schema_version=1,mode='horizon_fallback',evaluated=False,intervened=False)
    return value


def _decision(proposal,hospitals,metrics):
    value=deepcopy(proposal);value['send']=list(hospitals);value['reason']='cohort_pairscope_current_only'
    value['evidence']['policy']=NAME;value['evidence']['sent_hospitals']=list(hospitals)
    value['evidence']['planned_expected_cost']=round(metrics['total_cost_seconds'],3)
    for row in value['evidence']['candidate_plan']:row['planned_offset_seconds']=0 if row['hospital'] in hospitals else None
    return value


def _prepare(full,members,tails,native,priority):
    ms=members;ts=tails;joint_context=native
    for projection in (context,original.context,original.original.context,original.original.original.context,original.original.original.original.context):
        ms=projection.base_members_projection(ms);ts=projection.base_members_projection(ts)
        joint_context=projection.base_context_projection(joint_context)
    return model.prepare(full['original_stoploss_proposal'],members=ms,tail_members=ts,joint_context=joint_context,priority_context=priority)


def decide_joint(name,*,members,joint_context,tail_members,priority_context,owner_context):
    spec(name);native=context.validate_context(joint_context)
    context.validate_tail_members(native,tail_members,members=members)
    priority=context.validate_priority_context(priority_context,joint_context=native,members=members,tail_members=tail_members)
    owners=context.validate_owner_context(owner_context,joint_context=native,members=members,tail_members=tail_members,priority_context=priority)
    proposal=original.decide_joint(BASE_POLICY,members=context.base_members_projection(members),
        joint_context=context.base_context_projection(native),tail_members=context.base_members_projection(tail_members),
        priority_context=priority,owner_context=context.base_owner_projection(owners))
    old=proposal['evidence'];inherited=old['evaluated'];newly=not inherited and tail_members==[];evaluated=inherited or newly
    if type(inherited) is not bool:raise ValueError('typed inherited activation required')
    base_index=old['selected_index'];selected=base_index;anchor=old['stoploss_anchor_index']
    wait=old['original_waitpareto_proposal']['evidence'];pareto=wait['original_pareto_proposal']['evidence'];full=pareto['original_fullmenu_proposal']['evidence']
    if anchor!=wait['stoploss_anchor_index'] or anchor!=pareto['stoploss_anchor_index'] or anchor!=full['base_selected_index'] or anchor!=full['original_stoploss_proposal']['evidence']['selected_index']:
        raise ValueError('original Stoploss actual anchor differs')
    scores=[];origins=[];menu=[];feasible=[];guard=deepcopy(old['owner_probability_guard']);selection=deepcopy(old['protected_wait_selection']);coverage=None
    if newly:
        if len(native['eligible'])!=2 or len(members)!=2:raise ValueError('no-tail activation must contain exactly two eligible first contacts')
        joint=model.fullmenu.parent_views(full['original_stoploss_proposal'])['joint']
        built=model.build_menu(joint['combinations'],anchor);menu=built['menu_indices'];feasible=built['feasible_row_indices']
        prepared=_prepare(full,members,tail_members,native,priority);origins=deepcopy(prepared['origins'])
        scores=[model.score_row(index,position,prepared) for position,index in enumerate(menu)]
        guard=original.protection.guard_rows(scores,baseline_index=anchor,owner_inventory=owners['inventory'],menu_indices=menu,feasible_row_indices=feasible)
        selection=original.selector.select_protected_wait(scores,admissible_row_indices=guard['admissible_row_indices'],
            baseline_index=anchor,owner_inventory=owners['inventory'],menu_indices=menu,feasible_row_indices=feasible)
        selected=selection['selected_index']
        coverage=dict(original_joint_row_count=len(joint['combinations']),evaluated_row_count=len(scores),feasible_row_count=len(feasible),
            fresh_residual_origin_count=len(origins),captured_owner_count=len(owners['inventory']),first_contact_count=2,anchor_originally_feasible=True)
    intervened=selected!=base_index
    if intervened:
        chosen=next(row for row in scores if row['row_index']==selected)
        decisions=[_decision(p,send,chosen['member_metrics'][i]['metrics']) for i,(p,send) in enumerate(zip(joint['baseline_proposals'],joint['combinations'][selected]['hospitals']))]
    else:decisions=deepcopy(proposal['decisions'])
    priorities={context._key(p['patient_id']):p['effective_priority'] for p in priority['patients']}
    reason='inherited_active' if inherited else 'new_no_tail_first_pair' if newly else 'inherited_inactive_with_tail'
    for i,decision in enumerate(decisions):
        decision['evidence']['policy']=NAME
        decision['evidence']['cohort_pairscope']=dict(schema_version=1,mode='pair_cohort_pairscope',evaluated=evaluated,
            inherited_evaluated=inherited,newly_activated=newly,intervened=intervened,member_index=i,
            base_selected_index=base_index,stoploss_anchor_index=anchor,selected_index=selected,effective_priority=priorities[context._key(members[i]['patient_id'])])
    evidence=dict(schema_version=1,rule=RULE,base_policy=BASE_POLICY,base_source_sha256=BASE_SOURCE_SHA256,scoring_source_sha256=deepcopy(model.SOURCE_SHA256),
        identity_projection=dict(source_policy=NAME,calculation_policy=BASE_POLICY,
            fields=['joint_context.eligible[].job.policy_name','members[].workflow_snapshot.job.policy_name','tail_members[].workflow_snapshot.job.policy_name','owner_context.inventory[].job.policy_name'],
            copied_only=True,public_identity_unchanged=True,foreign_base_owner_label='__foreign_cohort_envelope__'),
        joint_context=native,priority_context=priority,owner_context=owners,original_envelope_proposal=proposal,
        evaluated=evaluated,inherited_evaluated=inherited,newly_activated=newly,activation_reason=reason,
        new_owner_origins=origins,new_menu_indices=menu,new_feasible_row_indices=feasible,new_row_scores=scores,new_coverage=coverage,
        score_source='new_row_scores' if newly else 'original_envelope_proposal.evidence.new_row_scores' if inherited else 'none',
        owner_probability_guard=guard,protected_wait_selection=selection,base_selected_index=base_index,stoploss_anchor_index=anchor,
        selected_index=selected,intervened=intervened,tiers=list(TIERS),tolerances=dict(probability=PROBABILITY_EPSILON,wait_seconds=WAIT_EPSILON),objective=OBJECTIVE,scope=SCOPE)
    return dict(decisions=decisions,evidence=evidence)
