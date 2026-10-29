"""통화 전달 기록과 상대측 내용 확인. 의료 수용·이송 상태를 변경하지 않는다.

actor_id는 시연 클라이언트의 선언 식별자이며 실제 사용자 인증이 아니다.
실제 전화·녹음은 수행하지 않는다. 정정은 원문 변경 대신 새 기록으로 남긴다.
"""
import hashlib
import json
import re

from . import db, service

MAX_ID = (1 << 63) - 1
MAX_PAGE = 100
OUTCOMES = frozenset(('can_accept', 'cannot_accept', 'checking', 'additional_info',
                      'no_answer', 'callback_requested', 'other'))
COUNTERPARTY_ROLES = frozenset(('paramedic', 'emergency_staff', 'coordinator', 'other'))

SCHEMA = """
CREATE TABLE IF NOT EXISTS call_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id TEXT NOT NULL REFERENCES patients(id),
    hospital_id TEXT NOT NULL REFERENCES hospitals(id),
    author_role TEXT NOT NULL CHECK(author_role IN ('paramedic','hospital')),
    author_scope TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    client_record_id TEXT NOT NULL,
    call_started_at INTEGER NOT NULL,
    call_ended_at INTEGER NOT NULL,
    recorded_at INTEGER NOT NULL,
    reported_outcome TEXT NOT NULL CHECK(reported_outcome IN
       ('can_accept','cannot_accept','checking','additional_info','no_answer','callback_requested','other')),
    counterparty_role TEXT NOT NULL CHECK(counterparty_role IN
       ('paramedic','emergency_staff','coordinator','other')),
    counterparty_label TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    supersedes_id INTEGER UNIQUE REFERENCES call_records(id),
    request_fingerprint TEXT NOT NULL,
    CHECK((author_role='paramedic' AND author_scope='') OR
          (author_role='hospital' AND author_scope=hospital_id)),
    CHECK(0 <= call_started_at AND call_started_at <= call_ended_at AND call_ended_at <= recorded_at),
    UNIQUE(author_role,author_scope,actor_id,client_record_id)
);
CREATE INDEX IF NOT EXISTS call_records_case ON call_records(patient_id,hospital_id,id);
CREATE TABLE IF NOT EXISTS call_record_confirmations (
    record_id INTEGER PRIMARY KEY REFERENCES call_records(id),
    role TEXT NOT NULL CHECK(role IN ('paramedic','hospital')),
    hospital_scope TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    client_confirmation_id TEXT NOT NULL,
    decision TEXT NOT NULL CHECK(decision IN ('confirm','dispute')),
    reason TEXT NOT NULL DEFAULT '',
    confirmed_at INTEGER NOT NULL,
    request_fingerprint TEXT NOT NULL,
    CHECK((role='paramedic' AND hospital_scope='') OR
          (role='hospital' AND hospital_scope<>'')),
    UNIQUE(role,hospital_scope,actor_id,client_confirmation_id)
);
CREATE TRIGGER IF NOT EXISTS call_records_no_update BEFORE UPDATE ON call_records
    BEGIN SELECT RAISE(ABORT, 'call record is immutable'); END;
CREATE TRIGGER IF NOT EXISTS call_records_no_delete BEFORE DELETE ON call_records
    BEGIN SELECT RAISE(ABORT, 'call record is immutable'); END;
CREATE TRIGGER IF NOT EXISTS call_records_no_replace BEFORE INSERT ON call_records
    WHEN EXISTS(SELECT 1 FROM call_records WHERE id=NEW.id OR
        (author_role=NEW.author_role AND author_scope=NEW.author_scope AND actor_id=NEW.actor_id
         AND client_record_id=NEW.client_record_id))
    BEGIN SELECT RAISE(ABORT, 'call record is immutable'); END;
CREATE TRIGGER IF NOT EXISTS call_confirmations_no_update BEFORE UPDATE ON call_record_confirmations
    BEGIN SELECT RAISE(ABORT, 'call confirmation is immutable'); END;
CREATE TRIGGER IF NOT EXISTS call_confirmations_no_delete BEFORE DELETE ON call_record_confirmations
    BEGIN SELECT RAISE(ABORT, 'call confirmation is immutable'); END;
CREATE TRIGGER IF NOT EXISTS call_confirmations_no_replace BEFORE INSERT ON call_record_confirmations
    WHEN EXISTS(SELECT 1 FROM call_record_confirmations WHERE record_id=NEW.record_id OR
        (role=NEW.role AND hospital_scope=NEW.hospital_scope AND actor_id=NEW.actor_id
         AND client_confirmation_id=NEW.client_confirmation_id))
    BEGIN SELECT RAISE(ABORT, 'call confirmation is immutable'); END;
"""


def _integer(value, name, minimum=0):
    if isinstance(value, str) and len(value) <= 19 and re.fullmatch(r'[0-9]+', value):
        value = int(value)
    service._require(type(value) is int and minimum <= value <= MAX_ID,
                     'INVALID', f'{name}는 범위 내 정수여야 합니다')
    return value


def _identifier(value, name, maximum=80):
    service._require(isinstance(value, str) and 1 <= len(value) <= maximum and
                     re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:@-]*', value) is not None,
                     'INVALID', f'{name}는 최대 {maximum}자의 불투명 식별자여야 합니다')
    return value


def _text(value, name, maximum, multiline=False):
    allowed = '\n\r\t' if multiline else ''
    service._require(isinstance(value, str) and len(value) <= maximum and
                     not any((ord(c) < 32 and c not in allowed) or ord(c) == 127 for c in value),
                     'INVALID', f'{name}는 최대 {maximum}자 문자열이어야 합니다')
    return value


def _payload(payload, allowed, required):
    service._require(isinstance(payload, dict) and not (payload.keys() - allowed)
                     and required <= payload.keys(), 'INVALID', '알 수 없거나 누락된 통화 기록 필드입니다')


def _relationship(conn, pid, hid, now):
    service._hospital(conn, hid)
    related = conn.execute(
        'SELECT 1 FROM inquiries WHERE patient_id=? AND hospital_id=? '
        'UNION ALL SELECT 1 FROM patients WHERE id=? AND destination_hospital_id=? '
        'UNION ALL SELECT 1 FROM conversation_hospital_sessions s '
        'JOIN conversations c ON c.id=s.conversation_id '
        'WHERE c.patient_id=? AND s.hospital_id=? '
        'UNION ALL SELECT 1 FROM call_records WHERE patient_id=? AND hospital_id=? '
        'AND recorded_at<=? LIMIT 1',
        (pid, hid, pid, hid, pid, hid, pid, hid, now)).fetchone()
    service._require(related, 'NOT_FOUND', '해당 병원과 이송 건의 통화 기록 범위가 없습니다', 404)


def _scope(conn, pid, hospital_id, now):
    service._require(isinstance(pid, str) and 1 <= len(pid) <= 128, 'INVALID', '환자 ID가 필요합니다')
    patient = service._patient(conn, pid)
    if hospital_id is not None:
        service._require(isinstance(hospital_id, str) and 1 <= len(hospital_id) <= 128,
                         'INVALID', '병원 ID가 필요합니다')
        _relationship(conn, pid, hospital_id, now)
    return patient, dict(patient_id=pid, role='hospital' if hospital_id is not None else 'paramedic',
                         hospital_id=hospital_id)


def _fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


def _visible(conn, now, pid, record_id, hospital_id):
    sql = 'SELECT * FROM call_records WHERE id=? AND patient_id=? AND recorded_at<=?'
    args = [record_id, pid, now]
    if hospital_id is not None:
        sql += ' AND hospital_id=?'
        args.append(hospital_id)
    record = conn.execute(sql, args).fetchone()
    service._require(record is not None, 'NOT_FOUND', '조회 가능한 통화 기록이 없습니다', 404)
    return dict(record)


def _public(conn, now, record):
    item = {key: value for key, value in dict(record).items()
            if key not in ('request_fingerprint', 'author_scope')}
    successor = conn.execute('SELECT id FROM call_records WHERE supersedes_id=? AND recorded_at<=?',
                              (item['id'], now)).fetchone()
    confirmation = conn.execute('SELECT role,actor_id,decision,reason,confirmed_at FROM '
                                 'call_record_confirmations WHERE record_id=? AND confirmed_at<=?',
                                 (item['id'], now)).fetchone()
    confirmation = dict(confirmation) if confirmation is not None else None
    item.update(superseded_by_id=successor['id'] if successor else None,
                confirmation=confirmation,
                confirmation_status='unconfirmed' if confirmation is None else
                                    'confirmed' if confirmation['decision'] == 'confirm' else 'disputed',
                confirmation_scope='record_content_only', operational_effect='none')
    return item


def create(conn, now, pid, payload, hospital_id=None):
    now = _integer(now, 'now')
    _payload(payload, {'actor_id', 'client_record_id', 'hospital_id', 'call_started_at', 'call_ended_at',
                       'reported_outcome', 'counterparty_role', 'counterparty_label', 'note', 'supersedes_id'},
             {'actor_id', 'client_record_id', 'call_started_at', 'call_ended_at',
              'reported_outcome', 'counterparty_role'})
    actor = _identifier(payload['actor_id'], 'actor_id')
    request_id = _identifier(payload['client_record_id'], 'client_record_id', 128)
    target = payload.get('hospital_id', hospital_id)
    service._require(isinstance(target, str) and 1 <= len(target) <= 128 and
                     (hospital_id is None or target == hospital_id), 'INVALID', '통화 상대 병원 ID가 필요합니다')
    started = _integer(payload['call_started_at'], 'call_started_at')
    ended = _integer(payload['call_ended_at'], 'call_ended_at')
    service._require(started <= ended <= now, 'INVALID', '통화 시각은 시작 <= 종료 <= 현재여야 합니다')
    outcome, counterpart = payload['reported_outcome'], payload['counterparty_role']
    service._require(isinstance(outcome, str) and outcome in OUTCOMES, 'INVALID', '통화 결과 값 오류')
    service._require(isinstance(counterpart, str) and counterpart in COUNTERPARTY_ROLES,
                     'INVALID', '통화 상대 역할 값 오류')
    label = _text(payload.get('counterparty_label', ''), 'counterparty_label', 80)
    note = _text(payload.get('note', ''), 'note', 1000, multiline=True)
    parent = payload.get('supersedes_id')
    if parent is not None:
        parent = _integer(parent, 'supersedes_id', 1)
    canonical = dict(patient_id=pid, hospital_id=target, call_started_at=started, call_ended_at=ended,
                     reported_outcome=outcome, counterparty_role=counterpart, counterparty_label=label,
                     note=note, supersedes_id=parent)
    fingerprint = _fingerprint(canonical)
    with db.transaction(conn):
        patient, scope = _scope(conn, pid, hospital_id, now)
        # 구급대원이 명시한 상대 병원과 기록만 공유한다. 전자 문의를 먼저
        # 만들도록 요구하지 않으며 대화 참여·문의·수용 처리 권한은 만들지 않는다.
        service._hospital(conn, target)
        service._require(patient['requested_at'] <= started, 'INVALID', '환자 등록 전 통화는 기록할 수 없습니다')
        role, origin = scope['role'], hospital_id or ''
        existing = conn.execute('SELECT * FROM call_records WHERE author_role=? AND author_scope=? '
                                'AND actor_id=? AND client_record_id=?', (role, origin, actor, request_id)).fetchone()
        if existing is not None:
            service._require(existing['recorded_at'] <= now, 'CLOCK_CONFLICT',
                             '서버 시각이 기존 통화 기록 시각보다 빠릅니다', 409)
            service._require(existing['request_fingerprint'] == fingerprint, 'CONFLICT',
                             '같은 통화 기록 식별자를 다른 내용에 재사용할 수 없습니다', 409)
            return dict(record=_public(conn, now, existing), duplicate=True, scope=scope)
        if parent is not None:
            old = _visible(conn, now, pid, parent, target)
            service._require(old['author_role'] == role and old['actor_id'] == actor,
                             'NOT_AUTHOR', '원 작성자만 정정 기록을 추가할 수 있습니다', 403)
            service._require(conn.execute('SELECT 1 FROM call_records WHERE supersedes_id=?',
                                          (parent,)).fetchone() is None, 'SUPERSEDED',
                             '이미 정정된 기록입니다. 가장 최근 기록을 기준으로 정정하세요', 409)
        record_id = conn.execute(
            'INSERT INTO call_records (patient_id,hospital_id,author_role,author_scope,actor_id,client_record_id,'
            'call_started_at,call_ended_at,recorded_at,reported_outcome,counterparty_role,counterparty_label,'
            'note,supersedes_id,request_fingerprint) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (pid, target, role, origin, actor, request_id, started, ended, now, outcome, counterpart,
             label, note, parent, fingerprint)).lastrowid
        record = _visible(conn, now, pid, record_id, target)
        return dict(record=_public(conn, now, record), duplicate=False, scope=scope)


def view(conn, now, pid, record_id, *, hospital_id=None):
    now, record_id = _integer(now, 'now'), _integer(record_id, 'record_id', 1)
    _, scope = _scope(conn, pid, hospital_id, now)
    return dict(record=_public(conn, now, _visible(conn, now, pid, record_id, hospital_id)), scope=scope)


def list_records(conn, now, pid, *, hospital_id=None, limit=50, before_id=None):
    now, limit = _integer(now, 'now'), _integer(limit, 'limit', 1)
    service._require(limit <= MAX_PAGE, 'INVALID', 'limit은 1~100입니다')
    before = None if before_id is None else _integer(before_id, 'before_id', 1)
    _, scope = _scope(conn, pid, hospital_id, now)
    sql, args = 'SELECT * FROM call_records WHERE patient_id=? AND recorded_at<=?', [pid, now]
    if hospital_id is not None:
        sql += ' AND hospital_id=?'
        args.append(hospital_id)
    if before is not None:
        sql += ' AND id<?'
        args.append(before)
    records = conn.execute(sql + ' ORDER BY id DESC LIMIT ?', (*args, limit + 1)).fetchall()
    more = len(records) > limit
    records = records[:limit]
    return dict(records=[_public(conn, now, row) for row in records], has_more=more,
                next_before_id=records[-1]['id'] if more else None, scope=scope)


def confirm(conn, now, pid, record_id, payload, *, hospital_id=None):
    now, record_id = _integer(now, 'now'), _integer(record_id, 'record_id', 1)
    _payload(payload, {'actor_id', 'client_confirmation_id', 'decision', 'reason'},
             {'actor_id', 'client_confirmation_id', 'decision'})
    actor = _identifier(payload['actor_id'], 'actor_id')
    key = _identifier(payload['client_confirmation_id'], 'client_confirmation_id', 128)
    decision = payload['decision']
    service._require(isinstance(decision, str) and decision in ('confirm', 'dispute'),
                     'INVALID', '확인은 confirm 또는 dispute입니다')
    reason = _text(payload.get('reason', ''), 'reason', 500, multiline=True)
    service._require(decision != 'dispute' or bool(reason.strip()), 'INVALID', '이의 사유가 필요합니다')
    fingerprint = _fingerprint(dict(patient_id=pid, record_id=record_id, decision=decision, reason=reason))
    with db.transaction(conn):
        _, scope = _scope(conn, pid, hospital_id, now)
        record = _visible(conn, now, pid, record_id, hospital_id)
        role, origin = scope['role'], hospital_id or ''
        service._require(role != record['author_role'], 'SELF_CONFIRMATION',
                         '작성자 측에서 상대측 내용 확인을 대신할 수 없습니다', 403)
        existing = conn.execute('SELECT * FROM call_record_confirmations WHERE role=? AND hospital_scope=? '
                                 'AND actor_id=? AND client_confirmation_id=?', (role, origin, actor, key)).fetchone()
        if existing is not None:
            service._require(existing['request_fingerprint'] == fingerprint, 'CONFLICT',
                             '같은 확인 식별자를 다른 내용에 재사용할 수 없습니다', 409)
            return dict(record=_public(conn, now, record), duplicate=True, scope=scope)
        service._require(conn.execute('SELECT 1 FROM call_records WHERE supersedes_id=?',
                                      (record_id,)).fetchone() is None, 'SUPERSEDED',
                         '정정된 기록은 새로 확인할 수 없습니다. 최신 기록을 확인하세요', 409)
        service._require(conn.execute('SELECT 1 FROM call_record_confirmations WHERE record_id=?',
                                      (record_id,)).fetchone() is None, 'ALREADY_CONFIRMED',
                         '상대측 확인이 이미 기록돼 있습니다', 409)
        conn.execute('INSERT INTO call_record_confirmations '
                     '(record_id,role,hospital_scope,actor_id,client_confirmation_id,decision,reason,'
                     'confirmed_at,request_fingerprint) VALUES (?,?,?,?,?,?,?,?,?)',
                     (record_id, role, origin, actor, key, decision, reason, now, fingerprint))
        return dict(record=_public(conn, now, record), duplicate=False, scope=scope)
