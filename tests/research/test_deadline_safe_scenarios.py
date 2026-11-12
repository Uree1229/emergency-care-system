"""Small-seed structural tests; reserved DEV/VAL inputs are never executed."""
from copy import deepcopy
import math
import unittest

from simulation.deadline_recovery_scenarios import (
    build_deadline_recovery_scenarios,
    make_scenario as make_previous,
)
from simulation.deadline_safe_scenarios import (
    ADDITIONAL_FAMILIES, DEVELOPMENT_SEEDS, DEV_SEEDS, FAMILIES,
    ORIGINAL_FAMILIES, VALIDATION_SEEDS,
    build_deadline_safe_scenarios, make_scenario,
)
from simulation.joint_policy_scenarios import _validate_extra_inputs
from simulation.map_replay import RouteSnapshot, validate_scenario


class DeadlineSafeScenarioTests(unittest.TestCase):
    family = 'deadline_multiple_pending_boundary'

    def case(self, seed=7):
        return make_scenario(seed, self.family)

    def test_manifest_and_fresh_disjoint_seed_partitions(self):
        self.assertEqual(len(ORIGINAL_FAMILIES), 72)
        self.assertEqual(ADDITIONAL_FAMILIES, (self.family,))
        self.assertEqual(len(FAMILIES), 73)
        self.assertEqual(len(set(FAMILIES)), 73)
        self.assertEqual(DEV_SEEDS, (37101, 37102, 37103))
        self.assertEqual(DEVELOPMENT_SEEDS, DEV_SEEDS)
        self.assertEqual(VALIDATION_SEEDS, tuple(range(38101, 38107)))
        self.assertFalse(set(DEVELOPMENT_SEEDS) & set(VALIDATION_SEEDS))

    def test_complete_previous_72_inputs_are_unchanged(self):
        for seed in (7, 8):
            self.assertEqual(build_deadline_safe_scenarios(seed, ORIGINAL_FAMILIES),
                             build_deadline_recovery_scenarios(seed))

    def test_all_small_seed_cases_validate(self):
        for seed in (7, 8):
            cases = build_deadline_safe_scenarios(seed)
            self.assertEqual([case['family'] for case in cases], list(FAMILIES))
            for case in cases:
                validate_scenario(case)
                if 'response_matrix' in case:
                    _validate_extra_inputs(case)
                self.assertTrue(case['synthetic'])
                self.assertFalse(case['experiment']['additional_resource_change_channel_enabled'])
                self.assertEqual(case['experiment']['patient_count'], len(case['patients']))

    def test_original_patient_inputs_and_treatment_rng_are_preserved(self):
        for seed in (7, 8):
            self.assertEqual(self.case(seed)['patients'],
                             make_previous(seed, 'scarce_alternative_late_deadline')['patients'])
        self.assertEqual([p['treatment_minutes'] for p in self.case()['patients']], [13, 17])

    def test_all_route_geometry_is_fixed_across_small_seeds(self):
        points = [(h['id'], h['lat'], h['lon']) for h in self.case()['hospitals']]
        self.assertEqual(points, [('H01', 35.18, 126.85), ('H02', 35.189481, 126.85),
                                  ('H03', 35.184, 126.85), ('H04', 35.175, 126.85)])
        self.assertEqual(points, [(h['id'], h['lat'], h['lon']) for h in self.case(8)['hospitals']])

    def test_fixed_real_route_durations_reproduce_three_five_four_two(self):
        routing = RouteSnapshot()
        for seed in (7, 8):
            case = self.case(seed)
            durations = {h['id']: math.ceil(routing.route(case['patients'][0]['location'],
                (h['lat'], h['lon']))['duration_seconds'] / 60) for h in case['hospitals']}
            self.assertEqual(durations, dict(H01=3, H02=5, H03=4, H04=2))

    def test_added_hospitals_really_occupy_ed_bed_until_three(self):
        case = self.case()
        for h in case['hospitals'][2:]:
            self.assertEqual(h['initial_occupancy'], [dict(until=3, demand=dict(ed_bed=1))])
            self.assertEqual(h['capacity'], dict(ed_bed=1, special_team=1))
            self.assertEqual(h['capabilities'], case['hospitals'][1]['capabilities'])
        self.assertEqual(case['exogenous_events'], [])

    def test_pending_response_environment_keeps_exact_quarter_and_two_half_work(self):
        for seed in (7, 8):
            case = self.case(seed)
            self.assertEqual(case['response_matrix'], {'*': {
                'H01': dict(delay=.25, silent=False), 'H02': dict(delay=.25, silent=False),
                'H03': dict(delay=.25, silent=False), 'H04': dict(delay=2.5, silent=False)}})
            self.assertEqual(case['inquiry_queue'], dict(enabled=True, workers=1,
                background_jobs=[dict(t=0, hospital='H02', service_minutes=3)]))

    def test_original_deadline_and_three_minute_ttl_are_distinct(self):
        case = self.case()
        self.assertEqual(case['timeout_minutes'], 3)
        self.assertEqual(case['accept_ttl_minutes'], 3)
        self.assertEqual(case['retry_minutes'], 3)
        self.assertEqual(case['transfer_preparation_minutes'], 3)
        self.assertEqual(case['travel_error_range'], [1.0, 1.0])
        self.assertEqual(case['max_minutes'], 60)

    def test_current_observation_contract_has_no_additional_channel(self):
        case = self.case()
        previous = make_previous(7, 'scarce_alternative_late_deadline')
        self.assertEqual(case['observation_config'], previous['observation_config'])
        self.assertEqual(case['resource_update_config'], previous['resource_update_config'])
        self.assertEqual(case['initial_inquiry_history'], previous['initial_inquiry_history'])
        self.assertFalse(case['experiment']['additional_resource_change_channel_enabled'])

    def test_environment_future_work_does_not_enter_observed_patient_or_hospital_fields(self):
        case = self.case()
        unsupported = {'predicted_response_at', 'late_reply_at', 'actual_pending_at_325',
                       'reserved_for', 'response_delay', 'recovery_deadline', 'wait_until_reply'}
        for row in case['patients'] + case['hospitals']:
            self.assertFalse(unsupported & set(row))
        self.assertNotIn('deadline_safe_family', case['patients'][0])
        self.assertTrue(case['experiment']['late_receipt_state_requires_raw_branch_verification'])

    def test_requested_order_and_empty_subset_are_deterministic(self):
        reverse = list(reversed(FAMILIES))
        self.assertEqual(build_deadline_safe_scenarios(7, reverse),
                         [make_scenario(7, family) for family in reverse])
        self.assertEqual(build_deadline_safe_scenarios(7, []), [])

    def test_returned_inputs_and_inherited_inputs_are_detached(self):
        for family in ('fresh_control', 'scarce_alternative_late_deadline', self.family):
            original = make_scenario(7, family)
            modified = deepcopy(original)
            modified['patients'][0]['resource_demands']['ed_bed'] = 999
            modified['hospitals'][0]['capacity']['ed_bed'] = 999
            modified['observation_config']['feed_interval_minutes'] = 999
            if modified.get('response_matrix'):
                modified['response_matrix']['*']['H01']['delay'] = 999
            self.assertEqual(make_scenario(7, family), original)

    def test_invalid_seed_and_family_inputs_are_rejected(self):
        for seed in (True, False, -1, 7.0, '7', None):
            with self.subTest(seed=seed), self.assertRaises(ValueError):
                build_deadline_safe_scenarios(seed)
        for families in ('fresh_control', b'fresh_control', 7, [None], ['unknown'],
                         ['fresh_control', 'fresh_control']):
            with self.subTest(families=families), self.assertRaises(ValueError):
                build_deadline_safe_scenarios(7, families)
        for family in (None, 7, 'unknown'):
            with self.subTest(family=family), self.assertRaises(ValueError):
                make_scenario(7, family)


if __name__ == '__main__':
    unittest.main()
