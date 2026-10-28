"""35차 문의함 중증도 정렬 방식: 실행기 동등성·중증만 우선 동작·선택/판정 규칙·seed 검사."""
import unittest

from inquiry_lab import (eat_capacity_policy as ek, eat_priority_policy as ep, experiment_priority as prio, experiment_triage as ex,
                         mass_scenarios as ms, priority_runner as pr, scenarios, triage_runner as tr)


class RunnerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ek.install([])
        ep.install(['eatkp_l0_m6'])
        cls.mass = prio.with_priority(ms.make_mass(ex.DEV_MASS[0]))

    @classmethod
    def tearDownClass(cls):
        ep.uninstall()
        ek.uninstall()

    def test_without_pf4_matches_round32_runner(self):
        arms = [ex.BASE_ARM, ex.CANDIDATES[0]]
        ours, theirs = tr.run_scenario(self.mass, arms), pr.run_scenario(self.mass, arms)
        for a, b in zip(ours['branches'], theirs['branches']):
            for key in ('patients', 'metrics', 'trace', 'backup', 'holds'):
                self.assertEqual(a[key], b[key], key)

    def test_high_first_differs_from_strict_and_from_spread(self):
        runs = {name: tr.run_scenario(self.mass, [pair])['branches'][0]['trace'] for name, pair in
                (('base', ex.BASE_ARM), ('pf', ex.CANDIDATES[0]), ('pf4', ex.CANDIDATES[1]))}
        self.assertNotEqual(runs['pf4'], runs['base'])
        self.assertNotEqual(runs['pf4'], runs['pf'])

    def test_names(self):
        self.assertTrue(tr.parse_rule(ex.CANDIDATES[1][1])['high_first'])
        with self.assertRaises(ValueError):
            tr.parse_rule(ex.CANDIDATES[0][1] + '+pf4')


class ProtocolTest(unittest.TestCase):
    def report(self, overall, high):
        def rows():
            out = {}
            for c in ex.CANDIDATES:
                v = {part: dict(delta_J_mean=d[c], ci95=[d[c] - 3, d[c] + 3])
                     for part, d in (('all', overall), ('high', high), ('mid', overall), ('low', overall))}
                out[ex.arm(*c)] = dict(versus_base=v)
            return out
        return dict(groups=dict(mix=rows(), mass=rows()))

    def test_selection_and_verdict(self):
        pf, pf4 = ex.CANDIDATES
        r = self.report({pf: 8.0, pf4: 1.0}, {pf: -40.0, pf4: -20.0})
        s = ex.select(r)
        self.assertEqual(tuple(s['arm']), pf4)              # pf는 전체 +8초라 제외
        self.assertTrue(ex.verdict(r, pf4)['adopted'])
        r = self.report({c: 0.0 for c in ex.CANDIDATES}, {c: 1.0 for c in ex.CANDIDATES})
        self.assertFalse(ex.select(r)['proceed'])

    def test_new_seeds_were_never_used(self):
        from inquiry_lab import (experiment_ack, experiment_aware, experiment_burden, experiment_calm, experiment_capacity,
                                 experiment_combo, experiment_confirm, experiment_coord, experiment_deadline, experiment_duty,
                                 experiment_event, experiment_fair, experiment_fastdecline, experiment_final, experiment_final2,
                                 experiment_hold, experiment_hold2, experiment_interrupt, experiment_levers, experiment_order,
                                 experiment_order2, experiment_priority2, experiment_public, experiment_reactive, experiment_seen,
                                 experiment_silent, experiment_status, experiment_surge, experiment_surge2, experiment_system,
                                 experiment_tune, experiment_voi, experiment_wide)
        used = (set(scenarios.DEVELOPMENT_MIX_SEEDS) | set(scenarios.VALIDATION_MIX_SEEDS)
                | set(scenarios.DEVELOPMENT_FAMILY_SEEDS) | set(scenarios.VALIDATION_FAMILY_SEEDS))
        mods = (experiment_confirm, experiment_public, experiment_ack, experiment_status, experiment_burden, experiment_coord,
                experiment_capacity, experiment_interrupt, experiment_order, experiment_order2, experiment_wide,
                experiment_aware, experiment_tune, experiment_seen, experiment_surge, experiment_fair, experiment_silent,
                experiment_event, experiment_hold, experiment_duty, experiment_hold2, experiment_deadline, experiment_reactive,
                experiment_surge2, experiment_calm, prio)
        for m in mods:
            used |= set(m.DEV_MIX) | set(m.VAL_MIX) | set(m.DEV_FAMILY) | set(m.VAL_FAMILY)
            used |= set(getattr(m, 'DEV_MASS', ())) | set(getattr(m, 'VAL_MASS', ()))
        for m in (experiment_final, experiment_combo, experiment_final2, experiment_fastdecline, experiment_system, experiment_priority2):
            used |= set(m.MIX) | set(m.MASS) | set(getattr(m, 'FAMILY', ()))
        used |= set(experiment_levers.LEVER_MIX) | set(experiment_levers.LEVER_MASS)
        used |= set(experiment_levers.STRESS_MIX) | set(experiment_levers.STRESS_MASS)
        used |= set(experiment_voi.MIX) | set(experiment_voi.FAMILY) | {999990001, 999990002}
        new = set(ex.DEV_MIX) | set(ex.VAL_MIX) | set(ex.DEV_MASS) | set(ex.VAL_MASS)
        self.assertFalse(new & used)
        for a, b in ((ex.DEV_MIX, ex.VAL_MIX), (ex.DEV_MASS, ex.VAL_MASS), (ex.DEV_MIX, ex.DEV_MASS), (ex.VAL_MIX, ex.VAL_MASS)):
            self.assertFalse(set(a) & set(b))
        self.assertFalse(any(lo <= s <= hi for s in new for lo, hi in scenarios.USED_SEED_RANGES))


if __name__ == '__main__':
    unittest.main()
