"""Operational backup safety tests; isolated synthetic fixtures, never data/*.db."""
from contextlib import closing, redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest

from server import db, service, team_backup, team_runtime


NOW = 2_000_000_000
PRIVATE_TEXT = 'SYNTHETIC-PRIVATE-VOICE-MUST-NOT-BE-PRINTED'
VOLATILE = {'planning_state', 'planning_results', 'sqlite_sequence'}


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def quote(identifier):
    return '"' + identifier.replace('"', '""') + '"'


def business_rows(path):
    """Independent row equality oracle, including every non-cache business table."""
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)) as conn:
        names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                 if not r[0].startswith('sqlite_') and r[0] not in VOLATILE]
        return {name: sorted([tuple(row) for row in conn.execute('SELECT * FROM ' + quote(name))],
                             key=repr) for name in sorted(names)}


class TeamBackupTests(unittest.TestCase):
    def setUp(self):
        parent = Path(__file__).resolve().parents[2] / '.codex-build/team-backup-20261030/tmp'
        parent.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='backup-safety-', dir=parent)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'common.db'
        self.output = self.root / 'new-backup'

    def tearDown(self):
        self.tmp.cleanup()

    def seed(self):
        team_runtime.prepare_database(self.source, now=NOW)
        with closing(db.connect(str(self.source))) as conn:
            service.create_patient(conn, NOW + 1, 'BACKUP-P', dict(
                reporter_role='transporter', consent=True, priority=2, priority_source='expert',
                lat=35.16, lon=126.85, symptoms=PRIVATE_TEXT, auto_inquiry=False,
                extra={'synthetic': True}))
            service.send_inquiries(conn, NOW + 2, 'BACKUP-P', ['H01'])
            iid = conn.execute('SELECT id FROM inquiries WHERE patient_id=?', ('BACKUP-P',)).fetchone()[0]
            service.respond(conn, NOW + 3, iid, 'accept', 'SYNTHETIC-STAFF')
            conn.execute("UPDATE planning_state SET completed_revision=revision,lease_until=?,"
                         "lease_owner='old-process',last_error='PLANNING_FAILED' WHERE id=1", (NOW + 300,))
            revision = conn.execute('SELECT revision FROM planning_state WHERE id=1').fetchone()[0]
            conn.execute('INSERT INTO planning_results VALUES(?,?,?,?,?)',
                         ('BACKUP-P', revision, NOW + 3, NOW + 30, '{"cached":true}'))
        return self.source

    def backup(self):
        self.seed()
        return team_backup.backup_database(self.source, self.output)

    def manifest(self):
        return json.loads((self.output / 'manifest.json').read_text(encoding='utf-8'))

    def write_manifest(self, value):
        (self.output / 'manifest.json').write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')

    def assert_rejected(self, function, *args, **kwargs):
        with self.assertRaises(team_backup.BackupError) as error:
            function(*args, **kwargs)
        self.assertNotIn(PRIVATE_TEXT, str(error.exception))
        return str(error.exception)

    def test_backup_manifest_is_verified_and_contains_schema_counts_hashes_only(self):
        self.seed()
        before = business_rows(self.source)
        original = file_hash(self.source)
        result = team_backup.backup_database(self.source, self.output)
        self.assertTrue(result['verified'])
        self.assertEqual(Path(result['snapshot_path']), self.output / 'snapshot.db')
        self.assertEqual(Path(result['manifest_path']), self.output / 'manifest.json')
        manifest = self.manifest()
        self.assertEqual(manifest['format'], 'team-common-sqlite-backup')
        self.assertEqual(manifest['version'], 1)
        self.assertIsInstance(manifest['created_at'], int)
        self.assertEqual(manifest['database_file'], 'snapshot.db')
        self.assertIsInstance(manifest['sqlite_version'], str)
        self.assertEqual(manifest['file_sha256'], file_hash(self.output / 'snapshot.db'))
        self.assertEqual(manifest['marker'], dict(schema_version=team_runtime.SCHEMA_VERSION,
            catalog_id=team_runtime.CATALOG_ID, baseline_id=team_runtime.BASELINE_ID))
        state = manifest['logical_state']
        for name in ('schema_sha256', 'business_sha256'):
            self.assertEqual(len(state[name]), 64)
        for name, rows in before.items():
            self.assertEqual(state['tables'][name]['count'], len(rows))
            self.assertGreater(len(state['tables'][name]['columns']), 0)
            self.assertEqual(len(state['tables'][name]['sha256']), 64)
        self.assertNotIn(PRIVATE_TEXT, json.dumps(manifest))
        self.assertEqual(before, business_rows(self.output / 'snapshot.db'))
        self.assertEqual(before, business_rows(self.source))
        self.assertEqual(original, file_hash(self.source))
        self.assertTrue(team_backup.verify_backup(self.output)['verified'])

    def test_missing_source_is_not_created(self):
        self.assert_rejected(team_backup.backup_database, self.source, self.output)
        self.assertFalse(self.source.exists())
        self.assertFalse(self.output.exists())

    def test_unknown_empty_and_non_database_sources_are_preserved(self):
        for name, content in (('empty.db', b''), ('text.db', b'preserve unrelated file')):
            with self.subTest(name=name):
                source = self.root / name
                source.write_bytes(content)
                self.assert_rejected(team_backup.backup_database, source, self.root / (name + '-backup'))
                self.assertEqual(source.read_bytes(), content)
        unrelated = self.root / 'unrelated.db'
        with closing(sqlite3.connect(unrelated)) as conn, conn:
            conn.execute('CREATE TABLE preserve(id INTEGER PRIMARY KEY,value TEXT)')
            conn.execute("INSERT INTO preserve VALUES(1,'unrelated')")
        original = unrelated.read_bytes()
        self.assert_rejected(team_backup.backup_database, unrelated, self.output)
        self.assertEqual(original, unrelated.read_bytes())

    def test_wrong_marker_or_public_hospital_catalog_is_rejected_without_repair(self):
        self.seed()
        for sql in ("UPDATE team_runtime_meta SET schema_version=999 WHERE id=1",
                    "UPDATE hospitals SET source='public' WHERE id='H01'"):
            with self.subTest(sql=sql):
                with closing(sqlite3.connect(self.source)) as conn, conn:
                    conn.execute(sql)
                original = file_hash(self.source)
                self.assert_rejected(team_backup.backup_database, self.source, self.output)
                self.assertEqual(original, file_hash(self.source))
                with closing(sqlite3.connect(self.source)) as conn, conn:
                    conn.execute('UPDATE team_runtime_meta SET schema_version=? WHERE id=1',
                                 (team_runtime.SCHEMA_VERSION,))

    def test_backup_refuses_existing_directory_and_file_without_overwriting(self):
        self.seed()
        self.output.mkdir()
        sentinel = self.output / 'sentinel.txt'
        sentinel.write_bytes(b'preserve existing directory')
        self.assert_rejected(team_backup.backup_database, self.source, self.output)
        self.assertEqual(sentinel.read_bytes(), b'preserve existing directory')
        target = self.root / 'existing-output'
        target.write_bytes(b'preserve existing file')
        self.assert_rejected(team_backup.backup_database, self.source, target)
        self.assertEqual(target.read_bytes(), b'preserve existing file')

    def test_source_cannot_be_used_as_backup_destination(self):
        self.seed()
        original = file_hash(self.source)
        self.assert_rejected(team_backup.backup_database, self.source, self.source)
        self.assertEqual(original, file_hash(self.source))

    def test_backup_refuses_hardlinked_source(self):
        self.seed()
        alias = self.root / 'source-alias.db'
        try:
            os.link(self.source, alias)
        except OSError as error:
            self.skipTest('Hard links are unavailable: ' + str(error))
        original = file_hash(self.source)
        self.assert_rejected(team_backup.backup_database, alias, self.output)
        self.assertEqual(original, file_hash(self.source))

    def test_backup_refuses_symlinked_source_without_following_it(self):
        self.seed()
        alias = self.root / 'source-link.db'
        try:
            alias.symlink_to(self.source)
        except OSError as error:
            self.skipTest('Symlinks are unavailable: ' + str(error))
        self.assert_rejected(team_backup.backup_database, alias, self.output)

    def test_backup_refuses_symlinked_destination_parent(self):
        self.seed()
        real_parent = self.root / 'actual-parent'
        real_parent.mkdir()
        alias = self.root / 'parent-link'
        try:
            alias.symlink_to(real_parent, target_is_directory=True)
        except OSError as error:
            self.skipTest('Symlinks are unavailable: ' + str(error))
        self.assert_rejected(team_backup.backup_database, self.source, alias / 'new-backup')
        self.assertEqual(list(real_parent.iterdir()), [])

    def test_verify_missing_or_incomplete_backup_does_not_create_files(self):
        self.assert_rejected(team_backup.verify_backup, self.output)
        self.assertFalse(self.output.exists())
        self.output.mkdir()
        self.assert_rejected(team_backup.verify_backup, self.output)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_verify_detects_raw_snapshot_tampering(self):
        self.backup()
        snapshot = self.output / 'snapshot.db'
        original = snapshot.read_bytes()
        snapshot.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
        self.assert_rejected(team_backup.verify_backup, self.output)

    def test_verify_detects_business_tampering_even_when_file_hash_is_updated(self):
        self.backup()
        snapshot = self.output / 'snapshot.db'
        with closing(sqlite3.connect(snapshot)) as conn, conn:
            conn.execute("UPDATE hospitals SET name='tampered synthetic hospital' WHERE id='H01'")
        manifest = self.manifest()
        manifest['file_sha256'] = file_hash(snapshot)
        self.write_manifest(manifest)
        self.assert_rejected(team_backup.verify_backup, self.output)

    def test_verify_detects_schema_tampering_even_when_file_hash_is_updated(self):
        self.backup()
        snapshot = self.output / 'snapshot.db'
        with closing(sqlite3.connect(snapshot)) as conn, conn:
            conn.execute('CREATE INDEX unexpected_backup_index ON patients(requested_at)')
        manifest = self.manifest()
        manifest['file_sha256'] = file_hash(snapshot)
        self.write_manifest(manifest)
        self.assert_rejected(team_backup.verify_backup, self.output)

    def test_manifest_schema_and_database_filename_are_strictly_validated(self):
        self.backup()
        original = self.manifest()
        invalid = [dict(original, version=999), dict(original, format='unknown'),
                   dict(original, database_file='../common.db'), dict(original, created_at=True),
                   dict(original, unsupported_field='must not be silently trusted')]
        missing = dict(original)
        missing.pop('logical_state')
        invalid.append(missing)
        for manifest in invalid:
            with self.subTest(manifest_keys=list(manifest)):
                self.write_manifest(manifest)
                self.assert_rejected(team_backup.verify_backup, self.output)
        self.write_manifest(original)
        self.assertTrue(team_backup.verify_backup(self.output)['verified'])

    def test_verify_rejects_backup_snapshot_with_sidecars(self):
        self.backup()
        for suffix in ('-wal', '-shm', '-journal'):
            with self.subTest(suffix=suffix):
                sidecar = self.output / ('snapshot.db' + suffix)
                sidecar.write_bytes(b'preserve sidecar')
                self.assert_rejected(team_backup.verify_backup, self.output)
                self.assertEqual(sidecar.read_bytes(), b'preserve sidecar')
                sidecar.unlink()

    def test_online_backup_includes_committed_wal_and_excludes_uncommitted_writer_rows(self):
        self.seed()
        with closing(db.connect(str(self.source))) as writer:
            writer.execute('PRAGMA wal_autocheckpoint=0')
            writer.execute("UPDATE hospitals SET name='committed in WAL' WHERE id='H01'")
            self.assertGreater(Path(str(self.source) + '-wal').stat().st_size, 0)
            writer.execute('BEGIN IMMEDIATE')
            writer.execute("UPDATE hospitals SET name='uncommitted change' WHERE id='H02'")
            before = business_rows(self.source)
            result = team_backup.backup_database(self.source, self.output, timeout_seconds=2)
            self.assertTrue(result['verified'])
            self.assertEqual(before, business_rows(self.output / 'snapshot.db'))
            self.assertEqual(before, business_rows(self.source))
            with closing(sqlite3.connect(self.output / 'snapshot.db')) as copy:
                self.assertEqual(copy.execute("SELECT name FROM hospitals WHERE id='H01'").fetchone()[0],
                                 'committed in WAL')
                self.assertNotEqual(copy.execute("SELECT name FROM hospitals WHERE id='H02'").fetchone()[0],
                                    'uncommitted change')
            writer.execute('ROLLBACK')

    def test_online_backup_is_allowed_while_common_runtime_owns_source_lease(self):
        self.seed()
        before = business_rows(self.source)
        with team_runtime.DatabaseLease(self.source):
            result = team_backup.backup_database(self.source, self.output)
        self.assertTrue(result['verified'])
        self.assertEqual(before, business_rows(self.output / 'snapshot.db'))

    def test_source_foreign_key_violation_is_rejected_without_repair(self):
        self.seed()
        with closing(sqlite3.connect(self.source)) as conn, conn:
            conn.execute("UPDATE inquiries SET patient_id='missing-synthetic-patient'")
        original = file_hash(self.source)
        self.assert_rejected(team_backup.backup_database, self.source, self.output)
        self.assertEqual(original, file_hash(self.source))

    def test_exclusive_sqlite_lock_obeys_requested_timeout(self):
        self.seed()
        with closing(sqlite3.connect(self.source, isolation_level=None)) as writer:
            writer.execute('PRAGMA journal_mode=DELETE')
            writer.execute('BEGIN EXCLUSIVE')
            started = time.monotonic()
            self.assert_rejected(team_backup.backup_database, self.source, self.output, timeout_seconds=.2)
            elapsed = time.monotonic() - started
            self.assertLess(elapsed, 2, 'A .2-second operation must not wait for SQLite default busy timeouts.')
            writer.execute('ROLLBACK')
        self.assertTrue(team_runtime.inspect_database(self.source)['valid'])

    def test_restore_preserves_all_business_rows_and_resets_only_planning_caches(self):
        self.backup()
        original_hash = file_hash(self.source)
        snapshot = self.output / 'snapshot.db'
        backup_hash = file_hash(snapshot)
        manifest_hash = file_hash(self.output / 'manifest.json')
        before = business_rows(self.source)
        with closing(sqlite3.connect(snapshot)) as conn:
            original_state = conn.execute('SELECT revision,completed_revision,lease_until FROM planning_state').fetchone()
        target = self.root / 'restored.db'
        result = team_backup.restore_database(self.output, target)
        self.assertTrue(result['verified'])
        self.assertTrue(result['business_state_preserved'])
        self.assertEqual(Path(result['database_path']), target)
        self.assertEqual(result['planning_reset']['cache_rows_removed'], 1)
        self.assertEqual(result['planning_reset']['previous_completed_revision'], original_state[1])
        self.assertEqual(result['planning_reset']['previous_lease_until'], original_state[2])
        self.assertEqual(result['planning_reset']['completed_revision'], -1)
        self.assertEqual(result['planning_reset']['lease_until'], 0)
        with closing(sqlite3.connect(target)) as conn:
            state = conn.execute('SELECT revision,completed_revision,lease_until,lease_owner,last_error '
                                 'FROM planning_state').fetchone()
            self.assertEqual(state, (original_state[0], -1, 0, None, None))
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM planning_results').fetchone()[0], 0)
            self.assertEqual(conn.execute('PRAGMA integrity_check').fetchall(), [('ok',)])
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])
        self.assertEqual(before, business_rows(target))
        self.assertEqual(before, business_rows(self.source))
        self.assertEqual(original_hash, file_hash(self.source))
        self.assertEqual(backup_hash, file_hash(snapshot))
        self.assertEqual(manifest_hash, file_hash(self.output / 'manifest.json'))
        self.assertTrue(team_runtime.inspect_database(target)['valid'])

    def test_restore_does_not_extend_expired_acceptance_or_refresh_old_resources(self):
        self.seed()
        with closing(sqlite3.connect(self.source)) as conn, conn:
            conn.execute('UPDATE inquiries SET expires_at=1')
            conn.execute('UPDATE resources SET updated_at=1,source_observed_at=1')
        team_backup.backup_database(self.source, self.output)
        target = self.root / 'expired-restored.db'
        team_backup.restore_database(self.output, target)
        with closing(sqlite3.connect(target)) as conn:
            self.assertEqual(conn.execute('SELECT state,expires_at FROM inquiries').fetchall(), [('ACCEPTED', 1)])
            self.assertEqual(conn.execute('SELECT DISTINCT updated_at,source_observed_at FROM resources').fetchall(),
                             [(1, 1)])
        self.assertEqual(business_rows(self.source), business_rows(target))

    def test_restore_never_overwrites_existing_database_or_backup_snapshot(self):
        self.backup()
        for target in (self.source, self.output / 'snapshot.db'):
            with self.subTest(target=target.name):
                original = file_hash(target)
                self.assert_rejected(team_backup.restore_database, self.output, target)
                self.assertEqual(original, file_hash(target))
        target = self.root / 'existing.db'
        target.write_bytes(b'preserve unrelated destination')
        self.assert_rejected(team_backup.restore_database, self.output, target)
        self.assertEqual(target.read_bytes(), b'preserve unrelated destination')

    def test_restore_refuses_existing_sqlite_sidecars_for_new_destination(self):
        self.backup()
        for suffix in ('-wal', '-shm', '-journal'):
            with self.subTest(suffix=suffix):
                target = self.root / ('restore' + suffix + '.db')
                sidecar = Path(str(target) + suffix)
                sidecar.write_bytes(b'preserve old sidecar')
                self.assert_rejected(team_backup.restore_database, self.output, target)
                self.assertFalse(target.exists())
                self.assertEqual(sidecar.read_bytes(), b'preserve old sidecar')

    def test_restore_respects_destination_runtime_lease_but_accepts_unlocked_stale_lockfile(self):
        self.backup()
        target = self.root / 'locked-destination.db'
        with team_runtime.DatabaseLease(target):
            self.assert_rejected(team_backup.restore_database, self.output, target)
            self.assertFalse(target.exists())
        self.assertTrue(Path(str(target) + '.team-runtime.lock').exists())
        result = team_backup.restore_database(self.output, target)
        self.assertTrue(result['business_state_preserved'])

    def test_tampered_backup_cannot_create_restore_target(self):
        self.backup()
        manifest = self.manifest()
        manifest['logical_state']['business_sha256'] = '0' * 64
        self.write_manifest(manifest)
        target = self.root / 'must-not-exist.db'
        self.assert_rejected(team_backup.restore_database, self.output, target)
        self.assertFalse(target.exists())

    def test_inspection_is_read_only_and_does_not_create_missing_database(self):
        self.assert_rejected(team_backup.inspect_state, self.source)
        self.assertFalse(self.source.exists())
        self.seed()
        original = file_hash(self.source)
        before = business_rows(self.source)
        state = team_backup.inspect_state(self.source)
        self.assertEqual(state['tables']['patients']['count'], 1)
        self.assertEqual(original, file_hash(self.source))
        self.assertEqual(before, business_rows(self.source))

    def test_invalid_timeouts_are_rejected_without_source_mutation(self):
        self.seed()
        original = file_hash(self.source)
        for timeout in (0, -1, float('nan'), float('inf'), True):
            with self.subTest(timeout=timeout):
                self.assert_rejected(team_backup.backup_database, self.source, self.output,
                                     timeout_seconds=timeout)
        self.assertEqual(original, file_hash(self.source))
        self.assertFalse(self.output.exists())

    def test_cli_supports_backup_verify_and_restore_without_printing_patient_data(self):
        self.seed()
        target = self.root / 'cli-restored.db'
        commands = [
            ['backup', '--db', str(self.source), '--output', str(self.output), '--timeout-seconds', '2'],
            ['verify', '--backup', str(self.output), '--timeout-seconds', '2'],
            ['restore', '--backup', str(self.output), '--db', str(target), '--timeout-seconds', '2'],
        ]
        for command in commands:
            with self.subTest(command=command[0]), redirect_stdout(io.StringIO()) as stdout, \
                    redirect_stderr(io.StringIO()) as stderr:
                self.assertEqual(team_backup.main(command), 0)
                self.assertNotIn(PRIVATE_TEXT, stdout.getvalue() + stderr.getvalue())
                self.assertTrue(json.loads(stdout.getvalue())['verified'])
        self.assertEqual(business_rows(self.source), business_rows(target))

    def test_deeply_nested_manifest_fails_with_safe_json_and_no_restore_creation(self):
        self.backup()
        (self.output / 'manifest.json').write_text('[' * 10000 + '0' + ']' * 10000, encoding='utf-8')
        target = self.root / 'deep-manifest-restore.db'
        with redirect_stdout(io.StringIO()) as stdout, redirect_stderr(io.StringIO()) as stderr:
            status = team_backup.main(['restore', '--backup', str(self.output), '--db', str(target)])
        self.assertEqual(status, 1)
        self.assertFalse(json.loads(stdout.getvalue())['success'])
        self.assertNotIn('Traceback', stderr.getvalue())
        self.assertFalse(target.exists())

    def test_cli_failure_is_nonzero_and_does_not_print_private_rows(self):
        self.seed()
        with closing(sqlite3.connect(self.source)) as conn, conn:
            conn.execute("UPDATE hospitals SET source='public' WHERE id='H01'")
        with redirect_stdout(io.StringIO()) as stdout, redirect_stderr(io.StringIO()) as stderr:
            status = team_backup.main(['backup', '--db', str(self.source), '--output', str(self.output)])
        self.assertNotEqual(status, 0)
        self.assertNotIn(PRIVATE_TEXT, stdout.getvalue() + stderr.getvalue())


if __name__ == '__main__':
    unittest.main()
