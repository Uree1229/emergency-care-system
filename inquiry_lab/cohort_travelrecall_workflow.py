"""Explicit isolated workflow kernel for a public own-decline extra review.

No server function or registry is replaced. Unchanged helpers retain their
frozen source implementation; local policy lookup, capture, apply and loop are
explicit and auditable. No simulator-private input reaches these functions.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from server import db, inquiry_workflow as foundation, inquiry_observations, service
from . import horizon_context, cohort_travelrecall_policy as policy, cohort_travelrecall_context as context

FOUNDATION_SHA256='952607d515d90d683b09e8c602720b7b2a15084bdbcce698c781f5bee1eb0fd0'
if hashlib.sha256(Path(foundation.__file__).read_bytes()).hexdigest()!=FOUNDATION_SHA256:raise ValueError('frozen decline-wake workflow foundation differs')
NAME=policy.NAME
JOB_FIELDS=('patient_id','version','started_at','deadline_at','enabled','policy_name')
_job=foundation._job
_inquiries=foundation._inquiries
_valid_acceptances=foundation._valid_acceptances
_revision=foundation._revision
_matches=foundation._matches
_update_wait=foundation._update_wait
_ready=foundation._ready
_exhaust=foundation._exhaust
_eligible_now=foundation._eligible_now
_record_decision=foundation._record_decision
_failed=foundation._failed
_capture_failed=foundation._capture_failed
_insert=foundation._insert
stop_in_transaction=foundation.stop_in_transaction


def _spec(name):
    return policy.spec(name) if name==NAME else foundation._spec(name)


def _policy(job):
    policy = _spec(job['policy_name'] if job else 'fixed_3')
    if job:
        service._require(job['policy_version'] == policy['version'] and
                         (job['policy_config'] is None or job['policy_config'] == policy),
                         'POLICY_VERSION_MISMATCH', '저장된 문의 정책과 현재 코드가 다릅니다. 담당자의 확인이 필요합니다.', 409)
    return policy


def attach(conn, now, pid, policy_name='fixed_3', enabled=True):
    """환자 등록 트랜잭션에 기본 자동 문의 작업을 함께 넣는다."""
    patient = service._patient(conn, pid)
    existing = _job(conn, pid)
    if existing:
        return existing
    service._require(patient['state'] == 'WAITING', 'INVALID_STATE',
                     '병원 선정 대기 상태에서만 자동 문의를 시작합니다', 409)
    return _insert(conn, now, pid, enabled, _spec(policy_name))


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
               pending_count=sum(q['state'] in service.OPEN_INQUIRY for q in inquiries),
               valid_accepted_inquiry_ids=_valid_acceptances(conn, now, patient, inquiries))
    return out


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


def _prefixes(conn):
    return dict(event_prefix_max=conn.execute('SELECT COALESCE(MAX(id),0) FROM events').fetchone()[0],
        decision_prefix_max=conn.execute('SELECT COALESCE(MAX(id),0) FROM inquiry_policy_decisions').fetchone()[0])


def _wake_context(conn,now,snapshot,job,inquiries):
    latest=db.row(conn.execute('SELECT * FROM inquiry_policy_decisions WHERE patient_id=? ORDER BY id DESC LIMIT 1',(job['patient_id'],)).fetchone())
    marks=_prefixes(conn)
    events=db.rows(conn.execute("SELECT * FROM events WHERE patient_id=? AND type='inquiry_responded' AND id<=? AND at<=? ORDER BY id",
        (job['patient_id'],marks['event_prefix_max'],now)))
    return context.build_wake_context(now=now,snapshot=snapshot,job=job,latest_decision=latest,
        inquiries=inquiries,events=events,**marks)


def _capture(conn,now,pid):
    """Original priority checks, with one narrowly inserted early-review branch."""
    with db.transaction(conn):
        job=_job(conn,pid)
        if not job or not job['enabled']:return None,False,None
        patient=service._patient(conn,pid)
        if patient['state']!='WAITING':
            stop_in_transaction(conn,now,pid,reason='초기 병원 선정 단계가 종료됐습니다.')
            return None,True,None
        inquiries=_inquiries(conn,pid)
        accepted=_valid_acceptances(conn,now,patient,inquiries)
        if accepted:return None,_ready(conn,now,job,accepted),None
        limited=_limits(conn,now,job,inquiries)
        if limited is not None:return None,limited,None
        pending=[q for q in inquiries if q['state'] in service.OPEN_INQUIRY]
        selected_policy=_policy(job)
        snapshot=dict(patient_id=pid,patient_version=patient['version'],job_version=job['version'],revision=_revision(conn),now=now)
        if pending:
            due=job['next_evaluation_at']
            if 'prior_reply_seconds' not in selected_policy['config']:
                due=max(due,max(q['last_sent_at'] for q in pending)+selected_policy['wait_seconds'])
            if now<due:
                wake=_wake_context(conn,now,snapshot,job,inquiries) if job['policy_name']==NAME else None
                if wake is not None:return snapshot,False,wake
                return None,_update_wait(conn,now,job,due),None
        return snapshot,False,None


def _time_context(conn,now,snapshot,job):
    if snapshot['now']>now:raise ValueError('current apply clock precedes its public capture')
    marks=_prefixes(conn)
    events=db.rows(conn.execute('SELECT * FROM events WHERE id<=? ORDER BY id',(marks['event_prefix_max'],)))
    public_job={key:deepcopy(job[key]) for key in JOB_FIELDS}
    value=horizon_context.build_context(now=now,patient_id=job['patient_id'],job_version=job['version'],job=public_job,events=events,**marks)
    return value,dict(apply_snapshot=deepcopy(snapshot),job=public_job)


def _apply(conn,now,snapshot,candidates,ledger,*,wake_context=None):
    """Return the committed call as data; no shared global recorder is installed."""
    record=None
    with db.transaction(conn):
        matches,job,patient=_matches(conn,snapshot)
        if not matches:return None
        inquiries=_inquiries(conn,patient['id'])
        accepted=_valid_acceptances(conn,now,patient,inquiries)
        if accepted:return dict(changed=_ready(conn,now,job,accepted),record=None)
        limited=_limits(conn,now,job,inquiries)
        if limited is not None:return dict(changed=limited,record=None)
        if wake_context is not None:
            if now!=snapshot['now']:return None
            current=_wake_context(conn,now,snapshot,job,inquiries)
            if json.dumps(current,sort_keys=True,allow_nan=False)!=json.dumps(wake_context,sort_keys=True,allow_nan=False):return None
        contacted={q['hospital_id'] for q in inquiries}
        eligible=[c for c in candidates['candidates'] if _eligible_now(conn,now,patient,c['hospital_id'])]
        remaining=[c for c in eligible if c['hospital_id'] not in contacted]
        selected_policy=_policy(job)
        travel={c['hospital_id']:c['travel']['duration_seconds'] for c in eligible}
        own_pending=[dict(q,travel_seconds=travel[q['hospital']]) for q in ledger['patient_pending'] if q['hospital'] in travel]
        temporal,source=_time_context(conn,now,snapshot,job)
        inputs=dict(now=now,candidates=deepcopy(remaining),observations=deepcopy(ledger['observations']),pending=deepcopy(ledger['pending']),
            patient_pending=own_pending,max_new=selected_policy['max_contacts']-len(contacted),
            last_contact_at=max((q['last_sent_at'] for q in inquiries if q['last_sent_at']<=now),default=None),time_context=temporal,wake_context=deepcopy(wake_context))
        before=deepcopy(inputs)
        decision=policy.decide(selected_policy['name'],**inputs)
        if inputs!=before:raise AssertionError('decline-wake policy mutated captured public inputs')
        _record_decision(conn,now,job,selected_policy,decision)
        decision_id=conn.execute('SELECT MAX(id) FROM inquiry_policy_decisions').fetchone()[0]
        if not decision['send']:
            if any(q['state'] in service.OPEN_INQUIRY for q in inquiries):
                reason=None if not remaining else '관측한 회신과 이동시간을 비교해 기존 문의의 회신을 더 기다립니다.'
                changed=_hold_pending(conn,now,job,decision['next_review_at'],reason)
            else:
                changed=_exhaust(conn,now,job,'NO_FRESH_CANDIDATES','현재 조건에서 새로 문의할 후보가 없습니다. 미응답은 거절로 처리하지 않으며 담당자의 확인이 필요합니다.')
        else:
            service._send_inquiries(conn,now,patient['id'],decision['send'],actor='inquiry_worker')
            conn.execute("UPDATE inquiry_workflows SET state='RUNNING',round_no=round_no+1,next_evaluation_at=?,updated_at=?,version=version+1,last_error=NULL,stop_reason=NULL WHERE patient_id=?",
                (decision['next_review_at'],now,patient['id']))
            service.log(conn,now,'system','inquiry_workflow_batch',patient['id'],hospital_ids=decision['send'],round_no=job['round_no']+1,
                next_evaluation_at=decision['next_review_at'],policy_name=selected_policy['name'],policy_label=selected_policy['label'],reason=decision['reason'])
            changed=True
        record=dict(name=selected_policy['name'],inputs=before,output=deepcopy(decision),db_event_prefix_max=temporal['event_prefix_max'],
            workflow_snapshot=source,patient_id=patient['id'],db_decision_id=decision_id)
    return dict(changed=changed,record=record)


def require_candidate_jobs(conn):
    """Out-of-scope mixed active jobs fail before this adapter can write."""
    rows=db.rows(conn.execute('SELECT patient_id,policy_name FROM inquiry_workflows WHERE enabled=1 ORDER BY started_at,patient_id'))
    if any(row['policy_name']!=NAME for row in rows):raise ValueError('decline-wake workflow supports only its candidate active jobs')


def run_once(path,estimator,clock,*,records):
    """Same native patient loop; records are appended only after successful commit."""
    conn=db.connect(path);did_work=False
    try:
        require_candidate_jobs(conn)
        patient_ids=[r['patient_id'] for r in conn.execute('SELECT patient_id FROM inquiry_workflows WHERE enabled=1 ORDER BY started_at,patient_id')]
        for pid in patient_ids:
            try:snapshot,changed,wake=_capture(conn,int(clock()),pid)
            except Exception as error:
                try:did_work=_capture_failed(conn,int(clock()),pid,error) or did_work
                except Exception:pass
                continue
            did_work=changed or did_work
            if snapshot is None:continue
            try:
                candidates=service.candidates(conn,int(clock()),pid,estimator)
                ledger=inquiry_observations.capture(conn,int(clock()),pid)
                result=_apply(conn,int(clock()),snapshot,candidates,ledger,wake_context=wake)
                if result is not None:
                    did_work=result['changed'] or did_work
                    if result['record'] is not None:records.append(result['record'])
            except Exception as error:did_work=_failed(conn,int(clock()),snapshot,error) or did_work
        return did_work
    finally:conn.close()
