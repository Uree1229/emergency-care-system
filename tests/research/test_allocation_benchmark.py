"""고정 입력·제약·분모·개발 잠금의 검증."""
from copy import deepcopy
import unittest

from simulation.allocation_scenarios import build_allocation_snapshots
from simulation import allocation_benchmark as benchmark


class AllocationBenchmark(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snapshots = {s['family']:s for s in build_allocation_snapshots(19)}

    def test_every_rule_receives_same_unmutated_snapshot(self):
        for family in ('abundant', 'scarce_greedy_trap', 'committed_queue', 'no_offer_control'):
            snapshot = deepcopy(self.snapshots[family]); original = deepcopy(snapshot)
            results = [benchmark.run_policy(snapshot, code) for code in benchmark.POLICIES]
            self.assertEqual(snapshot, original)
            self.assertEqual({b['input_sha256'] for b in results}, {benchmark.digest(snapshot)})
            self.assertTrue(all(benchmark.audit_assignment(snapshot, b['assignments']) for b in results))

    def test_cp_and_exact_enumeration_agree_on_objective(self):
        for family, snapshot in self.snapshots.items():
            if family == 'large_batch':
                continue
            left = benchmark.run_policy(snapshot, 'R3')['metrics']
            right = benchmark.run_policy(snapshot, 'C1')['metrics']
            for key in ('unassigned_priority_weight', 'priority_weighted_travel_minutes'):
                self.assertEqual(left[key], right[key], family)

    def test_greedy_trap_is_repaired_without_fabricating_capacity(self):
        snapshot = self.snapshots['scarce_greedy_trap']
        greedy = benchmark.run_policy(snapshot, 'R1')
        joint = benchmark.run_policy(snapshot, 'C1')
        self.assertEqual(greedy['metrics']['assigned_count'], 1)
        self.assertEqual(joint['metrics']['assigned_count'], 2)
        self.assertEqual(joint['metrics']['constraint_violations'], 0)

    def test_no_offer_control_cannot_use_unoffered_free_hospital(self):
        snapshot = self.snapshots['no_offer_control']
        for code in benchmark.POLICIES:
            result = benchmark.run_policy(snapshot, code)
            self.assertEqual(result['metrics']['assigned_count'], 0)
            self.assertIsNone(result['metrics']['travel_mean_minutes'])
            self.assertEqual(result['metrics']['travel_denominator'], 0)

    def test_audit_rejects_missing_ids_unoffered_hospital_and_overallocation(self):
        snapshot = self.snapshots['scarce_greedy_trap']
        pids=[p['id'] for p in snapshot['patients']]
        hids=[h['id'] for h in snapshot['hospitals']]
        for plan in ({pids[0]:None}, {pids[0]:hids[0],pids[1]:hids[1]},
                     {pids[0]:hids[0],pids[1]:hids[0]}):
            with self.assertRaises(ValueError):
                benchmark.audit_assignment(snapshot, plan)

    def test_common_patient_travel_reports_denominator_and_unpaired_assignments(self):
        snapshot=self.snapshots['scarce_greedy_trap']
        base=benchmark.run_policy(snapshot,'R1'); joint=benchmark.run_policy(snapshot,'C1')
        paired=benchmark.paired_common_travel(snapshot,base,joint)
        self.assertEqual(paired['denominator'],1)
        self.assertEqual(paired['comparison_only'],1)
        self.assertEqual(paired['baseline_only'],0)
        self.assertGreater(paired['mean_difference_minutes'],0)

    def test_existing_negative_capacity_cannot_be_used_but_does_not_break_none_plan(self):
        snapshot=self.snapshots['committed_queue']
        world=benchmark.ObservedWorld(snapshot)
        self.assertLess(world.hospitals[snapshot['hospitals'][0]['id']].free('ed_bed'),0)
        self.assertTrue(benchmark.audit_assignment(snapshot,{p['id']:None for p in snapshot['patients']}))

    def test_lock_rejects_changed_settings_source_and_overlapping_seeds(self):
        lock=benchmark.manifest(); benchmark.verify_lock(lock)
        for field in ('settings','source_sha256','validation_seeds'):
            altered=deepcopy(lock); altered[field]={} if field!='validation_seeds' else [19]
            with self.assertRaises(ValueError): benchmark.verify_lock(altered)
        self.assertFalse(set(benchmark.DEV_SEEDS)&set(benchmark.VALIDATION_SEEDS))

    def test_rule_input_has_only_current_fields_and_numeric_weights(self):
        snapshot=self.snapshots['multi_resource']; world=benchmark.ObservedWorld(snapshot)
        self.assertFalse(hasattr(world,'scenario'))
        self.assertFalse(hasattr(world,'outages'))
        self.assertTrue(all(p.priority in (1,2,4) for p in world.patients))
        self.assertTrue(all(q>0 for p in world.patients for q in p.demand.values()))


if __name__=='__main__': unittest.main()
