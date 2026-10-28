"""Structural-only inputs: no reserved seed is generated or executed here."""
from copy import deepcopy
import unittest

from simulation.cooperative_probe_scenarios import build_cooperative_probe_scenarios, make_scenario as old_case
from simulation.joint_policy_scenarios import _validate_extra_inputs
from simulation.map_replay import validate_scenario
from simulation.retry_dispatch_scenarios import (
    ADDITIONAL_FAMILIES, BASE_FAMILIES, DEVELOPMENT_SEEDS, DEV_SEEDS, FAMILIES,
    ORIGINAL_FAMILIES, VALIDATION_SEEDS, build_retry_dispatch_scenarios, make_scenario,
)


class RetryDispatchScenarioTests(unittest.TestCase):
    def case(self, suffix, seed=7):
        return make_scenario(seed, 'retry_dispatch_' + suffix)

    def test_manifest_preserves_95_and_adds_fourteen_distinct_boundaries(self):
        self.assertEqual(len(ORIGINAL_FAMILIES), 95)
        self.assertEqual(len(ADDITIONAL_FAMILIES), 14)
        self.assertEqual(len(FAMILIES), 109)
        self.assertEqual(len(set(FAMILIES)), 109)
        self.assertEqual(FAMILIES, ORIGINAL_FAMILIES + ADDITIONAL_FAMILIES)
        self.assertEqual(set(BASE_FAMILIES), set(ADDITIONAL_FAMILIES))

    def test_reserved_seed_declarations_are_exact_and_disjoint(self):
        self.assertEqual(DEV_SEEDS, (44101, 44102, 44103))
        self.assertEqual(DEVELOPMENT_SEEDS, DEV_SEEDS)
        self.assertEqual(VALIDATION_SEEDS, tuple(range(45101, 45107)))
        self.assertFalse(set(DEV_SEEDS) & set(VALIDATION_SEEDS))

    def test_all_preceding_95_complete_inputs_are_unchanged_at_both_small_seeds(self):
        for seed in (7, 8):
            self.assertEqual(build_retry_dispatch_scenarios(seed, ORIGINAL_FAMILIES),
                             build_cooperative_probe_scenarios(seed))

    def test_all_small_seed_inputs_validate_without_extra_resource_channel(self):
        for seed in (7, 8):
            cases = build_retry_dispatch_scenarios(seed)
            self.assertEqual([case['family'] for case in cases], list(FAMILIES))
            for case in cases:
                validate_scenario(case)
                if 'response_matrix' in case:
                    _validate_extra_inputs(case)
                self.assertFalse(case['experiment']['additional_resource_change_channel_enabled'])
                self.assertTrue(case['synthetic'])
                self.assertEqual(case['experiment']['patient_count'], len(case['patients']))

    def test_new_cases_preserve_parent_physical_supply_worker_and_supplied_priority(self):
        for seed in (7, 8):
            for family in ADDITIONAL_FAMILIES:
                current, parent = make_scenario(seed, family), old_case(seed, BASE_FAMILIES[family])
                self.assertEqual([(h['id'], h['capacity'], h['lat'], h['lon'], h['capabilities'])
                                  for h in current['hospitals']],
                                 [(h['id'], h['capacity'], h['lat'], h['lon'], h['capabilities'])
                                  for h in parent['hospitals']])
                self.assertEqual(current['inquiry_queue']['workers'], parent['inquiry_queue']['workers'])
                self.assertEqual(current['inquiry_queue']['enabled'], parent['inquiry_queue']['enabled'])
                self.assertEqual(current['patients'], parent['patients'])
                for key in ('max_minutes', 'timeout_minutes', 'retry_minutes', 'accept_ttl_minutes',
                            'transfer_preparation_minutes', 'travel_error_range', 'resource_update_config'):
                    self.assertEqual(current[key], parent[key])

    def test_frontier_case_exceeds_existing_episode_budget_without_raising_it(self):
        case = self.case('frontier_last_responsive')
        self.assertEqual(len(case['hospitals']), 12)
        self.assertEqual(len(case['patients']), 2)
        self.assertEqual(case['inquiry_queue']['workers'], 1)
        self.assertEqual(case['experiment']['original_concurrent_handle_limit'], 3)
        self.assertEqual(case['experiment']['original_episode_contact_limit'], 10)
        self.assertEqual([hid for hid, row in case['response_matrix']['*'].items() if not row['silent']], ['H12'])
        self.assertEqual({row['delay'] for row in case['response_matrix']['*'].values()}, {.5})

    def test_all_silent_case_does_not_invent_a_successful_hospital(self):
        case = self.case('frontier_all_silent')
        self.assertTrue(all(row == dict(delay=.5, silent=True) for row in case['response_matrix']['*'].values()))
        self.assertEqual(case['hospitals'], self.case('frontier_last_responsive')['hospitals'])

    def test_attempt_dependent_response_has_a_real_first_silent_service(self):
        for row in self.case('first_silent_then_reply')['response_matrix']['*'].values():
            self.assertEqual(row, dict(attempts=[dict(delay=.25, silent=True), dict(delay=.5, silent=False)]))

    def test_decline_recovery_has_real_occupancy_and_an_initial_optimistic_report(self):
        case = self.case('decline_then_recovery')
        hospital = case['hospitals'][0]
        self.assertEqual(hospital['initial_occupancy'], [dict(until=4, demand=hospital['capacity'])])
        report = case['observation_config']['initial_reports'][0]
        self.assertEqual(report['hospital'], hospital['id'])
        self.assertTrue(all(row['used'] == 0 for row in report['resources'].values()))
        self.assertEqual(case['observation_config']['feed_interval_minutes'], 1)
        self.assertEqual(case['exogenous_events'], [])

    def test_three_service_boundaries_do_not_change_original_deadline(self):
        for suffix, service in (('before', 2.75), ('at', 3), ('after', 3.25)):
            case = self.case('reply_' + suffix + '_deadline')
            self.assertEqual(case['timeout_minutes'], 3)
            self.assertEqual(case['response_matrix']['*']['H01'], dict(delay=service, silent=False))
            self.assertTrue(all(row['silent'] for hid, row in case['response_matrix']['*'].items() if hid != 'H01'))

    def test_slow_pending_work_retains_eight_real_requests_and_single_reviewers(self):
        case = self.case('pending_slow_work')
        self.assertEqual(len(case['patients']), 8)
        self.assertEqual(case['inquiry_queue']['workers'], 1)
        self.assertTrue(all(entry == dict(delay=2.75, silent=False)
            for row in case['response_matrix'].values() for entry in row.values()))
        self.assertEqual({p['search_start_t'] for p in case['patients']}, {0})

    def test_shared_burst_preserves_existing_service_work_with_explicit_background(self):
        case = self.case('shared_fifo_burst')
        parent = old_case(7, 'shared_fifo_burst')
        self.assertEqual(case['response_matrix'], parent['response_matrix'])
        self.assertEqual(case['inquiry_queue']['background_jobs'], [dict(t=0, hospital='H01', service_minutes=2)])
        self.assertEqual(case['inquiry_queue']['workers'], 1)

    def test_priority_boundary_keeps_supplied_weights_instead_of_inventing_triage(self):
        case = self.case('priority_reversal')
        self.assertEqual([p['priority'] for p in case['patients']], [4, 2])
        self.assertTrue(case['experiment']['priorities_are_experimental_weights_not_inferred_clinical_KTAS'])
        self.assertEqual(case['response_matrix']['*']['H02'], dict(attempts=[
            dict(delay=.5, silent=True), dict(delay=.5, silent=False)]))

    def test_future_request_retains_its_actual_later_registration_time(self):
        case = self.case('future_request')
        self.assertEqual([p['search_start_t'] for p in case['patients']], [0, 1])
        for key in ('occur_t', 'dispatch_t', 'ambulance_arrive_t', 'search_start_t'):
            self.assertEqual(case['patients'][1][key], 1)
        self.assertNotIn('future_patient', case)

    def test_unresolved_unknown_and_stale_do_not_gain_new_confirmations(self):
        for suffix in ('unknown', 'stale'):
            case = self.case(suffix + '_unresolved')
            observation = case['observation_config']
            self.assertEqual(observation['feed_interval_minutes'], 0)
            self.assertTrue(observation['confirmation_silent'])
            report = observation['initial_reports'][1]
            if suffix == 'unknown':
                self.assertIsNone(report['resources']['ed_bed'])
            else:
                self.assertEqual(report['observed_at'], -11)
            self.assertEqual(case['response_matrix']['*']['H02'], dict(delay=.25, silent=False))

    def test_new_report_old_control_uses_only_existing_out_of_order_feed_fields(self):
        case = self.case('new_report_old_control')
        self.assertEqual(case['observation_config']['feed_delay_by_source_minute'], {'0': 2, '1': 0})
        self.assertEqual(case['inquiry_queue']['background_jobs'], [dict(t=0, hospital='H03', service_minutes=1)])
        self.assertEqual(set(case['inquiry_queue']), {'enabled', 'workers', 'background_jobs'})
        self.assertTrue(case['experiment']['queue_controls_are_not_resource_reports'])

    def test_no_policy_instruction_or_private_schedule_is_added_as_a_public_field(self):
        forbidden = {'probe_at', 'force_retry', 'force_candidate', 'actual_completion_at', 'next_free_at',
            'expected_release_at', 'ack_at', 'ack_delay', 'extra_workers', 'future_patient',
            'queue_completion_at', 'normal_priority', 'retry_dispatch_enabled'}
        for family in ADDITIONAL_FAMILIES:
            case = make_scenario(7, family)
            self.assertFalse(set(case) & forbidden)
            for row in case['patients'] + case['hospitals']:
                self.assertFalse(set(row) & forbidden)

    def test_subset_order_empty_and_detachment_are_preserved(self):
        reverse = list(reversed(FAMILIES))
        self.assertEqual(build_retry_dispatch_scenarios(7, reverse), [make_scenario(7, f) for f in reverse])
        self.assertEqual(build_retry_dispatch_scenarios(7, []), [])
        for family in ('fresh_control', 'shortage_probe_late_reply', *ADDITIONAL_FAMILIES):
            original = make_scenario(7, family)
            changed = deepcopy(original)
            changed['patients'][0]['priority'] = 999
            changed['hospitals'][0]['capacity']['ed_bed'] = 999
            self.assertEqual(make_scenario(7, family), original)

    def test_invalid_seed_and_selection_inputs_are_rejected(self):
        for seed in (True, False, -1, 7.0, '7', None):
            with self.subTest(seed=seed), self.assertRaises(ValueError):
                build_retry_dispatch_scenarios(seed)
        for selection in ('fresh_control', b'fresh_control', 7, [None], ['unknown'],
                          ['fresh_control', 'fresh_control']):
            with self.subTest(selection=selection), self.assertRaises(ValueError):
                build_retry_dispatch_scenarios(7, selection)
        for family in (None, 7, 'unknown'):
            with self.subTest(family=family), self.assertRaises(ValueError):
                make_scenario(7, family)


if __name__ == '__main__':
    unittest.main()
