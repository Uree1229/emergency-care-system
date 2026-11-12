"""18차 분산 정렬 공정성 보완: 규칙 이름·16차 실행기와의 동등성·대기 상한 동작·선택/판정 규칙·seed 검사."""
import unittest

from inquiry_lab import (eat_capacity_policy as ek, eat_seen_policy as es, experiment_fair as ex, fair_runner as fr,
                         mass_scenarios as ms, scenarios, surge_runner as su)


class RuleTest(unittest.TestCase):
    def test_names(self):
        self.assertEqual(fr.parse_rule(ex.CANDIDATES[1])['order'], dict(kind='fair', threshold=60, observed=100, comply=50))
        self.assertEqual(fr.parse_rule(ex.SPREAD)['order']['kind'], 'spread')
        self.assertIsNone(fr.parse_rule(ex.FIFO)['order'])
        for bad in ('ord_fair60_o0_c50', 'ord_fair60_o100', f'{ex.FIFO}+ord_fair60_o100_c50+ord_spread_o100_c50'):
            with self.assertRaises(ValueError):
                fr.parse_rule(bad)
        self.assertEqual(len(set(ex.RULES)), 5)


class RunnerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ek.install([])
        es.install([ex.FOCUS])
        cls.mass = ms.make_mass(ex.DEV_MASS[0])

    @classmethod
    def tearDownClass(cls):
        es.uninstall()
        ek.uninstall()

    def test_existing_rules_match_round16_runner(self):
        arms = [('fixed_3', ex.FIFO), (ex.FOCUS, ex.SPREAD)]
        ours, theirs = fr.run_scenario(self.mass, arms), su.run_scenario(self.mass, arms)
        for a, b in zip(ours['branches'], theirs['branches']):
            for key in ('patients', 'metrics', 'trace', 'backup', 'ordering'):
                self.assertEqual(a[key], b[key], key)

    def test_huge_threshold_equals_spread_and_zero_threshold_equals_fifo_order(self):
        never = f'{ex.STEM}+{fr.fair_name(10_000, 100, 50)}'
        always = f'{ex.STEM}+{fr.fair_name(0, 100, 100)}'
        out = fr.run_scenario(self.mass, [(ex.FOCUS, ex.SPREAD), (ex.FOCUS, never), (ex.FOCUS, ex.FIFO), (ex.FOCUS, always)])
        spread, never_aged, fifo, all_aged = out['branches']
        self.assertEqual(spread['patients'], never_aged['patients'])        # 아무도 상한에 걸리지 않으면 분산 정렬과 같다
        self.assertEqual(fifo['patients'], all_aged['patients'])            # 모두 걸리면 받은 순서와 같다
        self.assertEqual(all_aged['ordering']['reordered'], 0)


class ProtocolTest(unittest.TestCase):
    def report(self, deltas, new_fail, spread_fail):
        def rows():
            out = {ex.SPREAD: dict(harm=dict(new_failures=spread_fail))}
            for c in ex.CANDIDATES:
                out[c] = dict(harm=dict(new_failures=new_fail[c]),
                              versus_spread=dict(delta_J_mean=deltas[c], ci95=[deltas[c] - 3, deltas[c] + 3]))
            return out
        return dict(groups=dict(mix={ex.FOCUS: rows()}, mass={ex.FOCUS: rows()}),
                    new_failure_difference={c: dict(difference=-4, ci95=[-8, -1]) for c in ex.CANDIDATES})

    def test_selection_and_verdict(self):
        t30, t60, t120 = ex.CANDIDATES
        r = self.report({t30: 3.0, t60: 1.0, t120: 0.0}, {t30: 1, t60: 2, t120: 4}, 5)
        s = ex.select(r)
        self.assertEqual(s['rule'], t60)                    # t30은 J +3초라 제외, t60이 새 실패 최소
        self.assertTrue(s['proceed'])
        v = ex.verdict(r, t60)
        self.assertTrue(v['adopted'])
        r = self.report({t30: 0.0, t60: 0.0, t120: 0.0}, {t30: 5, t60: 6, t120: 7}, 5)
        self.assertFalse(ex.select(r)['proceed'])

    def test_new_seeds_were_never_used(self):
        from inquiry_lab import (experiment_ack, experiment_aware, experiment_burden, experiment_capacity,
                                 experiment_confirm, experiment_coord, experiment_final, experiment_interrupt,
                                 experiment_order, experiment_order2, experiment_public, experiment_seen,
                                 experiment_status, experiment_surge, experiment_tune, experiment_voi, experiment_wide)
        used = (set(scenarios.DEVELOPMENT_MIX_SEEDS) | set(scenarios.VALIDATION_MIX_SEEDS)
                | set(scenarios.DEVELOPMENT_FAMILY_SEEDS) | set(scenarios.VALIDATION_FAMILY_SEEDS))
        for m in (experiment_confirm, experiment_public, experiment_ack, experiment_status, experiment_burden,
                  experiment_coord, experiment_capacity, experiment_interrupt, experiment_order, experiment_order2,
                  experiment_wide, experiment_aware, experiment_tune, experiment_seen, experiment_surge):
            used |= set(m.DEV_MIX) | set(m.VAL_MIX) | set(m.DEV_FAMILY) | set(m.VAL_FAMILY)
        for m in (experiment_wide, experiment_aware, experiment_tune, experiment_seen, experiment_surge):
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
