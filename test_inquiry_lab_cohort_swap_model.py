"""Synthetic H-metric preservation and atomic two-coordinate exchange units."""
from copy import deepcopy
import math
import unittest
from unittest.mock import patch
from inquiry_lab import horizon_policy as H, cohort_swap_model as m
from test_inquiry_lab_horizon_policy import inputs, context


def own_inputs(count=1, age=15, **changes):
    now=1015
    own=[dict(hospital='OWN'+str(i),patient='P',submitted_at=now-age,travel_seconds=300+i) for i in range(count)]
    value=inputs(now=now,pending=[{k:v for k,v in q.items() if k!='travel_seconds'} for q in own],patient_pending=own,
        last_contact_at=now-age,time_context=context(now=now))
    value.update(changes)
    return value


def shaped_plan():
    """Valid shape, deliberately not an assertion of original-H optimality."""
    args=own_inputs();proposal=H.decide(H.NAME,**args)
    for index,row in enumerate(proposal['evidence']['candidate_plan']):
        row['planned_offset_seconds']=0 if index<3 else 15 if index==3 else None
    proposal['send']=[row['hospital'] for row in proposal['evidence']['candidate_plan'] if row['planned_offset_seconds']==0]
    return args,proposal


def metric(wait=100.,probability=.8,arrival=1000.,contact=30.):
    return dict(selection_probability=probability,failure_probability=1-probability,capped_wait_seconds=wait,
        arrival_failure_cost_seconds=arrival,planned_contact_cost_seconds=contact,total_cost_seconds=arrival+contact)


class SwapModelTests(unittest.TestCase):
    def test_full_original_H_cost_and_pending_mass_are_reproduced(self):
        for args in (own_inputs(1),own_inputs(3),own_inputs(4),own_inputs(1,age=181)):
            proposal=H.decide(H.NAME,**args);before=deepcopy((args,proposal));model=m.build_model(args,proposal)
            value=m.metrics(model,model['original_plan'])
            self.assertLessEqual(abs(value['total_cost_seconds']-proposal['evidence']['planned_expected_cost']),.000500001)
            own_cost=H.bounded_expected_cost([(r['travel_seconds'],r['masses']) for r in model['own_items']],model['cell_edges_seconds'],1500)
            self.assertEqual(round(own_cost,3),proposal['evidence']['expected_cost_without_new'])
            self.assertEqual((args,proposal),before)
            self.assertEqual(model['horizon_source_sha256'],m.H_SOURCE_SHA256)

    def test_all_atomic_exchanges_have_complete_nine_trial_inventory_and_preserve_shape(self):
        args,proposal=shaped_plan();value=m.evaluate(args,proposal);trials=value['trials']
        self.assertEqual(len(trials),9)
        self.assertEqual([(r['outgoing_pool_index'],r['incoming_pool_index']) for r in trials],[(i,j) for i in range(3) for j in range(3,6)])
        base=value['model']['original_plan'];summary=m.plan_summary(base)
        for r in trials:
            self.assertEqual([h for h in base if r['plan'][h]!=base[h]],[r['outgoing_hospital'],r['incoming_hospital']])
            for key in ('offset_histogram','planned_count','immediate_count'):self.assertEqual(r['plan_summary'][key],summary[key])
            self.assertEqual(r['planned_hospital_set_preserved'],r['original_incoming_offset_seconds'] is not None)

    def test_no_immediate_contacts_has_zero_trials_and_copies_anchor(self):
        args=own_inputs(4);proposal=H.decide(H.NAME,**args);value=m.evaluate(args,proposal)
        self.assertEqual(proposal['send'],[]);self.assertEqual(value['trials'],[])
        self.assertIsNone(value['selection']['selected_trial_index'])
        self.assertEqual(value['selected_plan'],value['model']['original_plan'])

    def test_same_source_model_keeps_stale_pending_reply_mass_and_partial_H(self):
        args=own_inputs(1,age=181);proposal=H.decide(H.NAME,**args);model=m.build_model(args,proposal)
        self.assertEqual(len(model['own_items']),1);self.assertGreater(sum(model['own_items'][0]['masses']),0)
        args=own_inputs(now=1293,time_context=context(now=1293));proposal=H.decide(H.NAME,**args)
        self.assertEqual(m.build_model(args,proposal)['cell_edges_seconds'],[5,7])

    def test_probability_floor_and_strict_wait_boundary_use_existing_epsilons(self):
        base=metric();eps=m.PROBABILITY_EPSILON;weps=m.WAIT_EPSILON
        self.assertTrue(all(m.guard_metrics(base,metric(wait=90,probability=.8-eps)).values()))
        self.assertFalse(m.guard_metrics(base,metric(wait=90,probability=math.nextafter(.8-eps,-math.inf)))['selection_probability_nonregression'])
        self.assertFalse(m.guard_metrics(base,metric(wait=100-weps))['capped_wait_strict_improvement'])
        self.assertTrue(m.guard_metrics(base,metric(wait=math.nextafter(100-weps,-math.inf)))['capped_wait_strict_improvement'])

    def test_arrival_and_contact_guards_do_not_compensate_each_other(self):
        base=metric();eps=m.WAIT_EPSILON
        self.assertTrue(all(m.guard_metrics(base,metric(wait=90,arrival=1000+eps,contact=30+eps)).values()))
        self.assertFalse(m.guard_metrics(base,metric(wait=90,arrival=math.nextafter(1000+eps,math.inf),contact=0))['arrival_cost_nonregression'])
        self.assertFalse(m.guard_metrics(base,metric(wait=90,arrival=0,contact=math.nextafter(30+eps,math.inf)))['planned_contact_cost_nonregression'])

    def test_selection_uses_minimum_wait_band_then_cost_band_then_native_pair_order(self):
        trials=[dict(trial_index=0,admitted=True,metrics=metric(wait=90+m.WAIT_EPSILON/2,arrival=900)),
            dict(trial_index=1,admitted=True,metrics=metric(wait=90,arrival=901)),
            dict(trial_index=2,admitted=True,metrics=metric(wait=90+2*m.WAIT_EPSILON,arrival=1))]
        value=m.choose(trials)
        self.assertEqual(value['wait_band_trial_indices'],[0,1]);self.assertEqual(value['selected_trial_index'],0)
        trials[1]['metrics']=deepcopy(trials[0]['metrics']);self.assertEqual(m.choose(trials)['cost_band_trial_indices'],[0,1])
        self.assertEqual(m.choose(trials)['selected_trial_index'],0)
        for t in trials:t['admitted']=False
        self.assertTrue(m.choose(trials)['baseline_fallback'])

    def test_only_one_exchange_is_selected_without_iterative_replanning(self):
        args,proposal=shaped_plan();base=metric();others=[metric(wait=90)]*9
        with patch.object(m,'metrics',side_effect=[base]+others) as scores:
            value=m.evaluate(args,proposal)
        self.assertEqual(scores.call_count,10);self.assertTrue(value['one_exchange_only'])
        self.assertEqual(value['selection']['selected_trial_index'],0)
        self.assertEqual(value['selected_plan'],value['trials'][0]['plan'])

    def test_forecast_future_cap_relaxation_is_not_silently_repaired(self):
        args=own_inputs(3);proposal=H.decide(H.NAME,**args)
        for i,row in enumerate(proposal['evidence']['candidate_plan']):row['planned_offset_seconds']=0 if i==0 else 15 if i==1 else None
        proposal['send']=[proposal['evidence']['candidate_plan'][0]['hospital']]
        value=m.evaluate(args,proposal)
        self.assertTrue(value['original_future_cap_relaxation_unchanged'])
        self.assertEqual(sum(offset is not None and offset<=15 for offset in value['model']['original_plan'].values())+3,5)
        self.assertEqual(len(value['trials']),5)

    def test_future_contact_cost_uses_the_same_unconditional_no_accept_survival(self):
        args=own_inputs();proposal=H.decide(H.NAME,**args);model=m.build_model(args,proposal)
        plan={h:None for h in model['original_plan']};hospital=next(iter(plan));plan[hospital]=15
        value=m.metrics(model,plan)
        self.assertGreater(value['planned_contact_cost_seconds'],0)
        self.assertLess(value['planned_contact_cost_seconds'],15)
        plan[hospital]=0;self.assertEqual(m.metrics(model,plan)['planned_contact_cost_seconds'],15)

    def test_original_pool_order_and_immediate_source_mismatch_fail_closed(self):
        args,proposal=shaped_plan();proposal['send']=[]
        with self.assertRaises(ValueError):m.build_model(args,proposal)
        args,proposal=shaped_plan();proposal['evidence']['candidate_plan'].reverse()
        with self.assertRaises(ValueError):m.build_model(args,proposal)


if __name__=='__main__':unittest.main()
