"""Fast bounded joint assignment for the current accepted-offer snapshot.

H1 uses exactly C1's input validation, resource constraints and lexicographic
objective: unassigned priority weight, then priority-weighted travel minutes.
It always retains the validated priority/nearest greedy baseline. Deterministic
multi-start construction and improving one/two-patient repairs can improve that
baseline; neither this search nor a successful repair proves optimality.

The optional search budget starts *after* snapshot and baseline construction.
It is checked cooperatively, not a hard process deadline. Total wall time and
preprocessing/search times are recorded separately. ``max_moves`` bounds the
number of candidate plans evaluated (including multi-start plans), rather than
the number of patients or the number of accepted improvements. No OR-Tools
backend is loaded. Existing commitments and future events are never inspected
or modified; only ``ready`` patients and their supplied valid offers are used.
"""
from collections import Counter
import math
import time

from .cp_sat_assignment import (CPSATAssignmentRule, assignment_objective,
                                validate_assignment)


class HeuristicAssignmentRule:
    code = 'H1'
    joint = True

    _snapshot = CPSATAssignmentRule._snapshot
    _greedy = staticmethod(CPSATAssignmentRule._greedy)
    choose = CPSATAssignmentRule.choose

    def __init__(self, time_limit_seconds=.05, max_moves=256):
        if isinstance(time_limit_seconds, bool) \
                or not isinstance(time_limit_seconds, (int, float)) \
                or not math.isfinite(time_limit_seconds) or time_limit_seconds < 0:
            raise ValueError('time_limit_seconds must be finite and nonnegative')
        if isinstance(max_moves, bool) or not isinstance(max_moves, int) or max_moves < 0:
            raise ValueError('max_moves must be a nonnegative integer')
        self.time_limit_seconds = float(time_limit_seconds)
        self.max_moves = max_moves
        self.records = []
        self.fallbacks = 0

    @staticmethod
    def _construct(rows, travel, free, pressure, pressure_first, deadline):
        taken, plan = Counter(), {}
        for row in rows:
            if time.perf_counter() >= deadline:
                return None
            options = [hid for hid in row['options']
                       if all(free[(hid, resource)] - taken[(hid, resource)] >= quantity
                              for resource, quantity in row['demand'].items() if quantity)]

            def key(hid):
                if not pressure_first:
                    return travel[row['id'], hid], hid
                # Preserve scarce options for other patients. Only currently
                # supplied accepted offers contribute to this pressure score.
                load = sum(pressure[(hid, resource)] * quantity /
                           max(1, free[(hid, resource)] - taken[(hid, resource)])
                           for resource, quantity in row['demand'].items() if quantity)
                return load, travel[row['id'], hid], hid

            hid = min(options, key=key) if options else None
            plan[row['id']] = hid
            if hid is not None:
                for resource, quantity in row['demand'].items():
                    taken[hid, resource] += quantity
        return plan

    @staticmethod
    def _repairs(rows, plan):
        """Yield local candidates in deterministic order without world reads.

        First relocate/replace one blocker to admit an unassigned patient,
        then exchange two destinations, and finally insert/relocate one
        patient. Primary-objective repairs therefore cannot be starved by
        hundreds of travel-only relocation proposals in a large batch.
        Every proposal is independently capacity-checked before it is accepted.
        Larger displacement chains are deliberately outside this bounded rule.
        """
        for row in rows:
            if plan[row['id']] is not None:
                continue
            for hid in row['options']:
                for blocker in rows:
                    if plan[blocker['id']] != hid:
                        continue
                    if not any(row['demand'].get(resource, 0) and quantity
                               for resource, quantity in blocker['demand'].items()):
                        continue
                    # A replacement by a higher priority weight is valid too.
                    for alternate in blocker['options'] + [None]:
                        if alternate == hid:
                            continue
                        candidate = dict(plan)
                        candidate[row['id']] = hid
                        candidate[blocker['id']] = alternate
                        yield 'augment', candidate
        for index, first in enumerate(rows):
            a = plan[first['id']]
            if a is None:
                continue
            for second in rows[index + 1:]:
                b = plan[second['id']]
                if b is None or a == b or b not in first['options'] or a not in second['options']:
                    continue
                candidate = dict(plan)
                candidate[first['id']], candidate[second['id']] = b, a
                yield 'swap', candidate
        for row in rows:
            for hid in row['options']:
                if hid != plan[row['id']]:
                    candidate = dict(plan)
                    candidate[row['id']] = hid
                    yield 'relocate', candidate

    def decide_batch(self, sim, ready, t):
        started = time.perf_counter()
        if not ready:
            return {}
        rows, travel, free = self._snapshot(sim, ready, t)
        baseline = self._greedy(rows, travel, free)
        if not validate_assignment(rows, baseline, free):
            raise AssertionError('internal greedy baseline failed independent validation')
        best = dict(baseline)
        best_key = assignment_objective(rows, best, travel)
        search_started = time.perf_counter()
        deadline = search_started + self.time_limit_seconds
        evaluated = accepted = 0
        strategies, move_types = ['priority_nearest'], []
        strategy_results = [dict(strategy='priority_nearest', objective=list(best_key), accepted=True)]
        improvements = []
        stop_reason = 'search_complete'

        def allowed():
            nonlocal stop_reason
            if evaluated >= self.max_moves:
                stop_reason = 'move_limit'
                return False
            if time.perf_counter() >= deadline:
                stop_reason = 'time_limit'
                return False
            return True

        def consider(candidate, kind):
            nonlocal best, best_key, evaluated, accepted
            evaluated += 1
            if not validate_assignment(rows, candidate, free):
                return False
            key = assignment_objective(rows, candidate, travel)
            if key < best_key:
                best, best_key = candidate, key
                accepted += 1
                move_types.append(kind)
                improvements.append(dict(kind=kind, evaluation=evaluated, objective=list(key)))
                return True
            return False

        if self.time_limit_seconds == 0:
            stop_reason = 'time_limit_zero'
        elif self.max_moves == 0:
            stop_reason = 'move_limit'
        elif not any(row['options'] for row in rows):
            stop_reason = 'no_eligible_offers'
        else:
            pressure = Counter()
            for row in rows:
                for hid in row['options']:
                    for resource, quantity in row['demand'].items():
                        if quantity:
                            pressure[hid, resource] += row['priority'] * quantity / len(row['options'])
            # These priorities are experimental weights, not clinical tiers.
            # Density construction can beat priority-first when one patient
            # consumes the capacity needed by several collectively higher
            # weighted patients. The final objective decides, not this score.
            indexed = {row['id']: index for index, row in enumerate(rows)}
            scarcity = sorted(rows, key=lambda row: (len(row['options']), -row['priority'], indexed[row['id']]))
            density = sorted(rows, key=lambda row: (-row['priority'] / max(1, sum(row['demand'].values())),
                                                   min((travel[row['id'], hid] for hid in row['options']), default=math.inf),
                                                   len(row['options']), indexed[row['id']]))
            starts = [('scarcity_nearest', scarcity, False),
                      ('priority_pressure', rows, True),
                      ('scarcity_pressure', scarcity, True),
                      ('density_nearest', density, False),
                      ('density_pressure', density, True)]
            for name, ordered, pressure_first in starts:
                if not allowed():
                    break
                strategies.append(name)
                candidate = self._construct(ordered, travel, free, pressure, pressure_first, deadline)
                if candidate is None:
                    stop_reason = 'time_limit'
                    break
                changed = consider(candidate, name)
                strategy_results.append(dict(strategy=name,
                    objective=assignment_objective(rows, candidate, travel), accepted=changed))
            # First improvement restarts from the new incumbent. A strict
            # lexicographic improvement prevents cycles even with zero costs.
            while allowed():
                improved = False
                for kind, candidate in self._repairs(rows, best):
                    if not allowed():
                        break
                    if consider(candidate, kind):
                        improved = True
                        break
                if not improved:
                    break

        if not validate_assignment(rows, best, free):
            raise AssertionError('heuristic plan failed independent resource validation')
        baseline_key = assignment_objective(rows, baseline, travel)
        if best_key > baseline_key:
            raise AssertionError('heuristic result worsened its validated baseline')
        finished = time.perf_counter()
        self.records.append(dict(
            t=t, scope='current_same_minute_accepted_offers', status='HEURISTIC',
            objective_names=['unassigned_priority_weight', 'priority_weighted_travel_minutes'],
            objective=best_key, assignments=dict(best), assigned_count=sum(h is not None for h in best.values()),
            baseline_objective=baseline_key, baseline_assignments=dict(baseline),
            baseline_assigned_count=sum(h is not None for h in baseline.values()),
            batch_size=len(rows), edge_count=sum(len(row['options']) for row in rows),
            optimal_proven=False, time_limit_seconds=self.time_limit_seconds,
            time_budget_scope='optional_search_after_snapshot_and_baseline',
            preprocessing_seconds=search_started - started, search_seconds=finished - search_started,
            wall_seconds=finished - started, max_moves=self.max_moves,
            evaluated_moves=evaluated, accepted_moves=accepted, strategies=strategies,
            strategy_results=strategy_results, improvements=improvements,
            accepted_move_types=move_types, search_stop_reason=stop_reason))
        return best
