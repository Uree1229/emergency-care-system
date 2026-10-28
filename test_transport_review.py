"""도착 전 수용 조건 검토. 가상 시각/임시 DB만 사용한다."""
from server import db, service, transport_review as review
from test_server import Base, EST, T0


class TransportBase(Base):
    def setup_transport(self, pid='A', hid='H1', now=T0 + 20, in_transit=True,
                        enabled=True, **fields):
        self.patient(pid, **fields)
        iid = self.accepted(pid, hid, now=now - 10)
        service.confirm_destination(self.conn, now, pid, iid)
        if in_transit:
            service.depart(self.conn, now + 1, pid, EST)
        if enabled:
            review.enable(self.conn, now + 2, pid, True)
        return service.patient_view(self.conn, pid, now + 2)

    def scan(self, now=T0 + 30, hid=None):
        with db.transaction(self.conn):
            return review.scan_in_transaction(self.conn, now, hid)

    def transport(self, pid='A', now=T0 + 30):
        return review.view(self.conn, now, pid)

    def event_types(self, pid='A'):
        return [r['type'] for r in self.conn.execute(
            'SELECT type FROM events WHERE patient_id=? ORDER BY id', (pid,))]

    def decision(self, decision, pid='A', hid='H1', now=T0 + 31, note='병원 재확인'):
        current = self.transport(pid, now)
        return service.hospital_decision(
            self.conn, now, pid, hid, decision, note,
            expected_review_version=current['version'],
            expected_risk_signature=current['risk_signature'])

    def add_alternatives(self):
        for index in range(1, 6):
            hid = f'R{index}'
            service.upsert_hospital(self.conn, dict(
                id=hid, name=hid, lat=35.21 + index * .01, lon=126.85,
                capabilities=['응급의학', '소아청소년과'], source='synthetic'))
            service.set_resource(self.conn, T0, hid, 'ed_bed', 8, 0)


class CommittedResourceReview(TransportBase):
    def test_no_new_patient_free_capacity_does_not_invalidate_existing_commitment(self):
        self.setup_transport()
        resource = service.resource_view(self.conn, 'H1', T0 + 25)[0]
        self.assertEqual((resource['free'], resource['expected']), (0, 1))
        self.scan()
        patient = service._patient(self.conn, 'A')
        self.assertEqual(patient['destination_hospital_id'], 'H1')
        self.assertFalse(patient['needs_adjustment'])
        self.assertFalse(self.transport()['risk_signature'])

    def test_specific_resource_loss_marks_only_patient_using_that_resource(self):
        service.set_resource(self.conn, T0, 'H1', 'ed_bed', 2, 0)
        service.set_resource(self.conn, T0, 'H1', 'special_team', 1, 0)
        self.setup_transport('A', resource_demands={'ed_bed': 1, 'special_team': 1})
        self.setup_transport('B', resource_demands={'ed_bed': 1})
        service.set_resource(self.conn, T0 + 30, 'H1', 'special_team', 1, 1)
        self.scan(T0 + 30)
        self.assertTrue(self.transport('A')['risk_signature'])
        self.assertFalse(self.transport('B')['risk_signature'])
        for pid in ('A', 'B'):
            self.assertFalse(service._patient(self.conn, pid)['needs_adjustment'])
            self.assertEqual(service._patient(self.conn, pid)['destination_hospital_id'], 'H1')

    def test_aggregate_shortage_marks_all_users_instead_of_arbitrary_eta_victim(self):
        service.set_resource(self.conn, T0, 'H1', 'ed_bed', 2, 0)
        self.setup_transport('A')
        self.setup_transport('B')
        service.register_external_arrival(self.conn, T0 + 30, 'H1', {'ed_bed': 1})
        self.scan(T0 + 30)
        self.assertTrue(self.transport('A')['risk_signature'])
        self.assertTrue(self.transport('B')['risk_signature'])
        self.assertFalse(service._patient(self.conn, 'A')['needs_adjustment'])
        self.assertFalse(service._patient(self.conn, 'B')['needs_adjustment'])

    def test_identical_repeated_scan_does_not_duplicate_review_events(self):
        self.setup_transport()
        service.set_resource(self.conn, T0 + 30, 'H1', 'ed_bed', 1, 1)
        self.scan(T0 + 30)
        first = self.event_types()
        first_signature = self.transport()['risk_signature']
        self.scan(T0 + 31)
        self.scan(T0 + 32)
        self.assertEqual(self.event_types(), first)
        self.assertEqual(self.transport()['risk_signature'], first_signature)

    def test_capability_change_flags_only_patient_requiring_that_capability(self):
        service.set_resource(self.conn, T0, 'H1', 'ed_bed', 2, 0)
        self.setup_transport('A', required_capabilities=['소아청소년과'])
        self.setup_transport('B', required_capabilities=['응급의학'])
        service.upsert_hospital(self.conn, dict(id='H1', name='H1', lat=35.17, lon=126.85,
                                                capabilities=['응급의학'], source='synthetic'))
        self.scan(T0 + 30)
        self.assertTrue(self.transport('A')['risk_signature'])
        self.assertFalse(self.transport('B')['risk_signature'])

    def test_resource_becoming_unknown_flags_recheck_without_marking_adjustment(self):
        self.setup_transport()
        service.set_resource(self.conn, T0 + 30, 'H1', 'ed_bed', 1, 0, status='unknown')
        self.scan(T0 + 30)
        self.assertTrue(self.transport()['risk_signature'])
        self.assertFalse(service._patient(self.conn, 'A')['needs_adjustment'])

    def test_unrelated_resource_update_does_not_change_patient_review_version(self):
        self.setup_transport()
        before = self.transport()
        service.set_resource(self.conn, T0 + 30, 'H1', 'irrelevant_equipment', 1, 1)
        self.assertEqual(self.transport()['version'], before['version'])
        self.assertEqual(self.transport()['risk_signature'], before['risk_signature'])

    def test_reading_review_does_not_generate_new_reviews_or_inquiries(self):
        self.setup_transport()
        events = self.event_types()
        inquiries = [tuple(r) for r in self.conn.execute('SELECT * FROM inquiries')]
        for _ in range(3):
            self.transport()
        self.assertEqual(self.event_types(), events)
        self.assertEqual([tuple(r) for r in self.conn.execute('SELECT * FROM inquiries')], inquiries)


class TimeAndRestrictionReview(TransportBase):
    def test_scheduled_restriction_activates_without_new_database_write(self):
        self.setup_transport(required_capabilities=['소아청소년과'])
        service.add_restriction(self.conn, T0 + 25, 'H1', '소아청소년과',
                                '전문의 교대', starts_at=T0 + 60, ends_at=T0 + 120)
        self.scan(T0 + 59)
        self.assertFalse(self.transport(now=T0 + 59)['risk_signature'])
        self.scan(T0 + 60)
        self.assertTrue(self.transport(now=T0 + 60)['risk_signature'])
        self.assertFalse(service._patient(self.conn, 'A')['needs_adjustment'])

    def test_unrelated_capability_restriction_does_not_flag_patient(self):
        self.setup_transport(required_capabilities=['소아청소년과'])
        service.add_restriction(self.conn, T0 + 30, 'H1', '심장내과', '교대')
        self.scan(T0 + 30)
        self.assertFalse(self.transport()['risk_signature'])

    def test_fresh_resource_becoming_stale_requests_reconfirmation(self):
        self.setup_transport()
        self.scan(T0 + service.STALE_AFTER)
        self.assertFalse(self.transport(now=T0 + service.STALE_AFTER)['risk_signature'])
        self.scan(T0 + service.STALE_AFTER + 1)
        self.assertTrue(self.transport(now=T0 + service.STALE_AFTER + 1)['risk_signature'])
        self.assertFalse(service._patient(self.conn, 'A')['needs_adjustment'])

    def test_unknown_resource_at_acceptance_is_not_repeatedly_new_failure(self):
        service.set_resource(self.conn, T0, 'H1', 'ed_bed', 1, 0, status='unknown')
        self.setup_transport()
        self.scan(T0 + 30)
        self.assertFalse(self.transport()['risk_signature'])


class ReviewLifecycle(TransportBase):
    def test_manual_confirmation_saves_treatment_basis_without_starting_monitor(self):
        self.setup_transport(enabled=False)
        patient = service._patient(self.conn, 'A')
        self.assertTrue(patient['transport_baseline'])
        self.assertEqual(self.transport()['state'], 'NOT_STARTED')
        self.assertFalse(self.transport()['enabled'])
        self.assertIsNone(self.conn.execute(
            "SELECT * FROM transport_reviews WHERE patient_id='A'").fetchone())
        self.assertIsNone(self.conn.execute(
            "SELECT * FROM transport_inquiry_workflows WHERE patient_id='A'").fetchone())
        self.assertNotIn('transport_baseline_captured', self.event_types())
        self.assertFalse(patient['needs_adjustment'])

    def test_first_enable_compares_against_confirmation_not_already_changed_resource(self):
        self.setup_transport(enabled=False)
        service.set_resource(self.conn, T0 + 30, 'H1', 'ed_bed', 1, 0, status='unknown')
        self.assertEqual(self.transport()['state'], 'NOT_STARTED')
        review.enable(self.conn, T0 + 31, 'A', True)
        current = self.transport(now=T0 + 31)
        self.assertEqual(current['state'], 'RECHECK_REQUIRED')
        self.assertTrue(current['risk_signature'])
        self.assertEqual(current['baseline']['resources']['ed_bed']['status'], 'available')
        self.assertEqual([issue['kind'] for issue in current['evidence']['issues']], ['resource_unknown'])
        self.assertFalse(service._patient(self.conn, 'A')['needs_adjustment'])
        self.assertEqual(service._patient(self.conn, 'A')['destination_hospital_id'], 'H1')

    def test_waiting_patient_cannot_enable_post_confirmation_monitoring(self):
        self.patient()
        self.assertEqual(self.code(review.enable, self.conn, T0 + 1, 'A', True), 'INVALID_STATE')

    def test_manual_default_has_no_automatic_alternative_inquiries(self):
        self.setup_transport(enabled=False)
        service.set_resource(self.conn, T0 + 30, 'H1', 'ed_bed', 1, 1)
        self.scan(T0 + 30)
        self.assertFalse(self.transport()['enabled'])
        self.assertEqual(len(service.patient_view(self.conn, 'A', T0 + 30)['inquiries']), 1)
        self.assertEqual(service._patient(self.conn, 'A')['destination_hospital_id'], 'H1')

    def test_keep_acknowledges_same_risk_and_preserves_route(self):
        self.setup_transport()
        service.set_resource(self.conn, T0 + 30, 'H1', 'ed_bed', 1, 1)
        self.scan(T0 + 30)
        signature = self.transport()['risk_signature']
        self.assertTrue(signature)
        self.decision('keep', note='도착 전 별도 자리 확보')
        before = self.event_types()
        self.scan(T0 + 32)
        self.assertEqual(self.transport()['acknowledged_signature'], signature)
        self.assertEqual(self.event_types(), before)
        self.assertFalse(service._patient(self.conn, 'A')['needs_adjustment'])
        self.assertEqual(service._patient(self.conn, 'A')['destination_hospital_id'], 'H1')

    def test_enabled_review_requires_the_displayed_version_and_signature(self):
        self.setup_transport()
        service.set_resource(self.conn, T0 + 30, 'H1', 'ed_bed', 1, 1)
        self.scan(T0 + 30)
        before = service._patient(self.conn, 'A')
        with self.assertRaises(service.ServiceError) as missing:
            service.hospital_decision(self.conn, T0 + 31, 'A', 'H1', 'adjust', '자리 부족')
        self.assertEqual(missing.exception.status, 409)
        self.assertEqual(service._patient(self.conn, 'A'), before)
        current = self.transport()
        with self.assertRaises(service.ServiceError) as stale:
            service.hospital_decision(
                self.conn, T0 + 31, 'A', 'H1', 'adjust', '자리 부족',
                expected_review_version=current['version'] - 1,
                expected_risk_signature=current['risk_signature'])
        self.assertEqual(stale.exception.status, 409)
        self.assertEqual(service._patient(self.conn, 'A'), before)
        with self.assertRaises(service.ServiceError) as signature:
            service.hospital_decision(
                self.conn, T0 + 31, 'A', 'H1', 'adjust', '자리 부족',
                expected_review_version=current['version'], expected_risk_signature='obsolete')
        self.assertEqual(signature.exception.status, 409)
        self.assertEqual(service._patient(self.conn, 'A'), before)

    def test_same_hospital_new_risk_cannot_use_previously_displayed_review(self):
        self.setup_transport(required_capabilities=['소아청소년과'])
        service.set_resource(self.conn, T0 + 30, 'H1', 'ed_bed', 1, 1)
        self.scan(T0 + 30)
        stale = self.transport()
        service.add_restriction(self.conn, T0 + 31, 'H1', '소아청소년과', '전문의 부재')
        self.scan(T0 + 31)
        before = service._patient(self.conn, 'A')
        with self.assertRaises(service.ServiceError) as rejected:
            service.hospital_decision(
                self.conn, T0 + 32, 'A', 'H1', 'keep', '이전 자료 확인',
                expected_review_version=stale['version'], expected_risk_signature=stale['risk_signature'])
        self.assertEqual(rejected.exception.status, 409)
        self.assertEqual(service._patient(self.conn, 'A'), before)

    def test_restored_condition_then_repeated_shortage_requires_new_hospital_check(self):
        self.setup_transport()
        service.set_resource(self.conn, T0 + 30, 'H1', 'ed_bed', 1, 1)
        self.decision('keep', now=T0 + 31)
        signature = self.transport()['risk_signature']
        self.assertEqual(self.transport()['acknowledged_signature'], signature)
        service.set_resource(self.conn, T0 + 32, 'H1', 'ed_bed', 1, 0)
        self.assertFalse(self.transport(now=T0 + 32)['risk_signature'])
        self.assertIsNone(self.transport(now=T0 + 32)['acknowledged_signature'])
        service.set_resource(self.conn, T0 + 33, 'H1', 'ed_bed', 1, 1)
        self.assertEqual(self.transport(now=T0 + 33)['risk_signature'], signature)
        self.assertEqual(self.transport(now=T0 + 33)['state'], 'RECHECK_REQUIRED')

    def test_resume_after_manual_destination_change_captures_new_hospital_baseline(self):
        self.setup_transport()
        review.enable(self.conn, T0 + 25, 'A', False)
        service.hospital_decision(self.conn, T0 + 26, 'A', 'H1', 'adjust', '도착 전 유지 불가')
        iid = self.accepted('A', 'H2', now=T0 + 27)
        service.change_destination(self.conn, T0 + 33, 'A', iid, EST, lat=35.18, lon=126.85)
        self.assertFalse(self.transport(now=T0 + 33)['enabled'])
        review.enable(self.conn, T0 + 34, 'A', True)
        resumed = self.transport(now=T0 + 34)
        self.assertEqual(resumed['hospital_id'], 'H2')
        self.assertEqual(resumed['state'], 'MONITORING')
        self.assertFalse(resumed['risk_signature'])
        self.assertEqual(resumed['baseline']['resources']['ed_bed']['capacity'], 2)

    def test_manual_destination_change_saves_new_basis_before_monitor_is_enabled(self):
        self.setup_transport(enabled=False)
        first_basis = service._patient(self.conn, 'A')['transport_baseline']
        service.hospital_decision(self.conn, T0 + 26, 'A', 'H1', 'adjust', '도착 전 유지 불가')
        iid = self.accepted('A', 'H2', now=T0 + 27)
        service.change_destination(self.conn, T0 + 33, 'A', iid, EST, lat=35.18, lon=126.85)
        saved = service._patient(self.conn, 'A')['transport_baseline']
        self.assertNotEqual(saved, first_basis)
        self.assertEqual(self.transport(now=T0 + 33)['state'], 'NOT_STARTED')
        service.set_resource(self.conn, T0 + 34, 'H2', 'ed_bed', 2, 0, status='unknown')
        review.enable(self.conn, T0 + 35, 'A', True)
        current = self.transport(now=T0 + 35)
        self.assertEqual(current['hospital_id'], 'H2')
        self.assertEqual(current['state'], 'RECHECK_REQUIRED')
        self.assertEqual(current['baseline']['resources']['ed_bed']['status'], 'available')
        self.assertEqual(current['baseline']['resources']['ed_bed']['capacity'], 2)

    def test_arrival_ends_review_and_cannot_restart(self):
        self.setup_transport()
        service.confirm_arrival(self.conn, T0 + 30, 'A', 'H1')
        state = self.transport()['state']
        self.assertFalse(self.transport()['enabled'])
        self.scan(T0 + 31)
        self.assertEqual(self.transport()['state'], state)
        self.assertEqual(self.code(review.enable, self.conn, T0 + 31, 'A', True), 'INVALID_STATE')

    def test_cancel_ends_review_and_cannot_restart(self):
        self.setup_transport()
        service.cancel_patient(self.conn, T0 + 30, 'A', '119 이송')
        self.assertFalse(self.transport()['enabled'])
        self.assertEqual(self.code(review.enable, self.conn, T0 + 31, 'A', True), 'INVALID_STATE')
