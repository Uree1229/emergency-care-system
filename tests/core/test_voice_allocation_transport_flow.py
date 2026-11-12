"""Voice-originated allocation and transport guards over real local HTTP.

Synthetic patients/hospitals and a private DB only. No UI, microphone, external
route API or real hospital request. Worker seconds=0 uses its validated fallback;
the separate rehearsal exercises the full bounded CP-SAT worker.
"""
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from server import db, inquiry_workflow, service, transport_reassignment, worker
from server.app import make_handler
from server.travel import TravelEstimator


NOW = 2_000_000_000
ROOT = Path(__file__).resolve().parents[2]


class Runtime:
    def snapshot(self):
        return {'workers': {'planning': {'state': 'RUNNING'}, 'inquiry': {'state': 'RUNNING'}}}


class VoiceAllocationTransportFlow(unittest.TestCase):
    def setUp(self):
        tmp_root = ROOT / '.codex-build/voice-allocation-e2e-20261030/tmp'
        tmp_root.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='transport-flow-', dir=str(tmp_root))
        self.path = str(Path(self.tmp.name) / 'synthetic.db')
        self.conn = db.connect(self.path)
        db.init(self.conn)
        self.now = NOW
        self.counter = 0
        self.actor = 'SYNTHETIC-TRANSPORT-CREW'
        self.estimator = TravelEstimator(use_kakao=False)
        for hid, lat in [('VT-H1', 35.161), ('VT-H2', 35.18)]:
            service.upsert_hospital(self.conn, dict(id=hid, name='Synthetic ' + hid,
                lat=lat, lon=126.85, capabilities=['응급의학'], source='synthetic'))
            for rtype, capacity in [('ed_bed', 4), ('special_team', 2)]:
                service.set_resource(self.conn, self.now, hid, rtype, capacity, 0, source='synthetic')
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.path, self.estimator,
            clock=lambda: self.now, runtime=Runtime()))
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.origin = f'http://127.0.0.1:{self.http.server_port}'

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join(timeout=3)
        self.conn.close()
        self.tmp.cleanup()

    def call(self, method, path, body=None):
        request = urllib.request.Request(self.origin + path, method=method,
            data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None,
            headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=8) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as response:
            with response:
                return response.code, json.load(response)

    def ok(self, method, path, body=None):
        status, data = self.call(method, path, body)
        self.assertEqual(status, 200, data)
        return data

    def common(self, session=None, **extra):
        self.counter += 1
        return dict(actor_id=self.actor, client_request_id='transport-flow-' + str(self.counter),
            **({'expected_version': session['version']} if session else {}), **extra)

    def create_voice_patient(self, *, special=False):
        session = self.ok('POST', '/api/voice/sessions', self.common())['session']
        session = self.ok('POST', f'/api/voice/sessions/{session["id"]}/transcripts',
            self.common(session, text='47세 남성. 흉통. 의식 명료. 호흡 정상. 케이타스 2. 심전도 정상.',
                is_final=True, mode='auto', utterance_id='synthetic-utterance-' + str(self.counter)))['session']
        session = self.ok('POST', f'/api/voice/sessions/{session["id"]}/confirm',
                          self.common(session, confirmed=True))['session']
        demands = {'ed_bed': 1, **({'special_team': 1} if special else {})}
        result = self.ok('POST', f'/api/voice/sessions/{session["id"]}/submit',
            self.common(session, context={'consent': True, 'lat': 35.16, 'lon': 126.85,
                'required_capabilities': ['응급의학'], 'resource_demands': demands},
                start_inquiry=True, inquiry_policy='fixed_3'))
        self.assertEqual(result['patient']['state'], 'WAITING')
        return result['patient']['id']

    def patient(self, pid):
        return self.ok('GET', f'/api/patients/{pid}')

    def inquiry(self, pid, hid):
        return next(q for q in self.patient(pid)['inquiries'] if q['hospital_id'] == hid)

    def accept(self, pid, hid):
        inquiry = self.inquiry(pid, hid)
        result = self.ok('POST', f'/api/inquiries/{inquiry["id"]}/respond',
            {'action': 'accept', 'handled_by': 'SYNTHETIC-DESK', 'expected_version': inquiry['version']})
        return result['inquiry']

    def review(self, pid):
        return self.ok('GET', f'/api/patients/{pid}/transport-review')

    def setup_transport(self, *, special=False):
        pid = self.create_voice_patient(special=special)
        self.assertTrue(inquiry_workflow.run_once(self.path, self.estimator, clock=lambda: self.now))
        inquiry = self.accept(pid, 'VT-H1')
        self.assertTrue(worker.run_once(self.path, self.estimator, clock=lambda: self.now, seconds=0))
        recommendation = self.ok('GET', f'/api/patients/{pid}/recommendation')
        self.assertEqual(recommendation['status'], 'READY')
        self.assertEqual(recommendation['result']['hospital_id'], 'VT-H1')
        self.assertTrue(recommendation['result']['confirmable_now'])
        self.assertIsNone(self.patient(pid)['destination_hospital_id'])
        self.ok('POST', f'/api/patients/{pid}/confirm', {'inquiry_id': inquiry['id']})
        self.ok('POST', f'/api/patients/{pid}/transport-review', {'enabled': True})
        self.now += 1
        self.ok('POST', f'/api/patients/{pid}/depart', {})
        self.assertEqual(self.patient(pid)['state'], 'IN_TRANSIT')
        return pid

    def update_resource(self, rtype, capacity, occupied):
        self.now += 1
        return self.ok('PUT', f'/api/hospitals/VT-H1/resources/{rtype}',
                       {'capacity': capacity, 'occupied': occupied, 'handled_by': 'SYNTHETIC-DESK'})

    def decide(self, pid, decision, review=None):
        current = (review or self.review(pid))['review']
        return self.call('POST', f'/api/hospitals/VT-H1/patients/{pid}/decision',
            {'decision': decision, 'note': 'Synthetic condition decision',
             'handled_by': 'SYNTHETIC-DESK', 'expected_review_version': current['version'],
             'expected_risk_signature': current['risk_signature']})

    def test_voice_to_restricted_transport_requires_new_acceptance_and_manual_change_before_arrival(self):
        pid = self.setup_transport()
        self.now += 1
        self.ok('POST', '/api/hospitals/VT-H1/restrictions',
                {'scope': '응급의학', 'reason': 'Synthetic unavailable', 'handled_by': 'SYNTHETIC-DESK'})
        current = self.review(pid)
        self.assertEqual(current['review']['state'], 'RECHECK_REQUIRED')
        self.assertEqual(current['workflow']['state'], 'NOT_STARTED')
        self.assertFalse(self.patient(pid)['needs_adjustment'])
        self.assertEqual(self.patient(pid)['destination_hospital_id'], 'VT-H1')
        status, _ = self.decide(pid, 'adjust', current)
        self.assertEqual(status, 200)
        self.assertTrue(transport_reassignment.run_once(self.path, self.estimator, clock=lambda: self.now))
        pending = self.inquiry(pid, 'VT-H2')
        self.assertEqual(pending['state'], 'PENDING')
        status, rejected = self.call('POST', f'/api/patients/{pid}/change-destination',
                                    {'inquiry_id': pending['id'], 'lat': 35.165, 'lon': 126.85})
        self.assertEqual(status, 409)
        self.assertEqual(rejected['error'], 'NOT_ACCEPTED')
        self.assertEqual(self.patient(pid)['destination_hospital_id'], 'VT-H1')
        accepted = self.accept(pid, 'VT-H2')
        transport_reassignment.run_once(self.path, self.estimator, clock=lambda: self.now)
        self.assertEqual(self.review(pid)['workflow']['state'], 'AWAITING_CHANGE')
        # A new hospital acceptance still does not change the destination.
        self.assertEqual(self.patient(pid)['destination_hospital_id'], 'VT-H1')
        changed = self.ok('POST', f'/api/patients/{pid}/change-destination',
                          {'inquiry_id': accepted['id'], 'lat': 35.165, 'lon': 126.85})
        self.assertEqual((changed['state'], changed['destination_hospital_id']), ('IN_TRANSIT', 'VT-H2'))
        self.assertFalse(changed['needs_adjustment'])
        self.assertEqual(self.review(pid)['review']['state'], 'MONITORING')
        self.now += 1
        arrived = self.ok('POST', f'/api/hospitals/VT-H2/patients/{pid}/arrive',
                          {'handled_by': 'SYNTHETIC-DESK'})
        self.assertEqual(arrived['state'], 'ARRIVED')
        self.assertEqual(self.review(pid)['review']['state'], 'COMPLETED')
        self.assertEqual(service.expected_demand(self.conn, 'VT-H1').get('ed_bed', 0), 0)
        self.assertEqual(service.expected_demand(self.conn, 'VT-H2').get('ed_bed', 0), 0)
        resource = next(r for r in service.resource_view(self.conn, 'VT-H2', self.now) if r['resource_type'] == 'ed_bed')
        self.assertEqual(resource['occupied'], 1)

    def test_resource_loss_targets_only_voice_patient_requiring_that_resource(self):
        special = self.setup_transport(special=True)
        ordinary = self.setup_transport()
        ordinary_before = self.review(ordinary)['review']
        self.update_resource('special_team', 2, 2)
        affected = self.review(special)['review']
        unaffected = self.review(ordinary)['review']
        self.assertEqual(affected['state'], 'RECHECK_REQUIRED')
        self.assertEqual([issue['resource_type'] for issue in affected['evidence']['issues']], ['special_team'])
        self.assertEqual(unaffected['state'], 'MONITORING')
        self.assertEqual(unaffected['version'], ordinary_before['version'])
        for pid in (special, ordinary):
            patient = self.patient(pid)
            self.assertEqual(patient['destination_hospital_id'], 'VT-H1')
            self.assertFalse(patient['needs_adjustment'])
            self.assertEqual(self.review(pid)['workflow']['state'], 'NOT_STARTED')

    def test_zero_new_patient_free_capacity_preserves_committed_voice_patient(self):
        pid = self.setup_transport()
        # The one remaining bed is already accounted for by this commitment.
        self.update_resource('ed_bed', 1, 0)
        resource = next(r for r in service.resource_view(self.conn, 'VT-H1', self.now) if r['resource_type'] == 'ed_bed')
        self.assertEqual((resource['free'], resource['expected']), (0, 1))
        self.assertEqual(self.review(pid)['review']['state'], 'MONITORING')
        self.assertIsNone(self.review(pid)['review']['risk_signature'])
        self.assertEqual(self.patient(pid)['destination_hospital_id'], 'VT-H1')
        self.assertFalse(self.patient(pid)['needs_adjustment'])

    def test_hospital_keep_acknowledges_condition_without_new_inquiries_or_reroute(self):
        pid = self.setup_transport()
        self.update_resource('ed_bed', 1, 1)
        current = self.review(pid)
        self.assertEqual(current['review']['state'], 'RECHECK_REQUIRED')
        before = self.patient(pid)['inquiries']
        status, _ = self.decide(pid, 'keep', current)
        self.assertEqual(status, 200)
        self.assertFalse(transport_reassignment.run_once(self.path, self.estimator, clock=lambda: self.now))
        after = self.review(pid)
        self.assertEqual(after['review']['state'], 'MONITORING')
        self.assertEqual(after['review']['acknowledged_signature'], current['review']['risk_signature'])
        self.assertEqual(self.patient(pid)['inquiries'], before)
        self.assertEqual(self.patient(pid)['destination_hospital_id'], 'VT-H1')
        self.assertFalse(self.patient(pid)['needs_adjustment'])

    def test_obsolete_condition_decision_cannot_start_alternative_search(self):
        pid = self.setup_transport()
        self.update_resource('ed_bed', 1, 1)
        old = self.review(pid)
        self.update_resource('ed_bed', 0, 0)
        status, data = self.decide(pid, 'adjust', old)
        self.assertEqual(status, 409)
        self.assertEqual(data['error'], 'TRANSPORT_REVIEW_CONFLICT')
        self.assertEqual(self.review(pid)['workflow']['state'], 'NOT_STARTED')
        self.assertEqual(self.patient(pid)['destination_hospital_id'], 'VT-H1')
        self.assertFalse(self.patient(pid)['needs_adjustment'])


if __name__ == '__main__':
    unittest.main()
