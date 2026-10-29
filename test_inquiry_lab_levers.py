"""30차 운영 지렛대·강건성 확인 실험: 구성·환경 변경 복원·판정 규칙·seed 검사."""
import unittest

from inquiry_lab import (eat_capacity_policy, eat_seen_policy, experiment_hold2, experiment_levers as ex, hold2_runner,
                         scenarios)


class DesignTest(unittest.TestCase):
    def test_levers_change_one_thing_each(self):
        g = hold2_runner.parse_rule(ex.G_RULE)
        for key, _, rule, scale in ex.LEVERS:
            parsed = hold2_runner.parse_rule(rule)
            for part in ('surge', 'silent', 'event', 'hold', 'hold_capacity'):
                self.assertEqual(parsed[part], g[part], (key, part))
            self.assertIn(scale, (1.0, 0.8))
        self.assertEqual(ex.LEVERS[0][2], ex.G_RULE)

    def test_patch_restores_environment(self):
        before = (scenarios.REPLY_MEAN, scenarios.SERVICE_MEDIAN, dict(scenarios.ACCEPT_LEVELS))
        with ex.patched(dict(REPLY_MEAN=0.6, SERVICE_MEDIAN=110.0)):
            self.assertEqual(scenarios.REPLY_MEAN, 0.6)
        self.assertEqual((scenarios.REPLY_MEAN, scenarios.SERVICE_MEDIAN, dict(scenarios.ACCEPT_LEVELS)), before)
        with ex.patched(dict(SERVICE_MEDIAN=scenarios.SERVICE_MEDIAN * 0.8)):
            fast = scenarios.make_mix(experiment_hold2.DEV_MIX[0])
        slow = scenarios.make_mix(experiment_hold2.DEV_MIX[0])
        self.assertLess(sum(p['service_seconds'] for p in fast['potentials']), sum(p['service_seconds'] for p in slow['potentials']))

    def test_one_case_each_part(self):
        # 확인 실험 seed는 본 실행에만 쓴다. 실행 단위 점검은 이미 쓴 24차 개발 seed로 한다.
        try:
            lever = ex.run_case(('lever', None, 'mix', experiment_hold2.DEV_MIX[1]))
            stress = ex.run_case(('stress', 'low_reply', 'mix', experiment_hold2.DEV_MIX[1]))
        finally:
            eat_seen_policy.uninstall()
            eat_capacity_policy.uninstall()
        self.assertEqual(set(lever['branches']), {k for k, _, _, _ in ex.LEVERS})
        self.assertEqual(set(stress['branches']), {'A', 'R', 'G'})

    def test_verdict(self):
        row = lambda d: dict(delta_J_mean=d, ci95=[d - 3, d + 3])
        keys = [key for key, _, _, _ in ex.LEVERS if key != 'G']
        levers = dict(mix={key: dict(versus_G=row(-5.0)) for key in keys},
                      mass={key: dict(versus_G=row(-1.0 if key == 'order' else -5.0)) for key in keys})
        stress = {env: dict(groups=dict(mix=dict(G_minus_A=row(-50.0)), mass=dict(G_minus_A=row(-50.0)))) for env, _, _ in ex.ENVS}
        v = ex.verdict(dict(levers=levers, stress=stress))
        self.assertTrue(v['levers_confirmed']['review'])
        self.assertFalse(v['levers_confirmed']['order'])       # 다수 −1 + 3 > 0
        self.assertTrue(v['robust'])

    def test_new_seeds_were_never_used(self):
        from inquiry_lab import (experiment_ack, experiment_aware, experiment_burden, experiment_calm, experiment_capacity,
                                 experiment_combo, experiment_confirm, experiment_coord, experiment_deadline, experiment_duty,
                                 experiment_event, experiment_fair, experiment_final, experiment_final2, experiment_hold,
                                 experiment_interrupt, experiment_order, experiment_order2, experiment_public,
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
        used |= set(experiment_voi.MIX) | set(experiment_voi.FAMILY) | {999990001, 999990002}
        new = set(ex.LEVER_MIX) | set(ex.LEVER_MASS) | set(ex.STRESS_MIX) | set(ex.STRESS_MASS)
        self.assertFalse(new & used)
        for a, b in ((ex.LEVER_MIX, ex.LEVER_MASS), (ex.STRESS_MIX, ex.STRESS_MASS), (ex.LEVER_MIX, ex.STRESS_MIX),
                     (ex.LEVER_MASS, ex.STRESS_MASS)):
            self.assertFalse(set(a) & set(b))
        self.assertFalse(any(lo <= s <= hi for s in new for lo, hi in scenarios.USED_SEED_RANGES))


if __name__ == '__main__':
    unittest.main()
