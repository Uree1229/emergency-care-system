"""관측형 문의 정책의 개발 선택·신규 검증·재생 선정 계약."""
import copy
from contextlib import redirect_stderr
import io
import json
import unittest
from unittest.mock import patch

from simulation.adaptive_experiment import (
    ADAPTIVE_POLICIES, DEFAULT_DEVELOPMENT_START, DEFAULT_VALIDATION_START,
    DEVELOPMENT_POLICIES, FIXED_POLICIES, PREVIOUS_VALIDATION_SEEDS,
    build_adaptive_scenarios, export_replay_cases, lock_development_policies,
    main, report_markdown, run_experiment, select_replay_cases,
    validate_seed_split, validation_policies,
)
from test_workload_experiment import branch, scenario, tuning_record


def records_for_candidates(start_by_id=None):
    settings = start_by_id or {}
    return [dict(tuning_record(p['id'], start=settings.get(p['id'], 10)),
                 scenario_id='dev', seed=101) for p in DEVELOPMENT_POLICIES]


class AdaptiveExperimentTest(unittest.TestCase):
    def test_predeclared_candidates_have_only_observable_policy_config(self):
        self.assertEqual([p['k'] for p in FIXED_POLICIES], [1, 3, 5])
        self.assertEqual(len(ADAPTIVE_POLICIES), 4)
        self.assertEqual({(p['adaptive_inquiry']['initial_k'], p['adaptive_inquiry']['slow_minutes'])
                          for p in ADAPTIVE_POLICIES}, {(1, 3), (1, 6), (3, 3), (3, 6)})
        for p in ADAPTIVE_POLICIES:
            self.assertNotIn('workers', p['adaptive_inquiry'])
            self.assertNotIn('queue', p['adaptive_inquiry'])
            self.assertEqual(p['adaptive_inquiry']['min_samples'], 3)
            self.assertEqual(p['k'], 5)

    def test_new_scenarios_are_immutable_distinct_and_cross_nonresponse(self):
        base = scenario()
        original = copy.deepcopy(base)
        development = build_adaptive_scenarios(base, 'development')
        validation = build_adaptive_scenarios(base, 'validation')
        self.assertEqual(base, original)
        self.assertEqual(len(development), 8)
        self.assertEqual(len(validation), 8)
        self.assertFalse({s['id'] for s in development} & {s['id'] for s in validation})
        self.assertEqual({s['experiment_nonresponse'] for s in validation}, {0, .2})
        self.assertEqual({s['case_kind'] for s in validation},
                         {'work_sufficient_w1', 'work_sufficient_w2', 'work_scarce_w1', 'work_shortage_w1'})
        value = validation[0]
        self.assertEqual(len(value['inquiry_queue']['background_jobs']), 12)
        self.assertEqual(value['timeout_minutes'], 17)
        self.assertEqual(value['experiment']['generation_selected_from_outcomes'], False)
        self.assertEqual(value['patients'][2]['search_start_t'] - value['patients'][0]['search_start_t'], 1)
        self.assertTrue(all('adaptive_' in s['id'] for s in development + validation))
        development[0]['patients'][0]['label'] = 'mutated'
        self.assertNotEqual(development[1]['patients'][0]['label'], 'mutated')

    def test_worker_control_pairs_have_only_worker_count_difference(self):
        values = build_adaptive_scenarios(scenario(), 'validation', (0,))
        left, right = copy.deepcopy(values[0]), copy.deepcopy(values[1])
        for key in ('id', 'label', 'case_kind', 'description'):
            left.pop(key, None)
            right.pop(key, None)
        for value in (left, right):
            value['inquiry_queue'].pop('workers')
            value['experiment'].pop('case_kind')
        self.assertEqual(left, right)

    def test_invalid_nonresponse_conditions_fail(self):
        for values in ((), (0, 0), (-.1,), (1.1,), (float('nan'),)):
            with self.subTest(values=values), self.assertRaises(ValueError):
                build_adaptive_scenarios(scenario(), nonresponse_levels=values)

    def test_fresh_disjoint_seeds_and_previous_validation_exclusion(self):
        self.assertEqual(DEFAULT_DEVELOPMENT_START, 101)
        self.assertEqual(DEFAULT_VALIDATION_START, 2001)
        validate_seed_split([101, 102], [2001, 2002])
        self.assertEqual(PREVIOUS_VALIDATION_SEEDS, frozenset(range(1001, 1021)))
        for dev, val in (([], [2001]), ([101, 101], [2001]), ([101], [101]),
                         ([101], [1001]), ([1020], [2001]), ([False], [2001]), ([-1], [2001])):
            with self.subTest(dev=dev, val=val), self.assertRaises(ValueError):
                validate_seed_split(dev, val)

    def test_best_fixed_and_best_adaptive_are_chosen_separately(self):
        rows = records_for_candidates({FIXED_POLICIES[1]['id']: 7, ADAPTIVE_POLICIES[2]['id']: 8})
        locked = lock_development_policies(rows)
        self.assertEqual(locked['selected_fixed_policy']['id'], FIXED_POLICIES[1]['id'])
        self.assertEqual(locked['selected_adaptive_policy']['id'], ADAPTIVE_POLICIES[2]['id'])
        self.assertFalse(locked['validation_retuning'])
        self.assertEqual(len(locked['fixed']['scores']), 3)
        self.assertEqual(len(locked['adaptive']['scores']), 4)

    def test_coverage_precedes_attractive_conditional_time(self):
        rows = records_for_candidates()
        rows[1] = dict(tuning_record(FIXED_POLICIES[1]['id'], start=1, missed=1), scenario_id='dev', seed=101)
        rows[4] = dict(tuning_record(ADAPTIVE_POLICIES[1]['id'], start=1, missed=1), scenario_id='dev', seed=101)
        locked = lock_development_policies(rows)
        self.assertEqual(locked['selected_fixed_policy']['id'], FIXED_POLICIES[0]['id'])
        self.assertEqual(locked['selected_adaptive_policy']['id'], ADAPTIVE_POLICIES[0]['id'])

    def test_validation_cannot_reselect_policy(self):
        rows = records_for_candidates()
        rows[-1]['split'] = 'validation'
        with self.assertRaises(ValueError):
            lock_development_policies(rows)

    def test_missing_case_for_all_adaptive_candidates_still_rejected(self):
        rows = records_for_candidates()
        for row in rows[3:]:
            row['scenario_id'] = 'different_case'
        with self.assertRaises(ValueError):
            lock_development_policies(rows)

    def test_validation_keeps_all_fixed_controls_but_only_locked_adaptive(self):
        locked = lock_development_policies(records_for_candidates())
        policies = validation_policies(locked)
        self.assertEqual(len(policies), 4)
        self.assertEqual(policies[0], locked['selected_fixed_policy'])
        self.assertEqual(sum('adaptive_inquiry' in p for p in policies), 1)
        self.assertEqual({p['id'] for p in policies if 'adaptive_inquiry' not in p},
                         {p['id'] for p in FIXED_POLICIES})

    def test_runner_locks_before_new_validation_and_applies_condition_probability(self):
        calls = []
        dev = [dict(scenario('dev0'), experiment_nonresponse=0),
               dict(scenario('dev20'), experiment_nonresponse=.2)]
        val = [dict(scenario('val0'), experiment_nonresponse=0),
               dict(scenario('val20'), experiment_nonresponse=.2)]
        originals = copy.deepcopy((dev, val))
        def generator(value, policies, routing):
            calls.append((value['id'], value['seed'], value['experiment_environment']['nonresponse'],
                          [p['id'] for p in policies], value['experiment_environment']['kind']))
            branches = [branch(value, p,
                start=(6 if p['id'] == FIXED_POLICIES[1]['id'] else 10)
                      if value['id'].startswith('dev') else
                      (20 if 'adaptive_inquiry' in p else 5)) for p in policies]
            for b in branches:
                b['metrics']['adaptive_decisions'] = 4
            return dict(scenario=value, branches=branches)
        payload = run_experiment(dev, val, [101, 102], [2001, 2002],
                                 generator=generator, bootstrap_samples=100)
        self.assertEqual(payload['tuning']['selected_fixed_policy']['id'], FIXED_POLICIES[1]['id'])
        self.assertEqual(payload['tuning']['selected_adaptive_policy']['id'], ADAPTIVE_POLICIES[0]['id'])
        self.assertEqual(payload['branch_runs'], 44)
        self.assertTrue(all(c[1] < 2001 for c in calls[:4]))
        validation_calls = calls[4:]
        self.assertTrue(all(len(c[3]) == 4 for c in validation_calls))
        self.assertTrue(all(ADAPTIVE_POLICIES[1]['id'] not in c[3] for c in validation_calls))
        self.assertEqual({c[2] for c in calls}, {0, .2})
        self.assertTrue(all(c[4] == 'adaptive_inquiry_validation' for c in calls))
        self.assertEqual((dev, val), originals)
        self.assertEqual(payload['suites']['validation']['records'][0]['metrics']['adaptive_decisions'], 4)
        self.assertFalse(payload['actual_system_comparison'])
        text = report_markdown(payload)
        self.assertIn('검증에서 재선택하지 않았다', text)
        self.assertIn('개선·악화', text)
        self.assertIn('실제 119 체계 대비 개선율', text)
        self.assertIn('6/6', text)
        json.dumps(payload, allow_nan=False)

    def test_overlap_rejected_before_any_engine_calls(self):
        def fail(*args, **kwargs):
            self.fail('invalid seeds reached generator')
        for seed in (101, 1001):
            with self.assertRaises(ValueError):
                run_experiment([scenario('dev')], [scenario('val')], [101], [seed], generator=fail)

    def test_replay_export_uses_first_seed_even_if_later_seed_is_better(self):
        locked = lock_development_policies(records_for_candidates())
        specs, records = [], []
        for kind in ('work_sufficient_w1', 'work_sufficient_w2'):
            value = dict(scenario(kind), case_kind=kind, experiment_nonresponse=0)
            specs.append(value)
            for seed, adaptive_time in ((2002, 1), (2001, 20)):
                from simulation.workload_experiment import workload_record
                for p in validation_policies(locked):
                    row = workload_record(value, branch(value, p,
                        start=adaptive_time if 'adaptive_inquiry' in p else 10))
                    row.update(scenario_id=value['id'], case_kind=kind, seed=seed,
                               nonresponse_probability=0)
                    records.append(row)
        picks = select_replay_cases(records, locked)
        self.assertEqual(len(picks), 2)
        self.assertEqual([p['seed'] for p in picks], [2001, 2001])
        self.assertEqual([p['difference'] for p in picks], [10, 10])
        self.assertTrue(all(p['outcome_independent_selection'] for p in picks))
        exported = export_replay_cases(dict(replay_cases=picks, tuning=locked,
                                            service_lognormal_sigma=0), specs)
        self.assertFalse(exported['geometry_included'])
        self.assertEqual(len(exported['scenarios']), 2)
        for entry in exported['scenarios']:
            value = entry['scenario']
            self.assertEqual(value['experiment_environment']['kind'], 'adaptive_inquiry_validation')
            self.assertEqual(len(value['policies']), 4)
            self.assertEqual(value['experiment_selection']['seed'], 2001)
            self.assertIn('유리한 seed를 다시 고른 예시가 아니다', value['description'])

    def test_cli_rejects_previously_used_holdout_seed_before_data_loading(self):
        with patch('simulation.adaptive_experiment.build_default_scenarios') as builder:
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                main(['--validation-seed-start', '1001'])
            builder.assert_not_called()


if __name__ == '__main__':
    unittest.main()
