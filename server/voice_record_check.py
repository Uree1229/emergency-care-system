"""Inspect saved voice records in the database used by a live local server.

Examples:
  python -m server.voice_record_check --port 18042
  python -m server.voice_record_check --db "C:/existing/voice.db"
  python -m server.voice_record_check --port 18042 --include-text

Port mode identifies the current listening process, never a saved launch manifest.
Manual --db without --port is explicitly unverified against a running server.
Port mode also reads the local system-status endpoint to detect inconsistent
forwarding/observation scopes. Matching metadata is not proof of DB identity.
No schema initialization, microphone, external API or database writes occur.
"""
import argparse
import datetime
import json
import os
from pathlib import Path, PureWindowsPath
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.request


PORT_ENV = 'CAPSTONE_VOICE_RECORD_CHECK_PORT'
POWERSHELL_QUERY = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
try {
    $taskPort = [int]$env:CAPSTONE_VOICE_RECORD_CHECK_PORT
    $taskRows = @(Get-NetTCPConnection -LocalPort $taskPort -State Listen -ErrorAction Stop)
    $taskPids = @($taskRows | Select-Object -ExpandProperty OwningProcess -Unique)
    if ($taskPids.Count -ne 1 -or $taskPids[0] -le 0) { throw 'Ambiguous listener' }
    $taskPid = [int]$taskPids[0]
    $taskProcess = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $taskPid) -ErrorAction Stop
    if (-not $taskProcess -or -not $taskProcess.CommandLine) { throw 'Missing command line' }
    $taskAgain = @(Get-NetTCPConnection -LocalPort $taskPort -State Listen -ErrorAction Stop |
        Select-Object -ExpandProperty OwningProcess -Unique)
    if ($taskAgain.Count -ne 1 -or [int]$taskAgain[0] -ne $taskPid) { throw 'Listener changed' }
    @{
        pid = $taskPid
        command_line = [string]$taskProcess.CommandLine
        creation_epoch = ([DateTimeOffset]$taskProcess.CreationDate).ToUnixTimeSeconds()
        listeners = @($taskRows | ForEach-Object {
            @{ address = [string]$_.LocalAddress; port = [int]$_.LocalPort; pid = [int]$_.OwningProcess }
        })
    } | ConvertTo-Json -Depth 4 -Compress
} catch {
    [Console]::Error.WriteLine('Cannot determine one current listener and its command line.')
    exit 2
}
"""


class RecordCheckError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def split_windows_command_line(command):
    """Split Microsoft-style quoting/backslashes without executing a shell.

    Spaces inside quotes, --db="...", and escaped quotes are preserved. An
    unmatched quote is rejected rather than guessing a database path.
    """
    if not isinstance(command, str) or not command.strip():
        raise RecordCheckError('PROCESS_COMMAND_UNAVAILABLE', '프로세스 명령줄을 확인하지 못했습니다.')
    result = []
    index = 0
    while index < len(command):
        while index < len(command) and command[index] in ' \t':
            index += 1
        if index == len(command):
            break
        token = []
        quoted = False
        while index < len(command) and (quoted or command[index] not in ' \t'):
            char = command[index]
            if char == '\\':
                start = index
                while index < len(command) and command[index] == '\\':
                    index += 1
                count = index - start
                if index < len(command) and command[index] == '"':
                    token.extend('\\' * (count // 2))
                    if count % 2:
                        token.append('"')
                        index += 1
                    elif quoted and index + 1 < len(command) and command[index + 1] == '"':
                        token.append('"')
                        index += 2
                    else:
                        quoted = not quoted
                        index += 1
                else:
                    token.extend('\\' * count)
            elif char == '"':
                if quoted and index + 1 < len(command) and command[index + 1] == '"':
                    token.append('"')
                    index += 2
                else:
                    quoted = not quoted
                    index += 1
            else:
                token.append(char)
                index += 1
        if quoted:
            raise RecordCheckError('PROCESS_COMMAND_AMBIGUOUS', '명령줄 따옴표가 닫히지 않아 DB를 특정할 수 없습니다.')
        result.append(''.join(token))
    return result


def _one_option(tokens, option):
    values = []
    for index, token in enumerate(tokens):
        if token == option:
            if index + 1 >= len(tokens) or tokens[index + 1].startswith('--'):
                raise RecordCheckError('PROCESS_COMMAND_AMBIGUOUS', f'{option} 인자가 비어 있습니다.')
            values.append(tokens[index + 1])
        elif token.startswith(option + '='):
            values.append(token[len(option) + 1:])
    if len(values) > 1 or any(not value for value in values):
        raise RecordCheckError('PROCESS_COMMAND_AMBIGUOUS', f'{option} 인자를 하나로 특정할 수 없습니다.')
    return values[0] if values else None


def server_database_from_command(command, port):
    tokens = split_windows_command_line(command)
    modules = []
    for index, token in enumerate(tokens):
        if token == '-m' and index + 1 < len(tokens):
            modules.append((tokens[index + 1], index + 2))
        elif token.startswith('-m') and len(token) > 2:
            modules.append((token[2:], index + 1))
    if len(modules) != 1 or modules[0][0] != 'server.app':
        raise RecordCheckError('NOT_VOICE_SERVER', '해당 포트의 프로세스가 python -m server.app 실행본이 아닙니다.')
    arguments = tokens[modules[0][1]:]
    db_value = _one_option(arguments, '--db')
    if not db_value:
        raise RecordCheckError('PROCESS_DB_UNSPECIFIED', '프로세스에 명시적인 --db가 없어 실제 DB 경로를 확정할 수 없습니다.')
    if not (Path(db_value).is_absolute() or PureWindowsPath(db_value).is_absolute()):
        raise RecordCheckError('PROCESS_DB_RELATIVE', '서버 --db가 상대 경로입니다. 프로세스 작업 폴더를 추측하지 않습니다. 절대 경로로 서버를 실행하거나 수동 --db로 조회하세요.')
    process_port = _one_option(arguments, '--port')
    try:
        process_port = int(process_port) if process_port is not None else 8000
    except ValueError:
        raise RecordCheckError('PROCESS_COMMAND_AMBIGUOUS', '프로세스의 --port를 해석할 수 없습니다.') from None
    if process_port != port:
        raise RecordCheckError('PROCESS_PORT_MISMATCH', '듣는 포트와 서버 명령줄의 --port가 다릅니다.')
    return db_value


def discover_listener(port, *, run=None, platform=None):
    if type(port) is not int or not 1 <= port <= 65535:
        raise RecordCheckError('INVALID_PORT', '포트는 1~65535 정수여야 합니다.')
    if (platform or os.name) != 'nt':
        raise RecordCheckError('PLATFORM_UNSUPPORTED', '자동 포트 추적은 Windows에서 지원합니다. 다른 환경은 기존 DB를 --db로 명시하세요.')
    run = run or subprocess.run
    try:
        result = run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', POWERSHELL_QUERY],
                     capture_output=True, text=True, encoding='utf-8', timeout=15,
                     env={**os.environ, PORT_ENV: str(port)}, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise RecordCheckError('LISTENER_QUERY_FAILED', '현재 포트와 프로세스를 조회하지 못했습니다. 권한과 서버 실행 상태를 확인하세요.') from None
    if result.returncode:
        raise RecordCheckError('LISTENER_QUERY_FAILED', '듣는 프로세스를 하나로 특정하지 못했습니다. 서버 실행 상태·조회 권한·중복 리스너를 확인하세요.')
    try:
        data = json.loads(result.stdout.lstrip('\ufeff'))
        pid = data['pid']
        creation_epoch = data['creation_epoch']
        rows = data['listeners']
        if (type(pid) is not int or pid <= 0 or type(creation_epoch) is not int
                or creation_epoch <= 0 or not isinstance(rows, list) or not rows):
            raise ValueError
        if any(row['pid'] != pid or row['port'] != port for row in rows):
            raise ValueError
        database = server_database_from_command(data['command_line'], port)
    except RecordCheckError:
        raise
    except (ValueError, TypeError, KeyError):
        raise RecordCheckError('LISTENER_QUERY_AMBIGUOUS', '포트 조회 결과를 일관되게 확인할 수 없습니다.') from None
    return {'pid': pid, 'port': port, 'database_path': str(Path(database).resolve()),
            'process_created_at': creation_epoch, 'module': 'server.app', 'listeners': rows}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        return None


def read_http_status(port, *, opener=None):
    """Read only two nonclinical signals from exactly this loopback endpoint."""
    if type(port) is not int or not 1 <= port <= 65535:
        raise RecordCheckError('INVALID_PORT', '포트는 1~65535 정수여야 합니다.')
    opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    url = f'http://127.0.0.1:{port}/api/system/status'
    try:
        with opener.open(url, timeout=5) as response:
            if response.status != 200:
                raise RecordCheckError('HTTP_STATUS_UNAVAILABLE', '현재 포트의 시스템 상태 응답을 확인하지 못했습니다.')
            data = response.read(262145)
            if len(data) > 262144:
                raise RecordCheckError('HTTP_STATUS_AMBIGUOUS', '시스템 상태 응답이 진단 범위를 초과했습니다.')
        status = json.loads(data)
        started = status['server']['started_at']
        hospitals = status['data']['hospitals']
        if (status['database']['readable'] is not True or type(started) is not int
                or started <= 0 or type(hospitals) is not int or hospitals < 0):
            raise ValueError
        return {'server_started_at': started, 'hospitals': hospitals}
    except RecordCheckError:
        raise
    except (OSError, urllib.error.URLError):
        raise RecordCheckError('HTTP_STATUS_UNAVAILABLE', '해당 포트의 HTTP 상태를 확인하지 못했습니다. 다른 DB의 빈 기록을 서버 기록으로 판정하지 않았습니다.') from None
    except (ValueError, TypeError, KeyError):
        raise RecordCheckError('HTTP_STATUS_AMBIGUOUS', 'HTTP 상태의 시작 시각·병원 건수를 확인할 수 없습니다.') from None


def _utc(value):
    if value is None:
        return None
    try:
        return datetime.datetime.fromtimestamp(value, datetime.timezone.utc).isoformat()
    except (ValueError, TypeError, OSError, OverflowError):
        return None


def read_records(database, *, limit=3, include_text=False):
    if type(limit) is not int or not 1 <= limit <= 20:
        raise RecordCheckError('INVALID_LIMIT', '최신 기록 수는 1~20이어야 합니다.')
    path = Path(database).expanduser().resolve()
    if not path.is_file():
        raise RecordCheckError('DB_NOT_FOUND', '지정한 기존 DB 파일이 없습니다. 빈 DB는 생성하지 않았습니다.')
    connection = None
    try:
        connection = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA query_only=ON')
        connection.execute('BEGIN')
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'voice_sessions', 'voice_utterances'} <= tables:
            raise RecordCheckError('VOICE_SCHEMA_MISSING', '이 DB에는 음성 초안·발화 테이블이 없습니다. 다른 DB일 수 있습니다.')
        counts = {
            'hospitals': connection.execute('SELECT COUNT(*) FROM hospitals').fetchone()[0] if 'hospitals' in tables else None,
            'voice_sessions': connection.execute('SELECT COUNT(*) FROM voice_sessions').fetchone()[0],
            'voice_utterances': connection.execute('SELECT COUNT(*) FROM voice_utterances').fetchone()[0],
            'quick_sessions': connection.execute("SELECT COUNT(*) FROM voice_sessions WHERE actor_id LIKE 'QUICK-VOICE-%'").fetchone()[0],
            'quick_utterances': connection.execute("SELECT COUNT(*) FROM voice_utterances u JOIN voice_sessions s ON s.id=u.session_id WHERE s.actor_id LIKE 'QUICK-VOICE-%'").fetchone()[0],
        }
        latest = []
        for row in connection.execute("SELECT id,state,version,fields,updated_at FROM voice_sessions WHERE actor_id LIKE 'QUICK-VOICE-%' ORDER BY updated_at DESC,rowid DESC LIMIT ?", (limit,)):
            fields = json.loads(row['fields'])
            if not isinstance(fields, dict):
                raise RecordCheckError('VOICE_DATA_INVALID', '음성 초안 필드 형식을 확인할 수 없습니다.')
            utterance_count = connection.execute('SELECT COUNT(*) FROM voice_utterances WHERE session_id=?', (row['id'],)).fetchone()[0]
            item = {'session_id': row['id'], 'state': row['state'], 'version': row['version'],
                    'structured_field_count': len(fields), 'utterance_count': utterance_count,
                    'updated_at': row['updated_at'], 'updated_at_utc': _utc(row['updated_at']),
                    'record_status': 'saved_utterances' if utterance_count else 'draft_without_utterances',
                    'extraction_status': 'has_structured_fields' if fields else 'no_structured_fields'}
            if include_text:
                item['latest_utterances'] = [dict(utterance) for utterance in connection.execute(
                    'SELECT utterance_id,transcript,mode,provider,recorded_at FROM voice_utterances WHERE session_id=? ORDER BY rowid DESC LIMIT ?',
                    (row['id'], limit))]
            latest.append(item)
        return {'counts': counts, 'latest_quick_sessions': latest, 'include_text': bool(include_text),
                'record_status': 'saved_utterances' if counts['voice_utterances'] else
                                 'drafts_without_utterances' if counts['voice_sessions'] else 'no_saved_sessions'}
    except RecordCheckError:
        raise
    except json.JSONDecodeError:
        raise RecordCheckError('VOICE_DATA_INVALID', '음성 초안 JSON 형식을 확인할 수 없습니다.') from None
    except sqlite3.Error:
        raise RecordCheckError('DB_READ_FAILED', '기존 DB를 읽지 못했습니다. 음성 스키마·조회 권한·잠금 상태를 확인하세요.') from None
    finally:
        if connection is not None:
            connection.close()


def inspect_records(*, port=None, database=None, limit=3, include_text=False,
                    discover=None, status_reader=None):
    discover = discover or discover_listener
    status_reader = status_reader or read_http_status
    if port is None and database is None:
        port = 18042
    listener = discover(port) if port is not None else None
    status = status_reader(port) if listener else None
    if listener and (type(listener.get('process_created_at')) is not int
                     or status['server_started_at'] < listener['process_created_at']):
        raise RecordCheckError('LISTENER_HTTP_TIME_MISMATCH', 'HTTP 서버 시작 시각이 조회한 프로세스 생성 시각보다 이릅니다. 프록시·격리 환경의 관측 범위가 다를 수 있어 기록을 판정하지 않았습니다.')
    path = Path(database).expanduser().resolve() if database is not None else Path(listener['database_path'])
    if listener and os.path.normcase(str(path)) != os.path.normcase(str(Path(listener['database_path']).resolve())):
        raise RecordCheckError('DB_PORT_MISMATCH', '수동 --db가 현재 포트의 서버 DB와 다릅니다. 다른 DB의 0건을 서버 기록으로 보고하지 않았습니다.')
    result = read_records(path, limit=limit, include_text=include_text)
    if listener and (result['counts']['hospitals'] is None
                     or result['counts']['hospitals'] != status['hospitals']):
        raise RecordCheckError('DB_HTTP_COUNT_MISMATCH', 'HTTP 서버와 프로세스 DB의 병원 건수가 다릅니다. 다른 DB의 0건을 서버 기록으로 판정하지 않았습니다.')
    if listener:
        after = discover(port)
        if ((after['pid'], after['database_path'], after.get('process_created_at')) !=
                (listener['pid'], listener['database_path'], listener['process_created_at'])):
            raise RecordCheckError('LISTENER_CHANGED', '조회 중 서버 프로세스 또는 DB 경로가 바뀌었습니다. 다시 조회하세요.')
        if status_reader(port) != status:
            raise RecordCheckError('HTTP_STATUS_CHANGED', '조회 중 HTTP 서버 상태가 바뀌었습니다. 다시 조회하세요.')
    result['source'] = {'kind': 'explicit_db_listener_http_consistent' if database is not None and listener else
                               'listener_http_db_consistent' if listener else 'explicit_db_unverified',
                        'database_path': str(path), 'consistency_checked': listener is not None,
                        'identity_guaranteed': False,
                        'consistency_scope': 'PID/start time/hospital-count consistency only; not proof of database identity' if listener else
                                             'manual database path; no live-server identity check'}
    if listener:
        result['source'].update(listener)
        result['source']['http_status'] = status
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, help='현재 듣는 Windows 서버 포트; 인자 없으면 18042')
    parser.add_argument('--db', help='기존 SQLite 파일; --port와 함께 주면 실제 서버 DB와 일치해야 함')
    parser.add_argument('--limit', type=int, default=3, help='최신 QUICK 세션 수와 텍스트 조회 시 발화 수(1~20)')
    parser.add_argument('--include-text', action='store_true', help='명시적으로 인식 원문 출력 허용; 기본은 건수·메타데이터만')
    args = parser.parse_args(argv)
    try:
        result = inspect_records(port=args.port, database=args.db, limit=args.limit, include_text=args.include_text)
    except RecordCheckError as error:
        print(json.dumps({'error': error.code, 'message': str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
