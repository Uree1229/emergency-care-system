"""수용 제한의 잘못된 입력이 저장되어 후속 조회를 망가뜨리지 않아야 한다."""
import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from server import db, planning, service, transport_review
from server.app import make_handler
from test_server import Base, EST, T0


MAX_SQLITE_INT = 2**63 - 1


class RestrictionValidationBase(Base):
    def snapshot(self):
        # 업무 테이블뿐 아니라 사건, 재평가 결과, 계획 revision까지 함께 검사한다.
        return tuple(self.conn.iterdump())

    def add(self, **changes):
        arguments = dict(scope='all', reason='인력 부족', starts_at=None, ends_at=None,
                         actor='hospital')
        arguments.update(changes)
        return service.add_restriction(self.conn, T0, 'H1', **arguments)

    def assert_invalid_unchanged(self, **changes):
        before = self.snapshot()
        with self.assertRaises(service.ServiceError) as caught:
            self.add(**changes)
        self.assertEqual(caught.exception.code, 'INVALID')
        self.assertEqual(caught.exception.status, 400)
        self.assertEqual(self.snapshot(), before)

    def stored(self):
        return dict(self.conn.execute('SELECT * FROM restrictions ORDER BY id DESC LIMIT 1').fetchone())


class RestrictionTimeValidation(RestrictionValidationBase):
    def test_malformed_times_are_rejected_before_any_write(self):
        malformed = ('tomorrow', '', '1790000000', [], {}, True, False, 1.0,
                     float('nan'), float('inf'), -1, MAX_SQLITE_INT + 1)
        for field in ('starts_at', 'ends_at'):
            for value in malformed:
                with self.subTest(field=field, value=repr(value)):
                    self.assert_invalid_unchanged(**{field: value})

    def test_end_must_follow_explicit_or_default_start(self):
        for arguments in (dict(ends_at=T0), dict(ends_at=T0 - 1),
                          dict(starts_at=T0 + 50, ends_at=T0 + 50),
                          dict(starts_at=T0 + 50, ends_at=T0 + 49),
                          dict(starts_at=0, ends_at=0)):
            with self.subTest(arguments=arguments):
                self.assert_invalid_unchanged(**arguments)

    def test_zero_start_is_preserved_and_expired_limit_needs_recheck(self):
        view = self.add(starts_at=0, ends_at=1)
        saved = self.stored()
        self.assertEqual((saved['starts_at'], saved['ends_at']), (0, 1))
        self.assertEqual(view['restrictions'][0]['status'], 'recheck')
        self.assertIsNone(saved['cleared_at'])

    def test_default_start_and_open_end_remain_supported(self):
        view = self.add()
        saved = self.stored()
        self.assertEqual(saved['starts_at'], T0)
        self.assertIsNone(saved['ends_at'])
        self.assertEqual(view['restrictions'][0]['status'], 'active')

    def test_future_start_is_scheduled_and_integer_upper_boundary_is_allowed(self):
        view = self.add(starts_at=T0 + 100, ends_at=MAX_SQLITE_INT)
        self.assertEqual(view['restrictions'][0]['status'], 'scheduled')
        self.assertEqual(self.stored()['ends_at'], MAX_SQLITE_INT)
        service.clear_restriction(self.conn, T0, 'H1', self.stored()['id'])
        view = self.add(starts_at=MAX_SQLITE_INT)
        self.assertEqual(view['restrictions'][0]['status'], 'scheduled')
        self.assertEqual(self.stored()['starts_at'], MAX_SQLITE_INT)


class RestrictionTextValidation(RestrictionValidationBase):
    def test_invalid_text_types_and_empty_values_leave_database_unchanged(self):
        for field in ('scope', 'reason', 'actor'):
            for value in (None, '', ' \t\n ', True, False, 1, 1.5, [], {}):
                with self.subTest(field=field, value=repr(value)):
                    self.assert_invalid_unchanged(**{field: value})

    def test_text_length_limits_are_checked_before_write(self):
        for field, limit in (('scope', 100), ('reason', 2000), ('actor', 80)):
            with self.subTest(field=field):
                self.assert_invalid_unchanged(**{field: '가' * (limit + 1)})

    def test_trimmed_text_is_saved_and_named_scope_remains_supported(self):
        view = self.add(scope=' 소아청소년과 ', reason=' 야간 인력 부족 ', actor=' nurse-one ')
        saved = self.stored()
        self.assertEqual((saved['scope'], saved['reason']), ('소아청소년과', '야간 인력 부족'))
        event = self.conn.execute("SELECT actor FROM events WHERE type='restriction_added' ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(event['actor'], 'nurse-one')
        self.assertEqual(view['restrictions'][0]['scope'], '소아청소년과')

    def test_valid_maximum_text_lengths_are_not_truncated(self):
        self.add(scope=' ' + '가' * 100 + ' ', reason=' ' + '나' * 2000 + ' ',
                 actor=' ' + '다' * 80 + ' ')
        saved = self.stored()
        self.assertEqual((len(saved['scope']), len(saved['reason'])), (100, 2000))
        actor = self.conn.execute("SELECT actor FROM events WHERE type='restriction_added' ORDER BY id DESC LIMIT 1").fetchone()[0]
        self.assertEqual(len(actor), 80)


class RestrictionRejectionRecovery(RestrictionValidationBase):
    def test_invalid_limit_does_not_mutate_transport_review_or_break_reads(self):
        self.patient('A', required_capabilities=['소아청소년과'])
        iid = self.accepted('A', 'H1')
        service.confirm_destination(self.conn, T0 + 20, 'A', iid)
        service.depart(self.conn, T0 + 21, 'A', EST)
        transport_review.enable(self.conn, T0 + 22, 'A', True)
        before = self.snapshot()
        self.assert_invalid_unchanged(ends_at='tomorrow')
        self.assert_invalid_unchanged(scope=[], reason={}, ends_at=False)
        for _ in range(2):
            self.assertEqual(service.hospital_view(self.conn, 'H1', T0 + 30)['restrictions'], [])
            self.assertEqual(len(service.list_hospitals(self.conn, T0 + 30)), 3)
            self.assertEqual(service.restrictions(self.conn, 'H1', T0 + 30), [])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(service._patient(self.conn, 'A')['destination_hospital_id'], 'H1')

        service.add_restriction(self.conn, T0 + 30, 'H1', 'all', '확인된 수용 제한', ends_at=T0 + 60)
        review = self.conn.execute("SELECT state,risk_signature FROM transport_reviews WHERE patient_id='A'").fetchone()
        self.assertEqual(review['state'], 'RECHECK_REQUIRED')
        self.assertTrue(review['risk_signature'])
        self.assertEqual(service._patient(self.conn, 'A')['destination_hospital_id'], 'H1')

    def test_legacy_malformed_time_is_visible_as_recheck_without_silent_repair(self):
        self.patient('A', required_capabilities=['소아청소년과'])
        iid = self.accepted('A', 'H1')
        service.confirm_destination(self.conn, T0 + 20, 'A', iid)
        transport_review.enable(self.conn, T0 + 22, 'A', True)
        self.patient('WAITING', now=T0 + 23)
        malformed_times = ((T0, 'tomorrow'), ('tomorrow', None),
                           (T0 + 0.5, None), (T0, T0 + 0.5),
                           (-1, T0 + 30), (T0, -1), (T0 + 10, T0 + 5))
        for start, end in malformed_times:
            with self.subTest(start=start, end=end):
                rid = self.conn.execute(
                    'INSERT INTO restrictions(hospital_id,scope,reason,starts_at,ends_at) VALUES(?,?,?,?,?)',
                    ('H1', 'all', '과거 잘못된 기록', start, end)).lastrowid
                raw_before = tuple(self.conn.execute('SELECT * FROM restrictions WHERE id=?', (rid,)).fetchone())
                visible = service.restrictions(self.conn, 'H1', T0 + 30)[0]
                self.assertEqual(visible['status'], 'recheck')
                self.assertEqual(visible['validation_error'], 'INVALID_RESTRICTION_DATA')
                self.assertEqual(service.hospital_view(self.conn, 'H1', T0 + 30)['restrictions'][0]['status'], 'recheck')
                self.assertEqual(len(service.list_hospitals(self.conn, T0 + 30)), 3)
                candidates = service.candidates(self.conn, T0 + 30, 'WAITING', EST)
                h1 = next(candidate for candidate in candidates['candidates'] if candidate['hospital_id'] == 'H1')
                self.assertTrue(any('재확인' in text for text in h1['checks']))
                with db.transaction(self.conn):
                    transport_review.scan_in_transaction(self.conn, T0 + 30, 'H1')
                self.assertEqual(transport_review.view(self.conn, T0 + 30, 'A')['state'], 'RECHECK_REQUIRED')
                self.assertEqual(tuple(self.conn.execute('SELECT * FROM restrictions WHERE id=?', (rid,)).fetchone()),
                                 raw_before)
                service.clear_restriction(self.conn, T0 + 31, 'H1', rid)
                self.assertEqual(service.restrictions(self.conn, 'H1', T0 + 31), [])
                raw_after = dict(self.conn.execute('SELECT * FROM restrictions WHERE id=?', (rid,)).fetchone())
                self.assertEqual((raw_after['starts_at'], raw_after['ends_at']), (start, end))
                self.assertEqual(raw_after['cleared_at'], T0 + 31)

    def test_planning_publish_does_not_crash_on_legacy_malformed_start(self):
        self.conn.execute(
            'INSERT INTO restrictions(hospital_id,scope,reason,starts_at,ends_at) VALUES(?,?,?,?,?)',
            ('H1', 'all', '과거 잘못된 시작시각', 'tomorrow', None))
        self.conn.execute('UPDATE planning_state SET lease_owner=?,lease_until=? WHERE id=1',
                          ('validation-test-owner', T0 + 30))
        snapshot = planning.capture(self.conn, T0)
        self.assertTrue(planning.publish(self.conn, snapshot, {}, T0, 'validation-test-owner'))


class RestrictionValidationHttp(RestrictionValidationBase):
    def setUp(self):
        super().setUp()
        self.http = ThreadingHTTPServer(('127.0.0.1', 0),
                                       make_handler(self.path, EST, clock=lambda: T0))
        self.worker = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.worker.start()
        self.base = f'http://127.0.0.1:{self.http.server_port}'

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.worker.join(timeout=5)
        super().tearDown()

    def call(self, method, path, body=None):
        request = urllib.request.Request(
            self.base + path, method=method,
            data=None if body is None else json.dumps(body).encode('utf-8'),
            headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as response:
            with response:
                return response.code, json.load(response)

    def test_invalid_post_returns_400_and_does_not_poison_later_reads(self):
        before = self.snapshot()
        path = '/api/hospitals/H1/restrictions'
        for changes in (dict(ends_at='tomorrow'), dict(ends_at=[]), dict(ends_at=True),
                        dict(ends_at=1.5), dict(ends_at=float('nan')),
                        dict(starts_at=float('inf')), dict(ends_at=MAX_SQLITE_INT + 1),
                        dict(starts_at='tomorrow'), dict(starts_at=T0 + 60, ends_at=T0 + 30),
                        dict(scope={}), dict(reason=[]), dict(handled_by='')):
            with self.subTest(changes=changes):
                payload = dict(scope='all', reason='인력 부족')
                payload.update(changes)
                status, data = self.call('POST', path, payload)
                self.assertEqual(status, 400)
                self.assertEqual(data['error'], 'INVALID')
                self.assertEqual(self.snapshot(), before)
                for read_path in ('/api/hospitals/H1', '/api/hospitals'):
                    self.assertEqual(self.call('GET', read_path)[0], 200)
                self.assertEqual(self.snapshot(), before)

        status, view = self.call('POST', path, dict(scope='all', reason='정상 제한', ends_at=T0 + 30))
        self.assertEqual(status, 200)
        self.assertEqual(view['restrictions'][0]['status'], 'active')
        self.assertEqual(len(view['restrictions']), 1)

    def test_http_preserves_explicit_zero_and_future_start(self):
        path = '/api/hospitals/H1/restrictions'
        status, view = self.call('POST', path, dict(scope='all', reason='과거 제한', starts_at=0, ends_at=1))
        self.assertEqual(status, 200)
        self.assertEqual((view['restrictions'][0]['starts_at'], view['restrictions'][0]['status']), (0, 'recheck'))
        status, view = self.call('POST', path, dict(scope='소아청소년과', reason='예정 제한', starts_at=T0 + 30))
        self.assertEqual(status, 200)
        self.assertEqual((view['restrictions'][-1]['starts_at'], view['restrictions'][-1]['status']),
                         (T0 + 30, 'scheduled'))


if __name__ == '__main__':
    unittest.main()
