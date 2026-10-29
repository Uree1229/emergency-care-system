"""서버 문의 정책 검증 기록 → 지도 재생 데이터 변환기.

    python -m simulation.server_replay_adapter
    → simulation/viewer/server_replay_data.json (뷰어가 map_replay_data.json 뒤에 덧붙여 재생)

원칙 (docs/HANDOFF_20261012.md 다음 작업 1~5)
- 서버 실험의 원시 trace를 재계산하지 않는다. 시각은 초 → 분(소수)으로만 바꾸고 원래 초와 사건 이름을 함께 보존한다.
- 사례는 결과를 보기 전에 정한 규칙으로 고른다: 시나리오 유형마다 가장 낮은 검증 seed 1개.
  V3가 고정3보다 나은 사례와 느린 사례가 그대로 섞인다. 각 사례 설명에 V3 − 고정3 차이를 적는다.
- 무응답(reply_omitted)은 수용 불가와 구분하고, 다른 병원 확정 뒤 취소된 문의의 회신은 반영하지 않은 회신으로 표시한다.
- 이동시간·병원·환자는 서버 실험의 합성값이다. 도로 경로나 실제 병원 위치로 표시하지 않는다.
- 서버 실험은 문의·회신·선정까지만 다룬다. 치료 시작·완료, 이송 중 전원 효과를 만들어 내지 않는다.
  도착은 서버 기록의 예상 도착시각(선정 + 합성 이동시간)으로만 표시한다.
"""
import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / 'simulation_results' / 'server_inquiry_v3_validation_20261012.json'
DEFAULT_OUTPUT = ROOT / 'simulation' / 'viewer' / 'server_replay_data.json'
DEFAULT_POLICIES = ('fixed_3', 'observed_v2', 'v3_w45_h900')

POLICY_TEXT = {
    'fixed_1': ('고정 1곳', '한 번에 1곳 문의 · 60초 뒤 다음 1곳'),
    'fixed_3': ('고정 3곳', '한 번에 3곳 문의 · 60초 뒤 다음 3곳 (서버 기본 정책)'),
    'fixed_5': ('고정 5곳', '한 번에 5곳 문의 · 60초 뒤 다음 5곳'),
    'observed_v2': ('관측 V2', '첫 1곳 · 15초마다 재평가 · 120초 이후 유리한 대체 후보에 조건부 추가 문의'),
    'v3_w45_h900': ('관측 V3', '첫 1곳 · 15초마다 재평가 · 최근 문의 후 45초 대기 상한 · 과거 회신 15분만 사용 (선택된 V3)'),
}
FAMILY_TEXT = {
    'immediate_all_accept': '모든 병원이 곧바로 수용',
    'delayed_near_fast_far': '가까운 병원은 늦고 먼 병원은 빠름',
    'no_response_near': '가까운 병원 장시간 무응답',
    'simultaneous_w1': '여러 환자 동시 발생 · 병원 담당 1명',
    'staggered_w2': '환자 순차 발생 · 병원 담당 2명',
    'all_unavailable_control': '모든 병원 수용 불가 (대조군)',
    'historical_shift': '과거 회신 이력과 현재 상태가 다름',
    'history_helpful': '과거 회신 이력이 현재와 일치',
}
TRACE_TO_VIEWER = {
    'queue_added': 'ask',
    'review_started': 'review_started',
    'review_finished': 'review_completed',
    'reply_accept': 'accept',
    'reply_decline': 'decline',
    'reply_omitted': 'no_response',
    'reply_suppressed_after_cancel': 'reply_suppressed',
    'review_cancelled_before_start': 'review_cancelled',
    'destination_confirmed': 'commit',
}
REASONS_OBSERVED = {'initial_best', 'wait_for_hedge', 'pending_preferred', 'hedge_better_candidate', 'concurrency_cap',
                    'no_candidates', 'bounded_wait_expansion', 'fixed_wave'}


def minutes(seconds):
    return None if seconds is None else round(seconds / 60, 6)


def select_scenarios(report):
    """유형마다 가장 낮은 seed. 결과 지표를 보지 않는다."""
    by_family = {}
    for s in report['scenarios']:
        cur = by_family.get(s['family'])
        if cur is None or s['seed'] < cur['seed']:
            by_family[s['family']] = s
    return [by_family[f] for f in sorted(by_family)]


def convert_events(trace):
    events = []
    for sequence, e in enumerate(trace):
        kind = TRACE_TO_VIEWER.get(e['type'])
        if kind is None:
            raise ValueError(f"알 수 없는 서버 trace 사건: {e['type']}")
        events.append({**{k: v for k, v in e.items() if k not in ('at', 'type')},
                       't': minutes(e['at']), 'type': kind, 'server_type': e['type'], 'at_seconds': e['at'],
                       'patient': e.get('patient_id'), 'hospital': e.get('hospital_id'),
                       'server_sequence': sequence})
    return events


def decision_events(decisions, observed):
    out = []
    for d in decisions:
        if not observed:
            continue
        evidence = dict(d.get('evidence') or {})
        out.append(dict(t=minutes(d['at']), type='inquiry_policy', policy_kind='observed', patient=d.get('patient_id'),
                        reason=d.get('reason'), send=list(d.get('hospital_ids') or d.get('sent_hospitals') or []),
                        open_requests=evidence.get('patient_pending_count'), evidence=evidence,
                        evidence_recorded='evidence' in d,
                        at_seconds=d['at'], server_type='decision'))
    return out


def queue_snapshots(trace, hospitals):
    """검토 대기열의 시점별 상태. trace의 접수·시작·종료·취소만으로 재구성한다."""
    st = {h['id']: dict(hospital=h['id'], workers=h.get('workers', 1), waiting=0, processing=0, completed=0,
                        submitted=0, cancelled=0, queue_wait_total_minutes=0.0, service_total_minutes=0.0) for h in hospitals}
    queued_at, frames = {}, []
    peak = 0
    for e in sorted(trace, key=lambda x: x['at']):
        h = st.get(e.get('hospital_id'))
        if h is None:
            continue
        key = (e.get('hospital_id'), e.get('inquiry_id'))
        if e['type'] == 'queue_added':
            h['submitted'] += 1
            h['waiting'] += 1
            queued_at[key] = e['at']
        elif e['type'] == 'review_started':
            h['waiting'] -= 1
            h['processing'] += 1
            h['queue_wait_total_minutes'] += (e['at'] - queued_at.get(key, e['at'])) / 60
        elif e['type'] == 'review_finished':
            h['processing'] -= 1
            h['completed'] += 1
            h['service_total_minutes'] += e.get('service_seconds', 0) / 60
        elif e['type'] == 'review_cancelled_before_start':
            h['waiting'] -= 1
            h['cancelled'] += 1
            h['queue_wait_total_minutes'] += (e['at'] - queued_at.get(key, e['at'])) / 60
        else:
            continue
        snapshot = dict(t=minutes(e['at']), hospitals=[dict(v) for v in st.values()])
        if frames and frames[-1]['t'] == snapshot['t']:
            frames[-1] = snapshot
        else:
            frames.append(snapshot)
    peak = max((sum(h['waiting'] for h in f['hospitals']) for f in frames), default=0)
    return frames, peak


def convert_branch(branch, scenario):
    hospitals = {h['id']: h for h in scenario['hospitals']}
    patients = {p['id']: p for p in scenario['patients']}
    observed = branch['policy'].startswith(('observed', 'v3'))
    events = convert_events(branch['trace']) + decision_events(branch['decisions'], observed)
    trajectories, outcomes = [], []
    for res in branch['patients']:
        p = patients[res['id']]
        legs = []
        # The server stores estimated_arrival_seconds relative to this patient,
        # while selected_at and viewer events use the shared scenario clock.
        arrival_at = (res['created_at'] + res['estimated_arrival_seconds']
                      if res['estimated_arrival_seconds'] is not None else None)
        events.append(dict(t=minutes(res['created_at']), type='request', patient=res['id'],
                           at_seconds=res['created_at'], server_type='patient_info_received',
                           reason='서버에 환자 정보 등록 · 병원 탐색 시작. 119 출동·현장 도착 기록은 없음'))
        if not res['failed'] and res['selected_at'] is not None:
            hid = res['destination_hospital_id']
            h = hospitals[hid]
            legs.append(dict(kind='transport', depart_t=minutes(res['selected_at']), arrive_t=minutes(arrival_at),
                             origin=[p['lat'], p['lon']], destination=[h['lat'], h['lon']], hospital=hid,
                             geometry_kind='synthetic', provider='server_experiment_synthetic',
                             duration_seconds=res['travel_seconds'], ended_by='estimated_arrival'))
            events.append(dict(t=minutes(arrival_at), type='arrive', patient=res['id'], hospital=hid,
                               reason='서버 기록의 예상 도착시각(선정 + 합성 이동시간). 실제 도착 기록이 아님',
                               at_seconds=arrival_at, server_type='estimated_arrival',
                               elapsed_seconds=res['estimated_arrival_seconds']))
        if res['failed']:
            deadline = res['created_at'] + branch['policy_spec'].get('deadline_seconds', 300)
            events.append(dict(t=minutes(deadline), type='observation_end', patient=res['id'], state='UNASSIGNED',
                               reason=f"서버 정책 선정 기한({branch['policy_spec'].get('deadline_seconds', 300)}초) 안에 수용 확정 없음",
                               at_seconds=deadline, server_type='selection_deadline'))
        trajectories.append(dict(patient=res['id'], legs=legs))
        outcomes.append(dict(id=res['id'], state='UNASSIGNED' if res['failed'] else 'SELECTED',
                             destination_hospital_id=res['destination_hospital_id'], occur_t=minutes(res['created_at']),
                             search_start_t=minutes(res['created_at']), first_commit_t=minutes(res['selected_at']),
                             final_arrive_t=minutes(arrival_at), treatment_start_t=None,
                             inquiries=res['inquiry_count'], contacted_hospitals=res['contacted_hospitals'],
                             selection_seconds=res['selection_seconds'],
                             estimated_arrival_elapsed_seconds=res['estimated_arrival_seconds']))
    frames, peak = queue_snapshots(branch['trace'], scenario['hospitals'])
    m = branch['metrics']
    arrivals = [o['final_arrive_t'] - o['occur_t'] for o in outcomes if o['final_arrive_t'] is not None]
    jobs = [e for e in branch['trace'] if e['type'] == 'queue_added']
    total_wait = sum(h['queue_wait_total_minutes'] for h in frames[-1]['hospitals']) if frames else 0
    metrics = dict(m, occur_to_final_arrival_mean=sum(arrivals) / len(arrivals) if arrivals else None,
                   inquiry_queue_wait_mean=total_wait / len(jobs) if jobs else None, inquiry_peak_waiting=peak,
                   inquiry_service_minutes=m.get('review_work_seconds', 0) / 60, time_unit='minutes_from_seconds')
    short, description = POLICY_TEXT.get(branch['policy'], (branch['policy'], branch['policy_spec'].get('label', '')))
    if branch['policy'].startswith(('v3_', 'observed_v3')):
        config = branch['policy_spec']['config']
        short = '관측 V3'
        description = (f"첫 1곳 · {branch['policy_spec']['review_seconds']}초마다 재평가 · "
                       f"최근 문의 후 {config['max_wait_seconds']}초 추가 문의 대기 상한 · "
                       f"과거 회신 {config['history_window_seconds'] / 60:g}분 사용")
    policy = dict(server_policy=dict(name=branch['policy'], short=short, description=description,
                                     spec=branch['policy_spec']), rule='서버', k=branch['policy_spec'].get('batch_size'))
    # The harness processes completions and confirmations before the workflow,
    # then appends new inquiries and dispatches queue work. Keep original trace
    # ordering within each phase, rather than showing a decision before its reply.
    def event_phase(event):
        kind = event['server_type']
        if kind == 'patient_info_received':
            return 0
        if kind in ('review_finished', 'reply_accept', 'reply_decline', 'reply_omitted',
                    'reply_suppressed_after_cancel', 'destination_confirmed'):
            return 10
        if kind == 'selection_deadline':
            return 15
        if kind == 'decision':
            return 20
        return 30
    events.sort(key=lambda e: (e['at_seconds'], event_phase(e)))
    return dict(id=branch['policy'], label=short, policy=policy, events=events, trajectories=trajectories,
                metrics=metrics, patient_outcomes=outcomes, resource_snapshots=[], inquiry_snapshots=frames,
                server_snapshot_sha256=branch.get('snapshot_sha256'))


def compare_text(branches_by_policy, selected_policy='v3_w45_h900'):
    """V3 − 고정3. 양쪽에서 선정된 같은 환자의 선정 시간과 실패 수."""
    base, v3 = branches_by_policy.get('fixed_3'), branches_by_policy.get(selected_policy)
    if not base or not v3:
        return None, ''
    b = {p['id']: p for p in base['patients']}
    pairs = [(b[p['id']]['selection_seconds'], p['selection_seconds']) for p in v3['patients']
             if p['selection_seconds'] is not None and b.get(p['id'], {}).get('selection_seconds') is not None]
    fb, fv = sum(p['failed'] for p in base['patients']), sum(p['failed'] for p in v3['patients'])
    diff = sum(v - a for a, v in pairs) / len(pairs) if pairs else None
    if fv < fb or (fv == fb and diff is not None and diff < 0):
        verdict = 'V3가 고정3보다 빠르거나 실패가 적은 사례'
    elif fv > fb or (diff is not None and diff > 0):
        verdict = 'V3가 고정3보다 느리거나 실패가 많은 사례'
    else:
        verdict = 'V3와 고정3이 같은 사례'
    text = (f"{verdict}: 공통 선정 {len(pairs)}명의 선정 시간 차이 {diff:+.1f}초, " if diff is not None else f"{verdict}: ") + \
        f"선정 실패 고정3 {fb}명 · V3 {fv}명. 검증 seed 중 이 유형의 첫 seed를 고정 규칙으로 골랐으며 대표 효과가 아니다."
    return diff, text


def convert(report, policies=None, *, source_path=DEFAULT_INPUT):
    selected_policy = report['selected_policy']['name']
    policies = tuple(policies) if policies is not None else ('fixed_3', 'observed_v2', selected_policy)
    if not policies or len(set(policies)) != len(policies):
        raise ValueError('재생할 정책은 중복 없이 하나 이상 필요합니다.')
    source_path = Path(source_path).resolve()
    try:
        source_file = source_path.relative_to(ROOT).as_posix()
    except ValueError:
        source_file = source_path.as_posix()
    results = {r['scenario']['id']: r for r in report['results']}
    out = []
    for sc in select_scenarios(report):
        r = results[sc['id']]
        by_policy = {b['policy']: b for b in r['branches']}
        missing = [p for p in policies if p not in by_policy]
        if missing:
            raise ValueError(f"{sc['id']}에 정책 기록이 없습니다: {missing}")
        branches = [convert_branch(by_policy[p], sc) for p in policies]
        _, summary = compare_text(by_policy, selected_policy)
        family = FAMILY_TEXT.get(sc['family'], sc['family'])
        scenario = dict(
            id=f"server-{sc['id']}", label=f"[서버 문의 정책] {family} · seed {sc['seed']}",
            family=sc['family'], seed=sc['seed'], synthetic=True, server_inquiry_only=True,
            description=f"{sc['description']} {summary}", comparison=summary,
            hospitals=[dict(id=h['id'], name=h['name'], lat=h['lat'], lon=h['lon'], capabilities=h.get('capabilities', []),
                            workers=h.get('workers', 1), capacity={}) for h in sc['hospitals']],
            patients=[dict(id=p['id'], label='합성 환자', location=[p['lat'], p['lon']], occur_t=minutes(p['at']),
                           dispatch_t=None, ambulance_arrive_t=None, search_start_t=minutes(p['at']),
                           required_capabilities=p.get('required_capabilities', []), priority=p.get('priority'))
                      for p in sc['patients']],
            inquiry_queue=dict(enabled=True),
            assumptions=[
                f'서버 문의 정책 기록({source_file})을 재계산 없이 변환했습니다. 시각은 초 단위 기록을 분으로 나눠 표시합니다.',
                '병원·환자·회신 조건·이동시간은 모두 서버 실험의 합성값입니다. 병원 위치는 실제 병원이 아니며 경로는 직선 추정입니다.',
                '서버 실험은 문의·회신·선정까지만 다룹니다. 도착은 예상 도착시각이며 치료 시작·완료와 전원은 기록하지 않았습니다.',
                '회신 없음은 수용 불가와 다릅니다. 다른 병원이 확정된 뒤 끝난 검토의 회신은 배정에 반영되지 않습니다.',
            ],
            source=dict(file=source_file, scenario_id=sc['id'], report_kind=report.get('kind'),
                        selected_policy=selected_policy,
                        selection_rule='scenario family별 최저 검증 seed (결과 지표 미사용)'))
        duration = max((e['t'] for b in branches for e in b['events']), default=1)
        out.append(dict(schema_version=2, scenario=scenario, branches=branches, duration_minutes=duration,
                        routing_summary=dict(mode='server_synthetic', provider='server_experiment_synthetic')))
    return dict(schema_version=2, source_kind='server_inquiry_validation', scenarios=out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--input', type=Path, default=DEFAULT_INPUT)
    ap.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    ap.add_argument('--policies', nargs='+', default=None)
    a = ap.parse_args(argv)
    raw = a.input.read_bytes()
    # Reject the same file or an output that would replace a source experiment.
    if a.input.resolve() == a.output.resolve() or a.output.resolve().parent == DEFAULT_INPUT.parent:
        ap.error('실험 보고서 디렉터리 또는 입력 파일에는 재생 결과를 저장할 수 없습니다.')
    payload = convert(json.loads(raw), a.policies, source_path=a.input)
    payload['source_sha256'] = hashlib.sha256(raw).hexdigest()
    a.output.parent.mkdir(parents=True, exist_ok=True)
    tmp = a.output.with_suffix('.tmp')
    tmp.write_text(json.dumps(payload, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    tmp.replace(a.output)
    print(f"서버 재생 데이터 저장: {a.output} · 사례 {len(payload['scenarios'])}개 · 분기 "
          f"{sum(len(s['branches']) for s in payload['scenarios'])}개")
    for s in payload['scenarios']:
        print(' -', s['scenario']['label'], '|', s['scenario']['comparison'].split(' 검증 seed')[0])


if __name__ == '__main__':
    main()
