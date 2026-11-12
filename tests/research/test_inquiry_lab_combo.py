"""21차 조합 확인 실험: 구성·실행 단위·판정·seed 검사."""
import unittest

from inquiry_lab import eat_capacity_policy, eat_seen_policy, event_runner, experiment_combo as ec, scenarios


class DesignTest(unittest.TestCase):
    def test_arms(self):
        self.assertEqual([a[0] for a in ec.ARMS], ['A', 'E', 'F', 'R'])
        combo = event_runner.parse_rule(ec.COMBO)
        self.assertEqual((combo['silent'], combo['event']), (dict(grace=0), True))
        package = event_runner.parse_rule(ec.PACKAGE)
        self.assertEqual((package['silent'], package['event']), (None, False))

    def test_one_case_runs_all_arms(self):
        seed = next(s for s in ec.MIX if scenarios.make_mix(s)['mix']['arrival'] == 'burst')
        try:
            out = ec.run_case(('mix', seed, [(p, r) for _, _, p, r in ec.ARMS]))
        finally:
            eat_seen_policy.uninstall()
            eat_capacity_policy.uninstall()
        self.assertEqual(set(out['branches']), {ec.arm(p, r) for _, _, p, r in ec.ARMS})

    def test_verdict(self):
        def block(delta):
            return dict(comparisons=dict(F_minus_E=dict(delta_J_mean=delta, ci95=[delta - 3, delta + 3])))
        self.assertTrue(ec.verdict(dict(groups=dict(mix=block(-5.0), mass=block(1.0))))['adopted'])
        self.assertFalse(ec.verdict(dict(groups=dict(mix=block(-2.0), mass=block(-20.0))))['adopted'])

    def test_new_seeds_were_never_used(self):
        from inquiry_lab import (experiment_ack, experiment_aware, experiment_burden, experiment_capacity,
                                 experiment_confirm, experiment_coord, experiment_event, experiment_fair, experiment_final,
                                 experiment_interrupt, experiment_order, experiment_order2, experiment_public,
                                 experiment_seen, experiment_silent, experiment_status, experiment_surge, experiment_tune,
                                 experiment_voi, experiment_wide)
        used = (set(scenarios.DEVELOPMENT_MIX_SEEDS) | set(scenarios.VALIDATION_MIX_SEEDS)
                | set(scenarios.DEVELOPMENT_FAMILY_SEEDS) | set(scenarios.VALIDATION_FAMILY_SEEDS))
        mods = (experiment_confirm, experiment_public, experiment_ack, experiment_status, experiment_burden, experiment_coord,
                experiment_capacity, experiment_interrupt, experiment_order, experiment_order2, experiment_wide,
                experiment_aware, experiment_tune, experiment_seen, experiment_surge, experiment_fair, experiment_silent,
                experiment_event)
        for m in mods:
            used |= set(m.DEV_MIX) | set(m.VAL_MIX) | set(m.DEV_FAMILY) | set(m.VAL_FAMILY)
            used |= set(getattr(m, 'DEV_MASS', ())) | set(getattr(m, 'VAL_MASS', ()))
        used |= set(experiment_final.MIX) | set(experiment_final.MASS) | set(experiment_final.FAMILY)
        used |= set(experiment_voi.MIX) | set(experiment_voi.FAMILY) | {999990001, 999990002}
        new = set(ec.MIX) | set(ec.MASS) | set(ec.FAMILY)
        self.assertFalse(new & used)
        self.assertFalse(set(ec.MIX) & set(ec.MASS))
        self.assertFalse(any(lo <= s <= hi for s in new for lo, hi in scenarios.USED_SEED_RANGES))


if __name__ == '__main__':
    unittest.main()
