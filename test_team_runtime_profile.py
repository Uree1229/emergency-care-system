"""Shared runtime compatibility while preserving the strict allocation lock."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from server import allocation_baseline as baseline
from server import team_runtime_profile as profile


class TeamRuntimeProfileTests(unittest.TestCase):
    def setUp(self):
        parent = baseline.ROOT/'.codex-build/team-runtime-profile-20261030/tmp'
        parent.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='profile-', dir=parent)
        self.root = Path(self.tmp.name)
        for name in (*baseline.SOURCE_PATHS, 'config/server_allocation_baseline_20261030.json', profile.PROFILE_PATH):
            destination = self.root/name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(baseline.ROOT/name, destination)
        self.lock = json.loads((self.root/'config/server_allocation_baseline_20261030.json').read_text(encoding='utf-8'))
        self.environment = deepcopy(self.lock['environment'])

    def tearDown(self):
        self.tmp.cleanup()

    def check(self, mode='full', allow_environment_difference=False, *, environment=None, value=None):
        with patch.object(baseline, 'current_environment', return_value=(
                self.environment if environment is None else environment)):
            return profile.verify_profile(mode, allow_environment_difference, root=self.root, profile=value)

    @staticmethod
    def codes(report, key='issues'):
        return [item['code'] for item in report[key]]

    def files(self):
        return {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}

    def eol_edit(self):
        path = self.root/'solver_improved.py'
        path.write_bytes(path.read_bytes().replace(b'\r\n', b'\n').replace(b'\n', b'\r\n'))

    def test_exact_source_settings_and_environment_match_without_writes(self):
        before = self.files()
        report = self.check()
        self.assertTrue(report['ok'], report)
        self.assertEqual(report['source_equivalence'], 'exact_bytes')
        self.assertTrue(report['exact_baseline_ok'])
        self.assertTrue(report['benchmark_environment'])
        self.assertTrue(report['environment_checked'])
        self.assertTrue(report['environment_match'])
        self.assertEqual(before, self.files())

    def test_crlf_checkout_is_compatible_but_not_byte_exact(self):
        self.eol_edit()
        report = self.check()
        self.assertTrue(report['ok'], report)
        self.assertEqual(report['source_equivalence'], 'eol_only')
        self.assertFalse(report['exact_baseline_ok'])
        self.assertFalse(report['benchmark_environment'])
        self.assertIn('EOL_ONLY_SOURCE_DIFFERENCE', self.codes(report, 'warnings'))

    def test_all_lf_checkout_is_compatible_even_when_original_files_mix_eol(self):
        for name in baseline.SOURCE_PATHS:
            path = self.root/name
            path.write_bytes(path.read_bytes().replace(b'\r\n', b'\n'))
        report = self.check()
        self.assertTrue(report['ok'], report)
        self.assertEqual(report['source_equivalence'], 'eol_only')

    def test_strict_original_baseline_still_rejects_crlf_change(self):
        before = (self.root/'config/server_allocation_baseline_20261030.json').read_bytes()
        self.eol_edit()
        self.assertTrue(self.check()['ok'])
        strict = baseline.verify(self.root, environment=self.environment)
        self.assertFalse(strict['ok'])
        self.assertIn('SOURCE_CHANGED', self.codes(strict))
        self.assertEqual(before, (self.root/'config/server_allocation_baseline_20261030.json').read_bytes())

    def test_content_or_whitespace_changes_are_not_eol_equivalent(self):
        path = self.root/'solver_improved.py'
        path.write_bytes(path.read_bytes()+b'\n# changed content\n')
        report = self.check()
        self.assertFalse(report['ok'])
        self.assertEqual(report['source_equivalence'], 'changed')
        self.assertIn('SOURCE_CHANGED', self.codes(report))

    def test_bare_cr_is_not_a_supported_normalization(self):
        path = self.root/'solver_improved.py'
        path.write_bytes(path.read_bytes().replace(b'\r\n', b'\n').replace(b'\n', b'\r', 1))
        report = self.check()
        self.assertFalse(report['ok'])
        self.assertEqual(report['source_equivalence'], 'changed')

    def test_missing_resource_accounting_dependency_fails(self):
        (self.root/'server/resource_accounting.py').unlink()
        report = self.check()
        self.assertFalse(report['ok'])
        self.assertIn('SOURCE_MISSING_OR_UNREADABLE', self.codes(report))

    def test_setting_change_fails_despite_environment_override(self):
        path = self.root/'server/solver_adapter.py'
        path.write_bytes(path.read_bytes().replace(b'HORIZON_MINUTES = 120', b'HORIZON_MINUTES = 121'))
        report = self.check(allow_environment_difference=True)
        self.assertFalse(report['ok'])
        self.assertIn('SETTINGS_CHANGED', self.codes(report))

    def test_runtime_environment_difference_requires_explicit_override(self):
        current = {**self.environment, 'sqlite': 'another-test-version'}
        strict = self.check(environment=current)
        self.assertFalse(strict['ok'])
        self.assertIn('ENVIRONMENT_DIFFERENCE', self.codes(strict))
        allowed = self.check(allow_environment_difference=True, environment=current)
        self.assertTrue(allowed['ok'], allowed)
        self.assertFalse(allowed['environment_match'])
        self.assertFalse(allowed['exact_baseline_ok'])
        self.assertFalse(allowed['benchmark_environment'])
        self.assertIn('NOT_FROZEN_BENCHMARK_ENVIRONMENT', self.codes(allowed, 'warnings'))

    def test_missing_solver_package_is_not_waived_by_environment_override(self):
        with patch.object(baseline, 'current_environment', side_effect=ModuleNotFoundError('ortools')):
            report = profile.verify_profile(allow_environment_difference=True, root=self.root)
        self.assertFalse(report['ok'])
        self.assertIn('SOLVER_ENVIRONMENT_UNAVAILABLE', self.codes(report))

    def test_voice_only_never_imports_ortools_or_claims_runtime_checked(self):
        with patch.object(baseline, 'current_environment', side_effect=AssertionError('must not import solver')):
            report = profile.verify_profile(mode='voice-only', root=self.root)
        self.assertTrue(report['ok'], report)
        self.assertFalse(report['environment_checked'])
        self.assertIsNone(report['environment_match'])
        self.assertIsNone(report['actual_environment']['ortools'])
        self.assertFalse(report['exact_baseline_ok'])
        self.assertFalse(report['benchmark_environment'])

    def test_profile_tamper_is_rejected_even_if_digest_is_recomputed(self):
        value = json.loads((self.root/profile.PROFILE_PATH).read_text(encoding='utf-8'))
        value['sources']['solver_improved.py']['lf_sha256'] = '0'*64
        value['profile_sha256'] = profile.profile_digest(value)
        report = self.check(value=value)
        self.assertFalse(report['ok'])
        self.assertEqual(self.codes(report), ['INVALID_RUNTIME_PROFILE'])

    def test_original_manifest_change_is_not_hidden_by_runtime_profile(self):
        path = self.root/'config/server_allocation_baseline_20261030.json'
        changed = deepcopy(self.lock)
        changed['environment']['sqlite'] = 'tampered'
        changed['manifest_sha256'] = baseline.manifest_digest(changed)
        path.write_text(json.dumps(changed), encoding='utf-8')
        self.assertEqual(self.codes(self.check()), ['INVALID_RUNTIME_PROFILE'])

    def test_duplicate_profile_keys_are_invalid(self):
        path = self.root/'duplicate-profile.json'
        path.write_text('{"schema_version":1,"schema_version":1}', encoding='utf-8')
        self.assertEqual(self.codes(self.check(value=path)), ['INVALID_RUNTIME_PROFILE'])

    def test_invalid_mode_and_nonboolean_environment_override_are_rejected(self):
        for kwargs in ({'mode': 'other'}, {'allow_environment_difference': 'yes'}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                profile.verify_profile(**kwargs)


if __name__ == '__main__':
    unittest.main()
