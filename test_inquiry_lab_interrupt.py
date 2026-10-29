"""9차 취소 즉시 검토 중단: 규칙 이름·8차 실행기와의 동등성·중단 회계·진행/판정 규칙·seed 검사."""
import unittest

from inquiry_lab import capacity_runner as cr, experiment_interrupt as ei, harness, interrupt_runner as ir, scenarios


def _seed(arrival, minimum_patients=1):
    return next(s for s in ei.DEV_MIX if scenarios.make_mix(s)['mix']['arrival'] == arrival
                and scenarios.make_mix(s)['mix']['patients'] >= minimum_patients)


class RuleTest(unittest.TestCase):
    def test_names(self):
        self.assertEqual(ir.parse_rule('ci_r30_q50')['interrupt'], dict(notice=30, comply=50))
        self.assertEqual(ir.parse_rule('ci_r30_q50')['capacity'], dict(kind='base'))
        both = ir.parse_rule('od_k2_d60_l120+ci_r15_q100')
        self.assertEqual(both['capacity']['kind'], 'od')
        self.assertEqual(both['interrupt'], dict(notice=15, comply=100))
        self.assertIsNone(ir.parse_rule('min2')['interrupt'])
        for bad in ('ci_r30_q0', 'ci_r30', 'base+od_k2_d0_l0', 'ci_r1_q50+ci_r1_q50', 'ci_r30_q101', None):
            with self.assertRaises(ValueError):
                ir.parse_rule(bad)
        self.assertEqual(len(ei.GRID), 8)
        self.assertEqual(len(set(ei.DEV_RULES)), 12)
        self.assertTrue(set(ei.VAL_RULES) <= set(ei.DEV_RULES) | {'ci_r0_q100'})

    def test_compliance_is_deterministic_and_calibrated(self):
        self.assertEqual(ir.complies('s', 'P01', 'H01', 50), ir.complies('s', 'P01', 'H01', 50))
        self.assertTrue(all(ir.complies('s', f'P{i}', 'H01', 100) for i in range(200)))
        share = sum(ir.complies('s', f'P{i}', f'H{j}', 50) for i in range(50) for j in range(20)) / 1000
        self.assertGreater(share, 0.45)
        self.assertLess(share, 0.55)


class RunnerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        harness.install(['eat_l15_f'])
        cls.burst = scenarios.make_mix(_seed('burst', 5))
        cls.stream = scenarios.make_mix(_seed('stream', 5))

    @classmethod
    def tearDownClass(cls):
        harness.uninstall()

    def test_without_interrupt_matches_capacity_runner(self):
        arms = [('fixed_3', 'base'), ('eat_l15_f', 'base'), ('eat_l15_f', 'min2'), ('eat_l15_f', 'od_k2_d60_l120')]
        for scenario in (self.burst, self.stream):
            ours = ir.run_scenario(scenario, arms)
            theirs = cr.run_scenario(scenario, arms)
            for a, b in zip(ours['branches'], theirs['branches']):
                self.assertEqual(a['patients'], b['patients'])
                self.assertEqual(a['metrics'], b['metrics'])
                self.assertEqual(a['trace'], b['trace'])
                self.assertEqual(a['backup'], b['backup'])
                self.assertEqual(a['interrupt'], dict(count=0, saved_seconds=0, cancelled_in_review=0))

    def test_late_notice_never_interrupts(self):
        out = ir.run_scenario(self.burst, [('fixed_3', 'base'), ('fixed_3', 'ci_r999_q100')])
        self.assertEqual(out['branches'][0]['patients'], out['branches'][1]['patients'])
        self.assertEqual(out['branches'][1]['interrupt']['count'], 0)
        self.assertGreater(out['branches'][1]['interrupt']['cancelled_in_review'], 0)

    def test_instant_interrupt_accounting(self):
        for scenario in (self.burst, self.stream):
            branch = ir.run_scenario(scenario, [('fixed_3', 'ci_r0_q100')])['branches'][0]
            it, trace = branch['interrupt'], branch['trace']
            self.assertGreater(it['count'], 0)
            self.assertEqual(it['count'], it['cancelled_in_review'])
            stops = [t for t in trace if t['type'] == 'review_interrupted']
            self.assertEqual(len(stops), it['count'])
            self.assertEqual(sum(t['saved_seconds'] for t in stops), it['saved_seconds'])
            started = sum(t['service_seconds'] for t in trace if t['type'] == 'review_started')
            self.assertEqual(branch['metrics']['review_work_seconds'], started - it['saved_seconds'])
            self.assertEqual(branch['metrics']['reply_suppressed_count'], 0)
            stopped = {t['inquiry_id'] for t in stops}
            finished = {t['inquiry_id'] for t in trace if t['type'] == 'review_finished'}
            self.assertFalse(stopped & finished)

    def test_partial_compliance_and_backup_combination(self):
        branch = ir.run_scenario(self.burst, [('eat_l15_f', 'od_k2_d60_l120+ci_r30_q50')])['branches'][0]
        it = branch['interrupt']
        self.assertLessEqual(it['count'], it['cancelled_in_review'])
        stops = [t for t in branch['trace'] if t['type'] == 'review_interrupted']
        self.assertEqual(len(stops), it['count'])
        b = branch['backup']
        self.assertGreaterEqual(b['on_duty_seconds'], b['busy_seconds'])
        started = sum(t['service_seconds'] for t in branch['trace'] if t['type'] == 'review_started')
        self.assertEqual(branch['metrics']['review_work_seconds'], started - it['saved_seconds'])


class ProtocolTest(unittest.TestCase):
    def table(self, delta, ci):
        return {ei.VERDICT_RULE: dict(paired_vs_base=dict(delta_J_mean=delta, ci95=ci))}

    def test_go_and_verdict(self):
        self.assertTrue(ei.go(self.table(-3.0, [-9.0, 2.0]))['proceed'])
        self.assertFalse(ei.go(self.table(0.5, [-4.0, 5.0]))['proceed'])
        self.assertFalse(ei.verdict(self.table(-3.0, [-9.0, 2.0]))['effect_confirmed'])
        self.assertTrue(ei.verdict(self.table(-3.0, [-9.0, -0.1]))['effect_confirmed'])

    def test_new_seeds_were_never_used(self):
        from inquiry_lab import (experiment_ack, experiment_burden, experiment_capacity, experiment_confirm,
                                 experiment_coord, experiment_public, experiment_status, experiment_voi)
        used = (set(scenarios.DEVELOPMENT_MIX_SEEDS) | set(scenarios.VALIDATION_MIX_SEEDS)
                | set(scenarios.DEVELOPMENT_FAMILY_SEEDS) | set(scenarios.VALIDATION_FAMILY_SEEDS))
        for m in (experiment_confirm, experiment_public, experiment_ack, experiment_status, experiment_burden,
                  experiment_coord, experiment_capacity):
            used |= set(m.DEV_MIX) | set(m.VAL_MIX) | set(m.DEV_FAMILY) | set(m.VAL_FAMILY)
        used |= set(experiment_voi.MIX) | set(experiment_voi.FAMILY)
        new = set(ei.DEV_MIX) | set(ei.VAL_MIX) | set(ei.DEV_FAMILY) | set(ei.VAL_FAMILY)
        self.assertFalse(new & used)
        self.assertFalse(set(ei.DEV_MIX) & set(ei.VAL_MIX))
        self.assertFalse(any(lo <= s <= hi for s in new for lo, hi in scenarios.USED_SEED_RANGES))


if __name__ == '__main__':
    unittest.main()
