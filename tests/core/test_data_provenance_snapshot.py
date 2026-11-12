"""Read snapshot isolation with real WAL commits; no solver, API or clinical data."""
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from server import data_provenance as provenance


NOW = 2_000_000_000
SCOPE = dict(model_resources=['ed_bed', 'special_team'], ui_labels={
    'public_identity_frozen': 'public identity', 'synthetic_identity': 'synthetic identity',
    'public_observation': 'public observation', 'registered_reference': 'reference',
    'synthetic_fixture': 'synthetic operation', 'unknown': 'unknown'})

SCHEMA = """
CREATE TABLE hospitals(id TEXT PRIMARY KEY,name TEXT,source TEXT,source_ids TEXT);
CREATE TABLE resources(hospital_id TEXT,resource_type TEXT,capacity INTEGER,occupied INTEGER,
                       status TEXT,source TEXT,source_observed_at INTEGER,updated_at INTEGER);
CREATE TABLE public_api_snapshots(hospital_id TEXT,endpoint TEXT,source_observed_at INTEGER,
                                  fetched_at INTEGER,normalized TEXT);
CREATE TABLE inquiries(hospital_id TEXT,state TEXT);
INSERT INTO hospitals VALUES('SNAP-H1','before commit','synthetic','{}');
INSERT INTO resources VALUES('SNAP-H1','ed_bed',4,0,'available','synthetic',1,1);
INSERT INTO public_api_snapshots VALUES('SNAP-H1','nmc_rltm_beds',1,1,
    '{"resources":[{"resource_type":"ed_bed","reported_available":4}]}');
INSERT INTO inquiries VALUES('SNAP-H1','PENDING');
"""


class ProvenanceSnapshotTests(unittest.TestCase):
    def setUp(self):
        parent = Path(__file__).resolve().parents[2] / '.codex-build/provenance-snapshot-20261030/tmp'
        parent.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='wal-snapshot-', dir=parent)
        self.path = Path(self.tmp.name) / 'synthetic.db'
        self.writer = sqlite3.connect(self.path, isolation_level=None, timeout=.2)
        self.writer.execute('PRAGMA journal_mode=WAL')
        self.writer.executescript(SCHEMA)
        self.reader = sqlite3.connect(self.path, isolation_level=None, timeout=.2)
        self.scope = patch.object(provenance, 'resource_scope', return_value=SCOPE)
        self.scope.start()

    def tearDown(self):
        self.scope.stop()
        self.reader.close()
        self.writer.close()
        self.tmp.cleanup()

    def inventory(self):
        return provenance.inventory(self.reader, NOW)

    def commit_new_state(self):
        self.writer.execute('BEGIN IMMEDIATE')
        try:
            self.writer.execute("UPDATE hospitals SET name='after commit'")
            self.writer.execute('UPDATE resources SET capacity=9,updated_at=2,source_observed_at=2')
            self.writer.execute('UPDATE public_api_snapshots SET fetched_at=2,source_observed_at=2,'
                                "normalized='{\"resources\":[{\"resource_type\":\"ed_bed\",\"reported_available\":9}]}'")
            self.writer.execute("UPDATE inquiries SET state='ACCEPTED'")
            self.writer.execute('COMMIT')
        except BaseException:
            self.writer.execute('ROLLBACK')
            raise

    def assert_version(self, result, after):
        hospital = result['hospitals'][0]
        self.assertEqual(hospital['name'], 'after commit' if after else 'before commit')
        self.assertEqual(hospital['operating_resources'][0]['capacity'], 9 if after else 4)
        self.assertEqual(hospital['operating_resources'][0]['updated_at'], 2 if after else 1)
        self.assertEqual(hospital['public_evidence'][0]['evidence'][0]['reported_available'], 9 if after else 4)
        self.assertEqual(hospital['public_evidence'][0]['fetched_at'], 2 if after else 1)
        self.assertEqual(hospital['acceptance_scope']['stored_inquiry_state_counts'],
                         {'ACCEPTED': 1} if after else {'PENDING': 1})

    def test_wal_writer_commit_between_selects_cannot_mix_one_inventory_response(self):
        original = provenance._rows
        commits = []
        trace = []
        self.reader.set_trace_callback(trace.append)

        def interleave(conn, sql, args=()):
            rows = original(conn, sql, args)
            if sql.startswith('SELECT * FROM hospitals') and not commits:
                self.assertTrue(self.reader.in_transaction)
                self.commit_new_state()  # Must succeed while the reader's snapshot stays open.
                commits.append(True)
            return rows

        with patch.object(provenance, '_rows', side_effect=interleave):
            first = self.inventory()
        self.assertEqual(commits, [True])
        self.assert_version(first, after=False)
        self.assertFalse(self.reader.in_transaction)
        self.assert_version(self.inventory(), after=True)
        self.assertTrue(any(sql.startswith('SAVEPOINT provenance_view_') for sql in trace))
        self.assertFalse(any('BEGIN IMMEDIATE' in sql.upper() for sql in trace))
        self.assertFalse(any(sql.upper().startswith(('INSERT', 'UPDATE', 'DELETE')) for sql in trace))

    def test_error_releases_owned_snapshot_and_next_call_reads_latest_commit(self):
        original = provenance._rows

        def interleave_and_fail(conn, sql, args=()):
            rows = original(conn, sql, args)
            if sql.startswith('SELECT * FROM hospitals'):
                self.commit_new_state()
                raise RuntimeError('synthetic read failure')
            return rows

        with patch.object(provenance, '_rows', side_effect=interleave_and_fail):
            with self.assertRaisesRegex(RuntimeError, 'synthetic read failure'):
                self.inventory()
        self.assertFalse(self.reader.in_transaction)
        self.assert_version(self.inventory(), after=True)

    def test_nested_success_preserves_outer_transaction_and_pending_writes(self):
        self.reader.execute('BEGIN')
        self.reader.execute("UPDATE hospitals SET name='caller pending'")
        result = self.inventory()
        self.assertTrue(self.reader.in_transaction)
        self.assertEqual(result['hospitals'][0]['name'], 'caller pending')
        self.assertEqual(self.writer.execute('SELECT name FROM hospitals').fetchone()[0], 'before commit')
        self.reader.execute('ROLLBACK')
        self.assertFalse(self.reader.in_transaction)
        self.assert_version(self.inventory(), after=False)

    def test_nested_error_does_not_rollback_or_commit_callers_pending_writes(self):
        self.reader.execute('BEGIN')
        self.reader.execute("UPDATE hospitals SET name='caller pending'")
        with patch.object(provenance, '_rows', side_effect=ValueError('synthetic nested failure')):
            with self.assertRaisesRegex(ValueError, 'synthetic nested failure'):
                self.inventory()
        self.assertTrue(self.reader.in_transaction)
        self.assertEqual(self.reader.execute('SELECT name FROM hospitals').fetchone()[0], 'caller pending')
        self.assertEqual(self.writer.execute('SELECT name FROM hospitals').fetchone()[0], 'before commit')
        self.reader.execute('ROLLBACK')
        self.assert_version(self.inventory(), after=False)

    def test_outer_savepoint_is_still_owned_by_caller_after_inventory(self):
        self.reader.execute('SAVEPOINT caller_transaction')
        self.reader.execute('UPDATE resources SET capacity=7')
        self.assertEqual(self.inventory()['hospitals'][0]['operating_resources'][0]['capacity'], 7)
        self.assertTrue(self.reader.in_transaction)
        self.reader.execute('ROLLBACK TO caller_transaction')
        self.reader.execute('RELEASE caller_transaction')
        self.assertFalse(self.reader.in_transaction)
        self.assert_version(self.inventory(), after=False)

    def test_base_exception_also_releases_owned_snapshot(self):
        with patch.object(provenance, 'resource_scope', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.inventory()
        self.assertFalse(self.reader.in_transaction)
        self.assert_version(self.inventory(), after=False)

    def test_query_only_connection_performs_no_schema_or_business_writes(self):
        self.reader.execute('PRAGMA query_only=ON')
        changes = self.reader.total_changes
        before = self.reader.execute('SELECT type,name,sql FROM sqlite_master ORDER BY name').fetchall()
        self.assert_version(self.inventory(), after=False)
        self.assertEqual(self.reader.total_changes, changes)
        self.assertEqual(before, self.reader.execute('SELECT type,name,sql FROM sqlite_master ORDER BY name').fetchall())
        self.assertFalse(self.reader.in_transaction)

    def test_read_inventory_cli_entry_keeps_existing_begin_compatible(self):
        self.assert_version(provenance.read_inventory(self.path, NOW), after=False)
        # read_inventory owns its separate connection; no caller lock remains.
        self.commit_new_state()
        self.assert_version(provenance.read_inventory(self.path, NOW), after=True)


if __name__ == '__main__':
    unittest.main()
