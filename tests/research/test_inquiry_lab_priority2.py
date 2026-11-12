"""34차 중증 환자 적극 문의 확인 실험: 구성·판정 규칙·보고서·seed 검사."""
import unittest

from inquiry_lab import experiment_priority as prev, experiment_priority2 as ex, scenarios


class DesignTest(unittest.TestCase):
    def test_arms(self):
        self.assertEqual(ex.ARMS, (prev.BASE_ARM, ('eatkp_l0_m6', prev.G_RULE)))
        self.assertIn(ex.CANDIDATE, prev.CANDIDATES)

    def report(self, mass_high, mass_all, mix_all):
        def parts(all_, high):
            row = lambda d: dict(delta_J_mean=d, ci95=[d - 3, d + 3])
            return dict(all=row(all_), high=row(high), mid=row(0.0), low=row(0.0))
        name = prev.arm(*ex.CANDIDATE)
        group = lambda all_, high: {name: dict(versus_base=parts(all_, high), label='후보', J=900.0, failed=1,
                                               J_by_level=dict(high=1.0, mid=1.0, low=1.0)),
                                    prev.arm(*ex.BASE_ARM): dict(label='기준', J=901.0, failed=1, J_by_level=dict(high=1.0, mid=1.0, low=1.0))}
        return dict(groups=dict(mass=group(mass_all, mass_high), mix=group(mix_all, 0.0)), generated_at='-', scenario_count=2,
                    elapsed_seconds=1.0)

    def test_verdict_and_markdown(self):
        r = self.report(-20.0, -2.0, 0.5)
        v = ex.verdict(r)
        self.assertTrue(v['adopted'])
        r['verdict'] = v
        self.assertIn('채택 후보 **True**', ex.markdown(r))
        self.assertFalse(ex.verdict(self.report(-2.0, -2.0, 0.5))['adopted'])     # 중증 상한 +1
        self.assertFalse(ex.verdict(self.report(-20.0, 2.5, 0.5))['adopted'])     # 다수 전체 상한 5.5
        self.assertFalse(ex.verdict(self.report(-20.0, -2.0, 2.5))['adopted'])    # 혼합 전체 상한 5.5

    def test_new_seeds_were_never_used(self):
        from inquiry_lab import (experiment_ack, experiment_aware, experiment_burden, experiment_calm, experiment_capacity,
                                 experiment_combo, experiment_confirm, experiment_coord, experiment_deadline, experiment_duty,
                                 experiment_event, experiment_fair, experiment_fastdecline, experiment_final, experiment_final2,
                                 experiment_hold, experiment_hold2, experiment_interrupt, experiment_levers, experiment_order,
                                 experiment_order2, experiment_public, experiment_reactive, experiment_seen, experiment_silent,
                                 experiment_status, experiment_surge, experiment_surge2, experiment_system, experiment_tune,
                                 experiment_voi, experiment_wide)
        used = (set(scenarios.DEVELOPMENT_MIX_SEEDS) | set(scenarios.VALIDATION_MIX_SEEDS)
                | set(scenarios.DEVELOPMENT_FAMILY_SEEDS) | set(scenarios.VALIDATION_FAMILY_SEEDS))
        mods = (experiment_confirm, experiment_public, experiment_ack, experiment_status, experiment_burden, experiment_coord,
                experiment_capacity, experiment_interrupt, experiment_order, experiment_order2, experiment_wide,
                experiment_aware, experiment_tune, experiment_seen, experiment_surge, experiment_fair, experiment_silent,
                experiment_event, experiment_hold, experiment_duty, experiment_hold2, experiment_deadline, experiment_reactive,
                experiment_surge2, experiment_calm, prev)
        for m in mods:
            used |= set(m.DEV_MIX) | set(m.VAL_MIX) | set(m.DEV_FAMILY) | set(m.VAL_FAMILY)
            used |= set(getattr(m, 'DEV_MASS', ())) | set(getattr(m, 'VAL_MASS', ()))
        for m in (experiment_final, experiment_combo, experiment_final2, experiment_fastdecline, experiment_system):
            used |= set(m.MIX) | set(m.MASS) | set(getattr(m, 'FAMILY', ()))
        used |= set(experiment_levers.LEVER_MIX) | set(experiment_levers.LEVER_MASS)
        used |= set(experiment_levers.STRESS_MIX) | set(experiment_levers.STRESS_MASS)
        used |= set(experiment_voi.MIX) | set(experiment_voi.FAMILY) | {999990001, 999990002}
        new = set(ex.MIX) | set(ex.MASS)
        self.assertFalse(new & used)
        self.assertFalse(set(ex.MIX) & set(ex.MASS))
        self.assertFalse(any(lo <= s <= hi for s in new for lo, hi in scenarios.USED_SEED_RANGES))


if __name__ == '__main__':
    unittest.main()
