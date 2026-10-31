"""Offline guards for frozen road input; no real routing requests."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from server import voice_road_rehearsal as road
from simulation.road_routes import RoadRouteError


class FixtureProvider:
    requests_completed = 0

    def route(self, origin, destination):
        self.requests_completed += 1
        return dict(provider='kakao', geometry_kind='road', geometry=[list(origin), list(destination)],
            duration_seconds=61, distance_meters=700, ambulance_priority=False,
            requested_endpoints=[list(origin), list(destination)],
            snapped_endpoints=[list(origin), list(destination)],
            connectors=[], fetched_at='2026-10-30T00:00:00+00:00')


class SnapshotTests(unittest.TestCase):
    def test_all_execution_pairs_are_precollected_and_unknown_pair_cannot_fetch(self):
        provider = FixtureProvider()
        snapshot = road.SnapshotEstimator(provider)
        midpoint = snapshot.prepare()
        self.assertEqual(provider.requests_completed, 6)
        self.assertEqual(midpoint, (35.1605, 126.85))
        self.assertEqual(snapshot.estimate(road.ORIGIN, road.HOSPITALS[0])['duration_seconds'], 120)
        before = provider.requests_completed
        with self.assertRaisesRegex(RoadRouteError, 'outside'):
            snapshot.estimate((35.9, 126.9), road.HOSPITALS[0])
        self.assertEqual(before, provider.requests_completed)

    def test_connector_time_rounding_and_mutation_isolation(self):
        snapshot = road.SnapshotEstimator(FixtureProvider())
        route = snapshot.collect(road.ORIGIN, road.HOSPITALS[0])
        route['geometry'][0][0] = 99
        key = road._key(road.ORIGIN, road.HOSPITALS[0])
        self.assertEqual(snapshot.routes[key]['geometry'][0][0], road.ORIGIN[0])
        snapshot.routes[key]['connectors'] = [dict(distance_meters=100, included_in_duration=False)]
        estimate = snapshot.estimate(road.ORIGIN, road.HOSPITALS[0])
        self.assertAlmostEqual(estimate['connector_seconds'], 36)
        self.assertEqual(estimate['duration_seconds'], 120)
        snapshot.routes[key]['duration_seconds'] = 120
        self.assertEqual(snapshot.estimate(road.ORIGIN, road.HOSPITALS[0])['duration_seconds'], 180)

    def test_synthetic_route_is_refused(self):
        provider = FixtureProvider()
        original = provider.route
        provider.route = lambda *args: {**original(*args), 'provider': 'synthetic'}
        with self.assertRaisesRegex(RoadRouteError, 'fallback refused'):
            road.SnapshotEstimator(provider).prepare()

    def test_failed_route_collection_is_persisted_without_fixture_database(self):
        parent = road.ROOT/'.codex-build/voice-road-tests'
        parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as tmp:
            output = Path(tmp)/'new'
            provider = FixtureProvider()
            provider.route = lambda *args: (_ for _ in ()).throw(RoadRouteError('offline fixture unavailable'))
            with patch.object(road.allocation_baseline, 'verify', return_value={'ok': True, 'manifest_sha256': 'fixture'}):
                result = road.run(output, provider=provider)
            self.assertFalse(result['pass'])
            self.assertEqual(result['passed'], 0)
            self.assertIn('offline fixture unavailable', (output/'report.md').read_text(encoding='utf-8'))
            self.assertFalse(list(output.rglob('fixture.db')))
            self.assertEqual(json.loads((output/'report.json').read_text(encoding='utf-8'))['collected_route_count'], 0)
            with self.assertRaises(FileExistsError):
                road.run(output, provider=provider)


if __name__ == '__main__':
    unittest.main()
