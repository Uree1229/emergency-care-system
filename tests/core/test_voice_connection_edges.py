"""Server-only question/answer/undo integration regressions. No real speech or UI."""
import unittest
from unittest.mock import patch

from server import db, service, voice_input as voice


NOW = 2_000_000_000


class VoiceConnectionEdges(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(':memory:')
        db.init(self.conn)
        self.counter = 0
        self.actor = 'connection-edge-crew'

    def tearDown(self):
        self.conn.close()

    def common(self, session=None, **extra):
        self.counter += 1
        result = dict(actor_id=self.actor, client_request_id='connection-edge-' + str(self.counter), **extra)
        if session is not None:
            result['expected_version'] = session['version']
        return result

    def create(self):
        return voice.create(self.conn, NOW, self.common())['session']

    def say(self, session, text):
        return voice.transcript(self.conn, NOW, session['id'], self.common(session,
            text=text, utterance_id='spoken-' + str(self.counter), is_final=True, mode='auto'))['session']

    def answer(self, session, question, text):
        return voice.answer_followup(self.conn, NOW, session['id'], self.common(session,
            question_id=question, text=text, utterance_id='answer-' + str(self.counter)))['session']

    def question(self, session, question):
        return next((item for item in session['followup']['items'] if item['id'] == question), None)

    def test_undo_unavailable_answer_restores_prior_clarification_instead_of_skipping_it(self):
        session = self.say(self.create(), '47세 남성. 혈압120에80.')
        before_answer = self.say(session, '혈압이 지금 떨어졌어')
        self.assertTrue(self.question(before_answer, 'blood_pressure')['askable'])
        self.assertTrue(before_answer['field_metadata']['systolic_bp']['needs_clarification'])
        session = self.answer(before_answer, 'blood_pressure', '미측정')
        self.assertFalse(self.question(session, 'blood_pressure')['askable'])

        undone = voice.undo(self.conn, NOW, session['id'], self.common(session))['session']
        self.assertEqual(undone['fields'], before_answer['fields'])
        self.assertEqual(undone['field_metadata'], before_answer['field_metadata'])
        self.assertEqual(self.question(undone, 'blood_pressure'), self.question(before_answer, 'blood_pressure'))

    def test_undo_measurement_restores_previous_unavailable_question_status(self):
        unavailable = self.answer(self.create(), 'blood_pressure', '미측정')
        self.assertFalse(self.question(unavailable, 'blood_pressure')['askable'])
        session = self.answer(unavailable, 'blood_pressure', '100에80')
        self.assertIsNone(self.question(session, 'blood_pressure'))

        undone = voice.undo(self.conn, NOW, session['id'], self.common(session))['session']
        self.assertEqual(undone['fields'], unavailable['fields'])
        self.assertEqual(undone['field_metadata'], unavailable['field_metadata'])
        self.assertEqual(self.question(undone, 'blood_pressure')['answer_status'], 'unavailable')
        self.assertFalse(self.question(undone, 'blood_pressure')['askable'])

    def test_valid_symptom_answer_resolves_flags_on_alternative_polarity_fields(self):
        for text, field in [('흉통 있음', 'symptoms'), ('흉통 없음', 'denied_symptoms')]:
            with self.subTest(text=text):
                session = self.answer(self.create(), 'symptoms', '글쎄')
                self.assertTrue(self.question(session, 'symptoms')['askable'])
                session = self.answer(session, 'symptoms', text)
                self.assertEqual(session['fields'][field], ['흉통'])
                self.assertIsNone(self.question(session, 'symptoms'))

    def test_followup_answer_invalidates_previous_confirmation_and_stale_confirm_fails(self):
        session = self.say(self.create(), '47세 남성. 흉통.')
        confirmed = voice.confirm(self.conn, NOW, session['id'], self.common(session, confirmed=True))['session']
        self.assertTrue(confirmed['confirmed'])
        latest = self.answer(confirmed, 'blood_pressure', '100에80')
        self.assertFalse(latest['confirmed'])
        with self.assertRaises(service.ServiceError) as raised:
            voice.confirm(self.conn, NOW, session['id'], self.common(confirmed, confirmed=True))
        self.assertEqual(raised.exception.code, 'VOICE_VERSION_CONFLICT')

    def test_resolving_one_symptom_does_not_hide_another_uncertain_symptom(self):
        session = self.say(self.create(), '흉통 의심. 복통 의심.')
        self.assertEqual(set(session['fields']['uncertain_symptoms']), {'흉통', '복통'})
        session = self.answer(session, 'symptoms', '흉통 있음')
        self.assertEqual(session['fields']['symptoms'], ['흉통'])
        self.assertEqual(session['fields']['uncertain_symptoms'], ['복통'])
        question = self.question(session, 'symptoms')
        self.assertTrue(question['askable'])
        self.assertEqual(question['uncertain_items'], ['복통'])

    def test_partial_bp_observation_reopens_group_and_undo_restores_unavailable(self):
        unavailable = self.answer(self.create(), 'blood_pressure', '미측정')
        session = voice.edit(self.conn, NOW, unavailable['id'], self.common(unavailable,
            changes={'systolic_bp': 110}, change_kind='observation'))['session']
        question = self.question(session, 'blood_pressure')
        self.assertTrue(question['askable'])
        self.assertEqual(question['missing_fields'], ['diastolic_bp'])
        self.assertNotIn('diastolic_bp', session['fields'])
        self.assertFalse(session['field_metadata']['systolic_bp'].get('needs_clarification', False))
        self.assertTrue(session['field_metadata']['diastolic_bp']['needs_clarification'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM voice_followup_answers WHERE session_id=?',
                                          (session['id'],)).fetchone()[0], 0)
        undone = voice.undo(self.conn, NOW, session['id'], self.common(session))['session']
        self.assertEqual(undone['fields'], unavailable['fields'])
        self.assertEqual(undone['field_metadata'], unavailable['field_metadata'])
        self.assertEqual(self.question(undone, 'blood_pressure'), self.question(unavailable, 'blood_pressure'))

    def test_resolving_one_unavailable_question_preserves_other_question_and_undo_restores_both(self):
        session = self.answer(self.create(), 'blood_pressure', '미측정')
        before_answer = self.answer(session, 'pulse', '미측정')
        session = self.answer(before_answer, 'blood_pressure', '100에80')
        self.assertIsNone(self.question(session, 'blood_pressure'))
        self.assertFalse(self.question(session, 'pulse')['askable'])
        undone = voice.undo(self.conn, NOW, session['id'], self.common(session))['session']
        for question in ('blood_pressure', 'pulse'):
            self.assertEqual(self.question(undone, question), self.question(before_answer, question))
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM voice_followup_answers WHERE session_id=?',
                                          (session['id'],)).fetchone()[0], 2)

    def test_failed_answer_transaction_restores_fields_question_status_and_revision(self):
        before = self.answer(self.create(), 'blood_pressure', '미측정')
        count_before = self.conn.execute('SELECT COUNT(*) FROM voice_revisions WHERE session_id=?',
                                        (before['id'],)).fetchone()[0]
        with patch.object(voice, '_public', side_effect=RuntimeError('injected response failure')):
            with self.assertRaises(RuntimeError):
                self.answer(before, 'blood_pressure', '100에80')
        after = voice.get(self.conn, NOW, before['id'], self.actor)['session']
        self.assertEqual(after, before)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM voice_revisions WHERE session_id=?',
                                          (before['id'],)).fetchone()[0], count_before)
        self.assertFalse(self.question(after, 'blood_pressure')['askable'])


if __name__ == '__main__':
    unittest.main()
