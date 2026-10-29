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
        a, b = Simulation(7, ParallelGreedyPolicy()).run(), Simulation(7, ParallelGreedyPolicy()).run()
        a.pop('decision_ms_mean'), b.pop('decision_ms_mean')      # 실제 계산시간(벽시계)만 실행마다 다르다
        self.assertEqual(a, b)

    def test_solver_hook_is_explicitly_unimplemented(self):
        with self.assertRaises(NotImplementedError):
            Simulation(1, SolverPolicy(CONFIG['k_parallel'])).run()


# ---------------------------------------------------------------- R0~R3 · 문의 정책 · 조건 변경 대응

from simulation.experiment import evaluate, paired          # noqa: E402
from simulation.sim import Hospital, Patient, Policy        # noqa: E402

SMALL = dict(CONFIG, tail=120)


def tiny_world(hospitals, patients):
    return [Hospital(hid, loc, set(caps), dict(cap)) for hid, loc, caps, cap in hospitals], patients, []


class LegacyRegression(unittest.TestCase):
    def test_b0_b1_results_unchanged_after_refactor(self):
        b0, b1 = Simulation(1, SequentialPolicy()).run(), Simulation(1, ParallelGreedyPolicy()).run()
        self.assertEqual((b0['time_to_commit'], b0['inquiries'], b0['redirects']), (7.4, 61, 0))
        self.assertAlmostEqual(b0['mean_wait'], 28.42222222222222)
        self.assertEqual((b1['inquiries'], b1['redirects']), (135, 1))
        self.assertAlmostEqual(b1['time_to_commit'], 3.066666666666667)
        b1x = Simulation(3, ParallelGreedyPolicy(), dict(CONFIG, nonresponse=0.52, walkin_rate=0.012)).run()
        self.assertEqual((b1x['inquiries'], b1x['redirects']), (93, 0))


class FirstResponseVsArrival(unittest.TestCase):                 # 발표 대본 6장
    """먼 병원(FAR)이 1분 뒤, 가까운 병원(NEAR)이 2분 뒤 수용한다."""

    def run_policy(self, **spec):
        world = tiny_world([('FAR', (35.60, 126.85), [], {'ed_bed': 5}), ('NEAR', (35.165, 126.85), [], {'ed_bed': 5})],
                           [Patient('A', 0, (35.16, 126.85), set(), {'ed_bed': 1}, 4, 30)])
        delays = {'FAR': 1, 'NEAR': 2}
        sim = Simulation(1, Policy(**spec), SMALL, world=world, record=True, outages=[],
                         response_fn=lambda p, hid, n: (False, delays[hid]))
        sim.run()
        return sim.patients['A']

    def test_first_accept_vs_waiting_for_nearer(self):
        r0, r1_no_wait, r1_wait = (self.run_policy(rule='R0', k=2, wait=0), self.run_policy(rule='R1', k=2, wait=0),
                                   self.run_policy(rule='R1', k=2, wait=2))
        r0_wait = self.run_policy(rule='R0', k=2, wait=2)
        self.assertEqual((r0.dest, r0.t_commit), ('FAR', 1))
        self.assertEqual((r0_wait.dest, r0_wait.t_commit), ('FAR', 2))     # 기다려도 첫 수용 규칙은 먼 병원
        self.assertEqual((r1_no_wait.dest, r1_no_wait.t_commit), ('FAR', 1))   # 기다리지 않으면 규칙이 달라도 같다
        self.assertEqual((r1_wait.dest, r1_wait.t_commit), ('NEAR', 2))       # 1분 늦게 확정하지만 더 일찍 도착
        self.assertLess(r1_wait.final_arrive_t, r0.final_arrive_t)


class ResourceSpecificNotification(unittest.TestCase):           # 발표 대본 7장
    def run_policy(self, notify):
        world = tiny_world([('H', (35.20, 126.85), [], {'ed_bed': 5, 'special_team': 1}),
                            ('G', (35.25, 126.85), [], {'ed_bed': 5, 'special_team': 1})],
                           [Patient('A', 0, (35.16, 126.85), set(), {'ed_bed': 1, 'special_team': 1}, 4, 30),
                            Patient('B', 0, (35.16, 126.85), set(), {'ed_bed': 1}, 4, 30)])
        sim = Simulation(1, Policy(rule='R1', k=1, notify=notify), SMALL, world=world, record=True,
                         outages=[(2, 40, 'H', 'special_team')], response_fn=lambda p, hid, n: (False, 1))
        sim.run()
        return sim

    def test_only_patient_needing_lost_equipment_is_rechecked(self):
        sim = self.run_policy(notify=True)
        notified = [e['patient'] for e in sim.log if e['type'] == 'notify']
        self.assertEqual(notified, ['A'])
        self.assertEqual(sim.patients['A'].dest, 'G')         # 대체 병원 수용 응답 뒤에만 변경
        self.assertEqual(sim.patients['B'].dest, 'H')         # B의 배정은 유지
        redirect = [e for e in sim.log if e['type'] == 'redirect']
        accepts = [e for e in sim.log if e['type'] == 'accept' and e['patient'] == 'A' and e['hospital'] == 'G']
        self.assertTrue(redirect and accepts and accepts[0]['t'] <= redirect[0]['t'])

    def test_notification_off_keeps_everyone(self):
        sim = self.run_policy(notify=False)
        self.assertEqual((sim.patients['A'].dest, sim.patients['B'].dest), ('H', 'H'))
        self.assertFalse([e for e in sim.log if e['type'] == 'notify'])


class JointAssignment(unittest.TestCase):
    def test_last_bed_goes_to_higher_priority_and_capacity_holds(self):
        world = tiny_world([('ONE', (35.17, 126.85), [], {'ed_bed': 1}), ('FAR', (35.50, 126.85), [], {'ed_bed': 1})],
                           [Patient('LOW', 0, (35.16, 126.85), set(), {'ed_bed': 1}, 1, 30),
                            Patient('HIGH', 0, (35.16, 126.85), set(), {'ed_bed': 1}, 4, 30)])
        sim = Simulation(1, Policy(rule='R3', k=2, wait=1), SMALL, world=world, record=True, outages=[],
                         response_fn=lambda p, hid, n: (False, 1))
        sim.run()
        self.assertEqual((sim.patients['HIGH'].dest, sim.patients['LOW'].dest), ('ONE', 'FAR'))


class RuleInvariants(unittest.TestCase):
    def test_every_rule_commits_only_after_valid_acceptance(self):
        cfg = dict(CONFIG, arrival_rate=0.4, init_occupancy=0.85)
        for rule in ('R0', 'R1', 'R2', 'R3'):
            for seed in (1, 2):
                sim = Checked(seed, Policy(rule=rule, k=3, wait=2), cfg, record=True)
                m = sim.run()
                self.assertEqual(m['treated'] + m['untreated'], m['patients'])
                accepted = {(e['patient'], e['hospital']) for e in sim.log if e['type'] == 'accept'}
                for e in sim.log:
                    if e['type'] in ('commit', 'redirect'):
                        self.assertIn((e['patient'], e['hospital']), accepted)
                times = [e['t'] for e in sim.log]
                self.assertEqual(times, sorted(times))

    def test_policies_see_only_patients_already_requested(self):
        sim = Simulation(1, Policy(rule='R2'), CONFIG)
        self.assertTrue(all(q.t_req <= 30 for q in sim.visible_patients(30)))
        self.assertLess(len(sim.visible_patients(30)), len(sim.patients))

    def test_search_radius_reduces_inquiries_and_expands_when_needed(self):
        wide = [Simulation(s, Policy(rule='R1', k=3)).run() for s in (1, 2, 3)]
        near = [Simulation(s, Policy(rule='R1', k=3, near_minutes=10)).run() for s in (1, 2, 3)]
        self.assertLess(sum(m['inquiries'] for m in near), sum(m['inquiries'] for m in wide))
        self.assertEqual(sum(m['unassigned'] for m in near), 0)


class Experiment(unittest.TestCase):
    def test_paired_difference_of_identical_policy_is_zero(self):
        res = evaluate([dict(rule='R1', k=3, wait=0), dict(rule='R1', k=3, wait=0, name='same')], [1, 2], ['normal'])
        name = Policy(rule='R1', k=3, wait=0).name
        r = paired(res[name]['normal'], res['same']['normal'], 'arrive_mean')
        self.assertEqual((r['diff'], r['better'], r['worse']), (0, 0, 0))


if __name__ == '__main__':
    unittest.main()
