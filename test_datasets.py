"""데이터 생성 재현성, 숨겨진 정보, 사건 재생과 알려진 경계조건을 검증."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from datasets.allocation import golden_cases, make_case
from datasets.episodes import episode_config, make_episode, world_from_episode
from datasets.generate import generate
from datasets.io import partition, read_jsonl, write_jsonl
from datasets.profiles import ALLOCATION_PROFILES, EPISODE_PROFILES
from datasets.replay import replay
from datasets.server_cases import PROFILES, make_server_case
from datasets.validate import check_golden, validate_corpus, validate_record
from server import db
from simulation.sim import CONFIG, ParallelGreedyPolicy, SequentialPolicy, Simulation, make_world


class DatasetTests(unittest.TestCase):
    def test_all_profiles_valid_and_reproducible(self):
        self.assertEqual(len(ALLOCATION_PROFILES), 50)
        for pid, desc, cfg in ALLOCATION_PROFILES:
            for seed in (0, 8, 9):
                a = make_case(pid, desc, cfg, seed)
                validate_record(a)
                self.assertEqual(a, make_case(pid, desc, cfg, seed))
                self.assertEqual(a['split'], partition(seed))

    def test_observed_actual_are_separate_and_error_has_direction(self):
        profiles = {p: (d, c) for p, d, c in ALLOCATION_PROFILES}
        for pid in ('stale_capacity', 'unobserved_walkin', 'travel_underestimate', 'duration_underestimate'):
            desc, cfg = profiles[pid]
            record = make_case(pid, desc, cfg, 0)
            self.assertNotEqual(record['observed'], record['actual'])
            actual = copy.deepcopy(record['actual'])
            record['observed']['patients'][0]['duration'] = 999
            self.assertEqual(record['actual'], actual)
        desc, cfg = profiles['unknown_priority']
        self.assertTrue(all(p['priority'] == 4 for p in make_case('unknown_priority', desc, cfg, 0)['observed']['patients']))

    def test_all_golden_labels_are_proven_on_small_instances(self):
        for record in golden_cases():
            validate_record(record)
            check_golden(record)

    def test_all_server_scenarios_replay(self):
        for pid in PROFILES:
            conn = db.connect(':memory:')
            try:
                record = make_server_case(pid, 0)
                validate_record(record)
                replay(record, conn)
            finally:
                conn.close()

    def test_episode_replay_matches_original_world(self):
        record = make_episode('normal', '기본', {}, 0)
        # JSON 왕복 후에도 숫자 키·집합·사건 순서가 복원된다.
        record = json.loads(json.dumps(record))
        validate_record(record)
        cfg = episode_config(record)
        for policy in (SequentialPolicy(), ParallelGreedyPolicy()):
            native = Simulation(0, policy, cfg).run()
            restored = Simulation(0, policy, cfg, world=world_from_episode(record)).run()
            native.pop('decision_ms_mean'), restored.pop('decision_ms_mean')   # 벽시계 계산시간만 실행마다 다르다
            self.assertEqual(native, restored)

    def test_episode_world_has_no_shared_mutable_state(self):
        record = make_episode('normal', '기본', {}, 0)
        pristine = copy.deepcopy(record)
        h1, p1, _ = world_from_episode(record)
        h2, p2, _ = world_from_episode(record)
        h1[0].cap['ed_bed'] = 999
        p1[0].demand['ed_bed'] = 999
        self.assertNotEqual(h1[0].cap, h2[0].cap)
        self.assertNotEqual(p1[0].demand, p2[0].demand)
        self.assertEqual(pristine, record)

    def test_zero_arrival_and_walkin_rates_are_supported(self):
        hs, ps, walkins = make_world(0, dict(CONFIG, arrival_rate=0, walkin_rate=0))
        self.assertTrue(hs)
        self.assertEqual((ps, walkins), ([], []))
        record = make_episode('no_walkins', '미등록 유입 없음', {'walkin_rate': 0}, 0)
        self.assertFalse(any(e['type'] == 'external_arrival' for e in record['events']))

    def test_all_episode_profiles_have_valid_events(self):
        for pid, desc, cfg in EPISODE_PROFILES:
            validate_record(make_episode(pid, desc, cfg, 0))

    def test_compression_is_byte_reproducible_and_filterable(self):
        with tempfile.TemporaryDirectory() as temp:
            records = [make_server_case('normal', s) for s in (0, 8, 9)]
            a, b = Path(temp) / 'a.gz', Path(temp) / 'b.gz'
            one, two = write_jsonl(a, records), write_jsonl(b, records)
            self.assertEqual(one['sha256'], two['sha256'])
            self.assertEqual([r['seed'] for r in read_jsonl(a, split='test')], [9])

    def test_small_corpus_manifest_and_all_replays(self):
        with tempfile.TemporaryDirectory() as temp:
            manifest = generate(temp, allocation_seeds=1, episode_seeds=1, server_seeds=1)
            report = validate_corpus(temp)
            self.assertEqual(report['records_validated'], manifest['total_records'])
            self.assertEqual(report['seed_split_leakage'], 0)
            self.assertEqual(report['server_cases_replayed'], len(PROFILES))


if __name__ == '__main__':
    unittest.main()
