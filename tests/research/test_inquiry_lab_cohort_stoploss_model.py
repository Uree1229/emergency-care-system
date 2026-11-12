"""Synthetic stoploss math and unchanged frozen-plan recurrence boundaries.

Only public mathematical fixtures, direct quadrature and frozen source reads;
no outcome, scenario generator, performance input or simulator is used.
"""
import ast
from copy import deepcopy
import inspect
import math
import unittest
from unittest.mock import patch
from inquiry_lab import cohort_stoploss_model as model,cohort_advance_model as advance
from test_inquiry_lab_cohort_advance_model import initial


def queue_row(q0,*,age=20.,r=.79,mu=math.log(10),sigma=1.,empty=False):
    mR=0. if empty else model.residual.head_expected_remaining(mu,sigma,r,age)
    return dict(hospital='X',estimated_wait_seconds=q0,service_mu=mu,service_sigma=sigma,
        reply_probability=r,accept_probability=.4,mean_service_seconds=math.exp(mu+sigma*sigma/2),
        assumed_workers=1,travel_seconds=20,inquiry_cost_seconds=15.,
        head_expected_remaining_seconds=mR,age_seconds=None if empty else age,
        head=None if empty else dict(hospital='X',patient='HEAD',submitted_at=900),
        queue_count=0 if empty else 1,queued_behind_count=0,
        prior_terminal_reply=None,estimated_start_at=None if empty else 980)


def first(pid,plans,*,items=(),horizon=100,age=20.,r=.79):
    records=[];contacts=[]
    for h,s,q in plans:
        row=queue_row(q,age=age,r=r,empty=q==0);row['hospital']=h
        records.append(row);contacts.append(dict(hospital=h,offset_seconds=s))
    return dict(patient_id=pid,effective_priority=4,
        source_owner=dict(inputs=dict(now=1000),evaluation_end_at=1000+horizon),
        hypothetical_origin=dict(cell_edges_seconds=model.H.cell_edges(horizon,5),own_items=deepcopy(list(items))),
        continuation=dict(queue_records=records,plan=contacts),initial_contact_cost_seconds=15.*len(items))


def independent_stoploss(mu,sigma,t):
    if t==0:return math.exp(mu+sigma*sigma/2)
    z=(math.log(t)-mu)/sigma
    return math.exp(mu+sigma*sigma/2)*.5*math.erfc((z-sigma)/math.sqrt(2))-t*.5*math.erfc(z/math.sqrt(2))


def normalized_function(function):
    tree=ast.parse(inspect.getsource(function)).body[0]
    if isinstance(tree.body[0],ast.Expr) and isinstance(tree.body[0].value,ast.Constant):tree.body.pop(0)
    return tree


class ShiftedThinnedStoplossTests(unittest.TestCase):
    def test_decomposition_preserves_parent_mean_in_every_branch(self):
        parent=queue_row(1);mR=parent['head_expected_remaining_seconds']
        for q,branch,F,p in ((0.,'thinned_head',0.,0.),(mR/3,'thinned_head',0.,1/3),
                (mR,'shifted_full_head',0.,1.),(mR+40,'shifted_full_head',40.,1.)):
            row=dict(parent,estimated_wait_seconds=q);d=model.base_stoploss_decomposition(row)
            self.assertEqual(d['decomposition_branch'],branch)
            self.assertAlmostEqual(d['deterministic_follower_seconds'],F,13)
            self.assertAlmostEqual(d['head_inclusion_probability'],p,15)
            self.assertAlmostEqual(d['reconstructed_mean_seconds'],q,13)
            self.assertEqual(model.base_stoploss_at_elapsed(d,0)['remaining_seconds'],q)

    def test_zero_head_is_deterministic_without_epsilon(self):
        for q in (0.,3.,1e-200):
            row=dict(queue_row(q,age=1e100,r=0.),head_expected_remaining_seconds=0.)
            d=model.base_stoploss_decomposition(row)
            self.assertEqual(d['deterministic_follower_seconds'],q)
            self.assertEqual(d['head_inclusion_probability'],0.)
            for s in (0.,q/2,q,q+1):
                self.assertEqual(model.base_stoploss_at_elapsed(d,s)['remaining_seconds'],max(q-s,0.))

    def test_materially_inconsistent_head_mean_rejected_before_any_clamp(self):
        row=queue_row(40.);mR=row['head_expected_remaining_seconds']
        for invalid in (0.,mR/2,2*mR,mR+1e-8):
            with self.assertRaisesRegex(ValueError,'parent head mean'):
                model.base_stoploss_decomposition(dict(row,head_expected_remaining_seconds=invalid))
        # The declared tolerance is tied to representable spacing, including
        # subnormal values, without a fixed seconds or relative epsilon.
        perturbed=math.nextafter(mR,math.inf)
        d=model.base_stoploss_decomposition(dict(row,head_expected_remaining_seconds=perturbed))
        self.assertEqual(d['recomputed_current_head_mean_seconds'],mR)
        self.assertEqual(d['head_mean_numeric_tolerance_seconds'],8*max(math.ulp(mR),math.ulp(perturbed)))

    def test_material_bound_violation_cannot_be_silently_clamped(self):
        d=model.base_stoploss_decomposition(queue_row(40.))
        for invalid in (d['head_expected_remaining_seconds']+100.,d['head_expected_remaining_seconds']/2):
            broken=dict(d,head_expected_remaining_seconds=invalid)
            with self.assertRaisesRegex(ArithmeticError,'bound correction'):
                model.base_stoploss_at_elapsed(broken,1.)

    def test_only_ulp_sized_bound_correction_is_permitted_and_recorded(self):
        d=model.base_stoploss_decomposition(queue_row(40.))
        lower=39.;target=lower-4*math.ulp(lower)
        adjusted=target-(d['deterministic_follower_seconds']-1.)
        result=model.base_stoploss_at_elapsed(dict(d,head_expected_remaining_seconds=adjusted),1.)
        self.assertGreater(result['numeric_bound_correction_seconds'],0.)
        self.assertEqual(result['remaining_seconds'],lower)
        self.assertEqual(result['numeric_bound_correction_seconds'],lower-result['raw_remaining_seconds'])
        self.assertEqual(result['numeric_bound_tolerance_seconds'],32*max(math.ulp(d['q0_seconds']),
            math.ulp(result['raw_remaining_seconds']),math.ulp(lower)))
        self.assertLessEqual(abs(result['numeric_bound_correction_seconds']),result['numeric_bound_tolerance_seconds'])

    def test_thinning_underflow_cannot_silently_destroy_parent_mean(self):
        row=queue_row(1e-300,age=1e100,r=1.)
        with self.assertRaisesRegex(ArithmeticError,'mean preservation'):
            model.base_stoploss_decomposition(row)

    def test_high_age_tail_correction_exposes_log_subtraction_sensitivity(self):
        row=queue_row(1.,age=1e9,r=1.);row['estimated_wait_seconds']=row['head_expected_remaining_seconds']
        d=model.base_stoploss_decomposition(row);result=model.base_stoploss_at_elapsed(d,15.)
        log_error=result['log_stoploss_numeric_error_bound'];raw=result['raw_remaining_seconds']
        scalar_allowance=32*max(math.ulp(d['q0_seconds']),math.ulp(raw),math.ulp(result['baseline_positive_part_seconds']))
        tail_allowance=abs(raw)*math.expm1(log_error+32*math.ulp(d['current_log_no_reply_probability']))
        self.assertEqual(result['numeric_bound_tolerance_seconds'],max(scalar_allowance,tail_allowance))
        self.assertGreater(log_error,0.)
        self.assertLessEqual(abs(result['numeric_bound_correction_seconds']),result['numeric_bound_tolerance_seconds'])
        self.assertLess(result['numeric_bound_tolerance_seconds'],1e-8*d['q0_seconds'])
        self.assertGreaterEqual(result['remaining_seconds'],max(d['q0_seconds']-15.,0.))

    def test_nonfinite_or_unresolved_numerical_allowance_raises(self):
        d=model.base_stoploss_decomposition(queue_row(1.));logJ=model._log_stoploss(d['service_mu'],d['service_sigma'],35.)
        for invalid in (math.inf,math.nan,-1.,1e308):
            with patch.object(model,'_log_stoploss_arithmetic',return_value=(logJ,invalid)):
                with self.assertRaises(ArithmeticError):model.base_stoploss_at_elapsed(d,15.)
        # Degenerate log operands must not become a zero or infinite clamp.
        with patch.object(model.residual,'_log_normal_survival',return_value=-math.inf):
            with self.assertRaises(ArithmeticError):model._log_stoploss_arithmetic(0.,1.,20.)

    def test_empty_queue_is_exact_zero_and_retains_no_invented_head(self):
        d=model.base_stoploss_decomposition(queue_row(0,empty=True))
        for s in (0.,15.,300.,1e100):
            result=model.base_stoploss_at_elapsed(d,s)
            self.assertEqual(result['remaining_seconds'],0.)
            self.assertIsNone(result['log_stoploss']);self.assertIsNone(result['fixed_log_no_reply_probability'])
            self.assertIsNone(result['stoploss_age_seconds'])
        self.assertEqual(d['head_inclusion_probability'],0.)

    def test_before_follower_shift_is_exact_linear_drain(self):
        row=queue_row(1);mR=row['head_expected_remaining_seconds'];row['estimated_wait_seconds']=mR+40
        d=model.base_stoploss_decomposition(row)
        for s in (1.,15.,39.):
            result=model.base_stoploss_at_elapsed(d,s)
            self.assertEqual(result['branch'],'before_shift')
            self.assertAlmostEqual(result['remaining_seconds'],row['estimated_wait_seconds']-s,13)
            self.assertIsNone(result['log_stoploss'])

    def test_fixed_current_denominator_differs_from_unobserved_future_silence(self):
        row=queue_row(1,age=20.,r=1.);row['estimated_wait_seconds']=row['head_expected_remaining_seconds']
        d=model.base_stoploss_decomposition(row);s=60.;actual=model.base_stoploss_at_elapsed(d,s)
        z=(math.log(row['age_seconds'])-row['service_mu'])/row['service_sigma']
        denominator=.5*math.erfc(z/math.sqrt(2))
        expected=independent_stoploss(row['service_mu'],row['service_sigma'],80.)/denominator
        self.assertAlmostEqual(actual['remaining_seconds'],expected,12)
        future_reconditioned=model.residual.head_expected_remaining(row['service_mu'],row['service_sigma'],1.,80.)
        self.assertLess(actual['remaining_seconds'],future_reconditioned)
        self.assertEqual(actual['fixed_log_no_reply_probability'],d['current_log_no_reply_probability'])

    def test_shifted_and_thinned_laws_match_direct_analytic_formula(self):
        for r in (0.,.79,1-1e-12,1.):
            row=queue_row(1,r=r);mR=row['head_expected_remaining_seconds']
            for q in (mR/4,mR,mR+40):
                d=model.base_stoploss_decomposition(dict(row,estimated_wait_seconds=q))
                F=d['deterministic_follower_seconds'];p=d['head_inclusion_probability']
                denominator=(1-r)+r*.5*math.erfc((math.log(row['age_seconds'])-row['service_mu'])/row['service_sigma']/math.sqrt(2))
                for s in (0.,1.,15.,40.,120.):
                    wanted=F-s+p*mR if s<F else p*independent_stoploss(row['service_mu'],row['service_sigma'],row['age_seconds']+s-F)/denominator
                    self.assertAlmostEqual(model.base_stoploss_at_elapsed(d,s)['remaining_seconds'],wanted,11)

    def test_independent_normal_density_quadrature_verifies_declared_law(self):
        row=queue_row(1,age=20.,r=.79);mR=row['head_expected_remaining_seconds']
        for q,s in ((mR/3,20.),(mR+40,60.)):
            d=model.base_stoploss_decomposition(dict(row,estimated_wait_seconds=q))
            F=d['deterministic_follower_seconds'];p=d['head_inclusion_probability'];t=20+s-F
            low=(math.log(t)-row['service_mu'])/row['service_sigma'];high=12.;n=8192;step=(high-low)/n
            def density(z):return max(0.,math.exp(row['service_mu']+row['service_sigma']*z)-t)*math.exp(-z*z/2)/math.sqrt(2*math.pi)
            integral=(density(low)+density(high)+sum((4 if i%2 else 2)*density(low+i*step) for i in range(1,n)))*step/3
            denominator=(1-row['reply_probability'])+row['reply_probability']*.5*math.erfc((math.log(20)-row['service_mu'])/math.sqrt(2))
            self.assertAlmostEqual(model.base_stoploss_at_elapsed(d,s)['remaining_seconds'],p*integral/denominator,9)

    def test_nonnegative_monotone_convex_bounded_and_above_jensen_baseline(self):
        for r in (0.,.79,1.):
            row=queue_row(1,r=r);mR=row['head_expected_remaining_seconds']
            for q in (mR/4,mR,mR+40):
                d=model.base_stoploss_decomposition(dict(row,estimated_wait_seconds=q))
                values=[model.base_stoploss_at_elapsed(d,s)['remaining_seconds'] for s in range(0,201)]
                self.assertEqual(values[0],q)
                for s,value in enumerate(values):
                    self.assertGreaterEqual(value,max(q-s,0.));self.assertLessEqual(value,q)
                self.assertTrue(all(b<=a+1e-12 for a,b in zip(values,values[1:])))
                slopes=[b-a for a,b in zip(values,values[1:])]
                self.assertTrue(all(b>=a-1e-11 for a,b in zip(slopes,slopes[1:])))
                self.assertGreater(values[100],max(q-100,0.))

    def test_far_tail_high_age_and_r_zero_one_remain_finite(self):
        for age in (0.,1e9,1e100):
            for r in (0.,.79,1-1e-12,1.):
                row=queue_row(1,age=age,r=r);mR=row['head_expected_remaining_seconds']
                for q in (mR/2,mR,mR+20):
                    d=model.base_stoploss_decomposition(dict(row,estimated_wait_seconds=q))
                    for s in (0.,15.,300.,age):
                        value=model.base_stoploss_at_elapsed(d,s)
                        self.assertTrue(all(not isinstance(x,float) or math.isfinite(x) for x in value.values()))
                        self.assertGreaterEqual(value['remaining_seconds'],max(q-s,0.))
                        self.assertLessEqual(value['remaining_seconds'],q)

    def test_invalid_head_law_and_clock_values_rejected(self):
        row=queue_row(10.)
        for field,value in (('service_sigma',0.),('reply_probability',1.1),('age_seconds',-1),
                ('head_expected_remaining_seconds',-1),('estimated_wait_seconds',math.inf),('queue_count',True),('head',None)):
            with self.assertRaises(ValueError):model.base_stoploss_decomposition(dict(row,**{field:value}))
        for change in (dict(estimated_wait_seconds=1.),dict(age_seconds=0.),dict(head={}),dict(head_expected_remaining_seconds=1.)):
            with self.assertRaises(ValueError):model.base_stoploss_decomposition(dict(queue_row(0.,empty=True),**change))
        d=model.base_stoploss_decomposition(row)
        for s in (-1,True,math.inf,math.nan):
            with self.assertRaises(ValueError):model.base_stoploss_at_elapsed(d,s)


class FrozenPlanRecurrenceTests(unittest.TestCase):
    def test_stoploss_F0_built_once_before_forward_and_reused(self):
        ps=[first('A',[('X',15,30.),('Y',30,0.)]),first('B',[('X',15,30.)])]
        before=deepcopy(ps)
        with patch.object(model,'base_stoploss_at_elapsed',wraps=model.base_stoploss_at_elapsed) as call:
            jobs=model.score_first_cohort(ps)['jobs']
        self.assertEqual(call.call_count,3);self.assertEqual(ps,before)
        for j in jobs:
            self.assertEqual(j['fixed_F0_wait_seconds'],j['base_stoploss_at_send']['remaining_seconds'])
            self.assertEqual(j['base_remaining_seconds'],j['fixed_F0_wait_seconds'])
            self.assertEqual(j['source_queue_record'],ps[j['first_patient_index']]['continuation']['queue_records'][j['parent_queue_record_index']])

    def test_current_mean_and_initial_cdf_memberships_costs_preserved_at_zero(self):
        ps=[first('A',[('X',0,30.),('Y',0,20.)],items=[initial()]),first('B',[('X',0,30.)])]
        new=model.score_first_cohort(ps);old=advance.score_first_cohort(ps)
        self.assertEqual(new['patient_scores'],old['patient_scores'])
        for a,b in zip(new['jobs'],old['jobs']):
            self.assertEqual({k:v for k,v in a.items() if k not in ('base_stoploss_decomposition','base_stoploss_at_send')},b)

    def test_shared_future_work_keeps_chronology_maximum_probe_and_recurrence(self):
        ps=[first('A',[('X',0,30.),('Y',15,10.)]),first('B',[('X',15,30.)])]
        jobs=model.score_first_cohort(ps)['jobs'];completion={}
        self.assertEqual([(j['send_offset_seconds'],j['first_patient_index'],j['plan_candidate_index']) for j in jobs],[(0,0,0),(15,0,1),(15,1,0)])
        for j in jobs:
            s=j['send_offset_seconds'];prior=completion.get(j['hospital'],0.)
            self.assertEqual(j['corrected_wait_seconds'],max(j['base_remaining_seconds'],prior-s,0.))
            self.assertEqual(j['probe_offset_seconds'],min(s+j['corrected_wait_seconds'],j['owner_horizon_seconds']))
            self.assertEqual(j['future_completion_after_seconds'],s+j['corrected_wait_seconds']+j['full_mean_work_seconds']*j['no_prior_accept_probability'])
            completion[j['hospital']]=j['future_completion_after_seconds']

    def test_future_cross_hospital_survival_uses_only_fixed_F0_not_F1(self):
        ps=[first('O',[('X',0,30.)]),first('A',[('X',15,30.),('Y',15,30.)])]
        jobs=model.score_first_cohort(ps)['jobs'];ax=next(j for j in jobs if j['patient_id']=='A' and j['hospital']=='X')
        ay=next(j for j in jobs if j['patient_id']=='A' and j['hospital']=='Y')
        factor=ay['fixed_F0_survival_factors'][0]
        self.assertEqual(factor['selection_probability_at_probe'],model._future_cdf(ax,ay['probe_offset_seconds'],'fixed_F0_wait_seconds'))
        self.assertNotEqual(ax['corrected_wait_seconds'],ax['fixed_F0_wait_seconds'])

    def test_ast_unchanged_cdf_score_and_mean_future_recurrence(self):
        for name in ('_key','_future_cdf','score_first_cohort'):
            self.assertEqual(ast.dump(normalized_function(getattr(model,name)),include_attributes=False),
                ast.dump(normalized_function(getattr(advance,name)),include_attributes=False))
        changed=normalized_function(model._forward);expected=normalized_function(advance._forward)
        for index,node in enumerate(changed.body):
            if isinstance(node,ast.For):
                for ni,line in enumerate(node.body):
                    if isinstance(line,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='base_remaining' for t in line.targets):
                        node.body[ni]=deepcopy(next(n for n in expected.body[index].body if isinstance(n,ast.Assign)
                            and any(isinstance(t,ast.Name) and t.id=='base_remaining' for t in n.targets)))
        self.assertEqual(ast.dump(changed,include_attributes=False),ast.dump(expected,include_attributes=False))

    def test_invalid_future_membership_and_duplicate_patients_rejected(self):
        with self.assertRaises(ValueError):model.score_first_cohort([first('A',[('X',100,0.)])])
        with self.assertRaises(ValueError):model.score_first_cohort([first('A',[]),first('A',[])])


if __name__=='__main__':unittest.main()
