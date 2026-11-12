"""Structural environments only; sweep holdout seeds are not executed here."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from simulation.map_replay import validate_scenario
from simulation.stable_c1_scenarios import build_stable_c1_scenarios
from simulation.total_wait_scenarios import (
    ADVERSARIAL_FAMILIES, BASIC, DEVELOPMENT_SEEDS, FAMILIES, ORIGINAL_FAMILIES,
    VALIDATION_SEEDS, build_total_wait_scenarios,
)


ENVIRONMENT_FIELDS = (
    'hospitals', 'patients', 'exogenous_events', 'response_environment',
    'observation_config', 'max_minutes', 'timeout_minutes', 'retry_minutes',
    'accept_ttl_minutes', 'nonresponse', 'transfer_preparation_minutes',
)


def one(family, seed=7):
    return build_total_wait_scenarios(seed, [family])[0]


class TotalWaitScenarioTests(unittest.TestCase):
    def test_manifest_retains_twenty_six_previous_and_adds_five_total_wait_cases(self):
        self.assertEqual(len(ORIGINAL_FAMILIES), 26)
        self.assertEqual(len(ADVERSARIAL_FAMILIES), 5)
        self.assertEqual(FAMILIES, ORIGINAL_FAMILIES + ADVERSARIAL_FAMILIES)
        self.assertEqual(len(set(FAMILIES)), 31)
        cases = build_total_wait_scenarios(7)
        self.assertEqual([case['family'] for case in cases], list(FAMILIES))
        for case in cases:
            validate_scenario(case)
            self.assertEqual(case['id'], f"total-wait-{case['family']}-7")
            self.assertEqual(case['total_wait_family'], case['family'])
            self.assertEqual(case['experiment']['patient_count'], len(case['patients']))

    def test_reserved_seeds_disjoint_from_all_prior_sweeps_and_structural_seeds(self):
        self.assertEqual(DEVELOPMENT_SEEDS, (25101, 25102, 25103))
        self.assertEqual(VALIDATION_SEEDS, tuple(range(26101, 26107)))
        previous = set()
        for start, count in ((17101, 3), (18101, 6), (19101, 3), (20101, 6),
                             (21101, 3), (22101, 6), (23101, 3), (24101, 6)):
            previous.update(range(start, start + count))
        reserved = set(DEVELOPMENT_SEEDS + VALIDATION_SEEDS)
        self.assertFalse(previous & reserved)
        self.assertFalse(set(DEVELOPMENT_SEEDS) & set(VALIDATION_SEEDS))
        self.assertTrue({7, 8}.isdisjoint(reserved))

    def test_previous_families_keep_all_frozen_environment_fields(self):
        originals = {c['family']: c for c in build_stable_c1_scenarios(7)}
        for case in build_total_wait_scenarios(7, ORIGINAL_FAMILIES):
            for field in ENVIRONMENT_FIELDS:
                self.assertEqual(case[field], originals[case['family']][field],
                                 (case['family'], field))

    def test_subsets_keep_order_and_family_randomness(self):
        cases = {c['family']: c for c in build_total_wait_scenarios(7)}
        selected = ['release_notification_delay', 'no_alternative_hospital',
                    'intrinsic_single_hospital_queue', 'fresh_control']
        before = selected[:]
        subset = build_total_wait_scenarios(7, selected)
        self.assertEqual(before, selected)
        self.assertEqual(subset, [cases[f] for f in selected])
        self.assertEqual(build_total_wait_scenarios(7, iter(selected)), subset)
        self.assertEqual(build_total_wait_scenarios(7, []), [])

    def test_builds_and_families_are_deeply_detached(self):
        cases = build_total_wait_scenarios(7)
        reference = deepcopy(cases)
        target = next(c for c in cases if c['family'] == 'release_long_retry')
        target['hospitals'][0]['initial_occupancy'][0]['until'] = 999
        target['resource_update_config']['missing_resources'].append('ed_bed')
        target['patients'][0]['priority'] = 1
        for index, case in enumerate(cases):
            if case['family'] != target['family']:
                self.assertEqual(case, reference[index])
        self.assertEqual(build_total_wait_scenarios(7), reference)

    def test_long_retry_separates_past_report_and_current_initial_occupancy(self):
        case = one('release_long_retry')
        self.assertEqual(len(case['patients']), 1)
        self.assertEqual([h['id'] for h in case['hospitals']], ['NEAR'])
        self.assertEqual(case['hospitals'][0]['initial_occupancy'],
                         [{'until': 6, 'demand': {'ed_bed': 1}}])
        self.assertEqual(case['retry_minutes'], 12)
        report = case['observation_config']['initial_reports'][0]
        self.assertEqual(report['observed_at'], -1)
        self.assertEqual(report['resources']['ed_bed'], {'capacity': 1, 'used': 0})
        self.assertEqual(case['resource_update_config'],
                         {'delay_minutes': 0, 'missing_resources': [], 'silent': False})

    def test_delayed_notice_cannot_be_delivered_before_actual_release(self):
        case = one('release_notification_delay')
        self.assertEqual(case['resource_update_config']['delay_minutes'], 3)
        self.assertEqual(case['experiment']['physical_release_minute'], 6)
        self.assertEqual(case['experiment']['earliest_release_notification_minute'], 9)
        self.assertEqual(case['observation_config']['feed_interval_minutes'], 0)

    def test_live_offer_case_has_both_negative_change_and_release_before_expiry(self):
        case = one('release_before_offer_expiry')
        self.assertEqual(case['exogenous_events'], [dict(
            type='resource_unavailable', t=2, until=4,
            hospital='NEAR', resource='ed_bed')])
        replies = case['response_environment']['*']
        self.assertEqual(replies['NEAR']['delay'], 1)
        self.assertEqual(replies['FAR']['delay'], 5)
        self.assertEqual(case['accept_ttl_minutes'], 5)
        self.assertLess(case['experiment']['physical_release_minute'],
                        case['experiment']['first_near_offer_expiry_minute'])
        self.assertEqual(case['observation_config']['feed_interval_minutes'], 0)

    def test_periodic_report_control_separates_same_information_from_retry_review(self):
        case = one('periodic_release_long_retry')
        self.assertEqual(case['observation_config']['feed_interval_minutes'], 1)
        self.assertEqual(case['observation_config']['feed_delay_minutes'], 2)
        self.assertEqual(case['retry_minutes'], 12)
        self.assertEqual(case['hospitals'][0]['initial_occupancy'],
                         [{'until': 6, 'demand': {'ed_bed': 1}}])
        self.assertEqual(case['observation_config']['initial_reports'][0]['observed_at'], -1)
        self.assertEqual(case['experiment']['first_full_report_delivery_minute'], 2)
        self.assertEqual(case['experiment']['first_post_release_feed_delivery_minute'], 8)
        self.assertEqual(case['resource_update_config']['delay_minutes'], 0)

    def test_intrinsic_queue_has_one_bed_and_no_alternative_or_missing_counts(self):
        case = one('intrinsic_single_hospital_queue')
        self.assertEqual([h['id'] for h in case['hospitals']], ['NEAR'])
        self.assertEqual(case['hospitals'][0]['capacity']['ed_bed'], 1)
        self.assertEqual(len(case['patients']), 3)
        self.assertEqual({p['priority'] for p in case['patients']}, {4})
        self.assertTrue(all(p['required_capabilities'] == [BASIC] and
                            p['resource_demands'] == {'ed_bed': 1}
                            for p in case['patients']))
        self.assertGreaterEqual(min(p['treatment_minutes'] for p in case['patients']), 24)
        self.assertEqual(case['observation_config']['feed_interval_minutes'], 1)
        self.assertEqual(case['exogenous_events'], [])
        self.assertEqual(case['max_minutes'], 120)
        self.assertEqual(one('fresh_control')['max_minutes'], 60)

    def test_notification_contract_preserves_persistent_masks_and_silence(self):
        for family in ('partial_team_unresolved', 'persistent_partial_slow'):
            with self.subTest(family=family):
                case = one(family)
                self.assertIn('special_team', case['resource_update_config']['missing_resources'])
        self.assertTrue(one('missing_unresolved')['resource_update_config']['silent'])
        self.assertFalse(one('missing_recheck')['resource_update_config']['silent'])
        for case in build_total_wait_scenarios(7):
            observation = case['observation_config']
            expected = sorted(set(observation.get('feed_missing_resources', [])) |
                              set(observation.get('confirmation_missing_resources', [])))
            self.assertEqual(case['resource_update_config']['missing_resources'], expected)
            self.assertEqual(case['resource_update_config']['silent'],
                             observation.get('confirmation_silent', False))

    def test_existing_adverse_outage_and_future_patient_controls_are_preserved(self):
        cases = {c['family']: c for c in build_total_wait_scenarios(7)}
        self.assertEqual(cases['watch_outage_recovery']['exogenous_events'][0], dict(
            type='resource_unavailable', t=1, until=8,
            hospital='NEAR', resource='special_team'))
        future = cases['future_patient_not_yet_active']
        p03 = next(p for p in future['patients'] if p['id'] == 'P03')
        self.assertEqual(p03['search_start_t'], 12)
        self.assertEqual(future['experiment']['future_patient_start_minute'], 12)

    def test_only_environment_contract_not_policy_flags_is_added(self):
        for case in build_total_wait_scenarios(7):
            self.assertFalse({'resource_updates', 'event_review', 'total_wait_config',
                              'forecast_release_at', 'future_release_hint'} & set(case))
            self.assertEqual(set(case['resource_update_config']),
                             {'delay_minutes', 'missing_resources', 'silent'})

    def test_seed_variation_changes_treatment_or_non_tied_geometry(self):
        a, b = one('release_before_offer_expiry', 7), one('release_before_offer_expiry', 8)
        self.assertNotEqual((a['hospitals'][1]['lat'], a['patients'][0]['treatment_minutes']),
                            (b['hospitals'][1]['lat'], b['patients'][0]['treatment_minutes']))

    def test_fully_synthetic_construction_never_touches_network_or_database(self):
        with patch('sqlite3.connect', side_effect=AssertionError('no live database')), \
                patch('socket.create_connection', side_effect=AssertionError('no network')):
            cases = build_total_wait_scenarios(7)
        for case in cases:
            self.assertTrue(case['synthetic'])
            self.assertTrue(all(h['identity_source'] == 'synthetic' for h in case['hospitals']))
            self.assertTrue(all(p['clinical_data_source'] == 'synthetic' for p in case['patients']))
            encoded = json.dumps(case, allow_nan=False)
            self.assertNotIn('http://', encoded)
            self.assertNotIn('https://', encoded)
            self.assertNotIn('serviceKey', encoded)

    def test_invalid_seeds_and_families_are_rejected(self):
        for seed in (-1, True, 1.5, '7', None):
            with self.subTest(seed=seed), self.assertRaises(ValueError):
                build_total_wait_scenarios(seed)
        for families in ('fresh_control', b'fresh_control', True, [None],
                         ['missing'], ['fresh_control', 'fresh_control']):
            with self.subTest(families=families), self.assertRaises(ValueError):
                build_total_wait_scenarios(7, families)


if __name__ == '__main__':
    unittest.main()
