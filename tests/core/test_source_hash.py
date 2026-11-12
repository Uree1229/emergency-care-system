"""줄바꿈과 무관한 새 잠금용 해시."""
import tempfile
import unittest
from pathlib import Path

from scripts import source_hash


class NormalizedSourceHash(unittest.TestCase):
    def test_crlf_and_lf_checkouts_share_one_fingerprint(self):
        with tempfile.TemporaryDirectory() as folder:
            lf, crlf = Path(folder, 'lf.py'), Path(folder, 'crlf.py')
            lf.write_bytes(b'a = 1\nb = 2\n')
            crlf.write_bytes(b'a = 1\r\nb = 2\r\n')
            self.assertEqual(source_hash.normalized_sha256(lf), source_hash.normalized_sha256(crlf))

    def test_content_change_still_changes_the_fingerprint(self):
        with tempfile.TemporaryDirectory() as folder:
            first, second = Path(folder, 'a.py'), Path(folder, 'b.py')
            first.write_bytes(b'a = 1\n')
            second.write_bytes(b'a = 2\n')
            self.assertNotEqual(source_hash.normalized_sha256(first), source_hash.normalized_sha256(second))


if __name__ == '__main__':
    unittest.main()
