"""Single current-offer augment retained before C1 model construction.

The initializer and both solver stages share the original model/search budget.
A successful augment preserves every originally assigned patient and admits one
additional patient. It is an incumbent, never an optimality certificate.
"""
from collections import Counter
import time

from .cp_sat_assignment import (CPSATAssignmentRule, CPSATDependencyError,
    _load_cp_model, assignment_objective, validate_assignment)
from .stable_cp_sat_assignment import StableCPSATAssignmentRule

VARIANT = 'single_augment'
MAX_CANDIDATES = 128
INITIALIZER_LIMIT_SECONDS = .005
INITIALIZER_RULE = 'first_strict_single_blocker_augment'
BUDGET_SCOPE = 'shared_initializer_model_and_two_solver_stages'


def _proposals(rows, plan):
    for row in rows:
        if plan[row['id']] is not None:
            continue
        for hid in row['options']:
            for blocker in rows:
                if plan[blocker['id']] != hid:
                    continue
                for alternate in blocker['options']:
                    if alternate != hid:
                        candidate = dict(plan)
                        candidate[row['id']] = hid
                        candidate[blocker['id']] = alternate
                        yield row['id'], hid, blocker['id'], alternate, candidate


def _unstarted_trace(rows, travel, baseline, reason):
    return dict(schema_version=1, variant=VARIANT, rule=INITIALIZER_RULE,
        max_candidates=MAX_CANDIDATES, initializer_limit_seconds=INITIALIZER_LIMIT_SECONDS,
        budget_scope=BUDGET_SCOPE, original_greedy_assignments=dict(baseline),
        original_greedy_objective=assignment_objective(rows,baseline,travel),
        selected_assignments=dict(baseline), selected_objective=assignment_objective(rows,baseline,travel),
        applied=False, evaluated_candidates=0, candidates=[], stop_reason=reason,
        elapsed_seconds=0., started_at=None, deadline_started_at=None,
        global_deadline_at=None, initializer_deadline_at=None, finished_at=None,
        optimality_claimed=False)


class SeededCPSATAssignmentRule(CPSATAssignmentRule):
    """Copy of the frozen two-stage pipeline with one budgeted initializer."""

    def _initialize(self, rows, travel, free, baseline, budget_started, deadline):
        started = time.perf_counter()
        cap = min(deadline, started + INITIALIZER_LIMIT_SECONDS)
        trace = _unstarted_trace(rows,travel,baseline,'exhausted')
        trace.update(started_at=started, deadline_started_at=budget_started,
            global_deadline_at=deadline, initializer_deadline_at=cap)
        plan = dict(baseline)
        proposals = iter(_proposals(rows,baseline))
        while True:
            now = time.perf_counter()
            if trace['evaluated_candidates'] >= MAX_CANDIDATES:
                trace['stop_reason']='candidate_limit'; break
            if now >= deadline:
                trace['stop_reason']='global_time_limit'; break
            if now >= cap:
                trace['stop_reason']='initializer_time_limit'; break
            try:
                pid,hid,blocker,alternate,candidate=next(proposals)
            except StopIteration:
                break
            checked = time.perf_counter()
            if checked >= deadline:
                trace['stop_reason']='global_time_limit'; break
            if checked >= cap:
                trace['stop_reason']='initializer_time_limit'; break
            feasible = validate_assignment(rows,candidate,free)
            key = assignment_objective(rows,candidate,travel) if feasible else None
            accepted = feasible and key < trace['original_greedy_objective']
            finished = time.perf_counter()
            trace['evaluated_candidates'] += 1
            trace['candidates'].append(dict(candidate_index=trace['evaluated_candidates'],
                patient=pid,hospital=hid,blocker=blocker,alternate=alternate,
                assignments=dict(candidate),feasible=feasible,objective=key,accepted=bool(accepted),
                started_at=checked,finished_at=finished))
            if accepted:
                plan=dict(candidate)
                trace.update(applied=True,stop_reason='improvement')
                break
        finished=time.perf_counter()
        trace.update(selected_assignments=dict(plan),selected_objective=assignment_objective(rows,plan,travel),
            finished_at=finished,elapsed_seconds=finished-started)
        return plan,trace

    def decide_batch(self, sim, ready, t):
        started = time.perf_counter()
        if not ready:
            return {}
        rows, travel, free = self._snapshot(sim, ready, t)
        greedy = self._greedy(rows, travel, free)
        if not validate_assignment(rows, greedy, free):
            raise AssertionError('internal greedy fallback failed independent validation')
        plan = dict(greedy)
        meta = dict(t=t, scope='current_same_minute_accepted_offers',
                    objective_names=['unassigned_priority_weight', 'priority_weighted_travel_minutes'],
                    batch_size=len(rows), edge_count=sum(len(row['options']) for row in rows),
                    time_limit_seconds=self.time_limit_seconds, seed=self.seed,
                    num_search_workers=1, stages=[], optimal_proven=False,
                    primary_optimal_proven=False, secondary_optimal_proven=False,
                    fallback=False, fallback_reason=None)
        meta['incumbent'] = _unstarted_trace(rows,travel,greedy,'not_started')
        meta['c1_incumbent_budget'] = dict(scope=BUDGET_SCOPE,limit_seconds=self.time_limit_seconds,
            started_at=None,deadline_at=None,finished_at=None,model_started_at=None,
            model_finished_at=None,stage_requests=[])
        meta['c1_incumbent_selection'] = dict(initializer_assignments=dict(greedy),
            initializer_objective=assignment_objective(rows,greedy,travel),stage_candidates=[],
            selected_source='original_greedy',selected_assignments=dict(greedy),
            selected_objective=assignment_objective(rows,greedy,travel))

        def finish(status, fallback_reason=None):
            meta['c1_incumbent_budget']['finished_at']=time.perf_counter()
            meta['c1_incumbent_selection'].update(selected_assignments=dict(plan),
                selected_objective=assignment_objective(rows,plan,travel))
            if not validate_assignment(rows, plan, free):
                raise AssertionError('CP-SAT plan failed independent resource validation')
            meta.update(status=status, objective=assignment_objective(rows, plan, travel),
                        assigned_count=sum(hid is not None for hid in plan.values()),
                        fallback=fallback_reason is not None, fallback_reason=fallback_reason,
                        wall_seconds=time.perf_counter() - started,
                        assignments=dict(plan))
            if fallback_reason is not None:
                self.fallbacks += 1
            self.records.append(meta)
            return plan

        if self.time_limit_seconds == 0:
            meta['incumbent']['stop_reason']='not_started_zero_budget'
            return finish('FALLBACK', 'time_limit_zero')
        if not meta['edge_count']:
            meta['incumbent']['stop_reason']='not_started_empty_model'
            meta['optimal_proven'] = True
            meta['primary_optimal_proven'] = True
            meta['secondary_optimal_proven'] = True
            return finish('OPTIMAL_EMPTY')
        try:
            cp = _load_cp_model()
        except CPSATDependencyError as exc:
            if self.require_backend:
                raise
            meta['dependency_error'] = str(exc)
            meta['incumbent']['stop_reason']='not_started_missing_backend'
            return finish('MISSING_DEPENDENCY', 'missing_backend')

        # Lazy import is startup overhead, separate from the shared model/solve
        # budget. It is still included in the observable total wall_seconds.
        budget_started=time.perf_counter()
        deadline = budget_started + self.time_limit_seconds
        meta['c1_incumbent_budget'].update(started_at=budget_started,deadline_at=deadline)
        plan,meta['incumbent']=self._initialize(rows,travel,free,greedy,budget_started,deadline)
        meta['c1_incumbent_selection'].update(initializer_assignments=dict(plan),
            initializer_objective=assignment_objective(rows,plan,travel),
            selected_source='initializer' if plan != greedy else 'original_greedy')
        model_started=time.perf_counter()
        if model_started >= deadline:
            return finish('FALLBACK','budget_exhausted_before_model')
        meta['c1_incumbent_budget']['model_started_at']=model_started
        model = cp.CpModel()
        variables = {(row['id'], hid): model.NewBoolVar(f'x_{index}_{j}')
                     for index, row in enumerate(rows)
                     for j, hid in enumerate(row['options'])}
        for row in rows:
            model.Add(sum(variables[(row['id'], hid)] for hid in row['options']) <= 1)
        for (hid, resource), available in sorted(free.items()):
            terms = [row['demand'].get(resource, 0) * variables[(row['id'], hid)]
                     for row in rows if hid in row['options'] and row['demand'].get(resource, 0)]
            if terms:
                model.Add(sum(terms) <= max(0, available))
        assigned_weight = sum(row['priority'] * variables[(row['id'], hid)]
                              for row in rows for hid in row['options'])
        weighted_travel = sum(row['priority'] * travel[(row['id'], hid)] * variables[(row['id'], hid)]
                              for row in rows for hid in row['options'])
        for row in rows:
            for hid in row['options']:
                model.AddHint(variables[(row['id'], hid)], int(plan[row['id']] == hid))
        model.Maximize(assigned_weight)
        meta['c1_incumbent_budget']['model_finished_at']=time.perf_counter()
        requested=time.perf_counter()
        remaining = deadline - requested
        if remaining <= 0:
            return finish('FALLBACK', 'budget_exhausted_before_stage_one')
        request=dict(index=0,requested_at=requested,remaining_seconds=remaining,finished_at=None)
        meta['c1_incumbent_budget']['stage_requests'].append(request)
        solver, status, stage = self._solve_stage(cp, model, remaining)
        request['finished_at']=time.perf_counter()
        stage['objective_name'] = 'assigned_priority_weight'
        meta['stages'].append(stage)
        if status not in (cp.OPTIMAL, cp.FEASIBLE):
            return finish(stage['status'], 'no_feasible_solver_incumbent')

        def read_plan(current_solver):
            candidate = {}
            for row in rows:
                chosen = [hid for hid in row['options']
                          if current_solver.Value(variables[(row['id'], hid)])]
                if len(chosen) > 1:
                    return {}  # Fail the independent validator below.
                candidate[row['id']] = chosen[0] if chosen else None
            return candidate

        incumbent = read_plan(solver)
        feasible=validate_assignment(rows,incumbent,free)
        key=assignment_objective(rows,incumbent,travel) if feasible else None
        selected=feasible and key <= assignment_objective(rows,plan,travel)
        meta['c1_incumbent_selection']['stage_candidates'].append(dict(stage_index=0,
            assignments=dict(incumbent),objective=key,feasible=feasible,selected=bool(selected)))
        if not feasible:
            return finish('INVALID_INCUMBENT', 'invalid_solver_incumbent')
        if selected:
            plan=incumbent
            meta['c1_incumbent_selection']['selected_source']='solver_stage_one'
        if status != cp.OPTIMAL:
            if not selected:
                return finish('FEASIBLE', 'incumbent_better_than_solver_incumbent')
            return finish('FEASIBLE')
        meta['primary_optimal_proven'] = True
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            return finish('FEASIBLE_PRIMARY_OPTIMAL')
        optimum_weight = sum(row['priority'] for row in rows if incumbent[row['id']] is not None)
        model.Add(assigned_weight == optimum_weight)
        model.Minimize(weighted_travel)
        model.ClearHints()
        for row in rows:
            for hid in row['options']:
                model.AddHint(variables[(row['id'], hid)], int(plan[row['id']] == hid))
        requested=time.perf_counter()
        remaining = deadline - requested
        if remaining <= 0:
            return finish('FEASIBLE_PRIMARY_OPTIMAL')
        request=dict(index=1,requested_at=requested,remaining_seconds=remaining,finished_at=None)
        meta['c1_incumbent_budget']['stage_requests'].append(request)
        solver, status, stage = self._solve_stage(cp, model, remaining)
        request['finished_at']=time.perf_counter()
        stage['objective_name'] = 'priority_weighted_travel_minutes'
        meta['stages'].append(stage)
        if status in (cp.OPTIMAL, cp.FEASIBLE):
            candidate = read_plan(solver)
            feasible=validate_assignment(rows,candidate,free)
            key=assignment_objective(rows,candidate,travel) if feasible else None
            selected=feasible and key <= assignment_objective(rows,plan,travel)
            meta['c1_incumbent_selection']['stage_candidates'].append(dict(stage_index=1,
                assignments=dict(candidate),objective=key,feasible=feasible,selected=bool(selected)))
            if not feasible:
                return finish('INVALID_INCUMBENT', 'invalid_solver_incumbent')
            if selected:
                plan=candidate
                meta['c1_incumbent_selection']['selected_source']='solver_stage_two'
            meta['optimal_proven'] = status == cp.OPTIMAL
            meta['secondary_optimal_proven'] = status == cp.OPTIMAL
            return finish('OPTIMAL' if meta['optimal_proven'] else 'FEASIBLE_PRIMARY_OPTIMAL')
        # Stage one's validated incumbent remains safe if cost optimization
        # finds no solution before its remaining deadline.
        return finish('FEASIBLE_PRIMARY_OPTIMAL')


class StableSeededCPSATAssignmentRule(StableCPSATAssignmentRule, SeededCPSATAssignmentRule):
    """Reuse the frozen exact-tie selector through its original super call."""
    pass
