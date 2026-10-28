import copy
import hashlib
import json
import unittest

from simulation.allocation_benchmark_audit import audit_report, AllocationAuditError


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def report_fixture(empty=False):
    snapshot = dict(id='audit-tiny', family='no_offer_control' if empty else 'abundant', seed=8101,
        synthetic=True, at=10,
        hospitals=[dict(id='H', capacity={'ed_bed': 4, 'special_team': 0},
            occupied={'ed_bed': 1}, committed={'ed_bed': 1}, queued={'ed_bed': 1}, capabilities=['general'])],
        patients=[dict(id='P', priority='high', t_req=0, accepted_hospitals=[] if empty else ['H'],
            accepted_at={} if empty else {'H': 2}, required_capabilities=['general'],
            resource_demands={'ed_bed': 1, 'special_team': 0}, travel_minutes={'H': 5})])
    plan = {'P': None if empty else 'H'}
    metrics = dict(patient_count=1, assigned_count=0 if empty else 1,
        unassigned_count=1 if empty else 0, unassigned_priority_weight=4 if empty else 0,
        priority_weighted_travel_minutes=0 if empty else 20,
        unassigned_by_priority={'4': int(empty), '2': 0, '1': 0},
        travel_denominator=0 if empty else 1, travel_mean_minutes=None if empty else 5,
        travel_max_minutes=None if empty else 5, travel_p95_minutes=None if empty else 5, constraint_violations=0)
    stages = [] if empty else [dict(status='OPTIMAL', objective=4, best_bound=4, objective_name='assigned_priority_weight'),
                              dict(status='OPTIMAL', objective=20, best_bound=20, objective_name='priority_weighted_travel_minutes')]
    record = dict(edge_count=0 if empty else 1, batch_size=1, assignments=plan,
        objective=[metrics['unassigned_priority_weight'], metrics['priority_weighted_travel_minutes']],
        assigned_count=metrics['assigned_count'], fallback=False, stages=stages,
        status='OPTIMAL_EMPTY' if empty else 'OPTIMAL', optimal_proven=True,
        primary_optimal_proven=True, secondary_optimal_proven=True)
    branches = [dict(policy=code, assignments=copy.deepcopy(plan), metrics=copy.deepcopy(metrics),
        input_sha256=digest(snapshot), fallbacks=0, solver_records=[copy.deepcopy(record)] if code == 'C1' else [])
        for code in ('R0', 'R1', 'R2', 'R3', 'C1')]
    for branch in branches:
        branch['wall_seconds'] = .01
    common = dict(denominator=0 if empty else 1, mean_difference_minutes=None if empty else 0,
                  total_difference_minutes=0, comparison_only=0, baseline_only=0)
    return dict(kind='frozen_allocation_benchmark', synthetic=True, phase='development',
        settings={'r3_enumeration_limit': 20000}, source_sha256={'source.py': 'abc'}, seeds=[8101],
        families=[snapshot['family']], runtime_versions={'python': '3.12.14', 'ortools': '9.14.6206'},
        results=[dict(snapshot=snapshot, combinations=1 if empty else 2, branches=branches,
                      common_travel_vs_r1={code: copy.deepcopy(common) for code in ('R0', 'R1', 'R2', 'R3', 'C1')})])


class AllocationAuditTests(unittest.TestCase):
    def test_saved_raw_ledger_and_two_stage_proof(self):
        result = audit_report(report_fixture(), expected_source_sha256={'source.py': 'abc'})
        self.assertEqual(result['cp_sat_two_stage_proofs'], 1)
        self.assertEqual(result['logical_empty_proofs'], 0)
        self.assertEqual(result['small_exact_cases'], 1)
        self.assertTrue(result['provenance_checked'])

    def test_logical_empty_is_distinguished_from_solver_proof(self):
        result = audit_report(report_fixture(empty=True))
        self.assertEqual(result['logical_empty_proofs'], 1)
        self.assertEqual(result['cp_sat_two_stage_proofs'], 0)

    def test_reject_changed_source_and_input_digest(self):
        with self.assertRaisesRegex(AllocationAuditError, 'source hashes'):
            audit_report(report_fixture(), expected_source_sha256={'source.py': 'other'})
        report = report_fixture()
        report['results'][0]['branches'][0]['input_sha256'] = 'fake'
        with self.assertRaisesRegex(AllocationAuditError, 'input digest'):
            audit_report(report)

    def test_reject_metrics_mismatched_to_raw_assignments(self):
        report = report_fixture()
        report['results'][0]['branches'][0]['metrics']['priority_weighted_travel_minutes'] = 19
        with self.assertRaisesRegex(AllocationAuditError, 'raw metric mismatch'):
            audit_report(report)

    def test_reject_ignoring_committed_or_queued_demand(self):
        report = report_fixture()
        snapshot = report['results'][0]['snapshot']
        snapshot['hospitals'][0]['queued']['ed_bed'] = 2
        for branch in report['results'][0]['branches']:
            branch['input_sha256'] = digest(snapshot)
        report['results'][0]['combinations'] = 1
        with self.assertRaisesRegex(AllocationAuditError, 'outside feasible'):
            audit_report(report)

    def test_reject_false_solver_proof(self):
        report = report_fixture()
        record = report['results'][0]['branches'][-1]['solver_records'][0]
        record['stages'][-1]['status'] = 'FEASIBLE'
        with self.assertRaisesRegex(AllocationAuditError, 'proof flag'):
            audit_report(report)

    def test_reject_false_empty_proof(self):
        report = report_fixture()
        report['results'][0]['branches'][-1]['solver_records'][0]['status'] = 'OPTIMAL_EMPTY'
        with self.assertRaisesRegex(AllocationAuditError, 'false empty'):
            audit_report(report)

    def test_reject_common_assigned_denominator_change(self):
        report = report_fixture()
        report['results'][0]['common_travel_vs_r1']['R0']['denominator'] = 0
        with self.assertRaisesRegex(AllocationAuditError, 'denominator'):
            audit_report(report)

    def test_zero_demand_does_not_consume_negative_other_resource(self):
        report = report_fixture()
        snapshot = report['results'][0]['snapshot']
        snapshot['hospitals'][0]['occupied']['special_team'] = 1
        for branch in report['results'][0]['branches']:
            branch['input_sha256'] = digest(snapshot)
        self.assertEqual(audit_report(report)['cases'], 1)

    def test_reject_missing_family_or_seed_even_without_summary(self):
        report = report_fixture()
        report['seeds'].append(8102)
        with self.assertRaisesRegex(AllocationAuditError, 'family/seed'):
            audit_report(report)
        report = report_fixture()
        report['families'].append('multi_resource')
        with self.assertRaisesRegex(AllocationAuditError, 'family/seed'):
            audit_report(report)

    def test_lock_checks_phase_seeds_families_and_runtime(self):
        report = report_fixture()
        lock = dict(settings=copy.deepcopy(report['settings']), source_sha256=report['source_sha256'],
                    families=report['families'], development_seeds=[8101], validation_seeds=[9101],
                    runtime_versions=copy.deepcopy(report['runtime_versions']))
        self.assertTrue(audit_report(report, lock=lock)['provenance_checked'])
        for field, value, pattern in [('families', ['other'], 'families'),
                                     ('development_seeds', [8102], 'seed list'),
                                     ('runtime_versions', {'python': 'different'}, 'runtime')]:
            bad_lock = copy.deepcopy(lock)
            bad_lock[field] = value
            with self.assertRaisesRegex(AllocationAuditError, pattern):
                audit_report(report, lock=bad_lock)

    def test_validation_requires_matching_lock_digest(self):
        report = report_fixture()
        report['phase'] = 'validation'
        lock = dict(settings=copy.deepcopy(report['settings']), source_sha256=report['source_sha256'],
                    families=report['families'], development_seeds=[8100], validation_seeds=[8101],
                    runtime_versions=copy.deepcopy(report['runtime_versions']))
        report['lock_sha256'] = digest(lock)
        audit_report(report, lock=lock)
        report['lock_sha256'] = 'different'
        with self.assertRaisesRegex(AllocationAuditError, 'lock digest'):
            audit_report(report, lock=lock)

    def test_recompute_saved_summary_averages_and_cp_comparison(self):
        report = report_fixture()
        summary = dict(family=report['families'][0], policies={}, cp_vs_r3=dict(better=0, equal=1, worse=0))
        for code in ('R0', 'R1', 'R2', 'R3', 'C1'):
            summary['policies'][code] = dict(cases=1, mean_unassigned_count=0,
                mean_unassigned_priority_weight=0, mean_priority_weighted_travel_minutes=20,
                fallbacks=0, wall_median_seconds=.01, wall_p95_seconds=.01, wall_max_seconds=.01)
        report['summary'] = [summary]
        audit_report(report)
        report['summary'][0]['policies']['C1']['mean_priority_weighted_travel_minutes'] = 19
        with self.assertRaisesRegex(AllocationAuditError, 'raw aggregate'):
            audit_report(report)
        report['summary'][0]['policies']['C1']['mean_priority_weighted_travel_minutes'] = 20
        report['summary'][0]['cp_vs_r3'] = dict(better=1, equal=0, worse=0)
        with self.assertRaisesRegex(AllocationAuditError, 'comparison differs'):
            audit_report(report)


if __name__ == '__main__':
    unittest.main()
