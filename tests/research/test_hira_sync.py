"""네트워크·실 인증키 없이 심평원 수집 조정·보고·실패 보존을 시험한다."""
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server import db, hira_data, service, sync_hira


NOW = 1_790_000_000


def hospital(number):
    return dict(id=f'NMC-A{number:03}', name=f'가상 {number} 병원',
                address=f'경기도 평택시 시험로 {number}', phone='031-000-0100',
                lat=36.99 + number / 100, lon=127.11, capabilities=[],
                capabilities_status='unknown', source='public', source_ids={'nmc': f'A{number:03}'})


def basis(number, **changes):
    h = hospital(number)
    record = dict(ykiho=f'FIXTURE-HIRA-{number}', yadmNm=h['name'], addr=h['address'],
                  telno='031-000-0999', XPos=str(h['lon']), YPos=str(h['lat']),
                  drTotCnt='20', mdeptSdrCnt='10')
    record.update(changes)
    return record


DETAILS = {
    'hira_departments': [dict(dgsbjtCd='05', dgsbjtCdNm='정형외과', dgsbjtPrSdrCnt='3')],
    'hira_specialists': [dict(dgsbjtCd='05', dgsbjtCdNm='정형외과', dtlSdrCnt='2')],
    'hira_equipment': [dict(oftCd='CT', oftCdNm='가상 CT', oftCnt='2')],
    'hira_facilities': [dict(emymCnt='10', soprmCnt='2', partumCnt='1', nbySprmCnt='0')],
    'hira_special_care': [dict(srchCd='TEST', srchCdNm='가상 특수진료')],
    'hira_operating_details': [dict(emyDayYn='Y', emyNgtYn='Y', emyNgtTelNo1='031-000-0200')],
    'hira_designated_specialties': [dict(srchCd='TEST-SPECIALTY', srchCdNm='가상 지정분야')],
    'hira_nursing_grades': [dict(tyCd='TEST-NURSE', tyCdNm='가상 간호분야', careGrd='1')],
    'hira_other_personnel': [dict(gnlNopDtlCd='TEST-PHARM', dtlGnlNopCdNm='약사', gnlNopCnt='4')],
    'hira_transport': [dict(trafNm='시내버스', lineNo='100')],
    'hira_meal_additions': [dict(tyCd='TEST-MEAL', tyCdNm='가상 식대구분', trmealGrd='1', calcNopCnt='2')],
}


class FakeClient:
    def __init__(self, records, failures=None, return_all_basis=False, spatial_records=None):
        self.records = copy.deepcopy(records)
        self.spatial_records = copy.deepcopy(spatial_records or [])
        self.failures = failures or {}
        self.return_all_basis = return_all_basis
        self.calls = []

    def fetch_all(self, endpoint, **params):
        self.calls.append((endpoint, params.copy()))
        identifier = params.get('ykiho', params.get('yadmNm', 'spatial'))
        failure = self.failures.get((endpoint, identifier))
        if failure is not None:
            raise failure
        if endpoint == 'hira_hospital_basis':
            if 'yadmNm' not in params:
                return copy.deepcopy(self.spatial_records)
            records = self.records if self.return_all_basis else [r for r in self.records if r['yadmNm'] == params['yadmNm']]
            return copy.deepcopy(records)
        return copy.deepcopy(DETAILS[endpoint])


class HiraSync(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.base = Path(self.directory.name)
        self.path = self.base / 'public.db'
        self.conn = db.connect(str(self.path))
        self.addCleanup(self.conn.close)
        db.init(self.conn)

    def add_hospitals(self, *numbers):
        for number in numbers:
            service.upsert_hospital(self.conn, hospital(number))

    def sync(self, client, now=NOW):
        return sync_hira.sync_once(self.path, client=client, clock=lambda: now)

    def snapshots(self):
        return [dict(row) for row in self.conn.execute('SELECT * FROM public_api_snapshots ORDER BY hospital_id,endpoint')]

    def last_run(self):
        row = dict(self.conn.execute('SELECT * FROM hira_sync_runs ORDER BY id DESC LIMIT 1').fetchone())
        row['stats'] = json.loads(row['stats'])
        return row

    def test_success_collects_all_eleven_details_stores_twelve_observations_and_preserves_operations(self):
        self.add_hospitals(1)
        service.set_resource(self.conn, NOW, 'NMC-A001', 'ed_bed', 15, 9)
        resource = dict(self.conn.execute('SELECT * FROM resources').fetchone())
        client = FakeClient([basis(1)])
        report = self.sync(client)
        self.assertEqual((report['status'], report['hospitals_linked'], report['snapshots']), ('SUCCESS', 1, 12))
        self.assertEqual({endpoint for endpoint, _ in client.calls}, set(hira_data.OPERATIONS))
        self.assertEqual(len(hira_data.DETAIL_ENDPOINTS), 11)
        snapshots = self.snapshots()
        self.assertEqual(len(snapshots), 12)
        self.assertTrue(all(row['source_observed_at'] is None for row in snapshots))
        self.assertTrue(all(json.loads(row['normalized'])['data_kind'] == 'registered' for row in snapshots))
        stored = db.row(self.conn.execute('SELECT * FROM hospitals').fetchone())
        self.assertEqual(stored['source_ids'], {'nmc': 'A001', 'hira': 'FIXTURE-HIRA-1'})
        self.assertEqual((stored['capabilities'], stored['capabilities_status']), ([], 'unknown'))
        self.assertEqual(dict(self.conn.execute('SELECT * FROM resources').fetchone()), resource)
        self.assertTrue(report['matches'][0]['evidence']['strict_match'])
        self.assertEqual((self.last_run()['status'], self.last_run()['stats']['snapshots']), ('SUCCESS', 12))

    def test_identical_basis_rows_across_name_searches_are_deduplicated(self):
        self.add_hospitals(1, 2)
        client = FakeClient([basis(1), basis(1), basis(2), basis(2)], return_all_basis=True)
        report = self.sync(client)
        self.assertEqual(report['status'], 'SUCCESS')
        self.assertEqual(len(report['candidate_records']), 2)
        self.assertEqual(len(report['matches']), 2)
        self.assertEqual(report['ambiguous'], [])
        self.assertEqual(report['snapshots'], 24)
        self.assertEqual(sum(endpoint == 'hira_hospital_basis' for endpoint, _ in client.calls), 2)

    def test_same_hira_id_with_different_basis_values_remains_ambiguous(self):
        self.add_hospitals(1)
        report = self.sync(FakeClient([basis(1), basis(1, telno='031-000-0888')]))
        self.assertEqual(report['status'], 'PARTIAL')
        self.assertEqual(report['matches'], [])
        self.assertEqual(len(report['ambiguous']), 1)
        self.assertIn('DUPLICATE_HIRA_ID', report['ambiguous'][0]['reasons'])
        self.assertEqual(self.snapshots(), [])

    def test_unmatched_address_is_reported_without_guessing_hira_id(self):
        self.add_hospitals(1)
        client = FakeClient([basis(1, addr='경기도 안성시 다른로 99')])
        report = self.sync(client)
        self.assertEqual(report['status'], 'PARTIAL')
        self.assertEqual((report['hospitals_linked'], report['snapshots']), (0, 0))
        self.assertIn('ADDRESS_MISMATCH', report['unmatched'][0]['reasons'])
        self.assertEqual(len(client.calls), 1)
        self.assertNotIn('hira', db.row(self.conn.execute('SELECT * FROM hospitals').fetchone())['source_ids'])

    def test_spatial_fallback_recovers_whitespace_name_after_all_name_searches(self):
        self.add_hospitals(1, 2)
        compact = hospital(1)
        compact['name'] = '가상1병원'
        service.upsert_hospital(self.conn, compact)
        client = FakeClient([basis(2)], spatial_records=[basis(1), basis(2), basis(1)])
        report = self.sync(client)
        self.assertEqual((report['status'], report['hospitals_linked'], report['snapshots']), ('SUCCESS', 2, 24))
        self.assertEqual(len(report['candidate_records']), 2)
        expected_params = {'xPos':str(compact['lon']), 'yPos':str(compact['lat']), 'radius':'1000'}
        self.assertEqual(client.calls[:3], [
            ('hira_hospital_basis', {'yadmNm':'가상1병원'}),
            ('hira_hospital_basis', {'yadmNm':hospital(2)['name']}),
            ('hira_hospital_basis', expected_params),
        ])
        self.assertTrue(all(endpoint != 'hira_hospital_basis' for endpoint, _ in client.calls[3:]))
        self.assertEqual(report['spatial_searches'], [
            {'hospital_id':compact['id'], **expected_params, 'candidate_count':3}])
        self.assertTrue(all(match['evidence']['strict_match'] for match in report['matches']))

    def test_query_derived_distance_does_not_create_duplicate_identity(self):
        self.add_hospitals(1, 2)
        client = FakeClient([basis(2)], spatial_records=[basis(1, distance='12'),
                            basis(2, distance='113'), basis(2, distance='114')])
        report = self.sync(client)
        self.assertEqual((report['status'], report['hospitals_linked']), ('SUCCESS', 2))
        self.assertEqual(len(report['candidate_records']), 2)
        self.assertEqual(report['ambiguous'], [])
        preserved = next(r for r in report['candidate_records'] if r['ykiho'] == 'FIXTURE-HIRA-2')
        self.assertNotIn('distance', preserved)
        self.assertEqual(next(r for r in report['candidate_records']
                              if r['ykiho'] == 'FIXTURE-HIRA-1')['distance'], '12')

    def test_spatial_record_substantive_conflicts_remain_ambiguous(self):
        self.add_hospitals(1, 2)
        client = FakeClient([basis(2)], spatial_records=[basis(1),
                            basis(2, telno='031-000-0888', distance='50')])
        report = self.sync(client)
        self.assertEqual((report['status'], report['hospitals_linked']), ('PARTIAL', 1))
        self.assertEqual(len(report['candidate_records']), 3)
        self.assertEqual(report['ambiguous'][0]['hospital_id'], 'NMC-A002')
        self.assertIn('DUPLICATE_HIRA_ID', report['ambiguous'][0]['reasons'])

    def test_spatial_fallback_does_not_relax_full_name_identity(self):
        self.add_hospitals(1)
        client = FakeClient([], spatial_records=[basis(1, yadmNm='다른법인가상 1 병원')])
        report = self.sync(client)
        self.assertEqual((report['status'], report['snapshots']), ('PARTIAL', 0))
        self.assertEqual(len(client.calls), 2)
        self.assertIn('NO_EXACT_NAME_MATCH', report['unmatched'][0]['reasons'])
        self.assertEqual(self.snapshots(), [])

    def test_spatial_search_radius_does_not_relax_300_meter_identity_limit(self):
        self.add_hospitals(1)
        client = FakeClient([], spatial_records=[basis(1, YPos=str(hospital(1)['lat'] + 0.006))])
        report = self.sync(client)
        self.assertEqual((report['status'], report['snapshots']), ('PARTIAL', 0))
        self.assertEqual(len(client.calls), 2)
        self.assertIn('DISTANCE_EXCEEDED', report['unmatched'][0]['reasons'])
        evidence = report['unmatched'][0]['candidates'][0]['evidence']
        self.assertGreater(evidence['distance_meters'], 300)
        self.assertEqual(evidence['maximum_distance_meters'], 300)

    def test_spatial_fallback_does_not_relax_address_identity(self):
        self.add_hospitals(1)
        client = FakeClient([], spatial_records=[basis(1, addr='경기도 평택시 다른로 999')])
        report = self.sync(client)
        self.assertEqual((report['status'], report['snapshots']), ('PARTIAL', 0))
        self.assertIn('ADDRESS_MISMATCH', report['unmatched'][0]['reasons'])
        self.assertEqual(self.snapshots(), [])

    def test_spatial_params_reject_invalid_coordinates_and_preserve_axis_order(self):
        invalid = [(None, 127), ('bad', 127), (True, 127), (37, False),
                   (float('nan'), 127), (37, float('inf')), (91, 127),
                   (37, 181), (0, 0)]
        for lat, lon in invalid:
            with self.subTest(lat=lat, lon=lon):
                self.assertIsNone(sync_hira._spatial_search_params({'lat':lat, 'lon':lon}))
        self.assertEqual(sync_hira._spatial_search_params({'lat':'37.1', 'lon':'127.2'}),
                         {'xPos':'127.2', 'yPos':'37.1', 'radius':'1000'})

    def test_unmatched_invalid_coordinates_skip_spatial_network_query(self):
        self.add_hospitals(1)
        invalid = {**hospital(1), 'lat':float('nan')}
        client = FakeClient([], spatial_records=[basis(1)])
        original_rows = db.rows
        def invalid_rows(cursor):
            records = original_rows(cursor)
            # db 모듈은 대화 마이그레이션도 공유하므로 병원 조회만 변형한다.
            return [invalid] if records and 'source' in records[0] else records
        with patch.object(sync_hira.db, 'rows', side_effect=invalid_rows):
            report = self.sync(client)
        self.assertEqual((report['status'], report['snapshots']), ('PARTIAL', 0))
        self.assertEqual(client.calls, [('hira_hospital_basis', {'yadmNm':hospital(1)['name']})])
        self.assertEqual(report['spatial_searches'], [])

    def test_spatial_access_denied_stops_before_detail_calls_and_records_error(self):
        self.add_hospitals(1, 2)
        client = FakeClient([basis(2)], failures={
            ('hira_hospital_basis', 'spatial'): hira_data.HiraDataError('심평원 API HTTP 오류: 403', access_denied=True)})
        report = self.sync(client)
        self.assertEqual((report['status'], report['snapshots']), ('ERROR', 0))
        self.assertEqual(len(client.calls), 3)
        self.assertTrue(all(endpoint == 'hira_hospital_basis' for endpoint, _ in client.calls))
        self.assertEqual(report['errors'], [{'error':'심평원 API HTTP 오류: 403'}])

    def test_one_detail_failure_keeps_other_hospital_bundle_and_reports_partial(self):
        self.add_hospitals(1, 2)
        client = FakeClient([basis(1), basis(2)], failures={
            ('hira_equipment', 'FIXTURE-HIRA-1'): hira_data.HiraDataError('가상 상세 수집 실패')})
        report = self.sync(client)
        self.assertEqual((report['status'], report['hospitals_linked'], report['snapshots']), ('PARTIAL', 1, 12))
        self.assertEqual(report['errors'][0]['hospital_id'], 'NMC-A001')
        self.assertEqual({s['hospital_id'] for s in self.snapshots()}, {'NMC-A002'})
        self.assertNotIn('hira', db.row(self.conn.execute("SELECT * FROM hospitals WHERE id='NMC-A001'").fetchone())['source_ids'])
        self.assertEqual(self.last_run()['status'], 'PARTIAL')

    def test_first_detail_403_stops_further_endpoint_and_hospital_calls(self):
        self.add_hospitals(1, 2)
        client = FakeClient([basis(1), basis(2)], failures={
            ('hira_departments', 'FIXTURE-HIRA-1'): hira_data.HiraDataError('심평원 API HTTP 오류: 403', access_denied=True)})
        report = self.sync(client)
        self.assertEqual((report['status'], report['snapshots']), ('ERROR', 0))
        details = [(endpoint, params) for endpoint, params in client.calls if endpoint != 'hira_hospital_basis']
        self.assertEqual(details, [('hira_departments', {'ykiho': 'FIXTURE-HIRA-1'})])
        self.assertEqual(self.last_run()['error'], '심평원 API HTTP 오류: 403')

    def test_later_access_denied_keeps_prior_complete_bundle(self):
        self.add_hospitals(1, 2, 3)
        client = FakeClient([basis(1), basis(2), basis(3)], failures={
            ('hira_specialists', 'FIXTURE-HIRA-2'): hira_data.HiraDataError('심평원 API HTTP 오류: 403', access_denied=True)})
        report = self.sync(client)
        self.assertEqual((report['status'], report['hospitals_linked'], report['snapshots']), ('PARTIAL', 1, 12))
        self.assertEqual({s['hospital_id'] for s in self.snapshots()}, {'NMC-A001'})
        self.assertFalse(any(params.get('ykiho') == 'FIXTURE-HIRA-3' for _, params in client.calls))

    def test_failed_refresh_preserves_previous_hira_observations(self):
        self.add_hospitals(1)
        self.sync(FakeClient([basis(1)]))
        before = self.snapshots()
        client = FakeClient([basis(1)], failures={
            ('hira_facilities', 'FIXTURE-HIRA-1'): hira_data.HiraDataError('가상 시설 조회 실패')})
        report = self.sync(client, now=NOW + 60)
        self.assertEqual(report['status'], 'PARTIAL')
        self.assertEqual((report['hospitals_linked'], report['snapshots']), (0, 0))
        self.assertEqual(self.snapshots(), before)
        self.assertEqual(db.row(self.conn.execute('SELECT * FROM hospitals').fetchone())['source_ids']['hira'], 'FIXTURE-HIRA-1')

    def test_last_new_endpoint_failure_preserves_all_previous_observations_for_that_hospital(self):
        self.add_hospitals(1, 2)
        self.sync(FakeClient([basis(1), basis(2)]))
        old_first = [s for s in self.snapshots() if s['hospital_id'] == 'NMC-A001']
        client = FakeClient([basis(1), basis(2)], failures={
            ('hira_meal_additions','FIXTURE-HIRA-1'):hira_data.HiraDataError('가상 식대 조회 실패')})
        report = self.sync(client, now=NOW + 60)
        self.assertEqual((report['status'], report['hospitals_linked'], report['snapshots']), ('PARTIAL',1,12))
        self.assertEqual(report['errors'], [{'hospital_id':'NMC-A001','endpoint':'hira_meal_additions',
                                            'error':'가상 식대 조회 실패'}])
        self.assertEqual([s for s in self.snapshots() if s['hospital_id']=='NMC-A001'], old_first)
        second = [s for s in self.snapshots() if s['hospital_id']=='NMC-A002']
        self.assertEqual(len(second),12)
        self.assertTrue(all(s['fetched_at']==NOW+60 for s in second))
        self.assertEqual([endpoint for endpoint,params in client.calls if params.get('ykiho')=='FIXTURE-HIRA-1'],
                         list(hira_data.DETAIL_ENDPOINTS))

    def test_basis_access_denied_stops_queries_and_records_controlled_error(self):
        self.add_hospitals(1, 2)
        client = FakeClient([basis(1), basis(2)], failures={
            ('hira_hospital_basis', hospital(1)['name']): hira_data.HiraDataError('심평원 API HTTP 오류: 403', access_denied=True)})
        report = self.sync(client)
        self.assertEqual(report['status'], 'ERROR')
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(report['errors'], [{'error': '심평원 API HTTP 오류: 403'}])
        self.assertEqual(self.snapshots(), [])

    def test_unexpected_exception_does_not_leak_url_or_fake_secret_in_report_and_log(self):
        self.add_hospitals(1)
        client = FakeClient([basis(1)], failures={
            ('hira_equipment', 'FIXTURE-HIRA-1'): RuntimeError('https://fixture.invalid/?serviceKey=FAKE-PRIVATE-TOKEN')})
        report = self.sync(client)
        logged = json.dumps([report, self.last_run()], ensure_ascii=False)
        self.assertEqual(report['status'], 'ERROR')
        self.assertIn('심평원 수집·매칭·적재 처리에 실패했습니다', logged)
        self.assertNotIn('fixture.invalid', logged)
        self.assertNotIn('FAKE-PRIVATE-TOKEN', logged)
        self.assertEqual(self.snapshots(), [])

    def test_no_public_hospitals_fails_before_any_client_call(self):
        client = FakeClient([])
        report = self.sync(client)
        self.assertEqual(report['status'], 'ERROR')
        self.assertEqual(report['requested_hospitals'], 0)
        self.assertEqual(client.calls, [])
        self.assertIn('공공 병원 목록이 없습니다', report['errors'][0]['error'])

    def test_cli_missing_database_rejected_without_creating_database_or_loading_key(self):
        path = self.base / 'does-not-exist.db'
        output = self.base / 'not-created'
        with patch.object(sync_hira, 'HiraClient') as client, patch('sys.stderr', new_callable=io.StringIO):
            with self.assertRaises(SystemExit) as cm:
                sync_hira.main(['--db', str(path), '--out', str(output)])
        self.assertEqual(cm.exception.code, 2)
        client.assert_not_called()
        self.assertFalse(path.exists())
        self.assertFalse(output.exists())

    def test_cli_missing_key_rejected_without_writing_report_or_run_log(self):
        self.add_hospitals(1)
        output = self.base / 'not-created'
        with patch.object(sync_hira, 'HiraClient', side_effect=hira_data.HiraDataError('DATA_GO_KR_KEY 환경변수를 설정하세요')), \
                patch('sys.stderr', new_callable=io.StringIO):
            with self.assertRaises(SystemExit) as cm:
                sync_hira.main(['--db', str(self.path), '--out', str(output)])
        self.assertEqual(cm.exception.code, 2)
        self.assertFalse(output.exists())
        self.assertIsNone(self.conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='hira_sync_runs'").fetchone())

    def test_cli_success_creates_report_pointer_and_summary(self):
        self.add_hospitals(1)
        output = self.base / 'reports'
        client = FakeClient([basis(1)])
        with patch.object(sync_hira, 'HiraClient', return_value=client), patch('sys.stdout', new_callable=io.StringIO) as stdout:
            status = sync_hira.main(['--db', str(self.path), '--out', str(output)])
        self.assertEqual(status, 0)
        pointer = json.loads((output / 'latest.json').read_text(encoding='utf-8'))
        report = json.loads(Path(pointer['report']).read_text(encoding='utf-8'))
        self.assertEqual((report['status'], report['snapshots']), ('SUCCESS', 12))
        lines = [json.loads(line) for line in stdout.getvalue().splitlines()]
        self.assertEqual(lines[0]['status'], 'SUCCESS')
        self.assertEqual(lines[1]['report'], pointer['report'])

    def test_cli_partial_creates_reviewable_report_and_returns_one(self):
        self.add_hospitals(1)
        output = self.base / 'reports'
        client = FakeClient([basis(1, XPos='')])
        with patch.object(sync_hira, 'HiraClient', return_value=client), patch('sys.stdout', new_callable=io.StringIO):
            status = sync_hira.main(['--db', str(self.path), '--out', str(output)])
        self.assertEqual(status, 1)
        pointer = json.loads((output / 'latest.json').read_text(encoding='utf-8'))
        report = json.loads(Path(pointer['report']).read_text(encoding='utf-8'))
        self.assertEqual(report['status'], 'PARTIAL')
        self.assertIn('COORDINATES_MISSING_OR_INVALID', report['unmatched'][0]['reasons'])
        self.assertEqual(report['snapshots'], 0)


if __name__ == '__main__':
    unittest.main()
