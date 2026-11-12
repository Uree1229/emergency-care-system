"""일반 이송 환자의 실제 반영 점유만 해제하는 회귀 계약. 임시 DB만 사용한다."""
import threading
import unittest

from server import db, service, transport_review
from test_server import Base, EST, T0


class ArrivalAccountingBase(Base):
    def prepared(self, pid='A', hospital='H1', now=T0 + 20, in_transit=True, **fields):
        self.patient(pid, **fields)
        iid = self.accepted(pid, hospital, now=now - 10)
        service.confirm_destination(self.conn, now, pid, iid)
        if in_transit:
            service.depart(self.conn, now + 1, pid, EST)
        return service._patient(self.conn, pid)

    def arrived(self, pid='A', hospital='H1', now=T0 + 30, **fields):
        self.prepared(pid, hospital, now=now - 10, **fields)
        service.confirm_arrival(self.conn, now, pid, hospital)
        return service._patient(self.conn, pid)

    def resource(self, name='ed_bed', hospital='H1'):
        return db.row(self.conn.execute(
            'SELECT * FROM resources WHERE hospital_id=? AND resource_type=?',
            (hospital, name)).fetchone())

    def snapshot(self):
        # 업무 상태뿐 아니라 잠금 안의 사건·대화·배정 재계산 요청도 롤백돼야 한다.
        tables = ('resources', 'patients', 'external_arrivals', 'events', 'planning_state',
                  'conversations', 'conversation_hospital_sessions', 'transport_reviews')
        return {table: [tuple(row) for row in self.conn.execute(f'SELECT * FROM {table} ORDER BY 1')]
                for table in tables}

    def assert_error(self, code, function, *args, **kwargs):
        with self.assertRaises(service.ServiceError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(caught.exception.status, 409)
        return caught.exception


class ArrivalResourceLifecycle(ArrivalAccountingBase):
    def test_arrival_records_actual_resource_changes_and_discharge_releases_only_them(self):
        service.set_resource(self.conn, T0, 'H1', 'ed_bed', 8, 2)
        service.set_resource(self.conn, T0, 'H1', 'special_team', 3, 1)
        patient = self.arrived(resource_demands={'ed_bed': 2, 'special_team': 1})
        ledger = patient['arrival_accounting']
        self.assertEqual(ledger['version'], 1)
        self.assertEqual(ledger['hospital_id'], 'H1')
        self.assertEqual(ledger['applied_resource_demands'], {'ed_bed': 2, 'special_team': 1})
        self.assertEqual(ledger['applied_resource_epochs'], {
            key: self.resource(key)['occupancy_epoch'] for key in ('ed_bed', 'special_team')})
        self.assertNotIn('arrival_accounting', service.patient_view(self.conn, 'A', T0 + 31))
        self.assertNotIn('arrival_accounting', service.incoming(self.conn, T0 + 31, 'H1')[0])
        self.assertEqual(self.resource()['occupied'], 4)
        self.assertEqual(self.resource('special_team')['occupied'], 2)
        result = service.discharge(self.conn, T0 + 60, 'A', 'H1')
        self.assertEqual(result['state'], 'COMPLETED')
        self.assertEqual(self.resource()['occupied'], 2)
        self.assertEqual(self.resource('special_team')['occupied'], 1)

    def test_missing_at_arrival_resource_added_later_is_never_released(self):
        patient = self.arrived(resource_demands={'ed_bed': 1, 'new_equipment': 1})
        service.set_resource(self.conn, T0 + 40, 'H1', 'new_equipment', 4, 2)
        service.discharge(self.conn, T0 + 60, 'A', 'H1')
        self.assertEqual(self.resource()['occupied'], 0)
        self.assertEqual(self.resource('new_equipment')['occupied'], 2)
        self.assertEqual(patient['arrival_accounting']['applied_resource_demands'], {'ed_bed': 1})

    def test_no_known_resources_at_arrival_still_has_empty_managed_ledger(self):
        self.prepared()
        self.conn.execute("DELETE FROM resources WHERE hospital_id='H1'")
        service.confirm_arrival(self.conn, T0 + 30, 'A', 'H1')
        ledger = service._patient(self.conn, 'A')['arrival_accounting']
        self.assertEqual(ledger['applied_resource_demands'], {})
        self.assertEqual(ledger['applied_resource_epochs'], {})
        service.set_resource(self.conn, T0 + 40, 'H1', 'ed_bed', 4, 2)
        service.discharge(self.conn, T0 + 60, 'A', 'H1')
        self.assertEqual(self.resource()['occupied'], 2)

    def test_later_requirement_edits_do_not_rewrite_arrival_resource_ownership(self):
        self.arrived()
        service.set_resource(self.conn, T0 + 40, 'H1', 'special_team', 3, 2)
        self.conn.execute("UPDATE patients SET resource_demands=? WHERE id='A'",
                          (db.dumps({'ed_bed': 2, 'special_team': 1}),))
        service.discharge(self.conn, T0 + 60, 'A', 'H1')
        self.assertEqual(self.resource()['occupied'], 0)
        self.assertEqual(self.resource('special_team')['occupied'], 2)

    def test_two_ordinary_patients_and_external_arrival_release_their_own_counts(self):
        service.set_resource(self.conn, T0, 'H1', 'ed_bed', 6, 1)
        self.arrived('A')
        self.arrived('B', now=T0 + 40)
        eid = service.register_external_arrival(
            self.conn, T0 + 50, 'H1', {'ed_bed': 1}, request_id='mixed-arrival')['external_arrival_id']
        self.assertEqual(self.resource()['occupied'], 4)
        service.discharge(self.conn, T0 + 60, 'A', 'H1')
        self.assertEqual(self.resource()['occupied'], 3)
        service.discharge_external_arrival(self.conn, T0 + 70, 'H1', eid)
        self.assertEqual(self.resource()['occupied'], 2)
        service.discharge(self.conn, T0 + 80, 'B', 'H1')
        self.assertEqual(self.resource()['occupied'], 1)

    def test_capacity_and_status_only_update_preserves_valid_release(self):
        self.arrived()
        epoch = self.resource()['occupancy_epoch']
        service.set_resource(self.conn, T0 + 40, 'H1', 'ed_bed', 8, 1, status='unknown')
        self.assertEqual(self.resource()['occupancy_epoch'], epoch)
        service.discharge(self.conn, T0 + 60, 'A', 'H1')
        self.assertEqual(self.resource()['occupied'], 0)

    def test_unknown_status_resource_still_tracks_actual_arrival_and_release(self):
        service.set_resource(self.conn, T0, 'H1', 'ed_bed', 4, 2, status='unknown')
        patient = self.arrived()
        self.assertEqual(patient['arrival_accounting']['applied_resource_demands'], {'ed_bed': 1})
        self.assertEqual(self.resource()['occupied'], 3)
        service.discharge(self.conn, T0 + 60, 'A', 'H1')
        self.assertEqual(self.resource()['occupied'], 2)

    def test_arrival_from_confirmed_state_preserves_existing_endpoint_contract(self):
        self.prepared(in_transit=False)
        result = service.confirm_arrival(self.conn, T0 + 30, 'A', 'H1')
        self.assertEqual(result['state'], 'ARRIVED')
        self.assertEqual(self.resource()['occupied'], 1)
        service.discharge(self.conn, T0 + 60, 'A', 'H1')
        self.assertEqual(self.resource()['occupied'], 0)

    def test_arrival_and_discharge_retries_do_not_double_apply_resources(self):
        self.arrived()
        before = self.snapshot()
        self.assert_error('INVALID_STATE', service.confirm_arrival, self.conn, T0 + 40, 'A', 'H1')
        self.assertEqual(self.snapshot(), before)
        service.discharge(self.conn, T0 + 60, 'A', 'H1')
        before = self.snapshot()
        self.assert_error('INVALID_STATE', service.discharge, self.conn, T0 + 70, 'A', 'H1')
        self.assertEqual(self.snapshot(), before)


class ArrivalResourceConflicts(ArrivalAccountingBase):
    def test_arrival_overflow_rolls_back_patient_conversation_and_all_resource_changes(self):
        service.set_resource(self.conn, T0, 'H1', 'special_team', 2, 0)
        self.prepared(resource_demands={'ed_bed': 1, 'special_team': 1})
        self.conn.execute("UPDATE resources SET occupied=? WHERE hospital_id='H1' AND resource_type='special_team'",
                          ((1 << 63) - 1,))
        before = self.snapshot()
        self.assert_error('RESOURCE_CONFLICT', service.confirm_arrival, self.conn, T0 + 30, 'A', 'H1')
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.resource()['occupied'], 0)

    def test_manual_correction_cannot_release_external_patients_occupancy(self):
        self.arrived()
        service.set_resource(self.conn, T0 + 40, 'H1', 'ed_bed', 4, 0)
        service.register_external_arrival(self.conn, T0 + 50, 'H1', {'ed_bed': 1},
                                          request_id='new-person-after-correction')
        before = self.snapshot()
        self.assert_error('RESOURCE_CONFLICT', service.discharge, self.conn, T0 + 60, 'A', 'H1')
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.resource()['occupied'], 1)

    def test_manual_correction_blocks_release_even_with_sufficient_count(self):
        service.set_resource(self.conn, T0, 'H1', 'ed_bed', 8, 2)
        self.arrived()
        service.set_resource(self.conn, T0 + 40, 'H1', 'ed_bed', 8, 4)
        before = self.snapshot()
        self.assert_error('RESOURCE_CONFLICT', service.discharge, self.conn, T0 + 60, 'A', 'H1')
        self.assertEqual(self.snapshot(), before)

    def test_ordinary_release_checks_other_ordinary_and_external_managed_demand(self):
        service.set_resource(self.conn, T0, 'H1', 'ed_bed', 8, 0)
        self.arrived('A')
        self.arrived('B', now=T0 + 40)
        service.register_external_arrival(self.conn, T0 + 50, 'H1', {'ed_bed': 1},
                                          request_id='drift-external')
        # 구버전 집계 경로가 epoch를 바꾸지 않고 전체 점유만 낮춘 경우도 검출해야 한다.
        self.conn.execute("UPDATE resources SET occupied=2 WHERE hospital_id='H1' AND resource_type='ed_bed'")
        before = self.snapshot()
        self.assert_error('RESOURCE_CONFLICT', service.discharge, self.conn, T0 + 60, 'A', 'H1')
        self.assertEqual(self.snapshot(), before)

    def test_external_release_checks_ordinary_managed_demand_too(self):
        service.set_resource(self.conn, T0, 'H1', 'ed_bed', 4, 0)
        self.arrived()
        eid = service.register_external_arrival(self.conn, T0 + 40, 'H1', {'ed_bed': 1},
                                                request_id='ordinary-cross-ledger')['external_arrival_id']
        self.conn.execute("UPDATE resources SET occupied=1 WHERE hospital_id='H1' AND resource_type='ed_bed'")
        before = self.snapshot()
        self.assert_error('RESOURCE_CONFLICT', service.discharge_external_arrival,
                          self.conn, T0 + 60, 'H1', eid)
        self.assertEqual(self.snapshot(), before)

    def test_one_deleted_resource_rolls_back_entire_multiresource_discharge(self):
        service.set_resource(self.conn, T0, 'H1', 'special_team', 3, 0)
        self.arrived(resource_demands={'ed_bed': 1, 'special_team': 1})
        self.conn.execute("DELETE FROM resources WHERE hospital_id='H1' AND resource_type='special_team'")
        before = self.snapshot()
        self.assert_error('RESOURCE_CONFLICT', service.discharge, self.conn, T0 + 60, 'A', 'H1')
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.resource()['occupied'], 1)

    def test_resource_deleted_and_recreated_is_not_original_occupancy(self):
        self.arrived()
        old_epoch = self.resource()['occupancy_epoch']
        self.conn.execute("DELETE FROM resources WHERE hospital_id='H1' AND resource_type='ed_bed'")
        service.set_resource(self.conn, T0 + 40, 'H1', 'ed_bed', 4, 1)
        self.assertNotEqual(self.resource()['occupancy_epoch'], old_epoch)
        before = self.snapshot()
        self.assert_error('RESOURCE_CONFLICT', service.discharge, self.conn, T0 + 60, 'A', 'H1')
        self.assertEqual(self.snapshot(), before)

    def test_wrong_hospital_does_not_close_patient_or_modify_resources(self):
        self.arrived()
        before = self.snapshot()
        self.assert_error('INVALID_STATE', service.discharge, self.conn, T0 + 60, 'A', 'H2')
        self.assertEqual(self.snapshot(), before)

    def test_legacy_arrived_without_snapshot_does_not_guess_resource_release(self):
        self.arrived()
        self.conn.execute("UPDATE patients SET arrival_accounting=NULL WHERE id='A'")
        before = self.snapshot()
        self.assert_error('INVALID_STATE', service.discharge, self.conn, T0 + 60, 'A', 'H1')
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.resource()['occupied'], 1)

    def test_malformed_ledger_rejects_discharge_without_any_partial_write(self):
        patient = self.arrived()
        ledger = patient['arrival_accounting']
        malformed = [
            [],
            {**ledger, 'version': 2},
            {**ledger, 'hospital_id': 'H2'},
            {**ledger, 'applied_resource_demands': {'ed_bed': -1}},
            {**ledger, 'applied_resource_demands': {'ed_bed': True}},
            {**ledger, 'applied_resource_epochs': {}},
        ]
        for value in malformed:
            with self.subTest(ledger=value):
                self.conn.execute("UPDATE patients SET arrival_accounting=? WHERE id='A'", (db.dumps(value),))
                before = self.snapshot()
                self.assert_error('RESOURCE_CONFLICT', service.discharge, self.conn, T0 + 60, 'A', 'H1')
                self.assertEqual(self.snapshot(), before)

    def test_invalid_json_ledger_blocks_release_but_patient_and_incoming_reads_remain_available(self):
        self.arrived()
        self.conn.execute("UPDATE patients SET arrival_accounting='not-json' WHERE id='A'")
        before = self.snapshot()
        self.assert_error('RESOURCE_CONFLICT', service.discharge, self.conn, T0 + 60, 'A', 'H1')
        self.assertEqual(self.snapshot(), before)
        patient = service.patient_view(self.conn, 'A', T0 + 60)
        self.assertEqual(patient['state'], 'ARRIVED')
        self.assertNotIn('arrival_accounting', patient)
        incoming = service.incoming(self.conn, T0 + 60, 'H1')
        self.assertEqual([patient['id'] for patient in incoming], ['A'])
        self.assertNotIn('arrival_accounting', incoming[0])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.conn.execute(
            "SELECT arrival_accounting FROM patients WHERE id='A'").fetchone()[0], 'not-json')

    def test_multiresource_underflow_is_atomic(self):
        service.set_resource(self.conn, T0, 'H1', 'special_team', 2, 0)
        self.arrived(resource_demands={'ed_bed': 1, 'special_team': 1})
        self.conn.execute("UPDATE resources SET occupied=0 WHERE hospital_id='H1' AND resource_type='special_team'")
        before = self.snapshot()
        self.assert_error('RESOURCE_CONFLICT', service.discharge, self.conn, T0 + 60, 'A', 'H1')
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.resource()['occupied'], 1)


class ArrivalAccountingConcurrency(ArrivalAccountingBase):
    def concurrently(self, function, now):
        barrier, results, errors = threading.Barrier(2), [], []

        def run():
            conn = db.connect(self.path)
            try:
                barrier.wait(timeout=5)
                results.append(function(conn, now, 'A', 'H1'))
            except BaseException as error:
                errors.append(error)
            finally:
                conn.close()

        threads = [threading.Thread(target=run) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], service.ServiceError)
        self.assertEqual((errors[0].code, errors[0].status), ('INVALID_STATE', 409))

    def test_concurrent_arrival_applies_one_resource_increment_and_one_event(self):
        self.prepared()
        self.concurrently(service.confirm_arrival, T0 + 30)
        self.assertEqual(self.resource()['occupied'], 1)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM events WHERE patient_id='A' AND type='arrival_confirmed'").fetchone()[0], 1)
        self.assertEqual(service._patient(self.conn, 'A')['state'], 'ARRIVED')

    def test_concurrent_discharge_releases_one_resource_increment_and_one_event(self):
        self.arrived()
        self.concurrently(service.discharge, T0 + 60)
        self.assertEqual(self.resource()['occupied'], 0)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM events WHERE patient_id='A' AND type='discharged'").fetchone()[0], 1)
        self.assertEqual(service._patient(self.conn, 'A')['state'], 'COMPLETED')


class ArrivalAccountingMigration(ArrivalAccountingBase):
    def test_old_schema_migration_preserves_arrival_history_and_does_not_invent_snapshot(self):
        self.arrived()
        original = service._patient(self.conn, 'A')
        resources = [tuple(row) for row in self.conn.execute('SELECT * FROM resources ORDER BY 1,2')]
        events = [tuple(row) for row in self.conn.execute('SELECT * FROM events ORDER BY id')]
        self.conn.execute('ALTER TABLE patients DROP COLUMN arrival_accounting')
        db.init(self.conn)
        patient = service._patient(self.conn, 'A')
        self.assertIsNone(patient['arrival_accounting'])
        for key, value in original.items():
            if key != 'arrival_accounting':
                self.assertEqual(patient[key], value)
        self.assertEqual([tuple(row) for row in self.conn.execute('SELECT * FROM resources ORDER BY 1,2')], resources)
        self.assertEqual([tuple(row) for row in self.conn.execute('SELECT * FROM events ORDER BY id')], events)
        before = self.snapshot()
        db.init(self.conn)
        self.assertEqual(self.snapshot(), before)
        self.assert_error('INVALID_STATE', service.discharge, self.conn, T0 + 60, 'A', 'H1')
        self.assertEqual(self.snapshot(), before)

    def test_reinitialization_preserves_existing_new_arrival_ledger(self):
        patient = self.arrived()
        ledger = patient['arrival_accounting']
        before = self.snapshot()
        db.init(self.conn)
        self.assertEqual(service._patient(self.conn, 'A')['arrival_accounting'], ledger)
        self.assertEqual(self.snapshot(), before)
        service.discharge(self.conn, T0 + 60, 'A', 'H1')
        self.assertEqual(self.resource()['occupied'], 0)


class ArrivalAccountingTransportReview(ArrivalAccountingBase):
    def test_valid_discharge_rechecks_existing_in_transit_patient_without_changing_destination(self):
        service.set_resource(self.conn, T0, 'H1', 'ed_bed', 2, 0)
        self.prepared('B')
        transport_review.enable(self.conn, T0 + 22, 'B', True)
        self.arrived('A', now=T0 + 35)
        # 수용 확정 후 발생한 비관리 점유 1건 때문에 B의 수용 조건이 부족해진다.
        with db.transaction(self.conn):
            service._adjust_occupied(self.conn, T0 + 40, 'H1', {'ed_bed': 1}, +1)
            transport_review.scan_in_transaction(self.conn, T0 + 40, 'H1')
        self.assertTrue(transport_review.view(self.conn, T0 + 40, 'B')['risk_signature'])
        service.discharge(self.conn, T0 + 60, 'A', 'H1')
        review = transport_review.view(self.conn, T0 + 60, 'B')
        self.assertEqual(review['state'], 'MONITORING')
        self.assertFalse(review['risk_signature'])
        patient = service._patient(self.conn, 'B')
        self.assertEqual((patient['state'], patient['destination_hospital_id']), ('IN_TRANSIT', 'H1'))
        self.assertFalse(patient['needs_adjustment'])
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM inquiries WHERE patient_id='B'").fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()
