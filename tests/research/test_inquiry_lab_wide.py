"""12차 다수 사상자 EAT-W: 입력 생성·EAT 동등성·이름·선택/판정 규칙·seed 검사."""
from copy import deepcopy
import unittest

from inquiry_lab import eat_policy, eat_wide_policy as ew, experiment_wide as ex, mass_scenarios as ms, scenarios
from server import inquiry_experiment


class MassTest(unittest.TestCase):
    def test_shape_and_determinism(self):
        for seed in ex.DEV_MASS[:6]:
            a, b = ms.make_mass(seed), ms.make_mass(seed)
            self.assertEqual(a, b)
            inquiry_experiment.validate(a)
            self.assertIn(len(a['patients']), ms.PATIENT_COUNTS)
            self.assertTrue(8 <= len(a['hospitals']) <= 12)
            times = [p['at'] for p in a['patients']]
            if a['mix']['arrival'] == 'simultaneous':
                self.assertEqual(set(times), {0})
            else:
                self.assertTrue(all(0 <= t <= ms.WAVE_SECONDS for t in times))
        with self.assertRaises(ValueError):
            ms.make_mass(1001)                     # 이미 본 seed 범위


class PolicyTest(unittest.TestCase):
    NOW = 4000

    def inputs(self, queue_others=()):
        cands = [dict(hospital_id=f'H{i:02d}', travel=dict(duration_seconds=300 + 40 * i), resources_enough=True)
                 for i in range(1, 11)]
        pending = [dict(hospital=h, patient=f'Q{k}', submitted_at=self.NOW - 5) for k, h in enumerate(queue_others)]
        return dict(now=self.NOW, candidates=cands, pending=pending, patient_pending=[], max_new=10, last_contact_at=None)

    def test_k6_m4_equals_locked_eat(self):
        for queue in ((), ('H01', 'H01', 'H02', 'H03', 'H04', 'H05', 'H06')):
            ours = ew.decide('eatw_k6_m4', **deepcopy(self.inputs(queue)))
            theirs = eat_policy.decide('eat_l15_f', **deepcopy(self.inputs(queue)))
            self.assertEqual(ours['send'], theirs['send'])
            a, b = dict(ours['evidence']), dict(theirs['evidence'])
            a.pop('policy'), b.pop('policy')
            self.assertEqual(a, b)
        self.assertIs(eat_policy.spec, ew.eat_policy.spec)      # 원래 명세 함수로 되돌린다

    def test_wider_pool_can_reach_far_idle_hospitals(self):
        busy = tuple(f'H{i:02d}' for i in range(1, 7) for _ in range(4))
        wide = ew.decide('eatw_k10_m4', **self.inputs(busy))
        narrow = ew.decide('eatw_k6_m4', **self.inputs(busy))
        self.assertEqual(len(wide['evidence']['candidate_plan']), 10)
        self.assertEqual(len(narrow['evidence']['candidate_plan']), 6)
        self.assertTrue(all(h <= 'H06' for h in narrow['send']))
        self.assertTrue(any(h > 'H06' for h in wide['send']))      # 붐비는 가까운 6곳 대신 한산한 먼 병원에 묻는다

    def test_names(self):
        self.assertEqual(ew.spec('eatw_k10_m6')['config']['max_candidates_evaluated'], 10)
        self.assertEqual(ew.spec('eatw_k10_m6')['config']['max_concurrent'], 6)
        for bad in ('eatw_k0_m4', 'eatw_k10', 'eat_l15_f', None):
            with self.assertRaises(ValueError):
                ew.spec(bad)


class ProtocolTest(unittest.TestCase):
    def report(self, mix, mass):
        def block(deltas):
            return dict(metrics={c: dict(J=900 + deltas[c]) for c in ex.CANDIDATES},
                        versus_eat={c: dict(delta_J_mean=deltas[c], ci95=[deltas[c] - 3, deltas[c] + 3]) for c in ex.CANDIDATES})
        return dict(groups=dict(mix=dict(base=block(mix)), mass=dict(base=block(mass))))

    def test_selection_and_verdict(self):
        k8, k10, k10m6 = ex.CANDIDATES
        r = self.report({k8: 0.5, k10: 1.5, k10m6: 4.0}, {k8: -5.0, k10: -9.0, k10m6: -20.0})
        s = ex.select(r)
        self.assertEqual(s['policy'], k10)          # k10m6은 혼합에서 +4초라 제외
        self.assertTrue(s['proceed'])
        v = ex.verdict(r, k10)
        self.assertTrue(v['mass_better'])
        self.assertTrue(v['mix_noninferior'])
        r = self.report({k8: 3.0, k10: 3.0, k10m6: 3.0}, {k8: -5.0, k10: -9.0, k10m6: -20.0})
        self.assertFalse(ex.select(r)['proceed'])

    def test_new_seeds_were_never_used(self):
        from inquiry_lab import (experiment_ack, experiment_burden, experiment_capacity, experiment_confirm,
                                 experiment_coord, experiment_interrupt, experiment_order, experiment_order2,
                                 experiment_public, experiment_status, experiment_voi)
        used = (set(scenarios.DEVELOPMENT_MIX_SEEDS) | set(scenarios.VALIDATION_MIX_SEEDS)
                | set(scenarios.DEVELOPMENT_FAMILY_SEEDS) | set(scenarios.VALIDATION_FAMILY_SEEDS))
        for m in (experiment_confirm, experiment_public, experiment_ack, experiment_status, experiment_burden,
                  experiment_coord, experiment_capacity, experiment_interrupt, experiment_order, experiment_order2):
            used |= set(m.DEV_MIX) | set(m.VAL_MIX) | set(m.DEV_FAMILY) | set(m.VAL_FAMILY)
        used |= set(experiment_voi.MIX) | set(experiment_voi.FAMILY)
        new = set(ex.DEV_MIX) | set(ex.VAL_MIX) | set(ex.DEV_MASS) | set(ex.VAL_MASS) | set(ex.DEV_FAMILY) | set(ex.VAL_FAMILY)
        self.assertFalse(new & used)
        for a, b in ((ex.DEV_MIX, ex.VAL_MIX), (ex.DEV_MASS, ex.VAL_MASS), (ex.DEV_MIX, ex.DEV_MASS), (ex.VAL_MIX, ex.VAL_MASS)):
            self.assertFalse(set(a) & set(b))
        self.assertFalse(any(lo <= s <= hi for s in new for lo, hi in scenarios.USED_SEED_RANGES))


if __name__ == '__main__':
    unittest.main()
