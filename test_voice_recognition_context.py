"""ASR candidate disagreement guards; synthetic text, no microphone/network/model."""
import copy
import json
import unittest

from server import db, service, voice_input as voice, voice_recognition_context as context
from test_server import Base, T0


class RecognitionContextUnit(unittest.TestCase):
    def test_limits_are_strict_and_no_coercion_or_silent_truncation(self):
        invalid = [None, '맥박90', {}, [None], [12], [''], [' '], ['x' * 4001], ['맥박90'] * 17,
                   ['x' * 4000] * 5]
        for value in invalid:
            with self.subTest(value=type(value).__name__):
                with self.assertRaises(service.ServiceError): context.validate('맥박90', value)
        self.assertEqual(context.validate('맥박90', ['맥박90', '맥박19', '맥박19']), ['맥박19'])

    def test_numeric_candidates_do_not_use_plausibility(self):
        parsed, report = context.analyze('맥박19', ['맥박90'], lambda text: voice._extract(text, T0))
        self.assertEqual(report['disputed_fields'], ['pulse'])
        self.assertEqual(report['candidates'], [{'field': 'pulse', 'values': [19, 90]}])
        self.assertNotIn('pulse', parsed['fields'])

    def test_same_spoken_and_digit_value_agree_without_rewriting_raw(self):
        parsed, report = context.analyze('맥박 구십', ['맥박90'], lambda text: voice._extract(text, T0))
        self.assertEqual(report['status'], 'agreement')
        self.assertEqual(parsed['fields']['pulse'], 90)
        self.assertEqual(parsed['evidence']['pulse'], ['맥박 구십'])
        self.assertEqual(report['coverage'], 'provided_hypotheses_only')

    def test_missing_top_or_missing_lower_is_uncertain_not_recovery(self):
        for top, other in [('맥박 확인 불가', '맥박90'), ('맥박90', '맥박 확인 불가')]:
            with self.subTest(top=top):
                parsed, report = context.analyze(top, [other], lambda text: voice._extract(text, T0))
                self.assertNotIn('pulse', parsed['fields'])
                self.assertIn('pulse', report['disputed_fields'])
                self.assertIn(None, report['candidates'][0]['values'])

    def test_pressure_pair_is_joint_even_when_only_one_value_differs(self):
        parsed, report = context.analyze('혈압120에80. 맥박90.', ['혈압110에80. 맥박90.'],
                                         lambda text: voice._extract(text, T0))
        self.assertEqual(report['disputed_fields'], ['diastolic_bp', 'systolic_bp'])
        self.assertEqual(parsed['fields'], {'pulse': 90})

    def test_negation_conflict_suppresses_derived_r1_without_vital_spill(self):
        parsed, report = context.analyze('호흡곤란 없음. 맥박90.', ['호흡곤란 있음. 맥박90.'],
                                         lambda text: voice._extract(text, T0))
        for key in ('symptoms', 'denied_symptoms', 'breathing', 'chief_complaint'):
            self.assertIn(key, report['disputed_fields'])
            self.assertNotIn(key, parsed['fields'])
        self.assertEqual(parsed['fields']['pulse'], 90)

    def test_single_hypothesis_is_not_claimed_verified(self):
        parsed, report = context.analyze('맥박90', [], lambda text: voice._extract(text, T0))
        self.assertEqual(report['status'], 'single_hypothesis')
        self.assertEqual(report['alternatives_count'], 0)
        self.assertTrue(report['limitations'])

    def test_symptom_order_is_not_a_polarity_conflict(self):
        def parse(text):
            return dict(fields={'symptoms': text.split()}, evidence={}, warnings=[])
        parsed, report = context.analyze('흉통 구토', ['구토 흉통'], parse)
        self.assertEqual(report['disputed_fields'], [])
        self.assertEqual(parsed['fields']['symptoms'], ['흉통', '구토'])


class RecognitionContextWorkflow(Base):
    def setUp(self):
        super().setUp(); self.now = T0 + 30; self.sequence = 0

    def key(self):
        self.sequence += 1; return 'context-' + str(self.sequence)

    def create(self):
        return voice.create(self.conn, self.now, dict(actor_id='context-crew', client_request_id=self.key()))['session']

    def body(self, s, **extra):
        return dict(actor_id='context-crew', client_request_id=self.key(), expected_version=s['version'], **extra)

    def dictate(self, s, text, alternatives=None, **extra):
        body = self.body(s, utterance_id=self.key(), text=text, is_final=True, **extra)
        if alternatives is not None: body['recognition_alternatives'] = alternatives
        return voice.transcript(self.conn, self.now, s['id'], body)

    def answer(self, s, qid, text, alternatives=None, **extra):
        body = self.body(s, question_id=qid, utterance_id=self.key(), text=text, **extra)
        if alternatives is not None: body['recognition_alternatives'] = alternatives
        return voice.answer_followup(self.conn, self.now, s['id'], body)

    def test_legacy_payload_is_unchanged_and_capability_is_explicit(self):
        result = self.dictate(self.create(), '맥박90')
        self.assertNotIn('recognition_context', result)
        self.assertEqual(result['session']['fields']['pulse'], 90)
        self.assertEqual(voice.capabilities()['recognition_context_revision'], context.REVISION)

    def test_conflict_keeps_previous_as_previous_and_saves_undisputed_fields(self):
        s = self.dictate(self.create(), '맥박80')['session']
        s = voice.confirm(self.conn, self.now, s['id'], self.body(s, confirmed=True))['session']
        result = self.dictate(s, '맥박90. 혈압120에80.', ['맥박19. 혈압120에80.'])
        s = result['session']; self.assertEqual(s['fields']['pulse'], 80)
        self.assertTrue(s['field_metadata']['pulse']['needs_clarification'])
        self.assertEqual(s['field_metadata']['pulse']['evidence'], ['맥박80'])
        self.assertEqual(s['fields']['systolic_bp'], 120)
        self.assertFalse(s['confirmed'])
        self.assertEqual(s['utterances'][-1]['transcript'], '맥박90. 혈압120에80.')
        self.assertIn('이전 관찰값', s['speak_text'])
        self.assertEqual(self.code(voice.confirm, self.conn, self.now, s['id'], self.body(s, confirmed=True)),
                         'VOICE_RECOGNITION_REVIEW_REQUIRED')
        self.assertEqual(self.code(voice.submit, self.conn, self.now, s['id'], self.body(s, context={})),
                         'VOICE_RECOGNITION_REVIEW_REQUIRED')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM patients').fetchone()[0], 0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM inquiries').fetchone()[0], 0)

    def test_new_conflicted_field_stays_missing_and_question_is_targeted(self):
        result = self.dictate(self.create(), '혈압120에80. 맥박19.', ['혈압120에80. 맥박90.'])
        s = result['session']; self.assertNotIn('pulse', s['fields'])
        question = next(q for q in s['followup']['items'] if q['id'] == 'pulse')
        self.assertEqual(question['reason'], 'needs_clarification')
        self.assertEqual(question['clarification_fields'], ['pulse'])
        self.assertNotIn('blood_pressure', [q['id'] for q in s['followup']['items']])

    def test_context_on_partials_is_rejected_without_any_write(self):
        s = self.create(); before = self.conn.total_changes
        body = self.body(s, utterance_id='partial', text='맥박90', is_final=False, recognition_alternatives=['맥박19'])
        self.assertEqual(self.code(voice.transcript, self.conn, self.now, s['id'], body), 'INVALID_RECOGNITION_CONTEXT')
        self.assertEqual(self.conn.total_changes, before)
        self.assertEqual(voice.get(self.conn, self.now, s['id'], 'context-crew')['session']['version'], s['version'])

    def test_exact_retry_retains_original_context_report_and_does_not_duplicate(self):
        s = self.create(); body = self.body(s, utterance_id='retry-u', text='맥박90', is_final=True,
                                          recognition_alternatives=['맥박19'])
        first = voice.transcript(self.conn, self.now, s['id'], body)
        again = voice.transcript(self.conn, self.now + 1, s['id'], copy.deepcopy(body))
        self.assertTrue(again['duplicate'])
        self.assertEqual(first['recognition_context'], again['recognition_context'])
        self.assertEqual(len(again['session']['utterances']), 1)
        altered = copy.deepcopy(body); altered['recognition_alternatives'] = ['맥박91']
        self.assertEqual(self.code(voice.transcript, self.conn, self.now, s['id'], altered), 'VOICE_REQUEST_CONFLICT')

    def test_actor_and_version_ownership_are_preserved(self):
        s = self.create(); body = self.body(s, utterance_id='other', text='맥박90', is_final=True,
                                          recognition_alternatives=['맥박19'])
        body['actor_id'] = 'someone-else'
        self.assertEqual(self.code(voice.transcript, self.conn, self.now, s['id'], body), 'FORBIDDEN')
        body['actor_id'] = 'context-crew'; body['expected_version'] += 1
        self.assertEqual(self.code(voice.transcript, self.conn, self.now, s['id'], body), 'VOICE_VERSION_CONFLICT')

    def test_undo_restores_pre_conflict_metadata_and_original_value(self):
        s = self.dictate(self.create(), '맥박80')['session']
        s = self.dictate(s, '맥박90', ['맥박19'])['session']
        s = voice.undo(self.conn, self.now, s['id'], self.body(s))['session']
        self.assertEqual(s['fields']['pulse'], 80)
        self.assertNotIn('recognition_context', s['field_metadata']['pulse'])
        self.assertFalse(s['field_metadata']['pulse'].get('needs_clarification', False))

    def test_unambiguous_fresh_answer_clears_only_question_conflict(self):
        s = self.dictate(self.create(), '맥박90. 혈압120에80.', ['맥박19. 혈압100에80.'])['session']
        s = self.answer(s, 'pulse', '맥박90')['session']
        self.assertEqual(s['fields']['pulse'], 90)
        self.assertNotIn('recognition_context', s['field_metadata']['pulse'])
        self.assertTrue(s['field_metadata']['systolic_bp']['needs_clarification'])
        self.assertNotIn('pulse', [q['id'] for q in s['followup']['items']])

    def test_disputed_followup_never_picks_lower_number(self):
        s = self.create(); result = self.answer(s, 'pulse', '19', ['90'])
        self.assertFalse(result['answer']['accepted'])
        self.assertFalse(result['answer']['unavailable'])
        self.assertEqual(result['answer']['applied_fields'], [])
        self.assertNotIn('pulse', result['session']['fields'])
        self.assertEqual(result['session']['utterances'][-1]['transcript'], '19')
        self.assertEqual(result['recognition_context']['candidates'], [{'field': 'pulse', 'values': [19, 90]}])

    def test_disputed_age_followup_marks_all_question_peers_without_missing_metadata(self):
        result = self.answer(self.create(), 'age', '17세', ['70세'])
        self.assertFalse(result['answer']['accepted'])
        self.assertEqual(result['recognition_context']['disputed_fields'], ['age', 'age_group'])
        for key in ('age', 'age_group'):
            self.assertTrue(result['session']['field_metadata'][key]['needs_clarification'])
        s = self.answer(result['session'], 'age', '17세')['session']
        self.assertFalse(any(item.get('needs_clarification') and item.get('recognition_context')
                             for item in s['field_metadata'].values()))

    def test_korean_and_digit_followup_agreement_uses_question_label_not_value(self):
        result = self.answer(self.create(), 'pulse', '구십', ['90'])
        self.assertTrue(result['answer']['accepted'])
        self.assertTrue(result['answer']['contextual_answer'])
        self.assertEqual(result['session']['fields']['pulse'], 90)
        self.assertEqual(result['session']['field_metadata']['pulse']['evidence'], ['구십'])

    def test_unavailable_vs_number_does_not_hide_question(self):
        for text, others in [('미측정', ['90']), ('90', ['미측정'])]:
            with self.subTest(text=text):
                result = self.answer(self.create(), 'pulse', text, others)
                self.assertFalse(result['answer']['accepted'])
                self.assertFalse(result['answer']['unavailable'])
                question = next(q for q in result['session']['followup']['items'] if q['id'] == 'pulse')
                self.assertTrue(question['askable'])
                self.assertEqual(question['reason'], 'needs_clarification')

    def test_unavailable_alone_cannot_clear_prior_asr_disagreement(self):
        s = self.dictate(self.create(), '맥박90', ['맥박19'])['session']
        s = self.answer(s, 'pulse', '미측정')['session']
        self.assertTrue(s['field_metadata']['pulse']['needs_clarification'])
        self.assertEqual(self.code(voice.confirm, self.conn, self.now, s['id'], self.body(s, confirmed=True)),
                         'VOICE_EMPTY_DRAFT')
        self.assertEqual(self.code(voice.submit, self.conn, self.now, s['id'], self.body(s, context={})),
                         'VOICE_RECOGNITION_REVIEW_REQUIRED')

    def test_command_context_never_authorizes_action_or_undo(self):
        s = self.dictate(self.create(), '맥박80')['session']
        result = self.dictate(s, '방금 입력 취소', ['맥박90'], mode='auto')
        self.assertEqual(result['recognition_context']['status'], 'command_blocked')
        self.assertEqual(result['session']['version'], s['version'])
        self.assertEqual(result['session']['fields']['pulse'], 80)
        self.assertEqual(result['operational_effect'], 'voice_command_needs_clarification')

    def test_natural_observation_keeps_transition_with_disputed_new_value(self):
        s = self.dictate(self.create(), '혈압120에80')['session']
        result = self.dictate(s, '혈압100에70으로 떨어졌어', ['혈압110에70으로 떨어졌어'], mode='auto')
        self.assertEqual(result['command']['action'], 'observe')
        self.assertEqual(result['recognition_context']['status'], 'needs_clarification')
        self.assertEqual(result['session']['fields']['systolic_bp'], 120)
        self.assertTrue(result['session']['field_metadata']['diastolic_bp']['needs_clarification'])

    def test_explicit_field_edit_can_resolve_conflict_and_confirm(self):
        s = self.dictate(self.create(), '맥박19', ['맥박90'])['session']
        s = voice.edit(self.conn, self.now, s['id'], self.body(s, changes={'pulse': 19}, change_kind='correction'))['session']
        self.assertNotIn('recognition_context', s['field_metadata']['pulse'])
        self.assertTrue(voice.confirm(self.conn, self.now, s['id'], self.body(s, confirmed=True))['session']['confirmed'])

    def test_clean_age_and_consciousness_answers_retire_disputed_derived_peers(self):
        for text, other, qid, answer, peer in [
            ('나이17세', '나이70세', 'age', '17세', 'age_category'),
            ('의식 명료', '의식 반응 없음', 'consciousness', '의식 명료', 'avpu')]:
            with self.subTest(qid=qid):
                s = self.dictate(self.create(), text, [other])['session']
                self.assertTrue(s['field_metadata'][peer]['needs_clarification'])
                result = self.answer(s, qid, answer); s = result['session']
                self.assertNotIn(qid, [q['id'] for q in s['followup']['items']])
                self.assertFalse(s['field_metadata'].get(peer, {}).get('needs_clarification', False))
                if peer not in s['fields']:
                    self.assertTrue(s['field_metadata'][peer]['removed'])
                    self.assertEqual(s['field_metadata'][peer]['source'], 'recognition_peer_reset')

    def test_clean_symptom_denial_clears_opposite_branch_and_retires_compromised_chief(self):
        s = self.dictate(self.create(), '호흡곤란 없음. 맥박90.', ['호흡곤란 있음. 맥박90.'])['session']
        s = self.answer(s, 'symptoms', '호흡곤란 없음')['session']
        self.assertNotIn('symptoms', [q['id'] for q in s['followup']['items']])
        self.assertFalse(any(s['field_metadata'].get(key, {}).get('needs_clarification')
                             for key in ['symptoms', 'denied_symptoms', 'chief_complaint']))
        self.assertNotIn('chief_complaint', s['fields'])
        # Actual parser-generated respiratory conflict remains its own question.
        self.assertTrue(s['field_metadata']['breathing']['needs_clarification'])
        s = self.answer(s, 'breathing', '호흡 정상')['session']
        self.assertFalse(any(item.get('needs_clarification') and item.get('recognition_context')
                             for item in s['field_metadata'].values()))

    def test_partial_symptom_answer_preserves_other_polarity_dispute(self):
        s = self.dictate(self.create(), '흉통 있음. 구토 없음. 맥박90.',
                        ['흉통 없음. 구토 있음. 맥박90.'])['session']
        s = self.answer(s, 'symptoms', '흉통 없음')['session']
        self.assertIn('구토', s['fields']['uncertain_symptoms'])
        question = next(q for q in s['followup']['items'] if q['id'] == 'symptoms')
        self.assertEqual(question['uncertain_items'], ['구토'])
        self.assertTrue(s['field_metadata']['uncertain_symptoms']['needs_clarification'])
        self.assertEqual(self.code(voice.confirm, self.conn, self.now, s['id'], self.body(s, confirmed=True)),
                         'VOICE_RECOGNITION_REVIEW_REQUIRED')
        s = self.answer(s, 'symptoms', '구토 없음')['session']
        self.assertNotIn('symptoms', [q['id'] for q in s['followup']['items']])
        self.assertFalse(any(item.get('needs_clarification') and item.get('recognition_context')
                             for key, item in s['field_metadata'].items() if key in voice.LIST_FIELDS))
        self.assertEqual(set(s['fields']['denied_symptoms']), {'흉통', '구토'})

    def test_malformed_context_rejects_before_receipt_or_utterance(self):
        for alternatives in [None, {'text': '90'}, [''], ['x'] * 17]:
            s = self.create(); before = self.conn.total_changes
            body = self.body(s, utterance_id=self.key(), text='맥박90', is_final=True,
                             recognition_alternatives=alternatives)
            self.assertEqual(self.code(voice.transcript, self.conn, self.now, s['id'], body),
                             'INVALID_RECOGNITION_CONTEXT')
            self.assertEqual(self.conn.total_changes, before)


if __name__ == '__main__': unittest.main()
