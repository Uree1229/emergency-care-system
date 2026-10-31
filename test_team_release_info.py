import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from server.team_release_info import release_info, check_dependencies
from server import team_runtime


class ReleaseIdentityTests(unittest.TestCase):
    def test_voice_only_does_not_require_installed_solver_packages(self):
        with patch('importlib.metadata.version', side_effect=AssertionError('No lookup')):
            self.assertFalse(check_dependencies('voice-only')['checked'])

    def test_full_mode_checks_every_exact_pin(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root/'requirements-server.txt').write_text('# pins\nfirst==1.0\nsecond==2.0\n')
            with patch('importlib.metadata.version', side_effect=lambda name: {'first':'1.0','second':'2.0'}[name]):
                result = check_dependencies('full', root)
            self.assertEqual(set(result['packages']), {'first','second'})
            self.assertTrue(result['checked'])
            with patch('importlib.metadata.version', return_value='1.0'):
                with self.assertRaisesRegex(ValueError, 'second'):
                    check_dependencies('full', root)

    def test_development_tree_does_not_claim_a_release(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'development'
            root.mkdir()
            self.assertEqual(release_info(root)['status'], 'unpackaged')
            self.assertFalse(release_info(root)['verified'])

    def test_adjacent_manifest_cannot_identify_another_tree(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'other'
            root.mkdir()
            (root.parent/'release-manifest.json').write_text('{}')
            with self.assertRaises(ValueError):
                release_info(root)

    def test_failed_integrity_never_claims_verified(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'source'
            root.mkdir()
            (root.parent/'release-manifest.json').write_text('{}')
            with patch('server.team_release_20261030.verify_release', return_value={
                    'ok':False, 'issues':['changed file']}):
                with self.assertRaisesRegex(ValueError, 'changed file'):
                    release_info(root)

    def test_identity_comes_from_verified_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'source'
            root.mkdir()
            (root.parent/'release-manifest.json').write_text(json.dumps({'label':'team-v1'}))
            report = dict(ok=True, release_id='team-v1:123', manifest_sha256='a'*64,
                          content_sha256='b'*64, file_count=3)
            with patch('server.team_release_20261030.verify_release', return_value=report) as verify:
                result = release_info(root)
            verify.assert_called_once_with(root.parent)
            self.assertTrue(result['verified'])
            self.assertEqual(result['label'], 'team-v1')
            self.assertEqual(result['manifest_sha256'], 'a'*64)

    def test_release_failure_precedes_database_and_socket_work(self):
        with tempfile.TemporaryDirectory() as temporary:
            runtime = team_runtime.CommonRuntime(Path(temporary)/'never-created.db', mode='voice-only')
            with patch.object(team_runtime, 'verify_profile', return_value={'ok':True}), \
                    patch.object(team_runtime, 'release_info', side_effect=ValueError('release changed')), \
                    patch.object(team_runtime, 'inspect_database') as inspect:
                with self.assertRaisesRegex(ValueError, 'release changed'):
                    runtime.start()
            inspect.assert_not_called()
            self.assertIsNone(runtime.http)
            self.assertFalse(Path(runtime.path).exists())


if __name__ == '__main__':
    unittest.main()
