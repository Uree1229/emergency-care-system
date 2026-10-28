"""Synthetic exact-H support predicates and explicit selector subdomains."""
from copy import deepcopy
import inspect
import math
import unittest
from inquiry_lab import cohort_pairshield_model as model


def item(hospital,wait=0.,masses=None,mode='queued'):
    return dict(hospital=hospital,mode=mode,wait_seconds=wait,masses=[.1,.1] if masses is None else list(masses))


def people(ids=('A','B')):
    return [dict(patient_id=pid,cell_edges_seconds=[5,10],fixed_initial_items=[item('X'+str(i),10.,[0.,0.]) if i==0 else item('X'+str(i))]) for i,pid in enumerate(ids)]


def row(index,persons=None,wait=100.,probabilities=(.5,.5,.5),ids=('A','B')):
    members=[dict(patient_id=pid,effective_priority=t,metrics=dict(selection_probability=p,capped_wait_seconds=wait if i==0 else 0.,total_cost_seconds=100.))
        for i,(pid,t,p) in enumerate(zip((*ids,'R'),(4,2,1),probabilities))]
    return dict(row_index=index,first_cohort_envelope=dict(patient_scores=deepcopy(people(ids) if persons is None else persons)),member_metrics=members,
        tier_probabilities={str(t):p for t,p in zip((4,2,1),probabilities)},sum_capped_wait_seconds=wait)


def guard(rows,ids=('A','B'),anchor=7,menu=None,feasible=None):
    indices=[r['row_index'] for r in rows]
    return model.guard_rows(rows,baseline_index=anchor,current_pair_patient_ids=list(ids),
        menu_indices=indices if menu is None else menu,feasible_row_indices=sorted(indices) if feasible is None else feasible)


def inventory(ids=('A','B')):
    return [dict(patient=dict(id=pid,effective_priority=t)) for pid,t in zip((*ids,'R'),(4,2,1))]


class MeanQueueMembershipTests(unittest.TestCase):
    def test_removed_baseline_boundary_contact_is_rejected(self):
        change=people();change[0]['fixed_initial_items']=[item('NEW')]
        rows=[row(7),row(2,change)];before=deepcopy(rows);proof=guard(rows)
        self.assertEqual(proof['admissible_row_indices'],[7]);self.assertEqual(proof['rejected_row_indices'],[2])
        self.assertEqual(proof['row_checks'][1]['owner_checks'][0]['removed_baseline_boundary_hospitals'],['X0'])
        self.assertEqual(rows,before)

    def test_added_candidate_boundary_contact_is_rejected(self):
        change=people();change[1]['fixed_initial_items']=[item('NEW',10.,[0.,0.])]
        proof=guard([row(7),row(2,change)])
        self.assertEqual(proof['row_checks'][1]['owner_checks'][1]['added_candidate_boundary_hospitals'],['NEW'])
        self.assertEqual(proof['admissible_row_indices'],[7])

    def test_shared_hospital_may_change_boundary_class_in_both_directions(self):
        change=people();change[0]['fixed_initial_items']=[item('X0',0.,[.1,.1])]
        change[1]['fixed_initial_items']=[item('X1',10.,[0.,0.])]
        proof=guard([row(7),row(2,change)])
        self.assertEqual(proof['admissible_row_indices'],[7,2])
        self.assertTrue(all(c['shared_boundary_status_changes'] for c in proof['row_checks'][1]['owner_checks']))

    def test_exact_H_is_boundary_but_next_float_below_is_not(self):
        for wait,blocked in ((10.,True),(math.nextafter(10.,-math.inf),False),(math.nextafter(10.,math.inf),True)):
            baseline=people();baseline[0]['fixed_initial_items']=[item('X0',wait,[0.,0.])]
            change=deepcopy(baseline);change[0]['fixed_initial_items']=[item('NEW')]
            self.assertEqual(2 in guard([row(7,baseline),row(2,change)])['rejected_row_indices'],blocked)

    def test_nonzero_stored_mass_and_nonqueued_mode_do_not_meet_predicate(self):
        for descriptor in (item('X0',10.,[math.ulp(0.),0.]),item('X0',10.,[0.,0.],mode='residual')):
            baseline=people();baseline[0]['fixed_initial_items']=[descriptor]
            change=deepcopy(baseline);change[0]['fixed_initial_items']=[item('NEW')]
            self.assertEqual(guard([row(7,baseline),row(2,change)])['admissible_row_indices'],[7,2])

    def test_hospital_opportunity_cannot_be_moved_to_the_other_owner(self):
        change=people();change[0]['fixed_initial_items']=[item('NEW')];change[1]['fixed_initial_items']=[item('X0')]
        self.assertIn(2,guard([row(7),row(2,change)])['rejected_row_indices'])

    def test_typed_owner_ids_and_initial_order_are_supported(self):
        ids=(1,'1');rows=[row(7,ids=ids),row(2,ids=ids)]
        for r in rows:r['first_cohort_envelope']['patient_scores'].reverse()
        proof=guard(rows,ids=ids)
        self.assertEqual(proof['admissible_row_indices'],[7,2]);self.assertIs(type(proof['baseline_owner_support'][0]['patient_id']),int)
        with self.assertRaises(ValueError):guard(rows,ids=(True,'1'))

    def test_complete_source_rows_owner_H_and_cardinality_are_required(self):
        for mode in ('missing_owner','duplicate_owner','different_H','duplicate_hospital','wrong_cardinality','invalid_mass'):
            rows=[row(7),row(2)];people2=rows[1]['first_cohort_envelope']['patient_scores']
            if mode=='missing_owner':people2.pop()
            elif mode=='duplicate_owner':people2[1]=deepcopy(people2[0])
            elif mode=='different_H':people2[0]['cell_edges_seconds']=[5,11]
            elif mode=='duplicate_hospital':people2[0]['fixed_initial_items']*=2
            elif mode=='wrong_cardinality':people2[0]['fixed_initial_items']=[]
            else:people2[0]['fixed_initial_items'][0]['masses']=[False,0.]
            with self.assertRaises(ValueError):guard(rows)
        for kwargs in (dict(menu=[7]),dict(feasible=[7]),dict(anchor=9)):
            with self.assertRaises(ValueError):guard([row(7),row(2)],**kwargs)

    def test_empty_first_wave_keeps_baseline_admitted(self):
        first=people()
        for p in first:p['fixed_initial_items']=[]
        proof=guard([row(7,first)])
        self.assertEqual(proof['admissible_row_indices'],[7]);self.assertTrue(proof['baseline_always_admissible'])

    def test_explicit_intersection_selector_retains_all_owner_P_guard(self):
        changed=people();changed[0]['fixed_initial_items']=[item('NEW')]
        rows=[row(7),row(2,changed,0.,(.9,.9,.9)),row(3,wait=80.),row(4,wait=0.,probabilities=(.5,.5,.49))]
        indices=[r['row_index'] for r in rows];p=model.selector.parent_guard.guard_rows(rows,baseline_index=7,owner_inventory=inventory(),menu_indices=indices,feasible_row_indices=sorted(indices));s=guard(rows)
        result=model.select_supported_wait(rows,baseline_index=7,owner_inventory=inventory(),menu_indices=indices,feasible_row_indices=sorted(indices),
            probability_admissible_row_indices=p['admissible_row_indices'],support_admissible_row_indices=s['admissible_row_indices'])
        self.assertEqual(result['projected_domain_row_indices'],[7,3]);self.assertEqual(result['selected_index'],3)
        self.assertFalse(result['original_JOINT_rows_reclassified']);self.assertEqual(result['unchanged_selector']['selected_index'],3)

    def test_selector_rejects_omitted_support_row_or_lost_original_anchor(self):
        rows=[row(7),row(2,wait=80.)];indices=[7,2]
        args=dict(baseline_index=7,owner_inventory=inventory(),menu_indices=indices,feasible_row_indices=[2,7],probability_admissible_row_indices=indices)
        with self.assertRaises(ValueError):model.select_supported_wait(rows,**args,support_admissible_row_indices=[7])
        args['probability_admissible_row_indices']=[2]
        with self.assertRaises(ValueError):model.select_supported_wait(rows,**args,support_admissible_row_indices=indices)
        typed=[row(7),row(1,wait=80.)]
        with self.assertRaises(ValueError):model.select_supported_wait(typed,baseline_index=7,owner_inventory=inventory(),menu_indices=[7,1],feasible_row_indices=[1,7],probability_admissible_row_indices=[7,1],support_admissible_row_indices=[7,True])

    def test_no_outcome_or_seed_input_in_guard_interface(self):
        self.assertEqual(set(inspect.signature(model.guard_rows).parameters),{'row_scores','baseline_index','current_pair_patient_ids','menu_indices','feasible_row_indices'})


if __name__=='__main__':unittest.main()
