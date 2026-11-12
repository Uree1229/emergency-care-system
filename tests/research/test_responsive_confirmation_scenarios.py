"""Structural cases only; reserved development and validation seeds stay unused."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from simulation.map_replay import validate_scenario
from simulation.opportunity_scenarios import build_opportunity_scenarios
from simulation.responsive_confirmation_scenarios import (
    ADVERSARIAL_FAMILIES, BASIC, DEVELOPMENT_SEEDS, FAMILIES,
    ORIGINAL_FAMILIES, SCARCE, VALIDATION_SEEDS,
    build_responsive_confirmation_scenarios,
)


ENVIRONMENT_FIELDS = (
    'hospitals', 'patients', 'exogenous_events', 'response_environment',
    'observation_config', 'max_minutes', 'timeout_minutes', 'retry_minutes',
    'accept_ttl_minutes', 'nonresponse', 'transfer_preparation_minutes',
)


def one(family, seed=7):
    return build_responsive_confirmation_scenarios(seed, [family])[0]


class ResponsiveConfirmationScenarioTests(unittest.TestCase):
    def test_manifest_keeps_sixteen_previous_and_adds_six_adversarial_families(self):
        self.assertEqual(len(ORIGINAL_FAMILIES), 16)
        self.assertEqual(len(ADVERSARIAL_FAMILIES), 6)
        self.assertEqual(FAMILIES, ORIGINAL_FAMILIES + ADVERSARIAL_FAMILIES)
        self.assertEqual(len(set(FAMILIES)), 22)
        cases = build_responsive_confirmation_scenarios(7)
        self.assertEqual([case['family'] for case in cases], list(FAMILIES))
        for case in cases:
            validate_scenario(case)
            self.assertEqual(case['id'], f"responsive-confirmation-{case['family']}-7")
            self.assertEqual(case['experiment']['patient_count'], len(case['patients']))
            self.assertEqual(case['responsive_confirmation_family'], case['family'])

    def test_reserved_seeds_are_disjoint_from_all_prior_information_sweeps(self):
        self.assertEqual(DEVELOPMENT_SEEDS, (21101, 21102, 21103))
        self.assertEqual(VALIDATION_SEEDS, tuple(range(22101, 22107)))
        previous = (set(range(17101, 17104)) | set(range(18101, 18107)) |
                    set(range(19101, 19104)) | set(range(20101, 20107)))
        reserved = set(DEVELOPMENT_SEEDS + VALIDATION_SEEDS)
        self.assertFalse(previous & reserved)
        self.assertFalse(set(DEVELOPMENT_SEEDS) & set(VALIDATION_SEEDS))
        self.assertTrue({7, 8}.isdisjoint(reserved))

    def test_original_families_keep_exact_frozen_environment_inputs(self):
        originals = {case['family']: case for case in build_opportunity_scenarios(7)}
        for case in build_responsive_confirmation_scenarios(7, ORIGINAL_FAMILIES):
            for key in ENVIRONMENT_FIELDS:
                self.assertEqual(case[key], originals[case['family']][key],
                                 (case['family'], key))

    def test_subsets_preserve_order_and_independent_family_stream(self):
        cases = {case['family']: case for case in build_responsive_confirmation_scenarios(7)}
        selected = ['watch_outage_recovery', 'fresh_control', 'slow_confirmation_eight']
        before = selected[:]
        subset = build_responsive_confirmation_scenarios(7, selected)
        self.assertEqual(selected, before)
        self.assertEqual(subset, [cases[family] for family in selected])
        self.assertEqual(build_responsive_confirmation_scenarios(7, iter(selected)), subset)
        self.assertEqual(build_responsive_confirmation_scenarios(7, []), [])

    def test_repeated_builds_and_families_are_deeply_detached(self):
        cases = build_responsive_confirmation_scenarios(7)
        reference = deepcopy(cases)
        changed = next(case for case in cases if case['family'] == 'slow_confirmation_eight')
        changed['hospitals'][0]['capacity']['ed_bed'] = 999
        changed['patients'][0]['priority'] = 1
        changed['observation_config']['initial_reports'][0]['resources']['ed_bed']['used'] = 999
        changed['assumptions'].append('caller mutation')
        for index, case in enumerate(cases):
            if case['family'] != 'slow_confirmation_eight':
                self.assertEqual(case, reference[index])
        self.assertEqual(build_responsive_confirmation_scenarios(7), reference)

    def test_new_seeds_change_map_and_treatment_but_not_existing_case_random_stream(self):
        first, second = one('slow_confirmation_eight', 7), one('slow_confirmation_eight', 8)
        self.assertNotEqual(
            (first['hospitals'][1]['lat'], first['patients'][0]['treatment_minutes']),
            (second['hospitals'][1]['lat'], second['patients'][0]['treatment_minutes']))
        base = build_opportunity_scenarios(7, ['partial_team_missing'])[0]
        for case in build_responsive_confirmation_scenarios(7, ADVERSARIAL_FAMILIES):
            self.assertEqual(case['hospitals'], base['hospitals'])
            self.assertEqual(case['response_environment'], base['response_environment'])

    def test_confirmation_delays_cover_three_and_eight_minutes(self):
        self.assertEqual(one('slow_confirmation_three')['observation_config']['confirmation_delay_minutes'], 3)
        self.assertEqual(one('slow_confirmation_eight')['observation_config']['confirmation_delay_minutes'], 8)
        for family in ('slow_confirmation_three', 'slow_confirmation_eight'):
            config = one(family)['observation_config']
            self.assertFalse(config['confirmation_silent'])
            self.assertEqual(config['confirmation_missing_resources'], [])

    def test_persistent_partial_information_remains_missing_after_slow_confirmation(self):
        case = one('persistent_partial_slow')
        config = case['observation_config']
        self.assertEqual(config['confirmation_delay_minutes'], 8)
        self.assertEqual(config['confirmation_missing_resources'], ['special_team'])
        self.assertFalse(config['confirmation_silent'])
        self.assertFalse(case['experiment']['resource_confirmation_resolves_missing_team'])
        self.assertEqual(config['feed_interval_minutes'], 0)

    def test_acceptance_expires_before_early_or_post_acceptance_quantity_reply(self):
        case = one('expiry_before_slow_confirmation')
        acceptance = case['response_environment']['*']['NEAR']['delay']
        expiry = acceptance + case['accept_ttl_minutes']
        early_quantity_reply = case['observation_config']['confirmation_delay_minutes']
        self.assertEqual((acceptance, expiry, early_quantity_reply), (1, 2, 8))
        self.assertLess(expiry, early_quantity_reply)
        self.assertLess(expiry, acceptance + early_quantity_reply)
        self.assertTrue(case['experiment']['offer_expires_before_first_resource_reply'])

    def test_outage_recovery_requires_a_new_current_report_without_periodic_feed(self):
        case = one('watch_outage_recovery')
        self.assertEqual(case['exogenous_events'], [dict(
            type='resource_unavailable', t=1, until=8,
            hospital='NEAR', resource='special_team')])
        self.assertEqual(case['observation_config']['confirmation_delay_minutes'], 3)
        self.assertEqual(case['observation_config']['feed_interval_minutes'], 0)
        self.assertIsNone(case['observation_config']['initial_reports'][0]['resources']['special_team'])
        self.assertFalse(case['experiment']['periodic_feed_enabled'])

    def test_future_patient_has_not_occurred_during_initial_flexible_batch(self):
        case = one('future_patient_not_yet_active')
        by_id = {patient['id']: patient for patient in case['patients']}
        for key in ('occur_t', 'dispatch_t', 'ambulance_arrive_t', 'search_start_t'):
            self.assertEqual(by_id['P03'][key], 12)
            self.assertEqual(by_id['P01'][key], 0)
            self.assertEqual(by_id['P02'][key], 0)
        self.assertEqual(by_id['P03']['required_capabilities'], [BASIC, SCARCE])
        self.assertEqual(by_id['P03']['resource_demands'], {'ed_bed': 1, 'special_team': 1})
        self.assertEqual(case['observation_config']['confirmation_delay_minutes'], 3)

    def test_original_multiple_waiters_still_supply_request_deduplication_contention(self):
        case = one('multiple_waiting_patients')
        waiting = [patient for patient in case['patients'] if patient['resource_demands'].get('special_team')]
        self.assertEqual([patient['id'] for patient in waiting], ['P03', 'P04'])
        self.assertTrue(all(patient['search_start_t'] == 0 for patient in waiting))
        near = next(hospital for hospital in case['hospitals'] if hospital['id'] == 'NEAR')
        self.assertEqual(near['capacity'], {'ed_bed': 3, 'special_team': 2})

    def test_unknown_quantity_does_not_mutate_physical_capacity(self):
        for case in build_responsive_confirmation_scenarios(7, ADVERSARIAL_FAMILIES):
            by_id = {hospital['id']: hospital for hospital in case['hospitals']}
            for report in case['observation_config']['initial_reports']:
                self.assertIsNone(report['resources']['special_team'])
                self.assertEqual(report['observed_at'], 0)
                self.assertGreater(by_id[report['hospital']]['capacity']['special_team'], 0)
                self.assertEqual(report['resources']['ed_bed'], {
                    'capacity': by_id[report['hospital']]['capacity']['ed_bed'], 'used': 0})

    def test_cases_do_not_contain_policy_tuning_or_future_information_in_report_payloads(self):
        known_config = {
            'feed_interval_minutes', 'feed_delay_minutes', 'feed_missing_resources',
            'confirmation_delay_minutes', 'confirmation_silent',
            'confirmation_missing_resources', 'feed_delay_by_source_minute', 'initial_reports',
        }
        for case in build_responsive_confirmation_scenarios(7):
            self.assertLessEqual(set(case['observation_config']), known_config)
            self.assertFalse({'responsive_config', 'opportunity_config', 'prefetch_config',
                              'watch_config'} & set(case))
            for report in case['observation_config'].get('initial_reports', []):
                self.assertEqual(set(report), {'hospital', 'observed_at', 'resources'})
                self.assertEqual(set(report['resources']), {'ed_bed', 'special_team'})
                for resource in report['resources'].values():
                    if resource is not None:
                        self.assertEqual(set(resource), {'capacity', 'used'})

    def test_case_construction_is_synthetic_and_needs_no_live_database_or_network(self):
        with patch('sqlite3.connect', side_effect=AssertionError('no live database')), \
                patch('socket.create_connection', side_effect=AssertionError('no network')):
            cases = build_responsive_confirmation_scenarios(7)
        for case in cases:
            self.assertTrue(case['synthetic'])
            self.assertTrue(all(h['identity_source'] == 'synthetic' for h in case['hospitals']))
            self.assertTrue(all(p['clinical_data_source'] == 'synthetic' for p in case['patients']))
            encoded = json.dumps(case, allow_nan=False)
            self.assertNotIn('http://', encoded)
            self.assertNotIn('https://', encoded)
            self.assertNotIn('serviceKey', encoded)

    def test_horizon_and_common_inquiry_settings_are_identical_in_every_case(self):
        for case in build_responsive_confirmation_scenarios(7):
            self.assertEqual(case['max_minutes'], 60)
            self.assertEqual(case['experiment']['common_inquiry_settings'],
                             {'k': 3, 'wait': 2, 'notify': True})
            self.assertLess(max(p['search_start_t'] for p in case['patients']), case['max_minutes'])

    def test_invalid_seed_and_family_selection_are_rejected(self):
        for seed in (-1, True, 1.5, '7', None):
            with self.subTest(seed=seed), self.assertRaises(ValueError):
                build_responsive_confirmation_scenarios(seed)
        for families in ('fresh_control', b'fresh_control', True, [None],
                         ['missing'], ['fresh_control', 'fresh_control']):
            with self.subTest(families=families), self.assertRaises(ValueError):
                build_responsive_confirmation_scenarios(7, families)


if __name__ == '__main__':
    unittest.main()
