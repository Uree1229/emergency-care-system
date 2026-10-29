"""서버 문의 정책 검증 기록 → 지도 재생 변환의 일치 검사.

실행: python -m unittest test_server_replay_adapter -v
"""
import json
import unittest
from collections import Counter

from simulation.server_replay_adapter import DEFAULT_INPUT, DEFAULT_POLICIES, TRACE_TO_VIEWER, convert, select_scenarios


class ServerReplayAdapterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = json.loads(DEFAULT_INPUT.read_text(encoding='utf-8'))
        cls.payload = convert(cls.report)
        cls.raw = {r['scenario']['id']: {b['policy']: b for b in r['branches']} for r in cls.report['results']}

    def test_selection_is_lowest_seed_per_family_without_metrics(self):
        chosen = select_scenarios(self.report)
        families = {s['family'] for s in self.report['scenarios']}
        self.assertEqual({s['family'] for s in chosen}, families)
        for s in chosen:
            self.assertEqual(s['seed'], min(x['seed'] for x in self.report['scenarios'] if x['family'] == s['family']))
        # 결과 지표를 바꿔도 선택은 같다.
        tampered = json.loads(json.dumps(self.report))
        for r in tampered['results']:
            for b in r['branches']:
                for p in b['patients']:
                    p['selection_seconds'] = 0
        self.assertEqual([s['id'] for s in select_scenarios(tampered)], [s['id'] for s in chosen])

    def test_every_trace_event_is_preserved_one_to_one(self):
        for case in self.payload['scenarios']:
            sid = case['scenario']['source']['scenario_id']
            for branch in case['branches']:
                raw = self.raw[sid][branch['id']]
                converted = [(e['at_seconds'], e['server_type'], e.get('patient'), e.get('hospital'), e.get('inquiry_id'))
                             for e in branch['events'] if e['server_type'] in TRACE_TO_VIEWER]
                original = [(e['at'], e['type'], e.get('patient_id'), e.get('hospital_id'), e.get('inquiry_id'))
                            for e in raw['trace']]
                self.assertEqual(Counter(converted), Counter(original), (sid, branch['id']))
                for e in branch['events']:
                    self.assertAlmostEqual(e['t'], e['at_seconds'] / 60, places=5)

    def test_counts_and_selection_match_server_results(self):
        for case in self.payload['scenarios']:
            sid = case['scenario']['source']['scenario_id']
            for branch in case['branches']:
                raw = self.raw[sid][branch['id']]
                events = branch['events']
                self.assertEqual(sum(e['type'] == 'ask' for e in events), sum(p['inquiry_count'] for p in raw['patients']))
                commits = {e['patient']: (e['at_seconds'], e['hospital']) for e in events if e['type'] == 'commit'}
                for p in raw['patients']:
                    if p['failed']:
                        self.assertNotIn(p['id'], commits)
                    else:
                        self.assertEqual(commits[p['id']], (p['selected_at'], p['destination_hospital_id']))
                types = Counter(e['type'] for e in raw['trace'])
                self.assertEqual(sum(e['type'] == 'no_response' for e in events), types['reply_omitted'])
                self.assertEqual(sum(e['type'] == 'decline' for e in events), types['reply_decline'])
                self.assertEqual(sum(e['type'] == 'accept' for e in events), types['reply_accept'])

    def test_no_invented_treatment_and_failed_patients_have_no_route(self):
        for case in self.payload['scenarios']:
            for branch in case['branches']:
                types = {e['type'] for e in branch['events']}
                self.assertFalse(types & {'treatment_start', 'treatment_complete', 'transfer_depart', 'redirect'})
                for o in branch['patient_outcomes']:
                    legs = next(t['legs'] for t in branch['trajectories'] if t['patient'] == o['id'])
                    if o['state'] == 'UNASSIGNED':
                        self.assertEqual(legs, [])
                    else:
                        self.assertEqual(len(legs), 1)
                        self.assertEqual(legs[0]['geometry_kind'], 'synthetic')
                for e in branch['events']:
                    if e['type'] == 'arrive':
                        self.assertEqual(e['server_type'], 'estimated_arrival')
                failed = {o['id'] for o in branch['patient_outcomes'] if o['state'] == 'UNASSIGNED'}
                ends = {e['patient'] for e in branch['events'] if e['type'] == 'observation_end'}
                self.assertEqual(ends, failed)

    def test_queue_snapshots_are_consistent(self):
        for case in self.payload['scenarios']:
            sid = case['scenario']['source']['scenario_id']
            for branch in case['branches']:
                types = Counter(e['type'] for e in self.raw[sid][branch['id']]['trace'])
                frames = branch['inquiry_snapshots']
                for f in frames:
                    for h in f['hospitals']:
                        self.assertGreaterEqual(h['waiting'], 0)
                        self.assertGreaterEqual(h['processing'], 0)
                        self.assertLessEqual(h['processing'], h['workers'])
                last = frames[-1]['hospitals']
                self.assertEqual(sum(h['submitted'] for h in last), types['queue_added'])
                self.assertEqual(sum(h['completed'] for h in last), types['review_finished'])

    def test_mixed_outcomes_are_kept_and_labelled_synthetic(self):
        verdicts = [c['scenario']['comparison'] for c in self.payload['scenarios']]
        self.assertTrue(any('빠르거나' in v for v in verdicts))
        self.assertTrue(any('느리거나' in v for v in verdicts))
        for case in self.payload['scenarios']:
            self.assertTrue(case['scenario']['server_inquiry_only'])
            self.assertEqual([b['id'] for b in case['branches']], list(DEFAULT_POLICIES))
            self.assertIn('합성', ' '.join(case['scenario']['assumptions']))


if __name__ == '__main__':
    unittest.main()
