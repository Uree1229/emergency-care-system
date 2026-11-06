"""Bounded uncertainty for complete symptom-list reports; synthetic text only."""
import unittest

from server import db, voice_input, voice_terms


NOW = 2_000_000_000
LIST_KEYS = ('symptoms', 'denied_symptoms', 'uncertain_symptoms')


def symptoms(fields):
    return {key: set(fields[key]) for key in LIST_KEYS if key in fields}


class IsolatedUncertaintyTests(unittest.TestCase):
    def test_complete_symptom_list_shares_its_explicit_uncertainty(self):
        cases = (
            ('경련 실신 의심', {'경련', '실신'}),
            ('경련 실신 미확인', {'경련', '실신'}),
            ('경련 실신 여부 미확인', {'경련', '실신'}),
            ('증상은 경련 실신 미확인', {'경련', '실신'}),
            ('흉통 호흡곤란 미확인', {'흉통', '호흡곤란'}),
            ('구토 오심 어지럼 의심', {'구토', '오심', '어지럼'}),
            ('흉통과 호흡곤란 구토 어지럼 미확인', {'흉통', '호흡곤란', '구토', '어지럼'}),
        )
        for text, expected in cases:
            with self.subTest(text=text):
                for extract in (voice_terms.extract, voice_input._extract):
                    result = extract(text, NOW)
                    self.assertEqual(symptoms(result['fields']), {'uncertain_symptoms': expected})
                    self.assertTrue(all(quote in text for quotes in result['evidence'].values() for quote in quotes))

    def test_comma_segment_isolated_from_other_vitals(self):
        text = '혈압 120-80, 경련 실신 미확인, 맥박 90'
        fields = voice_terms.extract(text, NOW)['fields']
        self.assertEqual(symptoms(fields), {'uncertain_symptoms': {'경련', '실신'}})
        self.assertEqual((fields['systolic_bp'], fields['diastolic_bp'], fields['pulse']), (120, 80, 90))

    def test_mixed_reports_do_not_gain_shared_uncertainty(self):
        for text in ('53세 남성 흉통과 호흡곤란 구토 어지럼 미확인 맥박 90',
                     '흉통과 호흡곤란 구토 어지럼 미확인 의식 명료',
                     '의식 명료 흉통과 호흡곤란 구토 어지럼 미확인'):
            with self.subTest(text=text):
                fields = voice_terms.extract(text, NOW)['fields']
                self.assertEqual(set(fields.get('uncertain_symptoms', [])), {'어지럼'})
                self.assertEqual(set(fields.get('symptoms', [])), {'흉통', '호흡곤란', '구토'})

    def test_predicate_and_comma_keep_distinct_local_states(self):
        cases = (
            ('경련 있음 실신 미확인', {'symptoms': {'경련'}, 'uncertain_symptoms': {'실신'}}),
            ('경련 없고 실신 미확인', {'denied_symptoms': {'경련'}, 'uncertain_symptoms': {'실신'}}),
            ('경련, 실신 미확인', {'symptoms': {'경련'}, 'uncertain_symptoms': {'실신'}}),
            ('경련 실신, 미확인', {'symptoms': {'경련', '실신'}}),
            ('경련 실신 미확인, 구토 있음', {'uncertain_symptoms': {'경련', '실신'}, 'symptoms': {'구토'}}),
        )
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(symptoms(voice_terms.extract(text, NOW)['fields']), expected)

    def test_negated_or_open_ended_tail_does_not_gain_propagation(self):
        for text in ('경련 실신 의심 아님', '경련 실신 미확인 아님',
                     '경련 실신 미확인 90', '경련 실신 의심하지만 확인 필요',
                     '경련 실신 없음 미확인'):
            with self.subTest(text=text):
                fields = voice_terms.extract(text, NOW)['fields']
                self.assertNotIn('경련', fields.get('uncertain_symptoms', []))

    def test_quotes_examples_history_and_questions_do_not_gain_propagation(self):
        for text in ('예시, 경련 실신 미확인, 라고 읽으세요',
                     '"경련 실신 미확인"', '인용, 경련 실신 의심',
                     '경련 실신 미확인, 라고 말했다'):
            with self.subTest(text=text):
                fields = voice_terms.extract(text, NOW)['fields']
                self.assertNotIn('경련', fields.get('uncertain_symptoms', []))
        for text in ('과거 경련 실신 미확인', '어머니 경련 실신 의심', '경련 실신 미확인인가요?'):
            with self.subTest(text=text):
                self.assertEqual(symptoms(voice_terms.extract(text, NOW)['fields']), {})

    def test_existing_single_symptom_suspected_r1_menu_is_preserved(self):
        self.assertEqual(voice_input._extract('경련 의심', NOW)['fields']['chief_complaint'], 'altered_seizure')

    def test_state_predicate_aliases_and_repeated_names_are_not_a_new_shared_list(self):
        for text, expected in (
            ('부어 있음 경련 미확인', {'symptoms': {'부종'}, 'uncertain_symptoms': {'경련'}}),
            ('토함 경련 미확인', {'symptoms': {'구토'}, 'uncertain_symptoms': {'경련'}}),
            ('경련 경련 미확인', {}),
        ):
            with self.subTest(text=text):
                self.assertEqual(symptoms(voice_terms.extract(text, NOW)['fields']), expected)

    def test_quote_or_family_context_in_other_sentences_prevents_new_sharing(self):
        for text in ('예시는 다음과 같습니다. 경련 실신 미확인.',
                     '어머니는 흉통입니다. 경련 실신 미확인.',
                     '예시 과거 기록, 경련 실신 미확인',
                     '과거 기록입니다. 경련 실신 미확인.'):
            with self.subTest(text=text):
                fields = voice_terms.extract(text, NOW)['fields']
                self.assertNotIn('경련', fields.get('uncertain_symptoms', []))


class IsolatedUncertaintyDraftTests(unittest.TestCase):
    def test_uncertain_list_is_asked_again_and_explicit_denials_resolve_it(self):
        conn = db.connect(':memory:')
        try:
            db.init(conn)
            actor = 'isolated-uncertainty-regression'
            session = voice_input.create(conn, NOW, dict(actor_id=actor, client_request_id='create'))['session']
            text = '경련 실신 미확인'
            first = voice_input.transcript(conn, NOW, session['id'], dict(
                actor_id=actor, client_request_id='first', expected_version=session['version'],
                utterance_id='first', text=text, mode='auto', is_final=True))
            session = first['session']
            self.assertEqual(symptoms(session['fields']), {'uncertain_symptoms': {'경련', '실신'}})
            self.assertEqual(session['utterances'][-1]['transcript'], text)
            followup = voice_input.followup(conn, NOW, session['id'], actor)['followup']
            question = next(item for item in followup['items'] if item['id'] == 'symptoms')
            self.assertEqual(set(question['uncertain_items']), {'경련', '실신'})
            answer = '경련 없음. 실신 없음'
            result = voice_input.answer_followup(conn, NOW, session['id'], dict(
                actor_id=actor, client_request_id='answer', expected_version=session['version'],
                utterance_id='answer', question_id='symptoms', profile='voice_extended_v1', text=answer))
            self.assertTrue(result['answer']['accepted'])
            self.assertFalse(result['session']['fields'].get('uncertain_symptoms'))
            self.assertEqual(set(result['session']['fields']['denied_symptoms']), {'경련', '실신'})
            self.assertNotIn('symptoms', [item['id'] for item in result['followup']['items']])
            self.assertEqual(result['session']['utterances'][-1]['transcript'], answer)
            self.assertIsNone(result['session']['patient_id'])
            for table in ('patients', 'inquiries', 'hospitals'):
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0], 0)
        finally:
            conn.close()


if __name__ == '__main__':
    unittest.main()
