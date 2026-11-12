"""Synthetic scalar and hook regressions; no producer or new scenario runs."""
from copy import deepcopy
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from inquiry_lab import cohort_hedge_numeric_audit as audit

F=audit.frozen
ROOT=Path(__file__).resolve().parents[2]


def owner_fixture(job=True):
    now=1000
    return dict(patient=dict(id='OWNER',state='WAITING',version=1,reported_priority=None,
            priority_source='unknown',effective_priority=4),evaluation_end_at=1017,
        job=dict(patient_id='OWNER',version=1,started_at=now,deadline_at=1017,
            next_evaluation_at=1007,enabled=1,policy_name=F.NAME) if job else None,
        valid_acceptance_ids=[],inputs=dict(now=now,observations=[],pending=[],patient_pending=[],
            candidates=[dict(hospital_id='NEW',travel=dict(duration_seconds=100),resources_enough=True)],
            max_new=10,last_contact_at=None))


class EmptyProductScalarTests(unittest.TestCase):
    def test_empty_product_keeps_exact_values_costs_and_input_with_only_PF_float_cast(self):
        items=[];bins=F.horizon.intervals(17);before=deepcopy((items,bins))
        original=audit._ORIGINAL_OWNER_METRICS(items,bins,17,0.)
        self.assertIs(type(original['selection_probability']),int);self.assertIs(type(original['failure_probability']),int)
        result=audit.corrected_owner_metrics(items,bins,17,0.)
        self.assertEqual(original,result);self.assertIs(type(result['selection_probability']),float)
        self.assertIs(type(result['failure_probability']),float)
        self.assertEqual(result['selection_probability'],0.);self.assertEqual(result['failure_probability'],1.)
        self.assertEqual(result['selection_probability']+result['failure_probability'],1.)
        for key in set(result)-{'selection_probability','failure_probability'}:
            self.assertEqual(result[key],original[key]);self.assertIs(type(result[key]),type(original[key]))
        self.assertEqual((items,bins),before)

    def test_nonempty_probabilities_and_strict_comparison_remain_unchanged(self):
        items=[(100,[.1,.2,.05,.01])];bins=F.horizon.intervals(17);before=deepcopy(items)
        original=audit._ORIGINAL_OWNER_METRICS(items,bins,17,3.5)
        result=audit.corrected_owner_metrics(items,bins,17,3.5)
        F.precise_same(result,original,'unchanged nonempty arithmetic');self.assertEqual(items,before)
        forged=deepcopy(result);forged['selection_probability']+=2e-13
        with self.assertRaises(ValueError):F.precise_same(forged,result,'probability tolerance unchanged')
        with patch.object(audit,'_ORIGINAL_OWNER_METRICS',return_value=dict(original,selection_probability=0.,failure_probability=1.)):
            with self.assertRaisesRegex(ValueError,'exact integer P0/F1'):audit.corrected_owner_metrics([],bins,17,0.)

    def test_no_job_and_initial_none_plan_use_same_independent_empty_case(self):
        for has_job in (False,True):
            owner=owner_fixture(has_job);before=deepcopy(owner);origin=F.freeze_owner(owner);empty_calls=[]
            def typed(items,bins,end,cost):
                if not items:empty_calls.append((end,cost))
                return audit.corrected_owner_metrics(items,bins,end,cost)
            with patch.object(F,'owner_metrics',side_effect=typed):score=F.score_owner(owner,origin,[])
            self.assertTrue(empty_calls);self.assertEqual(owner,before)
            if not has_job:
                self.assertEqual(score['planning_status'],'no_job')
                self.assertIs(type(score['metrics']['selection_probability']),float)
                self.assertEqual(score['metrics']['selection_probability'],0.)
            else:self.assertEqual(score['planning_status'],'plan')

    def test_first_recorded_empty_owner120_is_double_SHA_bound_and_public_only(self):
        build=ROOT/'.codex-build/eat-cohort-hedge-20261021'
        index_bytes=(build/'repair-raw-index.json').read_bytes()
        self.assertEqual(hashlib.sha256(index_bytes).hexdigest(),'d0ca5cd23bb0aaaca3712136aee394a7476c43a87272002c8203aeb18615fb4f')
        entry=json.loads(index_bytes)['cases'][120]
        self.assertEqual(entry['file'],'120_232065133180578048046404889944020441713.json.gz')
        packed=(build/'repair-cases'/entry['file']).read_bytes()
        self.assertEqual(hashlib.sha256(packed).hexdigest(),entry['compressed_sha256'])
        self.assertEqual(entry['compressed_sha256'],'ef1195eb3faa9ab001088266a30b9c40e3afbbc6bea881004e05206cd793a06b')
        body=gzip.decompress(packed);self.assertEqual(hashlib.sha256(body).hexdigest(),entry['raw_sha256'])
        self.assertEqual(entry['raw_sha256'],'6d3c082872e23fa35e01203f65cc32421d22108157b9466bfd45bff65a2a4f79')
        replay=json.loads(body);branch=next(b for b in replay['branches'] if b['policy']==F.NAME)
        group=next(g for g in branch['joint_groups'] if g['id']==2);ev=group['evidence']
        row=next(r for r in ev['row_scores'] if r['row_index']==84)
        score=next(o for o in row['owner_scores'] if o['patient_id']=='HIST_H01_2')
        origin=next(o for o in ev['owner_origins'] if o['patient_id']==score['patient_id'])
        self.assertEqual(origin['own_items'],[]);self.assertEqual(score['planning_status'],'no_job')
        remaining=origin['evaluation_end_at']-origin['now'];self.assertEqual(remaining,103)
        actual=audit.corrected_owner_metrics([],F.horizon.intervals(remaining),remaining,0.)
        F.precise_same(score['metrics'],actual,'saved public empty owner exact independent metrics')


class NumericHookTests(unittest.TestCase):
    def test_candidate_count_original_callable_capture_no_mutation_and_restoration(self):
        raw={'branches':[{'policy':F.NAME}]};before=deepcopy(raw);old=F.owner_metrics;bins=F.horizon.intervals(17)
        proof=dict(passed=True,history_provenance_addendum={'historical_owner_occurrences_checked':1})
        def inherited(value):
            self.assertIs(value,raw)
            for _ in range(2):self.assertIs(type(F.owner_metrics([],bins,17,0.)['failure_probability']),float)
            return proof
        with patch.object(audit,'_ORIGINAL_HISTORY_REPLAY',side_effect=inherited), \
                patch.object(audit.history,'audit_replay',side_effect=AssertionError('must use captured callable')):
            result=audit.audit_replay(raw)
        self.assertEqual(raw,before);self.assertIs(F.owner_metrics,old)
        self.assertEqual(result['history_provenance_addendum'],proof['history_provenance_addendum'])
        self.assertEqual(result['numeric_audit_addendum']['empty_products_typed'],2)
        self.assertTrue(result['numeric_audit_addendum']['values_unchanged'])
        self.assertNotIn('numeric_audit_addendum',proof)

    def test_exception_restores_hook_and_nested_hook_is_rejected(self):
        raw={'branches':[{'policy':F.NAME}]};old=F.owner_metrics
        with patch.object(audit,'_ORIGINAL_HISTORY_REPLAY',side_effect=RuntimeError('synthetic audit failure')):
            with self.assertRaisesRegex(RuntimeError,'synthetic audit failure'):audit.audit_replay(raw)
        self.assertIs(F.owner_metrics,old)
        with patch.object(F,'owner_metrics',return_value={}),patch.object(audit,'_ORIGINAL_HISTORY_REPLAY') as inherited:
            with self.assertRaisesRegex(ValueError,'already replaced'):audit.audit_replay(raw)
            inherited.assert_not_called()
        self.assertIs(F.owner_metrics,old)

    def test_native_twelve_control_proof_is_returned_unchanged(self):
        raw={'branches':[{'policy':'eat_cohort_dynamic_l15_f'}]};proof={'passed':True,'branches':1}
        old=F.owner_metrics
        with patch.object(audit,'_ORIGINAL_HISTORY_REPLAY',return_value=proof):result=audit.audit_replay(raw)
        self.assertIs(result,proof);self.assertIs(F.owner_metrics,old)
        self.assertNotIn('numeric_audit_addendum',result)

    def test_import_installs_no_hook_and_imports_no_producer_runner_or_model(self):
        code="import sys;from inquiry_lab import cohort_hedge_audit as a;old=a.owner_metrics;from inquiry_lab import cohort_hedge_numeric_audit as n;assert a.owner_metrics is old;print([k for k in sys.modules if k.startswith('inquiry_lab.') and k.endswith(('_policy','_runner','_model','_context','_experiment'))])"
        result=subprocess.run([sys.executable,'-B','-c',code],cwd=ROOT,capture_output=True,text=True,check=True)
        self.assertEqual(result.stdout.strip(),'[]')


if __name__=='__main__':unittest.main()
