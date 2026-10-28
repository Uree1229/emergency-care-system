"""Current-offer constraints, deterministic repair and safe baseline checks."""
import copy
from itertools import product
import random
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from simulation.cp_sat_assignment import assignment_objective, validate_assignment
from simulation.heuristic_assignment import HeuristicAssignmentRule
from simulation.sim import Hospital, Patient


def patient(pid, priority=1, demand=None):
    return Patient(pid, 0, (0, 0), set(),
                   {'ed_bed': 1} if demand is None else dict(demand), priority, 30)


class World:
    """Only current hospitals/locations/travel; no future events or responses."""
    def __init__(self, hospitals, travel=None):
        self.hospitals = {hid: Hospital(hid, (0, 0), set(), dict(cap))
                          for hid, cap in hospitals.items()}
        self.times = dict(travel or {})

    def position(self, p, t):
        return p.loc

    def travel(self, p, hid, loc):
        return self.times.get((p.id, hid), 1)


def independent_objective(world, ready, plan):
    return [sum(p.priority for p, _ in ready if plan[p.id] is None),
            sum(p.priority * world.travel(p, plan[p.id], p.loc)
                for p, _ in ready if plan[p.id] is not None)]


def independent_feasible(world, ready, plan):
    if set(plan) != {p.id for p, _ in ready}:
        return False
    used = {}
    for p, options in ready:
        hid = plan[p.id]
        if hid is None:
            continue
        if hid not in options:
            return False
        for resource, quantity in p.demand.items():
            if quantity:
                used[hid, resource] = used.get((hid, resource), 0) + quantity
    return all(quantity <= world.hospitals[hid].free(resource)
               for (hid, resource), quantity in used.items())


class HeuristicAssignmentTests(unittest.TestCase):
    def rule(self, **kwargs):
        return HeuristicAssignmentRule(time_limit_seconds=5, **kwargs)

    def test_empty_batch_reads_no_environment(self):
        rule = self.rule()
        self.assertEqual(rule.decide_batch(SimpleNamespace(), [], 0), {})
        self.assertEqual(rule.records, [])

    def test_single_patient_chooses_nearest_current_offer(self):
        world = World({'A': {'ed_bed': 2}, 'B': {'ed_bed': 2}}, {('P', 'A'): 30, ('P', 'B'): 10})
        p = patient('P')
        self.assertEqual(self.rule().choose(world, p, ['A', 'B'], 0), 'B')
        self.assertIsNone(self.rule().choose(world, p, [], 0))
        self.assertEqual(self.rule().decide_batch(world, [(p, ['A', 'B'])], 0), {'P': 'B'})

    def test_spare_resources_preserve_nearest_baseline(self):
        world = World({'A': {'ed_bed': 4}, 'B': {'ed_bed': 4}}, {('P1', 'A'): 7, ('P2', 'B'): 9})
        ready = [(patient('P1', 4), ['A', 'B']), (patient('P2', 2), ['A', 'B'])]
        rule = self.rule()
        result = rule.decide_batch(world, ready, 0)
        self.assertEqual(result, {'P1': 'B', 'P2': 'A'})
        self.assertEqual(rule.records[-1]['objective'], [0, 6])

    def test_scarce_only_offer_is_preserved(self):
        world = World({'A': {'ed_bed': 1}, 'B': {'ed_bed': 1}}, {('P1', 'B'): 10})
        ready = [(patient('P1', 4), ['A', 'B']), (patient('P2', 4), ['A'])]
        rule = self.rule()
        self.assertEqual(rule.decide_batch(world, ready, 0), {'P1': 'B', 'P2': 'A'})
        self.assertEqual(rule.records[-1]['baseline_objective'][0], 4)
        self.assertEqual(rule.records[-1]['objective'], [0, 44])
        self.assertIn('scarcity_nearest', rule.records[-1]['accepted_move_types'])

    def test_does_not_allocate_unoffered_but_attractive_hospital(self):
        world = World({'A': {'ed_bed': 1}, 'B': {'ed_bed': 100}}, {('P', 'A'): 100, ('P', 'B'): 0})
        self.assertEqual(self.rule().decide_batch(world, [(patient('P'), ['A'])], 0), {'P': 'A'})

    def test_multiple_resources_and_zero_demands(self):
        world = World({'A': {'ed_bed': 2, 'team': 1}, 'B': {'ed_bed': 2, 'team': 0}})
        ready = [(patient('P1', 4, {'ed_bed': 1, 'team': 1}), ['A', 'B']),
                 (patient('P2', 2, {'ed_bed': 1, 'team': 0, 'missing': 0}), ['A', 'B']),
                 (patient('P3', 1, {'ed_bed': 2, 'team': 1}), ['A', 'B'])]
        rule = self.rule()
        result = rule.decide_batch(world, ready, 0)
        self.assertEqual(result['P1'], 'A')
        self.assertIsNone(result['P3'])
        self.assertTrue(independent_feasible(world, ready, result))

    def test_negative_free_missing_resources_and_no_options(self):
        world = World({'A': {'ed_bed': 0}, 'B': {'ed_bed': 2}})
        world.hospitals['A'].committed['LOCKED'] = {'ed_bed': 1}
        ready = [(patient('P1', 4), ['A']), (patient('P2', 2, {'team': 1}), ['B']),
                 (patient('P3'), [])]
        rule = self.rule()
        result = rule.decide_batch(world, ready, 0)
        self.assertEqual(result, {'P1': None, 'P2': None, 'P3': None})
        self.assertEqual(rule.records[-1]['search_stop_reason'], 'no_eligible_offers')
        self.assertFalse(rule.records[-1]['optimal_proven'])

    def test_empty_demand_can_use_offer_without_consuming_negative_resource(self):
        world = World({'A': {'ed_bed': 0}})
        world.hospitals['A'].committed['LOCKED'] = {'ed_bed': 1}
        self.assertEqual(self.rule().decide_batch(world, [(patient('P', demand={}), ['A'])], 0), {'P': 'A'})

    def test_weighted_priority_is_not_patient_count(self):
        world = World({'A': {'ed_bed': 2}}, {('HIGH', 'A'): 20})
        ready = [(patient('LOW1'), ['A']), (patient('HIGH', 4, {'ed_bed': 2}), ['A']),
                 (patient('LOW2'), ['A'])]
        rule = self.rule()
        self.assertEqual(rule.decide_batch(world, ready, 0), {'HIGH': 'A', 'LOW1': None, 'LOW2': None})
        self.assertEqual(rule.records[-1]['assigned_count'], 1)
        self.assertEqual(rule.records[-1]['objective'], [2, 80])

    def test_equal_priority_weight_can_choose_two_medium_for_travel(self):
        world = World({'A': {'ed_bed': 2}}, {('HIGH', 'A'): 20})
        ready = [(patient('HIGH', 4, {'ed_bed': 2}), ['A']),
                 (patient('M1', 2), ['A']), (patient('M2', 2), ['A'])]
        rule = self.rule()
        result = rule.decide_batch(world, ready, 0)
        self.assertEqual(result, {'HIGH': None, 'M1': 'A', 'M2': 'A'})
        self.assertEqual(rule.records[-1]['objective'], [4, 4])

    def test_combined_weight_can_outweigh_one_higher_weight_patient(self):
        world = World({'A': {'ed_bed': 2}})
        ready = [(patient('HIGH', 4, {'ed_bed': 2}), ['A']),
                 (patient('M1', 3), ['A']), (patient('M2', 3), ['A'])]
        rule = self.rule()
        result = rule.decide_batch(world, ready, 0)
        self.assertEqual(result, {'HIGH': None, 'M1': 'A', 'M2': 'A'})
        self.assertEqual(rule.records[-1]['objective'], [4, 6])

    def test_swap_repair_improves_when_all_constructions_tie(self):
        world = World({'A': {'ed_bed': 1}, 'B': {'ed_bed': 1}},
                      {('P1', 'A'): 1, ('P1', 'B'): 2, ('P2', 'A'): 1, ('P2', 'B'): 100})
        ready = [(patient('P1'), ['A', 'B']), (patient('P2'), ['A', 'B'])]
        rule = self.rule()
        self.assertEqual(rule.decide_batch(world, ready, 0), {'P1': 'B', 'P2': 'A'})
        record = rule.records[-1]
        self.assertTrue(all(r['objective'] == [0, 101] for r in record['strategy_results']))
        self.assertEqual(record['objective'], [0, 3])
        self.assertIn('swap', record['accepted_move_types'])

    def test_augment_repair_admits_patient_without_changing_prior_commitment(self):
        world = World({'A': {'ed_bed': 1}, 'B': {'ed_bed': 1}})
        # Directly exercise the bounded repair generator independently of the
        # scarcity construction that already solves this small fixture.
        rule = self.rule()
        ready = [(patient('P1', 4), ['A', 'B']), (patient('P2', 4), ['A'])]
        rows, travel, free = rule._snapshot(world, ready, 0)
        base = {'P1': 'A', 'P2': None}
        candidates = [candidate for kind, candidate in rule._repairs(rows, base)
                      if kind == 'augment' and validate_assignment(rows, candidate, free)]
        self.assertIn({'P1': 'B', 'P2': 'A'}, candidates)
        self.assertLess(assignment_objective(rows, candidates[0], travel), assignment_objective(rows, base, travel))

    def test_large_batch_augment_is_not_starved_by_relocation_candidates(self):
        world = World({hid: {'ed_bed': 98 if hid in ('C', 'D', 'E', 'F') else 1}
                       for hid in ('A', 'B', 'C', 'D', 'E', 'F')})
        ready = [(patient(f'P{index:03d}'), ['C', 'D', 'E', 'F']) for index in range(98)]
        ready += [(patient('P098'), ['A', 'B']), (patient('P099'), ['A'])]
        for p, options in ready:
            for hid in options:
                world.times[p.id, hid] = 1 if hid in ('A', 'C') else 100
        rule = self.rule(max_moves=256)
        rows, travel, free = rule._snapshot(world, ready, 0)
        baseline = rule._greedy(rows, travel, free)
        self.assertIsNone(baseline['P099'])
        # Hold construction at the baseline to isolate the repair stage. The
        # 98 flexible patients would otherwise produce 294 travel relocations
        # before the useful augment, exhausting the candidate budget.
        with patch.object(rule, '_construct', return_value=baseline):
            result = rule.decide_batch(world, ready, 0)
        self.assertEqual(result['P098'], 'B')
        self.assertEqual(result['P099'], 'A')
        self.assertEqual(sum(v is not None for v in result.values()), 100)
        self.assertIn('augment', rule.records[-1]['accepted_move_types'])
        self.assertLessEqual(rule.records[-1]['evaluated_moves'], 256)

    def test_current_committed_active_queue_and_patients_remain_unchanged(self):
        world = World({'A': {'ed_bed': 4, 'team': 2}})
        h = world.hospitals['A']
        h.active.append([100, {'team': 1}])
        h.committed['LOCKED'] = {'ed_bed': 1}
        h.queue.append(patient('QUEUED'))
        ready = [(patient('P1', 4, {'ed_bed': 1, 'team': 1}), ['A']),
                 (patient('P2', 2, {'ed_bed': 1, 'team': 1}), ['A']), (patient('P3'), ['A'])]
        before = copy.deepcopy((world.__dict__, [p.__dict__ for p, _ in ready], ready))
        self.assertEqual(self.rule().decide_batch(world, ready, 0), {'P1': 'A', 'P2': None, 'P3': 'A'})
        # Hospital dataclasses are compared through their fields.
        self.assertEqual(h.__dict__, before[0]['hospitals']['A'].__dict__)
        self.assertEqual([p.__dict__ for p, _ in ready], before[1])
        self.assertEqual(ready, before[2])

    def test_permutations_and_duplicate_offer_entries_are_deterministic(self):
        world = World({'A': {'ed_bed': 1}, 'B': {'ed_bed': 1}})
        p1, p2 = patient('P1'), patient('P2')
        expected = self.rule().decide_batch(world, [(p1, ['A', 'B']), (p2, ['A', 'B'])], 0)
        for _ in range(4):
            actual = self.rule().decide_batch(world, [(p2, ['B', 'A', 'B']), (p1, ['B', 'A'])], 0)
            self.assertEqual(actual, expected)

    def test_request_time_remains_baseline_tie_breaker(self):
        world = World({'A': {'ed_bed': 1}})
        a, b = patient('A'), patient('B')
        a.t_req, b.t_req = 1, 0
        rule = HeuristicAssignmentRule(time_limit_seconds=0)
        self.assertEqual(rule.decide_batch(world, [(a, ['A']), (b, ['A'])], 2), {'B': 'A', 'A': None})

    def test_zero_time_returns_exact_baseline_without_loading_backend(self):
        world = World({'A': {'ed_bed': 1}, 'B': {'ed_bed': 1}}, {('P1', 'B'): 10})
        ready = [(patient('P1', 4), ['A', 'B']), (patient('P2', 4), ['A'])]
        rule = HeuristicAssignmentRule(time_limit_seconds=0)
        with patch('simulation.cp_sat_assignment._load_cp_model', side_effect=AssertionError('not needed')):
            self.assertEqual(rule.decide_batch(world, ready, 0), {'P1': 'A', 'P2': None})
        self.assertEqual(rule.records[-1]['search_stop_reason'], 'time_limit_zero')
        self.assertEqual(rule.records[-1]['evaluated_moves'], 0)
        self.assertFalse(rule.records[-1]['optimal_proven'])

    def test_zero_move_limit_returns_baseline(self):
        world = World({'A': {'ed_bed': 1}, 'B': {'ed_bed': 1}})
        rule = self.rule(max_moves=0)
        result = rule.decide_batch(world, [(patient('P1'), ['A', 'B']), (patient('P2'), ['A'])], 0)
        self.assertEqual(result, rule.records[-1]['baseline_assignments'])
        self.assertEqual(rule.records[-1]['evaluated_moves'], 0)
        self.assertEqual(rule.records[-1]['search_stop_reason'], 'move_limit')

    def test_move_budget_bounds_evaluated_candidates(self):
        world = World({'A': {'ed_bed': 1}, 'B': {'ed_bed': 1}})
        ready = [(patient('P1'), ['A', 'B']), (patient('P2'), ['A'])]
        for limit in (1, 2, 4, 8):
            with self.subTest(limit=limit):
                rule = self.rule(max_moves=limit)
                result = rule.decide_batch(world, ready, 0)
                self.assertLessEqual(rule.records[-1]['evaluated_moves'], limit)
                self.assertTrue(independent_feasible(world, ready, result))

    def test_expired_cooperative_budget_returns_baseline(self):
        world = World({'A': {'ed_bed': 1}})
        rule = HeuristicAssignmentRule(time_limit_seconds=.01)
        with patch('simulation.heuristic_assignment.time.perf_counter', side_effect=[0, .02, .04, .06, .08]):
            result = rule.decide_batch(world, [(patient('P'), ['A'])], 0)
        self.assertEqual(result, {'P': 'A'})
        record = rule.records[-1]
        self.assertEqual(record['evaluated_moves'], 0)
        self.assertEqual(record['search_stop_reason'], 'time_limit')
        self.assertGreater(record['wall_seconds'], record['time_limit_seconds'])

    def test_backend_is_never_loaded_even_with_positive_budget(self):
        world = World({'A': {'ed_bed': 2}})
        with patch('simulation.cp_sat_assignment._load_cp_model', side_effect=AssertionError('not needed')):
            self.assertEqual(self.rule().decide_batch(world, [(patient('P'), ['A'])], 0), {'P': 'A'})

    def test_metadata_exposes_budget_scope_no_proof_and_improvement_history(self):
        world = World({'A': {'ed_bed': 1}, 'B': {'ed_bed': 1}})
        ready = [(patient('P1'), ['A', 'B']), (patient('P2'), ['A'])]
        rule = self.rule()
        result = rule.decide_batch(world, ready, 11)
        record = rule.records[-1]
        self.assertEqual(record['status'], 'HEURISTIC')
        self.assertEqual(record['t'], 11)
        self.assertEqual(record['scope'], 'current_same_minute_accepted_offers')
        self.assertEqual(record['time_budget_scope'], 'optional_search_after_snapshot_and_baseline')
        self.assertEqual(record['assignments'], result)
        self.assertEqual(record['objective'], independent_objective(world, ready, result))
        self.assertEqual(record['assigned_count'], sum(v is not None for v in result.values()))
        self.assertEqual(record['accepted_moves'], len(record['improvements']))
        self.assertEqual(record['accepted_moves'], len(record['accepted_move_types']))
        self.assertAlmostEqual(record['wall_seconds'], record['preprocessing_seconds'] + record['search_seconds'])
        self.assertFalse(record['optimal_proven'])
        prior = record['baseline_objective']
        for improvement in record['improvements']:
            self.assertLess(improvement['objective'], prior)
            prior = improvement['objective']
        self.assertEqual(prior, record['objective'])

    def test_random_current_snapshots_remain_feasible_and_never_worsen_baseline(self):
        rng = random.Random(12101)
        for case in range(100):
            world = World({hid: {'ed_bed': rng.randrange(5), 'team': rng.randrange(3)}
                           for hid in ('A', 'B', 'C')})
            ready = []
            for index in range(rng.randrange(1, 8)):
                p = patient(f'P{index}', rng.choice((1, 2, 4)),
                            {'ed_bed': rng.randrange(1, 3), 'team': rng.randrange(2)})
                options = [hid for hid in world.hospitals if rng.random() < .8]
                ready.append((p, options))
                world.times.update({(p.id, hid): rng.randrange(30) for hid in options})
            rule = self.rule()
            result = rule.decide_batch(world, ready, case)
            record = rule.records[-1]
            with self.subTest(case=case):
                self.assertTrue(independent_feasible(world, ready, result))
                self.assertLessEqual(independent_objective(world, ready, result), record['baseline_objective'])
                self.assertEqual(record['objective'], independent_objective(world, ready, result))
                self.assertLessEqual(record['evaluated_moves'], 256)

    def test_small_exhaustive_optimum_is_lower_bound_not_claimed_as_proof(self):
        world = World({'A': {'ed_bed': 2}, 'B': {'ed_bed': 1}},
                      {('P1', 'A'): 20, ('P1', 'B'): 3, ('P2', 'A'): 2})
        ready = [(patient('P1', 4), ['A', 'B']), (patient('P2', 2), ['A']),
                 (patient('P3', 1), ['A', 'B'])]
        objectives = [independent_objective(world, ready, plan)
                      for combo in product(*(options + [None] for _, options in ready))
                      if independent_feasible(world, ready, plan := {p.id: hid for (p, _), hid in zip(ready, combo)})]
        rule = self.rule()
        result = rule.decide_batch(world, ready, 0)
        self.assertGreaterEqual(independent_objective(world, ready, result), min(objectives))
        self.assertFalse(rule.records[-1]['optimal_proven'])

    def test_invalid_constructor_settings_rejected(self):
        for value in (True, -1, float('inf'), float('nan'), '1'):
            with self.subTest(time=value), self.assertRaises(ValueError):
                HeuristicAssignmentRule(time_limit_seconds=value)
        for value in (True, -1, .1, '1'):
            with self.subTest(moves=value), self.assertRaises(ValueError):
                HeuristicAssignmentRule(max_moves=value)

    def test_invalid_demands_priority_travel_capacity_and_duplicate_ids_rejected(self):
        world = World({'A': {'ed_bed': 2}})
        for value in (True, -1, float('nan'), float('inf'), .5):
            with self.subTest(travel=value), self.assertRaises(ValueError):
                world.times['P', 'A'] = value
                self.rule().decide_batch(world, [(patient('P'), ['A'])], 0)
        world.times['P', 'A'] = 1
        for value in (True, -1, float('nan'), float('inf'), .5):
            with self.subTest(demand=value), self.assertRaises(ValueError):
                self.rule().decide_batch(world, [(patient('P', demand={'ed_bed': value}), ['A'])], 0)
        for value in (True, -1, 0, float('nan'), float('inf'), .5):
            with self.subTest(priority=value), self.assertRaises(ValueError):
                self.rule().decide_batch(world, [(patient('P', value), ['A'])], 0)
        with patch.object(world.hospitals['A'], 'free', return_value=.5), self.assertRaises(ValueError):
            self.rule().decide_batch(world, [(patient('P'), ['A'])], 0)
        p = patient('P')
        with self.assertRaises(ValueError):
            self.rule().decide_batch(world, [(p, ['A']), (p, ['A'])], 0)
        with self.assertRaises(ValueError):
            self.rule().decide_batch(world, [(p, ['MISSING'])], 0)


if __name__ == '__main__':
    unittest.main()
