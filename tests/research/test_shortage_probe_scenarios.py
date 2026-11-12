"""Structural small-seed checks; reserved DEV/VAL inputs are never executed."""
from copy import deepcopy
import math
import unittest

from simulation.deadline_safe_scenarios import (
    build_deadline_safe_scenarios,
    make_scenario as make_previous,
)
from simulation.information_observations import ResourceObservationStore
from simulation.joint_policy_scenarios import _validate_extra_inputs
from simulation.map_replay import RouteSnapshot, validate_scenario
from simulation.partial_information_scenarios import BASIC, SCARCE
from simulation.shortage_probe_scenarios import (
    ADDITIONAL_FAMILIES, DEVELOPMENT_SEEDS, DEV_SEEDS, FAMILIES,
    ORIGINAL_FAMILIES, VALIDATION_SEEDS,
    build_shortage_probe_scenarios, make_scenario,
)


class ShortageProbeScenarioTests(unittest.TestCase):
    def case(self, suffix='release_before_reply', seed=7):
        return make_scenario(seed, f'shortage_probe_{suffix}')

    def test_manifest_preserves_73_and_adds_ten_distinct_boundaries(self):
        self.assertEqual(len(ORIGINAL_FAMILIES), 73)
        self.assertEqual(len(ADDITIONAL_FAMILIES), 10)
        self.assertEqual(len(FAMILIES), 83)
        self.assertEqual(len(set(FAMILIES)), 83)
        self.assertEqual(FAMILIES, ORIGINAL_FAMILIES + ADDITIONAL_FAMILIES)

    def test_fresh_seed_partitions_are_exact_and_disjoint(self):
        self.assertEqual(DEV_SEEDS, (39101, 39102, 39103))
        self.assertEqual(DEVELOPMENT_SEEDS, DEV_SEEDS)
        self.assertEqual(VALIDATION_SEEDS, tuple(range(40101, 40107)))
        self.assertFalse(set(DEV_SEEDS) & set(VALIDATION_SEEDS))

    def test_complete_previous_inputs_remain_value_for_value_unchanged(self):
        for seed in (7, 8):
            self.assertEqual(build_shortage_probe_scenarios(seed, ORIGINAL_FAMILIES),
                             build_deadline_safe_scenarios(seed))

    def test_all_small_seed_inputs_validate_and_keep_the_same_information_channel(self):
        for seed in (7, 8):
            cases = build_shortage_probe_scenarios(seed)
            self.assertEqual([case['family'] for case in cases], list(FAMILIES))
            for case in cases:
                validate_scenario(case)
                if 'response_matrix' in case:
                    _validate_extra_inputs(case)
                self.assertTrue(case['synthetic'])
                self.assertFalse(case['experiment']['additional_resource_change_channel_enabled'])
                self.assertEqual(case['experiment']['patient_count'], len(case['patients']))

    def test_new_geometry_is_fixed_and_real_minute_routes_are_three_and_five(self):
        routing = RouteSnapshot()
        for seed in (7, 8):
            for family in ADDITIONAL_FAMILIES:
                case = make_scenario(seed, family)
                self.assertEqual([(h['id'], h['lat'], h['lon']) for h in case['hospitals']],
                    [('H01', 35.18, 126.85), ('H02', 35.189481, 126.85)])
                durations = {h['id']: math.ceil(routing.route(case['patients'][0]['location'],
                    (h['lat'], h['lon']))['duration_seconds']/60) for h in case['hospitals']}
                self.assertEqual(durations, dict(H01=3, H02=5))

    def test_first_patient_treatment_stream_is_inherited_for_both_small_seeds(self):
        for seed in (7, 8):
            original = make_previous(seed, 'scarce_options_staggered_replies')['patients'][0]
            for family in ADDITIONAL_FAMILIES:
                patient = make_scenario(seed, family)['patients'][0]
                self.assertEqual(patient['treatment_minutes'], original['treatment_minutes'])
                self.assertEqual(patient['location'], original['location'])
                self.assertEqual(patient['search_start_t'], 0)

    def test_release_case_changes_only_physical_occupancy_and_actual_response_contract(self):
        case = self.case()
        self.assertEqual(case['hospitals'][0]['initial_occupancy'],
                         [dict(until=1, demand=dict(ed_bed=1))])
        self.assertNotIn('initial_occupancy', case['hospitals'][1])
        self.assertEqual(case['response_matrix'], {'*': {
            'H01': dict(delay=.75, silent=False), 'H02': dict(delay=2.5, silent=False)}})
        self.assertEqual(case['inquiry_queue'], dict(enabled=True, workers=1, background_jobs=[]))

    def test_still_full_case_keeps_a_real_occupied_bed_through_the_probe_reply(self):
        case = self.case('still_full')
        self.assertEqual(case['hospitals'][0]['initial_occupancy'],
                         [dict(until=8, demand=dict(ed_bed=1))])
        self.assertEqual(case['response_matrix'], self.case()['response_matrix'])

    def test_silent_case_never_delivers_a_nearby_reply_and_keeps_original_offer(self):
        case = self.case('silent')
        self.assertEqual(case['response_matrix']['*']['H01'], dict(delay=.75, silent=True))
        self.assertEqual(case['response_matrix']['*']['H02'], dict(delay=2.5, silent=False))
        self.assertEqual(case['timeout_minutes'], 3)
        self.assertTrue(case['experiment']['nearby_reply_never_delivered'])

    def test_fifo_boundary_really_registers_future_demand_after_probe_eligibility(self):
        case = self.case('later_patient_fifo')
        self.assertEqual(len(case['patients']), 2)
        p01, p02 = case['patients']
        self.assertEqual(p01['search_start_t'], 0)
        self.assertEqual([p02[field] for field in
            ('occur_t', 'dispatch_t', 'ambulance_arrive_t', 'search_start_t')], [1, 1, 1, 1])
        self.assertEqual(p02['required_capabilities'], [SCARCE])
        self.assertEqual(p02['resource_demands'], dict(ed_bed=1, special_team=1))
        self.assertIn(SCARCE, case['hospitals'][0]['capabilities'])
        self.assertNotIn(SCARCE, case['hospitals'][1]['capabilities'])
        self.assertEqual(case['hospitals'][1]['capacity']['special_team'], 0)
        self.assertEqual(case['response_matrix']['P01']['H01']['delay'], 2.5)
        self.assertEqual(case['response_matrix']['P02']['H01']['delay'], .25)
        self.assertEqual(case['inquiry_queue']['workers'], 1)
        self.assertEqual(case['inquiry_queue']['background_jobs'], [])

    def test_late_reply_background_work_is_private_and_original_timeout_is_unchanged(self):
        case = self.case('late_reply')
        self.assertEqual(case['inquiry_queue']['background_jobs'],
                         [dict(t=0, hospital='H01', service_minutes=3)])
        self.assertEqual(case['timeout_minutes'], 3)
        self.assertEqual(case['response_matrix']['*']['H01'], dict(delay=.75, silent=False))

    def test_short_ttl_is_an_environment_lifetime_without_any_refresh_field(self):
        case = self.case('short_ttl')
        self.assertEqual(case['accept_ttl_minutes'], 1)
        self.assertEqual(case['experiment']['original_accept_ttl_minutes'], 1)
        self.assertEqual(case['timeout_minutes'], 3)
        self.assertEqual(case['response_matrix'], self.case()['response_matrix'])

    def test_nonbed_shortage_is_also_physically_occupied(self):
        case = self.case('other_resource_shortage')
        self.assertEqual(case['patients'][0]['resource_demands'], dict(ed_bed=1, special_team=1))
        self.assertEqual(case['hospitals'][0]['initial_occupancy'],
                         [dict(until=1, demand=dict(ed_bed=1, special_team=1))])

    def test_missing_other_resource_remains_unknown_without_a_fresh_feed(self):
        case = self.case('missing_other_resource')
        config = case['observation_config']
        self.assertEqual(config['feed_interval_minutes'], 0)
        self.assertTrue(config['confirmation_silent'])
        store = ResourceObservationStore(stale_after_minutes=10)
        for index, report in enumerate(config['initial_reports']):
            store.apply(report['hospital'], report['resources'], observed_at=report['observed_at'],
                        received_at=0, now=0, sequence=index, source='initial')
        self.assertEqual(store.get('H01', 'special_team', now=.5)['status'], 'unknown')
        self.assertEqual(store.get('H02', 'special_team', now=.5)['status'], 'fresh')
        self.assertEqual(case['hospitals'][0]['capacity']['special_team'], 1)

    def test_stale_report_is_not_a_current_full_bed_observation(self):
        case = self.case('stale_report')
        config = case['observation_config']
        self.assertEqual(config['feed_interval_minutes'], 0)
        self.assertTrue(config['confirmation_silent'])
        store = ResourceObservationStore(stale_after_minutes=10)
        for index, report in enumerate(config['initial_reports']):
            store.apply(report['hospital'], report['resources'], observed_at=report['observed_at'],
                        received_at=0, now=0, sequence=index, source='initial')
        self.assertEqual(store.get('H01', 'ed_bed', now=.5)['status'], 'stale')
        self.assertEqual(store.get('H02', 'ed_bed', now=.5)['status'], 'fresh')

    def test_existing_other_patient_demand_is_current_and_compatible_with_the_full_bed(self):
        case = self.case('other_patient_pending')
        self.assertEqual([p['search_start_t'] for p in case['patients']], [0, 0])
        self.assertEqual(case['patients'][0]['resource_demands'], dict(ed_bed=2))
        self.assertEqual(case['patients'][1]['resource_demands'], dict(ed_bed=1, special_team=1))
        self.assertEqual(case['hospitals'][0]['initial_occupancy'][0]['demand'], dict(ed_bed=1))
        self.assertEqual(case['hospitals'][0]['capacity'], dict(ed_bed=2, special_team=1))
        self.assertEqual(case['hospitals'][1]['capacity'], dict(ed_bed=2, special_team=0))
        self.assertEqual(case['response_matrix']['P02']['H01'], dict(delay=2, silent=False))
        self.assertEqual(case['patients'][1]['required_capabilities'], [SCARCE])

    def test_supported_schema_has_no_predicted_future_policy_fields(self):
        unsupported = {'probe_at', 'predicted_release_at', 'future_patient', 'reply_at',
            'next_free_at', 'reserve_for', 'probe_allowed', 'force_probe', 'response_delay',
            'queue_completion_at', 'shortage_probe_enabled'}
        for family in ADDITIONAL_FAMILIES:
            case = make_scenario(7, family)
            for row in case['patients'] + case['hospitals']:
                self.assertFalse(unsupported & set(row))
            self.assertEqual(case['exogenous_events'], [])
            self.assertTrue(case['experiment']['raw_branch_reachability_must_be_verified'])
            self.assertEqual(case['resource_update_config'],
                dict(delay_minutes=0, missing_resources=[], silent=False))

    def test_original_movement_clock_and_common_limits_are_unchanged(self):
        for family in ADDITIONAL_FAMILIES:
            case = make_scenario(7, family)
            self.assertEqual(case['max_minutes'], 60)
            self.assertEqual(case['timeout_minutes'], 3)
            self.assertEqual(case['retry_minutes'], 3)
            self.assertEqual(case['transfer_preparation_minutes'], 3)
            self.assertEqual(case['travel_error_range'], [1.0, 1.0])

    def test_order_empty_selection_and_detached_return_values(self):
        reversed_families = list(reversed(FAMILIES))
        self.assertEqual(build_shortage_probe_scenarios(7, reversed_families),
                         [make_scenario(7, family) for family in reversed_families])
        self.assertEqual(build_shortage_probe_scenarios(7, []), [])
        for family in ('fresh_control', 'deadline_multiple_pending_boundary', *ADDITIONAL_FAMILIES):
            original = make_scenario(7, family)
            modified = deepcopy(original)
            modified['patients'][0]['resource_demands']['ed_bed'] = 999
            modified['hospitals'][0]['capacity']['ed_bed'] = 999
            modified['observation_config']['feed_interval_minutes'] = 999
            self.assertEqual(make_scenario(7, family), original)

    def test_invalid_seed_family_and_selection_inputs_are_rejected(self):
        for seed in (True, False, -1, 7.0, '7', None):
            with self.subTest(seed=seed), self.assertRaises(ValueError):
                build_shortage_probe_scenarios(seed)
        for families in ('fresh_control', b'fresh_control', 7, [None], ['unknown'],
                         ['fresh_control', 'fresh_control']):
            with self.subTest(families=families), self.assertRaises(ValueError):
                build_shortage_probe_scenarios(7, families)
        for family in (None, 7, 'unknown'):
            with self.subTest(family=family), self.assertRaises(ValueError):
                make_scenario(7, family)


if __name__ == '__main__':
    unittest.main()
