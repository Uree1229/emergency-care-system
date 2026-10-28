"""2차 확정 대기 실험: 실행기 동등성·확정 규칙·선택 규칙 검사."""
import unittest

from inquiry_lab import confirm_policy, eat_policy, experiment_confirm, harness, runner, scenarios
from server import inquiry_experiment


class RunnerEquivalenceTest(unittest.TestCase):
    def setUp(self):
        harness.install(['eat_l15_f'])

    def tearDown(self):
        harness.uninstall()

    def test_immediate_rule_reproduces_server_experiment_exactly(self):
        policies = ('fixed_3', 'v3_w45_h900', 'eat_l15_f')
        for scenario in (harness.build('family:staggered_w2', 870001), scenarios.make_mix(850002)):
            original = inquiry_experiment.run_scenario(scenario, policies=policies)
            ours = runner.run_scenario(scenario, [(p, 'immediate') for p in policies])
            for a, b in zip(original['branches'], ours['branches']):
                for key in ('patients', 'metrics', 'decisions', 'trace'):
                    self.assertEqual(a[key], b[key], (scenario['id'], a['policy'], key))

    def test_hold_never_loses_a_single_patient_acceptance(self):
        for seed in (850001, 850005, 850009, 850013):
            scenario = scenarios.make_mix(seed)
            if len(scenario['patients']) != 1:
                continue
            result = runner.run_scenario(scenario, [('fixed_3', 'immediate'), ('fixed_3', 'hold120')])
            a, b = (br['patients'][0] for br in result['branches'])
            self.assertEqual(a['failed'], b['failed'])
            if not a['failed']:
                self.assertLessEqual(b['selected_at'], 300)


class ConfirmRuleTest(unittest.TestCase):
    NOW = 10000

    def decide(self, own, hold=60, held=0, deadline_left=200, observations=()):
        pending = [dict(hospital=r['hospital'], patient=r['patient'], submitted_at=r['submitted_at']) for r in own]
        return confirm_policy.decide(now=self.NOW, accepted=[dict(hospital='H05', travel_seconds=600)],
                                     own_pending=own, observations=list(observations), pending=pending,
                                     deadline_left=deadline_left, hold=hold, held_seconds=held)

    def test_trivial_cases_confirm(self):
        near = [dict(hospital='H01', patient='P', submitted_at=self.NOW - 5, travel_seconds=300)]
        self.assertEqual(self.decide(near, hold=0)['reason'], 'immediate')
        self.assertEqual(self.decide([], hold=60)['reason'], 'no_nearer_pending')
        far = [dict(hospital='H09', patient='P', submitted_at=self.NOW - 5, travel_seconds=700)]
        self.assertEqual(self.decide(far)['reason'], 'no_nearer_pending')
        self.assertEqual(self.decide(near, held=58)['reason'], 'hold_budget_or_deadline')
        self.assertEqual(self.decide(near, deadline_left=4)['reason'], 'hold_budget_or_deadline')

    def test_likely_near_acceptance_holds_and_stale_one_confirms(self):
        accepts = [dict(hospital='H01', patient=f'O{k}', submitted_at=self.NOW - 900 + 60 * k,
                        observed_at=self.NOW - 880 + 60 * k, outcome='accept') for k in range(6)]
        fresh = [dict(hospital='H01', patient='P', submitted_at=self.NOW - 10, travel_seconds=300)]
        decision = self.decide(fresh, observations=accepts)
        self.assertEqual(decision['action'], 'hold')
        self.assertLess(decision['evidence']['best_hold_cost'], decision['evidence']['immediate_cost'])
        # 처리시간 표본이 20초 안팎인 병원에서 290초째 무응답이면 기다릴 이유가 거의 없다.
        stale = [dict(hospital='H01', patient='P', submitted_at=self.NOW - 290, travel_seconds=590)]
        self.assertEqual(self.decide(stale, observations=accepts)['action'], 'confirm')

    def test_pending_masses_match_eat_policy_model(self):
        row = dict(hospital='H01', patient='P', submitted_at=self.NOW - 40, travel_seconds=320)
        obs = [dict(hospital='H01', patient='Z', submitted_at=self.NOW - 500, observed_at=self.NOW - 430,
                    outcome='decline')]
        pending = [dict(hospital='H01', patient='P', submitted_at=self.NOW - 40)]
        result = eat_policy.decide('eat_l15_f', now=self.NOW, candidates=[], observations=obs, pending=pending,
                                   patient_pending=[row], max_new=5, last_contact_at=self.NOW - 40)
        horizon = 300 - 40
        cells = -(-horizon // 5)
        items = confirm_policy.pending_masses([row], now=self.NOW, observations=obs, pending=pending,
                                              cells=cells, step=5)
        value = eat_policy.expected_cost([(t, m) for _, t, m in items], cells, 5, horizon,
                                         eat_policy.BASE_CONFIG['failure_penalty_seconds'])
        self.assertAlmostEqual(round(value, 3), result['evidence']['expected_cost_without_new'], places=3)

    def test_rule_names(self):
        self.assertEqual(confirm_policy.parse_rule('immediate'), 0)
        self.assertEqual(confirm_policy.parse_rule('hold60'), 60)
        for bad in ('hold', 'hold-5', 'wait60', None):
            with self.assertRaises(ValueError):
                confirm_policy.parse_rule(bad)


class SelectionTest(unittest.TestCase):
    def test_selection_requires_all_conditions(self):
        def m(J, failed, work):
            return dict(J=J, failed=failed, review_work_seconds_per_patient=work)
        metrics = {'fixed_3|immediate': m(900, 10, 350), 'eat_l15_f|immediate': m(880, 9, 310),
                   'eat_l15_f|hold30': m(875, 9, 320), 'eat_l15_f|hold60': m(870, 10, 330),
                   'eat_l15_f|hold120': m(860, 9, 360)}
        chosen = experiment_confirm.select(metrics)
        self.assertEqual(chosen['arm'], 'eat_l15_f|hold30')      # hold60 실패 증가, hold120 업무 초과
        metrics['eat_l15_f|hold30'] = m(885, 9, 320)
        self.assertFalse(experiment_confirm.select(metrics)['adopted_for_validation'])

    def test_seed_ranges_are_new_and_disjoint(self):
        dev = set(experiment_confirm.DEV_MIX) | set(experiment_confirm.DEV_FAMILY)
        val = set(experiment_confirm.VAL_MIX) | set(experiment_confirm.VAL_FAMILY)
        round1 = (set(scenarios.DEVELOPMENT_MIX_SEEDS) | set(scenarios.VALIDATION_MIX_SEEDS)
                  | set(scenarios.DEVELOPMENT_FAMILY_SEEDS) | set(scenarios.VALIDATION_FAMILY_SEEDS))
        self.assertFalse(dev & val)
        self.assertFalse((dev | val) & round1)


if __name__ == '__main__':
    unittest.main()
