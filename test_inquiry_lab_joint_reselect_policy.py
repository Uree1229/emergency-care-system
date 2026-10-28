"""Synthetic table reselection units; no saved failure or scenario replay."""
from copy import deepcopy
import math
import unittest
from unittest.mock import patch

from inquiry_lab import joint_reselect_policy as policy, joint_reselect_context as context
from inquiry_lab import joint_tail_context
from test_inquiry_lab_joint_reselect_context import fixture


def model(n=3,first=False,fast_last=True):
    rows=[]
    for i in range(n):
        service=5 if fast_last and i==n-1 else 10
        rows.append(dict(hospital=f'H{i}',travel_seconds=1000 if first and i else 0,
            service_mu=math.log(service),service_sigma=.1,reply_probability=1.,accept_probability=1.,
            mean_service_seconds=math.exp(math.log(service)+.005),assumed_workers=1,
            residual_wait_seconds=0.,inquiry_cost_seconds=15))
    return dict(horizon_seconds=300,cell_edges_seconds=list(range(5,301,5)),failure_penalty_seconds=1500,hospitals=rows)


def tail_pool(member,hospitals):
    member['inputs']['candidates']=[dict(hospital_id=h,travel={'duration_seconds':i},
        resources_enough=True) for i,h in enumerate(hospitals)]


def original_tail_proposal(members,joint,tails,baseline=None,models=None):
    native=context.base_context_projection(joint)
    projected=context.base_members_projection(members)
    projected_tails=context.base_members_projection(tails)
    first_context=joint_tail_context.base_context_projection(native)
    first_members=joint_tail_context.base_members_projection(projected)
    joint_policy=policy.original.original
    horizon=joint_policy.original
    n=len(members[0]['inputs']['candidates'])
    baseline=[['H0'],['H0']] if baseline is None else baseline
    models=[model(n,first=True),model(n)] if models is None else models
    proposals=[]
    for member,send in zip(first_members,baseline):
        decision=horizon.decide(horizon.NAME,**deepcopy(member['inputs']))
        decision['send']=list(send)
        decision['evidence']['sent_hospitals']=list(send)
        for row in decision['evidence']['candidate_plan']:
            row['planned_offset_seconds']=0 if row['hospital'] in send else 120
        proposals.append(decision)
    with patch.object(horizon,'decide',side_effect=deepcopy(proposals)), \
            patch.object(joint_policy,'_model',side_effect=deepcopy(models)):
        first=joint_policy.decide_joint(joint_policy.NAME,members=first_members,joint_context=first_context)
    with patch.object(joint_policy,'decide_joint',return_value=deepcopy(first)):
        return policy.original.decide_joint(policy.BASE_POLICY,members=projected,
            joint_context=native,tail_members=projected_tails)


def evaluated(members,joint,tails,proposal):
    with patch.object(policy.original,'decide_joint',return_value=proposal):
        return policy.decide_joint(policy.NAME,members=members,joint_context=joint,tail_members=tails)


class JointReselectPolicyTests(unittest.TestCase):
    def test_best_safe_alternative_is_chosen_instead_of_original_tail_baseline(self):
        _,joint,members,tails=fixture(3,3)
        tail_pool(tails[0],['H2'])
        proposal=original_tail_proposal(members,joint,tails)
        self.assertTrue(proposal['evidence']['vetoed'])
        self.assertEqual([d['send'] for d in proposal['decisions']],[['H0'],['H0']])
        before=deepcopy((members,joint,tails,proposal))
        result=evaluated(members,joint,tails,proposal)
        self.assertEqual((members,joint,tails,proposal),before)
        self.assertTrue(result['evidence']['applied'])
        self.assertEqual([d['send'] for d in result['decisions']],[['H0'],['H1']])
        self.assertEqual(result['evidence']['original_tail_proposal'],before[3])
        nested=before[3]['evidence']['original_joint_proposal']
        self.assertEqual([d['send'] for d in nested['decisions']],[['H0'],['H2']])
        self.assertEqual(result['evidence']['selected_tail_checks'][0]['selected_zero_pending_hospitals'],['H2'])
        self.assertTrue(all('joint_tail' not in d['evidence'] for d in result['decisions']))

    def test_every_original_row_intersects_pair_feasibility_and_tail_constraint(self):
        _,joint,members,tails=fixture(3,3)
        tail_pool(tails[0],['H2'])
        proposal=original_tail_proposal(members,joint,tails)
        result=evaluated(members,joint,tails,proposal)
        original=proposal['evidence']['original_joint_proposal']['evidence']['combinations']
        rows=result['evidence']['row_feasibility']
        self.assertEqual(len(rows),9)
        for metadata,row in zip(rows,original):
            safe=not any('H2' in batch for batch in row['hospitals'])
            self.assertEqual(metadata['joint_feasible'],row['feasible'])
            self.assertEqual(metadata['tail_feasible'],safe)
            self.assertEqual(metadata['feasible'],row['feasible'] and safe)
        self.assertTrue(any(not x['joint_feasible'] and x['tail_feasible'] for x in rows))
        self.assertTrue(any(x['joint_feasible'] and not x['tail_feasible'] for x in rows))

    def test_baseline_best_tie_precedes_lexical_other_row(self):
        _,joint,members,tails=fixture(3,3)
        tail_pool(tails[0],['H2'])
        models=[model(3,fast_last=False),model(3,fast_last=False)]
        proposal=original_tail_proposal(members,joint,tails,baseline=[['H1'],['H0']],models=models)
        result=evaluated(members,joint,tails,proposal)
        e=result['evidence']
        self.assertTrue(e['baseline_best_tied'])
        self.assertFalse(e['applied'])
        self.assertNotEqual(e['best_index'],e['selected_index'])
        self.assertEqual([d['send'] for d in result['decisions']],[['H1'],['H0']])
        self.assertEqual(len(e['row_feasibility']),9)

    def test_other_equal_minima_keep_first_original_enumeration(self):
        _,joint,members,tails=fixture(3,4)
        tail_pool(tails[0],['H3'])
        proposal=original_tail_proposal(members,joint,tails)
        result=evaluated(members,joint,tails,proposal)
        self.assertTrue(result['evidence']['applied'])
        self.assertEqual([d['send'] for d in result['decisions']],[['H0'],['H1']])

    def test_no_strict_safe_improvement_retains_baseline(self):
        _,joint,members,tails=fixture(3,2)
        tail_pool(tails[0],['H1'])
        proposal=original_tail_proposal(members,joint,tails)
        result=evaluated(members,joint,tails,proposal)
        self.assertFalse(result['evidence']['applied'])
        self.assertEqual([d['send'] for d in result['decisions']],[['H0'],['H0']])

    def test_zero_tail_keeps_original_joint_winner(self):
        _,joint,members,tails=fixture(2,3)
        proposal=original_tail_proposal(members,joint,tails)
        result=evaluated(members,joint,tails,proposal)
        self.assertEqual([d['send'] for d in result['decisions']],[['H0'],['H2']])
        self.assertTrue(all(row['tail_feasible'] for row in result['evidence']['row_feasibility']))
        self.assertEqual(result['evidence']['selected_tail_checks'],[])

    def test_all_400_rows_are_checked_at_original_six_choose_three_counts(self):
        _,joint,members,tails=fixture(3,6)
        tail_pool(tails[0],['H5'])
        proposal=original_tail_proposal(members,joint,tails,baseline=[['H0','H1','H2'],['H0','H1','H2']])
        result=evaluated(members,joint,tails,proposal)
        self.assertEqual(len(result['evidence']['row_feasibility']),400)
        self.assertTrue(all(len(d['send'])==3 for d in result['decisions']))
        self.assertTrue(all(d['next_review_at']==1015 for d in result['decisions']))
        self.assertTrue(all(row['planned_offset_seconds'] in (0,None) for d in result['decisions'] for row in d['evidence']['candidate_plan']))

    def test_spec_and_fallback_preserve_entire_frozen_tail_core(self):
        before,after=policy.original.spec(),policy.spec()
        for key in ('name','label'):before.pop(key);after.pop(key)
        self.assertEqual(before,after)
        _,_,members,_=fixture(2,3)
        args=members[0]['inputs']
        before=policy.original.decide(policy.BASE_POLICY,**deepcopy(args))
        after=policy.decide(policy.NAME,**deepcopy(args))
        self.assertEqual(after['evidence'].pop('joint_reselect'),
            dict(schema_version=1,mode='horizon_fallback',reselection_evaluated=False))
        after['evidence']['policy']=policy.BASE_POLICY
        self.assertEqual(before,after)

    def test_missing_tail_fails_closed_before_any_original_proposal_call(self):
        _,joint,members,tails=fixture(3,3)
        with patch.object(policy.original,'decide_joint') as invoked:
            with self.assertRaises(ValueError):policy.decide_joint(policy.NAME,members=members,joint_context=joint,tail_members=[])
            invoked.assert_not_called()

    def test_frozen_tail_is_called_once_with_copied_native_aliases(self):
        _,joint,members,tails=fixture(3,3)
        proposal=original_tail_proposal(members,joint,tails)
        with patch.object(policy.original,'decide_joint',return_value=proposal) as invoked:
            policy.decide_joint(policy.NAME,members=members,joint_context=joint,tail_members=tails)
        self.assertEqual(invoked.call_count,1)
        self.assertEqual(invoked.call_args.kwargs['joint_context'],context.base_context_projection(joint))
        self.assertEqual(invoked.call_args.kwargs['tail_members'],context.base_members_projection(tails))
        self.assertTrue(all(m['workflow_snapshot']['job']['policy_name']==policy.NAME for m in members+tails))

    def test_real_frozen_proposals_and_tables_remain_separate_from_actual_outputs(self):
        _,joint,members,tails=fixture(3,3)
        before=deepcopy((joint,members,tails))
        result=policy.decide_joint(policy.NAME,members=members,joint_context=joint,tail_members=tails)
        self.assertEqual((joint,members,tails),before)
        self.assertTrue(all(d['evidence']['policy']==policy.NAME for d in result['decisions']))
        self.assertEqual(result['evidence']['original_tail_proposal']['evidence']['joint_context'],context.base_context_projection(joint))


if __name__=='__main__':unittest.main()
