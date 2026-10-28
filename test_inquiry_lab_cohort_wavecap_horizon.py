"""Synthetic planner/cap-law tests; no scenario or performance benchmark."""
import ast
from copy import deepcopy
import inspect
import math
import unittest
from inquiry_lab import horizon_policy as old, cohort_wavecap_horizon as h
from test_inquiry_lab_horizon_policy import inputs, context


def own_inputs(count=3, age=15, **changes):
    now=1015
    own=[dict(hospital='OWN'+str(i),patient='P',submitted_at=now-age,travel_seconds=300+i) for i in range(count)]
    values=inputs(now=now, pending=[{k:v for k,v in q.items() if k!='travel_seconds'} for q in own],
        patient_pending=own,last_contact_at=now-age,time_context=context(now=now))
    values.update(changes)
    return values


class WavecapHorizonTests(unittest.TestCase):
    def test_only_identity_changes_configuration(self):
        a=old.spec();b=h.spec()
        for key in ('name','label'):a.pop(key);b.pop(key)
        self.assertEqual(a,b)
        self.assertIs(h.Beliefs,old.Beliefs)
        self.assertIs(h._queue_wait_estimate,old._queue_wait_estimate)

    def test_cdf_edges_and_objective_functions_exact_source_AST(self):
        for name in ('cell_edges','masses_at_edges','bounded_expected_cost'):
            a=ast.dump(ast.parse(inspect.getsource(getattr(old,name))),include_attributes=False)
            b=ast.dump(ast.parse(inspect.getsource(getattr(h,name))),include_attributes=False)
            self.assertEqual(a,b)

    def test_original_pending_CDF_and_new_queue_math_exact_source_AST(self):
        def nested(module,name):
            root=ast.parse(inspect.getsource(module.decide))
            return ast.dump(next(n for n in ast.walk(root) if isinstance(n,ast.FunctionDef) and n.name==name),include_attributes=False)
        for name in ('pending_item','new_item','inquiry_cost','planned_item','plan_cost'):
            self.assertEqual(nested(old,name),nested(h,name))

    def test_original_age_180_counts_and_181_releases(self):
        own=[dict(submitted_at=0)]
        rows=h.plan_occupancy({'A':180,'B':181},own,0)
        self.assertEqual(rows,[dict(offset_seconds=180,original_fresh_count=1,planned_fresh_count=1,total_fresh_count=2),
            dict(offset_seconds=181,original_fresh_count=0,planned_fresh_count=2,total_fresh_count=2)])

    def test_same_plan_age_180_counts_and_181_releases(self):
        rows=h.plan_occupancy({'A':0,'B':180,'C':181},[],0)
        self.assertEqual([r['planned_fresh_count'] for r in rows],[1,2,2])

    def test_same_clock_wave_counts_entire_batch_once_per_hospital(self):
        rows=h.plan_occupancy(dict(A=15,B=15,C=None),[dict(submitted_at=0)]*3,0)
        self.assertEqual(rows,[dict(offset_seconds=15,original_fresh_count=3,planned_fresh_count=2,total_fresh_count=5)])
        self.assertEqual(h.plan_occupancy(dict(A=None),[],0),[])

    def test_actual_planner_every_wave_safe_and_inputs_unchanged(self):
        for fresh in (1,3,4):
            args=own_inputs(fresh);before=deepcopy(args)
            value=h.decide(h.NAME,**args)
            self.assertEqual(args,before)
            self.assertTrue(all(r['total_fresh_count']<=4 for r in h.recorded_occupancy(args,value)))
            self.assertLessEqual(len(value['send']),4-fresh)
            self.assertEqual(value['next_review_at'],args['now']+15)
            self.assertEqual(value['evidence']['wavecap_plan']['occupancy'],h.recorded_occupancy(args,value))

    def test_queued_and_residual_pending_mass_kept_with_stale_own(self):
        args=own_inputs(1,age=181)
        a=old.decide(old.NAME,**args);b=h.decide(h.NAME,**args)
        self.assertEqual(b['evidence']['expected_cost_without_new'],a['evidence']['expected_cost_without_new'])
        self.assertEqual(b['evidence']['own_observation_timed_out_count'],1)
        self.assertLess(b['evidence']['expected_cost_without_new'],1785)
        self.assertTrue(b['send'])

    def test_full_budget_and_empty_pool_preserve_no_send(self):
        for changes in (dict(max_new=0),dict(candidates=[])):
            args=own_inputs(4,**changes);value=h.decide(h.NAME,**args)
            self.assertEqual(value['send'],[])
            self.assertEqual(value['evidence']['wavecap_plan']['occupancy'],[])

    def test_no_fresh_pending_safety_still_sends(self):
        value=h.decide(h.NAME,**inputs())
        self.assertTrue(value['send'])
        self.assertTrue(all(r['total_fresh_count']<=4 for r in value['evidence']['wavecap_plan']['occupancy']))

    def test_forced_trial_filters_even_when_cheaper_invalid(self):
        pool=[dict(hospital='A',travel_seconds=1),dict(hospital='B',travel_seconds=2)]
        def feasible(plan):return plan['A']!=0
        row,plan,reset=h.force_initial_plan(dict(A=15,B=30),pool,feasible,lambda p:0 if p['A']==0 else 10)
        self.assertEqual(row['hospital'],'B');self.assertEqual(plan,dict(A=15,B=0));self.assertFalse(reset)

    def test_forced_reset_keeps_only_one_contact_and_rechecks_feasibility(self):
        pool=[dict(hospital='A',travel_seconds=2),dict(hospital='B',travel_seconds=1)]
        def feasible(plan):return sum(x is not None for x in plan.values())==1
        row,plan,reset=h.force_initial_plan(dict(A=15,B=30),pool,feasible,lambda p:7)
        self.assertTrue(reset);self.assertEqual(row['hospital'],'B');self.assertEqual(plan,dict(A=None,B=0))
        with self.assertRaises(AssertionError):h.force_initial_plan(dict(A=15,B=30),pool,lambda p:False,lambda p:0)

    def test_generator_inputs_match_lists_and_no_new_law_parameters(self):
        args=own_inputs(3);value=h.decide(h.NAME,**args)
        gen=dict(args,**{k:iter(args[k]) for k in ('candidates','observations','pending','patient_pending')})
        self.assertEqual(value,h.decide(h.NAME,**gen))
        cfg=h.spec()['config'];self.assertEqual(cfg['max_concurrent'],4);self.assertEqual(cfg['plan_passes'],2)
        self.assertEqual(cfg['lambda_seconds'],15);self.assertEqual(cfg['max_candidates_evaluated'],6)
        self.assertEqual(cfg['plan_offsets_seconds'],[0,15,30,60,90,120,180])

    def test_partial_H_and_A_still_bound_future_starts(self):
        args=own_inputs(3,now=1293,time_context=context(now=1293))
        value=h.decide(h.NAME,**args)
        self.assertEqual(value['evidence']['time_horizon']['cell_edges_seconds'],[5,7])
        self.assertTrue(all(r['planned_offset_seconds'] in (None,0) for r in value['evidence']['candidate_plan']))


if __name__=='__main__':unittest.main()
