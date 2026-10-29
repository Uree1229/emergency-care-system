"""Conservative first-wave membership support at the public H boundary.

Only membership substitutions involving a queued descriptor censored to exact
zero by its deterministic mean wait at or beyond H are rejected.  This does
not assert physical impossibility, change a CDF, or compare actual outcomes.
"""
from copy import deepcopy
import hashlib
import math
from pathlib import Path
from . import cohort_waitpareto_model as selector

NAME='eat_cohort_pairshield_l15_f'
RULE='retain_removed_baseline_and_reject_added_candidate_queued_H_boundary_zero_memberships'
SCOPE='current first pair in newly activated Pairscope groups only; exact stored queued wait>=own H and every stored initial mass==0; retain baseline boundary hospital contacts and reject newly added candidate boundary contacts; shared membership classification may change freely; no new numeric threshold, CDF law or physical-zero guarantee'
SELECT_RULE='unchanged_wait_selector_on_explicit_probability_and_support_intersection_subdomain'
SELECT_SCOPE='validate complete original probability-admitted source first, then explicitly project the intersection with support-admitted rows; unchanged wait-first selector on that labeled subdomain; original JOINT feasibility and score values are never reclassified'
SOURCE_SHA256={
    'cohort_pairscope_model':'497d54ab3774b7f01e53e2f7aeff0bf852414ae26cd6a556f95140eb181ffdbb',
    'cohort_pairscope_policy':'193127b8019a34c4dc7736d9dce3c11e260d09ef0214660cda00a52709ddf1da',
    'cohort_waitpareto_model':'730e39ee0333fec00d7d6f4421dfc4c05cb9e387137625bf593958066e2110e9',
    'cohort_pareto_model':'3292bae7a0c2d0017e7fa39a80adc60819e4f23879b48e2d871975aeb343be91',
}
for name,digest in SOURCE_SHA256.items():
    if hashlib.sha256(Path(__file__).with_name(name+'.py').read_bytes()).hexdigest()!=digest:
        raise ValueError('frozen pair-shield source differs: '+name)


def _key(value):return selector.parent_guard._key(value)


def _indices(values):
    if type(values) is not list or any(type(i) is not int or i<0 for i in values) or len(set(values))!=len(values):
        raise ValueError('unique typed nonnegative row indices required')


def _owner_support(person):
    pid=person['patient_id'];_key(pid);edges=person['cell_edges_seconds']
    if type(edges) is not list or not edges or any(type(x) not in (int,float) or not math.isfinite(x) or x<=0 for x in edges) or any(b<=a for a,b in zip(edges,edges[1:])):
        raise ValueError('complete increasing positive owner H grid required')
    horizon=edges[-1];items=person['fixed_initial_items']
    if type(items) is not list:raise ValueError('complete first-wave initial items required')
    seen=set();records=[]
    for item in items:
        hospital=item['hospital'];key=_key(hospital);wait=item['wait_seconds'];mode=item['mode'];masses=item['masses']
        if key in seen:raise ValueError('duplicate typed initial hospital')
        seen.add(key)
        if mode not in ('queued','residual') or type(wait) not in (int,float) or not math.isfinite(wait) or wait<0:
            raise ValueError('valid stored initial descriptor required')
        if type(masses) is not list or len(masses)!=len(edges) or any(type(x) not in (int,float) or not math.isfinite(x) or x<0 for x in masses):
            raise ValueError('complete nonnegative stored initial masses required')
        zero=all(x==0 for x in masses);boundary=mode=='queued' and wait>=horizon and zero
        records.append(dict(hospital=hospital,mode=mode,wait_seconds=wait,horizon_seconds=horizon,
            stored_mass_count=len(masses),all_stored_masses_zero=zero,queued_H_boundary_zero=boundary))
    return dict(patient_id=pid,cell_edges_seconds=deepcopy(edges),horizon_seconds=horizon,initial_hospital_support=records)


def guard_rows(row_scores,*,baseline_index,current_pair_patient_ids,menu_indices,feasible_row_indices):
    if type(row_scores) is not list or not 1<=len(row_scores)<=400:raise ValueError('complete bounded source score rows required')
    _indices(menu_indices);_indices(feasible_row_indices)
    if len(menu_indices)!=len(row_scores) or set(menu_indices)!=set(feasible_row_indices):raise ValueError('complete original feasible coverage required')
    if type(baseline_index) is not int or baseline_index not in menu_indices:raise ValueError('original Stoploss anchor required')
    if type(current_pair_patient_ids) is not list or len(current_pair_patient_ids)!=2:raise ValueError('exact current first pair required')
    owner_keys=[_key(pid) for pid in current_pair_patient_ids]
    if len(set(owner_keys))!=2:raise ValueError('distinct typed current owners required')
    by_row={}
    for row in row_scores:
        index=row['row_index']
        if type(index) is not int or index<0 or index in by_row:raise ValueError('unique typed source score row required')
        people=row['first_cohort_envelope']['patient_scores']
        if type(people) is not list or len(people)!=2:raise ValueError('complete current first-wave owner partition required')
        people_by={}
        for person in people:
            key=_key(person['patient_id'])
            if key in people_by:raise ValueError('duplicate typed first-wave owner')
            people_by[key]=_owner_support(person)
        if set(people_by)!=set(owner_keys):raise ValueError('current first-wave owner identities differ')
        by_row[index]=people_by
    if list(by_row)!=menu_indices:raise ValueError('complete source menu order differs')
    baseline=by_row[baseline_index];checks=[];admitted=[];rejected=[]
    for index,owners in by_row.items():
        owner_checks=[]
        for key,pid in zip(owner_keys,current_pair_patient_ids):
            before=baseline[key];after=owners[key]
            if after['cell_edges_seconds']!=before['cell_edges_seconds']:raise ValueError('owner H grid differs across original rows')
            previous=before['initial_hospital_support'];current=after['initial_hospital_support']
            if len(previous)!=len(current):raise ValueError('original per-owner H inquiry cardinality differs')
            previous_by={_key(x['hospital']):x for x in previous};current_by={_key(x['hospital']):x for x in current}
            removed=[x['hospital'] for x in previous if _key(x['hospital']) not in current_by and x['queued_H_boundary_zero']]
            added=[x['hospital'] for x in current if _key(x['hospital']) not in previous_by and x['queued_H_boundary_zero']]
            shared=[dict(hospital=x['hospital'],baseline_boundary_zero=x['queued_H_boundary_zero'],candidate_boundary_zero=current_by[_key(x['hospital'])]['queued_H_boundary_zero'])
                for x in previous if _key(x['hospital']) in current_by and x['queued_H_boundary_zero']!=current_by[_key(x['hospital'])]['queued_H_boundary_zero']]
            owner_checks.append(dict(patient_id=pid,horizon_seconds=before['horizon_seconds'],
                baseline_initial_hospitals=[x['hospital'] for x in previous],candidate_initial_hospitals=[x['hospital'] for x in current],
                baseline_boundary_hospitals=[x['hospital'] for x in previous if x['queued_H_boundary_zero']],
                candidate_boundary_hospitals=[x['hospital'] for x in current if x['queued_H_boundary_zero']],
                removed_baseline_boundary_hospitals=removed,added_candidate_boundary_hospitals=added,
                shared_boundary_status_changes=shared,admissible=not removed and not added))
        allowed=all(x['admissible'] for x in owner_checks)
        if index==baseline_index and not allowed:raise ArithmeticError('original baseline failed its own support guard')
        (admitted if allowed else rejected).append(index)
        checks.append(dict(row_index=index,is_baseline=index==baseline_index,owner_checks=owner_checks,admissible=allowed))
    return dict(schema_version=1,rule=RULE,source_sha256=deepcopy(SOURCE_SHA256),baseline_row_index=baseline_index,
        current_pair_patient_ids=list(current_pair_patient_ids),baseline_owner_support=[baseline[key] for key in owner_keys],
        row_checks=checks,admissible_row_indices=admitted,rejected_row_indices=rejected,checked_row_count=len(row_scores),
        current_owner_count=2,baseline_always_admissible=baseline_index in admitted,scope=SCOPE)


def select_supported_wait(row_scores,*,baseline_index,owner_inventory,menu_indices,feasible_row_indices,
                          probability_admissible_row_indices,support_admissible_row_indices):
    _indices(support_admissible_row_indices)
    # Validates full source partition and the exact unchanged all-owner P set.
    original=selector.select_protected_wait(row_scores,admissible_row_indices=probability_admissible_row_indices,
        baseline_index=baseline_index,owner_inventory=owner_inventory,menu_indices=menu_indices,feasible_row_indices=feasible_row_indices)
    base=next(row for row in row_scores if row['row_index']==baseline_index)
    ids=[p['patient_id'] for p in base['first_cohort_envelope']['patient_scores']]
    support=guard_rows(row_scores,baseline_index=baseline_index,current_pair_patient_ids=ids,
        menu_indices=menu_indices,feasible_row_indices=feasible_row_indices)
    if support_admissible_row_indices!=support['admissible_row_indices']:
        raise ValueError('support-admitted set differs from complete membership law')
    domain=[index for index in menu_indices if index in set(probability_admissible_row_indices) and index in set(support_admissible_row_indices)]
    if baseline_index not in domain:raise ValueError('original baseline must remain in explicit intersection domain')
    rows=[row for row in row_scores if row['row_index'] in set(domain)]
    selected=selector.select_protected_wait(rows,admissible_row_indices=domain,baseline_index=baseline_index,
        owner_inventory=owner_inventory,menu_indices=domain,feasible_row_indices=domain)
    return dict(schema_version=1,rule=SELECT_RULE,source_sha256=deepcopy(SOURCE_SHA256),
        original_menu_indices=list(menu_indices),original_feasible_row_indices=list(feasible_row_indices),
        probability_admissible_row_indices=list(probability_admissible_row_indices),support_admissible_row_indices=list(support_admissible_row_indices),
        projected_domain_row_indices=domain,original_probability_domain_selection=original,unchanged_selector=selected,
        baseline_in_domain=True,original_JOINT_rows_reclassified=False,selected_index=selected['selected_index'],scope=SELECT_SCOPE)
