"""CP-SAT replacement for R3's *current, same-minute* joint decision.

The two lexicographic objectives exactly match ``JointAssignmentRule``:
minimize unassigned priority weight, then priority-weighted travel minutes.
This is deliberately not maximum patient count or a whole-episode optimum.
Only the supplied current accepted offers, current position/travel estimates,
and currently free resources are read. Existing commitments remain locked.

OR-Tools is imported lazily. ``require_backend=True`` makes an absent backend
an explicit dependency error; otherwise a validated priority greedy fallback
is returned and recorded. No future arrival, reply or resource-release schedule
is read. Integer minutes match the existing simulator's travel contract.
"""
from collections import Counter
import math
import time


MAX_INTEGER = (1 << 60) - 1


class CPSATDependencyError(ImportError):
    """The optional CP-SAT backend cannot be loaded."""


def _load_cp_model():
    try:
        from ortools.sat.python import cp_model
    except ImportError as exc:
        raise CPSATDependencyError(
            'CP-SAT requires OR-Tools. Install requirements-portable.txt or '
            'add this project\'s .solver_deps directory to PYTHONPATH.'
        ) from exc
    return cp_model


def _integer(value, label, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or value < (1 if positive else 0) or value > MAX_INTEGER \
            or not math.isfinite(value) or int(value) != value:
        raise ValueError(f'{label} must be a finite '
                         f'{"positive" if positive else "nonnegative"} integer')
    return int(value)


def assignment_objective(rows, plan, travel):
    """The exact R3 objective pair, independently computed from a plan."""
    return [sum(row['priority'] for row in rows if plan[row['id']] is None),
            sum(row['priority'] * travel[(row['id'], plan[row['id']])]
                for row in rows if plan[row['id']] is not None)]


def validate_assignment(rows, plan, free):
    """Check IDs, offered edges and each resource without using CP-SAT."""
    if set(plan) != {row['id'] for row in rows}:
        return False
    used = Counter()
    for row in rows:
        hid = plan[row['id']]
        if hid is None:
            continue
        if hid not in row['options']:
            return False
        for resource, quantity in row['demand'].items():
            if quantity:
                used[(hid, resource)] += quantity
    # A negative pre-existing free value never forces an unrelated all-None
    # plan to be infeasible, but cannot be consumed by a new assignment.
    return all(quantity <= free.get(key, 0) for key, quantity in used.items())


class CPSATAssignmentRule:
    """Deterministic CP-SAT for one ready batch; no future-knowledge oracle.

    Each solver stage shares ``time_limit_seconds``. A proven stage-one
    optimum is locked before stage two is run. If stage one is merely feasible,
    its incumbent is usable but the lexicographic optimum is not claimed.
    ``records`` expose stages, bounds, wall time, input summary and fallback.
    """
    code = 'C1'
    joint = True

    def __init__(self, time_limit_seconds=1.0, seed=0, require_backend=False):
        if isinstance(time_limit_seconds, bool) \
                or not isinstance(time_limit_seconds, (int, float)) \
                or not math.isfinite(time_limit_seconds) or time_limit_seconds < 0:
            raise ValueError('time_limit_seconds must be finite and nonnegative')
        if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < (1 << 31):
            raise ValueError('seed must be an integer in [0, 2**31)')
        if not isinstance(require_backend, bool):
            raise ValueError('require_backend must be bool')
        self.time_limit_seconds = float(time_limit_seconds)
        self.seed = seed
        self.require_backend = require_backend
        self.fallbacks = 0
        self.records = []

    def choose(self, sim, p, options, t):
        """A redirect/single decision uses the same nearest rule as R3."""
        if not options:
            return None
        position = sim.position(p, t)
        return min(options, key=lambda hid: (sim.travel(p, hid, position), hid))

    def _snapshot(self, sim, ready, t):
        rows, travel, free = [], {}, {}
        for p, options in sorted(ready, key=lambda row: (-row[0].priority,
                                                        row[0].t_req, row[0].id)):
            priority = _integer(p.priority, 'priority', positive=True)
            demand = {resource: _integer(quantity, 'resource demand')
                      for resource, quantity in p.demand.items()}
            position = sim.position(p, t)
            offered = sorted(set(options))
            for hid in offered:
                if hid not in sim.hospitals:
                    raise ValueError(f'unknown offered hospital: {hid}')
                h = sim.hospitals[hid]
                for resource in demand:
                    key = (hid, resource)
                    if key not in free:
                        quantity = h.free(resource)
                        # Free resources can be negative after external events.
                        if isinstance(quantity, bool) or not isinstance(quantity, (int, float)) \
                                or abs(quantity) > MAX_INTEGER or not math.isfinite(quantity) \
                                or int(quantity) != quantity:
                            raise ValueError('free resource quantities must be finite integers')
                        free[key] = int(quantity)
                travel[(p.id, hid)] = _integer(sim.travel(p, hid, position), 'travel minutes')
            feasible = [hid for hid in offered
                        if all(free[(hid, resource)] >= quantity
                               for resource, quantity in demand.items() if quantity)]
            rows.append(dict(id=p.id, priority=priority, demand=demand, options=feasible))
        if len({row['id'] for row in rows}) != len(rows):
            raise ValueError('ready patients must have unique IDs')
        if sum(row['priority'] for row in rows) > MAX_INTEGER:
            raise ValueError('total priority exceeds the safe integer model range')
        # Check raw expression domains, rather than relying on the at-most-one
        # constraints to make overflowing coefficient sums harmless.
        if sum(row['priority'] * len(row['options']) for row in rows) > MAX_INTEGER:
            raise ValueError('priority expression exceeds the safe integer model range')
        cost_domain = sum(row['priority'] * travel[(row['id'], hid)]
                          for row in rows for hid in row['options'])
        if cost_domain > MAX_INTEGER:
            raise ValueError('weighted travel exceeds the safe integer model range')
        if sum(sum(row['demand'].values()) for row in rows) > MAX_INTEGER:
            raise ValueError('resource expression exceeds the safe integer model range')
        return rows, travel, free

    @staticmethod
    def _greedy(rows, travel, free):
        taken, plan = Counter(), {}
        for row in rows:
            feasible = [hid for hid in row['options']
                        if all(free[(hid, resource)] - taken[(hid, resource)] >= quantity
                               for resource, quantity in row['demand'].items() if quantity)]
            hid = min(feasible, key=lambda hid: (travel[(row['id'], hid)], hid)) if feasible else None
            plan[row['id']] = hid
            if hid is not None:
                for resource, quantity in row['demand'].items():
                    taken[(hid, resource)] += quantity
        return plan

    def _solve_stage(self, cp, model, remaining):
        solver = cp.CpSolver()
        solver.parameters.num_search_workers = 1
        solver.parameters.random_seed = self.seed
        solver.parameters.max_time_in_seconds = remaining
        status = solver.Solve(model)
        feasible = status in (cp.OPTIMAL, cp.FEASIBLE)
        record = dict(status=solver.StatusName(status),
                      objective=solver.ObjectiveValue() if feasible else None,
                      best_bound=solver.BestObjectiveBound(),
                      wall_seconds=solver.WallTime())
        return solver, status, record

    def decide_batch(self, sim, ready, t):
        started = time.perf_counter()
        if not ready:
            return {}
        rows, travel, free = self._snapshot(sim, ready, t)
        greedy = self._greedy(rows, travel, free)
        if not validate_assignment(rows, greedy, free):
            raise AssertionError('internal greedy fallback failed independent validation')
        plan = greedy
        meta = dict(t=t, scope='current_same_minute_accepted_offers',
                    objective_names=['unassigned_priority_weight', 'priority_weighted_travel_minutes'],
                    batch_size=len(rows), edge_count=sum(len(row['options']) for row in rows),
                    time_limit_seconds=self.time_limit_seconds, seed=self.seed,
                    num_search_workers=1, stages=[], optimal_proven=False,
                    primary_optimal_proven=False, secondary_optimal_proven=False,
                    fallback=False, fallback_reason=None)

        def finish(status, fallback_reason=None):
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
            return finish('FALLBACK', 'time_limit_zero')
        if not meta['edge_count']:
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
            return finish('MISSING_DEPENDENCY', 'missing_backend')

        # Lazy import is startup overhead, separate from the shared model/solve
        # budget. It is still included in the observable total wall_seconds.
        deadline = time.perf_counter() + self.time_limit_seconds
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
                model.AddHint(variables[(row['id'], hid)], int(greedy[row['id']] == hid))
        model.Maximize(assigned_weight)
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            return finish('FALLBACK', 'budget_exhausted_before_stage_one')
        solver, status, stage = self._solve_stage(cp, model, remaining)
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
        if not validate_assignment(rows, incumbent, free):
            return finish('INVALID_INCUMBENT', 'invalid_solver_incumbent')
        # A deadline incumbent can be worse than the precomputed safe greedy
        # plan. Keep the best actual plan; this does not prove optimality.
        if assignment_objective(rows, incumbent, travel) <= assignment_objective(rows, greedy, travel):
            plan = incumbent
        if status != cp.OPTIMAL:
            if plan == greedy and assignment_objective(rows, incumbent, travel) > assignment_objective(rows, greedy, travel):
                return finish('FEASIBLE', 'greedy_better_than_solver_incumbent')
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
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            return finish('FEASIBLE_PRIMARY_OPTIMAL')
        solver, status, stage = self._solve_stage(cp, model, remaining)
        stage['objective_name'] = 'priority_weighted_travel_minutes'
        meta['stages'].append(stage)
        if status in (cp.OPTIMAL, cp.FEASIBLE):
            candidate = read_plan(solver)
            if not validate_assignment(rows, candidate, free):
                return finish('INVALID_INCUMBENT', 'invalid_solver_incumbent')
            if assignment_objective(rows, candidate, travel) <= assignment_objective(rows, plan, travel):
                plan = candidate
            meta['optimal_proven'] = status == cp.OPTIMAL
            meta['secondary_optimal_proven'] = status == cp.OPTIMAL
            return finish('OPTIMAL' if meta['optimal_proven'] else 'FEASIBLE_PRIMARY_OPTIMAL')
        # Stage one's validated incumbent remains safe if cost optimization
        # finds no solution before its remaining deadline.
        return finish('FEASIBLE_PRIMARY_OPTIMAL')
