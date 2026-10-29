"""서버 문의 정책 선택·관측 이력·결정 로그의 연결 검증.

실행: python -m unittest test_inquiry_policies -v
가상 환자·병원과 고정 시각만 사용하며 운영 DB나 외부 API를 호출하지 않는다.
"""
import copy
import json
import threading

from server import db, inquiry_policies as policies, inquiry_workflow as workflow, service
from server.service import ServiceError
from test_inquiry_workflow import WorkflowBase
from test_server import EST, T0, base_patient


class PolicyBase(WorkflowBase):
    def start(self, pid='A', now=T0, enabled=True, policy=None):
        return workflow.start(self.conn, now, pid, enabled=enabled, policy_name=policy)

    def decision_rows(self, pid='A'):
        result = [dict(r) for r in self.conn.execute(
            'SELECT * FROM inquiry_policy_decisions WHERE patient_id=? ORDER BY id', (pid,))]
        for item in result:
            for key in ('hospital_ids', 'evidence'):
                if isinstance(item.get(key), str):
                    item[key] = json.loads(item[key])
        return result

    def expect_error(self, status, fn, *args, **kwargs):
        with self.assertRaises(ServiceError) as cm:
            fn(*args, **kwargs)
        self.assertEqual(cm.exception.status, status)
        return cm.exception


class PolicySelection(PolicyBase):
    def test_default_policy_preserves_three_hospital_wave(self):
        self.add_hospitals()
        self.patient(auto_inquiry=True)
        spec = self.job()['policy']
        self.assertEqual(spec['name'], 'fixed_3')
        self.assertTrue(spec['label'])
        self.assertTrue(spec['version'])
        self.run_worker()
        self.assertEqual(len(self.inquiries()), 3)

    def test_selected_fixed_policies_send_one_three_and_five_hospitals(self):
        self.add_hospitals()
        for name, batch_size in (('fixed_1', 1), ('fixed_3', 3), ('fixed_5', 5)):
            with self.subTest(policy=name):
                self.patient(name, auto_inquiry=True, inquiry_policy=name)
                self.assertEqual(self.job(name)['policy']['name'], name)
                self.run_worker()
                self.assertEqual(len(self.inquiries(name)), batch_size)
                self.assertEqual({q['state'] for q in self.inquiries(name)}, {'PENDING'})
                self.assertIsNone(service.patient_view(self.conn, name, T0)['destination_hospital_id'])

    def test_unknown_policy_create_and_start_have_no_side_effects(self):
        self.expect_error(400, self.patient, 'BAD', auto_inquiry=True, inquiry_policy='not-a-policy')
        self.assertIsNone(self.conn.execute("SELECT 1 FROM patients WHERE id='BAD'").fetchone())
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM events WHERE patient_id='BAD'").fetchone()[0], 0)
        self.patient()
        before = self.conn.execute('SELECT COUNT(*) FROM inquiry_workflows').fetchone()[0]
        events = self.conn.execute('SELECT COUNT(*) FROM events').fetchone()[0]
        self.expect_error(400, self.start, policy='not-a-policy')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM inquiry_workflows').fetchone()[0], before)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM events').fetchone()[0], events)

    def test_active_policy_change_is_rejected_and_same_policy_is_idempotent(self):
        self.patient(auto_inquiry=True, inquiry_policy='fixed_1')
        before = dict(self.conn.execute("SELECT * FROM inquiry_workflows WHERE patient_id='A'").fetchone())
        self.start(policy='fixed_1')
        self.assertEqual(dict(self.conn.execute("SELECT * FROM inquiry_workflows WHERE patient_id='A'").fetchone()), before)
        for enabled in (True, False):
            with self.subTest(enabled=enabled):
                error = self.expect_error(409, self.start, enabled=enabled, policy='fixed_5')
                self.assertEqual(error.code, 'POLICY_ACTIVE')
                self.assertEqual(dict(self.conn.execute("SELECT * FROM inquiry_workflows WHERE patient_id='A'").fetchone()), before)

    def test_frozen_policy_mismatch_rejects_explicit_same_name_atomically(self):
        for active in (True, False):
            for corrupted_field in ('policy_version', 'policy_config'):
                pid = f'freeze-{active}-{corrupted_field}'
                self.patient(pid, auto_inquiry=True, inquiry_policy='fixed_3')
                if not active:
                    self.start(pid=pid, enabled=False)
                corrupted_value = 'old' if corrupted_field == 'policy_version' else db.dumps({'name': 'tampered'})
                self.conn.execute(f'UPDATE inquiry_workflows SET {corrupted_field}=? WHERE patient_id=?',
                                  (corrupted_value, pid))
                before_job = tuple(self.conn.execute('SELECT * FROM inquiry_workflows WHERE patient_id=?', (pid,)).fetchone())
                before_events = [tuple(r) for r in self.conn.execute('SELECT * FROM events WHERE patient_id=? ORDER BY id', (pid,))]
                before_decisions = self.decision_rows(pid)
                for enabled in (True, False):
                    with self.subTest(active=active, field=corrupted_field, enabled=enabled):
                        error = self.expect_error(409, self.start, pid=pid, now=T0 + 2,
                                                  enabled=enabled, policy='fixed_3')
                        self.assertEqual(error.code, 'POLICY_VERSION_MISMATCH')
                        self.assertEqual(tuple(self.conn.execute('SELECT * FROM inquiry_workflows WHERE patient_id=?',
                                                                (pid,)).fetchone()), before_job)
                        self.assertEqual([tuple(r) for r in self.conn.execute('SELECT * FROM events WHERE patient_id=? ORDER BY id',
                                                                            (pid,))], before_events)
                        self.assertEqual(self.decision_rows(pid), before_decisions)

    def test_pause_switch_and_resume_preserve_contacts_and_restart_deadline(self):
        self.add_hospitals()
        self.patient(auto_inquiry=True, inquiry_policy='fixed_1')
        self.run_worker()
        first = self.inquiries()
        self.assertEqual(len(first), 1)
        self.start(now=T0 + 1, enabled=False)
        switched = self.start(now=T0 + 2, enabled=False, policy='fixed_5')
        self.assertEqual(switched['policy']['name'], 'fixed_5')
        self.assertEqual(switched['state'], 'PAUSED')
        self.assertFalse(switched['enabled'])
        resumed = self.start(now=T0 + 100, enabled=True)
        self.assertEqual(resumed['policy']['name'], 'fixed_5')
        self.assertEqual(resumed['deadline_at'], T0 + 100 + workflow.DEADLINE_SECONDS)
        self.run_worker(T0 + 100)
        self.assertEqual(len(self.inquiries()), 6)
        self.assertEqual(self.inquiries()[0]['id'], first[0]['id'])
        self.assertTrue(all(q['attempts'] == 1 for q in self.inquiries()))

    def test_selected_policy_and_decisions_survive_database_reinitialization(self):
        self.add_hospitals()
        self.patient(auto_inquiry=True, inquiry_policy='fixed_5')
        self.run_worker()
        decisions = self.decision_rows()
        inquiries = self.inquiries()
        conn = db.connect(self.path)
        try:
            db.init(conn)
            view = workflow.view(conn, T0 + 1, 'A')
            self.assertEqual(view['policy']['name'], 'fixed_5')
            self.assertEqual(view['latest_decision']['id'], decisions[-1]['id'])
        finally:
            conn.close()
        self.assertEqual(self.decision_rows(), decisions)
        self.assertEqual(self.inquiries(), inquiries)

    def test_legacy_workflow_without_policy_columns_migrates_to_fixed_three(self):
        self.add_hospitals()
        self.patient(auto_inquiry=True)
        self.run_worker()
        before = self.inquiries()
        self.conn.execute('ALTER TABLE inquiry_workflows DROP COLUMN policy_version')
        self.conn.execute('ALTER TABLE inquiry_workflows DROP COLUMN policy_name')
        db.init(self.conn)
        self.assertEqual(self.job()['policy']['name'], 'fixed_3')
        self.assertEqual(self.inquiries(), before)
        self.run_worker(T0 + 60)
        self.assertEqual(len(self.inquiries()), 6)

    def test_decision_log_matches_sent_contacts_and_repeated_tick_does_not_duplicate(self):
        self.add_hospitals()
        self.patient(auto_inquiry=True, inquiry_policy='fixed_3')
        self.run_worker()
        decisions = self.decision_rows()
        self.assertEqual(len(decisions), 1)
        decision = decisions[0]
        self.assertEqual(decision['policy_name'], 'fixed_3')
        self.assertEqual(decision['hospital_ids'], [q['hospital_id'] for q in self.inquiries()])
        self.assertTrue(decision['reason'])
        self.assertIsInstance(decision['evidence'], dict)
        for _ in range(3):
            self.run_worker()
            self.job()
        self.assertEqual(self.decision_rows(), decisions)
        self.assertEqual(self.job()['latest_decision']['id'], decision['id'])


class ObservedPolicy(PolicyBase):
    def test_past_hospital_reply_history_changes_actual_server_contact_order(self):
        for hid, delay in (('H1', 120), ('H2', 1)):
            for index in range(2):
                pid = f'history-{hid}-{index}'
                self.patient(pid, now=T0 - 310)
                service.send_inquiries(self.conn, T0 - 300, pid, [hid])
                service.respond(self.conn, T0 - 300 + delay, self.inquiry_id(pid, hid),
                                'decline', 'history-nurse', reason='병상')
        self.patient(auto_inquiry=True, inquiry_policy='observed_v2')
        class NearHospitals:
            def estimate(self, origin, target):
                result = dict(EST.estimate(origin, target))
                result['duration_seconds'] = {35.17: 60, 35.20: 70}.get(target[0], 600)
                return result
        self.run_worker(estimator=NearHospitals())
        self.assertEqual([q['hospital_id'] for q in self.inquiries()], ['H2'])
        evidence = self.job()['latest_decision']['evidence']
        self.assertEqual(evidence['global_reply_samples'], 4)

    def test_cold_start_sends_one_and_reviews_without_inventing_a_reply(self):
        self.add_hospitals()
        self.patient(auto_inquiry=True, inquiry_policy='observed_v2')
        self.run_worker()
        self.assertEqual(len(self.inquiries()), 1)
        self.assertEqual(self.job()['next_evaluation_at'], T0 + 15)
        self.run_worker(T0 + 14)
        self.assertEqual(len(self.inquiries()), 1)
        self.run_worker(T0 + 15)
        self.assertEqual(len(self.inquiries()), 1)
        self.assertEqual(self.inquiries()[0]['state'], 'PENDING')
        self.assertEqual(self.job(now=T0 + 15)['next_evaluation_at'], T0 + 30)

    def test_hedge_at_two_minutes_is_conditional_on_better_candidate_and_backlog(self):
        self.add_hospitals()
        self.patient(auto_inquiry=True, inquiry_policy='observed_v2')
        class NearbyAlternatives:
            def estimate(self, origin, target):
                result = dict(EST.estimate(origin, target))
                result['duration_seconds'] = 60 if target[0] == 35.17 else 70
                return result
        estimator = NearbyAlternatives()
        self.run_worker(estimator=estimator)
        first_hospital = self.inquiries()[0]['hospital_id']
        for index in range(5):
            pid = f'backlog-{index}'
            self.patient(pid, now=T0 + 1)
            service.send_inquiries(self.conn, T0 + 1, pid, [first_hospital])
        self.run_worker(T0 + 15, estimator=estimator)
        self.assertEqual(len(self.inquiries()), 1)
        self.run_worker(T0 + 120, estimator=estimator)
        self.assertEqual(len(self.inquiries()), 2)
        self.assertEqual(self.inquiries()[0]['state'], 'PENDING')
        self.assertNotEqual(self.inquiries()[1]['hospital_id'], first_hospital)
        self.assertEqual(self.job(now=T0 + 120)['latest_decision']['reason'], 'hedge_better_candidate')

    def test_no_response_timeout_does_not_mutate_hospital_inquiry(self):
        self.add_hospitals()
        self.patient(auto_inquiry=True, inquiry_policy='observed_v2')
        self.run_worker()
        first = self.inquiries()[0]
        self.run_worker(T0 + 181)
        old = next(q for q in self.inquiries() if q['id'] == first['id'])
        self.assertEqual((old['state'], old['attempts']), ('PENDING', 1))
        self.assertGreaterEqual(len(self.inquiries()), 2)
        self.assertEqual(service.patient_view(self.conn, 'A', T0 + 181)['state'], 'WAITING')

    def test_valid_acceptance_stops_requests_but_does_not_confirm_destination(self):
        self.add_hospitals()
        self.patient(auto_inquiry=True, inquiry_policy='observed_v2')
        self.run_worker()
        q = self.inquiries()[0]
        service.respond(self.conn, T0 + 1, q['id'], 'accept', 'nurse')
        self.run_worker(T0 + 15)
        self.run_worker(T0 + 120)
        self.assertEqual(len(self.inquiries()), 1)
        self.assertEqual(self.job(now=T0 + 120)['state'], 'AWAITING_CONFIRMATION')
        patient = service.patient_view(self.conn, 'A', T0 + 120)
        self.assertEqual(patient['state'], 'WAITING')
        self.assertIsNone(patient['destination_hospital_id'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM conversation_hospital_sessions').fetchone()[0], 0)

    def test_observed_hospital_history_changes_ranking_without_using_future_rows(self):
        now = T0
        candidates = [{'hospital_id': 'H1', 'travel': {'duration_seconds': 60}, 'resources_enough': True},
                      {'hospital_id': 'H2', 'travel': {'duration_seconds': 70}, 'resources_enough': True}]
        observations = [dict(hospital='H1', patient=f'old-H1-{i}', submitted_at=now - 300,
                             observed_at=now - 180, outcome='decline') for i in range(2)]
        observations += [dict(hospital='H2', patient=f'old-H2-{i}', submitted_at=now - 300,
                              observed_at=now - 299, outcome='accept') for i in range(2)]
        arguments = dict(now=now, candidates=candidates, observations=observations,
                         pending=[], patient_pending=[], max_new=3)
        cold = policies.decide('observed_v2', **dict(arguments, observations=[]))
        learned = policies.decide('observed_v2', **arguments)
        self.assertEqual(cold['send'], ['H1'])
        self.assertEqual(learned['send'], ['H2'])
        future = [dict(hospital=None, patient={'untrusted': 'future'}, submitted_at=now + 1,
                       observed_at=now + 2, outcome='invalid-future-outcome')]
        augmented = policies.decide('observed_v2', **dict(arguments, observations=observations + future))
        self.assertEqual(augmented, learned)
        before = copy.deepcopy(arguments)
        policies.decide('observed_v2', **arguments)
        self.assertEqual(arguments, before)


class PolicyConcurrency(PolicyBase):
    def test_policy_switch_during_candidate_calculation_discards_old_policy_result(self):
        self.add_hospitals()
        self.patient(auto_inquiry=True, inquiry_policy='fixed_1')
        entered, release = threading.Event(), threading.Event()
        errors = []
        class BlockingEstimator:
            def estimate(self, origin, target):
                entered.set()
                if not release.wait(timeout=10):
                    raise RuntimeError('이동시간 계산 대기 초과')
                return EST.estimate(origin, target)
        def calculate():
            try:
                self.run_worker(estimator=BlockingEstimator())
            except Exception as error:
                errors.append(error)
        thread = threading.Thread(target=calculate)
        thread.start()
        try:
            self.assertTrue(entered.wait(timeout=5))
            self.start(now=T0 + 1, enabled=False)
            self.start(now=T0 + 1, enabled=False, policy='fixed_5')
            self.start(now=T0 + 2, enabled=True)
        finally:
            release.set()
            thread.join(timeout=15)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(self.inquiries(), [])
        self.assertEqual(self.decision_rows(), [])
        self.run_worker(T0 + 2)
        self.assertEqual(len(self.inquiries()), 5)
        self.assertEqual(self.decision_rows()[-1]['policy_name'], 'fixed_5')

    def test_hospital_reply_during_new_wave_calculation_prevents_extra_requests(self):
        self.add_hospitals()
        self.patient(auto_inquiry=True, inquiry_policy='fixed_1')
        self.run_worker()
        first = self.inquiries()[0]
        before = len(self.decision_rows())
        entered, release = threading.Event(), threading.Event()
        errors = []
        class BlockingEstimator:
            def estimate(self, origin, target):
                entered.set()
                if not release.wait(timeout=10):
                    raise RuntimeError('이동시간 계산 대기 초과')
                return EST.estimate(origin, target)
        def calculate():
            try:
                self.run_worker(T0 + 60, estimator=BlockingEstimator())
            except Exception as error:
                errors.append(error)
        thread = threading.Thread(target=calculate)
        thread.start()
        try:
            self.assertTrue(entered.wait(timeout=5))
            service.respond(self.conn, T0 + 60, first['id'], 'accept', 'nurse')
        finally:
            release.set()
            thread.join(timeout=15)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(self.inquiries()), 1)
        self.assertEqual(len(self.decision_rows()), before)
        self.run_worker(T0 + 61)
        self.assertEqual(self.job(now=T0 + 61)['state'], 'AWAITING_CONFIRMATION')


class PolicyObservations(PolicyBase):
    def capture(self, now=T0):
        from server.inquiry_observations import capture
        return capture(self.conn, now, 'A')

    def test_final_decision_is_single_observation_after_checking_and_question(self):
        self.patient('old', now=T0 - 300)
        service.send_inquiries(self.conn, T0 - 290, 'old', ['H1'])
        iid = self.inquiry_id('old', 'H1')
        service.respond(self.conn, T0 - 280, iid, 'checking', 'nurse')
        service.respond(self.conn, T0 - 270, iid, 'question', 'nurse', question='현재 의식 상태?')
        service.answer_question(self.conn, T0 - 269, 'old', iid, '명료')
        service.respond(self.conn, T0 - 260, iid, 'accept', 'nurse')
        self.patient()
        before = self.inquiries('old')
        events = self.conn.execute('SELECT COUNT(*) FROM events').fetchone()[0]
        captured = self.capture()
        observations = [o for o in captured['observations'] if o['patient'] == 'old']
        self.assertEqual(len(observations), 1)
        self.assertEqual((observations[0]['outcome'], observations[0]['submitted_at'], observations[0]['observed_at']),
                         ('accept', T0 - 290, T0 - 260))
        self.assertNotIn('old', {p['patient'] for p in captured['pending']})
        self.assertNotIn('명료', json.dumps(captured, ensure_ascii=False))
        self.assertNotIn('의식 상태', json.dumps(captured, ensure_ascii=False))
        self.assertEqual(self.inquiries('old'), before)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM events').fetchone()[0], events)

    def test_future_acceptance_does_not_hide_current_pending_request(self):
        self.patient('future', now=T0 - 110)
        service.send_inquiries(self.conn, T0 - 100, 'future', ['H1'])
        service.respond(self.conn, T0 + 20, self.inquiry_id('future', 'H1'), 'accept', 'future-nurse')
        self.patient()
        self.assertEqual(self.inquiries('future')[0]['state'], 'ACCEPTED')
        captured = self.capture()
        self.assertEqual([o for o in captured['observations'] if o['patient'] == 'future'], [])
        self.assertEqual([p for p in captured['pending'] if p['patient'] == 'future'],
                         [dict(hospital='H1', patient='future', submitted_at=T0 - 100)])

    def test_timeout_observation_stays_timeout_after_late_actual_acceptance(self):
        self.patient('late', now=T0 - 310)
        service.send_inquiries(self.conn, T0 - 300, 'late', ['H1'])
        self.patient()
        before = self.capture()
        old = [o for o in before['observations'] if o['patient'] == 'late']
        self.assertEqual(len(old), 1)
        self.assertEqual((old[0]['outcome'], old[0]['observed_at']), ('timeout', T0 - 120))
        self.assertEqual(self.inquiries('late')[0]['state'], 'PENDING')
        service.respond(self.conn, T0 + 1, self.inquiry_id('late', 'H1'), 'accept', 'late-nurse')
        after = self.capture(T0 + 2)
        self.assertEqual([o for o in after['observations'] if o['patient'] == 'late'], old)
        self.assertNotIn('late', {p['patient'] for p in after['pending']})
        self.assertEqual(self.inquiries('late')[0]['state'], 'ACCEPTED')

    def test_early_cancel_and_retry_do_not_invent_timeout_or_duplicate_samples(self):
        for pid in ('cancel', 'retry'):
            self.patient(pid, now=T0 - 310)
            service.send_inquiries(self.conn, T0 - 300, pid, ['H1'])
        service.cancel_inquiry(self.conn, T0 - 250, 'cancel', self.inquiry_id('cancel', 'H1'), '증상 호전')
        service.retry_inquiry(self.conn, T0 - 179, 'retry', self.inquiry_id('retry', 'H1'))
        self.patient()
        captured = self.capture()
        self.assertEqual(captured['observations'], [])
        self.assertEqual(captured['pending'], [dict(hospital='H1', patient='retry', submitted_at=T0 - 179)])


class PolicyHttp(PolicyBase):
    def test_registry_selection_switch_and_decision_history_routes(self):
        self.add_hospitals()
        with self.http() as call:
            status, registry = call('GET', '/api/inquiry-policies')
            self.assertEqual(status, 200)
            self.assertEqual({p['name'] for p in registry['policies']},
                             {'fixed_1', 'fixed_3', 'fixed_5', 'observed_v2', 'observed_v3'})
            status, patient = call('POST', '/api/patients',
                                   dict(base_patient(), id='HTTP-POLICY', auto_inquiry=True, inquiry_policy='fixed_5'))
            self.assertEqual(status, 200)
            self.assertEqual(patient['inquiry_workflow']['policy']['name'], 'fixed_5')
            self.run_worker(T0 + 10)
            endpoint = '/api/patients/HTTP-POLICY/inquiry-workflow'
            self.assertEqual(call('POST', endpoint, {'enabled': True, 'policy_name': 'fixed_1'})[0], 409)
            self.assertEqual(call('POST', endpoint, {'enabled': False})[0], 200)
            self.assertEqual(call('POST', endpoint, {'enabled': False, 'policy_name': 'observed_v2'})[0], 200)
            self.assertEqual(call('POST', endpoint, {'enabled': True})[1]['policy']['name'], 'observed_v2')
            status, history = call('GET', '/api/patients/HTTP-POLICY/inquiry-decisions?limit=1')
            self.assertEqual(status, 200)
            self.assertEqual(history['patient_id'], 'HTTP-POLICY')
            self.assertEqual(len(history['decisions']), 1)
            self.assertIsInstance(history['decisions'][0]['hospital_ids'], list)
            self.assertIsInstance(history['decisions'][0]['evidence'], dict)
            self.assertEqual(call('GET', '/api/patients/MISSING/inquiry-decisions')[0], 404)
            for value in ('0', '101', '-1', 'true', 'bad'):
                self.assertEqual(call('GET', '/api/patients/HTTP-POLICY/inquiry-decisions?limit=' + value)[0], 400)

    def test_unknown_policy_http_create_leaves_no_patient(self):
        with self.http() as call:
            status, _ = call('POST', '/api/patients',
                             dict(base_patient(), id='HTTP-BAD-POLICY', auto_inquiry=True, inquiry_policy='bogus'))
            self.assertEqual(status, 400)
            self.assertEqual(call('GET', '/api/patients/HTTP-BAD-POLICY')[0], 404)


if __name__ == '__main__':
    import unittest
    unittest.main()
