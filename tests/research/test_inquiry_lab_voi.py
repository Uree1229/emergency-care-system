"""4차 1단계: 회신 정보 가치 진단(완전 정보 EAT 변형) 검사."""
from copy import deepcopy
import unittest

from inquiry_lab import eat_oracle_policy, eat_policy, experiment_voi, harness, scenarios
from server import inquiry_workflow


class OracleDecideTest(unittest.TestCase):
    NOW = 7000

    def tearDown(self):
        eat_oracle_policy.clear()

    def inputs(self, variant):
        cands = [dict(hospital_id=f'H0{i}', travel=dict(duration_seconds=300 + 40 * i), resources_enough=True)
                 for i in range(1, 7)]
        if variant == 0:
            return dict(now=self.NOW, candidates=cands, max_new=10)
        own = [dict(hospital='H01', patient='P01', submitted_at=self.NOW - 50, travel_seconds=340)]
        pending = [dict(hospital='H01', patient='P01', submitted_at=self.NOW - 50),
                   dict(hospital='H03', patient='P02', submitted_at=self.NOW - 20)]
        obs = [dict(hospital='H02', patient='Z', submitted_at=self.NOW - 700, observed_at=self.NOW - 640,
                    outcome='decline')]
        return dict(now=self.NOW, candidates=cands[1:], observations=obs, pending=pending,
                    patient_pending=own, max_new=9, last_contact_at=self.NOW - 50)

    def test_without_information_matches_locked_eat(self):
        for name in eat_oracle_policy.KINDS:
            for variant in (0, 1):
                ours = eat_oracle_policy.decide(name, **deepcopy(self.inputs(variant)))
                theirs = eat_policy.decide('eat_l15_f', **deepcopy(self.inputs(variant)))
                for key in ('send', 'reason', 'next_review_at'):
                    self.assertEqual(ours[key], theirs[key], (name, variant, key))
                a, b = dict(ours['evidence']), dict(theirs['evidence'])
                for key in ('policy', 'oracle_information'):
                    a.pop(key, None), b.pop(key, None)
                self.assertEqual(a, b)

    def test_information_is_used_only_for_the_current_patient(self):
        potentials = [dict(patient_id='P01', hospital_id=f'H0{i}', service_seconds=20 + 10 * i,
                           no_response=(i == 1), accept=(i != 2)) for i in range(1, 7)]
        potentials += [dict(patient_id='P02', hospital_id=f'H0{i}', service_seconds=200, no_response=False,
                            accept=False) for i in range(1, 7)]
        eat_oracle_policy.set_scenario(potentials)
        eat_oracle_policy._CONTEXT['patient'] = 'P01'
        out = eat_oracle_policy.decide('eato_all', **self.inputs(0))
        plan = {row['hospital']: row for row in out['evidence']['candidate_plan']}
        self.assertLess(plan['H01']['reply_probability'], 1e-3)       # 무응답
        self.assertLess(plan['H02']['accept_probability'], 1e-3)      # 불가
        self.assertAlmostEqual(plan['H03']['accept_probability'], 1.0)
        self.assertNotIn('H01', out['send'])
        self.assertNotIn('H02', out['send'])
        silence = eat_oracle_policy.decide('eato_silence', **self.inputs(0))
        splan = {row['hospital']: row for row in silence['evidence']['candidate_plan']}
        self.assertLess(splan['H01']['reply_probability'], 1e-3)
        self.assertGreater(splan['H02']['accept_probability'], 0.2)    # 무응답만 알고 수용 여부는 모름
        with self.assertRaises(ValueError):
            eat_oracle_policy.spec('eato_nothing')


class IntegrationTest(unittest.TestCase):
    def tearDown(self):
        eat_oracle_policy.uninstall()

    def test_full_reply_information_reaches_single_patient_optimum(self):
        reached = 0
        for kind, seed in (('family:delayed_near_fast_far', 940001), ('family:no_response_near', 940001),
                           ('family:immediate_all_accept', 940002), ('family:history_helpful', 940003)):
            result = experiment_voi.run_case((kind, seed))
            best = result['oracle']['arrival_seconds']['P01']
            full = result['branches']['eato_all']['patients'][0]
            eat = result['branches']['eat_l15_f']['patients'][0]
            self.assertFalse(full['failed'])
            self.assertGreaterEqual(full['estimated_arrival_seconds'], best)
            self.assertLessEqual(full['estimated_arrival_seconds'], eat['estimated_arrival_seconds'])
            reached += full['estimated_arrival_seconds'] == best
        self.assertGreaterEqual(reached, 3)

    def test_wrapper_restores_and_other_branches_unchanged(self):
        result = experiment_voi.run_case(('mix', 930005))
        plain = harness.run_case(('mix', 930005, ('fixed_3', 'eat_l15_f')))
        for policy in ('fixed_3', 'eat_l15_f'):
            self.assertEqual(result['branches'][policy]['patients'], plain['branches'][policy]['patients'])
        self.assertIsNone(eat_oracle_policy._CONTEXT['patient'])
        eat_oracle_policy.uninstall()
        self.assertIs(inquiry_workflow._apply, eat_oracle_policy._ORIGINAL_APPLY)

    def test_seeds_are_new(self):
        from inquiry_lab import experiment_confirm, experiment_public
        used = (set(scenarios.DEVELOPMENT_MIX_SEEDS) | set(scenarios.VALIDATION_MIX_SEEDS)
                | set(experiment_confirm.DEV_MIX) | set(experiment_confirm.VAL_MIX)
                | set(experiment_public.DEV_MIX) | set(experiment_public.VAL_MIX))
        self.assertFalse(set(experiment_voi.MIX) & used)


if __name__ == '__main__':
    unittest.main()
