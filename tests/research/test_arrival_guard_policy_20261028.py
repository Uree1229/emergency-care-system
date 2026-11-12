from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from simulation import arrival_guard_policy_20261028 as p

def row(pid,priority=1,options=('H1',),demand=1):
    return dict(id=pid,priority=priority,demand={'ed_bed':demand},options=list(options))
def world(rows,travel,free):
    sim=SimpleNamespace(hospitals={hid:SimpleNamespace(free=lambda resource,hid=hid:free[(hid,resource)]) for hid,_ in free},
        position=lambda patient,t:(0,0),travel=lambda patient,hid,position:travel[(patient.id,hid)])
    ready=[(SimpleNamespace(id=r['id'],priority=r['priority'],demand=r['demand'],t_req=0,caps=set()),r['options']) for r in rows]
    return sim,ready

class GuardPolicyTests(unittest.TestCase):
    def test_same_count_priority_swap_falls_back_and_discloses_changed_objective(self):
        rows=[row('A',1),row('B',2)];travel={('A','H1'):5,('B','H1'):5};free={('H1','ed_bed'):1}
        result=p.guard_current_plan(rows,travel,free,{'A':None,'B':'H1'})
        self.assertEqual(result['actual_plan'],{'A':'H1','B':None})
        self.assertEqual(result['original_h1_objective'][0],1)
        self.assertEqual(result['actual_objective'][0],2)
        self.assertTrue(result['objective_change_disclosed'])
        self.assertFalse(result['old_H1_local_objective_nonregression_claimed'])
        self.assertFalse(result['whole_episode_nonregression_guaranteed'])
    def test_strict_current_count_gain_and_nonincreased_priority_miss_adopts_parent(self):
        rows=[row('A',options=('H1','H2')),row('B')]
        travel={('A','H1'):5,('A','H2'):8,('B','H1'):5};free={('H1','ed_bed'):1,('H2','ed_bed'):1}
        parent={'A':'H2','B':'H1'};source=deepcopy((rows,travel,free,parent))
        result=p.guard_current_plan(rows,travel,free,parent)
        self.assertTrue(result['used_h1_proposal']);self.assertEqual(result['actual_plan'],parent)
        self.assertEqual(result['baseline_assigned_count'],1);self.assertEqual(result['actual_assigned_count'],2)
        self.assertEqual((rows,travel,free,parent),source)
    def test_same_count_travel_only_tradeoff_is_not_adopted(self):
        rows=[row('A',4,('H1','H2')),row('B',1,('H1','H2'))]
        travel={('A','H1'):1,('A','H2'):2,('B','H1'):2,('B','H2'):100}
        result=p.guard_current_plan(rows,travel,{('H1','ed_bed'):1,('H2','ed_bed'):1},{'A':'H2','B':'H1'})
        self.assertFalse(result['used_h1_proposal'])
        self.assertEqual(result['actual_plan'],{'A':'H1','B':'H2'})
    def test_more_people_with_higher_priority_miss_is_rejected(self):
        rows=[row('A',4,demand=2),row('B'),row('C')]
        result=p.guard_current_plan(rows,{(r['id'],'H1'):1 for r in rows},{('H1','ed_bed'):2},
            {'A':None,'B':'H1','C':'H1'})
        self.assertTrue(result['strict_current_count_gain']);self.assertFalse(result['baseline_priority_cost_nonincrease'])
        self.assertFalse(result['used_h1_proposal']);self.assertEqual(result['actual_assigned_count'],1)
    def test_caller_order_not_priority_order_determines_nearest_baseline(self):
        rows=[row('LOW',1),row('HIGH',4)]
        result=p.caller_order_nearest(rows,{('LOW','H1'):1,('HIGH','H1'):1},{('H1','ed_bed'):1})
        self.assertEqual(result,{'LOW':'H1','HIGH':None})
    def test_nearest_ties_use_hospital_identifier(self):
        self.assertEqual(p.caller_order_nearest([row('A',options=('H2','H1'))],
            {('A','H1'):1,('A','H2'):1},{('H1','ed_bed'):1,('H2','ed_bed'):1}),{'A':'H1'})
    def test_unknown_or_infeasible_parent_plan_is_not_silently_replaced(self):
        rows=[row('A'),row('B')];travel={('A','H1'):1,('B','H1'):1};free={('H1','ed_bed'):1}
        for plan in ({'A':'H1'},{'A':'H1','B':'H1'},{'A':'UNKNOWN','B':None}):
            with self.assertRaises(ValueError):p.guard_current_plan(rows,travel,free,plan)
    def test_unknown_travel_or_current_quantity_fails_closed(self):
        for travel,free in (({}, {('H1','ed_bed'):1}),({('A','H1'):None},{('H1','ed_bed'):1}),
                            ({('A','H1'):1},{('H1','ed_bed'):True}),({('A','H1'):1},{})):
            with self.assertRaises(ValueError):p.guard_current_plan([row('A')],travel,free,{'A':None})
    def test_negative_preexisting_resource_does_not_invalidate_all_none(self):
        result=p.guard_current_plan([row('A')],{('A','H1'):1},{('H1','ed_bed'):-1},{'A':None})
        self.assertEqual(result['actual_plan'],{'A':None})
    def test_zero_resource_demand_stays_feasible(self):
        result=p.guard_current_plan([row('A',demand=0)],{('A','H1'):0},{('H1','ed_bed'):0},{'A':'H1'})
        self.assertEqual(result['actual_plan'],{'A':'H1'})
    def test_duplicate_patient_or_offered_identifier_rejected(self):
        for rows in ([row('A'),row('A')],[row('A',options=('H1','H1'))]):
            with self.assertRaises(ValueError):p.guard_current_plan(rows,{('A','H1'):1},{('H1','ed_bed'):2},{'A':'H1'})
    def test_original_h1_record_is_preserved_when_actual_guard_action_differs(self):
        rows=[row('A'),row('B',2)];travel={('A','H1'):5,('B','H1'):5};free={('H1','ed_bed'):1}
        sim,ready=world(rows,travel,free);rule=p.ArrivalGuardAssignmentRule()
        actual=rule.decide_batch(sim,ready,10);record=rule.records[0]
        self.assertEqual(actual,{'A':'H1','B':None})
        self.assertEqual(record['original_h1_plan'],{'A':None,'B':'H1'})
        self.assertEqual(record['original_h1_record']['assignments'],record['original_h1_plan'])
        self.assertNotEqual(record['original_h1_record']['assignments'],actual)
        self.assertEqual(record['caller_ready_order'],['A','B'])
    def test_original_parent_safety_timeout_invalidates_candidate(self):
        rows=[row('A')];sim,ready=world(rows,{('A','H1'):5},{('H1','ed_bed'):1});rule=p.ArrivalGuardAssignmentRule()
        def timed_out(*args):
            rule.parent.records.append(dict(search_stop_reason='time_limit',search_seconds=10,evaluated_moves=1,assignments={'A':'H1'}))
            return {'A':'H1'}
        with patch.object(rule.parent,'decide_batch',side_effect=timed_out):
            with self.assertRaises(p.ArrivalGuardSafetyBudgetExhausted):rule.decide_batch(sim,ready,10)
        self.assertFalse(rule.records)
    def test_empty_ready_never_fabricates_a_parent_decision(self):
        rule=p.ArrivalGuardAssignmentRule();self.assertEqual(rule.decide_batch(None,[],0),{})
        self.assertFalse(rule.records);self.assertFalse(rule.parent.records)
if __name__=='__main__':unittest.main(verbosity=2)
