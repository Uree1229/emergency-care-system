"""Treatment review contracts over an isolated synthetic database.

These tests compare persisted patient, inquiry and planning state. No UI,
microphone, external medical API or real hospital request is used.
"""
import copy
import unittest
from unittest.mock import patch

from server import db, service, treatment_binding, voice_input as voice


NOW = 2_000_000_000


class VoiceTreatmentReviewTests(unittest.TestCase):
    def setUp(self):
        from server import voice_treatment_review
        self.review = voice_treatment_review
        self.conn = db.connect(':memory:')
        db.init(self.conn)
        self.actor = 'TREATMENT-REVIEW-QA'
        self.counter = 0
        for hid, caps in (('REVIEW-GENERAL', ['응급의학']),
                          ('REVIEW-CARDIAC', ['응급의학', '심장내과', '정형외과', '추가진료'])):
            service.upsert_hospital(self.conn, dict(id=hid, name='Synthetic '+hid,
                lat=35.17, lon=126.85, capabilities=caps, source='synthetic'))
            for resource in ('ed_bed', 'special_team'):
                service.set_resource(self.conn, NOW, hid, resource, 5, 0, source='synthetic')

    def tearDown(self):
        self.conn.close()

    def common(self, session=None, **extra):
        self.counter += 1
        body = dict(actor_id=self.actor, client_request_id='treatment-'+str(self.counter), **extra)
        if session is not None:
            body['expected_version'] = session['version']
        return body

    def patient(self, *, profile='TR_GENERAL_ED', stemi='no', complaint='cardiac',
                capabilities=None, demands=None):
        return service.create_patient(self.conn, NOW, 'REVIEW-P1', dict(
            reporter_role='transporter', consent=True, lat=35.16, lon=126.85,
            age=47, symptoms='흉통', consciousness='alert', breathing='normal',
            priority=4, priority_source='expert',
            required_capabilities=list(capabilities or []), resource_demands=dict(demands or {'ed_bed':1}),
            extra=dict(treatment_profile_id=profile, avpu='A', chief_complaint=complaint,
                age_group='adult', pre_ktas_level=2, stemi_suspected=stemi,
                vitals={'sbp':120, 'dbp':80, 'hr':90}, other_team_data={'preserve':True})))

    def create(self):
        return voice.create(self.conn, NOW, self.common(patient_id='REVIEW-P1'))['session']

    def edit(self, session, changes):
        return voice.edit(self.conn, NOW, session['id'], self.common(session,
            changes=changes, change_kind='observation'))['session']

    def approve(self, session):
        return voice.confirm(self.conn, NOW, session['id'], self.common(session, confirmed=True))['session']

    def preview(self, session, selection=None):
        return self.review.preview(self.conn, NOW, session['id'], self.actor, selection=selection)['review']

    def apply_body(self, session, proposal=None, selection=None, **extra):
        proposal = proposal or self.preview(session, selection=selection)
        selected = copy.deepcopy(selection if selection is not None else proposal['selection'])
        return self.common(session, expected_patient_version=service._patient(self.conn, 'REVIEW-P1')['version'],
            review_token=proposal['token'], confirmed=True, selection=selected, **extra)

    def apply(self, session, proposal=None, selection=None, runtime=None, **extra):
        return self.review.apply(self.conn, NOW, session['id'],
            self.apply_body(session, proposal, selection, **extra), runtime=runtime)

    def accepted(self, hospital='REVIEW-GENERAL'):
        service.send_inquiries(self.conn, NOW, 'REVIEW-P1', [hospital])
        item = self.conn.execute('SELECT id FROM inquiries WHERE patient_id=? AND hospital_id=?',
                                 ('REVIEW-P1', hospital)).fetchone()[0]
        service.respond(self.conn, NOW, item, 'accept', 'SYNTHETIC-DESK')
        return item

    def state(self):
        tables = ('patients', 'inquiries', 'events', 'patient_amendments', 'voice_sessions',
                  'voice_requests', 'voice_revisions', 'planning_state', 'inquiry_workflows')
        return {name:[tuple(row) for row in self.conn.execute('SELECT * FROM '+name+' ORDER BY rowid')]
                for name in tables}

    def assert_rejected_without_writes(self, session, body, **kwargs):
        before = self.state()
        with self.assertRaises(service.ServiceError) as error:
            self.review.apply(self.conn, NOW, session['id'], body, **kwargs)
        self.assertEqual(self.state(), before)
        return error.exception

    def test_preview_is_read_only_and_explains_changed_treatment_requirements(self):
        initial = self.patient()
        accepted = self.accepted()
        session = self.edit(self.create(), {'stemi_suspected':'yes'})
        before = self.state()
        proposal = self.preview(session)
        self.assertEqual(self.state(), before)
        self.assertTrue(proposal['suggestion_available'])
        self.assertTrue(proposal['review_required'])
        self.assertEqual(proposal['current']['treatment_profile_id'], 'TR_GENERAL_ED')
        self.assertEqual(proposal['proposed']['treatment_profile_id'], 'TR_CARDIAC_INTERVENTION')
        self.assertIn('심장내과', proposal['proposed']['required_capabilities'])
        self.assertEqual(service._patient(self.conn, initial['id'])['extra']['treatment_profile_id'], 'TR_GENERAL_ED')
        self.assertEqual(service._inquiry(self.conn, accepted)['state'], 'ACCEPTED')

    def test_apply_requires_independent_draft_and_treatment_confirmation(self):
        self.patient()
        session = self.edit(self.create(), {'stemi_suspected':'yes'})
        self.assert_rejected_without_writes(session, self.apply_body(session))
        approved = self.approve(session)
        for invalid in (False, 1, 'true'):
            body = self.apply_body(approved)
            body['confirmed'] = invalid
            error = self.assert_rejected_without_writes(approved, body)
            self.assertEqual(error.code, 'TREATMENT_CONFIRMATION_REQUIRED')

    def test_confirmed_suggestion_applies_once_closes_old_acceptance_and_requeues_planning(self):
        self.patient()
        old = self.accepted()
        session = self.approve(self.edit(self.create(), {'stemi_suspected':'yes'}))
        before_revision = self.conn.execute('SELECT revision FROM planning_state WHERE id=1').fetchone()[0]
        result = self.apply(session, allow_reduction=True)
        patient = result['patient']
        self.assertEqual(patient['extra']['treatment_profile_id'], 'TR_CARDIAC_INTERVENTION')
        self.assertEqual(patient['extra']['stemi_suspected'], 'yes')
        self.assertIn('심장내과', patient['required_capabilities'])
        self.assertEqual(service._inquiry(self.conn, old)['state'], 'CANCELLED')
        self.assertEqual(patient['state'], 'WAITING')
        self.assertIsNone(patient['destination_hospital_id'])
        self.assertEqual(result['session']['state'], 'SUBMITTED')
        self.assertTrue(result['reevaluation']['requested'])
        after_revision = self.conn.execute('SELECT revision FROM planning_state WHERE id=1').fetchone()[0]
        self.assertGreater(after_revision, before_revision)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM inquiries').fetchone()[0], 1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM patient_amendments').fetchone()[0], 1)
        self.assertEqual(patient['extra']['other_team_data'], {'preserve':True})

    def test_same_request_retries_without_duplicate_amendment_or_planning_change(self):
        self.patient()
        session = self.approve(self.edit(self.create(), {'stemi_suspected':'yes'}))
        body = self.apply_body(session, allow_reduction=True)
        first = self.review.apply(self.conn, NOW, session['id'], body)
        before_retry = self.state()
        duplicate = self.review.apply(self.conn, NOW, session['id'], copy.deepcopy(body))
        self.assertTrue(duplicate['duplicate'])
        self.assertEqual(duplicate['patient']['version'], first['patient']['version'])
        self.assertEqual(self.state(), before_retry)

    def test_keep_conditions_applies_observation_without_adopting_suggested_profile(self):
        self.patient()
        session = self.approve(self.edit(self.create(), {'stemi_suspected':'yes'}))
        result = self.apply(session, selection={'mode':'keep'})
        self.assertEqual(result['patient']['extra']['treatment_profile_id'], 'TR_GENERAL_ED')
        self.assertEqual(result['patient']['extra']['stemi_suspected'], 'yes')
        self.assertNotIn('심장내과', result['patient']['required_capabilities'])

    def test_unchanged_keep_preserves_patient_version_acceptance_and_planning_revision(self):
        initial = self.patient()
        accepted = self.accepted()
        session = self.approve(self.create())
        revision = self.conn.execute('SELECT revision FROM planning_state WHERE id=1').fetchone()[0]
        result = self.apply(session, selection={'mode':'keep'})
        self.assertEqual(result['patient']['version'], initial['version'])
        self.assertEqual(service._inquiry(self.conn, accepted)['state'], 'ACCEPTED')
        self.assertFalse(result['reevaluation']['requested'])
        self.assertEqual(self.conn.execute('SELECT revision FROM planning_state WHERE id=1').fetchone()[0], revision)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM patient_amendments').fetchone()[0], 0)

    def test_vital_only_update_never_suggests_downgrading_existing_custom_profile(self):
        self.patient(profile='TR_CARDIAC_INTERVENTION', stemi='no')
        session = self.edit(self.create(), {'pulse':96})
        proposal = self.preview(session)
        self.assertFalse(proposal['suggestion_available'])
        self.assertEqual(proposal['proposed']['treatment_profile_id'], 'TR_CARDIAC_INTERVENTION')

    def test_age_change_within_same_category_does_not_downgrade_existing_custom_profile(self):
        self.patient(profile='TR_CARDIAC_INTERVENTION', stemi='no')
        session = self.edit(self.create(), {'age':48})
        self.assertEqual(session['fields']['age_category'], 'adult')
        proposal = self.preview(session)
        self.assertFalse(proposal['suggestion_available'])
        self.assertEqual(proposal['proposed']['treatment_profile_id'], 'TR_CARDIAC_INTERVENTION')
        self.assertEqual(proposal['proposed']['required_capabilities'], proposal['current']['required_capabilities'])

    def test_new_explicit_condition_is_reviewed_even_if_effective_requirements_are_equal(self):
        initial = self.patient()
        accepted = self.accepted()
        session = self.approve(self.create())
        selection = {'mode':'custom', 'required_capabilities':['응급의학']}
        proposal = self.preview(session, selection=selection)
        self.assertEqual(proposal['current']['required_capabilities'], proposal['proposed']['required_capabilities'])
        self.assertEqual(proposal['current']['explicit_required_capabilities'], [])
        self.assertEqual(proposal['proposed']['explicit_required_capabilities'], ['응급의학'])
        self.assertTrue(proposal['review_required'])
        self.assertTrue(any(change['field']=='explicit_required_capabilities' for change in proposal['changes']))
        result = self.apply(session, proposal=proposal, selection=selection)
        self.assertEqual(result['patient']['explicit_required_capabilities'], ['응급의학'])
        self.assertEqual(result['patient']['version'], initial['version']+1)
        self.assertTrue(result['reevaluation']['requested'])
        self.assertEqual(service._inquiry(self.conn, accepted)['state'], 'CANCELLED')

    def test_order_only_explicit_capabilities_change_keeps_acceptance_and_planning_revision(self):
        initial = self.patient(capabilities=['추가진료','응급의학'])
        accepted = self.accepted('REVIEW-CARDIAC')
        session = self.approve(self.create())
        selection = {'mode':'custom', 'required_capabilities':['응급의학','추가진료']}
        proposal = self.preview(session, selection=selection)
        self.assertFalse(proposal['review_required'])
        self.assertEqual(proposal['changes'], [])
        revision = self.conn.execute('SELECT revision FROM planning_state WHERE id=1').fetchone()[0]
        result = self.apply(session, proposal=proposal, selection=selection)
        self.assertEqual(result['patient']['version'], initial['version'])
        self.assertEqual(set(result['patient']['explicit_required_capabilities']), {'응급의학','추가진료'})
        self.assertFalse(result['reevaluation']['requested'])
        self.assertEqual(service._inquiry(self.conn, accepted)['state'], 'ACCEPTED')
        self.assertEqual(self.conn.execute('SELECT revision FROM planning_state WHERE id=1').fetchone()[0], revision)

    def test_profile_switch_preserves_explicit_requirements_without_sticking_old_derived_conditions(self):
        self.patient(profile='TR_ORTHOPEDIC', complaint='trauma', capabilities=['추가진료'],
                     demands={'ed_bed':1, 'special_team':2})
        session = self.approve(self.edit(self.create(), {'chief_complaint':'cardiac', 'stemi_suspected':'yes'}))
        result = self.apply(session, allow_reduction=True)
        patient = result['patient']
        self.assertIn('심장내과', patient['required_capabilities'])
        self.assertIn('추가진료', patient['required_capabilities'])
        self.assertNotIn('정형외과', patient['required_capabilities'])
        self.assertEqual(patient['explicit_required_capabilities'], ['추가진료'])
        self.assertEqual(patient['explicit_resource_demands'], {'ed_bed':1, 'special_team':2})
        self.assertEqual(patient['resource_demands']['special_team'], 2)

    def test_custom_conditions_are_version_bound_and_do_not_infer_new_requirements(self):
        self.patient()
        session = self.approve(self.edit(self.create(), {'pulse':96}))
        selection = {'mode':'custom', 'treatment_profile_id':'TR_ORTHOPEDIC',
                     'required_capabilities':['추가진료'], 'resource_demands':{'ed_bed':2}}
        result = self.apply(session, selection=selection, allow_reduction=True)
        self.assertEqual(result['patient']['extra']['treatment_profile_id'], 'TR_ORTHOPEDIC')
        self.assertEqual(result['patient']['explicit_required_capabilities'], ['추가진료'])
        self.assertEqual(result['patient']['explicit_resource_demands'], {'ed_bed':2})

    def test_old_voice_or_patient_version_and_wrong_owner_cannot_apply(self):
        self.patient()
        session = self.approve(self.edit(self.create(), {'stemi_suspected':'yes'}))
        proposal = self.preview(session)
        newer = self.approve(self.edit(session, {'pulse':96}))
        stale = self.apply_body(newer, proposal=proposal)
        self.assert_rejected_without_writes(newer, stale)
        foreign = self.apply_body(newer)
        foreign['actor_id'] = 'OTHER-CREW'
        self.assertEqual(self.assert_rejected_without_writes(newer, foreign).status, 403)
        body = self.apply_body(newer)
        service.update_patient(self.conn, NOW, 'REVIEW-P1', {'contact':'010-0000-0000'})
        self.assert_rejected_without_writes(newer, body)

    def test_changed_profile_catalog_invalidates_the_review_token(self):
        self.patient()
        session = self.approve(self.edit(self.create(), {'stemi_suspected':'yes'}))
        body = self.apply_body(session)
        catalog = treatment_binding.catalog()
        catalog['catalog_date'] = 'different-test-version'
        with patch.object(treatment_binding, 'catalog', return_value=catalog):
            self.assert_rejected_without_writes(session, body)

    def test_reduction_requires_explicit_acknowledgement_and_never_sends_a_hospital_request(self):
        self.patient(profile='TR_CARDIAC_INTERVENTION', stemi='yes')
        old = self.accepted('REVIEW-CARDIAC')
        session = self.approve(self.edit(self.create(), {'stemi_suspected':'no'}))
        proposal = self.preview(session)
        self.assertTrue(proposal['reductions'])
        self.assert_rejected_without_writes(session, self.apply_body(session, proposal=proposal))
        result = self.apply(session, proposal=proposal, allow_reduction=True)
        self.assertEqual(result['patient']['extra']['treatment_profile_id'], 'TR_GENERAL_ED')
        self.assertEqual(service._inquiry(self.conn, old)['state'], 'CANCELLED')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM inquiries').fetchone()[0], 1)

    def test_disabled_planner_does_not_roll_back_an_explicit_patient_amendment(self):
        class DisabledRuntime:
            def snapshot(self):
                return {'workers':{'planning':{'state':'DISABLED'}}}
        self.patient()
        session = self.approve(self.edit(self.create(), {'stemi_suspected':'yes'}))
        result = self.apply(session, runtime=DisabledRuntime(), allow_reduction=True)
        self.assertEqual(result['session']['state'], 'SUBMITTED')
        self.assertTrue(result['reevaluation']['requested'])
        self.assertEqual(result['reevaluation']['availability']['worker_state'], 'DISABLED')
        self.assertFalse(result['reevaluation']['availability']['accepting_requests'])

    def test_failure_after_amendment_rolls_back_patient_inquiry_session_and_planning_state(self):
        self.patient()
        self.accepted()
        session = self.approve(self.edit(self.create(), {'stemi_suspected':'yes'}))
        body = self.apply_body(session, allow_reduction=True)
        original = voice.patient_amendments.amend
        def fail_after_update(*args, **kwargs):
            original(*args, **kwargs)
            raise service.ServiceError('INJECTED_TEST_FAILURE', 'synthetic post-update failure', 409)
        with patch.object(voice.patient_amendments, 'amend', side_effect=fail_after_update):
            error = self.assert_rejected_without_writes(session, body)
            self.assertEqual(error.code, 'INJECTED_TEST_FAILURE')
        self.assertFalse(self.review.apply(self.conn, NOW, session['id'], body)['duplicate'])


if __name__ == '__main__':
    unittest.main()
