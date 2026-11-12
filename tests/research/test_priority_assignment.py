"""R4 changes actual same-minute assignment, not just experiment scoring."""
import copy
from itertools import permutations
from types import SimpleNamespace
import unittest

from simulation.priority_assignment import PriorityJointAssignmentRule
from simulation.sim import Hospital, JointAssignmentRule, Patient


def patient(pid, priority, demand=None, t_req=0):
    return Patient(pid, t_req, (0, 0), set(),
                   dict(demand or {'ed_bed': 1}), priority, 30)


class World:
    """Only the current fields permitted to the assignment rule are exposed."""

    def __init__(self, hospitals, travel=None):
        self.hospitals = {hid: Hospital(hid, (0, 0), set(), dict(cap))
                          for hid, cap in hospitals.items()}
        self.times = travel or {}

    def position(self, p, t):
        return p.loc

    def travel(self, p, hid, loc):
        return self.times.get((p.id, hid), 1)


class PriorityAssignmentTests(unittest.TestCase):
    def test_lexicographic_high_priority_differs_from_weighted_sum_tie(self):
        high = patient('HIGH', 4, {'ed_bed': 2})
        med_a, med_b = patient('MED_A', 2), patient('MED_B', 2)
        sim = World({'H': {'ed_bed': 2}}, {('HIGH', 'H'): 10})
        ready = [(high, ['H']), (med_a, ['H']), (med_b, ['H'])]
        weighted = JointAssignmentRule().decide_batch(sim, ready, 0)
        protected = PriorityJointAssignmentRule().decide_batch(sim, ready, 0)
        # Both alternatives leave priority weight 4 unassigned. R3 then favors
        # the two short medium trips; R4 protects the one tier-4 patient first.
        self.assertEqual(weighted, {'HIGH': None, 'MED_A': 'H', 'MED_B': 'H'})
        self.assertEqual(protected, {'HIGH': 'H', 'MED_A': None, 'MED_B': None})

    def test_joint_choice_preserves_scarce_option_without_rejecting_flexible_patient(self):
        flexible, scarce = patient('A_FLEX', 4), patient('B_SCARCE', 4)
        sim = World({'H': {'ed_bed': 1}, 'G': {'ed_bed': 1}},
                    {('A_FLEX', 'G'): 10})
        result = PriorityJointAssignmentRule().decide_batch(
            sim, [(flexible, ['H', 'G']), (scarce, ['H'])], 0)
        self.assertEqual(result, {'A_FLEX': 'G', 'B_SCARCE': 'H'})

    def test_each_resource_capacity_and_existing_commitment_are_respected(self):
        sim = World({'H': {'ed_bed': 3, 'special_team': 1}})
        sim.hospitals['H'].committed['ALREADY_TRANSIT'] = {'ed_bed': 1}
        a = patient('A', 4, {'ed_bed': 1, 'special_team': 1})
        b = patient('B', 2, {'ed_bed': 1, 'special_team': 1})
        c = patient('C', 1)
        before = copy.deepcopy(sim.hospitals['H'].__dict__)
        result = PriorityJointAssignmentRule().decide_batch(
            sim, [(a, ['H']), (b, ['H']), (c, ['H'])], 0)
        self.assertEqual(result, {'A': 'H', 'B': None, 'C': 'H'})
        self.assertEqual(sim.hospitals['H'].__dict__, before)

    def test_same_tier_uses_travel_before_deterministic_tie_break(self):
        a, b = patient('A', 4), patient('B', 4)
        sim = World({'H': {'ed_bed': 1}}, {('A', 'H'): 10, ('B', 'H'): 2})
        self.assertEqual(PriorityJointAssignmentRule().decide_batch(
            sim, [(a, ['H']), (b, ['H'])], 0), {'A': None, 'B': 'H'})

    def test_tier_travel_objectives_are_lexicographic(self):
        high, low = patient('HIGH', 4), patient('LOW', 1)
        sim = World({'A': {'ed_bed': 1}, 'B': {'ed_bed': 1}},
                    {('HIGH', 'A'): 1, ('HIGH', 'B'): 2,
                     ('LOW', 'A'): 1, ('LOW', 'B'): 100})
        result = PriorityJointAssignmentRule().decide_batch(
            sim, [(low, ['A', 'B']), (high, ['A', 'B'])], 0)
        self.assertEqual(result, {'HIGH': 'A', 'LOW': 'B'})

    def test_fallback_orders_priorities_and_does_not_double_allocate(self):
        high, low = patient('HIGH', 4), patient('LOW', 1)
        sim = World({'H': {'ed_bed': 1}})
        rule = PriorityJointAssignmentRule(limit=1)
        result = rule.decide_batch(sim, [(low, ['H']), (high, ['H'])], 0)
        self.assertEqual(result, {'HIGH': 'H', 'LOW': None})
        self.assertEqual(rule.fallbacks, 1)

    def test_fallback_is_explicitly_not_exact_with_scarce_alternatives(self):
        flexible, scarce = patient('A_FLEX', 4), patient('B_SCARCE', 4)
        sim = World({'H': {'ed_bed': 1}, 'G': {'ed_bed': 1}},
                    {('A_FLEX', 'G'): 10})
        ready = [(flexible, ['H', 'G']), (scarce, ['H'])]
        result = PriorityJointAssignmentRule(limit=1).decide_batch(sim, ready, 0)
        self.assertEqual(result, {'A_FLEX': 'H', 'B_SCARCE': None})

    def test_exact_limit_boundary_does_not_fallback(self):
        p = patient('P', 4)
        rule = PriorityJointAssignmentRule(limit=2)
        self.assertEqual(rule.decide_batch(World({'H': {'ed_bed': 1}}),
                                          [(p, ['H'])], 0), {'P': 'H'})
        self.assertEqual(rule.fallbacks, 0)

    def test_input_patient_and_option_permutations_have_same_result(self):
        a, b = patient('A', 4), patient('B', 2)
        sim = World({'A': {'ed_bed': 1}, 'B': {'ed_bed': 1}})
        original = [(a, ['B', 'A']), (b, ['A', 'B'])]
        expected = {'A': 'A', 'B': 'B'}
        for ordered in permutations(original):
            for reverse_options in (False, True):
                ready = [(p, list(reversed(options)) if reverse_options else options[:])
                         for p, options in ordered]
                before = [(p.id, list(options)) for p, options in ready]
                self.assertEqual(PriorityJointAssignmentRule().decide_batch(sim, ready, 0), expected)
                self.assertEqual([(p.id, options) for p, options in ready], before)

    def test_fallback_equal_tier_orders_request_time_then_id(self):
        early, late, same_time = (patient('Z_EARLY', 4, t_req=1),
                                  patient('A_LATE', 4, t_req=2),
                                  patient('B_LATE', 4, t_req=2))
        sim = World({'H': {'ed_bed': 2}})
        expected = {'Z_EARLY': 'H', 'A_LATE': 'H', 'B_LATE': None}
        for ordered in permutations([(late, ['H']), (same_time, ['H']), (early, ['H'])]):
            self.assertEqual(PriorityJointAssignmentRule(limit=1).decide_batch(
                sim, ordered, 2), expected)

    def test_no_options_and_negative_free_are_safe(self):
        sim = World({'H': {'ed_bed': 0}})
        sim.hospitals['H'].committed['EXISTING'] = {'ed_bed': 1}
        a, b = patient('A', 4), patient('B', 2)
        self.assertEqual(PriorityJointAssignmentRule().decide_batch(
            sim, [(a, ['H']), (b, [])], 0), {'A': None, 'B': None})

    def test_empty_ready_does_not_read_environment_or_fallback(self):
        rule = PriorityJointAssignmentRule(limit=1)
        self.assertEqual(rule.decide_batch(SimpleNamespace(), [], 0), {})
        self.assertEqual(rule.fallbacks, 0)

    def test_single_choice_is_nearest_with_stable_hospital_tie_break(self):
        p = patient('P', 4)
        sim = World({'B': {'ed_bed': 1}, 'A': {'ed_bed': 1}, 'C': {'ed_bed': 1}},
                    {('P', 'C'): 5})
        self.assertEqual(PriorityJointAssignmentRule().choose(sim, p, ['C', 'B', 'A'], 0), 'A')

    def test_invalid_limit_priority_and_duplicate_patients_fail_explicitly(self):
        for limit in (True, 0, -1, 1.5):
            with self.assertRaises(ValueError):
                PriorityJointAssignmentRule(limit=limit)
        sim = World({'H': {'ed_bed': 2}})
        rule = PriorityJointAssignmentRule()
        with self.assertRaises(ValueError):
            rule.decide_batch(sim, [(patient('P', 3), ['H'])], 0)
        p = patient('P', 4)
        with self.assertRaises(ValueError):
            rule.decide_batch(sim, [(p, ['H']), (p, ['H'])], 0)


if __name__ == '__main__':
    unittest.main()
