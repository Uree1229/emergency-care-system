"""Four-arm G1 arithmetic; frozen bootstrap is used only with an explicit alias.

Canonical audits retain original H1 and actual G1.  No simulator, policy, or
future trajectory is called by this module.  Known patient cohorts are fixed
using old R1/H1 endpoints before inspecting G1 endpoints.
"""
from __future__ import annotations

from copy import deepcopy

from . import final_assignment_analysis_20261028 as reference

POLICIES = ('R0', 'R1', 'H1', 'G1')
ALIAS = {'R0': 'R0', 'R1': 'R1', 'H1': 'G1'}


def project_candidate(cases):
    result = deepcopy(cases)
    for case in result:
        audit = case['audit']
        reference.require(set(audit['patient_vectors']) == set(POLICIES), 'four actual vector labels required')
        reference.require(set(audit['branch_metrics']) == set(POLICIES), 'four actual metric labels required')
        for field in ('patient_vectors', 'branch_metrics'):
            audit[field] = {alias: deepcopy(audit[field][actual]) for alias, actual in ALIAS.items()}
    return result


def _checked(cases):
    candidate = reference._checked_cases(project_candidate(cases))
    original = deepcopy(cases)
    for case in original:
        for field in ('patient_vectors', 'branch_metrics'):
            case['audit'][field] = {code: case['audit'][field][code] for code in reference.POLICIES}
    old = {case['case_id']: case for case in reference._checked_cases(original)}
    result = []
    for row in candidate:
        prev = old[row['case_id']]
        reference.require(row['scenario_sha256'] == prev['scenario_sha256'], 'four-arm input mismatch')
        row['vectors']['G1'] = row['vectors'].pop('H1')
        row['metrics']['G1'] = row['metrics'].pop('H1')
        row['vectors']['H1'] = prev['vectors']['H1']
        row['metrics']['H1'] = prev['metrics']['H1']
        for left, right in zip(row['vectors']['H1'], row['vectors']['G1']):
            for field in ('id', 'priority', 'occur_t', 'search_start_t', 'observation_end_t'):
                reference.require(type(left[field]) is type(right[field]) and left[field] == right[field], 'original H1/G1 source mismatch')
        result.append(row)
    return result


def _identifier(case, row):
    return dict(case_id=case['case_id'], patient_id=row['id'], patient_id_type=type(row['id']).__name__, priority=row['priority'])


def _priority_summary(rows, code, priority=None):
    pairs = [(case, row) for case in rows for row in case['vectors'][code]
        if priority is None or row['priority'] == priority]
    lost = [_identifier(case, row) for case, row in pairs if not row['selected']]
    return dict(patients=len(pairs), unassigned=len(lost),
        unassigned_priority_weight=sum(row['priority'] for _, row in pairs if not row['selected']),
        not_arrived=sum(not row['arrived'] for _, row in pairs), unassigned_identifiers=lost)


def _tail_cohorts(rows):
    fixed = []; targets = []; old_targets = []; shared = []
    for case in rows:
        for base, old, new in zip(case['vectors']['R1'], case['vectors']['H1'], case['vectors']['G1']):
            ref = _identifier(case, base)
            if base['arrived']:
                delta = new['arrival_capped_minutes'] - base['arrival_elapsed_minutes']
                fixed.append(dict(ref, delta_minutes=delta, candidate_arrived=new['arrived'],
                    before_arrival_minutes=base['arrival_elapsed_minutes'], after_arrival_or_cap_minutes=new['arrival_capped_minutes']))
                if new['arrived']:
                    shared.append(delta)
                if old['arrived'] and old['arrival_elapsed_minutes'] > base['arrival_elapsed_minutes']:
                    old_targets.append(dict(ref, delta_minutes=old['arrival_elapsed_minutes']-base['arrival_elapsed_minutes']))
                    targets.append(dict(fixed[-1]))
    return dict(fixed_R1_arrived=dict(reference._delta_summary([r['delta_minutes'] for r in fixed]),
            candidate_missing_count=sum(not r['candidate_arrived'] for r in fixed), patient_differences=fixed),
        original_delayed_targets=dict(reference._delta_summary([r['delta_minutes'] for r in targets]),
            candidate_missing_count=sum(not r['candidate_arrived'] for r in targets), patient_differences=targets,
            original_patient_differences=old_targets),
        actual_shared_arrival=reference._delta_summary(shared),
        missing_candidate_endpoint='observation_end_t retained as censored cap; not an observed arrival')


def _priority_comparison(comparison, priority):
    result=dict(priority=priority,baseline=comparison['baseline'],candidate='G1')
    for endpoint in ('selected','arrived'):
        for direction in ('lost','gained'):
            name=endpoint+'_'+direction
            identifiers=[row for row in comparison[name+'_identifiers'] if row['priority']==priority]
            result[name+'_count']=len(identifiers);result[name+'_identifiers']=identifiers
    for output,source in (('common_arrival','common_arrival_patient_differences'),
        ('all_patient_arrival_capped','all_patient_arrival_capped_differences')):
        values=[r['delta_minutes'] for r in comparison[source] if r['priority']==priority]
        result[output]=reference._delta_summary(values)
    return result


def evaluate_goals(groups, phase, goals):
    checks = []
    def add(name, passed, **values):
        checks.append(dict(name=name, passed=bool(passed), **values))
    for group in groups:
        key = group['track'] + '/' + group['stratum']
        counts = group['coverage_preservation']
        for field in ('unassigned', 'unassigned_priority_weight'):
            add(key+'/'+field, counts['G1'][field] <= counts['H1'][field], baseline=counts['H1'][field], candidate=counts['G1'][field])
        pri = group['priority_groups']['4']
        add(key+'/priority4_unassigned', pri['G1']['unassigned'] <= pri['H1']['unassigned'], baseline=pri['H1']['unassigned'], candidate=pri['G1']['unassigned'])
    if phase == 'regression':
        lookup = {(g['track'], g['stratum']): g for g in groups}
        for key, limit in ((('main','surge'), goals['known_surge_unassigned_maximum']),
            (('stress','large_simultaneous'), goals['known_stress_large_simultaneous_unassigned_maximum']),
            (('stress','scarce_greedy_trap'), goals['known_stress_scarce_greedy_trap_unassigned_maximum']),
            (('stress','scarce_long_detour'), goals['known_stress_scarce_long_detour_unassigned_maximum'])):
            reference.require(key in lookup, 'known required stratum absent: '+str(key))
            value = lookup[key]['coverage_preservation']['G1']['unassigned']
            add('/'.join(key)+'/known_maximum', value <= limit, candidate=value, inclusive_limit=limit)
        tail = lookup['main','surge']['arrival_tail_cohorts']
        fixed, target, shared = tail['fixed_R1_arrived'], tail['original_delayed_targets'], tail['actual_shared_arrival']
        reference.require(fixed['denominator'] == goals['fixed_R1_arrived_known_surge_population'], 'fixed old R1 arrived cohort changed')
        reference.require(target['denominator'] == goals['original_delayed_known_surge_target_count'], 'original delayed target cohort changed')
        for name, value, limit in (
            ('known_fixed_R1_arrived_delayed_count', fixed['delayed_count'], goals['known_fixed_R1_arrived_delayed_count_exclusive_maximum']),
            ('known_fixed_R1_arrived_maximum_capped_delay_minutes', fixed['maximum_delay_minutes'], goals['known_fixed_R1_arrived_capped_delay_minutes_exclusive_maximum']),
            ('original_target_remaining_delayed_count', target['delayed_count'], goals['original_target_remaining_delayed_count_exclusive_maximum']),
            ('known_shared_arrival_maximum_delay_minutes', shared['maximum_delay_minutes'], goals['known_shared_arrival_delay_minutes_exclusive_maximum'])):
            add(name, value is not None and value < limit, candidate=value, exclusive_limit=limit)
    return dict(passed=all(row['passed'] for row in checks), checks=checks,
        phase=phase, exact_success_ID_preservation_is_unlock_condition=False,
        cross_stratum_compensation_allowed=False)


def analyze(cases, *, phase, goals, bootstrap_samples=5000):
    reference.require(phase in ('regression', 'validation'), 'unknown phase')
    checked = _checked(cases)
    aliased = reference.analyze(project_candidate(cases), bootstrap_samples=bootstrap_samples)
    groups = []
    for track, stratum in sorted({(r['track'], r['stratum']) for r in checked}):
        rows = [r for r in checked if (r['track'], r['stratum']) == (track, stratum)]
        comparison = {code: reference._descriptive_comparison(rows, code, 'G1') for code in ('H1','R1')}
        groups.append(dict(track=track, stratum=stratum, cases=len(rows),
            patients=sum(len(r['vectors']['R1']) for r in rows),
            policies={code: reference._policy_summary(rows, code) for code in POLICIES},
            coverage_preservation={code:_priority_summary(rows, code) for code in ('H1','G1')},
            priority_groups={str(p):{code:_priority_summary(rows, code, p) for code in POLICIES} for p in (4,2,1)},
            direct_comparisons=comparison,
            priority_group_comparisons={str(p):{code:_priority_comparison(value,p)
                for code,value in comparison.items()} for p in (4,2,1)},
            exact_old_H1_success_IDs_preserved=comparison['H1']['selected_lost_count']==comparison['H1']['arrived_lost_count']==0,
            arrival_tail_cohorts=_tail_cohorts(rows)))
    return dict(schema_version=1, analysis='arrival_guard_four_actual_arms_v1', candidate='G1',
        canonical_policy_labels=list(POLICIES), frozen_bootstrap_projection=ALIAS,
        frozen_bootstrap_projection_only=True, frozen_bootstrap=aliased, groups=groups,
        goals=evaluate_goals(groups, phase, goals), policy_or_model_executions=0,
        original_H1_metrics_overwritten=False, input_modified=False,
        clinical_priority_interpretation=False, experimental_priority_weights=[4,2,1],
        fixed_cohort_reference='old R1 arrived; old H1 versus R1 shared-arrival delayed targets',
        exact_success_ID_preservation_reported_separately=True, strata_pooled_for_efficacy=False)
