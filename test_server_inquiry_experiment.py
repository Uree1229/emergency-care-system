"""실제 문의 워크플로를 사용하는 격리 실험의 비교 공정성과 분모 검증.

네트워크나 운영 DB 없이 작은 합성 입력으로 정책 선택의 결과를 확인한다.
"""
import copy
import json
import unittest
from collections import Counter

from server import inquiry_experiment as experiment
from server import inquiry_experiment_scenarios as scenarios


def scenario(hospital_count=3, patient_count=1, *, service_seconds=7,
             no_response=False, accept=True, workers=1):
    hospitals = [dict(id=f'H{i}', name=f'가상 병원 {i}', lat=35.16 + i * .01,
                      lon=126.85, capabilities=['응급의학'], workers=workers)
                 for i in range(1, hospital_count + 1)]
    patients = [dict(id=f'P{i}', at=0, lat=35.16, lon=126.85,
                     travel_seconds={h['id']: 120 + index * 10
                                     for index, h in enumerate(hospitals)})
                for i in range(patient_count)]
    return dict(id='unit-small', label='격리 실험 검증', horizon_seconds=300,
                hospitals=hospitals, patients=patients, history=[],
                potentials=[dict(patient_id=p['id'], hospital_id=h['id'],
                                 service_seconds=service_seconds, no_response=no_response,
                                 accept=accept)
                            for p in patients for h in hospitals])


def by_policy(result):
    return {branch['policy']: branch for branch in result['branches']}


def by_patient(branch):
    return {patient['id']: patient for patient in branch['patients']}


class PairedServerPolicies(unittest.TestCase):
    def test_real_workflow_selects_only_after_reply_and_adds_travel(self):
        result = experiment.run_scenario(scenario(1), policies=('fixed_1',))
        branch = result['branches'][0]
        patient = branch['patients'][0]
        self.assertEqual(patient['selection_seconds'], 7)
        self.assertEqual(patient['selected_at'], 7)
        self.assertEqual(patient['travel_seconds'], 120)
        self.assertEqual(patient['estimated_arrival_seconds'], 127)
        self.assertEqual(patient['contacted_hospitals'], ['H1'])
        self.assertEqual(patient['inquiry_count'], 1)
        self.assertFalse(patient['failed'])
        self.assertEqual(branch['metrics']['selected_count'], 1)
        self.assertEqual(branch['metrics']['failed_count'], 0)
        self.assertEqual(branch['metrics']['selection_seconds_mean'], 7)
        self.assertEqual(branch['metrics']['selection_wait_capped_mean'], 7)
        self.assertEqual(branch['metrics']['estimated_arrival_seconds_mean'], 127)
        confirms = [e for e in branch['trace'] if e['type'] == 'destination_confirmed']
        self.assertEqual([(e['at'], e['patient_id'], e['hospital_id']) for e in confirms],
                         [(7, 'P0', 'H1')])

    def test_silent_requests_are_failures_instead_of_zero_time_successes(self):
        result = experiment.run_scenario(scenario(no_response=True), policies=('fixed_3',))
        branch = result['branches'][0]
        patient = branch['patients'][0]
        self.assertTrue(patient['failed'])
        for key in ('selected_at', 'selection_seconds', 'travel_seconds',
                    'estimated_arrival_seconds'):
            self.assertIsNone(patient[key], key)
        self.assertEqual(patient['inquiry_count'], 3)
        self.assertEqual(branch['metrics']['patient_count'], 1)
        self.assertEqual(branch['metrics']['selected_count'], 0)
        self.assertEqual(branch['metrics']['failed_count'], 1)
        self.assertIsNone(branch['metrics']['selection_seconds_mean'])
        self.assertIsNone(branch['metrics']['estimated_arrival_seconds_mean'])
        self.assertEqual(branch['metrics']['selection_wait_capped_mean'], 300)
        self.assertTrue(any(e['type'] == 'reply_omitted' for e in branch['trace']))
        self.assertFalse(any(e['type'] == 'destination_confirmed' for e in branch['trace']))

    def test_all_declines_remain_unselected_and_keep_actual_inquiry_count(self):
        result = experiment.run_scenario(scenario(accept=False), policies=('fixed_1',))
        branch = result['branches'][0]
        self.assertTrue(branch['patients'][0]['failed'])
        self.assertIsNone(branch['patients'][0]['selection_seconds'])
        self.assertEqual(branch['patients'][0]['inquiry_count'], 3)
        self.assertEqual(branch['metrics']['failed_count'], 1)
        self.assertIsNone(branch['metrics']['selection_seconds_mean'])
        self.assertEqual(branch['metrics']['selection_wait_capped_mean'], 300)

    def test_each_branch_uses_identical_initial_snapshot_and_environment(self):
        source = scenario(5, 2)
        original = copy.deepcopy(source)
        # 기존 네 정책의 짝 비교 계약을 신규 공개 정책 수와 독립적으로 고정한다.
        result = experiment.run_scenario(source, policies=('fixed_1', 'fixed_3', 'fixed_5', 'observed_v2'))
        self.assertEqual(source, original)
        self.assertEqual(len(result['branches']), 4)
        initial = result['initial_snapshot_sha256']
        environment = result['environment_sha256']
        self.assertEqual(len(initial), 64)
        self.assertEqual(len(environment), 64)
        self.assertEqual({b['snapshot_sha256'] for b in result['branches']}, {initial})
        self.assertEqual({b['environment_sha256'] for b in result['branches']}, {environment})

    def test_policy_repeat_and_execution_order_do_not_change_branch_results(self):
        source = scenario(3, 2, service_seconds=13)
        policies = ('fixed_1', 'fixed_3', 'fixed_5', 'observed_v2')
        normal = by_policy(experiment.run_scenario(source, policies=policies))
        reverse = by_policy(experiment.run_scenario(source, policies=tuple(reversed(policies))))
        alone = by_policy(experiment.run_scenario(source, policies=('observed_v2',)))
        for name in policies:
            with self.subTest(policy=name):
                self.assertEqual(normal[name], reverse[name])
        self.assertEqual(normal['observed_v2'], alone['observed_v2'])

    def test_future_response_schedule_cannot_change_initial_observed_decision(self):
        fast = scenario(3, service_seconds=8)
        slow = copy.deepcopy(fast)
        slow['potentials'][0].update(service_seconds=240, no_response=True, accept=False)
        fast_branch = experiment.run_scenario(fast, policies=('observed_v2',))['branches'][0]
        slow_branch = experiment.run_scenario(slow, policies=('observed_v2',))['branches'][0]
        self.assertNotEqual(fast_branch['environment_sha256'], slow_branch['environment_sha256'])
        self.assertEqual(fast_branch['snapshot_sha256'], slow_branch['snapshot_sha256'])
        self.assertEqual(fast_branch['decisions'][0], slow_branch['decisions'][0])
        self.assertEqual(fast_branch['decisions'][0]['hospital_ids'], ['H1'])

    def test_shared_past_reply_history_is_available_to_observed_policy(self):
        source = scenario(2)
        source['history'] = [dict(patient_id=f'past-{hid}-{i}', hospital_id=hid,
                                  sent_at=-180, reply_at=reply, action='decline')
                             for hid, reply in (('H1', -20), ('H2', -179))
                             for i in range(2)]
        branches = by_policy(experiment.run_scenario(source, policies=('fixed_1', 'observed_v2')))
        self.assertEqual(branches['fixed_1']['decisions'][0]['hospital_ids'], ['H1'])
        self.assertEqual(branches['observed_v2']['decisions'][0]['hospital_ids'], ['H2'])
        self.assertEqual(branches['observed_v2']['decisions'][0]['evidence']['global_reply_samples'], 4)
        self.assertEqual(branches['observed_v2']['metrics']['patient_count'], 1)
        self.assertEqual(branches['observed_v2']['patients'][0]['travel_seconds'], 130)

    def test_two_workers_remove_queue_delay_without_changing_review_demand(self):
        single = scenario(1, 2, service_seconds=20, workers=1)
        double = copy.deepcopy(single)
        double['hospitals'][0]['workers'] = 2
        a = experiment.run_scenario(single, policies=('fixed_1',))['branches'][0]
        b = experiment.run_scenario(double, policies=('fixed_1',))['branches'][0]
        self.assertEqual([p['selection_seconds'] for p in a['patients']], [20, 40])
        self.assertEqual([p['selection_seconds'] for p in b['patients']], [20, 20])
        self.assertEqual(a['metrics']['review_work_seconds'], 40)
        self.assertEqual(b['metrics']['review_work_seconds'], 40)
        self.assertEqual(a['metrics']['inquiries_per_patient'], 1)
        self.assertEqual(b['metrics']['inquiries_per_patient'], 1)
        self.assertEqual(a['metrics']['failed_count'], b['metrics']['failed_count'])

    def test_event_skipping_matches_one_second_polling(self):
        source = scenario(3, 2, service_seconds=17)
        source['patients'][1]['at'] = 9
        for potential in source['potentials']:
            if potential['hospital_id'] == 'H1':
                potential['no_response'] = True
        skipped = experiment.run_scenario(source)
        polled = experiment.run_scenario(source, tick_seconds=1)
        self.assertEqual(skipped, polled)

    def test_contact_limit_exhaustion_does_not_hide_late_valid_reply_within_window(self):
        source = scenario(10, service_seconds=1)
        for potential in source['potentials']:
            if int(potential['hospital_id'][1:]) <= 5:
                potential['accept'] = False
            else:
                potential['service_seconds'] = 200
        branch = experiment.run_scenario(source, policies=('fixed_5',))['branches'][0]
        patient = branch['patients'][0]
        self.assertEqual(patient['inquiry_count'], 10)
        self.assertFalse(patient['failed'])
        self.assertEqual(patient['selection_seconds'], 201)
        self.assertEqual(patient['destination_hospital_id'], 'H6')
        self.assertEqual(branch['metrics']['selection_seconds_mean'], 201)

    def test_acceptance_at_three_hundred_seconds_counts_but_later_reply_does_not(self):
        within = experiment.run_scenario(scenario(1, service_seconds=300),
                                         policies=('fixed_1',))['branches'][0]
        outside = experiment.run_scenario(scenario(1, service_seconds=301),
                                          policies=('fixed_1',))['branches'][0]
        self.assertFalse(within['patients'][0]['failed'])
        self.assertEqual(within['patients'][0]['selection_seconds'], 300)
        self.assertTrue(outside['patients'][0]['failed'])
        self.assertIsNone(outside['patients'][0]['selection_seconds'])
        self.assertEqual(outside['metrics']['review_work_seconds'], 301)
        self.assertTrue(any(e['type'] == 'reply_suppressed_after_cancel' and e['at'] == 301
                            for e in outside['trace']))

    def test_other_hospital_confirmation_cancels_queued_but_not_active_review(self):
        source = scenario(2, 2, service_seconds=100)
        for potential in source['potentials']:
            if potential['hospital_id'] == 'H2':
                potential['service_seconds'] = 5
        branch = experiment.run_scenario(source, policies=('fixed_3',))['branches'][0]
        patients = by_patient(branch)
        self.assertEqual(patients['P0']['selection_seconds'], 5)
        self.assertEqual(patients['P1']['selection_seconds'], 10)
        self.assertEqual(patients['P0']['inquiry_count'], 2)
        self.assertEqual(patients['P1']['inquiry_count'], 2)
        trace = branch['trace']
        self.assertTrue(any(e['type'] == 'review_cancelled_before_start' and
                            e['patient_id'] == 'P1' and e['hospital_id'] == 'H1'
                            for e in trace))
        self.assertFalse(any(e['type'] == 'review_started' and
                             e['patient_id'] == 'P1' and e['hospital_id'] == 'H1'
                             for e in trace))
        self.assertTrue(any(e['type'] == 'reply_suppressed_after_cancel' and
                            e['patient_id'] == 'P0' and e['hospital_id'] == 'H1'
                            for e in trace))
        self.assertEqual(branch['metrics']['review_work_seconds'], 110)
        self.assertEqual(sum(e['type'] == 'destination_confirmed' for e in trace), 2)


class PairedSummary(unittest.TestCase):
    def test_time_comparison_uses_only_matching_selected_patient_ids(self):
        def patient(pid, selected, arrival, contacts):
            return dict(id=pid, selection_seconds=selected,
                        estimated_arrival_seconds=arrival, inquiry_count=contacts,
                        failed=selected is None)
        baseline = dict(policy='fixed_3', patients=[patient('A', 20, 200, 3),
                         patient('B', 10, 130, 3), patient('C', None, None, 3)],
                        metrics={'inquiries_per_patient': 3})
        selected = dict(policy='observed_v2', patients=[patient('A', 8, 220, 1),
                         patient('B', None, None, 1), patient('C', 6, 160, 1)],
                        metrics={'inquiries_per_patient': 1})
        comparison = experiment.paired_summary(selected, baseline)
        self.assertEqual(comparison['common_patient_count'], 1)
        self.assertEqual(comparison['selected_only_policy_count'], 1)
        self.assertEqual(comparison['selected_only_baseline_count'], 1)
        self.assertEqual(comparison['common_selection_difference_seconds_mean'], -12)
        self.assertEqual(comparison['common_arrival_difference_seconds_mean'], 20)
        self.assertEqual(comparison['inquiries_difference_per_patient'], -2)

    def test_no_common_selected_patient_has_no_mean_instead_of_zero(self):
        baseline = dict(policy='fixed_1', patients=[dict(id='A', failed=True,
                        selection_seconds=None, estimated_arrival_seconds=None, inquiry_count=1)],
                        metrics={'inquiries_per_patient': 1})
        selected = copy.deepcopy(baseline)
        selected['policy'] = 'observed_v2'
        comparison = experiment.paired_summary(selected, baseline)
        self.assertEqual(comparison['common_patient_count'], 0)
        self.assertIsNone(comparison['common_selection_difference_seconds_mean'])
        self.assertIsNone(comparison['common_arrival_difference_seconds_mean'])

    def test_aggregate_weights_patients_and_pairs_each_scenario_separately(self):
        def patient(pid, selected, arrival, contacts):
            return dict(id=pid, selection_seconds=selected,
                        estimated_arrival_seconds=arrival, inquiry_count=contacts,
                        failed=selected is None)

        def branch(policy, patients, work):
            return dict(policy=policy, patients=patients,
                        metrics={'review_work_seconds': work,
                                 'review_started_count': sum(p['inquiry_count'] for p in patients)})

        results = [dict(scenario={'family': 'many', 'id': 'many-1'}, branches=[
            branch('fixed_3', [patient('A', 10, 100, 3), patient('B', 20, 200, 3),
                               patient('C', None, None, 3), patient('D', None, None, 3)], 90),
            branch('observed_v2', [patient('A', 6, 110, 1), patient('B', None, None, 2),
                                   patient('C', 30, 300, 2), patient('D', None, None, 2)], 40)]),
            dict(scenario={'family': 'single', 'id': 'single-2'}, branches=[
                branch('fixed_3', [patient('A', 100, 400, 3)], 20),
                branch('observed_v2', [patient('A', 50, 500, 1)], 10)])]
        original = copy.deepcopy(results)
        rows = experiment.aggregate(results)
        self.assertEqual(results, original)
        keyed = {(row['family'], row['policy']): row for row in rows}
        base = keyed['ALL', 'fixed_3']
        ours = keyed['ALL', 'observed_v2']
        self.assertEqual((ours['patient_count'], ours['selected_count'], ours['failed_count']),
                         (5, 3, 2))
        self.assertEqual(ours['scenario_count'], 2)
        self.assertAlmostEqual(base['selection_seconds_mean'], 130 / 3)
        self.assertAlmostEqual(ours['selection_seconds_mean'], 86 / 3)
        self.assertEqual(base['selection_wait_capped_mean'], 146)
        self.assertEqual(ours['selection_wait_capped_mean'], 137.2)
        self.assertEqual(base['inquiries_per_patient'], 3)
        self.assertEqual(ours['inquiries_per_patient'], 1.6)
        self.assertEqual((base['review_work_seconds'], ours['review_work_seconds']), (110, 50))
        self.assertEqual((base['review_work_seconds_per_patient'], ours['review_work_seconds_per_patient']),
                         (22, 10))
        self.assertEqual((base['review_started_per_patient'], ours['review_started_per_patient']),
                         (3, 1.6))
        self.assertEqual(ours['common_patient_count_vs_fixed3'], 2)
        self.assertEqual(ours['common_failed_vs_fixed3'], 1)
        self.assertEqual(ours['selected_only_policy_vs_fixed3'], 1)
        self.assertEqual(ours['selected_only_fixed3'], 1)
        self.assertEqual(ours['common_selection_difference_vs_fixed3'], -27)
        self.assertEqual(ours['common_arrival_difference_vs_fixed3'], 55)
        self.assertEqual(keyed['many', 'observed_v2']['patient_count'], 4)
        self.assertEqual(keyed['single', 'observed_v2']['patient_count'], 1)


class ScenarioGeneration(unittest.TestCase):
    def test_eight_families_have_complete_stable_order_independent_inputs(self):
        seeds = (7, 23)
        first = scenarios.make_suite(seeds=seeds)
        repeated = scenarios.make_suite(seeds=seeds)
        reversed_seeds = scenarios.make_suite(seeds=tuple(reversed(seeds)))
        self.assertEqual(len(scenarios.FAMILY_NAMES), 8)
        self.assertEqual(len(first), 16)
        self.assertEqual(Counter(item['family'] for item in first),
                         Counter({family: 2 for family in scenarios.FAMILY_NAMES}))
        self.assertEqual(len({item['id'] for item in first}), 16)
        stable_json = lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True,
                                               separators=(',', ':'))
        self.assertEqual(stable_json(first), stable_json(repeated))
        self.assertEqual({item['id']: item for item in first},
                         {item['id']: item for item in reversed_seeds})
        self.assertEqual(json.loads(stable_json(first)), first)
        for item in first:
            with self.subTest(scenario=item['id']):
                experiment.validate(item)
                expected = {(p['id'], h['id']) for p in item['patients'] for h in item['hospitals']}
                actual = [(row['patient_id'], row['hospital_id']) for row in item['potentials']]
                self.assertEqual(set(actual), expected)
                self.assertEqual(len(actual), len(expected))

    def test_empty_duplicate_and_invalid_seeds_are_rejected(self):
        for seeds in ((), [], (1, 1), (-1,), (True,), ('1',), '12', None):
            with self.subTest(seeds=seeds):
                with self.assertRaises(ValueError):
                    scenarios.make_suite(seeds=seeds)


if __name__ == '__main__':
    unittest.main()
