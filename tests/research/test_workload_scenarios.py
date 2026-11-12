"""문의 부하·조건 변화 실험 입력의 통제와 불변성. 네트워크 호출 없음."""
import copy
import unittest

from simulation.map_replay import generate_replay, validate_scenario
from simulation.workload_scenarios import build_workload_scenarios


def base_scenario():
    return dict(
        id='normal', label='기준', seed=17, synthetic=True, max_minutes=120,
        hospitals=[dict(id=f'H{i}', name=f'시험 기관 {i}', lat=37.12 + .01 * (i + 1),
                        lon=127.07, capacity={'ed_bed': 2, 'special_team': 1},
                        capabilities=['기존 조건'], initial_occupancy=[dict(until=50, demand={'ed_bed': 1})])
                   for i in range(8)],
        patients=[dict(id='OLD', label='시험 환자', location=[37.12, 127.07],
                       ambulance_origin=[37.09, 127.04], occur_t=0, dispatch_t=1,
                       ambulance_arrive_t=7, search_start_t=10, priority=4,
                       required_capabilities=['기존 조건'], resource_demands={'ed_bed': 1},
                       treatment_minutes=25, arrival_assessment_adds_capabilities=['기존 추가조건'])],
        policies=[dict(id='old', rule='R0')], assumptions=['기존 고정 문의 설정'],
        exogenous_events=[dict(t=15, type='resource_unavailable', hospital='H0',
                               resource='special_team', until=70)],
        response_environment={'*': {'H0': {'delay': 1}}},
    )


class WorkloadScenariosTest(unittest.TestCase):
    def test_two_families_have_valid_unique_scenarios(self):
        for split in ('development', 'validation'):
            scenarios = build_workload_scenarios(base_scenario(), split)
            self.assertEqual(len(scenarios), 8)
            self.assertEqual(len({s['id'] for s in scenarios}), 8)
            self.assertEqual(sum(s['family'] == 'inquiry_workload' for s in scenarios), 4)
            self.assertEqual(sum(s['family'] == 'condition_change' for s in scenarios), 4)
            for scenario in scenarios:
                validate_scenario(scenario)
                self.assertTrue(scenario['id'].startswith(('work_', 'change_')))
                self.assertEqual(scenario['split'], split)
                self.assertEqual(scenario['experiment']['split'], split)
                self.assertEqual(scenario['experiment']['family'], scenario['family'])
                self.assertEqual(scenario['experiment']['case_kind'], scenario['case_kind'])
                self.assertTrue(6 <= len(scenario['patients']) <= 12)
                self.assertNotIn('policies', scenario)
                self.assertEqual(scenario['experiment']['clinical_values'], 'synthetic')

    def test_preserves_base_and_route_coordinates(self):
        base = base_scenario()
        before = copy.deepcopy(base)
        scenarios = build_workload_scenarios(base)
        self.assertEqual(base, before)
        fields = ('id', 'name', 'lat', 'lon')
        for scenario in scenarios:
            self.assertEqual([[h[k] for k in fields] for h in scenario['hospitals']],
                             [[h[k] for k in fields] for h in base['hospitals']])
            for patient in scenario['patients']:
                self.assertEqual(patient['location'], base['patients'][0]['location'])
                self.assertEqual(patient['ambulance_origin'], base['patients'][0]['ambulance_origin'])
                self.assertNotIn('arrival_assessment_adds_capabilities', patient)
                self.assertEqual(patient['dispatch_t'] - patient['occur_t'], 1)
                self.assertEqual(patient['ambulance_arrive_t'] - patient['dispatch_t'], 6)
                self.assertEqual(patient['search_start_t'] - patient['ambulance_arrive_t'], 3)

    def test_each_input_and_each_patient_are_independent_objects(self):
        scenarios = build_workload_scenarios(base_scenario())
        scenarios[0]['patients'][0]['location'][0] = 0
        scenarios[0]['patients'][0]['required_capabilities'].append('변경')
        scenarios[0]['hospitals'][0]['capacity']['ed_bed'] = 99
        scenarios[0]['response_environment']['P01']['H0']['delay'] = 99
        scenarios[0]['inquiry_queue']['background_jobs'][0]['service_minutes'] = 99
        self.assertEqual(scenarios[0]['patients'][1]['location'][0], 37.12)
        self.assertNotIn('변경', scenarios[0]['patients'][1]['required_capabilities'])
        self.assertEqual(scenarios[1]['hospitals'][0]['capacity']['ed_bed'], 4)
        self.assertNotEqual(scenarios[1]['response_environment']['P01']['H0']['delay'], 99)
        self.assertNotEqual(scenarios[1]['inquiry_queue']['background_jobs'][0]['service_minutes'], 99)

    def test_worker_controls_differ_only_in_worker_count_and_identity(self):
        left, right = build_workload_scenarios(base_scenario())[:2]
        left, right = copy.deepcopy(left), copy.deepcopy(right)
        for field in ('id', 'label', 'case_kind'):
            left.pop(field)
            right.pop(field)
        left['experiment'].pop('case_kind')
        right['experiment'].pop('case_kind')
        self.assertEqual((left['inquiry_queue']['workers'], right['inquiry_queue']['workers']), (1, 2))
        right['inquiry_queue']['workers'] = 1
        self.assertEqual(left, right)
        self.assertFalse(left['exogenous_events'])

    def test_queue_background_is_policy_independent_and_covers_each_hospital(self):
        for scenario in build_workload_scenarios(base_scenario())[:4]:
            queue = scenario['inquiry_queue']
            self.assertTrue(queue['enabled'])
            jobs = queue['background_jobs']
            self.assertEqual(len(jobs), 2 * len(scenario['hospitals']))
            for h in scenario['hospitals']:
                self.assertEqual(sum(job['hospital'] == h['id'] for job in jobs), 2)
            self.assertTrue(all(job['t'] >= 0 and job['service_minutes'] >= 1 for job in jobs))
            self.assertNotIn('*', scenario['response_environment'])
            for p in scenario['patients']:
                self.assertEqual(set(scenario['response_environment'][p['id']]),
                                 {h['id'] for h in scenario['hospitals']})

    def test_change_controls_have_identical_inputs_except_event_and_labels(self):
        scenarios = build_workload_scenarios(base_scenario())[4:]
        stripped = []
        for scenario in scenarios:
            self.assertFalse(scenario['inquiry_queue']['enabled'])
            self.assertFalse(scenario['inquiry_queue']['background_jobs'])
            value = copy.deepcopy(scenario)
            for field in ('id', 'label', 'description', 'exogenous_events', 'case_kind'):
                value.pop(field)
            value['experiment'].pop('case_kind')
            stripped.append(value)
        self.assertTrue(all(s == stripped[0] for s in stripped))
        self.assertEqual(scenarios[0]['exogenous_events'], [])
        short, long, arrival = [s['exogenous_events'][0] for s in scenarios[1:]]
        self.assertEqual(short['t'], long['t'])
        self.assertLess(short['until'], long['until'])
        self.assertEqual(short['hospital'], long['hospital'])
        self.assertEqual(arrival['t'], short['t'])
        self.assertEqual(arrival['type'], 'external_arrival')
        self.assertGreaterEqual(short['t'], min(p['search_start_t'] for p in scenarios[0]['patients']))

    def test_development_and_validation_inputs_predeclared_different(self):
        development = build_workload_scenarios(base_scenario())
        validation = build_workload_scenarios(base_scenario(), 'validation')
        self.assertTrue(set(s['id'] for s in development).isdisjoint(s['id'] for s in validation))
        for dev, val in zip(development, validation):
            self.assertEqual(dev['case_kind'], val['case_kind'])
            self.assertNotEqual(len(dev['patients']), len(val['patients']))
            self.assertNotEqual([p['occur_t'] for p in dev['patients']],
                                [p['occur_t'] for p in val['patients']])
            self.assertNotEqual(dev['timeout_minutes'], val['timeout_minutes'])
            self.assertNotEqual(dev['response_environment'], val['response_environment'])
        self.assertNotEqual(development[0]['hospitals'][0]['capacity'],
                            validation[0]['hospitals'][0]['capacity'])

    def test_change_cases_can_affect_an_already_committed_patient_without_hindsight(self):
        policy = dict(id='sample', label='시험', rule='R1', k=3, wait=0, notify=True)
        scenarios = build_workload_scenarios(base_scenario())[4:]
        for scenario in scenarios:
            replay = generate_replay(scenario, [policy])['branches'][0]
            notifications = [e for e in replay['events'] if e['type'] == 'notify']
            if not scenario['exogenous_events']:
                self.assertEqual(notifications, [])
                continue
            self.assertTrue(notifications)
            for notification in notifications:
                patient = next(p for p in scenario['patients'] if p['id'] == notification['patient'])
                self.assertIn(notification['resource'], patient['resource_demands'])
                commitments = [e for e in replay['events'] if e['type'] == 'commit'
                               and e['patient'] == notification['patient']]
                prior = [e for e in commitments if e['t'] < notification['t']]
                self.assertTrue(prior)
                self.assertGreater(prior[-1]['eta'], notification['t'])

    def test_scarce_condition_and_shortage_have_structural_meaning(self):
        _, _, scarce, shortage, *_ = build_workload_scenarios(base_scenario())
        patient = scarce['patients'][-1]
        eligible = [h['id'] for h in scarce['hospitals']
                    if set(patient['required_capabilities']) <= set(h['capabilities'])]
        self.assertEqual(eligible, [scarce['hospitals'][0]['id']])
        self.assertEqual(patient['priority'], 4)
        self.assertEqual(sum(h['capacity']['ed_bed'] for h in shortage['hospitals']), 3)
        self.assertTrue(all(p['treatment_minutes'] > shortage['max_minutes'] for p in shortage['patients']))
        demands = {tuple(sorted(p['resource_demands'])) for p in scarce['patients']}
        self.assertGreaterEqual(len(demands), 3)

    def test_invalid_split_and_missing_base_rejected(self):
        with self.assertRaises(ValueError):
            build_workload_scenarios(base_scenario(), 'test')
        with self.assertRaises(ValueError):
            build_workload_scenarios({'patients': [], 'hospitals': []})
        base = base_scenario()
        base['hospitals'] = base['hospitals'][:3]
        with self.assertRaises(ValueError):
            build_workload_scenarios(base)


if __name__ == '__main__':
    unittest.main()
