"""Structural boundary tests only; no reserved development/validation replays."""
from copy import deepcopy
import unittest

from simulation.deadline_recovery_scenarios import (
    ADDITIONAL_FAMILIES, DEVELOPMENT_SEEDS, DEV_SEEDS, FAMILIES,
    ORIGINAL_FAMILIES, VALIDATION_SEEDS,
    build_deadline_recovery_scenarios, make_scenario,
)
from simulation.joint_policy_scenarios import _validate_extra_inputs
from simulation.map_replay import RouteSnapshot, validate_scenario
from simulation.scarce_delay_scenarios import (
    build_scarce_delay_scenarios,
    make_scenario as make_original,
)


class DeadlineRecoveryScenarioTests(unittest.TestCase):
    def case(self, family, seed=7):
        return make_scenario(seed, family)

    def test_manifest_and_new_reserved_seed_partitions(self):
        self.assertEqual(len(ORIGINAL_FAMILIES), 67)
        self.assertEqual(len(ADDITIONAL_FAMILIES), 5)
        self.assertEqual(len(FAMILIES), 72)
        self.assertEqual(len(set(FAMILIES)), 72)
        self.assertEqual(DEV_SEEDS, (35101, 35102, 35103))
        self.assertEqual(DEVELOPMENT_SEEDS, DEV_SEEDS)
        self.assertEqual(VALIDATION_SEEDS, tuple(range(36101, 36107)))
        self.assertFalse(set(DEVELOPMENT_SEEDS) & set(VALIDATION_SEEDS))

    def test_complete_inherited_inputs_are_value_for_value_unchanged(self):
        for seed in (7, 8):
            self.assertEqual(build_deadline_recovery_scenarios(seed, ORIGINAL_FAMILIES),
                             build_scarce_delay_scenarios(seed))

    def test_all_small_seed_environments_validate(self):
        for seed in (7, 8):
            cases = build_deadline_recovery_scenarios(seed)
            self.assertEqual([c['family'] for c in cases], list(FAMILIES))
            for case in cases:
                validate_scenario(case)
                if 'response_matrix' in case:
                    _validate_extra_inputs(case)
                self.assertTrue(case['synthetic'])
                self.assertFalse(case['experiment']['additional_resource_change_channel_enabled'])
                self.assertEqual(case['experiment']['patient_count'], len(case['patients']))

    def test_original_325_boundary_is_inherited_not_replaced(self):
        case = self.case('scarce_alternative_late_deadline')
        self.assertEqual(case, make_original(7, 'scarce_alternative_late_deadline'))
        self.assertEqual(case['inquiry_queue']['background_jobs'][0]['service_minutes'] +
                         case['response_matrix']['*']['H02']['delay'], 3.25)

    def test_new_cases_keep_original_timeout_retry_fifo_and_travel_physics(self):
        for seed in (7, 8):
            original = make_original(seed, 'scarce_alternative_late_deadline')
            for case in build_deadline_recovery_scenarios(seed, ADDITIONAL_FAMILIES):
                self.assertEqual(case['timeout_minutes'], 3)
                self.assertEqual(case['retry_minutes'], 3)
                self.assertEqual(case['accept_ttl_minutes'], 10)
                self.assertEqual(case['transfer_preparation_minutes'], 3)
                self.assertEqual(case['travel_error_range'], [1.0, 1.0])
                self.assertEqual(case['inquiry_queue'], original['inquiry_queue'])
                self.assertEqual(case['observation_config'], original['observation_config'])
                self.assertEqual(case['resource_update_config'], original['resource_update_config'])
                self.assertEqual(case['patients'][:2], original['patients'])
                self.assertEqual([(h['id'], h['lat'], h['lon'], h['capacity']) for h in case['hospitals']],
                                 [(h['id'], h['lat'], h['lon'], h['capacity']) for h in original['hospitals']])
                self.assertEqual(case['experiment']['bounded_late_receipt_window_minutes'], .25)

    def test_half_minute_late_work_is_outside_quarter_minute_window(self):
        case = self.case('deadline_reply_at_350')
        total = case['inquiry_queue']['background_jobs'][0]['service_minutes'] + \
                case['response_matrix']['*']['H02']['delay']
        self.assertEqual(total, 3.5)
        self.assertGreater(total, case['timeout_minutes'] + .25)
        self.assertFalse(case['response_matrix']['*']['H02']['silent'])

    def test_three_quarter_minute_late_work_is_outside_window(self):
        case = self.case('deadline_reply_at_375')
        total = case['inquiry_queue']['background_jobs'][0]['service_minutes'] + \
                case['response_matrix']['*']['H02']['delay']
        self.assertEqual(total, 3.75)
        self.assertGreater(total, case['timeout_minutes'] + .25)

    def test_silent_completion_is_not_a_received_proposal(self):
        case = self.case('deadline_silent_alternative')
        self.assertTrue(case['response_matrix']['*']['H02']['silent'])
        self.assertFalse(case['response_matrix']['*']['H01']['silent'])
        self.assertEqual(case['response_matrix']['*']['H02']['delay'], .25)
        self.assertEqual(case['inquiry_queue']['background_jobs'],
                         [dict(t=0, hospital='H02', service_minutes=3)])

    def test_shortage_precedes_actual_periodic_report_and_late_completion(self):
        case = self.case('deadline_capacity_lost_before_receipt')
        event, = case['exogenous_events']
        self.assertEqual(event, dict(type='resource_unavailable', t=2,
                                     until=8, hospital='H02', resource='ed_bed'))
        self.assertLess(event['t'], 3)
        self.assertGreater(event['until'], 3.25)
        self.assertEqual(case['observation_config']['feed_interval_minutes'], 1)
        self.assertEqual(case['observation_config']['feed_delay_minutes'], 0)
        self.assertEqual(case['resource_update_config'],
                         make_original(7, 'scarce_alternative_late_deadline')['resource_update_config'])

    def test_later_patient_is_actually_registered_at_three_and_only_h02_compatible(self):
        case = self.case('deadline_later_single_candidate')
        first, limited, later = case['patients']
        self.assertEqual(later['id'], 'P03')
        for field in ('occur_t', 'dispatch_t', 'ambulance_arrive_t', 'search_start_t'):
            self.assertEqual(first[field], 0)
            self.assertEqual(limited[field], 0)
            self.assertEqual(later[field], 3)
        compatible = [h['id'] for h in case['hospitals']
                      if set(later['required_capabilities']) <= set(h['capabilities'])]
        self.assertEqual(compatible, ['H02'])
        self.assertEqual(later['priority'], first['priority'])
        self.assertEqual(later['resource_demands'], dict(ed_bed=1))

    def test_no_new_environment_fields_silently_encode_condition_changes_or_receipt_lag(self):
        unsupported = {'condition_changes', 'patient_updates', 'patient_condition_changes',
                       'receipt_delay', 'reply_receipt_delay', 'delivery_delay', 'late_reply_at',
                       'accepted_offer_extension', 'timeout_extension'}
        for case in build_deadline_recovery_scenarios(7, ADDITIONAL_FAMILIES):
            self.assertFalse(unsupported & set(case))
            for row in case['patients'] + case['hospitals']:
                self.assertFalse(unsupported & set(row))
            for entries in case['response_matrix'].values():
                for entry in entries.values():
                    self.assertEqual(set(entry), {'delay', 'silent'})
                    self.assertGreater(entry['delay'], 0)
                    self.assertEqual(entry['delay'] * 4, round(entry['delay'] * 4))

    def test_candidate_routes_stay_real_and_do_not_use_late_annotations(self):
        routing = RouteSnapshot()
        for case in build_deadline_recovery_scenarios(7, ADDITIONAL_FAMILIES):
            for h in case['hospitals']:
                route = routing.route(case['patients'][0]['location'], (h['lat'], h['lon']))
                self.assertGreater(route['duration_seconds'], 0)
                self.assertNotIn('late_reply', route)

    def test_requested_order_and_empty_subset_are_deterministic(self):
        reverse = list(reversed(FAMILIES))
        self.assertEqual(build_deadline_recovery_scenarios(7, reverse),
                         [self.case(family) for family in reverse])
        self.assertEqual(build_deadline_recovery_scenarios(7, []), [])

    def test_returned_inputs_are_detached_from_inherited_and_future_calls(self):
        for family in ('fresh_control', 'scarce_alternative_late_deadline',
                       'deadline_capacity_lost_before_receipt', 'deadline_later_single_candidate'):
            original = self.case(family)
            modified = deepcopy(original)
            modified['patients'][0]['resource_demands']['ed_bed'] = 999
            modified['hospitals'][0]['capacity']['ed_bed'] = 999
            modified['observation_config']['feed_interval_minutes'] = 999
            if modified.get('response_matrix'):
                modified['response_matrix']['*']['H01']['delay'] = 999
            self.assertEqual(self.case(family), original)

    def test_small_seed_changes_only_supported_environment_streams_reproducibly(self):
        for family in ADDITIONAL_FAMILIES:
            left, right = self.case(family, 7), self.case(family, 8)
            self.assertNotEqual(left, right)
            self.assertEqual(left, self.case(family, 7))

    def test_invalid_seeds_and_family_selections_are_rejected(self):
        for seed in (True, False, -1, 7.0, '7', None):
            with self.subTest(seed=seed), self.assertRaises(ValueError):
                build_deadline_recovery_scenarios(seed)
        for families in ('fresh_control', b'fresh_control', 7, [None], ['unknown'],
                         ['fresh_control', 'fresh_control']):
            with self.subTest(families=families), self.assertRaises(ValueError):
                build_deadline_recovery_scenarios(7, families)
        for family in (None, 7, 'unknown'):
            with self.subTest(family=family), self.assertRaises(ValueError):
                make_scenario(7, family)


if __name__ == '__main__':
    unittest.main()
