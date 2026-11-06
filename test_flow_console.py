"""Fresh synthetic SQLite and handler stubs only; no server, browser or audio."""
from contextlib import closing, redirect_stdout, redirect_stderr
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from server import db, flow_console as console
from server import flow_console_runtime as console_runtime

NOW = 10000
PRIVATE = 'PRIVATE_ORIGINAL_CONTACT_EVIDENCE_CANARY'


class FlowConsoleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='flow-console-')
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'synthetic.db'
        self.conn = db.connect(str(self.path))
        self.addCleanup(self.conn.close)
        db.init(self.conn)
        for hid in ('H1', 'H2'):
            self.conn.execute('INSERT INTO hospitals(id,name,lat,lon,source) VALUES(?,?,?,?,?)',
                              (hid, '가상 ' + hid, 35.16, 126.85, 'synthetic'))
        extra = {'vitals': {'hr': 92, 'sbp': 120, 'dbp': 80, 'spo2': 98, 'private': PRIVATE},
                 'voice_observations': {'session_id': 'VS-APPLIED', 'fields': {'secret': PRIVATE},
                                        'field_metadata': {'pulse': {'evidence': [PRIVATE]}}},
                 'contact': PRIVATE, 'free_note': PRIVATE}
        for pid, age in (('P-A', 47), ('P-B', 61)):
            self.conn.execute('INSERT INTO patients(id,reporter_role,contact,age,symptoms,extra,consent,'
                'lat,lon,requested_at,state,consciousness,breathing) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (pid, 'transporter', PRIVATE, age, '합성 흉통' if pid == 'P-A' else '다른 환자',
                 json.dumps(extra if pid == 'P-A' else {}), 1, 35.16, 126.85, NOW-100, 'WAITING', 'alert', 'normal'))
            self.conn.execute('INSERT INTO events(at,actor,patient_id,type,data) VALUES(?,?,?,?,?)',
                (NOW-100, 'creator-' + pid, pid, 'patient_created', json.dumps({'private': PRIVATE})))
        self.conn.execute('INSERT INTO voice_sessions(id,actor_id,patient_id,state,version,fields,field_metadata,'
            'created_at,updated_at,submitted_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
            ('VS-APPLIED', 'original-crew', 'P-A', 'SUBMITTED', 4, '{}', '{}', NOW-100, NOW-50, NOW-50))
        fields = dict(age=32, sex='female', chief_complaint='respiratory', private=PRIVATE)
        self.conn.execute('INSERT INTO voice_sessions(id,actor_id,version,fields,field_metadata,created_at,updated_at) '
            'VALUES(?,?,?,?,?,?,?)', ('VS-DRAFT', 'draft-crew', 3, json.dumps(fields),
                                    json.dumps({'private': PRIVATE}), NOW-20, NOW-10))
        self.conn.execute('INSERT INTO voice_utterances VALUES(?,?,?,?,?,?,?)',
            ('VS-DRAFT', 'u1', PRIVATE, 'dictation', 'synthetic', NOW-10, NOW-10))
        self.conn.execute('INSERT INTO inquiries(patient_id,hospital_id,state,created_at,last_sent_at,patient_version,'
            'expires_at,question,answer) VALUES(?,?,?,?,?,?,?,?,?)',
            ('P-A', 'H1', 'ACCEPTED', NOW-90, NOW-90, 1, NOW-1, '합성 추가 질문', '합성 답변'))
        self.conn.execute('INSERT INTO inquiry_workflows(patient_id,enabled,state,started_at,deadline_at,'
            'next_evaluation_at,updated_at) VALUES(?,?,?,?,?,?,?)',
            ('P-A', 1, 'RUNNING', NOW-80, NOW+200, NOW+10, NOW-10))
        for pid in ('P-A', 'P-B'):
            cid = self.conn.execute('INSERT INTO conversations(patient_id,created_at) VALUES(?,?)', (pid, NOW-50)).lastrowid
            event = self.conn.execute('INSERT INTO events(at,actor,patient_id,hospital_id,type,data) VALUES(?,?,?,?,?,?)',
                (NOW-40, 'hospital-actor', pid, 'H1', 'destination_confirmed', json.dumps({'private': PRIVATE}))).lastrowid
            hsid = self.conn.execute('INSERT INTO conversation_hospital_sessions(conversation_id,hospital_id,'
                'joined_at,joined_event_id) VALUES(?,?,?,?)', (cid, 'H1', NOW-40, event)).lastrowid
            self.conn.execute('INSERT INTO chat_messages(conversation_id,hospital_session_id,sender_role,sender_name,'
                'body,created_at,client_message_id) VALUES(?,?,?,?,?,?,?)',
                (cid, hsid, 'patient', '가상 구급대', '환자 A 전달 내용' if pid == 'P-A' else 'B_MESSAGE_SECRET', NOW-20, 'm1'))

    def snapshot(self, **kwargs):
        return console.snapshot(self.path, NOW, **kwargs)

    def logical(self):
        return hashlib.sha256('\n'.join(self.conn.iterdump()).encode()).hexdigest()

    def error(self, status, code, function, *args, **kwargs):
        with self.assertRaises(console.FlowConsoleError) as found:
            function(*args, **kwargs)
        self.assertEqual(found.exception.status, status)
        self.assertEqual(found.exception.code, code)

    def test_empty_selection_is_explicit_and_both_lists_are_metadata_only(self):
        result = self.snapshot()
        self.assertIsNone(result['selected'])
        self.assertTrue(result['readonly'])
        self.assertFalse(result['clinical_state_changed'])
        self.assertEqual(result['scope'], 'synthetic_local_observer')
        self.assertEqual(len(result['patients']), 2)
        self.assertEqual([d['id'] for d in result['drafts']], ['VS-DRAFT'])
        self.assertNotIn(PRIVATE, json.dumps(result, ensure_ascii=False))

    def test_patient_snapshot_keeps_current_values_and_links_only_its_voice_owner(self):
        selected = self.snapshot(pid='P-A')['selected']
        self.assertEqual(selected['kind'], 'patient')
        self.assertEqual(selected['owner_actor_id'], 'original-crew')
        self.assertEqual(selected['owner_actor_source'], 'linked_voice')
        self.assertEqual(selected['voice_session_id'], 'VS-APPLIED')
        self.assertEqual(selected['patient']['vitals'], {'hr': 92, 'sbp': 120, 'dbp': 80, 'spo2': 98})
        self.assertEqual(selected['inquiries'][0]['state'], 'ACCEPTED', 'An expired timestamp is not an expiry mutation')
        self.assertEqual(selected['workflow']['initial']['state'], 'RUNNING')

    def test_unregistered_draft_is_selectable_without_transcripts_evidence_or_any_patient_data(self):
        value = self.snapshot(sid='VS-DRAFT')
        self.assertEqual(value['selected']['kind'], 'draft')
        self.assertEqual(value['selected']['draft'], dict(id='VS-DRAFT', actor_id='draft-crew', version=3,
            age=32, sex='female', chief_complaint='respiratory', updated_at=NOW-10))
        self.assertIsNone(value['selected']['patient'])
        self.assertEqual(value['selected']['messages'], [])
        self.assertNotIn(PRIVATE, json.dumps(value, ensure_ascii=False))

    def test_patient_creation_actor_fallback_never_uses_the_later_hospital_actor(self):
        selected = self.snapshot(pid='P-B')['selected']
        self.assertEqual(selected['owner_actor_id'], 'creator-P-B')
        self.assertEqual(selected['owner_actor_source'], 'patient_created_event')
        self.assertIsNone(selected['voice_session_id'])
        self.assertNotEqual(selected['owner_actor_id'], 'hospital-actor')

    def test_all_tables_schema_and_database_bytes_are_unchanged_by_repeated_snapshots(self):
        logical, content = self.logical(), self.path.read_bytes()
        for selection in ({}, {'pid': 'P-A'}, {'sid': 'VS-DRAFT'}, {'pid': 'P-B'}):
            self.snapshot(**selection)
        self.assertEqual(self.logical(), logical)
        self.assertEqual(self.path.read_bytes(), content)

    def test_observation_never_creates_a_missing_conversation_or_expires_an_inquiry(self):
        self.conn.execute("DELETE FROM chat_messages WHERE conversation_id=(SELECT id FROM conversations WHERE patient_id='P-B')")
        self.conn.execute("DELETE FROM conversation_hospital_sessions WHERE conversation_id=(SELECT id FROM conversations WHERE patient_id='P-B')")
        self.conn.execute("DELETE FROM conversations WHERE patient_id='P-B'")
        before = self.logical()
        self.assertEqual(self.snapshot(pid='P-B')['selected']['messages'], [])
        self.assertEqual(self.logical(), before)
        self.assertEqual(self.conn.execute('SELECT state FROM inquiries').fetchone()[0], 'ACCEPTED')

    def test_business_helpers_and_db_connect_cannot_be_called_by_observation(self):
        from server import service, conversations
        with patch.object(db, 'connect', side_effect=AssertionError('No business DB connection')):
            with patch.object(service, 'patient_view', side_effect=AssertionError('No business GET')):
                with patch.object(conversations, 'get_conversation', side_effect=AssertionError('No room/expiry helper')):
                    self.assertEqual(self.snapshot(pid='P-A')['selected']['patient']['id'], 'P-A')

    def test_read_uri_and_query_only_enforcement_reject_writes_and_execute_one_read_transaction(self):
        real_connect, seen, trace = sqlite3.connect, [], []
        def connect(*args, **kwargs):
            self.assertTrue(args[0].endswith('?mode=ro'))
            self.assertIs(kwargs['uri'], True)
            conn = real_connect(*args, **kwargs)
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute('CREATE TABLE must_not_exist(value TEXT)')
            conn.set_trace_callback(trace.append)
            seen.append(conn)
            return conn
        with patch.object(console.sqlite3, 'connect', connect):
            self.snapshot(pid='P-A')
        self.assertEqual(len(seen), 1)
        self.assertEqual(sum(sql == 'BEGIN' for sql in trace), 1)
        self.assertEqual(sum(sql == 'ROLLBACK' for sql in trace), 1)
        self.assertIn('PRAGMA query_only=ON', trace)
        self.assertFalse(any(sql.lstrip().upper().startswith(('UPDATE ', 'INSERT ', 'DELETE ', 'CREATE ', 'ALTER ')) for sql in trace))

    def test_selected_patient_messages_and_events_do_not_leak_other_patient_or_raw_event_data(self):
        value = self.snapshot(pid='P-A')['selected']
        self.assertEqual([row['body'] for row in value['messages']], ['환자 A 전달 내용'])
        self.assertNotIn('B_MESSAGE_SECRET', json.dumps(value, ensure_ascii=False))
        self.assertNotIn(PRIVATE, json.dumps(value, ensure_ascii=False))
        self.assertTrue(all('data' not in event and 'actor' not in event for event in value['events']))
        self.assertEqual(value['hospital_sessions'][0]['hospital_name'], '가상 H1')

    def test_recent_history_is_bounded_to_eighty_and_displayed_in_stable_order(self):
        cid = self.conn.execute("SELECT id FROM conversations WHERE patient_id='P-A'").fetchone()[0]
        for n in range(100):
            self.conn.execute('INSERT INTO events(at,actor,patient_id,type) VALUES(?,?,?,?)', (n, 'demo', 'P-A', 'other'))
            self.conn.execute('INSERT INTO chat_messages(conversation_id,sender_role,sender_name,body,created_at,'
                'client_message_id) VALUES(?,?,?,?,?,?)', (cid, 'patient', 'demo', str(n), n, 'extra-' + str(n)))
        selected = self.snapshot(pid='P-A')['selected']
        self.assertEqual(len(selected['events']), 80)
        self.assertEqual(len(selected['messages']), 80)
        self.assertEqual(selected['events'], sorted(selected['events'], key=lambda e: e['id']))
        self.assertEqual(selected['messages'][0]['body'], '20')
        self.assertTrue(all(e['summary'] == '업무 기록' for e in selected['events']))

    def test_latest_lists_are_bounded_and_a_older_explicit_selection_is_still_available(self):
        for n in range(101):
            self.conn.execute('INSERT INTO voice_sessions(id,actor_id,version,fields,created_at,updated_at) VALUES(?,?,?,?,?,?)',
                              (f'VS-{n:03}', 'demo', 1, '{}', NOW, NOW+n))
            self.conn.execute('INSERT INTO patients(id,reporter_role,requested_at,state) VALUES(?,?,?,?)',
                              (f'P-{n:03}', 'transporter', NOW+n, 'WAITING'))
        result = self.snapshot(sid='VS-DRAFT')
        self.assertEqual(len(result['patients']), 100)
        self.assertEqual(len(result['drafts']), 100)
        self.assertEqual(result['selected']['draft']['id'], 'VS-DRAFT')

    def test_submitted_voice_session_follows_its_exact_registered_patient_without_a_draft_choice(self):
        before, content = self.logical(), self.path.read_bytes()
        selected = self.snapshot(sid='VS-APPLIED')['selected']
        self.assertEqual(selected['kind'], 'patient')
        self.assertEqual(selected['patient']['id'], 'P-A')
        self.assertIsNone(selected['draft'])
        self.assertEqual(selected['voice_session_id'], 'VS-APPLIED')
        self.assertEqual(selected['owner_actor_id'], 'original-crew')
        self.assertEqual([item['body'] for item in selected['messages']], ['환자 A 전달 내용'])
        self.assertNotIn('B_MESSAGE_SECRET', json.dumps(selected, ensure_ascii=False))
        self.assertEqual(self.logical(), before)
        self.assertEqual(self.path.read_bytes(), content)

    def test_unlinked_submitted_or_patient_linked_draft_is_not_an_unregistered_choice(self):
        self.conn.execute("UPDATE voice_sessions SET state='SUBMITTED' WHERE id='VS-DRAFT'")
        self.error(404, 'NOT_FOUND', self.snapshot, sid='VS-DRAFT')
        self.assertEqual(self.snapshot()['drafts'], [])
        self.conn.execute("UPDATE voice_sessions SET state='DRAFT',patient_id='P-B' WHERE id='VS-DRAFT'")
        self.error(404, 'NOT_FOUND', self.snapshot, sid='VS-DRAFT')
        self.assertEqual(self.snapshot()['drafts'], [])

    def test_same_selected_sid_transitions_to_its_registered_patient_and_preserves_other_scope(self):
        self.assertEqual(self.snapshot(sid='VS-DRAFT')['selected']['kind'], 'draft')
        self.conn.execute("UPDATE voice_sessions SET state='SUBMITTED',patient_id='P-B',submitted_at=?,version=version+1 WHERE id='VS-DRAFT'", (NOW,))
        before = self.logical()
        result = self.snapshot(sid='VS-DRAFT')
        self.assertEqual(result['selected']['kind'], 'patient')
        self.assertEqual(result['selected']['patient']['id'], 'P-B')
        self.assertEqual(result['selected']['owner_actor_id'], 'draft-crew')
        self.assertEqual(result['selected']['voice_session_id'], 'VS-DRAFT')
        self.assertEqual([item['body'] for item in result['selected']['messages']], ['B_MESSAGE_SECRET'])
        self.assertNotIn('환자 A 전달 내용', json.dumps(result['selected'], ensure_ascii=False))
        self.assertEqual(result['drafts'], [])
        self.assertEqual(self.logical(), before)

    def test_common_route_follows_registration_on_the_same_sid_query_without_writes(self):
        class Parent:
            def _dispatch(self, method):
                return ('parent', method)
            def _send(self, status, body):
                return status, body
        handler = console_runtime.wrap_handler(Parent, SimpleNamespace(data_mode='synthetic', path=str(self.path)))()
        handler.path = '/api/flow-console?sid=VS-APPLIED'
        before = self.logical()
        status, body = handler._dispatch('GET')
        self.assertEqual(status, 200)
        self.assertEqual(body['selected']['kind'], 'patient')
        self.assertEqual(body['selected']['patient']['id'], 'P-A')
        self.assertEqual(self.logical(), before)

    def test_missing_selection_is_404_and_malformed_selection_is_400(self):
        for kwargs in ({'pid': 'MISSING'}, {'sid': 'VS-MISSING'}):
            self.error(404, 'NOT_FOUND', self.snapshot, **kwargs)
        for kwargs in ({'pid': 'P-A', 'sid': 'VS-DRAFT'}, {'pid': '../escape'}, {'sid': ''}, {'pid': True}):
            self.error(400, 'INVALID_QUERY', self.snapshot, **kwargs)

    def test_query_rejects_duplicates_unknown_keys_empty_values_and_percent_encoded_escapes(self):
        self.assertEqual(console.parse_query(''), {})
        self.assertEqual(console.parse_query('pid=P-A'), {'pid': 'P-A'})
        self.assertEqual(console.parse_query('sid=VS-DRAFT'), {'sid': 'VS-DRAFT'})
        for query in ('pid=P-A&pid=P-A', 'pid=P-A&sid=VS-DRAFT', 'actor_id=crew', 'hospital_id=H1',
                      'pid=', 'pid=%2E%2E%2Fsecret', 'pid=P-A&unknown=1', 'pid', 'sid=%FF'):
            self.error(400, 'INVALID_QUERY', console.parse_query, query)

    def test_public_mode_is_hidden_before_any_file_or_database_access(self):
        with patch.object(console.sqlite3, 'connect', side_effect=AssertionError('Public access forbidden')):
            self.error(404, 'FLOW_CONSOLE_UNAVAILABLE', console.snapshot, self.path, NOW, data_mode='public')

    def test_public_labelled_hospitals_fail_closed_even_if_a_caller_mislabels_mode(self):
        self.conn.execute("UPDATE hospitals SET source='public' WHERE id='H1'")
        self.error(403, 'SYNTHETIC_ONLY', self.snapshot)

    def test_non_synthetic_hospital_beyond_the_display_limit_still_blocks_observation(self):
        for n in range(100):
            self.conn.execute('INSERT INTO hospitals(id,name,lat,lon,source) VALUES(?,?,?,?,?)',
                              (f'H-LIMIT-{n:03}', '가상 추가 병원', 35.16, 126.85, 'synthetic'))
        self.conn.execute('INSERT INTO hospitals(id,name,lat,lon,source) VALUES(?,?,?,?,?)',
                          ('ZZZ-PUBLIC', '공개 기관', 35.16, 126.85, 'public'))
        self.assertNotIn('ZZZ-PUBLIC', [row[0] for row in self.conn.execute('SELECT id FROM hospitals ORDER BY id LIMIT 100')])
        before = self.logical()
        self.error(403, 'SYNTHETIC_ONLY', self.snapshot)
        self.assertEqual(self.logical(), before)

    def test_missing_database_or_missing_schema_is_503_without_creation_or_migration(self):
        missing = self.path.parent / 'missing.db'
        self.error(503, 'FLOW_CONSOLE_UNAVAILABLE', console.snapshot, missing, NOW)
        self.assertFalse(missing.exists())
        empty = self.path.parent / 'empty.db'
        with closing(sqlite3.connect(empty)) as conn:
            conn.execute('CREATE TABLE unrelated(value TEXT)')
        before = empty.read_bytes()
        self.error(503, 'FLOW_CONSOLE_UNAVAILABLE', console.snapshot, empty, NOW)
        self.assertEqual(empty.read_bytes(), before)

    def test_recommendation_staleness_is_derived_without_publishing_or_dumping_solver_data(self):
        revision = self.conn.execute('SELECT revision FROM planning_state').fetchone()[0]
        self.conn.execute('INSERT INTO planning_results VALUES(?,?,?,?,?)', ('P-A', revision, NOW-10, NOW+20,
            json.dumps({'status': 'ASSIGNED', 'hospital_id': 'H1', 'private': PRIVATE})))
        ready = self.snapshot(pid='P-A')['selected']['recommendation']
        self.assertEqual((ready['status'], ready['is_current'], ready['hospital_id']), ('READY', True, 'H1'))
        self.assertNotIn(PRIVATE, json.dumps(ready))
        self.conn.execute('UPDATE planning_state SET revision=revision+1')
        stale = self.snapshot(pid='P-A')['selected']['recommendation']
        self.assertEqual((stale['status'], stale['is_current'], stale['hospital_id']), ('STALE', False, None))

    def test_optional_planning_tables_may_be_absent_without_migration(self):
        self.conn.execute('DROP TABLE planning_results')
        self.assertIsNone(self.snapshot(pid='P-A')['selected']['recommendation'])

    def test_all_workflow_projections_use_actual_current_schema_columns(self):
        self.conn.execute('INSERT INTO transport_inquiry_workflows(patient_id,source_hospital_id,episode_start_event,'
            'enabled,state,started_at,deadline_at,next_evaluation_at,round_no,updated_at,version) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            ('P-A', 'H1', 1, 1, 'AWAITING_CHANGE', NOW-10, NOW+30, NOW+5, 2, NOW, 3))
        self.conn.execute('INSERT INTO transport_reviews(patient_id,enabled,state,hospital_id,updated_at,version,last_checked_at) '
            'VALUES(?,?,?,?,?,?,?)', ('P-A', 1, 'SEARCHING', 'H1', NOW, 4, NOW-1))
        before = self.logical()
        workflow = self.snapshot(pid='P-A')['selected']['workflow']
        self.assertEqual(workflow['initial'], dict(enabled=1, state='RUNNING', round_no=0, version=1, updated_at=NOW-10))
        self.assertEqual(workflow['alternative'], dict(enabled=1, state='AWAITING_CHANGE', round_no=2, version=3, updated_at=NOW))
        self.assertEqual(workflow['review'], dict(enabled=1, state='SEARCHING', hospital_id='H1', version=4, updated_at=NOW, last_checked_at=NOW-1))
        self.assertEqual(self.logical(), before)

    def test_draft_and_vitals_projection_preserve_zero_and_never_invent_missing_observations(self):
        self.conn.execute("UPDATE voice_sessions SET fields=? WHERE id='VS-DRAFT'", (json.dumps({'age': 0, 'sex': 'unknown'}),))
        draft = self.snapshot(sid='VS-DRAFT')['selected']['draft']
        self.assertEqual(draft['age'], 0)
        self.assertEqual(draft['sex'], 'unknown')
        self.assertIsNone(draft['chief_complaint'])
        selected = self.snapshot(pid='P-B')['selected']['patient']
        self.assertEqual(selected['vitals'], {})

    def test_minimal_common_wrapper_route_keeps_other_routes_and_methods_unchanged(self):
        class Parent:
            def _dispatch(self, method):
                return ('parent', method)
            def _send(self, status, body):
                return status, body
        handler = console_runtime.wrap_handler(Parent, SimpleNamespace(data_mode='synthetic', path=str(self.path)))()
        handler.path = '/api/flow-console?sid=VS-DRAFT'
        status, body = handler._dispatch('GET')
        self.assertEqual(status, 200)
        self.assertEqual(body['selected']['draft']['id'], 'VS-DRAFT')
        handler.path = '/api/flow-console?pid=P-A&pid=P-B'
        self.assertEqual(handler._dispatch('GET')[0], 400)
        handler.path = '/api/flow-console'
        self.assertEqual(handler._dispatch('POST'), ('parent', 'POST'))
        handler.path = '/api/unrelated'
        self.assertEqual(handler._dispatch('GET'), ('parent', 'GET'))
        with self.assertRaises(ValueError):
            console_runtime.wrap_handler(Parent, SimpleNamespace(data_mode='public', path=str(self.path)), data_mode='public')

    def test_standalone_install_wraps_one_started_loopback_runtime_exactly_once(self):
        class Parent:
            def _dispatch(self, method):
                return ('parent', method, self.path)
            def _send(self, status, body):
                return status, body
        owner = SimpleNamespace(path=str(self.path), host='127.0.0.1',
            http=SimpleNamespace(server_address=('127.0.0.1', 18035), RequestHandlerClass=Parent))
        before = self.logical()
        self.assertIs(console_runtime.install_console(owner), owner)
        wrapped = owner.http.RequestHandlerClass
        self.assertIs(console_runtime.install_console(owner).http.RequestHandlerClass, wrapped)
        self.assertIs(console_runtime.wrap_handler(wrapped, owner), wrapped)
        handler = wrapped()
        handler.path = '/api/flow-console?pid=P-A'
        status, value = handler._dispatch('GET')
        self.assertEqual(status, 200)
        self.assertEqual(value['selected']['patient']['id'], 'P-A')
        for path in ('/api/flow-console', '/api/flow-console/other', '/api/team-runtime', '/paramedic/'):
            handler.path = path
            for method in ('POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS', 'HEAD'):
                self.assertEqual(handler._dispatch(method), ('parent', method, path))
            if path != '/api/flow-console':
                self.assertEqual(handler._dispatch('GET'), ('parent', 'GET', path))
        self.assertEqual(self.logical(), before)

    def test_standalone_install_rejects_public_unstarted_nonloopback_or_rebound_database(self):
        class Parent:
            def _dispatch(self, method):
                return method
        def owner(**kwargs):
            return SimpleNamespace(path=str(self.path), host='127.0.0.1',
                http=SimpleNamespace(server_address=('127.0.0.1', 18035), RequestHandlerClass=Parent), **kwargs)
        for runtime in (owner(data_mode='public'), SimpleNamespace(path=str(self.path), http=None),
                        SimpleNamespace(path=str(self.path), host='0.0.0.0', http=SimpleNamespace(server_address=('0.0.0.0', 18035), RequestHandlerClass=Parent)),
                        SimpleNamespace(path=str(self.path), host='127.0.0.1', http=SimpleNamespace(server_address=('0.0.0.0', 18035), RequestHandlerClass=Parent))):
            with self.assertRaises(ValueError):
                console_runtime.install_console(runtime)
        first = owner()
        console_runtime.install_console(first)
        with self.assertRaises(ValueError):
            console_runtime.wrap_handler(first.http.RequestHandlerClass, SimpleNamespace(path=str(self.path.parent/'different.db')))

    def test_wrapper_blocks_public_mode_changed_after_install_and_absolute_or_fragment_query(self):
        class Parent:
            def _dispatch(self, method):
                return method
            def _send(self, status, body):
                return status, body
        owner = SimpleNamespace(path=str(self.path), data_mode='synthetic')
        handler = console_runtime.wrap_handler(Parent, owner)()
        for path in ('http://outside.example/api/flow-console?pid=P-A', '/api/flow-console?pid=P-A#fragment'):
            handler.path = path
            self.assertEqual(handler._dispatch('GET')[0], 400)
        owner.data_mode = 'public'
        handler.path = '/api/flow-console?pid=P-A'
        with patch.object(console, 'snapshot', side_effect=AssertionError('Public DB must not be read')):
            self.assertEqual(handler._dispatch('GET')[0], 404)

    def test_cli_parser_requires_an_explicit_db_and_never_accepts_nonloopback_bind_options(self):
        parser = console_runtime.argument_parser()
        parsed = parser.parse_args(['--db', str(self.path), '--port', '0', '--mode', 'voice-only'])
        self.assertEqual(parsed.port, 0)
        self.assertEqual(parsed.mode, 'voice-only')
        self.assertEqual(parser.parse_args(['--db', str(self.path), '--mode', 'full']).mode, 'full')
        for argv in ([], ['--db', str(self.path), '--port', '-1'], ['--db', str(self.path), '--port', '65536'],
                     ['--db', str(self.path), '--port', '1.5'], ['--db', str(self.path), '--host', '0.0.0.0'],
                     ['--db', str(self.path), '--mode', 'public']):
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                parser.parse_args(argv)

    def test_cli_mock_runtime_prints_url_and_writes_fresh_run_receipts_without_server_start(self):
        class Parent:
            def _dispatch(self, method):
                return method
        run = self.path.parent / 'owned-cli-run'
        instances = []
        class Thread:
            def is_alive(self):
                return True
            def join(self, timeout):
                raise AssertionError('stop request must be checked before joining')
        class Runtime:
            def __init__(self, path, **options):
                self.path, self.options, self.host = path, options, options['host']
                self.http = SimpleNamespace(server_address=('127.0.0.1', 18035), RequestHandlerClass=Parent)
                self.thread, self.base_url, self.closed = Thread(), 'http://127.0.0.1:18035', False
                instances.append(self)
            def start(self):
                (run/'stop-requested.json').write_text('{}', encoding='utf-8')
                return self
            def close(self):
                self.closed = True
        before = self.logical()
        with patch('server.team_runtime.CommonRuntime', Runtime), redirect_stdout(io.StringIO()) as output:
            result = console_runtime.main(['--db', str(self.path), '--mode', 'voice-only', '--run-dir', str(run)])
        self.assertEqual(result, 0)
        self.assertIn('http://127.0.0.1:18035/flow-console.html', output.getvalue())
        self.assertEqual(instances[0].options, dict(port=18035, mode='voice-only', host='127.0.0.1'))
        self.assertTrue(instances[0].closed)
        ready = json.loads((run/'ready.json').read_text(encoding='utf-8'))
        stopped = json.loads((run/'stopped.json').read_text(encoding='utf-8'))
        self.assertEqual(ready['console_url'], 'http://127.0.0.1:18035/flow-console.html')
        self.assertTrue(stopped['started'])
        self.assertTrue(stopped['graceful'])
        self.assertEqual(stopped['reason'], 'stop_requested')
        self.assertEqual(self.logical(), before)

    def test_cli_existing_run_directory_is_not_written_or_used_to_start_a_runtime(self):
        run = self.path.parent/'existing-run'
        run.mkdir()
        (run/'keep.txt').write_text('preserve existing run', encoding='utf-8')
        with patch('server.team_runtime.CommonRuntime', side_effect=AssertionError('No startup')), redirect_stdout(io.StringIO()):
            self.assertEqual(console_runtime.main(['--db', str(self.path), '--run-dir', str(run)]), 1)
        self.assertEqual([p.name for p in run.iterdir()], ['keep.txt'])

    def test_cli_failed_start_closes_only_its_mock_runtime_and_reports_no_success(self):
        run = self.path.parent/'failed-cli-run'
        instances = []
        class Runtime:
            def __init__(self, path, **kwargs):
                self.closed = False
                instances.append(self)
            def start(self):
                raise ValueError('Unrelated DB is rejected by the base runtime')
            def close(self):
                self.closed = True
        with patch('server.team_runtime.CommonRuntime', Runtime), redirect_stdout(io.StringIO()):
            self.assertEqual(console_runtime.main(['--db', str(self.path), '--run-dir', str(run)]), 1)
        self.assertTrue(instances[0].closed)
        self.assertFalse((run/'ready.json').exists())
        stopped = json.loads((run/'stopped.json').read_text(encoding='utf-8'))
        self.assertFalse(stopped['started'])
        self.assertFalse(stopped['graceful'])


if __name__ == '__main__':
    unittest.main()
