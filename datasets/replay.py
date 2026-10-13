"""가상 서버 시나리오를 검증하고 새 SQLite DB로 재생한다."""
import argparse
import json
from pathlib import Path
import time

from server import db, planning, service, solver_adapter
from server.travel import TravelEstimator

from .io import find


def replay(record, conn):
    db.init(conn)
    if conn.execute('SELECT COUNT(*) FROM patients').fetchone()[0] or conn.execute('SELECT COUNT(*) FROM hospitals').fetchone()[0]:
        raise ValueError('replay requires an empty database')
    epoch = record['epoch']
    estimator = TravelEstimator(use_kakao=False)
    for h in record['hospitals']:
        service.upsert_hospital(conn, dict(h, source='synthetic'))
        service.set_resource(conn, epoch, h['id'], 'ed_bed', h['capacity'], h['occupied'], source='synthetic')
        service.set_resource(conn, epoch, h['id'], 'special_team', 1, 0, source='synthetic')
    def inquiry(s):
        q = conn.execute('SELECT id FROM inquiries WHERE patient_id=? AND hospital_id=?',
                         (s['patient_id'], s['hospital_id'])).fetchone()
        if q is None:
            raise AssertionError('missing inquiry in replay')
        return q[0]
    outputs = []
    now = epoch
    for s in record['steps']:
        now = epoch + s['at']
        op, pid, hid = s['operation'], s.get('patient_id'), s.get('hospital_id')
        try:
            if op == 'create':
                out = service.create_patient(conn, now, pid, record['patients'][pid])
            elif op == 'inquire':
                out = service.send_inquiries(conn, now, pid, s['hospital_ids'])
            elif op == 'respond':
                out = service.respond(conn, now, inquiry(s), s['action'], '가상 담당자',
                                      reason=s.get('reason'), question=s.get('question'))
            elif op == 'answer':
                out = service.answer_question(conn, now, pid, inquiry(s), s['answer'])
            elif op == 'cancel':
                out = service.cancel_patient(conn, now, pid, '기타')
            elif op == 'no_show':
                out = service.mark_no_show(conn, now, pid, hid, '가상 미도착')
            elif op == 'confirm':
                out = service.confirm_destination(conn, now, pid, inquiry(s))
            elif op == 'depart':
                out = service.depart(conn, now, pid, estimator)
            elif op == 'arrive':
                out = service.confirm_arrival(conn, now, pid, hid)
            elif op == 'discharge':
                out = service.discharge(conn, now, pid, hid)
            elif op == 'retry':
                out = service.retry_inquiry(conn, now, pid, inquiry(s))
            elif op == 'view':
                out = service.patient_view(conn, pid, now)
            elif op == 'external_arrival':
                out = service.register_external_arrival(conn, now, hid, s['resource_demands'], note='가상 미등록 환자')
            elif op == 'decision':
                out = service.hospital_decision(conn, now, pid, hid, s['decision'], '가상 판단')
            elif op == 'change':
                out = service.change_destination(conn, now, pid, inquiry(s), estimator, s['lat'], s['lon'])
            elif op == 'restrict':
                out = service.add_restriction(conn, now, hid, s['scope'], s['reason'],
                                              epoch + s['ends_after'] if s['ends_after'] is not None else None)
            elif op == 'resource':
                out = service.set_resource(conn, now, hid, 'ed_bed', s['capacity'], s['occupied'], s['status'], source='synthetic')
            else:
                raise ValueError(f'unknown replay operation: {op}')
            if s.get('expected_error'):
                raise AssertionError(f"expected {s['expected_error']} at {s['at']}")
            for key, expected in s.get('expected_output', {}).items():
                if out.get(key) != expected:
                    raise AssertionError(f'{op}: {key} != {expected!r}')
            outputs.append(dict(at=s['at'], operation=op, outcome='OK'))
        except service.ServiceError as error:
            if error.code != s.get('expected_error'):
                raise
            outputs.append(dict(at=s['at'], operation=op, outcome=error.code))
    for check in record['checks']:
        kind, pid, hid = check['kind'], check.get('patient_id'), check.get('hospital_id')
        if kind in ('patient_state', 'destination', 'patient_field'):
            p = service.patient_view(conn, pid, now)
            key = 'state' if kind == 'patient_state' else ('destination_hospital_id' if kind == 'destination' else check['key'])
            actual = p[key]
        elif kind in ('inquiry_state', 'attempts', 'inquiry_field'):
            q = service._inquiry(conn, inquiry(check))
            actual = q['state'] if kind == 'inquiry_state' else q['attempts'] if kind == 'attempts' else q[check['key']]
        elif kind == 'expected_demand':
            actual = service.expected_demand(conn, hid).get('ed_bed', 0)
        elif kind == 'patient_count':
            actual = conn.execute('SELECT COUNT(*) FROM patients').fetchone()[0]
        elif kind in ('occupied', 'resource_flag'):
            resource = next(r for r in service.resource_view(conn, hid, now) if r['resource_type'] == 'ed_bed')
            actual = resource['occupied'] if kind == 'occupied' else resource[check['key']]
        elif kind == 'restriction_status':
            actual = service.restrictions(conn, hid, now)[0]['status']
        else:
            raise ValueError(f'unknown replay check: {kind}')
        if actual != check['value']:
            raise AssertionError(f"{record['id']}: {check} != {actual!r}")
    with db.transaction(conn):
        snapshot = planning.capture(conn, now)
    model, context = solver_adapter.build(snapshot, estimator)
    return dict(id=record['id'], checks_passed=len(record['checks']), steps=outputs, now=now,
                planner_patient_count=len(model['patients']), planner_hospital_count=len(model['capacities']))


def main():
    ap = argparse.ArgumentParser(description='가상 시나리오를 새 서버 DB로 재생')
    ap.add_argument('--input', default='datasets/generated/server_cases.jsonl.gz')
    ap.add_argument('--case', default='API-walkin_redirect-0000')
    ap.add_argument('--db', required=True, help='새 DB 경로. 기존 DB를 덮어쓰지 않음')
    args = ap.parse_args()
    target = Path(args.db)
    if target.exists():
        ap.error('기존 DB입니다. 새로운 경로를 지정하세요.')
    record = find(args.input, args.case)
    record['epoch'] = int(time.time()) - max(s['at'] for s in record['steps'])
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = db.connect(str(target))
    try:
        print(json.dumps(replay(record, conn), ensure_ascii=False, indent=2))
    finally:
        conn.close()


if __name__ == '__main__':
    main()
