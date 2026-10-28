from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from simulation import arrival_guard_priority_policy_20261028 as p


def row(pid,priority=1,options=('H1',),demand=1):
    return dict(id=pid,priority=priority,demand={'ed_bed':demand},options=list(options))


def world(rows,travel,free):
    sim=SimpleNamespace(hospitals={hid:SimpleNamespace(free=lambda resource,hid=hid:free[(hid,resource)]) for hid,_ in free},
        position=lambda patient,t:(0,0),travel=lambda patient,hid,position:travel[(patient.id,hid)])
    ready=[(SimpleNamespace(id=r['id'],priority=r['priority'],demand=r['demand'],t_req=0,caps=set()),r['options']) for r in rows]
    return sim,ready


class PriorityGuardPolicyTests(unittest.TestCase):
    def test_same_count_weight4_gain_over_weight2_uses_original_H1(self):
        rows=[row('P05',2),row('P11',4)];travel={(r['id'],'H1'):5 for r in rows}
        result=p.guard_current_plan(rows,travel,{('H1','ed_bed'):1},{'P05':None,'P11':'H1'})
        self.assertTrue(result['same_current_count_priority4_gain']);self.assertFalse(result['strict_current_count_gain'])
        self.assertEqual(result['actual_plan'],{'P05':None,'P11':'H1'})
        self.assertEqual((result['baseline_priority4_assigned_count'],result['original_h1_priority4_assigned_count'],result['actual_priority4_assigned_count']),(0,1,1))
        self.assertEqual(result['reason'],'same_current_count_priority4_gain_without_baseline_priority_cost')

    def test_same_count_weight1_weight2_swap_keeps_caller_nearest(self):
        rows=[row('P03',1),row('P10',2)]
        result=p.guard_current_plan(rows,{(r['id'],'H1'):5 for r in rows},{('H1','ed_bed'):1},{'P03':None,'P10':'H1'})
        self.assertEqual(result['actual_plan'],{'P03':'H1','P10':None})
        self.assertFalse(result['used_h1_proposal']);self.assertFalse(result['same_current_count_priority4_gain'])

    def test_same_weight4_count_same_admission_count_keeps_nearest(self):
        rows=[row('A',4,('H1','H2')),row('B',4,('H1','H2'))]
        result=p.guard_current_plan(rows,{('A','H1'):1,('A','H2'):2,('B','H1'):2,('B','H2'):100},
            {('H1','ed_bed'):1,('H2','ed_bed'):1},{'A':'H2','B':'H1'})
        self.assertEqual(result['actual_plan'],{'A':'H1','B':'H2'})
        self.assertFalse(result['used_h1_proposal'])

    def test_count_gain_retains_scarcity_augmentation(self):
        rows=[row('A',4,('H1','H2')),row('B',4)]
        parent={'A':'H2','B':'H1'};travel={('A','H1'):5,('A','H2'):8,('B','H1'):5};free={('H1','ed_bed'):1,('H2','ed_bed'):1}
        saved=deepcopy((rows,travel,free,parent));result=p.guard_current_plan(rows,travel,free,parent)
        self.assertTrue(result['strict_current_count_gain']);self.assertEqual(result['actual_plan'],parent)
        self.assertEqual((rows,travel,free,parent),saved)

    def test_more_people_with_greater_weighted_miss_is_rejected(self):
        rows=[row('A',4,demand=2),row('B'),row('C')]
        result=p.guard_current_plan(rows,{(r['id'],'H1'):1 for r in rows},{('H1','ed_bed'):2},{'A':None,'B':'H1','C':'H1'})
        self.assertTrue(result['strict_current_count_gain']);self.assertFalse(result['baseline_priority_cost_nonincrease'])
        self.assertEqual(result['actual_plan'],{'A':'H1','B':None,'C':None})

    def test_same_count_weight4_gain_cannot_override_weighted_miss_guard(self):
        rows=[row('B'+str(i),2) for i in range(4)]+[row('H0',4)]+[row('H'+str(i)) for i in range(1,4)]
        parent={r['id']:'H1' if r['id'].startswith('H') else None for r in rows}
        result=p.guard_current_plan(rows,{(r['id'],'H1'):1 for r in rows},{('H1','ed_bed'):4},parent)
        self.assertTrue(result['same_current_count_priority4_gain']);self.assertFalse(result['baseline_priority_cost_nonincrease'])
        self.assertFalse(result['used_h1_proposal']);self.assertEqual(result['actual_priority4_assigned_count'],0)

    def test_losing_admission_count_is_not_permitted_even_with_more_weight4(self):
        rows=[row('A',2),row('B',2),row('C',4,demand=2)]
        result=p.guard_current_plan(rows,{(r['id'],'H1'):1 for r in rows},{('H1','ed_bed'):2},{'A':None,'B':None,'C':'H1'})
        self.assertFalse(result['used_h1_proposal']);self.assertFalse(result['same_current_count_priority4_gain'])
        self.assertEqual(result['actual_assigned_count'],2)

    def test_current_count_gain_does_not_claim_whole_episode_weight4_guarantee(self):
        rows=[row('A',4,demand=2),row('B',2),row('C',2)]
        result=p.guard_current_plan(rows,{(r['id'],'H1'):1 for r in rows},{('H1','ed_bed'):2},{'A':None,'B':'H1','C':'H1'})
        self.assertTrue(result['used_h1_proposal']);self.assertEqual(result['actual_priority4_assigned_count'],0)
        self.assertTrue(result['objective_change_disclosed'])
        self.assertFalse(result['whole_episode_priority4_nonregression_guaranteed'])

    def test_all_two_patient_weight_pairs_and_feasible_parent_choices_follow_predicate(self):
        for a in (1,2,4):
            for b in (1,2,4):
                for selected in ('A','B',None):
                    with self.subTest(a=a,b=b,selected=selected):
                        rows=[row('A',a),row('B',b)];parent={r['id']:'H1' if r['id']==selected else None for r in rows}
                        result=p.guard_current_plan(rows,{('A','H1'):1,('B','H1'):1},{('H1','ed_bed'):1},parent)
                        expected=selected=='B' and b==4 and a!=4
                        self.assertEqual(result['used_h1_proposal'],expected)
                        self.assertEqual(result['actual_plan'],parent if expected else {'A':'H1','B':None})

    def test_infeasible_parent_unknown_travel_and_resource_rejected(self):
        rows=[row('A'),row('B')]
        for travel,free,parent in (({('A','H1'):1,('B','H1'):1},{('H1','ed_bed'):1},{'A':'H1','B':'H1'}),
            ({},{('H1','ed_bed'):1},{'A':None,'B':None}),
            ({('A','H1'):1,('B','H1'):1},{},{'A':None,'B':None})):
            with self.assertRaises(ValueError):p.guard_current_plan(rows,travel,free,parent)

    def test_original_H1_and_current_nearest_records_remain_distinct(self):
        rows=[row('A',1),row('B',2)];sim,ready=world(rows,{('A','H1'):5,('B','H1'):5},{('H1','ed_bed'):1})
        rule=p.ArrivalGuardPriorityAssignmentRule();actual=rule.decide_batch(sim,ready,10);record=rule.records[0]
        self.assertEqual(actual,{'A':'H1','B':None});self.assertEqual(record['caller_ready_order'],['A','B'])
        self.assertEqual(record['original_h1_record']['assignments'],{'A':None,'B':'H1'})
        self.assertNotEqual(record['original_h1_record']['assignments'],actual)
        self.assertNotIn('decision_index',record['original_h1_record'])

    def test_parent_timeout_rejects_phase_instead_of_silent_fallback(self):
        rows=[row('A')];sim,ready=world(rows,{('A','H1'):1},{('H1','ed_bed'):1});rule=p.ArrivalGuardPriorityAssignmentRule()
        def timeout(*args):
            rule.parent.records.append(dict(search_stop_reason='time_limit',search_seconds=10,evaluated_moves=1,assignments={'A':'H1'}))
            return {'A':'H1'}
        with patch.object(rule.parent,'decide_batch',side_effect=timeout):
            with self.assertRaises(p.ArrivalGuardSafetyBudgetExhausted):rule.decide_batch(sim,ready,0)
        self.assertFalse(rule.records)

    def test_empty_ready_has_no_parent_proposal(self):
        rule=p.ArrivalGuardPriorityAssignmentRule();self.assertEqual(rule.decide_batch(None,[],0),{})
        self.assertFalse(rule.records);self.assertFalse(rule.parent.records)


if __name__=='__main__':unittest.main(verbosity=2)
