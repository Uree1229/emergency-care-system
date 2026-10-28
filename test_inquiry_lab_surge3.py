"""36차 선제 호출 6명 + 보조 복귀 60초 확인 실험: 구성·판정 규칙·seed 검사."""
import unittest

from inquiry_lab import experiment_surge2 as prev, experiment_surge3 as ex, scenarios


class DesignTest(unittest.TestCase):
    def test_rules(self):
        self.assertEqual(ex.RULES, (prev.BASE, 'od_k2_d60_l60+sg_n6_w120+' + prev.REST))

    def test_verdict(self):
        row = lambda d: dict(delta_J_mean=d, ci95=[d - 2, d + 2])

        def report(mix, mass, duty):
            group = lambda d: {ex.FOCUS: {ex.CANDIDATE: dict(versus_base=row(d))}}
            return dict(groups=dict(mix=group(mix), mass=group(mass)),
                        pooled_duty_difference={ex.CANDIDATE: dict(difference_per_patient=duty, ci95=[duty - 3, duty + 3])})
        self.assertTrue(ex.verdict(report(-3.0, 0.5, -25.0))['adopted'])
        self.assertFalse(ex.verdict(report(-1.0, 0.5, -25.0))['adopted'])      # 혼합 상한 +1
        self.assertFalse(ex.verdict(report(-3.0, 3.5, -25.0))['adopted'])      # 다수 상한 5.5
        self.assertFalse(ex.verdict(report(-3.0, 0.5, -1.0))['adopted'])       # 근무 상한 +2

    def test_new_seeds_were_never_used(self):
        from inquiry_lab import (experiment_ack, experiment_aware, experiment_burden, experiment_calm, experiment_capacity,
                                 experiment_combo, experiment_confirm, experiment_coord, experiment_deadline, experiment_duty,
                                 experiment_event, experiment_fair, experiment_fastdecline, experiment_final, experiment_final2,
                                 experiment_hold, experiment_hold2, experiment_interrupt, experiment_levers, experiment_order,
                                 experiment_order2, experiment_priority, experiment_priority2, experiment_public,
                                 experiment_reactive, experiment_seen, experiment_silent, experiment_status, experiment_surge,
                                 experiment_system, experiment_triage, experiment_tune, experiment_voi, experiment_wide)
        used = (set(scenarios.DEVELOPMENT_MIX_SEEDS) | set(scenarios.VALIDATION_MIX_SEEDS)
                | set(scenarios.DEVELOPMENT_FAMILY_SEEDS) | set(scenarios.VALIDATION_FAMILY_SEEDS))
        mods = (experiment_confirm, experiment_public, experiment_ack, experiment_status, experiment_burden, experiment_coord,
                experiment_capacity, experiment_interrupt, experiment_order, experiment_order2, experiment_wide,
                experiment_aware, experiment_tune, experiment_seen, experiment_surge, experiment_fair, experiment_silent,
                experiment_event, experiment_hold, experiment_duty, experiment_hold2, experiment_deadline, experiment_reactive,
                prev, experiment_calm, experiment_priority, experiment_triage)
        for m in mods:
            used |= set(m.DEV_MIX) | set(m.VAL_MIX) | set(m.DEV_FAMILY) | set(m.VAL_FAMILY)
            used |= set(getattr(m, 'DEV_MASS', ())) | set(getattr(m, 'VAL_MASS', ()))
        for m in (experiment_final, experiment_combo, experiment_final2, experiment_fastdecline, experiment_system, experiment_priority2):
            used |= set(m.MIX) | set(m.MASS) | set(getattr(m, 'FAMILY', ()))
        used |= set(experiment_levers.LEVER_MIX) | set(experiment_levers.LEVER_MASS)
        used |= set(experiment_levers.STRESS_MIX) | set(experiment_levers.STRESS_MASS)
        used |= set(experiment_voi.MIX) | set(experiment_voi.FAMILY) | {999990001, 999990002}
        new = set(ex.MIX) | set(ex.MASS) | set(ex.FAMILY)
        self.assertFalse(new & used)
        self.assertFalse(set(ex.MIX) & set(ex.MASS))
        self.assertFalse(any(lo <= s <= hi for s in new for lo, hi in scenarios.USED_SEED_RANGES))


if __name__ == '__main__':
    unittest.main()
