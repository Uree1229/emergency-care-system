"""G3 current-opportunity protection, bounded chains and small policy oracles."""
from copy import deepcopy
from itertools import product
import random
from types import SimpleNamespace
import unittest

from simulation import protected_assignment_20261029 as policy
from simulation.arrival_guard_policy_20261028 import caller_order_nearest
from simulation.cp_sat_assignment import validate_assignment


def row(pid, priority=2, options=('H1',), demand=None):
    return dict(id=pid, priority=priority, options=list(options),
                demand=dict(demand or {'bed': 1}))


def oracle(rows, travel, free):
    """Enumerate feasible policies independently of G3's search implementation."""
    baseline = caller_order_nearest(rows, travel, free)
    protected = {pid for pid, hid in baseline.items() if hid is not None}
    best = None
    for destinations in product(*(r['options'] + [None] for r in rows)):
        plan = dict(zip((r['id'] for r in rows), destinations))
        if not validate_assignment(rows, plan, free) or any(plan[pid] is None for pid in protected):
            continue
        count = sum(h is not None for h in plan.values())
        deltas = [travel[pid, plan[pid]]-travel[pid, baseline[pid]] for pid in protected]
        if count == len(protected) and plan != baseline:
            if not deltas or any(d > 0 for d in deltas) or not any(d < 0 for d in deltas):
                continue
        positive = [max(0, x) for x in deltas]
        key = [sum(r['priority'] for r in rows if plan[r['id']] is None),
               sum(h is None for h in plan.values()), max(positive, default=0),sum(positive),
               sum(r['priority']*travel[r['id'], plan[r['id']]] for r in rows if plan[r['id']] is not None),
               sum(plan[pid] != baseline[pid] for pid in protected)]
        if best is None or key < best:
            best = key
    return best


class ProtectedCurrentPlanTests(unittest.TestCase):
    def test_priority_only_exchange_cannot_take_existing_patient_opportunity(self):
        # Exact observable t8 pattern of main_surge_0096, before future events.
        rows = [row('P08',2,('H04',)),row('P09',1,('H04',)),row('P12',4,('H04',))]
        travel = {('P08','H04'):7,('P09','H04'):7,('P12','H04'):6}
        result = policy.protected_current_plan(rows,travel,{('H04','bed'):1})
        self.assertEqual(result['actual_plan'],{'P08':'H04','P09':None,'P12':None})
        self.assertEqual(result['protected_patient_ids'],['P08'])
        self.assertEqual(result['reason'],'baseline_preserved')
        self.assertFalse(result['whole_episode_priority4_nonregression_guaranteed'])
        self.assertFalse(result['clinical_priority_policy_validated'])

    def test_scarce_offer_augmentation_keeps_old_admission_and_adds_second(self):
        rows=[row('A',2,('H1','H2')),row('B',4)]
        travel={('A','H1'):1,('A','H2'):5,('B','H1'):2}
        result=policy.protected_current_plan(rows,travel,{('H1','bed'):1,('H2','bed'):1})
        self.assertEqual(result['actual_plan'],{'A':'H2','B':'H1'})
        self.assertTrue(result['used_protected_augmentation'])
        self.assertEqual(result['per_patient_regret']['A']['positive_travel_regret_minutes'],4)

    def test_multi_resource_target_moves_two_distinct_blockers(self):
        rows=[row('A',2,('H1','H2'),{'bed':1}),row('B',2,('H1','H3'),{'team':1}),
              row('C',4,('H1',),{'bed':1,'team':1})]
        free={(h,r):1 for h in ('H1','H2','H3') for r in ('bed','team')}
        travel={(r['id'],h):(1 if h=='H1' else 2) for r in rows for h in r['options']}
        result=policy.protected_current_plan(rows,travel,free,max_moves=30)
        self.assertEqual(result['actual_plan'],{'A':'H2','B':'H3','C':'H1'})
        self.assertTrue(validate_assignment(rows,result['actual_plan'],free))

    def test_three_hospital_chain_under_partial_node_budget(self):
        rows=[row('A',2,('H1','H2')),row('B',2,('H2','H3')),row('C',2,('H1',))]
        travel={('A','H1'):1,('A','H2'):2,('B','H2'):1,('B','H3'):2,('C','H1'):1}
        free={(h,'bed'):1 for h in ('H1','H2','H3')}
        result=policy.protected_current_plan(rows,travel,free,max_moves=10)
        self.assertEqual(result['actual_plan'],{'A':'H2','B':'H3','C':'H1'})
        self.assertEqual(result['search_mode'],'bounded_augmentation')
        self.assertLessEqual(result['work_units'],10)
        self.assertGreaterEqual(result['maximum_chain_depth'],3)

    def test_same_count_pareto_exchange_is_allowed(self):
        rows=[row('A',2,('H1','H2')),row('B',2,('H1','H2'))]
        travel={('A','H1'):1,('A','H2'):1,('B','H1'):1,('B','H2'):5}
        result=policy.protected_current_plan(rows,travel,{('H1','bed'):1,('H2','bed'):1})
        self.assertEqual(result['actual_plan'],{'A':'H2','B':'H1'})
        self.assertEqual(result['reason'],'same_count_pareto_improvement')
        self.assertEqual(result['actual_objective'][2:4],[0,0])

    def test_same_count_weighted_travel_improvement_with_one_victim_is_rejected(self):
        rows=[row('A',1,('H1','H2')),row('B',4,('H1','H2'))]
        travel={('A','H1'):1,('A','H2'):4,('B','H1'):1,('B','H2'):20}
        result=policy.protected_current_plan(rows,travel,{('H1','bed'):1,('H2','bed'):1})
        self.assertEqual(result['actual_plan'],result['baseline_ready_order_r1_plan'])

    def test_no_current_capacity_or_offers_keeps_valid_empty_admission(self):
        rows=[row('A'),row('B',options=())]
        result=policy.protected_current_plan(rows,{('A','H1'):1},{('H1','bed'):-1})
        self.assertEqual(result['actual_plan'],{'A':None,'B':None})
        self.assertEqual(result['actual_assigned_count'],0)
        self.assertEqual(policy.protected_current_plan([],{}, {})['actual_plan'],{})

    def test_zero_budget_keeps_baseline_and_counts_partial_nodes_at_small_budget(self):
        rows=[row('A',2,('H1','H2')),row('B',4)]
        travel={('A','H1'):1,('A','H2'):2,('B','H1'):1}
        free={('H1','bed'):1,('H2','bed'):1}
        zero=policy.protected_current_plan(rows,travel,free,max_moves=0)
        self.assertEqual(zero['actual_plan'],{'A':'H1','B':None})
        self.assertEqual(zero['work_units'],0)
        one=policy.protected_current_plan(rows,travel,free,max_moves=1)
        self.assertEqual(one['work_units'],1)
        self.assertEqual(one['evaluated_plans'],0)
        self.assertEqual(one['search_stop_reason'],'work_limit')

    def test_determinism_and_input_immutability(self):
        rows=[row('A',2,('H1','H2')),row('B',4)]
        travel={('A','H1'):1,('A','H2'):3,('B','H1'):2}
        free={('H1','bed'):1,('H2','bed'):1};before=deepcopy((rows,travel,free))
        first=policy.protected_current_plan(rows,travel,free)
        self.assertEqual(first,policy.protected_current_plan(rows,travel,free))
        self.assertEqual((rows,travel,free),before)

    def test_invalid_public_snapshot_is_rejected(self):
        rows=[row('A')];travel={('A','H1'):1};free={('H1','bed'):1}
        invalid=[([row('A'),row('A')],travel,free,4),
                 ([row('A',options=('H1','H1'))],travel,free,4),
                 ([dict(row('A'),priority=True)],travel,free,4),
                 ([dict(row('A'),options=[[]])],travel,free,4),
                 (rows,{},free,4),(rows,travel,{},4),
                 (rows,{('A','H1'):float('nan')},free,4),
                 (rows,travel,free,True),(rows,travel,free,-1)]
        for args in invalid:
            with self.subTest(args=args),self.assertRaises(ValueError):
                policy.protected_current_plan(*args[:3],max_moves=args[3])

    def test_small_policy_envelopes_match_bruteforce_with_heterogeneous_resources(self):
        rng=random.Random(71193)
        for case in range(60):
            rows=[row(str(i),rng.choice((1,2,4)),tuple(h for h in ('H1','H2') if rng.random()<.8),
                      {'bed':rng.randint(1,2),'team':rng.randint(0,1)}) for i in range(3)]
            free={(h,r):rng.randint(0,3) for h in ('H1','H2') for r in ('bed','team')}
            travel={(r['id'],h):rng.randint(1,15) for r in rows for h in r['options']}
            actual=policy.protected_current_plan(rows,travel,free,max_moves=10000)
            with self.subTest(case=case):
                self.assertEqual(actual['actual_objective'],oracle(rows,travel,free))
                self.assertTrue(validate_assignment(rows,actual['actual_plan'],free))
                self.assertFalse(actual['optimal_proven'])


class ObservedOnlyWrapperTests(unittest.TestCase):
    def test_wrapper_does_not_read_future_schedules_or_modify_world(self):
        class Hospital:
            def free(self, resource):return 1
        class World:
            hospitals={'H1':Hospital(),'H2':Hospital()}
            def position(self,p,t):return (0,0)
            def travel(self,p,hid,position):return 1 if hid=='H1' else 3
            def __getattr__(self,name):raise AssertionError('unexpected world access '+name)
        a=SimpleNamespace(id='A',priority=2,t_req=0,demand={'bed':1})
        b=SimpleNamespace(id='B',priority=4,t_req=0,demand={'bed':1})
        ready=[(a,['H1','H2']),(b,['H1'])];before=deepcopy(ready)
        rule=policy.ProtectedAssignmentRule()
        self.assertEqual(rule.decide_batch(World(),ready,8),{'A':'H2','B':'H1'})
        self.assertEqual(ready,before)
        self.assertEqual(len(rule.records),1)
        self.assertEqual(rule.records[0]['protected_patient_ids'],['A'])
        self.assertEqual(rule.code,'G3')
        self.assertFalse(rule.records[0]['actual_arrival_nonregression_guaranteed'])
        self.assertEqual(rule.decide_batch(World(),[],9),{})

    def test_safety_exhaustion_is_explicit_technical_failure(self):
        from unittest.mock import patch
        a=SimpleNamespace(id='A',priority=2,t_req=0,demand={'bed':1})
        world=SimpleNamespace(hospitals={'H1':SimpleNamespace(free=lambda resource:1)},
                              position=lambda p,t:(0,0),travel=lambda p,h,pos:1)
        rule=policy.ProtectedAssignmentRule()
        with patch('simulation.protected_assignment_20261029.time.perf_counter',side_effect=[0,20]):
            with self.assertRaises(policy.ProtectedAssignmentSafetyBudgetExhausted):
                rule.decide_batch(world,[(a,['H1'])],0)
        self.assertEqual(rule.records,[])


if __name__=='__main__':unittest.main()
