"""33차 시스템 전체 권장안 확인 실험: 구성·실행 단위·판정 규칙·seed 검사."""
import unittest

from inquiry_lab import eat_capacity_policy, eat_seen_policy, experiment_hold2, experiment_system as ex, hold2_runner, scenarios


class DesignTest(unittest.TestCase):
    def test_arms(self):
        self.assertEqual([k for k, _, _, _, _ in ex.ARMS], ['A', 'G', 'H', 'RH'])
        g, h = hold2_runner.parse_rule(ex.G_RULE), hold2_runner.parse_rule(ex.H_RULE)
        self.assertEqual((h['capacity']['delay'], h['interrupt']), (30, dict(notice=0, comply=100)))
        for key in ('surge', 'order', 'silent', 'event', 'hold', 'hold_capacity'):
            self.assertEqual(g[key], h[key], key)
        self.assertEqual(len(ex.SOURCE_FILES), len(set(ex.SOURCE_FILES)))

    def test_one_case(self):
        # 확인 실험 seed는 본 실행에만 쓴다. 실행 단위 점검은 이미 쓴 24차 개발 seed로 한다.
        try:
            out = ex.run_case(('mix', experiment_hold2.DEV_MIX[2]))
        finally:
            eat_seen_policy.uninstall()
            eat_capacity_policy.uninstall()
        self.assertEqual(set(out['branches']), {'A', 'G', 'H', 'RH'})

    def test_verdict(self):
        row = lambda d: dict(delta_J_mean=d, ci95=[d - 3, d + 3])
        report = lambda mix, mass: dict(groups=dict(mix=dict(comparisons=dict(H_minus_G=row(mix))),
                                                    mass=dict(comparisons=dict(H_minus_G=row(mass)))))
        self.assertTrue(ex.verdict(report(-10.0, -30.0))['confirmed'])
        self.assertFalse(ex.verdict(report(-1.0, -30.0))['confirmed'])

    def test_new_seeds_were_never_used(self):
        from inquiry_lab import (experiment_ack, experiment_aware, experiment_burden, experiment_calm, experiment_capacity,
                                 experiment_combo, experiment_confirm, experiment_coord, experiment_deadline, experiment_duty,
                                 experiment_event, experiment_fair, experiment_fastdecline, experiment_final, experiment_final2,
                                 experiment_hold, experiment_interrupt, experiment_levers, experiment_order, experiment_order2,
                                 experiment_priority, experiment_public, experiment_reactive, experiment_seen, experiment_silent,
                                 experiment_status, experiment_surge, experiment_surge2, experiment_tune, experiment_voi,
                                 experiment_wide)
        used = (set(scenarios.DEVELOPMENT_MIX_SEEDS) | set(scenarios.VALIDATION_MIX_SEEDS)
                | set(scenarios.DEVELOPMENT_FAMILY_SEEDS) | set(scenarios.VALIDATION_FAMILY_SEEDS))
        mods = (experiment_confirm, experiment_public, experiment_ack, experiment_status, experiment_burden, experiment_coord,
                experiment_capacity, experiment_interrupt, experiment_order, experiment_order2, experiment_wide,
                experiment_aware, experiment_tune, experiment_seen, experiment_surge, experiment_fair, experiment_silent,
                experiment_event, experiment_hold, experiment_duty, experiment_hold2, experiment_deadline, experiment_reactive,
                experiment_surge2, experiment_calm, experiment_priority)
        for m in mods:
            used |= set(m.DEV_MIX) | set(m.VAL_MIX) | set(m.DEV_FAMILY) | set(m.VAL_FAMILY)
            used |= set(getattr(m, 'DEV_MASS', ())) | set(getattr(m, 'VAL_MASS', ()))
        for m in (experiment_final, experiment_combo, experiment_final2):
            used |= set(m.MIX) | set(m.MASS) | set(m.FAMILY)
        used |= set(experiment_levers.LEVER_MIX) | set(experiment_levers.LEVER_MASS)
        used |= set(experiment_levers.STRESS_MIX) | set(experiment_levers.STRESS_MASS)
        used |= set(experiment_fastdecline.MIX) | set(experiment_fastdecline.MASS)
        used |= set(experiment_voi.MIX) | set(experiment_voi.FAMILY) | {999990001, 999990002}
        new = set(ex.MIX) | set(ex.MASS)
        self.assertFalse(new & used)
        self.assertFalse(set(ex.MIX) & set(ex.MASS))
        self.assertFalse(any(lo <= s <= hi for s in new for lo, hi in scenarios.USED_SEED_RANGES))


if __name__ == '__main__':
    unittest.main()
