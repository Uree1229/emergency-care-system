"""14차 EAT-K 재조정: 기준과의 동등성·이름·선택/판정 규칙·seed 검사."""
from copy import deepcopy
import unittest

from inquiry_lab import (aware_runner as ar, eat_capacity_policy as ek, eat_tuned_policy as et, experiment_tune as tu,
                         scenarios)


class PolicyTest(unittest.TestCase):
    NOW = 4000

    def inputs(self):
        cands = [dict(hospital_id=f'H{i:02d}', travel=dict(duration_seconds=300 + 40 * i), resources_enough=True)
                 for i in range(1, 11)]
        busy = tuple(f'H{i:02d}' for i in range(1, 7) for _ in range(3))
        pending = [dict(hospital=h, patient=f'Q{k}', submitted_at=self.NOW - 5) for k, h in enumerate(busy)]
        return dict(now=self.NOW, candidates=cands, pending=pending, patient_pending=[], max_new=10, last_contact_at=None)

    def tearDown(self):
        ek.set_capacity(None)

    def test_l15_m4_equals_round13_reference(self):
        for table in (None, {f'H{i:02d}': dict(base=1, live=2 if i % 2 else 1) for i in range(1, 11)}):
            ek.set_capacity(table)
            a = et.decide('eatkt_l15_m4', **deepcopy(self.inputs()))
            b = ek.decide(tu.REFERENCE, **deepcopy(self.inputs()))
            ea, eb = dict(a['evidence']), dict(b['evidence'])
            ea.pop('policy'), eb.pop('policy')
            self.assertEqual((a['send'], ea), (b['send'], eb))
        self.assertIs(ek.spec, et.eat_capacity_policy.spec)

    def test_lambda_and_concurrency_are_applied(self):
        self.assertEqual(et.spec('eatkt_l0_m6')['config']['lambda_seconds'], 0)
        self.assertEqual(et.spec('eatkt_l0_m6')['config']['max_concurrent'], 6)
        self.assertEqual(et.spec('eatkt_l0_m6')['config']['capacity_mode'], 'live')
        self.assertLessEqual(len(et.decide('eatkt_l0_m6', **self.inputs())['send']), 6)
        for bad in ('eatkt_l0', 'eatkt_lx_m4', 'eatk_live_k10_m4', None):
            with self.assertRaises(ValueError):
                et.spec(bad)


class RunnerTest(unittest.TestCase):
    def test_reference_and_l15_m4_match_in_runner(self):
        ek.install([tu.REFERENCE])
        et.install(['eatkt_l15_m4'])
        try:
            seed = next(s for s in tu.DEV_MIX if scenarios.make_mix(s)['mix']['arrival'] == 'burst')
            out = ar.run_scenario(scenarios.make_mix(seed), [(tu.REFERENCE, tu.PACKAGE), ('eatkt_l15_m4', tu.PACKAGE)])
            a, b = out['branches']
            self.assertEqual(a['patients'], b['patients'])
            self.assertEqual(a['metrics'], b['metrics'])
        finally:
            et.uninstall()
            ek.uninstall()


class ProtocolTest(unittest.TestCase):
    def report(self, mix, mass_package):
        def block(deltas, Js):
            return dict(metrics={c: dict(J=Js[c]) for c in tu.CANDIDATES},
                        versus_reference={c: dict(delta_J_mean=deltas[c], ci95=[deltas[c] - 4, deltas[c] + 4]) for c in tu.CANDIDATES})
        zero = {c: 0.0 for c in tu.CANDIDATES}
        return dict(groups=dict(
            mix={'base': block(mix, zero), tu.PACKAGE: block(mix, zero)},
            mass={tu.PACKAGE: block({c: mass_package[c] - 1000 for c in tu.CANDIDATES}, mass_package),
                  'base': block(zero, {c: 1000.0 for c in tu.CANDIDATES})}))

    def test_selection_and_verdict(self):
        l0m4, l0m6, l15m6, l30m4 = tu.CANDIDATES
        r = self.report({l0m4: 0.5, l0m6: 3.0, l15m6: 0.0, l30m4: -1.0}, {l0m4: 985.0, l0m6: 970.0, l15m6: 995.0, l30m4: 1001.0})
        s = tu.select(r)
        self.assertEqual(s['policy'], l0m4)                 # l0m6은 혼합 +3초라 제외
        self.assertTrue(s['proceed'])
        v = tu.verdict(r, l0m4)
        self.assertTrue(v['mass_package_better'] and v['mix_noninferior'])
        r = self.report({c: 0.0 for c in tu.CANDIDATES}, {c: 1002.0 for c in tu.CANDIDATES})
        self.assertFalse(tu.select(r)['proceed'])

    def test_new_seeds_were_never_used(self):
        from inquiry_lab import (experiment_ack, experiment_aware, experiment_burden, experiment_capacity,
                                 experiment_confirm, experiment_coord, experiment_interrupt, experiment_order,
                                 experiment_order2, experiment_public, experiment_status, experiment_voi, experiment_wide)
        used = (set(scenarios.DEVELOPMENT_MIX_SEEDS) | set(scenarios.VALIDATION_MIX_SEEDS)
                | set(scenarios.DEVELOPMENT_FAMILY_SEEDS) | set(scenarios.VALIDATION_FAMILY_SEEDS))
        for m in (experiment_confirm, experiment_public, experiment_ack, experiment_status, experiment_burden,
                  experiment_coord, experiment_capacity, experiment_interrupt, experiment_order, experiment_order2,
                  experiment_wide, experiment_aware):
            used |= set(m.DEV_MIX) | set(m.VAL_MIX) | set(m.DEV_FAMILY) | set(m.VAL_FAMILY)
        for m in (experiment_wide, experiment_aware):
            used |= set(m.DEV_MASS) | set(m.VAL_MASS)
        used |= set(experiment_voi.MIX) | set(experiment_voi.FAMILY) | {999990001, 999990002}
        new = set(tu.DEV_MIX) | set(tu.VAL_MIX) | set(tu.DEV_MASS) | set(tu.VAL_MASS) | set(tu.DEV_FAMILY) | set(tu.VAL_FAMILY)
        self.assertFalse(new & used)
        for a, b in ((tu.DEV_MIX, tu.VAL_MIX), (tu.DEV_MASS, tu.VAL_MASS), (tu.DEV_MIX, tu.DEV_MASS), (tu.VAL_MIX, tu.VAL_MASS)):
            self.assertFalse(set(a) & set(b))
        self.assertFalse(any(lo <= s <= hi for s in new for lo, hi in scenarios.USED_SEED_RANGES))


if __name__ == '__main__':
    unittest.main()
