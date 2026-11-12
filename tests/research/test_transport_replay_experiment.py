"""합성 수용 조건 변경 시연이 실제 서비스 기록과 사람 판단 순서를 보존하는지 검증한다."""
import json
from pathlib import Path
import tempfile
import unittest

from server import service
from server import transport_replay_experiment as experiment


class TransportReplayExperiment(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = experiment.run()
        cls.scenarios = {s['family']: s for s in cls.report['scenarios']}

    @staticmethod
    def patient(snapshot, pid='A'):
        return next(p for p in snapshot['patients'] if p['id'] == pid)

    def snapshot(self, family, action):
        return next(s for s in self.scenarios[family]['snapshots'] if s['action'] == action)

    def test_report_is_deterministic_json_with_explicit_synthetic_limits(self):
        self.assertEqual(self.report, experiment.run())
        self.assertEqual(json.loads(json.dumps(self.report)), self.report)
        self.assertTrue(self.report['synthetic'])
        self.assertEqual(self.report['kind'], 'server_transport_replay')
        self.assertEqual(set(self.scenarios), set(experiment.FAMILIES))
        self.assertGreater(len(self.report['limitations']), 5)

    def test_trace_is_exactly_the_isolated_service_database_events(self):
        with tempfile.TemporaryDirectory() as temporary:
            recorder = experiment.Recorder(Path(temporary) / 'trace.db', 'change')
            try:
                result = recorder.run()
                stored = service.events_since(recorder.conn, limit=10000)
                self.assertEqual([e['raw_event'] for e in result['trace']], stored)
                for sequence, (event, raw) in enumerate(zip(result['trace'], stored)):
                    self.assertEqual(event['sequence'], sequence)
                    self.assertEqual(event['event_id'], raw['id'])
                    self.assertEqual(event['type'], raw['type'])
                    self.assertEqual(event['payload'], raw['data'])
                    self.assertEqual(event['at_seconds'], raw['at'] - experiment.EPOCH)
            finally:
                recorder.close()

    def test_only_patient_requiring_changed_resource_is_flagged(self):
        scan = self.snapshot('unaffected', 'server_review_scan')
        affected, unaffected = self.patient(scan, 'A'), self.patient(scan, 'B')
        self.assertEqual(affected['transport_review']['state'], 'RECHECK_REQUIRED')
        self.assertTrue(affected['transport_review']['risk_signature'])
        self.assertEqual(unaffected['transport_review']['state'], 'MONITORING')
        self.assertFalse(unaffected['transport_review']['risk_signature'])
        final = self.scenarios['unaffected']['final_patients']
        self.assertEqual(next(p for p in final if p['id'] == 'B')['state'], 'ARRIVED')
        self.assertEqual(next(p for p in final if p['id'] == 'A')['state'], 'IN_TRANSIT')
        self.assertFalse(any(e['type'] == 'destination_changed' for e in self.scenarios['unaffected']['trace']))

    def test_keep_maintains_destination_and_does_not_open_alternatives(self):
        result = self.scenarios['keep']
        kept = self.patient(self.snapshot('keep', 'human_hospital_keep'))
        self.assertEqual(kept['destination_hospital_id'], 'H1')
        self.assertFalse(kept['needs_adjustment'])
        self.assertFalse(kept['transport_reassignment']['enabled'])
        self.assertIn('hospital_keeps', [e['type'] for e in result['trace']])
        self.assertNotIn('transport_inquiry_started', [e['type'] for e in result['trace']])
        self.assertEqual(result['final_patients'][0]['state'], 'ARRIVED')

    def test_alternative_acceptance_cannot_move_destination_before_human_change(self):
        accepted = self.patient(self.snapshot('change', 'synthetic_alternative_accept'))
        self.assertEqual(accepted['destination_hospital_id'], 'H1')
        # The first worker starts inquiries; the later one records ready state.
        ready = self.patient(next(s for s in self.scenarios['change']['snapshots']
                                  if s['action'] == 'server_alternative_worker' and s['at_seconds'] == 83))
        self.assertEqual(ready['transport_reassignment']['state'], 'AWAITING_CHANGE')
        self.assertEqual(ready['destination_hospital_id'], 'H1')
        changed = self.patient(self.snapshot('change', 'human_transporter_changes_destination'))
        self.assertEqual(changed['destination_hospital_id'], 'H2')
        self.assertFalse(changed['needs_adjustment'])
        events = self.scenarios['change']['trace']
        changes = [e for e in events if e['type'] == 'destination_changed']
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]['at_seconds'], 95)
        self.assertEqual(changes[0]['actor'], 'synthetic_transporter')
        self.assertEqual(changes[0]['payload']['from_hospital'], 'H1')
        self.assertEqual(self.scenarios['change']['final_patients'][0]['state'], 'ARRIVED')

    def test_no_alternative_does_not_fabricate_arrival_or_destination_change(self):
        result = self.scenarios['no_alternative']
        final = result['final_patients'][0]
        self.assertEqual(final['state'], 'IN_TRANSIT')
        self.assertEqual(final['destination_hospital_id'], 'H1')
        self.assertTrue(final['needs_adjustment'])
        self.assertEqual(final['transport_reassignment']['state'], 'EXHAUSTED')
        self.assertFalse(final['transport_reassignment']['enabled'])
        types = [e['type'] for e in result['trace']]
        self.assertNotIn('arrival_confirmed', types)
        self.assertNotIn('destination_changed', types)
        self.assertEqual(result['movement'][-1]['at_seconds'], result['observation_end_seconds'])
        self.assertEqual(result['metrics']['unresolved_count'], 1)

    def test_positions_and_resource_capacities_are_explicit_synthetic_inputs(self):
        for scenario in self.scenarios.values():
            times = [s['at_seconds'] for s in scenario['snapshots']]
            self.assertEqual(times, sorted(times))
            self.assertEqual(times[-1], scenario['observation_end_seconds'])
            self.assertTrue(scenario['synthetic'])
            self.assertTrue(all(m['source'] == 'synthetic_observation' for m in scenario['movement']))
            for movement in scenario['movement']:
                self.assertTrue(any(s['at_seconds'] == movement['at_seconds'] and
                    dict(patient_id=movement['patient_id'], lat=movement['lat'], lon=movement['lon'])
                    in s['synthetic_inputs']['positions'] for s in scenario['snapshots']))
            for hospital in scenario['hospitals']:
                self.assertIn('special_team', hospital['capacity'])
            self.assertNotIn('time_saved', scenario['metrics'])

    def test_rejects_unknown_family_without_using_existing_database(self):
        with self.assertRaises(ValueError):
            experiment.run_scenario('live')
        with tempfile.TemporaryDirectory() as temporary:
            recorder = experiment.Recorder(Path(temporary) / 'time.db', 'keep')
            try:
                recorder.action(5, 'later')
                with self.assertRaises(ValueError):
                    recorder.action(4, 'earlier')
            finally:
                recorder.close()


if __name__ == '__main__':
    unittest.main()
