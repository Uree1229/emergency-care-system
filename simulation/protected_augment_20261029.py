"""G4 repairs G3's search schedule without changing its admissible plans.

Strict primary admission improvements immediately restart from the incumbent.
Admission discovery precedes regret refinement under the same total work budget.
All policy constraints and objectives remain G3's; no future data is read.
The frozen G3 source remains intact and is explicitly bound below.
"""
from collections import Counter
from copy import deepcopy
import hashlib
from pathlib import Path
import time

from . import protected_assignment_20261029 as _parent
from .protected_assignment_20261029 import (
    _validated, _WorkLimit, protected_objective, MAX_WORK_UNITS, SAFETY_SECONDS,
    SCOPE, OBJECTIVE_NAMES)
from .cp_sat_assignment import validate_assignment
from .arrival_guard_policy_20261028 import caller_order_nearest

SEARCH_SCHEDULE = 'admission_first_restart_then_regret_refinement'
PARENT_G3_SOURCE_SHA256 = '33f3018979a6719b217c6e6f0e2b9bb0ffec9488619dd1d9fb0b03c438950fca'
if hashlib.sha256(Path(_parent.__file__).read_bytes()).hexdigest() != PARENT_G3_SOURCE_SHA256:
    raise ValueError('frozen G3 source differs')


class ProtectedAugmentSafetyBudgetExhausted(RuntimeError):
    pass


class _AdmissionRestart(Exception):
    pass


def _current_primary_lower_bound(rows, free):
    """Optimistic capacity/count and priority bound; not a feasibility claim.

    Each resource ignores hospital compatibility and simultaneous resources.
    This relaxation can overestimate admissions but cannot understate the true
    attainable count. Its top-weight sum similarly overestimates attainable
    admitted weight. Reaching both bounds makes further primary gains impossible.
    """
    eligible = [row for row in rows if row['options']]
    maximum_count = len(eligible)
    resources = set().union(*(set(row['demand']) for row in eligible)) if eligible else set()
    for resource in resources:
        positive = [row['demand'].get(resource, 0) for row in eligible
                    if row['demand'].get(resource, 0) > 0]
        if not positive:
            continue
        zero = sum(not row['demand'].get(resource, 0) for row in eligible)
        capacity = sum(max(0, value) for (hid, r), value in free.items() if r == resource)
        maximum_count = min(maximum_count, zero + capacity // min(positive))
    top_weight = sum(sorted((row['priority'] for row in eligible), reverse=True)[:maximum_count])
    return [sum(row['priority'] for row in rows)-top_weight, len(rows)-maximum_count]


def _solve_augment(rows, travel, free, max_moves, safety_check=None):
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
                    duplicate_partial_states=0, maximum_chain_depth=0, fast_admission_restarts=0)
    stop = 'policy_neighborhood_complete'
    mode = 'bounded_augmentation'
    phase = 'initial'
    phase_units = Counter()
    primary_bound = _current_primary_lower_bound(rows, free)

    def tick():
        if safety_check is not None:
            safety_check()
        if counters['work_units'] >= max_moves:
            raise _WorkLimit
        counters['work_units'] += 1
        phase_units[phase] += 1

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
        if phase == 'admission' and key[:2] >= best_key[:2]:
            return
        if key < best_key:
            primary_gain = key[:2] < best_key[:2]
            best, best_key = dict(plan), key
            counters['accepted_improvements'] += 1
            if primary_gain and phase in ('admission', 'regret'):
                raise _AdmissionRestart

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
            phase = 'exact'
            enumerate_current(0, {}, Counter())
            stop = 'small_policy_envelope_complete'
        else:
            phase = 'admission'
            while True:
                if best_key[:2] == primary_bound:
                    phase = 'regret'
                start, old_key = dict(best), list(best_key)
                seen_partial.clear()
                roots = sorted(rows, key=lambda row: (start[row['id']] is not None,
                    -row['priority'], len(row['options']), order[row['id']]))
                try:
                    for row in roots:
                        pid = row['id']
                        for hid in sorted(row['options'], key=lambda h: (travel[pid, h], h)):
                            if hid == start[pid]:
                                continue
                            for candidate in force(pid, hid, start, set()):
                                consider(candidate)
                except _AdmissionRestart:
                    # Do not enumerate the first admitted patient's same-count
                    # placements while other patients could still be admitted.
                    counters['fast_admission_restarts'] += 1
                    phase = 'admission'
                    continue
                if phase == 'admission':
                    phase = 'regret'
                    continue
                if best_key == old_key:
                    break
                phase = 'admission'
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
    return dict(schema_version=1, rule='G4', scope=SCOPE,
        objective_names=list(OBJECTIVE_NAMES), baseline_ready_order_r1_plan=dict(baseline),
        actual_plan=dict(best), protected_patient_ids=[row['id'] for row in rows if row['id'] in protected],
        caller_ready_order=[row['id'] for row in rows], baseline_assigned_count=base_count,
        actual_assigned_count=actual_count, baseline_objective=protected_objective(rows, travel, baseline, baseline),
        actual_objective=list(best_key), per_patient_regret=regret,
        baseline_priority4_assigned_count=sum(row['priority']==4 and baseline[row['id']] is not None for row in rows),
        actual_priority4_assigned_count=sum(row['priority']==4 and best[row['id']] is not None for row in rows),
        protected_assignments_preserved=True, same_count_pareto_required=True,
        used_protected_augmentation=actual_count > base_count, reason=reason,
        max_work_units=max_moves, search_mode=mode, search_stop_reason=stop,
        search_schedule=SEARCH_SCHEDULE, phase_work_units=dict(phase_units),
        optimistic_primary_lower_bound=primary_bound,
        optimistic_primary_bound_reached=best_key[:2] == primary_bound, **counters,
        objective_change_disclosed=True, clinical_priority_policy_validated=False,
        whole_episode_nonregression_guaranteed=False, actual_arrival_nonregression_guaranteed=False,
        whole_episode_priority4_nonregression_guaranteed=False, optimal_proven=False)


def protected_augment_plan(rows, travel, free, max_moves=MAX_WORK_UNITS):
    """Same pure policy as G3, with admission-first deterministic work scheduling."""
    return _solve_augment(rows, travel, free, max_moves)


class ProtectedAugmentAssignmentRule(_parent.ProtectedAssignmentRule):
    code = 'G4'

    def decide_batch(self, sim, ready, t):
        if not ready:
            return {}
        started = time.perf_counter()

        def safety():
            if time.perf_counter()-started >= self.safety_seconds:
                raise ProtectedAugmentSafetyBudgetExhausted('G4 technical safety limit exhausted')

        sorted_rows, travel, free = self._snapshot(sim, ready, t)
        by_id = {row['id']: row for row in sorted_rows}
        rows = [deepcopy(by_id[p.id]) for p, _ in ready]
        original = deepcopy((rows, travel, free))
        result = _solve_augment(rows, travel, free, self.max_moves, safety)
        safety()
        if (rows, travel, free) != original:
            raise AssertionError('G4 mutated its public input')
        self.records.append(dict(result, t=t, status='PROTECTED_HEURISTIC',
                                 wall_seconds=time.perf_counter()-started,
                                 safety_seconds=self.safety_seconds,
                                 technical_safety_limit_exhausted=False))
        return deepcopy(result['actual_plan'])
