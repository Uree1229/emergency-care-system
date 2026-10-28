"""4차 2단계 환경: 병원이 검토를 시작하면 '확인 중'(checking)을 누르는 합성 사건을 넣은 실행기.

서버 실험기(`server.inquiry_experiment._branch`)와 같은 순서로 진행하고, 병원 검토가 시작될 때
아래 규칙으로 '확인 중' 회신(`service.respond(..., 'checking')`)을 예약한다. 확정은 즉시 확정이다.

- 시나리오마다 누르는 비율 ρ ∈ {0.3, 0.6, 0.9}(별도 난수열). 결국 회신할 문의는 ρ, 끝내 무응답인
  문의는 0.1ρ의 확률로 누른다("확인 중을 눌렀지만 답이 없음"도 드물게 있다).
- 누르는 시각은 검토 시작 후 0~10초. 그 전에 결정이 끝나면 누르지 않는다.
- 같은 환자·병원 문의의 누름 여부와 시각은 정책과 무관하게 같다(공통 난수).
- ack_mode='off'면 확인 중을 보내지 않으며, 원래 서버 실험기와 결과가 같다(test로 확인).
수치는 설계 가정이다. 실제 병원 담당자의 버튼 사용률·시각은 측정하지 않았다.
"""
from collections import deque
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import random
import tempfile

from server import db, inquiry_experiment as ie, inquiry_policies, inquiry_workflow, service

ACK_RATES = (0.3, 0.6, 0.9)
SILENT_ACK_FACTOR = 0.1
ACK_MAX_DELAY = 10
# perfect: 결국 회신할 문의는 검토 시작 즉시 반드시 누르고, 무응답 문의는 누르지 않는다.
# '물어본 뒤에 알게 되는' 관여 정보의 상한을 재는 비교 환경이다(실제 운영 가정 아님).
MODES = ('off', 'on', 'perfect')


def _rng(*keys):
    token = '\x1f'.join(str(k) for k in ('ack-environment', *keys)).encode('utf-8')
    return random.Random(int.from_bytes(sha256(token).digest(), 'big'))


def ack_rate(scenario):
    return _rng(scenario['id'], 'rate').choice(ACK_RATES)


def ack_plan(scenario, patient_id, hospital_id, potential):
    """이 문의가 검토 시작 후 몇 초에 확인 중을 누르는지(누르지 않으면 None)."""
    rate = ack_rate(scenario)
    r = _rng(scenario['id'], patient_id, hospital_id)
    press = r.random() < (rate * SILENT_ACK_FACTOR if potential['no_response'] else rate)
    delay = r.randint(0, ACK_MAX_DELAY)
    if not press or delay >= potential['service_seconds']:
        return None
    return delay


def _branch(path, scenario, policy, mode, expected_hash, environment_hash):
    conn = db.connect(path)
    initial_hash = ie.snapshot_digest(conn)
    if initial_hash != expected_hash:
        raise AssertionError('branch initial snapshot differs')
    patients = sorted(scenario['patients'], key=lambda p: (p['at'], p['id']))
    by_pid = {p['id']: p for p in patients}
    potentials = {(r['patient_id'], r['hospital_id']): r for r in scenario['potentials']}
    queues = {h['id']: deque() for h in scenario['hospitals']}
    workers = {h['id']: h['workers'] for h in scenario['hospitals']}
    active, acks, seen, finished, outcomes, trace = [], [], set(), set(), {}, []
    estimator = ie.MatrixEstimator(scenario['hospitals'], patients)
    work_seconds = 0
    observed_first = set()
    at = 0

    def record(typ, q, **extra):
        trace.append(dict(at=at, type=typ, patient_id=q['patient_id'], hospital_id=q['hospital_id'],
                          inquiry_id=q['id'], **extra))

    def is_open(iid):
        return conn.execute('SELECT state FROM inquiries WHERE id=?', (iid,)).fetchone()[0] in service.OPEN_INQUIRY

    try:
        while True:
            now = ie.EPOCH + at
            for patient in patients:
                if patient['at'] == at:
                    service.create_patient(conn, now, patient['id'], ie._payload(patient, policy),
                                           actor='experiment_patient')
            due_acks = sorted([x for x in acks if x['due'] <= at], key=lambda x: (x['due'], x['q']['id']))
            acks = [x for x in acks if x['due'] > at]
            for task in due_acks:
                q = task['q']
                if is_open(q['id']):
                    service.respond(conn, now, q['id'], 'checking', '합성 검토자')
                    record('reply_checking', q)
            completing = sorted([x for x in active if x['due'] <= at],
                                key=lambda x: (x['due'], x['q']['hospital_id'], x['q']['id']))
            active = [x for x in active if x['due'] > at]
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
                    q = min(accepted, key=lambda q: (patient['travel_seconds'][q['hospital_id']], q['hospital_id']))
                    service.confirm_destination(conn, now, pid, q['id'], actor='experiment_human_zero_delay')
                    travel = patient['travel_seconds'][q['hospital_id']]
                    outcomes[pid] = dict(selected_at=at, selection_seconds=at - patient['at'], travel_seconds=travel,
                                         estimated_arrival_seconds=at - patient['at'] + travel,
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
                    if mode == 'on':
                        delay = ack_plan(scenario, q['patient_id'], hid, potential)
                    elif mode == 'perfect':
                        delay = None if potential['no_response'] else 0
                    else:
                        delay = None
                    if delay is not None:
                        if delay == 0:
                            service.respond(conn, now, q['id'], 'checking', '합성 검토자')
                            record('reply_checking', q)
                        else:
                            acks.append(dict(q=q, due=at + delay))
            if len(finished) == len(patients) and not active:
                break
            next_times = [p['at'] for p in patients if p['at'] > at]
            next_times.extend(p['at'] + 300 for p in patients if p['id'] not in finished)
            next_times.extend(x['due'] for x in active)
            next_times.extend(x['due'] for x in acks)
            next_times.extend(r[0] - ie.EPOCH for r in conn.execute(
                'SELECT next_evaluation_at FROM inquiry_workflows WHERE enabled=1') if r[0] > now)
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
        metrics['checking_count'] = sum(x['type'] == 'reply_checking' for x in trace)
        return dict(policy=policy, mode=mode, policy_spec=inquiry_policies.spec(policy), snapshot_sha256=initial_hash,
                    environment_sha256=environment_hash, patients=rows, metrics=metrics, decisions=decisions,
                    trace=trace)
    finally:
        conn.close()


def run_scenario(scenario, arms):
    """arms: [(정책, 'off'|'on')]. 모든 분기는 같은 초기 DB에서 시작하고 같은 병원 잠재 회신을 쓴다."""
    scenario = deepcopy(scenario)
    ie.validate(scenario)
    arms = [tuple(a) for a in arms]
    if not arms or len(arms) != len(set(arms)):
        raise ValueError('unique arms are required')
    for policy, mode in arms:
        inquiry_policies.spec(policy)
        if mode not in MODES:
            raise ValueError('ack mode must be off, on or perfect')
    environment_hash = ie.digest(scenario)
    with tempfile.TemporaryDirectory(prefix='inquiry-lab-ack-') as directory:
        root = Path(directory)
        base = ie._base_database(root / 'base.db', scenario)
        try:
            initial_hash = ie.snapshot_digest(base)
            branches = []
            for index, (policy, mode) in enumerate(arms):
                path = root / f'arm{index}.db'
                branch = db.connect(path)
                base.backup(branch)
                branch.close()
                branches.append(_branch(path, scenario, policy, mode, initial_hash, environment_hash))
        finally:
            base.close()
    return dict(scenario={k: scenario.get(k) for k in ('id', 'family', 'seed', 'description')},
                initial_snapshot_sha256=initial_hash, environment_sha256=environment_hash, branches=branches,
                ack_rate=ack_rate(scenario))
