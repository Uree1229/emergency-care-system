"""Repeatable, keyless team demo using saved public hospital observations.

python -m server.team_demo --db data/team-demo.db --scenario normal --port 18027

No hospital messages leave this process. Public observations keep their original
timestamps; they never become operational capacity or a clinical acceptance.
"""
import argparse
import json
import math
import re
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from . import db, service, transport_review
from .app import make_handler
from .inquiry_worker import InquiryWorker
from .travel import TravelEstimator

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / 'datasets/demo/seoul_public_catalog.json'
CASES = ROOT / 'datasets/examples/cau_public_flow_cases.json'
CAU = 'NMC-A1100003'
YE = 'NMC-A1100011'
SCENARIOS = {'normal': ('CAU-E2E-001', CAU), 'change': ('CAU-CHANGE-001', YE)}


def origin(value):
    """Validate an explicitly supplied server origin without making requests."""
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError:
        raise ValueError('서버 주소의 포트가 올바르지 않습니다.') from None
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname or
            parsed.username or parsed.password or parsed.path not in ('', '/') or
            parsed.query or parsed.fragment or any(c.isspace() for c in value)):
        raise ValueError('서버 주소는 http(s)://호스트[:포트] 형식이어야 합니다.')
    if port is not None and not 1 <= port <= 65535:
        raise ValueError('서버 포트 범위가 올바르지 않습니다.')
    return value.rstrip('/')


def _valid_number(value, limit):
    return type(value) in (int, float) and math.isfinite(value) and -limit <= value <= limit


def load_catalog(path=CATALOG):
    """Validate the complete fixture before touching any database rows."""
    content = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if (not isinstance(content, dict) or content.get('schema_version') != 1 or
            type(content.get('captured_at')) is not int or content['captured_at'] <= 0 or
            not isinstance(content.get('hospitals'), list) or not content['hospitals'] or
            not isinstance(content.get('snapshots'), list)):
        raise ValueError('공공 병원 fixture 형식이 올바르지 않습니다.')
    ids = set()
    source_ids = set()
    for h in content['hospitals']:
        if (not isinstance(h, dict) or not isinstance(h.get('id'), str) or not h['id'] or
                h['id'] in ids or not isinstance(h.get('name'), str) or not h['name'] or
                h.get('source') != 'public' or not _valid_number(h.get('lat'), 90) or
                not _valid_number(h.get('lon'), 180) or not isinstance(h.get('capabilities'), list) or
                not all(isinstance(c, str) for c in h['capabilities']) or
                not isinstance(h.get('source_ids'), dict) or
                not isinstance(h['source_ids'].get('nmc'), str) or not h['source_ids']['nmc'] or
                h['source_ids']['nmc'] in source_ids or
                h.get('capabilities_status', 'known') not in ('known', 'unknown')):
            raise ValueError('공공 병원 fixture에 잘못된 기관 또는 중복 출처가 있습니다.')
        ids.add(h['id'])
        source_ids.add(h['source_ids']['nmc'])
    seen = set()
    for s in content['snapshots']:
        if (not isinstance(s, dict) or s.get('hospital_id') not in ids or
                not isinstance(s.get('endpoint'), str) or not s['endpoint'] or
                (s['hospital_id'], s['endpoint']) in seen or
                type(s.get('fetched_at')) is not int or s['fetched_at'] <= 0 or
                not (s.get('source_observed_at') is None or
                     type(s['source_observed_at']) is int and s['source_observed_at'] >= 0) or
                not isinstance(s.get('normalized'), dict) or not isinstance(s.get('raw_data'), dict)):
            raise ValueError('공공 관측 fixture에 잘못된 참조 또는 중복 관측이 있습니다.')
        seen.add((s['hospital_id'], s['endpoint']))
    return content


def seed_catalog(conn, catalog):
    """Only an empty hospital catalog is populated; existing observations stay intact."""
    with db.transaction(conn):
        if conn.execute('SELECT 1 FROM hospitals LIMIT 1').fetchone():
            return False
        for hospital in catalog['hospitals']:
            service.upsert_hospital(conn, hospital)
        for snap in catalog['snapshots']:
            conn.execute('INSERT INTO public_api_snapshots '
                         '(hospital_id,endpoint,source_observed_at,fetched_at,normalized,raw_data) '
                         'VALUES(?,?,?,?,?,?)',
                         (snap['hospital_id'], snap['endpoint'], snap.get('source_observed_at'),
                          snap['fetched_at'], db.dumps(snap['normalized']), db.dumps(snap['raw_data'])))
    return True


def _registry(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS team_demo_cases ('
                 'patient_id TEXT PRIMARY KEY REFERENCES patients(id), '
                 'scenario TEXT NOT NULL CHECK(scenario IN (\'normal\',\'change\')), '
                 'created_at INTEGER NOT NULL)')


def prepare(path, scenario='normal', case_suffix='', catalog_path=CATALOG,
            cases_path=CASES, now=None):
    """Add only missing demo cases. Existing cases, including terminal ones, are unchanged.

    `empty` means no new cases, never deletion. A new DB is the simplest way to
    repeat the change scenario because its hospital restriction is intentionally
    retained until a user clears it.
    """
    if scenario not in ('normal', 'change', 'both', 'empty'):
        raise ValueError('알 수 없는 시나리오입니다.')
    if not isinstance(case_suffix, str) or not re.fullmatch(r'[A-Za-z0-9_-]{0,64}', case_suffix):
        raise ValueError('case-suffix는 영문·숫자·밑줄·하이픈 64자 이내입니다.')
    now = int(time.time()) if now is None else now
    catalog = load_catalog(catalog_path)
    case_data = json.loads(Path(cases_path).read_text(encoding='utf-8-sig'))
    selected = ['normal', 'change'] if scenario == 'both' else [] if scenario == 'empty' else [scenario]
    prepared = []
    for name in selected:
        payload = json.loads(json.dumps(case_data[name]))
        payload['id'] = SCENARIOS[name][0] + ('-' + case_suffix if case_suffix else '')
        marker = 'CAU_PUBLIC_FLOW_' + name.upper()
        if (payload.get('extra', {}).get('scenario_id') != marker or
                payload['extra'].get('synthetic') is not True or
                payload['extra'].get('hospital_messages_delivered_externally') is not False):
            raise ValueError('가상 환자 fixture 표시가 누락됐습니다.')
        # Validate every clinical payload before the first case can be committed.
        service._bind_treatment_fields(service._clean_patient(payload))
        if not _valid_number(payload.get('lat'), 90) or not _valid_number(payload.get('lon'), 180):
            raise ValueError('가상 발생 위치가 올바르지 않습니다.')
        prepared.append((name, payload))
    path = str(path)
    if path != ':memory:':
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = db.connect(path)
    try:
        db.init(conn)
        seed_catalog(conn, catalog)
        # Validate every requested case and dependency before creating the first.
        for name, payload in prepared:
            hid = SCENARIOS[name][1]
            hospital = service._hospital(conn, hid)
            if hospital['source'] != 'public':
                raise ValueError('실제 공공 병원 목록이 있는 전용 테스트 DB를 사용하세요.')
            existing = db.row(conn.execute('SELECT * FROM patients WHERE id=?', (payload['id'],)).fetchone())
            if existing and existing['extra'].get('scenario_id') != payload['extra']['scenario_id']:
                raise ValueError(f"기존 환자 {payload['id']}가 시연 사례와 충돌합니다. 새 DB 또는 suffix를 쓰세요.")
            if not existing and service.blocking_restriction(conn, hid, set(payload['required_capabilities']), now):
                raise ValueError('시연 목적 병원에 수용 제한이 있습니다. 기존 제한을 보존합니다. 새 DB를 쓰세요.')
        _registry(conn)
        created = []
        for name, payload in prepared:
            pid, hid = payload['id'], SCENARIOS[name][1]
            if not conn.execute('SELECT 1 FROM patients WHERE id=?', (pid,)).fetchone():
                service.create_patient(conn, now, pid, payload, actor='team_demo_synthetic')
                p = service.send_inquiries(conn, now, pid, [hid], actor='team_demo_synthetic')
                if name == 'change':
                    # Explicit prepared synthetic response, never a real hospital response.
                    iid = p['inquiries'][0]['id']
                    service.respond(conn, now, iid, 'accept', '[가상] 테스트 준비')
                    service.confirm_destination(conn, now, pid, iid, actor='team_demo_synthetic')
                    service.depart(conn, now, pid, TravelEstimator(use_kakao=False), actor='team_demo_synthetic')
                    transport_review.enable(conn, now, pid, enabled=True)
                    service.add_restriction(conn, now, hid, '정형외과',
                        '[가상 테스트] 이송 중 치료 조건 유지 어려움. 실제 병원의 수용 제한이 아닙니다.',
                        ends_at=now + 7200, actor='team_demo_synthetic')
                created.append(pid)
            conn.execute('INSERT OR IGNORE INTO team_demo_cases(patient_id,scenario,created_at) VALUES(?,?,?)',
                         (pid, name, now))
        return dict(created=created, reused=[p['id'] for _, p in prepared if p['id'] not in created],
                    config=demo_config(conn), hospital_count=conn.execute('SELECT COUNT(*) FROM hospitals').fetchone()[0],
                    operational_resources=conn.execute('SELECT COUNT(*) FROM resources').fetchone()[0])
    finally:
        conn.close()


def demo_config(conn, base_url=None, intake_url=None):
    """Read-only DB-scoped landing configuration; relative URLs travel with the server."""
    base = origin(base_url) if base_url else ''
    if intake_url:
        intake_url = origin(intake_url) + '/'
    config = dict(source_note='실제 공공 API 저장본 + 가상 환자·가상 병원 응답. '
                              '문의·채팅은 테스트 서버에만 저장되며 실제 병원에는 전송되지 않습니다.',
                  dataset_path='datasets/demo/seoul_public_catalog.json', cases=[])
    if base:
        config['base'] = base
    if intake_url:
        config['intake_url'] = intake_url
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='team_demo_cases'").fetchone():
        return config
    for row in conn.execute('SELECT c.patient_id,c.scenario FROM team_demo_cases c '
                             'JOIN patients p ON p.id=c.patient_id ORDER BY c.created_at,c.patient_id'):
        pid, name = row['patient_id'], row['scenario']
        hid = SCENARIOS[name][1]
        item = dict(id=pid, label='기본 이송 전체 흐름' if name == 'normal' else '이송 중 목적지 변경',
                    description='중앙대 교내 가상 낙상 → 중앙대학교병원 문의. 병원 질문·수용 응답부터 시작합니다.'
                    if name == 'normal' else '여의도성모병원으로 가상 이송 중. 가상 조건 변경을 재확인합니다.',
                    patient_url=base + '/patient/?' + urlencode({'pid': pid}),
                    hospital_url=base + '/hospital/?' + urlencode({'hid': hid, 'pid': pid}),
                    initial_state='WAITING' if name == 'normal' else 'IN_TRANSIT')
        if name == 'change':
            item['alternative_hospital_url'] = base + '/hospital/?' + urlencode({'hid': CAU, 'pid': pid})
        config['cases'].append(item)
    return config


def make_demo_handler(path, estimator, base_url=None, intake_url=None):
    if base_url:
        base_url = origin(base_url)
    if intake_url:
        intake_url = origin(intake_url)
    parent = make_handler(path, estimator, cors_origin=None)

    class DemoHandler(parent):
        def _static(self, path_):
            if path_ == '/public-flow-demo.json':
                conn = db.connect(path)
                try:
                    payload = demo_config(conn, base_url, intake_url)
                finally:
                    conn.close()
                return self._send(200, payload)
            return super()._static(path_)

    return DemoHandler


def main(argv=None):
    parser = argparse.ArgumentParser(description='공공 병원 저장본을 사용하는 팀 공동 테스트 (외부 전송 없음)')
    parser.add_argument('--db', default='data/team-demo.db')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=18027)
    parser.add_argument('--scenario', choices=['normal', 'change', 'both', 'empty'], default='normal')
    parser.add_argument('--case-suffix', default='')
    parser.add_argument('--base-url', help='주소를 고정해야 할 때만 지정. 기본 링크는 접속한 서버 주소를 사용합니다.')
    parser.add_argument('--intake-url', help='별도로 실행한 원본 환자 앱의 origin (예: http://127.0.0.1:18028)')
    parser.add_argument('--prepare-only', action='store_true', help='데이터만 준비하고 HTTP 서버를 실행하지 않습니다.')
    args = parser.parse_args(argv)
    try:
        if args.base_url:
            args.base_url = origin(args.base_url)
        if args.intake_url:
            args.intake_url = origin(args.intake_url)
        if not 1 <= args.port <= 65535:
            raise ValueError('포트는 1~65535입니다.')
        result = prepare(args.db, args.scenario, args.case_suffix)
    except (ValueError, OSError, KeyError, service.ServiceError) as exc:
        parser.error(str(exc))
    print(json.dumps({k: v for k, v in result.items() if k != 'config'}, ensure_ascii=False), flush=True)
    if args.prepare_only:
        return
    estimator = TravelEstimator(use_kakao=False)
    server = ThreadingHTTPServer((args.host, args.port),
                                 make_demo_handler(args.db, estimator, args.base_url, args.intake_url))
    worker = InquiryWorker(args.db, estimator)
    worker.start()
    shown_host = '127.0.0.1' if args.host in ('0.0.0.0', '::') else args.host
    print(f'http://{shown_host}:{args.port}/public-flow-demo.html', flush=True)
    print('실제 병원에는 전송되지 않습니다. 저장된 공공 정보의 관측시각을 확인하세요.', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        worker.stop()
        server.server_close()


if __name__ == '__main__':
    main()
