"""7차 여러 환자 동시 계획: EAT-J 상한·안전 규칙·경합 진단·선택 규칙 검사."""
from copy import deepcopy
import unittest

from inquiry_lab import eat_coord_policy, eat_policy, experiment_coord as ec, harness, scenarios
from server import inquiry_experiment


class EatJTest(unittest.TestCase):
    NOW = 4000

    def inputs(self, queue_others=(), own_pending=False):
        cands = [dict(hospital_id=f'H0{i}', travel=dict(duration_seconds=300 + 40 * i), resources_enough=True)
                 for i in range(1, 7)]
        pending = [dict(hospital=h, patient=f'Q{k}', submitted_at=self.NOW - 5) for k, h in enumerate(queue_others)]
        own = []
        if own_pending:
            own = [dict(hospital='H09', patient='P01', submitted_at=self.NOW - 20, travel_seconds=700)]
            pending.append(dict(hospital='H09', patient='P01', submitted_at=self.NOW - 20))
        return dict(now=self.NOW, candidates=cands, pending=pending, patient_pending=own, max_new=10,
                    last_contact_at=self.NOW - 20 if own_pending else None)

    def test_large_cap_matches_locked_eat(self):
        for queue in ((), ('H01', 'H01', 'H02')):
            ours = eat_coord_policy.decide('eatj_c99', **deepcopy(self.inputs(queue)))
            theirs = eat_policy.decide('eat_l15_f', **deepcopy(self.inputs(queue)))
            self.assertEqual(ours['send'], theirs['send'])
            a, b = dict(ours['evidence']), dict(theirs['evidence'])
            for key in ('policy', 'hospital_inflight_cap', 'capped_hospitals'):
                a.pop(key, None), b.pop(key, None)
            self.assertEqual(a, b)

    def test_capped_hospitals_are_not_asked_now(self):
        out = eat_coord_policy.decide('eatj_c1', **self.inputs(('H01', 'H02'), own_pending=True))
        self.assertNotIn('H01', out['send'])
        self.assertNotIn('H02', out['send'])
        self.assertEqual(out['evidence']['capped_hospitals'], ['H01', 'H02'])

    def test_safety_rule_still_sends_when_everything_is_capped(self):
        out = eat_coord_policy.decide('eatj_c1', **self.inputs(('H01', 'H02', 'H03', 'H04', 'H05', 'H06', 'H01')))
        self.assertEqual(len(out['send']), 1)
        self.assertNotEqual(out['send'][0], 'H01')     # 가장 붐비는 병원은 피한다

    def test_names(self):
        self.assertEqual(eat_coord_policy.spec('eatj_c2')['config']['hospital_inflight_cap'], 2)
        for bad in ('eatj_c0', 'eatj_2', 'eat_l15_f', None):
            with self.assertRaises(ValueError):
                eat_coord_policy.spec(bad)


class ExperimentTest(unittest.TestCase):
    def tearDown(self):
        eat_coord_policy.uninstall()

    def test_burst_case_spreads_inquiries(self):
        seed = next(s for s in ec.DEV_MIX if scenarios.make_mix(s)['mix']['arrival'] == 'burst')
        result = ec.run_case(('mix', seed, ec.DEV_POLICIES))
        self.assertEqual(set(result['branches']), set(ec.DEV_POLICIES))
        eat = result['branches']['eat_l15_f']['patients']
        tight = result['branches']['eatj_c1']['patients']
        self.assertLessEqual(sum(p['inquiry_count'] for p in tight), sum(p['inquiry_count'] for p in eat))
        inf = ec.run_case(('mix_inf', seed, (ec.BASE_POLICY,)))
        self.assertTrue(inf['scenario_id'].endswith('-infinite-workers'))
        headroom = ec.congestion_headroom([result], [inf])
        self.assertLessEqual(headroom['ci95'][0], headroom['delta_J_mean'])

    def test_selection_and_seeds(self):
        def m(J, failed, inq, work):
            return dict(J=J, failed=failed, inquiries_per_patient=inq, review_work_seconds_per_patient=work)
        metrics = {'fixed_3': m(950, 20, 6.0, 360), 'eat_l15_f': m(900, 15, 5.0, 320),
                   'eatj_c1': m(880, 16, 4.0, 300), 'eatj_c2': m(885, 15, 4.5, 310), 'eatj_c3': m(885, 15, 4.8, 315)}
        self.assertEqual(ec.select(metrics)['policy'], 'eatj_c3')       # c1 실패 증가, c2·c3 동률이면 큰 상한
        for c in ec.CANDIDATES:
            metrics[c] = m(901, 15, 4.5, 300)
        self.assertFalse(ec.select(metrics)['adopted_for_validation'])
        from inquiry_lab import experiment_ack, experiment_burden, experiment_confirm, experiment_public, experiment_status, experiment_voi
        used = (set(scenarios.DEVELOPMENT_MIX_SEEDS) | set(scenarios.VALIDATION_MIX_SEEDS)
                | set(experiment_confirm.DEV_MIX) | set(experiment_confirm.VAL_MIX)
                | set(experiment_public.DEV_MIX) | set(experiment_public.VAL_MIX) | set(experiment_voi.MIX)
                | set(experiment_ack.DEV_MIX) | set(experiment_ack.VAL_MIX)
                | set(experiment_status.DEV_MIX) | set(experiment_status.VAL_MIX)
                | set(experiment_burden.DEV_MIX) | set(experiment_burden.VAL_MIX))
        new = set(ec.DEV_MIX) | set(ec.VAL_MIX)
        self.assertFalse(new & used)
        self.assertFalse(set(ec.DEV_MIX) & set(ec.VAL_MIX))


if __name__ == '__main__':
    unittest.main()
