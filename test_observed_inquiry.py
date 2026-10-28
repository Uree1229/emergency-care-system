"""Past-only boundaries and hospital-specific delayed inquiry hedging."""
from copy import deepcopy
import json
import unittest

from simulation.observed_inquiry import (
    DEFAULT_OBSERVED_CONFIG, plan_inquiries, validate_observed_config,
)


def candidate(hospital, travel=10):
    return {'hospital': hospital, 'travel_minutes': travel}


def observation(hospital, submitted=0, observed=1, outcome='reply', patient='A'):
    return {'hospital': hospital, 'patient': patient, 'submitted_t': submitted,
            'observed_t': observed, 'outcome': outcome}


def inquiry(hospital='H1', submitted=0, patient='A', travel=10):
    return {'hospital': hospital, 'patient': patient, 'submitted_t': submitted,
            'travel_minutes': travel}


class ObservedInquiryTests(unittest.TestCase):
    def plan(self, candidates=None, observations=(), pending=(),
             patient_pending=(), t=10, max_k=3, **config):
        if candidates is None:
            candidates = [candidate('H1'), candidate('H2')]
        return plan_inquiries(config, t=t, candidates=candidates,
                              observations=observations, pending=pending,
                              patient_pending=patient_pending, max_k=max_k)

    def hospital(self, result, hospital):
        return next(row for row in result['evidence']['hospitals']
                    if row['hospital'] == hospital)

    def test_cold_start_sends_only_one_and_uses_deterministic_ties(self):
        result = self.plan(candidates=[candidate('H2'), candidate('H1')],
                           max_new_per_review=3)
        self.assertEqual((result['send'], result['reason']), (['H1'], 'initial_best'))
        self.assertEqual(self.hospital(result, 'H1')['reply_estimate_source'], 'prior')
        self.assertEqual(self.hospital(result, 'H1')['base_reply_minutes'], 3)
        self.assertEqual(self.hospital(result, 'H1')['smoothed_timeout_fraction'], .5)
        self.assertEqual(result['evidence']['candidate_scores'][0]['score_minutes'], 16)

    def test_hospital_reply_history_changes_rank_without_truthful_queue_access(self):
        observations = [observation('H1', 0, 8), observation('H1', 1, 9),
                        observation('H2', 0, 1), observation('H2', 1, 2)]
        result = self.plan([candidate('H1', 5), candidate('H2', 8)], observations)
        self.assertEqual(result['send'], ['H2'])
        self.assertEqual(self.hospital(result, 'H1')['base_reply_minutes'], 8)
        self.assertEqual(self.hospital(result, 'H2')['base_reply_minutes'], 1)

    def test_sparse_hospital_samples_fall_back_to_global_then_prior(self):
        one = self.plan(observations=[observation('H1', 0, 8)])
        self.assertEqual(self.hospital(one, 'H1')['reply_estimate_source'], 'prior')
        two = self.plan(observations=[observation('H1', 0, 8),
                                      observation('H3', 0, 2)])
        self.assertEqual(self.hospital(two, 'H1')['reply_estimate_source'], 'global')
        self.assertEqual(self.hospital(two, 'H2')['base_reply_minutes'], 5)

    def test_timeouts_increase_only_that_hospital_risk_and_are_not_reply_samples(self):
        result = self.plan(observations=[observation('H1', 0, 8, 'timeout')] * 3)
        self.assertEqual(result['send'], ['H2'])
        h1 = self.hospital(result, 'H1')
        self.assertEqual(h1['reply_samples'], 0)
        self.assertEqual(h1['timeout_samples'], 3)
        self.assertAlmostEqual(h1['smoothed_timeout_fraction'], .8)
        self.assertEqual(result['evidence']['global_reply_samples'], 0)
        self.assertEqual(self.hospital(result, 'H2')['smoothed_timeout_fraction'], .5)

    def test_accept_and_decline_are_both_observed_reply_latencies(self):
        result = self.plan(observations=[observation('H1', 0, 2, 'accept'),
                                        observation('H1', 0, 4, 'decline')])
        self.assertEqual(self.hospital(result, 'H1')['median_reply_minutes'], 3)
        self.assertEqual(self.hospital(result, 'H1')['reply_samples'], 2)

    def test_server_own_backlog_is_hospital_specific(self):
        pending = [inquiry('H1', 9, 'B'), inquiry('H1', 9, 'C')]
        result = self.plan(pending=pending)
        self.assertEqual(result['send'], ['H2'])
        self.assertEqual(self.hospital(result, 'H1')['unresolved_inquiries'], 2)
        self.assertEqual(self.hospital(result, 'H2')['unresolved_inquiries'], 0)
        self.assertEqual(self.plan(pending=pending, congestion_weight=0)['send'], ['H1'])

    def test_pending_total_delay_excludes_the_inquiry_itself(self):
        own = inquiry(submitted=9)
        result = self.plan(pending=[own], patient_pending=[own])
        score = result['evidence']['pending_scores'][0]
        self.assertEqual(score['estimated_total_reply_minutes'], 3)
        self.assertEqual(score['remaining_cost_minutes'], 2)
        self.assertFalse(score['overdue_uncertainty_guard'])

    def test_hedge_cannot_start_before_minimum_age_even_for_better_candidate(self):
        own = inquiry(submitted=9, travel=50)
        result = self.plan([candidate('H2', 1)], pending=[own], patient_pending=[own])
        self.assertEqual(result['send'], [])
        self.assertEqual(result['reason'], 'wait_for_hedge')
        result = self.plan([candidate('H2', 1)], pending=[own], patient_pending=[own], t=11)
        self.assertEqual((result['send'], result['reason']),
                         (['H2'], 'hedge_better_candidate'))

    def test_overdue_guard_does_not_assume_old_reply_is_one_minute_away(self):
        own = inquiry(submitted=0)
        kwargs = {'candidates': [candidate('H2')], 'pending': [own],
                  'patient_pending': [own]}
        tied = self.plan(t=3, **kwargs)
        self.assertEqual(tied['reason'], 'pending_preferred')
        result = self.plan(t=4, **kwargs)
        self.assertEqual(result['send'], ['H2'])
        score = result['evidence']['pending_scores'][0]
        self.assertTrue(score['overdue_uncertainty_guard'])
        self.assertEqual(score['remaining_cost_minutes'], 4)

    def test_best_pending_request_can_prevent_needless_extra_inquiry(self):
        own = inquiry(submitted=0, travel=1)
        result = self.plan([candidate('H2', 30)], pending=[own], patient_pending=[own])
        self.assertEqual((result['send'], result['reason']), ([], 'pending_preferred'))

    def test_hedge_margin_is_strict_and_configurable(self):
        own = inquiry(submitted=0)
        kwargs = {'candidates': [candidate('H2')], 'pending': [own],
                  'patient_pending': [own], 't': 4}
        self.assertEqual(self.plan(**kwargs)['send'], ['H2'])
        self.assertEqual(self.plan(**kwargs, hedge_margin=1)['send'], [])

    def test_concurrent_cap_and_per_review_limit_preserve_submitted_work(self):
        own = inquiry(submitted=0, travel=50)
        candidates = [candidate('H2'), candidate('H3'), candidate('H4')]
        kwargs = {'candidates': candidates, 'pending': [own], 'patient_pending': [own]}
        result = self.plan(max_k=1, **kwargs)
        self.assertEqual((result['send'], result['reason']), ([], 'concurrency_cap'))
        self.assertEqual(self.plan(**kwargs)['send'], ['H2'])
        self.assertEqual(self.plan(**kwargs, max_new_per_review=3)['send'], ['H2', 'H3'])
        self.assertEqual(own, inquiry(submitted=0, travel=50))

    def test_defensive_open_candidate_exclusion_prevents_duplicate_submissions(self):
        own = inquiry(submitted=0, travel=50)
        result = self.plan([candidate('H1'), candidate('H2')],
                           pending=[own], patient_pending=[own])
        self.assertEqual(result['send'], ['H2'])
        self.assertEqual([row['hospital'] for row in result['evidence']['candidate_scores']],
                         ['H2'])

    def test_empty_candidates_report_no_candidates_without_sending(self):
        result = self.plan(candidates=[])
        self.assertEqual((result['send'], result['reason']), ([], 'no_candidates'))

    def test_window_is_open_lower_closed_upper_on_observation_time(self):
        observations = [observation('H1', 0, 7), observation('H1', 0, 8),
                        observation('H1', 9, 10)]
        result = self.plan(observations=observations, t=10, window=3)
        self.assertEqual(self.hospital(result, 'H1')['reply_samples'], 2)
        self.assertEqual(self.hospital(result, 'H1')['median_reply_minutes'], 4.5)

    def test_old_unresolved_queries_still_count_outside_reply_window(self):
        result = self.plan(pending=[inquiry('H1', 0, 'B')], t=100, window=3)
        self.assertEqual(self.hospital(result, 'H1')['unresolved_inquiries'], 1)

    def test_future_observations_and_pending_are_ignored_before_outcome_validation(self):
        past = [observation('H1', 0, 2)]
        expected = self.plan(observations=past)
        result = self.plan(observations=past + [observation('H1', 0, 11, 'unknown'),
                                                observation('H1', 11, 10, 'unknown')],
                           pending=[inquiry('H1', 11, 'B')],
                           patient_pending=[inquiry('H1', 11, 'B')])
        self.assertEqual(result, expected)

    def test_unknown_private_state_fields_are_not_read_or_exported(self):
        private = observation('H1', 0, 2)
        private.update(actual_workers=900, finish_t=999, future_accept=True)
        ledger = inquiry('H1', 9, 'B')
        ledger.update(actual_workers=0, finish_t=10000)
        expected = self.plan(observations=[observation('H1', 0, 2)],
                             pending=[inquiry('H1', 9, 'B')])
        actual = self.plan(observations=[private], pending=[ledger])
        self.assertEqual(actual, expected)
        dumped = json.dumps(actual)
        self.assertNotIn('actual_workers', dumped)
        self.assertNotIn('finish_t', dumped)
        self.assertNotIn('future_accept', dumped)

    def test_duplicate_ledger_rows_do_not_inflate_load_or_cap(self):
        own = inquiry(submitted=0, travel=50)
        expected = self.plan(pending=[own], patient_pending=[own])
        actual = self.plan(pending=[own, deepcopy(own)],
                           patient_pending=[own, deepcopy(own)])
        self.assertEqual(actual, expected)

    def test_inputs_are_immutable_output_is_serializable_and_order_deterministic(self):
        config = {'congestion_weight': .5}
        candidates = [candidate('H2'), candidate('H1')]
        observations = [observation('H2', 1, 2), observation('H1', 0, 3)]
        pending = [inquiry('H2', 9, 'B')]
        before = deepcopy((config, candidates, observations, pending))
        kwargs = dict(t=10, candidates=candidates, observations=observations,
                      pending=pending, patient_pending=[], max_k=3)
        result = plan_inquiries(config, **kwargs)
        self.assertEqual((config, candidates, observations, pending), before)
        self.assertEqual(result, plan_inquiries(config, **kwargs))
        kwargs['candidates'] = candidates[::-1]
        kwargs['observations'] = observations[::-1]
        self.assertEqual(result, plan_inquiries(config, **kwargs))
        self.assertEqual(json.loads(json.dumps(result)), result)

    def test_config_is_complete_and_independent(self):
        supplied = {'hedge_after': 4}
        result = validate_observed_config(supplied, max_k=1)
        self.assertEqual(result, {**DEFAULT_OBSERVED_CONFIG, 'hedge_after': 4})
        result['window'] = 50
        self.assertEqual(supplied, {'hedge_after': 4})
        self.assertEqual(DEFAULT_OBSERVED_CONFIG['window'], 30)

    def test_invalid_config_and_caps_are_rejected(self):
        configs = [None, [], {'typo': 1}, {'window': 0}, {'window': 1.5},
                   {'window': True}, {'prior_reply': 0}, {'prior_reply': float('nan')},
                   {'prior_reply': float('inf')}, {'min_samples': 0},
                   {'congestion_weight': -1}, {'congestion_weight': False},
                   {'hedge_after': 0}, {'hedge_after': True}, {'hedge_margin': -1},
                   {'max_new_per_review': 0}, {'max_new_per_review': 1.5},
                   {'timeout_penalty': -1}]
        for config in configs:
            with self.subTest(config=config), self.assertRaises(ValueError):
                validate_observed_config(config)
        for cap in (0, -1, 1.5, True):
            with self.subTest(cap=cap), self.assertRaises(ValueError):
                validate_observed_config({}, max_k=cap)

    def test_invalid_observed_data_and_time_are_rejected(self):
        bad = [observation('H1', 5, 4), observation('H1', 0, 4, 'unknown'),
               observation('', 0, 4), observation('H1', -1, 4)]
        for row in bad:
            with self.subTest(row=row), self.assertRaises(ValueError):
                self.plan(observations=[row])
        for t in (-1, True, 1.5):
            with self.subTest(t=t), self.assertRaises(ValueError):
                self.plan(t=t)
        for rows in ([candidate('H1', -1)], [candidate('H1', float('nan'))],
                     [candidate('H1'), candidate('H1')]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                self.plan(candidates=rows)

    def test_patient_pending_requires_actual_ledger_subset_and_single_patient(self):
        own = inquiry()
        with self.assertRaises(ValueError):
            self.plan(patient_pending=[own])
        other = inquiry('H2', patient='B')
        with self.assertRaises(ValueError):
            self.plan(pending=[own, other], patient_pending=[own, other])


if __name__ == '__main__':
    unittest.main()
