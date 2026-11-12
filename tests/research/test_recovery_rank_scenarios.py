"""Small 7/8 structural checks; reserved validation inputs are not generated."""
from copy import deepcopy
import unittest

from simulation.joint_policy_scenarios import _validate_extra_inputs
from simulation.map_replay import validate_scenario
from simulation.recovery_dispatch_scenarios import make_scenario as old_case
from simulation.recovery_rank_scenarios import (
    FAMILIES, ORIGINAL_FAMILIES, ADDITIONAL_FAMILIES, BASE_FAMILIES,
    DEV_SEEDS, DEVELOPMENT_SEEDS, VALIDATION_SEEDS, make_scenario, build_recovery_rank_scenarios,
)


class RecoveryRankScenarioTests(unittest.TestCase):
    def case(self, suffix):
        return make_scenario(7, 'recovery_rank_' + suffix)

    def test_manifest_preserves121_and_adds_eight(self):
        self.assertEqual((len(ORIGINAL_FAMILIES), len(ADDITIONAL_FAMILIES), len(FAMILIES)), (121, 8, 129))
        self.assertEqual(FAMILIES, ORIGINAL_FAMILIES + ADDITIONAL_FAMILIES)
        self.assertEqual(len(set(FAMILIES)), 129)
        self.assertEqual(set(BASE_FAMILIES), set(ADDITIONAL_FAMILIES))

    def test_seed_lineage_is_explicit(self):
        self.assertEqual(DEV_SEEDS, DEVELOPMENT_SEEDS)
        self.assertEqual(DEV_SEEDS, (44101, 44102, 44103))
        self.assertEqual(VALIDATION_SEEDS, tuple(range(47101, 47107)))

    def test_all_inherited_inputs_are_complete_equal_copies(self):
        for seed in (7, 8):
            for family in ORIGINAL_FAMILIES:
                self.assertEqual(make_scenario(seed, family), old_case(seed, family))

    def test_every_small_input_validates_without_new_resource_push(self):
        for seed in (7, 8):
            cases = build_recovery_rank_scenarios(seed)
            self.assertEqual([c['family'] for c in cases], list(FAMILIES))
            for case in cases:
                validate_scenario(case)
                if 'response_matrix' in case:
                    _validate_extra_inputs(case)
                self.assertTrue(case['synthetic'])
                self.assertFalse(case['experiment']['additional_resource_change_channel_enabled'])

    def test_new_cases_keep_original_capacities_routes_workers_priorities_and_caps(self):
        for seed in (7, 8):
            for family in ADDITIONAL_FAMILIES:
                new, old = make_scenario(seed, family), old_case(seed, BASE_FAMILIES[family])
                self.assertEqual([(h['id'], h['capacity'], h['lat'], h['lon']) for h in new['hospitals']],
                                 [(h['id'], h['capacity'], h['lat'], h['lon']) for h in old['hospitals']])
                self.assertEqual(new['inquiry_queue'], old['inquiry_queue'])
                for key in ('priority', 'treatment_minutes', 'location', 'ambulance_origin'):
                    self.assertEqual(new['patients'][0][key], old['patients'][0][key])
                for key in ('max_minutes', 'timeout_minutes', 'retry_minutes', 'accept_ttl_minutes',
                            'transfer_preparation_minutes', 'resource_update_config'):
                    self.assertEqual(new[key], old[key])

    def test_repeated_recovery_includes_real_rank_work_and_a_second_loss(self):
        c = self.case('repeated_recovery_after_decline')
        self.assertEqual([(e['t'], e['until']) for e in c['exogenous_events']], [(5, 8)] * 3 + [(9, 16)] * 3)
        self.assertEqual(c['response_matrix']['*']['H01']['attempts'][2:],
                         [dict(delay=2.5, silent=False), dict(delay=.5, silent=True)])

    def test_same_shortage_has_no_second_physical_recovery_in_window(self):
        c = self.case('same_shortage_refresh')
        self.assertEqual([e['until'] for e in c['exogenous_events']], [120] * 3)
        self.assertEqual(c['observation_config']['feed_interval_minutes'], 1)

    def test_partial_context_requires_bed_and_team_and_real_two_minute_transport(self):
        c = self.case('partial_required_resource')
        self.assertEqual(c['patients'][0]['resource_demands'], dict(ed_bed=1, special_team=1))
        self.assertEqual(c['observation_config']['feed_delay_minutes'], 2)
        self.assertEqual(c['observation_config']['feed_missing_resources'], ['special_team'])

    def test_old_optimism_uses_existing_source_transport_and_actual_three_slot_outage(self):
        c = self.case('predecline_source_late')
        self.assertEqual(c['observation_config']['feed_delay_by_source_minute'], {'0': 5})
        self.assertEqual([(e['t'], e['until']) for e in c['exogenous_events']], [(1, 120)] * 3)

    def test_busy_then_decline_keeps_original_one_bed_and_real_paid_work(self):
        c = self.case('busy_then_clinical_decline')
        self.assertEqual(c['hospitals'][0]['capacity']['ed_bed'], 1)
        self.assertEqual(c['hospitals'][0]['initial_occupancy'][0]['until'], 4)
        self.assertEqual(c['inquiry_queue']['background_jobs'], [dict(t=0, hospital='H01', service_minutes=5)])
        self.assertEqual(c['response_matrix']['*']['H02'], dict(delay=2.5, silent=True))
        self.assertEqual(c['response_matrix']['*']['H01'], dict(delay=.75, silent=False))

    def test_unvisited_control_reuses_the_existing_fourth_hospital(self):
        c = self.case('unvisited_recovery_alternative')
        self.assertEqual(len(c['hospitals']), 4)
        h = next(h for h in c['hospitals'] if h['id'] == 'H04')
        self.assertEqual(h['initial_occupancy'], [dict(until=8, demand=dict(ed_bed=3))])
        negative = self.case('unvisited_current_alternative')
        for hid in ('H02', 'H03'):
            self.assertEqual([(e['t'], e['until']) for e in negative['exogenous_events'] if e['hospital'] == hid], [(1, 9)] * 3)

    def test_future_patient_is_an_actual_new_request_with_real_fifo_service(self):
        c = self.case('later_patient_fifo')
        self.assertEqual([p['search_start_t'] for p in c['patients']], [0, 9])
        p = c['patients'][1]
        self.assertEqual([p[k] for k in ('occur_t', 'dispatch_t', 'ambulance_arrive_t')], [9, 9, 9])
        self.assertEqual(p['resource_demands'], dict(ed_bed=1, special_team=1))
        self.assertEqual(c['response_matrix']['P01']['H01']['attempts'][2], dict(delay=2.75, silent=False))
        self.assertEqual(c['response_matrix']['P02']['H01'], dict(delay=.25, silent=False))

    def test_only_existing_runtime_fields_and_explicit_reachability_limits(self):
        forbidden = {'patient_events', 'condition_changes', 'force_recovery', 'reopen_at', 'rank_at',
                     'token_at', 'ack_at', 'next_free_at', 'actual_completion_at', 'extra_workers'}
        for f in ADDITIONAL_FAMILIES:
            c = make_scenario(7, f)
            self.assertFalse(set(c) & forbidden)
            self.assertTrue(c['experiment']['raw_branch_reachability_must_be_verified'])
            self.assertFalse(c['experiment']['condition_change_runtime_event_supported'])

    def test_returned_inputs_are_detached_and_selection_order_is_preserved(self):
        self.assertEqual(build_recovery_rank_scenarios(7, []), [])
        selected = list(reversed(FAMILIES))
        self.assertEqual([c['family'] for c in build_recovery_rank_scenarios(7, selected)], selected)
        for f in ADDITIONAL_FAMILIES:
            old = make_scenario(7, f)
            changed = deepcopy(old)
            changed['hospitals'][0]['capacity']['ed_bed'] = 999
            self.assertEqual(make_scenario(7, f), old)

    def test_invalid_seed_or_family_is_rejected(self):
        for seed in (True, False, -1, '7', 7.0, None):
            with self.assertRaises(ValueError):
                build_recovery_rank_scenarios(seed)
        for families in ('fresh_control', b'fresh_control', 3, [None], ['bad'], ['fresh_control'] * 2):
            with self.assertRaises(ValueError):
                build_recovery_rank_scenarios(7, families)


if __name__ == '__main__':
    unittest.main()
