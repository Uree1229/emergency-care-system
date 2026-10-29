"""Record-only adapter equivalence; synthetic preflight seed7 only."""
from copy import deepcopy
import unittest
from inquiry_lab import harness, runner, batch_runner, batch_policy, residual_policy
from server import inquiry_policies


class BatchRunnerTests(unittest.TestCase):
    def test_unmodified_arms_exactly_reproduce_existing_runner(self):
        harness.install(['eat_l15_f'])
        try:
            arms=[('eat_residual_l15_f','immediate'),('fixed_3','immediate'),('eat_l15_f','immediate'),('v3_w45_h900','immediate'),('eat_l15_f','hold120')]
            for kind in ('mix','family:simultaneous_w1','family:staggered_w2','family:no_response_near'):
                with self.subTest(kind=kind):
                    scenario=harness.build(kind,7);before=deepcopy(scenario)
                    from inquiry_lab import residual_runner
                    old=residual_runner.run_scenario(scenario,arms)
                    new=batch_runner.run_scenario(scenario,arms)
                    self.assertEqual(scenario,before)
                    self.assertEqual(new['initial_snapshot_sha256'],old['initial_snapshot_sha256'])
                    for a,b in zip(old['branches'],new['branches']):
                        for key in a:self.assertEqual(a[key],b[key],(kind,a['policy'],key))
        finally:harness.uninstall()

    def test_candidate_records_full_inputs_and_restores_registry(self):
        old_decide=inquiry_policies.decide;old_registry=deepcopy(inquiry_policies._POLICIES)
        scenario=harness.build('mix',7)
        result=batch_runner.run_scenario(scenario,[(batch_policy.NAME,'immediate')])
        self.assertIs(inquiry_policies.decide,old_decide)
        self.assertEqual(inquiry_policies._POLICIES,old_registry)
        branch=result['branches'][0]
        self.assertTrue(branch['policy_calls'])
        self.assertEqual(len(branch['policy_calls']),len(branch['db_decisions']))
        for call,decision in zip(branch['policy_calls'],branch['db_decisions']):
            self.assertEqual(set(call['inputs']),{'now','candidates','observations','pending','patient_pending','max_new','last_contact_at'})
            self.assertEqual(call['patient_id'],decision['patient_id'])
            self.assertEqual(call['at']+1800000000,call['inputs']['now'])
            self.assertEqual(call['output']['evidence'],decision['evidence'])

    def test_invalid_arms_restore_registry_even_on_failure(self):
        old=inquiry_policies.decide;registry=deepcopy(inquiry_policies._POLICIES)
        with self.assertRaises(ValueError):
            batch_runner.run_scenario(harness.build('mix',7),[('unknown','immediate')])
        self.assertIs(inquiry_policies.decide,old)
        self.assertEqual(inquiry_policies._POLICIES,registry)


if __name__=='__main__':unittest.main()
