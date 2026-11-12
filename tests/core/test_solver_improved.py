import copy
import unittest
from solver_improved import *


class AllocationTests(unittest.TestCase):
    def test_both_policies_match_exhaustive_and_show_tradeoff(self):
        data = conflict_case()
        for policy in ('total_first', 'severity_first'):
            plan, meta = solve(data, policy)
            self.assertTrue(meta['lex_optimal'])
            self.assertEqual(score(data, plan, policy), score(data, exhaustive(data, policy), policy))
            self.assertEqual(plan['H'] is None, policy == 'total_first')

    def test_fixed_patient_still_occupies_bed(self):
        data = conflict_case()
        data['fixed'] = {'H': dict(h=0, start=0)}
        plan, _ = solve(data, 'total_first')
        self.assertEqual(plan['H'], data['fixed']['H'])
        self.assertTrue(all(plan[f'L{k}'] is None for k in range(3)))

    def test_elapsed_wait_is_not_reset(self):
        data = conflict_case()
        data.update(now=5, horizon=10)
        plan, _ = solve(data)
        self.assertTrue(all(a is None for a in plan.values()))

    def test_stale_hint_rejected_and_zero_budget_fallback_valid(self):
        data = conflict_case()
        old = greedy(data)
        data['blocks'] = [dict(h=0, start=0, duration=4, special=False)]
        data['version'] += 1
        plan, meta = solve(data, seconds=0, hint=old)
        self.assertTrue(meta['hint_rejected'])
        self.assertTrue(meta['fallback'])
        self.assertTrue(validate(data, plan))
        self.assertTrue(all(a is None for a in plan.values()))

    def test_conflicting_fixed_state_needs_review(self):
        data = conflict_case()
        data['fixed'] = {'H': dict(h=0, start=0)}
        data['blocks'] = [dict(h=0, start=0, duration=1, special=False)]
        plan, meta = solve(data)
        self.assertIsNone(plan)
        self.assertEqual(meta['status'], 'NEEDS_REVIEW')

    def test_version_change_prevents_commit(self):
        data = conflict_case()
        plan, meta = solve(data)
        self.assertTrue(commit_allowed(data, plan, meta))
        data['version'] += 1
        self.assertFalse(commit_allowed(data, plan, meta))

    def test_shared_team_and_multiple_hospitals_match_exhaustive(self):
        data = dict(version=1, now=1, horizon=3, capacities=[(2, 1), (1, 0)], blocks=[], fixed={}, patients=[])
        for i in range(3):
            p = sample(GROUPS[i], str(i), 2)
            p.update(deadline=3, special=i < 2, travel=[0, 1], eligible=[0, 1])
            data['patients'].append(p)
        plan, meta = solve(data)
        self.assertTrue(meta['lex_optimal'])
        self.assertEqual(score(data, plan, 'severity_first'), score(data, exhaustive(data, 'severity_first'), 'severity_first'))
        self.assertTrue(validate(data, plan))

    def test_empty_candidate_and_deadline_boundary(self):
        data = conflict_case()
        data['patients'] = [sample(4, 'no_candidate', 1), sample(2, 'boundary', 1)]
        data['patients'][0]['eligible'] = []
        data['patients'][1]['travel'] = [2]
        plan, _ = solve(data)
        self.assertIsNone(plan['no_candidate'])
        self.assertEqual(plan['boundary']['start'], 2)

    def test_invalid_ids_rejected(self):
        data = conflict_case()
        data['patients'][1]['id'] = 'H'
        with self.assertRaises(ValueError):
            solve(data)


if __name__ == '__main__':
    unittest.main(verbosity=2)
