"""Synthetic orchestration and exact inherited-proposal/output boundaries."""
from copy import deepcopy
import unittest
from unittest.mock import patch
from inquiry_lab import cohort_recourse_policy as policy
from test_inquiry_lab_cohort_hedge_policy import fixture as old_fixture,invoke as old_invoke
from test_inquiry_lab_cohort_dynamic_policy import fixture as D_fixture,invoke as D_invoke
from test_inquiry_lab_cohort_hedge_context import inventory


def fixture(extras=1,count=4):
    if count==4:args=old_fixture(extras=extras)
    else:
        j,ms,ts,p,cs=D_fixture(count);d,_=D_invoke(j,ms,ts,p,cs)
        for row in j['eligible']:row['job']['policy_name']=policy.BASE_POLICY
        for member in ms+ts:member['workflow_snapshot']['job']['policy_name']=policy.BASE_POLICY
        j,ms,ts,p,owners=inventory(j,ms,ts,p,extras=extras)
        args=(j,ms,ts,p,owners,d)
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


class CohortRecoursePolicyTests(unittest.TestCase):
    def test_original_Hedge_menu_sends_origins_and_inputs_preserved(self):
        args=fixture();before=deepcopy(args);result=invoke(args);e=result['evidence']
        self.assertEqual(args,before);self.assertEqual(e['original_hedge_proposal'],args[-1])
        self.assertEqual(e['owner_origins'],args[-1]['evidence']['owner_origins'])
        self.assertEqual(e['menu_indices'],args[-1]['evidence']['menu_indices'])
        for row,base in zip(e['row_scores'],args[-1]['evidence']['row_scores']):
            self.assertEqual(row['base_row_score'],base)
            self.assertEqual([j['source']['inquiry'] for j in row['first_cohort']['jobs']],
                base['first_cohort_score']['projection']['final_pending'][len(base['first_cohort_score']['projection']['initial_pending']):])
            self.assertEqual(len(row['member_metrics']),len(args[-2]['inventory']))

    def test_unit_survival_preserves_Hedge_metrics_choice_and_actual(self):
        args=fixture()
        with patch.object(policy.model,'_f0',return_value=0.0):result=invoke(args)
        e=result['evidence'];self.assertFalse(e['intervened']);self.assertEqual(e['selected_index'],args[-1]['evidence']['selected_index'])
        for new,old in zip(e['row_scores'],args[-1]['evidence']['row_scores']):
            self.assertEqual(new['member_metrics'],old['member_metrics'])
            self.assertEqual(new['tier_probabilities'],old['tier_probabilities'])
            self.assertEqual(new['sum_capped_wait_seconds'],old['sum_capped_wait_seconds'])
        for new,old in zip(result['decisions'],args[-1]['decisions']):
            new['evidence'].pop('cohort_recourse');new['evidence']['policy']=policy.BASE_POLICY;self.assertEqual(new,old)

    def test_reference_two_pairs_and_last_singleton_order_preserved(self):
        for count in (5,6):
            args=fixture(extras=0,count=count)
            with patch.object(policy.model,'_f0',return_value=0.0):result=invoke(args)
            for row in result['evidence']['row_scores']:
                first=row['first_cohort'];old=row['base_row_score']['first_cohort_score']
                self.assertEqual(first['member_metrics'],old['member_metrics'])
                self.assertEqual(len(first['member_metrics']),count)
                kinds=[j['kind'] for j in first['jobs']]
                if count==5:self.assertEqual(kinds[-1],'reference_H_singleton')
                else:self.assertIn(1,[j['step_index'] for j in first['jobs'] if j['kind']=='reference_CS_selected'])

    def test_no_evaluation_exact_actual_plus_new_marker(self):
        args=list(fixture());args[-1]['evidence'].update(evaluated=False,menu_indices=[],owner_origins=[])
        with patch.object(policy.model,'score_first_cohort') as called:result=invoke(args)
        called.assert_not_called();self.assertFalse(result['evidence']['evaluated'])
        for new,old in zip(result['decisions'],args[-1]['decisions']):
            new['evidence'].pop('cohort_recourse');new['evidence']['policy']=policy.BASE_POLICY;self.assertEqual(new,old)

    def test_changed_actual_reconstructed_from_H_baseline_only(self):
        # Isolated output transformation, not a natural optimum intervention.
        args=fixture();base=args[-1]['evidence']['selected_index'];target=next(i for i in args[-1]['evidence']['menu_indices'] if i!=base)
        selector=policy.original.original._select
        def choose(rows,index):
            value=selector(rows,index);value['selected_index']=target;return value
        with patch.object(policy.original.original,'_select',side_effect=choose):result=invoke(args)
        self.assertTrue(result['evidence']['intervened'])
        d=args[-1]['evidence']['original_dynamic_proposal']
        joint=policy.original.original._joint(d['evidence']['original_cohort_success_proposal'])['evidence']
        self.assertEqual([x['send'] for x in result['decisions']],joint['combinations'][target]['hospitals'])
        for decision in result['decisions']:
            self.assertEqual(decision['reason'],'cohort_recourse_current_only')
            self.assertFalse(set(decision['evidence'])&{'joint_first','joint_tail','joint_budget','cohort_success','cohort_dynamic','cohort_hedge'})
            self.assertIn('cohort_recourse',decision['evidence'])

    def test_invalid_complete_context_precedes_foundation(self):
        args=list(fixture());args[-2]['inventory'].pop()
        with patch.object(policy.original,'decide_joint') as called:
            with self.assertRaises(ValueError):invoke(args)
        called.assert_not_called()

    def test_fallback_is_exact_frozen_Hedge_core(self):
        args=fixture(extras=0);inputs=deepcopy(args[1][0]['inputs'])
        old=policy.original.decide(policy.BASE_POLICY,**inputs);new=policy.decide(policy.NAME,**inputs)
        self.assertEqual(new['evidence'].pop('cohort_recourse'),dict(schema_version=1,mode='horizon_fallback',evaluated=False,intervened=False))
        new['evidence']['policy']=policy.BASE_POLICY;self.assertEqual(new,old)


if __name__=='__main__':unittest.main()
