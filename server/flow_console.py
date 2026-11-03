"""SELECT-only local synthetic observer; not an authenticated hospital view.

Never calls business GET helpers: some create conversations or expire inquiries.
No migrations, missing DB creation, acknowledgement or operational transitions.
"""
from contextlib import closing
import json
import math
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import parse_qsl

REVISION = 'flow-console-20261103-v1'
LIST_LIMIT = 100
HISTORY_LIMIT = 80
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z')
_ACTOR = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:@-]{0,79}\Z')
_EVENT_LABELS = {
    'patient_created': '환자 등록', 'patient_updated': '환자정보 수정',
    'voice_information_applied': '음성 기록 반영', 'inquiry_sent': '수용 문의',
    'inquiry_responded': '병원 응답', 'inquiry_answered': '추가 질문 답변',
    'inquiry_cancelled': '문의 취소', 'inquiry_closed': '문의 종료',
    'inquiry_expired': '응답 만료', 'destination_confirmed': '목적지 확정',
    'destination_changed': '목적지 변경', 'departed': '출발',
    'arrival_confirmed': '도착 확인', 'patient_cancelled': '이송 취소',
    'adjustment_needed': '병원 조정 요청', 'hospital_keeps': '수용 유지',
    'enroute_patient_change_requested': '상태 변경 검토 요청',
    'enroute_patient_change_decided': '상태 변경 검토 결과',
    'transport_recheck_required': '이송 조건 재확인',
}
_VITALS = frozenset(('sbp', 'dbp', 'hr', 'spo2', 'temp', 'rr', 'glucose'))


class FlowConsoleError(Exception):
    def __init__(self, code, status, message):
        super().__init__(message)
        self.code, self.status, self.message = code, status, message

    def response(self):
        return dict(error=self.code, message=self.message)


def _invalid():
    raise FlowConsoleError('INVALID_QUERY', 400, '환자 또는 미등록 기록 하나만 선택하세요.')


def _identifier(value):
    return isinstance(value, str) and _ID.fullmatch(value) is not None


def parse_query(query):
    """Parse before opening a DB; duplicates and ignored query keys are errors."""
    if not isinstance(query, str) or len(query) > 1024:
        _invalid()
    try:
        pairs = parse_qsl(query, keep_blank_values=True, strict_parsing=True,
                          max_num_fields=4, encoding='utf-8', errors='strict')
    except (ValueError, UnicodeError):
        _invalid()
    if len(pairs) > 1 or any(key not in ('pid', 'sid') or not _identifier(value) for key, value in pairs):
        _invalid()
    return dict(pairs)


def _text(value, maximum=2000):
    return value[:maximum] if isinstance(value, str) else None


def _object(value):
    if not isinstance(value, str):
        return {}
    try:
        result = json.loads(value, parse_constant=lambda _: None)
    except (ValueError, TypeError, RecursionError):
        return {}
    return result if isinstance(result, dict) else {}


def _number(value):
    return value if type(value) in (int, float) and math.isfinite(value) else None


def _actor(value):
    return value if isinstance(value, str) and _ACTOR.fullmatch(value) else None


def _draft(row):
    fields = _object(row['fields'])
    sex = fields.get('sex')
    return dict(id=row['id'], actor_id=_actor(row['actor_id']), version=row['version'],
                age=_number(fields.get('age')), sex=sex if sex in ('male', 'female', 'unknown') else None,
                chief_complaint=_text(fields.get('chief_complaint'), 80), updated_at=row['updated_at'])


def _patient(row, *, compact=False):
    result = {key: row[key] for key in ('id', 'age', 'state', 'version', 'destination_hospital_id', 'requested_at')}
    result['symptoms'] = _text(row['symptoms'])
    if compact:
        return result
    result.update({key: row[key] for key in ('onset_at', 'consciousness', 'breathing', 'departed_at', 'eta_at',
                                            'arrived_at', 'lat', 'lon')})
    extra = _object(row['extra'])
    vitals = extra.get('vitals') if isinstance(extra.get('vitals'), dict) else {}
    result['vitals'] = {key: _number(value) for key, value in vitals.items()
                        if key in _VITALS and _number(value) is not None}
    return result


def _row(conn, sql, args=()):
    item = conn.execute(sql, args).fetchone()
    return dict(item) if item is not None else None


def _rows(conn, sql, args=()):
    return [dict(item) for item in conn.execute(sql, args)]


def _owner(conn, patient):
    applied = _object(patient['extra']).get('voice_observations')
    sid = applied.get('session_id') if isinstance(applied, dict) else None
    linked = None
    if _identifier(sid):
        linked = _row(conn, 'SELECT id,actor_id FROM voice_sessions WHERE id=? AND patient_id=?', (sid, patient['id']))
    if linked is None:
        linked = _row(conn, "SELECT id,actor_id FROM voice_sessions WHERE patient_id=? AND state='SUBMITTED' "
                           'ORDER BY submitted_at DESC,updated_at DESC,id DESC LIMIT 1', (patient['id'],))
    if linked is not None and _actor(linked['actor_id']):
        return linked['actor_id'], linked['id'], 'linked_voice'
    created = _row(conn, "SELECT actor FROM events WHERE patient_id=? AND type='patient_created' ORDER BY id LIMIT 1", (patient['id'],))
    return (_actor(created['actor']) if created else None), None, 'patient_created_event' if created else None


def _workflow(conn, pid, tables):
    output = dict(initial=None, alternative=None, review=None)
    for key, table, fields in (
        ('initial', 'inquiry_workflows', 'enabled,state,round_no,version,updated_at'),
        ('alternative', 'transport_inquiry_workflows', 'enabled,state,round_no,version,updated_at'),
        ('review', 'transport_reviews', 'enabled,state,hospital_id,version,updated_at,last_checked_at'),
    ):
        if table in tables:
            output[key] = _row(conn, f'SELECT {fields} FROM {table} WHERE patient_id=?', (pid,))
    return output


def _recommendation(conn, now, patient, tables):
    if 'planning_state' not in tables or 'planning_results' not in tables:
        return None
    state = _row(conn, 'SELECT revision,last_error FROM planning_state WHERE id=1')
    if state is None:
        return None
    base = dict(status='QUEUED', revision=state['revision'], created_at=None, expires_at=None,
                is_current=False, hospital_id=None, result_status=None)
    if patient['state'] not in ('WAITING', 'CONFIRMED', 'IN_TRANSIT'):
        return dict(base, status='NOT_APPLICABLE')
    row = _row(conn, 'SELECT revision,created_at,expires_at,data FROM planning_results WHERE patient_id=?', (patient['id'],))
    if row is None:
        return dict(base, status='ERROR' if state['last_error'] else 'QUEUED')
    base.update(created_at=row['created_at'], expires_at=row['expires_at'])
    if row['revision'] != state['revision'] or now >= row['expires_at']:
        return dict(base, status='STALE')
    result = _object(row['data'])
    hid = result.get('hospital_id')
    return dict(base, status='READY', is_current=True,
                hospital_id=hid if _identifier(hid) else None, result_status=_text(result.get('status'), 80))


def _selected_patient(conn, now, patient, tables):
    pid = patient['id']
    owner, sid, owner_source = _owner(conn, patient)
    inquiries = _rows(conn, 'SELECT q.id,q.hospital_id,h.name AS hospital_name,q.state,q.version,q.patient_version,'
        'q.created_at,q.responded_at,q.expires_at,q.reason,q.question,q.answer,q.handled_by '
        'FROM inquiries q LEFT JOIN hospitals h ON h.id=q.hospital_id WHERE q.patient_id=? ORDER BY q.id DESC LIMIT 200', (pid,))
    events = _rows(conn, 'SELECT e.id,e.at,e.type,e.hospital_id,h.name AS hospital_name '
        'FROM events e LEFT JOIN hospitals h ON h.id=e.hospital_id WHERE e.patient_id=? ORDER BY e.id DESC LIMIT ?', (pid, HISTORY_LIMIT))
    for event in events:
        event['summary'] = _EVENT_LABELS.get(event['type'], '업무 기록')
    sessions, messages = [], []
    if {'conversations', 'conversation_hospital_sessions', 'chat_messages'} <= tables:
        sessions = _rows(conn, 'SELECT s.id,s.hospital_id,h.name AS hospital_name,s.joined_at,s.left_at '
            'FROM conversation_hospital_sessions s JOIN conversations c ON c.id=s.conversation_id '
            'LEFT JOIN hospitals h ON h.id=s.hospital_id WHERE c.patient_id=? ORDER BY s.id', (pid,))
        messages = _rows(conn, 'SELECT m.id,m.sender_role,m.sender_name,m.body,m.created_at,m.hospital_session_id,'
            's.hospital_id,h.name AS hospital_name FROM chat_messages m JOIN conversations c ON c.id=m.conversation_id '
            'LEFT JOIN conversation_hospital_sessions s ON s.id=m.hospital_session_id '
            'LEFT JOIN hospitals h ON h.id=s.hospital_id WHERE c.patient_id=? ORDER BY m.id DESC LIMIT ?', (pid, HISTORY_LIMIT))
    return dict(kind='patient', patient=_patient(patient), draft=None, owner_actor_id=owner,
                owner_actor_source=owner_source, voice_session_id=sid, inquiries=inquiries,
                workflow=_workflow(conn, pid, tables), events=list(reversed(events)), messages=list(reversed(messages)),
                hospital_sessions=sessions, recommendation=_recommendation(conn, now, patient, tables))


def snapshot(path, now, *, pid=None, sid=None, data_mode='synthetic', timeout=.5):
    """One bounded read transaction on an existing read-only SQLite URI."""
    if data_mode != 'synthetic':
        raise FlowConsoleError('FLOW_CONSOLE_UNAVAILABLE', 404, '이 실행 모드에서는 관제 화면을 제공하지 않습니다.')
    if pid is not None and sid is not None or (pid is not None and not _identifier(pid)) or (sid is not None and not _identifier(sid)):
        _invalid()
    if type(now) is not int or now < 0:
        _invalid()
    try:
        with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True,
                                     isolation_level=None, timeout=timeout)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute('PRAGMA query_only=ON')
            deadline = time.monotonic() + timeout
            conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
            conn.execute('BEGIN')
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if conn.execute("SELECT 1 FROM hospitals WHERE source IS NULL OR source!='synthetic' LIMIT 1").fetchone() is not None:
                raise FlowConsoleError('SYNTHETIC_ONLY', 403, '가상 시연 자료만 관찰할 수 있습니다.')
            hospitals = _rows(conn, 'SELECT id,name,source FROM hospitals ORDER BY id LIMIT 100')
            patients = [_patient(row, compact=True) for row in _rows(conn,
                'SELECT * FROM patients ORDER BY requested_at DESC,id DESC LIMIT ?', (LIST_LIMIT,))]
            drafts = [_draft(row) for row in _rows(conn,
                "SELECT id,actor_id,version,fields,updated_at FROM voice_sessions WHERE state='DRAFT' AND patient_id IS NULL "
                'ORDER BY updated_at DESC,id DESC LIMIT ?', (LIST_LIMIT,))]
            selected = None
            if pid is not None:
                patient = _row(conn, 'SELECT * FROM patients WHERE id=?', (pid,))
                if patient is None:
                    raise FlowConsoleError('NOT_FOUND', 404, '선택한 환자 기록이 없습니다.')
                selected = _selected_patient(conn, now, patient, tables)
            elif sid is not None:
                row = _row(conn, 'SELECT id,actor_id,state,patient_id,version,fields,updated_at '
                           'FROM voice_sessions WHERE id=?', (sid,))
                if row is None:
                    raise FlowConsoleError('NOT_FOUND', 404, '선택한 미등록 초안이 없습니다.')
                if row['state'] == 'DRAFT' and row['patient_id'] is None:
                    selected = dict(kind='draft', patient=None, draft=_draft(row), owner_actor_id=_actor(row['actor_id']),
                        owner_actor_source='draft_owner', voice_session_id=row['id'], inquiries=[],
                        workflow=dict(initial=None, alternative=None, review=None), events=[], messages=[],
                        hospital_sessions=[], recommendation=None)
                elif row['state'] == 'SUBMITTED' and _identifier(row['patient_id']):
                    # Registration happens only in the original role UI. An old
                    # selected draft follows that exact stored link, without
                    # creating a patient, resubmitting or modifying either row.
                    patient = _row(conn, 'SELECT * FROM patients WHERE id=?', (row['patient_id'],))
                    if patient is None:
                        raise FlowConsoleError('NOT_FOUND', 404, '연결된 환자 기록이 없습니다.')
                    selected = _selected_patient(conn, now, patient, tables)
                else:
                    raise FlowConsoleError('NOT_FOUND', 404, '선택한 미등록 초안이 없습니다.')
            result = dict(revision=REVISION, checked_at=now, scope='synthetic_local_observer', readonly=True,
                          clinical_state_changed=False, patients=patients, drafts=drafts, hospitals=hospitals, selected=selected)
            conn.execute('ROLLBACK')
            return result
    except FlowConsoleError:
        raise
    except (OSError, sqlite3.Error, ValueError, TypeError, OverflowError):
        raise FlowConsoleError('FLOW_CONSOLE_UNAVAILABLE', 503, '관제 자료를 읽지 못했습니다. 서버 상태를 확인하세요.') from None
