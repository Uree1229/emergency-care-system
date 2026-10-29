"""Unit-namespace generator -> four arms -> recorded-event guard audit."""
from copy import deepcopy
import unittest
from simulation import final_assignment_scenarios_20261028 as generator
from simulation import final_assignment_engine_20261028 as reference
from simulation import arrival_guard_engine_20261028 as engine
from simulation import arrival_guard_audit_20261028 as auditor

def check(test,stratum):
    seed=generator.seed_for('unit',stratum,2026102801)
    scenario=generator.build_case(stratum,seed);before=deepcopy(scenario)
    bundle=engine.run_comparison(scenario,include_controls=True)
    result=auditor.audit_bundle(bundle,source_scenario=scenario,
        scenario_sha256=reference.canonical_sha256(scenario))
    test.assertTrue(result['passed'])
    test.assertEqual(set(result['patient_vectors']),{'R0','R1','H1','G1'})
    test.assertEqual(scenario,before)
    test.assertEqual({len(v) for v in result['patient_vectors'].values()},{len(scenario['patients'])})
    test.assertTrue(all(not b['metrics']['whole_episode_optimal_proven'] for b in bundle['candidate']['branches']))

class GuardIntegrationTests(unittest.TestCase):
    def test_single_four_arm_audit(self):check(self,'routine_single')
    def test_stream_four_arm_audit(self):check(self,'routine_stream')
    def test_surge_four_arm_audit(self):check(self,'surge')
if __name__=='__main__':unittest.main(verbosity=2)
