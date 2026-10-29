"""Versioned post-inquiry updates and hospital-wide call inbox HTTP integration."""
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from server import db, inquiry_workflow, service
from server.app import make_handler
from test_server import Base, EST, T0


class UpdateAndInboxHttp(Base):
    def setUp(self):
        super().setUp()
        self.patient()
        self.now = T0 + 20
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
        req = urllib.request.Request(self.base_url + path, method=method,
                                     data=None if body is None else json.dumps(body).encode('utf-8'),
                                     headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            with error:
                return error.code, json.load(error)

    def amendment(self, **changes):
        body = dict(actor_id='crew-1', client_request_id='amend-1', expected_patient_version=1,
                    reason='가상 환자의 추가 상태 확인', changes={'symptoms': '고열과 구토', 'consciousness': 'impaired'})
        body.update(changes)
        return body

    def make_call(self, pid='A', hid='H1', key='call-1', **changes):
        body = dict(actor_id='crew-1', client_record_id=key, hospital_id=hid,
                    call_started_at=T0 + 5, call_ended_at=T0 + 10, reported_outcome='checking',
                    counterparty_role='emergency_staff', note='통화 전달 메모')
        body.update(changes)
        status, result = self.call('POST', f'/api/patients/{pid}/call-records', body)
        self.assertEqual(status, 200, result)
        return result['record']

    def test_amendment_closes_old_acceptance_and_old_screen_cannot_answer_new_round(self):
        service.send_inquiries(self.conn, T0, 'A', ['H1', 'H2'])
        iid = self.inquiry_id('A', 'H1')
        service.respond(self.conn, T0 + 5, iid, 'accept', 'desk-1')
        old_version = service._inquiry(self.conn, iid)['version']
        body = self.amendment()
        status, updated = self.call('POST', '/api/patients/A/amendments', body)
        self.assertEqual(status, 200, updated)
        self.assertEqual(updated['patient']['version'], 2)
        self.assertEqual(updated['patient']['state'], 'WAITING')
        self.assertIsNone(updated['patient']['destination_hospital_id'])
        self.assertEqual({q['state'] for q in updated['patient']['inquiries']}, {'CANCELLED'})
        self.assertTrue(all(q['requires_review_version'] for q in updated['patient']['inquiries']))
        self.assertEqual(self.call('POST', '/api/patients/A/confirm', {'inquiry_id': iid})[0], 409)
        self.assertEqual(self.call('POST', '/api/patients/A/inquiries', {'hospital_ids': ['H1']})[0], 200)
        path = f'/api/inquiries/{iid}/respond'
        self.assertEqual(self.call('POST', path, {'action': 'accept', 'handled_by': 'desk-1'})[1]['error'],
                         'REVIEW_VERSION_REQUIRED')
        self.assertEqual(self.call('POST', path, {'action': 'accept', 'handled_by': 'desk-1',
                                                  'expected_version': old_version})[0], 409)
        latest = self.call('GET', '/api/hospitals/H1/inquiries')[1]['open'][0]
        response = dict(action='accept', handled_by='desk-1', expected_version=latest['version'])
        status, accepted = self.call('POST', path, response)
        self.assertEqual(status, 200, accepted)
        self.assertEqual(accepted['inquiry']['patient_version'], 2)
        status, retry = self.call('POST', path, response)
        self.assertEqual(status, 200, retry)
        self.assertTrue(retry['duplicate'])
        self.assertEqual(self.call('POST', path, dict(response, handled_by='another-desk'))[0], 409)
        self.assertEqual(self.call('POST', path, dict(response, reason='changed'))[0], 409)
        self.assertEqual(self.call('POST', path, dict(response,
            expected_version=accepted['inquiry']['version'], handled_by='another-desk'))[0], 409)
        self.assertEqual(self.call('POST', '/api/patients/A/confirm', {'inquiry_id': iid})[0], 200)
        count = self.conn.execute('SELECT COUNT(*) FROM patient_amendments').fetchone()[0]
        status, retry = self.call('POST', '/api/patients/A/amendments', body)
        self.assertEqual(status, 200)
        self.assertTrue(retry['duplicate'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM patient_amendments').fetchone()[0], count)

    def test_decline_exact_retry_is_idempotent_but_reason_change_is_not(self):
        service.send_inquiries(self.conn, T0, 'A', ['H1'])
        self.call('POST', '/api/patients/A/amendments', self.amendment())
        self.call('POST', '/api/patients/A/inquiries', {'hospital_ids': ['H1']})
        q = self.call('GET', '/api/hospitals/H1/inquiries')[1]['open'][0]
        body = dict(action='decline', handled_by='desk-1', expected_version=q['version'], reason='인력')
        path = f'/api/inquiries/{q["id"]}/respond'
        self.assertEqual(self.call('POST', path, body)[0], 200)
        self.assertTrue(self.call('POST', path, body)[1]['duplicate'])
        self.assertEqual(self.call('POST', path, dict(body, reason='장비'))[0], 409)

    def test_amendment_and_destination_confirmation_cannot_both_commit(self):
        iid = self.accepted('A', 'H1')
        barrier = threading.Barrier(2)
        results = []

        def submit(path, body):
            barrier.wait(timeout=5)
            results.append(self.call('POST', path, body))

        first = threading.Thread(target=submit, args=('/api/patients/A/amendments', self.amendment()))
        second = threading.Thread(target=submit, args=('/api/patients/A/confirm', {'inquiry_id': iid}))
        first.start()
        second.start()
        first.join(timeout=8)
        second.join(timeout=8)
        self.assertFalse(first.is_alive() or second.is_alive())
        self.assertEqual(sorted(status for status, body in results), [200, 409])
        p = service._patient(self.conn, 'A')
        q = service._inquiry(self.conn, iid)
        amendments = self.conn.execute('SELECT COUNT(*) FROM patient_amendments').fetchone()[0]
        if p['state'] == 'CONFIRMED':
            self.assertEqual((q['state'], amendments), ('CONFIRMED', 0))
        else:
            self.assertEqual((p['state'], q['state'], amendments), ('WAITING', 'CANCELLED', 1))

    def test_history_and_hospital_notice_keep_clinical_text_out_of_event_metadata(self):
        service.send_inquiries(self.conn, T0, 'A', ['H1', 'H2'])
        self.assertEqual(self.call('POST', '/api/patients/A/amendments', self.amendment())[0], 200)
        history = self.call('GET', '/api/patients/A/amendments')[1]['amendments']
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]['from_version'], 1)
        self.assertEqual(history[0]['to_version'], 2)
        self.assertEqual(history[0]['after']['symptoms'], '고열과 구토')
        events = self.call('GET', '/api/hospitals/H2/patients/A/important-events?actor_id=desk-2')[1]['items']
        self.assertTrue(any(e['type'] == 'patient_information_amended' for e in events))
        self.assertNotIn('고열과 구토', json.dumps(events, ensure_ascii=False))
        self.assertEqual(self.call('GET', '/api/hospitals/H3/patients/A/important-events?actor_id=desk-3')[0], 403)
        event = self.conn.execute("SELECT data FROM events WHERE type='patient_information_amended' LIMIT 1").fetchone()[0]
        self.assertNotIn('고열과 구토', event)

    def test_active_workflow_requeries_current_version_without_extending_deadline(self):
        inquiry_workflow.start(self.conn, T0, 'A', enabled=True, policy_name='fixed_1')
        inquiry_workflow.run_once(self.path, EST, clock=lambda: self.now)
        old = dict(self.conn.execute("SELECT * FROM inquiry_workflows WHERE patient_id='A'").fetchone())
        initial = service._inquiry(self.conn, self.inquiry_id('A', 'H1'))
        self.now += 1
        self.assertEqual(self.call('POST', '/api/patients/A/amendments', self.amendment())[0], 200)
        inquiry_workflow.run_once(self.path, EST, clock=lambda: self.now)
        current = service._inquiry(self.conn, initial['id'])
        job = self.call('GET', '/api/patients/A/inquiry-workflow')[1]
        self.assertEqual(current['state'], 'PENDING')
        self.assertEqual(current['patient_version'], 2)
        self.assertEqual(current['attempts'], initial['attempts'] + 1)
        self.assertEqual(job['deadline_at'], old['deadline_at'])
        self.assertEqual(job['current_version_contacted_count'], 1)

    def test_amendment_input_and_phase_limits_are_atomic(self):
        path = '/api/patients/A/amendments'
        before = dict(service._patient(self.conn, 'A'))
        for change in ({'lat': 35.2}, {'state': 'CONFIRMED'}, {'consent': True}, {}):
            with self.subTest(change=change):
                self.assertEqual(self.call('POST', path, self.amendment(changes=change))[0], 400)
        self.assertEqual(self.call('POST', path, self.amendment(expected_patient_version=99))[0], 409)
        self.assertEqual(dict(service._patient(self.conn, 'A')), before)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM patient_amendments').fetchone()[0], 0)
        for suffix in ('?limit=', '?limit=0', '?limit=101', '?before_id=', '?limit=1&limit=2'):
            self.assertEqual(self.call('GET', path + suffix)[0], 400)
        iid = self.accepted('A', 'H1')
        service.confirm_destination(self.conn, self.now, 'A', iid)
        current = dict(service._patient(self.conn, 'A'))
        self.assertEqual(self.call('POST', path, self.amendment(expected_patient_version=current['version']))[0], 409)
        self.assertEqual(dict(service._patient(self.conn, 'A')), current)

    def test_hospital_inbox_discovers_phone_only_patients_and_pages_both_directions(self):
        self.patient('B', name='PRIVATE-B')
        self.patient('C', name='PRIVATE-C')
        first = self.make_call('A', 'H1', 'call-A')
        second = self.make_call('B', 'H1', 'call-B')
        self.make_call('C', 'H2', 'call-C')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM inquiries').fetchone()[0], 0)
        path = '/api/hospitals/H1/call-records'
        status, newest = self.call('GET', path + '?limit=1')
        self.assertEqual(status, 200)
        self.assertEqual(newest['records'][0]['id'], second['id'])
        older = self.call('GET', path + f'?before_id={newest["next_before_id"]}')[1]
        self.assertEqual([r['id'] for r in older['records']], [first['id']])
        stream = self.call('GET', path + '?after_id=0&limit=1')[1]
        self.assertEqual(stream['cursor_mode'], 'new_records')
        self.assertEqual(stream['records'][0]['id'], first['id'])
        following = self.call('GET', path + f'?after_id={stream["next_after_id"]}')[1]
        self.assertEqual([r['id'] for r in following['records']], [second['id']])
        self.assertNotIn('PRIVATE-B', json.dumps(following))
        self.assertNotIn('PRIVATE-C', json.dumps(following))
        self.assertEqual(self.call('GET', path + '?patient_id=C')[1]['records'], [])

    def test_inbox_confirmation_refresh_and_corrected_original_filter(self):
        old = self.make_call()
        path = '/api/hospitals/H1/call-records'
        before = self.call('GET', path + '?author_role=paramedic&confirmation_status=unconfirmed')[1]
        self.assertEqual([x['id'] for x in before['records']], [old['id']])
        self.assertTrue(before['stream']['confirmation_changes_require_refresh'])
        self.call('POST', f'/api/hospitals/H1/patients/A/call-records/{old["id"]}/confirmation',
                  {'actor_id': 'desk-1', 'client_confirmation_id': 'check-1', 'decision': 'confirm'})
        self.assertEqual(self.call('GET', path + '?confirmation_status=unconfirmed')[1]['records'], [])
        self.assertEqual(self.call('GET', path + f'?after_id={old["id"]}')[1]['records'], [])
        corrected = self.make_call(key='call-correction', supersedes_id=old['id'], note='정정 기록')
        self.assertEqual([x['id'] for x in self.call('GET', path)[1]['records']], [corrected['id']])
        self.assertEqual(len(self.call('GET', path + '?current_only=false')[1]['records']), 2)
        self.assertEqual([x['id'] for x in self.call('GET', path + '?confirmation_status=unconfirmed')[1]['records']], [corrected['id']])

    def test_inbox_bad_filters_and_read_only_side_effects(self):
        self.make_call()
        path = '/api/hospitals/H1/call-records'
        revision = self.conn.execute('SELECT revision FROM planning_state').fetchone()[0]
        events = self.conn.execute('SELECT COUNT(*) FROM events').fetchone()[0]
        for suffix in ('?limit=', '?after_id=', '?limit=0', '?after_id=0&before_id=1',
                       '?current_only=', '?current_only=yes', '?confirmation_status=',
                       '?patient_id=', '?author_role=doctor', '?limit=1&limit=2', '?actor_id=desk-1'):
            with self.subTest(suffix=suffix):
                self.assertEqual(self.call('GET', path + suffix)[0], 400)
        self.assertEqual(self.call('GET', '/api/hospitals/NO-HOSPITAL/call-records')[0], 404)
        self.assertEqual(self.call('GET', path)[0], 200)
        self.assertEqual(self.conn.execute('SELECT revision FROM planning_state').fetchone()[0], revision)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM events').fetchone()[0], events)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM important_event_receipts').fetchone()[0], 0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM call_record_confirmations').fetchone()[0], 0)

    def test_legacy_database_migration_preserves_inquiries_and_default_response_contract(self):
        target = str(Path(self.path).with_name('legacy.db'))
        conn = db.connect(target)
        try:
            legacy = db.SCHEMA.replace(
                '    requires_review_version INTEGER NOT NULL DEFAULT 0 CHECK(requires_review_version IN (0,1)),\n', '').replace(
                '    review_response_fingerprint TEXT,\n', '')
            conn.executescript(legacy)
            conn.execute("INSERT INTO hospitals(id,name,lat,lon,source) VALUES('L1','legacy',35,126,'synthetic')")
            conn.execute("INSERT INTO patients(id,reporter_role,consent,requested_at,state) VALUES('OLD','transporter',1,?,'WAITING')", (T0,))
            conn.execute("INSERT INTO inquiries(patient_id,hospital_id,state,created_at,last_sent_at,patient_version) "
                         "VALUES('OLD','L1','PENDING',?,?,1)", (T0, T0))
            db.init(conn)
            db.init(conn)
            q = dict(conn.execute('SELECT * FROM inquiries').fetchone())
            self.assertEqual(q['requires_review_version'], 0)
            self.assertIsNone(q['review_response_fingerprint'])
            self.assertEqual(service.respond(conn, T0 + 5, q['id'], 'accept', 'legacy-desk')['inquiry']['state'], 'ACCEPTED')
        finally:
            conn.close()


if __name__ == '__main__':
    import unittest
    unittest.main()
