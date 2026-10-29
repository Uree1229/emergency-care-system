"""Structural and causal-timing checks using tiny, non-holdout seeds.

These tests do not run a development/validation sweep or infer algorithm
quality from the crafted cases. They verify what the environment reports and
what actually exists are separate, reproducible experiment inputs.
"""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from simulation.map_replay import validate_scenario
from simulation.partial_information_scenarios import (
    BASIC, DEVELOPMENT_SEEDS, FAMILIES, RESOURCE_KEYS, VALIDATION_SEEDS,
    build_information_scenarios,
)


def one(family, seed=7):
    return build_information_scenarios(seed, [family])[0]


def hospital(case, hid):
    return next(row for row in case['hospitals'] if row['id'] == hid)


def report(case, hid):
    return next(row for row in case['observation_config']['initial_reports']
                if row['hospital'] == hid)


class PartialInformationScenarioTests(unittest.TestCase):
    def test_all_eight_cases_are_finite_standard_replay_inputs(self):
        cases = build_information_scenarios(7)
        self.assertEqual(len(cases), 8)
        self.assertEqual([case['family'] for case in cases], list(FAMILIES))
        for case in cases:
            with self.subTest(family=case['family']):
                validate_scenario(case)
                self.assertEqual(case['id'], f"information-{case['family']}-7")
                self.assertEqual(case['information_family'], case['family'])
                self.assertEqual(case['experiment']['information_family'], case['family'])
                self.assertEqual(case['seed'], 7)
                self.assertTrue(case['synthetic'])
                self.assertIn(len(case['hospitals']), (2, 3))
                self.assertIn(len(case['patients']), (2, 3))
                self.assertGreaterEqual(case['max_minutes'], 40)
                self.assertLessEqual(case['max_minutes'], 65)
                self.assertEqual(case['experiment']['timing_unit'], 'integer_minute')
                self.assertEqual(case['experiment']['common_inquiry_settings'],
                                 {'k': 3, 'wait': 2, 'notify': True})
                self.assertEqual({row['id'] for row in case['hospitals']}, {'NEAR', 'FAR'})
                self.assertTrue(all(set(row['capacity']) == set(RESOURCE_KEYS)
                                    for row in case['hospitals']))
                json.dumps(case, allow_nan=False, ensure_ascii=False)

    def test_development_and_validation_seeds_are_reserved_and_disjoint(self):
        self.assertEqual(DEVELOPMENT_SEEDS, (17101, 17102, 17103))
        self.assertEqual(VALIDATION_SEEDS, (18101, 18102, 18103, 18104, 18105, 18106))
        self.assertFalse(set(DEVELOPMENT_SEEDS) & set(VALIDATION_SEEDS))
        self.assertNotIn(7, DEVELOPMENT_SEEDS + VALIDATION_SEEDS)

    def test_family_filter_keeps_order_and_its_own_deterministic_random_stream(self):
        complete = {case['family']: case for case in build_information_scenarios(7)}
        selected = ['out_of_order', 'missing_recheck', 'fresh_control']
        before = list(selected)
        subset = build_information_scenarios(7, selected)
        self.assertEqual(selected, before)
        self.assertEqual([case['family'] for case in subset], selected)
        self.assertEqual(subset, [complete[family] for family in selected])
        self.assertEqual(build_information_scenarios(7, iter(selected)), subset)
        self.assertEqual(build_information_scenarios(7, []), [])

    def test_families_and_repeated_builds_do_not_share_mutable_values(self):
        cases = build_information_scenarios(7)
        expected = deepcopy(cases)
        cases[0]['hospitals'][0]['capacity']['ed_bed'] = 999
        cases[0]['patients'][0]['resource_demands']['ed_bed'] = 999
        cases[0]['response_environment']['*']['NEAR']['delay'] = 999
        cases[0]['assumptions'].append('caller mutation')
        partial = next(case for case in cases if case['family'] == 'partial_team_missing')
        partial['observation_config']['initial_reports'][0]['resources']['ed_bed']['used'] = 999
        self.assertEqual(cases[1]['hospitals'], expected[1]['hospitals'])
        self.assertEqual(cases[1]['patients'], expected[1]['patients'])
        self.assertEqual(build_information_scenarios(7), expected)

    def test_different_seeds_change_truth_inputs_without_using_global_state(self):
        first, changed = one('fresh_control', 7), one('fresh_control', 8)
        self.assertNotEqual(
            (hospital(first, 'FAR')['lat'], first['patients'][0]['treatment_minutes']),
            (hospital(changed, 'FAR')['lat'], changed['patients'][0]['treatment_minutes']))
        self.assertEqual(one('fresh_control', 7), first)

    def test_reporting_config_is_separate_from_capacity_and_uses_integer_minutes(self):
        for case in build_information_scenarios(7):
            config = case['observation_config']
            for key in ('feed_interval_minutes', 'feed_delay_minutes', 'confirmation_delay_minutes'):
                self.assertIs(type(config[key]), int)
                self.assertGreaterEqual(config[key], 0)
            self.assertGreaterEqual(config['confirmation_delay_minutes'], 1)
            self.assertIs(type(config['confirmation_silent']), bool)
            for row in case['hospitals']:
                self.assertNotIn('observed_at', row)
                self.assertNotIn('feed_delay_minutes', row)
                self.assertTrue(all(type(value) is int and value > 0
                                    for value in row['capacity'].values()))
            for observed in config.get('initial_reports', []):
                self.assertIs(type(observed['observed_at']), int)
                self.assertIn(observed['hospital'], {row['id'] for row in case['hospitals']})
                for resource in observed['resources'].values():
                    if resource is not None:
                        self.assertGreaterEqual(resource['used'], 0)
                        self.assertLessEqual(resource['used'], resource['capacity'])
                        self.assertIs(type(resource['capacity']), int)

    def test_fresh_control_uses_initial_truth_and_no_reporting_delay(self):
        case = one('fresh_control')
        config = case['observation_config']
        self.assertEqual(config['feed_interval_minutes'], 1)
        self.assertEqual(config['feed_delay_minutes'], 0)
        self.assertEqual(config['feed_missing_resources'], [])
        self.assertNotIn('initial_reports', config)
        self.assertEqual(case['exogenous_events'], [])

    def test_explicit_empty_initial_reports_distinguish_unknown_from_zero_capacity(self):
        for family, silent in (('missing_recheck', False), ('missing_unresolved', True)):
            case = one(family)
            config = case['observation_config']
            self.assertIn('initial_reports', config)
            self.assertEqual(config['initial_reports'], [])
            self.assertEqual(config['feed_interval_minutes'], 0)
            self.assertIs(config['confirmation_silent'], silent)
            self.assertEqual(config['confirmation_delay_minutes'], 1)
            self.assertTrue(all(row['capacity']['ed_bed'] > 0 for row in case['hospitals']))
            self.assertTrue(all(not row.get('initial_occupancy') for row in case['hospitals']))

    def test_stale_shortage_report_does_not_make_the_real_hospital_full(self):
        case = one('stale_shortage_recovery')
        near, observed = hospital(case, 'NEAR'), report(case, 'NEAR')
        self.assertEqual(observed['observed_at'], -11)
        self.assertGreater(0 - observed['observed_at'], 10)
        self.assertFalse(near.get('initial_occupancy'))
        for resource, quantity in near['capacity'].items():
            self.assertEqual(observed['resources'][resource], {'capacity': quantity, 'used': quantity})
        self.assertEqual(report(case, 'FAR')['observed_at'], 0)
        self.assertTrue(all(value['used'] == 0 for value in report(case, 'FAR')['resources'].values()))

    def test_optimistic_change_occurs_between_acceptance_and_wait_deadline(self):
        case = one('delayed_optimistic_change')
        replies = case['response_environment']['*']
        near_reply = replies['NEAR']['delay']
        wait = case['experiment']['common_inquiry_settings']['wait']
        event = case['exogenous_events'][0]
        self.assertEqual(near_reply, 1)
        self.assertLess(near_reply, event['t'])
        self.assertLess(event['t'], near_reply + wait)
        self.assertGreater(replies['FAR']['delay'], near_reply + wait)
        self.assertEqual(event['type'], 'resource_unavailable')
        self.assertEqual(event['resource'], 'special_team')
        self.assertEqual(hospital(case, 'NEAR')['capacity']['special_team'] - 1, 0)
        self.assertEqual(case['observation_config']['feed_delay_minutes'], 5)
        self.assertTrue(all(patient['required_capabilities'] == [BASIC] for patient in case['patients']))

    def test_pessimistic_report_lags_real_recovery_and_a_truthful_acceptance(self):
        case = one('delayed_pessimistic_recovery')
        near = hospital(case, 'NEAR')
        occupancy = near['initial_occupancy'][0]
        self.assertEqual(occupancy['demand'], near['capacity'])
        recovery = occupancy['until']
        reply = case['response_environment']['*']['NEAR']['delay']
        feed_delivery = recovery + case['observation_config']['feed_delay_minutes']
        self.assertEqual(recovery, 2)
        self.assertGreater(reply, recovery)
        self.assertLess(reply, feed_delivery)
        self.assertEqual(feed_delivery, 8)
        self.assertEqual(case['experiment']['first_post_recovery_feed_delivery_minute'], feed_delivery)
        self.assertNotIn('initial_reports', case['observation_config'])

    def test_partial_team_missing_targets_only_patients_who_need_the_team(self):
        case = one('partial_team_missing')
        self.assertEqual(case['observation_config']['feed_interval_minutes'], 0)
        for row in case['hospitals']:
            observed = report(case, row['id'])
            self.assertEqual(observed['observed_at'], 0)
            self.assertEqual(observed['resources']['ed_bed'],
                             {'capacity': row['capacity']['ed_bed'], 'used': 0})
            self.assertIsNone(observed['resources']['special_team'])
            self.assertGreater(row['capacity']['special_team'], 0)
        team = [p['id'] for p in case['patients'] if p['resource_demands'].get('special_team', 0)]
        no_team = [p['id'] for p in case['patients'] if not p['resource_demands'].get('special_team', 0)]
        self.assertEqual(team, ['P03'])
        self.assertEqual(no_team, ['P01', 'P02'])
        self.assertEqual(case['experiment']['patients_requiring_missing_resource'], team)

    def test_out_of_order_sources_have_different_truth_and_reversed_arrival_order(self):
        case = one('out_of_order')
        config = case['observation_config']
        delays = config['feed_delay_by_source_minute']
        old_source, new_source = 1, 4
        old_arrival = old_source + delays.get(str(old_source), config['feed_delay_minutes'])
        new_arrival = new_source + delays.get(str(new_source), config['feed_delay_minutes'])
        recovery = hospital(case, 'NEAR')['initial_occupancy'][0]['until']
        self.assertLess(old_source, recovery)
        self.assertLess(recovery, new_source)
        self.assertLess(old_source, new_source)
        self.assertGreater(old_arrival, new_arrival)
        self.assertEqual((old_arrival, new_arrival), (9, 4))
        self.assertEqual(case['experiment']['older_delivery_minute'], old_arrival)
        self.assertEqual(case['experiment']['newer_delivery_minute'], new_arrival)

    def test_build_needs_no_database_or_network_and_uses_synthetic_identity(self):
        with patch('sqlite3.connect', side_effect=AssertionError('no live DB')), \
                patch('socket.create_connection', side_effect=AssertionError('no network')):
            cases = build_information_scenarios(7)
        for case in cases:
            self.assertTrue(all(row['identity_source'] == 'synthetic' for row in case['hospitals']))
            self.assertTrue(all(row['clinical_data_source'] == 'synthetic' for row in case['patients']))
            encoded = json.dumps(case)
            self.assertNotIn('http://', encoded)
            self.assertNotIn('https://', encoded)
            self.assertNotIn('serviceKey', encoded)

    def test_invalid_seed_or_family_selection_is_rejected(self):
        for seed in (-1, True, 1.5, '7', None):
            with self.subTest(seed=seed), self.assertRaises(ValueError):
                build_information_scenarios(seed)
        for families in ('fresh_control', b'fresh_control', True, [None],
                         ['missing'], ['fresh_control', 'fresh_control']):
            with self.subTest(families=families), self.assertRaises(ValueError):
                build_information_scenarios(7, families)


if __name__ == '__main__':
    unittest.main()
