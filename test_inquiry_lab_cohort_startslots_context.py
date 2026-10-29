"""Synthetic complete native capture and calculation-only identity aliases."""
from copy import deepcopy
import unittest
from inquiry_lab import cohort_startslots_context as context
from test_inquiry_lab_cohort_startslots_policy import fixture as policy_fixture


def fixture():
    return policy_fixture()[0]


class CohortStartslotsContextTests(unittest.TestCase):
    def test_full_snapshot_validation_and_pure_identity_projection(self):
        j,ms,ts,p,owners,_=fixture();before=deepcopy((j,ms,ts,p,owners))
        self.assertEqual(context.validate_owner_context(owners,joint_context=j,members=ms,tail_members=ts,priority_context=p),owners)
        base=context.base_owner_projection(owners)
        self.assertTrue(all(o['job'] is None or o['job']['policy_name']==context.BASE_POLICY_NAME for o in base['inventory']))
        self.assertEqual((j,ms,ts,p,owners),before)
        context.original.validate_owner_context(base,joint_context=context.base_context_projection(j),
            members=context.base_members_projection(ms),tail_members=context.base_members_projection(ts),priority_context=p)

    def test_foreign_Hedge_job_remains_unsupported_in_calculation_copy(self):
        j,ms,ts,p,owners,_=fixture();owner=next(o for o in owners['inventory'] if o['patient']['id']=='OWNER0')
        owner['job']['policy_name']=context.BASE_POLICY_NAME
        context.validate_owner_context(owners,joint_context=j,members=ms,tail_members=ts,priority_context=p)
        copied=context.base_owner_projection(owners)
        value=next(o for o in copied['inventory'] if o['patient']['id']=='OWNER0')
        self.assertEqual(value['job']['policy_name'],'__foreign_cohort_tailwait__')
        self.assertEqual(owner['job']['policy_name'],context.BASE_POLICY_NAME)

    def test_omitted_owner_stale_version_and_wrong_native_label_rejected(self):
        for edit in (lambda x:x['inventory'].pop(),lambda x:x['inventory'][0]['patient'].update(version=999)):
            j,ms,ts,p,owners,_=fixture();edit(owners)
            with self.assertRaises(ValueError):context.validate_owner_context(owners,joint_context=j,members=ms,tail_members=ts,priority_context=p)
        j,ms,ts,p,owners,_=fixture();j['eligible'][0]['job']['policy_name']=context.BASE_POLICY_NAME
        with self.assertRaises(ValueError):context.validate_context(j)

    def test_priority_contract_unknown_default_and_expert_tiers_unchanged(self):
        for value,source,expected in [(None,'unknown',4),(1,'unknown',4),(4,'expert',4),(2,'expert',2),(1,'expert',1)]:
            self.assertEqual(context.effective_priority(value,source),expected)
        with self.assertRaises(ValueError):context.effective_priority(3,'expert')


if __name__=='__main__':unittest.main()
