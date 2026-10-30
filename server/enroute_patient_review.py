"""Separate, hospital-reviewed clinical updates for confirmed/in-transit fixtures.

The WAITING amendment/voice submit contract is unchanged. A request is a proposal,
not patient application, a hospital acceptance, or a destination change. Actor
and hospital IDs use this application's existing local demonstration contract.
"""
import copy
import hashlib
import json
import uuid

from . import db, service, voice_input as voice, voice_treatment_review as treatment
from . import transport_review, transport_reassignment, treatment_binding

REVISION = 'enroute-patient-review-20261030-v1'
TABLE = 'enroute_patient_reviews'
SCHEMA = (
    """CREATE TABLE IF NOT EXISTS enroute_patient_reviews (
       id TEXT PRIMARY KEY, patient_id TEXT NOT NULL REFERENCES patients(id),
       hospital_id TEXT NOT NULL REFERENCES hospitals(id), actor_id TEXT NOT NULL,
       session_id TEXT NOT NULL REFERENCES voice_sessions(id), session_version INTEGER NOT NULL,
       patient_version INTEGER NOT NULL, created_at INTEGER NOT NULL,
       proposal TEXT NOT NULL, patient_patch TEXT NOT NULL,
       state TEXT NOT NULL DEFAULT 'PENDING' CHECK(state IN
       ('PENDING','SUPERSEDED','REJECTED','APPLIED_KEEP','APPLIED_ADJUST')),
       version INTEGER NOT NULL DEFAULT 1, decided_at INTEGER, decided_by TEXT,
       decision_reason TEXT, applied_patient_version INTEGER)""",
    "CREATE UNIQUE INDEX IF NOT EXISTS enroute_pending_patient ON enroute_patient_reviews(patient_id) WHERE state='PENDING'",
    """CREATE TRIGGER IF NOT EXISTS enroute_proposal_immutable
       BEFORE UPDATE ON enroute_patient_reviews WHEN
       OLD.patient_id!=NEW.patient_id OR OLD.hospital_id!=NEW.hospital_id OR
       OLD.actor_id!=NEW.actor_id OR OLD.session_id!=NEW.session_id OR
       OLD.session_version!=NEW.session_version OR OLD.patient_version!=NEW.patient_version OR
       OLD.created_at!=NEW.created_at OR OLD.proposal!=NEW.proposal OR OLD.patient_patch!=NEW.patient_patch
       BEGIN SELECT RAISE(ABORT,'enroute proposal is immutable'); END""",
)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def exists(conn):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)).fetchone() is not None


def ensure_schema(conn):
    for statement in SCHEMA:
        conn.execute(statement)


def _reason(value):
    service._require(isinstance(value, str) and 0 < len(value.strip()) <= 500
        and not any(ord(c) < 32 or ord(c) == 127 for c in value),
        'INVALID', '변경 또는 판단 사유는 500자 이내의 설명입니다')
    return value.strip()


def _build(conn, now, sid, actor, selection=None):
    row, patient, proposal, override = treatment._build(conn, now, sid, actor, selection)
    service._require(patient['state'] in service.ACTIVE_TRANSPORT, 'INVALID_STATE',
        '목적지가 확정되었거나 이송 중인 환자에게 사용하는 별도 검토 절차입니다', 409)
    proposal = copy.deepcopy(proposal)
    proposal['blockers'] = [b for b in proposal['blockers'] if b != 'INVALID_STATE']
    proposal.update(revision=REVISION, destination_hospital_id=patient['destination_hospital_id'],
        operational_effect='proposal_only_until_current_hospital_decision',
        automatic_destination_change=False, can_apply=False,
        can_request=not proposal['blockers'])
    proposal['token'] = _digest(dict(base_token=proposal['token'], revision=REVISION,
        destination=patient['destination_hospital_id']))
    patch = voice._patient_patch(row, now, patient)
    if override['treatment_profile_id'] is None:
        patch['extra'].pop('treatment_profile_id', None)
    else:
        patch['extra']['treatment_profile_id'] = override['treatment_profile_id']
    patch.update({k: copy.deepcopy(override[k]) for k in ('required_capabilities', 'resource_demands') if k in override})
    cleaned = service._clean_patient(patch, partial=True)
    service._bind_treatment_fields(cleaned, patient)
    proposal['observation_changes'] = {key: dict(previous=patient.get(key), proposed=value)
        for key, value in patch.items() if key not in ('extra',) and patient.get(key) != value}
    proposal['proposed_observations'] = copy.deepcopy(patch.get('extra', {}))
    return row, patient, proposal, cleaned


def preview(conn, now, sid, actor, selection=None):
    voice._now(now)
    row, patient, proposal, _ = _build(conn, now, sid, voice._actor(actor), selection)
    return dict(review=proposal, session=voice.get(conn, now, row['id'], actor)['session'])


def preview_request(conn, now, sid, body):
    voice._shape(body, voice.COMMON, {'selection'})
    row = voice._row(conn, sid, voice._actor(body['actor_id']), now)
    voice._mutable(row, body['expected_version'])
    voice._identifier(body['client_request_id'], 'client_request_id')
    return preview(conn, now, sid, body['actor_id'], body.get('selection'))


def request(conn, now, sid, body):
    voice._shape(body, voice.COMMON | {'expected_patient_version', 'review_token', 'confirmed', 'selection', 'reason'},
                 {'allow_reduction'})
    service._require(body['confirmed'] is True, 'CONFIRMATION_REQUIRED', '변경안 전송을 명시적으로 확인하세요')
    service._require(type(body.get('allow_reduction', False)) is bool, 'INVALID', 'allow_reduction은 불리언입니다')
    reason = _reason(body['reason'])
    def action(actor):
        ensure_schema(conn)
        row, patient, proposal, cleaned = _build(conn, now, sid, actor, body['selection'])
        voice._mutable(row, body['expected_version'])
        service._require(type(body['expected_patient_version']) is int and
            body['expected_patient_version'] == patient['version'] == row['base_patient_version'],
            'ENROUTE_REVIEW_STALE', '환자정보가 바뀌었습니다. 최신 정보로 새 초안을 만드세요', 409)
        service._require(proposal['can_request'], 'VOICE_CONFIRMATION_REQUIRED', '현재 음성 초안을 먼저 확인하세요', 409)
        service._require(proposal['token'] == body['review_token'], 'ENROUTE_REVIEW_STALE', '변경안을 다시 조회하세요', 409)
        service._require(not proposal['reductions'] or body.get('allow_reduction') is True,
            'TREATMENT_REDUCTION_CONFIRMATION_REQUIRED', '필요조건 감소를 별도로 확인하세요', 409)
        proposal['request_reason'] = reason
        conn.execute("UPDATE enroute_patient_reviews SET state='SUPERSEDED',version=version+1 "
                     "WHERE patient_id=? AND state='PENDING'", (patient['id'],))
        rid = 'ER-' + uuid.uuid4().hex
        conn.execute('INSERT INTO enroute_patient_reviews '
            '(id,patient_id,hospital_id,actor_id,session_id,session_version,patient_version,created_at,proposal,patient_patch) '
            'VALUES(?,?,?,?,?,?,?,?,?,?)', (rid, patient['id'], patient['destination_hospital_id'], actor,
            sid, row['version'], patient['version'], now, _json(proposal), _json(cleaned)))
        service.log(conn, now, actor, 'enroute_patient_change_requested', patient['id'],
            patient['destination_hospital_id'], review_id=rid, source_session_id=sid,
            patient_version=patient['version'], reason=reason)
        return dict(review=get(conn, now, rid), patient=service.patient_view(conn, patient['id'], now),
                    operational_effect='hospital_review_requested_patient_unchanged')
    return voice._operate(conn, now, 'enroute_review_request:' + sid, body, action)


def _record(conn, rid):
    row = conn.execute('SELECT * FROM enroute_patient_reviews WHERE id=?', (rid,)).fetchone() if exists(conn) else None
    service._require(row is not None, 'NOT_FOUND', '변경 검토 기록을 찾을 수 없습니다', 404)
    return dict(row)


def _context(conn, now, row):
    patient = service._patient(conn, row['patient_id'])
    session = conn.execute('SELECT * FROM voice_sessions WHERE id=?', (row['session_id'],)).fetchone()
    proposal = json.loads(row['proposal'])
    blockers = []
    if patient['state'] not in service.ACTIVE_TRANSPORT: blockers.append('TRANSPORT_ENDED')
    if patient['destination_hospital_id'] != row['hospital_id']: blockers.append('DESTINATION_CHANGED')
    if patient['version'] != row['patient_version']: blockers.append('PATIENT_CHANGED')
    if session is None or session['state'] != 'DRAFT' or session['version'] != row['session_version']:
        blockers.append('VOICE_DRAFT_CHANGED')
    if session is not None and session['confirmed_version'] != row['session_version']:
        blockers.append('VOICE_CONFIRMATION_REQUIRED')
    if _digest(treatment_binding.catalog()) != proposal['catalog_sha256']: blockers.append('CATALOG_CHANGED')
    if proposal.get('unsupported_solver_resources'): blockers.append('UNSUPPORTED_SOLVER_RESOURCE')
    if row['state'] != 'PENDING': blockers.append('REVIEW_CLOSED')
    snapshot = transport_review._snapshot(conn, now, patient) if patient['destination_hospital_id'] else None
    if snapshot: snapshot.pop('captured_at', None)
    monitor = transport_review.view(conn, now, patient['id'])
    token = _digest(dict(review_id=row['id'], version=row['version'], patient_version=patient['version'],
        patient_state=patient['state'], destination=patient['destination_hospital_id'],
        session_version=session['version'] if session else None,
        hospital_snapshot=snapshot, monitor_enabled=bool(monitor['enabled']),
        monitor_version=monitor.get('version'),monitor_policy=monitor.get('policy'),
        monitor_state=monitor['state'],monitor_risk_signature=monitor.get('risk_signature'),
        proposal_sha256=_digest(proposal), blockers=blockers))
    return patient, blockers, token, monitor


def get(conn, now, rid, hospital_id=None):
    voice._now(now)
    row = _record(conn, rid)
    service._require(hospital_id is None or row['hospital_id'] == hospital_id,
        'NOT_FOUND', '이 병원의 검토 기록이 아닙니다', 404)
    patient, blockers, token, monitor = _context(conn, now, row)
    item = {k: v for k, v in row.items() if k != 'patient_patch'}
    item['proposal'] = json.loads(row['proposal'])
    item.update(blockers=blockers, can_decide=not blockers, decision_token=token,
        current_patient_version=patient['version'], current_destination_hospital_id=patient['destination_hospital_id'],
        monitoring_enabled=bool(monitor['enabled']), authentication='local_demo_actor_ids_not_login')
    return item


def list_reviews(conn, now, *, patient_id=None, hospital_id=None):
    voice._now(now)
    service._require((patient_id is None) != (hospital_id is None), 'INVALID', '환자 또는 병원 범위를 하나 지정하세요')
    if patient_id is not None: service._patient(conn, patient_id)
    if hospital_id is not None: service._hospital(conn, hospital_id)
    if not exists(conn): return {'reviews': []}
    field, value = ('patient_id', patient_id) if patient_id is not None else ('hospital_id', hospital_id)
    rows = conn.execute(f'SELECT id FROM enroute_patient_reviews WHERE {field}=? ORDER BY created_at DESC,rowid DESC LIMIT 100', (value,)).fetchall()
    return {'reviews': [get(conn, now, row['id'], hospital_id) for row in rows]}


def _can_keep(conn, now, patient, patch):
    proposed = copy.deepcopy(patient)
    proposed.update({k: json.loads(v) if k in ('extra','required_capabilities','resource_demands',
        'explicit_required_capabilities','explicit_resource_demands','danger_signs') else v for k,v in patch.items()})
    hospital = service._hospital(conn, patient['destination_hospital_id'])
    service._require(hospital.get('capabilities_status', 'known') == 'known' and
        set(proposed['required_capabilities']).issubset(hospital['capabilities']),
        'ENROUTE_KEEP_UNSUPPORTED', '변경된 진료조건을 현재 병원이 확인할 수 없습니다. 조정 필요를 검토하세요', 409)
    short, unknown = service.shortfalls(conn, hospital['id'], proposed['resource_demands'], exclude_patient=patient['id'])
    resources = {r['resource_type']: dict(r) for r in conn.execute('SELECT * FROM resources WHERE hospital_id=?', (hospital['id'],))}
    stale = any(now-resources[k]['updated_at'] > service.STALE_AFTER for k in proposed['resource_demands'] if k in resources)
    service._require(not short and not unknown and not stale and service.blocking_restriction(
        conn,hospital['id'],set(proposed['required_capabilities']),now) is None,
        'ENROUTE_KEEP_UNVERIFIED', '변경 조건의 자원·최신성·수용 제한을 다시 확인하세요', 409)


def decide(conn, now, rid, hid, body):
    voice._shape(body, {'actor_id','client_request_id','expected_review_version','expected_patient_version',
        'decision_token','decision','confirmed','reason'})
    service._require(body['confirmed'] is True, 'CONFIRMATION_REQUIRED', '현재 병원이 변경 내용을 명시적으로 확인해야 합니다')
    decision = body['decision']
    service._require(decision in ('keep','adjust','reject'), 'INVALID', '판단은 keep/adjust/reject입니다')
    reason = _reason(body['reason'])
    # Scope is checked before the shared idempotency cache can return a saved
    # result. Retrying a valid key through another hospital must never disclose
    # the original hospital's review/patient payload.
    service._require(_record(conn,rid)['hospital_id'] == hid, 'NOT_FOUND', '이 병원의 검토 기록이 아닙니다', 404)
    def action(actor):
        row = _record(conn, rid)
        service._require(row['hospital_id'] == hid, 'NOT_FOUND', '이 병원의 검토 기록이 아닙니다', 404)
        patient, blockers, token, monitor = _context(conn, now, row)
        service._require(type(body['expected_review_version']) is int and row['version'] == body['expected_review_version']
            and type(body['expected_patient_version']) is int and patient['version'] == body['expected_patient_version']
            and token == body['decision_token'], 'ENROUTE_REVIEW_STALE', '검토 조건이 바뀌었습니다. 다시 조회하세요', 409)
        service._require(row['state'] == 'PENDING', 'ENROUTE_REVIEW_CLOSED', '이미 종료된 검토입니다', 409)
        service._require(patient['destination_hospital_id'] == hid and patient['state'] in service.ACTIVE_TRANSPORT,
            'ENROUTE_REVIEW_STALE', '목적지 또는 이송 상태가 바뀌었습니다', 409)
        if decision == 'reject':
            conn.execute("UPDATE enroute_patient_reviews SET state='REJECTED',version=version+1,decided_at=?,"
                'decided_by=?,decision_reason=? WHERE id=?', (now,actor,reason,rid))
        else:
            service._require(not blockers, 'ENROUTE_REVIEW_STALE', '초안·환자·치료조건이 바뀌어 재검토가 필요합니다', 409, blockers=blockers)
            service._require(monitor['enabled'], 'REVIEW_DISABLED', '이송 조건 감시를 먼저 활성화하세요', 409)
            patch = json.loads(row['patient_patch'])
            if decision == 'keep': _can_keep(conn, now, patient, patch)
            transport_reassignment.stop_in_transaction(conn,now,patient['id'],'환자 상태 변경으로 이전 대체 문의 종료')
            conn.execute('UPDATE patients SET '+','.join(k+'=?' for k in patch)+',version=version+1 WHERE id=?',
                         (*patch.values(),patient['id']))
            for q in conn.execute('SELECT * FROM inquiries WHERE patient_id=?', (patient['id'],)).fetchall():
                if q['state'] in ('PENDING','CHECKING','ACCEPTED','CONFIRMED'):
                    conn.execute("UPDATE inquiries SET state='CANCELLED',requires_review_version=1,reason=?,version=version+1 WHERE id=?",
                                 ('병원 확인 후 환자 상태 변경 적용',q['id']))
                    service.log(conn,now,actor,'inquiry_closed',patient['id'],q['hospital_id'],inquiry_id=q['id'],reason='환자 상태 변경')
            transport_review.scan_in_transaction(conn,now,hid)
            current = transport_review.view(conn,now,patient['id'])
            service.hospital_decision(conn,now,patient['id'],hid,decision,note=reason,actor=actor,
                expected_review_version=current['version'],expected_risk_signature=current['risk_signature'])
            if decision == 'keep': transport_review.capture_baseline_in_transaction(conn,now,patient['id'])
            conn.execute("UPDATE voice_sessions SET state='SUBMITTED',submitted_at=?,updated_at=? WHERE id=?", (now,now,row['session_id']))
            final = service._patient(conn,patient['id'])
            conn.execute('UPDATE enroute_patient_reviews SET state=?,version=version+1,decided_at=?,decided_by=?,decision_reason=?,applied_patient_version=? WHERE id=?',
                ('APPLIED_'+decision.upper(),now,actor,reason,final['version'],rid))
            service.log(conn,now,actor,'patient_information_amended',patient['id'],hid,
                enroute_review_id=rid,changed_fields=sorted(patch),from_patient_version=patient['version'],to_patient_version=final['version'])
        service.log(conn,now,actor,'enroute_patient_change_decided',patient['id'],hid,review_id=rid,decision=decision,reason=reason)
        return dict(review=get(conn,now,rid,hid),patient=service.patient_view(conn,patient['id'],now),
            operational_effect='patient_unchanged' if decision=='reject' else 'hospital_reviewed_patient_change',
            automatic_destination_change=False)
    return voice._operate(conn,now,'enroute_review_decision:'+hid+':'+rid,body,action)
