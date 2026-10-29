import copy
import hashlib
import json
import unittest

from simulation.allocation_stress_audit import (
    AllocationStressAuditError, audit_report, _raw_problem, _raw_metrics, _exact_objective)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def fixture(empty=False):
    snapshot = dict(id='audit-1', family='audit_fixture', seed=12101, size=1, synthetic=True, at=10,
        hospitals=[dict(id='H', capabilities=['general'], capacity={'bed': 4, 'team': 0},
            occupied={'bed': 1}, committed={'bed': 1}, queued={'bed': 1})],
        patients=[dict(id='P', priority='high', t_req=0,
            accepted_hospitals=[] if empty else ['H'], accepted_at={} if empty else {'H': 2},
            required_capabilities=['general'], resource_demands={'bed': 1, 'team': 0},
            travel_minutes={'H': 5})])
    plan = {'P': None if empty else 'H'}
    metrics = dict(patient_count=1, assigned_count=0 if empty else 1, unassigned_count=int(empty),
        unassigned_priority_weight=4 if empty else 0, priority_weighted_travel_minutes=0 if empty else 20,
        unassigned_by_priority={'4': int(empty), '2': 0, '1': 0}, travel_denominator=0 if empty else 1,
        travel_mean_minutes=None if empty else 5, travel_max_minutes=None if empty else 5,
        travel_p95_minutes=None if empty else 5, constraint_violations=0)
    objective = [metrics['unassigned_priority_weight'], metrics['priority_weighted_travel_minutes']]
    h1 = dict(status='HEURISTIC', optimal_proven=False, assignments=plan, objective=objective,
        assigned_count=metrics['assigned_count'], time_limit_seconds=.01,
        baseline_assignments=plan, baseline_objective=objective,
        baseline_assigned_count=metrics['assigned_count'], max_moves=10, evaluated_moves=2,
        accepted_moves=0, wall_seconds=.009, preprocessing_seconds=.001, search_seconds=.007,
        batch_size=1, edge_count=0 if empty else 1, strategies=['priority_nearest'],
        strategy_results=[dict(strategy='priority_nearest', objective=objective, accepted=True)],
        improvements=[], accepted_move_types=[], search_stop_reason='search_complete')
    stages = [] if empty else [
        dict(status='OPTIMAL', objective_name='assigned_priority_weight', objective=4, best_bound=4, wall_seconds=.003),
        dict(status='OPTIMAL', objective_name='priority_weighted_travel_minutes', objective=20, best_bound=20, wall_seconds=.003)]
    cp = dict(status='OPTIMAL_EMPTY' if empty else 'OPTIMAL', optimal_proven=True,
        primary_optimal_proven=True, secondary_optimal_proven=True,
        assignments=plan, objective=objective, assigned_count=metrics['assigned_count'],
        edge_count=0 if empty else 1, batch_size=1, stages=stages,
        fallback=False, fallback_reason=None, seed=0, num_search_workers=1,
        time_limit_seconds=.01, wall_seconds=.009)
    policies = ['R1', 'R2', 'H1@0.01', 'C1@0.01']
    branches = [dict(policy=code, algorithm=code.split('@')[0],
        budget_seconds=.01 if '@' in code else None, assignments=copy.deepcopy(plan),
        metrics=copy.deepcopy(metrics), wall_seconds=.01, fallbacks=0,
        solver_records=[copy.deepcopy(h1 if code.startswith('H1') else cp)] if '@' in code else [],
        input_sha256=digest(snapshot)) for code in policies]
    common = dict(denominator=0 if empty else 1, mean_difference_minutes=None if empty else 0,
        total_difference_minutes=0, comparison_only=0, baseline_only=0)
    result = dict(snapshot=snapshot, case_index=0, execution_order=policies, branches=branches,
        common_travel_vs_r1={code: copy.deepcopy(common) for code in policies},
        common_travel_vs_c1_same_budget={'H1@0.01': copy.deepcopy(common)},
        combinations=1 if empty else 2,
        reference=dict(kind='proven_optimum', policy='C1@0.01', objective=objective, optimal_proven=True),
        objective_gaps={code: dict(primary_difference=0, secondary_difference_if_primary_equal=0,
            relative_secondary_gap_if_primary_equal=None if empty else 0) for code in policies})
    summary = []
    for family in ('audit_fixture', 'ALL'):
        summary_policies = {}
        for code in policies:
            proof = ('OPTIMAL_EMPTY' if empty else 'OPTIMAL') if code.startswith('C1') \
                else 'HEURISTIC' if code.startswith('H1') else 'NO_PROOF'
            summary_policies[code] = dict(cases=1, mean_unassigned_count=int(empty),
                mean_unassigned_priority_weight=4 if empty else 0,
                mean_priority_weighted_travel_minutes=0 if empty else 20,
                unassigned_by_priority_totals={'4': int(empty), '2': 0, '1': 0},
                wall_median_seconds=.01, wall_p95_seconds=.01, wall_max_seconds=.01,
                fallbacks=0, optimal_proven_count=int(code.startswith('C1')),
                proof_status_counts={proof: 1}, constraint_violations=0)
        summary.append(dict(family=family, cases=1, policies=summary_policies,
            h1_vs_c1={'0.01': dict(better=0, equal=1, worse=0, common_travel=copy.deepcopy(common))},
            proven_reference_count=1))
    return dict(schema_version=1, kind='allocation_stress_comparison', synthetic=True, phase='development',
        settings=dict(objective=['unassigned_priority_weight', 'priority_weighted_travel_minutes'],
            priority_weights={'high': 4, 'medium': 2, 'low': 1}, budgets_seconds=[.01]),
        families=['audit_fixture'], sizes=[1], seeds=[12101],
        runtime_versions={'python': '3.12.14', 'ortools': '9.15.6755'}, source_sha256={'source.py': 'abc'},
        lock_sha256=None, results=[result], summary=summary)


def refresh_input(report):
    for branch in report['results'][0]['branches']:
        branch['input_sha256'] = digest(report['results'][0]['snapshot'])


class AllocationStressAuditTests(unittest.TestCase):
    def test_valid_artifact_has_independent_exact_and_solver_evidence(self):
        result = audit_report(fixture(), expected_source_sha256={'source.py': 'abc'})
        self.assertEqual(result['cases'], 1)
        self.assertEqual(result['branches'], 4)
        self.assertEqual(result['cp_sat_two_stage_proofs'], 1)
        self.assertEqual(result['small_exact_cases'], 1)
        self.assertTrue(result['provenance_checked'])

    def test_empty_proof_is_not_solver_proof(self):
        result = audit_report(fixture(empty=True))
        self.assertEqual(result['logical_empty_proofs'], 1)
        self.assertEqual(result['cp_sat_two_stage_proofs'], 0)

    def test_reject_source_or_input_hash_mismatch(self):
        with self.assertRaisesRegex(AllocationStressAuditError, 'source hashes'):
            audit_report(fixture(), expected_source_sha256={'source.py': 'other'})
        report = fixture()
        report['results'][0]['branches'][0]['input_sha256'] = 'other'
        with self.assertRaisesRegex(AllocationStressAuditError, 'input digest'):
            audit_report(report)

    def test_reject_ignored_existing_committed_and_queue(self):
        report = fixture()
        report['results'][0]['snapshot']['hospitals'][0]['queued']['bed'] = 2
        refresh_input(report)
        report['results'][0]['combinations'] = 1
        with self.assertRaisesRegex(AllocationStressAuditError, 'outside feasible'):
            audit_report(report)

    def test_reject_unaccepted_hospital_even_with_capacity(self):
        report = fixture()
        p = report['results'][0]['snapshot']['patients'][0]
        p['accepted_hospitals'], p['accepted_at'] = [], {}
        refresh_input(report)
        report['results'][0]['combinations'] = 1
        with self.assertRaisesRegex(AllocationStressAuditError, 'outside feasible'):
            audit_report(report)

    def test_reject_future_acceptance(self):
        report = fixture()
        report['results'][0]['snapshot']['patients'][0]['accepted_at']['H'] = 11
        refresh_input(report)
        with self.assertRaisesRegex(AllocationStressAuditError, 'future or pre-request'):
            audit_report(report)

    def test_reject_future_patient(self):
        report = fixture()
        report['results'][0]['snapshot']['patients'][0]['t_req'] = 11
        with self.assertRaisesRegex(AllocationStressAuditError, 'future patient'):
            audit_report(report)

    def test_reject_false_metric(self):
        report = fixture()
        report['results'][0]['branches'][0]['metrics']['priority_weighted_travel_minutes'] = 19
        with self.assertRaisesRegex(AllocationStressAuditError, 'raw metric mismatch'):
            audit_report(report)

    def test_reject_patient_id_missing(self):
        report = fixture()
        report['results'][0]['branches'][0]['assignments'] = {}
        with self.assertRaisesRegex(AllocationStressAuditError, 'patient IDs'):
            audit_report(report)

    def test_zero_demand_does_not_consume_negative_other_resource(self):
        report = fixture()
        report['results'][0]['snapshot']['hospitals'][0]['occupied']['team'] = 1
        refresh_input(report)
        audit_report(report)

    def test_reject_heuristic_claiming_optimality(self):
        report = fixture()
        report['results'][0]['branches'][2]['solver_records'][0]['optimal_proven'] = True
        with self.assertRaisesRegex(AllocationStressAuditError, 'must not claim optimality'):
            audit_report(report)

    def test_reject_h1_rewritten_baseline(self):
        report = fixture()
        report['results'][0]['branches'][2]['solver_records'][0]['baseline_assignments'] = {'P': None}
        with self.assertRaisesRegex(AllocationStressAuditError, 'documented nearest greedy'):
            audit_report(report)

    def test_reject_h1_counter_beyond_operation_limit(self):
        report = fixture()
        report['results'][0]['branches'][2]['solver_records'][0]['evaluated_moves'] = 11
        with self.assertRaisesRegex(AllocationStressAuditError, 'move counter'):
            audit_report(report)

    def test_reject_h1_component_times_exceed_total(self):
        report = fixture()
        report['results'][0]['branches'][2]['solver_records'][0]['search_seconds'] = .02
        with self.assertRaisesRegex(AllocationStressAuditError, 'component time'):
            audit_report(report)

    def test_reject_false_cp_proof_flag_or_bound(self):
        report = fixture()
        record = report['results'][0]['branches'][3]['solver_records'][0]
        record['stages'][1]['status'] = 'FEASIBLE'
        with self.assertRaisesRegex(AllocationStressAuditError, 'proof flag'):
            audit_report(report)
        report = fixture()
        report['results'][0]['branches'][3]['solver_records'][0]['stages'][1]['best_bound'] = 19
        with self.assertRaisesRegex(AllocationStressAuditError, 'objective/bound'):
            audit_report(report)

    def test_reject_cp_fallback_counter_mismatch(self):
        report = fixture()
        report['results'][0]['branches'][3]['fallbacks'] = 1
        with self.assertRaisesRegex(AllocationStressAuditError, 'fallback counter'):
            audit_report(report)

    def test_reject_false_empty_proof(self):
        report = fixture()
        report['results'][0]['branches'][3]['solver_records'][0]['status'] = 'OPTIMAL_EMPTY'
        with self.assertRaisesRegex(AllocationStressAuditError, 'false empty optimum'):
            audit_report(report)

    def test_reject_common_patient_denominator_or_difference(self):
        report = fixture()
        report['results'][0]['common_travel_vs_r1']['H1@0.01']['denominator'] = 0
        with self.assertRaisesRegex(AllocationStressAuditError, 'R1 denominator'):
            audit_report(report)
        report = fixture()
        report['results'][0]['common_travel_vs_c1_same_budget']['H1@0.01']['total_difference_minutes'] = 1
        with self.assertRaisesRegex(AllocationStressAuditError, 'C1 denominator'):
            audit_report(report)

    def test_reject_reference_and_gap_changes(self):
        report = fixture()
        report['results'][0]['reference']['objective'][1] = 19
        with self.assertRaisesRegex(AllocationStressAuditError, 'reference objective'):
            audit_report(report)
        report = fixture()
        report['results'][0]['objective_gaps']['H1@0.01']['primary_difference'] = 1
        with self.assertRaisesRegex(AllocationStressAuditError, 'objective gap'):
            audit_report(report)

    def test_reject_incomplete_case_policy_and_execution_coverage(self):
        report = fixture()
        report['sizes'].append(2)
        with self.assertRaisesRegex(AllocationStressAuditError, 'case coverage'):
            audit_report(report)
        report = fixture()
        report['results'][0]['branches'].pop()
        with self.assertRaisesRegex(AllocationStressAuditError, 'branch policy'):
            audit_report(report)
        report = fixture()
        report['results'][0]['execution_order'][0] = 'R2'
        with self.assertRaisesRegex(AllocationStressAuditError, 'execution order'):
            audit_report(report)

    def test_reject_summary_aggregate_change(self):
        report = fixture()
        report['summary'][0]['policies']['H1@0.01']['mean_unassigned_count'] = 1
        with self.assertRaisesRegex(AllocationStressAuditError, 'summary raw aggregate'):
            audit_report(report)

    def test_reject_summary_h1_c1_comparison_change(self):
        report = fixture()
        report['summary'][0]['h1_vs_c1']['0.01']['better'] = 1
        with self.assertRaisesRegex(AllocationStressAuditError, 'summary H1/C1 comparison'):
            audit_report(report)

    def test_reject_hidden_future_information(self):
        report = fixture()
        report['results'][0]['snapshot']['future_release_times'] = [20]
        with self.assertRaisesRegex(AllocationStressAuditError, 'unobserved/unknown snapshot'):
            audit_report(report)
        report = fixture()
        report['results'][0]['snapshot']['patients'][0]['pending_reply_schedule'] = {'H': 11}
        with self.assertRaisesRegex(AllocationStressAuditError, 'unobserved/unknown patient'):
            audit_report(report)

    def test_reject_h1_objective_worse_than_real_initial_plan(self):
        report = fixture()
        branch = report['results'][0]['branches'][2]
        branch['assignments'] = {'P': None}
        branch['metrics'] = dict(patient_count=1, assigned_count=0, unassigned_count=1,
            unassigned_priority_weight=4, priority_weighted_travel_minutes=0,
            unassigned_by_priority={'4': 1, '2': 0, '1': 0}, travel_denominator=0,
            travel_mean_minutes=None, travel_max_minutes=None, travel_p95_minutes=None, constraint_violations=0)
        record = branch['solver_records'][0]
        record['assignments'], record['objective'], record['assigned_count'] = {'P': None}, [4, 0], 0
        with self.assertRaisesRegex(AllocationStressAuditError, 'worsened the baseline'):
            audit_report(report)

    def test_reject_h1_non_improving_history(self):
        report = fixture()
        record = report['results'][0]['branches'][2]['solver_records'][0]
        record.update(accepted_moves=1, improvements=[dict(kind='swap', evaluation=2, objective=[0, 21])],
                      accepted_move_types=['swap'])
        with self.assertRaisesRegex(AllocationStressAuditError, 'not strictly lexicographic'):
            audit_report(report)

    def test_cp_unproven_loose_upper_bound_is_allowed(self):
        report = fixture()
        result = report['results'][0]
        record = result['branches'][3]['solver_records'][0]
        record.update(status='FEASIBLE', optimal_proven=False, primary_optimal_proven=False,
            secondary_optimal_proven=False,
            stages=[dict(status='FEASIBLE', objective_name='assigned_priority_weight',
                         objective=4, best_bound=400, wall_seconds=.003)])
        result['reference'].update(kind='observed_best', optimal_proven=False)
        for summary in report['summary']:
            summary['proven_reference_count'] = 0
            summary['policies']['C1@0.01'].update(optimal_proven_count=0, proof_status_counts={'FEASIBLE': 1})
        output = audit_report(report)
        self.assertEqual(output['unproven_cp_cases'], 1)

    def test_unknown_solver_zero_placeholder_bound_is_not_treated_as_valid_bound(self):
        report = fixture()
        result = report['results'][0]
        branch = result['branches'][3]
        record = branch['solver_records'][0]
        branch['fallbacks'] = 1
        record.update(status='UNKNOWN', optimal_proven=False, primary_optimal_proven=False,
            secondary_optimal_proven=False, fallback=True, fallback_reason='no_feasible_solver_incumbent',
            stages=[dict(status='UNKNOWN', objective_name='assigned_priority_weight',
                         objective=None, best_bound=0, wall_seconds=.003)])
        result['reference'].update(kind='observed_best', optimal_proven=False)
        for summary in report['summary']:
            summary['proven_reference_count'] = 0
            summary['policies']['C1@0.01'].update(optimal_proven_count=0, fallbacks=1,
                                               proof_status_counts={'UNKNOWN': 1})
        self.assertEqual(audit_report(report)['unproven_cp_cases'], 1)

    def test_reject_any_branch_better_than_claimed_proven_cp_optimum(self):
        report = fixture()
        result = report['results'][0]
        snapshot = result['snapshot']
        snapshot['hospitals'].append(dict(id='J', capabilities=['general'], capacity={'bed': 1}))
        p = snapshot['patients'][0]
        p['accepted_hospitals'].append('J')
        p['accepted_at']['J'], p['travel_minutes']['J'] = 2, 4
        refresh_input(report)
        result['combinations'] = 3
        for branch in result['branches'][:3]:
            branch['assignments'] = {'P': 'J'}
            branch['metrics'].update(priority_weighted_travel_minutes=16,
                travel_mean_minutes=4, travel_max_minutes=4, travel_p95_minutes=4)
        record = result['branches'][2]['solver_records'][0]
        record.update(assignments={'P': 'J'}, objective=[0, 16], baseline_assignments={'P': 'J'},
            baseline_objective=[0, 16], edge_count=2,
            strategy_results=[dict(strategy='priority_nearest', objective=[0, 16], accepted=True)])
        result['branches'][3]['solver_records'][0]['edge_count'] = 2
        result['common_travel_vs_r1']['C1@0.01'].update(mean_difference_minutes=1, total_difference_minutes=1)
        result['common_travel_vs_c1_same_budget']['H1@0.01'].update(
            mean_difference_minutes=-1, total_difference_minutes=-1)
        with self.assertRaisesRegex(AllocationStressAuditError, 'better than a proven optimum'):
            audit_report(report)

    def test_development_validation_lock_separation(self):
        report = fixture()
        lock = dict(kind='allocation_stress_lock', settings=copy.deepcopy(report['settings']),
            source_sha256=report['source_sha256'], families=report['families'], sizes=report['sizes'],
            runtime_versions=report['runtime_versions'], development_seeds=[12101], validation_seeds=[13101])
        audit_report(report, lock=lock)
        lock['validation_seeds'] = [12101]
        with self.assertRaisesRegex(AllocationStressAuditError, 'seed overlap'):
            audit_report(report, lock=lock)

    def test_validation_requires_exact_lock_digest_and_runtime(self):
        report = fixture()
        report['phase'] = 'validation'
        lock = dict(kind='allocation_stress_lock', settings=copy.deepcopy(report['settings']),
            source_sha256=report['source_sha256'], families=report['families'], sizes=report['sizes'],
            runtime_versions=report['runtime_versions'], development_seeds=[12100], validation_seeds=[12101])
        report['lock_sha256'] = digest(lock)
        audit_report(report, lock=lock)
        report['lock_sha256'] = 'bad'
        with self.assertRaisesRegex(AllocationStressAuditError, 'lock digest'):
            audit_report(report, lock=lock)
        report['lock_sha256'] = digest(lock)
        lock['runtime_versions'] = {'python': 'other', 'ortools': '9.15.6755'}
        with self.assertRaisesRegex(AllocationStressAuditError, 'runtime_versions'):
            audit_report(report, lock=lock)

    def test_independent_enumeration_requires_joint_multi_resource_feasibility(self):
        snapshot = fixture()['results'][0]['snapshot']
        snapshot['hospitals'] = [
            dict(id='H', capabilities=['general'], capacity={'bed': 1, 'team': 1}),
            dict(id='J', capabilities=['general'], capacity={'bed': 1, 'team': 0})]
        snapshot['patients'] = [
            dict(id='A', priority='low', t_req=0, accepted_hospitals=['H', 'J'],
                 accepted_at={'H': 1, 'J': 1}, resource_demands={'bed': 1}, travel_minutes={'H': 1, 'J': 5}),
            dict(id='B', priority='high', t_req=0, accepted_hospitals=['H'],
                 accepted_at={'H': 1}, resource_demands={'bed': 1, 'team': 1}, travel_minutes={'H': 2, 'J': 2})]
        patients, free, options = _raw_problem(snapshot)
        self.assertEqual(_exact_objective(patients, free, options), (0, 13))
        with self.assertRaisesRegex(AllocationStressAuditError, 'resource ledger exceeded'):
            _raw_metrics(snapshot, {'A': 'H', 'B': 'H'}, free, options)


if __name__ == '__main__':
    unittest.main()
