"""G5 real replay accounting, frozen-G2 regression and audit tamper tests."""
from collections import Counter
from copy import deepcopy
import gzip
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from simulation import final_assignment_scenarios_20261028 as scenarios
from simulation.arrival_guard_priority_engine_20261028 import run_guard_replay
from simulation.g2_preserving_replay_20261029 import run_preserving_replay,audit_preserving_replay
from simulation import g2_preserving_audit_20261029 as auditor
from simulation.online_allocation_audit import OnlineAllocationAuditError


class G2PreservingReplayTests(unittest.TestCase):
    def case(self,family,seed=77):
        return next(s for s in scenarios.build_stress_cases(seed) if s['family']==family)

    def test_general_and_all_stress_families_have_independent_audits(self):
        inputs=[scenarios.build_case(s,96) for s in scenarios.STRATA]+scenarios.build_stress_cases(77)
        for source in inputs:
            with self.subTest(case=source['id']):
                before=deepcopy(source);replay=run_preserving_replay(source);saved=deepcopy(replay)
                audit=audit_preserving_replay(replay,source_scenario=source)
                self.assertTrue(audit['passed'])
                self.assertFalse(audit['allocator_reexecuted'])
                self.assertFalse(audit['whole_episode_nonregression_verified'])
                self.assertEqual(source,before);self.assertEqual(replay,saved)
                self.assertEqual(set(audit['patient_vectors']),{'G5'})
                self.assertEqual(len(audit['patient_vectors']['G5']),len(source['patients']))

    def test_stress_endpoints_preserve_actual_executed_g2(self):
        for family in ('scarce_greedy_trap','scarce_long_detour','large_simultaneous','abundant_control','no_capacity_control'):
            source=self.case(family)
            old=run_guard_replay(source)['branches'][0]
            new=run_preserving_replay(source)['branches'][0]
            with self.subTest(family=family):
                self.assertEqual(new['patient_outcomes'],old['patient_outcomes'])
                self.assertEqual([d['assignments'] for d in new['allocation_decisions']],
                                 [d['assignments'] for d in old['allocation_decisions']])
                self.assertTrue(all(r['actual_plan']==r['g2_plan'] for r in new['preserving_records']))

    def test_known_85_minute_episode_does_not_undo_priority4_benefit(self):
        path=Path(__file__).resolve().parents[2]/'.codex-build/expanded-samples-20261029/raw/main_surge_0096.json.gz'
        if not path.exists():self.skipTest('immutable local regression artifact absent')
        bundle=json.loads(gzip.decompress(path.read_bytes()))
        old=bundle['candidate']['branches'][0]
        source=bundle['candidate']['original_scenario']
        replay=run_preserving_replay(source);audit=audit_preserving_replay(replay,source_scenario=source)
        old_by={p['id']:p for p in old['patient_outcomes']}
        new_by={p['id']:p for p in replay['branches'][0]['patient_outcomes']}
        self.assertEqual(old_by['P12']['final_arrive_t'],14)
        self.assertEqual(new_by['P12'],old_by['P12'])
        self.assertEqual(new_by['P08'],old_by['P08'])
        self.assertTrue(audit['passed'])

    def test_audit_never_executes_candidate_allocator(self):
        source=self.case('scarce_greedy_trap');replay=run_preserving_replay(source)
        with patch('simulation.g2_preserving_assignment_20261029.G2PreservingAssignmentRule.decide_batch',
                   side_effect=AssertionError('audit reexecuted candidate')):
            self.assertTrue(audit_preserving_replay(replay,source_scenario=source)['passed'])

    def test_audit_rejects_tampered_constraints_lineage_counts_and_indices(self):
        source=self.case('scarce_greedy_trap');replay=run_preserving_replay(source)
        mutations=('selected','high','footprint','saved','reason','budget','partial_budget','counter',
                   'record_index','decision_index','orphan','source','guarantee','shortcut','label')
        for mutation in mutations:
            bad=deepcopy(replay);branch=bad['branches'][0]
            record=next(r for r in branch['preserving_records'] if r['selected_patient_ids'])
            if mutation=='selected':record['selected_patient_ids']=[]
            elif mutation=='high':record['priority4_destinations']={'FAKE':'FAKE'}
            elif mutation=='footprint':record['actual_resource_footprint'][0]['quantity']+=1
            elif mutation=='saved':record['travel_saved_minutes'][record['selected_patient_ids'][0]]+=1
            elif mutation=='reason':record['reason']='strict_G2_preserving_pareto_refinement'
            elif mutation=='budget':record['total_work_units']=2049
            elif mutation=='partial_budget':record['work_units']=2049
            elif mutation=='counter':record['accepted_improvements']=record['evaluated_plans']+1
            elif mutation=='record_index':
                next(d for d in branch['allocation_decisions'] if d['kind']=='batch')['preserving_record_index']=9999
            elif mutation=='decision_index':record['decision_index']=9999
            elif mutation=='orphan':branch['preserving_records'].append(deepcopy(record))
            elif mutation=='source':record['parent_g2_source_sha256']='0'*64
            elif mutation=='guarantee':record['whole_episode_nonregression_guaranteed']=True
            elif mutation=='shortcut':record['g2_seed_mode']='singleton_guard_cannot_gain'
            elif mutation=='label':branch['policy']['rule']='G2'
            with self.subTest(mutation=mutation),self.assertRaises((OnlineAllocationAuditError,AssertionError)):
                audit_preserving_replay(bad,source_scenario=source)

    def test_audit_rejects_reused_record_reference(self):
        source=scenarios.build_case('routine_stream',96);replay=run_preserving_replay(source)
        branch=replay['branches'][0]
        decisions=[d for d in branch['allocation_decisions'] if d['kind']=='batch']
        self.assertGreaterEqual(len(decisions),2)
        decisions[1]['preserving_record_index']=decisions[0]['preserving_record_index']
        with self.assertRaises((OnlineAllocationAuditError,AssertionError)):
            audit_preserving_replay(replay,source_scenario=source)

    def test_batch_audit_rejects_equal_travel_swap_without_strict_gain(self):
        # A real two-patient replay with equal travel and one slot per hospital.
        # The isolated batch audit is tested because changing actual events too
        # would trigger the general event audit before this strict-gain check.
        source=self.case('abundant_control')
        source['patients']=source['patients'][:2]
        for p in source['patients']:p['priority']=2
        for h in source['hospitals']:
            h['capacity']={r:1 for r in h['capacity']}
        replay=run_preserving_replay(source);branch=replay['branches'][0]
        self.assertTrue(audit_preserving_replay(replay,source_scenario=source)['passed'])
        record=deepcopy(branch['preserving_records'][0])
        decision=deepcopy(branch['allocation_decisions'][record['decision_index']])
        ids=record['selected_patient_ids'];self.assertEqual(len(ids),2)
        a,b=ids
        self.assertNotEqual(record['g2_plan'][a],record['g2_plan'][b])
        new=dict(record['g2_plan']);new[a],new[b]=new[b],new[a]
        decision['assignments']=dict(new);record['actual_plan']=dict(new)
        record['actual_travel_objective'][2]=2
        record['reason']='strict_G2_preserving_pareto_refinement'
        with self.assertRaisesRegex(OnlineAllocationAuditError,'strict travel gain'):
            auditor._batch(replay['scenario'],decision,record,record['decision_index'],Counter())


if __name__=='__main__':unittest.main()
