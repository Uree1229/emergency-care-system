"""우선순위 보호 실험 입력의 대조·비유출·독립 복사 계약."""
import copy
import unittest

from simulation.map_replay import validate_scenario
from simulation.priority_scenarios import FAMILIES, build_priority_scenarios
from test_workload_experiment import scenario


class PriorityScenariosTest(unittest.TestCase):
    def cases(self, split='development'):
        return {value['case_kind']: value for value in
                build_priority_scenarios(scenario(), split, (0,))}

    def test_factorial_split_ids_and_source_independence(self):
        source = scenario()
        original = copy.deepcopy(source)
        development = build_priority_scenarios(source)
        validation = build_priority_scenarios(source, 'validation')
        self.assertEqual(source, original)
        self.assertEqual(len(development), 16)
        self.assertEqual(len(validation), 16)
        self.assertEqual({value['case_kind'] for value in development}, set(FAMILIES))
        self.assertEqual({value['experiment_nonresponse'] for value in validation}, {0, .2})
        self.assertFalse({value['id'] for value in development} & {value['id'] for value in validation})
        self.assertEqual({len(value['patients']) for value in development}, {12})
        self.assertEqual({len(value['patients']) for value in validation}, {16})
        self.assertNotEqual(development[0]['timeout_minutes'], validation[0]['timeout_minutes'])
        self.assertNotEqual(development[0]['response_environment']['P01'],
                            validation[0]['response_environment']['P01'])
        development[0]['patients'][0]['location'][0] += 1
        development[0]['patients'][0]['resource_demands']['ed_bed'] = 99
        development[0]['inquiry_queue']['background_jobs'][0]['service_minutes'] = 99
        development[0]['response_environment']['P01']['H1']['delay'] = 99
        for value in (development[1], validation[0]):
            self.assertNotEqual(development[0]['patients'][0]['location'], value['patients'][0]['location'])
            self.assertNotEqual(value['patients'][0]['resource_demands']['ed_bed'], 99)
            self.assertNotEqual(value['inquiry_queue']['background_jobs'][0]['service_minutes'], 99)
            self.assertNotEqual(value['response_environment']['P01']['H1']['delay'], 99)

    def test_all_inputs_validate_and_explicitly_disallow_future_information(self):
        for split in ('development', 'validation'):
            for value in build_priority_scenarios(scenario(), split):
                with self.subTest(split=split, case=value['id']):
                    validate_scenario(value)
                    self.assertTrue(value['synthetic'])
                    self.assertEqual(value['family'], 'priority_protection')
                    self.assertTrue(value['inquiry_queue']['enabled'])
                    for key in ('generation_selected_from_outcomes', 'policy_has_true_queue_state',
                                'future_information_available_to_policy',
                                'known_future_patients_available_to_policy',
                                'pre_reservation_for_unborn_patients'):
                        self.assertIs(value['experiment'][key], False)
                    self.assertTrue(all(h['clinical_data_source'] == 'synthetic' for h in value['hospitals']))

    def test_public_hospital_identity_is_preserved_and_patient_locations_diversify(self):
        original = scenario()
        hospital_points = [{key: h[key] for key in ('id', 'name', 'lat', 'lon')}
                           for h in original['hospitals']]
        first = original['patients'][0]
        phases = [first[key] - first['occur_t'] for key in
                  ('dispatch_t', 'ambulance_arrive_t', 'search_start_t')]
        for value in self.cases().values():
            self.assertEqual([{key: h[key] for key in ('id', 'name', 'lat', 'lon')}
                              for h in value['hospitals']], hospital_points)
            self.assertGreater(len({tuple(p['location']) for p in value['patients']}), 4)
            for patient in value['patients']:
                offsets = [patient['location'][i] - first['location'][i] for i in (0, 1)]
                self.assertTrue(all(abs(offset) <= .0040000001 for offset in offsets))
                for index in (0, 1):
                    self.assertAlmostEqual(patient['ambulance_origin'][index] - first['ambulance_origin'][index],
                                           offsets[index])
                self.assertEqual([patient[key] - patient['occur_t'] for key in
                                  ('dispatch_t', 'ambulance_arrive_t', 'search_start_t')], phases)

    def test_worker_pair_differs_only_in_worker_count_and_identity(self):
        for split in ('development', 'validation'):
            cases = self.cases(split)
            one = copy.deepcopy(cases['sufficient_burst_w1'])
            two = copy.deepcopy(cases['sufficient_burst_w2'])
            self.assertEqual(one['inquiry_queue']['workers'], 1)
            self.assertEqual(two['inquiry_queue']['workers'], 2)
            for value in (one, two):
                for key in ('id', 'label', 'case_kind'):
                    value.pop(key)
                value['experiment'].pop('case_kind')
                value['inquiry_queue'].pop('workers')
            self.assertEqual(one, two)

    def test_same_nonresponse_pair_differs_only_in_probability_and_identity(self):
        cases = build_priority_scenarios(scenario())
        for index in range(0, len(cases), 2):
            one, two = copy.deepcopy(cases[index:index + 2])
            for value in (one, two):
                for key in ('id', 'label', 'experiment_nonresponse'):
                    value.pop(key)
                value['experiment'].pop('nonresponse_probability')
            self.assertEqual(one, two)

    def test_sufficient_capacity_and_simultaneous_priority_competition_are_distinct(self):
        for split in ('development', 'validation'):
            cases = self.cases(split)
            for kind in ('sufficient_burst_w1', 'sufficient_burst_w2'):
                value = cases[kind]
                count = len(value['patients'])
                self.assertTrue(all(h['capacity']['ed_bed'] >= count for h in value['hospitals']))
                self.assertTrue(all(h['capacity']['special_team'] >= count for h in value['hospitals']))
            mixed = cases['mixed_priority_scarcity']
            self.assertEqual(len({p['search_start_t'] for p in mixed['patients']}), 1)
            self.assertEqual([p['priority'] for p in mixed['patients'][:4]], [1, 4, 2, 4])
            self.assertEqual(sum(h['capacity']['ed_bed'] for h in mixed['hospitals']),
                             len(mixed['patients']) // 2)
            self.assertTrue(all(p['treatment_minutes'] > mixed['max_minutes'] for p in mixed['patients']))
            self.assertTrue(all(p['required_capabilities'] == ['가상 기본진료']
                                for p in mixed['patients']))

    def test_staggered_high_priority_arrives_after_lower_priority_without_foreknowledge(self):
        for split in ('development', 'validation'):
            value = self.cases(split)['staggered_priority']
            high = [p for p in value['patients'] if p['priority'] == 4]
            lower = [p for p in value['patients'] if p['priority'] < 4]
            self.assertEqual(len(high), len(lower))
            self.assertGreater(min(p['search_start_t'] for p in high),
                               max(p['search_start_t'] for p in lower))
            self.assertEqual(sum(h['capacity']['ed_bed'] for h in value['hospitals']), len(lower))
            self.assertFalse(value['experiment']['pre_reservation_for_unborn_patients'])
            self.assertFalse(value['experiment']['known_future_patients_available_to_policy'])

    def test_scarce_capability_is_only_needed_by_predeclared_high_priority_subset(self):
        for split in ('development', 'validation'):
            value = self.cases(split)['scarce_capability']
            ids = value['experiment']['scarce_patient_ids']
            hospitals = value['experiment']['scarce_hospital_ids']
            self.assertEqual(len(ids), len(value['patients']) // 4)
            self.assertEqual(len(hospitals), 2)
            self.assertEqual([h['id'] for h in value['hospitals']
                              if '가상 희소치료조건' in h['capabilities']], hospitals)
            for patient in value['patients']:
                if patient['id'] in ids:
                    self.assertEqual(patient['priority'], 4)
                    self.assertIn('가상 희소치료조건', patient['required_capabilities'])
                    self.assertEqual(patient['resource_demands']['special_team'], 1)
                else:
                    self.assertNotIn('가상 희소치료조건', patient['required_capabilities'])

    def test_background_schedule_and_resource_events_are_fixed_not_outcome_selected(self):
        for split in ('development', 'validation'):
            cases = self.cases(split)
            shift = cases['shifting_workload']['inquiry_queue']['background_jobs']
            fixed = cases['sufficient_burst_w1']['inquiry_queue']['background_jobs']
            self.assertGreater(len(shift), len(fixed))
            self.assertEqual(shift, sorted(shift, key=lambda job: (job['t'], job['hospital'])))
            changed = cases['resource_change']
            self.assertEqual(len(changed['exogenous_events']), 2)
            start = min(p['search_start_t'] for p in changed['patients'])
            offsets = [11, 24] if split == 'validation' else [8, 18]
            self.assertEqual([event['t'] - start for event in changed['exogenous_events']], offsets)
            self.assertEqual([event['type'] for event in changed['exogenous_events']],
                             ['resource_unavailable', 'external_arrival'])
            self.assertTrue(all(event['hospital'] == changed['hospitals'][0]['id']
                                for event in changed['exogenous_events']))
            modified = scenario()
            modified['seed'] = 9876
            modified['response_environment']['*']['H1']['delay'] = 100
            modified['patients'][0]['treatment_minutes'] = 987
            regenerated = {s['case_kind']: s for s in build_priority_scenarios(modified, split, (0,))}
            self.assertEqual(regenerated['resource_change']['exogenous_events'], changed['exogenous_events'])
            self.assertEqual(regenerated['shifting_workload']['inquiry_queue']['background_jobs'], shift)
            self.assertTrue(all(not case['exogenous_events'] for kind, case in cases.items()
                                if kind != 'resource_change'))

    def test_total_shortage_limits_all_policies_without_making_more_resources(self):
        for split in ('development', 'validation'):
            value = self.cases(split)['total_shortage']
            self.assertEqual(sum(h['capacity']['ed_bed'] for h in value['hospitals']), 3)
            self.assertLess(3, len(value['patients']))
            self.assertTrue(all(p['treatment_minutes'] > value['max_minutes'] for p in value['patients']))
            self.assertEqual(value['experiment']['total_structural_slots'], 3)

    def test_generation_is_repeatable_and_invalid_input_rejected(self):
        self.assertEqual(build_priority_scenarios(scenario()), build_priority_scenarios(scenario()))
        for levels in ((), (0, 0), (True,), (-.1,), (1.1,), (float('nan'),), (.00001, .00002)):
            with self.subTest(levels=levels), self.assertRaises(ValueError):
                build_priority_scenarios(scenario(), nonresponse_levels=levels)
        with self.assertRaises(ValueError):
            build_priority_scenarios(scenario(), split='test')
        with self.assertRaises(ValueError):
            build_priority_scenarios(dict(scenario(), patients=[]))
        with self.assertRaises(ValueError):
            build_priority_scenarios(dict(scenario(), hospitals=scenario()['hospitals'][:3]))


if __name__ == '__main__':
    unittest.main()
