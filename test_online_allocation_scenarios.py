"""Structural tests; reserved online experiment seeds are deliberately unused."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from simulation.map_replay import RouteSnapshot, generate_replay, validate_scenario
from simulation.online_allocation_scenarios import (
    ADVANCED, BASIC, DEVELOPMENT_SEEDS, FAMILIES, RESOURCE_KEYS, SCARCE,
    VALIDATION_SEEDS, build_online_scenarios,
)


class OnlineAllocationScenariosTest(unittest.TestCase):
    def setUp(self):
        self.scenarios = build_online_scenarios(7)
        self.by_family = {value['family']: value for value in self.scenarios}

    def test_family_order_and_reserved_split_are_fixed(self):
        self.assertEqual(len(FAMILIES), 9)
        self.assertEqual([v['family'] for v in self.scenarios], list(FAMILIES))
        self.assertEqual(DEVELOPMENT_SEEDS, tuple(range(10101, 10107)))
        self.assertEqual(VALIDATION_SEEDS, tuple(range(11101, 11113)))
        self.assertFalse(set(DEVELOPMENT_SEEDS) & set(VALIDATION_SEEDS))

    def test_reproducible_independent_family_streams_and_json(self):
        self.assertEqual(self.scenarios, build_online_scenarios(7))
        self.assertNotEqual(self.scenarios, build_online_scenarios(8))
        self.assertEqual(self.scenarios, json.loads(json.dumps(self.scenarios, allow_nan=False)))
        self.assertEqual(build_online_scenarios(7, reversed(FAMILIES)), list(reversed(self.scenarios)))
        for value in self.scenarios:
            self.assertEqual(build_online_scenarios(7, [value['family']]), [value])
        self.assertEqual(build_online_scenarios(7, []), [])

    def test_outputs_do_not_share_mutable_state(self):
        before = deepcopy(self.scenarios)
        self.scenarios[0]['hospitals'][0]['capacity']['ed_bed'] = 999
        self.scenarios[0]['response_environment']['P01']['FICT-H01']['attempts'][0]['delay'] = 999
        self.assertEqual(build_online_scenarios(7), before)
        self.assertEqual(self.scenarios[1], before[1])

    def test_all_inputs_validate_and_use_only_simulation_values(self):
        for value in self.scenarios:
            validate_scenario(value)
            self.assertIs(value['synthetic'], True)
            self.assertIs(value['online_allocation_comparison'], True)
            self.assertEqual(value['experiment']['family'], value['family'])
            self.assertLessEqual(value['max_minutes'], 100)
            self.assertNotIn('policies', value)
            self.assertEqual(value['experiment']['common_inquiry_settings'],
                             dict(k=3, wait=2, notify=True))
            for hospital in value['hospitals']:
                self.assertTrue(hospital['id'].startswith('FICT-'))
                self.assertTrue(hospital['name'].startswith('합성'))
                self.assertEqual(hospital['identity_source'], 'synthetic')
                self.assertEqual(set(hospital['capacity']), set(RESOURCE_KEYS))
                self.assertLess(abs(hospital['lat'] - 35.17), .25)
                self.assertLess(abs(hospital['lon'] - 126.85), .1)
            for patient in value['patients']:
                times = [patient[k] for k in ('occur_t', 'dispatch_t', 'ambulance_arrive_t', 'search_start_t')]
                self.assertEqual(times, sorted(times))
                self.assertTrue(all(q > 0 for q in patient['resource_demands'].values()))
                self.assertTrue(set(patient['resource_demands']) <= set(RESOURCE_KEYS))

    def test_dispatch_and_routes_do_not_require_external_services(self):
        routing = RouteSnapshot()
        with patch('builtins.open', side_effect=AssertionError('unexpected file read')):
            with patch.object(Path, 'read_text', side_effect=AssertionError('unexpected file read')):
                values = build_online_scenarios(7)
                self.assertEqual(values, self.scenarios)
                for value in values:
                    p = value['patients'][0]
                    for h in value['hospitals']:
                        route = routing.route(p['location'], (h['lat'], h['lon']))
                        self.assertEqual(route['geometry_kind'], 'synthetic_straight_line')
        self.assertEqual(routing.summary()['road_routes'], 0)

    def test_reply_environment_is_explicit_keyed_and_attempt_specific(self):
        for value in self.scenarios:
            env = value['response_environment']
            self.assertNotIn('*', env)
            self.assertEqual(set(env), {p['id'] for p in value['patients']})
            for p in value['patients']:
                self.assertEqual(set(env[p['id']]), {h['id'] for h in value['hospitals']})
                for entry in env[p['id']].values():
                    self.assertEqual(len(entry['attempts']), 3)
                    for attempt in entry['attempts']:
                        self.assertGreaterEqual(attempt['delay'], 1)
                        self.assertIsInstance(attempt['silent'], bool)
                self.assertNotIn('response_environment', p)
                self.assertNotIn('future_events', p)

    def test_greedy_trap_preserves_a_real_alternative_for_flexible_patient(self):
        for family in ('scarce_greedy_trap', 'scarce_long_detour'):
            value = self.by_family[family]
            flexible, specific = value['patients']
            h1, h2, h3 = value['hospitals']
            self.assertNotIn(SCARCE, flexible['required_capabilities'])
            self.assertIn(SCARCE, specific['required_capabilities'])
            self.assertIn(SCARCE, h1['capabilities'])
            self.assertNotIn(SCARCE, h2['capabilities'])
            self.assertEqual(h1['capacity']['ed_bed'], 1)
            self.assertEqual(h2['capacity']['ed_bed'], 1)
            self.assertEqual(h3['capacity']['ed_bed'], 0)
            self.assertTrue(all(p['treatment_minutes'] > value['max_minutes'] for p in value['patients']))
        routes = RouteSnapshot()
        value = self.by_family['scarce_long_detour']
        p = value['patients'][0]
        near, far = [routes.route(p['location'], (h['lat'], h['lon']))['duration_seconds']
                     for h in value['hospitals'][:2]]
        self.assertGreater(far, near * 10)

    def test_first_reply_and_nearest_arrival_are_distinct_with_same_wait(self):
        value = self.by_family['delayed_replies']
        for patient in value['patients']:
            env = value['response_environment'][patient['id']]
            self.assertEqual(env['FICT-H03']['attempts'][0]['delay'], 1)
            self.assertEqual(env['FICT-H01']['attempts'][0]['delay'], 3)
        policies = [dict(id=rule, label=rule, rule=rule, k=3, wait=2, notify=True)
                    for rule in ('R0', 'R1')]
        first, nearest = generate_replay(value, policies)['branches']
        self.assertEqual({p['destination_hospital_id'] for p in first['patient_outcomes']}, {'FICT-H03'})
        self.assertEqual({p['destination_hospital_id'] for p in nearest['patient_outcomes']}, {'FICT-H01'})
        self.assertEqual(first['metrics']['inquiries'], nearest['metrics']['inquiries'])

    def test_large_family_exceeds_r3_enumeration_limit(self):
        value = self.by_family['large_simultaneous']
        self.assertIn(len(value['patients']), (14, 15, 16))
        self.assertEqual(len({p['search_start_t'] for p in value['patients']}), 1)
        combinations = 1
        for patient in value['patients']:
            options = sum(set(patient['required_capabilities']) <= set(h['capabilities'])
                          for h in value['hospitals'])
            combinations *= options + 1
        self.assertGreater(combinations, 1_000_000)
        self.assertEqual(value['experiment']['theoretical_combinations'], combinations)

    def test_staggered_control_and_abundant_control_remove_batch_scarcity(self):
        staggered = self.by_family['staggered_singleton']
        starts = [p['search_start_t'] for p in staggered['patients']]
        self.assertEqual(len(set(starts)), len(starts))
        self.assertGreater(min(b - a for a, b in zip(starts, starts[1:])), 20)
        abundant = self.by_family['abundant_control']
        n = len(abundant['patients'])
        coords = {(h['lat'], h['lon']) for h in abundant['hospitals']}
        self.assertEqual(len(coords), 1)
        self.assertTrue(all(h['capacity']['ed_bed'] >= n for h in abundant['hospitals']))

    def test_condition_change_is_patient_specific_and_transfer_need_is_hidden_until_arrival(self):
        value = self.by_family['resource_change_enroute']
        a, b = value['patients']
        self.assertIn('special_team', a['resource_demands'])
        self.assertNotIn('special_team', b['resource_demands'])
        event = value['exogenous_events'][0]
        self.assertGreater(event['t'], a['search_start_t'] + 1)
        self.assertEqual(event['resource'], 'special_team')
        transfer = self.by_family['arrival_assessment_transfer']
        p = transfer['patients'][0]
        self.assertNotIn(ADVANCED, p['required_capabilities'])
        self.assertEqual(p['arrival_assessment_adds_capabilities'], [ADVANCED])
        self.assertEqual(sum(ADVANCED in h['capabilities'] for h in transfer['hospitals']), 1)
        self.assertTrue(all(BASIC in h['capabilities'] for h in transfer['hospitals']))

    def test_no_capacity_control_cannot_be_fixed_by_assignment(self):
        value = self.by_family['no_capacity_control']
        self.assertTrue(all(all(q == 0 for q in h['capacity'].values()) for h in value['hospitals']))
        self.assertTrue(all(p['resource_demands']['ed_bed'] == 1 for p in value['patients']))

    def test_tiny_seed_dynamic_cases_really_redirect_and_transfer(self):
        policy = [dict(id='unit', label='unit', rule='R1', k=3, wait=2, notify=True)]
        value = self.by_family['resource_change_enroute']
        branch = generate_replay(value, policy)['branches'][0]
        notified = [e['patient'] for e in branch['events'] if e['type'] == 'notify']
        redirected = [e['patient'] for e in branch['events'] if e['type'] == 'redirect']
        self.assertEqual(notified, ['P01'])
        self.assertEqual(redirected, ['P01'])
        value = self.by_family['arrival_assessment_transfer']
        branch = generate_replay(value, policy)['branches'][0]
        self.assertEqual(sum(e['type'] == 'arrival_unable' for e in branch['events']), 1)
        self.assertEqual(sum(e['type'] == 'transfer_depart' for e in branch['events']), 1)

    def test_invalid_seed_and_family_selection_fail_before_building(self):
        for seed in (-1, True, 1.5, '7', None):
            with self.assertRaises(ValueError):
                build_online_scenarios(seed)
        for families in ('delayed_replies', ['unknown'], ['delayed_replies', 'delayed_replies'], None):
            if families is None:
                continue
            with self.assertRaises(ValueError):
                build_online_scenarios(7, families)
        with self.assertRaises(ValueError):
            build_online_scenarios(7, 1)


if __name__ == '__main__':
    unittest.main()
