"""공개 진료자료를 환자 수용 확답으로 오인하지 않는지 검증한다."""
import copy
import unittest

from server.treatment_data import (
    EXCLUDED, MATCH, NEEDS_CONFIRMATION, evaluate_requirements,
    normalize_detail, normalize_equipment_beds, normalize_severe,
)


class DetailNormalizationTests(unittest.TestCase):
    def test_department_names_are_trimmed_deduplicated_without_alias_guessing(self):
        result = normalize_detail({'dgidIdName': ' 내과, 산부인과,,내과 , 소아청소년과 '})
        self.assertEqual(result['departments'], ['내과', '산부인과', '소아청소년과'])
        self.assertEqual(result['departments_source_field'], 'dgidIdName')
        self.assertEqual(result['departments_status'], 'reported')
        self.assertEqual(result['equipment'], [])
        self.assertIsNone(result['source_observed_at'])

    def test_missing_empty_invalid_department_values_are_unknown(self):
        for raw in ({}, {'dgidIdName': ''}, {'dgidIdName': None}, {'dgidIdName': 1},
                    {'dgidIdName': ' , , '}):
            with self.subTest(raw=raw):
                result = normalize_detail(raw)
                self.assertEqual(result['departments'], [])
                self.assertEqual(result['departments_status'], 'unknown')

    def test_detail_facility_counts_and_old_codes_are_not_operational_resources(self):
        raw = {'dgidIdName': '산부인과', 'hpicuyn': '10', 'hpopyn': '5',
               'hvec': '2', 'MKioskTy8': 'Y', 'hvctayn': 'Y'}
        before = copy.deepcopy(raw)
        result = normalize_detail(raw)
        self.assertNotIn('resources', result)
        self.assertNotIn('capabilities', result)
        self.assertEqual(result['equipment'], [])
        self.assertEqual(raw, before)

    def test_registered_facility_count_is_not_operational_capacity(self):
        result = normalize_detail({'hperyn': '27', 'hpnicuyn': '0', 'hpopyn': '-1'})
        facilities = {r['facility_type']: r for r in result['facilities']}
        self.assertEqual(facilities['ed_bed']['reported_count'], 27)
        self.assertEqual(facilities['nicu_bed']['reported_count'], 0)
        self.assertIsNone(facilities['operating_room']['reported_count'])
        self.assertNotIn('resources', result)
        self.assertNotIn('capacity', facilities['ed_bed'])

    def test_v13_neurological_and_thoracic_icu_fields_are_not_pediatric_or_cardiac(self):
        result = normalize_detail({'hpcuyn': '3', 'hpccuyn': '4'})
        facilities = {r['facility_type']: r for r in result['facilities']}
        self.assertEqual(facilities['neurological_icu_bed']['reported_count'], 3)
        self.assertEqual(facilities['neurological_icu_bed']['source_field'], 'hpcuyn')
        self.assertEqual(facilities['thoracic_icu_bed']['reported_count'], 4)
        self.assertEqual(facilities['thoracic_icu_bed']['source_field'], 'hpccuyn')
        self.assertNotIn('pediatric_icu_bed', facilities)
        self.assertNotIn('cardiac_icu_bed', facilities)


class EquipmentNormalizationTests(unittest.TestCase):
    def test_only_exact_verified_fields_and_y_n_values_are_normalized(self):
        result = normalize_equipment_beds({'hvctayn': 'Y ', 'hvmriayn': 'N',
                                          'hvincuayn': 'N1', 'hvventisoayn': 'N1',
                                          'HVANGIOAYN': 'Y', 'hvidate': '20261011123000'})
        equipment = {r['equipment_type']: r for r in result['equipment']}
        self.assertIs(equipment['ct']['reported_available'], True)
        self.assertIs(equipment['mri']['reported_available'], False)
        self.assertIsNone(equipment['incubator']['reported_available'])
        self.assertIsNone(equipment['neonatal_ventilator']['reported_available'])
        self.assertIsNone(equipment['angiography']['reported_available'])
        self.assertIsInstance(result['source_observed_at'], int)

    def test_unknown_equipment_values_never_become_false(self):
        for value in ('', '정보미제공', 'N1', '불가능', 'y', 'n', '0', '1', None):
            with self.subTest(value=value):
                result = normalize_equipment_beds({'hvctayn': value})
                equipment = result['equipment'][0]
                self.assertIsNone(equipment['reported_available'])
                self.assertEqual(equipment['status'], 'unknown')

    def test_missing_date_is_unknown_and_source_input_is_immutable(self):
        raw = {'hvctayn': 'Y', 'hvventiayn': 'N'}
        before = copy.deepcopy(raw)
        result = normalize_equipment_beds(raw)
        self.assertIsNone(result['source_observed_at'])
        self.assertEqual(raw, before)


class SevereNormalizationTests(unittest.TestCase):
    def test_mixed_case_and_raw_values_are_preserved_without_code_guessing(self):
        result = normalize_severe({'MKioskTy1': 'Y ', 'mkioskty1': '불가능',
                                   'MKioskTy8': '정보미제공', 'MKioskTy1Msg': '체중 2kg 이상',
                                   'hpid': 'A000001'})
        self.assertEqual(result['code_version'], 'unverified')
        self.assertEqual(result['fields'], [
            {'source_field': 'MKioskTy1', 'reported_value': 'Y ', 'status': 'uninterpreted'},
            {'source_field': 'mkioskty1', 'reported_value': '불가능', 'status': 'uninterpreted'},
            {'source_field': 'MKioskTy8', 'reported_value': '정보미제공', 'status': 'uninterpreted'},
        ])
        self.assertEqual(result['messages'], [
            {'source_field': 'MKioskTy1Msg', 'reported_value': '체중 2kg 이상'}])
        self.assertNotIn('capabilities', result)

    def test_normalization_does_not_mutate_input(self):
        raw = {'MKioskTy1': 'N', 'MKioskTy1Msg': '연령 확인 필요'}
        before = copy.deepcopy(raw)
        normalize_severe(raw)
        self.assertEqual(raw, before)


class RequirementComparisonTests(unittest.TestCase):
    now = 2000

    def beds(self, quantity, stamp=1990, status='reported', **metadata):
        data = {'resources': [{'resource_type': 'ed_bed', 'source_field': 'hvec',
                               'reported_available': quantity, 'status': status}],
                'source_observed_at': stamp}
        return {'data': data, 'source_observed_at': stamp, 'fetched_at': self.now, **metadata}

    def evaluate(self, requirements, **observations):
        return evaluate_requirements(requirements, now=self.now, **observations)

    def test_present_department_is_structural_match_but_not_acceptance(self):
        result = self.evaluate({'departments': ['산부인과']},
                               detail=normalize_detail({'dgidIdName': '내과,산부인과'}))
        self.assertEqual(result['status'], MATCH)
        self.assertEqual(result['evidence'][0]['current_team_availability'], 'unknown')
        self.assertTrue(result['patient_acceptance_required'])

    def test_missing_department_does_not_mean_unavailable(self):
        for detail in (None, normalize_detail({}), normalize_detail({'dgidIdName': '내과'})):
            with self.subTest(detail=detail):
                result = self.evaluate({'departments': ['산부인과']}, detail=detail)
                self.assertEqual(result['status'], NEEDS_CONFIRMATION)
                self.assertEqual(result['excluded'], [])

    def test_unverified_alias_is_not_mapped_to_department(self):
        result = self.evaluate({'required_capabilities': ['pediatrics']},
                               detail=normalize_detail({'dgidIdName': '소아청소년과'}))
        self.assertEqual(result['status'], NEEDS_CONFIRMATION)

    def test_stale_department_list_needs_confirmation(self):
        detail = {'data': normalize_detail({'dgidIdName': '산부인과'}), 'stale': True}
        result = self.evaluate({'departments': ['산부인과']}, detail=detail)
        self.assertEqual(result['status'], NEEDS_CONFIRMATION)
        self.assertTrue(result['stale'])

    def test_positive_fresh_resource_matches_observation_only(self):
        result = self.evaluate({'resource_demands': {'ed_bed': 1}}, beds=self.beds(2))
        self.assertEqual(result['status'], MATCH)
        self.assertTrue(result['patient_acceptance_required'])
        self.assertEqual(result['evidence'][0]['source_field'], 'hvec')

    def test_zero_resource_means_temporary_unavailability_not_permanent_lack(self):
        result = self.evaluate({'resources': {'ed_bed': 1}}, beds=self.beds(0))
        self.assertEqual(result['status'], NEEDS_CONFIRMATION)
        self.assertEqual(result['unknown'][0]['reason'], 'temporary_unavailable')
        self.assertEqual(result['excluded'], [])
        self.assertNotIn('capabilities', result)

    def test_public_zero_can_only_exclude_with_explicit_experiment_opt_in(self):
        result = evaluate_requirements({'resources': {'ed_bed': 1}}, beds=self.beds(0),
                                       now=self.now, exclude_public_unavailability=True)
        self.assertEqual(result['status'], EXCLUDED)
        self.assertEqual(result['excluded'][0]['reason'], 'temporary_unavailable')
        self.assertEqual(result['excluded'][0]['exclusion_scope'], 'current_inquiry_round')

    def test_quantity_smaller_than_demand_is_temporary_unavailability(self):
        result = self.evaluate({'resources': {'ed_bed': 2}}, beds=self.beds(1))
        self.assertEqual(result['status'], NEEDS_CONFIRMATION)
        self.assertEqual(result['evidence'][0]['required_quantity'], 2)

    def test_stale_zero_never_hard_excludes(self):
        for snapshot in (self.beds(0, stamp=1000), self.beds(0, stale=True)):
            with self.subTest(snapshot=snapshot):
                result = self.evaluate({'resources': {'ed_bed': 1}}, beds=snapshot)
                self.assertEqual(result['status'], NEEDS_CONFIRMATION)
                self.assertTrue(result['stale'])
                self.assertEqual(result['excluded'], [])

    def test_new_fetch_does_not_refresh_old_observed_at(self):
        result = self.evaluate({'resources': {'ed_bed': 1}}, beds=self.beds(0, stamp=1000))
        self.assertEqual(result['status'], NEEDS_CONFIRMATION)
        self.assertEqual(result['evidence'][0]['freshness'], 'stale')

    def test_missing_and_future_observation_time_cannot_exclude(self):
        for stamp in (None, '1990', True, 2400, float('nan'), float('inf')):
            with self.subTest(stamp=stamp):
                result = self.evaluate({'resources': {'ed_bed': 1}}, beds=self.beds(0, stamp=stamp))
                self.assertEqual(result['status'], NEEDS_CONFIRMATION)
                self.assertEqual(result['evidence'][0]['reason'], 'observation_time_unknown')

    def test_missing_negative_noninteger_or_unknown_resources_never_equal_zero(self):
        for quantity in (None, -1, '0', False):
            with self.subTest(quantity=quantity):
                result = self.evaluate({'resources': {'ed_bed': 1}}, beds=self.beds(quantity))
                self.assertEqual(result['status'], NEEDS_CONFIRMATION)
        result = self.evaluate({'resources': {'ed_bed': 1}}, beds=self.beds(0, status='unknown'))
        self.assertEqual(result['status'], NEEDS_CONFIRMATION)

    def test_absent_resource_type_needs_confirmation(self):
        result = self.evaluate({'resources': {'nicu_bed': 1}}, beds=self.beds(20))
        self.assertEqual(result['status'], NEEDS_CONFIRMATION)

    def test_freshness_boundary_is_inclusive(self):
        result = self.evaluate({'resources': {'ed_bed': 1}}, beds=self.beds(1, stamp=1400))
        self.assertEqual(result['status'], MATCH)

    def test_pure_normalized_beds_are_supported(self):
        result = self.evaluate({'resources': {'ed_bed': 1}}, beds=self.beds(1)['data'])
        self.assertEqual(result['status'], MATCH)

    def test_unverified_severe_codes_are_not_inferred_even_if_y_or_n(self):
        for value in ('Y', 'N', '불가능', '정보미제공'):
            with self.subTest(value=value):
                result = self.evaluate({'severe_fields': ['MKioskTy8']},
                                       severe=normalize_severe({'MKioskTy8': value}))
                self.assertEqual(result['status'], NEEDS_CONFIRMATION)
                self.assertEqual(result['evidence'][0]['reported_value'], value)

    def test_no_requirements_does_not_assert_acceptance(self):
        self.assertEqual(self.evaluate({})['status'], NEEDS_CONFIRMATION)

    def test_unknown_equipment_not_inferred_from_raw_input(self):
        result = self.evaluate({'equipment': ['ct']}, detail={'hvctayn': 'Y'})
        self.assertEqual(result['status'], NEEDS_CONFIRMATION)

    def equipment_beds(self, available, stamp=1990, status='reported'):
        return {'data': {'equipment': [{'equipment_type': 'ct', 'source_field': 'hvctayn',
                                       'reported_available': available, 'status': status}]},
                'source_observed_at': stamp, 'fetched_at': self.now}

    def test_fresh_equipment_y_matches_public_observation(self):
        result = self.evaluate({'equipment': ['ct']}, beds=self.equipment_beds(True))
        self.assertEqual(result['status'], MATCH)
        self.assertTrue(result['patient_acceptance_required'])

    def test_fresh_equipment_n_keeps_hospital_for_confirmation_by_default(self):
        result = self.evaluate({'equipment': ['ct']}, beds=self.equipment_beds(False))
        self.assertEqual(result['status'], NEEDS_CONFIRMATION)
        self.assertEqual(result['unknown'][0]['reason'], 'temporary_unavailable')
        self.assertEqual(result['excluded'], [])

    def test_fresh_equipment_n_can_be_excluded_with_explicit_experiment_opt_in(self):
        result = evaluate_requirements({'equipment': ['ct']}, beds=self.equipment_beds(False),
                                       now=self.now, exclude_public_unavailability=True)
        self.assertEqual(result['status'], EXCLUDED)
        self.assertEqual(result['excluded'][0]['exclusion_scope'], 'current_inquiry_round')

    def test_stale_or_unknown_equipment_never_excludes(self):
        for snapshot in (self.equipment_beds(False, stamp=1000), self.equipment_beds(False, stamp=None),
                         self.equipment_beds(None), self.equipment_beds(False, status='unknown'),
                         self.equipment_beds(0)):
            with self.subTest(snapshot=snapshot):
                result = self.evaluate({'equipment': ['ct']}, beds=snapshot)
                self.assertEqual(result['status'], NEEDS_CONFIRMATION)

    def test_detail_equipment_is_not_used_as_current_availability(self):
        result = self.evaluate({'equipment': ['ct']}, detail=self.equipment_beds(True))
        self.assertEqual(result['status'], NEEDS_CONFIRMATION)

    def test_extended_resource_demands_are_included_and_duplicate_demand_uses_maximum(self):
        result = self.evaluate({'resource_demands': {'ed_bed': 1},
                                'extended_resource_demands': {'ed_bed': 2}}, beds=self.beds(1))
        self.assertEqual(result['status'], NEEDS_CONFIRMATION)
        self.assertEqual(result['evidence'][0]['required_quantity'], 2)
        result = self.evaluate({'resource_demands': {'ed_bed': 1},
                                'extended_resource_demands': {'nicu_bed': 1}}, beds=self.beds(1))
        self.assertEqual(result['status'], NEEDS_CONFIRMATION)

    def test_required_service_cannot_be_satisfied_by_department_only(self):
        result = self.evaluate({'departments': ['산부인과'], 'required_services': ['high_risk_delivery']},
                               detail=normalize_detail({'dgidIdName': '산부인과'}))
        self.assertEqual(result['status'], NEEDS_CONFIRMATION)
        self.assertEqual(result['unknown'][0]['kind'], 'service')

    def test_human_confirmation_flag_or_items_prevent_automatic_match(self):
        for human in (True, ['당직 전문의 가용', '마취팀 가용']):
            with self.subTest(human=human):
                result = self.evaluate({'departments': ['산부인과'], 'human_confirmation': human},
                                       detail=normalize_detail({'dgidIdName': '산부인과'}))
                self.assertEqual(result['status'], NEEDS_CONFIRMATION)
                self.assertTrue(result['patient_acceptance_required'])

    def test_invalid_list_requirements_cannot_be_silently_iterated_as_characters(self):
        for name in ('departments', 'equipment', 'severe_fields', 'required_services'):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    self.evaluate({name: 'not-a-list'})

    def test_match_plus_unknown_is_needs_confirmation(self):
        result = self.evaluate({'departments': ['산부인과'], 'resources': {'nicu_bed': 1}},
                               detail=normalize_detail({'dgidIdName': '산부인과'}), beds=self.beds(1))
        self.assertEqual(result['status'], NEEDS_CONFIRMATION)

    def test_explicit_current_round_exclusion_is_not_overridden_by_unknown_department(self):
        result = evaluate_requirements({'departments': ['산부인과'], 'resources': {'ed_bed': 1}},
                                       beds=self.beds(0), now=self.now,
                                       exclude_public_unavailability=True)
        self.assertEqual(result['status'], EXCLUDED)
        self.assertEqual(len(result['unknown']), 1)
        self.assertEqual(len(result['excluded']), 1)

    def test_explicit_department_evidence_maps_only_exact_catalog_names(self):
        requirements = {'required_capabilities': ['응급의학'], 'department_evidence': {
            'match_mode': 'all_capabilities_exact_name', 'groups': [
                {'capability': '응급의학', 'any_of_exact_names': ['응급의학과']}]}}
        result = self.evaluate(requirements, detail=normalize_detail({'dgidIdName': '응급의학과,내과'}))
        self.assertEqual(result['status'], MATCH)
        self.assertEqual(result['evidence'][0]['requirement'], '응급의학')
        self.assertEqual(result['evidence'][0]['matched_department_names'], ['응급의학과'])

    def test_internal_medicine_does_not_satisfy_cardiology_alias_group(self):
        requirements = {'required_capabilities': ['심장내과'], 'department_evidence': {
            'groups': [{'capability': '심장내과', 'any_of_exact_names': ['심장내과', '순환기내과']}]}}
        for name in ('내과', '심장내과전문센터', '순환기내과진료'):
            with self.subTest(name=name):
                result = self.evaluate(requirements, detail=normalize_detail({'dgidIdName': name}))
                self.assertEqual(result['status'], NEEDS_CONFIRMATION)
        result = self.evaluate(requirements, detail=normalize_detail({'dgidIdName': '내과,순환기내과'}))
        self.assertEqual(result['status'], MATCH)

    def test_all_department_groups_must_match_without_substring_guessing(self):
        requirements = {'required_capabilities': ['산부인과', '소아청소년과'], 'department_evidence': {
            'groups': [{'capability': '산부인과', 'any_of_exact_names': ['산부인과']},
                       {'capability': '소아청소년과', 'any_of_exact_names': ['소아청소년과']}]}}
        result = self.evaluate(requirements, detail=normalize_detail({'dgidIdName': '산부인과'}))
        self.assertEqual(result['status'], NEEDS_CONFIRMATION)
        self.assertEqual(len(result['unknown']), 1)

    def test_explicit_public_exclusion_opt_in_does_not_exclude_stale_observations(self):
        result = evaluate_requirements({'resources': {'ed_bed': 1}}, beds=self.beds(0, stamp=1000),
                                       now=self.now, exclude_public_unavailability=True)
        self.assertEqual(result['status'], NEEDS_CONFIRMATION)
        self.assertEqual(result['excluded'], [])

    def delivery_beds(self, raw, stamp=1990):
        from server.public_data import normalize_beds
        return {'data': normalize_beds({'hv42': raw}), 'source_observed_at': stamp,
                'fetched_at': self.now}

    def test_delivery_y_is_service_evidence_while_required_quantity_stays_unknown(self):
        result = self.evaluate({'extended_resource_demands': {'delivery_room': 1}},
                               beds=self.delivery_beds('Y'))
        self.assertEqual(result['status'], NEEDS_CONFIRMATION)
        self.assertIsNone(result['evidence'][0]['reported_available'])
        self.assertEqual(result['evidence'][0]['reason'], 'available_quantity_unknown')
        auxiliary = result['service_availability_evidence'][0]
        self.assertEqual(auxiliary['status'], MATCH)
        self.assertIs(auxiliary['reported_available'], True)
        self.assertEqual(auxiliary['source_field'], 'hv42')
        self.assertEqual(auxiliary['interpretation'], 'public_service_flag_not_quantity')
        self.assertTrue(auxiliary['patient_acceptance_required'])

    def test_delivery_n_keeps_candidate_for_confirmation_and_never_creates_zero_slots(self):
        result = self.evaluate({'resources': {'delivery_room': 1}}, beds=self.delivery_beds('N'))
        self.assertEqual(result['status'], NEEDS_CONFIRMATION)
        self.assertIsNone(result['evidence'][0]['reported_available'])
        auxiliary = result['service_availability_evidence'][0]
        self.assertEqual(auxiliary['status'], NEEDS_CONFIRMATION)
        self.assertIs(auxiliary['reported_available'], False)
        self.assertEqual(auxiliary['reason'], 'temporary_unavailable')
        self.assertEqual(result['excluded'], [])

    def test_delivery_numeric_quantity_is_not_overridden_by_unknown_auxiliary_flag(self):
        result = self.evaluate({'resources': {'delivery_room': 1}}, beds=self.delivery_beds('2'))
        self.assertEqual(result['status'], MATCH)
        self.assertEqual(result['evidence'][0]['reported_available'], 2)
        self.assertIsNone(result['service_availability_evidence'][0]['reported_available'])
        self.assertEqual(result['service_availability_evidence'][0]['status'], NEEDS_CONFIRMATION)

    def test_delivery_missing_and_special_service_values_remain_unknown(self):
        for raw in (None, '', 'N1', 'Y1', '정보미제공', 'y', 'n'):
            with self.subTest(raw=raw):
                result = self.evaluate({'resources': {'delivery_room': 1}}, beds=self.delivery_beds(raw))
                self.assertEqual(result['status'], NEEDS_CONFIRMATION)
                self.assertIsNone(result['service_availability_evidence'][0]['reported_available'])

    def test_delivery_service_flag_respects_unknown_stale_and_future_observation_times(self):
        for stamp, reason in ((None, 'observation_time_unknown'), (1000, 'stale'),
                              (2400, 'observation_time_unknown')):
            with self.subTest(stamp=stamp):
                result = self.evaluate({'resources': {'delivery_room': 1}},
                                       beds=self.delivery_beds('Y', stamp=stamp))
                self.assertEqual(result['status'], NEEDS_CONFIRMATION)
                auxiliary = result['service_availability_evidence'][0]
                self.assertEqual(auxiliary['status'], NEEDS_CONFIRMATION)
                self.assertEqual(auxiliary['reason'], reason)

    def test_delivery_service_y_does_not_prove_high_risk_delivery_or_neonatal_care(self):
        result = self.evaluate({'required_services': ['high_risk_delivery', 'neonatal_intensive_care']},
                               beds=self.delivery_beds('Y'))
        self.assertEqual(result['status'], NEEDS_CONFIRMATION)
        self.assertTrue(all(row['status'] == NEEDS_CONFIRMATION for row in result['evidence']))
        self.assertEqual(result['service_availability_evidence'], [])

    def test_exact_delivery_service_requirement_can_read_flag_without_claiming_quantity(self):
        result = self.evaluate({'required_services': ['delivery_room']}, beds=self.delivery_beds('Y'))
        self.assertEqual(result['status'], MATCH)
        self.assertTrue(result['patient_acceptance_required'])
        self.assertEqual(result['evidence'][0]['kind'], 'service')
        self.assertEqual(result['evidence'][0]['interpretation'], 'public_service_flag_not_quantity')

    def test_delivery_service_n_is_never_excluded_without_explicit_opt_in(self):
        result = self.evaluate({'required_services': ['delivery_room']}, beds=self.delivery_beds('N'))
        self.assertEqual(result['status'], NEEDS_CONFIRMATION)
        result = evaluate_requirements({'required_services': ['delivery_room']},
                                       beds=self.delivery_beds('N'), now=self.now,
                                       exclude_public_unavailability=True)
        self.assertEqual(result['status'], EXCLUDED)
        self.assertEqual(result['excluded'][0]['exclusion_scope'], 'current_inquiry_round')

    def test_stale_delivery_n_never_excludes_even_with_experiment_opt_in(self):
        result = evaluate_requirements({'required_services': ['delivery_room']},
                                       beds=self.delivery_beds('N', stamp=1000), now=self.now,
                                       exclude_public_unavailability=True)
        self.assertEqual(result['status'], NEEDS_CONFIRMATION)
        self.assertEqual(result['excluded'], [])

    def test_all_arguments_remain_immutable(self):
        requirements = {'required_capabilities': ['산부인과'], 'resource_demands': {'ed_bed': 1}}
        detail = normalize_detail({'dgidIdName': '산부인과'})
        beds = self.beds(1)
        severe = normalize_severe({'MKioskTy8': 'Y'})
        before = copy.deepcopy((requirements, detail, beds, severe))
        self.evaluate(requirements, detail=detail, beds=beds, severe=severe)
        self.assertEqual((requirements, detail, beds, severe), before)

    def test_invalid_resource_requirements_fail_instead_of_being_ignored(self):
        for quantity in (-1, 1.5, '1', True):
            with self.subTest(quantity=quantity):
                with self.assertRaises(ValueError):
                    self.evaluate({'resources': {'ed_bed': quantity}}, beds=self.beds(20))

    def test_zero_resource_requirement_needs_no_capacity(self):
        result = self.evaluate({'departments': ['내과'], 'resources': {'ed_bed': 0}},
                               detail=normalize_detail({'dgidIdName': '내과'}))
        self.assertEqual(result['status'], MATCH)
        self.assertEqual(len(result['evidence']), 1)


class ApiEvidenceConnectionTests(unittest.TestCase):
    now = 2000

    def snapshot(self, data, **meta):
        return {'data': data, 'fetched_at': 1999, 'source_observed_at': None, **meta}

    def evaluate(self, requirements, observations, **kwargs):
        return evaluate_requirements(requirements, observations=observations, now=self.now, **kwargs)

    def registered(self, endpoint, rows):
        from server.hira_data import normalize_records
        return self.snapshot(normalize_records(endpoint, rows))

    def test_hira_exact_department_fallback_connects_registered_doctor_and_specialist_evidence(self):
        obs = {
            'nmc_hospital_detail': normalize_detail({}),
            'hira_departments': self.registered('hira_departments', [
                {'dgsbjtCd': '10', 'dgsbjtCdNm': '산부인과', 'dgsbjtPrSdrCnt': '3'}]),
            'hira_specialists': self.registered('hira_specialists', [
                {'dgsbjtCd': '10', 'dgsbjtCdNm': '산부인과', 'dtlSdrCnt': '2'}]),
        }
        result = self.evaluate({'departments': ['산부인과']}, obs)
        self.assertEqual(result['status'], MATCH)
        row = result['evidence'][0]
        self.assertEqual(row['endpoint'], 'hira_departments')
        self.assertEqual(row['source_field'], 'dgsbjtCdNm')
        self.assertEqual(row['current_team_availability'], 'unknown')
        self.assertEqual({r['kind'] for r in row['registered_evidence']},
                         {'registered_department', 'registered_specialists'})
        self.assertEqual([r['reported_count'] for r in row['registered_evidence']], [3, 2])
        self.assertTrue(result['patient_acceptance_required'])

    def test_hira_internal_medicine_does_not_satisfy_cardiac_profile(self):
        obs = {'hira_departments': self.registered('hira_departments', [
            {'dgsbjtCd': '01', 'dgsbjtCdNm': '내과', 'dgsbjtPrSdrCnt': '20'}])}
        result = self.evaluate({'departments': ['심장내과']}, obs)
        self.assertEqual(result['status'], NEEDS_CONFIRMATION)
        self.assertEqual(result['evidence'][0]['matched_department_names'], [])

    def test_registered_specialists_do_not_satisfy_current_special_team_demand(self):
        obs = {'hira_departments': self.registered('hira_departments', [
            {'dgsbjtCdNm': '산부인과', 'dgsbjtPrSdrCnt': '10'}]),
               'hira_specialists': self.registered('hira_specialists', [
                   {'dgsbjtCdNm': '산부인과', 'dtlSdrCnt': '8'}])}
        result = self.evaluate({'departments': ['산부인과'], 'resources': {'special_team': 1}}, obs)
        self.assertEqual(result['status'], NEEDS_CONFIRMATION)
        team = next(row for row in result['evidence'] if row['kind'] == 'resource')
        self.assertIsNone(team['reported_available'])
        self.assertEqual(team['registered_evidence'][0]['reported_count'], 8)
        self.assertFalse(team['registered_counts_are_current_capacity'])

    def test_registered_ct_is_connected_without_inventing_current_availability(self):
        obs = {'hira_equipment': self.registered('hira_equipment', [
            {'oftCd': 'B108', 'oftCdNm': 'CT', 'oftCnt': '4'}])}
        result = self.evaluate({'equipment': ['ct']}, obs)
        self.assertEqual(result['status'], NEEDS_CONFIRMATION)
        current = result['current_evidence'][0]
        self.assertIsNone(current['reported_available'])
        registry = current['registered_evidence'][0]
        self.assertEqual(registry['reported_count'], 4)
        self.assertEqual(registry['source_field'], 'oftCnt')
        self.assertEqual(registry['equipment_type'], 'ct')
        self.assertEqual(registry['endpoint'], 'hira_equipment')
        self.assertEqual(registry['fetched_at'], 1999)
        self.assertIsNone(registry['source_observed_at'])
        self.assertEqual(registry['freshness'], 'unknown')

    def test_registered_general_ventilator_does_not_become_neonatal_ventilator(self):
        obs = {'hira_equipment': self.registered('hira_equipment', [
            {'oftCd': 'D202', 'oftCdNm': '인공호흡기', 'oftCnt': '20'}])}
        result = self.evaluate({'equipment': ['neonatal_ventilator']}, obs)
        self.assertEqual(result['status'], NEEDS_CONFIRMATION)
        self.assertEqual(result['evidence'][0]['registered_evidence'], [])

    def test_unverified_equipment_names_and_cone_beam_ct_are_not_mapped_to_ct(self):
        for code, name in (('B109', '콘빔CT'), ('UNKNOWN', 'CT전문장비'), ('B999', 'CT')):
            with self.subTest(code=code):
                obs = {'hira_equipment': self.registered('hira_equipment', [
                    {'oftCd': code, 'oftCdNm': name, 'oftCnt': '1'}])}
                result = self.evaluate({'equipment': ['ct']}, obs)
                self.assertIsNone(result['registered_evidence'][0]['equipment_type'])
                self.assertEqual(result['status'], NEEDS_CONFIRMATION)

    def test_registered_nicu_or_delivery_room_counts_do_not_satisfy_available_slots(self):
        obs = {'hira_facilities': self.registered('hira_facilities', [
            {'partumCnt': '4', 'nbySprmCnt': '10'}])}
        result = self.evaluate({'resources': {'delivery_room': 1, 'nicu_bed': 1}}, obs)
        self.assertEqual(result['status'], NEEDS_CONFIRMATION)
        self.assertEqual(result['excluded'], [])
        rows = {row['requirement']: row for row in result['evidence']}
        for kind, number in (('delivery_room', 4), ('nicu_bed', 10)):
            self.assertIsNone(rows[kind]['reported_available'])
            self.assertEqual(rows[kind]['registered_evidence'][0]['reported_count'], number)

    def test_nmc_and_hira_facility_counts_stay_separate_reference_observations(self):
        obs = {'nmc_hospital_detail': normalize_detail({'hpnicuyn': '8'}),
               'hira_facilities': self.registered('hira_facilities', [{'nbySprmCnt': '10'}])}
        result = self.evaluate({'resources': {'nicu_bed': 1}}, obs)
        evidence = result['evidence'][0]['registered_evidence']
        self.assertEqual({(row['endpoint'], row['reported_count']) for row in evidence},
                         {('nmc_hospital_detail', 8), ('hira_facilities', 10)})
        self.assertIsNone(result['evidence'][0]['reported_available'])

    def test_empty_registered_results_and_registered_zero_do_not_exclude_hospital(self):
        for rows in ([], [{'nbySprmCnt': '0'}], [{'nbySprmCnt': 'N1'}]):
            with self.subTest(rows=rows):
                obs = {'hira_facilities': self.registered('hira_facilities', rows)}
                result = self.evaluate({'resources': {'nicu_bed': 1}}, obs,
                                       exclude_public_unavailability=True)
                self.assertEqual(result['status'], NEEDS_CONFIRMATION)
                self.assertEqual(result['excluded'], [])

    def test_stale_hira_department_is_not_structural_match(self):
        registered = self.registered('hira_departments', [{'dgsbjtCdNm': '산부인과'}])
        registered['stale'] = True
        result = self.evaluate({'departments': ['산부인과']}, {'hira_departments': registered})
        self.assertEqual(result['status'], NEEDS_CONFIRMATION)
        self.assertEqual(result['evidence'][0]['reason'], 'stale')

    def test_new_fetch_does_not_make_registered_equipment_current(self):
        obs = {'hira_equipment': self.registered('hira_equipment', [
            {'oftCd': 'B301', 'oftCdNm': 'MRI', 'oftCnt': '1'}])}
        obs['hira_equipment']['source_observed_at'] = 1990
        result = self.evaluate({'equipment': ['mri']}, obs)
        self.assertEqual(result['status'], NEEDS_CONFIRMATION)
        self.assertIsNone(result['evidence'][0]['reported_available'])

    def test_legacy_positional_observation_overrides_mapping_without_changing_signature(self):
        requirements = {'departments': ['산부인과']}
        result = evaluate_requirements(requirements, normalize_detail({'dgidIdName': '산부인과'}),
                                       None, None, now=self.now,
                                       observations={'nmc_hospital_detail': normalize_detail({})})
        self.assertEqual(result['status'], MATCH)

    def test_fresh_current_value_stays_distinct_from_registered_count(self):
        obs = {'hira_facilities': self.registered('hira_facilities', [{'nbySprmCnt': '20'}]),
               'nmc_rltm_beds': self.snapshot({'resources': [
                   {'resource_type': 'nicu_bed', 'source_field': 'hvncc',
                    'reported_available': 2, 'status': 'reported'}]}, source_observed_at=1990)}
        result = self.evaluate({'resources': {'nicu_bed': 3}}, obs)
        self.assertEqual(result['status'], NEEDS_CONFIRMATION)
        row = result['evidence'][0]
        self.assertEqual(row['reported_available'], 2)
        self.assertEqual(row['registered_evidence'][0]['reported_count'], 20)
        self.assertEqual(row['reason'], 'temporary_unavailable')

    def test_messages_and_operating_details_are_context_not_automatic_acceptance(self):
        message = {'message': '산과 수용 가능', 'disease_code': 'X', 'display_state': 'blocked',
                   'starts_at': 1900, 'ends_at': 2100, 'source_fields': {'message': 'symBlkMsg'}}
        operation = {'emergency_department': {'night': {'reported_operating': True}},
                     'emergency_contacts': {'night': ['000-000-0000']}}
        obs = {'nmc_acceptance_messages': self.snapshot({'messages': [message]}),
               'hira_operating_details': self.snapshot({'operating_details': [operation]})}
        result = self.evaluate({'required_services': ['high_risk_delivery']}, obs)
        self.assertEqual(result['status'], NEEDS_CONFIRMATION)
        self.assertEqual(result['excluded'], [])
        self.assertEqual(len(result['operational_context']), 2)
        context = next(row for row in result['operational_context'] if row['kind'] == 'acceptance_message')
        self.assertEqual(context['message'], message['message'])
        self.assertEqual(context['freshness'], 'unknown')
        self.assertIsNone(context['source_observed_at'])
        self.assertEqual(context['interpretation'], 'context_only_requires_confirmation')

    def test_designation_and_nursing_grade_do_not_imply_clinical_team_or_personnel_count(self):
        obs = {'hira_designated_specialties': self.snapshot({'designated_specialties': [
            {'code': 'X', 'name': '산부인과', 'status': 'registered_uninterpreted'}]}),
               'hira_nursing_grades': self.snapshot({'nursing_grades': [
                   {'code': 'Y', 'name': '일반병동', 'reported_grade': '1'}]})}
        result = self.evaluate({'resources': {'special_team': 1}}, obs)
        self.assertEqual(result['status'], NEEDS_CONFIRMATION)
        nursing = next(row for row in result['registered_evidence'] if row['kind'] == 'registered_nursing_grade')
        self.assertEqual(nursing['reported_grade'], '1')
        self.assertNotIn('reported_count', nursing)
        self.assertEqual(nursing['current_availability'], 'unknown')

    def test_conflicting_registered_equipment_code_name_requires_confirmation(self):
        obs = {'hira_equipment': self.registered('hira_equipment', [
            {'oftCd': 'B108', 'oftCdNm': 'MRI', 'oftCnt': '1'}]),
               'nmc_rltm_beds': self.snapshot({'equipment': [
                   {'equipment_type': 'ct', 'source_field': 'hvctayn', 'reported_available': True,
                    'status': 'reported'}]}, source_observed_at=1990)}
        result = self.evaluate({'equipment': ['ct']}, obs)
        self.assertEqual(result['status'], NEEDS_CONFIRMATION)
        self.assertEqual(result['evidence'][0]['reason'], 'conflicting_registered_records')

    def test_conflicting_current_resource_duplicates_do_not_select_last_value(self):
        obs = {'nmc_rltm_beds': self.snapshot({'resources': [
            {'resource_type': 'nicu_bed', 'reported_available': 1, 'status': 'reported'},
            {'resource_type': 'nicu_bed', 'reported_available': 0, 'status': 'reported'}]},
            source_observed_at=1990)}
        result = self.evaluate({'resources': {'nicu_bed': 1}}, obs, exclude_public_unavailability=True)
        self.assertEqual(result['status'], NEEDS_CONFIRMATION)
        self.assertEqual(result['evidence'][0]['reason'], 'conflicting_public_observations')
        self.assertEqual(result['excluded'], [])

    def test_conflicting_registered_department_rows_are_not_structural_match(self):
        obs = {'hira_departments': self.registered('hira_departments', [
            {'dgsbjtCd': '10', 'dgsbjtCdNm': '산부인과', 'dgsbjtPrSdrCnt': '1'},
            {'dgsbjtCd': '10', 'dgsbjtCdNm': '내과', 'dgsbjtPrSdrCnt': '1'}])}
        result = self.evaluate({'departments': ['산부인과']}, obs)
        self.assertEqual(result['status'], NEEDS_CONFIRMATION)
        self.assertEqual(result['evidence'][0]['reason'], 'conflicting_registered_records')

    def test_api_evidence_inputs_and_nested_outputs_are_detached(self):
        obs = {'hira_facilities': self.registered('hira_facilities', [{'nbySprmCnt': '2'}]),
               'nmc_acceptance_messages': self.snapshot({'messages': [{'source_fields': {'a': 'b'}}]})}
        requirements = {'resources': {'nicu_bed': 1}}
        before = copy.deepcopy((requirements, obs))
        result = self.evaluate(requirements, obs)
        result['registered_evidence'][0]['reported_count'] = 100
        result['operational_context'][0]['source_fields']['a'] = 'changed'
        self.assertEqual((requirements, obs), before)

    def test_invalid_observation_mapping_is_rejected(self):
        with self.assertRaises(TypeError):
            evaluate_requirements({}, observations=[])


if __name__ == '__main__':
    unittest.main()
