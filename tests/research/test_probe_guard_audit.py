"""Seed-7 full-raw audit and adversarial provenance checks; no DEV/VAL runs."""
from copy import deepcopy
import importlib.util
import json
import subprocess
import sys
import unittest

from simulation import probe_guard_audit as audit
from simulation.probe_guard_engine import run_probe_guard_branch
from simulation.recovery_rank_engine import run_recovery_rank_branch
from simulation.recovery_rank_scenarios import make_scenario


class ProbeGuardRawAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = {}
        for family in ('recovery_dispatch_duplicate_recovery_feeds',
                'shortage_probe_release_before_reply', 'out_of_order',
                'cooperative_probe_busy_admission', 'recovery_dispatch_future_single_candidate',
                'recovery_rank_same_shortage_refresh', 'recovery_dispatch_same_shortage_refresh'):
            for variant in audit.VARIANTS:
                scenario = make_scenario(7, family)
                cls.cases[family, variant] = scenario, run_probe_guard_branch(scenario, variant=variant)

    def fixture(self, variant='renewed', family='recovery_dispatch_duplicate_recovery_feeds'):
        return deepcopy(self.cases[family, variant])

    def reject(self, scenario, branch, component=False):
        with self.assertRaises((audit.ProbeGuardAuditError, ValueError, KeyError, TypeError, IndexError)):
            (audit.audit_probes if component else audit.audit_branch)(scenario, branch)

    def event(self, branch, sequence, **changes):
        for rows in (branch['events'], branch['ordered_emissions']):
            next(row for row in rows if row.get('event_sequence') == sequence).update(deepcopy(changes))

    def selected(self, branch):
        return next(row for row in branch['probe_guard_records'] if row['shadow_decision']['send'])

    def delegated(self):
        return self.fixture(family='shortage_probe_release_before_reply')

    def test_full_workflows_both_variants_without_projection(self):
        for key, pair in self.cases.items():
            with self.subTest(case=key):
                self.assertTrue(audit.audit_branch(*deepcopy(pair))['passed'])

    @unittest.skipUnless(importlib.util.find_spec('ortools'), 'requires project .solver_deps on PYTHONPATH')
    def test_seed7_c1_full_raw_allocation(self):
        scenario = make_scenario(7, 'recovery_dispatch_duplicate_recovery_feeds')
        for variant in audit.VARIANTS:
            branch = run_probe_guard_branch(scenario, allocation='C1', variant=variant)
            self.assertTrue(audit.audit_branch(scenario, branch)['passed'])

    def test_read_only_original_inputs_and_raw_branch(self):
        scenario, branch = self.fixture()
        before = deepcopy((scenario, branch))
        audit.audit_branch(scenario, branch)
        self.assertEqual((scenario, branch), before)

    def test_exact_frozen_reference_delegation(self):
        scenario = make_scenario(7, 'out_of_order')
        branch = run_recovery_rank_branch(scenario, variant='normal')
        self.assertEqual(audit.audit_branch(scenario, branch), audit.rank.audit_branch(scenario, branch))

    def test_actual_veto_witness_has_no_hidden_optional_clinical_job(self):
        scenario, branch = self.fixture('off')
        checked = audit.audit_branch(scenario, branch)
        self.assertGreater(branch['metrics']['probe_guard_shadow_selected'], 0)
        self.assertEqual(checked['counts']['shortage_probe_sent'], 0)
        self.assertFalse(any(row['work_class'] == 'probe' for row in branch['cooperative_job_ledger']))

    def test_actual_delegate_keeps_real_attempt_and_service(self):
        scenario, branch = self.delegated()
        checked = audit.audit_branch(scenario, branch)
        self.assertGreater(checked['counts']['shortage_probe_sent'], 0)
        asks = [event for event in branch['ordered_emissions']
            if event['type'] == 'ask' and 'shortage_probe_review_event_sequence' in event]
        self.assertTrue(asks)
        guard = self.selected(branch)['guard_decision']
        self.assertEqual(guard['selected_bed_observed_at'], guard['first_ask_submitted_at'])
        self.assertEqual(guard['action'], 'delegate')
        for ask in asks:
            job = next(row for row in branch['cooperative_job_ledger']
                if row['job_id'] == ask['cooperative_job_id'])
            self.assertEqual(job['clinical_attempt'], ask['clinical_attempt'])
            self.assertEqual(job['work_class'], 'probe')

    def test_every_actual_namespace_field_is_required(self):
        for key in audit.NAMESPACE_FIELDS:
            with self.subTest(key=key):
                scenario, branch = self.fixture()
                del branch[key]
                self.reject(scenario, branch)

    def test_entire_namespace_cannot_be_hidden(self):
        scenario, branch = self.fixture()
        for key in audit.NAMESPACE_FIELDS:
            del branch[key]
        self.reject(scenario, branch)

    def test_unknown_namespace_or_future_contract_rejected(self):
        scenario, branch = self.fixture()
        branch['probe_guard_future_reply'] = 0.5
        self.reject(scenario, branch)
        scenario, branch = self.fixture()
        branch['probe_guard_spec']['future_reply'] = 0.5
        self.reject(scenario, branch)

    def test_enabled_and_variant_are_exact_actual_identity(self):
        for key, value in (('probe_guard_enabled', 1), ('probe_guard_variant', 'other'),
                ('comparison_id', 'cooperative_probe_20261017')):
            scenario, branch = self.fixture()
            branch[key] = value
            self.reject(scenario, branch)

    def test_actual_wrapper_cannot_claim_the_old_class(self):
        scenario, branch = self.fixture()
        branch['probe_guard_foundation_spec']['actual_replays']['cooperative_queue'] = (
            'simulation.recovery_rank_engine.CooperativeNormalRecoveryRankReplay')
        self.reject(scenario, branch)

    def test_wrapper_cannot_claim_normal_selector_changed(self):
        scenario, branch = self.fixture()
        branch['probe_guard_foundation_spec']['actual_normal_selector_changed'] = True
        self.reject(scenario, branch)

    def test_inherited_base_contract_bound(self):
        for key in ('comparison_id', 'recovery_rank_selector', 'shortage_probe_spec'):
            scenario, branch = self.fixture()
            branch['probe_guard_base_contract'].pop(key)
            self.reject(scenario, branch)

    def test_inherited_contract_cannot_be_an_actual_protocol_claim(self):
        scenario, branch = self.fixture()
        branch['shortage_probe_spec'] = deepcopy(branch['probe_guard_base_contract']['shortage_probe_spec'])
        self.reject(scenario, branch)

    def test_actual_policy_identity_and_normal_rank_are_bound(self):
        for change in ('name', 'rule', 'rank'):
            scenario, branch = self.fixture()
            if change == 'name': branch['policy']['name'] = 'another_execution'
            elif change == 'rule': branch['policy']['rule'] = 'unknown'
            else: branch['recovery_rank_variant'] = 'latched'
            self.reject(scenario, branch)

    def test_missing_guard_record_or_reordered_records_rejected(self):
        scenario, branch = self.fixture()
        branch['probe_guard_records'].pop()
        self.reject(scenario, branch, component=True)
        scenario, branch = self.fixture()
        branch['probe_guard_records'].reverse()
        self.reject(scenario, branch, component=True)

    def test_missing_guard_marker_does_not_erase_due_veto(self):
        scenario, branch = self.fixture()
        sequence = self.selected(branch)['event_sequence']
        self.event(branch, sequence, type='event_review_cycle')
        self.reject(scenario, branch, component=True)

    def test_guard_marker_variant_and_schema_bound(self):
        for changes in ({'variant': 'off'}, {'future_reply': 0.5}):
            scenario, branch = self.fixture()
            self.event(branch, self.selected(branch)['event_sequence'], **changes)
            self.reject(scenario, branch, component=True)

    def test_veto_cannot_be_changed_to_a_nonexecuted_delegation(self):
        scenario, branch = self.fixture()
        self.selected(branch)['guard_decision']['action'] = 'delegate'
        self.reject(scenario, branch, component=True)

    def test_future_public_snapshot_fields_rejected(self):
        scenario, branch = self.fixture()
        self.selected(branch)['snapshot']['future_worker_free_at'] = 3
        self.reject(scenario, branch, component=True)

    def test_first_ask_clock_is_actual_raw_submission(self):
        scenario, branch = self.fixture()
        self.selected(branch)['snapshot']['patient']['first_ask']['submitted_at'] = 1
        self.reject(scenario, branch, component=True)

    def test_report_source_and_receipt_times_rebuilt_from_raw(self):
        for key in ('observed_at', 'received_at'):
            scenario, branch = self.fixture()
            row = self.selected(branch)
            target = row['guard_decision']['selected_hospital']
            candidate = next(c for c in row['snapshot']['candidates'] if c['hospital'] == target)
            candidate['resources']['ed_bed'][key] = 999
            self.reject(scenario, branch, component=True)

    def test_raw_source_time_fabrication_reaches_lower_information_audit(self):
        scenario, branch = self.fixture()
        event = next(event for event in branch['ordered_emissions']
            if event['type'] == 'resource_report_received' and event.get('hospital') == 'H01'
            and event['t'] == 1)
        self.event(branch, event['event_sequence'], observed_at=999)
        self.reject(scenario, branch)

    def test_registered_patients_and_own_pending_are_full_history(self):
        for part in ('registered_search_patients', 'pending_inquiries'):
            scenario, branch = self.fixture()
            row = self.selected(branch)
            if part == 'registered_search_patients': row['snapshot'][part] = []
            else: row['snapshot']['patient'][part] = []
            self.reject(scenario, branch, component=True)

    def test_shadow_policy_is_independently_reconstructed(self):
        scenario, branch = self.fixture()
        self.selected(branch)['shadow_decision']['send'] = ['H04']
        self.reject(scenario, branch, component=True)

    def test_guard_rule_is_independently_reconstructed(self):
        for key, value in (('selected_bed_observed_at', 0), ('reason', 'ORIGINAL_PROBE_REVIEW_ALLOWED'),
                ('selected_hospital', 'H04'), ('original_send', []), ('future_reply', 0.5)):
            scenario, branch = self.fixture()
            self.selected(branch)['guard_decision'][key] = value
            self.reject(scenario, branch, component=True)

    def test_delegation_requires_the_immediate_actual_original_review(self):
        scenario, branch = self.delegated()
        guard = self.selected(branch)
        self.assertEqual(guard['guard_decision']['action'], 'delegate')
        self.event(branch, guard['event_sequence'] + 1, type='event_review_cycle')
        self.reject(scenario, branch, component=True)

    def test_original_review_cannot_change_the_delegated_shadow(self):
        scenario, branch = self.delegated()
        branch['shortage_probe_records'][0]['result']['send'] = []
        self.reject(scenario, branch, component=True)

    def test_report_wake_reopens_one_tried_hospital_without_erasing_contacts(self):
        scenario, branch = self.fixture('off', 'recovery_dispatch_same_shortage_refresh')
        guard = next(row for row in branch['probe_guard_records'] if row['snapshot']['t'] == 9.75)
        patient = guard['snapshot']['patient']
        self.assertEqual(patient['tried_hospitals'], ['H03', 'H04'])
        self.assertEqual((patient['inquiry_episode'], patient['episode_contacts']), (0, 5))
        before = [event for event in branch['ordered_emissions']
            if event['event_sequence'] < guard['event_sequence']]
        self.assertEqual(sum(event['type'] == 'ask' and event.get('hospital') == 'H02'
            for event in before), 2)
        self.assertTrue(any(event['type'] == 'resource_event_review' and event['t'] == 8
            and event['hospital'] == 'H02' for event in before))
        self.assertTrue(any(event['type'] == 'resource_event_review' and event['t'] == 9
            and event['hospital'] == 'H03' for event in before))
        self.assertTrue(any(event['type'] == 'ask' and event['t'] == 9.25
            and event['hospital'] == 'H03' for event in before))
        self.assertTrue(audit.audit_branch(scenario, branch)['passed'])

    def test_wake_reconstruction_is_exact_not_a_relaxed_tried_subset(self):
        for wrong in (['H02', 'H03', 'H04'], ['H03']):
            scenario, branch = self.fixture('off', 'recovery_dispatch_same_shortage_refresh')
            guard = next(row for row in branch['probe_guard_records'] if row['snapshot']['t'] == 9.75)
            guard['snapshot']['patient']['tried_hospitals'] = wrong
            self.reject(scenario, branch, component=True)

    def test_a_report_wake_cannot_reopen_another_hospital_in_guard_history(self):
        scenario, branch = self.fixture('off', 'recovery_dispatch_same_shortage_refresh')
        wake = next(event for event in branch['ordered_emissions']
            if event['type'] == 'resource_event_review' and event['t'] == 8 and event['hospital'] == 'H02')
        self.event(branch, wake['event_sequence'], hospital='H04')
        self.reject(scenario, branch, component=True)

    def test_extra_ask_after_a_veto_is_rejected(self):
        scenario, branch = self.fixture('off')
        guard = self.selected(branch)
        following = next(row for row in branch['ordered_emissions']
            if row['event_sequence'] == guard['event_sequence'] + 1)
        self.event(branch, following['event_sequence'], type='ask', patient='P01', hospital='H01',
            attempt=99, clinical_attempt=99, submitted_at=following['t'], timeout_at=following['t'] + 3,
            inquiry_episode=0, shortage_probe_review_event_sequence=guard['event_sequence'])
        self.reject(scenario, branch, component=True)
        self.reject(scenario, branch)

    def test_delegated_probe_cannot_be_detached_from_real_ask(self):
        scenario, branch = self.delegated()
        ask = next(row for row in branch['ordered_emissions']
            if row['type'] == 'ask' and 'shortage_probe_review_event_sequence' in row)
        self.event(branch, ask['event_sequence'], shortage_probe_review_event_sequence=99999)
        self.reject(scenario, branch, component=True)

    def test_clinical_attempt_and_actual_work_are_not_rewritten(self):
        for part in ('attempt', 'work'):
            scenario, branch = self.delegated()
            ask = next(row for row in branch['ordered_emissions']
                if row['type'] == 'ask' and 'shortage_probe_review_event_sequence' in row)
            if part == 'attempt':
                self.event(branch, ask['event_sequence'], clinical_attempt=99)
            else:
                next(job for job in branch['cooperative_job_ledger']
                    if job['job_id'] == ask['cooperative_job_id'])['service_minutes'] += 0.25
            self.reject(scenario, branch)

    def test_unchanged_normal_seven_inputs_and_episode_are_audited(self):
        for part in ('future', 'episode'):
            scenario, branch = self.fixture()
            if part == 'future': branch['inquiry_decisions'][0]['inputs']['future_reply'] = 0.5
            else: branch['inquiry_decisions'][0]['inquiry_episode'] += 1
            self.reject(scenario, branch)

    def test_every_due_cycle_and_original_order_required(self):
        scenario, branch = self.fixture()
        event = next(row for row in branch['ordered_emissions'] if row['type'] == 'shortage_probe_cycle')
        self.event(branch, event['event_sequence'], type='event_review_cycle')
        self.reject(scenario, branch, component=True)
        scenario, branch = self.fixture()
        branch['ordered_emissions'].reverse()
        self.reject(scenario, branch)

    def test_guard_metrics_match_actual_veto_delegation_and_shadow(self):
        for key in ('probe_guard_reviews', 'probe_guard_vetoes', 'probe_guard_delegations',
                'probe_guard_shadow_selected'):
            scenario, branch = self.fixture()
            branch['metrics'][key] += 1
            self.reject(scenario, branch, component=True)

    def test_replay_schema_and_actual_branch_identity(self):
        scenario, branch = self.fixture()
        result = dict(schema_version=1, experiment_id=audit.EXPERIMENT_ID, scenario=scenario,
            branches=[branch], execution_order=[branch['id']])
        self.assertTrue(audit.audit_probe_guard_replay(result)['passed'])
        result['execution_order'] = ['not-executed']
        with self.assertRaises(audit.ProbeGuardAuditError): audit.audit_probe_guard_replay(result)

    def test_engine_solver_free_auditor_import(self):
        code = ('import sys,json;import simulation.probe_guard_audit;'
            'print(json.dumps([n for n in sys.modules if n.startswith("ortools") or '
            'n.startswith("simulation.") and n.endswith("_engine")]))')
        result = subprocess.run([sys.executable, '-B', '-c', code], capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout), [])


if __name__ == '__main__': unittest.main()
