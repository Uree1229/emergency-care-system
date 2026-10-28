"""Synthetic narrow fallback scope and unchanged Tailwait group contracts."""
from copy import deepcopy
from functools import lru_cache
import math
import unittest
from unittest.mock import patch
from inquiry_lab import cohort_swap_policy as policy
from test_inquiry_lab_cohort_tailwait_policy import fixture as old_fixture,invoke as old_invoke,promote
from test_inquiry_lab_horizon_policy import inputs
from test_inquiry_lab_cohort_swap_model import own_inputs,shaped_plan


@lru_cache(maxsize=3)
def _source(kind='inherited'):
    args,_=old_fixture(kind)
    return args[:5]+(old_invoke(args),)


def fixture(kind='inherited'):
    args=promote(_source(kind),policy.BASE_POLICY,policy.NAME)
    return args,args[-1]['evidence']['stoploss_anchor_index']


def invoke(args):
    j,ms,ts,p,owners,parent=args
    with patch.object(policy.original,'decide_joint',return_value=deepcopy(parent)):
        return policy.decide_joint(policy.NAME,members=ms,joint_context=j,tail_members=ts,priority_context=p,owner_context=owners)


class SwapPolicyTests(unittest.TestCase):
    def test_configuration_is_accepted_Tailwait_only(self):
        a=policy.original.spec();b=policy.spec()
        for key in ('name','label'):a.pop(key);b.pop(key)
        self.assertEqual(a,b)
        with self.assertRaises(ValueError):policy.spec(policy.BASE_POLICY)

    def test_full_group_inherited_new_and_one_tail_paths_are_exact_Tailwait_copy(self):
        for kind in ('inherited','new','one_tail'):
            args,_=fixture(kind);before=deepcopy(args)
            with patch.object(policy.model,'evaluate',side_effect=AssertionError('first group must not swap')):
                value=invoke(args)
            self.assertEqual(args,before);self.assertEqual(value['evidence']['original_tailwait_proposal'],args[-1])
            for new,old in zip(value['decisions'],args[-1]['decisions']):
                self.assertFalse(new['evidence'].pop('cohort_swap')['evaluated'])
                new['evidence']['policy']=policy.BASE_POLICY;self.assertEqual(new,old)

    def test_first_singleton_and_empty_own_fallback_copy_exact_parent_without_scoring(self):
        for args in (inputs(),own_inputs(last_contact_at=None),inputs(last_contact_at=1000)):
            parent=policy.original.decide(policy.BASE_POLICY,**deepcopy(args))
            with patch.object(policy.model,'evaluate',side_effect=AssertionError('copied path must not evaluate')):
                value=policy.decide(policy.NAME,**deepcopy(args))
            proof=value['evidence'].pop('cohort_swap');self.assertFalse(proof['evaluated'])
            self.assertIsNone(proof['swap_projection']);value['evidence']['policy']=policy.BASE_POLICY
            self.assertEqual(value,parent)

    def test_invalid_last_contact_does_not_open_new_scope(self):
        for invalid in (None,True,'1010',math.nan,math.inf,1016):
            args=own_inputs(last_contact_at=invalid)
            with patch.object(policy.model,'evaluate') as score:value=policy.decide(policy.NAME,**args)
            score.assert_not_called();self.assertFalse(value['evidence']['cohort_swap']['evaluated'])

    def test_genuine_followup_stores_complete_parent_and_all_trials_without_replanning(self):
        args=own_inputs();before=deepcopy(args);parent=policy.original.decide(policy.BASE_POLICY,**args)
        value=policy.decide(policy.NAME,**args);proof=value['evidence']['cohort_swap']
        self.assertEqual(args,before);self.assertTrue(proof['evaluated'])
        self.assertEqual(proof['original_tailwait_proposal'],parent)
        self.assertEqual(proof['swap_projection'],policy.model.evaluate(args,parent))
        self.assertLessEqual(len(proof['swap_projection']['trials']),9)
        self.assertEqual(value['next_review_at'],parent['next_review_at']);self.assertEqual(value['reason'],parent['reason'])

    def test_declared_score_orchestration_changes_only_one_plan_exchange_and_current_send(self):
        args,_=shaped_plan();parent=policy.original.decide(policy.BASE_POLICY,**args)
        # Explicit orchestration mock: not a valid independent full H proof.
        for i,row in enumerate(parent['evidence']['candidate_plan']):row['planned_offset_seconds']=0 if i==0 else None
        parent['send']=[parent['evidence']['candidate_plan'][0]['hospital']]
        parent['evidence']['sent_hospitals']=list(parent['send'])
        plan={r['hospital']:r['planned_offset_seconds'] for r in parent['evidence']['candidate_plan']}
        keys=list(plan);plan[keys[0]],plan[keys[1]]=plan[keys[1]],plan[keys[0]]
        projection=dict(selection=dict(selected_trial_index=0),selected_plan=plan,selected_metrics=dict(total_cost_seconds=123.456789))
        before=deepcopy(parent)
        with patch.object(policy.original,'decide',return_value=parent),patch.object(policy.model,'evaluate',return_value=projection):
            value=policy.decide(policy.NAME,**args)
        self.assertEqual(parent,before);self.assertEqual(value['send'],[keys[1]])
        self.assertEqual(value['evidence']['planned_expected_cost'],123.457)
        self.assertEqual({r['hospital']:r['planned_offset_seconds'] for r in value['evidence']['candidate_plan']},plan)
        self.assertEqual(value['evidence']['cohort_swap']['original_tailwait_proposal'],before)
        self.assertTrue(value['evidence']['cohort_swap']['send_changed'])
        self.assertEqual(value['next_review_at'],parent['next_review_at'])

    def test_no_admitted_exchange_is_exact_parent_core_including_reason_and_plan(self):
        args=own_inputs(4);parent=policy.original.decide(policy.BASE_POLICY,**args)
        value=policy.decide(policy.NAME,**args);proof=value['evidence'].pop('cohort_swap')
        self.assertTrue(proof['evaluated']);self.assertIsNone(proof['selected_trial_index'])
        value['evidence']['policy']=policy.BASE_POLICY;self.assertEqual(value,parent)

    def test_generator_inputs_are_materialized_once_and_global_H_unchanged(self):
        args=own_inputs();fn=policy.HORIZON.decide;a=policy.decide(policy.NAME,**args)
        generators=dict(args,**{k:iter(args[k]) for k in ('candidates','observations','pending','patient_pending')})
        self.assertEqual(a,policy.decide(policy.NAME,**generators));self.assertIs(policy.HORIZON.decide,fn)

    def test_complete_owner_and_native_identity_guards_precede_parent_group(self):
        args,_=fixture();args[-2]['inventory'].pop()
        with patch.object(policy.original,'decide_joint') as parent:
            with self.assertRaises(ValueError):invoke(args)
        parent.assert_not_called()


if __name__=='__main__':unittest.main()
