"""Independent current-envelope oracle and frozen-G2 equivalence tests for G5."""
from collections import Counter
from copy import deepcopy
import gzip
import hashlib
from itertools import product
import json
from pathlib import Path
import random
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from simulation import g2_preserving_assignment_20261029 as candidate
from simulation import arrival_guard_priority_policy_20261028 as g2
from simulation.arrival_guard_policy_20261028 import caller_order_nearest
from simulation.cp_sat_assignment import validate_assignment


def row(pid, priority=2, options=('H1','H2'), demand=None):
    return dict(id=pid,priority=priority,options=list(options),
                demand={'bed':1} if demand is None else dict(demand))


def footprint(rows,plan):
    result=Counter()
    for r in rows:
        if plan[r['id']] is not None:
            for resource,units in r['demand'].items():
                result[plan[r['id']],resource]+=units
    return +result


def oracle(rows,travel,free,seed):
    """Exhaust every offered assignment independently of the search order."""
    best=None
    selected={p for p,h in seed.items() if h is not None}
    for values in product(*(r['options']+[None] for r in rows)):
        plan=dict(zip((r['id'] for r in rows),values))
        if {p for p,h in plan.items() if h is not None}!=selected:continue
        if not validate_assignment(rows,plan,free):continue
        if footprint(rows,plan)!=footprint(rows,seed):continue
        if any(r['priority']==4 and plan[r['id']]!=seed[r['id']] for r in rows):continue
        if any(travel[r['id'],plan[r['id']]]>travel[r['id'],seed[r['id']]]
               for r in rows if r['id'] in selected):continue
        cost=[sum(r['priority']*travel[r['id'],plan[r['id']]] for r in rows if r['id'] in selected),
              sum(travel[r['id'],plan[r['id']]] for r in rows if r['id'] in selected),
              sum(plan[p]!=seed[p] for p in seed)]
        if best is None or cost<best:best=cost
    return best


class G2PreservingPureTests(unittest.TestCase):
    def exchange(self):
        rows=[row('A',1),row('B',2)]
        travel={('A','H1'):10,('A','H2'):5,('B','H1'):10,('B','H2'):10}
        free={(h,'bed'):1 for h in ('H1','H2')}
        return rows,travel,free,{'A':'H1','B':'H2'}

    def assert_invariants(self,rows,travel,free,seed,result):
        actual=result['actual_plan']
        self.assertTrue(validate_assignment(rows,actual,free))
        self.assertEqual({p for p,h in actual.items() if h is not None},
                         {p for p,h in seed.items() if h is not None})
        self.assertEqual(footprint(rows,actual),footprint(rows,seed))
        for r in rows:
            p=r['id']
            if r['priority']==4:self.assertEqual(actual[p],seed[p])
            if seed[p] is not None:
                self.assertLessEqual(travel[p,actual[p]],travel[p,seed[p]])
        if actual!=seed:
            self.assertLess(result['actual_travel_objective'][0],result['g2_travel_objective'][0])
        self.assertFalse(result['whole_episode_nonregression_guaranteed'])
        self.assertFalse(result['whole_episode_priority4_nonregression_guaranteed'])

    def test_strict_pareto_exchange_and_input_immutability(self):
        args=self.exchange();before=deepcopy(args)
        result=candidate.refine_g2_plan(*args)
        self.assertEqual(result['actual_plan'],{'A':'H2','B':'H1'})
        self.assertEqual(result['travel_saved_minutes'],{'A':5,'B':0})
        self.assertEqual(result,candidate.refine_g2_plan(*args))
        self.assertEqual(args,before)
        self.assert_invariants(*args,result)

    def test_selected_ids_and_priority4_destination_are_hard_constraints(self):
        rows=[row('A',2,('H1',)),row('B',4,('H1',))]
        travel={('A','H1'):10,('B','H1'):1};free={('H1','bed'):1}
        seed={'A':'H1','B':None}
        self.assertEqual(candidate.refine_g2_plan(rows,travel,free,seed)['actual_plan'],seed)
        rows=[row('A',4),row('B',2)]
        travel={('A','H1'):10,('A','H2'):1,('B','H1'):1,('B','H2'):10}
        seed={'A':'H1','B':'H2'};free={('H1','bed'):1,('H2','bed'):1}
        result=candidate.refine_g2_plan(rows,travel,free,seed)
        self.assertEqual(result['actual_plan'],seed)
        self.assert_invariants(rows,travel,free,seed,result)

    def test_spare_capacity_does_not_allow_footprint_changes_or_tradeoff(self):
        rows=[row('A')];travel={('A','H1'):10,('A','H2'):1}
        free={('H1','bed'):1,('H2','bed'):3};seed={'A':'H1'}
        self.assertEqual(candidate.refine_g2_plan(rows,travel,free,seed)['actual_plan'],seed)
        rows,travel,free,seed=self.exchange();travel['B','H1']=11
        self.assertEqual(candidate.refine_g2_plan(rows,travel,free,seed)['actual_plan'],seed)

    def test_three_cycle_and_no_unnecessary_equal_cost_changes(self):
        rows=[row('A',1,('H1','H2')),row('B',2,('H2','H3')),row('C',2,('H3','H1'))]
        seed={'A':'H1','B':'H2','C':'H3'}
        travel={(r['id'],h):(10 if h==seed[r['id']] else 5) for r in rows for h in r['options']}
        free={(h,'bed'):1 for h in ('H1','H2','H3')}
        result=candidate.refine_g2_plan(rows,travel,free,seed)
        self.assertEqual(result['actual_plan'],{'A':'H2','B':'H3','C':'H1'})
        self.assert_invariants(rows,travel,free,seed,result)
        equal={k:5 for k in travel}
        self.assertEqual(candidate.refine_g2_plan(rows,equal,free,seed)['actual_plan'],seed)

    def test_zero_and_partial_work_budgets_return_feasible_incumbents(self):
        args=self.exchange()
        for limit in (0,1,2,3,5,8,32,2048):
            with self.subTest(limit=limit):
                result=candidate.refine_g2_plan(*args,max_moves=limit)
                self.assertLessEqual(result['work_units'],limit)
                self.assertLessEqual(result['evaluated_plans'],result['work_units'])
                self.assert_invariants(*args,result)
        zero=candidate.refine_g2_plan(*args,max_moves=0)
        self.assertEqual(zero['actual_plan'],args[-1])
        self.assertEqual(zero['search_stop_reason'],'work_limit')

    def test_empty_no_capacity_and_zero_demands(self):
        self.assertEqual(candidate.refine_g2_plan([],{}, {},{})['actual_plan'],{})
        rows=[row('A',options=('H1',))];travel={('A','H1'):1};free={('H1','bed'):-1}
        self.assertEqual(candidate.refine_g2_plan(rows,travel,free,{'A':None})['actual_plan'],{'A':None})
        rows=[row('A',1,demand={})];travel={('A','H1'):10,('A','H2'):1}
        result=candidate.refine_g2_plan(rows,travel,{}, {'A':'H1'})
        self.assertEqual(result['actual_plan'],{'A':'H2'})

    def test_invalid_inputs_fail_without_mutation(self):
        rows,travel,free,seed=self.exchange()
        bad=[(rows,{},free,seed,2048),(rows,travel,free,{'A':'H1'},2048),
             (rows,travel,free,{'A':'H1','B':'H1'},2048),
             (rows+[deepcopy(rows[0])],travel,free,seed,2048),
             (rows,travel,free,seed,True),(rows,travel,free,seed,-1),
             (rows,travel,free,seed,2049),(rows,travel,free,seed,1.0)]
        for index,args in enumerate(bad):
            before=deepcopy(args)
            with self.subTest(index=index),self.assertRaises(ValueError):
                candidate.refine_g2_plan(*args[:4],max_moves=args[4])
            self.assertEqual(args,before)

    def test_independent_small_complete_oracle(self):
        rng=random.Random(20261029371)
        for index in range(100):
            rows=[row(str(i),rng.choice((1,2,4)),tuple(h for h in ('H1','H2','H3') if rng.random()<.7),
                      {'bed':rng.randint(0,2),'team':rng.randint(0,1)}) for i in range(4)]
            travel={(r['id'],h):rng.randint(0,15) for r in rows for h in r['options']}
            free={(h,res):rng.randint(0,4) for h in ('H1','H2','H3') for res in ('bed','team')}
            feasible=[]
            for values in product(*(r['options']+[None] for r in rows)):
                plan=dict(zip((r['id'] for r in rows),values))
                if validate_assignment(rows,plan,free):feasible.append(plan)
            seed=rng.choice(feasible)
            result=candidate.refine_g2_plan(rows,travel,free,seed)
            with self.subTest(index=index):
                self.assertNotEqual(result['search_stop_reason'],'work_limit')
                self.assertEqual(result['actual_travel_objective'],oracle(rows,travel,free,seed))
                self.assert_invariants(rows,travel,free,seed,result)

    def test_recorded_85_minute_snapshot_preserves_p12_instead_of_restoring_p08(self):
        path=Path(__file__).parent/'.codex-build/expanded-samples-20261029/raw/main_surge_0096.json.gz'
        if not path.exists():self.skipTest('local immutable regression artifact not in checkout')
        branch=json.loads(gzip.decompress(path.read_bytes()))['candidate']['branches'][0]
        guard=next(r for r in branch['guard_records'] if r['t']==8 and 'P08' in r['actual_plan'])
        inp=branch['allocation_decisions'][guard['decision_index']]['inputs']
        rows=[{k:r[k] for k in ('id','priority','demand','options')} for r in inp['patients']]
        travel={(e['patient'],e['hospital']):e['minutes'] for e in inp['travel_minutes']}
        free={(e['hospital'],e['resource']):e['free'] for e in inp['free_resources']}
        seed=guard['actual_plan'];self.assertIsNone(seed['P08']);self.assertIsNotNone(seed['P12'])
        result=candidate.refine_g2_plan(rows,travel,free,seed)
        self.assertIsNone(result['actual_plan']['P08'])
        self.assertEqual(result['actual_plan']['P12'],seed['P12'])
        self.assert_invariants(rows,travel,free,seed,result)

    def test_thousand_locked_patients_do_not_hide_two_patient_strict_swap(self):
        rows,travel,free,seed=self.exchange()
        for i in range(1000):
            pid=f'HIGH{i:04d}'
            rows.append(row(pid,4,('H3',)))
            travel[pid,'H3']=1
            seed[pid]='H3'
        free['H3','bed']=1000
        before=deepcopy((rows,travel,free,seed))
        result=candidate.refine_g2_plan(rows,travel,free,seed)
        self.assertEqual(result['actual_plan']['A'],'H2')
        self.assertEqual(result['actual_plan']['B'],'H1')
        self.assertTrue(all(result['actual_plan'][f'HIGH{i:04d}']=='H3' for i in range(1000)))
        self.assertLessEqual(result['work_units'],2048)
        self.assertEqual((rows,travel,free,seed),before)
        self.assert_invariants(rows,travel,free,seed,result)

    def test_thousand_movable_patients_use_iterative_bounded_search(self):
        rows=[row(f'P{i:04d}',2) for i in range(1000)]
        seed={r['id']:('H1' if i<500 else 'H2') for i,r in enumerate(rows)}
        travel={(r['id'],h):(10 if h==seed[r['id']] else 5) for r in rows for h in r['options']}
        free={('H1','bed'):500,('H2','bed'):500}
        for limit in (64,2048):
            with self.subTest(limit=limit):
                result=candidate.refine_g2_plan(rows,travel,free,seed,max_moves=limit)
                self.assertLessEqual(result['work_units'],limit)
                self.assert_invariants(rows,travel,free,seed,result)


class G2PreservingWrapperTests(unittest.TestCase):
    def world(self,free=None,travel=None):
        free=free or {('H1','bed'):1,('H2','bed'):1}
        travel=travel or {}
        class Hospital:
            def __init__(self,hid):self.hid=hid
            def free(self,res):return free.get((self.hid,res),0)
        class World:
            hospitals={h:Hospital(h) for h in ('H1','H2')}
            def position(self,p,t):return (0,0)
            def travel(self,p,hid,pos):return travel.get((p.id,hid),1 if hid=='H1' else 3)
            def __getattr__(self,name):raise AssertionError('forbidden future/world access '+name)
        return World()

    def patient(self,pid,priority=2):
        return SimpleNamespace(id=pid,priority=priority,t_req=0,demand={'bed':1})

    def test_shortcut_seed_matches_actual_frozen_g2_and_no_parent_is_fabricated(self):
        a,b=self.patient('A'),self.patient('B',4)
        inputs=[[(a,['H1'])],[(a,['H1']),(b,['H2'])],[(a,[])]]
        for ready in inputs:
            with self.subTest(ready=ready):
                old=g2.ArrivalGuardPriorityAssignmentRule().decide_batch(self.world(),ready,8)
                rule=candidate.G2PreservingAssignmentRule()
                with patch.object(rule.parent,'decide_batch',side_effect=AssertionError('shortcut should not run H1')):
                    actual=rule.decide_batch(self.world(),ready,8)
                record=rule.records[0]
                self.assertEqual(record['g2_plan'],old)
                self.assertEqual(actual,old)
                self.assertIsNone(record['parent_g2_record'])
                self.assertEqual(record['parent_work_units'],0)

    def test_nonshortcut_parent_and_repeated_calls_use_detached_current_world_only(self):
        rule=candidate.G2PreservingAssignmentRule()
        a,b=self.patient('A'),self.patient('B',4)
        rule.decide_batch(self.world(),[(a,['H1'])],0)
        ready=[(a,['H1']),(b,['H1'])];before=deepcopy(ready)
        expected=g2.ArrivalGuardPriorityAssignmentRule().decide_batch(self.world(),ready,8)
        actual=rule.decide_batch(self.world(),ready,8)
        self.assertEqual(actual,expected)
        self.assertEqual(actual,{'A':None,'B':'H1'})
        self.assertEqual(ready,before)
        self.assertEqual(rule.records[-1]['g2_seed_mode'],'unchanged_G2_parent')
        self.assertIsNotNone(rule.records[-1]['parent_g2_record'])
        self.assertLessEqual(rule.records[-1]['total_work_units'],2048)
        self.assertEqual(len(rule.parent.records),1)

    def test_shortcut_does_not_bypass_snapshot_validation(self):
        a=self.patient('A');a.priority=True
        with self.assertRaises(ValueError):candidate.G2PreservingAssignmentRule().decide_batch(self.world(),[(a,['H1'])],0)
        a=self.patient('A')
        with self.assertRaises(ValueError):candidate.G2PreservingAssignmentRule().decide_batch(self.world(),[(a,['UNKNOWN'])],0)

    def test_time_safety_raises_without_returning_or_recording_different_seed(self):
        rule=candidate.G2PreservingAssignmentRule();a=self.patient('A')
        with patch('simulation.g2_preserving_assignment_20261029.time.perf_counter',side_effect=[0,20]):
            with self.assertRaises(candidate.ArrivalGuardSafetyBudgetExhausted):
                rule.decide_batch(self.world(),[(a,['H1'])],0)
        self.assertEqual(rule.records,[])

    def test_frozen_g2_hash(self):
        self.assertEqual(hashlib.sha256(Path(g2.__file__).read_bytes()).hexdigest(),candidate.G2_SOURCE_SHA256)


if __name__=='__main__':unittest.main()
