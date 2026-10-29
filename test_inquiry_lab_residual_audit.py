"""Independent residual math and saved full-raw evidence mutations only."""
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

from inquiry_lab import residual_audit as audit

ROOT=Path(__file__).resolve().parent
FIXTURE=ROOT/'simulation/fixtures/eat_residual_20261020/mix7.json.gz'
COMPRESSED_SHA256='92e3ac5dcf65859e68c1f4756e5b1067d05f9740e94c37c90a33ca3e6b1c862f'
RAW_SHA256='643b8d83777d0f6ed71932e629ec46e9657938ce07d86bdbb4409f28e2179b96'


def inputs():
    now=audit.EPOCH+200
    return dict(now=now,candidates=[dict(hospital_id=f'H{i:02d}',travel=dict(duration_seconds=100+i))
        for i in range(1,9)],observations=[],pending=[
            dict(hospital='H01',patient='Q1',submitted_at=now-100),
            dict(hospital='H01',patient='Q2',submitted_at=now-40)],patient_pending=[],
        max_new=10,last_contact_at=None)


def output(value):
    rows=audit.residual_rows(value)
    return dict(send=[],next_review_at=value['now']+15,reason='unit_math_evidence',
        evidence=dict(policy=audit.NAME,sent_hospitals=[],candidate_plan=[dict(hospital=row['hospital'])
            for row in rows],residual_queue=dict(schema_version=1,
                rule='head_residual_plus_full_followers',hospitals=rows)))


def fixture():
    before=FIXTURE.stat();compressed=FIXTURE.read_bytes();after=FIXTURE.stat()
    if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns) \
            or hashlib.sha256(compressed).hexdigest()!=COMPRESSED_SHA256:
        raise AssertionError('saved first residual fixture compressed bytes changed')
    raw=gzip.decompress(compressed)
    if hashlib.sha256(raw).hexdigest()!=RAW_SHA256:raise AssertionError('saved residual raw bytes changed')
    value=json.loads(raw)
    if value['scenario']['seed']!=7 or len(value['branches'])!=5:raise AssertionError('actual fixture identity differs')
    return value


class ResidualIndependentMathTests(unittest.TestCase):
    def test_zero_age_equals_full_mean_for_every_reply_probability(self):
        mu,sigma=math.log(66),.7;mean=math.exp(mu+.5*sigma*sigma)
        for reply in (0,.4,.79,1):
            self.assertEqual(audit.expected_remaining(0,mu,sigma,reply),mean)

    def test_conditional_work_matches_independent_numerical_quadrature(self):
        # Integrate (exp(y)-age)*normal_density(y), a different calculation
        # from the analytic lognormal tail moment used by either implementation.
        mu,sigma=math.log(66),.7
        for age in (10,100,300):
            lo=math.log(age);hi=mu+12*sigma;n=2000;step=(hi-lo)/n
            f=lambda y:(math.exp(y)-age)*math.exp(-.5*((y-mu)/sigma)**2)/(sigma*math.sqrt(2*math.pi))
            integral=step/3*(f(lo)+f(hi)+sum((4 if i%2 else 2)*f(lo+i*step) for i in range(1,n)))
            sf=.5*math.erfc((math.log(age)-mu)/(sigma*math.sqrt(2)))
            for reply in (0,.79,1):
                expected=integral/((1-reply)+reply*sf)
                self.assertAlmostEqual(audit.expected_remaining(age,mu,sigma,reply),expected,places=6)

    def test_silent_completion_mixture_is_not_always_conditional_service_survival(self):
        values=[audit.expected_remaining(200,math.log(66),.7,r) for r in (0,.79,1)]
        self.assertLess(values[0],values[1]);self.assertLess(values[1],values[2])
        self.assertLess(audit.expected_remaining(10000,math.log(66),.7,0),1e-5)

    def test_extreme_tail_is_finite_without_erfc_zero_division(self):
        for age in (1e10,1e100):
            for reply in (0,.79,1):
                result=audit.expected_remaining(age,math.log(66),.25,reply)
                self.assertTrue(math.isfinite(result));self.assertGreaterEqual(result,0)

    def test_invalid_distribution_values_rejected(self):
        for args in ((-1,4,.7,.79),(1,4,0,.79),(1,4,.7,1.1),(1,float('nan'),.7,.79)):
            with self.assertRaises(audit.ResidualAuditError):audit.expected_remaining(*args)

    def test_head_plus_full_followers_and_first_six_pool(self):
        value=inputs();rows=audit.residual_rows(value)
        self.assertEqual([row['hospital'] for row in rows],[f'H{i:02d}' for i in range(1,7)])
        row=rows[0];self.assertEqual(row['queue_count'],2);self.assertEqual(row['queued_behind_count'],1)
        self.assertAlmostEqual(row['estimated_wait_seconds'],row['head_expected_remaining_seconds']+row['mean_service_seconds'])
        self.assertEqual(row['original_full_wait_seconds'],2*row['mean_service_seconds'])

    def test_empty_queue_has_no_invented_head_clock_or_residual(self):
        row=audit.residual_rows(inputs())[1]
        self.assertIsNone(row['head']);self.assertIsNone(row['estimated_start_at']);self.assertIsNone(row['age_seconds'])
        self.assertEqual(row['estimated_wait_seconds'],0);self.assertEqual(row['head_expected_remaining_seconds'],0)

    def test_only_strictly_earlier_submitted_terminal_reply_can_restart_head_clock(self):
        value=inputs();now=value['now']
        value['observations']=[dict(hospital='H01',submitted_at=now-130,observed_at=now-60,outcome='decline'),
            dict(hospital='H01',submitted_at=now-100,observed_at=now-10,outcome='accept'),
            dict(hospital='H01',submitted_at=now-140,observed_at=now-5,outcome='reply'),
            dict(hospital='H01',submitted_at=now-150,observed_at=now-2,outcome='timeout')]
        row=audit.residual_rows(value)[0]
        self.assertEqual(row['prior_terminal_reply'],value['observations'][0])
        self.assertEqual(row['estimated_start_at'],now-60);self.assertEqual(row['age_seconds'],60)

    def test_head_tie_has_declared_public_identity_order(self):
        value=inputs();now=value['now']
        value['pending']=[dict(hospital='H01',patient='Q',submitted_at=now-30),
            dict(hospital='H01',patient=2,submitted_at=now-30)]
        self.assertEqual(audit.residual_rows(value)[0]['head']['patient'],2)

    def test_trace_arithmetic_count_fields_and_original_pool_cannot_be_changed(self):
        value=inputs()
        for field,replacement in (('queue_count',3),('assumed_workers',2),('queued_behind_count',0),
                ('age_seconds',0),('estimated_wait_seconds',0),('head_expected_remaining_seconds',1000)):
            result=output(value);result['evidence']['residual_queue']['hospitals'][0][field]=replacement
            with self.assertRaises(audit.ResidualAuditError):audit.audit_residual(value,result)
        result=output(value);result['evidence']['candidate_plan'].reverse()
        with self.assertRaises(audit.ResidualAuditError):audit.audit_residual(value,result)

    def test_no_rejected_shortlist_combination_and_no_new_threshold(self):
        value=inputs();result=output(value);self.assertEqual(audit.audit_residual(value,result),6)
        result['evidence']['shortlist']={}
        with self.assertRaises(audit.ResidualAuditError):audit.audit_residual(value,result)
        result=output(value);result['evidence']['residual_queue']['threshold']=30
        with self.assertRaises(audit.ResidualAuditError):audit.audit_residual(value,result)

    def test_parent_workflow_and_censor_ledger_are_exact_bound_original_functions(self):
        self.assertIs(audit._ledger,audit.parent._ledger)
        self.assertIs(audit._audit_trace_fixed,audit._fixed._audit_trace_fixed)
        self.assertEqual(hashlib.sha256(audit.FIXED_TRACE_PATH.read_bytes()).hexdigest(),audit.FIXED_TRACE_SHA256)

    def test_parent_call_audit_only_adds_new_evidence_check_and_counter(self):
        code=inspect.getsource(audit._audit_calls_residual)
        code=code.replace('def _audit_calls_residual(', 'def _audit_calls(',1)
        code=code.replace("if call['name']==NAME:changed+=audit_residual(inputs,output)",
            "if call['name']==NAME:changed+=audit_shortlist(inputs,output)",1)
        code=code.replace("else:require('residual_queue' not in output['evidence'] and 'shortlist' not in output['evidence'],'reference policy contains residual intervention')",
            "else:require('shortlist' not in output['evidence'],'reference policy contains shortlist intervention')",1)
        code=code.replace('residual_queue_candidates_checked=changed','shortlist_membership_changes=changed',1)
        self.assertEqual(code,inspect.getsource(audit.parent._audit_calls))

    def test_false_zero_age_and_unknown_trace_fields_rejected(self):
        value=inputs();value['pending'][0]['submitted_at']=value['now']
        value['pending'][1]['submitted_at']=value['now']
        result=output(value);result['evidence']['residual_queue']['hospitals'][0]['age_seconds']=False
        with self.assertRaises(audit.ResidualAuditError):audit.audit_residual(value,result)
        result=output(value);result['evidence']['residual_queue']['hospitals'][0]['private_due']=300
        with self.assertRaises(audit.ResidualAuditError):audit.audit_residual(value,result)

    def test_clean_import_has_no_producer_server_or_kernel(self):
        code=("import json,sys; import inquiry_lab.residual_audit; print(json.dumps(sorted(n for n in sys.modules "
            "if n.startswith(('server.','simulation.','ortools')) or n in {'inquiry_lab.eat_policy',"
            "'inquiry_lab.residual_policy','inquiry_lab.residual_runner','inquiry_lab.shortlist_policy'})))")
        result=subprocess.check_output([sys.executable,'-B','-c',code],cwd=ROOT,text=True)
        self.assertEqual(json.loads(result),[])


class ResidualActualSavedRawAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.raw=fixture()

    def pair(self):
        raw=deepcopy(self.raw)
        return raw['scenario'],next(row for row in raw['branches'] if row['policy']==audit.NAME)

    def reject(self,scenario,branch):
        with self.assertRaises(audit.ResidualAuditError):audit.audit_branch(scenario,branch)

    def call(self,branch):
        return next(row for row in branch['policy_calls'] if any(h['queue_count'] for h in row['output']['evidence']['residual_queue']['hospitals']))

    def sync(self,branch,call):
        row=next(row for row in branch['db_decisions'] if row['id']==call['db_decision_id'])
        row.update(evidence=deepcopy(call['output']['evidence']),reason=call['output']['reason'],hospital_ids=deepcopy(call['output']['send']))

    def test_actual_five_full_raw_arms_and_read_only(self):
        raw=deepcopy(self.raw);before=deepcopy(raw);checked=audit.audit_replay(raw)
        self.assertTrue(checked['passed']);self.assertEqual(checked['branches'],5);self.assertEqual(raw,before)

    def test_actual_residual_numbers_rebuilt(self):
        for key in ('estimated_wait_seconds','service_mu','service_sigma','reply_probability','mean_service_seconds'):
            scenario,branch=self.pair();call=self.call(branch)
            call['output']['evidence']['residual_queue']['hospitals'][0][key]+=1
            self.sync(branch,call);self.reject(scenario,branch)

    def test_actual_unknown_input_or_future_observation_rejected(self):
        scenario,branch=self.pair();call=self.call(branch);call['inputs']['workers']=1;self.reject(scenario,branch)
        scenario,branch=self.pair();call=self.call(branch)
        call['inputs']['observations'].append(dict(hospital='H01',patient='future',submitted_at=call['inputs']['now'],
            observed_at=call['inputs']['now']+1,outcome='accept'));self.reject(scenario,branch)

    def test_original_pending_capture_and_db_watermark_preserved(self):
        scenario,branch=self.pair();call=self.call(branch);call['inputs']['pending'].pop();self.reject(scenario,branch)
        scenario,branch=self.pair();call=self.call(branch);call['db_event_prefix_max']=-1;self.reject(scenario,branch)

    def test_actual_service_worker_and_outcome_checks_remain_active(self):
        scenario,branch=self.pair();next(row for row in branch['trace'] if row['type']=='review_started')['service_seconds']+=1
        self.reject(scenario,branch)
        scenario,branch=self.pair();next(row for row in branch['trace'] if row['type']=='reply_accept')['type']='reply_decline'
        self.reject(scenario,branch)

    def test_actual_wait_and_work_recomputed_from_full_raw(self):
        for key in ('selection_wait_capped_mean','review_work_seconds'):
            scenario,branch=self.pair();branch['metrics'][key]+=1;self.reject(scenario,branch)

    def test_actual_pool_keeps_original_nearest_six(self):
        scenario,branch=self.pair();call=self.call(branch);call['output']['evidence']['candidate_plan'].reverse()
        self.sync(branch,call);self.reject(scenario,branch)


if __name__=='__main__':unittest.main()
