"""우선순위 실험의 개발 잠금·미사용 검증·군별 분모·선택 독립 재생 계약."""
import copy
from contextlib import redirect_stderr
import io
import json
import unittest
from unittest.mock import patch

from simulation.priority_experiment import (
    DEFAULT_DEVELOPMENT_START, DEFAULT_VALIDATION_START, DEVELOPMENT_POLICIES,
    FIXED_POLICIES, LOCKED_V2, OBJECTIVE_ORDER, PREVIOUS_VALIDATION_SEEDS,
    PRIORITY_POLICIES, REPLAY_CONDITIONS, _canonical_policy, _paired_group,
    comparison_summary, export_replay_cases, lock_development_policies, main,
    priority_summary, report_markdown, resume_validation, run_experiment,
    score_policy, select_replay_cases, validate_development_input,
    validate_seed_split, validation_policies,
)
from simulation.workload_experiment import workload_record
from test_workload_experiment import branch, scenario


def record(policy, start=10, missed=0, high_missing=False, sid='dev', seed=301):
    value = scenario(sid)
    b = branch(value, policy, start=start, missed=missed)
    if high_missing:
        b['patient_outcomes'][0].update(state='UNASSIGNED', first_commit_t=None,
            final_arrive_t=None, treatment_start_t=None, treatment_complete_t=None)
    r = workload_record(value, b)
    r.update(scenario_id=sid, scenario_label=value['label'], case_kind='tiny', family=value['family'],
        seed=seed, split='development', nonresponse_probability=0)
    return r


def records(starts=None):
    return [record(p, start=(starts or {}).get(p['id'], 10)) for p in DEVELOPMENT_POLICIES]


def generator(value, policies, routing):
    branches = [branch(value, p) for p in policies]
    for b, p in zip(branches, policies):
        b['policy'] = _canonical_policy(p)
        b['metrics'].update(observed_decisions=7, observed_hedges=2)
    return dict(scenario=value, branches=branches)


class PriorityExperimentTest(unittest.TestCase):
    def saved(self):
        d, v = [scenario('dev')], [scenario('val')]
        return run_experiment(d, v, [301], [5001], generator=generator,
            bootstrap_samples=100, development_only=True), d, v

    def test_candidate_contract_locks_v2_and_complexity_tie_order(self):
        self.assertEqual(len(DEVELOPMENT_POLICIES), 8)
        self.assertEqual([p['k'] for p in FIXED_POLICIES], [1, 3, 5])
        self.assertEqual([p['priority_order'] for p in PRIORITY_POLICIES], ['requests', 'all', 'all', 'all'])
        self.assertEqual(PRIORITY_POLICIES[-1]['rule'], 'R4')
        self.assertEqual(PRIORITY_POLICIES[2]['high_priority_hedge_after'], 1)
        from simulation.observed_experiment import OBSERVED_POLICIES
        selected = next(p for p in OBSERVED_POLICIES if p['id'] == 'observed_v2_w1_h2')
        self.assertEqual(LOCKED_V2['observed_inquiry'], selected['observed_inquiry'])
        locked = lock_development_policies(records())
        self.assertEqual(locked['selected_priority_policy'], PRIORITY_POLICIES[0])
        self.assertEqual(locked['selected_fixed_policy'], FIXED_POLICIES[0])
        self.assertEqual(locked['objective_order'], OBJECTIVE_ORDER)
        self.assertEqual(locked['v2_reference'], LOCKED_V2)

    def test_previously_used_seed_rounds_and_overlap_are_rejected(self):
        self.assertEqual((DEFAULT_DEVELOPMENT_START, DEFAULT_VALIDATION_START), (301, 5001))
        self.assertEqual(len(PREVIOUS_VALIDATION_SEEDS), 80)
        validate_seed_split([301, 308], [5001, 5020])
        for d, v in (([], [5001]), ([301, 301], [5001]), ([301], [301]),
                     ([True], [5001]), ([-1], [5001]), ([1001], [5001]),
                     ([301], [2001]), ([301], [3001]), ([301], [4001])):
            with self.subTest(d=d, v=v), self.assertRaises(ValueError):
                validate_seed_split(d, v)

    def test_high_priority_coverage_precedes_total_and_conditional_speed(self):
        rows = records()
        # Missing the high patient is worse even if its conditional times are short.
        rows[4] = record(PRIORITY_POLICIES[0], start=1, high_missing=True)
        # Candidate 2 misses one low patient while keeping the high patient.
        r = record(PRIORITY_POLICIES[1], start=50)
        r['outcomes'][1].update(treatment_start_t=None, final_arrive_t=None, first_commit_t=None)
        r['metrics'].update(not_treated=1, not_arrived=1, start_observed=2)
        rows[5] = r
        locked = lock_development_policies(rows)
        # All coverage candidates win over the low-missing candidate.
        self.assertEqual(locked['selected_priority_policy'], PRIORITY_POLICIES[2])
        rows[6] = record(PRIORITY_POLICIES[2], high_missing=True)
        rows[7] = record(PRIORITY_POLICIES[3], high_missing=True)
        self.assertEqual(lock_development_policies(rows)['selected_priority_policy'], PRIORITY_POLICIES[1])

    def test_incomplete_or_incompatible_patient_coverage_cannot_win(self):
        rows = records()
        for change in ('missing', 'duplicate', 'identity', 'priority', 'split'):
            changed = copy.deepcopy(rows)
            if change == 'missing': changed.pop()
            elif change == 'duplicate': changed.append(copy.deepcopy(changed[-1]))
            elif change == 'identity': changed[-1]['outcomes'][0]['id'] = 'wrong'
            elif change == 'priority': changed[-1]['outcomes'][0]['priority'] = 1
            else: changed[-1]['split'] = 'validation'
            with self.subTest(change=change), self.assertRaises(ValueError):
                lock_development_policies(changed)

    def test_group_denominators_and_censoring_keep_nulls(self):
        r = record(LOCKED_V2, start=10, high_missing=True)
        summary = priority_summary([r])
        high = summary['by_priority']['4']
        self.assertEqual((high['patients'], high['not_treated'], high['start_observed']), (1, 1, 0))
        self.assertIsNone(high['start_mean_minutes'])
        self.assertIsNone(high['start_p95_minutes'])
        self.assertEqual(summary['overall']['start_observed'], 2)
        self.assertEqual(summary['overall']['start_mean_minutes'], 10)
        self.assertIsNone(score_policy([r])['priority4_conditional_start_mean'])
        json.dumps(summary, allow_nan=False)

    def test_nearest_rank_p95_does_not_include_censored_patients(self):
        r = record(LOCKED_V2)
        r['outcomes'][0]['treatment_start_t'] = r['outcomes'][0]['search_start_t'] + 100
        a = priority_summary([r])['overall']
        self.assertEqual(a['start_p95_minutes'], 100)
        self.assertEqual(a['start_max_minutes'], 100)

    def test_whole_seed_pairing_keeps_common_identity_and_seed_clustering(self):
        base, after = [], []
        for seed, time in ((5001, 12), (5002, 8)):
            for sid in ('a', 'b'):
                base.append(record(LOCKED_V2, sid=sid, seed=seed))
                after.append(record(PRIORITY_POLICIES[0], start=time, sid=sid, seed=seed))
        after[0]['outcomes'][0]['treatment_start_t'] = None
        s = _paired_group(base, after, samples=100)
        self.assertEqual(s['paired_seed_count'], 2)
        self.assertEqual(s['common_patient_total'], 11)
        self.assertEqual(len(s['seed_details']), 2)
        self.assertEqual(s['seed_details'][0]['common_patients'], 5)
        self.assertEqual(s['mean_difference'], 0)
        self.assertEqual(s['regression_seed_count'], 1)
        self.assertEqual(s['interval_method'], 'paired_whole_seed_percentile_bootstrap')

    def test_runner_freezes_before_validation_and_preserves_diagnostics(self):
        calls = []
        def observing(value, policies, routing):
            calls.append((value['id'], len(policies)))
            return generator(value, policies, routing)
        payload = run_experiment([scenario('dev')], [scenario('val')], [301], [5001],
            generator=observing, bootstrap_samples=100)
        self.assertEqual(calls, [('dev', 8), ('val', 5)])
        self.assertEqual(payload['branch_runs'], 13)
        self.assertEqual(payload['patient_runs'], 39)
        self.assertEqual(len(validation_policies(payload['tuning'])), 5)
        suite = payload['suites']['validation']
        self.assertEqual(suite['baseline_id'], LOCKED_V2['id'])
        self.assertEqual(suite['records'][0]['metrics']['observed_decisions'], 7)
        self.assertEqual(len(suite['overall_summary']), 5)
        self.assertEqual(len(suite['policy_comparisons']), 5)
        self.assertIn('검증에서 재선택하지 않았다', report_markdown(payload))
        self.assertIn('상황별 개선·악화', report_markdown(payload))
        json.dumps(payload, allow_nan=False)

    def test_development_only_never_runs_holdout(self):
        calls = []
        def observing(value, policies, routing):
            calls.append(value['id'])
            return generator(value, policies, routing)
        payload = run_experiment([scenario('dev')], [scenario('val')], [301], [5001],
            generator=observing, bootstrap_samples=100, development_only=True)
        self.assertEqual(calls, ['dev'])
        self.assertEqual(payload['replay_cases'], [])
        self.assertEqual(payload['suites']['validation']['records'], [])
        self.assertIn('아직 실행하지 않았다', report_markdown(payload))

    def test_resume_executes_only_holdout_and_preserves_saved_development(self):
        saved, d, v = self.saved()
        original, calls = copy.deepcopy(saved), []
        def observing(value, policies, routing):
            calls.append(value['id'])
            return generator(value, policies, routing)
        with patch('simulation.priority_experiment.lock_development_policies',
                   side_effect=AssertionError('must not retune')):
            payload = resume_validation(saved, d, v, [301], [5001], generator=observing, bootstrap_samples=100)
        self.assertEqual(calls, ['val'])
        self.assertEqual(saved, original)
        self.assertEqual(payload['tuning'], saved['tuning'])
        self.assertEqual(payload['suites']['development'], saved['suites']['development'])
        self.assertFalse(payload['development_reselected'])
        self.assertEqual(payload['branch_runs'], 13)

    def test_resume_rejects_modified_seed_sigma_or_scenario(self):
        saved, d, v = self.saved()
        for ds, vs, sigma in (([302], [5001], .35), ([301], [5002], .35), ([301], [5001], .4)):
            with self.subTest(ds=ds, vs=vs), self.assertRaises(ValueError):
                validate_development_input(saved, d, v, ds, vs, sigma)
        changed = copy.deepcopy(v)
        changed[0]['timeout_minutes'] += 1
        with self.assertRaises(ValueError):
            validate_development_input(saved, d, changed, [301], [5001], .35)

    def test_resume_rejects_tampered_coverage_config_outcomes_score_or_selection(self):
        saved, d, v = self.saved()
        variants = []
        for mutation in (
            lambda x: x.update(development_only=False),
            lambda x: x['suites']['development']['records'].pop(),
            lambda x: x['suites']['development']['records'].append(copy.deepcopy(x['suites']['development']['records'][0])),
            lambda x: x['suites']['development']['records'][0]['policy'].update(k=99),
            lambda x: x['suites']['development']['records'][0]['outcomes'][0].update(id='wrong'),
            lambda x: x['tuning']['priority']['scores'][0].update(priority4_not_treated=99),
            lambda x: x['tuning'].update(selected_priority_policy=copy.deepcopy(PRIORITY_POLICIES[1])),
            lambda x: x['suites']['validation']['records'].append({'seed': 5001}),
            lambda x: x['replay_cases'].append({'seed': 5001}),
        ):
            value = copy.deepcopy(saved)
            mutation(value)
            variants.append(value)
        for i, value in enumerate(variants):
            with self.subTest(variant=i), self.assertRaises(ValueError):
                validate_development_input(value, d, v, [301], [5001], .35)

    def test_replay_selection_is_predeclared_even_when_selected_policy_is_worse(self):
        locked = lock_development_policies(records())
        inputs, rows = [], []
        for family, nr in REPLAY_CONDITIONS:
            value = dict(scenario(family), case_kind=family, experiment_nonresponse=nr)
            inputs.append(value)
            for seed, start in ((5002, 1), (5001, 20)):
                for p in validation_policies(locked):
                    r = workload_record(value, branch(value, p, start=10 if p['id'] == LOCKED_V2['id'] else start))
                    r.update(scenario_id=family, seed=seed, case_kind=family, nonresponse_probability=nr)
                    rows.append(r)
        picks = select_replay_cases(rows, locked)
        self.assertEqual(len(picks), 3)
        self.assertEqual([p['seed'] for p in picks], [5001] * 3)
        self.assertEqual([p['difference'] for p in picks], [10] * 3)
        self.assertTrue(all(p['outcome_independent_selection'] for p in picks))
        exported = export_replay_cases(dict(replay_cases=picks, tuning=locked, service_lognormal_sigma=0), inputs)
        self.assertEqual(len(exported['scenarios']), 3)
        self.assertTrue(all(len(e['scenario']['policies']) == 5 for e in exported['scenarios']))
        self.assertTrue(all('유리한 검증 결과를 고른 예시가 아니다' in e['scenario']['description'] for e in exported['scenarios']))

    def test_invalid_input_rejected_before_engine(self):
        with patch('simulation.priority_experiment._run_conditions') as runner:
            with self.assertRaises(ValueError):
                run_experiment([scenario('same')], [scenario('same')], [301], [5001])
            runner.assert_not_called()

    def test_cli_rejects_old_seeds_collisions_and_overwrite_before_database(self):
        cases = [ ['--validation-seed-start', x] for x in ('1001', '2001', '3001', '4001') ]
        cases += [ ['--development-only', '--development-input', 'saved.json'],
            ['--development-input', 'simulation_results/priority_protection_corrected.json'],
            ['--replay-output', 'simulation_results/priority_protection_corrected.json'] ]
        for args in cases:
            with self.subTest(args=args), patch('simulation.priority_experiment.build_default_scenarios') as builder:
                with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    main(args)
                builder.assert_not_called()


if __name__ == '__main__':
    unittest.main()
