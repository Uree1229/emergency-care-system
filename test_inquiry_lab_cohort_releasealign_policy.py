"""Known clock boundaries, copied plans and no physical timeout cancellation."""
from copy import deepcopy
from functools import lru_cache
import math
import unittest
from unittest.mock import patch

from inquiry_lab import cohort_releasealign_policy as policy
from test_inquiry_lab_cohort_tailwait_policy import fixture as old_fixture, invoke as old_invoke, promote
from test_inquiry_lab_horizon_policy import inputs, context


def own_inputs(count=4, now=1180, submitted=1000, **changes):
    own = [dict(hospital='OWN'+str(i), patient='P', submitted_at=submitted, travel_seconds=300+i) for i in range(count)]
    value = inputs(now=now, pending=[{k:v for k,v in q.items() if k != 'travel_seconds'} for q in own],
        patient_pending=own, last_contact_at=submitted, time_context=context(now=now))
    value.update(changes)
    return value


@lru_cache(maxsize=3)
def _parent_fixture(kind='inherited'):
    args,_ = old_fixture(kind)
    return args[:5]+(old_invoke(args),)


def fixture(kind='inherited'):
    args = promote(_parent_fixture(kind), policy.BASE_POLICY, policy.NAME)
    return args,args[-1]['evidence']['stoploss_anchor_index']


def invoke(args):
    j,ms,ts,p,owners,parent = args
    with patch.object(policy.original,'decide_joint',return_value=deepcopy(parent)):
        return policy.decide_joint(policy.NAME,members=ms,joint_context=j,tail_members=ts,priority_context=p,owner_context=owners)


class ReleasealignPolicyTests(unittest.TestCase):
    def test_configuration_and_identity_only(self):
        a,b = policy.original.spec(),policy.spec()
        for k in ('name','label'): a.pop(k);b.pop(k)
        self.assertEqual(a,b)
        self.assertEqual(policy.spec()['review_seconds'],15)
        with self.assertRaises(ValueError): policy.spec(policy.BASE_POLICY)

    def test_age180_still_full_and_first_integer_release_is181(self):
        args = own_inputs();before = deepcopy(args)
        parent = policy.original.decide(policy.BASE_POLICY,**args)
        value = policy.decide(policy.NAME,**args)
        tag = value['evidence'].pop('cohort_releasealign')
        self.assertTrue(tag['evaluated']);self.assertTrue(tag['intervened'])
        self.assertEqual(tag['fresh_own_pending_count'],4)
        self.assertEqual(tag['earliest_slot_release_at'],1181)
        self.assertEqual(value['next_review_at'],1181)
        self.assertEqual(parent['next_review_at'],1195)
        value['evidence']['policy']=policy.BASE_POLICY;value['next_review_at']=parent['next_review_at']
        self.assertEqual(value,parent);self.assertEqual(args,before)

    def test_age181_is_stale_but_pending_reply_probability_is_retained(self):
        args = own_inputs(now=1181)
        value = policy.decide(policy.NAME,**args);tag = value['evidence']['cohort_releasealign']
        self.assertEqual(tag['fresh_own_pending_count'],0);self.assertFalse(tag['evaluated'])
        self.assertEqual(value['next_review_at'],1196)
        self.assertEqual(value['evidence']['own_observation_timed_out_count'],4)
        self.assertEqual(value['evidence']['expected_cost_without_new'],tag['original_tailwait_proposal']['evidence']['expected_cost_without_new'])

    def test_fractional_submission_releases_at_first_strict_integer_after180(self):
        args = own_inputs(submitted=1000.5)
        tag = policy.decide(policy.NAME,**args)['evidence']['cohort_releasealign']
        self.assertEqual(tag['earliest_slot_release_at'],1181)

    def test_earliest_of_staggered_own_contacts_is_used(self):
        args = own_inputs()
        for i,q in enumerate(args['patient_pending']): q['submitted_at']+=i
        args['pending']=[{k:v for k,v in q.items() if k!='travel_seconds'} for q in args['patient_pending']]
        tag = policy.decide(policy.NAME,**args)['evidence']['cohort_releasealign']
        self.assertEqual(tag['selected_next_review_at'],1181)

    def test_no_earlier_than_due_or_equal_due_is_exact_copy(self):
        for now in (1165,1166):
            args = own_inputs(now=now)
            value = policy.decide(policy.NAME,**args);tag = value['evidence']['cohort_releasealign']
            self.assertTrue(tag['evaluated']);self.assertFalse(tag['intervened'])
            self.assertEqual(value['next_review_at'],now+15)

    def test_public_A_and_H_cutoff_strictly_exclude_release_at_boundary(self):
        args = own_inputs(time_context=context(now=1180,automatic=1181))
        value = policy.decide(policy.NAME,**args)
        self.assertFalse(value['evidence']['cohort_releasealign']['intervened'])
        args = own_inputs(now=1299,submitted=1119)
        value = policy.decide(policy.NAME,**args)
        self.assertEqual(value['evidence']['cohort_releasealign']['earliest_slot_release_at'],1300)
        self.assertFalse(value['evidence']['cohort_releasealign']['intervened'])

    def test_empty_candidates_or_contact_budget_or_not_full_is_not_changed(self):
        for changes in (dict(candidates=[]),dict(max_new=0),dict(count=3)):
            args = own_inputs(**changes)
            value = policy.decide(policy.NAME,**args);tag = value['evidence']['cohort_releasealign']
            self.assertFalse(tag['evaluated']);self.assertFalse(tag['intervened'])

    def test_prior_contact_required_and_invalid_last_contact_is_copied(self):
        for last in (None,True,float('nan'),float('inf'),1181):
            args = own_inputs(last_contact_at=last)
            tag = policy.decide(policy.NAME,**args)['evidence']['cohort_releasealign']
            self.assertFalse(tag['contacted_owner']);self.assertFalse(tag['intervened'])

    def test_first_unpaired_and_all_first_groups_copy_parent_exactly(self):
        value = policy.decide(policy.NAME,**inputs());tag=value['evidence'].pop('cohort_releasealign')
        value['evidence']['policy']=policy.BASE_POLICY
        self.assertFalse(tag['evaluated']);self.assertEqual(value,tag['original_tailwait_proposal'])
        for kind in ('inherited','new','one_tail'):
            args,_=fixture(kind);before=deepcopy(args);value=invoke(args)
            self.assertEqual(args,before);self.assertEqual(value['evidence']['original_tailwait_proposal'],args[-1])
            for new,old in zip(value['decisions'],args[-1]['decisions']):
                self.assertEqual(new['evidence'].pop('cohort_releasealign')['mode'],'pair_copied_tailwait')
                new['evidence']['policy']=policy.BASE_POLICY;self.assertEqual(new,old)

    def test_foreign_pending_duplicates_and_future_rows_do_not_create_slots(self):
        args = own_inputs(count=3)
        args['pending'].append(dict(hospital='OTHER',patient='OTHER',submitted_at=1000))
        args['patient_pending'].append(deepcopy(args['patient_pending'][0]))
        args['patient_pending'].append(dict(hospital='FUTURE',patient='P',submitted_at=1181,travel_seconds=10))
        tag = policy.decide(policy.NAME,**args)['evidence']['cohort_releasealign']
        self.assertEqual(tag['normalized_own_pending_count'],3);self.assertFalse(tag['evaluated'])

    def test_generator_interface_and_input_immutability(self):
        args=own_inputs();original=deepcopy(args)
        expected=policy.decide(policy.NAME,**args)
        gen=dict(args,**{k:iter(args[k]) for k in ('candidates','observations','pending','patient_pending')})
        self.assertEqual(policy.decide(policy.NAME,**gen),expected);self.assertEqual(args,original)


if __name__=='__main__': unittest.main()
