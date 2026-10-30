"""Explicit numerical observations, spacing variants and natural update intent.

Synthetic transcript tests only. These do not measure a microphone/STT engine.
"""
import unittest

from server.voice_terms import extract, observation_fields, parse_command

NOW = 2_000_000_000


class NaturalObservationTests(unittest.TestCase):
    def fields(self, text):
        return extract(text, NOW)['fields']

    def test_current_bp_change_has_exact_value_and_observation_intent(self):
        for text in ('혈압이 지금 100에 80까지 떨어졌어',
                     '혈압이현재100에80으로 떨어졌어',
                     '혈압이100에80까지 떨어졌어',
                     '지금 혈압 100/80으로 내려갔어',
                     '비 피 현재 100에 80으로 나왔어'):
            with self.subTest(text=text):
                self.assertEqual(self.fields(text), {'systolic_bp': 100, 'diastolic_bp': 80})
                parsed = parse_command(text)
                self.assertEqual(parsed['action'], 'observe')
                self.assertEqual(parsed['text'], text)
                self.assertEqual(observation_fields(text), ['systolic_bp', 'diastolic_bp'])

    def test_unambiguous_old_to_current_bp_selects_only_explicit_new_value(self):
        for text in ('혈압 120에80에서 지금100에80으로 떨어졌어',
                     '혈압 120/80에서 100/70으로 내려갔어',
                     '혈압 110에70이었다가 현재130에90으로 올랐어',
                     '혈압 120/80에서 지금100/80mmHg로 떨어졌어'):
            with self.subTest(text=text):
                result = extract(text, NOW)
                expected = ({'systolic_bp': 130, 'diastolic_bp': 90} if '130' in text else
                            {'systolic_bp': 100, 'diastolic_bp': 70} if '100/70' in text else
                            {'systolic_bp': 100, 'diastolic_bp': 80})
                self.assertEqual(result['fields'], expected)
                for quotes in result['evidence'].values():
                    self.assertTrue(all(quote in text for quote in quotes))

    def test_current_scalar_and_explicit_transition_keep_new_measurement(self):
        for text, key, value in (
            ('맥박이 지금 110회로 올랐어', 'pulse', 110),
            ('맥박 90에서120으로 올랐어', 'pulse', 120),
            ('산소포화도97에서92로 떨어졌어', 'spo2', 92),
            ('체온37.2도에서 현재38.2도로 올라갔어', 'temperature', 38.2),
            ('호흡수가 현재24회로 나왔어', 'respiratory_rate', 24),
            ('혈당이 방금180으로 측정됐어', 'glucose', 180),
        ):
            with self.subTest(text=text):
                self.assertEqual(self.fields(text), {key: value})
                self.assertEqual(parse_command(text)['action'], 'observe')

    def test_state_updates_are_explicit_and_do_not_diagnose(self):
        for text, expected in (
            ('의식이 지금 명료해졌어', {'consciousness': 'alert'}),
            ('호흡이 현재 비정상으로 바뀌었어', {'breathing': 'abnormal'}),
            ('지금 의 식 명료. 호 흡 정상', {'consciousness': 'alert', 'breathing': 'normal'}),
        ):
            with self.subTest(text=text):
                self.assertEqual(self.fields(text), expected)
                self.assertEqual(parse_command(text)['action'], 'observe')
        self.assertEqual(self.fields('의식이 지금 돌아왔어'), {})

    def test_no_value_change_still_exposes_target_for_followup(self):
        self.assertEqual(self.fields('혈압이 지금 떨어졌어'), {})
        self.assertEqual(parse_command('혈압이 지금 떨어졌어')['action'], 'observe')
        self.assertEqual(observation_fields('혈압이 지금 떨어졌어'), ['systolic_bp', 'diastolic_bp'])
        self.assertEqual(observation_fields('수축기 혈압이 현재90으로 떨어졌어'), ['systolic_bp'])
        self.assertEqual(observation_fields('호흡수가 지금 바뀌었어'), ['respiratory_rate'])

    def test_ranges_uncertain_and_conflicting_values_are_not_latest_measurements(self):
        for text in ('맥박90에서120사이', '혈압100에80 정도로 떨어졌어',
                     '맥박 지금90인지110인지모름',
                     '맥박이 구십인지 백십인지 잘 안 들림',
                     '맥박100으로 떨어졌다는 말인 것 같아',
                     '혈압 120/80. 혈압 100/70',
                     '맥박90에서120으로 올랐어. 맥박130'):
            with self.subTest(text=text):
                self.assertEqual(self.fields(text), {})

    def test_historical_family_question_changes_do_not_trigger_observation(self):
        for text in ('어머니 혈압이 지금 100/80으로 떨어졌어',
                     '과거 맥박이110으로 올랐어', '혈압이 지금100/80입니까?',
                     '호흡수가 지금24회인가요?', '혈압이 지금 떨어졌어?'):
            with self.subTest(text=text):
                self.assertEqual(self.fields(text), {})
                self.assertEqual(observation_fields(text), [])
                self.assertEqual(parse_command(text)['action'], 'unknown')

    def test_correction_remains_distinct_from_new_observation(self):
        parsed = parse_command('혈압 100에80으로 수정해')
        self.assertEqual(parsed['action'], 'edit')
        self.assertEqual(self.fields(parsed['text']), {'systolic_bp': 100, 'diastolic_bp': 80})
        self.assertEqual(self.fields('혈압120/80 아니100/80'), {})


class MedicalNumberFormatTests(unittest.TestCase):
    def test_korean_decimal_with_explicit_supported_unit(self):
        for text, expected in (
            ('체온 삼십칠 점 이 도', {'temperature': 37.2}),
            ('체온 37 점 2 도', {'temperature': 37.2}),
            ('산소포화도 구십칠 점 오 퍼센트', {'spo2': 97.5}),
            ('산소 포화도 구십칠 점 오 이 퍼센트', {'spo2': 97.52}),
        ):
            with self.subTest(text=text):
                result = extract(text, NOW)
                self.assertEqual(result['fields'], expected)
                self.assertTrue(all(q in text for quotes in result['evidence'].values() for q in quotes))

    def test_partial_or_overprecise_decimals_never_truncate(self):
        for text in ('체온 삼십칠 점 이', '체온 삼십칠 점',
                     '체온 삼십칠 점 이 오 육 도', '체온37.234도',
                     '호흡수 이십 점 오 회', '혈당5.5mmol/L'):
            with self.subTest(text=text):
                self.assertEqual(extract(text, NOW)['fields'], {})

    def test_medical_aliases_tolerate_spacing_not_changed_meaning(self):
        for text, expected in (
            ('맥 박 아흔둘 회', {'pulse': 92}),
            ('산 소 포 화 도 구십칠 퍼센트', {'spo2': 97}),
            ('체 온 삼십칠 점 이 도', {'temperature': 37.2}),
            ('혈 당 BG 160 mg/dL', {'glucose': 160}),
            ('에이치 알 92', {'pulse': 92}),
            ('알 알 20회', {'respiratory_rate': 20}),
        ):
            with self.subTest(text=text):
                self.assertEqual(extract(text, NOW)['fields'], expected)

    def test_wrong_explicit_units_never_become_canonical_measurements(self):
        for text in ('맥박90퍼센트', '호흡수20도', '체온37.2회',
                     '산소포화도97mmHg', '혈당100퍼센트', '혈압120/80퍼센트'):
            with self.subTest(text=text):
                self.assertEqual(extract(text, NOW)['fields'], {})

    def test_severely_damaged_transcript_does_not_reconstruct_expected_script(self):
        for text in ('2시간 후면 정상입니다', '고급 수입 회', '도와 주려는 없습니다',
                     '혈압1208092회', '산소포화도 구팔'):
            with self.subTest(text=text):
                self.assertEqual(extract(text, NOW)['fields'], {})


if __name__ == '__main__':
    unittest.main()
