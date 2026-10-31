"""Read-only allocation input guidance via real local HTTP. Synthetic data only."""
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from server import allocation_input_review, db, service, worker
from server.app import make_handler
from server.travel import TravelEstimator

NOW = 2_000_000_000


class Runtime:
    def snapshot(self):
        return {'workers': {'planning': {'state': 'RUNNING'}}}


class AllocationInputReviewHTTP(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='allocation-input-http-')
        self.path = str(Path(self.tmp.name) / 'fixture.db')
        self.conn = db.connect(self.path)
        db.init(self.conn)
        self.estimator = TravelEstimator(use_kakao=False)
        service.upsert_hospital(self.conn, dict(id='INPUT-HTTP-H1', name='Synthetic HTTP hospital',
            lat=35.161, lon=126.85, capabilities=['응급의학'], source='synthetic'))
        service.set_resource(self.conn, NOW, 'INPUT-HTTP-H1', 'ed_bed', 3, 0, source='synthetic')
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.path, self.estimator,
            clock=lambda: NOW, runtime=Runtime()))
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.origin = f'http://127.0.0.1:{self.http.server_port}'
        self.pid = 'INPUT-HTTP-P1'

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join(timeout=3)
        self.conn.close()
        self.tmp.cleanup()

    def call(self, path):
        try:
            with urllib.request.urlopen(self.origin + path, timeout=5) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as response:
            with response:
                return response.code, json.load(response)

    def ok(self, path):
        status, data = self.call(path)
        self.assertEqual(status, 200, data)
        return data

    def create(self, **changes):
        payload = dict(reporter_role='transporter', consent=True, age=47, symptoms='발열',
            consciousness='alert', breathing='normal', priority=2, priority_source='expert',
            lat=35.16, lon=126.85, required_capabilities=[], resource_demands={'ed_bed': 1},
            extra={'treatment_profile_id': 'TR_GENERAL_ED'})
        payload.update(changes)
        return service.create_patient(self.conn, NOW, self.pid, payload)

    def endpoint(self, suffix):
        return f'/api/patients/{self.pid}/{suffix}'

    def state(self):
        tables = ('patients', 'inquiries', 'planning_state', 'planning_results', 'events',
                  'voice_sessions', 'voice_utterances', 'voice_revisions', 'voice_requests',
                  'patient_amendments')
        return {table: [tuple(row) for row in self.conn.execute('SELECT * FROM ' + table + ' ORDER BY rowid')]
                for table in tables}

    def test_capabilities_and_dedicated_review_are_read_only(self):
        patient = self.create(priority=None, priority_source='unknown')
        before = self.state()
        capabilities = self.ok('/api/voice/capabilities')
        self.assertEqual(capabilities['allocation_input_review_revision'], allocation_input_review.REVISION)
        data = self.ok(self.endpoint('input-review'))
        self.assertEqual(data['patient_version'], patient['version'])
        review = data['input_review']
        self.assertFalse(review['blocking'])
        self.assertEqual(review['status'], 'REVIEW_REQUIRED')
        priority = next(item for item in review['items'] if item['field'] == 'priority')
        self.assertEqual(priority['used_priority'], 4)
        self.assertEqual(priority['action']['body'], {'patient_id': self.pid})
        self.assertEqual(before, self.state())

    def test_dedicated_route_rejects_any_query_including_blank_or_duplicates(self):
        self.create()
        for query in ('actor_id=', 'actor_id=x', 'actor_id=x&actor_id=y', 'unused=', 'unused=1'):
            with self.subTest(query=query):
                code, _ = self.call(self.endpoint('input-review') + '?' + query)
                self.assertEqual(code, 400)
        code, _ = self.call('/api/patients/INPUT-ABSENT/input-review')
        self.assertEqual(code, 404)

    def test_queued_recommendation_and_candidates_both_include_current_guidance(self):
        self.create(priority=None, priority_source='unknown')
        before = self.state()
        queued = self.ok(self.endpoint('recommendation'))
        self.assertEqual(queued['status'], 'QUEUED')
        self.assertIsNone(queued['result'])
        self.assertTrue(queued['input_review']['requires_review'])
        candidates = self.ok(self.endpoint('candidates'))
        self.assertEqual([item['hospital_id'] for item in candidates['candidates']], ['INPUT-HTTP-H1'])
        self.assertEqual(candidates['input_review'], queued['input_review'])
        self.assertEqual(before, self.state())

    def test_optional_observation_does_not_block_ready_acceptance(self):
        self.create(extra={'treatment_profile_id': 'TR_GENERAL_ED',
            'vitals': {'sbp': None, 'dbp': None},
            'voice_observations': {'fields': {'systolic_bp': None, 'diastolic_bp': None},
                'field_metadata': {name: {'followup_unavailable_for': 'blood_pressure', 'observed_at': NOW-12}
                                   for name in ('systolic_bp', 'diastolic_bp')}}})
        service.send_inquiries(self.conn, NOW, self.pid, ['INPUT-HTTP-H1'])
        iid = self.conn.execute('SELECT id FROM inquiries WHERE patient_id=?', (self.pid,)).fetchone()[0]
        service.respond(self.conn, NOW, iid, 'accept', 'SYNTHETIC-DESK')
        self.assertTrue(worker.run_once(self.path, self.estimator, clock=lambda: NOW, seconds=0))
        before = self.state()
        ready = self.ok(self.endpoint('recommendation'))
        self.assertEqual(ready['status'], 'READY')
        self.assertEqual(ready['input_review']['status'], 'INFORMATION_ONLY')
        self.assertFalse(ready['input_review']['requires_review'])
        self.assertTrue(ready['result']['confirmable_now'])
        self.assertEqual(ready['result']['hospital_id'], 'INPUT-HTTP-H1')
        self.assertEqual(service._inquiry(self.conn, iid)['state'], 'ACCEPTED')
        self.assertEqual(before, self.state())

    def test_private_voice_provenance_is_not_disclosed_in_guidance(self):
        self.create(consciousness='unknown', extra={'voice_observations': {
            'fields': {'consciousness': 'unknown'}, 'actor_id': 'PRIVATE-ACTOR',
            'session_id': 'PRIVATE-SESSION', 'field_metadata': {'consciousness': {
                'needs_clarification': True, 'observed_at': NOW-15,
                'evidence': ['PRIVATE-QUOTE'], 'clarification_evidence': ['PRIVATE-EVIDENCE']}}}})
        review = self.ok(self.endpoint('input-review'))['input_review']
        serialized = json.dumps(review)
        self.assertEqual(review['review_count'], 1)
        for secret in ('PRIVATE-ACTOR', 'PRIVATE-SESSION', 'PRIVATE-QUOTE', 'PRIVATE-EVIDENCE'):
            self.assertNotIn(secret, serialized)
        action = review['items'][0]['action']
        self.assertEqual(action['endpoint'], '/api/voice/sessions')
        self.assertTrue(action['requires_actor'])


if __name__ == '__main__':
    unittest.main()
