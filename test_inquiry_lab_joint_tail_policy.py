"""Synthetic soft-veto units; no saved loss case or scenario replay."""
from copy import deepcopy
import math
import unittest
from unittest.mock import patch

from inquiry_lab import joint_tail_policy as policy, joint_tail_context as context
from test_inquiry_lab_joint_tail_context import fixture


def model(first=False):
    rows = [dict(hospital=f'H{i}',travel_seconds=1000 if first and i==1 else 0,
        service_mu=math.log(10),service_sigma=.1,reply_probability=1.,accept_probability=1.,
        mean_service_seconds=math.exp(math.log(10)+.005),assumed_workers=1,
        residual_wait_seconds=0.,inquiry_cost_seconds=15) for i in range(2)]
    return dict(horizon_seconds=300,cell_edges_seconds=list(range(5,301,5)),
        failure_penalty_seconds=1500,hospitals=rows)


def original_proposal(members,joint,applied=True):
    projected = context.base_members_projection(members)
    native = context.base_context_projection(joint)
    baseline = [['H0'],['H0']] if applied else [['H0'],['H1']]
    horizon = policy.original.original
    proposals = []
    for member,send in zip(projected,baseline):
        decision = horizon.decide(horizon.NAME,**deepcopy(member['inputs']))
        decision['send'] = list(send)
        decision['evidence']['sent_hospitals'] = list(send)
        for row in decision['evidence']['candidate_plan']:
            row['planned_offset_seconds'] = 0 if row['hospital'] in send else 120
        proposals.append(decision)
    with patch.object(horizon,'decide',side_effect=deepcopy(proposals)), \
            patch.object(policy.original,'_model',side_effect=[model(first=True),model()]):
        result = policy.original.decide_joint(policy.BASE_POLICY,members=projected,joint_context=native)
    if applied: assert result['evidence']['applied']
    return result


def tail_pool(member,hospitals):
    member['inputs']['candidates'] = [dict(hospital_id=h,travel={'duration_seconds':i},
        resources_enough=True) for i,h in enumerate(hospitals)]


def evaluated(members,joint,tails,proposal):
    with patch.object(policy.original,'decide_joint',return_value=proposal):
        return policy.decide_joint(policy.NAME,members=members,joint_context=joint,tail_members=tails)


class JointTailPolicyTests(unittest.TestCase):
    def test_spec_and_unpaired_fallback_only_change_identity_and_guard_metadata(self):
        old,new = policy.original.spec(),policy.spec()
        for key in ('name','label'): old.pop(key);new.pop(key)
        self.assertEqual(old,new)
        _,_,members,_ = fixture(2,2)
        args = members[0]['inputs']
        old = policy.original.decide(policy.BASE_POLICY,**deepcopy(args))
        new = policy.decide(policy.NAME,**deepcopy(args))
        self.assertEqual(new['evidence'].pop('joint_tail'),
            dict(schema_version=1,mode='horizon_fallback',guard_evaluated=False))
        new['evidence']['policy'] = policy.BASE_POLICY
        self.assertEqual(old,new)

    def test_last_public_zero_option_loss_vetoes_whole_winner_to_pair_baseline(self):
        _,joint,members,tails = fixture(3,2)
        tail_pool(tails[0],['H1'])
        proposal = original_proposal(members,joint)
        before = deepcopy((members,joint,tails,proposal))
        result = evaluated(members,joint,tails,proposal)
        self.assertEqual((members,joint,tails,proposal),before)
        self.assertTrue(result['evidence']['vetoed'])
        self.assertEqual(result['evidence']['triggered_tail_patients'],['P2'])
        self.assertEqual([d['send'] for d in result['decisions']],[['H0'],['H0']])
        check = result['evidence']['tail_checks'][0]
        self.assertEqual(check['baseline_pending_counts'],{'H1':0})
        self.assertEqual(check['winner_pending_counts'],{'H1':1})
        self.assertEqual(result['evidence']['original_joint_proposal'],before[3])
        self.assertEqual([d['send'] for d in proposal['decisions']],[['H0'],['H1']])
        self.assertTrue(all(d['evidence']['joint_first']['applied'] is False for d in result['decisions']))
        self.assertTrue(all(d['reason']=='joint_tail_veto_baseline_current_only' for d in result['decisions']))

    def test_another_zero_option_preserves_original_joint_winner(self):
        _,joint,members,tails = fixture(3,2)
        tail_pool(tails[0],['H1','H2'])
        result = evaluated(members,joint,tails,original_proposal(members,joint))
        self.assertFalse(result['evidence']['vetoed'])
        self.assertEqual([d['send'] for d in result['decisions']],[['H0'],['H1']])
        self.assertEqual(result['evidence']['tail_checks'][0]['winner_zero_pending_hospitals'],['H2'])

    def test_preexisting_public_pending_and_pair_queries_are_counted_once(self):
        _,joint,members,tails = fixture(3,2)
        tail_pool(tails[0],['H0','H1'])
        pending = [dict(hospital='H0',patient=f'OTHER{i}',submitted_at=990) for i in range(3)]
        for member in members+tails: member['inputs']['pending']=deepcopy(pending)
        result = evaluated(members,joint,tails,original_proposal(members,joint))
        check = result['evidence']['tail_checks'][0]
        self.assertEqual(check['baseline_pending_counts'],{'H0':5,'H1':0})
        self.assertEqual(check['winner_pending_counts'],{'H0':4,'H1':1})
        self.assertTrue(result['evidence']['vetoed'])

    def test_no_baseline_zero_option_or_empty_pool_does_not_create_veto(self):
        _,joint,members,tails = fixture(3,2)
        for pool in (['H0'],[]):
            tail_pool(tails[0],pool)
            result = evaluated(members,joint,tails,original_proposal(members,joint))
            self.assertFalse(result['evidence']['vetoed'])
            self.assertFalse(result['evidence']['tail_checks'][0]['triggered'])

    def test_original_applied_false_is_unchanged_without_guard_evaluation(self):
        _,joint,members,tails = fixture(3,2)
        tail_pool(tails[0],['H1'])
        proposal = original_proposal(members,joint,applied=False)
        result = evaluated(members,joint,tails,proposal)
        self.assertFalse(result['evidence']['guard_evaluated'])
        self.assertFalse(result['evidence']['vetoed'])
        self.assertEqual(result['evidence']['tail_checks'],[])
        self.assertEqual([d['send'] for d in result['decisions']],[d['send'] for d in proposal['decisions']])

    def test_zero_tail_retains_original_winner(self):
        _,joint,members,tails = fixture(2,2)
        result = evaluated(members,joint,tails,original_proposal(members,joint))
        self.assertTrue(result['evidence']['guard_evaluated'])
        self.assertFalse(result['evidence']['vetoed'])
        self.assertEqual(result['evidence']['tail_checks'],[])

    def test_every_remaining_current_patient_is_checked_not_only_first_tail(self):
        _,joint,members,tails = fixture(4,2)
        tail_pool(tails[0],['H2'])
        tail_pool(tails[1],['H1'])
        result = evaluated(members,joint,tails,original_proposal(members,joint))
        self.assertEqual([x['patient_id'] for x in result['evidence']['tail_checks']],['P2','P3'])
        self.assertEqual(result['evidence']['triggered_tail_patients'],['P3'])
        self.assertTrue(result['evidence']['vetoed'])

    def test_pool_is_bounded_by_unchanged_nearest_six_and_max_new(self):
        _,joint,members,tails = fixture(3,2)
        tail_pool(tails[0],['H1','H2','H3','H4','H5','H6','H7'])
        tails[0]['inputs']['max_new']=1
        result = evaluated(members,joint,tails,original_proposal(members,joint))
        self.assertEqual(result['evidence']['tail_checks'][0]['candidate_pool'],['H1'])
        self.assertTrue(result['evidence']['vetoed'])
        tails[0]['inputs']['max_new']=10
        result = evaluated(members,joint,tails,original_proposal(members,joint))
        self.assertEqual(result['evidence']['tail_checks'][0]['candidate_pool'],['H1','H2','H3','H4','H5','H6'])
        self.assertFalse(result['evidence']['vetoed'])

    def test_no_second_best_policy_call_and_base_alias_is_copied_only(self):
        _,joint,members,tails = fixture(3,2)
        tail_pool(tails[0],['H1'])
        proposal = original_proposal(members,joint)
        with patch.object(policy.original,'decide_joint',return_value=proposal) as invoked:
            policy.decide_joint(policy.NAME,members=members,joint_context=joint,tail_members=tails)
        self.assertEqual(invoked.call_count,1)
        self.assertEqual(invoked.call_args.kwargs['joint_context'],context.base_context_projection(joint))
        self.assertEqual(invoked.call_args.kwargs['members'],context.base_members_projection(members))
        self.assertTrue(all(m['workflow_snapshot']['job']['policy_name']==policy.NAME for m in members))

    def test_missing_or_stale_tail_fails_closed_before_original_winner_is_called(self):
        _,joint,members,tails = fixture(3,2)
        for invalid in ([],[dict(tails[0],patient_id='UNREGISTERED')]):
            with patch.object(policy.original,'decide_joint') as invoked:
                with self.assertRaises(ValueError):
                    policy.decide_joint(policy.NAME,members=members,joint_context=joint,tail_members=invalid)
                invoked.assert_not_called()

    def test_real_frozen_joint_call_accepts_copied_identity_projection(self):
        _,joint,members,tails = fixture(3,2)
        before = deepcopy((joint,members,tails))
        result = policy.decide_joint(policy.NAME,members=members,joint_context=joint,tail_members=tails)
        self.assertEqual((joint,members,tails),before)
        self.assertEqual(len(result['decisions']),2)
        self.assertTrue(all(d['evidence']['policy']==policy.NAME for d in result['decisions']))
        self.assertEqual(result['evidence']['original_joint_proposal']['evidence']['joint_context'],
            context.base_context_projection(joint))


if __name__=='__main__': unittest.main()
