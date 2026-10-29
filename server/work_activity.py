"""Explicit start/stop measurements of human work, separate from workflow waiting.

These demo-scoped actor IDs are supplied by callers; they are not authentication.
No patient narrative, message body, or call content belongs in this ledger.
"""
import re

from . import db, service

MAX_ID = (1 << 63) - 1
MAX_PAGE = 200
MAX_SUMMARY_ROWS = 10000
ROLES = frozenset(('paramedic', 'hospital'))
KINDS = frozenset(('patient_input', 'request_view', 'request_review', 'respond',
                   'resource_update', 'additional_question', 'recheck', 'chat', 'call'))

SCHEMA = """
CREATE TABLE IF NOT EXISTS work_activities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id TEXT NOT NULL REFERENCES patients(id),
    role TEXT NOT NULL CHECK(role IN ('paramedic','hospital')),
    hospital_id TEXT REFERENCES hospitals(id),
    actor_id TEXT NOT NULL,
    client_activity_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('patient_input','request_view','request_review','respond',
                                    'resource_update','additional_question','recheck','chat','call')),
    started_at INTEGER NOT NULL,
    ended_at INTEGER,
    CHECK((role='paramedic' AND hospital_id IS NULL) OR
          (role='hospital' AND hospital_id IS NOT NULL)),
    CHECK(started_at >= 0 AND (ended_at IS NULL OR ended_at >= started_at))
);
CREATE UNIQUE INDEX IF NOT EXISTS work_activity_paramedic_request
    ON work_activities(actor_id,client_activity_id) WHERE role='paramedic';
CREATE UNIQUE INDEX IF NOT EXISTS work_activity_hospital_request
    ON work_activities(hospital_id,actor_id,client_activity_id) WHERE role='hospital';
CREATE INDEX IF NOT EXISTS work_activity_scope
    ON work_activities(patient_id,role,hospital_id,actor_id,id);
"""


def _integer(value, name, minimum=0, maximum=MAX_ID):
    if isinstance(value, str) and len(value) <= 19 and re.fullmatch(r'[0-9]+', value):
        value = int(value)
    service._require(type(value) is int and minimum <= value <= maximum,
                     'INVALID', f'{name} must be an integer between {minimum} and {maximum}')
    return value


def _identifier(value, name, maximum=80):
    service._require(isinstance(value, str) and 1 <= len(value) <= maximum and
                     re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:@-]*', value) is not None,
                     'INVALID', f'{name} must be a bounded, opaque ASCII identifier')
    return value


def _payload(payload, allowed, required):
    service._require(isinstance(payload, dict), 'INVALID', 'JSON object required')
    service._require(not (payload.keys() - allowed) and required <= payload.keys(),
                     'INVALID', 'Unexpected or missing activity fields')


def _scope(conn, pid, role, hospital_id, now):
    service._require(isinstance(role, str) and role in ROLES, 'INVALID', 'Invalid activity role')
    service._require(isinstance(pid, str) and 1 <= len(pid) <= 128,
                     'INVALID', 'patient_id required')
    patient = service._patient(conn, pid)
    if role == 'paramedic':
        service._require(hospital_id is None, 'INVALID', 'Paramedic scope has no hospital_id')
    else:
        service._require(isinstance(hospital_id, str) and 1 <= len(hospital_id) <= 128,
                         'INVALID', 'hospital_id required')
        service._hospital(conn, hospital_id)
        # 명시적으로 공유된 통화의 상대 기관도 해당 건의 계측을 사용할 수 있다.
        # 이 관계를 의료 업무나 대화 참여 권한에 재사용하지 않는다.
        related = patient['destination_hospital_id'] == hospital_id or conn.execute(
            'SELECT 1 FROM inquiries WHERE patient_id=? AND hospital_id=? LIMIT 1',
            (pid, hospital_id)).fetchone() or conn.execute(
            'SELECT 1 FROM conversation_hospital_sessions s JOIN conversations c '
            'ON c.id=s.conversation_id WHERE c.patient_id=? AND s.hospital_id=? LIMIT 1',
            (pid, hospital_id)).fetchone() or conn.execute(
            'SELECT 1 FROM call_records WHERE patient_id=? AND hospital_id=? '
            'AND recorded_at<=? LIMIT 1',
            (pid, hospital_id, now)).fetchone()
        service._require(related, 'NOT_FOUND', 'No activity scope for this hospital and patient', 404)
    return dict(patient_id=pid, role=role, hospital_id=hospital_id)


def _record(record, now):
    item = dict(record)
    end = item['ended_at']
    # Do not expose a decision/end later than the caller's evaluation clock.
    if end is not None and end > now:
        end = None
    item['ended_at'] = end
    item.update(state='OPEN' if end is None else 'CLOSED',
                completed_seconds=0 if end is None else end - item['started_at'],
                open_elapsed_seconds=max(0, now - item['started_at']) if end is None else 0)
    return item


def start(conn, now, pid, role, payload, hospital_id=None):
    """Start one measured task; an exact retry never changes its original start."""
    now = _integer(now, 'now')
    _payload(payload, {'actor_id', 'client_activity_id', 'kind'},
             {'actor_id', 'client_activity_id', 'kind'})
    actor = _identifier(payload['actor_id'], 'actor_id')
    key = _identifier(payload['client_activity_id'], 'client_activity_id', 128)
    kind = payload['kind']
    service._require(isinstance(kind, str) and kind in KINDS, 'INVALID', 'Invalid activity kind')
    with db.transaction(conn):
        scope = _scope(conn, pid, role, hospital_id, now)
        prior = conn.execute('SELECT * FROM work_activities WHERE role=? AND hospital_id IS ? '
                             'AND actor_id=? AND client_activity_id=?',
                             (role, hospital_id, actor, key)).fetchone()
        if prior is not None:
            service._require(prior['patient_id'] == pid and prior['kind'] == kind,
                             'CONFLICT', 'client_activity_id was already used for another task', 409)
            return dict(activity=_record(prior, now), duplicate=True, scope=scope)
        activity_id = conn.execute(
            'INSERT INTO work_activities '
            '(patient_id,role,hospital_id,actor_id,client_activity_id,kind,started_at) VALUES (?,?,?,?,?,?,?)',
            (pid, role, hospital_id, actor, key, kind, now)).lastrowid
        record = conn.execute('SELECT * FROM work_activities WHERE id=?', (activity_id,)).fetchone()
    return dict(activity=_record(record, now), duplicate=False, scope=scope)


def stop(conn, now, pid, role, activity_id, payload, hospital_id=None):
    """End the same actor's task using the server clock; repeated stop is harmless."""
    now = _integer(now, 'now')
    activity_id = _integer(activity_id, 'activity_id', 1)
    _payload(payload, {'actor_id'}, {'actor_id'})
    actor = _identifier(payload['actor_id'], 'actor_id')
    with db.transaction(conn):
        scope = _scope(conn, pid, role, hospital_id, now)
        record = conn.execute(
            'SELECT * FROM work_activities WHERE id=? AND patient_id=? AND role=? '
            'AND hospital_id IS ? AND actor_id=?',
            (activity_id, pid, role, hospital_id, actor)).fetchone()
        service._require(record is not None, 'NOT_FOUND', 'Activity not found in this scope', 404)
        service._require(now >= record['started_at'], 'CLOCK_CONFLICT',
                         'Stop clock precedes the activity start', 409)
        duplicate = record['ended_at'] is not None
        if not duplicate:
            conn.execute('UPDATE work_activities SET ended_at=? WHERE id=?', (now, activity_id))
            record = conn.execute('SELECT * FROM work_activities WHERE id=?', (activity_id,)).fetchone()
    return dict(activity=_record(record, now), duplicate=duplicate, scope=scope)


def list_records(conn, now, pid, role, actor_id, hospital_id=None, limit=50, before_id=None):
    now = _integer(now, 'now')
    actor = _identifier(actor_id, 'actor_id')
    limit = _integer(limit, 'limit', 1, MAX_PAGE)
    before = None if before_id is None else _integer(before_id, 'before_id', 1)
    scope = _scope(conn, pid, role, hospital_id, now)
    query = ('SELECT * FROM work_activities WHERE patient_id=? AND role=? AND hospital_id IS ? '
             'AND actor_id=? AND started_at<=?')
    args = [pid, role, hospital_id, actor, now]
    if before is not None:
        query += ' AND id<?'
        args.append(before)
    records = conn.execute(query + ' ORDER BY id DESC LIMIT ?', (*args, limit + 1)).fetchall()
    has_more = len(records) > limit
    items = [_record(record, now) for record in records[:limit]]
    return dict(scope=scope, actor_id=actor, measured_at=now, activities=items,
                has_more=has_more, next_before_id=items[-1]['id'] if has_more else None)


def _union_seconds(intervals):
    total, current = 0, None
    for start_at, end_at in sorted(intervals):
        if current is None:
            current = [start_at, end_at]
        elif start_at <= current[1]:
            current[1] = max(current[1], end_at)
        else:
            total += current[1] - current[0]
            current = [start_at, end_at]
    return total + (current[1] - current[0] if current is not None else 0)


def summary(conn, now, pid, role, actor_id=None, hospital_id=None, since=None, until=None):
    """Sum closed intervals per actor, merging overlaps; open intervals stay separate.

    Bounds are [since, until). They clip measured intervals; missing end events are
    never treated as confirmed work. Across actors the result is person-seconds,
    not elapsed wall time. Kind subtotals may overlap each other.
    """
    now = _integer(now, 'now')
    lower = 0 if since is None else _integer(since, 'since')
    upper = now if until is None else _integer(until, 'until')
    service._require(lower <= upper <= now, 'INVALID', 'Require since <= until <= now')
    actor = None if actor_id is None else _identifier(actor_id, 'actor_id')
    scope = _scope(conn, pid, role, hospital_id, now)
    query = ('SELECT * FROM work_activities WHERE patient_id=? AND role=? '
             'AND hospital_id IS ? AND started_at<=? AND started_at<=? '
             'AND (ended_at IS NULL OR ended_at>=?)')
    args = [pid, role, hospital_id, now, upper, lower]
    if actor is not None:
        query += ' AND actor_id=?'
        args.append(actor)
    records = conn.execute(query + ' ORDER BY id LIMIT ?', (*args, MAX_SUMMARY_ROWS + 1)).fetchall()
    service._require(len(records) <= MAX_SUMMARY_ROWS, 'TOO_MANY_RECORDS',
                     'Narrow the activity summary scope or time range')
    actors = {}
    for record in records:
        item = _record(record, now)
        end = item['ended_at']
        start_at, end_at = max(lower, item['started_at']), min(upper, end if end is not None else now)
        if end_at < start_at or (end_at == start_at and not lower <= item['started_at'] < upper):
            continue
        measured = max(0, end_at - start_at)
        entry = actors.setdefault(item['actor_id'], dict(actor_id=item['actor_id'],
            closed_activity_count=0, open_activity_count=0, completed_raw_seconds=0,
            open_raw_elapsed_seconds=0, closed_intervals=[], open_intervals=[], by_kind={}))
        kind_entry = entry['by_kind'].setdefault(item['kind'], dict(
            closed_activity_count=0, open_activity_count=0, completed_raw_seconds=0,
            open_raw_elapsed_seconds=0))
        if end is None:
            entry['open_activity_count'] += 1
            entry['open_raw_elapsed_seconds'] += measured
            entry['open_intervals'].append((start_at, end_at))
            kind_entry['open_activity_count'] += 1
            kind_entry['open_raw_elapsed_seconds'] += measured
        else:
            entry['closed_activity_count'] += 1
            entry['completed_raw_seconds'] += measured
            entry['closed_intervals'].append((start_at, end_at))
            kind_entry['closed_activity_count'] += 1
            kind_entry['completed_raw_seconds'] += measured
    items = []
    for key in sorted(actors):
        entry = actors[key]
        entry['completed_active_seconds'] = _union_seconds(entry.pop('closed_intervals'))
        entry['open_elapsed_union_seconds'] = _union_seconds(entry.pop('open_intervals'))
        entry['completed_overlap_seconds'] = entry['completed_raw_seconds'] - entry['completed_active_seconds']
        items.append(entry)
    totals = {key: sum(item[key] for item in items) for key in (
        'closed_activity_count', 'open_activity_count', 'completed_raw_seconds',
        'completed_active_seconds', 'completed_overlap_seconds', 'open_raw_elapsed_seconds',
        'open_elapsed_union_seconds')}
    return dict(scope=scope, actor_id=actor, measured_at=now,
                window=dict(since=lower, until=upper, interval='[since, until)'),
                units='person_seconds', totals=totals, actors=items,
                measurement=dict(source='explicit_server_timed_start_stop',
                                 open_intervals_in_completed_total=False,
                                 across_actor_totals_are_person_seconds=True,
                                 scope_is_single_patient=True,
                                 cross_patient_overlap_not_removed=True,
                                 kind_subtotals_are_raw_and_may_overlap=True,
                                 inferred_from_workflow_waiting=False))
