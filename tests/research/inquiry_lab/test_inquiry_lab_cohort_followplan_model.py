"""Synthetic CDF, immutable source, integer wave and continuation boundaries."""
from copy import deepcopy
import math
import unittest
from inquiry_lab import cohort_followplan_model as model
from test_inquiry_lab_cohort_hedge_model import owner as old_owner


def owner(candidates=6,horizon=300):
    o=old_owner(own_count=0,candidates=candidates,horizon=horizon)
    o.update(inquiries=[])
    return o


def jobs(o,count=1,wait=0):
    horizon=o['evaluation_end_at']-o['inputs']['now'];edges=model.H.cell_edges(horizon,5)
    return [dict(job_index=i,patient_id='OWNER',hospital=f'H{i}',
        source=dict(inquiry=dict(hospital=f'H{i}',patient='OWNER',submitted_at=1000)),
        owner_horizon_seconds=horizon,cell_edges_seconds=edges,travel_seconds=i*10,
        service_mu=math.log(30),service_sigma=.5,reply_probability=.8,accept_probability=.7,
        pass1_wait_seconds=wait,discount_seconds=0.,inquiry_cost_seconds=15.) for i in range(count)]


def run(o,js,review=1015):
    return model.score_first_patient(o,initial_jobs=js,all_jobs=js,row_pending=o['inputs']['pending']+[j['source']['inquiry'] for j in js],next_review_at=review)


class CohortFollowplanModelTests(unittest.TestCase):
    def test_source_immutable_and_hypothesis_has_no_fabricated_inquiry_or_version(self):
        o=owner();js=jobs(o,2);before=deepcopy((o,js));s=run(o,js)
        self.assertEqual((o,js),before);self.assertEqual(s['source_owner'],o)
        h=s['hypothetical_owner'];self.assertEqual(h['inquiries'],[])
        self.assertEqual(h['job']['version'],o['job']['version'])
        self.assertEqual(h['inputs']['pending'],o['inputs']['pending'])
        self.assertEqual(h['inputs']['max_new'],8)
        self.assertEqual(h['inputs']['last_contact_at'],1000)
        self.assertEqual(h['job']['next_evaluation_at'],1015)

    def test_initial_CDF_uses_pass1_wait_without_same_clock_half_job_refreeze(self):
        o=owner(candidates=1);js=jobs(o,1,wait=12)
        o['inputs']['pending']=[dict(hospital='H0',patient='OTHER',submitted_at=1000)]
        s=run(o,js);item=s['hypothetical_origin']['own_items'][0]
        self.assertEqual(item['wait_seconds'],12)
        self.assertEqual(s['source_origin']['own_items'],[])
        expected=.8*.7*model.H._lognorm_cdf(50-12,math.log(30),.5)
        self.assertAlmostEqual(model.original._descriptor_cdf(item,50),expected,14)

    def test_initial_four_integer_slots_block_future_until_strict_timeout_after180(self):
        o=owner(candidates=10);s=run(o,jobs(o,4))
        future=s['continuation'];self.assertEqual(future['barrier_offset_seconds'],15)
        self.assertTrue(all(r['total_fresh_count']<=4 for r in future['occupancy']))
        self.assertTrue(all(p['offset_seconds'] is None or p['offset_seconds']>=195 for p in future['plan']))
        self.assertEqual(len(s['hypothetical_origin']['original_all_own']),4)
        self.assertEqual(len(future['plan']),6)
        self.assertEqual([p['hospital'] for p in future['plan']],[f'H{i}' for i in range(4,10)])

    def test_non_grid_horizon_and_exact_public_nextreview_used(self):
        o=owner(candidates=2,horizon=17);o['job']['deadline_at']=1016
        s=run(o,jobs(o,1),review=1007);f=s['continuation']
        self.assertEqual(f['barrier_offset_seconds'],7)
        self.assertEqual(f['allowed_offsets_seconds'],[7])
        self.assertEqual(s['hypothetical_origin']['cell_edges_seconds'],[5,10,15,17])

    def test_zero_initial_no_pending_ignores_nextreview_like_actual_capture(self):
        o=owner(candidates=1);s=run(o,[],review=1090)
        self.assertEqual(s['continuation']['barrier_offset_seconds'],0)
        self.assertTrue(s['continuation']['forced'] or any(p['offset_seconds']==0 for p in s['continuation']['plan']))

    def test_no_remaining_candidates_exact_initial_cdf_and_fixed_contact_cost(self):
        o=owner(candidates=1);js=jobs(o,1);s=run(o,js)
        self.assertEqual(s['continuation']['planning_status'],'no_candidates')
        m=s['metrics'];self.assertEqual(m['immediate_contact_cost_seconds'],15.)
        self.assertEqual(m['planned_contact_cost_seconds'],0.)
        self.assertAlmostEqual(m['selection_probability'],.8*.7*model.H._lognorm_cdf(300,math.log(30),.5),14)
        self.assertEqual(m['total_cost_seconds'],m['arrival_failure_cost_seconds']+15.)

    def test_future_contact_cost_conditioned_once_on_original_time_initial_survival(self):
        o=owner(candidates=2);s=run(o,jobs(o,1),review=1007)
        planned=[p for p in s['continuation']['plan'] if p['offset_seconds'] is not None]
        self.assertEqual(len(planned),1);t=planned[0]['offset_seconds']
        item=s['hypothetical_origin']['own_items'][0]
        expected=15*(1-model.original._descriptor_cdf(item,t))
        self.assertAlmostEqual(s['metrics']['planned_contact_cost_seconds'],expected,13)

    def test_invalid_first_pending_job_horizon_and_source_fail_closed(self):
        o=owner();js=jobs(o,1)
        for edit in (lambda a,b:a['inquiries'].append({'id':1}),lambda a,b:a['inputs']['patient_pending'].append({}),
                     lambda a,b:b[0].update(owner_horizon_seconds=299),lambda a,b:b[0].update(patient_id='WRONG')):
            a,b=deepcopy((o,js));edit(a,b)
            with self.assertRaises(ValueError):run(a,b)
        with self.assertRaises(ValueError):run(o,js,review=999)


if __name__=='__main__':unittest.main()
