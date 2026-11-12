"""32차 중증도 관점: 실행기 동등성·중증도 등록·중증 우선 정렬·정책 동등성·선택/판정 규칙·seed 검사."""
import unittest

from inquiry_lab import (eat_capacity_policy as ek, eat_priority_policy as ep, eat_seen_policy as es, experiment_priority as ex,
                         hold2_runner as h2, mass_scenarios as ms, priority_runner as pr, scenarios)


class RunnerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ek.install([])
        ep.install(['eatkp_l0_m6'])
        cls.mass = ms.make_mass(ex.DEV_MASS[0])
        cls.mass_prio = ex.with_priority(cls.mass)

    @classmethod
    def tearDownClass(cls):
        ep.uninstall()
        ek.uninstall()

    def test_priority_assignment(self):
        levels = [p['priority'] for p in self.mass_prio['patients']]
        self.assertTrue(set(levels) <= {4, 2, 1})
        self.assertEqual(levels, [p['priority'] for p in ex.with_priority(self.mass)['patients']])   # 해시로 고정

    def test_without_priority_rules_matches_round24_runner(self):
        arms = [('fixed_3', 'base'), (ex.FOCUS, ex.G_RULE)]
        for scenario in (self.mass, self.mass_prio):     # 중증도를 등록해도 정렬·정책이 같으면 결과가 같다
            ours, theirs = pr.run_scenario(scenario, arms), h2.run_scenario(scenario, arms)
            for a, b in zip(ours['branches'], theirs['branches']):
                for key in ('patients', 'metrics', 'trace', 'backup', 'holds'):
                    self.assertEqual(a[key], b[key], key)

    def test_priority_first_changes_order_toward_high(self):
        base = pr.run_scenario(self.mass_prio, [(ex.FOCUS, ex.G_RULE)])['branches'][0]
        pf = pr.run_scenario(self.mass_prio, [(ex.FOCUS, ex.PF_RULE)])['branches'][0]
        self.assertNotEqual(base['trace'], pf['trace'])
        level = {p['id']: p['priority'] for p in self.mass_prio['patients']}
        first_review = lambda branch: {t['patient_id']: t['at'] for t in reversed(branch['trace']) if t['type'] == 'review_started'}
        mean = lambda d, lv: sum(v for k, v in d.items() if level[k] == lv) / max(1, sum(1 for k in d if level[k] == lv))
        self.assertLessEqual(mean(first_review(pf), 4), mean(first_review(base), 4))   # 중증 환자의 첫 검토가 빨라진다

    def test_policy_matches_round15_without_high_priority(self):
        medium = dict(self.mass, patients=[dict(p, priority=2) for p in self.mass['patients']])
        ours = pr.run_scenario(medium, [('eatkp_l0_m6', ex.G_RULE)])['branches'][0]
        theirs = pr.run_scenario(medium, [(ex.FOCUS, ex.G_RULE)])['branches'][0]
        for key in ('patients', 'metrics', 'backup', 'holds'):
            self.assertEqual(ours[key], theirs[key], key)


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
        pf, agg, both = ex.CANDIDATES
        r = self.report({pf: 0.5, agg: 1.0, both: 3.0}, {pf: -10.0, agg: -4.0, both: -20.0})
        s = ex.select(r)
        self.assertEqual(tuple(s['arm']), pf)                 # both는 전체 +3초라 제외
        v = ex.verdict(r, pf)
        self.assertTrue(v['high_better'] and v['overall_noninferior'] and v['adopted'])
        r = self.report({c: 0.0 for c in ex.CANDIDATES}, {c: 0.5 for c in ex.CANDIDATES})
        self.assertFalse(ex.select(r)['proceed'])

    def test_new_seeds_were_never_used(self):
        from inquiry_lab import (experiment_ack, experiment_aware, experiment_burden, experiment_calm, experiment_capacity,
                                 experiment_combo, experiment_confirm, experiment_coord, experiment_deadline, experiment_duty,
                                 experiment_event, experiment_fair, experiment_fastdecline, experiment_final, experiment_final2,
                                 experiment_hold, experiment_hold2, experiment_interrupt, experiment_levers, experiment_order,
                                 experiment_order2, experiment_public, experiment_reactive, experiment_seen, experiment_silent,
                                 experiment_status, experiment_surge, experiment_surge2, experiment_tune, experiment_voi,
                                 experiment_wide)
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
        used |= set(experiment_fastdecline.MIX) | set(experiment_fastdecline.MASS)
        used |= set(experiment_voi.MIX) | set(experiment_voi.FAMILY) | {999990001, 999990002}
        new = set(ex.DEV_MIX) | set(ex.VAL_MIX) | set(ex.DEV_MASS) | set(ex.VAL_MASS)
        self.assertFalse(new & used)
        for a, b in ((ex.DEV_MIX, ex.VAL_MIX), (ex.DEV_MASS, ex.VAL_MASS), (ex.DEV_MIX, ex.DEV_MASS), (ex.VAL_MIX, ex.VAL_MASS)):
            self.assertFalse(set(a) & set(b))
        self.assertFalse(any(lo <= s <= hi for s in new for lo, hi in scenarios.USED_SEED_RANGES))


if __name__ == '__main__':
    unittest.main()
