"""Independent provenance and tampering tests; structural seeds 7 and 8 only."""
from copy import deepcopy
import json
import subprocess
import sys
import unittest

from simulation import deadline_recovery_audit as audit
from simulation.deadline_recovery_engine import run_deadline_recovery_branch, run_deadline_recovery_replay
from simulation.deadline_recovery_scenarios import make_scenario


class DeadlineRecoveryRawAudit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scenario = make_scenario(7, 'scarce_alternative_late_deadline')
        cls.branch = run_deadline_recovery_branch(cls.scenario, late_reply_recovery=True)

    def fixture(self):
        return deepcopy(self.scenario), deepcopy(self.branch)

    def event(self, branch, kind, *, match=None, **updates):
        event = next(row for row in branch['ordered_emissions'] if row['type'] == kind
            and (match is None or match(row)))
        sequence = event['event_sequence']
        event.update(deepcopy(updates))
        next(row for row in branch['events'] if row.get('event_sequence') == sequence).update(deepcopy(updates))
        return event

    def reject(self, scenario, branch):
        with self.assertRaises((audit.DeadlineRecoveryAuditError, KeyError, IndexError, TypeError)):
            audit.audit_branch(scenario, branch)

    def test_actual_late_offer_is_auditable(self):
        checked = audit.audit_branch(deepcopy(self.scenario), deepcopy(self.branch))
        self.assertTrue(checked['passed'])
        self.assertEqual(checked['counts']['late_offer_accepted'], 1)
        self.assertEqual(checked['counts']['callback_acceptance_proofs'], 1)
        self.assertEqual(checked['counts']['late_offer_callbacks'], 1)
        self.assertFalse(checked['actual_optimality_claimed'])
        self.assertEqual([row['treatment_start_t'] for row in self.branch['patient_outcomes']], [8.25, 3.5])

    def test_all_eight_branches_auditable(self):
        result = run_deadline_recovery_replay(deepcopy(self.scenario))
        checked = audit.audit_deadline_recovery_replay(result)
        self.assertEqual(checked['branches'], 8)
        self.assertEqual(checked['counts']['late_offer_accepted'], 2)

    def test_original_scarce_gain_is_unchanged(self):
        scenario = make_scenario(8, 'scarce_options_staggered_replies')
        branch = run_deadline_recovery_branch(scenario, late_reply_recovery=True)
        self.assertTrue(audit.audit_branch(scenario, branch)['passed'])
        self.assertEqual([row['treatment_start_t'] for row in branch['patient_outcomes']], [5.5, 3.5])

    def test_outside_window_is_rejected_with_actual_receipt(self):
        for family in ('deadline_reply_at_350', 'deadline_reply_at_375'):
            with self.subTest(family=family):
                scenario = make_scenario(8, family)
                branch = run_deadline_recovery_branch(scenario, late_reply_recovery=True)
                checked = audit.audit_branch(scenario, branch)
                self.assertEqual(checked['counts']['late_offer_accepted'], 0)
                self.assertEqual(branch['late_offer_records'][0]['result']['reason'], 'OUTSIDE_WINDOW')

    def test_silent_completion_has_no_callback_receipt(self):
        scenario = make_scenario(8, 'deadline_silent_alternative')
        branch = run_deadline_recovery_branch(scenario, late_reply_recovery=True)
        checked = audit.audit_branch(scenario, branch)
        self.assertEqual(checked['counts']['late_offer_receipts'], 0)
        self.assertTrue(any(event['type'] == 'review_completed' and event.get('hospital') == 'H02'
                            for event in branch['events']))

    def test_capacity_loss_rejects_actual_hospital_reply(self):
        scenario = make_scenario(8, 'deadline_capacity_lost_before_receipt')
        branch = run_deadline_recovery_branch(scenario, late_reply_recovery=True)
        checked = audit.audit_branch(scenario, branch)
        self.assertEqual(checked['counts']['late_offer_accepted'], 0)
        self.assertEqual(branch['late_offer_records'][0]['snapshot']['receipt']['outcome'], 'decline')

    def test_current_single_candidate_opportunity_is_protected(self):
        scenario = make_scenario(8, 'deadline_later_single_candidate')
        branch = run_deadline_recovery_branch(scenario, late_reply_recovery=True)
        checked = audit.audit_branch(scenario, branch)
        self.assertEqual(checked['counts']['late_offer_accepted'], 0)
        self.assertEqual(branch['late_offer_records'][0]['result']['reason'], 'SCARCE_OPPORTUNITY')
        self.assertEqual(branch['late_offer_records'][0]['result']['protected_patients'], ['P03'])

    def test_original_timeout_cannot_be_extended(self):
        scenario, branch = self.fixture()
        self.event(branch, 'ask', match=lambda e:e['hospital'] == 'H02', timeout_at=3.25)
        self.reject(scenario, branch)

    def test_original_timeout_feedback_must_remain(self):
        scenario, branch = self.fixture()
        self.event(branch, 'inquiry_feedback', match=lambda e:e['hospital'] == 'H02', outcome='reply')
        self.reject(scenario, branch)

    def test_original_no_response_cannot_be_reclassified(self):
        scenario, branch = self.fixture()
        self.event(branch, 'no_response', match=lambda e:e['hospital'] == 'H02', type='decline')
        self.reject(scenario, branch)

    def test_registration_deadline_cannot_change(self):
        scenario, branch = self.fixture()
        self.event(branch, 'late_offer_callback_registered', timeout_at=3.25)
        self.reject(scenario, branch)

    def test_original_no_response_cannot_be_hidden(self):
        scenario, branch = self.fixture()
        self.event(branch, 'no_response', match=lambda e:e['hospital'] == 'H02', type='unknown_hidden_terminal')
        self.reject(scenario, branch)

    def test_no_queue_does_not_fabricate_late_callback(self):
        scenario = make_scenario(8, 'scarce_alternative_late_deadline')
        scenario['inquiry_queue']['enabled'] = False
        branch = run_deadline_recovery_branch(scenario, late_reply_recovery=True)
        checked = audit.audit_branch(scenario, branch)
        self.assertEqual(checked['counts']['late_offer_receipts'], 0)

    def test_registration_condition_must_match_actual_patient(self):
        scenario, branch = self.fixture()
        condition = deepcopy(branch['late_offer_callbacks'][0]['condition'])
        condition['priority'] += 1
        self.event(branch, 'late_offer_callback_registered', condition=condition,
                   condition_fingerprint=audit._fingerprint(condition))
        self.reject(scenario, branch)

    def test_registration_must_reference_actual_supplement(self):
        scenario, branch = self.fixture()
        self.event(branch, 'late_offer_callback_registered', supplement_review_event_sequence=0)
        self.reject(scenario, branch)

    def test_callback_export_must_match_raw_registration(self):
        scenario, branch = self.fixture()
        branch['late_offer_callbacks'][0]['attempt'] += 1
        self.reject(scenario, branch)

    def test_actual_hospital_completion_cannot_move(self):
        scenario, branch = self.fixture()
        self.event(branch, 'review_completed', match=lambda e:e.get('hospital') == 'H02'
                   and e.get('patient') == 'P01', t=3)
        self.reject(scenario, branch)

    def test_expired_original_late_reply_must_stay_ignored(self):
        scenario, branch = self.fixture()
        self.event(branch, 'late_reply', ignored=False)
        self.reject(scenario, branch)

    def test_receipt_cannot_be_delivered_earlier(self):
        scenario, branch = self.fixture()
        self.event(branch, 'hospital_reply_received', received_at=3)
        self.reject(scenario, branch)

    def test_receipt_cannot_claim_unsubmitted_attempt(self):
        scenario, branch = self.fixture()
        self.event(branch, 'hospital_reply_received', attempt=2)
        self.reject(scenario, branch)

    def test_received_hospital_outcome_must_match_current_capacity(self):
        scenario, branch = self.fixture()
        self.event(branch, 'hospital_reply_received', outcome='decline')
        self.reject(scenario, branch)

    def test_private_silence_cannot_produce_received_callback(self):
        scenario, branch = self.fixture()
        scenario['response_matrix']['*']['H02']['silent'] = True
        self.reject(scenario, branch)

    def test_receipt_must_reference_original_ask(self):
        scenario, branch = self.fixture()
        self.event(branch, 'hospital_reply_received', ask_event_sequence=0)
        self.reject(scenario, branch)

    def test_snapshot_receipt_must_match_raw_received_outcome(self):
        scenario, branch = self.fixture()
        branch['late_offer_records'][0]['snapshot']['receipt']['outcome'] = 'decline'
        self.reject(scenario, branch)

    def test_snapshot_current_condition_cannot_change(self):
        scenario, branch = self.fixture()
        patient = branch['late_offer_records'][0]['snapshot']['patient']
        patient['condition']['priority'] += 1
        patient['condition_fingerprint'] = audit._fingerprint(patient['condition'])
        self.reject(scenario, branch)

    def test_snapshot_current_state_is_reconstructed(self):
        scenario, branch = self.fixture()
        branch['late_offer_records'][0]['snapshot']['patient']['state'] = 'TRANSIT'
        self.reject(scenario, branch)

    def test_snapshot_latest_attempt_is_reconstructed(self):
        scenario, branch = self.fixture()
        branch['late_offer_records'][0]['snapshot']['patient']['latest_attempt'] += 1
        self.reject(scenario, branch)

    def test_snapshot_first_commitment_is_reconstructed(self):
        scenario, branch = self.fixture()
        branch['late_offer_records'][0]['snapshot']['patient']['first_commit_t'] = 1
        self.reject(scenario, branch)

    def test_snapshot_resource_free_is_reconstructed(self):
        scenario, branch = self.fixture()
        branch['late_offer_records'][0]['snapshot']['resources']['ed_bed']['free'] += 1
        self.reject(scenario, branch)

    def test_snapshot_resource_source_is_reconstructed(self):
        scenario, branch = self.fixture()
        branch['late_offer_records'][0]['snapshot']['resources']['ed_bed']['observed_at'] = 3.25
        self.reject(scenario, branch)

    def test_snapshot_valid_offers_keep_actual_source(self):
        scenario, branch = self.fixture()
        branch['late_offer_records'][0]['snapshot']['patient']['valid_offers'][0]['expires_at'] += 1
        self.reject(scenario, branch)

    def test_snapshot_no_future_patient_allowed(self):
        scenario, branch = self.fixture()
        rows = branch['late_offer_records'][0]['snapshot']['registered_search_patients']
        fake = deepcopy(rows[0]); fake['id'] = 'FUTURE'; fake['condition']['id'] = 'FUTURE'
        fake['request_t'] = 10
        rows.append(fake)
        self.reject(scenario, branch)

    def test_opportunity_cannot_omit_current_single_candidate(self):
        scenario = make_scenario(8, 'deadline_later_single_candidate')
        branch = run_deadline_recovery_branch(scenario, late_reply_recovery=True)
        snapshot = branch['late_offer_records'][0]['snapshot']
        snapshot['registered_search_patients'] = [row for row in snapshot['registered_search_patients'] if row['id'] != 'P03']
        self.reject(scenario, branch)

    def test_opportunity_original_pending_handle_cannot_be_renewed(self):
        scenario = make_scenario(8, 'deadline_later_single_candidate')
        branch = run_deadline_recovery_branch(scenario, late_reply_recovery=True)
        other = next(row for row in branch['late_offer_records'][0]['snapshot']['registered_search_patients'] if row['id'] == 'P03')
        other['pending_inquiries'][0]['timeout_at'] += 1
        self.reject(scenario, branch)

    def test_policy_result_cannot_extend_window(self):
        scenario, branch = self.fixture()
        branch['late_offer_records'][0]['result']['expires_at'] += 1
        self.reject(scenario, branch)

    def test_new_offer_lifetime_must_start_at_actual_receipt(self):
        scenario, branch = self.fixture()
        self.event(branch, 'late_offer_accepted', expires_at=10)
        self.reject(scenario, branch)

    def test_adoption_must_link_actual_receipt(self):
        scenario, branch = self.fixture()
        self.event(branch, 'late_offer_accepted', receipt_event_sequence=0)
        self.reject(scenario, branch)

    def test_ordinary_late_accept_must_link_unique_adoption(self):
        scenario, branch = self.fixture()
        self.event(branch, 'accept', match=lambda e:'late_offer_event_sequence' in e, late_offer_event_sequence=0)
        self.reject(scenario, branch)

    def test_late_accept_must_link_actual_receipt(self):
        scenario, branch = self.fixture()
        self.event(branch, 'accept', match=lambda e:'late_offer_event_sequence' in e, receipt_event_sequence=0)
        self.reject(scenario, branch)

    def test_callback_count_must_equal_raw_events(self):
        scenario, branch = self.fixture()
        branch['metrics']['late_offer_accepted'] += 1
        self.reject(scenario, branch)

    def test_protocol_must_keep_original_timeout(self):
        scenario, branch = self.fixture()
        branch['deadline_recovery_spec']['original_timeout_unchanged'] = False
        self.reject(scenario, branch)

    def test_disable_flag_cannot_hide_callback(self):
        scenario, branch = self.fixture()
        branch['late_reply_recovery_enabled'] = False
        self.reject(scenario, branch)

    def test_orphan_record_is_rejected(self):
        scenario, branch = self.fixture()
        branch['late_offer_records'].append(deepcopy(branch['late_offer_records'][0]))
        self.reject(scenario, branch)

    def test_missing_record_is_rejected(self):
        scenario, branch = self.fixture()
        branch['late_offer_records'].clear()
        self.reject(scenario, branch)

    def test_extra_private_future_field_is_rejected(self):
        scenario, branch = self.fixture()
        branch['late_offer_records'][0]['snapshot']['finish_t'] = 3.25
        self.reject(scenario, branch)

    def test_saved_raw_audits_without_engine_or_solver_import(self):
        payload = json.dumps(dict(scenario=self.scenario, branch=self.branch), ensure_ascii=True)
        code = '''
import importlib.abc, json, sys
class Block(importlib.abc.MetaPathFinder):
 def find_spec(self, fullname, path=None, target=None):
  if fullname.startswith('ortools') or (fullname.startswith('simulation.') and (
      fullname.endswith('_engine') or fullname.endswith('_assignment') or fullname in (
      'simulation.sim','simulation.map_replay','simulation.inquiry_queue','simulation.observation_store'))):
   raise AssertionError('forbidden execution import ' + fullname)
sys.meta_path.insert(0, Block())
from simulation.deadline_recovery_audit import audit_branch
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
