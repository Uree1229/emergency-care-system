"""실 API·키 없이 가상 기관으로 HIRA 오연결 방지를 검증한다."""
import copy
import json
import math
from pathlib import Path
import tempfile
import unittest

from server.hira_matching import EARTH_RADIUS_METERS, identity_fingerprint, load_reviewed_links, match_hospitals


def hospital(**changes):
    value = dict(id='NMC-A001', name='가상 중앙병원', address='경기도 평택시 가상로 12-3',
                 phone='031-000-0100', lat=36.99, lon=127.11, source_ids={'nmc': 'A001'})
    value.update(changes)
    return value


def record(**changes):
    value = dict(ykiho='SYNTHETIC-HIRA-001', yadmNm='가상 중앙병원', addr='경기도 평택시 가상로 12-3',
                 telno='0310000100', XPos='127.11', YPos='36.99')
    value.update(changes)
    return value


def reviewed_pair(left, right, **changes):
    value = dict(hospital_id=left['id'], ykiho=right['ykiho'], reviewed_on='2026-10-11',
                 reason='가상 공식 출처로 별칭·도로명 표기와 동일 기관 확인',
                 source_urls=['https://www.data.go.kr/data/15001698/openapi.do'],
                 nmc_identity_sha256=identity_fingerprint(left, 'nmc'),
                 hira_identity_sha256=identity_fingerprint(right, 'hira'))
    value.update(changes)
    return value


def alias_pair(meters=360):
    return (hospital(name='가상 중앙병원'),
            record(yadmNm='의료법인 가상중앙병원', addr='경기도 평택시 다른 표기길 12-3',
                   YPos=str(36.99 + math.degrees(meters / EARTH_RADIUS_METERS))))


class HiraMatching(unittest.TestCase):
    def test_exact_name_address_and_coordinates_match_without_mutating_inputs(self):
        hospitals, records = [hospital()], [record()]
        before = copy.deepcopy((hospitals, records))
        result = match_hospitals(hospitals, records)
        self.assertEqual((hospitals, records), before)
        self.assertEqual((result['unmatched'], result['ambiguous']), ([], []))
        self.assertEqual(result['matches'][0]['ykiho'], 'SYNTHETIC-HIRA-001')
        self.assertEqual(result['matches'][0]['evidence']['distance_meters'], 0)
        self.assertEqual(result['matches'][0]['evidence']['phone_comparison'], 'same')
        self.assertTrue(result['matches'][0]['evidence']['strict_match'])
        self.assertTrue(result['matches'][0]['evidence']['name_exact_match'])
        self.assertTrue(result['matches'][0]['evidence']['address_exact_match'])
        self.assertTrue(result['matches'][0]['evidence']['distance_within_limit'])

    def test_nfkc_name_punctuation_and_parenthetical_address_are_normalized(self):
        result = match_hospitals([hospital(name='ＡＢＣ · 가상병원',
                                          address='경기도 평택시 가상로 12-3 （가상동, 중앙관(별관)）')],
                                 [record(yadmNm='ABC가상병원')])
        self.assertEqual(len(result['matches']), 1)

    def test_name_only_match_does_not_override_different_address(self):
        result = match_hospitals([hospital()], [record(addr='경기도 안성시 가상로 12-3')])
        self.assertEqual(result['matches'], [])
        self.assertIn('ADDRESS_MISMATCH', result['unmatched'][0]['reasons'])

    def test_trailing_address_separator_before_neighborhood_is_normalized(self):
        result = match_hospitals([hospital()], [record(addr='경기도 평택시 가상로 12-3, (가상동)')])
        self.assertEqual(len(result['matches']), 1)
        self.assertTrue(result['matches'][0]['evidence']['address_exact_match'])

    def test_department_or_partial_hospital_names_are_not_removed(self):
        for name in ('가상 중앙병원 응급의학과', '중앙병원', '가상 중앙병원 평택분원'):
            with self.subTest(name=name):
                result = match_hospitals([hospital()], [record(yadmNm=name)])
                self.assertEqual(result['matches'], [])
                self.assertIn('NO_EXACT_NAME_MATCH', result['unmatched'][0]['reasons'])

    def test_building_number_hyphen_is_not_erased(self):
        result = match_hospitals([hospital()], [record(addr='경기도 평택시 가상로 123')])
        self.assertEqual(result['matches'], [])
        self.assertIn('ADDRESS_MISMATCH', result['unmatched'][0]['reasons'])

    def test_floor_and_unit_outside_parentheses_are_not_erased(self):
        result = match_hospitals([hospital(address='경기도 평택시 가상로 12-3, 2층')],
                                 [record(addr='경기도 평택시 가상로 12-3, 3층')])
        self.assertEqual(result['matches'], [])
        self.assertIn('ADDRESS_MISMATCH', result['unmatched'][0]['reasons'])

    def test_missing_coordinates_require_manual_verification(self):
        for left, right in ((hospital(lat=None), record()), (hospital(), record(XPos=''))):
            with self.subTest(left=left, right=right):
                result = match_hospitals([left], [right])
                self.assertEqual(result['matches'], [])
                self.assertIn('COORDINATES_MISSING_OR_INVALID', result['unmatched'][0]['reasons'])

    def test_invalid_coordinates_are_not_close_enough_by_default(self):
        for changes in ({'XPos': 'NaN'}, {'YPos': 'Infinity'}, {'XPos': True},
                        {'YPos': '91'}, {'XPos': '181'}, {'XPos': '0', 'YPos': '0'}):
            with self.subTest(changes=changes):
                result = match_hospitals([hospital()], [record(**changes)])
                self.assertEqual(result['matches'], [])
                self.assertIn('COORDINATES_MISSING_OR_INVALID', result['unmatched'][0]['reasons'])

    def test_distance_limit_is_300_meters(self):
        for meters, expected in ((299, True), (300, True), (301, False)):
            with self.subTest(meters=meters):
                latitude = 36.99 + math.degrees(meters / EARTH_RADIUS_METERS)
                result = match_hospitals([hospital()], [record(YPos=str(latitude))])
                self.assertEqual(bool(result['matches']), expected)
                if not expected:
                    self.assertIn('DISTANCE_EXCEEDED', result['unmatched'][0]['reasons'])

    def test_same_phone_does_not_replace_required_address_or_coordinates(self):
        for changes in ({'addr': ''}, {'addr': '경기도 평택시 다른로 1'}, {'XPos': ''}):
            with self.subTest(changes=changes):
                result = match_hospitals([hospital()], [record(**changes)])
                self.assertEqual(result['matches'], [])
                self.assertEqual(result['unmatched'][0]['candidates'][0]['evidence']['phone_comparison'], 'same')

    def test_emergency_and_main_phone_difference_is_reported_as_auxiliary_evidence(self):
        result = match_hospitals([hospital()], [record(telno='031-000-0999')])
        self.assertEqual(len(result['matches']), 1)
        self.assertEqual(result['matches'][0]['evidence']['phone_comparison'], 'different')
        self.assertFalse(result['matches'][0]['evidence']['phone_is_identity_requirement'])

    def test_duplicate_hira_identifier_is_ambiguous_even_when_rows_identical(self):
        result = match_hospitals([hospital()], [record(), record()])
        self.assertEqual(result['matches'], [])
        self.assertIn('DUPLICATE_HIRA_ID', result['ambiguous'][0]['reasons'])

    def test_two_distinct_hira_ids_matching_all_fields_are_ambiguous(self):
        result = match_hospitals([hospital()], [record(), record(ykiho='SYNTHETIC-HIRA-002')])
        self.assertEqual(result['matches'], [])
        self.assertIn('MULTIPLE_MATCHING_RECORDS', result['ambiguous'][0]['reasons'])

    def test_one_hira_identifier_cannot_be_assigned_to_two_nmc_hospitals(self):
        result = match_hospitals([hospital(), hospital(id='NMC-A002')], [record()])
        self.assertEqual(result['matches'], [])
        self.assertEqual(len(result['ambiguous']), 2)
        self.assertTrue(all('SHARED_HIRA_ID' in item['reasons'] for item in result['ambiguous']))

    def test_conflicting_existing_hira_mapping_is_not_overwritten(self):
        value = hospital(source_ids={'nmc': 'A001', 'hira': 'EXISTING-OTHER-ID'})
        result = match_hospitals([value], [record()])
        self.assertEqual(result['matches'], [])
        self.assertIn('EXISTING_HIRA_ID_CONFLICT', result['ambiguous'][0]['reasons'])
        self.assertEqual(value['source_ids']['hira'], 'EXISTING-OTHER-ID')

    def test_consistent_existing_hira_id_still_requires_all_identity_checks(self):
        value = hospital(source_ids={'hira': 'SYNTHETIC-HIRA-001'})
        result = match_hospitals([value], [record()])
        self.assertEqual(len(result['matches']), 1)
        result = match_hospitals([value], [record(XPos='')])
        self.assertEqual(result['matches'], [])
        self.assertEqual(len(result['unmatched']), 1)

    def test_existing_hira_owner_blocks_new_assignment_even_without_name_match(self):
        owner = hospital(id='NMC-OWNER', name='별도 기관', source_ids={'hira': 'SYNTHETIC-HIRA-001'})
        result = match_hospitals([hospital(), owner], [record()])
        self.assertEqual(result['matches'], [])
        self.assertEqual(len(result['ambiguous']), 2)
        self.assertTrue(all('SHARED_HIRA_ID' in item['reasons'] for item in result['ambiguous']))

    def test_missing_hira_id_or_malformed_address_never_matches(self):
        for changes in ({'ykiho': ''}, {'addr': '경기도 평택시 가상로 12-3 (가상동'},
                        {'addr': '경기도 평택시 가상로 12-3 )'}):
            with self.subTest(changes=changes):
                result = match_hospitals([hospital()], [record(**changes)])
                self.assertEqual(result['matches'], [])
                self.assertTrue(result['unmatched'])

    def test_duplicate_internal_hospital_id_is_ambiguous(self):
        result = match_hospitals([hospital(), hospital()], [record()])
        self.assertEqual(result['matches'], [])
        self.assertEqual(len(result['ambiguous']), 2)
        self.assertTrue(all('DUPLICATE_HOSPITAL_ID' in item['reasons'] for item in result['ambiguous']))

    def test_empty_inputs_have_stable_three_collection_result(self):
        self.assertEqual(match_hospitals([], []), dict(matches=[], unmatched=[], ambiguous=[]))
        result = match_hospitals([hospital()], [])
        self.assertEqual(len(result['unmatched']), 1)
        self.assertIn('NO_EXACT_NAME_MATCH', result['unmatched'][0]['reasons'])


class ReviewedHiraMatching(unittest.TestCase):
    def test_reviewed_alias_address_and_360_meter_pair_matches_only_pinned_pair(self):
        left, right = alias_pair()
        links = [reviewed_pair(left, right)]
        before = copy.deepcopy((left, right, links))
        self.assertEqual(match_hospitals([left], [right])['matches'], [])
        result = match_hospitals([left], [right], reviewed_links=links)
        self.assertEqual((left, right, links), before)
        self.assertEqual(len(result['matches']), 1)
        evidence = result['matches'][0]['evidence']
        self.assertEqual(evidence['match_method'], 'reviewed_identity_link')
        self.assertFalse(evidence['strict_match'])
        self.assertFalse(evidence['name_exact_match'])
        self.assertFalse(evidence['address_exact_match'])
        self.assertFalse(evidence['distance_within_limit'])
        self.assertEqual(evidence['maximum_distance_meters'], 300)
        self.assertEqual(evidence['reviewed_maximum_distance_meters'], 500)
        self.assertTrue(evidence['identity_verified'])
        self.assertTrue(evidence['phone_is_identity_requirement'])
        self.assertEqual(evidence['reviewed_identity']['source_urls'], links[0]['source_urls'])

    def test_reviewed_link_never_applies_to_another_hospital_or_hira_id(self):
        left, right = alias_pair()
        links = [reviewed_pair(left, right)]
        for changed_left, changed_right in ((dict(left, id='NMC-OTHER'), right),
                                             (left, dict(right, ykiho='SYNTHETIC-OTHER'))):
            with self.subTest(left=changed_left, right=changed_right):
                self.assertEqual(match_hospitals([changed_left], [changed_right], links)['matches'], [])

    def test_every_pinned_identity_field_change_invalidates_reviewed_exception(self):
        left, right = alias_pair()
        links = [reviewed_pair(left, right)]
        changes = [
            ('nmc', 'id', 'NMC-CHANGED'), ('nmc', 'name', left['name'] + ' '),
            ('nmc', 'address', left['address'] + ' '), ('nmc', 'phone', '0310000100'),
            ('nmc', 'lat', left['lat'] + 0.00001), ('nmc', 'lon', left['lon'] + 0.00001),
            ('hira', 'ykiho', 'SYNTHETIC-CHANGED'), ('hira', 'yadmNm', right['yadmNm'] + ' '),
            ('hira', 'addr', right['addr'] + ' '), ('hira', 'telno', '031-000-0100'),
            ('hira', 'XPos', '127.1100'), ('hira', 'YPos', str(float(right['YPos']) + 0.00001)),
        ]
        for provider, key, value in changes:
            with self.subTest(provider=provider, key=key):
                changed_left, changed_right = copy.deepcopy(left), copy.deepcopy(right)
                (changed_left if provider == 'nmc' else changed_right)[key] = value
                result = match_hospitals([changed_left], [changed_right], links)
                self.assertEqual(result['matches'], [])
                self.assertEqual(len(result['unmatched']), 1)

    def test_hash_change_can_still_match_original_strict_conditions(self):
        left, right = hospital(), record()
        links = [reviewed_pair(left, right)]
        right['telno'] = '031-000-0999'
        result = match_hospitals([left], [right], links)
        evidence = result['matches'][0]['evidence']
        self.assertTrue(evidence['strict_match'])
        self.assertEqual(evidence['match_method'], 'exact_name_address_coordinates')
        self.assertIsNone(evidence['reviewed_identity'])
        self.assertFalse(evidence['phone_is_identity_requirement'])

    def test_changed_nonidentity_registration_counts_do_not_invalidate_link(self):
        left, right = alias_pair()
        links = [reviewed_pair(left, right)]
        right.update(drTotCnt='200', mdeptSdrCnt='100')
        result = match_hospitals([left], [right], links)
        self.assertEqual(len(result['matches']), 1)

    def test_reviewed_phone_mismatch_and_either_missing_phone_rejected_even_with_new_pins(self):
        for side, value in (('hira', '0310000999'), ('hira', ''), ('nmc', None)):
            with self.subTest(side=side, value=value):
                left, right = alias_pair()
                (left if side == 'nmc' else right)['phone' if side == 'nmc' else 'telno'] = value
                links = [reviewed_pair(left, right)]
                self.assertEqual(match_hospitals([left], [right], links)['matches'], [])

    def test_reviewed_distance_500_limit_does_not_change_general_limit(self):
        for meters, expected in ((499, True), (500, True), (501, False)):
            with self.subTest(meters=meters):
                left, right = alias_pair(meters)
                result = match_hospitals([left], [right], [reviewed_pair(left, right)])
                self.assertEqual(bool(result['matches']), expected)
        left, right = hospital(), record(YPos=str(36.99 + math.degrees(360 / EARTH_RADIUS_METERS)))
        result = match_hospitals([left], [right])
        self.assertEqual(result['matches'], [])
        self.assertIn('DISTANCE_EXCEEDED', result['unmatched'][0]['reasons'])

    def test_reviewed_invalid_coordinates_or_missing_identity_remain_unmatched(self):
        for provider, field, value in (
            ('nmc', 'lat', None), ('hira', 'XPos', 'NaN'), ('hira', 'YPos', '91'),
            ('nmc', 'name', ''), ('hira', 'yadmNm', ''), ('nmc', 'address', ''),
            ('hira', 'addr', '주소 (잘못된 괄호'),
        ):
            with self.subTest(provider=provider, field=field):
                left, right = alias_pair()
                (left if provider == 'nmc' else right)[field] = value
                self.assertEqual(match_hospitals([left], [right], [reviewed_pair(left, right)])['matches'], [])

    def test_reviewed_duplicate_hira_still_ambiguous(self):
        left, right = alias_pair()
        result = match_hospitals([left], [right, copy.deepcopy(right)], [reviewed_pair(left, right)])
        self.assertEqual(result['matches'], [])
        self.assertIn('DUPLICATE_HIRA_ID', result['ambiguous'][0]['reasons'])

    def test_reviewed_existing_mapping_conflict_still_ambiguous(self):
        left, right = alias_pair()
        left['source_ids']['hira'] = 'EXISTING-OTHER-ID'
        result = match_hospitals([left], [right], [reviewed_pair(left, right)])
        self.assertEqual(result['matches'], [])
        self.assertIn('EXISTING_HIRA_ID_CONFLICT', result['ambiguous'][0]['reasons'])

    def test_reviewed_pair_does_not_override_another_existing_owner(self):
        left, right = alias_pair()
        owner = hospital(id='NMC-OWNER', name='별도 기관', source_ids={'hira': right['ykiho']})
        result = match_hospitals([left, owner], [right], [reviewed_pair(left, right)])
        self.assertEqual(result['matches'], [])
        self.assertEqual(len(result['ambiguous']), 2)
        self.assertTrue(all('SHARED_HIRA_ID' in item['reasons'] for item in result['ambiguous']))

    def test_reviewed_pair_plus_another_strict_candidate_still_ambiguous(self):
        left, right = alias_pair()
        result = match_hospitals([left], [right, record(ykiho='SYNTHETIC-OTHER')], [reviewed_pair(left, right)])
        self.assertEqual(result['matches'], [])
        self.assertIn('MULTIPLE_MATCHING_RECORDS', result['ambiguous'][0]['reasons'])


class ReviewedLinkSchema(unittest.TestCase):
    def test_valid_file_loads_and_missing_file_has_no_exceptions(self):
        left, right = alias_pair()
        links = [reviewed_pair(left, right)]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'links.json'
            self.assertEqual(load_reviewed_links(path), [])
            path.write_text(json.dumps({'links': links}, ensure_ascii=False), encoding='utf-8')
            self.assertEqual(load_reviewed_links(path), links)

    def test_invalid_documents_and_corrupt_json_rejected(self):
        documents = ['{invalid', '[]', 'null', '{}', '{"links":{}}']
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'links.json'
            for content in documents:
                with self.subTest(content=content):
                    path.write_text(content, encoding='utf-8')
                    with self.assertRaises(ValueError):
                        load_reviewed_links(path)

    def test_required_fields_dates_hashes_and_sources_validated_for_direct_calls(self):
        left, right = alias_pair()
        link = reviewed_pair(left, right)
        changes = [
            {'hospital_id': ''}, {'hospital_id': ' NMC-A001'}, {'ykiho': None},
            {'reason': ''}, {'reviewed_on': '2026-02-30'}, {'reviewed_on': '20261011'},
            {'nmc_identity_sha256': '123'}, {'hira_identity_sha256': 'Z' * 64},
            {'source_urls': []}, {'source_urls': ['http://www.data.go.kr/data/15001698/openapi.do']},
            {'source_urls': ['https://']}, {'source_urls': ['https://user:password@example.invalid']},
            {'source_urls': ['https://example.invalid/has space']}, {'source_urls': 'https://example.invalid'},
        ]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                match_hospitals([left], [right], [dict(link, **change)])

    def test_duplicate_pair_and_one_to_many_or_many_to_one_config_rejected(self):
        left, right = alias_pair()
        link = reviewed_pair(left, right)
        for second in (copy.deepcopy(link), dict(link, ykiho='SYNTHETIC-OTHER'), dict(link, hospital_id='NMC-OTHER')):
            with self.subTest(second=second), self.assertRaises(ValueError):
                match_hospitals([left], [right], [link, second])

    def test_identity_fingerprint_rejects_unknown_provider_or_nonobject(self):
        with self.assertRaises(ValueError):
            identity_fingerprint(hospital(), 'unknown')
        with self.assertRaises(ValueError):
            identity_fingerprint([], 'nmc')


if __name__ == '__main__':
    unittest.main()
