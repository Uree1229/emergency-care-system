"""Preserve a stable patient order when C1's two objectives are equal.

This is a deliberately bounded tie rule, not a third optimization stage.
The frozen C1 solver still minimizes unassigned priority weight and then
priority-weighted travel. Only when its returned feasible plan has exactly
the same objective pair as its deterministic priority/request/ID nearest
greedy baseline is that baseline selected. Existing commitments, future
patients and resource confirmation schedules are never read or changed.

The solver keeps its existing shared model/search budget. The additional
snapshot, validation and tie comparison are recorded tie-selection overhead,
included in total wall time; they do not receive a second search budget.
"""
from __future__ import annotations

import time

from .cp_sat_assignment import (
    CPSATAssignmentRule, assignment_objective, validate_assignment,
)


TIE_RULE = 'equal_objective_priority_request_id_nearest_baseline'


class StableCPSATAssignmentRule(CPSATAssignmentRule):
    """Keep C1's original objective/proofs and use a named exact tie policy.

    Patient order is ``(-priority, request time, patient ID)`` and each
    patient's feasible destination order is ``(travel minutes, hospital ID)``.
    The stable baseline is not preferred over any strict C1 improvement.
    ``optimal_proven`` continues to describe only the original two objectives;
    canonical optimality over all equal-objective plans is never claimed.
    """

    def __init__(self, time_limit_seconds=.05, seed=0, require_backend=False):
        super().__init__(time_limit_seconds=time_limit_seconds, seed=seed,
                         require_backend=require_backend)

    def decide_batch(self, sim, ready, t):
        started = time.perf_counter()
        ready = list(ready)
        solver_plan = super().decide_batch(sim, ready, t)
        if not ready:
            return solver_plan

        tie_started = time.perf_counter()
        # Replay supplies a detached current-input view. Re-snapshotting that
        # same view exposes no physical truth, future events or new offers.
        rows, travel, free = self._snapshot(sim, ready, t)
        baseline = self._greedy(rows, travel, free)
        if not validate_assignment(rows, baseline, free):
            raise AssertionError('stable baseline failed independent resource validation')
        if not validate_assignment(rows, solver_plan, free):
            raise AssertionError('C1 solver plan failed stable input validation')
        solver_objective = assignment_objective(rows, solver_plan, travel)
        baseline_objective = assignment_objective(rows, baseline, travel)
        if solver_objective > baseline_objective:
            raise AssertionError('C1 returned a plan worse than its own greedy baseline')

        equal = solver_objective == baseline_objective
        selected = dict(baseline if equal else solver_plan)
        applied = selected != solver_plan
        reason = ('equal_objective_baseline' if applied else
                  'equal_objective_unchanged' if equal else 'solver_strictly_better')
        if not validate_assignment(rows, selected, free):
            raise AssertionError('stable tie selection violated resource constraints')
        if assignment_objective(rows, selected, travel) != solver_objective:
            raise AssertionError('stable tie selection changed the C1 objective')

        record = self.records[-1]
        if record['assignments'] != solver_plan or record['objective'] != solver_objective:
            raise AssertionError('C1 solver record does not match its returned plan')
        record['stability'] = dict(
            rule=TIE_RULE, solver_assignments=dict(solver_plan),
            baseline_assignments=dict(baseline),
            baseline_objective=list(baseline_objective),
            solver_objective=list(solver_objective),
            selected_assignments=dict(selected), applied=applied, reason=reason,
            elapsed_seconds=time.perf_counter() - tie_started,
            optimality_claimed=False,
        )
        record['solver_pipeline_wall_seconds'] = record['wall_seconds']
        record['assignments'] = dict(selected)
        record['assigned_count'] = sum(hid is not None for hid in selected.values())
        record['wall_seconds'] = time.perf_counter() - started
        return selected
