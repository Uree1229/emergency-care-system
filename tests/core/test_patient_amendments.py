"""Patient information changes after inquiries, using only temporary synthetic data."""
import concurrent.futures
import copy
import json
import sqlite3
import threading

from server import db, inquiry_observations, inquiry_workflow as workflow, patient_amendments as amendments, service
from server.service import ServiceError
from test_inquiry_workflow import WorkflowBase
from test_server import EST, T0


class PatientAmendmentTests(WorkflowBase):
    def setUp(self):
        super().setUp()
        self.conn.executescript(amendments.SCHEMA)
        self.patient()

    def payload(self, **changes):
        value = dict(actor_id='crew-1', client_request_id='amend-1', expected_patient_version=1,
                     reason='환자 상태 재확인', changes={'symptoms': '고열 및 구토'})
        value.update(changes)
        return value

    def amend(self, now=T0 + 30, pid='A', **changes):
        return amendments.amend(self.conn, now, pid, self.payload(**changes))

    def error(self, code, fn, *args, **kwargs):
        with self.assertRaises(ServiceError) as caught:
            fn(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def business(self):
        return {name: [dict(r) for r in self.conn.execute(f'SELECT * FROM {name} ORDER BY rowid')]
                for name in ('patients', 'inquiries', 'inquiry_workflows', 'events',
                             'resources', 'planning_state', 'patient_amendments')}

    def send(self, hospitals=('H1', 'H2')):
        service.send_inquiries(self.conn, T0 + 5, 'A', list(hospitals))

    def test_question_then_amend_closes_request_and_preserves_question_history(self):
        self.send(('H1',))
        iid = self.inquiry_id('A', 'H1')
        service.respond(self.conn, T0 + 10, iid, 'question', 'desk', question='현재 의식 상태는?')
        prior = [tuple(r) for r in self.conn.execute('SELECT * FROM events')]
        result = self.amend()
        self.assertFalse(result['duplicate'])
        self.assertEqual(result['patient']['version'], 2)
        self.assertEqual(result['patient']['state'], 'WAITING')
        q = service._inquiry(self.conn, iid)
        self.assertEqual((q['state'], q['patient_version'], q['requires_review_version']), ('CANCELLED', 1, 1))
        self.assertEqual(q['question'], '현재 의식 상태는?')
        self.assertEqual([tuple(r) for r in self.conn.execute('SELECT * FROM events')][:len(prior)], prior)
        self.assertEqual(result['amendment']['closed_inquiry_ids'], [iid])

    def test_previous_acceptance_cannot_be_used_after_amendment(self):
        iid = self.accepted('A', 'H1')
        responded = service._inquiry(self.conn, iid)['responded_at']
        result = self.amend()
        self.assertEqual(service._inquiry(self.conn, iid)['state'], 'CANCELLED')
        self.assertEqual(service._inquiry(self.conn, iid)['responded_at'], responded)
        self.assertIsNone(result['patient']['destination_hospital_id'])
        self.error('NOT_ACCEPTED', service.confirm_destination, self.conn, T0 + 31, 'A', iid)

    def test_historical_declines_and_cancellations_preserved_but_version_guard_enabled(self):
        self.send(('H1', 'H2', 'H3'))
        service.respond(self.conn, T0 + 6, self.inquiry_id('A', 'H1'), 'decline', 'desk', reason='병상')
        service.cancel_inquiry(self.conn, T0 + 7, 'A', self.inquiry_id('A', 'H2'), '기타')
        result = self.amend()
        states = {q['hospital_id']: q['state'] for q in result['patient']['inquiries']}
        self.assertEqual(states, {'H1': 'DECLINED', 'H2': 'CANCELLED', 'H3': 'CANCELLED'})
        self.assertTrue(all(q['requires_review_version'] == 1 for q in result['patient']['inquiries']))
        self.assertEqual(result['amendment']['affected_hospital_ids'], ['H1', 'H2', 'H3'])

    def test_same_inquiry_can_be_resent_for_new_version_and_requires_fresh_response_version(self):
        self.send(('H1',))
        iid = self.inquiry_id('A', 'H1')
        old = service._inquiry(self.conn, iid)['version']
        self.amend()
        self.error('REVIEW_VERSION_REQUIRED', service.respond, self.conn, T0 + 31, iid, 'accept', 'desk')
        service.send_inquiries(self.conn, T0 + 32, 'A', ['H1'])
        current = service._inquiry(self.conn, iid)
        self.assertEqual(current['patient_version'], 2)
        self.assertEqual(current['attempts'], 2)
        self.error('CONFLICT', service.respond, self.conn, T0 + 33, iid, 'accept', 'desk', expected_version=old)
        service.respond(self.conn, T0 + 34, iid, 'accept', 'desk', expected_version=current['version'])
        self.assertEqual(service._inquiry(self.conn, iid)['state'], 'ACCEPTED')

    def test_legacy_patch_contract_is_unchanged(self):
        self.send()
        self.error('ALREADY_SENT', service.update_patient, self.conn, T0 + 20, 'A', {'symptoms': '바뀐 증상'})
        self.assertEqual(self.amend()['patient']['symptoms'], '고열 및 구토')

    def test_exact_retry_is_success_even_after_destination_confirmed(self):
        self.send(('H1',))
        original = self.amend()
        service.send_inquiries(self.conn, T0 + 31, 'A', ['H1'])
        iid = self.inquiry_id('A', 'H1')
        service.respond(self.conn, T0 + 32, iid, 'accept', 'desk',
                        expected_version=service._inquiry(self.conn, iid)['version'])
        service.confirm_destination(self.conn, T0 + 33, 'A', iid)
        before = self.business()
        retry = self.amend(now=T0 + 40)
        self.assertTrue(retry['duplicate'])
        self.assertEqual(retry['amendment'], original['amendment'])
        self.assertEqual(retry['patient']['state'], 'CONFIRMED')
        self.assertEqual(self.business(), before)

    def test_exact_retry_after_patient_cancelled_does_not_reopen_case(self):
        original = self.amend()
        service.cancel_patient(self.conn, T0 + 31, 'A', '기타')
        before = self.business()
        retry = self.amend(now=T0 + 32)
        self.assertTrue(retry['duplicate'])
        self.assertEqual(retry['amendment'], original['amendment'])
        self.assertEqual(retry['patient']['state'], 'CANCELLED')
        self.assertEqual(self.business(), before)

    def test_conflicting_retry_does_not_change_any_state(self):
        self.amend()
        before = self.business()
        self.error('CONFLICT', self.amend, changes={'symptoms': '다른 내용'})
        self.error('CONFLICT', self.amend, reason='다른 사유')
        self.patient('B')
        self.error('CONFLICT', self.amend, pid='B')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM patient_amendments').fetchone()[0], 1)
        self.assertEqual(self.business()['patient_amendments'], before['patient_amendments'])

    def test_stale_expected_patient_version_is_rejected_without_side_effects(self):
        self.amend()
        before = self.business()
        self.error('CONFLICT', self.amend, client_request_id='next')
        self.assertEqual(self.business(), before)

    def test_real_second_amendment_increments_once(self):
        self.amend()
        result = self.amend(now=T0 + 40, client_request_id='amend-2', expected_patient_version=2,
                            changes={'breathing': 'abnormal'})
        self.assertEqual(result['patient']['version'], 3)
        self.assertEqual((result['amendment']['from_version'], result['amendment']['to_version']), (2, 3))

    def test_noop_and_equivalent_set_order_do_not_invalidate_requests(self):
        self.send()
        before = self.business()
        self.error('NO_CHANGE', self.amend, changes={'symptoms': '고열'})
        self.error('NO_CHANGE', self.amend, changes={'danger_signs': []})
        self.assertEqual(self.business(), before)

    def test_audit_contains_only_changed_fields_and_does_not_copy_full_profile(self):
        result = self.amend()
        audit = result['amendment']
        self.assertEqual(audit['changed_fields'], ['symptoms'])
        self.assertEqual(audit['before'], {'symptoms': '고열'})
        self.assertEqual(audit['after'], {'symptoms': '고열 및 구토'})
        self.assertNotIn('request_fingerprint', audit)
        self.assertNotIn('contact', audit['after'])

    def test_audit_original_is_immutable(self):
        self.amend()
        for sql in ('UPDATE patient_amendments SET reason="changed"', 'DELETE FROM patient_amendments',
                    'INSERT OR REPLACE INTO patient_amendments SELECT * FROM patient_amendments'):
            with self.subTest(sql=sql), self.assertRaises(sqlite3.IntegrityError):
                self.conn.execute(sql)

    def test_change_of_required_resources_updates_explicit_and_bound_fields(self):
        result = self.amend(changes={'resource_demands': {'ed_bed': 2, 'special_team': 1},
                                     'required_capabilities': ['소아청소년과']})
        self.assertEqual(result['patient']['resource_demands'], {'ed_bed': 2, 'special_team': 1})
        self.assertEqual(result['patient']['explicit_resource_demands'], {'ed_bed': 2, 'special_team': 1})
        self.assertEqual(result['patient']['required_capabilities'], ['소아청소년과'])

    def test_unknown_priority_stays_conservative(self):
        result = self.amend(changes={'consciousness': 'unknown', 'breathing': 'unknown'})
        self.assertEqual(result['patient']['effective_priority'], 4)

    def test_invalid_changes_and_control_fields_are_atomic(self):
        self.send()
        before = self.business()
        for changes in ({}, {'lat': 35}, {'reporter_role': 'self'}, {'consent': False},
                        {'state': 'CONFIRMED'}, {'destination_hospital_id': 'H1'},
                        {'priority_source': 'expert'}, {'age': True},
                        {'age': 1 << 70}, {'onset_at': 1 << 70},
                        {'resource_demands': {'ed_bed': 1 << 70}},
                        {'resource_demands': {'ed_bed': 0}}, {'extra': {'value': float('nan')}},
                        {'symptoms': 'x' * 17000}):
            with self.subTest(changes=list(changes)):
                self.error('INVALID', self.amend, changes=changes)
                self.assertEqual(self.business(), before)

    def test_invalid_envelope_and_identifiers_are_rejected(self):
        for field, value in (('actor_id', ''), ('client_request_id', 'spaces not allowed'),
                             ('expected_patient_version', True), ('expected_patient_version', '1'),
                             ('reason', ''), ('reason', 'x' * 501), ('reason', 'line\nbreak')):
            with self.subTest(field=field, value=str(value)[:20]):
                self.error('INVALID', self.amend, **{field: value})
        self.error('INVALID', amendments.amend, self.conn, T0 + 30, 'A', [])
        self.error('INVALID', self.amend, extra_field='not permitted')

    def test_non_waiting_states_are_rejected_atomically(self):
        for state in ('CONFIRMED', 'IN_TRANSIT', 'ARRIVED', 'COMPLETED', 'CANCELLED'):
            with self.subTest(state=state):
                self.conn.execute('UPDATE patients SET state=? WHERE id=?', (state, 'A'))
                before = self.business()
                self.error('INVALID_STATE', self.amend)
                self.assertEqual(self.business(), before)

    def test_future_retry_and_before_registration_clock_are_rejected(self):
        self.amend()
        self.error('CLOCK_CONFLICT', self.amend, now=T0 + 29)
        self.patient('B', now=T0 + 100)
        self.error('CLOCK_CONFLICT', self.amend, now=T0 + 99, pid='B', client_request_id='future')

    def test_history_pages_and_future_cutoff_are_read_only(self):
        self.amend()
        self.amend(now=T0 + 40, expected_patient_version=2, client_request_id='second', changes={'age': 8})
        before = self.business()
        first = amendments.list_records(self.conn, T0 + 50, 'A', limit=1)
        self.assertTrue(first['has_more'])
        self.assertEqual(first['amendments'][0]['to_version'], 3)
        second = amendments.list_records(self.conn, T0 + 50, 'A', before_id=first['next_before_id'])
        self.assertEqual(second['amendments'][0]['to_version'], 2)
        self.assertEqual(len(amendments.list_records(self.conn, T0 + 35, 'A')['amendments']), 1)
        self.assertEqual(self.business(), before)
        for limit in (0, 101, True):
            self.error('INVALID', amendments.list_records, self.conn, T0 + 50, 'A', limit=limit)

    def test_automatic_queries_revisit_hospitals_for_new_patient_version(self):
        self.patient('AUTO', auto_inquiry=True)
        self.run_worker()
        old = self.inquiries('AUTO')
        self.assertEqual(len(old), 3)
        result = self.amend(pid='AUTO')
        self.assertEqual(result['patient']['inquiry_workflow']['contacted_count'], 3)
        self.assertEqual(result['patient']['inquiry_workflow']['current_version_contacted_count'], 0)
        self.run_worker(T0 + 31)
        new = self.inquiries('AUTO')
        self.assertEqual([q['id'] for q in new], [q['id'] for q in old])
        self.assertTrue(all(q['patient_version'] == 2 and q['attempts'] == 2 for q in new))
        self.assertEqual(self.job('AUTO', T0 + 31)['current_version_contacted_count'], 3)

    def test_workflow_deadline_policy_and_round_are_not_extended_by_amendment(self):
        self.patient('AUTO', auto_inquiry=True)
        self.run_worker()
        before = self.job('AUTO')
        self.amend(pid='AUTO')
        after = self.job('AUTO')
        for key in ('started_at', 'deadline_at', 'round_no', 'policy_name', 'policy_version', 'policy_config'):
            self.assertEqual(after[key], before[key], key)
        self.assertEqual(after['state'], 'QUEUED')
        self.assertEqual(after['next_evaluation_at'], T0 + 30)
        self.assertGreater(after['version'], before['version'])

    def test_paused_workflow_remains_paused(self):
        self.patient('AUTO', auto_inquiry=True)
        self.run_worker()
        workflow.start(self.conn, T0 + 10, 'AUTO', enabled=False)
        before = dict(workflow._job(self.conn, 'AUTO'))
        self.amend(pid='AUTO')
        self.assertEqual(workflow._job(self.conn, 'AUTO'), before)
        self.run_worker(T0 + 31)
        self.assertTrue(all(q['state'] == 'CANCELLED' for q in self.inquiries('AUTO')))

    def test_elapsed_deadline_is_not_reset_to_allow_more_automatic_queries(self):
        self.patient('AUTO', auto_inquiry=True)
        self.run_worker()
        deadline = self.job('AUTO')['deadline_at']
        self.amend(pid='AUTO', now=deadline + 1)
        self.run_worker(deadline + 2)
        self.assertEqual(self.job('AUTO', deadline + 2)['state'], 'EXHAUSTED')
        self.assertEqual(self.job('AUTO', deadline + 2)['deadline_at'], deadline)
        self.assertTrue(all(q['state'] == 'CANCELLED' for q in self.inquiries('AUTO')))

    def test_policy_mismatch_is_detected_before_any_mutation(self):
        self.patient('AUTO', auto_inquiry=True)
        self.conn.execute('UPDATE inquiry_workflows SET policy_version=? WHERE patient_id=?', ('old', 'AUTO'))
        before = self.business()
        self.error('POLICY_VERSION_MISMATCH', self.amend, pid='AUTO')
        self.assertEqual(self.business(), before)

    def test_snapshot_computed_before_amendment_cannot_publish_queries(self):
        self.patient('AUTO', auto_inquiry=True)
        snapshot, _ = workflow._capture(self.conn, T0, 'AUTO')
        candidates = service.candidates(self.conn, T0, 'AUTO', EST)
        ledger = inquiry_observations.capture(self.conn, T0, 'AUTO')
        self.amend(pid='AUTO')
        self.assertFalse(workflow._apply(self.conn, T0 + 31, snapshot, candidates, ledger))
        self.assertEqual(self.inquiries('AUTO'), [])

    def test_concurrent_exact_retries_create_one_amendment(self):
        barrier = threading.Barrier(2)
        def apply():
            conn = db.connect(self.path)
            try:
                barrier.wait(timeout=5)
                return amendments.amend(conn, T0 + 30, 'A', self.payload())
            finally:
                conn.close()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            first, second = list(pool.map(lambda _: apply(), range(2)))
        self.assertEqual({first['duplicate'], second['duplicate']}, {False, True})
        self.assertEqual(first['amendment']['id'], second['amendment']['id'])
        self.assertEqual(service._patient(self.conn, 'A')['version'], 2)

    def test_competing_new_requests_for_same_patient_version_have_one_winner(self):
        barrier = threading.Barrier(2)
        def apply(index):
            conn = db.connect(self.path)
            try:
                barrier.wait(timeout=5)
                try:
                    amendments.amend(conn, T0 + 30, 'A', self.payload(client_request_id=f'key-{index}'))
                    return 'success'
                except ServiceError as error:
                    return error.code
            finally:
                conn.close()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            result = list(pool.map(apply, (1, 2)))
        self.assertCountEqual(result, ['success', 'CONFLICT'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM patient_amendments').fetchone()[0], 1)
