"""38차 병원이 적은 지역 적응 규칙: 이름·기본 환경 동등성·농어촌형 동작·판정 규칙·seed 검사."""
import copy
import unittest
from unittest import mock

from inquiry_lab import (eat_capacity_policy as ek, eat_sparse_policy as esp, experiment_hold2, experiment_region as region,
                         experiment_sparse as ex, hold2_runner as h2, mass_scenarios as ms, scenarios)


class PolicyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ek.install([])
        esp.install(['eatkr_n4_l0'])

    @classmethod
    def tearDownClass(cls):
        esp.uninstall()
        ek.uninstall()

    def test_names(self):
        self.assertEqual(esp.parse('eatkr_n4_l0'), (4, 0))
        for bad in ('eatkr_n0_l0', 'eatkr_4_0', 'eatks_seenbase_k10_m4'):
            with self.assertRaises(ValueError):
                esp.parse(bad)

    def test_identical_when_more_than_four_hospitals(self):
        for scenario in (scenarios.make_mix(experiment_hold2.DEV_MIX[4]), ms.make_mass(experiment_hold2.DEV_MASS[9])):
            self.assertGreater(len(scenario['hospitals']), 4)
            ours = h2.run_scenario(scenario, [('eatkr_n4_l0', ex.G_RULE)])['branches'][0]
            theirs = h2.run_scenario(scenario, [(ex.FOCUS, ex.G_RULE)])['branches'][0]
            for key in ('patients', 'metrics', 'backup', 'holds'):
                self.assertEqual(ours[key], theirs[key], key)

    def test_rural_asks_at_least_as_many(self):
        rural = region.regionalize(scenarios.make_mix(experiment_hold2.DEV_MIX[4]), **ex.RURAL)
        calls = []
        original = esp.decide

        def record(name, **kw):
            calls.append(copy.deepcopy(kw))
            return original(name, **kw)
        with mock.patch.object(esp, 'decide', side_effect=record):
            h2.run_scenario(rural, [('eatkr_n4_l0', ex.G_RULE)])
        self.assertTrue(calls)
        more = 0
        for kw in calls:
            policy = esp.eat_seen_policy.spec(esp.BASE)
            self.assertLessEqual(esp.hospitals_for_patient(policy, kw.get('candidates'), kw['max_new']), 4)
            two = original('eatkr_n4_l0', **copy.deepcopy(kw))
            one = esp.eat_seen_policy.decide(esp.BASE, **copy.deepcopy(kw))
            self.assertGreaterEqual(len(two['send']), len(one['send']))
            more += len(two['send']) > len(one['send'])
        self.assertGreater(more, 0)


class ProtocolTest(unittest.TestCase):
    def test_verdict(self):
        row = lambda d: dict(delta_J_mean=d, ci95=[d - 3, d + 3])
        report = lambda mix, mass: dict(groups=dict(mix=dict(S_minus_G=row(mix)), mass=dict(S_minus_G=row(mass))))
        self.assertTrue(ex.verdict(report(-5.0, 1.0))['adopted'])
        self.assertFalse(ex.verdict(report(-2.0, 1.0))['adopted'])      # 상한 0 미만이 없음
        self.assertFalse(ex.verdict(report(-5.0, 2.5))['adopted'])      # 다수 상한 5.5

    def test_new_seeds_were_never_used(self):
        from inquiry_lab import (experiment_ack, experiment_aware, experiment_burden, experiment_calm, experiment_capacity,
                                 experiment_combo, experiment_confirm, experiment_coord, experiment_deadline, experiment_duty,
                                 experiment_event, experiment_fair, experiment_fastdecline, experiment_final, experiment_final2,
                                 experiment_hold, experiment_interrupt, experiment_levers, experiment_order, experiment_order2,
                                 experiment_priority, experiment_priority2, experiment_public, experiment_reactive, experiment_seen,
                                 experiment_silent, experiment_status, experiment_surge, experiment_surge2, experiment_surge3,
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
                  experiment_priority2, experiment_surge3, region):
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
