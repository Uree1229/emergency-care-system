"""Synthetic unit boundaries only; no scenarios or performance runs."""
from copy import deepcopy
import math
import unittest
from unittest.mock import patch

from inquiry_lab import eat_policy, residual_policy, horizon_policy, horizon_context


def context(now=1000, created=1000, started=None, automatic=None, version=1):
    started = created if started is None else started
    automatic = started + 300 if automatic is None else automatic
    events = [dict(id=1, at=created, type='patient_created', patient_id='P', data={}),
        dict(id=2, at=started, type='inquiry_workflow_started', patient_id='P',
            data=dict(deadline_seconds=automatic-started, restarted=started != created))]
    job = dict(patient_id='P', version=version, started_at=started,
        deadline_at=automatic, enabled=1)
    return horizon_context.build_context(now=now, patient_id='P', job_version=version,
        event_prefix_max=2, decision_prefix_max=0, job=job, events=events)


def inputs(now=1000, **changes):
    value = dict(now=now, candidates=[dict(hospital_id=f'H{i}',
        travel=dict(duration_seconds=300+10*i), resources_enough=True) for i in range(8)],
        observations=[], pending=[], patient_pending=[], max_new=10, last_contact_at=None)
    value.update(changes)
    if 'time_context' not in value:
        value['time_context'] = context(now=now)
    return value


def without_extra(result):
    value = deepcopy(result)
    value['evidence'].pop('time_horizon')
    value['evidence']['policy'] = residual_policy.NAME
    return value


class HorizonMathTests(unittest.TestCase):
    def test_partial_bin_edges_and_midpoints(self):
        self.assertEqual(horizon_policy.cell_edges(7, 5), [5, 7])
        self.assertEqual(horizon_policy.cell_edges(1, 5), [1])
        self.assertEqual(horizon_policy.cell_edges(15, 5), [5, 10, 15])
        for value in (0, -1, True, 1.5):
            with self.assertRaises(ValueError): horizon_policy.cell_edges(value, 5)
        # All acceptance occurs in (5,7], represented by 6 seconds, not 7.5.
        self.assertEqual(horizon_policy.bounded_expected_cost([(0, [0, 1])], [5, 7], 1500), 6)

    def test_cdf_is_never_sampled_after_H_and_includes_exact_H(self):
        seen = []
        def cdf(value):
            seen.append(value)
            return float(value >= 7)
        masses = horizon_policy.masses_at_edges(cdf, 1, [5, 7])
        self.assertEqual(seen, [5, 7])
        self.assertEqual(masses, [0, 1])
        self.assertEqual(sum(masses), 1)

    def test_shifted_partial_bin_uses_elapsed_two_not_five(self):
        edges = horizon_policy.cell_edges(17, 5)
        cdf = lambda x: min(1., max(0., x/10.))
        delayed = horizon_policy.masses_at_edges(lambda x: cdf(x-15), 1, edges)
        self.assertEqual(delayed, [0, 0, 0, .2])
        old_shift = eat_policy._shift((0, horizon_policy.masses_at_edges(cdf, 1, edges)), 3)[1]
        self.assertEqual(old_shift[-1], .5)

    def test_regular_bins_preserve_original_masses_and_cost_exactly(self):
        edges = horizon_policy.cell_edges(300, 5)
        cdf = lambda x: eat_policy._lognorm_cdf(x, math.log(66), .7)
        masses = horizon_policy.masses_at_edges(cdf, .316, edges)
        self.assertEqual(masses, eat_policy._masses(cdf, .316, 60, 5))
        items = [(350, masses), (500, [x*.7 for x in masses])]
        self.assertEqual(horizon_policy.bounded_expected_cost(items, edges, 1500),
            eat_policy.expected_cost(items, 60, 5, 300, 1500))

    def test_A_does_not_cut_existing_or_new_reply_mass_before_H(self):
        # At now=45, a prior inquiry may reply at120, and a new inquiry sent
        # now may also reply at120. A=60 controls sends, while H=300 bounds
        # observation. Both deterministic distributions retain all mass.
        edges = horizon_policy.cell_edges(255, 5)
        prior = lambda x: float(45+x >= 120)
        new = lambda x: float(x >= 75)
        for cdf in (prior, new):
            masses = horizon_policy.masses_at_edges(cdf, 1, edges)
            self.assertEqual(sum(masses), 1)
            self.assertEqual(sum(masses[:3]), 0)  # A-now is only 15 seconds.
            self.assertEqual(sum(masses[:15]), 1)


class HorizonPolicyTests(unittest.TestCase):
    def test_spec_changes_only_identity_and_copies_frozen_R_config(self):
        before = residual_policy.spec()
        after = horizon_policy.spec()
        for key in ('name', 'label'): before.pop(key); after.pop(key)
        self.assertEqual(before, after)
        after['config']['lambda_seconds'] = 999
        self.assertEqual(horizon_policy.spec()['config']['lambda_seconds'], 15)
        self.assertIs(horizon_policy.Beliefs, residual_policy.Beliefs)
        self.assertIs(horizon_policy._queue_wait_estimate, residual_policy._queue_wait_estimate)
        for invalid in ('eat_l15_f', residual_policy.NAME, None):
            with self.assertRaises(ValueError): horizon_policy.spec(invalid)

    def test_same_public_origin_and_full_grid_preserve_R_core(self):
        for budget in (0, 1, 10):
            args = inputs(max_new=budget)
            baseline = {k:v for k,v in args.items() if k != 'time_context'}
            self.assertEqual(without_extra(horizon_policy.decide(horizon_policy.NAME, **args)),
                residual_policy.decide(residual_policy.NAME, **baseline))

    def test_finished_oldest_inquiry_does_not_advance_observation_origin(self):
        row = dict(hospital='OWN', patient='P', submitted_at=1120)
        args = inputs(now=1180, pending=[row], patient_pending=[dict(row, travel_seconds=300)],
            last_contact_at=1120, time_context=context(now=1180))
        result = horizon_policy.decide(horizon_policy.NAME, **args)
        self.assertEqual(result['evidence']['horizon_seconds'], 120)
        baseline = {k:v for k,v in args.items() if k != 'time_context'}
        self.assertEqual(residual_policy.decide(residual_policy.NAME, **baseline)['evidence']['horizon_seconds'], 240)

    def test_delayed_first_contact_uses_original_public_H(self):
        result = horizon_policy.decide(horizon_policy.NAME, **inputs(now=1100,
            time_context=context(now=1100)))
        self.assertEqual(result['evidence']['horizon_seconds'], 200)

    def test_restart_preserves_H_and_uses_current_A(self):
        result = horizon_policy.decide(horizon_policy.NAME, **inputs(now=1200,
            time_context=context(now=1200, started=1150, automatic=1450, version=4)))
        meta = result['evidence']['time_horizon']
        self.assertEqual(result['evidence']['horizon_seconds'], 100)
        self.assertEqual(meta['new_contact_window_seconds'], 100)
        self.assertTrue(all(x < 100 for x in meta['allowed_plan_offsets_seconds']))

    def test_remaining_one_through_fourteen_has_no_minimum_fifteen_extension(self):
        for remaining in range(1, 15):
            now = 1300-remaining
            result = horizon_policy.decide(horizon_policy.NAME, **inputs(now=now,
                time_context=context(now=now)))
            meta = result['evidence']['time_horizon']
            self.assertEqual(result['evidence']['horizon_seconds'], remaining)
            self.assertEqual(meta['cell_edges_seconds'][-1], remaining)
            self.assertLessEqual(meta['cell_representatives_seconds'][-1], remaining)
            self.assertEqual(result['next_review_at'], now+15)

    def test_A60_H300_allows_send45_but_no_future_send75(self):
        result = horizon_policy.decide(horizon_policy.NAME, **inputs(now=45,
            time_context=context(now=45, created=0, automatic=60)))
        meta = result['evidence']['time_horizon']
        self.assertEqual(result['evidence']['horizon_seconds'], 255)
        self.assertEqual(meta['allowed_plan_offsets_seconds'], [0])
        self.assertTrue(result['send'])
        self.assertEqual(meta['cell_edges_seconds'][-1], 255)

    def test_after_A_keeps_pending_model_but_starts_no_new_inquiry(self):
        row = dict(hospital='OWN', patient='P', submitted_at=45)
        result = horizon_policy.decide(horizon_policy.NAME, **inputs(now=75,
            pending=[row], patient_pending=[dict(row, travel_seconds=300)], last_contact_at=45,
            time_context=context(now=75, created=0, automatic=60)))
        self.assertEqual(result['send'], [])
        self.assertEqual(result['reason'], 'automatic_inquiry_deadline')
        self.assertEqual(result['evidence']['horizon_seconds'], 225)
        self.assertEqual(result['evidence']['time_horizon']['allowed_plan_offsets_seconds'], [])
        self.assertLess(result['evidence']['expected_cost_without_new'], 1725)

    def test_inputs_are_unchanged_and_generator_inputs_are_supported(self):
        args = inputs()
        before = deepcopy(args)
        one = horizon_policy.decide(horizon_policy.NAME, **args)
        self.assertEqual(args, before)
        generators = dict(args)
        for key in ('candidates', 'observations', 'pending', 'patient_pending'):
            generators[key] = iter(args[key])
        self.assertEqual(horizon_policy.decide(horizon_policy.NAME, **generators), one)

    def test_context_mismatch_and_invalid_public_boundaries_fail_closed(self):
        row = dict(hospital='OWN', patient='OTHER', submitted_at=990)
        with self.assertRaisesRegex(ValueError, 'patient differs'):
            horizon_policy.decide(horizon_policy.NAME, **inputs(pending=[row],
                patient_pending=[dict(row, travel_seconds=300)]))
        for now in (1300, 1301):
            with self.assertRaises(ValueError): context(now=now)
        for bad in (None, {}, dict(context(), hidden_workers=20)):
            with self.assertRaises(ValueError):
                horizon_policy.decide(horizon_policy.NAME, **inputs(time_context=bad))


if __name__ == '__main__': unittest.main()
