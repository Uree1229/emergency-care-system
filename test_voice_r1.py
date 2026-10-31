"""서버 음성 입력의 R1 맞춤(server/voice_r1.py): R1 칸 추출, 서버 추출기 문제 수정, 세션·제출 변환, 첫 문의 필수 칸."""
import unittest

from server import service, voice_input as voice, voice_r1, voice_terms
from test_server import Base, T0

NOW = T0 + 30


def r1(text, now=NOW):
    """세션 반영 단계와 같은 방식(voice_terms 결과 + R1 칸)으로 추출한다."""
    return voice._extract(text, now)


class R1ExtractTest(unittest.TestCase):
    def test_required_fields_from_one_report(self):
        out = r1('58세 남자 가슴 쥐어짜는 통증, 의식 명료, 호흡 정상, 프리케이타스 2')['fields']
        self.assertEqual({k: out[k] for k in ('avpu', 'consciousness', 'breathing', 'chief_complaint', 'age_category', 'pre_ktas')},
                         {'avpu': 'A', 'consciousness': 'alert', 'breathing': 'normal', 'chief_complaint': 'cardiac',
                          'age_category': 'adult', 'pre_ktas': 2})
        self.assertEqual(voice_r1.missing(out), [])

    def test_particles_arrest_and_laterality(self):
        out = r1('의식은 명료하나 오른쪽 얼굴이 처짐, 발음 어눌, 케이타스 2')['fields']
        self.assertEqual((out['avpu'], out['chief_complaint'], out['pre_ktas']), ('A', 'stroke', 2))
        out = r1('의식 없고 숨도 안 쉼, 심폐소생술 시행 중, 등급 1')['fields']
        self.assertEqual((out['avpu'], out['breathing'], out['chief_complaint'], out['pre_ktas']), ('U', 'absent', 'cardiac', 1))
        out = r1('할아버지가 갑자기 왼쪽 팔다리에 힘이 없고 말을 못 함')['fields']   # 가족어로 문장을 버리지 않는다
        self.assertEqual((out['chief_complaint'], out['age_category']), ('stroke', 'adult'))

    def test_pregnancy_bleeding_is_obstetric_and_weeks_are_read(self):
        result = r1('임신 28주 임산부 하혈, 의식 명료')
        self.assertEqual({k: result['fields'][k] for k in ('chief_complaint', 'age_category', 'pregnancy_weeks')},
                         {'chief_complaint': 'obstetric', 'age_category': 'pregnant', 'pregnancy_weeks': 28})
        self.assertTrue(any('산과' in w for w in result['warnings']))

    def test_conservative_cases_are_left_for_the_operator(self):
        self.assertNotIn('avpu', r1('의식 있음, 흉통')['fields'])                      # '의식 있음'만으로 명료라 하지 않는다
        suspected = r1('흉통, ST 상승 의심')
        self.assertEqual(suspected['fields']['stemi_suspected'], 'yes')                    # 칸 이름 자체가 'STEMI 의심'
        self.assertEqual(suspected['fields']['symptoms'], ['흉통'])                         # '의심'이 쉼표 너머 흉통을 꾸미지 않는다
        self.assertNotIn('stemi_suspected', r1('흉통, ST 상승 여부 모름')['fields'])       # 의심 외의 불확실은 넣지 않는다
        self.assertEqual(r1('뇌졸중 의심')['fields']['chief_complaint'], 'stroke')         # 주증상 묶음 이름 자체가 '의심'
        conflict = r1('의식 명료, 통증에 반응')
        self.assertNotIn('avpu', conflict['fields'])
        self.assertNotIn('consciousness', conflict['fields'])                             # 상충하면 의식 상태도 비운다
        self.assertTrue(any('서로 다른 값' in w or '서로 달라' in w for w in conflict['warnings']))
        self.assertNotIn('chief_complaint', r1('과거 뇌졸중 병력 있음')['fields'])       # 과거력 문장은 건너뛴다
        self.assertEqual(r1('혈압 110에 70으로 수정')['fields'].get('avpu'), None)          # 정정 문장은 받아쓰기로 넣지 않는다

    def test_round3_rules(self):
        self.assertEqual(r1('60대 정도로 보이는 여성, 반응 없음')['fields']['age_category'], 'adult')   # 대략 나이라도 성인은 확실
        self.assertEqual(r1('임신 30주, 복통, 질 출혈')['fields']['chief_complaint'], 'obstetric')
        self.assertEqual(r1('고열, 경련 의심')['fields']['chief_complaint'], 'altered_seizure')           # 약한 후보끼리면 구체 묶음
        self.assertEqual(r1('경련 2분 지속, 지금은 멈춤')['fields']['seizure_ongoing'], 'no')
        self.assertEqual(r1('넘어져서 손목 다침, 비응급')['fields']['pre_ktas'], 5)

    def test_avpu_letter_scores_and_unclassified(self):
        out = r1('의식 V, GCS 13, 통증 점수 칠점, 등급 미분류')['fields']
        self.assertEqual((out['avpu'], out['consciousness'], out['gcs'], out['pain_score'], out['pre_ktas']),
                         ('V', 'impaired', 13, 7, 'unclassified'))

    def test_profile_rules(self):
        base = dict(chief_complaint='cardiac', age_category='adult')
        self.assertEqual(voice_r1.profile_for(base), 'TR_GENERAL_ED')                      # 흉통만으로는 중재시술 아님
        self.assertEqual(voice_r1.profile_for(base | {'stemi_suspected': 'yes'}), 'TR_CARDIAC_INTERVENTION')
        self.assertEqual(voice_r1.profile_for(base | {'age_category': 'child'}), 'TR_PEDIATRIC')
        self.assertEqual(voice_r1.profile_for(dict(chief_complaint='trauma', injury_region='head')), 'TR_NEUROSURGICAL')


class ServerExtractorFixTest(unittest.TestCase):
    def test_numbers_and_abbreviations(self):
        self.assertEqual(voice_terms.extract('호흡수 이십', NOW)['fields'], {'respiratory_rate': 20})
        self.assertEqual(voice_terms.extract('산소포화도 아흔둘', NOW)['fields'], {'spo2': 92})
        self.assertEqual(voice_terms.extract('BP 90/50 HR 130', NOW)['fields'],
                         {'systolic_bp': 90, 'diastolic_bp': 50, 'pulse': 130})
        self.assertEqual(voice_terms.extract('할머니 흉통', NOW)['fields'], {'symptoms': ['흉통']})
        self.assertEqual(voice_terms.extract('할아버지 흉통', NOW)['fields'], {'symptoms': ['흉통']})
        self.assertEqual(voice_terms.extract('아버지는 흉통', NOW)['fields'], {})               # 가족 정보는 계속 거른다

    def test_qualifiers_apply_to_their_comma_segment_only(self):
        out = voice_terms.extract('72세 여자 우측 편마비, 발음 이상, 혈압 170에 100', NOW)['fields']
        self.assertEqual((out['age'], out['systolic_bp'], out['diastolic_bp']), (72, 170, 100))   # '발음 이상'은 대략값 아님
        out = voice_terms.extract('임신 가능성 있음, 하복부 통증, 혈압 100에 60', NOW)['fields']
        self.assertEqual((out['systolic_bp'], out['diastolic_bp']), (100, 60))                     # 불확실은 그 구간에만
        self.assertEqual(voice_terms.extract('체온 35도 이하, 저체온 의심, 의식 혼미', NOW)['fields'],
                         {'consciousness': 'impaired'})                                              # 대략값 체온만 비운다
        self.assertIsNone(voice_terms.extract('60세 이상 남성', NOW)['fields'].get('age'))         # 수에 붙은 '이상'은 대략값

    def test_qualifiers_reach_only_nearby_values_without_commas(self):
        out = voice_terms.extract('8세 여아 땅콩 먹다 기도 폐쇄 추정 산소포화도 93', NOW)['fields']
        self.assertEqual((out['age'], out['spo2']), (8, 93))                                      # 먼 '추정'은 나이를 지우지 않는다
        self.assertNotIn('systolic_bp', voice_terms.extract('혈압 150에 90 정도로 보임 맥박 88', NOW)['fields'])
        self.assertEqual(voice_terms.extract('흉통 마지막 정상 1시간 전', NOW)['fields']['onset_at'], NOW - 3600)
        self.assertEqual(voice_terms.extract('BP 160/100 PR 92 BT 37.8', NOW)['fields'],
                         {'systolic_bp': 160, 'diastolic_bp': 100, 'pulse': 92, 'temperature': 37.8})
        self.assertEqual(voice_terms.extract('12세 남학생 축구하다 넘어짐', NOW)['fields']['age'], 12)       # '넘어짐'은 대략값 아님
        self.assertNotIn('spo2', voice_terms.extract('산소포화도 90 넘음', NOW)['fields'])

    def test_history_and_questions_drop_only_their_segment(self):
        out = voice_terms.extract('천식 병력 있음, 호흡곤란, 산소포화도 90', NOW)['fields']
        self.assertEqual((out['symptoms'], out['spo2']), (['호흡곤란'], 90))
        self.assertEqual(voice_terms.extract('아버지는 심장병 있었고, 환자 본인은 흉통', NOW)['fields'], {'symptoms': ['흉통']})
        self.assertEqual(voice_terms.extract('어머니는 70세 흉통, 기침', NOW)['fields'], {})          # 가족 주어는 뒤 구간까지
        self.assertEqual(voice_terms.extract('혈압 120에 80, 흉통 있나요?', NOW)['fields'],
                         {'systolic_bp': 120, 'diastolic_bp': 80})

    def test_command_variants_and_missing(self):
        for phrase, action in (('아까 말한 거 되돌려', 'undo'), ('입력한 내용 다시 불러줘', 'review'),
                               ('병원에 보내 주세요', 'submit'), ('뭐 빠졌는지 알려줘', 'missing'),
                               ('빠진 항목 알려줘', 'missing'), ('입력 내용 읽어줘', 'review'), ('이제 보내 줘', 'submit')):
            self.assertEqual(voice_terms.parse_command(phrase)['action'], action, phrase)
        for phrase in ('전송하지 마', '확인, 전송해', '환자는 확인이 필요함'):
            self.assertEqual(voice_terms.parse_command(phrase)['action'], 'unknown', phrase)

    def test_r1_fields_are_validated_and_read_back(self):
        with self.assertRaises(service.ServiceError):
            voice_terms.normalize_fields({'pre_ktas': 6})
        with self.assertRaises(service.ServiceError):
            voice_terms.normalize_fields({'avpu': 'A', 'consciousness': 'impaired'})
        spoken = voice_terms.readback({'avpu': 'P', 'consciousness': 'impaired', 'chief_complaint': 'stroke', 'pre_ktas': 2})
        for part in ('P 통증 반응', '뇌졸중 의심', 'Pre-KTAS 등급 2단계'):
            self.assertIn(part, spoken)


class R1SessionTest(Base):
    def setUp(self):
        super().setUp()
        self.counter = 0

    def key(self):
        self.counter += 1
        return 'r1-' + str(self.counter)

    def common(self, session, **extra):
        return dict(actor_id='crew-1', client_request_id=self.key(), expected_version=session['version'], **extra)

    def start(self, text):
        s = voice.create(self.conn, NOW, {'actor_id': 'crew-1', 'client_request_id': self.key()})['session']
        body = self.common(s, utterance_id=self.key(), text=text, is_final=True)
        return voice.transcript(self.conn, NOW, s['id'], body)['session']

    def approve(self, s):
        return voice.confirm(self.conn, NOW, s['id'], self.common(s, confirmed=True))['session']

    def submit(self, s, **extra):
        return voice.submit(self.conn, NOW, s['id'], self.common(
            s, context={'consent': True, 'lat': 35.16, 'lon': 126.85}, **extra))

    def test_first_inquiry_requires_r1_fields(self):
        s = self.start('65세 남성. 흉통. 혈압 90에 60.')
        spoken = voice.command(self.conn, NOW, s['id'], self.common(s, text='빠진 항목 알려줘'))
        self.assertEqual(spoken['missing_fields'], ['avpu', 'breathing', 'pre_ktas'])
        s = self.approve(s)
        self.assertEqual(self.code(self.submit, s, start_inquiry=True), 'R1_FIELDS_REQUIRED')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM patients').fetchone()[0], 0)

    def test_submit_maps_r1_fields_to_patient(self):
        s = self.approve(self.start('65세 남성. 흉통 있고 ST 상승. 의식 명료. 호흡 정상. 혈압 90에 60. 통증 8점. 케이타스 2.'))
        p = self.submit(s, start_inquiry=True)['patient']
        self.assertEqual((p['priority'], p['priority_source'], p['consciousness']), (4, 'expert', 'alert'))
        extra = p['extra']
        self.assertEqual({k: extra[k] for k in ('avpu', 'chief_complaint', 'age_group', 'pre_ktas_level', 'stemi_suspected')},
                         {'avpu': 'A', 'chief_complaint': 'cardiac', 'age_group': 'adult', 'pre_ktas_level': 2,
                          'stemi_suspected': 'yes'})
        self.assertEqual(extra['vitals'], {'sbp': 90, 'dbp': 60, 'nrs': 8})
        self.assertEqual(extra['treatment_profile_id'], 'TR_CARDIAC_INTERVENTION')
        self.assertIn('심장내과', p['required_capabilities'])
        self.assertTrue(p['inquiry_workflow']['enabled'])

    def say(self, s, text):
        """무전기 방식: 버튼 하나로 보낸 최종 문장(mode=auto)."""
        return voice.transcript(self.conn, NOW, s['id'], self.common(s, utterance_id=self.key(), text=text, is_final=True,
                                                                      mode='auto'))

    def test_auto_mode_routes_one_button_to_dictation_or_command(self):
        s = self.start('흉통.')
        first = self.say(s, '혈압 120에 80 확인해')                           # 값을 말하면 읽기 명령이 아니라 받아쓰기
        self.assertEqual(first['effective_mode'], 'dictation')
        self.assertEqual(first['session']['fields']['systolic_bp'], 120)
        spoken = self.say(first['session'], '혈압 얼마야')
        self.assertEqual((spoken['effective_mode'], spoken['command']['action']), ('command', 'review'))
        self.assertIn('120', spoken['speak_text'])
        undo = self.say(first['session'], '방금 거 취소')
        self.assertEqual(undo['effective_mode'], 'command')
        self.assertNotIn('systolic_bp', undo['session']['fields'])
        missing = self.say(undo['session'], '빠진 거 알려줘')
        self.assertIn('pre_ktas', missing['missing_fields'])
        # 숫자가 있으면 받아쓰기로 본다(이 문장의 혈압은 추출기가 아직 못 읽는다: '혈압'과 숫자 사이에 다른 말)
        other = self.start('흉통.')
        self.assertEqual(self.say(other, '혈압 확인해 보니 120에 80')['effective_mode'], 'dictation')

    def test_auto_mode_does_not_confirm_by_voice(self):
        s = self.start('흉통. 의식 명료.')
        result = self.say(s, '확인')
        self.assertEqual(result['operational_effect'], 'confirmation_requires_tap')
        self.assertFalse(voice.get(self.conn, NOW, s['id'], 'crew-1')['session']['confirmed'])

    def test_avpu_edit_keeps_consciousness_consistent(self):
        s = self.start('의식 명료. 흉통.')
        self.assertEqual((s['fields']['avpu'], s['fields']['consciousness']), ('A', 'alert'))
        s = voice.edit(self.conn, NOW, s['id'], self.common(s, changes={'avpu': 'P'}, change_kind='correction'))['session']
        self.assertEqual((s['fields']['avpu'], s['fields']['consciousness']), ('P', 'impaired'))
        s = voice.edit(self.conn, NOW, s['id'], self.common(s, changes={'consciousness': 'alert'},
                                                            change_kind='correction'))['session']
        self.assertNotIn('avpu', s['fields'])                                              # 어긋난 AVPU는 다시 확인받는다


if __name__ == '__main__':
    unittest.main()
