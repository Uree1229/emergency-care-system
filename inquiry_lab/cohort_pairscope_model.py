"""Frozen complete-owner scoring for a newly evaluated no-tail first pair.

The parent activation gate supplies no score rows or residual-owner origins in
this case.  This module materializes them from the same current public prefix,
using the unchanged Dynamic, Recourse, Followplan and Envelope laws.  It does
not remove original JOINT feasibility or invent later actions/observations.
"""
from copy import deepcopy
import hashlib
from pathlib import Path
from . import cohort_fullmenu_model as fullmenu,cohort_hedge_model as hedge
from . import cohort_recourse_model as recourse,cohort_followplan_model as followplan
from . import cohort_envelope_model as envelope

NAME='eat_cohort_pairscope_l15_f'
TIERS=(4,2,1)
RULE='same_prefix_no_tail_pair_with_complete_fresh_residual_origins_and_frozen_Envelope_scores'
SCOPE='newly evaluated exact-two eligible first-contact scope, not only two waiting owners; unchanged original JOINT feasibility/cardinalities and Dynamic/Recourse/Followplan/Envelope laws; freeze every residual owner from current public inputs; current pair only, no future input or realized success guarantee'
SOURCE_SHA256={
    'cohort_fullmenu_model':'8185c654bb7402d67708b14bd151572b50950cc36d639c0b5153fa37eececaf1',
    'cohort_dynamic_policy':'62ef7fbba3ee90015f800aae7fb38f820955c7b92cd46d35df216c9a54afccfb',
    'cohort_hedge_model':'348cc0d2207ce2a27ba8f98f12b62d69c592e66d50f10fa66f42b39f2f40d79e',
    'cohort_recourse_model':'e776bd643611a9ce33cbfa3fdbd8f05042fae755c20be5e0a1f4bddfdcebc2de',
    'cohort_followplan_model':'8f8dfc82451b05a804d33253823bde3a6cbb68ff67728d1458dbb17aca0d6268',
    'cohort_envelope_model':'c7a07257df98bc4435eee6c7253509d9ab9241ee2eab1062ad3563fc7cd57866',
    'cohort_stoploss_context':'a53774b1855e07fd15fb7436b0207db488ba84446f491f51692be01e3e0ba911',
}
for name,digest in SOURCE_SHA256.items():
    if hashlib.sha256(Path(__file__).with_name(name+'.py').read_bytes()).hexdigest()!=digest:
        raise ValueError('frozen pair-scope scoring source differs: '+name)
D=fullmenu.dynamic
JOINT=fullmenu.JOINT


def build_menu(table,anchor_index):
    menu,added,feasible=fullmenu.full_feasible_menu([anchor_index],table)
    return dict(menu_indices=menu,added_row_indices=added,feasible_row_indices=feasible,
        anchor_row_index=anchor_index,anchor_originally_feasible=True)


def prepare(proposal,*,members,tail_members,joint_context,priority_context):
    """Do not call parent prepare: its inactive residual origins are empty."""
    if type(tail_members) is not list or tail_members:
        raise ValueError('new pair scope requires exactly two eligible first contacts and no tail')
    if type(members) is not list or len(members)!=2 or len(joint_context['eligible'])!=2:
        raise ValueError('exactly two eligible current members required')
    views=fullmenu.parent_views(proposal)
    if views['hedge']['evidence']['evaluated'] is not False or views['hedge']['evidence']['owner_origins']!=[]:
        raise ValueError('fresh origin path applies only to the unevaluated parent')
    fp_context=fullmenu.stoploss_context.original;rc_context=fp_context.original
    hedge_context=rc_context.original;dc=hedge_context.original
    projected_members=members;projected_context=joint_context;hedge_members=recourse_members=None
    for projection in (fullmenu.stoploss_context,fp_context,rc_context,hedge_context,dc):
        projected_members=projection.base_members_projection(projected_members)
        projected_context=projection.base_context_projection(projected_context)
        if projection is fp_context:recourse_members=projected_members
        if projection is rc_context:hedge_members=projected_members
    models=[JOINT._model(m['inputs'],p) for m,p in zip(projected_members,views['joint']['baseline_proposals'])]
    owners=views['hedge']['evidence']['owner_context']
    first_ids=[fullmenu._key(pid) for pid in owners['first_cohort_patient_ids']]
    if first_ids!=[fullmenu._key(m['patient_id']) for m in members]:
        raise ValueError('captured first-owner partition differs from the current pair')
    residual=[o for o in owners['inventory'] if fullmenu._key(o['patient']['id']) not in set(first_ids)]
    origins=[hedge.freeze_owner(o) for o in residual]
    return dict(views=views,cs_members=projected_members,cs_tails=[],cs_context=projected_context,
        current_models=models,priority_context=priority_context,hedge_members=hedge_members,
        recourse_members=recourse_members,owners=owners,
        owner_by={fullmenu._key(o['patient']['id']):o for o in owners['inventory']},residual=residual,origins=origins)


def score_row(row_index,menu_position,prepared):
    p=prepared;joint=p['views']['joint'];table=joint['combinations']
    if type(row_index) is not int or not 0<=row_index<len(table) or not table[row_index]['feasible']:
        raise ValueError('pair-scope row must retain original JOINT feasibility')
    if type(menu_position) is not int or menu_position<0:raise ValueError('nonnegative full-menu position required')
    if p['cs_tails']:raise ValueError('no-tail scope cannot project later first pairs')
    if len(p['residual'])!=len(p['origins']):raise ValueError('complete residual-owner origin partition required')
    row=D._score(row_index,menu_position,table[row_index]['hospitals'],members=p['cs_members'],
        tail_members=[],joint_context=p['cs_context'],priority_context=p['priority_context'],current_models=p['current_models'])
    if row['projection']['steps']!=[] or row['projection']['singleton'] is not None:
        raise ValueError('no-tail projection unexpectedly contains later first contacts')
    initial=recourse.score_first_cohort(row,members=p['hedge_members'],
        current_baseline_proposals=joint['baseline_proposals'],priorities=p['priority_context'])
    residual=[recourse.score_owner(owner,origin,row['projection']['final_pending'],initial['jobs'])
        for owner,origin in zip(p['residual'],p['origins'])]
    if len(residual)!=len(p['residual']) or len(p['residual'])!=len(p['origins']):
        raise ValueError('complete residual-owner origin partition required')
    reviews=followplan.first_review_sources(row,members=p['recourse_members'],current_baseline_proposals=joint['baseline_proposals']);plans=[]
    for review in reviews:
        pid=review['patient_id'];jobs=[j for j in initial['jobs'] if fullmenu._key(j['patient_id'])==fullmenu._key(pid)]
        plans.append(followplan.score_first_patient(p['owner_by'][fullmenu._key(pid)],initial_jobs=jobs,
            row_pending=row['projection']['final_pending'],all_jobs=initial['jobs'],next_review_at=review['next_review_at']))
    first=envelope.score_first_cohort(plans)
    residual_metrics=[dict(patient_id=s['patient_id'],effective_priority=s['effective_priority'],metrics=deepcopy(s['metrics'])) for s in residual]
    metrics=[dict(patient_id=s['patient_id'],effective_priority=s['effective_priority'],metrics=deepcopy(s['metrics'])) for s in first['patient_scores']]+residual_metrics
    if sorted(fullmenu._key(m['patient_id']) for m in metrics)!=sorted(fullmenu._key(o['patient']['id']) for o in p['owners']['inventory']):
        raise ValueError('complete first-plus-residual score partition differs')
    return dict(row_index=row_index,row_kind='new_no_tail_first_pair',dynamic_row_projection=row,
        first_plan_source_sha256=envelope._sha(row),first_cohort_envelope=first,
        unchanged_law_residual_member_metrics=residual_metrics,member_metrics=metrics,
        tier_probabilities={str(t):sum((m['metrics']['selection_probability'] for m in metrics if m['effective_priority']==t),0.) for t in TIERS},
        sum_capped_wait_seconds=sum((m['metrics']['capped_wait_seconds'] for m in metrics),0.))
