"""Handoff 5.1: Korean subject particles are not measured numbers.

Synthetic transcript strings and a fresh temporary SQLite DB only. No microphone,
existing demonstration DB, hospital fixture, or external service is involved.
"""
import tempfile
import unittest
from pathlib import Path

from server import db, service, voice_input as voice
from server.voice_terms import extract


NOW = 2_000_000_000
NO_VALUE_REPORTS = (
    ('맥박이 지금 떨어졌어', ('pulse',)),
    ('맥박이 떨어졌어', ('pulse',)),
    ('맥박이 빨라졌어', ('pulse',)),
    ('혈당이 올랐어', ('glucose',)),
    ('혈당이 지금 떨어졌어', ('glucose',)),
    ('혈압이 지금 떨어졌어', ('systolic_bp', 'diastolic_bp')),
    ('체온이 지금 올랐어', ('temperature',)),
)
BASELINE = {'pulse': 92, 'glucose': 105, 'systolic_bp': 120,
            'diastolic_bp': 80, 'temperature': 37.2}


class ParticleExtractionTests(unittest.TestCase):
    def test_no_value_change_reports_never_turn_subject_particle_into_two(self):
        for text, _ in NO_VALUE_REPORTS:
            with self.subTest(text=text):
                result = extract(text, NOW)
                self.assertEqual(result['fields'], {})
                self.assertEqual(result['evidence'], {})

    def test_explicit_arabic_and_korean_numbers_keep_their_measurements(self):
        for text, expected in (
            ('맥박이 지금 110으로 올랐어', {'pulse': 110}),
            ('맥박이 지금 백십 회로 올랐어', {'pulse': 110}),
            ('혈당이 지금 180으로 올랐어', {'glucose': 180}),
            ('혈당 이백 mg/dL', {'glucose': 200}),
            ('맥박 이십 회', {'pulse': 20}),
            ('호흡수 이십 회', {'respiratory_rate': 20}),
            ('맥박 이 회', {'pulse': 2}),
            ('혈당 이 mg/dL', {'glucose': 2}),
            ('체온 삼십칠 점 이 도', {'temperature': 37.2}),
        ):
            with self.subTest(text=text):
                result = extract(text, NOW)
                self.assertEqual(result['fields'], expected)
                self.assertTrue(all(quote in text for quotes in result['evidence'].values()
                                    for quote in quotes))

    def test_multiple_reports_do_not_create_or_borrow_another_fields_number(self):
        for text, expected in (
            ('맥박이 떨어졌고 혈당이 올랐어', {}),
            ('맥박이 지금 떨어졌어. 혈당이 지금 180으로 올랐어', {'glucose': 180}),
            ('맥박이 지금 110으로 올랐어. 혈당이 지금 떨어졌어', {'pulse': 110}),
        ):
            with self.subTest(text=text):
                self.assertEqual(extract(text, NOW)['fields'], expected)

    def test_unavailable_and_spoken_questions_are_not_measurement_evidence(self):
        for text in ('맥박이 미측정입니다', '혈당이 미측정입니다',
                     '맥박이 몇 회인가요?', '혈당이 얼마인가요?',
                     '맥박이 지금 2회인가요?', '혈당이 지금 2인가요?'):
            with self.subTest(text=text):
                self.assertEqual(extract(text, NOW)['fields'], {})


class ParticleSessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='voice-particle-')
        self.conn = db.connect(str(Path(self.tmp.name) / 'synthetic.sqlite'))
        db.init(self.conn)
        self.counter = 0
        self.actor = 'handoff-particle-synthetic-crew'
        self.patient_id = 'handoff-particle-synthetic-patient'
        service.create_patient(self.conn, NOW - 1, self.patient_id, dict(
            reporter_role='transporter', consent=True, contact='010-0000-0000',
            age=47, sex='male', symptoms='합성 시험용 흉통', required_capabilities=[],
            resource_demands={'ed_bed': 1}, lat=35.16, lon=126.85))
        self.patient_before = dict(self.conn.execute(
            'SELECT * FROM patients WHERE id=?', (self.patient_id,)).fetchone())

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def key(self):
        self.counter += 1
        return 'handoff-particle-' + str(self.counter)

    def common(self, session=None, **extra):
        body = dict(actor_id=self.actor, client_request_id=self.key(), **extra)
        if session is not None:
            body['expected_version'] = session['version']
        return body

    def say(self, session, text, mode='auto'):
        return voice.transcript(self.conn, NOW, session['id'], self.common(
            session, utterance_id=self.key(), text=text, is_final=True, mode=mode))

    def baseline(self):
        session = voice.create(self.conn, NOW, self.common(patient_id=self.patient_id))['session']
        session = self.say(session, '맥박 92회. 혈당 105 mg/dL. 혈압 120에 80. 체온 37.2도.',
                           mode='dictation')['session']
        for key, value in BASELINE.items():
            self.assertEqual(session['fields'][key], value)
        return session

    def assert_patient_untouched(self):
        self.assertEqual(dict(self.conn.execute('SELECT * FROM patients WHERE id=?',
                                               (self.patient_id,)).fetchone()), self.patient_before)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM hospitals').fetchone()[0], 0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM inquiries').fetchone()[0], 0)

    def assert_recheck(self, session, targets, text):
        for key in targets:
            self.assertEqual(session['fields'][key], BASELINE[key])
            self.assertTrue(session['field_metadata'][key]['needs_clarification'])
            self.assertEqual(session['field_metadata'][key]['clarification_evidence'], [text])
        question_ids = {'blood_pressure' if key in ('systolic_bp', 'diastolic_bp') else key
                        for key in targets}
        for qid in question_ids:
            question = next(q for q in session['followup']['items'] if q['id'] == qid)
            self.assertEqual(question['reason'], 'needs_clarification')
            self.assertTrue(question['askable'])

    def test_auto_changes_without_values_preserve_prior_values_and_request_recheck(self):
        for text, targets in NO_VALUE_REPORTS:
            with self.subTest(text=text):
                session = self.baseline()
                result = self.say(session, text)
                updated = result['session']
                self.assertEqual(result['effective_mode'], 'command')
                self.assertEqual(result['command']['action'], 'observe')
                self.assertEqual(result['extraction']['fields'], {})
                self.assertEqual(updated['fields'], session['fields'])
                self.assert_recheck(updated, targets, text)
                self.assertEqual(updated['utterances'][-1]['transcript'], text)
                self.assert_patient_untouched()

    def test_multi_target_change_preserves_both_values_and_rechecks_each_target(self):
        text = '맥박이 떨어졌고 혈당이 올랐어'
        session = self.baseline()
        updated = self.say(session, text)['session']
        self.assertEqual(updated['fields'], session['fields'])
        self.assert_recheck(updated, ('pulse', 'glucose'), text)
        self.assert_patient_untouched()

    def test_mixed_report_updates_only_the_explicit_measurement(self):
        text = '맥박이 지금 떨어졌어. 혈당이 지금 180으로 올랐어'
        session = self.baseline()
        result = self.say(session, text)
        updated = result['session']
        self.assertEqual(result['extraction']['fields'], {'glucose': 180})
        self.assertEqual(updated['fields'], {**session['fields'], 'glucose': 180})
        self.assert_recheck(updated, ('pulse',), text)
        self.assertFalse(updated['field_metadata']['glucose'].get('needs_clarification', False))
        glucose_change = next(c for c in updated['latest_changes'] if c['field'] == 'glucose')
        self.assertEqual((glucose_change['previous'], glucose_change['current']), (105, 180))
        self.assert_patient_untouched()

    def test_later_explicit_measurement_resolves_recheck_instead_of_permanently_rejecting_numbers(self):
        for text in ('맥박이 지금 110으로 올랐어', '맥박이 지금 백십 회로 올랐어'):
            with self.subTest(text=text):
                session = self.say(self.baseline(), '맥박이 지금 떨어졌어')['session']
                updated = self.say(session, text)['session']
                self.assertEqual(updated['fields']['pulse'], 110)
                self.assertFalse(updated['field_metadata']['pulse'].get('needs_clarification', False))
                self.assertNotIn('pulse', [q['id'] for q in updated['followup']['items']])
                evidence = updated['field_metadata']['pulse']['evidence']
                self.assertTrue(evidence)
                self.assertTrue(all(quote in text for quote in evidence))
                self.assertEqual(updated['utterances'][-1]['transcript'], text)
                self.assert_patient_untouched()

    def test_unavailable_followup_preserves_previous_value_without_inventing_two(self):
        session = self.say(self.baseline(), '맥박이 지금 떨어졌어')['session']
        text = '맥박이 미측정입니다'
        result = voice.answer_followup(self.conn, NOW, session['id'], self.common(
            session, question_id='pulse', utterance_id=self.key(), text=text, unavailable=True))
        self.assertTrue(result['answer']['accepted'])
        self.assertTrue(result['answer']['unavailable'])
        self.assertEqual(result['answer']['applied_fields'], [])
        self.assertEqual(result['session']['fields']['pulse'], 92)
        question = next(q for q in result['followup']['items'] if q['id'] == 'pulse')
        self.assertEqual(question['answer_status'], 'unavailable')
        self.assertFalse(question['askable'])
        self.assert_patient_untouched()

    def test_saved_value_readback_with_number_cannot_replace_observations_or_clear_recheck(self):
        session = self.say(self.baseline(), '맥박이 지금 떨어졌어')['session']
        for text in ('맥박 2로 저장됐는지 알려줘', '맥박 읽어줘'):
            with self.subTest(text=text):
                result = self.say(session, text)
                updated = result['session']
                self.assertEqual(result['command']['action'], 'review')
                self.assertEqual(updated['fields'], session['fields'])
                self.assertEqual(updated['field_metadata'], session['field_metadata'])
                self.assertEqual(updated['version'], session['version'])
                self.assertIn('이전 관찰값', result['speak_text'])
                self.assert_patient_untouched()


if __name__ == '__main__':
    unittest.main()
