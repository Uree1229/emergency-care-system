"""Read-only reproducibility lock checks. No live service, DB or UI mutation."""
from copy import deepcopy
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from server import allocation_baseline as baseline


class AllocationBaselineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = json.loads(baseline.DEFAULT_MANIFEST.read_text(encoding='utf-8'))
        cls.actual_environment = baseline.current_environment()
        cls.temp_root = baseline.ROOT / '.codex-build/allocation-baseline-20261030/tmp'
        cls.temp_root.mkdir(parents=True, exist_ok=True)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='baseline-fixture-', dir=self.temp_root)
        self.root = Path(self.tmp.name)
        for path in baseline.SOURCE_PATHS:
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(baseline.ROOT / path, target)
        self.lock = deepcopy(self.original)

    def tearDown(self):
        self.tmp.cleanup()

    def check(self, lock=None, environment=None):
        return baseline.verify(self.root, self.lock if lock is None else lock,
            environment=self.actual_environment if environment is None else environment)

    def resign(self):
        self.lock['manifest_sha256'] = baseline.manifest_digest(self.lock)

    def codes(self, result):
        return [issue['code'] for issue in result['issues']]

    def test_frozen_sources_settings_and_runtime_environment_pass_without_any_write(self):
        before = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        lock_before = deepcopy(self.lock)
        result = self.check()
        after = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertTrue(result['ok'], result)
        self.assertEqual(before, after)
        self.assertEqual(lock_before, self.lock)
        self.assertEqual(result['source_count'], len(baseline.SOURCE_PATHS))

    def test_changed_solver_is_identified_without_refreshing_manifest(self):
        source = self.root / 'solver_improved.py'
        source.write_bytes(source.read_bytes() + b'\n# drift fixture\n')
        original_hash = self.lock['sources']['solver_improved.py']['sha256']
        result = self.check()
        self.assertFalse(result['ok'])
        changed = next(i for i in result['issues'] if i['code'] == 'SOURCE_CHANGED')
        self.assertEqual(changed['path'], 'solver_improved.py')
        self.assertEqual(self.lock['sources']['solver_improved.py']['sha256'], original_hash)

    def test_missing_source_is_identified(self):
        (self.root / 'server/planning.py').unlink()
        result = self.check()
        item = next(i for i in result['issues'] if i['code'] == 'SOURCE_MISSING_OR_UNREADABLE')
        self.assertEqual(item['path'], 'server/planning.py')

    def test_resource_ledger_dependency_change_or_loss_cannot_pass_baseline(self):
        relative = 'server/resource_accounting.py'
        self.assertIn(relative, self.lock['sources'])
        source = self.root / relative
        source.write_bytes(source.read_bytes() + b'\n# synthetic ledger drift\n')
        changed = self.check()
        self.assertFalse(changed['ok'])
        self.assertTrue(any(item['code'] == 'SOURCE_CHANGED' and item['path'] == relative
                            for item in changed['issues']))
        source.unlink()
        missing = self.check()
        self.assertFalse(missing['ok'])
        self.assertTrue(any(item['code'] == 'SOURCE_MISSING_OR_UNREADABLE' and item['path'] == relative
                            for item in missing['issues']))

    def test_settings_drift_is_reported_along_with_source_change(self):
        source = self.root / 'server/solver_adapter.py'
        source.write_text(source.read_text(encoding='utf-8').replace('HORIZON_MINUTES = 120', 'HORIZON_MINUTES = 121'), encoding='utf-8')
        result = self.check()
        self.assertIn('SOURCE_CHANGED', self.codes(result))
        self.assertIn('SETTINGS_CHANGED', self.codes(result))
        item = next(i for i in result['issues'] if i['code'] == 'SETTINGS_CHANGED')
        self.assertEqual(item['actual']['model']['horizon_minutes'], 121)

    def test_misdeclared_settings_fail_even_when_content_digest_is_recomputed(self):
        self.lock['settings']['inquiry']['batch_size'] = 5
        self.resign()
        result = self.check()
        self.assertEqual(self.codes(result), ['SETTINGS_CHANGED'])

    def test_manifest_content_change_without_digest_refresh_is_invalid(self):
        self.lock['settings']['solver']['seconds'] = 10
        result = self.check()
        self.assertEqual(self.codes(result), ['INVALID_MANIFEST'])
        self.assertIn('digest mismatch', result['issues'][0]['actual'])

    def test_whitelist_cannot_omit_a_core_dependency(self):
        del self.lock['sources']['server/service.py']
        self.resign()
        self.assertEqual(self.codes(self.check()), ['INVALID_MANIFEST'])

    def test_runtime_library_change_is_explicit_reproducibility_failure(self):
        environment = {**self.actual_environment, 'ortools': 'different-test-version'}
        result = self.check(environment=environment)
        self.assertEqual(self.codes(result), ['ENVIRONMENT_CHANGED'])
        self.assertEqual(result['issues'][0]['actual']['ortools'], 'different-test-version')

    def test_schema_boolean_or_unknown_fields_are_not_silently_accepted(self):
        for update in ({'schema_version': True}, {'unknown': 'ignored?'}):
            with self.subTest(update=update):
                lock = {**self.lock, **update}
                lock['manifest_sha256'] = baseline.manifest_digest(lock)
                self.assertEqual(self.codes(self.check(lock)), ['INVALID_MANIFEST'])

    def test_traversal_windows_drive_and_backslash_paths_are_rejected_before_read(self):
        fingerprint = deepcopy(self.lock['sources']['solver_improved.py'])
        for path in ('../outside.py', '/outside.py', 'C:/outside.py', 'server\\outside.py'):
            with self.subTest(path=path):
                lock = deepcopy(self.lock)
                lock['sources'][path] = fingerprint
                lock['manifest_sha256'] = baseline.manifest_digest(lock)
                result = self.check(lock)
                self.assertEqual(self.codes(result), ['INVALID_MANIFEST'])

    def test_duplicate_json_keys_are_rejected(self):
        path = self.root / 'duplicate.json'
        path.write_text('{"schema_version":1,"schema_version":1}', encoding='utf-8')
        result = baseline.verify(self.root, path, environment=self.actual_environment)
        self.assertEqual(self.codes(result), ['INVALID_MANIFEST'])
        self.assertIn('duplicate JSON key', result['issues'][0]['actual'])

    def test_voice_and_ui_edits_are_outside_algorithm_lock(self):
        for relative in ('server/voice_terms.py', 'paramedic-ui/app.js'):
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('synthetic unrelated concurrent edit', encoding='utf-8')
        self.assertTrue(self.check()['ok'])

    def test_frozen_objective_is_priority_group_first_not_total_patient_count_first(self):
        from solver_improved import score
        data = {'patients': [{'id': 'HIGH', 'priority': 4, 'release': 0},
                             {'id': 'LOW', 'priority': 1, 'release': 0}]}
        plan = {'HIGH': None, 'LOW': {'h': 0, 'start': 5}}
        self.assertEqual(score(data, plan, 'severity_first'), (1, 0, 0, 0, 0, 5))
        settings = self.lock['settings']
        self.assertEqual(settings['solver']['policy'], 'severity_first')
        self.assertEqual(settings['solver']['seconds'], 3)
        self.assertEqual(settings['inquiry']['policy'], 'fixed_3')
        self.assertEqual(settings['model']['resource_types'], ['ed_bed', 'special_team'])

    def test_cli_failure_returns_nonzero_and_reports_exact_drift(self):
        report = {'baseline_id': baseline.BASELINE_ID, 'ok': False,
                  'issues': [{'code': 'SOURCE_CHANGED', 'path': 'solver_improved.py'}]}
        output = io.StringIO()
        with patch.object(baseline, 'verify', return_value=report), patch('sys.stdout', output):
            code = baseline.main(['check', '--json'])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output.getvalue()), report)


if __name__ == '__main__':
    unittest.main()
