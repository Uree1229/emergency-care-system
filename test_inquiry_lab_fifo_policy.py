"""Visible-FIFO queue mathematics and the isolated new-inquiry wait factor.

Only synthetic unit rows, complete small path enumeration and numerical
quadrature are used. No performance environment, DEV/fresh/heldout seed or
actual worker/service/potential is executed or supplied to a policy.
"""
import ast
from copy import deepcopy
import hashlib
import inspect
import math
from pathlib import Path
import unittest
from unittest.mock import patch

from inquiry_lab import residual_policy as old, fifo_policy as new


class FixedBeliefs:
    def __init__(self,r=.79,mu=math.log(66),sigma=.7):
        self.r,self.value,self.sigma=r,mu,sigma
    def mu(self,h):return self.value
    def reply(self,h):return self.r
    def mean_service(self,h):return math.exp(self.value+self.sigma*self.sigma/2)


def row(patient,submitted,hospital='H01'):
    return dict(hospital=hospital,patient=patient,submitted_at=submitted)


def queue(rows=(),history=(),now=1000,r=.79,workers=1,mu=math.log(66),sigma=.7):
    rows=old.inquiry_policies._pending(rows,now)
    rows=[r for r in rows if r['hospital']=='H01']
    history=old.inquiry_policies._observations(history,now,1800)
    beliefs=FixedBeliefs(r,mu,sigma)
    return new._queue_wait_estimate('H01',now,rows,history,beliefs,workers)


def cdf(x,mu,sigma):
    return .5*math.erfc(-(math.log(x)-mu)/(sigma*math.sqrt(2))) if x>0 else 0.


def tail_stoploss(age,mu,sigma):
    mean=math.exp(mu+sigma*sigma/2)
    if age==0:return 1.,mean
    z=(math.log(age)-mu)/sigma
    tail=.5*math.erfc(z/math.sqrt(2))
    stop=mean*.5*math.erfc((z-sigma)/math.sqrt(2))-age*tail
    return tail,stop


def enumerate_small_paths(mu,sigma,r,submitted,age,step):
    """Enumerate every rounded completion path; do not merge prefix states."""
    ends=[min(age,k*step) for k in range(math.ceil(age/step)+1)]
    mean=math.exp(mu+sigma*sigma/2);terms=[];complete=[]
    def visit(position,completion,path_probability):
        if position==len(submitted):
            complete.append(path_probability*(1-r)**position)
            return
        start=max(completion,submitted[position])
        tail,stop=tail_stoploss(age-start,mu,sigma)
        weight=path_probability*(1-r)**position
        terms.append((position,weight*tail,weight*(stop+(len(submitted)-position-1)*mean*tail)))
        previous=start
        for end in ends:
            if end<=start:continue
            probability=cdf(end-start,mu,sigma)-cdf(previous-start,mu,sigma)
            visit(position+1,end,path_probability*probability)
            previous=end
    visit(0,0.,1.)
    den=sum(x[1] for x in terms)+sum(complete)
    num=sum(x[2] for x in terms)
    return num/den,den,num,terms,complete


def continuous_two_job(mu,sigma,r,age,panels=4096):
    """Independent continuous simultaneous-two-job integral, no time grid."""
    def integrate(function):
        width=age/panels
        value=function(0)+function(age)
        value+=sum((4 if i%2 else 2)*function(i*width) for i in range(1,panels))
        return value*width/3
    def density(x):
        if x<=0:return 0.
        return math.exp(-((math.log(x)-mu)/sigma)**2/2)/(x*sigma*math.sqrt(2*math.pi))
    tail,stop=tail_stoploss(age,mu,sigma)
    mean=math.exp(mu+sigma*sigma/2);q=1-r
    active_second=integrate(lambda x:density(x)*tail_stoploss(age-x,mu,sigma)[0])
    remaining_second=integrate(lambda x:density(x)*tail_stoploss(age-x,mu,sigma)[1])
    completed=integrate(lambda x:density(x)*cdf(age-x,mu,sigma))
    den=tail+q*active_second+q*q*completed
    num=stop+mean*tail+q*remaining_second
    return num/den


def inputs(n=8,**kwargs):
    result=dict(now=1000,candidates=[dict(hospital_id=f'H{i:02}',
        travel=dict(duration_seconds=300+10*i),resources_enough=True) for i in range(1,n+1)],
        observations=[],pending=[],patient_pending=[],max_new=10,last_contact_at=None)
    result.update(kwargs)
    return result


def core(value):
    result=deepcopy(value)
    result['evidence'].pop('fifo_queue')
    result['evidence']['policy']=old.NAME
    return result


def old_core(value):
    result=deepcopy(value);result['evidence'].pop('residual_queue')
    return result


class FIFOMathTests(unittest.TestCase):
    def test_small_three_job_grid_matches_complete_unmerged_path_enumeration(self):
        mu,sigma=math.log(6),.6
        for r in (0.,.35,.79,.999):
            for submitted in ((0,0,0),(0,3,9)):
                result=new._prefix_workload(mu,sigma,r,0,submitted,12)
                value,den,num,terms,completed=enumerate_small_paths(mu,sigma,r,submitted,12,5)
                self.assertTrue(math.isclose(result['wait_seconds'],value,rel_tol=1e-12,abs_tol=1e-12))
                self.assertTrue(math.isclose(result['silence_probability'],den,rel_tol=1e-12,abs_tol=1e-12))
                self.assertTrue(math.isclose(result['remaining_numerator'],num,rel_tol=1e-12,abs_tol=1e-12))
                for layer in result['prefix_layers']:
                    position=layer['position']
                    self.assertAlmostEqual(layer['active_denominator'],sum(t[1] for t in terms if t[0]==position),places=12)
                    self.assertAlmostEqual(layer['active_numerator'],sum(t[2] for t in terms if t[0]==position),places=12)
                self.assertAlmostEqual(result['prefix_layers'][-1]['completed_weight'],sum(completed),places=12)

    def test_partial_final_cell_is_now_and_no_completion_mass_disappears(self):
        result=new._prefix_workload(math.log(6),.6,0.,0,(0,0),12)
        self.assertEqual(result['grid_offsets_seconds'],[0,5,10,12])
        layers=result['prefix_layers']
        total=sum(layer['active_denominator'] for layer in layers)+layers[-1]['completed_weight']
        self.assertAlmostEqual(total,1.,places=13)
        self.assertEqual(result['silence_probability'],1.)
        self.assertGreater(layers[-1]['completed_weight'],0.)

    def test_all_completed_mass_is_denominator_only(self):
        result=new._prefix_workload(math.log(6),.6,.35,0,(0,0),12)
        layers=result['prefix_layers']
        self.assertGreater(layers[-1]['completed_weight'],0.)
        self.assertAlmostEqual(result['remaining_numerator'],sum(l['active_numerator'] for l in layers),places=12)
        self.assertAlmostEqual(result['silence_probability'],sum(l['active_denominator'] for l in layers)
            +layers[-1]['completed_weight'],places=12)

    def test_analytic_unfinished_tail_and_stoploss_survive_the_last_grid_edge(self):
        mu,sigma=math.log(66),.7
        result=new._prefix_workload(mu,sigma,.79,0,(0,0),12)
        tail,stop=tail_stoploss(12,mu,sigma);mean=math.exp(mu+sigma*sigma/2)
        first=result['prefix_layers'][0]
        self.assertAlmostEqual(first['active_denominator'],tail,places=14)
        self.assertAlmostEqual(first['active_numerator'],stop+mean*tail,places=12)
        self.assertGreater(tail,.99)
        self.assertGreater(stop,60.)

    def test_grid_error_converges_to_independent_continuous_two_job_integral(self):
        mu,sigma,r,age=math.log(6),.6,.35,12
        exact=continuous_two_job(mu,sigma,r,age)
        errors=[]
        for step in (5,2,1):
            value=new._prefix_workload(mu,sigma,r,0,(0,0),age,step,600)['wait_seconds']
            errors.append(abs(value-exact))
        self.assertTrue(errors[0]>errors[1]>errors[2])
        self.assertLess(errors[-1],.35)

    def test_staggered_arrivals_keep_their_release_times(self):
        mu,sigma=math.log(6),.6
        simultaneous=new._prefix_workload(mu,sigma,0.,0,(0,0),12)
        staggered=new._prefix_workload(mu,sigma,0.,0,(0,10),12)
        expected=enumerate_small_paths(mu,sigma,0.,(0,10),12,5)[0]
        self.assertAlmostEqual(staggered['wait_seconds'],expected,places=12)
        self.assertGreater(staggered['wait_seconds'],simultaneous['wait_seconds'])

    def test_age_zero_math_is_exact_total_mean(self):
        mu,sigma=math.log(66),.7
        for n in (1,2,10):
            for r in (0.,.79,1.):
                result=new._prefix_workload(mu,sigma,r,1000,[1000]*n,1000)
                self.assertEqual(result['wait_seconds'],n*math.exp(mu+sigma*sigma/2))
                self.assertEqual(result['silence_probability'],1.)

    def test_single_job_dp_is_original_analytic_formula(self):
        mu,sigma=math.log(66),.7
        for age in (12,100,300):
            for r in (0.,.79,1.):
                value=new._prefix_workload(mu,sigma,r,0,[0],age)['wait_seconds']
                expected=old.head_expected_remaining(mu,sigma,r,age)
                self.assertTrue(math.isclose(value,expected,rel_tol=1e-12,abs_tol=1e-12))

    def test_cell_bound_is_derived_from_age_and_fixed_deadline_grid(self):
        for age in (1,12,300,301,1800):
            result=new._prefix_workload(math.log(66),.7,.79,0,(0,0),age)
            self.assertEqual(result['grid_step_seconds'],max(5,math.ceil(age/60)))
            self.assertLessEqual(len(result['grid_offsets_seconds'])-1,60)
            self.assertEqual(result['grid_offsets_seconds'][-1],age)

    def test_no_response_boundary_keeps_exact_unit_normalizer(self):
        result=new._prefix_workload(math.log(66),.7,0.,0,(0,20,70),180)
        self.assertEqual(result['silence_probability'],1.)
        self.assertGreaterEqual(result['wait_seconds'],0.)
        self.assertTrue(math.isfinite(result['wait_seconds']))

    def test_interval_probability_uses_stable_low_and_high_tails(self):
        mu,sigma=math.log(66),.7
        for lower,upper in ((0,5),(10,20),(66,100),(300,400),(1e8,2e8)):
            actual=new._service_interval(lower,upper,mu,sigma)
            if lower<300:expected=cdf(upper,mu,sigma)-cdf(lower,mu,sigma)
            else:
                zl=(math.log(lower)-mu)/sigma;zu=(math.log(upper)-mu)/sigma
                expected=.5*(math.erfc(zl/math.sqrt(2))-math.erfc(zu/math.sqrt(2)))
            self.assertTrue(math.isclose(actual,expected,rel_tol=1e-10,abs_tol=1e-15))
            self.assertGreater(actual,0.)

    def test_positive_underflow_is_explicit_instead_of_silently_dropping_a_path(self):
        with self.assertRaises(new._FIFONumericalError) as caught:new._positive_exp(-10000.)
        self.assertEqual(caught.exception.reason,'numerical_underflow')
        with self.assertRaises(new._FIFONumericalError) as caught:new._product(1e-300,1e-300)
        self.assertEqual(caught.exception.reason,'numerical_underflow')

    def test_distribution_grid_and_visible_submission_validation(self):
        good=dict(mu=math.log(66),sigma=.7,reply_probability=.79,head_start=0,submitted_at=(0,0),now=12)
        for change in (dict(mu=True),dict(mu=float('inf')),dict(sigma=0),dict(reply_probability=-.1),
            dict(reply_probability=1.1),dict(head_start=-1),dict(head_start=13),dict(now=True),
            dict(grid_seconds=0),dict(grid_seconds=True),dict(max_grid_cells=0),dict(max_grid_cells=False),
            dict(submitted_at=[]),dict(submitted_at=[0,13]),dict(submitted_at=[2,1]),
            dict(submitted_at=[1,2]),dict(submitted_at=[0,float('nan')])):
            with self.assertRaises(ValueError):new._prefix_workload(**dict(good,**change))


class FIFOQueueTests(unittest.TestCase):
    def test_empty_queue_uses_exact_original_zero_without_a_fictitious_head(self):
        value,record=queue()
        self.assertEqual(value,0.)
        self.assertIsNone(record['head']);self.assertEqual(record['fifo_order'],[])
        self.assertEqual(record['method'],'residual_analytic_exact')
        self.assertEqual(record['fallback_reason'],'empty_queue')
        self.assertIsNone(record['silence_probability'])

    def test_single_job_exact_boundary_never_runs_discretized_dp(self):
        for age in (0,12,180,600):
            for r in (0.,.79,1.):
                rows=[row('P',1000-age)]
                expected=old._queue_wait_estimate('H01',1000,rows,[],FixedBeliefs(r),1)[0]
                with patch.object(new,'_prefix_workload',side_effect=AssertionError('single-job DP executed')):
                    actual,record=queue(rows=rows,r=r)
                self.assertEqual(actual,expected)
                self.assertEqual(record['fallback_reason'],'single_job_exact')
                self.assertEqual(record['method'],'residual_analytic_exact')

    def test_fresh_backlog_and_fully_responsive_boundaries_are_exact_original(self):
        for rows,r,reason in (([row('A',1000),row('B',1000)],.79,'fresh_head_exact'),
                ([row('A',900),row('B',950)],1.,'fully_responsive_exact')):
            expected=old._queue_wait_estimate('H01',1000,rows,[],FixedBeliefs(r),1)[0]
            with patch.object(new,'_prefix_workload',side_effect=AssertionError('analytic boundary DP executed')):
                value,record=queue(rows=rows,r=r)
            self.assertEqual(value,expected);self.assertEqual(record['fallback_reason'],reason)

    def test_multiple_workers_fall_back_to_original_assumption_explicitly(self):
        rows=[row('A',900),row('B',950)]
        expected=old._queue_wait_estimate('H01',1000,rows,[],FixedBeliefs(),2)[0]
        value,record=queue(rows=rows,workers=2)
        self.assertEqual(value,expected)
        self.assertEqual(record['method'],'residual_analytic_fallback')
        self.assertEqual(record['fallback_reason'],'worker_assumption')
        self.assertEqual(record['conditional_work_seconds'],value*2)
        for workers in (0,-1,False,float('nan')):
            with self.assertRaises(ValueError):queue(rows=rows,workers=workers)

    def test_any_received_non_timeout_reply_on_a_visible_job_uses_analytic_fallback(self):
        rows=[row('A',900),row('B',950)]
        for outcome in ('reply','accept','decline'):
            history=[dict(hospital='H01',submitted_at=900,observed_at=980,outcome=outcome)]
            value,record=queue(rows=rows,history=history)
            expected=old._queue_wait_estimate('H01',1000,rows,history,FixedBeliefs(),1)[0]
            self.assertEqual(value,expected)
            self.assertEqual(record['fallback_reason'],'visible_reply')
            self.assertEqual(record['method'],'residual_analytic_fallback')
        history=[dict(hospital='H01',submitted_at=900,observed_at=980,outcome='timeout')]
        _,record=queue(rows=rows,history=history)
        self.assertEqual(record['method'],'weighted_prefix_dp')

    def test_head_start_uses_only_the_same_prior_terminal_bound_as_residual(self):
        rows=[row('A',900),row('B',950)]
        previous=dict(hospital='H01',submitted_at=800,observed_at=980,outcome='decline')
        irrelevant=dict(hospital='H01',submitted_at=910,observed_at=990,outcome='reply')
        value,record=queue(rows=rows,history=[previous,irrelevant])
        self.assertEqual(record['prior_terminal_reply'],previous)
        self.assertEqual(record['estimated_start_at'],980)
        self.assertEqual(record['age_seconds'],20)
        expected=new._prefix_workload(math.log(66),.7,.79,980,[900,950],1000)['wait_seconds']
        self.assertEqual(value,expected)

    def test_same_second_fifo_order_is_original_deterministic_tie_not_private_truth(self):
        rows=[row('B',900),row('A',900),row(2,900)]
        one=queue(rows=rows);two=queue(rows=list(reversed(rows)))
        self.assertEqual(one,two)
        self.assertEqual([r['patient'] for r in one[1]['fifo_order']],[2,'A','B'])
        self.assertEqual(one[1]['head']['patient'],2)

    def test_extreme_tail_underflow_explicitly_retains_original_analytic_result(self):
        rows=[row('A',0),row('B',0)]
        expected=old._queue_wait_estimate('H01',1e100,rows,[],FixedBeliefs(),1)[0]
        value,record=queue(rows=rows,now=1e100)
        self.assertEqual(value,expected)
        self.assertEqual(record['method'],'residual_analytic_fallback')
        self.assertEqual(record['fallback_reason'],'numerical_underflow')
        self.assertIsNone(record['silence_probability']);self.assertEqual(record['prefix_layers'],[])
        self.assertTrue(math.isfinite(value))

    def test_dp_trace_normalizer_layers_and_work_are_consistent(self):
        value,record=queue(rows=[row('A',900),row('B',930),row('C',980)])
        self.assertEqual(record['method'],'weighted_prefix_dp')
        self.assertIsNone(record['fallback_reason'])
        self.assertEqual(record['grid_base_seconds'],5);self.assertEqual(record['grid_max_cells'],60)
        self.assertEqual([r['position'] for r in record['prefix_layers']],[0,1,2])
        self.assertGreater(record['silence_probability'],0.)
        self.assertLessEqual(record['silence_probability'],1.)
        self.assertEqual(value,record['estimated_wait_seconds'])
        self.assertEqual(value,record['conditional_work_seconds'])
        self.assertEqual(value,record['remaining_numerator']/record['silence_probability'])


class FIFOPolicySingleFactorTests(unittest.TestCase):
    def test_spec_changes_only_identity_and_the_base_source_is_bound(self):
        before=old.spec(old.NAME);after=new.spec(new.NAME)
        for key in ('name','label'):before.pop(key);after.pop(key)
        self.assertEqual(before,after)
        self.assertEqual(hashlib.sha256(Path(old.__file__).read_bytes()).hexdigest(),new.BASE_SOURCE_SHA256)
        for name in (None,old.NAME,'eat_fifo_l30_f'):
            with self.assertRaises(ValueError):new.spec(name)

    def test_probability_planner_and_head_analytic_kernels_remain_frozen_objects(self):
        for name in ('Beliefs','expected_cost','_lognorm_cdf','_masses','_no_accept_by','_shift',
                '_log_normal_survival','head_expected_remaining'):
            self.assertIs(getattr(new,name),getattr(old,name))

    def test_empty_single_and_fresh_queues_preserve_original_output_core_exactly(self):
        for rows in ([],[row('A',800)],[row('A',1000),row('B',1000)]):
            for n in (0,1,8):
                args=inputs(n,pending=rows)
                self.assertEqual(core(new.decide(new.NAME,**args)),old_core(old.decide(old.NAME,**args)))

    def test_evidence_is_only_fifo_original_pool_and_has_no_other_candidate_combination(self):
        result=new.decide(new.NAME,**inputs(10,pending=[row('A',800),row('B',900)]))
        evidence=result['evidence'];trace=evidence['fifo_queue']
        self.assertEqual(trace['schema_version'],1);self.assertEqual(trace['rule'],new.FIFO_RULE)
        self.assertEqual([r['hospital'] for r in trace['hospitals']],HOSPITALS)
        for key in ('residual_queue','guard','batch','shortlist'):self.assertNotIn(key,evidence)

    def test_original_pending_cdf_horizon_and_review_are_unchanged(self):
        rows=[row('P',995,'OWN0'),row('P',995,'OWN1')]
        own=[dict(r,travel_seconds=300+i) for i,r in enumerate(rows)]
        args=inputs(pending=rows+[row('A',800),row('B',900)],patient_pending=own,last_contact_at=850)
        before=old.decide(old.NAME,**args);after=new.decide(new.NAME,**args)
        for key in ('horizon_seconds','expected_cost_without_new','own_waiting_count',
                'own_observation_timed_out_count','unresolved_inquiry_count','lambda_seconds','congestion_aware'):
            self.assertEqual(after['evidence'][key],before['evidence'][key])
        self.assertEqual(after['next_review_at'],before['next_review_at'])

    def test_inputs_are_unchanged_and_hidden_or_future_fields_cannot_change_output(self):
        args=inputs(pending=[row('A',800),row('B',900)])
        before=deepcopy(args);expected=new.decide(new.NAME,**args)
        self.assertEqual(args,before)
        self.assertEqual(new.decide(new.NAME,**args),expected)
        args['pending'][0].update(workers=99,potential_service_seconds=1,future_reply_at=1001)
        args['pending'].append(row('FUTURE',1001))
        args['observations'].append(dict(hospital='H01',submitted_at=800,observed_at=1001,outcome='accept'))
        self.assertEqual(new.decide(new.NAME,**args),expected)

    def test_original_pure_api_keeps_generic_iterable_and_malformed_input_behavior(self):
        args=inputs(pending=[row('A',800),row('B',900)])
        expected=new.decide(new.NAME,**args)
        for key in ('candidates','observations','pending','patient_pending'):
            args[key]=(r for r in args[key])
        self.assertEqual(new.decide(new.NAME,**args),expected)
        for change in (dict(now=True),dict(now=-1),dict(max_new=False),dict(max_new=-1),
            dict(patient_pending=[dict(hospital='X',patient='P',submitted_at=900,travel_seconds=300)])):
            with self.assertRaises(ValueError):new.decide(new.NAME,**inputs(**change))

    def test_ast_diff_is_only_queue_evidence_identity_and_new_queue_function_binding(self):
        before=ast.parse(inspect.getsource(old.decide)).body[0]
        after=ast.parse(inspect.getsource(new.decide)).body[0]
        replacements=0
        for node in ast.walk(after):
            if isinstance(node,ast.Subscript) and isinstance(node.value,ast.Name) and node.value.id=='evidence' \
                    and isinstance(node.slice,ast.Constant) and node.slice.value=='fifo_queue':
                node.slice.value='residual_queue';replacements+=1
            elif isinstance(node,ast.Name) and node.id=='FIFO_RULE':node.id='RESIDUAL_RULE'
        self.assertEqual(replacements,1)
        self.assertEqual(ast.dump(after,include_attributes=False),ast.dump(before,include_attributes=False))
        self.assertIsNot(new._queue_wait_estimate,old._queue_wait_estimate)


HOSPITALS=[f'H{i:02}' for i in range(1,7)]

if __name__=='__main__':unittest.main()
