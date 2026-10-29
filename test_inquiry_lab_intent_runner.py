"""Original workflow equivalence and explicit public identity/prefix checks."""
from copy import deepcopy
import unittest
from unittest.mock import patch
from inquiry_lab import harness, guard_runner, intent_runner, intent_policy
from server import inquiry_policies, inquiry_workflow


class IntentRunnerTests(unittest.TestCase):
    def test_old_six_arms_full_raw_equivalence_with_apply_identity_wrapper(self):
        arms=[('fixed_3','immediate'),('eat_l15_f','immediate'),('eat_residual_l15_f','immediate'),
            ('eat_guard_l15_f','immediate'),('v3_w45_h900','immediate'),('eat_l15_f','hold120')]
        for kind in ('mix','family:simultaneous_w1','family:staggered_w2','family:no_response_near'):
            with self.subTest(kind=kind):
                scenario=harness.build(kind,7);before=deepcopy(scenario);original_apply=inquiry_workflow._apply
                old=guard_runner.run_scenario(scenario,arms)
                new=intent_runner.run_scenario(scenario,arms)
                self.assertEqual(new,old);self.assertEqual(scenario,before)
                self.assertIs(inquiry_workflow._apply,original_apply)

    def test_new_context_matches_explicit_patient_and_strict_previous_decision_ID(self):
        old_decide=inquiry_policies.decide;old_apply=inquiry_workflow._apply
        registry=deepcopy(inquiry_policies._POLICIES)
        result=intent_runner.run_scenario(harness.build('mix',7),[(intent_policy.NAME,'immediate')])
        branch=result['branches'][0];previous=0;seen_first=False
        for call,decision in zip(branch['policy_calls'],branch['db_decisions']):
            context=call['inputs']['public_context']
            self.assertEqual(context['patient_id'],decision['patient_id'])
            self.assertEqual(context['event_prefix_max'],call['db_event_prefix_max'])
            self.assertEqual(context['decision_prefix_max'],previous)
            self.assertLess(previous,decision['id']);previous=decision['id']
            if not call['inputs']['patient_pending']:seen_first=True
            self.assertEqual(len(call['inputs']),8)
        self.assertTrue(seen_first);self.assertTrue(branch['policy_calls'])
        self.assertIs(inquiry_policies.decide,old_decide);self.assertIs(inquiry_workflow._apply,old_apply)
        self.assertEqual(inquiry_policies._POLICIES,registry)

    def test_apply_and_registry_are_restored_on_invalid_arm(self):
        old_decide=inquiry_policies.decide;old_apply=inquiry_workflow._apply
        registry=deepcopy(inquiry_policies._POLICIES)
        with self.assertRaises(ValueError):
            intent_runner.run_scenario(harness.build('mix',7),[('unknown','immediate')])
        self.assertIs(inquiry_policies.decide,old_decide);self.assertIs(inquiry_workflow._apply,old_apply)
        self.assertEqual(inquiry_policies._POLICIES,registry)

    def test_apply_and_read_snapshot_are_restored_after_policy_exception(self):
        old_decide=inquiry_policies.decide;old_apply=inquiry_workflow._apply
        registry=deepcopy(inquiry_policies._POLICIES)
        with patch.object(intent_policy,'decide',side_effect=RuntimeError('controlled policy error')) as failing:
            with self.assertRaisesRegex(RuntimeError,'server workflow failed'):
                intent_runner.run_scenario(harness.build('mix',7),[(intent_policy.NAME,'immediate')])
        self.assertEqual(failing.call_count,1)
        self.assertIs(inquiry_policies.decide,old_decide);self.assertIs(inquiry_workflow._apply,old_apply)
        self.assertEqual(inquiry_policies._POLICIES,registry)


if __name__=='__main__':unittest.main()
