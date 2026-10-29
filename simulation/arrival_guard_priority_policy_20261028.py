"""G2: current count gains or equal-count experimental weight-4 protection.

The reference B is caller-order nearest on this candidate's current public
snapshot. The proposal H is immutable H1. H must not increase B's weighted miss
and must either admit more patients or, at equal count, admit more weight-4
patients. This changes H1's objective; no whole-episode guarantee is implied.
"""
from copy import deepcopy
import time

from . import heuristic_assignment as parent_source
from .arrival_guard_policy_20261028 import (
    _checked_rows, caller_order_nearest, ArrivalGuardSafetyBudgetExhausted,
    PARENT_SOURCE_SHA256, PARENT_MAX_MOVES, PARENT_SAFETY_SECONDS)
from .cp_sat_assignment import assignment_objective, validate_assignment

GUARD_RULE='baseline_priority_miss_nonincrease_and_strict_count_gain_or_same_count_priority4_gain_else_caller_order_nearest'
SCOPE='current supplied accepted-offer snapshot only; caller-order nearest is not a whole-episode R1 replay; count or same-count weight4 protection changes H1 objective; no global success, priority or arrival guarantee'


def guard_current_plan(rows,travel,free,original_h1_plan):
    """Apply G2 to validated public inputs, retaining all three plans."""
    _checked_rows(rows,travel,free)
    if type(original_h1_plan) is not dict or not validate_assignment(rows,original_h1_plan,free):
        raise ValueError('original H1 proposal is not feasible for supplied current snapshot')
    baseline=caller_order_nearest(rows,travel,free)
    bobj=assignment_objective(rows,baseline,travel)
    hobj=assignment_objective(rows,original_h1_plan,travel)
    bc=sum(h is not None for h in baseline.values())
    hc=sum(h is not None for h in original_h1_plan.values())
    def top4(plan):return sum(row['priority']==4 and plan[row['id']] is not None for row in rows)
    b4,h4=top4(baseline),top4(original_h1_plan)
    count_gain=hc>bc;same_count_top4_gain=hc==bc and h4>b4
    priority_ok=hobj[0]<=bobj[0]
    use_h1=priority_ok and (count_gain or same_count_top4_gain)
    actual=deepcopy(original_h1_plan if use_h1 else baseline)
    if use_h1:
        reason=('strict_current_count_gain_without_baseline_priority_cost' if count_gain else
            'same_current_count_priority4_gain_without_baseline_priority_cost')
    elif count_gain or same_count_top4_gain:
        reason='rejected_current_admission_gain_with_baseline_priority_cost'
    else:reason='preserve_caller_nearest_without_count_or_priority4_gain'
    return dict(schema_version=1,rule=GUARD_RULE,scope=SCOPE,
        caller_ready_order=[row['id'] for row in rows],
        baseline_ready_order_r1_plan=baseline,original_h1_plan=deepcopy(original_h1_plan),actual_plan=actual,
        baseline_objective=bobj,original_h1_objective=hobj,actual_objective=assignment_objective(rows,actual,travel),
        baseline_assigned_count=bc,original_h1_assigned_count=hc,
        actual_assigned_count=sum(h is not None for h in actual.values()),
        baseline_priority4_assigned_count=b4,original_h1_priority4_assigned_count=h4,
        actual_priority4_assigned_count=top4(actual),
        strict_current_count_gain=count_gain,same_current_count_priority4_gain=same_count_top4_gain,
        baseline_priority_cost_nonincrease=priority_ok,used_h1_proposal=use_h1,reason=reason,
        objective_change_disclosed=True,old_H1_local_objective_nonregression_claimed=False,
        whole_episode_nonregression_guaranteed=False,actual_arrival_nonregression_guaranteed=False,
        whole_episode_priority4_nonregression_guaranteed=False)


class ArrivalGuardPriorityAssignmentRule:
    code='G2'
    joint=True
    choose=parent_source.HeuristicAssignmentRule.choose

    def __init__(self):
        self.parent=parent_source.HeuristicAssignmentRule(
            time_limit_seconds=PARENT_SAFETY_SECONDS,max_moves=PARENT_MAX_MOVES)
        self.records=[];self.fallbacks=0

    def decide_batch(self,sim,ready,t):
        started=time.perf_counter()
        if not ready:return {}
        sorted_rows,travel,free=self.parent._snapshot(sim,ready,t)
        by_id={row['id']:row for row in sorted_rows}
        caller_rows=[deepcopy(by_id[p.id]) for p,_ in ready]
        before=deepcopy((caller_rows,travel,free));offset=len(self.parent.records)
        proposal=self.parent.decide_batch(sim,ready,t)
        if len(self.parent.records)!=offset+1:raise AssertionError('one original H1 proposal required')
        original=deepcopy(self.parent.records[-1])
        if original['search_stop_reason'] in ('time_limit','time_limit_zero') or original['search_seconds']>=PARENT_SAFETY_SECONDS:
            raise ArrivalGuardSafetyBudgetExhausted('original H1 safety guard exhausted')
        if original['evaluated_moves']>PARENT_MAX_MOVES:raise AssertionError('original H1 work limit exceeded')
        if original['assignments']!=proposal:raise AssertionError('original proposal record differs')
        result=guard_current_plan(caller_rows,travel,free,proposal)
        if (caller_rows,travel,free)!=before:raise AssertionError('guard changed public snapshot')
        self.records.append(dict(result,t=t,status='GUARDED_HEURISTIC',optimal_proven=False,
            original_h1_record=original,parent_h1_source_sha256=PARENT_SOURCE_SHA256,
            parent_h1_max_moves=PARENT_MAX_MOVES,parent_h1_safety_seconds=PARENT_SAFETY_SECONDS,
            wall_seconds=time.perf_counter()-started))
        return deepcopy(result['actual_plan'])
