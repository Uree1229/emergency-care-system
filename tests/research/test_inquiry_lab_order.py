"""10차 병원 문의함 정렬: 규칙 이름·9차 실행기와의 동등성·정렬 동작·선택 규칙·seed 검사."""
import unittest

from inquiry_lab import experiment_order as eo, harness, interrupt_runner as ir, order_runner as orr, scenarios


def _seed(arrival, minimum_patients=1):
    return next(s for s in eo.DEV_MIX if scenarios.make_mix(s)['mix']['arrival'] == arrival
                and scenarios.make_mix(s)['mix']['patients'] >= minimum_patients)


class RuleTest(unittest.TestCase):
    def test_names(self):
        r = orr.parse_rule('od_k2_d60_l120+ci_r30_q50+ord_spread_o100_c50')
        self.assertEqual(r['capacity']['kind'], 'od')
        self.assertEqual(r['interrupt'], dict(notice=30, comply=50))
        self.assertEqual(r['order'], dict(kind='spread', observed=100, comply=50))
        self.assertEqual(orr.parse_rule('ord_open_c50')['order'], dict(kind='open', observed=100, comply=50))
        self.assertEqual(orr.parse_rule('ci_r30_q50+ord_open_c100')['capacity'], dict(kind='base'))
        self.assertIsNone(orr.parse_rule('min2')['order'])
        for bad in ('ord_spread_o0_c50', 'ord_open_c0', 'ord_open_c50+ord_open_c50', 'base+min2',
                    'ord_spread_o100', 'ord_open_c50+od_k2_d60_l120', None, ''):
            with self.assertRaises(ValueError):
                orr.parse_rule(bad)
        self.assertEqual(len(set(eo.DEV_RULES)), 12)
        self.assertIn(eo.SPREAD, eo.DEV_RULES)
        self.assertIn(eo.OPEN, eo.DEV_RULES)

    def test_choose_prefers_fewest_then_earliest(self):
        self.assertEqual(orr.choose([1, 0, 0, 2]), 1)
        self.assertEqual(orr.choose([0, 0]), 0)
        self.assertEqual(orr.choose([3, 2, 1]), 2)

    def test_draws_are_deterministic_and_calibrated(self):
        self.assertEqual(orr.follows('s', 'H01', 3, 50), orr.follows('s', 'H01', 3, 50))
        self.assertTrue(all(orr.observed('s', f'P{i}', 'H01', 100) for i in range(100)))
        share = sum(orr.follows('s', f'H{j}', i, 50) for i in range(100) for j in range(10)) / 1000
        self.assertGreater(share, 0.45)
        self.assertLess(share, 0.55)


class RunnerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        harness.install(['eat_l15_f'])
        cls.burst = scenarios.make_mix(_seed('burst', 6))
        cls.stream = scenarios.make_mix(_seed('stream', 5))

    @classmethod
    def tearDownClass(cls):
        harness.uninstall()

    def test_without_order_matches_interrupt_runner(self):
        arms = [('fixed_3', 'base'), ('eat_l15_f', 'ci_r30_q50'), ('eat_l15_f', 'od_k2_d60_l120+ci_r30_q50')]
        for scenario in (self.burst, self.stream):
            ours = orr.run_scenario(scenario, arms)
            theirs = ir.run_scenario(scenario, arms)
            for a, b in zip(ours['branches'], theirs['branches']):
                for key in ('patients', 'metrics', 'trace', 'backup', 'interrupt', 'calls'):
                    self.assertEqual(a[key], b[key], key)
                self.assertEqual(a['ordering'], dict(picks=0, followed=0, reordered=0))

    def test_single_patient_is_unchanged(self):
        single = scenarios.make_mix(_seed('single'))
        out = orr.run_scenario(single, [('fixed_3', 'base'), ('fixed_3', 'ord_spread_o100_c100'), ('fixed_3', 'ord_open_c100')])
        self.assertEqual(out['branches'][0]['patients'], out['branches'][1]['patients'])
        self.assertEqual(out['branches'][0]['patients'], out['branches'][2]['patients'])
        self.assertEqual(out['branches'][1]['ordering']['reordered'], 0)

    def test_spread_reduces_duplicate_reviews_in_a_burst(self):
        out = orr.run_scenario(self.burst, [('fixed_3', 'base'), ('fixed_3', 'ord_spread_o100_c100')])
        fifo, spread = out['branches']
        self.assertGreater(spread['ordering']['reordered'], 0)
        self.assertEqual(spread['ordering']['picks'], spread['ordering']['followed'])
        self.assertLess(spread['overlap']['parallel_starts'], fifo['overlap']['parallel_starts'])
        # 모든 문의는 한 번만 검토를 시작한다.
        started = [t['inquiry_id'] for t in spread['trace'] if t['type'] == 'review_started']
        self.assertEqual(len(started), len(set(started)))

    def test_partial_follow_and_observation(self):
        branch = orr.run_scenario(self.burst, [('eat_l15_f', 'ord_spread_o50_c50')])['branches'][0]
        o = branch['ordering']
        self.assertLessEqual(o['followed'], o['picks'])
        self.assertLessEqual(o['reordered'], o['followed'])


class ProtocolTest(unittest.TestCase):
    def table(self, spread_J, open_J, delta, ci):
        rows = {}
        for rule, J in ((eo.SPREAD, spread_J), (eo.OPEN, open_J)):
            rows[rule] = dict(J=J, paired_vs_base=dict(delta_J_mean=delta, ci95=ci))
        return rows

    def test_selection(self):
        s = eo.select(self.table(900.0, 905.0, -3.0, [-6.0, 0.5]))
        self.assertEqual(s['rule'], eo.SPREAD)
        self.assertTrue(s['proceed'])
        s = eo.select(self.table(910.0, 905.0, 1.0, [-2.0, 4.0]))
        self.assertEqual(s['rule'], eo.OPEN)
        self.assertFalse(s['proceed'])
        rules = eo.validation_rules(eo.SPREAD)
        self.assertEqual(rules[3], 'ord_spread_o100_c100')
        self.assertIn(f'{eo.BACKUP_RULE}+{eo.SPREAD}', rules)
        self.assertEqual(eo.validation_rules(eo.OPEN)[3], 'ord_open_c100')
        self.assertTrue(eo.verdict({eo.SPREAD: dict(paired_vs_base=dict(delta_J_mean=-2, ci95=[-4, -0.5]))}, eo.SPREAD)['effect_confirmed'])

    def test_new_seeds_were_never_used(self):
        from inquiry_lab import (experiment_ack, experiment_burden, experiment_capacity, experiment_confirm,
                                 experiment_coord, experiment_interrupt, experiment_public, experiment_status,
                                 experiment_voi)
        used = (set(scenarios.DEVELOPMENT_MIX_SEEDS) | set(scenarios.VALIDATION_MIX_SEEDS)
                | set(scenarios.DEVELOPMENT_FAMILY_SEEDS) | set(scenarios.VALIDATION_FAMILY_SEEDS))
        for m in (experiment_confirm, experiment_public, experiment_ack, experiment_status, experiment_burden,
                  experiment_coord, experiment_capacity, experiment_interrupt):
            used |= set(m.DEV_MIX) | set(m.VAL_MIX) | set(m.DEV_FAMILY) | set(m.VAL_FAMILY)
        used |= set(experiment_voi.MIX) | set(experiment_voi.FAMILY)
        new = set(eo.DEV_MIX) | set(eo.VAL_MIX) | set(eo.DEV_FAMILY) | set(eo.VAL_FAMILY)
        self.assertFalse(new & used)
        self.assertFalse(set(eo.DEV_MIX) & set(eo.VAL_MIX))
        self.assertFalse(any(lo <= s <= hi for s in new for lo, hi in scenarios.USED_SEED_RANGES))


if __name__ == '__main__':
    unittest.main()
