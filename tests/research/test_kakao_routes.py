"""Offline Kakao protocol/security/road-input tests; no real API calls."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from simulation.kakao_routes import KakaoRouteProvider, snapshot_summary
from simulation.map_replay import RouteSnapshot
from simulation.road_online_replay import build_road_scenarios, generate_road_online_replay, main
from simulation.road_routes import RoadRouteError
from test_road_online_replay import RoadFixture, catalog, policy


ORIGIN = [37., 127.]
DESTINATION = [37.02, 127.02]
FAKE_KEY = 'offline-fixture-not-a-real-key'


def response():
    return {'routes': [dict(result_code=0, result_msg='길찾기 성공',
        summary=dict(origin=dict(x=127., y=37.), destination=dict(x=127.02, y=37.02),
                     duration=200, distance=3000),
        sections=[dict(roads=[dict(vertexes=[127., 37., 127.01, 37.])]),
                  dict(roads=[dict(vertexes=[127.01, 37., 127.02, 37.02])])])]}


class FakeOpener:
    def __init__(self, value=None):
        self.value = response() if value is None else value
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append(request)
        return BytesIO(json.dumps(self.value).encode())


class KakaoRouteTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[2] / '.codex-build')
        self.addCleanup(self.directory.cleanup)

    def provider(self, value=None, **kwargs):
        opener = FakeOpener(value)
        return KakaoRouteProvider(cache_dir=self.directory.name, key=FAKE_KEY,
                                  opener=opener, **kwargs), opener

    def test_wire_options_and_coordinates_and_separate_sections(self):
        provider, opener = self.provider()
        result = provider.route(ORIGIN, DESTINATION)
        request = opener.requests[0]
        query = parse_qs(urlsplit(request.full_url).query)
        self.assertEqual(query['origin'], ['127.000000,37.000000'])
        self.assertEqual(query['destination'], ['127.020000,37.020000'])
        self.assertEqual(query['summary'], ['false'])
        self.assertEqual(query['priority'], ['TIME'])
        self.assertNotIn(FAKE_KEY, request.full_url)
        self.assertEqual(request.get_header('Authorization'), f'KakaoAK {FAKE_KEY}')
        self.assertEqual(result['geometry'], [[37., 127.], [37., 127.01], [37.02, 127.02]])
        self.assertEqual(result['duration_seconds'], 200.)
        self.assertEqual(result['distance_meters'], 3000.)
        self.assertEqual(result['provider'], 'kakao')
        self.assertEqual(result['geometry_kind'], 'road')
        self.assertTrue(result['traffic_snapshot'])
        self.assertFalse(result['live_traffic'])
        self.assertFalse(result['ambulance_priority'])

    def test_cache_has_no_key_and_reuses_independent_copy_offline(self):
        provider, opener = self.provider()
        result = provider.route(ORIGIN, DESTINATION)
        expected = deepcopy(result)
        result['geometry'][0][0] = -1
        self.assertEqual(provider.route(ORIGIN, DESTINATION), expected)
        self.assertEqual(len(opener.requests), 1)
        offline = KakaoRouteProvider(cache_dir=self.directory.name, key='', offline=True)
        self.assertEqual(offline.route(ORIGIN, DESTINATION), expected)
        cache = next(Path(self.directory.name).glob('[0-9a-f]' * 64 + '.json'))
        self.assertNotIn(FAKE_KEY, cache.read_text(encoding='utf-8'))
        self.assertNotIn('Authorization', cache.read_text(encoding='utf-8'))

    def test_expired_cache_is_not_reused_as_new_traffic_or_offline(self):
        provider, opener = self.provider()
        provider.route(ORIGIN, DESTINATION)
        cache = next(Path(self.directory.name).glob('[0-9a-f]' * 64 + '.json'))
        value = json.loads(cache.read_text(encoding='utf-8'))
        value['route']['fetched_at'] = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        cache.write_text(json.dumps(value), encoding='utf-8')
        offline, offline_opener = self.provider(offline=True)
        with self.assertRaises(RoadRouteError):
            offline.route(ORIGIN, DESTINATION)
        self.assertFalse(offline_opener.requests)
        with patch('simulation.road_routes.time.sleep'):
            provider.route(ORIGIN, DESTINATION)
        self.assertEqual(len(opener.requests), 2)

    def test_missing_key_and_error_text_never_disclose_credentials(self):
        opener = FakeOpener()
        provider = KakaoRouteProvider(cache_dir=self.directory.name, key='', opener=opener)
        with self.assertRaisesRegex(RoadRouteError, 'not configured'):
            provider.route(ORIGIN, DESTINATION)
        self.assertFalse(opener.requests)
        def bad_opener(request, timeout):
            raise OSError('Authorization: KakaoAK ' + FAKE_KEY)
        provider = KakaoRouteProvider(cache_dir=self.directory.name, key=FAKE_KEY, opener=bad_opener)
        with self.assertRaises(RoadRouteError) as error, patch('simulation.road_routes.time.sleep'):
            provider.route(ORIGIN, DESTINATION)
        self.assertNotIn(FAKE_KEY, str(error.exception))
        self.assertNotIn('Authorization', str(error.exception))

    def test_route_rejection_does_not_cache_or_use_straight_fallback(self):
        value = response()
        value['routes'][0].update(result_code=101, result_msg=FAKE_KEY)
        provider, _ = self.provider(value)
        with self.assertRaises(RoadRouteError) as error:
            provider.route(ORIGIN, DESTINATION)
        self.assertIn('code 101', str(error.exception))
        self.assertNotIn(FAKE_KEY, str(error.exception))
        self.assertFalse(list(Path(self.directory.name).glob('[0-9a-f]' * 64 + '.json')))

    def test_invalid_geometry_timing_and_endpoints_fail_closed(self):
        for defect in ('missing_sections', 'missing_roads', 'odd_vertices', 'one_point',
                       'nan', 'boolean_coordinate', 'disconnected', 'zero_duration',
                       'negative_distance', 'missing_duration', 'summary_endpoint', 'bool_code'):
            with self.subTest(defect=defect):
                value = response()
                raw = value['routes'][0]
                road = raw['sections'][0]['roads'][0]
                if defect == 'missing_sections': raw['sections'] = []
                elif defect == 'missing_roads': raw['sections'][0]['roads'] = []
                elif defect == 'odd_vertices': road['vertexes'].append(127.)
                elif defect == 'one_point': road['vertexes'] = [127., 37.]
                elif defect == 'nan': road['vertexes'][0] = float('nan')
                elif defect == 'boolean_coordinate': road['vertexes'][0] = True
                elif defect == 'disconnected': raw['sections'][1]['roads'][0]['vertexes'][0] = 127.1
                elif defect == 'zero_duration': raw['summary']['duration'] = 0
                elif defect == 'negative_distance': raw['summary']['distance'] = -1
                elif defect == 'missing_duration': del raw['summary']['duration']
                elif defect == 'summary_endpoint': raw['summary']['destination']['x'] = 128.
                elif defect == 'bool_code': raw['result_code'] = False
                provider, _ = self.provider(value)
                with self.assertRaises(RoadRouteError), patch('simulation.road_routes.time.sleep'):
                    provider.route(ORIGIN, DESTINATION)

    def test_snapshot_connector_contract_and_correct_provider_summary(self):
        provider, _ = self.provider()
        snapshot = RouteSnapshot(provider)
        result = snapshot.route([37.001, 127.001], [37.019, 127.019])
        self.assertEqual(result['motion_geometry'][0], [37.001, 127.001])
        self.assertEqual(result['motion_geometry'][-1], [37.019, 127.019])
        self.assertEqual(len(result['connectors']), 2)
        self.assertGreater(result['duration_seconds'], result['road_duration_seconds'])
        summary = snapshot_summary(snapshot, scenario_only=True)
        self.assertEqual(summary['provider'], 'kakao')
        self.assertTrue(summary['traffic_snapshot'])
        self.assertEqual(summary['routes_evaluated'], 1)
        self.assertEqual(summary['synthetic_routes'], 0)
        self.assertIsNotNone(summary['snapshot_first_fetched_at'])

    def test_invalid_cache_and_cache_age_are_rejected(self):
        provider, _ = self.provider()
        provider.route(ORIGIN, DESTINATION)
        cache = next(Path(self.directory.name).glob('[0-9a-f]' * 64 + '.json'))
        value = json.loads(cache.read_text(encoding='utf-8'))
        value['route']['provider'] = 'osrm'
        cache.write_text(json.dumps(value), encoding='utf-8')
        offline, _ = self.provider(offline=True)
        with self.assertRaises(RoadRouteError): offline.route(ORIGIN, DESTINATION)
        for age in (0, -1, 86401, float('inf')):
            with self.assertRaises(ValueError): self.provider(cache_max_age_seconds=age)

    def test_cache_with_nonzero_geometry_and_zero_duration_is_rejected(self):
        provider, _ = self.provider()
        provider.route(ORIGIN, DESTINATION)
        cache = next(Path(self.directory.name).glob('[0-9a-f]' * 64 + '.json'))
        value = json.loads(cache.read_text(encoding='utf-8'))
        value['route']['duration_seconds'] = 0
        cache.write_text(json.dumps(value), encoding='utf-8')
        offline, _ = self.provider(offline=True)
        with self.assertRaises(RoadRouteError): offline.route(ORIGIN, DESTINATION)


class KakaoReplayTests(unittest.TestCase):
    def test_changed_hospital_uses_change_scene_eta_before_replay(self):
        class DifferentScenes(RoadFixture):
            def route(self, origin, destination):
                result = super().route(origin, destination)
                if origin[0] >= 37.17 and round(destination[0], 2) == 37.09:
                    result['duration_seconds'] = 8 * 60
                return result
        routing = RouteSnapshot(DifferentScenes({37.14: 10, 37.09: 12, 37.05: 20}))
        scenarios = build_road_scenarios(routing, hospitals=catalog())
        self.assertEqual(scenarios[0]['hospitals'][0]['id'], 'H1')
        change = scenarios[1]
        self.assertEqual(change['hospitals'][0]['id'], 'H2')
        self.assertEqual(change['exogenous_events'][0]['hospital'], 'H2')
        self.assertEqual(change['hospitals'][0]['capacity']['special_team'], 1)
        self.assertEqual(change['experiment']['changed_hospital_rule'],
                         'minimum_road_eta_from_change_scene_before_replay')
        branch = generate_road_online_replay([change], routing, [policy('R1')])['scenarios'][0]['branches'][0]
        self.assertEqual([e['patient'] for e in branch['events'] if e['type'] == 'redirect'], ['ROAD-P01'])

    def test_current_road_costs_and_metadata_use_kakao_from_input(self):
        class KakaoFixture(RoadFixture):
            provider_name = 'kakao'
            def route(self, origin, destination):
                result = super().route(origin, destination)
                result.update(provider='kakao', fetched_at=datetime.now(timezone.utc).isoformat(),
                              traffic_snapshot=True)
                return result
        routing = RouteSnapshot(KakaoFixture({37.14: 20, 37.09: 4, 37.05: 10}))
        scenario = build_road_scenarios(routing, hospitals=catalog())[2]
        replay = generate_road_online_replay([scenario], routing, [policy('R1')])
        branch = replay['scenarios'][0]['branches'][0]
        self.assertEqual(next(e for e in branch['events'] if e['type'] == 'commit')['hospital'], 'H2')
        self.assertEqual(replay['routing_summary']['provider'], 'kakao')
        self.assertEqual(replay['scenarios'][0]['routing_summary']['provider'], 'kakao')
        self.assertTrue(all(leg['provider'] == 'kakao' for t in branch['trajectories'] for leg in t['legs']))
        self.assertIn('kakao', replay['scenarios'][0]['scenario']['experiment']['routing'])
        self.assertTrue(any('카카오모빌리티' in line for line in replay['limitations']))

    def test_kakao_failure_preserves_existing_output_and_does_not_switch_to_osrm(self):
        class Unavailable:
            provider_name = 'kakao'
            def __init__(self, **kwargs): pass
            def route(self, *args): raise RoadRouteError('Kakao fixture unavailable')
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[2] / '.codex-build') as directory:
            output = Path(directory) / 'existing.json'
            output.write_text('original', encoding='utf-8')
            with patch('simulation.road_online_replay.KakaoRouteProvider', Unavailable), \
                 patch('simulation.road_online_replay.RoadRouteProvider') as osrm, \
                 patch('simulation.road_online_replay.load_public_hospitals', return_value=catalog()):
                with self.assertRaises(SystemExit): main(['--provider', 'kakao', '--output', str(output)])
            self.assertEqual(output.read_text(), 'original')
            osrm.assert_not_called()


if __name__ == '__main__':
    unittest.main()
