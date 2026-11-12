"""Synthetic inherited-tail guard scope and exact copied new-pair behavior."""
from copy import deepcopy
from functools import lru_cache
import unittest
from unittest.mock import patch
from inquiry_lab import cohort_tailwait_policy as policy
from test_inquiry_lab_cohort_pairshield_policy import fixture as shield_fixture,invoke as shield_invoke
from test_inquiry_lab_cohort_pairscope_policy import inherited_fixture as scope_inherited,invoke as scope_invoke


def promote(args,old_name,new_name):
    j,ms,ts,p,owners,parent=deepcopy(args)
    for row in j['eligible']:row['job']['policy_name']=new_name
    for member in ms+ts:member['workflow_snapshot']['job']['policy_name']=new_name
    for owner in owners['inventory']:
        if owner['job'] is not None and owner['job']['policy_name']==old_name:owner['job']['policy_name']=new_name
    return j,ms,ts,p,owners,parent


@lru_cache(maxsize=3)
def _source_fixture(kind='inherited'):
    if kind=='inherited':
        previous=scope_inherited();scope=scope_invoke(previous)
        args=promote(previous,policy.original.BASE_POLICY,policy.BASE_POLICY);args=args[:5]+(scope,)
    else:
        args,_=shield_fixture(count=2 if kind=='new' else 3)
    return args[:5]+(shield_invoke(args),)


def fixture(kind='inherited'):
    args=promote(_source_fixture(kind),policy.BASE_POLICY,policy.NAME)
    return args,args[-1]['evidence']['stoploss_anchor_index']


def invoke(args):
    j,ms,ts,p,owners,parent=args
    with patch.object(policy.original,'decide_joint',return_value=deepcopy(parent)):
        return policy.decide_joint(policy.NAME,members=ms,joint_context=j,tail_members=ts,priority_context=p,owner_context=owners)


def conflict_fixture():
    """Declared score orchestration mock: current improves while tails lose."""
    args,anchor=fixture();old=args[-1]['evidence'];scope=old['original_pairscope_proposal']['evidence'];env=scope['original_envelope_proposal']['evidence']
    rows=env['new_row_scores'];target=next(r['row_index'] for r in rows if r['row_index']!=anchor)
    current={m['patient_id'] for m in args[1]};tails={m['patient_id'] for m in args[2]}
    for row in rows:
        for member in row['member_metrics']:
            wait=75. if member['patient_id'] in current else 106. if member['patient_id'] in tails else 100.
            member['metrics'].update(selection_probability=.5,capped_wait_seconds=wait if row['row_index']==target else 100.,total_cost_seconds=123.4567)
        row['tier_probabilities']={str(t):sum(m['metrics']['selection_probability'] for m in row['member_metrics'] if m['effective_priority']==t) for t in (4,2,1)}
        row['sum_capped_wait_seconds']=sum(m['metrics']['capped_wait_seconds'] for m in row['member_metrics'])
    full=env['original_waitpareto_proposal']['evidence']['original_pareto_proposal']['evidence']['original_fullmenu_proposal']['evidence']
    p=model_probability=policy.model.selector.parent_guard.guard_rows(rows,baseline_index=anchor,owner_inventory=args[-2]['inventory'],menu_indices=full['menu_indices'],feasible_row_indices=full['feasible_row_indices'])
    selection=policy.model.selector.select_protected_wait(rows,baseline_index=anchor,owner_inventory=args[-2]['inventory'],admissible_row_indices=p['admissible_row_indices'],menu_indices=full['menu_indices'],feasible_row_indices=full['feasible_row_indices'])
    assert selection['selected_index']==target
    env.update(owner_probability_guard=model_probability,protected_wait_selection=selection,selected_index=target)
    scope.update(owner_probability_guard=deepcopy(p),protected_wait_selection=deepcopy(selection),selected_index=target,base_selected_index=target)
    old.update(selected_index=target,base_selected_index=target)
    return args,anchor,target


class TailwaitPolicyTests(unittest.TestCase):
    def test_only_inherited_active_tail_group_is_guarded_with_complete_rows(self):
        args,_=fixture();before=deepcopy(args);e=invoke(args)['evidence']
        self.assertTrue(e['tailwait_evaluated']);self.assertEqual(e['original_pairshield_proposal'],args[-1]);self.assertEqual(args,before)
        scope=args[-1]['evidence']['original_pairscope_proposal']['evidence'];env=scope['original_envelope_proposal']['evidence']
        self.assertEqual(e['protected_tailwait_selection']['original_probability_domain_selection'],env['protected_wait_selection'])
        self.assertEqual(e['owner_wait_guard']['compared_owner_count'],len(args[-2]['inventory']))

    def test_current_pair_gain_cannot_compensate_tail_owner_wait_regression(self):
        args,anchor,target=conflict_fixture();before=deepcopy(args)
        with patch.object(policy.original.original.model,'score_row',side_effect=AssertionError('no score rebuild')):result=invoke(args)
        e=result['evidence'];self.assertEqual(args,before);self.assertEqual(e['base_selected_index'],target);self.assertEqual(e['selected_index'],anchor)
        self.assertTrue(e['intervened']);self.assertIn(target,e['owner_wait_guard']['rejected_row_indices'])

    def test_changed_action_is_current_pair_from_original_table_with_fixed_caps(self):
        args,anchor,_=conflict_fixture();result=invoke(args);scope=args[-1]['evidence']['original_pairscope_proposal']['evidence'];env=scope['original_envelope_proposal']['evidence']
        full=env['original_waitpareto_proposal']['evidence']['original_pareto_proposal']['evidence']['original_fullmenu_proposal']['evidence']
        table=policy.original.original.model.fullmenu.parent_views(full['original_stoploss_proposal'])['joint']
        self.assertEqual([d['send'] for d in result['decisions']],table['combinations'][anchor]['hospitals'])
        self.assertTrue(all(d['evidence']['planned_expected_cost']==123.457 for d in result['decisions']))
        self.assertTrue(all(d['reason']=='cohort_tailwait_current_only' for d in result['decisions']))
        self.assertEqual([len(d['send']) for d in result['decisions']],table['cardinalities'])

    def test_new_no_tail_pair_copies_parent_without_wait_guard(self):
        args,_=fixture('new');self.assertEqual(args[2],[])
        with patch.object(policy.model,'guard_rows') as guard,patch.object(policy.model,'select_wait_guarded') as select:result=invoke(args)
        guard.assert_not_called();select.assert_not_called();self.assertFalse(result['evidence']['tailwait_evaluated'])
        for new,old in zip(result['decisions'],args[-1]['decisions']):
            new['evidence'].pop('cohort_tailwait');new['evidence']['policy']=policy.BASE_POLICY;self.assertEqual(new,old)

    def test_inactive_one_tail_path_copies_parent(self):
        args,_=fixture('one_tail')
        with patch.object(policy.model,'guard_rows') as guard:result=invoke(args)
        guard.assert_not_called();self.assertFalse(result['evidence']['tailwait_evaluated'])
        for new,old in zip(result['decisions'],args[-1]['decisions']):
            new['evidence'].pop('cohort_tailwait');new['evidence']['policy']=policy.BASE_POLICY;self.assertEqual(new,old)

    def test_missing_owner_and_anchor_mismatch_rejected(self):
        args,_=fixture();args[-2]['inventory'].pop()
        with patch.object(policy.original,'decide_joint') as parent:
            with self.assertRaises(ValueError):invoke(args)
        parent.assert_not_called()
        args,_=fixture();args[-1]['evidence']['stoploss_anchor_index']=-1
        with self.assertRaises(ValueError):invoke(args)

    def test_spec_and_singleton_H_fallback_remain_parent_core(self):
        old=policy.original.spec(policy.BASE_POLICY);new=policy.spec()
        for key in ('name','label'):old.pop(key);new.pop(key)
        self.assertEqual(old,new);args,_=fixture();inputs=args[1][0]['inputs']
        old=policy.original.decide(policy.BASE_POLICY,**deepcopy(inputs));new=policy.decide(policy.NAME,**deepcopy(inputs))
        self.assertEqual(new['evidence'].pop('cohort_tailwait'),dict(schema_version=1,mode='horizon_fallback',evaluated=False,intervened=False))
        new['evidence']['policy']=policy.BASE_POLICY;self.assertEqual(new,old)


if __name__=='__main__':unittest.main()
