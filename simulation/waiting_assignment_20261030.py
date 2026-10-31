"""W1: protect older waiting groups before current-batch travel refinement.

This changes H1's static objective, not its observed world or inquiry policy.
Use the same bounded H1 reference first, then spend only its unused candidate
evaluation budget on the waiting objective. No counterfactual future outcome,
reply schedule, resource release or treatment duration is supplied to this rule.
The current H1 primary miss cannot increase; whole-episode nonregression and
individual outcome preservation are deliberately not promised.
"""
from collections import Counter
from copy import deepcopy
import hashlib
from itertools import product
import math
from pathlib import Path
import time
from types import SimpleNamespace

from . import heuristic_assignment as h1_source
from .heuristic_assignment import HeuristicAssignmentRule
from .cp_sat_assignment import CPSATAssignmentRule, MAX_INTEGER, assignment_objective, validate_assignment

PARENT_H1_SHA256='17ace2f204915695d13a7f69a2dca61236f0c25e80c79030ac294e261c1dcfcb'
MAX_MOVES=2048
SAFETY_SECONDS=10.0
PRIORITIES=(4,2,1)
VERSION='waiting-assignment-20261030-v1'
SCOPE='current accepted-offer batch and elapsed search wait only; not whole-episode arrival protection'
if hashlib.sha256(Path(h1_source.__file__).read_bytes()).hexdigest()!=PARENT_H1_SHA256:
    raise ValueError('frozen original H1 source differs')


class WaitingSafetyBudgetExhausted(RuntimeError):
    """Technical failure; never return a wall-time-dependent performance result."""


def _validated(rows,travel,free,ages,max_moves):
    if type(max_moves) is not int or not 0<=max_moves<=MAX_MOVES:
        raise ValueError('max_moves must be an exact integer in [0,2048]')
    if type(rows) is not list or type(travel) is not dict or type(free) is not dict or type(ages) is not dict:
        raise ValueError('primitive rows/travel/free/ages snapshots required')
    ids=set()
    for row in rows:
        if type(row) is not dict or set(row)!={'id','priority','demand','options'}:
            raise ValueError('only the current snapshot row fields are accepted')
        pid=row['id']
        if type(pid) is not str or not pid or pid in ids:raise ValueError('unique nonempty string patient IDs required')
        ids.add(pid)
        if type(row['priority']) is not int or row['priority'] not in PRIORITIES:
            raise ValueError('experimental priority must be exact 4,2,1')
        if type(row['demand']) is not dict or type(row['options']) is not list:
            raise ValueError('resource demand and offered edge snapshots required')
        if any(type(hid) is not str or not hid for hid in row['options']):raise ValueError('hospital IDs must be strings')
        if len(row['options'])!=len(set(row['options'])):raise ValueError('duplicate hospital offer')
        if any(type(k) is not str or not k or type(v) is not int or not 0<=v<=MAX_INTEGER for k,v in row['demand'].items()):
            raise ValueError('resource demands must be bounded nonnegative exact integers')
        for hid in row['options']:
            if (pid,hid) not in travel:raise ValueError('offered travel missing')
            for resource in row['demand']:
                if (hid,resource) not in free:raise ValueError('offered resource snapshot missing')
    if set(ages)!=ids or any(type(v) is not int or not 0<=v<=MAX_INTEGER for v in ages.values()):
        raise ValueError('exact nonnegative elapsed age required for every ready patient')
    for values,label,negative in ((travel,'travel',False),(free,'free',True)):
        for key,value in values.items():
            if type(key) is not tuple or len(key)!=2 or any(type(k) is not str or not k for k in key):
                raise ValueError(label+' keys must be identifier pairs')
            if type(value) is not int or abs(value)>MAX_INTEGER or (value<0 and not negative):
                raise ValueError(label+' values must be bounded exact integers')
    if sum(row['priority'] for row in rows)>MAX_INTEGER or sum(sum(r['demand'].values()) for r in rows)>MAX_INTEGER:
        raise ValueError('snapshot expression exceeds safe integer domain')
    if sum(row['priority']*travel[row['id'],hid] for row in rows for hid in row['options'])>MAX_INTEGER:
        raise ValueError('weighted travel expression exceeds safe integer domain')


def age_buckets(rows,ages):
    """Oldest groups first within tier; omit each tier's youngest age group.

    Omitting that group means a tier with no age contrast contributes no new
    tie-breaker. Equal-age batches retain H1's primary/travel policy exactly.
    These are finite counts, not arbitrarily large aging coefficients.
    """
    buckets=[]
    for priority in PRIORITIES:
        values=sorted({ages[r['id']] for r in rows if r['priority']==priority},reverse=True)
        buckets.extend(dict(priority=priority,age=value) for value in values[:-1])
    return buckets


def waiting_objective(rows,plan,travel,ages):
    original=assignment_objective(rows,plan,travel)
    waiting=[sum(r['priority']==bucket['priority'] and ages[r['id']]==bucket['age']
        and plan[r['id']] is None for r in rows) for bucket in age_buckets(rows,ages)]
    return [original[0],*waiting,original[1]]


def _reference(rows,travel,free,ages,max_moves,seconds):
    """Call unchanged H1 on a new bridge containing only primitive snapshots."""
    at=max(ages.values(),default=0)
    ready=[(SimpleNamespace(id=r['id'],priority=r['priority'],demand=dict(r['demand']),t_req=at-ages[r['id']]),
            list(r['options'])) for r in rows]
    hospitals={hid:SimpleNamespace(free=lambda resource,hid=hid:free[hid,resource])
        for r in rows for hid in r['options']}
    view=SimpleNamespace(hospitals=hospitals,position=lambda patient,t:None,
        travel=lambda patient,hid,position:travel[patient.id,hid])
    rule=HeuristicAssignmentRule(time_limit_seconds=seconds,max_moves=max_moves)
    plan=rule.decide_batch(view,ready,at)
    return plan,rule.records[0]


def solve_waiting(rows,travel,free,ages,max_moves=MAX_MOVES,time_limit_seconds=SAFETY_SECONDS):
    """Return {assignments,record}; never mutate supplied current snapshots.

    Work units are complete candidate plan evaluations, matching H1's original
    counter. H1 evaluations plus W1 evaluations never exceed max_moves. Snapshot
    validation/construction costs are separately visible in total wall time.
    """
    if type(time_limit_seconds) not in (int,float) or not math.isfinite(time_limit_seconds) or not 0<time_limit_seconds<=SAFETY_SECONDS:
        raise ValueError('positive technical safety limit at most ten seconds required')
    started=time.perf_counter();deadline=started+time_limit_seconds
    def safety():
        if time.perf_counter()>=deadline:raise WaitingSafetyBudgetExhausted('W1 technical guard exhausted; comparative outcome invalid')
    _validated(rows,travel,free,ages,max_moves)
    rows=deepcopy(sorted(rows,key=lambda r:(-r['priority'],-ages[r['id']],r['id'])))
    travel=dict(travel);free=dict(free);ages=dict(ages)
    safety();buckets=age_buckets(rows,ages)
    if rows:
        remaining_seconds=deadline-time.perf_counter()
        if remaining_seconds<=0:raise WaitingSafetyBudgetExhausted('no reference safety time remains')
        hplan,hrecord=_reference(rows,travel,free,ages,max_moves,remaining_seconds)
        if hrecord['search_stop_reason'] in ('time_limit','time_limit_zero'):
            raise WaitingSafetyBudgetExhausted('H1 reference guard exhausted; comparative outcome invalid')
    else:
        hplan={};hrecord=dict(assignments={},objective=[0,0],baseline_assignments={},baseline_objective=[0,0],
            evaluated_moves=0,accepted_moves=0,search_stop_reason='no_eligible_offers',wall_seconds=0,search_seconds=0,
            preprocessing_seconds=0,strategies=[],improvements=[],max_moves=max_moves,optimal_proven=False)
    safety()
    if not validate_assignment(rows,hplan,free):raise AssertionError('reference plan infeasible')
    hprimary=assignment_objective(rows,hplan,travel)[0]
    h_moves=hrecord['evaluated_moves'];remaining=max_moves-h_moves
    if not 0<=remaining<=max_moves:raise AssertionError('invalid H1 work accounting')
    best=dict(hplan);base_key=waiting_objective(rows,best,travel,ages);best_key=list(base_key)
    waiting_started=time.perf_counter();evaluated=accepted=feasible=0
    improvements=[];strategies=['H1_reference'];strategy_results=[];mode='no_waiting_age_contrast'
    stop='search_complete';enumeration_complete=False

    def allowed():
        nonlocal stop
        safety()
        if evaluated>=remaining:stop='move_limit';return False
        return True

    def consider(candidate,kind):
        nonlocal best,best_key,evaluated,accepted,feasible
        if not allowed():return False
        evaluated+=1
        if not validate_assignment(rows,candidate,free):return False
        feasible+=1;key=waiting_objective(rows,candidate,travel,ages)
        if key<best_key:
            if key[0]>hprimary:raise AssertionError('primary reference protection failed')
            best=dict(candidate);best_key=list(key);accepted+=1
            improvements.append(dict(evaluation=h_moves+evaluated,waiting_evaluation=evaluated,kind=kind,objective=list(key)))
            return True
        return False

    if not rows or not any(r['options'] for r in rows):
        stop='no_eligible_offers';mode='no_eligible_offers'
    elif not buckets:
        mode='no_waiting_age_contrast'
    elif remaining<=0:
        stop='move_limit';mode='H1_used_total_budget'
    elif math.prod(len(r['options'])+1 for r in rows)<=remaining:
        mode='complete_small_enumeration';strategies.append(mode)
        for choices in product(*(r['options']+[None] for r in rows)):
            safety();consider(dict(zip((r['id'] for r in rows),choices)),mode)
        enumeration_complete=True
    else:
        mode='bounded_waiting_construction_and_repairs'
        order={r['id']:i for i,r in enumerate(rows)}
        oldest=sorted(rows,key=lambda r:(-r['priority'],-ages[r['id']],len(r['options']),order[r['id']]))
        oldest_any=sorted(rows,key=lambda r:(-ages[r['id']],-r['priority'],len(r['options']),order[r['id']]))
        scarce=sorted(rows,key=lambda r:(len(r['options']),-r['priority'],-ages[r['id']],order[r['id']]))
        pressure=Counter()
        for row in rows:
            for hid in row['options']:
                for resource,quantity in row['demand'].items():
                    if quantity:pressure[hid,resource]+=row['priority']*quantity/len(row['options'])
        starts=[('oldest_priority_nearest',oldest,False),('oldest_priority_pressure',oldest,True),
            ('oldest_any_nearest',oldest_any,False),('scarcity_oldest_nearest',scarce,False)]
        for name,ordered,pressure_first in starts:
            if not allowed():break
            strategies.append(name)
            candidate=HeuristicAssignmentRule._construct(ordered,travel,free,pressure,pressure_first,deadline)
            if candidate is None:raise WaitingSafetyBudgetExhausted('waiting construction guard exhausted')
            changed=consider(candidate,name)
            strategy_results.append(dict(strategy=name,objective=waiting_objective(rows,candidate,travel,ages),accepted=changed))
        while allowed():
            improved=False
            for kind,candidate in HeuristicAssignmentRule._repairs(oldest,best):
                if not allowed():break
                if consider(candidate,'waiting_'+kind):improved=True;break
            if not improved:break
    safety()
    if not validate_assignment(rows,best,free) or best_key[0]>hprimary or best_key>base_key:
        raise AssertionError('W1 violated current reference/objective invariants')
    finished=time.perf_counter()
    if finished>=deadline:raise WaitingSafetyBudgetExhausted('W1 final guard exhausted')
    reason='waiting_objective_improved' if best!=hplan else (
        'no_waiting_age_contrast_keep_H1' if not buckets else 'no_better_waiting_plan_within_total_budget')
    names=['unassigned_priority_weight',*[f"unassigned_p{b['priority']}_age{b['age']}" for b in buckets],
        'priority_weighted_travel_minutes']
    record=dict(version=VERSION,t=None,scope=SCOPE,status='WAITING_HEURISTIC',
        assignments=dict(best),objective=list(best_key),objective_names=names,ages=ages,age_buckets=buckets,
        assigned_count=sum(h is not None for h in best.values()),
        baseline_assignments=dict(hplan),baseline_objective=list(base_key),
        baseline_assigned_count=sum(h is not None for h in hplan.values()),
        H1_reference_assignments=dict(hplan),H1_reference_objective=assignment_objective(rows,hplan,travel),
        H1_reference_primary=hprimary,H1_reference_waiting_objective=list(base_key),H1_reference_record=deepcopy(hrecord),
        H1_reference_evaluated_moves=h_moves,waiting_evaluated_moves=evaluated,
        evaluated_moves=h_moves+evaluated,max_moves=max_moves,accepted_moves=accepted,
        feasible_waiting_plans=feasible,improvements=improvements,strategies=strategies,strategy_results=strategy_results,
        accepted_move_types=[i['kind'] for i in improvements],selection_reason=reason,
        search_mode=mode,small_enumeration_complete=enumeration_complete,search_stop_reason=stop,
        batch_size=len(rows),edge_count=sum(len(r['options']) for r in rows),
        time_limit_seconds=float(time_limit_seconds),wall_seconds=finished-started,
        preprocessing_seconds=hrecord.get('preprocessing_seconds',0)+(waiting_started-started-hrecord.get('wall_seconds',0)),
        search_seconds=hrecord.get('search_seconds',0)+finished-waiting_started,
        H1_reference_wall_seconds=hrecord.get('wall_seconds',0),waiting_wall_seconds=finished-waiting_started,
        time_budget_scope='total technical guard including reference and waiting work; complete plan evaluations share one budget',
        optimal_proven=False,whole_episode_nonregression_guaranteed=False,
        future_inputs_used=False,reference_source_sha256=PARENT_H1_SHA256)
    return dict(assignments=dict(best),record=record)


class WaitingAssignmentRule:
    code='W1'
    joint=True
    _snapshot=CPSATAssignmentRule._snapshot
    choose=CPSATAssignmentRule.choose

    def __init__(self,time_limit_seconds=SAFETY_SECONDS,max_moves=MAX_MOVES):
        if type(max_moves) is not int or not 0<=max_moves<=MAX_MOVES:raise ValueError('invalid W1 work budget')
        if type(time_limit_seconds) not in (int,float) or not math.isfinite(time_limit_seconds) or not 0<time_limit_seconds<=SAFETY_SECONDS:
            raise ValueError('invalid W1 technical safety limit')
        self.max_moves=max_moves;self.time_limit_seconds=float(time_limit_seconds);self.records=[];self.fallbacks=0

    def decide_batch(self,sim,ready,t):
        started=time.perf_counter()
        if type(t) is not int or t<0:raise ValueError('current time must be an exact nonnegative integer')
        # request_t is already in the original observed-input whitelist. It is
        # the actual search start, not an occurrence inferred from future data.
        ages={}
        for patient,_ in ready:
            if type(patient.t_req) is not int or not 0<=patient.t_req<=t:raise ValueError('ready request time outside observed past')
            ages[patient.id]=t-patient.t_req
        rows,travel,free=self._snapshot(sim,ready,t)
        snapshot_seconds=time.perf_counter()-started
        remaining=self.time_limit_seconds-snapshot_seconds
        if remaining<=0:raise WaitingSafetyBudgetExhausted('W1 snapshot construction exhausted technical guard')
        solved=solve_waiting(rows,travel,free,ages,self.max_moves,remaining)
        record=solved['record'];record['t']=t
        record['wall_seconds']+=snapshot_seconds
        record['preprocessing_seconds']+=snapshot_seconds
        record['time_limit_seconds']=self.time_limit_seconds
        record['snapshot_seconds']=snapshot_seconds
        if record['wall_seconds']>=self.time_limit_seconds:raise WaitingSafetyBudgetExhausted('W1 complete call guard exhausted')
        if 't' in record['H1_reference_record']:record['H1_reference_record']['t']=t
        self.records.append(record)
        return solved['assignments']
