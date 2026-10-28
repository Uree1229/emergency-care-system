"""28차 선제 호출 기준 낮추기: 후보 구성·선제 호출 작동 비율·선택/판정 규칙·seed 검사."""
import unittest

from inquiry_lab import eat_capacity_policy, eat_seen_policy, experiment_hold2, experiment_surge2 as ex, hold2_runner, scenarios


class DesignTest(unittest.TestCase):
    def test_candidates_change_only_surge_and_linger(self):
        base = hold2_runner.parse_rule(ex.BASE)
        self.assertEqual(base['surge'], dict(n=10, window=120))
        seen = set()
        for rule in ex.CANDIDATES:
            parsed = hold2_runner.parse_rule(rule)
            for key in ('interrupt', 'order', 'silent', 'event', 'hold', 'hold_capacity'):
                self.assertEqual(parsed[key], base[key], key)
            self.assertEqual((parsed['capacity']['k'], parsed['capacity']['delay']), (2, 60))
            seen.add((parsed['surge']['n'], parsed['capacity']['linger']))
        self.assertEqual(seen, {(6, 120), (6, 60), (8, 120), (8, 60)})
        self.assertEqual(ex.short(ex.BASE), '선제 10명·복귀 전 120초(BASE)')

    def test_one_case_and_surge_share(self):
        # 실행 단위 점검은 이미 쓴 24차 개발 seed로 한다.
        try:
            out = ex.run_case(('mass', experiment_hold2.DEV_MASS[3], [(ex.FOCUS, ex.BASE), (ex.FOCUS, ex.CANDIDATES[0])]))
        finally:
            eat_seen_policy.uninstall()
            eat_capacity_policy.uninstall()
        a, b = ex.arm(ex.FOCUS, ex.CANDIDATES[0]), ex.arm(ex.FOCUS, ex.BASE)
        self.assertEqual(ex.surge_share([out], a), 1.0)            # 다수 사상자에서는 6명 기준이 반드시 작동한다
        first = lambda name: out['branches'][name]['backup']['surge_first_at']
        self.assertLessEqual(first(a), first(b) if first(b) is not None else 10 ** 9)

    def test_selection_and_verdict(self):
        n6l120, n6l60, n8l120, n8l60 = ex.CANDIDATES

        def report(mix, mass, duty):
            def rows(deltas):
                return {c: dict(versus_base=dict(delta_J_mean=deltas[c], ci95=[deltas[c] - 3, deltas[c] + 3])) for c in ex.CANDIDATES}
            return dict(groups=dict(mix={ex.FOCUS: rows(mix)}, mass={ex.FOCUS: rows(mass)}), pooled_duty=duty,
                        pooled_duty_difference={c: dict(difference_per_patient=duty[c] - 220, ci95=[duty[c] - 225, duty[c] - 215])
                                                for c in ex.CANDIDATES})
        r = report({n6l120: -3.0, n6l60: -3.5, n8l120: -2.0, n8l60: 2.5}, {n6l120: -1.0, n6l60: -0.6, n8l120: 0.0, n8l60: -1.0},
                   {n6l120: 250, n6l60: 205, n8l120: 230, n8l60: 190})
        s = ex.select(r)
        self.assertEqual(s['rule'], n6l60)                  # n8l60은 혼합 +2.5초라 제외, n6l60은 최솟값 1초 이내 중 근무 최소
        self.assertTrue(ex.verdict(r, n6l60)['adopted'])        # 혼합 −3.5 + 3 < 0
        self.assertFalse(ex.verdict(r, n6l120)['adopted'])      # 혼합 −3.0 + 3 = 0은 0 미만이 아니다
        r = report({c: 0.5 for c in ex.CANDIDATES}, {c: 0.0 for c in ex.CANDIDATES}, {c: 220 for c in ex.CANDIDATES})
        self.assertFalse(ex.select(r)['proceed'])

    def test_new_seeds_were_never_used(self):
        from inquiry_lab import (experiment_ack, experiment_aware, experiment_burden, experiment_capacity,
                                 experiment_combo, experiment_confirm, experiment_coord, experiment_deadline, experiment_duty,
                                 experiment_event, experiment_fair, experiment_final, experiment_final2, experiment_hold,
                                 experiment_interrupt, experiment_order, experiment_order2, experiment_public,
                                 experiment_reactive, experiment_seen, experiment_silent, experiment_status, experiment_surge,
                                 experiment_tune, experiment_voi, experiment_wide)
        used = (set(scenarios.DEVELOPMENT_MIX_SEEDS) | set(scenarios.VALIDATION_MIX_SEEDS)
                | set(scenarios.DEVELOPMENT_FAMILY_SEEDS) | set(scenarios.VALIDATION_FAMILY_SEEDS))
        mods = (experiment_confirm, experiment_public, experiment_ack, experiment_status, experiment_burden, experiment_coord,
                experiment_capacity, experiment_interrupt, experiment_order, experiment_order2, experiment_wide,
                experiment_aware, experiment_tune, experiment_seen, experiment_surge, experiment_fair, experiment_silent,
                experiment_event, experiment_hold, experiment_duty, experiment_hold2, experiment_deadline, experiment_reactive)
        for m in mods:
            used |= set(m.DEV_MIX) | set(m.VAL_MIX) | set(m.DEV_FAMILY) | set(m.VAL_FAMILY)
            used |= set(getattr(m, 'DEV_MASS', ())) | set(getattr(m, 'VAL_MASS', ()))
        for m in (experiment_final, experiment_combo, experiment_final2):
            used |= set(m.MIX) | set(m.MASS) | set(m.FAMILY)
        used |= set(experiment_voi.MIX) | set(experiment_voi.FAMILY) | {999990001, 999990002}
        new = set(ex.DEV_MIX) | set(ex.VAL_MIX) | set(ex.DEV_MASS) | set(ex.VAL_MASS) | set(ex.DEV_FAMILY) | set(ex.VAL_FAMILY)
        self.assertFalse(new & used)
        for a, b in ((ex.DEV_MIX, ex.VAL_MIX), (ex.DEV_MASS, ex.VAL_MASS), (ex.DEV_MIX, ex.DEV_MASS), (ex.VAL_MIX, ex.VAL_MASS)):
            self.assertFalse(set(a) & set(b))
        self.assertFalse(any(lo <= s <= hi for s in new for lo, hi in scenarios.USED_SEED_RANGES))


if __name__ == '__main__':
    unittest.main()
