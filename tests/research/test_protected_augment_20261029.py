"""G4 search-schedule regression, unchanged policy, independent small oracles."""
from copy import deepcopy
from itertools import product
from pathlib import Path
import hashlib
import random
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from simulation import protected_augment_20261029 as policy
from simulation.protected_assignment_20261029 import protected_current_plan
from simulation.cp_sat_assignment import validate_assignment
from test_protected_assignment_20261029 import row, oracle


class ProtectedAugmentTests(unittest.TestCase):
    def large_case(self):
        rows=[row(f'P{i:02d}',4,('H1','H2','H3') if i<=12 else ('H1',),
                  {'bed':1,'team':1}) for i in range(1,17)]
        free={(h,r):5 for h in ('H1','H2','H3') for r in ('bed','team')}
        travel={(r['id'],h):int(h[1:]) for r in rows for h in r['options']}
        return rows,travel,free

    def test_first_admission_restart_reaches_15_without_dropping_12_protected(self):
        rows,travel,free=self.large_case()
        old=protected_current_plan(rows,travel,free)
        result=policy.protected_augment_plan(rows,travel,free)
        self.assertEqual(old['actual_assigned_count'],13)
        self.assertEqual(result['baseline_assigned_count'],12)
        self.assertEqual(result['actual_assigned_count'],15)
        self.assertTrue(all(result['actual_plan'][pid] is not None for pid in result['protected_patient_ids']))
        self.assertTrue(validate_assignment(rows,result['actual_plan'],free))
        self.assertEqual(result['work_units'],2048)
        self.assertEqual(result['fast_admission_restarts'],3)
        self.assertEqual(result['optimistic_primary_lower_bound'],[4,1])
        self.assertTrue(result['optimistic_primary_bound_reached'])
        self.assertGreater(result['phase_work_units']['regret'],0)
        self.assertEqual(sum(result['phase_work_units'].values()),result['work_units'])
        self.assertEqual(result['search_schedule'],'admission_first_restart_then_regret_refinement')

    def test_frozen_parent_source_remains_bound(self):
        import simulation.protected_assignment_20261029 as parent
        self.assertEqual(hashlib.sha256(Path(parent.__file__).read_bytes()).hexdigest(),
                         policy.PARENT_G3_SOURCE_SHA256)

    def test_85_minute_snapshot_still_rejects_same_count_priority_displacement(self):
        rows=[row('P08',2),row('P09',1),row('P12',4)]
        result=policy.protected_augment_plan(rows,{(r['id'],'H1'):7 for r in rows},{('H1','bed'):1})
        self.assertEqual(result['actual_plan'],{'P08':'H1','P09':None,'P12':None})
        self.assertEqual(result['reason'],'baseline_preserved')
        self.assertFalse(result['whole_episode_priority4_nonregression_guaranteed'])

    def test_scarcity_augmentation_and_multi_resource_chain(self):
        rows=[row('A',2,('H1','H2'),{'bed':1}),row('B',2,('H1','H3'),{'team':1}),
              row('C',4,('H1',),{'bed':1,'team':1})]
        travel={(r['id'],h):1 if h=='H1' else 2 for r in rows for h in r['options']}
        free={(h,r):1 for h in ('H1','H2','H3') for r in ('bed','team')}
        result=policy.protected_augment_plan(rows,travel,free,max_moves=30)
        self.assertEqual(result['actual_plan'],{'A':'H2','B':'H3','C':'H1'})

    def test_three_hospital_partial_node_budget(self):
        rows=[row('A',2,('H1','H2')),row('B',2,('H2','H3')),row('C',2,('H1',))]
        travel={('A','H1'):1,('A','H2'):2,('B','H2'):1,('B','H3'):2,('C','H1'):1}
        result=policy.protected_augment_plan(rows,travel,{(h,'bed'):1 for h in ('H1','H2','H3')},max_moves=10)
        self.assertEqual(result['actual_assigned_count'],3)
        self.assertLessEqual(result['work_units'],10)
        self.assertGreater(result['fast_admission_restarts'],0)

    def test_same_count_pareto_exchange_allowed_but_single_victim_rejected(self):
        rows=[row('A',1,('H1','H2')),row('B',4,('H1','H2'))]
        free={('H1','bed'):1,('H2','bed'):1}
        travel={('A','H1'):1,('A','H2'):1,('B','H1'):1,('B','H2'):20}
        result=policy.protected_augment_plan(rows,travel,free)
        self.assertEqual(result['actual_plan'],{'A':'H2','B':'H1'})
        travel['A','H2']=2
        rejected=policy.protected_augment_plan(rows,travel,free)
        self.assertEqual(rejected['actual_plan'],rejected['baseline_ready_order_r1_plan'])

    def test_zero_and_small_budget_return_valid_protected_incumbent(self):
        rows,travel,free=self.large_case()
        for limit in (0,1,10,32,64):
            result=policy.protected_augment_plan(rows,travel,free,max_moves=limit)
            with self.subTest(limit=limit):
                self.assertLessEqual(result['work_units'],limit)
                self.assertTrue(validate_assignment(rows,result['actual_plan'],free))
                self.assertTrue(all(result['actual_plan'][pid] is not None for pid in result['protected_patient_ids']))
                self.assertFalse(result['optimal_proven'])

    def test_invalid_inputs_and_no_feasible_capacity(self):
        rows=[row('A')];travel={('A','H1'):1};free={('H1','bed'):1}
        for changes in ((rows,{},free,2048),([row('A'),row('A')],travel,free,2048),
                        (rows,travel,free,True),(rows,travel,free,-1)):
            with self.assertRaises(ValueError):
                policy.protected_augment_plan(*changes[:3],max_moves=changes[3])
        result=policy.protected_augment_plan(rows,travel,{('H1','bed'):-1})
        self.assertEqual(result['actual_plan'],{'A':None})
        self.assertEqual(policy.protected_augment_plan([],{}, {})['actual_plan'],{})

    def test_determinism_and_input_immutability(self):
        rows,travel,free=self.large_case();before=deepcopy((rows,travel,free))
        result=policy.protected_augment_plan(rows,travel,free)
        self.assertEqual(result,policy.protected_augment_plan(rows,travel,free))
        self.assertEqual((rows,travel,free),before)

    def test_small_envelopes_have_unchanged_policy_optimum(self):
        rng=random.Random(88471)
        for index in range(80):
            rows=[row(str(i),rng.choice((1,2,4)),tuple(h for h in ('H1','H2') if rng.random()<.8),
                      {'bed':rng.randint(1,2),'team':rng.randint(0,1)}) for i in range(3)]
            travel={(r['id'],h):rng.randint(1,15) for r in rows for h in r['options']}
            free={(h,r):rng.randint(0,3) for h in ('H1','H2') for r in ('bed','team')}
            actual=policy.protected_augment_plan(rows,travel,free,max_moves=10000)
            with self.subTest(index=index):
                self.assertEqual(actual['actual_objective'],oracle(rows,travel,free))
                self.assertEqual(actual['actual_objective'],protected_current_plan(rows,travel,free,max_moves=10000)['actual_objective'])

    def test_optimistic_primary_bound_never_excludes_any_feasible_primary(self):
        rng=random.Random(11571)
        for index in range(80):
            rows=[row(str(i),rng.choice((1,2,4)),tuple(h for h in ('H1','H2') if rng.random()<.8),
                      {'bed':rng.randint(1,2),'team':rng.randint(0,1)}) for i in range(3)]
            free={(h,r):rng.randint(0,3) for h in ('H1','H2') for r in ('bed','team')}
            bound=policy._current_primary_lower_bound(rows,free)
            for options in product(*(r['options']+[None] for r in rows)):
                plan=dict(zip((r['id'] for r in rows),options))
                if validate_assignment(rows,plan,free):
                    key=[sum(r['priority'] for r in rows if plan[r['id']] is None),sum(h is None for h in plan.values())]
                    self.assertLessEqual(bound,key,(index,rows,free,bound,key))


class ProtectedAugmentWrapperTests(unittest.TestCase):
    def test_observed_only_wrapper_and_lineage(self):
        class Hospital:
            def free(self,resource):return 1
        class World:
            hospitals={'H1':Hospital(),'H2':Hospital()}
            def position(self,p,t):return (0,0)
            def travel(self,p,hid,position):return 1 if hid=='H1' else 3
            def __getattr__(self,name):raise AssertionError('forbidden world access '+name)
        a=SimpleNamespace(id='A',priority=2,t_req=0,demand={'bed':1})
        b=SimpleNamespace(id='B',priority=4,t_req=0,demand={'bed':1})
        ready=[(a,['H1','H2']),(b,['H1'])];before=deepcopy(ready)
        rule=policy.ProtectedAugmentAssignmentRule()
        self.assertEqual(rule.decide_batch(World(),ready,8),{'A':'H2','B':'H1'})
        self.assertEqual(ready,before)
        self.assertEqual(rule.code,'G4')
        self.assertEqual(rule.records[0]['rule'],'G4')
        self.assertEqual(rule.records[0]['search_schedule'],policy.SEARCH_SCHEDULE)
        self.assertFalse(rule.records[0]['actual_arrival_nonregression_guaranteed'])

    def test_wall_safety_failure_does_not_return_a_different_valid_plan(self):
        a=SimpleNamespace(id='A',priority=2,t_req=0,demand={'bed':1})
        world=SimpleNamespace(hospitals={'H1':SimpleNamespace(free=lambda resource:1)},
                              position=lambda p,t:(0,0),travel=lambda p,h,pos:1)
        rule=policy.ProtectedAugmentAssignmentRule()
        with patch('simulation.protected_augment_20261029.time.perf_counter',side_effect=[0,20]):
            with self.assertRaises(policy.ProtectedAugmentSafetyBudgetExhausted):
                rule.decide_batch(world,[(a,['H1'])],0)
        self.assertEqual(rule.records,[])


if __name__=='__main__':unittest.main()
