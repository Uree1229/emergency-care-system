"""Unit inputs for exhaustive executable batches; no performance replay.

Controlled cost tables below exercise optimizer boundaries only. They are not
formal experiments, development observations, or simulated performance data.
"""
import ast
from copy import deepcopy
import inspect
from itertools import combinations
import math
import unittest
from unittest.mock import patch

from inquiry_lab import residual_policy as old, batch_policy as new


def candidate(hospital, travel):
    return dict(hospital_id=hospital, travel=dict(duration_seconds=travel), resources_enough=True)


def inputs(n=8, **kwargs):
    result = dict(now=1000,
        candidates=[candidate(f'H{i:02}', 300+10*i) for i in range(1, n+1)],
        observations=[], pending=[], patient_pending=[], max_new=10)
    result.update(kwargs)
    return result


def with_own(count, age=5, **kwargs):
    rows = [dict(hospital=f'OWN{i}', patient='P', submitted_at=1000-age) for i in range(count)]
    return inputs(pending=rows,
        patient_pending=[dict(row, travel_seconds=9000+i) for i, row in enumerate(rows)], **kwargs)


def decide(**kwargs):
    return new.decide(new.NAME, **inputs(**kwargs))


def batch(result):
    return result['evidence']['batch']


def cost_table(values, default=500.):
    """Pure unit oracle for final batch cost, identifying rows by travel only."""
    def expected(items, *args):
        selected = tuple(sorted(travel for travel, _ in items if travel < 9000))
        return values.get(selected, default) - 15*len(selected)
    return expected


def direct_first_accept_cost(items, cells, step, horizon, failure):
    """Independent bin winner formula, without producer survival hazards."""
    ordered = sorted(items, key=lambda row: row[0])
    cumulative = [[sum(masses[:k]) for k in range(cells+1)] for _, masses in ordered]
    total = 0.
    for k in range(cells):
        for i, (travel, masses) in enumerate(ordered):
            win = masses[k]
            for j in range(len(ordered)):
                if j != i:
                    win *= 1.-cumulative[j][k+1 if j < i else k]
            total += win*((k+.5)*step+travel)
    none = math.prod(1.-cumulative[i][-1] for i in range(len(ordered)))
    return total+none*(horizon+failure)


class BatchOptimizerTests(unittest.TestCase):
    def test_all_57_current_subsets_are_recorded_once_in_pool_order(self):
        result = decide(n=10)
        trace = batch(result)
        pool = [f'H{i:02}' for i in range(1, 7)]
        expected = [list(c) for size in range(5) for c in combinations(pool, size)]
        self.assertEqual(trace['pool'], pool)
        self.assertEqual(trace['room'], 4)
        self.assertEqual(trace['maximum_subset_size'], 4)
        self.assertEqual(trace['evaluated_count'], 57)
        self.assertEqual([row['hospitals'] for row in trace['subsets']], expected)
        self.assertEqual(sum(row['selected_before_safety'] for row in trace['subsets']), 1)
        self.assertTrue(all(math.isfinite(row['cost']) for row in trace['subsets']))

    def test_every_subset_cost_matches_independent_bin_probability_oracle(self):
        args = inputs()
        result = new.decide(new.NAME, **args)
        trace = batch(result)
        step, cells, horizon = 5, 60, 300
        mu, sigma, chance = math.log(66), .7, .79*.40
        def cdf(x):
            return .5*math.erfc(-(math.log(x)-mu)/(sigma*math.sqrt(2))) if x > 0 else 0.
        masses = [(cdf(k*step)-cdf((k-1)*step))*chance for k in range(1, cells+1)]
        travel = {row['hospital_id']:row['travel']['duration_seconds'] for row in args['candidates']}
        for row in trace['subsets']:
            items = [(travel[h], masses) for h in row['hospitals']]
            expected = direct_first_accept_cost(items, cells, step, horizon, 1500)+15*len(items)
            self.assertTrue(math.isclose(row['cost'], expected, rel_tol=1e-12, abs_tol=1e-9))
        best = min(row['cost'] for row in trace['subsets'])
        expected = next(row for row in trace['subsets'] if row['cost'] <= best+1e-9)
        self.assertEqual(trace['minimum_cost'], best)
        self.assertEqual(trace['optimal_subset'], expected['hospitals'])
        self.assertEqual(trace['optimal_cost'], expected['cost'])

    def test_full_four_slot_batch_can_choose_fifth_candidate_immediately(self):
        target = (310, 320, 330, 350)
        with patch.object(new, 'expected_cost', side_effect=cost_table({target:10.})):
            result = decide()
        self.assertEqual(result['send'], ['H01', 'H02', 'H03', 'H05'])
        self.assertFalse(batch(result)['forced'])
        self.assertEqual(batch(result)['selected_cost'], 10.)
        self.assertEqual([row['planned_offset_seconds'] for row in result['evidence']['candidate_plan']],
            [0, 0, 0, None, 0, None])

    def test_jointly_beneficial_pair_is_not_lost_by_coordinate_local_minimum(self):
        values = {():100., (310,):110., (320,):110., (310, 320):20.}
        with patch.object(new, 'expected_cost', side_effect=cost_table(values)):
            result = decide(n=2)
        self.assertEqual(result['send'], ['H01', 'H02'])
        self.assertEqual(batch(result)['optimal_cost'], 20.)

    def test_ties_choose_fewer_inquiries_then_original_pool_combination(self):
        args = with_own(1, n=3)
        args['candidates'] = [candidate('Z', 330), candidate('A', 310), candidate('M', 320)]
        values = {():200., (310,):10., (320,):10., (330,):10.,
            (310, 330):10., (310, 320, 330):10.}
        with patch.object(new, 'expected_cost', side_effect=cost_table(values)):
            result = new.decide(new.NAME, **args)
        self.assertEqual(result['send'], ['Z'])
        self.assertEqual(batch(result)['optimal_subset'], ['Z'])

    def test_epsilon_ties_are_measured_from_global_minimum_not_a_chain(self):
        values = {():200., (310,):100.+1.5e-9,
            (320,):100.+.75e-9, (330,):100.}
        with patch.object(new, 'expected_cost', side_effect=cost_table(values)):
            result = new.decide(new.NAME, **with_own(1, n=3))
        trace = batch(result)
        self.assertEqual(trace['minimum_cost'], 100.)
        self.assertEqual(trace['optimal_subset'], ['H02'])
        self.assertLessEqual(trace['optimal_cost']-trace['minimum_cost'], 1e-9)

    def test_more_than_epsilon_improvement_beats_earlier_or_smaller_subset(self):
        values = {():100.+2e-9, (310,):100.}
        with patch.object(new, 'expected_cost', side_effect=cost_table(values)):
            result = new.decide(new.NAME, **with_own(1, n=1))
        self.assertEqual(batch(result)['optimal_subset'], ['H01'])
        self.assertFalse(batch(result)['forced'])

    def test_empty_batch_tie_is_preserved_while_a_fresh_inquiry_exists(self):
        values = {():100., (310,):100.-.5e-9}
        with patch.object(new, 'expected_cost', side_effect=cost_table(values)):
            result = new.decide(new.NAME, **with_own(1, n=1))
        self.assertEqual(result['send'], [])
        self.assertEqual(result['reason'], 'eat_wait')
        self.assertEqual(batch(result)['optimal_subset'], [])
        self.assertFalse(batch(result)['forced'])

    def test_force_one_keeps_original_singleton_cost_then_travel_tie_rule(self):
        args = inputs(n=3)
        args['candidates'] = [candidate('Z', 330), candidate('A', 310), candidate('M', 320)]
        values = {():100., (310,):110., (320,):110., (330,):110.}
        with patch.object(new, 'expected_cost', side_effect=cost_table(values)):
            result = new.decide(new.NAME, **args)
        trace = batch(result)
        self.assertEqual(trace['optimal_subset'], [])
        self.assertEqual(trace['optimal_cost'], 100.)
        self.assertTrue(trace['subsets'][0]['selected_before_safety'])
        self.assertEqual(result['send'], ['A'])
        self.assertEqual(trace['safety_hospital'], 'A')
        self.assertEqual(trace['selected_cost'], 110.)
        self.assertTrue(trace['forced'])

    def test_force_one_exact_cost_and_travel_tie_preserves_pool_order(self):
        args = inputs(n=2, candidates=[candidate('Z', 310), candidate('A', 310)])
        with patch.object(new, 'expected_cost', side_effect=cost_table({():100., (310,):110.})):
            result = new.decide(new.NAME, **args)
        self.assertEqual(result['send'], ['Z'])
        self.assertTrue(batch(result)['forced'])

    def test_selected_batch_and_candidate_plan_have_no_future_offsets(self):
        rows = [dict(hospital='H01', patient='OTHER', submitted_at=800)]
        result = decide(pending=rows)
        trace = batch(result)
        self.assertEqual(trace['selected'], result['send'])
        self.assertEqual(result['evidence']['sent_hospitals'], result['send'])
        self.assertEqual(result['evidence']['planned_expected_cost'], round(trace['selected_cost'], 3))
        plan = result['evidence']['candidate_plan']
        self.assertTrue(all(row['planned_offset_seconds'] in (0, None) for row in plan))
        self.assertEqual([row['hospital'] for row in plan if row['planned_offset_seconds']==0], result['send'])

    def test_fresh_pending_consumes_room_and_full_cap_never_sends(self):
        for count in range(5):
            result = new.decide(new.NAME, **with_own(count))
            trace = batch(result)
            self.assertEqual(trace['room'], 4-count)
            self.assertEqual(trace['maximum_subset_size'], 4-count)
            self.assertLessEqual(len(result['send']), 4-count)
            self.assertTrue(all(len(row['hospitals']) <= 4-count for row in trace['subsets']))
        self.assertEqual(result['reason'], 'eat_concurrency_cap')
        self.assertEqual(trace['evaluated_count'], 1)
        self.assertEqual(result['send'], [])

    def test_180_second_boundary_keeps_original_fresh_count_rule(self):
        for age, expected in ((180, 0), (181, 4)):
            result = new.decide(new.NAME, **with_own(4, age=age))
            self.assertEqual(batch(result)['room'], expected)
            self.assertEqual(result['evidence']['own_waiting_count'], 4 if age==180 else 0)
            self.assertEqual(result['evidence']['own_observation_timed_out_count'], 0 if age==180 else 4)

    def test_overfull_visible_pending_keeps_raw_negative_room_and_only_empty(self):
        result = new.decide(new.NAME, **with_own(5))
        trace = batch(result)
        self.assertEqual(trace['room'], -1)
        self.assertEqual(trace['maximum_subset_size'], 0)
        self.assertEqual(trace['evaluated_count'], 1)
        self.assertEqual(trace['subsets'][0]['hospitals'], [])
        self.assertEqual(result['send'], [])
        self.assertFalse(trace['forced'])

    def test_contact_limit_and_empty_pool_always_have_one_empty_subset(self):
        for n, limit, reason in ((0,10,'no_candidates'), (8,0,'contact_budget')):
            result = decide(n=n, max_new=limit)
            trace = batch(result)
            self.assertEqual(result['reason'], reason)
            self.assertEqual(trace['pool'], [])
            self.assertEqual(trace['evaluated_count'], 1)
            self.assertEqual(trace['optimal_subset'], [])
            self.assertEqual(trace['selected'], [])
            self.assertFalse(trace['forced'])
            self.assertIsNone(trace['safety_hospital'])

    def test_small_contact_budget_limits_pool_and_number_sent(self):
        for limit in (1, 2, 3):
            result = decide(n=10, max_new=limit)
            trace = batch(result)
            self.assertEqual(trace['room'], limit)
            self.assertEqual(len(trace['pool']), limit)
            self.assertEqual(trace['evaluated_count'], 2**limit)
            self.assertLessEqual(len(result['send']), limit)


class BatchSingleFactorTests(unittest.TestCase):
    def test_spec_changes_identity_only_and_returns_detached_config(self):
        before = old.spec(old.NAME)
        after = new.spec(new.NAME)
        for key in ('name', 'label'):
            before.pop(key); after.pop(key)
        self.assertEqual(before, after)
        after['config']['lambda_seconds'] = 999
        self.assertEqual(new.spec()['config']['lambda_seconds'], 15)
        for name in (None, old.NAME, 'eat_batch_l30_f'):
            with self.assertRaises(ValueError): new.spec(name)

    def test_beliefs_probability_residual_and_pending_helpers_are_frozen_objects(self):
        for name in ('Beliefs', 'expected_cost', '_lognorm_cdf', '_masses',
                '_no_accept_by', '_shift', '_queue_wait_estimate'):
            self.assertIs(getattr(new, name), getattr(old, name))
        self.assertEqual(new.RESIDUAL_RULE, old.RESIDUAL_RULE)

    def test_original_current_cost_horizon_and_residual_estimates_are_unchanged(self):
        args = with_own(2)
        args['pending'] += [dict(hospital='H01', patient='OTHER', submitted_at=800)]
        args['observations'] += [dict(hospital='H01', submitted_at=700, observed_at=900, outcome='decline')]
        args['last_contact_at'] = 850
        before = old.decide(old.NAME, **args)['evidence']
        after = new.decide(new.NAME, **args)['evidence']
        for key in ('horizon_seconds', 'expected_cost_without_new', 'residual_queue',
                'reply_probability_global', 'accept_probability_global', 'service_median_seconds',
                'history_used_count', 'own_waiting_count', 'own_observation_timed_out_count',
                'unresolved_inquiry_count', 'lambda_seconds', 'congestion_aware'):
            self.assertEqual(before[key], after[key])
        self.assertNotIn('shortlist', after)

    def test_zero_contact_budget_preserves_old_output_except_added_evidence_and_identity(self):
        args = with_own(2, max_new=0)
        before = old.decide(old.NAME, **args)
        after = new.decide(new.NAME, **args)
        after['evidence'].pop('batch')
        after['evidence']['policy'] = old.NAME
        self.assertEqual(before, after)

    def test_repeated_calls_and_public_inputs_are_unchanged(self):
        args = with_own(1)
        args['pending'] += [dict(hospital='H02', patient='OTHER', submitted_at=800)]
        frozen = deepcopy(args)
        self.assertEqual(new.decide(new.NAME, **args), new.decide(new.NAME, **args))
        self.assertEqual(args, frozen)

    def test_hidden_extra_fields_and_future_ledger_rows_do_not_enter_decision(self):
        args = inputs(pending=[dict(hospital='H01', patient='OTHER', submitted_at=800)])
        baseline = new.decide(new.NAME, **args)
        args['pending'][0].update(actual_workers=99, potential_service_seconds=1, future_release_at=1001)
        args['candidates'][0].update(next_reply_at=1001, actual_accept=True)
        args['pending'].append(dict(hospital='H01', patient='FUTURE', submitted_at=1001))
        args['observations'].append(dict(hospital='H01', submitted_at=800, observed_at=1001, outcome='accept'))
        self.assertEqual(new.decide(new.NAME, **args), baseline)

    def test_original_input_validation_is_retained(self):
        for changed in ({'now': True}, {'now':-1}, {'max_new':False}, {'max_new':-1},
                {'candidates':[candidate('H',300),candidate('H',400)]},
                {'patient_pending':[dict(hospital='OWN',patient='P',submitted_at=990,travel_seconds=300)]}):
            with self.assertRaises(ValueError): decide(**changed)

    def test_ast_changes_only_planner_block_and_additional_batch_evidence(self):
        before = ast.parse(inspect.getsource(old.decide)).body[0]
        after = ast.parse(inspect.getsource(new.decide)).body[0]
        def planner_bounds(function):
            start = next(i for i,n in enumerate(function.body)
                if isinstance(n,ast.FunctionDef) and n.name=='plan_cost')
            end = next(i for i,n in enumerate(function.body)
                if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='scored' for t in n.targets))
            return start,end
        old_start,old_end = planner_bounds(before)
        new_start,new_end = planner_bounds(after)
        after.body[new_start:new_end] = deepcopy(before.body[old_start:old_end])
        added = [n for n in after.body if isinstance(n,ast.Assign) and any(
            isinstance(t,ast.Subscript) and isinstance(t.value,ast.Name) and t.value.id=='evidence'
            and isinstance(t.slice,ast.Constant) and t.slice.value=='batch' for t in n.targets)]
        self.assertEqual(len(added), 1)
        after.body.remove(added[0])
        self.assertEqual(ast.dump(after,include_attributes=False), ast.dump(before,include_attributes=False))


if __name__ == '__main__': unittest.main()
