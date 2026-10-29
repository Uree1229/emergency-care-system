"""Structural adversarial-case tests; no development or holdout replay runs."""
from copy import deepcopy
import math
import unittest

from simulation.joint_policy_scenarios import build_joint_policy_scenarios
from simulation.map_replay import RouteSnapshot, validate_scenario
from simulation.partial_information_scenarios import SCARCE
from simulation.scarce_delay_scenarios import (
    ADDITIONAL_FAMILIES, DEV_SEEDS, DEVELOPMENT_SEEDS, FAMILIES,
    ORIGINAL_FAMILIES, VALIDATION_SEEDS, build_scarce_delay_scenarios, make_scenario,
)


class ScarceDelayScenarioTests(unittest.TestCase):
    def case(self, family, seed=7):
        return make_scenario(seed, family)

    def test_manifest_and_disjoint_reserved_partitions(self):
        self.assertEqual(len(ORIGINAL_FAMILIES), 56)
        self.assertEqual(len(ADDITIONAL_FAMILIES), 11)
        self.assertEqual(len(FAMILIES), 67)
        self.assertEqual(len(set(FAMILIES)), 67)
        self.assertEqual(DEV_SEEDS, (33101, 33102, 33103))
        self.assertEqual(DEVELOPMENT_SEEDS, DEV_SEEDS)
        self.assertEqual(VALIDATION_SEEDS, tuple(range(34101, 34107)))
        self.assertFalse(set(DEV_SEEDS) & set(VALIDATION_SEEDS))

    def test_all_56_inherited_complete_inputs_are_unchanged(self):
        for seed in (7, 8):
            self.assertEqual(build_scarce_delay_scenarios(seed, ORIGINAL_FAMILIES),
                             build_joint_policy_scenarios(seed))

    def test_all_small_seed_cases_validate_and_have_no_extra_reports(self):
        for seed in (7, 8):
            cases = build_scarce_delay_scenarios(seed)
            self.assertEqual([case['family'] for case in cases], list(FAMILIES))
            for case in cases:
                validate_scenario(case)
                self.assertTrue(case['synthetic'])
                self.assertFalse(case['experiment']['additional_resource_change_channel_enabled'])
                self.assertEqual(case['experiment']['patient_count'], len(case['patients']))

    def test_new_cases_preserve_common_physics_and_fifo(self):
        for case in build_scarce_delay_scenarios(7, ADDITIONAL_FAMILIES):
            self.assertEqual(case['transfer_preparation_minutes'], 3)
            self.assertEqual(case['travel_error_range'], [1.0, 1.0])
            self.assertEqual(case['timeout_minutes'], 3)
            self.assertEqual(case['retry_minutes'], 3)
            self.assertEqual(case['inquiry_queue']['workers'], 1)
            self.assertTrue(case['inquiry_queue']['enabled'])
            self.assertEqual(case['experiment']['review_resolution_seconds'], 15)
            self.assertEqual(case['experiment']['timing_unit'], 'real_minutes')
            self.assertEqual(case['initial_inquiry_history'], [])

    def test_new_cases_use_only_environment_service_parameters(self):
        for case in build_scarce_delay_scenarios(7, ADDITIONAL_FAMILIES):
            self.assertEqual(case['response_environment'], {})
            hospitals = {h['id'] for h in case['hospitals']}
            for entries in case['response_matrix'].values():
                self.assertEqual(set(entries), hospitals)
                for entry in entries.values():
                    self.assertEqual(set(entry), {'delay', 'silent'})
                    self.assertIs(type(entry['silent']), bool)
                    self.assertGreater(entry['delay'], 0)
                    self.assertEqual(entry['delay'] * 4, round(entry['delay'] * 4))
            # Annotations about potential outcomes stay out of observed patient,
            # hospital capability/capacity, and received-report fields.
            for row in case['patients'] + case['hospitals']:
                self.assertFalse({'response_delay', 'silent', 'future_request',
                                  'first_inquiry_hospital', 'reserved_for'} & set(row))

    def test_silent_alternative_is_still_structurally_possible(self):
        case = self.case('scarce_alternative_silent')
        self.assertTrue(case['response_matrix']['*']['H02']['silent'])
        self.assertFalse(case['response_matrix']['*']['H01']['silent'])
        flexible = case['patients'][0]
        self.assertTrue(all(set(flexible['required_capabilities']) <= set(h['capabilities'])
                            for h in case['hospitals']))

    def test_late_alternative_finishes_after_original_timeout(self):
        case = self.case('scarce_alternative_late_deadline')
        background, = case['inquiry_queue']['background_jobs']
        self.assertEqual(background['hospital'], 'H02')
        self.assertEqual(background['t'], 0)
        self.assertGreater(background['service_minutes'] +
            case['response_matrix']['*']['H02']['delay'], case['timeout_minutes'])

    def test_unknown_is_not_zero_or_fabricated_capacity(self):
        case = self.case('scarce_alternative_unknown')
        reports = {r['hospital']: r for r in case['observation_config']['initial_reports']}
        self.assertIsNone(reports['H02']['resources']['ed_bed'])
        self.assertEqual(reports['H01']['resources']['ed_bed'], dict(capacity=1, used=0))
        self.assertEqual(case['hospitals'][1]['capacity']['ed_bed'], 1)
        self.assertEqual(case['observation_config']['feed_interval_minutes'], 0)
        self.assertTrue(case['observation_config']['confirmation_silent'])

    def test_stale_report_is_old_while_true_bed_remains_free(self):
        case = self.case('scarce_alternative_stale')
        report = case['observation_config']['initial_reports'][1]
        self.assertEqual(report['hospital'], 'H02')
        self.assertEqual(report['observed_at'], -11)
        self.assertEqual(report['resources']['ed_bed'], dict(capacity=1, used=0))
        self.assertEqual(case['hospitals'][1].get('initial_occupancy', []), [])
        self.assertTrue(case['observation_config']['confirmation_silent'])

    def test_fast_distant_reply_still_has_longer_real_route(self):
        for seed in (7, 8):
            case = self.case('scarce_distant_accepted_alternative', seed)
            routing = RouteSnapshot()
            travel = {h['id']: math.ceil(routing.route(case['patients'][0]['location'],
                (h['lat'], h['lon']))['duration_seconds'] / 60) for h in case['hospitals']}
            services = case['response_matrix']['*']
            self.assertLess(services['H02']['delay'], services['H01']['delay'])
            self.assertGreater(travel['H02'], travel['H01'])
            self.assertGreater(travel['H02'] + services['H02']['delay'],
                               travel['H01'] + services['H01']['delay'])
            self.assertTrue(case['experiment']['state_requires_raw_branch_verification'])

    def test_later_patient_is_not_a_time_zero_request(self):
        case = self.case('scarce_late_request')
        first, later = case['patients']
        for key in ('occur_t', 'dispatch_t', 'ambulance_arrive_t', 'search_start_t'):
            self.assertEqual(first[key], 0)
            self.assertEqual(later[key], 1)
        self.assertIn(SCARCE, later['required_capabilities'])

    def test_priority_reversal_makes_flexible_patient_more_urgent(self):
        case = self.case('scarce_priority_reversal')
        flexible, limited = case['patients']
        self.assertGreater(flexible['priority'], limited['priority'])
        self.assertNotIn(SCARCE, flexible['required_capabilities'])
        self.assertIn(SCARCE, limited['required_capabilities'])

    def test_single_candidate_case_has_no_possible_alternative(self):
        case = self.case('scarce_single_candidate_competition')
        self.assertEqual(len(case['hospitals']), 1)
        for patient in case['patients']:
            self.assertIn(SCARCE, patient['required_capabilities'])
            self.assertEqual(patient['resource_demands'], dict(ed_bed=1, special_team=1))
        self.assertEqual(case['hospitals'][0]['capacity']['ed_bed'], 1)

    def test_candidate_pressure_exceeds_episode_budget_without_changing_it(self):
        case = self.case('scarce_contact_concurrency_limits')
        self.assertEqual(len(case['hospitals']), 12)
        self.assertEqual(len({h['id'] for h in case['hospitals']}), 12)
        self.assertGreater(len(case['hospitals']), case['experiment']['episode_contact_limit'])
        self.assertTrue(all(case['response_matrix']['*'][h]['silent'] for h in
                            (f'H{index:02d}' for index in range(1, 12))))
        self.assertFalse(case['response_matrix']['*']['H12']['silent'])
        self.assertNotIn('max_new', case)
        self.assertNotIn('episode_contact_limit', case.get('inquiry_queue', {}))

    def test_expiry_is_original_lifetime_not_a_policy_extension(self):
        case = self.case('scarce_alternative_offer_expiry')
        self.assertEqual(case['accept_ttl_minutes'], 1)
        self.assertEqual(case['timeout_minutes'], 3)
        self.assertTrue(case['experiment']['state_requires_raw_branch_verification'])

    def test_reported_shortage_is_backed_by_real_initial_occupancy(self):
        case = self.case('scarce_reported_bed_shortage')
        near, far = case['hospitals']
        occupancy, = near['initial_occupancy']
        self.assertEqual(occupancy['until'], 4)
        self.assertEqual(occupancy['demand'], near['capacity'])
        self.assertEqual(far.get('initial_occupancy', []), [])
        self.assertNotIn('initial_reports', case['observation_config'])

    def test_requested_order_and_subsets_do_not_change_input_streams(self):
        reverse = list(reversed(FAMILIES))
        self.assertEqual(build_scarce_delay_scenarios(7, reverse),
                         [self.case(family) for family in reverse])
        self.assertEqual(build_scarce_delay_scenarios(7, []), [])

    def test_outputs_are_fully_detached(self):
        for family in ('fresh_control', 'scarce_alternative_unknown',
                       'scarce_contact_concurrency_limits'):
            original = self.case(family)
            modified = deepcopy(original)
            modified['patients'][0]['resource_demands']['ed_bed'] = 999
            modified['hospitals'][0]['capacity']['ed_bed'] = 999
            modified['observation_config']['feed_interval_minutes'] = 999
            if modified.get('response_matrix'):
                next(iter(modified['response_matrix'].values()))['H01']['delay'] = 999
            self.assertEqual(self.case(family), original)

    def test_small_seed_changes_potential_environment_reproducibly(self):
        left = self.case('scarce_distant_accepted_alternative', 7)
        right = self.case('scarce_distant_accepted_alternative', 8)
        self.assertNotEqual(left['hospitals'][1]['lat'], right['hospitals'][1]['lat'])
        self.assertEqual(left, self.case('scarce_distant_accepted_alternative', 7))

    def test_invalid_seed_and_family_inputs_are_rejected(self):
        for seed in (True, False, -1, 7.0, '7', None):
            with self.subTest(seed=seed), self.assertRaises(ValueError):
                build_scarce_delay_scenarios(seed)
        for families in ('fresh_control', b'fresh_control', 7, [None], ['unknown'],
                         ['fresh_control', 'fresh_control']):
            with self.subTest(families=families), self.assertRaises(ValueError):
                build_scarce_delay_scenarios(7, families)
        for family in (None, 7, 'unknown'):
            with self.subTest(family=family), self.assertRaises(ValueError):
                make_scenario(7, family)


if __name__ == '__main__':
    unittest.main()
