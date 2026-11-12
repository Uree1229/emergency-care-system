"""Synthetic orchestration and public arithmetic checks; no scenario replay."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from inquiry_lab import joint_budget_policy as policy, joint_budget_context as context
from test_inquiry_lab_joint_budget_context import fixture
from test_inquiry_lab_joint_tail_policy import original_proposal


def tail_proposal(members,joint,tails):
    pair=context.base_members_projection(members)
    ctx=context.base_context_projection(joint)
    tail=context.base_members_projection(tails)
    frozen_joint=original_proposal(pair,ctx)
    with patch.object(policy.original.original,'decide_joint',return_value=frozen_joint):
        return policy.original.decide_joint(policy.BASE_POLICY,members=pair,joint_context=ctx,tail_members=tail)


def virtual(counts):
    return dict(decisions=[dict(send=[f'H{i}' for i in range(k)]) for k in counts],
        evidence=dict(cardinalities=list(counts),synthetic_marker='complete mocked projection return'))


def run(members,joint,tails,proposal,counts):
    with patch.object(policy.original,'decide_joint',return_value=proposal) as tail_call, \
            patch.object(policy.original.original,'decide_joint',side_effect=[virtual(k) for k in counts]) as joint_call:
        result=policy.decide_joint(policy.NAME,members=members,joint_context=joint,tail_members=tails)
    return result,tail_call,joint_call


class JointBudgetPolicyTests(unittest.TestCase):
    def test_spec_and_H_fallback_change_only_identity_and_new_metadata(self):
        old,new=policy.original.spec(),policy.spec()
        for key in ('name','label'):old.pop(key);new.pop(key)
        self.assertEqual(old,new)
        _,_,members,_=fixture(2)
        old=policy.original.decide(policy.BASE_POLICY,**deepcopy(members[0]['inputs']))
        new=policy.decide(policy.NAME,**deepcopy(members[0]['inputs']))
        self.assertEqual(new['evidence'].pop('joint_budget'),
            dict(schema_version=1,mode='horizon_fallback',projection_evaluated=False))
        new['evidence']['policy']=policy.BASE_POLICY
        self.assertEqual(old,new)
        with self.assertRaises(ValueError):policy.spec(policy.BASE_POLICY)

    def test_one_later_member_decline_restores_exact_original_winner(self):
        _,joint,members,tails=fixture()
        source=tail_proposal(members,joint,tails)
        self.assertTrue(source['evidence']['vetoed'])
        before=deepcopy((members,joint,tails,source))
        result,tail_call,joint_call=run(members,joint,tails,source,[[1,2],[2,1]])
        self.assertEqual((members,joint,tails,source),before)
        self.assertTrue(result['evidence']['cancelled_tail_veto'])
        self.assertEqual(result['evidence']['declining_patients'],['P2'])
        self.assertEqual(result['evidence']['original_tail_proposal'],source)
        self.assertEqual(tail_call.call_count,1)
        self.assertEqual(joint_call.call_count,2)
        for actual,expected in zip(result['decisions'],source['evidence']['original_joint_proposal']['decisions']):
            meta=actual['evidence'].pop('joint_budget')
            self.assertTrue(meta['cancelled_tail_veto'])
            self.assertNotIn('joint_tail',actual['evidence'])
            actual['evidence']['policy']=policy.PROJECTION_POLICY
            self.assertEqual(actual,expected)

    def test_equal_or_larger_baseline_counts_keep_exact_tail_decisions(self):
        _,joint,members,tails=fixture()
        source=tail_proposal(members,joint,tails)
        for counts in ([[1,1],[1,1]],[[2,2],[1,2]]):
            result,_,_=run(members,joint,tails,source,counts)
            self.assertFalse(result['evidence']['cancelled_tail_veto'])
            for actual,expected in zip(result['decisions'],source['decisions']):
                actual['evidence'].pop('joint_budget')
                actual['evidence']['policy']=policy.BASE_POLICY
                self.assertEqual(actual,expected)

    def test_any_later_pair_can_trigger_without_early_stop(self):
        _,joint,members,tails=fixture(6)
        source=tail_proposal(members,joint,tails)
        result,_,calls=run(members,joint,tails,source,[[1,1],[1,2],[1,1],[2,1]])
        self.assertEqual(calls.call_count,4)
        self.assertEqual(result['evidence']['declining_patients'],['P4'])
        self.assertEqual([r['patient_id'] for r in result['evidence']['paired_count_comparisons']],['P2','P3','P4','P5'])

    def test_final_singleton_is_captured_and_guarded_but_never_projected(self):
        _,joint,members,tails=fixture(5)
        source=tail_proposal(members,joint,tails)
        result,_,calls=run(members,joint,tails,source,[[1,1],[1,1]])
        self.assertEqual(calls.call_count,2)
        self.assertEqual([r['patient_id'] for r in result['evidence']['paired_count_comparisons']],['P2','P3'])
        for path in result['evidence']['projections'].values():
            self.assertEqual(path['excluded_singleton_patient_ids'],['P4'])
            self.assertEqual([r['patient']['id'] for r in path['steps'][0]['joint_context']['eligible']],['P2','P3','P4'])
        self.assertEqual(source['evidence']['triggered_tail_patients'],['P2','P3','P4'])

    def test_singleton_only_keeps_veto_with_zero_virtual_calls(self):
        _,joint,members,tails=fixture(3)
        source=tail_proposal(members,joint,tails)
        result,_,calls=run(members,joint,tails,source,[])
        calls.assert_not_called()
        self.assertTrue(result['evidence']['projection_evaluated'])
        self.assertFalse(result['evidence']['cancelled_tail_veto'])
        self.assertEqual(result['evidence']['paired_count_comparisons'],[])
        self.assertEqual([d['send'] for d in result['decisions']],[d['send'] for d in source['decisions']])

    def test_no_original_tail_veto_skips_all_projection(self):
        _,joint,members,tails=fixture(2)
        source=tail_proposal(members,joint,tails)
        self.assertFalse(source['evidence']['vetoed'])
        result,_,calls=run(members,joint,tails,source,[])
        calls.assert_not_called()
        self.assertFalse(result['evidence']['projection_evaluated'])
        self.assertEqual(result['evidence']['projections'],dict(baseline=None,winner=None))

    def test_both_current_pair_and_each_virtual_pair_have_common_exact_ledgers(self):
        _,joint,members,tails=fixture(6)
        pending=[dict(hospital='H9',patient='OTHER',submitted_at=990)]
        for member in members+tails:member['inputs']['pending']=deepcopy(pending)
        source=tail_proposal(members,joint,tails)
        result,_,calls=run(members,joint,tails,source,[[1,2],[2,1],[2,1],[1,2]])
        for path in result['evidence']['projections'].values():
            expected=deepcopy(pending)
            expected.extend(r['inquiry'] for r in path['initial_appended_inquiries'])
            self.assertEqual(path['initial_pending'],pending)
            for step in path['steps']:
                self.assertEqual(step['members'][0]['inputs']['pending'],expected)
                self.assertEqual(step['members'][1]['inputs']['pending'],expected)
                expected.extend(r['inquiry'] for r in step['appended_inquiries'])
            self.assertEqual(path['final_pending'],expected)
            self.assertTrue(all(row['submitted_at']==1000 for row in expected[1:]))
        self.assertEqual(calls.call_count,4)

    def test_two_step_ledgers_match_manual_public_inquiry_oracle_for_both_paths(self):
        _,joint,members,tails=fixture(6)
        prefix=[dict(hospital='H9',patient='OTHER',submitted_at=990)]
        for member in members+tails:member['inputs']['pending']=deepcopy(prefix)
        source=tail_proposal(members,joint,tails)
        result,_,_=run(members,joint,tails,source,[[1,2],[2,1],[2,1],[1,2]])
        row=lambda h,p:dict(hospital=h,patient=p,submitted_at=1000)
        initial=dict(baseline=prefix+[row('H0','P0'),row('H0','P1')],
            winner=prefix+[row('H0','P0'),row('H1','P1')])
        after_first=dict(baseline=initial['baseline']+[row('H0','P2'),row('H0','P3'),row('H1','P3')],
            winner=initial['winner']+[row('H0','P2'),row('H1','P2'),row('H0','P3')])
        final=dict(baseline=after_first['baseline']+[row('H0','P4'),row('H1','P4'),row('H0','P5')],
            winner=after_first['winner']+[row('H0','P4'),row('H0','P5'),row('H1','P5')])
        for name,path in result['evidence']['projections'].items():
            self.assertEqual(path['steps'][0]['members'][0]['inputs']['pending'],initial[name])
            self.assertEqual(path['steps'][0]['members'][1]['inputs']['pending'],initial[name])
            self.assertEqual(path['steps'][1]['members'][0]['inputs']['pending'],after_first[name])
            self.assertEqual(path['steps'][1]['members'][1]['inputs']['pending'],after_first[name])
            self.assertEqual(path['final_pending'],final[name])
        # H's positive future offsets remain in the immutable source plan only.
        for p in source['evidence']['original_joint_proposal']['evidence']['baseline_proposals']:
            self.assertEqual(p['evidence']['candidate_plan'][1]['planned_offset_seconds'],120)

    def test_public_identity_renaming_has_no_patient_or_seed_exception(self):
        _,joint,members,tails=fixture()
        source=tail_proposal(members,joint,tails)
        result,_,_=run(members,joint,tails,source,[[1,1],[2,1]])
        names={f'P{i}':100+i for i in range(4)}
        def rename(value):
            if isinstance(value,list):return [rename(v) for v in value]
            if isinstance(value,tuple):return tuple(rename(v) for v in value)
            if isinstance(value,dict):return {k:rename(v) for k,v in value.items()}
            return names.get(value,value) if isinstance(value,str) else value
        changed_joint,changed_members,changed_tails,changed_source=rename((joint,members,tails,source))
        changed,_,_=run(changed_members,changed_joint,changed_tails,changed_source,[[1,1],[2,1]])
        self.assertEqual(changed,rename(result))

    def test_projection_keeps_clock_sources_versions_and_original_input_except_alias_and_pending(self):
        _,joint,members,tails=fixture(6)
        source=tail_proposal(members,joint,tails)
        result,_,_=run(members,joint,tails,source,[[1,1],[1,1],[1,1],[1,1]])
        for path in result['evidence']['projections'].values():
            for step in path['steps']:
                self.assertEqual(step['joint_context']['now'],joint['now'])
                self.assertEqual(step['joint_context']['event_prefix_max'],joint['event_prefix_max'])
                self.assertEqual(step['joint_context']['decision_prefix_max'],joint['decision_prefix_max'])
                offset=2*step['step_index']
                self.assertEqual([r['patient']['id'] for r in step['joint_context']['eligible']],
                    [r['patient']['id'] for r in joint['eligible'][offset+2:]])
                for expected,actual in zip(tails[offset:offset+2],step['members']):
                    actual=deepcopy(actual)
                    actual['inputs']['pending']=deepcopy(expected['inputs']['pending'])
                    actual['workflow_snapshot']['job']['policy_name']=policy.NAME
                    self.assertEqual(actual,expected)

    def test_every_append_has_exact_immutable_proposal_send_source(self):
        _,joint,members,tails=fixture()
        source=tail_proposal(members,joint,tails)
        result,_,_=run(members,joint,tails,source,[[1,2],[2,1]])
        for name,path in result['evidence']['projections'].items():
            for row in path['initial_appended_inquiries']:
                self.assertEqual(row['source']['kind'],'current_pair_'+name)
                self.assertIsNone(row['source']['step_index'])
                self.assertTrue(row['source']['proposal_path'].startswith('original_tail_proposal.evidence.original_joint_proposal.'))
            for step in path['steps']:
                self.assertEqual(step['source_tail_member_indices'],[0,1])
                for row in step['appended_inquiries']:
                    origin=row['source']
                    self.assertEqual(origin['kind'],'projected_pair_selected')
                    self.assertEqual(origin['step_index'],step['step_index'])
                    self.assertEqual(origin['proposal_path'],f"projections.{name}.steps[0].proposal.decisions[{origin['member_index']}].send[{origin['send_index']}]")
                    self.assertEqual(row['inquiry']['hospital'],step['proposal']['decisions'][origin['member_index']]['send'][origin['send_index']])

    def test_missing_or_invalid_singleton_fails_before_frozen_tail_call(self):
        _,joint,members,tails=fixture(5)
        invalid=[tails[:-1]]
        bad=deepcopy(tails);bad[-1]['workflow_snapshot']['apply_snapshot']['job_version']=2;invalid.append(bad)
        for tail in invalid:
            with patch.object(policy.original,'decide_joint') as invoked:
                with self.assertRaises(ValueError):policy.decide_joint(policy.NAME,members=members,joint_context=joint,tail_members=tail)
                invoked.assert_not_called()

    def test_actual_rows_are_only_baseline_or_original_winner_not_virtual_pair(self):
        _,joint,members,tails=fixture()
        source=tail_proposal(members,joint,tails)
        result,_,_=run(members,joint,tails,source,[[1,1],[2,2]])
        nested=source['evidence']['original_joint_proposal']
        self.assertEqual(result['evidence']['actual_selected_index'],nested['evidence']['selected_index'])
        self.assertEqual([d['send'] for d in result['decisions']],[d['send'] for d in nested['decisions']])
        self.assertNotEqual([d['send'] for d in result['decisions']],[['H0','H1'],['H0','H1']])

    def test_real_frozen_JOINT_projection_records_full_H_plans_and_table_at_partial_horizon(self):
        _,joint,members,tails=fixture(5)
        joint['now']=1283
        for member in members+tails:member['inputs']['now']=1283
        source=tail_proposal(members,joint,tails)
        before=deepcopy((joint,members,tails,source))
        with patch.object(policy.original,'decide_joint',return_value=source):
            result=policy.decide_joint(policy.NAME,members=members,joint_context=joint,tail_members=tails)
        self.assertEqual((joint,members,tails,source),before)
        for path in result['evidence']['projections'].values():
            step=path['steps'][0]
            self.assertEqual(step['proposal']['evidence']['joint_context'],step['joint_context'])
            self.assertTrue(step['proposal']['evidence']['combinations'])
            for proposal in step['proposal']['evidence']['baseline_proposals']:
                self.assertEqual(proposal['evidence']['time_horizon']['cell_edges_seconds'],[5,10,15,17])
                self.assertTrue(all(r['planned_offset_seconds'] is None or r['planned_offset_seconds']<17
                    for r in proposal['evidence']['candidate_plan']))

    def test_real_nonveto_path_preserves_input_and_global_policy_identities(self):
        _,joint,members,tails=fixture()
        before=deepcopy((joint,members,tails))
        result=policy.decide_joint(policy.NAME,members=members,joint_context=joint,tail_members=tails)
        self.assertEqual((joint,members,tails),before)
        self.assertTrue(all(d['evidence']['policy']==policy.NAME for d in result['decisions']))
        self.assertEqual(policy.original.NAME,policy.BASE_POLICY)
        self.assertEqual(policy.original.original.NAME,policy.PROJECTION_POLICY)


if __name__=='__main__':unittest.main()
