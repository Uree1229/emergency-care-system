"""Synthetic intact parent-plan/menu boundaries and current-only decisions."""
from copy import deepcopy
import unittest
from unittest.mock import patch
from inquiry_lab import cohort_advance_policy as policy
from test_inquiry_lab_cohort_followplan_policy import fixture as old_fixture,invoke as old_invoke


def fixture(extras=1,count=4):
    args=old_fixture(extras=extras,count=count);proposal=old_invoke(args)
    j,ms,ts,p,owners,_=deepcopy(args)
    for row in j['eligible']:row['job']['policy_name']=policy.NAME
    for member in ms+ts:member['workflow_snapshot']['job']['policy_name']=policy.NAME
    for owner in owners['inventory']:
        if owner['job'] is not None and owner['job']['policy_name']==policy.BASE_POLICY:owner['job']['policy_name']=policy.NAME
    return j,ms,ts,p,owners,proposal


def invoke(args):
    j,ms,ts,p,owners,proposal=args
    with patch.object(policy.original,'decide_joint',return_value=deepcopy(proposal)):
        return policy.decide_joint(policy.NAME,members=ms,joint_context=j,tail_members=ts,priority_context=p,owner_context=owners)


class CohortAdvancePolicyTests(unittest.TestCase):
    def test_full_original_proposal_menu_row_initial_CDF_plans_and_residuals_preserved(self):
        args=fixture();before=deepcopy(args);e=invoke(args)['evidence']
        self.assertEqual(args,before);self.assertEqual(e['original_followplan_proposal'],args[-1])
        self.assertEqual(e['menu_indices'],args[-1]['evidence']['menu_indices'])
        for new,old in zip(e['row_scores'],args[-1]['evidence']['row_scores']):
            self.assertEqual(new['base_row_score'],old);self.assertEqual(new['owner_scores'],old['owner_scores'])
            self.assertEqual(len(new['member_metrics']),len(args[-2]['inventory']))
            for score,parent in zip(new['first_cohort_advance']['patient_scores'],old['first_cohort_future']):
                self.assertEqual(score['fixed_initial_items'],parent['hypothetical_origin']['own_items'])
            jobs=new['first_cohort_advance']['jobs']
            self.assertEqual([(j['send_offset_seconds'],j['first_patient_index'],j['plan_candidate_index']) for j in jobs],
                sorted((j['send_offset_seconds'],j['first_patient_index'],j['plan_candidate_index']) for j in jobs))

    def test_first_partition_current_suffix_and_singleton_unchanged(self):
        for count in (4,5,6):
            args=fixture(extras=0,count=count);result=invoke(args)
            for row in result['evidence']['row_scores']:
                first=row['first_cohort_advance']['patient_scores']
                self.assertEqual([s['patient_id'] for s in first],args[-2]['first_cohort_patient_ids'])
                self.assertEqual(len(first),count)

    def test_fallback_exact_parent_plus_new_marker(self):
        inputs=deepcopy(fixture(extras=0)[1][0]['inputs'])
        old=policy.original.decide(policy.BASE_POLICY,**inputs);new=policy.decide(policy.NAME,**inputs)
        self.assertEqual(new['evidence'].pop('cohort_advance'),dict(schema_version=1,mode='horizon_fallback',evaluated=False,intervened=False))
        new['evidence']['policy']=policy.BASE_POLICY;self.assertEqual(new,old)

    def test_no_parent_evaluation_has_no_new_scoring(self):
        args=list(fixture());args[-1]['evidence'].update(evaluated=False,menu_indices=[])
        with patch.object(policy.model,'score_first_cohort') as call:result=invoke(args)
        call.assert_not_called()
        for new,old in zip(result['decisions'],args[-1]['decisions']):
            new['evidence'].pop('cohort_advance');new['evidence']['policy']=policy.BASE_POLICY;self.assertEqual(new,old)

    def test_changed_current_choice_uses_frozen_H_baseline_without_future_sends(self):
        args=fixture();base=args[-1]['evidence']['selected_index'];target=next(i for i in args[-1]['evidence']['menu_indices'] if i!=base)
        selector=policy.D._select
        def choose(rows,index):
            r=selector(rows,index);r['selected_index']=target;return r
        with patch.object(policy.D,'_select',side_effect=choose):result=invoke(args)
        self.assertTrue(result['evidence']['intervened'])
        h=args[-1]['evidence']['original_recourse_proposal']['evidence']['original_hedge_proposal']
        d=h['evidence']['original_dynamic_proposal'];joint=policy.D._joint(d['evidence']['original_cohort_success_proposal'])['evidence']
        self.assertEqual([r['send'] for r in result['decisions']],joint['combinations'][target]['hospitals'])
        for decision in result['decisions']:
            self.assertEqual(decision['reason'],'cohort_advance_current_only')
            self.assertFalse(set(decision['evidence'])&{'cohort_followplan','cohort_recourse','cohort_hedge','cohort_dynamic','cohort_success'})
            self.assertIn('cohort_advance',decision['evidence'])

    def test_invalid_complete_capture_rejected_before_parent(self):
        args=list(fixture());args[-2]['inventory'].pop()
        with patch.object(policy.original,'decide_joint') as call:
            with self.assertRaises(ValueError):invoke(args)
        call.assert_not_called()

    def test_tied_metrics_prefer_actual_followplan_index(self):
        rows=[dict(row_index=i,tier_probabilities={'4':2.,'2':0.,'1':0.},sum_capped_wait_seconds=11.) for i in (7,3,1)]
        self.assertEqual(policy.D._select(rows,3)['selected_index'],3)


if __name__=='__main__':unittest.main()
