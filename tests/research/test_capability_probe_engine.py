"""Capability delegation preserves the selected implementation's raw branch."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from simulation.capability_probe_engine import (
    CAPABILITY_PROBE_SPEC,NAMESPACE_FIELDS,DEFAULT_CAPABILITY_PROBE_POLICIES,
    run_capability_probe_branch,run_capability_probe_replay,
)
from simulation.cooperative_probe_engine import run_cooperative_probe_branch
from simulation.shortage_probe_engine import run_shortage_probe_branch
from simulation.cooperative_probe_scenarios import make_scenario


def capability(case,**kwargs):
    return run_capability_probe_branch(case,capability_probe=True,shortage_probe=True,**kwargs)


def stable_evidence(testcase,actual,reference):
    testcase.assertEqual(set(actual),set(reference)|NAMESPACE_FIELDS)
    # Full clinical and inquiry evidence, including their actual clocks and
    # sequence references. Independent executions have different wall timings.
    for field in ('id','label','policy','patient_outcomes','ordered_emissions','events','inquiry_snapshots',
            'resource_snapshots','inquiry_decisions','late_offer_records','shortage_probe_records',
            'shortage_probe_spec','scarce_delay_spec','deadline_recovery_spec','comparison_id'):
        testcase.assertEqual(actual[field],reference[field],field)


class CapabilityDelegationTests(unittest.TestCase):
    def test_queue_enabled_delegation_retains_cooperative_controls_and_work(self):
        case=make_scenario(7,'shortage_probe_later_patient_fifo')
        for allocation in ('H1','C1'):
            actual=capability(case,allocation=allocation,branch_id='same',label='same label')
            expected=run_cooperative_probe_branch(case,allocation=allocation,shortage_probe=True,
                cooperative_probe=True,branch_id='same',label='same label')
            stable_evidence(self,actual,expected)
            for field in ('cooperative_job_ledger','cooperative_control_acks','cooperative_ready_records','cooperative_busy_retry_records'):
                self.assertEqual(actual[field],expected[field],field)
            self.assertEqual(actual['capability_mode'],'cooperative_queue')
            self.assertTrue(actual['cooperative_probe_enabled'])
            self.assertEqual([p['treatment_start_t'] for p in actual['patient_outcomes']],[8.25,4.25])

    def test_queue_disabled_restores_the_original_independent_probe(self):
        for family in ('delayed_pessimistic_recovery','out_of_order'):
            case=make_scenario(7,family)
            for allocation in ('H1','C1'):
                actual=capability(case,allocation=allocation,branch_id='same')
                expected=run_shortage_probe_branch(case,allocation=allocation,shortage_probe=True,branch_id='same')
                stable_evidence(self,actual,expected)
                self.assertEqual(actual['capability_mode'],'independent_replies')
                self.assertNotIn('cooperative_probe_enabled',actual)
                self.assertNotIn('cooperative_control_acks',actual)
                self.assertTrue(any(r['result']['send'] for r in actual['shortage_probe_records']))

    def test_queue_disabled_pessimistic_report_preserves_confirmation_and_other_patient_gain(self):
        case=make_scenario(7,'delayed_pessimistic_recovery');branch=capability(case)
        rows={p['id']:p for p in branch['patient_outcomes']}
        self.assertEqual(rows['P03']['treatment_start_t'],12)
        self.assertTrue(any(e['type']=='resource_confirmation_request' and e['t']==5 and e['hospital']=='NEAR'
            for e in branch['ordered_emissions']))
        self.assertTrue(any(e['type']=='ask' and e['t']==6 and e['patient']=='P03'
            for e in branch['ordered_emissions']))

    def test_queue_disabled_out_of_order_report_preserves_the_two_minute_gain(self):
        branch=capability(make_scenario(7,'out_of_order'))
        self.assertEqual(branch['patient_outcomes'][0]['treatment_start_t'],8)

    def test_capability_selection_uses_declared_support_not_family_or_case_identity(self):
        case=make_scenario(7,'shortage_probe_later_patient_fifo')
        case.update(id='unrelated-name',family='unrelated-family',label='arbitrary')
        self.assertEqual(capability(case)['capability_mode'],'cooperative_queue')
        case=make_scenario(7,'out_of_order');case.update(id='other',family='other')
        self.assertEqual(capability(case)['capability_mode'],'independent_replies')

    def test_an_explicitly_disabled_queue_uses_independent_replies(self):
        case=make_scenario(7,'out_of_order');case['inquiry_queue']=dict(enabled=False,workers=4,background_jobs=[])
        result=capability(case)
        self.assertEqual(result['capability_mode'],'independent_replies')
        self.assertIsNone(result['inquiry_queue_report'])

    def test_reference_branches_have_no_capability_namespace(self):
        case=make_scenario(7,'shortage_probe_late_reply')
        for cooperative in (False,True):
            actual=run_capability_probe_branch(case,shortage_probe=True,cooperative_probe=cooperative,branch_id='same')
            expected=run_cooperative_probe_branch(case,shortage_probe=True,cooperative_probe=cooperative,branch_id='same')
            self.assertFalse(NAMESPACE_FIELDS.intersection(actual))
            for field in ('patient_outcomes','ordered_emissions','inquiry_decisions','policy'):
                self.assertEqual(actual[field],expected[field])

    def test_every_delegated_top_level_value_is_returned_verbatim(self):
        # This unit stub isolates wrapper transparency. It is not a replay or
        # audit fixture and does not substitute any clinical evidence.
        case=make_scenario(7,'shortage_probe_late_reply');sentinel=dict(
            id='raw identity',label='raw label',opaque_legacy_key={'a':[1,2]},wall_seconds=object())
        original=sentinel.copy()
        with patch('simulation.capability_probe_engine.cooperative.run_cooperative_probe_branch',return_value=sentinel) as call:
            result=capability(case,branch_id='provided-before-execution',label='provided label')
        self.assertIs(result,sentinel);self.assertEqual(call.call_count,1)
        self.assertEqual(call.call_args.kwargs['branch_id'],'provided-before-execution')
        self.assertEqual(call.call_args.kwargs['label'],'provided label')
        for key,value in original.items():self.assertIs(result[key],value)

    def test_no_worker_control_or_response_is_created_in_the_independent_mode(self):
        case=make_scenario(7,'out_of_order')
        with patch('simulation.capability_probe_engine.cooperative.run_cooperative_probe_branch',side_effect=AssertionError('wrong mode')):
            result=capability(case)
        self.assertFalse(any(e['type'].startswith('cooperative_') for e in result['ordered_emissions']))

    def test_underlying_reference_namespace_cannot_be_overwritten(self):
        case=make_scenario(7,'out_of_order')
        with patch('simulation.capability_probe_engine.independent.run_shortage_probe_branch',
                return_value=dict(capability_mode='already present')):
            with self.assertRaises(ValueError):capability(case)

    def test_metadata_is_descriptive_and_detached_from_the_module_constant(self):
        result=capability(make_scenario(7,'out_of_order'))
        self.assertEqual(result['capability_probe_spec'],CAPABILITY_PROBE_SPEC)
        self.assertTrue(result['capability_probe_enabled'])
        result['capability_probe_spec']['mode_source']='changed by reader'
        self.assertEqual(CAPABILITY_PROBE_SPEC['mode_source'],'scenario.inquiry_queue.enabled')

    def test_twelve_defaults_preserve_the_previous_ten_reference_specs(self):
        from simulation.cooperative_probe_engine import DEFAULT_COOPERATIVE_PROBE_POLICIES
        self.assertEqual(len(DEFAULT_CAPABILITY_PROBE_POLICIES),12)
        for previous,current in zip(DEFAULT_COOPERATIVE_PROBE_POLICIES,DEFAULT_CAPABILITY_PROBE_POLICIES[:10]):
            self.assertEqual(current,dict(previous,capability_probe=False))
        self.assertEqual([p['id'] for p in DEFAULT_CAPABILITY_PROBE_POLICIES[-2:]],['v4_H1_capability','v4_C1_capability'])

    def test_execution_order_does_not_change_canonical_branch_order(self):
        specs=deepcopy(list(DEFAULT_CAPABILITY_PROBE_POLICIES[-2:]));ids=[p['id'] for p in specs]
        result=run_capability_probe_replay(make_scenario(7,'out_of_order'),specs,list(reversed(ids)))
        self.assertEqual([b['id'] for b in result['branches']],ids)
        self.assertEqual(result['execution_order'],list(reversed(ids)))

    def test_scenario_and_policy_rows_are_never_mutated(self):
        case=make_scenario(7,'out_of_order');specs=deepcopy(list(DEFAULT_CAPABILITY_PROBE_POLICIES[-2:]))
        before=deepcopy((case,specs));run_capability_probe_replay(case,specs)
        self.assertEqual((case,specs),before)

    def test_capability_mode_requires_explicit_complete_foundation(self):
        case=make_scenario(7,'out_of_order')
        for changes in (dict(capability_probe=1),dict(capability_probe=True),
                dict(capability_probe=True,shortage_probe=True,late_pending_guard=False),
                dict(capability_probe=True,shortage_probe=True,cohort_registration=False)):
            with self.assertRaises(ValueError):run_capability_probe_branch(case,**changes)

    def test_unknown_policy_flags_and_repeated_execution_are_rejected(self):
        case=make_scenario(7,'out_of_order');specs=deepcopy(list(DEFAULT_CAPABILITY_PROBE_POLICIES[-2:]))
        bad=deepcopy(specs);bad[0]['mode_override']='cooperative_queue'
        with self.assertRaises(ValueError):run_capability_probe_replay(case,bad)
        with self.assertRaises(ValueError):run_capability_probe_replay(case,specs,[specs[0]['id']]*2)


if __name__=='__main__':unittest.main()
