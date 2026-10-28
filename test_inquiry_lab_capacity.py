"""8차 병원 문의 처리 용량 실험: 실행기 동등성·보조 담당 호출 규칙·선택 규칙·seed 검사."""
from copy import deepcopy
import unittest

from inquiry_lab import capacity_runner as cr, experiment_capacity as ec, harness, scenarios
from server import inquiry_experiment


def _seed(arrival, minimum_patients=1):
    return next(s for s in ec.DEV_MIX if scenarios.make_mix(s)['mix']['arrival'] == arrival
                and scenarios.make_mix(s)['mix']['patients'] >= minimum_patients)


def _strip(trace):
    return [{k: v for k, v in row.items() if k != 'backup'} for row in trace]


def _core(branch):
    m = branch['metrics']
    return branch['patients'], {k: m[k] for k in ('review_work_seconds', 'review_started_count',
                                                   'cancelled_before_start_count', 'reply_suppressed_count')}


class RunnerTest(unittest.TestCase):
    POLICIES = ('fixed_3', 'eat_l15_f')

    @classmethod
    def setUpClass(cls):
        harness.install(['eat_l15_f'])
        cls.burst = scenarios.make_mix(_seed('burst', 5))
        cls.stream = scenarios.make_mix(_seed('stream', 5))

    @classmethod
    def tearDownClass(cls):
        harness.uninstall()

    def test_rule_names(self):
        self.assertEqual(cr.parse_rule('od_k3_d60_l120'), dict(kind='od', k=3, delay=60, linger=120))
        self.assertEqual(cr.parse_rule('min2'), dict(kind='min2'))
        for bad in ('od_k0_d0_l0', 'od_k2_d60', 'two', None):
            with self.assertRaises(ValueError):
                cr.parse_rule(bad)
        self.assertEqual(len(ec.GRID), 21)
        self.assertEqual(len(set(ec.DEV_RULES)), 24)

    def test_base_matches_server_harness(self):
        for scenario in (self.burst, self.stream):
            server = inquiry_experiment.run_scenario(scenario, policies=self.POLICIES)
            ours = cr.run_scenario(scenario, [(p, 'base') for p in self.POLICIES])
            for a, b in zip(server['branches'], ours['branches']):
                self.assertEqual(_core(a), _core(b))
                self.assertEqual(a['trace'], b['trace'])
                self.assertEqual(a['decisions'], b['decisions'])
            self.assertEqual(server['environment_sha256'], ours['environment_sha256'])

    def test_min2_and_inf_match_server_with_more_workers(self):
        for rule, workers in (('min2', lambda w: max(2, w)), ('inf', lambda w: cr.INFINITE_WORKERS)):
            changed = deepcopy(self.burst)
            for h in changed['hospitals']:
                h['workers'] = workers(h['workers'])
            server = inquiry_experiment.run_scenario(changed, policies=self.POLICIES)
            ours = cr.run_scenario(self.burst, [(p, rule) for p in self.POLICIES])
            for a, b in zip(server['branches'], ours['branches']):
                self.assertEqual(_core(a), _core(b))
                self.assertEqual(a['trace'], _strip(b['trace']))

    def test_instant_backup_at_two_unanswered_equals_min2(self):
        for scenario in (self.burst, self.stream):
            out = cr.run_scenario(scenario, [('eat_l15_f', 'min2'), ('eat_l15_f', 'od_k2_d0_l0')])
            self.assertEqual(out['branches'][0]['patients'], out['branches'][1]['patients'])
            self.assertGreater(out['branches'][1]['backup']['calls'], 0)

    def test_backup_that_is_never_called_equals_base(self):
        single = scenarios.make_mix(_seed('single'))
        out = cr.run_scenario(self.burst, [('fixed_3', 'base'), ('fixed_3', 'od_k99_d60_l0')])
        self.assertEqual(out['branches'][0]['patients'], out['branches'][1]['patients'])
        self.assertEqual(out['branches'][1]['backup']['calls'], 0)
        lone = cr.run_scenario(single, [('fixed_3', 'od_k2_d0_l0')])
        self.assertEqual(lone['branches'][0]['backup']['calls'], 0)

    def test_call_arrival_release_accounting(self):
        rule = 'od_k3_d60_l120'
        branch = cr.run_scenario(self.burst, [('fixed_3', rule)])['branches'][0]
        calls, trace = branch['calls'], branch['trace']
        self.assertTrue(calls)
        one_worker = {h['id'] for h in self.burst['hospitals'] if h['workers'] == 1}
        for c in calls:
            self.assertIn(c['hospital_id'], one_worker)
            self.assertGreaterEqual(c['unanswered_at_call'], 3)
            self.assertEqual(c['arrive_at'], c['called_at'] + 60)
            self.assertGreaterEqual(c['released_at'], c['arrive_at'])
            self.assertLessEqual(c['busy_seconds'], c['released_at'] - c['called_at'])
            starts = [t for t in trace if t['type'] == 'review_started' and t.get('backup')
                      and t['hospital_id'] == c['hospital_id'] and c['arrive_at'] <= t['at'] < c['released_at']]
            self.assertEqual(len(starts), c['reviews'])
        backup_starts = [t for t in trace if t['type'] == 'review_started' and t.get('backup')]
        self.assertEqual(len(backup_starts), sum(c['reviews'] for c in calls))
        b = branch['backup']
        self.assertEqual(b['calls'], len(calls))
        self.assertEqual(b['on_duty_seconds'], sum(c['released_at'] - c['called_at'] for c in calls))
        self.assertGreaterEqual(b['on_duty_seconds'], b['busy_seconds'])


class SelectionTest(unittest.TestCase):
    def table(self, values):
        rows = {}
        for rule in ec.GRID:
            cap, duty = values.get(rule, (0.5, 10.0))
            rows[rule] = dict(capture=dict(capture=cap), backup=dict(on_duty_seconds_per_patient=duty))
        return rows

    def test_cheapest_rule_that_reaches_target(self):
        rows = self.table({'od_k2_d60_l0': (0.80, 30.0), 'od_k3_d60_l0': (0.76, 20.0),
                           'od_k4_d60_l120': (0.74, 5.0), 'od_k2_d0_l0': (1.0, 1.0)})
        s = ec.select(rows)
        self.assertEqual(s['rule'], 'od_k3_d60_l0')
        self.assertTrue(s['target_met'])
        self.assertEqual(set(s['feasible']), {'od_k2_d60_l0', 'od_k3_d60_l0'})

    def test_fallback_takes_highest_capture(self):
        rows = self.table({'od_k2_d60_l120': (0.70, 40.0), 'od_k2_d0_l0': (0.99, 1.0)})
        s = ec.select(rows)
        self.assertEqual(s['rule'], 'od_k2_d60_l120')
        self.assertFalse(s['target_met'])
        self.assertEqual(ec.sensitivity_rules('od_k2_d60_l120'), ('od_k2_d0_l120', 'od_k2_d30_l120', 'od_k2_d120_l120'))

    def test_new_seeds_were_never_used(self):
        from inquiry_lab import (experiment_ack, experiment_burden, experiment_confirm, experiment_coord,
                                 experiment_public, experiment_status, experiment_voi)
        used = (set(scenarios.DEVELOPMENT_MIX_SEEDS) | set(scenarios.VALIDATION_MIX_SEEDS)
                | set(scenarios.DEVELOPMENT_FAMILY_SEEDS) | set(scenarios.VALIDATION_FAMILY_SEEDS))
        for m in (experiment_confirm, experiment_public, experiment_ack, experiment_status, experiment_burden,
                  experiment_coord):
            used |= set(m.DEV_MIX) | set(m.VAL_MIX) | set(m.DEV_FAMILY) | set(m.VAL_FAMILY)
        used |= set(experiment_voi.MIX) | set(experiment_voi.FAMILY)
        new = set(ec.DEV_MIX) | set(ec.VAL_MIX) | set(ec.DEV_FAMILY) | set(ec.VAL_FAMILY)
        self.assertFalse(new & used)
        self.assertFalse(set(ec.DEV_MIX) & set(ec.VAL_MIX))
        self.assertFalse(any(lo <= s <= hi for s in new for lo, hi in scenarios.USED_SEED_RANGES))


if __name__ == '__main__':
    unittest.main()
