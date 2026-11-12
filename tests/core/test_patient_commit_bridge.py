"""Team patient profile → common server interoperability, using disposable data only."""
import importlib.util
import json
import tempfile
import threading
import unittest
import urllib.request
import uuid
from datetime import date, datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from server import db, service
from server.app import make_handler
from server.travel import TravelEstimator


MODULE_PATH = Path(__file__).resolve().parents[2] / 'emergency-app' / 'common_server.py'
SPEC = importlib.util.spec_from_file_location('patient_common_bridge', MODULE_PATH)
bridge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bridge)
PROFILE = (7, '테스트 환자', '2010-04-17', '010-0000-0000',
           '가상 주소', '테스트 보호자', '010-0000-0001')


def payload(**changes):
    values = dict(reporter_role='guardian', consciousness='yes', breathing='yes',
                  symptoms='기타', required_capabilities=['응급의학과'], special_team=False,
                  lat=35.16, lon=126.85, consent=True)
    values.update(changes)
    return bridge.build_episode_payload(PROFILE, **values)


class IntakeContract(unittest.TestCase):
    def test_unknown_and_abnormal_answers_are_not_invented_as_absent(self):
        unknown = payload(consciousness='', breathing='unknown')
        self.assertEqual((unknown['consciousness'], unknown['breathing']), ('unknown', 'unknown'))
        answer = payload(consciousness='no', breathing='no')
        self.assertEqual((answer['consciousness'], answer['breathing']), ('unresponsive', 'abnormal'))
        self.assertNotEqual(answer['breathing'], 'absent')
        self.assertIsNone(answer['priority'])
        self.assertEqual(answer['priority_source'], 'unknown')

    def test_profile_snapshot_keeps_birthday_contact_and_guardian_metadata(self):
        result = payload()
        today = datetime.now(timezone(timedelta(hours=9))).date()
        born = date.fromisoformat(PROFILE[2])
        expected_age = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
        self.assertEqual((result['birth'], result['age']), (PROFILE[2], expected_age))
        self.assertEqual([result[k] for k in ('name', 'contact', 'address', 'guardian', 'guardian_phone')],
                         [PROFILE[i] for i in (1, 3, 4, 5, 6)])
        self.assertEqual(result['extra']['source_app'], 'emergency-app@945c413')
        self.assertEqual(result['extra']['source_profile_id'], PROFILE[0])

    def test_missing_or_invalid_legacy_birthdays_stay_unknown(self):
        values = dict(reporter_role='self', consciousness='', breathing='', symptoms='',
                      required_capabilities=[], special_team=False, lat=35.16, lon=126.85, consent=True)
        for birth in ('', None, 'wrong-date', '2035-01-01', '20100417'):
            with self.subTest(birth=birth):
                profile = (PROFILE[0], PROFILE[1], birth, *PROFILE[3:])
                result = bridge.build_episode_payload(profile, **values)
                self.assertIsNone(result['birth'])
                self.assertIsNone(result['age'])
        unregistered = bridge.build_episode_payload(None, **values)
        self.assertIsNone(unregistered['name'])
        self.assertIsNone(unregistered['birth'])
        self.assertIsNone(unregistered['extra']['source_profile_id'])

    def test_invalid_consent_location_and_reporter_are_rejected_before_network(self):
        invalid = [dict(consent=False), dict(consent='true'), dict(consent=1),
                   dict(lat=None), dict(lat=float('nan')), dict(lon=float('inf')),
                   dict(lat=91), dict(lon=-181), dict(lat=True), dict(reporter_role='hospital')]
        with patch.object(bridge, 'urlopen') as network:
            for change in invalid:
                with self.subTest(change=change), self.assertRaises(bridge.CommonServerError):
                    payload(**change)
            network.assert_not_called()

    def test_base_url_validation_and_transport_query_encoding(self):
        with patch.dict('os.environ', {}, clear=True):
            self.assertEqual(bridge.common_server_base(), 'http://127.0.0.1:18020')
        for value in ('ftp://example.com', 'http://user:password@example.com', 'http://localhost:99999',
                      'http://localhost/api', 'http://localhost?key=value', 'http://localhost#fragment'):
            with self.subTest(value=value), patch.dict('os.environ', {'COMMON_API_URL': value}):
                with self.assertRaises(bridge.CommonServerError) as raised:
                    bridge.common_server_base()
                self.assertEqual(raised.exception.code, 'CONFIG')
        identifier = '환자 A & B/?#'
        with patch.dict('os.environ', {'COMMON_API_URL': 'https://example.com/'}):
            parsed = urlsplit(bridge.transport_url(identifier))
        self.assertEqual((parsed.scheme, parsed.netloc, parsed.path), ('https', 'example.com', '/patient/'))
        self.assertEqual(parse_qs(parsed.query), {'pid': [identifier]})

    def test_invalid_submission_identifier_and_missing_consent_do_not_send(self):
        with patch.object(bridge, 'urlopen') as network:
            for bad_token in ('', None, 'not-a-uuid'):
                with self.subTest(token=bad_token), self.assertRaises(bridge.CommonServerError):
                    bridge.create_episode(payload(), bad_token)
            outgoing = payload()
            outgoing.pop('consent')
            with self.assertRaises(bridge.CommonServerError) as raised:
                bridge.create_episode(outgoing, str(uuid.uuid4()))
            self.assertEqual(raised.exception.code, 'CONSENT_REQUIRED')
            network.assert_not_called()


class BridgeHttpCase(unittest.TestCase):
    """Real JSON routes, isolated SQLite; no planner worker or medical API calls."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / 'bridge.db')
        self.conn = db.connect(self.path)
        db.init(self.conn)
        service.upsert_hospital(self.conn, dict(
            id='BRIDGE-H', name='가상 병원', lat=35.17, lon=126.86,
            capabilities=['응급의학과'], source='synthetic'))
        service.set_resource(self.conn, 1_790_000_000, 'BRIDGE-H', 'ed_bed', 8, 0,
                             source='public_observation')
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(
            self.path, TravelEstimator(use_kakao=False), clock=lambda: 1_790_000_001))
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.http.server_port}'
        self.env = patch.dict('os.environ', {'COMMON_API_URL': self.base})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.http.shutdown()
        self.http.server_close()
        self.thread.join(2)
        self.conn.close()
        self.tmp.cleanup()

    def get(self, endpoint):
        with urllib.request.urlopen(self.base + endpoint, timeout=3) as response:
            return json.load(response)

    def counts(self):
        return tuple(self.conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                     for table in ('patients', 'inquiry_workflows', 'events'))

    def test_real_create_starts_one_workflow_and_preserves_unknown_priority_and_profile(self):
        result = bridge.create_episode(payload(special_team=True, breathing='no'), str(uuid.uuid4()))
        saved = self.get('/api/patients/' + result['id'])
        self.assertEqual((saved['state'], saved['priority'], saved['priority_source']),
                         ('WAITING', None, 'unknown'))
        self.assertEqual(saved['effective_priority'], 4)
        self.assertTrue(saved['call_119'])
        self.assertEqual(saved['resource_demands'], {'ed_bed': 1, 'special_team': 1})
        self.assertEqual(saved['required_capabilities'], ['응급의학과'])
        self.assertEqual((saved['name'], saved['birth'], saved['guardian_phone']),
                         (PROFILE[1], PROFILE[2], PROFILE[6]))
        self.assertEqual((saved['inquiry_workflow']['policy_name'], saved['inquiry_workflow']['enabled']),
                         ('fixed_3', 1))
        self.assertEqual(saved['extra']['source_profile_id'], 7)
        self.assertEqual(self.counts()[:2], (1, 1))

    def test_repeat_post_returns_same_episode_without_duplicate_events_or_workflow(self):
        token = str(uuid.uuid4())
        first = bridge.create_episode(payload(), token)
        before = self.counts()
        repeat = bridge.create_episode(payload(), token)
        self.assertEqual(first['id'], repeat['id'])
        self.assertEqual(self.counts(), before)
        events = service.events_since(self.conn, patient_id=first['id'])
        self.assertEqual(sum(e['type'] == 'patient_created' for e in events), 1)
        self.assertEqual(sum(e['type'] == 'inquiry_workflow_started' for e in events), 1)

    def test_changed_payload_same_token_is_conflict_and_keeps_original_episode(self):
        token = str(uuid.uuid4())
        first = bridge.create_episode(payload(symptoms='처음 증상'), token)
        before = self.counts()
        with self.assertRaises(bridge.CommonServerError) as raised:
            bridge.create_episode(payload(symptoms='바뀐 증상'), token)
        self.assertEqual((raised.exception.status, raised.exception.code), (409, 'SUBMISSION_CONFLICT'))
        self.assertEqual(self.counts(), before)
        self.assertEqual(self.get('/api/patients/' + first['id'])['symptoms'], '처음 증상')

    def test_lost_post_response_recovers_committed_episode_with_one_read(self):
        real_open = bridge.urlopen
        requests = []

        def lose_response(request, **kwargs):
            requests.append((request.get_method(), urlsplit(request.full_url).path))
            if request.get_method() == 'POST':
                with real_open(request, **kwargs) as response:
                    response.read()  # The server committed; only the reply was lost.
                raise TimeoutError('simulated lost response')
            return real_open(request, **kwargs)

        with patch.object(bridge, 'urlopen', side_effect=lose_response):
            result = bridge.create_episode(payload(), str(uuid.uuid4()))
        self.assertEqual([method for method, _ in requests], ['POST', 'GET'])
        self.assertEqual(result['state'], 'WAITING')
        self.assertEqual(self.counts()[:2], (1, 1))
        self.assertEqual(sum(e['type'] == 'patient_created'
                             for e in service.events_since(self.conn, patient_id=result['id'])), 1)

    def test_catalog_uses_only_common_server_get_and_preserves_resource_source(self):
        real_open = bridge.urlopen
        requests = []

        def inspect_request(request, **kwargs):
            parsed = urlsplit(request.full_url)
            requests.append((request.get_method(), parsed.hostname, parsed.path))
            self.assertEqual(parsed.netloc, urlsplit(self.base).netloc)
            return real_open(request, **kwargs)

        before = self.counts()
        with patch.object(bridge, 'urlopen', side_effect=inspect_request):
            hospitals = bridge.hospital_catalog()
        self.assertEqual(requests, [('GET', '127.0.0.1', '/api/hospitals')])
        self.assertEqual(len(hospitals), 1)
        self.assertEqual(hospitals[0]['id'], 'BRIDGE-H')
        self.assertEqual(hospitals[0]['resources'][0]['source'], 'public_observation')
        self.assertEqual(self.counts(), before)

    def test_failed_transport_does_not_fallback_to_external_apis_or_duplicate_post(self):
        with patch.object(bridge, 'urlopen', side_effect=TimeoutError('offline')) as network:
            with self.assertRaises(bridge.CommonServerError) as raised:
                bridge.create_episode(payload(), str(uuid.uuid4()))
        self.assertEqual((raised.exception.status, raised.exception.code), (503, 'CONNECTION_FAILED'))
        self.assertEqual([call.args[0].get_method() for call in network.call_args_list], ['POST', 'GET'])
        self.assertTrue(all(call.args[0].full_url.startswith(self.base + '/api/patients')
                            for call in network.call_args_list))
        self.assertEqual(self.counts()[:2], (0, 0))


if __name__ == '__main__':
    unittest.main()
