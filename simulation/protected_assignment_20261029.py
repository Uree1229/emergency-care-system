"""G3: preserve current baseline admission opportunities before joint repairs.

The reference is caller-order nearest on the supplied current accepted offers.
Its admitted IDs remain admitted. At unchanged count, changes must be a travel
Pareto improvement for those IDs. Additional admissions may require detours;
their maximum/sum positive baseline travel regret are explicit tie breakers.

This intentionally changes G2's experimental priority-only exchange policy.
It neither proves whole-episode R1 nonregression nor makes clinical priorities.
Existing frozen policies are imported without modification.
"""
from collections import Counter
from copy import deepcopy
import math
import time

from .arrival_guard_policy_20261028 import _checked_rows, caller_order_nearest
from .cp_sat_assignment import CPSATAssignmentRule, validate_assignment
from .heuristic_assignment import HeuristicAssignmentRule


MAX_WORK_UNITS = 2048
SAFETY_SECONDS = 10.0
SCOPE = ('current supplied accepted-offer snapshot only; protects caller-order '
         'nearest admitted IDs, not the counterfactual whole-episode R1 path')
OBJECTIVE_NAMES = ('unassigned_priority_weight', 'unassigned_count',
                   'maximum_positive_baseline_travel_regret',
                   'sum_positive_baseline_travel_regret',
                   'priority_weighted_travel_minutes', 'changed_baseline_destinations')


class ProtectedAssignmentSafetyBudgetExhausted(RuntimeError):
    """Technical failure, never a wall-dependent alternative plan."""


class _WorkLimit(Exception):
    pass


def _validated(rows, travel, free, max_moves):
    if type(max_moves) is not int or max_moves < 0:
        raise ValueError('max_moves must be a nonnegative exact integer')
    # Validate option element types before the frozen helper takes a set.
    if type(rows) is list:
        for row in rows:
            if type(row) is dict and type(row.get('options')) is list:
                if any(type(hid) is not str or not hid for hid in row['options']):
                    raise ValueError('nonempty string offered hospital IDs required')
    _checked_rows(rows, travel, free)


def protected_objective(rows, travel, baseline, plan):
    """Exact finite lexicographic policy key, independently recomputable."""
    protected = [row for row in rows if baseline[row['id']] is not None]
    regret = [max(0, travel[row['id'], plan[row['id']]] -
                  travel[row['id'], baseline[row['id']]]) for row in protected]
    return [sum(row['priority'] for row in rows if plan[row['id']] is None),
            sum(plan[row['id']] is None for row in rows),
            max(regret, default=0), sum(regret),
            sum(row['priority'] * travel[row['id'], plan[row['id']]]
                for row in rows if plan[row['id']] is not None),
            sum(plan[row['id']] != baseline[row['id']] for row in protected)]


def _solve(rows, travel, free, max_moves, safety_check=None):
    _validated(rows, travel, free, max_moves)
    baseline = caller_order_nearest(rows, travel, free)
    protected = {pid for pid, hid in baseline.items() if hid is not None}
    by_id = {row['id']: row for row in rows}
    order = {row['id']: index for index, row in enumerate(rows)}
    base_count = len(protected)
    best = dict(baseline)
    best_key = protected_objective(rows, travel, baseline, best)
    counters = dict(work_units=0, evaluated_plans=0, feasible_plans=0,
                    rejected_policy_plans=0, accepted_improvements=0,
                    duplicate_partial_states=0, maximum_chain_depth=0)
    stop = 'policy_neighborhood_complete'
    mode = 'bounded_augmentation'

    def tick():
        if safety_check is not None:
            safety_check()
        if counters['work_units'] >= max_moves:
            raise _WorkLimit
        counters['work_units'] += 1

    def admissible(plan):
        if any(plan[pid] is None for pid in protected):
            return False
        count = sum(hid is not None for hid in plan.values())
        if count < base_count:
            return False
        if count == base_count and plan != baseline:
            deltas = [travel[pid, plan[pid]] - travel[pid, baseline[pid]] for pid in protected]
            if not deltas or max(deltas) > 0 or min(deltas) >= 0:
                return False
        return True

    def consider(plan):
        nonlocal best, best_key
        tick()
        counters['evaluated_plans'] += 1
        if not validate_assignment(rows, plan, free):
            return
        counters['feasible_plans'] += 1
        if not admissible(plan):
            counters['rejected_policy_plans'] += 1
            return
        key = protected_objective(rows, travel, baseline, plan)
        if key < best_key:
            best, best_key = dict(plan), key
            counters['accepted_improvements'] += 1

    # Small envelopes are traversed completely when their worst-case node and
    # leaf-evaluation count fits this same budget. This is policy-constrained,
    # not an unrestricted global optimum or a claim about future arrivals.
    exact_rows = sorted(rows, key=lambda row: (row['id'] not in protected,
        len(row['options']), -row['priority'], order[row['id']]))
    products, exact_work_bound = 1, 0
    for row in exact_rows:
        products *= len(row['options']) + (row['id'] not in protected)
        exact_work_bound += products
    exact_work_bound += products

    def enumerate_current(index, plan, used):
        if index == len(exact_rows):
            consider(plan)
            return
        row = exact_rows[index]
        options = sorted(row['options'], key=lambda hid: (travel[row['id'], hid], hid))
        if row['id'] not in protected:
            options.append(None)
        for hid in options:
            tick()
            if hid is not None and any(used[hid, resource] + quantity > free[hid, resource]
                                      for resource, quantity in row['demand'].items() if quantity):
                continue
            plan[row['id']] = hid
            if hid is not None:
                for resource, quantity in row['demand'].items():
                    used[hid, resource] += quantity
            enumerate_current(index + 1, plan, used)
            if hid is not None:
                for resource, quantity in row['demand'].items():
                    used[hid, resource] -= quantity
            del plan[row['id']]

    seen_partial = set()

    def force(pid, hid, plan, locked):
        """Displace resource-sharing blockers, retaining all protected IDs at leaves."""
        tick()
        state = (pid, hid, tuple(plan[row['id']] for row in rows), tuple(sorted(locked)))
        if state in seen_partial:
            counters['duplicate_partial_states'] += 1
            return
        seen_partial.add(state)
        counters['maximum_chain_depth'] = max(counters['maximum_chain_depth'], len(locked) + 1)
        partial = dict(plan)
        partial[pid] = None
        yield from room(pid, hid, partial, locked | {pid})

    def room(pid, hid, plan, locked):
        tick()
        demand = by_id[pid]['demand']
        used = Counter()
        for other in rows:
            if plan[other['id']] == hid:
                used.update(other['demand'])
        deficient = {resource for resource, quantity in demand.items()
                     if quantity and used[resource] + quantity > free[hid, resource]}
        if not deficient:
            result = dict(plan)
            result[pid] = hid
            yield result
            return
        blockers = [row for row in rows if row['id'] not in locked and plan[row['id']] == hid
                    and any(row['demand'].get(resource, 0) for resource in deficient)]
        blockers.sort(key=lambda row: (row['priority'], -len(row['options']), order[row['id']]))
        for blocker in blockers:
            bid = blocker['id']
            alternatives = sorted((h for h in blocker['options'] if h != hid),
                                  key=lambda h: (travel[bid, h], h))
            for alternate in alternatives:
                for changed in force(bid, alternate, plan, locked):
                    yield from room(pid, hid, changed, locked | {bid})
            # A newly added, unprotected admission can be replaced by a better
            # one; an originally protected admission can never be dropped.
            if bid not in protected:
                tick()
                changed = dict(plan)
                changed[bid] = None
                yield from room(pid, hid, changed, locked | {bid})

    try:
        if not rows:
            stop = 'empty_batch'
        elif max_moves == 0:
            stop = 'work_limit'
        elif exact_work_bound <= max_moves:
            mode = 'complete_small_policy_envelope'
            enumerate_current(0, {}, Counter())
            stop = 'small_policy_envelope_complete'
        else:
            # Searches restart from each improving incumbent. All roots in a
            # pass share one immutable start plan; strict keys prevent cycles.
            while True:
                start, old_key = dict(best), list(best_key)
                seen_partial.clear()
                roots = sorted(rows, key=lambda row: (start[row['id']] is not None,
                    -row['priority'], len(row['options']), order[row['id']]))
                for row in roots:
                    pid = row['id']
                    for hid in sorted(row['options'], key=lambda h: (travel[pid, h], h)):
                        if hid == start[pid]:
                            continue
                        for candidate in force(pid, hid, start, set()):
                            consider(candidate)
                if best_key == old_key:
                    break
    except _WorkLimit:
        stop = 'work_limit'

    if not validate_assignment(rows, best, free) or not admissible(best):
        raise AssertionError('protected incumbent is not a feasible admissible plan')
    actual_count = sum(h is not None for h in best.values())
    regret = {}
    for row in rows:
        pid = row['id']
        bt = travel[pid, baseline[pid]] if baseline[pid] is not None else None
        at = travel[pid, best[pid]] if best[pid] is not None else None
        regret[pid] = dict(protected=pid in protected, baseline_hospital=baseline[pid],
                          actual_hospital=best[pid], baseline_travel_minutes=bt,
                          actual_travel_minutes=at,
                          positive_travel_regret_minutes=max(0, at-bt) if bt is not None else 0)
    reason = ('protected_admission_augmentation' if actual_count > base_count else
              'same_count_pareto_improvement' if best != baseline else 'baseline_preserved')
    return dict(schema_version=1, rule='G3', scope=SCOPE,
        objective_names=list(OBJECTIVE_NAMES), baseline_ready_order_r1_plan=dict(baseline),
        actual_plan=dict(best), protected_patient_ids=[row['id'] for row in rows if row['id'] in protected],
        caller_ready_order=[row['id'] for row in rows], baseline_assigned_count=base_count,
        actual_assigned_count=actual_count, baseline_objective=protected_objective(rows, travel, baseline, baseline),
        actual_objective=list(best_key), per_patient_regret=regret,
        baseline_priority4_assigned_count=sum(row['priority']==4 and baseline[row['id']] is not None for row in rows),
        actual_priority4_assigned_count=sum(row['priority']==4 and best[row['id']] is not None for row in rows),
        protected_assignments_preserved=True, same_count_pareto_required=True,
        used_protected_augmentation=actual_count > base_count, reason=reason,
        max_work_units=max_moves, search_mode=mode, search_stop_reason=stop, **counters,
        objective_change_disclosed=True, clinical_priority_policy_validated=False,
        whole_episode_nonregression_guaranteed=False, actual_arrival_nonregression_guaranteed=False,
        whole_episode_priority4_nonregression_guaranteed=False, optimal_proven=False)


def protected_current_plan(rows, travel, free, max_moves=MAX_WORK_UNITS):
    """Deterministic bounded public-input policy; never reads a simulator/world."""
    return _solve(rows, travel, free, max_moves)


class ProtectedAssignmentRule:
    code = 'G3'
    joint = True
    choose = HeuristicAssignmentRule.choose
    _snapshot = CPSATAssignmentRule._snapshot

    def __init__(self, max_moves=MAX_WORK_UNITS, safety_seconds=SAFETY_SECONDS):
        if type(max_moves) is not int or max_moves < 0:
            raise ValueError('max_moves must be a nonnegative exact integer')
        if (type(safety_seconds) not in (int, float) or not math.isfinite(safety_seconds)
                or safety_seconds <= 0):
            raise ValueError('safety_seconds must be positive and finite')
        self.max_moves, self.safety_seconds = max_moves, float(safety_seconds)
        self.records, self.fallbacks = [], 0

    def decide_batch(self, sim, ready, t):
        if not ready:
            return {}
        started = time.perf_counter()

        def safety():
            if time.perf_counter()-started >= self.safety_seconds:
                raise ProtectedAssignmentSafetyBudgetExhausted('G3 technical safety limit exhausted')

        sorted_rows, travel, free = self._snapshot(sim, ready, t)
        by_id = {row['id']: row for row in sorted_rows}
        rows = [deepcopy(by_id[p.id]) for p, _ in ready]
        original = deepcopy((rows, travel, free))
        result = _solve(rows, travel, free, self.max_moves, safety)
        safety()
        if (rows, travel, free) != original:
            raise AssertionError('G3 mutated its public input')
        self.records.append(dict(result, t=t, status='PROTECTED_HEURISTIC',
                                 wall_seconds=time.perf_counter()-started,
                                 safety_seconds=self.safety_seconds,
                                 technical_safety_limit_exhausted=False))
        return deepcopy(result['actual_plan'])
