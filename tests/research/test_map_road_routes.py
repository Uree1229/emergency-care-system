"""Road-input integration tests for map replay; no external service is called."""
import copy
import math
import unittest

from simulation.map_replay import ReplaySimulation, RouteSnapshot, generate_replay
from simulation.road_routes import distance_meters, point_along, synthetic_route
from simulation.sim import Policy


def scenario():
    return dict(id='road-unit', label='Synthetic road integration fixture', seed=17,
                synthetic=True, max_minutes=100, transfer_preparation_minutes=3,
                hospitals=[dict(id='NEAR', name='Near fixture', lat=37.15, lon=127.0,
                                capabilities=['basic'], capacity={'ed_bed': 3, 'special_team': 1}),
                           dict(id='FAR', name='Far fixture', lat=37.25, lon=127.0,
                                capabilities=['basic'], capacity={'ed_bed': 3, 'special_team': 1}),
                           dict(id='ADVANCED', name='Advanced fixture', lat=37.30, lon=127.0,
                                capabilities=['basic', 'advanced'], capacity={'ed_bed': 3, 'special_team': 1})],
                patients=[dict(id='P', label='Patient fixture', location=[37.10, 127.0], occur_t=0,
                               dispatch_t=1, ambulance_origin=[37.08, 127.0], ambulance_arrive_t=7,
                               search_start_t=10, required_capabilities=['basic'],
                               resource_demands={'ed_bed': 1, 'special_team': 1},
                               treatment_minutes=15, priority=4)],
                exogenous_events=[], response_environment={'*': {'NEAR': dict(delay=1),
                                                                 'FAR': dict(delay=1),
                                                                 'ADVANCED': dict(delay=1)}})


class MockRoadProvider:
    """Bent road with configurable duration independent of straight-line distance."""
    def __init__(self, durations=None, fallback_lat=None, snap_offset=0):
        self.durations = durations or {37.15: 1200, 37.25: 240, 37.30: 600, 37.10: 180}
        self.fallback_lat = fallback_lat
        self.snap_offset = snap_offset
        self.calls = []

    def route(self, origin, destination):
        origin, destination = list(origin), list(destination)
        self.calls.append((tuple(origin), tuple(destination)))
        if destination[0] == self.fallback_lat:
            return synthetic_route(origin, destination, 'mock_route_failure')
        start = [origin[0] + self.snap_offset, origin[1]]
        end = [destination[0] - self.snap_offset, destination[1]]
        bend = [(start[0] + end[0]) / 2, max(start[1], end[1]) + .02]
        geometry = [start, bend, end]
        connectors = []
        for kind, a, b in (('origin', origin, start), ('destination', end, destination)):
            gap = distance_meters(a, b)
            if gap:
                connectors.append(dict(endpoint=kind, geometry=[a, b], distance_meters=gap,
                                       geometry_kind='offroad_connector', included_in_duration=False))
        return dict(duration_seconds=self.durations.get(round(destination[0], 2), 600),
                    distance_meters=sum(distance_meters(a, b) for a, b in zip(geometry, geometry[1:])),
                    geometry=geometry, provider='osrm', geometry_kind='road',
                    requested_endpoints=[origin, destination], snapped_endpoints=[start, end],
                    connectors=connectors, source='mock_osrm_fixture',
                    fetched_at='2026-10-11T00:00:00+00:00',
                    live_traffic=False, ambulance_priority=False)


ONE_ARRIVAL_POLICY = [dict(id='arrival', label='Arrival comparison', rule='R1', k=3, wait=2, notify=True)]


class MapRoadIntegrationTests(unittest.TestCase):
    def test_road_duration_changes_candidate_order_and_selected_hospital(self):
        value = scenario()
        routing = RouteSnapshot(MockRoadProvider())
        engine = ReplaySimulation(value, Policy(rule='R1', k=3, wait=2), routing=routing)
        patient = engine.patients['P']
        self.assertEqual(engine.candidates(patient, patient.loc), ['FAR', 'ADVANCED', 'NEAR'])
        self.assertEqual(engine.travel(patient, 'NEAR', patient.loc), 20)
        self.assertEqual(engine.travel(patient, 'FAR', patient.loc), 4)
        road = generate_replay(value, ONE_ARRIVAL_POLICY, routing=routing)['branches'][0]
        straight = generate_replay(value, ONE_ARRIVAL_POLICY)['branches'][0]
        self.assertEqual(straight['patient_outcomes'][0]['destination_hospital_id'], 'NEAR')
        self.assertEqual(road['patient_outcomes'][0]['destination_hospital_id'], 'FAR')
        leg = road['trajectories'][0]['legs'][1]
        self.assertEqual(leg['arrive_t'] - leg['depart_t'], 4)
        self.assertEqual(leg['estimated_minutes'], 4)
        self.assertEqual(leg['duration_seconds'], 240)

    def test_current_engine_position_follows_motion_geometry_not_chord(self):
        engine = ReplaySimulation(scenario(), Policy(rule='R1', k=3, wait=2),
                                  routing=RouteSnapshot(MockRoadProvider(snap_offset=.0001)))
        patient = engine.patients['P']
        engine.commit(patient, 'NEAR', 10)
        leg = engine.legs['P'][0]
        t = (leg['depart_t'] + leg['arrive_t']) / 2
        current = engine.position(patient, t)
        self.assertEqual(list(current), point_along(leg['motion_geometry'], .5))
        self.assertGreater(current[1], 127.01)
        self.assertEqual(leg['motion_geometry'][0], leg['origin'])
        self.assertEqual(leg['motion_geometry'][-1], leg['destination'])

    def test_redirect_truncation_is_continuous_and_preserves_past_playback(self):
        engine = ReplaySimulation(scenario(), Policy(rule='R1', k=3, wait=2),
                                  routing=RouteSnapshot(MockRoadProvider(snap_offset=.0001)))
        patient = engine.patients['P']
        engine.commit(patient, 'NEAR', 10)
        old_geometry = copy.deepcopy(engine.legs['P'][0]['motion_geometry'])
        old_arrive = patient.arrive_t
        prior = {t: point_along(old_geometry, (t - 10) / (old_arrive - 10)) for t in (10, 12, 14)}
        redirect_t = 16
        expected_turn = list(engine.position(patient, redirect_t))
        engine.commit(patient, 'FAR', redirect_t, redirect=True)
        old, new = engine.legs['P']
        self.assertEqual(old['ended_by'], 'redirect')
        self.assertEqual(old['arrive_t'], new['depart_t'])
        self.assertEqual(old['destination'], new['origin'])
        self.assertEqual(old['motion_geometry'][-1], new['motion_geometry'][0])
        self.assertEqual(new['origin'], expected_turn)
        self.assertEqual(new['origin'], point_along(old_geometry, (redirect_t - 10) / (old_arrive - 10)))
        for t, expected in prior.items():
            actual = point_along(old['motion_geometry'], (t - 10) / (redirect_t - 10))
            self.assertAlmostEqual(actual[0], expected[0], places=7)
            self.assertAlmostEqual(actual[1], expected[1], places=7)

    def test_resource_change_redirects_from_road_midpoint_in_full_replay(self):
        value = scenario()
        durations = {37.15: 1200, 37.25: 1800, 37.30: 2400, 37.10: 180}
        value['exogenous_events'] = [dict(t=14, type='resource_unavailable', hospital='NEAR',
                                           resource='special_team', until=90)]
        policy = [dict(id='one', label='One request', rule='R0', k=1, wait=0, notify=True)]
        branch = generate_replay(value, policy, routing=RouteSnapshot(MockRoadProvider(durations)))['branches'][0]
        redirect = next(e for e in branch['events'] if e['type'] == 'redirect')
        transport = [leg for leg in branch['trajectories'][0]['legs'] if leg['kind'] == 'transport']
        self.assertEqual(len(transport), 2)
        old, new = transport
        self.assertEqual(old['arrive_t'], redirect['t'])
        self.assertEqual(old['destination'], new['origin'])
        self.assertGreater(new['origin'][1], 127.0)
        self.assertEqual(old['motion_geometry'][-1], new['motion_geometry'][0])
        self.assertEqual(branch['metrics']['transfers'], 0)

    def test_transfer_uses_arrival_hospital_as_new_route_origin(self):
        value = scenario()
        value['patients'][0]['arrival_assessment_adds_capabilities'] = ['advanced']
        provider = MockRoadProvider({37.15: 600, 37.25: 900, 37.30: 1200, 37.10: 180})
        branch = generate_replay(value, ONE_ARRIVAL_POLICY, routing=RouteSnapshot(provider))['branches'][0]
        unable = next(e for e in branch['events'] if e['type'] == 'arrival_unable')
        search = next(e for e in branch['events'] if e['type'] == 'transfer_search')
        transfer = next(leg for leg in branch['trajectories'][0]['legs'] if leg['kind'] == 'transfer')
        self.assertEqual(unable['hospital'], 'NEAR')
        self.assertEqual(search['t'], unable['t'] + 3)
        self.assertEqual(transfer['origin'], [37.15, 127.0])
        self.assertEqual(transfer['motion_geometry'][0], transfer['origin'])
        self.assertEqual(transfer['destination'], [37.30, 127.0])
        self.assertIn(((37.15, 127.0), (37.30, 127.0)), provider.calls)
        self.assertEqual(branch['patient_outcomes'][0]['transfers'], 1)
        self.assertEqual(branch['patient_outcomes'][0]['destination_hospital_id'], 'ADVANCED')

    def test_route_dispatch_preserves_care_gap_common_branches_and_input(self):
        value = scenario()
        original = copy.deepcopy(value)
        provider = MockRoadProvider()
        data = generate_replay(value, routing=RouteSnapshot(provider), dispatch_timing='route')
        changed = data['scenario']['patients'][0]
        self.assertEqual(value, original)
        self.assertEqual(changed['ambulance_arrive_t'], 4)  # dispatch 1 + road 3 min
        self.assertEqual(changed['search_start_t'], 7)  # original 3 min of scene care
        common_types = {'patient_occurs', 'ambulance_dispatched', 'ambulance_arrive',
                        'scene_care_start', 'scene_care_complete'}
        common = [[e for e in branch['events'] if e['type'] in common_types] for branch in data['branches']]
        self.assertTrue(all(events == common[0] for events in common))
        for branch in data['branches']:
            dispatch = branch['trajectories'][0]['legs'][0]
            self.assertEqual((dispatch['depart_t'], dispatch['arrive_t']), (1, 4))
            self.assertTrue(all(e['t'] >= 7 for e in branch['events'] if e['type'] == 'ask'))

    def test_route_snapshot_is_shared_across_algorithm_branches_and_replays(self):
        provider = MockRoadProvider()
        routing = RouteSnapshot(provider)
        first = generate_replay(scenario(), routing=routing)
        self.assertEqual(len(provider.calls), 4)  # dispatch and three candidate routes
        self.assertEqual(len(provider.calls), len(set(provider.calls)))
        second = generate_replay(scenario(), routing=routing)
        self.assertEqual(first, second)
        self.assertEqual(len(provider.calls), 4)
        self.assertEqual(first['routing_summary']['routes_evaluated'], 4)

    def test_fallback_summary_counts_unselected_candidate_routes(self):
        provider = MockRoadProvider(fallback_lat=37.30)
        policy = [dict(id='one', label='One request', rule='R0', k=1, wait=0, notify=True)]
        data = generate_replay(scenario(), policy, routing=RouteSnapshot(provider))
        summary = data['routing_summary']
        self.assertEqual(summary['routes_evaluated'], 4)
        self.assertEqual(summary['road_routes'], 3)
        self.assertEqual(summary['fallback_routes'], 1)
        # The fallback was considered as a candidate but never driven. It must
        # still be counted in the input provenance summary.
        self.assertTrue(all(leg['geometry_kind'] == 'road'
                            for leg in data['branches'][0]['trajectories'][0]['legs']))

    def test_access_connectors_increase_travel_time_but_remain_separate(self):
        provider = MockRoadProvider(snap_offset=.001)
        routing = RouteSnapshot(provider)
        route = routing.route([37.10, 127.0], [37.25, 127.0])
        self.assertGreater(route['duration_seconds'], route['road_duration_seconds'])
        self.assertAlmostEqual(route['duration_seconds'], route['road_duration_seconds']
                               + route['connector_duration_seconds'])
        self.assertEqual(route['motion_geometry'][0], [37.10, 127.0])
        self.assertNotEqual(route['geometry'][0], route['motion_geometry'][0])
        self.assertTrue(all(c['geometry_kind'] == 'offroad_connector' for c in route['connectors']))
        engine = ReplaySimulation(scenario(), Policy(), routing=routing)
        self.assertEqual(engine.travel(engine.patients['P'], 'FAR', (37.10, 127.0)),
                         math.ceil(route['duration_seconds'] / 60))


if __name__ == '__main__':
    unittest.main()
