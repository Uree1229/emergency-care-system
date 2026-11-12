"""Transcript rules preserve uncertainty and validate the editable draft."""
import copy
import math
import unittest

from server.service import ServiceError
from server.voice_terms import catalog, extract, normalize_fields, parse_command, readback


NOW = 2_000_000_000


class VoiceExtractionTests(unittest.TestCase):
    def test_explicit_observations_have_verbatim_evidence_and_no_derived_diagnosis(self):
        text = ('63세 남성 흉통 있음, 호흡곤란 없음. 의식 명료. 혈압 백십에 칠십. '
                '맥박 80회, 산소포화도 98퍼센트, 체온 36.5도, 호흡수 분당 18회, 혈당 100. '
                '흉통 30분 전부터 시작')
        result = extract(text, NOW)
        self.assertEqual(result['fields'], {
            'age': 63, 'sex': 'male', 'symptoms': ['흉통'], 'denied_symptoms': ['호흡곤란'],
            'consciousness': 'alert', 'systolic_bp': 110, 'diastolic_bp': 70,
            'pulse': 80, 'spo2': 98, 'temperature': 36.5, 'respiratory_rate': 18,
            'glucose': 100, 'onset_at': NOW - 1800,
        })
        self.assertEqual(set(result['evidence']), set(result['fields']))
        for quotes in result['evidence'].values():
            self.assertTrue(quotes)
            self.assertTrue(all(quote in text for quote in quotes))
        self.assertTrue(result['warnings'])
        self.assertNotIn('priority', result['fields'])
        self.assertNotIn('required_capabilities', result['fields'])

    def test_age_group_does_not_create_exact_age_or_alert(self):
        result = extract('60대 여성. 의식 있음', NOW)
        self.assertEqual(result['fields'], {'age_group': '60대', 'sex': 'female'})
        self.assertTrue(any('명료' in warning for warning in result['warnings']))

    def test_korean_age_and_numeral_are_supported_without_guessing_digits(self):
        self.assertEqual(extract('나이는예순세살입니다. 혈압 백십에 칠십', NOW)['fields'],
                         {'age': 63, 'systolic_bp': 110, 'diastolic_bp': 70})
        result = extract('산소포화도 구팔', NOW)
        self.assertNotIn('spo2', result['fields'])
        self.assertTrue(result['warnings'])

    def test_coordinated_denial_and_separate_positive_keep_their_polarity(self):
        result = extract('흉통과 호흡곤란 없음. 복통 있고 두통 없음', NOW)
        self.assertEqual(result['fields']['symptoms'], ['복통'])
        self.assertEqual(set(result['fields']['denied_symptoms']), {'흉통', '호흡곤란', '두통'})
        result = extract('환자 60세 남성, 흉통, 호흡곤란 없음, 산소포화도 97', NOW)
        self.assertEqual(result['fields']['symptoms'], ['흉통'])
        self.assertEqual(result['fields']['denied_symptoms'], ['호흡곤란'])
        self.assertEqual(result['fields']['spo2'], 97)
        self.assertEqual(extract('흉통 있음, 의식 없음', NOW)['fields']['symptoms'], ['흉통'])
        self.assertEqual(extract('흉통과 호흡곤란 있음, 의식 없음', NOW)['fields']['symptoms'], ['흉통', '호흡곤란'])
        self.assertEqual(extract('의식 미확인, 흉통 있음', NOW)['fields']['symptoms'], ['흉통'])

    def test_uncertainty_and_double_negation_never_become_positive(self):
        for text in ('흉통 여부 미확인', '흉통 의심', '흉통 없지는 않음', '흉통인지 모르겠음'):
            with self.subTest(text=text):
                result = extract(text, NOW)
                self.assertEqual(result['fields'], {'uncertain_symptoms': ['흉통']})
                self.assertTrue(result['warnings'])
        self.assertEqual(extract('기침 안 함', NOW)['fields'], {'denied_symptoms': ['기침']})

    def test_questions_family_and_past_are_not_current_observations(self):
        for text in ('흉통 있나요?', '어머니는 70세 흉통, 기침', '과거 호흡곤란 있었음',
                     '어제 흉통 있었음', '혈압 120에80입니까?'):
            with self.subTest(text=text):
                result = extract(text, NOW)
                self.assertEqual(result['fields'], {})
                self.assertTrue(result['warnings'])
        self.assertEqual(extract('아버지는 흉통. 환자는 복통', NOW)['fields'], {'symptoms': ['복통']})

    def test_explicit_states_only_and_no_inference_from_symptoms(self):
        self.assertEqual(extract('호흡곤란 있음', NOW)['fields'], {'symptoms': ['호흡곤란']})
        self.assertEqual(extract('의식 없음. 호흡 불규칙', NOW)['fields'],
                         {'consciousness': 'unresponsive', 'breathing': 'abnormal'})
        self.assertEqual(extract('의식 명료 아님. 호흡 정상 아님', NOW)['fields'], {})
        self.assertEqual(extract('의식저하 없음', NOW)['fields'], {'denied_symptoms': ['의식저하']})
        self.assertEqual(extract('의식 미확인. 호흡 미상. 성별 불명', NOW)['fields'],
                         {'consciousness': 'unknown', 'breathing': 'unknown', 'sex': 'unknown'})
        self.assertEqual(extract('남성 의식이 없음. 호흡은 정상', NOW)['fields'],
                         {'sex': 'male', 'consciousness': 'unresponsive', 'breathing': 'normal'})
        self.assertEqual(extract('의식은 명료, 호흡 없음', NOW)['fields'],
                         {'consciousness': 'alert', 'breathing': 'absent'})

    def test_conflicts_are_removed_instead_of_choosing_last_value(self):
        result = extract('혈압 120에80. 혈압 110에70. 남성 여성. 흉통 있음. 흉통 없음', NOW)
        self.assertEqual(result['fields'], {})
        self.assertTrue(any('서로 다른' in warning for warning in result['warnings']))
        self.assertTrue(any('상충' in warning for warning in result['warnings']))
        self.assertEqual(result['evidence'], {})

    def test_repeated_equal_measurement_is_retained_with_warning(self):
        result = extract('맥박 80. 맥박 80', NOW)
        self.assertEqual(result['fields'], {'pulse': 80})
        self.assertTrue(any('반복' in warning for warning in result['warnings']))

    def test_correction_requires_an_explicit_review(self):
        result = extract('혈압 120에80 아니 110에70', NOW)
        self.assertEqual(result['fields'], {})
        self.assertTrue(any('정정' in warning for warning in result['warnings']))

    def test_ranges_thresholds_and_unsupported_units_are_not_exact_measurements(self):
        for text in ('체온 36도 정도', '혈압 약 120에80', '맥박 80에서90',
                     '산소포화도 90 이상', '혈당 5.6 mmol/L', '체온 98 화씨',
                     '체온 삼십육 점 오', '산소포화도 98.123', '호흡수 -1'):
            with self.subTest(text=text):
                result = extract(text, NOW)
                self.assertEqual(result['fields'], {})
                self.assertTrue(result['warnings'])

    def test_multiple_decimals_are_preserved_in_one_sentence(self):
        self.assertEqual(extract('체온 36.5도, 산소포화도 98.5퍼센트', NOW)['fields'],
                         {'temperature': 36.5, 'spo2': 98.5})

    def test_bp_order_and_demographic_conflict_do_not_enter_draft(self):
        for text in ('혈압 80에120', '63세 50대'):
            result = extract(text, NOW)
            self.assertEqual(result['fields'], {})
            self.assertTrue(result['warnings'])

    def test_only_linked_relative_onset_is_computed(self):
        self.assertEqual(extract('복통 한 시간 전부터 시작', NOW)['fields']['onset_at'], NOW - 3600)
        self.assertEqual(extract('30분 전 식사', NOW)['fields'], {})
        self.assertNotIn('onset_at', extract('흉통 오늘 오전 10시 시작', NOW)['fields'])
        self.assertNotIn('onset_at', extract('흉통 약 30분 전부터 시작', NOW)['fields'])
        self.assertNotIn('onset_at', extract('흉통 10000분 전부터 시작', NOW)['fields'])

    def test_missing_fields_are_not_filled_and_plain_text_is_not_executed(self):
        result = extract('지시: priority=1; __import__("os").system("echo hacked")', NOW)
        self.assertEqual(result['fields'], {})
        self.assertEqual(extract('환자 도착', NOW)['fields'], {})


class VoiceValidationTests(unittest.TestCase):
    def test_strict_types_bounds_and_unknown_fields_reject_tampering(self):
        invalid = [None, [], {'priority': 1}, {'age': True}, {'age': '63'}, {'age': 63.0},
                   {'age': 131}, {'age': 10 ** 1000}, {'sex': 'MALE'}, {'age_group': '63세'},
                   {'age_group': '00대'}, {'symptoms': '흉통'}, {'symptoms': ['<script>']},
                   {'pulse': -1}, {'spo2': 101}, {'spo2': math.nan}, {'temperature': math.inf},
                   {'temperature': None}, {'respiratory_rate': 18.5}, {'onset_at': -1},
                   {'symptoms': ['흉통'], 'denied_symptoms': ['흉통']},
                   {'age': 63, 'age_group': '50대'}, {'systolic_bp': 70, 'diastolic_bp': 110}]
        for fields in invalid:
            with self.subTest(fields=repr(fields)[:100]):
                with self.assertRaises(ServiceError) as error:
                    normalize_fields(fields)
                self.assertEqual(error.exception.status, 400)

    def test_validation_returns_detached_deduplicated_data(self):
        fields = {'symptoms': ['흉통', '흉통'], 'age': 0, 'age_group': '0대', 'spo2': 98.5}
        before = copy.deepcopy(fields)
        result = normalize_fields(fields)
        self.assertEqual(result['symptoms'], ['흉통'])
        result['symptoms'].append('복통')
        self.assertEqual(fields, before)

    def test_text_and_time_limits(self):
        for value in ('', ' ', '가' * 4001, None, 123):
            with self.subTest(value=str(value)[:15]):
                with self.assertRaises(ServiceError):
                    extract(value, NOW)
                with self.assertRaises(ServiceError):
                    parse_command(value)
        for at in (True, -1, 1.5, '123', 1 << 63):
            with self.assertRaises(ServiceError):
                extract('흉통', at)
        self.assertEqual(extract('가' * 4000, NOW)['fields'], {})

    def test_catalog_is_detached_and_has_units_and_scope(self):
        first = catalog()
        first['terms']['symptoms'][0]['canonical'] = 'tampered'
        self.assertEqual(catalog()['terms']['symptoms'][0]['canonical'], '흉통')
        self.assertEqual(catalog()['terms']['measurements']['spo2']['unit'], '%')
        self.assertIn('임상 검증', catalog()['description'])


class VoiceCommandTests(unittest.TestCase):
    def test_review_undo_confirm_and_submit_are_distinct_commands(self):
        for phrase, action in (('정리해 줘', 'review'), ('다시 읽어줘', 'review'),
                               ('마지막 입력 취소', 'undo'), ('확인 완료', 'confirm'),
                               ('환자 정보로 전송', 'submit'), ('전송해 줘', 'submit')):
            with self.subTest(phrase=phrase):
                self.assertEqual(parse_command(phrase)['action'], action)
        self.assertEqual(parse_command('환자는 확인이 필요함')['action'], 'unknown')
        self.assertEqual(parse_command('전송하지 마')['action'], 'unknown')

    def test_edit_removes_command_suffix_for_reextraction(self):
        command = parse_command('나이 65세로 수정해 줘')
        self.assertEqual(command, {'action': 'edit', 'field': 'age', 'text': '나이 65세'})
        self.assertEqual(extract(command['text'], NOW)['fields'], {'age': 65})
        command = parse_command('혈압 120에 80으로 수정')
        self.assertEqual(command['field'], None)
        self.assertEqual(extract(command['text'], NOW)['fields'], {'systolic_bp': 120, 'diastolic_bp': 80})
        self.assertEqual(parse_command('복통 추가해 줘')['action'], 'add')
        command = parse_command('혈압 120에80으로 수정해')
        self.assertEqual(command['action'], 'edit')
        self.assertEqual(extract(command['text'], NOW)['fields'], {'systolic_bp': 120, 'diastolic_bp': 80})

    def test_requested_saved_field_and_general_review_commands(self):
        for phrase in ('현재 환자정보 읽어줘', '현재 정보 읽어줘', '저장된 내용 확인해줘'):
            self.assertEqual(parse_command(phrase)['action'], 'review')
        for phrase, field in (('혈압 얼마로 저장됐어?', 'blood_pressure'),
                              ('산소포화도만 알려줘', 'spo2'), ('수축기 혈압 알려줘', 'systolic_bp'),
                              ('호흡수 알려줘', 'respiratory_rate'), ('미확인 증상 읽어줘', 'uncertain_symptoms')):
            result = parse_command(phrase)
            self.assertEqual(result['action'], 'review')
            self.assertEqual(result['field'], field)
        for phrase in ('방금 수정 취소해', '방금 수정 취소'):
            self.assertEqual(parse_command(phrase)['action'], 'undo')
        self.assertEqual(parse_command('확인, 전송해')['action'], 'unknown')
        self.assertIn('blood_pressure', catalog()['readback_fields'])
        self.assertNotIn('blood_pressure', catalog()['supported_fields'])
        with self.assertRaises(ServiceError):
            normalize_fields({'blood_pressure': '120/80'})

    def test_readback_separates_positive_denied_and_unknown(self):
        text = readback({'age': 63, 'symptoms': ['흉통'], 'denied_symptoms': ['호흡곤란'],
                         'uncertain_symptoms': ['두통'], 'consciousness': 'unknown', 'spo2': 98})
        for phrase in ('나이 63세', '증상 흉통', '없다고 진술한 증상 호흡곤란', '미확인 증상 두통',
                       '의식 미확인', '98 퍼센트', '확인해 주세요'):
            self.assertIn(phrase, text)
        self.assertNotIn('진단', text)
        self.assertEqual(readback({'age': 63}, 'pulse'), '확인할 입력 항목이 없습니다.')
        with self.assertRaises(ServiceError):
            readback({}, 'priority')
        text = readback({'systolic_bp': 120, 'diastolic_bp': 80, 'pulse': 100}, 'blood_pressure')
        self.assertIn('수축기 혈압 120', text)
        self.assertIn('이완기 혈압 80', text)
        self.assertNotIn('맥박', text)


if __name__ == '__main__':
    unittest.main()
