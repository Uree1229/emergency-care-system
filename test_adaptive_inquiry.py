"""Observation-only boundaries and behaviour of adaptive inquiry concurrency."""
from copy import deepcopy
import unittest

from simulation.adaptive_inquiry import (
    DEFAULT_ADAPTIVE_CONFIG, decide_inquiry_limit, validate_adaptive_config,
)


def observation(submitted, observed, outcome='accept', patient='A', hospital='H'):
    return {'submitted_t': submitted, 'observed_t': observed, 'outcome': outcome,
            'patient': patient, 'hospital': hospital}


class AdaptiveInquiryTests(unittest.TestCase):
    def decide(self, observations=(), pending=(), t=20, max_k=5, **config):
        return decide_inquiry_limit(config, t=t, observations=observations,
                                    pending=pending, max_k=max_k)

    def test_cold_start_uses_predeclared_initial_limit(self):
        initial = self.decide()
        self.assertEqual((initial['limit'], initial['reason']), (3, 'cold_start'))
        self.assertEqual(initial['evidence']['completed_samples'], 0)
        self.assertIsNone(initial['evidence']['median_reply_minutes'])
        self.assertIsNone(initial['evidence']['timeout_fraction'])
        self.assertEqual(self.decide(initial_k=1)['limit'], 1)

    def test_fast_reply_samples_use_fast_limit_including_declines(self):
        replies = [observation(10, 11, 'decline'), observation(12, 14, 'reply'),
                   observation(15, 16, 'decline')]
        result = self.decide(replies, fast_k=4)
        self.assertEqual((result['limit'], result['reason']), (4, 'fast_replies'))
        self.assertEqual(result['evidence']['median_reply_minutes'], 1)
        self.assertEqual(result['evidence']['reply_samples'], 3)

    def test_slow_boundary_reduces_limit_and_beats_timeout_expansion(self):
        history = [observation(10, 13), observation(12, 16, 'decline'),
                   observation(14, 16)]
        history += [observation(1, 18, 'timeout')] * 3
        result = self.decide(history)
        self.assertEqual((result['limit'], result['reason']), (1, 'slow_replies'))
        self.assertEqual(result['evidence']['median_reply_minutes'], 3)
        self.assertEqual(result['evidence']['timeout_fraction'], 0.5)

    def test_timeouts_with_non_slow_reply_widen_at_fraction_boundary(self):
        history = [observation(10, 11), observation(1, 16, 'timeout'),
                   observation(2, 17, 'timeout'), observation(13, 15, 'decline')]
        result = self.decide(history)
        self.assertEqual((result['limit'], result['reason']),
                         (5, 'timeouts_without_slow_replies'))
        self.assertEqual(result['evidence']['completed_samples'], 4)
        self.assertEqual(result['evidence']['median_reply_minutes'], 1.5)

    def test_timeouts_below_fraction_do_not_widen(self):
        history = [observation(10, 11)] * 3 + [observation(1, 18, 'timeout')]
        result = self.decide(history)
        self.assertEqual((result['limit'], result['reason']), (3, 'fast_replies'))

    def test_all_timeouts_do_not_pretend_to_prove_fast_processing(self):
        result = self.decide([observation(1, 16, 'timeout')] * 3)
        self.assertEqual((result['limit'], result['reason']), (3, 'cold_start'))
        self.assertEqual(result['evidence']['timeout_fraction'], 1)
        self.assertIsNone(result['evidence']['median_reply_minutes'])

    def test_insufficient_observations_with_aged_pending_backlog_reduce_limit(self):
        pending = [{'submitted_t': 17}, {'submitted_t': 16}, {'submitted_t': 0}]
        result = self.decide([observation(16, 17)], pending)
        self.assertEqual((result['limit'], result['reason']), (1, 'aged_pending'))
        self.assertEqual(result['evidence']['aged_pending_count'], 3)
        # Age exactly equal to the threshold is included; just younger is not.
        pending[-1]['submitted_t'] = 18
        result = self.decide(pending=pending)
        self.assertEqual(result['reason'], 'cold_start')
        self.assertEqual(result['evidence']['aged_pending_count'], 2)

    def test_pending_guard_uses_ongoing_old_requests_beyond_reply_window(self):
        pending = [{'submitted_t': 1}] * 3
        result = self.decide(pending=pending, t=100, window=10)
        self.assertEqual(result['reason'], 'aged_pending')
        self.assertEqual(result['evidence']['pending_count'], 3)

    def test_window_uses_observation_time_with_open_lower_closed_upper_bound(self):
        recent = [observation(7, 8), observation(8, 10), observation(10, 20)]
        stale = [observation(0, 0), observation(0, 7)]
        result = self.decide(recent + stale, window=13)
        self.assertEqual(result['evidence']['completed_samples'], 3)
        self.assertEqual(result['evidence']['median_reply_minutes'], 2)
        self.assertEqual(result['reason'], 'fast_replies')

    def test_future_observations_and_submissions_cannot_change_current_choice(self):
        history = [observation(10, 11)] * 3
        expected = self.decide(history)
        result = self.decide(history + [observation(0, 21, 'timeout'),
                                        observation(21, 22),
                                        observation(21, 20)],
                             pending=[{'submitted_t': 21}] * 10)
        self.assertEqual(result, expected)

    def test_unknown_extra_state_is_never_read_or_included_in_evidence(self):
        replies = [observation(10, 11)] * 3
        for row in replies:
            row['future_finish'] = 200
            row['actual_workers'] = 100
        result = self.decide(replies, pending=[{'submitted_t': 19, 'finish_t': 200}])
        self.assertEqual(result['reason'], 'fast_replies')
        self.assertFalse({'future_finish', 'actual_workers', 'finish_t'}
                         & result['evidence'].keys())

    def test_concurrency_cap_applies_to_cold_fast_and_timeout_limits(self):
        self.assertEqual(self.decide(max_k=1)['limit'], 1)
        self.assertEqual(self.decide([observation(10, 11)] * 3,
                                     max_k=2)['limit'], 2)
        history = [observation(10, 11)] + [observation(0, 16, 'timeout')] * 2
        self.assertEqual(self.decide(history, max_k=4)['limit'], 4)

    def test_decisions_are_deterministic_and_do_not_modify_inputs(self):
        config = {'initial_k': 1, 'slow_minutes': 6}
        history = [observation(10, 12)] * 3
        pending = [{'submitted_t': 18, 'patient': 'B', 'hospital': 'H2'}]
        before = deepcopy((config, history, pending))
        kwargs = {'t': 20, 'observations': history, 'pending': pending, 'max_k': 5}
        self.assertEqual(decide_inquiry_limit(config, **kwargs),
                         decide_inquiry_limit(config, **kwargs))
        self.assertEqual((config, history, pending), before)

    def test_config_returns_complete_fresh_dict(self):
        supplied = {'initial_k': 1}
        result = validate_adaptive_config(supplied, max_k=2)
        self.assertEqual(result, {**DEFAULT_ADAPTIVE_CONFIG, 'initial_k': 1})
        result['window'] = 99
        self.assertEqual(supplied, {'initial_k': 1})
        self.assertEqual(DEFAULT_ADAPTIVE_CONFIG['window'], 20)

    def test_invalid_config_types_ranges_and_unknown_fields_are_rejected(self):
        invalid = [None, [], {'unexpected': 1}, {'initial_k': 2},
                   {'initial_k': True}, {'window': 0}, {'window': 1.5},
                   {'min_samples': -1}, {'min_k': 2, 'initial_k': 1},
                   {'fast_k': 2}, {'slow_minutes': 0},
                   {'slow_minutes': float('nan')}, {'slow_minutes': float('inf')},
                   {'slow_minutes': True}, {'timeout_fraction': -0.1},
                   {'timeout_fraction': 1.1}, {'timeout_fraction': False}]
        for config in invalid:
            with self.subTest(config=config), self.assertRaises(ValueError):
                validate_adaptive_config(config)
        for cap in (0, -1, 1.5, True):
            with self.subTest(cap=cap), self.assertRaises(ValueError):
                validate_adaptive_config({}, max_k=cap)
        with self.assertRaises(ValueError):
            validate_adaptive_config({'min_k': 2}, max_k=1)

    def test_invalid_observed_data_and_evaluation_time_are_rejected(self):
        for row in (observation(10, 9), observation(1, 2, 'unknown'),
                    observation(-1, 2), observation(1, float('nan'))):
            with self.subTest(row=row), self.assertRaises(ValueError):
                self.decide([row])
        for t in (-1, True, 1.5):
            with self.subTest(t=t), self.assertRaises(ValueError):
                self.decide(t=t)


if __name__ == '__main__':
    unittest.main()
