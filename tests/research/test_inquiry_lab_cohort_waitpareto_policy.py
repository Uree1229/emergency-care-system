"""Tiny synthetic immutable-parent and protected wait-first output checks."""
from copy import deepcopy
from functools import lru_cache
import unittest
from unittest.mock import patch
from inquiry_lab import cohort_waitpareto_policy as policy
from test_inquiry_lab_cohort_pareto_policy import objective_fixture as pareto_fixture,invoke as pareto_invoke


@lru_cache(maxsize=2)
def _source_fixture(wait_gain=True):
    args,anchor,higher=pareto_fixture();full=args[-1]['evidence']
    lower=next(i for i in full['menu_indices'] if i not in (anchor,higher))
    for row in full['row_scores']:
        index=row['row_index'];prob=.5001 if wait_gain and index==higher else .5
        wait=101. if wait_gain and index==higher else 90. if wait_gain and index==lower else 100.
        for member in row['member_metrics']:
            member['metrics'].update(selection_probability=prob,capped_wait_seconds=wait,total_cost_seconds=123.4567)
        row['tier_probabilities']={str(t):sum((m['metrics']['selection_probability'] for m in row['member_metrics'] if m['effective_priority']==t),0.0) for t in (4,2,1)}
        row['sum_capped_wait_seconds']=sum(m['metrics']['capped_wait_seconds'] for m in row['member_metrics'])
    parent=pareto_invoke(args)
    return args[:5]+(parent,anchor,higher,lower)


def fixture(wait_gain=True):
    j,ms,ts,p,owners,parent,anchor,higher,lower=deepcopy(_source_fixture(wait_gain))
    for item in j['eligible']:item['job']['policy_name']=policy.NAME
    for member in ms+ts:member['workflow_snapshot']['job']['policy_name']=policy.NAME
    for owner in owners['inventory']:
        if owner['job'] is not None and owner['job']['policy_name']==policy.BASE_POLICY:owner['job']['policy_name']=policy.NAME
    return (j,ms,ts,p,owners,parent),anchor,higher,lower


def invoke(args):
    j,ms,ts,p,owners,parent=args
    with patch.object(policy.original,'decide_joint',return_value=deepcopy(parent)):
        return policy.decide_joint(policy.NAME,members=ms,joint_context=j,tail_members=ts,priority_context=p,owner_context=owners)


class WaitparetoPolicyTests(unittest.TestCase):
    def test_parent_prefers_higherP_worseW_new_selector_prefers_lower_protectedW(self):
        args,anchor,higher,lower=fixture();before=deepcopy(args);result=invoke(args);e=result['evidence']
        self.assertEqual(args[-1]['evidence']['selected_index'],higher)
        self.assertEqual(e['base_selected_index'],higher);self.assertEqual(e['stoploss_anchor_index'],anchor)
        self.assertEqual(e['selected_index'],lower);self.assertTrue(e['intervened'])
        self.assertEqual(args,before);self.assertEqual(e['original_pareto_proposal'],args[-1])
        self.assertFalse(e['protected_wait_selection']['additional_baseline_wait_cap_applied'])
        self.assertTrue(e['protected_wait_selection']['selected_wait_within_baseline_tolerance'])

    def test_current_pair_output_rebuilt_from_existing_H_rows_only(self):
        args,anchor,higher,lower=fixture();result=invoke(args)
        full=args[-1]['evidence']['original_fullmenu_proposal']['evidence']
        joint=policy.original.original.model.parent_views(full['original_stoploss_proposal'])['joint']
        self.assertEqual([d['send'] for d in result['decisions']],joint['combinations'][lower]['hospitals'])
        for d in result['decisions']:
            self.assertEqual(d['reason'],'cohort_waitpareto_current_only')
            self.assertEqual(d['evidence']['planned_expected_cost'],123.457)
            self.assertTrue(all(r['planned_offset_seconds'] in (0,None) for r in d['evidence']['candidate_plan']))
            self.assertFalse(set(d['evidence'])&{'cohort_pareto','cohort_fullmenu','cohort_stoploss','cohort_followplan'})

    def test_no_new_scores_or_guard_rebuilt_and_no_duplicate_full_scores(self):
        args,_,_,_=fixture()
        with (patch.object(policy.original.original.model,'score_added_row') as score,
                patch.object(policy.original.model,'guard_rows') as guard):result=invoke(args)
        score.assert_not_called();guard.assert_not_called()
        e=result['evidence'];self.assertNotIn('row_scores',e);self.assertNotIn('owner_probability_guard',e)
        self.assertEqual(e['original_pareto_proposal'],args[-1])

    def test_zero_effect_case_copies_exact_parent_core(self):
        args,anchor,_,_=fixture(False);result=invoke(args)
        self.assertEqual(result['evidence']['selected_index'],anchor);self.assertFalse(result['evidence']['intervened'])
        for new,old in zip(result['decisions'],args[-1]['decisions']):
            new['evidence'].pop('cohort_waitpareto');new['evidence']['policy']=policy.BASE_POLICY;self.assertEqual(new,old)

    def test_parent_admissible_set_cannot_omit_a_better_protected_row(self):
        args,anchor,higher,lower=fixture()
        args[-1]['evidence']['owner_probability_guard']['admissible_row_indices'].remove(lower)
        with self.assertRaises(ValueError):invoke(args)

    def test_unevaluated_parent_retains_actions_and_activation(self):
        args,_,_,_=fixture();old=args[-1]['evidence'];old.update(evaluated=False,owner_probability_guard=None)
        full=old['original_fullmenu_proposal']['evidence'];full.update(row_scores=[],menu_indices=[])
        with patch.object(policy.model,'select_protected_wait') as select:result=invoke(args)
        select.assert_not_called();self.assertIsNone(result['evidence']['protected_wait_selection'])
        for new,prior in zip(result['decisions'],args[-1]['decisions']):
            new['evidence'].pop('cohort_waitpareto');new['evidence']['policy']=policy.BASE_POLICY;self.assertEqual(new,prior)

    def test_missing_partition_or_wrong_anchor_rejected(self):
        args,_,_,_=fixture();args[-1]['evidence']['original_fullmenu_proposal']['evidence']['row_scores'][0]['member_metrics'].pop()
        with self.assertRaises(ValueError):invoke(args)
        args,_,_,_=fixture();args[-1]['evidence']['stoploss_anchor_index']=-1
        with self.assertRaises(ValueError):invoke(args)

    def test_native_capture_validation_precedes_parent_call(self):
        args,_,_,_=fixture();args[-2]['inventory'].pop()
        with patch.object(policy.original,'decide_joint') as parent:
            with self.assertRaises(ValueError):invoke(args)
        parent.assert_not_called()

    def test_public_identity_renaming_preserves_scores_and_selection(self):
        args,_,_,_=fixture();result=invoke(args)
        names={owner['patient']['id']:'RENAMED_'+str(owner['patient']['id']) for owner in args[-2]['inventory']}
        def rename(value):
            if isinstance(value,list):return [rename(item) for item in value]
            if isinstance(value,tuple):return tuple(rename(item) for item in value)
            if isinstance(value,dict):return {key:rename(item) for key,item in value.items()}
            return names.get(value,value) if isinstance(value,str) else value
        self.assertEqual(invoke(rename(args)),rename(result))

    def test_spec_and_unpaired_fallback_preserve_parent_core(self):
        before=policy.original.spec(policy.BASE_POLICY);after=policy.spec()
        for key in ('name','label'):before.pop(key);after.pop(key)
        self.assertEqual(before,after)
        args,_,_,_=fixture();inputs=args[1][0]['inputs']
        old=policy.original.decide(policy.BASE_POLICY,**deepcopy(inputs));new=policy.decide(policy.NAME,**deepcopy(inputs))
        self.assertEqual(new['evidence'].pop('cohort_waitpareto'),dict(schema_version=1,mode='horizon_fallback',evaluated=False,intervened=False))
        new['evidence']['policy']=policy.BASE_POLICY;self.assertEqual(new,old)


if __name__=='__main__':unittest.main()
