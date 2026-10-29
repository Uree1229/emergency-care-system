"""Bounded checks of experiment provenance, censoring and paired metrics.

No full development/validation sweep is executed here. Small real replays test
the experiment's rotation wiring; lock failures and CLI overwrite protection
are checked before the replay runner can do work.
"""
from contextlib import contextmanager, redirect_stderr
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from simulation import h1_online_experiment as experiment
from simulation.h1_online_engine import run_h1_online_replay
from simulation.online_allocation_scenarios import build_online_scenarios
from test_h1_online_engine import HAS_CP_SAT, scarcity_scenario


@contextmanager
def workspace_tempdir(prefix):
    # A Windows sandbox may expose a temporary root whose children are not
    # writable. Keep disposable fixtures inside this test's workspace instead.
    workspace = Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory(prefix=prefix, dir=workspace) as name:
        target = Path(name).resolve()
        if not target.is_relative_to(workspace):
            raise AssertionError('temporary fixture escaped the workspace')
        yield target


def outcome(pid, arrived, *, priority=4, occur=0):
    return dict(id=pid, priority=priority, occur_t=occur, search_start_t=occur,
                first_commit_t=None if arrived is None else occur + 1,
                final_arrive_t=arrived,
                treatment_start_t=None if arrived is None else arrived + 1)


def raw_branch(outcomes):
    return dict(patient_outcomes=deepcopy(outcomes), events=[],
                allocation_decisions=[dict(elapsed_seconds=value) for value in (.001, .004, .002)],
                metrics={'observation_end_t': 50})


class H1ExperimentProvenanceTests(unittest.TestCase):
    def test_development_and_validation_seeds_are_disjoint_and_factors_fixed(self):
        self.assertEqual(len(experiment.DEV_SEEDS), 3)
        self.assertEqual(len(experiment.VALIDATION_SEEDS), 6)
        self.assertFalse(set(experiment.DEV_SEEDS) & set(experiment.VALIDATION_SEEDS))
        self.assertEqual(len(set(experiment.FAMILIES)), 9)
        self.assertEqual(experiment.POLICY_IDS, ('R1', 'H1', 'C1'))
        policies = experiment.SETTINGS['policies']
        self.assertEqual([item['id'] for item in policies], ['R1', 'H1', 'C1'])
        self.assertTrue(all((item['k'], item['wait'], item['notify']) == (3, 2, True)
                            for item in policies))
        h1 = next(item for item in policies if item['id'] == 'H1')
        cp = next(item for item in policies if item['id'] == 'C1')
        self.assertEqual((h1['time_limit_seconds'], h1['max_moves']), (.05, 2048))
        self.assertEqual(cp['time_limit_seconds'], .05)
        self.assertTrue(cp['require_backend'])
        for seed in (experiment.DEV_SEEDS[0], experiment.VALIDATION_SEEDS[0]):
            cases = build_online_scenarios(seed)
            self.assertEqual(len(cases), 9)
            self.assertEqual({case['family'] for case in cases}, set(experiment.FAMILIES))
            self.assertTrue(all(case['synthetic'] for case in cases))

    def test_changed_sources_settings_runtime_or_seeds_fail_before_simulation(self):
        with patch.object(experiment, 'source_hashes', return_value={'simulation/a.py': 'a' * 64}), \
                patch.object(experiment, 'runtime_versions', return_value={'python': 'test', 'ortools': 'test'}), \
                patch.object(experiment, 'run_h1_online_replay') as replay:
            locked = experiment.manifest()
            experiment.verify_lock(locked)
            altered = []
            for field in ('source_sha256', 'settings', 'runtime_versions', 'validation_seeds'):
                value = deepcopy(locked)
                if field == 'source_sha256':
                    value[field]['simulation/a.py'] = 'b' * 64
                elif field == 'settings':
                    value[field]['policies'][1]['max_moves'] += 1
                elif field == 'runtime_versions':
                    value[field]['ortools'] = 'changed-version'
                else:
                    value[field][0] += 1000
                altered.append((field, value))
            for field, value in altered:
                with self.subTest(changed=field), self.assertRaisesRegex(ValueError, '잠금'):
                    experiment.run('validation', value)
            replay.assert_not_called()

    def test_new_settings_change_is_rejected_against_an_existing_manifest(self):
        with patch.object(experiment, 'source_hashes', return_value={}), \
                patch.object(experiment, 'runtime_versions', return_value={}):
            locked = experiment.manifest()
            changed = deepcopy(experiment.SETTINGS)
            changed['policies'][0]['wait'] = 0
            with patch.object(experiment, 'SETTINGS', changed), self.assertRaisesRegex(ValueError, '잠금'):
                experiment.verify_lock(locked)

    def test_source_manifest_contains_reproducible_algorithm_and_config_files(self):
        hashes = experiment.source_hashes()
        self.assertIn('simulation/heuristic_assignment.py', hashes)
        self.assertIn('simulation/h1_online_engine.py', hashes)
        self.assertIn('simulation/h1_online_audit.py', hashes)
        self.assertIn('config/heuristic_assignment_selected_20261014.json', hashes)
        self.assertTrue(all(len(value) == 64 and int(value, 16) >= 0 for value in hashes.values()))

    def test_cli_refuses_any_existing_result_companion_before_running(self):
        with workspace_tempdir('.h1-experiment-test-') as directory:
            root = Path(directory)
            output = root / 'comparison.json'
            protected = [output, output.with_suffix('.md'), root / 'comparison_audit.json']
            for path in protected:
                with self.subTest(existing=path.name):
                    path.write_text('preserve this existing artifact', encoding='utf-8')
                    stderr = io.StringIO()
                    with patch.object(experiment, 'run') as run, redirect_stderr(stderr), \
                            self.assertRaises(SystemExit) as stopped:
                        experiment.main(['--output', str(output)])
                    self.assertEqual(stopped.exception.code, 2)
                    self.assertIn('output already exists', stderr.getvalue())
                    run.assert_not_called()
                    self.assertEqual(path.read_text(encoding='utf-8'), 'preserve this existing artifact')
                    path.unlink()

    def test_cli_refuses_existing_development_lock_and_validation_relock(self):
        with workspace_tempdir('.h1-lock-test-') as directory:
            output, lock = Path(directory) / 'new.json', Path(directory) / 'locked.json'
            lock.write_text('preserve lock', encoding='utf-8')
            arguments = [
                ['--output', str(output), '--lock', '--lock-file', str(lock)],
                ['--phase', 'validation', '--lock', '--output', str(output), '--lock-file', str(lock)],
            ]
            for argv in arguments:
                with self.subTest(argv=argv), patch.object(experiment, 'run') as run, \
                        redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as stopped:
                    experiment.main(argv)
                self.assertEqual(stopped.exception.code, 2)
                run.assert_not_called()
                self.assertEqual(lock.read_text(encoding='utf-8'), 'preserve lock')
                self.assertFalse(output.exists())


class H1ExperimentMetricTests(unittest.TestCase):
    def setUp(self):
        self.baseline = raw_branch([
            outcome('A', 10), outcome('B', 20, priority=2),
            outcome('C', None, priority=1, occur=5), outcome('D', 30),
            outcome('E', 18, priority=1),
        ])
        self.comparison = raw_branch([
            outcome('E', 18, priority=1), outcome('D', None),
            outcome('C', 12, priority=1, occur=5), outcome('B', 16, priority=2),
            outcome('A', 13),
        ])

    def test_common_patient_delta_uses_matching_ids_and_preserves_missing_sides(self):
        result = experiment.common_patient_metrics({}, self.baseline, self.comparison)['arrival']
        self.assertEqual(result['denominator'], 3)
        self.assertAlmostEqual(result['mean_difference_minutes'], -1 / 3)
        self.assertEqual(result['comparison_only'], 1)
        self.assertEqual(result['baseline_only'], 1)
        details = experiment.paired_arrival_details(self.baseline, self.comparison)
        self.assertEqual(details, dict(denominator=3, delayed=1, earlier=1, equal=1,
                                     max_delay_minutes=3, p95_difference_minutes=3))

    def test_arrival_p95_excludes_missing_endpoints_and_retains_denominator(self):
        result = experiment.diagnostic_metrics({}, self.comparison)
        self.assertEqual(result['arrival_denominator'], 4)
        self.assertEqual(result['arrival_p95_minutes'], 18)
        self.assertEqual(result['allocation_decision_denominator'], 3)
        self.assertAlmostEqual(result['allocation_wall_p95_ms'], 4)
        self.assertAlmostEqual(result['allocation_wall_max_ms'], 4)
        self.assertEqual(result['priority_groups']['4']['not_arrived'], 1)
        self.assertEqual(result['priority_groups']['1']['patients'], 2)
        summary = experiment.summary_metrics({}, self.comparison)
        self.assertEqual(summary['arrival_denominator'], 4)
        self.assertAlmostEqual(summary['arrival_mean_minutes'], 13.5)
        self.assertAlmostEqual(summary['arrival_capped_mean_minutes'], 20.8)

    def test_empty_arrival_and_decision_distributions_are_none_not_zero(self):
        value = raw_branch([outcome('A', None), outcome('B', None, priority=2)])
        value['allocation_decisions'] = []
        result = experiment.diagnostic_metrics({}, value)
        self.assertEqual(result['arrival_denominator'], 0)
        self.assertIsNone(result['arrival_p95_minutes'])
        self.assertEqual(result['allocation_decision_denominator'], 0)
        self.assertIsNone(result['allocation_wall_p95_ms'])
        self.assertIsNone(result['allocation_wall_max_ms'])
        # Matching populations are required by the experiment; use a complete
        # all-missing counterpart for the common-patient distribution.
        missing = raw_branch([outcome(p['id'], None, priority=p['priority'], occur=p['occur_t'])
                              for p in self.baseline['patient_outcomes']])
        paired = experiment.paired_arrival_details(self.baseline, missing)
        self.assertEqual(paired['denominator'], 0)
        self.assertIsNone(paired['max_delay_minutes'])
        self.assertIsNone(paired['p95_difference_minutes'])

    def test_p95_is_nearest_rank_and_not_automatically_the_maximum(self):
        value = raw_branch([outcome(f'P{index}', index + 1) for index in range(20)])
        result = experiment.diagnostic_metrics({}, value)
        self.assertEqual(result['arrival_denominator'], 20)
        self.assertEqual(result['arrival_p95_minutes'], 19)


@unittest.skipUnless(HAS_CP_SAT, 'optional OR-Tools backend is not installed')
class H1ExperimentSmallRunTests(unittest.TestCase):
    def test_selected_h1_config_drift_is_rejected_before_replay(self):
        with workspace_tempdir('.h1-selection-test-') as directory:
            root = Path(directory)
            (root / 'config').mkdir()
            (root / 'config/heuristic_assignment_selected_20261014.json').write_text(
                json.dumps({'parameters': {'time_limit_seconds': .10, 'max_moves': 2048}}),
                encoding='utf-8')
            with patch.object(experiment, 'ROOT', root), \
                    patch.object(experiment, 'run_h1_online_replay') as replay, \
                    self.assertRaisesRegex(ValueError, 'previous development selection'):
                experiment.run('development')
            replay.assert_not_called()

    def test_rotation_wiring_preserves_canonical_branches_in_three_tiny_replays(self):
        families = ('scarce_greedy_trap', 'abundant_control', 'staggered_singleton')
        cases = []
        for index, family in enumerate(families):
            case = scarcity_scenario()
            case.update(id=f'h1-experiment-small-{index}', family=family, seed=15101)
            cases.append(case)
        progress = Mock()
        with patch.object(experiment, 'DEV_SEEDS', (15101,)), \
                patch.object(experiment, 'FAMILIES', families), \
                patch.object(experiment, 'build_online_scenarios', return_value=cases), \
                patch.object(experiment, 'source_hashes', return_value={}), \
                patch.object(experiment, 'runtime_versions', return_value={'python': 'test', 'ortools': 'test'}), \
                patch.object(experiment, 'run_h1_online_replay', wraps=run_h1_online_replay) as replay:
            report = experiment.run('development', progress=progress)
        self.assertEqual([call.kwargs['execution_order'] for call in replay.call_args_list],
                         [('R1', 'H1', 'C1'), ('H1', 'C1', 'R1'), ('C1', 'R1', 'H1')])
        self.assertEqual(len(report['results']), 3)
        self.assertEqual(progress.call_count, 3)
        for case in report['results']:
            self.assertEqual([branch['id'] for branch in case['branches']], ['R1', 'H1', 'C1'])
            self.assertEqual(set(case['summaries']), {'R1', 'H1', 'C1'})
            self.assertEqual(case['summaries']['H1']['unassigned'], 0)
        first, second, third = [next(b for b in case['branches'] if b['id'] == 'H1')
                                 for case in report['results']]
        self.assertEqual(first['events'], second['events'])
        self.assertEqual(first['events'], third['events'])
        json.dumps(report, allow_nan=False)


if __name__ == '__main__':
    unittest.main()
