"""Synthetic row-specific reference calls, finite menu and actual outputs."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from inquiry_lab import cohort_dynamic_policy as policy,cohort_dynamic_context as context
from test_inquiry_lab_cohort_success_audit import fixture as valid_CS_fixture,priority as CS_priority,output as CS_output
from test_inquiry_lab_cohort_dynamic_context import fixture as basic_fixture


def fixture(count=5,remaining=120):
    _,_,joint,members,tails,budget=valid_CS_fixture(patients=count,remaining=remaining)
    priority=CS_priority(joint,members,tails)
    decisions,evidence=CS_output(members,joint,tails,budget,priority)
    proposal=dict(decisions=decisions,evidence=evidence)
    for row in joint['eligible']:row['job']['policy_name']=policy.NAME
    for member in members+tails:member['workflow_snapshot']['job']['policy_name']=policy.NAME
    return joint,members,tails,priority,proposal


def invoke(joint,members,tails,priority,proposal):
    frozen=policy.original.decide_joint
    seen=[]
    def reference(name,**kwargs):
        seen.append(deepcopy(kwargs))
        return deepcopy(proposal) if len(seen)==1 else frozen(name,**kwargs)
    with patch.object(policy.original,'decide_joint',side_effect=reference):
        result=policy.decide_joint(policy.NAME,members=members,joint_context=joint,
            tail_members=tails,priority_context=priority)
    return result,seen


def score(index,p4=1.,p2=0.,p1=0.,wait=100.):
    return dict(row_index=index,member_metrics=[],tier_probabilities={'4':p4,'2':p2,'1':p1},sum_capped_wait_seconds=wait,projection={})


class CohortDynamicPolicyTests(unittest.TestCase):
    def test_spec_and_fallback_only_add_native_metadata(self):
        old,new=policy.original.spec(),policy.spec()
        for key in ('name','label'):old.pop(key);new.pop(key)
        self.assertEqual(old,new)
        _,_,members,_,_=basic_fixture()
        old=policy.original.decide(policy.BASE_POLICY,**deepcopy(members[0]['inputs']))
        new=policy.decide(policy.NAME,**deepcopy(members[0]['inputs']))
        self.assertEqual(new['evidence'].pop('cohort_dynamic'),
            dict(schema_version=1,mode='horizon_fallback',evaluated=False,intervened=False))
        new['evidence']['policy']=policy.BASE_POLICY
        self.assertEqual(new,old)

    def test_menu_has_CS_B_TAIL_order_and_never_scores_other_rows(self):
        joint,members,tails,priority,source=fixture(4)
        before=deepcopy((joint,members,tails,priority,source))
        result,seen=invoke(joint,members,tails,priority,source)
        budget=source['evidence']['original_budget_proposal']
        expected=list(dict.fromkeys([source['evidence']['selected_index'],source['evidence']['base_budget_selected_index'],
            budget['evidence']['original_tail_proposal']['evidence']['actual_selected_index']]))
        self.assertEqual(result['evidence']['menu_indices'],expected)
        self.assertLessEqual(len(expected),3)
        self.assertEqual([r['row_index'] for r in result['evidence']['row_scores']],expected)
        self.assertEqual(len(seen),1+len(expected))
        self.assertEqual((joint,members,tails,priority,source),before)
        self.assertEqual(result['evidence']['original_cohort_success_proposal'],source)

    def test_same_index_references_are_deduplicated(self):
        joint,members,tails,priority,source=fixture(4)
        source['evidence']['selected_index']=source['evidence']['base_budget_selected_index']
        result,_=invoke(joint,members,tails,priority,source)
        self.assertEqual(len(result['evidence']['menu_indices']),2)
        self.assertEqual(len(result['evidence']['row_scores']),2)

    def test_CS_unevaluated_skips_all_dynamic_calls_and_preserves_outputs(self):
        joint,members,tails,priority,source=fixture(4)
        source['evidence']['evaluated']=False
        with patch.object(policy,'_score') as scored:result,seen=invoke(joint,members,tails,priority,source)
        scored.assert_not_called();self.assertEqual(len(seen),1)
        self.assertEqual(result['evidence']['menu_indices'],[])
        for new,old in zip(result['decisions'],source['decisions']):
            new['evidence'].pop('cohort_dynamic');new['evidence']['policy']=policy.BASE_POLICY
            self.assertEqual(new,old)

    def test_each_row_recomputes_reference_CS_under_its_own_pending(self):
        joint,members,tails,priority,source=fixture(6)
        result,seen=invoke(joint,members,tails,priority,source)
        menu=result['evidence']['menu_indices']
        self.assertEqual(len(seen),1+2*len(menu))
        for position,row in enumerate(result['evidence']['row_scores']):
            projection=row['projection'];ledger=deepcopy(projection['initial_pending'])
            ledger.extend(r['inquiry'] for r in projection['initial_appended_inquiries'])
            for step in projection['steps']:
                for member in step['members']+step['tail_members']:
                    self.assertEqual(member['inputs']['pending'],ledger)
                    self.assertEqual(member['inputs']['now'],joint['now'])
                    self.assertEqual(member['workflow_snapshot']['job']['policy_name'],policy.BASE_POLICY)
                context=step['joint_context'];start=2*step['step_index']+2
                self.assertEqual([p['patient']['id'] for p in context['eligible']],
                    [p['patient']['id'] for p in joint['eligible'][start:]])
                self.assertEqual(context['event_prefix_max'],joint['event_prefix_max'])
                self.assertEqual(context['decision_prefix_max'],joint['decision_prefix_max'])
                self.assertEqual([r['patient_id'] for r in step['priority_context']['patients']],
                    [p['patient']['id'] for p in context['eligible']])
                self.assertEqual(step['priority_context']['revision'],priority['revision'])
                self.assertEqual(step['proposal']['evidence']['joint_context'],context)
                ledger.extend(r['inquiry'] for r in step['appended_inquiries'])
            self.assertEqual(projection['final_pending'],ledger)
            if position:
                self.assertNotEqual(projection['steps'][0]['members'][0]['inputs']['pending'],
                    result['evidence']['row_scores'][0]['projection']['steps'][0]['members'][0]['inputs']['pending'])

    def test_virtual_send_sources_and_actual_CS_output_memberships_match(self):
        joint,members,tails,priority,source=fixture(5)
        result,_=invoke(joint,members,tails,priority,source)
        for pos,row in enumerate(result['evidence']['row_scores']):
            projection=row['projection']
            for added in projection['initial_appended_inquiries']:
                self.assertEqual(added['source']['kind'],'current_pair_row')
                self.assertIn('combinations['+str(row['row_index'])+']',added['source']['proposal_path'])
            for step in projection['steps']:
                for added in step['appended_inquiries']:
                    s=added['source'];self.assertEqual(s['kind'],'reference_CS_selected')
                    self.assertEqual(added['inquiry']['hospital'],step['proposal']['decisions'][s['member_index']]['send'][s['send_index']])
                    self.assertTrue(s['proposal_path'].startswith(f'row_scores[{pos}].projection.steps['))
            singleton=projection['singleton'];self.assertIsNotNone(singleton)
            expected=deepcopy(projection['initial_pending'])
            expected.extend(a['inquiry'] for a in projection['initial_appended_inquiries'])
            for step in projection['steps']:expected.extend(a['inquiry'] for a in step['appended_inquiries'])
            self.assertEqual(singleton['member']['inputs']['pending'],expected)
            self.assertEqual(singleton['member']['workflow_snapshot']['job']['policy_name'],policy.BASE_POLICY)
            expected.extend(a['inquiry'] for a in singleton['appended_inquiries'])
            self.assertEqual(projection['final_pending'],expected)
            self.assertEqual([a['inquiry']['hospital'] for a in singleton['appended_inquiries']],singleton['proposal']['send'])

    def test_dynamic_CS_continuation_differs_across_candidate_ledgers(self):
        joint,members,tails,priority,source=fixture(4)
        result,_=invoke(joint,members,tails,priority,source)
        sends=[[d['send'] for d in r['projection']['steps'][0]['proposal']['decisions']] for r in result['evidence']['row_scores']]
        self.assertTrue(any(s!=sends[0] for s in sends[1:]))

    def test_scores_use_selected_pair_output_not_CS_future_template_metrics(self):
        joint,members,tails,priority,source=fixture(4)
        result,_=invoke(joint,members,tails,priority,source)
        for row in result['evidence']['row_scores']:
            step=row['projection']['steps'][0];proposal=step['proposal'];j=policy._joint(proposal)
            models=[policy.JOINT._model(m['inputs'],p) for m,p in zip(step['members'],j['evidence']['baseline_proposals'])]
            expected=policy.JOINT._combination(models,[d['send'] for d in proposal['decisions']])
            self.assertEqual([r['metrics'] for r in row['member_metrics'][2:4]],expected['member_metrics'])

    def test_global_priority_then_wait_uses_CS_preference_and_menu_order(self):
        self.assertEqual(policy._select([score(9,p4=.8,wait=1),score(2,p4=.9,wait=1000)],9)['selected_index'],2)
        self.assertEqual(policy._select([score(9,p2=.2),score(2,p2=.3)],9)['selected_index'],2)
        self.assertEqual(policy._select([score(9,p1=.2),score(2,p1=.3)],9)['selected_index'],2)
        rows=[score(9,p4=1-.75e-12),score(2,p4=1),score(0,p4=1-1.5e-12,wait=1)]
        self.assertEqual(policy._select(rows,9)['selected_index'],9)
        self.assertEqual(policy._select(rows,99)['selected_index'],9)
        self.assertEqual(policy._select([score(9,wait=100),score(2,wait=100-2e-9)],9)['selected_index'],2)

    def test_changed_output_has_new_metadata_only_and_no_virtual_sends(self):
        joint,members,tails,priority,source=fixture(4)
        original_score=policy._score
        target=source['evidence']['base_budget_selected_index']
        self.assertNotEqual(target,source['evidence']['selected_index'])
        def scored(index,*args,**kwargs):
            result=original_score(index,*args,**kwargs);result['tier_probabilities']['4']=10 if index==target else 0
            return result
        with patch.object(policy,'_score',side_effect=scored):result,_=invoke(joint,members,tails,priority,source)
        self.assertTrue(result['evidence']['intervened'])
        joint_source=policy._joint(source)['evidence']
        self.assertEqual([d['send'] for d in result['decisions']],joint_source['combinations'][target]['hospitals'])
        for d in result['decisions']:
            self.assertEqual(d['reason'],'cohort_dynamic_current_only')
            self.assertFalse(set(d['evidence'])&{'joint_first','joint_tail','joint_budget','cohort_success'})
            self.assertIn('cohort_dynamic',d['evidence'])

    def test_final_tie_preserves_exact_frozen_CS_output_flags(self):
        joint,members,tails,priority,source=fixture(4)
        def scored(index,*a,**kw):return score(index)
        with patch.object(policy,'_score',side_effect=scored):result,_=invoke(joint,members,tails,priority,source)
        self.assertFalse(result['evidence']['intervened'])
        for new,old in zip(result['decisions'],source['decisions']):
            new['evidence'].pop('cohort_dynamic');new['evidence']['policy']=policy.BASE_POLICY
            self.assertEqual(new,old)

    def test_missing_singleton_or_priority_fails_before_CS_call(self):
        joint,members,tails,priority,_=fixture(5)
        for badtails,badpriority in ((tails[:-1],priority),(tails,dict(priority,patients=priority['patients'][:-1]))):
            with patch.object(policy.original,'decide_joint') as called:
                with self.assertRaises(ValueError):policy.decide_joint(policy.NAME,members=members,joint_context=joint,
                    tail_members=badtails,priority_context=badpriority)
                called.assert_not_called()

    def test_partial_H_and_immutable_sources_survive_repeated_row_projection(self):
        joint,members,tails,priority,source=fixture(5,17)
        before=deepcopy((joint,members,tails,priority,source))
        first,_=invoke(joint,members,tails,priority,source);second,_=invoke(joint,members,tails,priority,source)
        self.assertEqual(first,second);self.assertEqual((joint,members,tails,priority,source),before)
        for row in first['evidence']['row_scores']:
            singleton=row['projection']['singleton']
            self.assertEqual(singleton['proposal']['evidence']['time_horizon']['cell_edges_seconds'],[5,10,15,17])
            self.assertTrue(all(0<=r['metrics']['capped_wait_seconds']<=17 for r in row['member_metrics']))


if __name__=='__main__':unittest.main()
