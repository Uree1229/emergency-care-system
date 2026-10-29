from copy import deepcopy
import hashlib
from pathlib import Path
import unittest

from simulation import arrival_guard_priority_engine_20261028 as e
from simulation.online_allocation_scenarios import build_online_scenarios
from simulation.map_replay import RouteSnapshot
from simulation import sim,heuristic_assignment,online_allocation_audit


def scenario(family='scarce_greedy_trap'):
    return build_online_scenarios(29,[family])[0]


class PriorityGuardEngineTests(unittest.TestCase):
    def test_scarce_count_gain_preserved_and_actual_commits_match_records(self):
        source=scenario();before=deepcopy(source);replay=e.run_guard_replay(source);branch=replay['branches'][0]
        self.assertEqual(source,before);self.assertEqual(replay['comparison_id'],'arrival_guard_priority_20261028')
        self.assertEqual(replay['execution_order'],['G2']);self.assertEqual(branch['policy']['rule'],'G2')
        self.assertEqual(sum(p['first_commit_t'] is not None for p in branch['patient_outcomes']),2)
        self.assertTrue(branch['guard_records'][0]['strict_current_count_gain'])
        for index,decision in enumerate(branch['allocation_decisions']):
            if decision['kind']=='batch':
                record=branch['guard_records'][decision['guard_record_index']]
                self.assertEqual(record['actual_plan'],decision['assignments']);self.assertEqual(record['decision_index'],index)
        self.assertEqual(branch['heuristic_records'],[]);self.assertEqual(branch['solver_records'],[])
        self.assertIn('arrival_guard_priority',branch['policy']);self.assertNotIn('arrival_guard',branch['policy'])
        online_allocation_audit.audit_branch(replay['scenario'],branch,exact_limit=0)

    def test_same_count_weight4_gain_is_committed_in_actual_loop(self):
        source=scenario();source['hospitals'][1]['capacity']={'ed_bed':0,'special_team':0}
        source['patients'][0]['priority']=2;source['patients'][1]['priority']=4
        branch=e.run_guard_replay(source)['branches'][0];record=branch['guard_records'][0]
        self.assertEqual(record['baseline_ready_order_r1_plan'],{'P01':'FICT-H01','P02':None})
        self.assertEqual(record['actual_plan'],{'P01':None,'P02':'FICT-H01'})
        self.assertTrue(record['same_current_count_priority4_gain']);self.assertTrue(record['used_h1_proposal'])
        self.assertEqual(record['actual_priority4_assigned_count'],1)

    def test_same_count_weight1_weight2_still_keeps_caller_order(self):
        source=scenario();source['hospitals'][1]['capacity']={'ed_bed':0,'special_team':0}
        source['patients'][0]['priority']=1;source['patients'][1]['priority']=2
        record=e.run_guard_replay(source)['branches'][0]['guard_records'][0]
        self.assertEqual(record['original_h1_plan'],{'P01':None,'P02':'FICT-H01'})
        self.assertEqual(record['actual_plan'],{'P01':'FICT-H01','P02':None})
        self.assertFalse(record['used_h1_proposal'])

    def test_fresh_controls_keep_original_three_arm_schema_and_rotation(self):
        bundle=e.run_comparison(scenario('delayed_replies'),control_execution_order=['H1','R0','R1'])
        controls=bundle['controls'];candidate=bundle['candidate']
        self.assertEqual(controls['comparison_id'],'final_assignment_20261028')
        self.assertEqual(controls['execution_order'],['H1','R0','R1'])
        self.assertEqual([b['id'] for b in controls['branches']],['R0','R1','H1'])
        self.assertEqual(candidate['input_scenario_sha256'],controls['input_scenario_sha256'])

    def test_candidate_only_mode_does_not_fabricate_or_execute_controls(self):
        bundle=e.run_comparison(scenario('abundant_control'),include_controls=False)
        self.assertIsNone(bundle['controls']);self.assertEqual(len(bundle['candidate']['branches']),1)
        with self.assertRaises(ValueError):e.run_comparison(scenario(),include_controls=1)
        with self.assertRaises(ValueError):e.run_comparison(scenario(),include_controls=False,control_execution_order=['R0','R1','H1'])

    def test_no_capacity_has_no_fabricated_guard_records(self):
        branch=e.run_guard_replay(scenario('no_capacity_control'))['branches'][0]
        self.assertFalse(branch['allocation_decisions']);self.assertFalse(branch['guard_records'])
        self.assertTrue(all(p['first_commit_t'] is None for p in branch['patient_outcomes']))

    def test_route_provider_rejected_and_caller_cache_is_untouched(self):
        with self.assertRaises(ValueError):e.run_guard_replay(scenario(),RouteSnapshot(provider=object()))
        with self.assertRaises(ValueError):e.run_guard_replay(scenario(),object())
        route=RouteSnapshot();route.route((35.17,126.85),(35.18,126.85));before=deepcopy((route.routes,route.used_keys))
        e.run_guard_replay(scenario(),route);self.assertEqual((route.routes,route.used_keys),before)

    def test_existing_source_registry_and_methods_are_unchanged(self):
        paths=[Path(sim.__file__),Path(heuristic_assignment.__file__),Path('simulation/arrival_guard_policy_20261028.py'),Path('simulation/arrival_guard_engine_20261028.py')]
        before=[hashlib.sha256(path.read_bytes()).hexdigest() for path in paths]
        registry=dict(sim.RULES);method=heuristic_assignment.HeuristicAssignmentRule.decide_batch
        e.run_guard_replay(scenario())
        self.assertEqual(registry,sim.RULES);self.assertIs(method,heuristic_assignment.HeuristicAssignmentRule.decide_batch)
        self.assertEqual(before,[hashlib.sha256(path.read_bytes()).hexdigest() for path in paths])


if __name__=='__main__':unittest.main(verbosity=2)
