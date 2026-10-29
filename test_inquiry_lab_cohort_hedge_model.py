"""Pure synthetic origin CDFs, exact shifted costs and every-wave occupancy."""
from copy import deepcopy
import math
import unittest
from inquiry_lab import cohort_hedge_model as model


def owner(*,own_count=1,age=30,horizon=300,barrier=15,candidates=2):
    now=1000
    pending=[dict(hospital=f'OLD{i}',patient='OWNER',submitted_at=now-age) for i in range(own_count)]
    own=[dict(q,travel_seconds=30+i) for i,q in enumerate(pending)]
    return dict(patient=dict(id='OWNER',effective_priority=4),evaluation_end_at=now+horizon,
        job=dict(patient_id='OWNER',version=1,started_at=now,deadline_at=now+300,next_evaluation_at=now+barrier,enabled=1,policy_name=model.NAME),
        valid_acceptance_ids=[],inputs=dict(now=now,candidates=[dict(hospital_id=f'H{i}',travel={'duration_seconds':i*10}) for i in range(candidates)],
            observations=[],pending=pending,patient_pending=own,max_new=10-own_count,last_contact_at=now-age if own_count else None))


class CohortHedgeModelTests(unittest.TestCase):
    def test_own_CDF_matches_origin_conditional_formula(self):
        o=owner();fixed=model.freeze_owner(o);x=fixed['own_items'][0]
        cfg=model.H.spec()['config'];belief=model.H.Beliefs([],1000,cfg)
        f0=model.H._lognorm_cdf(30,belief.mu('OLD0'),belief.sigma)
        reply_left=belief.reply('OLD0')*(1-f0)/(1-belief.reply('OLD0')*f0)
        expected=reply_left*belief.accept('OLD0')*(model.H._lognorm_cdf(50,belief.mu('OLD0'),belief.sigma)-f0)/(1-f0)
        self.assertAlmostEqual(model._descriptor_cdf(x,20),expected,14)
        self.assertEqual(fixed['cell_edges_seconds'][-1],300)

    def test_virtual_same_second_follower_never_changes_original_own_CDF(self):
        o=owner(age=0);fixed=model.freeze_owner(o);before=deepcopy(fixed)
        extra=dict(hospital='OLD0',patient='LATER',submitted_at=1000)
        model.score_owner(o,fixed,o['inputs']['pending']+[extra])
        self.assertEqual(fixed,before)
        # Re-freezing the changed prefix would add the legacy half-job delay;
        # the production path deliberately never does that.
        other=deepcopy(o);other['inputs']['pending'].append(extra)
        self.assertNotEqual(model.freeze_owner(other)['own_items'],fixed['own_items'])

    def test_row_new_queue_changes_future_score_not_own_distribution(self):
        o=owner(candidates=1);fixed=model.freeze_owner(o)
        clear=model.score_owner(o,fixed,o['inputs']['pending'])
        loaded=model.score_owner(o,fixed,o['inputs']['pending']+[dict(hospital='H0',patient='FIRST',submitted_at=1000)])
        self.assertGreater(loaded['queue_records'][0]['estimated_wait_seconds'],clear['queue_records'][0]['estimated_wait_seconds'])
        self.assertNotEqual(loaded['metrics']['selection_probability'],clear['metrics']['selection_probability'])
        self.assertEqual(fixed,model.freeze_owner(o))

    def test_non_grid_barrier_partial_H_and_analytic_contact_survival(self):
        o=owner(age=181,horizon=17,barrier=7,candidates=1);f=model.freeze_owner(o);s=model.score_owner(o,f,o['inputs']['pending'])
        self.assertEqual(s['barrier_offset_seconds'],7);self.assertEqual(s['allowed_offsets_seconds'],[7])
        self.assertEqual(f['cell_edges_seconds'],[5,10,15,17])
        selected=[p for p in s['plan'] if p['offset_seconds'] is not None]
        self.assertEqual(len(selected),1);t=selected[0]['offset_seconds']
        expected=15*math.prod(1-model._descriptor_cdf(x,t) for x in f['own_items'])
        self.assertAlmostEqual(s['metrics']['planned_contact_cost_seconds'],expected,13)
        self.assertEqual(t,7)

    def test_every_wave_capacity_includes_original_and_all_earlier_plans(self):
        all_own=[dict(submitted_at=1000) for _ in range(4)]
        row=model.plan_occupancy({'H0':180},all_own,1000)[0]
        self.assertEqual(row['total_fresh_count'],5)
        self.assertEqual(model.plan_occupancy({'H0':181},all_own,1000)[0]['total_fresh_count'],1)
        rows=model.plan_occupancy({'A':0,'B':0,'C':0,'D':15,'E':30},[],1000)
        self.assertEqual(rows[-1]['total_fresh_count'],5)
        o=owner(own_count=4,age=0,barrier=15,candidates=6);f=model.freeze_owner(o);s=model.score_owner(o,f,o['inputs']['pending'])
        self.assertTrue(all(r['total_fresh_count']<=4 for r in s['occupancy']))
        self.assertTrue(all(p['offset_seconds'] is None or p['offset_seconds']>180 for p in s['plan']))

    def test_full_occupancy_uses_own_without_eligible_travel(self):
        o=owner(own_count=4,age=0,candidates=1);o['inputs']['patient_pending']=[]
        f=model.freeze_owner(o);self.assertEqual(len(f['original_all_own']),4)
        s=model.score_owner(o,f,o['inputs']['pending'])
        self.assertTrue(all(p['offset_seconds'] is None or p['offset_seconds']>180 for p in s['plan']))

    def test_pending_zero_ignores_next_eval_but_honors_start_and_force_one(self):
        o=owner(own_count=0,barrier=90,candidates=1);f=model.freeze_owner(o);s=model.score_owner(o,f,[])
        self.assertEqual(s['barrier_offset_seconds'],0)
        self.assertEqual(s['plan'][0]['offset_seconds'],0)
        o['job']['started_at']=1007;f=model.freeze_owner(o);s=model.score_owner(o,f,[])
        self.assertEqual(s['barrier_offset_seconds'],7)
        self.assertEqual(s['plan'][0]['offset_seconds'],7)

    def test_no_contact_after_A_but_existing_CDF_retains_H(self):
        o=owner(horizon=100,barrier=7);o['job']['deadline_at']=1007
        f=model.freeze_owner(o);s=model.score_owner(o,f,o['inputs']['pending'])
        self.assertEqual(s['planning_status'],'barrier_outside_new_contact_window')
        self.assertGreater(s['metrics']['selection_probability'],0)
        self.assertEqual(s['allowed_offsets_seconds'],[])

    def test_disabled_missing_unsupported_poolzero_and_accepted_modes(self):
        variants=[]
        for reason,edit in [('no_job',lambda o:o.update(job=None)),('disabled_job',lambda o:o['job'].update(enabled=0)),
            ('unsupported_policy',lambda o:o['job'].update(policy_name='fixed_3')),('closed_new_contact_window',lambda o:o['job'].update(deadline_at=1000)),
            ('contact_budget_exhausted',lambda o:o['inputs'].update(max_new=0)),('no_candidates',lambda o:o['inputs'].update(candidates=[]))]:
            o=owner();edit(o);f=model.freeze_owner(o);s=model.score_owner(o,f,o['inputs']['pending'])
            self.assertEqual(s['planning_status'],reason);self.assertEqual(s['metrics']['planned_contact_cost_seconds'],0)
            variants.append(s['metrics'])
        self.assertTrue(all(x==variants[0] for x in variants))
        o=owner();o['valid_acceptance_ids']=[1];s=model.score_owner(o,model.freeze_owner(o),o['inputs']['pending'])
        self.assertEqual(s['metrics']['selection_probability'],1);self.assertEqual(s['metrics']['capped_wait_seconds'],0)

    def test_partial_one_to_fourteen_H_and_strict_endpoint(self):
        for horizon in range(1,15):
            o=owner(own_count=0,horizon=horizon,candidates=1);f=model.freeze_owner(o);s=model.score_owner(o,f,[])
            self.assertEqual(f['cell_edges_seconds'][-1],horizon)
            self.assertTrue(all(t<horizon for t in s['allowed_offsets_seconds']))
            self.assertTrue(0<=s['metrics']['capped_wait_seconds']<=horizon)

    def test_origin_shift_and_input_nonmutation(self):
        o=owner();before=deepcopy(o);f=model.freeze_owner(o);s=model.score_owner(o,f,o['inputs']['pending'])
        shifted=deepcopy(o);d=5000
        shifted['inputs']['now']+=d;shifted['evaluation_end_at']+=d;shifted['inputs']['last_contact_at']+=d
        for q in shifted['inputs']['pending']+shifted['inputs']['patient_pending']:q['submitted_at']+=d
        for k in ['started_at','deadline_at','next_evaluation_at']:shifted['job'][k]+=d
        t=model.score_owner(shifted,model.freeze_owner(shifted),shifted['inputs']['pending'])
        self.assertEqual(s,t);self.assertEqual(o,before)

    def test_source_prefix_and_patient_mismatch_fail_closed(self):
        o=owner();f=model.freeze_owner(o)
        with self.assertRaises(ValueError):model.score_owner(o,f,[])
        with self.assertRaises(ValueError):model.score_owner(o,f,o['inputs']['pending']+[dict(hospital='H0',patient='X',submitted_at=1001)])
        bad=deepcopy(f);bad['patient_id']='X'
        with self.assertRaises(ValueError):model.score_owner(o,bad,o['inputs']['pending'])


if __name__=='__main__':unittest.main()
