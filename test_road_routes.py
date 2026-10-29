"""Road route parsing, caching, failure semantics and geometry interpolation.

All network responses are injected fixtures; tests never call a public server.
"""
import copy
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from simulation.road_routes import (RoadRouteError, RoadRouteProvider, distance_meters,
                                    point_along, prefix_route)


ORIGIN = [37.0, 127.0]
DESTINATION = [37.02, 127.02]


def response():
    return {'code': 'Ok', 'data_version': '2026-10-01T00:00:00Z',
            'routes': [{'duration': 200.0, 'distance': 3000.0,
                        'geometry': {'type': 'LineString',
                                     'coordinates': [[127.0, 37.0], [127.01, 37.0],
                                                     [127.02, 37.02]]}}],
            'waypoints': [{'location': [127.0, 37.0]}, {'location': [127.02, 37.02]}]}


class FakeOpener:
    def __init__(self, value=None):
        self.value = value if value is not None else response()
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append(request)
        return BytesIO(json.dumps(self.value).encode())


class RoadRouteTests(unittest.TestCase):
    def setUp(self):
        temporary_root = Path(__file__).resolve().parent / '.codex-build'
        temporary_root.mkdir(exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(dir=temporary_root)
        self.addCleanup(self.directory.cleanup)

    def provider(self, value=None, **kwargs):
        opener = FakeOpener(value)
        return RoadRouteProvider(cache_dir=self.directory.name, opener=opener, **kwargs), opener

    def test_wire_coordinates_are_lon_lat_and_result_is_lat_lon(self):
        provider, opener = self.provider()
        result = provider.route(ORIGIN, DESTINATION)
        self.assertIn('/127.000000,37.000000;127.020000,37.020000?', opener.requests[0].full_url)
        self.assertEqual(result['geometry'], [[37., 127.], [37., 127.01], [37.02, 127.02]])
        self.assertEqual(result['duration_seconds'], 200.)
        self.assertEqual(result['distance_meters'], 3000.)
        self.assertEqual(result['provider'], 'osrm')
        self.assertEqual(result['geometry_kind'], 'road')
        self.assertFalse(result['live_traffic'])
        self.assertFalse(result['ambulance_priority'])

    def test_cached_result_is_frozen_and_returns_an_independent_copy(self):
        provider, opener = self.provider()
        first = provider.route(ORIGIN, DESTINATION)
        frozen = copy.deepcopy(first)
        first['geometry'][0][0] = -1
        second = provider.route(ORIGIN, DESTINATION)
        self.assertEqual(second, frozen)
        self.assertEqual(len(opener.requests), 1)
        offline = RoadRouteProvider(cache_dir=self.directory.name, offline=True)
        self.assertEqual(offline.route(ORIGIN, DESTINATION), frozen)

    def test_requests_are_direction_specific_and_rate_limited(self):
        provider, opener = self.provider()
        with patch('simulation.road_routes.time.sleep') as sleep:
            provider.route(ORIGIN, DESTINATION)
            provider.route(DESTINATION, ORIGIN)
        self.assertEqual(len(opener.requests), 2)
        self.assertTrue(sleep.called)
        self.assertGreater(sleep.call_args.args[0], 0.9)

    def test_snap_connectors_are_separate_and_not_invented_road_geometry(self):
        provider, _ = self.provider()
        result = provider.route([37.001, 127.001], [37.019, 127.019])
        self.assertEqual(result['geometry'][0], ORIGIN)
        self.assertEqual(result['geometry'][-1], DESTINATION)
        self.assertEqual(len(result['connectors']), 2)
        self.assertTrue(all(x['geometry_kind'] == 'offroad_connector' for x in result['connectors']))
        self.assertTrue(all(not x['included_in_duration'] for x in result['connectors']))

    def test_no_route_is_an_error_not_silent_straight_line(self):
        provider, _ = self.provider({'code': 'NoRoute'})
        with self.assertRaises(RoadRouteError):
            provider.route(ORIGIN, DESTINATION)
        self.assertEqual(list(Path(self.directory.name).glob('[0-9a-f]' * 64 + '.json')), [])

    def test_fallback_must_be_explicit_and_never_claims_road(self):
        provider, _ = self.provider({'code': 'NoRoute'}, allow_synthetic_fallback=True)
        result = provider.route(ORIGIN, DESTINATION)
        self.assertEqual(result['provider'], 'synthetic')
        self.assertEqual(result['geometry_kind'], 'synthetic_straight_line')
        self.assertIsNone(result['fetched_at'])

    def test_offline_cache_miss_does_not_use_network(self):
        provider, opener = self.provider(offline=True)
        with self.assertRaises(RoadRouteError):
            provider.route(ORIGIN, DESTINATION)
        self.assertEqual(opener.requests, [])

    def test_corrupt_cache_is_rejected_in_offline_mode(self):
        provider, _ = self.provider()
        provider.route(ORIGIN, DESTINATION)
        path = next(Path(self.directory.name).glob('[0-9a-f]' * 64 + '.json'))
        value = json.loads(path.read_text(encoding='utf-8'))
        value['route']['geometry'][0] = [float('nan'), 127.]
        path.write_text(json.dumps(value), encoding='utf-8')
        with self.assertRaises(RoadRouteError):
            RoadRouteProvider(cache_dir=self.directory.name, offline=True).route(ORIGIN, DESTINATION)

    def test_network_error_does_not_disclose_request_url(self):
        def bad_opener(request, timeout):
            raise OSError('url=https://example.test/?secret=secret-value')
        provider = RoadRouteProvider(cache_dir=self.directory.name, opener=bad_opener)
        with self.assertRaises(RoadRouteError) as error:
            provider.route(ORIGIN, DESTINATION)
        self.assertNotIn('secret', str(error.exception))

    def test_nonzero_geometry_cannot_have_zero_duration(self):
        value = response()
        value['routes'][0]['duration'] = 0
        provider, _ = self.provider(value)
        with self.assertRaises(RoadRouteError):
            provider.route(ORIGIN, DESTINATION)

    def test_same_snapped_location_can_have_a_true_zero_route(self):
        value = response()
        value['routes'][0].update(duration=0, distance=0,
                                   geometry={'type': 'LineString', 'coordinates': [[127., 37.], [127., 37.]]})
        value['waypoints'][1]['location'] = [127., 37.]
        provider, _ = self.provider(value)
        route = provider.route(ORIGIN, ORIGIN)
        self.assertEqual(route['duration_seconds'], 0)
        self.assertEqual(point_along(route, 0.5), ORIGIN)

    def test_invalid_geometry_and_endpoint_mismatch_are_rejected(self):
        for defect in ('single_point', 'nan', 'endpoint_mismatch'):
            with self.subTest(defect=defect):
                value = response()
                if defect == 'single_point':
                    value['routes'][0]['geometry']['coordinates'] = [[127., 37.]]
                elif defect == 'nan':
                    value['routes'][0]['geometry']['coordinates'][1][0] = float('nan')
                else:
                    value['waypoints'][1]['location'] = [127., 38.]
                provider, _ = self.provider(value)
                with self.assertRaises(RoadRouteError), patch('simulation.road_routes.time.sleep'):
                    provider.route(ORIGIN, DESTINATION)

    def test_credentials_in_base_url_and_invalid_input_are_rejected(self):
        for url in ('https://secret@example.test', 'https://example.test?key=secret'):
            with self.assertRaises(ValueError):
                RoadRouteProvider(base_url=url)
        provider, opener = self.provider()
        for point in ([float('nan'), 127], [91, 127], [True, 127]):
            with self.assertRaises(ValueError):
                provider.route(point, DESTINATION)
        self.assertEqual(opener.requests, [])


class RoadGeometryTests(unittest.TestCase):
    def test_interpolation_uses_length_not_vertex_index(self):
        # On the equator these segment lengths are in the exact ratio 1:9.
        geometry = [[0., 0.], [0., 0.01], [0., 0.1]]
        self.assertAlmostEqual(point_along(geometry, .5)[1], .05, places=10)
        self.assertEqual(point_along(geometry, -1), geometry[0])
        self.assertEqual(point_along(geometry, 2), geometry[-1])

    def test_truncation_preserves_continuity_and_does_not_mutate_original(self):
        route = dict(geometry=[[0., 0.], [0., .01], [0., .1]], distance_meters=100.,
                     duration_seconds=20., snapped_endpoints=[[0., 0.], [0., .1]],
                     requested_endpoints=[[0., 0.], [0., .1]], connectors=[{'example': True}],
                     geometry_kind='road', provider='osrm')
        before = copy.deepcopy(route)
        cut = prefix_route(route, .4)
        self.assertEqual(cut['geometry'][-1], point_along(route, .4))
        self.assertEqual(cut['distance_meters'], 40.)
        self.assertEqual(cut['duration_seconds'], 8.)
        self.assertEqual(cut['connectors'], [])
        self.assertEqual(route, before)
        self.assertEqual(prefix_route(route, 0)['geometry'], [[0., 0.], [0., 0.]])

    def test_duplicate_vertices_and_nonfinite_progress(self):
        geometry = [[0., 0.], [0., 0.], [0., .1]]
        self.assertAlmostEqual(point_along(geometry, .5)[1], .05, places=10)
        with self.assertRaises(ValueError):
            point_along(geometry, float('nan'))
        self.assertEqual(distance_meters([37., 127.], [37., 127.]), 0.)


if __name__ == '__main__':
    unittest.main()
