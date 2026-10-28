"""6차 같은 J에서 문의 줄이기: 부담 비교·파레토·선택·판정 규칙 검사."""
import unittest

from inquiry_lab import eat_policy, experiment as base, experiment_burden as eb, scenarios


def scenario(rows):
    """rows: [(정책, [(실패, 도착, 문의)...], 검토업무)]"""
    branches = {}
    for policy, patients, work in rows:
        branches[policy] = dict(review_work_seconds=work, cancelled_before_start_count=0,
                                patients=[dict(id=f'P{i}', failed=f, estimated_arrival_seconds=a, inquiry_count=q,
                                               selection_seconds=10)
                                          for i, (f, a, q) in enumerate(patients)])
    return dict(kind='mix', branches=branches, oracle=dict(exact=False, arrival_seconds={}))


class BurdenTest(unittest.TestCase):
    def test_paired_burden_mean_and_interval(self):
        results = [scenario([('a', [(False, 400, 3), (False, 410, 2)], 100), ('b', [(False, 400, 5), (False, 410, 4)], 160)]),
                   scenario([('a', [(False, 500, 4)], 80), ('b', [(False, 500, 4)], 90)])]
        row = eb.paired_burden(results, 'a', 'b', 'inquiry_count')
        self.assertAlmostEqual(row['delta_mean'], (-2 - 2 + 0) / 3)
        self.assertLessEqual(row['ci95'][0], row['delta_mean'])
        self.assertGreaterEqual(row['ci95'][1], row['delta_mean'])
        work = eb.paired_burden(results, 'a', 'b', 'review_work_seconds')
        self.assertAlmostEqual(work['delta_mean'], (-60 - 10) / 3)

    def test_frontier_marks_dominated(self):
        m = {'x': dict(J=900, inquiries_per_patient=5.0, review_work_seconds_per_patient=300),
             'y': dict(J=890, inquiries_per_patient=4.5, review_work_seconds_per_patient=280),
             'z': dict(J=880, inquiries_per_patient=6.0, review_work_seconds_per_patient=350)}
        fr = eb.frontier(m, ['x', 'y', 'z'])
        self.assertEqual(fr['x']['dominated_by'], ['y'])
        self.assertEqual(fr['y']['dominated_by'], [])
        self.assertEqual(fr['z']['dominated_by'], [])

    def test_selection_and_verdict(self):
        def j(policy, delta, ci=(0, 0)):
            return dict(policy=policy, baseline=eb.BASE_POLICY, penalty=base.PRIMARY_PENALTY, delta_J_mean=delta, ci95=list(ci))

        def q(policy, delta, ci=(0, 0)):
            return dict(policy=policy, baseline=eb.BASE_POLICY, key='inquiry_count', delta_mean=delta, ci95=list(ci))
        report = dict(paired=[j('eat_l25_f', 1.0), j('eat_l35_f', 2.0), j('eat_l45_f', 3.0), j('eat_l60_f', 9.0)],
                      paired_burden=[q('eat_l25_f', -0.2), q('eat_l35_f', -0.4), q('eat_l45_f', -0.6), q('eat_l60_f', -0.9)],
                      mix=dict(metrics={'eat_l25_f': dict(inquiries_per_patient=4.8), 'eat_l35_f': dict(inquiries_per_patient=4.6),
                                        'eat_l45_f': dict(inquiries_per_patient=4.4), 'eat_l60_f': dict(inquiries_per_patient=4.1)}))
        self.assertEqual(eb.select(report)['policy'], 'eat_l35_f')     # λ45·60은 ΔJ가 δ/2 초과
        report['paired'] = [j(p, 4.0) for p in eb.CANDIDATES]
        self.assertFalse(eb.select(report)['adopted_for_validation'])
        report = dict(paired=[j('eat_l35_f', 1.5, (-2.0, 4.9))], paired_burden=[q('eat_l35_f', -0.4, (-0.6, -0.2))])
        self.assertTrue(eb.verdict(report, 'eat_l35_f')['passed'])
        report = dict(paired=[j('eat_l35_f', 1.5, (-2.0, 5.1))], paired_burden=[q('eat_l35_f', -0.4, (-0.6, -0.2))])
        self.assertFalse(eb.verdict(report, 'eat_l35_f')['passed'])

    def test_policies_and_seeds(self):
        lams = [eat_policy.spec(p)['config']['lambda_seconds'] for p in (eb.BASE_POLICY,) + eb.CANDIDATES + eb.CURVE]
        self.assertEqual(len(set(lams)), len(lams))
        from inquiry_lab import experiment_ack, experiment_confirm, experiment_public, experiment_status, experiment_voi
        used = (set(scenarios.DEVELOPMENT_MIX_SEEDS) | set(scenarios.VALIDATION_MIX_SEEDS)
                | set(experiment_confirm.DEV_MIX) | set(experiment_confirm.VAL_MIX)
                | set(experiment_public.DEV_MIX) | set(experiment_public.VAL_MIX) | set(experiment_voi.MIX)
                | set(experiment_ack.DEV_MIX) | set(experiment_ack.VAL_MIX)
                | set(experiment_status.DEV_MIX) | set(experiment_status.VAL_MIX))
        new = set(eb.DEV_MIX) | set(eb.VAL_MIX)
        self.assertFalse(new & used)
        self.assertFalse(set(eb.DEV_MIX) & set(eb.VAL_MIX))
        scenarios.make_mix(eb.DEV_MIX[0])
        scenarios.make_family_suite((eb.DEV_FAMILY[0],))


if __name__ == '__main__':
    unittest.main()
