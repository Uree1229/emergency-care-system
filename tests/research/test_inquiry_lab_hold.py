"""22차 최종 구성의 확정 대기 규칙: 규칙 이름·20차 실행기와의 동등성·대기 동작·선택/판정 규칙·seed 검사."""
import unittest

from inquiry_lab import (eat_capacity_policy as ek, eat_seen_policy as es, event_runner as er, experiment_hold as ex,
                         hold_runner as hr, mass_scenarios as ms, scenarios)


class RuleTest(unittest.TestCase):
    def test_names(self):
        self.assertEqual(hr.parse_rule(ex.CANDIDATES[2])['hold'], 120)
        self.assertEqual(hr.parse_rule(ex.FINAL)['hold'], 0)
        for bad in ('cf60', 'base+cf0', 'base+cf60+cf60'):
            with self.assertRaises(ValueError):
                hr.parse_rule(bad)
        self.assertEqual(len(set(ex.RULES)), 4)


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

    def test_without_rule_matches_round20_runner(self):
        arms = [('fixed_3', 'base'), (ex.FOCUS, ex.FINAL)]
        for scenario in (self.burst, self.mass):
            ours, theirs = hr.run_scenario(scenario, arms), er.run_scenario(scenario, arms)
            for a, b in zip(ours['branches'], theirs['branches']):
                for key in ('patients', 'metrics', 'trace', 'backup', 'silent_closed', 'replans'):
                    self.assertEqual(a[key], b[key], key)
                self.assertEqual(a['holds'], dict(count=0, held_seconds=0, switched=0))

    def test_holds_are_bounded_and_logged(self):
        branch = hr.run_scenario(self.mass, [('fixed_3', f'{ex.FINAL}+cf30')])['branches'][0]
        started = [t for t in branch['trace'] if t['type'] == 'confirmation_hold_started']
        ended = [t for t in branch['trace'] if t['type'] == 'confirmation_hold_ended']
        self.assertEqual(len(ended), branch['holds']['count'])
        self.assertLessEqual(len(ended), len(started))
        self.assertTrue(all(t['held_seconds'] <= 30 for t in ended))
        for p in branch['patients']:                         # 대기해도 관측 300초 안에 확정하거나 실패한다
            if not p['failed']:
                self.assertLessEqual(p['selection_seconds'], 300)


class ProtocolTest(unittest.TestCase):
    def report(self, mix, mass):
        def rows(deltas):
            return {c: dict(versus_final=dict(delta_J_mean=deltas[c], ci95=[deltas[c] - 3, deltas[c] + 3])) for c in ex.CANDIDATES}
        return dict(groups=dict(mix={ex.FOCUS: rows(mix)}, mass={ex.FOCUS: rows(mass)}))

    def test_selection_and_verdict(self):
        c30, c60, c120 = ex.CANDIDATES
        r = self.report({c30: -4.0, c60: -5.0, c120: -6.0}, {c30: 0.0, c60: -1.0, c120: 3.0})
        s = ex.select(r)
        self.assertEqual(s['rule'], c60)                    # c120은 다수 +3초라 제외
        self.assertTrue(ex.verdict(r, c60)['adopted'])
        r = self.report({c: 1.0 for c in ex.CANDIDATES}, {c: 0.0 for c in ex.CANDIDATES})
        self.assertFalse(ex.select(r)['proceed'])

    def test_new_seeds_were_never_used(self):
        from inquiry_lab import (experiment_ack, experiment_aware, experiment_burden, experiment_capacity,
                                 experiment_combo, experiment_confirm, experiment_coord, experiment_event, experiment_fair,
                                 experiment_final, experiment_interrupt, experiment_order, experiment_order2,
                                 experiment_public, experiment_seen, experiment_silent, experiment_status, experiment_surge,
                                 experiment_tune, experiment_voi, experiment_wide)
        used = (set(scenarios.DEVELOPMENT_MIX_SEEDS) | set(scenarios.VALIDATION_MIX_SEEDS)
                | set(scenarios.DEVELOPMENT_FAMILY_SEEDS) | set(scenarios.VALIDATION_FAMILY_SEEDS))
        mods = (experiment_confirm, experiment_public, experiment_ack, experiment_status, experiment_burden, experiment_coord,
                experiment_capacity, experiment_interrupt, experiment_order, experiment_order2, experiment_wide,
                experiment_aware, experiment_tune, experiment_seen, experiment_surge, experiment_fair, experiment_silent,
                experiment_event)
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
