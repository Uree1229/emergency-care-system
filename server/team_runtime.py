"""One non-destructive, local team server. UI and voice parsers are unchanged.

python -m server.team_runtime serve
python -m server.team_runtime check

The default DB is dedicated to synthetic integration work. Existing unrelated
DBs are rejected before initialization; resources are never refreshed silently.
"""
import argparse
from contextlib import AbstractContextManager
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import sqlite3
import threading
import time
from urllib.parse import urlsplit

from . import db, service, synthetic
from .app import make_handler
from .inquiry_worker import InquiryWorker
from .system_status import RuntimeStatus
from .travel import TravelEstimator
from .team_runtime_profile import verify_profile
from .team_release_info import release_info, check_dependencies

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT/'data/team-common.db'
DEFAULT_PORT = 18033
SCHEMA_VERSION = 1
CATALOG_ID = 'team-synthetic-hospitals-20261030-v1'
BASELINE_ID = 'server-allocation-baseline-20261030-v1'
REVISION = 'team-runtime-20261030-v1'
HOSPITAL_IDS = tuple(item[0] for item in synthetic.HOSPITALS)
COUNT_TABLES = ('patients', 'inquiries', 'voice_sessions', 'events')


class ExclusiveHTTPServer(ThreadingHTTPServer):
    # Windows SO_REUSEADDR can let a second HTTP server steal/distribute a port.
    # The common server requires one owner, including against legacy launchers.
    allow_reuse_address = False

    def server_bind(self):
        if os.name == 'nt' and hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def _path(path):
    if str(path) == ':memory:':
        raise ValueError('공통 실행에는 별도 파일 DB 경로를 사용하세요.')
    result = Path(path).resolve()
    if result.exists() and not result.is_file():
        raise ValueError('DB 경로가 파일이 아닙니다.')
    return result


class DatabaseLease(AbstractContextManager):
    """Keep an OS lock, not a stale PID claim. The sidecar is never unlinked."""
    def __init__(self, path):
        self.path = _path(path)
        self.handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = Path(str(self.path)+'.team-runtime.lock').open('a+b')
        try:
            self.handle.seek(0, os.SEEK_END)
            if self.handle.tell() == 0:
                self.handle.write(b'0')
                self.handle.flush()
            self.handle.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            self.handle.close()
            self.handle = None
            raise ValueError('같은 DB를 사용하는 공통 실행기가 이미 있습니다. 기존 실행기를 종료하세요.') from error
        return self

    def __exit__(self, *_):
        if self.handle is not None:
            self.handle.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            self.handle.close()
            self.handle = None


def inspect_database(path, now=None):
    """Read-only validation: no creation, migration, expiry, seed or refresh."""
    path, now = _path(path), int(time.time()) if now is None else now
    if not path.exists():
        return dict(path=str(path), exists=False, valid=False, hospital_count=0,
                    resources=dict(count=0, stale_count=0), counts={name: 0 for name in COUNT_TABLES})
    conn = None
    try:
        conn = sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA query_only=ON')
        marker = conn.execute('SELECT * FROM team_runtime_meta WHERE id=1').fetchone()
        if marker is None or (marker['schema_version'], marker['catalog_id'], marker['baseline_id']) != (
                SCHEMA_VERSION, CATALOG_ID, BASELINE_ID):
            raise ValueError('공통 실행 전용 DB 표식이나 버전이 다릅니다. 새 DB 경로를 지정하세요.')
        hospitals = conn.execute('SELECT id,source FROM hospitals ORDER BY id').fetchall()
        if [(row['id'], row['source']) for row in hospitals] != [(hid, 'synthetic') for hid in sorted(HOSPITAL_IDS)]:
            raise ValueError('공통 실행은 지정된 합성 병원 전용 DB만 사용합니다. 기존 DB는 보존합니다.')
        resources = conn.execute('SELECT updated_at FROM resources').fetchall()
        return dict(path=str(path), exists=True, valid=True, hospital_count=len(hospitals),
            resources=dict(count=len(resources), stale_count=sum(now-row['updated_at'] > service.STALE_AFTER for row in resources)),
            counts={name: conn.execute('SELECT COUNT(*) FROM '+name).fetchone()[0] for name in COUNT_TABLES})
    except sqlite3.Error as error:
        raise ValueError('기존 DB는 공통 실행 전용 DB가 아닙니다. 초기화하지 않았습니다. 새 DB 경로를 쓰세요.') from error
    finally:
        if conn is not None:
            conn.close()


def _prepare_database(path, now=None):
    path, now = _path(path), int(time.time()) if now is None else now
    existing = inspect_database(path, now)
    if existing['exists']:
        return dict(existing, reused=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=False)
    conn = db.connect(str(path))
    try:
        db.init(conn)
        with db.transaction(conn):
            synthetic.seed_hospitals(conn, now, occupancy=0)
            conn.execute('CREATE TABLE team_runtime_meta ('
                'id INTEGER PRIMARY KEY CHECK(id=1),schema_version INTEGER NOT NULL,'
                'catalog_id TEXT NOT NULL,baseline_id TEXT NOT NULL,created_at INTEGER NOT NULL)')
            conn.execute('INSERT INTO team_runtime_meta VALUES(1,?,?,?,?)',
                         (SCHEMA_VERSION, CATALOG_ID, BASELINE_ID, now))
    finally:
        conn.close()
    return dict(inspect_database(path, now), reused=False)


def prepare_database(path=DEFAULT_DB, now=None):
    # Validate before even creating a lock sidecar for an unrelated existing DB.
    inspect_database(path, now)
    with DatabaseLease(path):
        return _prepare_database(path, now)


def refresh_fixture_resources(path=DEFAULT_DB, now=None):
    """Explicit synthetic re-confirmation; quantities/status/commitments survive."""
    now = int(time.time()) if now is None else now
    before = inspect_database(path, now)
    if not before['exists']:
        raise ValueError('먼저 공통 실행 전용 DB를 준비하세요.')
    with DatabaseLease(path):
        inspect_database(path, now)
        conn = db.connect(str(_path(path)))
        try:
            with db.transaction(conn):
                resources = db.rows(conn.execute('SELECT * FROM resources ORDER BY hospital_id,resource_type'))
                for resource in resources:
                    service.set_resource(conn, now, resource['hospital_id'], resource['resource_type'],
                        resource['capacity'], resource['occupied'], resource['status'], source=resource['source'],
                        source_observed_at=resource['source_observed_at'], actor='synthetic_fixture_refresh')
        finally:
            conn.close()
    return dict(inspect_database(path, now), refreshed=len(resources),
                note='합성 자원의 수량·상태를 유지하고 관측시각을 명시적으로 갱신했습니다.')


def create_example(path=DEFAULT_DB, patient_id='TEAM-COMMON-001'):
    if not isinstance(patient_id, str) or not patient_id or not all(ch.isascii() and (ch.isalnum() or ch in '_-') for ch in patient_id):
        raise ValueError('시연 ID에는 영문·숫자·밑줄·하이픈을 사용하세요.')
    now = int(time.time())
    inspect_database(path, now)
    with DatabaseLease(path):
        _prepare_database(path, now)
        conn = db.connect(str(_path(path)))
        try:
            existing = conn.execute('SELECT id FROM patients WHERE id=?', (patient_id,)).fetchone()
            if existing:
                raise ValueError('이미 있는 환자는 바꾸지 않습니다. 새 --patient-id를 지정하세요.')
            patient = service.create_patient(conn, now, patient_id, dict(
                reporter_role='transporter', consent=True, age=47, symptoms='[가상 시연] 흉통',
                consciousness='alert', breathing='normal', priority=4, priority_source='expert',
                lat=synthetic.CENTER[0], lon=synthetic.CENTER[1], auto_inquiry=True, inquiry_policy='fixed_3',
                extra=dict(treatment_profile_id='TR_GENERAL_ED', chief_complaint='cardiac',
                    avpu='A', age_group='adult', pre_ktas_level=2, stemi_suspected='no',
                    vitals={'hr':90}, synthetic_fixture=True)), actor='team_runtime_fixture')
            return dict(patient_id=patient['id'], state=patient['state'], synthetic=True,
                        note='음성 인식 결과가 아닌 명시적으로 만든 가상 예제입니다.')
        finally:
            conn.close()


class CommonRuntime(AbstractContextManager):
    def __init__(self, path=DEFAULT_DB, *, port=0, mode='full', host='127.0.0.1',
                 allow_environment_difference=False):
        if host != '127.0.0.1':
            raise ValueError('공통 시연 서버는 127.0.0.1에서만 실행합니다.')
        if type(port) is not int or not 0 <= port <= 65535:
            raise ValueError('port는 0~65535 정수입니다.')
        if mode not in ('full', 'voice-only'):
            raise ValueError('mode는 full 또는 voice-only입니다.')
        self.path = str(_path(path))
        self.port, self.mode, self.host = port, mode, host
        self.allow_environment_difference = allow_environment_difference
        self.http = self.thread = self.lease = self.planner = self.inquiry_worker = None
        self._started_workers = []
        self.base_url = None
        self.profile = None
        self.release = None
        self.dependencies = None
        self.runtime = RuntimeStatus()

    def start(self):
        if self.http is not None or self.base_url is not None:
            raise RuntimeError('같은 공통 실행 객체는 한 번만 시작합니다.')
        self.profile = verify_profile(self.mode, self.allow_environment_difference)
        if not self.profile['ok']:
            raise ValueError('실행 기준 검사 실패: '+json.dumps(self.profile['issues'], ensure_ascii=False))
        self.release = release_info()
        self.dependencies = check_dependencies(self.mode)
        inspect_database(self.path)
        estimator = TravelEstimator(use_kakao=False)
        parent = make_handler(self.path, estimator, cors_origin=None, runtime=self.runtime)
        owner = self
        class Handler(parent):
            def _dispatch(self, method):
                if method == 'GET' and urlsplit(self.path).path == '/api/team-runtime':
                    try:
                        result = owner.info()
                    except (ValueError, OSError):
                        self._send(503, {'error':'TEAM_DATABASE_UNAVAILABLE',
                            'message':'공통 실행 DB를 점검하세요. 기존 기록은 초기화하지 않았습니다.'})
                    else:
                        self._send(200, result)
                    return
                super()._dispatch(method)
        try:
            # Reserve the port BEFORE DB creation/migration/worker startup.
            self.http = ExclusiveHTTPServer((self.host, self.port), Handler)
            # Keep the DB lease until in-flight handlers finish during close.
            self.http.daemon_threads = False
            self.lease = DatabaseLease(self.path)
            self.lease.__enter__()
            self.prepared = _prepare_database(self.path)
            if self.mode == 'full':
                from .worker import Worker
                self.planner = Worker(self.path, estimator, seconds=3)
                self.inquiry_worker = InquiryWorker(self.path, estimator)
            self.runtime.configure('planning', self.planner, enabled=self.planner is not None)
            self.runtime.configure('inquiry', self.inquiry_worker, enabled=self.inquiry_worker is not None)
            for background in (self.planner, self.inquiry_worker):
                if background is not None:
                    self._started_workers.append(background)
                    background.start()
            self.thread = threading.Thread(target=self.http.serve_forever,
                kwargs={'poll_interval':0.1}, daemon=True, name='team-http')
            self.thread.start()
            self.base_url = f'http://{self.host}:{self.http.server_port}'
            return self
        except BaseException:
            self.close()
            raise

    def info(self):
        links = dict(home='/', voice='/paramedic/', voice_test='/paramedic/quick-test.html',
                     transport='/patient/', hospital='/hospital/?hid=H01',
                     status='/api/system/status', runtime='/api/team-runtime')
        return dict(revision=REVISION, mode=self.mode, synthetic=True, baseline_id=BASELINE_ID,
            solver_seconds=3 if self.mode == 'full' else None, inquiry_policy='fixed_3',
            travel_provider='synthetic', external_hospital_messages=False, profile=self.profile,
            release=self.release, dependency_environment=self.dependencies,
            database=inspect_database(self.path), workers=self.runtime.snapshot()['workers'],
            links={key:(self.base_url or '')+value for key,value in links.items()},
            note='메인 음성·이송 진행·병원 검토·추천·중요 알림을 같은 서버와 DB로 제공합니다. 수용·목적지는 담당자가 확정합니다.')

    def close(self):
        # Stop accepting HTTP writes before releasing workers and the DB lease.
        if self.http is not None:
            if self.thread is not None and self.thread.is_alive():
                self.http.shutdown()
                self.thread.join(timeout=5)
            self.http.server_close()
            self.http = None
        for background in reversed(self._started_workers):
            if background.thread.ident is not None:
                background.stop()
        if any(background.thread.is_alive() for background in self._started_workers):
            # Worker.stop has a bounded join. A blocked old worker must not
            # overlap a second runtime just because that join timed out.
            raise RuntimeError('워커가 아직 종료 중입니다. DB 실행 잠금을 유지합니다. 종료 상태를 확인하고 다시 close하세요.')
        self._started_workers.clear()
        if self.lease is not None:
            self.lease.__exit__(None, None, None)
            self.lease = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *_):
        self.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('serve', 'prepare', 'check', 'refresh-fixture-resources', 'create-example'))
    parser.add_argument('--db', default=str(DEFAULT_DB))
    parser.add_argument('--port', type=int, default=DEFAULT_PORT)
    parser.add_argument('--mode', choices=('full', 'voice-only'), default='full')
    parser.add_argument('--allow-environment-difference', action='store_true',
                        help='환경 버전 차이를 명시적으로 허용하는 통합 시험. 기준 성능 실험에는 사용하지 않음')
    parser.add_argument('--patient-id', default='TEAM-COMMON-001')
    args = parser.parse_args(argv)
    try:
        if args.action == 'serve':
            with CommonRuntime(args.db, port=args.port, mode=args.mode,
                    allow_environment_difference=args.allow_environment_difference) as runtime:
                print(json.dumps(runtime.info(), ensure_ascii=False, indent=2), flush=True)
                print('종료: Ctrl+C / 병원 수용·목적지 확정은 담당자가 수행합니다.', flush=True)
                while runtime.thread.is_alive():
                    runtime.thread.join(timeout=0.5)
            return 0
        if args.action == 'check':
            result = dict(profile=verify_profile(args.mode, args.allow_environment_difference),
                          database=inspect_database(args.db))
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result['profile']['ok'] and result['database']['valid'] else 1
        profile = verify_profile(args.mode, args.allow_environment_difference)
        if not profile['ok']:
            raise ValueError('실행 기준 검사 실패: '+json.dumps(profile['issues'], ensure_ascii=False))
        if args.action == 'prepare':
            result = prepare_database(args.db)
        elif args.action == 'refresh-fixture-resources':
            result = refresh_fixture_resources(args.db)
        else:
            result = create_example(args.db, args.patient_id)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except KeyboardInterrupt:
        return 0
    except (ValueError, OSError) as error:
        print(json.dumps({'error':str(error)}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
