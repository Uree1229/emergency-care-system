"""Line-ending independent fingerprints for new experiment locks.

Existing locks hash exact bytes and stay as they are. New locks should record
normalized_sha256 so a Windows CRLF checkout and an LF checkout of the same commit
produce the same fingerprint.

    python scripts/source_hash.py simulation/example.py config/example.json
"""
import hashlib
import json
import sys
from pathlib import Path


def normalized_bytes(data):
    return data.replace(b'\r\n', b'\n')


def normalized_sha256(path):
    return hashlib.sha256(normalized_bytes(Path(path).read_bytes())).hexdigest()


def main(argv=None):
    paths = sys.argv[1:] if argv is None else argv
    if not paths:
        print(__doc__.strip())
        return 2
    print(json.dumps({Path(p).as_posix(): normalized_sha256(p) for p in paths}, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
