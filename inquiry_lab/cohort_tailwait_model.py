"""All-owner predicted wait protection for inherited projected-tail cohorts.

Only existing score records are compared.  The protection is local to the
current disclosed surrogate and does not bound actual individual waiting.
"""
from copy import deepcopy
import hashlib
import math
from pathlib import Path
from . import cohort_waitpareto_model as selector

NAME='eat_cohort_tailwait_l15_f'
TIERS=(4,2,1)
WAIT_EPSILON=selector.WAIT_EPSILON
RULE='every_captured_owner_predicted_wait_nonregression_from_original_Stoploss_row'
SCOPE='inherited evaluated cohorts with a projected unqueried tail; protect every captured scored owner, including current pair, tail and queried residual owners, against the same Stoploss actual row within existing1e-9 wait tolerance; existing scores only, not an actual individual-wait or success guarantee'
SELECT_RULE='unchanged_wait_selector_on_explicit_probability_and_owner_wait_intersection'
SELECT_SCOPE='first validate the entire original all-owner probability-admitted source; explicitly project its intersection with owner-wait admissibility; unchanged wait-first/priority/Stoploss-anchor selector on that labeled subdomain, no original score or JOINT feasibility changes'
SOURCE_SHA256={
    'cohort_pairshield_model':'10feb1b090e2ae1027363081104ba1e57e5923224df5abc8ba0e36601c64fe7c',
    'cohort_pairshield_policy':'5c3a54803b6502e36f4eb9d957ceb5c91e71bd21933512a13debe05538027438',
    'cohort_waitpareto_model':'730e39ee0333fec00d7d6f4421dfc4c05cb9e387137625bf593958066e2110e9',
    'cohort_pareto_model':'3292bae7a0c2d0017e7fa39a80adc60819e4f23879b48e2d871975aeb343be91',
}
for name,digest in SOURCE_SHA256.items():
    if hashlib.sha256(Path(__file__).with_name(name+'.py').read_bytes()).hexdigest()!=digest:
        raise ValueError('frozen tail-wait source differs: '+name)


def _key(value):return selector.parent_guard._key(value)


def _indices(values):
    if type(values) is not list or any(type(i) is not int or i<0 for i in values) or len(set(values))!=len(values):
        raise ValueError('complete unique typed nonnegative row indices required')


def _wait(value):
    if type(value) not in (int,float) or not math.isfinite(value) or value<0:
        raise ValueError('finite nonnegative existing capped wait required')
    return value


def guard_rows(row_scores,*,baseline_index,owner_inventory,menu_indices,feasible_row_indices):
    if type(row_scores) is not list or not 1<=len(row_scores)<=400:raise ValueError('complete bounded original score rows required')
    _indices(menu_indices);_indices(feasible_row_indices)
    if len(menu_indices)!=len(row_scores) or set(menu_indices)!=set(feasible_row_indices):raise ValueError('complete original feasible row coverage required')
    if type(baseline_index) is not int or baseline_index not in menu_indices:raise ValueError('original Stoploss actual anchor required')
    if type(owner_inventory) is not list or not owner_inventory:raise ValueError('complete captured scored-owner inventory required')
    owners=[];keys=set()
    for owner in owner_inventory:
        person=owner['patient'];pid=person['id'];key=_key(pid);tier=person['effective_priority']
        if key in keys or type(tier) is not int or tier not in TIERS:raise ValueError('duplicate owner or invalid captured priority')
        owners.append((key,pid,tier));keys.add(key)
    rows=[];indices=[]
    for row in row_scores:
        index=row['row_index'];members=row['member_metrics']
        if type(index) is not int or index<0 or index in indices:raise ValueError('unique typed score row required')
        indices.append(index)
        if type(members) is not list or len(members)!=len(owners):raise ValueError('complete row owner partition required')
        by={}
        for member in members:
            key=_key(member['patient_id'])
            if key in by:raise ValueError('duplicate typed scored owner')
            by[key]=member
        if set(by)!=keys:raise ValueError('row captured-owner partition differs')
        for key,pid,tier in owners:
            member=by[key]
            if type(member['effective_priority']) is not int or member['effective_priority']!=tier:raise ValueError('row owner priority differs')
            _wait(member['metrics']['capped_wait_seconds'])
        rows.append((index,by))
    if indices!=menu_indices:raise ValueError('complete original score row order differs')
    baseline=next(by for index,by in rows if index==baseline_index)
    baseline_waits=[dict(patient_id=pid,effective_priority=tier,capped_wait_seconds=baseline[key]['metrics']['capped_wait_seconds']) for key,pid,tier in owners]
    checks=[];admitted=[];rejected=[]
    for index,by in rows:
        regressions=[]
        for key,pid,tier in owners:
            before=baseline[key]['metrics']['capped_wait_seconds'];after=by[key]['metrics']['capped_wait_seconds'];maximum=before+WAIT_EPSILON
            if after>maximum:
                regressions.append(dict(patient_id=pid,effective_priority=tier,baseline_capped_wait_seconds=before,
                    candidate_capped_wait_seconds=after,maximum_allowed_wait_seconds=maximum,delta_capped_wait_seconds=after-before))
        allowed=not regressions
        if index==baseline_index and not allowed:raise ArithmeticError('baseline failed its own wait guard')
        (admitted if allowed else rejected).append(index)
        checks.append(dict(row_index=index,is_baseline=index==baseline_index,verified_owner_count=len(owners),admissible=allowed,owner_regressions=regressions))
    return dict(schema_version=1,rule=RULE,source_sha256=deepcopy(SOURCE_SHA256),wait_epsilon=WAIT_EPSILON,
        baseline_row_index=baseline_index,baseline_owner_waits=baseline_waits,row_checks=checks,
        admissible_row_indices=admitted,rejected_row_indices=rejected,compared_owner_count=len(owners),
        checked_row_count=len(row_scores),baseline_always_admissible=baseline_index in admitted,scope=SCOPE)


def select_wait_guarded(row_scores,*,baseline_index,owner_inventory,menu_indices,feasible_row_indices,
                        probability_admissible_row_indices,wait_admissible_row_indices):
    _indices(wait_admissible_row_indices)
    original=selector.select_protected_wait(row_scores,baseline_index=baseline_index,owner_inventory=owner_inventory,
        menu_indices=menu_indices,feasible_row_indices=feasible_row_indices,admissible_row_indices=probability_admissible_row_indices)
    guarded=guard_rows(row_scores,baseline_index=baseline_index,owner_inventory=owner_inventory,menu_indices=menu_indices,feasible_row_indices=feasible_row_indices)
    if wait_admissible_row_indices!=guarded['admissible_row_indices']:raise ValueError('owner-wait admitted set differs from complete wait law')
    domain=[i for i in menu_indices if i in set(probability_admissible_row_indices) and i in set(wait_admissible_row_indices)]
    if baseline_index not in domain:raise ValueError('original baseline must remain in the labeled intersection domain')
    rows=[r for r in row_scores if r['row_index'] in set(domain)]
    selected=selector.select_protected_wait(rows,baseline_index=baseline_index,owner_inventory=owner_inventory,
        menu_indices=domain,feasible_row_indices=domain,admissible_row_indices=domain)
    return dict(schema_version=1,rule=SELECT_RULE,source_sha256=deepcopy(SOURCE_SHA256),original_menu_indices=list(menu_indices),
        original_feasible_row_indices=list(feasible_row_indices),probability_admissible_row_indices=list(probability_admissible_row_indices),
        wait_admissible_row_indices=list(wait_admissible_row_indices),projected_domain_row_indices=domain,
        original_probability_domain_selection=original,unchanged_selector=selected,baseline_in_domain=True,
        original_JOINT_rows_reclassified=False,selected_index=selected['selected_index'],scope=SELECT_SCOPE)
