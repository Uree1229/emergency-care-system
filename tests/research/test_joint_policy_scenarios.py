"""Small-seed structural tests; reserved joint-policy seeds are never executed."""
from copy import deepcopy
import math
import unittest

from simulation.joint_policy_scenarios import (
    ADDITIONAL_FAMILIES, DEV_SEEDS, DEVELOPMENT_SEEDS, FAMILIES,
    ORIGINAL_FAMILIES, VALIDATION_SEEDS,
    build_joint_policy_scenarios, make_scenario,
)
from simulation.map_replay import RouteSnapshot, validate_scenario
from simulation.outage_recovery_scenarios import build_outage_recovery_scenarios
from simulation.partial_information_scenarios import SCARCE


class JointPolicyScenarioTests(unittest.TestCase):
    def case(self, family, seed=7):
        return make_scenario(seed, family)

    def test_manifest_and_reserved_partitions(self):
        self.assertEqual(len(ORIGINAL_FAMILIES), 48)
        self.assertEqual(len(ADDITIONAL_FAMILIES), 8)
        self.assertEqual(len(FAMILIES), 56)
        self.assertEqual(len(set(FAMILIES)), 56)
        self.assertEqual(DEV_SEEDS, (31101, 31102, 31103))
        self.assertEqual(DEVELOPMENT_SEEDS, DEV_SEEDS)
        self.assertEqual(VALIDATION_SEEDS, tuple(range(32101, 32107)))
        self.assertFalse(set(DEV_SEEDS) & set(VALIDATION_SEEDS))

    def test_every_inherited_case_is_completely_unchanged(self):
        for seed in (7, 8):
            old = build_outage_recovery_scenarios(seed)
            new = build_joint_policy_scenarios(seed, ORIGINAL_FAMILIES)
            self.assertEqual(new, old)

    def test_all_small_seed_cases_validate_without_new_reports(self):
        for seed in (7, 8):
            cases = build_joint_policy_scenarios(seed)
            self.assertEqual([c['family'] for c in cases], list(FAMILIES))
            for case in cases:
                validate_scenario(case)
                self.assertTrue(case['synthetic'])
                self.assertFalse(case['experiment']['additional_resource_change_channel_enabled'])
                self.assertEqual(case['experiment']['patient_count'], len(case['patients']))

    def test_each_new_case_has_common_fifo_and_real_minute_physics(self):
        for case in build_joint_policy_scenarios(7, ADDITIONAL_FAMILIES):
            self.assertTrue(case['inquiry_queue']['enabled'])
            self.assertEqual(case['inquiry_queue']['workers'], 1)
            self.assertIn(case['max_minutes'], (30, 60))
            self.assertEqual(case['transfer_preparation_minutes'], 3)
            self.assertEqual(case['accept_ttl_minutes'], 10)
            self.assertEqual(case['travel_error_range'], [1.0, 1.0])
            self.assertEqual(case['experiment']['timing_unit'], 'real_minutes')
            self.assertEqual(case['experiment']['review_resolution_seconds'], 15)
            self.assertTrue(all(type(p[k]) is int for p in case['patients']
                for k in ('occur_t', 'dispatch_t', 'ambulance_arrive_t', 'search_start_t')))

    def test_fractional_services_use_quarter_minutes_and_explicit_silence(self):
        found_fractional = False
        for case in build_joint_policy_scenarios(7, ADDITIONAL_FAMILIES):
            for entries in case['response_matrix'].values():
                for hid, entry in entries.items():
                    self.assertIn(hid, {h['id'] for h in case['hospitals']})
                    self.assertGreater(entry['delay'], 0)
                    self.assertEqual(entry['delay'] * 4, round(entry['delay'] * 4))
                    self.assertIs(type(entry['silent']), bool)
                    found_fractional |= entry['delay'] != int(entry['delay'])
        self.assertTrue(found_fractional)

    def test_new_history_is_relative_seconds_and_only_past_received(self):
        for case in build_joint_policy_scenarios(7, ADDITIONAL_FAMILIES):
            self.assertEqual(case['experiment']['history_timing_unit'], 'relative_seconds')
            for row in case['initial_inquiry_history']:
                self.assertLess(row['submitted_at'], row['observed_at'])
                self.assertLessEqual(row['observed_at'], 0)
                self.assertEqual(row['outcome'], 'reply')

    def test_scarce_patient_has_exactly_one_compatible_hospital_and_shared_bed(self):
        case = self.case('scarce_options_staggered_replies')
        self.assertEqual(len(case['patients']), 2)
        limited = next(p for p in case['patients'] if SCARCE in p['required_capabilities'])
        compatible = [h['id'] for h in case['hospitals']
                      if set(limited['required_capabilities']) <= set(h['capabilities'])]
        self.assertEqual(compatible, ['H01'])
        self.assertTrue(all(h['capacity']['ed_bed'] == 1 for h in case['hospitals']))
        self.assertEqual(limited['priority'], case['patients'][0]['priority'])

    def test_silent_near_options_remain_structurally_compatible(self):
        case = self.case('silent_first_options')
        matrix = case['response_matrix']['*']
        self.assertTrue(matrix['H01']['silent'])
        self.assertTrue(matrix['H02']['silent'])
        self.assertFalse(matrix['H03']['silent'])
        self.assertFalse(matrix['H04']['silent'])
        required = set(case['patients'][0]['required_capabilities'])
        self.assertTrue(all(required <= set(h['capabilities']) for h in case['hospitals']))

    def test_burst_has_per_patient_service_and_insufficient_near_capacity(self):
        case = self.case('shared_fifo_burst')
        self.assertEqual(len(case['patients']), 8)
        self.assertEqual(set(case['response_matrix']), {p['id'] for p in case['patients']})
        self.assertTrue(all(p['search_start_t'] == 0 for p in case['patients']))
        self.assertEqual({p['priority'] for p in case['patients']}, {1, 2, 4})
        self.assertLess(case['hospitals'][0]['capacity']['ed_bed'], len(case['patients']))
        self.assertGreaterEqual(sum(h['capacity']['ed_bed'] for h in case['hospitals']),
                                len(case['patients']))

    def test_background_work_makes_original_reply_later_than_deadline(self):
        case = self.case('late_reply_deadline')
        background, = case['inquiry_queue']['background_jobs']
        service = case['response_matrix']['*']['H01']['delay']
        self.assertGreater(background['service_minutes'] + service, case['timeout_minutes'])
        self.assertEqual(background['t'], 0)
        self.assertEqual(case['max_minutes'], 30)

    def test_recent_reversal_has_both_regimes_in_v3_window(self):
        case = self.case('recent_history_reversal')
        history = case['initial_inquiry_history']
        self.assertTrue(all(-900 < row['observed_at'] < 0 for row in history))
        near = sorted((r for r in history if r['hospital'] == 'H01'),
                      key=lambda r: r['observed_at'])
        farther = sorted((r for r in history if r['hospital'] == 'H02'),
                         key=lambda r: r['observed_at'])
        duration = lambda row: row['observed_at'] - row['submitted_at']
        self.assertLess(duration(near[0]), duration(near[-1]))
        self.assertGreater(duration(farther[0]), duration(farther[-1]))
        self.assertGreater(case['response_matrix']['*']['H01']['delay'],
                           case['response_matrix']['*']['H02']['delay'])

    def test_history_window_boundary_and_single_sample_are_explicit(self):
        history = self.case('sparse_history_window_boundary')['initial_inquiry_history']
        self.assertEqual(sum(r['hospital'] == 'H01' for r in history), 1)
        self.assertEqual(sorted(r['observed_at'] for r in history if r['hospital'] == 'H02'),
                         [-900, -899])

    def test_faster_far_reply_does_not_mean_faster_physical_arrival(self):
        case = self.case('selection_arrival_tradeoff')
        routing = RouteSnapshot()
        services = case['response_matrix']['*']
        patient = case['patients'][0]
        arrivals = {}
        for hospital in case['hospitals']:
            travel = math.ceil(routing.route(patient['location'],
                (hospital['lat'], hospital['lon']))['duration_seconds'] / 60)
            arrivals[hospital['id']] = travel + services[hospital['id']]['delay']
        self.assertLess(services['H02']['delay'], services['H01']['delay'])
        self.assertGreater(arrivals['H02'], arrivals['H01'])

    def test_outage_is_later_than_fast_offer_before_physical_arrival(self):
        for seed in (7, 8):
            case = self.case('outage_during_v3_hedge', seed)
            outage, = case['exogenous_events']
            first = case['response_matrix']['*']['H01']['delay']
            near = case['hospitals'][0]
            travel = math.ceil(RouteSnapshot().route(case['patients'][0]['location'],
                (near['lat'], near['lon']))['duration_seconds'] / 60)
            self.assertLess(first, outage['t'])
            self.assertLess(outage['t'], first + travel)
            self.assertEqual(outage['until'], 10)

    def test_requested_family_order_does_not_change_inputs(self):
        chosen = list(reversed(FAMILIES))
        cases = build_joint_policy_scenarios(7, chosen)
        self.assertEqual(cases, [self.case(family) for family in chosen])

    def test_outputs_are_detached_in_all_environment_fields(self):
        for family in ('fresh_control', 'recent_history_reversal', 'shared_fifo_burst'):
            original = self.case(family)
            changed = deepcopy(original)
            changed['hospitals'][0]['capacity']['ed_bed'] = 999
            changed['patients'][0]['resource_demands']['ed_bed'] = 999
            changed['observation_config']['feed_interval_minutes'] = 999
            if 'response_matrix' in changed:
                next(iter(changed['response_matrix'].values()))['H01']['delay'] = 999
            if changed.get('initial_inquiry_history'):
                changed['initial_inquiry_history'][0]['observed_at'] = 999
            self.assertEqual(self.case(family), original)

    def test_small_seeds_change_potential_environment_without_policy_input(self):
        left = self.case('shared_fifo_burst', 7)
        right = self.case('shared_fifo_burst', 8)
        self.assertNotEqual(left['response_matrix'], right['response_matrix'])
        self.assertFalse(any('policy' in key for key in left['response_matrix']))

    def test_invalid_seed_is_rejected(self):
        for seed in (True, False, -1, 7.0, '7', None):
            with self.subTest(seed=seed), self.assertRaises(ValueError):
                build_joint_policy_scenarios(seed)
            with self.subTest(seed=seed), self.assertRaises(ValueError):
                make_scenario(seed, 'fresh_control')

    def test_invalid_or_duplicate_family_is_rejected(self):
        for families in ('fresh_control', b'fresh_control', 7, [None], ['unknown'],
                         ['fresh_control', 'fresh_control']):
            with self.subTest(families=families), self.assertRaises(ValueError):
                build_joint_policy_scenarios(7, families)
        for family in (None, 7, 'unknown'):
            with self.subTest(family=family), self.assertRaises(ValueError):
                make_scenario(7, family)

    def test_empty_family_selection_is_supported(self):
        self.assertEqual(build_joint_policy_scenarios(7, []), [])


if __name__ == '__main__':
    unittest.main()
