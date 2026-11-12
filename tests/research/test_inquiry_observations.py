"""최종 결정 관측과 진행 단계의 분리. 임시 DB만 사용한다."""
import json

from server import inquiry_observations, inquiry_policies, service
from test_server import Base, T0


class DecisionObservations(Base):
    def send(self, pid='A', hid='H1', now=T0):
        self.patient(pid, now=now)
        service.send_inquiries(self.conn, now, pid, [hid])
        return self.inquiry_id(pid, hid)

    def capture(self, now=T0 + 100, **kwargs):
        return inquiry_observations.capture(self.conn, now, 'A', **kwargs)

    def test_checking_is_stage_not_final_decision_and_pending_remains(self):
        iid = self.send()
        service.respond(self.conn, T0 + 5, iid, 'checking', 'nurse')
        captured = self.capture()
        self.assertEqual(captured['schema_version'], 2)
        self.assertEqual(captured['observation_basis'], 'final_decision')
        self.assertEqual(captured['observations'], [])
        self.assertEqual(captured['patient_pending'], captured['pending'])
        self.assertEqual(len(captured['pending']), 1)
        attempt = captured['attempts'][0]
        self.assertEqual(attempt['first_reply_at'], T0 + 5)
        self.assertEqual(attempt['first_checking_at'], T0 + 5)
        self.assertIsNone(attempt['decision_at'])
        self.assertTrue(attempt['unresolved'])

    def test_all_questions_and_final_accept_are_preserved_without_content(self):
        iid = self.send()
        service.respond(self.conn, T0 + 5, iid, 'checking', 'PRIVATE-NURSE')
        service.respond(self.conn, T0 + 10, iid, 'question', 'PRIVATE-NURSE', question='PRIVATE-QUESTION-1')
        service.answer_question(self.conn, T0 + 11, 'A', iid, 'PRIVATE-ANSWER')
        service.respond(self.conn, T0 + 20, iid, 'question', 'PRIVATE-NURSE', question='PRIVATE-QUESTION-2')
        service.respond(self.conn, T0 + 90, iid, 'accept', 'PRIVATE-NURSE')
        before = self.conn.total_changes
        result = self.capture()
        self.assertEqual(self.conn.total_changes, before)
        self.assertEqual(result['observations'], [dict(hospital='H1', patient='A', submitted_at=T0,
                                                     observed_at=T0 + 90, outcome='accept')])
        attempt = result['attempts'][0]
        self.assertEqual(attempt['decision'], 'accept')
        self.assertEqual(attempt['decision_at'], T0 + 90)
        self.assertEqual(attempt['first_question_at'], T0 + 10)
        self.assertEqual([(x['stage'], x['at']) for x in attempt['stages']],
                         [('checking', T0 + 5), ('question', T0 + 10),
                          ('question', T0 + 20), ('accept', T0 + 90)])
        self.assertEqual(result['pending'], [])
        self.assertNotIn('PRIVATE-', json.dumps(result))

    def test_final_decline_after_question_uses_decision_time(self):
        iid = self.send()
        service.respond(self.conn, T0 + 2, iid, 'question', 'nurse', question='more data')
        service.respond(self.conn, T0 + 60, iid, 'decline', 'nurse', reason='병상')
        result = self.capture()
        self.assertEqual(result['observations'][0]['outcome'], 'decline')
        self.assertEqual(result['observations'][0]['observed_at'], T0 + 60)
        self.assertEqual(result['attempts'][0]['first_reply_at'], T0 + 2)

    def test_checking_then_no_decision_times_out_at_exact_deadline(self):
        iid = self.send()
        service.respond(self.conn, T0 + 1, iid, 'checking', 'nurse')
        self.assertEqual(self.capture(T0 + 179)['observations'], [])
        result = self.capture(T0 + 180)
        self.assertEqual(result['observations'][0]['outcome'], 'timeout')
        self.assertEqual(result['observations'][0]['observed_at'], T0 + 180)
        self.assertEqual(len(result['pending']), 1)
        self.assertEqual(self.conn.execute('SELECT state FROM inquiries WHERE id=?', (iid,)).fetchone()[0],
                         'CHECKING')

    def test_exact_deadline_accept_is_a_decision_not_timeout(self):
        iid = self.send()
        service.respond(self.conn, T0 + 2, iid, 'checking', 'nurse')
        service.respond(self.conn, T0 + 180, iid, 'accept', 'nurse')
        self.assertEqual(self.capture(T0 + 180)['observations'][0]['outcome'], 'accept')

    def test_late_actual_accept_preserves_timeout_and_decision_stage(self):
        iid = self.send()
        service.respond(self.conn, T0 + 2, iid, 'checking', 'nurse')
        before = self.capture(T0 + 180)['observations']
        service.respond(self.conn, T0 + 181, iid, 'accept', 'nurse')
        after = self.capture(T0 + 190)
        self.assertEqual(after['observations'], before)
        self.assertEqual(after['attempts'][0]['decision_at'], T0 + 181)
        self.assertEqual(after['attempts'][0]['decision'], 'accept')
        self.assertEqual(after['pending'], [])
        self.assertEqual(self.conn.execute('SELECT state FROM inquiries WHERE id=?', (iid,)).fetchone()[0],
                         'ACCEPTED')

    def test_retry_keeps_separate_attempts_and_censors_old_checking(self):
        iid = self.send()
        service.respond(self.conn, T0 + 10, iid, 'checking', 'nurse')
        service.retry_inquiry(self.conn, T0 + 120, 'A', iid)
        service.respond(self.conn, T0 + 150, iid, 'accept', 'nurse')
        result = self.capture(T0 + 160)
        self.assertEqual(len(result['observations']), 1)
        self.assertEqual(result['observations'][0]['submitted_at'], T0 + 120)
        self.assertEqual(len(result['attempts']), 2)
        first, second = result['attempts']
        self.assertEqual(first['end_reason'], 'retried')
        self.assertFalse(first['unresolved'])
        self.assertIsNone(first['decision'])
        self.assertNotEqual(first['attempt_event_id'], second['attempt_event_id'])
        self.assertEqual(first['inquiry_id'], second['inquiry_id'])

    def test_retry_after_timeout_has_one_sample_per_attempt(self):
        iid = self.send()
        service.respond(self.conn, T0 + 10, iid, 'checking', 'nurse')
        service.retry_inquiry(self.conn, T0 + 200, 'A', iid)
        service.respond(self.conn, T0 + 250, iid, 'decline', 'nurse', reason='병상')
        result = self.capture(T0 + 260)
        self.assertEqual([(x['submitted_at'], x['observed_at'], x['outcome']) for x in result['observations']],
                         [(T0, T0 + 180, 'timeout'), (T0 + 200, T0 + 250, 'decline')])

    def test_cancel_before_timeout_is_censored_and_no_longer_pending(self):
        iid = self.send()
        service.respond(self.conn, T0 + 5, iid, 'checking', 'nurse')
        service.cancel_inquiry(self.conn, T0 + 40, 'A', iid, '기타')
        result = self.capture(T0 + 250)
        self.assertEqual(result['observations'], [])
        self.assertEqual(result['pending'], [])
        self.assertEqual(result['attempts'][0]['end_reason'], 'cancelled')
        self.assertEqual(len(result['attempts'][0]['stages']), 1)

    def test_cancel_at_timeout_retains_timeout(self):
        iid = self.send()
        service.cancel_inquiry(self.conn, T0 + 180, 'A', iid, '기타')
        self.assertEqual(self.capture(T0 + 200)['observations'][0]['outcome'], 'timeout')

    def test_future_events_are_filtered_before_attempt_or_stage_changes(self):
        iid = self.send()
        service.respond(self.conn, T0 + 10, iid, 'checking', 'nurse')
        service.respond(self.conn, T0 + 200, iid, 'accept', 'nurse')
        result = self.capture(T0 + 100)
        self.assertEqual(result['observations'], [])
        self.assertTrue(result['attempts'][0]['unresolved'])
        self.assertEqual([x['stage'] for x in result['attempts'][0]['stages']], ['checking'])
        self.assertEqual(len(result['pending']), 1)

    def test_same_second_events_keep_event_order_and_duplicate_final_does_not_count_twice(self):
        iid = self.send()
        service.respond(self.conn, T0 + 5, iid, 'checking', 'nurse')
        service.respond(self.conn, T0 + 5, iid, 'question', 'nurse', question='data')
        service.respond(self.conn, T0 + 5, iid, 'accept', 'nurse')
        service.respond(self.conn, T0 + 6, iid, 'accept', 'nurse')
        result = self.capture()
        self.assertEqual(len(result['observations']), 1)
        stages = result['attempts'][0]['stages']
        self.assertEqual([x['stage'] for x in stages], ['checking', 'question', 'accept'])
        self.assertEqual([x['event_id'] for x in stages], sorted(x['event_id'] for x in stages))

    def test_mismatched_inquiry_id_and_invalid_actions_do_not_create_stages(self):
        iid = self.send()
        service.log(self.conn, T0 + 1, 'nurse', 'inquiry_responded', 'A', 'H1',
                    inquiry_id=iid + 100, action='accept')
        service.log(self.conn, T0 + 2, 'nurse', 'inquiry_responded', 'A', 'H1',
                    inquiry_id=iid, action='unknown')
        result = self.capture()
        self.assertEqual(result['observations'], [])
        self.assertEqual(result['attempts'][0]['stages'], [])
        self.assertTrue(result['attempts'][0]['unresolved'])

    def test_exact_lower_window_boundary_is_not_included(self):
        iid = self.send()
        service.respond(self.conn, T0 + 50, iid, 'accept', 'nurse')
        result = self.capture(T0 + 1850)
        self.assertEqual(result['observations'], [])
        self.assertEqual(result['attempts'], [])

    def test_old_unresolved_attempt_remains_pending(self):
        iid = self.send()
        service.respond(self.conn, T0 + 1, iid, 'checking', 'nurse')
        result = self.capture(T0 + 4000)
        self.assertEqual(result['observations'], [])
        self.assertEqual(len(result['attempts']), 1)
        self.assertEqual(len(result['pending']), 1)

    def test_input_validation_does_not_write(self):
        before = self.conn.total_changes
        for values in ({'now': True}, {'now': -1}, {'timeout_seconds': 0}, {'window_seconds': 1.5}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                args = dict(now=T0, pid='A', **{k: v for k, v in values.items() if k != 'now'})
                args['now'] = values.get('now', T0)
                inquiry_observations.capture(self.conn, **args)
        self.assertEqual(self.conn.total_changes, before)


class FinalDecisionPolicyInput(Base):
    def test_legacy_reply_is_not_a_final_decision_sample(self):
        candidates = [dict(hospital_id='H1', travel={'duration_seconds': 60}),
                      dict(hospital_id='H2', travel={'duration_seconds': 70})]
        history = [dict(hospital='H2', submitted_at=T0 - 100, observed_at=T0 - 99,
                        outcome='reply') for _ in range(2)]
        for name in ('observed_v2', 'observed_v3'):
            with self.subTest(name=name):
                args = dict(now=T0, candidates=candidates, pending=[], patient_pending=[], max_new=3)
                base = inquiry_policies.decide(name, **args)
                result = inquiry_policies.decide(name, observations=history, **args)
                self.assertEqual(base, result)
                self.assertEqual(result['send'], ['H1'])
                self.assertEqual(result['evidence']['global_reply_samples'], 0)

    def test_only_observed_policies_bump_contract_version(self):
        for name in ('fixed_1', 'fixed_3', 'fixed_5'):
            self.assertEqual(inquiry_policies.spec(name)['version'], '1')
        for name in ('observed_v2', 'observed_v3', *inquiry_policies.V3_EXPERIMENT_NAMES):
            self.assertEqual(inquiry_policies.spec(name)['version'], '2')


if __name__ == '__main__':
    import unittest
    unittest.main()
