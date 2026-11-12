from copy import deepcopy
import hashlib
from pathlib import Path
import unittest

from simulation import arrival_guard_engine_20261028 as e
from simulation.online_allocation_scenarios import build_online_scenarios
from simulation.map_replay import RouteSnapshot
from simulation import sim,heuristic_assignment,online_allocation_audit

def scenario(family='scarce_greedy_trap'):
    return build_online_scenarios(29,[family])[0]

class GuardEngineTests(unittest.TestCase):
    def test_scarce_two_patient_increment_preserved_and_actual_commits_match_guard(self):
        source=scenario();before=deepcopy(source);result=e.run_guard_replay(source)
        self.assertEqual(source,before);self.assertEqual(result['execution_order'],['G1'])
        branch=result['branches'][0]
        self.assertEqual(sum(p['first_commit_t'] is not None for p in branch['patient_outcomes']),2)
        self.assertTrue(branch['guard_records'][0]['used_h1_proposal'])
        for decision in branch['allocation_decisions']:
            if decision['kind']=='batch':
                record=branch['guard_records'][decision['guard_record_index']]
                self.assertEqual(record['actual_plan'],decision['assignments'])
                self.assertEqual(record['decision_index'],branch['allocation_decisions'].index(decision))
        self.assertEqual(branch['heuristic_records'],[]);self.assertEqual(branch['solver_records'],[])
        self.assertEqual(branch['policy']['rule'],'G1')
        online_allocation_audit.audit_branch(result['scenario'],branch,exact_limit=0)
    def test_same_count_priority_swap_is_blocked_in_actual_physical_loop(self):
        source=scenario();source['hospitals'][1]['capacity']={'ed_bed':0,'special_team':0}
        source['patients'][0]['priority']=1;source['patients'][1]['priority']=2
        result=e.run_guard_replay(source);record=result['branches'][0]['guard_records'][0]
        self.assertEqual(record['original_h1_plan'],{'P01':None,'P02':'FICT-H01'})
        self.assertEqual(record['actual_plan'],{'P01':'FICT-H01','P02':None})
        self.assertFalse(record['used_h1_proposal'])
    def test_fresh_controls_remain_the_old_three_arm_replay_schema(self):
        result=e.run_comparison(scenario('delayed_replies'),control_execution_order=['H1','R0','R1'])
        controls=result['controls'];candidate=result['candidate']
        self.assertEqual(controls['comparison_id'],'final_assignment_20261028')
        self.assertEqual([b['id'] for b in controls['branches']],['R0','R1','H1'])
        self.assertEqual(controls['execution_order'],['H1','R0','R1'])
        self.assertEqual(candidate['comparison_id'],'arrival_guard_20261028')
        self.assertEqual(controls['input_scenario_sha256'],candidate['input_scenario_sha256'])
    def test_known_recovery_mode_executes_no_controls(self):
        result=e.run_comparison(scenario('abundant_control'),include_controls=False)
        self.assertIsNone(result['controls']);self.assertEqual(len(result['candidate']['branches']),1)
        with self.assertRaises(ValueError):e.run_comparison(scenario(),include_controls=False,control_execution_order=['R0','R1','H1'])
        with self.assertRaises(ValueError):e.run_comparison(scenario(),include_controls=1)
    def test_provider_or_custom_routing_is_rejected(self):
        with self.assertRaises(ValueError):e.run_guard_replay(scenario(),RouteSnapshot(provider=object()))
        with self.assertRaises(ValueError):e.run_guard_replay(scenario(),object())
    def test_caller_routing_cache_and_used_keys_are_not_modified(self):
        routing=RouteSnapshot();routing.route((35.17,126.85),(35.18,126.85))
        before=deepcopy((routing.routes,routing.used_keys));e.run_guard_replay(scenario(),routing)
        self.assertEqual((routing.routes,routing.used_keys),before)
    def test_original_sources_registry_and_methods_are_not_replaced(self):
        files=[Path(heuristic_assignment.__file__),Path(sim.__file__)]
        hashes=[hashlib.sha256(path.read_bytes()).hexdigest() for path in files]
        registry=dict(sim.RULES);method=heuristic_assignment.HeuristicAssignmentRule.decide_batch
        e.run_guard_replay(scenario())
        self.assertEqual(registry,sim.RULES)
        self.assertIs(method,heuristic_assignment.HeuristicAssignmentRule.decide_batch)
        self.assertEqual(hashes,[hashlib.sha256(path.read_bytes()).hexdigest() for path in files])
    def test_no_capacity_control_has_no_fabricated_decisions(self):
        result=e.run_guard_replay(scenario('no_capacity_control'));branch=result['branches'][0]
        self.assertFalse(branch['allocation_decisions']);self.assertFalse(branch['guard_records'])
        self.assertTrue(all(p['first_commit_t'] is None for p in branch['patient_outcomes']))
if __name__=='__main__':unittest.main(verbosity=2)
