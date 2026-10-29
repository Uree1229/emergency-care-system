"""Per-owner final predicted-probability nonregression on frozen Fullmenu rows.

Only existing public score records are compared.  This guard is a constraint
on the inherited surrogate, not a guarantee about realized success identities
or a separate counterfactual Stoploss trajectory.  No score is recomputed.
"""
from copy import deepcopy
import hashlib
import math
from pathlib import Path

NAME='eat_cohort_pareto_l15_f'
TIERS=(4,2,1)
PROBABILITY_EPSILON=1e-12
RULE='every_owner_final_predicted_probability_nonregression_from_Stoploss_actual_row'
SCOPE='all current scored owners and tiers, including first cohort and previously queried residual owners; compare complete existing Fullmenu rows against embedded Stoploss actual-row probabilities at this committed prefix with existing1e-12 tolerance; no new scoring, outcome identities or future inputs; predicted nonregression is not realized-success preservation'
SOURCE_SHA256={
    'cohort_fullmenu_model':'8185c654bb7402d67708b14bd151572b50950cc36d639c0b5153fa37eececaf1',
    'cohort_fullmenu_policy':'86835a44678ad372c4b9853623cc50ab78a61200a30b3ab6226af5f1d77215fe',
    'cohort_dynamic_policy':'62ef7fbba3ee90015f800aae7fb38f820955c7b92cd46d35df216c9a54afccfb',
}
for name,digest in SOURCE_SHA256.items():
    if hashlib.sha256(Path(__file__).with_name(name+'.py').read_bytes()).hexdigest()!=digest:
        raise ValueError('frozen Pareto score/selector source differs: '+name)


def _key(pid):
    if type(pid) not in (str,int) or pid=='':raise ValueError('typed public owner identity required')
    return type(pid).__name__,str(pid)


def _probability(value):
    if type(value) not in (int,float) or not math.isfinite(value) or not 0<=value<=1:
        raise ValueError('finite predicted selection probability in [0,1] required')
    return value


def guard_rows(row_scores,*,baseline_index,owner_inventory,menu_indices,feasible_row_indices):
    """Filter every existing row; retain the Stoploss row as fallback/anchor."""
    if type(row_scores) is not list or not 1<=len(row_scores)<=400:
        raise ValueError('complete bounded Fullmenu score rows required')
    if type(baseline_index) is not int or baseline_index<0:raise ValueError('Stoploss actual row index required')
    for indices in (menu_indices,feasible_row_indices):
        if type(indices) is not list or any(type(i) is not int or i<0 for i in indices) or len(set(indices))!=len(indices):
            raise ValueError('complete unique original feasible/menu indices required')
    if set(menu_indices)!=set(feasible_row_indices) or len(menu_indices)!=len(row_scores):
        raise ValueError('complete exhaustive Fullmenu row coverage required')
    if type(owner_inventory) is not list or not owner_inventory:raise ValueError('complete scored-owner inventory required')
    owners=[];owner_keys=set()
    for owner in owner_inventory:
        patient=owner['patient'];pid=patient['id'];key=_key(pid);tier=patient['effective_priority']
        if key in owner_keys:raise ValueError('duplicate scored owner inventory identity')
        if type(tier) is not int or tier not in TIERS:raise ValueError('valid scored owner priority required')
        owners.append((key,pid,tier));owner_keys.add(key)
    rows=[];indices=[]
    for row in row_scores:
        index=row['row_index']
        if type(index) is not int or index<0 or index in indices:raise ValueError('duplicate or invalid Fullmenu score row')
        indices.append(index);members=row['member_metrics']
        if type(members) is not list or len(members)!=len(owners):raise ValueError('complete per-row scored-owner partition required')
        by={}
        for member in members:
            key=_key(member['patient_id'])
            if key in by:raise ValueError('duplicate per-row scored owner')
            by[key]=member
        if set(by)!=owner_keys:raise ValueError('per-row scored-owner identity partition differs')
        for key,pid,tier in owners:
            member=by[key]
            if type(member['effective_priority']) is not int or member['effective_priority']!=tier:
                raise ValueError('per-row scored-owner priority differs')
            _probability(member['metrics']['selection_probability'])
        rows.append((index,by))
    if indices!=menu_indices or baseline_index not in indices:
        raise ValueError('Fullmenu row order or embedded Stoploss anchor differs')
    baseline=next(by for index,by in rows if index==baseline_index)
    baseline_probabilities=[dict(patient_id=pid,effective_priority=tier,
        selection_probability=baseline[key]['metrics']['selection_probability']) for key,pid,tier in owners]
    checks=[];admissible=[];rejected=[]
    for index,by in rows:
        regressions=[]
        for key,pid,tier in owners:
            before=baseline[key]['metrics']['selection_probability'];after=by[key]['metrics']['selection_probability']
            threshold=before-PROBABILITY_EPSILON
            if after<threshold:
                regressions.append(dict(patient_id=pid,effective_priority=tier,
                    baseline_selection_probability=before,candidate_selection_probability=after,
                    minimum_allowed_probability=threshold,delta_selection_probability=after-before))
        allowed=not regressions
        if index==baseline_index and not allowed:raise ArithmeticError('Stoploss baseline failed its own probability guard')
        (admissible if allowed else rejected).append(index)
        checks.append(dict(row_index=index,is_baseline=index==baseline_index,verified_owner_count=len(owners),
            admissible=allowed,owner_regressions=regressions))
    return dict(schema_version=1,rule=RULE,source_sha256=deepcopy(SOURCE_SHA256),probability_epsilon=PROBABILITY_EPSILON,
        baseline_row_index=baseline_index,baseline_owner_probabilities=baseline_probabilities,row_checks=checks,
        admissible_row_indices=admissible,rejected_row_indices=rejected,compared_owner_count=len(owners),
        checked_row_count=len(rows),baseline_always_admissible=baseline_index in admissible,scope=SCOPE)
