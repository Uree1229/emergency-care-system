"""공공 API 수집·저장 회귀 테스트. 실 API/인증키 없이 실행한다.

실행: python -m unittest test_public_data -v
"""
import io
import json
import tempfile
import unittest
import urllib.error
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from xml.sax.saxutils import escape

from server import db, planning, public_data, service, solver_adapter, sync_public
from server.travel import TravelEstimator


NOW = int(datetime(2026, 10, 11, 3, 5, tzinfo=timezone.utc).timestamp())
OBSERVED = NOW - 300
EST = TravelEstimator(use_kakao=False)


def hospital(hpid='A000001', **changes):
    record = dict(hpid=hpid, dutyName='테스트 응급의료기관', dutyAddr='광주광역시 테스트로 1',
                  dutyTel1='062-000-0001', dutyTel3='062-000-0003',
                  wgs84Lat='35.16', wgs84Lon='126.85')
    record.update(changes)
    return record


def beds(hpid='A000001', **changes):
    record = dict(hpid=hpid, hvec='7', hvidate='20261011120000')
    record.update(changes)
    return record


def xml_page(items, total=None, page=1, code='00'):
    entries = ''.join('<item>' + ''.join(f'<{k}>{escape(str(v))}</{k}>' for k, v in item.items())
                      + '</item>' for item in items)
    page_tag = '' if page is None else f'<pageNo>{page}</pageNo>'
    return ('<response><header><resultCode>' + escape(code) + '</resultCode></header>'
            f'<body><items>{entries}</items><totalCount>{len(items) if total is None else total}</totalCount>'
            f'{page_tag}</body></response>').encode('utf-8')


class Response(io.BytesIO):
    pass


class ApiParsing(unittest.TestCase):
    def test_namespaced_xml_and_empty_items(self):
        value = public_data.parse_response(xml_page([hospital()], total=1).replace(
            b'<response>', b'<response xmlns="urn:public-api">'))
        self.assertEqual((value['total_count'], value['page_no']), (1, 1))
        self.assertEqual(value['items'][0]['hpid'], 'A000001')
        self.assertEqual(public_data.parse_response(xml_page([], total=0))['items'], [])

    def test_bad_xml_error_codes_and_counts_are_not_success(self):
        for payload in (b'<broken', xml_page([], code='30'), xml_page([], total=-1),
                        xml_page([], page=0), xml_page([]).replace(b'<totalCount>0</totalCount>', b'')):
            with self.subTest(payload=payload):
                with self.assertRaises(public_data.PublicDataError):
                    public_data.parse_response(payload)

    def test_error_message_does_not_echo_response_or_arbitrary_error_code(self):
        secret = 'PRIVATE/SERVICE+KEY='
        payload = xml_page([], code='bad ' + secret)
        with self.assertRaises(public_data.PublicDataError) as cm:
            public_data.parse_response(payload)
        self.assertNotIn(secret, str(cm.exception))
        self.assertNotIn('bad ', str(cm.exception))

    def test_normalization_preserves_missing_and_negative_as_unknown(self):
        for value in ('', '-1', '미상', '1.5', None):
            normalized = public_data.normalize_beds(beds(hvec=value, hvidate='invalid'))
            with self.subTest(value=value):
                self.assertIsNone(normalized['resources'][0]['reported_available'])
                self.assertEqual(normalized['resources'][0]['status'], 'unknown')
                self.assertIsNone(normalized['source_observed_at'])
        normalized = public_data.normalize_beds(beds(hvec='0'))
        self.assertEqual(normalized['resources'][0]['reported_available'], 0)
        self.assertEqual(normalized['resources'][0]['status'], 'reported')
        self.assertEqual(normalized['source_observed_at'], OBSERVED)

    def test_hospital_coordinates_and_identifier_are_validated(self):
        for changes in ({'wgs84Lat': 'NaN'}, {'wgs84Lon': 'Infinity'}, {'wgs84Lat': '91'},
                        {'wgs84Lat': '0', 'wgs84Lon': '0'}, {'wgs84Lat': ''},
                        {'hpid': '../bad'}, {'dutyName': ''}):
            with self.subTest(changes=changes):
                self.assertIsNone(public_data.normalize_hospital(hospital(**changes)))
        self.assertEqual(public_data.normalize_hospital(hospital())['phone'], '062-000-0003')

    def test_verified_resource_fields_map_to_distinct_public_observations(self):
        normalized = public_data.normalize_beds(beds(hvec='7', hvoc='2', hvicc='4', hvncc='0', hv42='3'))
        self.assertEqual(normalized['resources'][0]['source_field'], 'hvec')
        resources = {r['source_field']: r for r in normalized['resources']}
        self.assertEqual({field: resources[field]['reported_available'] for field in ('hvec', 'hvoc', 'hvicc', 'hvncc', 'hv42')},
                         {'hvec': 7, 'hvoc': 2, 'hvicc': 4, 'hvncc': 0, 'hv42': 3})
        self.assertTrue(all(resources[field]['status'] == 'reported' for field in resources))
        self.assertEqual(len({r['resource_type'] for r in resources.values()}), len(resources))

    def test_missing_or_negative_additional_resource_counts_are_unknown(self):
        resources = {r['source_field']: r for r in public_data.normalize_beds(
            beds(hvoc='-1', hvncc='', hv42='unknown'))['resources']}
        for field in ('hvoc', 'hvncc', 'hv42'):
            with self.subTest(field=field):
                self.assertIsNone(resources[field]['reported_available'])
                self.assertEqual(resources[field]['status'], 'unknown')

    def test_delivery_y_n_are_service_flags_not_resource_quantities(self):
        for raw, expected in (('Y', True), ('N', False), (' Y ', True)):
            with self.subTest(raw=raw):
                normalized = public_data.normalize_beds(beds(hv42=raw))
                quantity = next(r for r in normalized['resources'] if r['resource_type'] == 'delivery_room')
                self.assertIsNone(quantity['reported_available'])
                self.assertEqual(quantity['status'], 'unknown')
                self.assertEqual(normalized['service_availability'], [{
                    'service_type': 'delivery_room', 'source_field': 'hv42',
                    'reported_available': expected, 'status': 'reported'}])

    def test_delivery_numbers_preserve_quantity_without_inventing_service_confirmation(self):
        for raw in ('0', '1', '3'):
            with self.subTest(raw=raw):
                normalized = public_data.normalize_beds(beds(hv42=raw))
                quantity = next(r for r in normalized['resources'] if r['resource_type'] == 'delivery_room')
                self.assertEqual(quantity['reported_available'], int(raw))
                self.assertEqual(quantity['status'], 'reported')
                self.assertIsNone(normalized['service_availability'][0]['reported_available'])
                self.assertEqual(normalized['service_availability'][0]['status'], 'unknown')

    def test_delivery_missing_special_lowercase_values_remain_unknown(self):
        for raw in (None, '', 'N1', 'Y1', '정보미제공', '불가능', 'y', 'n', '-1', '1.5', False):
            with self.subTest(raw=raw):
                normalized = public_data.normalize_beds(beds(hv42=raw))
                quantity = next(r for r in normalized['resources'] if r['resource_type'] == 'delivery_room')
                self.assertIsNone(quantity['reported_available'])
                self.assertIsNone(normalized['service_availability'][0]['reported_available'])
                self.assertEqual(normalized['service_availability'][0]['status'], 'unknown')
        self.assertIsNone(public_data.normalize_beds(beds())['service_availability'][0]['reported_available'])


class ApiClient(unittest.TestCase):
    def client(self, opener, **kw):
        return public_data.NmcClient(key='PRIVATE%2FSERVICE%2BKEY%3D', page_size=1,
                                     district='동구', opener=opener, sleep=lambda _: None, **kw)

    def test_fetches_all_pages_and_decodes_key_once(self):
        calls = []
        def opener(request, timeout):
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query)
            calls.append(query)
            page = int(query['pageNo'][0])
            return Response(xml_page([hospital('A' + str(page))], total=2, page=page))
        result = self.client(opener).fetch_all('nmc_hospital_list', '광주광역시')
        self.assertEqual([r['hpid'] for r in result], ['A1', 'A2'])
        self.assertEqual([c['pageNo'][0] for c in calls], ['1', '2'])
        self.assertEqual(calls[0]['ServiceKey'], ['PRIVATE/SERVICE+KEY='])
        self.assertEqual(calls[0]['Q0'], ['광주광역시'])
        self.assertEqual(calls[0]['Q1'], ['동구'])

    def test_bed_request_includes_required_stage1_and_stage2(self):
        calls = []
        def opener(request, timeout):
            calls.append(urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query))
            return Response(xml_page([], 0))
        self.client(opener).fetch_all('nmc_rltm_beds', '광주광역시')
        self.assertEqual(calls[0]['STAGE1'], ['광주광역시'])
        self.assertEqual(calls[0]['STAGE2'], ['동구'])

    def test_missing_or_blank_district_is_rejected(self):
        for district in (None, '', '   '):
            with self.subTest(district=district):
                def opener(request, timeout):
                    self.fail('STAGE2 없는 병상 조회는 네트워크 호출 전에 거부해야 합니다')
                with self.assertRaises(public_data.PublicDataError):
                    public_data.NmcClient(key='test-key', district=district, opener=opener).fetch_all(
                        'nmc_rltm_beds', '광주광역시')

    def test_rejects_changed_total_wrong_page_and_missing_page(self):
        scenarios = ((xml_page([hospital('A')], 2, 1), xml_page([hospital('B')], 3, 2)),
                     (xml_page([hospital('A')], 2, 1), xml_page([hospital('B')], 2, 1)),
                     (xml_page([hospital('A')], 2, 1), xml_page([], 2, 2)))
        for pages in scenarios:
            with self.subTest(pages=pages):
                sequence = iter(pages)
                with self.assertRaises(public_data.PublicDataError):
                    self.client(lambda request, timeout: Response(next(sequence))).fetch_all(
                        'nmc_hospital_list', '광주광역시')

    def test_http_503_retries_but_403_does_not_and_never_leaks_url(self):
        for status, expected_calls in ((503, 3), (403, 1)):
            calls = []
            def opener(request, timeout):
                calls.append(request.full_url)
                raise urllib.error.HTTPError(request.full_url, status, 'PRIVATE/SERVICE+KEY=', {}, None)
            with self.subTest(status=status):
                with self.assertRaises(public_data.PublicDataError) as cm:
                    self.client(opener).fetch_all('nmc_rltm_beds', '광주광역시')
                self.assertEqual(len(calls), expected_calls)
                self.assertNotIn('PRIVATE', str(cm.exception))
                self.assertNotIn('serviceKey', str(cm.exception))
                self.assertNotIn('https:', str(cm.exception))

    def test_retryable_connection_error_can_recover(self):
        calls = []
        def opener(request, timeout):
            calls.append(request)
            if len(calls) == 1:
                raise urllib.error.URLError('PRIVATE/SERVICE+KEY=')
            return Response(xml_page([], 0, 1))
        self.assertEqual(self.client(opener).fetch_all('nmc_rltm_beds', '광주광역시'), [])
        self.assertEqual(len(calls), 2)

    def test_api_error_is_not_treated_as_empty_success(self):
        with self.assertRaises(public_data.PublicDataError):
            self.client(lambda request, timeout: Response(xml_page([], code='30'))).fetch_all(
                'nmc_rltm_beds', '광주광역시')

    def test_max_pages_never_returns_partial_data(self):
        with self.assertRaises(public_data.PublicDataError):
            self.client(lambda request, timeout: Response(xml_page([hospital()], 10, 1)), max_pages=1).fetch_all(
                'nmc_hospital_list', '광주광역시')

    def test_missing_coordinates_are_enriched_using_matching_hpid_detail(self):
        calls = []
        def opener(request, timeout):
            url = urllib.parse.urlsplit(request.full_url)
            query = urllib.parse.parse_qs(url.query)
            calls.append((url.path.rsplit('/', 1)[-1], query))
            if url.path.endswith('getEgytListInfoInqire'):
                return Response(xml_page([hospital(wgs84Lat='', wgs84Lon='')]))
            detail = xml_page([hospital()]).replace(b'<totalCount>1</totalCount>', b'')
            return Response(detail)
        result = self.client(opener).fetch_hospitals('광주광역시')
        self.assertEqual(public_data.normalize_hospital(result[0])['lat'], 35.16)
        self.assertEqual([v[0] for v in calls], ['getEgytListInfoInqire', 'getEgytBassInfoInqire'])
        self.assertEqual(calls[1][1]['HPID'], ['A000001'])

    def test_detail_for_different_hospital_is_rejected(self):
        def opener(request, timeout):
            if 'getEgytListInfoInqire' in request.full_url:
                return Response(xml_page([hospital(wgs84Lat='')]))
            return Response(xml_page([hospital('WRONG-ID')]))
        with self.assertRaises(public_data.PublicDataError):
            self.client(opener).fetch_hospitals('광주광역시')

    def test_usable_list_coordinates_do_not_request_detail(self):
        calls = []
        def opener(request, timeout):
            calls.append(request.full_url)
            return Response(xml_page([hospital()]))
        self.assertEqual(len(self.client(opener).fetch_hospitals('광주광역시')), 1)
        self.assertEqual(len(calls), 1)


class PublicStorage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / 'public.db')
        self.conn = db.connect(self.path)
        db.init(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def ingest(self, **kw):
        return public_data.apply_records(self.conn, NOW, **kw)

    def patient(self):
        return service.create_patient(self.conn, NOW, 'P1', dict(
            reporter_role='transporter', consent=True, age=40, symptoms='테스트',
            required_capabilities=['소아청소년과'], resource_demands={'ed_bed': 1},
            priority=4, priority_source='expert', lat=35.16, lon=126.85))

    def snapshot(self):
        return dict(self.conn.execute("SELECT * FROM public_api_snapshots WHERE endpoint='nmc_rltm_beds'").fetchone())

    def test_initial_ingest_keeps_public_observation_separate_from_operational_resources(self):
        stats = self.ingest(basic_records=[hospital()], bed_records=[beds()])
        view = service.hospital_view(self.conn, 'NMC-A000001', NOW)
        self.assertEqual(stats['hospitals_created'], 1)
        self.assertEqual(view['capabilities_status'], 'unknown')
        self.assertEqual(view['resources'], [])
        public_beds = next(v for v in view['public_data'] if v['endpoint'] == 'nmc_rltm_beds')
        self.assertEqual(public_beds['data']['resources'][0]['reported_available'], 7)
        self.assertEqual(public_beds['source_observed_at'], OBSERVED)
        self.assertFalse(public_beds['stale'])
        self.assertEqual(public_beds['fetched_at'], NOW)

    def test_reingest_with_referencing_patient_preserves_fk_and_manual_values(self):
        self.ingest(basic_records=[hospital()], bed_records=[beds()])
        hid = 'NMC-A000001'
        self.conn.execute("UPDATE hospitals SET capabilities=?,capabilities_status='known' WHERE id=?",
                          (db.dumps(['소아청소년과']), hid))
        service.set_resource(self.conn, NOW, hid, 'ed_bed', 10, 3)
        self.patient()
        service.send_inquiries(self.conn, NOW, 'P1', [hid])
        iid = self.conn.execute('SELECT id FROM inquiries').fetchone()[0]
        service.respond(self.conn, NOW, iid, 'accept', 'tester')
        service.confirm_destination(self.conn, NOW, 'P1', iid)
        service.add_restriction(self.conn, NOW, hid, '신경외과', '당직 공백')
        resource_before = dict(self.conn.execute('SELECT * FROM resources').fetchone())
        self.ingest(basic_records=[hospital(dutyName='변경된 공공 병원명', dutyTel3='', dutyTel1='')],
                    bed_records=[beds(hvec='2')])
        view = service.hospital_view(self.conn, hid, NOW)
        self.assertEqual(view['name'], '변경된 공공 병원명')
        self.assertEqual(view['phone'], '062-000-0003')
        self.assertEqual(view['capabilities'], ['소아청소년과'])
        self.assertEqual(view['capabilities_status'], 'known')
        self.assertEqual(dict(self.conn.execute('SELECT * FROM resources').fetchone()), resource_before)
        self.assertEqual(view['resources'][0]['free'], 6)
        self.assertEqual(len(view['restrictions']), 1)
        self.assertEqual(self.conn.execute('SELECT destination_hospital_id FROM patients').fetchone()[0], hid)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM inquiries').fetchone()[0], 1)
        self.assertEqual(self.conn.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_explicit_mapping_to_manual_hospital_preserves_manual_metadata(self):
        service.upsert_hospital(self.conn, dict(id='H01', name='담당자 병원명', address='수동 주소', phone='수동 전화',
            lat=35.2, lon=126.9, capabilities=['외과'], source='hospital_input', source_ids={'nmc': 'A000001'}))
        self.ingest(basic_records=[hospital()], bed_records=[beds()])
        view = service.hospital_view(self.conn, 'H01', NOW)
        self.assertEqual((view['name'], view['address'], view['phone']), ('담당자 병원명', '수동 주소', '수동 전화'))
        self.assertEqual((view['lat'], view['lon']), (35.2, 126.9))
        self.assertEqual(view['capabilities'], ['외과'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM hospitals').fetchone()[0], 1)
        self.assertEqual(len(view['public_data']), 2)

    def test_unknown_capabilities_remain_candidate_without_claiming_supported(self):
        self.ingest(basic_records=[hospital()], bed_records=[beds()])
        self.patient()
        result = service.candidates(self.conn, NOW, 'P1', EST)
        self.assertEqual(result['excluded'], [])
        self.assertEqual(len(result['candidates']), 1)
        candidate = result['candidates'][0]
        self.assertTrue(any('진료 분야 확인 필요' in v for v in candidate['checks']))
        self.assertFalse(any('진료 분야 보유' in v for v in candidate['reasons']))
        self.assertFalse(candidate['resources_enough'])
        self.assertTrue(candidate['public_data'])

    def test_solver_requires_acceptance_and_operational_resources_even_with_public_beds(self):
        self.ingest(basic_records=[hospital()], bed_records=[beds(hvec='99')])
        self.patient()
        hid = 'NMC-A000001'
        model, _ = solver_adapter.build(planning.capture(self.conn, NOW), EST)
        self.assertEqual(model['patients'][0]['eligible'], [])
        service.send_inquiries(self.conn, NOW, 'P1', [hid])
        iid = self.conn.execute('SELECT id FROM inquiries').fetchone()[0]
        service.respond(self.conn, NOW, iid, 'accept', 'tester')
        model, _ = solver_adapter.build(planning.capture(self.conn, NOW), EST)
        self.assertEqual(model['patients'][0]['eligible'], [])
        service.set_resource(self.conn, NOW, hid, 'ed_bed', 1, 0)
        model, context = solver_adapter.build(planning.capture(self.conn, NOW), EST)
        self.assertEqual(model['patients'][0]['eligible'], [0])
        self.assertIn('CAPABILITIES_UNVERIFIED:' + hid, context['P1']['warnings'])

    def test_duplicate_records_roll_back_hospitals_snapshots_and_revision(self):
        before = self.conn.execute('SELECT revision FROM planning_state').fetchone()[0]
        with self.assertRaises(public_data.PublicDataError):
            self.ingest(basic_records=[hospital(), hospital()], bed_records=[beds()])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM hospitals').fetchone()[0], 0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM public_api_snapshots').fetchone()[0], 0)
        self.assertEqual(self.conn.execute('SELECT revision FROM planning_state').fetchone()[0], before)

    def test_duplicate_bed_records_roll_back_basic_update_and_old_snapshot(self):
        self.ingest(basic_records=[hospital()], bed_records=[beds()])
        before = self.snapshot()
        revision = self.conn.execute('SELECT revision FROM planning_state').fetchone()[0]
        with self.assertRaises(public_data.PublicDataError):
            self.ingest(basic_records=[hospital(dutyName='반영되면 안 됨')], bed_records=[beds(), beds()])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.conn.execute('SELECT name FROM hospitals').fetchone()[0], '테스트 응급의료기관')
        self.assertEqual(self.conn.execute('SELECT revision FROM planning_state').fetchone()[0], revision)

    def test_older_or_unknown_observation_does_not_replace_newer_snapshot(self):
        self.ingest(basic_records=[hospital()], bed_records=[beds()])
        before = self.snapshot()
        for stamp in ('20261011115900', '', 'invalid'):
            with self.subTest(stamp=stamp):
                stats = self.ingest(bed_records=[beds(hvec='99', hvidate=stamp)])
                self.assertEqual(stats['skipped_invalid'], 1)
                self.assertEqual(self.snapshot(), before)

    def test_missing_or_future_source_time_remains_unknown(self):
        self.ingest(basic_records=[hospital()], bed_records=[beds(hvec='-1', hvidate='')])
        view = next(v for v in service.hospital_view(self.conn, 'NMC-A000001', NOW)['public_data']
                    if v['endpoint'] == 'nmc_rltm_beds')
        self.assertTrue(view['observation_time_unknown'])
        self.assertIsNone(view['stale'])
        self.assertIsNone(view['data']['resources'][0]['reported_available'])
        self.ingest(bed_records=[beds(hvec='99', hvidate='20990101000000')])
        self.assertIsNone(self.snapshot()['source_observed_at'])
        self.assertTrue(all(r['reported_available'] is None
                            for r in json.loads(self.snapshot()['normalized'])['resources']))

    def test_delivery_flag_ingest_keeps_public_observation_separate_from_operational_slots(self):
        self.ingest(basic_records=[hospital()], bed_records=[beds(hv42='Y')])
        normalized = json.loads(self.snapshot()['normalized'])
        self.assertIs(normalized['service_availability'][0]['reported_available'], True)
        quantity = next(r for r in normalized['resources'] if r['resource_type'] == 'delivery_room')
        self.assertIsNone(quantity['reported_available'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM resources').fetchone()[0], 0)
        self.assertEqual(json.loads(self.snapshot()['raw_data'])['hv42'], 'Y')

    def test_future_source_time_invalidates_delivery_flag_as_well_as_counts(self):
        self.ingest(basic_records=[hospital()],
                    bed_records=[beds(hv42='Y', hvidate='20990101000000')])
        normalized = json.loads(self.snapshot()['normalized'])
        self.assertIsNone(self.snapshot()['source_observed_at'])
        self.assertIsNone(normalized['service_availability'][0]['reported_available'])
        self.assertEqual(normalized['service_availability'][0]['status'], 'unknown')
        self.assertEqual(json.loads(self.snapshot()['raw_data'])['hv42'], 'Y')

    def test_missing_source_time_preserves_delivery_raw_flag_with_unknown_freshness(self):
        self.ingest(basic_records=[hospital()], bed_records=[beds(hv42='N', hvidate='')])
        snapshot = next(r for r in public_data.snapshot_view(self.conn, 'NMC-A000001', NOW)
                        if r['endpoint'] == 'nmc_rltm_beds')
        self.assertIsNone(snapshot['source_observed_at'])
        self.assertIsNone(snapshot['stale'])
        self.assertIs(snapshot['data']['service_availability'][0]['reported_available'], False)

    def test_new_fetch_does_not_make_old_delivery_service_flag_fresh(self):
        self.ingest(basic_records=[hospital()], bed_records=[beds(hv42='Y')])
        # 재수집 시각은 최신이어도 병원 원본 시각은 이전 관측 그대로다.
        public_data.apply_records(self.conn, NOW + 1000, bed_records=[beds(hv42='Y')])
        snapshot = next(r for r in public_data.snapshot_view(self.conn, 'NMC-A000001', NOW + 1000)
                        if r['endpoint'] == 'nmc_rltm_beds')
        self.assertTrue(snapshot['stale'])
        self.assertEqual(snapshot['source_observed_at'], OBSERVED)
        self.assertEqual(snapshot['fetched_at'], NOW + 1000)

    def test_older_delivery_service_flag_cannot_overwrite_latest_observation(self):
        self.ingest(basic_records=[hospital()], bed_records=[beds(hv42='Y')])
        before = self.snapshot()
        stats = self.ingest(bed_records=[beds(hv42='N', hvidate='20261011115900')])
        self.assertEqual(stats['skipped_invalid'], 1)
        self.assertEqual(self.snapshot(), before)

    def test_stale_public_snapshot_is_not_refreshed_by_local_arrival(self):
        self.ingest(basic_records=[hospital()], bed_records=[beds()])
        hid = 'NMC-A000001'
        service.set_resource(self.conn, NOW, hid, 'ed_bed', 1, 0)
        self.patient()
        service.send_inquiries(self.conn, NOW, 'P1', [hid])
        iid = self.conn.execute('SELECT id FROM inquiries').fetchone()[0]
        service.respond(self.conn, NOW, iid, 'accept', 'tester')
        service.confirm_destination(self.conn, NOW, 'P1', iid)
        service.confirm_arrival(self.conn, NOW + 400, 'P1', hid)
        view = service.hospital_view(self.conn, hid, NOW + 400)
        self.assertFalse(view['resources'][0]['stale'])
        observation = next(v for v in view['public_data'] if v['endpoint'] == 'nmc_rltm_beds')
        self.assertTrue(observation['stale'])
        self.assertEqual(observation['source_observed_at'], OBSERVED)
        self.assertEqual(observation['fetched_at'], NOW)

    def test_invalid_or_unmatched_records_do_not_create_phantom_hospitals(self):
        stats = self.ingest(basic_records=[hospital(wgs84Lat='NaN')], bed_records=[beds('UNMAPPED')])
        self.assertEqual(stats['skipped_invalid'], 1)
        self.assertEqual(stats['unmatched_beds'], 1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM hospitals').fetchone()[0], 0)

    def test_sync_failure_preserves_existing_data_and_records_safe_error(self):
        self.ingest(basic_records=[hospital()], bed_records=[beds()])
        before = self.snapshot()
        class FailingClient:
            def fetch_all(self, operation, region):
                if operation == 'nmc_hospital_list':
                    return [hospital(dutyName='실패로 미반영')]
                raise urllib.error.URLError('https://example.test/?serviceKey=VERY_SECRET_KEY')
        with self.assertRaises(public_data.PublicDataError) as cm:
            public_data.sync_once(self.path, '광주광역시', client=FailingClient(), clock=lambda: NOW + 1)
        self.assertNotIn('VERY_SECRET_KEY', str(cm.exception))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.conn.execute('SELECT name FROM hospitals').fetchone()[0], '테스트 응급의료기관')
        run = dict(self.conn.execute('SELECT * FROM public_sync_runs').fetchone())
        self.assertEqual(run['status'], 'ERROR')
        self.assertIsNotNone(run['finished_at'])
        self.assertNotIn('VERY_SECRET_KEY', run['error'])

    def test_sync_success_and_empty_next_refresh_preserve_existing_records(self):
        class Client:
            def fetch_all(self, operation, region):
                return [hospital()] if operation == 'nmc_hospital_list' else [beds()]
        result = public_data.sync_once(self.path, '광주광역시', client=Client(), clock=lambda: NOW)
        self.assertEqual(result['status'], 'SUCCESS')
        before = self.snapshot()
        class EmptyClient:
            def fetch_all(self, operation, region):
                return []
        result = public_data.sync_once(self.path, '광주광역시', client=EmptyClient(), clock=lambda: NOW + 10)
        self.assertEqual(result['status'], 'SUCCESS')
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM hospitals').fetchone()[0], 1)
        self.assertEqual([r['status'] for r in self.conn.execute('SELECT status FROM public_sync_runs')],
                         ['SUCCESS', 'SUCCESS'])

    def test_beds_only_sync_uses_saved_hpid_mapping_without_calling_basic(self):
        self.ingest(basic_records=[hospital()])
        calls = []
        class Client:
            def fetch_all(self, operation, region):
                calls.append(operation)
                return [beds()]
        result = public_data.sync_once(self.path, '광주광역시', client=Client(),
                                      include_basic=False, clock=lambda: NOW)
        self.assertEqual(calls, ['nmc_rltm_beds'])
        self.assertEqual(result['snapshots'], 1)

    def test_periodic_cli_refreshes_basic_on_its_own_interval(self):
        calls = []
        class StopAfterThree:
            count = 0
            def is_set(self):
                return self.count >= 3
            def wait(self, interval):
                self.count += 1
        def sync(path, region, client, include_basic):
            calls.append(include_basic)
            return {'status': 'SUCCESS'}
        with patch.object(sync_public, 'NmcClient', return_value=object()), \
             patch.object(sync_public, 'sync_once', side_effect=sync), \
             patch.object(sync_public.threading, 'Event', return_value=StopAfterThree()), \
             patch.object(sync_public.time, 'monotonic', side_effect=[0, 0, 60, 120, 120]), \
             patch('sys.stdout', new_callable=io.StringIO):
            result = sync_public.main(['--db', self.path, '--region', '광주광역시',
                                       '--district', '동구', '--interval', '60', '--basic-interval', '120'])
        self.assertEqual(calls, [True, False, True])
        self.assertIsNone(result)

    def test_cli_requires_district_before_creating_client(self):
        with patch.object(sync_public, 'NmcClient') as client, patch('sys.stderr', new_callable=io.StringIO):
            with self.assertRaises(SystemExit) as cm:
                sync_public.main(['--db', self.path, '--region', '광주광역시'])
        self.assertEqual(cm.exception.code, 2)
        client.assert_not_called()

    def test_failed_basic_refresh_is_retried_next_period(self):
        calls = []
        class StopAfterTwo:
            count = 0
            def is_set(self):
                return self.count >= 2
            def wait(self, interval):
                self.count += 1
        def sync(path, region, client, include_basic):
            calls.append(include_basic)
            if len(calls) == 1:
                raise public_data.PublicDataError('일시적 API 장애')
            return {'status': 'SUCCESS'}
        with patch.object(sync_public, 'NmcClient', return_value=object()), \
             patch.object(sync_public, 'sync_once', side_effect=sync), \
             patch.object(sync_public.threading, 'Event', return_value=StopAfterTwo()), \
             patch.object(sync_public.time, 'monotonic', side_effect=[0, 60, 60]), \
             patch('sys.stdout', new_callable=io.StringIO):
            sync_public.main(['--db', self.path, '--region', '광주광역시', '--district', '동구', '--interval', '60'])
        self.assertEqual(calls, [True, True])


if __name__ == '__main__':
    unittest.main()
