"""UI handoff 5.5 answers through the service API, using synthetic in-memory data.

No recognizer, microphone, existing patient database or hospital request is used.
The supplied text must remain the evidence even when the question supplies context.
"""
import unittest

from server import db, voice_input as voice


NOW = 2_000_000_000


class VoiceUIHandoffAnswers(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(':memory:')
        db.init(self.conn)
        self.counter = 0
        self.actor = 'ui-handoff-answer-test'

    def tearDown(self):
        self.conn.close()

    def key(self):
        self.counter += 1
        return 'ui-handoff-' + str(self.counter)

    def create(self):
        return voice.create(self.conn, NOW, dict(
            actor_id=self.actor, client_request_id=self.key()))['session']

    def answer(self, question_id, text, session=None, **extra):
        session = session or self.create()
        return voice.answer_followup(self.conn, NOW, session['id'], dict(
            actor_id=self.actor, client_request_id=self.key(),
            expected_version=session['version'], utterance_id=self.key(),
            question_id=question_id, profile='voice_extended_v1', text=text, **extra))

    def assert_answer(self, question_id, text, expected, *, derived_fields=None):
        result = self.answer(question_id, text)
        session = result['session']
        derived_fields = derived_fields or {}
        self.assertTrue(result['answer']['accepted'], text)
        self.assertFalse(result['answer']['unavailable'], text)
        self.assertEqual(result['answer']['applied_fields'], sorted(expected), text)
        self.assertEqual(session['fields'], {**expected, **derived_fields}, text)
        self.assertEqual(session['utterances'][-1]['transcript'], text)
        self.assertEqual(session['utterances'][-1]['provider'], 'guided_followup')
        for field in expected:
            metadata = session['field_metadata'][field]
            self.assertEqual(metadata['evidence'], [text])
            self.assertEqual(metadata['source'], 'followup_answer')
            self.assertEqual(metadata['question_id'], question_id)
        for field in derived_fields:
            metadata = session['field_metadata'][field]
            self.assertEqual(metadata['derived'], 'r1_reconcile')
            self.assertEqual(metadata['evidence'], [])
            self.assertEqual(metadata['source'], 'followup_answer')
        self.assertNotIn(question_id, [item['id'] for item in result['followup']['items']])
        self.assertEqual(result['operational_effect'], 'draft_only')
        self.assertIsNone(session['patient_id'])
        return result

    def test_rate_answers_accept_natural_prefixes_and_units_only_in_the_asked_field(self):
        examples = (
            ('분당 20회', 20), ('분당 20', 20), ('1분에 20회', 20),
            ('분당 92회', 92), ('20번', 20), ('스무 번', 20),
            ('분당 20분', 20), ('20회요', 20), ('92회요', 92),
        )
        for question_id in ('pulse', 'respiratory_rate'):
            for text, value in examples:
                with self.subTest(question=question_id, text=text):
                    self.assert_answer(question_id, text, {question_id: value})

    def test_number_and_category_endings_preserve_raw_evidence(self):
        for question_id, text, expected in (
            ('spo2', '97퍼센트요', {'spo2': 97}),
            ('glucose', '105요', {'glucose': 105}),
            ('age', '47세요', {'age': 47}),
            ('breathing', '정상이요', {'breathing': 'normal'}),
            ('breathing', '정상임', {'breathing': 'normal'}),
            ('consciousness', '명료함', {'consciousness': 'alert'}),
        ):
            with self.subTest(question=question_id, text=text):
                self.assert_answer(question_id, text, expected,
                                   derived_fields={'age_category': 'adult'} if question_id == 'age' else None)

    def test_percent_and_temperature_spellings(self):
        for question_id, text, expected in (
            ('spo2', '97프로', {'spo2': 97}),
            ('temperature', '37.2도씨', {'temperature': 37.2}),
            ('temperature', '37도 2', {'temperature': 37.2}),
            ('temperature', '37도2', {'temperature': 37.2}),
        ):
            with self.subTest(question=question_id, text=text):
                self.assert_answer(question_id, text, expected)

    def test_blood_pressure_pair_separators(self):
        for text in ('110 70', '110의 70', '110하고 70', '백십 칠십'):
            with self.subTest(text=text):
                self.assert_answer('blood_pressure', text,
                                   {'systolic_bp': 110, 'diastolic_bp': 70})

    def test_labelled_blood_pressure_without_connector_in_a_long_description(self):
        session = self.create()
        text = '47세 남성 환자입니다. 혈압 120 80. 산소포화도 97퍼센트.'
        result = voice.transcript(self.conn, NOW, session['id'], dict(
            actor_id=self.actor, client_request_id=self.key(),
            expected_version=session['version'], utterance_id=self.key(),
            is_final=True, mode='dictation', text=text))
        updated = result['session']
        self.assertEqual(updated['fields']['systolic_bp'], 120)
        self.assertEqual(updated['fields']['diastolic_bp'], 80)
        self.assertEqual(updated['fields']['age'], 47)
        self.assertEqual(updated['fields']['spo2'], 97)
        self.assertEqual(updated['utterances'][-1]['transcript'], text)

    def test_onset_answers_accept_natural_time_words(self):
        for text, elapsed in (
            ('30분 전쯤', 1800), ('한 시간 전', 3600),
            ('30분 전부터', 1800), ('아까 30분 전에', 1800),
        ):
            with self.subTest(text=text):
                self.assert_answer('onset_at', text, {'onset_at': NOW - elapsed})

    def test_unavailable_aliases_leave_values_unfilled_and_stop_repeating(self):
        aliases = ('비 측정', '비측정', '및 측정', '및측정', '미측',
                   '미측정이요', '측정 안 했어요', '측정안했어요', '모르겠습니다', '패스')
        for text in aliases:
            with self.subTest(text=text):
                result = self.answer('glucose', text)
                session = result['session']
                self.assertTrue(result['answer']['accepted'])
                self.assertTrue(result['answer']['unavailable'])
                self.assertEqual(result['answer']['applied_fields'], [])
                self.assertEqual(session['fields'], {})
                self.assertEqual(session['utterances'][-1]['transcript'], text)
                item = next(item for item in result['followup']['items'] if item['id'] == 'glucose')
                self.assertFalse(item['askable'])
                self.assertEqual(item['answer_status'], 'unavailable')
                refreshed = voice.followup(self.conn, NOW, session['id'], self.actor)['followup']
                self.assertNotEqual(refreshed['next_question']['id'], 'glucose')
                self.assertNotIn('glucose', [item['id'] for item in refreshed['items'] if item['askable']])

    def test_unavailable_flag_preserves_original_recognized_text_for_a_bp_pair(self):
        text = '및 측정'
        result = self.answer('blood_pressure', text, unavailable=True)
        self.assertTrue(result['answer']['accepted'])
        self.assertTrue(result['answer']['unavailable'])
        self.assertEqual(result['session']['fields'], {})
        self.assertEqual(result['session']['utterances'][-1]['transcript'], text)
        for field in ('systolic_bp', 'diastolic_bp'):
            self.assertEqual(result['session']['field_metadata'][field]['followup_unavailable_for'],
                             'blood_pressure')

    def test_rate_minute_unit_repair_is_not_applied_to_other_questions(self):
        for question_id in ('age', 'blood_pressure', 'spo2', 'temperature', 'glucose', 'onset_at'):
            with self.subTest(question=question_id):
                result = self.answer(question_id, '분당 20분')
                self.assertFalse(result['answer']['accepted'])
                self.assertFalse(result['answer']['unavailable'])
                self.assertEqual(result['session']['fields'], {})

    def test_looser_answer_grammar_still_rejects_multiple_measurements(self):
        for question_id, text in (
            ('pulse', '분당 20회 또는 30회'),
            ('respiratory_rate', '20번 30번'),
            ('glucose', '105 110요'),
            ('temperature', '37.2도씨 39.4도씨'),
            ('blood_pressure', '110 70 60'),
            ('blood_pressure', '110의 70하고 60'),
        ):
            with self.subTest(question=question_id, text=text):
                result = self.answer(question_id, text)
                self.assertFalse(result['answer']['accepted'])
                self.assertEqual(result['answer']['applied_fields'], [])
                self.assertEqual(result['session']['fields'], {})
                item = next(item for item in result['followup']['items'] if item['id'] == question_id)
                self.assertTrue(item['askable'])

    def test_wrong_target_observation_is_ignored_and_existing_value_is_preserved(self):
        session = self.create()
        session = voice.edit(self.conn, NOW, session['id'], dict(
            actor_id=self.actor, client_request_id=self.key(), expected_version=session['version'],
            changes={'pulse': 78}, change_kind='correction'))['session']
        result = self.answer('blood_pressure', '맥박 92회', session)
        self.assertFalse(result['answer']['accepted'])
        self.assertEqual(result['answer']['applied_fields'], [])
        self.assertEqual(result['answer']['ignored_fields'], ['pulse'])
        self.assertEqual(result['session']['fields'], {'pulse': 78})

    def test_accepted_category_answer_never_applies_other_labelled_fields(self):
        text = '의식 명료. 맥박 92회.'
        result = self.answer('consciousness', text)
        self.assertTrue(result['answer']['accepted'])
        self.assertEqual(result['answer']['applied_fields'], ['consciousness'])
        self.assertEqual(result['answer']['ignored_fields'], ['pulse'])
        self.assertEqual(result['session']['fields'], {'consciousness': 'alert'})
        self.assertEqual(result['session']['field_metadata']['consciousness']['evidence'], [text])

    def test_negative_category_answers_are_never_rewritten_as_normal_or_alert(self):
        for question_id, text, forbidden in (
            ('breathing', '정상 아님', 'normal'),
            ('breathing', '정상이 아님', 'normal'),
            ('breathing', '정상이 아니요', 'normal'),
            ('breathing', '정상은 아닙니다', 'normal'),
            ('consciousness', '명료하지 않음', 'alert'),
        ):
            with self.subTest(question=question_id, text=text):
                result = self.answer(question_id, text)
                self.assertNotEqual(result['session']['fields'].get(question_id), forbidden)
        self.assert_answer('breathing', '비정상이요', {'breathing': 'abnormal'})

    def test_unrelated_words_and_numeric_text_are_not_unavailable_aliases(self):
        for text in ('비정상', '호흡 없음', '편안했어요', '넘어졌어요', '모르핀',
                     '패스 20', '미측정 105요', '모르겠습니다 105'):
            with self.subTest(text=text):
                result = self.answer('glucose', text)
                self.assertFalse(result['answer']['unavailable'])
                self.assertNotIn('glucose', result['session']['fields'])

    def test_no_answer_creates_a_patient_or_inquiry(self):
        self.assert_answer('respiratory_rate', '분당 20회', {'respiratory_rate': 20})
        self.answer('glucose', '패스')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM patients').fetchone()[0], 0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM inquiries').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
