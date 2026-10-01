"""공통 서버 업무 규칙 테스트 (T02~T05, S05·S07, H23·H24·H25, H33~H38).

실행: python -m unittest test_server -v
"""
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from server import db, probe_apis, service, synthetic
from server.app import make_handler
from server.service import ServiceError
from server.travel import TravelEstimator

T0 = 1_790_000_000
EST = TravelEstimator(use_kakao=False)


def base_patient(**kw):
    p = dict(reporter_role='guardian', consent=True, contact='010-0000-0000', age=7, symptoms='고열',
             required_capabilities=[], resource_demands={'ed_bed': 1}, lat=35.16, lon=126.85)
    p.update(kw)
    return p


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / 't.db')
        self.conn = db.connect(self.path)
        db.init(self.conn)
        for hid, lat, caps, beds in (('H1', 35.17, ['응급의학', '소아청소년과'], 1),
                                     ('H2', 35.20, ['응급의학', '소아청소년과'], 2),
                                     ('H3', 35.30, ['응급의학'], 3)):
            service.upsert_hospital(self.conn, dict(id=hid, name=hid, lat=lat, lon=126.85, capabilities=caps,
                                                    source='synthetic'))
            service.set_resource(self.conn, T0, hid, 'ed_bed', beds, 0, source='synthetic')

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def patient(self, pid='A', now=T0, **kw):
        return service.create_patient(self.conn, now, pid, base_patient(**kw))

    def inquiry_id(self, pid, hid):
        return self.conn.execute('SELECT id FROM inquiries WHERE patient_id=? AND hospital_id=?',
                                 (pid, hid)).fetchone()[0]

    def accepted(self, pid, hid, now=T0 + 10):
        service.send_inquiries(self.conn, now, pid, [hid])
        iid = self.inquiry_id(pid, hid)
        service.respond(self.conn, now + 5, iid, 'accept', 'nurse1')
        return iid

    def code(self, fn, *a, **kw):
        with self.assertRaises(ServiceError) as cm:
            fn(*a, **kw)
        return cm.exception.code


class NormalFlow(Base):
    def test_inquiry_to_arrival_and_discharge(self):          # T02
        self.patient(required_capabilities=['소아청소년과'])
        cand = service.candidates(self.conn, T0, 'A', EST)
        self.assertEqual([c['hospital_id'] for c in cand['candidates']], ['H1', 'H2'])
        self.assertEqual(cand['excluded'][0]['reason'], 'NO_COMPATIBLE_CAPABILITY')
        self.assertIn('가상 운영 상태', cand['candidates'][0]['checks'])

        service.send_inquiries(self.conn, T0 + 10, 'A', ['H1', 'H2'])
        h1, h2 = self.inquiry_id('A', 'H1'), self.inquiry_id('A', 'H2')
        service.respond(self.conn, T0 + 20, h1, 'checking', 'nurse1')
        service.respond(self.conn, T0 + 30, h1, 'accept', 'nurse1')
        # 수용 응답만으로는 자원을 점유하지 않는다
        self.assertEqual(service.hospital_view(self.conn, 'H1', T0 + 30)['resources'][0]['free'], 1)

        view = service.confirm_destination(self.conn, T0 + 40, 'A', h1)
        self.assertEqual((view['state'], view['destination_hospital_id']), ('CONFIRMED', 'H1'))
        self.assertEqual({q['hospital_id']: q['state'] for q in view['inquiries']},
                         {'H1': 'CONFIRMED', 'H2': 'CANCELLED'})
        self.assertTrue(service.hospital_inquiries(self.conn, T0 + 40, 'H2')['recent'][0]['closed_by_patient'])
        r = service.hospital_view(self.conn, 'H1', T0 + 40)['resources'][0]
        self.assertEqual((r['expected'], r['free']), (1, 0))

        view = service.depart(self.conn, T0 + 50, 'A', EST)
        self.assertEqual(view['state'], 'IN_TRANSIT')
        self.assertGreater(view['eta_at'], T0 + 50)
        self.assertEqual(service.incoming(self.conn, T0 + 60, 'H1')[0]['id'], 'A')

        service.confirm_arrival(self.conn, T0 + 600, 'A', 'H1')
        r = service.hospital_view(self.conn, 'H1', T0 + 600)['resources'][0]
        self.assertEqual((r['occupied'], r['expected'], r['free']), (1, 0, 0))   # 이중 계산 없음 (H38)
        service.discharge(self.conn, T0 + 900, 'A', 'H1')
        r = service.hospital_view(self.conn, 'H1', T0 + 900)['resources'][0]
        self.assertEqual((r['occupied'], r['free']), (0, 1))
        types = [e['type'] for e in service.events_since(self.conn, patient_id='A')]
        self.assertEqual(types[0], 'patient_created')
        self.assertIn('destination_confirmed', types)
        self.assertEqual(types[-1], 'discharged')


class InputRules(Base):
    def test_consent_and_unknown_answers(self):
        self.assertEqual(self.code(service.create_patient, self.conn, T0, 'X', base_patient(consent=False)),
                         'CONSENT_REQUIRED')
        view = self.patient('U', age=None, danger_signs=['의식 저하'])
        self.assertIsNone(view['age'])                 # 모르겠음은 null로 남는다
        self.assertTrue(view['call_119'])
        self.assertEqual(view['effective_priority'], 4)   # 미확인 중증도는 높음으로 다룸
        view = self.patient('S', priority=1, priority_source='self')
        self.assertEqual(view['effective_priority'], 4)   # 자기신고 경증은 낮추지 않음
        view = self.patient('E', priority=1, priority_source='expert')
        self.assertEqual(view['effective_priority'], 1)

    def test_edit_only_before_sending(self):
        self.patient()
        self.assertEqual(service.update_patient(self.conn, T0 + 1, 'A', {'symptoms': '고열·구토'})['version'], 2)
        service.send_inquiries(self.conn, T0 + 2, 'A', ['H1'])
        self.assertEqual(self.code(service.update_patient, self.conn, T0 + 3, 'A', {'age': 8}), 'ALREADY_SENT')


class Exceptions(Base):                                       # T03
    def test_decline_needs_reason_and_pending_cannot_confirm(self):
        self.patient()
        service.send_inquiries(self.conn, T0, 'A', ['H1', 'H2'])
        h1, h2 = self.inquiry_id('A', 'H1'), self.inquiry_id('A', 'H2')
        self.assertEqual(self.code(service.respond, self.conn, T0 + 5, h1, 'decline', 'n'), 'INVALID')
        service.respond(self.conn, T0 + 5, h1, 'decline', 'n', reason='병상')
        self.assertEqual(self.code(service.confirm_destination, self.conn, T0 + 6, 'A', h1), 'NOT_ACCEPTED')
        self.assertEqual(self.code(service.confirm_destination, self.conn, T0 + 6, 'A', h2), 'NOT_ACCEPTED')

    def test_overdue_and_retry(self):
        self.patient()
        service.send_inquiries(self.conn, T0, 'A', ['H1'])
        iid = self.inquiry_id('A', 'H1')
        view = service.patient_view(self.conn, 'A', T0 + service.PENDING_WARN)
        self.assertTrue(view['inquiries'][0]['overdue'])
        self.assertEqual(service.hospital_inquiries(self.conn, T0 + 61, 'H1')['open'][0]['overdue'], True)
        self.assertEqual(self.code(service.retry_inquiry, self.conn, T0 + 60, 'A', iid), 'TOO_SOON')
        view = service.retry_inquiry(self.conn, T0 + service.RETRY_MIN, 'A', iid)
        self.assertEqual((view['inquiries'][0]['attempts'], view['inquiries'][0]['overdue']), (2, False))

    def test_question_answer_and_cancel(self):
        self.patient()
        service.send_inquiries(self.conn, T0, 'A', ['H1'])
        iid = self.inquiry_id('A', 'H1')
        service.respond(self.conn, T0 + 5, iid, 'question', 'n', question='복용 중인 약이 있나요?')
        view = service.answer_question(self.conn, T0 + 10, 'A', iid, '없음')
        self.assertEqual(view['inquiries'][0]['answer'], '없음')
        service.cancel_inquiry(self.conn, T0 + 20, 'A', iid, '증상 호전')
        self.assertEqual(self.code(service.respond, self.conn, T0 + 21, iid, 'accept', 'n'), 'INVALID_STATE')

    def test_expired_acceptance_cannot_confirm(self):
        self.patient()
        iid = self.accepted('A', 'H1')
        late = T0 + 15 + service.ACCEPT_TTL
        self.assertEqual(self.code(service.confirm_destination, self.conn, late, 'A', iid), 'ACCEPTANCE_EXPIRED')
        self.assertEqual(service.patient_view(self.conn, 'A', late)['inquiries'][0]['state'], 'EXPIRED')
        service.send_inquiries(self.conn, late + 1, 'A', ['H1'])   # 다시 문의 가능
        self.assertEqual(service.patient_view(self.conn, 'A', late + 1)['inquiries'][0]['state'], 'PENDING')

    def test_restriction_and_shortfall_block_accept(self):     # H23
        self.patient()
        service.send_inquiries(self.conn, T0, 'A', ['H1'])
        iid = self.inquiry_id('A', 'H1')
        service.add_restriction(self.conn, T0 + 1, 'H1', 'all', '인력 부족', ends_at=T0 + 100)
        self.assertEqual(self.code(service.respond, self.conn, T0 + 2, iid, 'accept', 'n'), 'RESTRICTED')
        self.assertEqual(service.candidates(self.conn, T0 + 2, 'A', EST)['excluded'][0]['reason'], 'RESTRICTED')
        # 종료 예정이 지나도 자동 복구하지 않고 재확인 필요로 표시
        h1 = [c for c in service.candidates(self.conn, T0 + 101, 'A', EST)['candidates'] if c['hospital_id'] == 'H1'][0]
        self.assertTrue(any('재확인' in c for c in h1['checks']))
        service.set_resource(self.conn, T0 + 102, 'H1', 'ed_bed', 1, 1)
        self.assertEqual(self.code(service.respond, self.conn, T0 + 103, iid, 'accept', 'n'), 'RESOURCE_CONFLICT')

    def test_cancel_releases_expected_demand(self):
        self.patient()
        service.confirm_destination(self.conn, T0 + 20, 'A', self.accepted('A', 'H1'))
        self.assertEqual(service.hospital_view(self.conn, 'H1', T0 + 20)['resources'][0]['free'], 0)
        service.cancel_patient(self.conn, T0 + 30, 'A', '증상 호전')
        self.assertEqual(service.hospital_view(self.conn, 'H1', T0 + 30)['resources'][0]['free'], 1)

    def test_no_compatible_hospital(self):
        self.patient(required_capabilities=['심장내과'])
        cand = service.candidates(self.conn, T0, 'A', EST)
        self.assertEqual((cand['candidates'], cand['empty_reason']), ([], 'NO_COMPATIBLE_HOSPITAL'))


class Concurrency(Base):                                      # T04
    def test_duplicate_click_and_conflicting_staff(self):
        self.patient()
        service.send_inquiries(self.conn, T0, 'A', ['H1'])
        iid = self.inquiry_id('A', 'H1')
        version = service.hospital_inquiries(self.conn, T0, 'H1')['open'][0]['version']
        self.assertFalse(service.respond(self.conn, T0 + 1, iid, 'accept', 'n1', expected_version=version)['duplicate'])
        self.assertTrue(service.respond(self.conn, T0 + 2, iid, 'accept', 'n1', expected_version=version)['duplicate'])
        self.assertEqual(self.code(service.respond, self.conn, T0 + 3, iid, 'decline', 'n2', reason='병상',
                                   expected_version=version), 'CONFLICT')

    def test_two_patients_last_bed_only_one_confirms(self):
        self.patient('A')
        self.patient('B')
        ia, ib = self.accepted('A', 'H1'), self.accepted('B', 'H1')   # 둘 다 수용 응답(점유 아님)
        results, barrier = {}, threading.Barrier(2)

        def run(pid, iid):
            conn = db.connect(self.path)
            barrier.wait()
            try:
                service.confirm_destination(conn, T0 + 30, pid, iid)
                results[pid] = 'ok'
            except ServiceError as e:
                results[pid] = e.code
            finally:
                conn.close()

        threads = [threading.Thread(target=run, args=a) for a in (('A', ia), ('B', ib))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(results.values()), ['RESOURCE_CONFLICT', 'ok'])
        r = service.hospital_view(self.conn, 'H1', T0 + 31)['resources'][0]
        self.assertEqual((r['expected'], r['free']), (1, 0))

    def test_resend_does_not_duplicate(self):
        self.patient()
        service.send_inquiries(self.conn, T0, 'A', ['H1', 'H1'])
        service.send_inquiries(self.conn, T0 + 1, 'A', ['H1'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM inquiries').fetchone()[0], 1)


class SuddenArrival(Base):                                    # T05
    def test_unregistered_b_affects_in_transit_a(self):
        self.patient('A')
        service.confirm_destination(self.conn, T0 + 20, 'A', self.accepted('A', 'H1'))
        service.depart(self.conn, T0 + 30, 'A', EST)
        result = service.register_external_arrival(self.conn, T0 + 60, 'H1', {'ed_bed': 1}, note='도보 내원 중증')
        self.assertEqual([a['patient']['id'] for a in result['affected']], ['A'])
        r = result['hospital']['resources'][0]
        self.assertEqual((r['occupied'], r['expected'], r['free']), (1, 1, -1))

        # 목적지는 자동으로 바뀌지 않는다
        self.assertEqual(service.patient_view(self.conn, 'A', T0 + 61)['destination_hospital_id'], 'H1')
        self.assertEqual(self.code(service.send_inquiries, self.conn, T0 + 61, 'A', ['H2']), 'INVALID_STATE')

        view = service.hospital_decision(self.conn, T0 + 70, 'A', 'H1', 'adjust', '진료 자리 부족')
        self.assertTrue(view['needs_adjustment'])
        self.assertEqual(view['notifications'][0]['type'], 'adjustment_needed')
        self.assertEqual(view['destination_hospital_id'], 'H1')

        # 새 병원 수용 확인 전에는 변경 불가
        service.send_inquiries(self.conn, T0 + 80, 'A', ['H2'])
        h2 = self.inquiry_id('A', 'H2')
        self.assertEqual(self.code(service.change_destination, self.conn, T0 + 81, 'A', h2, EST), 'NOT_ACCEPTED')
        service.respond(self.conn, T0 + 90, h2, 'accept', 'n2')
        view = service.change_destination(self.conn, T0 + 100, 'A', h2, EST, lat=35.18, lon=126.85)
        self.assertEqual((view['state'], view['destination_hospital_id'], view['needs_adjustment']),
                         ('IN_TRANSIT', 'H2', 0))
        self.assertEqual(service.affected_patients(self.conn, T0 + 100, 'H1'), [])
        old = [e['type'] for e in service.events_since(self.conn, hospital_id='H1')]
        self.assertIn('patient_redirected_away', old)

    def test_keep_decision_notifies_without_change(self):
        self.patient('A')
        service.confirm_destination(self.conn, T0 + 20, 'A', self.accepted('A', 'H2'))
        service.depart(self.conn, T0 + 30, 'A', EST)
        result = service.register_external_arrival(self.conn, T0 + 60, 'H2', {'ed_bed': 1})
        self.assertEqual(result['affected'], [])   # 여유 2 - B 1 = A 수용 가능
        view = service.hospital_decision(self.conn, T0 + 70, 'A', 'H2', 'keep')
        self.assertEqual(view['notifications'][0]['type'], 'hospital_keeps')
        self.assertEqual(self.code(service.change_destination, self.conn, T0 + 71, 'A', 1, EST), 'NOT_REQUESTED')


class Synthetic(unittest.TestCase):
    def test_seed(self):
        conn = db.connect(':memory:')
        db.init(conn)
        synthetic.seed_hospitals(conn, T0)
        ids = synthetic.seed_patients(conn, T0, 20)
        hs = service.list_hospitals(conn, T0)
        self.assertEqual((len(hs), len(ids)), (10, 20))
        self.assertTrue(all(h['source'] == 'synthetic' for h in hs))
        self.assertTrue(all(0 <= r['occupied'] <= r['capacity'] for h in hs for r in h['resources']))
        for pid in ids:
            service.candidates(conn, T0, pid, EST)


class Probe(unittest.TestCase):
    def test_scrub_and_inventory(self):
        key = 'abc+def/=='
        self.assertNotIn('abc', probe_apis.scrub('serviceKey=abc%2Bdef%2F%3D%3D&x=abc+def/==', key))
        xml = ('<response><header><resultCode>00</resultCode></header><body><items>'
               '<item><a>1</a><b></b></item><item><a>2</a></item></items><totalCount>2</totalCount></body></response>')
        header, n, fields = probe_apis.inventory_xml(xml)
        self.assertEqual((header['resultCode'], n), ('00', 2))
        self.assertEqual(fields['a'], dict(present=2, empty=0, samples=['1', '2'], missing=0))
        self.assertEqual(fields['b'], dict(present=1, empty=1, samples=[], missing=1))


class Http(Base):
    def test_http_roundtrip(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.path, EST, clock=lambda: T0))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f'http://127.0.0.1:{server.server_port}'

        def call(method, path, body=None):
            req = urllib.request.Request(base + path, method=method, data=None if body is None else
                                         json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
            try:
                with urllib.request.urlopen(req) as r:
                    return r.status, json.load(r)
            except urllib.error.HTTPError as e:
                with e:
                    return e.code, json.load(e)

        try:
            status, view = call('POST', '/api/patients', dict(base_patient(), id='W1'))
            self.assertEqual((status, view['state']), (200, 'WAITING'))
            self.assertEqual(call('POST', '/api/patients', base_patient(consent=False))[0], 400)
            status, cand = call('GET', '/api/patients/W1/candidates')
            self.assertEqual(len(cand['candidates']), 3)
            call('POST', '/api/patients/W1/inquiries', {'hospital_ids': ['H1']})
            iid = call('GET', '/api/hospitals/H1/inquiries')[1]['open'][0]['id']
            self.assertEqual(call('POST', f'/api/inquiries/{iid}/respond', {'action': 'accept', 'handled_by': 'n'})[0], 200)
            status, view = call('POST', '/api/patients/W1/confirm', {'inquiry_id': iid})
            self.assertEqual(view['state'], 'CONFIRMED')
            self.assertEqual(call('GET', '/api/patients/NOPE')[0], 404)
            self.assertEqual(call('GET', '/api/nothing')[0], 404)
        finally:
            server.shutdown()
            server.server_close()


if __name__ == '__main__':
    unittest.main()
