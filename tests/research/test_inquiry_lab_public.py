"""3차 공개 병상 신호 실험: 신호 생성·보정식·EAT-P 동등성·선택 규칙 검사."""
from copy import deepcopy
import math
import random
import unittest

from inquiry_lab import (eat_policy, eat_public_policy, experiment_confirm, experiment_public, harness,
                         public_signal, scenarios)
from server import inquiry_experiment


def corr(xs, ys):
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return sxy / (sx * sy)


class SignalTest(unittest.TestCase):
    def test_hidden_parameters_reproduce_make_mix(self):
        for seed in list(experiment_public.DEV_MIX[:40]) + list(experiment_public.VAL_MIX[:20]):
            scenario = scenarios.make_mix(seed)
            hidden = public_signal.hidden_parameters(seed)
            self.assertEqual(hidden['level'], scenario['mix']['level'])
            self.assertEqual([h['id'] for h in scenario['hospitals']], list(hidden['hospitals']))
            # 처리 인원은 같은 병원 난수열의 네 번째 추출이다. 앞선 추출 순서가 같아야 일치한다.
            self.assertEqual([h['workers'] for h in scenario['hospitals']],
                             [hp['workers'] for hp in hidden['hospitals'].values()])

    def test_signal_quality_and_informativeness(self):
        fresh, noise, qualities, missing, total = ([], []), ([], []), set(), 0, 0
        for seed in experiment_public.DEV_MIX[:200]:
            hidden = public_signal.hidden_parameters(seed)
            signal = public_signal.mix_signal(seed)
            self.assertEqual(signal, public_signal.mix_signal(seed))
            qualities.add(signal['quality'])
            total += len(hidden['hospitals'])
            missing += len(hidden['hospitals']) - len(signal['hospitals'])
            for hid, row in signal['hospitals'].items():
                bucket = fresh if row['kind'] == 'fresh' else noise if row['kind'] == 'noise' else None
                if row['kind'] in ('fresh', 'noise'):
                    self.assertLessEqual(row['age_seconds'], public_signal.FRESH_MAX_AGE)
                else:
                    self.assertGreaterEqual(row['age_seconds'], public_signal.STALE_AGE[0])
                if bucket is not None:
                    bucket[0].append(row['beds'])
                    bucket[1].append(hidden['hospitals'][hid]['accept'])
        self.assertEqual(qualities, set(public_signal.QUALITIES))
        self.assertAlmostEqual(missing / total, public_signal.MISSING_PROBABILITY, delta=0.04)
        self.assertGreater(corr(*fresh), 0.6)
        self.assertLess(abs(corr(*noise)), 0.15)      # 무관 신호는 병원 수용과 상관이 없다

    def test_noise_signal_is_independent_of_regional_scarcity(self):
        beds = {}
        for seed in experiment_public.DEV_MIX:
            hidden = public_signal.hidden_parameters(seed)
            for row in public_signal.mix_signal(seed)['hospitals'].values():
                if row['kind'] == 'noise':
                    beds.setdefault(hidden['level'], []).append(row['beds'])
        means = [sum(v) / len(v) for v in beds.values()]
        self.assertEqual(len(means), 3)
        self.assertLess(max(means) - min(means), 1.2)   # 지역 혼잡(0.15~0.65 → 평균 병상 1.5~6.5)을 드러내지 않음

    def test_policy_view_hides_quality(self):
        signal = public_signal.mix_signal(890005)
        view = public_signal.policy_view(signal, 1000)
        for hid, row in view.items():
            self.assertEqual(set(row), {'beds', 'reported_at'})
            self.assertEqual(row['reported_at'], 1000 - signal['hospitals'][hid]['age_seconds'])

    def test_calibration_recovers_logistic(self):
        rng = random.Random(7)
        samples = []
        for _ in range(4000):
            beds = rng.randint(0, 15)
            p = public_signal.logistic(-1.5, 0.3, beds)
            samples.append((beds, 1 if rng.random() < p else 0))
        fit = public_signal.fit_calibration(samples)
        self.assertAlmostEqual(fit['beta0'], -1.5, delta=0.15)
        self.assertAlmostEqual(fit['beta1'], 0.3, delta=0.03)
        dev = public_signal.fit_calibration(public_signal.calibration_samples(experiment_public.DEV_MIX[:60]))
        self.assertGreater(dev['beta1'], 0)


class EatPublicPolicyTest(unittest.TestCase):
    NOW = 5000

    def tearDown(self):
        eat_public_policy.clear_context()

    def inputs(self, variant):
        cands = [dict(hospital_id=f'H0{i}', travel=dict(duration_seconds=300 + 50 * i), resources_enough=True)
                 for i in range(1, 7)]
        if variant == 0:
            return dict(now=self.NOW, candidates=cands, max_new=10)
        own = [dict(hospital='H01', patient='P', submitted_at=self.NOW - 40, travel_seconds=350)]
        obs = [dict(hospital='H02', patient='Z', submitted_at=self.NOW - 600, observed_at=self.NOW - 540,
                    outcome='accept'),
               dict(hospital='H03', patient='Y', submitted_at=self.NOW - 400, observed_at=self.NOW - 220,
                    outcome='timeout')]
        pending = [dict(hospital='H01', patient='P', submitted_at=self.NOW - 40),
                   dict(hospital='H04', patient='Q', submitted_at=self.NOW - 10)]
        return dict(now=self.NOW, candidates=cands[1:], observations=obs, pending=pending,
                    patient_pending=own, max_new=9, last_contact_at=self.NOW - 40)

    def test_without_signal_matches_locked_eat_exactly(self):
        eat_public_policy.clear_context()
        for variant in (0, 1):
            ours = eat_public_policy.decide('eatp_t900', **deepcopy(self.inputs(variant)))
            theirs = eat_policy.decide('eat_l15_f', **deepcopy(self.inputs(variant)))
            for key in ('send', 'reason', 'next_review_at'):
                self.assertEqual(ours[key], theirs[key])
            a, b = dict(ours['evidence']), dict(theirs['evidence'])
            for key in ('policy', 'public_signal'):
                a.pop(key, None), b.pop(key, None)
            self.assertEqual(a, b)

    def test_signal_changes_accept_estimates_and_ignores_future_reports(self):
        calibration = dict(beta0=-2.0, beta1=0.35, cap=15)
        signal = {'H01': dict(beds=12, reported_at=self.NOW - 60), 'H02': dict(beds=0, reported_at=self.NOW - 60),
                  'H03': dict(beds=12, reported_at=self.NOW + 60)}
        eat_public_policy.set_context(signal, calibration)
        out = eat_public_policy.decide('eatp_t900', **self.inputs(0))
        plan = {row['hospital']: row for row in out['evidence']['candidate_plan']}
        self.assertGreater(plan['H01']['accept_probability'], plan['H02']['accept_probability'])
        self.assertNotIn('H03', out['evidence']['public_signal'])
        # 오래된 보고일수록 공통 추정 쪽으로 돌아간다.
        eat_public_policy.set_context({'H01': dict(beds=12, reported_at=self.NOW - 20000)}, calibration)
        old = eat_public_policy.decide('eatp_t900', **self.inputs(0))
        old_plan = {row['hospital']: row for row in old['evidence']['candidate_plan']}
        self.assertLess(old_plan['H01']['accept_probability'], plan['H01']['accept_probability'])

    def test_oracle_reference_uses_true_accept_probabilities(self):
        eat_public_policy.set_context({}, None, oracle={'H01': 0.9, 'H02': 0.05})
        out = eat_public_policy.decide(eat_public_policy.ORACLE_NAME, **self.inputs(0))
        plan = {row['hospital']: row for row in out['evidence']['candidate_plan']}
        self.assertAlmostEqual(plan['H01']['accept_probability'], 0.9, places=2)
        self.assertAlmostEqual(plan['H02']['accept_probability'], 0.05, places=2)
        self.assertNotIn(eat_public_policy.ORACLE_NAME, experiment_public.CANDIDATES)

    def test_spec_names(self):
        self.assertEqual(eat_public_policy.spec('eatp_t900')['config']['signal_decay_seconds'], 900)
        for bad in ('eatp_900', 'eat_l15_f', 'eatp_tx', None):
            with self.assertRaises(ValueError):
                eat_public_policy.spec(bad)


class IntegrationTest(unittest.TestCase):
    def tearDown(self):
        eat_public_policy.uninstall()

    def test_signal_does_not_change_locked_eat_branch(self):
        calibration = public_signal.fit_calibration(public_signal.calibration_samples(experiment_public.DEV_MIX[:40]))
        task = ('mix', 890003, experiment_public.DEV_POLICIES, calibration)
        result = experiment_public.run_case(task)
        self.assertEqual(set(result['branches']), set(experiment_public.DEV_POLICIES))
        plain = harness.run_case(('mix', 890003, ('fixed_3', 'eat_l15_f')))
        for policy in ('fixed_3', 'eat_l15_f'):
            self.assertEqual(result['branches'][policy]['patients'], plain['branches'][policy]['patients'])
        self.assertIn(result['mix']['signal'], public_signal.QUALITIES)

    def test_selection_rule(self):
        def m(J, failed, inq, work):
            return dict(J=J, failed=failed, inquiries_per_patient=inq, review_work_seconds_per_patient=work)
        metrics = {'fixed_3': m(950, 20, 6.0, 360), 'eat_l15_f': m(900, 15, 5.0, 320),
                   'eatp_t900': m(890, 15, 5.1, 330), 'eatp_t2700': m(880, 16, 4.9, 310),   # 실패 증가로 제외
                   'eatp_t900_s16': m(890, 15, 5.0, 320), 'eatp_t2700_s16': m(870, 15, 6.5, 330),  # 문의 초과로 제외
                   'eatp_oracle': m(800, 10, 4.0, 300)}          # 비교 기준은 후보가 아니다
        self.assertEqual(experiment_public.select(metrics)['policy'], 'eatp_t900')   # 동률이면 약한 강도
        for name in ('eatp_t900', 'eatp_t900_s16'):
            metrics[name] = m(905, 15, 5.1, 330)
        self.assertFalse(experiment_public.select(metrics)['adopted_for_validation'])

    def test_seeds_are_new(self):
        new = (set(experiment_public.DEV_MIX) | set(experiment_public.VAL_MIX)
               | set(experiment_public.DEV_FAMILY) | set(experiment_public.VAL_FAMILY))
        old = (set(scenarios.DEVELOPMENT_MIX_SEEDS) | set(scenarios.VALIDATION_MIX_SEEDS)
               | set(scenarios.DEVELOPMENT_FAMILY_SEEDS) | set(scenarios.VALIDATION_FAMILY_SEEDS)
               | set(experiment_confirm.DEV_MIX) | set(experiment_confirm.VAL_MIX)
               | set(experiment_confirm.DEV_FAMILY) | set(experiment_confirm.VAL_FAMILY))
        self.assertFalse(new & old)
        self.assertFalse((set(experiment_public.DEV_MIX) | set(experiment_public.DEV_FAMILY))
                         & (set(experiment_public.VAL_MIX) | set(experiment_public.VAL_FAMILY)))


if __name__ == '__main__':
    unittest.main()
