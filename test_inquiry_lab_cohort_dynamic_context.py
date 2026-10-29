"""Synthetic native roster and priority delegation checks."""
from copy import deepcopy
import unittest
from inquiry_lab import cohort_dynamic_context as context
from test_inquiry_lab_cohort_success_context import fixture as CS_fixture


def fixture(count=5,hospitals=2):
    registry,_,members,tails,priority=CS_fixture(count,hospitals)
    for job in registry['jobs']:job['policy_name']=context.POLICY_NAME
    for member in members+tails:member['workflow_snapshot']['job']['policy_name']=context.POLICY_NAME
    joint=context.build_context(**registry)
    rows=[{k:v for k,v in r.items() if k!='effective_priority'} for r in priority['patients']]
    priority=context.build_priority_context(joint_context=joint,members=members,tail_members=tails,priority_rows=rows)
    return registry,joint,members,tails,priority


class CohortDynamicContextTests(unittest.TestCase):
    def test_alias_is_copied_and_legacy_context_shape_is_unchanged(self):
        registry,joint,members,tails,priority=fixture()
        before=deepcopy((registry,joint,members,tails,priority))
        self.assertEqual(set(joint),{'schema_version','now','event_prefix_max','decision_prefix_max','eligible','pair_patient_ids'})
        self.assertTrue(all(r['job']['policy_name']=='eat_cohort_success_l15_f'
            for r in context.base_context_projection(joint)['eligible']))
        self.assertTrue(all(m['workflow_snapshot']['job']['policy_name']=='eat_cohort_success_l15_f'
            for m in context.base_members_projection(members+tails)))
        self.assertEqual((registry,joint,members,tails,priority),before)

    def test_all_expert_tiers_and_unknown_fallback_match_frozen_CS(self):
        for tier in (4,2,1):
            self.assertEqual(context.effective_priority(tier,'expert'),tier)
            self.assertEqual(context.effective_priority(tier,'self'),4)
            self.assertEqual(context.effective_priority(tier,'unknown'),4)
        self.assertEqual(context.effective_priority(None,'unknown'),4)

    def test_complete_singleton_priority_and_public_snapshot_required(self):
        _,joint,members,tails,priority=fixture()
        bad=deepcopy(priority);bad['patients'].pop()
        with self.assertRaises(ValueError):context.validate_priority_context(bad,joint_context=joint,members=members,tail_members=tails)
        for key in ('revision','now','event_prefix_max','decision_prefix_max'):
            bad=deepcopy(priority);bad[key]+=1
            with self.assertRaises(ValueError):context.validate_priority_context(bad,joint_context=joint,members=members,tail_members=tails)
        with self.assertRaises(ValueError):context.validate_tail_members(joint,tails[:-1],members=members)

    def test_native_policy_and_common_ledger_fail_closed(self):
        _,joint,members,tails,_=fixture()
        bad=deepcopy(joint);bad['eligible'][0]['job']['policy_name']='eat_cohort_success_l15_f'
        with self.assertRaises(ValueError):context.validate_context(bad)
        bad=deepcopy(tails);bad[-1]['inputs']['pending']=[dict(hospital='H0',patient='OTHER',submitted_at=990)]
        with self.assertRaises(ValueError):context.validate_tail_members(joint,bad,members=members)

    def test_other_policy_jobs_are_excluded_and_priority_input_is_unmodified(self):
        registry,_,_,_,_=fixture(4)
        registry['jobs'][0]['policy_name']='eat_cohort_success_l15_f'
        self.assertEqual(context.build_context(**registry)['pair_patient_ids'],['P1','P2'])
        _,joint,members,tails,priority=fixture()
        before=deepcopy((joint,members,tails,priority))
        self.assertEqual(context.validate_priority_context(priority,joint_context=joint,members=members,tail_members=tails),priority)
        self.assertEqual((joint,members,tails,priority),before)


if __name__=='__main__':unittest.main()
