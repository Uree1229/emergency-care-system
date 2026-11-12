"""Synthetic unchanged parent paths and first-wave support filtering."""
from copy import deepcopy
from functools import lru_cache
import unittest
from unittest.mock import patch
from inquiry_lab import cohort_pairshield_policy as policy
from test_inquiry_lab_cohort_pairscope_policy import fixture as previous_fixture,invoke as previous_invoke,inherited_fixture as previous_inherited


@lru_cache(maxsize=4)
def _source_fixture(count=2,extras=3):
    args,anchor=previous_fixture(count=count,extras=extras);return args[:5]+(previous_invoke(args),),anchor


def fixture(count=2,extras=3):
    args,anchor=deepcopy(_source_fixture(count,extras));j,ms,ts,p,owners,parent=args
    for row in j['eligible']:row['job']['policy_name']=policy.NAME
    for member in ms+ts:member['workflow_snapshot']['job']['policy_name']=policy.NAME
    for owner in owners['inventory']:
        if owner['job'] is not None and owner['job']['policy_name']==policy.BASE_POLICY:owner['job']['policy_name']=policy.NAME
    return (j,ms,ts,p,owners,parent),anchor


def invoke(args):
    j,ms,ts,p,owners,parent=args
    with patch.object(policy.original,'decide_joint',return_value=deepcopy(parent)):
        return policy.decide_joint(policy.NAME,members=ms,joint_context=j,tail_members=ts,priority_context=p,owner_context=owners)


def guarded_choice_fixture():
    """Explicit score/descriptor orchestration mock, not a natural replay."""
    args,anchor=fixture(extras=0);old=args[-1]['evidence'];rows=old['new_row_scores'];target=next(r['row_index'] for r in rows if r['row_index']!=anchor)
    baseline=next(r for r in rows if r['row_index']==anchor)
    targetrow=next(r for r in rows if r['row_index']==target)
    bpeople=baseline['first_cohort_envelope']['patient_scores'];tpeople=targetrow['first_cohort_envelope']['patient_scores']
    owner=next(p['patient_id'] for p,q in zip(bpeople,tpeople) if {it['hospital'] for it in p['fixed_initial_items']}!={it['hospital'] for it in q['fixed_initial_items']})
    bp=next(p for p in bpeople if p['patient_id']==owner);tp=next(p for p in tpeople if p['patient_id']==owner)
    removed=next(h for h in [x['hospital'] for x in bp['fixed_initial_items']] if h not in {x['hospital'] for x in tp['fixed_initial_items']})
    for row in rows:
        for person in row['first_cohort_envelope']['patient_scores']:
            for it in person['fixed_initial_items']:
                if person['patient_id']==owner and it['hospital']==removed:
                    it['wait_seconds']=person['cell_edges_seconds'][-1];it['masses']=[0.]*len(person['cell_edges_seconds'])
        for member in row['member_metrics']:member['metrics'].update(selection_probability=.5,capped_wait_seconds=50. if row['row_index']==target else 100.,total_cost_seconds=123.4567)
        row['tier_probabilities']={str(t):sum(m['metrics']['selection_probability'] for m in row['member_metrics'] if m['effective_priority']==t) for t in (4,2,1)}
        row['sum_capped_wait_seconds']=sum(m['metrics']['capped_wait_seconds'] for m in row['member_metrics'])
    old['owner_probability_guard']=policy.original.original.protection.guard_rows(rows,baseline_index=anchor,owner_inventory=args[-2]['inventory'],menu_indices=old['new_menu_indices'],feasible_row_indices=old['new_feasible_row_indices'])
    old['protected_wait_selection']=policy.model.selector.select_protected_wait(rows,baseline_index=anchor,owner_inventory=args[-2]['inventory'],admissible_row_indices=old['owner_probability_guard']['admissible_row_indices'],menu_indices=old['new_menu_indices'],feasible_row_indices=old['new_feasible_row_indices'])
    old['selected_index']=old['protected_wait_selection']['selected_index'];assert old['selected_index']==target
    return args,anchor,target


class PairShieldPolicyTests(unittest.TestCase):
    def test_new_parent_group_shield_preserves_entire_source_and_values(self):
        args,_=fixture();before=deepcopy(args);e=invoke(args)['evidence']
        self.assertEqual(args,before);self.assertEqual(e['original_pairscope_proposal'],args[-1]);self.assertTrue(e['shield_evaluated'])
        self.assertEqual(e['protected_support_selection']['original_probability_domain_selection'],args[-1]['evidence']['protected_wait_selection'])
        self.assertTrue(e['mean_queue_support_guard']['baseline_always_admissible'])

    def test_removed_zero_contact_can_restore_original_anchor_without_new_scores(self):
        args,anchor,target=guarded_choice_fixture();before=deepcopy(args)
        with patch.object(policy.original.model,'score_row',side_effect=AssertionError('no new scoring')):
            result=invoke(args)
        e=result['evidence'];self.assertEqual(args,before);self.assertEqual(e['base_selected_index'],target);self.assertEqual(e['selected_index'],anchor);self.assertTrue(e['intervened'])
        self.assertIn(target,e['mean_queue_support_guard']['rejected_row_indices'])
        self.assertEqual(e['original_pairscope_proposal'],args[-1])

    def test_changed_output_is_current_pair_from_original_joint_table(self):
        args,anchor,_=guarded_choice_fixture();result=invoke(args);e=result['evidence']
        env=args[-1]['evidence']['original_envelope_proposal']['evidence'];full=env['original_waitpareto_proposal']['evidence']['original_pareto_proposal']['evidence']['original_fullmenu_proposal']['evidence']
        table=policy.original.model.fullmenu.parent_views(full['original_stoploss_proposal'])['joint']
        self.assertEqual([d['send'] for d in result['decisions']],table['combinations'][anchor]['hospitals'])
        self.assertTrue(all(d['reason']=='cohort_pairshield_current_only' for d in result['decisions']))
        self.assertTrue(all(d['evidence']['planned_expected_cost']==123.457 for d in result['decisions']))

    def test_zero_veto_case_copies_exact_parent_decision_core(self):
        args,_=fixture();result=invoke(args);self.assertFalse(result['evidence']['intervened'])
        for new,old in zip(result['decisions'],args[-1]['decisions']):
            new['evidence'].pop('cohort_pairshield');new['evidence']['policy']=policy.BASE_POLICY;self.assertEqual(new,old)

    def test_old_active_parent_path_does_not_call_shield_or_selector(self):
        previous=previous_inherited();parent=previous_invoke(previous);j,ms,ts,p,owners,_=deepcopy(previous)
        for row in j['eligible']:row['job']['policy_name']=policy.NAME
        for member in ms+ts:member['workflow_snapshot']['job']['policy_name']=policy.NAME
        for owner in owners['inventory']:
            if owner['job'] is not None and owner['job']['policy_name']==policy.BASE_POLICY:owner['job']['policy_name']=policy.NAME
        args=(j,ms,ts,p,owners,parent)
        with patch.object(policy.model,'guard_rows') as guard,patch.object(policy.model,'select_supported_wait') as choose:result=invoke(args)
        guard.assert_not_called();choose.assert_not_called();self.assertFalse(result['evidence']['shield_evaluated'])
        for new,old in zip(result['decisions'],parent['decisions']):
            new['evidence'].pop('cohort_pairshield');new['evidence']['policy']=policy.BASE_POLICY;self.assertEqual(new,old)

    def test_one_tail_inactive_parent_path_unchanged(self):
        args,_=fixture(count=3,extras=1)
        with patch.object(policy.model,'guard_rows') as guard:result=invoke(args)
        guard.assert_not_called();self.assertFalse(result['evidence']['shield_evaluated']);self.assertFalse(result['evidence']['evaluated'])

    def test_native_omission_and_inconsistent_parent_scope_rejected(self):
        args,_=fixture();args[-2]['inventory'].pop()
        with patch.object(policy.original,'decide_joint') as parent:
            with self.assertRaises(ValueError):invoke(args)
        parent.assert_not_called()
        args,_=fixture();args[-1]['evidence']['inherited_evaluated']=True
        with self.assertRaises(ValueError):invoke(args)

    def test_spec_and_singleton_fallback_preserve_parent_core(self):
        old=policy.original.spec(policy.BASE_POLICY);new=policy.spec()
        for key in ('name','label'):old.pop(key);new.pop(key)
        self.assertEqual(old,new);args,_=fixture();inputs=args[1][0]['inputs']
        old=policy.original.decide(policy.BASE_POLICY,**deepcopy(inputs));new=policy.decide(policy.NAME,**deepcopy(inputs))
        self.assertEqual(new['evidence'].pop('cohort_pairshield'),dict(schema_version=1,mode='horizon_fallback',evaluated=False,intervened=False))
        new['evidence']['policy']=policy.BASE_POLICY;self.assertEqual(new,old)


if __name__=='__main__':unittest.main()
