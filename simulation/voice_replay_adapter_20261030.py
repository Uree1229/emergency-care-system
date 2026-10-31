"""Read-only voice rehearsal DBs -> trace viewer. No solver or network calls.

python -m simulation.voice_replay_adapter_20261030 --input RUN_DIRECTORY --output NEW_JSON
The horizontal axis is record order, never elapsed treatment or travel time.
Original epochs, database IDs and same-second ordering limitations are retained.
"""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3

from .transport_replay_adapter import EVENT_TYPES

ROOT = Path(__file__).resolve().parents[1]
LABELS = {
    'normal_arrival': '정상 수용·사람의 확정·도착',
    'waiting_treatment_change': '음성 치료조건 변경·재수용·새 배정',
    'enroute_resource_keep': '이송 중 자원 변경·병원의 수용 유지',
    'enroute_resource_reroute': '이송 중 자원 변경·새 수용·사람의 목적지 변경',
    'optional_unavailable': '선택 항목 미측정·배정 가능',
    'no_hospital_response': '병원 무응답·미배정 유지',
    'no_available_capacity': '가용 자원 없음·미배정 유지',
    'stale_treatment_approval': '오래된 치료조건 승인 차단',
    'missing_r1_rejected': '필수 R1 누락·환자 생성 차단',
}
ASSUMPTIONS = [
    '실제 서버 HTTP·워커를 격리 DB에서 실행한 기능 기록이다. 음성 원음·STT·실제 병원 연락·임상 효과 검증이 아니다.',
    '가로축 #번호는 기록 탐색 순서다. 원본 epoch와 경과초를 사건 상세에 보존하며 이를 업무·이동시간 개선율로 사용하지 않는다.',
    'DB 사건의 ID 순서는 보존한다. 같은 초에 저장된 음성·솔버·DB의 서로 다른 기록 간 정확한 선후는 독립적으로 증명되지 않는다.',
    '원본에 없는 이동·도착·치료를 ETA로 생성하지 않는다. 도로 자료가 별도로 없는 사례에는 차량 궤적이 없다.',
    '수용 가능 응답과 서버 추천은 목적지 확정이 아니다. 목적지는 명시적 확정·변경 성공 사건 이후에만 바뀐다.',
]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _rows(conn, table):
    return [dict(row) for row in conn.execute('SELECT * FROM ' + table)]


def _patient(row):
    value = deepcopy(row)
    for key in ('extra', 'required_capabilities', 'resource_demands', 'danger_signs'):
        if isinstance(value.get(key), str):
            value[key] = json.loads(value[key])
    return value


def _initial_patients(conn, final):
    initial = {}
    for row in conn.execute('SELECT response FROM voice_requests ORDER BY recorded_at, rowid'):
        response = json.loads(row[0])
        patient = response.get('patient')
        if patient and patient.get('id') not in initial:
            initial[patient['id']] = _patient(patient)
    for row in final:
        if row['id'] not in initial:
            # No historical clinical input is invented from the final DB state.
            initial[row['id']] = dict(id=row['id'], lat=row['lat'], lon=row['lon'],
                required_capabilities=[], resource_demands={}, historical_input_unavailable=True)
    return initial


def _merge_records(events, voices, solves):
    """Preserve DB order; place extra streams at their recorded second only.

    Independent streams at equal epochs are grouped deterministically; this is
    explicitly a display ordering, not an invented cross-stream audit sequence.
    """
    records = []
    for row in events:
        payload = json.loads(row['data'])
        kind = EVENT_TYPES.get(row['type'], row['type'])
        if row['type'] == 'inquiry_responded':
            kind = {'accept': 'accept', 'decline': 'decline', 'checking': 'review_started'}.get(payload.get('action'), row['type'])
        records.append(dict(source_at=row['at'], stream='database_events', source_order=row['id'],
            type=kind, server_type=row['type'], server_event_id=row['id'], patient=row['patient_id'],
            hospital=row['hospital_id'], actor=row['actor'], inquiry_id=payload.get('inquiry_id'),
            reason=payload.get('note') or payload.get('reason'), payload=payload, raw_event=deepcopy(row)))
    for index, row in enumerate(voices):
        records.append(dict(source_at=row['recorded_at'], stream='voice_utterances', source_order=index,
            type='voice_transcript', patient=row.get('patient_id'), hospital=None,
            session_id=row['session_id'], reason=row['transcript'], payload=deepcopy(row)))
    for index, row in enumerate(solves):
        records.append(dict(source_at=row['case_time'], stream='solver_records', source_order=index,
            type='allocation_recommendation', patient=None, hospital=None,
            reason='서버 추천 계산. 사람의 확정 전 목적지 미변경.', payload=deepcopy(row)))
    stream_rank = {'voice_utterances': 0, 'solver_records': 1, 'database_events': 2}
    records.sort(key=lambda row: (row['source_at'], stream_rank[row['stream']], row['source_order']))
    return records


def _reduce_state(initial, records):
    """Only derive fields whose transitions are explicitly in stored events."""
    states = {}
    frames = []
    for event in records:
        pid, kind = event.get('patient'), event['type']
        if kind == 'request' and pid in initial:
            states[pid] = dict(id=pid, state='WAITING', destination_hospital_id=None,
                transport_review={'state': 'DISABLED'}, transport_reassignment={'state': 'IDLE'},
                snapshot_source='database_event_reconstruction')
        current = states.get(pid)
        if current:
            if kind == 'commit':
                current.update(state='CONFIRMED', destination_hospital_id=event['hospital'])
            elif kind == 'depart':
                current['state'] = 'IN_TRANSIT'
            elif kind == 'redirect':
                current['destination_hospital_id'] = event['hospital']
                current['transport_review'] = {'state': 'MONITORING'}
            elif kind == 'arrive':
                current['state'] = 'ARRIVED'
            review = {'recheck': 'RECHECK_REQUIRED', 'transport_monitor_started': 'MONITORING',
                'acceptance_kept': 'MONITORING', 'conditions_restored': 'MONITORING',
                'alternative_requested': 'SEARCHING', 'alternative_ready': 'AWAITING_CHANGE',
                'alternative_exhausted': 'EXHAUSTED', 'transport_monitor_completed': 'COMPLETED'}
            if kind in review:
                current['transport_review'] = {'state': review[kind],
                    'evidence': {'issues': event.get('payload', {}).get('issues', [])}}
            job = {'alternative_requested': 'RUNNING', 'alternative_ready': 'AWAITING_CHANGE',
                'alternative_exhausted': 'EXHAUSTED', 'redirect': 'STOPPED', 'arrive': 'STOPPED'}
            if kind in job:
                current['transport_reassignment'] = {'state': job[kind]}
        frames.append(dict(t=event['t'], source_at=event['source_at'], patients=deepcopy(list(states.values()))))
    return frames


def convert_case(run_dir, case, report):
    if Path(case['name']).name != case['name'] or case['name'] in ('.', '..'):
        raise ValueError('잘못된 시나리오 경로입니다.')
    directory = run_dir / case['name']
    path = directory / 'fixture.db'
    before = digest(path)
    conn = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    try:
        raw_events = _rows(conn, 'events')
        final = [_patient(row) for row in _rows(conn, 'patients')]
        hospitals = _rows(conn, 'hospitals')
        voices = [dict(row) for row in conn.execute('SELECT u.*,s.patient_id FROM voice_utterances u JOIN voice_sessions s ON s.id=u.session_id ORDER BY u.recorded_at,u.rowid')]
        initial = _initial_patients(conn, final)
    finally:
        conn.close()
    if digest(path) != before:
        raise ValueError('원본 DB가 변환 중 변경됐습니다: ' + str(path))
    side_path = directory / 'replay_observations.json'
    side = json.loads(side_path.read_text(encoding='utf-8')) if side_path.is_file() else {}
    records = _merge_records(raw_events, voices, case.get('solver_runs', []))
    for index, frame in enumerate(side.get('snapshots', [])):
        records.append(dict(source_at=frame['at'], stream='captured_snapshots', source_order=index,
            type='state_observed', patient=None, hospital=None,
            reason='해당 API 응답 직후 서버 상태 관측'))
    records.sort(key=lambda row: (row['source_at'], {'voice_utterances': 0, 'solver_records': 1,
        'database_events': 2, 'captured_snapshots': 3}[row['stream']], row['source_order']))
    base = min((row['source_at'] for row in records), default=0)
    for index, row in enumerate(records, 1):
        row.update(t=index, source_elapsed_seconds=row['source_at'] - base)
    end = len(records) + 1

    def axis(at, *, first=False):
        exact = [row['t'] for row in records if row['source_at'] == at]
        if exact:
            return min(exact) if first else max(exact)
        prior = [row['t'] for row in records if row['source_at'] < at]
        return max(prior, default=0)

    frames = _reduce_state(initial, records)
    # Real captured patient_view snapshots supersede reconstructed frames at the
    # same source instant. They remain source-tagged and never leak backwards.
    for frame in side.get('snapshots', []):
        frames.append(dict(frame, t=axis(frame['at']), source_at=frame['at'],
            snapshot_source='captured_patient_view'))
    frames.sort(key=lambda row: row['t'])
    trajectories = deepcopy(side.get('trajectories', []))
    for trajectory in trajectories:
        for leg in trajectory['legs']:
            depart, arrive = leg['source_depart_at'], leg['source_arrive_at']
            if arrive <= depart:
                raise ValueError('궤적 시각이 역전되거나 같습니다.')
            leg['depart_t'], leg['arrive_t'] = axis(depart), axis(arrive)
            if leg['arrive_t'] <= leg['depart_t']:
                raise ValueError('궤적 경계에 대응하는 원본 사건이 필요합니다.')
            # Preserve explicit off-road connector geometry in the movement
            # path; the fetched road-only geometry stays independently intact.
            geometry = deepcopy(leg.get('geometry', []))
            if geometry:
                leg['motion_geometry'] = [leg['origin'], *geometry, leg['destination']]
    patients = []
    outcomes = []
    for pid, p in initial.items():
        own = [row for row in records if row.get('patient') == pid]
        first = next((row['t'] for row in own if row['type'] == 'request'), 0)
        patients.append(dict(id=pid, label='합성 음성 입력 환자', location=[p.get('lat'), p.get('lon')],
            lat=p.get('lat'), lon=p.get('lon'), required_capabilities=p.get('required_capabilities', []),
            resource_demands=p.get('resource_demands', {}), occur_t=first, search_start_t=first,
            dispatch_t=None, ambulance_arrive_t=None))
        saved = next(row for row in final if row['id'] == pid)
        outcomes.append(dict(id=pid, state=saved['state'], destination_hospital_id=saved['destination_hospital_id'],
            first_commit_t=next((row['t'] for row in own if row['type'] == 'commit'), None),
            final_arrive_t=next((row['t'] for row in own if row['type'] == 'arrive'), None),
            treatment_start_t=None, needs_adjustment=bool(saved['needs_adjustment'])))
    records.append(dict(t=end, type='observation_end', patient=None, hospital=None,
        source_at=max((row['source_at'] for row in records), default=base),
        source_elapsed_seconds=max((row['source_at']-base for row in records), default=0),
        reason='관찰 종료. ' + ('기능 검사 통과.' if case['pass'] else '검사 실패 원문 보존: ' + case.get('error', ''))))
    last_states = {p['id']: p for p in frames[-1]['patients']} if frames else {}
    frames.append(dict(t=end, patients=[dict(last_states.get(p['id'], {}), **p) for p in outcomes],
        snapshot_source='final_database_state'))
    for hospital in hospitals:
        hospital['capabilities'] = json.loads(hospital['capabilities'])
    road = side.get('routing_summary', {}).get('provider') == 'kakao' or bool(trajectories and any(
        leg.get('geometry_kind') == 'road' for tr in trajectories for leg in tr['legs']))
    prefix = 'voice-road-' if road else 'voice-'
    label = LABELS.get(case['name'], case['name'])
    scenario = dict(id=prefix+case['name'], label='[음성·도로]' + label if road else '[음성 연결] '+label,
        description=('PASS' if case['pass'] else 'FAIL') + ' · 실제 서버 처리 기록 · 합성 환자·병원·역할 입력',
        synthetic=True, voice_server_replay=True, voice_road_replay=road, server_transport_replay=True,
        timeline_mode='event_order', validation_dataset=False,
        hospitals=hospitals, patients=patients, inquiry_queue={'enabled': False},
        assumptions=ASSUMPTIONS + side.get('assumptions', []),
        source=dict(kind='voice_rehearsal_database', run=str(run_dir.relative_to(ROOT)) if run_dir.is_relative_to(ROOT) else str(run_dir),
            case=case['name'], database_sha256=before, report_sha256=digest(run_dir/'report.json'),
            observations_sha256=digest(side_path) if side_path.is_file() else None,
            run_pass=report['pass'], case_pass=case['pass'], source_stable=report.get('sources_stable'),
            records_order='DB IDs retained; same-second independent streams have display ordering only'))
    branch = dict(id='voice_server_flow', label='음성→문의→배정→조건 변경 처리',
        policy={'transport_record': True, 'description': 'fixed_3 문의·3초 CP-SAT·명시적 사람 확정'},
        events=records, trajectories=trajectories, patient_outcomes=outcomes, transport_snapshots=frames,
        resource_snapshots=[], inquiry_snapshots=[], solver_records=case.get('solver_runs', []),
        verification=dict(passed=case['pass'], steps=case.get('steps', []), requests=case.get('requests', []), error=case.get('error')))
    return dict(schema_version=2, scenario=scenario, branches=[branch], duration_minutes=end,
        routing_summary=side.get('routing_summary', {'mode': 'unrecorded_movement', 'provider': 'none'}))


def convert_run(run_dir):
    run_dir = Path(run_dir).resolve()
    report = json.loads((run_dir/'report.json').read_text(encoding='utf-8'))
    names = [case['name'] for case in report['cases']]
    if names != report['planned_cases'] or len(set(names)) != len(names):
        raise ValueError('계획된 모든 시나리오를 순서대로 보존해야 합니다.')
    return dict(schema_version=2, source_kind='voice_server_replay', validation_dataset=False,
        source_sha256=digest(run_dir/'report.json'), run_pass=report['pass'],
        scenarios=[convert_case(run_dir, case, report) for case in report['cases']])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    output = args.output.resolve()
    if output.is_relative_to(args.input.resolve()) or output.exists():
        parser.error('원본 폴더 밖의 새 JSON 경로를 지정하세요.')
    value = convert_run(args.input)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as handle:
        json.dump(value, handle, ensure_ascii=False, allow_nan=False)
    print(json.dumps({'scenarios': len(value['scenarios']), 'run_pass': value['run_pass']}))


if __name__ == '__main__':
    main()
