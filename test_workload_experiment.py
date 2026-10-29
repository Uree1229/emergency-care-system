"""문의 부하 검증의 정책 고정·분모·대조 설계. 외부 네트워크를 사용하지 않는다."""
import copy
import json
import unittest

from simulation.workload_experiment import (
    BASELINE, CHANGE_POLICIES, TUNING_POLICIES, export_replay_cases,
    report_markdown, run_experiment, run_suite, score_policy,
    seed_workload_scenario, select_development_policy, select_validation_replays,
    validation_policies, workload_record,
)


def scenario(case_id='tiny', family='inquiry_workload'):
    return dict(id=case_id, label='작은 합성 입력', family=family, case_kind='tiny',
        max_minutes=35, timeout_minutes=8, retry_minutes=3,
        hospitals=[dict(id=f'H{i}', name=f'합성 병원 {i}', lat=37.10 + i * .01, lon=127.0,
                        capabilities=['basic'], capacity={'ed_bed': 3}) for i in range(1, 5)],
        patients=[dict(id=f'P{i}', location=[37.1, 127.0], occur_t=i-1, dispatch_t=i,
                       ambulance_origin=[37.09, 127.0], ambulance_arrive_t=i+2, search_start_t=i+4,
                       required_capabilities=['basic'], resource_demands={'ed_bed': 1},
                       priority=4 if i == 1 else 1, treatment_minutes=3) for i in range(1, 4)],
        response_environment={'*': {f'H{i}': dict(delay=i, silent=False) for i in range(1, 5)}},
        inquiry_queue=dict(enabled=family == 'inquiry_workload', workers=1,
                           background_jobs=[dict(t=4, hospital='H1', service_minutes=3)]),
        exogenous_events=[])


def branch(value, policy, start=12, missed=0):
    outcomes = []
    for index, p in enumerate(value['patients']):
        absent = index < missed
        outcomes.append(dict(id=p['id'], state='UNASSIGNED' if absent else 'COMPLETE',
            first_commit_t=None if absent else p['search_start_t']+1,
            final_arrive_t=None if absent else p['search_start_t']+start,
            treatment_start_t=None if absent else p['search_start_t']+start,
            treatment_complete_t=None if absent else p['search_start_t']+start+3,
            inquiries=policy.get('k', 3), redirects=0, transfers=0))
    queue = dict(end_t=35, aggregate=dict(submitted=10, completed=8, waiting=1, processing=1,
                                        queue_wait_total_minutes=12, service_total_minutes=20),
                 by_hospital=[]) if value.get('inquiry_queue', {}).get('enabled') else None
    return dict(id=policy['id'], label=policy['label'], policy=policy, patient_outcomes=outcomes,
                metrics=dict(inquiry_queue_wait_mean=2 if queue else None,
                             inquiry_peak_waiting=4 if queue else None,
                             resource_invalid_transit_minutes=0, notifications=0),
                inquiry_queue_report=queue,
                trajectories=[dict(geometry=['must_not_export'])])


def tuning_record(policy_id, start=10, inquiries=3, missed=0):
    return dict(policy_id=policy_id, split='development', metrics=dict(
        patients=3, start_observed=3-missed, not_treated=missed, not_arrived=missed,
        start_mean_minutes=start, inquiries=inquiries*3))


class WorkloadExperimentTest(unittest.TestCase):
    def test_service_environment_repeatable_preserves_fixed_silence_and_input(self):
        value = scenario()
        value['response_environment']['*']['H1']['silent'] = True
        original = copy.deepcopy(value)
        seeded = seed_workload_scenario(value, 17, nonresponse=0, sigma=0)
        self.assertEqual(seeded, seed_workload_scenario(value, 17, nonresponse=0, sigma=0))
        self.assertEqual(value, original)
        self.assertTrue(all(a['silent'] for a in seeded['response_environment']['P1']['H1']['attempts']))
        self.assertTrue(all(not a['silent'] for a in seeded['response_environment']['P1']['H2']['attempts']))
        self.assertEqual({a['delay'] for a in seeded['response_environment']['P1']['H2']['attempts']}, {2})
        self.assertFalse(seeded['experiment_environment']['base_fixed_silence_replaced'])

    def test_coverage_precedes_smaller_conditional_time(self):
        records = [tuning_record('full', start=100), tuning_record('missing', start=1, missed=1)]
        choices = [dict(id='full'), dict(id='missing')]
        selected = select_development_policy(records, choices)
        self.assertEqual(selected['selected_policy']['id'], 'full')
        self.assertFalse(selected['validation_retuning'])
        self.assertEqual(selected['inquiry_penalty_minutes'], .25)

    def test_fixed_cost_and_baseline_tie_break(self):
        rows = [tuning_record(p['id'], start=10, inquiries=4) for p in TUNING_POLICIES]
        selected = select_development_policy(rows)
        self.assertEqual(selected['selected_policy']['id'], BASELINE['id'])
        self.assertEqual(score_policy(rows[:1])['combined_minutes'], 11)

    def test_validation_records_cannot_be_used_to_tune(self):
        record = tuning_record(BASELINE['id'])
        record['split'] = 'validation'
        with self.assertRaises(ValueError):
            select_development_policy([record], [BASELINE])

    def test_missing_development_cases_cannot_gain_a_selection_advantage(self):
        rows = [dict(tuning_record('a'), scenario_id='case', seed=1),
                dict(tuning_record('a'), scenario_id='case', seed=2),
                dict(tuning_record('b'), scenario_id='case', seed=1)]
        with self.assertRaises(ValueError):
            select_development_policy(rows, [dict(id='a'), dict(id='b')])

    def test_zero_coverage_is_null_not_zero(self):
        score = score_policy([tuning_record('none', start=None, missed=3)])
        self.assertIsNone(score['combined_minutes'])
        self.assertIsNone(score['conditional_start_mean'])
        json.dumps(score, allow_nan=False)

    def test_validation_only_keeps_locked_candidate_and_reference_controls(self):
        chosen = TUNING_POLICIES[1]
        policies = validation_policies(chosen)
        self.assertEqual([p['id'] for p in policies], ['load_travel', 'load_w2', 'load_k1', 'load_k5'])
        self.assertNotIn('load_w10', [p['id'] for p in policies])
        self.assertEqual(len(validation_policies(BASELINE)), 3)

    def test_workload_record_drops_geometry_and_keeps_queue_denominator(self):
        value = scenario()
        result = workload_record(value, branch(value, BASELINE))
        self.assertNotIn('trajectories', result)
        self.assertEqual(result['metrics']['queue_wait_mean'], 2)
        self.assertEqual(result['metrics']['queue_submitted'], 10)
        self.assertEqual(result['metrics']['queue_open'], 2)
        self.assertEqual(result['inquiry_queue_report']['aggregate']['completed'], 8)

    def test_queue_configuration_without_engine_support_fails(self):
        value = scenario()
        result = branch(value, BASELINE)
        result.pop('inquiry_queue_report')
        with self.assertRaises(RuntimeError):
            workload_record(value, result)

    def test_seed_overlap_is_rejected_before_generation(self):
        with self.assertRaises(ValueError):
            run_experiment([scenario()], [scenario()], [1, 2], [2, 3])

    def test_end_to_end_freezes_development_choice_and_isolates_queue_ablation(self):
        calls = []
        dev = [scenario('dev')]
        val = [scenario('val'), scenario('change', 'condition_change')]
        original = copy.deepcopy(val)
        def generator(value, policies, routing):
            calls.append((value['id'], value['seed'], bool(value['inquiry_queue']['enabled']),
                          [p['id'] for p in policies]))
            # w2 wins only development; held-out data could favor an unselected w10.
            branches = [branch(value, p, start=5 if p['id'] == 'load_w2' else 10)
                        for p in policies]
            return dict(scenario=value, branches=branches)
        payload = run_experiment(dev, val, [1, 2], [1001, 1002], generator=generator,
                                 bootstrap_samples=100)
        self.assertEqual(payload['tuning']['selected_policy']['id'], 'load_w2')
        self.assertEqual(calls[:2], [('dev', 1, True, [p['id'] for p in TUNING_POLICIES]),
                                    ('dev', 2, True, [p['id'] for p in TUNING_POLICIES])])
        validation_calls = [c for c in calls if c[1] >= 1001]
        self.assertTrue(all('load_w10' not in c[3] for c in validation_calls))
        off = [c for c in validation_calls if c[0] == 'val' and not c[2]]
        self.assertEqual(len(off), 2)
        self.assertEqual(val, original)
        self.assertEqual(payload['branch_runs'], 24)
        self.assertFalse(payload['actual_system_comparison'])
        text = report_markdown(payload)
        self.assertIn('검증에서 재선택하지 않았다', text)
        self.assertIn('실제 개선율이 아니다', text)
        self.assertIn('개선·악화', text)
        self.assertIn('6/6', text)
        exported = export_replay_cases(payload, val)
        self.assertFalse(exported['geometry_included'])
        self.assertTrue(all(item['scenario']['experiment_selection']['selection'].startswith('validation_')
                            for item in exported['scenarios']))
        json.dumps(payload, allow_nan=False)

    def test_equal_coverage_replay_selection_keeps_regression(self):
        value = scenario()
        rows = []
        for seed, delta in ((1001, -3), (1002, 4), (1003, -8)):
            for policy, start, missed in ((BASELINE, 12, 0), (TUNING_POLICIES[1], 12+delta, int(seed == 1003))):
                record = workload_record(value, branch(value, policy, start=start, missed=missed))
                record.update(scenario_id='tiny', seed=seed)
                rows.append(record)
        picked = select_validation_replays(rows, [], 'load_w2')
        self.assertEqual([p['seed'] for p in picked], [1001, 1002])
        self.assertEqual([p['difference'] for p in picked], [-3, 4])
        self.assertEqual(picked[0]['metric'], 'treatment_start_t')

    def test_replay_selection_does_not_call_an_all_regression_result_improvement(self):
        value = scenario()
        rows = []
        for policy, start in ((BASELINE, 10), (TUNING_POLICIES[1], 12)):
            record = workload_record(value, branch(value, policy, start=start))
            record.update(scenario_id='tiny', seed=1001)
            rows.append(record)
        picked = select_validation_replays(rows, [], 'load_w2')
        self.assertEqual([p['selection'] for p in picked], ['validation_worst_observed'])

    def test_fixed_inquiry_controls_use_first_seed_even_when_later_seed_is_better(self):
        rows, specs = [], []
        for kind in ('work_sufficient_w1', 'work_sufficient_w2'):
            value = scenario(f'{kind}_validation')
            value['case_kind'] = kind
            specs.append(value)
            for seed, k1_start in ((1002, 1), (1001, 16)):
                for policy, start in ((BASELINE, 10), (dict(BASELINE, id='load_k1', k=1), k1_start),
                                      (dict(BASELINE, id='load_k5', k=5), 9)):
                    record = workload_record(value, branch(value, policy, start=start))
                    record.update(scenario_id=value['id'], case_kind=kind, seed=seed)
                    rows.append(record)
        picked = select_validation_replays(rows, [], BASELINE['id'])
        self.assertEqual(len(picked), 2)
        self.assertTrue(all(p['selection'] == 'validation_fixed_inquiry_control' for p in picked))
        self.assertEqual([p['seed'] for p in picked], [1001, 1001])
        self.assertEqual([p['difference'] for p in picked], [6, 6])
        self.assertTrue(all(p['outcome_independent_selection'] for p in picked))
        exported = export_replay_cases(dict(replay_cases=picked, nonresponse=0,
            service_lognormal_sigma=0, tuning=dict(selected_policy=BASELINE)), specs)
        for item in exported['scenarios']:
            value = item['scenario']
            self.assertEqual([p['id'] for p in value['policies']], ['load_travel', 'load_k1', 'load_k5'])
            self.assertIn('문의규모 고정 대조 · 첫 검증 seed', value['label'])
            self.assertIn('사후에 더 나은 정책이나 seed를 고른 예시가 아니다', value['description'])

    def test_common_real_engine_queue_is_executed_without_network(self):
        value = scenario()
        result = run_suite([value], [BASELINE, TUNING_POLICIES[1]], [3],
                           'development', 'tiny', sigma=0, bootstrap_samples=100)
        self.assertEqual(len(result['records']), 2)
        self.assertTrue(all(r['queue_enabled'] for r in result['records']))
        self.assertTrue(all(r['metrics']['queue_submitted'] > 0 for r in result['records']))
        self.assertGreater(result['records'][0]['metrics']['queue_wait_mean'], 0)


if __name__ == '__main__':
    unittest.main()
