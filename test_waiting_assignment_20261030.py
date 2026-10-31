"""Meaningful bounded current-batch waiting-policy tests; no future simulator."""
from collections import Counter
from copy import deepcopy
from itertools import product
import random
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from simulation import waiting_assignment_20261030 as w
from simulation.heuristic_assignment import HeuristicAssignmentRule


def row(pid,priority=1,options=None,demand=None):
    return dict(id=pid,priority=priority,options=['H'] if options is None else options,
        demand={'bed':1} if demand is None else demand)


def independent_key(rows,plan,travel,ages):
    values=[sum(r['priority'] for r in rows if plan[r['id']] is None)]
    for priority in (4,2,1):
        levels=sorted({ages[r['id']] for r in rows if r['priority']==priority},reverse=True)
        for age in levels[:-1]:
            values.append(sum(plan[r['id']] is None for r in rows if r['priority']==priority and ages[r['id']]==age))
    values.append(sum(r['priority']*travel[r['id'],plan[r['id']]] for r in rows if plan[r['id']] is not None))
    return tuple(values)


def oracle(rows,travel,free,ages):
    candidates=[]
    for choices in product(*(r['options']+[None] for r in rows)):
        used=Counter()
        for r,hid in zip(rows,choices):
            if hid is not None:
                for resource,quantity in r['demand'].items():used[hid,resource]+=quantity
        if all(n<=free.get(key,0) for key,n in used.items()):
            plan={r['id']:hid for r,hid in zip(rows,choices)}
            candidates.append(independent_key(rows,plan,travel,ages))
    return min(candidates)


class WaitingTests(unittest.TestCase):
    def test_sparse_travel_refinement_cannot_displace_older_equal_weight_patient(self):
        rows=[row('old',demand={'bed':1,'team':1}),row('new')]
        result=w.solve_waiting(rows,{('old','H'):10,('new','H'):8},{('H','bed'):1,('H','team'):1},{'old':10,'new':1})
        r=result['record']
        self.assertEqual({'old':None,'new':'H'},r['H1_reference_assignments'])
        self.assertEqual({'old':'H','new':None},result['assignments'])
        self.assertEqual([1,0,10],r['objective']);self.assertEqual([1,1,8],r['baseline_objective'])
        self.assertEqual(1,r['H1_reference_primary'])
        self.assertLessEqual(r['evaluated_moves'],2048)

    def test_equal_waits_preserve_original_H1_travel_choice_without_new_policy_work(self):
        rows=[row('old',demand={'bed':1,'team':1}),row('new')]
        r=w.solve_waiting(rows,{('old','H'):10,('new','H'):8},{('H','bed'):1,('H','team'):1},{'old':2,'new':2})['record']
        self.assertEqual(r['assignments'],r['H1_reference_assignments'])
        self.assertEqual([],r['age_buckets']);self.assertEqual(0,r['waiting_evaluated_moves'])
        self.assertEqual('no_waiting_age_contrast_keep_H1',r['selection_reason'])

    def test_cross_priority_older_low_weight_does_not_override_primary(self):
        rows=[row('old',1),row('higher',2)]
        r=w.solve_waiting(rows,{('old','H'):7,('higher','H'):6},{('H','bed'):1},{'old':100,'higher':2})['record']
        self.assertEqual({'old':None,'higher':'H'},r['assignments'])
        self.assertEqual([1,12],r['objective'])

    def test_primary_gain_dominates_a_waiting_bucket(self):
        rows=[row('high',4),row('old',1),row('new',1)]
        r=w.solve_waiting(rows,{(p['id'],'H'):1 for p in rows},{('H','bed'):1},{'high':0,'old':100,'new':0})['record']
        self.assertEqual('H',r['assignments']['high']);self.assertIsNone(r['assignments']['old'])
        self.assertEqual(2,r['objective'][0])

    def test_buckets_omit_youngest_group_and_ties_are_group_counts(self):
        rows=[row('a',4),row('b',4),row('c',2),row('d',2),row('e',1),row('f',1),row('g',1)]
        ages=dict(a=20,b=5,c=2,d=2,e=10,f=8,g=8)
        self.assertEqual([dict(priority=4,age=20),dict(priority=1,age=10)],w.age_buckets(rows,ages))

    def test_complete_small_cases_equal_independent_exhaustive_oracle(self):
        rng=random.Random(883170);checked=0
        for _ in range(50):
            count=rng.randint(2,4);rows=[];travel={};free={('A','bed'):rng.randint(0,2),('B','bed'):rng.randint(0,2)}
            ages={f'P{i}':rng.randint(0,12) for i in range(count)};ages['P0']=20;ages['P1']=0
            for i in range(count):
                options=[hid for hid in ('A','B') if rng.random()<.8]
                rows.append(row(f'P{i}',1,options))
                travel.update({(f'P{i}',hid):rng.randint(0,20) for hid in options})
            result=w.solve_waiting(rows,travel,free,ages);r=result['record']
            self.assertEqual(oracle(rows,travel,free,ages),tuple(r['objective']))
            self.assertEqual(independent_key(rows,result['assignments'],travel,ages),tuple(r['objective']))
            self.assertLessEqual(r['objective'][0],r['H1_reference_primary'])
            checked+=1
        self.assertEqual(50,checked)

    def test_multi_resource_and_priority_small_oracle(self):
        rows=[row('a',4,['A','B'],{'bed':1,'team':1}),row('b',4,['A'],{'bed':1}),
            row('c',2,['A','B'],{'bed':1}),row('d',1,['B'],{'bed':1,'team':1})]
        travel={(r['id'],h):i+1 for i,r in enumerate(rows) for h in r['options']}
        free={('A','bed'):1,('A','team'):1,('B','bed'):2,('B','team'):1};ages=dict(a=12,b=1,c=9,d=2)
        result=w.solve_waiting(rows,travel,free,ages)
        self.assertEqual(oracle(rows,travel,free,ages),tuple(result['record']['objective']))

    def test_budget_is_shared_with_H1_not_two_separate_limits(self):
        rows=[row(f'P{i}',1,['A','B']) for i in range(6)]
        travel={(r['id'],h):i+1 for i,r in enumerate(rows) for h in r['options']}
        free={('A','bed'):2,('B','bed'):1};ages={r['id']:i for i,r in enumerate(rows)}
        for budget in (0,1,5,7,12,23,100):
            with self.subTest(budget=budget):
                r=w.solve_waiting(rows,travel,free,ages,max_moves=budget)['record']
                self.assertEqual(r['H1_reference_evaluated_moves']+r['waiting_evaluated_moves'],r['evaluated_moves'])
                self.assertLessEqual(r['evaluated_moves'],budget)
                self.assertLessEqual(r['objective'][0],r['H1_reference_primary'])

    def test_improvement_records_strictly_decrease_the_new_objective(self):
        rows=[row('old',demand={'bed':1,'team':1}),row('new')]
        r=w.solve_waiting(rows,{('old','H'):10,('new','H'):8},{('H','bed'):1,('H','team'):1},{'old':10,'new':1})['record']
        previous=tuple(r['baseline_objective']);last=r['H1_reference_evaluated_moves']
        for change in r['improvements']:
            self.assertLess(tuple(change['objective']),previous)
            self.assertGreater(change['evaluation'],last)
            previous=tuple(change['objective']);last=change['evaluation']
        self.assertEqual(previous,tuple(r['objective']))

    def test_inputs_unchanged_and_old_commitments_not_exposed(self):
        rows=[row('old'),row('new')];travel={('old','H'):9,('new','H'):3};free={('H','bed'):1};ages={'old':12,'new':1}
        frozen=deepcopy((rows,travel,free,ages));w.solve_waiting(rows,travel,free,ages)
        self.assertEqual(frozen,(rows,travel,free,ages))

    def test_empty_no_offer_and_negative_free_are_feasible_nonassignments(self):
        self.assertEqual({},w.solve_waiting([],{}, {},{})['assignments'])
        result=w.solve_waiting([row('P',options=[])],{}, {},{'P':0})
        self.assertEqual({'P':None},result['assignments'])
        result=w.solve_waiting([row('P')],{('P','H'):1},{('H','bed'):-1},{'P':0})
        self.assertEqual({'P':None},result['assignments'])

    def test_malformed_or_future_shaped_inputs_are_rejected(self):
        good=([row('P')],{('P','H'):1},{('H','bed'):1},{'P':0})
        cases=[]
        for bad_age in (-1,True,1.5,2**61):cases.append((good[0],good[1],good[2],{'P':bad_age}))
        cases.append(([{**row('P'),'future_response':object()}],*good[1:]))
        cases.append(([row('P'),row('P')],*good[1:]))
        cases.append(([row('P',options=['H','H'])],*good[1:]))
        cases.append((good[0],{('P','H'):True},good[2],good[3]))
        cases.append((good[0],good[1],{('H','bed'):object()},good[3]))
        for args in cases:
            with self.subTest(args=str(args)):
                with self.assertRaises(ValueError):w.solve_waiting(*args)
        for budget in (-1,True,2049):
            with self.assertRaises(ValueError):w.solve_waiting(*good,max_moves=budget)

    def test_technical_timeout_is_error_not_a_returned_fallback(self):
        with patch.object(w.time,'perf_counter',side_effect=[0.,11.]):
            with self.assertRaises(w.WaitingSafetyBudgetExhausted):w.solve_waiting([row('P')],{('P','H'):1},{('H','bed'):1},{'P':0})

    def test_rule_reads_only_existing_observed_world_and_request_time(self):
        reads=[]
        class ObservedOnly:
            def __getattribute__(self,name):
                if name not in ('hospitals','position','travel'):raise AssertionError('future/world field requested: '+name)
                reads.append(name);return object.__getattribute__(self,name)
            hospitals={'H':SimpleNamespace(free=lambda resource:1)}
            def position(self,p,t):return (0,0)
            def travel(self,p,hid,position):return 2
        a=SimpleNamespace(id='a',priority=1,demand={'bed':1},t_req=1)
        b=SimpleNamespace(id='b',priority=1,demand={'bed':1},t_req=9)
        rule=w.WaitingAssignmentRule();plan=rule.decide_batch(ObservedOnly(),[(a,['H']),(b,['H'])],10)
        self.assertEqual({'a':'H','b':None},plan)
        self.assertEqual({'a':9,'b':1},rule.records[0]['ages'])
        self.assertEqual(10,rule.records[0]['t'])
        self.assertLessEqual(set(reads),{'hospitals','position','travel'})
        b.t_req=11
        with self.assertRaises(ValueError):rule.decide_batch(ObservedOnly(),[(b,['H'])],10)

    def test_recorded_reference_matches_direct_unchanged_H1(self):
        ready=[(SimpleNamespace(id='a',priority=1,demand={'bed':1,'team':1},t_req=0),['H']),
            (SimpleNamespace(id='b',priority=1,demand={'bed':1},t_req=9),['H'])]
        world=SimpleNamespace(hospitals={'H':SimpleNamespace(free=lambda resource:1)},
            position=lambda p,t:(0,0),travel=lambda p,h,l:10 if p.id=='a' else 8)
        original=HeuristicAssignmentRule(time_limit_seconds=10,max_moves=2048)
        expected=original.decide_batch(world,ready,10)
        candidate=w.WaitingAssignmentRule();candidate.decide_batch(world,ready,10)
        self.assertEqual(expected,candidate.records[0]['H1_reference_assignments'])
        self.assertEqual(original.records[0]['objective'],candidate.records[0]['H1_reference_objective'])


if __name__=='__main__':unittest.main()
