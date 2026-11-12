"""Isolated synthetic database recovery guards; no running server or real DB."""
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from server import recovery_rehearsal as recovery
from server import team_runtime


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        parent = recovery.ROOT/'.codex-build/recovery-rehearsal-tests'
        parent.mkdir(parents=True,exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=parent)
        self.root = Path(self.temp.name)
        self.source = self.root/'fixture.db'
        team_runtime.prepare_database(self.source)

    def tearDown(self):
        self.temp.cleanup()

    def test_backup_restore_logical_rows_and_existing_destination_guard(self):
        before = recovery.logical_state(self.source)
        backup = self.root/'backup.db'
        result = recovery.backup_database(self.source,backup,artifact_root=self.root)
        self.assertEqual(result['business_state'],before)
        restored = self.root/'restored.db'
        self.assertEqual(recovery.backup_database(backup,restored,artifact_root=self.root)['business_state'],before)
        bytes_before = restored.read_bytes()
        with self.assertRaises(FileExistsError):
            recovery.backup_database(backup,restored,artifact_root=self.root)
        self.assertEqual(restored.read_bytes(),bytes_before)

    def test_backup_includes_committed_business_rows_in_live_wal(self):
        with closing(sqlite3.connect(self.source)) as writer:
            writer.execute('PRAGMA journal_mode=WAL')
            writer.execute('PRAGMA wal_autocheckpoint=0')
            writer.execute("UPDATE resources SET occupied=2 WHERE hospital_id='H01' AND resource_type='ed_bed'")
            writer.commit()
            self.assertTrue(Path(str(self.source)+'-wal').exists())
            result = recovery.backup_database(self.source,self.root/'live-wal-backup.db',artifact_root=self.root)
            self.assertEqual(result['business_state'],recovery.logical_state(self.source))
            with closing(recovery._readonly(self.root/'live-wal-backup.db')) as conn:
                self.assertEqual(conn.execute("SELECT occupied FROM resources WHERE hospital_id='H01' AND resource_type='ed_bed'").fetchone()[0],2)

    def test_reject_outside_paths_without_creating_target(self):
        for source,target in ((self.root.parent/'other.db',self.root/'target.db'),
                              (self.source,self.root.parent/'outside-target.db')):
            with self.assertRaises(ValueError):
                recovery.backup_database(source,target,artifact_root=self.root)
        self.assertFalse((self.root/'target.db').exists())

    def test_unknown_and_corrupted_source_never_create_backup(self):
        unknown = self.root/'unknown.db'
        with closing(sqlite3.connect(unknown)) as conn,conn:
            conn.execute('CREATE TABLE original(value TEXT)')
            conn.execute("INSERT INTO original VALUES('keep')")
        corrupted = self.root/'corrupted.db'
        corrupted.write_bytes(b'not a database')
        for source in (unknown,corrupted):
            target = self.root/(source.stem+'-backup.db')
            original = source.read_bytes()
            with self.subTest(source=source.name),self.assertRaises(ValueError):
                recovery.backup_database(source,target,artifact_root=self.root)
            self.assertEqual(source.read_bytes(),original)
            self.assertFalse(target.exists())

    def test_preserving_two_column_migration_and_repeat(self):
        result = recovery.migrate_fixture(self.source)
        self.assertTrue(result['pass_'])
        self.assertTrue(result['repeated_migration_preserved'])
        self.assertEqual(result['before_projection'],result['after_projection'])
        self.assertEqual(result['after']['tables']['patients']['count'],1)
        self.assertEqual(result['after']['tables']['inquiries']['count'],1)
        self.assertEqual(result['after']['tables']['voice_sessions']['count'],1)

    def test_active_runtime_lease_blocks_fixture_migration(self):
        before = recovery.logical_state(self.source)
        with team_runtime.DatabaseLease(self.source):
            with self.assertRaises(ValueError):
                recovery.migrate_fixture(self.source)
        self.assertEqual(recovery.logical_state(self.source),before)

    def test_projection_detects_real_business_mutation(self):
        before = recovery.logical_state(self.source)
        with closing(sqlite3.connect(self.source)) as conn,conn:
            conn.execute("UPDATE resources SET occupied=1 WHERE hospital_id='H01' AND resource_type='ed_bed'")
        after = recovery.logical_state(self.source)
        self.assertNotEqual(before['sha256'],after['sha256'])
        self.assertNotEqual(before['tables']['resources'],after['tables']['resources'])

    def test_source_change_evidence_preserves_both_digests_and_missing_files(self):
        source = self.root/'source.py'
        source.write_bytes(b'original')
        manifest = {'source.py':{'sha256':recovery._hash(b'original')}}
        self.assertEqual(recovery.manifest_changes(self.root,manifest),[])
        source.write_bytes(b'changed')
        changed = recovery.manifest_changes(self.root,manifest)[0]
        self.assertEqual(changed['before_sha256'],recovery._hash(b'original'))
        self.assertEqual(changed['after_sha256'],recovery._hash(b'changed'))
        self.assertIsNone(recovery.manifest_changes(self.root,{'absent.py':{'sha256':'original'}})[0]['after_sha256'])


if __name__=='__main__':
    unittest.main()
