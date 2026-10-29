"""11차 분산 정렬 개선·통합 패키지: 규칙 이름·10차 실행기와의 동등성·선택/판정 규칙·seed 검사."""
import unittest

from inquiry_lab import experiment_order2 as e2, harness, order2_runner as o2, order_runner as o1, scenarios


def _seed(arrival, minimum_patients=1):
    return next(s for s in e2.DEV_MIX if scenarios.make_mix(s)['mix']['arrival'] == arrival
                and scenarios.make_mix(s)['mix']['patients'] >= minimum_patients)


class RuleTest(unittest.TestCase):
    def test_names(self):
        self.assertEqual(o2.parse_rule('ord_age_o100_c50')['order'], dict(kind='age', observed=100, comply=50))
        self.assertEqual(o2.parse_rule('ord_risk_o50_c100')['order'], dict(kind='risk', observed=50, comply=100))
        full = o2.parse_rule(e2.package('ord_age_o100_c50'))
        self.assertEqual((full['capacity']['kind'], full['interrupt']['notice'], full['order']['kind']), ('od', 30, 'age'))
        for bad in ('ord_foo_o100_c50', 'ord_age_o100', 'ord_risk_o0_c50', 'ord_age_o100_c50+ord_risk_o100_c50'):
            with self.assertRaises(ValueError):
                o2.parse_rule(bad)
        self.assertEqual(o2.spread_name(100, 50, 'risk'), 'ord_risk_o100_c50')
        self.assertEqual(len(set(e2.DEV_RULES)), 10)

    def test_tuple_keys_break_ties_by_risk_then_arrival(self):
        self.assertEqual(o2.choose([(1, 5), (0, 9), (0, 3)]), 2)
        self.assertEqual(o2.choose([(0, -2), (0, -2), (1, -9)]), 0)


class RunnerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        harness.install(['eat_l15_f'])
        cls.burst = scenarios.make_mix(_seed('burst', 6))
        cls.stream = scenarios.make_mix(_seed('stream', 5))

    @classmethod
    def tearDownClass(cls):
        harness.uninstall()

    def test_existing_rules_match_round10_runner(self):
        arms = [('fixed_3', 'base'), ('eat_l15_f', 'ord_spread_o100_c50'), ('eat_l15_f', 'ord_open_c50'),
                ('eat_l15_f', 'od_k2_d60_l120+ci_r30_q50+ord_spread_o100_c50')]
        for scenario in (self.burst, self.stream):
            ours = o2.run_scenario(scenario, arms)
            theirs = o1.run_scenario(scenario, arms)
            for a, b in zip(ours['branches'], theirs['branches']):
                for key in ('patients', 'metrics', 'trace', 'backup', 'interrupt', 'ordering', 'overlap'):
                    self.assertEqual(a[key], b[key], key)

    def test_new_orders_run_and_keep_invariants(self):
        for scenario in (self.burst, self.stream):
            out = o2.run_scenario(scenario, [('eat_l15_f', 'ord_age_o100_c100'), ('eat_l15_f', 'ord_risk_o100_c100'),
                                             ('fixed_3', e2.package('ord_risk_o100_c50'))])
            for branch in out['branches']:
                started = [t['inquiry_id'] for t in branch['trace'] if t['type'] == 'review_started']
                self.assertEqual(len(started), len(set(started)))
                o = branch['ordering']
                self.assertLessEqual(o['reordered'], o['followed'])
                self.assertLessEqual(o['followed'], o['picks'])
            self.assertEqual(out['branches'][0]['ordering']['picks'], out['branches'][0]['ordering']['followed'])


class ProtocolTest(unittest.TestCase):
    def test_selection_and_validation_rules(self):
        table = {e2.CANDIDATES[0]: dict(J=900.0), e2.CANDIDATES[1]: dict(J=905.0), e2.REFERENCE_ORDER: dict(J=903.0)}
        versus = {e2.CANDIDATES[0]: dict(delta_J_mean=-3.0, ci95=[-6, 0.5]), e2.CANDIDATES[1]: dict(delta_J_mean=2.0, ci95=[-1, 5])}
        s = e2.select(table, versus)
        self.assertEqual(s['rule'], e2.CANDIDATES[0])
        self.assertTrue(s['proceed'])
        rules, chosen = e2.validation_rules(s)
        self.assertEqual(chosen, e2.CANDIDATES[0])
        self.assertEqual(rules, ('base', 'min2', e2.REFERENCE_ORDER, e2.CANDIDATES[0],
                                 f'{e2.INTERRUPT_RULE}+{e2.CANDIDATES[0]}', e2.package(e2.CANDIDATES[0])))
        versus[e2.CANDIDATES[0]] = dict(delta_J_mean=1.0, ci95=[-2, 4])
        s = e2.select(table, versus)
        self.assertFalse(s['proceed'])
        rules, chosen = e2.validation_rules(s)
        self.assertEqual(chosen, e2.REFERENCE_ORDER)
        self.assertNotIn(e2.CANDIDATES[0], rules)
        report = dict(versus_reference={}, versus_current={e2.package(chosen): dict(delta_J_mean=-60.0, ci95=[-70, -50])})
        v = e2.verdict(report, s, chosen)
        self.assertIsNone(v['improvement']['confirmed'])
        self.assertTrue(v['package']['confirmed'])

    def test_new_seeds_were_never_used(self):
        from inquiry_lab import (experiment_ack, experiment_burden, experiment_capacity, experiment_confirm,
                                 experiment_coord, experiment_interrupt, experiment_order, experiment_public,
                                 experiment_status, experiment_voi)
        used = (set(scenarios.DEVELOPMENT_MIX_SEEDS) | set(scenarios.VALIDATION_MIX_SEEDS)
                | set(scenarios.DEVELOPMENT_FAMILY_SEEDS) | set(scenarios.VALIDATION_FAMILY_SEEDS))
        for m in (experiment_confirm, experiment_public, experiment_ack, experiment_status, experiment_burden,
                  experiment_coord, experiment_capacity, experiment_interrupt, experiment_order):
            used |= set(m.DEV_MIX) | set(m.VAL_MIX) | set(m.DEV_FAMILY) | set(m.VAL_FAMILY)
        used |= set(experiment_voi.MIX) | set(experiment_voi.FAMILY)
        new = set(e2.DEV_MIX) | set(e2.VAL_MIX) | set(e2.DEV_FAMILY) | set(e2.VAL_FAMILY)
        self.assertFalse(new & used)
        self.assertFalse(set(e2.DEV_MIX) & set(e2.VAL_MIX))
        self.assertFalse(any(lo <= s <= hi for s in new for lo, hi in scenarios.USED_SEED_RANGES))


if __name__ == '__main__':
    unittest.main()
