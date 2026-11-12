"""Blood-pressure formatting and symptom-list scope; no audio/ASR claims."""
import unittest

from server import db, voice_input, voice_terms


NOW = 2_000_000_000
BP_KEYS = ('systolic_bp', 'diastolic_bp')
SYMPTOM_KEYS = ('symptoms', 'denied_symptoms', 'uncertain_symptoms')


def project(fields, keys):
    return {key: fields[key] for key in keys if key in fields}


class BloodPressureFormatTests(unittest.TestCase):
    def test_dash_form_preserves_both_numbers_and_verbatim_evidence(self):
        for text in ('혈압 120-80', '혈압 118 - 76입니다.', 'BP 126–82',
                     '비피 112−72', '혈압 130-85 mmHg'):
            with self.subTest(text=text):
                result = voice_terms.extract(text, NOW)
                self.assertEqual(set(project(result['fields'], BP_KEYS)), set(BP_KEYS))
                self.assertGreater(result['fields']['systolic_bp'], result['fields']['diastolic_bp'])
                self.assertTrue(all(quote in text for key in BP_KEYS for quote in result['evidence'][key]))
        self.assertEqual(project(voice_input._extract('혈압 120-80', NOW)['fields'], BP_KEYS),
                         dict(systolic_bp=120, diastolic_bp=80))

    def test_ambiguous_invalid_or_single_measurement_ranges_never_supply_a_pair(self):
        for text in ('혈압 120--80', '혈압 120-80-70', '혈압 120-80~90',
                     '혈압 -120-80', '혈압 80-120', '혈압 80-80',
                     '혈압 120~80', '혈압 약 120-80', '혈압 120-80 사이',
                     '혈압 120-80 범위', '혈압 120-80입니까?',
                     '과거 혈압 120-80', '수축기 혈압 120-80',
                     '이완기 혈압 90-60', '수 축 기 혈압 120-80',
                     '수축기 혈압 120–80', '이완기 혈압 90−60',
                     '혈압 400-80', '혈압 120-400', '혈압 120-80mmHg-70',
                     '혈압 120-80mmHg 범위', '혈압 120-80과130-90',
                     '혈압 120-80 및130-90', '혈압 120-80e2', '혈압 120-80번',
                     '혈압120-80에서130--85로 올랐어',
                     '혈압120-80에서130-400으로 올랐어',
                     '혈압120-80에서400-85로 올랐어'):
            with self.subTest(text=text):
                self.assertEqual(project(voice_terms.extract(text, NOW)['fields'], BP_KEYS), {})

    def test_next_label_and_existing_pair_forms_keep_their_values(self):
        text = '혈압 122-78, 맥박 92회'
        result = voice_terms.extract(text, NOW)['fields']
        self.assertEqual(project(result, (*BP_KEYS, 'pulse')),
                         dict(systolic_bp=122, diastolic_bp=78, pulse=92))
        for text in ('혈압 122/78', '혈압 122에78', '혈압 122 78'):
            with self.subTest(text=text):
                self.assertEqual(project(voice_terms.extract(text, NOW)['fields'], BP_KEYS),
                                 dict(systolic_bp=122, diastolic_bp=78))


class SymptomListScopeTests(unittest.TestCase):
    def test_shared_trailing_denial_and_uncertainty_include_bare_space_lists(self):
        for text in ('경련 실신 없음', '경련과 실신 없음', '경련 및 실신 없음',
                     '경련 실신 구토 없음'):
            with self.subTest(text=text):
                result = voice_terms.extract(text, NOW)
                self.assertNotIn('symptoms', result['fields'])
                self.assertEqual(set(result['fields']['denied_symptoms']),
                                 {'경련', '실신', '구토'} if '구토' in text else {'경련', '실신'})
                self.assertTrue(all(quote in text for quotes in result['evidence'].values() for quote in quotes))
        result = voice_terms.extract('경련과 실신 여부 미확인', NOW)['fields']
        self.assertEqual(project(result, SYMPTOM_KEYS), {'uncertain_symptoms': ['경련', '실신']})

    def test_positive_predicate_comma_and_other_field_end_the_shared_scope(self):
        cases = (
            ('경련 있고 실신 없음', {'symptoms': ['경련'], 'denied_symptoms': ['실신']}),
            ('경련, 실신 없음', {'symptoms': ['경련'], 'denied_symptoms': ['실신']}),
            ('경련 실신 있음, 구토 없음', {'symptoms': ['경련', '실신'], 'denied_symptoms': ['구토']}),
            ('경련 실신 맥박 90 없음', {'symptoms': ['경련', '실신']}),
            ('경련 실신, 보호자 없음', {'symptoms': ['경련', '실신']}),
            ('경련 실신, 의심됨', {'symptoms': ['경련', '실신']}),
            ('경련 실신 구토, 보호자 없음', {'symptoms': ['구토', '경련', '실신']}),
            ('경련 실신 맥박 90회, 출혈 없음', {'symptoms': ['경련', '실신'], 'denied_symptoms': ['출혈']}),
        )
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(project(voice_terms.extract(text, NOW)['fields'], SYMPTOM_KEYS), expected)

    def test_mixed_report_uncertainty_does_not_spread_over_separate_observations(self):
        # A pure symptom-only segment now shares uncertainty by its declared
        # list contract. Age/vitals protect the mixed report used by V117.
        text = '53세 남성 흉통과 호흡곤란 구토 어지럼 미확인 의식 명료'
        fields = voice_terms.extract(text, NOW)['fields']
        self.assertEqual(fields.get('uncertain_symptoms'), ['어지럼'])
        self.assertEqual(set(fields.get('symptoms', [])), {'흉통', '호흡곤란', '구토'})
        self.assertEqual(voice_terms.extract('경련이 없는 것은 아님', NOW)['fields'],
                         {'uncertain_symptoms': ['경련']})

    def test_denied_r1_symptom_candidates_do_not_override_independent_evidence(self):
        for text in ('경련 실신 없음', '경련과 실신 없음'):
            with self.subTest(text=text):
                result = voice_input._extract(text, NOW)['fields']
                self.assertNotIn('chief_complaint', result)
                self.assertNotIn('symptoms', result)
        cases = (
            ('경련 실신 없음. 흉통 있음', 'cardiac'),
            ('경련 실신 없음. 의식 저하', 'altered_seizure'),
            ('흉통 없음. 심근경색 의심', 'cardiac'),
            ('경련 의심', 'altered_seizure'),
        )
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(voice_input._extract(text, NOW)['fields'].get('chief_complaint'), expected)


class VoiceDraftFormatTests(unittest.TestCase):
    def answer_pressure(self, conn, index, text):
        actor = 'pressure-answer-regression'
        session = voice_input.create(conn, NOW, {
            'actor_id': actor, 'client_request_id': f'answer-create-{index}'})['session']
        body = dict(actor_id=actor, client_request_id=f'answer-write-{index}',
                    expected_version=session['version'], utterance_id=f'answer-utterance-{index}',
                    question_id='blood_pressure', profile='voice_extended_v1', text=text)
        return voice_input.answer_followup(conn, NOW, session['id'], body)

    def test_guided_pressure_answers_accept_dash_pairs_and_preserve_raw_evidence(self):
        conn = db.connect(':memory:')
        try:
            db.init(conn)
            for index, text in enumerate(('120-80', '120–80', '120−80', '혈압 120-80',
                                          '현재 혈압 120 - 80 mmHg입니다')):
                with self.subTest(text=text):
                    response = self.answer_pressure(conn, index, text)
                    self.assertTrue(response['answer']['accepted'])
                    self.assertEqual(response['session']['fields'], {'systolic_bp': 120, 'diastolic_bp': 80})
                    self.assertEqual(response['session']['utterances'][-1]['transcript'], text)
                    for key in BP_KEYS:
                        self.assertEqual(response['session']['field_metadata'][key]['evidence'], [text])
                    self.assertNotIn('blood_pressure', [q['id'] for q in response['followup']['items']])
                    self.assertEqual(response['operational_effect'], 'draft_only')
        finally:
            conn.close()

    def test_guided_pressure_answers_do_not_choose_ranges_or_extra_values(self):
        conn = db.connect(':memory:')
        try:
            db.init(conn)
            for index, text in enumerate(('120--80', '-120-80', '80-120', '120-400',
                                          '400-80', '120-80-70', '120-80~90',
                                          '120-80 맥박 90', '약 120-80', '120-80입니까?',
                                          '120-80범위', '120-80번', '수축기 120-80')):
                with self.subTest(text=text):
                    response = self.answer_pressure(conn, index, text)
                    self.assertFalse(response['answer']['accepted'])
                    self.assertEqual(project(response['session']['fields'], BP_KEYS), {})
                    self.assertIn('blood_pressure', [q['id'] for q in response['followup']['items']])
                    self.assertEqual(response['session']['utterances'][-1]['transcript'], text)
        finally:
            conn.close()

    def test_final_transcript_keeps_raw_evidence_and_retry_does_not_duplicate(self):
        conn = db.connect(':memory:')
        try:
            db.init(conn)
            for index, (text, expected) in enumerate((
                ('혈압 120-80', {'systolic_bp': 120, 'diastolic_bp': 80}),
                ('경련 실신 없음', {'denied_symptoms': ['경련', '실신']}),
            )):
                session = voice_input.create(conn, NOW, {
                    'actor_id': 'format-regression', 'client_request_id': f'create-{index}'})['session']
                body = dict(actor_id='format-regression', client_request_id=f'write-{index}',
                            expected_version=session['version'], utterance_id=f'utterance-{index}',
                            text=text, mode='auto', is_final=True)
                response = voice_input.transcript(conn, NOW, session['id'], body)
                final = response['session']
                self.assertEqual(final['fields'], expected)
                self.assertEqual(final['utterances'][0]['transcript'], text)
                self.assertEqual(response['operational_effect'], 'draft_only')
                self.assertEqual(final['state'], 'DRAFT')
                self.assertIsNone(final['patient_id'])
                retry = voice_input.transcript(conn, NOW, session['id'], body)
                self.assertTrue(retry['duplicate'])
                self.assertEqual(retry['session']['version'], final['version'])
                self.assertEqual(len(retry['session']['utterances']), 1)
            for table in ('patients', 'inquiries', 'hospitals'):
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0], 0)
        finally:
            conn.close()


if __name__ == '__main__':
    unittest.main()
