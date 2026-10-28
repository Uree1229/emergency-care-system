"""20차 회신 즉시 재판단: 규칙 이름·19차 실행기와의 동등성·재판단 동작·진행/판정 규칙·seed 검사."""
import unittest

from inquiry_lab import (eat_capacity_policy as ek, eat_seen_policy as es, event_runner as er, experiment_event as ex,
                         mass_scenarios as ms, scenarios, silent_runner as sr)


class RuleTest(unittest.TestCase):
    def test_names(self):
        self.assertTrue(er.parse_rule(ex.CANDIDATE)['event'])
        self.assertTrue(er.parse_rule('base+ev')['event'])
        self.assertFalse(er.parse_rule(ex.PACKAGE)['event'])
        self.assertIsNone(er.parse_rule(ex.PACKAGE)['silent'])
        combo = er.parse_rule(ex.COMBO)
        self.assertEqual((combo['silent'], combo['event']), (dict(grace=0), True))
        self.assertEqual(set(ex.COMPARE.values()) | set(ex.COMPARE), set(ex.RULES))
        for bad in ('ev', 'base+ev+ev'):
            with self.assertRaises(ValueError):
                er.parse_rule(bad)


class RunnerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ek.install([])
        es.install([ex.FOCUS])
        cls.mass = ms.make_mass(ex.DEV_MASS[0])
        seed = next(s for s in ex.DEV_MIX if scenarios.make_mix(s)['mix']['arrival'] == 'burst')
        cls.burst = scenarios.make_mix(seed)

    @classmethod
    def tearDownClass(cls):
        es.uninstall()
        ek.uninstall()

    def test_without_rule_matches_round19_runner(self):
        arms = [('fixed_3', 'base'), (ex.FOCUS, ex.PACKAGE)]
        for scenario in (self.burst, self.mass):
            ours, theirs = er.run_scenario(scenario, arms), sr.run_scenario(scenario, arms)
            for a, b in zip(ours['branches'], theirs['branches']):
                for key in ('patients', 'metrics', 'trace', 'backup', 'silent_closed'):
                    self.assertEqual(a[key], b[key], key)
                self.assertEqual(a['replans'], 0)

    def test_declines_trigger_same_tick_decisions(self):
        branch = er.run_scenario(self.mass, [(ex.FOCUS, 'base+ev')])['branches'][0]
        self.assertGreater(branch['replans'], 0)
        declined = {(t['at'], t['patient_id']) for t in branch['trace'] if t['type'] == 'reply_decline'}
        decided = {(d['at'], d['patient_id']) for d in branch['decisions']}
        self.assertTrue(declined & decided)                # 거절이 온 그 시각에 같은 환자를 다시 판단한다


class ProtocolTest(unittest.TestCase):
    def report(self, mix, mass):
        def rows(delta):
            return {ex.CANDIDATE: dict(versus_without=dict(delta_J_mean=delta, ci95=[delta - 3, delta + 3]))}
        return dict(groups=dict(mix={ex.FOCUS: rows(mix)}, mass={ex.FOCUS: rows(mass)}))

    def test_go_and_verdict(self):
        self.assertTrue(ex.go(self.report(-4.0, 1.0))['proceed'])
        self.assertFalse(ex.go(self.report(-4.0, 3.0))['proceed'])
        self.assertFalse(ex.go(self.report(0.5, 0.5))['proceed'])
        self.assertTrue(ex.verdict(self.report(-4.0, 1.0))['adopted'])       # 혼합 −4 + 3 < 0, 다수 1 + 3 < 5
        self.assertFalse(ex.verdict(self.report(-2.0, 1.0))['adopted'])      # 둘 다 상한 ≥ 0

    def test_new_seeds_were_never_used(self):
        from inquiry_lab import (experiment_ack, experiment_aware, experiment_burden, experiment_capacity,
                                 experiment_confirm, experiment_coord, experiment_fair, experiment_final,
                                 experiment_interrupt, experiment_order, experiment_order2, experiment_public,
                                 experiment_seen, experiment_silent, experiment_status, experiment_surge, experiment_tune,
                                 experiment_voi, experiment_wide)
        used = (set(scenarios.DEVELOPMENT_MIX_SEEDS) | set(scenarios.VALIDATION_MIX_SEEDS)
                | set(scenarios.DEVELOPMENT_FAMILY_SEEDS) | set(scenarios.VALIDATION_FAMILY_SEEDS))
        mods = (experiment_confirm, experiment_public, experiment_ack, experiment_status, experiment_burden, experiment_coord,
                experiment_capacity, experiment_interrupt, experiment_order, experiment_order2, experiment_wide,
                experiment_aware, experiment_tune, experiment_seen, experiment_surge, experiment_fair, experiment_silent)
        for m in mods:
            used |= set(m.DEV_MIX) | set(m.VAL_MIX) | set(m.DEV_FAMILY) | set(m.VAL_FAMILY)
            used |= set(getattr(m, 'DEV_MASS', ())) | set(getattr(m, 'VAL_MASS', ()))
        used |= set(experiment_final.MIX) | set(experiment_final.MASS) | set(experiment_final.FAMILY)
        used |= set(experiment_voi.MIX) | set(experiment_voi.FAMILY) | {999990001, 999990002}
        new = set(ex.DEV_MIX) | set(ex.VAL_MIX) | set(ex.DEV_MASS) | set(ex.VAL_MASS) | set(ex.DEV_FAMILY) | set(ex.VAL_FAMILY)
        self.assertFalse(new & used)
        for a, b in ((ex.DEV_MIX, ex.VAL_MIX), (ex.DEV_MASS, ex.VAL_MASS), (ex.DEV_MIX, ex.DEV_MASS), (ex.VAL_MIX, ex.VAL_MASS)):
            self.assertFalse(set(a) & set(b))
        self.assertFalse(any(lo <= s <= hi for s in new for lo, hi in scenarios.USED_SEED_RANGES))


if __name__ == '__main__':
    unittest.main()
