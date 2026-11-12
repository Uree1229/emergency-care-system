"""Structural tests only; held-out allocation experiment seeds are not run."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from simulation.allocation_scenarios import (
    DEVELOPMENT_SEEDS, FAMILIES, PRIORITIES, RESOURCE_KEYS, VALIDATION_SEEDS,
    build_allocation_snapshots, validate_allocation_snapshot,
)


class AllocationScenariosTest(unittest.TestCase):
    def setUp(self):
        # Tiny structural seed, intentionally outside both experiment splits.
        self.snapshots = build_allocation_snapshots(7)
        self.by_family = {s['family']: s for s in self.snapshots}

    def test_exact_preregistered_families_and_seed_splits(self):
        self.assertEqual(len(FAMILIES), 8)
        self.assertEqual([s['family'] for s in self.snapshots], list(FAMILIES))
        self.assertEqual(DEVELOPMENT_SEEDS, tuple(range(8101, 8109)))
        self.assertEqual(VALIDATION_SEEDS, tuple(range(9101, 9121)))
        self.assertFalse(set(DEVELOPMENT_SEEDS) & set(VALIDATION_SEEDS))

    def test_reproducible_and_json_serializable(self):
        self.assertEqual(self.snapshots, build_allocation_snapshots(7))
        self.assertEqual(self.snapshots, json.loads(json.dumps(self.snapshots)))
        self.assertNotEqual(self.snapshots, build_allocation_snapshots(8))

    def test_family_random_stream_is_independent_of_subset_and_order(self):
        subset = build_allocation_snapshots(7, reversed(FAMILIES))
        self.assertEqual(subset, list(reversed(self.snapshots)))
        for snapshot in self.snapshots:
            self.assertEqual(build_allocation_snapshots(7, [snapshot['family']]), [snapshot])
        self.assertEqual(build_allocation_snapshots(7, []), [])

    def test_results_are_independent_mutable_values(self):
        self.snapshots[0]['hospitals'][0]['capacity']['ed_bed'] = 999
        self.snapshots[0]['patients'][0]['accepted_hospitals'].clear()
        fresh = build_allocation_snapshots(7)
        self.assertNotEqual(self.snapshots[0], fresh[0])

    def test_every_snapshot_is_structurally_valid_and_observed(self):
        for snapshot in self.snapshots:
            validate_allocation_snapshot(snapshot)
            self.assertIs(snapshot['synthetic'], True)
            self.assertEqual(snapshot['at'], 10)
            hospital_ids = {h['id'] for h in snapshot['hospitals']}
            for hospital in snapshot['hospitals']:
                self.assertTrue(hospital['id'].startswith('FICT-'))
                for key in ('capacity', 'occupied', 'committed', 'queued'):
                    self.assertEqual(set(hospital[key]), set(RESOURCE_KEYS))
                    self.assertTrue(all(v >= 0 for v in hospital[key].values()))
            for patient in snapshot['patients']:
                self.assertIn(patient['priority'], PRIORITIES)
                self.assertLessEqual(patient['t_req'], snapshot['at'])
                self.assertEqual(set(patient['travel_minutes']), hospital_ids)
                for at in patient['accepted_at'].values():
                    self.assertLessEqual(patient['t_req'], at)
                    self.assertLessEqual(at, snapshot['at'])
                self.assertNotIn('response_schedule', patient)
                self.assertNotIn('future_events', patient)
                self.assertNotIn('actual_arrival', patient)

    def test_abundant_control_has_no_scarcity_or_travel_preference(self):
        snapshot = self.by_family['abundant']
        n = len(snapshot['patients'])
        for hospital in snapshot['hospitals']:
            self.assertGreaterEqual(hospital['capacity']['ed_bed'], n)
        for patient in snapshot['patients']:
            self.assertEqual(len(patient['accepted_hospitals']), 3)
            self.assertEqual(len(set(patient['travel_minutes'].values())), 1)
            self.assertEqual(len(set(patient['accepted_at'].values())), 1)

    def test_first_reply_is_not_nearest_arrival_control(self):
        patient = self.by_family['first_reply_vs_arrival']['patients'][0]
        first = min(patient['accepted_at'], key=patient['accepted_at'].get)
        nearest = min(patient['travel_minutes'], key=patient['travel_minutes'].get)
        self.assertNotEqual(first, nearest)

    def test_greedy_trap_has_two_feasible_joint_assignments_without_reuse(self):
        snapshot = self.by_family['scarce_greedy_trap']
        flexible, specific = snapshot['patients']
        self.assertEqual(len(flexible['accepted_hospitals']), 2)
        self.assertEqual(len(specific['accepted_hospitals']), 1)
        shared = specific['accepted_hospitals'][0]
        alternative = next(h for h in flexible['accepted_hospitals'] if h != shared)
        self.assertIn(alternative, flexible['accepted_hospitals'])
        self.assertEqual({h['capacity']['ed_bed'] for h in snapshot['hospitals']}, {1})

    def test_multi_resource_and_priority_conflict_are_explicit(self):
        snapshot = self.by_family['multi_resource']
        self.assertTrue(any(p['resource_demands']['special_team'] for p in snapshot['patients']))
        self.assertTrue(any(h['capacity']['special_team'] == 0 for h in snapshot['hospitals']))
        conflict = self.by_family['priority_cardinality']
        high = [p for p in conflict['patients'] if p['priority'] == 'high']
        low = [p for p in conflict['patients'] if p['priority'] == 'low']
        self.assertEqual(len(high), 1)
        self.assertEqual(len(low), 2)
        self.assertEqual(high[0]['resource_demands']['ed_bed'], 2)
        self.assertEqual(sum(p['resource_demands']['ed_bed'] for p in low), 2)

    def test_committed_and_queued_are_not_discarded_or_clamped_in_input(self):
        hospital = self.by_family['committed_queue']['hospitals'][0]
        free = (hospital['capacity']['ed_bed'] - hospital['occupied']['ed_bed']
                - hospital['committed']['ed_bed'] - hospital['queued']['ed_bed'])
        self.assertLess(free, 0)
        self.assertEqual(hospital['queued']['ed_bed'], 1)

    def test_zero_capacity_and_missing_offer_controls_are_distinct(self):
        snapshot = self.by_family['no_offer_control']
        self.assertTrue(any(not p['accepted_hospitals'] for p in snapshot['patients']))
        positive = {h['id'] for h in snapshot['hospitals'] if h['capacity']['ed_bed'] > 0}
        offered = {hid for p in snapshot['patients'] for hid in p['accepted_hospitals']}
        self.assertTrue(positive)
        self.assertFalse(positive & offered)

    def test_large_batch_exceeds_small_enumeration_limit(self):
        snapshot = self.by_family['large_batch']
        self.assertGreaterEqual(len(snapshot['patients']), 14)
        combinations = 1
        for patient in snapshot['patients']:
            combinations *= len(patient['accepted_hospitals']) + 1
        self.assertGreater(combinations, 1_000_000)

    def test_generation_does_not_open_database_or_read_files(self):
        with patch('builtins.open', side_effect=AssertionError('unexpected file read')):
            with patch.object(Path, 'read_text', side_effect=AssertionError('unexpected file read')):
                self.assertEqual(build_allocation_snapshots(7), self.snapshots)

    def test_bad_seed_and_bad_family_selection_are_rejected(self):
        for seed in (-1, 1.5, True, '7', None):
            with self.assertRaises(ValueError):
                build_allocation_snapshots(seed)
        for families in ('abundant', ['unknown'], ['abundant', 'abundant']):
            with self.assertRaises(ValueError):
                build_allocation_snapshots(7, families)

    def test_validator_rejects_future_acceptance_or_patient(self):
        snapshot = copy.deepcopy(self.snapshots[0])
        snapshot['patients'][0]['accepted_at']['FICT-H01'] = 11
        with self.assertRaises(ValueError):
            validate_allocation_snapshot(snapshot)
        snapshot = copy.deepcopy(self.snapshots[0])
        snapshot['patients'][0]['t_req'] = 11
        with self.assertRaises(ValueError):
            validate_allocation_snapshot(snapshot)

    def test_validator_rejects_false_capability_or_unknown_offer(self):
        snapshot = copy.deepcopy(self.snapshots[0])
        snapshot['patients'][0]['required_capabilities'] = ['not-present']
        with self.assertRaises(ValueError):
            validate_allocation_snapshot(snapshot)
        snapshot = copy.deepcopy(self.snapshots[0])
        snapshot['patients'][0]['accepted_hospitals'].append('unknown')
        with self.assertRaises(ValueError):
            validate_allocation_snapshot(snapshot)

    def test_validator_rejects_noninteger_resource_and_unknown_priority(self):
        snapshot = copy.deepcopy(self.snapshots[0])
        snapshot['hospitals'][0]['capacity']['ed_bed'] = True
        with self.assertRaises(ValueError):
            validate_allocation_snapshot(snapshot)
        snapshot = copy.deepcopy(self.snapshots[0])
        snapshot['patients'][0]['priority'] = 'clinical-diagnosis'
        with self.assertRaises(ValueError):
            validate_allocation_snapshot(snapshot)


if __name__ == '__main__':
    unittest.main()
