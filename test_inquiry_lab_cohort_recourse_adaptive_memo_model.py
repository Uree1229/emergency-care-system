"""Dispatch boundaries and preservation; all fixtures are synthetic."""
from copy import deepcopy
import unittest
from inquiry_lab import cohort_recourse_adaptive_memo_model as adaptive
from test_inquiry_lab_cohort_recourse_memo_model import owner, typed_ordered

original = adaptive.original


def args_for(o):
    return (o, original.freeze_owner(o), o['inputs']['pending'], [])


def outcome(function, args):
    try:
        return ('return', typed_ordered(function(*args)))
    except Exception as exc:
        return ('error', type(exc).__module__, type(exc).__qualname__, typed_ordered(exc.args))


class AdaptiveMemoTests(unittest.TestCase):
    def assert_equal(self, args):
        before = typed_ordered(deepcopy(args))
        self.assertEqual(outcome(original.score_owner, args), outcome(adaptive.score_owner, args))
        self.assertEqual(before, typed_ordered(args))

    def test_remaining_window_boundary(self):
        for remaining in (44, 45, 46):
            with self.subTest(remaining=remaining):
                args = args_for(owner(horizon=remaining, own_count=0))
                self.assertEqual(adaptive.dispatch_kind(*args), 'original' if remaining == 44 else 'memo')
                self.assert_equal(args)

    def test_barrier_changes_eligible_window(self):
        for barrier in (5, 6):
            args = args_for(owner(horizon=50, barrier=barrier))
            self.assertEqual(adaptive.dispatch_kind(*args), 'memo' if barrier == 5 else 'original')
            self.assert_equal(args)

    def test_fixed_own_boundary_even_when_stale(self):
        for count in (3, 4):
            for age in (0, 181):
                args = args_for(owner(horizon=100, own_count=count, age=age))
                self.assertEqual(adaptive.dispatch_kind(*args), 'memo' if count == 3 else 'original')
                self.assert_equal(args)

    def test_budget_and_candidate_boundaries(self):
        for candidates in (1, 2):
            for budget in (1, 2):
                o = owner(horizon=100, candidates=candidates)
                o['inputs']['max_new'] = budget
                args = args_for(o)
                self.assertEqual(adaptive.dispatch_kind(*args), 'memo' if candidates == budget == 2 else 'original')
                self.assert_equal(args)

    def test_origin_mismatches_preserve_first_exception(self):
        for key, value in [('now', 999), ('evaluation_end_at', 2000), ('patient_id', 'OTHER')]:
            args = args_for(owner(horizon=100))
            args[1][key] = value
            self.assertEqual(adaptive.dispatch_kind(*args), 'original')
            self.assert_equal(args)

    def test_nonfinite_or_boolean_read_fields_fallback(self):
        for field, value in [('next_evaluation_at', float('inf')), ('started_at', float('nan')), ('deadline_at', True)]:
            args = args_for(owner(horizon=100))
            args[0]['job'][field] = value
            self.assertEqual(adaptive.dispatch_kind(*args), 'original')
            self.assert_equal(args)
        args = args_for(owner(horizon=100)); args[0]['inputs']['max_new'] = True
        self.assertEqual(adaptive.dispatch_kind(*args), 'original'); self.assert_equal(args)

    def test_float_boundary_is_not_coerced(self):
        for barrier in (5.0, 5.5):
            args = args_for(owner(horizon=50, barrier=barrier))
            self.assertEqual(adaptive.dispatch_kind(*args), 'memo' if barrier == 5.0 else 'original')
            self.assert_equal(args)

    def test_custom_owner_has_no_extra_getter_calls(self):
        class Owner(dict):
            def __getitem__(self, key):
                self.log.append(key)
                return super().__getitem__(key)
            def get(self, key, default=None):
                self.log.append('get:'+key)
                return super().get(key, default)
        logs, results = [], []
        for function in (original.score_owner, adaptive.score_owner):
            args = args_for(owner(horizon=100))
            special = Owner(args[0]); special.log = []
            args = (special, *args[1:])
            results.append(outcome(function, args)); logs.append(special.log)
        self.assertEqual(results[0], results[1]); self.assertEqual(logs[0], logs[1])

    def test_custom_guard_number_does_not_acquire_comparisons(self):
        class Trap:
            def __ge__(self, other):
                raise AssertionError('unexpected guard comparison')
            def __lt__(self, other):
                raise AssertionError('unexpected guard comparison')
        args = args_for(owner(horizon=100)); args[0]['job']['started_at'] = Trap()
        self.assertEqual(adaptive.dispatch_kind(*args), 'original')
        self.assertEqual(outcome(original.score_owner, args), outcome(adaptive.score_owner, args))

    def test_row_and_jobs_are_not_walked_by_guard(self):
        class TrapList(list):
            def __iter__(self):
                raise AssertionError('row/jobs were iterated')
        args = args_for(owner(horizon=100))
        row, jobs = TrapList(), TrapList()
        self.assertEqual(adaptive.dispatch_kind(args[0], args[1], row, jobs), 'memo')
        altered = (args[0], args[1], row, jobs)
        self.assertEqual(outcome(original.score_owner, altered), outcome(adaptive.score_owner, altered))

    def test_invalid_edges_fallback_and_errors_unchanged(self):
        for edges in ([True, 10], ['invalid'], [float('nan'), 10]):
            args = args_for(owner(horizon=100)); args[1]['cell_edges_seconds'] = edges
            self.assertEqual(adaptive.dispatch_kind(*args), 'original')
            self.assert_equal(args)

    def test_unread_extra_custom_or_cyclic_values_are_ignored(self):
        args = args_for(owner(horizon=100))
        cycle = []; cycle.append(cycle)
        args[0]['unread'] = cycle
        self.assertEqual(adaptive.dispatch_kind(*args), 'memo')
        self.assertEqual(outcome(original.score_owner, args), outcome(adaptive.score_owner, args))

    def test_nonplanning_guards(self):
        for label, mutate in [
            ('accept', lambda o: o.update(valid_acceptance_ids=[1])),
            ('disabled', lambda o: o['job'].update(enabled=0)),
            ('policy', lambda o: o['job'].update(policy_name='fixed_3')),
            ('job', lambda o: o.update(job=None))]:
            with self.subTest(label=label):
                args = args_for(owner(horizon=100)); mutate(args[0])
                self.assertEqual(adaptive.dispatch_kind(*args), 'original')
                self.assert_equal(args)


if __name__ == '__main__':
    unittest.main()
