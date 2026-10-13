"""실제 문의 워크플로를 복제 DB와 합성 회신 환경에서 비교한다.

python -m server.inquiry_experiment --output simulation_results/server_inquiry_comparison_20261012
운영 DB·외부 API·HTTP 워커를 사용하지 않는다. 정책 파라미터는 변경하지 않는다.
"""
from __future__ import annotations

import argparse
from collections import deque
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import platform
import tempfile

from . import db, inquiry_policies, inquiry_workflow, service

EPOCH = 1_800_000_000
# 최초 네 정책의 비교 계약을 유지한다. 신규 후보는 별도 실험에서 명시한다.
POLICIES = ('fixed_1', 'fixed_3', 'fixed_5', 'observed_v2')
LIMITATIONS = [
    '실제 서버 문의 코드의 합성 환경 비교이며 현행 119 체계 복제나 임상 효과 검증이 아니다.',
    '병원·환자·이동시간·회신·문의 처리 인원은 합성값이다. 공식 평균 선정시간을 고정 응답시간으로 쓰지 않았다.',
    '치료 자원은 충분하게 두어 문의 처리 부하를 분리했다. 병상 부족·수술·이송 중 재배정·CP-SAT는 이번 비교 범위가 아니다.',
    '선정은 유효한 수용 회신 후 실험자가 지연 없이 확정한 시각이다. 실제 사람의 확인·통화 시간을 포함하지 않는다.',
    '예상 도착은 선정 경과시간+고정 합성 이동시간이며 실제 구급차 이동이나 치료·예후가 아니다.',
    '환자마다 발생 후 300초까지 관측한다. 작업이 EXHAUSTED여도 그 안의 뒤늦은 유효 회신은 인정한다.',
    '회신 누락은 합성 검토 작업이 끝난 뒤 답을 보내지 않는 모형이다. 검토자 영구 점유와 다르다.',
    '취소된 미착수 문의는 큐에서 제거하며, 이미 착수한 문의는 끝까지 업무시간을 소비한다. 기존 지도 큐 모형과 별도 가정이다.',
    '고정 설정 4종을 변경 없이 비교했다. 결과를 보고 재튜닝하거나 최적 정책·전국 개선율을 선언하지 않는다.',
]


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode('utf-8')).hexdigest()


def snapshot_digest(conn):
    """파일 헤더와 WAL 상태에 의존하지 않는 논리 SQL 스냅샷 해시."""
    return hashlib.sha256('\n'.join(conn.iterdump()).encode('utf-8')).hexdigest()


def _integer(value, minimum=0):
    return type(value) is int and value >= minimum


def validate(scenario):
    if not isinstance(scenario, dict) or not scenario.get('id'):
        raise ValueError('scenario id is required')
    hospitals, patients = scenario['hospitals'], scenario['patients']
    hids, pids = [h['id'] for h in hospitals], [p['id'] for p in patients]
    if not hids or not pids or len(hids) != len(set(hids)) or len(pids) != len(set(pids)):
        raise ValueError('unique nonempty hospitals and patients are required')
    if scenario.get('horizon_seconds', 300) != 300:
        raise ValueError('this experiment uses the frozen 300-second server deadline')
    if any(not _integer(h['workers'], 1) for h in hospitals):
        raise ValueError('workers must be positive integers')
    if len({(h['lat'], h['lon']) for h in hospitals}) != len(hospitals):
        raise ValueError('hospital coordinates must be distinct')
    travel_by_origin = {}
    for p in patients:
        if not _integer(p['at']) or set(p['travel_seconds']) != set(hids):
            raise ValueError('patient arrival and travel matrix are required')
        if any(not _integer(t, 1) for t in p['travel_seconds'].values()):
            raise ValueError('travel seconds must be positive integers')
        origin = (p['lat'], p['lon'])
        if origin in travel_by_origin and travel_by_origin[origin] != p['travel_seconds']:
            raise ValueError('same origin must have the same fixed travel estimates')
        travel_by_origin[origin] = p['travel_seconds']
    pairs = [(p['patient_id'], p['hospital_id']) for p in scenario['potentials']]
    if len(pairs) != len(set(pairs)) or set(pairs) != {(p, h) for p in pids for h in hids}:
        raise ValueError('one shared potential response is required for every patient/hospital pair')
    for value in scenario['potentials']:
        if (not _integer(value['service_seconds'], 1) or
                type(value['no_response']) is not bool or type(value['accept']) is not bool):
            raise ValueError('invalid potential response')
    for row in scenario.get('history', []):
        if (row['patient_id'] in pids or row['hospital_id'] not in hids or
                type(row['sent_at']) is not int or row['sent_at'] >= 0 or
                row['action'] not in ('decline', 'checking') or
                (row['reply_at'] is not None and (type(row['reply_at']) is not int or
                 not row['sent_at'] <= row['reply_at'] < 0))):
            raise ValueError('history must contain only past, separate patients')


class MatrixEstimator:
    """좌표만으로 고정 이동시간을 찾는다. 회신 환경은 보유하지 않는다."""
    def __init__(self, hospitals, patients):
        by_hid = {h['id']: (h['lat'], h['lon']) for h in hospitals}
        self.values = {((p['lat'], p['lon']), by_hid[hid]): seconds
                       for p in patients for hid, seconds in p['travel_seconds'].items()}

    def estimate(self, origin, destination):
        seconds = self.values[(tuple(origin), tuple(destination))]
        return dict(duration_seconds=seconds, distance_meters=0, provider='synthetic_matrix',
                    scenario='frozen server experiment; not a road estimate')


def _payload(patient, policy=None):
    data = dict(reporter_role='transporter', consent=True, symptoms='가상 실험 환자',
                required_capabilities=['응급의학'], resource_demands={'ed_bed': 1},
                lat=patient['lat'], lon=patient['lon'])
    if policy is not None:
        data.update(auto_inquiry=True, inquiry_policy=policy)
    return data


def _base_database(path, scenario):
    conn = db.connect(path)
    db.init(conn)
    for hospital in scenario['hospitals']:
        service.upsert_hospital(conn, {k: hospital[k] for k in ('id', 'name', 'lat', 'lon', 'capabilities')} |
                                {'source': 'synthetic'})
        service.set_resource(conn, EPOCH, hospital['id'], 'ed_bed', 10000, 0, source='hospital_input')
    # 실제 서비스 함수로 과거 사건을 만들되 현재 환자와 자동 작업을 만들지 않는다.
    history = scenario.get('history', [])
    for row in sorted(history, key=lambda r: (r['sent_at'], r['patient_id'], r['hospital_id'])):
        pid = row['patient_id']
        if conn.execute('SELECT 1 FROM patients WHERE id=?', (pid,)).fetchone() is None:
            p = scenario['patients'][0]
            service.create_patient(conn, EPOCH + row['sent_at'] - 1, pid, _payload(p))
    events = []
    for row in history:
        events.append((row['sent_at'], 0, row))
        if row['reply_at'] is not None:
            events.append((row['reply_at'], 1, row))
    for at, action, row in sorted(events, key=lambda item: (item[0], item[1], item[2]['patient_id'], item[2]['hospital_id'])):
        if action == 0:
            service.send_inquiries(conn, EPOCH + at, row['patient_id'], [row['hospital_id']], actor='experiment_history')
        else:
            iid = conn.execute('SELECT id FROM inquiries WHERE patient_id=? AND hospital_id=?',
                               (row['patient_id'], row['hospital_id'])).fetchone()[0]
            service.respond(conn, EPOCH + at, iid, row['action'], '합성 과거 담당자',
                            reason='기타' if row['action'] == 'decline' else None)
    return conn


def _mean(values):
    values = list(values)
    return sum(values) / len(values) if values else None


def summarize_patients(patients):
    selected = [p for p in patients if not p['failed']]
    return dict(patient_count=len(patients), selected_count=len(selected),
                failed_count=len(patients) - len(selected),
                selection_seconds_mean=_mean(p['selection_seconds'] for p in selected),
                selection_wait_capped_mean=_mean(p['selection_seconds'] if not p['failed'] else 300 for p in patients),
                inquiries_per_patient=_mean(p['inquiry_count'] for p in patients),
                estimated_arrival_seconds_mean=_mean(p['estimated_arrival_seconds'] for p in selected))


def paired_summary(branch, baseline):
    ours = {p['id']: p for p in branch['patients']}
    theirs = {p['id']: p for p in baseline['patients']}
    if set(ours) != set(theirs):
        raise ValueError('paired results must contain the same patient IDs')
    common = [pid for pid in ours if not ours[pid]['failed'] and not theirs[pid]['failed']]
    return dict(common_patient_count=len(common),
                common_failed_count=sum(ours[p]['failed'] and theirs[p]['failed'] for p in ours),
                selected_only_policy_count=sum(not ours[p]['failed'] and theirs[p]['failed'] for p in ours),
                selected_only_baseline_count=sum(ours[p]['failed'] and not theirs[p]['failed'] for p in ours),
                common_selection_difference_seconds_mean=_mean(ours[p]['selection_seconds'] - theirs[p]['selection_seconds'] for p in common),
                common_arrival_difference_seconds_mean=_mean(ours[p]['estimated_arrival_seconds'] - theirs[p]['estimated_arrival_seconds'] for p in common),
                inquiries_difference_per_patient=_mean(ours[p]['inquiry_count'] - theirs[p]['inquiry_count'] for p in ours))


def _branch(path, scenario, policy, expected_hash, environment_hash, tick_seconds=None):
    conn = db.connect(path)
    initial_hash = snapshot_digest(conn)
    if initial_hash != expected_hash:
        raise AssertionError('branch initial snapshot differs')
    patients = sorted(scenario['patients'], key=lambda p: (p['at'], p['id']))
    by_pid = {p['id']: p for p in patients}
    potentials = {(r['patient_id'], r['hospital_id']): r for r in scenario['potentials']}
    queues = {h['id']: deque() for h in scenario['hospitals']}
    workers = {h['id']: h['workers'] for h in scenario['hospitals']}
    active, seen, finished, outcomes, trace = [], set(), set(), {}, []
    estimator = MatrixEstimator(scenario['hospitals'], patients)
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
            now = EPOCH + at
            for patient in patients:
                if patient['at'] == at:
                    service.create_patient(conn, now, patient['id'], _payload(patient, policy), actor='experiment_patient')
            # 같은 시각에 끝난 모든 회신을 반영한 뒤 공통 확정 규칙을 적용한다.
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
            for q in db.rows(conn.execute('SELECT * FROM inquiries ORDER BY id')):
                if q['patient_id'] not in by_pid or q['id'] in seen:
                    continue
                seen.add(q['id'])
                queues[q['hospital_id']].append(q)
                record('queue_added', q)
            for hid in sorted(queues):
                # 취소 알림이 도착한 미착수 요청은 제거한다. 착수한 요청은 중단하지 않는다.
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
            next_times.extend(r[0] - EPOCH for r in conn.execute('SELECT next_evaluation_at FROM inquiry_workflows WHERE enabled=1')
                              if r[0] > now)
            if tick_seconds is not None:
                next_times.append(at + tick_seconds)
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
            value = dict(at=r['at'] - EPOCH, patient_id=r['patient_id'], reason=r['reason'], hospital_ids=r['hospital_ids'])
            if r['patient_id'] not in observed_first:
                value['evidence'] = r['evidence']
                observed_first.add(r['patient_id'])
            decisions.append(value)
        metrics = summarize_patients(rows)
        metrics['review_work_seconds'] = work_seconds
        metrics['review_started_count'] = sum(x['type'] == 'review_started' for x in trace)
        metrics['cancelled_before_start_count'] = sum(x['type'] == 'review_cancelled_before_start' for x in trace)
        metrics['reply_suppressed_count'] = sum(x['type'] == 'reply_suppressed_after_cancel' for x in trace)
        return dict(policy=policy, policy_spec=inquiry_policies.spec(policy), snapshot_sha256=initial_hash,
                    environment_sha256=environment_hash, patients=rows, metrics=metrics, decisions=decisions, trace=trace)
    finally:
        conn.close()


def run_scenario(scenario, policies=POLICIES, *, tick_seconds=None):
    scenario = deepcopy(scenario)
    validate(scenario)
    policies = tuple(policies)
    if not policies or len(policies) != len(set(policies)):
        raise ValueError('unique policies are required')
    for policy in policies:
        inquiry_policies.spec(policy)
    if tick_seconds is not None and not _integer(tick_seconds, 1):
        raise ValueError('tick_seconds must be positive')
    environment_hash = digest(scenario)
    with tempfile.TemporaryDirectory(prefix='server-inquiry-experiment-') as directory:
        root = Path(directory)
        base = _base_database(root / 'base.db', scenario)
        try:
            initial_hash = snapshot_digest(base)
            branches = []
            for policy in policies:
                path = root / (policy + '.db')
                branch = db.connect(path)
                base.backup(branch)
                branch.close()
                branches.append(_branch(path, scenario, policy, initial_hash, environment_hash, tick_seconds))
        finally:
            base.close()
    return dict(scenario={k: scenario.get(k) for k in ('id', 'family', 'seed', 'description')},
                initial_snapshot_sha256=initial_hash, environment_sha256=environment_hash, branches=branches)


def aggregate(results):
    output = []
    for family in ['ALL'] + sorted({r['scenario']['family'] for r in results}):
        records = results if family == 'ALL' else [r for r in results if r['scenario']['family'] == family]
        names = [b['policy'] for b in records[0]['branches']]
        for name in names:
            branches = [next(b for b in r['branches'] if b['policy'] == name) for r in records]
            patients = [p for b in branches for p in b['patients']]
            metrics = summarize_patients(patients)
            metrics['review_work_seconds'] = sum(b['metrics']['review_work_seconds'] for b in branches)
            metrics['review_work_seconds_per_patient'] = metrics['review_work_seconds'] / len(patients)
            metrics['review_started_per_patient'] = sum(b['metrics']['review_started_count'] for b in branches) / len(patients)
            common, both_failed, only_us, only_base, deltas, arrival_deltas = 0, 0, 0, 0, [], []
            baseline_available = all(any(b['policy'] == 'fixed_3' for b in r['branches']) for r in records)
            if baseline_available:
                for result, branch in zip(records, branches):
                    baseline = next(b for b in result['branches'] if b['policy'] == 'fixed_3')
                    paired = paired_summary(branch, baseline)
                    common += paired['common_patient_count']
                    both_failed += paired['common_failed_count']
                    only_us += paired['selected_only_policy_count']
                    only_base += paired['selected_only_baseline_count']
                    ours = {p['id']: p for p in branch['patients']}
                    for p in baseline['patients']:
                        if not p['failed'] and not ours[p['id']]['failed']:
                            deltas.append(ours[p['id']]['selection_seconds'] - p['selection_seconds'])
                            arrival_deltas.append(ours[p['id']]['estimated_arrival_seconds'] - p['estimated_arrival_seconds'])
            output.append(dict(family=family, policy=name, scenario_count=len(records), **metrics,
                               common_patient_count_vs_fixed3=common, selected_only_policy_vs_fixed3=only_us,
                               selected_only_fixed3=only_base, common_failed_vs_fixed3=both_failed,
                               common_selection_difference_vs_fixed3=_mean(deltas),
                               common_arrival_difference_vs_fixed3=_mean(arrival_deltas)))
    return output


def family_equal_summary(summary):
    """단일/다수 환자 조건의 환자 수 차이를 제거한 조건 균등 평균."""
    output = []
    for policy in dict.fromkeys(row['policy'] for row in summary):
        rows = [row for row in summary if row['family'] != 'ALL' and row['policy'] == policy]
        output.append(dict(policy=policy, family_count=len(rows),
                           success_fraction_mean=_mean(row['selected_count'] / row['patient_count'] for row in rows),
                           selection_wait_capped_mean=_mean(row['selection_wait_capped_mean'] for row in rows),
                           inquiries_per_patient_mean=_mean(row['inquiries_per_patient'] for row in rows),
                           review_work_seconds_per_patient_mean=_mean(row['review_work_seconds_per_patient'] for row in rows)))
    return output


def source_fingerprints():
    root = Path(__file__).resolve().parents[1]
    sources = ('server/inquiry_experiment.py', 'server/inquiry_experiment_scenarios.py',
               'server/inquiry_workflow.py', 'server/inquiry_policies.py', 'server/inquiry_observations.py',
               'server/service.py', 'server/db.py', 'server/treatment_binding.py', 'simulation/observed_inquiry.py')
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in sources}


def markdown(report):
    def number(value):
        return '—' if value is None else f'{value:.2f}'
    lines = ['# 서버 문의 정책 비교', '', '실제 서버의 문의 코드를 같은 초기 DB와 합성 회신 환경에서 실행했다. 시간 단위는 초다.', '',
             f"시나리오 {len(report['results'])}개 · 정책 분기 {sum(len(r['branches']) for r in report['results'])}개. 정책 재튜닝 없음.", '',
             '선정시간 평균은 성공한 환자만, 300초 제한 대기 평균은 실패를 300초로 포함한다. 비교 시간차는 양쪽 모두 선정된 동일 환자만 사용한다. ALL은 전체 환자 기준이며 환자 8명 조건이 단일 환자 조건보다 큰 비중을 갖는다.', '',
             '## 전체 환자 기준과 조건 균등 평균', '',
             '조건 균등 평균은 각 유형에 같은 비중을 준다. 다수 환자 조건은 전체 환자 평균에서 더 큰 비중을 차지하므로 두 집계를 함께 확인한다. 기본 64개 입력에서는 동시·순차 조건의 환자 128명이 전체 176명 중 72.7%다.', '',
             '| 정책 | 유형 수 | 유형별 성공률 평균 | 유형별 300초 제한 대기 평균 | 유형별 문의/환자 평균 |',
             '|---|---:|---:|---:|---:|']
    for row in report.get('family_equal_summary', family_equal_summary(report['summary'])):
        lines.append(f"| {row['policy']} | {row['family_count']} | {number(row['success_fraction_mean'] * 100)}% | {number(row['selection_wait_capped_mean'])} | {number(row['inquiries_per_patient_mean'])} |")
    lines += ['', '## 전체 환자 및 유형별 결과', '',
              '| 조건 | 정책 | 성공/전체 | 선정 평균 | 제한 대기 평균 | 문의/환자 | 공통 환자 수 | 선정 차이 vs 고정3 | 예상 도착 차이 vs 고정3 |',
              '|---|---|---:|---:|---:|---:|---:|---:|---:|']
    for row in report['summary']:
        lines.append(f"| {row['family']} | {row['policy']} | {row['selected_count']}/{row['patient_count']} | {number(row['selection_seconds_mean'])} | {number(row['selection_wait_capped_mean'])} | {number(row['inquiries_per_patient'])} | {row['common_patient_count_vs_fixed3']} | {number(row['common_selection_difference_vs_fixed3'])} | {number(row['common_arrival_difference_vs_fixed3'])} |")
    lines += ['', '음수 시간차는 고정3보다 빠름, 양수는 느림이다. 선정이 빨라도 더 먼 병원을 고르면 예상 도착은 늦어질 수 있다.', '',
              '## 성공 분모와 실제 검토 업무', '',
              '| 조건 | 정책 | 공통 성공 vs 고정3 | 해당 정책만 성공 | 고정3만 성공 | 공통 실패 | 검토 착수/환자 | 착수한 전체 업무시간/환자(초) |',
              '|---|---|---:|---:|---:|---:|---:|---:|']
    for row in report['summary']:
        lines.append(f"| {row['family']} | {row['policy']} | {row['common_patient_count_vs_fixed3']} | {row['selected_only_policy_vs_fixed3']} | {row['selected_only_fixed3']} | {row['common_failed_vs_fixed3']} | {number(row['review_started_per_patient'])} | {number(row['review_work_seconds_per_patient'])} |")
    lines += ['', '착수 업무시간은 취소 이후에도 끝까지 소비하는 작업을 포함한다. 병원 전체 업무시간·의료진 인력 수를 뜻하지 않는다.', '',
              '## 재현과 검증', '',
              '각 분기는 초기 SQL 스냅샷 해시와 전체 잠재 환경 해시가 같다. 정책에는 실제 DB에서 관측된 사건만 전달한다. JSON에는 환자별 결과·문의/검토 이력·첫 판단 근거와 이후 판단 사유를 저장한다.', '',
              '## 실험 범위와 한계', '']
    lines.extend('- ' + value for value in LIMITATIONS)
    return '\n'.join(lines) + '\n'


def main():
    from .inquiry_experiment_scenarios import make_suite
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('simulation_results/server_inquiry_comparison_20261012'))
    parser.add_argument('--seed-count', type=int, default=8)
    parser.add_argument('--seed-start', type=int, default=6101)
    args = parser.parse_args()
    if not 1 <= args.seed_count <= 100:
        parser.error('seed-count must be 1..100')
    paths = [args.output.with_suffix(suffix) for suffix in ('.json', '.md')]
    if any(path.exists() for path in paths):
        parser.error('output already exists; choose a new output path')
    scenarios = make_suite(seeds=tuple(range(args.seed_start, args.seed_start + args.seed_count)))
    sources = source_fingerprints()
    results = []
    for index, scenario in enumerate(scenarios, 1):
        results.append(run_scenario(scenario))
        print(f"{index}/{len(scenarios)} {scenario['id']}", flush=True)
    if source_fingerprints() != sources:
        raise RuntimeError('source files changed during experiment; results were not published')
    summary = aggregate(results)
    report = dict(kind='server_inquiry_policy_synthetic_comparison',
                  seeds=list(range(args.seed_start, args.seed_start + args.seed_count)),
                  parameters_frozen=True, clinically_validated=False, policies=[inquiry_policies.spec(p) for p in POLICIES],
                  source_sha256=sources, python_version=platform.python_version(),
                  limitations=LIMITATIONS, scenarios=scenarios, results=results, summary=summary,
                  family_equal_summary=family_equal_summary(summary))
    paths[0].parent.mkdir(parents=True, exist_ok=True)
    paths[0].write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    paths[1].write_text(markdown(report), encoding='utf-8')
    print(json.dumps({'json': str(paths[0]), 'markdown': str(paths[1])}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
