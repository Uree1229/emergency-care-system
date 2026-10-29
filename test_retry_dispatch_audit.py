"""Adversarial full-raw audit checks on structural seeds 7 and 8 only."""
from copy import deepcopy
import json
import subprocess
import sys
import unittest

from simulation import retry_dispatch_audit as audit
from simulation.retry_dispatch_engine import run_retry_dispatch_branch, run_retry_dispatch_replay
from simulation.capability_probe_engine import run_capability_probe_branch
from simulation.capability_probe_audit import audit_branch as frozen_audit_branch
from simulation.cooperative_probe_scenarios import make_scenario


class RetryDispatchRawAudit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = {}
        for family in ('fresh_control', 'shortage_probe_later_patient_fifo',
                       'cooperative_probe_busy_early_report', 'cooperative_probe_busy_attempt_vary',
                       'delayed_pessimistic_recovery', 'scarce_contact_concurrency_limits'):
            for variant in audit.VARIANTS:
                scenario = make_scenario(7, family)
                cls.cases[family, variant] = scenario, run_retry_dispatch_branch(scenario, variant=variant)

    def fixture(self, family='shortage_probe_later_patient_fifo', variant='balanced'):
        return deepcopy(self.cases[family, variant])

    def reject(self, scenario, branch):
        with self.assertRaises((audit.RetryDispatchAuditError, KeyError, IndexError, TypeError, ValueError)):
            audit.audit_branch(scenario, branch)

    def event(self, branch, kind, *, match=None, **changes):
        event = next(row for row in branch['ordered_emissions'] if row['type'] == kind
                     and (match is None or match(row)))
        event.update(deepcopy(changes))
        next(row for row in branch['events'] if row.get('event_sequence') == event['event_sequence']).update(deepcopy(changes))
        return event

    def test_all_three_ablations_audit_on_both_environment_protocols(self):
        for key, pair in self.cases.items():
            with self.subTest(key=key):
                self.assertTrue(audit.audit_branch(*deepcopy(pair))['passed'])

    def test_all_six_candidate_branches_keep_audited_allocation_rules(self):
        replay = run_retry_dispatch_replay(make_scenario(8, 'fresh_control'))
        checked = audit.audit_retry_dispatch_replay(replay)
        self.assertTrue(checked['passed'])
        self.assertEqual(checked['branches'], 6)
        self.assertEqual({row['policy']['rule'] for row in replay['branches']}, {'H1', 'C1'})

    def test_frozen_capability_reference_still_has_exact_original_proof(self):
        scenario = make_scenario(8, 'fresh_control')
        branch = run_capability_probe_branch(scenario, shortage_probe=True, capability_probe=True)
        self.assertEqual(audit.audit_branch(scenario, branch), frozen_audit_branch(scenario, branch))

    def test_audit_is_read_only(self):
        for family in ('fresh_control', 'shortage_probe_later_patient_fifo'):
            scenario, branch = self.fixture(family)
            before = deepcopy((scenario, branch))
            self.assertTrue(audit.audit_branch(scenario, branch)['passed'])
            self.assertEqual((scenario, branch), before)

    def test_unknown_or_missing_dispatch_namespace_rejected(self):
        for field in sorted(audit.NAMESPACE_FIELDS):
            scenario, branch = self.fixture()
            del branch[field]
            self.reject(scenario, branch)
        scenario, branch = self.fixture()
        branch['retry_dispatch_future_finish'] = None
        self.reject(scenario, branch)

    def test_candidate_identity_cannot_omit_entire_namespace(self):
        scenario, branch = self.fixture()
        for field in audit.NAMESPACE_FIELDS:
            del branch[field]
        self.reject(scenario, branch)

    def test_candidate_identity_must_be_the_actual_executed_policy(self):
        scenario, branch = self.fixture()
        branch['id'] = 'renamed_after_execution'
        self.reject(scenario, branch)

    def test_selector_name_and_enabled_flag_are_explicit(self):
        for value in (None, 'v4', 'unknown', False):
            scenario, branch = self.fixture()
            branch['retry_dispatch_selector'] = value
            self.reject(scenario, branch)
        for value in (1, False, None):
            scenario, branch = self.fixture()
            branch['retry_dispatch_enabled'] = value
            self.reject(scenario, branch)

    def test_declared_queue_support_cannot_change(self):
        for value in (False, 'true', 1, None):
            scenario, branch = self.fixture()
            scenario['inquiry_queue']['enabled'] = value
            self.reject(scenario, branch)

    def test_protocol_cannot_hide_added_workers_or_future_calendar(self):
        for field in ('workers_added', 'extra_resource_change_publications', 'hidden_work_calendar_visible'):
            scenario, branch = self.fixture()
            branch['retry_dispatch_spec'][field] = True
            self.reject(scenario, branch)

    def test_contract_boolean_cannot_be_equal_valued_integer(self):
        scenario, branch = self.fixture()
        branch['retry_dispatch_spec']['original_seven_inputs_retained'] = 1
        self.reject(scenario, branch)

    def test_changed_selector_cannot_claim_unchanged_parent_delegation(self):
        scenario, branch = self.fixture()
        branch['capability_probe_enabled'] = True
        self.reject(scenario, branch)
        scenario, branch = self.fixture()
        branch['retry_dispatch_foundation_spec']['selector_changed'] = False
        self.reject(scenario, branch)

    def test_internal_v4_slot_cannot_hide_the_changed_candidate_tier(self):
        scenario, branch = self.fixture()
        branch['scarce_delay_spec']['actual_selector_reused'] = True
        self.reject(scenario, branch)

    def test_original_contact_clock_cannot_be_edited_for_the_new_selector(self):
        scenario, branch = self.fixture('scarce_contact_concurrency_limits')
        row = next(row for row in branch['inquiry_decisions'] if row['inputs']['last_contact_at'] is not None)
        row['inputs']['last_contact_at'] -= 15
        self.reject(scenario, branch)

    def test_original_selector_budget_cannot_be_enlarged(self):
        scenario, branch = self.fixture()
        branch['inquiry_decisions'][0]['inputs']['max_new'] += 1
        self.reject(scenario, branch)

    def test_original_pending_ledger_cannot_hide_other_patient_load(self):
        scenario, branch = self.fixture('scarce_contact_concurrency_limits')
        row = next(row for row in branch['inquiry_decisions'] if row['inputs']['pending'])
        row['inputs']['pending'].clear()
        self.reject(scenario, branch)

    def test_original_selector_inputs_cannot_gain_hidden_environment_fields(self):
        scenario, branch = self.fixture()
        branch['inquiry_decisions'][0]['inputs']['remaining_work'] = 0
        self.reject(scenario, branch)

    def test_dispatch_records_cannot_delete_or_duplicate_a_review(self):
        for duplicate in (False, True):
            scenario, branch = self.fixture()
            if duplicate:
                branch['retry_dispatch_records'].append(deepcopy(branch['retry_dispatch_records'][0]))
            else:
                del branch['retry_dispatch_records'][0]
            self.reject(scenario, branch)

    def test_dispatch_decision_cannot_differ_from_actual_inquiry_decision(self):
        scenario, branch = self.fixture()
        branch['retry_dispatch_records'][0]['decision']['reason'] = 'fabricated'
        self.reject(scenario, branch)

    def test_context_cannot_gain_future_or_unknown_input_fields(self):
        scenario, branch = self.fixture()
        branch['retry_dispatch_records'][0]['context']['actual_worker_finish_t'] = 2
        self.reject(scenario, branch)

    def test_context_cannot_erase_a_past_contact_across_episodes(self):
        scenario, branch = self.fixture('scarce_contact_concurrency_limits')
        row = next(row for row in branch['retry_dispatch_records'] if row['context']['contacts'])
        row['context']['contacts'].pop()
        self.reject(scenario, branch)

    def test_exported_contact_ledger_cannot_erase_a_timeout_burden(self):
        scenario, branch = self.fixture('scarce_contact_concurrency_limits')
        branch['retry_dispatch_contacts'].pop()
        self.reject(scenario, branch)

    def test_exported_contact_ledger_cannot_duplicate_a_contact(self):
        scenario, branch = self.fixture()
        branch['retry_dispatch_contacts'].append(deepcopy(branch['retry_dispatch_contacts'][0]))
        self.reject(scenario, branch)

    def test_contact_attempts_cannot_reset_across_episodes(self):
        scenario, branch = self.fixture('scarce_contact_concurrency_limits')
        self.event(branch, 'ask', match=lambda row: row['attempt'] > 1, attempt=1)
        self.reject(scenario, branch)

    def test_contact_resources_cannot_claim_a_new_report_quantity(self):
        scenario, branch = self.fixture()
        row = branch['retry_dispatch_contacts'][0]
        resource = next(iter(row['resources']))
        row['resources'][resource]['capacity'] += 1
        self.reject(scenario, branch)

    def test_context_universe_cannot_omit_a_less_contacted_hospital(self):
        scenario, branch = self.fixture()
        row = next(row for row in branch['retry_dispatch_records'] if row['context']['universe'])
        row['context']['universe'].pop()
        self.reject(scenario, branch)

    def test_context_universe_cannot_add_a_hidden_shortcut(self):
        scenario, branch = self.fixture()
        row = next(row for row in branch['retry_dispatch_records'] if row['context']['universe'])
        row['context']['universe'][0]['travel_minutes'] = 0
        self.reject(scenario, branch)

    def test_context_episode_contacts_cannot_be_reset(self):
        scenario, branch = self.fixture('scarce_contact_concurrency_limits')
        row = next(row for row in branch['retry_dispatch_records'] if row['context']['episode']['contacts'])
        row['context']['episode']['contacts'] = 0
        self.reject(scenario, branch)

    def test_first_acceptance_cannot_be_erased_to_reactivate_initial_scope(self):
        scenario, branch = self.fixture('scarce_contact_concurrency_limits')
        row = next(row for row in branch['retry_dispatch_records']
                   if row['context']['patient']['first_accept_event_sequence'] is not None)
        row['context']['patient']['first_accept_event_sequence'] = None
        self.reject(scenario, branch)

    def test_past_wake_cannot_be_removed_from_later_context(self):
        scenario, branch = self.fixture('scarce_contact_concurrency_limits', 'paced')
        row = next(row for row in branch['retry_dispatch_records'] if row['context']['retry_barriers'])
        row['context']['retry_barriers'].pop()
        self.reject(scenario, branch)

    def test_original_raw_wake_deadline_cannot_be_changed_only_in_dispatch_export(self):
        scenario, branch = self.fixture('scarce_contact_concurrency_limits', 'paced')
        row = next(row for row in branch['retry_dispatch_wakes'] if row['previous_retry_at'] is not None)
        row['previous_retry_at'] += .25
        self.reject(scenario, branch)

    def test_wake_report_reference_cannot_point_to_a_future_or_unrelated_packet(self):
        scenario, branch = self.fixture('scarce_contact_concurrency_limits', 'paced')
        branch['retry_dispatch_wakes'][0]['report_event_sequence'] = len(branch['ordered_emissions']) + 1
        self.reject(scenario, branch)

    def test_wake_public_quantity_cannot_be_fabricated_to_release_a_wait(self):
        scenario, branch = self.fixture('scarce_contact_concurrency_limits', 'paced')
        row = branch['retry_dispatch_wakes'][0]
        resource = next(iter(row['resources']))
        row['resources'][resource]['used'] += 1
        self.reject(scenario, branch)

    def test_selector_evidence_cannot_rewrite_the_actual_kernel_choice(self):
        scenario, branch = self.fixture()
        row = branch['retry_dispatch_records'][0]
        row['decision']['evidence']['retry_dispatch']['kernel_candidate_ids'] = []
        self.reject(scenario, branch)

    def test_condition_fingerprint_cannot_be_reused_for_a_different_patient(self):
        scenario, branch = self.fixture()
        branch['retry_dispatch_records'][0]['context']['patient']['condition']['id'] = 'OTHER'
        self.reject(scenario, branch)

    def test_cooperative_busy_contact_cannot_consume_clinical_response_ordinal(self):
        scenario, branch = self.fixture('cooperative_probe_busy_attempt_vary')
        self.event(branch, 'ask', match=lambda row: row.get('cooperative_job_id') is None, clinical_attempt=1)
        self.reject(scenario, branch)

    def test_actual_service_work_cannot_be_reduced(self):
        scenario, branch = self.fixture()
        self.event(branch, 'cooperative_job_submitted', service_minutes=.25)
        self.reject(scenario, branch)

    def test_raw_worker_cannot_be_added(self):
        scenario, branch = self.fixture()
        self.event(branch, 'cooperative_job_started', worker=99)
        self.reject(scenario, branch)

    def test_full_physical_job_ledger_cannot_be_removed(self):
        scenario, branch = self.fixture()
        branch['cooperative_job_ledger'].clear()
        self.reject(scenario, branch)

    def test_independent_environment_cannot_gain_queue_controls(self):
        scenario, branch = self.fixture('fresh_control')
        branch['cooperative_probe_enabled'] = False
        self.reject(scenario, branch)

    def test_patient_endpoint_and_total_wait_metrics_remain_independently_audited(self):
        scenario, branch = self.fixture()
        branch['patient_outcomes'][0]['treatment_start_t'] -= 1
        self.reject(scenario, branch)
        scenario, branch = self.fixture()
        branch['metrics']['inquiries'] -= 1
        self.reject(scenario, branch)

    def test_dispatch_metrics_cannot_hide_a_review(self):
        scenario, branch = self.fixture()
        branch['metrics']['retry_dispatch_reviews'] -= 1
        self.reject(scenario, branch)

    def test_independent_auditor_imports_no_replay_engine_or_solver(self):
        code = (
            'import sys,json; import simulation.retry_dispatch_audit; '
            'from simulation.retry_dispatch_policy import decide; '
            'print(json.dumps([n for n in sys.modules if n.endswith("_engine") '
            'or n.startswith("ortools") or n.endswith("cp_sat_assignment")]))')
        result = subprocess.run([sys.executable, '-B', '-c', code], capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout), [])


if __name__ == '__main__':
    unittest.main()
