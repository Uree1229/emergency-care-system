"""Synthetic priority capture checks; no saved environments."""
from copy import deepcopy
import unittest
from inquiry_lab import cohort_success_context as context
from test_inquiry_lab_joint_budget_context import fixture as budget_fixture


def fixture(count=5,hospitals=2):
    registry,_,members,tails=budget_fixture(count,hospitals)
    for job in registry['jobs']:job['policy_name']=context.POLICY_NAME
    for member in members+tails:member['workflow_snapshot']['job']['policy_name']=context.POLICY_NAME
    joint=context.build_context(**registry)
    rows=[dict(patient_id=m['patient_id'],patient_version=1,reported_priority=None,priority_source='unknown')
        for m in members+tails]
    priority=context.build_priority_context(joint_context=joint,members=members,tail_members=tails,priority_rows=rows)
    return registry,joint,members,tails,priority


class CohortSuccessContextTests(unittest.TestCase):
    def test_native_context_and_copied_budget_alias_preserve_original_inputs(self):
        registry,joint,members,tails,priority=fixture()
        before=deepcopy((registry,joint,members,tails,priority))
        self.assertTrue(all(r['job']['policy_name']=='eat_joint_budget_l15_f'
            for r in context.base_context_projection(joint)['eligible']))
        self.assertTrue(all(m['workflow_snapshot']['job']['policy_name']=='eat_joint_budget_l15_f'
            for m in context.base_members_projection(members+tails)))
        self.assertEqual((registry,joint,members,tails,priority),before)
        self.assertEqual([r['effective_priority'] for r in priority['patients']],[4]*5)

    def test_expert_tiers_and_unverified_values_follow_public_service_rule(self):
        for value in (4,2,1):
            self.assertEqual(context.effective_priority(value,'expert'),value)
            self.assertEqual(context.effective_priority(value,'self'),4)
            self.assertEqual(context.effective_priority(value,'unknown'),4)
        self.assertEqual(context.effective_priority(None,'expert'),4)
        self.assertEqual(context.effective_priority(None,'unknown'),4)
        for value,source in ((3,'expert'),(True,'expert'),('2','expert'),(2,'latent')):
            with self.assertRaises(ValueError):context.effective_priority(value,source)

    def test_priority_rows_cover_complete_ordered_roster_including_singleton(self):
        _,joint,members,tails,priority=fixture()
        rows=[{k:v for k,v in r.items() if k!='effective_priority'} for r in priority['patients']]
        for bad in (rows[:-1],list(reversed(rows)),rows+[deepcopy(rows[-1])]):
            with self.assertRaises(ValueError):context.build_priority_context(
                joint_context=joint,members=members,tail_members=tails,priority_rows=bad)
        with self.assertRaises(ValueError):context.validate_tail_members(joint,tails[:-1],members=members)

    def test_forged_priority_effective_tier_version_and_snapshot_are_rejected(self):
        _,joint,members,tails,priority=fixture()
        variants=[]
        for field,value in [('effective_priority',2),('patient_version',2),('patient_id','FUTURE')]:
            bad=deepcopy(priority);bad['patients'][-1][field]=value;variants.append(bad)
        for field,value in [('now',1001),('event_prefix_max',99),('decision_prefix_max',1),('revision',8),('source','oracle')]:
            bad=deepcopy(priority);bad[field]=value;variants.append(bad)
        bad=deepcopy(priority);bad['patients'][-1]['hidden_service']=1;variants.append(bad)
        bad=deepcopy(priority);bad['revision']=True;variants.append(bad)
        for bad in variants:
            with self.assertRaises(ValueError):context.validate_priority_context(
                bad,joint_context=joint,members=members,tail_members=tails)

    def test_reported_tier_and_source_are_preserved_without_mutation(self):
        _,joint,members,tails,priority=fixture()
        rows=[{k:v for k,v in r.items() if k!='effective_priority'} for r in priority['patients']]
        rows[0].update(reported_priority=2,priority_source='expert')
        rows[-1].update(reported_priority=1,priority_source='self')
        before=deepcopy((joint,members,tails,rows))
        result=context.build_priority_context(joint_context=joint,members=members,tail_members=tails,priority_rows=rows)
        self.assertEqual(result['patients'][0]['effective_priority'],2)
        self.assertEqual(result['patients'][-1]['reported_priority'],1)
        self.assertEqual(result['patients'][-1]['effective_priority'],4)
        self.assertEqual((joint,members,tails,rows),before)
        self.assertEqual(context.validate_priority_context(result,joint_context=joint,members=members,tail_members=tails),result)

    def test_other_policy_job_and_stale_public_tail_do_not_enter(self):
        registry,_,_,_,_=fixture(4)
        registry['jobs'][0]['policy_name']='eat_joint_budget_l15_f'
        self.assertEqual(context.build_context(**registry)['pair_patient_ids'],['P1','P2'])
        _,joint,members,tails,_=fixture()
        tails[-1]['inputs']['pending']=[dict(hospital='H0',patient='OTHER',submitted_at=990)]
        with self.assertRaises(ValueError):context.validate_tail_members(joint,tails,members=members)


if __name__=='__main__':unittest.main()
