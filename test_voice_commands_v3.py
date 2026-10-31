"""Command intent corpus plus boundaries; synthetic text, no microphone/UI."""
import re
import unittest

from server import voice_commands as commands


CONFIG = commands.catalog()


def field(text, include_readback=False):
    compact = re.sub(r'\s+', '', text).casefold()
    matches = []
    for key, names in CONFIG['field_aliases'].items():
        if key == 'blood_pressure' and not include_readback:
            continue
        for name in names:
            name = re.sub(r'\s+', '', name).casefold()
            for match in re.finditer(re.escape(name), compact):
                matches.append((match.start(), match.end(), key))
    selected = []
    for item in sorted(matches, key=lambda hit: (-(hit[1]-hit[0]), hit[0])):
        if not any(item[0] < old[1] and old[0] < item[1] for old in selected):
            selected.append(item)
    fields = {item[2] for item in selected}
    return next(iter(fields)) if len(fields) == 1 else None


def parse(text, **extra):
    return commands.parse(text, field, **extra)


class CommandPoolTests(unittest.TestCase):
    def test_labeled_realistic_phrasing_corpus(self):
        for example in CONFIG['examples']:
            with self.subTest(text=example['text']):
                result = parse(example['text'])
                self.assertEqual(result['action'], example['action'], result)
                if 'field' in example:
                    self.assertEqual(result['field'], example['field'], result)
                for key in ('fields', 'read_only', 'rejected'):
                    if key in example:
                        self.assertEqual(result.get(key), example[key], result)

    def test_legacy_exact_edit_shape_preserves_payload_for_reextraction(self):
        self.assertEqual(parse('나이 65세로 수정해 줘'),
            dict(action='edit', field='age', text='나이 65세'))
        self.assertEqual(parse('혈압 120에 80으로 수정'),
            dict(action='edit', field=None, text='혈압 120에 80'))
        self.assertEqual(parse('혈압 120에80으로 수정해'),
            dict(action='edit', field=None, text='혈압 120에80'))

    def test_polite_edits_retain_observed_text_not_normalized_or_inferred_values(self):
        for text, expected in (
            ('일단 맥박을 92회로 고쳐 주세요', '맥박을 92회'),
            ('체온 삼십칠 점 이 도로 바꿔 주시겠어요', '체온 삼십칠 점 이 도'),
            ('혈압 100에 80, 맥박 95로 정정해 주세요', '혈압 100에 80, 맥박 95'),
            ('정정 나이 50세', '나이 50세'),
        ):
            with self.subTest(text=text):
                result = parse(text)
                self.assertEqual(result['action'], 'edit')
                self.assertEqual(result['text'], expected)

    def test_readback_of_one_or_multiple_explicit_fields(self):
        self.assertEqual(parse('혈압과 맥박을 알려 주세요')['fields'], ['blood_pressure', 'pulse'])
        result = parse('의식 AVPU를 읽어 주세요')
        self.assertEqual((result['action'], result['field']), ('review', 'avpu'))

    def test_explicit_saved_value_queries_stay_read_only_even_with_numbers(self):
        for text in ('맥박 95로 저장됐는지 알려줘', '맥박 95로 기록되어 있는 값 확인해줘',
                     '맥박 95가 맞는지 조회해줘', '혈압 100에80과 맥박95로 저장된 값 알려줘'):
            with self.subTest(text=text):
                result = parse(text)
                self.assertEqual(result['action'], 'review')
                self.assertTrue(result['read_only'])
        self.assertNotIn('read_only', parse('혈압 120에 80 확인해'))

    def test_history_of_explicit_field_precedes_general_readback(self):
        for text, expected in (
            ('맥박 변경 이력 알려줘', 'pulse'), ('혈압 이력 보여줘', 'blood_pressure'),
            ('최근 체온 수정내역 읽어주세요', 'temperature'), ('맥박 최근 변경내역 확인해줘', 'pulse'),
        ):
            with self.subTest(text=text):
                result = parse(text)
                self.assertEqual((result['action'], result['field']), ('history', expected))
        result = parse('혈압과 맥박 변경이력 알려줘')
        self.assertEqual(result['action'], 'history')
        self.assertEqual(result['fields'], ['blood_pressure', 'pulse'])

    def test_remove_requires_whole_supported_field_names_and_never_patient_cancel(self):
        result = parse('혈압이랑 맥박을 지워 주세요')
        self.assertEqual((result['action'], result['field'], result['fields']),
                         ('remove', None, ['blood_pressure', 'pulse']))
        for text in ('환자 삭제해 줘', '전체 삭제해 줘', '증상에서 흉통 삭제해 줘',
                     '혈압 말고 모두 삭제해 줘', '가족의 혈압 지워 줘', '환자정보 초기화해 줘'):
            with self.subTest(text=text):
                result = parse(text)
                self.assertEqual(result['action'], 'unknown')
                self.assertTrue(result.get('rejected'), result)

    def test_negated_commands_are_explicitly_rejected_before_auto_dictation(self):
        for text in ('혈압 90으로 수정하지 마', '맥박 100으로 바꾸지 말아 줘',
                     '체온 38도로 변경 안 해', '산소포화도 95를 지우지 마',
                     '맥박 90으로 안 바꿔 줘', '혈압 90으로 정정하지 않고 있어',
                     '맥박 95가 아니라고 했잖아 수정해줘', '맥박 95는 넣지 말고 확인해줘',
                     '혈압 90으로 수정 말아줘'):
            with self.subTest(text=text):
                result = parse(text, observation_fields=lambda _: ['pulse'])
                self.assertEqual(result['action'], 'unknown')
                self.assertTrue(result['rejected'])
                self.assertEqual(result['reason'], 'NEGATED_COMMAND')

    def test_conditional_or_permission_requests_do_not_apply_embedded_values(self):
        for text in ('혈압 90이면 수정해', '맥박 90으로 바꿔도 돼?',
                     '맥박을 90으로 고칠까?', '맥박이 90일 때 수정해 줘',
                     '병원에서 연락오면 전송해', '확인되면 보내 줘',
                     '맥박 95라고 말했을 때 수정해줘', '맥박 95로 바꾼다고 가정하고 읽어줘'):
            with self.subTest(text=text):
                result = parse(text, observation_fields=lambda _: ['pulse'])
                self.assertTrue(result['rejected'])
                self.assertEqual(result['reason'], 'CONDITIONAL_COMMAND')

    def test_quoted_and_past_commands_are_not_current_instructions(self):
        for text, reason in (
            ('"혈압 90으로 수정해 줘"라고 말했어', 'QUOTED_COMMAND'),
            ('지난번 맥박을 100으로 수정했어요', 'HISTORICAL_COMMAND'),
            ('병원에 보냈어', 'HISTORICAL_COMMAND'),
        ):
            with self.subTest(text=text):
                result = parse(text, observation_fields=lambda _: ['pulse'])
                self.assertEqual(result['reason'], reason)
                self.assertEqual(result['action'], 'unknown')

    def test_multiple_actions_are_rejected_but_one_multifield_edit_is_allowed(self):
        for text in ('확인, 전송해', '혈압 90으로 바꾸고 전송해',
                     '체온 삭제한 후 혈압 100으로 변경해',
                     '혈압 수정하고 맥박 추가해', '입력 내용을 확인해서 전송해'):
            with self.subTest(text=text):
                result = parse(text)
                self.assertTrue(result.get('rejected'), result)
        self.assertEqual(parse('혈압 100에 80, 맥박 92로 수정해')['action'], 'edit')

    def test_clinical_negation_is_not_a_rejected_control(self):
        for text in ('구토 없음', '호흡곤란 없음', '출혈 미확인', '의식 명료하지 않음',
                     '혈압은 모름', '흉통 의심', '맥박이 있는지 모르겠어'):
            with self.subTest(text=text):
                result = parse(text)
                self.assertNotIn('rejected', result)

    def test_observation_classification_uses_conservative_callback(self):
        text = '혈압이 지금 100에 80까지 떨어졌어'
        result = parse(text, observation_fields=lambda value: ['systolic_bp'] if value == text else [])
        self.assertEqual(result, dict(action='observe', field='blood_pressure', text=text))
        self.assertEqual(parse('지금 좀 어때')['action'], 'unknown')

    def test_missing_value_is_rejected_instead_of_mutating_anything(self):
        for text in ('수정해 줘', '저장해 줘', '기록해 줘', '정정'):
            with self.subTest(text=text):
                result = parse(text)
                self.assertEqual(result['action'], 'unknown')
                self.assertTrue(result.get('rejected'))

    def test_custom_database_pool_is_used_without_global_mutation(self):
        custom = commands.catalog()
        custom['actions']['review']['aliases'].append('내가 등록한 조회 문구')
        self.assertEqual(parse('내가 등록한 조회 문구', config=custom)['action'], 'review')
        self.assertEqual(parse('내가 등록한 조회 문구')['action'], 'unknown')
        custom['actions']['review']['aliases'].clear()
        self.assertTrue(commands.catalog()['actions']['review']['aliases'])

    def test_text_boundaries(self):
        for text in ('', ' ', None, 1, '가' * 4001):
            with self.subTest(text=str(text)[:10]):
                with self.assertRaises(ValueError):
                    parse(text)
        self.assertEqual(parse('가' * 4000)['action'], 'unknown')


if __name__ == '__main__':
    unittest.main()
