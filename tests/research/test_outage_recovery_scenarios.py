"""Structural scenarios only; no reserved development or validation run."""
from copy import deepcopy
import math
import unittest

from simulation.existing_info_scenarios import build_existing_info_scenarios
from simulation.map_replay import RouteSnapshot, validate_scenario
from simulation.outage_recovery_scenarios import (
    ADVERSARIAL_FAMILIES, DEVELOPMENT_SEEDS, VALIDATION_SEEDS, FAMILIES,
    ORIGINAL_FAMILIES, build_outage_recovery_scenarios,
)


class OutageRecoveryScenarioTests(unittest.TestCase):
    def case(self, family, seed=7):
        return build_outage_recovery_scenarios(seed, [family])[0]

    def test_manifest_and_reserved_partitions(self):
        self.assertEqual(len(ORIGINAL_FAMILIES), 39)
        self.assertEqual(len(ADVERSARIAL_FAMILIES), 9)
        self.assertEqual(len(FAMILIES), 48)
        self.assertEqual(len(set(FAMILIES)), 48)
        self.assertEqual(DEVELOPMENT_SEEDS, (29101, 29102, 29103))
        self.assertEqual(VALIDATION_SEEDS, tuple(range(30101, 30107)))
        self.assertFalse(set(DEVELOPMENT_SEEDS) & set(VALIDATION_SEEDS))

    def test_all_cases_valid_and_additional_reports_disabled(self):
        cases = build_outage_recovery_scenarios(7)
        self.assertEqual([c['family'] for c in cases], list(FAMILIES))
        for case in cases:
            validate_scenario(case)
            self.assertTrue(case['synthetic'])
            self.assertFalse(case['experiment']['additional_resource_change_channel_enabled'])
            self.assertEqual(case['experiment']['patient_count'], len(case['patients']))

    def test_all_inherited_truth_reporting_and_timing_remain_exact(self):
        before = build_existing_info_scenarios(7)
        after = build_outage_recovery_scenarios(7, ORIGINAL_FAMILIES)
        descriptive = {'id', 'assumptions', 'experiment', 'outage_recovery_family'}
        for original, new in zip(before, after):
            self.assertEqual({k: v for k, v in original.items() if k not in descriptive},
                             {k: v for k, v in new.items() if k not in descriptive})

    def test_requested_order_has_no_random_stream_coupling(self):
        selected = list(reversed(ADVERSARIAL_FAMILIES))
        separate = [self.case(family) for family in selected]
        self.assertEqual(build_outage_recovery_scenarios(7, selected), separate)

    def test_mutation_does_not_contaminate_subsequent_calls(self):
        original = self.case('notify_before_backup_reply')
        dirty = deepcopy(original)
        dirty['patients'][0]['resource_demands']['ed_bed'] = 99
        dirty['hospitals'][0]['capacity']['ed_bed'] = 99
        dirty['observation_config']['feed_interval_minutes'] = 99
        dirty['exogenous_events'][0]['until'] = 99
        self.assertEqual(self.case('notify_before_backup_reply'), original)

    def test_second_small_seed_supports_all_new_cases(self):
        cases = build_outage_recovery_scenarios(8, ADVERSARIAL_FAMILIES)
        self.assertEqual(len(cases), 9)
        for case in cases:
            validate_scenario(case)
            self.assertEqual(case['seed'], 8)

    def test_new_cases_keep_three_minute_transfer_preparation(self):
        for case in build_outage_recovery_scenarios(7, ADVERSARIAL_FAMILIES):
            self.assertEqual(case['transfer_preparation_minutes'], 3)
            self.assertEqual(case['experiment']['transfer_preparation_minutes'], 3)

    def test_original_backup_reply_is_later_than_received_alert(self):
        case = self.case('notify_before_backup_reply')
        event, = case['exogenous_events']
        self.assertEqual(event['t'], 2)
        self.assertEqual(case['response_environment']['*']['FAR']['delay'], 5)
        self.assertLess(event['t'], case['response_environment']['*']['FAR']['delay'])

    def test_backup_reply_precedes_alert_and_is_still_valid(self):
        case = self.case('notify_after_backup_reply')
        reply = case['response_environment']['*']['FAR']['delay']
        event, = case['exogenous_events']
        self.assertEqual(reply, 2)
        self.assertEqual(event['t'], 3)
        self.assertLess(event['t'], reply + case.get('accept_ttl_minutes', 10))

    def test_expired_backup_cannot_be_used_when_alert_arrives(self):
        case = self.case('expired_backup_after_notify')
        reply = case['response_environment']['*']['FAR']['delay']
        event, = case['exogenous_events']
        self.assertEqual(reply + case['accept_ttl_minutes'], 3)
        self.assertGreater(event['t'], reply + case['accept_ttl_minutes'])
        near = case['hospitals'][0]
        travel = math.ceil(RouteSnapshot().route(case['patients'][0]['location'],
                           (near['lat'], near['lon']))['duration_seconds'] / 60)
        self.assertGreater(1 + travel, event['t'])

    def test_silent_backup_preserves_original_timeout(self):
        case = self.case('silent_backup_timeout')
        self.assertTrue(case['response_environment']['*']['FAR']['silent'])
        self.assertEqual(case['timeout_minutes'], 4)
        self.assertIsNone(case['experiment']['original_far_reply_minute'])
        self.assertEqual(case['experiment']['original_far_timeout_minute'], 4)

    def test_late_original_hospital_reply_remains_after_timeout(self):
        case = self.case('late_backup_timeout')
        self.assertTrue(case['inquiry_queue']['enabled'])
        self.assertEqual(case['inquiry_queue']['workers'], 1)
        background, = case['inquiry_queue']['background_jobs']
        self.assertEqual(background, dict(t=0, hospital='FAR', service_minutes=4))
        far_service = case['response_environment']['*']['FAR']['delay']
        self.assertEqual(background['service_minutes'] + far_service, 7)
        self.assertGreater(7, case['timeout_minutes'])

    def test_two_affected_patients_compete_for_one_backup_bed(self):
        case = self.case('competing_backup_capacity')
        self.assertEqual(len(case['patients']), 2)
        self.assertEqual(case['hospitals'][0]['capacity']['ed_bed'], 2)
        self.assertEqual(case['hospitals'][1]['capacity']['ed_bed'], 1)
        self.assertEqual(len(case['exogenous_events']), 2)
        self.assertTrue(all(e['t'] == 2 and e['hospital'] == 'NEAR'
                            and e['resource'] == 'ed_bed' for e in case['exogenous_events']))
        self.assertEqual(sum(p['resource_demands']['ed_bed'] for p in case['patients']), 2)

    def test_quick_restore_is_before_original_near_arrival_for_both_small_seeds(self):
        for seed in (7, 8):
            case = self.case('restore_before_failed_arrival', seed)
            event, = case['exogenous_events']
            near = case['hospitals'][0]
            travel = math.ceil(RouteSnapshot().route(case['patients'][0]['location'],
                               (near['lat'], near['lon']))['duration_seconds'] / 60)
            self.assertEqual(event['until'], 3)
            self.assertLess(event['until'], 1 + travel)

    def test_backup_resources_have_stale_and_missing_fields_without_feed_backdoor(self):
        case = self.case('unverified_backup_resources')
        observation = case['observation_config']
        self.assertEqual(observation['feed_interval_minutes'], 0)
        far = next(r for r in observation['initial_reports'] if r['hospital'] == 'FAR')
        self.assertEqual(far['observed_at'], -11)
        self.assertIsNotNone(far['resources']['ed_bed'])
        self.assertIsNone(far['resources']['special_team'])
        self.assertEqual(set(observation['confirmation_missing_resources']),
                         {'ed_bed', 'special_team'})
        patient = case['patients'][0]
        self.assertEqual(patient['resource_demands'], {'ed_bed': 1, 'special_team': 1})
        self.assertTrue(set(patient['required_capabilities']) <=
                        set(case['hospitals'][1]['capabilities']))

    def test_boundary_outage_matches_early_arrival_for_both_small_seeds(self):
        for seed in (7, 8):
            case = self.case('outage_at_arrival_boundary', seed)
            event, = case['exogenous_events']
            near = case['hospitals'][0]
            travel = math.ceil(RouteSnapshot().route(case['patients'][0]['location'],
                               (near['lat'], near['lon']))['duration_seconds'] / 60)
            self.assertEqual(event['t'], 1 + travel)

    def test_invalid_seeds_rejected(self):
        for seed in (True, False, -1, 7.0, '7', None):
            with self.subTest(seed=seed), self.assertRaises(ValueError):
                build_outage_recovery_scenarios(seed)

    def test_invalid_families_rejected(self):
        for selected in ('fresh_control', b'fresh_control', 7, [None], ['unknown'],
                         ['fresh_control', 'fresh_control']):
            with self.subTest(families=selected), self.assertRaises(ValueError):
                build_outage_recovery_scenarios(7, selected)

    def test_empty_selection_supported(self):
        self.assertEqual(build_outage_recovery_scenarios(7, []), [])


if __name__ == '__main__':
    unittest.main()
