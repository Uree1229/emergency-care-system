"""Unified workflow unknown dispositions; synthetic in-memory records only."""
import copy
import unittest
from server import db, service, voice_input as voice

NOW = 2_000_000_000


class UnifiedUnknownDefaults(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(':memory:')
        db.init(self.conn)
        self.counter = 0

    def tearDown(self):
        self.conn.close()

    def key(self):
        self.counter += 1
        return 'followup-edge-' + str(self.counter)

    def draft(self, text='47세 남성. 흉통. 의식 명료. 호흡 정상. 혈압 120에 80. 맥박 92회.'):
        s = voice.create(self.conn, NOW, dict(actor_id='review', client_request_id=self.key()))['session']
        return voice.transcript(self.conn, NOW, s['id'], dict(
            actor_id='review', client_request_id=self.key(), expected_version=s['version'],
            utterance_id=self.key(), is_final=True, text=text))['session']

    def answer(self, session, text, question_id=None, **extra):
        body = dict(actor_id='review', client_request_id=self.key(), expected_version=session['version'],
                    utterance_id=self.key(), question_id=question_id or session['followup']['next_question']['id'],
                    text=text, **extra)
        return voice.answer_followup(self.conn, NOW + 1, session['id'], body)

    def test_explicit_remaining_unknown_is_one_atomic_disposition_preserving_known_observations(self):
        for text in ('남은 항목 전부 다 모름으로 해줘', '항목 전부 다 모름 처리',
                     '남은 항목 전부 미확인으로 남겨줘', '나머지 질문 모두 미측정으로 처리해주세요',
                     '그럼 나머지는 다 확인 불가로 기록해줘', '전부 미확인', '남은 항목은 모름.',
                     '나머지는 모르겠어요', '나머지는 모르겠습니다', '나머지는 몰라요'):
            with self.subTest(text=text):
                s = self.draft()
                before_meta = copy.deepcopy(s['field_metadata'])
                expected = [q['id'] for q in s['followup']['items'] if q['askable']]
                before_revisions = self.conn.execute('SELECT COUNT(*) FROM voice_revisions WHERE session_id=?',
                                                      (s['id'],)).fetchone()[0]
                result = self.answer(s, text)
                current = result['session']
                self.assertEqual(current['fields'], s['fields'])
                for key in s['fields']:
                    self.assertEqual(current['field_metadata'][key], before_meta[key], key)
                self.assertEqual(result['answer']['unavailable_question_ids'], expected)
                self.assertEqual(result['answer']['unavailable_scope'], 'remaining')
                self.assertTrue(result['answer']['accepted'])
                self.assertTrue(result['answer']['unavailable'])
                self.assertEqual(result['answer']['applied_fields'], [])
                self.assertTrue(result['followup']['collection_complete'])
                self.assertFalse(result['followup']['complete'])
                self.assertTrue(all(not q['askable'] and q['answer_status'] == 'unavailable'
                                    for q in result['followup']['items']))
                self.assertEqual(current['version'], s['version'] + 1)
                self.assertEqual(len(current['utterances']), len(s['utterances']) + 1)
                self.assertEqual(current['utterances'][-1]['transcript'], text)
                self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM voice_revisions WHERE session_id=?',
                                                  (s['id'],)).fetchone()[0], before_revisions + 1)
                self.assertEqual(current['patient_id'], s['patient_id'])
                self.assertEqual(current['state'], 'DRAFT')
                self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM patients').fetchone()[0], 0)
                self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM inquiries').fetchone()[0], 0)

    def test_remaining_unknown_keeps_partial_pressure_sibling_metadata_and_new_observation_reopens_group(self):
        s = self.draft('47세 남성. 의식 명료. 호흡 정상.')
        s = voice.edit(self.conn, NOW, s['id'], dict(
            actor_id='review', client_request_id=self.key(), expected_version=s['version'],
            changes={'systolic_bp': 120}, change_kind='correction'))['session']
        old = copy.deepcopy(s['field_metadata']['systolic_bp'])
        current = self.answer(s, '남은 항목 전부 미확인으로 남겨줘', 'blood_pressure')['session']
        self.assertEqual(current['fields']['systolic_bp'], 120)
        self.assertNotIn('diastolic_bp', current['fields'])
        self.assertEqual(current['field_metadata']['systolic_bp'], old)
        self.assertEqual(current['field_metadata']['diastolic_bp']['followup_unavailable_scope'], 'remaining')
        bp = next(q for q in current['followup']['items'] if q['id'] == 'blood_pressure')
        self.assertFalse(bp['askable'])
        current = voice.edit(self.conn, NOW + 2, s['id'], dict(
            actor_id='review', client_request_id=self.key(), expected_version=current['version'],
            changes={'systolic_bp': 125}, change_kind='correction'))['session']
        bp = next(q for q in current['followup']['items'] if q['id'] == 'blood_pressure')
        self.assertTrue(bp['askable'])
        self.assertNotIn('followup_unavailable_scope', current['field_metadata']['diastolic_bp'])

    def test_ordinary_unknown_and_single_disposition_only_answer_current_question(self):
        for text in ('모름', '모름으로 처리해줘', '미확인으로 남겨주세요'):
            with self.subTest(text=text):
                s = self.draft()
                result = self.answer(s, text, 'glucose')
                self.assertTrue(result['answer']['accepted'])
                self.assertEqual(result['answer']['unavailable_question_ids'], ['glucose'])
                self.assertNotIn('unavailable_scope', result['answer'])
                self.assertTrue(any(q['askable'] for q in result['followup']['items']))
                self.assertFalse(next(q for q in result['followup']['items'] if q['id'] == 'glucose')['askable'])
                self.assertEqual(result['session']['fields'], s['fields'])

    def test_remaining_unknown_includes_previously_unrecognized_question_without_filling_it(self):
        s = self.draft()
        failed = self.answer(s, '잘 알아듣지 못했어', 'glucose')
        self.assertFalse(failed['answer']['accepted'])
        current = failed['session']
        self.assertTrue(current['field_metadata']['glucose']['needs_clarification'])
        result = self.answer(current, '남은 항목 전부 미확인으로 남겨줘', 'spo2')
        self.assertIn('glucose', result['answer']['unavailable_question_ids'])
        self.assertNotIn('glucose', result['session']['fields'])
        self.assertTrue(result['followup']['collection_complete'])
        self.assertTrue(result['session']['field_metadata']['glucose']['needs_clarification'])

    def test_remaining_unknown_rejects_mixed_negative_quoted_reported_question_and_stt_gibberish(self):
        for text in ('남은 항목 전부 미확인으로 처리하지 마',
                     '남은 항목 전부 미확인으로 처리하면 안 돼',
                     '남은 항목 전부 미확인으로 처리했어',
                     '남은 항목 전부 미확인으로 처리해줘?',
                     '"남은 항목 전부 미확인으로 남겨줘"',
                     '환자가 남은 항목 전부 미확인으로 남겨줘라고 했어',
                     '남은 항목 전부 미확인 맥박 92회',
                     '남은 항목 전부 모름인데 맥박은 구십이야',
                     '남은 항목 전부 다 무릎으로 해줘',
                     '항목 전부 다 모름 처리 모윰으로 처리',
                     '전북 남원 항 목 타 모르 므로 해줘', '모름 처리하지 마'):
            with self.subTest(text=text):
                self.assertFalse(voice._remaining_unavailable_answer(text))
                s = self.draft('47세 남성.')
                result = self.answer(s, text, 'pulse')
                self.assertFalse(result['answer']['accepted'])
                self.assertFalse(result['answer']['unavailable'])
                self.assertEqual(result['answer']['unavailable_question_ids'], [])
                self.assertEqual(result['session']['fields'], s['fields'])
                self.assertTrue(all(q['askable'] for q in result['followup']['items']))
                self.assertEqual(result['session']['utterances'][-1]['transcript'], text)

    def test_remaining_unknown_idempotency_stale_actor_and_undo_keep_existing_boundaries(self):
        s = self.draft()
        body = dict(actor_id='review', client_request_id=self.key(), expected_version=s['version'],
                    utterance_id=self.key(), question_id='glucose', text='남은 항목 전부 미확인으로 남겨줘')
        result = voice.answer_followup(self.conn, NOW + 1, s['id'], body)
        replay = voice.answer_followup(self.conn, NOW + 1, s['id'], body)
        self.assertTrue(replay['duplicate'])
        self.assertEqual(replay['session'], result['session'])
        self.assertEqual(len(replay['session']['utterances']), len(s['utterances']) + 1)
        for patch, expected_error in (({'client_request_id': self.key()}, 'VOICE_VERSION_CONFLICT'),
                                      ({'client_request_id': self.key(), 'actor_id': 'other'}, 'FORBIDDEN'),
                                      ({'client_request_id': self.key(),
                                        'expected_version': result['session']['version']}, 'VOICE_UTTERANCE_FINALIZED')):
            with self.subTest(error=expected_error):
                with self.assertRaises(service.ServiceError) as raised:
                    voice.answer_followup(self.conn, NOW + 2, s['id'], {**body, **patch})
                self.assertEqual(raised.exception.code, expected_error)
        undone = voice.undo(self.conn, NOW + 3, s['id'], dict(actor_id='review',
            client_request_id=self.key(), expected_version=result['session']['version']))['session']
        self.assertEqual(undone['fields'], s['fields'])
        self.assertEqual(undone['field_metadata'], s['field_metadata'])
        self.assertEqual(undone['followup'], s['followup'])
        self.assertEqual(len(undone['utterances']), len(s['utterances']) + 1)

    def test_remaining_disposition_with_competing_recognition_is_rejected_without_mutation(self):
        session = self.draft()
        with self.assertRaises(service.ServiceError) as raised:
            self.answer(session, '남은 항목 전부 미확인으로 남겨줘',
                        recognition_alternatives=['맥박 90회'])
        self.assertEqual(raised.exception.code, 'VOICE_ANSWER_CONFLICT')
        current = voice.get(self.conn, NOW + 1, session['id'], 'review')['session']
        self.assertEqual(current, session)

    def test_remaining_disposition_allows_only_agreeing_scope_hypotheses(self):
        session = self.draft()
        result = self.answer(session, '남은 항목 전부 미확인으로 남겨줘',
                             recognition_alternatives=['나머지는 모름', '전부 미확인'])
        self.assertEqual(result['answer']['unavailable_scope'], 'remaining')
        self.assertEqual(result['session']['fields'], session['fields'])
        self.assertTrue(result['followup']['collection_complete'])

    def test_remaining_disposition_preserves_recognition_dispute_and_confirmation_gate(self):
        session = voice.create(self.conn, NOW, dict(actor_id='review', client_request_id=self.key()))['session']
        session = voice.transcript(self.conn, NOW, session['id'], dict(actor_id='review',
            client_request_id=self.key(), expected_version=session['version'],
            utterance_id=self.key(), is_final=True, text='47세 남성. 맥박19.',
            recognition_alternatives=['47세 남성. 맥박90.']))['session']
        self.assertTrue(session['field_metadata']['pulse']['recognition_context'])
        result = self.answer(session, '남은 항목 전부 미확인으로 남겨줘')
        current = result['session']
        self.assertNotIn('pulse', current['fields'])
        self.assertTrue(current['field_metadata']['pulse']['needs_clarification'])
        self.assertEqual(current['field_metadata']['pulse']['recognition_context'],
                         session['field_metadata']['pulse']['recognition_context'])
        with self.assertRaises(service.ServiceError) as raised:
            voice.confirm(self.conn, NOW + 2, current['id'], dict(actor_id='review',
                client_request_id=self.key(), expected_version=current['version'], confirmed=True))
        self.assertEqual(raised.exception.code, 'VOICE_RECOGNITION_REVIEW_REQUIRED')

    def test_bulk_unknown_preserves_existing_patient_and_requires_reconfirmation_of_new_draft_revision(self):
        patient = service.create_patient(self.conn, NOW, 'synthetic-followup-patient', dict(
            reporter_role='transporter', consent=True, age=47, symptoms='흉통',
            consciousness='alert', breathing='normal', contact='010-0000-0000',
            required_capabilities=[], resource_demands={'ed_bed': 1}, lat=35.16, lon=126.85))
        s = voice.create(self.conn, NOW, dict(actor_id='review', client_request_id=self.key(),
                                           patient_id=patient['id']))['session']
        s = voice.confirm(self.conn, NOW, s['id'], dict(actor_id='review', client_request_id=self.key(),
            expected_version=s['version'], confirmed=True))['session']
        self.assertTrue(s['confirmed'])
        before = dict(self.conn.execute('SELECT * FROM patients WHERE id=?', (patient['id'],)).fetchone())
        result = self.answer(s, '남은 항목 전부 미확인으로 남겨줘')
        self.assertEqual(result['session']['patient_id'], patient['id'])
        self.assertEqual(result['session']['fields'], s['fields'])
        self.assertFalse(result['session']['confirmed'])
        self.assertEqual(dict(self.conn.execute('SELECT * FROM patients WHERE id=?',
                                               (patient['id'],)).fetchone()), before)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM inquiries').fetchone()[0], 0)
