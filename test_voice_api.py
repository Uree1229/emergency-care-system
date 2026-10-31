"""Real local HTTP contract tests; no external speech or hospital requests."""
import http.client
import json
import os
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from server import voice_input, service
from server.app import make_handler
from test_server import Base, EST, T0


class VoiceHttp(Base):
    def setUp(self):
        super().setUp()
        self.now, self.counter = T0 + 30, 0
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.path, EST, clock=lambda: self.now))
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.http.server_port}'

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join(timeout=2)
        super().tearDown()

    def call(self, method, path, body=None):
        request = urllib.request.Request(self.url + path, method=method,
            data=None if body is None else json.dumps(body).encode('utf-8'),
            headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as response:
            with response:
                return response.code, json.load(response)

    def common(self, s=None, **extra):
        self.counter += 1
        result = dict(actor_id='crew-http', client_request_id='http-' + str(self.counter), **extra)
        if s:
            result['expected_version'] = s['version']
        return result

    def create(self, **extra):
        status, result = self.call('POST', '/api/voice/sessions', self.common(**extra))
        self.assertEqual(status, 200, result)
        return result['session']

    def step(self, method, s, suffix, **extra):
        status, result = self.call(method, f'/api/voice/sessions/{s["id"]}/{suffix}', self.common(s, **extra))
        self.assertEqual(status, 200, result)
        return result['session']

    def test_all_draft_routes_to_candidate_recommendation(self):
        self.assertEqual(self.call('GET', '/api/voice/capabilities')[0], 200)
        self.assertIn('supported_fields', self.call('GET', '/api/voice/terms')[1])
        s = self.create()
        path = f'/api/voice/sessions/{s["id"]}'
        self.assertEqual(self.call('GET', path + '?actor_id=crew-http')[1]['session']['id'], s['id'])
        status, preview = self.call('POST', path + '/transcripts', self.common(s,
            utterance_id='part1', text='나이 60세', is_final=False))
        self.assertEqual(status, 200)
        self.assertEqual(preview['session']['fields'], {})
        s = self.step('POST', s, 'transcripts', utterance_id='part1',
                      text='나이 60세. 흉통. 호흡곤란 없음. 혈압 90에 60.', is_final=True)
        s = self.step('POST', s, 'commands', text='혈압을 110에 70으로 수정해')
        self.assertEqual(s['fields']['systolic_bp'], 110)
        s = self.step('PATCH', s, 'fields', changes={'spo2': 98}, change_kind='observation')
        s = self.step('POST', s, 'undo')
        self.assertNotIn('spo2', s['fields'])
        status, spoken = self.call('GET', path + '/readback?actor_id=crew-http&field=blood_pressure')
        self.assertEqual(status, 200, spoken)
        self.assertIn('110', spoken['speak_text'])
        status, command_review = self.call('POST', path + '/commands', self.common(s, text='혈압 얼마로 저장됐어?'))
        self.assertEqual(status, 200, command_review)
        self.assertEqual(command_review['command']['action'], 'review')
        self.assertEqual(self.call('GET', path + '/history?actor_id=crew-http')[0], 200)
        s = self.step('POST', s, 'confirm', confirmed=True)
        status, submitted = self.call('POST', path + '/submit', self.common(s,
            context={'consent': True, 'lat': 35.16, 'lon': 126.85, 'required_capabilities': ['응급의학']}))
        self.assertEqual(status, 200, submitted)
        self.assertEqual(submitted['session']['state'], 'SUBMITTED')
        status, candidates = self.call('GET', submitted['candidates_endpoint'])
        self.assertEqual(status, 200, candidates)
        self.assertEqual(len(candidates['candidates']), 3)
        self.assertEqual(submitted['patient']['inquiries'], [])
        self.assertIn('호흡곤란', submitted['patient']['extra']['voice_observations']['fields']['denied_symptoms'])

    def test_audio_not_configured_and_mocked_audio_through_http(self):
        s = self.create()
        path = f'/api/voice/sessions/{s["id"]}/audio'
        body = self.common(s, utterance_id='a1', audio_base64='mock')
        with patch.dict(os.environ, {'AZURE_SPEECH_KEY': '', 'AZURE_SPEECH_ENDPOINT': ''}):
            status, failed = self.call('POST', path, body)
        self.assertEqual((status, failed['error']), (503, 'STT_NOT_CONFIGURED'))
        with patch.object(voice_input.voice_audio, 'transcribe', return_value={
                'text': '맥박 100', 'provider': 'test', 'language': 'ko-KR', 'is_final': True,
                'duration_seconds': 1.0}):
            status, result = self.call('POST', path, body)
        self.assertEqual(status, 200, result)
        self.assertEqual(result['session']['fields']['pulse'], 100)

    def test_scope_query_types_unknown_commands_and_empty_confirm(self):
        s = self.create()
        path = f'/api/voice/sessions/{s["id"]}'
        for suffix, expected in (('?actor_id=other', 403), ('?actor_id=', 400),
                                 ('?actor_id=crew-http&actor_id=crew-http', 400),
                                 ('?actor_id=crew-http&surprise=1', 400)):
            self.assertEqual(self.call('GET', path + suffix)[0], expected)
        self.assertEqual(self.call('POST', path + '/confirm', self.common(s, confirmed=True))[0], 400)
        self.assertEqual(self.call('POST', path + '/commands', self.common(s, text='확인, 전송해'))[0], 422)
        self.assertEqual(self.call('PATCH', path + '/fields', self.common(s,
            changes={'priority': 4}, change_kind='correction'))[0], 400)
        bad = self.common(s, changes={'age': 60}, change_kind='correction')
        bad['expected_version'] = True
        self.assertEqual(self.call('PATCH', path + '/fields', bad)[0], 400)
        self.assertEqual(self.call('POST', path + '/transcripts', {'actor_id': 'crew-http'})[0], 400)

    def test_payload_limit_checked_before_read_and_invalid_json_is_400(self):
        conn = http.client.HTTPConnection('127.0.0.1', self.http.server_port, timeout=5)
        conn.request('POST', '/api/voice/sessions', body=None,
                     headers={'Content-Length': str(voice_input.MAX_HTTP_BYTES + 1)})
        response = conn.getresponse()
        self.assertEqual(response.status, 413)
        response.read()
        conn.close()
        conn = http.client.HTTPConnection('127.0.0.1', self.http.server_port, timeout=5)
        conn.request('POST', '/api/voice/sessions', body=b'{')
        response = conn.getresponse()
        self.assertEqual(response.status, 400)
        self.assertEqual(json.load(response)['error'], 'INVALID_JSON')
        conn.close()

    def test_existing_patient_correction_reaches_hospital_notice_route(self):
        self.patient()
        iid = self.accepted('A', 'H1')
        s = self.create(patient_id='A')
        s = self.step('PATCH', s, 'fields', changes={'consciousness': 'impaired'}, change_kind='correction')
        s = self.step('POST', s, 'confirm', confirmed=True)
        s = self.step('POST', s, 'submit')
        self.assertEqual(s['state'], 'SUBMITTED')
        self.assertEqual(service._inquiry(self.conn, iid)['state'], 'CANCELLED')
        status, events = self.call('GET', '/api/hospitals/H1/patients/A/important-events?actor_id=desk-1')
        self.assertEqual(status, 200, events)
        self.assertTrue(any(event['type'] == 'patient_information_amended' for event in events['items']))
