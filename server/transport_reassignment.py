"""수용 확정 이후 조정 요청에 대한 별도의 대체병원 문의 작업.

병원이 기존 수용 유지가 어렵다고 명시적으로 판단한 뒤에만 실행한다. 기존
목적지·수용 기록을 보존하며, 새 병원의 유효한 수용 회신을 확보해도 목적지를
자동 변경하지 않는다. 120초 위치 신선도와 문의 수/시간 한도는 운영 설정값이다.
"""
import math
import time

from . import db, service, inquiry_policies, inquiry_observations, inquiry_workflow

POSITION_MAX_AGE_SECONDS = 120
READY_POLL_SECONDS = 1
STATES = {'QUEUED', 'RUNNING', 'AWAITING_POSITION', 'AWAITING_CHANGE',
          'EXHAUSTED', 'PAUSED', 'COMPLETED', 'ERROR'}


def _job(conn, pid):
    return db.row(conn.execute('SELECT * FROM transport_inquiry_workflows WHERE patient_id=?',
                               (pid,)).fetchone())


def _review(conn, pid):
    return db.row(conn.execute('SELECT * FROM transport_reviews WHERE patient_id=?',
                               (pid,)).fetchone())


def _spec(name):
    try:
        return inquiry_policies.spec(name)
    except ValueError as error:
        raise service.ServiceError('INVALID_POLICY', str(error), 400) from None


def _policy(job):
    policy = _spec(job['policy_name'] if job else 'fixed_3')
    if job:
        service._require(job['policy_version'] == policy['version'] and
                         job['policy_config'] == policy,
                         'POLICY_VERSION_MISMATCH',
                         '저장된 대체병원 문의 정책이 현재 코드와 다릅니다. 담당자의 확인이 필요합니다.', 409)
    return policy


def _set_review(conn, now, job, state):
    """같은 목적지의 활성 모니터링 상태만 갱신한다."""
    review = _review(conn, job['patient_id'])
    if (review and review['enabled'] and
            review['hospital_id'] == job['source_hospital_id'] and review['state'] != state):
        conn.execute('UPDATE transport_reviews SET state=?,updated_at=?,version=version+1 '
                     'WHERE patient_id=?', (state, now, job['patient_id']))


def start_in_transaction(conn, now, pid, policy_name='fixed_3'):
    """병원의 명시적 adjust 판단과 같은 쓰기 트랜잭션에서 시작한다.

    이미 진행 중인 같은 작업은 마감 시각과 문의 예산을 유지한다. 초기 문의의
    병원 수는 새 문의 예산에 포함하지 않으며, 이전에 닫힌 문의 행은 새 환자
    버전으로 재문의할 수 있다.
    """
    patient = service._patient(conn, pid)
    review = _review(conn, pid)
    service._require(patient['state'] in service.ACTIVE_TRANSPORT and patient['needs_adjustment'],
                     'NOT_REQUESTED', '병원의 조정 필요 판단 후에만 대체병원을 자동 탐색합니다.', 409)
    service._require(review and review['enabled'] and
                     review['hospital_id'] == patient['destination_hospital_id'],
                     'REVIEW_DISABLED', '이 이송 건의 수용 조건 모니터링을 먼저 활성화하세요.', 409)
    policy = _spec(policy_name)
    old = _job(conn, pid)
    if old and old['enabled'] and old['source_hospital_id'] == patient['destination_hospital_id']:
        existing_policy = _policy(old)
        service._require(existing_policy == policy, 'POLICY_ACTIVE',
                         '실행 중 대체병원 문의 정책은 바꿀 수 없습니다. 먼저 일시정지하세요.', 409)
        _set_review(conn, now, old, 'AWAITING_CHANGE' if old['state'] == 'AWAITING_CHANGE' else 'SEARCHING')
        return _job(conn, pid)
    if old and old['enabled']:
        stop_in_transaction(conn, now, pid, '기존 목적지가 변경되어 이전 대체병원 문의를 종료했습니다.')
    watermark = conn.execute('SELECT COALESCE(MAX(id),0) FROM events').fetchone()[0]
    values = (1, 'QUEUED', now, now + policy['deadline_seconds'], now, now,
              patient['destination_hospital_id'], watermark,
              policy['name'], policy['version'], db.dumps(policy))
    if old:
        conn.execute('UPDATE transport_inquiry_workflows SET enabled=?,state=?,started_at=?,deadline_at=?, '
                     'next_evaluation_at=?,updated_at=?,source_hospital_id=?,episode_start_event=?, '
                     'policy_name=?,policy_version=?,policy_config=?,round_no=0,version=version+1, '
                     'last_error=NULL,stop_reason=NULL WHERE patient_id=?', (*values, pid))
    else:
        conn.execute('INSERT INTO transport_inquiry_workflows '
                     '(enabled,state,started_at,deadline_at,next_evaluation_at,updated_at,source_hospital_id, '
                     'episode_start_event,policy_name,policy_version,policy_config,patient_id,round_no,version) '
                     'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0,1)', (*values, pid))
    job = _job(conn, pid)
    _set_review(conn, now, job, 'SEARCHING')
    service.log(conn, now, 'system', 'transport_inquiry_started', pid, job['source_hospital_id'],
                episode_start_event=watermark, policy_name=policy['name'], policy_label=policy['label'],
                batch_size=policy['batch_size'], max_contacts=policy['max_contacts'],
                deadline_seconds=policy['deadline_seconds'],
                note='기존 목적지를 유지한 채 대체병원 문의를 시작합니다. 목적지 변경은 별도 판단입니다.')
    return _job(conn, pid)


def stop_in_transaction(conn, now, pid, reason, state='COMPLETED'):
    """명시적 종료는 episode 문의를 닫고, 한도/오류 종료는 늦은 회신을 보존한다."""
    service._require(state in STATES, 'INVALID', '대체병원 자동 문의 상태 오류')
    job = _job(conn, pid)
    if not job:
        return None
    if job['enabled'] or job['state'] != state or job['stop_reason'] != reason:
        conn.execute('UPDATE transport_inquiry_workflows SET enabled=0,state=?,stop_reason=?,updated_at=?, '
                     'version=version+1 WHERE patient_id=?', (state, reason, now, pid))
    if state in ('COMPLETED', 'PAUSED'):
        patient = service._patient(conn, pid)
        for inquiry in _episode_inquiries(conn, job):
            if (inquiry['state'] in service.OPEN_INQUIRY + ('ACCEPTED',) and
                    inquiry['hospital_id'] != patient['destination_hospital_id']):
                conn.execute("UPDATE inquiries SET state='CANCELLED',reason=?,version=version+1 WHERE id=?",
                             (reason, inquiry['id']))
                service.log(conn, now, 'system', 'inquiry_closed', pid, inquiry['hospital_id'],
                            inquiry_id=inquiry['id'], reason=reason, workflow='transport_reassignment')
    return _job(conn, pid)


def _episode_inquiries(conn, job):
    """동일 초에 재사용된 기존 문의도 사건 ID로 새 episode에 연결한다."""
    if not job:
        return []
    return db.rows(conn.execute(
        "SELECT i.* FROM inquiries i WHERE i.patient_id=? AND i.hospital_id!=? AND EXISTS "
        "(SELECT 1 FROM events e WHERE e.id>? AND e.patient_id=i.patient_id "
        "AND e.hospital_id=i.hospital_id AND e.type IN ('inquiry_sent','inquiry_retried')) ORDER BY i.id",
        (job['patient_id'], job['source_hospital_id'], job['episode_start_event'])))


def _valid_acceptances(conn, now, patient, inquiries):
    return inquiry_workflow._valid_acceptances(conn, now, patient,
        [q for q in inquiries if q['hospital_id'] != patient['destination_hospital_id']])


def _current_pending(patient, inquiries):
    return [q for q in inquiries if q['state'] in service.OPEN_INQUIRY and
            q['patient_version'] == patient['version']]


def _position_ready(patient, now):
    if any(type(patient.get(key)) not in (int, float) or
           not math.isfinite(patient[key]) or abs(patient[key]) > limit
           for key, limit in (('lat', 90), ('lon', 180))):
        return False
    if patient['state'] == 'IN_TRANSIT':
        updated = patient.get('location_updated_at')
        return type(updated) is int and 0 <= now - updated <= POSITION_MAX_AGE_SECONDS
    return True


def view(conn, now, pid):
    """조회는 후보 계산, 문의 송신, 만료 처리나 모니터링 시작을 하지 않는다."""
    patient = service._patient(conn, pid)
    job = _job(conn, pid)
    inquiries = _episode_inquiries(conn, job)
    out = dict(job) if job else dict(patient_id=pid, enabled=0, state='NOT_STARTED')
    latest = None
    if job:
        latest = db.row(conn.execute("SELECT * FROM events WHERE patient_id=? AND id>? "
                                    "AND type='transport_inquiry_decision' ORDER BY id DESC LIMIT 1",
                                     (pid, job['episode_start_event'])).fetchone())
    out.update(enabled=bool(out['enabled']), policy=_policy(job), latest_decision=latest,
               contacted_count=len(inquiries), contacted_hospital_ids=[q['hospital_id'] for q in inquiries],
               pending_count=len(_current_pending(patient, inquiries)),
               valid_accepted_inquiry_ids=_valid_acceptances(conn, now, patient, inquiries),
               position_ready=_position_ready(patient, now),
               position_max_age_seconds=POSITION_MAX_AGE_SECONDS,
               note='대체병원 수용 회신 후 담당자가 목적지를 변경합니다. 현재 목적지는 자동으로 바꾸지 않습니다.')
    return out


def _revision(conn):
    return conn.execute('SELECT revision FROM planning_state WHERE id=1').fetchone()[0]


def _authorized(patient, review, job):
    return (patient['state'] in service.ACTIVE_TRANSPORT and patient['needs_adjustment'] and
            patient['destination_hospital_id'] == job['source_hospital_id'] and review and
            review['enabled'] and review['hospital_id'] == job['source_hospital_id'])


def _update_wait(conn, now, job, next_at, state='RUNNING', reason=None):
    if (job['state'] == state and job['next_evaluation_at'] == next_at and
            job['stop_reason'] == reason):
        return False
    changed_state = job['state'] != state or job['stop_reason'] != reason
    conn.execute('UPDATE transport_inquiry_workflows SET state=?,next_evaluation_at=?,stop_reason=?, '
                 'updated_at=?,version=version+1,last_error=NULL WHERE patient_id=?',
                 (state, next_at, reason, now, job['patient_id']))
    if state == 'RUNNING':
        _set_review(conn, now, job, 'SEARCHING')
    if changed_state and reason:
        service.log(conn, now, 'system', 'transport_inquiry_waiting', job['patient_id'],
                    job['source_hospital_id'], state=state, note=reason, next_evaluation_at=next_at)
    return True


def _ready(conn, now, job, accepted):
    if job['state'] == 'AWAITING_CHANGE':
        return False
    conn.execute("UPDATE transport_inquiry_workflows SET state='AWAITING_CHANGE',next_evaluation_at=?, "
                 'updated_at=?,version=version+1,last_error=NULL,stop_reason=NULL WHERE patient_id=?',
                 (now + READY_POLL_SECONDS, now, job['patient_id']))
    _set_review(conn, now, job, 'AWAITING_CHANGE')
    service.log(conn, now, 'system', 'transport_inquiry_ready', job['patient_id'],
                job['source_hospital_id'], accepted_inquiry_ids=accepted,
                note='대체병원의 유효한 수용 회신을 확보했습니다. 기존 목적지는 유지하며 담당자의 변경 판단을 기다립니다.')
    return True


def _exhaust(conn, now, job, code, reason, state='EXHAUSTED', last_error=None):
    stop_in_transaction(conn, now, job['patient_id'], reason, state)
    if last_error is not None:
        conn.execute('UPDATE transport_inquiry_workflows SET last_error=? WHERE patient_id=?',
                     (last_error, job['patient_id']))
    _set_review(conn, now, job, 'EXHAUSTED')
    service.log(conn, now, 'system', 'transport_inquiry_exhausted', job['patient_id'],
                job['source_hospital_id'], reason=code, note=reason, human_review_required=True)
    return True


def _limits(conn, now, job, patient, inquiries):
    policy = _policy(job)
    if now >= job['deadline_at']:
        return _exhaust(conn, now, job, 'DEADLINE',
                        '대체병원 자동 탐색 제한시간이 끝났습니다. 기존 목적지와 미응답 문의는 유지하며 담당자의 확인이 필요합니다.')
    if len(inquiries) >= policy['max_contacts']:
        pending = _current_pending(patient, inquiries)
        if pending:
            due = min(job['deadline_at'], max(q['last_sent_at'] for q in pending) + policy['wait_seconds'])
            if now < due:
                return _update_wait(conn, now, job, due)
        return _exhaust(conn, now, job, 'CONTACT_LIMIT',
                        '대체병원 자동 문의 수 한도에 도달했습니다. 기존 목적지와 미응답 문의는 유지하며 담당자의 판단이 필요합니다.')
    return None


def _capture(conn, now, pid):
    with db.transaction(conn):
        job = _job(conn, pid)
        if not job or not job['enabled']:
            return None, False
        patient, review = service._patient(conn, pid), _review(conn, pid)
        if not _authorized(patient, review, job):
            stop_in_transaction(conn, now, pid, '조정 요청이 종료되거나 기존 이송 상태가 변경됐습니다.')
            return None, True
        inquiries = _episode_inquiries(conn, job)
        accepted = _valid_acceptances(conn, now, patient, inquiries)
        if accepted:
            return None, _ready(conn, now, job, accepted)
        limited = _limits(conn, now, job, patient, inquiries)
        if limited is not None:
            return None, limited
        if not _position_ready(patient, now):
            return None, _update_wait(conn, now, job, min(now + READY_POLL_SECONDS, job['deadline_at']),
                                     'AWAITING_POSITION', '대체병원 탐색에 사용할 최신 위치를 입력해주세요.')
        pending = _current_pending(patient, inquiries)
        policy = _policy(job)
        if pending:
            due = job['next_evaluation_at']
            if 'prior_reply_seconds' not in policy['config']:
                due = max(due, max(q['last_sent_at'] for q in pending) + policy['wait_seconds'])
            if now < due and job['state'] != 'AWAITING_POSITION':
                return None, _update_wait(conn, now, job, due)
        return dict(patient_id=pid, patient_version=patient['version'],
                    location_version=patient.get('location_version', 0), job_version=job['version'],
                    review_version=review['version'], revision=_revision(conn)), False


def _matches(conn, now, snapshot):
    job = _job(conn, snapshot['patient_id'])
    patient = service._patient(conn, snapshot['patient_id'])
    review = _review(conn, snapshot['patient_id'])
    return (job and job['enabled'] and job['version'] == snapshot['job_version'] and
            _authorized(patient, review, job) and patient['version'] == snapshot['patient_version'] and
            patient.get('location_version', 0) == snapshot['location_version'] and
            review['version'] == snapshot['review_version'] and
            _revision(conn) == snapshot['revision']), job, patient


def _record_decision(conn, now, job, policy, decision):
    service.log(conn, now, 'system', 'transport_inquiry_decision', job['patient_id'],
                job['source_hospital_id'], policy_name=policy['name'], policy_version=policy['version'],
                policy_label=policy['label'], job_version=job['version'], reason=decision['reason'],
                hospital_ids=decision['send'], evidence=decision['evidence'])


def _apply(conn, now, snapshot, candidates, ledger):
    with db.transaction(conn):
        matches, job, patient = _matches(conn, now, snapshot)
        if not matches:
            return False
        inquiries = _episode_inquiries(conn, job)
        accepted = _valid_acceptances(conn, now, patient, inquiries)
        if accepted:
            return _ready(conn, now, job, accepted)
        limited = _limits(conn, now, job, patient, inquiries)
        if limited is not None:
            return limited
        # 계산 중 시각이 지나 위치가 오래되거나 예약 제한이 시작된 경우도 다시 검사한다.
        if not _position_ready(patient, now):
            return _update_wait(conn, now, job, min(now + READY_POLL_SECONDS, job['deadline_at']),
                                'AWAITING_POSITION', '대체병원 탐색에 사용할 최신 위치를 입력해주세요.')
        contacted = {q['hospital_id'] for q in inquiries}
        eligible = [c for c in candidates['candidates']
                    if c['hospital_id'] != job['source_hospital_id'] and
                    inquiry_workflow._eligible_now(conn, now, patient, c['hospital_id'])]
        remaining = [c for c in eligible if c['hospital_id'] not in contacted]
        policy = _policy(job)
        travel = {c['hospital_id']: c['travel']['duration_seconds'] for c in eligible}
        current_pending = {q['hospital_id'] for q in _current_pending(patient, inquiries)}
        own_pending = [dict(q, travel_seconds=travel[q['hospital']]) for q in ledger['patient_pending']
                       if q['hospital'] in travel and q['hospital'] in current_pending]
        decision = inquiry_policies.decide(policy['name'], now=now, candidates=remaining,
                    observations=ledger['observations'], pending=ledger['pending'],
                    patient_pending=own_pending, max_new=policy['max_contacts'] - len(contacted),
                    last_contact_at=max((q['last_sent_at'] for q in inquiries if q['last_sent_at'] <= now),
                                        default=None))
        _record_decision(conn, now, job, policy, decision)
        if not decision['send']:
            if current_pending:
                return _update_wait(conn, now, job, min(decision['next_review_at'], job['deadline_at']),
                    reason='기존 문의의 회신을 기다립니다. 미응답은 거절이 아니며 기존 목적지는 유지합니다.')
            return _exhaust(conn, now, job, 'NO_FRESH_CANDIDATES',
                            '새로 문의할 대체 후보가 없습니다. 기존 목적지를 유지하며 담당자의 확인이 필요합니다.')
        service._send_inquiries(conn, now, patient['id'], decision['send'], actor='transport_inquiry_worker')
        conn.execute("UPDATE transport_inquiry_workflows SET state='RUNNING',round_no=round_no+1, "
                     'next_evaluation_at=?,updated_at=?,version=version+1,last_error=NULL,stop_reason=NULL '
                     'WHERE patient_id=?', (decision['next_review_at'], now, patient['id']))
        _set_review(conn, now, job, 'SEARCHING')
        service.log(conn, now, 'system', 'transport_inquiry_batch', patient['id'], job['source_hospital_id'],
                    hospital_ids=decision['send'], round_no=job['round_no'] + 1,
                    next_evaluation_at=decision['next_review_at'], policy_name=policy['name'],
                    policy_label=policy['label'], reason=decision['reason'])
        return True


def _failed(conn, now, pid, error, snapshot=None, expected_job_version=None):
    with db.transaction(conn):
        if snapshot:
            matches, job, _ = _matches(conn, now, snapshot)
            if not matches:
                return False
        else:
            job = _job(conn, pid)
            if (not job or not job['enabled'] or
                    (expected_job_version is not None and job['version'] != expected_job_version)):
                return False
            if not _authorized(service._patient(conn, pid), _review(conn, pid), job):
                return False
        message = error.message if isinstance(error, service.ServiceError) else '대체병원 후보 계산 중 오류가 발생했습니다.'
        return _exhaust(conn, now, job, 'CALCULATION_ERROR',
                        '대체병원 자동 문의를 중단했습니다. 기존 목적지는 유지하며 담당자의 확인이 필요합니다.',
                        'ERROR', message)


def run_once(path, estimator, clock=lambda: int(time.time())):
    """백그라운드 worker만 호출한다. 외부 이동시간 계산 중 쓰기 잠금을 보유하지 않는다."""
    conn = db.connect(path)
    did_work = False
    try:
        jobs = list(conn.execute('SELECT patient_id,version FROM transport_inquiry_workflows '
                                 'WHERE enabled=1 ORDER BY started_at,patient_id'))
        for initial_job in jobs:
            pid = initial_job['patient_id']
            snapshot = None
            try:
                snapshot, changed = _capture(conn, int(clock()), pid)
                did_work = changed or did_work
                if snapshot is None:
                    continue
                candidates = service.candidates(conn, int(clock()), pid, estimator)
                ledger = inquiry_observations.capture(conn, int(clock()), pid)
                did_work = _apply(conn, int(clock()), snapshot, candidates, ledger) or did_work
            except Exception as error:
                try:
                    did_work = _failed(conn, int(clock()), pid, error, snapshot,
                                       initial_job['version']) or did_work
                except Exception:
                    # 저장소 자체가 깨져도 다른 환자의 진행을 시도한다.
                    pass
        return did_work
    finally:
        conn.close()
