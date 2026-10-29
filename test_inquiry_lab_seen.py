"""15차 열람 기록으로 처리 인원 추정: 정책 동등성·표 열·실행기 동등성·선택/판정 규칙·seed 검사."""
from copy import deepcopy
import unittest

from inquiry_lab import (aware_runner as ar, eat_capacity_policy as ek, eat_seen_policy as es, experiment_seen as ex,
                         scenarios, seen_runner as sr)


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

    def test_seen_columns_are_used(self):
        table = {f'H{i:02d}': dict(base=1, live=2, seen=3 if i == 1 else 1, seenbase=2) for i in range(1, 11)}
        ek.set_capacity(table)
        seen = es.decide('eatks_seen_k10_m4', **deepcopy(self.inputs()))
        both = es.decide('eatks_seenbase_k10_m4', **deepcopy(self.inputs()))
        self.assertEqual(seen['evidence']['capacity_used']['H01'], 3)
        self.assertEqual(seen['evidence']['capacity_used']['H02'], 1)
        self.assertEqual(both['evidence']['capacity_used']['H02'], 2)
        # seen 열이 live 열과 같으면 13차 기준과 결과가 같다.
        ek.set_capacity({h: dict(v, seen=v['live']) for h, v in table.items()})
        a, b = es.decide('eatks_seen_k10_m4', **deepcopy(self.inputs())), ek.decide(ex.REFERENCE, **deepcopy(self.inputs()))
        ea, eb = dict(a['evidence']), dict(b['evidence'])
        ea.pop('policy'), eb.pop('policy')
        self.assertEqual((a['send'], ea), (b['send'], eb))
        self.assertIs(ek.spec, es.eat_capacity_policy.spec)

    def test_names(self):
        self.assertEqual(es.spec('eatks_seenbase_k10_m4')['config']['capacity_mode'], 'seenbase')
        for bad in ('eatks_live_k10_m4', 'eatks_seen_k10', None):
            with self.assertRaises(ValueError):
                es.spec(bad)


class RunnerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ek.install([ex.REFERENCE])
        es.install(list(ex.CANDIDATES))
        seed = next(s for s in ex.DEV_MIX if scenarios.make_mix(s)['mix']['arrival'] == 'burst'
                    and scenarios.make_mix(s)['mix']['patients'] >= 6)
        cls.burst = scenarios.make_mix(seed)

    @classmethod
    def tearDownClass(cls):
        es.uninstall()
        ek.uninstall()

    def test_existing_columns_match_round13_runner(self):
        arms = [('fixed_3', 'base'), (ex.REFERENCE, 'base'), (ex.REFERENCE, ex.PACKAGE)]
        ours, theirs = sr.run_scenario(self.burst, arms), ar.run_scenario(self.burst, arms)
        for a, b in zip(ours['branches'], theirs['branches']):
            for key in ('patients', 'metrics', 'trace', 'backup'):
                self.assertEqual(a[key], b[key], key)
        self.assertIsNone(ek._CAPACITY)

    def test_seen_counts_reviews_in_progress(self):
        seen, original = [], ek.set_capacity

        def spy(table):
            if table is not None:
                seen.append(deepcopy(table))
            original(table)
        ek.set_capacity = spy
        try:
            sr.run_scenario(self.burst, [(ex.CANDIDATES[0], ex.PACKAGE)])
        finally:
            ek.set_capacity = original
        self.assertTrue(all(v['seen'] >= 1 and v['seenbase'] >= v['base'] for t in seen for v in t.values()))
        self.assertTrue(any(v['seen'] >= 2 for t in seen for v in t.values()))


class ProtocolTest(unittest.TestCase):
    def report(self, ref, eat):
        def block(rule_ref, rule_eat):
            return dict(versus_reference={c: dict(delta_J_mean=rule_ref[c], ci95=[rule_ref[c] - 3, rule_ref[c] + 3]) for c in ex.CANDIDATES},
                        versus_eat={c: dict(delta_J_mean=rule_eat[c], ci95=[rule_eat[c] - 3, rule_eat[c] + 3]) for c in ex.CANDIDATES})
        zero = {c: 0.0 for c in ex.CANDIDATES}
        return dict(groups=dict(mix={'base': block(ref, zero), ex.PACKAGE: block(ref, zero)},
                                mass={'base': block(ref, zero), ex.PACKAGE: block(ref, eat)}))

    def test_selection_prefers_seen(self):
        seen, both = ex.CANDIDATES
        r = self.report({seen: 1.0, both: 0.0}, {seen: -10.0, both: -12.0})
        s = ex.select(r)
        self.assertEqual(s['policy'], seen)
        v = ex.verdict(r, seen)
        self.assertTrue(v['adopted'])                       # 1 + 3 < 5, −10 + 3 < 0
        r = self.report({seen: 3.0, both: 1.0}, {seen: -10.0, both: -12.0})
        self.assertEqual(ex.select(r)['policy'], both)
        r = self.report({seen: 3.0, both: 3.0}, {seen: -10.0, both: -12.0})
        self.assertFalse(ex.select(r)['proceed'])

    def test_new_seeds_were_never_used(self):
        from inquiry_lab import (experiment_ack, experiment_aware, experiment_burden, experiment_capacity,
                                 experiment_confirm, experiment_coord, experiment_interrupt, experiment_order,
                                 experiment_order2, experiment_public, experiment_status, experiment_tune,
                                 experiment_voi, experiment_wide)
        used = (set(scenarios.DEVELOPMENT_MIX_SEEDS) | set(scenarios.VALIDATION_MIX_SEEDS)
                | set(scenarios.DEVELOPMENT_FAMILY_SEEDS) | set(scenarios.VALIDATION_FAMILY_SEEDS))
        for m in (experiment_confirm, experiment_public, experiment_ack, experiment_status, experiment_burden,
                  experiment_coord, experiment_capacity, experiment_interrupt, experiment_order, experiment_order2,
                  experiment_wide, experiment_aware, experiment_tune):
            used |= set(m.DEV_MIX) | set(m.VAL_MIX) | set(m.DEV_FAMILY) | set(m.VAL_FAMILY)
        for m in (experiment_wide, experiment_aware, experiment_tune):
            used |= set(m.DEV_MASS) | set(m.VAL_MASS)
        used |= set(experiment_voi.MIX) | set(experiment_voi.FAMILY) | {999990001, 999990002}
        new = set(ex.DEV_MIX) | set(ex.VAL_MIX) | set(ex.DEV_MASS) | set(ex.VAL_MASS) | set(ex.DEV_FAMILY) | set(ex.VAL_FAMILY)
        self.assertFalse(new & used)
        for a, b in ((ex.DEV_MIX, ex.VAL_MIX), (ex.DEV_MASS, ex.VAL_MASS), (ex.DEV_MIX, ex.DEV_MASS), (ex.VAL_MIX, ex.VAL_MASS)):
            self.assertFalse(set(a) & set(b))
        self.assertFalse(any(lo <= s <= hi for s in new for lo, hi in scenarios.USED_SEED_RANGES))


if __name__ == '__main__':
    unittest.main()
