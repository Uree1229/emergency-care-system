"""Standalone post-optimization certificate; deliberately not wired to a policy.

For two complete plans on the same candidate-pool keys, certify an unchanged
offset histogram and at most four fresh own inquiries at every planned start
under a no-new-terminal-observation assumption. Both plans are checked. An
infeasible reference rejects an exchange; the original decision is not changed.

This is only part of a feasibility review. Candidate eligibility, legal policy
offsets/contact budgets, exchange topology, CDF/utility guards and actual patient
success or waiting-time benefit remain upstream or experimentally unvalidated.
"""
from copy import deepcopy
import math

MAX_CONCURRENT = 4
OBSERVATION_TIMEOUT_SECONDS = 180
RULE = 'same_pool_and_offset_histogram_with_both_no_terminal_observation_plans_cap_feasible'
SCOPE = 'standalone admission certificate only; normalized eligible own-pending source retained including stale rows; inclusive180 freshness; no plan rewrite, new observation, CDF/model scoring or actual-outcome guarantee'


def _number(value, label):
    if type(value) not in (int, float) or value < 0:
        raise ValueError(label + ' must be a finite nonnegative number, excluding bool')
    if type(value) is float and not math.isfinite(value):
        raise ValueError(label + ' must be finite')


def _own_source(rows, now):
    if type(rows) is not list:
        raise ValueError('normalized_own_pending must be a list, not raw/generator input')
    result = []; seen = set(); owner = None
    for row in rows:
        if type(row) is not dict or set(row) != {'hospital', 'patient', 'submitted_at', 'travel_seconds'}:
            raise ValueError('normalized own row must have exactly the four H own-pending fields')
        hospital = row['hospital']; patient = row['patient']
        if type(hospital) is not str or not hospital:
            raise ValueError('normalized own hospital must be a nonempty string')
        if type(patient) not in (int, str):
            raise ValueError('normalized own patient must be a typed integer/string identifier, excluding bool')
        identity = (type(patient), patient)
        if owner is None: owner = identity
        if identity != owner:
            raise ValueError('normalized own rows must describe one typed owner')
        _number(row['submitted_at'], 'submitted_at')
        _number(row['travel_seconds'], 'travel_seconds')
        if row['submitted_at'] > now:
            raise ValueError('a future row is not normalized current own-pending input')
        key = (hospital, identity, row['submitted_at'])
        if key in seen:
            raise ValueError('duplicate normalized own identity; do not silently normalize/deduplicate here')
        seen.add(key); result.append(deepcopy(row))
    return result


def _plan_source(plan):
    if type(plan) is not dict:
        raise ValueError('complete plan must be an ordinary dictionary')
    for hospital, offset in plan.items():
        if type(hospital) is not str or not hospital:
            raise ValueError('plan hospital must be a nonempty string')
        if offset is not None and (type(offset) is not int or offset < 0):
            raise ValueError('planned offset must be None or a nonnegative integer number of seconds')
    return deepcopy(plan)


def _histogram(plan):
    offsets = sorted({value for value in plan.values() if value is not None})
    return [dict(offset_seconds=None, count=sum(value is None for value in plan.values()))] + [
        dict(offset_seconds=offset, count=sum(value == offset for value in plan.values())) for offset in offsets]


def _plan_certificate(plan, own, now):
    occupancy = []
    for start in sorted({value for value in plan.values() if value is not None}):
        original = sum(0 <= now+start-row['submitted_at'] <= OBSERVATION_TIMEOUT_SECONDS for row in own)
        planned = sum(offset is not None and offset <= start and start-offset <= OBSERVATION_TIMEOUT_SECONDS
            for offset in plan.values())
        occupancy.append(dict(offset_seconds=start, at=now+start, original_fresh_count=original,
            planned_fresh_count=planned, total_fresh_count=original+planned,
            within_cap=original+planned <= MAX_CONCURRENT))
    return dict(offset_histogram=_histogram(plan), planned_count=sum(offset is not None for offset in plan.values()),
        immediate_count=sum(offset == 0 for offset in plan.values()), occupancy=occupancy,
        feasible=all(row['within_cap'] for row in occupancy),
        violating_starts=[deepcopy(row) for row in occupancy if not row['within_cap']])


def certify_exchange(*, now, normalized_own_pending, reference_plan, trial_plan):
    """Return a pure, self-contained certificate; never change either plan.

Rows are already-normalized H own=True rows. Stale rows stay in the recorded
source and naturally contribute zero once their age exceeds180. Empty plans
have no planned-start constraints; no separate current-state safety claim is
made in that vacuous case. Hospital key order is copied without reordering.
"""
    if type(now) is not int or now < 0:
        raise ValueError('now must be a nonnegative integer clock, excluding bool')
    own = _own_source(normalized_own_pending, now)
    reference = _plan_source(reference_plan); trial = _plan_source(trial_plan)
    base = _plan_certificate(reference, own, now); candidate = _plan_certificate(trial, own, now)
    same_pool = set(reference) == set(trial)
    same_histogram = base['offset_histogram'] == candidate['offset_histogram']
    same_occupancy = base['occupancy'] == candidate['occupancy']
    if same_histogram and not same_occupancy:
        raise AssertionError('equal offset histograms must induce identical own+planned occupancy')
    reasons = []
    if not same_pool: reasons.append('candidate_pool_keyset_differs')
    if not same_histogram: reasons.append('offset_histogram_differs')
    if not base['feasible']: reasons.append('reference_forecast_exceeds_cap')
    if not candidate['feasible']: reasons.append('trial_forecast_exceeds_cap')
    return dict(schema_version=1, rule=RULE, scope=SCOPE, now=now,
        normalized_own_pending=own, reference_plan=reference, trial_plan=trial,
        candidate_pool_keyset_equal=same_pool, offset_histogram_equal=same_histogram,
        occupancy_equal=same_occupancy, equal_histogram_implies_equal_occupancy=(not same_histogram) or same_occupancy,
        reference=base, trial=candidate, admitted=not reasons, rejection_reasons=reasons,
        maximum_concurrent=MAX_CONCURRENT, observation_timeout_seconds=OBSERVATION_TIMEOUT_SECONDS,
        current_original_fresh_count=sum(0 <= now-row['submitted_at'] <= OBSERVATION_TIMEOUT_SECONDS for row in own),
        integration_status='standalone_not_wired', reference_decision_replaced=False, plan_reoptimization_performed=False,
        eligibility_checked=False, legal_policy_offsets_checked=False, exchange_topology_checked=False,
        utility_or_CDF_scored=False, actual_benefit_validated=False, actual_success_guaranteed=False)
