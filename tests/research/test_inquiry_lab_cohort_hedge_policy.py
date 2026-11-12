"""Synthetic intact D menus, complete owner scores and output transformations."""
from copy import deepcopy
import unittest
from unittest.mock import patch
from inquiry_lab import cohort_hedge_policy as policy,cohort_hedge_context as context
from test_inquiry_lab_cohort_dynamic_policy import fixture as D_fixture,invoke as D_invoke
from test_inquiry_lab_cohort_hedge_context import inventory,fixture as context_fixture


def fixture(extras=1):
    j,ms,ts,p,cs=D_fixture(4)
    d,_=D_invoke(j,ms,ts,p,cs)
    for e in j['eligible']:e['job']['policy_name']=policy.NAME
    for m in ms+ts:m['workflow_snapshot']['job']['policy_name']=policy.NAME
    j,ms,ts,p,owners=inventory(j,ms,ts,p,extras=extras)
    return j,ms,ts,p,owners,d


def invoke(args):
    j,ms,ts,p,owners,d=args
    with patch.object(policy.original,'decide_joint',return_value=deepcopy(d)):
        return policy.decide_joint(policy.NAME,members=ms,joint_context=j,tail_members=ts,priority_context=p,owner_context=owners)


def owner_row(args):return next(r for r in args[-2]['inventory'] if r['patient']['id']=='OWNER0')


class CohortHedgePolicyTests(unittest.TestCase):
    def test_menu_first_cohort_scores_and_original_D_are_intact(self):
        args=fixture();before=deepcopy(args);result=invoke(args)
        e=result['evidence'];d=args[-1]
        self.assertEqual(e['original_dynamic_proposal'],d)
        self.assertEqual(e['menu_indices'],d['evidence']['menu_indices'])
        for row,old in zip(e['row_scores'],d['evidence']['row_scores']):
            self.assertEqual(row['first_cohort_score'],old)
            self.assertEqual(row['member_metrics'][:4],old['member_metrics'])
            self.assertEqual(len(row['member_metrics']),5)
        self.assertEqual(args,before)

    def test_freezes_each_owner_once_and_scores_each_menu_row_locally(self):
        args=fixture(extras=2)
        with (patch.object(policy.model,'freeze_owner',wraps=policy.model.freeze_owner) as freeze,
              patch.object(policy.model,'score_owner',wraps=policy.model.score_owner) as score):
            result=invoke(args)
        self.assertEqual(freeze.call_count,2)
        self.assertEqual(score.call_count,2*len(result['evidence']['menu_indices']))
        for row in result['evidence']['row_scores']:
            for s in row['owner_scores']:
                self.assertIn(s['patient_id'],['OWNER0','OWNER1'])
            # Speculative owner plans never enter the D source's final ledger.
            self.assertFalse(any(q['patient'].startswith('OWNER') for q in row['first_cohort_score']['projection']['final_pending']))

    def test_fullscore_is_first_cohort_plus_each_residual_owner_once(self):
        result=invoke(fixture(extras=2))
        for row in result['evidence']['row_scores']:
            ms=row['member_metrics']
            self.assertEqual(len({r['patient_id'] for r in ms}),6)
            self.assertEqual(row['tier_probabilities']['4'],sum(m['metrics']['selection_probability'] for m in ms))
            self.assertEqual(row['sum_capped_wait_seconds'],sum(m['metrics']['capped_wait_seconds'] for m in ms))

    def test_owner_local_row_backlog_changes_score(self):
        args=fixture();owners=args[-2]
        owner=owner_row(args)
        owner['inputs']['candidates']=[dict(hospital_id='H03',travel={'duration_seconds':30})]
        result=invoke(args)
        costs=[r['owner_scores'][0]['metrics']['selection_probability'] for r in result['evidence']['row_scores']]
        self.assertTrue(any(x!=costs[0] for x in costs[1:]))

    def test_disabled_no_job_and_valid_accepted_owners_have_constant_scores(self):
        args=fixture();owner=owner_row(args);owner['job']['enabled']=0
        result=invoke(args)
        self.assertTrue(all(r['owner_scores'][0]['planning_status']=='disabled_job' for r in result['evidence']['row_scores']))
        self.assertEqual(len(result['evidence']['owner_origins']),1)

    def test_no_owners_reduces_to_original_D_choice(self):
        args=fixture(extras=0);result=invoke(args)
        self.assertFalse(result['evidence']['intervened'])
        self.assertEqual(result['evidence']['selected_index'],args[-1]['evidence']['selected_index'])
        for new,old in zip(result['decisions'],args[-1]['decisions']):
            new['evidence'].pop('cohort_hedge');new['evidence']['policy']=policy.BASE_POLICY
            self.assertEqual(new,old)

    def test_no_D_evaluation_preserves_actual_without_owner_search(self):
        args=list(fixture());args[-1]['evidence']['evaluated']=False;args[-1]['evidence']['menu_indices']=[]
        with patch.object(policy.model,'freeze_owner') as freeze:result=invoke(args)
        freeze.assert_not_called();self.assertFalse(result['evidence']['evaluated'])
        self.assertEqual(result['evidence']['owner_origins'],[])
        for new,old in zip(result['decisions'],args[-1]['decisions']):
            new['evidence'].pop('cohort_hedge');new['evidence']['policy']=policy.BASE_POLICY
            self.assertEqual(new,old)

    def test_intervened_actual_rebuilds_H_without_legacy_flags(self):
        # This explicitly isolates output transformation; no natural full-math
        # intervention or complete raw provenance is claimed for this mock.
        args=fixture();d=args[-1];target=next(i for i in d['evidence']['menu_indices'] if i!=d['evidence']['selected_index'])
        selector=policy.original._select
        def choose(rows,base):
            result=selector(rows,base);result['selected_index']=target;return result
        with patch.object(policy.original,'_select',side_effect=choose):result=invoke(args)
        self.assertTrue(result['evidence']['intervened'])
        joint=policy.original._joint(d['evidence']['original_cohort_success_proposal'])['evidence']
        self.assertEqual([r['send'] for r in result['decisions']],joint['combinations'][target]['hospitals'])
        for decision in result['decisions']:
            self.assertEqual(decision['reason'],'cohort_hedge_current_only')
            self.assertFalse(set(decision['evidence'])&{'joint_first','joint_tail','joint_budget','cohort_success','cohort_dynamic'})
            self.assertIn('cohort_hedge',decision['evidence'])

    def test_priorities_are_lexicographic_and_D_ties_preferred(self):
        args=fixture();owner=owner_row(args);owner['patient'].update(reported_priority=2,priority_source='expert',effective_priority=2)
        result=invoke(args)
        for row in result['evidence']['row_scores']:
            self.assertEqual(row['tier_probabilities']['2'],row['owner_scores'][0]['metrics']['selection_probability'])
        rows=[dict(row_index=9,tier_probabilities={'4':1,'2':0,'1':0},sum_capped_wait_seconds=5),
            dict(row_index=2,tier_probabilities={'4':1,'2':1,'1':0},sum_capped_wait_seconds=500)]
        self.assertEqual(policy.original._select(rows,9)['selected_index'],2)
        rows[0]['tier_probabilities']['2']=1;rows[0]['sum_capped_wait_seconds']=500
        self.assertEqual(policy.original._select(rows,9)['selected_index'],9)

    def test_complete_owner_validation_precedes_D_or_model_call(self):
        args=list(fixture());args[-2]['inventory'].pop(0)
        with patch.object(policy.original,'decide_joint') as called:
            with self.assertRaises(ValueError):invoke(args)
            called.assert_not_called()

    def test_fallback_is_exact_D_core_with_new_native_marker(self):
        _,ms,_,_,_=context_fixture(extras=0)
        old=policy.original.decide(policy.BASE_POLICY,**deepcopy(ms[0]['inputs']))
        new=policy.decide(policy.NAME,**deepcopy(ms[0]['inputs']))
        self.assertEqual(new['evidence'].pop('cohort_hedge'),dict(schema_version=1,mode='horizon_fallback',evaluated=False,intervened=False))
        new['evidence']['policy']=policy.BASE_POLICY;self.assertEqual(new,old)


if __name__=='__main__':unittest.main()
