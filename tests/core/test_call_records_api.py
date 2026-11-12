"""Call record HTTP contracts with temporary synthetic cases and no outgoing calls."""
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from server import db, service
from server.app import make_handler
from test_server import Base, EST, T0


class CallRecordsHttp(Base):
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

    def payload(self, key='call-1', hospital='H1', **changes):
        value = dict(actor_id='crew-1', client_record_id=key, hospital_id=hospital,
                     call_started_at=T0 + 5, call_ended_at=T0 + 15,
                     reported_outcome='can_accept', counterparty_role='emergency_staff',
                     counterparty_label='시연 응급실 담당', note='가상 환자에 대한 통화 전달 메모')
        value.update(changes)
        return value

    def create(self, **changes):
        status, result = self.call('POST', '/api/patients/A/call-records', self.payload(**changes))
        self.assertEqual(status, 200, result)
        return result['record']

    def confirmation(self, key='confirm-1', **changes):
        value = dict(actor_id='desk-1', client_confirmation_id=key, decision='confirm')
        value.update(changes)
        return value

    def test_phone_only_contact_and_confirmation_do_not_create_medical_acceptance(self):
        before_revision = self.conn.execute('SELECT revision FROM planning_state').fetchone()[0]
        before_events = self.conn.execute('SELECT COUNT(*) FROM events').fetchone()[0]
        before_patient = dict(service._patient(self.conn, 'A'))
        record = self.create(hospital='H3')
        rid = record['id']
        self.assertEqual(record['confirmation_status'], 'unconfirmed')
        self.assertEqual(record['recorded_at'], T0 + 20)
        self.assertEqual(record['call_ended_at'], T0 + 15)
        hospital = '/api/hospitals/H3/patients/A'
        status, listing = self.call('GET', hospital + '/call-records')
        self.assertEqual(status, 200)
        self.assertEqual([r['id'] for r in listing['records']], [rid])
        status, result = self.call('POST', f'{hospital}/call-records/{rid}/confirmation', self.confirmation())
        self.assertEqual(status, 200, result)
        self.assertEqual(result['record']['confirmation_status'], 'confirmed')
        self.assertEqual(result['record']['confirmation_scope'], 'record_content_only')
        self.assertEqual(result['record']['operational_effect'], 'none')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM inquiries').fetchone()[0], 0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM conversation_hospital_sessions').fetchone()[0], 0)
        self.assertEqual(dict(service._patient(self.conn, 'A')), before_patient)
        self.assertEqual(self.conn.execute('SELECT revision FROM planning_state').fetchone()[0], before_revision)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM events').fetchone()[0], before_events)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM work_activities').fetchone()[0], 0)
        self.assertEqual(self.call('GET', hospital + '/conversation')[0], 403)
        self.assertEqual(self.call('GET', hospital + '/important-events?actor_id=desk-1')[0], 403)
        self.assertEqual(self.call('GET', '/api/hospitals/H3/incoming')[1]['incoming'], [])
        status, activity = self.call('POST', hospital + '/work-activities', {
            'actor_id': 'desk-1', 'client_activity_id': 'review-1', 'kind': 'request_review'})
        self.assertEqual(status, 200)
        self.now += 5
        self.call('POST', f'{hospital}/work-activities/{activity["activity"]["id"]}/stop', {'actor_id': 'desk-1'})
        total = self.call('GET', hospital + '/work-activities/summary?actor_id=desk-1')[1]
        self.assertEqual(total['totals']['completed_active_seconds'], 5)

    def test_existing_inquiry_stays_pending_and_no_conversation_join_is_created(self):
        service.send_inquiries(self.conn, T0, 'A', ['H1'])
        iid = self.inquiry_id('A', 'H1')
        before_revision = self.conn.execute('SELECT revision FROM planning_state').fetchone()[0]
        record = self.create()
        self.call('POST', f'/api/hospitals/H1/patients/A/call-records/{record["id"]}/confirmation', self.confirmation())
        self.assertEqual(service._inquiry(self.conn, iid)['state'], 'PENDING')
        self.assertIsNone(service._patient(self.conn, 'A')['destination_hospital_id'])
        self.assertEqual(self.conn.execute('SELECT revision FROM planning_state').fetchone()[0], before_revision)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM conversation_hospital_sessions').fetchone()[0], 0)

    def test_scope_pagination_and_same_key_retry(self):
        first = self.create()
        retry = self.call('POST', '/api/patients/A/call-records', self.payload())[1]
        self.assertTrue(retry['duplicate'])
        self.assertEqual(retry['record']['id'], first['id'])
        self.assertEqual(self.call('POST', '/api/patients/A/call-records', self.payload(note='changed'))[0], 409)
        second = self.create(key='call-2', hospital='H2')
        page = self.call('GET', '/api/patients/A/call-records?limit=1')[1]
        self.assertTrue(page['has_more'])
        self.assertEqual(page['records'][0]['id'], second['id'])
        older = self.call('GET', f'/api/patients/A/call-records?before_id={page["next_before_id"]}')[1]
        self.assertEqual([r['id'] for r in older['records']], [first['id']])
        h1 = '/api/hospitals/H1/patients/A/call-records'
        self.assertEqual([r['id'] for r in self.call('GET', h1)[1]['records']], [first['id']])
        self.assertEqual(self.call('GET', f'{h1}/{second["id"]}')[0], 404)
        self.assertEqual(self.call('POST', f'{h1}/{second["id"]}/confirmation', self.confirmation())[0], 404)
        self.patient('B')
        self.assertEqual(self.call('GET', f'/api/patients/B/call-records/{first["id"]}')[0], 404)

    def test_corrections_preserve_original_and_require_fresh_counterparty_review(self):
        old = self.create()
        path = '/api/hospitals/H1/patients/A/call-records'
        status, confirmed = self.call('POST', f'{path}/{old["id"]}/confirmation', self.confirmation())
        self.assertEqual(status, 200)
        self.now += 10
        corrected = self.create(key='correction-1', supersedes_id=old['id'], note='정정한 통화 전달 메모')
        self.assertEqual(corrected['confirmation_status'], 'unconfirmed')
        prior = self.call('GET', f'/api/patients/A/call-records/{old["id"]}')[1]['record']
        self.assertEqual(prior['note'], old['note'])
        self.assertEqual(prior['confirmation_status'], 'confirmed')
        self.assertEqual(prior['superseded_by_id'], corrected['id'])
        retry = self.call('POST', f'{path}/{old["id"]}/confirmation', self.confirmation())[1]
        self.assertTrue(retry['duplicate'])
        self.assertEqual(self.call('POST', f'{path}/{old["id"]}/confirmation', self.confirmation('new-confirm'))[0], 409)
        self.assertEqual(self.call('POST', f'/api/patients/A/call-records/{corrected["id"]}/confirmation',
                                   self.confirmation())[0], 403)
        status, disputed = self.call('POST', f'{path}/{corrected["id"]}/confirmation', self.confirmation(
            'dispute-1', decision='dispute', reason='전달 내용 확인이 필요함'))
        self.assertEqual(status, 200)
        self.assertEqual(disputed['record']['confirmation_status'], 'disputed')

    def test_hospital_written_result_can_be_reviewed_by_paramedic_side(self):
        self.create()
        payload = self.payload('hospital-call', actor_id='desk-2', counterparty_role='paramedic')
        payload.pop('hospital_id')
        path = '/api/hospitals/H1/patients/A/call-records'
        status, created = self.call('POST', path, payload)
        self.assertEqual(status, 200, created)
        record = created['record']
        self.assertEqual(record['author_role'], 'hospital')
        status, result = self.call('POST', f'/api/patients/A/call-records/{record["id"]}/confirmation',
                                   self.confirmation(actor_id='crew-2'))
        self.assertEqual(status, 200)
        self.assertEqual(result['record']['confirmation_status'], 'confirmed')
        self.assertEqual(self.call('POST', path, self.payload('wrong-hid', hospital='H2'))[0], 400)

    def test_bad_filters_payload_and_large_record_ids_return_client_errors(self):
        path = '/api/patients/A/call-records'
        for suffix in ('?limit=', '?limit=0', '?limit=101', '?before_id=',
                       '?limit=1&limit=2', '?actor_id=', '?hospital_id=H2'):
            with self.subTest(suffix=suffix):
                self.assertEqual(self.call('GET', path + suffix)[0], 400)
        for changes in ({'call_ended_at': T0 + 30}, {'call_started_at': True},
                        {'call_started_at': T0 - 1}, {'note': 'x' * 1001},
                        {'reported_outcome': 'ACCEPTED'}, {'role': 'system'}):
            with self.subTest(changes=list(changes)):
                self.assertEqual(self.call('POST', path, self.payload(**changes))[0], 400)
        huge_id = '9' * 4301
        self.assertEqual(self.call('GET', f'{path}/{huge_id}')[0], 400)
        self.assertEqual(self.call('POST', f'{path}/{huge_id}/confirmation', self.confirmation())[0], 400)
        self.assertEqual(self.call('POST', path, [self.payload()])[0], 400)

    def test_reinitialization_preserves_phone_notes_confirmations_and_patient(self):
        record = self.create()
        self.call('POST', f'/api/hospitals/H1/patients/A/call-records/{record["id"]}/confirmation', self.confirmation())
        before = dict(service._patient(self.conn, 'A'))
        db.init(self.conn)
        db.init(self.conn)
        status, result = self.call('GET', f'/api/patients/A/call-records/{record["id"]}')
        self.assertEqual(status, 200)
        self.assertEqual(result['record']['confirmation_status'], 'confirmed')
        self.assertEqual(dict(service._patient(self.conn, 'A')), before)


if __name__ == '__main__':
    import unittest
    unittest.main()
