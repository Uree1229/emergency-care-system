"""Synthetic final-score constraints; no policy, simulation or outcome runs."""
import ast
from copy import deepcopy
import inspect
import math
import unittest
from inquiry_lab import cohort_pareto_model as model


def inventory(ids=('A','B','C'),tiers=(4,2,1)):
    return [dict(patient=dict(id=pid,effective_priority=tier)) for pid,tier in zip(ids,tiers)]


def row(index,probabilities=(.5,.6,.7),*,ids=('A','B','C'),tiers=(4,2,1)):
    return dict(row_index=index,member_metrics=[dict(patient_id=pid,effective_priority=tier,
        metrics=dict(selection_probability=p,capped_wait_seconds=100.,total_cost_seconds=100.))
        for pid,tier,p in zip(ids,tiers,probabilities)],tier_probabilities={'4':100.,'2':100.,'1':100.},sum_capped_wait_seconds=0.)


def guard(rows,*,baseline=7,owners=None,menu=None,feasible=None):
    indices=[r['row_index'] for r in rows]
    return model.guard_rows(rows,baseline_index=baseline,owner_inventory=inventory() if owners is None else owners,
        menu_indices=indices if menu is None else menu,feasible_row_indices=sorted(indices) if feasible is None else feasible)


class PerOwnerProbabilityGuardTests(unittest.TestCase):
    def test_net_probability_gain_cannot_hide_any_owner_loss(self):
        rows=[row(7),row(2,(.49,1.,1.)),row(9,(.5,.6,.8))];before=deepcopy(rows);proof=guard(rows)
        self.assertEqual(proof['admissible_row_indices'],[7,9]);self.assertEqual(proof['rejected_row_indices'],[2])
        regression=proof['row_checks'][1]['owner_regressions'][0]
        self.assertEqual(regression['patient_id'],'A');self.assertEqual(regression['effective_priority'],4)
        self.assertEqual(regression['minimum_allowed_probability'],.5-1e-12)
        self.assertEqual(rows,before)

    def test_every_tier_and_owner_is_guarded_independently(self):
        rows=[row(7)]
        for index in range(3):
            values=[.5,.6,.7];values[index]-=.01;rows.append(row(index,values))
        proof=guard(rows)
        self.assertEqual(proof['admissible_row_indices'],[7])
        self.assertEqual([r['owner_regressions'][0]['effective_priority'] for r in proof['row_checks'][1:]],[4,2,1])
        self.assertEqual(proof['compared_owner_count'],3);self.assertEqual(proof['checked_row_count'],4)

    def test_exact_epsilon_boundary_and_next_representable_value(self):
        threshold=.5-model.PROBABILITY_EPSILON
        rows=[row(7),row(1,(threshold,.6,.7)),row(2,(math.nextafter(threshold,-math.inf),.6,.7)),
            row(3,(math.nextafter(threshold,math.inf),.6,.7))]
        proof=guard(rows)
        self.assertEqual(proof['admissible_row_indices'],[7,1,3]);self.assertEqual(proof['rejected_row_indices'],[2])

    def test_zero_and_one_probability_edges_keep_baseline_admissible(self):
        rows=[row(7,(0.,1.,.5)),row(2,(0.,1.-1e-12,.5))]
        proof=guard(rows)
        self.assertTrue(proof['baseline_always_admissible']);self.assertEqual(proof['admissible_row_indices'],[7,2])
        self.assertTrue(proof['row_checks'][0]['is_baseline'])
        self.assertEqual([p['selection_probability'] for p in proof['baseline_owner_probabilities']],[0.,1.,.5])

    def test_typed_int_and_string_owner_ids_are_distinct(self):
        ids=(1,'1','A');owners=inventory(ids)
        proof=guard([row(7,ids=ids),row(3,(.5,.59,.7),ids=ids)],owners=owners)
        self.assertEqual(proof['row_checks'][1]['owner_regressions'][0]['patient_id'],'1')
        self.assertIs(type(proof['baseline_owner_probabilities'][0]['patient_id']),int)

    def test_per_row_owner_order_does_not_change_guard(self):
        rows=[row(7),row(3,(.49,.6,.7))];before=guard(rows)
        for item in rows:item['member_metrics'].reverse()
        self.assertEqual(guard(rows),before)

    def test_first_and_previously_queried_owner_have_same_constraint(self):
        owners=inventory();owners[0]['inquiries']=[]
        owners[1]['inquiries']=[dict(hospital_id='H1',state='PENDING',last_sent_at=1000)]
        proof=guard([row(7),row(3,(.5,.59,.7))],owners=owners)
        self.assertEqual(proof['admissible_row_indices'],[7])
        self.assertEqual(proof['row_checks'][1]['owner_regressions'][0]['patient_id'],'B')

    def test_missing_duplicate_extra_or_wrong_tier_owner_partition_rejected(self):
        variants=[]
        changed=row(2);changed['member_metrics'].pop();variants.append(changed)
        changed=row(2);changed['member_metrics'][2]=deepcopy(changed['member_metrics'][0]);variants.append(changed)
        changed=row(2);changed['member_metrics'][0]['patient_id']='UNKNOWN';variants.append(changed)
        changed=row(2);changed['member_metrics'][0]['effective_priority']=2;variants.append(changed)
        changed=row(2);changed['member_metrics'][0]['effective_priority']=True;variants.append(changed)
        for bad in variants:
            with self.assertRaises(ValueError):guard([row(7),bad])
        with self.assertRaises(ValueError):guard([row(7)],owners=inventory()+[inventory()[0]])
        with self.assertRaises(ValueError):guard([row(7)],owners=[])

    def test_invalid_probability_or_owner_id_rejected_even_on_baseline(self):
        for value in (math.inf,math.nan,-.1,1.1,True):
            bad=row(7);bad['member_metrics'][0]['metrics']['selection_probability']=value
            with self.assertRaises(ValueError):guard([bad])
        for pid in (True,None,''):
            ids=(pid,'B','C')
            with self.assertRaises(ValueError):guard([row(7,ids=ids)],owners=inventory(ids))

    def test_complete_coverage_order_unique_rows_and_baseline_required(self):
        rows=[row(7),row(3)]
        for kwargs in (dict(menu=[7]),dict(menu=[3,7]),dict(menu=[7,7]),dict(feasible=[7]),dict(feasible=[3,7,9]),dict(baseline=9)):
            with self.assertRaises(ValueError):guard(rows,**kwargs)
        with self.assertRaises(ValueError):guard([row(7),row(7)])
        with self.assertRaises(ValueError):guard([])

    def test_no_guard_effect_case_preserves_entire_menu(self):
        rows=[row(7),row(3,(.5,.7,.7)),row(2,(.6,.6,.8))];proof=guard(rows)
        self.assertEqual(proof['admissible_row_indices'],[7,3,2]);self.assertEqual(proof['rejected_row_indices'],[])
        self.assertTrue(all(not item['owner_regressions'] for item in proof['row_checks']))

    def test_no_source_model_execution_or_outcome_dependency(self):
        source=ast.parse(inspect.getsource(model))
        imports=[alias.name for node in ast.walk(source) if isinstance(node,(ast.Import,ast.ImportFrom)) for alias in node.names]
        self.assertFalse(any(any(token in name for token in ('policy','audit','runner','simulation','scenario','experiment')) for name in imports))
        self.assertEqual(set(inspect.signature(model.guard_rows).parameters),
            {'row_scores','baseline_index','owner_inventory','menu_indices','feasible_row_indices'})


if __name__=='__main__':unittest.main()
