"""Synthetic intact Recourse boundaries and actual-current-only application."""
from copy import deepcopy
import unittest
from unittest.mock import patch
from inquiry_lab import cohort_followplan_policy as policy
from test_inquiry_lab_cohort_recourse_policy import fixture as old_fixture,invoke as old_invoke


def fixture(extras=1,count=4):
    args=old_fixture(extras=extras,count=count)
    proposal=old_invoke(args)
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


class CohortFollowplanPolicyTests(unittest.TestCase):
    def test_original_menu_rows_jobs_residuals_and_actual_inputs_unchanged(self):
        args=fixture();before=deepcopy(args);value=invoke(args);e=value['evidence']
        self.assertEqual(args,before)
        self.assertEqual(e['original_recourse_proposal'],args[-1])
        self.assertEqual(e['menu_indices'],args[-1]['evidence']['menu_indices'])
        for row,old in zip(e['row_scores'],args[-1]['evidence']['row_scores']):
            self.assertEqual(row['base_row_score'],old)
            self.assertEqual(row['owner_scores'],old['owner_scores'])
            self.assertEqual(len(row['member_metrics']),len(args[-2]['inventory']))
            firstids=[r['patient_id'] for r in row['first_cohort_future']]
            self.assertEqual(firstids,args[-2]['first_cohort_patient_ids'])

    def test_next_reviews_have_exact_current_reference_and_singleton_sources(self):
        for count in (4,5,6):
            args=fixture(extras=0,count=count);result=invoke(args)
            for row in result['evidence']['row_scores']:
                reviews=row['first_review_sources']
                self.assertEqual(len(reviews),count)
                self.assertEqual([p['next_review_at'] for p in row['first_cohort_future']],[r['next_review_at'] for r in reviews])
                self.assertEqual(reviews[0]['kind'],'current_pair_H_baseline')
                self.assertEqual(reviews[2]['kind'],'reference_CS_decision')
                if count==5:self.assertEqual(reviews[-1]['kind'],'reference_H_singleton')
                if count==6:self.assertEqual(reviews[-1]['step_index'],1)

    def test_fallback_exact_recourse_output_plus_only_new_marker(self):
        args=fixture(extras=0);inputs=deepcopy(args[1][0]['inputs'])
        old=policy.original.decide(policy.BASE_POLICY,**inputs);new=policy.decide(policy.NAME,**inputs)
        self.assertEqual(new['evidence'].pop('cohort_followplan'),dict(schema_version=1,mode='horizon_fallback',evaluated=False,intervened=False))
        new['evidence']['policy']=policy.BASE_POLICY;self.assertEqual(new,old)

    def test_unevaluated_exact_output_plus_new_marker_and_no_plan_call(self):
        args=list(fixture());args[-1]['evidence'].update(evaluated=False,menu_indices=[])
        with patch.object(policy.model,'score_first_patient') as planner:result=invoke(args)
        planner.assert_not_called()
        for new,old in zip(result['decisions'],args[-1]['decisions']):
            new['evidence'].pop('cohort_followplan');new['evidence']['policy']=policy.BASE_POLICY
            self.assertEqual(new,old)

    def test_changed_choice_reconstructed_current_pair_only_from_H_baseline(self):
        args=fixture();baseline=args[-1]['evidence']['selected_index']
        target=next(i for i in args[-1]['evidence']['menu_indices'] if i!=baseline)
        selector=policy.original.original.original._select
        def choose(rows,index):
            result=selector(rows,index);result['selected_index']=target;return result
        with patch.object(policy.original.original.original,'_select',side_effect=choose):result=invoke(args)
        self.assertTrue(result['evidence']['intervened'])
        h=args[-1]['evidence']['original_hedge_proposal']['evidence']['original_dynamic_proposal']
        joint=policy.original.original.original._joint(h['evidence']['original_cohort_success_proposal'])['evidence']
        self.assertEqual([d['send'] for d in result['decisions']],joint['combinations'][target]['hospitals'])
        for decision in result['decisions']:
            self.assertEqual(decision['reason'],'cohort_followplan_current_only')
            self.assertFalse(set(decision['evidence'])&{'cohort_recourse','cohort_hedge','cohort_dynamic','cohort_success'})
            self.assertIn('cohort_followplan',decision['evidence'])

    def test_invalid_complete_owner_inventory_rejected_before_foundation(self):
        args=list(fixture());args[-2]['inventory'].pop()
        with patch.object(policy.original,'decide_joint') as called:
            with self.assertRaises(ValueError):invoke(args)
        called.assert_not_called()

    def test_selection_tie_prefers_actual_recourse_index(self):
        rows=[dict(row_index=i,tier_probabilities={'4':1.,'2':0.,'1':0.},sum_capped_wait_seconds=30.) for i in (9,2,7)]
        self.assertEqual(policy.original.original.original._select(rows,2)['selected_index'],2)


if __name__=='__main__':unittest.main()
