"""Small-seed structural checks; reserved DEV/VAL inputs remain unused."""
from copy import deepcopy
import math
import unittest

from simulation.cooperative_probe_scenarios import (
    ADDITIONAL_FAMILIES, DEVELOPMENT_SEEDS, DEV_SEEDS, FAMILIES, ORIGINAL_FAMILIES,
    VALIDATION_SEEDS, build_cooperative_probe_scenarios, make_scenario,
)
from simulation.joint_policy_scenarios import _validate_extra_inputs
from simulation.map_replay import RouteSnapshot, validate_scenario
from simulation.partial_information_scenarios import SCARCE
from simulation.shortage_probe_scenarios import (
    build_shortage_probe_scenarios,
    make_scenario as make_previous,
)


class CooperativeProbeScenarioTests(unittest.TestCase):
    def case(self, suffix='active_reply_050', seed=7):
        return make_scenario(seed, f'cooperative_probe_{suffix}')

    def test_manifest_preserves_83_and_adds_twelve_distinct_boundaries(self):
        self.assertEqual(len(ORIGINAL_FAMILIES), 83)
        self.assertEqual(len(ADDITIONAL_FAMILIES), 12)
        self.assertEqual(len(FAMILIES), 95)
        self.assertEqual(len(set(FAMILIES)), 95)
        self.assertEqual(FAMILIES, ORIGINAL_FAMILIES + ADDITIONAL_FAMILIES)

    def test_reserved_development_validation_seed_partitions_are_exact(self):
        self.assertEqual(DEV_SEEDS, (41101, 41102, 41103))
        self.assertEqual(DEVELOPMENT_SEEDS, DEV_SEEDS)
        self.assertEqual(VALIDATION_SEEDS, tuple(range(42101, 42107)))
        self.assertFalse(set(DEV_SEEDS) & set(VALIDATION_SEEDS))

    def test_all_preceding_83_inputs_remain_exact_complete_copies(self):
        for seed in (7, 8):
            self.assertEqual(build_cooperative_probe_scenarios(seed, ORIGINAL_FAMILIES),
                             build_shortage_probe_scenarios(seed))

    def test_all_small_seed_cases_validate_and_keep_old_observation_channels(self):
        for seed in (7, 8):
            cases = build_cooperative_probe_scenarios(seed)
            self.assertEqual([case['family'] for case in cases], list(FAMILIES))
            for case in cases:
                validate_scenario(case)
                if 'response_matrix' in case:
                    _validate_extra_inputs(case)
                self.assertTrue(case['synthetic'])
                self.assertFalse(case['experiment']['additional_resource_change_channel_enabled'])
                self.assertEqual(case['experiment']['patient_count'], len(case['patients']))
                self.assertTrue(all('ed_bed' in p['resource_demands'] for p in case['patients']))

    def test_original_treatment_stream_and_common_clocks_are_not_scaled(self):
        for seed in (7, 8):
            inherited = make_previous(seed, 'shortage_probe_release_before_reply')
            for family in ADDITIONAL_FAMILIES:
                case = make_scenario(seed, family)
                self.assertEqual(case['patients'][0]['treatment_minutes'],
                                 inherited['patients'][0]['treatment_minutes'])
                self.assertEqual(case['max_minutes'], 60)
                self.assertEqual(case['timeout_minutes'], 3)
                self.assertEqual(case['retry_minutes'], 3)
                self.assertEqual(case['transfer_preparation_minutes'], 3)
                self.assertEqual(case['travel_error_range'], [1.0, 1.0])

    def test_active_half_and_three_minute_work_are_real_service_inputs(self):
        self.assertEqual(self.case('active_reply_050')['response_matrix']['*']['H01'],
                         dict(delay=.5, silent=False))
        self.assertEqual(self.case('active_reply_300')['response_matrix']['*']['H01'],
                         dict(delay=3, silent=False))
        self.assertEqual(self.case('active_reply_300')['timeout_minutes'], 3)

    def test_pause_resume_boundary_registers_normal_work_only_at_its_real_request(self):
        case = self.case('pause_resume')
        self.assertEqual(len(case['patients']), 2)
        self.assertEqual(case['patients'][1]['search_start_t'], 1)
        self.assertEqual(case['patients'][1]['required_capabilities'], [SCARCE])
        self.assertEqual(case['patients'][1]['resource_demands'], dict(ed_bed=1, special_team=1))
        self.assertEqual(case['response_matrix']['P01']['H01']['delay'], 2.5)
        self.assertEqual(case['response_matrix']['P02']['H01']['delay'], .5)
        self.assertEqual(case['inquiry_queue']['workers'], 1)

    def test_two_normal_jobs_keep_different_service_and_supplied_priorities(self):
        case = self.case('two_normal_fifo')
        self.assertEqual([p['search_start_t'] for p in case['patients']], [0, 1, 1])
        self.assertEqual([p['priority'] for p in case['patients']], [4, 2, 4])
        self.assertEqual(case['response_matrix']['P02']['H01']['delay'], .25)
        self.assertEqual(case['response_matrix']['P03']['H01']['delay'], .5)
        self.assertEqual(case['hospitals'][0]['capacity'], dict(ed_bed=2, special_team=2))
        self.assertEqual(case['patients'][0]['resource_demands'], dict(ed_bed=2))
        self.assertEqual(case['hospitals'][0]['initial_occupancy'][0]['demand'], dict(ed_bed=1))

    def test_completed_before_normal_boundary_has_private_early_release_and_short_work(self):
        case = self.case('completed_before_normal')
        self.assertEqual(case['hospitals'][0]['initial_occupancy'],
                         [dict(until=.5, demand=dict(ed_bed=1))])
        self.assertEqual(case['response_matrix']['P01']['H01']['delay'], .25)
        self.assertEqual(case['patients'][1]['search_start_t'], 1)
        self.assertEqual(case['response_matrix']['P02']['H01']['delay'], .25)

    def test_busy_admission_uses_actual_existing_background_work(self):
        case = self.case('busy_admission')
        self.assertEqual(case['inquiry_queue'], dict(enabled=True, workers=1,
            background_jobs=[dict(t=0, hospital='H01', service_minutes=3)]))
        self.assertEqual(case['response_matrix']['*']['H01'], dict(delay=.75, silent=False))

    def test_completed_silent_work_has_no_clinical_reply(self):
        case = self.case('silent_completion')
        self.assertEqual(case['response_matrix']['*']['H01'], dict(delay=1.5, silent=True))
        self.assertEqual(case['response_matrix']['*']['H02'], dict(delay=2.5, silent=False))
        self.assertTrue(case['experiment']['nearby_clinical_reply_never_delivered'])
        self.assertTrue(case['experiment']['queue_control_status_is_not_a_clinical_reply'])

    def test_expiry_boundary_has_nearer_original_pending_and_one_minute_ttl(self):
        case = self.case('expired_offer')
        routing = RouteSnapshot()
        durations = {h['id']: math.ceil(routing.route(case['patients'][0]['location'],
            (h['lat'], h['lon']))['duration_seconds']/60) for h in case['hospitals']}
        self.assertEqual(durations, dict(H01=5, H02=3))
        self.assertEqual(case['accept_ttl_minutes'], 1)
        self.assertEqual(case['response_matrix']['*']['H01']['delay'], .25)
        self.assertEqual(case['response_matrix']['*']['H02']['delay'], 2.5)
        self.assertEqual(case['hospitals'][0]['initial_occupancy'][0]['until'], .5)

    def test_multiple_workers_are_shared_existing_environment_capacity(self):
        case = self.case('multiple_workers')
        self.assertEqual(case['inquiry_queue'], dict(enabled=True, workers=2,
            background_jobs=[dict(t=0, hospital='H01', service_minutes=3)]))
        self.assertEqual(case['response_matrix']['*']['H01']['delay'], 1.5)
        self.assertEqual(case['hospitals'][0]['capacity'],
                         self.case()['hospitals'][0]['capacity'])

    def test_current_protected_hospital_and_separate_candidate_remain_distinct(self):
        case = self.case('protected_hospital_alternative')
        self.assertEqual([p['search_start_t'] for p in case['patients']], [0, 0])
        self.assertEqual(case['patients'][0]['resource_demands'], dict(ed_bed=2))
        self.assertEqual(case['patients'][1]['resource_demands'], dict(ed_bed=1, special_team=1))
        self.assertEqual(case['patients'][1]['required_capabilities'], [SCARCE])
        self.assertIn(SCARCE, case['hospitals'][0]['capabilities'])
        self.assertTrue(all(SCARCE not in h['capabilities'] for h in case['hospitals'][1:]))
        self.assertEqual(case['hospitals'][2]['id'], 'H03')
        self.assertEqual(case['hospitals'][2]['initial_occupancy'],
                         [dict(until=1, demand=dict(ed_bed=2))])
        self.assertEqual(case['response_matrix']['P02']['H01']['delay'], 2)
        self.assertEqual(case['response_matrix']['P01']['H03']['delay'], .5)

    def test_no_unsupported_control_schedule_or_future_prediction_fields_are_added(self):
        unsupported = {'probe_at', 'actual_completion_at', 'next_free_at', 'expected_release_at',
            'ack_at', 'ack_delay', 'switching_overhead', 'extra_workers', 'force_probe',
            'future_patient', 'queue_completion_at', 'normal_priority', 'cooperative_probe_enabled'}
        for family in ADDITIONAL_FAMILIES:
            case = make_scenario(7, family)
            for row in case['patients'] + case['hospitals']:
                self.assertFalse(set(row) & unsupported)
            self.assertFalse(set(case) & unsupported)
            self.assertEqual(set(case['inquiry_queue']), {'enabled', 'workers', 'background_jobs'})
            self.assertEqual(case['exogenous_events'], [])
            self.assertEqual(case['resource_update_config'],
                             dict(delay_minutes=0, missing_resources=[], silent=False))

    def test_observation_config_is_inherited_without_ack_or_status_resource_fields(self):
        previous = make_previous(7, 'shortage_probe_release_before_reply')['observation_config']
        for family in ADDITIONAL_FAMILIES:
            case = make_scenario(7, family)
            if family != 'cooperative_probe_busy_early_report':
                self.assertEqual(case['observation_config'], previous)
            self.assertTrue(case['experiment']['raw_branch_reachability_must_be_verified'])

    def test_busy_early_report_uses_only_supported_delayed_feed_and_integer_patient_times(self):
        case = self.case('busy_early_report')
        self.assertEqual([case['patients'][0][key] for key in
            ('occur_t', 'dispatch_t', 'ambulance_arrive_t', 'search_start_t')], [0, 0, 0, 0])
        self.assertEqual(case['observation_config']['feed_interval_minutes'], 1)
        self.assertEqual(case['observation_config']['feed_delay_by_source_minute'], {'0': 1})
        reports = {report['hospital']: report for report in case['observation_config']['initial_reports']}
        self.assertEqual(reports['H01']['observed_at'], -.25)
        self.assertEqual(reports['H01']['resources']['ed_bed']['used'], 0)
        self.assertEqual(case['hospitals'][0]['initial_occupancy'][0]['demand'], dict(ed_bed=1))
        self.assertEqual(case['inquiry_queue']['background_jobs'],
                         [dict(t=0, hospital='H03', service_minutes=1)])
        self.assertEqual(case['response_matrix']['*']['H01'], dict(delay=.25, silent=False))

    def test_busy_contact_has_honest_attempt_dependent_response_environment(self):
        case = self.case('busy_attempt_vary')
        self.assertEqual(case['response_matrix']['*']['H01'], dict(attempts=[
            dict(delay=.25, silent=False), dict(delay=.75, silent=True),
            dict(delay=.25, silent=False)]))
        self.assertTrue(case['experiment']['clinical_response_matrix_depends_on_actual_clinical_review_attempt'])
        self.assertTrue(case['experiment']['actual_contact_and_clinical_review_attempt_contracts_must_be_disclosed'])

    def test_requested_order_empty_selection_and_return_detachment(self):
        reverse = list(reversed(FAMILIES))
        self.assertEqual(build_cooperative_probe_scenarios(7, reverse),
                         [make_scenario(7, family) for family in reverse])
        self.assertEqual(build_cooperative_probe_scenarios(7, []), [])
        for family in ('fresh_control', 'deadline_multiple_pending_boundary',
                       'shortage_probe_later_patient_fifo', *ADDITIONAL_FAMILIES):
            original = make_scenario(7, family)
            changed = deepcopy(original)
            changed['patients'][0]['resource_demands']['ed_bed'] = 999
            changed['hospitals'][0]['capacity']['ed_bed'] = 999
            changed.setdefault('inquiry_queue', {})['workers'] = 999
            self.assertEqual(make_scenario(7, family), original)

    def test_invalid_inputs_are_rejected(self):
        for seed in (True, False, -1, 7.0, '7', None):
            with self.subTest(seed=seed), self.assertRaises(ValueError):
                build_cooperative_probe_scenarios(seed)
        for families in ('fresh_control', b'fresh_control', 7, [None], ['unknown'],
                         ['fresh_control', 'fresh_control']):
            with self.subTest(families=families), self.assertRaises(ValueError):
                build_cooperative_probe_scenarios(7, families)
        for family in (None, 7, 'unknown'):
            with self.subTest(family=family), self.assertRaises(ValueError):
                make_scenario(7, family)


if __name__ == '__main__':
    unittest.main()
