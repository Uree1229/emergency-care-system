"""Adversarial raw provenance checks; structural seeds 7 and 8 only."""
from copy import deepcopy
import json
import subprocess
import sys
import unittest

from simulation import shortage_probe_audit as audit
from simulation import deadline_safe_audit as previous
from simulation.shortage_probe_engine import run_shortage_probe_branch, run_shortage_probe_replay
from simulation.shortage_probe_scenarios import make_scenario, ADDITIONAL_FAMILIES


class ShortageProbeRawAudit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scenario = make_scenario(7, 'shortage_probe_release_before_reply')
        cls.branch = run_shortage_probe_branch(cls.scenario, shortage_probe=True)
        cls.silent_scenario = make_scenario(7, 'shortage_probe_silent')
        cls.silent_branch = run_shortage_probe_branch(cls.silent_scenario, shortage_probe=True)

    def fixture(self):
        return deepcopy(self.scenario), deepcopy(self.branch)

    def reject(self, scenario, branch):
        with self.assertRaises((audit.ShortageProbeAuditError, KeyError, IndexError, TypeError)):
            audit.audit_branch(scenario, branch)

    def sending_record(self, branch):
        return next(row for row in branch['shortage_probe_records'] if row['result']['send'])

    def event(self, branch, kind, *, match=None, **updates):
        event = next(row for row in branch['ordered_emissions'] if row['type'] == kind
                     and (match is None or match(row)))
        sequence = event['event_sequence']
        event.update(deepcopy(updates))
        next(row for row in branch['events'] if row.get('event_sequence') == sequence).update(deepcopy(updates))
        return event

    def test_actual_release_reply_and_unchanged_gate_are_auditable(self):
        checked = audit.audit_branch(deepcopy(self.scenario), deepcopy(self.branch))
        self.assertTrue(checked['passed'])
        self.assertEqual(checked['counts']['shortage_probe_sent'], 1)
        self.assertEqual(checked['counts']['explicit_shortage_checks'], 1)
        self.assertFalse(checked['actual_optimality_claimed'])
        record = self.sending_record(self.branch)
        self.assertEqual(record['snapshot']['t'], .5)
        resource = next(row for row in record['snapshot']['candidates'] if row['hospital'] == 'H01')['resources']['ed_bed']
        self.assertEqual(resource['free'], 0)
        self.assertEqual(self.branch['patient_outcomes'][0]['treatment_start_t'], 4.25)

    def test_probe_false_uses_exact_frozen_safe_audit(self):
        branch = run_shortage_probe_branch(self.scenario, shortage_probe=False)
        self.assertEqual(audit.audit_branch(self.scenario, deepcopy(branch)),
                         previous.audit_branch(self.scenario, deepcopy(branch)))

    def test_eight_branches_pass_direct_raw_audit(self):
        result = run_shortage_probe_replay(deepcopy(self.scenario))
        checked = audit.audit_shortage_probe_replay(result)
        self.assertTrue(checked['passed'])
        self.assertEqual(checked['branches'], 8)

    def test_original_benefit_and_late_recovery_remain_auditable(self):
        for family in ('scarce_options_staggered_replies', 'scarce_alternative_late_deadline',
                       'deadline_multiple_pending_boundary'):
            scenario = make_scenario(8, family)
            branch = run_shortage_probe_branch(scenario, shortage_probe=True)
            self.assertTrue(audit.audit_branch(scenario, branch)['passed'])

    def test_every_new_environment_has_independently_auditable_actual_execution(self):
        for family in ADDITIONAL_FAMILIES:
            with self.subTest(family=family):
                scenario = make_scenario(8, family)
                branch = run_shortage_probe_branch(scenario, allocation='C1', shortage_probe=True)
                self.assertTrue(audit.audit_branch(scenario, branch)['passed'])

    def test_original_shortage_cannot_be_relabelled_sufficient(self):
        scenario, branch = self.fixture()
        row = self.sending_record(branch)
        next(c for c in row['snapshot']['candidates'] if c['hospital'] == 'H01')['resources']['ed_bed']['free'] = 1
        self.reject(scenario, branch)

    def test_probe_cannot_masquerade_as_ordinary_inquiry_check(self):
        scenario, branch = self.fixture()
        self.event(branch, 'information_check', match=lambda row:row['purpose'] == 'shortage_probe', purpose='inquiry')
        self.reject(scenario, branch)

    def test_recheck_reason_is_explicit(self):
        scenario, branch = self.fixture()
        self.event(branch, 'information_check', match=lambda row:row['purpose'] == 'shortage_probe', reason=None)
        self.reject(scenario, branch)

    def test_resource_check_retains_received_shortage(self):
        scenario, branch = self.fixture()
        event = next(row for row in branch['ordered_emissions'] if row['type'] == 'information_check'
                     and row['purpose'] == 'shortage_probe')
        resources = deepcopy(event['resources']); resources['ed_bed']['used'] = 0; resources['ed_bed']['free'] = 1
        self.event(branch, 'information_check', match=lambda row:row['purpose'] == 'shortage_probe', resources=resources)
        self.reject(scenario, branch)

    def test_probe_ask_has_unique_review_source(self):
        scenario, branch = self.fixture()
        self.event(branch, 'ask', match=lambda row:'shortage_probe_review_event_sequence' in row,
                   shortage_probe_review_event_sequence=0)
        self.reject(scenario, branch)

    def test_actual_inquiry_deadline_cannot_be_extended(self):
        scenario, branch = self.fixture()
        self.event(branch, 'ask', match=lambda row:'shortage_probe_review_event_sequence' in row, timeout_at=10)
        self.reject(scenario, branch)

    def test_probe_cannot_jump_fifo_work(self):
        scenario, branch = self.fixture()
        self.event(branch, 'review_completed', match=lambda row:row.get('hospital') == 'H01', attempt=99)
        self.reject(scenario, branch)

    def test_actual_acceptance_needs_current_physical_capacity(self):
        scenario, branch = self.fixture()
        scenario['hospitals'][0]['initial_occupancy'][0]['until'] = 8
        self.reject(scenario, branch)

    def test_first_original_contact_time_is_reconstructed(self):
        scenario, branch = self.fixture()
        self.sending_record(branch)['snapshot']['patient']['first_ask']['submitted_at'] = .25
        self.reject(scenario, branch)

    def test_last_actual_contact_time_is_reconstructed(self):
        scenario, branch = self.fixture()
        self.sending_record(branch)['snapshot']['patient']['last_contact_at'] = -.5
        self.reject(scenario, branch)

    def test_original_pending_handle_cannot_be_removed(self):
        scenario, branch = self.fixture()
        self.sending_record(branch)['snapshot']['patient']['pending_inquiries'] = []
        self.reject(scenario, branch)

    def test_contact_budget_is_reconstructed(self):
        scenario, branch = self.fixture()
        self.sending_record(branch)['snapshot']['patient']['episode_contacts'] = 0
        self.reject(scenario, branch)

    def test_patient_demand_is_reconstructed(self):
        scenario, branch = self.fixture()
        self.sending_record(branch)['snapshot']['patient']['demand'] = dict(ed_bed=2)
        self.reject(scenario, branch)

    def test_patient_capabilities_are_reconstructed(self):
        scenario, branch = self.fixture()
        self.sending_record(branch)['snapshot']['patient']['required_capabilities'] = []
        self.reject(scenario, branch)

    def test_tried_hospitals_are_not_forgotten(self):
        scenario, branch = self.fixture()
        self.sending_record(branch)['snapshot']['patient']['tried_hospitals'] = []
        self.reject(scenario, branch)

    def test_registered_cohort_cannot_omit_current_patient(self):
        scenario, branch = self.fixture()
        self.sending_record(branch)['snapshot']['registered_search_patients'] = []
        self.reject(scenario, branch)

    def test_private_future_release_cannot_be_policy_input(self):
        scenario, branch = self.fixture()
        self.sending_record(branch)['snapshot']['candidates'][0]['available_at'] = 1
        self.reject(scenario, branch)

    def test_candidate_decision_is_independently_recomputed(self):
        scenario, branch = self.fixture()
        self.sending_record(branch)['result']['candidate_checks'][0]['eligible'] = False
        self.reject(scenario, branch)

    def test_protected_hospitals_are_independently_recomputed(self):
        scenario, branch = self.fixture()
        self.sending_record(branch)['result']['protected_hospitals'] = ['H01']
        self.reject(scenario, branch)

    def test_no_action_review_cannot_fabricate_send(self):
        scenario, branch = self.fixture()
        self.sending_record(branch)['result']['send'] = []
        self.reject(scenario, branch)

    def test_duplicate_probe_record_is_rejected(self):
        scenario, branch = self.fixture()
        branch['shortage_probe_records'].append(deepcopy(self.sending_record(branch)))
        self.reject(scenario, branch)

    def test_suppressed_probe_record_is_rejected(self):
        scenario, branch = self.fixture()
        branch['shortage_probe_records'].clear()
        self.reject(scenario, branch)

    def test_falsely_disabled_probe_flag_cannot_hide_evidence(self):
        scenario, branch = self.fixture()
        branch['shortage_probe_enabled'] = False
        self.reject(scenario, branch)

    def test_protocol_cannot_claim_free_oracle_information(self):
        scenario, branch = self.fixture()
        branch['shortage_probe_spec']['future_release_or_review_schedule_visible'] = True
        self.reject(scenario, branch)

    def test_counter_does_not_replace_raw_actual_asks(self):
        scenario, branch = self.fixture()
        branch['metrics']['shortage_probe_sent'] += 1
        self.reject(scenario, branch)

    def test_silent_optional_work_does_not_delay_existing_usable_offer(self):
        checked = audit.audit_branch(deepcopy(self.silent_scenario), deepcopy(self.silent_branch))
        self.assertEqual(checked['counts']['shortage_probe_nonblocking_ready'], 1)
        self.assertEqual(self.silent_branch['patient_outcomes'][0]['treatment_start_t'], 7.5)
        self.assertEqual(self.silent_branch['shortage_probe_nonblocking_records'][0]['snapshot']['patient']['pending_inquiries'][0]['timeout_at'], 3.5)

    def test_nonblocking_requires_actual_original_probe_identity(self):
        scenario, branch = deepcopy(self.silent_scenario), deepcopy(self.silent_branch)
        branch['shortage_probe_nonblocking_records'][0]['snapshot']['probe_asks'][0]['ask_event_sequence'] = 0
        self.reject(scenario, branch)

    def test_nonblocking_cannot_hide_current_pending_work(self):
        scenario, branch = deepcopy(self.silent_scenario), deepcopy(self.silent_branch)
        branch['shortage_probe_nonblocking_records'][0]['snapshot']['patient']['pending_inquiries'].clear()
        self.reject(scenario, branch)

    def test_nonblocking_requires_received_unexpired_offer(self):
        scenario, branch = deepcopy(self.silent_scenario), deepcopy(self.silent_branch)
        branch['shortage_probe_nonblocking_records'][0]['snapshot']['patient']['valid_offers'][0]['accepted_at'] = 0
        self.reject(scenario, branch)

    def test_nonblocking_rejection_or_options_cannot_be_forged(self):
        scenario, branch = deepcopy(self.silent_scenario), deepcopy(self.silent_branch)
        branch['shortage_probe_nonblocking_records'][0]['result']['options'] = ['H01']
        self.reject(scenario, branch)

    def test_nonblocking_cannot_skip_current_allocation_check(self):
        scenario, branch = deepcopy(self.silent_scenario), deepcopy(self.silent_branch)
        review_sequence = branch['shortage_probe_nonblocking_records'][0]['event_sequence']
        self.event(branch, 'information_check', match=lambda row:row['event_sequence'] == review_sequence + 1, eligible=False)
        self.reject(scenario, branch)

    def test_nonblocking_marker_has_exact_source_and_options(self):
        scenario, branch = deepcopy(self.silent_scenario), deepcopy(self.silent_branch)
        self.event(branch, 'shortage_probe_nonblocking_ready', review_event_sequence=0)
        self.reject(scenario, branch)

    def test_nonblocking_records_cannot_be_suppressed(self):
        scenario, branch = deepcopy(self.silent_scenario), deepcopy(self.silent_branch)
        branch['shortage_probe_nonblocking_records'].clear()
        self.reject(scenario, branch)

    def test_nonblocking_count_is_not_substituted_for_proof(self):
        scenario, branch = deepcopy(self.silent_scenario), deepcopy(self.silent_branch)
        branch['metrics']['shortage_probe_nonblocking_ready'] += 1
        self.reject(scenario, branch)

    def test_saved_raw_audits_without_engine_or_solver(self):
        payload = json.dumps(dict(scenario=self.scenario, branch=self.branch), ensure_ascii=True)
        code = '''
import importlib.abc,json,sys
class Block(importlib.abc.MetaPathFinder):
 def find_spec(self, fullname, path=None, target=None):
  if fullname.startswith('ortools') or (fullname.startswith('simulation.') and (
      fullname.endswith('_engine') or fullname.endswith('_assignment') or fullname in (
      'simulation.sim','simulation.map_replay','simulation.inquiry_queue','simulation.observation_store'))):
   raise AssertionError('forbidden execution import ' + fullname)
sys.meta_path.insert(0,Block())
from simulation.shortage_probe_audit import audit_branch
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
