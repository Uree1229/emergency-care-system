"""서버 기록의 환자별 상대 시간과 지도 재생의 절대 시간 회귀 검사."""
import copy
import hashlib
import io
import json
import tempfile
import unittest
from collections import Counter
from contextlib import redirect_stdout
from pathlib import Path

from simulation.server_replay_adapter import (
    DEFAULT_INPUT, DEFAULT_OUTPUT, TRACE_TO_VIEWER, convert, convert_branch, main, queue_snapshots,
)


class ServerReplayTimeRegressionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source_bytes = DEFAULT_INPUT.read_bytes()
        cls.report = json.loads(cls.source_bytes)
        by_id = {s['id']: s for s in cls.report['scenarios']}
        cls.converted = [
            (by_id[result['scenario']['id']], raw, convert_branch(raw, by_id[result['scenario']['id']]))
            for result in cls.report['results'] for raw in result['branches']
        ]

    def test_transport_duration_matches_raw_travel_for_every_validation_branch(self):
        """발생 시각이 다른 환자도 선정 후 이동에 원래 이동시간이 걸린다."""
        checked = 0
        for scenario, raw, replay in self.converted:
            by_patient = {t['patient']: t['legs'] for t in replay['trajectories']}
            for patient in raw['patients']:
                with self.subTest(scenario=scenario['id'], policy=raw['policy'], patient=patient['id']):
                    if patient['failed']:
                        self.assertEqual(by_patient[patient['id']], [])
                        continue
                    checked += 1
                    leg, = by_patient[patient['id']]
                    self.assertAlmostEqual(leg['depart_t'] * 60, patient['selected_at'], places=4)
                    self.assertAlmostEqual((leg['arrive_t'] - leg['depart_t']) * 60,
                                           patient['travel_seconds'], places=4)
                    self.assertGreater(leg['arrive_t'], leg['depart_t'])
        self.assertGreater(checked, 0)

    def test_arrival_event_and_outcome_use_absolute_scene_time(self):
        """raw estimated_arrival_seconds는 발생 후 경과 시간이며 절대 시각이 아니다."""
        for scenario, raw, replay in self.converted:
            arrivals = {e['patient']: e for e in replay['events'] if e['type'] == 'arrive'}
            outcomes = {p['id']: p for p in replay['patient_outcomes']}
            for patient in raw['patients']:
                with self.subTest(scenario=scenario['id'], policy=raw['policy'], patient=patient['id']):
                    outcome = outcomes[patient['id']]
                    if patient['failed']:
                        self.assertNotIn(patient['id'], arrivals)
                        self.assertIsNone(outcome['final_arrive_t'])
                        continue
                    absolute_arrival = patient['created_at'] + patient['estimated_arrival_seconds']
                    # 두 독립적인 원본 표현이 같은 절대 시각이어야 한다.
                    self.assertEqual(absolute_arrival, patient['selected_at'] + patient['travel_seconds'])
                    event = arrivals[patient['id']]
                    self.assertEqual(event['at_seconds'], absolute_arrival)
                    self.assertAlmostEqual(event['t'] * 60, absolute_arrival, places=4)
                    self.assertAlmostEqual(outcome['final_arrive_t'] * 60, absolute_arrival, places=4)

    def test_arrival_mean_matches_raw_patient_relative_durations(self):
        for scenario, raw, replay in self.converted:
            selected = [p['estimated_arrival_seconds'] for p in raw['patients'] if not p['failed']]
            value = replay['metrics']['occur_to_final_arrival_mean']
            with self.subTest(scenario=scenario['id'], policy=raw['policy']):
                if not selected:
                    self.assertIsNone(value)
                else:
                    self.assertAlmostEqual(value, sum(selected) / len(selected) / 60, places=5)

    def test_staggered_patient_fixtures_expose_nonzero_offsets(self):
        """동시에 발생한 환자만 검사해 시간축 오류가 숨지 않도록 한다."""
        staggered = [(raw, replay) for scenario, raw, replay in self.converted
                     if scenario['family'] == 'staggered_w2']
        self.assertTrue(staggered)
        for raw, replay in staggered:
            created = {p['created_at'] for p in raw['patients']}
            self.assertGreater(len(created), 1)
            arrivals = {e['patient']: e for e in replay['events'] if e['type'] == 'arrive'}
            later_selected = [p for p in raw['patients'] if not p['failed'] and p['created_at'] > 0]
            self.assertTrue(later_selected)
            for patient in later_selected:
                self.assertEqual(arrivals[patient['id']]['at_seconds'] - patient['estimated_arrival_seconds'],
                                 patient['created_at'])

    def test_source_identity_locations_trace_fields_and_input_are_preserved(self):
        before = copy.deepcopy(self.report)
        payload = convert(self.report)
        self.assertEqual(self.report, before)
        for scenario, raw, replay in self.converted:
            trace_events = [e for e in replay['events'] if e['server_type'] in TRACE_TO_VIEWER]
            self.assertEqual(len(trace_events), len(raw['trace']))
            # 조사·판단 근거 필드까지 원본 기록이 사라지거나 덮어써지지 않아야 한다.
            original = Counter(json.dumps(e, sort_keys=True) for e in raw['trace'])
            restored = []
            for event in trace_events:
                reconstructed = {k: v for k, v in event.items()
                                 if k not in ('t', 'type', 'server_type', 'at_seconds', 'patient', 'hospital',
                                              'server_sequence')}
                reconstructed.update(at=event['at_seconds'], type=event['server_type'])
                restored.append(json.dumps(reconstructed, sort_keys=True))
            self.assertEqual(Counter(restored), original)
            hospitals = {h['id']: h for h in scenario['hospitals']}
            patients = {p['id']: p for p in scenario['patients']}
            self.assertEqual({t['patient'] for t in replay['trajectories']}, set(patients))
            for trajectory in replay['trajectories']:
                for leg in trajectory['legs']:
                    patient, hospital = patients[trajectory['patient']], hospitals[leg['hospital']]
                    self.assertEqual(leg['origin'], [patient['lat'], patient['lon']])
                    self.assertEqual(leg['destination'], [hospital['lat'], hospital['lon']])
        raw_scenarios = {s['id']: s for s in self.report['scenarios']}
        for case in payload['scenarios']:
            scenario = case['scenario']
            original = raw_scenarios[scenario['source']['scenario_id']]
            self.assertEqual({(h['id'], h['lat'], h['lon']) for h in scenario['hospitals']},
                             {(h['id'], h['lat'], h['lon']) for h in original['hospitals']})
            self.assertEqual({(p['id'], tuple(p['location'])) for p in scenario['patients']},
                             {(p['id'], (p['lat'], p['lon'])) for p in original['patients']})

    def test_saved_viewer_artifact_has_current_source_hash_and_absolute_arrivals(self):
        """수정된 변환기만 통과하고 배포 JSON은 옛 시각으로 남는 경우도 탐지한다."""
        artifact = json.loads(DEFAULT_OUTPUT.read_text(encoding='utf-8'))
        self.assertEqual(artifact['source_sha256'], hashlib.sha256(self.source_bytes).hexdigest())
        raw = {r['scenario']['id']: {b['policy']: b for b in r['branches']}
               for r in self.report['results']}
        for case in artifact['scenarios']:
            sid = case['scenario']['source']['scenario_id']
            for branch in case['branches']:
                arrivals = {e['patient']: e for e in branch['events'] if e['type'] == 'arrive'}
                for patient in raw[sid][branch['id']]['patients']:
                    if not patient['failed']:
                        self.assertEqual(arrivals[patient['id']]['at_seconds'],
                                         patient['created_at'] + patient['estimated_arrival_seconds'])

    def test_same_time_trace_order_preserves_original_causality(self):
        checked_ties = 0
        for scenario, raw, replay in self.converted:
            events = [e for e in replay['events'] if e['server_type'] in TRACE_TO_VIEWER]
            original = [(e['at'], e['type'], e.get('patient_id'), e.get('hospital_id'), e.get('inquiry_id'))
                        for e in raw['trace']]
            converted = [(e['at_seconds'], e['server_type'], e.get('patient_id'), e.get('hospital_id'), e.get('inquiry_id'))
                         for e in events]
            self.assertEqual(converted, original, (scenario['id'], raw['policy']))
            checked_ties += sum(a['at'] == b['at'] for a, b in zip(raw['trace'], raw['trace'][1:]))
        self.assertGreater(checked_ties, 0)

    def test_decisions_follow_observed_replies_and_precede_same_time_submission(self):
        checked_replies, checked_submissions = 0, 0
        for scenario, raw, replay in self.converted:
            by_time = {}
            for event in replay['events']:
                by_time.setdefault(event['at_seconds'], []).append(event)
            for at, events in by_time.items():
                decision_positions = [i for i, e in enumerate(events) if e['server_type'] == 'decision']
                if not decision_positions:
                    continue
                replies = [i for i, e in enumerate(events) if e['server_type'] in
                           ('reply_accept', 'reply_decline', 'reply_omitted', 'destination_confirmed')]
                submissions = [i for i, e in enumerate(events) if e['server_type'] in
                               ('queue_added', 'review_started')]
                if replies:
                    checked_replies += 1
                    self.assertLess(max(replies), min(decision_positions), (scenario['id'], raw['policy'], at))
                if submissions:
                    checked_submissions += 1
                    self.assertLess(max(decision_positions), min(submissions), (scenario['id'], raw['policy'], at))
        self.assertGreater(checked_replies, 0)
        self.assertGreater(checked_submissions, 0)

    def test_cli_custom_input_labels_actual_source_and_hash(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[2]) as folder:
            source = Path(folder) / 'copied-validation.json'
            output = Path(folder) / 'viewer.json'
            source.write_bytes(self.source_bytes)
            with redirect_stdout(io.StringIO()):
                main(['--input', str(source), '--output', str(output)])
            artifact = json.loads(output.read_text(encoding='utf-8'))
            self.assertEqual(artifact['source_sha256'], hashlib.sha256(source.read_bytes()).hexdigest())
            for case in artifact['scenarios']:
                labelled = Path(case['scenario']['source']['file'])
                resolved = labelled if labelled.is_absolute() else Path(__file__).resolve().parents[2] / labelled
                self.assertEqual(resolved.resolve(), source.resolve())

    def test_default_policy_selection_follows_report_winner(self):
        changed = copy.deepcopy(self.report)
        previous = changed['selected_policy']['name']
        alternative = 'v3_w60_h600'
        changed['selected_policy']['name'] = alternative
        for result in changed['results']:
            for branch in result['branches']:
                if branch['policy'] == previous:
                    branch['policy'] = alternative
                    branch['policy_spec']['name'] = alternative
        payload = convert(changed)
        for case in payload['scenarios']:
            self.assertEqual([b['id'] for b in case['branches']], ['fixed_3', 'observed_v2', alternative])

    def test_queue_wait_mean_includes_time_spent_waiting_before_cancellation(self):
        cancellation_count = 0
        for scenario, raw, replay in self.converted:
            queued, elapsed, submitted = {}, 0, 0
            for event in raw['trace']:
                key = (event.get('hospital_id'), event.get('inquiry_id'))
                if event['type'] == 'queue_added':
                    self.assertNotIn(key, queued)
                    queued[key] = event['at']
                    submitted += 1
                elif event['type'] in ('review_started', 'review_cancelled_before_start'):
                    self.assertIn(key, queued)
                    elapsed += event['at'] - queued.pop(key)
                    cancellation_count += event['type'] == 'review_cancelled_before_start'
            self.assertFalse(queued, (scenario['id'], raw['policy']))
            expected = elapsed / submitted / 60 if submitted else None
            actual = replay['metrics']['inquiry_queue_wait_mean']
            if expected is None:
                self.assertIsNone(actual)
            else:
                self.assertAlmostEqual(actual, expected, places=10)
        self.assertGreater(cancellation_count, 0)

    def test_cancelled_waiting_job_is_accounted_without_consuming_a_worker(self):
        trace = [
            dict(at=0, type='queue_added', hospital_id='H', inquiry_id='Q1'),
            dict(at=0, type='review_started', hospital_id='H', inquiry_id='Q1'),
            dict(at=5, type='queue_added', hospital_id='H', inquiry_id='Q2'),
            dict(at=7, type='queue_added', hospital_id='H', inquiry_id='Q3'),
            dict(at=25, type='review_cancelled_before_start', hospital_id='H', inquiry_id='Q2'),
            dict(at=30, type='review_finished', hospital_id='H', inquiry_id='Q1', service_seconds=30),
            dict(at=30, type='review_started', hospital_id='H', inquiry_id='Q3'),
            dict(at=50, type='review_finished', hospital_id='H', inquiry_id='Q3', service_seconds=20),
        ]
        frames, peak = queue_snapshots(trace, [dict(id='H', workers=1)])
        final, = frames[-1]['hospitals']
        self.assertEqual(peak, 2)
        self.assertEqual((final['submitted'], final['completed'], final['waiting'], final['processing']),
                         (3, 2, 0, 0))
        self.assertAlmostEqual(final['queue_wait_total_minutes'] * 60, 20 + 23)
        self.assertAlmostEqual(final['service_total_minutes'] * 60, 30 + 20)
        self.assertTrue(all(f['hospitals'][0]['processing'] <= 1 for f in frames))


if __name__ == '__main__':
    unittest.main()
