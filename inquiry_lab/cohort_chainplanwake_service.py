"""Four explicit service entry points with local initial-workflow lookup.

Their SQL/validation/transaction bodies copy frozen service code. Other service
operations resolve to frozen implementations; no original globals change.
Only these four public entry points support local workflow-policy views. The
candidate-only physical engine uses other frozen low-level operations directly.
"""
import hashlib
from pathlib import Path
from server import db, service as foundation
from server.service import (_require,_bind_treatment_fields,_clean_patient,_patient,_inquiry,_hospital,
    transaction,rows,log,expire_acceptances,effective_priority,OPEN_INQUIRY,PENDING_WARN,RETRY_MIN,
    NOTIFY_TYPES,ACTIVE_TRANSPORT,_check_acceptance,_close_other_inquiries)

FOUNDATION_SHA256='d451a0dc2f3a8fd18f2ec5993ce7cf6c237b3bd75e90708cd4d460de3f82fd76'
if hashlib.sha256(Path(foundation.__file__).read_bytes()).hexdigest()!=FOUNDATION_SHA256:raise ValueError('frozen decline-wake service foundation differs')


def __getattr__(name):
    return getattr(foundation,name)


def create_patient(conn, now, pid, payload, actor='patient_web'):
    _require('auto_inquiry' not in payload or type(payload['auto_inquiry']) is bool,
             'INVALID', 'auto_inquiry는 true 또는 false입니다')
    if 'inquiry_policy' in payload:
        from .cohort_chainplanwake_workflow import _spec
        _spec(payload['inquiry_policy'])
    fields = _bind_treatment_fields(_clean_patient(payload))
    _require(isinstance(pid, str) and pid, 'INVALID', '환자 ID 필요')
    fields.update(id=pid, requested_at=now, state='WAITING')
    with transaction(conn):
        _require(conn.execute('SELECT 1 FROM patients WHERE id=?', (pid,)).fetchone() is None,
                 'DUPLICATE', '이미 있는 환자 ID', 409)
        cols = ', '.join(fields)
        conn.execute(f'INSERT INTO patients ({cols}) VALUES ({",".join("?" * len(fields))})', tuple(fields.values()))
        log(conn, now, actor, 'patient_created', pid, danger=bool(payload.get('danger_signs')))
        db.ensure_conversation(conn, _patient(conn, pid), now)
        if payload.get('auto_inquiry', False) or 'inquiry_policy' in payload:
            from . import cohort_chainplanwake_workflow as inquiry_workflow
            inquiry_workflow.attach(conn, now, pid, payload.get('inquiry_policy', 'fixed_3'),
                                    enabled=payload.get('auto_inquiry', False))
    return patient_view(conn, pid, now)


def patient_view(conn, pid, now):
    expire_acceptances(conn, now)
    p = _patient(conn, pid)
    p['effective_priority'] = effective_priority(p)
    p['call_119'] = bool(p['danger_signs']) or p.get('consciousness') == 'unresponsive' or p.get('breathing') in ('abnormal', 'absent')
    inquiries = rows(conn.execute('SELECT i.*, h.name AS hospital_name, h.phone AS hospital_phone '
                                  'FROM inquiries i JOIN hospitals h ON h.id=i.hospital_id '
                                  'WHERE patient_id=? ORDER BY i.id', (pid,)))
    for q in inquiries:
        q['elapsed_seconds'] = now - q['last_sent_at']
        q['overdue'] = q['state'] in OPEN_INQUIRY and q['elapsed_seconds'] >= PENDING_WARN
        q['can_retry'] = q['state'] in OPEN_INQUIRY and q['elapsed_seconds'] >= RETRY_MIN
        q['usable'] = q['state'] == 'ACCEPTED' and q['patient_version'] == p['version']
    p['inquiries'] = inquiries
    from . import cohort_chainplanwake_workflow as inquiry_workflow
    from server import transport_review, transport_reassignment
    p['inquiry_workflow'] = inquiry_workflow.view(conn, now, pid)
    p['transport_review'] = transport_review.view(conn, now, pid)
    p['transport_inquiry_workflow'] = transport_reassignment.view(conn, now, pid)
    if p['destination_hospital_id']:
        h = _hospital(conn, p['destination_hospital_id'])
        p['destination'] = dict(id=h['id'], name=h['name'], address=h['address'], phone=h['phone'],
                                lat=h['lat'], lon=h['lon'], source=h['source'])
    p['notifications'] = rows(conn.execute(
        f"SELECT id, at, hospital_id, type, data FROM events WHERE patient_id=? AND type IN {NOTIFY_TYPES} "
        f"ORDER BY id DESC LIMIT 20", (pid,)))
    p.pop('arrival_accounting', None)  # 내부 정산 기준은 화면과 대화에 노출하지 않는다.
    return p


def confirm_destination(conn, now, pid, iid, actor='patient_web'):
    """수용 확인된 병원 한 곳을 목적지로 확정하고 다른 문의를 닫는다 (P27, P28, S05, S07)."""
    with transaction(conn):
        p = _patient(conn, pid)
        _require(p['state'] == 'WAITING', 'INVALID_STATE', '이미 목적지가 정해졌습니다', 409, state=p['state'])
        q = _inquiry(conn, iid)
        _check_acceptance(conn, now, p, q)
        conversation = db.ensure_conversation(conn, p, now)
        conn.execute("UPDATE patients SET state='CONFIRMED', destination_hospital_id=?, version=version+1 "
                     "WHERE id=? AND version=?", (q['hospital_id'], pid, p['version']))
        conn.execute("UPDATE inquiries SET state='CONFIRMED', version=version+1 WHERE id=?", (iid,))
        _close_other_inquiries(conn, now, pid, iid, '다른 병원 확정')
        from . import cohort_chainplanwake_workflow as inquiry_workflow
        inquiry_workflow.stop_in_transaction(conn, now, pid, reason='목적지가 확정됐습니다.')
        event_id = log(conn, now, actor, 'destination_confirmed', pid, q['hospital_id'], inquiry_id=iid)
        db.join_conversation_hospital(conn, conversation['id'], now, q['hospital_id'], iid,
                                      event_id, _patient(conn, pid))
        from server import transport_review
        transport_review.capture_baseline_in_transaction(conn, now, pid)
    return patient_view(conn, pid, now)


def cancel_patient(conn, now, pid, reason, actor='patient_web'):
    """이송 취소. 도착 예정 수요는 상태 변경과 함께 자동으로 빠진다 (P41, H28)."""
    _require(reason, 'INVALID', '취소 사유를 입력하세요')
    with transaction(conn):
        p = _patient(conn, pid)
        _require(p['state'] in ('WAITING',) + ACTIVE_TRANSPORT, 'INVALID_STATE', '취소할 수 없는 상태입니다', 409)
        conversation = db.ensure_conversation(conn, p, now)
        conn.execute("UPDATE patients SET state='CANCELLED', closed_reason=?, version=version+1 WHERE id=?",
                     (reason, pid))
        _close_other_inquiries(conn, now, pid, -1, '환자 측 취소')
        from . import cohort_chainplanwake_workflow as inquiry_workflow
        inquiry_workflow.stop_in_transaction(conn, now, pid, reason='환자 이송이 취소됐습니다.')
        event_id = log(conn, now, actor, 'patient_cancelled', pid, p['destination_hospital_id'], reason=reason)
        db.close_conversation(conn, conversation['id'], now, event_id)
        from server import transport_review
        transport_review.finish_in_transaction(conn, now, pid, '환자 이송이 취소됐습니다.')
    return patient_view(conn, pid, now)
