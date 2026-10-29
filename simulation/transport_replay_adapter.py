"""실제 서버 처리 기록과 별도 합성 위치 관측을 지도 재생으로 변환한다.

python -m server.transport_replay_experiment
python -m simulation.transport_replay_adapter
"""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / 'simulation_results' / 'server_transport_replay.json'
DEFAULT_OUTPUT = ROOT / 'simulation' / 'viewer' / 'transport_replay_data.json'

EVENT_TYPES = {
    'patient_created': 'request', 'inquiry_sent': 'ask', 'inquiry_retried': 'ask',
    'destination_confirmed': 'commit', 'departed': 'depart',
    'destination_changed': 'redirect', 'arrival_confirmed': 'arrive',
    'transport_recheck_required': 'recheck',
    'transport_conditions_restored': 'conditions_restored',
    'transport_recheck_acknowledged': 'acceptance_kept',
    'transport_alternative_requested': 'alternative_requested',
    'transport_inquiry_ready': 'alternative_ready',
    'transport_inquiry_exhausted': 'alternative_exhausted',
    'resource_updated': 'resource_update', 'external_arrival': 'external_arrival',
    'inquiry_closed': 'inquiry_closed', 'inquiry_cancelled': 'inquiry_closed',
}


def minute(seconds):
    return None if seconds is None else seconds / 60


def convert_event(event):
    """원본 사건을 하나도 생략하지 않는다. 알 수 없는 사건도 원래 이름을 남긴다."""
    kind = EVENT_TYPES.get(event['type'], event['type'])
    data = event.get('payload') or {}
    if event['type'] == 'inquiry_responded':
        kind = {'accept': 'accept', 'decline': 'decline', 'checking': 'review_started'}.get(data.get('action'), 'inquiry_responded')
    return dict(t=minute(event['at_seconds']), at_seconds=event['at_seconds'],
                type=kind, server_type=event['type'], server_event_id=event['event_id'],
                server_sequence=event['sequence'], patient=event.get('patient_id'),
                hospital=event.get('hospital_id'), actor=event.get('actor'),
                inquiry_id=data.get('inquiry_id'), reason=data.get('note') or data.get('reason'),
                evidence=data.get('issues'), payload=data, raw_event=event.get('raw_event'))


def transport_legs(scenario, pid):
    """관측점 사이만 보간한다. ETA로 도착이나 구병원까지의 이동을 만들지 않는다."""
    observations = sorted((m for m in scenario['movement'] if m['patient_id'] == pid),
                          key=lambda m: m['at_seconds'])
    actions = [e for e in scenario['trace'] if e.get('patient_id') == pid]
    legs = []
    for start, end in zip(observations, observations[1:]):
        if end['at_seconds'] <= start['at_seconds']:
            if (end['lat'], end['lon']) != (start['lat'], start['lon']):
                raise ValueError(f'{pid}: 같은 시각의 위치가 충돌합니다.')
            continue
        prior = [e for e in actions if e['at_seconds'] <= start['at_seconds']]
        if not any(e['type'] == 'departed' for e in prior):
            continue
        if any(e['type'] == 'arrival_confirmed' for e in prior):
            continue
        # A movement interval must end at every human change. Otherwise its
        # attribution/route would silently interpolate across a different target.
        if any(e['type'] == 'destination_changed' and start['at_seconds'] < e['at_seconds'] < end['at_seconds'] for e in actions):
            raise ValueError(f'{pid}: 목적지 변경 시각의 위치 관측이 없습니다.')
        destinations = [e for e in prior if e['type'] in ('destination_confirmed', 'destination_changed')]
        hid = destinations[-1].get('hospital_id') if destinations else None
        ended = [e for e in actions if e['at_seconds'] == end['at_seconds'] and
                 e['type'] in ('destination_changed', 'arrival_confirmed')]
        legs.append(dict(kind='transport', depart_t=minute(start['at_seconds']),
                         arrive_t=minute(end['at_seconds']),
                         origin=[start['lat'], start['lon']], destination=[end['lat'], end['lon']],
                         hospital=hid, geometry_kind='synthetic', provider='synthetic_position_observations',
                         duration_seconds=end['at_seconds'] - start['at_seconds'],
                         ended_by=ended[-1]['type'] if ended else 'position_observed',
                         observation_source='scripted_synthetic_input'))
    return legs


def convert_scenario(sc, report, source_file):
    trace = sc['trace']
    if any((a['at_seconds'], a['sequence']) > (b['at_seconds'], b['sequence']) for a, b in zip(trace, trace[1:])):
        raise ValueError(f"{sc['id']}: 원본 서버 사건 순서가 역전됐습니다.")
    events = [convert_event(e) for e in trace]
    end = sc['observation_end_seconds']
    frames = [dict(t=minute(s['at_seconds']), **s) for s in sc['snapshots']]
    final = {p['id']: p for p in sc['final_patients']}
    outcomes = []
    for p in sc['patients']:
        pid = p['id']; saved = final[pid]
        own = [e for e in trace if e.get('patient_id') == pid]
        commit = next((e['at_seconds'] for e in own if e['type'] == 'destination_confirmed'), None)
        created = next(e['at_seconds'] for e in own if e['type'] == 'patient_created')
        arrival = next((e['at_seconds'] for e in own if e['type'] == 'arrival_confirmed'), None)
        outcomes.append(dict(id=pid, state=saved['state'], destination_hospital_id=saved['destination_hospital_id'],
                             occur_t=minute(created), search_start_t=minute(created), first_commit_t=minute(commit),
                             final_arrive_t=minute(arrival), treatment_start_t=None,
                             needs_adjustment=bool(saved.get('needs_adjustment')),
                             transport_review=saved.get('transport_review'),
                             transport_reassignment=saved.get('transport_reassignment')))
        events.append(dict(t=minute(end), at_seconds=end, type='observation_end', patient=pid,
                           hospital=saved['destination_hospital_id'], state=saved['state'],
                           reason='시연 관찰 종료. 도착이 기록되지 않은 환자의 도착·치료를 추정하지 않습니다.',
                           source='replay_observation_boundary'))
    events.sort(key=lambda e: (e['at_seconds'], e.get('server_sequence', float('inf'))))
    scenario = dict(id=f"transport-{sc['id']}", label=f"[서버 조건 변경] {sc['label']}",
                    description=sc['description'], family=sc.get('family'), synthetic=True,
                    server_transport_replay=True,
                    hospitals=sc['hospitals'],
                    patients=[dict(**p, location=[p['lat'], p['lon']],
                                   occur_t=next(o['occur_t'] for o in outcomes if o['id'] == p['id']),
                                   search_start_t=next(o['search_start_t'] for o in outcomes if o['id'] == p['id']),
                                   dispatch_t=None, ambulance_arrive_t=None) for p in sc['patients']],
                    inquiry_queue=dict(enabled=False),
                    assumptions=report.get('limitations', []),
                    source=dict(file=source_file, scenario_id=sc['id'], report_kind=report.get('kind'),
                                selection_rule='사전 정의한 기능 시연 4종. 정책 성능 비교 또는 임상 효과 검증이 아님'))
    metrics = dict(recorded_event_count=len(trace),
                   destination_changes=sum(e['type'] == 'destination_changed' for e in trace),
                   recheck_patients=len({e.get('patient_id') for e in trace if e['type'] == 'transport_recheck_required'}),
                   unresolved_patients=sum(p['state'] != 'ARRIVED' for p in outcomes))
    branch = dict(id='server_flow', label='서버 처리 기록',
                  policy=dict(transport_record=True, description='병원 재확인 · 대체 수용 확인 · 사람의 목적지 변경'),
                  events=events, trajectories=[dict(patient=p['id'], legs=transport_legs(sc, p['id'])) for p in sc['patients']],
                  patient_outcomes=outcomes, metrics=metrics, transport_snapshots=frames,
                  resource_snapshots=[], inquiry_snapshots=[])
    return dict(schema_version=2, scenario=scenario, branches=[branch], duration_minutes=minute(end),
                routing_summary=dict(mode='server_transport_synthetic', provider='synthetic_position_observations'))


def convert(report, *, source_path=DEFAULT_INPUT):
    source_path = Path(source_path).resolve()
    try:
        source_file = source_path.relative_to(ROOT).as_posix()
    except ValueError:
        source_file = source_path.as_posix()
    return dict(schema_version=2, source_kind='server_transport_replay',
                scenarios=[convert_scenario(sc, report, source_file) for sc in report['scenarios']])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--input', type=Path, default=DEFAULT_INPUT)
    ap.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    args = ap.parse_args(argv)
    if args.input.resolve() == args.output.resolve() or args.output.resolve().parent == DEFAULT_INPUT.parent:
        ap.error('원본 실험 결과에는 재생 자료를 덮어쓸 수 없습니다.')
    raw = args.input.read_bytes()
    payload = convert(json.loads(raw), source_path=args.input)
    payload['source_sha256'] = hashlib.sha256(raw).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.output.with_suffix('.tmp')
    tmp.write_text(json.dumps(payload, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    tmp.replace(args.output)
    print(f"서버 조건 변경 재생: {args.output} · {len(payload['scenarios'])}사례")


if __name__ == '__main__':
    main()
