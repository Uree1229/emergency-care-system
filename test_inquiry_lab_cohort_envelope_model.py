"""Declared-law envelope identities and immutable score reconstruction."""
import ast
from copy import deepcopy
import inspect
import math
import unittest
from unittest.mock import patch
from inquiry_lab import cohort_envelope_model as model
from test_inquiry_lab_cohort_stoploss_model import queue_row,first,independent_stoploss


class ConditionalEnvelopeTests(unittest.TestCase):
    def test_random_straddle_matches_independent_lognormal_maximum_identity(self):
        mu=math.log(100.);sigma=.5;row=queue_row(1.,age=0.,r=1.,mu=mu,sigma=sigma)
        row['estimated_wait_seconds']=row['head_expected_remaining_seconds']/2
        d=model.stoploss.base_stoploss_decomposition(row);before=deepcopy(d)
        result=model.envelope_wait(d,10.,80.)
        expected=70.+.5*independent_stoploss(mu,sigma,80.)
        self.assertAlmostEqual(result['envelope_wait_seconds'],expected,11)
        self.assertGreater(result['envelope_minus_old_max_seconds'],0.)
        self.assertEqual(d,before);self.assertFalse(result['numeric_clamp_applied'])

    def test_prior_finished_before_send_is_exact_parent_stoploss(self):
        d=model.stoploss.base_stoploss_decomposition(queue_row(40.))
        for send,prior in ((0.,0.),(10.,0.),(10.,10.),(10.,9.999)):
            result=model.envelope_wait(d,send,prior)
            self.assertEqual(result['envelope_wait_seconds'],model.stoploss.base_stoploss_at_elapsed(d,send)['remaining_seconds'])
            self.assertEqual(result['barrier_elapsed_seconds'],send)

    def test_deterministic_baseline_equals_deterministic_maximum(self):
        d=model.stoploss.base_stoploss_decomposition(queue_row(40.,age=1e100,r=0.))
        for send in (0.,20.,40.,100.):
            for prior in (0.,10.,40.,80.,1e9):
                result=model.envelope_wait(d,send,prior)
                self.assertEqual(result['envelope_wait_seconds'],max(max(40.,prior)-send,0.))
                self.assertEqual(result['envelope_minus_old_max_seconds'],0.)

    def test_same_T_bounds_across_shift_thin_tails_and_large_priors(self):
        for age,r in ((0.,1.),(20.,.79),(1e9,1.)):
            base=queue_row(1.,age=age,r=r);m=base['head_expected_remaining_seconds']
            for q in (m/3,m,m+40):
                d=model.stoploss.base_stoploss_decomposition(dict(base,estimated_wait_seconds=q))
                for send,prior in ((0.,10.),(15.,30.),(100.,500.),(1e6,1e6+50)):
                    result=model.envelope_wait(d,send,prior);value=result['envelope_wait_seconds'];tol=result['numeric_bound_tolerance_seconds']
                    self.assertGreaterEqual(value,result['local_lower_bound_seconds']-tol)
                    self.assertLessEqual(value,result['local_upper_bound_seconds']+tol)
                    self.assertTrue(result['local_bounds_within_allowance'])

    def test_send_and_prior_reject_bool_negative_or_nonfinite(self):
        d=model.stoploss.base_stoploss_decomposition(queue_row(40.))
        for invalid in (True,-1.,math.inf,math.nan,None):
            with self.assertRaises(ValueError):model.envelope_wait(d,invalid,0.)
            with self.assertRaises(ValueError):model.envelope_wait(d,0.,invalid)

    def test_current_no_reply_denominator_not_future_conditioned(self):
        d=model.stoploss.base_stoploss_decomposition(queue_row(40.,age=20.,r=.79))
        result=model.envelope_wait(d,15.,80.)
        self.assertEqual(result['baseline_at_send']['fixed_log_no_reply_probability'],d['current_log_no_reply_probability'])
        self.assertEqual(result['baseline_at_barrier']['fixed_log_no_reply_probability'],d['current_log_no_reply_probability'])
        self.assertEqual(result['barrier_elapsed_seconds'],80.)

    def test_frozen_plan_membership_F0_and_chronological_prior_are_preserved(self):
        plans=[first('A',[('X',15,40),('Y',20,20)]),first('B',[('X',25,40)])]
        before=deepcopy(plans);jobs=model.stoploss._jobs(plans);new=model._forward(jobs,plans);completion={}
        self.assertEqual(plans,before);self.assertEqual(jobs,model.stoploss._jobs(plans))
        for original,job in zip(jobs,new):
            for key,value in original.items():self.assertEqual(job[key],value)
            prior=completion.get(job['hospital'],0.)
            self.assertEqual(job['prior_future_completion_seconds'],prior)
            expected=model.envelope_wait(job['base_stoploss_decomposition'],job['send_offset_seconds'],prior)
            self.assertEqual(job['queue_envelope'],expected)
            self.assertEqual(job['corrected_wait_seconds'],expected['envelope_wait_seconds'])
            completion[job['hospital']]=job['future_completion_after_seconds']
        self.assertEqual(new[2]['prior_future_completion_seconds'],new[0]['future_completion_after_seconds'])

    def test_no_future_contacts_preserves_parent_metrics_and_initial_items(self):
        plans=[first('A',[]),first('B',[])];new=model.score_first_cohort(plans);old=model.stoploss.score_first_cohort(plans)
        self.assertEqual(new['jobs'],[]);self.assertEqual(new['patient_scores'],old['patient_scores'])
        with self.assertRaises(ValueError):model.score_first_cohort([plans[0],plans[0]])

    def test_inherited_row_uses_literal_frozen_plans_and_copies_residual_metrics(self):
        from test_inquiry_lab_cohort_fullmenu_model import source_fixture
        *_,proposal=source_fixture(4,1);row=proposal['evidence']['row_scores'][0];before=deepcopy(row)
        with patch.object(model.recourse,'score_first_cohort',side_effect=AssertionError('no initial rebuild')),patch.object(model.followplan,'score_first_patient',side_effect=AssertionError('no plan rebuild')):
            new=model.rescore_row(row,None)
        self.assertEqual(row,before);plans=row['base_row_score']['first_cohort_future']
        self.assertEqual(new['first_plan_source_sha256'],model._sha(plans))
        ids={model.stoploss._key(p['patient_id']) for p in plans}
        residual=[m for m in row['member_metrics'] if model.stoploss._key(m['patient_id']) not in ids]
        self.assertEqual(new['unchanged_residual_member_metrics'],residual)
        self.assertEqual(new['first_cohort_envelope'],model.score_first_cohort(plans))

    def test_expanded_row_reuses_D_and_reconstructs_first_plans_only(self):
        from test_inquiry_lab_cohort_fullmenu_policy import fixture,invoke
        args=fixture();full=invoke(args)['evidence'];stop=args[-1]
        # Fullmenu fixture labels -> immutable Stoploss calculation labels.
        from inquiry_lab import cohort_fullmenu_context as context
        prepared=model.fullmenu.prepare(stop,members=context.base_members_projection(args[1]),tail_members=context.base_members_projection(args[2]),joint_context=context.base_context_projection(args[0]),priority_context=args[3])
        old=next(r for r in full['row_scores'] if r.get('row_kind')=='expanded');before=deepcopy(old);called=[];score=model.recourse.score_owner
        def record(owner,*rest):called.append(owner['patient']['id']);return score(owner,*rest)
        with patch.object(model.fullmenu.dynamic,'_score',side_effect=AssertionError('D must not rerun')),patch.object(model.recourse,'score_owner',side_effect=record):
            new=model.rescore_row(old,prepared)
        self.assertEqual(old,before);self.assertEqual(new['first_plan_source_sha256'],model._sha(old['dynamic_row_projection']))
        self.assertEqual(set(called),{m['patient_id'] for m in args[1]+args[2]})
        self.assertNotIn('OWNER0',called)
        self.assertEqual([m['patient_id'] for m in new['member_metrics']],[m['patient_id'] for m in old['member_metrics']])

    def test_unknown_row_and_changed_first_partition_are_rejected(self):
        with self.assertRaises(ValueError):model.rescore_row(dict(row_index=1,member_metrics=[]),None)
        from test_inquiry_lab_cohort_fullmenu_model import source_fixture
        *_,proposal=source_fixture(4,1);row=deepcopy(proposal['evidence']['row_scores'][0]);row['member_metrics'].pop(0)
        with self.assertRaises(ValueError):model.rescore_row(row,None)

    def test_no_generator_or_outcome_input_in_new_model(self):
        source=ast.parse(inspect.getsource(model));imports=[a.name for n in ast.walk(source) if isinstance(n,(ast.Import,ast.ImportFrom)) for a in n.names]
        self.assertFalse(any(any(token in name for token in ('experiment','scenario','runner','oracle','audit')) for name in imports))
        self.assertEqual(set(inspect.signature(model.envelope_wait).parameters),{'decomposition','send_seconds','prior_completion_seconds'})


if __name__=='__main__':unittest.main()
