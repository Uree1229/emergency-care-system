"""37차 지역형 강건성 확인 실험: 지역형 변환·실행 단위·판정 규칙·seed 검사."""
import unittest

from inquiry_lab import (eat_capacity_policy, eat_seen_policy, experiment_hold2, experiment_region as ex, mass_scenarios as ms,
                         scenarios)


class DesignTest(unittest.TestCase):
    def test_regionalize(self):
        s = ms.make_mass(experiment_hold2.DEV_MASS[0])
        rural = ex.regionalize(s, keep=4, stretch=2.5, offset=300)
        self.assertEqual(len(rural['hospitals']), 4)
        ids = {h['id'] for h in rural['hospitals']}
        self.assertTrue(all(set(p['travel_seconds']) == ids for p in rural['patients']))
        self.assertTrue(all(q['hospital_id'] in ids for q in rural['potentials']))
        for before, after in zip(s['patients'], rural['patients']):
            near = min(before['travel_seconds'][h] for h in ids)
            self.assertEqual(min(after['travel_seconds'].values()), max(60, near + 300))
            gaps_before = sorted(before['travel_seconds'][h] - near for h in ids)
            gaps_after = sorted(t - min(after['travel_seconds'].values()) for t in after['travel_seconds'].values())
            self.assertTrue(all(abs(b * 2.5 - a) <= 1 for b, a in zip(gaps_before, gaps_after)))
        urban = ex.regionalize(s, stretch=0.5)
        self.assertEqual(len(urban['hospitals']), len(s['hospitals']))
        self.assertEqual(s, ms.make_mass(experiment_hold2.DEV_MASS[0]))       # 원본은 바꾸지 않는다

    def test_one_case(self):
        # 확인 실험 seed는 본 실행에만 쓴다. 실행 단위 점검은 이미 쓴 24차 개발 seed로 한다.
        try:
            out = ex.run_case(('rural', 'mix', experiment_hold2.DEV_MIX[3]))
        finally:
            eat_seen_policy.uninstall()
            eat_capacity_policy.uninstall()
        self.assertEqual(set(out['branches']), {'A', 'R', 'G'})
        self.assertLessEqual(out['hospitals'], 4)

    def test_verdict(self):
        row = lambda d: dict(delta_J_mean=d, ci95=[d - 3, d + 3])
        report = dict(regions={r: dict(groups=dict(mix=dict(G_minus_A=row(-30.0)), mass=dict(G_minus_A=row(-100.0)))) for r, _, _ in ex.REGIONS})
        self.assertTrue(ex.verdict(report)['robust'])
        report['regions']['rural']['groups']['mix']['G_minus_A'] = row(-1.0)
        self.assertFalse(ex.verdict(report)['robust'])

    def test_new_seeds_were_never_used(self):
        from inquiry_lab import (experiment_ack, experiment_aware, experiment_burden, experiment_calm, experiment_capacity,
                                 experiment_combo, experiment_confirm, experiment_coord, experiment_deadline, experiment_duty,
                                 experiment_event, experiment_fair, experiment_fastdecline, experiment_final, experiment_final2,
                                 experiment_hold, experiment_interrupt, experiment_levers, experiment_order, experiment_order2,
                                 experiment_priority, experiment_priority2, experiment_public, experiment_reactive, experiment_seen,
                                 experiment_silent, experiment_status, experiment_surge, experiment_surge2, experiment_surge3,
                                 experiment_system, experiment_triage, experiment_tune, experiment_voi, experiment_wide)
        used = (set(scenarios.DEVELOPMENT_MIX_SEEDS) | set(scenarios.VALIDATION_MIX_SEEDS)
                | set(scenarios.DEVELOPMENT_FAMILY_SEEDS) | set(scenarios.VALIDATION_FAMILY_SEEDS))
        mods = (experiment_confirm, experiment_public, experiment_ack, experiment_status, experiment_burden, experiment_coord,
                experiment_capacity, experiment_interrupt, experiment_order, experiment_order2, experiment_wide,
                experiment_aware, experiment_tune, experiment_seen, experiment_surge, experiment_fair, experiment_silent,
                experiment_event, experiment_hold, experiment_duty, experiment_hold2, experiment_deadline, experiment_reactive,
                experiment_surge2, experiment_calm, experiment_priority, experiment_triage)
        for m in mods:
            used |= set(m.DEV_MIX) | set(m.VAL_MIX) | set(m.DEV_FAMILY) | set(m.VAL_FAMILY)
            used |= set(getattr(m, 'DEV_MASS', ())) | set(getattr(m, 'VAL_MASS', ()))
        for m in (experiment_final, experiment_combo, experiment_final2, experiment_fastdecline, experiment_system,
                  experiment_priority2, experiment_surge3):
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
