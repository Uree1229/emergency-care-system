"""시스템 밖 유입의 개별 점유 수명. 공개 병상 관측값은 변경하지 않는다."""
import re
import json

from . import db, service, transport_review, resource_accounting

MAX_ID = (1 << 63) - 1


def _integer(value, name, minimum=1, maximum=MAX_ID):
    if isinstance(value, str) and len(value) <= 19 and re.fullmatch(r'[0-9]+', value):
        value = int(value)
    service._require(type(value) is int and minimum <= value <= maximum,
                     'INVALID', f'{name} 값이 올바르지 않습니다')
    return value


def _actor(actor):
    service._require(isinstance(actor, str) and 0 < len(actor.strip()) <= 80,
                     'INVALID', 'handled_by는 1~80자 문자열입니다')
    return actor.strip()


def _record(conn, hid, eid):
    eid = _integer(eid, 'external_arrival_id')
    record = _decoded(conn.execute('SELECT * FROM external_arrivals WHERE hospital_id=? AND id=?',
                                   (hid, eid)).fetchone())
    service._require(record is not None, 'NOT_FOUND', '이 병원의 외부 유입 기록이 없습니다', 404)
    return record


def _decoded(raw):
    if raw is None:
        return None
    record, errors = dict(raw), []
    for key in ('resource_demands', 'applied_resource_demands', 'applied_resource_epochs', 'request_payload'):
        if record[key] is None and key == 'request_payload':
            continue
        try:
            value = json.loads(record[key])
            if not isinstance(value, dict):
                raise ValueError()
            record[key] = value
        except (TypeError, ValueError):
            record[key] = None
            errors.append(key)
    if errors:
        record['data_errors'] = errors
    return record


def _public(record):
    return {key: value for key, value in record.items()
            if key not in ('request_payload', 'applied_resource_epochs')}


def _response(conn, now, hid, record, duplicate):
    return dict(external_arrival_id=record['id'], external_arrival=_public(record), duplicate=duplicate,
                affected=service.affected_patients(conn, now, hid), hospital=service.hospital_view(conn, hid, now))


def register(conn, now, hid, demands, occurred_at=None, note=None, actor='hospital', request_id=None):
    service._require(isinstance(demands, dict) and demands and all(
        isinstance(key, str) and 0 < len(key) <= 100 and key == key.strip() and
        type(value) is int and 0 < value <= MAX_ID for key, value in demands.items()),
        'INVALID', 'resource_demands는 {자원: 양의 정수}입니다')
    service._require(note is None or isinstance(note, str) and len(note) <= 2000,
                     'INVALID', 'note는 2000자 이하 문자열 또는 null입니다')
    if occurred_at is not None:
        service._require(type(occurred_at) is int, 'INVALID', 'occurred_at은 epoch 초 정수입니다')
        _integer(occurred_at, 'occurred_at', minimum=0)
    if request_id is not None:
        service._require(isinstance(request_id, str) and 0 < len(request_id) <= 128 and
                         request_id == request_id.strip(), 'INVALID', 'request_id는 1~128자 문자열입니다')
    actor = _actor(actor)
    demands = dict(sorted(demands.items()))
    # 생략된 발생시각은 null로 비교한다. 재전송 시 새 관측시각과 혼동하지 않는다.
    payload = dict(resource_demands=demands, occurred_at=occurred_at, note=note)
    with db.transaction(conn):
        service._hospital(conn, hid)
        if request_id is not None:
            old = _decoded(conn.execute('SELECT * FROM external_arrivals WHERE hospital_id=? AND request_id=?',
                                        (hid, request_id)).fetchone())
            if old is not None:
                service._require(old['request_payload'] == payload, 'CONFLICT',
                                 '같은 request_id의 내용이 다릅니다. 기존 기록을 조회하세요.', 409)
                return _response(conn, now, hid, old, True)
        ledger = resource_accounting.capture_and_apply(conn, now, hid, demands)
        applied, epochs = ledger['applied_resource_demands'], ledger['applied_resource_epochs']
        eid = conn.execute(
            'INSERT INTO external_arrivals (hospital_id,occurred_at,observed_at,resource_demands,note,state,'
            'registered_by,request_id,request_payload,applied_resource_demands,applied_resource_epochs) '
            "VALUES (?,?,?,?,?,'ACTIVE',?,?,?,?,?)",
            (hid, now if occurred_at is None else occurred_at, now, db.dumps(demands), note, actor,
             request_id, db.dumps(payload), db.dumps(applied), db.dumps(epochs))).lastrowid
        affected = service.affected_patients(conn, now, hid)
        service.log(conn, now, actor, 'external_arrival', hospital_id=hid, external_id=eid,
                    demands=demands, applied_resource_demands=applied,
                    affected=[a['patient']['id'] for a in affected])
        transport_review.scan_in_transaction(conn, now, hid)
        return _response(conn, now, hid, _record(conn, hid, eid), False)


def list_records(conn, now, hid, state='ACTIVE', limit=100, before_id=None):
    service._hospital(conn, hid)
    service._require(isinstance(state, str) and state in ('ACTIVE', 'DISCHARGED', 'LEGACY', 'RECONCILED', 'all'),
                     'INVALID', 'state는 ACTIVE, DISCHARGED, LEGACY, RECONCILED, all 중 하나입니다')
    limit = _integer(limit, 'limit', maximum=200)
    sql, args = 'SELECT * FROM external_arrivals WHERE hospital_id=?', [hid]
    if state != 'all':
        sql += ' AND state=?'
        args.append(state)
    if before_id is not None:
        sql += ' AND id<?'
        args.append(_integer(before_id, 'before_id'))
    args.append(limit + 1)
    records = [_decoded(record) for record in conn.execute(sql + ' ORDER BY id DESC LIMIT ?', args)]
    page = records[:limit]
    return dict(external_arrivals=[_public(record) for record in page],
                next_before_id=page[-1]['id'] if len(records) > limit else None)


def view(conn, now, hid, eid):
    service._hospital(conn, hid)
    return dict(external_arrival=_public(_record(conn, hid, eid)))


def discharge(conn, now, hid, eid, actor='hospital', expected_version=None):
    actor = _actor(actor)
    if expected_version is not None:
        service._require(type(expected_version) is int and 0 < expected_version <= MAX_ID,
                         'INVALID', 'expected_version은 양의 정수입니다')
    with db.transaction(conn):
        service._hospital(conn, hid)
        record = _record(conn, hid, eid)
        if record['state'] == 'DISCHARGED':
            return _response(conn, now, hid, record, True)
        service._require(record['state'] == 'ACTIVE', 'INVALID_STATE',
                         '과거 기록은 점유가 이미 정산됐을 수 있어 자동 차감할 수 없습니다', 409)
        service._require(expected_version is None or expected_version == record['version'], 'CONFLICT',
                         '외부 유입 기록이 변경됐습니다. 새로고침 후 다시 처리하세요.', 409)
        applied = record['applied_resource_demands']
        epochs = record['applied_resource_epochs']
        try:
            resource_accounting.release(conn, now, hid, applied, epochs)
        except service.ServiceError as error:
            if error.code == 'RESOURCE_CONFLICT':
                error.details['external_arrival_id'] = record['id']
            raise
        conn.execute("UPDATE external_arrivals SET state='DISCHARGED',discharged_at=?,discharged_by=?,"
                     'version=version+1 WHERE id=?', (now, actor, record['id']))
        service.log(conn, now, actor, 'external_arrival_discharged', hospital_id=hid,
                    external_id=record['id'], released_resource_demands=applied)
        transport_review.scan_in_transaction(conn, now, hid)
        return _response(conn, now, hid, _record(conn, hid, record['id']), False)
