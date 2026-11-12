"""Portable team setup: provenance, non-destructive repeats and live landing config."""
import copy
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.request import urlopen

from server import db, service, team_demo
from server.travel import TravelEstimator


class TeamDemoTests(unittest.TestCase):
    NOW = 1791880000

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='team-demo-')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'nested/demo.db'
        self.catalog = team_demo.load_catalog()

    def conn(self):
        conn = db.connect(str(self.path))
        self.addCleanup(conn.close)
        return conn

    def prepare(self, scenario='normal', **kwargs):
        return team_demo.prepare(self.path, scenario, now=self.NOW, **kwargs)

    def test_bundled_catalog_is_public_snapshot_not_operational_capacity(self):
        result = self.prepare()
        conn = self.conn()
        self.assertEqual(20, result['hospital_count'])
        self.assertEqual(0, result['operational_resources'])
        self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM hospitals WHERE source!='public'").fetchone()[0])
        for snap in self.catalog['snapshots']:
            actual = conn.execute('SELECT * FROM public_api_snapshots WHERE hospital_id=? AND endpoint=?',
                                  (snap['hospital_id'], snap['endpoint'])).fetchone()
            self.assertEqual(snap['fetched_at'], actual['fetched_at'])
            self.assertEqual(snap.get('source_observed_at'), actual['source_observed_at'])
            self.assertEqual(snap['normalized'], json.loads(actual['normalized']))
        patient = service.patient_view(conn, 'CAU-E2E-001', self.NOW)
        self.assertEqual('WAITING', patient['state'])
        self.assertEqual([('PENDING', team_demo.CAU)],
                         [(q['state'], q['hospital_id']) for q in patient['inquiries']])
        self.assertFalse(patient['inquiry_workflow']['enabled'])
        self.assertEqual(1, conn.execute('SELECT COUNT(*) FROM conversations').fetchone()[0])

    def test_prepared_change_is_explicit_synthetic_recheck_without_automatic_reroute(self):
        self.prepare('change')
        conn = self.conn()
        p = service.patient_view(conn, 'CAU-CHANGE-001', self.NOW)
        self.assertEqual('IN_TRANSIT', p['state'])
        self.assertEqual(team_demo.YE, p['destination_hospital_id'])
        self.assertEqual('RECHECK_REQUIRED', p['transport_review']['state'])
        self.assertFalse(p['needs_adjustment'])
        self.assertEqual('NOT_STARTED', p['transport_inquiry_workflow']['state'])
        self.assertTrue(p['extra']['synthetic'])
        self.assertFalse(p['extra']['hospital_messages_delivered_externally'])
        restriction = conn.execute('SELECT * FROM restrictions').fetchone()
        self.assertIn('[가상 테스트]', restriction['reason'])
        self.assertEqual('정형외과', restriction['scope'])
        self.assertEqual(0, conn.execute('SELECT COUNT(*) FROM resources').fetchone()[0])

    def test_restart_preserves_progress_events_observations_and_operator_edits(self):
        self.prepare('both')
        conn = self.conn()
        service.cancel_patient(conn, self.NOW + 1, 'CAU-E2E-001', '테스트 완료')
        conn.execute('UPDATE hospitals SET phone=? WHERE id=?', ('operator-edited', team_demo.CAU))
        before = list(conn.iterdump())
        result = team_demo.prepare(self.path, 'both', now=self.NOW + 10000)
        self.assertEqual([], result['created'])
        self.assertEqual({'CAU-E2E-001', 'CAU-CHANGE-001'}, set(result['reused']))
        self.assertEqual(before, list(conn.iterdump()))

    def test_empty_creates_no_cases_and_never_deletes_existing(self):
        self.prepare('empty')
        conn = self.conn()
        self.assertEqual(0, conn.execute('SELECT COUNT(*) FROM patients').fetchone()[0])
        self.prepare('normal')
        before = list(conn.iterdump())
        self.prepare('empty')
        self.assertEqual(before, list(conn.iterdump()))

    def test_suffix_adds_new_normal_case_while_old_terminal_case_is_kept(self):
        self.prepare()
        conn = self.conn()
        service.cancel_patient(conn, self.NOW + 1, 'CAU-E2E-001', '연습 완료')
        self.prepare(case_suffix='run2')
        self.assertEqual('CANCELLED', service._patient(conn, 'CAU-E2E-001')['state'])
        self.assertEqual('WAITING', service._patient(conn, 'CAU-E2E-001-run2')['state'])
        self.assertEqual(2, len(team_demo.demo_config(conn)['cases']))
        with self.assertRaises(ValueError):
            self.prepare(case_suffix='../outside')

    def test_conflicting_existing_case_aborts_before_creating_other_requested_case(self):
        self.prepare('empty')
        conn = self.conn()
        service.create_patient(conn, self.NOW, 'CAU-CHANGE-001',
                               {'reporter_role': 'transporter', 'consent': True, 'extra': {'synthetic': True}})
        before = list(conn.iterdump())
        with self.assertRaisesRegex(ValueError, '충돌'):
            self.prepare('both')
        self.assertEqual(before, list(conn.iterdump()))

    def test_invalid_fixture_is_rejected_before_database_creation(self):
        bad = copy.deepcopy(self.catalog)
        bad['snapshots'][-1]['hospital_id'] = 'MISSING-HOSPITAL'
        fixture = Path(self.temp.name) / 'bad.json'
        fixture.write_text(json.dumps(bad), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '잘못된 참조'):
            self.prepare(catalog_path=fixture)
        self.assertFalse(self.path.exists())

    def test_duplicate_public_source_and_non_public_hospitals_are_rejected(self):
        fixture = Path(self.temp.name) / 'bad.json'
        for mutate in ('duplicate_source', 'synthetic'):
            bad = copy.deepcopy(self.catalog)
            if mutate == 'duplicate_source':
                bad['hospitals'][1]['source_ids']['nmc'] = bad['hospitals'][0]['source_ids']['nmc']
            else:
                bad['hospitals'][0]['source'] = 'synthetic'
            fixture.write_text(json.dumps(bad), encoding='utf-8')
            with self.assertRaises(ValueError):
                self.prepare(catalog_path=fixture)
        self.assertFalse(self.path.exists())

    def test_existing_catalog_is_not_mixed_with_fixture_or_replaced(self):
        self.path.parent.mkdir(parents=True)
        conn = self.conn()
        db.init(conn)
        service.upsert_hospital(conn, {'id': 'H01', 'name': 'Existing custom hospital',
                                      'lat': 35, 'lon': 127, 'source': 'synthetic'})
        before = list(conn.iterdump())
        with self.assertRaises(service.ServiceError):
            self.prepare()
        self.assertEqual(before, list(conn.iterdump()))
        self.assertEqual(0, conn.execute('SELECT COUNT(*) FROM public_api_snapshots').fetchone()[0])

    def test_existing_restriction_is_not_cleared_to_create_another_change_case(self):
        self.prepare('change')
        conn = self.conn()
        before = list(conn.iterdump())
        with self.assertRaisesRegex(ValueError, '수용 제한'):
            self.prepare('change', case_suffix='run2')
        self.assertEqual(before, list(conn.iterdump()))

    def test_preparation_never_calls_external_apis_even_if_keys_exist(self):
        with patch('urllib.request.urlopen', side_effect=AssertionError('unexpected external request')):
            self.prepare('both')

    def test_invalid_second_case_is_rejected_before_creating_database(self):
        cases = json.loads(team_demo.CASES.read_text(encoding='utf-8'))
        cases['change']['breathing'] = 'invalid-clinical-state'
        fixture = Path(self.temp.name) / 'bad-cases.json'
        fixture.write_text(json.dumps(cases), encoding='utf-8')
        with self.assertRaises(service.ServiceError):
            self.prepare('both', cases_path=fixture)
        self.assertFalse(self.path.exists())

    def test_dynamic_landing_configuration_follows_its_own_db_and_origin(self):
        self.prepare('normal', case_suffix='portable')
        server = ThreadingHTTPServer(('127.0.0.1', 0), team_demo.make_demo_handler(
            str(self.path), TravelEstimator(use_kakao=False)))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with urlopen(f'http://127.0.0.1:{server.server_port}/public-flow-demo.json', timeout=5) as response:
            config = json.load(response)
        self.assertNotIn('base', config)
        self.assertEqual(['CAU-E2E-001-portable'], [c['id'] for c in config['cases']])
        self.assertTrue(config['cases'][0]['patient_url'].startswith('/patient/'))
        conn = self.conn()
        alternative = team_demo.demo_config(conn, 'http://192.168.10.2:18027', 'http://192.168.10.2:18028/')
        self.assertEqual('http://192.168.10.2:18027', alternative['base'])
        self.assertEqual('http://192.168.10.2:18028/', alternative['intake_url'])
        self.assertTrue(alternative['cases'][0]['hospital_url'].startswith(alternative['base']))

    def test_origin_validation_rejects_credentials_paths_query_and_bad_ports(self):
        for value in ('file:///tmp/a', 'http://user:pass@localhost:8000', 'http://localhost/a',
                      'http://localhost?secret=x', 'http://localhost#frag', 'http://localhost:99999',
                      'http://localhost:0', 'http://local host:80'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                team_demo.origin(value)
        self.assertEqual('http://localhost:18027', team_demo.origin('http://localhost:18027/'))


if __name__ == '__main__':
    unittest.main()
