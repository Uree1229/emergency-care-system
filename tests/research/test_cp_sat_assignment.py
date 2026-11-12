"""Exact small-model checks and deterministic safe-fallback coverage for C1."""
import copy
from itertools import product
import random
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from simulation.cp_sat_assignment import (CPSATAssignmentRule, CPSATDependencyError,
                                           _load_cp_model, validate_assignment)
from simulation.sim import Hospital, JointAssignmentRule, Patient


def patient(pid, priority=1, demand=None):
    return Patient(pid, 0, (0, 0), set(),
                   dict({'ed_bed': 1} if demand is None else demand), priority, 30)


class World:
    """No future patients, release schedule, response queue or world seed."""
    def __init__(self, hospitals, travel=None):
        self.hospitals = {hid: Hospital(hid, (0, 0), set(), dict(cap))
                          for hid, cap in hospitals.items()}
        self.times = travel or {}

    def position(self, p, t):
        return p.loc

    def travel(self, p, hid, loc):
        return self.times.get((p.id, hid), 1)


def objective(ready, plan, world):
    return (sum(p.priority for p, _ in ready if plan[p.id] is None),
            sum(p.priority * world.travel(p, plan[p.id], p.loc)
                for p, _ in ready if plan[p.id] is not None))


def enumerate_objective(world, ready):
    """Separate enumeration, independent of both CP-SAT and R3 helpers."""
    best = None
    for combination in product(*(options + [None] for _, options in ready)):
        used = {}
        for (p, _), hid in zip(ready, combination):
            if hid is not None:
                for resource, quantity in p.demand.items():
                    if quantity:
                        used[(hid, resource)] = used.get((hid, resource), 0) + quantity
        if any(quantity > world.hospitals[hid].free(resource)
               for (hid, resource), quantity in used.items()):
            continue
        value = objective(ready, {p.id: hid for (p, _), hid in zip(ready, combination)}, world)
        if best is None or value < best:
            best = value
    return best


try:
    _load_cp_model()
    HAS_CP_SAT = True
except CPSATDependencyError:
    HAS_CP_SAT = False


@unittest.skipUnless(HAS_CP_SAT, 'optional OR-Tools backend is not installed')
class CPSATExactTests(unittest.TestCase):
    def rule(self):
        return CPSATAssignmentRule(time_limit_seconds=5, require_backend=True)

    def test_matches_separate_exhaustive_enumeration_and_r3(self):
        rng = random.Random(6127)
        for case in range(30):
            world = World({hid: {'ed_bed': rng.randrange(3), 'special_team': rng.randrange(2)}
                           for hid in ('A', 'B', 'C')})
            ready = []
            for index in range(rng.randrange(1, 5)):
                p = patient(f'P{index}', rng.choice([1, 2, 4]),
                            {'ed_bed': rng.randrange(1, 3), 'special_team': rng.randrange(2)})
                options = [hid for hid in world.hospitals if rng.random() < 0.8]
                ready.append((p, options))
                for hid in options:
                    world.times[(p.id, hid)] = rng.randrange(0, 25)
            rule = self.rule()
            actual = rule.decide_batch(world, ready, case)
            expected = enumerate_objective(world, ready)
            r3 = JointAssignmentRule().decide_batch(world, ready, case)
            with self.subTest(case=case):
                self.assertEqual(objective(ready, actual, world), expected)
                self.assertEqual(objective(ready, actual, world), objective(ready, r3, world))
                self.assertEqual(rule.records[-1]['objective'], list(expected))
                self.assertTrue(rule.records[-1]['optimal_proven'])

    def test_weighted_priority_objective_is_not_maximum_patient_count(self):
        high = patient('HIGH', 4, {'ed_bed': 2})
        a, b = patient('LOW_A'), patient('LOW_B')
        ready = [(a, ['H']), (high, ['H']), (b, ['H'])]
        world = World({'H': {'ed_bed': 2}}, {('HIGH', 'H'): 20})
        rule = self.rule()
        plan = rule.decide_batch(world, ready, 0)
        self.assertEqual(plan, {'HIGH': 'H', 'LOW_A': None, 'LOW_B': None})
        self.assertEqual(rule.records[-1]['assigned_count'], 1)
        self.assertEqual(rule.records[-1]['objective'], [2, 80])

    def test_same_weight_uses_weighted_travel_to_choose_two_medium_patients(self):
        high = patient('HIGH', 4, {'ed_bed': 2})
        a, b = patient('MED_A', 2), patient('MED_B', 2)
        world = World({'H': {'ed_bed': 2}}, {('HIGH', 'H'): 20})
        plan = self.rule().decide_batch(world, [(high, ['H']), (a, ['H']), (b, ['H'])], 0)
        self.assertEqual(plan, {'HIGH': None, 'MED_A': 'H', 'MED_B': 'H'})

    def test_joint_assignment_preserves_only_option_of_scarce_patient(self):
        a, b = patient('A', 4), patient('B', 4)
        world = World({'H': {'ed_bed': 1}, 'G': {'ed_bed': 1}}, {('A', 'G'): 10})
        self.assertEqual(self.rule().decide_batch(world, [(a, ['H', 'G']), (b, ['H'])], 0),
                         {'A': 'G', 'B': 'H'})

    def test_current_committed_active_and_queue_demands_are_respected_without_mutation(self):
        world = World({'H': {'ed_bed': 4, 'special_team': 2}})
        h = world.hospitals['H']
        h.active.append([100, {'special_team': 1}])
        h.committed['LOCKED'] = {'ed_bed': 1}
        h.queue.append(patient('QUEUED'))
        a = patient('A', 4, {'ed_bed': 1, 'special_team': 1})
        b = patient('B', 2, {'ed_bed': 1, 'special_team': 1})
        c = patient('C', 1)
        ready = [(a, ['H']), (b, ['H']), (c, ['H'])]
        before = copy.deepcopy(h.__dict__)
        plan = self.rule().decide_batch(world, ready, 0)
        self.assertEqual(plan, {'A': 'H', 'B': None, 'C': 'H'})
        self.assertEqual(h.__dict__, before)

    def test_negative_free_missing_resource_and_empty_options_are_safe(self):
        world = World({'H': {'ed_bed': 0}, 'G': {'ed_bed': 2}})
        world.hospitals['H'].committed['LOCKED'] = {'ed_bed': 1}
        a = patient('A', 4)
        b = patient('B', 2, {'special_team': 1})
        c = patient('C')
        rule = self.rule()
        result = rule.decide_batch(world, [(a, ['H']), (b, ['G']), (c, [])], 0)
        self.assertEqual(result, {'A': None, 'B': None, 'C': None})
        self.assertEqual(rule.records[-1]['status'], 'OPTIMAL_EMPTY')

    def test_tie_is_deterministic_under_input_permutation(self):
        a, b = patient('A'), patient('B')
        world = World({'H': {'ed_bed': 1}, 'G': {'ed_bed': 1}})
        first = self.rule().decide_batch(world, [(a, ['H', 'G']), (b, ['G', 'H'])], 0)
        for _ in range(3):
            self.assertEqual(self.rule().decide_batch(world,
                             [(b, ['H', 'G', 'G']), (a, ['G', 'H'])], 0), first)

    def test_stage_metadata_identifies_current_batch_scope_and_bounds(self):
        world = World({'H': {'ed_bed': 1}})
        rule = self.rule()
        rule.decide_batch(world, [(patient('P', 4), ['H'])], 17)
        record = rule.records[-1]
        self.assertEqual(record['scope'], 'current_same_minute_accepted_offers')
        self.assertEqual(record['t'], 17)
        self.assertEqual(record['objective_names'], ['unassigned_priority_weight',
                                                    'priority_weighted_travel_minutes'])
        self.assertEqual(record['num_search_workers'], 1)
        self.assertEqual([stage['objective_name'] for stage in record['stages']],
                         ['assigned_priority_weight', 'priority_weighted_travel_minutes'])
        for stage in record['stages']:
            self.assertEqual(stage['status'], 'OPTIMAL')
            self.assertEqual(stage['best_bound'], stage['objective'])
        self.assertFalse(record['fallback'])
        self.assertGreaterEqual(record['wall_seconds'], 0)


class CPSATFallbackTests(unittest.TestCase):
    def setUp(self):
        self.world = World({'H': {'ed_bed': 1}, 'G': {'ed_bed': 1}}, {('A', 'G'): 20})
        self.a, self.b = patient('A', 4), patient('B', 1)
        self.ready = [(self.a, ['H', 'G']), (self.b, ['H'])]

    def test_zero_time_limit_uses_validated_priority_greedy(self):
        rule = CPSATAssignmentRule(time_limit_seconds=0)
        with patch('simulation.cp_sat_assignment._load_cp_model', side_effect=AssertionError('not needed')):
            plan = rule.decide_batch(self.world, self.ready, 0)
        self.assertEqual(plan, {'A': 'H', 'B': None})
        self.assertEqual(rule.fallbacks, 1)
        self.assertEqual(rule.records[-1]['fallback_reason'], 'time_limit_zero')
        self.assertFalse(rule.records[-1]['optimal_proven'])

    def test_missing_backend_is_visible_and_safe(self):
        rule = CPSATAssignmentRule()
        with patch('simulation.cp_sat_assignment._load_cp_model',
                   side_effect=CPSATDependencyError('Install OR-Tools')):
            result = rule.decide_batch(self.world, self.ready, 0)
        self.assertEqual(result, {'A': 'H', 'B': None})
        self.assertEqual(rule.records[-1]['status'], 'MISSING_DEPENDENCY')
        self.assertIn('OR-Tools', rule.records[-1]['dependency_error'])
        self.assertEqual(rule.fallbacks, 1)

    def test_missing_backend_can_be_required_explicitly(self):
        rule = CPSATAssignmentRule(require_backend=True)
        with patch('simulation.cp_sat_assignment._load_cp_model',
                   side_effect=CPSATDependencyError('Install OR-Tools')):
            with self.assertRaisesRegex(CPSATDependencyError, 'OR-Tools'):
                rule.decide_batch(self.world, self.ready, 0)
        self.assertEqual(rule.records, [])

    def test_empty_batch_does_not_read_environment(self):
        rule = CPSATAssignmentRule()
        self.assertEqual(rule.decide_batch(SimpleNamespace(), [], 0), {})
        self.assertEqual(rule.records, [])

    def test_single_patient_choice_matches_r3_nearest(self):
        rule = CPSATAssignmentRule()
        self.assertEqual(rule.choose(self.world, self.a, ['G', 'H'], 0), 'H')
        self.assertIsNone(rule.choose(self.world, self.a, [], 0))

    def test_independent_validator_rejects_duplicate_capacity_and_nonoffered_edge(self):
        rows = [{'id': 'A', 'options': ['H'], 'demand': {'ed_bed': 1}},
                {'id': 'B', 'options': ['H'], 'demand': {'ed_bed': 1}}]
        free = {('H', 'ed_bed'): 1}
        self.assertFalse(validate_assignment(rows, {'A': 'H', 'B': 'H'}, free))
        self.assertFalse(validate_assignment(rows, {'A': 'G', 'B': None}, free))
        self.assertFalse(validate_assignment(rows, {'A': 'H'}, free))
        self.assertTrue(validate_assignment(rows, {'A': 'H', 'B': None}, free))
        self.assertTrue(validate_assignment(rows, {'A': None, 'B': None}, {('H', 'ed_bed'): -1}))

    def test_invalid_numeric_inputs_are_rejected_before_backend_load(self):
        for value in (True, -1, float('inf'), float('nan'), '1'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                CPSATAssignmentRule(time_limit_seconds=value)
        for value in (True, -1, 0.1, 1 << 31):
            with self.assertRaises(ValueError):
                CPSATAssignmentRule(seed=value)
        for value in (float('nan'), float('inf'), -1, 0.1, True):
            self.world.times[('A', 'H')] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                CPSATAssignmentRule().decide_batch(self.world, self.ready, 0)
        self.world.times[('A', 'H')] = 1
        self.a.demand['ed_bed'] = -1
        with self.assertRaises(ValueError):
            CPSATAssignmentRule().decide_batch(self.world, self.ready, 0)

    def test_duplicate_ids_and_unknown_hospital_are_rejected(self):
        with self.assertRaises(ValueError):
            CPSATAssignmentRule().decide_batch(self.world, [(self.a, ['H']), (self.a, ['G'])], 0)
        with self.assertRaises(ValueError):
            CPSATAssignmentRule().decide_batch(self.world, [(self.a, ['UNKNOWN'])], 0)

    def test_unsafe_integer_expression_domains_are_rejected(self):
        self.a.priority = 1 << 58
        self.world.times[('A', 'H')] = 10
        with self.assertRaisesRegex(ValueError, 'weighted travel'):
            CPSATAssignmentRule().decide_batch(self.world, self.ready, 0)
        self.a.priority = 1 << 10000
        with self.assertRaises(ValueError):
            CPSATAssignmentRule().decide_batch(self.world, self.ready, 0)

    @unittest.skipUnless(HAS_CP_SAT, 'optional OR-Tools backend is not installed')
    def test_solver_incumbent_is_independently_validated_before_return(self):
        cp = _load_cp_model()
        rule = CPSATAssignmentRule(require_backend=True)
        fake = SimpleNamespace(Value=lambda variable: 1)
        with patch.object(rule, '_solve_stage', return_value=(fake, cp.FEASIBLE,
                          dict(status='FEASIBLE', objective=5, best_bound=5, wall_seconds=0))):
            result = rule.decide_batch(self.world, self.ready, 0)
        self.assertEqual(result, {'A': 'H', 'B': None})
        self.assertEqual(rule.records[-1]['fallback_reason'], 'invalid_solver_incumbent')
        self.assertFalse(rule.records[-1]['optimal_proven'])

    @unittest.skipUnless(HAS_CP_SAT, 'optional OR-Tools backend is not installed')
    def test_no_solver_incumbent_uses_safe_fallback_without_timing_assumption(self):
        cp = _load_cp_model()
        rule = CPSATAssignmentRule(require_backend=True)
        with patch.object(rule, '_solve_stage', return_value=(None, cp.UNKNOWN,
                          dict(status='UNKNOWN', objective=None, best_bound=5, wall_seconds=0))):
            result = rule.decide_batch(self.world, self.ready, 0)
        self.assertEqual(result, {'A': 'H', 'B': None})
        self.assertEqual(rule.records[-1]['fallback_reason'], 'no_feasible_solver_incumbent')
        self.assertFalse(rule.records[-1]['optimal_proven'])

    @unittest.skipUnless(HAS_CP_SAT, 'optional OR-Tools backend is not installed')
    def test_no_stage_two_incumbent_keeps_valid_primary_solution(self):
        cp = _load_cp_model()
        rule = CPSATAssignmentRule(require_backend=True)
        original = rule._solve_stage
        calls = []

        def staged(backend, model, remaining):
            calls.append(remaining)
            if len(calls) == 1:
                return original(backend, model, remaining)
            return None, cp.UNKNOWN, dict(status='UNKNOWN', objective=None, best_bound=0, wall_seconds=0)

        with patch.object(rule, '_solve_stage', side_effect=staged):
            result = rule.decide_batch(self.world, self.ready, 0)
        self.assertEqual(sum(hid is not None for hid in result.values()), 2)
        self.assertEqual(rule.records[-1]['status'], 'FEASIBLE_PRIMARY_OPTIMAL')
        self.assertFalse(rule.records[-1]['optimal_proven'])
        self.assertTrue(rule.records[-1]['primary_optimal_proven'])
        self.assertFalse(rule.records[-1]['secondary_optimal_proven'])
        self.assertLessEqual(calls[1], calls[0])

    @unittest.skipUnless(HAS_CP_SAT, 'optional OR-Tools backend is not installed')
    def test_shared_deadline_does_not_start_stage_two_after_budget_expires(self):
        rule = CPSATAssignmentRule(time_limit_seconds=1, require_backend=True)
        times = iter([0.0, 0.0, 0.0, 1.1, 1.1])
        with patch('simulation.cp_sat_assignment.time',
                   SimpleNamespace(perf_counter=lambda: next(times))):
            result = rule.decide_batch(self.world, self.ready, 0)
        self.assertEqual(sum(hid is not None for hid in result.values()), 2)
        record = rule.records[-1]
        self.assertEqual(len(record['stages']), 1)
        self.assertTrue(record['primary_optimal_proven'])
        self.assertFalse(record['optimal_proven'])
        self.assertEqual(record['status'], 'FEASIBLE_PRIMARY_OPTIMAL')


if __name__ == '__main__':
    unittest.main()
