"""19차 응답 없이 닫힌 문의 조기 종료: 규칙 이름·18차 실행기와의 동등성·자동 종료 회계·선택/판정 규칙·seed 검사."""
import unittest

from inquiry_lab import (eat_capacity_policy as ek, eat_seen_policy as es, experiment_silent as ex, fair_runner as fr,
                         mass_scenarios as ms, scenarios, silent_runner as sr)


class RuleTest(unittest.TestCase):
    def test_names(self):
        self.assertEqual(sr.parse_rule(ex.CANDIDATES[1])['silent'], dict(grace=30))
        self.assertEqual(sr.parse_rule('base+nr0')['silent'], dict(grace=0))
        self.assertIsNone(sr.parse_rule(ex.PACKAGE)['silent'])
        with self.assertRaises(ValueError):
            sr.parse_rule('base+nr0+nr30')
        self.assertEqual(len(set(ex.RULES)), 6)


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

    def test_without_rule_matches_round18_runner(self):
        arms = [('fixed_3', 'base'), (ex.FOCUS, ex.PACKAGE)]
        for scenario in (self.burst, self.mass):
            ours, theirs = sr.run_scenario(scenario, arms), fr.run_scenario(scenario, arms)
            for a, b in zip(ours['branches'], theirs['branches']):
                for key in ('patients', 'metrics', 'trace', 'backup'):
                    self.assertEqual(a[key], b[key], key)
                self.assertEqual(a['silent_closed'], 0)

    def test_silent_reviews_are_closed_after_grace(self):
        branch = sr.run_scenario(self.mass, [(ex.FOCUS, ex.CANDIDATES[1])])['branches'][0]
        trace = branch['trace']
        omitted = {t['inquiry_id']: t['at'] for t in trace if t['type'] == 'reply_omitted'}
        closed = [t for t in trace if t['type'] == 'reply_closed_without_answer']
        self.assertEqual(len(closed), branch['silent_closed'])
        self.assertGreater(len(closed), 0)
        for t in closed:
            self.assertEqual(t['at'], omitted[t['inquiry_id']] + 30)        # 답 없이 끝난 뒤 정확히 30초 뒤 종료
        self.assertLessEqual(len(closed), len(omitted))                    # 그 사이 확정·취소된 문의는 종료하지 않는다


class ProtocolTest(unittest.TestCase):
    def report(self, mix, mass):
        def rows(deltas):
            return {c: dict(versus_package=dict(delta_J_mean=deltas[c], ci95=[deltas[c] - 3, deltas[c] + 3])) for c in ex.CANDIDATES}
        return dict(groups=dict(mix={ex.FOCUS: rows(mix)}, mass={ex.FOCUS: rows(mass)}))

    def test_selection_and_verdict(self):
        g0, g30, g60 = ex.CANDIDATES
        r = self.report({g0: -6.0, g30: -5.0, g60: -2.0}, {g0: 3.0, g30: -1.0, g60: -1.0})
        s = ex.select(r)
        self.assertEqual(s['rule'], g30)                    # g0은 다수 +3초라 제외, g30의 평균 −3.0이 최소
        self.assertTrue(s['proceed'])
        v = ex.verdict(r, g30)
        self.assertTrue(v['adopted'])                       # 혼합 −5 + 3 < 0, 다수 −1 + 3 < 5
        r = self.report({c: 0.5 for c in ex.CANDIDATES}, {c: 0.0 for c in ex.CANDIDATES})
        self.assertFalse(ex.select(r)['proceed'])

    def test_new_seeds_were_never_used(self):
        from inquiry_lab import (experiment_ack, experiment_aware, experiment_burden, experiment_capacity,
                                 experiment_confirm, experiment_coord, experiment_fair, experiment_final,
                                 experiment_interrupt, experiment_order, experiment_order2, experiment_public,
                                 experiment_seen, experiment_status, experiment_surge, experiment_tune, experiment_voi,
                                 experiment_wide)
        used = (set(scenarios.DEVELOPMENT_MIX_SEEDS) | set(scenarios.VALIDATION_MIX_SEEDS)
                | set(scenarios.DEVELOPMENT_FAMILY_SEEDS) | set(scenarios.VALIDATION_FAMILY_SEEDS))
        for m in (experiment_confirm, experiment_public, experiment_ack, experiment_status, experiment_burden,
                  experiment_coord, experiment_capacity, experiment_interrupt, experiment_order, experiment_order2,
                  experiment_wide, experiment_aware, experiment_tune, experiment_seen, experiment_surge, experiment_fair):
            used |= set(m.DEV_MIX) | set(m.VAL_MIX) | set(m.DEV_FAMILY) | set(m.VAL_FAMILY)
        for m in (experiment_wide, experiment_aware, experiment_tune, experiment_seen, experiment_surge, experiment_fair):
            used |= set(m.DEV_MASS) | set(m.VAL_MASS)
        used |= set(experiment_final.MIX) | set(experiment_final.MASS) | set(experiment_final.FAMILY)
        used |= set(experiment_voi.MIX) | set(experiment_voi.FAMILY) | {999990001, 999990002}
        new = set(ex.DEV_MIX) | set(ex.VAL_MIX) | set(ex.DEV_MASS) | set(ex.VAL_MASS) | set(ex.DEV_FAMILY) | set(ex.VAL_FAMILY)
        self.assertFalse(new & used)
        for a, b in ((ex.DEV_MIX, ex.VAL_MIX), (ex.DEV_MASS, ex.VAL_MASS), (ex.DEV_MIX, ex.DEV_MASS), (ex.VAL_MIX, ex.VAL_MASS)):
            self.assertFalse(set(a) & set(b))
        self.assertFalse(any(lo <= s <= hi for s in new for lo, hi in scenarios.USED_SEED_RANGES))


if __name__ == '__main__':
    unittest.main()
