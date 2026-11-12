"""40차 수용 예측 가능 환경: 환경 변환·지식 표·정책 동등성·유형 반영 동작·판정 규칙·seed 검사."""
import copy
import unittest
from unittest import mock

from inquiry_lab import (eat_capacity_policy as ek, eat_type_policy as et, experiment_hold2, experiment_predict as ex, hold2_runner as h2,
                         mass_scenarios as ms, predictable_scenarios as ps, scenarios)


class EnvironmentTest(unittest.TestCase):
    def test_predictable_and_knowledge(self):
        s = scenarios.make_mix(experiment_hold2.DEV_MIX[6])
        original = copy.deepcopy(s)
        t = ps.predictable(s)
        self.assertEqual(s, original)                                         # 원본은 바꾸지 않는다
        self.assertEqual(t, ps.predictable(s))                                # 해시로 고정
        types = {p['id']: p['ptype'] for p in t['patients']}
        for a, b in zip(s['potentials'], t['potentials']):
            self.assertEqual((a['no_response'], a['service_seconds']), (b['no_response'], b['service_seconds']))
            category = ps.fit(s['id'], b['hospital_id'], types[b['patient_id']])
            if category == 'normal':
                self.assertEqual(a['accept'], b['accept'])
            elif category == 'strong':
                self.assertTrue(b['accept'] or not a['accept'])               # 강함은 수용을 줄이지 않는다
            else:
                self.assertTrue(a['accept'] or not b['accept'])               # 약함은 수용을 늘리지 않는다
        true = ps.knowledge(t, 0.0)
        self.assertEqual(true['types'], types)
        self.assertTrue(all(true['fit'][(pt, h['id'])] == ps.fit(s['id'], h['id'], pt) for pt in ps.TYPES for h in t['hospitals']))


class PolicyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ek.install([])
        et.install()
        cls.mass = ps.predictable(ms.make_mass(experiment_hold2.DEV_MASS[10]))

    @classmethod
    def tearDownClass(cls):
        et.uninstall()
        ek.uninstall()

    def test_without_knowledge_matches_round15_policy(self):
        et.set_knowledge(None)
        ours = h2.run_scenario(self.mass, [(et.NAME, ex.G_RULE)])['branches'][0]
        theirs = h2.run_scenario(self.mass, [(ex.FOCUS, ex.G_RULE)])['branches'][0]
        for key in ('patients', 'metrics', 'backup', 'holds'):
            self.assertEqual(ours[key], theirs[key], key)

    def test_knowledge_steers_first_inquiries_toward_strong_fit(self):
        knowledge = ps.knowledge(self.mass, 0.0)
        calls, original = [], et.decide

        def record(name, **kw):
            out = original(name, **kw)
            calls.append((copy.deepcopy(et._LAST), out['send']))
            return out
        et.set_knowledge(knowledge)
        try:
            with mock.patch.object(et, 'decide', side_effect=record):
                h2.run_scenario(self.mass, [(et.NAME, ex.G_RULE)])
        finally:
            et.set_knowledge(None)
        sent = [(knowledge['types'][last['patient']], h) for last, send in calls for h in send]
        self.assertTrue(sent)
        share = lambda cat: sum(knowledge['fit'][x] == cat for x in sent) / len(sent)
        self.assertGreater(share('strong'), share('weak'))                   # 적합도 강한 병원에 더 많이 묻는다


class ProtocolTest(unittest.TestCase):
    def test_verdict(self):
        row = lambda d: dict(delta_J_mean=d, ci95=[d - 3, d + 3])

        def report(tf, tn):
            return dict(groups={k: dict(comparisons=dict(TF_minus_G=row(tf[i]), TN_minus_G=row(tn[i]))) for i, k in enumerate(('mix', 'mass'))})
        v = ex.verdict(report((-20.0, -40.0), (-10.0, 1.0)))
        self.assertTrue(v['confirmed'] and v['noisy_useful'])
        v = ex.verdict(report((-20.0, -1.0), (-1.0, 3.0)))
        self.assertFalse(v['confirmed'] or v['noisy_useful'])

    def test_new_seeds_were_never_used(self):
        from inquiry_lab import (experiment_ack, experiment_aware, experiment_burden, experiment_calm, experiment_capacity,
                                 experiment_combo, experiment_confirm, experiment_coord, experiment_deadline, experiment_duty,
                                 experiment_event, experiment_fair, experiment_fastdecline, experiment_final, experiment_final2,
                                 experiment_hold, experiment_interrupt, experiment_levers, experiment_order, experiment_order2,
                                 experiment_package, experiment_priority, experiment_priority2, experiment_public,
                                 experiment_reactive, experiment_region, experiment_seen, experiment_silent, experiment_sparse,
                                 experiment_status, experiment_surge, experiment_surge2, experiment_surge3, experiment_system,
                                 experiment_triage, experiment_tune, experiment_voi, experiment_wide)
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
                  experiment_priority2, experiment_surge3, experiment_region, experiment_sparse, experiment_package):
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
