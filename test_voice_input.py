"""Voice draft workflow: observation/correction, confirmation and patient boundary."""
import copy
import json
import threading
from unittest.mock import patch

from server import db, service, voice_input as voice
from test_server import Base, T0

# R1: 첫 문의를 시작하려면 필수 5칸(AVPU·호흡·주증상·연령 구분·Pre-KTAS)이 있어야 한다.
R1_TEXT = '65세 남성. 흉통. 의식 명료. 호흡 정상. 혈압 90에 60. 케이타스 2.'


class VoiceWorkflow(Base):
    def setUp(self):
        super().setUp()
        self.now = T0 + 30
        self.counter = 0

    def key(self):
        self.counter += 1
        return 'req-' + str(self.counter)

    def create(self, pid=None):
        body = {'actor_id': 'crew-1', 'client_request_id': self.key()}
        if pid:
            body['patient_id'] = pid
        return voice.create(self.conn, self.now, body)['session']

    def common(self, session, **extra):
        return dict(actor_id='crew-1', client_request_id=self.key(),
                    expected_version=session['version'], **extra)

    def dictate(self, session, text='65세 남성. 흉통. 혈압 90에 60. 산소포화도 98퍼센트.', **extra):
        body = self.common(session, utterance_id=self.key(), text=text, is_final=True, **extra)
        return voice.transcript(self.conn, self.now, session['id'], body)['session']

    def edit(self, session, changes, **extra):
        return voice.edit(self.conn, self.now, session['id'],
                          self.common(session, changes=changes, change_kind='correction', **extra))['session']

    def approve(self, session):
        return voice.confirm(self.conn, self.now, session['id'],
                             self.common(session, confirmed=True))['session']

    def test_voice_to_correct_read_confirm_and_new_patient_without_dispatch(self):
        s = self.dictate(self.create())
        self.assertEqual(s['fields']['systolic_bp'], 90)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM patients').fetchone()[0], 0)
        body = self.common(s, text='혈압 110에 70으로 수정해줘')
        s = voice.command(self.conn, self.now, s['id'], body)['session']
        self.assertEqual((s['fields']['systolic_bp'], s['fields']['diastolic_bp']), (110, 70))
        self.assertEqual(s['field_metadata']['systolic_bp']['change_kind'], 'correction')
        spoken = voice.readback(self.conn, self.now, s['id'], 'crew-1', 'systolic_bp')
        self.assertIn('110', spoken['speak_text'])
        s = self.approve(s)
        body = self.common(s, context={'consent': True, 'lat': 35.16, 'lon': 126.85,
                                      'required_capabilities': ['소아청소년과']})
        result = voice.submit(self.conn, self.now, s['id'], body)
        p = result['patient']
        self.assertEqual((p['reporter_role'], p['age'], p['state']), ('transporter', 65, 'WAITING'))
        self.assertEqual(p['extra']['voice_observations']['fields']['systolic_bp'], 110)
        self.assertEqual(p['priority_source'], 'unknown')
        self.assertEqual(p['required_capabilities'], ['소아청소년과'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM inquiries').fetchone()[0], 0)
        self.assertTrue(voice.submit(self.conn, self.now, s['id'], body)['duplicate'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM patients').fetchone()[0], 1)
        self.assertNotIn('흉통', self.conn.execute(
            "SELECT data FROM events WHERE type='voice_information_applied'").fetchone()[0])

    def test_partial_neither_changes_draft_nor_executes_commands(self):
        s = self.dictate(self.create())
        s = self.approve(s)
        body = self.common(s, utterance_id='partial', text='혈압 150에 100', is_final=False)
        result = voice.transcript(self.conn, self.now, s['id'], body)
        self.assertEqual(result['preview']['fields']['systolic_bp'], 150)
        self.assertEqual(result['session']['fields'], s['fields'])
        self.assertEqual(result['session']['version'], s['version'])
        self.assertTrue(result['session']['confirmed'])
        self.assertEqual(len(result['session']['utterances']), 1)
        body = self.common(s, utterance_id='partial-command', text='취소', is_final=False, mode='command')
        self.assertEqual(voice.transcript(self.conn, self.now, s['id'], body)['session']['version'], s['version'])

    def test_edit_invalidates_confirmation_and_stale_confirm_fails(self):
        s = self.approve(self.dictate(self.create()))
        older = copy.deepcopy(s)
        s = self.edit(s, {'pulse': 100})
        self.assertFalse(s['confirmed'])
        self.assertEqual(self.code(voice.confirm, self.conn, self.now, s['id'],
                         self.common(older, confirmed=True)), 'VOICE_VERSION_CONFLICT')
        self.assertEqual(self.code(voice.submit, self.conn, self.now, s['id'], self.common(s)),
                         'VOICE_CONFIRMATION_REQUIRED')

    def test_stored_record_and_voice_observations_remain_separate_from_unknown(self):
        self.patient(symptoms='뒷머리 쪽 심한 통증과 어지러움, 보호자 설명', age=62)
        s = self.create('A')
        self.assertIn('보호자', s['baseline_summary']['symptoms'])
        self.assertIn('보호자', s['speak_text'])
        self.assertNotIn('denied_symptoms', s['fields'])
        self.assertNotIn('spo2', s['fields'])
        self.assertEqual(s['fields']['age'], 62)

    def test_idempotency_keys_are_scoped_to_operation_payload_and_actor(self):
        body = dict(actor_id='crew-1', client_request_id='same')
        first = voice.create(self.conn, self.now, body)
        second = voice.create(self.conn, self.now, body)
        self.assertEqual(first['session']['id'], second['session']['id'])
        self.assertTrue(second['duplicate'])
        self.assertEqual(self.code(voice.create, self.conn, self.now, dict(body, patient_id='A')),
                         'VOICE_REQUEST_CONFLICT')
        self.assertEqual(self.code(voice.get, self.conn, self.now, first['session']['id'], 'crew-2'), 'FORBIDDEN')
        self.assertEqual(self.code(voice.history, self.conn, self.now, first['session']['id'], 'crew-2'), 'FORBIDDEN')

    def test_duplicate_utterance_cannot_apply_twice_with_a_different_key(self):
        s = self.create()
        body = self.common(s, utterance_id='u1', text='맥박 100', is_final=True)
        result = voice.transcript(self.conn, self.now, s['id'], body)
        self.assertTrue(voice.transcript(self.conn, self.now, s['id'], body)['duplicate'])
        body = self.common(result['session'], utterance_id='u1', text='맥박 110', is_final=True)
        self.assertEqual(self.code(voice.transcript, self.conn, self.now, s['id'], body), 'VOICE_UTTERANCE_FINALIZED')

    def test_undo_chain_preserves_history_without_toggling(self):
        s = self.dictate(self.create(), '나이 60세')
        s = self.edit(s, {'age': 61})
        s = self.edit(s, {'age': 62})
        s = self.approve(s)
        s = voice.undo(self.conn, self.now, s['id'], self.common(s))['session']
        self.assertEqual(s['fields']['age'], 61)
        self.assertFalse(s['confirmed'])
        s = voice.undo(self.conn, self.now, s['id'], self.common(s))['session']
        self.assertEqual(s['fields']['age'], 60)
        history = voice.history(self.conn, self.now, s['id'], 'crew-1')['revisions']
        self.assertEqual(len(history), 5)
        self.assertEqual(history[-2]['kind'], 'undo')

    def test_observation_and_correction_preserve_both_old_and_new_values(self):
        s = self.dictate(self.create(), '혈압 110에 70')
        s = voice.edit(self.conn, self.now, s['id'], self.common(s,
            changes={'systolic_bp': 90, 'diastolic_bp': 60}, change_kind='observation'))['session']
        s = self.edit(s, {'systolic_bp': 95, 'diastolic_bp': 65})
        history = voice.history(self.conn, self.now, s['id'], 'crew-1')['revisions']
        self.assertEqual([r['kind'] for r in history], ['dictation', 'observation', 'correction'])
        self.assertEqual(history[1]['before_fields']['systolic_bp'], 110)
        self.assertEqual(history[2]['after_fields']['systolic_bp'], 95)

    def test_invalid_merged_bp_or_schema_cannot_partially_change_draft(self):
        s = self.dictate(self.create(), '혈압 110에 70')
        before = voice.get(self.conn, self.now, s['id'], 'crew-1')
        for values in ({'systolic_bp': 60}, {'priority': 1}, {'age': True}, {'spo2': float('nan')}):
            with self.subTest(values=values):
                self.assertTrue(self.code(voice.edit, self.conn, self.now, s['id'],
                    self.common(s, changes=values, change_kind='correction')).startswith('INVALID'))
                self.assertEqual(voice.get(self.conn, self.now, s['id'], 'crew-1'), before)

    def test_post_inquiry_apply_closes_old_acceptance_not_destination(self):
        self.patient()
        iid = self.accepted('A', 'H1')
        s = self.edit(self.create('A'), {'consciousness': 'impaired', 'spo2': 95})
        s = self.approve(s)
        result = voice.submit(self.conn, self.now, s['id'], self.common(s))
        p = result['patient']
        self.assertEqual((p['version'], p['state'], p['destination_hospital_id']), (2, 'WAITING', None))
        self.assertEqual(service._inquiry(self.conn, iid)['state'], 'CANCELLED')
        self.assertEqual(self.code(service.confirm_destination, self.conn, self.now, 'A', iid), 'NOT_ACCEPTED')
        self.assertTrue(self.conn.execute("SELECT 1 FROM events WHERE type='patient_information_amended' AND hospital_id='H1'").fetchone())

    def test_deleted_core_fields_are_really_cleared_on_apply(self):
        self.patient(age=62, consciousness='alert', breathing='normal', onset_at=T0)
        s = self.create('A')
        s = self.edit(s, {'spo2': 98}, unset_fields=['age', 'consciousness', 'breathing', 'onset_at'])
        s = self.approve(s)
        p = voice.submit(self.conn, self.now, s['id'], self.common(s))['patient']
        self.assertTrue(all(p[key] is None for key in ('age', 'consciousness', 'breathing', 'onset_at')))
        self.assertNotIn('age', p['extra']['voice_observations']['fields'])

    def test_patient_change_and_confirmed_destination_block_old_draft(self):
        self.patient()
        s = self.approve(self.edit(self.create('A'), {'spo2': 98}))
        service.update_patient(self.conn, self.now, 'A', {'age': 40})
        self.assertEqual(self.code(voice.submit, self.conn, self.now, s['id'], self.common(s)),
                         'VOICE_PATIENT_VERSION_CONFLICT')
        s = self.approve(self.edit(self.create('A'), {'spo2': 97}))
        iid = self.accepted('A', 'H1')
        service.confirm_destination(self.conn, self.now, 'A', iid)
        # Confirmation itself can change patient.version, either guard is a valid rejection.
        code = self.code(voice.submit, self.conn, self.now, s['id'], self.common(s))
        self.assertIn(code, ('VOICE_PATIENT_VERSION_CONFLICT', 'INVALID_STATE'))
        self.assertEqual(service._patient(self.conn, 'A')['state'], 'CONFIRMED')

    def test_failure_after_patient_create_rolls_back_patient_session_and_events(self):
        s = self.approve(self.dictate(self.create(), text=R1_TEXT))
        events_before = self.conn.execute('SELECT COUNT(*) FROM events').fetchone()[0]
        body = self.common(s, context={'consent': True, 'lat': 35.16, 'lon': 126.85}, start_inquiry=True)
        with patch.object(voice.inquiry_workflow, 'start', side_effect=service.ServiceError('TEST_FAILURE', 'test', 409)):
            self.assertEqual(self.code(voice.submit, self.conn, self.now, s['id'], body), 'TEST_FAILURE')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM patients').fetchone()[0], 0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM events').fetchone()[0], events_before)
        self.assertEqual(voice.get(self.conn, self.now, s['id'], 'crew-1')['session']['state'], 'DRAFT')
        self.assertFalse(voice.submit(self.conn, self.now, s['id'], body)['duplicate'])

    def test_new_patient_explicitly_starts_existing_inquiry_workflow(self):
        s = self.approve(self.dictate(self.create(), text=R1_TEXT))
        result = voice.submit(self.conn, self.now, s['id'], self.common(s,
            context={'consent': True, 'lat': 35.16, 'lon': 126.85}, start_inquiry=True, inquiry_policy='fixed_1'))
        self.assertTrue(result['patient']['inquiry_workflow']['enabled'])
        self.assertEqual(result['patient']['inquiry_workflow']['policy_name'], 'fixed_1')

    def test_audio_precheck_and_no_write_lock_during_provider_call(self):
        s = self.create()
        body = self.common(s, utterance_id='wav-1', audio_base64='mock-base64')
        def recognize(*args):
            other = db.connect(self.path)
            try:
                with db.transaction(other):
                    other.execute("INSERT INTO events(at,actor,type,data) VALUES(?,'test','probe','{}')", (self.now,))
            finally:
                other.close()
            return {'text': '맥박 100', 'provider': 'mock', 'is_final': True}
        with patch.object(voice.voice_audio, 'transcribe', side_effect=recognize) as mocked:
            result = voice.audio(self.conn, self.now, s['id'], body)
            self.assertEqual(result['session']['fields']['pulse'], 100)
            self.assertTrue(voice.audio(self.conn, self.now, s['id'], body)['duplicate'])
            self.assertEqual(mocked.call_count, 1)
        self.assertNotIn('mock-base64', '\n'.join(r[0] for r in self.conn.execute('SELECT response FROM voice_requests')))

    def test_inflight_audio_cannot_overwrite_a_newer_edit(self):
        s = self.create()
        body = self.common(s, utterance_id='wav-race', audio_base64='mock')
        def recognize(*args):
            other = db.connect(self.path)
            try:
                voice.edit(other, self.now, s['id'], self.common(s, changes={'pulse': 110}, change_kind='correction'))
            finally:
                other.close()
            return {'text': '맥박 100', 'provider': 'mock'}
        with patch.object(voice.voice_audio, 'transcribe', side_effect=recognize):
            self.assertEqual(self.code(voice.audio, self.conn, self.now, s['id'], body), 'VOICE_VERSION_CONFLICT')
        saved = voice.get(self.conn, self.now, s['id'], 'crew-1')['session']
        self.assertEqual(saved['fields']['pulse'], 110)
        self.assertEqual(saved['utterances'], [])

    def test_two_concurrent_edits_only_one_wins_expected_version(self):
        s = self.create()
        gate, outcomes = threading.Barrier(2), []
        bodies = [self.common(s, changes={'pulse': pulse}, change_kind='correction') for pulse in (100, 110)]
        def run(body):
            conn = db.connect(self.path)
            try:
                gate.wait(timeout=5)
                try:
                    voice.edit(conn, self.now, s['id'], body)
                    outcomes.append('ok')
                except service.ServiceError as error:
                    outcomes.append(error.code)
            finally:
                conn.close()
        threads = [threading.Thread(target=run, args=(body,)) for body in bodies]
        for worker in threads:
            worker.start()
        for worker in threads:
            worker.join(timeout=7)
        self.assertTrue(all(not worker.is_alive() for worker in threads))
        self.assertCountEqual(outcomes, ['ok', 'VOICE_VERSION_CONFLICT'])

    def test_nested_transaction_inner_failure_can_be_caught_without_committing_outer(self):
        with db.transaction(self.conn):
            self.conn.execute("INSERT INTO events(at,actor,type,data) VALUES(?,'test','outer','{}')", (self.now,))
            try:
                with db.transaction(self.conn):
                    self.conn.execute("INSERT INTO events(at,actor,type,data) VALUES(?,'test','inner','{}')", (self.now,))
                    raise ValueError('rollback inner')
            except ValueError:
                pass
            self.assertEqual([r[0] for r in self.conn.execute("SELECT type FROM events WHERE actor='test'")], ['outer'])
        self.assertFalse(self.conn.in_transaction)

    def test_unchanged_or_undone_draft_preserves_existing_hospital_acceptance(self):
        self.patient(age=62)
        iid = self.accepted('A', 'H1')
        s = self.create('A')
        s = self.edit(s, {'age': 63})
        s = voice.undo(self.conn, self.now, s['id'], self.common(s))['session']
        s = self.approve(s)
        result = voice.submit(self.conn, self.now, s['id'], self.common(s))
        self.assertEqual(result['operational_effect'], 'voice_record_saved_no_patient_change')
        self.assertEqual(result['patient']['version'], 1)
        self.assertEqual(service._inquiry(self.conn, iid)['state'], 'ACCEPTED')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM patient_amendments').fetchone()[0], 0)

    def test_age_group_replaces_incorrect_exact_age_with_unknown_not_rounded_guess(self):
        self.patient(age=7)
        s = self.approve(self.dictate(self.create('A'), '60대 남성'))
        p = voice.submit(self.conn, self.now, s['id'], self.common(s))['patient']
        self.assertIsNone(p['age'])
        self.assertEqual(p['extra']['voice_observations']['fields']['age_group'], '60대')

    def test_full_quotes_are_retained_in_voice_history_but_not_amendment_payload(self):
        self.patient()
        s = self.create('A')
        for text in ('흉통.' + ' 환자 설명.' * 430, '기침 없음.' + ' 현장 설명.' * 430,
                     '발열 의심.' + ' 상태 확인.' * 430):
            s = self.dictate(s, text)
        s = self.approve(s)
        result = voice.submit(self.conn, self.now, s['id'], self.common(s))
        self.assertEqual(result['patient']['version'], 2)
        metadata = result['patient']['extra']['voice_observations']['field_metadata']
        self.assertTrue(all('evidence' not in row for row in metadata.values()))
        self.assertTrue(result['session']['utterances'][0]['transcript'].startswith('흉통.'))

    def test_actor_bound_and_future_time_rejected_before_apply(self):
        self.assertEqual(self.code(voice.create, self.conn, self.now,
            {'actor_id': 'x' * 81, 'client_request_id': 'actor-too-long'}), 'INVALID')
        s = self.create()
        self.assertEqual(self.code(voice.edit, self.conn, self.now, s['id'], self.common(s,
            changes={'onset_at': self.now + 1}, change_kind='correction')), 'INVALID_VOICE_FIELDS')


