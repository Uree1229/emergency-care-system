"""고정 다수 환자 시나리오: 입력 분리, 인과 순서, 부족 반례. 네트워크 호출 없음."""
import copy
import unittest

from simulation.map_replay import generate_replay, validate_scenario
from simulation.multi_scenarios import COMPARISON_POLICIES, build_multi_scenarios


def base_scenario():
    return dict(
        id='normal', label='기준', seed=17, synthetic=True, max_minutes=120,
        hospitals=[dict(id=f'H{i}', name=f'시험 기관 {i}', lat=37.12 + .01 * (i + 1),
                        lon=127.07, capacity={'ed_bed': 2, 'special_team': 1},
                        capabilities=['가상 응급진료', '가상 추가치료']) for i in range(8)],
        patients=[dict(id='P01', label='시험 환자', location=[37.12, 127.07],
                       ambulance_origin=[37.09, 127.04], occur_t=0, dispatch_t=1,
                       ambulance_arrive_t=7, search_start_t=10, priority=4,
                       required_capabilities=['가상 응급진료'],
                       resource_demands={'ed_bed': 1, 'special_team': 1}, treatment_minutes=25)],
        exogenous_events=[], response_environment={'*': {'H0': {'delay': 1}}},
        assumptions=['시험 데이터'],
    )


class MultiScenariosTest(unittest.TestCase):
    def test_builder_preserves_input_and_road_coordinates(self):
        base = base_scenario()
        before = copy.deepcopy(base)
        values = build_multi_scenarios(base)
        self.assertEqual(base, before)
        self.assertEqual(len(values), 4)
        self.assertEqual(len({v['id'] for v in values}), 4)
        for value in values:
            validate_scenario(value)
            self.assertTrue(value['id'].startswith('multi_'))
            self.assertEqual(len(value['patients']), 5)
            self.assertFalse(value['exogenous_events'])
            self.assertEqual([(h['id'], h['name'], h['lat'], h['lon']) for h in value['hospitals']],
                             [(h['id'], h['name'], h['lat'], h['lon']) for h in base['hospitals']])
            for p in value['patients']:
                for key in ('location', 'ambulance_origin', 'occur_t', 'dispatch_t',
                            'ambulance_arrive_t', 'search_start_t'):
                    self.assertEqual(p[key], base['patients'][0][key])
                self.assertNotIn('arrival_assessment_adds_capabilities', p)

    def test_scenarios_policies_and_patient_data_are_independent(self):
        values = build_multi_scenarios(base_scenario())
        values[0]['hospitals'][0]['capacity']['ed_bed'] = 99
        values[0]['patients'][0]['location'][0] = 0
        values[0]['policies'][0]['k'] = 99
        self.assertEqual(values[1]['hospitals'][0]['capacity']['ed_bed'], 1)
        self.assertEqual(values[0]['patients'][1]['location'][0], 37.12)
        self.assertEqual(values[1]['policies'][0]['k'], 3)
        self.assertEqual(COMPARISON_POLICIES[0]['k'], 3)

    def test_comparison_has_same_inquiry_settings_and_explicit_environment(self):
        for value in build_multi_scenarios(base_scenario()):
            self.assertEqual([p['rule'] for p in value['policies']], ['R0', 'R1', 'R2', 'R3'])
            self.assertTrue(all((p['k'], p['wait'], p['notify']) == (3, 2, True)
                                for p in value['policies']))
            self.assertNotIn('*', value['response_environment'])
            self.assertEqual(set(value['response_environment']), {p['id'] for p in value['patients']})
            for entries in value['response_environment'].values():
                self.assertEqual(set(entries), {h['id'] for h in value['hospitals']})
            self.assertTrue(all(h['clinical_data_source'] == 'synthetic' for h in value['hospitals']))

    def test_replay_is_deterministic_and_phase_fair_for_all_four_rules(self):
        for value in build_multi_scenarios(base_scenario()):
            before = copy.deepcopy(value)
            first = generate_replay(value, value['policies'])
            second = generate_replay(value, value['policies'])
            self.assertEqual(first, second)
            self.assertEqual(value, before)
            common = [[e for e in b['events'] if e['t'] < 10] for b in first['branches']]
            self.assertTrue(all(events == common[0] for events in common))
            for branch in first['branches']:
                self.assertEqual(len(branch['patient_outcomes']), 5)
                self.assertTrue(all(e['t'] >= 10 for e in branch['events'] if e['type'] == 'ask'))

    def test_same_minute_ready_batch_and_capacity_shortage_are_real(self):
        simultaneous, _, _, shortage = build_multi_scenarios(base_scenario())
        joint = generate_replay(simultaneous, [simultaneous['policies'][-1]])['branches'][0]
        commits = [e for e in joint['events'] if e['type'] == 'commit']
        self.assertEqual(len(commits), 5)
        self.assertEqual(len({e['t'] for e in commits}), 1)
        self.assertEqual(joint['metrics']['unassigned'], 0)
        data = generate_replay(shortage, shortage['policies'])
        for branch in data['branches']:
            self.assertEqual(branch['metrics']['unassigned'], 3)
            self.assertEqual(len([e for e in branch['events'] if e['type'] == 'commit']), 2)
            self.assertEqual(len([e for e in branch['events'] if e['type'] == 'treatment_complete']), 0)

    def test_scarce_condition_has_one_structural_candidate(self):
        value = build_multi_scenarios(base_scenario())[1]
        special = value['patients'][-1]
        capable = [h['id'] for h in value['hospitals']
                   if set(special['required_capabilities']) <= set(h['capabilities'])]
        self.assertEqual(capable, [value['hospitals'][0]['id']])

    def test_missing_base_data_is_rejected(self):
        with self.assertRaises(ValueError):
            build_multi_scenarios({'hospitals': [], 'patients': []})
        value = base_scenario()
        value['hospitals'] = value['hospitals'][:2]
        with self.assertRaises(ValueError):
            build_multi_scenarios(value)


if __name__ == '__main__':
    unittest.main()
