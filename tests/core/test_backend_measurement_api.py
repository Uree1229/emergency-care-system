"""HTTP integration for the server-only work and important-event contracts.

Uses temporary databases and synthetic cases; no public APIs or team UIs.
"""
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from server import db, service, transport_review
from server.app import make_handler
from test_server import Base, EST, T0


class MeasurementHttp(Base):
    def setUp(self):
        super().setUp()
        self.patient()
        self.now = T0
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(
            self.path, EST, clock=lambda: self.now))
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f'http://127.0.0.1:{self.http.server_port}'

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join(timeout=2)
        super().tearDown()

    def call(self, method, path, body=None):
        request = urllib.request.Request(
            self.base_url + path, method=method,
            data=None if body is None else json.dumps(body).encode('utf-8'),
            headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            with error:
                return error.code, json.load(error)

    def test_activity_roundtrip_uses_server_time_and_unions_overlap(self):
        path = '/api/patients/A/work-activities'
        first = {'actor_id': 'crew-1', 'client_activity_id': 'request-1', 'kind': 'patient_input'}
        status, result = self.call('POST', path, first)
        self.assertEqual(status, 200)
        a = result['activity']['id']
        self.now += 10
        retry = self.call('POST', path, first)[1]
        self.assertTrue(retry['duplicate'])
        self.assertEqual(retry['activity']['started_at'], T0)
        status, second = self.call('POST', path, {
            'actor_id': 'crew-1', 'client_activity_id': 'request-2', 'kind': 'call'})
        self.assertEqual(status, 200)
        b = second['activity']['id']
        self.now = T0 + 30
        self.assertEqual(self.call('POST', f'{path}/{a}/stop', {'actor_id': 'crew-1'})[0], 200)
        self.now = T0 + 40
        stopped = self.call('POST', f'{path}/{b}/stop', {'actor_id': 'crew-1'})[1]
        self.assertEqual(stopped['activity']['completed_seconds'], 30)
        self.now += 20
        self.assertTrue(self.call('POST', f'{path}/{b}/stop', {'actor_id': 'crew-1'})[1]['duplicate'])
        status, summary = self.call('GET', path + '/summary?actor_id=crew-1')
        self.assertEqual(status, 200)
        self.assertEqual(summary['totals']['completed_raw_seconds'], 60)
        self.assertEqual(summary['totals']['completed_active_seconds'], 40)
        self.assertEqual(summary['totals']['completed_overlap_seconds'], 20)
        self.assertFalse(summary['measurement']['inferred_from_workflow_waiting'])
        status, page = self.call('GET', path + '?actor_id=crew-1&limit=1')
        self.assertEqual(status, 200)
        self.assertEqual(len(page['activities']), 1)
        self.assertTrue(page['has_more'])
        status, older = self.call('GET', path + f'?actor_id=crew-1&before_id={page["next_before_id"]}')
        self.assertEqual(status, 200)
        self.assertEqual([x['id'] for x in older['activities']], [a])

    def test_open_work_is_not_counted_as_completed_or_inferred_from_wait(self):
        path = '/api/patients/A/work-activities'
        self.call('POST', path, {'actor_id': 'crew-2', 'client_activity_id': 'left-open', 'kind': 'request_view'})
        self.now += 90
        status, result = self.call('GET', path + '/summary?actor_id=crew-2')
        self.assertEqual(status, 200)
        self.assertEqual(result['totals']['open_activity_count'], 1)
        self.assertEqual(result['totals']['completed_active_seconds'], 0)
        self.assertEqual(result['totals']['open_elapsed_union_seconds'], 90)

    def test_hospital_activity_scope_and_validation(self):
        path = '/api/hospitals/H1/patients/A/work-activities'
        payload = {'actor_id': 'desk-1', 'client_activity_id': 'hospital-1', 'kind': 'request_review'}
        self.assertEqual(self.call('POST', path, payload)[0], 404)
        service.send_inquiries(self.conn, T0, 'A', ['H1'])
        status, started = self.call('POST', path, payload)
        self.assertEqual(status, 200)
        aid = started['activity']['id']
        self.assertEqual(started['activity']['role'], 'hospital')
        self.assertEqual(self.call('POST', f'{path}/{aid}/stop', {'actor_id': 'other-desk'})[0], 404)
        self.assertEqual(self.call('POST', f'/api/patients/A/work-activities/{aid}/stop',
                                   {'actor_id': 'desk-1'})[0], 404)
        self.assertEqual(self.call('POST', path, dict(payload, notes='patient narrative'))[0], 400)
        for suffix in ('?actor_id=desk-1&limit=0', '?actor_id=desk-1&limit=101',
                       '?actor_id=desk-1&before_id=1.5', '/summary?since=-1',
                       '/summary?until=nan', '/summary?since=999999999999999999999999'):
            with self.subTest(suffix=suffix):
                self.assertEqual(self.call('GET', path + suffix)[0], 400)

    def test_measurement_schema_is_additive_and_does_not_schedule_planning(self):
        path = '/api/patients/A/work-activities'
        version = self.conn.execute('SELECT revision FROM planning_state WHERE id=1').fetchone()[0]
        events = self.conn.execute('SELECT COUNT(*) FROM events').fetchone()[0]
        before = dict(service._patient(self.conn, 'A'))
        result = self.call('POST', path, {'actor_id': 'crew-1', 'client_activity_id': 'one', 'kind': 'chat'})[1]
        self.now += 12
        self.call('POST', f'{path}/{result["activity"]["id"]}/stop', {'actor_id': 'crew-1'})
        self.assertEqual(self.conn.execute('SELECT revision FROM planning_state WHERE id=1').fetchone()[0], version)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM events').fetchone()[0], events)
        self.assertEqual(dict(service._patient(self.conn, 'A')), before)
        db.init(self.conn)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM work_activities').fetchone()[0], 1)
        self.assertEqual(dict(service._patient(self.conn, 'A')), before)

    def confirm_with_cancelled_alternative(self):
        service.send_inquiries(self.conn, T0, 'A', ['H1', 'H2'])
        service.respond(self.conn, T0 + 5, self.inquiry_id('A', 'H1'), 'accept', 'desk-1')
        service.confirm_destination(self.conn, T0 + 10, 'A', self.inquiry_id('A', 'H1'))
        self.now = T0 + 20
        return self.conn.execute("SELECT id FROM events WHERE patient_id='A' "
                                 "AND hospital_id='H2' AND type='inquiry_closed'").fetchone()[0]

    def test_event_get_does_not_mark_delivered_and_ack_is_not_medical_acceptance(self):
        event_id = self.confirm_with_cancelled_alternative()
        path = '/api/hospitals/H2/patients/A/important-events'
        status, listing = self.call('GET', path + '?actor_id=desk-2')
        self.assertEqual(status, 200)
        item = next(x for x in listing['items'] if x['event_id'] == event_id)
        self.assertIsNone(item['delivered_at'])
        self.assertIsNone(item['acknowledged_at'])
        body = {'actor_id': 'desk-2'}
        self.assertEqual(self.call('POST', f'{path}/{event_id}/acknowledge', body)[0], 409)
        version = self.conn.execute('SELECT revision FROM planning_state WHERE id=1').fetchone()[0]
        events = self.conn.execute('SELECT COUNT(*) FROM events').fetchone()[0]
        delivered = self.call('POST', f'{path}/{event_id}/delivered', body)
        self.assertEqual(delivered[0], 200)
        self.assertEqual(delivered[1]['event']['delivered_at'], self.now)
        self.now += 7
        acknowledged = self.call('POST', f'{path}/{event_id}/acknowledge', body)
        self.assertEqual(acknowledged[0], 200)
        self.assertEqual(acknowledged[1]['event']['acknowledged_at'], self.now)
        self.now += 5
        duplicate = self.call('POST', f'{path}/{event_id}/acknowledge', body)[1]
        self.assertTrue(duplicate['duplicate'])
        self.assertEqual(duplicate['event']['acknowledged_at'], T0 + 27)
        self.assertEqual(service._patient(self.conn, 'A')['destination_hospital_id'], 'H1')
        self.assertEqual(service._inquiry(self.conn, self.inquiry_id('A', 'H2'))['state'], 'CANCELLED')
        self.assertEqual(self.conn.execute('SELECT revision FROM planning_state WHERE id=1').fetchone()[0], version)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM events').fetchone()[0], events)
        self.assertEqual(self.call('GET', path + '?actor_id=desk-2&unacknowledged_only=true')[1]['items'], [])
        self.assertEqual(self.call('GET', path + '?actor_id=another-desk&unacknowledged_only=true')[1]['items'][0]['event_id'], event_id)

    def test_event_cursor_unread_reconnect_and_scope_isolation(self):
        event_id = self.confirm_with_cancelled_alternative()
        path = '/api/patients/A/important-events'
        status, first = self.call('GET', path + '?actor_id=crew-1&limit=1')
        self.assertEqual(status, 200)
        self.assertTrue(first['has_more'])
        status, second = self.call('GET', path + f'?actor_id=crew-1&after_id={first["next_after_id"]}')
        self.assertEqual(status, 200)
        self.assertTrue(all(x['event_id'] > first['next_after_id'] for x in second['items']))
        status, unread = self.call('GET', path + '?actor_id=crew-1&unacknowledged_only=true')
        self.assertEqual(status, 200)
        self.assertIn(first['items'][0]['event_id'], [x['event_id'] for x in unread['items']])
        self.assertIsNone(unread['next_after_id'])
        self.assertEqual(self.call('GET', path + '?actor_id=crew-1&after_id=1&unacknowledged_only=true')[0], 400)
        self.assertEqual(self.call('GET', '/api/hospitals/H3/patients/A/important-events?actor_id=desk-3')[0], 403)
        self.assertEqual(self.call('POST', f'/api/hospitals/H1/patients/A/important-events/{event_id}/delivered',
                                   {'actor_id': 'desk-1'})[0], 404)
        self.assertEqual(self.call('GET', path + '?actor_id=crew-1&limit=0')[0], 400)
        self.assertEqual(self.call('GET', path + '?actor_id=crew-1&unacknowledged_only=maybe')[0], 400)
        self.assertEqual(self.call('GET', path)[0], 400)

    def test_recheck_notification_receipt_keeps_clinical_review_pending(self):
        self.confirm_with_cancelled_alternative()
        transport_review.enable(self.conn, T0 + 20, 'A', True)
        service.set_resource(self.conn, T0 + 30, 'H1', 'ed_bed', 1, 1)
        with db.transaction(self.conn):
            transport_review.scan_in_transaction(self.conn, T0 + 30)
        self.now = T0 + 35
        path = '/api/hospitals/H1/patients/A/important-events'
        result = self.call('GET', path + '?actor_id=desk-1')[1]
        event = next(x for x in result['items'] if x['type'] == 'transport_recheck_required')
        for action in ('delivered', 'acknowledge'):
            self.assertEqual(self.call('POST', f'{path}/{event["event_id"]}/{action}', {'actor_id': 'desk-1'})[0], 200)
        self.assertEqual(transport_review.view(self.conn, self.now, 'A')['state'], 'RECHECK_REQUIRED')
        self.assertEqual(service._patient(self.conn, 'A')['destination_hospital_id'], 'H1')


    def test_blank_duplicate_filters_and_overlong_path_ids_are_client_errors(self):
        path = '/api/patients/A/work-activities'
        for suffix in ('/summary?actor_id=', '/summary?since=', '/summary?until=',
                       '?actor_id=crew-1&limit=', '/summary?actor_id=one&actor_id=two'):
            with self.subTest(suffix=suffix):
                self.assertEqual(self.call('GET', path + suffix)[0], 400)
        events = '/api/patients/A/important-events'
        for suffix in ('?actor_id=', '?actor_id=crew-1&limit=',
                       '?actor_id=crew-1&after_id=', '?actor_id=crew-1&unacknowledged_only=',
                       '?actor_id=one&actor_id=two'):
            with self.subTest(suffix=suffix):
                self.assertEqual(self.call('GET', events + suffix)[0], 400)
        long_id = '9' * 4301
        for target in (f'{path}/{long_id}/stop', f'{events}/{long_id}/delivered',
                       f'{events}/{long_id}/acknowledge'):
            with self.subTest(target=target[:60]):
                self.assertEqual(self.call('POST', target, {'actor_id': 'crew-1'})[0], 400)


if __name__ == '__main__':
    import unittest
    unittest.main()
