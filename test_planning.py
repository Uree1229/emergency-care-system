"""배정 연결 회귀: 동시 변경, 응답 만료, 기존 목적지 유지, 프로세스 제한시간."""
import json
import threading
import time
import unittest
from unittest.mock import patch
import urllib.request
from http.server import ThreadingHTTPServer

from server import db, planning, service, solver_adapter, worker
from server.app import make_handler
from solver_improved import conflict_case, exhaustive, sample, score, solve, validate
from test_server import Base, EST, T0, base_patient


class PlanningFlow(Base):
    def run_plan(self, now=T0 + 20, runner=worker.bounded_solve):
        return worker.run_once(self.path, EST, clock=lambda: now, seconds=0, runner=runner)

    def test_profile_persisted_and_delivered_to_hospital(self):
        self.patient(name='시연 환자', birth='2019-01-02', address='가상 주소', guardian='보호자',
                     guardian_phone='010-0000-0000', consciousness='unknown', breathing='absent')
        self.assertTrue(service.patient_view(self.conn, 'A', T0)['call_119'])
        self.accepted('A', 'H1')
        hospital = service.hospital_inquiries(self.conn, T0 + 20, 'H1')
        p = hospital['recent'][0]['patient']
        self.assertEqual((p['name'], p['breathing'], p['consciousness']), ('시연 환자', 'absent', 'unknown'))

    def test_profile_validation(self):
        for payload in ({'lat': float('nan')}, {'lon': 181}, {'lat': True}, {'birth': '2026-02-30'},
                        {'breathing': []}, {'treatment_duration_seconds': 0}, {'priority_source': {}},
                        {'priority': True, 'priority_source': 'expert'}, {'reporter_role': {}}):
            self.assertEqual(self.code(service.create_patient, self.conn, T0, 'X', base_patient(**payload)), 'INVALID')

    def test_old_db_migration_preserves_patient_and_is_idempotent(self):
        old = db.connect(':memory:')
        try:
            old.executescript(db.SCHEMA)
            old.execute("INSERT INTO patients(id,reporter_role,consent,requested_at,state,symptoms) "
                        "VALUES('OLD','self',1,?,'WAITING','고열')", (T0,))
            db.init(old)
            db.init(old)
            self.assertEqual(service.patient_view(old, 'OLD', T0)['symptoms'], '고열')
            self.assertIsNone(service.patient_view(old, 'OLD', T0)['consciousness'])
        finally:
            old.close()

    def test_only_accepted_hospital_can_be_recommended_no_reservation(self):
        self.patient()
        self.assertTrue(self.run_plan())
        r = planning.patient_result(self.conn, T0 + 20, 'A')['result']
        self.assertEqual(r['status'], 'UNASSIGNED')
        self.assertIsNone(r['hospital_id'])
        iid = self.accepted('A', 'H1')
        self.assertTrue(self.run_plan())
        r = planning.patient_result(self.conn, T0 + 20, 'A')['result']
        self.assertEqual((r['hospital_id'], r['inquiry_id'], r['confirmable_now']), ('H1', iid, True))
        self.assertEqual(service.hospital_view(self.conn, 'H1', T0 + 20)['resources'][0]['expected'], 0)
        self.assertEqual(service.patient_view(self.conn, 'A', T0 + 20)['state'], 'WAITING')

    def test_shared_bed_scheduled_without_overbooking_final_confirmation(self):
        for pid in ('A', 'B'):
            self.patient(pid=pid)
            self.accepted(pid, 'H1')
        self.run_plan()
        a = planning.patient_result(self.conn, T0 + 20, 'A')['result']
        b = planning.patient_result(self.conn, T0 + 20, 'B')['result']
        self.assertGreaterEqual(abs(a['planned_start_at'] - b['planned_start_at']), 1800)
        service.confirm_destination(self.conn, T0 + 21, 'A', a['inquiry_id'])
        self.assertEqual(self.code(service.confirm_destination, self.conn, T0 + 21, 'B', b['inquiry_id']), 'RESOURCE_CONFLICT')
        self.assertEqual(planning.patient_result(self.conn, T0 + 21, 'B')['status'], 'STALE')

    def test_input_mutated_while_solver_runs_discards_plan(self):
        self.patient()
        self.accepted('A', 'H1')
        def mutating(data, seconds):
            other = db.connect(self.path)
            try:
                service.set_resource(other, T0 + 20, 'H1', 'ed_bed', 1, 1)
            finally:
                other.close()
            return solve(data, seconds=0)
        self.assertFalse(self.run_plan(runner=mutating))
        self.assertIsNone(planning.patient_result(self.conn, T0 + 20, 'A')['result'])
        self.assertTrue(self.run_plan())
        self.assertEqual(planning.patient_result(self.conn, T0 + 20, 'A')['result']['status'], 'UNASSIGNED')

    def test_expired_acceptance_is_not_recommended(self):
        self.patient()
        self.accepted('A', 'H1')
        self.run_plan()
        self.assertEqual(planning.patient_result(self.conn, T0 + 700, 'A')['status'], 'STALE')
        self.run_plan(T0 + 700)
        self.assertEqual(planning.patient_result(self.conn, T0 + 700, 'A')['result']['status'], 'UNASSIGNED')

    def test_transit_eta_is_not_added_twice_and_destination_is_held(self):
        self.patient(max_wait_seconds=60)
        iid = self.accepted('A', 'H1')
        service.confirm_destination(self.conn, T0 + 16, 'A', iid)
        p = service.depart(self.conn, T0 + 17, 'A', EST)
        self.run_plan()
        r = planning.patient_result(self.conn, T0 + 20, 'A')['result']
        self.assertEqual(r['hospital_id'], 'H1')
        self.assertTrue(r['destination_locked'])
        self.assertEqual(r['planned_start_at'], solver_adapter.minute(p['eta_at']) * 60)
        self.assertIn('WAIT_LIMIT_EXCEEDED', r['warnings'])
        self.assertEqual(r['status'], 'NEEDS_REVIEW')
        self.assertEqual(service.patient_view(self.conn, 'A', T0 + 20)['destination_hospital_id'], 'H1')

    def test_external_arrival_triggers_review_without_reroute(self):
        self.patient()
        iid = self.accepted('A', 'H1')
        service.confirm_destination(self.conn, T0 + 16, 'A', iid)
        service.depart(self.conn, T0 + 17, 'A', EST)
        self.run_plan()
        service.register_external_arrival(self.conn, T0 + 21, 'H1', {'ed_bed': 1}, note='가상 환자 B')
        self.assertEqual(planning.patient_result(self.conn, T0 + 21, 'A')['status'], 'STALE')
        self.run_plan(T0 + 22)
        r = planning.patient_result(self.conn, T0 + 22, 'A')['result']
        self.assertEqual((r['status'], r['hospital_id']), ('NEEDS_REVIEW', 'H1'))
        self.assertEqual(service.patient_view(self.conn, 'A', T0 + 22)['state'], 'IN_TRANSIT')

    def test_destination_restriction_requires_review_and_arrival_ends_planning(self):
        self.patient()
        iid = self.accepted('A', 'H1')
        service.confirm_destination(self.conn, T0 + 16, 'A', iid)
        service.add_restriction(self.conn, T0 + 17, 'H1', 'all', '가상 제한')
        self.run_plan()
        r = planning.patient_result(self.conn, T0 + 20, 'A')['result']
        self.assertEqual((r['status'], r['hospital_id']), ('NEEDS_REVIEW', 'H1'))
        self.assertIn('DESTINATION_RESTRICTED', r['warnings'])
        service.confirm_arrival(self.conn, T0 + 21, 'A', 'H1')
        self.assertEqual(planning.patient_result(self.conn, T0 + 21, 'A')['status'], 'NOT_APPLICABLE')
        self.assertEqual(self.code(planning.request, self.conn, T0 + 21, 'A'), 'INVALID_STATE')

    def test_adjustment_lists_only_new_accepted_alternatives(self):
        self.patient()
        iid = self.accepted('A', 'H1')
        service.confirm_destination(self.conn, T0 + 16, 'A', iid)
        service.depart(self.conn, T0 + 17, 'A', EST)
        service.hospital_decision(self.conn, T0 + 18, 'A', 'H1', 'adjust', '자원 확인 필요')
        alternative = self.accepted('A', 'H2', now=T0 + 19)
        self.run_plan(T0 + 26)
        r = planning.patient_result(self.conn, T0 + 26, 'A')['result']
        self.assertEqual(r['hospital_id'], 'H1')
        self.assertEqual(r['accepted_alternatives'][0]['inquiry_id'], alternative)
        self.assertTrue(r['accepted_alternatives'][0]['current_location_required'])
        service.change_destination(self.conn, T0 + 27, 'A', alternative, EST, lat=35.18, lon=126.85)
        self.assertEqual(planning.patient_result(self.conn, T0 + 27, 'A')['status'], 'STALE')
        self.run_plan(T0 + 28)
        self.assertEqual(planning.patient_result(self.conn, T0 + 28, 'A')['result']['hospital_id'], 'H2')

    def test_revision_and_job_request_roll_back_with_failed_input(self):
        before = self.conn.execute('SELECT revision FROM planning_state').fetchone()[0]
        with self.assertRaises(RuntimeError):
            with db.transaction(self.conn):
                self.conn.execute("UPDATE resources SET occupied=1 WHERE hospital_id='H1'")
                raise RuntimeError('rollback')
        self.assertEqual(self.conn.execute('SELECT revision FROM planning_state').fetchone()[0], before)
        self.assertEqual(service.hospital_view(self.conn, 'H1', T0)['resources'][0]['occupied'], 0)

    def test_other_worker_lease_and_idle_poll_do_not_repeat_work(self):
        self.patient()
        self.accepted('A', 'H1')
        self.conn.execute('UPDATE planning_state SET lease_until=? WHERE id=1', (T0 + 100,))
        self.assertFalse(self.run_plan())
        self.conn.execute('UPDATE planning_state SET lease_until=0 WHERE id=1')
        self.assertTrue(self.run_plan())
        self.assertFalse(self.run_plan())

    def test_no_hospital_data_yields_explicit_review(self):
        empty = db.connect(':memory:')
        try:
            db.init(empty)
            service.create_patient(empty, T0, 'E', base_patient())
            snapshot = planning.capture(empty, T0)
            model, context = solver_adapter.build(snapshot, EST)
            r = solver_adapter.present(snapshot, model, context, None, {'status': 'NEEDS_REVIEW'})
            self.assertEqual(r['E']['status'], 'NEEDS_REVIEW')
        finally:
            empty.close()

    def test_unknown_resource_and_unsupported_demand_are_explicit(self):
        self.patient(resource_demands={'icu': 1})
        self.accepted('A', 'H1')
        self.run_plan()
        r = planning.patient_result(self.conn, T0 + 20, 'A')['result']
        self.assertIn('UNSUPPORTED_RESOURCE', r['warnings'])
        self.assertEqual(r['status'], 'NEEDS_REVIEW')
        self.patient(pid='B')
        self.accepted('B', 'H2')
        service.set_resource(self.conn, T0 + 20, 'H2', 'ed_bed', 2, 0, 'unknown')
        self.run_plan()
        self.assertEqual(planning.patient_result(self.conn, T0 + 20, 'B')['result']['status'], 'UNASSIGNED')

    def test_error_retries_and_request_survives_new_connection(self):
        self.patient()
        self.accepted('A', 'H1')
        def fail(data, seconds):
            raise RuntimeError('private detail')
        with self.assertRaises(RuntimeError):
            self.run_plan(runner=fail)
        self.assertEqual(planning.patient_result(self.conn, T0 + 20, 'A')['status'], 'ERROR')
        self.assertTrue(self.run_plan(T0 + 26))
        self.assertEqual(planning.patient_result(self.conn, T0 + 26, 'A')['status'], 'READY')

    def test_http_create_same_second_and_recommendation_routes(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.path, EST, clock=lambda: T0 + 20))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def request(method, path, payload=None):
            req = urllib.request.Request(f'http://127.0.0.1:{server.server_port}{path}',
                                         data=json.dumps(payload).encode() if payload is not None else None,
                                         method=method, headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(req) as resp:
                return json.load(resp)
        try:
            a, b = [request('POST', '/api/patients', base_patient()) for _ in range(2)]
            self.assertNotEqual(a['id'], b['id'])
            self.assertEqual(request('GET', f"/api/patients/{a['id']}/recommendation")['status'], 'QUEUED')
            self.run_plan()
            self.assertEqual(request('GET', f"/api/patients/{a['id']}/recommendation")['status'], 'READY')
            self.assertEqual(request('POST', f"/api/patients/{a['id']}/replan", {})['status'], 'STALE')
        finally:
            server.shutdown()
            thread.join()
            server.server_close()

    def test_travel_runs_without_write_lock_and_state_race_is_rejected(self):
        self.patient()
        iid = self.accepted('A', 'H1')
        service.confirm_destination(self.conn, T0 + 16, 'A', iid)
        path = self.path
        class ChangingEstimator:
            def estimate(self, origin, destination):
                other = db.connect(path)
                try:
                    service.cancel_patient(other, T0 + 18, 'A', '119 이송')
                finally:
                    other.close()
                return EST.estimate(origin, destination)
        self.assertEqual(self.code(service.depart, self.conn, T0 + 17, 'A', ChangingEstimator()), 'CONFLICT')
        self.assertEqual(service.patient_view(self.conn, 'A', T0 + 19)['state'], 'CANCELLED')


class SolverExtensions(unittest.TestCase):
    def test_solver_recovers_when_greedy_locked_order_has_no_plan(self):
        a, b = sample(4, 'A', 3), sample(1, 'B', 1)
        a.update(deadline=0, arrivals=[0])
        b.update(deadline=0, arrivals=[1])
        data = dict(version=1, now=0, horizon=2, capacities=[(1, 0)], patients=[a, b], fixed={},
                    destination_locks={'A': 0, 'B': 0}, blocks=[])
        from solver_improved import greedy
        self.assertIsNone(greedy(data))
        plan, meta = solve(data)
        self.assertTrue(meta['lex_optimal'])
        self.assertEqual(plan, {'A': dict(h=0, start=2), 'B': dict(h=0, start=1)})

    def test_destination_lock_can_delay_and_matches_exhaustive(self):
        p = sample(4, 'A', 1)
        p.update(deadline=0, travel=[0, 0], eligible=[0, 1])
        data = dict(version=1, now=0, horizon=3, capacities=[(1, 0), (1, 0)], patients=[p], fixed={},
                    destination_locks={'A': 0}, blocks=[dict(h=0, start=0, duration=2, special=False)])
        plan, _ = solve(data)
        self.assertEqual(plan['A'], {'h': 0, 'start': 2})
        self.assertEqual(score(data, plan, 'severity_first'), score(data, exhaustive(data, 'severity_first'), 'severity_first'))
        self.assertFalse(validate(data, {'A': None}))
        self.assertFalse(validate(data, {'A': dict(h=1, start=0)}))

    def test_team_only_block_and_multiple_bed_demand(self):
        p = sample(4, 'A', 1)
        p.update(special=True, demands=[2, 1])
        data = dict(version=1, now=0, horizon=2, capacities=[(2, 1)], patients=[p], fixed={},
                    blocks=[dict(h=0, start=0, duration=1, special=True, demands=[0, 1])])
        plan, _ = solve(data)
        self.assertEqual(plan['A']['start'], 1)
        self.assertEqual(score(data, plan, 'severity_first'), score(data, exhaustive(data, 'severity_first'), 'severity_first'))

    def test_bounded_process_returns_valid_plan_and_tiny_budget_falls_back(self):
        data = conflict_case()
        started = time.monotonic()
        plan, meta = worker.bounded_solve(data, seconds=0.001)
        self.assertLess(time.monotonic() - started, 3)
        self.assertTrue(meta['fallback'])
        self.assertTrue(validate(data, plan))
        plan, meta = worker.bounded_solve(data, seconds=3)
        self.assertTrue(validate(data, plan))
        self.assertTrue(meta['lex_optimal'])

    def test_process_launch_failure_uses_valid_fallback(self):
        data = conflict_case()
        with patch('multiprocessing.process.BaseProcess.start', side_effect=OSError('process unavailable')):
            plan, meta = worker.bounded_solve(data)
        self.assertTrue(validate(data, plan))
        self.assertEqual(meta['status'], 'SOLVER_ERROR_FALLBACK')


if __name__ == '__main__':
    unittest.main()
