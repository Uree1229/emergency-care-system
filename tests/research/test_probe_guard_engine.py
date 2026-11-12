"""Seed-7 structural smoke checks, separate from performance evaluation."""
from copy import deepcopy
from types import SimpleNamespace
import unittest

from simulation.probe_guard_engine import (BASE_CONTRACT_FIELDS, EXPERIMENT_ID,
    NAMESPACE_FIELDS, ProbeGuardMixin, run_probe_guard_branch)
from simulation.probe_guard_policy import decide_probe_guard
from simulation.recovery_rank_engine import run_recovery_rank_branch
from simulation.recovery_rank_scenarios import make_scenario
from simulation.shortage_probe_engine import decide_shortage_probe


class ProbeGuardEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.case = make_scenario(7, 'recovery_dispatch_duplicate_recovery_feeds')
        cls.before = deepcopy(cls.case)
        cls.branches = {variant: run_probe_guard_branch(cls.case, variant=variant)
            for variant in ('off', 'renewed')}
        cls.delegate_case = make_scenario(7, 'shortage_probe_release_before_reply')
        cls.delegate = run_probe_guard_branch(cls.delegate_case, variant='renewed')
        cls.independent_case = make_scenario(7, 'out_of_order')
        cls.independent = run_probe_guard_branch(cls.independent_case, variant='renewed')

    def test_wrapper_preserves_original_scenario(self):
        self.assertEqual(self.case, self.before)

    def test_actual_wrapper_identity_and_inherited_contract_are_distinct(self):
        for variant, branch in self.branches.items():
            self.assertEqual({key for key in branch if key.startswith('probe_guard_')}, NAMESPACE_FIELDS)
            self.assertEqual(branch['comparison_id'], EXPERIMENT_ID)
            self.assertEqual(branch['probe_guard_variant'], variant)
            foundation = branch['probe_guard_foundation_spec']
            self.assertFalse(foundation['actual_normal_selector_changed'])
            self.assertEqual(foundation['mode'], 'cooperative_queue')
            self.assertIn('simulation.probe_guard_engine.CooperativeProbeGuardReplay',
                foundation['actual_replays'].values())
            base = branch['probe_guard_base_contract']
            self.assertEqual(base['recovery_rank_selector'], 'recovery_rank_normal')
            self.assertEqual(branch['recovery_rank_variant'], 'normal')
            for key in BASE_CONTRACT_FIELDS:
                if key != 'comparison_id':
                    self.assertNotIn(key, branch)

    def test_every_guard_record_contains_full_unmodified_original_snapshot(self):
        for branch in (*self.branches.values(), self.delegate, self.independent):
            events = {row['event_sequence']: row for row in branch['ordered_emissions']}
            for row in branch['probe_guard_records']:
                event = events[row['event_sequence']]
                self.assertEqual(event['type'], 'probe_guard_review')
                self.assertEqual(row['shadow_decision'], decide_shortage_probe(row['snapshot']))
                self.assertEqual(row['guard_decision'], decide_probe_guard(
                    branch['probe_guard_variant'], snapshot=row['snapshot']))
                self.assertEqual(event['t'], row['snapshot']['t'])

    def test_veto_has_no_probe_review_ask_or_clinical_probe_job(self):
        for branch in self.branches.values():
            self.assertTrue(branch['probe_guard_records'])
            self.assertGreater(branch['metrics']['probe_guard_shadow_selected'], 0)
            self.assertEqual(branch['shortage_probe_records'], [])
            self.assertEqual(branch['metrics']['shortage_probe_sent'], 0)
            self.assertFalse(any(row['type'] == 'ask' and 'shortage_probe_review_event_sequence' in row
                for row in branch['ordered_emissions']))
            self.assertFalse(any(row['work_class'] == 'probe' for row in branch['cooperative_job_ledger']))

    def test_delegate_calls_real_parent_review_with_literal_shadow_input_and_decision(self):
        branch = self.delegate
        events = {row['event_sequence']: row for row in branch['ordered_emissions']}
        selected = [row for row in branch['probe_guard_records'] if row['shadow_decision']['send']
            and row['guard_decision']['action'] == 'delegate']
        self.assertTrue(selected)
        for guard in selected:
            event = events[guard['event_sequence'] + 1]
            self.assertEqual(event['type'], 'shortage_probe_review')
            actual = branch['shortage_probe_records'][event['record_index']]
            self.assertEqual(actual['snapshot'], guard['snapshot'])
            self.assertEqual(actual['result'], guard['shadow_decision'])

    def test_original_normal_seven_inputs_and_rank_decisions_are_retained(self):
        for branch in self.branches.values():
            self.assertEqual(len(branch['inquiry_decisions']), len(branch['recovery_rank_records']))
            for actual, rank in zip(branch['inquiry_decisions'], branch['recovery_rank_records'], strict=True):
                self.assertEqual(len(actual['inputs']), 7)
                self.assertEqual(actual['decision'], rank['decision'])
                self.assertEqual(actual['decision']['evidence']['policy'], 'recovery_rank_normal')

    def test_contact_deadline_episode_and_workers_remain_original(self):
        for branch in self.branches.values():
            for event in branch['ordered_emissions']:
                if event['type'] == 'ask':
                    self.assertEqual(event['timeout_at'] - event['submitted_at'], 3)
            for row in branch['retry_dispatch_records']:
                self.assertLessEqual(row['context']['episode']['contacts'], 10)
            self.assertEqual(branch['inquiry_queue_report']['aggregate']['workers'], 4)
            self.assertFalse(branch['resource_updates_enabled'])

    def test_queue_disabled_uses_independent_foundation_without_invented_control(self):
        self.assertEqual(self.independent['probe_guard_foundation_spec']['mode'], 'independent_replies')
        self.assertFalse(any(row['type'].startswith('cooperative_') for row in self.independent['ordered_emissions']))
        self.assertNotIn('cooperative_probe_spec', self.independent['probe_guard_base_contract'])

    def test_no_due_probe_keeps_actual_endpoints_and_normal_decisions(self):
        reference = run_recovery_rank_branch(self.independent_case, variant='normal')
        self.assertEqual(self.independent['patient_outcomes'], reference['patient_outcomes'])
        self.assertEqual([row['decision'] for row in self.independent['inquiry_decisions']],
            [row['decision'] for row in reference['inquiry_decisions']])

    def test_names_are_assigned_before_execution_and_exports_are_detached(self):
        branch = run_probe_guard_branch(self.independent_case, branch_id='unit_guard', label='Unit guard')
        self.assertEqual((branch['id'], branch['policy']['name'], branch['label']),
            ('unit_guard', 'unit_guard', 'Unit guard'))
        branch['probe_guard_spec']['variants'].append('invalid')
        self.assertEqual(self.branches['off']['probe_guard_spec']['variants'], ['off', 'renewed'])

    def test_due_gate_does_not_change_patient_or_ledger_state(self):
        gate = object.__new__(ProbeGuardMixin)
        gate._episode_ids = {}
        gate._probe_counts = {}
        gate._probe_first_asks = {'P': {'submitted_at': 0}}
        gate._last_contact_t = {'P': 0}
        gate._episode_contacts = {'P': 1}
        patient = SimpleNamespace(id='P', state='SEARCH', t_commit=None, inquiries=1, asks=[{'hid': 'A'}])
        before = deepcopy((gate.__dict__, patient.__dict__))
        self.assertFalse(gate._probe_guard_review_due(patient, .25))
        self.assertTrue(gate._probe_guard_review_due(patient, .5))
        self.assertEqual((gate.__dict__, patient.__dict__), before)

    def test_unknown_variant_allocation_and_latched_foundation_are_rejected(self):
        for change in (dict(variant='eager'), dict(allocation='R1')):
            with self.assertRaises(ValueError):
                run_probe_guard_branch(self.case, **change)
        with self.assertRaises(ValueError):
            ProbeGuardMixin(rank_variant='latched')


if __name__ == '__main__':
    unittest.main()
