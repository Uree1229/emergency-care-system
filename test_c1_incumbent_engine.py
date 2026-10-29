"""Real seed-7 workflow checks; raw diagnostics saved before assertions."""
from copy import deepcopy
import gzip
import json
from pathlib import Path
import unittest
from uuid import uuid4

from simulation.c1_incumbent_engine import run_c1_incumbent_branch,EXPERIMENT_ID,NAMESPACE_FIELDS
from simulation.c1_incumbent_scenarios import make_scenario,ADDITIONAL_FAMILIES
from simulation.recovery_rank_engine import (IndependentNormalRecoveryRankReplay,
    CooperativeNormalRecoveryRankReplay,RECOVERY_RANK_SPEC,RECOVERY_RANK_FOUNDATION_SPEC)

DIAGNOSTICS=Path(__file__).resolve().parent/'.codex-build/c1-incumbent-20261020/engine-diagnostics'


def save_raw(case,branch):
    DIAGNOSTICS.mkdir(parents=True,exist_ok=True)
    data=(json.dumps(dict(schema_version=1,experiment_id='c1_incumbent_engine_diagnostic_20261020',
        scenario=deepcopy(case),branches=[branch],execution_order=[branch['id']]),
        ensure_ascii=False,allow_nan=False,separators=(',',':'))+'\n').encode()
    path=DIAGNOSTICS/f'{case["family"]}-{branch["id"]}-{uuid4().hex}.json.gz'
    with path.open('xb') as stream:stream.write(gzip.compress(data,mtime=0))
    return path


class IncumbentEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases={};cls.branches={};cls.paths={}
        for family in ('out_of_order',)+ADDITIONAL_FAMILIES:
            case=make_scenario(7,family);cls.cases[family]=case
            budget=case.get('experiment',{}).get('c1_budget_seconds',.05)
            for allocation,seeded in (('C1',False),('C1',True),('H1',False)):
                key=(family,allocation,seeded)
                branch=run_c1_incumbent_branch(case,allocation=allocation,seeded=seeded,budget_seconds=budget)
                cls.paths[key]=save_raw(case,branch);cls.branches[key]=branch

    def test_actual_workflow_classes_and_descriptors_are_original(self):
        for (family,allocation,seeded),branch in self.branches.items():
            mode='cooperative_queue' if self.cases[family].get('inquiry_queue',{}).get('enabled',False) else 'independent_replies'
            replay=CooperativeNormalRecoveryRankReplay if mode=='cooperative_queue' else IndependentNormalRecoveryRankReplay
            self.assertEqual(branch['c1_incumbent']['actual_replay_class'],f'{replay.__module__}.{replay.__name__}')
            self.assertEqual(branch['recovery_rank_spec'],RECOVERY_RANK_SPEC)
            self.assertEqual(branch['recovery_rank_foundation_spec'],dict(RECOVERY_RANK_FOUNDATION_SPEC,mode=mode))
            self.assertEqual(branch['recovery_rank_variant'],'normal')
            self.assertNotEqual(branch['comparison_id'],EXPERIMENT_ID)
            self.assertEqual(branch['allocation_intervention_id'],EXPERIMENT_ID)
            self.assertFalse(any(k.startswith('public_offer_') or k.startswith('offer_reserve_') for k in branch))

    def test_exact_new_namespace_and_literal_solver_class(self):
        for (family,allocation,seeded),branch in self.branches.items():
            self.assertEqual({k for k in branch if k.startswith('c1_incumbent') or k=='allocation_intervention_id'},NAMESPACE_FIELDS)
            contract=branch['c1_incumbent']
            self.assertEqual(contract['seeded'],seeded)
            expected=('simulation.c1_incumbent_assignment.StableSeededCPSATAssignmentRule' if seeded else
                'simulation.stable_cp_sat_assignment.StableCPSATAssignmentRule' if allocation=='C1' else
                'simulation.heuristic_assignment.HeuristicAssignmentRule')
            self.assertEqual(contract['actual_assignment_class'],expected)

    def test_native_budget_is_common_to_both_c1_branches_and_h1_is_unchanged(self):
        for family in ADDITIONAL_FAMILIES:
            for seeded in (False,True):
                branch=self.branches[family,'C1',seeded]
                self.assertEqual(branch['c1_incumbent']['budget_seconds'],.001)
                self.assertTrue(all(r['time_limit_seconds']==.001 for r in branch['solver_records']))
            branch=self.branches[family,'H1',False]
            self.assertEqual(branch['c1_incumbent']['budget_seconds'],.05)
            self.assertTrue(all(r['time_limit_seconds']==.05 for r in branch['heuristic_records']))

    def test_initializer_records_only_exist_on_actual_seeded_c1(self):
        for (family,allocation,seeded),branch in self.branches.items():
            for record in branch['solver_records']:
                self.assertEqual('incumbent' in record,seeded)
                self.assertIn('stability',record)
                if seeded:
                    self.assertEqual(record['incumbent']['variant'],'single_augment')
                    self.assertEqual(record['incumbent']['max_candidates'],128)
                    self.assertFalse(record['incumbent']['optimality_claimed'])

    def test_original_inquiry_inputs_deadlines_reports_and_physical_gates_remain(self):
        for (family,allocation,seeded),branch in self.branches.items():
            self.assertFalse(branch['resource_updates_enabled'])
            for row in branch['inquiry_decisions']:
                self.assertEqual(len(row['inputs']),7)
                self.assertEqual(row['decision']['evidence']['policy'],'recovery_rank_normal')
            for e in branch['ordered_emissions']:
                if e['type']=='ask':
                    self.assertEqual(e['timeout_at']-e['submitted_at'],self.cases[family].get('timeout_minutes',6))

    def test_raw_diagnostics_and_inputs_remain_complete(self):
        for key,path in self.paths.items():
            raw=json.loads(gzip.decompress(path.read_bytes()))
            self.assertEqual(raw['scenario'],self.cases[key[0]])
            self.assertEqual(raw['branches'],[self.branches[key]])
            self.assertEqual(self.cases[key[0]],make_scenario(7,key[0]))

    def test_invalid_flags_and_budgets_fail(self):
        case=self.cases['out_of_order']
        for kw in (dict(allocation='R1'),dict(seeded=1),dict(budget_seconds=True),
            dict(budget_seconds=-1),dict(budget_seconds=float('inf'))):
            with self.assertRaises(ValueError):run_c1_incumbent_branch(case,**kw)


if __name__=='__main__':unittest.main()
