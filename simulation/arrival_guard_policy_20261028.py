"""Public-snapshot guard for a separately named G1 allocation candidate.

Preserve the caller-order nearest current plan unless the immutable H1 proposal
admits strictly more patients without increasing that plan's priority miss.
This intentionally changes H1's current lexicographic objective; it is not a
whole-episode R1 counterfactual or an arrival/nonregression guarantee.
"""
from collections import Counter
from copy import deepcopy
import hashlib
from pathlib import Path
import time

from . import heuristic_assignment as parent_source
from .cp_sat_assignment import assignment_objective, validate_assignment, _integer

PARENT_SOURCE_SHA256='17ace2f204915695d13a7f69a2dca61236f0c25e80c79030ac294e261c1dcfcb'
if hashlib.sha256(Path(parent_source.__file__).read_bytes()).hexdigest()!=PARENT_SOURCE_SHA256:
    raise ValueError('frozen H1 source differs')
PARENT_MAX_MOVES=2048
PARENT_SAFETY_SECONDS=10.0
GUARD_RULE='strict_current_count_gain_and_no_greater_baseline_priority_miss_else_caller_order_nearest'
SCOPE='current supplied accepted-offer snapshot only; caller-order nearest is not a whole-episode R1 replay; H1 priority-only improvement may be rejected; no global success or arrival guarantee'


class ArrivalGuardSafetyBudgetExhausted(RuntimeError):
    pass


def _checked_rows(rows,travel,free):
    if type(rows) is not list or type(travel) is not dict or type(free) is not dict:
        raise ValueError('public rows, travel and resources required')
    seen=set()
    for row in rows:
        if type(row) is not dict or set(row)!={'id','priority','demand','options'}:
            raise ValueError('public row schema differs')
        pid=row['id']
        if type(pid) is not str or not pid or pid in seen:raise ValueError('unique string patient IDs required')
        seen.add(pid);_integer(row['priority'],'priority',positive=True)
        if type(row['demand']) is not dict or type(row['options']) is not list:
            raise ValueError('public demand/options required')
        if len(row['options'])!=len(set(row['options'])):raise ValueError('duplicate offered edge')
        for resource,quantity in row['demand'].items():
            if type(resource) is not str or not resource:raise ValueError('resource identifier required')
            _integer(quantity,'resource demand')
        for hid in row['options']:
            if type(hid) is not str or not hid:raise ValueError('hospital identifier required')
            _integer(travel.get((pid,hid)),'known offered travel')
            for resource in row['demand']:
                key=(hid,resource)
                if key not in free or type(free[key]) is not int:
                    raise ValueError('known integral current free resource required')
    return rows


def caller_order_nearest(rows,travel,free):
    """Same current input, retaining caller order rather than H1 priority sort."""
    _checked_rows(rows,travel,free)
    taken=Counter();plan={}
    for row in rows:
        feasible=[hid for hid in row['options'] if all(
            free[(hid,resource)]-taken[(hid,resource)]>=quantity
            for resource,quantity in row['demand'].items() if quantity)]
        chosen=min(feasible,key=lambda hid:(travel[(row['id'],hid)],hid)) if feasible else None
        plan[row['id']]=chosen
        if chosen is not None:
            for resource,quantity in row['demand'].items():taken[(chosen,resource)]+=quantity
    if not validate_assignment(rows,plan,free):raise AssertionError('caller-order nearest plan invalid')
    return plan


def guard_current_plan(rows,travel,free,original_h1_plan):
    """Return exact transparent policy evidence without mutating any input."""
    _checked_rows(rows,travel,free)
    if type(original_h1_plan) is not dict or not validate_assignment(rows,original_h1_plan,free):
        raise ValueError('original H1 proposal is not feasible for supplied current snapshot')
    baseline=caller_order_nearest(rows,travel,free)
    b_objective=assignment_objective(rows,baseline,travel)
    h_objective=assignment_objective(rows,original_h1_plan,travel)
    b_count=sum(h is not None for h in baseline.values())
    h_count=sum(h is not None for h in original_h1_plan.values())
    count_gain=h_count>b_count
    priority_ok=h_objective[0]<=b_objective[0]
    use_h1=count_gain and priority_ok
    actual=deepcopy(original_h1_plan if use_h1 else baseline)
    reason=('strict_current_count_gain_without_baseline_priority_cost' if use_h1 else
        'rejected_current_count_gain_with_baseline_priority_cost' if count_gain else
        'preserve_caller_nearest_without_strict_current_count_gain')
    return dict(schema_version=1,rule=GUARD_RULE,scope=SCOPE,
        caller_ready_order=[row['id'] for row in rows],
        baseline_ready_order_r1_plan=baseline,original_h1_plan=deepcopy(original_h1_plan),
        actual_plan=actual,baseline_objective=b_objective,original_h1_objective=h_objective,
        actual_objective=assignment_objective(rows,actual,travel),
        baseline_assigned_count=b_count,original_h1_assigned_count=h_count,
        actual_assigned_count=sum(h is not None for h in actual.values()),
        strict_current_count_gain=count_gain,baseline_priority_cost_nonincrease=priority_ok,
        used_h1_proposal=use_h1,reason=reason,objective_change_disclosed=True,
        old_H1_local_objective_nonregression_claimed=False,
        whole_episode_nonregression_guaranteed=False,actual_arrival_nonregression_guaranteed=False)


class ArrivalGuardAssignmentRule:
    code='G1'
    joint=True
    choose=parent_source.HeuristicAssignmentRule.choose

    def __init__(self):
        self.parent=parent_source.HeuristicAssignmentRule(
            time_limit_seconds=PARENT_SAFETY_SECONDS,max_moves=PARENT_MAX_MOVES)
        self.records=[]
        self.fallbacks=0

    def decide_batch(self,sim,ready,t):
        started=time.perf_counter()
        if not ready:return {}
        sorted_rows,travel,free=self.parent._snapshot(sim,ready,t)
        by_id={row['id']:row for row in sorted_rows}
        caller_rows=[deepcopy(by_id[p.id]) for p,_ in ready]
        before=deepcopy((caller_rows,travel,free))
        offset=len(self.parent.records)
        proposal=self.parent.decide_batch(sim,ready,t)
        if len(self.parent.records)!=offset+1:raise AssertionError('one original H1 proposal required')
        original=deepcopy(self.parent.records[-1])
        if original['search_stop_reason'] in ('time_limit','time_limit_zero') or original['search_seconds']>=PARENT_SAFETY_SECONDS:
            raise ArrivalGuardSafetyBudgetExhausted('original H1 safety guard exhausted')
        if original['evaluated_moves']>PARENT_MAX_MOVES:raise AssertionError('original H1 work limit exceeded')
        if original['assignments']!=proposal:raise AssertionError('original proposal record differs')
        result=guard_current_plan(caller_rows,travel,free,proposal)
        if (caller_rows,travel,free)!=before:raise AssertionError('guard changed public snapshot')
        record=dict(result,t=t,status='GUARDED_HEURISTIC',optimal_proven=False,
            original_h1_record=original,parent_h1_source_sha256=PARENT_SOURCE_SHA256,
            parent_h1_max_moves=PARENT_MAX_MOVES,parent_h1_safety_seconds=PARENT_SAFETY_SECONDS,
            wall_seconds=time.perf_counter()-started)
        self.records.append(record)
        return deepcopy(result['actual_plan'])
