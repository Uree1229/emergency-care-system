import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from inquiry_lab import fifo_experiment as e


def metrics():
    return {name:dict(failed=4,J=800,inquiries_per_patient=4,review_work_seconds_per_patient=300)
        for name in (e.PRIMARY,e.RESIDUAL,e.EAT,e.FIXED)}


def pairs():
    return {name:dict(wait_sum_delta_seconds=-20,lost_selections=0,ci95_wait=[-4,-1])
        for name in (e.RESIDUAL,e.EAT)}


def judge(*args,**kwargs):
    kwargs.setdefault('all_case_preservation',dict(lost_selections=0))
    return e.assess(*args,**kwargs)


class GuardExperimentTests(unittest.TestCase):
    def test_json_protocol_and_preserved_parent(self):
        self.assertEqual(e.protocol(),json.loads(e.raw(e.protocol())))
        self.assertEqual(len(e.verify_old()),64)

    def test_known_repair_and_fresh_roles_are_separate(self):
        role=[*e.seeds('development-mix',96),*e.seeds('validation-mix',320),
            *e.seeds('development-stress',3),*e.seeds('validation-stress',6)]
        self.assertEqual(len(set(role)),425);self.assertGreaterEqual(min(role),1<<127)
        self.assertFalse(set(role)&{s for _,s,_ in e.tasks('repair')})
        self.assertEqual([len(e.tasks(s)) for s in ('repair','development','validation')],[120,120,368])
        self.assertTrue(all(len(a)==1 for _,_,a in e.tasks('repair')))
        self.assertTrue(all(len(a)==6 for _,_,a in e.tasks('development')))
        with self.assertRaises(ValueError):e.tasks('unknown')

    def test_exact_four_regression_identifiers(self):
        actual={(x['seed'],x['patient']) for x in e.recoveries()}
        self.assertEqual(actual,{(228101498504225084293068059876723338508,'P06'),
            (182333718807841691121563430520403801660,'P08'),
            (303280438093041801274650730462668685752,'P03'),
            (265361016079724772267459973626161360039,'P06')})

    def test_no_net_failure_substitution_for_one_lost_recovery(self):
        m=metrics();p=pairs();recovered=[dict(preserved=True) for _ in range(4)]
        self.assertTrue(judge(m,p,'repair',recovered)['passed'])
        m[e.PRIMARY]['failed']=0;recovered[2]['preserved']=False
        self.assertFalse(judge(m,p,'repair',recovered)['passed'])
        recovered[2]['preserved']=True;p[e.RESIDUAL]['lost_selections']=1
        self.assertFalse(judge(m,p,'repair',recovered)['passed'])

    def test_wait_must_drop_against_both_baselines(self):
        m=metrics();p=pairs();self.assertTrue(judge(m,p,'development')['passed'])
        p[e.EAT]['wait_sum_delta_seconds']=1
        self.assertFalse(judge(m,p,'development')['passed'])
        p[e.EAT]['wait_sum_delta_seconds']=-1;p[e.RESIDUAL]['wait_sum_delta_seconds']=0
        self.assertFalse(judge(m,p,'development')['passed'])

    def test_J_failure_work_and_both_validation_CIs_guard(self):
        m=metrics();p=pairs();self.assertTrue(judge(m,p,'validation')['passed'])
        p[e.EAT]['ci95_wait']=[-4,1];self.assertFalse(judge(m,p,'validation')['passed'])
        p[e.EAT]['ci95_wait']=[-4,-1]
        for field,bad in [('J',801),('failed',5),('review_work_seconds_per_patient',301),('inquiries_per_patient',5)]:
            with self.subTest(field=field):
                changed=metrics();changed[e.PRIMARY][field]=bad
                self.assertFalse(judge(changed,p,'development')['passed'])
        with self.assertRaises(ValueError):judge(m,p,'unknown')

    def test_stress_success_loss_and_missing_whole_case_guard_are_rejected(self):
        self.assertFalse(e.assess(metrics(),pairs(),'development')['passed'])
        self.assertFalse(judge(metrics(),pairs(),'development',all_case_preservation={'lost_selections':1})['passed'])

    def test_failures_still_count_as_300_wait(self):
        self.assertEqual(e.wait(dict(failed=True)),300)
        self.assertEqual(e.wait(dict(failed=False,selection_seconds=70)),70)

    def _prior(self,mutation=None,stage='development'):
        with tempfile.TemporaryDirectory() as tmp,patch.object(e,'BUILD',Path(tmp)),\
                patch.object(e,'protocol',return_value={'source':'locked'}),\
                patch.object(e,'source_hashes',return_value={'a':'digest'}),\
                patch.object(e,'stage_cases',return_value=[]) as cases,\
                patch.object(e,'ProcessPoolExecutor') as pool:
            assessment={'passed':mutation!='failed'}
            r=dict(assessment=assessment,protocol={'source':'locked'},cases=[],prior_stage_evidence_sha256={})
            previous='repair' if stage=='development' else 'development'
            e.write_new(e.BUILD/(previous+'.json'),e.raw(r));digest=e.sha(e.raw(r))
            a=dict(passed=True,report_sha256=digest,source_sha256={'a':'digest'})
            lock=dict(candidate=e.PRIMARY,assessment=assessment,protocol={'source':'locked'},
                next_stage_allowed=assessment['passed'],report_sha256=digest)
            if mutation=='hash':lock['report_sha256']='changed'
            if mutation=='candidate':lock['candidate']='other'
            if mutation=='source':a['source_sha256']={'changed':'value'}
            e.write_new(e.BUILD/(previous+'-audit.json'),e.raw(a));e.write_new(e.BUILD/(previous+'-lock.json'),e.raw(lock))
            if mutation:
                with patch.object(e,'verify_freeze',return_value={'protocol':{'source':'locked'}}):
                    with self.assertRaises(AssertionError):e.execute(stage)
                cases.assert_not_called()
                self.assertFalse((e.BUILD/(stage+'-cases')).exists())
            else:
                evidence=e.verify_prior(stage);self.assertEqual(len(evidence),3)
                cases.assert_called_once_with(previous)
            pool.assert_not_called()

    def test_prior_stage_rejects_failed_or_changed_lock_without_performance(self):
        for stage in ('development','validation'):
            for mutation in ('failed','hash','candidate','source'):
                with self.subTest(stage=stage,mutation=mutation):self._prior(mutation,stage)

    def test_valid_locked_repair_reads_only_saved_cases(self):self._prior()

    def test_valid_prior_reaches_execution_boundary_without_running_a_case(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(e,'BUILD',Path(tmp)),\
                patch.object(e,'verify_freeze',return_value={'protocol':{'locked':True}}),\
                patch.object(e,'verify_prior',return_value={}) as prior,\
                patch.object(e,'tasks',return_value=[]),\
                patch.object(e,'ProcessPoolExecutor',side_effect=RuntimeError('execution boundary')) as pool:
            e.write_new(e.BUILD/'input-manifest.json',e.raw(dict(entries=[])))
            with self.assertRaisesRegex(RuntimeError,'execution boundary'):e.execute('development')
            prior.assert_called_once_with('development');pool.assert_called_once()


if __name__=='__main__':unittest.main()
