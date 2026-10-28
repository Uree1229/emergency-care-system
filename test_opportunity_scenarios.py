"""Structural cases; no reserved development or validation seed is executed."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from simulation.map_replay import validate_scenario
from simulation.opportunity_scenarios import (
    ADVERSARIAL_FAMILIES, BASIC, DEVELOPMENT_SEEDS, FAMILIES,
    ORIGINAL_FAMILIES, SCARCE, VALIDATION_SEEDS, build_opportunity_scenarios,
)
from simulation.partial_information_scenarios import build_information_scenarios


def one(family, seed=7):
    return build_opportunity_scenarios(seed, [family])[0]


class OpportunityScenarioTests(unittest.TestCase):
    def test_manifest_has_original_eight_and_eight_adversarial_families(self):
        self.assertEqual(len(ORIGINAL_FAMILIES), 8)
        self.assertEqual(len(ADVERSARIAL_FAMILIES), 8)
        self.assertEqual(FAMILIES, ORIGINAL_FAMILIES + ADVERSARIAL_FAMILIES)
        self.assertEqual(len(set(FAMILIES)), 16)
        cases = build_opportunity_scenarios(7)
        self.assertEqual([c['family'] for c in cases], list(FAMILIES))
        for case in cases:
            validate_scenario(case)
            self.assertEqual(case['id'], f"opportunity-{case['family']}-7")
            self.assertEqual(case['experiment']['patient_count'], len(case['patients']))
            self.assertEqual(case['opportunity_family'], case['family'])

    def test_reserved_seeds_are_disjoint_from_previous_experiments_and_structural_tests(self):
        self.assertEqual(DEVELOPMENT_SEEDS, (19101, 19102, 19103))
        self.assertEqual(VALIDATION_SEEDS, tuple(range(20101, 20107)))
        self.assertFalse(set(DEVELOPMENT_SEEDS) & set(VALIDATION_SEEDS))
        previous = set(range(17101, 17104)) | set(range(18101, 18107))
        self.assertFalse(previous & set(DEVELOPMENT_SEEDS + VALIDATION_SEEDS))
        self.assertNotIn(7, DEVELOPMENT_SEEDS + VALIDATION_SEEDS)
        self.assertNotIn(8, DEVELOPMENT_SEEDS + VALIDATION_SEEDS)

    def test_original_families_reuse_exact_truth_and_reporting_inputs(self):
        old = {c['family']: c for c in build_information_scenarios(7)}
        for case in build_opportunity_scenarios(7, ORIGINAL_FAMILIES):
            reference = old[case['family']]
            for key in ('hospitals', 'patients', 'exogenous_events', 'response_environment',
                        'observation_config', 'max_minutes', 'timeout_minutes',
                        'retry_minutes', 'accept_ttl_minutes', 'nonresponse',
                        'transfer_preparation_minutes'):
                self.assertEqual(case[key], reference[key], (case['family'], key))

    def test_subsets_preserve_requested_order_and_family_stream(self):
        all_cases = {c['family']: c for c in build_opportunity_scenarios(7)}
        selected = ['waiting_priority_low', 'fresh_control', 'multiple_waiting_patients']
        before = selected[:]
        subset = build_opportunity_scenarios(7, selected)
        self.assertEqual(selected, before)
        self.assertEqual(subset, [all_cases[f] for f in selected])
        self.assertEqual(build_opportunity_scenarios(7, iter(selected)), subset)
        self.assertEqual(build_opportunity_scenarios(7, []), [])

    def test_cases_and_repeated_builds_are_deeply_detached(self):
        cases = build_opportunity_scenarios(7)
        reference = deepcopy(cases)
        partial = next(c for c in cases if c['family'] == 'partial_team_missing')
        partial['hospitals'][0]['capacity']['ed_bed'] = 999
        partial['patients'][0]['priority'] = 1
        partial['observation_config']['initial_reports'][0]['resources']['ed_bed']['used'] = 999
        partial['assumptions'].append('caller mutation')
        for index, case in enumerate(cases):
            if case['family'] != 'partial_team_missing':
                self.assertEqual(case, reference[index])
        self.assertEqual(build_opportunity_scenarios(7), reference)

    def test_new_seed_varies_truth_map_and_treatment_without_global_randomness(self):
        first, other = one('partial_team_missing', 7), one('partial_team_missing', 8)
        self.assertNotEqual(
            (first['hospitals'][1]['lat'], first['patients'][0]['treatment_minutes']),
            (other['hospitals'][1]['lat'], other['patients'][0]['treatment_minutes']))
        self.assertEqual(one('partial_team_missing', 7), first)

    def test_unknown_team_is_distinct_from_known_fresh_bed_and_physical_zero(self):
        for family in ADVERSARIAL_FAMILIES:
            case = one(family)
            self.assertEqual(case['observation_config']['feed_interval_minutes'], 0)
            by_id = {h['id']: h for h in case['hospitals']}
            for report in case['observation_config']['initial_reports']:
                self.assertEqual(report['observed_at'], 0)
                resources = report['resources']
                self.assertIsNone(resources['special_team'])
                self.assertEqual(resources['ed_bed'],
                    {'capacity': by_id[report['hospital']]['capacity']['ed_bed'], 'used': 0})
                self.assertGreater(by_id[report['hospital']]['capacity']['special_team'], 0)

    def test_unresolved_family_never_refreshes_team_quantity_via_confirmation(self):
        case = one('partial_team_unresolved')
        config = case['observation_config']
        self.assertFalse(config['confirmation_silent'])
        self.assertEqual(config['confirmation_missing_resources'], ['special_team'])
        self.assertEqual(config['feed_interval_minutes'], 0)
        self.assertFalse(case['experiment']['resource_confirmation_resolves_missing_team'])

    def test_confirmation_delay_has_short_and_beyond_grace_cases(self):
        self.assertEqual(one('delayed_team_confirmation')['observation_config']['confirmation_delay_minutes'], 2)
        self.assertEqual(one('confirmation_beyond_grace')['observation_config']['confirmation_delay_minutes'], 5)
        for family in ('delayed_team_confirmation', 'confirmation_beyond_grace'):
            config = one(family)['observation_config']
            self.assertEqual(config['confirmation_missing_resources'], [])
            self.assertFalse(config['confirmation_silent'])

    def test_priority_tradeoffs_are_inverse_and_do_not_change_truth(self):
        high, low = one('waiting_priority_high'), one('waiting_priority_low')
        self.assertEqual([p['priority'] for p in high['patients']], [1, 1, 4])
        self.assertEqual([p['priority'] for p in low['patients']], [4, 4, 1])
        for key in ('hospitals', 'observation_config', 'response_environment', 'exogenous_events'):
            self.assertEqual(high[key], low[key])
        for a, b in zip(high['patients'], low['patients']):
            self.assertEqual({k: v for k, v in a.items() if k != 'priority'},
                             {k: v for k, v in b.items() if k != 'priority'})

    def test_multiple_waiters_compete_for_beds_before_two_team_counts_are_known(self):
        case = one('multiple_waiting_patients')
        self.assertEqual([p['id'] for p in case['patients']], ['P01', 'P02', 'P03', 'P04'])
        team = [p for p in case['patients'] if p['resource_demands'].get('special_team')]
        self.assertEqual(len(team), 2)
        self.assertTrue(all(p['required_capabilities'] == [BASIC, SCARCE] for p in team))
        near = next(h for h in case['hospitals'] if h['id'] == 'NEAR')
        self.assertEqual(near['capacity'], {'ed_bed': 3, 'special_team': 2})
        report = next(r for r in case['observation_config']['initial_reports'] if r['hospital'] == 'NEAR')
        self.assertEqual(report['resources']['ed_bed']['capacity'], 3)
        self.assertIsNone(report['resources']['special_team'])
        self.assertEqual(case['experiment']['patients_requiring_missing_resource'], ['P03', 'P04'])

    def test_no_alternative_has_only_one_structurally_compatible_hospital(self):
        case = one('no_alternative_hospital')
        self.assertEqual([h['id'] for h in case['hospitals']], ['NEAR'])
        self.assertEqual(set(case['response_environment']['*']), {'NEAR'})
        self.assertEqual([r['hospital'] for r in case['observation_config']['initial_reports']], ['NEAR'])
        self.assertIsNone(case['experiment']['alternative_hospital_id'])
        self.assertTrue(all(set(p['required_capabilities']) <= set(case['hospitals'][0]['capabilities'])
                            for p in case['patients']))

    def test_expiring_offer_does_not_become_valid_when_resource_confirmation_arrives(self):
        case = one('expiring_waiting_offer')
        acceptance = case['response_environment']['*']['NEAR']['delay']
        expiry = acceptance + case['accept_ttl_minutes']
        confirmation = acceptance + case['observation_config']['confirmation_delay_minutes']
        self.assertEqual((acceptance, expiry, confirmation), (1, 2, 3))
        self.assertLess(expiry, confirmation)
        self.assertTrue(case['experiment']['offer_expires_before_first_resource_reply'])

    def test_no_new_policy_config_or_expected_future_data_is_added_to_report_payload(self):
        known_observation_fields = {
            'feed_interval_minutes', 'feed_delay_minutes', 'feed_missing_resources',
            'confirmation_delay_minutes', 'confirmation_silent',
            'confirmation_missing_resources', 'feed_delay_by_source_minute', 'initial_reports',
        }
        for case in build_opportunity_scenarios(7):
            self.assertLessEqual(set(case['observation_config']), known_observation_fields)
            self.assertNotIn('opportunity_config', case)
            for report in case['observation_config'].get('initial_reports', []):
                self.assertEqual(set(report), {'hospital', 'observed_at', 'resources'})
                self.assertEqual(set(report['resources']), {'ed_bed', 'special_team'})

    def test_case_construction_needs_no_live_network_or_database(self):
        with patch('sqlite3.connect', side_effect=AssertionError('no live database')), \
                patch('socket.create_connection', side_effect=AssertionError('no network')):
            cases = build_opportunity_scenarios(7)
        for case in cases:
            self.assertTrue(case['synthetic'])
            self.assertTrue(all(h['identity_source'] == 'synthetic' for h in case['hospitals']))
            self.assertTrue(all(p['clinical_data_source'] == 'synthetic' for p in case['patients']))
            encoded = json.dumps(case)
            self.assertNotIn('http://', encoded)
            self.assertNotIn('https://', encoded)
            self.assertNotIn('serviceKey', encoded)

    def test_invalid_seed_and_family_selections_are_rejected(self):
        for seed in (-1, True, 1.5, '7', None):
            with self.subTest(seed=seed), self.assertRaises(ValueError):
                build_opportunity_scenarios(seed)
        for families in ('fresh_control', b'fresh_control', True, [None],
                         ['missing'], ['fresh_control', 'fresh_control']):
            with self.subTest(families=families), self.assertRaises(ValueError):
                build_opportunity_scenarios(7, families)


if __name__ == '__main__':
    unittest.main()
