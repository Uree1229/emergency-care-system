"""G5: refine a frozen G2 decision without changing its admission priorities.

Only currently received offers, travel estimates and current free resources
are supplied. Selected IDs, weight-4 destinations and aggregate hospital
resource consumption are locked to G2. Every selected patient's travel must
be no longer than G2's. These are current-snapshot constraints, not a proof
about future resource releases, future patients or whole-episode outcomes.

Two logical shortcuts avoid the parent search when G2 must return the caller
baseline: a singleton, or a baseline admitting every ready patient. Neither
shortcut is described as an executed H1 proposal. All other seeds use the
unchanged G2 implementation and its unchanged H1 search.
"""
from collections import Counter
from copy import deepcopy
import hashlib
from pathlib import Path
import time

from . import arrival_guard_priority_policy_20261028 as frozen_g2
from .arrival_guard_policy_20261028 import (
    _checked_rows, caller_order_nearest, PARENT_MAX_MOVES, PARENT_SAFETY_SECONDS,
    ArrivalGuardSafetyBudgetExhausted)
from .cp_sat_assignment import validate_assignment

G2_SOURCE_SHA256 = '57373bf5fa1723adbd284274b7b63ba52f8c5665d02e94c6a78ae258fd759878'
if hashlib.sha256(Path(frozen_g2.__file__).read_bytes()).hexdigest() != G2_SOURCE_SHA256:
    raise ValueError('frozen G2 source differs')

CONSTRAINTS = dict(selected_ids='exact_G2_selected_set', priority4='same_G2_destination',
    resources='same_per_hospital_resource_footprint', travel='per_selected_patient_nonincrease',
    change_requires='strict_travel_improvement_for_at_least_one_selected_patient')
SCOPE = 'current accepted-offer snapshot only; no whole-episode or clinical nonregression guarantee'


def resource_footprint(rows, plan):
    consumed = Counter()
    for row in rows:
        hid = plan[row['id']]
        if hid is not None:
            for resource, quantity in row['demand'].items():
                if quantity:
                    consumed[hid, resource] += quantity
    return [dict(hospital=hid, resource=res, quantity=q)
            for (hid, res), q in sorted(consumed.items()) if q]


def _cost(rows, travel, seed, plan):
    selected = [row for row in rows if plan[row['id']] is not None]
    return [sum(row['priority'] * travel[row['id'], plan[row['id']]] for row in selected),
            sum(travel[row['id'], plan[row['id']]] for row in selected),
            sum(plan[pid] != hid for pid, hid in seed.items())]


class _WorkLimit(Exception):
    pass


def _refine(rows, travel, free, seed, max_moves, safety_check=None):
    _checked_rows(rows, travel, free)
    if type(max_moves) is not int or not 0 <= max_moves <= PARENT_MAX_MOVES:
        raise ValueError('refinement work must be an integer in [0, 2048]')
    if type(seed) is not dict or not validate_assignment(rows, seed, free):
        raise ValueError('feasible G2 seed with exact input IDs required')
    before = deepcopy((rows, travel, free, seed))
    selected = [row for row in rows if seed[row['id']] is not None]
    locked = {row['id']: seed[row['id']] for row in selected if row['priority'] == 4}
    target_rows = resource_footprint(rows, seed)
    target = {(r['hospital'], r['resource']): r['quantity'] for r in target_rows}
    choices = {row['id']: ([locked[row['id']]] if row['id'] in locked else
        sorted((hid for hid in row['options']
                if travel[row['id'], hid] <= travel[row['id'], seed[row['id']]]),
               key=lambda hid: (travel[row['id'], hid], hid))) for row in selected}
    # Most-constrained first, with stable IDs for reproducibility. No treatment
    # duration, pending response time or future release is available here.
    fixed = [row for row in selected if len(choices[row['id']]) == 1]
    order = sorted((row for row in selected if len(choices[row['id']]) > 1),
                   key=lambda row: (len(choices[row['id']]), row['id']))
    best, best_key = dict(seed), _cost(rows, travel, seed, seed)
    counters = dict(work_units=0, evaluated_plans=0, feasible_policy_plans=0,
                    accepted_improvements=0)
    stop = 'pareto_envelope_complete'

    def tick():
        if safety_check is not None:
            safety_check()
        if counters['work_units'] >= max_moves:
            raise _WorkLimit
        counters['work_units'] += 1

    def enumerate_iterative():
        """Bounded DFS without Python recursion or future schedule access."""
        nonlocal best, best_key
        plan = {row['id']: None for row in rows if seed[row['id']] is None}
        used = Counter()
        for row in fixed:
            hid = choices[row['id']][0]
            plan[row['id']] = hid
            for res, q in row['demand'].items():
                if q:
                    used[hid, res] += q
        cursors, index = [0] * len(order), 0

        def undo(at):
            row = order[at]
            hid = plan.pop(row['id'])
            for res, q in row['demand'].items():
                if q:
                    used[hid, res] -= q

        while index >= 0:
            if index == len(order):
                tick()
                counters['evaluated_plans'] += 1
                if dict(+used) == target:
                    counters['feasible_policy_plans'] += 1
                    key = _cost(rows, travel, seed, plan)
                    if key < best_key:
                        best, best_key = dict(plan), key
                        counters['accepted_improvements'] += 1
                index -= 1
                if index >= 0:
                    undo(index)
                continue
            row = order[index]
            pid = row['id']
            if cursors[index] == len(choices[pid]):
                cursors[index] = 0
                index -= 1
                if index >= 0:
                    undo(index)
                continue
            hid = choices[pid][cursors[index]]
            cursors[index] += 1
            tick()
            if any(used[hid, res] + q > target.get((hid, res), 0)
                   for res, q in row['demand'].items() if q):
                continue
            plan[pid] = hid
            for res, q in row['demand'].items():
                if q:
                    used[hid, res] += q
            index += 1

    movable = [row for row in selected if row['id'] not in locked]
    potential = any(any(travel[row['id'], hid] < travel[row['id'], seed[row['id']]]
                        for hid in choices[row['id']]) for row in movable)
    try:
        if not potential:
            stop = 'no_strict_pareto_edge'
        elif max_moves == 0:
            stop = 'work_limit'
        else:
            enumerate_iterative()
    except _WorkLimit:
        stop = 'work_limit'

    if not validate_assignment(rows, best, free):
        raise AssertionError('refined plan is infeasible')
    if {pid for pid, hid in best.items() if hid is not None} != {row['id'] for row in selected}:
        raise AssertionError('G2 selected set changed')
    if any(best[pid] != hid for pid, hid in locked.items()):
        raise AssertionError('G2 weight-4 destination changed')
    if resource_footprint(rows, best) != target_rows:
        raise AssertionError('G2 resource footprint changed')
    if any(travel[row['id'], best[row['id']]] > travel[row['id'], seed[row['id']]] for row in selected):
        raise AssertionError('selected patient travel worsened')
    if best != seed and not best_key[0] < _cost(rows, travel, seed, seed)[0]:
        raise AssertionError('changed plan lacks a strict Pareto gain')
    if (rows, travel, free, seed) != before:
        raise AssertionError('refinement mutated its public input')
    return dict(schema_version=1, rule='G5', scope=SCOPE, constraints=dict(CONSTRAINTS),
        g2_plan=dict(seed), actual_plan=best,
        selected_patient_ids=[row['id'] for row in rows if seed[row['id']] is not None],
        priority4_destinations=locked, g2_resource_footprint=target_rows,
        actual_resource_footprint=resource_footprint(rows, best),
        g2_travel_objective=_cost(rows, travel, seed, seed), actual_travel_objective=best_key,
        travel_saved_minutes={row['id']: travel[row['id'], seed[row['id']]]-
                             travel[row['id'], best[row['id']]] for row in selected},
        reason='strict_G2_preserving_pareto_refinement' if best != seed else 'G2_plan_preserved',
        max_refinement_work_units=max_moves, search_stop_reason=stop,
        optimal_proven=False, whole_episode_nonregression_guaranteed=False,
        whole_episode_priority4_nonregression_guaranteed=False, clinical_priority_policy_validated=False,
        **counters)


def refine_g2_plan(rows, travel, free, g2_plan, max_moves=PARENT_MAX_MOVES):
    """Pure, deterministic G2-seeded refinement; never changes selected IDs."""
    return _refine(rows, travel, free, g2_plan, max_moves)


class G2PreservingAssignmentRule:
    code = 'G5'
    joint = True
    choose = frozen_g2.ArrivalGuardPriorityAssignmentRule.choose

    def __init__(self):
        self.parent = frozen_g2.ArrivalGuardPriorityAssignmentRule()
        self.records = []
        self.fallbacks = 0

    def decide_batch(self, sim, ready, t):
        started = time.perf_counter()
        if not ready:
            return {}
        sorted_rows, travel, free = self.parent.parent._snapshot(sim, ready, t)
        by_id = {row['id']: row for row in sorted_rows}
        rows = [deepcopy(by_id[p.id]) for p, _ in ready]
        baseline = caller_order_nearest(rows, travel, free)
        shortcut = ('singleton_guard_cannot_gain' if len(rows) == 1 else
                    'all_ready_already_assigned' if all(hid is not None for hid in baseline.values()) else None)
        parent_record, parent_work = None, 0
        if shortcut:
            seed = dict(baseline)
        else:
            offset = len(self.parent.records)
            seed = self.parent.decide_batch(sim, ready, t)
            if len(self.parent.records) != offset + 1:
                raise AssertionError('one unchanged G2 parent decision required')
            parent_record = deepcopy(self.parent.records[-1])
            parent_work = parent_record['original_h1_record']['evaluated_moves']

        def check():
            if time.perf_counter() - started >= PARENT_SAFETY_SECONDS:
                raise ArrivalGuardSafetyBudgetExhausted('G5 technical safety budget exhausted')

        check()
        result = _refine(rows, travel, free, seed, PARENT_MAX_MOVES-parent_work, check)
        check()
        result.update(t=t, status='G2_PRESERVING_PARETO', parent_g2_record=parent_record,
            parent_g2_source_sha256=G2_SOURCE_SHA256, g2_seed_mode=shortcut or 'unchanged_G2_parent',
            parent_work_units=parent_work, total_work_units=parent_work+result['work_units'],
            total_max_work_units=PARENT_MAX_MOVES, safety_seconds=PARENT_SAFETY_SECONDS,
            wall_seconds=time.perf_counter()-started)
        self.records.append(result)
        return deepcopy(result['actual_plan'])
