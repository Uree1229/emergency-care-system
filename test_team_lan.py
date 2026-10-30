"""Actual local TLS/CA gateway tests; only synthetic disposable fixture servers."""
import base64
from contextlib import closing, redirect_stdout
from copy import deepcopy
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import shutil
import socket
import ssl
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from server import team_lan as lan


FULL = dict(revision='team-runtime-20261030-v1', mode='full', synthetic=True,
    baseline_id=lan.BASELINE, travel_provider='synthetic', external_hospital_messages=False,
    solver_seconds=3, inquiry_policy='fixed_3', database={'valid': True},
    profile=dict(ok=True, profile_id='team-runtime-profile-20261030-v1', mode='full', source_count=21,
                 source_equivalence='exact_bytes'),
    workers={name: dict(enabled=True, state='RUNNING', thread_alive=True) for name in ('planning', 'inquiry')})


def free_port():
    with socket.socket() as connection:
        connection.bind(('127.0.0.1', 0))
        return connection.getsockname()[1]


class AuthorityTests(unittest.TestCase):
    def test_https_default_443_authority_and_origin_normalize_without_binding_privileged_port(self):
        for host in ('192.168.30.2', '192.168.30.2:443'):
            authority = lan._allowed_authority(host, '192.168.30.2', 443)
            self.assertEqual(authority, ('192.168.30.2', 443))
            for origin in ('https://192.168.30.2', 'https://192.168.30.2:443'):
                self.assertTrue(lan._same_origin(origin, authority))
            for origin in ('http://192.168.30.2', 'https://192.168.30.2:18443',
                           'https://elsewhere.invalid', 'https://team:secret@192.168.30.2'):
                self.assertFalse(lan._same_origin(origin, authority))
        self.assertIsNone(lan._allowed_authority('192.168.30.2', '192.168.30.2', 18443))
        self.assertEqual(lan._allowed_authority('192.168.30.2:18443', '192.168.30.2', 18443),
                         ('192.168.30.2', 18443))


class TeamLanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        parent = Path(__file__).resolve().parent / '.codex-build/team-lan-20261102/tmp'
        parent.mkdir(parents=True, exist_ok=True)
        cls.tmp = tempfile.TemporaryDirectory(prefix='https-team-', dir=parent)
        cls.root = Path(cls.tmp.name)
        cls.bundle = cls.root / 'bundle'
        lan.prepare('127.0.0.1', cls.bundle)
        cls.token = (cls.bundle/'access-token.txt').read_text(encoding='ascii').strip()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.metadata = deepcopy(FULL)
        self.forwarded = []
        self.audio_delay = 0
        self.gateway = self.gateway_thread = None
        owner = self
        class Echo(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def respond(self):
                body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
                owner.forwarded.append(dict(method=self.command, path=self.path, body=body,
                                            headers={key.lower(): value for key,value in self.headers.items()}))
                if self.path.endswith('/audio') and owner.audio_delay:
                    time.sleep(owner.audio_delay)
                if self.path == '/api/team-runtime':
                    data, content_type = json.dumps(owner.metadata).encode(), 'application/json'
                elif self.path == '/static-fixture':
                    data, content_type = b'<html>synthetic fixture</html>', 'text/html'
                else:
                    data, content_type = json.dumps({'forwarded': True}).encode(), 'application/json'
                self.send_response(200)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Connection', 'X-Hop-Response')
                self.send_header('X-Hop-Response', 'drop')
                self.send_header('Set-Cookie', 'fixture=drop')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = do_PATCH = do_OPTIONS = respond
        self.upstream = ThreadingHTTPServer(('127.0.0.1', 0), Echo)
        self.upstream_thread = threading.Thread(target=self.upstream.serve_forever, kwargs={'poll_interval': .02})
        self.upstream_thread.start()

    def tearDown(self):
        if self.gateway is not None:
            self.gateway.shutdown()
            self.gateway_thread.join(timeout=3)
            self.gateway.server_close()
        self.upstream.shutdown()
        self.upstream_thread.join(timeout=3)
        self.upstream.server_close()

    def start_gateway(self, upstream_port=None):
        self.gateway = lan.make_gateway(self.bundle, upstream_port or self.upstream.server_port, port=free_port())
        self.port = self.gateway.server_port
        self.gateway_thread = threading.Thread(target=self.gateway.serve_forever, kwargs={'poll_interval': .02})
        self.gateway_thread.start()

    def request(self, method='GET', path='/api/system/status', body=None, headers=None, authenticated=True, timeout=5):
        values = {'Host': f'127.0.0.1:{self.port}'}
        if authenticated:
            values['Authorization'] = 'Basic '+base64.b64encode(('team:'+self.token).encode()).decode()
        values.update(headers or {})
        context = ssl.create_default_context(cafile=str(self.bundle/'rootCA.crt'))
        with closing(http.client.HTTPSConnection('127.0.0.1', self.port, context=context, timeout=timeout)) as connection:
            connection.request(method, path, body=body, headers=values)
            response = connection.getresponse()
            return response.status, dict((k.lower(),v) for k,v in response.getheaders()), response.read()

    def clone_bundle(self, name):
        return Path(shutil.copytree(self.bundle, self.root/name))

    def test_prepare_new_bundle_contains_san_ca_private_files_and_git_exclusion_without_secret_output(self):
        output = self.root / 'separate-prepare'
        with redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(lan.main(['prepare', '--ip', '192.168.30.2', '--output', str(output)]), 0)
        result = json.loads(stdout.getvalue())
        self.assertTrue(result['prepared'])
        token = (output/'access-token.txt').read_text(encoding='ascii').strip()
        self.assertRegex(token, r'^[A-Za-z0-9_-]{32}$')
        self.assertNotIn(token, stdout.getvalue())
        self.assertNotIn(token, (output/'manifest.json').read_text())
        self.assertEqual((output/'.gitignore').read_text(), '*\n')
        self.assertTrue(all((output/name).is_file() for name in lan.FILES))
        decoded = ssl._ssl._test_decode_cert(str(output/'server.crt'))
        self.assertIn(('IP Address', '192.168.30.2'), decoded['subjectAltName'])
        self.assertIn(('IP Address', '127.0.0.1'), decoded['subjectAltName'])
        self.assertIn(('DNS', 'localhost'), decoded['subjectAltName'])

    def test_existing_output_is_preserved_and_invalid_networks_do_not_create_output(self):
        before = {path.name: path.read_bytes() for path in self.bundle.iterdir()}
        with self.assertRaises(lan.LanError):
            lan.prepare('127.0.0.1', self.bundle)
        self.assertEqual(before, {path.name:path.read_bytes() for path in self.bundle.iterdir()})
        for index, ip in enumerate(('8.8.8.8', '169.254.1.2', '0.0.0.0', '127.0.0.2', '::1', 'bad-ip')):
            output = self.root / ('invalid-ip-'+str(index))
            with self.subTest(ip=ip), self.assertRaises(lan.LanError):
                lan.prepare(ip, output)
            self.assertFalse(output.exists())

    def test_missing_openssl_does_not_create_output(self):
        output = self.root/'no-openssl'
        with self.assertRaises(lan.LanError):
            lan.prepare('127.0.0.1', output, openssl=str(self.root/'missing-openssl.exe'))
        self.assertFalse(output.exists())

    def test_bundle_is_never_created_inside_web_served_roots(self):
        root = Path(lan.__file__).resolve().parents[1]
        for name in ('server/static', 'patient-ui', 'hospital-ui', 'paramedic-ui'):
            output = root/name/'must-not-create-lan-private-bundle'
            with self.subTest(root=name), self.assertRaises(lan.LanError):
                lan.prepare('127.0.0.1', output)
            self.assertFalse(output.exists())

    def test_manifest_cannot_change_upstream_or_runtime_mode_or_reference_outside_bundle(self):
        for index, changes in enumerate(({'upstream_host':'192.168.1.2'}, {'runtime_mode':'voice-only'},
                                         {'replay':True}, {'key':'../external.key'}, {'version':True})):
            bundle = self.clone_bundle('invalid-manifest-'+str(index))
            path = bundle/'manifest.json'
            value = json.loads(path.read_text())
            value.update(changes)
            path.write_text(json.dumps(value))
            with self.subTest(changes=changes), self.assertRaises(lan.LanError):
                lan.load_bundle(bundle)

    def test_certificate_san_must_cover_manifest_ip_before_any_bind(self):
        bundle = self.clone_bundle('wrong-cert-ip')
        path = bundle/'manifest.json'
        value = json.loads(path.read_text())
        value['ip'] = '192.168.30.77'
        path.write_text(json.dumps(value))
        with patch.object(lan, 'Gateway', side_effect=AssertionError('Must reject before binding')):
            with self.assertRaises(lan.LanError):
                lan.make_gateway(bundle, self.upstream.server_port, port=free_port())

    def test_leaf_certificate_signed_by_another_ca_is_rejected_before_any_bind(self):
        alternate = self.root/'alternate-ca-bundle'
        lan.prepare('127.0.0.1', alternate)
        bundle = self.clone_bundle('wrong-ca-leaf')
        for name in ('server.crt', 'server.key'):
            shutil.copyfile(alternate/name, bundle/name)
        # The swapped leaf and key match each other, but do not chain to this bundle's CA.
        with patch.object(lan, 'Gateway', side_effect=AssertionError('Must reject before binding')):
            with self.assertRaises(lan.LanError):
                lan.make_gateway(bundle, self.upstream.server_port, port=free_port())

    def test_actual_tls_verifies_ca_hostname_and_requires_authentication(self):
        self.start_gateway()
        status, headers, _ = self.request(authenticated=False)
        self.assertEqual(status, 401)
        self.assertIn('basic', headers['www-authenticate'].lower())
        self.assertEqual(self.request(headers={'Authorization':'Basic invalid!'})[0], 401)
        self.assertEqual(self.request()[0], 200)
        with self.assertRaises(ssl.SSLCertVerificationError):
            with closing(http.client.HTTPSConnection('127.0.0.1', self.port, timeout=3)) as connection:
                connection.request('GET','/')
        context = ssl.create_default_context(cafile=str(self.bundle/'rootCA.crt'))
        with socket.create_connection(('127.0.0.1', self.port), timeout=3) as connection:
            with self.assertRaises(ssl.SSLCertVerificationError):
                context.wrap_socket(connection, server_hostname='192.168.99.99')

    def test_tls_minimum_and_ca_auth_are_not_plain_http(self):
        self.start_gateway()
        self.assertEqual(self.gateway.tls_context.minimum_version, ssl.TLSVersion.TLSv1_2)
        with closing(http.client.HTTPConnection('127.0.0.1', self.port, timeout=3)) as connection:
            with self.assertRaises((OSError, http.client.HTTPException)):
                connection.request('GET', '/')
                connection.getresponse()

    def test_same_origin_enforced_without_adding_cors_permission(self):
        self.start_gateway()
        before = len(self.forwarded)
        self.assertEqual(self.request(headers={'Origin':'https://elsewhere.invalid'})[0], 403)
        self.assertEqual(self.request(headers={'Origin':'null'})[0], 403)
        self.assertEqual(self.request(headers={'Sec-Fetch-Site':'cross-site'})[0], 403)
        self.assertEqual(self.request(headers={'Host':'elsewhere.invalid'})[0], 400)
        self.assertEqual(before, len(self.forwarded))
        status, headers, _ = self.request(headers={'Origin':f'https://127.0.0.1:{self.port}'})
        self.assertEqual(status, 200)
        self.assertNotIn('access-control-allow-origin', headers)

    def test_body_length_limits_and_transfer_encoding_are_rejected_before_forward(self):
        self.start_gateway()
        before = len(self.forwarded)
        self.assertEqual(self.request('POST', headers={'Content-Length':str(lan.MAX_BODY+1)})[0], 413)
        self.assertEqual(self.request('POST', headers={'Content-Length':'-1'})[0], 400)
        self.assertEqual(self.request('POST', headers={'Transfer-Encoding':'chunked'})[0], 400)
        self.assertEqual(before, len(self.forwarded))

    def test_authenticated_static_json_and_mutation_relay_strips_auth_cookies_hop_headers(self):
        self.start_gateway()
        self.assertEqual(self.request(path='/static-fixture')[2], b'<html>synthetic fixture</html>')
        status, response_headers, content = self.request('POST', '/api/fixture?case=synthetic', b'{"fixture":true}',
            headers={'Content-Type':'application/json', 'Cookie':'private=must-not-forward',
                'Connection':'X-Hop-Request', 'X-Hop-Request':'drop', 'Proxy-Authorization':'drop',
                'Forwarded':'drop', 'X-Forwarded-Host':'drop', 'X-Forwarded-Proto':'http',
                'Origin':f'https://127.0.0.1:{self.port}'})
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(content)['forwarded'])
        actual = self.forwarded[-1]
        self.assertEqual(actual['method'], 'POST')
        self.assertEqual(actual['body'], b'{"fixture":true}')
        self.assertEqual(actual['path'], '/api/fixture?case=synthetic')
        self.assertEqual(actual['headers']['host'], f'127.0.0.1:{self.upstream.server_port}')
        self.assertEqual(actual['headers']['origin'], f'https://127.0.0.1:{self.port}')
        self.assertEqual(response_headers['cache-control'], 'no-store')
        self.assertEqual(response_headers['x-content-type-options'], 'nosniff')
        self.assertEqual(response_headers['referrer-policy'], 'no-referrer')
        self.assertIn('max-age=', response_headers['strict-transport-security'])
        for name in ('authorization','proxy-authorization','cookie','x-hop-request','forwarded','x-forwarded-host','x-forwarded-proto'):
            self.assertNotIn(name, actual['headers'])
        for name in ('x-hop-response','set-cookie','access-control-allow-origin'):
            self.assertNotIn(name, response_headers)

    def test_delayed_audio_upstream_response_beyond_ten_seconds_is_relayed_over_actual_tls(self):
        self.start_gateway()
        self.audio_delay = 11
        body = b'{"utterance_id":"synthetic-delay","audio_base64":"synthetic-no-audio-provider"}'
        status, _, content = self.request('POST', '/api/voice/sessions/VS-SYNTHETIC/audio', body,
            headers={'Content-Type':'application/json', 'Origin':f'https://127.0.0.1:{self.port}'}, timeout=25)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(content), {'forwarded': True})
        self.assertEqual(self.forwarded[-1]['body'], body)
        self.assertEqual(self.forwarded[-1]['path'], '/api/voice/sessions/VS-SYNTHETIC/audio')

    def test_wrong_upstream_configuration_and_non_full_metadata_fail_closed(self):
        for field, value in (('mode','voice-only'), ('travel_provider','kakao'), ('inquiry_policy','fixed_5'),
                             ('synthetic',False), ('external_hospital_messages',True), ('profile',{'ok':False}),
                             ('database',{'valid':False}), ('workers',{})):
            self.metadata = deepcopy(FULL)
            self.metadata[field] = value
            with self.subTest(field=field), self.assertRaises(lan.LanError):
                lan.make_gateway(self.bundle, self.upstream.server_port, port=free_port())
        with self.assertRaises(lan.LanError):
            lan.make_gateway(self.bundle, self.upstream.server_port, port=self.upstream.server_port)
        with self.assertRaises(lan.LanError):
            lan.check_upstream(free_port())

    def test_upstream_failure_and_invalid_path_errors_do_not_expose_request_values(self):
        self.start_gateway()
        self.assertEqual(self.request(path='http://elsewhere.invalid/')[0], 400)
        self.upstream.shutdown()
        self.upstream_thread.join(timeout=3)
        self.upstream.server_close()
        # Server reset is deliberately a synthetic transport failure.
        status, _, content = self.request(path='/api/fixture?private=synthetic-secret')
        self.assertEqual(status, 502)
        self.assertNotIn(b'synthetic-secret', content)
        self.assertNotIn(self.token.encode(), content)

    def test_secret_symlink_is_rejected_when_os_allows_link_creation(self):
        bundle = self.clone_bundle('symlink-bundle')
        token_path = bundle/'access-token.txt'
        token_path.unlink()
        try:
            token_path.symlink_to(self.bundle/'access-token.txt')
        except OSError:
            self.skipTest('Creating symlinks requires Windows developer mode or a privileged token.')
        with self.assertRaises(lan.LanError):
            lan.load_bundle(bundle)

    def test_actual_common_runtime_full_metadata_passes_through_verified_tls(self):
        from server.team_runtime import CommonRuntime
        db_path = self.root/'actual-full-runtime.db'
        try:
            runtime = CommonRuntime(db_path, mode='full', port=0).start()
        except ValueError as error:
            self.fail('Install requirements-server.txt before the actual full runtime gateway test: '+str(error))
        try:
            until = time.monotonic()+8
            while True:
                try:
                    lan.check_upstream(runtime.http.server_port)
                    break
                except lan.LanError:
                    if time.monotonic() >= until:
                        raise
                    time.sleep(.05)
            self.start_gateway(runtime.http.server_port)
            status, _, content = self.request(path='/api/team-runtime')
            self.assertEqual(status, 200)
            metadata = json.loads(content)
            self.assertEqual(metadata['mode'], 'full')
            self.assertTrue(all(metadata['workers'][name]['state']=='RUNNING'
                                for name in ('planning','inquiry')))
            self.assertEqual(self.request(path='/api/voice/capabilities')[0], 200)
            self.assertEqual(self.request(path='/api/system/status')[0], 200)
        finally:
            # Gateway shuts down first, then the upstream keeps its original DB lease lifecycle.
            if self.gateway is not None:
                self.gateway.shutdown()
                self.gateway_thread.join(timeout=3)
                self.gateway.server_close()
                self.gateway = None
            runtime.close()


if __name__ == '__main__':
    unittest.main()
