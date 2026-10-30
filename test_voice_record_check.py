"""Read-only record discovery regressions; synthetic files and mocked processes only."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from server import voice_record_check as check


class CommandTests(unittest.TestCase):
    def test_quoted_paths_and_equals_options_are_preserved(self):
        command = r'"C:\Program Files\Python\python.exe" -m server.app --db "C:\voice tests\기록.db" --port 18042'
        self.assertEqual(check.server_database_from_command(command, 18042), r'C:\voice tests\기록.db')
        self.assertEqual(check.server_database_from_command(
            'python.exe -mserver.app --db="C:/voice tests/기록.db" --port=18042', 18042),
            'C:/voice tests/기록.db')

    def test_windows_escaped_quotes_and_backslashes(self):
        self.assertEqual(check.split_windows_command_line(r'python.exe "path with space" "a\\" "a\"b"'),
                         ['python.exe', 'path with space', 'a\\', 'a"b'])

    def test_ambiguous_or_unrelated_commands_fail_without_guessing(self):
        examples = [
            'python.exe -m server.app --db "C:/not closed.db --port 18042',
            'python.exe -m other.app --db C:/voice.db --port 18042',
            'python.exe -m server.app --port 18042',
            'python.exe -m server.app --db data/voice.db --port 18042',
            'python.exe -m server.app --db C:/one.db --db C:/two.db --port 18042',
            'python.exe -m server.app --db C:/one.db --port 18041',
        ]
        for command in examples:
            with self.subTest(command=command), self.assertRaises(check.RecordCheckError):
                check.server_database_from_command(command, 18042)

    def test_process_default_port_is_only_8000(self):
        command = 'python.exe -m server.app --db C:/voice.db'
        self.assertEqual(check.server_database_from_command(command, 8000), 'C:/voice.db')
        with self.assertRaises(check.RecordCheckError):
            check.server_database_from_command(command, 18042)


class DiscoveryTests(unittest.TestCase):
    def reply(self, **updates):
        data = {'pid': 5448, 'command_line': 'python.exe -m server.app --db C:/voice.db --port 18042',
                'creation_epoch': 2_000_000_000,
                'listeners': [{'address': '127.0.0.1', 'port': 18042, 'pid': 5448}]}
        data.update(updates)
        return subprocess.CompletedProcess([], 0, json.dumps(data), '')

    def test_static_command_and_integer_env_port_not_shell_interpolation(self):
        run = Mock(return_value=self.reply())
        result = check.discover_listener(18042, run=run, platform='nt')
        self.assertEqual(result['pid'], 5448)
        args, kwargs = run.call_args
        self.assertEqual(args[0][-1], check.POWERSHELL_QUERY)
        self.assertEqual(kwargs['env'][check.PORT_ENV], '18042')
        self.assertNotIn('shell', kwargs)
        self.assertNotIn('command_line', result)

    def test_multiple_addresses_same_pid_are_allowed(self):
        rows = [{'address': address, 'port': 18042, 'pid': 5448} for address in ('127.0.0.1', '::1')]
        self.assertEqual(check.discover_listener(18042, run=Mock(return_value=self.reply(listeners=rows)), platform='nt')['pid'], 5448)

    def test_multiple_pids_or_wrong_ports_fail(self):
        for rows in ([{'address': '::1', 'port': 18042, 'pid': 99}],
                     [{'address': '127.0.0.1', 'port': 18041, 'pid': 5448}], []):
            with self.subTest(rows=rows), self.assertRaises(check.RecordCheckError):
                check.discover_listener(18042, run=Mock(return_value=self.reply(listeners=rows)), platform='nt')

    def test_no_listener_timeout_and_unreadable_metadata_fail_closed(self):
        for run in (Mock(return_value=subprocess.CompletedProcess([], 2, '', 'private command text')),
                    Mock(side_effect=subprocess.TimeoutExpired('powershell.exe', 15)),
                    Mock(return_value=subprocess.CompletedProcess([], 0, 'not json', ''))):
            with self.assertRaises(check.RecordCheckError) as raised:
                check.discover_listener(18042, run=run, platform='nt')
            self.assertNotIn('private command', str(raised.exception))

    def test_invalid_port_and_unsupported_platform_do_not_spawn(self):
        run = Mock()
        for port in (0, 65536, True):
            with self.assertRaises(check.RecordCheckError):
                check.discover_listener(port, run=run, platform='nt')
        with self.assertRaises(check.RecordCheckError):
            check.discover_listener(18042, run=run, platform='posix')
        run.assert_not_called()


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/'voice records.db'
        connection = sqlite3.connect(self.path)
        connection.executescript('''
            CREATE TABLE voice_sessions(id TEXT,actor_id TEXT,state TEXT,version INTEGER,fields TEXT,updated_at INTEGER);
            CREATE TABLE voice_utterances(session_id TEXT,utterance_id TEXT,transcript TEXT,mode TEXT,provider TEXT,recorded_at INTEGER);
            CREATE TABLE hospitals(id TEXT);
        ''')
        connection.close()

    def tearDown(self):
        self.temp.cleanup()

    def fill(self):
        connection = sqlite3.connect(self.path)
        connection.execute('INSERT INTO voice_sessions VALUES(?,?,?,?,?,?)', ('VS-1','QUICK-VOICE-demo','DRAFT',2,'{}',2_000_000_000))
        connection.execute('INSERT INTO voice_utterances VALUES(?,?,?,?,?,?)', ('VS-1','UT-1','합성 원문','dictation','app_transcript',2_000_000_000))
        connection.commit()
        connection.close()

    def listener(self, pid=5448, path=None):
        return {'pid': pid, 'port': 18042, 'database_path': str((path or self.path).resolve()),
                'process_created_at':2_000_000_000,
                'module':'server.app','listeners':[{'address':'127.0.0.1','port':18042,'pid':pid}]}

    def test_zero_records_is_distinct_from_saved_text_with_zero_extracted_fields(self):
        empty = check.read_records(self.path)
        self.assertEqual(empty['record_status'], 'no_saved_sessions')
        self.fill()
        saved = check.read_records(self.path)
        self.assertEqual(saved['counts']['voice_utterances'], 1)
        self.assertEqual(saved['record_status'], 'saved_utterances')
        self.assertEqual(saved['latest_quick_sessions'][0]['structured_field_count'], 0)
        self.assertEqual(saved['latest_quick_sessions'][0]['extraction_status'], 'no_structured_fields')

    def test_default_omits_transcript_and_fields_values(self):
        self.fill()
        result = check.read_records(self.path)
        output = json.dumps(result, ensure_ascii=False)
        self.assertNotIn('합성 원문', output)
        self.assertNotIn('transcript', output)
        self.assertNotIn('latest_utterances', result['latest_quick_sessions'][0])
        included = check.read_records(self.path, include_text=True)
        self.assertEqual(included['latest_quick_sessions'][0]['latest_utterances'][0]['transcript'], '합성 원문')

    def test_read_only_uri_and_file_bytes_are_preserved(self):
        self.fill()
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        real_connect = sqlite3.connect
        with patch.object(check.sqlite3, 'connect', wraps=real_connect) as connect:
            check.read_records(self.path)
        args, kwargs = connect.call_args
        self.assertTrue(args[0].endswith('?mode=ro'))
        self.assertTrue(kwargs['uri'])
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), before)

    def test_nonexistent_file_is_not_created(self):
        missing = Path(self.temp.name)/'never-created.db'
        with self.assertRaises(check.RecordCheckError) as raised:
            check.read_records(missing)
        self.assertEqual(raised.exception.code, 'DB_NOT_FOUND')
        self.assertFalse(missing.exists())

    def test_missing_voice_schema_is_not_initialized(self):
        other = Path(self.temp.name)/'other.db'
        sqlite3.connect(other).close()
        before = other.read_bytes()
        with self.assertRaises(check.RecordCheckError) as raised:
            check.read_records(other)
        self.assertEqual(raised.exception.code, 'VOICE_SCHEMA_MISSING')
        self.assertEqual(other.read_bytes(), before)

    def test_explicit_database_source_is_unverified_without_port(self):
        discover = Mock()
        result = check.inspect_records(database=self.path, discover=discover)
        self.assertEqual(result['source']['kind'], 'explicit_db_unverified')
        self.assertFalse(result['source']['consistency_checked'])
        self.assertFalse(result['source']['identity_guaranteed'])
        discover.assert_not_called()

    def test_default_and_explicit_port_use_live_source_twice(self):
        for arguments in ({}, {'port':18042}, {'port':18042,'database':self.path}):
            with self.subTest(arguments=arguments):
                discover = Mock(return_value=self.listener())
                status_reader = Mock(return_value={'server_started_at':2_000_000_001,'hospitals':0})
                result = check.inspect_records(discover=discover, status_reader=status_reader, **arguments)
                self.assertTrue(result['source']['consistency_checked'])
                self.assertFalse(result['source']['identity_guaranteed'])
                self.assertEqual(discover.call_count, 2)
                self.assertEqual(status_reader.call_count, 2)

    def test_manual_database_mismatch_is_rejected_before_opening(self):
        other = Path(self.temp.name)/'other.db'
        with patch.object(check, 'read_records') as read:
            with self.assertRaises(check.RecordCheckError) as raised:
                check.inspect_records(port=18042, database=other, discover=Mock(return_value=self.listener()),
                    status_reader=Mock(return_value={'server_started_at':2_000_000_001,'hospitals':0}))
        self.assertEqual(raised.exception.code, 'DB_PORT_MISMATCH')
        read.assert_not_called()
        self.assertFalse(other.exists())

    def test_server_restart_during_read_is_reported_instead_of_returning_counts(self):
        discover = Mock(side_effect=[self.listener(), self.listener(pid=1234)])
        with self.assertRaises(check.RecordCheckError) as raised:
            check.inspect_records(port=18042, discover=discover,
                status_reader=Mock(return_value={'server_started_at':2_000_000_001,'hospitals':0}))
        self.assertEqual(raised.exception.code, 'LISTENER_CHANGED')

    def test_forwarded_http_from_older_process_is_rejected_before_opening_db(self):
        with patch.object(check, 'read_records') as read:
            with self.assertRaises(check.RecordCheckError) as raised:
                check.inspect_records(port=18042, discover=Mock(return_value=self.listener()),
                    status_reader=Mock(return_value={'server_started_at':1_999_999_000,'hospitals':0}))
        self.assertEqual(raised.exception.code, 'LISTENER_HTTP_TIME_MISMATCH')
        read.assert_not_called()

    def test_forwarded_http_with_different_hospital_count_does_not_report_zero(self):
        with self.assertRaises(check.RecordCheckError) as raised:
            check.inspect_records(port=18042, discover=Mock(return_value=self.listener()),
                status_reader=Mock(return_value={'server_started_at':2_000_000_001,'hospitals':10}))
        self.assertEqual(raised.exception.code, 'DB_HTTP_COUNT_MISMATCH')

    def test_http_unavailable_and_http_restart_fail_closed(self):
        unavailable = Mock(side_effect=check.RecordCheckError('HTTP_STATUS_UNAVAILABLE','unavailable'))
        with self.assertRaises(check.RecordCheckError) as raised:
            check.inspect_records(port=18042, discover=Mock(return_value=self.listener()), status_reader=unavailable)
        self.assertEqual(raised.exception.code, 'HTTP_STATUS_UNAVAILABLE')
        changed = Mock(side_effect=[{'server_started_at':2_000_000_001,'hospitals':0},
                                   {'server_started_at':2_000_000_002,'hospitals':0}])
        with self.assertRaises(check.RecordCheckError) as raised:
            check.inspect_records(port=18042, discover=Mock(return_value=self.listener()), status_reader=changed)
        self.assertEqual(raised.exception.code, 'HTTP_STATUS_CHANGED')

    def test_cli_reports_errors_without_traceback_or_creation(self):
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            code = check.main(['--db', str(Path(self.temp.name)/'missing.db')])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output.getvalue())['error'], 'DB_NOT_FOUND')
        self.assertNotIn('Traceback', output.getvalue())


class HttpStatusTests(unittest.TestCase):
    def opener(self, payload):
        response = Mock(status=200)
        response.read.return_value = json.dumps(payload).encode()
        context = Mock()
        context.__enter__ = Mock(return_value=response)
        context.__exit__ = Mock(return_value=None)
        opener = Mock()
        opener.open.return_value = context
        return opener

    def test_only_loopback_status_metadata_is_returned(self):
        opener = self.opener({'server':{'started_at':2_000_000_001},'database':{'readable':True},
                              'data':{'hospitals':10},'unused_private_text':'not returned'})
        result = check.read_http_status(18042, opener=opener)
        self.assertEqual(result, {'server_started_at':2_000_000_001,'hospitals':10})
        opener.open.assert_called_once_with('http://127.0.0.1:18042/api/system/status', timeout=5)

    def test_missing_or_ambiguous_metadata_is_rejected(self):
        for payload in ({}, {'server':{'started_at':None},'database':{'readable':True},'data':{'hospitals':0}},
                        {'server':{'started_at':2_000_000_000},'database':{'readable':True},'data':{'hospitals':False}}):
            with self.assertRaises(check.RecordCheckError):
                check.read_http_status(18042, opener=self.opener(payload))

    def test_http_connection_failure_does_not_return_an_empty_record_result(self):
        opener = Mock()
        opener.open.side_effect = OSError('private system detail')
        with self.assertRaises(check.RecordCheckError) as raised:
            check.read_http_status(18042, opener=opener)
        self.assertEqual(raised.exception.code, 'HTTP_STATUS_UNAVAILABLE')
        self.assertNotIn('private system', str(raised.exception))


if __name__ == '__main__':
    unittest.main()
