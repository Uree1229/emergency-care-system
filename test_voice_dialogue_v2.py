"""Voice-v2 dialogue service contracts using an isolated synthetic SQLite DB.

These tests submit strings directly, never real audio or hospital requests.
"""
import copy
import json

from server import voice_input as voice
from test_server import Base, T0


LONG_STT = ('47세 남성 환자입니다 20분 전부터 가슴통증이 있습니다 '
            '2시간 후면 정상입니다 혈압 120 80 92회 산소 포화도 97% '
            '체온 37.2도 고급 수입 회 혈당 105입니다 도와 주려는 없습니다')
OBSERVATIONS = {'age', 'age_group', 'sex', 'symptoms', 'denied_symptoms',
                'uncertain_symptoms', 'consciousness', 'breathing', 'systolic_bp',
                'diastolic_bp', 'pulse', 'spo2', 'temperature', 'respiratory_rate',
                'glucose', 'onset_at'}


class VoiceDialogueV2(Base):
    def setUp(self):
        super().setUp()
        self.now = T0 + 30
        self.counter = 0
        self.actor = 'dialogue-v2-crew'

    def key(self):
        self.counter += 1
        return 'dialogue-v2-' + str(self.counter)

    def common(self, session=None, **extra):
        body = dict(actor_id=self.actor, client_request_id=self.key(), **extra)
        if session:
            body['expected_version'] = session['version']
        return body

    def create(self):
        return voice.create(self.conn, self.now, self.common())['session']

    def say(self, session, text, mode='dictation'):
        return voice.transcript(self.conn, self.now, session['id'], self.common(
            session, utterance_id=self.key(), text=text, is_final=True, mode=mode))

    def edit(self, session, fields):
        return voice.edit(self.conn, self.now, session['id'], self.common(
            session, changes=fields, change_kind='correction'))['session']

    def answer_body(self, session, question_id, text, **extra):
        return self.common(session, question_id=question_id, utterance_id=self.key(), text=text, **extra)

    def answer(self, session, question_id, text, **extra):
        return voice.answer_followup(self.conn, self.now, session['id'],
                                     self.answer_body(session, question_id, text, **extra))

    def snapshot(self, session):
        return voice.get(self.conn, self.now, session['id'], self.actor)['session']

    def test_long_corrupted_dictation_keeps_seven_observations_and_five_questions(self):
        session = self.say(self.create(), LONG_STT)['session']
        self.assertEqual(set(session['fields']) & OBSERVATIONS,
                         {'age', 'sex', 'symptoms', 'onset_at', 'spo2', 'temperature', 'glucose'})
        self.assertEqual(session['fields']['age'], 47)
        self.assertEqual(session['fields']['onset_at'], self.now - 1200)
        questions = session['followup']
        self.assertEqual([item['id'] for item in questions['items']],
                         ['consciousness', 'breathing', 'blood_pressure', 'pulse', 'respiratory_rate'])
        self.assertEqual(questions['next_question']['id'], 'consciousness')
        self.assertEqual(session['utterances'][0]['transcript'], LONG_STT)
        self.assertEqual(voice.followup(self.conn, self.now, session['id'], self.actor)['followup'], questions)

    def test_short_bp_answer_is_scoped_and_raw_evidence_is_preserved(self):
        session = self.create()
        result = self.answer(session, 'blood_pressure', '100에80')
        updated = result['session']
        self.assertTrue(result['answer']['accepted'])
        self.assertTrue(result['answer']['contextual_answer'])
        self.assertEqual(result['answer']['applied_fields'], ['diastolic_bp', 'systolic_bp'])
        self.assertEqual((updated['fields']['systolic_bp'], updated['fields']['diastolic_bp']), (100, 80))
        self.assertEqual(updated['field_metadata']['systolic_bp']['evidence'], ['100에80'])
        self.assertEqual(updated['field_metadata']['diastolic_bp']['evidence'], ['100에80'])
        self.assertEqual(updated['field_metadata']['systolic_bp']['question_id'], 'blood_pressure')
        self.assertEqual(updated['utterances'][-1]['provider'], 'guided_followup')
        self.assertEqual(updated['utterances'][-1]['transcript'], '100에80')
        self.assertNotIn('blood_pressure', [item['id'] for item in result['followup']['items']])

    def test_wrong_target_answer_changes_neither_bp_nor_other_measurement(self):
        session = self.edit(self.create(), {'pulse': 78})
        result = self.answer(session, 'blood_pressure', '맥박 92')
        fields = result['session']['fields']
        self.assertFalse(result['answer']['accepted'])
        self.assertFalse(result['answer']['contextual_answer'])
        self.assertEqual(result['answer']['applied_fields'], [])
        self.assertEqual(fields['pulse'], 78)
        self.assertNotIn('systolic_bp', fields)
        self.assertNotIn('diastolic_bp', fields)
        bp = next(item for item in result['followup']['items'] if item['id'] == 'blood_pressure')
        self.assertTrue(bp['askable'])
        self.assertEqual(bp['reason'], 'needs_clarification')

    def test_numeric_unavailable_does_not_invent_zero_or_repeat_forever(self):
        result = self.answer(self.create(), 'blood_pressure', '혈압 미측정')
        session = result['session']
        self.assertTrue(result['answer']['accepted'])
        self.assertTrue(result['answer']['unavailable'])
        self.assertNotIn('systolic_bp', session['fields'])
        self.assertNotIn('diastolic_bp', session['fields'])
        bp = next(item for item in result['followup']['items'] if item['id'] == 'blood_pressure')
        self.assertFalse(bp['askable'])
        self.assertEqual(bp['answer_status'], 'unavailable')
        asked = voice.followup(self.conn, self.now, session['id'], self.actor)['followup']
        self.assertFalse(next(item for item in asked['items'] if item['id'] == 'blood_pressure')['askable'])
        self.assertNotEqual(asked['next_question']['id'], 'blood_pressure')
        # A later measured value replaces the unavailable-answer marker.
        latest = self.say(session, '혈압이 지금 100에 80으로 측정됐어', 'auto')['session']
        self.assertEqual(latest['fields']['systolic_bp'], 100)
        self.assertNotIn('blood_pressure', [item['id'] for item in latest['followup']['items']])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM voice_followup_answers WHERE session_id=?',
                                          (session['id'],)).fetchone()[0], 0)

    def test_known_zero_is_an_observation_not_missing(self):
        session = self.say(self.create(), '나이 0세. 맥박 0회.')['session']
        self.assertEqual(session['fields']['age'], 0)
        self.assertEqual(session['fields']['pulse'], 0)
        unresolved = [item['id'] for item in session['followup']['items']]
        self.assertNotIn('age', unresolved)
        self.assertNotIn('pulse', unresolved)

    def test_followup_reads_and_answers_enforce_owner_and_version(self):
        original = self.create()
        session = self.answer(original, 'blood_pressure', '100에80')['session']
        stale = self.answer_body(original, 'pulse', '맥박 92')
        self.assertEqual(self.code(voice.answer_followup, self.conn, self.now, session['id'], stale),
                         'VOICE_VERSION_CONFLICT')
        foreign = self.answer_body(session, 'pulse', '맥박 92')
        foreign['actor_id'] = 'another-crew'
        self.assertEqual(self.code(voice.answer_followup, self.conn, self.now, session['id'], foreign), 'FORBIDDEN')
        self.assertEqual(self.code(voice.followup, self.conn, self.now, session['id'], 'another-crew'), 'FORBIDDEN')
        self.assertEqual(self.snapshot(session)['version'], session['version'])

    def test_followup_exact_retry_is_idempotent_and_reused_utterance_rejected(self):
        session = self.create()
        body = self.answer_body(session, 'blood_pressure', '맥박 92')  # unresolved target remains offered
        first = voice.answer_followup(self.conn, self.now, session['id'], body)
        repeated = voice.answer_followup(self.conn, self.now, session['id'], copy.deepcopy(body))
        self.assertFalse(first['duplicate'])
        self.assertTrue(repeated['duplicate'])
        self.assertEqual(repeated['session']['version'], first['session']['version'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM voice_utterances WHERE session_id=?',
                                          (session['id'],)).fetchone()[0], 1)
        changed = copy.deepcopy(body)
        changed['text'] = '혈압 100에 80'
        self.assertEqual(self.code(voice.answer_followup, self.conn, self.now, session['id'], changed),
                         'VOICE_REQUEST_CONFLICT')
        duplicate_utterance = self.answer_body(first['session'], 'blood_pressure', '혈압 100에 80')
        duplicate_utterance['utterance_id'] = body['utterance_id']
        self.assertEqual(self.code(voice.answer_followup, self.conn, self.now, session['id'], duplicate_utterance),
                         'VOICE_UTTERANCE_FINALIZED')

    def test_resolved_question_rejects_late_answer(self):
        session = self.answer(self.create(), 'blood_pressure', '100에80')['session']
        self.assertEqual(self.code(voice.answer_followup, self.conn, self.now, session['id'],
                                  self.answer_body(session, 'blood_pressure', '110에70')),
                         'VOICE_QUESTION_CHANGED')
        self.assertEqual(self.snapshot(session)['fields']['systolic_bp'], 100)

    def test_natural_bp_change_is_observation_and_correction_remains_distinct(self):
        session = self.say(self.create(), '혈압 120에 80')['session']
        observed = self.say(session, '혈압이 지금 100에 80까지 떨어졌어', 'auto')
        session = observed['session']
        self.assertEqual(observed['requested_mode'], 'auto')
        self.assertEqual(observed['effective_mode'], 'command')
        self.assertEqual(observed['command']['action'], 'observe')
        self.assertEqual((session['fields']['systolic_bp'], session['fields']['diastolic_bp']), (100, 80))
        self.assertEqual(session['field_metadata']['systolic_bp']['change_kind'], 'observation')
        changed = next(item for item in session['latest_changes'] if item['field'] == 'systolic_bp')
        self.assertEqual((changed['previous'], changed['current'], changed['direction']), (120, 100, 'decreased'))
        self.assertEqual(changed['change_kind'], 'observation')
        corrected = self.say(session, '혈압 110에 70으로 수정해', 'auto')['session']
        self.assertEqual(corrected['field_metadata']['systolic_bp']['change_kind'], 'correction')
        revisions = voice.history(self.conn, self.now, session['id'], self.actor)['revisions']
        self.assertEqual([item['kind'] for item in revisions[-2:]], ['observation', 'correction'])
        self.assertEqual(revisions[-2]['before_fields']['systolic_bp'], 120)
        self.assertEqual(revisions[-2]['after_fields']['systolic_bp'], 100)
        self.assertEqual(revisions[-1]['before_fields']['systolic_bp'], 100)
        self.assertEqual(revisions[-1]['after_fields']['systolic_bp'], 110)

    def test_ambiguous_update_preserves_old_bp_and_answer_clears_recheck(self):
        session = self.say(self.create(), '혈압 120에 80')['session']
        unclear = self.say(session, '혈압이 지금 100인지 110인지 모르겠어', 'auto')
        session = unclear['session']
        self.assertEqual((session['fields']['systolic_bp'], session['fields']['diastolic_bp']), (120, 80))
        for key in ('systolic_bp', 'diastolic_bp'):
            self.assertTrue(session['field_metadata'][key]['needs_clarification'])
        bp = next(item for item in session['followup']['items'] if item['id'] == 'blood_pressure')
        self.assertEqual(bp['reason'], 'needs_clarification')
        self.assertIn('이전 관찰값', voice.readback(self.conn, self.now, session['id'], self.actor,
                                                'blood_pressure')['speak_text'])
        resolved = self.answer(session, 'blood_pressure', '100에80')['session']
        self.assertEqual(resolved['fields']['systolic_bp'], 100)
        for key in ('systolic_bp', 'diastolic_bp'):
            self.assertFalse(resolved['field_metadata'][key].get('needs_clarification', False))
        self.assertNotIn('blood_pressure', [item['id'] for item in resolved['followup']['items']])
        self.assertNotIn('이전 관찰값', voice.readback(self.conn, self.now, session['id'], self.actor,
                                                   'blood_pressure')['speak_text'])

    def test_auto_review_missing_and_submit_intent_never_register_patient(self):
        session = self.say(self.create(), '혈압 120에 80')['session']
        review = self.say(session, '혈압 읽어줘', 'auto')
        self.assertEqual(review['command']['action'], 'review')
        self.assertIn('120', review['speak_text'])
        self.assertEqual(review['session']['version'], session['version'])
        missing = self.say(review['session'], '빠진 항목 알려줘', 'auto')
        self.assertEqual(missing['command']['action'], 'missing')
        self.assertIn('followup', missing)
        submit = self.say(missing['session'], '환자 정보로 전송', 'auto')
        self.assertEqual(submit['command']['action'], 'submit')
        self.assertEqual(submit['operational_effect'], 'explicit_submit_api_required')
        self.assertEqual(submit['session']['state'], 'DRAFT')
        self.assertIsNone(submit['session']['patient_id'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM patients').fetchone()[0], 0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM inquiries').fetchone()[0], 0)

    def test_partial_auto_update_never_mutates_draft_or_history(self):
        session = self.say(self.create(), '혈압 120에 80')['session']
        body = self.common(session, utterance_id=self.key(), text='혈압이 지금 100에 80까지 떨어졌어',
                           is_final=False, mode='auto')
        result = voice.transcript(self.conn, self.now, session['id'], body)
        self.assertFalse(result['is_final'])
        self.assertEqual(result['session']['fields'], session['fields'])
        self.assertEqual(result['session']['version'], session['version'])
        self.assertEqual(len(result['session']['utterances']), 1)


if __name__ == '__main__':
    import unittest
    unittest.main()
