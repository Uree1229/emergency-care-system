"""추천 조회·워커 가용성·수용 응답과 모델 입력 부족의 구분. 실제 DB/API는 사용하지 않는다."""
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
import json
import threading
from types import SimpleNamespace
import urllib.error
import urllib.request

from server import db, planning, public_data, service, solver_adapter, worker
from server.app import make_handler
from solver_improved import solve
from test_server import Base, EST, T0


def runtime_with(state):
    enabled = None if state == 'UNKNOWN' else state != 'DISABLED'
    observed = dict(server=dict(state='RUNNING'), workers=dict(
        planning=dict(state=state, enabled=enabled, thread_alive=state not in ('DISABLED', 'STOPPED')),
        inquiry=dict(state='RUNNING', enabled=True, thread_alive=True)))
    return SimpleNamespace(snapshot=lambda: observed)


class RecommendationHttpContract(Base):
    @contextmanager
    def http(self, runtime=None, now=T0 + 20):
        server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(
            self.path, EST, clock=lambda: now, runtime=runtime))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def call(path, method='GET', payload=None):
            request = urllib.request.Request(
                f'http://127.0.0.1:{server.server_port}{path}', method=method,
                data=None if payload is None else json.dumps(payload).encode('utf-8'),
                headers={'Content-Type': 'application/json'})
            try:
                response = urllib.request.urlopen(request, timeout=5)
            except urllib.error.HTTPError as exc:
                response = exc
            with response:
                return response.code, json.load(response)

        try:
            yield call
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def database_state(self):
        return list(self.conn.iterdump())

    def test_unobserved_runtime_preserves_queued_and_unknown_availability(self):
        self.patient()
        with self.http() as call:
            status, result = call('/api/patients/A/recommendation')
        self.assertEqual((status, result['status'], result['result']), (200, 'QUEUED', None))
        self.assertEqual(result['availability'], dict(worker_state='UNKNOWN', accepting_requests=None,
                                                      reason_code='PLANNER_UNOBSERVED'))

    def test_worker_states_have_explicit_availability_without_changing_envelope(self):
        self.patient()
        states = (
            ('RUNNING', True, None), ('STARTING', True, None),
            ('ERROR', True, 'PLANNER_ERROR'), ('STALLED', True, 'PLANNER_STALLED'),
            ('DISABLED', False, 'PLANNER_DISABLED'), ('STOPPED', False, 'PLANNER_STOPPED'),
            ('STOPPING', False, 'PLANNER_STOPPING'), ('UNKNOWN', None, 'PLANNER_UNOBSERVED'))
        for state, accepting, reason in states:
            with self.subTest(state=state), self.http(runtime_with(state)) as call:
                status, result = call('/api/patients/A/recommendation')
                self.assertEqual(status, 200)
                self.assertEqual(result['status'], 'QUEUED')
                self.assertEqual(result['availability'], dict(worker_state=state,
                    accepting_requests=accepting, reason_code=reason))

    def test_disabled_stopped_and_stopping_replan_do_not_modify_database(self):
        self.patient()
        for state in ('DISABLED', 'STOPPED', 'STOPPING'):
            before = self.database_state()
            with self.subTest(state=state), self.http(runtime_with(state)) as call:
                status, result = call('/api/patients/A/replan', 'POST', {})
                self.assertEqual(status, 409)
                self.assertEqual(result['error'], 'PLANNER_' + state)
            self.assertEqual(self.database_state(), before)

    def test_unobserved_runtime_still_accepts_legacy_replan(self):
        self.patient()
        revision = self.conn.execute('SELECT revision FROM planning_state').fetchone()[0]
        with self.http() as call:
            status, result = call('/api/patients/A/replan', 'POST', {})
        self.assertEqual(status, 200)
        self.assertEqual(result['availability']['accepting_requests'], None)
        self.assertEqual(self.conn.execute('SELECT revision FROM planning_state').fetchone()[0], revision + 1)

    def test_error_and_stalled_workers_allow_replan_for_recovery(self):
        self.patient()
        for state in ('ERROR', 'STALLED'):
            revision = self.conn.execute('SELECT revision FROM planning_state').fetchone()[0]
            with self.subTest(state=state), self.http(runtime_with(state)) as call:
                status, result = call('/api/patients/A/replan', 'POST', {})
                self.assertEqual(status, 200)
                self.assertTrue(result['availability']['accepting_requests'])
                self.assertEqual(result['availability']['reason_code'], 'PLANNER_' + state)
            self.assertEqual(self.conn.execute('SELECT revision FROM planning_state').fetchone()[0], revision + 1)

    def test_terminal_and_missing_patient_validation_precede_disabled_worker(self):
        self.patient()
        service.cancel_patient(self.conn, T0 + 1, 'A', '119 이송')
        before = self.database_state()
        with self.http(runtime_with('DISABLED')) as call:
            status, result = call('/api/patients/A/recommendation')
            self.assertEqual((status, result['status']), (200, 'NOT_APPLICABLE'))
            self.assertEqual(result['patient_state'], 'CANCELLED')
            self.assertIsNone(result['result'])
            status, result = call('/api/patients/A/replan', 'POST', {})
            self.assertEqual((status, result['error']), (409, 'INVALID_STATE'))
            for suffix, method, payload in (('recommendation', 'GET', None), ('replan', 'POST', {})):
                status, result = call('/api/patients/MISSING/' + suffix, method, payload)
                self.assertEqual((status, result['error']), (404, 'NOT_FOUND'))
        self.assertEqual(self.database_state(), before)

    def test_fresh_result_and_its_assumptions_survive_availability_decoration(self):
        self.patient()
        self.accepted('A', 'H1')
        self.assertTrue(worker.run_once(self.path, EST, clock=lambda: T0 + 20, seconds=0))
        expected = planning.patient_result(self.conn, T0 + 20, 'A')
        with self.http(runtime_with('DISABLED')) as call:
            status, result = call('/api/patients/A/recommendation')
        self.assertEqual(status, 200)
        self.assertEqual({key: result[key] for key in expected}, expected)
        self.assertEqual(result['status'], 'READY')
        self.assertEqual(result['result']['status'], 'RECOMMENDED')
        self.assertEqual(result['result']['assumptions']['parameter_source'], 'experiment')
        self.assertFalse(result['availability']['accepting_requests'])

    def test_expired_recommendation_is_stale_and_hidden_without_mutation(self):
        self.patient()
        self.accepted('A', 'H1')
        worker.run_once(self.path, EST, clock=lambda: T0 + 20, seconds=0)
        before = self.database_state()
        with self.http(runtime_with('RUNNING'), now=T0 + 51) as call:
            status, result = call('/api/patients/A/recommendation')
        self.assertEqual((status, result['status'], result['result']), (200, 'STALE', None))
        self.assertTrue(result['availability']['accepting_requests'])
        self.assertEqual(self.database_state(), before)

    def test_patient_and_hospital_get_share_availability_and_do_not_mutate_database(self):
        self.patient()
        iid = self.accepted('A', 'H1')
        service.confirm_destination(self.conn, T0 + 16, 'A', iid)
        before = self.database_state()
        with self.http(runtime_with('DISABLED')) as call:
            pstatus, patient = call('/api/patients/A/recommendation')
            hstatus, hospital = call('/api/hospitals/H1/incoming')
            empty_status, empty_hospital = call('/api/hospitals/H2/incoming')
        self.assertEqual((pstatus, hstatus, empty_status), (200, 200, 200))
        self.assertEqual(hospital['planning_availability'], patient['availability'])
        self.assertEqual(hospital['planning'][0]['availability'], patient['availability'])
        self.assertEqual(hospital['planning'][0]['patient_id'], 'A')
        self.assertEqual(empty_hospital['planning'], [])
        self.assertEqual(empty_hospital['planning_availability'], patient['availability'])
        self.assertEqual(self.database_state(), before)


class AcceptedEligibilityContract(Base):
    def result(self, now=T0 + 20):
        snapshot = planning.capture(self.conn, now)
        model, context = solver_adapter.build(snapshot, EST)
        plan, meta = solve(model, seconds=0)
        return solver_adapter.present(snapshot, model, context, plan, meta)['A']

    def accepted_reason(self, result, iid, code):
        reason = next(item for item in result['eligibility_reasons'] if item['inquiry_id'] == iid)
        self.assertEqual(reason['hospital_id'], 'H1')
        self.assertIn(code, reason['codes'])
        self.assertNotIn('NO_VALID_ACCEPTED_HOSPITAL', result['warnings'])
        return reason

    def test_current_accepted_eligible_hospital_keeps_recommendation(self):
        self.patient()
        iid = self.accepted('A', 'H1')
        result = self.result()
        self.assertEqual(result['status'], 'RECOMMENDED')
        self.assertEqual(result['inquiry_id'], iid)
        self.assertEqual(result['eligibility_reasons'], [dict(hospital_id='H1', inquiry_id=iid, codes=[])])

    def test_no_accepted_response_retains_no_valid_accepted_warning(self):
        self.patient()
        service.send_inquiries(self.conn, T0 + 10, 'A', ['H1'])
        result = self.result()
        self.assertEqual(result['eligibility_reasons'], [])
        self.assertIn('NO_VALID_ACCEPTED_HOSPITAL', result['warnings'])
        self.assertNotIn('NO_ELIGIBLE_ACCEPTED_HOSPITAL', result['warnings'])

    def test_expired_and_patient_version_mismatched_responses_are_not_current_acceptances(self):
        self.patient()
        iid = self.accepted('A', 'H1')
        self.conn.execute('UPDATE inquiries SET expires_at=? WHERE id=?', (T0 + 20, iid))
        expired = self.result()
        self.assertEqual(expired['eligibility_reasons'], [])
        self.assertIn('NO_VALID_ACCEPTED_HOSPITAL', expired['warnings'])
        self.conn.execute('UPDATE inquiries SET expires_at=?, patient_version=patient_version-1 WHERE id=?',
                          (T0 + 100, iid))
        mismatched = self.result()
        self.assertEqual(mismatched['eligibility_reasons'], [])
        self.assertIn('NO_VALID_ACCEPTED_HOSPITAL', mismatched['warnings'])

    def test_accepted_but_missing_operating_resource_is_not_claimed_to_be_no_response(self):
        self.patient()
        iid = self.accepted('A', 'H1')
        self.conn.execute("DELETE FROM resources WHERE hospital_id='H1'")
        result = self.result()
        self.accepted_reason(result, iid, 'OPERATING_RESOURCE_MISSING')
        self.assertIn('NO_ELIGIBLE_ACCEPTED_HOSPITAL', result['warnings'])
        self.assertIn('OPERATING_RESOURCE_MISSING:H1', result['warnings'])
        self.assertEqual(result['status'], 'UNASSIGNED')
        self.assertFalse(result['confirmable_now'])
        self.assertEqual(service.confirm_destination(self.conn, T0 + 21, 'A', iid)['state'], 'CONFIRMED')

    def test_unknown_and_stale_operating_resources_have_distinct_reasons(self):
        self.patient()
        iid = self.accepted('A', 'H1')
        service.set_resource(self.conn, T0 + 16, 'H1', 'ed_bed', 1, 0, 'unknown')
        result = self.result()
        self.accepted_reason(result, iid, 'OPERATING_RESOURCE_UNKNOWN')
        self.assertIn('OPERATING_RESOURCE_UNKNOWN:H1', result['warnings'])
        service.set_resource(self.conn, T0 - service.STALE_AFTER, 'H1', 'ed_bed', 1, 0)
        result = self.result()
        self.accepted_reason(result, iid, 'OPERATING_RESOURCE_STALE')
        self.assertIn('OPERATING_RESOURCE_STALE:H1', result['warnings'])

    def test_capacity_shortfall_is_distinct_from_missing_acceptance(self):
        self.patient()
        iid = self.accepted('A', 'H1')
        service.set_resource(self.conn, T0 + 16, 'H1', 'ed_bed', 1, 1)
        result = self.result()
        self.accepted_reason(result, iid, 'RESOURCE_CAPACITY_INSUFFICIENT')
        self.assertIn('RESOURCE_CAPACITY_INSUFFICIENT:H1', result['warnings'])
        self.assertFalse(result['confirmable_now'])

    def test_location_missing_is_explicit_even_with_acceptance(self):
        self.patient(lat=None, lon=None)
        iid = self.accepted('A', 'H1')
        result = self.result()
        self.accepted_reason(result, iid, 'LOCATION_REQUIRED')
        self.assertIn('NO_ELIGIBLE_ACCEPTED_HOSPITAL', result['warnings'])

    def test_unsupported_resource_is_explicit_even_with_acceptance(self):
        self.patient(resource_demands={'icu': 1})
        iid = self.accepted('A', 'H1')
        result = self.result()
        self.accepted_reason(result, iid, 'UNSUPPORTED_RESOURCE')
        self.assertEqual(result['status'], 'NEEDS_REVIEW')

    def test_capability_mismatch_after_acceptance_has_explicit_reason(self):
        self.patient(required_capabilities=['소아청소년과'])
        iid = self.accepted('A', 'H1')
        hospital = service.hospital_view(self.conn, 'H1', T0 + 16)
        service.upsert_hospital(self.conn, {**hospital, 'capabilities': ['응급의학']})
        result = self.result()
        self.accepted_reason(result, iid, 'CAPABILITY_MISMATCH')
        self.assertIn('NO_ELIGIBLE_ACCEPTED_HOSPITAL', result['warnings'])

    def test_restriction_after_acceptance_has_explicit_reason(self):
        self.patient()
        iid = self.accepted('A', 'H1')
        service.add_restriction(self.conn, T0 + 16, 'H1', 'all', '시연 제한')
        result = self.result()
        self.accepted_reason(result, iid, 'RESTRICTED')
        self.assertIn('NO_ELIGIBLE_ACCEPTED_HOSPITAL', result['warnings'])

    def test_public_bed_observation_does_not_fill_missing_operating_resources(self):
        self.patient()
        iid = self.accepted('A', 'H1')
        self.conn.execute("DELETE FROM resources WHERE hospital_id='H1'")
        public_data._save_snapshot(self.conn, 'H1', 'nmc_rltm_beds', T0 + 16, T0 + 16,
                                  dict(available_beds={'ed': 99}), {'hvec': '99'})
        result = self.result()
        self.accepted_reason(result, iid, 'OPERATING_RESOURCE_MISSING')
        self.assertEqual(result['status'], 'UNASSIGNED')
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM resources WHERE hospital_id='H1'").fetchone()[0], 0)

    def test_reason_list_is_scoped_to_current_accepted_hospitals(self):
        self.patient()
        accepted = self.accepted('A', 'H1')
        service.send_inquiries(self.conn, T0 + 16, 'A', ['H2', 'H3'])
        rejected = self.inquiry_id('A', 'H2')
        service.respond(self.conn, T0 + 17, rejected, 'decline', 'tester', reason='기타')
        result = self.result()
        self.assertEqual([item['inquiry_id'] for item in result['eligibility_reasons']], [accepted])


if __name__ == '__main__':
    import unittest
    unittest.main()
