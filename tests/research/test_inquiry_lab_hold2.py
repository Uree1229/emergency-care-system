"""24차 확정 대기 개선: 규칙 이름·22차 실행기/2차 계산과의 동등성·처리 인원 반영 방향·선택/판정 규칙·seed 검사."""
import copy
import unittest
from unittest import mock

from inquiry_lab import (confirm_capacity_policy as ccp, confirm_policy as cp, eat_capacity_policy as ek, eat_seen_policy as es,
                         experiment_hold2 as ex, hold2_runner as h2, hold_runner as hr, mass_scenarios as ms, scenarios)


class RuleTest(unittest.TestCase):
    def test_names(self):
        self.assertEqual(h2.parse_rule(ex.BASE)['hold'], 120)
        self.assertFalse(h2.parse_rule(ex.BASE)['hold_capacity'])
        parsed = [h2.parse_rule(c) for c in ex.CANDIDATES]
        self.assertEqual([(p['hold'], p['hold_capacity']) for p in parsed], [(120, True), (180, False), (180, True)])
        for bad in ('cfk60', 'base+cfk0', 'base+cf60+cfk60', 'base+cfk60+cf60'):
            with self.assertRaises(ValueError):
                h2.parse_rule(bad)
        self.assertEqual(len(set(ex.RULES)), 4)


class RunnerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ek.install([])
        es.install([ex.FOCUS])
        cls.mass = ms.make_mass(ex.DEV_MASS[0])
        seed = next(s for s in ex.DEV_MIX if scenarios.make_mix(s)['mix']['arrival'] == 'burst')
        cls.burst = scenarios.make_mix(seed)

    @classmethod
    def tearDownClass(cls):
        es.uninstall()
        ek.uninstall()

    def test_without_capacity_rule_matches_round22_runner(self):
        arms = [('fixed_3', 'base'), (ex.FOCUS, ex.BASE), ('fixed_3', ex.CANDIDATES[1])]
        for scenario in (self.burst, self.mass):
            ours, theirs = h2.run_scenario(scenario, arms), hr.run_scenario(scenario, arms)
            for a, b in zip(ours['branches'], theirs['branches']):
                for key in ('patients', 'metrics', 'trace', 'backup', 'silent_closed', 'replans', 'holds'):
                    self.assertEqual(a[key], b[key], key)

    def test_policy_matches_round2_and_more_workers_raise_acceptance(self):
        calls = []
        original = cp.decide

        def record(**kw):
            calls.append(copy.deepcopy(kw))
            return original(**kw)
        with mock.patch.object(cp, 'decide', side_effect=record):
            hr.run_scenario(self.mass, [(ex.FOCUS, ex.BASE)])
        self.assertTrue(calls)
        raised = 0
        for kw in calls:
            self.assertEqual(ccp.decide(**kw), original(**kw))
            self.assertEqual(ccp.decide(**kw, workers_of=lambda h: 1), original(**kw))
            one, two = original(**kw), ccp.decide(**kw, workers_of=lambda h: 2)
            if 'nearer_accept_probability' in one['evidence']:
                p1, p2 = one['evidence']['nearer_accept_probability'], two['evidence']['nearer_accept_probability']
                self.assertGreaterEqual(p2, p1 - 1e-9)       # 처리 인원이 늘면 가까운 병원 수용이 빨라진다
                raised += p2 > p1 + 1e-9
        self.assertGreater(raised, 0)

    def test_capacity_holds_are_bounded_and_logged(self):
        branch = h2.run_scenario(self.mass, [(ex.FOCUS, ex.CANDIDATES[2])])['branches'][0]
        ended = [t for t in branch['trace'] if t['type'] == 'confirmation_hold_ended']
        self.assertEqual(len(ended), branch['holds']['count'])
        self.assertTrue(all(t['held_seconds'] <= 180 for t in ended))
        for p in branch['patients']:
            if not p['failed']:
                self.assertLessEqual(p['selection_seconds'], 300)


class ProtocolTest(unittest.TestCase):
    def report(self, mix, mass):
        def rows(deltas):
            return {c: dict(versus_base=dict(delta_J_mean=deltas[c], ci95=[deltas[c] - 3, deltas[c] + 3])) for c in ex.CANDIDATES}
        return dict(groups=dict(mix={ex.FOCUS: rows(mix)}, mass={ex.FOCUS: rows(mass)}))

    def test_selection_and_verdict(self):
        k120, c180, k180 = ex.CANDIDATES
        r = self.report({k120: -1.0, c180: 1.0, k180: 3.0}, {k120: -2.0, c180: -6.0, k180: -9.0})
        s = ex.select(r)
        self.assertEqual(s['rule'], c180)                   # k180은 혼합 +3초라 제외
        v = ex.verdict(r, c180)
        self.assertTrue(v['adopted'])                       # 혼합 1 + 3 < 5, 다수 −6 + 3 < 0
        self.assertFalse(ex.verdict(r, k120)['adopted'])    # 둘 다 상한이 0 이상
        r = self.report({c: 1.0 for c in ex.CANDIDATES}, {c: -0.5 for c in ex.CANDIDATES})
        self.assertFalse(ex.select(r)['proceed'])

    def test_new_seeds_were_never_used(self):
        from inquiry_lab import (experiment_ack, experiment_aware, experiment_burden, experiment_capacity,
                                 experiment_combo, experiment_confirm, experiment_coord, experiment_duty, experiment_event,
                                 experiment_fair, experiment_final, experiment_hold, experiment_interrupt, experiment_order,
                                 experiment_order2, experiment_public, experiment_seen, experiment_silent, experiment_status,
                                 experiment_surge, experiment_tune, experiment_voi, experiment_wide)
        used = (set(scenarios.DEVELOPMENT_MIX_SEEDS) | set(scenarios.VALIDATION_MIX_SEEDS)
                | set(scenarios.DEVELOPMENT_FAMILY_SEEDS) | set(scenarios.VALIDATION_FAMILY_SEEDS))
        mods = (experiment_confirm, experiment_public, experiment_ack, experiment_status, experiment_burden, experiment_coord,
                experiment_capacity, experiment_interrupt, experiment_order, experiment_order2, experiment_wide,
                experiment_aware, experiment_tune, experiment_seen, experiment_surge, experiment_fair, experiment_silent,
                experiment_event, experiment_hold, experiment_duty)
        for m in mods:
            used |= set(m.DEV_MIX) | set(m.VAL_MIX) | set(m.DEV_FAMILY) | set(m.VAL_FAMILY)
            used |= set(getattr(m, 'DEV_MASS', ())) | set(getattr(m, 'VAL_MASS', ()))
        for m in (experiment_final, experiment_combo):
            used |= set(m.MIX) | set(m.MASS) | set(m.FAMILY)
        used |= set(experiment_voi.MIX) | set(experiment_voi.FAMILY) | {999990001, 999990002}
        new = set(ex.DEV_MIX) | set(ex.VAL_MIX) | set(ex.DEV_MASS) | set(ex.VAL_MASS) | set(ex.DEV_FAMILY) | set(ex.VAL_FAMILY)
        self.assertFalse(new & used)
        for a, b in ((ex.DEV_MIX, ex.VAL_MIX), (ex.DEV_MASS, ex.VAL_MASS), (ex.DEV_MIX, ex.DEV_MASS), (ex.VAL_MIX, ex.VAL_MASS)):
            self.assertFalse(set(a) & set(b))
        self.assertFalse(any(lo <= s <= hi for s in new for lo, hi in scenarios.USED_SEED_RANGES))


if __name__ == '__main__':
    unittest.main()
