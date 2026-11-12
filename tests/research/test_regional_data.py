"""권역 수집·상세 원문 보존·재현 가능한 스냅샷 회귀 시험.

실 API, 인증키, 사용자 환경변수 또는 레지스트리를 읽지 않는다.
"""
import copy
from contextlib import closing
import hashlib
import io
import json
import sqlite3
import tempfile
import unittest
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from server import db, public_data, service, sync_region


ROOT = Path(__file__).resolve().parents[2]
NOW = int(datetime(2026, 10, 11, 3, 5, tzinfo=timezone.utc).timestamp())


def hospital(hpid='A000001', address='경기도 평택시 중앙로 1', **changes):
    value = dict(hpid=hpid, dutyName='가상 검증용 병원', dutyAddr=address,
                 dutyTel1='031-000-0001', dutyTel3='031-000-0002',
                 wgs84Lat='36.99', wgs84Lon='127.11')
    value.update(changes)
    return value


def beds(hpid='A000001', **changes):
    value = dict(hpid=hpid, hvec='7', hvoc='2', hvncc='0', hv42='Y',
                 hvidate='20261011120000', hvctayn='Y', hvventiayn='N')
    value.update(changes)
    return value


def detail(hpid='A000001', **changes):
    value = dict(hpid=hpid, dgidIdName='응급의학과, 정형외과, 산부인과',
                 hperyn='12', hpopyn='3', hpnicuyn='0', dutyEryn='1')
    value.update(changes)
    return value


def severe(hpid='A000001', **changes):
    value = dict(hpid=hpid, MKioskTy1='Y', MKioskTy1Msg='가상 조건 확인 필요',
                 mkioskty1='N')
    value.update(changes)
    return value


def region_spec():
    return sync_region.load_region(ROOT / 'config' / 'experiment_region.json')


def treatment_spec():
    return {'profiles': [dict(id='TEST_ED', required_capabilities=['응급의학과'],
                              resource_demands={'ed_bed': 1},
                              human_confirmation=['해당 환자 수용'])]}


def success_runs(spec):
    return [{**value, 'status': 'SUCCESS'} for value in spec['candidate_districts']]


class RegionConfiguration(unittest.TestCase):
    def test_default_has_two_origins_eight_unique_candidate_cities(self):
        spec = region_spec()
        pairs = {(d['region'], d['district']) for d in spec['candidate_districts']}
        self.assertEqual(len(pairs), 8)
        self.assertEqual({d['district'] for d in spec['origin_districts']}, {'안성시', '평택시'})
        self.assertEqual({d['district'] for d in spec['candidate_districts']},
                         {'안성시', '평택시', '오산시', '화성시', '용인시', '수원시', '천안시', '아산시'})
        self.assertTrue({(d['region'], d['district']) for d in spec['origin_districts']} <= pairs)

    def load(self, value):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'region.json'
            path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
            return sync_region.load_region(path)

    def test_duplicate_districts_after_whitespace_trim_rejected(self):
        spec = region_spec()
        spec['candidate_districts'].append({'region': ' 경기도 ', 'district': ' 평택시 '})
        with self.assertRaises(ValueError):
            self.load(spec)

    def test_origin_outside_candidate_scope_and_invalid_district_rejected(self):
        spec = region_spec()
        spec['origin_districts'].append({'region': '서울특별시', 'district': '강남구'})
        with self.assertRaises(ValueError):
            self.load(spec)
        for value in ([], [{'region': '경기도', 'district': ''}], [None]):
            with self.subTest(value=value):
                spec = region_spec()
                spec['candidate_districts'] = value
                with self.assertRaises(ValueError):
                    self.load(spec)

    def test_address_district_requires_city_boundary_and_matching_province(self):
        spec = region_spec()
        for address, expected in (('경기도 평택시 중앙로 1', '평택시'),
                                  ('경기도 수원시 영통구 테스트로 1', '수원시'),
                                  ('충청남도 천안시 동남구 테스트로 1', '천안시'),
                                  ('경기도 안성시', '안성시')):
            with self.subTest(address=address):
                self.assertEqual(sync_region.address_district(address, spec)['district'], expected)
        for address in ('경기도 평택시청길 1', '충청남도 평택시 중앙로 1',
                        '서울특별시 강남구 테스트로 1', '', None):
            with self.subTest(address=address):
                self.assertIsNone(sync_region.address_district(address, spec))

    def test_severe_api_uses_required_district_filter_without_live_network(self):
        calls = []

        def opener(request, timeout):
            url = urllib.parse.urlsplit(request.full_url)
            calls.append((url.path, urllib.parse.parse_qs(url.query)))
            return io.BytesIO(b'<response><header><resultCode>00</resultCode></header>'
                              b'<body><items></items><totalCount>0</totalCount>'
                              b'<pageNo>1</pageNo></body></response>')

        client = public_data.NmcClient(key='fixture-not-a-real-key', district='평택시', opener=opener)
        self.assertEqual(client.fetch_all('nmc_severe_accept', '경기도'), [])
        self.assertTrue(calls[0][0].endswith('/getSrsillDissAceptncPosblInfoInqire'))
        self.assertEqual(calls[0][1]['STAGE1'], ['경기도'])
        self.assertEqual(calls[0][1]['STAGE2'], ['평택시'])


class RegionalDbFixture(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.base = Path(self.directory.name)
        self.path = self.base / 'public.db'
        self.conn = db.connect(str(self.path))
        self.addCleanup(self.conn.close)
        db.init(self.conn)

    def ingest(self, hpid='A000001', address='경기도 평택시 중앙로 1'):
        return public_data.apply_records(self.conn, NOW, [hospital(hpid, address)],
                                         [beds(hpid)], [detail(hpid)], [severe(hpid)])

    def state(self):
        tables = ('hospitals', 'resources', 'restrictions', 'patients', 'inquiries',
                  'events', 'public_api_snapshots', 'planning_state')
        return {table: [tuple(r) for r in self.conn.execute('SELECT * FROM ' + table)] for table in tables}


class RegionalDbTest(RegionalDbFixture):
    def test_detail_and_severe_observations_do_not_infer_operating_capacity(self):
        stats = self.ingest()
        self.assertEqual(stats['snapshots'], 4)
        row = db.row(self.conn.execute('SELECT * FROM hospitals').fetchone())
        self.assertEqual(row['capabilities_status'], 'unknown')
        self.assertEqual(row['capabilities'], [])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM resources').fetchone()[0], 0)
        observations = {r['endpoint']: r for r in public_data.snapshot_view(self.conn, row['id'], NOW)}
        self.assertEqual(observations['nmc_hospital_detail']['data']['departments'],
                         ['응급의학과', '정형외과', '산부인과'])
        severe_data = observations['nmc_severe_accept']['data']
        self.assertEqual(severe_data['code_version'], 'unverified')
        self.assertEqual({r['source_field']: r['reported_value'] for r in severe_data['fields']},
                         {'MKioskTy1': 'Y', 'mkioskty1': 'N'})
        raw = json.loads(self.conn.execute("SELECT raw_data FROM public_api_snapshots "
                                          "WHERE endpoint='nmc_severe_accept'").fetchone()[0])
        self.assertEqual(raw, severe())

    def test_reingest_preserves_manual_operating_resources_restrictions_and_capabilities(self):
        self.ingest()
        hid = 'NMC-A000001'
        self.conn.execute("UPDATE hospitals SET capabilities=?,capabilities_status='known' WHERE id=?",
                          (db.dumps(['정형외과']), hid))
        service.set_resource(self.conn, NOW, hid, 'ed_bed', 20, 13)
        service.add_restriction(self.conn, NOW, hid, '산부인과', '가상 당직 제한')
        before = self.state()
        public_data.apply_records(self.conn, NOW + 1,
                                  [hospital(dutyName='갱신된 공개 병원명')], [beds(hvec='1')],
                                  [detail(dgidIdName='외과')], [severe(MKioskTy1='N')])
        after = self.state()
        for table in ('resources', 'restrictions', 'patients', 'inquiries', 'events'):
            self.assertEqual(before[table], after[table], table)
        row = db.row(self.conn.execute('SELECT * FROM hospitals').fetchone())
        self.assertEqual((row['capabilities'], row['capabilities_status']), (['정형외과'], 'known'))
        self.assertEqual(row['name'], '갱신된 공개 병원명')
        self.assertEqual(self.conn.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_duplicate_or_unmatched_detail_and_severe_roll_back_entire_apply(self):
        self.ingest()
        for details, severes in (([detail(), detail()], [severe()]),
                                  ([detail('OTHER')], [severe()]),
                                  ([detail()], [severe(), severe()]),
                                  ([detail()], [severe('OTHER')])):
            with self.subTest(details=details, severes=severes):
                before = self.state()
                with self.assertRaises(public_data.PublicDataError):
                    public_data.apply_records(self.conn, NOW + 20,
                                              [hospital(dutyName='반영되면 안 되는 이름')],
                                              [beds(hvec='1')], details, severes)
                self.assertEqual(self.state(), before)

    def test_repeated_same_hpid_across_collection_runs_has_one_hospital(self):
        self.ingest()
        self.ingest()
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM hospitals').fetchone()[0], 1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM public_api_snapshots').fetchone()[0], 4)

    def client(self, fail=None):
        class FakeClient:
            district = '평택시'

            def fetch_hospitals(inner, region):
                return [hospital(dutyName='새로 수집한 병원명')]

            def fetch_all(inner, operation, region):
                if operation == fail:
                    raise public_data.PublicDataError('가상 수집 실패')
                return [beds(hvec='1')] if operation == 'nmc_rltm_beds' else [severe()]

            def fetch_detail(inner, hpid):
                if fail == 'detail':
                    raise public_data.PublicDataError('가상 상세 수집 실패')
                return detail(hpid)
        return FakeClient()

    def test_sync_detail_or_severe_failure_preserves_previous_region_data(self):
        self.ingest()
        for failure in ('detail', 'nmc_severe_accept'):
            with self.subTest(failure=failure):
                before = self.state()
                with self.assertRaises(public_data.PublicDataError):
                    public_data.sync_once(self.path, '경기도', client=self.client(failure),
                                          include_details=True, include_severe=True, clock=lambda: NOW + 30)
                self.assertEqual(self.state(), before)
                run = dict(self.conn.execute('SELECT * FROM public_sync_runs ORDER BY id DESC LIMIT 1').fetchone())
                self.assertEqual((run['region'], run['district'], run['status']), ('경기도', '평택시', 'ERROR'))

    def test_sync_success_applies_list_beds_detail_and_severe_together(self):
        result = public_data.sync_once(self.path, '경기도', client=self.client(),
                                       include_details=True, include_severe=True, clock=lambda: NOW)
        self.assertEqual((result['status'], result['snapshots'], result['hospitals_created']), ('SUCCESS', 4, 1))
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM public_api_snapshots').fetchone()[0], 4)

    def test_details_without_basic_collection_rejected_without_data_change(self):
        self.ingest()
        before = self.state()
        with self.assertRaises(public_data.PublicDataError):
            public_data.sync_once(self.path, '경기도', client=self.client(), include_basic=False,
                                  include_details=True, clock=lambda: NOW)
        self.assertEqual(self.state(), before)


class RegionalSnapshot(RegionalDbFixture):
    def export(self, name='snapshot', runs=None):
        spec = region_spec()
        directory = self.base / name
        summary = sync_region.export_snapshot(self.path, directory, spec, treatment_spec(),
                                              success_runs(spec) if runs is None else runs, now=NOW)
        return directory, summary

    def test_delivery_service_flag_export_preserves_unknown_quantity(self):
        self.ingest()
        directory, summary = self.export()
        self.assertEqual(summary['delivery_room_service_flags'], {'yes':1})
        self.assertEqual(summary['delivery_room_missing_bed_observations'],0)
        profiles = json.loads((directory / 'hospital_profiles.json').read_text(encoding='utf-8'))
        bed_data = profiles[0]['observations']['nmc_rltm_beds']['data']
        self.assertIs(bed_data['service_availability'][0]['reported_available'], True)
        self.assertIsNone(next(r for r in bed_data['resources'] if r['resource_type']=='delivery_room')['reported_available'])
        self.conn.execute("DELETE FROM public_api_snapshots WHERE endpoint='nmc_rltm_beds'")
        _, summary = self.export('missing-beds')
        self.assertEqual(summary['delivery_room_service_flags'],{})
        self.assertEqual(summary['delivery_room_missing_bed_observations'],1)

    def test_hira_multirow_registration_exports_as_one_hospital_observation(self):
        from server import hira_data
        self.ingest()
        hospital_row = db.row(self.conn.execute('SELECT * FROM hospitals').fetchone())
        records = {endpoint:[] for endpoint in hira_data.OPERATIONS}
        records['hira_hospital_basis'] = [{'ykiho':'SYNTHETIC-HIRA', 'yadmNm':hospital_row['name'],
                                          'addr':hospital_row['address'], 'XPos':str(hospital_row['lon']),
                                          'YPos':str(hospital_row['lat'])}]
        records['hira_departments'] = [{'dgsbjtCd':'01','dgsbjtCdNm':'내과','dgsbjtPrSdrCnt':'2'},
                                       {'dgsbjtCd':'05','dgsbjtCdNm':'정형외과','dgsbjtPrSdrCnt':'1'}]
        hira_data.apply_bundles(self.conn,NOW,[{'hospital_id':hospital_row['id'],'ykiho':'SYNTHETIC-HIRA','records':records}])
        directory, summary = self.export()
        self.assertEqual(summary['hira_linked_hospital_count'],1)
        self.assertEqual(summary['endpoint_counts']['hira_departments'],1)
        self.assertEqual(summary['hira_expected_endpoint_count'],len(hira_data.OPERATIONS))
        self.assertEqual(summary['hira_endpoint_counts'],dict.fromkeys(hira_data.OPERATIONS,1))
        self.assertEqual(summary['hira_complete_hospital_count'],1)
        inventory = json.loads((directory / 'field_inventory.json').read_text(encoding='utf-8'))
        self.assertEqual(inventory['hira_departments']['dgsbjtCd'],{'present':1,'empty':0,'missing':0})
        profiles = json.loads((directory / 'hospital_profiles.json').read_text(encoding='utf-8'))
        observation = profiles[0]['observations']['hira_departments']
        self.assertIsNone(observation['source_observed_at'])
        self.assertEqual(observation['data']['data_kind'],'registered')
        self.assertEqual(observation['data']['record_count'],2)
        self.assertEqual(summary['operational_resource_count'],0)

    def test_export_uses_hira_department_fallback_in_treatment_judgment(self):
        from server import hira_data
        self.ingest()
        public_data.apply_records(self.conn,NOW,detail_records=[detail(dgidIdName='')])
        normalized = hira_data.normalize_records('hira_departments',[
            {'dgsbjtCd':'23','dgsbjtCdNm':'응급의학과','dgsbjtPrSdrCnt':'3'}])
        public_data._save_snapshot(self.conn,'NMC-A000001','hira_departments',NOW,None,
                                   normalized,{'items':[{'dgsbjtCd':'23','dgsbjtCdNm':'응급의학과'}]})
        directory, summary = self.export()
        profile = json.loads((directory / 'hospital_profiles.json').read_text(encoding='utf-8'))[0]
        result = profile['treatment_evaluations']['TEST_ED']
        self.assertEqual(result['status'],'NEEDS_CONFIRMATION')
        department = next(row for row in result['evidence'] if row['kind']=='department')
        self.assertEqual(department['status'],'MATCH')
        self.assertEqual(department['endpoint'],'hira_departments')
        self.assertEqual(department['registered_evidence'][0]['reported_count'],3)
        self.assertEqual(summary['hira_complete_hospital_count'],0)

    def test_nmc_multirow_messages_export_flat_fields_and_context_without_acceptance(self):
        from server import nmc_messages
        self.ingest()
        self.ingest('A000002','경기도 안성시 테스트로 1')
        records=[{'hpid':'A000001','symBlkMsg':'산과 확인 필요','symTypCod':'X'},
                 {'hpid':'A000001','symBlkMsg':'연령 확인 필요','symTypCod':'Y'}]
        nmc_messages.apply_bundles(self.conn,NOW,[
            {'hospital_id':'NMC-A000001','hpid':'A000001','records':records},
            {'hospital_id':'NMC-A000002','hpid':'A000002','records':[]}])
        directory,summary=self.export()
        message_summary=summary['nmc_acceptance_messages']
        self.assertEqual(message_summary['hospitals_collected'],2)
        self.assertEqual(message_summary['hospitals_missing'],0)
        self.assertEqual(message_summary['hospitals_with_messages'],1)
        self.assertEqual(message_summary['message_rows'],2)
        inventory=json.loads((directory/'field_inventory.json').read_text(encoding='utf-8'))
        self.assertEqual(inventory['nmc_acceptance_messages']['symBlkMsg'],
                         {'present':1,'empty':0,'missing':1})
        self.assertNotIn('items',inventory['nmc_acceptance_messages'])
        profiles=json.loads((directory/'hospital_profiles.json').read_text(encoding='utf-8'))
        result=profiles[0]['treatment_evaluations']['TEST_ED']
        messages=[row for row in result['operational_context'] if row['kind']=='acceptance_message']
        self.assertEqual(len(messages),2)
        self.assertTrue(all(row['freshness']=='unknown' for row in messages))
        self.assertEqual(result['status'],'NEEDS_CONFIRMATION')
        self.assertEqual(summary['operational_resource_count'],0)

    def test_message_collection_summary_distinguishes_missing_and_empty(self):
        from server import nmc_messages
        self.ingest()
        _,summary=self.export('missing-message')
        self.assertEqual(summary['nmc_acceptance_messages']['hospitals_collected'],0)
        self.assertEqual(summary['nmc_acceptance_messages']['hospitals_missing'],1)
        nmc_messages.apply_bundles(self.conn,NOW,[
            {'hospital_id':'NMC-A000001','hpid':'A000001','records':[]}])
        _,summary=self.export('empty-message')
        self.assertEqual(summary['nmc_acceptance_messages']['hospitals_collected'],1)
        self.assertEqual(summary['nmc_acceptance_messages']['hospitals_missing'],0)
        self.assertEqual(summary['nmc_acceptance_messages']['message_rows'],0)

    def test_export_message_latest_run_matches_frozen_database(self):
        from server import sync_messages
        self.ingest()
        class EmptyClient:
            def fetch_all(inner,hpid):
                return []
        report=sync_messages.sync_once(self.path,client=EmptyClient(),clock=lambda:NOW)
        _,summary=self.export()
        run=summary['nmc_acceptance_messages']['last_sync']
        self.assertEqual(run['id'],report['run_id'])
        self.assertEqual(run['status'],'SUCCESS')
        self.assertEqual(json.loads(run['stats'])['empty_results'],1)

    def test_manifest_hashes_message_binding_and_identity_configuration_when_present(self):
        self.ingest()
        fixture_root=self.base/'source-root'
        (fixture_root/'server').mkdir(parents=True)
        (fixture_root/'config').mkdir()
        names=('public_data.py','treatment_data.py','sync_region.py','hira_data.py',
               'hira_matching.py','sync_hira.py','nmc_messages.py','sync_messages.py','treatment_binding.py')
        for name in names:
            (fixture_root/'server'/name).write_text('synthetic source '+name,encoding='utf-8')
        identity=fixture_root/'config'/'hospital_identity_links.json'
        identity.write_text('{"links":[]}',encoding='utf-8')
        with patch.object(sync_region,'ROOT',fixture_root):
            directory,_=self.export()
        manifest=json.loads((directory/'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(set(manifest['normalizer_sha256']),set(names))
        for name,digest in manifest['normalizer_sha256'].items():
            self.assertEqual(digest,hashlib.sha256((fixture_root/'server'/name).read_bytes()).hexdigest())
        self.assertEqual(manifest['configuration_sha256']['config/hospital_identity_links.json'],
                         hashlib.sha256(identity.read_bytes()).hexdigest())

    def test_export_profiles_identify_out_of_scope_hospitals_and_zero_coverage(self):
        self.ingest()
        self.ingest('A000002', '충청남도 천안시 동남구 테스트로 1')
        self.ingest('A000003', '서울특별시 강남구 테스트로 1')
        directory, summary = self.export()
        self.assertEqual(summary['hospital_count'], 2)
        self.assertEqual(len(summary['outside_scope']), 1)
        self.assertEqual(summary['outside_scope'][0]['id'], 'NMC-A000003')
        self.assertEqual(summary['endpoint_counts'], dict.fromkeys(
            ('nmc_hospital_list', 'nmc_hospital_detail', 'nmc_rltm_beds', 'nmc_severe_accept'), 2))
        self.assertEqual(summary['duplicate_nmc_ids'], 0)
        self.assertEqual(summary['integrity_check'], 'ok')
        self.assertEqual(summary['foreign_key_violations'], [])
        counts = {d['district']: d['hospitals'] for d in summary['districts']}
        self.assertEqual((counts['평택시'], counts['천안시'], counts['안성시']), (1, 1, 0))
        profiles = json.loads((directory / 'hospital_profiles.json').read_text(encoding='utf-8'))
        self.assertEqual({p['hospital_id'] for p in profiles}, {'NMC-A000001', 'NMC-A000002'})
        self.assertTrue(all(p['treatment_evaluations']['TEST_ED']['patient_acceptance_required'] for p in profiles))

    def test_empty_database_exports_explicit_empty_coverage(self):
        directory, summary = self.export()
        self.assertEqual(summary['hospital_count'], 0)
        self.assertTrue(all(d['hospitals'] == 0 for d in summary['districts']))
        self.assertEqual(summary['endpoint_counts'], {})
        self.assertEqual(json.loads((directory / 'hospital_profiles.json').read_text(encoding='utf-8')), [])
        self.assertEqual(json.loads((directory / 'field_inventory.json').read_text(encoding='utf-8')), {})

    def test_manifest_verifies_every_frozen_file_without_hashing_itself(self):
        self.ingest()
        directory, _ = self.export()
        manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
        self.assertNotIn('manifest.json', manifest['sha256'])
        self.assertIn('public.db', manifest['sha256'])
        for name, digest in manifest['sha256'].items():
            self.assertEqual(hashlib.sha256((directory / name).read_bytes()).hexdigest(), digest, name)

    def test_same_database_and_time_export_identical_bytes_to_new_directories(self):
        self.ingest()
        first, _ = self.export('first')
        second, _ = self.export('second')
        first_files = {p.name: p.read_bytes() for p in first.iterdir() if p.is_file()}
        second_files = {p.name: p.read_bytes() for p in second.iterdir() if p.is_file()}
        self.assertEqual(first_files, second_files)

    def test_reexport_into_same_directory_keeps_manifest_self_excluded(self):
        self.ingest()
        directory, _ = self.export()
        self.export()
        manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
        self.assertNotIn('manifest.json', manifest['sha256'])
        for name, digest in manifest['sha256'].items():
            self.assertEqual(hashlib.sha256((directory / name).read_bytes()).hexdigest(), digest, name)

    def test_frozen_json_and_database_do_not_change_when_source_updates(self):
        self.ingest()
        directory, _ = self.export()
        before = {p.name: p.read_bytes() for p in directory.iterdir() if p.is_file()}
        self.ingest('A000002', '경기도 안성시 테스트로 1')
        self.assertEqual(before, {p.name: p.read_bytes() for p in directory.iterdir() if p.is_file()})
        with closing(sqlite3.connect(str(directory / 'public.db'))) as frozen:
            self.assertEqual(frozen.execute('SELECT COUNT(*) FROM hospitals').fetchone()[0], 1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM hospitals').fetchone()[0], 2)

    def test_export_reads_one_frozen_state_if_source_changes_during_json_generation(self):
        self.ingest()
        original_evaluate = sync_region.evaluate_requirements

        def evaluate(*args, **kwargs):
            self.conn.execute("UPDATE hospitals SET name='동시 수집으로 바뀐 이름' WHERE id='NMC-A000001'")
            return original_evaluate(*args, **kwargs)

        with patch.object(sync_region, 'evaluate_requirements', side_effect=evaluate):
            directory, _ = self.export()
        profiles = json.loads((directory / 'hospital_profiles.json').read_text(encoding='utf-8'))
        hospitals = json.loads((directory / 'hospitals.json').read_text(encoding='utf-8'))
        with closing(sqlite3.connect(str(directory / 'public.db'))) as frozen:
            frozen_name = frozen.execute('SELECT name FROM hospitals').fetchone()[0]
        self.assertEqual((profiles[0]['name'], hospitals[0]['name'], frozen_name),
                         ('가상 검증용 병원',) * 3)
        self.assertEqual(self.conn.execute('SELECT name FROM hospitals').fetchone()[0], '동시 수집으로 바뀐 이름')

    def test_failed_missing_duplicate_or_wrong_district_run_not_complete(self):
        spec = region_spec()
        good = success_runs(spec)
        failed = copy.deepcopy(good)
        failed[0]['status'] = 'ERROR'
        duplicate = copy.deepcopy(good)
        duplicate[-1] = copy.deepcopy(duplicate[0])
        wrong = copy.deepcopy(good)
        wrong[-1]['district'] = '강남구'
        for number, runs in enumerate((failed, good[:-1], duplicate, wrong)):
            with self.subTest(runs=runs):
                _, summary = self.export('bad-' + str(number), runs)
                self.assertFalse(summary['all_requested_districts_succeeded'])

    def test_regional_cli_records_one_failed_district_without_claiming_completion(self):
        spec = region_spec()
        treatments = self.base / 'treatments.json'
        treatments.write_text(json.dumps(treatment_spec()), encoding='utf-8')
        calls = []

        class FakeClient:
            def __init__(inner, district):
                inner.district = district

        def sync(path, region, client, **options):
            calls.append((region, client.district, options))
            if client.district == '화성시':
                raise public_data.PublicDataError('가상 지역 수집 실패')
            return dict(region=region, district=client.district, status='SUCCESS')

        with patch.object(sync_region, 'NmcClient', FakeClient), \
                patch.object(sync_region, 'sync_once', side_effect=sync), \
                patch('sys.stdout', new_callable=io.StringIO):
            result = sync_region.main(['--db', str(self.path), '--out', str(self.base / 'cli'),
                                       '--treatments', str(treatments)])
        self.assertEqual(result, 1)
        self.assertEqual(len(calls), len(spec['candidate_districts']))
        self.assertTrue(all(options == {'include_details': True, 'include_severe': True}
                            for _, _, options in calls))
        latest = json.loads((self.base / 'cli' / 'latest.json').read_text(encoding='utf-8'))
        summary = json.loads(Path(latest['summary']).read_text(encoding='utf-8'))
        self.assertFalse(summary['all_requested_districts_succeeded'])
        self.assertEqual(sum(run['status'] == 'ERROR' for run in summary['runs']), 1)


if __name__ == '__main__':
    unittest.main()
