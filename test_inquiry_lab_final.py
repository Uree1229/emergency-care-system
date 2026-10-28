"""17차 최종 통합 검증: 단계 구성·실행 단위·판정·seed 검사."""
import unittest

from inquiry_lab import eat_capacity_policy, eat_seen_policy, experiment_final as ef, scenarios, surge_runner


class DesignTest(unittest.TestCase):
    def test_steps_are_cumulative_and_valid(self):
        self.assertEqual([s[0] for s in ef.STEPS], ['A', 'B', 'C', 'D', 'E'])
        self.assertEqual(ef.STEPS[0][2:], ('fixed_3', 'base'))
        for _, _, policy, rule in ef.STEPS:
            surge_runner.parse_rule(rule)
            if policy.startswith(eat_seen_policy.PREFIX):
                eat_seen_policy.spec(policy)
        self.assertEqual(surge_runner.parse_rule(ef.STEPS[-1][3])['surge'], dict(n=10, window=120))
        self.assertEqual(len({(p, r) for _, _, p, r in ef.STEPS}), 5)

    def test_one_case_runs_all_steps(self):
        seed = next(s for s in ef.MIX if scenarios.make_mix(s)['mix']['arrival'] == 'burst')
        try:
            out = ef.run_case(('mix', seed, [(p, r) for _, _, p, r in ef.STEPS]))
        finally:
            # 같은 프로세스의 서버 정책 검사(test_inquiry_v4 등)를 위해 실험용 등록을 되돌린다.
            eat_seen_policy.uninstall()
            eat_capacity_policy.uninstall()
        self.assertEqual(set(out['branches']), {ef.arm(p, r) for _, _, p, r in ef.STEPS})

    def test_verdict(self):
        report = dict(groups={g: dict(steps=[dict(), dict(versus_current=dict(delta_J_mean=-60.0, ci95=[-70.0, -50.0]))])
                              for g in ('mix', 'mass')})
        v = ef.verdict(report)
        self.assertTrue(v['mix']['confirmed'] and v['mass']['confirmed'])

    def test_new_seeds_were_never_used(self):
        from inquiry_lab import (experiment_ack, experiment_aware, experiment_burden, experiment_capacity,
                                 experiment_confirm, experiment_coord, experiment_interrupt, experiment_order,
                                 experiment_order2, experiment_public, experiment_seen, experiment_status,
                                 experiment_surge, experiment_tune, experiment_voi, experiment_wide)
        used = (set(scenarios.DEVELOPMENT_MIX_SEEDS) | set(scenarios.VALIDATION_MIX_SEEDS)
                | set(scenarios.DEVELOPMENT_FAMILY_SEEDS) | set(scenarios.VALIDATION_FAMILY_SEEDS))
        for m in (experiment_confirm, experiment_public, experiment_ack, experiment_status, experiment_burden,
                  experiment_coord, experiment_capacity, experiment_interrupt, experiment_order, experiment_order2,
                  experiment_wide, experiment_aware, experiment_tune, experiment_seen, experiment_surge):
            used |= set(m.DEV_MIX) | set(m.VAL_MIX) | set(m.DEV_FAMILY) | set(m.VAL_FAMILY)
        for m in (experiment_wide, experiment_aware, experiment_tune, experiment_seen, experiment_surge):
            used |= set(m.DEV_MASS) | set(m.VAL_MASS)
        used |= set(experiment_voi.MIX) | set(experiment_voi.FAMILY) | {999990001, 999990002}
        new = set(ef.MIX) | set(ef.MASS) | set(ef.FAMILY)
        self.assertFalse(new & used)
        self.assertFalse(set(ef.MIX) & set(ef.MASS))
        self.assertFalse(any(lo <= s <= hi for s in new for lo, hi in scenarios.USED_SEED_RANGES))


if __name__ == '__main__':
    unittest.main()
