"""Synthetic matching activation, current-pair execution and copied paths."""
from copy import deepcopy
from functools import lru_cache
import unittest
from unittest.mock import patch
from inquiry_lab import cohort_startslots_policy as policy
from test_inquiry_lab_cohort_tailwait_policy import fixture as previous_fixture,invoke as previous_invoke,promote
from test_inquiry_lab_cohort_pairscope_policy import fixture as scope_fixture,invoke as scope_invoke
from test_inquiry_lab_cohort_pairshield_policy import invoke as shield_invoke


@lru_cache(maxsize=1)
def _copied_fixture():
    args,_=previous_fixture();return promote(args,policy.BASE_POLICY,policy.NAME)[:5]+(previous_invoke(args),)


def fixture():
    args=deepcopy(_copied_fixture());return args,args[-1]['evidence']['stoploss_anchor_index']


@lru_cache(maxsize=6)
def _initial_fixture(count=4,hospitals=6,extras=0,statuses=False):
    args,_=scope_fixture(count=count,extras=extras,hospitals=hospitals,statuses=statuses);scope=scope_invoke(args)
    args=promote(args,'eat_cohort_pairscope_l15_f','eat_cohort_pairshield_l15_f');args=args[:5]+(scope,);shield=shield_invoke(args)
    args=promote(args,'eat_cohort_pairshield_l15_f',policy.BASE_POLICY);args=args[:5]+(shield,);tail=previous_invoke(args)
    args=promote(args,policy.BASE_POLICY,policy.NAME);return args[:5]+(tail,)


def initial_fixture(count=4,hospitals=6):return deepcopy(_initial_fixture(count,hospitals))


def invoke(args):
    j,ms,ts,p,owners,parent=args
    with patch.object(policy.original,'decide_joint',return_value=deepcopy(parent)):
        return policy.decide_joint(policy.NAME,members=ms,joint_context=j,tail_members=ts,priority_context=p,owner_context=owners)


class StartSlotPolicyTests(unittest.TestCase):
    def test_empty_initial_roster_allocates_but_commits_only_current_native_pair(self):
        args=initial_fixture();before=deepcopy(args);result=invoke(args);e=result['evidence']
        self.assertEqual(args,before);self.assertTrue(e['scope_eligible']);self.assertTrue(e['evaluated'])
        self.assertEqual(e['original_tailwait_proposal'],args[-1]);self.assertEqual(len(e['allocation']['planned_sends']),4)
        self.assertEqual(len(result['decisions']),2);self.assertEqual([d['send'] for d in result['decisions']],e['allocation']['planned_sends'][:2])
        self.assertEqual([m['patient_id'] for m in args[1]],args[0]['pair_patient_ids'])
        self.assertFalse(e['future_assignments_committed']);self.assertFalse(e['old_probability_wait_guards_protect_novel_counts'])
        self.assertTrue(all(d['evidence']['cohort_startslots']['original_H_plan_reference_only'] for d in result['decisions']))

    def test_matching_counts_are_explicitly_new_and_original_parent_proof_is_intact(self):
        args=initial_fixture();e=invoke(args)['evidence'];a=e['allocation']
        self.assertTrue(all(len(s)>=1 for s in a['planned_sends']));self.assertTrue(any(len(s)!=r['original_H_immediate_cardinality'] for s,r in zip(a['planned_sends'],a['owner_records'])))
        self.assertFalse(a['original_probability_wait_guards_applicable']);self.assertEqual(e['original_tailwait_proposal'],args[-1])
        self.assertTrue(all(p['evidence']['policy']==policy.HORIZON.NAME for p in e['public_H_baseline_proposals']))

    def test_existing_pending_scope_copies_parent_without_model_or_H_search(self):
        args=deepcopy(_initial_fixture(extras=1,statuses=True))
        with patch.object(policy.model,'allocate_slots') as allocate,patch.object(policy.HORIZON,'decide') as H:result=invoke(args)
        allocate.assert_not_called();H.assert_not_called();self.assertFalse(result['evidence']['scope_eligible'])
        self.assertTrue(all(not d['evidence']['cohort_startslots']['original_H_plan_reference_only'] for d in result['decisions']))
        for d,p in zip(result['decisions'],args[-1]['decisions']):
            d['evidence'].pop('cohort_startslots');d['evidence']['policy']=policy.BASE_POLICY;self.assertEqual(d,p)

    def test_new_no_tail_and_one_tail_groups_remain_parent_paths(self):
        for count in (2,3):
            args=initial_fixture(count=count)
            with patch.object(policy.model,'allocate_slots') as allocate:result=invoke(args)
            allocate.assert_not_called();self.assertFalse(result['evidence']['evaluated'])
            for d,p in zip(result['decisions'],args[-1]['decisions']):
                d['evidence'].pop('cohort_startslots');d['evidence']['policy']=policy.BASE_POLICY;self.assertEqual(d,p)

    def test_insufficient_slots_copy_exact_parent_and_retain_failed_cover_certificate(self):
        args=initial_fixture(hospitals=3);result=invoke(args);e=result['evidence']
        self.assertTrue(e['scope_eligible']);self.assertFalse(e['evaluated']);self.assertFalse(e['allocation']['coverage_complete'])
        self.assertEqual(e['allocation']['coverage']['flow'],3)
        for d,p in zip(result['decisions'],args[-1]['decisions']):
            d['evidence'].pop('cohort_startslots');d['evidence']['policy']=policy.BASE_POLICY;self.assertEqual(d,p)

    def test_missing_owners_and_stale_prefix_fail_before_parent_or_new_allocation(self):
        args=initial_fixture();args[-2]['inventory'].pop()
        with patch.object(policy.original,'decide_joint') as parent:
            with self.assertRaises(ValueError):invoke(args)
        parent.assert_not_called()
        args=initial_fixture();args[1][0]['workflow_snapshot']['apply_snapshot']['revision']+=1
        with self.assertRaises(ValueError):invoke(args)

    def test_changed_current_decisions_use_H_evidence_and_fixed_next_review(self):
        args=initial_fixture();result=invoke(args);e=result['evidence']
        for i,d in enumerate(result['decisions']):
            self.assertEqual(d['reason'],'cohort_startslots_current_pair_only')
            self.assertEqual(d['next_review_at'],e['public_H_baseline_proposals'][i]['next_review_at'])
            self.assertEqual(d['evidence']['planned_expected_cost'],round(e['allocation']['final_owner_metrics'][i]['metrics']['total_cost_seconds'],3))
            self.assertEqual([r['hospital'] for r in d['evidence']['candidate_plan'] if r['planned_offset_seconds']==0],d['send'])

    def test_singleton_fallback_and_spec_keep_original_H_core(self):
        old=policy.original.spec(policy.BASE_POLICY);new=policy.spec()
        for key in ('name','label'):old.pop(key);new.pop(key)
        self.assertEqual(old,new);args,_=fixture();inputs=args[1][0]['inputs']
        old=policy.original.decide(policy.BASE_POLICY,**deepcopy(inputs));new=policy.decide(policy.NAME,**deepcopy(inputs))
        self.assertEqual(new['evidence'].pop('cohort_startslots'),dict(schema_version=1,mode='horizon_fallback',evaluated=False,intervened=False,original_H_plan_reference_only=False))
        new['evidence']['policy']=policy.BASE_POLICY;self.assertEqual(new,old)


if __name__=='__main__':unittest.main()
