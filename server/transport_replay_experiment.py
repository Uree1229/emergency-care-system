"""실제 서버 수용 조건 검토·목적지 변경을 합성 입력으로 기록한다.

python -m server.transport_replay_experiment --output simulation_results/server_transport_replay.json

매 실행은 자체 임시 SQLite를 사용한다. HTTP, 외부 API, 운영 DB를 사용하지
않으며 서비스 함수를 호출해 생성된 사건만 trace로 내보낸다. 위치와 도착
확인은 실험자가 미리 정한 합성 관측이다. 임상 결과나 비교 효과가 아니다.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile

from . import db, service, transport_review, transport_reassignment

EPOCH = 1_800_000_000
FAMILIES = ('unaffected', 'keep', 'change', 'no_alternative')
LIMITATIONS = [
    '실제 서버 업무 코드의 결정적 시연 기록이며 현행 119 체계 복제나 임상 효과 검증이 아니다.',
    '병원, 환자, 자원, 병원 판단, 회신, 위치, 이동시간과 도착 확인 시점은 모두 합성 입력이다.',
    '자원 변화는 재확인 사유이며 실제 치료 불가 판정이나 목적지 자동 변경이 아니다.',
    '대체병원 수용 응답 이후에도 원래 목적지를 유지하며, 별도의 합성 사람 판단 입력으로만 변경한다.',
    '관측 종료까지 미해결인 경우 도착, 치료, 전원 성공을 만들어 내지 않는다.',
    '좌표 사이 보간은 시각화용이며 도로 경로·실제 구급차 위치나 이송 시간의 증거가 아니다.',
    '비교군을 둔 성능 실험이 아니므로 시간 절감·의료 효과·재이송 감소율을 계산하지 않는다.',
]
HOSPITALS = [
    dict(id='H1', name='가상 병원 1', lat=35.17, lon=126.85,
         capabilities=['응급의학', '소아청소년과'], capacity={'ed_bed': 2, 'special_team': 1}),
    dict(id='H2', name='가상 병원 2', lat=35.20, lon=126.85,
         capabilities=['응급의학', '소아청소년과'], capacity={'ed_bed': 3, 'special_team': 1}),
    dict(id='H3', name='가상 병원 3', lat=35.23, lon=126.85,
         capabilities=['응급의학', '소아청소년과'], capacity={'ed_bed': 3, 'special_team': 1}),
]
LABELS = {
    'unaffected': ('환자별 영향 구분', '같은 병원으로 가는 A·B 중 특정 치료팀이 필요한 A만 재확인한다. B는 기존 이송을 유지한다.'),
    'keep': ('병원의 수용 유지 판단', '치료팀 상태 변화 후 병원이 기존 환자의 치료 준비를 유지할 수 있다고 판단한다. 목적지는 바뀌지 않는다.'),
    'change': ('수용 확인 뒤 사람의 목적지 변경', '병원 조정 요청 → 대체 문의 → 새 수용 응답 → 사람의 목적지 변경 순서를 실제 서버 사건으로 재생한다.'),
    'no_alternative': ('대체 수용을 확보하지 못함', '대체 후보 모두 수용 불가로 응답한다. 문의 기한이 끝나도 목적지·이송 기록은 유지하며 도착 성공을 가정하지 않는다.'),
}


class SyntheticEstimator:
    """병원별 고정 합성 이동시간. 네트워크와 미래 회신 정보를 사용하지 않는다."""
    def estimate(self, origin, destination):
        seconds = {35.17: 300, 35.20: 420, 35.23: 600}[destination[0]]
        return dict(duration_seconds=seconds, distance_meters=0,
                    provider='synthetic_transport_replay',
                    scenario='합성 고정 이동시간; 실제 도로 경로 아님')


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode('utf-8')).hexdigest()


class Recorder:
    def __init__(self, path, family):
        self.path, self.family = str(path), family
        self.conn = db.connect(self.path)
        db.init(self.conn)
        self.estimator = SyntheticEstimator()
        self.snapshots, self.movement = [], []
        self.last_at = -1
        self.input_patients = []

    def close(self):
        self.conn.close()

    def action(self, at, name, function=lambda: None, positions=()):
        if type(at) is not int or at < self.last_at:
            raise ValueError('actions must use chronological integer seconds')
        self.last_at = at
        function()
        positions = [dict(p) for p in positions]
        for position in positions:
            self.movement.append(dict(at_seconds=at, source='synthetic_observation',
                                      action=name, **position))
        patients = []
        for row in self.conn.execute('SELECT id FROM patients ORDER BY id'):
            patient = service._patient(self.conn, row['id'])
            patient['transport_review'] = transport_review.view(self.conn, EPOCH + at, row['id'])
            patient['transport_reassignment'] = transport_reassignment.view(self.conn, EPOCH + at, row['id'])
            patients.append(patient)
        hospitals = []
        for hospital in HOSPITALS:
            hospitals.append(dict(id=hospital['id'], resources=db.rows(self.conn.execute(
                'SELECT * FROM resources WHERE hospital_id=? ORDER BY resource_type', (hospital['id'],)))))
        self.snapshots.append(dict(at_seconds=at, action=name,
                                   synthetic_inputs=dict(positions=positions),
                                   patients=patients, hospitals=hospitals))

    def setup(self):
        def seed():
            for hospital in HOSPITALS:
                service.upsert_hospital(self.conn, {k: hospital[k] for k in
                    ('id', 'name', 'lat', 'lon', 'capabilities')} | {'source': 'synthetic'})
                for resource, capacity in hospital['capacity'].items():
                    service.set_resource(self.conn, EPOCH, hospital['id'], resource,
                                         capacity, 0, source='synthetic')
        self.action(0, 'synthetic_hospitals_and_resources', seed)
        pids = ['A', 'B'] if self.family == 'unaffected' else ['A']
        for pid in pids:
            demands = {'ed_bed': 1, **({'special_team': 1} if pid == 'A' else {})}
            patient = dict(id=pid, lat=35.16, lon=126.85,
                           required_capabilities=['소아청소년과'], resource_demands=demands)
            self.input_patients.append(deepcopy(patient))
            self.action(1, f'create_patient_{pid}', lambda p=patient: service.create_patient(
                self.conn, EPOCH + 1, p['id'], dict(
                    reporter_role='transporter', consent=True, age=None,
                    symptoms='합성 시연 환자; 진단·실제 개인정보 없음',
                    lat=p['lat'], lon=p['lon'], required_capabilities=p['required_capabilities'],
                    resource_demands=p['resource_demands'])), [dict(patient_id=pid, lat=35.16, lon=126.85)])
        for pid in pids:
            self.action(5, f'send_initial_{pid}', lambda pid=pid: service.send_inquiries(
                self.conn, EPOCH + 5, pid, ['H1'], actor='synthetic_transporter'))
        for pid in pids:
            self.action(10, f'accept_initial_{pid}', lambda pid=pid: service.respond(
                self.conn, EPOCH + 10, self.iid(pid, 'H1'), 'accept', '합성 병원 담당자'))
        for pid in pids:
            self.action(20, f'confirm_destination_{pid}', lambda pid=pid: service.confirm_destination(
                self.conn, EPOCH + 20, pid, self.iid(pid, 'H1'), actor='synthetic_transporter'))
        for pid in pids:
            self.action(21, f'depart_{pid}', lambda pid=pid: service.depart(
                self.conn, EPOCH + 21, pid, self.estimator), [dict(patient_id=pid, lat=35.16, lon=126.85)])
        for pid in pids:
            self.action(22, f'enable_monitor_{pid}', lambda pid=pid: transport_review.enable(
                self.conn, EPOCH + 22, pid, True))

    def iid(self, pid, hid):
        return self.conn.execute('SELECT id FROM inquiries WHERE patient_id=? AND hospital_id=?',
                                 (pid, hid)).fetchone()['id']

    def position(self, at, pid, lat, lon=126.85):
        self.action(at, f'position_{pid}', lambda: service.update_transport_position(
            self.conn, EPOCH + at, pid, lat, lon, actor='synthetic_gps'),
            [dict(patient_id=pid, lat=lat, lon=lon)])

    def loss(self):
        self.position(55, 'A', 35.162)
        if self.family == 'unaffected':
            self.position(55, 'B', 35.162)
        self.action(60, 'synthetic_team_unavailable', lambda: service.set_resource(
            self.conn, EPOCH + 60, 'H1', 'special_team', 1, 1, source='synthetic'))
        def scan():
            with db.transaction(self.conn):
                transport_review.scan_in_transaction(self.conn, EPOCH + 61, 'H1')
        self.action(61, 'server_review_scan', scan)

    def decide(self, at, decision):
        def decide():
            current = transport_review.view(self.conn, EPOCH + at, 'A')
            service.hospital_decision(self.conn, EPOCH + at, 'A', 'H1', decision,
                note='합성 병원 판단: 기존 치료 조건 유지' if decision == 'keep' else '합성 병원 판단: 치료팀 확보 어려움',
                actor='synthetic_hospital', expected_review_version=current['version'],
                expected_risk_signature=current['risk_signature'])
        self.action(at, f'human_hospital_{decision}', decide)

    def worker(self, at):
        self.action(at, 'server_alternative_worker', lambda: transport_reassignment.run_once(
            self.path, self.estimator, clock=lambda: EPOCH + at))

    def arrive(self, at, pid, hid):
        hospital = next(h for h in HOSPITALS if h['id'] == hid)
        self.position(at, pid, hospital['lat'], hospital['lon'])
        self.action(at, f'synthetic_hospital_arrival_{pid}', lambda: service.confirm_arrival(
            self.conn, EPOCH + at, pid, hid, actor='synthetic_hospital'))

    def run(self):
        self.setup()
        self.loss()
        if self.family == 'unaffected':
            self.position(150, 'B', 35.165)
            self.arrive(321, 'B', 'H1')
            self.position(400, 'A', 35.162)
        elif self.family == 'keep':
            self.decide(65, 'keep')
            self.action(70, 'synthetic_team_available_again', lambda: service.set_resource(
                self.conn, EPOCH + 70, 'H1', 'special_team', 1, 0, source='synthetic'))
            self.position(150, 'A', 35.165)
            self.arrive(321, 'A', 'H1')
        else:
            self.decide(65, 'adjust')
            self.worker(66)
            if self.family == 'change':
                self.action(80, 'synthetic_alternative_accept', lambda: service.respond(
                    self.conn, EPOCH + 80, self.iid('A', 'H2'), 'accept', '합성 대체병원 담당자'))
                self.action(82, 'synthetic_other_alternative_decline', lambda: service.respond(
                    self.conn, EPOCH + 82, self.iid('A', 'H3'), 'decline', '합성 대체병원 담당자', reason='인력'))
                self.worker(83)
                self.position(95, 'A', 35.163)
                self.action(95, 'human_transporter_changes_destination', lambda: service.change_destination(
                    self.conn, EPOCH + 95, 'A', self.iid('A', 'H2'), self.estimator,
                    lat=35.163, lon=126.85, actor='synthetic_transporter'),
                    [dict(patient_id='A', lat=35.163, lon=126.85)])
                self.position(200, 'A', 35.174)
                self.position(350, 'A', 35.187)
                self.arrive(515, 'A', 'H2')
            else:
                for at, hid in ((80, 'H2'), (82, 'H3')):
                    self.action(at, f'synthetic_alternative_decline_{hid}', lambda at=at, hid=hid: service.respond(
                        self.conn, EPOCH + at, self.iid('A', hid), 'decline', '합성 대체병원 담당자', reason='인력'))
                self.worker(83)
                for at in (150, 250, 350, 366, 400):
                    self.position(at, 'A', 35.162)
                    self.worker(at)
        self.action(self.last_at, 'observation_end')
        raw_events = service.events_since(self.conn, limit=10000)
        trace = []
        for sequence, event in enumerate(raw_events):
            entity_type = 'patient' if event['patient_id'] else ('hospital' if event['hospital_id'] else 'system')
            trace.append(dict(sequence=sequence, event_id=event['id'], at_seconds=event['at'] - EPOCH,
                at=event['at'], type=event['type'], actor=event['actor'], entity_type=entity_type,
                entity_id=event['patient_id'] or event['hospital_id'], patient_id=event['patient_id'],
                hospital_id=event['hospital_id'], payload=event['data'], raw_event=event))
        label, description = LABELS[self.family]
        final_patients = self.snapshots[-1]['patients']
        metrics = dict(patient_count=len(final_patients),
            destination_change_count=sum(e['type'] == 'destination_changed' for e in trace),
            arrival_count=sum(e['type'] == 'arrival_confirmed' for e in trace),
            unresolved_count=sum(p['state'] in service.ACTIVE_TRANSPORT for p in final_patients),
            metric_scope='기록된 업무 사건 수; 임상·성능 개선 지표 아님')
        result = dict(id=f'server-transport-{self.family}', family=self.family, label=label,
            description=description, synthetic=True, hospitals=deepcopy(HOSPITALS),
            patients=self.input_patients, trace=trace, snapshots=self.snapshots,
            movement=self.movement, observation_end_seconds=self.last_at,
            final_patients=final_patients, metrics=metrics)
        result['content_sha256'] = digest(result)
        return result


def run_scenario(family):
    if family not in FAMILIES:
        raise ValueError(f'unknown transport replay family: {family}')
    with tempfile.TemporaryDirectory(prefix='server-transport-replay-') as temporary:
        recorder = Recorder(Path(temporary) / 'replay.db', family)
        try:
            return recorder.run()
        finally:
            recorder.close()


def run():
    report = dict(schema_version=1, kind='server_transport_replay', synthetic=True,
                  epoch=EPOCH, provenance='server service functions + isolated temporary SQLite',
                  limitations=LIMITATIONS, scenarios=[run_scenario(family) for family in FAMILIES])
    report['content_sha256'] = digest(report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('simulation_results/server_transport_replay.json'))
    args = parser.parse_args()
    report = run()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(dict(output=str(args.output), scenarios=len(report['scenarios']),
                         synthetic=True, content_sha256=report['content_sha256']), ensure_ascii=False))


if __name__ == '__main__':
    main()
