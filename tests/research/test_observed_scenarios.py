"""병원별 관측 정책 입력: 사전 생성, 치료 용량 분리, 신규 스트림 계약."""
import copy
import unittest

from simulation.map_replay import validate_scenario
from simulation.observed_scenarios import FAMILIES, build_observed_scenarios
from test_workload_experiment import scenario


class ObservedScenariosTest(unittest.TestCase):
    def test_inputs_are_immutable_independent_and_full_factorial(self):
        source = scenario()
        original = copy.deepcopy(source)
        development = build_observed_scenarios(source)
        validation = build_observed_scenarios(source, 'validation')
        self.assertEqual(source, original)
        self.assertEqual(len(development), 16)
        self.assertEqual(len(validation), 16)
        self.assertEqual({s['case_kind'] for s in development}, set(FAMILIES))
        self.assertEqual({s['experiment_nonresponse'] for s in validation}, {0, .2})
        self.assertFalse({s['id'] for s in development} & {s['id'] for s in validation})
        self.assertEqual({len(s['patients']) for s in development}, {12})
        self.assertEqual({len(s['patients']) for s in validation}, {16})
        development[0]['patients'][0]['resource_demands']['ed_bed'] = 99
        self.assertNotEqual(development[1]['patients'][0]['resource_demands']['ed_bed'], 99)
        self.assertNotEqual(validation[0]['patients'][0]['resource_demands']['ed_bed'], 99)

    def test_all_inputs_validate_without_network_or_database(self):
        for split in ('development', 'validation'):
            for value in build_observed_scenarios(scenario(), split):
                with self.subTest(split=split, case=value['id']):
                    validate_scenario(value)
                    self.assertFalse(value['experiment']['generation_selected_from_outcomes'])
                    self.assertFalse(value['experiment']['policy_has_true_queue_state'])
                    self.assertFalse(value['experiment']['future_information_available_to_policy'])

    def test_time_stream_is_long_enough_to_receive_observations(self):
        dev = {s['case_kind']: s for s in build_observed_scenarios(scenario(), nonresponse_levels=(0,))}
        val = {s['case_kind']: s for s in build_observed_scenarios(scenario(), 'validation', (0,))}
        self.assertEqual(dev['streaming_w1']['patients'][-1]['search_start_t'] -
                         dev['streaming_w1']['patients'][0]['search_start_t'], 55)
        self.assertEqual(val['streaming_w1']['patients'][-1]['search_start_t'] -
                         val['streaming_w1']['patients'][0]['search_start_t'], 90)
        self.assertLess(dev['burst_w1']['patients'][-1]['search_start_t'] -
                        dev['burst_w1']['patients'][0]['search_start_t'], 5)
        self.assertNotEqual(dev['streaming_w1']['timeout_minutes'], val['streaming_w1']['timeout_minutes'])
        self.assertNotEqual(dev['streaming_w1']['response_environment']['P01'],
                            val['streaming_w1']['response_environment']['P01'])

    def test_worker_controls_differ_only_in_worker_count_and_case_identity(self):
        inputs = {s['case_kind']: s for s in build_observed_scenarios(scenario(), 'validation', (0,))}
        for prefix in ('burst', 'streaming', 'shifting_background'):
            a, b = (copy.deepcopy(inputs[f'{prefix}_w{workers}']) for workers in (1, 2))
            for value in (a, b):
                for key in ('id', 'label', 'case_kind'):
                    value.pop(key)
                value['inquiry_queue'].pop('workers')
                value['experiment'].pop('case_kind')
            self.assertEqual(a, b)

    def test_nonshortage_capacity_and_scarce_conditions(self):
        inputs = build_observed_scenarios(scenario(), nonresponse_levels=(0,))
        for value in inputs:
            if value['case_kind'] != 'totalshortage':
                self.assertTrue(all(h['capacity']['ed_bed'] >= len(value['patients']) for h in value['hospitals']))
        scarce = next(s for s in inputs if s['case_kind'] == 'scarce_stream')
        self.assertEqual(len(scarce['experiment']['scarce_patient_ids']), 3)
        self.assertEqual(sum('가상 희소치료조건' in h['capabilities'] for h in scarce['hospitals']), 1)
        shortage = next(s for s in inputs if s['case_kind'] == 'totalshortage')
        self.assertEqual(sum(h['capacity']['ed_bed'] for h in shortage['hospitals']), 3)
        self.assertTrue(all(p['treatment_minutes'] > shortage['max_minutes'] for p in shortage['patients']))

    def test_background_changes_are_predeclared_and_do_not_follow_policy_output(self):
        inputs = {s['case_kind']: s for s in build_observed_scenarios(scenario(), nonresponse_levels=(0,))}
        fixed = inputs['streaming_w1']['inquiry_queue']['background_jobs']
        shifted = inputs['shifting_background_w1']['inquiry_queue']['background_jobs']
        self.assertGreater(len(shifted), len(fixed))
        self.assertEqual(shifted, sorted(shifted, key=lambda j: (j['t'], j['hospital'])))
        again = {s['case_kind']: s for s in build_observed_scenarios(scenario(), nonresponse_levels=(0,))}
        self.assertEqual(inputs, again)

    def test_invalid_inputs_fail(self):
        for levels in ((), (0, 0), (True,), (-.1,), (1.1,), (float('nan'),), (.00001, .00002)):
            with self.subTest(levels=levels), self.assertRaises(ValueError):
                build_observed_scenarios(scenario(), nonresponse_levels=levels)
        with self.assertRaises(ValueError):
            build_observed_scenarios(scenario(), split='test')


if __name__ == '__main__':
    unittest.main()
