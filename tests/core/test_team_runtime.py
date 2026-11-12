"""Common launcher isolation and real worker lifecycle; synthetic local data only."""
import json
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import socket
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.request import urlopen

from server import db, service, synthetic, team_runtime
from server import voice_input as voice


NOW = 2_000_000_000


class TeamRuntimeTests(unittest.TestCase):
    def setUp(self):
        parent = Path(__file__).resolve().parents[2]/'.codex-build/team-runtime-20261030/tmp'
        parent.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='runtime-guard-', dir=parent)
        self.root = Path(self.tmp.name)
        self.path = self.root/'common.db'

    def tearDown(self):
        self.tmp.cleanup()

    def get(self, runtime, path):
        with urlopen(runtime.base_url+path, timeout=5) as response:
            return json.load(response)

    def wait_running(self, runtime):
        until = time.monotonic()+6
        last = None
        while time.monotonic() < until:
            last = self.get(runtime, '/api/system/status')
            if all(last['workers'][name]['state'] == 'RUNNING' for name in ('planning', 'inquiry')):
                return last
            time.sleep(.05)
        self.fail('Actual worker health did not reach RUNNING: '+repr(last))

    def business(self):
        with closing(sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro', uri=True)) as conn:
            tables = ('patients', 'inquiries', 'resources', 'voice_sessions', 'voice_utterances',
                      'voice_revisions', 'voice_requests', 'patient_amendments', 'events')
            return {table: [tuple(row) for row in conn.execute('SELECT * FROM '+table+' ORDER BY rowid')]
                    for table in tables}

    def test_fresh_database_has_synthetic_catalog_and_profile_capability_names(self):
        prepared = team_runtime.prepare_database(self.path, now=NOW)
        self.assertFalse(prepared['reused'])
        inspected = team_runtime.inspect_database(self.path, now=NOW)
        self.assertTrue(inspected['valid'])
        self.assertEqual(inspected['hospital_count'], 10)
        self.assertEqual(inspected['resources']['count'], 20)
        self.assertEqual(inspected['resources']['stale_count'], 0)
        with closing(sqlite3.connect(self.path)) as conn:
            hospitals = conn.execute('SELECT id,source,capabilities FROM hospitals ORDER BY id').fetchall()
            self.assertEqual({row[0] for row in hospitals}, {h[0] for h in synthetic.HOSPITALS})
            self.assertTrue(all(row[1] == 'synthetic' and '응급의학' in json.loads(row[2]) for row in hospitals))
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM patients').fetchone()[0], 0)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM inquiries').fetchone()[0], 0)

    def test_prepare_reuse_preserves_business_rows_and_does_not_refresh_old_resources(self):
        team_runtime.prepare_database(self.path, now=NOW)
        conn = db.connect(str(self.path))
        try:
            service.create_patient(conn, NOW+1, 'SAVED-RUNTIME-P',
                dict(reporter_role='transporter', consent=True, extra={'synthetic': True},
                     priority=2, priority_source='expert', lat=35.16, lon=126.85))
            service.set_resource(conn, NOW+2, 'H01', 'ed_bed', 7, 2, status='limited')
            voice.create(conn, NOW+3, dict(actor_id='RUNTIME-QA', client_request_id='saved-voice'))
        finally:
            conn.close()
        before = self.business()
        prepared = team_runtime.prepare_database(self.path, now=NOW+7200)
        self.assertTrue(prepared['reused'])
        self.assertEqual(before, self.business())
        self.assertEqual(team_runtime.inspect_database(self.path, now=NOW+7200)['resources']['stale_count'], 20)

    def test_unknown_sqlite_is_rejected_without_schema_or_content_changes(self):
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute('CREATE TABLE existing_business(id INTEGER PRIMARY KEY, value TEXT)')
            conn.execute("INSERT INTO existing_business VALUES(1,'preserve')")
        content = self.path.read_bytes()
        modified = self.path.stat().st_mtime_ns
        for operation in (team_runtime.prepare_database, team_runtime.inspect_database):
            with self.subTest(operation=operation.__name__), self.assertRaises(ValueError):
                operation(self.path)
            self.assertEqual(self.path.read_bytes(), content)
            self.assertEqual(self.path.stat().st_mtime_ns, modified)
        with closing(sqlite3.connect(self.path)) as conn:
            self.assertEqual(conn.execute('SELECT name FROM sqlite_master WHERE type=\'table\'').fetchall(),
                             [('existing_business',)])

    def test_empty_or_non_database_file_is_not_replaced(self):
        for name, content in (('empty.db', b''), ('not-sqlite.db', b'preserve non-db data')):
            path = self.root/name
            path.write_bytes(content)
            with self.subTest(name=name), self.assertRaises(ValueError):
                team_runtime.prepare_database(path)
            self.assertEqual(path.read_bytes(), content)

    def test_inspection_is_read_only_and_does_not_create_missing_database(self):
        missing = team_runtime.inspect_database(self.path)
        self.assertFalse(missing['exists'])
        self.assertFalse(self.path.exists())
        team_runtime.prepare_database(self.path, now=NOW)
        before = self.business()
        modified = self.path.stat().st_mtime_ns
        inspected = team_runtime.inspect_database(self.path, now=NOW+700)
        self.assertTrue(inspected['valid'])
        self.assertEqual(inspected['resources']['stale_count'], 20)
        self.assertEqual(before, self.business())
        self.assertEqual(self.path.stat().st_mtime_ns, modified)

    def test_marker_does_not_allow_a_mixed_public_catalog(self):
        team_runtime.prepare_database(self.path, now=NOW)
        conn = db.connect(str(self.path))
        try:
            service.upsert_hospital(conn, dict(id='PUBLIC-UNRELATED', name='Preserve unrelated data',
                source='public', lat=35, lon=127, capabilities=[]))
        finally:
            conn.close()
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            team_runtime.prepare_database(self.path)
        self.assertEqual(before, self.path.read_bytes())

    def test_wrong_marker_version_or_missing_marker_is_rejected_without_repair(self):
        for name, sql in (
                ('wrong-version', "UPDATE team_runtime_meta SET schema_version=2 WHERE id=1"),
                ('wrong-catalog', "UPDATE team_runtime_meta SET catalog_id='different' WHERE id=1"),
                ('wrong-baseline', "UPDATE team_runtime_meta SET baseline_id='different' WHERE id=1"),
                ('missing-marker', 'DELETE FROM team_runtime_meta')):
            path = self.root/(name+'.db')
            team_runtime.prepare_database(path, now=NOW)
            with closing(sqlite3.connect(path)) as conn, conn:
                conn.execute(sql)
            before = path.read_bytes()
            with self.subTest(name=name), self.assertRaises(ValueError):
                team_runtime.prepare_database(path)
            self.assertEqual(before, path.read_bytes())

    def test_database_lease_rejects_second_owner_and_can_be_reacquired(self):
        with team_runtime.DatabaseLease(self.path):
            with self.assertRaises((ValueError, RuntimeError, OSError)):
                with team_runtime.DatabaseLease(self.path):
                    self.fail('Second lease unexpectedly acquired')
        with team_runtime.DatabaseLease(self.path):
            pass
        self.assertFalse(self.path.exists())

    def test_occupied_port_is_rejected_before_database_creation(self):
        with socket.socket() as occupied:
            occupied.bind(('127.0.0.1', 0))
            occupied.listen()
            runtime = team_runtime.CommonRuntime(self.path, port=occupied.getsockname()[1], mode='voice-only')
            with self.assertRaises(OSError):
                runtime.start()
            runtime.close()
        self.assertFalse(self.path.exists())

    def test_legacy_reusable_http_listener_cannot_share_the_launcher_port(self):
        class LegacyReusableHTTPServer(ThreadingHTTPServer):
            allow_reuse_address = True

        with LegacyReusableHTTPServer(('127.0.0.1', 0), BaseHTTPRequestHandler) as legacy:
            runtime = team_runtime.CommonRuntime(self.path, port=legacy.server_port, mode='voice-only')
            try:
                with self.assertRaises(OSError):
                    runtime.start()
            finally:
                runtime.close()
        self.assertFalse(self.path.exists())

    def test_second_runtime_cannot_bind_first_runtime_port_even_with_different_database(self):
        other_path = self.root/'other-runtime.db'
        with team_runtime.CommonRuntime(self.path, port=0, mode='voice-only') as first:
            other = team_runtime.CommonRuntime(other_path, port=first.http.server_port, mode='voice-only')
            try:
                with self.assertRaises(OSError):
                    other.start()
                self.assertFalse(other_path.exists())
                self.assertEqual(self.get(first, '/api/team-runtime')['mode'], 'voice-only')
            finally:
                other.close()
        self.assertFalse(other_path.exists())

    def test_invalid_bindings_or_modes_are_rejected_before_database_creation(self):
        for changes in (dict(host='0.0.0.0'), dict(port=True), dict(port=-1),
                        dict(port=65536), dict(mode='unrecognized')):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                team_runtime.CommonRuntime(self.path, **changes)
        self.assertFalse(self.path.exists())

    def test_full_runtime_has_actual_workers_and_no_external_routing(self):
        with patch('server.travel.kakao_estimate', side_effect=AssertionError('No external routing')) as kakao:
            with team_runtime.CommonRuntime(self.path, mode='full') as runtime:
                status = self.wait_running(runtime)
                self.assertTrue(runtime.planner.thread.is_alive())
                self.assertTrue(runtime.inquiry_worker.thread.is_alive())
                self.assertEqual(runtime.planner.seconds, 3)
                self.assertFalse(runtime.planner.estimator.use_kakao)
                self.assertTrue(status['workers']['planning']['enabled'])
                self.assertTrue(status['workers']['inquiry']['enabled'])
                manifest = self.get(runtime, '/api/team-runtime')
                self.assertIsInstance(manifest, dict)
                self.assertIn('links', manifest)
                self.assertEqual(len(self.get(runtime, '/api/hospitals')), 10)
                planner, inquiry = runtime.planner, runtime.inquiry_worker
            self.assertFalse(planner.thread.is_alive())
            self.assertFalse(inquiry.thread.is_alive())
            kakao.assert_not_called()

    def test_voice_only_explicitly_disables_both_workers(self):
        with team_runtime.CommonRuntime(self.path, mode='voice-only') as runtime:
            status = self.get(runtime, '/api/system/status')
            self.assertIsNone(runtime.planner)
            self.assertIsNone(runtime.inquiry_worker)
            for name in ('planning', 'inquiry'):
                self.assertEqual(status['workers'][name]['state'], 'DISABLED')
                self.assertFalse(status['workers'][name]['enabled'])

    def test_second_runtime_cannot_share_the_same_database(self):
        with team_runtime.CommonRuntime(self.path, mode='voice-only'):
            other = team_runtime.CommonRuntime(self.path, mode='voice-only')
            try:
                with self.assertRaises((ValueError, RuntimeError, OSError)):
                    other.start()
            finally:
                other.close()

    def test_failed_inquiry_start_stops_started_planner_and_releases_lease(self):
        runtime = team_runtime.CommonRuntime(self.path, mode='full')
        with patch('server.inquiry_worker.InquiryWorker.start', side_effect=RuntimeError('synthetic startup failure')):
            with self.assertRaisesRegex(RuntimeError, 'synthetic startup failure'):
                runtime.start()
        if runtime.planner is not None:
            self.assertFalse(runtime.planner.thread.is_alive())
        if runtime.inquiry_worker is not None:
            self.assertFalse(runtime.inquiry_worker.thread.is_alive())
        runtime.close()
        with team_runtime.DatabaseLease(self.path):
            pass
        with team_runtime.CommonRuntime(self.path, mode='voice-only'):
            pass

    def test_profile_rejection_precedes_port_and_database_creation(self):
        runtime = team_runtime.CommonRuntime(self.path)
        with patch.object(team_runtime, 'verify_profile', return_value={
                'ok': False, 'issues': [{'code': 'SOURCE_CHANGED', 'path': 'fixture'}]}):
            with self.assertRaisesRegex(ValueError, 'SOURCE_CHANGED'):
                runtime.start()
        self.assertFalse(self.path.exists())
        self.assertIsNone(runtime.http)

    def test_voice_only_restart_preserves_saved_case_and_resource_changes(self):
        team_runtime.prepare_database(self.path)
        team_runtime.create_example(self.path, 'RUNTIME-SAVED-EXAMPLE')
        before = self.business()
        for _ in range(2):
            with team_runtime.CommonRuntime(self.path, mode='voice-only') as runtime:
                info = self.get(runtime, '/api/team-runtime')
                self.assertEqual(info['database']['counts']['patients'], 1)
                self.assertEqual(info['mode'], 'voice-only')
            self.assertEqual(before, self.business())

    def test_explicit_resource_refresh_preserves_quantities_status_and_patient_rows(self):
        team_runtime.prepare_database(self.path, now=NOW)
        team_runtime.create_example(self.path, 'RUNTIME-REFRESH-EXAMPLE')
        conn = db.connect(str(self.path))
        try:
            service.set_resource(conn, NOW+1, 'H01', 'ed_bed', 7, 2, status='limited')
            old = [dict(row) for row in conn.execute('SELECT * FROM resources ORDER BY hospital_id,resource_type')]
        finally:
            conn.close()
        before = self.business()
        refreshed = team_runtime.refresh_fixture_resources(self.path, now=NOW+7200)
        self.assertEqual(refreshed['refreshed'], 20)
        self.assertEqual(refreshed['resources']['stale_count'], 0)
        after = self.business()
        for table in ('patients', 'inquiries', 'voice_sessions', 'patient_amendments'):
            self.assertEqual(before[table], after[table])
        with closing(sqlite3.connect(self.path)) as conn:
            conn.row_factory = sqlite3.Row
            new = [dict(row) for row in conn.execute('SELECT * FROM resources ORDER BY hospital_id,resource_type')]
        for first, second in zip(old, new):
            for key in ('hospital_id', 'resource_type', 'capacity', 'occupied', 'status', 'source', 'source_observed_at'):
                self.assertEqual(first[key], second[key])
            self.assertEqual(second['updated_at'], NOW+7200)
            self.assertEqual(second['version'], first['version']+1)

    def test_running_database_cannot_be_prepared_or_refreshed_by_another_owner(self):
        with team_runtime.CommonRuntime(self.path, mode='voice-only'):
            before = self.business()
            for operation in (team_runtime.prepare_database, team_runtime.refresh_fixture_resources):
                with self.subTest(operation=operation.__name__), self.assertRaises(ValueError):
                    operation(self.path)
            self.assertEqual(before, self.business())

    def test_example_creation_is_explicit_and_never_overwrites_a_saved_patient(self):
        created = team_runtime.create_example(self.path, 'RUNTIME-EXAMPLE')
        self.assertTrue(created['synthetic'])
        self.assertEqual(created['patient_id'], 'RUNTIME-EXAMPLE')
        conn = db.connect(str(self.path))
        try:
            patient = service.patient_view(conn, 'RUNTIME-EXAMPLE', int(time.time()))
            self.assertEqual(patient['state'], 'WAITING')
            self.assertIsNone(patient['destination_hospital_id'])
            self.assertEqual(patient['inquiries'], [])
            self.assertEqual(patient['inquiry_workflow']['policy_name'], 'fixed_3')
        finally:
            conn.close()
        before = self.business()
        with self.assertRaises(ValueError):
            team_runtime.create_example(self.path, 'RUNTIME-EXAMPLE')
        self.assertEqual(before, self.business())

    def test_blocked_worker_keeps_database_lease_until_a_later_successful_close(self):
        class BlockedWorker:
            def __init__(self):
                self.alive = True
                self.ident = 1
                self.thread = self
                self.stop_calls = 0

            def is_alive(self):
                return self.alive

            def stop(self):
                # Model a bounded Worker.stop() whose join has timed out.
                self.stop_calls += 1

        runtime = team_runtime.CommonRuntime(self.path, mode='voice-only').start()
        blocked = BlockedWorker()
        runtime._started_workers.append(blocked)
        try:
            with self.assertRaisesRegex(RuntimeError, 'DB 실행 잠금'):
                runtime.close()
            self.assertEqual(blocked.stop_calls, 1)
            self.assertIsNotNone(runtime.lease)
            self.assertIn(blocked, runtime._started_workers)
            with self.assertRaises(ValueError):
                with team_runtime.DatabaseLease(self.path):
                    self.fail('Lease released while the old worker was still alive')
            blocked.alive = False
            runtime.close()
            self.assertEqual(blocked.stop_calls, 2)
            self.assertIsNone(runtime.lease)
            self.assertEqual(runtime._started_workers, [])
            with team_runtime.DatabaseLease(self.path):
                pass
        finally:
            blocked.alive = False
            runtime.close()


if __name__ == '__main__':
    unittest.main()
