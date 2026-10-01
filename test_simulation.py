"""시뮬레이터 불변조건 테스트. 실행: python -m unittest test_simulation -v"""
import unittest

from simulation.sim import CONFIG, ParallelGreedyPolicy, SequentialPolicy, Simulation, SolverPolicy, make_world


class Checked(Simulation):
    """매 치료 시작 직후 자원 용량을 넘지 않는지, 문의 동시 개수가 정책 한도 이내인지 확인한다."""

    def start_treatments(self, h, t):
        super().start_treatments(h, t)
        for r, cap in h.cap.items():
            assert h.used(r) <= cap, (h.id, r, t)

    def send_asks(self, p, t, exclude=()):
        super().send_asks(p, t, exclude)
        assert len(p.asks) <= self.policy.batch_size, (p.id, len(p.asks))


class SimulationTest(unittest.TestCase):
    def test_same_world_for_every_policy(self):
        for seed in (1, 2, 3):
            a, b = make_world(seed), make_world(seed)
            key = lambda w: [(p.id, p.t_req, p.loc, p.priority, p.duration) for p in w[1]]
            self.assertEqual(key(a), key(b))
            self.assertEqual([(t, h, p.duration) for t, h, p in a[2]], [(t, h, p.duration) for t, h, p in b[2]])

    def test_invariants_hold(self):
        for seed in range(1, 6):
            for policy in (SequentialPolicy(), ParallelGreedyPolicy()):
                sim = Checked(seed, policy)
                m = sim.run()
                self.assertEqual(m['treated'] + m['untreated'], m['patients'])
                for p in sim.patients.values():
                    if p.t_start is not None:
                        self.assertGreaterEqual(p.t_start, p.arrive_t)        # 도착 전 치료 시작 없음
                        self.assertGreaterEqual(p.arrive_t, p.t_commit + 1)   # 확정 뒤 이동
                    if policy.name.startswith('B0'):
                        self.assertEqual(p.redirects, 0)                      # 알림 없음 → 변경 없음

    def test_parallel_commits_faster_and_asks_more(self):
        b0 = [Simulation(s, SequentialPolicy()).run() for s in range(1, 11)]
        b1 = [Simulation(s, ParallelGreedyPolicy()).run() for s in range(1, 11)]
        self.assertTrue(all(y['time_to_commit'] <= x['time_to_commit'] for x, y in zip(b0, b1)))
        self.assertTrue(all(y['inquiries'] >= x['inquiries'] for x, y in zip(b0, b1)))

    def test_deterministic(self):
        self.assertEqual(Simulation(7, ParallelGreedyPolicy()).run(), Simulation(7, ParallelGreedyPolicy()).run())

    def test_solver_hook_is_explicitly_unimplemented(self):
        with self.assertRaises(NotImplementedError):
            Simulation(1, SolverPolicy(CONFIG['k_parallel'])).run()


if __name__ == '__main__':
    unittest.main()
