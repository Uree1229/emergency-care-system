"""Frontend-free local integration: existing HTTP API and inquiry worker only.

python -m server.flow_rehearsal run --scenario all
python -m server.flow_rehearsal serve --db data/flow-server.db --port 18030

The two frontend slots deliberately remain empty. Role-emulated replies in a
rehearsal are synthetic actions, not clinical decisions or performance evidence.
"""
import argparse
import json
import math
from pathlib import Path
import sqlite3
import threading
import time
import uuid

from http.server import ThreadingHTTPServer

from . import db, service
from .app import make_handler
from .inquiry_worker import InquiryWorker
from .system_status import RuntimeStatus
from .travel import TravelEstimator


HOSPITALS = (
    ('FLOW-H1', '연결검증 가상병원 1', 37.5054, 126.9574, 1),
    ('FLOW-H2', '연결검증 가상병원 2', 37.5100, 126.9600, 3),
    ('FLOW-H3', '연결검증 가상병원 3', 37.5160, 126.9670, 3),
)
FRONTENDS = {
    'paramedic': {'status': 'placeholder', 'path': '/patient/'},
    'hospital': {'status': 'placeholder', 'path': '/hospital/'},
}


def prepare_database(path):
    """Create an isolated synthetic catalog; never initialize an unrelated DB."""
    path = Path(path).resolve()
    if path.exists():
        if not path.is_file():
            raise ValueError('DB 경로는 파일이어야 합니다. 새 DB 파일명을 지정하세요.')
        try:
            conn = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
        except sqlite3.Error:
            raise ValueError('기존 DB를 읽을 수 없습니다. 새 DB 경로를 지정하세요.') from None
        try:
            try:
                marker = conn.execute('SELECT schema_version FROM flow_rehearsal_meta WHERE id=1').fetchone()
                hospitals = conn.execute('SELECT id,source FROM hospitals ORDER BY id').fetchall()
            except sqlite3.DatabaseError:
                raise ValueError('기존 업무 DB는 사용하지 않습니다. 새 DB 경로를 지정하세요.') from None
            if (marker != (1,) or hospitals != sorted((h[0], 'synthetic') for h in HOSPITALS)):
                raise ValueError('연결검증 전용 가상 DB만 다시 사용할 수 있습니다.')
        finally:
            conn.close()
        return str(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Reserve the new file exclusively before SQLite can initialize it.
    path.touch(exist_ok=False)
    conn = db.connect(str(path))
    try:
        db.init(conn)
        now = int(time.time())
        for hid, name, lat, lon, capacity in HOSPITALS:
            service.upsert_hospital(conn, dict(id=hid, name=name, lat=lat, lon=lon,
                capabilities=['응급의학과', '정형외과'], source='synthetic'))
            service.set_resource(conn, now, hid, 'ed_bed', capacity, 0,
                                 source='synthetic', actor='flow_rehearsal_setup')
        conn.execute('CREATE TABLE flow_rehearsal_meta ('
                     'id INTEGER PRIMARY KEY CHECK(id=1), schema_version INTEGER NOT NULL, '
                     'created_at INTEGER NOT NULL)')
        conn.execute('INSERT INTO flow_rehearsal_meta VALUES(1,1,?)', (now,))
    finally:
        conn.close()
    return str(path)


def manifest():
    return dict(schema_version=1, service='frontend-free-flow', synthetic=True,
                external_hospital_messages=False, frontends=FRONTENDS,
                links=dict(status='/api/system/status', hospitals='/api/hospitals',
                           inquiry_policies='/api/inquiry-policies'),
                contract='docs/FRONTEND_FREE_FLOW.md')


def api_only_handler(path, estimator, runtime):
    base = make_handler(path, estimator, runtime=runtime)

    class Handler(base):
        def _static(self, path):
            # Leave the team's actual frontend files and their usual launcher alone.
            if path in ('/', '/manifest.json'):
                return self._send(200, manifest())
            if path.rstrip('/') in ('/patient', '/hospital'):
                return self._send(501, dict(error='FRONTEND_NOT_CONNECTED',
                    message='프론트엔드 연결 자리입니다. 업무 API는 사용할 수 있습니다.',
                    frontends=FRONTENDS))
            return self._send(404, dict(error='NOT_FOUND'))

    return Handler


class LocalFlowServer:
    """Disposable server lifecycle with a persistent, explicitly separate DB."""
    def __init__(self, path, *, port=0, host='127.0.0.1', poll_seconds=0.1):
        if host not in ('127.0.0.1', 'localhost'):
            raise ValueError('연결검증 서버는 이 PC의 loopback 주소에서만 실행합니다.')
        if type(port) is not int or not 0 <= port <= 65535:
            raise ValueError('port는 0~65535 정수입니다.')
        if (type(poll_seconds) not in (int, float) or not math.isfinite(poll_seconds)
                or poll_seconds <= 0):
            raise ValueError('poll_seconds는 양의 유한수입니다.')
        self.path, self.port = str(Path(path).resolve()), port
        self.poll_seconds = poll_seconds
        self.server = self.thread = self.worker = None
        self.base_url = None
        self._worker_started = self._http_started = False

    def __enter__(self):
        if self.server is not None:
            raise RuntimeError('같은 서버 객체를 다시 시작할 수 없습니다.')
        self.path = prepare_database(self.path)
        self.estimator = TravelEstimator(use_kakao=False)
        self.runtime = RuntimeStatus()
        self.worker = InquiryWorker(self.path, self.estimator, poll_seconds=self.poll_seconds)
        self.runtime.configure('planning', enabled=False)
        self.runtime.configure('inquiry', self.worker)
        try:
            self.server = ThreadingHTTPServer(('127.0.0.1', self.port),
                api_only_handler(self.path, self.estimator, self.runtime))
            self.server.daemon_threads = True
            self.base_url = f'http://127.0.0.1:{self.server.server_port}'
            self.thread = threading.Thread(target=self.server.serve_forever,
                                           kwargs={'poll_interval': 0.05}, daemon=True)
            self.thread.start()
            self._http_started = True
            self.worker.start()
            self._worker_started = True
            return self
        except BaseException:
            self.close()
            raise

    def close(self):
        if self._worker_started:
            self.worker.stop()
            self._worker_started = False
        if self._http_started:
            self.server.shutdown()
            self.thread.join(timeout=5)
            self._http_started = False
        if self.server is not None:
            self.server.server_close()

    def __exit__(self, *_):
        self.close()


def write_report(output, report):
    output = Path(output)
    (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    lines = ['# 프론트엔드 독립 전체 연결 검증', '',
             f"- 실행 ID: `{report['run_id']}`", f"- 결과: {'통과' if report['passed'] else '실패'}",
             '- 구급대원·병원 프론트엔드는 미연결 상태. 역할 클라이언트가 가상 조작을 실행함.',
             '- 기존 HTTP API와 문의 워커를 사용함. 실제 병원 연락·외부 API 호출 없음.',
             '- 기능 연결 검증이며 치료 결과나 알고리즘 우수성 검증이 아님.',
             f"- 전용 DB: `{report['database']}`", '']
    for scenario in report['scenarios']:
        lines += [f"## {scenario.get('scenario', 'unknown')}", '',
                  f"환자 ID: `{scenario.get('patient_id', '-')}`", '',
                  '| 단계 | 역할 | HTTP | 경로 | 상태 | 검증 |', '|---|---|---|---|---|---|']
        for step in scenario.get('steps', []):
            fields = [step.get('id', ''), step.get('role', ''), step.get('method', ''),
                      step.get('path', ''), step.get('status', ''),
                      '; '.join((('통과: ' if check.get('passed') else '실패: ') + str(check.get('name', '')))
                                if isinstance(check, dict) else str(check) for check in step.get('checks', []))]
            lines.append('| ' + ' | '.join(str(x).replace('|', '\\|').replace('\n', ' ') for x in fields) + ' |')
        lines += ['', '최종 상태: `' + json.dumps(scenario.get('outcome', {}), ensure_ascii=False) + '`', '']
    if report.get('failure'):
        lines += ['실패: ' + str(report['failure']), '']
    (output / 'report.md').write_text('\n'.join(lines), encoding='utf-8')


def run_all(args):
    from .flow_scenarios import ScenarioError, run_scenario
    run_id = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '-' + uuid.uuid4().hex[:6]
    output = Path(args.output or Path('outputs') / 'flow-rehearsal' / run_id).resolve()
    output.mkdir(parents=True, exist_ok=False)
    report = dict(schema_version=1, run_id=run_id, started_at=int(time.time()),
                  database=str(output / 'flow.db'), api_origin=None, frontends=FRONTENDS,
                  synthetic=True, external_hospital_messages=False, scenarios=[], passed=False)
    try:
        with LocalFlowServer(output / 'flow.db') as local:
            report['api_origin'] = local.base_url
            scenarios = ('normal', 'amended', 'change') if args.scenario == 'all' else (args.scenario,)
            for scenario in scenarios:
                try:
                    result = run_scenario(local.base_url, scenario, run_id=run_id, timeout=args.timeout)
                except ScenarioError as error:
                    report['scenarios'].append(error.report)
                    raise
                report['scenarios'].append(result)
            report['passed'] = all(item.get('passed') for item in report['scenarios'])
    except Exception as error:
        report['failure'] = str(error)
    finally:
        report['finished_at'] = int(time.time())
        write_report(output, report)
    print(json.dumps(dict(passed=report['passed'], report=str(output / 'report.json'),
                          summary=str(output / 'report.md'), database=report['database']), ensure_ascii=False), flush=True)
    return 0 if report['passed'] else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description='프론트 없이 전체 HTTP 연결 흐름 검증')
    sub = parser.add_subparsers(dest='command', required=True)
    run = sub.add_parser('run', help='새 전용 DB로 가상 역할의 전체 흐름을 실행하고 보고서 저장')
    run.add_argument('--scenario', choices=('normal', 'amended', 'change', 'all'), default='all')
    run.add_argument('--output', help='새로 생성할 결과 폴더. 기존 폴더는 덮어쓰지 않음')
    run.add_argument('--timeout', type=float, default=10, help='각 자동 처리 대기 제한(초)')
    serve = sub.add_parser('serve', help='프론트 슬롯을 비워 두고 공통 업무 API만 실행')
    serve.add_argument('--db', default='data/flow-server.db')
    serve.add_argument('--port', type=int, default=18030)
    args = parser.parse_args(argv)
    if args.command == 'run':
        if not math.isfinite(args.timeout) or not 0 < args.timeout <= 120:
            parser.error('timeout은 0초 초과 120초 이하의 유한수입니다.')
        try:
            return run_all(args)
        except (OSError, ValueError) as error:
            parser.error(str(error))
    try:
        with LocalFlowServer(args.db, port=args.port) as local:
            print(json.dumps(dict(base_url=local.base_url, database=local.path, **manifest()),
                             ensure_ascii=False), flush=True)
            while True:
                time.sleep(0.5)
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError) as error:
        parser.error(str(error))


if __name__ == '__main__':
    raise SystemExit(main())
