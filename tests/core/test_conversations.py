"""환자별 이송 대화방의 업무 상태, 병원별 열람 범위와 재전송 안전성.

실행: python scripts/test_suites.py python test_conversations
실제 의료·지도 API나 운영 DB를 사용하지 않는다.
"""
import json
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager
from http.server import ThreadingHTTPServer

from server import conversations, db, service
from server.app import make_handler
from server.db import transaction
from server.service import ServiceError
from test_server import Base, EST, T0


class ConversationBase(Base):
    def view(self, pid='A', hid=None, now=T0 + 100, **kw):
        return conversations.get_conversation(self.conn, now, pid, hospital_id=hid, **kw)

    def send(self, body='환자 상태 전달', key='m1', session=None, pid='A', hid=None,
             now=T0 + 100, **extra):
        payload = dict(body=body, client_message_id=key, hospital_session_id=session, **extra)
        return conversations.send_message(self.conn, now, pid, payload, hospital_id=hid)

    def status_error(self, status, fn, *args, **kwargs):
        with self.assertRaises(ServiceError) as cm:
            fn(*args, **kwargs)
        self.assertEqual(cm.exception.status, status)
        return cm.exception

    def confirm(self, hid='H1', pid='A', now=T0 + 20):
        iid = self.accepted(pid, hid, now=now - 10)
        service.confirm_destination(self.conn, now, pid, iid)
        return self.view(pid, now=now)['active_hospital_session']['id']

    def reassign(self, old='H1', new='H2', pid='A', now=T0 + 50):
        service.hospital_decision(self.conn, now, pid, old, 'adjust', '치료 자원 재확인 필요')
        iid = self.accepted(pid, new, now=now + 1)
        service.change_destination(self.conn, now + 10, pid, iid, EST)
        return self.view(pid, now=now + 10)['active_hospital_session']['id']

    @staticmethod
    def message_bodies(view):
        return [item['body'] for item in view['items'] if item['kind'] == 'message']

    @contextmanager
    def http(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.path, EST, clock=lambda: T0 + 100))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'

        def call(method, path, body=None):
            req = urllib.request.Request(base + path, method=method,
                                         data=None if body is None else json.dumps(body).encode('utf-8'),
                                         headers={'Content-Type': 'application/json'})
            try:
                with urllib.request.urlopen(req, timeout=10) as response:
                    return response.status, json.load(response)
            except urllib.error.HTTPError as exc:
                with exc:
                    return exc.code, json.load(exc)

        try:
            yield call
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


class ConversationLifecycle(ConversationBase):
    def test_creation_and_acceptance_do_not_admit_hospital(self):
        self.patient()
        view = self.view()
        room_id = view['conversation']['id']
        self.assertEqual((view['conversation']['patient_id'], view['conversation']['state']), ('A', 'OPEN'))
        self.assertIsNone(view['active_hospital_session'])
        self.assertEqual(view['hospital_sessions'], [])
        self.assertTrue(view['can_send'])
        self.assertTrue(any(item['type'] == 'patient_created' for item in view['items']))
        self.accepted('A', 'H1')
        self.assertIsNone(self.view()['active_hospital_session'])
        with self.assertRaises(ServiceError) as cm:
            self.view(hid='H1')
        self.assertIn(cm.exception.status, (403, 404))
        session = self.confirm()
        self.assertEqual(self.view()['conversation']['id'], room_id)
        joined = self.view(hid='H1')
        self.assertTrue(joined['can_send'])
        self.assertEqual(joined['active_hospital_session']['id'], session)
        self.assertEqual([s['hospital_id'] for s in joined['hospital_sessions']], ['H1'])

    def test_failed_confirmation_does_not_join_hospital(self):
        self.patient()
        service.send_inquiries(self.conn, T0 + 1, 'A', ['H1'])
        iid = self.inquiry_id('A', 'H1')
        self.status_error(409, service.confirm_destination, self.conn, T0 + 2, 'A', iid)
        self.assertEqual(self.view()['hospital_sessions'], [])
        self.assertIsNone(self.view()['active_hospital_session'])

    def test_chat_does_not_modify_inquiry_state_or_planning_version(self):
        self.patient()
        service.send_inquiries(self.conn, T0 + 1, 'A', ['H1'])
        before = dict(self.conn.execute('SELECT version,state FROM patients WHERE id=?', ('A',)).fetchone())
        revision = self.conn.execute('SELECT revision FROM planning_state WHERE id=1').fetchone()[0]
        event_count = self.conn.execute('SELECT COUNT(*) FROM events').fetchone()[0]
        result = self.send(body='수용 가능합니다', sender_role='hospital')
        self.assertNotIn(result['message']['sender_role'], ('hospital', 'system'))
        after = dict(self.conn.execute('SELECT version,state FROM patients WHERE id=?', ('A',)).fetchone())
        self.assertEqual(after, before)
        self.assertEqual(self.conn.execute('SELECT revision FROM planning_state WHERE id=1').fetchone()[0], revision)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM events').fetchone()[0], event_count)
        self.assertEqual(self.conn.execute('SELECT state FROM inquiries').fetchone()[0], 'PENDING')

    def test_structured_status_and_two_party_messages_are_available_together(self):
        self.patient()
        session = self.confirm()
        self.send('약 12분 후 도착합니다', 'transport', session)
        hospital = self.send('현재 의식 상태를 알려주세요', 'hospital', session, hid='H1', sender_role='system')
        self.assertEqual(hospital['message']['sender_role'], 'hospital')
        for hid in (None, 'H1'):
            view = self.view(hid=hid)
            self.assertEqual(set(self.message_bodies(view)), {'약 12분 후 도착합니다', '현재 의식 상태를 알려주세요'})
            self.assertTrue(any(item['kind'] == 'system' and item['type'] == 'destination_confirmed'
                                for item in view['items']))

    def test_terminal_states_close_room_and_block_new_messages(self):
        for terminal in ('arrival', 'cancel', 'no-show'):
            with self.subTest(terminal=terminal):
                pid = terminal
                self.patient(pid)
                session = self.confirm('H2', pid)
                if terminal == 'arrival':
                    service.confirm_arrival(self.conn, T0 + 30, pid, 'H2')
                elif terminal == 'no-show':
                    service.mark_no_show(self.conn, T0 + 30, pid, 'H2')
                else:
                    service.cancel_patient(self.conn, T0 + 30, pid, '증상 호전')
                view = self.view(pid)
                self.assertEqual(view['conversation']['state'], 'CLOSED')
                self.assertFalse(view['can_send'])
                self.status_error(409, self.send, pid=pid, session=session)
                self.status_error(403, self.send, pid=pid, session=session, hid='H2')
                self.assertFalse(self.view(pid, hid='H2')['can_send'])


class ConversationPrivacy(ConversationBase):
    def test_reassignment_keeps_room_but_scopes_each_hospital_history(self):
        self.patient()
        room_id = self.view()['conversation']['id']
        self.send('확정 전 비공개 대화', 'pre', now=T0 + 20)
        old_session = self.confirm(now=T0 + 20)
        self.send('첫 병원에게 보낸 내용', 'h1-p', old_session, now=T0 + 20)
        self.send('첫 병원의 답변', 'h1-h', old_session, hid='H1', now=T0 + 20)
        # 모든 시각이 같아도 참여 범위는 메시지/event ID 경계로 구별해야 한다.
        service.hospital_decision(self.conn, T0 + 20, 'A', 'H1', 'adjust', '대체병원 확인')
        service.send_inquiries(self.conn, T0 + 20, 'A', ['H2'])
        service.respond(self.conn, T0 + 20, self.inquiry_id('A', 'H2'), 'accept', 'nurse2')
        service.change_destination(self.conn, T0 + 20, 'A', self.inquiry_id('A', 'H2'), EST)
        new_session = self.view()['active_hospital_session']['id']
        self.assertNotEqual(old_session, new_session)
        self.send('새 병원에게 보낸 내용', 'h2-p', new_session, now=T0 + 20)
        self.send('새 병원의 답변', 'h2-h', new_session, hid='H2', now=T0 + 20)
        all_view, old, new = self.view(), self.view(hid='H1'), self.view(hid='H2')
        self.assertEqual(all_view['conversation']['id'], room_id)
        self.assertEqual(len(self.message_bodies(all_view)), 5)
        self.assertEqual(set(self.message_bodies(old)), {'첫 병원에게 보낸 내용', '첫 병원의 답변'})
        self.assertEqual(set(self.message_bodies(new)), {'새 병원에게 보낸 내용', '새 병원의 답변'})
        self.assertFalse(old['can_send'])
        self.assertTrue(new['can_send'])
        self.assertEqual({s['hospital_id'] for s in old['hospital_sessions']}, {'H1'})
        self.assertEqual({s['hospital_id'] for s in new['hospital_sessions']}, {'H2'})
        self.status_error(409, self.send, '이전 화면에서 전송', 'stale-mobile', old_session)
        self.status_error(403, self.send, '이전 병원 전송', 'stale-hospital', old_session, hid='H1')

    def test_unrelated_hospital_and_patient_cannot_access_other_case(self):
        self.patient('A')
        self.patient('B')
        session_a = self.confirm('H1', 'A')
        session_b = self.confirm('H2', 'B')
        self.send('A 환자만의 내용', 'same-key', session_a, pid='A')
        self.send('B 환자만의 내용', 'same-key', session_b, pid='B')
        self.assertEqual(self.message_bodies(self.view('A')), ['A 환자만의 내용'])
        self.assertEqual(self.message_bodies(self.view('B')), ['B 환자만의 내용'])
        with self.assertRaises(ServiceError) as cm:
            self.view('B', hid='H1')
        self.assertIn(cm.exception.status, (403, 404))
        self.status_error(409, self.send, '잘못된 환자 세션', 'cross-case', session_a, pid='B')
        self.assertEqual(self.message_bodies(self.view('B')), ['B 환자만의 내용'])

    def test_old_hospital_keeps_join_snapshot_and_excludes_later_case_events(self):
        self.patient(symptoms='최초 증상')
        self.confirm()
        snapshot = self.view(hid='H1')['patient']['symptoms']
        self.reassign()
        # 미래 개인정보가 보관된 이전 병원 열람에 따라붙지 않아야 한다.
        with transaction(self.conn):
            self.conn.execute("UPDATE patients SET symptoms='변경 이후 민감 정보' WHERE id='A'")
            service.log(self.conn, T0 + 100, 'hospital', 'hospital_keeps', 'A', 'H2', note='H2 전용 메모')
        old = self.view(hid='H1')
        self.assertEqual(old['patient']['symptoms'], snapshot)
        self.assertEqual(old['patient']['destination_hospital_id'], 'H1')
        self.assertNotIn('변경 이후 민감 정보', json.dumps(old, ensure_ascii=False))
        self.assertNotIn('H2 전용 메모', json.dumps(old, ensure_ascii=False))
        self.assertEqual(self.view(hid='H2')['patient']['symptoms'], '변경 이후 민감 정보')

    def test_hospital_waits_until_new_destination_commit_to_leave(self):
        self.patient()
        session = self.confirm()
        service.hospital_decision(self.conn, T0 + 30, 'A', 'H1', 'adjust', '자원 확인')
        service.send_inquiries(self.conn, T0 + 31, 'A', ['H2'])
        self.status_error(409, service.change_destination, self.conn, T0 + 32, 'A', self.inquiry_id('A', 'H2'), EST)
        self.assertEqual(self.view()['active_hospital_session']['id'], session)
        self.assertTrue(self.view(hid='H1')['can_send'])
        self.send('아직 기존 병원과 연락 가능', 'still-active', session, hid='H1')


class ConversationIdempotency(ConversationBase):
    def test_retry_returns_original_and_conflicting_reuse_is_rejected(self):
        self.patient()
        first = self.send('동일한 전달 내용', 'retry')
        retried = self.send('동일한 전달 내용', 'retry', now=T0 + 110)
        self.assertFalse(first['duplicate'])
        self.assertTrue(retried['duplicate'])
        self.assertEqual(retried['message'], first['message'])
        self.status_error(409, self.send, '바뀐 내용', 'retry')
        self.assertEqual(self.message_bodies(self.view()), ['동일한 전달 내용'])

    def test_patient_and_hospital_have_separate_idempotency_namespace(self):
        self.patient()
        session = self.confirm()
        patient = self.send('이송자 내용', 'shared-key', session)
        hospital = self.send('병원 내용', 'shared-key', session, hid='H1')
        self.assertNotEqual(patient['message']['id'], hospital['message']['id'])
        self.assertFalse(hospital['duplicate'])
        self.assertTrue(self.send('병원 내용', 'shared-key', session, hid='H1')['duplicate'])

    def test_reusing_key_for_different_session_is_rejected(self):
        self.patient()
        self.send('동일 내용', 'same-key')
        session = self.confirm()
        self.status_error(409, self.send, '동일 내용', 'same-key', session)

    def test_saved_hospital_message_retries_after_leaving_or_closing_without_leaking(self):
        for transition in ('reassign', 'arrival'):
            with self.subTest(transition=transition):
                pid = 'retry-' + transition
                self.patient(pid)
                old_session = self.confirm('H1', pid)
                saved = self.send('이미 저장된 병원 답변', 'hospital-retry', old_session, pid=pid, hid='H1')
                if transition == 'reassign':
                    new_session = self.reassign(pid=pid)
                    # 같은 키와 내용으로 요청해도 다른 병원의 과거 메시지는 반환하지 않는다.
                    for sid in (old_session, new_session):
                        self.status_error(403, self.send, '이미 저장된 병원 답변', 'hospital-retry', sid,
                                          pid=pid, hid='H2')
                else:
                    service.confirm_arrival(self.conn, T0 + 120, pid, 'H1')
                with transaction(self.conn):
                    self.conn.execute('UPDATE hospitals SET name=? WHERE id=?', ('이름 변경 ' + transition, 'H1'))
                counts = (self.conn.execute('SELECT COUNT(*) FROM chat_messages').fetchone()[0],
                          self.conn.execute('SELECT COUNT(*) FROM events').fetchone()[0],
                          self.conn.execute('SELECT revision FROM planning_state WHERE id=1').fetchone()[0])
                replay = self.send('이미 저장된 병원 답변', 'hospital-retry', old_session, pid=pid, hid='H1',
                                   now=T0 + 130)
                self.assertTrue(replay['duplicate'])
                self.assertEqual(replay['message'], saved['message'])
                self.status_error(409, self.send, '수정된 병원 답변', 'hospital-retry', old_session, pid=pid, hid='H1')
                self.status_error(409, self.send, '이미 저장된 병원 답변', 'hospital-retry', old_session,
                                  pid=pid, hid='H1', sender_name='다른 이름')
                explicit = self.send('이미 저장된 병원 답변', 'hospital-retry', old_session, pid=pid, hid='H1',
                                     sender_name=saved['message']['sender_name'])
                self.assertEqual(explicit['message'], saved['message'])
                self.assertEqual((self.conn.execute('SELECT COUNT(*) FROM chat_messages').fetchone()[0],
                                  self.conn.execute('SELECT COUNT(*) FROM events').fetchone()[0],
                                  self.conn.execute('SELECT revision FROM planning_state WHERE id=1').fetchone()[0]),
                                 counts)

    def test_rejoining_hospital_sees_only_its_sessions_and_old_session_cannot_send_new_message(self):
        self.patient()
        first = self.confirm('H1')
        self.send('첫 H1 구간', 'first-h1', first, hid='H1')
        middle = self.reassign()
        self.send('H2 전용 구간', 'middle-h2', middle, hid='H2')
        last = self.reassign(old='H2', new='H1', now=T0 + 70)
        self.send('두 번째 H1 구간', 'second-h1', last, hid='H1')
        h1, h2 = self.view(hid='H1'), self.view(hid='H2')
        self.assertNotEqual(first, last)
        self.assertEqual([s['id'] for s in h1['hospital_sessions']], [first, last])
        self.assertEqual(set(self.message_bodies(h1)), {'첫 H1 구간', '두 번째 H1 구간'})
        self.assertEqual(self.message_bodies(h2), ['H2 전용 구간'])
        self.assertTrue(h1['can_send'])
        self.assertFalse(h2['can_send'])
        self.status_error(409, self.send, '과거 화면에서 새 전송', 'stale-rejoin', first, hid='H1')
        self.assertTrue(self.send('첫 H1 구간', 'first-h1', first, hid='H1')['duplicate'])


class ConversationTimeline(ConversationBase):
    def test_same_epoch_messages_and_system_events_keep_actual_interleaving_order(self):
        self.patient()
        self.send('문의 전 메시지', 'before', now=T0)
        service.send_inquiries(self.conn, T0, 'A', ['H1'])
        self.send('문의 후 메시지', 'after-inquiry', now=T0)
        service.respond(self.conn, T0, self.inquiry_id('A', 'H1'), 'checking', 'nurse')
        self.send('응답 후 메시지', 'after-response', now=T0)
        items = self.view()['items']
        self.assertEqual({item['at'] for item in items}, {T0})
        self.assertEqual([item['order_key'] for item in items], sorted(item['order_key'] for item in items))
        sequence = [item['type'] if item['kind'] == 'system' else item['body'] for item in items]
        self.assertEqual(sequence, ['patient_created', '문의 전 메시지', 'inquiry_sent', '문의 후 메시지',
                                    'inquiry_responded', '응답 후 메시지'])

    def test_pagination_does_not_drop_events_or_messages_beyond_first_hundred(self):
        self.patient()
        with transaction(self.conn):
            for i in range(105):
                service.log(self.conn, T0, 'patient_web', 'inquiry_sent', 'A', 'H1', inquiry_id=i, attempt=1)
        for i in range(105):
            self.send(f'순서 {i}', f'page-{i}', now=T0)
        expected_events = self.conn.execute("SELECT COUNT(*) FROM events WHERE patient_id='A'").fetchone()[0]
        cursors, seen = dict(event_id=0, message_id=0), set()
        event_count = message_count = 0
        for _ in range(20):
            view = self.view(event_cursor=cursors['event_id'], message_cursor=cursors['message_id'], limit=37)
            self.assertTrue(all(item['id'].startswith(('event:', 'message:')) for item in view['items']))
            ids = {item['id'] for item in view['items']}
            self.assertEqual(len(ids), len(view['items']))
            self.assertTrue(seen.isdisjoint(ids))
            seen.update(ids)
            event_count += sum(item['kind'] == 'system' for item in view['items'])
            message_count += sum(item['kind'] == 'message' for item in view['items'])
            for key in ('event_id', 'message_id'):
                self.assertGreaterEqual(view['cursors'][key], cursors[key])
            cursors = view['cursors']
            self.assertEqual(view['has_more'], any(view['more'].values()))
            if not view['has_more']:
                break
            self.assertTrue(view['items'])
        else:
            self.fail('대화방 페이지 조회가 끝나지 않음')
        self.assertEqual((event_count, message_count), (expected_events, 105))
        self.assertEqual(self.view(event_cursor=cursors['event_id'], message_cursor=cursors['message_id'])['items'], [])

    def test_question_answer_history_is_immutable_when_latest_fields_are_overwritten(self):
        self.patient()
        service.send_inquiries(self.conn, T0 + 1, 'A', ['H1'])
        iid = self.inquiry_id('A', 'H1')
        service.respond(self.conn, T0 + 2, iid, 'question', 'nurse', question='첫 질문: 복용약?')
        service.answer_question(self.conn, T0 + 3, 'A', iid, '첫 답변: 없음')
        first = {item['id']: json.dumps(item, ensure_ascii=False, sort_keys=True)
                 for item in self.view()['items'] if item['type'] in ('inquiry_question', 'inquiry_answered')}
        self.assertEqual(len(first), 2)
        self.assertIn('첫 질문: 복용약?', ''.join(first.values()))
        self.assertIn('첫 답변: 없음', ''.join(first.values()))
        service.respond(self.conn, T0 + 4, iid, 'question', 'nurse', question='두 번째 질문: 알레르기?')
        service.answer_question(self.conn, T0 + 5, 'A', iid, '두 번째 답변: 모름')
        latest = {item['id']: json.dumps(item, ensure_ascii=False, sort_keys=True)
                  for item in self.view()['items'] if item['type'] in ('inquiry_question', 'inquiry_answered')}
        self.assertEqual(len(latest), 4)
        for item_id, original in first.items():
            self.assertEqual(latest[item_id], original)


class ConversationValidation(ConversationBase):
    def test_blank_oversized_or_nonstring_messages_are_rejected(self):
        self.patient()
        for body in ('', ' \n\t ', '가' * 2001, None, False, 5, ['내용']):
            with self.subTest(body=repr(body)[:50]):
                self.status_error(400, self.send, body=body)
        self.assertEqual(self.message_bodies(self.view()), [])

    def test_client_id_and_session_are_required_and_validated(self):
        self.patient()
        for key in ('', ' ', 'k' * 129, None, False, 123):
            with self.subTest(key=repr(key)[:40]):
                self.status_error(400, self.send, key=key)
        self.status_error(400, conversations.send_message, self.conn, T0, 'A',
                          {'body': '내용', 'client_message_id': 'no-session'})
        for bad_session in (True, False, -1, 'invalid'):
            with self.subTest(session=bad_session):
                with self.assertRaises(ServiceError) as cm:
                    self.send(session=bad_session)
                self.assertIn(cm.exception.status, (400, 409))
        self.assertEqual(self.message_bodies(self.view()), [])

    def test_invalid_cursor_and_page_size_do_not_silently_reset_history(self):
        self.patient()
        for name in ('event_cursor', 'message_cursor'):
            for value in (True, False, -1, 1.5, 'bad', None, 1 << 64, str(1 << 64)):
                with self.subTest(name=name, value=value):
                    self.status_error(400, self.view, **{name: value})
        for value in (True, False, 0, -1, 1.5, 'bad', None):
            with self.subTest(limit=value):
                self.status_error(400, self.view, limit=value)


class ConversationMigration(ConversationBase):
    def test_legacy_cases_backfill_without_rewriting_business_records(self):
        self.patient('A', symptoms='이전 DB 환자 A')
        self.confirm('H1', 'A')
        self.reassign(pid='A')
        self.patient('B', symptoms='이전 DB 환자 B')
        self.confirm('H3', 'B')
        service.cancel_patient(self.conn, T0 + 70, 'B', '증상 호전')
        before_patients = [tuple(r) for r in self.conn.execute('SELECT * FROM patients ORDER BY id')]
        before_events = [tuple(r) for r in self.conn.execute('SELECT * FROM events ORDER BY id')]
        # 새 대화 테이블이 없던 버전의 DB를 재현한다.
        self.conn.execute('DROP TABLE chat_messages')
        self.conn.execute('DROP TABLE conversation_hospital_sessions')
        self.conn.execute('DROP TABLE conversations')
        db.init(self.conn)
        old = self.view('A', hid='H1')
        self.assertFalse(old['can_send'])
        self.assertTrue(old['patient']['historical_snapshot_unavailable'])
        self.assertNotIn('이전 DB 환자 A', json.dumps(old, ensure_ascii=False))
        self.assertEqual(self.view('A', hid='H2')['patient']['symptoms'], '이전 DB 환자 A')
        self.assertEqual(self.view('B')['conversation']['state'], 'CLOSED')
        self.assertEqual([tuple(r) for r in self.conn.execute('SELECT * FROM patients ORDER BY id')], before_patients)
        self.assertEqual([tuple(r) for r in self.conn.execute('SELECT * FROM events ORDER BY id')], before_events)
        sessions = [tuple(r) for r in self.conn.execute('SELECT * FROM conversation_hospital_sessions ORDER BY id')]
        db.init(self.conn)
        self.assertEqual([tuple(r) for r in self.conn.execute('SELECT * FROM conversation_hospital_sessions ORDER BY id')],
                         sessions)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM conversations').fetchone()[0], 2)


class ConversationHttp(ConversationBase):
    def test_all_four_routes_and_query_validation(self):
        self.patient()
        session = self.confirm()
        with self.http() as call:
            patient_path = '/api/patients/A'
            hospital_path = '/api/hospitals/H1/patients/A'
            self.assertEqual(call('GET', patient_path + '/conversation')[0], 200)
            self.assertEqual(call('GET', hospital_path + '/conversation')[0], 200)
            p = {'body': '환자 앱 전송', 'client_message_id': 'http-p', 'hospital_session_id': session}
            h = {'body': '병원 웹 전송', 'client_message_id': 'http-h', 'hospital_session_id': session}
            self.assertEqual(call('POST', patient_path + '/messages', p)[0], 200)
            self.assertEqual(call('POST', hospital_path + '/messages', h)[0], 200)
            status, view = call('GET', patient_path + '/conversation?event_cursor=0&message_cursor=0&limit=1')
            self.assertEqual(status, 200)
            self.assertTrue(view['has_more'])
            for query in ('event_cursor=-1', 'event_cursor=true', 'message_cursor=x', 'limit=0'):
                self.assertEqual(call('GET', patient_path + '/conversation?' + query)[0], 400)
            self.assertEqual(call('GET', '/api/patients/NOPE/conversation')[0], 404)
            self.assertIn(call('GET', '/api/hospitals/H3/patients/A/conversation')[0], (403, 404))
            self.assertEqual(call('POST', patient_path + '/messages', dict(p, body='다른 내용'))[0], 409)

    def test_concurrent_http_retry_creates_exactly_one_message(self):
        self.patient()
        with self.http() as call:
            barrier = threading.Barrier(2)
            results = []
            failures = []
            payload = dict(body='중복 네트워크 재전송', client_message_id='concurrent', hospital_session_id=None)

            def send():
                try:
                    barrier.wait(timeout=5)
                    results.append(call('POST', '/api/patients/A/messages', payload))
                except Exception as exc:
                    failures.append(exc)

            threads = [threading.Thread(target=send) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=15)
            self.assertFalse(any(thread.is_alive() for thread in threads))
            self.assertEqual(failures, [])
            self.assertEqual([status for status, _ in results], [200, 200])
            self.assertEqual(sorted(result['duplicate'] for _, result in results), [False, True])
            self.assertEqual(results[0][1]['message'], results[1][1]['message'])
            self.assertEqual(self.message_bodies(call('GET', '/api/patients/A/conversation')[1]),
                             ['중복 네트워크 재전송'])


if __name__ == '__main__':
    import unittest
    unittest.main()
