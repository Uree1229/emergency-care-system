"""Metrics and fixed holdout protocol; no performance/heldout scenes here."""
import unittest
import json
from copy import deepcopy
from inquiry_lab import shortlist_experiment as e


def row(pid,w,failed=False):
    return dict(id=pid,failed=failed,selection_seconds=None if failed else w,
        estimated_arrival_seconds=None if failed else w+300)


class ShortlistExperimentTests(unittest.TestCase):
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


if __name__=='__main__':unittest.main()
