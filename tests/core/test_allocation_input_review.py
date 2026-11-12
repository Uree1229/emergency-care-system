"""Pure committed-input review tests; no microphone, UI, DB, or clinical inference."""
import copy
import json
import unittest

from server.allocation_input_review import assess, REVISION

NOW = 2_000_000_000


def patient(**changes):
    value = dict(id='INPUT-REVIEW-QA', version=3, state='WAITING', priority=4, priority_source='expert',
                 lat=35.16, lon=126.85, consciousness='alert', breathing='normal', symptoms='흉통',
                 extra={'avpu':'A', 'chief_complaint':'cardiac', 'age_group':'adult'})
    value.update(changes)
    return value


def saved(p, fields, metadata):
    p['extra']['voice_observations'] = dict(fields=fields, field_metadata=metadata,
        actor_id='DO-NOT-EXPOSE-ACTOR', session_id='VS-DO-NOT-EXPOSE',
        transcript='DO-NOT-EXPOSE-TRANSCRIPT')
    return p


class AllocationInputReviewTests(unittest.TestCase):
    def item(self, result, field):
        return next(row for row in result['items'] if row['field'] == field)

    def test_known_expert_input_does_not_require_every_checklist_item(self):
        p = patient()
        result = assess(p, NOW)
        self.assertEqual(result['revision'], REVISION)
        self.assertEqual(result['status'], 'NO_REVIEW_FLAGS')
        self.assertFalse(result['blocking'])
        self.assertEqual(result['items'], [])

    def test_unknown_or_self_priority_reports_existing_conservative_weight(self):
        for priority, source in ((None, 'unknown'), (1, 'self'), (True, 'expert')):
            with self.subTest(priority=priority, source=source):
                result = assess(patient(priority=priority, priority_source=source), NOW)
                row = self.item(result, 'priority')
                self.assertEqual(row['code'], 'PRIORITY_UNVERIFIED')
                self.assertEqual(row['used_priority'], 4)
                self.assertTrue(result['requires_review'])

    def test_current_priority_wins_over_obsolete_voice_grade(self):
        p = patient(priority=1)
        p['extra']['pre_ktas_level'] = 4
        saved(p, {'pre_ktas':2}, {'pre_ktas':{'needs_clarification':True}})
        self.assertFalse(assess(p, NOW)['requires_review'])
        p['extra']['pre_ktas_level'] = 2
        self.assertFalse(assess(p, NOW)['requires_review'])

    def test_current_grade_flag_is_important_when_it_matches_used_priority(self):
        p = patient(priority=4)
        p['extra']['pre_ktas_level'] = 2
        saved(p, {'pre_ktas':2}, {'pre_ktas':{'needs_clarification':True}})
        row = self.item(assess(p, NOW), 'pre_ktas')
        self.assertEqual(row['level'], 'review')
        self.assertEqual(row['used_priority'], 4)

    def test_optional_blood_pressure_unavailable_is_not_a_blocking_review(self):
        p = patient()
        saved(p, {}, {key:{'needs_clarification':True, 'followup_unavailable_for':'blood_pressure'}
                      for key in ('systolic_bp','diastolic_bp')})
        result = assess(p, NOW)
        self.assertEqual(result['status'], 'INFORMATION_ONLY')
        self.assertFalse(result['requires_review']);self.assertFalse(result['blocking'])
        row = self.item(result, 'blood_pressure')
        self.assertEqual(row['state'], 'unavailable')
        self.assertEqual(row['fields'], ['systolic_bp','diastolic_bp'])

    def test_profile_dependencies_are_relevant_only_to_current_complaint(self):
        for complaint, relevant in (('cardiac', True), ('trauma', False)):
            p = patient();p['extra'].update(chief_complaint=complaint, stemi_suspected='unmeasured')
            result = assess(p, NOW)
            self.assertEqual(self.item(result, 'stemi_suspected')['level'], 'review' if relevant else 'info')

    def test_other_profile_branches_do_not_promote_all_secondary_fields(self):
        for complaint, field in (('trauma','injury_region'), ('altered_seizure','injury_region'),
                                 ('obstetric','labor'), ('obstetric','pregnancy_weeks')):
            p = patient();p['extra'].update(chief_complaint=complaint)
            saved(p, {}, {field:{'needs_clarification':True}})
            self.assertEqual(self.item(assess(p, NOW), field)['level'], 'review')
        p = patient();saved(p, {}, {'labor':{'needs_clarification':True}})
        self.assertEqual(self.item(assess(p, NOW), 'labor')['level'], 'info')

    def test_canonical_manual_measurement_does_not_inherit_old_flags(self):
        p = patient();p['extra']['vitals'] = {'hr':95}
        saved(p, {'pulse':90}, {'pulse':{'needs_clarification':True}})
        self.assertEqual(assess(p, NOW)['items'], [])

    def test_explicit_empty_vitals_cannot_resurrect_old_measurements_or_flags(self):
        p = patient();p['extra']['vitals'] = {}
        saved(p, {'systolic_bp':120, 'diastolic_bp':80},
            {key:{'needs_clarification':True} for key in ('systolic_bp','diastolic_bp')})
        self.assertEqual(assess(p, NOW)['items'], [])

    def test_current_core_value_and_exact_type_control_metadata_reuse(self):
        p = patient(consciousness='impaired')
        saved(p, {'consciousness':'alert'}, {'consciousness':{'needs_clarification':True}})
        self.assertFalse(assess(p, NOW)['requires_review'])
        p['extra']['vitals'] = {'hr':90.0}
        p['extra']['voice_observations'] = {'fields':{'pulse':90}, 'field_metadata':{'pulse':{'needs_clarification':True}}}
        self.assertEqual(assess(p, NOW)['items'], [])

    def test_current_consciousness_flag_groups_under_handoff_review(self):
        p = patient()
        saved(p, {'consciousness':'alert','avpu':'A'}, {'consciousness':{'needs_clarification':True}})
        row = self.item(assess(p, NOW), 'consciousness')
        self.assertEqual(row['level'], 'review')
        self.assertEqual(row['relevance'], 'hospital_handoff')

    def test_symptom_uncertainty_does_not_override_changed_manual_text(self):
        p = patient(symptoms='미확인/의심: 흉통')
        saved(p, {'uncertain_symptoms':['흉통']}, {})
        self.assertEqual(self.item(assess(p, NOW), 'symptoms')['state'], 'uncertain')
        p['symptoms'] = '수동으로 갱신한 현재 증상'
        self.assertEqual(assess(p, NOW)['items'], [])

    def test_timestamps_are_age_information_not_a_new_clinical_ttl(self):
        p = patient();p['extra']['vitals'] = {'hr':90}
        saved(p, {'pulse':90}, {'pulse':{'needs_clarification':True,'observed_at':NOW-86400}})
        result = assess(p, NOW);row = self.item(result, 'pulse')
        self.assertEqual(row['age_seconds'], 86400)
        self.assertFalse(result['requires_review'])
        p['extra']['voice_observations']['field_metadata']['pulse']['observed_at'] = NOW+1
        self.assertIsNone(self.item(assess(p, NOW), 'pulse')['observed_at'])

    def test_missing_location_is_an_actual_model_input_and_has_manual_action(self):
        result = assess(patient(lat=None), NOW)
        row = self.item(result, 'location')
        self.assertEqual(row['code'], 'LOCATION_REQUIRED')
        self.assertEqual(row['action']['kind'], 'review_patient_location')
        self.assertFalse(result['blocking'])

    def test_actions_do_not_link_to_private_voice_owner_or_apply_in_transit(self):
        p = patient();p['extra']['stemi_suspected'] = 'unmeasured'
        saved(p, {}, {'stemi_suspected':{'evidence':['PRIVATE-EVIDENCE'],
            'clarification_evidence':['PRIVATE-CLARIFICATION']}})
        result = assess(p, NOW)
        row = self.item(result, 'stemi_suspected')
        self.assertEqual(row['action']['body'], {'patient_id':p['id']})
        text = json.dumps(result)
        for forbidden in ('DO-NOT-EXPOSE','PRIVATE-EVIDENCE','PRIVATE-CLARIFICATION','actor_id','session_id'):
            self.assertNotIn(forbidden, text)
        p['state'] = 'IN_TRANSIT'
        row = self.item(assess(p, NOW), 'stemi_suspected')
        self.assertEqual(row['action']['kind'], 'review_transport')
        self.assertTrue(row['action']['endpoint'].endswith('/transport-review'))

    def test_assessment_is_pure_and_tolerates_malformed_optional_metadata(self):
        p = patient();p['extra']['voice_observations'] = {'fields':[], 'field_metadata':{'pulse':[]}}
        original = copy.deepcopy(p)
        result = assess(p, NOW)
        self.assertEqual(p, original)
        self.assertEqual(result['items'], [])
        p['extra']['voice_observations'] = {'fields':{}, 'field_metadata':{'pulse':{'status':[]}}}
        self.assertEqual(assess(p, NOW)['items'], [])
        p['extra'] = []
        self.assertEqual(assess(p, NOW)['items'], [])

    def test_legacy_nullable_core_does_not_create_an_artificial_mandatory_list(self):
        p = patient(consciousness=None, breathing=None)
        self.assertEqual(assess(p, NOW)['items'], [])
        p['consciousness'] = 'unknown'
        self.assertTrue(assess(p, NOW)['requires_review'])

    def test_unavailable_nullable_core_answer_is_visible_without_inventing_a_value(self):
        p = patient(consciousness=None, breathing=None)
        saved(p, {}, {'consciousness':{'needs_clarification':True,
                                      'followup_unavailable_for':'consciousness'}})
        row = self.item(assess(p, NOW), 'consciousness')
        self.assertEqual(row['level'], 'review')
        self.assertEqual(row['state'], 'unavailable')
        self.assertIsNone(p['consciousness'])

    def test_unavailable_marker_must_cover_the_actual_followup_question(self):
        p = patient()
        saved(p, {'consciousness':'alert','avpu':'A'},
              {'avpu':{'needs_clarification':True,'followup_unavailable_for':'consciousness'}})
        # A marker on AVPU alone cannot claim the consciousness question
        # received an unavailable answer.
        self.assertEqual(self.item(assess(p, NOW), 'consciousness')['state'], 'needs_clarification')

    def test_group_timestamp_cannot_make_older_affected_measurement_look_new(self):
        p = patient();p['extra']['vitals'] = {'sbp':120,'dbp':80}
        saved(p, {'systolic_bp':120,'diastolic_bp':80}, {
            'systolic_bp':{'needs_clarification':True,'observed_at':NOW-600},
            'diastolic_bp':{'needs_clarification':True,'observed_at':NOW-10}})
        row = self.item(assess(p, NOW), 'blood_pressure')
        self.assertEqual(row['observed_at'], NOW-600)
        self.assertEqual(row['age_seconds'], 600)
        # Metadata from an unaffected healthy alternative is not a refresh.
        p = patient();saved(p, {'consciousness':'alert','avpu':'A'}, {
            'consciousness':{'needs_clarification':True,'observed_at':NOW-600},
            'avpu':{'observed_at':NOW-10}})
        self.assertEqual(self.item(assess(p, NOW), 'consciousness')['observed_at'], NOW-600)


if __name__ == '__main__':unittest.main()
