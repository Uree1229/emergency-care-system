"""4차 2단계: '확인 중' 환경과 EAT-C 검사."""
from copy import deepcopy
import unittest

from inquiry_lab import ack_environment, eat_ack_policy, eat_policy, experiment_ack, experiment_voi, harness, scenarios
from server import inquiry_experiment


class AckEnvironmentTest(unittest.TestCase):
    def setUp(self):
        eat_ack_policy.install()

    def tearDown(self):
        eat_ack_policy.uninstall()

    def test_off_mode_reproduces_server_experiment(self):
        policies = ('fixed_3', 'v3_w45_h900', 'eat_l15_f')
        for scenario in (harness.build('family:staggered_w2', 960001), scenarios.make_mix(950004)):
            original = inquiry_experiment.run_scenario(scenario, policies=policies)
            ours = ack_environment.run_scenario(scenario, [(p, 'off') for p in policies])
            for a, b in zip(original['branches'], ours['branches']):
                self.assertEqual(a['patients'], b['patients'])
                self.assertEqual(a['decisions'], b['decisions'])
                self.assertEqual(a['trace'], b['trace'])
                metrics = dict(b['metrics'])
                self.assertEqual(metrics.pop('checking_count'), 0)
                self.assertEqual(a['metrics'], metrics)

    def test_on_mode_sends_checking_before_decisions_and_keeps_fixed_policy(self):
        scenario = scenarios.make_mix(950007)
        result = ack_environment.run_scenario(scenario, [('fixed_3', 'off'), ('fixed_3', 'on'), ('eat_l15_f', 'on')])
        off, on, eat = result['branches']
        self.assertEqual(off['patients'], on['patients'])        # 고정 정책은 관측을 쓰지 않는다
        self.assertGreater(on['metrics']['checking_count'], 0)
        pot = {(p['patient_id'], p['hospital_id']): p for p in scenario['potentials']}
        for branch in (on, eat):
            started = {}
            for e in branch['trace']:
                key = (e['patient_id'], e['hospital_id'])
                if e['type'] == 'review_started':
                    started[key] = e['at']
                if e['type'] == 'reply_checking':
                    delay = e['at'] - started[key]
                    self.assertTrue(0 <= delay < pot[key]['service_seconds'])
                    self.assertEqual(delay, ack_environment.ack_plan(scenario, *key, pot[key]))

    def test_silent_inquiries_rarely_press_checking(self):
        pressed = {True: [0, 0], False: [0, 0]}
        for seed in experiment_ack.DEV_MIX[:60]:
            s = scenarios.make_mix(seed)
            for p in s['potentials']:
                plan = ack_environment.ack_plan(s, p['patient_id'], p['hospital_id'], p)
                pressed[p['no_response']][0] += plan is not None
                pressed[p['no_response']][1] += 1
        silent = pressed[True][0] / pressed[True][1]
        replying = pressed[False][0] / pressed[False][1]
        self.assertLess(silent, 0.12)
        self.assertGreater(replying, 0.45)


class DecisionLedgerTest(unittest.TestCase):
    def tearDown(self):
        eat_ack_policy.uninstall()

    def test_extension_adds_checked_decisions_without_changing_other_policies(self):
        from inquiry_lab import decision_ledger
        scenario = scenarios.make_mix(950011)
        harness.install(['eat_l15_f'])
        plain = ack_environment.run_scenario(scenario, [('eat_l15_f', 'on'), ('v3_w45_h900', 'on')])
        decision_ledger.install()
        extended = ack_environment.run_scenario(scenario, [('eat_l15_f', 'on'), ('v3_w45_h900', 'on')])
        for a, b in zip(plain['branches'], extended['branches']):
            self.assertEqual(a['patients'], b['patients'])
            self.assertEqual(a['trace'], b['trace'])
        decision_ledger.uninstall()

    def test_checked_rows_are_ordered_and_follow_checking(self):
        from inquiry_lab import decision_ledger
        from server import db as server_db
        import tempfile
        from pathlib import Path
        scenario = scenarios.make_mix(950013)
        harness.install(['eat_l15_f'])
        # 확인 중 환경을 한 번 돌리고 마지막 DB에서 확장 행을 다시 만든다.
        with tempfile.TemporaryDirectory() as directory:
            base = inquiry_experiment._base_database(Path(directory) / 'b.db', scenario)
            h = inquiry_experiment.snapshot_digest(base)
            branch = server_db.connect(Path(directory) / 'a.db')
            base.backup(branch)
            branch.close()
            base.close()
            out = ack_environment._branch(Path(directory) / 'a.db', scenario, 'fixed_3', 'on', h, 'x')
            conn = server_db.connect(Path(directory) / 'a.db')
            # 장부 창(30분) 안의 시각에서 조회한다.
            rows = decision_ledger.checked_decisions(conn, inquiry_experiment.EPOCH + 1_000)
            conn.close()
        checks = sum(e['type'] == 'reply_checking' for e in out['trace'])
        decided_after_check = {(e['patient_id'], e['hospital_id']) for e in out['trace']
                               if e['type'] in ('reply_accept', 'reply_decline')} & \
                              {(e['patient_id'], e['hospital_id']) for e in out['trace'] if e['type'] == 'reply_checking'}
        self.assertGreater(checks, 0)
        self.assertEqual({(r['patient'], r['hospital']) for r in rows}, decided_after_check)
        for r in rows:
            self.assertTrue(r['submitted_at'] <= r['checked_at'] <= r['observed_at'])


class EatCTest(unittest.TestCase):
    NOW = 9000

    def inputs(self, own_sub=40, observations=()):
        cands = [dict(hospital_id=f'H0{i}', travel=dict(duration_seconds=300 + 40 * i), resources_enough=True)
                 for i in range(2, 7)]
        own = [dict(hospital='H01', patient='P01', submitted_at=self.NOW - own_sub, travel_seconds=340)]
        pending = [dict(hospital='H01', patient='P01', submitted_at=self.NOW - own_sub)]
        return dict(now=self.NOW, candidates=cands, observations=list(observations), pending=pending,
                    patient_pending=own, max_new=9, last_contact_at=self.NOW - own_sub)

    def test_zero_ack_rate_matches_locked_eat(self):
        for sub in (5, 40, 150):
            ours = eat_ack_policy.decide('eatc_k5_r0', **deepcopy(self.inputs(sub)))
            theirs = eat_policy.decide('eat_l15_f', **deepcopy(self.inputs(sub)))
            self.assertEqual(ours['send'], theirs['send'])
            self.assertEqual(ours['reason'], theirs['reason'])
            a, b = dict(ours['evidence']), dict(theirs['evidence'])
            for key in ('policy', 'checking'):
                a.pop(key, None), b.pop(key, None)
            self.assertEqual(a, b)

    def test_checking_is_evidence_not_a_service_sample(self):
        config = eat_ack_policy.spec('eatc_k5')['config']
        decided = [dict(hospital='H02', submitted_at=self.NOW - 900 + 100 * k, observed_at=self.NOW - 840 + 100 * k,
                        outcome='decline') for k in range(4)]
        checks = [dict(hospital='H03', submitted_at=self.NOW - 500 + 50 * k, observed_at=self.NOW - 497 + 50 * k,
                       outcome='reply') for k in range(4)]
        plain = eat_ack_policy.AckBeliefs(decided, self.NOW, config)
        with_checks = eat_ack_policy.AckBeliefs(decided + checks, self.NOW, config)
        self.assertAlmostEqual(plain.mu_global, with_checks.mu_global)       # 처리시간 추정 불변
        self.assertGreater(with_checks.reply('H03'), plain.reply('H03'))      # 관여 증거
        self.assertGreater(with_checks.ack_rate, plain.ack_rate)
        eat_beliefs = eat_policy.Beliefs(decided + checks, self.NOW, config)
        self.assertLess(eat_beliefs.mu_global, plain.mu_global)              # 1차 EAT는 짧게 오인

    def test_checking_makes_policy_wait_and_missing_checking_makes_it_hedge(self):
        # 누름 비율이 높다는 이력(다른 환자 첫 회신 대부분이 확인 중)
        history = [dict(hospital=f'H0{2 + k % 4}', patient=f'O{k}', submitted_at=self.NOW - 1200 + 40 * k,
                        observed_at=self.NOW - 1195 + 40 * k, outcome='reply') for k in range(12)]
        acked = history + [dict(hospital='H01', patient='P01', submitted_at=self.NOW - 30,
                                observed_at=self.NOW - 25, outcome='reply')]
        waiting = eat_ack_policy.decide('eatc_k5', **self.inputs(30, acked))
        missing = eat_ack_policy.decide('eatc_k5', **self.inputs(30, history))
        self.assertGreaterEqual(len(missing['send']), len(waiting['send']))
        self.assertGreater(len(missing['send']), 0)
        self.assertEqual(waiting['evidence']['checking']['own_acknowledged'], 1)


class ExperimentTest(unittest.TestCase):
    def tearDown(self):
        from inquiry_lab import eat_oracle_policy
        eat_oracle_policy.uninstall()
        eat_ack_policy.uninstall()

    def test_run_case_and_selection(self):
        result = experiment_ack.run_case(('mix', 950002, experiment_ack.DEV_ARMS))
        self.assertEqual(set(result['branches']), {experiment_ack.arm_name(*a) for a in experiment_ack.DEV_ARMS})
        self.assertIn(result['ack_rate'], ack_environment.ACK_RATES)

        def m(J, failed, inq, work):
            return dict(J=J, failed=failed, inquiries_per_patient=inq, review_work_seconds_per_patient=work)
        metrics = {'fixed_3|on': m(950, 20, 6.0, 360), 'eat_l15_f|off': m(900, 15, 5.0, 320),
                   'eatc_k5|on': m(880, 15, 5.1, 330), 'eatc_k20|on': m(880, 15, 4.9, 310)}
        self.assertEqual(experiment_ack.select(metrics)['arm'], 'eatc_k5|on')     # 동률이면 작은 κ
        metrics['eatc_k5|on'] = m(905, 15, 5.1, 330)
        metrics['eatc_k20|on'] = m(890, 16, 4.9, 310)
        self.assertFalse(experiment_ack.select(metrics)['adopted_for_validation'])

    def test_seeds_are_new(self):
        from inquiry_lab import experiment_confirm, experiment_public
        used = (set(scenarios.DEVELOPMENT_MIX_SEEDS) | set(scenarios.VALIDATION_MIX_SEEDS)
                | set(experiment_confirm.DEV_MIX) | set(experiment_confirm.VAL_MIX)
                | set(experiment_public.DEV_MIX) | set(experiment_public.VAL_MIX) | set(experiment_voi.MIX))
        new = set(experiment_ack.DEV_MIX) | set(experiment_ack.VAL_MIX)
        self.assertFalse(new & used)
        self.assertFalse(set(experiment_ack.DEV_MIX) & set(experiment_ack.VAL_MIX))


if __name__ == '__main__':
    unittest.main()
