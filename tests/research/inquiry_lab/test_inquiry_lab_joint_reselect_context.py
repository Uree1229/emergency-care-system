"""Synthetic complete-public-roster checks; no saved experiment cases."""
from copy import deepcopy
import unittest
from inquiry_lab import joint_reselect_context as context, joint_tail_context, joint_first_context
from test_inquiry_lab_joint_tail_context import fixture as tail_fixture


def fixture(count=3,hospitals=3):
    registry,_,members,tails = tail_fixture(count,hospitals)
    for job in registry['jobs']: job['policy_name']=context.POLICY_NAME
    for member in members+tails: member['workflow_snapshot']['job']['policy_name']=context.POLICY_NAME
    joint = context.build_context(**registry)
    return registry,joint,members,tails


class JointReselectContextTests(unittest.TestCase):
    def test_native_identity_and_two_copied_alias_layers_are_separate(self):
        registry,joint,members,tails = fixture()
        before = deepcopy((registry,joint,members,tails))
        one = context.base_context_projection(joint)
        two = joint_tail_context.base_context_projection(one)
        self.assertTrue(all(r['job']['policy_name']=='eat_joint_tail_l15_f' for r in one['eligible']))
        self.assertTrue(all(r['job']['policy_name']=='eat_joint_first_l15_f' for r in two['eligible']))
        self.assertEqual((registry,joint,members,tails),before)
        self.assertEqual(joint_tail_context.POLICY_NAME,'eat_joint_tail_l15_f')
        self.assertEqual(joint_first_context.POLICY_NAME,'eat_joint_first_l15_f')

    def test_only_current_native_policy_jobs_are_eligible(self):
        registry,_,_,_ = fixture(4)
        registry['jobs'][0]['policy_name']='eat_joint_tail_l15_f'
        registry['jobs'][1]['policy_name']='eat_joint_first_l15_f'
        joint = context.build_context(**registry)
        self.assertEqual(joint['pair_patient_ids'],['P2','P3'])

    def test_complete_current_tail_order_is_preserved(self):
        _,joint,members,tails = fixture(4)
        result = context.validate_tail_members(joint,tails,members=members)
        self.assertEqual([x['patient_id'] for x in result],['P2','P3'])
        for bad in ([],tails[:1],list(reversed(tails))):
            with self.assertRaises(ValueError): context.validate_tail_members(joint,bad,members=members)

    def test_pool_and_max_new_keep_original_adapter_rank(self):
        _,joint,members,tails = fixture(hospitals=8)
        self.assertEqual(context.validate_tail_members(joint,tails,members=members)[0]['hospitals'],
            ['H0','H1','H2','H3','H4','H5'])
        tails[0]['inputs']['max_new']=2
        self.assertEqual(context.validate_tail_members(joint,tails,members=members)[0]['hospitals'],['H0','H1'])

    def test_stale_prefix_version_clock_and_ledger_fail_closed(self):
        _,joint,members,tails = fixture()
        variants=[]
        bad=deepcopy(tails);bad[0]['inputs']['now']=1001;variants.append(bad)
        bad=deepcopy(tails);bad[0]['inputs']['time_context']['decision_prefix_max']=1;variants.append(bad)
        bad=deepcopy(tails);bad[0]['workflow_snapshot']['job']['version']=2;variants.append(bad)
        bad=deepcopy(tails);bad[0]['inputs']['pending']=[dict(hospital='H0',patient='OTHER',submitted_at=990)];variants.append(bad)
        for index,bad in enumerate(variants):
            with self.subTest(index=index),self.assertRaises(ValueError):
                context.validate_tail_members(joint,bad,members=members)

    def test_native_context_unknown_or_wrong_policy_cannot_be_aliased(self):
        _,joint,members,tails = fixture()
        bad=deepcopy(joint);bad['eligible'][0]['job']['policy_name']='eat_joint_tail_l15_f'
        with self.assertRaises(ValueError): context.validate_context(bad)
        bad=deepcopy(joint);bad['hidden_workers']=99
        with self.assertRaises(ValueError): context.validate_context(bad)
        bad=deepcopy(members);bad[0]['workflow_snapshot']['job']['policy_name']='eat_joint_tail_l15_f'
        with self.assertRaises(ValueError): context.validate_tail_members(joint,tails,members=bad)

    def test_no_tail_is_valid_and_no_source_is_mutated(self):
        registry,joint,members,tails = fixture(2)
        before=deepcopy((registry,joint,members,tails))
        self.assertEqual(context.validate_tail_members(joint,tails,members=members),[])
        self.assertEqual((registry,joint,members,tails),before)


if __name__=='__main__':unittest.main()
