"""통화 전달 기록: 가상 DB만 사용하며 실제 통화·병원 문의를 하지 않는다."""
import concurrent.futures
import json
import sqlite3
import threading

from server import call_records as calls, db, service, work_activity
from server.service import ServiceError
from test_server import Base, T0


class CallRecordTests(Base):
    def setUp(self):
        super().setUp()
        self.conn.executescript(calls.SCHEMA)
        self.patient()

    def payload(self, **kw):
        value = dict(actor_id='crew-1', client_record_id='call-1', hospital_id='H1',
                     call_started_at=T0 + 10, call_ended_at=T0 + 20,
                     reported_outcome='can_accept', counterparty_role='emergency_staff',
                     counterparty_label='응급실 당직자', note='도착 전 다시 확인 요청')
        value.update(kw)
        return value

    def make(self, now=T0 + 30, path_hospital_id=None, **kw):
        return calls.create(self.conn, now, 'A', self.payload(**kw), hospital_id=path_hospital_id)

    def confirmation(self, **kw):
        value = dict(actor_id='desk-1', client_confirmation_id='confirm-1', decision='confirm')
        value.update(kw)
        return value

    def check(self, rid, now=T0 + 40, hospital_id='H1', **kw):
        return calls.confirm(self.conn, now, 'A', rid, self.confirmation(**kw), hospital_id=hospital_id)

    def error(self, code, fn, *args, **kw):
        with self.assertRaises(ServiceError) as caught:
            fn(*args, **kw)
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def business(self):
        tables = ('patients', 'hospitals', 'resources', 'inquiries', 'conversation_hospital_sessions',
                  'chat_messages', 'events', 'planning_state')
        return {table: [dict(row) for row in self.conn.execute(f'SELECT * FROM {table} ORDER BY rowid')]
                for table in tables}

    def test_phone_only_record_shared_with_named_hospital_without_electronic_inquiry(self):
        before = self.business()
        result = self.make()
        item = result['record']
        self.assertFalse(result['duplicate'])
        self.assertEqual(item['recorded_at'], T0 + 30)
        self.assertEqual(item['reported_outcome'], 'can_accept')
        self.assertEqual(item['confirmation_status'], 'unconfirmed')
        self.assertEqual(item['operational_effect'], 'none')
        self.assertEqual(item['confirmation_scope'], 'record_content_only')
        self.assertEqual(calls.view(self.conn, T0 + 35, 'A', item['id'], hospital_id='H1')['record']['id'], item['id'])
        self.check(item['id'])
        self.assertEqual(self.business(), before)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM inquiries').fetchone()[0], 0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM conversation_hospital_sessions').fetchone()[0], 0)

    def test_exact_create_retry_preserves_record_and_conflicting_retry_is_rejected(self):
        first = self.make()
        second = self.make(now=T0 + 80)
        self.assertTrue(second['duplicate'])
        self.assertEqual(first['record'], second['record'])
        for changes in (dict(note='different'), dict(hospital_id='H2'), dict(call_ended_at=T0 + 19)):
            with self.subTest(changes=changes):
                self.error('CONFLICT', self.make, **changes)
        self.patient('B')
        self.error('CONFLICT', calls.create, self.conn, T0 + 30, 'B', self.payload())

    def test_hospital_must_have_relationship_and_can_record_followup_after_shared_phone_record(self):
        self.error('NOT_FOUND', self.make, path_hospital_id='H1', actor_id='desk-1')
        self.make()
        result = self.make(path_hospital_id='H1', actor_id='desk-1', client_record_id='hospital-call',
                           counterparty_role='paramedic')
        self.assertEqual(result['record']['author_role'], 'hospital')
        self.error('INVALID', self.make, path_hospital_id='H1', actor_id='desk-1', hospital_id='H2')
        data = self.payload(actor_id='desk-1', client_record_id='without-hid')
        data.pop('hospital_id')
        self.assertEqual(calls.create(self.conn, T0 + 31, 'A', data, hospital_id='H1')['record']['hospital_id'], 'H1')

    def test_existing_electronic_inquiry_also_allows_hospital_record(self):
        service.send_inquiries(self.conn, T0 + 5, 'A', ['H1'])
        result = self.make(path_hospital_id='H1', actor_id='desk-1', counterparty_role='paramedic')
        self.assertEqual(result['record']['author_role'], 'hospital')
        self.assertEqual(result['record']['confirmation_status'], 'unconfirmed')

    def test_hospital_sees_only_own_target_records_and_other_patient_is_not_visible(self):
        h1 = self.make()['record']['id']
        h2 = self.make(client_record_id='second', hospital_id='H2', note='other-hospital-note')['record']['id']
        self.assertEqual([x['id'] for x in calls.list_records(self.conn, T0 + 40, 'A', hospital_id='H2')['records']], [h2])
        self.error('NOT_FOUND', calls.view, self.conn, T0 + 40, 'A', h1, hospital_id='H2')
        self.error('NOT_FOUND', calls.confirm, self.conn, T0 + 40, 'A', h1, self.confirmation(), hospital_id='H2')
        self.patient('B')
        self.error('NOT_FOUND', calls.view, self.conn, T0 + 40, 'B', h1)

    def test_no_relationship_hospital_get_does_not_acquire_scope(self):
        self.error('NOT_FOUND', calls.list_records, self.conn, T0 + 40, 'A', hospital_id='H3')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM call_records').fetchone()[0], 0)

    def test_opposite_role_confirmation_is_only_record_content_not_acceptance(self):
        rid = self.make()['record']['id']
        before = self.business()
        result = self.check(rid)
        self.assertEqual(result['record']['confirmation_status'], 'confirmed')
        self.assertEqual(result['record']['confirmation']['confirmed_at'], T0 + 40)
        self.assertEqual(self.business(), before)
        self.assertIsNone(service._patient(self.conn, 'A')['destination_hospital_id'])

    def test_hospital_authored_record_can_only_be_confirmed_from_paramedic_path(self):
        service.send_inquiries(self.conn, T0 + 5, 'A', ['H1'])
        rid = self.make(path_hospital_id='H1', actor_id='desk-1', counterparty_role='paramedic')['record']['id']
        self.error('SELF_CONFIRMATION', self.check, rid)
        result = self.check(rid, hospital_id=None, actor_id='crew-1')
        self.assertEqual(result['record']['confirmation']['role'], 'paramedic')

    def test_origin_side_cannot_confirm_even_using_different_actor(self):
        rid = self.make()['record']['id']
        self.error('SELF_CONFIRMATION', self.check, rid, hospital_id=None, actor_id='crew-2')

    def test_confirmation_retries_are_idempotent_and_other_actor_cannot_overwrite(self):
        rid = self.make()['record']['id']
        first = self.check(rid)
        repeated = self.check(rid, now=T0 + 90)
        self.assertTrue(repeated['duplicate'])
        self.assertEqual(first['record'], repeated['record'])
        self.error('CONFLICT', self.check, rid, decision='dispute', reason='different')
        self.error('ALREADY_CONFIRMED', self.check, rid, actor_id='desk-2', client_confirmation_id='other-key')

    def test_confirmation_key_cannot_be_reused_for_another_record(self):
        first = self.make()['record']['id']
        second = self.make(client_record_id='second')['record']['id']
        self.check(first)
        self.error('CONFLICT', self.check, second)

    def test_dispute_requires_reason_and_preserves_source_text(self):
        rid = self.make()['record']['id']
        self.error('INVALID', self.check, rid, decision='dispute')
        self.error('INVALID', self.check, rid, decision='dispute', reason='   ')
        result = self.check(rid, decision='dispute', reason='그런 답변을 한 적이 없습니다')
        self.assertEqual(result['record']['confirmation_status'], 'disputed')
        self.assertEqual(result['record']['note'], '도착 전 다시 확인 요청')
        self.assertEqual(result['record']['reported_outcome'], 'can_accept')

    def test_correction_preserves_original_and_does_not_inherit_confirmation(self):
        first = self.make()['record']['id']
        self.check(first)
        second = self.make(now=T0 + 50, client_record_id='correction', supersedes_id=first,
                           reported_outcome='checking', note='확답이 아닌 확인 중으로 정정')['record']
        old = calls.view(self.conn, T0 + 55, 'A', first)['record']
        self.assertEqual(old['superseded_by_id'], second['id'])
        self.assertEqual(old['reported_outcome'], 'can_accept')
        self.assertEqual(old['confirmation_status'], 'confirmed')
        self.assertEqual(second['confirmation_status'], 'unconfirmed')
        self.assertEqual(second['supersedes_id'], first)
        self.assertTrue(self.check(first, now=T0 + 60)['duplicate'])
        self.error('SUPERSEDED', self.check, first, now=T0 + 60, client_confirmation_id='another')
        self.assertEqual(self.check(second['id'], now=T0 + 60, client_confirmation_id='second-confirm')['record']['confirmation_status'], 'confirmed')

    def test_only_original_author_can_correct_and_correction_forks_are_rejected(self):
        first = self.make()['record']['id']
        self.error('NOT_AUTHOR', self.make, client_record_id='wrong', supersedes_id=first, actor_id='crew-2')
        self.error('NOT_AUTHOR', self.make, client_record_id='wrong-side', supersedes_id=first,
                   actor_id='desk-1', path_hospital_id='H1')
        self.make(client_record_id='correct', supersedes_id=first)
        self.error('SUPERSEDED', self.make, client_record_id='fork', supersedes_id=first)

    def test_record_and_confirmation_rows_cannot_be_updated_deleted_or_replaced(self):
        rid = self.make()['record']['id']
        self.check(rid)
        for table, key in (('call_records', 'id'), ('call_record_confirmations', 'record_id')):
            with self.subTest(table=table):
                with self.assertRaises(sqlite3.IntegrityError):
                    self.conn.execute(f'UPDATE {table} SET actor_id=? WHERE {key}=?', ('forged', rid))
                with self.assertRaises(sqlite3.IntegrityError):
                    self.conn.execute(f'DELETE FROM {table} WHERE {key}=?', (rid,))
                row = self.conn.execute(f'SELECT * FROM {table} WHERE {key}=?', (rid,)).fetchone()
                placeholders = ','.join('?' for _ in row)
                with self.assertRaises(sqlite3.IntegrityError):
                    self.conn.execute(f'INSERT OR REPLACE INTO {table} VALUES ({placeholders})', tuple(row))

    def test_pagination_and_repeated_schema_install_preserve_history(self):
        ids = [self.make(client_record_id=f'item-{n}')['record']['id'] for n in range(3)]
        page = calls.list_records(self.conn, T0 + 40, 'A', limit=2)
        self.assertEqual([x['id'] for x in page['records']], ids[:0:-1])
        self.assertTrue(page['has_more'])
        rest = calls.list_records(self.conn, T0 + 40, 'A', before_id=page['next_before_id'])
        self.assertEqual([x['id'] for x in rest['records']], ids[:1])
        self.conn.executescript(calls.SCHEMA)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM call_records').fetchone()[0], 3)

    def test_future_records_and_future_confirmations_not_visible_to_past_clock(self):
        rid = self.make()['record']['id']
        self.assertEqual(calls.list_records(self.conn, T0 + 29, 'A')['records'], [])
        self.error('NOT_FOUND', calls.view, self.conn, T0 + 29, 'A', rid)
        self.check(rid, now=T0 + 90)
        self.assertEqual(calls.view(self.conn, T0 + 40, 'A', rid)['record']['confirmation_status'], 'unconfirmed')

    def test_create_retry_cannot_return_a_record_from_a_future_server_clock(self):
        first = self.make(now=T0 + 30)
        error = self.error('CLOCK_CONFLICT', self.make, now=T0 + 25)
        self.assertEqual(error.status, 409)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM call_records').fetchone()[0], 1)
        self.assertEqual(calls.view(self.conn, T0 + 30, 'A', first['record']['id'])['record'], first['record'])
        self.assertTrue(self.make(now=T0 + 30)['duplicate'])

    def test_future_phone_record_does_not_establish_hospital_record_scope(self):
        rid = self.make(now=T0 + 30)['record']['id']
        self.error('NOT_FOUND', self.make, now=T0 + 25, path_hospital_id='H1',
                   actor_id='desk-1', client_record_id='past-hospital', counterparty_role='paramedic')
        self.error('NOT_FOUND', calls.list_records, self.conn, T0 + 25, 'A', hospital_id='H1')
        self.error('NOT_FOUND', calls.confirm, self.conn, T0 + 25, 'A', rid,
                   self.confirmation(), hospital_id='H1')
        self.assertEqual(calls.view(self.conn, T0 + 30, 'A', rid, hospital_id='H1')['record']['id'], rid)

    def test_future_phone_record_does_not_establish_work_measurement_scope(self):
        self.make(now=T0 + 30)
        payload = dict(actor_id='desk-1', client_activity_id='phone-review', kind='request_review')
        self.error('NOT_FOUND', work_activity.start, self.conn, T0 + 25, 'A', 'hospital',
                   payload, hospital_id='H1')
        self.error('NOT_FOUND', work_activity.list_records, self.conn, T0 + 25, 'A',
                   'hospital', 'desk-1', hospital_id='H1')
        self.error('NOT_FOUND', work_activity.summary, self.conn, T0 + 25, 'A',
                   'hospital', hospital_id='H1')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM work_activities').fetchone()[0], 0)
        # A pre-existing inquiry remains sufficient; only the new phone relation is time-filtered.
        service.send_inquiries(self.conn, T0 + 5, 'A', ['H1'])
        result = work_activity.start(self.conn, T0 + 25, 'A', 'hospital', payload, hospital_id='H1')
        self.assertEqual(result['activity']['started_at'], T0 + 25)

    def test_bad_times_roles_and_outcomes_are_rejected_atomically(self):
        invalid = [dict(call_started_at=T0 - 1), dict(call_ended_at=T0 + 31),
                   dict(call_started_at=T0 + 25), dict(call_started_at=True),
                   dict(call_ended_at=-1), dict(reported_outcome='ACCEPTED'),
                   dict(counterparty_role='unknown'), dict(hospital_id=None)]
        for value in invalid:
            with self.subTest(value=value):
                self.error('INVALID', self.make, **value)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM call_records').fetchone()[0], 0)

    def test_bad_identifiers_text_fields_and_extra_fields_rejected(self):
        invalid = [dict(actor_id=''), dict(actor_id='name space'), dict(actor_id='a' * 81),
                   dict(client_record_id='x' * 129), dict(note='x' * 1001), dict(note='bad\x00text'),
                   dict(counterparty_label='line\nline'), dict(counterparty_label='x' * 81),
                   dict(extra='unexpected')]
        for value in invalid:
            with self.subTest(value=value):
                self.error('INVALID', self.make, **value)

    def test_list_and_record_id_bounds_and_bad_confirmation_fields(self):
        rid = self.make()['record']['id']
        for kw in (dict(limit=0), dict(limit=101), dict(limit=True), dict(before_id='9' * 4301)):
            with self.subTest(kw=kw):
                self.error('INVALID', calls.list_records, self.conn, T0 + 40, 'A', **kw)
        self.error('INVALID', calls.view, self.conn, T0 + 40, 'A', '9' * 4301)
        self.error('INVALID', calls.confirm, self.conn, T0 + 40, 'A', rid,
                   dict(self.confirmation(), claimed_accepted=True), hospital_id='H1')

    def test_concurrent_exact_create_has_one_record(self):
        barrier = threading.Barrier(2)

        def run():
            conn = db.connect(self.path)
            try:
                barrier.wait(timeout=5)
                return calls.create(conn, T0 + 30, 'A', self.payload())
            finally:
                conn.close()

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            a, b = list(pool.map(lambda _: run(), range(2)))
        self.assertEqual(a['record']['id'], b['record']['id'])
        self.assertEqual({a['duplicate'], b['duplicate']}, {False, True})

    def test_concurrent_confirmation_is_idempotent_and_correction_fork_fails(self):
        rid = self.make()['record']['id']
        barrier = threading.Barrier(2)

        def confirm():
            conn = db.connect(self.path)
            try:
                barrier.wait(timeout=5)
                return calls.confirm(conn, T0 + 40, 'A', rid, self.confirmation(), hospital_id='H1')
            finally:
                conn.close()

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            a, b = list(pool.map(lambda _: confirm(), range(2)))
        self.assertEqual({a['duplicate'], b['duplicate']}, {False, True})
        barrier = threading.Barrier(2)

        def correct(index):
            conn = db.connect(self.path)
            try:
                barrier.wait(timeout=5)
                try:
                    calls.create(conn, T0 + 50, 'A', self.payload(
                        client_record_id=f'correction-{index}', supersedes_id=rid))
                    return 'created'
                except ServiceError as error:
                    return error.code
            finally:
                conn.close()

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(correct, (1, 2)))
        self.assertCountEqual(results, ['created', 'SUPERSEDED'])


if __name__ == '__main__':
    import unittest
    unittest.main()
