"""EAT 문의 정책 실험(inquiry_lab)의 계약·계산·환경·선택 규칙 검사."""
from copy import deepcopy
import json
import math
import statistics
import tempfile
import unittest
from pathlib import Path

from inquiry_lab import eat_policy, experiment, harness, scenarios
from server import inquiry_experiment, inquiry_policies


def candidate(hid, travel):
    return dict(hospital_id=hid, travel=dict(duration_seconds=travel), resources_enough=True)


CANDIDATES = [candidate('H01', 300), candidate('H02', 360), candidate('H03', 420), candidate('H04', 480)]


class PolicySpecTest(unittest.TestCase):
    def test_grid_names_parse_and_differ_only_in_cost_settings(self):
        grid = eat_policy.variant_grid()
        self.assertEqual(len(grid), 10)
        specs = [eat_policy.spec(name) for name in grid]
        self.assertEqual({s['config']['lambda_seconds'] for s in specs}, set(eat_policy.LAMBDA_GRID))
        for s in specs:
            self.assertIn('prior_reply_seconds', s['config'])   # 매 review 호출되는 관측형 계약
            self.assertEqual(s['deadline_seconds'], 300)
            self.assertEqual(s['max_contacts'], 10)

    def test_invalid_names_are_rejected(self):
        for name in ('fixed_3', 'eat_30', 'eat_lx_f', 'eat_l30_z', None):
            with self.assertRaises(ValueError):
                eat_policy.spec(name)


class DecideContractTest(unittest.TestCase):
    def decide(self, **kwargs):
        args = dict(now=1000, candidates=deepcopy(CANDIDATES), observations=[], pending=[],
                    patient_pending=[], max_new=10)
        args.update(kwargs)
        return eat_policy.decide('eat_l30_f', **args)

    def test_first_decision_sends_and_respects_limits(self):
        result = self.decide()
        self.assertGreaterEqual(len(result['send']), 1)
        self.assertLessEqual(len(result['send']), eat_policy.BASE_CONFIG['max_concurrent'])
        self.assertTrue(set(result['send']) <= {c['hospital_id'] for c in CANDIDATES})
        self.assertEqual(result['next_review_at'], 1015)
        limited = self.decide(max_new=1)
        self.assertEqual(len(limited['send']), 1)
        none = self.decide(max_new=0)
        self.assertEqual(none['send'], [])
        self.assertEqual(none['reason'], 'contact_budget')

    def test_inputs_are_not_mutated_and_output_is_deterministic(self):
        obs = [dict(hospital='H02', patient='X', submitted_at=900, observed_at=960, outcome='accept')]
        pending = [dict(hospital='H01', patient='P', submitted_at=990)]
        own = [dict(hospital='H01', patient='P', submitted_at=990, travel_seconds=300)]
        frozen = deepcopy((CANDIDATES, obs, pending, own))
        a = self.decide(candidates=CANDIDATES[1:], observations=obs, pending=pending, patient_pending=own)
        b = self.decide(candidates=CANDIDATES[1:], observations=obs, pending=pending, patient_pending=own)
        self.assertEqual(a, b)
        self.assertEqual(frozen, (CANDIDATES, obs, pending, own))

    def test_future_rows_are_ignored(self):
        base = self.decide()
        future = [dict(hospital='H01', patient='Z', submitted_at=1100, observed_at=1150, outcome='decline')]
        self.assertEqual(self.decide(observations=future)['send'], base['send'])

    def test_overdue_pending_inquiry_triggers_a_hedge(self):
        """같은 대기 문의라도 막 보낸 직후에는 기다리고, 오래 무응답이면 다음 후보를 문의한다."""
        # 수용 이력이 많아 수용률이 높게 추정되는 상황
        obs = [dict(hospital=f'H0{i}', patient=f'O{i}{k}', submitted_at=100 + k * 70,
                    observed_at=130 + k * 70, outcome='accept') for i in (1, 2, 3, 4) for k in range(3)]
        fresh = dict(hospital='H01', patient='P', submitted_at=995)
        stale = dict(hospital='H01', patient='P', submitted_at=880)
        waiting = eat_policy.decide('eat_l30_f', now=1000, candidates=CANDIDATES[1:], observations=obs,
                                    pending=[fresh], patient_pending=[dict(fresh, travel_seconds=300)],
                                    max_new=9)
        overdue = eat_policy.decide('eat_l30_f', now=1000, candidates=CANDIDATES[1:], observations=obs,
                                    pending=[stale], patient_pending=[dict(stale, travel_seconds=300)],
                                    max_new=9)
        self.assertEqual(waiting['send'], [])
        self.assertEqual(waiting['reason'], 'eat_wait')
        self.assertGreaterEqual(len(overdue['send']), 1)

    def test_rejects_pending_outside_ledger(self):
        with self.assertRaises(ValueError):
            self.decide(pending=[], patient_pending=[dict(hospital='H01', patient='P', submitted_at=990,
                                                          travel_seconds=300)])


class CostModelTest(unittest.TestCase):
    def test_expected_cost_basics(self):
        cells, step, horizon, penalty = 60, 5, 300, 1500
        self.assertAlmostEqual(eat_policy.expected_cost([], cells, step, horizon, penalty), horizon + penalty)
        sure = (300, [1.0] + [0.0] * (cells - 1))           # 첫 구간에 확실히 수용
        self.assertAlmostEqual(eat_policy.expected_cost([sure], cells, step, horizon, penalty), 302.5)
        late = eat_policy._shift(sure, 6)
        self.assertGreater(eat_policy.expected_cost([late], cells, step, horizon, penalty), 302.5)
        far = (600, [1.0] + [0.0] * (cells - 1))
        # 같은 구간에 둘 다 수용하면 가까운 병원을 고른다고 본다.
        self.assertAlmostEqual(eat_policy.expected_cost([far, sure], cells, step, horizon, penalty), 302.5)
        half = (300, [0.5] + [0.0] * (cells - 1))
        value = eat_policy.expected_cost([half], cells, step, horizon, penalty)
        self.assertAlmostEqual(value, 0.5 * 302.5 + 0.5 * (horizon + penalty))

    def test_beliefs_learn_and_forget(self):
        config = eat_policy.spec('eat_l30_f')['config']
        empty = eat_policy.Beliefs([], 10000, config)
        self.assertAlmostEqual(empty.reply_global, eat_policy.PRIORS['reply_probability'])
        timeouts = [dict(hospital='H01', submitted_at=9000 + i, observed_at=9180 + i, outcome='timeout')
                    for i in range(5)]
        self.assertLess(eat_policy.Beliefs(timeouts, 10000, config).reply('H01'), empty.reply('H01'))
        accepts = [dict(hospital='H01', submitted_at=9000 + i * 100, observed_at=9020 + i * 100, outcome='accept')
                   for i in range(5)]
        recent = eat_policy.Beliefs(accepts, 9500, config).accept('H01')
        old = eat_policy.Beliefs(accepts, 9500 + 3600, config).accept('H01')
        self.assertGreater(recent, old)
        self.assertGreater(old, empty.accept('H01') - 1e-9)


class ScenarioTest(unittest.TestCase):
    def test_mix_is_valid_deterministic_and_unseen(self):
        a, b = scenarios.make_mix(810001), scenarios.make_mix(810001)
        self.assertEqual(a, b)
        for seed in list(scenarios.DEVELOPMENT_MIX_SEEDS[:20]) + list(scenarios.VALIDATION_MIX_SEEDS[:20]):
            inquiry_experiment.validate(scenarios.make_mix(seed))
        with self.assertRaises(ValueError):
            scenarios.make_mix(7101)
        with self.assertRaises(ValueError):
            scenarios.make_family_suite((6101,))
        dev = set(scenarios.DEVELOPMENT_MIX_SEEDS) | set(scenarios.DEVELOPMENT_FAMILY_SEEDS)
        val = set(scenarios.VALIDATION_MIX_SEEDS) | set(scenarios.VALIDATION_FAMILY_SEEDS)
        self.assertFalse(dev & val)

    def test_mix_matches_public_calibration_targets(self):
        silent, services = [], []
        for seed in scenarios.DEVELOPMENT_MIX_SEEDS[:120]:
            for p in scenarios.make_mix(seed)['potentials']:
                silent.append(p['no_response'])
                services.append(p['service_seconds'])
        rate = sum(silent) / len(silent)
        self.assertAlmostEqual(rate, 0.207, delta=0.04)      # 충북 무응답 20.7%
        self.assertAlmostEqual(statistics.median(services), 66, delta=12)   # 회신 중앙 1.1분

    def test_oracle_uses_only_responding_accepting_hospitals(self):
        s = scenarios.make_mix(810007)
        oracle = scenarios.oracle(s)['arrival_seconds']
        pot = {(p['patient_id'], p['hospital_id']): p for p in s['potentials']}
        for patient in s['patients']:
            ok = [pot[(patient['id'], h)]['service_seconds'] + t for h, t in patient['travel_seconds'].items()
                  if pot[(patient['id'], h)]['accept'] and not pot[(patient['id'], h)]['no_response']]
            self.assertEqual(oracle[patient['id']], min(ok) if ok else None)


class HarnessTest(unittest.TestCase):
    def tearDown(self):
        harness.uninstall()

    def test_uninstall_restores_registry(self):
        harness.install(['eat_l15_f'])
        self.assertEqual(inquiry_policies.spec('eat_l15_f')['name'], 'eat_l15_f')
        harness.uninstall()
        with self.assertRaises(ValueError):
            inquiry_policies.spec('eat_l15_f')
        with self.assertRaises(ValueError):
            inquiry_policies.spec('observed_v4')

    def test_eat_runs_in_real_server_workflow_and_respects_oracle(self):
        policies = ('fixed_3', 'v3_w45_h900', 'observed_v4', 'eat_l30_f')
        for kind, seed in (('family:no_response_near', 830001), ('family:delayed_near_fast_far', 830002),
                           ('mix', 810011)):
            result = harness.run_case((kind, seed, policies))
            self.assertEqual(set(result['branches']), set(policies))
            if result['oracle']['exact']:
                best = result['oracle']['arrival_seconds']['P01']
                for policy in policies:
                    p = result['branches'][policy]['patients'][0]
                    if best is not None and not p['failed']:
                        self.assertGreaterEqual(p['estimated_arrival_seconds'], best)
        # 등록은 이 프로세스에만 적용되고 원래 정책 목록은 그대로다.
        self.assertNotIn('eat_l30_f', inquiry_policies.POLICY_NAMES)

    def test_patient_cost(self):
        self.assertEqual(harness.patient_cost(dict(failed=True), 1500), 1800)
        self.assertEqual(harness.patient_cost(dict(failed=False, estimated_arrival_seconds=420)), 420)


class ExperimentRuleTest(unittest.TestCase):
    def test_selection_prefers_feasible_lowest_cost(self):
        m = {'fixed_3': dict(J=900, failed=10, inquiries_per_patient=5.0),
             'eat_a': dict(J=800, failed=11, inquiries_per_patient=4.0),     # 실패가 많아 제외
             'eat_b': dict(J=850, failed=9, inquiries_per_patient=4.5),
             'eat_c': dict(J=850, failed=9, inquiries_per_patient=4.0)}
        chosen = experiment.select(m, ('eat_a', 'eat_b', 'eat_c'))
        self.assertEqual(chosen['policy'], 'eat_c')
        self.assertTrue(chosen['constraints_met'])
        none = experiment.select({'fixed_3': m['fixed_3'], 'eat_a': m['eat_a']}, ('eat_a',))
        self.assertEqual(none['policy'], 'eat_a')
        self.assertFalse(none['constraints_met'])

    def test_paired_bootstrap_interval_contains_mean(self):
        def scenario(costs_a, costs_b):
            pa = [dict(id=f'P{i}', failed=False, estimated_arrival_seconds=c) for i, c in enumerate(costs_a)]
            pb = [dict(id=f'P{i}', failed=False, estimated_arrival_seconds=c) for i, c in enumerate(costs_b)]
            return dict(branches=dict(a=dict(patients=pa), b=dict(patients=pb)))
        results = [scenario([400, 410], [420, 415]), scenario([500], [500]), scenario([300], [330])]
        row = experiment.paired(results, 'a', 'b')
        self.assertAlmostEqual(row['delta_J_mean'], (-20 - 5 + 0 - 30) / 4)
        self.assertLessEqual(row['ci95'][0], row['delta_J_mean'])
        self.assertGreaterEqual(row['ci95'][1], row['delta_J_mean'])
        self.assertEqual(row['patients'], dict(better=3, same=1, worse=0))

    def test_relative_path_accepts_relative_and_absolute(self):
        rel = experiment.relative_path(Path('simulation_results') / 'x.json')
        self.assertEqual(rel, 'simulation_results/x.json')
        self.assertEqual(experiment.relative_path(experiment.ROOT / 'simulation_results' / 'x.json'), rel)

    def test_validation_refuses_changed_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            lock = dict(source_sha256={'inquiry_lab/eat_policy.py': '0' * 64},
                        development_report='x', development_report_sha256='0' * 64,
                        selected_policy='eat_l30_f')
            path = Path(directory) / 'lock.json'
            path.write_text(json.dumps(lock), encoding='utf-8')
            with self.assertRaises(RuntimeError):
                experiment.validation(path, Path(directory) / 'out', workers=1)


if __name__ == '__main__':
    unittest.main()
