"""Synthetic exhaustive-menu orchestration and current-only transformations."""
from copy import deepcopy
import unittest
from unittest.mock import patch
from inquiry_lab import cohort_fullmenu_policy as policy
from test_inquiry_lab_cohort_fullmenu_model import source_fixture,expanded_source_fixture


def fixture(count=4,extras=1,expanded=True):
    j,ms,ts,p,owners,parent=(expanded_source_fixture if expanded else source_fixture)(count,extras)
    for row in j['eligible']:row['job']['policy_name']=policy.NAME
    for member in ms+ts:member['workflow_snapshot']['job']['policy_name']=policy.NAME
    for owner in owners['inventory']:
        if owner['job'] is not None and owner['job']['policy_name']==policy.BASE_POLICY:owner['job']['policy_name']=policy.NAME
    return j,ms,ts,p,owners,parent


def invoke(args):
    j,ms,ts,p,owners,parent=args
    with patch.object(policy.original,'decide_joint',return_value=deepcopy(parent)):
        return policy.decide_joint(policy.NAME,members=ms,joint_context=j,tail_members=ts,priority_context=p,owner_context=owners)


class FullmenuPolicyTests(unittest.TestCase):
    def test_parent_proposal_rows_and_menu_prefix_exact_all_feasible_covered(self):
        args=fixture();before=deepcopy(args);result=invoke(args);e=result['evidence'];old=args[-1]['evidence']
        self.assertEqual(args,before);self.assertEqual(e['original_stoploss_proposal'],args[-1])
        self.assertEqual(e['parent_menu_indices'],old['menu_indices'])
        self.assertEqual(e['row_scores'][:len(old['menu_indices'])],old['row_scores'])
        self.assertEqual(e['menu_indices'][:len(old['menu_indices'])],old['menu_indices'])
        table=policy.model.parent_views(args[-1])['joint']['combinations']
        feasible=[i for i,row in enumerate(table) if row['feasible']]
        self.assertEqual(e['feasible_row_indices'],feasible)
        self.assertEqual(set(e['menu_indices']),set(feasible));self.assertEqual(len(e['menu_indices']),len(feasible))
        self.assertGreater(len(e['menu_indices']),3)
        self.assertEqual([r['row_index'] for r in e['row_scores']],e['menu_indices'])
        self.assertEqual(e['coverage'],dict(original_joint_row_count=len(table),parent_menu_row_count=len(old['menu_indices']),
            evaluated_row_count=len(feasible),expanded_row_count=len(feasible)-len(old['menu_indices']),feasible_row_count=len(feasible)))

    def test_only_added_rows_rebuilt_sequentially_with_final_positions(self):
        args=fixture()
        with (patch.object(policy.model,'prepare',wraps=policy.model.prepare) as prep,
                patch.object(policy.model,'score_added_row',wraps=policy.model.score_added_row) as score):
            e=invoke(args)['evidence']
        self.assertEqual(prep.call_count,1);self.assertEqual(score.call_count,len(e['added_row_indices']))
        self.assertEqual([(c.args[0],c.args[1]) for c in score.call_args_list],
            [(i,e['menu_indices'].index(i)) for i in e['added_row_indices']])

    def test_unseen_feasible_winner_can_replace_parent_current_choice(self):
        args=fixture();table=policy.model.parent_views(args[-1])['joint']['combinations']
        target=next(i for i,row in enumerate(table) if row['feasible'] and i not in args[-1]['evidence']['menu_indices'])
        original_score=policy.model.score_added_row
        def adversarial(index,position,prepared):
            row=original_score(index,position,prepared)
            if index==target:
                for member in row['member_metrics']:
                    member['metrics'].update(selection_probability=1.,capped_wait_seconds=0.,total_cost_seconds=123.4567)
                row['tier_probabilities']={str(t):sum(m['effective_priority']==t for m in row['member_metrics']) for t in policy.TIERS}
                row['sum_capped_wait_seconds']=0.
            return row
        with patch.object(policy.model,'score_added_row',side_effect=adversarial):result=invoke(args)
        self.assertEqual(result['evidence']['selected_index'],target);self.assertTrue(result['evidence']['intervened'])
        self.assertEqual([r['send'] for r in result['decisions']],table[target]['hospitals'])
        for decision in result['decisions']:
            self.assertEqual(decision['reason'],'cohort_fullmenu_current_only')
            self.assertEqual(decision['evidence']['planned_expected_cost'],123.457)
            self.assertFalse(set(decision['evidence'])&{'cohort_stoploss','cohort_followplan','cohort_recourse','cohort_hedge','cohort_dynamic'})
            self.assertTrue(all(row['planned_offset_seconds'] in (0,None) for row in decision['evidence']['candidate_plan']))

    def test_infeasible_rows_never_reach_added_score(self):
        args=fixture(expanded=False);seen=[];scorer=policy.model.score_added_row
        def record(index,position,prepared):seen.append(index);return scorer(index,position,prepared)
        with patch.object(policy.model,'score_added_row',side_effect=record):e=invoke(args)['evidence']
        table=policy.model.parent_views(args[-1])['joint']['combinations']
        self.assertTrue(all(table[i]['feasible'] for i in seen))
        self.assertFalse(set(seen)&{i for i,row in enumerate(table) if not row['feasible']})
        self.assertEqual(seen,e['added_row_indices'])

    def test_exact_tie_keeps_parent_stoploss_actual_index_and_output_core(self):
        args=fixture();base=args[-1]['evidence']['selected_index'];template=next(r for r in args[-1]['evidence']['row_scores'] if r['row_index']==base)
        # An orchestration fixture assigns identical final objective values to
        # every row; it does not claim the frozen analytic scores naturally tie.
        for old in args[-1]['evidence']['row_scores']:
            old['tier_probabilities']=deepcopy(template['tier_probabilities']);old['sum_capped_wait_seconds']=template['sum_capped_wait_seconds']
        real=policy.model.score_added_row
        def tied(index,position,prepared):
            row=real(index,position,prepared);row['tier_probabilities']=deepcopy(template['tier_probabilities'])
            row['sum_capped_wait_seconds']=template['sum_capped_wait_seconds'];return row
        with patch.object(policy.model,'score_added_row',side_effect=tied):result=invoke(args)
        self.assertEqual(result['evidence']['selected_index'],base);self.assertFalse(result['evidence']['intervened'])
        for new,old in zip(result['decisions'],args[-1]['decisions']):
            new['evidence'].pop('cohort_fullmenu');new['evidence']['policy']=policy.BASE_POLICY;self.assertEqual(new,old)

    def test_wait_improvement_cannot_override_tier_success_loss(self):
        parent=dict(row_index=7,tier_probabilities={'4':1.4,'2':.2,'1':.1},sum_capped_wait_seconds=100.)
        added=dict(row_index=3,tier_probabilities={'4':1.4-2e-12,'2':.2,'1':.1},sum_capped_wait_seconds=0.)
        self.assertEqual(policy.D._select([parent,added],7)['selected_index'],7)
        added['tier_probabilities']=deepcopy(parent['tier_probabilities'])
        self.assertEqual(policy.D._select([parent,added],7)['selected_index'],3)
        added['sum_capped_wait_seconds']=100.
        self.assertEqual(policy.D._select([added,parent],7)['selected_index'],7)

    def test_public_patient_renaming_preserves_scores_coverage_and_actions(self):
        args=fixture();result=invoke(args)
        # Keep registry lexical ordering coherent while changing every public
        # patient identifier throughout this synthetic source graph.
        names={row['patient']['id']:'RENAMED_'+str(row['patient']['id']) for row in args[-2]['inventory']}
        def rename(value):
            if isinstance(value,list):return [rename(item) for item in value]
            if isinstance(value,tuple):return tuple(rename(item) for item in value)
            if isinstance(value,dict):return {key:rename(item) for key,item in value.items()}
            return names.get(value,value) if isinstance(value,str) else value
        changed=invoke(rename(args))
        self.assertEqual(changed,rename(result))

    def test_incomplete_parent_row_inventory_rejected(self):
        args=list(fixture());args[-1]['evidence']['row_scores'].pop()
        with patch.object(policy.model,'score_added_row') as score:
            with self.assertRaises(ValueError):invoke(args)
        score.assert_not_called()

    def test_unevaluated_parent_keeps_fallback_and_skips_added_pipeline(self):
        args=list(fixture());args[-1]['evidence'].update(evaluated=False,menu_indices=[],row_scores=[])
        with patch.object(policy.model,'prepare') as prepared,patch.object(policy.model,'score_added_row') as score:
            result=invoke(args)
        prepared.assert_not_called();score.assert_not_called()
        for key in ('menu_indices','parent_menu_indices','added_row_indices','feasible_row_indices','row_scores'):
            self.assertEqual(result['evidence'][key],[])
        self.assertEqual(set(result['evidence']['coverage'].values()),{0})
        for new,old in zip(result['decisions'],args[-1]['decisions']):
            new['evidence'].pop('cohort_fullmenu');new['evidence']['policy']=policy.BASE_POLICY;self.assertEqual(new,old)

    def test_native_validation_precedes_parent_and_scoring(self):
        args=list(fixture());args[-2]['inventory'].pop()
        with patch.object(policy.original,'decide_joint') as parent:
            with self.assertRaises(ValueError):invoke(args)
        parent.assert_not_called()

    def test_spec_and_ungrouped_fallback_preserve_parent_core(self):
        before=policy.original.spec(policy.BASE_POLICY);after=policy.spec()
        for key in ('name','label'):before.pop(key);after.pop(key)
        self.assertEqual(before,after);self.assertIs(policy.D._select,policy.original.D._select)
        inputs=fixture(extras=0)[1][0]['inputs']
        old=policy.original.decide(policy.BASE_POLICY,**deepcopy(inputs));new=policy.decide(policy.NAME,**deepcopy(inputs))
        self.assertEqual(new['evidence'].pop('cohort_fullmenu'),dict(schema_version=1,mode='horizon_fallback',evaluated=False,intervened=False))
        new['evidence']['policy']=policy.BASE_POLICY;self.assertEqual(new,old)


if __name__=='__main__':unittest.main()
