"""Synthetic all-owner latency tradeoffs and unchanged probability protection."""
from copy import deepcopy
import inspect
import math
import unittest
from inquiry_lab import cohort_tailwait_model as model


IDS=('CURRENT_A','CURRENT_B','TAIL_A','TAIL_B','RESIDUAL')
TIERS=(4,2,1,4,4)


def inventory(ids=IDS):return [dict(patient=dict(id=pid,effective_priority=t)) for pid,t in zip(ids,TIERS)]


def row(index,waits=(100.,100.,100.,100.,100.),probabilities=(.5,.5,.5,.5,.5),ids=IDS):
    members=[dict(patient_id=pid,effective_priority=t,metrics=dict(selection_probability=p,capped_wait_seconds=w,total_cost_seconds=100.)) for pid,t,p,w in zip(ids,TIERS,probabilities,waits)]
    return dict(row_index=index,member_metrics=members,tier_probabilities={str(t):sum(m['metrics']['selection_probability'] for m in members if m['effective_priority']==t) for t in (4,2,1)},sum_capped_wait_seconds=sum(waits))


def guard(rows,owners=None,anchor=7,menu=None,feasible=None):
    indices=[r['row_index'] for r in rows]
    return model.guard_rows(rows,baseline_index=anchor,owner_inventory=inventory() if owners is None else owners,
        menu_indices=indices if menu is None else menu,feasible_row_indices=sorted(indices) if feasible is None else feasible)


class AllOwnerWaitTests(unittest.TestCase):
    def test_current_pair_improvement_cannot_hide_projected_tail_wait_loss(self):
        rows=[row(7),row(2,(75.,75.,106.5,106.,100.))];before=deepcopy(rows);proof=guard(rows)
        self.assertLess(rows[1]['sum_capped_wait_seconds'],rows[0]['sum_capped_wait_seconds'])
        self.assertEqual(proof['admissible_row_indices'],[7]);self.assertEqual(proof['rejected_row_indices'],[2])
        self.assertEqual([x['patient_id'] for x in proof['row_checks'][1]['owner_regressions']],['TAIL_A','TAIL_B'])
        self.assertEqual(rows,before)

    def test_queried_residual_or_current_owner_wait_loss_is_also_protected(self):
        for index in (0,1,4):
            waits=[80.]*5;waits[index]=101.;proof=guard([row(7),row(2,waits)])
            self.assertEqual(proof['row_checks'][1]['owner_regressions'][0]['patient_id'],IDS[index])
            self.assertEqual(proof['admissible_row_indices'],[7])

    def test_exact_existing_wait_epsilon_and_adjacent_float(self):
        boundary=100.+model.WAIT_EPSILON
        rows=[row(7),row(2,(boundary,100.,100.,100.,100.)),row(3,(math.nextafter(boundary,math.inf),100.,100.,100.,100.))]
        proof=guard(rows);self.assertEqual(proof['admissible_row_indices'],[7,2]);self.assertEqual(proof['rejected_row_indices'],[3])
        self.assertEqual(proof['row_checks'][2]['owner_regressions'][0]['maximum_allowed_wait_seconds'],boundary)

    def test_all_owner_improvement_and_baseline_are_admitted(self):
        proof=guard([row(7),row(2,(90.,95.,90.,100.,80.))])
        self.assertEqual(proof['admissible_row_indices'],[7,2]);self.assertTrue(proof['baseline_always_admissible'])
        self.assertEqual(proof['compared_owner_count'],5);self.assertEqual(proof['checked_row_count'],2)

    def test_typed_ids_and_owner_order_preserve_complete_comparison(self):
        ids=(1,'1','TAIL_A','TAIL_B','RESIDUAL');owners=inventory(ids);rows=[row(7,ids=ids),row(2,(100.,101.,100.,100.,100.),ids=ids)]
        for r in rows:r['member_metrics'].reverse()
        proof=guard(rows,owners=owners);self.assertEqual(proof['row_checks'][1]['owner_regressions'][0]['patient_id'],'1')
        self.assertIs(type(proof['baseline_owner_waits'][0]['patient_id']),int)

    def test_missing_duplicate_foreign_tier_and_bad_wait_are_rejected(self):
        for mode in ('missing','duplicate','foreign','tier','wait'):
            rows=[row(7),row(2)];members=rows[1]['member_metrics']
            if mode=='missing':members.pop()
            elif mode=='duplicate':members[-1]=deepcopy(members[0])
            elif mode=='foreign':members[0]['patient_id']='FOREIGN'
            elif mode=='tier':members[0]['effective_priority']=True
            else:members[0]['metrics']['capped_wait_seconds']=True
            with self.assertRaises(ValueError):guard(rows)
        for bad in (-1.,math.inf,math.nan):
            rows=[row(7),row(2)];rows[1]['member_metrics'][0]['metrics']['capped_wait_seconds']=bad
            with self.assertRaises(ValueError):guard(rows)

    def test_complete_original_coverage_order_and_anchor_required(self):
        rows=[row(7),row(2)]
        for kwargs in (dict(menu=[7]),dict(menu=[2,7]),dict(feasible=[7]),dict(anchor=9)):
            with self.assertRaises(ValueError):guard(rows,**kwargs)
        with self.assertRaises(ValueError):guard([row(7),row(7)])
        with self.assertRaises(ValueError):guard([],menu=[],feasible=[])

    def test_probability_and_wait_intersection_keeps_original_floor(self):
        rows=[row(7),row(2,(75.,75.,106.,106.,100.)),row(3,(50.,50.,50.,50.,50.),(.5,.5,.5,.5,.49)),row(4,(90.,90.,90.,90.,90.))]
        indices=[r['row_index'] for r in rows];owners=inventory();p=model.selector.parent_guard.guard_rows(rows,baseline_index=7,owner_inventory=owners,menu_indices=indices,feasible_row_indices=sorted(indices));w=guard(rows)
        result=model.select_wait_guarded(rows,baseline_index=7,owner_inventory=owners,menu_indices=indices,feasible_row_indices=sorted(indices),
            probability_admissible_row_indices=p['admissible_row_indices'],wait_admissible_row_indices=w['admissible_row_indices'])
        self.assertEqual(result['projected_domain_row_indices'],[7,4]);self.assertEqual(result['selected_index'],4)
        self.assertFalse(result['original_JOINT_rows_reclassified'])

    def test_omitted_wait_admissible_row_or_typed_index_forgery_rejected(self):
        rows=[row(7),row(1,(90.,90.,90.,90.,90.))]
        args=dict(baseline_index=7,owner_inventory=inventory(),menu_indices=[7,1],feasible_row_indices=[1,7],probability_admissible_row_indices=[7,1])
        for invalid in ([7],[7,True],[]):
            with self.assertRaises(ValueError):model.select_wait_guarded(rows,**args,wait_admissible_row_indices=invalid)

    def test_wait_admission_does_not_read_outcomes_or_future_events(self):
        self.assertEqual(set(inspect.signature(model.guard_rows).parameters),{'row_scores','baseline_index','owner_inventory','menu_indices','feasible_row_indices'})
        self.assertEqual(model.WAIT_EPSILON,model.selector.WAIT_EPSILON)


if __name__=='__main__':unittest.main()
