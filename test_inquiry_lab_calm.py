"""29차 부하 적응 EAT-K: 이름·15차 정책과의 동등성·한가할 때만 상한 변경·선택/판정 규칙·seed 검사."""
import copy
import unittest
from unittest import mock

from inquiry_lab import (eat_capacity_policy as ek, eat_load_policy as el, experiment_calm as ex, hold2_runner as h2,
                         mass_scenarios as ms, scenarios)


class NameTest(unittest.TestCase):
    def test_names_and_load(self):
        self.assertEqual(el.parse('eatkc_c2_m6'), (2, 6))
        for bad in ('eatkc_c0_m6', 'eatkc_c2_m0', 'eatkc_2_6', 'eatks_seenbase_k10_m4'):
            with self.assertRaises(ValueError):
                el.parse(bad)
        self.assertEqual(el.spec('eatkc_c3_m6')['config']['calm_max_concurrent'], 6)
        self.assertEqual(el.load([dict(patient='P1'), dict(patient='P2'), dict(patient='P1')], []), 3)   # 자기 문의 없음 → +1
        self.assertEqual(el.load([dict(patient='P1')], [dict(patient='P1')]), 1)
        self.assertEqual(len(set(ex.POLICIES)), 5)


class PolicyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ek.install([])
        el.install(list(ex.CANDIDATES) + ['eatkc_c2_m4'])
        seed = next(s for s in ex.DEV_MIX if scenarios.make_mix(s)['mix']['arrival'] == 'burst')
        cls.burst = scenarios.make_mix(seed)
        cls.mass = ms.make_mass(ex.DEV_MASS[0])
        # 한가한 순간이 잦은 순차 발생 장면(상한이 실제로 작동하는 곳)
        cls.streams = [scenarios.make_mix(s) for s in ex.DEV_MIX[:12] if scenarios.make_mix(s)['mix']['arrival'] == 'stream']

    @classmethod
    def tearDownClass(cls):
        el.uninstall()
        ek.uninstall()

    def test_cap_four_matches_round15_policy(self):
        for scenario in (self.burst, self.mass):
            ours = h2.run_scenario(scenario, [('eatkc_c2_m4', ex.RULE)])['branches'][0]
            theirs = h2.run_scenario(scenario, [(ex.FOCUS, ex.RULE)])['branches'][0]
            for key in ('patients', 'metrics', 'backup', 'holds'):
                self.assertEqual(ours[key], theirs[key], key)

    def test_cap_changes_only_when_calm(self):
        calls = []
        original = el.decide

        def record(name, **kw):
            calls.append(copy.deepcopy(kw))
            return original(name, **kw)
        with mock.patch.object(el, 'decide', side_effect=record):
            for scenario in [self.burst, self.mass] + self.streams:
                h2.run_scenario(scenario, [('eatkc_c2_m6', ex.RULE)])
        self.assertTrue(calls)
        busy = more = 0
        for kw in calls:
            n = el.load(kw.get('pending') or (), kw.get('patient_pending') or ())
            one = original('eatkc_c2_m4', **copy.deepcopy(kw))         # 상한 4곳 = 15차 정책
            two = original('eatkc_c2_m6', **copy.deepcopy(kw))
            if n > 2:
                busy += 1
                self.assertEqual(one['send'], two['send'])
            else:
                self.assertGreaterEqual(len(two['send']), len(one['send']))
                more += len(two['send']) > len(one['send'])
        self.assertGreater(busy, 0)
        self.assertGreater(more, 0)


class ProtocolTest(unittest.TestCase):
    def report(self, mix, mass):
        def rows(deltas):
            return {c: dict(versus_base=dict(delta_J_mean=deltas[c], ci95=[deltas[c] - 3, deltas[c] + 3])) for c in ex.CANDIDATES}
        return dict(groups=dict(mix=rows(mix), mass=rows(mass)))

    def test_selection_and_verdict(self):
        c1, c2, c3, c2m8 = ex.CANDIDATES
        r = self.report({c1: -2.0, c2: -6.0, c3: -7.0, c2m8: -8.0}, {c1: 0.0, c2: 0.5, c3: 3.0, c2m8: 1.0})
        s = ex.select(r)
        self.assertEqual(s['policy'], c2m8)                 # c3은 다수 +3초라 제외, c2m8의 평균 −3.5가 최소
        self.assertTrue(ex.verdict(r, c2m8)['adopted'])     # 혼합 −8 + 3 < 0, 다수 1 + 3 < 5
        r = self.report({x: 1.0 for x in ex.CANDIDATES}, {x: -0.5 for x in ex.CANDIDATES})
        self.assertFalse(ex.select(r)['proceed'])

    def test_new_seeds_were_never_used(self):
        from inquiry_lab import (experiment_ack, experiment_aware, experiment_burden, experiment_capacity,
                                 experiment_combo, experiment_confirm, experiment_coord, experiment_deadline, experiment_duty,
                                 experiment_event, experiment_fair, experiment_final, experiment_final2, experiment_hold,
                                 experiment_hold2, experiment_interrupt, experiment_order, experiment_order2, experiment_public,
                                 experiment_reactive, experiment_seen, experiment_silent, experiment_status, experiment_surge,
                                 experiment_surge2, experiment_tune, experiment_voi, experiment_wide)
        used = (set(scenarios.DEVELOPMENT_MIX_SEEDS) | set(scenarios.VALIDATION_MIX_SEEDS)
                | set(scenarios.DEVELOPMENT_FAMILY_SEEDS) | set(scenarios.VALIDATION_FAMILY_SEEDS))
        mods = (experiment_confirm, experiment_public, experiment_ack, experiment_status, experiment_burden, experiment_coord,
                experiment_capacity, experiment_interrupt, experiment_order, experiment_order2, experiment_wide,
                experiment_aware, experiment_tune, experiment_seen, experiment_surge, experiment_fair, experiment_silent,
                experiment_event, experiment_hold, experiment_duty, experiment_hold2, experiment_deadline, experiment_reactive,
                experiment_surge2)
        for m in mods:
            used |= set(m.DEV_MIX) | set(m.VAL_MIX) | set(m.DEV_FAMILY) | set(m.VAL_FAMILY)
            used |= set(getattr(m, 'DEV_MASS', ())) | set(getattr(m, 'VAL_MASS', ()))
        for m in (experiment_final, experiment_combo, experiment_final2):
            used |= set(m.MIX) | set(m.MASS) | set(m.FAMILY)
        used |= set(experiment_voi.MIX) | set(experiment_voi.FAMILY) | {999990001, 999990002}
        new = set(ex.DEV_MIX) | set(ex.VAL_MIX) | set(ex.DEV_MASS) | set(ex.VAL_MASS) | set(ex.DEV_FAMILY) | set(ex.VAL_FAMILY)
        self.assertFalse(new & used)
        for a, b in ((ex.DEV_MIX, ex.VAL_MIX), (ex.DEV_MASS, ex.VAL_MASS), (ex.DEV_MIX, ex.DEV_MASS), (ex.VAL_MIX, ex.VAL_MASS)):
            self.assertFalse(set(a) & set(b))
        self.assertFalse(any(lo <= s <= hi for s in new for lo, hi in scenarios.USED_SEED_RANGES))


if __name__ == '__main__':
    unittest.main()
