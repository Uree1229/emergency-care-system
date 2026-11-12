"""Local guards and real synthetic API workflows; no microphone or real hospital."""
from contextlib import closing, redirect_stdout
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from server import team_readiness as readiness


def observations():
    _, digest = readiness._catalog()
    worker = dict(state='RUNNING', enabled=True, thread_alive=True)
    hospitals = [dict(hospital_id=hid, identity={'origin': {'classification': 'synthetic_identity'}},
        operating_resources=[dict(resource_type=kind, origin={'classification': 'synthetic_fixture'},
            update_freshness=dict(time_status='recent', stale=False))
            for kind in ('ed_bed', 'special_team')]) for hid in sorted(readiness.HOSPITALS)]
    return dict(zip(readiness.ENDPOINTS, [
        dict(revision='team-runtime-20261030-v1', mode='full', synthetic=True,
            baseline_id=readiness.BASELINE, inquiry_policy='fixed_3', solver_seconds=3,
            external_hospital_messages=False, travel_provider='synthetic', profile={'ok': True},
            workers=dict(planning=worker, inquiry=worker),
            database=dict(valid=True, exists=True, hospital_count=10, resources=dict(count=20, stale_count=0))),
        dict(status='OK', scope='CURRENT_PROCESS', checked_at=int(time.time()), database={'readable': True},
            workers=dict(planning=worker, inquiry=worker), data={'stale_operating_resources': 0}),
        dict(model_resources=['ed_bed', 'special_team'], public_observation_to_operating_capacity=False,
             public_observation_confirms_patient_acceptance=False, catalog={'sha256': digest}),
        dict(hospitals=hospitals, read_only=True, external_api_calls=0),
        dict(schema_version='1', transcript_input=True, automatic_diagnosis=False,
             voice_revision='voice-v2-20261030', speech={'configured': False}),
    ]))


class FakeAPI:
    def __init__(self, response=None, *, fail_post=None, first_batch=1, existing=None):
        self.origin = 'http://127.0.0.1:18050'
        self.responses = response if response is not None else observations()
        self.fail_post, self.first_batch = fail_post, first_batch
        self.existing = existing or {}
        self.calls, self.patients = [], {}

    def request(self, method, path, body=None, **kwargs):
        self.calls.append((method, path, copy.deepcopy(body)))
        if path in self.responses:
            return copy.deepcopy(self.responses[path])
        if method == 'POST':
            count = sum(call[0] == 'POST' for call in self.calls)
            if count == self.fail_post:
                raise readiness.ReadinessError('SERVER_CONNECTION_FAILED')
            value = dict(body, requested_at=int(time.time()), state='WAITING',
                inquiry_workflow=dict(enabled=True, state='QUEUED', round_no=0, policy={'name': 'fixed_3'}),
                inquiries=[])
            self.patients[body['id']] = value
            return copy.deepcopy(value)
        pid = path.rsplit('/', 1)[-1]
        if pid in self.existing:
            return copy.deepcopy(self.existing[pid])
        value = self.patients.get(pid)
        if value is None:
            return None
        value = copy.deepcopy(value)
        value['inquiry_workflow'].update(state='RUNNING', round_no=1 if self.first_batch else 0)
        value['inquiries'] = [dict(state='PENDING') for _ in range(self.first_batch)]
        return value


class ReadinessGuardTests(unittest.TestCase):
    def setUp(self):
        parent = Path(__file__).resolve().parents[2] / '.codex-build/team-readiness-tests'
        parent.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.output = self.root / 'receipt.json'

    def seed(self, client, case='general', run_id='morning'):
        with patch.object(readiness, 'APIClient', return_value=client):
            return readiness.seed_case(client.origin, case, run_id, self.output, wait_seconds=.05)

    def test_only_literal_private_https_and_loopback_origins_are_accepted(self):
        for value in ('http://127.0.0.1:18050/', 'http://localhost:18050', 'https://[::1]:8443',
                      'https://10.2.3.4', 'https://172.16.2.3:8443', 'https://192.168.0.10:8443'):
            with self.subTest(value=value):
                self.assertEqual(readiness.normalize_origin(value), value.rstrip('/'))

    def test_public_dns_credential_paths_queries_fragments_and_plain_lan_are_rejected(self):
        for value in ('https://8.8.8.8', 'https://example.com', 'https://172.32.0.1',
                      'https://169.254.1.1', 'https://0.0.0.0', 'http://192.168.1.2',
                      'http://user:secret@127.0.0.1', 'http://127.0.0.1/api',
                      'http://127.0.0.1?secret=x', 'http://127.0.0.1#x',
                      'http://127.0.0.1:0', 'http://127.0.0.1:65536',
                      'http://127.0.0.1\\@evil.test', ' http://127.0.0.1'):
            with self.subTest(value=value), self.assertRaises(readiness.ReadinessError):
                readiness.normalize_origin(value)

    def test_gateway_token_is_basic_team_and_never_in_output(self):
        token = 'SYNTHETIC_PRIVATE_TOKEN_1234567890'
        path = self.root / 'token.txt'
        path.write_text(token + '\n', encoding='ascii')
        client = readiness.APIClient('http://127.0.0.1:18050', token_file=path)
        import base64
        self.assertEqual(base64.b64decode(client.authorization.split()[1]).decode(), 'team:' + token)
        with patch.object(readiness, 'check_server', return_value={'ready': True, 'status': 'READY'}):
            stream = io.StringIO()
            with redirect_stdout(stream):
                self.assertEqual(readiness.main(['check', '--server', client.origin, '--token-file', str(path)]), 0)
        self.assertNotIn(token, stream.getvalue())

    def test_invalid_token_is_reported_without_secret_or_path(self):
        token = 'secret\nsecond-line'
        path = self.root / 'token.txt'
        path.write_text(token)
        stream = io.StringIO()
        with redirect_stdout(stream):
            code = readiness.main(['check', '--server', 'http://127.0.0.1', '--token-file', str(path)])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(stream.getvalue())['error_code'], 'INVALID_TOKEN_FILE')
        self.assertNotIn('secret', stream.getvalue())
        self.assertNotIn(str(path), stream.getvalue())

    def test_redirect_is_not_followed_or_used_to_send_credentials_elsewhere(self):
        paths = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                paths.append(self.path)
                self.send_response(302)
                self.send_header('Location', 'http://8.8.8.8/stolen')
                self.send_header('Content-Length', '0')
                self.end_headers()
            def log_message(self, *_args):
                pass
        http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        try:
            client = readiness.APIClient('http://127.0.0.1:' + str(http.server_port))
            with self.assertRaisesRegex(readiness.ReadinessError, 'REDIRECT_REJECTED'):
                client.request('GET', '/api/team-runtime')
            self.assertEqual(paths, ['/api/team-runtime'])
        finally:
            http.shutdown(); http.server_close(); thread.join(timeout=2)

    def test_preflight_performs_only_the_five_read_only_gets(self):
        client = FakeAPI()
        result = readiness.preflight(client)
        self.assertTrue(result['ready'])
        self.assertEqual([(m, p) for m, p, _ in client.calls], [('GET', p) for p in readiness.ENDPOINTS])
        self.assertFalse(result['speech_configured'])

    def test_preflight_blocks_disabled_workers_stale_or_wrong_catalog_and_wrong_scope(self):
        changes = (
            (0, lambda value: value.update(mode='voice-only'), 'common_full_runtime'),
            (0, lambda value: value.update(profile={'ok': False}), 'common_full_runtime'),
            (1, lambda value: value['workers'].update(planning={'state': 'DISABLED'}), 'workers_running'),
            (0, lambda value: value['database']['resources'].update(stale_count=1), 'fresh_operating_resources'),
            (3, lambda value: value['hospitals'][0]['operating_resources'][0]['update_freshness'].update(time_status='future'), 'fresh_operating_resources'),
            (3, lambda value: value['hospitals'][0]['identity']['origin'].update(classification='public_identity_frozen'), 'common_synthetic_catalog'),
            (2, lambda value: value.update(catalog={'sha256': '0'*64}), 'catalog_and_resource_scope'),
            (2, lambda value: value.update(public_observation_to_operating_capacity=True), 'catalog_and_resource_scope'),
            (1, lambda value: value.update(checked_at=1), 'readable_current_status'),
        )
        for index, change, expected in changes:
            values = observations()
            change(values[readiness.ENDPOINTS[index]])
            with self.subTest(expected=expected):
                result = readiness.preflight(FakeAPI(values))
                self.assertFalse(result['ready'])
                self.assertIn(expected, result['failed_checks'])

    def test_remote_failure_returns_only_fixed_diagnostic(self):
        client = FakeAPI()
        with patch.object(client, 'request', side_effect=readiness.ReadinessError('AUTHENTICATION_REQUIRED')):
            result = readiness.preflight(client)
        self.assertFalse(result['ready'])
        self.assertEqual(result['error_code'], 'AUTHENTICATION_REQUIRED')

    def test_failed_preflight_saves_a_non_creation_receipt_and_never_posts(self):
        values = observations()
        values[readiness.ENDPOINTS[0]]['synthetic'] = False
        client = FakeAPI(values)
        result = self.seed(client)
        self.assertEqual(result['status'], 'NOT_CREATED')
        self.assertEqual(result['error_code'], 'PREFLIGHT_NOT_READY')
        self.assertFalse(any(method == 'POST' for method, _, _ in client.calls))
        self.assertEqual(result, json.loads(self.output.read_text(encoding='utf-8')))

    def test_existing_output_is_preserved_before_any_network_request(self):
        self.output.write_bytes(b'preserve-original')
        client = FakeAPI()
        with self.assertRaisesRegex(readiness.ReadinessError, 'OUTPUT_ALREADY_EXISTS'):
            self.seed(client)
        self.assertEqual(self.output.read_bytes(), b'preserve-original')
        self.assertEqual(client.calls, [])

    def test_preexisting_second_patient_prevents_both_simultaneous_creates(self):
        client = FakeAPI(existing={'TEAM-morning-B': dict(id='TEAM-morning-B', state='WAITING')})
        result = self.seed(client, case='simultaneous')
        self.assertEqual(result['error_code'], 'PATIENT_ALREADY_EXISTS')
        self.assertFalse(any(method == 'POST' for method, _, _ in client.calls))
        self.assertEqual(result['patients'][1]['submission_state'], 'EXISTING_NOT_CHANGED')

    def test_partial_post_failure_retains_created_id_and_uncertain_second_id_without_retry(self):
        client = FakeAPI(fail_post=2)
        result = self.seed(client, case='simultaneous')
        self.assertEqual(result['status'], 'PARTIAL_OR_UNCERTAIN')
        self.assertEqual(result['patients'][0]['submission_state'], 'CREATED')
        self.assertEqual(result['patients'][0]['patient_id'], 'TEAM-morning-A')
        self.assertEqual(result['patients'][1]['submission_state'], 'POST_OUTCOME_UNKNOWN')
        self.assertEqual(sum(m == 'POST' for m, _, _ in client.calls), 2)
        self.assertEqual(result, json.loads(self.output.read_text(encoding='utf-8')))

    def test_first_post_uncertainty_is_durable_and_is_never_retried(self):
        client = FakeAPI(fail_post=1)
        result = self.seed(client)
        self.assertEqual(result['patients'][0]['submission_state'], 'POST_OUTCOME_UNKNOWN')
        self.assertEqual(result['patients'][0]['requested_patient_id'], 'TEAM-morning-A')
        self.assertEqual(sum(m == 'POST' for m, _, _ in client.calls), 1)

    def test_one_eligible_hospital_is_a_valid_first_batch_and_catalog_drives_the_payload(self):
        client = FakeAPI(first_batch=1)
        result = self.seed(client, case='cardiac')
        self.assertEqual(result['status'], 'READY_FOR_MANUAL_RESPONSE')
        payload = next(body for method, _, body in client.calls if method == 'POST')
        profiles, _ = readiness._catalog()
        profile = profiles['TR_CARDIAC_INTERVENTION']
        self.assertEqual(payload['required_capabilities'], profile['required_capabilities'])
        self.assertEqual(payload['resource_demands'], profile['resource_demands'])
        self.assertEqual(payload['inquiry_policy'], 'fixed_3')
        self.assertTrue(payload['auto_inquiry'])
        self.assertEqual(result['patients'][0]['inquiry_state_counts'], {'PENDING': 1})
        self.assertNotIn('symptoms', self.output.read_text(encoding='utf-8'))
        self.assertTrue(all(path == '/api/patients' for method, path, _ in client.calls if method == 'POST'))

    def test_no_first_batch_times_out_and_keeps_the_created_patient_receipt(self):
        client = FakeAPI(first_batch=0)
        start = time.monotonic()
        result = self.seed(client)
        self.assertLess(time.monotonic() - start, 1)
        self.assertEqual(result['error_code'], 'INITIAL_BATCH_TIMEOUT')
        self.assertEqual(result['patients'][0]['submission_state'], 'CREATED')

    def test_invalid_run_id_fails_before_creating_receipt_or_network_calls(self):
        for value in ('한글', '../escape', '', 'space here', 'x'*41):
            client = FakeAPI()
            with self.subTest(value=value), self.assertRaisesRegex(readiness.ReadinessError, 'INVALID_RUN_ID'):
                self.seed(client, run_id=value)
            self.assertFalse(self.output.exists())
            self.assertEqual(client.calls, [])


class RealReadinessHTTPTests(unittest.TestCase):
    def setUp(self):
        from server.team_runtime import CommonRuntime
        parent = Path(__file__).resolve().parents[2] / '.codex-build/team-readiness-tests'
        parent.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / 'synthetic.db'
        # This is a feature connection test, never a frozen benchmark claim.
        self.runtime = CommonRuntime(self.path, port=0, mode='full', allow_environment_difference=True)
        self.addCleanup(self.runtime.close)
        self.runtime.start()
        until = time.monotonic() + 12
        while time.monotonic() < until:
            if readiness.check_server(self.runtime.base_url)['ready']:
                break
            time.sleep(.05)
        else:
            self.fail('Actual common-runtime workers did not become ready')

    def business(self):
        with closing(sqlite3.connect(self.path)) as conn:
            return {name: conn.execute('SELECT * FROM ' + name + ' ORDER BY rowid').fetchall()
                    for name in ('patients', 'resources', 'inquiries', 'events', 'voice_sessions')}

    def test_real_preflight_preserves_business_rows_and_does_not_refresh_resources(self):
        before = self.business()
        result = readiness.check_server(self.runtime.base_url)
        self.assertTrue(result['ready'])
        self.assertEqual(before, self.business())

    def test_real_simultaneous_cases_reach_first_batch_without_responses_or_confirmation(self):
        output = self.root / 'receipt.json'
        result = readiness.seed_case(self.runtime.base_url, 'simultaneous', 'real-http', output)
        self.assertEqual(result['status'], 'READY_FOR_MANUAL_RESPONSE')
        client = readiness.APIClient(self.runtime.base_url)
        self.assertEqual(len(result['patients']), 2)
        for entry, profile in zip(result['patients'], ('TR_GENERAL_ED', 'TR_CARDIAC_INTERVENTION')):
            patient = client.request('GET', '/api/patients/' + entry['patient_id'])
            self.assertEqual(patient['state'], 'WAITING')
            self.assertIsNone(patient['destination_hospital_id'])
            self.assertTrue(patient['inquiry_workflow']['enabled'])
            self.assertEqual(patient['extra']['treatment_profile_id'], profile)
            self.assertTrue(1 <= len(patient['inquiries']) <= 3)
            self.assertTrue(all(q['state'] == 'PENDING' for q in patient['inquiries']))
            self.assertGreaterEqual(patient['requested_at'], result['started_at'])
        before = self.business()
        duplicate = readiness.seed_case(self.runtime.base_url, 'simultaneous', 'real-http', self.root / 'duplicate.json')
        self.assertEqual(duplicate['error_code'], 'PATIENT_ALREADY_EXISTS')
        self.assertEqual(before, self.business())


if __name__ == '__main__':
    unittest.main()
