"""Road-policy integration, using offline bent-road fixtures rather than HTTP."""
from copy import deepcopy
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from simulation.cp_sat_assignment import CPSATDependencyError, _load_cp_model
from simulation.map_replay import RouteSnapshot
from simulation.online_allocation_engine import run_online_replay
from simulation.road_online_replay import (BASIC, SOURCE_KIND, build_road_scenarios,
                                          generate_road_online_replay, main)
from simulation.road_routes import RoadRouteError, distance_meters, synthetic_route


class RoadFixture:
    def __init__(self, minutes=None, fallback=False):
        self.minutes = minutes or {}
        self.fallback = fallback

    def route(self, origin, destination):
        if self.fallback:
            return synthetic_route(origin, destination, 'unit_test_only')
        a, b = list(origin), list(destination)
        bend = [(a[0] + b[0]) / 2, max(a[1], b[1]) + .002]
        geometry = [a, bend, b]
        duration = self.minutes.get(round(b[0], 2), 10) * 60
        return dict(provider='osrm', geometry_kind='road', source='offline_unit_fixture',
            geometry=geometry, duration_seconds=duration,
            distance_meters=sum(distance_meters(x, y) for x, y in zip(geometry, geometry[1:])),
            snapped_endpoints=[a, b], requested_endpoints=[a, b], connectors=[],
            live_traffic=False, ambulance_priority=False)


def catalog():
    return [dict(id=f'H{i}', name=f'Public identity fixture {i}', lat=lat, lon=127.07)
            for i, lat in enumerate((37.14, 37.09, 37.05), start=1)]


def policy(rule):
    return dict(id=rule, label=rule, rule=rule, k=3, wait=2, notify=True)


class RoadOnlineTests(unittest.TestCase):
    def test_build_uses_catalog_identity_and_road_dispatch_before_policies(self):
        hospitals = catalog()
        original = deepcopy(hospitals)
        routing = RouteSnapshot(RoadFixture({37.14: 6, 37.09: 12, 37.05: 20, 37.12: 7, 37.18: 7}))
        scenarios = build_road_scenarios(routing, hospitals=hospitals)
        self.assertEqual(hospitals, original)
        self.assertEqual([s['id'] for s in scenarios], ['road-online-simultaneous',
            'road-online-change-enroute', 'road-online-transfer-after-arrival'])
        for scenario in scenarios:
            self.assertTrue(scenario['real_road_allocation'])
            self.assertTrue(scenario['online_allocation_comparison'])
            self.assertEqual([h['name'] for h in scenario['hospitals']],
                             [h['name'] for h in original])
            for patient in scenario['patients']:
                self.assertEqual(patient['ambulance_arrive_t'], 8)
                self.assertEqual(patient['search_start_t'], 11)
                self.assertEqual(patient['dispatch_timing_source'],
                                 'route_estimate_plus_synthetic_connector')
        self.assertEqual(scenarios[0]['patients'][-1]['required_capabilities'],
                         [BASIC, '가상 희소치료조건'])

    def test_road_times_change_real_assignment_and_current_audit_costs(self):
        routing = RouteSnapshot(RoadFixture({37.14: 20, 37.09: 4, 37.05: 10}))
        scenario = build_road_scenarios(routing, hospitals=catalog())[2]
        original = deepcopy(scenario)
        replay = generate_road_online_replay([scenario], routing, [policy('R1')])
        branch = replay['scenarios'][0]['branches'][0]
        first = next(e for e in branch['events'] if e['type'] == 'commit')
        self.assertEqual(first['hospital'], 'H2')
        decision = branch['allocation_decisions'][0]
        costs = {r['hospital']: r['minutes'] for r in decision['inputs']['travel_minutes']}
        self.assertEqual(costs, {'H2': 4, 'H3': 10, 'H1': 20})
        straight = run_online_replay(scenario, [policy('R1')])['branches'][0]
        self.assertEqual(next(e for e in straight['events'] if e['type'] == 'commit')['hospital'], 'H1')
        self.assertEqual(scenario, original)
        self.assertTrue(all(l['geometry_kind'] == 'road'
                            for t in branch['trajectories'] for l in t['legs']))
        self.assertEqual(replay['source_kind'], SOURCE_KIND)
        self.assertFalse(replay['validation_dataset'])
        self.assertEqual(replay['scenarios'][0]['routing_summary']['synthetic_routes'], 0)
        json.dumps(replay, allow_nan=False)

    def test_change_redirect_preserves_position_and_transfer_follows_roads(self):
        routing = RouteSnapshot(RoadFixture({37.14: 6, 37.09: 12, 37.05: 20}))
        scenarios = build_road_scenarios(routing, hospitals=catalog())
        replay = generate_road_online_replay(scenarios[1:], routing, [policy('R1')])
        changed, transferred = [r['branches'][0] for r in replay['scenarios']]
        redirects = [e for e in changed['events'] if e['type'] == 'redirect']
        self.assertEqual([e['patient'] for e in redirects], ['ROAD-P01'])
        legs = next(t['legs'] for t in changed['trajectories'] if t['patient'] == 'ROAD-P01')
        before, after = legs[1:]
        self.assertEqual(before['destination'], after['origin'])
        self.assertEqual(before['arrive_t'], after['depart_t'])
        self.assertEqual(before['motion_geometry'][-1], after['motion_geometry'][0])
        self.assertEqual(after['geometry_kind'], 'road')
        transfers = [e for e in transferred['events'] if e['type'] == 'transfer_depart']
        self.assertEqual(len(transfers), 1)
        self.assertEqual(transfers[0]['hospital'], 'H3')
        self.assertEqual(transferred['trajectories'][0]['legs'][-1]['kind'], 'transfer')
        # Hidden assessment requirements must not enter pre-arrival decisions.
        decision = transferred['allocation_decisions'][0]
        self.assertEqual(decision['inputs']['patients'][0]['required_capabilities'], [BASIC])
        for forbidden in ('exogenous_events', 'response_environment', 'until', 'arrival_assessment'):
            self.assertNotIn(forbidden, json.dumps(decision['inputs']))

    def test_refuses_synthetic_model_or_fallback_in_road_export(self):
        with self.assertRaisesRegex(ValueError, 'road routing provider'):
            build_road_scenarios(RouteSnapshot(), hospitals=catalog())
        routing = RouteSnapshot(RoadFixture())
        scenarios = build_road_scenarios(routing, hospitals=catalog())
        with self.assertRaisesRegex(RoadRouteError, 'synthetic fallback'):
            generate_road_online_replay(scenarios[:1],
                RouteSnapshot(RoadFixture(fallback=True)), [policy('R1')])

    def test_failed_collection_preserves_existing_output(self):
        class UnavailableProvider:
            def __init__(self, **_kwargs):
                pass
            def route(self, *_args):
                raise RoadRouteError('fixture failure')
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            output = Path(directory) / 'existing.json'
            output.write_text('original', encoding='utf-8')
            with patch('simulation.road_online_replay.RoadRouteProvider', UnavailableProvider), \
                 patch('simulation.road_online_replay.load_public_hospitals', return_value=catalog()):
                with self.assertRaises(SystemExit):
                    main(['--output', str(output)])
            self.assertEqual(output.read_text(encoding='utf-8'), 'original')


try:
    _load_cp_model()
    HAS_CP_SAT = True
except CPSATDependencyError:
    HAS_CP_SAT = False


@unittest.skipUnless(HAS_CP_SAT, 'OR-Tools not installed')
class RoadFivePolicyTests(unittest.TestCase):
    def test_all_five_branches_reexecute_and_same_road_clock(self):
        routing = RouteSnapshot(RoadFixture({37.14: 6, 37.09: 12, 37.05: 20}))
        scenario = build_road_scenarios(routing, hospitals=catalog())[0]
        result = generate_road_online_replay([scenario], routing)['scenarios'][0]
        self.assertEqual([b['id'] for b in result['branches']], ['R0', 'R1', 'R2', 'R3', 'C1'])
        dispatches = []
        for branch in result['branches']:
            self.assertTrue(branch['allocation_decisions'])
            dispatches.append([t['legs'][0] for t in branch['trajectories']])
            for trajectory in branch['trajectories']:
                for leg in trajectory['legs'][1:]:
                    self.assertEqual(leg['estimated_minutes'], max(1, math.ceil(leg['planned_duration_seconds'] / 60)))
                    self.assertEqual(leg['geometry_kind'], 'road')
            self.assertFalse(branch['metrics']['whole_episode_optimal_proven'])
        self.assertTrue(all(d == dispatches[0] for d in dispatches[1:]))
        cp = result['branches'][-1]
        self.assertTrue(cp['solver_records'])
        self.assertEqual(cp['metrics']['solver_fallbacks'], 0)
        self.assertEqual(cp['metrics']['unassigned'], 0)
        self.assertEqual(result['branches'][0]['metrics']['unassigned'], 1)


if __name__ == '__main__':
    unittest.main()
