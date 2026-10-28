"""23차 보조 근무 줄이기(22차 채택 구성 기준): 후보 구성·실행 단위·근무 차이 계산·선택/판정 규칙·seed 검사."""
import unittest

from inquiry_lab import eat_capacity_policy, eat_seen_policy, experiment_duty as ex, hold_runner, scenarios


class DesignTest(unittest.TestCase):
    def test_candidates_change_only_backup_part(self):
        final = hold_runner.parse_rule(ex.FINAL)
        for rule in ex.CANDIDATES:
            parsed = hold_runner.parse_rule(rule)
            self.assertEqual(parsed['capacity']['kind'], 'od')
            for key in ('interrupt', 'order', 'surge', 'silent', 'event', 'hold'):
                self.assertEqual(parsed[key], final[key], key)
            self.assertNotEqual(parsed['capacity'], final['capacity'])
        self.assertEqual(ex.short(ex.CANDIDATES[0]), 'od_k2_d60_l0')

    def test_one_case_and_duty_difference(self):
        seed = next(s for s in ex.DEV_MIX if scenarios.make_mix(s)['mix']['arrival'] == 'burst'
                    and scenarios.make_mix(s)['mix']['patients'] >= 6)
        try:
            out = ex.run_case(('mix', seed, [(ex.FOCUS, ex.FINAL), (ex.FOCUS, ex.CANDIDATES[0])]))
        finally:
            eat_seen_policy.uninstall()
            eat_capacity_policy.uninstall()
        a, b = ex.arm(ex.FOCUS, ex.CANDIDATES[0]), ex.arm(ex.FOCUS, ex.FINAL)
        diff = ex.duty_difference([out], a, b)
        expected = (out['branches'][a]['backup']['on_duty_seconds'] - out['branches'][b]['backup']['on_duty_seconds']) / out['patient_count']
        self.assertAlmostEqual(diff['difference_per_patient'], expected)
        self.assertLessEqual(out['branches'][a]['backup']['on_duty_seconds'], out['branches'][b]['backup']['on_duty_seconds'])  # L=0은 대기 없이 복귀

    def test_selection_and_verdict(self):
        c0, c60, k3l0, k3l120 = ex.CANDIDATES

        def report(deltas, reductions):
            def rows():
                return {c: dict(versus_final=dict(delta_J_mean=deltas[c], ci95=[deltas[c] - 3, deltas[c] + 3])) for c in ex.CANDIDATES}
            return dict(groups=dict(mix={ex.FOCUS: rows()}, mass={ex.FOCUS: rows()}),
                        pooled_duty_difference={c: dict(difference_per_patient=reductions[c], ci95=[reductions[c] - 10, reductions[c] + 10])
                                                for c in ex.CANDIDATES})
        r = report({c0: 1.0, c60: 0.5, k3l0: 3.0, k3l120: 0.0}, {c0: -80.0, c60: -40.0, k3l0: -120.0, k3l120: -20.0})
        s = ex.select(r)
        self.assertEqual(s['rule'], c0)                     # k3l0은 J +3초라 제외, c0이 가장 많이 줄인다
        self.assertTrue(s['proceed'])
        self.assertTrue(ex.verdict(r, c0)['adopted'])        # J 1 + 3 < 5, 근무 −80 + 10 < 0
        r = report({c: 0.0 for c in ex.CANDIDATES}, {c: 5.0 for c in ex.CANDIDATES})
        self.assertFalse(ex.select(r)['proceed'])

    def test_new_seeds_were_never_used(self):
        from inquiry_lab import (experiment_ack, experiment_aware, experiment_burden, experiment_capacity,
                                 experiment_combo, experiment_confirm, experiment_coord, experiment_event, experiment_fair,
                                 experiment_final, experiment_hold, experiment_interrupt, experiment_order, experiment_order2,
                                 experiment_public, experiment_seen, experiment_silent, experiment_status, experiment_surge,
                                 experiment_tune, experiment_voi, experiment_wide)
        used = (set(scenarios.DEVELOPMENT_MIX_SEEDS) | set(scenarios.VALIDATION_MIX_SEEDS)
                | set(scenarios.DEVELOPMENT_FAMILY_SEEDS) | set(scenarios.VALIDATION_FAMILY_SEEDS))
        mods = (experiment_confirm, experiment_public, experiment_ack, experiment_status, experiment_burden, experiment_coord,
                experiment_capacity, experiment_interrupt, experiment_order, experiment_order2, experiment_wide,
                experiment_aware, experiment_tune, experiment_seen, experiment_surge, experiment_fair, experiment_silent,
                experiment_event, experiment_hold)
        for m in mods:
            used |= set(m.DEV_MIX) | set(m.VAL_MIX) | set(m.DEV_FAMILY) | set(m.VAL_FAMILY)
            used |= set(getattr(m, 'DEV_MASS', ())) | set(getattr(m, 'VAL_MASS', ()))
        for m in (experiment_final, experiment_combo):
            used |= set(m.MIX) | set(m.MASS) | set(m.FAMILY)
        used |= set(experiment_voi.MIX) | set(experiment_voi.FAMILY) | {999990001, 999990002}
        new = set(ex.DEV_MIX) | set(ex.VAL_MIX) | set(ex.DEV_MASS) | set(ex.VAL_MASS) | set(ex.DEV_FAMILY) | set(ex.VAL_FAMILY)
        self.assertFalse(new & used)
        for a, b in ((ex.DEV_MIX, ex.VAL_MIX), (ex.DEV_MASS, ex.VAL_MASS), (ex.DEV_MIX, ex.DEV_MASS), (ex.VAL_MIX, ex.VAL_MASS)):
            self.assertFalse(set(a) & set(b))
        self.assertFalse(any(lo <= s <= hi for s in new for lo, hi in scenarios.USED_SEED_RANGES))


if __name__ == '__main__':
    unittest.main()
