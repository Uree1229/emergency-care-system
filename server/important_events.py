"""중요 업무 사건의 전달과 사용자 인지 기록.

GET은 전달·확인을 만들지 않는다. 클라이언트가 해당 사건을 받은 뒤 delivered를,
담당자가 확인한 뒤 acknowledge를 호출한다. 확인은 의료적 수용 유지나 목적지
변경을 뜻하지 않는다. actor_id는 현 시연 서버의 클라이언트 선언 식별자이며
로그인·사용자 인증을 대신하지 않는다.
"""
import re

from . import db, service

MAX_ID = (1 << 63) - 1
MAX_PAGE = 200
MAX_ACTOR = 80

SCHEMA = """
CREATE TABLE IF NOT EXISTS important_event_receipts (
    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK(role IN ('paramedic','hospital')),
    hospital_scope TEXT NOT NULL DEFAULT '',
    actor_id TEXT NOT NULL,
    delivered_at INTEGER NOT NULL,
    acknowledged_at INTEGER,
    PRIMARY KEY(event_id,role,hospital_scope,actor_id),
    CHECK((role='paramedic' AND hospital_scope='') OR
          (role='hospital' AND hospital_scope<>'')),
    CHECK(acknowledged_at IS NULL OR acknowledged_at>=delivered_at)
);
CREATE INDEX IF NOT EXISTS important_receipts_recipient
    ON important_event_receipts(role,hospital_scope,actor_id,event_id);
"""

SUMMARIES = {
    'patient_information_amended': '환자정보가 수정됐습니다. 새 문의의 최신 정보를 다시 검토해야 합니다.',
    'inquiry_cancelled': '이 병원에 보낸 문의가 취소됐습니다.',
    'inquiry_closed': '이 병원에 보낸 문의가 종료됐습니다.',
    'inquiry_expired': '이 병원의 수용 응답이 만료됐습니다.',
    'inquiry_question': '병원에서 추가 정보를 요청했습니다.',
    'destination_confirmed': '목적지가 확정됐습니다.',
    'destination_changed': '목적지가 변경됐습니다.',
    'patient_redirected_away': '목적지가 변경되어 이 병원의 참여가 종료됐습니다.',
    'patient_cancelled': '이송이 취소됐습니다.',
    'transport_recheck_required': '변경된 조건이 이 환자에게 영향을 줄 수 있어 재확인이 필요합니다.',
    'transport_conditions_restored': '확인된 조건 위험이 해소됐습니다.',
    'adjustment_needed': '현재 병원이 이송 조정을 요청했습니다.',
    'transport_alternative_requested': '병원 판단에 따라 대체병원 탐색이 요청됐습니다.',
    'transport_inquiry_ready': '대체병원의 유효한 수용 응답이 있어 담당자 판단이 필요합니다.',
    'transport_inquiry_exhausted': '대체병원 자동 탐색이 종료되어 담당자 판단이 필요합니다.',
}
EVENT_TYPES = tuple(SUMMARIES)
INQUIRY_TYPES = ('inquiry_cancelled', 'inquiry_closed', 'inquiry_expired', 'inquiry_question',
                 'patient_information_amended')


def _integer(value, field, minimum=0, maximum=MAX_ID):
    if isinstance(value, str) and len(value) <= 19 and re.fullmatch('[0-9]+', value):
        value = int(value)
    service._require(type(value) is int and minimum <= value <= maximum,
                     'INVALID', f'{field} 값이 올바르지 않습니다')
    return value


def _actor(value):
    service._require(isinstance(value, str) and 0 < len(value) <= MAX_ACTOR and
                     value == value.strip() and not any(ord(c) < 32 or ord(c) == 127 for c in value),
                     'INVALID', f'actor_id는 공백 없는 양끝과 {MAX_ACTOR}자 이내 식별자여야 합니다')
    return value


def _boolean(value):
    if isinstance(value, str):
        values = {'true': True, 'false': False, '1': True, '0': False}
        service._require(value in values, 'INVALID', 'unacknowledged_only 값이 올바르지 않습니다')
        value = values[value]
    service._require(type(value) is bool, 'INVALID', 'unacknowledged_only는 true 또는 false입니다')
    return value


def _scope(conn, now, pid, actor_id, hospital_id):
    _integer(now, 'now')
    actor_id = _actor(actor_id)
    if hospital_id is None:
        service._patient(conn, pid)
        return 'paramedic', '', actor_id
    service._require(isinstance(hospital_id, str) and hospital_id, 'INVALID', 'hospital_id 값이 필요합니다')
    # 관계를 먼저 확인해, 관련 없는 환자와 존재하지 않는 환자가 같은 403을 반환한다.
    related = conn.execute(
        'SELECT 1 FROM inquiries WHERE patient_id=? AND hospital_id=? '
        'UNION ALL SELECT 1 FROM conversation_hospital_sessions s '
        'JOIN conversations c ON c.id=s.conversation_id '
        'WHERE c.patient_id=? AND s.hospital_id=? LIMIT 1',
        (pid, hospital_id, pid, hospital_id)).fetchone()
    service._require(related, 'NOT_PARTICIPANT', '관련 문의 또는 참여 이력이 있는 병원만 조회할 수 있습니다', 403)
    return 'hospital', hospital_id, actor_id


def _visibility(pid, now, hospital_id):
    marks = ','.join('?' for _ in EVENT_TYPES)
    sql = f'e.patient_id=? AND e.at<=? AND e.type IN ({marks})'
    args = [pid, now, *EVENT_TYPES]
    if hospital_id is not None:
        inquiry_marks = ','.join('?' for _ in INQUIRY_TYPES)
        sql += (
            f' AND ((e.hospital_id=? AND e.type IN ({inquiry_marks}) AND EXISTS('
            'SELECT 1 FROM inquiries q WHERE q.patient_id=e.patient_id AND '
            'q.hospital_id=e.hospital_id AND q.created_at<=e.at)) OR '
            '((e.hospital_id=? OR e.hospital_id IS NULL) AND EXISTS('
            'SELECT 1 FROM conversation_hospital_sessions s JOIN conversations c '
            'ON c.id=s.conversation_id WHERE c.patient_id=e.patient_id AND '
            's.hospital_id=? AND e.id>=s.joined_event_id AND '
            '(s.left_event_id IS NULL OR e.id<=s.left_event_id))))')
        args.extend([hospital_id, *INQUIRY_TYPES, hospital_id, hospital_id])
    return sql, args


def _item(event, receipt=None):
    receipt = receipt or {}
    delivered = receipt.get('delivered_at')
    acknowledged = receipt.get('acknowledged_at')
    return dict(event_id=event['id'], type=event['type'], at=event['at'],
                patient_id=event['patient_id'], hospital_id=event['hospital_id'],
                summary=SUMMARIES[event['type']], delivered_at=delivered,
                acknowledged_at=acknowledged,
                delivery_delay_seconds=None if delivered is None else delivered - event['at'],
                acknowledgement_delay_seconds=None if acknowledged is None else acknowledged - event['at'])


def list_events(conn, now, pid, *, actor_id, hospital_id=None, after_id=0, limit=100,
                unacknowledged_only=False):
    """stream 커서와 미확인 재조회는 별도다. 미확인 모드에는 after_id를 섞지 않는다.

    미확인 모드는 가장 오래된 limit개를 반복 반환한다. 확인한 뒤 다시 조회하면
    다음 미확인 건이 나타난다. 커서를 진행했다가 놓친 알림도 이 경로로 조회한다.
    """
    after_id = _integer(after_id, 'after_id')
    limit = _integer(limit, 'limit', 1, MAX_PAGE)
    unacknowledged_only = _boolean(unacknowledged_only)
    service._require(not (unacknowledged_only and after_id), 'INVALID',
                     '미확인 재조회에는 after_id를 함께 사용할 수 없습니다')
    role, hospital_scope, actor_id = _scope(conn, now, pid, actor_id, hospital_id)
    where, args = _visibility(pid, now, hospital_id)
    sql = ('SELECT e.*,r.delivered_at,r.acknowledged_at FROM events e '
           'LEFT JOIN important_event_receipts r ON r.event_id=e.id AND r.role=? '
           'AND r.hospital_scope=? AND r.actor_id=? WHERE ' + where + ' AND e.id>?')
    params = [role, hospital_scope, actor_id, *args, after_id]
    if unacknowledged_only:
        sql += ' AND r.acknowledged_at IS NULL'
    events = [dict(r) for r in conn.execute(sql + ' ORDER BY e.id LIMIT ?', (*params, limit + 1))]
    has_more = len(events) > limit
    events = events[:limit]
    return dict(items=[_item(e, e) for e in events], has_more=has_more,
                next_after_id=None if unacknowledged_only else events[-1]['id'] if events else after_id,
                scope=dict(patient_id=pid, role=role, hospital_id=hospital_id, actor_id=actor_id),
                unacknowledged_only=unacknowledged_only)


def _visible_event(conn, now, pid, event_id, hospital_id):
    where, args = _visibility(pid, now, hospital_id)
    event = conn.execute('SELECT e.* FROM events e WHERE ' + where + ' AND e.id=?',
                         (*args, event_id)).fetchone()
    service._require(event is not None, 'NOT_FOUND', '조회 가능한 중요 사건이 없습니다', 404)
    return dict(event)


def _receipt(conn, event_id, role, hospital_scope, actor_id):
    value = conn.execute('SELECT delivered_at,acknowledged_at FROM important_event_receipts '
                         'WHERE event_id=? AND role=? AND hospital_scope=? AND actor_id=?',
                         (event_id, role, hospital_scope, actor_id)).fetchone()
    return dict(value) if value is not None else None


def mark_delivered(conn, now, pid, event_id, *, actor_id, hospital_id=None):
    """클라이언트가 실제 받은 뒤 호출한다. 최초 서버 시각만 보존한다."""
    event_id = _integer(event_id, 'event_id', 1)
    with db.transaction(conn):
        role, hospital_scope, actor_id = _scope(conn, now, pid, actor_id, hospital_id)
        event = _visible_event(conn, now, pid, event_id, hospital_id)
        previous = _receipt(conn, event_id, role, hospital_scope, actor_id)
        if previous is None:
            conn.execute('INSERT INTO important_event_receipts '
                         '(event_id,role,hospital_scope,actor_id,delivered_at) VALUES (?,?,?,?,?)',
                         (event_id, role, hospital_scope, actor_id, now))
        receipt = _receipt(conn, event_id, role, hospital_scope, actor_id)
        return dict(event=_item(event, receipt), duplicate=previous is not None)


def acknowledge(conn, now, pid, event_id, *, actor_id, hospital_id=None):
    """사용자 인지만 기록한다. 수용·재확인·배정 업무 상태는 전혀 바꾸지 않는다."""
    event_id = _integer(event_id, 'event_id', 1)
    with db.transaction(conn):
        role, hospital_scope, actor_id = _scope(conn, now, pid, actor_id, hospital_id)
        event = _visible_event(conn, now, pid, event_id, hospital_id)
        receipt = _receipt(conn, event_id, role, hospital_scope, actor_id)
        service._require(receipt is not None, 'DELIVERY_REQUIRED', '먼저 사건 수신을 기록하세요', 409)
        duplicate = receipt['acknowledged_at'] is not None
        if not duplicate:
            service._require(now >= receipt['delivered_at'], 'INVALID_TIME',
                             '확인 시각은 수신 시각보다 빠를 수 없습니다', 409)
            conn.execute('UPDATE important_event_receipts SET acknowledged_at=? '
                         'WHERE event_id=? AND role=? AND hospital_scope=? AND actor_id=?',
                         (now, event_id, role, hospital_scope, actor_id))
        return dict(event=_item(event, _receipt(conn, event_id, role, hospital_scope, actor_id)),
                    duplicate=duplicate)
