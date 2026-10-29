"""25차 마감을 아는 EAT-K: 이름·15차 정책과의 동등성·마감 창 동작·선택/판정 규칙·seed 검사."""
import copy
import unittest
from unittest import mock

from inquiry_lab import (eat_capacity_policy as ek, eat_first_policy as ef, eat_seen_policy as es, experiment_deadline as ex,
                         hold2_runner as h2, mass_scenarios as ms, scenarios)


class NameTest(unittest.TestCase):
    def test_names(self):
        self.assertEqual(ef.parse('eatkf_seenbase_k10_m4'), ('eatks_seenbase_k10_m4', 0, None))
        self.assertEqual(ef.parse('eatkd_t120_m6'), ('eatks_seenbase_k10_m4', 120, 6))
        for bad in ('eatkd_t0_m6', 'eatkd_t120_m4', 'eatkd_t400_m6', 'eatkf_seenbase_k0_m4', 'eatks_seenbase_k10_m4'):
            with self.assertRaises(ValueError):
                ef.parse(bad)
        self.assertEqual(ef.spec('eatkd_t150_m8')['config']['deadline_max_concurrent'], 8)
        self.assertEqual(len(set(ex.POLICIES)), 6)


class PolicyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ek.install([])
        ef.install(list(ex.CANDIDATES))
        cls.mass = ms.make_mass(ex.DEV_MASS[0])

    @classmethod
    def tearDownClass(cls):
        ef.uninstall()
        ek.uninstall()

    def test_without_first_contact_matches_round15_policy(self):
        with mock.patch.object(ef, 'first_contact', return_value=None):
            ours = h2.run_scenario(self.mass, [('eatkf_seenbase_k10_m4', ex.RULE)])['branches'][0]
        theirs = h2.run_scenario(self.mass, [(ex.FOCUS, ex.RULE)])['branches'][0]
        for key in ('patients', 'metrics', 'backup', 'holds'):
            self.assertEqual(ours[key], theirs[key], key)

    def test_first_contact_is_used_and_deadline_raises_cap(self):
        calls = []
        original = ef.decide

        def record(name, **kw):
            if name == 'eatkf_seenbase_k10_m4':
                calls.append((copy.deepcopy(kw), copy.deepcopy(ef._LAST)))
            return original(name, **kw)
        with mock.patch.object(ef, 'decide', side_effect=record):
            h2.run_scenario(self.mass, [('eatkf_seenbase_k10_m4', ex.RULE)])
        self.assertTrue(calls)
        later, more = 0, 0
        for kw, last in calls:
            with mock.patch.dict(ef._LAST, last, clear=True):
                if last.get('first') is not None and kw.get('last_contact_at') is not None and last['first'] < kw['last_contact_at']:
                    later += 1                              # 마지막 문의보다 첫 문의가 이르다 = 경과 시간을 짧게 보던 경우
                one = ef.decide('eatkf_seenbase_k10_m4', **copy.deepcopy(kw))
                two = ef.decide('eatkd_t150_m8', **copy.deepcopy(kw))
                elapsed = kw['now'] - last['first'] if last.get('first') is not None else 0
                if 300 - elapsed > 150:
                    self.assertEqual(one['send'], two['send'])
                else:
                    self.assertGreaterEqual(len(two['send']), len(one['send']))
                    more += len(two['send']) > len(one['send'])
        self.assertGreater(later, 0)
        self.assertGreater(more, 0)


class ProtocolTest(unittest.TestCase):
    def report(self, mix, mass):
        def rows(deltas):
            return {c: dict(versus_base=dict(delta_J_mean=deltas[c], ci95=[deltas[c] - 3, deltas[c] + 3])) for c in ex.CANDIDATES}
        return dict(groups=dict(mix=rows(mix), mass=rows(mass)))

    def test_selection_and_verdict(self):
        f, a, b, c, d = ex.CANDIDATES
        r = self.report({f: 0.0, a: -2.0, b: 3.0, c: -1.0, d: -1.5}, {f: -1.0, a: -4.0, b: -9.0, c: -2.0, d: -3.0})
        s = ex.select(r)
        self.assertEqual(s['policy'], a)                    # b는 혼합 +3초라 제외
        self.assertTrue(ex.verdict(r, a)['adopted'])        # 혼합 −2 + 3 < 5, 다수 −4 + 3 < 0
        self.assertFalse(ex.verdict(r, f)['adopted'])       # 상한 0 미만이 없다
        r = self.report({x: 1.0 for x in ex.CANDIDATES}, {x: -0.5 for x in ex.CANDIDATES})
        self.assertFalse(ex.select(r)['proceed'])

    def test_new_seeds_were_never_used(self):
        from inquiry_lab import (experiment_ack, experiment_aware, experiment_burden, experiment_capacity,
                                 experiment_combo, experiment_confirm, experiment_coord, experiment_duty, experiment_event,
                                 experiment_fair, experiment_final, experiment_final2, experiment_hold, experiment_hold2,
                                 experiment_interrupt, experiment_order, experiment_order2, experiment_public, experiment_seen,
                                 experiment_silent, experiment_status, experiment_surge, experiment_tune, experiment_voi,
                                 experiment_wide)
        used = (set(scenarios.DEVELOPMENT_MIX_SEEDS) | set(scenarios.VALIDATION_MIX_SEEDS)
                | set(scenarios.DEVELOPMENT_FAMILY_SEEDS) | set(scenarios.VALIDATION_FAMILY_SEEDS))
        mods = (experiment_confirm, experiment_public, experiment_ack, experiment_status, experiment_burden, experiment_coord,
                experiment_capacity, experiment_interrupt, experiment_order, experiment_order2, experiment_wide,
                experiment_aware, experiment_tune, experiment_seen, experiment_surge, experiment_fair, experiment_silent,
                experiment_event, experiment_hold, experiment_duty, experiment_hold2)
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
