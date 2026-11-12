"""Synthetic menu, fresh-origin and no-tail projection invariants."""
import ast
from copy import deepcopy
import inspect
import unittest
from unittest.mock import patch
from inquiry_lab import cohort_pairscope_model as model
from test_inquiry_lab_cohort_pairscope_policy import fixture,policy


def prepared_fixture(**kwargs):
    args,anchor=fixture(**kwargs);old=args[-1]['evidence'];full=old['original_waitpareto_proposal']['evidence']['original_pareto_proposal']['evidence']['original_fullmenu_proposal']['evidence']
    return policy._prepare(full,args[1],args[2],args[0],args[3]),anchor,args


class NoTailPairModelTests(unittest.TestCase):
    def test_menu_keeps_anchor_then_every_original_feasible_row_in_order(self):
        table=[dict(feasible=i in (0,2,3,5)) for i in range(6)];before=deepcopy(table)
        menu=model.build_menu(table,3)
        self.assertEqual(menu['menu_indices'],[3,0,2,5]);self.assertEqual(menu['feasible_row_indices'],[0,2,3,5])
        self.assertEqual(table,before)
        for anchor in (1,-1,True,6):
            with self.assertRaises(ValueError):model.build_menu(table,anchor)

    def test_fresh_freeze_includes_all_residual_statuses_and_same_clock(self):
        args,_=fixture(extras=4);before=deepcopy(args)
        with patch.object(model.hedge,'freeze_owner',wraps=model.hedge.freeze_owner) as freeze:
            prepared,_,_=prepared_fixture(extras=4)
        self.assertEqual(freeze.call_count,4);self.assertEqual([o['patient_id'] for o in prepared['origins']],['OWNER0','OWNER1','OWNER2','OWNER3'])
        self.assertEqual(args,before)
        for owner,origin in zip(prepared['residual'],prepared['origins']):
            self.assertEqual(origin['now'],args[0]['now']);self.assertEqual(origin['original_pending'],owner['inputs']['pending'])
        self.assertEqual(prepared['views']['hedge']['evidence']['owner_origins'],[])

    def test_parent_prepare_roadblock_is_not_waived_or_mutated(self):
        p,_,args=prepared_fixture()
        # The new path supplies origins separately; parent immutable evidence stays empty.
        self.assertEqual(p['views']['hedge']['evidence']['owner_origins'],[])
        self.assertEqual(len(p['residual']),3);self.assertEqual(len(p['origins']),3)
        self.assertTrue(all(o['job'] is None or o['job']['policy_name'] in ('eat_cohort_hedge_l15_f','fixed_3') for o in p['owners']['inventory']))

    def test_new_projection_has_current_pair_only_and_all_owner_scores(self):
        prepared,anchor,args=prepared_fixture(extras=3);before=deepcopy(prepared)
        row=model.score_row(anchor,0,prepared);projection=row['dynamic_row_projection']['projection']
        self.assertEqual(projection['steps'],[]);self.assertIsNone(projection['singleton'])
        self.assertEqual([m['patient_id'] for m in row['member_metrics']],args[-2]['first_cohort_patient_ids']+['OWNER0','OWNER1','OWNER2'])
        self.assertEqual(prepared,before)
        self.assertEqual(row['first_plan_source_sha256'],model.envelope._sha(row['dynamic_row_projection']))
        self.assertTrue(all(q['patient'] in args[-2]['first_cohort_patient_ids'] for q in projection['final_pending'][len(projection['initial_pending']):]))

    def test_every_residual_owner_uses_unchanged_Recourse_law(self):
        prepared,anchor,_=prepared_fixture(extras=4);seen=[];score=model.recourse.score_owner
        def record(owner,*rest):seen.append(owner['patient']['id']);return score(owner,*rest)
        with patch.object(model.recourse,'score_owner',side_effect=record):row=model.score_row(anchor,0,prepared)
        self.assertEqual(seen[:4],['OWNER0','OWNER1','OWNER2','OWNER3'])
        self.assertEqual(seen[4:],prepared['owners']['first_cohort_patient_ids'])
        self.assertEqual(len(row['unchanged_law_residual_member_metrics']),4)

    def test_no_residual_case_has_complete_two_owner_partition(self):
        prepared,anchor,_=prepared_fixture(extras=0,statuses=False)
        self.assertEqual(prepared['origins'],[]);row=model.score_row(anchor,0,prepared)
        self.assertEqual(len(row['member_metrics']),2);self.assertEqual(row['unchanged_law_residual_member_metrics'],[])

    def test_invalid_row_position_tail_or_incomplete_origins_rejected(self):
        prepared,anchor,_=prepared_fixture();table=prepared['views']['joint']['combinations']
        bad=next((i for i,r in enumerate(table) if not r['feasible']),len(table))
        for index,position in ((bad,0),(-1,0),(True,0),(anchor,-1),(anchor,True)):
            with patch.object(model.D,'_score') as called:
                with self.assertRaises(ValueError):model.score_row(index,position,prepared)
            called.assert_not_called()
        broken=deepcopy(prepared);broken['origins'].pop()
        with self.assertRaises(ValueError):model.score_row(anchor,0,broken)
        broken=deepcopy(prepared);broken['cs_tails']=[{}]
        with self.assertRaises(ValueError):model.score_row(anchor,0,broken)

    def test_no_audit_or_scenario_dependency_and_only_public_inputs(self):
        source=ast.parse(inspect.getsource(model));imports=[a.name for n in ast.walk(source) if isinstance(n,(ast.Import,ast.ImportFrom)) for a in n.names]
        self.assertFalse(any(any(t in name for t in ('audit','runner','scenario','experiment','oracle')) for name in imports))
        self.assertEqual(set(inspect.signature(model.prepare).parameters),{'proposal','members','tail_members','joint_context','priority_context'})


if __name__=='__main__':unittest.main()
