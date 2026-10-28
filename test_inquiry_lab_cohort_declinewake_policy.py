"""Unchanged Tailwait mathematics and old-review clipping, synthetic inputs only."""
from copy import deepcopy
from functools import lru_cache
import unittest
from unittest.mock import patch
from inquiry_lab import cohort_declinewake_policy as policy
from test_inquiry_lab_cohort_tailwait_policy import fixture as old_fixture,invoke as old_invoke,promote
from test_inquiry_lab_horizon_policy import inputs
from test_inquiry_lab_cohort_declinewake_workflow import DBFixture,offers
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


class DeclinewakePolicyTests(unittest.TestCase):
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
                self.assertFalse(value['evidence'].pop('cohort_declinewake')['triggered'])
                value['evidence']['policy']=policy.BASE_POLICY;self.assertEqual(value,parent)

    def test_untriggered_H_fallback_is_exact_parent_including_no_candidate_and_budget(self):
        for args in (inputs(),inputs(max_new=0),inputs(candidates=[])):
            parent=policy.original.decide(policy.BASE_POLICY,**deepcopy(args));result=policy.decide(policy.NAME,**deepcopy(args))
            proof=result['evidence'].pop('cohort_declinewake');self.assertFalse(proof['triggered'])
            self.assertEqual(proof['original_tailwait_proposal'],parent)
            result['evidence']['policy']=policy.BASE_POLICY;self.assertEqual(result,parent)


class TriggeredMathTests(DBFixture):
    def test_trigger_keeps_full_genuine_H_send_cost_CDF_and_reason_with_only_review_clip(self):
        from inquiry_lab import cohort_declinewake_workflow as w
        self.seed();self.decline();snapshot,_,wake=w._capture(self.conn,1005,'P')
        temporal,_=w._time_context(self.conn,1005,snapshot,w._job(self.conn,'P'))
        ledger=inquiry_observations.capture(self.conn,1005,'P');travel={row['hospital_id']:row['travel']['duration_seconds'] for row in offers()['candidates']}
        args=dict(now=1005,candidates=offers()['candidates'][3:],observations=ledger['observations'],pending=ledger['pending'],
            patient_pending=[dict(q,travel_seconds=travel[q['hospital']]) for q in ledger['patient_pending']],max_new=7,last_contact_at=1000,time_context=temporal)
        parent=policy.original.decide(policy.BASE_POLICY,**deepcopy(args));before=deepcopy((args,wake))
        result=policy.decide(policy.NAME,**args,wake_context=wake)
        self.assertEqual((args,wake),before);proof=result['evidence'].pop('cohort_declinewake')
        self.assertTrue(proof['triggered']);self.assertEqual(proof['original_tailwait_proposal'],parent)
        self.assertEqual(result['next_review_at'],1015);result['next_review_at']=parent['next_review_at'];result['evidence']['policy']=policy.BASE_POLICY
        self.assertEqual(result,parent)

    def test_changed_context_prefix_or_identity_is_rejected(self):
        from inquiry_lab import cohort_declinewake_workflow as w
        self.seed();self.decline();snapshot,_,wake=w._capture(self.conn,1005,'P');temporal,_=w._time_context(self.conn,1005,snapshot,w._job(self.conn,'P'))
        args=inputs(now=1005,time_context=temporal)
        for changed in ('prefix','patient'):
            bad=deepcopy(wake)
            if changed=='prefix':bad['event_prefix_max']+=1
            else:bad['patient_id']='OTHER'
            with self.assertRaises(ValueError):policy.decide(policy.NAME,**args,wake_context=bad)


if __name__=='__main__':unittest.main()
