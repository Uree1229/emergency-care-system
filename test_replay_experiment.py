"""지도 실험 집계·짝지은 비교·잠재 응답의 재현성. 네트워크 호출 없음."""
import copy
import json
import unittest

from simulation.replay_experiment import (
    INQUIRY_POLICIES, branch_record, bootstrap_mean_interval, paired_summary,
    report_markdown, run_batch, seeded_scenario, select_replay_cases,
    shared_patient_time_difference, summarize,
)


def scenario():
    return dict(id='case', label='합성 사례', seed=0, max_minutes=60,
        hospitals=[dict(id='H1', name='가상 병원', lat=37.11, lon=127.0,
                        capabilities=['basic'], capacity={'ed_bed': 2})],
        patients=[dict(id='P1', location=[37.10, 127.0], occur_t=0, dispatch_t=1,
                       ambulance_origin=[37.09, 127.0], ambulance_arrive_t=3, search_start_t=5,
                       required_capabilities=['basic'], resource_demands={'ed_bed': 1},
                       priority=4, treatment_minutes=10),
                  dict(id='P2', location=[37.10, 127.0], occur_t=1, dispatch_t=2,
                       ambulance_origin=[37.09, 127.0], ambulance_arrive_t=4, search_start_t=6,
                       required_capabilities=['basic'], resource_demands={'ed_bed': 1},
                       priority=1, treatment_minutes=10)],
        response_environment={'*': {'H1': dict(delay=2, silent=True)}}, exogenous_events=[])


def branch(policy_id='R0', arrival=12, second_arrival=None):
    return dict(id=policy_id, label=policy_id, policy=dict(rule=policy_id, k=3, wait=2),
        metrics=dict(joint_fallbacks=0), trajectories=[{'geometry': ['must_not_be_copied']}],
        patient_outcomes=[dict(id='P1', state='COMPLETE', first_commit_t=7,
            final_arrive_t=arrival, treatment_start_t=arrival,
            treatment_complete_t=arrival + 10, inquiries=3, redirects=0, transfers=0),
            dict(id='P2', state='UNASSIGNED' if second_arrival is None else 'COMPLETE',
                 first_commit_t=None if second_arrival is None else 8,
                 final_arrive_t=second_arrival, treatment_start_t=second_arrival,
                 treatment_complete_t=None if second_arrival is None else second_arrival + 10,
                 inquiries=4, redirects=0, transfers=0)])


class ReplayExperimentTest(unittest.TestCase):
    def test_seeded_environment_is_repeatable_and_does_not_change_source(self):
        source = scenario()
        before = copy.deepcopy(source)
        first = seeded_scenario(source, 9)
        self.assertEqual(first, seeded_scenario(source, 9))
        self.assertNotEqual(first['response_environment'], seeded_scenario(source, 10)['response_environment'])
        self.assertEqual(source, before)
        self.assertTrue(first['experiment_environment']['base_fixed_silence_replaced'])
        json.dumps(first, allow_nan=False)

    def test_nonresponse_extremes_and_attempt_specific_delays(self):
        silent = seeded_scenario(scenario(), 3, nonresponse=1)
        replied = seeded_scenario(scenario(), 3, nonresponse=0)
        self.assertTrue(all(a['silent'] for a in silent['response_environment']['P1']['H1']['attempts']))
        self.assertTrue(all(not a['silent'] for a in replied['response_environment']['P1']['H1']['attempts']))
        delays = [a['delay'] for a in replied['response_environment']['P1']['H1']['attempts']]
        self.assertGreater(len(set(delays)), 1)
        self.assertGreaterEqual(min(delays), 1)

    def test_invalid_distribution_is_rejected(self):
        for probability in (-1, 2, float('nan')):
            with self.assertRaises(ValueError):
                seeded_scenario(scenario(), 1, nonresponse=probability)
        with self.assertRaises(ValueError):
            seeded_scenario(scenario(), 1, sigma=-1)

    def test_record_uses_search_relative_times_and_explicit_coverage(self):
        data = branch_record(scenario(), branch())
        metrics = data['metrics']
        self.assertEqual(metrics['patients'], 2)
        self.assertEqual(metrics['unassigned'], 1)
        self.assertEqual(metrics['high_priority_unassigned'], 0)
        self.assertEqual(metrics['completed'], 1)
        self.assertEqual(metrics['completion_coverage'], .5)
        self.assertEqual(metrics['arrival_observed'], 1)
        self.assertEqual(metrics['arrival_mean_minutes'], 7)
        self.assertEqual(metrics['inquiries_per_patient'], 3.5)
        self.assertNotIn('trajectories', data)
        self.assertEqual(data['priority']['4']['start_max_minutes'], 7)

    def test_empty_conditional_time_is_null_and_unassigned_is_counted(self):
        value = branch()
        for outcome in value['patient_outcomes']:
            outcome.update(state='UNASSIGNED', first_commit_t=None, final_arrive_t=None,
                           treatment_start_t=None, treatment_complete_t=None)
        metrics = branch_record(scenario(), value)['metrics']
        self.assertIsNone(metrics['commit_mean_minutes'])
        self.assertIsNone(metrics['arrival_mean_minutes'])
        self.assertEqual(metrics['high_priority_unassigned'], 1)
        self.assertEqual(metrics['unassigned'], 2)

    def test_paired_seed_matching_and_regression_direction(self):
        before = [dict(seed=1, metrics={'x': 10}), dict(seed=2, metrics={'x': 20})]
        after = [dict(seed=2, metrics={'x': 22}), dict(seed=1, metrics={'x': 6})]
        result = paired_summary(before, after, 'x', samples=100)
        self.assertEqual(result['mean_difference'], -1)
        self.assertEqual(result['regression_fraction'], .5)
        self.assertEqual(result['worst_seed'], 2)
        self.assertEqual(result['worst_difference'], 2)
        result = paired_summary(before, after, 'x', lower=False, samples=100)
        self.assertEqual(result['worst_seed'], 1)

    def test_missing_times_are_not_reported_as_zero(self):
        result = paired_summary([dict(seed=1, metrics={'x': None})],
                                [dict(seed=1, metrics={'x': 2})], 'x')
        self.assertIsNone(result['mean_difference'])
        self.assertEqual(result['paired_seed_count'], 0)
        self.assertIsNone(result['regression_fraction'])
        self.assertIsNone(bootstrap_mean_interval([1], 'x'))

    def test_shared_patient_comparison_excludes_different_coverage(self):
        before = branch_record(scenario(), branch(arrival=12))
        after = branch_record(scenario(), branch('R1', arrival=10, second_arrival=50))
        result = shared_patient_time_difference(before, after, 'final_arrive_t')
        self.assertEqual(result['mean_difference'], -2)
        self.assertEqual(result['common_patients'], 1)
        self.assertEqual(result['baseline_patients'], 2)

    def test_batch_calls_one_common_generator_per_seed_and_all_policies(self):
        calls = []
        policies = [dict(id='R0', label='R0'), dict(id='R1', label='R1')]
        def fake_generator(value, policies, routing):
            calls.append(copy.deepcopy(value))
            return dict(scenario=value, branches=[branch('R0'), branch('R1', arrival=10)])
        result = run_batch([scenario()], policies, [1, 2], generator=fake_generator, bootstrap_samples=100)
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(result['records']), 4)
        self.assertTrue(all('trajectories' not in record for record in result['records']))
        first = result['summaries'][0]['policies'][1]
        self.assertEqual(first['shared_patient_times']['final_arrive_t']['mean_difference'], -2)
        self.assertEqual(first['denominators']['arrival_observed'], 2)
        self.assertEqual(first['denominators']['patients'], 4)
        self.assertEqual(calls[0]['response_environment'], seeded_scenario(scenario(), 1)['response_environment'])

    def test_replay_selection_does_not_pick_incomparable_coverage(self):
        rows = []
        for seed, b, c in ((1, branch(arrival=12), branch('R1', arrival=10)),
                           (2, branch(arrival=12), branch('R1', arrival=15)),
                           (3, branch(arrival=12), branch('R1', arrival=1, second_arrival=2))):
            for value in (b, c):
                record = branch_record(scenario(), value)
                record.update(seed=seed, scenario_id='case', scenario_label='사례')
                rows.append(record)
        picks = select_replay_cases(rows)
        self.assertEqual([pick['seed'] for pick in picks], [1, 2])
        self.assertEqual([pick['difference'] for pick in picks], [-2, 3])

    def test_inquiry_grid_keeps_rule_constant_and_uses_common_k3w2_baseline(self):
        self.assertEqual(len(INQUIRY_POLICIES), 6)
        self.assertTrue(all(policy['rule'] == 'R1' for policy in INQUIRY_POLICIES))
        self.assertEqual((INQUIRY_POLICIES[0]['k'], INQUIRY_POLICIES[0]['wait']), (3, 2))

    def test_report_includes_limitations_and_denominators(self):
        policies = [dict(id='R0', label='R0'), dict(id='R1', label='R1')]
        def fake_generator(value, policies, routing):
            return dict(scenario=value, branches=[branch('R0'), branch('R1', arrival=10)])
        suite = run_batch([scenario()], policies, [1, 2], generator=fake_generator, bootstrap_samples=100)
        text = report_markdown(dict(seeds=[1, 2], nonresponse=.2, routing={'provider': 'synthetic'},
                                   suites={'assignment_rules': suite}, limitations=['합성 입력이다.'], replay_cases=[]))
        self.assertIn('합성 입력이다.', text)
        self.assertIn('2/4', text)
        self.assertIn('최적성 증명이 아니다', text)

    def test_real_common_engine_runs_with_synthetic_routes_and_no_network(self):
        policies = [dict(id='R0', label='R0', rule='R0', k=1, wait=0, notify=True),
                    dict(id='R1', label='R1', rule='R1', k=1, wait=0, notify=True)]
        result = run_batch([scenario()], policies, [1, 2], nonresponse=0, bootstrap_samples=100)
        self.assertEqual(len(result['records']), 4)
        self.assertTrue(all(row['metrics']['patients'] == 2 for row in result['records']))
        json.dumps(result, allow_nan=False)


if __name__ == '__main__':
    unittest.main()
