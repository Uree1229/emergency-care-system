"""16차 다수 사상자 선제 호출: 규칙 이름·15차 실행기와의 동등성·호출 회계·선택/판정 규칙·seed 검사."""
import unittest

from inquiry_lab import (eat_capacity_policy as ek, experiment_surge as ex, mass_scenarios as ms, scenarios,
                         seen_runner as sr, surge_runner as su)


class RuleTest(unittest.TestCase):
    def test_names(self):
        r = su.parse_rule(ex.surge_package(6))
        self.assertEqual(r['surge'], dict(n=6, window=120))
        self.assertEqual(r['capacity']['kind'], 'od')
        self.assertIsNone(su.parse_rule(ex.PACKAGE)['surge'])
        for bad in ('sg_n6_w120', 'min2+sg_n6_w120', 'od_k2_d60_l120+sg_n0_w120', 'od_k2_d60_l120+sg_n6_w120+sg_n6_w120'):
            with self.assertRaises(ValueError):
                su.parse_rule(bad)


class RunnerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ek.install([ex.FOCUS])
        cls.mass = ms.make_mass(ex.DEV_MASS[0])
        seed = next(s for s in ex.DEV_MIX if scenarios.make_mix(s)['mix']['arrival'] == 'burst')
        cls.burst = scenarios.make_mix(seed)

    @classmethod
    def tearDownClass(cls):
        ek.uninstall()

    def test_without_surge_matches_round15_runner(self):
        arms = [('fixed_3', 'base'), (ex.FOCUS, ex.PACKAGE)]
        for scenario in (self.burst, self.mass):
            ours, theirs = su.run_scenario(scenario, arms), sr.run_scenario(scenario, arms)
            for a, b in zip(ours['branches'], theirs['branches']):
                for key in ('patients', 'metrics', 'trace', 'interrupt', 'ordering'):
                    self.assertEqual(a[key], b[key], key)
                extra = {k: v for k, v in a['backup'].items() if k not in ('surge_calls', 'surge_first_at')}
                self.assertEqual(extra, b['backup'])
                self.assertEqual(a['backup']['surge_calls'], 0)

    def test_surge_calls_every_idle_backup_once_triggered(self):
        branch = su.run_scenario(self.mass, [(ex.FOCUS, ex.surge_package(6))])['branches'][0]
        b = branch['backup']
        self.assertGreater(b['surge_calls'], 0)
        hospitals = {h['id'] for h in self.mass['hospitals']}
        surge_calls = [c for c in branch['calls'] if c.get('surge')]
        self.assertTrue({c['hospital_id'] for c in surge_calls} <= hospitals)
        two = {h['id'] for h in self.mass['hospitals'] if h['workers'] == 2}
        if two:
            self.assertTrue(any(c['hospital_id'] in two for c in surge_calls))     # 평소 2명 병원도 부른다
        for c in branch['calls']:
            self.assertEqual(c['arrive_at'], c['called_at'] + 60)
            self.assertGreaterEqual(c['released_at'], c['arrive_at'])
        first = b['surge_first_at']
        self.assertGreaterEqual(sum(p['at'] <= first for p in self.mass['patients']), 6)

    def test_small_burst_does_not_trigger_high_threshold(self):
        branch = su.run_scenario(self.burst, [(ex.FOCUS, ex.surge_package(10))])['branches'][0]
        self.assertEqual(branch['backup']['surge_calls'], 0)       # 보정 혼합은 최대 8명


class ProtocolTest(unittest.TestCase):
    def report(self, mix, mass, J):
        def rows(deltas, Js):
            out = {ex.PACKAGE: dict(metrics=dict(J=1000.0))}
            for c in ex.CANDIDATES:
                out[c] = dict(metrics=dict(J=Js[c]), versus_package=dict(delta_J_mean=deltas[c], ci95=[deltas[c] - 3, deltas[c] + 3]))
            return out
        zero = {c: 0.0 for c in ex.CANDIDATES}
        return dict(groups=dict(mix={ex.FOCUS: rows(mix, zero)}, mass={ex.FOCUS: rows(mass, J)}))

    def test_selection_and_verdict(self):
        n6, n10 = ex.CANDIDATES
        r = self.report({n6: 3.0, n10: 0.5}, {n6: -20.0, n10: -8.0}, {n6: 980.0, n10: 992.0})
        s = ex.select(r)
        self.assertEqual(s['rule'], n10)                    # n6은 평소 +3초라 제외
        self.assertTrue(s['proceed'])
        v = ex.verdict(r, n10)
        self.assertTrue(v['adopted'])
        r = self.report({n6: 0.0, n10: 0.0}, {n6: 1.0, n10: 2.0}, {n6: 1001.0, n10: 1002.0})
        self.assertFalse(ex.select(r)['proceed'])

    def test_new_seeds_were_never_used(self):
        from inquiry_lab import (experiment_ack, experiment_aware, experiment_burden, experiment_capacity,
                                 experiment_confirm, experiment_coord, experiment_interrupt, experiment_order,
                                 experiment_order2, experiment_public, experiment_seen, experiment_status,
                                 experiment_tune, experiment_voi, experiment_wide)
        used = (set(scenarios.DEVELOPMENT_MIX_SEEDS) | set(scenarios.VALIDATION_MIX_SEEDS)
                | set(scenarios.DEVELOPMENT_FAMILY_SEEDS) | set(scenarios.VALIDATION_FAMILY_SEEDS))
        for m in (experiment_confirm, experiment_public, experiment_ack, experiment_status, experiment_burden,
                  experiment_coord, experiment_capacity, experiment_interrupt, experiment_order, experiment_order2,
                  experiment_wide, experiment_aware, experiment_tune, experiment_seen):
            used |= set(m.DEV_MIX) | set(m.VAL_MIX) | set(m.DEV_FAMILY) | set(m.VAL_FAMILY)
        for m in (experiment_wide, experiment_aware, experiment_tune, experiment_seen):
            used |= set(m.DEV_MASS) | set(m.VAL_MASS)
        used |= set(experiment_voi.MIX) | set(experiment_voi.FAMILY) | {999990001, 999990002}
        new = set(ex.DEV_MIX) | set(ex.VAL_MIX) | set(ex.DEV_MASS) | set(ex.VAL_MASS) | set(ex.DEV_FAMILY) | set(ex.VAL_FAMILY)
        self.assertFalse(new & used)
        for a, b in ((ex.DEV_MIX, ex.VAL_MIX), (ex.DEV_MASS, ex.VAL_MASS), (ex.DEV_MIX, ex.DEV_MASS), (ex.VAL_MIX, ex.VAL_MASS)):
            self.assertFalse(set(a) & set(b))
        self.assertFalse(any(lo <= s <= hi for s in new for lo, hi in scenarios.USED_SEED_RANGES))


if __name__ == '__main__':
    unittest.main()
