"""Unchanged Tailwait mathematics and old-review clipping, synthetic inputs only."""
from copy import deepcopy
from functools import lru_cache
import unittest
from unittest.mock import patch
from inquiry_lab import cohort_travelwake_policy as policy
from test_inquiry_lab_cohort_tailwait_policy import fixture as old_fixture,invoke as old_invoke,promote
from test_inquiry_lab_horizon_policy import inputs
from test_inquiry_lab_cohort_travelwake_workflow import DBFixture,offers
from server import inquiry_observations


@lru_cache(maxsize=3)
def _source(kind='inherited'):
    args,_=old_fixture(kind);return args[:5]+(old_invoke(args),)


def fixture(kind='inherited'):
    args=promote(_source(kind),policy.BASE_POLICY,policy.NAME)
    return args,args[-1]['evidence']['stoploss_anchor_index']


def invoke(args):
    j,ms,ts,p,owners,parent=args
    with patch.object(policy.original,'decide_joint',return_value=deepcopy(parent)):
        return policy.decide_joint(policy.NAME,members=ms,joint_context=j,tail_members=ts,priority_context=p,owner_context=owners)


class TravelwakePolicyTests(unittest.TestCase):
    def test_only_spec_identity_changes_and_fixed_name_required(self):
        a=policy.original.spec();b=policy.spec()
        for key in ('name','label'):a.pop(key);b.pop(key)
        self.assertEqual(a,b)
        with self.assertRaises(ValueError):policy.spec(policy.BASE_POLICY)

    def test_initial_groups_inherited_new_and_one_tail_preserve_full_Tailwait_actions(self):
        for kind in ('inherited','new','one_tail'):
            args,_=fixture(kind);before=deepcopy(args);result=invoke(args)
            self.assertEqual(args,before);self.assertEqual(result['evidence']['original_tailwait_proposal'],args[-1])
            for value,parent in zip(result['decisions'],args[-1]['decisions']):
                self.assertFalse(value['evidence'].pop('cohort_travelwake')['triggered'])
                value['evidence']['policy']=policy.BASE_POLICY;self.assertEqual(value,parent)

    def test_untriggered_H_fallback_is_exact_parent_including_no_candidate_and_budget(self):
        for args in (inputs(),inputs(max_new=0),inputs(candidates=[])):
            parent=policy.original.decide(policy.BASE_POLICY,**deepcopy(args));result=policy.decide(policy.NAME,**deepcopy(args))
            proof=result['evidence'].pop('cohort_travelwake');self.assertFalse(proof['triggered'])
            self.assertEqual(proof['original_tailwait_proposal'],parent)
            result['evidence']['policy']=policy.BASE_POLICY;self.assertEqual(result,parent)


class TriggeredMathTests(DBFixture):
    def test_trigger_keeps_genuine_H_math_but_filters_extra_send_and_keeps_review_clip(self):
        from inquiry_lab import cohort_travelwake_workflow as w
        self.seed();self.decline();snapshot,_,wake=w._capture(self.conn,1005,'P')
        temporal,_=w._time_context(self.conn,1005,snapshot,w._job(self.conn,'P'))
        ledger=inquiry_observations.capture(self.conn,1005,'P');travel={row['hospital_id']:row['travel']['duration_seconds'] for row in offers()['candidates']}
        args=dict(now=1005,candidates=offers()['candidates'][3:],observations=ledger['observations'],pending=ledger['pending'],
            patient_pending=[dict(q,travel_seconds=travel[q['hospital']]) for q in ledger['patient_pending']],max_new=7,last_contact_at=1000,time_context=temporal)
        parent=policy.original.decide(policy.BASE_POLICY,**deepcopy(args));before=deepcopy((args,wake))
        result=policy.decide(policy.NAME,**args,wake_context=wake)
        self.assertEqual((args,wake),before);proof=result['evidence'].pop('cohort_travelwake')
        self.assertTrue(proof['triggered']);self.assertEqual(proof['original_tailwait_proposal'],parent)
        pre=deepcopy(proof['original_declinewake_proposal']);pre['evidence'].pop('cohort_travelwake')
        pre['evidence']['policy']=policy.BASE_POLICY;pre['next_review_at']=parent['next_review_at']
        self.assertEqual(pre,parent)
        self.assertEqual(result['send'],proof['travel_guard']['allowed_sends'])
        self.assertEqual(result['evidence']['sent_hospitals'],result['send'])
        result['send']=parent['send'];result['evidence']['sent_hospitals']=parent['evidence']['sent_hospitals']
        self.assertEqual(result['next_review_at'],1015);result['next_review_at']=parent['next_review_at'];result['evidence']['policy']=policy.BASE_POLICY
        self.assertEqual(result,parent)

    def test_changed_context_prefix_or_identity_is_rejected(self):
        from inquiry_lab import cohort_travelwake_workflow as w
        self.seed();self.decline();snapshot,_,wake=w._capture(self.conn,1005,'P');temporal,_=w._time_context(self.conn,1005,snapshot,w._job(self.conn,'P'))
        args=inputs(now=1005,time_context=temporal)
        for changed in ('prefix','patient'):
            bad=deepcopy(wake)
            if changed=='prefix':bad['event_prefix_max']+=1
            else:bad['patient_id']='OTHER'
            with self.assertRaises(ValueError):policy.decide(policy.NAME,**args,wake_context=bad)


class PublicTravelGuardTests(unittest.TestCase):
    def call(self,*,new=(50,100,101),own=(100,),ages=None,missing=()):
        ages=[10]*len(own) if ages is None else ages
        pending=[dict(hospital='OLD'+str(i),patient='P',submitted_at=1000-age) for i,age in enumerate(ages)]
        captured=[dict(row,travel_seconds=travel) for i,(row,travel) in enumerate(zip(pending,own)) if i not in missing]
        candidates=[dict(hospital_id='NEW'+str(i),travel=dict(duration_seconds=travel)) for i,travel in enumerate(new)]
        args=dict(now=1000,patient_id='P',candidates=candidates,pending=pending,patient_pending=captured,
                  proposed_sends=[row['hospital_id'] for row in candidates])
        before=deepcopy(args);result=policy.travel_guard(**args);self.assertEqual(args,before)
        return result

    def test_nearer_and_equal_allowed_farther_rejected_in_parent_order(self):
        guard=self.call()
        self.assertEqual(guard['allowed_sends'],['NEW0','NEW1']);self.assertEqual(guard['rejected_hospital_ids'],['NEW2'])
        self.assertEqual(guard['minimum_fresh_pending_travel_seconds'],100)

    def test_multiple_own_pending_uses_minimum_not_maximum(self):
        guard=self.call(new=(90,100,150),own=(100,200))
        self.assertEqual(guard['allowed_sends'],['NEW0','NEW1'])

    def test_age180_included181_excluded(self):
        self.assertEqual(self.call(new=(101,),ages=[180])['allowed_sends'],[])
        self.assertEqual(self.call(new=(101,),ages=[181])['allowed_sends'],['NEW0'])

    def test_missing_own_travel_from_public_full_pending_blocks_extra_send(self):
        guard=self.call(new=(1,),own=(100,200),missing=(0,))
        self.assertTrue(guard['unknown_fresh_pending_travel']);self.assertEqual(guard['allowed_sends'],[])

    def test_unknown_stale_own_travel_does_not_invent_a_fresh_reference(self):
        guard=self.call(new=(999,),ages=[181],missing=(0,))
        self.assertFalse(guard['unknown_fresh_pending_travel']);self.assertEqual(guard['allowed_sends'],['NEW0'])

    def test_unknown_candidate_travel_blocks_even_without_own_pending(self):
        for value in (None,True,float('inf'),float('nan')):
            # NaN is intentionally not passed through the pure-input equality helper.
            args=dict(now=1000,patient_id='P',pending=[],patient_pending=[],proposed_sends=['N'],
                candidates=[dict(hospital_id='N',travel=dict(duration_seconds=value))])
            guard=policy.travel_guard(**args)
            self.assertEqual(guard['allowed_sends'],[])
            self.assertEqual(guard['rows'][0]['reason'],'unknown_new_travel')

    def test_known_candidate_without_fresh_own_is_allowed(self):
        self.assertEqual(self.call(new=(999,),own=())['allowed_sends'],['NEW0'])

    def test_foreign_or_typed_other_patient_pending_does_not_set_reference(self):
        for pid in ('OTHER',1):
            guard=policy.travel_guard(now=1000,patient_id='1',pending=[dict(patient=pid,hospital='H',submitted_at=990)],
                patient_pending=[],proposed_sends=['N'],candidates=[dict(hospital_id='N',travel=dict(duration_seconds=999))])
            self.assertEqual(guard['allowed_sends'],['N']);self.assertFalse(guard['unknown_fresh_pending_travel'])

    def test_zero_and_fractional_travel_not_rounded(self):
        self.assertEqual(self.call(new=(0,0.5),own=(0,))['allowed_sends'],['NEW0'])
        self.assertEqual(self.call(new=(0.5,0.6),own=(0.5,))['allowed_sends'],['NEW0'])

    def test_duplicate_candidate_source_is_unknown(self):
        candidate=dict(hospital_id='N',travel=dict(duration_seconds=1))
        guard=policy.travel_guard(now=1000,patient_id='P',pending=[],patient_pending=[],proposed_sends=['N'],
            candidates=[candidate,deepcopy(candidate)])
        self.assertEqual(guard['allowed_sends'],[])


if __name__=='__main__':unittest.main()
