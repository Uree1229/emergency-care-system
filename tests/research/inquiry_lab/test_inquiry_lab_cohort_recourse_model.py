"""Pure fixed-F0 arithmetic, fractional-work boundaries and inherited H plans."""
from copy import deepcopy
import math
import unittest
from unittest.mock import patch
from inquiry_lab import cohort_recourse_model as model
from test_inquiry_lab_cohort_hedge_model import owner
from test_inquiry_lab_cohort_recourse_policy import fixture,invoke


def job(index,patient,hospital,q0,*,horizon=17,mean=10):
    return dict(job_index=index,patient_id=patient,hospital=hospital,pass0_wait_seconds=q0,
        owner_horizon_seconds=horizon,reply_probability=.8,accept_probability=.7,
        service_mu=math.log(3),service_sigma=.5,full_mean_work_seconds=mean)


class CohortRecourseModelTests(unittest.TestCase):
    def test_forward_q1_precedes_fixed_F0_survival_no_feedback(self):
        rows=[job(0,'A','X',0),job(1,'A','Y',10),job(2,'B','Z',0),job(3,'B','Y',20)]
        before=deepcopy(rows);result=model._passes(rows)
        expected_u=1-model._f0(rows[0],10)
        self.assertEqual(result[0]['survival_probability'],1)
        self.assertAlmostEqual(result[1]['expected_work_seconds'],10*expected_u,14)
        expected_q1=20-(10-10*expected_u)
        self.assertEqual(result[3]['pass1_wait_seconds'],expected_q1)
        self.assertAlmostEqual(result[3]['survival_probability'],1-model._f0(rows[2],min(expected_q1,17)),14)
        self.assertEqual(rows,before)

    def test_own_hospital_excluded_other_hospital_and_H_clamp_exact(self):
        rows=[job(0,'A','X',12,horizon=7),job(1,'A','Y',0,horizon=7),job(2,'B','X',22)]
        result=model._passes(rows)
        self.assertEqual(result[0]['cancellation_probe_seconds'],7)
        self.assertEqual(result[0]['other_hospital_probabilities'],[dict(job_index=1,selection_probability_at_probe=model._f0(rows[1],7))])
        self.assertEqual(result[2]['other_hospital_probabilities'],[])
        self.assertEqual(result[2]['survival_probability'],1)
        self.assertGreaterEqual(result[2]['pass1_wait_seconds'],0)
        self.assertLess(result[2]['pass1_wait_seconds'],22)

    def test_all_unit_survival_preserves_every_first_metric_exactly(self):
        args=fixture(extras=0)
        with patch.object(model,'_f0',return_value=0.0):result=invoke(args)
        for row in result['evidence']['row_scores']:
            self.assertEqual(row['first_cohort']['member_metrics'],row['base_row_score']['first_cohort_score']['member_metrics'])
            self.assertTrue(all(j['discount_seconds']==0 and j['pass1_wait_seconds']==j['pass0_wait_seconds'] for j in row['first_cohort']['jobs']))

    def test_owner_discount_only_virtual_work_preserves_origin_and_full_queue(self):
        o=owner(candidates=1);fixed=model.freeze_owner(o);before=deepcopy((o,fixed))
        inquiry=dict(hospital='H0',patient='FIRST',submitted_at=1000);ledger=o['inputs']['pending']+[inquiry]
        jobs=[dict(hospital='H0',source=dict(inquiry=inquiry),discount_seconds=5.0)]
        new=model.score_owner(o,fixed,ledger,jobs);old=model.foundation.score_owner(o,fixed,ledger)
        nr,orr=new['queue_records'][0],old['queue_records'][0]
        self.assertEqual(nr['full_wait_before_discount_seconds'],orr['estimated_wait_seconds'])
        self.assertEqual(nr['virtual_discount_seconds'],5.0)
        self.assertEqual(nr['estimated_wait_seconds'],max(0,orr['estimated_wait_seconds']-5))
        for key in ('head','head_expected_remaining_seconds','age_seconds','queue_count','original_full_wait_seconds'):
            self.assertEqual(nr[key],orr[key])
        self.assertEqual((o,fixed),before)
        self.assertTrue(all(r['total_fresh_count']<=4 for r in new['occupancy']))

    def test_zero_discount_owner_metrics_and_plan_exactly_Hedge(self):
        o=owner(own_count=4,age=0,candidates=6);f=model.freeze_owner(o)
        new=model.score_owner(o,f,o['inputs']['pending'],[]);old=model.foundation.score_owner(o,f,o['inputs']['pending'])
        for key in ('metrics','plan','occupancy','allowed_offsets_seconds','passes','forced','planning_status'):
            self.assertEqual(new[key],old[key])
        self.assertTrue(all(x['offset_seconds'] is None or x['offset_seconds']>180 for x in new['plan']))

    def test_owner_wrong_virtual_source_fails_closed(self):
        o=owner();f=model.freeze_owner(o)
        with self.assertRaises(ValueError):model.score_owner(o,f,o['inputs']['pending'],[dict(source=dict(inquiry={}))])

    def test_non_grid_H_A_and_nojob_accepted_modes_unchanged(self):
        o=owner(age=181,horizon=17,barrier=7,candidates=1);f=model.freeze_owner(o)
        s=model.score_owner(o,f,o['inputs']['pending'],[])
        self.assertEqual(s['allowed_offsets_seconds'],[7]);self.assertEqual(f['cell_edges_seconds'][-1],17)
        o['job']=None;s=model.score_owner(o,f,o['inputs']['pending'],[])
        self.assertEqual(s['planning_status'],'no_job')
        o['valid_acceptance_ids']=[1];s=model.score_owner(o,f,o['inputs']['pending'],[])
        self.assertEqual(s['metrics']['selection_probability'],1.0);self.assertEqual(s['metrics']['capped_wait_seconds'],0.0)


if __name__=='__main__':unittest.main()
