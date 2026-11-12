"""Checks that stable C1 changes only exact ties and keeps proof scope honest."""
from copy import deepcopy
from itertools import permutations
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from simulation.cp_sat_assignment import (
    CPSATAssignmentRule, CPSATDependencyError, _load_cp_model,
    assignment_objective, validate_assignment,
)
from simulation.stable_cp_sat_assignment import StableCPSATAssignmentRule, TIE_RULE


def patient(pid, priority=4, request_t=0, demand=None):
    return SimpleNamespace(id=pid, priority=priority, t_req=request_t,
                           demand={'ed_bed': 1} if demand is None else dict(demand),
                           loc=(0, 0))


class World:
    """Only current quantities and travel are exposed, with no future state."""
    def __init__(self, capacities, travel=None):
        self.capacities = deepcopy(capacities)
        self.times = dict(travel or {})
        self.hospitals = {
            hid: SimpleNamespace(free=lambda resource, hid=hid:
                                 self.capacities[hid].get(resource, 0))
            for hid in capacities
        }

    def position(self, p, t):
        return p.loc

    def travel(self, p, hid, position):
        return self.times.get((p.id, hid), 3)


def incumbent(plan, *, optimal=False):
    """Supply a solver witness without depending on backend tie preference."""
    def decide(rule, sim, ready, t):
        rows, travel, free = rule._snapshot(sim, ready, t)
        if not validate_assignment(rows, plan, free):
            raise AssertionError('invalid test witness')
        rule.records.append(dict(
            t=t, assignments=dict(plan), objective=assignment_objective(rows, plan, travel),
            assigned_count=sum(hid is not None for hid in plan.values()),
            optimal_proven=optimal, primary_optimal_proven=optimal,
            secondary_optimal_proven=optimal, status='OPTIMAL' if optimal else 'FEASIBLE',
            stages=[dict(objective_name='assigned_priority_weight', status='OPTIMAL',
                         objective=8, best_bound=8, wall_seconds=0)],
            fallback=False, fallback_reason=None, wall_seconds=0,
        ))
        return dict(plan)
    return decide


try:
    _load_cp_model()
    HAS_CP_SAT = True
except CPSATDependencyError:
    HAS_CP_SAT = False


class StableTieTests(unittest.TestCase):
    def test_equal_objective_restores_priority_request_id_baseline(self):
        a, b, c = [patient(pid) for pid in ('P01', 'P02', 'P03')]
        world = World({'H': {'ed_bed': 2}})
        ready = [(a, ['H']), (b, ['H']), (c, ['H'])]
        old = {'P01': None, 'P02': 'H', 'P03': 'H'}
        rule = StableCPSATAssignmentRule()
        with patch.object(CPSATAssignmentRule, 'decide_batch', incumbent(old, optimal=True)):
            actual = rule.decide_batch(world, ready, 1)
        self.assertEqual(actual, {'P01': 'H', 'P02': 'H', 'P03': None})
        rec = rule.records[-1]
        self.assertEqual(rec['objective'], [4, 24])
        self.assertTrue(rec['optimal_proven'])
        self.assertEqual(rec['stability']['solver_assignments'], old)
        self.assertEqual(rec['stability']['selected_assignments'], actual)
        self.assertTrue(rec['stability']['applied'])
        self.assertEqual(rec['stability']['reason'], 'equal_objective_baseline')
        self.assertEqual(rec['stability']['rule'], TIE_RULE)
        self.assertFalse(rec['stability']['optimality_claimed'])

    def test_older_request_precedes_lower_id_at_same_priority(self):
        newer = patient('A', request_t=1)
        older = patient('Z', request_t=0)
        world = World({'H': {'ed_bed': 1}})
        rule = StableCPSATAssignmentRule()
        with patch.object(CPSATAssignmentRule, 'decide_batch',
                          incumbent({'A': 'H', 'Z': None})):
            actual = rule.decide_batch(world, [(newer, ['H']), (older, ['H'])], 2)
        self.assertEqual(actual, {'Z': 'H', 'A': None})

    def test_priority_precedes_old_request_and_lower_id_on_equal_pair(self):
        high = patient('Z', priority=4, request_t=1, demand={'ed_bed': 2})
        low_a = patient('A', priority=2)
        low_b = patient('B', priority=2)
        world = World({'H': {'ed_bed': 2}})
        rule = StableCPSATAssignmentRule()
        with patch.object(CPSATAssignmentRule, 'decide_batch',
                          incumbent({'Z': None, 'A': 'H', 'B': 'H'})):
            actual = rule.decide_batch(world, [(low_a, ['H']), (low_b, ['H']), (high, ['H'])], 2)
        self.assertEqual(actual, {'Z': 'H', 'A': None, 'B': None})
        # The original objective minimizes unassigned weight, not patient count.
        self.assertEqual(rule.records[-1]['objective'], [4, 12])
        self.assertEqual(rule.records[-1]['assigned_count'], 1)

    def test_strict_primary_improvement_is_never_replaced_by_greedy(self):
        a, b = patient('A'), patient('B')
        world = World({'H': {'ed_bed': 1}, 'G': {'ed_bed': 1}}, {('A', 'G'): 20})
        better = {'A': 'G', 'B': 'H'}
        rule = StableCPSATAssignmentRule()
        with patch.object(CPSATAssignmentRule, 'decide_batch', incumbent(better, optimal=True)):
            actual = rule.decide_batch(world, [(a, ['H', 'G']), (b, ['H'])], 0)
        self.assertEqual(actual, better)
        self.assertEqual(rule.records[-1]['stability']['reason'], 'solver_strictly_better')
        self.assertFalse(rule.records[-1]['stability']['applied'])

    def test_strict_secondary_improvement_is_never_replaced(self):
        a, b = patient('A'), patient('B')
        world = World({'H': {'ed_bed': 1}, 'G': {'ed_bed': 1}},
                      {('A', 'H'): 1, ('A', 'G'): 2, ('B', 'H'): 1, ('B', 'G'): 30})
        better = {'A': 'G', 'B': 'H'}
        rule = StableCPSATAssignmentRule()
        with patch.object(CPSATAssignmentRule, 'decide_batch', incumbent(better)):
            self.assertEqual(rule.decide_batch(world, [(a, ['H', 'G']), (b, ['H', 'G'])], 0), better)
        self.assertEqual(rule.records[-1]['objective'], [0, 12])
        self.assertFalse(rule.records[-1]['optimal_proven'])

    def test_hospital_id_breaks_equal_travel_tie(self):
        p = patient('P')
        world = World({'Z': {'ed_bed': 1}, 'A': {'ed_bed': 1}})
        rule = StableCPSATAssignmentRule()
        with patch.object(CPSATAssignmentRule, 'decide_batch', incumbent({'P': 'Z'})):
            self.assertEqual(rule.decide_batch(world, [(p, ['Z', 'A'])], 0), {'P': 'A'})

    def test_exact_tie_is_invariant_to_patient_and_offer_permutations(self):
        a, b = patient('A'), patient('B')
        world = World({'H': {'ed_bed': 1}, 'G': {'ed_bed': 1}})
        for ordered in permutations([a, b]):
            for offered in (['G', 'H'], ['H', 'G', 'G']):
                rule = StableCPSATAssignmentRule()
                with patch.object(CPSATAssignmentRule, 'decide_batch',
                                  incumbent({'A': 'H', 'B': 'G'})):
                    actual = rule.decide_batch(world, [(p, list(offered)) for p in ordered], 0)
                self.assertEqual(actual, {'A': 'G', 'B': 'H'})

    def test_feasible_equal_tie_does_not_gain_false_proof(self):
        a, b = patient('A'), patient('B')
        rule = StableCPSATAssignmentRule()
        with patch.object(CPSATAssignmentRule, 'decide_batch', incumbent({'A': None, 'B': 'H'})):
            rule.decide_batch(World({'H': {'ed_bed': 1}}), [(a, ['H']), (b, ['H'])], 0)
        rec = rule.records[-1]
        self.assertEqual(rec['status'], 'FEASIBLE')
        self.assertFalse(rec['optimal_proven'])
        self.assertFalse(rec['primary_optimal_proven'])
        self.assertFalse(rec['secondary_optimal_proven'])
        self.assertEqual(rec['stages'][0]['best_bound'], 8)

    def test_stable_choice_respects_multiple_resources_and_preserves_inputs(self):
        a = patient('A', demand={'ed_bed': 1, 'team': 1})
        b = patient('B', demand={'ed_bed': 1, 'team': 1})
        c = patient('C')
        world = World({'H': {'ed_bed': 2, 'team': 1}})
        ready = [(a, ['H']), (b, ['H']), (c, ['H'])]
        before = deepcopy([(p.__dict__, options) for p, options in ready])
        capacities = deepcopy(world.capacities)
        rule = StableCPSATAssignmentRule()
        with patch.object(CPSATAssignmentRule, 'decide_batch',
                          incumbent({'A': None, 'B': 'H', 'C': 'H'})):
            actual = rule.decide_batch(world, ready, 0)
        self.assertEqual(actual, {'A': 'H', 'B': None, 'C': 'H'})
        self.assertEqual([(p.__dict__, options) for p, options in ready], before)
        self.assertEqual(world.capacities, capacities)

    def test_zero_budget_keeps_validated_fallback_and_no_proof(self):
        rule = StableCPSATAssignmentRule(time_limit_seconds=0)
        a, b = patient('A'), patient('B')
        with patch('simulation.cp_sat_assignment._load_cp_model', side_effect=AssertionError('unused')):
            actual = rule.decide_batch(World({'H': {'ed_bed': 1}}), [(a, ['H']), (b, ['H'])], 0)
        self.assertEqual(actual, {'A': 'H', 'B': None})
        rec = rule.records[-1]
        self.assertEqual(rec['fallback_reason'], 'time_limit_zero')
        self.assertFalse(rec['optimal_proven'])
        self.assertEqual(rec['stability']['reason'], 'equal_objective_unchanged')
        self.assertGreaterEqual(rec['wall_seconds'], rec['solver_pipeline_wall_seconds'])
        self.assertGreaterEqual(rec['wall_seconds'], rec['stability']['elapsed_seconds'])

    def test_missing_backend_fallback_does_not_gain_proof(self):
        rule = StableCPSATAssignmentRule()
        with patch('simulation.cp_sat_assignment._load_cp_model',
                   side_effect=CPSATDependencyError('missing test backend')):
            self.assertEqual(rule.decide_batch(World({'H': {'ed_bed': 1}}),
                             [(patient('A'), ['H'])], 0), {'A': 'H'})
        self.assertEqual(rule.records[-1]['fallback_reason'], 'missing_backend')
        self.assertFalse(rule.records[-1]['optimal_proven'])

    def test_required_missing_backend_propagates_without_record(self):
        rule = StableCPSATAssignmentRule(require_backend=True)
        with patch('simulation.cp_sat_assignment._load_cp_model',
                   side_effect=CPSATDependencyError('missing test backend')):
            with self.assertRaises(CPSATDependencyError):
                rule.decide_batch(World({'H': {'ed_bed': 1}}), [(patient('A'), ['H'])], 0)
        self.assertEqual(rule.records, [])

    def test_empty_batch_reads_no_world_and_creates_no_record(self):
        rule = StableCPSATAssignmentRule()
        self.assertEqual(rule.decide_batch(SimpleNamespace(), [], 0), {})
        self.assertEqual(rule.records, [])

    def test_unexpected_worse_solver_plan_is_rejected(self):
        a, b = patient('A'), patient('B')
        world = World({'H': {'ed_bed': 2}})
        rule = StableCPSATAssignmentRule()
        with patch.object(CPSATAssignmentRule, 'decide_batch', incumbent({'A': 'H', 'B': None})):
            with self.assertRaisesRegex(AssertionError, 'worse than'):
                rule.decide_batch(world, [(a, ['H']), (b, ['H'])], 0)


@unittest.skipUnless(HAS_CP_SAT, 'optional OR-Tools backend is not installed')
class StableBackendTests(unittest.TestCase):
    def test_real_solver_tie_stabilizes_without_changing_objective_or_proof(self):
        world = World({'H': {'ed_bed': 2}})
        ready = [(patient(pid), ['H']) for pid in ('P03', 'P02', 'P01')]
        rule = StableCPSATAssignmentRule(time_limit_seconds=.05, require_backend=True)
        actual = rule.decide_batch(world, ready, 0)
        self.assertEqual(actual, {'P01': 'H', 'P02': 'H', 'P03': None})
        self.assertEqual(rule.records[-1]['objective'], [4, 24])
        self.assertTrue(rule.records[-1]['optimal_proven'])
        self.assertEqual(rule.records[-1]['time_limit_seconds'], .05)
        self.assertEqual([s['objective_name'] for s in rule.records[-1]['stages']],
                         ['assigned_priority_weight', 'priority_weighted_travel_minutes'])

    def test_tiny_budget_remains_feasible_and_does_not_claim_tie_optimality(self):
        world = World({'H': {'ed_bed': 2}, 'G': {'ed_bed': 2}})
        ready = [(patient(f'P{i}'), ['H', 'G']) for i in range(6)]
        rule = StableCPSATAssignmentRule(time_limit_seconds=1e-12, require_backend=True)
        actual = rule.decide_batch(world, ready, 0)
        rows, travel, free = rule._snapshot(world, ready, 0)
        self.assertTrue(validate_assignment(rows, actual, free))
        self.assertEqual(rule.records[-1]['objective'], assignment_objective(rows, actual, travel))
        self.assertFalse(rule.records[-1]['optimal_proven'])
        self.assertFalse(rule.records[-1]['stability']['optimality_claimed'])
        self.assertEqual(rule.records[-1]['time_limit_seconds'], 1e-12)


if __name__ == '__main__':
    unittest.main()
