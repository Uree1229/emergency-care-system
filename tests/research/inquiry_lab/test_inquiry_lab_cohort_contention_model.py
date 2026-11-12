"""Synthetic chronological expected work, fixed F0 and cost arithmetic."""
from copy import deepcopy
import math
import unittest
from inquiry_lab import cohort_contention_model as model


def initial(hospital='OLD',probability=.5,wait=0,horizon=100):
    x=dict(hospital=hospital,travel_seconds=10,service_mu=math.log(10),service_sigma=1.,
        total_probability=probability,mode='queued',wait_seconds=wait,spent_seconds=0.,f0=0.)
    edges=model.H.cell_edges(horizon,5)
    x['masses']=model.H.masses_at_edges(lambda t:model.original.original._descriptor_cdf(x,t),1.,edges)
    return x


def first(pid,plans,*,items=(),horizon=100,probability=.4,mean=10.,workers=1):
    """Minimal frozen-parent row parts; no committed DB/source fabrication."""
    queue=[];plan=[]
    for h,s,q in plans:
        queue.append(dict(hospital=h,estimated_wait_seconds=q,service_mu=math.log(10),service_sigma=1.,
            reply_probability=1.,accept_probability=probability,mean_service_seconds=mean,assumed_workers=workers,
            travel_seconds=20,inquiry_cost_seconds=15.))
        plan.append(dict(hospital=h,offset_seconds=s))
    return dict(patient_id=pid,effective_priority=4,source_owner=dict(inputs=dict(now=1000),evaluation_end_at=1000+horizon),
        hypothetical_origin=dict(cell_edges_seconds=model.H.cell_edges(horizon,5),own_items=deepcopy(list(items))),
        continuation=dict(queue_records=queue,plan=plan),initial_contact_cost_seconds=15.*len(items))


class CohortContentionModelTests(unittest.TestCase):
    def test_chronological_then_first_patient_then_candidate_order_not_candidate_priority(self):
        ps=[first('A',[('X',30,0.),('Y',15,0.)]),first('B',[('X',15,0.)])]
        result=model.score_first_cohort(ps)
        self.assertEqual([(j['patient_id'],j['hospital'],j['send_offset_seconds']) for j in result['jobs']],
            [('A','Y',15),('B','X',15),('A','X',30)])
        self.assertEqual([x['hospital'] for x in result['patient_scores'][0]['future_items']],['X','Y'])

    def test_first_future_job_zero_queue_still_uses_initial_survival_at_send(self):
        ps=[first('A',[('X',15,0.)],items=[initial()]),first('B',[('X',15,3.)])]
        before=deepcopy(ps);r=model.score_first_cohort(ps);a,b=r['jobs']
        expected=10*(1-model.original.original._descriptor_cdf(ps[0]['hypothetical_origin']['own_items'][0],15))
        self.assertEqual(a['corrected_wait_seconds'],0.)
        self.assertEqual(a['probe_offset_seconds'],15.)
        self.assertAlmostEqual(a['expected_work_seconds'],expected,14)
        self.assertAlmostEqual(b['corrected_wait_seconds'],3+expected,14)
        self.assertEqual(ps,before)

    def test_cross_hospital_F0_is_frozen_and_not_corrected_F1_feedback(self):
        ps=[first('O',[('X',10,0.)]),first('A',[('X',15,0.),('Y',15,10.)]),first('B',[('Y',15,3.)])]
        r=model.score_first_cohort(ps);o,ax,ay,b=r['jobs']
        self.assertEqual(ax['corrected_wait_seconds'],10.)
        self.assertEqual(ay['fixed_F0_survival_factors'],[dict(kind='future',future_job_index=1,hospital='X',selection_probability_at_probe=.2)])
        self.assertAlmostEqual(ay['expected_work_seconds'],8.,14)
        self.assertAlmostEqual(b['corrected_wait_seconds'],11.,14)
        self.assertEqual(o['fixed_F0_survival_factors'],[])

    def test_original_queue_not_aged_across_waves_and_no_H_truncation_of_work(self):
        ps=[first('A',[('X',0,0.)],horizon=300),first('B',[('X',195,5.)],horizon=300)]
        a,b=model.score_first_cohort(ps)['jobs']
        self.assertEqual(b['base_wait_seconds'],5.)
        self.assertEqual(b['corrected_wait_seconds'],15.)
        self.assertEqual(b['expected_work_seconds'],10.)
        self.assertEqual(b['probe_offset_seconds'],210.)

    def test_probe_clamped_to_H_and_mean_work_divided_by_workers(self):
        ps=[first('A',[('X',15,10.)],items=[initial(horizon=17)],horizon=17,mean=20,workers=2)]
        j=model.score_first_cohort(ps)['jobs'][0]
        self.assertEqual(j['probe_offset_seconds'],17)
        self.assertEqual(j['full_mean_work_seconds'],10.)
        expected=10*(1-model.original.original._descriptor_cdf(ps[0]['hypothetical_origin']['own_items'][0],17))
        self.assertAlmostEqual(j['expected_work_seconds'],expected,14)

    def test_selection_probability_uses_CDF_once_not_multiplied_by_expected_work_survival(self):
        ps=[first('A',[('X',15,0.)],items=[initial()])]
        r=model.score_first_cohort(ps);m=r['patient_scores'][0]['metrics'];j=r['jobs'][0]
        pi=model.original.original._descriptor_cdf(ps[0]['hypothetical_origin']['own_items'][0],100)
        pf=model._future_cdf(j,100,'corrected_wait_seconds')
        self.assertAlmostEqual(m['selection_probability'],1-(1-pi)*(1-pf),14)
        self.assertLess(j['no_prior_accept_probability'],1.)
        bad=1-(1-pi)*(1-pf*j['no_prior_accept_probability'])
        self.assertGreater(m['selection_probability'],bad)

    def test_contact_cost_uses_updated_own_F1_survival_at_future_send(self):
        ps=[first('O',[('X',0,0.)]),first('A',[('X',0,0.),('Y',15,0.)])]
        r=model.score_first_cohort(ps);a=r['patient_scores'][1];ax=next(j for j in r['jobs'] if j['patient_id']=='A' and j['hospital']=='X')
        expected=15+15*(1-model._future_cdf(ax,15,'corrected_wait_seconds'))
        self.assertAlmostEqual(a['future_contact_cost_seconds'],expected,14)
        self.assertEqual(a['metrics']['immediate_contact_cost_seconds'],0.)
        self.assertEqual(a['metrics']['total_cost_seconds'],a['metrics']['arrival_failure_cost_seconds']+expected)

    def test_empty_plan_preserves_initial_distribution_cost_and_source(self):
        ps=[first('A',[('X',None,0.)],items=[initial()])];before=deepcopy(ps);r=model.score_first_cohort(ps)
        self.assertEqual(r['jobs'],[]);p=r['patient_scores'][0]
        self.assertEqual(p['fixed_initial_items'],ps[0]['hypothetical_origin']['own_items'])
        self.assertEqual(p['future_items'],[]);self.assertEqual(p['future_contact_cost_seconds'],0.)
        self.assertEqual(p['metrics']['immediate_contact_cost_seconds'],15.)
        self.assertEqual(ps,before)

    def test_no_other_patient_future_work_preserves_full_parent_metric_exactly(self):
        from test_inquiry_lab_cohort_followplan_policy import fixture,invoke
        rows=invoke(fixture())['evidence']['row_scores']
        for row in rows:
            for parent in row['first_cohort_future']:
                result=model.score_first_cohort([parent])
                self.assertTrue(all(j['corrected_wait_seconds']==j['base_wait_seconds'] for j in result['jobs']))
                self.assertEqual(result['patient_scores'][0]['metrics'],parent['metrics'])

    def test_all_future_plan_times_and_memberships_are_exact_parent_copies(self):
        ps=[first('A',[('X',15,0.),('Y',45,0.),('Z',None,0.)],horizon=300),first('B',[('X',195,3.)],horizon=300)]
        r=model.score_first_cohort(ps)
        self.assertEqual([(j['patient_id'],j['hospital'],j['send_offset_seconds']) for j in r['jobs']],
            [('A','X',15),('A','Y',45),('B','X',195)])
        for j in r['jobs']:
            source=ps[j['first_patient_index']]
            self.assertEqual(j['source_plan'],source['continuation']['plan'][j['plan_candidate_index']])
            self.assertEqual(j['source_queue_record'],source['continuation']['queue_records'][j['parent_queue_record_index']])

    def test_future_jobs_after_H_and_duplicate_first_patients_rejected(self):
        with self.assertRaises(ValueError):model.score_first_cohort([first('A',[('X',100,0.)])])
        with self.assertRaises(ValueError):model.score_first_cohort([first('A',[]),first('A',[])])


if __name__=='__main__':unittest.main()
