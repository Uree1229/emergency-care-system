"""Whole-flow HTTP rehearsals with fresh local databases and no frontend files."""
import json
import tempfile
import threading
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from server.flow_rehearsal import LocalFlowServer, manifest
from server.flow_scenarios import ScenarioError, local_origin, run_scenario


class FlowScenarioValidation(unittest.TestCase):
    def test_loopback_origin_is_numeric_and_never_uses_dns_for_localhost(self):
        self.assertEqual(local_origin('http://localhost:18030/'), 'http://127.0.0.1:18030')
        self.assertEqual(local_origin('http://[::1]:18030'), 'http://[::1]:18030')

    def test_invalid_remote_or_ambiguous_origins_do_not_create_a_client(self):
        values = ['http://example.com:80', 'https://127.0.0.1:18030', 'http://127.0.0.1',
                  'http://127.0.0.1:0', 'http://127.0.0.1:65536', 'http://127.0.0.1:18030/api',
                  'http://user:password@127.0.0.1:18030', 'http://127.0.0.1:18030?key=secret',
                  'http://127.0.0.1:18030#fragment', 'http://127.0.0.1:18030\n']
        with patch('server.flow_scenarios.urllib.request.build_opener') as opener:
            for value in values:
                with self.subTest(value=value), self.assertRaises(ValueError):
                    run_scenario(value, 'normal')
            opener.assert_not_called()

    def test_invalid_scenario_identifiers_and_timeout_never_connect(self):
        invalid = [dict(scenario='unknown'), dict(run_id='../outside'), dict(run_id=''),
                   dict(run_id='한글'), dict(run_id='a' * 49), dict(timeout=0),
                   dict(timeout=float('nan')), dict(timeout=True), dict(timeout=121)]
        with patch('server.flow_scenarios.urllib.request.build_opener') as opener:
            for change in invalid:
                values = dict(scenario='normal', run_id='validation', timeout=10)
                values.update(change)
                with self.subTest(change=change), self.assertRaises(ValueError):
                    run_scenario('http://127.0.0.1:18030', **values)
            opener.assert_not_called()


class FlowScenarioHttp(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='flow-scenarios-')
        self.addCleanup(self.temp.cleanup)
        self.local = LocalFlowServer(Path(self.temp.name) / 'flow.db', poll_seconds=.05)
        self.local.__enter__()
        self.addCleanup(self.local.__exit__, None, None, None)

    def run_case(self, scenario):
        report = run_scenario(self.local.base_url, scenario, run_id='http-' + scenario)
        self.assertTrue(report['passed'])
        self.assertEqual(report['outcome']['state'], 'COMPLETED')
        self.assertTrue(report['synthetic'])
        self.assertFalse(report['external_hospital_messages'])
        self.assertEqual(report['automation_scope'], 'scripted_role_emulation')
        self.assertEqual(report['frontends'], {'paramedic': 'placeholder', 'hospital': 'placeholder'})
        self.assertFalse(report['outcome']['clinical_effectiveness_evaluated'])
        self.assertNotIn('손목', json.dumps(report, ensure_ascii=False))
        self.assertNotIn('현재 상태를 공유', json.dumps(report, ensure_ascii=False))
        self.assertTrue(all(step['status'] == step['expected_status'] for step in report['steps']))
        return {step['id']: step for step in report['steps']}, report

    def test_normal_registration_to_discharge_and_shared_room_uses_real_http(self):
        steps, report = self.run_case('normal')
        self.assertEqual(report['outcome']['destination_hospital_id'], 'FLOW-H1')
        self.assertFalse(report['outcome']['destination_changed'])
        for name in ('patient_register', 'automatic_initial_inquiry', 'hospital_call_inbox',
                     'call_record_confirm', 'call_is_not_acceptance', 'hospital_additional_question',
                     'crew_additional_answer', 'hospital_acceptance', 'crew_confirm_destination',
                     'initial_hospital_incoming', 'initial_chat_reconnect', 'crew_departure',
                     'hospital_arrival', 'hospital_discharge'):
            self.assertIn(name, steps)
        self.assertEqual(steps['hospital_checking_read']['path'], '/api/hospitals/FLOW-H1/inquiries')
        self.assertEqual(steps['hospital_checking_read']['role'], 'hospital')
        self.assertTrue(steps['initial_crew_chat_retry']['checks'][0]['passed'])

    def test_amendment_invalidates_old_response_and_new_version_is_reconfirmed(self):
        steps, _ = self.run_case('amended')
        self.assertEqual(steps['old_response_version_rejected']['status'], 409)
        self.assertIn('automatic_reinquiry_after_amendment', steps)
        self.assertIn('latest_information_acceptance', steps)

    def test_resource_change_rechecks_and_reroutes_only_after_explicit_roles(self):
        steps, report = self.run_case('change')
        self.assertTrue(report['outcome']['destination_changed'])
        self.assertEqual(report['outcome']['destination_hospital_id'], 'FLOW-H2')
        for name in ('condition_change_detected', 'notice_delivery', 'notice_ack',
                     'notice_reconnect', 'ack_is_not_reroute', 'hospital_adjustment',
                     'automatic_alternative_inquiry', 'accept_is_not_reroute',
                     'explicit_destination_change', 'old_hospital_new_message_blocked',
                     'hospital_review_incoming', 'replacement_hospital_incoming',
                     'replacement_chat_reconnect', 'external_patient_discharge'):
            self.assertIn(name, steps)
        self.assertEqual(steps['alternative_acceptance_read']['path'], '/api/hospitals/FLOW-H2/inquiries')
        self.assertEqual(steps['old_hospital_new_message_blocked']['status'], 403)

    def test_same_case_is_not_silently_overwritten_and_failure_keeps_partial_report(self):
        self.run_case('normal')
        with self.assertRaises(ScenarioError) as caught:
            run_scenario(self.local.base_url, 'normal', run_id='http-normal')
        report = caught.exception.report
        self.assertFalse(report['passed'])
        create = next(step for step in report['steps'] if step['id'] == 'patient_register')
        self.assertEqual(create['status'], 409)
        self.assertEqual(report['failure']['step_id'], 'patient_register')

    def test_all_scenarios_share_a_run_identifier_without_cross_case_key_conflicts(self):
        reports = [run_scenario(self.local.base_url, scenario, run_id='combined')
                   for scenario in ('normal', 'amended', 'change')]
        self.assertTrue(all(report['passed'] for report in reports))
        self.assertEqual(len({report['patient_id'] for report in reports}), 3)


class FlowScenarioNetworkIsolation(unittest.TestCase):
    def test_connection_failure_reports_the_attempted_step_without_private_payload(self):
        with patch('server.flow_scenarios.urllib.request.build_opener') as factory:
            factory.return_value.open.side_effect = urllib.error.URLError('unavailable')
            with self.assertRaises(ScenarioError) as caught:
                run_scenario('http://127.0.0.1:18030', 'normal', run_id='connection')
        report = caught.exception.report
        self.assertEqual(report['failure']['step_id'], 'flow_server_contract')
        self.assertEqual(report['steps'][0]['method'], 'GET')
        self.assertEqual(report['steps'][0]['path'], '/manifest.json')
        self.assertIsNone(report['steps'][0]['status'])
        self.assertNotIn('손목', json.dumps(report, ensure_ascii=False))

    def test_invalid_json_preserves_http_status_and_attempted_path(self):
        class InvalidJson(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                body = json.dumps(manifest()).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                self.send_response(200)
                self.send_header('Content-Type', 'text/html')
                self.send_header('Content-Length', '8')
                self.end_headers()
                self.wfile.write(b'not-json')

        server = ThreadingHTTPServer(('127.0.0.1', 0), InvalidJson)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with self.assertRaises(ScenarioError) as caught:
                run_scenario(f'http://127.0.0.1:{server.server_port}', 'normal', run_id='bad-json')
            report = caught.exception.report
            self.assertEqual(report['failure']['step_id'], 'patient_register')
            create = next(step for step in report['steps'] if step['id'] == 'patient_register')
            self.assertEqual(create['status'], 200)
            self.assertEqual(create['path'], '/api/patients')
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_redirect_is_reported_without_following_remote_location(self):
        class Redirect(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                body = json.dumps(manifest()).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                self.send_response(302)
                self.send_header('Location', 'http://example.com/never-request-this')
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', '2')
                self.end_headers()
                self.wfile.write(b'{}')

        server = ThreadingHTTPServer(('127.0.0.1', 0), Redirect)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with self.assertRaises(ScenarioError) as caught:
                run_scenario(f'http://127.0.0.1:{server.server_port}', 'normal', run_id='redirect')
            create = next(step for step in caught.exception.report['steps'] if step['id'] == 'patient_register')
            self.assertEqual(create['status'], 302)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_another_local_service_is_rejected_before_any_patient_creation(self):
        posts = []

        class WrongService(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                body = b'{"service":"another-demo"}'
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                posts.append(self.path)
                self.send_response(500)
                self.end_headers()

        server = ThreadingHTTPServer(('127.0.0.1', 0), WrongService)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with self.assertRaises(ScenarioError) as caught:
                run_scenario(f'http://127.0.0.1:{server.server_port}', 'normal', run_id='wrong-service')
            self.assertEqual(caught.exception.report['failure']['step_id'], 'flow_server_contract')
            self.assertEqual(posts, [])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == '__main__':
    unittest.main()
