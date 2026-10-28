"""Guard against seed reuse, changed locks, lost denominators and overwrites."""
from contextlib import redirect_stderr
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from simulation import opportunity_experiment as experiment


ROOT = Path(__file__).resolve().parent


class OpportunityExperimentTests(unittest.TestCase):
    def test_holdout_seeds_are_disjoint_from_every_previous_locked_experiment(self):
        current = set(experiment.DEV_SEEDS) | set(experiment.VALIDATION_SEEDS)
        self.assertFalse(set(experiment.DEV_SEEDS) & set(experiment.VALIDATION_SEEDS))
        for name in ('partial_information_lock_20261015.json', 'h1_online_lock_20261015.json'):
            lock = json.loads((ROOT / 'config' / name).read_text(encoding='utf-8'))
            self.assertFalse(current & (set(lock['development_seeds']) | set(lock['validation_seeds'])))
        self.assertEqual(len(experiment.POLICY_IDS), 8)
        self.assertEqual(len(experiment.FAMILIES), 16)

    def test_validation_checks_lock_before_any_scenario_or_replay(self):
        with patch.object(experiment, 'source_hashes', return_value={'a': 'a'*64}), \
                patch.object(experiment, 'runtime_versions', return_value={}), \
                patch.object(experiment, 'build_opportunity_scenarios') as scenarios, \
                patch.object(experiment, 'run_opportunity_replay') as replay:
            original = experiment.manifest()
            experiment.verify_lock(original)
            for field in ('source_sha256', 'settings', 'development_seeds', 'validation_seeds',
                          'families', 'runtime_versions'):
                bad = deepcopy(original)
                bad[field] = None
                with self.subTest(field=field), self.assertRaises(ValueError):
                    experiment.run('validation', bad)
            scenarios.assert_not_called()
            replay.assert_not_called()

    def test_hold_checks_and_unique_patients_and_budget_are_distinct(self):
        branch = dict(events=[dict(type='opportunity_hold', patient='A', t=1, held_since=1, deadline=4),
                              dict(type='opportunity_hold', patient='A', t=2, held_since=1, deadline=4),
                              dict(type='opportunity_hold', patient='B', t=2, held_since=2, deadline=3)],
                      protection_records=[{}, {}, {}], metrics={'protection_wall_seconds': .002})
        self.assertEqual(experiment.guard_metrics(branch), dict(guard_calls=3, hold_checks=3,
            held_patients=2, max_hold_age_minutes=1, max_hold_deadline_minutes=3,
            protection_wall_seconds=.002))

    def test_common_arrivals_do_not_hide_patients_lost_or_gained(self):
        def branch(rows):
            return dict(patient_outcomes=[dict(id=pid, occur_t=0, search_start_t=0,
                final_arrive_t=arrival, first_commit_t=arrival,
                treatment_start_t=arrival) for pid, arrival in rows])
        left = branch([('A', 10), ('B', None), ('C', 12)])
        right = branch([('C', None), ('A', 8), ('B', 20)])
        pair = experiment.common_patient_metrics({}, left, right)['arrival']
        self.assertEqual(pair, dict(denominator=1, mean_difference_minutes=-2,
                                   comparison_only=1, baseline_only=1))

    def test_markdown_preserves_zero_common_denominator_and_one_sided_counts(self):
        report = dict(phase='development', families=['unit'], limitations=[], results=[dict(
            scenario={'family':'unit'},
            summaries={key: {'not_arrived': 3 if key.startswith('observed') else 2}
                       for key in ('observed_H1', 'observed_C1', 'protected_H1', 'protected_C1')},
            paired_vs_observed={r: {'arrival': {'denominator':0, 'mean_difference_minutes':None,
                                                'comparison_only':1, 'baseline_only':0}} for r in ('H1','C1')},
            paired_protection_details={r: {'earlier':0, 'delayed':0} for r in ('H1','C1')},
            guard_metrics={r: {'held_patients':1} for r in ('protected_H1','protected_C1')})])
        value = experiment.markdown(report)
        self.assertIn('| unit | H1 | 3 | 2 | 0 | — | 0 | 0 | 1 |', value)

    def test_cli_rejects_existing_companions_and_path_aliases_without_run(self):
        with tempfile.TemporaryDirectory(prefix='.opportunity-runner-', dir=ROOT) as name:
            directory = Path(name)
            target = directory / 'result.json'
            for path in (target, target.with_suffix('.md'), directory / 'result_audit.json'):
                path.write_bytes(b'preserve')
                with patch.object(experiment, 'run') as run, redirect_stderr(io.StringIO()), \
                        self.assertRaises(SystemExit):
                    experiment.main(['--output', str(target)])
                self.assertEqual(path.read_bytes(), b'preserve')
                run.assert_not_called()
                path.unlink()
            for alias in (target, target.with_suffix('.md'), directory / 'result_audit.json'):
                with patch.object(experiment, 'run') as run, redirect_stderr(io.StringIO()), \
                        self.assertRaises(SystemExit):
                    experiment.main(['--output',str(target),'--lock','--lock-file',str(alias)])
                run.assert_not_called()

    def test_cli_rejects_non_json_and_validation_relock_before_run(self):
        for args in (['--output','result.md'], ['--phase','validation','--lock']):
            with patch.object(experiment, 'run') as run, redirect_stderr(io.StringIO()), \
                    self.assertRaises(SystemExit):
                experiment.main(args)
            run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
