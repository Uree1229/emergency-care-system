"""13차 처리 용량을 아는 EAT: 정책 동등성·처리 인원 표·실행기 동등성·선택/판정 규칙·seed 검사."""
from copy import deepcopy
import unittest

from inquiry_lab import (aware_runner as ar, eat_capacity_policy as ek, eat_policy, eat_wide_policy as ew,
                         experiment_aware as ea, harness, order2_runner as o2, scenarios)


def _seed(arrival, minimum_patients=1):
    return next(s for s in ea.DEV_MIX if scenarios.make_mix(s)['mix']['arrival'] == arrival
                and scenarios.make_mix(s)['mix']['patients'] >= minimum_patients)


class PolicyTest(unittest.TestCase):
    NOW = 4000

    def inputs(self, queue=()):
        cands = [dict(hospital_id=f'H{i:02d}', travel=dict(duration_seconds=300 + 40 * i), resources_enough=True)
                 for i in range(1, 11)]
        pending = [dict(hospital=h, patient=f'Q{k}', submitted_at=self.NOW - 5) for k, h in enumerate(queue)]
        return dict(now=self.NOW, candidates=cands, pending=pending, patient_pending=[], max_new=10, last_contact_at=None)

    def tearDown(self):
        ek.set_capacity(None)

    def strip(self, out):
        e = dict(out['evidence'])
        e.pop('policy'), e.pop('capacity_used', None)
        return out['send'], e

    def test_without_table_matches_eat_and_eat_w(self):
        busy = tuple(f'H{i:02d}' for i in range(1, 7) for _ in range(3))
        for queue in ((), busy):
            self.assertEqual(self.strip(ek.decide('eatk_live_k6_m4', **deepcopy(self.inputs(queue)))),
                             self.strip(eat_policy.decide('eat_l15_f', **deepcopy(self.inputs(queue)))))
            self.assertEqual(self.strip(ek.decide('eatk_base_k10_m4', **deepcopy(self.inputs(queue)))),
                             self.strip(ew.decide('eatw_k10_m4', **deepcopy(self.inputs(queue)))))

    def test_table_is_used_by_mode(self):
        busy = tuple(f'H{i:02d}' for i in range(1, 7) for _ in range(4))
        ek.set_capacity({f'H{i:02d}': dict(base=1, live=3 if i <= 6 else 1) for i in range(1, 11)})
        live = ek.decide('eatk_live_k10_m4', **self.inputs(busy))
        base = ek.decide('eatk_base_k10_m4', **self.inputs(busy))
        self.assertEqual(live['evidence']['capacity_used']['H01'], 3)
        self.assertEqual(base['evidence']['capacity_used']['H01'], 1)
        # 지금 인원이 많으면 붐비는 가까운 병원의 대기를 짧게 보아 그쪽에 묻는다.
        self.assertTrue(any(h <= 'H06' for h in live['send']))
        self.assertTrue(all(h > 'H06' for h in base['send']))

    def test_names(self):
        self.assertEqual(ek.spec('eatk_live_k10_m4')['config']['capacity_mode'], 'live')
        for bad in ('eatk_now_k10_m4', 'eatk_live_k0_m4', 'eatk_live_k10', None):
            with self.assertRaises(ValueError):
                ek.spec(bad)


class RunnerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ew.install([ea.WIDE])
        ek.install(list(ea.CANDIDATES))
        cls.burst = scenarios.make_mix(_seed('burst', 6))

    @classmethod
    def tearDownClass(cls):
        ek.uninstall()
        ew.uninstall()

    def test_other_policies_match_round11_runner(self):
        arms = [('fixed_3', 'base'), ('eat_l15_f', ea.PACKAGE), (ea.WIDE, 'base'), (ea.WIDE, ea.PACKAGE)]
        ours, theirs = ar.run_scenario(self.burst, arms), o2.run_scenario(self.burst, arms)
        for a, b in zip(ours['branches'], theirs['branches']):
            for key in ('patients', 'metrics', 'trace', 'backup', 'interrupt', 'ordering'):
                self.assertEqual(a[key], b[key], key)
        self.assertIsNone(ek._CAPACITY)

    def test_live_table_counts_joined_backups(self):
        seen, original = [], ek.set_capacity

        def spy(table):
            if table is not None:
                seen.append(deepcopy(table))
            original(table)
        ek.set_capacity = spy
        try:
            out = ar.run_scenario(self.burst, [('eatk_live_k10_m4', 'base'), ('eatk_base_k10_m4', 'base'),
                                               ('eatk_live_k10_m4', 'od_k2_d0_l0')])
        finally:
            ek.set_capacity = original
        self.assertEqual(out['branches'][0]['patients'], out['branches'][1]['patients'])   # 보조 없으면 두 모드가 같다
        self.assertTrue(any(v['live'] > v['base'] for t in seen for v in t.values()))
        self.assertIsNone(ek._CAPACITY)


class ProtocolTest(unittest.TestCase):
    def report(self, mix_deltas, mass_package, mass_base):
        def block(deltas, Js):
            return dict(metrics={c: dict(J=Js[c]) for c in ea.CANDIDATES},
                        versus_eat={c: dict(delta_J_mean=deltas[c], ci95=[deltas[c] - 4, deltas[c] + 4]) for c in ea.CANDIDATES})
        zero = {c: 0.0 for c in ea.CANDIDATES}
        return dict(groups=dict(
            mix={'base': block(mix_deltas, zero), ea.PACKAGE: block(mix_deltas, zero)},
            mass={ea.PACKAGE: block({c: mass_package[c] - 1000 for c in ea.CANDIDATES}, mass_package),
                  'base': block({c: mass_base[c] - 1000 for c in ea.CANDIDATES}, mass_base)}))

    def test_selection_and_verdict(self):
        b10, l10, l6 = ea.CANDIDATES
        r = self.report({b10: 0.0, l10: 0.5, l6: 3.0}, {b10: 995.0, l10: 990.0, l6: 980.0}, {b10: 1, l10: 1, l6: 1})
        s = ea.select(r)
        self.assertEqual(s['policy'], l10)                  # l6은 혼합 +3초라 제외
        self.assertTrue(s['proceed'])
        v = ea.verdict(r, l10)
        self.assertTrue(v['mass_package_better'])          # −10 ± 4
        self.assertTrue(v['mix_noninferior'])               # 0.5 + 4 < 5
        r2 = self.report({b10: 0.0, l10: 1.0, l6: 3.0}, {b10: 995.0, l10: 990.0, l6: 980.0}, {b10: 1, l10: 1, l6: 1})
        self.assertFalse(ea.verdict(r2, l10)['mix_noninferior'])   # 상한 5.0은 '< 5'를 넘지 못한다
        r = self.report({c: 0.0 for c in ea.CANDIDATES}, {c: 1005.0 for c in ea.CANDIDATES}, {c: 1 for c in ea.CANDIDATES})
        self.assertFalse(ea.select(r)['proceed'])

    def test_new_seeds_were_never_used(self):
        from inquiry_lab import (experiment_ack, experiment_burden, experiment_capacity, experiment_confirm,
                                 experiment_coord, experiment_interrupt, experiment_order, experiment_order2,
                                 experiment_public, experiment_status, experiment_voi, experiment_wide)
        used = (set(scenarios.DEVELOPMENT_MIX_SEEDS) | set(scenarios.VALIDATION_MIX_SEEDS)
                | set(scenarios.DEVELOPMENT_FAMILY_SEEDS) | set(scenarios.VALIDATION_FAMILY_SEEDS))
        for m in (experiment_confirm, experiment_public, experiment_ack, experiment_status, experiment_burden,
                  experiment_coord, experiment_capacity, experiment_interrupt, experiment_order, experiment_order2,
                  experiment_wide):
            used |= set(m.DEV_MIX) | set(m.VAL_MIX) | set(m.DEV_FAMILY) | set(m.VAL_FAMILY)
        used |= set(experiment_voi.MIX) | set(experiment_voi.FAMILY) | set(experiment_wide.DEV_MASS) | set(experiment_wide.VAL_MASS)
        used |= {999990001, 999990002}
        new = set(ea.DEV_MIX) | set(ea.VAL_MIX) | set(ea.DEV_MASS) | set(ea.VAL_MASS) | set(ea.DEV_FAMILY) | set(ea.VAL_FAMILY)
        self.assertFalse(new & used)
        for a, b in ((ea.DEV_MIX, ea.VAL_MIX), (ea.DEV_MASS, ea.VAL_MASS), (ea.DEV_MIX, ea.DEV_MASS), (ea.VAL_MIX, ea.VAL_MASS)):
            self.assertFalse(set(a) & set(b))
        self.assertFalse(any(lo <= s <= hi for s in new for lo, hi in scenarios.USED_SEED_RANGES))


if __name__ == '__main__':
    unittest.main()
