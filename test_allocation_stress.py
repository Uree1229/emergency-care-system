"""Fresh-case generation, current information and experiment evidence checks."""
from copy import deepcopy
from contextlib import redirect_stderr
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from simulation.allocation_benchmark import audit_assignment
from simulation import allocation_stress_experiment as experiment
from simulation.allocation_stress_scenarios import (DEVELOPMENT_SEEDS, VALIDATION_SEEDS,
    FAMILIES, SIZES, build_stress_snapshots, validate_stress_snapshot)


class StressScenarios(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snapshots = build_stress_snapshots(DEVELOPMENT_SEEDS[0])

    def test_predeclared_development_cohort_is_42_distinct_cases(self):
        rows = [row for seed in DEVELOPMENT_SEEDS for row in build_stress_snapshots(seed)]
        self.assertEqual(len(rows), 42)
        self.assertEqual(len({r['id'] for r in rows}), 42)
        self.assertEqual({r['family'] for r in rows}, set(FAMILIES))
        self.assertEqual({r['size'] for r in rows}, set(SIZES))
        self.assertFalse(set(DEVELOPMENT_SEEDS) & set(VALIDATION_SEEDS))
        self.assertEqual(len(VALIDATION_SEEDS) * len(FAMILIES) * len(SIZES), 84)

    def test_independent_family_and_size_streams(self):
        for original in self.snapshots:
            isolated = build_stress_snapshots(original['seed'], [original['family']], [original['size']])
            self.assertEqual(isolated, [original])

    def test_all_cases_are_valid_and_time_fields_are_observed(self):
        for row in self.snapshots:
            validate_stress_snapshot(row)
            self.assertEqual(row['size'], len(row['patients']))
            self.assertTrue(row['synthetic'])
            for p in row['patients']:
                self.assertLessEqual(p['t_req'], row['at'])
                self.assertTrue(all(p['t_req'] <= reply <= row['at'] for reply in p['accepted_at'].values()))

    def test_generator_rejects_bad_selections(self):
        inputs = [dict(seed=True), dict(seed=-1), dict(seed='1'), dict(seed=12101, families='dense_random'),
                  dict(seed=12101, families=['not-family']), dict(seed=12101, families=['dense_random'] * 2),
                  dict(seed=12101, sizes='20'), dict(seed=12101, sizes=[True]),
                  dict(seed=12101, sizes=[21]), dict(seed=12101, sizes=[20, 20])]
        for args in inputs:
            with self.subTest(args=args), self.assertRaises(ValueError):
                build_stress_snapshots(**args)

    def test_validator_rejects_future_fields_times_noninteger_resources_and_bad_edges(self):
        base = deepcopy(self.snapshots[0])
        changes = [lambda s: s.update(synthetic=False), lambda s: s.update(size=True),
                   lambda s: s.update(size=50), lambda s: s.update(future_arrivals=[]),
                   lambda s: s.update(patients=[None]), lambda s: s.update(hospitals=[None]),
                   lambda s: s['patients'][0].update(t_req=11),
                   lambda s: s['patients'][0]['accepted_at'].update({s['hospitals'][0]['id']: 11}),
                   lambda s: s['patients'][0]['resource_demands'].update(ed_bed=True),
                   lambda s: s['patients'][0]['resource_demands'].update(ed_bed=-1),
                   lambda s: s['patients'][0]['resource_demands'].update(ed_bed=1.5),
                   lambda s: s['patients'][0].update(accepted_hospitals=[{}]),
                   lambda s: s['patients'][0].update(accepted_hospitals=['UNKNOWN']),
                   lambda s: s['patients'][0].update(travel_minutes={}),
                   lambda s: s['patients'][0].update(priority='unknown'),
                   lambda s: s['patients'][0].update(lat=float('nan')),
                   lambda s: s['patients'][0].update(resource_demands={'ed_bed': 1}),
                   lambda s: s['patients'][0].update(id=s['patients'][1]['id']),
                   lambda s: s['hospitals'][0].update(capacity={'ed_bed': 1}),
                   lambda s: s['hospitals'][0].update(capabilities=['general', 'general'])]
        for index, change in enumerate(changes):
            altered = deepcopy(base)
            change(altered)
            with self.subTest(index=index), self.assertRaises(ValueError):
                validate_stress_snapshot(altered)

    def test_zero_capacity_control_cannot_assign_any_patient(self):
        snapshot = build_stress_snapshots(DEVELOPMENT_SEEDS[0], ['no_capacity_control'], [20])[0]
        for code in experiment.POLICIES:
            branch = experiment.run_policy(snapshot, code)
            self.assertEqual(branch['metrics']['assigned_count'], 0)
            self.assertIsNone(branch['metrics']['travel_mean_minutes'])
            self.assertEqual(branch['metrics']['travel_denominator'], 0)

    def test_singleton_trap_has_known_greedy_loss(self):
        snapshot = self.snapshots[0]
        branch = experiment.run_policy(snapshot, 'R1')
        self.assertEqual(branch['metrics']['unassigned_count'], snapshot['size'] // 2)

    def test_abundant_control_allows_every_patient(self):
        snapshot = build_stress_snapshots(DEVELOPMENT_SEEDS[0], ['abundant_control'], [20])[0]
        for code in experiment.POLICIES:
            branch = experiment.run_policy(snapshot, code)
            self.assertEqual(branch['metrics']['unassigned_count'], 0)
            self.assertEqual(branch['metrics']['constraint_violations'], 0)


class StressExperiment(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snapshot = build_stress_snapshots(DEVELOPMENT_SEEDS[0], ['singleton_trap'], [20])[0]

    def test_cyclic_order_visits_every_position(self):
        orders = [experiment.execution_order(i) for i in range(8)]
        self.assertTrue(all(set(order) == set(experiment.POLICIES) for order in orders))
        for position in range(8):
            self.assertEqual({order[position] for order in orders}, set(experiment.POLICIES))

    def test_every_branch_receives_identical_unmutated_input(self):
        before = deepcopy(self.snapshot)
        result = experiment.run_case(self.snapshot, 2)
        self.assertEqual(before, self.snapshot)
        self.assertEqual(result['execution_order'], experiment.execution_order(2))
        self.assertEqual({b['input_sha256'] for b in result['branches']}, {experiment.digest(self.snapshot)})
        for branch in result['branches']:
            self.assertTrue(audit_assignment(self.snapshot, branch['assignments']))
            self.assertGreaterEqual(branch['wall_seconds'], 0)
            self.assertEqual(branch['metrics']['constraint_violations'], 0)
        self.assertEqual(len(result['branches']), 8)

    @staticmethod
    def _branch(code, budget, primary, secondary, proven=False):
        return dict(policy=f'{code}@{budget:g}', algorithm=code, budget_seconds=budget,
                    metrics=dict(unassigned_priority_weight=primary, priority_weighted_travel_minutes=secondary),
                    solver_records=[dict(optimal_proven=proven)])

    def test_any_cp_budget_can_provide_proven_reference(self):
        branches = [self._branch('C1', .01, 0, 100, True), self._branch('C1', .2, 2, 50),
                    self._branch('H1', .2, 0, 110)]
        ref = experiment.choose_reference(branches)
        self.assertEqual(ref['kind'], 'proven_optimum')
        self.assertEqual(ref['policy'], 'C1@0.01')
        self.assertEqual(ref['objective'], [0, 100])

    def test_unproven_reference_is_only_observed_best(self):
        branches = [self._branch('C1', .2, 2, 50), self._branch('H1', .2, 0, 110)]
        ref = experiment.choose_reference(branches)
        self.assertEqual(ref['kind'], 'observed_best')
        self.assertFalse(ref['optimal_proven'])
        self.assertEqual(ref['policy'], 'H1@0.2')

    def test_different_optimal_proofs_are_rejected(self):
        branches = [self._branch('C1', .01, 0, 100, True), self._branch('C1', .2, 0, 99, True)]
        with self.assertRaises(ValueError):
            experiment.choose_reference(branches)

    def test_no_algorithm_may_beat_proven_optimum(self):
        branches = [self._branch('C1', .2, 0, 100, True), self._branch('H1', .2, 0, 99)]
        with self.assertRaises(ValueError):
            experiment.choose_reference(branches)

    def test_secondary_gap_not_reported_when_primary_differs(self):
        branches = [self._branch('H1', .2, 2, 50), self._branch('C1', .2, 0, 100, True)]
        gaps = experiment.objective_gaps(branches, experiment.choose_reference(branches))
        self.assertEqual(gaps['H1@0.2']['primary_difference'], 2)
        self.assertIsNone(gaps['H1@0.2']['secondary_difference_if_primary_equal'])
        self.assertIsNone(gaps['H1@0.2']['relative_secondary_gap_if_primary_equal'])

    def test_summary_counts_cases_and_priority_denominators(self):
        result = experiment.run_case(self.snapshot, 0)
        summary = experiment.summarize([result])
        all_rows = next(row for row in summary if row['family'] == 'ALL')
        self.assertEqual(all_rows['cases'], 1)
        for policy in experiment.POLICIES:
            branch = next(b for b in result['branches'] if b['policy'] == policy)
            row = all_rows['policies'][policy]
            self.assertEqual(row['mean_unassigned_count'], branch['metrics']['unassigned_count'])
            self.assertEqual(row['wall_p95_seconds'], branch['wall_seconds'])
            self.assertEqual(sum(row['unassigned_by_priority_totals'].values()), branch['metrics']['unassigned_count'])
        for values in all_rows['h1_vs_c1'].values():
            self.assertEqual(values['better'] + values['equal'] + values['worse'], 1)

    def test_validation_without_lock_stops_before_input_generation(self):
        with patch.object(experiment, 'build_stress_snapshots', side_effect=AssertionError('must not run')):
            with self.assertRaises(ValueError):
                experiment.run('validation')

    def test_validation_rejects_mismatched_lock_before_input_generation(self):
        with patch.object(experiment, 'manifest', return_value={'actual': 1}), \
             patch.object(experiment, 'build_stress_snapshots', side_effect=AssertionError('must not run')):
            with self.assertRaises(ValueError):
                experiment.run('validation', {'wrong': 1})

    def test_cli_existing_lock_is_rejected_before_experiment(self):
        with tempfile.TemporaryDirectory() as directory:
            locked = Path(directory) / 'lock.json'
            locked.write_text('preserve', encoding='utf-8')
            with patch.object(experiment, 'run', side_effect=AssertionError('must not run')), \
                 redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                experiment.main(['--lock', '--lock-file', str(locked)])
            self.assertEqual(locked.read_text(encoding='utf-8'), 'preserve')

    def test_cli_existing_validation_outputs_are_rejected_before_experiment(self):
        for existing_suffix in ('.json', '.md'):
            with self.subTest(existing_suffix=existing_suffix), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / 'validation.json'
                output.with_suffix(existing_suffix).write_text('preserve', encoding='utf-8')
                with patch.object(experiment, 'run', side_effect=AssertionError('must not run')), \
                     redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    experiment.main(['--phase', 'validation', '--output', str(output)])
                self.assertEqual(output.with_suffix(existing_suffix).read_text(encoding='utf-8'), 'preserve')

    def test_cli_development_cannot_overwrite_existing_validation_report(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'reserved.json'
            output.write_text('{"phase":"validation"}', encoding='utf-8')
            with patch.object(experiment, 'run', side_effect=AssertionError('must not run')), \
                 redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                experiment.main(['--phase', 'development', '--output', str(output)])
            self.assertEqual(output.read_text(encoding='utf-8'), '{"phase":"validation"}')


if __name__ == '__main__':
    unittest.main()
