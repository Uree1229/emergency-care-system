"""초기 병원 문의 자동 진행. 수용 응답과 목적지 확정은 자동화하지 않는다.

기본 3곳/60초/최대 10곳/300초는 시연을 위한 운영 설정값이다. 환자 치료의
허용 대기시간이나 임상 기준을 뜻하지 않는다. 같은 환자정보 버전에서 연락한 병원에는
자동 재문의하지 않으며 미응답을 수용 불가로 바꾸지 않는다.
"""
import time

from . import db, service, inquiry_policies, inquiry_observations

BATCH_SIZE = 3
WAIT_SECONDS = 60
MAX_CONTACTS = 10
DEADLINE_SECONDS = 300
READY_POLL_SECONDS = 1
STATES = {'QUEUED', 'RUNNING', 'AWAITING_CONFIRMATION', 'EXHAUSTED',
          'PAUSED', 'COMPLETED', 'ERROR'}


def _job(conn, pid):
    return db.row(conn.execute('SELECT * FROM inquiry_workflows WHERE patient_id=?', (pid,)).fetchone())


def _inquiries(conn, pid):
    return db.rows(conn.execute('SELECT * FROM inquiries WHERE patient_id=? ORDER BY id', (pid,)))


def _current_inquiries(patient, inquiries):
    """A new information version can revisit hospitals without erasing old inquiry history."""
    return [q for q in inquiries if q['patient_version'] == patient['version']]


def _valid_acceptances(conn, now, patient, inquiries):
    valid = []
    for inquiry in inquiries:
        if inquiry['state'] != 'ACCEPTED':
            continue
        try:
            service._check_acceptance(conn, now, patient, inquiry)
        except service.ServiceError:
            continue
        valid.append(inquiry['id'])
    return valid


def _spec(name):
    try:
        return inquiry_policies.spec(name)
    except ValueError as error:
        raise service.ServiceError('INVALID_POLICY', str(error), 400) from None


def _policy(job):
    policy = _spec(job['policy_name'] if job else 'fixed_3')
    if job:
        service._require(job['policy_version'] == policy['version'] and
                         (job['policy_config'] is None or job['policy_config'] == policy),
                         'POLICY_VERSION_MISMATCH', '저장된 문의 정책과 현재 코드가 다릅니다. 담당자의 확인이 필요합니다.', 409)
    return policy


def _insert(conn, now, pid, enabled, policy):
    conn.execute('INSERT INTO inquiry_workflows '
                 '(patient_id,enabled,state,started_at,deadline_at,next_evaluation_at,round_no,updated_at,version,'
                 'policy_name,policy_version,policy_config) VALUES (?,?,?,?,?,?,0,?,1,?,?,?)',
                 (pid, int(enabled), 'QUEUED' if enabled else 'PAUSED', now,
                  now + policy['deadline_seconds'], now, now,
                  policy['name'], policy['version'], db.dumps(policy)))
    service.log(conn, now, 'system', 'inquiry_workflow_started' if enabled else 'inquiry_workflow_paused',
                pid, policy_name=policy['name'], policy_label=policy['label'],
                batch_size=policy['batch_size'], wait_seconds=policy['wait_seconds'],
                max_contacts=policy['max_contacts'], deadline_seconds=policy['deadline_seconds'])
    return _job(conn, pid)


def attach(conn, now, pid, policy_name='fixed_3', enabled=True):
    """환자 등록 트랜잭션에 기본 자동 문의 작업을 함께 넣는다."""
    patient = service._patient(conn, pid)
    existing = _job(conn, pid)
    if existing:
        return existing
    service._require(patient['state'] == 'WAITING', 'INVALID_STATE',
                     '병원 선정 대기 상태에서만 자동 문의를 시작합니다', 409)
    return _insert(conn, now, pid, enabled, _spec(policy_name))


def start(conn, now, pid, enabled=True, policy_name=None):
    """명시적 시작/재시작/일시정지. 재시작해도 이미 문의한 병원은 보존한다."""
    service._require(type(enabled) is bool, 'INVALID', 'enabled는 true 또는 false입니다')
    selected = _spec(policy_name) if policy_name is not None else None
    with db.transaction(conn):
        patient = service._patient(conn, pid)
        job = _job(conn, pid)
        # 같은 이름의 명시적 요청도 저장된 버전/설정을 검증한다.
        # 거부될 작업을 먼저 커밋한 뒤 view()에서 오류 내지 않도록 한다.
        switching = selected is not None and job and selected['name'] != job['policy_name']
        if switching:
            policy = selected
        elif job:
            policy = _policy(job)
        else:
            policy = selected or _policy(None)
        if selected is not None and job and selected['name'] != job['policy_name']:
            service._require(not job['enabled'], 'POLICY_ACTIVE', '실행 중 정책은 바꿀 수 없습니다. 먼저 일시정지하세요.', 409)
            service._require(patient['state'] == 'WAITING', 'INVALID_STATE', '초기 병원 선정 대기 상태에서만 정책을 바꿉니다.', 409)
            conn.execute('UPDATE inquiry_workflows SET policy_name=?,policy_version=?,policy_config=?,version=version+1 '
                         'WHERE patient_id=?', (policy['name'], policy['version'], db.dumps(policy), pid))
            service.log(conn, now, 'system', 'inquiry_policy_changed', pid,
                        policy_name=policy['name'], policy_label=policy['label'])
        if enabled:
            service._require(patient['state'] == 'WAITING', 'INVALID_STATE',
                             '목적지 확정 이후에는 초기 자동 문의를 시작할 수 없습니다', 409)
            if job is None:
                _insert(conn, now, pid, True, policy)
            elif not job['enabled']:
                conn.execute("UPDATE inquiry_workflows SET enabled=1,state='QUEUED',started_at=?,"
                             'deadline_at=?,next_evaluation_at=?,updated_at=?,version=version+1,'
                             'last_error=NULL,stop_reason=NULL WHERE patient_id=?',
                             (now, now + policy['deadline_seconds'], now, now, pid))
                service.log(conn, now, 'system', 'inquiry_workflow_started', pid,
                            restarted=True, policy_name=policy['name'], policy_label=policy['label'],
                            batch_size=policy['batch_size'], wait_seconds=policy['wait_seconds'],
                            max_contacts=policy['max_contacts'], deadline_seconds=policy['deadline_seconds'])
        elif job is None:
            service._require(patient['state'] == 'WAITING', 'INVALID_STATE',
                             '종료된 이송 건에는 자동 문의를 만들지 않습니다', 409)
            _insert(conn, now, pid, False, policy)
        elif job['enabled']:
            stop_in_transaction(conn, now, pid, state='PAUSED', reason='사용자가 자동 문의를 일시정지했습니다.')
            service.log(conn, now, 'system', 'inquiry_workflow_paused', pid)
    return view(conn, now, pid)


def stop_in_transaction(conn, now, pid, state='COMPLETED', reason=None):
    """확정/취소 등 기존 업무 트랜잭션 안에서 문의 작업만 종료한다."""
    service._require(state in STATES, 'INVALID', '자동 문의 상태 오류')
    current = _job(conn, pid)
    if not current:
        return None
    if current['enabled'] or current['state'] != state or current['stop_reason'] != reason:
        conn.execute('UPDATE inquiry_workflows SET enabled=0,state=?,stop_reason=?,updated_at=?,'
                     'version=version+1 WHERE patient_id=?', (state, reason, now, pid))
    return _job(conn, pid)


def view(conn, now, pid):
    """조회는 작업을 시작하거나 병원에 문의하지 않는다."""
    patient = service._patient(conn, pid)
    job = _job(conn, pid)
    inquiries = _inquiries(conn, pid)
    out = dict(job) if job else dict(patient_id=pid, enabled=0, state='NOT_STARTED')
    out.update(enabled=bool(out['enabled']),
               policy=_policy(job),
               latest_decision=db.row(conn.execute('SELECT * FROM inquiry_policy_decisions WHERE patient_id=? '
                                                   'ORDER BY id DESC LIMIT 1', (pid,)).fetchone()),
               contacted_count=len(inquiries),
               contacted_hospital_ids=[q['hospital_id'] for q in inquiries],
               current_version_contacted_count=len(_current_inquiries(patient, inquiries)),
               current_version_contacted_hospital_ids=[q['hospital_id'] for q in _current_inquiries(patient, inquiries)],
               pending_count=sum(q['state'] in service.OPEN_INQUIRY for q in inquiries),
               valid_accepted_inquiry_ids=_valid_acceptances(conn, now, patient, inquiries))
    return out


def decisions(conn, now, pid, limit=20):
    service._patient(conn, pid)
    service._require(type(limit) is int and 1 <= limit <= 100, 'INVALID', 'limit은 1~100 정수입니다')
    return dict(patient_id=pid, decisions=db.rows(conn.execute('SELECT * FROM inquiry_policy_decisions '
                'WHERE patient_id=? ORDER BY id DESC LIMIT ?', (pid, limit))))


def _revision(conn):
    return conn.execute('SELECT revision FROM planning_state WHERE id=1').fetchone()[0]


def _update_wait(conn, now, job, next_at):
    # 이미 계산한 대기 시각을 매초 다시 쓰지 않아 두 워커도 같은 버전을 공유한다.
    if job['state'] == 'RUNNING' and job['next_evaluation_at'] == next_at:
        return False
    conn.execute("UPDATE inquiry_workflows SET state='RUNNING',next_evaluation_at=?,updated_at=?,"
                 'version=version+1,last_error=NULL WHERE patient_id=?', (next_at, now, job['patient_id']))
    return True


def _ready(conn, now, job, accepted):
    if job['state'] == 'AWAITING_CONFIRMATION':
        return False
    conn.execute("UPDATE inquiry_workflows SET state='AWAITING_CONFIRMATION',next_evaluation_at=?,"
                 'updated_at=?,version=version+1,last_error=NULL,stop_reason=NULL WHERE patient_id=?',
                 (now + READY_POLL_SECONDS, now, job['patient_id']))
    service.log(conn, now, 'system', 'inquiry_workflow_ready', job['patient_id'],
                accepted_inquiry_ids=accepted, note='유효한 수용 응답을 확보했습니다. 목적지 확정은 별도 판단입니다.')
    return True


def _exhaust(conn, now, job, code, reason, state='EXHAUSTED', last_error=None):
    stop_in_transaction(conn, now, job['patient_id'], state=state, reason=reason)
    if last_error is not None:
        conn.execute('UPDATE inquiry_workflows SET last_error=? WHERE patient_id=?',
                     (last_error, job['patient_id']))
    service.log(conn, now, 'system', 'inquiry_workflow_exhausted', job['patient_id'],
                reason=code, note=reason, human_review_required=True)
    return True


def _limits(conn, now, job, inquiries):
    policy = _policy(job)
    if now >= job['deadline_at']:
        return _exhaust(conn, now, job, 'DEADLINE', '자동 탐색 제한시간이 끝났습니다. 상황실 또는 담당자의 확인이 필요합니다.')
    if len(inquiries) >= policy['max_contacts']:
        pending = [q for q in inquiries if q['state'] in service.OPEN_INQUIRY]
        # 문의 수 한도는 추가 문의의 제한이다. 마지막 wave도 응답 대기시간을 보장한다.
        if pending:
            due = min(job['deadline_at'], max(q['last_sent_at'] for q in pending) + policy['wait_seconds'])
            if now < due:
                return _update_wait(conn, now, job, due)
        return _exhaust(conn, now, job, 'CONTACT_LIMIT', '자동 문의 병원 수 한도에 도달했습니다. 담당자의 추가 판단이 필요합니다.')
    return None


def _capture(conn, now, pid):
    """짧은 트랜잭션으로 상태를 확인한다. 외부 이동시간 계산은 여기서 하지 않는다."""
    with db.transaction(conn):
        job = _job(conn, pid)
        if not job or not job['enabled']:
            return None, False
        patient = service._patient(conn, pid)
        if patient['state'] != 'WAITING':
            stop_in_transaction(conn, now, pid, reason='초기 병원 선정 단계가 종료됐습니다.')
            return None, True
        inquiries = _current_inquiries(patient, _inquiries(conn, pid))
        accepted = _valid_acceptances(conn, now, patient, inquiries)
        if accepted:
            return None, _ready(conn, now, job, accepted)
        limited = _limits(conn, now, job, inquiries)
        if limited is not None:
            return None, limited
        pending = [q for q in inquiries if q['state'] in service.OPEN_INQUIRY]
        policy = _policy(job)
        if pending:
            due = job['next_evaluation_at']
            if 'prior_reply_seconds' not in policy['config']:
                due = max(due, max(q['last_sent_at'] for q in pending) + policy['wait_seconds'])
            if now < due:
                return None, _update_wait(conn, now, job, due)
        return dict(patient_id=pid, patient_version=patient['version'], job_version=job['version'],
                    revision=_revision(conn), now=now), False


def _matches(conn, snapshot):
    job = _job(conn, snapshot['patient_id'])
    patient = service._patient(conn, snapshot['patient_id'])
    return (job and job['enabled'] and job['version'] == snapshot['job_version'] and
            patient['state'] == 'WAITING' and patient['version'] == snapshot['patient_version'] and
            _revision(conn) == snapshot['revision']), job, patient


def _hold_pending(conn, now, job, next_at=None, reason=None):
    """새 후보가 없어도 이미 보낸 문의의 수용 회신은 계속 감시한다."""
    reason = reason or '새로 문의할 후보가 없어 기존 문의의 회신을 기다립니다. 미응답은 수용 불가가 아닙니다.'
    next_at = min(next_at or now + _policy(job)['review_seconds'], job['deadline_at'])
    changed_reason = job['stop_reason'] != reason or job['state'] != 'RUNNING'
    conn.execute("UPDATE inquiry_workflows SET state='RUNNING',next_evaluation_at=?,stop_reason=?,"
                 'updated_at=?,version=version+1,last_error=NULL WHERE patient_id=?',
                 (next_at, reason, now, job['patient_id']))
    if changed_reason:
        service.log(conn, now, 'system', 'inquiry_workflow_waiting', job['patient_id'],
                    note=reason, next_evaluation_at=next_at)
    return True


def _eligible_now(conn, now, patient, hospital_id):
    """예약된 제한 시작과 정보 노후화는 DB 버전 변경 없이도 발생한다."""
    if service.blocking_restriction(conn, hospital_id, set(patient['required_capabilities']), now):
        return False
    resources = {resource['resource_type']: resource
                 for resource in service.resource_view(conn, hospital_id, now)}
    for resource_type, need in patient['resource_demands'].items():
        resource = resources.get(resource_type)
        if (resource is not None and resource['free'] is not None and
                not resource['stale'] and resource['free'] < need):
            return False
    return True


def _record_decision(conn, now, job, policy, decision):
    previous = db.row(conn.execute('SELECT * FROM inquiry_policy_decisions WHERE patient_id=? '
                                   'ORDER BY id DESC LIMIT 1', (job['patient_id'],)).fetchone())
    conn.execute('INSERT INTO inquiry_policy_decisions '
                 '(patient_id,at,policy_name,policy_version,job_version,reason,hospital_ids,evidence) '
                 'VALUES (?,?,?,?,?,?,?,?)',
                 (job['patient_id'], now, policy['name'], policy['version'], job['version'],
                  decision['reason'], db.dumps(decision['send']), db.dumps(decision['evidence'])))
    if not decision['send'] and (not previous or previous['reason'] != decision['reason'] or
                                previous['policy_name'] != policy['name']):
        service.log(conn, now, 'system', 'inquiry_policy_decided', job['patient_id'],
                    policy_name=policy['name'], policy_label=policy['label'], reason=decision['reason'])


def _apply(conn, now, snapshot, candidates, ledger):
    with db.transaction(conn):
        matches, job, patient = _matches(conn, snapshot)
        if not matches:
            return False
        inquiries = _current_inquiries(patient, _inquiries(conn, patient['id']))
        accepted = _valid_acceptances(conn, now, patient, inquiries)
        if accepted:
            return _ready(conn, now, job, accepted)
        limited = _limits(conn, now, job, inquiries)
        if limited is not None:
            return limited
        contacted = {q['hospital_id'] for q in inquiries}
        eligible = [c for c in candidates['candidates'] if _eligible_now(conn, now, patient, c['hospital_id'])]
        remaining = [c for c in eligible if c['hospital_id'] not in contacted]
        policy = _policy(job)
        travel = {c['hospital_id']: c['travel']['duration_seconds'] for c in eligible}
        own_pending = [dict(q, travel_seconds=travel[q['hospital']]) for q in ledger['patient_pending']
                       if q['hospital'] in travel]
        decision = inquiry_policies.decide(policy['name'], now=now, candidates=remaining,
                    observations=ledger['observations'], pending=ledger['pending'], patient_pending=own_pending,
                    max_new=policy['max_contacts'] - len(contacted),
                    last_contact_at=max((q['last_sent_at'] for q in inquiries
                                         if q['last_sent_at'] <= now), default=None))
        _record_decision(conn, now, job, policy, decision)
        hospital_ids = decision['send']
        if not hospital_ids:
            if any(q['state'] in service.OPEN_INQUIRY for q in inquiries):
                reason = None if not remaining else '관측한 회신과 이동시간을 비교해 기존 문의의 회신을 더 기다립니다.'
                return _hold_pending(conn, now, job, decision['next_review_at'], reason)
            return _exhaust(conn, now, job, 'NO_FRESH_CANDIDATES',
                            '현재 조건에서 새로 문의할 후보가 없습니다. 미응답은 거절로 처리하지 않으며 담당자의 확인이 필요합니다.')
        service._send_inquiries(conn, now, patient['id'], hospital_ids, actor='inquiry_worker')
        conn.execute("UPDATE inquiry_workflows SET state='RUNNING',round_no=round_no+1,next_evaluation_at=?,"
                     'updated_at=?,version=version+1,last_error=NULL,stop_reason=NULL WHERE patient_id=?',
                     (decision['next_review_at'], now, patient['id']))
        service.log(conn, now, 'system', 'inquiry_workflow_batch', patient['id'],
                    hospital_ids=hospital_ids, round_no=job['round_no'] + 1,
                    next_evaluation_at=decision['next_review_at'], policy_name=policy['name'],
                    policy_label=policy['label'], reason=decision['reason'])
        return True


def _failed(conn, now, snapshot, error):
    with db.transaction(conn):
        matches, job, _ = _matches(conn, snapshot)
        if not matches:
            return False
        message = error.message if isinstance(error, service.ServiceError) else '후보 탐색 중 오류가 발생했습니다.'
        return _exhaust(conn, now, job, 'CALCULATION_ERROR',
                        '자동 문의를 중단했습니다. 담당자의 확인 후 다시 시작해주세요.',
                        state='ERROR', last_error=message)


def _capture_failed(conn, now, pid, error):
    """잘못된 한 이송 건 때문에 같은 루프의 다른 환자 탐색을 막지 않는다."""
    with db.transaction(conn):
        job = _job(conn, pid)
        if not job or not job['enabled']:
            return False
        message = error.message if isinstance(error, service.ServiceError) else '자동 문의 상태 확인 중 오류가 발생했습니다.'
        return _exhaust(conn, now, job, 'STATE_ERROR',
                        '자동 문의 상태를 확인하지 못했습니다. 담당자의 확인 후 다시 시작해주세요.',
                        state='ERROR', last_error=message)


def run_once(path, estimator, clock=lambda: int(time.time())):
    """활성 작업을 한 번 진행한다. GET 요청과 무관하게 백그라운드에서 호출한다."""
    conn = db.connect(path)
    did_work = False
    try:
        patient_ids = [r['patient_id'] for r in conn.execute(
            'SELECT patient_id FROM inquiry_workflows WHERE enabled=1 ORDER BY started_at,patient_id')]
        for pid in patient_ids:
            try:
                snapshot, changed = _capture(conn, int(clock()), pid)
            except Exception as error:
                try:
                    did_work = _capture_failed(conn, int(clock()), pid, error) or did_work
                except Exception:
                    # 저장소 자체가 복구 불가해도 다음 환자의 처리 시도는 유지한다.
                    pass
                continue
            did_work = changed or did_work
            if snapshot is None:
                continue
            try:
                # 후보 조회의 이동시간 API는 SQLite 쓰기 잠금 밖에서 실행한다.
                candidates = service.candidates(conn, int(clock()), pid, estimator)
                ledger = inquiry_observations.capture(conn, int(clock()), pid)
                changed = _apply(conn, int(clock()), snapshot, candidates, ledger)
            except Exception as error:
                changed = _failed(conn, int(clock()), snapshot, error)
            did_work = changed or did_work
        return did_work
    finally:
        conn.close()
