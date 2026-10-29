"""Human work measurements use temp DBs and explicit server clocks only."""
import concurrent.futures
import threading

from server import db, service, work_activity as activity
from server.service import ServiceError
from test_server import Base, T0


class WorkActivity(Base):
    def setUp(self):
        super().setUp()
        self.conn.executescript(activity.SCHEMA)
        self.patient()

    def start(self, key='task1', kind='request_review', actor='staff1', at=T0, pid='A',
              role='paramedic', hid=None):
        return activity.start(self.conn, at, pid, role,
                              dict(actor_id=actor, client_activity_id=key, kind=kind), hid)

    def stop(self, activity_id, at=T0 + 20, pid='A', role='paramedic', hid=None, actor='staff1'):
        return activity.stop(self.conn, at, pid, role, activity_id, dict(actor_id=actor), hid)

    def measured(self, key, start, end=None, **kwargs):
        result = self.start(key, at=T0 + start, **kwargs)
        if end is not None:
            self.stop(result['activity']['id'], at=T0 + end,
                      **{k: v for k, v in kwargs.items() if k in ('pid', 'role', 'hid', 'actor')})
        return result['activity']['id']

    def summary(self, **kwargs):
        return activity.summary(self.conn, T0 + 100, 'A', 'paramedic', **kwargs)

    def assert_error(self, code, func, *args, **kwargs):
        with self.assertRaises(ServiceError) as raised:
            func(*args, **kwargs)
        self.assertEqual(raised.exception.code, code)
        return raised.exception

    def test_server_timed_start_stop_and_exact_retries(self):
        first = self.start()
        self.assertFalse(first['duplicate'])
        record = first['activity']
        self.assertEqual(record['started_at'], T0)
        self.assertEqual(record['state'], 'OPEN')
        retry = self.start(at=T0 + 10)
        self.assertTrue(retry['duplicate'])
        self.assertEqual(retry['activity']['id'], record['id'])
        self.assertEqual(retry['activity']['started_at'], T0)
        stopped = self.stop(record['id'])
        self.assertEqual(stopped['activity']['completed_seconds'], 20)
        retry_stop = self.stop(record['id'], at=T0 + 90)
        self.assertTrue(retry_stop['duplicate'])
        self.assertEqual(retry_stop['activity']['ended_at'], T0 + 20)
        self.assertEqual(self.start(at=T0 + 95)['activity']['state'], 'CLOSED')

    def test_start_key_cannot_be_reused_for_other_kind_or_patient(self):
        self.start()
        self.patient('B')
        self.assert_error('CONFLICT', self.start, kind='call')
        self.assert_error('CONFLICT', self.start, pid='B')
        self.assertEqual(self.conn.execute('SELECT count(*) FROM work_activities').fetchone()[0], 1)

    def test_stop_is_scoped_to_patient_role_hospital_and_actor(self):
        record = self.start()['activity']
        self.patient('B')
        service.send_inquiries(self.conn, T0 + 1, 'A', ['H1'])
        for scope in (dict(pid='B'), dict(actor='staff2'), dict(role='hospital', hid='H1')):
            with self.subTest(scope=scope):
                self.assert_error('NOT_FOUND', self.stop, record['id'], **scope)
        self.assertIsNone(self.conn.execute('SELECT ended_at FROM work_activities').fetchone()[0])

    def test_invalid_payloads_and_timestamps_are_rejected_without_writes(self):
        valid = dict(actor_id='staff1', client_activity_id='task1', kind='call')
        bad_payloads = [[], {}, dict(valid, body='patient narrative'),
                        dict(valid, started_at=T0 - 100), dict(valid, actor_id=' 이름 '),
                        dict(valid, actor_id='x' * 81), dict(valid, client_activity_id='x' * 129),
                        dict(valid, kind='waiting'), dict(valid, kind=[])]
        for payload in bad_payloads:
            with self.subTest(payload=payload):
                self.assert_error('INVALID', activity.start, self.conn, T0, 'A', 'paramedic', payload)
        for clock in (True, -1, T0 + .5, str(activity.MAX_ID + 1)):
            with self.subTest(clock=clock):
                self.assert_error('INVALID', activity.start, self.conn, clock, 'A', 'paramedic', valid)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM work_activities').fetchone()[0], 0)

    def test_earlier_stop_clock_rejected_and_missing_id_rejected(self):
        record = self.start()['activity']
        self.assert_error('CLOCK_CONFLICT', self.stop, record['id'], at=T0 - 1)
        self.assert_error('NOT_FOUND', self.stop, record['id'] + 1)
        self.assert_error('INVALID', self.stop, True)
        self.assert_error('INVALID', activity.stop, self.conn, T0 + 1, 'A', 'paramedic', record['id'],
                          dict(actor_id='staff1', ended_at=T0))

    def test_hospital_needs_relationship_but_cancelled_inquiry_is_still_measureable(self):
        self.assert_error('NOT_FOUND', self.start, role='hospital', hid='H1')
        service.send_inquiries(self.conn, T0, 'A', ['H1'])
        self.conn.execute("UPDATE inquiries SET state='CANCELLED' WHERE patient_id='A'")
        result = self.start(role='hospital', hid='H1')
        self.assertEqual(result['scope']['hospital_id'], 'H1')
        self.assert_error('NOT_FOUND', self.start, role='hospital', hid='H2')

    def test_hospital_current_destination_also_grants_measurement_scope(self):
        self.conn.execute("UPDATE patients SET destination_hospital_id='H1' WHERE id='A'")
        result = self.start(role='hospital', hid='H1')
        self.assertEqual(result['activity']['role'], 'hospital')

    def test_hospital_historical_conversation_relationship(self):
        iid = self.accepted('A', 'H1')
        service.confirm_destination(self.conn, T0 + 20, 'A', iid)
        self.conn.execute("UPDATE conversation_hospital_sessions SET inquiry_id=NULL WHERE hospital_id='H1'")
        self.conn.execute("DELETE FROM inquiries WHERE patient_id='A'")
        self.conn.execute("UPDATE patients SET destination_hospital_id=NULL WHERE id='A'")
        self.assertEqual(self.start(role='hospital', hid='H1')['activity']['role'], 'hospital')

    def test_hospital_and_paramedic_client_keys_are_independent(self):
        service.send_inquiries(self.conn, T0, 'A', ['H1', 'H2'])
        a = self.start()['activity']['id']
        b = self.start(role='hospital', hid='H1')['activity']['id']
        c = self.start(role='hospital', hid='H2')['activity']['id']
        self.assertEqual(len({a, b, c}), 3)

    def test_measurement_never_changes_workflow_events_or_planning_revision(self):
        before_events = self.conn.execute('SELECT count(*) FROM events').fetchone()[0]
        before_revision = self.conn.execute('SELECT revision FROM planning_state').fetchone()[0]
        before_patient = tuple(self.conn.execute("SELECT * FROM patients WHERE id='A'").fetchone())
        record = self.start()['activity']
        self.stop(record['id'])
        self.summary()
        self.assertEqual(self.conn.execute('SELECT count(*) FROM events').fetchone()[0], before_events)
        self.assertEqual(self.conn.execute('SELECT revision FROM planning_state').fetchone()[0], before_revision)
        self.assertEqual(tuple(self.conn.execute("SELECT * FROM patients WHERE id='A'").fetchone()), before_patient)

    def test_summary_merges_nested_touching_and_disjoint_intervals(self):
        self.measured('a', 0, 20, kind='request_review')
        self.measured('b', 5, 10, kind='call')
        self.measured('c', 20, 30, kind='chat')
        self.measured('d', 40, 50, kind='respond')
        result = self.summary()
        self.assertEqual(result['totals']['completed_raw_seconds'], 45)
        self.assertEqual(result['totals']['completed_active_seconds'], 40)
        self.assertEqual(result['totals']['completed_overlap_seconds'], 5)
        self.assertEqual(result['actors'][0]['by_kind']['call']['completed_raw_seconds'], 5)

    def test_summary_counts_people_separately_instead_of_unioning_all_staff(self):
        self.measured('a', 0, 20)
        self.measured('b', 0, 20, actor='staff2')
        result = self.summary()
        self.assertEqual(result['units'], 'person_seconds')
        self.assertEqual(result['totals']['completed_active_seconds'], 40)
        self.assertEqual(len(result['actors']), 2)
        self.assertEqual(self.summary(actor_id='staff2')['totals']['completed_active_seconds'], 20)

    def test_unfinished_activity_never_inflates_completed_work(self):
        self.measured('a', 0, 20)
        self.measured('open-a', 10)
        self.measured('open-b', 30)
        result = self.summary()
        self.assertEqual(result['totals']['completed_active_seconds'], 20)
        self.assertEqual(result['totals']['open_activity_count'], 2)
        self.assertEqual(result['totals']['open_raw_elapsed_seconds'], 160)
        self.assertEqual(result['totals']['open_elapsed_union_seconds'], 90)
        self.assertFalse(result['measurement']['open_intervals_in_completed_total'])

    def test_time_window_clips_intervals_and_excludes_other_patients(self):
        self.measured('a', 0, 50)
        self.measured('b', 25, 70)
        self.measured('open', 60)
        self.patient('B')
        self.measured('other', 0, 90, pid='B')
        result = self.summary(since=str(T0 + 20), until=str(T0 + 65))
        self.assertEqual(result['totals']['completed_raw_seconds'], 70)
        self.assertEqual(result['totals']['completed_active_seconds'], 45)
        self.assertEqual(result['totals']['open_elapsed_union_seconds'], 5)
        self.assertTrue(result['measurement']['scope_is_single_patient'])
        self.assertTrue(result['measurement']['cross_patient_overlap_not_removed'])

    def test_zero_width_window_has_no_work_and_zero_duration_task_counts(self):
        self.measured('zero', 20, 20)
        result = self.summary(since=T0 + 20, until=T0 + 21)
        self.assertEqual(result['totals']['closed_activity_count'], 1)
        self.assertEqual(result['totals']['completed_active_seconds'], 0)
        empty = self.summary(since=T0 + 20, until=T0 + 20)
        self.assertEqual(empty['totals']['closed_activity_count'], 0)

    def test_no_future_start_or_end_is_used_as_completed_work(self):
        self.measured('ends_later', 0, 80)
        self.measured('starts_later', 70, 90)
        result = activity.summary(self.conn, T0 + 50, 'A', 'paramedic')
        self.assertEqual(result['totals']['completed_active_seconds'], 0)
        self.assertEqual(result['totals']['open_activity_count'], 1)
        listing = activity.list_records(self.conn, T0 + 50, 'A', 'paramedic', 'staff1')
        self.assertEqual(len(listing['activities']), 1)
        self.assertIsNone(listing['activities'][0]['ended_at'])

    def test_summary_empty_scope_has_explicit_zero_counts_and_measurement_definitions(self):
        result = self.summary()
        self.assertEqual(result['actors'], [])
        self.assertTrue(all(value == 0 for value in result['totals'].values()))
        self.assertFalse(result['measurement']['inferred_from_workflow_waiting'])

    def test_summary_rejects_invalid_bounds_and_actor(self):
        for kwargs in (dict(since=-1), dict(since=True), dict(until=T0 + 101),
                       dict(since=T0 + 20, until=T0 + 10), dict(actor_id=[])):
            with self.subTest(kwargs=kwargs):
                self.assert_error('INVALID', self.summary, **kwargs)

    def test_summary_refuses_silent_truncation(self):
        self.start()
        from unittest.mock import patch
        with patch.object(activity, 'MAX_SUMMARY_ROWS', 0):
            self.assert_error('TOO_MANY_RECORDS', self.summary)

    def test_list_is_paged_and_scoped_to_actor_and_patient(self):
        self.patient('B')
        expected = [self.start(f't{i}')['activity']['id'] for i in range(3)]
        self.start('other-patient', pid='B')
        self.start('other-actor', actor='staff2')
        first = activity.list_records(self.conn, T0 + 5, 'A', 'paramedic', 'staff1', limit='2')
        self.assertEqual([x['id'] for x in first['activities']], expected[:0:-1])
        self.assertTrue(first['has_more'])
        second = activity.list_records(self.conn, T0 + 5, 'A', 'paramedic', 'staff1',
                                        limit=2, before_id=first['next_before_id'])
        self.assertEqual([x['id'] for x in second['activities']], [expected[0]])
        self.assertFalse(second['has_more'])
        self.assertIsNone(second['next_before_id'])

    def test_list_limit_and_cursor_validation(self):
        for kwargs in (dict(limit=0), dict(limit=True), dict(limit=201),
                       dict(before_id=0), dict(before_id='9' * 20)):
            with self.subTest(kwargs=kwargs):
                self.assert_error('INVALID', activity.list_records, self.conn, T0,
                                  'A', 'paramedic', 'staff1', **kwargs)

    def test_schema_init_is_idempotent_and_preserves_data(self):
        first = self.start()['activity']
        self.conn.executescript(activity.SCHEMA)
        db.init(self.conn)
        self.assertEqual(self.conn.execute('SELECT id FROM work_activities').fetchone()[0], first['id'])

    def test_concurrent_start_and_stop_are_idempotent(self):
        barrier = threading.Barrier(2)

        def create():
            conn = db.connect(self.path)
            try:
                barrier.wait(timeout=5)
                return activity.start(conn, T0, 'A', 'paramedic',
                                      dict(actor_id='staff1', client_activity_id='same', kind='call'))
            finally:
                conn.close()

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            a, b = list(pool.map(lambda _: create(), range(2)))
        self.assertEqual(a['activity']['id'], b['activity']['id'])
        self.assertEqual({a['duplicate'], b['duplicate']}, {False, True})
        barrier = threading.Barrier(2)

        def finish():
            conn = db.connect(self.path)
            try:
                barrier.wait(timeout=5)
                return activity.stop(conn, T0 + 20, 'A', 'paramedic', a['activity']['id'],
                                     dict(actor_id='staff1'))
            finally:
                conn.close()

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            a, b = list(pool.map(lambda _: finish(), range(2)))
        self.assertEqual({a['duplicate'], b['duplicate']}, {False, True})
        self.assertEqual(a['activity']['completed_seconds'], 20)
