"""이송 건별 대화: 업무 사건과 자유메시지, 병원 참여 구간을 함께 조회한다.

역할은 호출 경로로 결정한다. 자유문장은 수용/확정/의료정보를 변경하지 않는다.
병원 ID만 사용하는 기존 시연 서버의 계약이며 사용자 인증을 대신하지 않는다.
"""
import re

from . import db, service

POLL_INTERVAL = 3
MAX_BODY = 2000
MAX_NAME = 80
MAX_PAGE = 200
MAX_ID = (1 << 63) - 1


def _integer(value, name, minimum=0, maximum=None):
    maximum = MAX_ID if maximum is None else maximum
    if isinstance(value, str) and len(value) <= 19 and re.fullmatch(r'[0-9]+', value):
        value = int(value)
    service._require(type(value) is int and value >= minimum and
                     value <= maximum,
                     'INVALID', f'{name} 값이 올바르지 않습니다')
    return value


def _sessions(conn, conversation_id, hospital_id=None):
    sql = ('SELECT s.*, h.name AS hospital_name FROM conversation_hospital_sessions s '
           'JOIN hospitals h ON h.id=s.hospital_id WHERE s.conversation_id=?')
    args = [conversation_id]
    if hospital_id is not None:
        sql += ' AND s.hospital_id=?'
        args.append(hospital_id)
    return db.rows(conn.execute(sql + ' ORDER BY s.id', args))


def _current_session(conn, conversation_id):
    return db.row(conn.execute(
        'SELECT s.*, h.name AS hospital_name FROM conversation_hospital_sessions s '
        'JOIN hospitals h ON h.id=s.hospital_id '
        'WHERE s.conversation_id=? AND s.left_at IS NULL', (conversation_id,)).fetchone())


def _public_session(s):
    # 참가 당시 환자 스냅샷을 참여자 목록에 중복 노출하지 않는다.
    return {k: v for k, v in s.items() if k != 'patient_snapshot'}


def _event_item(event, hospital_name):
    typ, data = event['type'], event['data']
    name = hospital_name or '병원'
    policy_label = data.get('policy_label', '기본 문의 정책')
    policy_reasons = {
        'fixed_wave': '정해진 문의 규모에 따라 다음 후보를 선택했습니다.',
        'initial_best': '관측한 회신과 이동시간의 비용을 비교해 문의 후보를 선택했습니다.',
        'bounded_wait_expansion': '추가 문의 대기 상한이 지나 다음 후보에 문의합니다. 기존 문의도 유지합니다.',
        'wait_for_hedge': '현재 문의의 회신을 더 기다립니다.',
        'hedge_better_candidate': '기존 문의 대기와 새 후보를 비교해 추가 문의를 선택했습니다.',
        'pending_preferred': '기존 문의의 회신을 기다리는 편을 선택했습니다.',
        'concurrency_cap': '정책상 대기 중인 문의 수 한도로 추가 문의를 보류했습니다.',
        'no_candidates': '현재 새로 문의할 후보가 없습니다.',
        'contact_budget': '추가 문의 병원 수 한도에 도달했습니다.',
    }
    messages = {
        'patient_created': '환자정보가 등록됐습니다.',
        'patient_updated': '환자정보가 수정됐습니다.',
        'inquiry_sent': f'{name}에 수용 문의를 등록했습니다.',
        'inquiry_retried': f'{name}에 다시 문의했습니다.',
        'inquiry_cancelled': f'{name} 문의가 취소됐습니다.',
        'inquiry_closed': f'{name} 문의가 종료됐습니다.',
        'inquiry_expired': f'{name}의 수용 응답이 만료됐습니다.',
        'inquiry_workflow_started': f'{policy_label}으로 서버 자동 문의를 시작했습니다. 후보를 확인하고 있습니다.',
        'inquiry_workflow_paused': '서버 자동 문의를 일시정지했습니다. 이미 보낸 문의는 유지됩니다.',
        'inquiry_workflow_batch': f"{policy_label} · 후보 {len(data.get('hospital_ids', []))}곳에 문의했습니다. 회신을 확인하고 있습니다.",
        'inquiry_policy_changed': f'{policy_label}으로 문의 정책 설정을 변경했습니다.',
        'inquiry_policy_decided': f"{policy_label} · {policy_reasons.get(data.get('reason'), '다음 문의를 재검토했습니다.')}",
        'inquiry_workflow_ready': '유효한 수용 응답이 있습니다. 담당자의 목적지 확정을 기다립니다.',
        'inquiry_workflow_waiting': data.get('note') or '새 후보가 없어 기존 문의의 회신을 확인하고 있습니다.',
        'inquiry_workflow_exhausted': data.get('note') or '자동 문의가 종료됐습니다. 담당자의 확인이 필요합니다.',
        'inquiry_question': f'{name}에서 추가 정보를 요청했습니다.',
        'inquiry_answered': f'{name}의 질문에 답변이 등록됐습니다.',
        'destination_confirmed': f'{name}으로 목적지가 확정됐습니다. 병원이 대화에 참여했습니다.',
        'departed': f'{name}으로 출발했습니다.',
        'adjustment_needed': f'{name}에서 조정을 요청했습니다. 대체 병원 확정 전까지 현재 목적지를 유지합니다.',
        'hospital_keeps': f'{name}에서 기존 수용을 유지하기로 했습니다.',
        'transport_monitor_started': '이송 중 수용 조건 감시를 시작했습니다. 목적지는 유지합니다.',
        'transport_monitor_paused': '이송 중 수용 조건 감시와 대체병원 자동 문의를 일시정지했습니다.',
        'transport_baseline_captured': f'{name}의 수용 당시 조건을 저장했습니다.',
        'transport_recheck_required': f'{name}의 변경된 조건이 이 환자에게 영향을 줄 수 있습니다. 병원의 수용 유지 여부를 재확인합니다.',
        'transport_conditions_restored': f'{name}의 조건에서 확인된 위험 사유가 해소됐습니다.',
        'transport_recheck_acknowledged': f'{name}에서 현재 조건을 확인하고 기존 수용을 유지했습니다.',
        'transport_alternative_requested': f'{name}의 조정 요청에 따라 대체병원 탐색을 시작합니다. 현재 목적지는 유지합니다.',
        'transport_inquiry_started': '대체병원 자동 문의를 시작했습니다. 새 목적지는 사람이 확정합니다.',
        'transport_inquiry_decision': '현재 위치와 병원 회신을 바탕으로 다음 대체 문의를 판단했습니다.',
        'transport_inquiry_batch': f"대체병원 후보 {len(data.get('hospital_ids', []))}곳에 문의했습니다.",
        'transport_inquiry_waiting': data.get('note') or '대체병원 회신을 기다립니다. 현재 목적지는 유지합니다.',
        'transport_inquiry_ready': '대체병원의 유효한 수용 응답을 확보했습니다. 목적지 변경은 담당자가 결정합니다.',
        'transport_inquiry_exhausted': data.get('note') or '대체병원 자동 탐색이 종료됐습니다. 담당자의 추가 판단이 필요합니다.',
        'transport_inquiry_stopped': data.get('reason') or '대체병원 자동 문의가 중지됐습니다.',
        'transport_monitor_completed': '이송 조건 감시를 종료했습니다.',
        'transport_position_updated': '이송자의 위치 관측이 갱신됐습니다.',
        'destination_changed': f'{name}으로 목적지가 변경됐습니다. 새 병원이 대화에 참여했습니다.',
        'patient_redirected_away': '목적지가 변경되어 이 병원의 대화 참여가 종료됐습니다.',
        'patient_cancelled': '이송이 취소되어 대화가 종료됐습니다.',
        'arrival_confirmed': f'{name} 도착이 확인되어 이송 대화가 종료됐습니다.',
        'no_show': '미도착 처리로 이송 대화가 종료됐습니다.',
        'discharged': '퇴실 처리가 완료됐습니다.',
    }
    if typ == 'inquiry_responded':
        action = data.get('action')
        text = {
            'checking': f'{name}에서 확인 중으로 응답했습니다.',
            'accept': f'{name}에서 수용 가능으로 응답했습니다. 목적지 확정 전입니다.',
            'decline': f'{name}에서 수용 불가로 응답했습니다.',
        }.get(action, f'{name}의 응답이 등록됐습니다.')
    else:
        text = messages.get(typ, '이송 관련 기록이 등록됐습니다.')
    return dict(id=f"event:{event['id']}", kind='system', event_id=event['id'],
                type=typ, sender_role='system', sender_name='시스템', at=event['at'], text=text,
                hospital_id=event['hospital_id'], hospital_name=hospital_name, data=data,
                order_key=[event['id'], 0, event['id']])


def _message_item(message):
    return dict(id=f"message:{message['id']}", kind='message', message_id=message['id'],
                conversation_id=message['conversation_id'],
                hospital_session_id=message['hospital_session_id'],
                sender_role=message['sender_role'], sender_name=message['sender_name'],
                body=message['body'], text=message['body'], at=message['created_at'],
                client_message_id=message['client_message_id'],
                order_key=[message['event_watermark'], 1, message['id']])


def get_conversation(conn, now, pid, hospital_id=None, event_cursor=0, message_cursor=0, limit=100):
    """두 커서로 누락 없이 폴링한다. 각 스트림당 limit개, 최대 2*limit개 반환."""
    event_cursor = _integer(event_cursor, 'event_cursor')
    message_cursor = _integer(message_cursor, 'message_cursor')
    limit = _integer(limit, 'limit', 1, MAX_PAGE)
    with db.transaction(conn):
        p = service._patient(conn, pid)
        if hospital_id is not None:
            service._hospital(conn, hospital_id)
        conversation = db.ensure_conversation(conn, p, now)
        cid = conversation['id']
        visible_sessions = _sessions(conn, cid, hospital_id)
        if hospital_id is not None:
            service._require(visible_sessions, 'NOT_PARTICIPANT',
                             '목적지 확정 후 참여한 병원만 대화를 조회할 수 있습니다', 403)
        else:
            # 기존 조회와 같은 만료 규칙. 읽음/확인 중 상태를 만들어 내지 않는다.
            service.expire_acceptances(conn, now)
        active = _current_session(conn, cid)
        owns_active = hospital_id is None or (active and active['hospital_id'] == hospital_id)

        event_sql = 'SELECT * FROM events WHERE patient_id=? AND id>?'
        event_args = [pid, event_cursor]
        message_sql = 'SELECT * FROM chat_messages WHERE conversation_id=? AND id>?'
        message_args = [cid, message_cursor]
        if hospital_id is not None:
            ranges = []
            for session in visible_sessions:
                ranges.append('(id>=? AND (? IS NULL OR id<=?))')
                event_args.extend([session['joined_event_id'], session['left_event_id'], session['left_event_id']])
            event_sql += ' AND (hospital_id IS NULL OR hospital_id=?) AND (' + ' OR '.join(ranges) + ')'
            # hospital_id placeholder precedes ranges in SQL.
            event_args.insert(2, hospital_id)
            session_ids = [s['id'] for s in visible_sessions]
            message_sql += ' AND hospital_session_id IN (' + ','.join('?' for _ in session_ids) + ')'
            message_args.extend(session_ids)
        events = db.rows(conn.execute(event_sql + ' ORDER BY id LIMIT ?', (*event_args, limit + 1)))
        messages = db.rows(conn.execute(message_sql + ' ORDER BY id LIMIT ?', (*message_args, limit + 1)))
        more = dict(events=len(events) > limit, messages=len(messages) > limit)
        events, messages = events[:limit], messages[:limit]
        names = {r['id']: r['name'] for r in conn.execute('SELECT id,name FROM hospitals')}
        items = [_event_item(e, names.get(e['hospital_id'])) for e in events]
        items.extend(_message_item(m) for m in messages)
        items.sort(key=lambda item: item['order_key'])
        if hospital_id is None:
            patient = service.patient_summary(p)
            inquiries = service.patient_view(conn, pid, now)['inquiries']
        elif owns_active:
            patient = service.patient_summary(p)
            inquiries = []
        else:
            # 이전 병원은 현재 환자 위치/상태나 새 병원 정보를 추가로 받지 않는다.
            patient = visible_sessions[-1]['patient_snapshot']
            inquiries = []
        return dict(conversation=conversation, patient=patient, inquiries=inquiries,
                    hospital_sessions=[_public_session(s) for s in visible_sessions],
                    active_hospital_session=_public_session(active) if active and owns_active else None,
                    handoff=dict(note=visible_sessions[-1]['patient_snapshot'].get('handoff_note'),
                                 joined_at=visible_sessions[-1]['joined_at']) if visible_sessions else None,
                    can_send=bool(conversation['state'] == 'OPEN' and owns_active),
                    items=items, cursors=dict(event_id=events[-1]['id'] if events else event_cursor,
                                              message_id=messages[-1]['id'] if messages else message_cursor),
                    has_more=any(more.values()), more=more, poll_interval_seconds=POLL_INTERVAL)


def send_message(conn, now, pid, payload, hospital_id=None):
    """메시지와 참가 구간을 같은 쓰기 잠금 안에서 검사해 목적지 변경과 경합하지 않는다."""
    service._require(isinstance(payload, dict), 'INVALID', 'JSON 객체가 필요합니다')
    body, client_id = payload.get('body'), payload.get('client_message_id')
    service._require(isinstance(body, str) and 0 < len(body.strip()) <= MAX_BODY and len(body) <= MAX_BODY
                     and not any(ord(c) < 32 and c not in '\n\r\t' for c in body),
                     'INVALID', f'메시지는 빈 내용 없이 {MAX_BODY}자 이내로 입력하세요')
    service._require(isinstance(client_id, str) and
                     re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', client_id) is not None,
                     'INVALID', 'client_message_id는 1~128자의 고유 영문·숫자 식별자입니다')
    service._require('hospital_session_id' in payload, 'INVALID', 'hospital_session_id를 명시하세요')
    session_id = payload['hospital_session_id']
    if session_id is not None:
        session_id = _integer(session_id, 'hospital_session_id', 1)
    supplied_name = payload.get('sender_name')
    service._require(supplied_name is None or (isinstance(supplied_name, str) and
                     0 < len(supplied_name.strip()) <= MAX_NAME and len(supplied_name) <= MAX_NAME
                     and not any(ord(c) < 32 for c in supplied_name)),
                     'INVALID', f'표시 이름은 {MAX_NAME}자 이내로 입력하세요')
    with db.transaction(conn):
        p = service._patient(conn, pid)
        h = service._hospital(conn, hospital_id) if hospital_id is not None else None
        conversation = db.ensure_conversation(conn, p, now)
        cid = conversation['id']
        active = _current_session(conn, cid)
        role = 'hospital' if hospital_id is not None else 'patient'
        name = supplied_name or (h['name'] if h else {'self': '환자', 'guardian': '보호자',
                                                     'transporter': '이송자'}.get(p['reporter_role'], '이송자'))
        prior = db.row(conn.execute('SELECT * FROM chat_messages WHERE conversation_id=? '
                                    'AND sender_role=? AND client_message_id=?',
                                    (cid, role, client_id)).fetchone())
        if prior:
            # 참여 종료 뒤에도 이미 저장한 자기 메시지의 재전송은 확인해 준다.
            # 새 병원이 같은 키를 보내 다른 병원의 메시지를 얻을 수는 없다.
            if hospital_id is not None:
                own_prior = conn.execute('SELECT 1 FROM conversation_hospital_sessions '
                                         'WHERE id=? AND conversation_id=? AND hospital_id=?',
                                         (prior['hospital_session_id'], cid, hospital_id)).fetchone()
                service._require(own_prior, 'NOT_PARTICIPANT', '다른 병원 메시지는 조회할 수 없습니다', 403)
            service._require(prior['body'] == body and (supplied_name is None or prior['sender_name'] == supplied_name) and
                             prior['hospital_session_id'] == session_id,
                             'MESSAGE_CONFLICT', '같은 메시지 ID에 다른 내용이나 참여 구간이 있습니다', 409)
            return dict(message=_message_item(prior), duplicate=True)
        if hospital_id is not None:
            service._require(active and active['hospital_id'] == hospital_id,
                             'NOT_PARTICIPANT', '현재 참여 중인 병원만 메시지를 보낼 수 있습니다', 403)
        service._require(conversation['state'] == 'OPEN', 'CONVERSATION_CLOSED',
                         '종료된 이송 대화에는 새 메시지를 보낼 수 없습니다', 409)
        expected_session = active['id'] if active else None
        service._require(session_id == expected_session, 'STALE_SESSION',
                         '참여 병원이 바뀌었습니다. 대화를 다시 조회한 뒤 전송하세요', 409,
                         current_hospital_session_id=expected_session)
        watermark = conn.execute('SELECT COALESCE(MAX(id),0) FROM events WHERE patient_id=?', (pid,)).fetchone()[0]
        cur = conn.execute('INSERT INTO chat_messages '
                           '(conversation_id,hospital_session_id,sender_role,sender_name,body,created_at,'
                           'client_message_id,event_watermark) VALUES (?,?,?,?,?,?,?,?)',
                           (cid, session_id, role, name, body, now, client_id, watermark))
        message = db.row(conn.execute('SELECT * FROM chat_messages WHERE id=?', (cur.lastrowid,)).fetchone())
    return dict(message=_message_item(message), duplicate=False)
