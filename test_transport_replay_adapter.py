"""서버 사건 일치, 사람의 변경 경계, 미해결 이송의 지도 변환 검사."""
from copy import deepcopy
import unittest

from server.transport_replay_experiment import run
from simulation.transport_replay_adapter import convert, transport_legs


class TransportReplayAdapter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = run()
        cls.payload = convert(cls.report)

    def test_exact_server_events_and_original_order_survive(self):
        for source, case in zip(self.report['scenarios'], self.payload['scenarios']):
            events = [e for e in case['branches'][0]['events'] if 'server_event_id' in e]
            self.assertEqual(len(events), len(source['trace']))
            for event, raw in zip(events, source['trace']):
                self.assertEqual(event['server_event_id'], raw['event_id'])
                self.assertEqual(event['raw_event'], raw['raw_event'])
                self.assertEqual(event['payload'], raw['payload'])
                self.assertEqual(event['at_seconds'], raw['at_seconds'])
                self.assertEqual(event['t'], raw['at_seconds'] / 60)

    def test_change_route_is_continuous_and_old_leg_never_reaches_old_hospital(self):
        case = next(c for c in self.payload['scenarios'] if c['scenario']['family'] == 'change')
        branch = case['branches'][0]
        redirects = [e for e in branch['events'] if e['type'] == 'redirect']
        self.assertEqual([(e['at_seconds'], e['hospital']) for e in redirects], [(95, 'H2')])
        legs = branch['trajectories'][0]['legs']
        before = next(l for l in legs if l['arrive_t'] == 95 / 60)
        after = next(l for l in legs if l['depart_t'] == 95 / 60)
        self.assertEqual(before['destination'], after['origin'])
        self.assertEqual(before['hospital'], 'H1')
        self.assertNotEqual(before['destination'], [35.17, 126.85])
        self.assertEqual(after['hospital'], 'H2')
        self.assertEqual(before['ended_by'], 'destination_changed')
        for a, b in zip(legs, legs[1:]):
            self.assertEqual(a['destination'], b['origin'])
            self.assertEqual(a['arrive_t'], b['depart_t'])

    def test_alternative_acceptance_is_separate_from_destination_change(self):
        case = next(c for c in self.payload['scenarios'] if c['scenario']['family'] == 'change')
        events = case['branches'][0]['events']
        self.assertEqual([(e['at_seconds'], e['hospital']) for e in events if e['type'] == 'accept'], [(10, 'H1'), (80, 'H2')])
        self.assertEqual([e['at_seconds'] for e in events if e['type'] == 'alternative_ready'], [83])
        self.assertEqual([e['at_seconds'] for e in events if e['type'] == 'redirect'], [95])
        frames = case['branches'][0]['transport_snapshots']
        self.assertTrue(all(p['destination_hospital_id'] in (None, 'H1') for f in frames if f['at_seconds'] < 95 for p in f['patients']))

    def test_no_arrival_treatment_or_transfer_is_invented_for_unresolved_case(self):
        case = next(c for c in self.payload['scenarios'] if c['scenario']['family'] == 'no_alternative')
        branch = case['branches'][0]
        self.assertFalse({'arrive', 'redirect', 'treatment_start', 'treatment_complete', 'transfer_depart'} & {e['type'] for e in branch['events']})
        self.assertIsNone(branch['patient_outcomes'][0]['final_arrive_t'])
        last = branch['trajectories'][0]['legs'][-1]
        self.assertEqual(last['destination'], [35.162, 126.85])
        self.assertEqual(last['arrive_t'], case['duration_minutes'])
        self.assertEqual(branch['patient_outcomes'][0]['state'], 'IN_TRANSIT')

    def test_impact_is_patient_specific_and_snapshots_are_not_final_state_duplicates(self):
        case = next(c for c in self.payload['scenarios'] if c['scenario']['family'] == 'unaffected')
        branch = case['branches'][0]
        self.assertEqual({e['patient'] for e in branch['events'] if e['type'] == 'recheck'}, {'A'})
        frames = branch['transport_snapshots']
        self.assertEqual(next(f for f in frames if f['action'] == 'depart_B')['patients'][1]['state'], 'IN_TRANSIT')
        self.assertEqual(frames[-1]['patients'][1]['state'], 'ARRIVED')
        self.assertTrue(all(e['type'] != 'redirect' for e in branch['events']))

    def test_missing_position_at_human_change_is_rejected(self):
        sc = deepcopy(next(s for s in self.report['scenarios'] if s['family'] == 'change'))
        sc['movement'] = [m for m in sc['movement'] if m['at_seconds'] != 95]
        with self.assertRaisesRegex(ValueError, '목적지 변경'):
            transport_legs(sc, 'A')

    def test_reversed_events_are_rejected_and_synthetic_limits_retained(self):
        report = deepcopy(self.report)
        report['scenarios'][0]['trace'].reverse()
        with self.assertRaises(ValueError):
            convert(report)
        for c in self.payload['scenarios']:
            self.assertTrue(c['scenario']['server_transport_replay'])
            self.assertFalse(c['scenario'].get('server_inquiry_only'))
            self.assertTrue(c['scenario']['synthetic'])
            self.assertEqual(len(c['branches']), 1)
            self.assertIn('성능', ' '.join(c['scenario']['assumptions']))


if __name__ == '__main__':
    unittest.main()
