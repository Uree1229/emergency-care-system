"""Profile storage remains isolated and runtime data stays outside Git."""
import importlib.util
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent


def isolated_database(path):
    spec = importlib.util.spec_from_file_location('dev1_isolated_profile', ROOT/'emergency-app/database.py')
    module = importlib.util.module_from_spec(spec)
    original_connect = sqlite3.connect

    def guarded_connect(target, *args, **kwargs):
        # The regression must fail before opening an unintended existing DB.
        if Path(target).resolve() != path.resolve():
            raise AssertionError('Profile DB configuration was ignored; no unrelated DB was opened.')
        return original_connect(target, *args, **kwargs)

    with patch.dict(os.environ, {'EMERGENCY_PROFILE_DB': str(path)}), patch.object(sqlite3, 'connect', guarded_connect):
        spec.loader.exec_module(module)
    return module


class ProfileIsolation(unittest.TestCase):
    def test_configured_nested_database_is_created_and_profile_survives_reinitialization(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)/'nested/profiles.db'
            module = isolated_database(target)
            self.assertTrue(target.is_file())
            self.assertEqual(module.DB_PATH.resolve(), target.resolve())
            key = module.get_retry_signing_key()
            self.assertEqual(len(key), 32)
            module.save_patient('합성 프로필', '2000-01-01', '', '가상 주소', '', '')
            module.create_database()
            self.assertEqual(isolated_database(target).get_retry_signing_key(), key)
            rows = module.get_patients()
            self.assertEqual(len(rows), 1)
            self.assertEqual(module.get_patient(rows[0][0])[1], '합성 프로필')

    def test_two_configured_databases_do_not_share_profiles(self):
        with tempfile.TemporaryDirectory() as temporary:
            first = isolated_database(Path(temporary)/'first/profiles.db')
            second = isolated_database(Path(temporary)/'second/profiles.db')
            self.assertNotEqual(first.get_retry_signing_key(), second.get_retry_signing_key())
            first.save_patient('첫 합성 프로필', '', '', '', '', '')
            self.assertEqual(len(first.get_patients()), 1)
            self.assertEqual(second.get_patients(), [])

    def test_git_does_not_track_intake_databases_or_python_cache(self):
        result = subprocess.run(['git', 'ls-files', '-z', '--', 'emergency-app'], cwd=ROOT,
                                check=True, capture_output=True)
        names = result.stdout.decode('utf-8').split('\0')
        forbidden = [name for name in names if name and ('/__pycache__/' in name
                     or name.endswith(('.pyc', '.db', '.db-wal', '.db-shm', '.sqlite', '.sqlite3')))]
        self.assertEqual(forbidden, [], 'Runtime DB/cache files belong in ignored local storage.')


if __name__ == '__main__':
    unittest.main()
