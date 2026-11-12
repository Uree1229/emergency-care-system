"""31차 빠른 거절 확인 실험: 시나리오 변형·실행 단위·판정 규칙·seed 검사."""
import unittest

from inquiry_lab import eat_capacity_policy, eat_seen_policy, experiment_fastdecline as ex, experiment_hold2, scenarios


class DesignTest(unittest.TestCase):
    def test_fast_declines_touch_only_declines(self):
        s = scenarios.make_mix(experiment_hold2.DEV_MIX[0])
        self.assertIs(ex.fast_declines(s, 0.0), s)
        fast = ex.fast_declines(s, 1.0)
        for a, b in zip(s['potentials'], fast['potentials']):
            if a['accept'] or a['no_response']:
                self.assertEqual(a, b)
            else:
                self.assertEqual(b['service_seconds'], min(a['service_seconds'], ex.FAST_SECONDS))
        half = ex.fast_declines(s, 0.5)
        self.assertEqual(half['potentials'], ex.fast_declines(s, 0.5)['potentials'])     # 해시로 고정
        changed = sum(a != b for a, b in zip(s['potentials'], half['potentials']))
        self.assertLessEqual(changed, sum(a != b for a, b in zip(s['potentials'], fast['potentials'])))

    def test_one_case(self):
        # 확인 실험 seed는 본 실행에만 쓴다. 실행 단위 점검은 이미 쓴 24차 개발 seed로 한다.
        try:
            out = ex.run_case(('mix', experiment_hold2.DEV_MIX[1]))
        finally:
            eat_seen_policy.uninstall()
            eat_capacity_policy.uninstall()
        self.assertEqual(set(out['branches']), {k for k, _, _, _ in ex.ARMS})

    def test_verdict(self):
        row = lambda d: dict(delta_J_mean=d, ci95=[d - 3, d + 3])
        report = lambda mix, mass: dict(groups=dict(mix=dict(comparisons=dict(G50_minus_G=row(mix))),
                                                    mass=dict(comparisons=dict(G50_minus_G=row(mass)))))
        self.assertTrue(ex.verdict(report(-10.0, -20.0))['confirmed'])
        self.assertFalse(ex.verdict(report(-2.0, -20.0))['confirmed'])

    def test_new_seeds_were_never_used(self):
        from inquiry_lab import (experiment_ack, experiment_aware, experiment_burden, experiment_calm, experiment_capacity,
                                 experiment_combo, experiment_confirm, experiment_coord, experiment_deadline, experiment_duty,
                                 experiment_event, experiment_fair, experiment_final, experiment_final2, experiment_hold,
                                 experiment_interrupt, experiment_levers, experiment_order, experiment_order2, experiment_public,
                                 experiment_reactive, experiment_seen, experiment_silent, experiment_status, experiment_surge,
                                 experiment_surge2, experiment_tune, experiment_voi, experiment_wide)
        used = (set(scenarios.DEVELOPMENT_MIX_SEEDS) | set(scenarios.VALIDATION_MIX_SEEDS)
                | set(scenarios.DEVELOPMENT_FAMILY_SEEDS) | set(scenarios.VALIDATION_FAMILY_SEEDS))
        mods = (experiment_confirm, experiment_public, experiment_ack, experiment_status, experiment_burden, experiment_coord,
                experiment_capacity, experiment_interrupt, experiment_order, experiment_order2, experiment_wide,
                experiment_aware, experiment_tune, experiment_seen, experiment_surge, experiment_fair, experiment_silent,
                experiment_event, experiment_hold, experiment_duty, experiment_hold2, experiment_deadline, experiment_reactive,
                experiment_surge2, experiment_calm)
        for m in mods:
            used |= set(m.DEV_MIX) | set(m.VAL_MIX) | set(m.DEV_FAMILY) | set(m.VAL_FAMILY)
            used |= set(getattr(m, 'DEV_MASS', ())) | set(getattr(m, 'VAL_MASS', ()))
        for m in (experiment_final, experiment_combo, experiment_final2):
            used |= set(m.MIX) | set(m.MASS) | set(m.FAMILY)
        used |= set(experiment_levers.LEVER_MIX) | set(experiment_levers.LEVER_MASS)
        used |= set(experiment_levers.STRESS_MIX) | set(experiment_levers.STRESS_MASS)
        used |= set(experiment_voi.MIX) | set(experiment_voi.FAMILY) | {999990001, 999990002}
        new = set(ex.MIX) | set(ex.MASS)
        self.assertFalse(new & used)
        self.assertFalse(set(ex.MIX) & set(ex.MASS))
        self.assertFalse(any(lo <= s <= hi for s in new for lo, hi in scenarios.USED_SEED_RANGES))


if __name__ == '__main__':
    unittest.main()
