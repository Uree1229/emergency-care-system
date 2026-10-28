"""Protect owner waits only when the inherited choice projects a first tail."""
from copy import deepcopy
import hashlib
from pathlib import Path
from . import cohort_pairshield_policy as original,cohort_tailwait_context as context,cohort_tailwait_model as model

NAME='eat_cohort_tailwait_l15_f'
BASE_POLICY='eat_cohort_pairshield_l15_f'
BASE_SOURCE_SHA256='5c3a54803b6502e36f4eb9d957ceb5c91e71bd21933512a13debe05538027438'
if hashlib.sha256(Path(original.__file__).read_bytes()).hexdigest()!=BASE_SOURCE_SHA256:
    raise ValueError('frozen tail-wait Pairshield foundation differs')
HORIZON=original.HORIZON
JOINT=original.JOINT
TIERS=(4,2,1)
PROBABILITY_EPSILON=1e-12
WAIT_EPSILON=1e-9
RULE='inherited_projected_tail_cohorts_with_all_captured_owner_wait_nonregression'
OBJECTIVE='unchanged inherited Envelope score rows and all-owner P floor; intersect with every captured owner wait<=same Stoploss anchor wait+existing1e-9; unchanged wait-first, priority and anchor selector on an explicit subdomain'
SCOPE='only original Pairscope inherited-evaluated groups with nonempty public tail; current pair, tail and queried residual waits all protected in the existing surrogate; all no-tail/new-pair/inactive/singleton paths copied; no score, CDF, queue, timing or actual-outcome inputs changed and no actual individual-wait guarantee'


def spec(name=NAME):
    if name!=NAME:raise ValueError('the fixed tail-wait candidate is '+NAME)
    value=original.spec(BASE_POLICY);value.update(name=NAME,label='EAT l15 f, owner-wait protection for inherited tail cohorts')
    return value


def decide(name,*,now,candidates,observations=(),pending=(),patient_pending=(),max_new,last_contact_at=None,time_context):
    spec(name)
    value=original.decide(BASE_POLICY,now=now,candidates=candidates,observations=observations,pending=pending,
        patient_pending=patient_pending,max_new=max_new,last_contact_at=last_contact_at,time_context=time_context)
    value['evidence']['policy']=NAME
    value['evidence']['cohort_tailwait']=dict(schema_version=1,mode='horizon_fallback',evaluated=False,intervened=False)
    return value


def _decision(proposal,hospitals,metrics):
    value=deepcopy(proposal);value['send']=list(hospitals);value['reason']='cohort_tailwait_current_only'
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
    old=proposal['evidence'];pairscope=old['original_pairscope_proposal']['evidence'];env=pairscope['original_envelope_proposal']['evidence']
    evaluated=old['evaluated'];inherited=pairscope['inherited_evaluated']
    if type(evaluated) is not bool or type(inherited) is not bool:raise ValueError('typed inherited activation required')
    guarded=evaluated and inherited and tail_members!=[]
    base_index=old['selected_index'];selected=base_index;anchor=old['stoploss_anchor_index'];guard=None;selection=None
    if guarded:
        if pairscope['newly_activated'] or old['shield_evaluated'] or not env['evaluated']:
            raise ValueError('owner-wait scope must be an inherited projected-tail group')
        full=env['original_waitpareto_proposal']['evidence']['original_pareto_proposal']['evidence']['original_fullmenu_proposal']['evidence']
        if anchor!=pairscope['stoploss_anchor_index'] or anchor!=env['stoploss_anchor_index'] or anchor!=full['base_selected_index'] or anchor!=full['original_stoploss_proposal']['evidence']['selected_index']:
            raise ValueError('original Stoploss actual anchor differs')
        if base_index!=env['selected_index']:raise ValueError('inherited Pairshield selection differs from immutable Envelope source')
        rows=env['new_row_scores'];menu=full['menu_indices'];feasible=full['feasible_row_indices']
        guard=model.guard_rows(rows,baseline_index=anchor,owner_inventory=owners['inventory'],menu_indices=menu,feasible_row_indices=feasible)
        selection=model.select_wait_guarded(rows,baseline_index=anchor,owner_inventory=owners['inventory'],menu_indices=menu,feasible_row_indices=feasible,
            probability_admissible_row_indices=env['owner_probability_guard']['admissible_row_indices'],wait_admissible_row_indices=guard['admissible_row_indices'])
        if selection['original_probability_domain_selection']!=env['protected_wait_selection']:
            raise ValueError('unchanged original P-domain selection differs')
        selected=selection['selected_index']
    intervened=selected!=base_index
    if intervened:
        joint=original.original.model.fullmenu.parent_views(full['original_stoploss_proposal'])['joint']
        chosen=next(row for row in rows if row['row_index']==selected)
        decisions=[_decision(p,send,chosen['member_metrics'][i]['metrics']) for i,(p,send) in enumerate(zip(joint['baseline_proposals'],joint['combinations'][selected]['hospitals']))]
    else:decisions=deepcopy(proposal['decisions'])
    priorities={context._key(p['patient_id']):p['effective_priority'] for p in priority['patients']}
    for i,decision in enumerate(decisions):
        decision['evidence']['policy']=NAME
        decision['evidence']['cohort_tailwait']=dict(schema_version=1,mode='pair_cohort_tailwait',evaluated=evaluated,
            tailwait_evaluated=guarded,intervened=intervened,member_index=i,base_selected_index=base_index,
            stoploss_anchor_index=anchor,selected_index=selected,effective_priority=priorities[context._key(members[i]['patient_id'])])
    evidence=dict(schema_version=1,rule=RULE,base_policy=BASE_POLICY,base_source_sha256=BASE_SOURCE_SHA256,guard_source_sha256=deepcopy(model.SOURCE_SHA256),
        identity_projection=dict(source_policy=NAME,calculation_policy=BASE_POLICY,
            fields=['joint_context.eligible[].job.policy_name','members[].workflow_snapshot.job.policy_name','tail_members[].workflow_snapshot.job.policy_name','owner_context.inventory[].job.policy_name'],
            copied_only=True,public_identity_unchanged=True,foreign_base_owner_label='__foreign_cohort_pairshield__'),
        joint_context=native,priority_context=priority,owner_context=owners,original_pairshield_proposal=proposal,
        evaluated=evaluated,tailwait_evaluated=guarded,owner_wait_guard=guard,protected_tailwait_selection=selection,
        base_selected_index=base_index,stoploss_anchor_index=anchor,selected_index=selected,intervened=intervened,
        tiers=list(TIERS),tolerances=dict(probability=PROBABILITY_EPSILON,wait_seconds=WAIT_EPSILON),objective=OBJECTIVE,scope=SCOPE)
    return dict(decisions=decisions,evidence=evidence)
