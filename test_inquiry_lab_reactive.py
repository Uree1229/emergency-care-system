"""보조 호출 대상 확대(ra): 규칙 이름·24차 실행기와의 동등성·2명 병원 호출 동작·선택/판정 규칙·seed 검사."""
import unittest

from inquiry_lab import (eat_capacity_policy as ek, eat_seen_policy as es, experiment_reactive as ex, hold2_runner as h2,
                         mass_scenarios as ms, reactive_runner as rr, scenarios)


class RuleTest(unittest.TestCase):
    def test_names(self):
        self.assertFalse(rr.parse_rule(ex.BASE)['reactive_all'])
        self.assertTrue(all(rr.parse_rule(c)['reactive_all'] for c in ex.CANDIDATES))
        self.assertEqual([rr.parse_rule(c)['capacity']['linger'] for c in ex.CANDIDATES], [120, 90, 60])
        for bad in ('ra', 'base+ra', 'min2+ra', f'{ex.BASE}+ra+ra'):
            with self.assertRaises(ValueError):
                rr.parse_rule(bad)
        self.assertEqual(len(set(ex.RULES)), 4)


class RunnerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ek.install([])
        es.install([ex.FOCUS])
        seed = next(s for s in ex.DEV_MIX if scenarios.make_mix(s)['mix']['arrival'] == 'burst'
                    and any(h['workers'] == 2 for h in scenarios.make_mix(s)['hospitals']))
        cls.burst = scenarios.make_mix(seed)
        cls.mass = ms.make_mass(ex.DEV_MASS[0])

    @classmethod
    def tearDownClass(cls):
        es.uninstall()
        ek.uninstall()

    def test_without_rule_matches_round24_runner(self):
        arms = [('fixed_3', 'base'), (ex.FOCUS, ex.BASE), ('fixed_3', 'od_k2_d60_l60' + ex.TAIL)]
        for scenario in (self.burst, self.mass):
            ours, theirs = rr.run_scenario(scenario, arms), h2.run_scenario(scenario, arms)
            for a, b in zip(ours['branches'], theirs['branches']):
                for key in ('patients', 'metrics', 'trace', 'backup', 'silent_closed', 'replans', 'holds'):
                    self.assertEqual(a[key], b[key], key)

    def test_two_worker_hospitals_are_called_at_k_plus_one(self):
        found = 0
        for scenario in (self.burst, self.mass):
            two = {h['id'] for h in scenario['hospitals'] if h['workers'] == 2}
            branch = rr.run_scenario(scenario, [(ex.FOCUS, ex.CANDIDATES[0])])['branches'][0]
            for t in branch['trace']:
                if t['type'] == 'backup_called' and t['hospital_id'] in two and not t.get('surge'):
                    self.assertGreaterEqual(t['unanswered'], 3)   # 두 명 모두 바쁘고 1건이 더 기다림
                    found += 1
        self.assertGreater(found, 0)


class ProtocolTest(unittest.TestCase):
    def report(self, mix, mass, duty):
        def rows(deltas):
            return {c: dict(versus_base=dict(delta_J_mean=deltas[c], ci95=[deltas[c] - 3, deltas[c] + 3])) for c in ex.CANDIDATES}
        return dict(groups=dict(mix={ex.FOCUS: rows(mix)}, mass={ex.FOCUS: rows(mass)}), pooled_duty=duty,
                    pooled_duty_difference={c: dict(difference_per_patient=duty[c] - 200, ci95=[duty[c] - 205, duty[c] - 195])
                                            for c in ex.CANDIDATES})

    def test_selection_prefers_less_duty_among_near_best(self):
        l120, l90, l60 = ex.CANDIDATES
        r = self.report({l120: -3.0, l90: -2.5, l60: -2.0}, {l120: -7.0, l90: -6.5, l60: -6.0}, {l120: 230, l90: 205, l60: 185})
        s = ex.select(r)
        self.assertEqual(s['rule'], l60)                    # 평균 −4.0은 최솟값 −5.0에서 1초 이내, 근무가 가장 적다
        self.assertTrue(ex.verdict(r, l60)['adopted'])      # 혼합 −2 + 3 < 5, 다수 −6 + 3 < 0
        r = self.report({l120: -3.0, l90: -1.0, l60: 3.0}, {l120: -7.0, l90: -2.0, l60: -6.0}, {l120: 230, l90: 205, l60: 185})
        self.assertEqual(ex.select(r)['rule'], l120)        # l60은 혼합 +3초로 제외, l90은 평균이 1초보다 멀다
        r = self.report({c: 1.0 for c in ex.CANDIDATES}, {c: -0.5 for c in ex.CANDIDATES}, {c: 200 for c in ex.CANDIDATES})
        self.assertFalse(ex.select(r)['proceed'])

    def test_new_seeds_were_never_used(self):
        from inquiry_lab import (experiment_ack, experiment_aware, experiment_burden, experiment_capacity,
                                 experiment_combo, experiment_confirm, experiment_coord, experiment_deadline, experiment_duty,
                                 experiment_event, experiment_fair, experiment_final, experiment_final2, experiment_hold,
                                 experiment_hold2, experiment_interrupt, experiment_order, experiment_order2, experiment_public,
                                 experiment_seen, experiment_silent, experiment_status, experiment_surge, experiment_tune,
                                 experiment_voi, experiment_wide)
        used = (set(scenarios.DEVELOPMENT_MIX_SEEDS) | set(scenarios.VALIDATION_MIX_SEEDS)
                | set(scenarios.DEVELOPMENT_FAMILY_SEEDS) | set(scenarios.VALIDATION_FAMILY_SEEDS))
        mods = (experiment_confirm, experiment_public, experiment_ack, experiment_status, experiment_burden, experiment_coord,
                experiment_capacity, experiment_interrupt, experiment_order, experiment_order2, experiment_wide,
                experiment_aware, experiment_tune, experiment_seen, experiment_surge, experiment_fair, experiment_silent,
                experiment_event, experiment_hold, experiment_duty, experiment_hold2, experiment_deadline)
        for m in mods:
            used |= set(m.DEV_MIX) | set(m.VAL_MIX) | set(m.DEV_FAMILY) | set(m.VAL_FAMILY)
            used |= set(getattr(m, 'DEV_MASS', ())) | set(getattr(m, 'VAL_MASS', ()))
        for m in (experiment_final, experiment_combo, experiment_final2):
            used |= set(m.MIX) | set(m.MASS) | set(m.FAMILY)
        used |= set(experiment_voi.MIX) | set(experiment_voi.FAMILY) | {999990001, 999990002}
        new = set(ex.DEV_MIX) | set(ex.VAL_MIX) | set(ex.DEV_MASS) | set(ex.VAL_MASS) | set(ex.DEV_FAMILY) | set(ex.VAL_FAMILY)
        self.assertFalse(new & used)
        for a, b in ((ex.DEV_MIX, ex.VAL_MIX), (ex.DEV_MASS, ex.VAL_MASS), (ex.DEV_MIX, ex.DEV_MASS), (ex.VAL_MIX, ex.VAL_MASS)):
            self.assertFalse(set(a) & set(b))
        self.assertFalse(any(lo <= s <= hi for s in new for lo, hi in scenarios.USED_SEED_RANGES))


if __name__ == '__main__':
    unittest.main()
