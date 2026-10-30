"""Online SQLite backups for the dedicated synthetic common runtime.

No source migration, expiry, resource refresh or existing-file overwrite occurs.
Only a NEW restored database has its transient planner cache/lease invalidated.
This is a local integrity check, not an authenticated signature or remote backup.
"""
import argparse
from contextlib import closing, contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import time

from .team_runtime import BASELINE_ID, CATALOG_ID, HOSPITAL_IDS, SCHEMA_VERSION, DatabaseLease

FORMAT = 'team-common-sqlite-backup'
VERSION = 1
SNAPSHOT = 'snapshot.db'
MANIFEST = 'manifest.json'
TRANSIENT_TABLES = frozenset(('planning_state', 'planning_results', 'sqlite_sequence'))
MARKER = dict(schema_version=SCHEMA_VERSION, catalog_id=CATALOG_ID, baseline_id=BASELINE_ID)


class BackupError(ValueError):
    """Safe diagnostic without patient values, SQL rows or underlying exceptions."""


class _Deadline:
    def __init__(self, seconds):
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds <= 0:
            raise BackupError('제한시간은 0보다 큰 유한한 초 단위 숫자여야 합니다.')
        self.end = time.monotonic()+seconds

    def remaining(self):
        seconds = self.end-time.monotonic()
        if seconds <= 0:
            raise BackupError('DB 작업 제한시간을 넘었습니다. 잠금이나 파일 상태를 확인한 뒤 새 경로로 재시도하세요.')
        return seconds

    def progress(self, *_):
        self.remaining()


@contextmanager
def _errors():
    try:
        yield
    except BackupError:
        raise
    except (sqlite3.Error, OSError, UnicodeError, ValueError, TypeError, OverflowError, RecursionError) as error:
        raise BackupError('백업 또는 복원 검증에 실패했습니다. 기존 파일은 덮어쓰지 않았습니다. 잠금·권한·파일 무결성을 확인하세요.') from error


def _path(value):
    # Reject aliases before resolve(), including junctions on Windows. A hard
    # link could otherwise bypass the runtime's per-path lease ownership.
    raw = Path(value).expanduser()
    if str(value) == ':memory:' or '..' in raw.parts:
        raise BackupError('상위 경로 별칭 없이 별도 DB 파일 경로를 지정하세요.')
    path = raw.absolute()
    for item in (*reversed(path.parents), path):
        if item.is_symlink() or (hasattr(item, 'is_junction') and item.is_junction()):
            raise BackupError('심볼릭 링크나 정션 경로는 사용하지 않습니다. 실제 경로를 지정하세요.')
        if item.exists():
            info = item.stat(follow_symlinks=False)
            if getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400):
                raise BackupError('재분석 경로는 사용하지 않습니다. 실제 경로를 지정하세요.')
            if item == path and stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
                raise BackupError('하드링크 파일은 사용하지 않습니다. 별도 파일 경로를 지정하세요.')
    return path.resolve()


def _file(path):
    path = _path(path)
    if not path.is_file():
        raise BackupError('읽을 DB 또는 백업 파일이 없습니다.')
    return path


def _sidecars_absent(path):
    for suffix in ('-wal', '-shm', '-journal'):
        candidate = Path(str(path)+suffix)
        if candidate.exists() or candidate.is_symlink():
            raise BackupError('대상 DB의 WAL·SHM·journal 파일이 이미 있습니다. 완전히 새로운 DB 경로를 지정하세요.')


def _new_database(path):
    path = _path(path)
    if path.exists():
        raise BackupError('기존 DB나 파일은 덮어쓰지 않습니다. 새 DB 경로를 지정하세요.')
    _sidecars_absent(path)
    return path


def _readonly(path, deadline):
    conn = sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=deadline.remaining(), isolation_level=None)
    try:
        conn.execute('PRAGMA query_only=ON')
        conn.set_progress_handler(lambda: int(time.monotonic() >= deadline.end), 1000)
        conn.execute('BEGIN')
    except BaseException:
        conn.close()
        raise
    return conn


def _identity(conn):
    row = conn.execute('SELECT schema_version,catalog_id,baseline_id FROM team_runtime_meta WHERE id=1').fetchone()
    hospitals = conn.execute('SELECT id,source FROM hospitals ORDER BY id').fetchall()
    if row != (SCHEMA_VERSION, CATALOG_ID, BASELINE_ID) or hospitals != [(hid, 'synthetic') for hid in sorted(HOSPITAL_IDS)]:
        raise BackupError('지정된 합성 병원을 사용하는 공통 실행 전용 DB만 백업·복원할 수 있습니다.')
    if conn.execute('SELECT COUNT(*) FROM team_runtime_meta').fetchone()[0] != 1:
        raise BackupError('공통 실행 DB 표식이 올바르지 않습니다.')
    if conn.execute('SELECT COUNT(*) FROM planning_state WHERE id=1').fetchone()[0] != 1:
        raise BackupError('공통 실행 DB의 계산 상태가 올바르지 않습니다.')


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_canonical(value).encode('utf-8')).hexdigest()


def _quote(identifier):
    return '"'+identifier.replace('"', '""')+'"'


def _state(conn, deadline):
    deadline.remaining()
    if conn.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
        raise BackupError('SQLite 무결성 검사를 통과하지 못했습니다.')
    if conn.execute('PRAGMA foreign_key_check').fetchone() is not None:
        raise BackupError('DB 참조 무결성 검사를 통과하지 못했습니다.')
    _identity(conn)
    schema = conn.execute('SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name,tbl_name').fetchall()
    tables = {}
    for (name,) in conn.execute("SELECT name FROM sqlite_schema WHERE type='table' ORDER BY name").fetchall():
        columns = [item[1] for item in conn.execute('PRAGMA table_info('+_quote(name)+')')]
        rows = []
        for row in conn.execute('SELECT * FROM '+_quote(name)):
            if len(rows) % 128 == 0:
                deadline.remaining()
            rows.append(_canonical([{'blob_hex': cell.hex()} if isinstance(cell, bytes) else cell for cell in row]))
        rows.sort()
        tables[name] = dict(columns=columns, count=len(rows), sha256=_digest(rows))
    deadline.remaining()
    return dict(schema_sha256=_digest(schema), tables=tables,
                business_sha256=_digest({key: value for key, value in tables.items() if key not in TRANSIENT_TABLES}))


def inspect_state(path, *, timeout_seconds=30):
    """Return hashes and counts only; inspect a consistent read-only snapshot."""
    with _errors():
        deadline = _Deadline(timeout_seconds)
        with closing(_readonly(_file(path), deadline)) as conn:
            return _state(conn, deadline)


def _hash_file(path, deadline):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while block := stream.read(1024*1024):
            deadline.remaining()
            digest.update(block)
    return digest.hexdigest()


def _copy(source, target, deadline):
    # The destination has already been reserved with exclusive file creation.
    # SQLite's backup API includes committed WAL data; copying a .db file does not.
    with closing(sqlite3.connect(target, timeout=deadline.remaining(), isolation_level=None)) as dest:
        source.backup(dest, pages=256, progress=deadline.progress, sleep=.01)
        deadline.remaining()
        # Make the artifact self-contained, with no live WAL/SHM dependencies.
        if dest.execute('PRAGMA journal_mode=DELETE').fetchone()[0] != 'delete':
            raise BackupError('단일 파일 백업 형식으로 변환하지 못했습니다.')


def _result(folder, manifest):
    return dict(verified=True, backup_dir=str(folder), snapshot_path=str(folder/SNAPSHOT),
                manifest_path=str(folder/MANIFEST), manifest=manifest)


def backup_database(source, output, *, timeout_seconds=30):
    """Create a NEW backup directory while the source runtime may be online."""
    with _errors():
        deadline = _Deadline(timeout_seconds)
        source, folder = _file(source), _path(output)
        if folder.exists() or folder == source:
            raise BackupError('백업 출력은 존재하지 않는 새 디렉터리여야 합니다.')
        # Validate the source before making output or modifying any destination.
        with closing(_readonly(source, deadline)) as conn:
            _identity(conn)
            folder.mkdir(parents=True, exist_ok=False)
            snapshot = folder/SNAPSHOT
            with snapshot.open('xb'):
                pass
            _copy(conn, snapshot, deadline)
        with closing(_readonly(snapshot, deadline)) as conn:
            state = _state(conn, deadline)
        manifest = dict(format=FORMAT, version=VERSION, created_at=int(time.time()),
            database_file=SNAPSHOT, sqlite_version=sqlite3.sqlite_version,
            file_sha256=_hash_file(snapshot, deadline), marker=dict(MARKER), logical_state=state)
        with (folder/MANIFEST).open('x', encoding='utf-8', newline='\n') as stream:
            stream.write(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2)+'\n')
        # A successful return requires rereading the completed artifact, not just
        # trusting the write path. An incomplete directory is preserved on error.
        return _verify(folder, deadline)


def _unique_object(items):
    result = {}
    for key, value in items:
        if key in result:
            raise BackupError('백업 명세에 중복 필드가 있습니다.')
        result[key] = value
    return result


def _load_manifest(folder):
    path = _file(folder/MANIFEST)
    if path.stat().st_size > 4*1024*1024:
        raise BackupError('백업 명세 파일이 너무 큽니다.')
    with path.open(encoding='utf-8') as stream:
        manifest = json.load(stream, object_pairs_hook=_unique_object)
    fields = {'format', 'version', 'created_at', 'database_file', 'sqlite_version', 'file_sha256', 'marker', 'logical_state'}
    if not isinstance(manifest, dict) or set(manifest) != fields:
        raise BackupError('백업 명세 필드가 올바르지 않습니다.')
    if (manifest['format'] != FORMAT or type(manifest['version']) is not int or manifest['version'] != VERSION
            or type(manifest['created_at']) is not int or manifest['created_at'] <= 0
            or manifest['database_file'] != SNAPSHOT or manifest['marker'] != MARKER
            or not isinstance(manifest['marker'], dict)
            or type(manifest['marker'].get('schema_version')) is not int
            or not isinstance(manifest['sqlite_version'], str)
            or re.fullmatch(r'\d+\.\d+\.\d+', manifest['sqlite_version']) is None
            or not isinstance(manifest['file_sha256'], str)
            or re.fullmatch('[0-9a-f]{64}', manifest['file_sha256']) is None):
        raise BackupError('백업 명세 버전·파일명·무결성 필드가 올바르지 않습니다.')
    state = manifest['logical_state']
    if not isinstance(state, dict) or set(state) != {'schema_sha256', 'business_sha256', 'tables'} or not isinstance(state['tables'], dict):
        raise BackupError('백업 논리 상태 명세가 올바르지 않습니다.')
    for digest in (state['schema_sha256'], state['business_sha256']):
        if not isinstance(digest, str) or re.fullmatch('[0-9a-f]{64}', digest) is None:
            raise BackupError('백업 논리 해시가 올바르지 않습니다.')
    for name, item in state['tables'].items():
        if (not isinstance(name, str) or not isinstance(item, dict) or set(item) != {'columns', 'count', 'sha256'}
                or not isinstance(item['columns'], list) or not all(isinstance(c, str) for c in item['columns'])
                or type(item['count']) is not int or item['count'] < 0
                or not isinstance(item['sha256'], str) or re.fullmatch('[0-9a-f]{64}', item['sha256']) is None):
            raise BackupError('백업 테이블 명세가 올바르지 않습니다.')
    return manifest


@contextmanager
def _verified_source(folder, deadline):
    folder = _path(folder)
    if not folder.is_dir():
        raise BackupError('완료된 백업 디렉터리를 지정하세요.')
    manifest = _load_manifest(folder)
    snapshot = _file(folder/SNAPSHOT)
    _sidecars_absent(snapshot)
    if _hash_file(snapshot, deadline) != manifest['file_sha256']:
        raise BackupError('백업 DB 파일 해시가 명세와 다릅니다.')
    with closing(_readonly(snapshot, deadline)) as conn:
        if _state(conn, deadline) != manifest['logical_state']:
            raise BackupError('백업 DB의 스키마·행 수·논리 해시가 명세와 다릅니다.')
        yield folder, manifest, conn


def _verify(folder, deadline):
    with _verified_source(folder, deadline) as (folder, manifest, _):
        return _result(folder, manifest)


def verify_backup(output, *, timeout_seconds=30):
    """Read-only verification; no migration or business-state changes."""
    with _errors():
        return _verify(output, _Deadline(timeout_seconds))


def restore_database(output, target, *, timeout_seconds=30):
    """Restore to a NEW path. Preserve business rows/times; reset planner only."""
    with _errors():
        deadline = _Deadline(timeout_seconds)
        with _verified_source(output, deadline) as (folder, manifest, source):
            target = _new_database(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            # Do not follow a pre-existing lock symlink through DatabaseLease.
            _path(str(target)+'.team-runtime.lock')
            with DatabaseLease(target):
                # Recheck after acquiring ownership and use exclusive creation.
                target = _new_database(target)
                with target.open('xb'):
                    pass
                _copy(source, target, deadline)
                with closing(_readonly(target, deadline)) as conn:
                    if _state(conn, deadline) != manifest['logical_state']:
                        raise BackupError('복사 직후 DB 검증에 실패했습니다. 서버를 시작하지 마세요.')
                with closing(sqlite3.connect(target, timeout=deadline.remaining(), isolation_level=None)) as conn:
                    conn.execute('BEGIN IMMEDIATE')
                    try:
                        previous = conn.execute('SELECT completed_revision,lease_until FROM planning_state WHERE id=1').fetchone()
                        removed = conn.execute('SELECT COUNT(*) FROM planning_results').fetchone()[0]
                        conn.execute('DELETE FROM planning_results')
                        conn.execute('UPDATE planning_state SET completed_revision=-1,lease_until=0,lease_owner=NULL,last_error=NULL WHERE id=1')
                        conn.execute('COMMIT')
                    except BaseException:
                        conn.execute('ROLLBACK')
                        raise
                with closing(_readonly(target, deadline)) as conn:
                    restored = _state(conn, deadline)
                if (restored['schema_sha256'] != manifest['logical_state']['schema_sha256']
                        or restored['business_sha256'] != manifest['logical_state']['business_sha256']):
                    raise BackupError('복원본의 업무 기록 검증에 실패했습니다. 서버를 시작하지 마세요.')
                return dict(_result(folder, manifest), database_path=str(target), business_state_preserved=True,
                    planning_reset=dict(cache_rows_removed=removed, previous_completed_revision=previous[0],
                        previous_lease_until=previous[1], completed_revision=-1, lease_until=0))


def main(argv=None):
    parser = argparse.ArgumentParser(description='합성 병원 공통 시연 DB 백업·검사·신규 복원')
    commands = parser.add_subparsers(dest='command', required=True)
    backup = commands.add_parser('backup')
    backup.add_argument('--db', required=True)
    backup.add_argument('--output', required=True)
    verify = commands.add_parser('verify')
    verify.add_argument('--backup', required=True)
    restore = commands.add_parser('restore')
    restore.add_argument('--backup', required=True)
    restore.add_argument('--db', required=True)
    for command in (backup, verify, restore):
        command.add_argument('--timeout-seconds', type=float, default=30)
    args = parser.parse_args(argv)
    try:
        if args.command == 'backup':
            result = backup_database(args.db, args.output, timeout_seconds=args.timeout_seconds)
        elif args.command == 'verify':
            result = verify_backup(args.backup, timeout_seconds=args.timeout_seconds)
        else:
            result = restore_database(args.backup, args.db, timeout_seconds=args.timeout_seconds)
    except BackupError as error:
        print(json.dumps(dict(success=False, error=str(error)), ensure_ascii=False))
        return 1
    # No row values or entire manifest in routine logs.
    print(json.dumps(dict(success=True, verified=result['verified'], backup_dir=result['backup_dir'],
        database_path=result.get('database_path'), tables=len(result['manifest']['logical_state']['tables']),
        business_sha256=result['manifest']['logical_state']['business_sha256'],
        business_state_preserved=result.get('business_state_preserved'), planning_reset=result.get('planning_reset')),
        ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
