"""Guard the reproducible local rehearsal: isolation, failure reports, real path."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from server import voice_allocation_rehearsal as rehearsal


class VoiceAllocationRehearsalTests(unittest.TestCase):
    def setUp(self):
        parent = rehearsal.ROOT/'.codex-build/voice-allocation-e2e-20261030/tmp'
        parent.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='runner-test-', dir=parent)
        self.root = Path(self.tmp.name)
        self.output = self.root/'new-output'

    def tearDown(self):
        self.tmp.cleanup()

    def test_real_http_and_three_second_solver_reach_arrival_and_reject_old_approval(self):
        report = rehearsal.run(self.output, cases=('normal_arrival', 'stale_treatment_approval'))
        self.assertTrue(report['pass'], report['cases'])
        self.assertEqual((report['passed'], report['failed']), (2, 0))
        self.assertEqual(report['cases'][0]['final_state'], 'ARRIVED')
        self.assertEqual(report['cases'][1]['final_state'], 'WAITING')
        self.assertEqual(report['solver_budget_seconds'], 3)
        for case in report['cases']:
            self.assertTrue(case['requests'])
            self.assertTrue(case['solver_runs'])
            for meta in case['solver_runs']:
                self.assertIn('fallback', meta)
                self.assertIn('lex_optimal', meta)
                self.assertIn('status', meta)
        self.assertFalse(report['existing_business_db_accessed'])
        self.assertEqual(report['actual_hospital_requests'], 0)
        self.assertTrue(report['source_snapshot_matches'])
        self.assertEqual(report['source_hashes_before'], report['snapshot_hashes'])
        self.assertTrue((self.output/'sources/server/voice_input.py').is_file())
        self.assertTrue((self.output/'sources/config/server_allocation_baseline_20261030.json').is_file())
        self.assertEqual(json.loads((self.output/'protocol.json').read_text(encoding='utf-8'))['inquiry_policy'], 'fixed_3')

    def test_existing_directory_is_never_reused_or_reset(self):
        self.output.mkdir()
        sentinel = self.output/'existing-business.db'
        sentinel.write_bytes(b'preserve existing contents')
        with self.assertRaises(FileExistsError):
            rehearsal.run(self.output, cases=('normal_arrival',))
        self.assertEqual(list(self.output.iterdir()), [sentinel])
        self.assertEqual(sentinel.read_bytes(), b'preserve existing contents')

    def test_changed_baseline_stops_before_output_or_database_creation(self):
        bad = {'ok': False, 'issues': [{'code': 'SOURCE_CHANGED', 'path': 'solver_improved.py'}]}
        with patch.object(rehearsal.allocation_baseline, 'verify', return_value=bad):
            with self.assertRaisesRegex(ValueError, 'SOURCE_CHANGED'):
                rehearsal.run(self.output, cases=('normal_arrival',))
        self.assertFalse(self.output.exists())

    def test_unknown_empty_or_duplicate_case_selection_is_rejected_before_writes(self):
        for cases in ((), ('unknown',), ('normal_arrival', 'normal_arrival')):
            with self.subTest(cases=cases), self.assertRaises(ValueError):
                rehearsal.run(self.output, cases=cases)
            self.assertFalse(self.output.exists())

    def test_failed_scenario_is_preserved_as_failure_in_json_and_markdown(self):
        def fail(flow):
            flow.require('expected_guard_fixture', False)
        with patch.object(rehearsal, 'normal_arrival', fail):
            report = rehearsal.run(self.output, cases=('normal_arrival',))
        self.assertFalse(report['pass'])
        self.assertEqual((report['passed'], report['failed']), (0, 1))
        self.assertIn('expected_guard_fixture', report['cases'][0]['error'])
        stored = json.loads((self.output/'report.json').read_text(encoding='utf-8'))
        self.assertFalse(stored['pass'])
        self.assertIn('FAIL', (self.output/'report.md').read_text(encoding='utf-8'))

    def test_source_drift_fails_even_when_all_scenario_assertions_pass(self):
        original = rehearsal.fingerprints()
        changed = {**original, 'server/voice_input.py': 'changed-by-another-editor'}
        with patch.object(rehearsal, 'normal_arrival', lambda flow: None), \
                patch.object(rehearsal, 'fingerprints', side_effect=[original, changed]):
            report = rehearsal.run(self.output, cases=('normal_arrival',))
        self.assertEqual(report['passed'], 1)
        self.assertFalse(report['sources_stable'])
        self.assertFalse(report['pass'])

    def test_http_startup_failure_closes_already_opened_fixture_database(self):
        connection = None
        original = rehearsal.db.connect
        def capture(path):
            nonlocal connection
            connection = original(path)
            return connection
        flow = rehearsal.Flow(self.output)
        with patch.object(rehearsal.db, 'connect', capture), \
                patch.object(rehearsal, 'ThreadingHTTPServer', side_effect=OSError('synthetic bind failure')):
            with self.assertRaisesRegex(OSError, 'synthetic bind failure'):
                flow.__enter__()
        self.assertIsNotNone(connection)
        with self.assertRaises(sqlite3.ProgrammingError):
            connection.execute('SELECT 1')
        self.assertIsNone(flow.conn)

    def test_final_source_read_error_preserves_failed_integrity_report(self):
        original = rehearsal.fingerprints()
        with patch.object(rehearsal, 'normal_arrival', lambda flow: None), \
                patch.object(rehearsal, 'fingerprints', side_effect=[original, FileNotFoundError('fixture')]):
            report = rehearsal.run(self.output, cases=('normal_arrival',))
        self.assertFalse(report['pass'])
        self.assertFalse(report['sources_stable'])
        self.assertEqual(report['integrity_errors'][0]['stage'], 'final_source_check')
        self.assertEqual(report['integrity_errors'][0]['error'], 'FileNotFoundError')
        stored = json.loads((self.output/'report.json').read_text(encoding='utf-8'))
        self.assertFalse(stored['pass'])


if __name__ == '__main__':
    unittest.main()
