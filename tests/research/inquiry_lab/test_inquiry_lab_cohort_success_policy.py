"""Synthetic fixed-template objective and output checks only."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from inquiry_lab import cohort_success_policy as policy,cohort_success_context as context
from test_inquiry_lab_cohort_success_context import fixture
from test_inquiry_lab_joint_budget_policy import tail_proposal


def mock_budget(members,joint,tails,cancel=True):
    """Force the rare outer B branch; projected arithmetic remains frozen.

The forced flag is an orchestration mock, not a saved independently audited
policy artifact. Independent-auditor tests construct their own valid source.
"""
    pair=context.base_members_projection(members);ctx=context.base_context_projection(joint)
    tail=context.base_members_projection(tails)
    source=tail_proposal(pair,ctx,tail)
    with patch.object(policy.original.original,'decide_joint',return_value=source):
        result=policy.original.decide_joint(policy.BASE_POLICY,members=pair,joint_context=ctx,tail_members=tail)
    result['evidence']['cancelled_tail_veto']=cancel
    if cancel:
        original=source['evidence']['original_joint_proposal']
        result['evidence']['actual_selected_index']=original['evidence']['selected_index']
        result['decisions']=deepcopy(original['decisions'])
        for decision in result['decisions']:decision['evidence']['policy']=policy.BASE_POLICY
    return result


def score(index,p4=1.,p2=0.,p1=0.,wait=100.,members=None):
    return dict(row_index=index,member_metrics=deepcopy(members or []),
        tier_probabilities={'4':p4,'2':p2,'1':p1},sum_capped_wait_seconds=wait)


def invoke(joint,members,tails,priority,budget):
    with patch.object(policy.original,'decide_joint',return_value=budget):
        return policy.decide_joint(policy.NAME,members=members,joint_context=joint,
            tail_members=tails,priority_context=priority)


class CohortSuccessPolicyTests(unittest.TestCase):
    def test_spec_and_fallback_preserve_complete_budget_core(self):
        old,new=policy.original.spec(),policy.spec()
        for key in ('name','label'):old.pop(key);new.pop(key)
        self.assertEqual(old,new)
        _,_,members,_,_=fixture()
        old=policy.original.decide(policy.BASE_POLICY,**deepcopy(members[0]['inputs']))
        new=policy.decide(policy.NAME,**deepcopy(members[0]['inputs']))
        self.assertEqual(new['evidence'].pop('cohort_success'),
            dict(schema_version=1,mode='horizon_fallback',evaluated=False,intervened=False))
        new['evidence']['policy']=policy.BASE_POLICY
        self.assertEqual(new,old)

    def test_priority_success_precedes_wait_and_lower_priority_counts(self):
        selected=policy._select([score(0,p4=.8,p2=2,wait=1),score(1,p4=.9,p2=0,wait=1000)],0)
        self.assertEqual(selected['selected_index'],1)
        selected=policy._select([score(0,p4=1,p2=.3,p1=3,wait=1),score(1,p4=1,p2=.4,p1=0,wait=1000)],0)
        self.assertEqual(selected['selected_index'],1)
        selected=policy._select([score(0,p1=.1,wait=1),score(1,p1=.2,wait=1000)],0)
        self.assertEqual(selected['selected_index'],1)

    def test_global_staged_tolerance_filters_then_wait_then_B_or_table_order(self):
        rows=[score(0,p4=1-1.5e-12,wait=1),score(1,p4=1-.75e-12,wait=100),score(2,p4=1,wait=100)]
        result=policy._select(rows,2)
        self.assertEqual(result['priority_filters'][0]['surviving_row_indices'],[1,2])
        self.assertEqual(result['selected_index'],2)
        self.assertEqual(policy._select(rows,0)['selected_index'],1)
        self.assertEqual(policy._select([score(0,wait=1),score(1,wait=1+.5e-9)],1)['selected_index'],1)
        self.assertEqual(policy._select([score(0,wait=1),score(1,wait=1+2e-9)],1)['selected_index'],0)

    def test_noncancelled_B_skips_template_and_scoring(self):
        _,joint,members,tails,priority=fixture()
        budget=mock_budget(members,joint,tails,cancel=False)
        with patch.object(policy,'_template') as template,patch.object(policy,'_score') as scored:
            result=invoke(joint,members,tails,priority,budget)
        template.assert_not_called();scored.assert_not_called()
        self.assertFalse(result['evidence']['evaluated'])
        self.assertFalse(result['evidence']['intervened'])
        self.assertIsNone(result['evidence']['continuation_template'])
        self.assertEqual(result['evidence']['row_scores'],[])
        for actual,old in zip(result['decisions'],budget['decisions']):
            actual['evidence'].pop('cohort_success');actual['evidence']['policy']=policy.BASE_POLICY
            self.assertEqual(actual,old)

    def test_template_copies_winner_members_and_singleton_H_exactly_once(self):
        _,joint,members,tails,_=fixture(7)
        budget=mock_budget(members,joint,tails)
        before=deepcopy((budget,tails))
        original=policy.HORIZON.decide
        with patch.object(policy.HORIZON,'decide',wraps=original) as h:
            template=policy._template(budget,tails)
        self.assertEqual(h.call_count,1)
        self.assertEqual((budget,tails),before)
        reference=budget['evidence']['projections']['winner']
        self.assertEqual(len(template['paired_steps']),2)
        for actual,source in zip(template['paired_steps'],reference['steps']):
            self.assertEqual(actual['members'],source['members'])
            self.assertEqual(actual['horizon_proposals'],source['proposal']['evidence']['baseline_proposals'])
            self.assertEqual(actual['sends'],[d['send'] for d in source['proposal']['decisions']])
        self.assertEqual(template['singleton']['member']['patient_id'],'P6')
        self.assertEqual(template['singleton']['member']['inputs']['pending'],reference['final_pending'])
        self.assertEqual(template['singleton']['send'],template['singleton']['horizon_proposal']['send'])

    def test_even_tail_template_performs_no_new_H_search(self):
        _,joint,members,tails,_=fixture(4)
        budget=mock_budget(members,joint,tails)
        with patch.object(policy.HORIZON,'decide') as h:template=policy._template(budget,tails)
        h.assert_not_called();self.assertIsNone(template['singleton'])

    def test_all_feasible_rows_are_scored_without_using_recorded_current_metrics(self):
        _,joint,members,tails,priority=fixture(4)
        budget=mock_budget(members,joint,tails)
        original=budget['evidence']['original_tail_proposal']['evidence']['original_joint_proposal']['evidence']
        for row in original['combinations']:
            for metric in row['member_metrics']:metric['selection_probability']=-123
        before=deepcopy((joint,members,tails,priority,budget))
        result=invoke(joint,members,tails,priority,budget)
        self.assertEqual((joint,members,tails,priority,budget),before)
        self.assertEqual([r['row_index'] for r in result['evidence']['row_scores']],
            [i for i,r in enumerate(original['combinations']) if r['feasible']])
        self.assertTrue(all(0<=m['metrics']['selection_probability']<=1
            for row in result['evidence']['row_scores'] for m in row['member_metrics']))
        self.assertEqual(result['evidence']['original_budget_proposal'],budget)

    def test_fixed_template_models_rebuild_common_ledger_without_reoptimizing(self):
        _,joint,members,tails,_=fixture(7)
        budget=mock_budget(members,joint,tails);template=policy._template(budget,tails)
        first=policy._first_members(members)
        original=budget['evidence']['original_tail_proposal']['evidence']['original_joint_proposal']['evidence']
        models=[policy.JOINT._model(m['inputs'],p) for m,p in zip(first,original['baseline_proposals'])]
        selected=[['H0'],['H1']]
        native_model=policy.JOINT._model
        with patch.object(policy.JOINT,'_model',wraps=native_model) as model, \
                patch.object(policy.JOINT,'decide_joint') as optimizer,patch.object(policy.HORIZON,'decide') as h:
            result=policy._score(0,selected,members=first,current_models=models,template=template,
                priorities={(str,f'P{i}'):4 for i in range(7)},now=1000)
        optimizer.assert_not_called();h.assert_not_called()
        self.assertEqual(len(result['member_metrics']),7)
        row=lambda hospital,pid:dict(hospital=hospital,patient=pid,submitted_at=1000)
        ledger=[row('H0','P0'),row('H1','P1')]
        calls=model.call_args_list
        for step_index,step in enumerate(template['paired_steps']):
            for index in (0,1):self.assertEqual(calls[2*step_index+index].args[0]['pending'],ledger)
            for person,send in zip(step['members'],step['sends']):
                ledger.extend(row(hospital,person['patient_id']) for hospital in send)
        self.assertEqual(calls[-1].args[0]['pending'],ledger)

    def test_cohort_probability_is_tier_sum_not_priority_weighted_scalar(self):
        _,joint,members,tails,_=fixture(4)
        budget=mock_budget(members,joint,tails);template=policy._template(budget,tails)
        first=policy._first_members(members)
        original=budget['evidence']['original_tail_proposal']['evidence']['original_joint_proposal']['evidence']
        models=[policy.JOINT._model(m['inputs'],p) for m,p in zip(first,original['baseline_proposals'])]
        tiers={(str,'P0'):4,(str,'P1'):2,(str,'P2'):1,(str,'P3'):1}
        result=policy._score(0,[['H0'],['H1']],members=first,current_models=models,template=template,priorities=tiers,now=1000)
        for tier in (4,2,1):self.assertEqual(result['tier_probabilities'][str(tier)],
            sum(m['metrics']['selection_probability'] for m in result['member_metrics'] if m['effective_priority']==tier))

    def test_intervened_output_has_only_new_metadata_and_actual_current_pair_sends(self):
        _,joint,members,tails,priority=fixture(4)
        budget=mock_budget(members,joint,tails)
        original=budget['evidence']['original_tail_proposal']['evidence']['original_joint_proposal']['evidence']
        baseline=original['baseline_index'];base=budget['evidence']['actual_selected_index']
        self.assertNotEqual(baseline,base)
        def scored(index,*args,**kwargs):
            metrics=[dict(patient_id=m['patient_id'],effective_priority=4,metrics=dict(
                total_cost_seconds=321.1234,selection_probability=.9,capped_wait_seconds=50)) for m in members]
            return score(index,p4=2 if index==baseline else 1,members=metrics)
        with patch.object(policy,'_score',side_effect=scored):result=invoke(joint,members,tails,priority,budget)
        self.assertTrue(result['evidence']['intervened'])
        self.assertEqual(result['evidence']['selected_index'],baseline)
        for i,decision in enumerate(result['decisions']):
            self.assertEqual(decision['send'],original['combinations'][baseline]['hospitals'][i])
            self.assertEqual(decision['reason'],'cohort_success_current_only')
            self.assertEqual(decision['evidence']['planned_expected_cost'],321.123)
            self.assertFalse(set(decision['evidence'])&{'joint_first','joint_tail','joint_budget'})
            self.assertEqual([r['hospital'] for r in decision['evidence']['candidate_plan'] if r['planned_offset_seconds']==0],decision['send'])

    def test_B_tie_retains_exact_legacy_flags_without_rewriting_meaning(self):
        _,joint,members,tails,priority=fixture(4)
        budget=mock_budget(members,joint,tails)
        with patch.object(policy,'_score',side_effect=lambda i,*a,**k:score(i)):
            result=invoke(joint,members,tails,priority,budget)
        self.assertFalse(result['evidence']['intervened'])
        for new,old in zip(result['decisions'],budget['decisions']):
            new['evidence'].pop('cohort_success');new['evidence']['policy']=policy.BASE_POLICY
            self.assertEqual(new,old)

    def test_source_feasible_third_row_can_be_selected_beyond_binary_references(self):
        _,joint,members,tails,priority=fixture(4)
        budget=mock_budget(members,joint,tails)
        original=budget['evidence']['original_tail_proposal']['evidence']['original_joint_proposal']['evidence']
        references={original['baseline_index'],budget['evidence']['actual_selected_index']}
        third=next(i for i in range(len(original['combinations'])) if i not in references)
        # Extend only the orchestration mock's declared feasible set. The real
        # producer obtains it unchanged from SHA-bound B; audit rechecks math.
        original['combinations'][third]['feasible']=True
        def scored(index,*args,**kwargs):
            values=[dict(patient_id=m['patient_id'],effective_priority=4,metrics=dict(
                total_cost_seconds=100.,selection_probability=.9,capped_wait_seconds=50.)) for m in members]
            return score(index,p4=3 if index==third else 1,members=values)
        with patch.object(policy,'_score',side_effect=scored):result=invoke(joint,members,tails,priority,budget)
        self.assertEqual(result['evidence']['selected_index'],third)
        self.assertEqual([d['send'] for d in result['decisions']],original['combinations'][third]['hospitals'])

    def test_patient_identity_renaming_preserves_scores_and_selected_action(self):
        _,joint,members,tails,priority=fixture(4)
        budget=mock_budget(members,joint,tails)
        result=invoke(joint,members,tails,priority,budget)
        names={f'P{i}':100+i for i in range(4)}
        def rename(value):
            if isinstance(value,list):return [rename(v) for v in value]
            if isinstance(value,tuple):return tuple(rename(v) for v in value)
            if isinstance(value,dict):return {k:rename(v) for k,v in value.items()}
            return names.get(value,value) if isinstance(value,str) else value
        changed_joint,changed_members,changed_tails,changed_priority,changed_budget=rename((joint,members,tails,priority,budget))
        changed=invoke(changed_joint,changed_members,changed_tails,changed_priority,changed_budget)
        self.assertEqual(changed,rename(result))

    def test_repeated_scores_do_not_share_candidate_ledgers_or_mutate_template(self):
        _,joint,members,tails,_=fixture(5)
        budget=mock_budget(members,joint,tails);template=policy._template(budget,tails)
        first=policy._first_members(members)
        original=budget['evidence']['original_tail_proposal']['evidence']['original_joint_proposal']['evidence']
        models=[policy.JOINT._model(m['inputs'],p) for m,p in zip(first,original['baseline_proposals'])]
        args=dict(members=first,current_models=models,template=template,
            priorities={(str,f'P{i}'):4 for i in range(5)},now=1000)
        before=deepcopy((first,models,template))
        expected=policy._score(0,[['H0'],['H0']],**args)
        policy._score(1,[['H1'],['H0']],**args)
        self.assertEqual(policy._score(0,[['H0'],['H0']],**args),expected)
        self.assertEqual((first,models,template),before)

    def test_missing_priority_or_singleton_fails_before_budget_policy(self):
        _,joint,members,tails,priority=fixture()
        for badpriority,badtails in ((dict(priority,patients=priority['patients'][:-1]),tails),(priority,tails[:-1])):
            with patch.object(policy.original,'decide_joint') as called:
                with self.assertRaises(ValueError):policy.decide_joint(policy.NAME,members=members,joint_context=joint,
                    tail_members=badtails,priority_context=badpriority)
                called.assert_not_called()

    def test_partial_horizon_and_A_edges_remain_the_frozen_H_cells(self):
        _,joint,members,tails,priority=fixture()
        joint['now']=1283
        for member in members+tails:member['inputs']['now']=1283
        priority=context.build_priority_context(joint_context=joint,members=members,tail_members=tails,
            priority_rows=[{k:v for k,v in p.items() if k!='effective_priority'} for p in priority['patients']])
        budget=mock_budget(members,joint,tails)
        result=invoke(joint,members,tails,priority,budget)
        template=result['evidence']['continuation_template']
        self.assertEqual(template['singleton']['horizon_proposal']['evidence']['time_horizon']['cell_edges_seconds'],[5,10,15,17])
        self.assertTrue(all(0<=m['metrics']['capped_wait_seconds']<=17
            for row in result['evidence']['row_scores'] for m in row['member_metrics']))


if __name__=='__main__':unittest.main()
