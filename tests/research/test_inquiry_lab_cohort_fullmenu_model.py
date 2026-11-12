"""Tiny synthetic menu coverage and frozen-stage reconstruction only."""
import ast
from copy import deepcopy
from functools import lru_cache
import inspect
import unittest
from unittest.mock import patch
from inquiry_lab import cohort_fullmenu_model as model
from test_inquiry_lab_cohort_stoploss_policy import fixture as stoploss_fixture,invoke as stoploss_invoke


@lru_cache(maxsize=3)
def _source_fixture(count=4,extras=1):
    # The existing functional fixture has three current offers, two tail
    # offers and public same-clock projections; no performance generator.
    args=stoploss_fixture(count=count,extras=extras)
    return args[:5]+(stoploss_invoke(args),)


def source_fixture(count=4,extras=1):return deepcopy(_source_fixture(count,extras))


def expanded_source_fixture(count=4,extras=1):
    """Declared feasibility mock for coverage tests, not a natural guard claim."""
    args=source_fixture(count,extras);table=model.parent_views(args[-1])['joint']['combinations']
    for row in table:row['feasible']=True
    return args


def prepared_fixture(count=4,extras=1,expanded=False):
    j,ms,ts,p,owners,parent=(expanded_source_fixture if expanded else source_fixture)(count,extras)
    return model.prepare(parent,members=ms,tail_members=ts,joint_context=j,priority_context=p),parent


class FullFeasibleMenuTests(unittest.TestCase):
    def test_three_hospital_menu_preserves_parent_order_then_all_feasible_indices(self):
        table=[dict(feasible=i in (0,2,3,5,6,8)) for i in range(9)]
        before=deepcopy(table);menu,added,feasible=model.full_feasible_menu([5,2,0],table)
        self.assertEqual(menu,[5,2,0,3,6,8]);self.assertEqual(added,[3,6,8])
        self.assertEqual(feasible,[0,2,3,5,6,8]);self.assertEqual(table,before)

    def test_two_hospital_table_has_no_three_row_beam_or_duplicate(self):
        table=[dict(feasible=True) for _ in range(4)]
        menu,added,feasible=model.full_feasible_menu([2],table)
        self.assertEqual(menu,[2,0,1,3]);self.assertEqual(added,[0,1,3])
        self.assertEqual(set(menu),set(feasible));self.assertEqual(len(menu),4)

    def test_no_added_row_when_parent_already_covers_every_feasible_row(self):
        menu,added,feasible=model.full_feasible_menu([2,0],[dict(feasible=True),dict(feasible=False),dict(feasible=True)])
        self.assertEqual(menu,[2,0]);self.assertEqual(added,[]);self.assertEqual(feasible,[0,2])

    def test_invalid_or_infeasible_parent_and_unknown_flags_rejected(self):
        table=[dict(feasible=True),dict(feasible=False)]
        for parent in ([],[0,0],[1],[True],[-1],[2],[0,1,2,3]):
            with self.assertRaises(ValueError):model.full_feasible_menu(parent,table)
        for invalid in ([],[dict(feasible=1)],[{}],[dict(feasible=True)]*401):
            with self.assertRaises(ValueError):model.full_feasible_menu([0],invalid)


class FrozenStageReconstructionTests(unittest.TestCase):
    def test_prepare_projects_each_native_identity_without_mutating_inputs(self):
        args=source_fixture();before=deepcopy(args);j,ms,ts,p,owners,parent=args
        prepared=model.prepare(parent,members=ms,tail_members=ts,joint_context=j,priority_context=p)
        for field,name in (('cs_members','eat_cohort_success_l15_f'),('hedge_members','eat_cohort_hedge_l15_f'),
                ('recourse_members','eat_cohort_recourse_l15_f')):
            self.assertTrue(all(m['workflow_snapshot']['job']['policy_name']==name for m in prepared[field]))
        self.assertTrue(all(r['job']['policy_name']=='eat_cohort_success_l15_f' for r in prepared['cs_context']['eligible']))
        self.assertEqual(args,before)
        self.assertIs(prepared['views']['dynamic'],parent['evidence']['original_followplan_proposal']['evidence']
            ['original_recourse_proposal']['evidence']['original_hedge_proposal']['evidence']['original_dynamic_proposal'])

    def test_reconstructing_original_rows_matches_parent_final_scores_exactly(self):
        prepared,parent=prepared_fixture(extras=0);before=deepcopy(parent)
        for position,old in enumerate(parent['evidence']['row_scores']):
            rebuilt=model.score_added_row(old['row_index'],position,prepared)
            for field in ('member_metrics','tier_probabilities','sum_capped_wait_seconds'):
                self.assertEqual(rebuilt[field],old[field])
            dynamic=old['base_row_score']['base_row_score']['base_row_score']['first_cohort_score']
            self.assertEqual(rebuilt['dynamic_row_projection'],dynamic)
        self.assertEqual(parent,before)

    def test_added_proof_retains_projection_and_final_metrics_only(self):
        prepared,parent=prepared_fixture(expanded=True);joint=prepared['views']['joint']
        menu,added,_=model.full_feasible_menu(parent['evidence']['menu_indices'],joint['combinations'])
        self.assertTrue(added)
        row=model.score_added_row(added[0],menu.index(added[0]),prepared)
        self.assertEqual(set(row),{'row_index','row_kind','dynamic_row_projection','member_metrics',
            'tier_probabilities','sum_capped_wait_seconds'})
        self.assertEqual(row['row_kind'],'expanded')
        self.assertEqual(row['dynamic_row_projection']['projection']['current_pair_send'],joint['combinations'][added[0]]['hospitals'])
        self.assertEqual(len(row['member_metrics']),len(prepared['owners']['inventory']))
        self.assertFalse(set(row)&{'first_cohort','first_cohort_future','first_cohort_stoploss','owner_scores','base_row_score'})

    def test_singleton_and_residual_owner_stages_preserve_complete_partition(self):
        prepared,parent=prepared_fixture(count=5,extras=1)
        index=parent['evidence']['menu_indices'][0]
        row=model.score_added_row(index,0,prepared)
        self.assertIsNotNone(row['dynamic_row_projection']['projection']['singleton'])
        self.assertEqual([m['patient_id'] for m in row['member_metrics']],prepared['owners']['first_cohort_patient_ids']+['OWNER0'])

    def test_invalid_added_row_and_position_rejected_before_dynamic(self):
        prepared,parent=prepared_fixture();table=prepared['views']['joint']['combinations']
        invalid=next(i for i,row in enumerate(table) if not row['feasible'])
        for index,position in ((invalid,0),(-1,0),(len(table),0),(True,0),(0,-1),(0,True)):
            with patch.object(model.dynamic,'_score') as scored:
                with self.assertRaises(ValueError):model.score_added_row(index,position,prepared)
            scored.assert_not_called()

    def test_no_audit_or_simulator_dependency_and_stage_functions_remain_frozen(self):
        source=ast.parse(inspect.getsource(model))
        imported=[alias.name for node in ast.walk(source) if isinstance(node,(ast.Import,ast.ImportFrom)) for alias in node.names]
        self.assertFalse(any(any(token in name for token in ('audit','runner','simulation','experiment','scenario')) for name in imported))
        self.assertIs(model.stoploss.original,model.followplan)
        self.assertIs(model.recourse.D,model.dynamic)


if __name__=='__main__':unittest.main()
