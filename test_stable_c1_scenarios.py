"""Structural fixtures only; reserved sweep seeds are not executed here."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from simulation.map_replay import validate_scenario
from simulation.responsive_confirmation_scenarios import build_responsive_confirmation_scenarios
from simulation.stable_c1_scenarios import (
    ADVERSARIAL_FAMILIES, BASIC, DEVELOPMENT_SEEDS, FAMILIES, ORIGINAL_FAMILIES,
    SCARCE, VALIDATION_SEEDS, build_stable_c1_scenarios,
)


ENVIRONMENT_FIELDS = (
    'hospitals', 'patients', 'exogenous_events', 'response_environment',
    'observation_config', 'max_minutes', 'timeout_minutes', 'retry_minutes',
    'accept_ttl_minutes', 'nonresponse', 'transfer_preparation_minutes',
)


def one(family, seed=7):
    return build_stable_c1_scenarios(seed, [family])[0]


class StableC1ScenarioTests(unittest.TestCase):
    def test_manifest_retains_twenty_two_previous_and_adds_four_structural_cases(self):
        self.assertEqual(len(ORIGINAL_FAMILIES), 22)
        self.assertEqual(len(ADVERSARIAL_FAMILIES), 4)
        self.assertEqual(FAMILIES, ORIGINAL_FAMILIES + ADVERSARIAL_FAMILIES)
        self.assertEqual(len(set(FAMILIES)), 26)
        cases = build_stable_c1_scenarios(7)
        self.assertEqual([case['family'] for case in cases], list(FAMILIES))
        for case in cases:
            validate_scenario(case)
            self.assertEqual(case['id'], f"stable-c1-{case['family']}-7")
            self.assertEqual(case['stable_c1_family'], case['family'])
            self.assertEqual(case['experiment']['patient_count'], len(case['patients']))

    def test_reserved_seeds_disjoint_from_every_prior_sweep_and_structural_seed(self):
        self.assertEqual(DEVELOPMENT_SEEDS, (23101, 23102, 23103))
        self.assertEqual(VALIDATION_SEEDS, tuple(range(24101, 24107)))
        previous = set()
        for start, count in ((17101, 3), (18101, 6), (19101, 3), (20101, 6),
                             (21101, 3), (22101, 6)):
            previous.update(range(start, start + count))
        reserved = set(DEVELOPMENT_SEEDS + VALIDATION_SEEDS)
        self.assertFalse(previous & reserved)
        self.assertFalse(set(DEVELOPMENT_SEEDS) & set(VALIDATION_SEEDS))
        self.assertTrue({7, 8}.isdisjoint(reserved))

    def test_previous_families_keep_exact_frozen_environment_fields(self):
        originals = {case['family']: case for case in build_responsive_confirmation_scenarios(7)}
        for case in build_stable_c1_scenarios(7, ORIGINAL_FAMILIES):
            for key in ENVIRONMENT_FIELDS:
                self.assertEqual(case[key], originals[case['family']][key],
                                 (case['family'], key))

    def test_subsets_keep_order_and_do_not_change_family_randomness(self):
        cases = {case['family']: case for case in build_stable_c1_scenarios(7)}
        selected = ['tie_same_request_identity', 'no_alternative_hospital',
                    'greedy_suboptimal_scarce_option', 'fresh_control']
        before = selected[:]
        subset = build_stable_c1_scenarios(7, selected)
        self.assertEqual(selected, before)
        self.assertEqual(subset, [cases[family] for family in selected])
        self.assertEqual(build_stable_c1_scenarios(7, iter(selected)), subset)
        self.assertEqual(build_stable_c1_scenarios(7, []), [])

    def test_builds_and_families_are_deeply_detached(self):
        cases = build_stable_c1_scenarios(7)
        reference = deepcopy(cases)
        changed = next(case for case in cases if case['family'] == 'tie_same_request_identity')
        changed['hospitals'][0]['capacity']['ed_bed'] = 999
        changed['patients'][0]['priority'] = 1
        changed['assumptions'].append('caller mutation')
        for index, case in enumerate(cases):
            if case['family'] != 'tie_same_request_identity':
                self.assertEqual(case, reference[index])
        self.assertEqual(build_stable_c1_scenarios(7), reference)

    def test_seed_variation_changes_treatment_and_non_tied_map_without_breaking_fixture(self):
        a, b = one('greedy_suboptimal_scarce_option', 7), one('greedy_suboptimal_scarce_option', 8)
        self.assertNotEqual(
            (a['hospitals'][1]['lat'], a['patients'][0]['treatment_minutes']),
            (b['hospitals'][1]['lat'], b['patients'][0]['treatment_minutes']))
        for seed in (7, 8):
            case = one('tie_equal_distance_hospital_identity', seed)
            self.assertEqual(
                [(h['lat'], h['lon']) for h in case['hospitals']],
                [(35.18, 126.85)] * 2)

    def test_older_request_precedes_lexically_earlier_id_and_offers_align(self):
        case = one('tie_older_request_priority')
        patients = {p['id']: p for p in case['patients']}
        replies = case['response_environment']
        self.assertEqual(patients['P02']['search_start_t'], 0)
        self.assertEqual(patients['P01']['search_start_t'], 1)
        self.assertEqual(patients['P01']['search_start_t'] + replies['*']['NEAR']['delay'], 2)
        self.assertEqual(patients['P02']['search_start_t'] + replies['P02']['NEAR']['delay'], 2)
        self.assertEqual(case['experiment']['expected_first_tie_patient_id'], 'P02')
        self.assertEqual(case['hospitals'][0]['capacity']['ed_bed'], 1)
        self.assertEqual({p['priority'] for p in case['patients']}, {4})
        self.assertEqual({tuple(p['location']) for p in case['patients']}, {(35.17, 126.85)})

    def test_same_request_tie_is_supplied_in_reverse_patient_identity_order(self):
        case = one('tie_same_request_identity')
        self.assertEqual([p['id'] for p in case['patients']], ['P02', 'P01'])
        self.assertEqual({p['search_start_t'] for p in case['patients']}, {0})
        self.assertEqual(case['experiment']['expected_first_tie_patient_id'], 'P01')
        self.assertEqual(len(case['hospitals']), 1)
        self.assertEqual(case['hospitals'][0]['capacity']['ed_bed'], 1)

    def test_equal_distance_hospital_tie_reverses_both_input_orders(self):
        case = one('tie_equal_distance_hospital_identity')
        self.assertEqual([h['id'] for h in case['hospitals']], ['Z_EQ', 'A_EQ'])
        self.assertEqual([p['id'] for p in case['patients']], ['P02', 'P01'])
        self.assertEqual(len({(h['lat'], h['lon']) for h in case['hospitals']}), 1)
        self.assertTrue(all(h['capacity']['ed_bed'] == 1 for h in case['hospitals']))
        self.assertEqual(case['experiment']['expected_first_tie_hospital_id'], 'A_EQ')
        self.assertEqual(set(case['response_environment']['*']), {'Z_EQ', 'A_EQ'})

    def test_suboptimal_greedy_fixture_has_flexible_first_and_only_near_for_second(self):
        case = one('greedy_suboptimal_scarce_option')
        patients = {p['id']: p for p in case['patients']}
        hospitals = {h['id']: h for h in case['hospitals']}
        self.assertEqual(patients['P01']['required_capabilities'], [BASIC])
        self.assertEqual(patients['P02']['required_capabilities'], [BASIC, SCARCE])
        self.assertEqual(patients['P02']['resource_demands'], {'ed_bed': 1, 'special_team': 1})
        self.assertTrue(SCARCE in hospitals['NEAR']['capabilities'])
        self.assertNotIn(SCARCE, hospitals['FAR']['capabilities'])
        self.assertTrue(all(h['capacity']['ed_bed'] == 1 for h in case['hospitals']))
        self.assertEqual(case['experiment']['expected_greedy_first_batch_unassigned_priority'], 4)
        self.assertEqual(case['experiment']['expected_c1_first_batch_unassigned_priority'], 0)

    def test_extra_families_use_fresh_report_inputs_without_future_environment_events(self):
        for case in build_stable_c1_scenarios(7, ADVERSARIAL_FAMILIES):
            config = case['observation_config']
            self.assertEqual(config['feed_interval_minutes'], 1)
            self.assertEqual(config['feed_delay_minutes'], 0)
            self.assertEqual(config['feed_missing_resources'], [])
            self.assertNotIn('initial_reports', config)
            self.assertEqual(case['exogenous_events'], [])
            self.assertFalse({'responsive_config', 'opportunity_config', 'tie_config'} & set(case))

    def test_original_regression_condition_remains_present_with_missing_team(self):
        case = one('no_alternative_hospital')
        self.assertEqual([h['id'] for h in case['hospitals']], ['NEAR'])
        self.assertEqual([p['id'] for p in case['patients']], ['P01', 'P02', 'P03'])
        self.assertIsNone(case['observation_config']['initial_reports'][0]['resources']['special_team'])
        self.assertEqual(case['hospitals'][0]['capacity']['ed_bed'], 2)

    def test_construction_is_fully_synthetic_and_does_not_touch_network_or_live_db(self):
        with patch('sqlite3.connect', side_effect=AssertionError('no live database')), \
                patch('socket.create_connection', side_effect=AssertionError('no network')):
            cases = build_stable_c1_scenarios(7)
        for case in cases:
            self.assertTrue(case['synthetic'])
            self.assertTrue(all(h['identity_source'] == 'synthetic' for h in case['hospitals']))
            self.assertTrue(all(p['clinical_data_source'] == 'synthetic' for p in case['patients']))
            encoded = json.dumps(case, allow_nan=False)
            self.assertNotIn('http://', encoded)
            self.assertNotIn('https://', encoded)
            self.assertNotIn('serviceKey', encoded)

    def test_common_inquiry_and_horizon_settings_are_identical(self):
        for case in build_stable_c1_scenarios(7):
            self.assertEqual(case['max_minutes'], 60)
            self.assertEqual(case['experiment']['common_inquiry_settings'],
                             {'k': 3, 'wait': 2, 'notify': True})
            self.assertLess(max(p['search_start_t'] for p in case['patients']), case['max_minutes'])

    def test_invalid_seed_and_family_selection_rejected(self):
        for seed in (-1, True, 1.5, '7', None):
            with self.subTest(seed=seed), self.assertRaises(ValueError):
                build_stable_c1_scenarios(seed)
        for families in ('fresh_control', b'fresh_control', True, [None],
                         ['missing'], ['fresh_control', 'fresh_control']):
            with self.subTest(families=families), self.assertRaises(ValueError):
                build_stable_c1_scenarios(7, families)


if __name__ == '__main__':
    unittest.main()
