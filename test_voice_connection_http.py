"""Connection contracts over isolated loopback HTTP; synthetic text, no audio.

This exercises the intended app API sequence without editing or automating the
Claude-owned frontend.  It never opens the user's live demo database or calls
an external speech provider, patient submission, or hospital inquiry endpoint.
"""
import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from server import db
from server.app import make_handler
from server.travel import TravelEstimator


NOW = 1_793_336_000
LONG_STT = ('47세 남성 환자입니다 20분 전부터 가슴통증이 있습니다 '
            '2시간 후면 정상입니다 혈압 120 80 92회 산소 포화도 97% '
            '체온 37.2도 고급 수입 회 혈당 105입니다 도와 주려는 없습니다')


class VoiceConnectionHttp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='voice-connection-http-')
        self.path = str(Path(self.tmp.name) / 'isolated.db')
        self.conn = db.connect(self.path)
        db.init(self.conn)
        self.now = NOW
        self.counter = 0
        self.actor = 'connection-http-synthetic'
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(
            self.path, TravelEstimator(use_kakao=False), clock=lambda: self.now))
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.origin = 'http://127.0.0.1:' + str(self.http.server_port)

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join(timeout=2)
        self.conn.close()
        self.tmp.cleanup()

    def call(self, method, path, body=None):
        request = urllib.request.Request(self.origin + path, method=method,
            data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None,
            headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as response:
            with response:
                return response.code, json.load(response)

    def common(self, session=None, **extra):
        self.counter += 1
        body = dict(actor_id=self.actor, client_request_id='connection-' + str(self.counter), **extra)
        if session:
            body['expected_version'] = session['version']
        return body

    def create(self):
        status, response = self.call('POST', '/api/voice/sessions', self.common())
        self.assertEqual(status, 200, response)
        return response['session']

    def route(self, session, suffix=''):
        return '/api/voice/sessions/' + session['id'] + ('/' + suffix if suffix else '')

    def post(self, session, suffix, **extra):
        status, response = self.call('POST', self.route(session, suffix), self.common(session, **extra))
        self.assertEqual(status, 200, response)
        return response

    def say(self, session, text, **extra):
        return self.post(session, 'transcripts', utterance_id='utterance-' + str(self.counter),
                         text=text, is_final=True, mode='auto', **extra)

    def answer(self, session, question, text):
        return self.post(session, 'followup/answers', question_id=question, text=text,
                         utterance_id='answer-' + str(self.counter))

    def get(self, session, suffix=''):
        status, response = self.call('GET', self.route(session, suffix) + '?actor_id=' + self.actor)
        self.assertEqual(status, 200, response)
        return response

    def assert_no_patient_side_effects(self):
        for table in ('patients', 'inquiries', 'hospitals'):
            self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0], 0, table)

    def assert_error(self, response, expected):
        status, body = response
        self.assertEqual(status, expected, body)
        self.assertIsInstance(body.get('error'), str)
        self.assertIsInstance(body.get('message'), str)
        return body

    def test_long_explanation_to_questions_answers_observation_correction_and_manual_confirm(self):
        status, capabilities = self.call('GET', '/api/voice/capabilities')
        self.assertEqual(status, 200)
        self.assertIn('auto', capabilities['transcript_modes'])
        self.assertIn('voice_extended_v1', capabilities['followup_profiles'])
        session = self.say(self.create(), LONG_STT)['session']
        questions = self.get(session, 'followup')
        self.assertEqual(questions['version'], session['version'])
        self.assertEqual([q['id'] for q in questions['followup']['items']],
                         ['consciousness', 'breathing', 'blood_pressure', 'pulse', 'respiratory_rate'])
        for question, text in [('consciousness', '명료'), ('breathing', '정상'),
                               ('blood_pressure', '120에80'), ('pulse', '92회'),
                               ('respiratory_rate', '20회')]:
            response = self.answer(session, question, text)
            self.assertTrue(response['answer']['accepted'], response)
            self.assertEqual(response['operational_effect'], 'draft_only')
            self.assertIsInstance(response['speak_text'], str)
            session = response['session']
            self.assertEqual(response['followup'], session['followup'])
        self.assertTrue(session['followup']['complete'])
        self.assertTrue(session['followup']['collection_complete'])
        observed = self.say(session, '혈압이 지금 100에 80까지 떨어졌어')
        session = observed['session']
        self.assertEqual(observed['command']['action'], 'observe')
        change = next(item for item in session['latest_changes'] if item['field'] == 'systolic_bp')
        self.assertEqual((change['previous'], change['current'], change['direction']), (120, 100, 'decreased'))
        self.assertEqual(change['change_kind'], 'observation')
        corrected = self.say(session, '혈압 110에 70으로 수정해')
        session = corrected['session']
        self.assertEqual(corrected['command']['action'], 'edit')
        self.assertEqual(session['field_metadata']['systolic_bp']['change_kind'], 'correction')
        status, readback = self.call('GET', self.route(session, 'readback') +
                                     '?actor_id=' + self.actor + '&field=blood_pressure')
        self.assertEqual(status, 200)
        self.assertIn('110', readback['speak_text'])
        self.assertIn('70', readback['speak_text'])
        self.assertEqual(readback['version'], session['version'])
        spoken_confirm = self.say(session, '확인')
        self.assertEqual(spoken_confirm['operational_effect'], 'confirmation_requires_tap')
        self.assertFalse(spoken_confirm['session']['confirmed'])
        session = self.post(session, 'confirm', confirmed=True)['session']
        self.assertTrue(session['confirmed'])
        self.assertEqual(session['confirmed_version'], session['version'])
        self.assertEqual(session['state'], 'DRAFT')
        intent = self.say(session, '환자 정보로 전송')
        self.assertEqual(intent['operational_effect'], 'explicit_submit_api_required')
        self.assertEqual(intent['session']['state'], 'DRAFT')
        self.assertIsNone(intent['session']['patient_id'])
        revisions = self.get(session, 'history')['revisions']
        self.assertEqual(revisions[-1]['kind'], 'correction')
        self.assert_no_patient_side_effects()

    def test_followup_answer_and_later_status_change_invalidate_confirmation(self):
        session = self.say(self.create(), LONG_STT)['session']
        approved = self.post(session, 'confirm', confirmed=True)['session']
        self.assertTrue(approved['confirmed'])
        answered = self.answer(approved, 'blood_pressure', '120에80')['session']
        self.assertFalse(answered['confirmed'])
        self.assertIsNone(answered['confirmed_version'])
        approved = self.post(answered, 'confirm', confirmed=True)['session']
        changed = self.say(approved, '혈압이 지금 100에 80까지 떨어졌어')['session']
        self.assertFalse(changed['confirmed'])
        self.assertGreater(changed['version'], approved['version'])
        stale = self.common(approved, confirmed=True)
        self.assert_error(self.call('POST', self.route(changed, 'confirm'), stale), 409)
        self.assertFalse(self.get(changed)['session']['confirmed'])
        self.assert_no_patient_side_effects()

    def test_partial_results_are_preview_only_and_final_uid_can_be_applied_once(self):
        session = self.say(self.create(), '혈압 120에 80')['session']
        approved = self.post(session, 'confirm', confirmed=True)['session']
        uid = 'one-final-utterance'
        partial = self.common(approved, text='혈압이 지금 100에 80까지 떨어졌어',
                              mode='auto', is_final=False, utterance_id=uid)
        status, preview = self.call('POST', self.route(approved, 'transcripts'), partial)
        self.assertEqual(status, 200, preview)
        self.assertFalse(preview['is_final'])
        self.assertEqual(preview['session']['version'], approved['version'])
        self.assertTrue(preview['session']['confirmed'])
        self.assertEqual(preview['session']['fields'], approved['fields'])
        self.assertEqual(len(preview['session']['utterances']), 1)
        final = self.common(approved, text=partial['text'], mode='auto', is_final=True, utterance_id=uid)
        status, applied = self.call('POST', self.route(approved, 'transcripts'), final)
        self.assertEqual(status, 200, applied)
        self.assertEqual(applied['session']['fields']['systolic_bp'], 100)
        self.assertFalse(applied['session']['confirmed'])
        self.assertEqual(len(applied['session']['utterances']), 2)
        self.assert_no_patient_side_effects()

    def test_exact_response_loss_retry_and_historical_replay_preserve_latest_state(self):
        session = self.create()
        body = self.common(session, question_id='blood_pressure', text='100에80', utterance_id='answer-bp')
        status, first = self.call('POST', self.route(session, 'followup/answers'), body)
        self.assertEqual(status, 200, first)
        self.assertFalse(first['duplicate'])
        # Simulate discarding the received response, then resend the exact request.
        status, replay = self.call('POST', self.route(session, 'followup/answers'), copy.deepcopy(body))
        self.assertEqual(status, 200, replay)
        self.assertTrue(replay['duplicate'])
        self.assertEqual(replay['session']['version'], first['session']['version'])
        self.assertEqual(len(replay['session']['utterances']), 1)
        latest = self.say(first['session'], '맥박 92회')['session']
        status, old_response = self.call('POST', self.route(session, 'followup/answers'), body)
        self.assertEqual(status, 200)
        self.assertTrue(old_response['duplicate'])
        self.assertLess(old_response['session']['version'], latest['version'])
        current = self.get(latest)['session']
        self.assertEqual(current['version'], latest['version'])
        self.assertEqual(current['fields']['pulse'], 92)
        self.assertEqual(len(current['utterances']), 2)
        changed = copy.deepcopy(body)
        changed['text'] = '110에70'
        self.assert_error(self.call('POST', self.route(session, 'followup/answers'), changed), 409)
        late = self.common(current, question_id='blood_pressure', text='110에70', utterance_id='late-bp')
        self.assertEqual(self.assert_error(self.call('POST', self.route(current, 'followup/answers'), late), 409)['error'],
                         'VOICE_QUESTION_CHANGED')
        foreign = self.common(current, question_id='temperature', text='37.2도', utterance_id='foreign')
        foreign['actor_id'] = 'someone-else'
        self.assert_error(self.call('POST', self.route(current, 'followup/answers'), foreign), 403)
        self.assert_error(self.call('GET', self.route(current, 'followup') + '?actor_id=someone-else'), 403)
        stale = self.common(first['session'], question_id='temperature', text='37.2도', utterance_id='stale')
        self.assertEqual(self.assert_error(self.call('POST', self.route(current, 'followup/answers'), stale), 409)['error'],
                         'VOICE_VERSION_CONFLICT')
        self.assertEqual(self.get(latest)['session']['version'], latest['version'])
        self.assert_no_patient_side_effects()

    def test_concurrent_question_answers_at_same_version_allow_only_one_write(self):
        session = self.create()
        bodies = [self.common(session, question_id=q, text=text, utterance_id=q)
                  for q, text in [('blood_pressure', '100에80'), ('pulse', '92회')]]
        barrier = threading.Barrier(3)
        results, errors = [], []
        def send(body):
            try:
                barrier.wait(timeout=5)
                results.append(self.call('POST', self.route(session, 'followup/answers'), body))
            except Exception as error:
                errors.append(error)
        threads = [threading.Thread(target=send, args=(body,)) for body in bodies]
        for thread in threads:
            thread.start()
        barrier.wait(timeout=5)
        for thread in threads:
            thread.join(timeout=6)
        self.assertFalse(errors, errors)
        self.assertEqual(sorted(status for status, _ in results), [200, 409], results)
        current = self.get(session)['session']
        self.assertEqual(current['version'], session['version'] + 1)
        self.assertEqual(len(current['utterances']), 1)
        self.assertNotEqual('pulse' in current['fields'], 'systolic_bp' in current['fields'])
        self.assert_no_patient_side_effects()

    def test_malformed_followup_contract_is_json_error_and_ambiguous_answer_stays_question(self):
        session = self.create()
        base = self.route(session, 'followup')
        for query in ('', '?actor_id=', '?actor_id=' + self.actor + '&extra=1',
                      '?actor_id=' + self.actor + '&actor_id=' + self.actor,
                      '?actor_id=' + self.actor + '&profile=unknown',
                      '?actor_id=' + self.actor + '&profile=voice_core_v1&profile=voice_extended_v1'):
            with self.subTest(query=query):
                self.assert_error(self.call('GET', base + query), 400)
        for extra in ({'profile': []}, {'profile': None}, {'question_id': []},
                      {'unavailable': 'true'}, {'expected_version': True},
                      {'text': ''}, {'surprise': True}):
            with self.subTest(extra=extra):
                body = self.common(session, question_id='blood_pressure', text='100에80',
                                   utterance_id='malformed-' + str(self.counter))
                body.update(extra)
                self.assert_error(self.call('POST', self.route(session, 'followup/answers'), body), 400)
        conflict = self.common(session, question_id='blood_pressure', text='100에80',
                               utterance_id='unavailable-conflict', unavailable=True)
        self.assertEqual(self.assert_error(self.call('POST', self.route(session, 'followup/answers'), conflict), 422)['error'],
                         'VOICE_ANSWER_CONFLICT')
        current = self.get(session)['session']
        self.assertEqual(current['version'], session['version'])
        self.assertEqual(current['utterances'], [])
        ambiguous = self.answer(session, 'blood_pressure', '100/80/60')
        self.assertFalse(ambiguous['answer']['accepted'])
        self.assertEqual(ambiguous['answer']['applied_fields'], [])
        self.assertNotIn('systolic_bp', ambiguous['session']['fields'])
        question = next(q for q in ambiguous['followup']['items'] if q['id'] == 'blood_pressure')
        self.assertTrue(question['askable'])
        self.assertEqual(question['reason'], 'needs_clarification')
        self.assert_no_patient_side_effects()


if __name__ == '__main__':
    unittest.main()
