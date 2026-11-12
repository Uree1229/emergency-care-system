"""Independent public-input and cadence tests for the inquiry-only candidates."""
from copy import deepcopy
from types import SimpleNamespace
import unittest

from simulation import inquiry_preserving_policy_20261029 as policy


def spec(refill='all', retry='history', name='TEST_Q'):
    return dict(id=name, refill=refill, retry=retry)


def refill_snapshot(**changes):
    value = dict(t=20, patient='P1', priority=2, request_t=5, state='SEARCH',
        has_valid_offer=False, ready=False, received_this_tick=True, retry_at=None,
        pending=[dict(hospital='OPEN', submitted_t=12)],
        candidates=[dict(hospital='B', travel_minutes=5),
                    dict(hospital='A', travel_minutes=5),
                    dict(hospital='C', travel_minutes=2)], max_k=3)
    value.update(changes)
    return value


def observation(hospital='H1', sent=95, observed=100, outcome='decline', patient='P1'):
    return dict(patient=patient, hospital=hospital, submitted_t=sent,
                observed_t=observed, outcome=outcome)


def retry_snapshot(observations=(), **changes):
    value = dict(t=100, patient='P1', priority=2, baseline_wait=3,
                 observations=list(observations))
    value.update(changes)
    return value


class InquirySpecTests(unittest.TestCase):
    def test_predeclared_six_specs_are_unique_and_do_not_claim_to_be_original_G2(self):
        self.assertEqual(len(policy.SPECS), 6)
        self.assertEqual(len({s['id'] for s in policy.SPECS}), 6)
        for item in policy.SPECS:
            with self.subTest(item=item):
                self.assertNotEqual(item['id'], 'G2')
                self.assertEqual(item, policy.normalize_spec(item))
                self.assertIsNot(item, policy.normalize_spec(item))

    def test_normalized_spec_does_not_alias_input(self):
        original = spec()
        result = policy.normalize_spec(original)
        result['id'] = 'OTHER'
        self.assertEqual(original, spec())

    def test_unknown_or_incomplete_specs_fail(self):
        bad = [None, [], {}, dict(id='X', refill='all'),
               dict(spec(), future_release=7), spec(name=''), spec(name='G2'),
               spec(refill='broadcast'), spec(retry='fixed_1'), spec(name=True)]
        for item in bad:
            with self.subTest(item=item), self.assertRaises(ValueError):
                policy.normalize_spec(item)


class InquiryRefillTests(unittest.TestCase):
    def test_one_and_all_fill_only_current_slots_in_distance_then_ID_order(self):
        snap = refill_snapshot()
        one = policy.decide_refill(spec(refill='one'), snap)
        all_ = policy.decide_refill(spec(refill='all'), snap)
        self.assertEqual(one['send'], ['C'])
        self.assertEqual(all_['send'], ['C', 'A'])
        self.assertEqual(all_['reason'], 'refill_after_received_result')
        self.assertEqual(all_['open_requests'], 1)
        snap['pending'].append(dict(hospital='OPEN2', submitted_t=13))
        for mode in ('one', 'all'):
            with self.subTest(mode=mode):
                self.assertEqual(policy.decide_refill(spec(refill=mode), snap)['send'], ['C'])

    def test_candidate_input_order_does_not_change_distance_tie_breaking(self):
        a = refill_snapshot(candidates=[dict(hospital=h, travel_minutes=0) for h in ('Z', 'A', 'B')])
        b = deepcopy(a)
        b['candidates'].reverse()
        self.assertEqual(policy.decide_refill(spec(), a), policy.decide_refill(spec(), b))
        self.assertEqual(policy.decide_refill(spec(), a)['send'], ['A', 'B'])

    def test_terminal_cooldown_and_no_event_guards(self):
        cases = [
            (dict(request_t=21), 'patient_not_started'),
            (dict(state='TRANSIT'), 'not_searching'),
            (dict(state='QUEUED'), 'not_searching'),
            (dict(state='TREATING'), 'not_searching'),
            (dict(has_valid_offer=True), 'offer_or_G2_decision_pending'),
            (dict(ready=True), 'offer_or_G2_decision_pending'),
            (dict(retry_at=21), 'retry_cooldown'),
            (dict(received_this_tick=False), 'no_received_reply_this_tick'),
            (dict(pending=[]), 'no_partial_wave_to_refill'),
            (dict(pending=[dict(hospital=h, submitted_t=10) for h in ('O1', 'O2', 'O3')]), 'concurrency_full'),
            (dict(candidates=[]), 'no_unqueried_candidate'),
        ]
        for change, reason in cases:
            with self.subTest(change=change):
                result = policy.decide_refill(spec(), refill_snapshot(**change))
                self.assertEqual((result['send'], result['reason']), ([], reason))
        self.assertTrue(policy.decide_refill(spec(), refill_snapshot(retry_at=20))['send'])

    def test_disabled_mode_still_checks_the_information_boundary(self):
        self.assertEqual(policy.decide_refill(spec(refill='none'), refill_snapshot())['reason'], 'disabled')
        hidden = refill_snapshot()
        hidden['future_resource_release'] = 30
        with self.assertRaises(ValueError):
            policy.decide_refill(spec(refill='none'), hidden)

    def test_outer_DTO_is_exact_and_has_strict_scalar_types(self):
        bad = [None, SimpleNamespace(**refill_snapshot())]
        missing = refill_snapshot()
        del missing['received_this_tick']
        bad.append(missing)
        for key, values in {
            't': [-1, True, 1.0, float('nan')],
            'request_t': [-1, True, 1.0],
            'priority': [True, 0, 3, 4.0],
            'patient': ['', None, 1],
            'state': ['', None, 1],
            'max_k': [0, 2, 4, True, 3.0],
            'retry_at': [-1, True, 1.0],
            'ready': [0, 1, 'false', None],
            'has_valid_offer': [0, 'false', None],
            'received_this_tick': [0, 'true', None],
        }.items():
            bad.extend(refill_snapshot(**{key: value}) for value in values)
        for index, value in enumerate(bad):
            with self.subTest(index=index), self.assertRaises(ValueError):
                policy.decide_refill(spec(), value)

    def test_pending_rejects_future_hidden_duplicate_and_over_cap_entries(self):
        bad = [None, (), [None], [dict(hospital='X', submitted_t=21)],
            [dict(hospital='X', submitted_t=-1)], [dict(hospital='X', submitted_t=True)],
            [dict(hospital='', submitted_t=10)], [dict(hospital=True, submitted_t=10)],
            [dict(hospital='X', submitted_t=10, at=25)],
            [dict(hospital='X', submitted_t=10, silent=True)],
            [dict(hospital='X', submitted_t=10), dict(hospital='X', submitted_t=11)],
            [dict(hospital='O'+str(i), submitted_t=10) for i in range(4)]]
        for index, rows in enumerate(bad):
            with self.subTest(index=index), self.assertRaises(ValueError):
                policy.decide_refill(spec(), refill_snapshot(pending=rows))

    def test_candidates_reject_hidden_fields_duplicates_open_hospitals_and_bad_costs(self):
        bad = [None, (), [None], [dict(hospital='OPEN', travel_minutes=1)],
            [dict(hospital='X', travel_minutes=1), dict(hospital='X', travel_minutes=2)],
            [dict(hospital='X', travel_minutes=1, resource_free=10)],
            [dict(hospital='', travel_minutes=1)],
            [dict(hospital='X', travel_minutes=x) for x in (-1,)],
            [dict(hospital='X', travel_minutes=True)],
            [dict(hospital='X', travel_minutes=1.0)],
            [dict(hospital='X', travel_minutes=float('inf'))],
            [dict(hospital='X', travel_minutes=float('nan'))]]
        for index, rows in enumerate(bad):
            with self.subTest(index=index), self.assertRaises(ValueError):
                policy.decide_refill(spec(), refill_snapshot(candidates=rows))

    def test_success_failure_and_returned_lists_do_not_mutate_inputs(self):
        cfg, snap = spec(), refill_snapshot()
        before = deepcopy((cfg, snap))
        result = policy.decide_refill(cfg, snap)
        self.assertEqual((cfg, snap), before)
        result['send'].append('CHANGED')
        self.assertEqual((cfg, snap), before)
        snap['pending'][0]['silent'] = True
        before = deepcopy((cfg, snap))
        with self.assertRaises(ValueError):
            policy.decide_refill(cfg, snap)
        self.assertEqual((cfg, snap), before)


class InquiryRetryTests(unittest.TestCase):
    def test_baseline_and_fixed_two_do_not_increase_a_shorter_baseline(self):
        for baseline in (1, 2, 3, 7):
            snap = retry_snapshot(baseline_wait=baseline)
            with self.subTest(baseline=baseline):
                normal = policy.decide_retry(spec(retry='baseline'), snap)
                fixed = policy.decide_retry(spec(retry='fixed_2'), snap)
                self.assertEqual((normal['wait_minutes'], normal['retry_at']), (baseline, 100+baseline))
                self.assertEqual(fixed['wait_minutes'], min(2, baseline))
                self.assertEqual(fixed['reason'], 'bounded_fixed_two')

    def test_history_empty_or_only_old_preserves_the_actual_baseline(self):
        for rows in ([], [observation(sent=60, observed=70)]):
            result = policy.decide_retry(spec(), retry_snapshot(rows))
            self.assertEqual((result['wait_minutes'], result['samples']), (3, 0))
            self.assertIsNone(result['observed_median_elapsed'])
            self.assertEqual(result['reason'], 'no_history_keep_baseline')

    def test_one_real_observation_is_used_without_an_undeclared_minimum_sample_gate(self):
        result = policy.decide_retry(spec(), retry_snapshot([observation(sent=99)]))
        self.assertEqual((result['wait_minutes'], result['samples'], result['observed_median_elapsed']), (1, 1, 1))

    def test_accept_decline_and_timeout_all_use_actual_observed_elapsed_times(self):
        rows = [observation('A', 99, 100, 'accept'), observation('B', 98, 100, 'decline'),
                observation('C', 94, 100, 'timeout')]
        result = policy.decide_retry(spec(), retry_snapshot(rows))
        self.assertEqual((result['wait_minutes'], result['samples'], result['observed_median_elapsed']), (2, 3, 2))
        two = policy.decide_retry(spec(), retry_snapshot([rows[0], rows[2]]))
        self.assertEqual(two['observed_median_elapsed'], 3.5)
        self.assertEqual(two['wait_minutes'], 3)

    def test_even_median_is_ceiled_and_wait_stays_positive_and_bounded(self):
        for durations, median, expected in [([1, 2], 1.5, 2), ([0], 0, 1), ([12], 12, 3)]:
            rows = [observation(str(i), 100-d, 100) for i, d in enumerate(durations)]
            with self.subTest(durations=durations):
                result = policy.decide_retry(spec(), retry_snapshot(rows))
                self.assertEqual(result['observed_median_elapsed'], median)
                self.assertEqual(result['wait_minutes'], expected)
                for baseline in (1, 2, 3):
                    limited = policy.decide_retry(spec(), retry_snapshot(rows, baseline_wait=baseline))
                    self.assertGreaterEqual(limited['wait_minutes'], 1)
                    self.assertLessEqual(limited['wait_minutes'], min(3, baseline))

    def test_window_is_open_at_t_minus_thirty_and_closed_at_now(self):
        rows = [observation('OLD', 60, 70), observation('IN', 70, 71), observation('NOW', 97, 100)]
        result = policy.decide_retry(spec(), retry_snapshot(rows))
        self.assertEqual(result['samples'], 2)
        self.assertEqual(result['observed_median_elapsed'], 2)
        self.assertEqual((result['history_window'], result['history_limit']), (30, 6))

    def test_only_latest_six_observations_count_and_input_order_is_irrelevant(self):
        rows = [observation('EXCLUDED', 80, 91)]
        rows += [observation('H'+str(i), 91+i, 92+i) for i in range(6)]
        a = policy.decide_retry(spec(), retry_snapshot(rows))
        b = policy.decide_retry(spec(), retry_snapshot(list(reversed(rows))))
        self.assertEqual(a, b)
        self.assertEqual((a['samples'], a['observed_median_elapsed'], a['wait_minutes']), (6, 1, 1))

    def test_same_hospital_later_attempt_is_valid_but_duplicate_submission_is_not(self):
        a = observation('H1', 95, 96)
        later = observation('H1', 98, 100)
        result = policy.decide_retry(spec(), retry_snapshot([a, later]))
        self.assertEqual(result['samples'], 2)
        for second in (deepcopy(a), dict(a, observed_t=97), dict(a, outcome='timeout')):
            with self.subTest(second=second), self.assertRaises(ValueError):
                policy.decide_retry(spec(), retry_snapshot([a, second]))

    def test_retry_header_and_observation_schema_are_exact(self):
        bad = [None, SimpleNamespace(**retry_snapshot()), dict(retry_snapshot(), future_capacity=9)]
        missing = retry_snapshot()
        del missing['observations']
        bad.append(missing)
        for key, values in {'t': [True, -1, 1.0], 'priority': [True, 3, 4.0],
                            'patient': ['', None, 7], 'baseline_wait': [0, -1, True, 2.0],
                            'observations': [None, (), [None]]}.items():
            bad.extend(dict(retry_snapshot(), **{key: value}) for value in values)
        for index, value in enumerate(bad):
            with self.subTest(index=index), self.assertRaises(ValueError):
                policy.decide_retry(spec(), value)

    def test_future_reversed_mixed_patient_unknown_and_private_observations_fail(self):
        bad = [observation(sent=101, observed=102), observation(sent=100, observed=99),
            observation(sent=-1), observation(observed=-1), observation(sent=True), observation(observed=True),
            observation(sent=95.0), observation(observed=100.0), observation(patient='OTHER'),
            observation(hospital=''), observation(outcome='reply'), observation(outcome='future_accept'),
            dict(observation(), due_t=105), dict(observation(), silent=False),
            dict(observation(), treatment_finishes_at=110)]
        for index, row in enumerate(bad):
            with self.subTest(index=index), self.assertRaises(ValueError):
                policy.decide_retry(spec(), retry_snapshot([row]))

    def test_out_of_window_records_are_not_a_schema_validation_bypass(self):
        old = observation(sent=0, observed=1)
        old['finish_t'] = 10
        with self.assertRaises(ValueError):
            policy.decide_retry(spec(), retry_snapshot([old]))

    def test_history_keeps_a_response_cadence_label_and_input_immutability(self):
        cfg, snap = spec(), retry_snapshot([observation('B', 98), observation('A', 99)])
        before = deepcopy((cfg, snap))
        result = policy.decide_retry(cfg, snap)
        self.assertEqual((cfg, snap), before)
        self.assertFalse(result['acceptance_or_resource_release_prediction'])
        self.assertEqual(result['reason'], 'observed_response_cadence')
        snap['observations'][0]['observed_t'] = 101
        before = deepcopy((cfg, snap))
        with self.assertRaises(ValueError):
            policy.decide_retry(cfg, snap)
        self.assertEqual((cfg, snap), before)


class InquiryObservationAdapterTests(unittest.TestCase):
    def test_refill_projection_never_reads_private_scheduled_ask_fields(self):
        from simulation.inquiry_preserving_replay_20261029 import InquiryPreservingReplay

        class Ask(dict):
            def __getitem__(self, key):
                if key != 'hid':
                    raise AssertionError('hidden scheduled ask field read: '+key)
                return super().__getitem__(key)

        engine = object.__new__(InquiryPreservingReplay)
        p = SimpleNamespace(id='P1', priority=2, t_req=5, state='SEARCH', asks=[Ask(hid='OPEN')],
                            tried={'TRIED'}, valid={}, retry_at=None)
        engine.position = lambda patient, t: (0, 0)
        engine.candidates = lambda patient, loc, exclude: ['OPEN', 'TRIED', 'NEW']
        engine.travel = lambda patient, hid, loc: 7
        engine.failed_hospitals = {'P1': set()}
        engine._latest_submission = {('P1', 'OPEN'): dict(submitted_t=10)}
        engine._received_at = {'P1': 20}
        engine.ready = []
        snap = engine._refill_snapshot(p, 20)
        self.assertEqual(set(snap), policy.REFILL_KEYS)
        self.assertEqual(snap['pending'], [dict(hospital='OPEN', submitted_t=10)])
        self.assertEqual(snap['candidates'], [dict(hospital='NEW', travel_minutes=7)])
        self.assertEqual(policy.decide_refill(spec(), snap)['send'], ['NEW'])

    def test_retry_projection_is_same_patient_received_history_and_is_detached(self):
        from simulation.inquiry_preserving_replay_20261029 import InquiryPreservingReplay
        engine = object.__new__(InquiryPreservingReplay)
        engine.cfg = dict(retry_wait=3)
        engine.received_ledger = [observation('GOOD', 18, 19),
                                  observation('FUTURE', 20, 21),
                                  observation('OTHER', 18, 19, patient='OTHER')]
        p = SimpleNamespace(id='P1', priority=4)
        snap = engine._retry_snapshot(p, 20)
        self.assertEqual(snap['observations'], [observation('GOOD', 18, 19)])
        self.assertEqual(set(snap), policy.RETRY_KEYS)
        snap['observations'][0]['hospital'] = 'MUTATED'
        self.assertEqual(engine.received_ledger[0]['hospital'], 'GOOD')
        self.assertEqual(engine.cfg, dict(retry_wait=3))


if __name__ == '__main__':
    unittest.main()
