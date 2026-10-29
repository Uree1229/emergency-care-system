"""Hospital-wide call records without acquiring clinical or conversation scope.

This is a read-only inbox of records explicitly shared with one hospital. Record
IDs discover new records; confirmation changes require refreshing the list.
"""
from contextlib import contextmanager
import re

from . import call_records, service

STATUSES = frozenset(('all', 'unconfirmed', 'confirmed', 'disputed'))
AUTHOR_ROLES = frozenset(('all', 'paramedic', 'hospital'))


def _boolean(value, name):
    if isinstance(value, str):
        choices = {'true': True, 'false': False, '1': True, '0': False}
        service._require(value in choices, 'INVALID', f'{name} must be true or false')
        value = choices[value]
    service._require(type(value) is bool, 'INVALID', f'{name} must be true or false')
    return value


@contextmanager
def _read_snapshot(conn):
    """Keep filter results and the public confirmation view in one snapshot."""
    owned = not conn.in_transaction
    if owned:
        conn.execute('BEGIN')
    try:
        yield
    finally:
        if owned:
            conn.rollback()


def list_records(conn, now, hid, *, limit=50, before_id=None, after_id=None,
                 confirmation_status='all', current_only=True, patient_id=None, author_role='all'):
    """Return only the named hospital's records, with bounded ID pagination.

    after_id (including 0) selects ascending new-record discovery. Without it,
    records are newest first and before_id pages toward older records. The two
    cursor modes cannot be combined. Current and confirmation filters only use
    information recorded at or before now. Reading never marks any item seen.
    """
    now = call_records._integer(now, 'now')
    limit = call_records._integer(limit, 'limit', 1)
    service._require(limit <= call_records.MAX_PAGE, 'INVALID', 'limit must be 1 through 100')
    service._require(before_id is None or after_id is None, 'INVALID',
                     'before_id and after_id cannot be combined')
    before = None if before_id is None else call_records._integer(before_id, 'before_id', 1)
    after = None if after_id is None else call_records._integer(after_id, 'after_id')
    service._require(isinstance(confirmation_status, str) and confirmation_status in STATUSES,
                     'INVALID', 'Unknown confirmation_status')
    service._require(isinstance(author_role, str) and author_role in AUTHOR_ROLES,
                     'INVALID', 'Unknown author_role')
    current = _boolean(current_only, 'current_only')
    if patient_id is not None:
        service._require(isinstance(patient_id, str) and 0 < len(patient_id) <= 128 and
                         re.fullmatch(r'[\w-]+', patient_id) is not None,
                         'INVALID', 'patient_id must be a bounded case identifier')
    service._require(isinstance(hid, str) and 0 < len(hid) <= 128,
                     'INVALID', 'hospital_id required')

    with _read_snapshot(conn):
        service._hospital(conn, hid)
        sql = ('SELECT r.* FROM call_records r LEFT JOIN call_record_confirmations c '
               'ON c.record_id=r.id AND c.confirmed_at<=? '
               'WHERE r.hospital_id=? AND r.recorded_at<=?')
        args = [now, hid, now]
        if patient_id is not None:
            sql += ' AND r.patient_id=?'
            args.append(patient_id)
        if author_role != 'all':
            sql += ' AND r.author_role=?'
            args.append(author_role)
        if current:
            sql += (' AND NOT EXISTS(SELECT 1 FROM call_records successor '
                    'WHERE successor.supersedes_id=r.id AND successor.recorded_at<=?)')
            args.append(now)
        if confirmation_status == 'unconfirmed':
            sql += ' AND c.record_id IS NULL'
        elif confirmation_status in ('confirmed', 'disputed'):
            sql += ' AND c.decision=?'
            args.append('confirm' if confirmation_status == 'confirmed' else 'dispute')
        if before is not None:
            sql += ' AND r.id<?'
            args.append(before)
        if after is not None:
            sql += ' AND r.id>?'
            args.append(after)
        direction = 'ASC' if after is not None else 'DESC'
        rows = conn.execute(sql + f' ORDER BY r.id {direction} LIMIT ?',
                            (*args, limit + 1)).fetchall()
        more = len(rows) > limit
        rows = rows[:limit]
        records = [call_records._public(conn, now, row) for row in rows]

    return dict(records=records, has_more=more,
                next_before_id=rows[-1]['id'] if more and after is None else None,
                next_after_id=(rows[-1]['id'] if rows else after) if after is not None else None,
                cursor_mode='new_records' if after is not None else 'latest',
                scope=dict(hospital_id=hid, patient_id=patient_id),
                filters=dict(confirmation_status=confirmation_status, current_only=current,
                             author_role=author_role),
                stream=dict(confirmation_changes_require_refresh=True, read_marks_seen=False))
