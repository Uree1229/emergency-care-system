"""Explicit frozen lab physical kernel with an injected local workflow step.

Simulation-private potentials/staffing remain confined to this physical engine,
exactly as in the frozen branch. Local policy/DB capture receives only public
inputs. No original module function, registry or namespace is replaced.
"""
from collections import deque
from copy import deepcopy
import hashlib
from pathlib import Path
import tempfile
from server import db,inquiry_experiment as ie,inquiry_observations
from . import confirm_policy,cohort_declinewake_service as service,cohort_declinewake_policy as candidate_policy
from . import runner as foundation

FOUNDATION_SHA256='36e40ae9189249f95617ac5807cfa60265dbc7287fa580fb02e0ac071369ebf4'
if hashlib.sha256(Path(foundation.__file__).read_bytes()).hexdigest()!=FOUNDATION_SHA256:raise ValueError('frozen decline-wake physical branch foundation differs')


def _branch(path, scenario, policy, rule, expected_hash, environment_hash, *, workflow_step):
    hold = confirm_policy.parse_rule(rule)
    conn = db.connect(path)
    initial_hash = ie.snapshot_digest(conn)
    if initial_hash != expected_hash:
        raise AssertionError('branch initial snapshot differs')
    patients = sorted(scenario['patients'], key=lambda p: (p['at'], p['id']))
    by_pid = {p['id']: p for p in patients}
    potentials = {(r['patient_id'], r['hospital_id']): r for r in scenario['potentials']}
    queues = {h['id']: deque() for h in scenario['hospitals']}
    workers = {h['id']: h['workers'] for h in scenario['hospitals']}
    active, seen, finished, outcomes, trace = [], set(), set(), {}, []
    estimator = ie.MatrixEstimator(scenario['hospitals'], patients)
    work_seconds = 0
    observed_first = set()
    holds = {}           # pid -> dict(started_at, next_check, first_best)
    hold_log = []
    at = 0

    def record(typ, q, **extra):
        trace.append(dict(at=at, type=typ, patient_id=q['patient_id'], hospital_id=q['hospital_id'],
                          inquiry_id=q['id'], **extra))

    def is_open(iid):
        return conn.execute('SELECT state FROM inquiries WHERE id=?', (iid,)).fetchone()[0] in service.OPEN_INQUIRY

    def confirm(patient, q, now, reason, decision=None):
        pid = patient['id']
        service.confirm_destination(conn, now, pid, q['id'], actor='experiment_human_zero_delay')
        travel = patient['travel_seconds'][q['hospital_id']]
        outcomes[pid] = dict(selected_at=at, selection_seconds=at - patient['at'], travel_seconds=travel,
                             estimated_arrival_seconds=at - patient['at'] + travel,
                             destination_hospital_id=q['hospital_id'])
        finished.add(pid)
        record('destination_confirmed', q)
        if pid in holds:
            h = holds.pop(pid)
            hold_log.append(dict(patient_id=pid, started_at=h['started_at'], ended_at=at,
                                 first_best=h['first_best'], confirmed=q['hospital_id'],
                                 switched=q['hospital_id'] != h['first_best'], reason=reason))
            record('confirmation_hold_ended', q, held_seconds=at - h['started_at'], reason=reason,
                   switched=q['hospital_id'] != h['first_best'])

    try:
        while True:
            now = ie.EPOCH + at
            for patient in patients:
                if patient['at'] == at:
                    service.create_patient(conn, now, patient['id'], ie._payload(patient, policy),
                                           actor='experiment_patient')
            completing = sorted([x for x in active if x['due'] <= at],
                                key=lambda x: (x['due'], x['q']['hospital_id'], x['q']['id']))
            active = [x for x in active if x['due'] > at]
            replied_now = {task['q']['patient_id'] for task in completing}
            for task in completing:
                q, potential = task['q'], task['potential']
                record('review_finished', q, started_at=task['started_at'],
                       service_seconds=potential['service_seconds'])
                if not is_open(q['id']):
                    record('reply_suppressed_after_cancel', q)
                elif potential['no_response']:
                    record('reply_omitted', q)
                else:
                    action = 'accept' if potential['accept'] else 'decline'
                    service.respond(conn, now, q['id'], action, '합성 검토자',
                                    reason='기타' if action == 'decline' else None)
                    record('reply_' + action, q)
            for patient in patients:
                pid = patient['id']
                if pid in finished or patient['at'] > at:
                    continue
                p = service._patient(conn, pid)
                accepted = []
                for q in db.rows(conn.execute("SELECT * FROM inquiries WHERE patient_id=? AND state='ACCEPTED'", (pid,))):
                    try:
                        service._check_acceptance(conn, now, p, q)
                    except service.ServiceError:
                        continue
                    accepted.append(q)
                if accepted:
                    q_best = min(accepted, key=lambda q: (patient['travel_seconds'][q['hospital_id']], q['hospital_id']))
                    if not hold:
                        confirm(patient, q_best, now, 'immediate')
                        continue
                    # 대기 중에는 5초 점검 시각이나 이 환자의 새 회신이 있을 때만 다시 판단한다.
                    if pid in holds and at < holds[pid]['next_check'] and pid not in replied_now:
                        continue
                    ledger = inquiry_observations.capture(conn, now, pid)
                    own = [dict(row, travel_seconds=patient['travel_seconds'][row['hospital']])
                           for row in ledger['patient_pending']]
                    decision = confirm_policy.decide(
                        now=now,
                        accepted=[dict(hospital=q['hospital_id'], travel_seconds=patient['travel_seconds'][q['hospital_id']])
                                  for q in accepted],
                        own_pending=own, observations=ledger['observations'], pending=ledger['pending'],
                        deadline_left=patient['at'] + 300 - at, hold=hold,
                        held_seconds=at - holds[pid]['started_at'] if pid in holds else 0)
                    if decision['action'] == 'confirm':
                        confirm(patient, q_best, now, decision['reason'], decision)
                    else:
                        if pid not in holds:
                            holds[pid] = dict(started_at=at, first_best=q_best['hospital_id'])
                            record('confirmation_hold_started', q_best, evidence=decision['evidence'])
                        holds[pid]['next_check'] = at + confirm_policy.CHECK_SECONDS
                elif at >= patient['at'] + 300:
                    service.cancel_patient(conn, now, pid, '실험 관측 종료', actor='experiment_observation_end')
                    outcomes[pid] = dict(selected_at=None, selection_seconds=None, travel_seconds=None,
                                         estimated_arrival_seconds=None, destination_hospital_id=None)
                    finished.add(pid)
                    holds.pop(pid, None)
            workflow_step(path, estimator, clock=lambda: now)
            errors = list(conn.execute("SELECT patient_id,last_error FROM inquiry_workflows WHERE state='ERROR'"))
            if errors:
                raise RuntimeError('server workflow failed: ' + repr([tuple(r) for r in errors]))
            for q in db.rows(conn.execute('SELECT * FROM inquiries ORDER BY id')):
                if q['patient_id'] not in by_pid or q['id'] in seen:
                    continue
                seen.add(q['id'])
                queues[q['hospital_id']].append(q)
                record('queue_added', q)
            for hid in sorted(queues):
                waiting = deque()
                for q in queues[hid]:
                    if is_open(q['id']):
                        waiting.append(q)
                    else:
                        record('review_cancelled_before_start', q)
                queues[hid] = waiting
                available = workers[hid] - sum(x['q']['hospital_id'] == hid for x in active)
                for _ in range(available):
                    if not queues[hid]:
                        break
                    q = queues[hid].popleft()
                    potential = potentials[(q['patient_id'], hid)]
                    duration = potential['service_seconds']
                    active.append(dict(q=q, potential=potential, started_at=at, due=at + duration))
                    work_seconds += duration
                    record('review_started', q, service_seconds=duration, due_at=at + duration)
            if len(finished) == len(patients) and not active:
                break
            next_times = [p['at'] for p in patients if p['at'] > at]
            next_times.extend(p['at'] + 300 for p in patients if p['id'] not in finished)
            next_times.extend(x['due'] for x in active)
            next_times.extend(r[0] - ie.EPOCH for r in conn.execute(
                'SELECT next_evaluation_at FROM inquiry_workflows WHERE enabled=1') if r[0] > now)
            next_times.extend(h['next_check'] for h in holds.values())
            future = [t for t in next_times if at < t]
            if not future:
                break
            at = min(future)
        rows = []
        for patient in patients:
            pid = patient['id']
            contacts = [r[0] for r in conn.execute('SELECT hospital_id FROM inquiries WHERE patient_id=? ORDER BY id', (pid,))]
            result = outcomes[pid]
            rows.append(dict(id=pid, created_at=patient['at'], **result, failed=result['selected_at'] is None,
                             inquiry_count=len(contacts), contacted_hospitals=contacts))
        decisions = []
        for r in db.rows(conn.execute('SELECT * FROM inquiry_policy_decisions ORDER BY id')):
            value = dict(at=r['at'] - ie.EPOCH, patient_id=r['patient_id'], reason=r['reason'],
                         hospital_ids=r['hospital_ids'])
            if r['patient_id'] not in observed_first:
                value['evidence'] = r['evidence']
                observed_first.add(r['patient_id'])
            decisions.append(value)
        metrics = ie.summarize_patients(rows)
        metrics['review_work_seconds'] = work_seconds
        metrics['review_started_count'] = sum(x['type'] == 'review_started' for x in trace)
        metrics['cancelled_before_start_count'] = sum(x['type'] == 'review_cancelled_before_start' for x in trace)
        metrics['reply_suppressed_count'] = sum(x['type'] == 'reply_suppressed_after_cancel' for x in trace)
        return dict(policy=policy, rule=rule, policy_spec=candidate_policy.spec(policy),
                    snapshot_sha256=initial_hash, environment_sha256=environment_hash, patients=rows,
                    metrics=metrics, decisions=decisions, trace=trace, holds=hold_log)
    finally:
        conn.close()


def run_candidate(scenario,arm,*,workflow_step,records):
    """One candidate branch, with explicit caller-owned capture records."""
    scenario=deepcopy(scenario);ie.validate(scenario);name,rule=arm
    candidate_policy.spec(name);confirm_policy.parse_rule(rule)
    environment=ie.digest(scenario)
    with tempfile.TemporaryDirectory(prefix='declinewake-execution-') as directory:
        base=ie._base_database(Path(directory)/'base.db',scenario)
        try:
            initial=ie.snapshot_digest(base);path=Path(directory)/'candidate.db';clone=db.connect(path)
            try:base.backup(clone)
            finally:clone.close()
            branch=_branch(path,scenario,name,rule,initial,environment,workflow_step=workflow_step)
            inspection=db.connect(path)
            try:
                decisions=db.rows(inspection.execute('SELECT * FROM inquiry_policy_decisions ORDER BY id'))
                if len(records)!=len(decisions):raise AssertionError('local captured policy/decision inventory differs')
                for call,decision in zip(records,decisions):
                    output=call['output'];temporal=call['inputs']['time_context'];source=call['workflow_snapshot']
                    if (call['name']!=decision['policy_name'] or call['db_decision_id']!=decision['id'] or call['patient_id']!=decision['patient_id'] or
                            call['inputs']['now']!=decision['at'] or output['send']!=decision['hospital_ids'] or output['reason']!=decision['reason'] or output['evidence']!=decision['evidence']):
                        raise AssertionError('local recorded policy differs from committed decision')
                    if temporal['patient_id']!=decision['patient_id'] or temporal['decision_prefix_max']>=decision['id'] or temporal['job_version']!=source['job']['version'] or source['job']['version']!=decision['job_version']:
                        raise AssertionError('local public time/apply provenance differs')
                    call['at']=decision['at']-ie.EPOCH
                branch.update(policy_calls=deepcopy(records),db_events=db.rows(inspection.execute('SELECT * FROM events ORDER BY id')),
                    db_inquiries=db.rows(inspection.execute('SELECT * FROM inquiries ORDER BY id')),db_decisions=decisions)
            finally:inspection.close()
        finally:base.close()
    return dict(schema_version=1,scenario=scenario,initial_snapshot_sha256=initial,environment_sha256=environment,arms=[list(arm)],branches=[branch])
