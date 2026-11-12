"""Frontend-free launcher isolation and real API lifecycle, disposable data only."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen

from server import db, flow_rehearsal as flow, service


class FlowRehearsalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='flow-rehearsal-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'flow.db'

    def test_existing_business_db_is_rejected_before_any_modification(self):
        conn = db.connect(str(self.path))
        db.init(conn)
        service.upsert_hospital(conn, dict(id='REAL', name='Existing', lat=35, lon=127,
                                          source='public', capabilities=[]))
        before = list(conn.iterdump())
        conn.close()
        with self.assertRaisesRegex(ValueError, '기존 업무 DB'):
            flow.prepare_database(self.path)
        conn = sqlite3.connect(self.path)
        try:
            self.assertEqual(before, list(conn.iterdump()))
        finally:
            conn.close()

    def test_restart_preserves_case_progress_and_resource_changes(self):
        flow.prepare_database(self.path)
        conn = db.connect(str(self.path))
        try:
            service.create_patient(conn, 1790000000, 'FLOW-SAVED',
                dict(reporter_role='transporter', consent=True, extra={'synthetic': True}))
            service.set_resource(conn, 1790000000, 'FLOW-H1', 'ed_bed', 7, 2)
            before = list(conn.iterdump())
            flow.prepare_database(self.path)
            self.assertEqual(before, list(conn.iterdump()))
        finally:
            conn.close()

    def test_marker_does_not_allow_a_mixed_catalog(self):
        flow.prepare_database(self.path)
        conn = db.connect(str(self.path))
        try:
            service.upsert_hospital(conn, dict(id='OTHER', name='Existing', lat=35, lon=127,
                                              source='public', capabilities=[]))
            before = list(conn.iterdump())
            with self.assertRaisesRegex(ValueError, '전용 가상 DB'):
                flow.prepare_database(self.path)
            self.assertEqual(before, list(conn.iterdump()))
        finally:
            conn.close()

    def test_blank_or_non_sqlite_existing_file_is_not_replaced(self):
        self.path.write_bytes(b'not a SQLite database')
        with self.assertRaisesRegex(ValueError, '기존 업무 DB'):
            flow.prepare_database(self.path)
        self.assertEqual(self.path.read_bytes(), b'not a SQLite database')

    def test_directory_or_unreadable_file_returns_clear_validation_error(self):
        with self.assertRaisesRegex(ValueError, '파일이어야'):
            flow.prepare_database(self.root)
        self.path.touch()
        with patch('server.flow_rehearsal.sqlite3.connect', side_effect=sqlite3.OperationalError('denied')):
            with self.assertRaisesRegex(ValueError, '읽을 수 없습니다'):
                flow.prepare_database(self.path)

    def test_api_worker_and_blank_frontend_slots_with_no_external_requests(self):
        with patch('server.travel.kakao_estimate', side_effect=AssertionError('No external route call')) as kakao:
            with flow.LocalFlowServer(self.path) as local:
                with urlopen(local.base_url + '/') as response:
                    manifest = json.load(response)
                self.assertEqual(manifest['frontends']['paramedic']['status'], 'placeholder')
                self.assertFalse(manifest['external_hospital_messages'])
                for path in ('/patient/', '/hospital/', '/patient', '/hospital'):
                    with self.assertRaises(HTTPError) as error:
                        urlopen(local.base_url + path)
                    with error.exception as response:
                        self.assertEqual(response.code, 501)
                        self.assertEqual(json.load(response)['error'], 'FRONTEND_NOT_CONNECTED')
                with urlopen(local.base_url + '/api/hospitals') as response:
                    hospitals = json.load(response)
                self.assertEqual({h['id'] for h in hospitals}, {'FLOW-H1', 'FLOW-H2', 'FLOW-H3'})
                self.assertTrue(local.worker.thread.is_alive())
                self.assertFalse(local.estimator.use_kakao)
            self.assertFalse(local.thread.is_alive())
            self.assertFalse(local.worker.thread.is_alive())
            kakao.assert_not_called()

    def test_invalid_binding_and_poll_interval_are_rejected_before_db_creation(self):
        for changes in (dict(host='0.0.0.0'), dict(port=True), dict(port=-1),
                        dict(poll_seconds=0), dict(poll_seconds=float('nan'))):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                flow.LocalFlowServer(self.path, **changes)
        self.assertFalse(self.path.exists())

    def test_report_records_step_results_and_does_not_claim_clinical_effect(self):
        report = dict(run_id='unit', passed=True, database=str(self.path), scenarios=[dict(
            scenario='normal', patient_id='FLOW-TEST', outcome={'state': 'COMPLETED'},
            steps=[dict(id='accept', role='hospital', method='POST', path='/api/inquiries/1/respond', status=200)])])
        flow.write_report(self.root, report)
        self.assertEqual(json.loads((self.root / 'report.json').read_text(encoding='utf-8')), report)
        summary = (self.root / 'report.md').read_text(encoding='utf-8')
        self.assertIn('COMPLETED', summary)
        self.assertIn('| accept | hospital | POST |', summary)
        self.assertIn('치료 결과나 알고리즘 우수성 검증이 아님', summary)


if __name__ == '__main__':
    unittest.main()
