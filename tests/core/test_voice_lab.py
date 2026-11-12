"""음성 초안 추출(voice_lab): 숫자 정규화·부정어·변경 제안·명령·되읽기·서버 입력 호환 검사."""
import copy
import unittest

from server import service
from voice_lab import extract as ex

try:   # 서버 음성 추출기(Codex 작업)가 없는 체크아웃에서는 비교 검사만 건너뛴다
    from server import voice_terms
    from voice_lab import compare_server
except ImportError:
    voice_terms = compare_server = None

NOW = 1793232000


class NormalizeTest(unittest.TestCase):
    def test_spoken_numbers(self):
        cases = {'혈압 구십에 육십': '혈압 90에 60', '체온 삼십팔점오': '체온 38.5', '혈압 일이공에 팔공': '혈압 120에 80',
                 '일곱 살': '7살', '서른다섯 살': '35살', '한 시간 반 전': '1시간 반 전', '등급 이': '등급 2',
                 '케이타스 이단계': '케이타스 2단계', '삼십분 전': '30분 전'}
        for spoken, expected in cases.items():
            self.assertEqual(ex.normalize(spoken), expected, spoken)

    def test_words_are_not_numbers(self):
        for text in ('혈압이 높음', '이대로 전송해', '이분이 쓰러지셨어요', '구토 있음', '의식 명료'):
            self.assertEqual(ex.normalize(text), text)


class ExtractTest(unittest.TestCase):
    def test_negation_scope(self):
        r = ex.propose_changes(None, '흉통 없음, 복통 있음', NOW)
        self.assertEqual({c['field']: c['after'] for c in r['changes']}, {'chief_complaint': 'abdominal'})
        r = ex.propose_changes(None, '흉통 있고 식은땀 없음', NOW)
        self.assertEqual([(c['field'], c['after']) for c in r['changes']], [('chief_complaint', 'cardiac')])

    def test_changes_are_relative_to_draft_and_draft_is_untouched(self):
        draft = ex.apply_changes(None, ex.propose_changes(None, '혈압 90에 60, 의식 명료', NOW)['changes'])
        before = copy.deepcopy(draft)
        r = ex.propose_changes(draft, '혈압 100에 70으로 수정, 의식 명료', NOW)
        self.assertEqual([(c['field'], c['before'], c['after']) for c in r['changes']],
                         [('vitals.sbp', 90, 100), ('vitals.dbp', 60, 70)])
        self.assertEqual(draft, before)
        cleared = ex.apply_changes(draft, ex.propose_changes(draft, '혈압 지워', NOW)['changes'])
        self.assertNotIn('sbp', cleared['vitals'])
        self.assertEqual(ex.propose_changes(draft, '의식 모름', NOW)['changes'][0]['after'], 'unknown')

    def test_commands_never_send(self):
        r = ex.propose_changes(None, '이대로 전송해', NOW)
        self.assertEqual((r['intent'], r['send_requested'], r['changes']), ('send', True, []))
        self.assertEqual(ex.propose_changes(None, '방금 거 취소', NOW)['intent'], 'undo')
        self.assertEqual(ex.propose_changes(None, '지금 내용 읽어줘', NOW)['intent'], 'readback')
        self.assertEqual(ex.propose_changes(None, '빠진 항목 알려줘', NOW)['intent'], 'missing')

    def test_out_of_range_values_are_rejected(self):
        r = ex.propose_changes(None, '산소포화도 120, 혈압 80에 120', NOW)
        self.assertEqual(r['changes'], [])
        self.assertEqual(sorted(x['field'] for x in r['rejected']), ['vitals.dbp', 'vitals.spo2'])

    def test_ambiguity_is_reported_not_hidden(self):
        r = ex.propose_changes(None, '흉통과 호흡곤란', NOW)
        ambiguity = [a for a in r['ambiguities'] if a['field'] == 'chief_complaint']
        self.assertEqual(ambiguity[0]['candidates'], ['cardiac', 'dyspnea'])
        r = ex.propose_changes(None, '의식 저하, 통증에 반응', NOW)
        self.assertEqual(r['changes'][-1]['after'], 'P')              # 구체적인 AVPU가 '의식 저하'보다 우선
        self.assertFalse([a for a in r['ambiguities'] if a['field'] == 'avpu'])


class ReadbackAndPayloadTest(unittest.TestCase):
    def setUp(self):
        utterance = '65세 남성, 흉통 30분 전부터, 의식 명료, 호흡 정상, 혈압 90에 60, ST 상승, 프리케이타스 2'
        self.draft = ex.apply_changes(None, ex.propose_changes(None, utterance, NOW)['changes'])

    def test_readback_and_missing(self):
        text = ex.readback_text(self.draft, NOW)
        for part in ('의식 명료', '흉통·심장', '성인 65세', 'Pre-KTAS 2단계', '혈압 90에 60', 'ST 상승 확인', '발생 30분 전',
                     '필수 항목 모두 입력됨'):
            self.assertIn(part, text)
        self.assertEqual(ex.missing_text(ex.apply_changes(None, [])),
                         '확인 필요: 의식, 호흡, 주증상, 연령대, Pre-KTAS 등급')

    def test_profile_rules(self):
        base = dict(self.draft)
        self.assertEqual(ex.profile_for(base)[0], 'TR_CARDIAC_INTERVENTION')
        self.assertEqual(ex.profile_for(base | {'stemi_suspected': None})[0], 'TR_GENERAL_ED')   # 흉통만으로는 중재시술 아님
        self.assertEqual(ex.profile_for(base | {'age_group': 'child'})[0], 'TR_PEDIATRIC')
        self.assertEqual(ex.profile_for(base | {'chief_complaint': 'trauma', 'injury_region': 'head'})[0],
                         'TR_NEUROSURGICAL')
        self.assertEqual(ex.profile_for(base | {'chief_complaint': 'obstetric', 'labor': 'yes', 'pregnancy_weeks': 33})[0],
                         'TR_HIGH_RISK_BIRTH_NEONATAL')

    def test_payload_passes_server_validation(self):
        cleaned = service._clean_patient(ex.to_server_payload(self.draft, consent=True, lat=37.5, lon=126.96))
        self.assertEqual((cleaned['priority'], cleaned['priority_source'], cleaned['consciousness']), (4, 'expert', 'alert'))
        unclassified = ex.to_server_payload(self.draft | {'pre_ktas': 'unclassified'}, consent=True)
        self.assertEqual(service._clean_patient(unclassified)['priority_source'], 'unknown')

    def test_payload_refuses_missing_required(self):
        with self.assertRaises(ValueError):
            ex.to_server_payload(self.draft | {'pre_ktas': None}, consent=True)


@unittest.skipIf(voice_terms is None, 'server.voice_terms 없음')
class CompareServerTest(unittest.TestCase):
    def test_symptom_groups_cover_server_catalog(self):
        canonical = {t['canonical'] for t in voice_terms.catalog()['terms']['symptoms']}
        self.assertEqual(canonical - set(compare_server.SYMPTOM_GROUP), set())   # 서버 증상이 늘면 변환표도 고친다

    def test_expectations_split_into_common_and_r1_only(self):
        common, r1_only = compare_server.split_expect(
            {'avpu': 'V', 'age_group': 'pregnant', 'pre_ktas': 2, 'vitals.sbp': 90, 'vitals.nrs': 7, 'intent': 'update'})
        self.assertEqual(common, {'consciousness': 'impaired', 'vitals.sbp': 90})
        self.assertEqual(r1_only, {'age_group': 'pregnant', 'pre_ktas': 2, 'vitals.nrs': 7})


if __name__ == '__main__':
    unittest.main()
