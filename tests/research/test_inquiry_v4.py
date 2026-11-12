"""V4 후보의 관측 경계와 V3 보존. 합성 초 단위 입력만 사용한다."""
import copy
import json
import unittest
from unittest.mock import patch

from server import inquiry_policies, inquiry_v4


NOW = 1_800_000_000


def candidate(hospital, seconds):
    return dict(hospital_id=hospital, travel=dict(duration_seconds=seconds))


def own(hospital='H1', submitted=NOW, travel=300, patient='own-patient'):
    return dict(hospital=hospital, patient=patient, submitted_at=submitted,
                travel_seconds=travel)


def arguments(age=30, own_rows=None, **updates):
    own_rows = [own()] if own_rows is None else own_rows
    result = dict(now=NOW + age, candidates=[candidate('H2', 360)], observations=[],
                  patient_pending=copy.deepcopy(own_rows),
                  pending=[{k: row[k] for k in ('hospital', 'patient', 'submitted_at')}
                           for row in own_rows], max_new=10)
    result.update(updates)
    return result


def history(hospital, count, now=NOW, delay=10):
    return [dict(hospital=hospital, submitted_at=now - 300 - index,
                 observed_at=now - 300 - index + delay, outcome='decline')
            for index in range(count)]


class LowLoadSupplement(unittest.TestCase):
    def test_spec_is_fixed_private_and_returns_independent_configuration(self):
        value = inquiry_v4.spec()
        self.assertEqual(value['name'], 'observed_v4')
        self.assertEqual(value['source_policy'], 'v3_w45_h900')
        self.assertTrue(value['research_only'])
        self.assertEqual(value['config']['cold_start_wait_seconds'], 30)
        self.assertEqual(value['config']['cold_start_travel_margin_seconds'], 120)
        self.assertEqual(value['config']['max_wait_seconds'], 45)
        self.assertEqual(value['review_seconds'], 15)
        self.assertEqual((value['config']['max_concurrent'], value['max_contacts'],
                          value['deadline_seconds']), (3, 10, 300))
        value['config']['cold_start_wait_seconds'] = 1
        self.assertEqual(inquiry_v4.spec()['config']['cold_start_wait_seconds'], 30)
        self.assertNotIn('observed_v4', inquiry_policies.POLICY_NAMES)
        with self.assertRaises(ValueError):
            inquiry_policies.spec('observed_v4')

    def test_actual_v3_function_is_called_with_explicit_frozen_base(self):
        original = inquiry_policies.decide
        with patch.object(inquiry_policies, 'decide', wraps=original) as spy:
            inquiry_v4.decide(**arguments())
        self.assertEqual(spy.call_count, 1)
        self.assertEqual(spy.call_args.args, ('v3_w45_h900',))

    def test_initial_inquiry_is_still_exactly_original_v3_first_one(self):
        args = arguments(0, own_rows=[], candidates=[candidate('H1', 300), candidate('H2', 360)])
        base = inquiry_policies.decide(inquiry_v4.BASE_POLICY, **args)
        result = inquiry_v4.decide(**args)
        self.assertEqual(result['send'], base['send'])
        self.assertEqual(result['reason'], base['reason'])
        self.assertEqual(result['send'], ['H1'])
        self.assertFalse(result['evidence']['cold_start_supplement']['applied'])

    def test_thirty_second_boundary_adds_one_but_v3_still_waits(self):
        before = inquiry_v4.decide(**arguments(29))
        exact = inquiry_v4.decide(**arguments(30))
        base = inquiry_policies.decide(inquiry_v4.BASE_POLICY, **arguments(30))
        self.assertEqual(before['send'], [])
        self.assertEqual(base['send'], [])
        self.assertEqual(exact['send'], ['H2'])
        self.assertEqual(exact['reason'], 'cold_start_low_load_expansion')
        self.assertEqual(exact['next_review_at'], NOW + 45)
        self.assertTrue(all(exact['evidence']['cold_start_supplement']['checks'].values()))

    def test_second_backup_uses_latest_transmission_thirty_seconds(self):
        rows = [own(), own('H2', NOW + 30, 360)]
        before = inquiry_v4.decide(**arguments(59, rows, candidates=[candidate('H3', 420)]))
        exact = inquiry_v4.decide(**arguments(60, rows, candidates=[candidate('H3', 420)]))
        self.assertEqual(before['send'], [])
        self.assertEqual(exact['send'], ['H3'])
        self.assertEqual(exact['evidence']['last_contact_age_seconds'], 30)

    def test_travel_margin_is_measured_against_shortest_waiting_destination(self):
        exact = inquiry_v4.decide(**arguments(candidates=[candidate('H2', 420)]))
        distant = inquiry_v4.decide(**arguments(candidates=[candidate('H2', 421)]))
        shorter = inquiry_v4.decide(**arguments(candidates=[candidate('H2', 200)]))
        self.assertEqual(exact['send'], ['H2'])
        self.assertEqual(exact['evidence']['cold_start_supplement']['additional_travel_seconds'], 120)
        self.assertEqual(distant['send'], [])
        self.assertEqual(shorter['send'], ['H2'])

    def test_known_other_patient_backlog_disables_supplement(self):
        args = arguments()
        args['pending'].append(dict(hospital='H9', patient='other', submitted_at=NOW))
        result = inquiry_v4.decide(**args)
        self.assertEqual(result['send'], [])
        detail = result['evidence']['cold_start_supplement']
        self.assertEqual(detail['other_patient_pending_count'], 1)
        self.assertFalse(detail['checks']['no_other_patient_pending'])

    def test_old_other_patient_pending_stays_global_workload(self):
        args = arguments()
        args['pending'].append(dict(hospital='H9', patient='other', submitted_at=NOW - 200))
        result = inquiry_v4.decide(**args)
        self.assertEqual(result['send'], [])
        self.assertEqual(result['evidence']['cold_start_supplement']['other_patient_pending_count'], 1)

    def test_candidate_current_pending_disables_supplement(self):
        args = arguments()
        args['pending'].append(dict(hospital='H2', patient='other', submitted_at=NOW))
        result = inquiry_v4.decide(**args)
        self.assertEqual(result['send'], [])
        detail = result['evidence']['cold_start_supplement']
        self.assertEqual(detail['candidate_unresolved_inquiries'], 1)
        self.assertFalse(detail['checks']['candidate_unloaded'])

    def test_two_waiting_hospital_reply_samples_disable_supplement(self):
        args = arguments(observations=history('H1', 2, delay=150))
        result = inquiry_v4.decide(**args)
        self.assertEqual(result['send'], [])
        self.assertFalse(result['evidence']['cold_start_supplement']['checks']['waiting_reply_samples_low'])

    def test_two_candidate_reply_samples_disable_supplement(self):
        result = inquiry_v4.decide(**arguments(observations=history('H2', 2, delay=150)))
        self.assertEqual(result['send'], [])
        self.assertFalse(result['evidence']['cold_start_supplement']['checks']['candidate_reply_samples_low'])

    def test_one_reply_sample_remains_below_fixed_threshold(self):
        rows = history('H1', 1, delay=150) + history('H2', 1, delay=150)
        result = inquiry_v4.decide(**arguments(observations=rows))
        self.assertEqual(result['send'], ['H2'])
        self.assertEqual(result['evidence']['cold_start_supplement']['waiting_reply_samples'], [1])

    def test_three_waiting_destinations_preserve_original_concurrency_cap(self):
        rows = [own(), own('H2', NOW + 5, 360), own('H3', NOW + 10, 420)]
        result = inquiry_v4.decide(**arguments(40, rows, candidates=[candidate('H4', 420)]))
        self.assertEqual(result['send'], [])
        self.assertEqual(result['reason'], 'concurrency_cap')

    def test_no_budget_and_no_candidates_never_create_inquiry(self):
        no_budget = inquiry_v4.decide(**arguments(max_new=0))
        no_candidates = inquiry_v4.decide(**arguments(candidates=[]))
        self.assertEqual(no_budget['send'], [])
        self.assertEqual(no_budget['reason'], 'contact_budget')
        self.assertEqual(no_candidates['send'], [])
        self.assertEqual(no_candidates['reason'], 'no_candidates')

    def test_original_forty_five_second_defense_still_sends_distant_candidate(self):
        args = arguments(45, candidates=[candidate('H2', 6000)])
        result = inquiry_v4.decide(**args)
        base = inquiry_policies.decide(inquiry_v4.BASE_POLICY, **args)
        self.assertEqual(result['send'], ['H2'])
        self.assertEqual(result['reason'], 'bounded_wait_expansion')
        self.assertEqual(result['send'], base['send'])
        self.assertFalse(result['evidence']['cold_start_supplement']['applied'])

    def test_original_better_candidate_hedge_is_preserved(self):
        args = arguments(120, own_rows=[own(travel=6000)], candidates=[candidate('H2', 300)])
        result = inquiry_v4.decide(**args)
        base = inquiry_policies.decide(inquiry_v4.BASE_POLICY, **args)
        self.assertEqual(result['send'], base['send'])
        self.assertEqual(result['reason'], base['reason'])
        self.assertEqual(result['reason'], 'hedge_better_candidate')

    def test_closed_recent_transmission_restarts_thirty_second_spacing(self):
        result = inquiry_v4.decide(**arguments(30, last_contact_at=NOW + 15))
        due = inquiry_v4.decide(**arguments(45, last_contact_at=NOW + 15))
        self.assertEqual(result['send'], [])
        self.assertEqual(result['evidence']['last_contact_age_seconds'], 15)
        self.assertEqual(due['send'], ['H2'])

    def test_future_last_contact_is_ignored_by_actual_v3_validation(self):
        expected = inquiry_v4.decide(**arguments())
        result = inquiry_v4.decide(**arguments(last_contact_at=NOW + 31))
        self.assertEqual(result, expected)

    def test_exact_observation_timeout_remains_open_and_expiry_is_not_decline(self):
        rows = [own(submitted=NOW - 150)]
        exact = inquiry_v4.decide(**arguments(30, rows, candidates=[candidate('H2', 6000)]))
        expired = inquiry_v4.decide(**arguments(31, rows, candidates=[candidate('H2', 6000)]))
        self.assertEqual(exact['reason'], 'bounded_wait_expansion')
        self.assertEqual(exact['evidence']['patient_pending_count'], 1)
        self.assertEqual(expired['reason'], 'initial_best')
        self.assertEqual(expired['evidence']['patient_pending_count'], 0)
        self.assertEqual(expired['evidence']['unresolved_inquiry_count'], 1)
        self.assertEqual(rows[0]['submitted_at'], NOW - 150)

    def test_duplicate_ledgers_are_deduplicated_and_inputs_are_unchanged(self):
        args = arguments()
        args['patient_pending'][0]['symptoms'] = 'SECRET-CLINICAL-TEXT'
        before = copy.deepcopy(args)
        expected = inquiry_v4.decide(**args)
        self.assertEqual(args, before)
        duplicate = copy.deepcopy(args)
        duplicate['pending'] *= 3
        duplicate['patient_pending'] *= 2
        self.assertEqual(inquiry_v4.decide(**duplicate), expected)
        encoded = json.dumps(expected)
        self.assertNotIn('own-patient', encoded)
        self.assertNotIn('SECRET-CLINICAL-TEXT', encoded)

    def test_iterable_inputs_are_supported_without_dropping_pending_rows(self):
        args = arguments()
        expected = inquiry_v4.decide(**args)
        generated = {key: (iter(value) if key in ('candidates', 'observations', 'pending',
                                                 'patient_pending') else value)
                     for key, value in args.items()}
        self.assertEqual(inquiry_v4.decide(**generated), expected)

    def test_future_and_old_observation_payloads_are_not_interpreted(self):
        class FuturePayload(dict):
            def get(self, key, *args):
                if key in ('hospital', 'outcome'):
                    raise AssertionError('future/old observation payload read')
                return super().get(key, *args)

        args = arguments()
        expected = inquiry_v4.decide(**args)
        args['observations'] = [FuturePayload(submitted_at=NOW + 31, observed_at=NOW + 32),
                                FuturePayload(submitted_at=NOW - 1000, observed_at=NOW - 900)]
        result = inquiry_v4.decide(**args)
        self.assertEqual(result['send'], expected['send'])
        self.assertEqual(result['evidence']['history_used_count'], 0)
        self.assertEqual(result['evidence']['history_excluded_count'], 1)

    def test_future_pending_payload_is_ignored_before_patient_and_hospital(self):
        class FuturePending(dict):
            def get(self, key, *args):
                if key != 'submitted_at':
                    raise AssertionError('future pending payload read')
                return super().get(key, *args)

        args = arguments()
        expected = inquiry_v4.decide(**args)
        args['pending'].append(FuturePending(submitted_at=NOW + 31))
        args['patient_pending'].append(FuturePending(submitted_at=NOW + 31))
        self.assertEqual(inquiry_v4.decide(**args), expected)

    def test_future_or_oracle_extras_do_not_change_policy(self):
        args = arguments()
        expected = inquiry_v4.decide(**args)
        args['candidates'][0].update(no_response=True, service_seconds=1, future_capacity=0)
        args['pending'][0].update(reply_due_at=NOW + 31, accept_next=True)
        args['patient_pending'][0].update(reply_due_at=NOW + 31, no_response=True)
        self.assertEqual(inquiry_v4.decide(**args), expected)

    def test_original_validation_errors_are_not_hidden(self):
        bad_values = [dict(now=True), dict(max_new=True), dict(last_contact_at=-1),
                      dict(candidates=[candidate('H2', -1)]),
                      dict(candidates=[candidate('H2', 300), candidate('H2', 301)]),
                      dict(pending=[])]
        for update in bad_values:
            with self.subTest(update=update), self.assertRaises(ValueError):
                inquiry_v4.decide(**arguments(**update))

    def test_multiple_own_patient_ids_remain_invalid(self):
        rows = [own(), own('H9', patient='another-patient')]
        with self.assertRaises(ValueError):
            inquiry_v4.decide(**arguments(30, rows))

    def test_supplement_keeps_original_candidate_ranking(self):
        args = arguments(candidates=[candidate('H3', 420), candidate('H2', 360)])
        base = inquiry_policies.decide(inquiry_v4.BASE_POLICY, **args)
        result = inquiry_v4.decide(**args)
        self.assertEqual(result['send'], [base['evidence']['candidate_scores'][0]['hospital']])
        self.assertEqual(result['evidence']['candidate_scores'], base['evidence']['candidate_scores'])


if __name__ == '__main__':
    unittest.main()
