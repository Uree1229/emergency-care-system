"""8차 실험 실행기: 서버 문의 실험기 분기를 옮겨 '병원 문의 처리 용량 규칙'만 바꿀 수 있게 한다.

`server.inquiry_experiment._branch`와 같은 순서로 환자 등록 → 회신 반영 → 확정 판단 → 문의 워크플로 →
병원 FIFO 처리를 진행한다. 용량 규칙 base는 원래 실험기와 같은 환자 결과·지표·사건 기록을 내야 한다
(test_inquiry_lab_capacity에서 확인). server 파일은 수정하지 않는다.

용량 규칙(모든 분기는 같은 시나리오·같은 초기 DB에서 시작하고, 문의 정책은 처리 인원을 보지 못한다)
- base: 생성된 처리 인원 그대로.
- min2: 처리 인원 1명 병원에 상시 2번째 담당을 둔다(7차 진단과 같은 환경).
- inf: 모든 병원 99명. 경합 손실의 상한을 재는 가상 환경.
- od_k{k}_d{d}_l{L}: 처리 인원 1명 병원마다 보조 담당 1명을 지정하고 필요할 때만 부른다.
  * 호출: 새 문의가 들어온 시각에 그 병원의 미회신 문의가 k건 이상이고 보조 담당이 쉬고 있으면 서버가 부른다.
    미회신 문의는 서버가 실제로 볼 수 있는 값이라 대기·검토 중·무응답으로 끝난 문의를 구분하지 않는다.
  * 합류: 호출 d초 뒤부터 일한다. 대기 문의는 원래 담당이 먼저 받고 보조는 넘치는 문의를 받는다.
  * 복귀: 보조 담당이 쉬고 있고 대기 문의가 없는 상태가 L초 이어지면 복귀한다. 검토 중인 문의는 끝까지 처리한다.
  * 근무시간은 호출부터 복귀까지(합류 대기·복귀 전 대기 포함)로 센다. 관측이 끝날 때 남아 있으면
    L초를 다 채운 것으로 보수적으로 센다.
"""
from collections import deque
from copy import deepcopy
from pathlib import Path
import re
import tempfile

from server import db, inquiry_experiment as ie, inquiry_policies, inquiry_workflow, service

INFINITE_WORKERS = 99
_ON_DEMAND = re.compile(r'od_k(\d+)_d(\d+)_l(\d+)')


def parse_rule(rule):
    if rule in ('base', 'min2', 'inf'):
        return dict(kind=rule)
    match = _ON_DEMAND.fullmatch(rule) if isinstance(rule, str) else None
    if not match:
        raise ValueError(f'unknown capacity rule: {rule!r}')
    k, delay, linger = (int(x) for x in match.groups())
    if k < 1:
        raise ValueError('k must be at least 1')
    return dict(kind='od', k=k, delay=delay, linger=linger)


def on_demand_name(k, delay, linger):
    return f'od_k{k}_d{delay}_l{linger}'


def _branch(path, scenario, policy, rule, expected_hash, environment_hash):
    spec = parse_rule(rule)
    conn = db.connect(path)
    initial_hash = ie.snapshot_digest(conn)
    if initial_hash != expected_hash:
        raise AssertionError('branch initial snapshot differs')
    patients = sorted(scenario['patients'], key=lambda p: (p['at'], p['id']))
    by_pid = {p['id']: p for p in patients}
    potentials = {(r['patient_id'], r['hospital_id']): r for r in scenario['potentials']}
    queues = {h['id']: deque() for h in scenario['hospitals']}
    workers = {h['id']: h['workers'] for h in scenario['hospitals']}
    if spec['kind'] == 'inf':
        workers = {hid: INFINITE_WORKERS for hid in workers}
    # min2는 '항상 합류해 있고 복귀하지 않는 보조 담당'으로 다룬다. 처리 순서는 처리 인원 2명과 같다.
    backups = {hid: dict(state='on' if spec['kind'] == 'min2' else 'off', called_at=0 if spec['kind'] == 'min2' else None,
                         arrive_at=0, idle_since=None)
               for hid, w in workers.items() if spec['kind'] in ('min2', 'od') and w == 1}
    calls, open_call = [], {}
    active, seen, finished, outcomes, trace = [], set(), set(), {}, []
    estimator = ie.MatrixEstimator(scenario['hospitals'], patients)
    work_seconds = 0
    observed_first = set()
    at = 0

    def record(typ, q, **extra):
        trace.append(dict(at=at, type=typ, patient_id=q['patient_id'], hospital_id=q['hospital_id'],
                          inquiry_id=q['id'], **extra))

    def record_backup(typ, hid, **extra):
        trace.append(dict(at=at, type=typ, hospital_id=hid, **extra))

    def is_open(iid):
        return conn.execute('SELECT state FROM inquiries WHERE id=?', (iid,)).fetchone()[0] in service.OPEN_INQUIRY

    def unanswered(hid):
        marks = ','.join('?' * len(service.OPEN_INQUIRY))
        return conn.execute(f'SELECT COUNT(*) FROM inquiries WHERE hospital_id=? AND state IN ({marks})',
                            (hid, *service.OPEN_INQUIRY)).fetchone()[0]

    def start(q, hid, by_backup):
        nonlocal work_seconds
        potential = potentials[(q['patient_id'], hid)]
        duration = potential['service_seconds']
        active.append(dict(q=q, potential=potential, started_at=at, due=at + duration, backup=by_backup))
        work_seconds += duration
        if by_backup:
            record('review_started', q, service_seconds=duration, due_at=at + duration, backup=True)
            if hid in open_call:
                open_call[hid]['reviews'] += 1
                open_call[hid]['busy_seconds'] += duration
            else:
                permanent[hid] = permanent.get(hid, 0) + duration
        else:
            record('review_started', q, service_seconds=duration, due_at=at + duration)

    def release(hid, when):
        call = open_call.pop(hid)
        call['released_at'] = when
        backups[hid].update(state='off', called_at=None, arrive_at=None, idle_since=None)
        record_backup('backup_released', hid, released_at=when, reviews=call['reviews'])

    permanent = {}
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
            for task in completing:
                q, potential = task['q'], task['potential']
                record('review_finished', q, started_at=task['started_at'], service_seconds=potential['service_seconds'])
                if not is_open(q['id']):
                    record('reply_suppressed_after_cancel', q)
                elif potential['no_response']:
                    record('reply_omitted', q)
                else:
                    action = 'accept' if potential['accept'] else 'decline'
                    service.respond(conn, now, q['id'], action, '합성 검토자', reason='기타' if action == 'decline' else None)
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
                    q = min(accepted, key=lambda q: (patient['travel_seconds'][q['hospital_id']], q['hospital_id']))
                    service.confirm_destination(conn, now, pid, q['id'], actor='experiment_human_zero_delay')
                    travel = patient['travel_seconds'][q['hospital_id']]
                    outcomes[pid] = dict(selected_at=at, selection_seconds=at - patient['at'],
                                         travel_seconds=travel, estimated_arrival_seconds=at - patient['at'] + travel,
                                         destination_hospital_id=q['hospital_id'])
                    finished.add(pid)
                    record('destination_confirmed', q)
                elif at >= patient['at'] + 300:
                    service.cancel_patient(conn, now, pid, '실험 관측 종료', actor='experiment_observation_end')
                    outcomes[pid] = dict(selected_at=None, selection_seconds=None, travel_seconds=None,
                                         estimated_arrival_seconds=None, destination_hospital_id=None)
                    finished.add(pid)
            inquiry_workflow.run_once(path, estimator, clock=lambda: now)
            errors = list(conn.execute("SELECT patient_id,last_error FROM inquiry_workflows WHERE state='ERROR'"))
            if errors:
                raise RuntimeError('server workflow failed: ' + repr([tuple(r) for r in errors]))
            arrived = set()
            for q in db.rows(conn.execute('SELECT * FROM inquiries ORDER BY id')):
                if q['patient_id'] not in by_pid or q['id'] in seen:
                    continue
                seen.add(q['id'])
                queues[q['hospital_id']].append(q)
                record('queue_added', q)
                arrived.add(q['hospital_id'])
            if spec['kind'] == 'od':
                # 서버는 이번 시각에 문의를 받은 병원의 미회신 문의 수만 보고 호출한다.
                for hid in sorted(arrived & set(backups)):
                    if backups[hid]['state'] != 'off':
                        continue
                    count = unanswered(hid)
                    if count >= spec['k']:
                        backups[hid].update(state='called', called_at=at, arrive_at=at + spec['delay'], idle_since=None)
                        open_call[hid] = dict(hospital_id=hid, called_at=at, arrive_at=at + spec['delay'],
                                              unanswered_at_call=count, reviews=0, busy_seconds=0)
                        calls.append(open_call[hid])
                        record_backup('backup_called', hid, unanswered=count, arrive_at=at + spec['delay'])
            for hid in sorted(queues):
                # 취소 알림이 도착한 미착수 요청은 제거한다. 착수한 요청은 중단하지 않는다.
                waiting = deque()
                for q in queues[hid]:
                    if is_open(q['id']):
                        waiting.append(q)
                    else:
                        record('review_cancelled_before_start', q)
                queues[hid] = waiting
                staff_busy = sum(x['q']['hospital_id'] == hid and not x['backup'] for x in active)
                for _ in range(workers[hid] - staff_busy):
                    if not queues[hid]:
                        break
                    start(queues[hid].popleft(), hid, False)
                b = backups.get(hid)
                if b is None:
                    continue
                if b['state'] == 'called' and b['arrive_at'] <= at:
                    b['state'] = 'on'
                    record_backup('backup_arrived', hid)
                if b['state'] != 'on':
                    continue
                busy = any(x['q']['hospital_id'] == hid and x['backup'] for x in active)
                if not busy and queues[hid]:
                    start(queues[hid].popleft(), hid, True)
                    busy = True
                if busy:
                    b['idle_since'] = None
                    continue
                if b['idle_since'] is None:
                    b['idle_since'] = at
                if spec['kind'] == 'od' and not queues[hid] and at >= b['idle_since'] + spec['linger']:
                    release(hid, at)
            if len(finished) == len(patients) and not active:
                break
            next_times = [p['at'] for p in patients if p['at'] > at]
            next_times.extend(p['at'] + 300 for p in patients if p['id'] not in finished)
            next_times.extend(x['due'] for x in active)
            next_times.extend(r[0] - ie.EPOCH for r in conn.execute(
                'SELECT next_evaluation_at FROM inquiry_workflows WHERE enabled=1') if r[0] > now)
            if spec['kind'] == 'od':
                next_times.extend(b['arrive_at'] for b in backups.values() if b['state'] == 'called')
                next_times.extend(b['idle_since'] + spec['linger'] for b in backups.values()
                                  if b['state'] == 'on' and b['idle_since'] is not None)
            future = [t for t in next_times if at < t]
            if not future:
                break
            at = min(future)
        end = at
        if spec['kind'] == 'od':
            for hid in sorted(open_call):
                b = backups[hid]
                # 합류 전이면 도착 뒤 할 일이 없어 L초를 기다린 것으로, 합류 중이면 마지막 유휴부터 L초로 센다.
                idle = b['arrive_at'] if b['state'] == 'called' else (b['idle_since'] if b['idle_since'] is not None else end)
                release(hid, max(end, idle + spec['linger']))
        rows = []
        for patient in patients:
            pid = patient['id']
            contacts = [r[0] for r in conn.execute('SELECT hospital_id FROM inquiries WHERE patient_id=? ORDER BY id', (pid,))]
            result = outcomes[pid]
            rows.append(dict(id=pid, created_at=patient['at'], **result, failed=result['selected_at'] is None,
                             inquiry_count=len(contacts), contacted_hospitals=contacts))
        decisions = []
        for r in db.rows(conn.execute('SELECT * FROM inquiry_policy_decisions ORDER BY id')):
            value = dict(at=r['at'] - ie.EPOCH, patient_id=r['patient_id'], reason=r['reason'], hospital_ids=r['hospital_ids'])
            if r['patient_id'] not in observed_first:
                value['evidence'] = r['evidence']
                observed_first.add(r['patient_id'])
            decisions.append(value)
        metrics = ie.summarize_patients(rows)
        metrics['review_work_seconds'] = work_seconds
        metrics['review_started_count'] = sum(x['type'] == 'review_started' for x in trace)
        metrics['cancelled_before_start_count'] = sum(x['type'] == 'review_cancelled_before_start' for x in trace)
        metrics['reply_suppressed_count'] = sum(x['type'] == 'reply_suppressed_after_cancel' for x in trace)
        backup = dict(hospitals=len(backups), calls=len(calls), false_alarms=sum(c['reviews'] == 0 for c in calls),
                      reviews=sum(c['reviews'] for c in calls),
                      busy_seconds=sum(c['busy_seconds'] for c in calls) + sum(permanent.values()),
                      on_duty_seconds=sum(c['released_at'] - c['called_at'] for c in calls),
                      permanent_hospital_seconds=end * len(backups) if spec['kind'] == 'min2' else 0,
                      end_seconds=end)
        return dict(policy=policy, rule=rule, policy_spec=inquiry_policies.spec(policy),
                    snapshot_sha256=initial_hash, environment_sha256=environment_hash, patients=rows,
                    metrics=metrics, backup=backup, calls=calls, decisions=decisions, trace=trace)
    finally:
        conn.close()


def run_scenario(scenario, arms):
    """arms: [(문의 정책, 용량 규칙)]. 모든 분기는 같은 초기 DB 스냅샷에서 시작한다."""
    scenario = deepcopy(scenario)
    ie.validate(scenario)
    arms = [tuple(a) for a in arms]
    if not arms or len(arms) != len(set(arms)):
        raise ValueError('unique arms are required')
    for policy, rule in arms:
        inquiry_policies.spec(policy)
        parse_rule(rule)
    environment_hash = ie.digest(scenario)
    with tempfile.TemporaryDirectory(prefix='inquiry-lab-capacity-') as directory:
        root = Path(directory)
        base = ie._base_database(root / 'base.db', scenario)
        try:
            initial_hash = ie.snapshot_digest(base)
            branches = []
            for index, (policy, rule) in enumerate(arms):
                path = root / f'arm{index}.db'
                branch = db.connect(path)
                base.backup(branch)
                branch.close()
                branches.append(_branch(path, scenario, policy, rule, initial_hash, environment_hash))
        finally:
            base.close()
    return dict(scenario={k: scenario.get(k) for k in ('id', 'family', 'seed', 'description')},
                initial_snapshot_sha256=initial_hash, environment_sha256=environment_hash, branches=branches)
