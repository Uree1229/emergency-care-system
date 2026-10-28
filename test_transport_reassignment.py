"""조정 요청 이후 자동 대체병원 문의와 사람의 목적지 변경 검증."""
import threading

from server import conversations, db, service, transport_reassignment as reassignment
from test_server import EST, T0
from test_transport_review import TransportBase


class PositionVersioning(TransportBase):
    def test_position_update_changes_location_version_only(self):
        self.setup_transport()
        before = service._patient(self.conn, 'A')
        service.update_transport_position(self.conn, T0 + 30, 'A', 35.18, 126.86)
        after = service._patient(self.conn, 'A')
        self.assertEqual(after['version'], before['version'])
        self.assertEqual(after['location_version'], before['location_version'] + 1)
        self.assertEqual(after['location_updated_at'], T0 + 30)
        self.assertEqual((after['lat'], after['lon']), (35.18, 126.86))
        self.assertEqual(after['destination_hospital_id'], 'H1')

    def test_position_repeat_is_idempotent_and_old_observation_is_rejected(self):
        self.setup_transport()
        service.update_transport_position(self.conn, T0 + 30, 'A', 35.18, 126.86, measured_at=T0 + 29)
        before = service._patient(self.conn, 'A')
        events = self.event_types()
        service.update_transport_position(self.conn, T0 + 31, 'A', 35.18, 126.86, measured_at=T0 + 29)
        self.assertEqual(service._patient(self.conn, 'A'), before)
        self.assertEqual(self.event_types(), events)
        self.assertEqual(self.code(service.update_transport_position, self.conn, T0 + 32,
                                   'A', 35.18, 126.86, measured_at=T0 + 28), 'STALE_LOCATION')
        self.assertEqual(self.code(service.update_transport_position, self.conn, T0 + 32,
                                   'A', 35.19, 126.86, measured_at=T0 + 29), 'LOCATION_CONFLICT')
        self.assertEqual(service._patient(self.conn, 'A'), before)

    def test_position_requires_active_transport_and_valid_complete_coordinate(self):
        self.patient()
        self.assertEqual(self.code(service.update_transport_position, self.conn, T0 + 1,
                                   'A', 35.18, 126.85), 'INVALID_STATE')
        self.setup_transport('B')
        for lat, lon in ((None, 126.85), (35.18, None), (float('nan'), 126.85), (91, 126.85)):
            with self.subTest(lat=lat, lon=lon):
                with self.assertRaises(service.ServiceError):
                    service.update_transport_position(self.conn, T0 + 30, 'B', lat, lon)
        self.assertEqual(self.code(service.update_transport_position, self.conn, T0 + 30,
                                   'B', 35.18, 126.85, measured_at=T0 + 31), 'INVALID')
        service.confirm_arrival(self.conn, T0 + 30, 'B', 'H1')
        self.assertEqual(self.code(service.update_transport_position, self.conn, T0 + 31,
                                   'B', 35.18, 126.85), 'INVALID_STATE')


class AlternativeBase(TransportBase):
    def worker(self, now=T0 + 32, estimator=EST):
        return reassignment.run_once(self.path, estimator, clock=lambda: now)

    def job(self, pid='A', now=T0 + 32):
        return reassignment.view(self.conn, now, pid)

    def open_alternatives(self, pid='A'):
        return [dict(row) for row in self.conn.execute(
            "SELECT * FROM inquiries WHERE patient_id=? AND hospital_id!='H1' "
            "AND state IN ('PENDING','CHECKING','ACCEPTED') ORDER BY id", (pid,))]

    def request_adjustment(self, pid='A', now=T0 + 31):
        service.set_resource(self.conn, now - 1, 'H1', 'ed_bed', 1, 1)
        self.scan(now - 1)
        self.decision('adjust', pid, now=now, note='필수 치료 자리 유지 불가')


class AlternativeWorkflow(AlternativeBase):
    def test_hospital_adjustment_starts_candidates_but_preserves_destination(self):
        self.add_alternatives()
        self.setup_transport(required_capabilities=['소아청소년과'])
        self.request_adjustment()
        self.worker()
        self.assertTrue(self.job()['enabled'])
        alternatives = self.open_alternatives()
        self.assertEqual(len(alternatives), 3)
        self.assertNotIn('H1', [q['hospital_id'] for q in alternatives])
        self.assertNotIn('H3', [q['hospital_id'] for q in alternatives])
        self.assertTrue(all(q['attempts'] == 1 for q in alternatives))
        patient = service._patient(self.conn, 'A')
        self.assertEqual(patient['destination_hospital_id'], 'H1')
        self.assertEqual(patient['state'], 'IN_TRANSIT')
        self.assertTrue(patient['needs_adjustment'])
        self.assertNotIn('destination_changed', self.event_types())

    def test_accepted_alternative_waits_for_human_destination_change(self):
        self.add_alternatives()
        self.setup_transport()
        self.request_adjustment()
        self.worker()
        inquiry = self.open_alternatives()[0]
        service.respond(self.conn, T0 + 33, inquiry['id'], 'accept', '대체병원 담당자')
        self.worker(T0 + 34)
        self.assertEqual(self.job(now=T0 + 34)['state'], 'AWAITING_CHANGE')
        self.assertEqual(service._patient(self.conn, 'A')['destination_hospital_id'], 'H1')
        self.assertEqual(conversations.get_conversation(self.conn, T0 + 34, 'A')
                         ['active_hospital_session']['hospital_id'], 'H1')
        changed = service.change_destination(self.conn, T0 + 35, 'A', inquiry['id'], EST,
                                               lat=35.18, lon=126.85)
        self.assertEqual(changed['destination_hospital_id'], inquiry['hospital_id'])
        self.assertFalse(changed['needs_adjustment'])
        self.assertFalse(self.job(now=T0 + 35)['enabled'])
        self.assertEqual(self.transport(now=T0 + 35)['state'], 'MONITORING')

    def test_keep_closes_episode_inquiries_and_prevents_automatic_resend(self):
        self.add_alternatives()
        self.setup_transport()
        self.request_adjustment()
        self.worker()
        inquiries = self.open_alternatives()
        service.respond(self.conn, T0 + 33, inquiries[0]['id'], 'accept', '대체병원 담당자')
        self.decision('keep', now=T0 + 34, note='기존 환자 치료 자원 확보')
        self.assertFalse(self.job(now=T0 + 34)['enabled'])
        self.assertEqual(self.open_alternatives(), [])
        after = [tuple(row) for row in self.conn.execute('SELECT * FROM inquiries ORDER BY id')]
        self.worker(T0 + 94)
        self.assertEqual([tuple(row) for row in self.conn.execute('SELECT * FROM inquiries ORDER BY id')], after)
        self.assertEqual(service._patient(self.conn, 'A')['destination_hospital_id'], 'H1')
        self.assertFalse(service._patient(self.conn, 'A')['needs_adjustment'])

    def test_manual_default_adjustment_does_not_create_automatic_inquiries(self):
        self.setup_transport(enabled=False)
        service.hospital_decision(self.conn, T0 + 30, 'A', 'H1', 'adjust', '필수 자원 부족')
        self.worker()
        self.assertFalse(self.job()['enabled'])
        self.assertEqual(self.open_alternatives(), [])
        self.assertTrue(service._patient(self.conn, 'A')['needs_adjustment'])

    def test_stale_position_waits_until_current_location_is_received(self):
        self.add_alternatives()
        self.setup_transport()
        self.request_adjustment(now=T0 + 160)
        self.worker(T0 + 161)
        self.assertEqual(self.open_alternatives(), [])
        self.assertEqual(self.job(now=T0 + 161)['state'], 'AWAITING_POSITION')
        service.update_transport_position(self.conn, T0 + 162, 'A', 35.18, 126.85)
        self.worker(T0 + 163)
        self.assertGreater(len(self.open_alternatives()), 0)
        self.assertEqual(service._patient(self.conn, 'A')['destination_hospital_id'], 'H1')

    def test_missing_position_waits_without_calling_estimator(self):
        self.add_alternatives()
        self.setup_transport()
        self.request_adjustment()
        self.conn.execute("UPDATE patients SET lat=NULL,lon=NULL,location_updated_at=NULL WHERE id='A'")

        class RejectEstimate:
            def estimate(self, *args):
                raise AssertionError('위치가 없으면 경로 계산도 시작하면 안 됩니다')

        self.worker(estimator=RejectEstimate())
        self.assertEqual(self.open_alternatives(), [])
        self.assertEqual(self.job()['state'], 'AWAITING_POSITION')

    def test_arrival_cancels_pending_alternatives_and_stops_worker(self):
        self.add_alternatives()
        self.setup_transport()
        self.request_adjustment()
        self.worker()
        service.confirm_arrival(self.conn, T0 + 33, 'A', 'H1')
        self.assertFalse(self.job(now=T0 + 33)['enabled'])
        self.assertEqual(self.open_alternatives(), [])
        before = self.event_types()
        self.worker(T0 + 34)
        self.assertEqual(self.event_types(), before)
        self.assertEqual(service._patient(self.conn, 'A')['state'], 'ARRIVED')

    def test_duplicate_worker_tick_does_not_repeat_episode_inquiries(self):
        self.add_alternatives()
        self.setup_transport()
        self.request_adjustment()
        self.worker()
        before = [tuple(r) for r in self.conn.execute('SELECT * FROM inquiries ORDER BY id')]
        events = self.event_types()
        self.worker()
        self.assertEqual([tuple(r) for r in self.conn.execute('SELECT * FROM inquiries ORDER BY id')], before)
        self.assertEqual(self.event_types(), events)

    def test_active_start_is_idempotent_and_does_not_reset_contact_budget(self):
        self.add_alternatives()
        self.setup_transport()
        self.request_adjustment()
        self.worker()
        before = self.job()
        events = self.event_types()
        with db.transaction(self.conn):
            reassignment.start_in_transaction(self.conn, T0 + 40, 'A', policy_name='fixed_3')
        self.assertEqual(self.job(now=T0 + 40), before)
        self.assertEqual(self.event_types(), events)
        with self.assertRaises(service.ServiceError) as different:
            with db.transaction(self.conn):
                reassignment.start_in_transaction(self.conn, T0 + 40, 'A', policy_name='fixed_5')
        self.assertEqual(different.exception.code, 'POLICY_ACTIVE')
        self.assertEqual(self.job(now=T0 + 40), before)

    def test_cancelled_initial_inquiry_is_reused_but_has_new_episode_budget(self):
        self.add_alternatives()
        self.patient()
        service.send_inquiries(self.conn, T0 + 1, 'A', ['H1', 'H2', 'H3', 'R1', 'R2', 'R3', 'R4', 'R5'])
        service.respond(self.conn, T0 + 10, self.inquiry_id('A', 'H1'), 'accept', '원병원')
        service.confirm_destination(self.conn, T0 + 20, 'A', self.inquiry_id('A', 'H1'))
        service.depart(self.conn, T0 + 21, 'A', EST)
        from server import transport_review
        transport_review.enable(self.conn, T0 + 22, 'A', True)
        old_id = self.inquiry_id('A', 'H2')
        self.request_adjustment()
        self.worker()
        job = self.job()
        self.assertEqual(job['contacted_count'], 3)
        self.assertIn('H2', job['contacted_hospital_ids'])
        self.assertEqual(self.inquiry_id('A', 'H2'), old_id)
        reused = service._inquiry(self.conn, old_id)
        self.assertEqual(reused['attempts'], 2)
        self.assertEqual(reused['state'], 'PENDING')
        self.assertEqual(reused['patient_version'], service._patient(self.conn, 'A')['version'])

    def test_deadline_stops_new_contacts_but_preserves_pending_and_late_valid_acceptance(self):
        self.add_alternatives()
        self.setup_transport()
        self.request_adjustment()
        self.worker()
        inquiry = self.open_alternatives()[0]
        service.update_transport_position(self.conn, T0 + 332, 'A', 35.18, 126.85)
        self.worker(T0 + 332)
        self.assertEqual(self.job(now=T0 + 332)['state'], 'EXHAUSTED')
        self.assertFalse(self.job(now=T0 + 332)['enabled'])
        self.assertEqual(service._inquiry(self.conn, inquiry['id'])['state'], 'PENDING')
        service.respond(self.conn, T0 + 333, inquiry['id'], 'accept', '늦게 확인한 병원')
        self.assertIn(inquiry['id'], self.job(now=T0 + 333)['valid_accepted_inquiry_ids'])
        self.assertEqual(service._patient(self.conn, 'A')['destination_hospital_id'], 'H1')
        service.change_destination(self.conn, T0 + 334, 'A', inquiry['id'], EST, lat=35.18, lon=126.85)
        self.assertEqual(service._patient(self.conn, 'A')['destination_hospital_id'], inquiry['hospital_id'])

    def test_old_hospital_cannot_read_messages_after_human_destination_change(self):
        self.add_alternatives()
        self.setup_transport()
        self.request_adjustment()
        self.worker()
        inquiry = self.open_alternatives()[0]
        service.respond(self.conn, T0 + 33, inquiry['id'], 'accept', '대체병원')
        service.change_destination(self.conn, T0 + 34, 'A', inquiry['id'], EST, lat=35.18, lon=126.85)
        session = conversations.get_conversation(self.conn, T0 + 35, 'A')['active_hospital_session']['id']
        conversations.send_message(self.conn, T0 + 35, 'A', dict(
            body='새 병원에게만 전달할 내용', client_message_id='new-route', hospital_session_id=session))
        old = conversations.get_conversation(self.conn, T0 + 36, 'A', hospital_id='H1')
        self.assertFalse(old['can_send'])
        self.assertNotIn('새 병원에게만 전달할 내용', [i.get('body') for i in old['items']])


class AlternativeCalculationRaces(AlternativeBase):
    def callback_estimator(self, callback):
        class MutatingEstimator:
            called = False

            def estimate(inner, origin, destination):
                if not inner.called:
                    inner.called = True
                    callback()
                return EST.estimate(origin, destination)

        return MutatingEstimator()

    def test_keep_during_external_travel_calculation_discards_old_candidates(self):
        self.add_alternatives()
        self.setup_transport()
        self.request_adjustment()
        self.worker(estimator=self.callback_estimator(lambda: self.decision('keep', now=T0 + 32)))
        self.assertEqual(self.open_alternatives(), [])
        self.assertFalse(self.job()['enabled'])
        self.assertFalse(service._patient(self.conn, 'A')['needs_adjustment'])
        self.assertEqual(service._patient(self.conn, 'A')['destination_hospital_id'], 'H1')

    def test_arrival_during_external_travel_calculation_cannot_send_new_inquiries(self):
        self.add_alternatives()
        self.setup_transport()
        self.request_adjustment()
        self.worker(estimator=self.callback_estimator(
            lambda: service.confirm_arrival(self.conn, T0 + 32, 'A', 'H1')))
        self.assertEqual(self.open_alternatives(), [])
        self.assertEqual(service._patient(self.conn, 'A')['state'], 'ARRIVED')
        self.assertFalse(self.job()['enabled'])

    def test_location_change_during_calculation_discards_result_then_recomputes(self):
        self.add_alternatives()
        self.setup_transport()
        self.request_adjustment()
        self.worker(estimator=self.callback_estimator(
            lambda: service.update_transport_position(self.conn, T0 + 32, 'A', 35.25, 126.85)))
        self.assertEqual(self.open_alternatives(), [])
        self.assertTrue(self.job()['enabled'])
        self.assertNotIn('transport_inquiry_batch', self.event_types())
        self.worker(T0 + 33)
        self.assertEqual(len(self.open_alternatives()), 3)

    def test_candidate_resource_change_discards_revision_then_recomputes(self):
        self.add_alternatives()
        self.setup_transport(required_capabilities=['소아청소년과'])
        self.request_adjustment()
        candidates = service.candidates(self.conn, T0 + 32, 'A', EST)['candidates']
        self.assertIn('H2', [c['hospital_id'] for c in candidates if c['auto_inquiry_eligible']])
        before_patient = service._patient(self.conn, 'A')
        before_review = self.transport()
        before_revision = self.conn.execute(
            'SELECT revision FROM planning_state WHERE id=1').fetchone()[0]

        # 다른 병원의 변화라 환자·위치·현재 목적지 검토 버전은 바뀌지 않는다.
        self.worker(estimator=self.callback_estimator(
            lambda: service.set_resource(self.conn, T0 + 32, 'H2', 'ed_bed', 2, 2)))

        after_patient = service._patient(self.conn, 'A')
        self.assertEqual(after_patient['version'], before_patient['version'])
        self.assertEqual(after_patient['location_version'], before_patient['location_version'])
        self.assertEqual(self.transport()['version'], before_review['version'])
        self.assertGreater(self.conn.execute(
            'SELECT revision FROM planning_state WHERE id=1').fetchone()[0], before_revision)
        self.assertEqual(self.open_alternatives(), [])
        self.assertTrue(self.job()['enabled'])
        self.assertNotIn('transport_inquiry_decision', self.event_types())
        self.assertNotIn('transport_inquiry_batch', self.event_types())

        self.worker(T0 + 33)
        alternatives = self.open_alternatives()
        self.assertEqual(len(alternatives), 3)
        self.assertNotIn('H2', [q['hospital_id'] for q in alternatives])
        self.assertEqual(service._patient(self.conn, 'A')['destination_hospital_id'], 'H1')
        self.assertEqual(self.event_types().count('transport_inquiry_batch'), 1)

    def test_two_workers_with_same_snapshot_send_only_one_wave(self):
        self.add_alternatives()
        self.setup_transport()
        self.request_adjustment()
        barrier = threading.Barrier(2)
        errors = []

        def run():
            try:
                self.worker(estimator=self.callback_estimator(lambda: barrier.wait(timeout=5)))
            except Exception as error:
                errors.append(error)

        threads = [threading.Thread(target=run) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(len(self.open_alternatives()), 3)
        self.assertEqual(self.event_types().count('transport_inquiry_batch'), 1)
        self.assertEqual(self.event_types().count('transport_inquiry_decision'), 1)
