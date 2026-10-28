"""실험 권역 수집과 재현 가능한 공개정보 스냅샷. 실제 수용 응답은 생성하지 않는다."""
import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import time

from . import db
from .public_data import NmcClient, PublicDataError, snapshot_view, sync_once
from .treatment_data import evaluate_requirements
from .hira_data import OPERATIONS as HIRA_OPERATIONS

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGION = ROOT / 'config' / 'experiment_region.json'
DEFAULT_TREATMENTS = ROOT / 'config' / 'treatment_requirements.json'


def load_region(path):
    spec = json.loads(Path(path).read_text(encoding='utf-8'))
    districts = spec.get('candidate_districts')
    if not isinstance(districts, list) or not districts:
        raise ValueError('candidate_districts가 필요합니다')
    pairs = []
    for item in districts:
        if not isinstance(item, dict) or any(not isinstance(item.get(k), str) or not item[k].strip()
                                             for k in ('region', 'district')):
            raise ValueError('권역의 시도·시군구를 확인하세요')
        pairs.append((item['region'].strip(), item['district'].strip()))
    if len(pairs) != len(set(pairs)):
        raise ValueError('권역에 중복 시군구가 있습니다')
    for item in spec.get('origin_districts', []):
        if (item.get('region'), item.get('district')) not in pairs:
            raise ValueError('환자 발생 지역은 후보 수집 범위에 포함돼야 합니다')
    return spec


def address_district(address, spec):
    address = address or ''
    for district in spec['candidate_districts']:
        prefix = district['region'] + ' ' + district['district']
        if address == prefix or address.startswith(prefix + ' '):
            return district
    return None


def _write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def export_snapshot(database, directory, spec, treatments, runs, now=None):
    now = int(time.time()) if now is None else int(now)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    # DB 백업 한 시점을 먼저 고정한다. 이후 JSON과 무결성 검사는 같은 복사본을 읽는다.
    frozen_database = directory / 'public.db'
    with closing(sqlite3.connect(str(database))) as source, closing(sqlite3.connect(str(frozen_database))) as backup:
        source.backup(backup)
    conn = db.connect(str(frozen_database))
    try:
        hospitals = db.rows(conn.execute("SELECT * FROM hospitals WHERE source='public' ORDER BY id"))
        all_snapshots = [dict(r) for r in conn.execute('SELECT * FROM public_api_snapshots ORDER BY hospital_id,endpoint')]
        for record in all_snapshots:
            record['normalized'] = json.loads(record['normalized'])
            record['raw_data'] = json.loads(record['raw_data'])
        by_hospital = {}
        for record in all_snapshots:
            by_hospital.setdefault(record['hospital_id'], []).append(record)
        profiles = []
        by_district = Counter()
        outside = []
        field_inventory = {}
        endpoint_counts = Counter()
        dept_nonempty = 0
        delivery_flags = Counter()
        for hospital in hospitals:
            district = address_district(hospital['address'], spec)
            if district is None:
                outside.append({'id': hospital['id'], 'name': hospital['name'], 'address': hospital['address']})
                continue
            by_district[(district['region'], district['district'])] += 1
            observations = {s['endpoint']: s for s in snapshot_view(conn, hospital['id'], now)}
            detail = observations.get('nmc_hospital_detail')
            beds = observations.get('nmc_rltm_beds')
            severe = observations.get('nmc_severe_accept')
            delivery_observation = next((row for row in (beds or {}).get('data',{}).get('service_availability',[])
                                         if row.get('service_type')=='delivery_room'),{})
            flag = delivery_observation.get('reported_available')
            if beds is not None:
                delivery_flags['yes' if flag is True else 'no' if flag is False else 'unknown'] += 1
            dept_nonempty += bool(detail and detail['data'].get('departments'))
            evaluations = {profile['id']: evaluate_requirements(profile, now=now, observations=observations)
                           for profile in treatments['profiles']}
            profiles.append({
                'hospital_id': hospital['id'], 'nmc_id': hospital['source_ids'].get('nmc'),
                'name': hospital['name'], 'address': hospital['address'], 'phone': hospital['phone'],
                'lat': hospital['lat'], 'lon': hospital['lon'], 'district': district,
                'source': 'public', 'observations': observations, 'treatment_evaluations': evaluations,
                'not_observed': ['current_specialists', 'anesthesiology_team', 'patient_acceptance',
                                 'response_duration', 'treatment_duration', 'future_release_time'],
            })
            for snapshot in by_hospital.get(hospital['id'], []):
                endpoint = snapshot['endpoint']
                endpoint_counts[endpoint] += 1
                raw = snapshot['raw_data']
                raw_records = raw['items'] if isinstance(raw.get('items'), list) else [raw]
                raw_records = [record for record in raw_records if isinstance(record, dict)]
                # 제공처와 무관하게 병원별 여러 행을 펼쳐 필드 제공 기관 수를 센다.
                for field in {field for record in raw_records for field in record}:
                    values = [record[field] for record in raw_records if field in record]
                    counts = field_inventory.setdefault(endpoint, {}).setdefault(field, {'present': 0, 'empty': 0})
                    counts['present'] += 1
                    counts['empty'] += all(value is None or not str(value).strip() for value in values)
        for endpoint, fields in field_inventory.items():
            for counts in fields.values():
                counts['missing'] = endpoint_counts[endpoint] - counts['present']
        identifiers = [hospital['source_ids'].get('nmc') for hospital in hospitals]
        summary = {
            'region_id': spec['id'], 'exported_at': now,
            'origin_districts': spec['origin_districts'],
            'districts': [{**district, 'hospitals': by_district[(district['region'], district['district'])]}
                          for district in spec['candidate_districts']],
            'hospital_count': len(profiles), 'endpoint_counts': dict(endpoint_counts),
            'departments_reported_count': dept_nonempty,
            'delivery_room_service_flags':dict(delivery_flags),
            'delivery_room_missing_bed_observations':len(profiles)-sum(delivery_flags.values()),
            'delivery_room_flags_note':'hv42의 Y/N 원문 관측 건수이며 병상 수량·현재 신선도·고위험 분만 수용 확답과 다름',
            'hira_linked_hospital_count':sum(bool(h['source_ids'].get('hira')) for h in hospitals),
            'hira_endpoint_counts':{endpoint:endpoint_counts[endpoint] for endpoint in HIRA_OPERATIONS},
            'hira_expected_endpoint_count':len(HIRA_OPERATIONS),
            'hira_complete_hospital_count':sum(set(HIRA_OPERATIONS) <= set(p['observations']) for p in profiles),
            'nmc_acceptance_messages':{
                'hospitals_collected':endpoint_counts['nmc_acceptance_messages'],
                'hospitals_missing':len(profiles)-endpoint_counts['nmc_acceptance_messages'],
                'hospitals_with_messages':sum(bool(p['observations'].get('nmc_acceptance_messages',{})
                                                   .get('data',{}).get('messages')) for p in profiles),
                'message_rows':sum(len(p['observations'].get('nmc_acceptance_messages',{})
                                        .get('data',{}).get('messages',[])) for p in profiles),
                'note':'빈 메시지 조회 결과는 수용 가능 확답이 아니며, 메시지 유효기간은 원천 갱신시각과 다름',
            },
            'missing_phone_count': sum(not p['phone'] for p in profiles),
            'missing_address_count': sum(not p['address'] for p in profiles),
            'outside_scope': outside,
            'duplicate_nmc_ids': len(identifiers) - len(set(identifiers)),
            'foreign_key_violations': [list(r) for r in conn.execute('PRAGMA foreign_key_check')],
            'integrity_check': conn.execute('PRAGMA integrity_check').fetchone()[0],
            'operational_resource_count': conn.execute('SELECT COUNT(*) FROM resources').fetchone()[0],
            'patient_count': conn.execute('SELECT COUNT(*) FROM patients').fetchone()[0],
            'all_requested_districts_succeeded': len(runs) == len(spec['candidate_districts']) and
                                               {(r.get('region'), r.get('district')) for r in runs} ==
                                               {(r['region'], r['district']) for r in spec['candidate_districts']} and
                                               all(run['status'] == 'SUCCESS' for run in runs),
            'runs': runs,
            'treatment_counts': {profile['id']: dict(Counter(p['treatment_evaluations'][profile['id']]['status']
                                                            for p in profiles)) for profile in treatments['profiles']},
            'limitations': [
                '등록 진료과·시설수 및 공개 가용 관측은 환자별 수용 확답이 아니다.',
                '중증질환 코드는 원문만 보존하며 자동 조건 판정에 사용하지 않는다.',
                '이 스냅샷에는 실제 환자·병원 응답시간·미래 자원 해제시간이 없다.',
                '현재 공통 서버 솔버는 병원 32곳 제한과 ed_bed/special_team 자원 제한이 있다.',
                '기존 가상 시뮬레이터·화면은 이 수집 DB로 자동 전환되지 않는다.',
            ],
        }
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='hira_sync_runs'").fetchone():
            hira_run = conn.execute('SELECT * FROM hira_sync_runs ORDER BY id DESC LIMIT 1').fetchone()
            summary['hira_last_sync'] = dict(hira_run) if hira_run else None
        else:
            summary['hira_last_sync'] = None
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='nmc_message_sync_runs'").fetchone():
            message_run = conn.execute('SELECT * FROM nmc_message_sync_runs ORDER BY id DESC LIMIT 1').fetchone()
            summary['nmc_acceptance_messages']['last_sync'] = dict(message_run) if message_run else None
        else:
            summary['nmc_acceptance_messages']['last_sync'] = None
        _write_json(directory / 'region_config.json', spec)
        _write_json(directory / 'treatment_requirements.json', treatments)
        _write_json(directory / 'hospitals.json', hospitals)
        _write_json(directory / 'public_api_snapshots.json', all_snapshots)
        _write_json(directory / 'hospital_profiles.json', profiles)
        _write_json(directory / 'field_inventory.json', field_inventory)
        _write_json(directory / 'summary.json', summary)
        conn.close()
        filenames = ('region_config.json', 'treatment_requirements.json', 'hospitals.json',
                     'public_api_snapshots.json', 'hospital_profiles.json', 'field_inventory.json',
                     'summary.json', 'public.db')
        hashes = {name: hashlib.sha256((directory / name).read_bytes()).hexdigest() for name in filenames}
        source_hashes = {name: hashlib.sha256((ROOT / 'server' / name).read_bytes()).hexdigest()
                         for name in ('public_data.py', 'treatment_data.py', 'sync_region.py',
                                      'hira_data.py','hira_matching.py','sync_hira.py')}
        for name in ('nmc_messages.py', 'sync_messages.py', 'treatment_binding.py'):
            path = ROOT / 'server' / name
            if path.is_file():
                source_hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        configuration_hashes = {}
        identity_config = ROOT / 'config' / 'hospital_identity_links.json'
        if identity_config.is_file():
            configuration_hashes['config/hospital_identity_links.json'] = hashlib.sha256(identity_config.read_bytes()).hexdigest()
        _write_json(directory / 'manifest.json', {'created_at': now, 'sha256': hashes,
                                                 'normalizer_sha256': source_hashes,
                                                 'configuration_sha256': configuration_hashes})
        return summary
    finally:
        conn.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description='실험 권역 공공자료 수집·치료조건 정리·스냅샷 고정')
    parser.add_argument('--db', default='data/public.db')
    parser.add_argument('--region-config', default=str(DEFAULT_REGION))
    parser.add_argument('--treatments', default=str(DEFAULT_TREATMENTS))
    parser.add_argument('--out', default='data/regional_snapshots')
    parser.add_argument('--export-only', action='store_true', help='통신 없이 현재 DB의 고정 스냅샷 생성')
    args = parser.parse_args(argv)
    if args.export_only and not Path(args.db).is_file():
        parser.error('--export-only에는 기존 DB 파일이 필요합니다')
    try:
        spec = load_region(args.region_config)
        treatments = json.loads(Path(args.treatments).read_text(encoding='utf-8'))
        if not treatments.get('profiles'):
            raise ValueError('치료조건 프로필이 필요합니다')
        # 키가 없으면 DB를 만들거나 수집을 시작하지 않는다.
        if not args.export_only:
            NmcClient(district=spec['candidate_districts'][0]['district'])
    except (ValueError, OSError, PublicDataError):
        parser.exit(2, '권역·치료조건 설정 또는 DATA_GO_KR_KEY 등록을 확인하세요\n')
    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    runs = []
    if not args.export_only:
        for district in spec['candidate_districts']:
            try:
                result = sync_once(args.db, district['region'],
                                   client=NmcClient(district=district['district']),
                                   include_details=True, include_severe=True)
            except PublicDataError as exc:
                result = {**district, 'status': 'ERROR', 'error': str(exc)}
            runs.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
    else:
        conn = db.connect(str(args.db))
        try:
            for district in spec['candidate_districts']:
                row = conn.execute('SELECT * FROM public_sync_runs WHERE region=? AND district=? '
                                   'ORDER BY id DESC LIMIT 1', (district['region'], district['district'])).fetchone()
                runs.append(dict(row) if row else {**district, 'status': 'NOT_COLLECTED'})
        finally:
            conn.close()
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    directory = Path(args.out) / stamp
    summary = export_snapshot(args.db, directory, spec, treatments, runs)
    _write_json(Path(args.out) / 'latest.json', {'directory': str(directory), 'summary': str(directory / 'summary.json')})
    print(json.dumps({'snapshot_directory': str(directory),
                      **{key: summary[key] for key in ('hospital_count', 'endpoint_counts',
                                                       'all_requested_districts_succeeded', 'integrity_check')}},
                     ensure_ascii=False), flush=True)
    return 0 if summary['all_requested_districts_succeeded'] and not summary['outside_scope'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
