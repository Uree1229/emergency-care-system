"""Raw-audit mutations use seeds 7/8; one named observed DEV regression is retained."""
from copy import deepcopy
import subprocess
import sys
import unittest

from simulation import joint_policy_audit as audit
from simulation.joint_policy_engine import run_joint_policy_branch
from simulation.joint_policy_scenarios import make_scenario


class JointRawAudit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures = {}
        for family, selector, allocation in (
            ('scarce_options_staggered_replies', 'v3', 'H1'),
            ('scarce_options_staggered_replies', 'v4', 'C1'),
            ('shared_fifo_burst', 'v3', 'H1'),
            ('late_reply_deadline', 'v3', 'C1'),
            ('outage_during_v3_hedge', 'v4', 'H1'),
            ('future_outage_after_early_offer', 'fixed_3', 'C1'),
            ('unverified_backup_resources', 'v3', 'H1'),
        ):
            scenario = make_scenario(7, family)
            branch = run_joint_policy_branch(scenario, inquiry=selector, allocation=allocation)
            cls.fixtures[family, selector] = (scenario, branch)

    def fixture(self, family='scarce_options_staggered_replies', selector='v3'):
        return deepcopy(self.fixtures[family, selector])

    def event(self, branch, typ, **updates):
        event = next(row for row in branch['ordered_emissions'] if row['type'] == typ)
        seq = event['event_sequence']
        event.update(updates)
        next(row for row in branch['events'] if row.get('event_sequence') == seq).update(updates)
        return event

    def reject(self, scenario, branch):
        with self.assertRaises((audit.JointPolicyAuditError, KeyError, IndexError)):
            audit.audit_branch(scenario, branch)

    def test_all_real_fixture_branches_pass_with_float_times(self):
        for key, (scenario, branch) in self.fixtures.items():
            with self.subTest(key=key):
                result = audit.audit_branch(scenario, branch)
                self.assertTrue(result['passed'])
                self.assertFalse(result['actual_optimality_claimed'])
                self.assertEqual(result['assignment_constraint_violations'], 0)
                self.assertEqual(result['waiting']['patients'], len(scenario['patients']))

    def test_seed_eight_fractional_selector_and_pending_guard_pass(self):
        scenario = make_scenario(8, 'scarce_options_staggered_replies')
        branch = run_joint_policy_branch(scenario, inquiry='v4', allocation='C1')
        self.assertTrue(audit.audit_branch(scenario, branch)['passed'])

    def test_observed_development_31102_post_hook_job_keeps_same_tick_start_event(self):
        # This DEV case already failed the raw audit. It is a correctness
        # regression, never an independent validation or policy-selection case.
        scenario = make_scenario(31102, 'shared_fifo_burst')
        branch = run_joint_policy_branch(scenario, inquiry='fixed_3', allocation='H1', recovery=False)
        self.assertTrue(audit.audit_branch(scenario, branch)['passed'])
        asks = [row for row in branch['ordered_emissions'] if row['type'] == 'ask'
                and row['patient'] == 'P08' and row['hospital'] == 'H02' and row['attempt'] == 2]
        self.assertEqual(len(asks), 1)
        self.assertEqual(asks[0]['t'], 6)
        starts = [row for row in branch['ordered_emissions'] if row['type'] == 'review_started'
                  and row['patient'] == 'P08' and row['hospital'] == 'H02' and row['attempt'] == 2]
        self.assertEqual(len(starts), 1)
        self.assertEqual(starts[0]['t'], asks[0]['t'])

    def test_auditor_imports_no_engine_solver_assignment_or_store(self):
        code = '''
import importlib.abc, sys
class Block(importlib.abc.MetaPathFinder):
 def find_spec(self, fullname, path=None, target=None):
  if fullname.startswith('ortools') or (fullname.startswith('simulation.') and (
      fullname.endswith('_engine') or fullname.endswith('_assignment') or fullname in (
      'simulation.sim','simulation.map_replay','simulation.inquiry_queue','simulation.observation_store'))):
   raise AssertionError('forbidden execution import '+fullname)
sys.meta_path.insert(0,Block())
from simulation import joint_policy_audit
joint_policy_audit._selector_reproduce('v4',dict(now=86400,candidates=[],max_new=0))
print('PASS')
'''
        result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'PASS')

    def test_off_grid_clock_rejected(self):
        scenario, branch = self.fixture()
        self.event(branch, 'joint_inquiry_policy', t=.1)
        self.reject(scenario, branch)

    def test_ordered_emission_projection_cannot_drop_or_change_events(self):
        scenario, branch = self.fixture()
        branch['ordered_emissions'][0]['type'] = 'changed'
        self.reject(scenario, branch)

    def test_duplicate_emission_sequence_rejected(self):
        scenario, branch = self.fixture()
        branch['events'].append(deepcopy(next(e for e in branch['events'] if 'event_sequence' in e)))
        self.reject(scenario, branch)

    def test_extra_resource_capture_channel_rejected(self):
        scenario, branch = self.fixture()
        self.event(branch, 'resource_report_captured', type='resource_update_captured')
        self.reject(scenario, branch)

    def test_extra_resource_cycle_channel_rejected(self):
        scenario, branch = self.fixture()
        self.event(branch, 'early_offer_cycle', type='resource_update_cycle')
        self.reject(scenario, branch)

    def test_publication_id_report_rejected(self):
        scenario, branch = self.fixture()
        self.event(branch, 'resource_report_received', publication_id=1)
        self.reject(scenario, branch)

    def test_enabled_extra_resource_source_rejected(self):
        scenario, branch = self.fixture()
        branch['resource_updates_enabled'] = True
        self.reject(scenario, branch)

    def test_fake_received_resource_quantity_rejected(self):
        scenario, branch = self.fixture()
        event = next(row for row in branch['ordered_emissions'] if row['type'] == 'resource_report_received')
        resources = deepcopy(event['resources'])
        resources['ed_bed']['capacity'] += 1
        self.event(branch, 'resource_report_received', resources=resources)
        self.reject(scenario, branch)

    def test_future_resource_source_time_rejected(self):
        scenario, branch = self.fixture()
        self.event(branch, 'resource_report_received', observed_at=1)
        self.reject(scenario, branch)

    def test_fake_physical_gate_capacity_rejected(self):
        scenario, branch = self.fixture()
        gate = next(row for row in branch['ordered_emissions'] if row['type'] == 'confirmation_gate')
        resources = deepcopy(gate['resources'])
        resources['ed_bed']['capacity'] += 1
        self.event(branch, 'confirmation_gate', resources=resources)
        self.reject(scenario, branch)

    def test_offer_cannot_be_renewed_at_confirmation(self):
        scenario, branch = self.fixture()
        self.event(branch, 'confirmation_gate', offer_expires_at=100)
        self.reject(scenario, branch)

    def test_fake_treatment_end_and_endpoint_rejected(self):
        scenario, branch = self.fixture()
        branch['patient_outcomes'][0]['treatment_start_t'] += .25
        self.reject(scenario, branch)

    def test_unobserved_patient_cannot_leave_whole_cohort_denominator(self):
        scenario, branch = self.fixture()
        branch['patient_outcomes'].pop()
        self.reject(scenario, branch)

    def test_move_estimate_cannot_be_shortened(self):
        scenario, branch = self.fixture()
        branch['trajectories'][0]['legs'][1]['estimated_minutes'] -= 1
        self.reject(scenario, branch)

    def test_actual_motion_cannot_teleport(self):
        scenario, branch = self.fixture()
        branch['trajectories'][0]['legs'][1]['origin'][0] += 1
        self.reject(scenario, branch)

    def test_original_transfer_preparation_cannot_shrink(self):
        scenario, branch = self.fixture('future_outage_after_early_offer', 'fixed_3')
        failure = next(row for row in branch['ordered_emissions'] if row['type'] == 'arrival_unable')
        self.event(branch, 'arrival_unable', transfer_search_at=failure['transfer_search_at'] - 1)
        self.reject(scenario, branch)

    def test_future_selector_now_rejected(self):
        scenario, branch = self.fixture()
        branch['inquiry_decisions'][0]['inputs']['now'] += 15
        self.reject(scenario, branch)

    def test_oracle_fields_in_selector_input_rejected(self):
        scenario, branch = self.fixture()
        branch['inquiry_decisions'][0]['inputs']['candidates'][0]['silent'] = True
        self.reject(scenario, branch)

    def test_future_reply_schedule_in_selector_input_rejected(self):
        scenario, branch = self.fixture()
        branch['inquiry_decisions'][0]['inputs']['until'] = 100
        self.reject(scenario, branch)

    def test_fake_observed_selector_history_rejected(self):
        scenario, branch = self.fixture()
        branch['inquiry_decisions'][0]['inputs']['observations'].append(dict(
            hospital='H01', patient='oracle', submitted_at=86390, observed_at=86400, outcome='reply'))
        self.reject(scenario, branch)

    def test_fake_pending_in_selector_input_rejected(self):
        scenario, branch = self.fixture()
        branch['inquiry_decisions'][0]['inputs']['pending'].append(dict(
            hospital='H01', patient='unobserved', submitted_at=86400))
        self.reject(scenario, branch)

    def test_selector_decision_must_reproduce_actual_pure_function(self):
        scenario, branch = self.fixture()
        branch['inquiry_decisions'][0]['decision']['reason'] = 'fabricated'
        self.reject(scenario, branch)

    def test_selector_clock_and_contact_cap_cannot_change(self):
        scenario, branch = self.fixture()
        branch['inquiry_decisions'][0]['inputs']['max_new'] = 99
        self.reject(scenario, branch)

    def test_original_timeout_handle_cannot_be_extended(self):
        scenario, branch = self.fixture('late_reply_deadline')
        ask = next(row for row in branch['ordered_emissions'] if row['type'] == 'ask')
        self.event(branch, 'ask', timeout_at=ask['timeout_at'] + .25)
        self.reject(scenario, branch)

    def test_original_feedback_receipt_cannot_be_advanced(self):
        scenario, branch = self.fixture('late_reply_deadline')
        receipt = next(row for row in branch['ordered_emissions'] if row['type'] == 'inquiry_feedback')
        self.event(branch, 'inquiry_feedback', observed_t=receipt['observed_t'] - .25)
        self.reject(scenario, branch)

    def test_silent_work_cannot_be_reported_as_received_reply(self):
        scenario, branch = self.fixture('late_reply_deadline')
        receipt = next(row for row in branch['ordered_emissions'] if row['type'] == 'inquiry_feedback' and row['outcome'] == 'timeout')
        for container in (branch['ordered_emissions'], branch['events']):
            next(row for row in container if row.get('event_sequence') == receipt['event_sequence'])['outcome'] = 'reply'
        self.reject(scenario, branch)

    def test_fifo_work_cannot_complete_early(self):
        scenario, branch = self.fixture('shared_fifo_burst')
        done = next(row for row in branch['ordered_emissions'] if row['type'] == 'review_completed')
        self.event(branch, 'review_completed', t=done['t'] - .25)
        self.reject(scenario, branch)

    def test_fifo_work_aggregate_cannot_drop_submitted_work(self):
        scenario, branch = self.fixture('shared_fifo_burst')
        branch['inquiry_queue_report']['aggregate']['submitted'] -= 1
        self.reject(scenario, branch)

    def test_heuristic_cannot_claim_optimality(self):
        scenario, branch = self.fixture()
        branch['heuristic_records'][0]['optimal_proven'] = True
        self.reject(scenario, branch)

    def test_model_cannot_claim_actual_optimality(self):
        scenario, branch = self.fixture()
        branch['allocation_decisions'][0]['actual_optimal_proven'] = True
        self.reject(scenario, branch)

    def test_model_cannot_use_unknown_oracle_hospital_fields(self):
        scenario, branch = self.fixture()
        branch['allocation_decisions'][0]['inputs']['hospitals'][0]['workers'] = 2
        self.reject(scenario, branch)

    def test_model_objective_is_recomputed(self):
        scenario, branch = self.fixture()
        branch['allocation_decisions'][0]['proposed_objective']['priority_weighted_travel_minutes'] += 1
        self.reject(scenario, branch)

    def test_c1_bound_cannot_be_false_optimal(self):
        scenario, branch = self.fixture(selector='v4')
        branch['solver_records'][0]['stages'][0]['best_bound'] += 1
        self.reject(scenario, branch)

    def test_c1_stability_objective_and_deterministic_baseline_checked(self):
        scenario, branch = self.fixture(selector='v4')
        branch['solver_records'][0]['stability']['baseline_objective'][0] += 1
        self.reject(scenario, branch)

    def test_unverified_resources_remain_censored_in_full_wait_denominator(self):
        scenario, branch = self.fixture('unverified_backup_resources')
        result = audit.audit_branch(scenario, branch)
        self.assertEqual(result['waiting']['untreated'], 1)
        self.assertEqual(result['waiting']['patients'], 1)
        detail = result['waiting']['patient_details'][0]
        self.assertIsNone(detail['request_to_treatment_minutes'])
        self.assertEqual(detail['request_to_treatment_capped_minutes'], scenario['max_minutes'])

    def test_pending_guard_has_actual_hold_and_no_unaccepted_model_edge(self):
        scenario, branch = self.fixture()
        result = audit.audit_branch(scenario, branch)
        self.assertGreater(result['counts']['pending_guard_holds'], 0)
        for row in branch['pending_guard_records']:
            for hold in row['result']['holds']:
                self.assertLessEqual(hold['deadline'], hold['submitted_at'] + 1)
                self.assertLessEqual(hold['deadline'], hold['held_since'] + 1)

    def test_pending_guard_cannot_reset_original_request_time(self):
        scenario, branch = self.fixture()
        record = next(row for row in branch['pending_guard_records'] if row['result']['holds'])
        record['snapshot']['waiting'][0]['pending_inquiries'][0]['submitted_at'] += .25
        self.reject(scenario, branch)

    def test_pending_guard_cannot_use_fake_fresh_bed_quantity(self):
        scenario, branch = self.fixture()
        record = next(row for row in branch['pending_guard_records'] if row['result']['holds'])
        record['snapshot']['waiting'][0]['bed_view']['free'] += 1
        self.reject(scenario, branch)

    def test_pending_guard_cannot_change_ready_priority(self):
        scenario, branch = self.fixture()
        record = next(row for row in branch['pending_guard_records'] if row['result']['holds'])
        record['snapshot']['ready'][0]['priority'] = 100
        self.reject(scenario, branch)

    def test_pending_guard_lifetime_cannot_renew(self):
        scenario, branch = self.fixture()
        record = next(row for row in branch['pending_guard_records'] if row['result']['holds'])
        record['result']['holds'][0]['deadline'] += 1
        self.reject(scenario, branch)

    def test_disabled_pending_guard_cannot_hide_interventions(self):
        scenario, branch = self.fixture()
        branch['pending_protection_enabled'] = False
        self.reject(scenario, branch)

    def test_pending_guard_snapshot_cannot_leak_future_completion(self):
        scenario, branch = self.fixture()
        branch['pending_guard_records'][0]['snapshot']['finish_t'] = 3
        self.reject(scenario, branch)

    def test_pending_guard_hold_count_matches_raw_events(self):
        scenario, branch = self.fixture()
        branch['metrics']['pending_guard_holds'] += 1
        self.reject(scenario, branch)

    def test_pending_guard_cannot_fabricate_alternative_resource_availability(self):
        scenario, branch = self.fixture()
        record = next(row for row in branch['pending_guard_records'] if row['result']['holds'])
        ready = record['snapshot']['ready'][0]
        hid = next(iter(ready['alternative_resource_views']))
        ready['alternative_resource_views'][hid]['ed_bed']['free'] += 1
        self.reject(scenario, branch)

    def test_pending_guard_cannot_fabricate_eligible_alternative_hospital(self):
        scenario, branch = self.fixture()
        record = next(row for row in branch['pending_guard_records'] if row['result']['holds'])
        record['snapshot']['ready'][0]['eligible_alternative_hospitals'].append('FUTURE-HOSPITAL')
        self.reject(scenario, branch)

    def test_known_required_shortage_never_creates_pending_protection(self):
        _, branch = self.fixture()
        snapshot = deepcopy(next(row for row in branch['pending_guard_records'] if row['result']['holds'])['snapshot'])
        waiter = snapshot['waiting'][0]
        resource = next(resource for resource in waiter['demand'] if resource != 'ed_bed')
        waiter['resource_views'][resource].update(status='fresh', free=0)
        self.assertEqual(audit._pending_guard_expected(snapshot)['held'], [])

    def test_no_inquiry_eligible_alternative_never_creates_pending_protection(self):
        _, branch = self.fixture()
        snapshot = deepcopy(next(row for row in branch['pending_guard_records'] if row['result']['holds'])['snapshot'])
        shared = snapshot['waiting'][0]['compatible_hospitals'][0]
        for ready in snapshot['ready']:
            ready['eligible_alternative_hospitals'] = [shared]
        self.assertEqual(audit._pending_guard_expected(snapshot)['held'], [])


if __name__ == '__main__':
    unittest.main()
