"""중요 사건 전달·사용자 확인: 가상 DB만 사용하며 업무 상태를 바꾸지 않는다."""
import json
import threading

from server import db, important_events as events, service
from server.service import ServiceError
from test_server import Base, EST, T0


class ImportantEventTests(Base):
    def setUp(self):
        super().setUp()
        self.conn.executescript(events.SCHEMA)
        self.patient()
        service.send_inquiries(self.conn, T0 + 10, 'A', ['H1', 'H2'])

    def question(self, hid='H1', now=T0 + 20):
        service.respond(self.conn, now, self.inquiry_id('A', hid), 'question', 'nurse',
                        question='민감한 질문 원문')
        return self.conn.execute('SELECT MAX(id) FROM events').fetchone()[0]

    def listing(self, now=T0 + 100, **kw):
        return events.list_events(self.conn, now, 'A', actor_id='unit-1', **kw)

    def error(self, code, fn, *args, **kw):
        with self.assertRaises(ServiceError) as cm:
            fn(*args, **kw)
        self.assertEqual(cm.exception.code, code)

    def test_get_does_not_create_receipts_events_or_change_planning(self):
        eid = self.question()
        before = self.conn.execute('SELECT COUNT(*) FROM events').fetchone()[0]
        revision = self.conn.execute('SELECT revision FROM planning_state').fetchone()[0]
        result = self.listing()
        self.assertEqual([item['event_id'] for item in result['items']], [eid])
        self.assertIsNone(result['items'][0]['delivered_at'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM important_event_receipts').fetchone()[0], 0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM events').fetchone()[0], before)
        self.assertEqual(self.conn.execute('SELECT revision FROM planning_state').fetchone()[0], revision)

    def test_explicit_delivery_acknowledgement_and_idempotent_retries(self):
        eid = self.question()
        result = events.mark_delivered(self.conn, T0 + 30, 'A', eid, actor_id='unit-1')
        self.assertFalse(result['duplicate'])
        self.assertEqual(result['event']['delivered_at'], T0 + 30)
        self.assertEqual(result['event']['delivery_delay_seconds'], 10)
        again = events.mark_delivered(self.conn, T0 + 40, 'A', eid, actor_id='unit-1')
        self.assertTrue(again['duplicate'])
        self.assertEqual(again['event']['delivered_at'], T0 + 30)
        confirmed = events.acknowledge(self.conn, T0 + 50, 'A', eid, actor_id='unit-1')
        self.assertFalse(confirmed['duplicate'])
        self.assertEqual(confirmed['event']['acknowledgement_delay_seconds'], 30)
        again = events.acknowledge(self.conn, T0 + 60, 'A', eid, actor_id='unit-1')
        self.assertTrue(again['duplicate'])
        self.assertEqual(again['event']['acknowledged_at'], T0 + 50)

    def test_ack_requires_delivery_and_is_independent_for_every_recipient(self):
        eid = self.question()
        self.error('DELIVERY_REQUIRED', events.acknowledge, self.conn, T0 + 30, 'A', eid, actor_id='unit-1')
        events.mark_delivered(self.conn, T0 + 30, 'A', eid, actor_id='unit-1')
        events.acknowledge(self.conn, T0 + 40, 'A', eid, actor_id='unit-1')
        other = events.list_events(self.conn, T0 + 50, 'A', actor_id='unit-2')['items'][0]
        hospital = self.listing(hospital_id='H1')['items'][0]
        self.assertIsNone(other['delivered_at'])
        self.assertIsNone(hospital['delivered_at'])
        self.error('DELIVERY_REQUIRED', events.acknowledge, self.conn, T0 + 50, 'A', eid,
                   actor_id='unit-1', hospital_id='H1')

    def test_receipts_do_not_acknowledge_medical_condition_or_change_destination(self):
        iid = self.inquiry_id('A', 'H1')
        service.respond(self.conn, T0 + 20, iid, 'accept', 'nurse')
        service.confirm_destination(self.conn, T0 + 30, 'A', iid)
        eid = service.log(self.conn, T0 + 40, 'system', 'transport_recheck_required', 'A', 'H1',
                          risk_signature='risk-signature')
        before = tuple(self.conn.execute("SELECT * FROM patients WHERE id='A'").fetchone())
        before_review = self.conn.execute("SELECT * FROM transport_reviews WHERE patient_id='A'").fetchone()
        revision = self.conn.execute('SELECT revision FROM planning_state').fetchone()[0]
        event_count = self.conn.execute('SELECT COUNT(*) FROM events').fetchone()[0]
        events.mark_delivered(self.conn, T0 + 50, 'A', eid, actor_id='nurse', hospital_id='H1')
        events.acknowledge(self.conn, T0 + 60, 'A', eid, actor_id='nurse', hospital_id='H1')
        self.assertEqual(tuple(self.conn.execute("SELECT * FROM patients WHERE id='A'").fetchone()), before)
        self.assertEqual(self.conn.execute("SELECT * FROM transport_reviews WHERE patient_id='A'").fetchone(), before_review)
        self.assertEqual(self.conn.execute('SELECT revision FROM planning_state').fetchone()[0], revision)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM events').fetchone()[0], event_count)

    def test_preconfirmation_hospital_sees_own_inquiry_only_and_no_raw_data(self):
        first, second = self.question(), self.question('H2', T0 + 21)
        result = self.listing(hospital_id='H1')
        self.assertEqual([item['event_id'] for item in result['items']], [first])
        self.assertNotIn('민감한 질문 원문', json.dumps(result, ensure_ascii=False))
        self.assertNotIn('data', result['items'][0])
        self.error('NOT_FOUND', events.mark_delivered, self.conn, T0 + 50, 'A', second,
                   actor_id='nurse', hospital_id='H1')

    def test_hospital_relation_check_does_not_disclose_patient_existence(self):
        for pid in ('A', 'missing-patient'):
            with self.subTest(pid=pid):
                self.error('NOT_PARTICIPANT', events.list_events, self.conn, T0 + 50, pid,
                           actor_id='nurse', hospital_id='H3')
        self.error('NOT_FOUND', events.list_events, self.conn, T0 + 50, 'missing-patient', actor_id='unit-1')

    def test_old_hospital_sees_handoff_exit_but_not_new_hospital_or_later_events(self):
        iid = self.inquiry_id('A', 'H1')
        service.respond(self.conn, T0 + 20, iid, 'accept', 'nurse')
        service.confirm_destination(self.conn, T0 + 30, 'A', iid)
        service.hospital_decision(self.conn, T0 + 40, 'A', 'H1', 'adjust', '재확인')
        service.send_inquiries(self.conn, T0 + 41, 'A', ['H2'])
        second = self.inquiry_id('A', 'H2')
        service.respond(self.conn, T0 + 45, second, 'accept', 'nurse2')
        service.change_destination(self.conn, T0 + 50, 'A', second, EST)
        hidden = service.log(self.conn, T0 + 51, 'system', 'transport_recheck_required', 'A', 'H1')
        new = service.log(self.conn, T0 + 52, 'system', 'transport_recheck_required', 'A', 'H2')
        old = self.listing(hospital_id='H1')['items']
        self.assertIn('patient_redirected_away', [e['type'] for e in old])
        self.assertNotIn('destination_changed', [e['type'] for e in old])
        self.assertNotIn(hidden, [e['event_id'] for e in old])
        self.assertNotIn(new, [e['event_id'] for e in old])
        self.assertTrue(all(e['hospital_id'] == 'H1' for e in old))
        current = self.listing(hospital_id='H2')['items']
        self.assertIn(new, [e['event_id'] for e in current])
        self.error('NOT_FOUND', events.mark_delivered, self.conn, T0 + 70, 'A', hidden,
                   actor_id='nurse', hospital_id='H1')

    def test_closed_request_notification_survives_destination_confirmation(self):
        iid = self.inquiry_id('A', 'H1')
        service.respond(self.conn, T0 + 20, iid, 'accept', 'nurse')
        service.confirm_destination(self.conn, T0 + 30, 'A', iid)
        result = self.listing(hospital_id='H2')
        self.assertEqual([e['type'] for e in result['items']], ['inquiry_closed'])
        eid = result['items'][0]['event_id']
        events.mark_delivered(self.conn, T0 + 50, 'A', eid, actor_id='nurse2', hospital_id='H2')
        events.acknowledge(self.conn, T0 + 60, 'A', eid, actor_id='nurse2', hospital_id='H2')
        self.assertEqual(service._inquiry(self.conn, self.inquiry_id('A', 'H2'))['state'], 'CANCELLED')

    def test_actual_patient_cancellation_is_visible_to_current_hospital(self):
        iid = self.inquiry_id('A', 'H1')
        service.respond(self.conn, T0 + 20, iid, 'accept', 'nurse')
        service.confirm_destination(self.conn, T0 + 30, 'A', iid)
        service.cancel_patient(self.conn, T0 + 40, 'A', '기타')
        canonical = dict(self.conn.execute("SELECT * FROM events WHERE patient_id='A' "
                                            "AND type='patient_cancelled'").fetchone())
        self.assertEqual(canonical['hospital_id'], 'H1')
        result = self.listing(hospital_id='H1')['items']
        self.assertIn(canonical['id'], [e['event_id'] for e in result])
        events.mark_delivered(self.conn, T0 + 50, 'A', canonical['id'], actor_id='nurse', hospital_id='H1')
        events.acknowledge(self.conn, T0 + 60, 'A', canonical['id'], actor_id='nurse', hospital_id='H1')
        # WAITING 상태 취소는 특정 병원을 지목하지 않는다. 문의 종료는 각 병원에 따로 보인다.
        self.patient('B')
        service.send_inquiries(self.conn, T0 + 10, 'B', ['H2'])
        service.cancel_patient(self.conn, T0 + 40, 'B', '기타')
        waiting = events.list_events(self.conn, T0 + 60, 'B', actor_id='nurse', hospital_id='H2')
        self.assertEqual([e['type'] for e in waiting['items']], ['inquiry_closed'])

    def test_unscoped_event_is_visible_only_within_hospital_participation_window(self):
        before_join = service.log(self.conn, T0 + 20, 'system', 'transport_inquiry_exhausted', 'A')
        iid = self.inquiry_id('A', 'H1')
        service.respond(self.conn, T0 + 21, iid, 'accept', 'nurse')
        service.confirm_destination(self.conn, T0 + 30, 'A', iid)
        during = service.log(self.conn, T0 + 35, 'system', 'transport_inquiry_ready', 'A',
                             private_data='새 병원과 환자 관련 원문')
        self.assertIn(during, [e['event_id'] for e in self.listing(hospital_id='H1')['items']])
        self.assertNotIn(before_join, [e['event_id'] for e in self.listing(hospital_id='H1')['items']])
        self.assertNotIn(during, [e['event_id'] for e in self.listing(hospital_id='H2')['items']])
        service.hospital_decision(self.conn, T0 + 40, 'A', 'H1', 'adjust', '재확인')
        service.send_inquiries(self.conn, T0 + 41, 'A', ['H2'])
        second = self.inquiry_id('A', 'H2')
        service.respond(self.conn, T0 + 45, second, 'accept', 'nurse2')
        service.change_destination(self.conn, T0 + 50, 'A', second, EST)
        after_leave = service.log(self.conn, T0 + 51, 'system', 'transport_inquiry_exhausted', 'A')
        old = self.listing(hospital_id='H1')
        current = self.listing(hospital_id='H2')
        self.assertNotIn(after_leave, [e['event_id'] for e in old['items']])
        self.assertIn(after_leave, [e['event_id'] for e in current['items']])
        self.assertNotIn(during, [e['event_id'] for e in current['items']])
        self.assertNotIn('새 병원과 환자 관련 원문', json.dumps(old, ensure_ascii=False))
        self.error('NOT_FOUND', events.mark_delivered, self.conn, T0 + 60, 'A', after_leave,
                   actor_id='nurse', hospital_id='H1')

    def test_cursor_and_unacknowledged_recovery_are_separate(self):
        first, second = self.question(), self.question('H2', T0 + 21)
        page = self.listing(limit='1')
        self.assertTrue(page['has_more'])
        self.assertEqual(page['next_after_id'], first)
        second_page = self.listing(after_id=str(first), limit=1)
        self.assertEqual([e['event_id'] for e in second_page['items']], [second])
        self.assertFalse(second_page['has_more'])
        self.assertEqual(self.listing(after_id=second)['items'], [])
        replay = self.listing(unacknowledged_only='true', limit=1)
        self.assertEqual([e['event_id'] for e in replay['items']], [first])
        self.assertIsNone(replay['next_after_id'])
        self.error('INVALID', self.listing, after_id=first, unacknowledged_only=True)
        events.mark_delivered(self.conn, T0 + 50, 'A', first, actor_id='unit-1')
        events.acknowledge(self.conn, T0 + 60, 'A', first, actor_id='unit-1')
        self.assertEqual([e['event_id'] for e in self.listing(unacknowledged_only=True)['items']], [second])

    def test_future_unknown_other_patient_events_cannot_be_received(self):
        future = self.question(now=T0 + 90)
        ordinary = service.log(self.conn, T0 + 20, 'system', 'patient_updated', 'A', fields=['symptoms'])
        self.patient('B')
        other = service.log(self.conn, T0 + 20, 'system', 'patient_cancelled', 'B')
        self.assertEqual(self.listing(now=T0 + 50)['items'], [])
        for eid in (future, ordinary, other, 999999):
            with self.subTest(eid=eid):
                self.error('NOT_FOUND', events.mark_delivered, self.conn, T0 + 50, 'A', eid, actor_id='unit-1')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM important_event_receipts').fetchone()[0], 0)

    def test_validation_limits_actor_identity_and_stage_time(self):
        for bad in ('', ' ', 'x' * 81, 'x\n', None, True):
            self.error('INVALID', events.list_events, self.conn, T0 + 50, 'A', actor_id=bad)
        for bad in (-1, True, 1.5, '1e2', '9' * 20):
            self.error('INVALID', self.listing, after_id=bad)
        for bad in (0, 201, True, 'no'):
            self.error('INVALID', self.listing, limit=bad)
        for bad in (None, 1, 'yes'):
            self.error('INVALID', self.listing, unacknowledged_only=bad)
        eid = self.question()
        events.mark_delivered(self.conn, T0 + 40, 'A', eid, actor_id='unit-1')
        self.error('INVALID_TIME', events.acknowledge, self.conn, T0 + 30, 'A', eid, actor_id='unit-1')

    def test_concurrent_delivery_retries_keep_one_receipt(self):
        eid = self.question()
        barrier = threading.Barrier(2)
        results, failures = [], []

        def record():
            connection = db.connect(self.path)
            try:
                barrier.wait(timeout=10)
                results.append(events.mark_delivered(connection, T0 + 40, 'A', eid, actor_id='unit-1'))
            except Exception as exc:
                failures.append(exc)
            finally:
                connection.close()

        threads = [threading.Thread(target=record) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)
            self.assertFalse(thread.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(sorted(item['duplicate'] for item in results), [False, True])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM important_event_receipts').fetchone()[0], 1)
