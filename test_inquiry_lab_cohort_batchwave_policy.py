"""Synthetic actual N-decision batch and exact accepted-parent fallback."""
from copy import deepcopy
from functools import lru_cache
import unittest
from unittest.mock import patch
from inquiry_lab import cohort_batchwave_policy as policy
from test_inquiry_lab_cohort_startslots_policy import initial_fixture as previous_initial,_initial_fixture as previous_source
from test_inquiry_lab_cohort_tailwait_policy import promote


@lru_cache(maxsize=1)
def _pending_fixture():
    args=deepcopy(previous_source(extras=1,statuses=True))
    return promote(args,'eat_cohort_startslots_l15_f',policy.NAME)


def fixture():
    args=deepcopy(_pending_fixture());return args,args[-1]['evidence']['stoploss_anchor_index']


def initial_fixture(count=4,hospitals=6):return promote(previous_initial(count,hospitals),'eat_cohort_startslots_l15_f',policy.NAME)


def invoke(args):
    j,ms,ts,p,owners,parent=args
    with patch.object(policy.original,'decide_joint',return_value=deepcopy(parent)):
        return policy.decide_joint(policy.NAME,members=ms,joint_context=j,tail_members=ts,priority_context=p,owner_context=owners)


class BatchWavePolicyTests(unittest.TestCase):
    def test_batch_emits_all_native_first_owners_and_full_actual_dispatch(self):
        args=initial_fixture();before=deepcopy(args);result=invoke(args);e=result['evidence'];n=len(args[1]+args[2])
        self.assertEqual(args,before);self.assertTrue(e['batch_evaluated']);self.assertEqual(len(result['decisions']),n)
        self.assertEqual(result['execution_member_indices'],list(range(n)));self.assertEqual(result['dispatch_order'],e['allocation']['dispatch_order'])
        self.assertEqual(e['original_tailwait_proposal'],args[-1]);self.assertTrue(e['actual_entire_first_roster_commit'])
        self.assertFalse(e['actual_Tailwait_continuation_claim']);self.assertFalse(e['old_probability_wait_guards_protect_novel_batch'])

    def test_every_decision_keeps_its_reference_send_set_count_and_next_clock(self):
        args=initial_fixture(count=5);result=invoke(args);e=result['evidence']
        self.assertTrue(e['batch_evaluated'])
        for i,(d,r) in enumerate(zip(result['decisions'],e['reference']['owner_references'])):
            self.assertEqual(d['send'],r['reference_send']);self.assertEqual(len(d['send']),r['reference_cardinality'])
            self.assertEqual(d['next_review_at'],r['reference_proposal']['next_review_at'])
            self.assertTrue(d['evidence']['cohort_batchwave']['original_reference_plan_reference_only'])
            self.assertEqual(d['evidence']['cohort_batchwave']['member_index'],i)
            self.assertEqual([p['hospital'] for p in d['evidence']['candidate_plan'] if p['planned_offset_seconds']==0],d['send'])

    def test_pending_global_queue_bypasses_reference_matching_and_copies_parent(self):
        args,_=fixture()
        with patch.object(policy.model,'build_batch_reference') as reference,patch.object(policy.model,'allocate_reference') as allocate:result=invoke(args)
        reference.assert_not_called();allocate.assert_not_called();self.assertFalse(result['evidence']['batch_evaluated'])
        self.assertEqual(result['execution_member_indices'],[0,1]);self.assertEqual(result['dispatch_order'],[])
        for d,p in zip(result['decisions'],args[-1]['decisions']):
            self.assertFalse(d['evidence']['cohort_batchwave']['original_reference_plan_reference_only'])
            d['evidence'].pop('cohort_batchwave');d['evidence']['policy']=policy.BASE_POLICY;self.assertEqual(d,p)

    def test_no_tail_or_one_tail_remains_pair_parent_path(self):
        for count in (2,3):
            args=initial_fixture(count=count)
            with patch.object(policy.model,'build_batch_reference') as reference:result=invoke(args)
            reference.assert_not_called();self.assertEqual(len(result['decisions']),2);self.assertFalse(result['evidence']['batch_evaluated'])

    def test_primary_coverage_failure_keeps_accepted_Tailwait_not_failed_Startslots(self):
        args=initial_fixture(hospitals=3);result=invoke(args);e=result['evidence']
        self.assertTrue(e['reference_evaluated']);self.assertFalse(e['batch_evaluated']);self.assertEqual(e['base_policy'],policy.BASE_POLICY)
        self.assertEqual(e['allocation']['dispatch_order'],[])
        for d,p in zip(result['decisions'],args[-1]['decisions']):
            d['evidence'].pop('cohort_batchwave');d['evidence']['policy']=policy.BASE_POLICY;self.assertEqual(d,p)

    def test_invalid_owner_or_stale_member_fails_before_parent_and_batch(self):
        args=initial_fixture();args[-2]['inventory'].pop()
        with patch.object(policy.original,'decide_joint') as parent:
            with self.assertRaises(ValueError):invoke(args)
        parent.assert_not_called()
        args=initial_fixture();args[1][0]['workflow_snapshot']['apply_snapshot']['revision']+=1
        with self.assertRaises(ValueError):invoke(args)

    def test_singleton_fallback_and_spec_preserve_accepted_parent_core(self):
        old=policy.original.spec(policy.BASE_POLICY);new=policy.spec()
        for key in ('name','label'):old.pop(key);new.pop(key)
        self.assertEqual(old,new);args,_=fixture();inputs=args[1][0]['inputs']
        old=policy.original.decide(policy.BASE_POLICY,**deepcopy(inputs));new=policy.decide(policy.NAME,**deepcopy(inputs))
        self.assertEqual(new['evidence'].pop('cohort_batchwave'),dict(schema_version=1,mode='horizon_fallback',evaluated=False,intervened=False,original_reference_plan_reference_only=False))
        new['evidence']['policy']=policy.BASE_POLICY;self.assertEqual(new,old)


if __name__=='__main__':unittest.main()
