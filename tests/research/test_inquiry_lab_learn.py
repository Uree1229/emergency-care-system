"""41차 스스로 학습하는 EAT-K: 숨은 값 복원·과거 기록·학습 표·정책 동등성·판정 규칙·seed 검사."""
import copy
import unittest

from inquiry_lab import (eat_capacity_policy as ek, eat_learn_policy as el, experiment_hold2, experiment_learn as ex, experiment_wide as wide,
                         hold2_runner as h2, learned_fit as lf, mass_scenarios as ms, predictable_scenarios as ps, scenarios)


class LearningTest(unittest.TestCase):
    def test_hidden_parameters_reproduce_potentials(self):
        for kind, seed in (('mix', experiment_hold2.DEV_MIX[7]), ('mass', experiment_hold2.DEV_MASS[11])):
            s = wide.build(kind, seed)
            params = lf.hidden(s)
            rng_of = ms._rng if kind == 'mass' else scenarios._rng
            for q in s['potentials']:
                r = rng_of(seed, 'potential', q['patient_id'], q['hospital_id'])
                r.gauss(0.0, scenarios.INQUIRY_SPREAD)
                self.assertEqual(r.random() >= params[q['hospital_id']]['reply'], q['no_response'])
                self.assertEqual(r.random() < params[q['hospital_id']]['accept'], q['accept'])

    def test_records_and_table(self):
        s = ps.predictable(scenarios.make_mix(experiment_hold2.DEV_MIX[7]))
        ids = [h['id'] for h in s['hospitals']]
        self.assertEqual(lf.past_records(s, 20), lf.past_records(s, 20))              # 해시로 고정
        self.assertEqual(len(lf.past_records(s, 20)), 20 * len(ids))
        params = lf.hidden(s)
        truth = {(t, h): lf.true_prob(params[h]['accept'], ps.fit(s['id'], h, t)) for t in ps.TYPES for h in ids}
        error = lambda n: sum(abs(lf.learned_table(lf.past_records(s, n), ids)[x] - truth[x]) for x in truth) / len(truth)
        self.assertLess(error(400), error(20))                                         # 기록이 많을수록 정확하다
        empty = lf.learned_table([], ids)
        self.assertTrue(all(abs(v - lf.PRIOR) < 1e-12 for v in empty.values()))         # 기록이 없으면 사전값


class PolicyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ek.install([])
        el.install()
        cls.mass = ps.predictable(ms.make_mass(experiment_hold2.DEV_MASS[12]))

    @classmethod
    def tearDownClass(cls):
        el.uninstall()
        ek.uninstall()

    def test_without_table_matches_round15_policy(self):
        el.set_knowledge(None)
        ours = h2.run_scenario(self.mass, [(el.NAME, ex.G_RULE)])['branches'][0]
        theirs = h2.run_scenario(self.mass, [(ex.FOCUS, ex.G_RULE)])['branches'][0]
        for key in ('patients', 'metrics', 'backup', 'holds'):
            self.assertEqual(ours[key], theirs[key], key)

    def test_table_changes_decisions(self):
        ids = [h['id'] for h in self.mass['hospitals']]
        table = lf.learned_table(lf.past_records(self.mass, 80), ids)
        el.set_knowledge(dict(types={p['id']: p['ptype'] for p in self.mass['patients']}, prob=table))
        try:
            ours = h2.run_scenario(self.mass, [(el.NAME, ex.G_RULE)])['branches'][0]
        finally:
            el.set_knowledge(None)
        theirs = h2.run_scenario(self.mass, [(ex.FOCUS, ex.G_RULE)])['branches'][0]
        self.assertNotEqual(ours['trace'], theirs['trace'])


class ProtocolTest(unittest.TestCase):
    def test_verdict(self):
        row = lambda d: dict(delta_J_mean=d, ci95=[d - 3, d + 3])

        def report(l80, l20):
            return dict(groups={k: dict(comparisons=dict(L80_minus_G=row(l80[i]), L20_minus_G=row(l20[i]))) for i, k in enumerate(('mix', 'mass'))})
        v = ex.verdict(report((-8.0, -30.0), (-2.0, -15.0)))
        self.assertTrue(v['confirmed'] and v['small_useful'])
        v = ex.verdict(report((-1.0, -30.0), (1.0, 3.0)))
        self.assertFalse(v['confirmed'] or v['small_useful'])

    def test_new_seeds_were_never_used(self):
        from inquiry_lab import (experiment_ack, experiment_aware, experiment_burden, experiment_calm, experiment_capacity,
                                 experiment_combo, experiment_confirm, experiment_coord, experiment_deadline, experiment_duty,
                                 experiment_event, experiment_fair, experiment_fastdecline, experiment_final, experiment_final2,
                                 experiment_hold, experiment_interrupt, experiment_levers, experiment_order, experiment_order2,
                                 experiment_package, experiment_predict, experiment_priority, experiment_priority2,
                                 experiment_public, experiment_reactive, experiment_region, experiment_seen, experiment_silent,
                                 experiment_sparse, experiment_status, experiment_surge, experiment_surge2, experiment_surge3,
                                 experiment_system, experiment_triage, experiment_tune, experiment_voi, experiment_wide)
        used = (set(scenarios.DEVELOPMENT_MIX_SEEDS) | set(scenarios.VALIDATION_MIX_SEEDS)
                | set(scenarios.DEVELOPMENT_FAMILY_SEEDS) | set(scenarios.VALIDATION_FAMILY_SEEDS))
        mods = (experiment_confirm, experiment_public, experiment_ack, experiment_status, experiment_burden, experiment_coord,
                experiment_capacity, experiment_interrupt, experiment_order, experiment_order2, experiment_wide,
                experiment_aware, experiment_tune, experiment_seen, experiment_surge, experiment_fair, experiment_silent,
                experiment_event, experiment_hold, experiment_duty, experiment_hold2, experiment_deadline, experiment_reactive,
                experiment_surge2, experiment_calm, experiment_priority, experiment_triage)
        for m in mods:
            used |= set(m.DEV_MIX) | set(m.VAL_MIX) | set(m.DEV_FAMILY) | set(m.VAL_FAMILY)
            used |= set(getattr(m, 'DEV_MASS', ())) | set(getattr(m, 'VAL_MASS', ()))
        for m in (experiment_final, experiment_combo, experiment_final2, experiment_fastdecline, experiment_system,
                  experiment_priority2, experiment_surge3, experiment_region, experiment_sparse, experiment_package, experiment_predict):
            used |= set(m.MIX) | set(m.MASS) | set(getattr(m, 'FAMILY', ()))
        used |= set(experiment_levers.LEVER_MIX) | set(experiment_levers.LEVER_MASS)
        used |= set(experiment_levers.STRESS_MIX) | set(experiment_levers.STRESS_MASS)
        used |= set(experiment_voi.MIX) | set(experiment_voi.FAMILY) | {999990001, 999990002}
        new = set(ex.MIX) | set(ex.MASS)
        self.assertFalse(new & used)
        self.assertFalse(set(ex.MIX) & set(ex.MASS))
        self.assertFalse(any(lo <= s <= hi for s in new for lo, hi in scenarios.USED_SEED_RANGES))


if __name__ == '__main__':
    unittest.main()
