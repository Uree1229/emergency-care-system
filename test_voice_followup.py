"""Collection-question policies; no microphone, database or clinical decisions."""
import copy
import unittest

from server import voice_followup as followup


FULL = dict(age=47, sex='male', symptoms=['흉통'], consciousness='alert',
            breathing='normal', systolic_bp=120, diastolic_bp=80, pulse=92,
            spo2=97, temperature=37.2, respiratory_rate=20, glucose=105,
            onset_at=1793330000)


class FollowupPlanTest(unittest.TestCase):
    def test_empty_extended_checklist_has_stable_grouped_questions(self):
        result = followup.plan({})
        self.assertEqual(result['profile'], 'voice_extended_v1')
        self.assertEqual(result['unanswered_count'], 12)
        self.assertEqual(result['next_question']['id'], 'age')
        bp = next(item for item in result['items'] if item['id'] == 'blood_pressure')
        self.assertEqual(bp['fields'], ['systolic_bp', 'diastolic_bp'])
        self.assertFalse(result['complete'])
        self.assertFalse(result['collection_complete'])

    def test_long_trial_missing_fields_are_asked_without_guessed_values(self):
        trial = {key: FULL[key] for key in ('age', 'sex', 'symptoms', 'onset_at', 'spo2', 'temperature', 'glucose')}
        before = copy.deepcopy(trial)
        result = followup.plan(trial)
        self.assertEqual([item['id'] for item in result['items']],
                         ['consciousness', 'breathing', 'blood_pressure', 'pulse', 'respiratory_rate'])
        self.assertEqual(result['next_question']['id'], 'consciousness')
        self.assertEqual(trial, before)
        self.assertTrue(all(item['reason'] == 'missing' for item in result['items']))

    def test_full_observations_resolve_checklist_without_clinical_claim(self):
        result = followup.plan(FULL)
        self.assertTrue(result['complete'])
        self.assertTrue(result['collection_complete'])
        self.assertIsNone(result['next_question'])
        self.assertEqual(result['items'], [])
        self.assertEqual(result['purpose'], 'demonstration_collection_checklist')
        self.assertNotIn('ready_to_submit', result)

    def test_core_profile_does_not_request_extended_measurements(self):
        core = {key: value for key, value in FULL.items()
                if key not in ('temperature', 'respiratory_rate', 'glucose', 'onset_at')}
        self.assertTrue(followup.plan(core, profile='voice_core_v1')['complete'])
        self.assertEqual(len(followup.plan(core)['items']), 4)

    def test_age_group_is_accepted_without_inventing_exact_age(self):
        fields = FULL | {'age_group': '40대'}
        fields.pop('age')
        self.assertTrue(followup.plan(fields)['complete'])
        self.assertNotIn('age', fields)

    def test_numeric_zero_is_not_treated_as_missing(self):
        fields = FULL | {'age': 0, 'pulse': 0, 'onset_at': 0}
        self.assertTrue(followup.plan(fields)['complete'])

    def test_single_bp_measurement_requests_pair_and_marks_only_missing_key(self):
        fields = dict(FULL)
        fields.pop('diastolic_bp')
        result = followup.plan(fields)
        self.assertEqual(len(result['items']), 1)
        self.assertEqual(result['next_question']['id'], 'blood_pressure')
        self.assertEqual(result['next_question']['missing_fields'], ['diastolic_bp'])

    def test_uncertain_symptoms_remain_unresolved_and_explicit_denial_is_not_global_denial(self):
        fields = dict(FULL)
        fields.pop('symptoms')
        fields['uncertain_symptoms'] = ['흉통']
        result = followup.plan(fields)
        self.assertEqual(result['next_question']['id'], 'symptoms')
        self.assertEqual(result['next_question']['reason'], 'uncertain')
        fields['denied_symptoms'] = ['구토']
        self.assertFalse(followup.plan(fields)['complete'])
        self.assertEqual(followup.plan(fields)['next_question']['uncertain_items'], ['흉통'])
        self.assertIn('흉통 증상', followup.plan(fields)['next_question']['question'])
        fields.pop('uncertain_symptoms')
        self.assertTrue(followup.plan(fields)['complete'])

    def test_explicit_unknown_stays_unresolved_without_repeating_question(self):
        result = followup.plan(FULL | {'sex': 'unknown', 'breathing': 'unknown'})
        self.assertFalse(result['complete'])
        self.assertTrue(result['collection_complete'])
        self.assertIsNone(result['next_question'])
        self.assertEqual([item['answer_status'] for item in result['items']], ['unavailable', 'unavailable'])
        self.assertTrue(all(not item['askable'] for item in result['items']))

    def test_answered_unavailable_question_ids_do_not_fill_patient_fields(self):
        fields = dict(FULL)
        fields.pop('temperature')
        fields.pop('glucose')
        result = followup.plan(fields, answered_unavailable=['temperature', 'glucose'])
        self.assertTrue(result['collection_complete'])
        self.assertFalse(result['complete'])
        self.assertEqual(result['unresolved_count'], 2)
        self.assertNotIn('temperature', fields)
        self.assertTrue(followup.plan(FULL, profile='voice_core_v1',
                                      answered_unavailable=['temperature'])['complete'])

    def test_ambiguity_warning_targets_only_missing_fields(self):
        warnings = ['혈압에 서로 다른 값이 있어 자동 입력하지 않았습니다. 확인해 주세요.']
        self.assertTrue(followup.plan(FULL, warnings)['complete'])  # historical accumulated warning
        fields = dict(FULL)
        fields.pop('systolic_bp')
        fields.pop('diastolic_bp')
        result = followup.plan(fields, warnings)
        self.assertEqual(result['next_question']['reason'], 'needs_clarification')
        self.assertIn('자동으로 확정하기 어려운', result['next_question']['question'])

    def test_fresh_metadata_flag_reopens_prior_value_without_overwriting_it(self):
        metadata = {'systolic_bp': {'needs_clarification': True}, 'pulse': {'status': 'uncertain'}}
        result = followup.plan(FULL, field_metadata=metadata)
        self.assertEqual([item['id'] for item in result['items']], ['blood_pressure', 'pulse'])
        self.assertEqual(result['next_question']['clarification_fields'], ['systolic_bp'])
        self.assertEqual(result['next_question']['missing_fields'], [])
        self.assertEqual(FULL['systolic_bp'], 120)
        self.assertTrue(all(item['reason'] == 'needs_clarification' for item in result['items']))

    def test_lookup_and_plan_are_fresh_deterministic_objects(self):
        first = followup.lookup('blood_pressure')
        first['fields'].append('not_a_field')
        self.assertEqual(followup.lookup('blood_pressure')['fields'], ['systolic_bp', 'diastolic_bp'])
        one, two = followup.plan({}), followup.plan({})
        self.assertEqual(one, two)
        one['next_question']['fields'].append('not_a_field')
        self.assertNotIn('not_a_field', one['items'][0]['fields'])
        self.assertNotIn('not_a_field', two['next_question']['fields'])

    def test_invalid_contracts_raise_without_fallback_profile(self):
        for invoke in (
            lambda: followup.plan({}, profile='clinical_required'),
            lambda: followup.lookup('glucose', profile='voice_core_v1'),
            lambda: followup.lookup('unknown_question'),
            lambda: followup.plan([], ()),
            lambda: followup.plan({}, '혈압 모호'),
            lambda: followup.plan({}, [False]),
            lambda: followup.plan({}, field_metadata=['bad']),
            lambda: followup.plan({}, answered_unavailable='blood_pressure'),
            lambda: followup.plan({}, answered_unavailable=['fake_question']),
        ):
            with self.subTest(invoke=invoke):
                with self.assertRaises((TypeError, ValueError)):
                    invoke()


if __name__ == '__main__':
    unittest.main()
