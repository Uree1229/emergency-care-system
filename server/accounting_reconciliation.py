"""담당자가 이미 정산한 기록의 명시적 종결. 자원 합계를 다시 차감하지 않는다."""
import base64
import json
import re

from . import db, service

MAX_ID = (1 << 63) - 1


def _integer(value, name, maximum=MAX_ID):
    if isinstance(value, str) and len(value) <= 19 and re.fullmatch(r'[0-9]+', value):
        value = int(value)
    service._require(type(value) is int and 0 < value <= maximum,
                     'INVALID', f'{name}는 양의 정수입니다')
    return value


def _clean(payload):
    service._require(isinstance(payload, dict), 'INVALID', 'JSON 객체가 필요합니다')
    request_id = payload.get('request_id')
    service._require(isinstance(request_id, str) and 0 < len(request_id) <= 128 and
                     request_id == request_id.strip(), 'INVALID', 'request_id는 1~128자 문자열입니다')
    fields = dict(request_id=request_id)
    for name, maximum in (('handled_by', 80), ('reason', 2000)):
        value = payload.get(name)
        service._require(isinstance(value, str) and 0 < len(value.strip()) <= maximum,
                         'INVALID', f'{name}는 1~{maximum}자 문자열입니다')
        fields[name] = value.strip()
    version = payload.get('expected_version')
    service._require(type(version) is int and 0 < version <= MAX_ID,
                     'INVALID', 'expected_version은 양의 정수입니다')
    versions = payload.get('expected_resource_versions')
    service._require(isinstance(versions, dict) and all(
        isinstance(key, str) and key.strip() and type(value) is int and 0 < value <= MAX_ID
        for key, value in versions.items()),
        'INVALID', 'expected_resource_versions에 확인한 병원의 전체 자원 버전을 보내세요')
    service._require(payload.get('confirm_no_resource_release') is True,
                     'INVALID', '이미 정산됐으며 자원을 다시 차감하지 않음을 명시적으로 확인해야 합니다')
    fields.update(expected_version=version, expected_resource_versions=dict(sorted(versions.items())),
                  confirm_no_resource_release=True)
    return fields


def _raw_value(value):
    # 손상된 JSON 문자열도 해석하지 않고 보존한다. SQLite BLOB 역시 원문 복원이 가능하다.
    return {'sqlite_blob_base64': base64.b64encode(value).decode('ascii')} if isinstance(value, bytes) else value


def _receipt(record):
    receipt = {key: record[key] for key in (
        'id', 'hospital_id', 'target_kind', 'target_id', 'request_id', 'created_at', 'handled_by', 'reason',
        'expected_version', 'prior_state', 'prior_version', 'result_state', 'result_version')}
    receipt['resource_snapshot'] = [{key: value for key, value in resource.items() if key != 'occupancy_epoch'}
                                    for resource in json.loads(record['resource_snapshot'])]
    receipt.update(resources_changed=False, released_resource_demands={})
    return receipt


def _response(record, duplicate):
    target = dict(id=record['target_id'] if record['target_kind'] == 'patient' else int(record['target_id']),
                  hospital_id=record['hospital_id'], state=record['result_state'], version=record['result_version'],
                  accounting_reconciliation_id=record['id'])
    key = 'patient' if record['target_kind'] == 'patient' else 'external_arrival'
    if key == 'patient':
        target['destination_hospital_id'] = record['hospital_id']
    return dict(duplicate=duplicate, reconciliation=_receipt(record), resources_changed=False,
                released_resource_demands={}, **{key: target})


def _patient_cleanup(conn, now, patient, reconciliation_id, actor):
    from . import inquiry_workflow, transport_reassignment, transport_review
    pid = patient['id']
    reason = '담당자가 기존 자원 정산을 확인하여 기록을 종결했습니다.'
    conversation = db.ensure_conversation(conn, dict(patient), now)
    service._close_other_inquiries(conn, now, pid, -1, reason)
    inquiry_workflow.stop_in_transaction(conn, now, pid, reason=reason)
    # 감시 기록이 없는 과거 이송도 대체 문의 작업이 남지 않게 명시적으로 종료한다.
    transport_reassignment.stop_in_transaction(conn, now, pid, reason)
    transport_review.finish_in_transaction(conn, now, pid, reason)
    event_id = service.log(conn, now, actor, 'patient_accounting_reconciled', pid,
                           patient['destination_hospital_id'], reconciliation_id=reconciliation_id,
                           resources_changed=False, released_resource_demands={})
    db.close_conversation(conn, conversation['id'], now, event_id)


def reconcile(conn, now, hid, kind, target_id, payload):
    service._require(kind in ('patient', 'external_arrival'), 'INVALID', '정산 대상 종류가 올바르지 않습니다')
    if kind == 'external_arrival':
        target_id = str(_integer(target_id, 'external_arrival_id'))
    else:
        service._require(isinstance(target_id, str) and target_id, 'INVALID', '환자 ID가 필요합니다')
    fields = _clean(payload)
    comparison = dict(fields, target_kind=kind, target_id=target_id)
    with db.transaction(conn):
        service._hospital(conn, hid)
        previous = conn.execute('SELECT * FROM accounting_reconciliations WHERE hospital_id=? AND request_id=?',
                                (hid, fields['request_id'])).fetchone()
        if previous is not None:
            service._require(json.loads(previous['request_payload']) == comparison, 'CONFLICT',
                             '같은 request_id의 정산 대상이나 확인 내용이 다릅니다. 기존 처리 결과를 조회하세요.', 409)
            return _response(previous, True)
        if kind == 'patient':
            target = conn.execute('SELECT * FROM patients WHERE id=? AND destination_hospital_id=?',
                                  (target_id, hid)).fetchone()
            allowed, result_state = ('ARRIVED',), 'COMPLETED'
        else:
            target = conn.execute('SELECT * FROM external_arrivals WHERE id=? AND hospital_id=?',
                                  (int(target_id), hid)).fetchone()
            allowed, result_state = ('ACTIVE', 'LEGACY'), 'RECONCILED'
        service._require(target is not None, 'NOT_FOUND', '이 병원의 정산 대상 기록이 없습니다', 404)
        service._require(target['state'] in allowed, 'INVALID_STATE', '이미 종료됐거나 정산·종결할 수 없는 상태입니다', 409)
        service._require(type(target['version']) is int and fields['expected_version'] == target['version'] and
                         target['version'] < MAX_ID, 'CONFLICT', '대상 기록이 변경됐습니다. 새로고침 후 다시 확인하세요.', 409)
        resources = [dict(resource) for resource in conn.execute(
            'SELECT * FROM resources WHERE hospital_id=? ORDER BY resource_type', (hid,))]
        current_versions = {resource['resource_type']: resource['version'] for resource in resources}
        service._require(current_versions == fields['expected_resource_versions'], 'CONFLICT',
                         '확인한 자원 집계가 변경됐습니다. 전체 자원 상태를 다시 조회한 뒤 정산하세요.', 409)
        columns = ('arrival_accounting',) if kind == 'patient' else (
            'resource_demands', 'applied_resource_demands', 'applied_resource_epochs')
        original = {column: _raw_value(target[column]) for column in columns}
        result_version = target['version'] + 1
        rid = conn.execute(
            'INSERT INTO accounting_reconciliations (hospital_id,target_kind,target_id,request_id,created_at,'
            'handled_by,reason,expected_version,prior_state,prior_version,result_state,result_version,'
            'request_payload,original_accounting,resource_snapshot) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (hid, kind, target_id, fields['request_id'], now, fields['handled_by'], fields['reason'],
             fields['expected_version'], target['state'], target['version'], result_state, result_version,
             db.dumps(comparison), db.dumps(original), db.dumps(resources))).lastrowid
        if kind == 'patient':
            conn.execute("UPDATE patients SET state='COMPLETED',version=?,needs_adjustment=0,adjustment_note=NULL,"
                         'accounting_reconciliation_id=?,accounting_reconciled_at=?,accounting_reconciled_by=? WHERE id=?',
                         (result_version, rid, now, fields['handled_by'], target_id))
            _patient_cleanup(conn, now, target, rid, fields['handled_by'])
        else:
            conn.execute("UPDATE external_arrivals SET state='RECONCILED',version=?,accounting_reconciliation_id=?,"
                         'accounting_reconciled_at=?,accounting_reconciled_by=? WHERE id=?',
                         (result_version, rid, now, fields['handled_by'], int(target_id)))
            service.log(conn, now, fields['handled_by'], 'external_arrival_accounting_reconciled', hospital_id=hid,
                        external_id=int(target_id), reconciliation_id=rid, resources_changed=False,
                        released_resource_demands={})
        receipt = conn.execute('SELECT * FROM accounting_reconciliations WHERE id=?', (rid,)).fetchone()
        return _response(receipt, False)


def view(conn, now, hid, rid):
    service._hospital(conn, hid)
    rid = _integer(rid, 'reconciliation_id')
    record = conn.execute('SELECT * FROM accounting_reconciliations WHERE hospital_id=? AND id=?', (hid, rid)).fetchone()
    service._require(record is not None, 'NOT_FOUND', '이 병원의 정산 처리 기록이 없습니다', 404)
    return dict(reconciliation=_receipt(record))


def list_records(conn, now, hid, kind='all', limit=100, before_id=None):
    service._hospital(conn, hid)
    service._require(kind in ('patient', 'external_arrival', 'all'), 'INVALID', 'kind 값이 올바르지 않습니다')
    limit = _integer(limit, 'limit', maximum=200)
    sql, args = 'SELECT * FROM accounting_reconciliations WHERE hospital_id=?', [hid]
    if kind != 'all':
        sql += ' AND target_kind=?'
        args.append(kind)
    if before_id is not None:
        sql += ' AND id<?'
        args.append(_integer(before_id, 'before_id'))
    records = list(conn.execute(sql + ' ORDER BY id DESC LIMIT ?', (*args, limit + 1)))
    page = records[:limit]
    return dict(reconciliations=[_receipt(record) for record in page],
                next_before_id=page[-1]['id'] if len(records) > limit else None)
