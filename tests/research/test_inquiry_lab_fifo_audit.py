"""Independent FIFO mathematics and already-saved full-raw mutations only.

Synthetic output builders validate recorded plan consistency, not optimizer
choice. No producer, planner, server or performance scenario executes here.
"""
from copy import deepcopy
import gzip
import hashlib
import inspect
import json
import math
from pathlib import Path
import subprocess
import sys
import unittest

from inquiry_lab import fifo_audit as audit

ROOT=Path(__file__).resolve().parents[2]
OLD_FIXTURE=ROOT/'simulation/fixtures/eat_guard_20261020/mix7.json.gz'
OLD_COMPRESSED_SHA256='541760d9797dd53b65cff243d2fa1d20d948c4b70eb3966b3aa5dff0d6a8bef1'
OLD_RAW_SHA256='9a2552dffb41e161c5dc8ff1936b1db22fbe6d24a2c0ba4827453869360a97fc'
FIXTURE=ROOT/'simulation/fixtures/eat_fifo_20261020/mix7.json.gz'
COMPRESSED_SHA256='8dcd1571f3ea70e9e01280a4868dde192d08865f647f6b62a70abdb97d4f21fc'
RAW_SHA256='28b578848f4d61d064e9158537891224622c048bf50f18140f888fb13a4f634d'


def inputs():
    now=audit.EPOCH+200
    return dict(now=now,candidates=[dict(hospital_id=f'H{i:02d}',travel=dict(duration_seconds=100+i))
        for i in range(1,9)],observations=[],pending=[
            dict(hospital='H01',patient='Q1',submitted_at=now-102),
            dict(hospital='H01',patient='Q2',submitted_at=now-40)],patient_pending=[],
        max_new=10,last_contact_at=None)


def closed_form(x,mu,sigma):
    mean=math.exp(mu+.5*sigma*sigma)
    if x<=0:return 1.,mean
    z=(math.log(x)-mu)/sigma
    sf=.5*math.erfc(z/math.sqrt(2))
    return sf,mean*.5*math.erfc((z-sigma)/math.sqrt(2))-x*sf


def two_job_paths(now,arrival,mu,sigma,reply,endpoints):
    """Direct 0/1/2 completed-job path enumeration on supplied grid."""
    q=1-reply;mean=math.exp(mu+.5*sigma*sigma);sf,stop=closed_form(now,mu,sigma)
    denominator=sf;numerator=stop+mean*sf;completed=0.
    cdf=lambda x:0. if x<=0 else .5*math.erfc(-(math.log(x)-mu)/(sigma*math.sqrt(2)))
    for lower,upper in zip(endpoints,endpoints[1:]):
        probability=cdf(upper)-cdf(lower);start=max(upper,arrival)
        survival,remaining=closed_form(now-start,mu,sigma)
        denominator+=q*probability*survival+q*q*probability*(1-survival)
        numerator+=q*probability*remaining
        completed+=q*q*probability*(1-survival)
    return denominator,numerator,completed


def two_job_continuous(now,arrival,mu,sigma,reply):
    """Continuous integral over first service duration; not a prefix DP."""
    n=6000;lower=mu-12*sigma;upper=math.log(now);step=(upper-lower)/n
    q=1-reply;mean=math.exp(mu+.5*sigma*sigma);sf,stop=closed_form(now,mu,sigma)
    def integrand(y):
        density=math.exp(-.5*((y-mu)/sigma)**2)/(sigma*math.sqrt(2*math.pi))
        survival,remaining=closed_form(now-max(math.exp(y),arrival),mu,sigma)
        return density*(q*survival+q*q*(1-survival)),density*q*remaining
    left,right=integrand(lower),integrand(upper);den=left[0]+right[0];num=left[1]+right[1]
    for i in range(1,n):
        d,v=integrand(lower+i*step);weight=4 if i%2 else 2;den+=weight*d;num+=weight*v
    den=sf+step*den/3;num=stop+mean*sf+step*num/3
    return den,num,num/den


def base_record(age=102,n=2,reply=.79,mu=math.log(66),sigma=.7):
    mean=math.exp(mu+.5*sigma*sigma);remaining=audit.residual.expected_remaining(age,mu,sigma,reply)
    queue=[dict(hospital='H',patient=f'P{i}',submitted_at=0) for i in range(n)]
    row=dict(hospital='H',queue_count=n,assumed_workers=1,head=queue[0] if queue else None,
        prior_terminal_reply=None,estimated_start_at=0 if queue else None,age_seconds=age if queue else None,
        service_mu=mu,service_sigma=sigma,mean_service_seconds=mean,reply_probability=reply,
        head_expected_remaining_seconds=remaining if queue else 0,queued_behind_count=max(0,n-1),
        estimated_wait_seconds=remaining+(n-1)*mean if queue else 0,original_full_wait_seconds=n*mean)
    return queue,row


def output(value):
    model=audit.batch.batch_model(value);rows=audit.fifo_rows(value);beliefs=audit.parent._beliefs(value['observations'],value['now'])
    plan=[];items=list(model['current']);send=[]
    for index,(candidate,record) in enumerate(zip(model['pool'],rows)):
        hid=candidate['hospital'];r,a,mu,sigma=beliefs(hid)
        offset=0 if index==0 and model['room']>0 else None
        plan.append(dict(hospital=hid,planned_offset_seconds=offset,inquiry_cost=15,
            reply_probability=round(r,4),accept_probability=round(a,4),
            queue_length=record['queue_count'],travel_seconds=candidate['travel_seconds']))
        if offset==0:
            wait=record['estimated_wait_seconds'];send.append(hid)
            masses=audit.parent._masses(lambda x:audit.parent._cdf(x-wait,mu,sigma),r*a,model['cells'])
            items.append((candidate['travel_seconds'],masses))
    cost=audit.batch.expected_first_accept_cost(items,model['cells'],model['horizon'])+15*len(send)
    base=audit.batch.expected_first_accept_cost(model['current'],model['cells'],model['horizon'])
    if value['max_new']==0:reason='contact_budget'
    elif send:reason='eat_initial' if not value['patient_pending'] else 'eat_hedge'
    elif not value['candidates']:reason='no_candidates'
    elif model['room']<=0:reason='eat_concurrency_cap'
    else:reason='eat_wait'
    evidence=dict(evaluated_at=value['now'],policy=audit.NAME,horizon_seconds=model['horizon'],lambda_seconds=15,
        congestion_aware=False,expected_cost_without_new=round(base,3),planned_expected_cost=round(cost,3),
        reply_probability_global=.79,accept_probability_global=.4,service_median_seconds=66,
        history_used_count=len(value['observations']),own_waiting_count=model['fresh'],
        own_observation_timed_out_count=len(value['patient_pending'])-model['fresh'],
        unresolved_inquiry_count=len(value['pending']),candidate_plan=plan,sent_hospitals=list(send),
        fifo_queue=dict(schema_version=1,rule=audit.FIFO_RULE,hospitals=deepcopy(rows)))
    return dict(send=send,reason=reason,evidence=evidence,next_review_at=value['now']+15)


def load_fixture(path,compressed_sha,raw_sha):
    before=path.stat();compressed=path.read_bytes();after=path.stat()
    if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns) \
            or hashlib.sha256(compressed).hexdigest()!=compressed_sha:
        raise AssertionError('saved fixture compressed bytes changed')
    raw=gzip.decompress(compressed)
    if hashlib.sha256(raw).hexdigest()!=raw_sha:raise AssertionError('saved fixture raw bytes changed')
    return json.loads(raw)


class FifoIndependentMathTests(unittest.TestCase):
    def test_two_job_complete_path_enumeration_matches_unweighted_prefix_N_D(self):
        mu,sigma,reply=math.log(5),.6,.79
        result=audit.prefix_expectation([0,4],12,0,mu,sigma,reply,5)
        self.assertEqual(result['endpoints'],[0.,5.,10.,12.])
        den,num,completed=two_job_paths(12,4,mu,sigma,reply,result['endpoints'])
        self.assertAlmostEqual(result['denominator'],den,places=12)
        self.assertAlmostEqual(result['numerator_seconds'],num,places=12)
        self.assertAlmostEqual(result['all_completed_silence_mass'],completed,places=12)
        self.assertGreater(completed,0)
        self.assertAlmostEqual(result['expected_remaining_seconds'],num/den,places=12)

    def test_grid_refinement_converges_to_continuous_two_job_integral(self):
        mu,sigma,reply=math.log(5),.6,.79
        _,_,truth=two_job_continuous(12,4,mu,sigma,reply)
        values=[audit.prefix_expectation([0,4],12,0,mu,sigma,reply,s)['expected_remaining_seconds']
            for s in (5,2,1,.5,.25)]
        self.assertLess(abs(values[-1]-truth),abs(values[0]-truth))
        self.assertLess(abs(values[-1]-truth),.15)

    def test_r_zero_silence_probability_is_exactly_one_and_all_mass_retained(self):
        result=audit.prefix_expectation([0,3,7],23,0,math.log(5),.6,0,5)
        self.assertEqual(result['denominator'],1)
        total=sum(r['active_denominator'] for r in result['stages'])+result['all_completed_silence_mass']
        self.assertAlmostEqual(total,1,places=12)
        self.assertGreaterEqual(result['numerator_seconds'],0)

    def test_every_layer_numerator_denominator_and_silent_completion_mass_accounted(self):
        result=audit.prefix_expectation([0,0,4],12,0,math.log(5),.6,.79,5)
        self.assertEqual(len(result['stages']),3)
        self.assertAlmostEqual(result['denominator'],sum(r['active_denominator'] for r in result['stages'])+
            result['all_completed_silence_mass'],places=12)
        self.assertAlmostEqual(result['numerator_seconds'],sum(r['active_numerator'] for r in result['stages']),places=12)
        for index,row in enumerate(result['stages']):
            self.assertEqual(row['position'],index);self.assertGreaterEqual(row['active_numerator'],0)
            self.assertGreaterEqual(row['entry_weight'],row['completed_weight'])
        self.assertGreater(result['denominator'],0);self.assertLessEqual(result['denominator'],1)

    def test_staggered_follower_arrivals_and_prior_head_clock_are_respected(self):
        early=audit.prefix_expectation([0,0],12,0,math.log(5),.6,.79,1)
        late=audit.prefix_expectation([0,11],12,0,math.log(5),.6,.79,1)
        self.assertNotAlmostEqual(early['expected_remaining_seconds'],late['expected_remaining_seconds'],places=5)
        shifted=audit.prefix_expectation([100,111],112,100,math.log(5),.6,.79,1)
        self.assertAlmostEqual(shifted['expected_remaining_seconds'],late['expected_remaining_seconds'],places=12)

    def test_empty_single_job_fresh_head_and_responsive_boundaries_are_old_R_exact(self):
        for age,n,r,reason in ((12,0,.79,'empty_queue'),(12,1,.79,'single_job_exact'),
                (0,3,.79,'fresh_head_exact'),(12,3,1,'fully_responsive_exact')):
            queue,base=base_record(age,n,r);result=audit.fifo_estimate(queue,[],age,base)
            self.assertEqual(result['fallback_reason'],reason);self.assertEqual(result['method'],'residual_analytic_exact')
            self.assertEqual(result['estimated_wait_seconds'],base['estimated_wait_seconds'])
            self.assertIsNone(result['silence_probability']);self.assertEqual(result['prefix_layers'],[])

    def test_visible_non_timeout_reply_and_worker_assumption_use_explicit_old_R_fallback(self):
        queue,base=base_record();result=audit.fifo_estimate(queue,[],102,base,2)
        self.assertEqual(result['fallback_reason'],'worker_assumption')
        self.assertEqual(result['estimated_wait_seconds'],base['estimated_wait_seconds']/2)
        for outcome in ('reply','accept','decline'):
            history=[dict(hospital='H',submitted_at=0,observed_at=50,outcome=outcome)]
            result=audit.fifo_estimate(queue,history,102,base)
            self.assertEqual(result['fallback_reason'],'visible_reply')
            self.assertEqual(result['method'],'residual_analytic_fallback')
        result=audit.fifo_estimate(queue,[dict(hospital='H',submitted_at=0,observed_at=50,outcome='timeout')],102,base)
        self.assertEqual(result['method'],'weighted_prefix_dp')

    def test_extreme_tail_underflow_has_independent_evidence_and_no_silent_zero_wait(self):
        queue,base=base_record(1e100);result=audit.fifo_estimate(queue,[],1e100,base)
        self.assertEqual(result['fallback_reason'],'numerical_underflow')
        self.assertEqual(result['estimated_wait_seconds'],base['estimated_wait_seconds'])
        self.assertGreater(result['estimated_wait_seconds'],0)

    def test_adaptive_grid_is_derived_from_original_sixty_cell_bound(self):
        queue,base=base_record(1001);result=audit.fifo_estimate(queue,[],1001,base)
        self.assertEqual(result['method'],'weighted_prefix_dp')
        self.assertEqual(result['grid_step_seconds'],17)
        self.assertLessEqual(len(result['grid_offsets_seconds'])-1,60)
        self.assertEqual(result['grid_offsets_seconds'][-1],1001)

    def test_additional_silence_condition_can_increase_work_no_false_old_R_upper_clamp(self):
        queue,base=base_record(300,2,.9);result=audit.fifo_estimate(queue,[],300,base)
        self.assertEqual(result['method'],'weighted_prefix_dp')
        self.assertGreater(result['estimated_wait_seconds'],base['estimated_wait_seconds'])

    def test_future_invalid_submission_and_unsorted_input_are_rejected(self):
        for submitted,now,start in (([0,13],12,0),([4,0],12,0),([4,5],12,0),([-1,0],12,0),([0],12,13)):
            with self.assertRaises(audit.FifoAuditError):audit.prefix_expectation(submitted,now,start,math.log(5),.6,.79,5)

    def test_normalized_queue_order_head_prior_and_all_public_fields_checked(self):
        value=inputs();result=output(value);before=deepcopy(value)
        checked=audit.audit_fifo_plan(value,result);self.assertEqual(checked['fifo_dp_candidates_checked'],1)
        self.assertEqual(value,before)
        for field,replacement in (('queue_count',3),('assumed_workers',2),('age_seconds',0),
                ('estimated_wait_seconds',0),('silence_probability',1),('remaining_numerator',0),
                ('grid_step_seconds',10),('grid_offsets_seconds',[0,102]),('fifo_order',[])):
            altered=deepcopy(result);altered['evidence']['fifo_queue']['hospitals'][0][field]=replacement
            with self.assertRaises(audit.FifoAuditError):audit.audit_fifo_plan(value,altered)

    def test_missing_or_modified_layer_and_fabricated_numeric_fallback_rejected(self):
        value=inputs();result=output(value)
        for kind in ('missing','numerator','count','fallback'):
            altered=deepcopy(result);row=altered['evidence']['fifo_queue']['hospitals'][0]
            if kind=='missing':row['prefix_layers'].pop()
            elif kind=='numerator':row['prefix_layers'][0]['active_numerator']+=1
            elif kind=='count':row['prefix_layers'][0]['entry_state_count']+=1
            else:row.update(method='residual_analytic_fallback',fallback_reason='numerical_underflow')
            with self.assertRaises(audit.FifoAuditError):audit.audit_fifo_plan(value,altered)

    def test_recorded_plan_cost_pool_original_CDF_clock_and_cap_remain_checked(self):
        value=inputs();result=output(value)
        for kind in ('cost','pool','offset','review','lambda','guard'):
            altered=deepcopy(result)
            if kind=='cost':altered['evidence']['planned_expected_cost']+=1
            elif kind=='pool':altered['evidence']['candidate_plan'].reverse()
            elif kind=='offset':altered['evidence']['candidate_plan'][0]['planned_offset_seconds']=5
            elif kind=='review':altered['next_review_at']+=1
            elif kind=='lambda':altered['evidence']['lambda_seconds']=30
            else:altered['evidence']['guard']={}
            with self.assertRaises(audit.FifoAuditError):audit.audit_fifo_plan(value,altered)

    def test_global_reply_accept_and_service_summary_are_rebuilt_from_public_history(self):
        value=inputs()
        for key in ('reply_probability_global','accept_probability_global','service_median_seconds'):
            result=output(value);result['evidence'][key]+=.1
            with self.assertRaises(audit.FifoAuditError):audit.audit_fifo_plan(value,result)

    def test_source_bound_ledger_and_workflow_and_full_original_call_body_restoration(self):
        self.assertEqual(hashlib.sha256(Path(audit.guard.__file__).read_bytes()).hexdigest(),audit.GUARD_AUDITOR_SHA256)
        self.assertIs(audit._ledger,audit.guard._ledger);self.assertIs(audit._audit_trace_fixed,audit.guard._audit_trace_fixed)
        source=inspect.getsource(audit._audit_calls_fifo);original=inspect.getsource(audit.guard._audit_calls_guard)
        a=source.index("        if call['name']==NAME:changed.update(audit_fifo_plan(inputs,output))")
        b=source.index('    for event in inquiry_events:',a)
        oa=original.index("        if call['name']==NAME:changed.update(audit_guard(inputs,output))")
        ob=original.index('    for event in inquiry_events:',oa)
        restored=source[:a]+original[oa:ob]+source[b:]
        restored=restored.replace('def _audit_calls_fifo(','def _audit_calls_guard(',1)
        restored=restored.replace("('fifo_queue_candidates_checked','fifo_dp_candidates_checked','fifo_exact_candidates_checked','fifo_fallback_candidates_checked')",
            "('utility_subsets_checked','guard_triggered','guard_batch_evaluated','guard_applied')",1)
        self.assertEqual(restored,original)

    def test_clean_import_never_loads_producer_server_kernel_or_solver(self):
        code=("import json,sys; import inquiry_lab.fifo_audit; print(json.dumps(sorted(n for n in sys.modules "
            "if n.startswith(('server.','simulation.','ortools')) or n in {'inquiry_lab.eat_policy',"
            "'inquiry_lab.residual_policy','inquiry_lab.residual_runner','inquiry_lab.batch_policy',"
            "'inquiry_lab.batch_runner','inquiry_lab.guard_policy','inquiry_lab.guard_runner',"
            "'inquiry_lab.fifo_policy','inquiry_lab.fifo_runner'})))")
        actual=subprocess.check_output([sys.executable,'-B','-c',code],cwd=ROOT,text=True)
        self.assertEqual(json.loads(actual),[])


class FifoFrozenReferenceRawTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.raw=load_fixture(OLD_FIXTURE,OLD_COMPRESSED_SHA256,OLD_RAW_SHA256)

    def test_all_six_old_anchors_full_raw_including_rejected_guard(self):
        value=deepcopy(self.raw);before=deepcopy(value);checked=audit.audit_replay(value)
        self.assertTrue(checked['passed']);self.assertEqual(checked['branches'],6);self.assertEqual(value,before)

    def test_old_anchor_cannot_smuggle_new_FIFO_or_previous_rejected_interventions(self):
        for name,key in ((audit.RESIDUAL_NAME,'fifo_queue'),(audit.RESIDUAL_NAME,'guard'),
                (audit.BASE_POLICY,'fifo_queue')):
            value=deepcopy(self.raw);branch=next(b for b in value['branches'] if b['policy']==name)
            call=branch['policy_calls'][0];call['output']['evidence'][key]={}
            decision=next(d for d in branch['db_decisions'] if d['id']==call['db_decision_id'])
            decision['evidence']=deepcopy(call['output']['evidence'])
            with self.assertRaises(audit.FifoAuditError):audit.audit_replay(value)


class FifoActualSavedRawAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw=load_fixture(FIXTURE,COMPRESSED_SHA256,RAW_SHA256)
        if cls.raw['scenario']['seed']!=7 or len(cls.raw['branches'])!=6:
            raise AssertionError('actual first FIFO fixture identity differs')

    def pair(self):
        raw=deepcopy(self.raw)
        return raw['scenario'],next(b for b in raw['branches'] if b['policy']==audit.NAME)

    def dp_call(self,branch):
        return next(c for c in branch['policy_calls']
            if any(r['method']=='weighted_prefix_dp' for r in c['output']['evidence']['fifo_queue']['hospitals']))

    def dp_row(self,call):
        return next(r for r in call['output']['evidence']['fifo_queue']['hospitals'] if r['method']=='weighted_prefix_dp')

    def sync(self,branch,call):
        row=next(d for d in branch['db_decisions'] if d['id']==call['db_decision_id'])
        row.update(evidence=deepcopy(call['output']['evidence']),reason=call['output']['reason'],
            hospital_ids=deepcopy(call['output']['send']))

    def reject(self,scenario,branch):
        with self.assertRaises(audit.FifoAuditError):audit.audit_branch(scenario,branch)

    def test_six_actual_full_raw_arms_all_queue_rows_layers_and_original_plan_costs(self):
        value=deepcopy(self.raw);before=deepcopy(value);result=audit.audit_replay(value)
        self.assertTrue(result['passed']);self.assertEqual(result['branches'],6);self.assertEqual(value,before)
        row=next(r for r in result['branch_audits'] if r['fifo_queue_candidates_checked'])
        self.assertEqual(row['policy_calls'],24);self.assertEqual(row['fifo_queue_candidates_checked'],110)
        self.assertEqual(row['fifo_dp_candidates_checked'],3);self.assertEqual(row['fifo_exact_candidates_checked'],107)
        self.assertEqual(row['fifo_fallback_candidates_checked'],0)
        self.assertTrue(row['fifo_queue_math_independently_reconstructed'])
        self.assertFalse(row['original_residual_search_independently_reexecuted'])
        self.assertFalse(row['optimality_claimed'])

    def test_actual_partial_last_bin_stops_exactly_now_and_no_work_tail_is_discarded(self):
        scenario,branch=self.pair();call=self.dp_call(branch);row=self.dp_row(call)
        self.assertEqual(row['age_seconds'],36);self.assertEqual(row['grid_offsets_seconds'][-3:],[30,35,36])
        row['grid_offsets_seconds'][-1]=40
        self.sync(branch,call);self.reject(scenario,branch)
        scenario,branch=self.pair();call=self.dp_call(branch);row=self.dp_row(call)
        row['grid_offsets_seconds'].pop();row['remaining_numerator']=0;row['estimated_wait_seconds']=0
        self.sync(branch,call);self.reject(scenario,branch)

    def test_actual_independent_N_D_layers_and_silent_completion_accounting_cannot_be_forged(self):
        for kind in ('den','num','layer','complete','count','scale'):
            scenario,branch=self.pair();call=self.dp_call(branch);row=self.dp_row(call)
            if kind=='den':row['silence_probability']/=2
            elif kind=='num':row['remaining_numerator']+=1
            elif kind=='layer':row['prefix_layers'][0]['active_numerator']+=1
            elif kind=='complete':row['prefix_layers'][-1]['completed_weight']+=.01
            elif kind=='count':row['prefix_layers'][0]['entry_state_count']+=1
            else:
                row['silence_probability']/=2;row['remaining_numerator']/=2
                for layer in row['prefix_layers']:
                    for key in ('entry_weight','active_denominator','active_numerator','completed_weight'):layer[key]/=2
            self.sync(branch,call);self.reject(scenario,branch)

    def test_actual_queue_public_order_worker_head_clock_and_grid_cannot_be_changed(self):
        for kind in ('order','worker','head','grid','start'):
            scenario,branch=self.pair();call=self.dp_call(branch);row=self.dp_row(call)
            if kind=='order':row['fifo_order'].reverse()
            elif kind=='worker':row['assumed_workers']=2
            elif kind=='head':row['head']['submitted_at']+=1
            elif kind=='grid':row['grid_step_seconds']=10
            else:row['estimated_start_at']+=1
            self.sync(branch,call);self.reject(scenario,branch)

    def test_actual_numeric_fallback_is_not_an_unchecked_excuse_for_replacing_DP(self):
        scenario,branch=self.pair();call=self.dp_call(branch);row=self.dp_row(call)
        row.update(method='residual_analytic_fallback',fallback_reason='numerical_underflow',
            grid_step_seconds=None,grid_offsets_seconds=[],prefix_layers=[],silence_probability=None,
            remaining_numerator=None)
        self.sync(branch,call);self.reject(scenario,branch)
        scenario,branch=self.pair();call=self.dp_call(branch);row=self.dp_row(call)
        row['fallback_reason']='visible_reply'
        self.sync(branch,call);self.reject(scenario,branch)

    def test_actual_unchanged_planner_cost_offsets_pool_and_global_belief_metadata_checked(self):
        for kind in ('cost','offset','pool','review','global'):
            scenario,branch=self.pair();call=self.dp_call(branch);result=call['output']
            if kind=='cost':result['evidence']['planned_expected_cost']+=1
            elif kind=='offset':result['evidence']['candidate_plan'][0]['planned_offset_seconds']=5
            elif kind=='pool':result['evidence']['candidate_plan'].reverse()
            elif kind=='review':result['next_review_at']+=1
            else:result['evidence']['service_median_seconds']+=1
            self.sync(branch,call);self.reject(scenario,branch)

    def test_actual_input_ledger_prefix_hidden_workers_and_future_observations_rejected(self):
        for kind in ('private','pending','prefix','future'):
            scenario,branch=self.pair();call=self.dp_call(branch)
            if kind=='private':call['inputs']['workers']=1
            elif kind=='pending':call['inputs']['pending'].pop()
            elif kind=='prefix':call['db_event_prefix_max']=-1
            else:call['inputs']['observations'].append(dict(hospital='H01',patient='future',
                submitted_at=call['inputs']['now'],observed_at=call['inputs']['now']+1,outcome='accept'))
            self.reject(scenario,branch)

    def test_actual_FIFO_workflow_service_outcome_and_wait_work_totals_are_still_audited(self):
        for kind in ('service','outcome','wait','work'):
            scenario,branch=self.pair()
            if kind=='service':next(t for t in branch['trace'] if t['type']=='review_started')['service_seconds']+=1
            elif kind=='outcome':next(t for t in branch['trace'] if t['type']=='reply_accept')['type']='reply_decline'
            elif kind=='wait':branch['metrics']['selection_wait_capped_mean']+=1
            else:branch['metrics']['review_work_seconds']+=1
            self.reject(scenario,branch)

    def test_actual_intervention_cannot_be_combined_with_rejected_guard_or_batch(self):
        for key in ('guard','batch','residual_queue','shortlist'):
            scenario,branch=self.pair();call=self.dp_call(branch);call['output']['evidence'][key]={}
            self.sync(branch,call);self.reject(scenario,branch)


if __name__=='__main__':unittest.main()
