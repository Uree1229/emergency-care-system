"""Synthetic uniform rescoring and unchanged protected selection orchestration."""
from copy import deepcopy
from functools import lru_cache
import unittest
from unittest.mock import patch
from inquiry_lab import cohort_envelope_policy as policy
from test_inquiry_lab_cohort_waitpareto_policy import fixture as previous_fixture,invoke as previous_invoke


@lru_cache(maxsize=1)
def _source_fixture():
    args,anchor,higher,lower=previous_fixture(False)
    return args[:5]+(previous_invoke(args),),anchor,higher,lower


def fixture():
    args,anchor,higher,lower=deepcopy(_source_fixture());j,ms,ts,p,owners,parent=args
    for row in j['eligible']:row['job']['policy_name']=policy.NAME
    for member in ms+ts:member['workflow_snapshot']['job']['policy_name']=policy.NAME
    for owner in owners['inventory']:
        if owner['job'] is not None and owner['job']['policy_name']==policy.BASE_POLICY:owner['job']['policy_name']=policy.NAME
    return (j,ms,ts,p,owners,parent),anchor,higher,lower


def invoke(args):
    j,ms,ts,p,owners,parent=args
    with patch.object(policy.original,'decide_joint',return_value=deepcopy(parent)):
        return policy.decide_joint(policy.NAME,members=ms,joint_context=j,tail_members=ts,priority_context=p,owner_context=owners)


class EnvelopePolicyTests(unittest.TestCase):
    def test_every_feasible_row_including_Stoploss_anchor_uniformly_rescored(self):
        args,anchor,_,_=fixture();before=deepcopy(args)
        with patch.object(policy.model,'rescore_row',wraps=policy.model.rescore_row) as score:result=invoke(args)
        e=result['evidence'];full=args[-1]['evidence']['original_pareto_proposal']['evidence']['original_fullmenu_proposal']['evidence']
        self.assertEqual(e['original_waitpareto_proposal'],args[-1]);self.assertEqual(args,before)
        self.assertEqual([r['row_index'] for r in e['new_row_scores']],full['menu_indices'])
        self.assertEqual(score.call_count,len(full['menu_indices']));self.assertIn(anchor,[c.args[0]['row_index'] for c in score.call_args_list])
        self.assertEqual(e['owner_probability_guard']['baseline_row_index'],anchor)
        self.assertEqual(e['protected_wait_selection']['baseline_row_index'],anchor)

    def test_current_pair_output_uses_new_metrics_and_existing_joint_table_only(self):
        args,anchor,_,target=fixture();real=policy.model.rescore_row
        def changed(row,prepared):
            result=real(row,prepared)
            for member in result['member_metrics']:
                member['metrics'].update(selection_probability=1. if row['row_index']==target else .5,capped_wait_seconds=0. if row['row_index']==target else 100.,total_cost_seconds=123.4567)
            result['tier_probabilities']={str(t):sum(m['metrics']['selection_probability'] for m in result['member_metrics'] if m['effective_priority']==t) for t in policy.TIERS}
            result['sum_capped_wait_seconds']=sum(m['metrics']['capped_wait_seconds'] for m in result['member_metrics']);return result
        with patch.object(policy.model,'rescore_row',side_effect=changed):result=invoke(args)
        e=result['evidence'];self.assertEqual(e['selected_index'],target);self.assertTrue(e['intervened'])
        full=e['original_waitpareto_proposal']['evidence']['original_pareto_proposal']['evidence']['original_fullmenu_proposal']['evidence']
        table=policy.model.fullmenu.parent_views(full['original_stoploss_proposal'])['joint']
        self.assertEqual([d['send'] for d in result['decisions']],table['combinations'][target]['hospitals'])
        self.assertTrue(all(d['evidence']['planned_expected_cost']==123.457 for d in result['decisions']))
        self.assertTrue(all(d['reason']=='cohort_envelope_current_only' for d in result['decisions']))

    def test_guard_uses_new_anchor_probabilities_not_old_probability_values(self):
        args,anchor,_,_=fixture();e=invoke(args)['evidence']
        by={r['row_index']:r for r in e['new_row_scores']};guard=e['owner_probability_guard']
        expected={m['patient_id']:m['metrics']['selection_probability'] for m in by[anchor]['member_metrics']}
        self.assertEqual({m['patient_id']:m['selection_probability'] for m in guard['baseline_owner_probabilities']},expected)
        self.assertTrue(guard['baseline_always_admissible'])

    def test_new_owner_probability_loss_remains_rejected_even_with_net_gain(self):
        args,anchor,_,target=fixture();real=policy.model.rescore_row
        def changed(row,prepared):
            result=real(row,prepared)
            for i,member in enumerate(result['member_metrics']):
                p=.49 if row['row_index']==target and i==0 else 1. if row['row_index']==target else .5
                member['metrics'].update(selection_probability=p,capped_wait_seconds=0. if row['row_index']==target else 100.)
            result['tier_probabilities']={str(t):sum(m['metrics']['selection_probability'] for m in result['member_metrics'] if m['effective_priority']==t) for t in policy.TIERS}
            result['sum_capped_wait_seconds']=sum(m['metrics']['capped_wait_seconds'] for m in result['member_metrics']);return result
        with patch.object(policy.model,'rescore_row',side_effect=changed):e=invoke(args)['evidence']
        self.assertIn(target,e['owner_probability_guard']['rejected_row_indices']);self.assertEqual(e['selected_index'],anchor)

    def test_original_Stoploss_index_breaks_new_score_ties(self):
        args,anchor,other,_=fixture();args[-1]['evidence']['selected_index']=other;real=policy.model.rescore_row
        def tied(row,prepared):
            result=real(row,prepared)
            for member in result['member_metrics']:member['metrics'].update(selection_probability=.5,capped_wait_seconds=100.)
            result['tier_probabilities']={str(t):sum(.5 for m in result['member_metrics'] if m['effective_priority']==t) for t in policy.TIERS}
            result['sum_capped_wait_seconds']=100.*len(result['member_metrics']);return result
        with patch.object(policy.model,'rescore_row',side_effect=tied):e=invoke(args)['evidence']
        self.assertEqual(e['base_selected_index'],other);self.assertEqual(e['selected_index'],anchor)

    def test_unevaluated_parent_never_rescores_or_changes_actions(self):
        args,_,_,_=fixture();old=args[-1]['evidence'];old.update(evaluated=False,protected_wait_selection=None)
        full=old['original_pareto_proposal']['evidence']['original_fullmenu_proposal']['evidence'];full.update(row_scores=[],menu_indices=[])
        with patch.object(policy.model,'rescore_row') as score:result=invoke(args)
        score.assert_not_called();self.assertEqual(result['evidence']['new_row_scores'],[])
        for new,prior in zip(result['decisions'],args[-1]['decisions']):
            new['evidence'].pop('cohort_envelope');new['evidence']['policy']=policy.BASE_POLICY;self.assertEqual(new,prior)

    def test_native_capture_rejected_before_parent_call(self):
        args,_,_,_=fixture();args[-2]['inventory'].pop()
        with patch.object(policy.original,'decide_joint') as old:
            with self.assertRaises(ValueError):invoke(args)
        old.assert_not_called()

    def test_original_anchor_mismatch_rejected(self):
        args,_,_,_=fixture();args[-1]['evidence']['stoploss_anchor_index']=-1
        with self.assertRaises(ValueError):invoke(args)

    def test_spec_and_singleton_fallback_are_exact_parent_core(self):
        old=policy.original.spec(policy.BASE_POLICY);new=policy.spec()
        for key in ('name','label'):old.pop(key);new.pop(key)
        self.assertEqual(old,new);args,_,_,_=fixture();inputs=args[1][0]['inputs']
        old=policy.original.decide(policy.BASE_POLICY,**deepcopy(inputs));new=policy.decide(policy.NAME,**deepcopy(inputs))
        self.assertEqual(new['evidence'].pop('cohort_envelope'),dict(schema_version=1,mode='horizon_fallback',evaluated=False,intervened=False))
        new['evidence']['policy']=policy.BASE_POLICY;self.assertEqual(new,old)


if __name__=='__main__':unittest.main()
