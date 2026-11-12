"""병원별 관측 V2: 개발 선택, 새 검증, V1 고정, 결과 독립 재생 계약."""
import copy
from contextlib import redirect_stderr
import io
import json
import unittest
from unittest.mock import patch

from simulation.observed_experiment import (
    DEFAULT_DEVELOPMENT_START, DEFAULT_VALIDATION_START, DEVELOPMENT_POLICIES,
    FIXED_POLICIES, OBSERVED_POLICIES, PREVIOUS_VALIDATION_SEEDS, REPLAY_FAMILIES,
    V1_REFERENCE, export_replay_cases, lock_development_policies, main,
    report_markdown, resume_validation, run_experiment, select_replay_cases,
    validate_development_input, validate_seed_split, validation_policies,
)
from test_workload_experiment import branch, scenario, tuning_record


def candidate_records(start_by_id=None):
    starts = start_by_id or {}
    return [dict(tuning_record(p['id'], start=starts.get(p['id'], 10)),
                 scenario_id='dev', seed=201) for p in DEVELOPMENT_POLICIES]


class ObservedExperimentTest(unittest.TestCase):
    def saved_development(self):
        from simulation.observed_experiment import _canonical_policy
        dev, val = [scenario('dev')], [scenario('val')]
        def generator(value, policies, routing):
            branches = [branch(value, p) for p in policies]
            for b, p in zip(branches, policies):
                b['policy'] = _canonical_policy(p)
            return dict(scenario=value, branches=branches)
        payload = run_experiment(dev, val, [201], [3001], generator=generator,
                                 bootstrap_samples=100, development_only=True)
        return payload, dev, val

    def test_predeclared_policy_candidates_and_locked_v1_reference(self):
        self.assertEqual(len(DEVELOPMENT_POLICIES), 8)
        self.assertEqual([p['k'] for p in FIXED_POLICIES], [1, 3, 5])
        self.assertEqual({(p['observed_inquiry']['congestion_weight'],
                           p['observed_inquiry']['hedge_after']) for p in OBSERVED_POLICIES},
                         {(0, 2), (0, 4), (1, 2), (1, 4)})
        for policy in OBSERVED_POLICIES:
            self.assertEqual(policy['k'], 3)
            self.assertNotIn('workers', policy['observed_inquiry'])
            self.assertNotIn('queue', policy['observed_inquiry'])
            self.assertEqual(policy['observed_inquiry']['max_new_per_review'], 1)
        from simulation.adaptive_experiment import ADAPTIVE_POLICIES
        old = next(p for p in ADAPTIVE_POLICIES if p['id'] == 'adaptive_i1_s3')
        self.assertEqual(V1_REFERENCE['adaptive_inquiry'], old['adaptive_inquiry'])

    def test_fresh_seeds_block_both_seen_validation_rounds(self):
        self.assertEqual(DEFAULT_DEVELOPMENT_START, 201)
        self.assertEqual(DEFAULT_VALIDATION_START, 3001)
        validate_seed_split([201, 208], [3001, 3020])
        self.assertEqual(PREVIOUS_VALIDATION_SEEDS,
                         frozenset(range(1001, 1021)) | frozenset(range(2001, 2021)))
        for dev, val in (([], [3001]), ([201, 201], [3001]), ([201], [201]),
                         ([1001], [3001]), ([201], [2020]), ([False], [3001]), ([-1], [3001])):
            with self.subTest(dev=dev, val=val), self.assertRaises(ValueError):
                validate_seed_split(dev, val)

    def test_best_fixed_and_v2_are_selected_separately_v1_not_retuned(self):
        locked = lock_development_policies(candidate_records({FIXED_POLICIES[1]['id']: 7,
            OBSERVED_POLICIES[2]['id']: 8, V1_REFERENCE['id']: 1}))
        self.assertEqual(locked['selected_fixed_policy']['id'], FIXED_POLICIES[1]['id'])
        self.assertEqual(locked['selected_observed_policy']['id'], OBSERVED_POLICIES[2]['id'])
        self.assertEqual(locked['v1_reference'], V1_REFERENCE)
        self.assertFalse(locked['validation_retuning'])
        self.assertEqual(len(locked['fixed']['scores']), 3)
        self.assertEqual(len(locked['observed']['scores']), 4)

    def test_coverage_precedes_conditional_time_and_all_candidates_share_cases(self):
        rows = candidate_records()
        rows[1] = dict(tuning_record(FIXED_POLICIES[1]['id'], start=1, missed=1), scenario_id='dev', seed=201)
        rows[5] = dict(tuning_record(OBSERVED_POLICIES[1]['id'], start=1, missed=1), scenario_id='dev', seed=201)
        locked = lock_development_policies(rows)
        self.assertEqual(locked['selected_fixed_policy']['id'], FIXED_POLICIES[0]['id'])
        self.assertEqual(locked['selected_observed_policy']['id'], OBSERVED_POLICIES[0]['id'])
        for row in rows[4:]:
            row['scenario_id'] = 'different_case'
        with self.assertRaises(ValueError):
            lock_development_policies(rows)

    def test_validation_records_cannot_change_locked_policy(self):
        rows = candidate_records()
        rows[-1]['split'] = 'validation'
        with self.assertRaises(ValueError):
            lock_development_policies(rows)

    def test_validation_keeps_all_fixed_one_v2_and_unchanged_v1(self):
        locked = lock_development_policies(candidate_records())
        policies = validation_policies(locked)
        self.assertEqual(len(policies), 5)
        self.assertEqual(policies[0], locked['selected_fixed_policy'])
        self.assertEqual(sum('observed_inquiry' in p for p in policies), 1)
        self.assertEqual(sum('adaptive_inquiry' in p for p in policies), 1)
        self.assertEqual(next(p for p in policies if 'adaptive_inquiry' in p), V1_REFERENCE)
        self.assertTrue({p['id'] for p in FIXED_POLICIES} <= {p['id'] for p in policies})

    def test_runner_freezes_before_holdout_and_preserves_engine_diagnostics(self):
        calls = []
        dev = [dict(scenario('dev0'), experiment_nonresponse=0), dict(scenario('dev20'), experiment_nonresponse=.2)]
        val = [dict(scenario('val0'), experiment_nonresponse=0), dict(scenario('val20'), experiment_nonresponse=.2)]
        originals = copy.deepcopy((dev, val))
        def generator(value, policies, routing):
            calls.append((value['id'], value['seed'], value['experiment_environment']['nonresponse'],
                          [p['id'] for p in policies], value['experiment_environment']['kind']))
            branches = [branch(value, p, start=(6 if p['id'] == FIXED_POLICIES[1]['id'] else 10)
                              if value['id'].startswith('dev') else (20 if 'observed_inquiry' in p else 5))
                        for p in policies]
            for b in branches:
                b['metrics'].update(observed_hedges=4, observed_decisions=12, observed_max_in_flight=2)
            return dict(scenario=value, branches=branches)
        payload = run_experiment(dev, val, [201, 202], [3001, 3002], generator=generator, bootstrap_samples=100)
        self.assertEqual(payload['tuning']['selected_fixed_policy']['id'], FIXED_POLICIES[1]['id'])
        self.assertEqual(payload['tuning']['selected_observed_policy']['id'], OBSERVED_POLICIES[0]['id'])
        self.assertEqual(payload['branch_runs'], 52)
        self.assertTrue(all(c[1] < 3001 for c in calls[:4]))
        self.assertTrue(all(len(c[3]) == 5 for c in calls[4:]))
        self.assertTrue(all(OBSERVED_POLICIES[1]['id'] not in c[3] for c in calls[4:]))
        self.assertEqual({c[2] for c in calls}, {0, .2})
        self.assertTrue(all(c[4] == 'observed_inquiry_validation' for c in calls))
        self.assertEqual((dev, val), originals)
        self.assertEqual(payload['suites']['validation']['records'][0]['metrics']['observed_hedges'], 4)
        text = report_markdown(payload)
        self.assertIn('검증에서 재선택하지 않았다', text)
        self.assertIn('개선·악화', text)
        self.assertIn('observed_hedges', text)
        self.assertIn('6/6', text)
        json.dumps(payload, allow_nan=False)

    def test_development_only_never_calls_holdout_generator_or_exports_cases(self):
        seen = []
        def generator(value, policies, routing):
            seen.append(value['id'])
            self.assertEqual(value['id'], 'dev')
            return dict(scenario=value, branches=[branch(value, p) for p in policies])
        payload = run_experiment([scenario('dev')], [scenario('val')], [201], [3001],
                                 generator=generator, bootstrap_samples=100, development_only=True)
        self.assertEqual(seen, ['dev'])
        self.assertEqual(payload['branch_runs'], 8)
        self.assertEqual(payload['suites']['validation']['records'], [])
        self.assertEqual(payload['replay_cases'], [])
        self.assertIn('아직 실행하지 않았다', report_markdown(payload))

    def test_invalid_overlap_rejected_before_engine_calls(self):
        def fail(*args, **kwargs):
            self.fail('invalid input reached engine')
        with self.assertRaises(ValueError):
            run_experiment([scenario('same')], [scenario('same')], [201], [3001], generator=fail)
        for seed in (201, 1001, 2001):
            with self.subTest(seed=seed), self.assertRaises(ValueError):
                run_experiment([scenario('dev')], [scenario('val')], [201], [seed], generator=fail)

    def test_replay_selection_is_first_seed_predeclared_families_even_when_worse(self):
        from simulation.workload_experiment import workload_record
        locked = lock_development_policies(candidate_records())
        specs, records = [], []
        for family in (*REPLAY_FAMILIES, 'not_predeclared'):
            value = dict(scenario(family), case_kind=family, experiment_nonresponse=0)
            specs.append(value)
            for seed, observed_time in ((3002, 1), (3001, 20)):
                for p in validation_policies(locked):
                    row = workload_record(value, branch(value, p, start=observed_time if 'observed_inquiry' in p else 10))
                    row.update(scenario_id=value['id'], case_kind=family, seed=seed, nonresponse_probability=0)
                    records.append(row)
        picks = select_replay_cases(records, locked)
        self.assertEqual(len(picks), 3)
        self.assertEqual([p['seed'] for p in picks], [3001] * 3)
        self.assertEqual([p['difference'] for p in picks], [10] * 3)
        self.assertTrue(all(p['outcome_independent_selection'] for p in picks))
        exported = export_replay_cases(dict(replay_cases=picks, tuning=locked, service_lognormal_sigma=0), specs)
        self.assertFalse(exported['geometry_included'])
        self.assertEqual(len(exported['scenarios']), 3)
        for entry in exported['scenarios']:
            value = entry['scenario']
            self.assertEqual(value['experiment_environment']['kind'], 'observed_inquiry_validation')
            self.assertEqual(len(value['policies']), 5)
            self.assertEqual(value['experiment_selection']['seed'], 3001)
            self.assertIn('유리한 검증 결과를 고른 예시가 아니다', value['description'])

    def test_cli_rejects_old_seeds_before_reading_public_database(self):
        for seed in ('1001', '2001'):
            with self.subTest(seed=seed), patch('simulation.observed_experiment.build_default_scenarios') as builder:
                with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    main(['--validation-seed-start', seed])
                builder.assert_not_called()

    def test_resume_runs_only_validation_without_development_reselection(self):
        saved, dev, val = self.saved_development()
        original, calls = copy.deepcopy(saved), []
        def generator(value, policies, routing):
            calls.append((value['id'], value['seed'], [p['id'] for p in policies]))
            self.assertEqual(value['id'], 'val')
            return dict(scenario=value, branches=[branch(value, p) for p in policies])
        with patch('simulation.observed_experiment.lock_development_policies',
                   side_effect=AssertionError('frozen selection must not run again')):
            payload = resume_validation(saved, dev, val, [201], [3001],
                generator=generator, bootstrap_samples=100)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(calls[0][2]), 5)
        self.assertEqual(saved, original)
        self.assertEqual(payload['tuning'], saved['tuning'])
        self.assertEqual(payload['suites']['development'], saved['suites']['development'])
        self.assertFalse(payload['development_reexecuted'])
        self.assertFalse(payload['development_reselected'])
        self.assertFalse(payload['development_only'])
        self.assertEqual(payload['branch_runs'], 13)
        self.assertEqual(payload['patient_runs'], 39)

    def test_resume_rejects_already_validated_or_wrong_experiment_before_engine(self):
        saved, dev, val = self.saved_development()
        variants = []
        for key, value in (('development_only', False), ('experiment', 'adaptive_inquiry_v1')):
            changed = copy.deepcopy(saved)
            changed[key] = value
            variants.append(changed)
        changed = copy.deepcopy(saved)
        changed['suites']['validation']['records'] = [{'seed': 3001}]
        variants.append(changed)
        changed = copy.deepcopy(saved)
        changed['replay_cases'] = [{'seed': 3001}]
        variants.append(changed)
        for value in variants:
            with self.subTest(value=value.get('experiment')):
                with patch('simulation.observed_experiment._run_conditions') as runner:
                    with self.assertRaises(ValueError):
                        resume_validation(value, dev, val, [201], [3001])
                    runner.assert_not_called()

    def test_resume_rejects_changed_seed_sigma_or_scenario(self):
        saved, dev, val = self.saved_development()
        for dev_seeds, val_seeds, sigma in (([202], [3001], .35), ([201], [3002], .35),
                                           ([201], [201], .35), ([201], [2001], .35),
                                           ([201], [3001], .4)):
            with self.subTest(dev=dev_seeds, val=val_seeds, sigma=sigma), self.assertRaises(ValueError):
                validate_development_input(saved, dev, val, dev_seeds, val_seeds, sigma)
        for specs in (dev, val):
            changed = copy.deepcopy(specs)
            changed[0]['timeout_minutes'] += 1
            with self.assertRaises(ValueError):
                validate_development_input(saved, changed if specs is dev else dev,
                    changed if specs is val else val, [201], [3001], .35)

    def test_resume_checks_current_candidate_settings_and_exact_coverage(self):
        saved, dev, val = self.saved_development()
        variants = []
        changed = copy.deepcopy(saved)
        changed['suites']['development']['records'][0]['policy']['k'] = 99
        variants.append(changed)
        changed = copy.deepcopy(saved)
        changed['suites']['development']['records'].pop()
        variants.append(changed)
        changed = copy.deepcopy(saved)
        changed['suites']['development']['records'].append(copy.deepcopy(changed['suites']['development']['records'][0]))
        variants.append(changed)
        changed = copy.deepcopy(saved)
        changed['suites']['development']['records'][0]['metrics']['patients'] = 99
        variants.append(changed)
        changed = copy.deepcopy(saved)
        changed['tuning']['selected_observed_policy']['observed_inquiry']['hedge_after'] = 99
        variants.append(changed)
        changed = copy.deepcopy(saved)
        changed['tuning']['observed']['scores'][0]['conditional_start_mean'] += 1
        variants.append(changed)
        for index, value in enumerate(variants):
            with self.subTest(variant=index), self.assertRaises(ValueError):
                validate_development_input(value, dev, val, [201], [3001], .35)

    def test_cli_development_input_cannot_be_overwritten_or_combined_with_development_only(self):
        for args in (['--development-only', '--development-input', 'saved.json'],
                     ['--development-input', 'simulation_results/observed_inquiry.json']):
            with self.subTest(args=args), patch('simulation.observed_experiment.build_default_scenarios') as builder:
                with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    main(args)
                builder.assert_not_called()


if __name__ == '__main__':
    unittest.main()
