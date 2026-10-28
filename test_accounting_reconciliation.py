"""수동 정산 확인 후 기록만 종결하는 계약. 실제 시연 DB와 화면은 사용하지 않는다."""
import json
import sqlite3
import threading
import unittest
from unittest.mock import patch

from server import accounting_reconciliation, db, service, transport_review
from test_arrival_accounting import ArrivalAccountingBase
from test_server import T0


class ReconciliationBase(ArrivalAccountingBase):
    def payload(self, request_id='manual-accounting-1', **changes):
        body = dict(request_id=request_id, reason='운영 집계에서 이미 정산한 기록입니다.',
                    handled_by='nurse-accounting',
                    expected_version=self.conn.execute(
                        "SELECT version FROM patients WHERE id='A'").fetchone()[0]
                    if self.conn.execute("SELECT 1 FROM patients WHERE id='A'").fetchone() else 1,
                    expected_resource_versions=self.resource_versions(), confirm_no_resource_release=True)
        body.update(changes)
        return body

    def resource_versions(self, hospital='H1'):
        return {row['resource_type']: row['version'] for row in self.conn.execute(
            'SELECT resource_type,version FROM resources WHERE hospital_id=? ORDER BY resource_type', (hospital,))}

    def reconcile_patient(self, body=None, pid='A', hospital='H1', now=T0 + 60, conn=None):
        return service.reconcile_patient_accounting(
            conn or self.conn, now, pid, hospital, body if body is not None else self.payload())

    def reconcile_external(self, eid, body=None, hospital='H1', now=T0 + 60, conn=None):
        return service.reconcile_external_arrival_accounting(
            conn or self.conn, now, hospital, eid, body if body is not None else self.payload(expected_version=1))

    def resource_snapshot(self):
        return [tuple(row) for row in self.conn.execute(
            'SELECT * FROM resources ORDER BY hospital_id,resource_type')]

    def record_snapshot(self):
        tables = ('patients', 'external_arrivals', 'events', 'planning_state',
                  'conversations', 'conversation_hospital_sessions', 'transport_reviews',
                  'accounting_reconciliations')
        return {table: [tuple(row) for row in self.conn.execute(f'SELECT * FROM {table} ORDER BY 1')]
                for table in tables}

    def error(self, code, function, *args, status=409, **kwargs):
        with self.assertRaises(service.ServiceError) as caught:
            function(*args, **kwargs)
        self.assertEqual((caught.exception.code, caught.exception.status), (code, status))
        return caught.exception

    def external(self, hospital='H1', request_id='registered-external'):
        return service.register_external_arrival(
            self.conn, T0 + 30, hospital, {'ed_bed': 1},
            request_id=request_id, actor='arrival-nurse')['external_arrival_id']

    def audit(self, rid):
        return dict(self.conn.execute('SELECT * FROM accounting_reconciliations WHERE id=?', (rid,)).fetchone())

    def assert_no_release(self, result, before):
        self.assertIs(result['resources_changed'], False)
        self.assertEqual(result['released_resource_demands'], {})
        self.assertEqual(self.resource_snapshot(), before)


class PatientReconciliation(ReconciliationBase):
    def test_manually_corrected_patient_closes_without_any_resource_write(self):
        self.arrived()
        service.set_resource(self.conn, T0 + 40, 'H1', 'ed_bed', 4, 0)
        body = self.payload()
        before = self.resource_snapshot()
        raw = self.conn.execute("SELECT arrival_accounting FROM patients WHERE id='A'").fetchone()[0]
        result = self.reconcile_patient(body)
        self.assertFalse(result['duplicate'])
        self.assert_no_release(result, before)
        target = self.conn.execute("SELECT * FROM patients WHERE id='A'").fetchone()
        self.assertEqual(target['state'], 'COMPLETED')
        self.assertEqual(target['version'], body['expected_version'] + 1)
        self.assertEqual(target['arrival_accounting'], raw)
        self.assertEqual(target['accounting_reconciled_at'], T0 + 60)
        self.assertEqual(target['accounting_reconciled_by'], body['handled_by'])
        audit = self.audit(target['accounting_reconciliation_id'])
        self.assertEqual((audit['hospital_id'], audit['target_kind'], audit['target_id']), ('H1', 'patient', 'A'))
        self.assertEqual((audit['prior_state'], audit['result_state']), ('ARRIVED', 'COMPLETED'))
        self.assertEqual((audit['prior_version'], audit['result_version']),
                         (body['expected_version'], body['expected_version'] + 1))
        self.assertEqual(audit['reason'], body['reason'])
        self.assertEqual(audit['handled_by'], body['handled_by'])
        self.assertEqual(json.loads(audit['original_accounting'])['arrival_accounting'], raw)
        self.assertNotIn('arrival_accounting', result['patient'])

    def test_null_or_damaged_patient_ledger_can_close_without_decoding(self):
        for raw in (None, 'not-json', '{"version":99}', '[]', b'\xff\x00invalid'):
            with self.subTest(raw=raw):
                pid = f'P{self.conn.execute("SELECT COUNT(*) FROM patients").fetchone()[0]}'
                service.set_resource(self.conn, T0, 'H1', 'ed_bed', 20, 0)
                self.arrived(pid)
                self.conn.execute('UPDATE patients SET arrival_accounting=? WHERE id=?', (raw, pid))
                version = self.conn.execute('SELECT version FROM patients WHERE id=?', (pid,)).fetchone()[0]
                before = self.resource_snapshot()
                result = self.reconcile_patient(self.payload(request_id=f'raw-{pid}', expected_version=version), pid=pid)
                self.assert_no_release(result, before)
                stored = self.conn.execute('SELECT state,arrival_accounting FROM patients WHERE id=?', (pid,)).fetchone()
                self.assertEqual((stored['state'], stored['arrival_accounting']), ('COMPLETED', raw))

    def test_reconciliation_does_not_unblock_by_changing_other_active_ledgers(self):
        self.arrived()
        service.set_resource(self.conn, T0 + 40, 'H1', 'ed_bed', 5, 0)
        self.arrived('B', now=T0 + 45)
        eid = self.external(request_id='mixed-after-correction')
        b_ledger = self.conn.execute("SELECT arrival_accounting FROM patients WHERE id='B'").fetchone()[0]
        e_ledger = self.conn.execute('SELECT applied_resource_demands,applied_resource_epochs FROM external_arrivals WHERE id=?',
                                     (eid,)).fetchone()
        before = self.resource_snapshot()
        self.reconcile_patient()
        self.assertEqual(self.resource_snapshot(), before)
        self.assertEqual(self.conn.execute("SELECT arrival_accounting FROM patients WHERE id='B'").fetchone()[0], b_ledger)
        self.assertEqual(tuple(self.conn.execute(
            'SELECT applied_resource_demands,applied_resource_epochs FROM external_arrivals WHERE id=?', (eid,)).fetchone()),
            tuple(e_ledger))
        service.discharge(self.conn, T0 + 70, 'B', 'H1')
        service.discharge_external_arrival(self.conn, T0 + 80, 'H1', eid)
        self.assertEqual(self.resource()['occupied'], 0)

    def test_patient_wrong_hospital_or_missing_patient_is_404_without_changes(self):
        self.arrived()
        before = self.record_snapshot(), self.resource_snapshot()
        for pid, hospital in (('A', 'H2'), ('A', 'missing'), ('missing', 'H1')):
            with self.subTest(pid=pid, hospital=hospital):
                self.error('NOT_FOUND', self.reconcile_patient, self.payload(), pid=pid, hospital=hospital, status=404)
                self.assertEqual((self.record_snapshot(), self.resource_snapshot()), before)

    def test_waiting_confirmed_transit_cancelled_and_normally_completed_are_rejected(self):
        for state in ('WAITING', 'CONFIRMED', 'IN_TRANSIT', 'CANCELLED', 'COMPLETED'):
            with self.subTest(state=state):
                pid = f'wrong-{state}'
                self.patient(pid)
                self.conn.execute('UPDATE patients SET state=?,destination_hospital_id=? WHERE id=?', (state, 'H1', pid))
                before = self.record_snapshot(), self.resource_snapshot()
                self.error('INVALID_STATE', self.reconcile_patient,
                           self.payload(request_id=pid, expected_version=1), pid=pid)
                self.assertEqual((self.record_snapshot(), self.resource_snapshot()), before)

    def test_stale_patient_version_rejects_before_audit_creation(self):
        self.arrived()
        before = self.record_snapshot(), self.resource_snapshot()
        self.error('CONFLICT', self.reconcile_patient, self.payload(expected_version=999))
        self.assertEqual((self.record_snapshot(), self.resource_snapshot()), before)

    def test_successful_close_does_not_expire_unrelated_acceptances(self):
        service.set_resource(self.conn, T0, 'H1', 'ed_bed', 5, 0)
        self.arrived()
        self.patient('UNRELATED')
        iid = self.accepted('UNRELATED', 'H2')
        before = tuple(self.conn.execute('SELECT * FROM inquiries WHERE id=?', (iid,)).fetchone())
        self.reconcile_patient(now=T0 + service.ACCEPT_TTL + 1000)
        self.assertEqual(tuple(self.conn.execute('SELECT * FROM inquiries WHERE id=?', (iid,)).fetchone()), before)

    def test_two_superseded_records_can_close_independently_without_aggregate_deadlock(self):
        service.set_resource(self.conn, T0, 'H1', 'ed_bed', 5, 0)
        self.arrived()
        self.arrived('B', now=T0 + 35)
        service.set_resource(self.conn, T0 + 40, 'H1', 'ed_bed', 5, 0)
        before = self.resource_snapshot()
        self.reconcile_patient()
        version = self.conn.execute("SELECT version FROM patients WHERE id='B'").fetchone()[0]
        self.reconcile_patient(self.payload(request_id='manual-close-B', expected_version=version), pid='B')
        self.assertEqual(self.resource_snapshot(), before)
        self.assertEqual([row[0] for row in self.conn.execute('SELECT state FROM patients ORDER BY id')],
                         ['COMPLETED', 'COMPLETED'])


class ExternalReconciliation(ReconciliationBase):
    def test_active_external_reconciles_without_release_and_preserves_raw_ledger(self):
        eid = self.external()
        body = self.payload(expected_version=1)
        before = self.resource_snapshot()
        raw = tuple(self.conn.execute(
            'SELECT applied_resource_demands,applied_resource_epochs FROM external_arrivals WHERE id=?', (eid,)).fetchone())
        result = self.reconcile_external(eid, body)
        self.assertFalse(result['duplicate'])
        self.assert_no_release(result, before)
        target = self.conn.execute('SELECT * FROM external_arrivals WHERE id=?', (eid,)).fetchone()
        self.assertEqual((target['state'], target['version']), ('RECONCILED', 2))
        self.assertEqual(target['accounting_reconciled_by'], body['handled_by'])
        self.assertEqual(target['accounting_reconciled_at'], T0 + 60)
        self.assertEqual((target['applied_resource_demands'], target['applied_resource_epochs']), raw)
        receipt = self.audit(target['accounting_reconciliation_id'])
        self.assertEqual((receipt['target_kind'], receipt['target_id'], receipt['prior_state'], receipt['result_state']),
                         ('external_arrival', str(eid), 'ACTIVE', 'RECONCILED'))

    def test_legacy_and_damaged_external_ledgers_can_close_without_decoding(self):
        for state, raw in (('LEGACY', '{}'), ('ACTIVE', 'not-json'), ('LEGACY', 'null'), ('ACTIVE', '[]'),
                           ('LEGACY', b'\xff\x00invalid')):
            with self.subTest(state=state, raw=raw):
                eid = self.external(request_id=f'legacy-{state}-{raw}')
                self.conn.execute('UPDATE external_arrivals SET state=?,applied_resource_demands=?,'
                                  'applied_resource_epochs=? WHERE id=?', (state, raw, raw, eid))
                before = self.resource_snapshot()
                result = self.reconcile_external(eid, self.payload(request_id=f'close-{eid}', expected_version=1))
                self.assert_no_release(result, before)
                target = self.conn.execute('SELECT * FROM external_arrivals WHERE id=?', (eid,)).fetchone()
                self.assertEqual((target['state'], target['applied_resource_demands'], target['applied_resource_epochs']),
                                 ('RECONCILED', raw, raw))

    def test_external_wrong_hospital_missing_and_discharged_reject_without_writes(self):
        eid = self.external()
        before = self.record_snapshot(), self.resource_snapshot()
        for hospital, ident in (('H2', eid), ('H1', eid + 999), ('missing', eid)):
            with self.subTest(hospital=hospital, ident=ident):
                self.error('NOT_FOUND', self.reconcile_external, ident, self.payload(), hospital=hospital, status=404)
                self.assertEqual((self.record_snapshot(), self.resource_snapshot()), before)
        service.discharge_external_arrival(self.conn, T0 + 40, 'H1', eid)
        before = self.record_snapshot(), self.resource_snapshot()
        self.error('INVALID_STATE', self.reconcile_external, eid, self.payload(expected_version=2))
        self.assertEqual((self.record_snapshot(), self.resource_snapshot()), before)

    def test_external_original_registration_retry_after_reconciliation_never_reoccupies(self):
        eid = self.external(request_id='original-registration-key')
        self.conn.execute("UPDATE external_arrivals SET applied_resource_demands='bad-json',"
                          "applied_resource_epochs='bad-json' WHERE id=?", (eid,))
        self.reconcile_external(eid, self.payload(request_id='reconciliation-key', expected_version=1))
        before = self.record_snapshot(), self.resource_snapshot()
        retry = service.register_external_arrival(
            self.conn, T0 + 100, 'H1', {'ed_bed': 1}, request_id='original-registration-key', actor='arrival-nurse')
        self.assertTrue(retry['duplicate'])
        self.assertEqual(retry['external_arrival_id'], eid)
        self.assertEqual(retry['external_arrival']['state'], 'RECONCILED')
        self.assertEqual((self.record_snapshot(), self.resource_snapshot()), before)

    def test_external_reconciled_filter_reads_without_changes(self):
        eid = self.external()
        self.reconcile_external(eid)
        before = self.record_snapshot(), self.resource_snapshot()
        result = service.list_external_arrivals(self.conn, T0 + 100, 'H1', state='RECONCILED')
        self.assertEqual([record['id'] for record in result['external_arrivals']], [eid])
        self.assertEqual(service.list_external_arrivals(self.conn, T0 + 100, 'H1')['external_arrivals'], [])
        self.assertEqual((self.record_snapshot(), self.resource_snapshot()), before)

    def test_stale_external_target_version_rejects_without_audit_or_resource_change(self):
        eid = self.external()
        before = self.record_snapshot(), self.resource_snapshot()
        self.error('CONFLICT', self.reconcile_external, eid, self.payload(expected_version=999))
        self.assertEqual((self.record_snapshot(), self.resource_snapshot()), before)


class ReconciliationValidation(ReconciliationBase):
    def setUp(self):
        super().setUp()
        self.arrived()

    def test_every_confirmation_and_audit_field_is_mandatory(self):
        body = self.payload()
        before = self.record_snapshot(), self.resource_snapshot()
        for key in body:
            with self.subTest(missing=key):
                invalid = {name: value for name, value in body.items() if name != key}
                self.error('INVALID', self.reconcile_patient, invalid, status=400)
                self.assertEqual((self.record_snapshot(), self.resource_snapshot()), before)

    def test_invalid_types_and_boundaries_are_rejected_without_writes(self):
        cases = {
            'request_id': (None, '', ' leading', 'trailing ', 'x' * 129, 5, True),
            'reason': (None, '', '   ', 'x' * 2001, [], False),
            'handled_by': (None, '', '   ', 'x' * 81, [], 4),
            'expected_version': (None, 0, -1, 1.5, '1', True, (1 << 63)),
            'expected_resource_versions': (None, [], 'ed_bed', {'ed_bed': 0}, {'ed_bed': -1},
                                           {'ed_bed': True}, {'ed_bed': '1'}, {'ed_bed': 1 << 63}),
            'confirm_no_resource_release': (None, False, 1, 'true', [], {}),
        }
        before = self.record_snapshot(), self.resource_snapshot()
        for key, values in cases.items():
            for value in values:
                with self.subTest(key=key, value=value):
                    self.error('INVALID', self.reconcile_patient, self.payload(**{key: value}), status=400)
                    self.assertEqual((self.record_snapshot(), self.resource_snapshot()), before)

    def test_body_must_be_object(self):
        before = self.record_snapshot(), self.resource_snapshot()
        for body in (None, False, 'text', [], 1):
            with self.subTest(body=body):
                self.error('INVALID', service.reconcile_patient_accounting,
                           self.conn, T0 + 60, 'A', 'H1', body, status=400)
                self.assertEqual((self.record_snapshot(), self.resource_snapshot()), before)

    def test_resource_versions_must_match_exact_current_hospital_set(self):
        body = self.payload()
        before = self.record_snapshot(), self.resource_snapshot()
        for versions in ({}, {'unknown_resource': 1}, {'ed_bed': body['expected_resource_versions']['ed_bed'] + 1},
                         {**body['expected_resource_versions'], 'foreign_resource': 1}):
            with self.subTest(versions=versions):
                self.error('CONFLICT', self.reconcile_patient, self.payload(expected_resource_versions=versions))
                self.assertEqual((self.record_snapshot(), self.resource_snapshot()), before)

    def test_resource_change_after_operator_read_requires_refresh(self):
        body = self.payload()
        service.set_resource(self.conn, T0 + 40, 'H1', 'ed_bed', 5, 0)
        before = self.record_snapshot(), self.resource_snapshot()
        self.error('CONFLICT', self.reconcile_patient, body)
        self.assertEqual((self.record_snapshot(), self.resource_snapshot()), before)
        self.reconcile_patient(self.payload())
        self.assertEqual(self.resource_snapshot(), before[1])

    def test_empty_resource_set_can_be_attested_explicitly_and_strings_trim(self):
        self.conn.execute("DELETE FROM resources WHERE hospital_id='H1'")
        body = self.payload(reason='  이미 집계에서 해제함  ', handled_by='  nurse  ', ignored='ignored')
        before = self.resource_snapshot()
        result = self.reconcile_patient(body)
        self.assert_no_release(result, before)
        audit = self.audit(result['reconciliation']['id'])
        self.assertEqual((audit['reason'], audit['handled_by']), ('이미 집계에서 해제함', 'nurse'))


class ReconciliationIdempotency(ReconciliationBase):
    def test_exact_patient_retry_after_resource_changes_is_readonly_duplicate(self):
        self.arrived()
        body = self.payload()
        first = self.reconcile_patient(body)
        service.set_resource(self.conn, T0 + 70, 'H1', 'ed_bed', 5, 3)
        before = self.record_snapshot(), self.resource_snapshot()
        second = self.reconcile_patient(body, now=T0 + 100)
        self.assertTrue(second['duplicate'])
        self.assertEqual(second['reconciliation']['id'], first['reconciliation']['id'])
        self.assert_no_release(second, before[1])
        self.assertEqual((self.record_snapshot(), self.resource_snapshot()), before)

    def test_exact_external_retry_after_resource_changes_is_readonly_duplicate(self):
        eid = self.external()
        body = self.payload(expected_version=1)
        first = self.reconcile_external(eid, body)
        service.set_resource(self.conn, T0 + 70, 'H1', 'ed_bed', 5, 3)
        before = self.record_snapshot(), self.resource_snapshot()
        second = self.reconcile_external(eid, body, now=T0 + 100)
        self.assertTrue(second['duplicate'])
        self.assertEqual(second['reconciliation']['id'], first['reconciliation']['id'])
        self.assertEqual((self.record_snapshot(), self.resource_snapshot()), before)

    def test_mismatched_retry_payload_is_conflict(self):
        self.arrived()
        body = self.payload()
        self.reconcile_patient(body)
        before = self.record_snapshot(), self.resource_snapshot()
        for change in ({'reason': '다른 이유'}, {'handled_by': 'other'}, {'expected_version': 999},
                       {'expected_resource_versions': {'ed_bed': 999}}):
            with self.subTest(change=change):
                self.error('CONFLICT', self.reconcile_patient, {**body, **change})
                self.assertEqual((self.record_snapshot(), self.resource_snapshot()), before)

    def test_same_hospital_request_key_cannot_close_another_target_or_target_kind(self):
        service.set_resource(self.conn, T0, 'H1', 'ed_bed', 5, 0)
        self.arrived()
        self.arrived('B', now=T0 + 35)
        eid = self.external()
        body = self.payload()
        self.reconcile_patient(body)
        before = self.record_snapshot(), self.resource_snapshot()
        self.error('CONFLICT', self.reconcile_patient, body, pid='B')
        self.error('CONFLICT', self.reconcile_external, eid, body)
        self.assertEqual((self.record_snapshot(), self.resource_snapshot()), before)

    def test_different_request_after_manual_close_is_invalid_state_and_readonly(self):
        self.arrived()
        self.reconcile_patient()
        before = self.record_snapshot(), self.resource_snapshot()
        self.error('INVALID_STATE', self.reconcile_patient, self.payload(request_id='different-close'))
        self.assertEqual((self.record_snapshot(), self.resource_snapshot()), before)

    def test_unknown_fields_do_not_break_same_request_retry(self):
        self.arrived()
        body = self.payload()
        self.reconcile_patient({**body, 'unrecognized': 'a'})
        before = self.record_snapshot(), self.resource_snapshot()
        retry = self.reconcile_patient({**body, 'unrecognized': 'b'})
        self.assertTrue(retry['duplicate'])
        self.assertEqual((self.record_snapshot(), self.resource_snapshot()), before)


class ReconciliationAtomicityAndAudit(ReconciliationBase):
    def test_patient_review_cleanup_failure_rolls_back_target_receipt_event_and_review(self):
        self.arrived()
        before = self.record_snapshot(), self.resource_snapshot()
        with patch('server.transport_review.finish_in_transaction', side_effect=RuntimeError('cleanup failed')):
            with self.assertRaisesRegex(RuntimeError, 'cleanup failed'):
                self.reconcile_patient()
        self.assertEqual((self.record_snapshot(), self.resource_snapshot()), before)

    def test_external_event_failure_rolls_back_target_receipt_and_event(self):
        eid = self.external()
        before = self.record_snapshot(), self.resource_snapshot()
        with patch('server.service.log', side_effect=RuntimeError('event failed')):
            with self.assertRaisesRegex(RuntimeError, 'event failed'):
                self.reconcile_external(eid)
        self.assertEqual((self.record_snapshot(), self.resource_snapshot()), before)

    def test_event_insert_failure_rolls_back_record_and_receipt(self):
        self.arrived()
        before = self.record_snapshot(), self.resource_snapshot()
        with patch('server.service.log', side_effect=RuntimeError('event failed')):
            with self.assertRaisesRegex(RuntimeError, 'event failed'):
                self.reconcile_patient()
        self.assertEqual((self.record_snapshot(), self.resource_snapshot()), before)

    def test_audit_receipt_cannot_be_updated_or_deleted(self):
        self.arrived()
        result = self.reconcile_patient()
        rid = result['reconciliation']['id']
        before = self.audit(rid)
        for sql in ('UPDATE accounting_reconciliations SET reason=? WHERE id=?',
                    'DELETE FROM accounting_reconciliations WHERE id=?'):
            with self.subTest(sql=sql):
                with self.assertRaises(sqlite3.IntegrityError):
                    self.conn.execute(sql, ('changed', rid) if sql.startswith('UPDATE') else (rid,))
                self.assertEqual(self.audit(rid), before)

    def test_insert_or_replace_cannot_overwrite_existing_audit_identity_or_request_key(self):
        self.arrived()
        rid = self.reconcile_patient()['reconciliation']['id']
        original = self.audit(rid)
        before = self.record_snapshot(), self.resource_snapshot()
        # SQLite REPLACE는 recursive_triggers가 꺼져 있으면 DELETE 트리거를 우회할 수 있다.
        self.assertEqual(self.conn.execute('PRAGMA recursive_triggers').fetchone()[0], 0)
        columns = list(original)
        for change in ({'reason': 'same-id-replacement'},
                       {'id': rid + 1000, 'reason': 'same-request-replacement'}):
            with self.subTest(change=change):
                replacement = {**original, **change}
                with self.assertRaises(sqlite3.IntegrityError):
                    self.conn.execute(
                        f'INSERT OR REPLACE INTO accounting_reconciliations ({",".join(columns)}) '
                        f'VALUES ({",".join("?" for _ in columns)})',
                        [replacement[column] for column in columns])
                self.assertEqual(self.audit(rid), original)
                self.assertEqual((self.record_snapshot(), self.resource_snapshot()), before)

    def test_closing_a_record_does_not_automatically_change_in_transit_destination(self):
        service.set_resource(self.conn, T0, 'H1', 'ed_bed', 3, 0)
        self.prepared('B')
        transport_review.enable(self.conn, T0 + 22, 'B', True)
        self.arrived()
        service.set_resource(self.conn, T0 + 40, 'H1', 'ed_bed', 3, 3)
        body = self.payload()
        before = self.resource_snapshot()
        self.reconcile_patient(body)
        self.assertEqual(self.resource_snapshot(), before)
        b = self.conn.execute("SELECT state,destination_hospital_id FROM patients WHERE id='B'").fetchone()
        self.assertEqual(tuple(b), ('IN_TRANSIT', 'H1'))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM inquiries WHERE patient_id='B'").fetchone()[0], 1)


class ReconciliationConcurrency(ReconciliationBase):
    def race(self, callback):
        barrier, results, errors = threading.Barrier(2), [], []

        def run():
            conn = db.connect(self.path)
            try:
                barrier.wait(timeout=5)
                results.append(callback(conn))
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
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        self.assertEqual(sorted(result['duplicate'] for result in results), [False, True])
        self.assertEqual(len({result['reconciliation']['id'] for result in results}), 1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM accounting_reconciliations').fetchone()[0], 1)

    def test_concurrent_patient_identical_retry_closes_once_without_release(self):
        self.arrived()
        body, before = self.payload(), self.resource_snapshot()
        self.race(lambda conn: self.reconcile_patient(body, conn=conn))
        self.assertEqual(self.resource_snapshot(), before)
        self.assertEqual(self.conn.execute("SELECT version FROM patients WHERE id='A'").fetchone()[0],
                         body['expected_version'] + 1)

    def test_concurrent_external_identical_retry_closes_once_without_release(self):
        eid = self.external()
        body, before = self.payload(expected_version=1), self.resource_snapshot()
        self.race(lambda conn: self.reconcile_external(eid, body, conn=conn))
        self.assertEqual(self.resource_snapshot(), before)
        self.assertEqual(self.conn.execute('SELECT version FROM external_arrivals WHERE id=?', (eid,)).fetchone()[0], 2)


class ReconciliationMigration(ReconciliationBase):
    def test_migration_adds_nullable_metadata_without_inventing_receipts_or_changing_resources(self):
        self.arrived()
        eid = self.external()
        for table in ('patients', 'external_arrivals'):
            for column in ('accounting_reconciliation_id', 'accounting_reconciled_at', 'accounting_reconciled_by'):
                self.conn.execute(f'ALTER TABLE {table} DROP COLUMN {column}')
        before_resources = self.resource_snapshot()
        before_events = [tuple(row) for row in self.conn.execute('SELECT * FROM events ORDER BY id')]
        db.init(self.conn)
        for table in ('patients', 'external_arrivals'):
            record = self.conn.execute(f'SELECT * FROM {table}').fetchone()
            self.assertIsNone(record['accounting_reconciliation_id'])
            self.assertIsNone(record['accounting_reconciled_at'])
            self.assertIsNone(record['accounting_reconciled_by'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM accounting_reconciliations').fetchone()[0], 0)
        self.assertEqual(self.resource_snapshot(), before_resources)
        self.assertEqual([tuple(row) for row in self.conn.execute('SELECT * FROM events ORDER BY id')], before_events)
        before = self.record_snapshot(), self.resource_snapshot()
        db.init(self.conn)
        self.assertEqual((self.record_snapshot(), self.resource_snapshot()), before)
        self.assertEqual(self.conn.execute('SELECT state FROM external_arrivals WHERE id=?', (eid,)).fetchone()[0], 'ACTIVE')

    def test_reinitialization_preserves_receipt_target_links_and_resource_snapshot(self):
        self.arrived()
        first = self.reconcile_patient()
        before = self.record_snapshot(), self.resource_snapshot()
        db.init(self.conn)
        self.assertEqual((self.record_snapshot(), self.resource_snapshot()), before)
        retry = self.reconcile_patient(self.payload(expected_version=first['reconciliation']['expected_version']))
        self.assertTrue(retry['duplicate'])


class ReconciliationReceipts(ReconciliationBase):
    def receipts(self):
        self.arrived()
        patient = self.reconcile_patient()['reconciliation']['id']
        one = self.external(request_id='receipt-external-one')
        first = self.reconcile_external(
            one, self.payload(request_id='receipt-close-one', expected_version=1))['reconciliation']['id']
        two = self.external(request_id='receipt-external-two')
        second = self.reconcile_external(
            two, self.payload(request_id='receipt-close-two', expected_version=1))['reconciliation']['id']
        other = self.external(hospital='H2', request_id='receipt-external-other')
        foreign = self.reconcile_external(
            other, self.payload(request_id='receipt-close-other', expected_version=1,
                                expected_resource_versions=self.resource_versions('H2')),
            hospital='H2')['reconciliation']['id']
        return patient, first, second, foreign

    def test_receipt_details_hide_internal_payload_raw_ledger_and_occupancy_epoch(self):
        patient, first, second, _ = self.receipts()
        before = self.record_snapshot(), self.resource_snapshot()
        for rid in (patient, first, second):
            with self.subTest(receipt=rid):
                result = accounting_reconciliation.view(self.conn, T0 + 100, 'H1', rid)['reconciliation']
                self.assertNotIn('request_payload', result)
                self.assertNotIn('original_accounting', result)
                self.assertFalse(result['resources_changed'])
                self.assertEqual(result['released_resource_demands'], {})
                self.assertTrue(result['resource_snapshot'])
                for resource in result['resource_snapshot']:
                    self.assertNotIn('occupancy_epoch', resource)
                    self.assertIn('version', resource)
                    self.assertIn('occupied', resource)
        self.assertEqual((self.record_snapshot(), self.resource_snapshot()), before)

    def test_receipt_pagination_and_kind_filter_are_scoped_and_readonly(self):
        patient, first, second, foreign = self.receipts()
        before = self.record_snapshot(), self.resource_snapshot()
        page = accounting_reconciliation.list_records(self.conn, T0 + 100, 'H1', limit=2)
        self.assertEqual([record['id'] for record in page['reconciliations']], [second, first])
        self.assertEqual(page['next_before_id'], first)
        next_page = accounting_reconciliation.list_records(
            self.conn, T0 + 100, 'H1', limit=2, before_id=page['next_before_id'])
        self.assertEqual([record['id'] for record in next_page['reconciliations']], [patient])
        self.assertIsNone(next_page['next_before_id'])
        for kind, ids in (('patient', [patient]), ('external_arrival', [second, first]), ('all', [second, first, patient])):
            result = accounting_reconciliation.list_records(self.conn, T0 + 100, 'H1', kind=kind)
            self.assertEqual([record['id'] for record in result['reconciliations']], ids)
            self.assertNotIn(foreign, [record['id'] for record in result['reconciliations']])
        self.assertEqual((self.record_snapshot(), self.resource_snapshot()), before)

    def test_receipt_wrong_hospital_missing_receipt_and_missing_hospital_are_404(self):
        patient, _, _, foreign = self.receipts()
        before = self.record_snapshot(), self.resource_snapshot()
        for hospital, rid in (('H2', patient), ('H1', foreign), ('H1', 999999), ('missing', patient)):
            with self.subTest(hospital=hospital, rid=rid):
                self.error('NOT_FOUND', accounting_reconciliation.view,
                           self.conn, T0 + 100, hospital, rid, status=404)
        self.error('NOT_FOUND', accounting_reconciliation.list_records,
                   self.conn, T0 + 100, 'missing', status=404)
        self.assertEqual((self.record_snapshot(), self.resource_snapshot()), before)

    def test_receipt_list_rejects_invalid_filters_and_bounds_without_writes(self):
        self.receipts()
        before = self.record_snapshot(), self.resource_snapshot()
        cases = [{'kind': 'invalid'}, {'kind': None}, {'kind': []},
                 *({'limit': value} for value in (0, -1, True, 201, '1.5', '1e2', None)),
                 *({'before_id': value} for value in (0, -1, True, 'x', '0', 1 << 63))]
        for change in cases:
            with self.subTest(change=change):
                self.error('INVALID', accounting_reconciliation.list_records,
                           self.conn, T0 + 100, 'H1', status=400, **change)
        self.assertEqual((self.record_snapshot(), self.resource_snapshot()), before)

    def test_empty_receipt_list_and_exact_full_page_have_no_extra_cursor(self):
        before = self.record_snapshot(), self.resource_snapshot()
        empty = accounting_reconciliation.list_records(self.conn, T0 + 100, 'H1')
        self.assertEqual(empty, {'reconciliations': [], 'next_before_id': None})
        self.assertEqual((self.record_snapshot(), self.resource_snapshot()), before)
        self.arrived()
        rid = self.reconcile_patient()['reconciliation']['id']
        before = self.record_snapshot(), self.resource_snapshot()
        result = accounting_reconciliation.list_records(self.conn, T0 + 100, 'H1', limit=1)
        self.assertEqual([record['id'] for record in result['reconciliations']], [rid])
        self.assertIsNone(result['next_before_id'])
        self.assertEqual((self.record_snapshot(), self.resource_snapshot()), before)


if __name__ == '__main__':
    unittest.main()
