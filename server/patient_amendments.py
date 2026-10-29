"""Versioned patient amendments before destination confirmation.

An amendment closes obsolete inquiries; it does not accept, dispatch or redirect.
The audit stores only changed fields, not a duplicate of the full patient record.
Caller actor IDs follow the existing demonstration contract, not authentication.
"""
import hashlib
import json
import re

from . import db, inquiry_workflow, service

MAX_ID = (1 << 63) - 1
MAX_CHANGE_JSON = 16384
ALLOWED_CHANGES = frozenset((
    'relation', 'contact', 'symptoms', 'name', 'address', 'guardian', 'guardian_phone',
    'birth', 'age', 'onset_at', 'extra', 'danger_signs', 'required_capabilities',
    'resource_demands', 'priority', 'priority_source', 'consciousness', 'breathing',
    'treatment_duration_seconds', 'max_wait_seconds',
))
JSON_PATIENT_FIELDS = frozenset((
    'extra', 'danger_signs', 'required_capabilities', 'resource_demands',
    'explicit_required_capabilities', 'explicit_resource_demands',
))
SET_FIELDS = frozenset(('danger_signs', 'required_capabilities', 'explicit_required_capabilities'))

SCHEMA = """
CREATE TABLE IF NOT EXISTS patient_amendments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id TEXT NOT NULL REFERENCES patients(id),
    actor_id TEXT NOT NULL,
    client_request_id TEXT NOT NULL,
    recorded_at INTEGER NOT NULL,
    reason TEXT NOT NULL,
    from_version INTEGER NOT NULL,
    to_version INTEGER NOT NULL,
    changed_fields TEXT NOT NULL,
    before_data TEXT NOT NULL,
    after_data TEXT NOT NULL,
    affected_hospital_ids TEXT NOT NULL,
    closed_inquiry_ids TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL,
    UNIQUE(actor_id,client_request_id),
    CHECK(recorded_at>=0 AND from_version>=1 AND to_version=from_version+1)
);
CREATE INDEX IF NOT EXISTS patient_amendments_patient ON patient_amendments(patient_id,id);
CREATE TRIGGER IF NOT EXISTS patient_amendments_no_update BEFORE UPDATE ON patient_amendments
    BEGIN SELECT RAISE(ABORT, 'patient amendment is immutable'); END;
CREATE TRIGGER IF NOT EXISTS patient_amendments_no_delete BEFORE DELETE ON patient_amendments
    BEGIN SELECT RAISE(ABORT, 'patient amendment is immutable'); END;
CREATE TRIGGER IF NOT EXISTS patient_amendments_no_replace BEFORE INSERT ON patient_amendments
    WHEN EXISTS (SELECT 1 FROM patient_amendments WHERE id=NEW.id OR
        (actor_id=NEW.actor_id AND client_request_id=NEW.client_request_id))
    BEGIN SELECT RAISE(ABORT, 'patient amendment is immutable'); END;
"""


def _integer(value, name, minimum=0):
    if isinstance(value, str) and len(value) <= 19 and re.fullmatch(r'[0-9]+', value):
        value = int(value)
    service._require(type(value) is int and minimum <= value <= MAX_ID,
                     'INVALID', f'{name} must be a bounded integer')
    return value


def _identifier(value, name, maximum=80):
    service._require(isinstance(value, str) and 1 <= len(value) <= maximum and
                     re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:@-]*', value) is not None,
                     'INVALID', f'{name} must be an opaque identifier within {maximum} characters')
    return value


def _canonical(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        raise service.ServiceError('INVALID', 'changes must contain finite JSON values', 400) from None


def _public(record):
    item = dict(record)
    item.pop('request_fingerprint', None)
    for key in ('changed_fields', 'affected_hospital_ids', 'closed_inquiry_ids'):
        item[key] = json.loads(item[key])
    item['before'] = json.loads(item.pop('before_data'))
    item['after'] = json.loads(item.pop('after_data'))
    item['operational_effect'] = 'obsolete_inquiries_closed'
    return item


def _typed(key, value):
    return json.loads(value) if key in JSON_PATIENT_FIELDS and isinstance(value, str) else value


def _equal(key, first, second):
    if key in SET_FIELDS and isinstance(first, list) and isinstance(second, list):
        return set(first) == set(second)
    return first == second


def amend(conn, now, pid, payload):
    now = _integer(now, 'now')
    service._require(isinstance(pid, str) and 1 <= len(pid) <= 128, 'INVALID', 'patient_id required')
    required = {'actor_id', 'client_request_id', 'expected_patient_version', 'reason', 'changes'}
    service._require(isinstance(payload, dict) and payload.keys() == required,
                     'INVALID', 'Unexpected or missing amendment fields')
    actor = _identifier(payload['actor_id'], 'actor_id')
    request_id = _identifier(payload['client_request_id'], 'client_request_id', 128)
    expected = payload['expected_patient_version']
    service._require(type(expected) is int and 1 <= expected <= MAX_ID,
                     'INVALID', 'expected_patient_version must be a positive integer')
    reason = payload['reason']
    service._require(isinstance(reason, str) and 0 < len(reason.strip()) <= 500 and len(reason) <= 500
                     and not any(ord(c) < 32 or ord(c) == 127 for c in reason),
                     'INVALID', 'reason must be nonempty text within 500 characters')
    changes = payload['changes']
    service._require(isinstance(changes, dict) and changes and not (changes.keys() - ALLOWED_CHANGES),
                     'INVALID', 'Only supported patient fields may be amended')
    encoded = _canonical(changes)
    service._require(len(encoded) <= MAX_CHANGE_JSON, 'INVALID', 'changes payload is too large')
    fields = service._clean_patient(changes, partial=True)
    service._require(all(type(value) is not int or 0 <= value <= MAX_ID for value in fields.values()),
                     'INVALID', 'Numeric patient fields exceed the database integer range')
    if 'resource_demands' in changes:
        service._require(all(value <= MAX_ID for value in changes['resource_demands'].values()),
                         'INVALID', 'Resource demand exceeds the database integer range')
    fingerprint = hashlib.sha256(_canonical(dict(patient_id=pid, expected_patient_version=expected,
                                                 reason=reason, changes=changes)).encode('utf-8')).hexdigest()
    with db.transaction(conn):
        existing = conn.execute('SELECT * FROM patient_amendments WHERE actor_id=? AND client_request_id=?',
                                 (actor, request_id)).fetchone()
        if existing is not None:
            service._require(existing['request_fingerprint'] == fingerprint, 'CONFLICT',
                             'The same amendment key cannot be reused with different content', 409)
            service._require(existing['recorded_at'] <= now, 'CLOCK_CONFLICT',
                             'The original amendment is later than the evaluation clock', 409)
            return dict(patient=service.patient_view(conn, pid, now), amendment=_public(existing), duplicate=True)
        patient = service._patient(conn, pid)
        service._require(patient['state'] == 'WAITING', 'INVALID_STATE',
                         'Patient amendments are supported before destination confirmation only', 409)
        service._require(patient['version'] == expected, 'CONFLICT',
                         'Patient information changed; reload before amending', 409,
                         current_patient_version=patient['version'])
        service._require(patient['requested_at'] <= now, 'CLOCK_CONFLICT',
                         'Amendment clock precedes patient registration', 409)
        job = inquiry_workflow._job(conn, pid)
        if job is not None:
            inquiry_workflow._policy(job)  # Validate frozen policy before any mutation.
        if set(fields) & {'extra', 'required_capabilities', 'resource_demands'}:
            service._bind_treatment_fields(fields, patient)
        typed = {key: _typed(key, value) for key, value in fields.items()}
        changed = {key: value for key, value in fields.items()
                   if not _equal(key, typed[key], patient.get(key))}
        service._require(changed, 'NO_CHANGE', 'The amendment does not change patient information')
        before = {key: patient.get(key) for key in sorted(changed)}
        after = {key: typed[key] for key in sorted(changed)}
        inquiries = inquiry_workflow._inquiries(conn, pid)
        affected = sorted({q['hospital_id'] for q in inquiries})
        closed_ids = [q['id'] for q in inquiries if q['state'] in service.OPEN_INQUIRY + ('ACCEPTED',)]
        assignments = ','.join(f'{key}=?' for key in changed)
        conn.execute(f'UPDATE patients SET {assignments},version=version+1 WHERE id=?',
                     (*changed.values(), pid))
        for inquiry in inquiries:
            if inquiry['id'] in closed_ids:
                conn.execute("UPDATE inquiries SET state='CANCELLED',reason=?,requires_review_version=1,"
                             'version=version+1 WHERE id=?', ('환자정보 변경으로 재확인 필요', inquiry['id']))
                service.log(conn, now, actor, 'inquiry_closed', pid, inquiry['hospital_id'],
                            inquiry_id=inquiry['id'], reason='환자정보 변경으로 재확인 필요',
                            from_patient_version=expected, to_patient_version=expected + 1)
            else:
                conn.execute('UPDATE inquiries SET requires_review_version=1,version=version+1 WHERE id=?',
                             (inquiry['id'],))
        if job is not None and job['enabled']:
            conn.execute("UPDATE inquiry_workflows SET state='QUEUED',next_evaluation_at=?,updated_at=?,"
                         'version=version+1,last_error=NULL,stop_reason=NULL WHERE patient_id=?', (now, now, pid))
        record_id = conn.execute('INSERT INTO patient_amendments '
            '(patient_id,actor_id,client_request_id,recorded_at,reason,from_version,to_version,changed_fields,'
            'before_data,after_data,affected_hospital_ids,closed_inquiry_ids,request_fingerprint) '
            'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (pid, actor, request_id, now, reason, expected, expected + 1, db.dumps(sorted(changed)),
             db.dumps(before), db.dumps(after), db.dumps(affected), db.dumps(closed_ids), fingerprint)).lastrowid
        for hospital in affected or [None]:
            service.log(conn, now, actor, 'patient_information_amended', pid, hospital,
                        amendment_id=record_id, changed_fields=sorted(changed),
                        from_version=expected, to_version=expected + 1)
        record = conn.execute('SELECT * FROM patient_amendments WHERE id=?', (record_id,)).fetchone()
        return dict(patient=service.patient_view(conn, pid, now), amendment=_public(record), duplicate=False)


def list_records(conn, now, pid, limit=50, before_id=None):
    now, limit = _integer(now, 'now'), _integer(limit, 'limit', 1)
    service._require(limit <= 100, 'INVALID', 'limit must be between 1 and 100')
    before = None if before_id is None else _integer(before_id, 'before_id', 1)
    service._patient(conn, pid)
    sql, args = 'SELECT * FROM patient_amendments WHERE patient_id=? AND recorded_at<=?', [pid, now]
    if before is not None:
        sql += ' AND id<?'
        args.append(before)
    rows = conn.execute(sql + ' ORDER BY id DESC LIMIT ?', (*args, limit + 1)).fetchall()
    more = len(rows) > limit
    rows = rows[:limit]
    return dict(amendments=[_public(row) for row in rows], has_more=more,
                next_before_id=rows[-1]['id'] if more else None)
