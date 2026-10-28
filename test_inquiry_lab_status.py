"""5차 응대 가능 상태 표시 실험: 상태 기반 무응답 환경·표시 신호·EAT-R 검사."""
from copy import deepcopy
import unittest

from inquiry_lab import eat_policy, eat_status_policy, experiment_status, harness, scenarios, status_signal
from server import inquiry_experiment


class StateEnvironmentTest(unittest.TestCase):
    def test_only_silence_changes_and_follows_state(self):
        silent, total = 0, 0
        for seed in experiment_status.DEV_MIX[:120]:
            base = scenarios.make_mix(seed)
            state, states = status_signal.make_state_mix(seed)
            inquiry_experiment.validate(state)
            a = {(p['patient_id'], p['hospital_id']): p for p in base['potentials']}
            for p in state['potentials']:
                q = a[(p['patient_id'], p['hospital_id'])]
                self.assertEqual((p['service_seconds'], p['accept']), (q['service_seconds'], q['accept']))
                if states[p['hospital_id']] == 'off':
                    self.assertTrue(p['no_response'])
                silent += p['no_response']
                total += 1
            self.assertEqual([x['travel_seconds'] for x in base['patients']], [x['travel_seconds'] for x in state['patients']])
        self.assertAlmostEqual(silent / total, 0.207, delta=0.05)

    def test_status_signal_qualities(self):
        seen, agree, flipped = set(), [0, 0], [0, 0]
        for seed in experiment_status.DEV_MIX:
            _, states = status_signal.make_state_mix(seed)
            sig = status_signal.status_signal(seed, states)
            self.assertEqual(sig, status_signal.status_signal(seed, states))
            seen.add(sig['quality'])
            if sig['quality'] == 'absent':
                self.assertEqual(sig['hospitals'], {})
            for hid, row in sig['hospitals'].items():
                truth = 'available' if states[hid] == 'on' else 'unavailable'
                bucket = agree if row['kind'] == 'accurate' else flipped if row['kind'] == 'noisy' else None
                if bucket is not None:
                    bucket[0] += row['status'] == truth
                    bucket[1] += 1
            self.assertTrue(all(set(v) == {'status', 'kind'} for v in sig['hospitals'].values()))
            self.assertTrue(all(v in ('available', 'unavailable') for v in status_signal.policy_view(sig).values()))
        self.assertEqual(seen, set(status_signal.QUALITIES))
        self.assertEqual(agree[0], agree[1])                               # 정확한 표시는 항상 일치
        self.assertAlmostEqual(1 - flipped[0] / flipped[1], status_signal.NOISY_FLIP, delta=0.08)

    def test_calibration_separates_statuses(self):
        cal = status_signal.calibration(experiment_status.DEV_MIX[:80])
        for source in ('pooled', 'trusted'):
            self.assertGreater(cal[source]['available']['reply_probability'],
                               cal[source]['unavailable']['reply_probability'] + 0.3)
        # 정확한 표시만 보면 '응대 불가'는 거의 회신하지 않는다(잔여 무응답만 남음).
        self.assertLess(cal['trusted']['unavailable']['reply_probability'], 0.1)
        self.assertLess(cal['trusted']['unavailable']['reply_probability'],
                        cal['pooled']['unavailable']['reply_probability'])


class EatRTest(unittest.TestCase):
    NOW = 6000

    def tearDown(self):
        eat_status_policy.clear_context()

    def inputs(self, variant):
        cands = [dict(hospital_id=f'H0{i}', travel=dict(duration_seconds=300 + 40 * i), resources_enough=True)
                 for i in range(1, 7)]
        if variant == 0:
            return dict(now=self.NOW, candidates=cands, max_new=10)
        own = [dict(hospital='H01', patient='P01', submitted_at=self.NOW - 30, travel_seconds=340)]
        pending = [dict(hospital='H01', patient='P01', submitted_at=self.NOW - 30)]
        return dict(now=self.NOW, candidates=cands[1:], pending=pending, patient_pending=own, max_new=9,
                    last_contact_at=self.NOW - 30)

    def test_without_status_matches_locked_eat(self):
        for variant in (0, 1):
            ours = eat_status_policy.decide('eatr_s4', **deepcopy(self.inputs(variant)))
            theirs = eat_policy.decide('eat_l15_f', **deepcopy(self.inputs(variant)))
            self.assertEqual(ours['send'], theirs['send'])
            self.assertEqual(ours['reason'], theirs['reason'])
            a, b = dict(ours['evidence']), dict(theirs['evidence'])
            for key in ('policy', 'status_signal'):
                a.pop(key, None), b.pop(key, None)
            self.assertEqual(a, b)

    def test_unavailable_status_lowers_reply_probability(self):
        table = {'available': dict(reply_probability=0.95), 'unavailable': dict(reply_probability=0.15)}
        trusted = {'available': dict(reply_probability=0.97), 'unavailable': dict(reply_probability=0.02)}
        eat_status_policy.set_context({'H01': 'unavailable', 'H02': 'available'}, dict(pooled=table, trusted=trusted))
        out = eat_status_policy.decide('eatr_s16', **self.inputs(0))
        plan = {row['hospital']: row for row in out['evidence']['candidate_plan']}
        self.assertLess(plan['H01']['reply_probability'], 0.4)
        self.assertGreater(plan['H02']['reply_probability'], 0.85)
        self.assertNotIn('H01', out['send'])
        trust = eat_status_policy.decide('eatr_t16', **self.inputs(0))
        tplan = {row['hospital']: row for row in trust['evidence']['candidate_plan']}
        self.assertLess(tplan['H01']['reply_probability'], plan['H01']['reply_probability'])
        for bad in ('eatr_x4', 'eatr_s0', 'eatr', None):
            with self.assertRaises(ValueError):
                eat_status_policy.spec(bad)


class IntegrationTest(unittest.TestCase):
    def tearDown(self):
        from inquiry_lab import eat_oracle_policy
        eat_oracle_policy.uninstall()
        eat_status_policy.uninstall()

    def test_run_case_keeps_other_branches(self):
        cal = status_signal.calibration(experiment_status.DEV_MIX[:40])
        result = experiment_status.run_case(('state', 990004, experiment_status.DEV_POLICIES, cal))
        self.assertEqual(set(result['branches']), set(experiment_status.DEV_POLICIES))
        scenario, _ = status_signal.make_state_mix(990004)
        harness.install(['eat_l15_f'])
        plain = inquiry_experiment.run_scenario(scenario, policies=('fixed_3', 'eat_l15_f'))
        for b in plain['branches']:
            self.assertEqual(result['branches'][b['policy']]['patients'],
                             [dict(id=p['id'], created_at=p['created_at'], failed=p['failed'],
                                   selection_seconds=p['selection_seconds'],
                                   estimated_arrival_seconds=p['estimated_arrival_seconds'],
                                   travel_seconds=p['travel_seconds'], destination=p['destination_hospital_id'],
                                   inquiry_count=p['inquiry_count'], contacted=p['contacted_hospitals'])
                              for p in b['patients']])

    def test_selection_and_seeds(self):
        def m(J, failed, inq, work):
            return dict(J=J, failed=failed, inquiries_per_patient=inq, review_work_seconds_per_patient=work)
        metrics = {'fixed_3': m(950, 20, 6.0, 360), 'eat_l15_f': m(900, 15, 5.0, 320),
                   'eatr_s4': m(880, 15, 4.8, 300), 'eatr_s16': m(880, 14, 4.6, 290), 'eatr_t16': m(880, 14, 4.5, 280),
                   'eato_silence': m(800, 9, 4.0, 250)}
        self.assertEqual(experiment_status.select(metrics)['policy'], 'eatr_s4')   # 동률이면 pooled·약한 강도
        metrics['eatr_s4'] = m(901, 15, 4.8, 300)
        metrics['eatr_s16'] = m(890, 16, 4.6, 290)
        metrics['eatr_t16'] = m(895, 15, 6.5, 280)        # 문의가 fixed_3 초과
        self.assertFalse(experiment_status.select(metrics)['adopted_for_validation'])
        from inquiry_lab import experiment_ack, experiment_confirm, experiment_public, experiment_voi
        used = (set(scenarios.DEVELOPMENT_MIX_SEEDS) | set(scenarios.VALIDATION_MIX_SEEDS)
                | set(experiment_confirm.DEV_MIX) | set(experiment_confirm.VAL_MIX)
                | set(experiment_public.DEV_MIX) | set(experiment_public.VAL_MIX) | set(experiment_voi.MIX)
                | set(experiment_ack.DEV_MIX) | set(experiment_ack.VAL_MIX))
        new = set(experiment_status.DEV_MIX) | set(experiment_status.VAL_MIX)
        self.assertFalse(new & used)
        self.assertFalse(set(experiment_status.DEV_MIX) & set(experiment_status.VAL_MIX))


if __name__ == '__main__':
    unittest.main()
