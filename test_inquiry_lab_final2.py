"""26차 최종 구성 확인 실험: 구성·실행 단위·판정 규칙·seed 검사."""
import unittest

from inquiry_lab import (eat_capacity_policy, eat_first_policy, eat_seen_policy, experiment_final2 as ex, experiment_hold2,
                         hold2_runner, scenarios)


class DesignTest(unittest.TestCase):
    def test_arms(self):
        self.assertEqual([k for k, _, _, _ in ex.ARMS], ['A', 'F', 'G', 'L', 'R'])
        for _, _, _, rule in ex.ARMS:
            hold2_runner.parse_rule(rule)
        g, light = hold2_runner.parse_rule(ex.G_RULE), hold2_runner.parse_rule(ex.LIGHT_RULE)
        self.assertEqual((g['capacity']['linger'], light['capacity']['linger']), (120, 60))
        for key in ('interrupt', 'order', 'surge', 'silent', 'event', 'hold', 'hold_capacity'):
            self.assertEqual(g[key], light[key], key)
        self.assertEqual(g['hold'], 120)

    def test_one_case(self):
        # 확인 실험 seed는 본 실행에만 쓴다. 구조 점검은 이미 쓴 24차 개발 seed로 한다.
        seed = next(s for s in experiment_hold2.DEV_MIX if scenarios.make_mix(s)['mix']['arrival'] == 'burst')
        try:
            out = ex.run_case(('mix', seed, [(p, r) for _, _, p, r in ex.ARMS]))
        finally:
            eat_first_policy.uninstall()
            eat_capacity_policy.uninstall()
        self.assertEqual(len(out['branches']), len(ex.ARMS))
        self.assertTrue(all('holds' in b and 'backup' in b for b in out['branches'].values()))

    def test_verdicts(self):
        row = lambda d, w=3: dict(delta_J_mean=d, ci95=[d - w, d + w])

        def report(mix, mass, light_mix=0.0, light_mass=0.0, duty=-40.0):
            return dict(groups=dict(mix=dict(comparisons=dict(G_minus_F=row(mix), L_minus_G=row(light_mix))),
                                    mass=dict(comparisons=dict(G_minus_F=row(mass), L_minus_G=row(light_mass)))),
                        L_minus_G_duty_pooled=dict(difference_per_patient=duty, ci95=[duty - 5, duty + 5]))
        self.assertTrue(ex.verdict(report(-4.0, 1.0))['confirmed'])      # −4 + 3 < 0, 1 + 3 < 5
        self.assertFalse(ex.verdict(report(-2.0, -9.0))['confirmed'])    # 혼합 상한 +1
        self.assertFalse(ex.verdict(report(-5.0, 2.5))['confirmed'])     # 다수 상한 5.5
        self.assertTrue(ex.light_verdict(report(0, 0, 1.0, 1.5))['confirmed'])
        self.assertFalse(ex.light_verdict(report(0, 0, 1.0, 2.5))['confirmed'])        # 다수 상한 5.5
        self.assertFalse(ex.light_verdict(report(0, 0, 0.0, 0.0, duty=-3.0))['confirmed'])  # 근무 상한 +2

    def test_new_seeds_were_never_used(self):
        from inquiry_lab import (experiment_ack, experiment_aware, experiment_burden, experiment_capacity,
                                 experiment_combo, experiment_confirm, experiment_coord, experiment_deadline, experiment_duty,
                                 experiment_event, experiment_fair, experiment_final, experiment_hold, experiment_interrupt,
                                 experiment_order, experiment_order2, experiment_public, experiment_seen, experiment_silent,
                                 experiment_status, experiment_surge, experiment_tune, experiment_voi, experiment_wide)
        used = (set(scenarios.DEVELOPMENT_MIX_SEEDS) | set(scenarios.VALIDATION_MIX_SEEDS)
                | set(scenarios.DEVELOPMENT_FAMILY_SEEDS) | set(scenarios.VALIDATION_FAMILY_SEEDS))
        mods = (experiment_confirm, experiment_public, experiment_ack, experiment_status, experiment_burden, experiment_coord,
                experiment_capacity, experiment_interrupt, experiment_order, experiment_order2, experiment_wide,
                experiment_aware, experiment_tune, experiment_seen, experiment_surge, experiment_fair, experiment_silent,
                experiment_event, experiment_hold, experiment_duty, experiment_hold2, experiment_deadline)
        for m in mods:
            used |= set(m.DEV_MIX) | set(m.VAL_MIX) | set(m.DEV_FAMILY) | set(m.VAL_FAMILY)
            used |= set(getattr(m, 'DEV_MASS', ())) | set(getattr(m, 'VAL_MASS', ()))
        for m in (experiment_final, experiment_combo):
            used |= set(m.MIX) | set(m.MASS) | set(m.FAMILY)
        used |= set(experiment_voi.MIX) | set(experiment_voi.FAMILY) | {999990001, 999990002}
        new = set(ex.MIX) | set(ex.MASS) | set(ex.FAMILY)
        self.assertFalse(new & used)
        self.assertFalse(set(ex.MIX) & set(ex.MASS))
        self.assertFalse(any(lo <= s <= hi for s in new for lo, hi in scenarios.USED_SEED_RANGES))


if __name__ == '__main__':
    unittest.main()
