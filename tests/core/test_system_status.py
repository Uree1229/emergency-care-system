"""운영 상태 조회는 업무를 진행하거나 환자 정보를 노출하지 않는다."""
import json
import sqlite3
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from server import db, service, system_status
from server.app import make_handler
from server.inquiry_worker import InquiryWorker
from server.worker import Worker
from test_server import Base, EST, T0


class FakeClock:
    def __init__(self, wall=1000, monotonic=100):
        self.wall = wall
        self.elapsed = monotonic

    def time(self):
        return self.wall

    def monotonic(self):
        return self.elapsed

    def advance(self, seconds):
        self.wall += seconds
        self.elapsed += seconds


class FakeThread:
    def __init__(self, alive=True):
        self.alive = alive

    def is_alive(self):
        return self.alive


class FakeWorker:
    def __init__(self, clock, alive=True):
        self.thread = FakeThread(alive)
        self.stop_event = threading.Event()
        self.health = system_status.WorkerHealth(clock=clock.time, monotonic=clock.monotonic,
                                                  stale_after_seconds=10)


class WorkerHealthContract(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.health = system_status.WorkerHealth(clock=self.clock.time, monotonic=self.clock.monotonic,
                                                  stale_after_seconds=10)

    def test_cycle_reports_busy_success_and_idle_without_work_is_success(self):
        self.health.worker_started()
        self.health.cycle_started()
        busy = self.health.snapshot(thread_alive=True)
        self.assertEqual(busy['phase'], 'BUSY')
        self.assertEqual(busy['last_cycle_started_at'], 1000)
        self.clock.advance(2)
        self.health.cycle_succeeded()
        result = self.health.snapshot(thread_alive=True)
        self.assertEqual((result['state'], result['phase']), ('RUNNING', 'IDLE'))
        self.assertEqual(result['last_success_at'], 1002)
        self.assertEqual(result['last_cycle_completed_at'], 1002)
        self.assertEqual(result['cycles_completed'], 1)
        self.assertEqual(result['consecutive_failures'], 0)
        self.assertIsNone(result['last_error_code'])

    def test_errors_recover_but_failure_history_is_retained(self):
        self.health.worker_started()
        for _ in range(2):
            self.health.cycle_started()
            self.clock.advance(1)
            self.health.cycle_failed('PLANNING_FAILED')
        failed = self.health.snapshot(thread_alive=True)
        self.assertEqual(failed['state'], 'ERROR')
        self.assertEqual(failed['last_error_code'], 'PLANNING_FAILED')
        self.assertEqual((failed['consecutive_failures'], failed['failures_total']), (2, 2))
        failure_at = failed['last_error_at']
        self.health.cycle_started()
        self.clock.advance(1)
        self.health.cycle_succeeded()
        recovered = self.health.snapshot(thread_alive=True)
        self.assertEqual(recovered['state'], 'RUNNING')
        self.assertEqual(recovered['consecutive_failures'], 0)
        self.assertEqual(recovered['failures_total'], 2)
        self.assertEqual(recovered['last_error_at'], failure_at)
        self.assertIsNone(recovered['last_error_code'])

    def test_stall_uses_monotonic_time_and_is_distinct_from_thread_death(self):
        self.health.worker_started()
        self.health.cycle_started()
        self.clock.wall += 86400
        self.assertNotEqual(self.health.snapshot(thread_alive=True)['state'], 'STALLED')
        self.clock.advance(11)
        stalled = self.health.snapshot(thread_alive=True)
        self.assertEqual(stalled['state'], 'STALLED')
        self.assertEqual(stalled['heartbeat_age_seconds'], 11)
        self.assertEqual(self.health.snapshot(thread_alive=False)['state'], 'STOPPED')

    def test_stop_request_reports_stopping_until_thread_has_exited(self):
        self.health.worker_started()
        self.health.cycle_started()
        self.assertEqual(self.health.snapshot(thread_alive=True, stop_requested=True)['state'], 'STOPPING')
        self.assertEqual(self.health.snapshot(thread_alive=False, stop_requested=True)['state'], 'STOPPED')

    def test_error_codes_never_echo_arbitrary_messages(self):
        secret = 'secret-key C:/private/patient-name.db'
        self.health.worker_started()
        self.health.cycle_started()
        self.health.cycle_failed(secret)
        self.assertNotIn(secret, json.dumps(self.health.snapshot(thread_alive=True)))

    def test_runtime_disabled_unknown_and_registered_thread_are_distinct(self):
        runtime = system_status.RuntimeStatus(clock=self.clock.time, monotonic=self.clock.monotonic)
        self.assertEqual(runtime.snapshot()['workers']['planning']['state'], 'UNKNOWN')
        runtime.configure('planning', enabled=False)
        disabled = runtime.snapshot()['workers']['planning']
        self.assertEqual(disabled['state'], 'DISABLED')
        self.assertFalse(disabled['enabled'])
        worker = FakeWorker(self.clock)
        worker.health.worker_started()
        worker.health.cycle_started()
        worker.health.cycle_succeeded()
        runtime.configure('inquiry', worker=worker)
        self.clock.advance(3)
        state = runtime.snapshot()
        self.assertEqual(state['workers']['inquiry']['state'], 'RUNNING')
        self.assertEqual(state['server']['started_at'], 1000)
        self.assertEqual(state['server']['uptime_seconds'], 3)
        worker.thread.alive = False
        self.assertEqual(runtime.snapshot()['workers']['inquiry']['state'], 'STOPPED')

    def test_runtime_snapshot_can_overlap_cycle_updates(self):
        runtime = system_status.RuntimeStatus(clock=self.clock.time, monotonic=self.clock.monotonic)
        worker = FakeWorker(self.clock)
        runtime.configure('planning', worker=worker)
        worker.health.worker_started()
        errors = []

        def update():
            try:
                for _ in range(250):
                    worker.health.cycle_started()
                    worker.health.cycle_succeeded()
            except BaseException as exc:
                errors.append(exc)

        thread = threading.Thread(target=update)
        thread.start()
        for _ in range(250):
            snapshot = runtime.snapshot()['workers']['planning']
            self.assertIn(snapshot['phase'], ('BUSY', 'IDLE'))
            self.assertGreaterEqual(snapshot['cycles_completed'], 0)
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(runtime.snapshot()['workers']['planning']['cycles_completed'], 250)


class SystemStatusBase(Base):
    def runtime(self, planning=True, inquiry=True):
        clock = FakeClock(T0, 100)
        runtime = system_status.RuntimeStatus(clock=clock.time, monotonic=clock.monotonic)
        for name, enabled in (('planning', planning), ('inquiry', inquiry)):
            if not enabled:
                runtime.configure(name, enabled=False)
                continue
            worker = FakeWorker(clock)
            worker.health.worker_started()
            worker.health.cycle_started()
            worker.health.cycle_succeeded()
            runtime.configure(name, worker=worker)
        return runtime

    @contextmanager
    def http(self, path=None, runtime=None, now=T0):
        handler = make_handler(path or self.path, EST, clock=lambda: now, runtime=runtime)
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def call(endpoint='/api/system/status'):
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{server.server_port}{endpoint}', timeout=5) as response:
                    return response.status, json.load(response), response.headers
            except urllib.error.HTTPError as exc:
                with exc:
                    return exc.code, json.load(exc), exc.headers

        try:
            yield call
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


class StatusReadContract(SystemStatusBase):
    def test_default_runtime_is_unknown_and_does_not_infer_liveness_from_queue(self):
        result = system_status.read_status(self.path, T0)
        self.assertEqual(result['schema_version'], 1)
        self.assertEqual(result['scope'], 'CURRENT_PROCESS')
        self.assertEqual(result['checked_at'], T0)
        self.assertEqual(result['status'], 'UNKNOWN')
        self.assertEqual(result['database']['state'], 'OK')
        self.assertTrue(result['database']['readable'])
        for worker in result['workers'].values():
            self.assertEqual(worker['state'], 'UNKNOWN')
        self.assertIsNone(result['server']['started_at'])
        self.assertIsNone(result['server']['uptime_seconds'])
        self.assertTrue(result['planning']['pending'])

    def test_disabled_is_explicit_and_healthy_workers_allow_ok(self):
        result = system_status.read_status(self.path, T0, runtime=self.runtime(planning=False))
        self.assertEqual(result['status'], 'OK')
        self.assertEqual(result['workers']['planning']['state'], 'DISABLED')
        self.assertEqual(result['workers']['inquiry']['state'], 'RUNNING')
        self.assertTrue(result['planning']['pending'])

    def test_result_staleness_and_lease_do_not_claim_worker_is_running(self):
        for pid in ('A', 'B', 'C'):
            self.patient(pid)
        revision = self.conn.execute('SELECT revision FROM planning_state').fetchone()[0]
        self.conn.execute('UPDATE planning_state SET completed_revision=?,lease_until=?,lease_owner=?',
                          (revision, T0 + 40, 'private-owner'))
        for pid, result_revision, expires_at in (('A', revision, T0 + 20),
                                                  ('B', revision, T0), ('C', revision - 1, T0 + 20)):
            self.conn.execute('INSERT INTO planning_results VALUES(?,?,?,?,?)',
                              (pid, result_revision, T0 - 2, expires_at, '{"private":"sensitive"}'))
        result = system_status.read_status(self.path, T0)
        self.assertEqual(result['planning']['result_counts'], dict(total=3, fresh=1, stale=2))
        self.assertTrue(result['planning']['pending'])
        self.assertTrue(result['planning']['lease_active'])
        self.assertEqual(result['planning']['lease_remaining_seconds'], 40)
        self.assertEqual(result['planning']['last_result_at'], T0 - 2)
        self.assertEqual(result['workers']['planning']['state'], 'UNKNOWN')

    def test_synchronized_revision_and_only_fresh_results_are_not_pending(self):
        self.patient('A')
        revision = self.conn.execute('SELECT revision FROM planning_state').fetchone()[0]
        self.conn.execute('UPDATE planning_state SET completed_revision=?,lease_until=?', (revision, T0 - 1))
        self.conn.execute('INSERT INTO planning_results VALUES(?,?,?,?,?)', ( 'A', revision, T0, T0 + 20, '{}'))
        result = system_status.read_status(self.path, T0, runtime=self.runtime())
        self.assertFalse(result['planning']['pending'])
        self.assertFalse(result['planning']['lease_active'])
        self.assertEqual(result['planning']['lease_remaining_seconds'], 0)

    def test_stopped_worker_and_persisted_errors_degrade_without_exception_detail(self):
        runtime = self.runtime()
        stopped = FakeWorker(FakeClock(T0, 100), alive=False)
        stopped.health.worker_started()
        runtime.configure('planning', worker=stopped)
        self.conn.execute('UPDATE planning_state SET last_error=?', ('private-patient-key-path',))
        result = system_status.read_status(self.path, T0, runtime=runtime)
        self.assertEqual(result['status'], 'DEGRADED')
        self.assertEqual(result['workers']['planning']['state'], 'STOPPED')
        self.assertEqual(result['planning']['error_code'], 'PLANNING_FAILED')
        self.assertNotIn('private-patient-key-path', json.dumps(result))

    def test_aggregate_due_and_error_workflows_and_unknown_states(self):
        for pid, enabled, state, due, error in (('A', 1, 'QUEUED', T0, None),
                                                ('B', 1, 'ERROR', T0 + 1, 'secret-error'),
                                                ('C', 0, 'PAUSED', T0 - 1, None),
                                                ('D', 1, 'PRIVATE-NAME', T0, None)):
            self.patient(pid)
            self.conn.execute('INSERT INTO inquiry_workflows '
                              '(patient_id,enabled,state,started_at,deadline_at,next_evaluation_at,updated_at,last_error) '
                              'VALUES(?,?,?,?,?,?,?,?)', (pid, enabled, state, T0 - 5, T0 + 300, due, T0 - 2, error))
        result = system_status.read_status(self.path, T0, runtime=self.runtime())
        initial = result['workflows']['initial']
        self.assertEqual(initial['enabled'], 3)
        self.assertGreaterEqual(initial['due'], 1)
        self.assertEqual(initial['errors'], 1)
        self.assertEqual(initial['by_state']['UNKNOWN'], 1)
        self.assertNotIn('PRIVATE-NAME', json.dumps(result))
        self.assertNotIn('secret-error', json.dumps(result))
        self.assertEqual(result['status'], 'DEGRADED')

    def test_alternative_search_and_transport_rechecks_are_separate_aggregates(self):
        for pid, enabled, state, checked in (('A', 1, 'MONITORING', T0 - 5),
                                             ('B', 1, 'RECHECK_REQUIRED', T0 - 1),
                                             ('C', 0, 'ERROR', None)):
            self.patient(pid)
            self.conn.execute('INSERT INTO transport_reviews '
                              '(patient_id,enabled,state,hospital_id,updated_at,last_checked_at,last_error) '
                              'VALUES(?,?,?,?,?,?,?)',
                              (pid, enabled, state, 'H1', T0, checked,
                               'private-review-error' if state == 'ERROR' else None))
        for pid, enabled, state, due in (('A', 1, 'RUNNING', T0),
                                         ('B', 0, 'PAUSED', T0 - 1), ('C', 1, 'ERROR', T0 + 1)):
            self.conn.execute('INSERT INTO transport_inquiry_workflows '
                              '(patient_id,source_hospital_id,episode_start_event,enabled,state,started_at,'
                              'deadline_at,next_evaluation_at,updated_at,last_error) VALUES(?,?,?,?,?,?,?,?,?,?)',
                              (pid, 'H1', 0, enabled, state, T0 - 1, T0 + 300, due, T0,
                               'private-alternative-error' if state == 'ERROR' else None))
        before = tuple(self.conn.iterdump())
        result = system_status.read_status(self.path, T0, runtime=self.runtime())
        self.assertEqual(result['workflows']['alternative'],
                         dict(enabled=2, due=1, errors=1, by_state=dict(RUNNING=1, PAUSED=1, ERROR=1)))
        self.assertEqual(result['workflows']['transport_reviews'],
                         dict(enabled=2, recheck_required=1, errors=1,
                              by_state=dict(MONITORING=1, RECHECK_REQUIRED=1, ERROR=1),
                              last_checked_at=T0 - 1))
        self.assertEqual(result['workflows']['initial']['enabled'], 0)
        self.assertEqual(tuple(self.conn.iterdump()), before)
        self.assertNotIn('private-', json.dumps(result))

    def test_worker_error_is_http_200_diagnostic_with_degraded_status(self):
        runtime = self.runtime(planning=False, inquiry=False)
        worker = FakeWorker(FakeClock(T0, 100))
        worker.health.worker_started()
        worker.health.cycle_started()
        worker.health.cycle_failed('INQUIRY_FAILED')
        runtime.configure('inquiry', worker=worker)
        with self.http(runtime=runtime) as call:
            code, result, _ = call()
        self.assertEqual(code, 200)
        self.assertEqual(result['status'], 'DEGRADED')
        self.assertEqual(result['workers']['inquiry']['state'], 'ERROR')
        self.assertEqual(result['workers']['inquiry']['last_error_code'], 'INQUIRY_FAILED')
        self.assertEqual(result['database']['state'], 'OK')

    def test_data_freshness_distinguishes_unknown_observation_from_stale(self):
        service.set_resource(self.conn, T0 - service.STALE_AFTER - 1, 'H1', 'ed_bed', 1, 0)
        for endpoint, observed, fetched in (('fresh', T0, T0),
                                             ('stale', T0 - service.STALE_AFTER - 1, T0),
                                             ('unknown', None, T0 + 1)):
            self.conn.execute('INSERT INTO public_api_snapshots VALUES(?,?,?,?,?,?)',
                              ('H1', endpoint, observed, fetched, '{}', '{"key":"secret-api-value"}'))
        result = system_status.read_status(self.path, T0, runtime=self.runtime())
        self.assertEqual(result['data']['hospitals'], 3)
        self.assertEqual(result['data']['operating_resources'], 3)
        self.assertEqual(result['data']['stale_operating_resources'], 1)
        self.assertEqual(result['data']['public_snapshots'], dict(total=3, latest_fetched_at=T0 + 1,
                                                                 latest_observed_at=T0, stale=1,
                                                                 unknown_observed_at=1))
        self.assertEqual(result['data']['public_sync']['status'], 'NOT_COLLECTED')
        self.assertNotIn('secret-api-value', json.dumps(result))

    def test_public_collection_history_sanitizes_error_and_arbitrary_state(self):
        self.conn.execute('INSERT INTO public_sync_runs(region,started_at,finished_at,status,error,stats) '
                          'VALUES(?,?,?,?,?,?)', ('private-region', T0 - 10, T0 - 5, 'ERROR',
                                                 'private-api-key-and-path', '{"secret":"value"}'))
        result = system_status.read_status(self.path, T0, runtime=self.runtime())
        sync = result['data']['public_sync']
        self.assertEqual((sync['last_started_at'], sync['last_finished_at'], sync['status']),
                         (T0 - 10, T0 - 5, 'ERROR'))
        self.assertIsNotNone(sync['last_error_code'])
        self.assertNotIn('private-', json.dumps(result))
        self.conn.execute('UPDATE public_sync_runs SET status=?', ('PRIVATE-STATUS',))
        sync = system_status.read_status(self.path, T0)['data']['public_sync']
        self.assertEqual(sync['status'], 'UNKNOWN')

    def test_get_never_runs_expiry_workflow_review_or_external_api(self):
        self.patient('A', auto_inquiry=True, symptoms='private-patient-symptoms', contact='private-contact')
        self.patient('B')
        iid = self.accepted('B', 'H1')
        self.conn.execute('UPDATE inquiries SET expires_at=? WHERE id=?', (T0 - 1, iid))
        before = tuple(self.conn.iterdump())
        with patch('server.db.connect', side_effect=AssertionError('must use readonly connection')):
            with patch.object(EST, 'estimate', side_effect=AssertionError('must not call routing API')):
                with self.http(runtime=self.runtime()) as call:
                    for _ in range(3):
                        status, result, headers = call()
                        self.assertEqual(status, 200)
                        self.assertEqual(headers['Cache-Control'], 'no-store')
                        text = json.dumps(result)
                        self.assertNotIn('private-patient-symptoms', text)
                        self.assertNotIn('private-contact', text)
                        self.assertNotIn(self.path, text)
        self.assertEqual(tuple(self.conn.iterdump()), before)
        self.assertEqual(self.conn.execute('SELECT state FROM inquiries WHERE id=?', (iid,)).fetchone()[0], 'ACCEPTED')

    def test_readonly_status_does_not_need_write_access_or_modify_journal_mode(self):
        self.conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        self.conn.execute('PRAGMA journal_mode=DELETE')
        before = tuple(self.conn.iterdump())
        result = system_status.read_status(self.path, T0)
        self.assertEqual(result['database']['state'], 'OK')
        self.assertEqual(self.conn.execute('PRAGMA journal_mode').fetchone()[0], 'delete')
        self.assertEqual(tuple(self.conn.iterdump()), before)


class StatusDatabaseFailures(SystemStatusBase):
    def assert_unavailable(self, result):
        self.assertEqual(result['status'], 'UNAVAILABLE')
        self.assertEqual(result['database']['state'], 'UNAVAILABLE')
        self.assertFalse(result['database']['readable'])
        self.assertEqual(result['database']['error_code'], 'DATABASE_UNAVAILABLE')
        for key in ('planning', 'workflows', 'data'):
            self.assertIsNone(result[key])
        text = json.dumps(result)
        self.assertNotIn(self.tmp.name, text)
        self.assertNotIn('sqlite', text.lower())

    def test_missing_file_is_unavailable_and_is_never_created(self):
        path = str(Path(self.tmp.name) / 'missing-private.db')
        with self.http(path=path) as call:
            status, result, _ = call()
        self.assertEqual(status, 503)
        self.assert_unavailable(result)
        self.assertFalse(Path(path).exists())

    def test_corrupt_database_returns_sanitized_503(self):
        path = Path(self.tmp.name) / 'corrupt-private.db'
        path.write_bytes(b'private-patient-name not a sqlite database')
        before = path.read_bytes()
        with self.http(path=str(path)) as call:
            status, result, _ = call()
        self.assertEqual(status, 503)
        self.assert_unavailable(result)
        self.assertEqual(path.read_bytes(), before)

    def test_legacy_schema_is_not_migrated_by_status_read(self):
        path = str(Path(self.tmp.name) / 'legacy.db')
        conn = sqlite3.connect(path)
        conn.execute('CREATE TABLE hospitals(id TEXT PRIMARY KEY)')
        conn.commit()
        before = tuple(conn.iterdump())
        result = system_status.read_status(path, T0)
        self.assert_unavailable(result)
        self.assertEqual(tuple(conn.iterdump()), before)
        conn.close()

    def test_busy_database_timeout_is_bounded_and_read_can_recover(self):
        self.conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        self.conn.execute('PRAGMA journal_mode=DELETE')
        self.conn.execute('BEGIN EXCLUSIVE')
        try:
            started = time.monotonic()
            result = system_status.read_status(self.path, T0, timeout=.05)
            self.assertLess(time.monotonic() - started, 1.0)
            self.assert_unavailable(result)
        finally:
            self.conn.execute('ROLLBACK')
        self.assertEqual(system_status.read_status(self.path, T0)['database']['state'], 'OK')

    def test_database_failure_retains_separate_worker_diagnostics(self):
        runtime = self.runtime(planning=False)
        result = system_status.read_status(str(Path(self.tmp.name) / 'missing.db'), T0, runtime=runtime)
        self.assert_unavailable(result)
        self.assertEqual(result['workers']['planning']['state'], 'DISABLED')
        self.assertEqual(result['workers']['inquiry']['state'], 'RUNNING')


class RealWorkerStatus(SystemStatusBase):
    def test_planning_worker_reports_actual_busy_and_stopped_thread(self):
        entered = threading.Event()
        release = threading.Event()

        def run_once(*args, **kwargs):
            entered.set()
            if not release.wait(timeout=5):
                raise AssertionError('test failed to release worker')
            return False

        worker = Worker(self.path, EST, seconds=0)
        runtime = system_status.RuntimeStatus()
        runtime.configure('planning', worker=worker)
        runtime.configure('inquiry', enabled=False)
        with patch('server.worker.run_once', side_effect=run_once):
            worker.start()
            try:
                self.assertTrue(entered.wait(timeout=3))
                busy = runtime.snapshot()['workers']['planning']
                self.assertEqual(busy['phase'], 'BUSY')
                self.assertTrue(busy['thread_alive'])
                worker.stop_event.set()
                self.assertEqual(runtime.snapshot()['workers']['planning']['state'], 'STOPPING')
            finally:
                release.set()
                worker.stop()
        final = runtime.snapshot()['workers']['planning']
        self.assertEqual(final['state'], 'STOPPED')
        self.assertGreaterEqual(final['cycles_completed'], 1)
        self.assertIsNotNone(final['last_success_at'])

    def test_inquiry_worker_records_failure_then_recovers_on_successful_cycle(self):
        first_failed = threading.Event()
        release = threading.Event()
        calls = []

        def run_once(*args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                first_failed.set()
                raise RuntimeError('private-patient-key-path')
            return False

        worker = InquiryWorker(self.path, EST, poll_seconds=.02, clock=lambda: T0)
        runtime = system_status.RuntimeStatus()
        runtime.configure('planning', enabled=False)
        runtime.configure('inquiry', worker=worker)
        with patch('server.inquiry_worker.run_once', side_effect=run_once):
            with patch('server.inquiry_worker.transport_reassignment.run_once', return_value=False):
                worker.start()
                try:
                    self.assertTrue(first_failed.wait(timeout=3))
                    deadline = time.monotonic() + 3
                    while time.monotonic() < deadline:
                        current = runtime.snapshot()['workers']['inquiry']
                        if current['failures_total'] and current['last_success_at'] is not None:
                            break
                        release.wait(.01)
                    current = runtime.snapshot()['workers']['inquiry']
                    self.assertEqual(current['failures_total'], 1)
                    self.assertEqual(current['consecutive_failures'], 0)
                    self.assertIsNotNone(current['last_error_at'])
                    self.assertIsNotNone(current['last_success_at'])
                    self.assertIsNone(current['last_error_code'])
                    self.assertNotIn('private-patient-key-path', json.dumps(current))
                finally:
                    worker.stop()
        self.assertEqual(runtime.snapshot()['workers']['inquiry']['state'], 'STOPPED')


if __name__ == '__main__':
    unittest.main()
