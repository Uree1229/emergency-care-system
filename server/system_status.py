"""현재 프로세스의 워커 관측과 읽기 전용 DB 상태. 업무 처리나 외부 호출을 하지 않는다."""
import math
from pathlib import Path
import sqlite3
import threading
import time

WORKER_NAMES = ('planning', 'inquiry')
ERROR_CODES = {'PLANNING_FAILED', 'INQUIRY_FAILED', 'WORKER_FAILED'}
INITIAL_STATES = {'QUEUED', 'RUNNING', 'AWAITING_CONFIRMATION', 'EXHAUSTED', 'PAUSED', 'COMPLETED', 'ERROR'}
ALTERNATIVE_STATES = {'QUEUED', 'RUNNING', 'AWAITING_POSITION', 'AWAITING_CHANGE', 'EXHAUSTED',
                      'PAUSED', 'COMPLETED', 'ERROR'}
REVIEW_STATES = {'MONITORING', 'RECHECK_REQUIRED', 'SEARCHING', 'AWAITING_CHANGE', 'EXHAUSTED',
                 'PAUSED', 'COMPLETED', 'ERROR'}


class WorkerHealth:
    """주기 시작·종료를 메모리에 기록한다. 경과 시간 판단에는 단조 시계를 쓴다."""
    def __init__(self, clock=time.time, monotonic=time.monotonic, stale_after_seconds=180):
        if not math.isfinite(stale_after_seconds) or stale_after_seconds <= 0:
            raise ValueError('stale_after_seconds must be finite and positive')
        self.clock, self.monotonic = clock, monotonic
        self.stale_after_seconds = stale_after_seconds
        self.lock = threading.Lock()
        self.started_at = self.heartbeat_at = self.heartbeat_mono = None
        self.last_cycle_started_at = self.last_cycle_completed_at = self.last_success_at = None
        self.last_error_at = self.last_error_code = None
        self.consecutive_failures = self.failures_total = self.cycles_completed = 0
        self.phase = 'IDLE'

    def _beat(self):
        self.heartbeat_at, self.heartbeat_mono = int(self.clock()), self.monotonic()

    def worker_started(self):
        with self.lock:
            self.started_at = int(self.clock())
            self._beat()

    def cycle_started(self):
        with self.lock:
            self._beat()
            self.last_cycle_started_at = self.heartbeat_at
            self.phase = 'BUSY'

    def cycle_succeeded(self):
        with self.lock:
            self._beat()
            self.last_cycle_completed_at = self.last_success_at = self.heartbeat_at
            self.cycles_completed += 1
            self.consecutive_failures, self.last_error_code = 0, None
            self.phase = 'IDLE'

    def cycle_failed(self, code):
        with self.lock:
            self._beat()
            self.last_cycle_completed_at = self.last_error_at = self.heartbeat_at
            self.last_error_code = code if isinstance(code, str) and code in ERROR_CODES else 'WORKER_FAILED'
            self.consecutive_failures += 1
            self.failures_total += 1
            self.cycles_completed += 1
            self.phase = 'IDLE'

    def snapshot(self, thread_alive, stop_requested=False):
        with self.lock:
            age = None if self.heartbeat_mono is None else max(0, self.monotonic() - self.heartbeat_mono)
            if self.started_at is None:
                state = 'STARTING'
            elif not thread_alive:
                state = 'STOPPED'
            elif age is not None and age > self.stale_after_seconds:
                state = 'STALLED'
            elif stop_requested:
                state = 'STOPPING'
            elif self.consecutive_failures:
                state = 'ERROR'
            elif self.last_cycle_started_at is None:
                state = 'STARTING'
            else:
                state = 'RUNNING'
            return dict(state=state, enabled=True, thread_alive=bool(thread_alive), phase=self.phase,
                        heartbeat_at=self.heartbeat_at,
                        heartbeat_age_seconds=None if age is None else round(age, 3),
                        stale_after_seconds=self.stale_after_seconds,
                        last_cycle_started_at=self.last_cycle_started_at,
                        last_cycle_completed_at=self.last_cycle_completed_at, last_success_at=self.last_success_at,
                        last_error_at=self.last_error_at, last_error_code=self.last_error_code,
                        consecutive_failures=self.consecutive_failures, failures_total=self.failures_total,
                        cycles_completed=self.cycles_completed)


class RuntimeStatus:
    """기동 코드가 등록한 워커만 관측한다. 다른 프로세스는 추정하지 않는다."""
    def __init__(self, clock=time.time, monotonic=time.monotonic):
        self.clock, self.monotonic = clock, monotonic
        self.started_at, self.started_mono = int(clock()), monotonic()
        self.lock, self.workers = threading.Lock(), {}

    def configure(self, name, worker=None, enabled=True):
        if name not in WORKER_NAMES or enabled and worker is None:
            raise ValueError('unknown worker or enabled worker missing')
        with self.lock:
            self.workers[name] = worker if enabled else None

    def snapshot(self):
        with self.lock:
            configured = dict(self.workers)
        workers = {}
        for name in WORKER_NAMES:
            if name not in configured:
                workers[name] = dict(state='UNKNOWN', enabled=None, thread_alive=None)
            elif configured[name] is None:
                workers[name] = dict(state='DISABLED', enabled=False, thread_alive=False)
            else:
                worker = configured[name]
                workers[name] = worker.health.snapshot(worker.thread.is_alive(), worker.stop_event.is_set())
        return dict(server=dict(state='RUNNING', started_at=self.started_at,
                                uptime_seconds=round(max(0, self.monotonic() - self.started_mono), 3)),
                    workers=workers)


def _timestamp(value):
    return value if type(value) is int and value >= 0 else None


def _count(conn, sql, args=()):
    return conn.execute(sql, args).fetchone()[0]


def _states(conn, table, allowed):
    # 테이블 이름은 위의 고정된 호출자만 전달한다. 임의 DB 문자열을 응답하지 않는다.
    result = {}
    for state, count in conn.execute(f'SELECT state,COUNT(*) FROM {table} GROUP BY state'):
        key = state if state in allowed else 'UNKNOWN'
        result[key] = result.get(key, 0) + count
    return result


def _workflow(conn, table, states, now):
    return dict(enabled=_count(conn, f'SELECT COUNT(*) FROM {table} WHERE enabled=1'),
                due=_count(conn, f'SELECT COUNT(*) FROM {table} WHERE enabled=1 AND next_evaluation_at<=?', (now,)),
                errors=_count(conn, f"SELECT COUNT(*) FROM {table} WHERE state='ERROR' OR COALESCE(last_error,'')!=''"),
                by_state=_states(conn, table, states))


def _database_snapshot(conn, now):
    from .service import STALE_AFTER
    state = conn.execute('SELECT revision,completed_revision,lease_until,last_error FROM planning_state WHERE id=1').fetchone()
    if state is None or any(type(value) is not int for value in state[:3]):
        raise sqlite3.DatabaseError('invalid planning state')
    revision, completed, lease_until, last_error = state
    total = _count(conn, 'SELECT COUNT(*) FROM planning_results')
    fresh = _count(conn, 'SELECT COUNT(*) FROM planning_results WHERE revision=? AND expires_at>?', (revision, now))
    expired = _count(conn, 'SELECT COUNT(*) FROM planning_results WHERE expires_at<=?', (now,))
    planning = dict(revision=revision, completed_revision=completed, pending=revision != completed or expired > 0,
                    lease_active=lease_until > now, lease_remaining_seconds=max(0, lease_until - now),
                    result_counts=dict(total=total, fresh=fresh, stale=total - fresh),
                    last_result_at=_timestamp(_count(conn, 'SELECT MAX(created_at) FROM planning_results')),
                    error_code='PLANNING_FAILED' if last_error else None)
    reviews = dict(enabled=_count(conn, 'SELECT COUNT(*) FROM transport_reviews WHERE enabled=1'),
                   recheck_required=_count(conn, "SELECT COUNT(*) FROM transport_reviews WHERE enabled=1 AND state='RECHECK_REQUIRED'"),
                   errors=_count(conn, "SELECT COUNT(*) FROM transport_reviews WHERE state='ERROR' OR COALESCE(last_error,'')!=''"),
                   by_state=_states(conn, 'transport_reviews', REVIEW_STATES),
                   last_checked_at=_timestamp(_count(conn, 'SELECT MAX(last_checked_at) FROM transport_reviews')))
    workflows = dict(initial=_workflow(conn, 'inquiry_workflows', INITIAL_STATES, now),
                     alternative=_workflow(conn, 'transport_inquiry_workflows', ALTERNATIVE_STATES, now),
                     transport_reviews=reviews)
    public = dict(total=_count(conn, 'SELECT COUNT(*) FROM public_api_snapshots'),
                  latest_fetched_at=_timestamp(_count(conn, 'SELECT MAX(fetched_at) FROM public_api_snapshots')),
                  latest_observed_at=_timestamp(_count(conn, 'SELECT MAX(source_observed_at) FROM public_api_snapshots')),
                  stale=_count(conn, 'SELECT COUNT(*) FROM public_api_snapshots WHERE source_observed_at<?', (now - STALE_AFTER,)),
                  unknown_observed_at=_count(conn, 'SELECT COUNT(*) FROM public_api_snapshots WHERE source_observed_at IS NULL'))
    run = conn.execute('SELECT started_at,finished_at,status,error FROM public_sync_runs ORDER BY id DESC LIMIT 1').fetchone()
    sync = dict(last_started_at=_timestamp(run[0]) if run else None,
                last_finished_at=_timestamp(run[1]) if run else None,
                status=run[2] if run and run[2] in ('RUNNING', 'SUCCESS', 'PARTIAL', 'ERROR') else
                       ('UNKNOWN' if run else 'NOT_COLLECTED'),
                last_error_code='PUBLIC_SYNC_FAILED' if run and (run[2] == 'ERROR' or run[3]) else None)
    data = dict(hospitals=_count(conn, 'SELECT COUNT(*) FROM hospitals'),
                operating_resources=_count(conn, 'SELECT COUNT(*) FROM resources'),
                stale_operating_resources=_count(conn, 'SELECT COUNT(*) FROM resources WHERE updated_at<?', (now - STALE_AFTER,)),
                public_snapshots=public, public_sync=sync)
    return planning, workflows, data


def read_status(path, now, runtime=None, timeout=.25):
    """별도 읽기 연결의 일관된 스냅샷. 파일 생성·마이그레이션·만료 처리도 금지한다."""
    observed = runtime.snapshot() if runtime is not None else dict(
        server=dict(state='RUNNING', started_at=None, uptime_seconds=None),
        workers={name: dict(state='UNKNOWN', enabled=None, thread_alive=None) for name in WORKER_NAMES})
    report = dict(schema_version=1, checked_at=now, scope='CURRENT_PROCESS', status='UNKNOWN', **observed,
                  database=dict(state='OK', readable=True, error_code=None),
                  planning=None, workflows=None, data=None, issues=[])
    conn = None
    try:
        uri = Path(path).resolve().as_uri() + '?mode=ro'
        conn = sqlite3.connect(uri, uri=True, timeout=timeout, isolation_level=None)
        deadline = time.monotonic() + timeout
        conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        conn.execute('BEGIN')
        report['planning'], report['workflows'], report['data'] = _database_snapshot(conn, now)
        conn.execute('ROLLBACK')
    except (sqlite3.Error, OSError, ValueError):
        report.update(status='UNAVAILABLE', database=dict(state='UNAVAILABLE', readable=False,
                                                         error_code='DATABASE_UNAVAILABLE'),
                      planning=None, workflows=None, data=None,
                      issues=[dict(code='DATABASE_UNAVAILABLE', component='database')])
        return report
    finally:
        if conn is not None:
            conn.close()
    for name, worker in report['workers'].items():
        if worker['state'] in ('ERROR', 'STALLED', 'STOPPED', 'STOPPING'):
            report['issues'].append(dict(code='WORKER_' + worker['state'], component=name))
    for name, jobs in report['workflows'].items():
        if jobs['errors']:
            report['issues'].append(dict(code='JOB_ERRORS', component=name))
    if report['planning']['error_code']:
        report['issues'].append(dict(code='PLANNING_FAILED', component='planning'))
    if report['data']['public_sync']['last_error_code']:
        report['issues'].append(dict(code='PUBLIC_SYNC_FAILED', component='public_sync'))
    report['status'] = 'DEGRADED' if report['issues'] else (
        'UNKNOWN' if any(w['state'] == 'UNKNOWN' for w in report['workers'].values()) else 'OK')
    return report
