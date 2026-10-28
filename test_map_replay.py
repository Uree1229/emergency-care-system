"""지도 재생의 공통 입력, 인과 순서, 전원 및 미래정보 분리 시험."""
import copy
from contextlib import closing
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from simulation.map_replay import (DEFAULT_POLICIES, ReplaySimulation, generate_replay,
                                   load_public_hospitals, validate_scenario, main)
from simulation.sim import Policy


def scenario():
    return dict(id='unit', label='합성 시험', seed=17, synthetic=True, max_minutes=100,
                transfer_preparation_minutes=3,
                hospitals=[dict(id='NEAR', name='가상 가까운 병원', lat=37.15, lon=127.0,
                                capabilities=['basic'], capacity={'ed_bed': 3, 'special_team': 1}),
                           dict(id='FAR', name='가상 먼 병원', lat=37.25, lon=127.0,
                                capabilities=['basic'], capacity={'ed_bed': 3, 'special_team': 1}),
                           dict(id='ADVANCED', name='가상 추가치료 병원', lat=37.30, lon=127.0,
                                capabilities=['basic', 'advanced'], capacity={'ed_bed': 3, 'special_team': 1})],
                patients=[dict(id='P', label='가상 환자', location=[37.10, 127.0], occur_t=0,
                               dispatch_t=1, ambulance_origin=[37.08, 127.0], ambulance_arrive_t=7,
                               search_start_t=10, required_capabilities=['basic'],
                               resource_demands={'ed_bed': 1, 'special_team': 1},
                               treatment_minutes=15, priority=4)],
                exogenous_events=[], response_environment={'*': {'NEAR': dict(delay=3),
                                                                 'FAR': dict(delay=1),
                                                                 'ADVANCED': dict(delay=2)}})


class MapReplayTest(unittest.TestCase):
    def test_cli_preserves_multiple_synthetic_experiment_inputs(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as temp:
            folder = Path(temp)
            base = scenario()
            base['id'] = 'base'
            source = folder / 'base.json'
            source.write_text(json.dumps(base), encoding='utf-8')
            extras = []
            for index in (1, 2):
                value = scenario()
                value['id'] = f'extra{index}'
                extra = folder / f'extra{index}.json'
                extra.write_text(json.dumps({'scenarios': [{'scenario': value}]}), encoding='utf-8')
                extras.append(extra)
            output = folder / 'map.json'
            with redirect_stdout(io.StringIO()):
                main(['--scenario', str(source), '--routing', 'synthetic', '--output', str(output),
                      '--synthetic-experiment-replays', str(extras[0]),
                      '--synthetic-experiment-replays', str(extras[1])])
            payload = json.loads(output.read_text(encoding='utf-8'))
            self.assertEqual([r['scenario']['id'] for r in payload['scenarios']],
                             ['extra1', 'extra2', 'base'])
            self.assertTrue(all(r['routing_summary']['road_routes'] == 0
                                for r in payload['scenarios']))

    def test_duplicate_extra_input_leaves_previous_map_file_unchanged(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as temp:
            folder = Path(temp)
            value = scenario()
            source, extra, output = (folder / n for n in ('base.json', 'extra.json', 'map.json'))
            source.write_text(json.dumps(value), encoding='utf-8')
            extra.write_text(json.dumps({'scenarios': [{'scenario': value}]}), encoding='utf-8')
            output.write_text('previous map', encoding='utf-8')
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                main(['--scenario', str(source), '--routing', 'synthetic', '--output', str(output),
                      '--synthetic-experiment-replays', str(extra)])
            self.assertEqual(output.read_text(encoding='utf-8'), 'previous map')

    def test_repeated_replay_is_deterministic_and_input_is_unchanged(self):
        value = scenario()
        before = copy.deepcopy(value)
        first, second = generate_replay(value), generate_replay(value)
        self.assertEqual(value, before)
        self.assertEqual(first, second)
        json.dumps(first, ensure_ascii=False, allow_nan=False)

    def test_119_phases_are_common_and_no_request_precedes_scene_care(self):
        data = generate_replay(scenario())
        prefixes = [[e for e in b['events'] if e['t'] < 10] for b in data['branches']]
        self.assertTrue(all(p == prefixes[0] for p in prefixes))
        self.assertEqual([e['type'] for e in prefixes[0]],
                         ['patient_occurs', 'ambulance_dispatched', 'ambulance_arrive', 'scene_care_start'])
        for branch in data['branches']:
            asks = [e for e in branch['events'] if e['type'] == 'ask']
            self.assertTrue(all(e['t'] >= 10 for e in asks))
            self.assertEqual(branch['trajectories'][0]['legs'][0]['kind'], 'dispatch')

    def test_parallel_rules_share_inquiry_policy_but_choose_different_destinations(self):
        data = generate_replay(scenario())
        first, arrival = data['branches'][1:]
        for key in ('k', 'wait', 'notify'):
            self.assertEqual(first['policy'][key], arrival['policy'][key])
        self.assertEqual(first['patient_outcomes'][0]['destination_hospital_id'], 'FAR')
        self.assertEqual(arrival['patient_outcomes'][0]['destination_hospital_id'], 'NEAR')
        self.assertLess(arrival['metrics']['arrive_mean'], first['metrics']['arrive_mean'])
        self.assertEqual(arrival['metrics']['inquiries'], first['metrics']['inquiries'])
        self.assertTrue(all(b['policy']['notify'] for b in data['branches']))

    def test_every_movement_requires_acceptance_and_legs_are_ordered(self):
        data = generate_replay(scenario())
        for branch in data['branches']:
            prior_accepts = set()
            for event in branch['events']:
                if event['type'] == 'accept':
                    prior_accepts.add((event['patient'], event['hospital']))
                if event['type'] in ('commit', 'redirect', 'transfer_depart'):
                    self.assertIn((event['patient'], event['hospital']), prior_accepts)
            legs = branch['trajectories'][0]['legs']
            for left, right in zip(legs, legs[1:]):
                self.assertLessEqual(left['arrive_t'], right['depart_t'])
                self.assertEqual(left['destination'], right['origin'])
            self.assertTrue(all(leg['depart_t'] < leg['arrive_t'] for leg in legs))

    def test_arrival_assessment_restarts_from_hospital_after_common_preparation(self):
        value = scenario()
        value['patients'][0]['arrival_assessment_adds_capabilities'] = ['advanced']
        data = generate_replay(value)
        for branch in data['branches']:
            events = branch['events']
            unable = next(e for e in events if e['type'] == 'arrival_unable')
            search = next(e for e in events if e['type'] == 'transfer_search')
            depart = next(e for e in events if e['type'] == 'transfer_depart')
            self.assertEqual(search['t'], unable['t'] + 3)
            self.assertGreater(depart['t'], search['t'])
            outcome = branch['patient_outcomes'][0]
            self.assertEqual((outcome['transfers'], outcome['destination_hospital_id']), (1, 'ADVANCED'))
            transfer = next(leg for leg in branch['trajectories'][0]['legs'] if leg['kind'] == 'transfer')
            origin_hospital = next(h for h in value['hospitals'] if h['id'] == unable['hospital'])
            self.assertEqual(transfer['origin'], [origin_hospital['lat'], origin_hospital['lon']])
            self.assertGreater(outcome['final_arrive_t'], unable['t'])
            complete = next(e for e in events if e['type'] == 'treatment_complete')
            self.assertEqual(complete['t'], outcome['treatment_start_t'] + 15)

    def test_arrival_capacity_failure_transfers_in_all_notification_modes(self):
        value = scenario()
        # NEAR is selected at 13 and reached at 24. Failure at that same minute
        # cannot produce an earlier en-route notification.
        value['exogenous_events'] = [dict(t=24, type='resource_unavailable', hospital='NEAR',
                                           resource='special_team', until=90)]
        policies = [dict(id=f'n{flag}', label='test', rule='R1', k=3, wait=2, notify=flag)
                    for flag in (False, True)]
        data = generate_replay(value, policies)
        for branch in data['branches']:
            self.assertEqual(branch['metrics']['transfers'], 1)
            self.assertTrue(any(e['type'] == 'arrival_unable' for e in branch['events']))

    def test_notification_only_affects_patients_requiring_changed_resource(self):
        value = scenario()
        second = copy.deepcopy(value['patients'][0])
        second.update(id='B', resource_demands={'ed_bed': 1})
        value['patients'].append(second)
        value['exogenous_events'] = [dict(t=14, type='resource_unavailable', hospital='NEAR',
                                           resource='special_team', until=90)]
        data = generate_replay(value, [dict(id='one', label='test', rule='R1', k=3, wait=2, notify=True)])
        notices = [e['patient'] for e in data['branches'][0]['events'] if e['type'] == 'notify']
        self.assertEqual(notices, ['P'])
        outcomes = {p['id']: p for p in data['branches'][0]['patient_outcomes']}
        self.assertEqual(outcomes['B']['destination_hospital_id'], 'NEAR')

    def test_enroute_alternative_search_continues_after_first_batch_is_silent(self):
        value = scenario()
        value['response_environment']['*']['FAR']['silent'] = True
        value['exogenous_events'] = [dict(t=14, type='resource_unavailable', hospital='NEAR',
                                           resource='special_team', until=90)]
        policies = [dict(id='one', label='test', rule='R0', k=1, wait=0, notify=True)]
        branch = generate_replay(value, policies)['branches'][0]
        events = branch['events']
        far_timeout = next(e for e in events if e['type'] == 'no_response' and e['hospital'] == 'FAR')
        advanced_ask = next(e for e in events if e['type'] == 'ask' and e['hospital'] == 'ADVANCED')
        redirect = next(e for e in events if e['type'] == 'redirect')
        self.assertEqual((far_timeout['t'], advanced_ask['t']), (20, 20))
        self.assertEqual((redirect['t'], redirect['hospital']), (22, 'ADVANCED'))
        self.assertEqual(branch['metrics']['transfers'], 0)
        self.assertFalse(any(e['type'] == 'arrival_unable' for e in events))

    def test_all_silent_does_not_imply_acceptance_or_zero_selection_time(self):
        value = scenario()
        value['max_minutes'] = 35
        for entry in value['response_environment']['*'].values():
            entry['silent'] = True
        data = generate_replay(value)
        for branch in data['branches']:
            self.assertIsNone(branch['metrics']['time_to_commit'])
            self.assertEqual(branch['metrics']['unassigned'], 1)
            self.assertFalse(any(e['type'] in ('accept', 'commit', 'arrive') for e in branch['events']))
            self.assertTrue(any(e['type'] == 'no_response' for e in branch['events']))

    def test_unassigned_replay_reaches_observation_end_with_terminal_outcome(self):
        value = scenario()
        value['max_minutes'] = 60
        for h in value['hospitals']:
            h['capacity']['special_team'] = 0
        data = generate_replay(value)
        self.assertEqual(data['duration_minutes'], 60)
        for branch in data['branches']:
            outcome = branch['patient_outcomes'][0]
            self.assertEqual(outcome['state'], 'UNASSIGNED')
            end = branch['events'][-1]
            self.assertEqual((end['t'], end['type'], end['state']), (60, 'observation_end', 'UNASSIGNED'))
            self.assertIsNone(outcome['treatment_complete_t'])

    def test_observation_before_119_arrival_does_not_emit_future_common_events(self):
        value = scenario()
        value['max_minutes'] = 5
        data = generate_replay(value)
        self.assertEqual(data['duration_minutes'], 5)
        for branch in data['branches']:
            self.assertTrue(all(e['t'] <= 5 for e in branch['events']))
            self.assertEqual(branch['metrics']['dispatch_travel_minutes'], 4)
            self.assertEqual(branch['metrics']['travel_minutes'], 0)
            self.assertFalse(any(e['type'] in ('ambulance_arrive', 'scene_care_complete', 'ask')
                                 for e in branch['events']))

    def test_committed_but_not_arrived_is_not_treated_at_observation_end(self):
        value = scenario()
        value['max_minutes'] = 15
        data = generate_replay(value)
        self.assertEqual(data['duration_minutes'], 15)
        for branch in data['branches']:
            self.assertEqual(branch['patient_outcomes'][0]['state'], 'NOT_TREATED')
            self.assertEqual(branch['events'][-1]['type'], 'observation_end')
            self.assertEqual(branch['events'][-1]['t'], 15)
            # All policies commit at 13; only two transport minutes are observed.
            self.assertEqual(branch['metrics']['travel_minutes'], 2)
            transport = branch['trajectories'][0]['legs'][1]
            self.assertGreater(transport['planned_arrive_t'], 15)

    def test_treatment_in_progress_is_incomplete_not_complete(self):
        value = scenario()
        value['max_minutes'] = 60
        value['patients'][0]['treatment_minutes'] = 100
        data = generate_replay(value)
        self.assertEqual(data['duration_minutes'], 60)
        for branch in data['branches']:
            outcome = branch['patient_outcomes'][0]
            self.assertEqual(outcome['state'], 'INCOMPLETE')
            self.assertIsNotNone(outcome['treatment_start_t'])
            self.assertIsNone(outcome['treatment_complete_t'])
            self.assertEqual(branch['events'][-1]['state'], 'INCOMPLETE')
            self.assertFalse(any(e['type'] == 'treatment_complete' for e in branch['events']))

    def test_temporary_unavailability_is_retried_after_observed_restoration(self):
        value = scenario()
        value['exogenous_events'] = [dict(t=0, type='resource_unavailable', hospital=h['id'],
                                           resource='special_team', until=20) for h in value['hospitals']]
        data = generate_replay(value)
        for branch in data['branches']:
            self.assertEqual(branch['metrics']['treated'], 1)
            self.assertEqual(branch['patient_outcomes'][0]['state'], 'COMPLETE')
            self.assertFalse(any(e['type'] == 'no_compatible' for e in branch['events']))
            accepts = [e for e in branch['events'] if e['type'] == 'accept']
            self.assertTrue(accepts)
            self.assertTrue(all(e['t'] >= 20 for e in accepts))
            asks = [e for e in branch['events'] if e['type'] == 'ask']
            self.assertEqual(asks[0]['t'], 10)
            self.assertTrue(any(e['type'] == 'decline' and e['t'] < 20 for e in branch['events']))

    def test_future_resource_change_and_actual_travel_error_are_not_in_policy_input(self):
        value = scenario()
        value['travel_error_range'] = [2.0, 2.0]
        value['exogenous_events'] = [dict(t=50, type='resource_unavailable', hospital='NEAR',
                                           resource='special_team', until=90)]
        policy = Policy(rule='R1', k=3, wait=2)
        sim = ReplaySimulation(value, policy)
        observed = []
        original = policy.choose

        def choose(engine, patient, options, t):
            selected = original(engine, patient, options, t)
            observed.append((t, engine.hospitals['NEAR'].cap['special_team'],
                             engine.travel(patient, selected, engine.position(patient, t))))
            return selected

        policy.choose = choose
        data = sim.run_replay()
        self.assertEqual(observed[0][1], 1)
        transport = data['trajectories'][0]['legs'][1]
        self.assertEqual(transport['arrive_t'] - transport['depart_t'], 2 * observed[0][2])

    def test_public_identity_loading_is_read_only_and_missing_db_is_explicit(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            path = Path(directory) / 'public.db'
            with closing(sqlite3.connect(path)) as connection:
                connection.execute('CREATE TABLE hospitals(id TEXT,name TEXT,lat REAL,lon REAL)')
                connection.executemany('INSERT INTO hospitals VALUES(?,?,?,?)',
                                       [(f'H{i}', f'기관{i}', 37.1 + i / 100, 127.0) for i in range(8)])
                connection.commit()
            before = path.read_bytes()
            rows = load_public_hospitals(path)
            self.assertEqual(len(rows), 8)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(set(rows[0]), {'id', 'name', 'lat', 'lon'})
            with self.assertRaises(FileNotFoundError):
                load_public_hospitals(Path(directory) / 'absent.db')

    def test_invalid_common_phase_order_is_rejected(self):
        value = scenario()
        value['patients'][0]['search_start_t'] = 3
        with self.assertRaises(ValueError):
            validate_scenario(value)


if __name__ == '__main__':
    unittest.main()
