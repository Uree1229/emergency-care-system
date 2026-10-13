"""심평원 신고자료 계약·안전한 연계 검증. 인증키·실 API 없이 실행한다."""
import copy
import io
import json
import tempfile
import unittest
import urllib.error
import urllib.parse
from pathlib import Path
from xml.sax.saxutils import escape

from server import db, hira_data, public_data, service


NOW = 1791690000
KEY = 'FIXTURE/ONLY+KEY='
YKIHO = 'FIXTURE/ENCRYPTED+ID='


def xml_page(records=None, *, total=None, page=1, code='00'):
    records = [] if records is None else records
    items = ''.join('<item>' + ''.join(f'<{k}>{escape(str(v))}</{k}>' for k, v in row.items())
                    + '</item>' for row in records)
    page_tag = '' if page is None else f'<pageNo>{page}</pageNo>'
    return (f'<response><header><resultCode>{escape(code)}</resultCode></header><body>'
            f'<items>{items}</items><totalCount>{len(records) if total is None else total}</totalCount>'
            f'{page_tag}</body></response>').encode('utf-8')


def bundle(hid='NMC-A001', ykiho=YKIHO):
    return {'hospital_id': hid, 'ykiho': ykiho, 'evidence': {'verified_pair': True}, 'records': {
        'hira_hospital_basis': [{'ykiho': ykiho, 'yadmNm': '기존 운영 병원 ' + hid.removeprefix('NMC-'),
                                 'addr': '기존 주소', 'XPos': '127.0', 'YPos': '37.0',
                                 'telno': '031-000-9999', 'drTotCnt': '50', 'mdeptSdrCnt': '30'}],
        'hira_departments': [{'dgsbjtCd': '01', 'dgsbjtCdNm': '내과', 'dgsbjtPrSdrCnt': '4'}],
        'hira_specialists': [{'dgsbjtCd': '01', 'dgsbjtCdNm': '내과', 'dtlSdrCnt': '3'}],
        'hira_equipment': [{'oftCd': 'TEST-CT', 'oftCdNm': '전산화단층촬영장치', 'oftCnt': '2'}],
        'hira_facilities': [{'emymCnt': '10', 'nbySprmCnt': '0', 'partumCnt': '2'}],
        'hira_special_care': [{'srchCd': 'FIXTURE-UNKNOWN', 'srchCdNm': '원문 특수진료'}],
        'hira_operating_details': [{'emyDayYn':'Y', 'emyNgtYn':'Y', 'emyNgtTelNo1':'031-000-0123',
                                    'trmtMonStart':'0900', 'trmtMonEnd':'1800'}],
        'hira_designated_specialties': [{'srchCd':'FIXTURE-SPECIALTY', 'srchCdNm':'원문 지정분야'}],
        'hira_nursing_grades': [{'tyCd':'FIXTURE-NURSE', 'tyCdNm':'원문 간호분야', 'careGrd':'1'}],
        'hira_other_personnel': [{'gnlNopDtlCd':'FIXTURE-PERSONNEL', 'dtlGnlNopCdNm':'약사', 'gnlNopCnt':'4'}],
        'hira_transport': [{'trafNm':'시내버스', 'lineNo':'100', 'arivPlc':'병원 앞', 'dist':'100m'}],
        'hira_meal_additions': [{'tyCd':'FIXTURE-MEAL', 'tyCdNm':'원문 식대구분', 'trmealGrd':'1',
                                'calcNopCnt':'2', 'gnmAddcYn':'Y'}],
    }}


class HiraClientTests(unittest.TestCase):
    def client(self, opener, **changes):
        return hira_data.HiraClient(key=urllib.parse.quote(KEY, safe=''), opener=opener,
                                    sleep=lambda _: None, **changes)

    def test_operations_match_official_swagger_urls_and_auth_parameter_case(self):
        expected = {
            'hira_hospital_basis': ('hospInfoServicev2/getHospBasisList', 'ServiceKey'),
            'hira_departments': ('MadmDtlInfoService2.8/getDgsbjtInfo2.8', 'serviceKey'),
            'hira_specialists': ('MadmDtlInfoService2.8/getSpcSbjtSdrInfo2.8', 'serviceKey'),
            'hira_equipment': ('MadmDtlInfoService2.8/getMedOftInfo2.8', 'serviceKey'),
            'hira_facilities': ('MadmDtlInfoService2.8/getEqpInfo2.8', 'serviceKey'),
            'hira_special_care': ('MadmDtlInfoService2.8/getSpclDiagInfo2.8', 'serviceKey'),
            'hira_operating_details': ('MadmDtlInfoService2.8/getDtlInfo2.8', 'serviceKey'),
            'hira_designated_specialties': ('MadmDtlInfoService2.8/getSpclHospAsgFldList2.8', 'serviceKey'),
            'hira_nursing_grades': ('MadmDtlInfoService2.8/getNursigGrdInfo2.8', 'serviceKey'),
            'hira_other_personnel': ('MadmDtlInfoService2.8/getEtcHstInfo2.8', 'serviceKey'),
            'hira_transport': ('MadmDtlInfoService2.8/getTrnsprtInfo2.8', 'serviceKey'),
            'hira_meal_additions': ('MadmDtlInfoService2.8/getFoepAddcInfo2.8', 'serviceKey'),
        }
        self.assertEqual(set(expected), set(hira_data.OPERATIONS))
        for endpoint, (path, key_name) in expected.items():
            with self.subTest(endpoint=endpoint):
                calls = []
                def opener(req, timeout):
                    calls.append(req.full_url)
                    return io.BytesIO(xml_page())
                params = {'yadmNm': '테스트 병원'} if endpoint == 'hira_hospital_basis' else {'ykiho': YKIHO}
                self.assertEqual(self.client(opener).fetch_all(endpoint, **params), [])
                self.assertEqual(len(calls), 1)
                parsed = urllib.parse.urlsplit(calls[0])
                self.assertEqual(parsed.scheme, 'https')
                self.assertEqual(parsed.netloc, 'apis.data.go.kr')
                self.assertEqual(parsed.path, '/B551182/' + path)
                query = urllib.parse.parse_qs(parsed.query)
                self.assertEqual(query[key_name], [KEY])
                self.assertNotIn('serviceKey' if key_name == 'ServiceKey' else 'ServiceKey', query)

    def test_encoding_is_applied_once_and_plus_in_key_and_ykiho_is_preserved(self):
        def opener(req, timeout):
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(req.full_url).query)
            self.assertEqual(query['serviceKey'], [KEY])
            self.assertEqual(query['ykiho'], [YKIHO])
            self.assertNotIn('%252F', req.full_url)
            return io.BytesIO(xml_page())
        self.client(opener).fetch_all('hira_specialists', ykiho=YKIHO)

    def test_http_403_is_not_retried_and_error_does_not_expose_credentials_or_url(self):
        calls = []
        def opener(req, timeout):
            calls.append(req.full_url)
            raise urllib.error.HTTPError(req.full_url, 403, 'denied ' + KEY, {}, None)
        with self.assertRaises(hira_data.HiraDataError) as cm:
            self.client(opener).fetch_all('hira_facilities', ykiho=YKIHO)
        self.assertEqual(len(calls), 1)
        self.assertTrue(cm.exception.access_denied)
        for secret in (KEY, YKIHO, urllib.parse.quote(KEY, safe=''), 'https://'):
            self.assertNotIn(secret, str(cm.exception))

    def test_http_500_is_retried_then_successfully_returned(self):
        calls, delays = [], []
        def opener(req, timeout):
            calls.append(1)
            if len(calls) < 3:
                raise urllib.error.HTTPError(req.full_url, 500, 'unavailable', {}, None)
            return io.BytesIO(xml_page([{'dtlSdrCnt': '2'}]))
        client = hira_data.HiraClient(key=KEY, opener=opener, sleep=delays.append)
        result = client.fetch_all('hira_specialists', ykiho=YKIHO)
        self.assertEqual(result, [{'dtlSdrCnt': '2'}])
        self.assertEqual((len(calls), delays), (3, [1, 2]))

    def test_connection_error_retries_without_echoing_arbitrary_error_message(self):
        calls = []
        def opener(req, timeout):
            calls.append(1)
            raise urllib.error.URLError('URL=' + req.full_url + ' ' + KEY)
        with self.assertRaises(hira_data.HiraDataError) as cm:
            self.client(opener, retries=1).fetch_all('hira_specialists', ykiho=YKIHO)
        self.assertEqual(len(calls), 2)
        self.assertFalse(cm.exception.access_denied)
        self.assertNotIn(KEY, str(cm.exception))
        self.assertNotIn('https://', str(cm.exception))

    def test_permission_result_codes_are_not_empty_success_or_retryable(self):
        for code in ('20', '30', '31'):
            with self.subTest(code=code):
                calls = []
                def opener(req, timeout):
                    calls.append(1)
                    return io.BytesIO(xml_page(code=code))
                with self.assertRaises(hira_data.HiraDataError) as cm:
                    self.client(opener).fetch_all('hira_departments', ykiho=YKIHO)
                self.assertTrue(cm.exception.access_denied)
                self.assertEqual(len(calls), 1)

    def test_malformed_response_uses_controlled_error(self):
        with self.assertRaises(hira_data.HiraDataError) as cm:
            self.client(lambda req, timeout: io.BytesIO((KEY + '<invalid').encode()))\
                .fetch_all('hira_equipment', ykiho=YKIHO)
        self.assertNotIn(KEY, str(cm.exception))

    def test_all_pages_are_collected_in_order(self):
        calls = []
        def opener(req, timeout):
            page = int(urllib.parse.parse_qs(urllib.parse.urlsplit(req.full_url).query)['pageNo'][0])
            calls.append(page)
            return io.BytesIO(xml_page([{'dtlSdrCnt': str(page)}], total=2, page=page))
        result = self.client(opener, page_size=1).fetch_all('hira_specialists', ykiho=YKIHO)
        self.assertEqual(calls, [1, 2])
        self.assertEqual(result, [{'dtlSdrCnt': '1'}, {'dtlSdrCnt': '2'}])

    def test_missing_changed_wrong_or_overfull_pages_never_return_partial_data(self):
        scenarios = (
            [xml_page([{'a': '1'}], total=2), xml_page([], total=2, page=2)],
            [xml_page([{'a': '1'}], total=2), xml_page([{'a': '2'}], total=3, page=2)],
            [xml_page([{'a': '1'}], total=2), xml_page([{'a': '2'}], total=2, page=1)],
            [xml_page([{'a': '1'}, {'a': '2'}], total=1)],
        )
        for pages in scenarios:
            with self.subTest(pages=pages):
                responses = iter(pages)
                with self.assertRaises(hira_data.HiraDataError):
                    self.client(lambda req, timeout: io.BytesIO(next(responses)), page_size=1)\
                        .fetch_all('hira_specialists', ykiho=YKIHO)

    def test_page_limit_rejects_incomplete_collection(self):
        with self.assertRaisesRegex(hira_data.HiraDataError, '최대 페이지'):
            self.client(lambda req, timeout: io.BytesIO(xml_page([{'a': '1'}], total=2)),
                        max_pages=1, page_size=1).fetch_all('hira_specialists', ykiho=YKIHO)

    def test_invalid_or_injected_params_are_rejected_before_network(self):
        calls = []
        client = self.client(lambda req, timeout: calls.append(1))
        for endpoint, params in (('invalid', {'ykiho': YKIHO}), ('hira_specialists', {}),
                                 ('hira_specialists', {'ykiho': ''}),
                                 ('hira_specialists', {'ykiho': YKIHO, 'serviceKey': 'injected'}),
                                 ('hira_hospital_basis', {'yadmNm': ' '}),
                                 ('hira_hospital_basis', {'radius': 5000})):
            with self.subTest(endpoint=endpoint, params=params):
                with self.assertRaises(hira_data.HiraDataError):
                    client.fetch_all(endpoint, **params)
        self.assertEqual(calls, [])

    def test_secret_values_are_redacted_from_successful_response_data(self):
        record = {'note': KEY, 'encoded_note': urllib.parse.quote(KEY, safe='')}
        result = self.client(lambda req, timeout: io.BytesIO(xml_page([record])))\
            .fetch_all('hira_specialists', ykiho=YKIHO)
        self.assertEqual(result, [{'note': '***', 'encoded_note': '***'}])


class HiraNormalizationTests(unittest.TestCase):
    def test_basis_separates_reported_total_doctors_from_medical_specialists(self):
        result = hira_data.normalize_records('hira_hospital_basis', bundle()['records']['hira_hospital_basis'])
        self.assertEqual(result['reported_total_doctors'], 50)
        self.assertEqual(result['reported_medical_specialists'], 30)
        self.assertNotIn('available_specialists', result)

    def test_department_doctor_count_is_not_specialist_count(self):
        departments = hira_data.normalize_records('hira_departments', [
            {'dgsbjtCd': '01', 'dgsbjtCdNm': '내과', 'dgsbjtPrSdrCnt': '5', 'dtlSdrCnt': '99'}])
        specialists = hira_data.normalize_records('hira_specialists', [
            {'dgsbjtCd': '01', 'dgsbjtCdNm': '내과', 'dtlSdrCnt': '3', 'dgsbjtPrSdrCnt': '99'}])
        self.assertEqual((departments['departments'][0]['reported_count'],
                          departments['departments'][0]['count_kind']), (5, 'doctors'))
        self.assertEqual((specialists['specialists'][0]['reported_count'],
                          specialists['specialists'][0]['count_kind']), (3, 'specialists'))

    def test_equipment_registration_count_is_not_available_equipment(self):
        result = hira_data.normalize_records('hira_equipment', [
            {'oftCd': 'CUSTOM-UNVERIFIED', 'oftCdNm': '장비 원문', 'oftCnt': '2'}])
        self.assertEqual(result['registered_equipment'], [{'code': 'CUSTOM-UNVERIFIED',
            'name': '장비 원문', 'reported_count': 2, 'source_field': 'oftCnt'}])
        self.assertNotIn('equipment', result)
        self.assertNotIn('reported_available', result['registered_equipment'][0])

    def test_facility_zero_missing_and_special_values_remain_distinct(self):
        result = hira_data.normalize_records('hira_facilities', [
            {'emymCnt': '0', 'nbySprmCnt': '2', 'soprmCnt': '-1', 'partumCnt': '미상'}])
        values = {r['source_field']: r for r in result['facilities']}
        self.assertEqual((values['emymCnt']['reported_count'], values['emymCnt']['status']), (0, 'reported'))
        self.assertEqual(values['nbySprmCnt']['reported_count'], 2)
        for field in ('soprmCnt', 'partumCnt', 'permSbdCnt'):
            self.assertIsNone(values[field]['reported_count'])
            self.assertEqual(values[field]['status'], 'unknown')
        self.assertNotIn('capacity', values['nbySprmCnt'])

    def test_special_care_codes_are_preserved_without_availability_inference(self):
        result = hira_data.normalize_records('hira_special_care', [
            {'srchCd': '0001', 'srchCdNm': '원문 상태'}])
        self.assertEqual(result['special_care'], [{'code': '0001', 'name': '원문 상태',
                                                  'status': 'registered_uninterpreted'}])
        self.assertNotIn('capabilities', result)

    def test_operating_details_keep_consultation_hours_separate_from_emergency_operations(self):
        records = [{'emyDayYn':'Y', 'emyNgtYn':'N', 'emyDayTelNo1':'031-000-1000',
                    'emyDayTelNo2':'', 'emyNgtTelNo1':'031-000-2000',
                    'trmtMonStart':'0900', 'trmtMonEnd':'1800', 'trmtSunStart':'2400',
                    'trmtSunEnd':'0000', 'rcvWeek':'09:00~17:30', 'noTrmtHoli':'휴진',
                    'lunchWeek':'12:00~13:00', 'parkQty':'20', 'parkXpnsYn':'유료',
                    'parkEtc':'30분 무료', 'plcNm':'시청', 'plcDir':'북쪽', 'plcDist':'300m'}]
        before = copy.deepcopy(records)
        result = hira_data.normalize_records('hira_operating_details', records)
        detail = result['operating_details'][0]
        self.assertEqual(detail['emergency_department']['day'],
            {'source_field':'emyDayYn', 'raw_value':'Y', 'reported_operating':True, 'status':'reported'})
        self.assertFalse(detail['emergency_department']['night']['reported_operating'])
        self.assertEqual(detail['emergency_contacts'], {'day':['031-000-1000'], 'night':['031-000-2000']})
        self.assertEqual(detail['consultation_hours'][0],
            {'day':'monday','start_field':'trmtMonStart','end_field':'trmtMonEnd','start_raw':'0900','end_raw':'1800'})
        self.assertEqual(detail['consultation_hours'][-1]['start_raw'], '2400')
        self.assertEqual(detail['closures']['holiday_raw'], '휴진')
        self.assertEqual(detail['parking'], {'reported_count':20,'operation_cost_raw':'유료','note':'30분 무료'})
        self.assertEqual(detail['location']['distance_raw'], '300m')
        self.assertNotIn('currently_open', detail)
        self.assertNotIn('travel_seconds', detail)
        self.assertEqual(records, before)

    def test_emergency_operation_numeric_codes_and_missing_values_are_not_guessed(self):
        for value in (None, '', '1', '2', '0', 'N1', '운영'):
            with self.subTest(value=value):
                result = hira_data.normalize_records('hira_operating_details', [{'emyDayYn':value}])
                detail = result['operating_details'][0]
                self.assertIsNone(detail['emergency_department']['day']['reported_operating'])
                self.assertEqual(detail['emergency_department']['day']['status'], 'unknown')
                self.assertIsNone(detail['emergency_department']['night']['reported_operating'])
                self.assertEqual(detail['emergency_contacts'], {'day':[], 'night':[]})

    def test_designated_specialty_is_registration_not_patient_acceptance(self):
        result = hira_data.normalize_records('hira_designated_specialties', [
            {'srchCd':'01', 'srchCdNm':'산부인과'}])
        self.assertEqual(result['designated_specialties'],
                         [{'code':'01', 'name':'산부인과', 'status':'registered_uninterpreted'}])
        self.assertNotIn('reported_available', result)
        self.assertNotIn('capabilities', result)

    def test_nursing_grade_is_preserved_as_category_not_staff_count(self):
        result = hira_data.normalize_records('hira_nursing_grades', [
            {'tyCd':'TEST', 'tyCdNm':'중환자실', 'careGrd':'1등급'}])
        self.assertEqual(result['nursing_grades'],
                         [{'code':'TEST','name':'중환자실','reported_grade':'1등급'}])
        self.assertNotIn('reported_count', result['nursing_grades'][0])

    def test_other_personnel_uses_exact_documented_name_and_nonnegative_integer_count(self):
        records = [{'gnlNopDtlCd':'TEST-PHARM', 'dtlGnlNopCdNm':'약사', 'gnlNopCnt':'3'},
                   {'gnlNopDtlCd':'TEST-ZERO', 'dtlGnlNopCdNm':'영양사', 'gnlNopCnt':'0'},
                   {'gnlNopDtlCd':'TEST-MISSING', 'gnlNopCdNm':'틀린 필드명', 'gnlNopCnt':'-1'}]
        result = hira_data.normalize_records('hira_other_personnel', records)
        self.assertEqual(result['other_personnel'][0], {'code':'TEST-PHARM','name':'약사',
            'reported_count':3,'source_field':'gnlNopCnt','count_kind':'registered_other_personnel'})
        self.assertEqual(result['other_personnel'][1]['reported_count'], 0)
        self.assertIsNone(result['other_personnel'][2]['reported_count'])
        self.assertIsNone(result['other_personnel'][2]['name'])
        self.assertNotIn('available', result['other_personnel'][0])

    def test_transport_guidance_does_not_become_ambulance_route_or_eta(self):
        result = hira_data.normalize_records('hira_transport', [{
            'trafNm':'시내버스', 'lineNo':'100-1', 'arivPlc':'병원 앞', 'dir':'서쪽',
            'dist':'100m / 도보 2분', 'rmk':'평일 운행'}])
        self.assertEqual(result['transport_guidance'], [{'mode':'시내버스','line_number':'100-1',
            'arrival_place':'병원 앞','direction':'서쪽','distance_raw':'100m / 도보 2분','note':'평일 운행'}])
        self.assertNotIn('travel_seconds', result)

    def test_meal_grade_and_personnel_do_not_become_clinical_resources(self):
        result = hira_data.normalize_records('hira_meal_additions', [{
            'tyCd':'MEAL','tyCdNm':'치료식','trmealGrd':'A','calcNopCnt':'2','gnmAddcYn':'1'}])
        self.assertEqual(result['meal_additions'], [{'code':'MEAL','name':'치료식','reported_grade':'A',
            'reported_personnel_count':2,'addition_raw':'1'}])
        self.assertNotIn('resources', result)

    def test_source_time_unknown_and_empty_result_do_not_mean_absence(self):
        for endpoint in hira_data.DETAIL_ENDPOINTS:
            with self.subTest(endpoint=endpoint):
                result = hira_data.normalize_records(endpoint, [])
                self.assertIsNone(result['source_observed_at'])
                self.assertEqual(result['data_kind'], 'registered')
                self.assertTrue(result['empty_result_not_absence'])
                self.assertEqual(result['record_count'], 0)

    def test_bad_counts_are_unknown_and_input_is_not_mutated(self):
        for value in ('', '-1', '1.5', None, 'N1'):
            with self.subTest(value=value):
                records = [{'dgsbjtCd': '01', 'dgsbjtCdNm': '내과', 'dtlSdrCnt': value}]
                before = copy.deepcopy(records)
                result = hira_data.normalize_records('hira_specialists', records)
                self.assertIsNone(result['specialists'][0]['reported_count'])
                self.assertEqual(records, before)


class HiraStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(str(Path(self.tmp.name) / 'hira.db'))
        db.init(self.conn)
        records = [dict(hpid=hpid, dutyName='기존 운영 병원 ' + hpid,
                        dutyAddr='기존 주소', dutyTel1='031-000-0000',
                        wgs84Lat='37.0', wgs84Lon='127.0') for hpid in ('A001', 'A002')]
        public_data.apply_records(self.conn, NOW, basic_records=records)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def state(self):
        return {table: [tuple(r) for r in self.conn.execute('SELECT * FROM ' + table + ' ORDER BY 1')]
                for table in ('hospitals', 'resources', 'restrictions', 'public_api_snapshots', 'planning_state')}

    def test_complete_bundle_preserves_operational_values_and_other_source_ids(self):
        self.conn.execute("UPDATE hospitals SET source_ids=?,capabilities=?,capabilities_status='known' WHERE id=?",
                          (db.dumps({'nmc': 'A001', 'custom': 'KEEP'}), db.dumps(['소아청소년과']), 'NMC-A001'))
        service.set_resource(self.conn, NOW, 'NMC-A001', 'ed_bed', 5, 2)
        service.add_restriction(self.conn, NOW, 'NMC-A001', '산부인과', '담당자 확인')
        hospital_before = db.row(self.conn.execute("SELECT * FROM hospitals WHERE id='NMC-A001'").fetchone())
        resources_before = [tuple(r) for r in self.conn.execute('SELECT * FROM resources')]
        restrictions_before = [tuple(r) for r in self.conn.execute('SELECT * FROM restrictions')]
        result = hira_data.apply_bundles(self.conn, NOW, [bundle()])
        after = db.row(self.conn.execute("SELECT * FROM hospitals WHERE id='NMC-A001'").fetchone())
        self.assertEqual(result, {'hospitals_linked': 1, 'snapshots': 12})
        self.assertEqual(after['source_ids'], {'nmc': 'A001', 'custom': 'KEEP', 'hira': YKIHO})
        for field in ('name', 'address', 'phone', 'lat', 'lon', 'capabilities', 'capabilities_status', 'source'):
            self.assertEqual(after[field], hospital_before[field])
        self.assertEqual([tuple(r) for r in self.conn.execute('SELECT * FROM resources')], resources_before)
        self.assertEqual([tuple(r) for r in self.conn.execute('SELECT * FROM restrictions')], restrictions_before)
        snapshots = self.conn.execute("SELECT * FROM public_api_snapshots WHERE endpoint LIKE 'hira_%'").fetchall()
        self.assertEqual(len(snapshots), 12)
        self.assertTrue(all(r['source_observed_at'] is None for r in snapshots))
        self.assertTrue(all(r['fetched_at'] == NOW for r in snapshots))
        self.assertEqual(self.conn.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_reingest_updates_same_snapshots_without_duplicate_hospitals_or_rows(self):
        hira_data.apply_bundles(self.conn, NOW, [bundle()])
        second = bundle()
        second['records']['hira_specialists'][0]['dtlSdrCnt'] = '4'
        hira_data.apply_bundles(self.conn, NOW + 100, [second])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM hospitals').fetchone()[0], 2)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM public_api_snapshots WHERE endpoint LIKE 'hira_%'").fetchone()[0], 12)
        row = self.conn.execute("SELECT * FROM public_api_snapshots WHERE endpoint='hira_specialists'").fetchone()
        self.assertEqual(json.loads(row['normalized'])['specialists'][0]['reported_count'], 4)
        self.assertIsNone(row['source_observed_at'])

    def test_duplicate_hospital_in_batch_is_rejected_and_rolled_back(self):
        before = self.state()
        with self.assertRaises(hira_data.HiraDataError):
            hira_data.apply_bundles(self.conn, NOW, [bundle(), bundle(ykiho='OTHER')])
        self.assertEqual(self.state(), before)

    def test_duplicate_hira_id_in_batch_is_rejected_and_rolled_back(self):
        before = self.state()
        with self.assertRaises(hira_data.HiraDataError):
            hira_data.apply_bundles(self.conn, NOW, [bundle(), bundle(hid='NMC-A002')])
        self.assertEqual(self.state(), before)

    def test_existing_different_hira_link_is_not_overwritten(self):
        self.conn.execute("UPDATE hospitals SET source_ids=? WHERE id='NMC-A001'",
                          (db.dumps({'nmc': 'A001', 'hira': 'ORIGINAL'}),))
        before = self.state()
        with self.assertRaises(hira_data.HiraDataError):
            hira_data.apply_bundles(self.conn, NOW, [bundle()])
        self.assertEqual(self.state(), before)

    def test_hira_id_already_linked_to_other_hospital_is_rejected(self):
        self.conn.execute("UPDATE hospitals SET source_ids=? WHERE id='NMC-A002'",
                          (db.dumps({'nmc': 'A002', 'hira': YKIHO}),))
        before = self.state()
        with self.assertRaises(hira_data.HiraDataError):
            hira_data.apply_bundles(self.conn, NOW, [bundle()])
        self.assertEqual(self.state(), before)

    def test_missing_or_extra_endpoint_cannot_create_partial_bundle(self):
        for mode in ('missing', 'missing_new', 'extra'):
            with self.subTest(mode=mode):
                value = bundle()
                if mode == 'missing':
                    del value['records']['hira_equipment']
                elif mode == 'missing_new':
                    del value['records']['hira_meal_additions']
                else:
                    value['records']['unexpected'] = []
                before = self.state()
                with self.assertRaises(hira_data.HiraDataError):
                    hira_data.apply_bundles(self.conn, NOW, [value])
                self.assertEqual(self.state(), before)

    def test_mismatched_basis_or_detail_id_is_rejected(self):
        for endpoint in ('hira_hospital_basis', 'hira_specialists', 'hira_other_personnel'):
            with self.subTest(endpoint=endpoint):
                value = bundle()
                value['records'][endpoint][0]['ykiho'] = 'WRONG'
                before = self.state()
                with self.assertRaises(hira_data.HiraDataError):
                    hira_data.apply_bundles(self.conn, NOW, [value])
                self.assertEqual(self.state(), before)
        # bundle의 매칭 주장만 믿지 않고 현재 이름·주소·좌표를 다시 검사한다.
        for field, wrong in (('yadmNm', '다른 병원'), ('addr', '다른 주소'), ('XPos', '128.0'),
                             ('YPos', ''), ('XPos', 'NaN')):
            with self.subTest(field=field, wrong=wrong):
                value = bundle()
                value['records']['hira_hospital_basis'][0][field] = wrong
                before = self.state()
                with self.assertRaises(hira_data.HiraDataError):
                    hira_data.apply_bundles(self.conn, NOW, [value])
                self.assertEqual(self.state(), before)

    def test_late_second_bundle_failure_rolls_back_first_bundle_and_links(self):
        before = self.state()
        second = bundle(hid='NMC-A002', ykiho='SECOND')
        second['records']['hira_special_care'][0]['ykiho'] = 'WRONG'
        with self.assertRaises(hira_data.HiraDataError):
            hira_data.apply_bundles(self.conn, NOW, [bundle(), second])
        self.assertEqual(self.state(), before)

    def test_new_endpoint_raw_records_are_preserved_without_unverified_field_interpretation(self):
        value = bundle()
        original = {'emyDayYn':'1', 'trmtMonStart':'0830', 'providerFutureField':'원문 보존'}
        value['records']['hira_operating_details'] = [original]
        hira_data.apply_bundles(self.conn, NOW, [value])
        snapshot = self.conn.execute(
            "SELECT normalized,raw_data FROM public_api_snapshots WHERE endpoint='hira_operating_details'").fetchone()
        self.assertEqual(json.loads(snapshot['raw_data']), {'items':[original]})
        normalized = json.loads(snapshot['normalized'])
        self.assertIsNone(normalized['operating_details'][0]['emergency_department']['day']['reported_operating'])
        self.assertNotIn('providerFutureField', normalized)

    def test_unknown_or_nonpublic_hospital_is_not_auto_created_or_linked(self):
        self.conn.execute("UPDATE hospitals SET source='hospital_input' WHERE id='NMC-A002'")
        for hid in ('UNKNOWN', 'NMC-A002'):
            with self.subTest(hid=hid):
                before = self.state()
                with self.assertRaises(hira_data.HiraDataError):
                    hira_data.apply_bundles(self.conn, NOW, [bundle(hid=hid)])
                self.assertEqual(self.state(), before)

    def test_missing_hira_identity_cannot_be_treated_as_verified_pair(self):
        for missing in (None, ''):
            with self.subTest(missing=missing):
                value = bundle(ykiho=missing)
                value['records']['hira_hospital_basis'] = [{'yadmNm': '기관 ID 없음'}]
                before = self.state()
                with self.assertRaises(hira_data.HiraDataError):
                    hira_data.apply_bundles(self.conn, NOW, [value])
                self.assertEqual(self.state(), before)
        for field, invalid in (('ykiho', 123), ('ykiho', ' '), ('hospital_id', None), ('hospital_id', ''),
                               ('records', None), ('records', [])):
            with self.subTest(field=field, invalid=invalid):
                value = bundle()
                value[field] = invalid
                before = self.state()
                with self.assertRaises(hira_data.HiraDataError):
                    hira_data.apply_bundles(self.conn, NOW, [value])
                self.assertEqual(self.state(), before)
        for invalid in (None, {}, ['not a record']):
            with self.subTest(records=invalid):
                value = bundle()
                value['records']['hira_departments'] = invalid
                before = self.state()
                with self.assertRaises(hira_data.HiraDataError):
                    hira_data.apply_bundles(self.conn, NOW, [value])
                self.assertEqual(self.state(), before)


if __name__ == '__main__':
    unittest.main()
