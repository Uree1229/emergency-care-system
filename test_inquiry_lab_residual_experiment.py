"""Metrics and fixed holdout protocol; no performance/heldout scenes here."""
import unittest
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
from copy import deepcopy
from inquiry_lab import residual_experiment as e


def row(pid,w,failed=False):
    return dict(id=pid,failed=failed,selection_seconds=None if failed else w,
        estimated_arrival_seconds=None if failed else w+300)


class ResidualExperimentTests(unittest.TestCase):
    def test_roles_have_disjoint_deterministic_128bit_seeds(self):
        sets=[set(e.seeds(role,n)) for role,n in [('development-mix',96),('validation-mix',320),
            ('development-stress',3),('validation-stress',6)]]
        self.assertEqual(sum(map(len,sets)),len(set.union(*sets)))
        self.assertEqual(len(set.union(*sets)),425)
        self.assertGreaterEqual(min(set.union(*sets)),1<<127)
        self.assertEqual(e.seeds('development-mix',4),e.seeds('development-mix',96)[:4])

    def test_fixed_arms_and_case_counts(self):
        self.assertEqual(len(e.ARMS),5)
        self.assertEqual(len(e.tasks('development')),120)
        self.assertEqual(len(e.tasks('validation')),368)
        self.assertEqual(len({(k,s) for k,s,_ in e.tasks('validation')}),368)
        self.assertFalse(e.SETTINGS['operational_adoption'])
        with self.assertRaises(ValueError):e.tasks('unknown')
        self.assertEqual(e.protocol(),json.loads(e.raw(e.protocol())))

    def test_all_patient_wait_retains_unsuccessful_observations(self):
        self.assertEqual(e.wait(row('P',None,True)),300)
        self.assertEqual(e.wait(row('P',40)),40)
        case={'branches':{'old':{'patients':[row('P',None,True)]},e.PRIMARY:{'patients':[row('P',40)]}}}
        result=e.paired([case],'old')
        self.assertEqual(result['wait_sum_delta_seconds'],-260)
        self.assertEqual(result['new_selections'],1)
        self.assertEqual(result['J_mean_delta_seconds'],-1460)

    def test_patient_weighting_and_input_immutability(self):
        cases=[{'branches':{'old':{'patients':[row('A',90)]},e.PRIMARY:{'patients':[row('A',30)]}}},
            {'branches':{'old':{'patients':[row(x,30) for x in 'BCD']},
                e.PRIMARY:{'patients':[row(x,30) for x in 'BCD']}}}]
        before=deepcopy(cases);result=e.paired(cases,'old',bootstrap=True)
        self.assertEqual(result['wait_mean_delta_seconds'],-15)
        self.assertEqual(result['wait_improved'],1)
        self.assertEqual(result['wait_same'],3)
        self.assertEqual(cases,before)
        self.assertEqual(result,e.paired(cases,'old',bootstrap=True))

    def test_faster_wait_does_not_hide_failure_or_J_increases(self):
        def metric(failed=3,J=700):
            return dict(failed=failed,J=J,inquiries_per_patient=4,review_work_seconds_per_patient=200)
        metrics={e.PRIMARY:metric(),e.INCUMBENT:metric(),e.ANCHOR:metric()}
        p=dict(wait_sum_delta_seconds=-20,ci95_wait=[-4,-1])
        with self.assertRaises(ValueError):e.assess(metrics,p,'unknown')
        self.assertTrue(e.assess(metrics,p,'development')['passed'])
        self.assertTrue(e.assess(metrics,p,'validation')['passed'])
        metrics[e.PRIMARY]['failed']=4
        self.assertFalse(e.assess(metrics,p,'development')['passed'])
        metrics[e.PRIMARY]=metric(J=701)
        self.assertFalse(e.assess(metrics,p,'development')['passed'])

    def test_validation_ci_and_strict_reduction_cannot_be_relaxed(self):
        m=dict(failed=0,J=600,inquiries_per_patient=3,review_work_seconds_per_patient=150)
        metrics={k:deepcopy(m) for k in (e.PRIMARY,e.INCUMBENT,e.ANCHOR)}
        p=dict(wait_sum_delta_seconds=-1,ci95_wait=[-2,1])
        self.assertTrue(e.assess(metrics,p,'development')['passed'])
        self.assertFalse(e.assess(metrics,p,'validation')['passed'])
        p['wait_sum_delta_seconds']=0;p['ci95_wait']=[0,0]
        self.assertFalse(e.assess(metrics,p,'development')['passed'])

    def test_old_sources_and_references_have_not_changed(self):
        self.assertEqual(len(e.verify_old()),64)

    def test_validation_rejects_failed_or_changed_lock_before_any_performance(self):
        for mutation in ('failed','hash','candidate','protocol','audit_source'):
            with self.subTest(mutation=mutation),tempfile.TemporaryDirectory() as tmp,\
                    patch.object(e,'BUILD',Path(tmp)),patch.object(e,'ProcessPoolExecutor') as pool,\
                    patch.object(e,'verify_saved_files') as saved:
                protocol={'source_sha256':{'f':'digest'}}
                dev=dict(assessment={'passed':mutation!='failed'},protocol=protocol)
                e.write_new(e.BUILD/'development.json',e.raw(dev));digest=e.sha(e.raw(dev))
                lock=dict(protocol=protocol,report_sha256='wrong' if mutation=='hash' else digest,
                    candidate='wrong' if mutation=='candidate' else e.PRIMARY,
                    assessment=dev['assessment'],validation_allowed=dev['assessment']['passed'])
                if mutation=='protocol':lock['protocol']={'changed':True}
                audit=dict(passed=True,report_sha256=digest,
                    source_sha256={} if mutation=='audit_source' else protocol['source_sha256'])
                e.write_new(e.BUILD/'development-lock.json',e.raw(lock))
                e.write_new(e.BUILD/'development-audit.json',e.raw(audit))
                with patch.object(e,'verify_freeze',return_value={'protocol':protocol}):
                    with self.assertRaises(AssertionError):e.execute('validation')
                pool.assert_not_called();saved.assert_not_called()
                self.assertFalse((e.BUILD/'validation-cases').exists())

    def test_only_valid_locked_development_reaches_the_execution_boundary(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(e,'BUILD',Path(tmp)),\
                patch.object(e,'ProcessPoolExecutor',side_effect=RuntimeError('execution boundary')) as pool,\
                patch.object(e,'verify_saved_files') as saved:
            protocol={'source_sha256':{'f':'digest'}};assessment={'passed':True}
            dev=dict(assessment=assessment,protocol=protocol)
            e.write_new(e.BUILD/'development.json',e.raw(dev));digest=e.sha(e.raw(dev))
            e.write_new(e.BUILD/'development-lock.json',e.raw(dict(protocol=protocol,report_sha256=digest,
                candidate=e.PRIMARY,assessment=assessment,validation_allowed=True)))
            e.write_new(e.BUILD/'development-audit.json',e.raw(dict(passed=True,report_sha256=digest,
                source_sha256=protocol['source_sha256'])))
            e.write_new(e.BUILD/'input-manifest.json',e.raw(dict(entries=[])))
            with patch.object(e,'verify_freeze',return_value={'protocol':protocol}):
                with self.assertRaisesRegex(RuntimeError,'execution boundary'):e.execute('validation')
            saved.assert_called_once_with(dev);pool.assert_called_once()


if __name__=='__main__':unittest.main()
