"""Exhaustive feasible-row coverage with the frozen Stoploss scoring pipeline.

One added row is materialized at a time.  Evidence retains its audited Dynamic
projection and final metrics, rather than every intermediate Recourse,
Followplan and Stoploss proof.  The original Stoploss rows remain exact copies.
This changes menu coverage only; the inherited surrogate is not a forecast
guarantee, observed future state, cancellation permission or capacity release.
"""
from copy import deepcopy
import hashlib
from pathlib import Path
from . import cohort_dynamic_policy as dynamic,cohort_recourse_model as recourse
from . import cohort_followplan_model as followplan,cohort_stoploss_model as stoploss
from . import cohort_stoploss_context as stoploss_context

NAME='eat_cohort_fullmenu_l15_f'
TIERS=(4,2,1)
RULE='all_original_feasible_joint_rows_with_frozen_Stoploss_scoring'
SCOPE='parent Stoploss menu rows copied exactly, then every other feasible original JOINT index in table order; same Dynamic reference projection, Recourse initial expected work and residual owners, Followplan frozen future plans and Stoploss scoring; compact added-row Dynamic projection plus final metrics; row-by-row intermediate lifetime; inherited stochastic, stage and reference-continuation approximations remain; no future state or actual capacity release'
SOURCE_SHA256={
    'cohort_dynamic_policy':'62ef7fbba3ee90015f800aae7fb38f820955c7b92cd46d35df216c9a54afccfb',
    'cohort_recourse_model':'e776bd643611a9ce33cbfa3fdbd8f05042fae755c20be5e0a1f4bddfdcebc2de',
    'cohort_followplan_model':'8f8dfc82451b05a804d33253823bde3a6cbb68ff67728d1458dbb17aca0d6268',
    'cohort_stoploss_model':'ae9e4f870c56551b922982b914a38418a681f81d210afb66254c4c740bd30c9e',
}
for module in (dynamic,recourse,followplan,stoploss):
    if hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()!=SOURCE_SHA256[module.__name__.rsplit('.',1)[-1]]:
        raise ValueError('frozen Fullmenu scoring source differs: '+module.__name__)
JOINT=dynamic.JOINT


def _key(value):return type(value).__name__,str(value)


def parent_views(proposal):
    """Borrow immutable native parent evidence; never copy its full graph."""
    follow=proposal['evidence']['original_followplan_proposal']
    rec=follow['evidence']['original_recourse_proposal']
    hedge=rec['evidence']['original_hedge_proposal']
    dyn=hedge['evidence']['original_dynamic_proposal']
    return dict(followplan=follow,recourse=rec,hedge=hedge,dynamic=dyn,
        joint=dynamic._joint(dyn['evidence']['original_cohort_success_proposal'])['evidence'])


def full_feasible_menu(parent_menu,table):
    """No beam: inherited rows first, every remaining feasible row in order."""
    if type(table) is not list or not 1<=len(table)<=400:
        raise ValueError('original bounded JOINT table required')
    if type(parent_menu) is not list or not 1<=len(parent_menu)<=3 or len(set(parent_menu))!=len(parent_menu):
        raise ValueError('complete unique parent Stoploss menu required')
    if any(type(row) is not dict or type(row.get('feasible')) is not bool for row in table):
        raise ValueError('original JOINT feasibility flags required')
    if any(type(i) is not int or not 0<=i<len(table) or not table[i]['feasible'] for i in parent_menu):
        raise ValueError('parent menu left original feasible rows')
    feasible=[i for i,row in enumerate(table) if row['feasible']]
    added=[i for i in feasible if i not in parent_menu]
    return list(parent_menu)+added,added,feasible


def prepare(proposal,*,members,tail_members,joint_context,priority_context):
    """Share row-independent public inputs; inputs have native Stoploss identity."""
    views=parent_views(proposal);fp_context=stoploss_context.original
    rc_context=fp_context.original;hedge_context=rc_context.original;dc=hedge_context.original
    # Stoploss -> Followplan -> Recourse -> Hedge -> Dynamic -> CS.  Each
    # frozen projection copies public identity fields and leaves clocks fixed.
    projected_members=members;projected_tails=tail_members;projected_context=joint_context
    hedge_members=None;recourse_members=None
    for projection in (stoploss_context,fp_context,rc_context,hedge_context,dc):
        projected_members=projection.base_members_projection(projected_members)
        projected_tails=projection.base_members_projection(projected_tails)
        projected_context=projection.base_context_projection(projected_context)
        if projection is fp_context:recourse_members=projected_members
        if projection is rc_context:hedge_members=projected_members
    models=[JOINT._model(m['inputs'],p) for m,p in zip(projected_members,views['joint']['baseline_proposals'])]
    owners=views['hedge']['evidence']['owner_context']
    first_ids={_key(pid) for pid in owners['first_cohort_patient_ids']}
    residual=[o for o in owners['inventory'] if _key(o['patient']['id']) not in first_ids]
    origins=views['hedge']['evidence']['owner_origins']
    if len(residual)!=len(origins):raise ValueError('parent residual-owner origin partition differs')
    return dict(views=views,cs_members=projected_members,cs_tails=projected_tails,
        cs_context=projected_context,current_models=models,priority_context=priority_context,
        hedge_members=hedge_members,recourse_members=recourse_members,owners=owners,
        owner_by={_key(o['patient']['id']):o for o in owners['inventory']},residual=residual,origins=origins)


def score_added_row(row_index,menu_position,prepared):
    """Reconstruct all frozen stages locally; return only compact proof."""
    p=prepared;joint=p['views']['joint'];table=joint['combinations']
    if type(row_index) is not int or not 0<=row_index<len(table) or not table[row_index]['feasible']:
        raise ValueError('added row is outside original feasible table')
    if type(menu_position) is not int or menu_position<0:raise ValueError('full menu position required')
    row=dynamic._score(row_index,menu_position,table[row_index]['hospitals'],members=p['cs_members'],
        tail_members=p['cs_tails'],joint_context=p['cs_context'],priority_context=p['priority_context'],
        current_models=p['current_models'])
    initial=recourse.score_first_cohort(row,members=p['hedge_members'],
        current_baseline_proposals=joint['baseline_proposals'],priorities=p['priority_context'])
    residual_scores=[recourse.score_owner(owner,origin,row['projection']['final_pending'],initial['jobs'])
        for owner,origin in zip(p['residual'],p['origins'])]
    reviews=followplan.first_review_sources(row,members=p['recourse_members'],
        current_baseline_proposals=joint['baseline_proposals'])
    plans=[]
    for review in reviews:
        pid=review['patient_id'];jobs=[j for j in initial['jobs'] if _key(j['patient_id'])==_key(pid)]
        plans.append(followplan.score_first_patient(p['owner_by'][_key(pid)],initial_jobs=jobs,
            row_pending=row['projection']['final_pending'],all_jobs=initial['jobs'],next_review_at=review['next_review_at']))
    final=stoploss.score_first_cohort(plans)
    metrics=[dict(patient_id=s['patient_id'],effective_priority=s['effective_priority'],metrics=deepcopy(s['metrics']))
        for s in final['patient_scores']+residual_scores]
    if sorted(_key(m['patient_id']) for m in metrics)!=sorted(_key(o['patient']['id']) for o in p['owners']['inventory']):
        raise ValueError('complete expanded-row owner score partition differs')
    return dict(row_index=row_index,row_kind='expanded',dynamic_row_projection=row,member_metrics=metrics,
        tier_probabilities={str(t):sum((m['metrics']['selection_probability'] for m in metrics if m['effective_priority']==t),0.0) for t in TIERS},
        sum_capped_wait_seconds=sum((m['metrics']['capped_wait_seconds'] for m in metrics),0.0))
