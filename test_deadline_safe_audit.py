"""Pending-work and callback provenance audits; structural seeds 7 and 8 only."""
from copy import deepcopy
import json
import subprocess
import sys
import unittest

from simulation import deadline_safe_audit as audit
from simulation import deadline_recovery_audit as prior
from simulation.deadline_safe_engine import run_deadline_safe_branch, run_deadline_safe_replay
from simulation.deadline_safe_scenarios import make_scenario


class DeadlineSafeRawAudit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scenario = make_scenario(7, 'scarce_alternative_late_deadline')
        cls.branch = run_deadline_safe_branch(cls.scenario, late_reply_recovery=True, late_pending_guard=True)
        cls.pending_scenario = make_scenario(7, 'deadline_multiple_pending_boundary')
        cls.pending_branch = run_deadline_safe_branch(cls.pending_scenario, late_reply_recovery=True, late_pending_guard=True)

    def fixture(self, pending=False):
        return (deepcopy(self.pending_scenario), deepcopy(self.pending_branch)) if pending else \
            (deepcopy(self.scenario), deepcopy(self.branch))

    def event(self, branch, kind, *, match=None, **updates):
        event = next(row for row in branch['ordered_emissions'] if row['type'] == kind
                     and (match is None or match(row)))
        sequence = event['event_sequence']
        event.update(deepcopy(updates))
        next(row for row in branch['events'] if row.get('event_sequence') == sequence).update(deepcopy(updates))
        return event

    def reject(self, scenario, branch):
        with self.assertRaises((audit.DeadlineSafeAuditError, KeyError, IndexError, TypeError)):
            audit.audit_branch(scenario, branch)

    def test_target_actual_late_acceptance_remains_auditable(self):
        checked = audit.audit_branch(deepcopy(self.scenario), deepcopy(self.branch))
        self.assertTrue(checked['passed'])
        self.assertEqual(checked['counts']['late_offer_accepted'], 1)
        self.assertFalse(checked['actual_optimality_claimed'])
        self.assertEqual(self.branch['late_offer_records'][0]['snapshot']['patient']['pending_inquiries'], [])
        self.assertEqual([row['treatment_start_t'] for row in self.branch['patient_outcomes']], [8.25, 3.5])

    def test_actual_pending_handle_is_reconstructed_at_receipt(self):
        checked = audit.audit_branch(deepcopy(self.pending_scenario), deepcopy(self.pending_branch))
        self.assertTrue(checked['passed'])
        self.assertEqual(checked['counts']['late_offer_accepted'], 0)
        record = self.pending_branch['late_offer_records'][0]
        self.assertEqual(record['result']['reason'], 'PENDING_INQUIRY')
        self.assertEqual([row['hospital'] for row in record['snapshot']['patient']['pending_inquiries']], ['H04'])
        self.assertEqual([row['treatment_start_t'] for row in self.pending_branch['patient_outcomes']], [7.5, 3.5])

    def test_all_ten_branches_pass_original_raw_audits(self):
        for scenario in (self.scenario, self.pending_scenario):
            result = run_deadline_safe_replay(deepcopy(scenario))
            checked = audit.audit_deadline_safe_replay(result)
            self.assertEqual(checked['branches'], 10)
            self.assertTrue(checked['passed'])

    def test_guard_false_exactly_uses_frozen_v1_audit(self):
        branch = run_deadline_safe_branch(self.scenario, late_reply_recovery=True, late_pending_guard=False)
        self.assertEqual(audit.audit_branch(self.scenario, deepcopy(branch)),
                         prior.audit_branch(self.scenario, deepcopy(branch)))

    def test_c1_pending_boundary_passes(self):
        scenario = make_scenario(8, 'deadline_multiple_pending_boundary')
        branch = run_deadline_safe_branch(scenario, allocation='C1', late_reply_recovery=True, late_pending_guard=True)
        self.assertTrue(audit.audit_branch(scenario, branch)['passed'])
        self.assertEqual(branch['late_offer_records'][0]['result']['reason'], 'PENDING_INQUIRY')

    def test_silent_completion_does_not_become_offer(self):
        scenario = make_scenario(8, 'deadline_silent_alternative')
        branch = run_deadline_safe_branch(scenario, late_reply_recovery=True, late_pending_guard=True)
        self.assertEqual(audit.audit_branch(scenario, branch)['counts']['late_offer_receipts'], 0)

    def test_original_scarce_gain_is_preserved(self):
        scenario = make_scenario(8, 'scarce_options_staggered_replies')
        branch = run_deadline_safe_branch(scenario, late_reply_recovery=True, late_pending_guard=True)
        self.assertTrue(audit.audit_branch(scenario, branch)['passed'])
        self.assertEqual([row['treatment_start_t'] for row in branch['patient_outcomes']], [5.5, 3.5])

    def test_pending_field_is_required(self):
        scenario, branch = self.fixture(True)
        del branch['late_offer_records'][0]['snapshot']['patient']['pending_inquiries']
        self.reject(scenario, branch)

    def test_actual_pending_handles_cannot_be_removed(self):
        scenario, branch = self.fixture(True)
        branch['late_offer_records'][0]['snapshot']['patient']['pending_inquiries'] = []
        self.reject(scenario, branch)

    def test_current_cohort_pending_cannot_be_omitted(self):
        scenario, branch = self.fixture(True)
        cohort = branch['late_offer_records'][0]['snapshot']['registered_search_patients']
        next(row for row in cohort if row['id'] == 'P01')['pending_inquiries'].clear()
        self.reject(scenario, branch)

    def test_pending_submit_time_cannot_be_rewritten(self):
        scenario, branch = self.fixture(True)
        branch['late_offer_records'][0]['snapshot']['patient']['pending_inquiries'][0]['submitted_at'] = 0
        self.reject(scenario, branch)

    def test_pending_original_deadline_cannot_be_renewed(self):
        scenario, branch = self.fixture(True)
        branch['late_offer_records'][0]['snapshot']['patient']['pending_inquiries'][0]['timeout_at'] += 1
        self.reject(scenario, branch)

    def test_pending_attempt_must_match_actual_ask(self):
        scenario, branch = self.fixture(True)
        branch['late_offer_records'][0]['snapshot']['patient']['pending_inquiries'][0]['attempt'] += 1
        self.reject(scenario, branch)

    def test_pending_ask_reference_must_match_actual_submission(self):
        scenario, branch = self.fixture(True)
        branch['late_offer_records'][0]['snapshot']['patient']['pending_inquiries'][0]['ask_event_sequence'] = 0
        self.reject(scenario, branch)

    def test_pending_hospital_must_match_actual_submission(self):
        scenario, branch = self.fixture(True)
        branch['late_offer_records'][0]['snapshot']['patient']['pending_inquiries'][0]['hospital'] = 'H01'
        self.reject(scenario, branch)

    def test_pending_handle_cannot_reveal_private_response_schedule(self):
        scenario, branch = self.fixture(True)
        branch['late_offer_records'][0]['snapshot']['patient']['pending_inquiries'][0]['finish_t'] = 3.75
        self.reject(scenario, branch)

    def test_pending_handle_cannot_reveal_private_silence(self):
        scenario, branch = self.fixture(True)
        branch['late_offer_records'][0]['snapshot']['patient']['pending_inquiries'][0]['silent'] = False
        self.reject(scenario, branch)

    def test_duplicate_pending_handle_is_rejected(self):
        scenario, branch = self.fixture(True)
        pending = branch['late_offer_records'][0]['snapshot']['patient']['pending_inquiries']
        pending.append(deepcopy(pending[0]))
        self.reject(scenario, branch)

    def test_closed_original_handle_cannot_count_as_pending(self):
        scenario, branch = self.fixture(True)
        callback = branch['late_offer_callbacks'][0]
        branch['late_offer_records'][0]['snapshot']['patient']['pending_inquiries'] = [
            {key:callback[key] for key in ('hospital', 'attempt', 'submitted_at', 'timeout_at', 'ask_event_sequence')}]
        self.reject(scenario, branch)

    def test_nonpending_case_cannot_invent_a_pending_handle(self):
        scenario, branch = self.fixture()
        handle = deepcopy(self.pending_branch['late_offer_records'][0]['snapshot']['patient']['pending_inquiries'][0])
        branch['late_offer_records'][0]['snapshot']['patient']['pending_inquiries'] = [handle]
        self.reject(scenario, branch)

    def test_pending_optional_reply_cannot_be_adopted(self):
        scenario, branch = self.fixture(True)
        branch['late_offer_records'][0]['result'] = dict(eligible=True, hospital='H02', expires_at=6.25,
                                                       protected_patients=[], reason='RECEIVED_LATE_OFFER')
        self.reject(scenario, branch)

    def test_pending_rejection_reason_is_independently_recomputed(self):
        scenario, branch = self.fixture(True)
        branch['late_offer_records'][0]['result']['reason'] = 'OUTSIDE_WINDOW'
        self.reject(scenario, branch)

    def test_optional_reply_does_not_cancel_real_pending_work(self):
        scenario, branch = self.fixture(True)
        self.event(branch, 'ask', match=lambda row:row['hospital'] == 'H04', timeout_at=3.25)
        self.reject(scenario, branch)

    def test_original_timeout_feedback_is_preserved(self):
        scenario, branch = self.fixture()
        self.event(branch, 'inquiry_feedback', match=lambda row:row['hospital'] == 'H02', outcome='reply')
        self.reject(scenario, branch)

    def test_original_no_response_is_preserved(self):
        scenario, branch = self.fixture()
        self.event(branch, 'no_response', match=lambda row:row['hospital'] == 'H02', type='hidden_terminal')
        self.reject(scenario, branch)

    def test_received_result_cannot_be_fabricated_from_work_completion(self):
        scenario, branch = self.fixture()
        scenario['response_matrix']['*']['H02']['silent'] = True
        self.reject(scenario, branch)

    def test_current_resource_view_cannot_be_rewritten(self):
        scenario, branch = self.fixture()
        branch['late_offer_records'][0]['snapshot']['resources']['ed_bed']['free'] += 1
        self.reject(scenario, branch)

    def test_original_callback_condition_is_preserved(self):
        scenario, branch = self.fixture()
        self.event(branch, 'hospital_reply_received', condition_fingerprint='0' * 64)
        self.reject(scenario, branch)

    def test_callback_receipt_timestamp_must_match_actual_fifo(self):
        scenario, branch = self.fixture()
        self.event(branch, 'hospital_reply_received', received_at=3)
        self.reject(scenario, branch)

    def test_new_acceptance_ttl_uses_actual_receipt(self):
        scenario, branch = self.fixture()
        self.event(branch, 'late_offer_accepted', expires_at=10)
        self.reject(scenario, branch)

    def test_actual_late_accept_has_unique_receipt_source(self):
        scenario, branch = self.fixture()
        self.event(branch, 'accept', match=lambda row:'late_offer_event_sequence' in row, receipt_event_sequence=0)
        self.reject(scenario, branch)

    def test_safe_protocol_cannot_claim_timeout_extension(self):
        scenario, branch = self.fixture()
        branch['deadline_safe_spec']['original_timeout_unchanged'] = False
        self.reject(scenario, branch)

    def test_false_guard_flag_cannot_hide_safe_snapshot(self):
        scenario, branch = self.fixture()
        branch['late_pending_guard_enabled'] = False
        self.reject(scenario, branch)

    def test_protocol_cannot_disable_pending_rejection(self):
        scenario, branch = self.fixture()
        branch['deadline_safe_spec']['pending_action'] = 'accept_optional_reply'
        self.reject(scenario, branch)

    def test_unknown_snapshot_policy_input_is_rejected(self):
        scenario, branch = self.fixture()
        branch['late_offer_records'][0]['snapshot']['patient']['worker_count'] = 1
        self.reject(scenario, branch)

    def test_callback_count_matches_raw_ledger(self):
        scenario, branch = self.fixture()
        branch['metrics']['late_offer_accepted'] += 1
        self.reject(scenario, branch)

    def test_extra_late_record_is_rejected(self):
        scenario, branch = self.fixture()
        branch['late_offer_records'].append(deepcopy(branch['late_offer_records'][0]))
        self.reject(scenario, branch)

    def test_saved_raw_audits_without_any_engine_or_solver(self):
        payload = json.dumps(dict(scenario=self.pending_scenario, branch=self.pending_branch), ensure_ascii=True)
        code = '''
import importlib.abc,json,sys
class Block(importlib.abc.MetaPathFinder):
 def find_spec(self, fullname, path=None, target=None):
  if fullname.startswith('ortools') or (fullname.startswith('simulation.') and (
      fullname.endswith('_engine') or fullname.endswith('_assignment') or fullname in (
      'simulation.sim','simulation.map_replay','simulation.inquiry_queue','simulation.observation_store'))):
   raise AssertionError('forbidden execution import ' + fullname)
sys.meta_path.insert(0,Block())
from simulation.deadline_safe_audit import audit_branch
value=json.loads(sys.stdin.read())
assert audit_branch(value['scenario'],value['branch'])['passed']
print('PASS')
'''
        result = subprocess.run([sys.executable, '-B', '-c', code], input=payload,
                                text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'PASS')


if __name__ == '__main__':
    unittest.main()
