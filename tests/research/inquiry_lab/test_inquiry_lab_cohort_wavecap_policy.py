"""Synthetic copied-group and narrowly activated follow-up correction contracts."""
from copy import deepcopy
from functools import lru_cache
import math
import unittest
from unittest.mock import patch
from inquiry_lab import cohort_wavecap_policy as policy
from test_inquiry_lab_cohort_tailwait_policy import fixture as old_fixture, invoke as old_invoke, promote
from test_inquiry_lab_horizon_policy import inputs
from test_inquiry_lab_cohort_wavecap_horizon import own_inputs


@lru_cache(maxsize=3)
def _parent_fixture(kind='inherited'):
    args,_=old_fixture(kind)
    return args[:5]+(old_invoke(args),)


def fixture(kind='inherited'):
    args=promote(_parent_fixture(kind),policy.BASE_POLICY,policy.NAME)
    return args,args[-1]['evidence']['stoploss_anchor_index']


def invoke(args):
    j,ms,ts,p,owners,parent=args
    with patch.object(policy.original,'decide_joint',return_value=deepcopy(parent)):
        return policy.decide_joint(policy.NAME,members=ms,joint_context=j,tail_members=ts,priority_context=p,owner_context=owners)


def over_cap_parent(args):
    """Explicit orchestration mock, not a valid independent parent score proof."""
    value=policy.original.decide(policy.BASE_POLICY,**deepcopy(args))
    rows=value['evidence']['candidate_plan']
    for row in rows:row['planned_offset_seconds']=None
    rows[0]['planned_offset_seconds']=0;rows[1]['planned_offset_seconds']=15
    value['send']=[rows[0]['hospital']];value['evidence']['sent_hospitals']=list(value['send'])
    return value


class WavecapPolicyTests(unittest.TestCase):
    def test_spec_is_Tailwait_configuration_and_strict_identity(self):
        a=policy.original.spec();b=policy.spec()
        for key in ('name','label'):a.pop(key);b.pop(key)
        self.assertEqual(a,b)
        with self.assertRaises(ValueError):policy.spec(policy.BASE_POLICY)

    def test_initial_and_new_pair_and_one_tail_copy_full_Tailwait_proposal(self):
        for kind in ('inherited','new','one_tail'):
            args,_=fixture(kind);before=deepcopy(args)
            with patch.object(policy.horizon,'decide',side_effect=AssertionError('group never uses cap planner')):
                value=invoke(args)
            self.assertEqual(args,before);self.assertEqual(value['evidence']['original_tailwait_proposal'],args[-1])
            self.assertFalse(value['evidence']['evaluated']);self.assertTrue(value['evidence']['copied_tailwait_group'])
            for new,old in zip(value['decisions'],args[-1]['decisions']):
                self.assertEqual(new['evidence'].pop('cohort_wavecap')['mode'],'pair_copied_tailwait')
                new['evidence']['policy']=policy.BASE_POLICY;self.assertEqual(new,old)

    def test_first_singleton_fallback_exact_parent_even_if_forecast_over_cap(self):
        args=own_inputs();args['last_contact_at']=None;parent=over_cap_parent(args)
        with patch.object(policy.original,'decide',return_value=deepcopy(parent)),patch.object(policy.horizon,'decide') as corrected:
            value=policy.decide(policy.NAME,**args)
        corrected.assert_not_called();meta=value['evidence'].pop('cohort_wavecap')
        self.assertTrue(meta['original_plan_over_cap']);self.assertFalse(meta['evaluated'])
        value['evidence']['policy']=policy.BASE_POLICY;self.assertEqual(value,parent)

    def test_valid_prior_contact_pending_and_original_over_cap_all_required(self):
        args=own_inputs();before=deepcopy(args);parent=over_cap_parent(args)
        with patch.object(policy.original,'decide',return_value=deepcopy(parent)):
            value=policy.decide(policy.NAME,**args)
        meta=value['evidence']['cohort_wavecap'];self.assertTrue(meta['evaluated'])
        self.assertEqual(args,before);self.assertEqual(meta['original_tailwait_proposal'],parent)
        self.assertIsNotNone(meta['corrected_horizon_proposal'])
        self.assertTrue(all(r['total_fresh_count']<=4 for r in meta['resulting_occupancy']))
        self.assertEqual(meta['normalized_own_pending_count'],3)
        self.assertFalse(meta['next_review_changed'])

    def test_invalid_last_contact_matches_H_normalization_and_does_not_activate(self):
        for invalid in (None,True,'1010',math.nan,math.inf,1016):
            args=own_inputs(last_contact_at=invalid);parent=over_cap_parent(args)
            with patch.object(policy.original,'decide',return_value=parent),patch.object(policy.horizon,'decide') as corrected:
                value=policy.decide(policy.NAME,**args)
            corrected.assert_not_called();self.assertFalse(value['evidence']['cohort_wavecap']['evaluated'])
        self.assertEqual(policy._last_contact(1015.,1015),1015.)

    def test_empty_normalized_own_and_feasible_plan_copy_exact_parent(self):
        for args in (inputs(last_contact_at=1000),own_inputs(max_new=0)):
            parent=policy.original.decide(policy.BASE_POLICY,**deepcopy(args))
            with patch.object(policy.horizon,'decide',side_effect=AssertionError('copy must not replan')):
                value=policy.decide(policy.NAME,**args)
            meta=value['evidence'].pop('cohort_wavecap');self.assertFalse(meta['evaluated'])
            value['evidence']['policy']=policy.BASE_POLICY;self.assertEqual(value,parent)

    def test_genuine_followup_keeps_every_wave_cap_without_mutating_shared_H(self):
        args=own_inputs(4);before=deepcopy(args);fn=policy.HORIZON.decide
        value=policy.decide(policy.NAME,**args);meta=value['evidence']['cohort_wavecap']
        self.assertEqual(args,before);self.assertIs(policy.HORIZON.decide,fn)
        self.assertEqual(value['send'],[])
        if meta['evaluated']:self.assertTrue(all(r['total_fresh_count']<=4 for r in meta['resulting_occupancy']))
        self.assertEqual(meta['original_tailwait_proposal']['evidence']['policy'],policy.BASE_POLICY)

    def test_generators_materialized_once_for_both_planners(self):
        args=own_inputs();a=policy.decide(policy.NAME,**args)
        generators=dict(args,**{k:iter(args[k]) for k in ('candidates','observations','pending','patient_pending')})
        self.assertEqual(a,policy.decide(policy.NAME,**generators))

    def test_group_sources_and_complete_owner_checks_precede_parent_call(self):
        args,_=fixture();args[-2]['inventory'].pop()
        with patch.object(policy.original,'decide_joint') as parent:
            with self.assertRaises(ValueError):invoke(args)
        parent.assert_not_called()


if __name__=='__main__':unittest.main()
