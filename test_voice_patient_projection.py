"""Voice-to-patient projection regressions using an isolated synthetic database.

These tests compare saved patient data and the hospital's summary, rather than
only the intermediate draft. No microphone, frontend or external service runs.
"""
import copy
import unittest

from server import db, service, voice_input as voice


NOW = 2_000_000_000


class VoicePatientProjection(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(':memory:')
        db.init(self.conn)
        self.counter = 0
        self.actor = 'projection-test-crew'
        service.upsert_hospital(self.conn, dict(id='PROJECTION-H1', name='Synthetic hospital',
            lat=35.17, lon=126.85, capabilities=['응급의학'], source='synthetic'))
        service.set_resource(self.conn, NOW, 'PROJECTION-H1', 'ed_bed', 5, 0, source='synthetic')

    def tearDown(self):
        self.conn.close()

    def common(self, session=None, **extra):
        self.counter += 1
        body = dict(actor_id=self.actor, client_request_id='projection-' + str(self.counter), **extra)
        if session is not None:
            body['expected_version'] = session['version']
        return body

    def patient(self, *, extra=None, **changes):
        payload = dict(reporter_role='transporter', consent=True, age=47,
            symptoms='환자가 설명한 흉부 통증', consciousness='alert', breathing='normal',
            lat=35.16, lon=126.85, priority=4, priority_source='expert',
            required_capabilities=['응급의학'], resource_demands={'ed_bed': 1},
            extra=copy.deepcopy(extra if extra is not None else self.manual_extra()))
        payload.update(changes)
        return service.create_patient(self.conn, NOW, 'PROJECTION-P1', payload)

    @staticmethod
    def manual_extra():
        return dict(avpu='A', chief_complaint='cardiac', age_group='adult', pre_ktas_level=2,
            stemi_suspected='no', injury_region='head', labor='no', pregnancy_weeks=35,
            seizure_ongoing='no', bleeding_risk='yes', high_risk_mechanism='no',
            vitals=dict(sbp=120, dbp=80, hr=90, rr=18, spo2=98, temp=37.2,
                        glucose=105, nrs=4, gcs=15, measured_at=NOW - 60,
                        monitor_tag='synthetic-monitor'),
            other_team_key={'preserve': True})

    def create(self, pid='PROJECTION-P1'):
        body = self.common()
        if pid is not None:
            body['patient_id'] = pid
        return voice.create(self.conn, NOW, body)['session']

    def edit(self, session, changes=None, unset=()):
        return voice.edit(self.conn, NOW, session['id'], self.common(session,
            changes=changes or {}, unset_fields=list(unset), change_kind='correction'))['session']

    def approve(self, session):
        return voice.confirm(self.conn, NOW, session['id'],
            self.common(session, confirmed=True))['session']

    def submit(self, session, **extra):
        return voice.submit(self.conn, NOW, session['id'], self.common(session, **extra))

    def say(self, session, text):
        return voice.transcript(self.conn, NOW, session['id'], self.common(session,
            text=text, utterance_id='projection-speech-' + str(self.counter),
            is_final=True, mode='auto'))['session']

    def answer(self, session, question, text):
        return voice.answer_followup(self.conn, NOW, session['id'], self.common(session,
            question_id=question, text=text,
            utterance_id='projection-answer-' + str(self.counter)))['session']

    def hospital_patient(self, pid='PROJECTION-P1'):
        service.send_inquiries(self.conn, NOW, pid, ['PROJECTION-H1'])
        view = service.hospital_inquiries(self.conn, NOW, 'PROJECTION-H1')
        return next(item['patient'] for item in view['open'] if item['patient_id'] == pid)

    def test_existing_manual_r1_and_vitals_are_available_in_voice_draft(self):
        self.patient()
        session = self.create()
        fields = session['fields']
        expected = dict(avpu='A', chief_complaint='cardiac', age_category='adult', pre_ktas=2,
            stemi_suspected='no', injury_region='head', labor='no', pregnancy_weeks=35,
            seizure_ongoing='no', bleeding_risk='yes', high_risk_mechanism='no',
            systolic_bp=120, diastolic_bp=80, pulse=90, respiratory_rate=18,
            spo2=98, temperature=37.2, glucose=105, pain_score=4, gcs=15)
        for key, value in expected.items():
            with self.subTest(field=key):
                self.assertEqual(fields.get(key), value)
                self.assertEqual(session['field_metadata'][key]['source'], 'patient_record')
        self.assertNotIn('measured_at', fields)
        self.assertNotIn('monitor_tag', fields)

    def test_updating_one_vital_preserves_other_measurements_and_unrelated_data(self):
        initial = self.patient()
        result = self.submit(self.approve(self.edit(self.create(), {'pulse': 95})))
        saved = result['patient']
        expected = copy.deepcopy(initial['extra']['vitals'])
        expected['hr'] = 95
        self.assertEqual(saved['extra']['vitals'], expected)
        self.assertEqual(saved['extra']['other_team_key'], {'preserve': True})
        self.assertEqual(saved['extra']['voice_observations']['fields']['pulse'], 95)
        self.assertEqual(self.hospital_patient()['extra']['vitals'], expected)

    def test_current_manual_values_override_historical_voice_snapshot(self):
        extra = self.manual_extra()
        extra['voice_observations'] = {'fields': dict(age=65, consciousness='unresponsive',
            breathing='absent', avpu='U', chief_complaint='trauma', age_category='child',
            pre_ktas=5, systolic_bp=90, diastolic_bp=60, pulse=70)}
        self.patient(extra=extra)
        fields = self.create()['fields']
        expected = dict(age=47, consciousness='alert', breathing='normal', avpu='A',
            chief_complaint='cardiac', age_category='adult', pre_ktas=2,
            systolic_bp=120, diastolic_bp=80, pulse=90)
        for key, value in expected.items():
            with self.subTest(field=key):
                self.assertEqual(fields.get(key), value)

    def test_explicit_unset_removes_r1_and_vital_mirrors_seen_by_hospital(self):
        self.patient()
        unset = ('avpu', 'chief_complaint', 'age_category', 'pre_ktas', 'pulse',
                 'stemi_suspected', 'injury_region', 'labor', 'pregnancy_weeks',
                 'seizure_ongoing', 'bleeding_risk', 'high_risk_mechanism')
        session = self.edit(self.create(), unset=unset)
        for key in unset:
            self.assertNotIn(key, session['fields'])
        saved = self.submit(self.approve(session))['patient']
        for key in ('avpu', 'chief_complaint', 'age_group', 'pre_ktas_level',
                    'stemi_suspected', 'injury_region', 'labor', 'pregnancy_weeks',
                    'seizure_ongoing', 'bleeding_risk', 'high_risk_mechanism'):
            with self.subTest(mirror=key):
                self.assertNotIn(key, saved['extra'])
        self.assertNotIn('hr', saved['extra']['vitals'])
        self.assertEqual(saved['extra']['vitals']['sbp'], 120)
        hospital = self.hospital_patient()
        self.assertNotIn('pre_ktas_level', hospital['extra'])
        self.assertNotIn('hr', hospital['extra']['vitals'])
        self.assertIsNone(hospital['priority'])
        self.assertEqual(hospital['priority_source'], 'unknown')

    def test_unsetting_prektas_clears_old_priority_without_guessing_replacement(self):
        self.patient()
        saved = self.submit(self.approve(self.edit(self.create(), unset=('pre_ktas',))))['patient']
        self.assertNotIn('pre_ktas_level', saved['extra'])
        self.assertIsNone(saved['priority'])
        self.assertEqual(saved['priority_source'], 'unknown')
        self.assertEqual(saved['effective_priority'], 4)
        self.assertEqual(saved['extra']['avpu'], 'A')
        self.assertEqual(saved['extra']['vitals']['hr'], 90)

    def test_undo_then_unchanged_submission_keeps_existing_hospital_acceptance(self):
        original = self.patient()
        service.send_inquiries(self.conn, NOW, original['id'], ['PROJECTION-H1'])
        inquiry = self.conn.execute('SELECT id FROM inquiries WHERE patient_id=?', (original['id'],)).fetchone()[0]
        service.respond(self.conn, NOW, inquiry, 'accept', 'synthetic-desk')
        session = self.create()
        initial_fields = copy.deepcopy(session['fields'])
        session = self.edit(session, {'pulse': 95}, unset=('pre_ktas',))
        session = voice.undo(self.conn, NOW, session['id'], self.common(session))['session']
        self.assertEqual(session['fields'], initial_fields)
        result = self.submit(self.approve(session))
        self.assertEqual(result['operational_effect'], 'voice_record_saved_no_patient_change')
        self.assertEqual(result['patient']['version'], original['version'])
        self.assertEqual(result['patient']['extra'], original['extra'])
        self.assertEqual(service._inquiry(self.conn, inquiry)['state'], 'ACCEPTED')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM patient_amendments').fetchone()[0], 0)

    def test_existing_treatment_requirements_remain_when_voice_complaint_changes(self):
        extra = self.manual_extra()
        extra['treatment_profile_id'] = 'TR_GENERAL_ED'
        original = self.patient(extra=extra)
        saved = self.submit(self.approve(self.edit(self.create(), {'chief_complaint': 'stroke'})))['patient']
        self.assertEqual(saved['extra']['chief_complaint'], 'stroke')
        self.assertEqual(saved['extra']['treatment_profile_id'], original['extra']['treatment_profile_id'])
        self.assertEqual(saved['required_capabilities'], original['required_capabilities'])
        self.assertEqual(saved['resource_demands'], original['resource_demands'])
        self.assertEqual(saved['extra']['other_team_key'], original['extra']['other_team_key'])

    def test_invalid_mirror_value_does_not_discard_other_valid_baseline_fields(self):
        extra = self.manual_extra()
        extra.update(avpu='invalid-avpu', pre_ktas_level=True)
        extra['vitals'].update(sbp=False, spo2='ninety-eight')
        self.patient(extra=extra)
        fields = self.create()['fields']
        for key in ('avpu', 'pre_ktas', 'systolic_bp', 'spo2'):
            self.assertNotIn(key, fields)
        self.assertEqual(fields['age'], 47)
        self.assertEqual(fields['chief_complaint'], 'cardiac')
        self.assertEqual(fields['diastolic_bp'], 80)
        self.assertEqual(fields['pulse'], 90)
        self.assertEqual(fields['temperature'], 37.2)

    def test_server_priority_is_not_reverse_inferred_as_prektas(self):
        self.patient(extra={'vitals': {'hr': 90}, 'other_team_key': 'keep'},
                     priority=2, priority_source='expert')
        session = self.create()
        self.assertNotIn('pre_ktas', session['fields'])
        saved = self.submit(self.approve(self.edit(session, {'pulse': 95})))['patient']
        self.assertEqual((saved['priority'], saved['priority_source']), (2, 'expert'))
        self.assertNotIn('pre_ktas_level', saved['extra'])

    def test_corrected_prektas_remains_consistent_after_apply_and_reopen(self):
        self.patient()
        saved = self.submit(self.approve(self.edit(self.create(), {'pre_ktas': 4})))['patient']
        self.assertEqual((saved['priority'], saved['priority_source']), (1, 'expert'))
        self.assertEqual(saved['extra']['pre_ktas_level'], 4)
        self.assertEqual(saved['extra']['voice_observations']['fields']['pre_ktas'], 4)
        reopened = self.create()
        self.assertEqual(reopened['fields']['pre_ktas'], 4)
        self.assertEqual(reopened['fields']['pulse'], 90)
        self.assertEqual(self.hospital_patient()['extra']['pre_ktas_level'], 4)

    def test_recheck_metadata_survives_submission_and_new_voice_session(self):
        self.patient()
        session = self.say(self.create(), '혈압이 지금 떨어졌어')
        self.assertEqual(session['fields']['systolic_bp'], 120)
        self.assertTrue(session['field_metadata']['systolic_bp']['needs_clarification'])
        self.submit(self.approve(session))
        reopened = self.create()
        self.assertEqual(reopened['fields']['systolic_bp'], 120)
        self.assertTrue(reopened['field_metadata']['systolic_bp']['needs_clarification'])
        question = next(q for q in reopened['followup']['items'] if q['id'] == 'blood_pressure')
        self.assertEqual(question['reason'], 'needs_clarification')
        self.assertTrue(question['askable'])

    def test_unavailable_answer_survives_submission_without_reopening_same_question(self):
        self.patient()
        session = self.say(self.create(), '혈압이 지금 떨어졌어')
        session = self.answer(session, 'blood_pressure', '미측정')
        self.submit(self.approve(session))
        reopened = self.create()
        question = next(q for q in reopened['followup']['items'] if q['id'] == 'blood_pressure')
        self.assertEqual(question['answer_status'], 'unavailable')
        self.assertFalse(question['askable'])
        self.assertTrue(reopened['field_metadata']['systolic_bp']['needs_clarification'])

    def test_new_manual_measurement_does_not_inherit_old_clarification(self):
        patient = self.patient()
        extra = copy.deepcopy(patient['extra'])
        extra['voice_observations'] = {
            'fields': {'systolic_bp': 100, 'diastolic_bp': 60},
            'field_metadata': {
                'systolic_bp': {'needs_clarification': True, 'clarification_at': NOW - 20,
                    'followup_unavailable_for': 'blood_pressure', 'observed_at': NOW - 30},
                'diastolic_bp': {'needs_clarification': True, 'clarification_at': NOW - 20,
                    'followup_unavailable_for': 'blood_pressure', 'observed_at': NOW - 30},
            },
        }
        # The current manual values (120/80) differ from the historical snapshot.
        service.update_patient(self.conn, NOW, patient['id'], {'extra': extra})
        reopened = self.create()
        self.assertEqual((reopened['fields']['systolic_bp'], reopened['fields']['diastolic_bp']), (120, 80))
        self.assertFalse(reopened['field_metadata']['systolic_bp'].get('needs_clarification', False))
        self.assertFalse(reopened['field_metadata']['diastolic_bp'].get('needs_clarification', False))
        self.assertFalse(any(q['id'] == 'blood_pressure' for q in reopened['followup']['items']))

    def test_current_free_text_symptoms_survive_unrelated_voice_vital_edit(self):
        extra = self.manual_extra()
        extra['voice_observations'] = {'fields': {
            'symptoms': ['흉통'], 'denied_symptoms': ['구토'], 'uncertain_symptoms': ['복통']}}
        current_text = '오른쪽 발목이 아프고 걸을 때 불편하다고 설명함'
        self.patient(extra=extra, symptoms=current_text)
        session = self.create()
        self.assertEqual(session['baseline_summary']['symptoms'], current_text)
        for key in ('symptoms', 'denied_symptoms', 'uncertain_symptoms'):
            self.assertNotIn(key, session['fields'])
        saved = self.submit(self.approve(self.edit(session, {'pulse': 95})))['patient']
        self.assertEqual(saved['symptoms'], current_text)
        self.assertEqual(self.hospital_patient()['symptoms'], current_text)

    def test_new_symptom_does_not_merge_superseded_structured_symptoms(self):
        extra = self.manual_extra()
        extra['voice_observations'] = {'fields': {
            'symptoms': ['흉통'], 'denied_symptoms': ['구토'], 'uncertain_symptoms': ['복통']}}
        self.patient(extra=extra, symptoms='발목 불편감을 현재 증상으로 확인함')
        session = self.say(self.create(), '발열 있음')
        self.assertEqual(session['fields']['symptoms'], ['발열'])
        self.assertNotIn('denied_symptoms', session['fields'])
        self.assertNotIn('uncertain_symptoms', session['fields'])
        saved = self.submit(self.approve(session))['patient']
        self.assertEqual(saved['symptoms'], '발열')
        self.assertEqual(saved['extra']['voice_observations']['fields']['symptoms'], ['발열'])
        self.assertEqual(self.hospital_patient()['symptoms'], '발열')

    def test_explicit_empty_vitals_do_not_resurrect_historical_voice_values(self):
        extra = self.manual_extra()
        extra['vitals'] = {}
        extra['voice_observations'] = {'fields': {
            'systolic_bp': 120, 'diastolic_bp': 80, 'pulse': 90, 'spo2': 98}}
        self.patient(extra=extra)
        session = self.create()
        for key in ('systolic_bp', 'diastolic_bp', 'pulse', 'spo2'):
            self.assertNotIn(key, session['fields'])
        saved = self.submit(self.approve(self.edit(session, {'chief_complaint': 'stroke'})))['patient']
        self.assertEqual(saved['extra']['vitals'], {})
        self.assertEqual(self.hospital_patient()['extra']['vitals'], {})

    def test_partial_manual_vitals_are_authoritative_over_older_full_snapshot(self):
        extra = self.manual_extra()
        extra['vitals'] = {'hr': 99, 'measured_at': NOW - 10}
        extra['voice_observations'] = {'fields': {
            'systolic_bp': 120, 'diastolic_bp': 80, 'pulse': 90, 'spo2': 98}}
        self.patient(extra=extra)
        session = self.create()
        self.assertEqual(session['fields']['pulse'], 99)
        for key in ('systolic_bp', 'diastolic_bp', 'spo2'):
            self.assertNotIn(key, session['fields'])
        saved = self.submit(self.approve(self.edit(session, {'pulse': 100})))['patient']
        self.assertEqual(saved['extra']['vitals'], {'hr': 100, 'measured_at': NOW - 10})
        self.assertEqual(self.hospital_patient()['extra']['vitals'], saved['extra']['vitals'])


if __name__ == '__main__':
    unittest.main()
