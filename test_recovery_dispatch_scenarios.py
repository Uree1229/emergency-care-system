"""Small 7/8 structural cases only; no new-family 441xx/461xx generation."""
from copy import deepcopy
import unittest

from simulation.joint_policy_scenarios import _validate_extra_inputs
from simulation.map_replay import validate_scenario
from simulation.partial_information_scenarios import SCARCE
from simulation.retry_dispatch_scenarios import build_retry_dispatch_scenarios, make_scenario as old_case
from simulation.recovery_dispatch_scenarios import (
    ADDITIONAL_FAMILIES, BASE_FAMILIES, DEVELOPMENT_SEEDS, DEV_SEEDS, FAMILIES,
    ORIGINAL_FAMILIES, VALIDATION_SEEDS, build_recovery_dispatch_scenarios, make_scenario,
)


class RecoveryDispatchScenarioTests(unittest.TestCase):
    def case(self, suffix, seed=7):
        return make_scenario(seed, 'recovery_dispatch_' + suffix)

    def test_manifest_keeps_109_and_adds_twelve_distinct_cases(self):
        self.assertEqual((len(ORIGINAL_FAMILIES), len(ADDITIONAL_FAMILIES), len(FAMILIES)), (109, 12, 121))
        self.assertEqual(len(set(FAMILIES)), 121)
        self.assertEqual(FAMILIES, ORIGINAL_FAMILIES + ADDITIONAL_FAMILIES)
        self.assertEqual(set(BASE_FAMILIES), set(ADDITIONAL_FAMILIES))

    def test_retrospective_dev_and_fresh_validation_reservations(self):
        self.assertEqual(DEV_SEEDS, (44101, 44102, 44103))
        self.assertEqual(DEVELOPMENT_SEEDS, DEV_SEEDS)
        self.assertEqual(VALIDATION_SEEDS, tuple(range(46101, 46107)))
        self.assertFalse(set(DEV_SEEDS) & set(VALIDATION_SEEDS))

    def test_every_inherited_input_is_an_exact_complete_copy_at_small_seeds(self):
        for seed in (7, 8):
            self.assertEqual(build_recovery_dispatch_scenarios(seed, ORIGINAL_FAMILIES),
                             build_retry_dispatch_scenarios(seed))

    def test_all_small_inputs_validate_with_existing_fields_and_no_resource_push(self):
        for seed in (7, 8):
            cases = build_recovery_dispatch_scenarios(seed)
            self.assertEqual([c['family'] for c in cases], list(FAMILIES))
            for case in cases:
                validate_scenario(case)
                if 'response_matrix' in case:
                    _validate_extra_inputs(case)
                self.assertTrue(case['synthetic'])
                self.assertFalse(case['experiment']['additional_resource_change_channel_enabled'])
                self.assertEqual(case['experiment']['patient_count'], len(case['patients']))

    def test_new_cases_keep_parent_capacity_route_workers_and_original_priority(self):
        for seed in (7, 8):
            for family in ADDITIONAL_FAMILIES:
                case, old = make_scenario(seed, family), old_case(seed, BASE_FAMILIES[family])
                self.assertEqual([(h['id'], h['capacity'], h['lat'], h['lon']) for h in case['hospitals']],
                                 [(h['id'], h['capacity'], h['lat'], h['lon']) for h in old['hospitals']])
                self.assertEqual(case['inquiry_queue']['workers'], old['inquiry_queue']['workers'])
                self.assertEqual(case['inquiry_queue']['enabled'], old['inquiry_queue']['enabled'])
                self.assertEqual(case['patients'][0]['priority'], old['patients'][0]['priority'])
                self.assertEqual(case['patients'][0]['treatment_minutes'], old['patients'][0]['treatment_minutes'])
                for key in ('max_minutes', 'timeout_minutes', 'retry_minutes', 'accept_ttl_minutes',
                            'transfer_preparation_minutes', 'travel_error_range', 'resource_update_config'):
                    self.assertEqual(case[key], old[key])

    def test_busy_control_uses_actual_background_work_not_a_clinical_decline(self):
        case = self.case('busy_nonadmission')
        self.assertEqual(case['inquiry_queue']['background_jobs'], [dict(t=0, hospital='H01', service_minutes=5)])
        self.assertEqual(case['hospitals'][0]['initial_occupancy'][0]['until'], 1)
        self.assertTrue(case['experiment']['control_nonadmission_is_not_a_patient_response'])

    def test_silence_remains_silence_after_actual_review_completion(self):
        self.assertEqual(self.case('silent_timeout')['response_matrix']['*']['H01'], dict(delay=.5, silent=True))

    def test_repeated_shortage_reports_have_no_physical_recovery_in_window(self):
        case = self.case('same_shortage_refresh')
        self.assertEqual(case['hospitals'][0]['initial_occupancy'][0]['until'], 120)
        self.assertEqual(case['max_minutes'], 60)
        self.assertEqual(case['observation_config']['feed_interval_minutes'], 1)

    def test_late_optimism_comes_from_real_preoutage_truth(self):
        case = self.case('predecline_optimism_late')
        self.assertEqual(case['hospitals'][0]['initial_occupancy'], [])
        self.assertEqual(case['exogenous_events'], [dict(type='resource_unavailable', t=1, until=120,
                                                        hospital='H01', resource='ed_bed') for _ in range(3)])
        self.assertEqual(case['experiment']['actual_bed_slots_unavailable'], 3)
        self.assertEqual(case['response_matrix']['*']['H01'], dict(delay=1.5, silent=False))
        self.assertEqual(case['observation_config']['feed_delay_by_source_minute'], {'0': 4})

    def test_out_of_order_shortage_uses_original_per_source_transport(self):
        case = self.case('out_of_order_shortage')
        self.assertEqual(case['observation_config']['feed_delay_by_source_minute'], {'3': 4, '4': 0})
        self.assertEqual(case['hospitals'][0]['initial_occupancy'][0]['until'], 4)

    def test_partial_unknown_retains_a_second_explicitly_required_resource(self):
        case = self.case('partial_unknown')
        self.assertEqual(case['patients'][0]['resource_demands'], dict(ed_bed=1, special_team=1))
        self.assertEqual(case['observation_config']['feed_delay_minutes'], 1)
        self.assertEqual(case['observation_config']['feed_missing_resources'], ['special_team'])
        self.assertTrue(case['observation_config']['confirmation_silent'])
        self.assertEqual(case['hospitals'][0]['capacity']['special_team'], 1)

    def test_stale_physical_recovery_uses_real_eleven_minute_report_lag(self):
        case = self.case('stale_recovery')
        self.assertEqual(case['observation_config']['feed_delay_minutes'], 11)
        self.assertTrue(case['observation_config']['confirmation_silent'])
        self.assertEqual(case['hospitals'][0]['initial_occupancy'][0]['until'], 4)

    def test_second_real_rejection_is_a_physical_outage_not_a_forged_response(self):
        case = self.case('recovery_then_decline')
        self.assertEqual(case['exogenous_events'], [dict(type='resource_unavailable', t=5, until=8,
                                                        hospital='H01', resource='ed_bed') for _ in range(3)])
        self.assertEqual(case['experiment']['actual_bed_slots_unavailable'], 3)
        self.assertEqual(case['response_matrix']['*']['H01'], dict(attempts=[dict(delay=.5, silent=False),
            dict(delay=2.5, silent=False), dict(delay=.5, silent=False)]))

    def test_maturity_boundary_keeps_actual_slow_reopen_and_other_offer(self):
        case = self.case('slow_reopen_maturity')
        self.assertEqual(case['response_matrix']['*']['H01'], dict(attempts=[
            dict(delay=.5, silent=False), dict(delay=2.75, silent=False)]))
        self.assertEqual(case['response_matrix']['*']['H04'], dict(delay=2.5, silent=False))
        self.assertTrue(case['experiment']['original_offer_readiness_not_modified'])
        self.assertEqual(case['timeout_minutes'], 3)

    def test_duplicate_feed_boundary_has_late_real_recovery_and_silent_later_reviews(self):
        case = self.case('duplicate_recovery_feeds')
        self.assertEqual(case['hospitals'][0]['initial_occupancy'][0]['until'], 8)
        self.assertEqual(case['observation_config']['feed_interval_minutes'], 1)
        self.assertEqual(case['response_matrix']['*']['H01'], dict(attempts=[
            dict(delay=.5, silent=False), dict(delay=.5, silent=True)]))

    def test_future_single_candidate_keeps_actual_registration_and_shared_work(self):
        case = self.case('future_single_candidate')
        self.assertEqual([p['search_start_t'] for p in case['patients']], [0, 5])
        patient = case['patients'][1]
        for key in ('occur_t', 'dispatch_t', 'ambulance_arrive_t', 'search_start_t'):
            self.assertEqual(patient[key], 5)
        self.assertEqual(patient['required_capabilities'], [SCARCE])
        self.assertEqual(patient['resource_demands'], dict(ed_bed=1, special_team=1))
        self.assertEqual([h['id'] for h in case['hospitals'] if SCARCE in h['capabilities']], ['H01'])
        self.assertEqual(case['response_matrix']['P01']['H01']['attempts'][1]['delay'], 2.75)
        self.assertEqual(case['response_matrix']['P02']['H01'], dict(delay=.25, silent=False))

    def test_budget_pressure_retains_twelve_candidates_original_two_patients_and_caps(self):
        case = self.case('contact_budget_pressure')
        self.assertEqual(len(case['hospitals']), 12)
        self.assertEqual([p['id'] for p in case['patients']], ['P01', 'P02'])
        self.assertEqual(case['experiment']['original_concurrent_handle_limit'], 3)
        self.assertEqual(case['experiment']['original_episode_contact_limit'], 10)
        self.assertEqual([hid for hid, r in case['response_matrix']['*'].items() if not r['silent']], ['H01'])

    def test_no_new_condition_or_control_schedule_input_is_invented(self):
        forbidden = {'patient_events', 'condition_changes', 'force_recovery', 'reopen_at', 'token_at',
            'ack_at', 'next_free_at', 'actual_completion_at', 'future_patient', 'extra_workers'}
        for family in ADDITIONAL_FAMILIES:
            case = make_scenario(7, family)
            self.assertFalse(set(case) & forbidden)
            self.assertEqual(set(case['inquiry_queue']), {'enabled', 'workers', 'background_jobs'})
            self.assertTrue(case['experiment']['patient_condition_change_not_supported_by_runtime_inputs'])
            self.assertTrue(case['experiment']['raw_branch_reachability_must_be_verified'])
            for row in case['patients'] + case['hospitals']:
                self.assertFalse(set(row) & forbidden)

    def test_subset_order_empty_and_detached_return_values(self):
        selected = list(reversed(FAMILIES))
        self.assertEqual(build_recovery_dispatch_scenarios(7, selected), [make_scenario(7, f) for f in selected])
        self.assertEqual(build_recovery_dispatch_scenarios(7, []), [])
        for family in ('fresh_control', 'retry_dispatch_decline_then_recovery', *ADDITIONAL_FAMILIES):
            old = make_scenario(7, family)
            changed = deepcopy(old)
            changed['patients'][0]['priority'] = 999
            changed['hospitals'][0]['capacity']['ed_bed'] = 999
            self.assertEqual(make_scenario(7, family), old)

    def test_invalid_seed_and_family_selection_inputs_are_rejected(self):
        for seed in (True, False, -1, 7.0, '7', None):
            with self.subTest(seed=seed), self.assertRaises(ValueError):
                build_recovery_dispatch_scenarios(seed)
        for families in ('fresh_control', b'fresh_control', 7, [None], ['unknown'], ['fresh_control', 'fresh_control']):
            with self.subTest(families=families), self.assertRaises(ValueError):
                build_recovery_dispatch_scenarios(7, families)
        for family in (None, 7, 'unknown'):
            with self.subTest(family=family), self.assertRaises(ValueError):
                make_scenario(7, family)


if __name__ == '__main__':
    unittest.main()
