"""Capability metadata cannot hide either frozen protocol's complete evidence."""
from copy import deepcopy
import json
import subprocess
import sys
import unittest

from simulation import capability_probe_audit as audit
from simulation import cooperative_probe_audit as frozen
from simulation.capability_probe_engine import run_capability_probe_branch, run_capability_probe_replay
from simulation.cooperative_probe_scenarios import make_scenario


class CapabilityProbeRawAudit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = {}
        for family in ('shortage_probe_later_patient_fifo', 'shortage_probe_late_reply',
                       'cooperative_probe_busy_early_report', 'cooperative_probe_busy_attempt_vary',
                       'delayed_pessimistic_recovery', 'out_of_order'):
            scenario = make_scenario(7, family)
            branch = run_capability_probe_branch(scenario, shortage_probe=True, capability_probe=True)
            cls.cases[family] = scenario, branch

    def fixture(self, family='shortage_probe_later_patient_fifo'):
        return deepcopy(self.cases[family])

    def reject(self, scenario, branch):
        with self.assertRaises((audit.CapabilityProbeAuditError, KeyError, IndexError, TypeError, ValueError)):
            audit.audit_branch(scenario, branch)

    def event(self, branch, kind, *, match=None, **changes):
        event = next(row for row in branch['ordered_emissions'] if row['type'] == kind
                     and (match is None or match(row)))
        event.update(deepcopy(changes))
        next(row for row in branch['events'] if row.get('event_sequence') == event['event_sequence']).update(deepcopy(changes))
        return event

    def test_supported_mode_keeps_all_physical_work_and_normal_patient_time(self):
        scenario, branch = self.fixture()
        checked = audit.audit_branch(scenario, branch)
        self.assertTrue(checked['passed'])
        self.assertEqual(branch['capability_mode'], 'cooperative_queue')
        self.assertEqual(checked['counts']['cooperative_preemptions'], 1)
        self.assertEqual([row['treatment_start_t'] for row in branch['patient_outcomes']], [8.25, 4.25])

    def test_independent_mode_retains_real_probe_and_original_patient_gain(self):
        for family, treatment in (('out_of_order', 8), ('delayed_pessimistic_recovery', 12)):
            with self.subTest(family=family):
                scenario, branch = self.fixture(family)
                self.assertTrue(audit.audit_branch(scenario, branch)['passed'])
                self.assertEqual(branch['capability_mode'], 'independent_replies')
                self.assertTrue(any(row['result']['send'] for row in branch['shortage_probe_records']))
                patient = next(row for row in branch['patient_outcomes']
                               if row['id'] == ('P03' if family == 'delayed_pessimistic_recovery' else 'P01'))
                self.assertEqual(patient['treatment_start_t'], treatment)

    def test_audit_returns_identical_parent_proof_and_does_not_mutate_either_input(self):
        for family in ('shortage_probe_later_patient_fifo', 'out_of_order'):
            scenario, branch = self.fixture(family)
            before = deepcopy((scenario, branch))
            self.assertEqual(audit.audit_branch(scenario, branch), frozen.audit_branch(scenario, branch))
            self.assertEqual((scenario, branch), before)

    def test_all_twelve_branches_pass_with_unchanged_ten_reference_proofs(self):
        replay = run_capability_probe_replay(make_scenario(8, 'shortage_probe_release_before_reply'))
        checked = audit.audit_capability_probe_replay(replay)
        self.assertTrue(checked['passed'])
        self.assertEqual(checked['branches'], 12)
        for branch in replay['branches'][:10]:
            self.assertFalse(audit.NAMESPACE_FIELDS.intersection(branch))
            self.assertEqual(checked['branch_audits'][branch['id']], frozen.audit_branch(replay['scenario'], branch))

    def test_custom_identity_is_set_in_the_original_policy(self):
        scenario = make_scenario(8, 'out_of_order')
        branch = run_capability_probe_branch(scenario, shortage_probe=True, capability_probe=True,
                                             inquiry='v3', branch_id='my-capability', label='custom label')
        self.assertTrue(audit.audit_branch(scenario, branch)['passed'])
        self.assertEqual(branch['id'], branch['policy']['name'])
        self.assertEqual(branch['label'], 'custom label')

    def test_explicitly_disabled_queue_does_not_invent_workers(self):
        scenario = make_scenario(8, 'out_of_order')
        scenario['inquiry_queue'] = dict(enabled=False, workers=4, background_jobs=[])
        branch = run_capability_probe_branch(scenario, shortage_probe=True, capability_probe=True)
        self.assertTrue(audit.audit_branch(scenario, branch)['passed'])
        self.assertIsNone(branch['inquiry_queue_report'])

    def test_wrong_capability_mode_is_rejected_in_both_directions(self):
        for family in ('out_of_order', 'shortage_probe_later_patient_fifo'):
            scenario, branch = self.fixture(family)
            branch['capability_mode'] = ('cooperative_queue' if branch['capability_mode'] == 'independent_replies'
                                         else 'independent_replies')
            self.reject(scenario, branch)

    def test_mode_is_derived_from_declared_support_rather_than_case_name(self):
        scenario, branch = self.fixture('out_of_order')
        scenario.update(id='arbitrary', family='cooperative_probe_pause_resume')
        self.assertTrue(audit.audit_branch(scenario, branch)['passed'])

    def test_missing_or_additional_namespace_field_is_rejected(self):
        for field in sorted(audit.NAMESPACE_FIELDS):
            scenario, branch = self.fixture()
            del branch[field]
            self.reject(scenario, branch)
        scenario, branch = self.fixture()
        branch['capability_hidden_rewrite'] = False
        self.reject(scenario, branch)

    def test_default_capability_identity_cannot_omit_all_namespace_fields(self):
        scenario, branch = self.fixture()
        for field in audit.NAMESPACE_FIELDS:
            del branch[field]
        self.reject(scenario, branch)

    def test_disabled_or_nonboolean_capability_flag_is_rejected(self):
        for value in (False, 1, 'true', None):
            scenario, branch = self.fixture()
            branch['capability_probe_enabled'] = value
            self.reject(scenario, branch)

    def test_declared_queue_capability_cannot_be_nonboolean(self):
        for value in (1, 'true', None):
            scenario, branch = self.fixture()
            scenario['inquiry_queue']['enabled'] = value
            self.reject(scenario, branch)

    def test_spec_cannot_claim_worker_resource_or_policy_changes(self):
        for key in ('wrapper_policy_or_queue_change', 'resource_update_channel_added',
                    'ack_or_worker_added_when_queue_disabled'):
            scenario, branch = self.fixture()
            branch['capability_probe_spec'][key] = True
            self.reject(scenario, branch)

    def test_spec_boolean_cannot_be_equal_valued_integer(self):
        scenario, branch = self.fixture()
        branch['capability_probe_spec']['underlying_raw_branch_retained'] = 1
        self.reject(scenario, branch)

    def test_spec_cannot_add_or_remove_protocol_terms(self):
        for extra in (True, False):
            scenario, branch = self.fixture()
            if extra:
                branch['capability_probe_spec']['hidden_policy'] = False
            else:
                del branch['capability_probe_spec']['mode_source']
            self.reject(scenario, branch)

    def test_wrong_raw_source_protocol_is_rejected(self):
        for family in ('out_of_order', 'shortage_probe_later_patient_fifo'):
            scenario, branch = self.fixture(family)
            branch['comparison_id'] = 'capability_probe_20261017'
            self.reject(scenario, branch)

    def test_changed_actual_cooperative_flags_cannot_hide_work(self):
        for field in ('cooperative_probe_enabled', 'cooperative_queue_enabled'):
            scenario, branch = self.fixture()
            branch[field] = False
            self.reject(scenario, branch)

    def test_independent_branch_cannot_gain_even_empty_cooperative_raw_fields(self):
        for field in ('cooperative_job_ledger', 'cooperative_control_acks', 'cooperative_busy_retry_records',
                      'cooperative_probe_enabled', 'cooperative_queue_enabled'):
            scenario, branch = self.fixture('out_of_order')
            branch[field] = [] if field.endswith(('ledger', 'acks', 'records')) else False
            self.reject(scenario, branch)

    def test_full_safe_foundation_cannot_be_disabled(self):
        for field in ('shortage_probe_enabled', 'late_pending_guard_enabled', 'late_reply_recovery_enabled',
                      'cohort_registration_enabled', 'scarce_routing_enabled'):
            scenario, branch = self.fixture('out_of_order')
            branch[field] = False
            self.reject(scenario, branch)

    def test_policy_identity_cannot_be_relabelled_after_execution(self):
        scenario, branch = self.fixture()
        branch['id'] = 'renamed'
        self.reject(scenario, branch)

    def test_parent_still_rejects_erased_physical_work(self):
        scenario, branch = self.fixture()
        self.event(branch, 'cooperative_job_paused', elapsed_minutes=0, remaining_minutes=2.5)
        self.reject(scenario, branch)

    def test_parent_still_rejects_forged_completion_control(self):
        scenario, branch = self.fixture()
        event = self.event(branch, 'cooperative_probe_control_ack', match=lambda row: row['state'] == 'running', state='completed')
        next(row for row in branch['cooperative_control_acks'] if row['event_sequence'] == event['event_sequence'])['state'] = 'completed'
        self.reject(scenario, branch)

    def test_parent_still_rejects_busy_contact_consuming_first_clinical_response(self):
        scenario, branch = self.fixture('cooperative_probe_busy_attempt_vary')
        self.event(branch, 'ask', match=lambda row: row.get('hospital') == 'H01'
                   and row.get('cooperative_job_id') is not None, clinical_attempt=2)
        self.reject(scenario, branch)

    def test_parent_still_rejects_old_independent_probe_decision_forgery(self):
        scenario, branch = self.fixture('out_of_order')
        next(row for row in branch['shortage_probe_records'] if row['result']['send'])['result']['send'] = []
        self.reject(scenario, branch)

    def test_parent_still_rejects_changed_patient_arrival(self):
        for family in ('out_of_order', 'shortage_probe_later_patient_fifo'):
            scenario, branch = self.fixture(family)
            branch['patient_outcomes'][0]['treatment_start_t'] -= .25
            self.reject(scenario, branch)

    def test_duplicate_or_missing_execution_is_rejected(self):
        replay = run_capability_probe_replay(make_scenario(8, 'out_of_order'))
        for duplicate in (True, False):
            altered = deepcopy(replay)
            if duplicate:
                altered['execution_order'][-1] = altered['execution_order'][0]
            else:
                altered['execution_order'].pop()
            with self.assertRaises(audit.CapabilityProbeAuditError):
                audit.audit_capability_probe_replay(altered)

    def test_saved_full_raw_audits_without_engine_solver_or_queue_implementation(self):
        code = '''
import importlib.abc,json,sys
class Block(importlib.abc.MetaPathFinder):
 def find_spec(self,fullname,path=None,target=None):
  if fullname.startswith('ortools') or (fullname.startswith('simulation.') and (
      fullname.endswith('_engine') or fullname.endswith('_assignment') or fullname in (
      'simulation.sim','simulation.map_replay','simulation.inquiry_queue','simulation.observation_store'))):
   raise AssertionError('forbidden execution import '+fullname)
sys.meta_path.insert(0,Block())
from simulation.capability_probe_audit import audit_branch
for scenario,branch in json.loads(sys.stdin.read()):
 assert audit_branch(scenario,branch)['passed']
print('PASS')
'''
        payload = json.dumps([self.fixture(), self.fixture('out_of_order')], ensure_ascii=True)
        result = subprocess.run([sys.executable, '-B', '-c', code], input=payload, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'PASS')


if __name__ == '__main__':
    unittest.main()
