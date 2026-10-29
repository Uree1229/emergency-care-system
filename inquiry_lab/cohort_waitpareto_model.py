"""Wait-first selection within the unchanged Pareto-admitted Fullmenu rows.

The original Stoploss row remains admitted.  Therefore choosing the minimum
wait band guarantees selected predicted wait <= baseline wait +1e-9 without
an additional baseline-wait cap.  This is a surrogate property, not a promise
about realized waits or success identities.  No service score is recomputed.
"""
from copy import deepcopy
import hashlib
import math
from pathlib import Path
from . import cohort_pareto_model as parent_guard

NAME='eat_cohort_waitpareto_l15_f'
TIERS=(4,2,1)
PROBABILITY_EPSILON=1e-12
WAIT_EPSILON=1e-9
RULE='minimum_wait_band_then_priority_probability_bands_with_Stoploss_anchor'
SCOPE='unchanged complete Fullmenu scores and Pareto-admitted rows; minimize total capped wait within existing1e-9 band, then existing tier4,2,1 probability bands, original embedded Stoploss anchor and source order; baseline wait bound follows from baseline admissibility, no extra wait cap or new guard; no realized outcome guarantee'
SOURCE_SHA256={
    'cohort_pareto_model':'3292bae7a0c2d0017e7fa39a80adc60819e4f23879b48e2d871975aeb343be91',
    'cohort_pareto_policy':'f61ef3ddd7e795508082419e8494bff225f916cf29b8a56c8b3a24017dd68122',
    'cohort_dynamic_policy':'62ef7fbba3ee90015f800aae7fb38f820955c7b92cd46d35df216c9a54afccfb',
}
for name,digest in SOURCE_SHA256.items():
    if hashlib.sha256(Path(__file__).with_name(name+'.py').read_bytes()).hexdigest()!=digest:
        raise ValueError('frozen protected-wait selector foundation differs: '+name)


def _wait(value):
    if type(value) not in (int,float) or not math.isfinite(value) or value<0:
        raise ValueError('finite nonnegative capped wait required')
    return value


def select_protected_wait(row_scores,*,admissible_row_indices,baseline_index,owner_inventory,menu_indices,feasible_row_indices):
    """Select existing admitted rows; validate their immutable source partition."""
    if type(row_scores) is not list or not 1<=len(row_scores)<=400:raise ValueError('complete bounded score rows required')
    if type(baseline_index) is not int or baseline_index<0:raise ValueError('original Stoploss anchor required')
    for indices in (menu_indices,feasible_row_indices,admissible_row_indices):
        if type(indices) is not list or any(type(i) is not int or i<0 for i in indices) or len(set(indices))!=len(indices):
            raise ValueError('complete unique source row indices required')
    if set(menu_indices)!=set(feasible_row_indices) or len(row_scores)!=len(menu_indices):
        raise ValueError('complete Fullmenu feasible coverage required')
    if not admissible_row_indices or baseline_index not in admissible_row_indices or not set(admissible_row_indices)<=set(menu_indices):
        raise ValueError('parent-admitted set must retain original Stoploss anchor')
    if [i for i in menu_indices if i in set(admissible_row_indices)]!=admissible_row_indices:
        raise ValueError('parent-admitted source order differs')
    if type(owner_inventory) is not list or not owner_inventory:raise ValueError('complete scored-owner inventory required')
    owners={}
    for owner in owner_inventory:
        p=owner['patient'];key=parent_guard._key(p['id']);tier=p['effective_priority']
        if key in owners or type(tier) is not int or tier not in TIERS:raise ValueError('duplicate or invalid scored owner inventory')
        owners[key]=tier
    indices=[];partitions={}
    for row in row_scores:
        index=row['row_index']
        if type(index) is not int or index<0 or index in indices:raise ValueError('duplicate or invalid source score row')
        indices.append(index);members=row['member_metrics']
        if type(members) is not list or len(members)!=len(owners):raise ValueError('complete per-row owner partition required')
        by={}
        for member in members:
            key=parent_guard._key(member['patient_id'])
            if key in by or key not in owners or type(member['effective_priority']) is not int or member['effective_priority']!=owners[key]:
                raise ValueError('duplicate, foreign or wrong-priority row owner')
            parent_guard._probability(member['metrics']['selection_probability'])
            _wait(member['metrics']['capped_wait_seconds']);by[key]=member
        if set(by)!=set(owners):raise ValueError('complete per-row owner identity partition differs')
        if set(row['tier_probabilities'])!={'4','2','1'}:raise ValueError('existing priority score fields differ')
        for tier in TIERS:
            expected=sum((m['metrics']['selection_probability'] for m in members if m['effective_priority']==tier),0.0)
            value=row['tier_probabilities'][str(tier)]
            if type(value) not in (int,float) or not math.isfinite(value) or value<0 or value!=expected:
                raise ValueError('existing tier probability aggregate differs')
        if _wait(row['sum_capped_wait_seconds'])!=sum((m['metrics']['capped_wait_seconds'] for m in members),0.0):
            raise ValueError('existing total capped wait aggregate differs')
        partitions[index]=by
    if indices!=menu_indices:raise ValueError('complete source score order differs')
    baseline=partitions[baseline_index]
    # Check the parent's stated admissibility using its unchanged tolerance;
    # this fails malformed evidence rather than introducing another filter.
    expected_admitted=[index for index in indices if all(partitions[index][key]['metrics']['selection_probability'] >=
        baseline[key]['metrics']['selection_probability']-PROBABILITY_EPSILON for key in owners)]
    if admissible_row_indices!=expected_admitted:
        raise ValueError('parent-admitted rows differ from unchanged per-owner probability guard')
    admitted=set(admissible_row_indices);survivors=[row for row in row_scores if row['row_index'] in admitted]
    minimum=min(row['sum_capped_wait_seconds'] for row in survivors)
    survivors=[row for row in survivors if row['sum_capped_wait_seconds']<=minimum+WAIT_EPSILON]
    wait_indices=[row['row_index'] for row in survivors];filters=[]
    for tier in TIERS:
        maximum=max(row['tier_probabilities'][str(tier)] for row in survivors)
        survivors=[row for row in survivors if row['tier_probabilities'][str(tier)]>=maximum-PROBABILITY_EPSILON]
        filters.append(dict(tier=tier,maximum_probability=maximum,surviving_row_indices=[row['row_index'] for row in survivors]))
    final=[row['row_index'] for row in survivors];selected=baseline_index if baseline_index in final else final[0]
    baseline_wait=next(row['sum_capped_wait_seconds'] for row in row_scores if row['row_index']==baseline_index)
    selected_wait=next(row['sum_capped_wait_seconds'] for row in row_scores if row['row_index']==selected)
    bounded=selected_wait<=baseline_wait+WAIT_EPSILON
    if not bounded:raise ArithmeticError('minimum-wait selection exceeds admitted baseline tolerance')
    return dict(schema_version=1,rule=RULE,source_sha256=deepcopy(SOURCE_SHA256),probability_epsilon=PROBABILITY_EPSILON,
        wait_epsilon=WAIT_EPSILON,baseline_row_index=baseline_index,baseline_sum_capped_wait_seconds=baseline_wait,
        admissible_row_indices=list(admissible_row_indices),minimum_wait_seconds=minimum,wait_surviving_row_indices=wait_indices,
        priority_filters=filters,final_surviving_row_indices=final,base_selected_tied=baseline_index in final,
        selected_index=selected,selected_sum_capped_wait_seconds=selected_wait,selected_wait_within_baseline_tolerance=bounded,
        additional_baseline_wait_cap_applied=False,scope=SCOPE)
