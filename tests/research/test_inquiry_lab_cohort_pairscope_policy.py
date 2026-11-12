"""Synthetic public-prefix scope and complete-owner policy boundaries."""
from copy import deepcopy
from functools import lru_cache
import unittest
from unittest.mock import patch
from inquiry_lab import cohort_pairscope_policy as policy,cohort_hedge_context as hedge_context
from test_inquiry_lab_cohort_dynamic_context import fixture as public_fixture
from test_inquiry_lab_cohort_hedge_context import inventory


@lru_cache(maxsize=12)
def _source_fixture(count=2,extras=3,hospitals=5,statuses=True):
    _,j,ms,ts,p=public_fixture(count,hospitals)
    for row in j['eligible']:row['job']['policy_name']=hedge_context.POLICY_NAME
    for member in ms+ts:member['workflow_snapshot']['job']['policy_name']=hedge_context.POLICY_NAME
    j,ms,ts,p,owners=inventory(j,ms,ts,p,extras=extras)
    for row in j['eligible']:row['job']['policy_name']=policy.BASE_POLICY
    for member in ms+ts:member['workflow_snapshot']['job']['policy_name']=policy.BASE_POLICY
    for owner in owners['inventory']:
        if owner['job'] is not None:owner['job']['policy_name']=policy.BASE_POLICY
    if statuses and extras:
        now=j['now'];owner=next(o for o in owners['inventory'] if o['patient']['id']=='OWNER0')
        pending=dict(hospital='H0',patient='OWNER0',submitted_at=now)
        for member in ms+ts:member['inputs']['pending']=[deepcopy(pending)]
        for current in owners['inventory']:current['inputs']['pending']=[deepcopy(pending)]
        owner['inquiries']=[dict(id=1,patient_id='OWNER0',hospital_id='H0',state='PENDING',last_sent_at=now)]
        owner['inputs'].update(max_new=9,last_contact_at=now,patient_pending=[dict(pending,travel_seconds=0)])
        owner['inputs']['candidates']=[c for c in owner['inputs']['candidates'] if c['hospital_id']!='H0']
        if extras>1:next(o for o in owners['inventory'] if o['patient']['id']=='OWNER1')['job']['enabled']=0
        if extras>2:
            missing=next(o for o in owners['inventory'] if o['patient']['id']=='OWNER2');missing['job']=None;missing['apply_snapshot']['job_version']=None
        if extras>3:next(o for o in owners['inventory'] if o['patient']['id']=='OWNER3')['job']['policy_name']='fixed_3'
    parent=policy.original.decide_joint(policy.BASE_POLICY,members=ms,joint_context=j,tail_members=ts,priority_context=p,owner_context=owners)
    return j,ms,ts,p,owners,parent


def fixture(count=2,extras=3,hospitals=5,statuses=True):
    j,ms,ts,p,owners,parent=deepcopy(_source_fixture(count,extras,hospitals,statuses))
    for row in j['eligible']:row['job']['policy_name']=policy.NAME
    for member in ms+ts:member['workflow_snapshot']['job']['policy_name']=policy.NAME
    for owner in owners['inventory']:
        if owner['job'] is not None and owner['job']['policy_name']==policy.BASE_POLICY:owner['job']['policy_name']=policy.NAME
    return (j,ms,ts,p,owners,parent),parent['evidence']['stoploss_anchor_index']


def invoke(args):
    j,ms,ts,p,owners,parent=args
    with patch.object(policy.original,'decide_joint',return_value=deepcopy(parent)):
        return policy.decide_joint(policy.NAME,members=ms,joint_context=j,tail_members=ts,priority_context=p,owner_context=owners)


def inherited_fixture():
    from test_inquiry_lab_cohort_envelope_policy import fixture as previous_fixture,invoke as previous_invoke
    args,*_=previous_fixture();parent=previous_invoke(args);j,ms,ts,p,owners,_=deepcopy(args)
    for row in j['eligible']:row['job']['policy_name']=policy.NAME
    for member in ms+ts:member['workflow_snapshot']['job']['policy_name']=policy.NAME
    for owner in owners['inventory']:
        if owner['job'] is not None and owner['job']['policy_name']==policy.BASE_POLICY:owner['job']['policy_name']=policy.NAME
    return j,ms,ts,p,owners,parent


class PairScopePolicyTests(unittest.TestCase):
    def test_exact_two_eligible_activates_despite_inactive_parent_and_no_tail(self):
        args,_=fixture();before=deepcopy(args);self.assertFalse(args[-1]['evidence']['evaluated'])
        e=invoke(args)['evidence'];self.assertEqual(args,before)
        self.assertTrue(e['evaluated']);self.assertTrue(e['newly_activated']);self.assertFalse(e['inherited_evaluated'])
        self.assertEqual(e['activation_reason'],'new_no_tail_first_pair');self.assertEqual(e['original_envelope_proposal'],args[-1])
        self.assertEqual(e['new_coverage']['first_contact_count'],2);self.assertEqual(e['new_coverage']['captured_owner_count'],5)

    def test_full_original_feasible_menu_and_Stoploss_actual_anchor(self):
        args,anchor=fixture(extras=0,statuses=False);e=invoke(args)['evidence']
        self.assertGreater(len(e['new_feasible_row_indices']),1)
        self.assertEqual(e['new_menu_indices'][0],anchor)
        self.assertEqual(set(e['new_menu_indices']),set(e['new_feasible_row_indices']))
        self.assertEqual([r['row_index'] for r in e['new_row_scores']],e['new_menu_indices'])
        self.assertEqual(e['owner_probability_guard']['baseline_row_index'],anchor)
        self.assertEqual(e['protected_wait_selection']['baseline_row_index'],anchor)

    def test_queried_disabled_missing_and_foreign_residuals_all_frozen_and_scored(self):
        args,_=fixture(extras=4);e=invoke(args)['evidence'];origins=e['new_owner_origins']
        self.assertEqual([o['patient_id'] for o in origins],['OWNER0','OWNER1','OWNER2','OWNER3'])
        queried=origins[0];self.assertEqual(len(queried['own_items']),1);self.assertEqual(queried['original_all_own'][0]['patient'],'OWNER0')
        wanted={o['patient']['id'] for o in args[-2]['inventory']}
        for row in e['new_row_scores']:
            self.assertEqual({m['patient_id'] for m in row['member_metrics']},wanted)
            self.assertEqual(len(row['member_metrics']),len(wanted))
            self.assertEqual({m['patient_id'] for m in row['unchanged_law_residual_member_metrics']},{'OWNER0','OWNER1','OWNER2','OWNER3'})

    def test_per_owner_loss_is_rejected_even_if_total_probability_increases(self):
        args,anchor=fixture(extras=1,statuses=False);real=policy.model.score_row
        baseline=invoke(args)['evidence'];target=next(i for i in baseline['new_menu_indices'] if i!=anchor)
        def change(index,position,prepared):
            row=real(index,position,prepared)
            for member in row['member_metrics']:
                p=.49 if index==target and member['patient_id']=='OWNER0' else 1. if index==target else .5
                member['metrics'].update(selection_probability=p,capped_wait_seconds=0. if index==target else 100.)
            row['tier_probabilities']={str(t):sum(m['metrics']['selection_probability'] for m in row['member_metrics'] if m['effective_priority']==t) for t in policy.TIERS}
            row['sum_capped_wait_seconds']=sum(m['metrics']['capped_wait_seconds'] for m in row['member_metrics']);return row
        with patch.object(policy.model,'score_row',side_effect=change):e=invoke(args)['evidence']
        self.assertIn(target,e['owner_probability_guard']['rejected_row_indices']);self.assertEqual(e['selected_index'],anchor)

    def test_new_choice_rebuilds_only_current_pair_from_original_H_table(self):
        args,anchor=fixture(extras=0,statuses=False);real=policy.model.score_row;initial=invoke(args)['evidence']
        target=next(i for i in initial['new_menu_indices'] if i!=args[-1]['evidence']['selected_index'])
        def change(index,position,prepared):
            row=real(index,position,prepared)
            for member in row['member_metrics']:member['metrics'].update(selection_probability=1. if index==target else .5,capped_wait_seconds=0. if index==target else 100.,total_cost_seconds=123.4567)
            row['tier_probabilities']={str(t):sum(m['metrics']['selection_probability'] for m in row['member_metrics'] if m['effective_priority']==t) for t in policy.TIERS}
            row['sum_capped_wait_seconds']=sum(m['metrics']['capped_wait_seconds'] for m in row['member_metrics']);return row
        with patch.object(policy.model,'score_row',side_effect=change):result=invoke(args)
        e=result['evidence'];self.assertEqual(e['selected_index'],target);self.assertTrue(e['intervened'])
        full=args[-1]['evidence']['original_waitpareto_proposal']['evidence']['original_pareto_proposal']['evidence']['original_fullmenu_proposal']['evidence']
        table=policy.model.fullmenu.parent_views(full['original_stoploss_proposal'])['joint']
        self.assertEqual([d['send'] for d in result['decisions']],table['combinations'][target]['hospitals'])
        self.assertTrue(all(d['evidence']['planned_expected_cost']==123.457 for d in result['decisions']))
        self.assertTrue(all(d['reason']=='cohort_pairscope_current_only' for d in result['decisions']))

    def test_inherited_active_actions_scores_guard_selection_are_copied_exactly(self):
        args=inherited_fixture();self.assertTrue(args[-1]['evidence']['evaluated'])
        with patch.object(policy.model,'prepare') as prep,patch.object(policy.model,'score_row') as score:result=invoke(args)
        prep.assert_not_called();score.assert_not_called();e=result['evidence']
        self.assertTrue(e['inherited_evaluated']);self.assertFalse(e['newly_activated']);self.assertEqual(e['new_row_scores'],[])
        self.assertEqual(e['owner_probability_guard'],args[-1]['evidence']['owner_probability_guard'])
        self.assertEqual(e['protected_wait_selection'],args[-1]['evidence']['protected_wait_selection'])
        self.assertEqual(e['original_envelope_proposal'],args[-1])
        for new,old in zip(result['decisions'],args[-1]['decisions']):
            new['evidence'].pop('cohort_pairscope');new['evidence']['policy']=policy.BASE_POLICY;self.assertEqual(new,old)

    def test_one_tail_remains_inactive_without_new_origin_or_row_calls(self):
        args,_=fixture(count=3,extras=1,statuses=False);self.assertEqual(len(args[2]),1);self.assertFalse(args[-1]['evidence']['evaluated'])
        with patch.object(policy.model,'prepare') as prep,patch.object(policy.model,'score_row') as score:result=invoke(args)
        prep.assert_not_called();score.assert_not_called();self.assertFalse(result['evidence']['evaluated'])
        self.assertEqual(result['evidence']['activation_reason'],'inherited_inactive_with_tail')
        for new,old in zip(result['decisions'],args[-1]['decisions']):
            new['evidence'].pop('cohort_pairscope');new['evidence']['policy']=policy.BASE_POLICY;self.assertEqual(new,old)

    def test_baseline_only_original_table_remains_valid_and_tied(self):
        args,anchor=fixture(extras=0,hospitals=1,statuses=False);e=invoke(args)['evidence']
        self.assertEqual(e['new_menu_indices'],[anchor]);self.assertEqual(e['selected_index'],anchor)
        self.assertTrue(e['owner_probability_guard']['baseline_always_admissible'])

    def test_missing_owner_or_wrong_anchor_rejected_before_any_new_action(self):
        args,_=fixture();args[-2]['inventory'].pop()
        with patch.object(policy.original,'decide_joint') as parent:
            with self.assertRaises(ValueError):invoke(args)
        parent.assert_not_called()
        args,_=fixture();args[-1]['evidence']['stoploss_anchor_index']=-1
        with self.assertRaises(ValueError):invoke(args)

    def test_spec_and_singleton_H_fallback_preserve_parent_core(self):
        old=policy.original.spec(policy.BASE_POLICY);new=policy.spec()
        for key in ('name','label'):old.pop(key);new.pop(key)
        self.assertEqual(old,new);args,_=fixture();inputs=args[1][0]['inputs']
        old=policy.original.decide(policy.BASE_POLICY,**deepcopy(inputs));new=policy.decide(policy.NAME,**deepcopy(inputs))
        self.assertEqual(new['evidence'].pop('cohort_pairscope'),dict(schema_version=1,mode='horizon_fallback',evaluated=False,intervened=False))
        new['evidence']['policy']=policy.BASE_POLICY;self.assertEqual(new,old)


if __name__=='__main__':unittest.main()
