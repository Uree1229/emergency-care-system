"""Synthetic complete Fullmenu scores, owner guard and current-only outputs."""
from copy import deepcopy
from functools import lru_cache
import unittest
from unittest.mock import patch
from inquiry_lab import cohort_pareto_policy as policy
from test_inquiry_lab_cohort_fullmenu_policy import fixture as fullmenu_fixture,invoke as fullmenu_invoke


@lru_cache(maxsize=1)
def _source_fixture():
    args=fullmenu_fixture();return args[:5]+(fullmenu_invoke(args),)


def fixture():
    j,ms,ts,p,owners,parent=deepcopy(_source_fixture())
    for item in j['eligible']:item['job']['policy_name']=policy.NAME
    for member in ms+ts:member['workflow_snapshot']['job']['policy_name']=policy.NAME
    for owner in owners['inventory']:
        if owner['job'] is not None and owner['job']['policy_name']==policy.BASE_POLICY:owner['job']['policy_name']=policy.NAME
    return j,ms,ts,p,owners,parent


def invoke(args):
    j,ms,ts,p,owners,parent=args
    with patch.object(policy.original,'decide_joint',return_value=deepcopy(parent)):
        return policy.decide_joint(policy.NAME,members=ms,joint_context=j,tail_members=ts,priority_context=p,owner_context=owners)


def objective_fixture(*,regression=False):
    """Explicit final-score orchestration mock, not a natural outcome claim."""
    args=fixture();e=args[-1]['evidence'];anchor=e['base_selected_index']
    target=next(index for index in e['menu_indices'] if index!=anchor)
    for row in e['row_scores']:
        for member in row['member_metrics']:
            member['metrics'].update(selection_probability=.5,capped_wait_seconds=100.,total_cost_seconds=123.4567)
        row['tier_probabilities']={str(t):sum(.5 for m in row['member_metrics'] if m['effective_priority']==t) for t in policy.TIERS}
        row['sum_capped_wait_seconds']=100.*len(row['member_metrics'])
    e['selected_index']=anchor
    if regression:
        chosen=next(row for row in e['row_scores'] if row['row_index']==target)
        chosen['member_metrics'][0]['metrics']['selection_probability']=.49
        chosen['member_metrics'][1]['metrics']['selection_probability']=1.
        chosen['tier_probabilities']['4']+=.49;chosen['sum_capped_wait_seconds']=0.
        e['selected_index']=target
    return args,anchor,target


class ParetoPolicyTests(unittest.TestCase):
    def test_fullmenu_proposal_and_exhaustive_evidence_exact_and_no_rescoring(self):
        args=fixture();before=deepcopy(args)
        with (patch.object(policy.original.model,'score_added_row') as added,
                patch.object(policy.original.model.stoploss,'score_first_cohort') as scorer):
            result=invoke(args)
        added.assert_not_called();scorer.assert_not_called()
        self.assertEqual(args,before);self.assertEqual(result['evidence']['original_fullmenu_proposal'],args[-1])
        guard=result['evidence']['owner_probability_guard']
        self.assertEqual([r['row_index'] for r in guard['row_checks']],args[-1]['evidence']['menu_indices'])
        self.assertEqual(guard['checked_row_count'],len(args[-1]['evidence']['row_scores']))
        self.assertEqual(guard['compared_owner_count'],len(args[-2]['inventory']))

    def test_net_gain_owner_loss_rejected_and_Stoploss_anchor_restored(self):
        args,anchor,target=objective_fixture(regression=True);result=invoke(args);e=result['evidence']
        self.assertEqual(e['base_selected_index'],target);self.assertEqual(e['stoploss_anchor_index'],anchor)
        self.assertEqual(e['selected_index'],anchor);self.assertTrue(e['intervened'])
        self.assertIn(target,e['owner_probability_guard']['rejected_row_indices'])
        joint=policy.original.model.parent_views(args[-1]['evidence']['original_stoploss_proposal'])['joint']
        self.assertEqual([d['send'] for d in result['decisions']],joint['combinations'][anchor]['hospitals'])
        for decision in result['decisions']:
            self.assertEqual(decision['reason'],'cohort_pareto_current_only')
            self.assertEqual(decision['evidence']['planned_expected_cost'],123.457)
            self.assertFalse(set(decision['evidence'])&{'cohort_fullmenu','cohort_stoploss','cohort_followplan','cohort_recourse'})
            self.assertTrue(all(r['planned_offset_seconds'] in (0,None) for r in decision['evidence']['candidate_plan']))

    def test_zero_regressions_exact_parent_core_and_original_Stoploss_tie_anchor(self):
        args,anchor,target=objective_fixture();result=invoke(args);e=result['evidence']
        self.assertEqual(e['selected_index'],anchor);self.assertFalse(e['intervened'])
        self.assertEqual(e['owner_probability_guard']['rejected_row_indices'],[])
        self.assertEqual(e['owner_probability_guard']['admissible_row_indices'],args[-1]['evidence']['menu_indices'])
        for new,old in zip(result['decisions'],args[-1]['decisions']):
            new['evidence'].pop('cohort_pareto');new['evidence']['policy']=policy.BASE_POLICY;self.assertEqual(new,old)

    def test_tie_anchor_is_Stoploss_even_if_Fullmenu_actual_is_another_row(self):
        args,anchor,target=objective_fixture();args[-1]['evidence']['selected_index']=target
        result=invoke(args)
        self.assertEqual(result['evidence']['selected_index'],anchor)
        self.assertEqual(result['evidence']['selection']['selected_index'],anchor)
        self.assertTrue(result['evidence']['selection']['base_selected_tied'])

    def test_admissible_wait_gain_can_be_selected(self):
        args,anchor,target=objective_fixture();chosen=next(r for r in args[-1]['evidence']['row_scores'] if r['row_index']==target)
        chosen['sum_capped_wait_seconds']=0.
        result=invoke(args)
        self.assertEqual(result['evidence']['selected_index'],target)
        self.assertIn(target,result['evidence']['owner_probability_guard']['admissible_row_indices'])

    def test_all_rows_and_residual_owner_checked(self):
        args,anchor,target=objective_fixture();chosen=next(r for r in args[-1]['evidence']['row_scores'] if r['row_index']==target)
        owner=next(m for m in chosen['member_metrics'] if m['patient_id']=='OWNER0')
        owner['metrics']['selection_probability']=.1
        proof=invoke(args)['evidence']['owner_probability_guard']
        row=next(r for r in proof['row_checks'] if r['row_index']==target)
        self.assertFalse(row['admissible']);self.assertEqual(row['owner_regressions'][0]['patient_id'],'OWNER0')
        self.assertTrue(all(r['verified_owner_count']==len(args[-2]['inventory']) for r in proof['row_checks']))

    def test_public_owner_identity_renaming_changes_no_scores_or_choice(self):
        args=fixture();result=invoke(args)
        names={owner['patient']['id']:'RENAMED_'+str(owner['patient']['id']) for owner in args[-2]['inventory']}
        def rename(value):
            if isinstance(value,list):return [rename(item) for item in value]
            if isinstance(value,tuple):return tuple(rename(item) for item in value)
            if isinstance(value,dict):return {key:rename(item) for key,item in value.items()}
            return names.get(value,value) if isinstance(value,str) else value
        self.assertEqual(invoke(rename(args)),rename(result))

    def test_unevaluated_parent_actions_and_gates_unchanged(self):
        args=list(fixture());old=args[-1]['evidence'];old.update(evaluated=False,menu_indices=[],row_scores=[],feasible_row_indices=[])
        with patch.object(policy.model,'guard_rows') as guard:result=invoke(args)
        guard.assert_not_called();self.assertIsNone(result['evidence']['owner_probability_guard'])
        self.assertIsNone(result['evidence']['selection'])
        for new,prior in zip(result['decisions'],args[-1]['decisions']):
            new['evidence'].pop('cohort_pareto');new['evidence']['policy']=policy.BASE_POLICY;self.assertEqual(new,prior)

    def test_missing_duplicate_owner_partition_or_anchor_rejected(self):
        args=fixture();args[-1]['evidence']['row_scores'][0]['member_metrics'].pop()
        with self.assertRaises(ValueError):invoke(args)
        args=fixture();bad=args[-1]['evidence']['row_scores'][0]['member_metrics'];bad[-1]=deepcopy(bad[0])
        with self.assertRaises(ValueError):invoke(args)
        args=fixture();args[-1]['evidence']['base_selected_index']=-1
        with self.assertRaises(ValueError):invoke(args)

    def test_native_capture_validation_precedes_parent(self):
        args=list(fixture());args[-2]['inventory'].pop()
        with patch.object(policy.original,'decide_joint') as parent:
            with self.assertRaises(ValueError):invoke(args)
        parent.assert_not_called()

    def test_spec_selector_and_fallback_are_exact_parent_core(self):
        before=policy.original.spec(policy.BASE_POLICY);after=policy.spec()
        for key in ('name','label'):before.pop(key);after.pop(key)
        self.assertEqual(before,after);self.assertIs(policy.D._select,policy.original.D._select)
        inputs=fixture()[1][0]['inputs']
        old=policy.original.decide(policy.BASE_POLICY,**deepcopy(inputs));new=policy.decide(policy.NAME,**deepcopy(inputs))
        self.assertEqual(new['evidence'].pop('cohort_pareto'),dict(schema_version=1,mode='horizon_fallback',evaluated=False,intervened=False))
        new['evidence']['policy']=policy.BASE_POLICY;self.assertEqual(new,old)


if __name__=='__main__':unittest.main()
