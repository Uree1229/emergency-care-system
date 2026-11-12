"""Synthetic protected-wait ordering and unchanged guard invariants."""
from copy import deepcopy
import math
import unittest
from inquiry_lab import cohort_waitpareto_model as model


def owners():return [dict(patient=dict(id=pid,effective_priority=tier)) for pid,tier in zip(('A','B','C'),(4,2,1))]


def row(index,wait=100.,probabilities=(.5,.5,.5)):
    members=[dict(patient_id=pid,effective_priority=tier,metrics=dict(selection_probability=p,
        capped_wait_seconds=wait if i==0 else 0.,total_cost_seconds=100.))
        for i,(pid,tier,p) in enumerate(zip(('A','B','C'),(4,2,1),probabilities))]
    return dict(row_index=index,member_metrics=members,tier_probabilities={str(tier):p for tier,p in zip((4,2,1),probabilities)},sum_capped_wait_seconds=wait)


def select(rows,*,anchor=7,admitted=None,inventory=None,menu=None,feasible=None):
    indices=[r['row_index'] for r in rows]
    if admitted is None:
        proof=model.parent_guard.guard_rows(rows,baseline_index=anchor,owner_inventory=owners() if inventory is None else inventory,
            menu_indices=indices,feasible_row_indices=sorted(indices))
        admitted=proof['admissible_row_indices']
    return model.select_protected_wait(rows,admissible_row_indices=admitted,baseline_index=anchor,
        owner_inventory=owners() if inventory is None else inventory,menu_indices=indices if menu is None else menu,
        feasible_row_indices=sorted(indices) if feasible is None else feasible)


class ProtectedWaitSelectionTests(unittest.TestCase):
    def test_lower_wait_wins_over_higher_probability_above_protection_floor(self):
        rows=[row(7),row(2,101.,(.8,.8,.8)),row(3,90.)];before=deepcopy(rows)
        proof=select(rows)
        self.assertEqual(proof['selected_index'],3);self.assertEqual(proof['minimum_wait_seconds'],90.)
        self.assertEqual(proof['wait_surviving_row_indices'],[3]);self.assertEqual(rows,before)
        self.assertFalse(proof['additional_baseline_wait_cap_applied'])

    def test_any_owner_probability_loss_remains_excluded(self):
        rows=[row(7),row(3,0.,(.49,1.,1.)),row(2,90.)]
        proof=select(rows)
        self.assertEqual(proof['admissible_row_indices'],[7,2]);self.assertEqual(proof['selected_index'],2)
        with self.assertRaises(ValueError):select(rows,admitted=[7,3,2])

    def test_priority_filters_only_break_the_minimum_wait_band(self):
        rows=[row(7),row(1,90.,(.6,.5,.5)),row(2,90.,(.6,.6,.5)),row(3,90.,(.6,.6,.6))]
        proof=select(rows)
        self.assertEqual(proof['selected_index'],3)
        self.assertEqual([f['surviving_row_indices'] for f in proof['priority_filters']],[[1,2,3],[2,3],[3]])

    def test_exact_wait_epsilon_band_and_next_float(self):
        limit=90.+model.WAIT_EPSILON
        rows=[row(7),row(1,90.),row(2,limit,(.6,.6,.6))]
        self.assertEqual(select(rows)['selected_index'],2)
        rows[2]=row(2,math.nextafter(limit,math.inf),(.6,.6,.6))
        self.assertEqual(select(rows)['selected_index'],1)

    def test_exact_probability_epsilon_tie_band(self):
        baseline=.6-model.PROBABILITY_EPSILON
        rows=[row(7,90.,(baseline,.5,.5)),row(2,90.,(.6,.5,.5))]
        self.assertEqual(select(rows)['selected_index'],7)
        rows[1]=row(2,90.,(math.nextafter(.6,math.inf),.5,.5))
        self.assertEqual(select(rows)['selected_index'],2)

    def test_Stoploss_anchor_then_original_order_breaks_complete_ties(self):
        self.assertEqual(select([row(2),row(7),row(3)])['selected_index'],7)
        proof=select([row(7),row(2,90.),row(3,90.)])
        self.assertEqual(proof['selected_index'],2);self.assertFalse(proof['base_selected_tied'])

    def test_selected_wait_bound_follows_from_anchor_admission(self):
        for waits in ((100.,101.,99.),(100.,0.,120.),(0.,1e-10,1.),(100.,100.+1e-9,100.+2e-9)):
            proof=select([row(7,waits[0]),row(2,waits[1],(.6,.6,.6)),row(3,waits[2],(.7,.7,.7))])
            self.assertLessEqual(proof['selected_sum_capped_wait_seconds'],proof['baseline_sum_capped_wait_seconds']+model.WAIT_EPSILON)
            self.assertTrue(proof['selected_wait_within_baseline_tolerance'])
            self.assertFalse(proof['additional_baseline_wait_cap_applied'])

    def test_omitting_an_admissible_lower_wait_row_is_rejected(self):
        rows=[row(7),row(2,90.),row(3,80.)]
        with self.assertRaises(ValueError):select(rows,admitted=[7,2])
        with self.assertRaises(ValueError):select(rows,admitted=[7,3,2])

    def test_complete_row_and_owner_partitions_required(self):
        for mode in ('missing','duplicate','foreign','tier'):
            rows=[row(7),row(2)]
            if mode=='missing':rows[1]['member_metrics'].pop()
            elif mode=='duplicate':rows[1]['member_metrics'][2]=deepcopy(rows[1]['member_metrics'][0])
            elif mode=='foreign':rows[1]['member_metrics'][0]['patient_id']='FOREIGN'
            else:rows[1]['member_metrics'][0]['effective_priority']=2
            with self.assertRaises(ValueError):select(rows,admitted=[7,2])
        with self.assertRaises(ValueError):select([row(7),row(2)],menu=[7],admitted=[7])
        with self.assertRaises(ValueError):select([row(7),row(2)],feasible=[7],admitted=[7])
        with self.assertRaises(ValueError):select([row(7)],admitted=[],inventory=owners())

    def test_nonfinite_or_inconsistent_existing_aggregate_rejected(self):
        for value in (math.nan,math.inf,-1.,True):
            rows=[row(7),row(2)];rows[1]['sum_capped_wait_seconds']=value
            with self.assertRaises(ValueError):select(rows,admitted=[7,2])
        rows=[row(7),row(2)];rows[1]['tier_probabilities']['4']=.7
        with self.assertRaises(ValueError):select(rows,admitted=[7,2])
        rows=[row(7),row(2)];rows[1]['sum_capped_wait_seconds']=99.
        with self.assertRaises(ValueError):select(rows,admitted=[7,2])
        rows=[row(7,probabilities=(1.,.5,.5)),row(2,probabilities=(1.,.5,.5))]
        rows[1]['tier_probabilities']['4']=True
        with self.assertRaises(ValueError):select(rows,admitted=[7,2])

    def test_owner_order_and_typed_identity_renaming_preserve_selection(self):
        rows=[row(7),row(2,90.)];inventory=owners()
        for item in rows:item['member_metrics'].reverse()
        inventory.reverse();self.assertEqual(select(rows,inventory=inventory)['selected_index'],2)
        mapping={'A':1,'B':'1','C':'C_RENAMED'}
        for owner in inventory:owner['patient']['id']=mapping[owner['patient']['id']]
        for item in rows:
            for member in item['member_metrics']:member['patient_id']=mapping[member['patient_id']]
        self.assertEqual(select(rows,inventory=inventory)['selected_index'],2)


if __name__=='__main__':unittest.main()
