"""서버 자동 문의: 실제 수용 확인은 사람에게 남기고 제한된 후보 탐색만 진행한다.

실행: python -m unittest test_inquiry_workflow -v
모든 환자·병원·시각은 임시 DB의 가상 값이다. 외부 API를 호출하지 않는다.
"""
import json
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager
from http.server import ThreadingHTTPServer

from server import db, inquiry_workflow as workflow, public_data, service
from server.app import make_handler
from server.db import transaction
from test_server import Base, EST, T0, base_patient


class WorkflowBase(Base):
    def add_hospitals(self, count=12):
        for index in range(count):
            hid = f'W{index + 1:02d}'
            service.upsert_hospital(self.conn, dict(id=hid, name=hid, lat=35.21 + index * .01, lon=126.85,
                                                    capabilities=['응급의학', '소아청소년과'], source='synthetic'))
            service.set_resource(self.conn, T0, hid, 'ed_bed', 5, 0, source='hospital_input')

    def run_worker(self, now=T0, estimator=EST):
        return workflow.run_once(self.path, estimator, clock=lambda: now)

    def job(self, pid='A', now=T0):
        return workflow.view(self.conn, now, pid)

    def inquiries(self, pid='A'):
        return [dict(r) for r in self.conn.execute('SELECT * FROM inquiries WHERE patient_id=? ORDER BY id', (pid,))]

    def decline_all(self, pid='A', now=T0 + 1):
        for q in self.inquiries(pid):
            if q['state'] in ('PENDING', 'CHECKING'):
                service.respond(self.conn, now, q['id'], 'decline', 'test-nurse', reason='병상')

    @contextmanager
    def http(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.path, EST, clock=lambda: T0 + 10))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'

        def call(method, endpoint, payload=None):
            request = urllib.request.Request(base + endpoint, method=method,
                                             data=None if payload is None else json.dumps(payload).encode('utf-8'),
                                             headers={'Content-Type': 'application/json'})
            try:
                with urllib.request.urlopen(request, timeout=10) as response:
                    return response.status, json.load(response)
            except urllib.error.HTTPError as error:
                with error:
                    return error.code, json.load(error)
        try:
            yield call
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


class WorkflowStartAndPause(WorkflowBase):
    def test_opt_in_only_preserves_manual_patient_behavior(self):
        manual = self.patient('A')
        automatic = self.patient('B', auto_inquiry=True)
        self.assertEqual(manual['inquiry_workflow']['state'], 'NOT_STARTED')
        self.assertEqual(self.job('A')['state'], 'NOT_STARTED')
        self.assertFalse(self.job('A')['enabled'])
        self.assertEqual(automatic['inquiry_workflow']['state'], 'QUEUED')
        self.assertTrue(automatic['inquiry_workflow']['enabled'])
        self.assertEqual(self.inquiries('B'), [])
        self.run_worker()
        self.assertEqual(self.inquiries('A'), [])
        self.assertGreater(len(self.inquiries('B')), 0)
        self.assertEqual(service.patient_view(self.conn, 'B', T0)['state'], 'WAITING')

    def test_reading_patient_and_workflow_does_not_send_queries(self):
        self.patient(auto_inquiry=True)
        before = [tuple(r) for r in self.conn.execute('SELECT * FROM inquiry_workflows')]
        events = self.conn.execute('SELECT COUNT(*) FROM events').fetchone()[0]
        for _ in range(3):
            self.assertEqual(self.job()['state'], 'QUEUED')
            self.assertEqual(service.patient_view(self.conn, 'A', T0)['state'], 'WAITING')
        self.assertEqual(self.inquiries(), [])
        self.assertEqual([tuple(r) for r in self.conn.execute('SELECT * FROM inquiry_workflows')], before)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM events').fetchone()[0], events)

    def test_pause_persists_across_reconnection_and_resume_does_not_resend(self):
        self.add_hospitals()
        self.patient(auto_inquiry=True)
        self.run_worker()
        first = self.inquiries()
        self.assertEqual(len(first), 3)
        workflow.start(self.conn, T0 + 1, 'A', enabled=False)
        self.assertFalse(self.job()['enabled'])
        self.assertEqual(self.job()['state'], 'PAUSED')
        conn = db.connect(self.path)
        try:
            db.init(conn)
            self.assertEqual(workflow.view(conn, T0 + 80, 'A')['state'], 'PAUSED')
            self.run_worker(T0 + 80)
            self.assertEqual(self.inquiries(), first)
            workflow.start(conn, T0 + 80, 'A', enabled=True)
        finally:
            conn.close()
        self.run_worker(T0 + 80)
        self.assertGreater(len(self.inquiries()), len(first))
        self.assertTrue(all(q['attempts'] == 1 for q in self.inquiries()))
        first_ids = [q['id'] for q in first]
        self.assertEqual([q['id'] for q in self.inquiries()[:3]], first_ids)


class WorkflowCandidatePolicy(WorkflowBase):
    def test_first_wave_uses_nearby_eligible_candidates_and_filters_known_inability(self):
        self.add_hospitals()
        service.add_restriction(self.conn, T0, 'H1', 'all', '인력 부족', ends_at=T0 + 1000)
        service.set_resource(self.conn, T0, 'W01', 'ed_bed', 1, 1, source='hospital_input')
        self.patient(auto_inquiry=True, required_capabilities=['소아청소년과'])
        self.run_worker()
        self.assertEqual([q['hospital_id'] for q in self.inquiries()], ['H2', 'W02', 'W03'])
        self.assertEqual({q['state'] for q in self.inquiries()}, {'PENDING'})
        self.assertIsNone(service.patient_view(self.conn, 'A', T0)['destination_hospital_id'])

    def test_unknown_or_stale_capacity_is_confirmed_by_inquiry_instead_of_hard_excluded(self):
        self.patient(auto_inquiry=True)
        service.set_resource(self.conn, T0, 'H1', 'ed_bed', 1, 1, source='hospital_input')
        service.set_resource(self.conn, T0, 'H2', 'ed_bed', 0, 0, status='unknown', source='hospital_input')
        self.conn.execute("UPDATE resources SET updated_at=? WHERE hospital_id='H1'", (T0 - service.STALE_AFTER - 1,))
        self.run_worker()
        contacted = {q['hospital_id'] for q in self.inquiries()}
        self.assertIn('H1', contacted)
        self.assertIn('H2', contacted)

    def test_public_reported_zero_is_not_treated_as_verified_operational_rejection(self):
        self.patient(auto_inquiry=True)
        self.conn.execute("DELETE FROM resources WHERE hospital_id='H1'")
        normalized = public_data.normalize_beds({'hvec': '0'})
        self.conn.execute('INSERT INTO public_api_snapshots VALUES(?,?,?,?,?,?)',
                          ('H1', 'nmc_rltm_beds', T0, T0, db.dumps(normalized), db.dumps({'hvec': '0'})))
        self.run_worker()
        self.assertIn('H1', {q['hospital_id'] for q in self.inquiries()})

    def test_all_declines_expand_immediately_while_silence_waits_sixty_seconds(self):
        self.add_hospitals()
        self.patient(auto_inquiry=True)
        self.run_worker()
        first = self.inquiries()
        self.run_worker(T0 + 59)
        self.assertEqual(self.inquiries(), first)
        self.run_worker(T0 + 60)
        self.assertEqual(len(self.inquiries()), 6)
        # 응답하지 않았다는 이유로 기존 문의를 취소하거나 거절로 바꾸지 않는다.
        self.assertEqual({q['state'] for q in self.inquiries()[:3]}, {'PENDING'})
        self.decline_all(now=T0 + 61)
        self.run_worker(T0 + 61)
        self.assertEqual(len(self.inquiries()), 9)
        self.assertTrue(all(q['attempts'] == 1 for q in self.inquiries()))

    def test_existing_manual_requests_are_not_reissued_or_counted_as_fresh_contacts(self):
        self.add_hospitals()
        self.patient(auto_inquiry=True)
        service.send_inquiries(self.conn, T0, 'A', ['H1'])
        service.respond(self.conn, T0 + 1, self.inquiry_id('A', 'H1'), 'decline', 'nurse', reason='병상')
        self.run_worker(T0 + 2)
        h1 = next(q for q in self.inquiries() if q['hospital_id'] == 'H1')
        self.assertEqual((h1['state'], h1['attempts']), ('DECLINED', 1))
        self.assertEqual(len({q['hospital_id'] for q in self.inquiries()}), len(self.inquiries()))


class WorkflowAcceptanceAndLimits(WorkflowBase):
    def test_pending_final_candidates_are_monitored_and_can_reply_late(self):
        self.patient(auto_inquiry=True)
        self.run_worker()
        first = self.inquiries()
        self.assertEqual(len(first), 3)
        self.run_worker(T0 + 60)
        self.assertEqual(len(self.inquiries()), 3)
        self.assertTrue(self.job()['enabled'])
        self.assertEqual(self.job()['state'], 'RUNNING')
        service.respond(self.conn, T0 + 70, first[0]['id'], 'accept', 'late-nurse')
        self.run_worker(T0 + 71)
        self.assertEqual(self.job()['state'], 'AWAITING_CONFIRMATION')
        self.assertEqual(len(self.inquiries()), 3)

    def test_contact_cap_waits_for_last_wave_instead_of_stopping_at_send(self):
        self.add_hospitals()
        self.patient(auto_inquiry=True)
        for seconds in (0, 60, 120, 180):
            self.run_worker(T0 + seconds)
        self.assertEqual(len(self.inquiries()), 10)
        self.assertTrue(self.job()['enabled'])
        self.run_worker(T0 + 239)
        self.assertTrue(self.job()['enabled'])
        service.respond(self.conn, T0 + 239, self.inquiries()[-1]['id'], 'accept', 'late-nurse')
        self.run_worker(T0 + 240)
        self.assertEqual(self.job()['state'], 'AWAITING_CONFIRMATION')
        self.assertEqual(len(self.inquiries()), 10)

    def test_valid_acceptance_stops_expansion_until_human_confirms(self):
        self.add_hospitals()
        self.patient(auto_inquiry=True)
        self.run_worker()
        q = self.inquiries()[0]
        service.respond(self.conn, T0 + 1, q['id'], 'accept', 'nurse')
        self.run_worker(T0 + 2)
        self.assertEqual(self.job()['state'], 'AWAITING_CONFIRMATION')
        self.run_worker(T0 + 61)
        self.assertEqual(len(self.inquiries()), 3)
        view = service.patient_view(self.conn, 'A', T0 + 61)
        self.assertEqual(view['state'], 'WAITING')
        self.assertIsNone(view['destination_hospital_id'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM conversation_hospital_sessions').fetchone()[0], 0)
        self.run_worker(T0 + 301)
        self.assertEqual(self.job(now=T0 + 301)['state'], 'AWAITING_CONFIRMATION')
        self.assertEqual(len(self.inquiries()), 3)
        service.confirm_destination(self.conn, T0 + 302, 'A', q['id'])
        self.assertFalse(self.job()['enabled'])
        self.assertEqual(self.job()['state'], 'COMPLETED')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM conversation_hospital_sessions').fetchone()[0], 1)
        self.run_worker(T0 + 360)
        self.assertEqual(len(self.inquiries()), 3)

    def test_expired_acceptance_resumes_without_resending_old_inquiry(self):
        self.add_hospitals()
        self.patient(auto_inquiry=True)
        self.run_worker()
        accepted = self.inquiries()[0]
        service.respond(self.conn, T0 + 1, accepted['id'], 'accept', 'nurse')
        self.run_worker(T0 + 2)
        # 응답 TTL(기본 600초) 대신 만료가 임박한 수용 응답을 재현한다.
        self.conn.execute('UPDATE inquiries SET expires_at=? WHERE id=?', (T0 + 30, accepted['id']))
        self.run_worker(T0 + 61)
        self.assertEqual(len(self.inquiries()), 6)
        old = next(q for q in self.inquiries() if q['id'] == accepted['id'])
        self.assertIn(old['state'], ('ACCEPTED', 'EXPIRED'))
        self.assertEqual(old['attempts'], 1)
        self.assertNotIn(old['id'], self.job(now=T0 + 61)['valid_accepted_inquiry_ids'])
        self.assertNotEqual(self.job()['state'], 'AWAITING_CONFIRMATION')

    def test_new_resource_shortage_invalidates_acceptance_and_reopens_search(self):
        self.add_hospitals()
        self.patient(auto_inquiry=True)
        self.run_worker()
        accepted = self.inquiries()[0]
        service.respond(self.conn, T0 + 1, accepted['id'], 'accept', 'nurse')
        self.run_worker(T0 + 2)
        service.set_resource(self.conn, T0 + 30, accepted['hospital_id'], 'ed_bed', 1, 1, source='hospital_input')
        self.run_worker(T0 + 61)
        self.assertEqual(len(self.inquiries()), 6)
        self.assertNotEqual(self.job()['state'], 'AWAITING_CONFIRMATION')
        self.assertEqual(next(q for q in self.inquiries() if q['id'] == accepted['id'])['attempts'], 1)

    def test_contact_limit_and_deadline_leave_pending_requests_and_require_manual_followup(self):
        self.add_hospitals()
        self.patient(auto_inquiry=True)
        for seconds in (0, 60, 120, 180, 240):
            self.run_worker(T0 + seconds)
        self.assertEqual(len(self.inquiries()), 10)
        self.assertEqual(self.job()['state'], 'EXHAUSTED')
        self.assertEqual({q['state'] for q in self.inquiries()}, {'PENDING'})
        self.assertTrue(all(q['attempts'] == 1 for q in self.inquiries()))
        self.patient('B', auto_inquiry=True)
        self.run_worker()
        before = self.inquiries('B')
        self.run_worker(T0 + 301)
        self.assertEqual(self.job('B')['state'], 'EXHAUSTED')
        self.assertEqual(self.inquiries('B'), before)
        self.assertEqual(service.patient_view(self.conn, 'B', T0 + 301)['state'], 'WAITING')

    def test_no_compatible_hospital_requires_manual_followup_without_invalid_contacts(self):
        self.patient(auto_inquiry=True, required_capabilities=['없는 진료 분야'])
        self.run_worker()
        self.assertEqual(self.inquiries(), [])
        self.assertEqual(self.job()['state'], 'EXHAUSTED')
        self.assertIsNotNone(self.job()['stop_reason'])

    def test_cancellation_stops_job_in_same_transaction(self):
        self.patient(auto_inquiry=True)
        service.cancel_patient(self.conn, T0 + 1, 'A', '증상 호전')
        self.assertFalse(self.job()['enabled'])
        self.assertEqual(self.job()['state'], 'COMPLETED')
        self.run_worker(T0 + 60)
        self.assertEqual(self.inquiries(), [])


class WorkflowConcurrency(WorkflowBase):
    def test_scheduled_restriction_starting_during_travel_estimation_is_rechecked_before_send(self):
        self.add_hospitals(count=1)
        service.add_restriction(self.conn, T0, 'H1', 'all', '예정된 진료 제한',
                                starts_at=T0 + 10, ends_at=T0 + 100)
        self.patient(auto_inquiry=True)
        clock_value = [T0]
        revision = self.conn.execute('SELECT revision FROM planning_state WHERE id=1').fetchone()[0]
        observed_revisions = []
        conn = self.conn

        class TimeAdvancingEstimator:
            def estimate(self, origin, target):
                # 데이터 갱신 없이 계산 중 시간만 흘러 예정된 제한이 시작된다.
                observed_revisions.append(conn.execute('SELECT revision FROM planning_state WHERE id=1').fetchone()[0])
                clock_value[0] = T0 + 20
                return EST.estimate(origin, target)

        workflow.run_once(self.path, TimeAdvancingEstimator(), clock=lambda: clock_value[0])
        self.assertTrue(observed_revisions)
        self.assertEqual(set(observed_revisions), {revision})
        self.assertEqual(clock_value[0], T0 + 20)
        self.assertEqual({q['hospital_id'] for q in self.inquiries()}, {'H2', 'H3', 'W01'})
        self.assertEqual(len(self.inquiries()), 3)
        self.assertNotEqual(self.job(now=T0 + 20)['state'], 'ERROR')

    def test_one_corrupted_case_does_not_block_other_jobs_during_state_capture(self):
        self.patient('A', auto_inquiry=True)
        self.patient('B', auto_inquiry=True)
        # 이전 저장소의 잘못된 JSON을 재현하여 후보 계산 전 상태 조회부터 실패시킨다.
        self.conn.execute("UPDATE patients SET required_capabilities='not-json' WHERE id='A'")
        self.run_worker()
        failed = dict(self.conn.execute("SELECT * FROM inquiry_workflows WHERE patient_id='A'").fetchone())
        self.assertEqual(failed['state'], 'ERROR')
        self.assertFalse(failed['enabled'])
        self.assertEqual(self.inquiries('A'), [])
        self.assertGreater(len(self.inquiries('B')), 0)
        self.assertNotIn('not-json', failed['last_error'])

    def test_one_case_estimation_failure_does_not_stop_other_cases_or_expose_exception(self):
        self.patient('A', auto_inquiry=True, lat=35.16)
        self.patient('B', auto_inquiry=True, lat=35.18)
        class FailingForOnePatient:
            def estimate(self, origin, target):
                if origin[0] == 35.16:
                    raise RuntimeError('테스트 비밀정보가 포함된 내부 예외')
                return EST.estimate(origin, target)
        self.run_worker(estimator=FailingForOnePatient())
        failed = self.job('A')
        self.assertEqual(failed['state'], 'ERROR')
        self.assertFalse(failed['enabled'])
        self.assertEqual(self.inquiries('A'), [])
        self.assertGreater(len(self.inquiries('B')), 0)
        self.assertNotIn('비밀정보', json.dumps(failed, ensure_ascii=False))
        event_data = [r['data'] for r in self.conn.execute("SELECT data FROM events WHERE patient_id='A'")]
        self.assertNotIn('비밀정보', ''.join(event_data))

    def test_two_workers_do_not_duplicate_or_send_two_simultaneous_waves(self):
        self.add_hospitals()
        self.patient(auto_inquiry=True)
        barrier, errors = threading.Barrier(2), []

        def run():
            try:
                barrier.wait(timeout=5)
                self.run_worker()
            except Exception as error:
                errors.append(error)

        threads = [threading.Thread(target=run) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(len(self.inquiries()), 3)
        self.assertTrue(all(q['attempts'] == 1 for q in self.inquiries()))

    def test_travel_estimation_holds_no_write_lock_and_stale_patient_result_is_discarded(self):
        self.add_hospitals()
        self.patient(auto_inquiry=True)
        estimating, release, written = threading.Event(), threading.Event(), threading.Event()
        errors = []

        class BlockingEstimator:
            def estimate(self, origin, target):
                estimating.set()
                if not release.wait(timeout=10):
                    raise RuntimeError('테스트 이동시간 계산 대기 시간 초과')
                return EST.estimate(origin, target)

        def calculate():
            try:
                self.run_worker(estimator=BlockingEstimator())
            except Exception as error:
                errors.append(error)

        def update():
            conn = db.connect(self.path)
            try:
                service.update_patient(conn, T0 + 1, 'A', {'required_capabilities': ['없는 진료 분야']})
                written.set()
            except Exception as error:
                errors.append(error)
            finally:
                conn.close()

        calculator = threading.Thread(target=calculate)
        calculator.start()
        writer = None
        try:
            self.assertTrue(estimating.wait(timeout=5))
            writer = threading.Thread(target=update)
            writer.start()
            self.assertTrue(written.wait(timeout=3), '이동시간 계산이 DB 쓰기 잠금을 점유함')
        finally:
            release.set()
            calculator.join(timeout=15)
            if writer:
                writer.join(timeout=15)
        self.assertFalse(calculator.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(self.inquiries(), [])
        self.run_worker(T0 + 2)
        self.assertEqual(self.inquiries(), [])

    def test_other_patient_requests_and_responses_are_not_changed_by_workflow(self):
        self.add_hospitals()
        self.patient('B')
        accepted = self.accepted('B', 'H1')
        self.conn.execute('UPDATE inquiries SET expires_at=? WHERE id=?', (T0 + 30, accepted))
        before = self.inquiries('B')
        self.patient('A', auto_inquiry=True)
        self.run_worker()
        self.run_worker(T0 + 60)
        self.assertEqual(self.inquiries('B'), before)
        self.assertEqual(self.inquiries('B')[0]['id'], accepted)


class WorkflowHttp(WorkflowBase):
    def test_create_start_pause_and_get_routes_and_get_is_readonly(self):
        with self.http() as call:
            status, patient = call('POST', '/api/patients', dict(base_patient(), id='HTTP-A', auto_inquiry=True))
            self.assertEqual(status, 200)
            self.assertEqual(patient['inquiry_workflow']['state'], 'QUEUED')
            endpoint = '/api/patients/HTTP-A/inquiry-workflow'
            for _ in range(3):
                status, job = call('GET', endpoint)
                self.assertEqual(status, 200)
                self.assertEqual(job['state'], 'QUEUED')
            self.assertEqual(self.inquiries('HTTP-A'), [])
            self.assertEqual(call('POST', endpoint, {'enabled': False})[1]['state'], 'PAUSED')
            self.assertEqual(call('POST', endpoint, {'enabled': True})[1]['state'], 'QUEUED')
            self.assertEqual(call('POST', endpoint, {'enabled': 'true'})[0], 400)
            self.assertEqual(call('GET', '/api/patients/MISSING/inquiry-workflow')[0], 404)

    def test_background_processing_sends_requests_without_any_client_get(self):
        from server.inquiry_worker import InquiryWorker
        with self.http() as call:
            status, _ = call('POST', '/api/patients', dict(base_patient(), id='HTTP-B', auto_inquiry=True))
            self.assertEqual(status, 200)
            # GET 없이 별도 서버 작업이 진행한다. HTTP 조회는 결과 확인 단계에만 쓴다.
            completed = threading.Event()
            class NotifyingEstimator:
                def estimate(self, origin, target):
                    completed.set()
                    return EST.estimate(origin, target)
            worker = InquiryWorker(self.path, NotifyingEstimator(), poll_seconds=.02, clock=lambda: T0 + 10)
            worker.start()
            try:
                self.assertTrue(completed.wait(timeout=5))
                # 명시적인 클라이언트 조회 없이 DB에 문의가 생성될 때까지 기다린다.
                for _ in range(100):
                    if self.inquiries('HTTP-B'):
                        break
                    threading.Event().wait(.02)
                self.assertGreater(len(self.inquiries('HTTP-B')), 0)
            finally:
                worker.stop()
            status, patient = call('GET', '/api/patients/HTTP-B')
            self.assertEqual(status, 200)
            self.assertGreater(len(patient['inquiries']), 0)


if __name__ == '__main__':
    import unittest
    unittest.main()
