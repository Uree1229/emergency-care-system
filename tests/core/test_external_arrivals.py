"""외부 유입 기록의 수명·재전송·자원 점유 계약. 실제 시연 DB는 사용하지 않는다."""
import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from server import db, service, transport_review
from server.app import make_handler
from test_server import Base, EST, T0


class ExternalArrivalBase(Base):
    def register(self, hospital='H1', now=T0 + 30, **kw):
        demands = kw.pop('demands', {'ed_bed': 1})
        return service.register_external_arrival(self.conn, now, hospital, demands, **kw)

    def stored(self, eid):
        return db.row(self.conn.execute('SELECT * FROM external_arrivals WHERE id=?', (eid,)).fetchone())

    def resource(self, hospital='H1', name='ed_bed'):
        return db.row(self.conn.execute('SELECT * FROM resources WHERE hospital_id=? AND resource_type=?',
                                        (hospital, name)).fetchone())

    def snapshot(self):
        return {table: [tuple(r) for r in self.conn.execute(f'SELECT * FROM {table} ORDER BY 1')]
                for table in ('resources', 'external_arrivals', 'events', 'planning_state', 'transport_reviews')}

    def assert_service_error(self, code, fn, *args, status=None, **kw):
        with self.assertRaises(service.ServiceError) as caught:
            fn(*args, **kw)
        self.assertEqual(caught.exception.code, code)
        if status is not None:
            self.assertEqual(caught.exception.status, status)
        return caught.exception


class ExternalArrivalLifecycle(ExternalArrivalBase):
    def test_register_then_discharge_releases_only_its_recorded_occupancy(self):
        service.set_resource(self.conn, T0, 'H1', 'ed_bed', 4, 2)
        result = self.register(note='도보 내원', actor='nurse-a', request_id='arrival-one')
        eid = result['external_arrival_id']
        record = self.stored(eid)
        self.assertFalse(result['duplicate'])
        self.assertEqual(record['state'], 'ACTIVE')
        self.assertEqual(record['resource_demands'], {'ed_bed': 1})
        self.assertEqual(record['occurred_at'], T0 + 30)
        self.assertEqual(record['observed_at'], T0 + 30)
        self.assertEqual(record['registered_by'], 'nurse-a')
        self.assertNotIn('request_payload', result['external_arrival'])
        self.assertNotIn('applied_resource_epochs', result['external_arrival'])
        self.assertEqual(self.resource()['occupied'], 3)

        result = service.discharge_external_arrival(self.conn, T0 + 90, 'H1', eid,
                                                    actor='nurse-b', expected_version=record['version'])
        self.assertFalse(result['duplicate'])
        closed = self.stored(eid)
        self.assertEqual(closed['state'], 'DISCHARGED')
        self.assertEqual(closed['discharged_at'], T0 + 90)
        self.assertEqual(closed['discharged_by'], 'nurse-b')
        self.assertEqual(closed['version'], record['version'] + 1)
        self.assertEqual(self.resource()['occupied'], 2)

    def test_legacy_call_without_request_id_still_registers_distinct_arrivals(self):
        a, b = self.register(), self.register(now=T0 + 31)
        self.assertNotEqual(a['external_arrival_id'], b['external_arrival_id'])
        self.assertEqual(self.resource()['occupied'], 2)

    def test_repeated_discharge_is_idempotent_even_with_original_version(self):
        eid = self.register(request_id='close-twice')['external_arrival_id']
        version = self.stored(eid)['version']
        service.discharge_external_arrival(self.conn, T0 + 60, 'H1', eid,
                                           expected_version=version)
        before = self.snapshot()
        result = service.discharge_external_arrival(self.conn, T0 + 120, 'H1', eid,
                                                    expected_version=version)
        self.assertTrue(result['duplicate'])
        self.assertEqual(self.snapshot(), before)

    def test_discharge_wrong_hospital_or_unknown_id_does_not_modify_anything(self):
        eid = self.register(request_id='scope-one')['external_arrival_id']
        before = self.snapshot()
        for hospital, external_id in [('H2', eid), ('H1', eid + 999)]:
            self.assert_service_error('NOT_FOUND', service.discharge_external_arrival,
                                      self.conn, T0 + 60, hospital, external_id, status=404)
        self.assertEqual(self.snapshot(), before)

    def test_wrong_expected_version_preserves_active_record_and_occupancy(self):
        eid = self.register(request_id='stale-close')['external_arrival_id']
        before = self.snapshot()
        self.assert_service_error('CONFLICT', service.discharge_external_arrival,
                                  self.conn, T0 + 60, 'H1', eid, expected_version=999, status=409)
        self.assertEqual(self.snapshot(), before)

    def test_multiresource_underflow_rejects_entire_discharge(self):
        service.set_resource(self.conn, T0, 'H1', 'special_team', 2, 0)
        eid = self.register(demands={'ed_bed': 1, 'special_team': 1},
                            request_id='multi-resource')['external_arrival_id']
        # 외부 시스템에서 실제 집계를 이미 수정한 경우 이 기록만 근거로 다른 점유를 빼면 안 된다.
        service.set_resource(self.conn, T0 + 40, 'H1', 'special_team', 2, 0)
        before = self.snapshot()
        with self.assertRaises(service.ServiceError) as caught:
            service.discharge_external_arrival(self.conn, T0 + 60, 'H1', eid)
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.resource()['occupied'], 1)
        self.assertEqual(self.stored(eid)['state'], 'ACTIVE')

    def test_discharge_does_not_deduct_resource_added_after_registration(self):
        eid = self.register(demands={'ed_bed': 1, 'new_equipment': 1},
                            request_id='missing-at-arrival')['external_arrival_id']
        self.assertEqual(self.stored(eid)['applied_resource_demands'], {'ed_bed': 1})
        self.assertIsNone(self.resource(name='new_equipment'))
        service.set_resource(self.conn, T0 + 40, 'H1', 'new_equipment', 3, 2)
        service.discharge_external_arrival(self.conn, T0 + 60, 'H1', eid)
        self.assertEqual(self.resource()['occupied'], 0)
        self.assertEqual(self.resource(name='new_equipment')['occupied'], 2)

    def test_unknown_status_existing_resource_records_actual_occupancy_and_can_release(self):
        service.set_resource(self.conn, T0, 'H1', 'ed_bed', 4, 2, status='unknown')
        eid = self.register(request_id='unknown-status')['external_arrival_id']
        self.assertEqual(self.resource()['occupied'], 3)
        self.assertEqual(self.stored(eid)['applied_resource_demands'], {'ed_bed': 1})
        service.discharge_external_arrival(self.conn, T0 + 60, 'H1', eid)
        self.assertEqual(self.resource()['occupied'], 2)

    def test_manual_occupancy_correction_blocks_release_even_when_count_is_large_enough(self):
        service.set_resource(self.conn, T0, 'H1', 'ed_bed', 10, 2)
        eid = self.register(request_id='correction-guard')['external_arrival_id']
        service.set_resource(self.conn, T0 + 40, 'H1', 'ed_bed', 10, 4)
        before = self.snapshot()
        self.assert_service_error('RESOURCE_CONFLICT', service.discharge_external_arrival,
                                  self.conn, T0 + 60, 'H1', eid, status=409)
        self.assertEqual(self.snapshot(), before)

    def test_capacity_only_update_does_not_prevent_legitimate_release(self):
        eid = self.register(request_id='capacity-only')['external_arrival_id']
        epoch = self.resource()['occupancy_epoch']
        service.set_resource(self.conn, T0 + 40, 'H1', 'ed_bed', 8, 1, status='limited')
        self.assertEqual(self.resource()['occupancy_epoch'], epoch)
        service.discharge_external_arrival(self.conn, T0 + 60, 'H1', eid)
        self.assertEqual(self.resource()['occupied'], 0)

    def test_deleted_applied_resource_blocks_entire_release(self):
        service.set_resource(self.conn, T0, 'H1', 'special_team', 2, 0)
        eid = self.register(demands={'ed_bed': 1, 'special_team': 1},
                            request_id='deleted-resource')['external_arrival_id']
        self.conn.execute("DELETE FROM resources WHERE hospital_id='H1' AND resource_type='special_team'")
        before = self.snapshot()
        self.assert_service_error('RESOURCE_CONFLICT', service.discharge_external_arrival,
                                  self.conn, T0 + 60, 'H1', eid, status=409)
        self.assertEqual(self.snapshot(), before)

    def test_legacy_aggregate_drift_cannot_release_another_active_external_arrival(self):
        first = self.register(request_id='ledger-first')['external_arrival_id']
        self.register(now=T0 + 31, request_id='ledger-second')
        with db.transaction(self.conn):
            # 기존 환자 집계 경로가 epoch를 바꾸지 않고 전체 점유를 낮춘 상황을 재현한다.
            service._adjust_occupied(self.conn, T0 + 40, 'H1', {'ed_bed': 1}, -1)
        self.assertEqual(self.resource()['occupied'], 1)
        before = self.snapshot()
        self.assert_service_error('RESOURCE_CONFLICT', service.discharge_external_arrival,
                                  self.conn, T0 + 60, 'H1', first, status=409)
        self.assertEqual(self.snapshot(), before)

    def test_concurrent_discharge_releases_occupancy_once(self):
        eid = self.register(request_id='concurrent-close')['external_arrival_id']
        barrier, results, errors = threading.Barrier(2), [], []

        def discharge():
            conn = db.connect(self.path)
            try:
                barrier.wait(timeout=5)
                results.append(service.discharge_external_arrival(
                    conn, T0 + 60, 'H1', eid, expected_version=1))
            except BaseException as error:
                errors.append(error)
            finally:
                conn.close()

        workers = [threading.Thread(target=discharge) for _ in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=10)
        self.assertTrue(all(not worker.is_alive() for worker in workers))
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        self.assertEqual(sorted(r['duplicate'] for r in results), [False, True])
        self.assertEqual(self.resource()['occupied'], 0)
        self.assertEqual(self.resource()['version'], 3)
        self.assertEqual(self.stored(eid)['version'], 2)


class ExternalArrivalIdempotency(ExternalArrivalBase):
    def test_retry_uses_original_implicit_timestamp_without_any_new_write(self):
        result = self.register(note='직접 내원', actor='nurse-a', request_id='retry-one')
        before = self.snapshot()
        retry = self.register(now=T0 + 120, note='직접 내원', actor='nurse-b', request_id='retry-one')
        self.assertTrue(retry['duplicate'])
        self.assertEqual(retry['external_arrival_id'], result['external_arrival_id'])
        self.assertEqual(self.snapshot(), before)

    def test_dictionary_key_order_is_not_a_payload_conflict(self):
        service.set_resource(self.conn, T0, 'H1', 'special_team', 2, 0)
        original = self.register(demands={'ed_bed': 1, 'special_team': 1},
                                 request_id='canonical-order', occurred_at=T0 + 20)
        before = self.snapshot()
        retry = self.register(now=T0 + 60, demands={'special_team': 1, 'ed_bed': 1},
                              request_id='canonical-order', occurred_at=T0 + 20)
        self.assertTrue(retry['duplicate'])
        self.assertEqual(retry['external_arrival_id'], original['external_arrival_id'])
        self.assertEqual(self.snapshot(), before)

    def test_same_token_changed_clinical_fields_conflicts(self):
        self.register(request_id='same-token', note='메모', occurred_at=T0 + 20)
        before = self.snapshot()
        for kw in [dict(demands={'ed_bed': 2}), dict(note='다른 메모'), dict(occurred_at=T0 + 21)]:
            fields = dict(request_id='same-token', note='메모', occurred_at=T0 + 20)
            fields.update(kw)
            with self.subTest(fields=fields):
                with self.assertRaises(service.ServiceError) as caught:
                    self.register(now=T0 + 60, **fields)
                self.assertEqual(caught.exception.status, 409)
                self.assertEqual(self.snapshot(), before)

    def test_retry_after_discharge_returns_old_record_without_reoccupying(self):
        original = self.register(request_id='retry-closed', note='동일 요청')
        eid = original['external_arrival_id']
        service.discharge_external_arrival(self.conn, T0 + 60, 'H1', eid)
        before = self.snapshot()
        retry = self.register(now=T0 + 120, request_id='retry-closed', note='동일 요청')
        self.assertTrue(retry['duplicate'])
        self.assertEqual(retry['external_arrival_id'], eid)
        self.assertEqual(self.stored(eid)['state'], 'DISCHARGED')
        self.assertEqual(self.snapshot(), before)

    def test_same_request_id_is_scoped_to_hospital(self):
        a = self.register(request_id='hospital-scoped')
        b = self.register(hospital='H2', request_id='hospital-scoped')
        self.assertNotEqual(a['external_arrival_id'], b['external_arrival_id'])
        self.assertFalse(b['duplicate'])
        self.assertEqual(self.resource('H1')['occupied'], 1)
        self.assertEqual(self.resource('H2')['occupied'], 1)

    def test_concurrent_same_request_id_registers_once(self):
        barrier, results, errors = threading.Barrier(2), [], []

        def register():
            conn = db.connect(self.path)
            try:
                barrier.wait(timeout=5)
                results.append(service.register_external_arrival(
                    conn, T0 + 30, 'H1', {'ed_bed': 1}, request_id='concurrent-arrival'))
            except BaseException as error:
                errors.append(error)
            finally:
                conn.close()

        workers = [threading.Thread(target=register) for _ in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=10)
        self.assertTrue(all(not worker.is_alive() for worker in workers))
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        self.assertEqual({r['external_arrival_id'] for r in results}, {results[0]['external_arrival_id']})
        self.assertEqual(sorted(r['duplicate'] for r in results), [False, True])
        self.assertEqual(self.resource()['occupied'], 1)
        self.assertEqual(self.resource()['version'], 2)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM external_arrivals').fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM events WHERE type='external_arrival'").fetchone()[0], 1)

    def test_invalid_demands_do_not_create_records(self):
        before = self.snapshot()
        for demands in ({}, [], {'ed_bed': 0}, {'ed_bed': -1}, {'ed_bed': True}, {'ed_bed': 1.0}):
            with self.subTest(demands=demands):
                self.assert_service_error('INVALID', self.register, demands=demands, request_id='bad-demands')
        self.assertEqual(self.snapshot(), before)


class ExternalArrivalMigration(ExternalArrivalBase):
    def test_original_rows_become_legacy_and_cannot_release_historical_aggregate(self):
        # 기존 6열 테이블을 그대로 재현하고 새 init을 적용한다.
        self.conn.execute('DROP TABLE external_arrivals')
        self.conn.execute('CREATE TABLE external_arrivals (id INTEGER PRIMARY KEY AUTOINCREMENT, '
                          'hospital_id TEXT NOT NULL REFERENCES hospitals(id), occurred_at INTEGER NOT NULL, '
                          'observed_at INTEGER NOT NULL, resource_demands TEXT NOT NULL, note TEXT)')
        eid = self.conn.execute('INSERT INTO external_arrivals '
                                '(hospital_id,occurred_at,observed_at,resource_demands,note) '
                                'VALUES (?,?,?,?,?)', ('H1', T0 - 100, T0 - 90, '{"ed_bed":1}', '과거 기록')).lastrowid
        service.set_resource(self.conn, T0, 'H1', 'ed_bed', 4, 2)
        original_resources = [tuple(r) for r in self.conn.execute('SELECT * FROM resources ORDER BY 1,2')]
        db.init(self.conn)
        self.assertEqual(self.stored(eid)['state'], 'LEGACY')
        self.assertEqual(self.stored(eid)['note'], '과거 기록')
        self.assertEqual([tuple(r) for r in self.conn.execute('SELECT * FROM resources ORDER BY 1,2')],
                         original_resources)
        before = self.snapshot()
        db.init(self.conn)
        self.assertEqual(self.snapshot(), before)
        with self.assertRaises(service.ServiceError) as caught:
            service.discharge_external_arrival(self.conn, T0 + 60, 'H1', eid)
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(self.snapshot(), before)


class ExternalArrivalQueries(ExternalArrivalBase):
    def test_hospital_scoped_state_filtered_cursor_pagination(self):
        ids = [self.register(request_id=f'list-{n}')['external_arrival_id'] for n in range(4)]
        other = self.register(hospital='H2', request_id='list-other')['external_arrival_id']
        service.discharge_external_arrival(self.conn, T0 + 60, 'H1', ids[1])
        before = self.snapshot()
        page = service.list_external_arrivals(self.conn, T0 + 70, 'H1', limit=2)
        self.assertEqual([r['id'] for r in page['external_arrivals']], [ids[3], ids[2]])
        self.assertEqual(page['next_before_id'], ids[2])
        second = service.list_external_arrivals(self.conn, T0 + 70, 'H1', limit=2,
                                               before_id=page['next_before_id'])
        self.assertEqual([r['id'] for r in second['external_arrivals']], [ids[0]])
        self.assertIsNone(second['next_before_id'])
        closed = service.list_external_arrivals(self.conn, T0 + 70, 'H1', state='DISCHARGED')
        self.assertEqual([r['id'] for r in closed['external_arrivals']], [ids[1]])
        all_rows = service.list_external_arrivals(self.conn, T0 + 70, 'H1', state='all')
        self.assertEqual([r['id'] for r in all_rows['external_arrivals']], list(reversed(ids)))
        self.assertNotIn(other, [r['id'] for r in all_rows['external_arrivals']])
        self.assertEqual(service.list_external_arrivals(self.conn, T0 + 70, 'H1', state='LEGACY')
                         ['external_arrivals'], [])
        detail = service.external_arrival_view(self.conn, T0 + 70, 'H1', ids[0])
        self.assertEqual(detail['external_arrival']['resource_demands'], {'ed_bed': 1})
        self.assertNotIn('request_payload', detail['external_arrival'])
        self.assertNotIn('applied_resource_epochs', detail['external_arrival'])
        self.assert_service_error('NOT_FOUND', service.external_arrival_view,
                                  self.conn, T0 + 70, 'H1', other, status=404)
        self.assertEqual(self.snapshot(), before)

    def test_exact_page_boundary_has_no_extra_cursor(self):
        ids = [self.register(request_id=f'exact-{n}')['external_arrival_id'] for n in range(2)]
        page = service.list_external_arrivals(self.conn, T0 + 70, 'H1', limit=2)
        self.assertEqual([r['id'] for r in page['external_arrivals']], list(reversed(ids)))
        self.assertIsNone(page['next_before_id'])


class ExternalArrivalTransportReview(ExternalArrivalBase):
    def test_register_and_release_recheck_without_automatic_destination_change(self):
        self.patient('A')
        iid = self.accepted('A', 'H1')
        service.confirm_destination(self.conn, T0 + 20, 'A', iid)
        service.depart(self.conn, T0 + 21, 'A', EST)
        transport_review.enable(self.conn, T0 + 22, 'A', True)
        self.assertFalse(transport_review.view(self.conn, T0 + 22, 'A')['risk_signature'])
        eid = self.register(request_id='review-impact')['external_arrival_id']
        changed = transport_review.view(self.conn, T0 + 30, 'A')
        self.assertTrue(changed['risk_signature'])
        self.assertEqual(changed['state'], 'RECHECK_REQUIRED')
        patient = service._patient(self.conn, 'A')
        self.assertEqual(patient['destination_hospital_id'], 'H1')
        self.assertEqual(patient['state'], 'IN_TRANSIT')
        self.assertFalse(patient['needs_adjustment'])

        service.discharge_external_arrival(self.conn, T0 + 60, 'H1', eid)
        recovered = transport_review.view(self.conn, T0 + 60, 'A')
        self.assertFalse(recovered['risk_signature'])
        self.assertEqual(recovered['state'], 'MONITORING')
        patient = service._patient(self.conn, 'A')
        self.assertEqual(patient['destination_hospital_id'], 'H1')
        self.assertFalse(patient['needs_adjustment'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM inquiries').fetchone()[0], 1)


class ExternalArrivalHttp(ExternalArrivalBase):
    def setUp(self):
        super().setUp()
        self.http = ThreadingHTTPServer(('127.0.0.1', 0),
                                       make_handler(self.path, EST, clock=lambda: T0 + 30))
        self.worker = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.worker.start()
        self.base = f'http://127.0.0.1:{self.http.server_port}'

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.worker.join(timeout=5)
        super().tearDown()

    def call(self, method, path, body=None):
        request = urllib.request.Request(
            self.base + path, method=method,
            data=None if body is None else json.dumps(body).encode(),
            headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as response:
            with response:
                return response.code, json.load(response)

    def test_http_register_retry_discharge_and_hospital_scope(self):
        path = '/api/hospitals/H1/external-arrivals'
        payload = dict(resource_demands={'ed_bed': 1}, note='http-case',
                       request_id='http-idempotency', handled_by='nurse-http')
        status, created = self.call('POST', path, payload)
        self.assertEqual(status, 200)
        eid = created['external_arrival_id']
        before = self.snapshot()
        status, duplicate = self.call('POST', path, payload)
        self.assertEqual(status, 200)
        self.assertTrue(duplicate['duplicate'])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.call('GET', path + f'/{eid}')[0], 200)
        self.assertEqual(self.call('GET', f'/api/hospitals/H2/external-arrivals/{eid}')[0], 404)
        self.assertEqual(self.call('POST', f'/api/hospitals/H2/external-arrivals/{eid}/discharge', {})[0], 404)
        self.assertEqual(self.snapshot(), before)
        status, closed = self.call('POST', path + f'/{eid}/discharge',
                                   dict(expected_version=1, handled_by='nurse-http'))
        self.assertEqual(status, 200)
        self.assertFalse(closed['duplicate'])
        self.assertEqual(self.stored(eid)['state'], 'DISCHARGED')
        self.assertEqual(self.resource()['occupied'], 0)

    def test_http_list_query_validation_and_read_without_side_effects(self):
        self.register(request_id='http-read')
        before = self.snapshot()
        path = '/api/hospitals/H1/external-arrivals'
        for query in ('', '?state=ACTIVE', '?state=DISCHARGED', '?state=LEGACY', '?state=all',
                      '?limit=1', '?before_id=1000'):
            with self.subTest(query=query):
                self.assertEqual(self.call('GET', path + query)[0], 200)
        for query in ('?state=INVALID', '?limit=0', '?limit=-1', '?limit=1.5', '?limit=true',
                      '?limit=201', '?before_id=0', '?before_id=-1', '?before_id=no'):
            with self.subTest(query=query):
                self.assertEqual(self.call('GET', path + query)[0], 400)
        self.assertEqual(self.call('GET', '/api/hospitals/UNKNOWN/external-arrivals')[0], 404)
        self.assertEqual(self.snapshot(), before)


if __name__ == '__main__':
    unittest.main()
