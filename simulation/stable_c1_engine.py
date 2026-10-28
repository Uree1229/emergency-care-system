"""Compare equal-objective C1 stability without changing frozen simulations."""
from __future__ import annotations

from copy import deepcopy

from .h1_online_engine import _policy_spec
from .heuristic_assignment import HeuristicAssignmentRule
from .map_replay import RouteSnapshot, validate_scenario
from .responsive_confirmation_engine import DEFAULT_RESPONSIVE_POLICIES, ResponsiveConfirmationReplay
from .sim import Policy
from .stable_cp_sat_assignment import StableCPSATAssignmentRule


EXPERIMENT_ID='stable_c1_20261015'
SOURCE_EXPERIMENT='stable_c1_comparison'
DEFAULT_STABLE_C1_POLICIES=tuple(dict(deepcopy(p),stable_ties=False) for p in DEFAULT_RESPONSIVE_POLICIES)+tuple(
    dict(deepcopy(next(p for p in DEFAULT_RESPONSIVE_POLICIES if p['id']==base)),
         id='stable_'+base,label='동률 안정화 · '+base,stable_ties=True)
    for base in ('observed_C1','protected_C1','responsive_C1'))


def stability_metrics(branch):
    records=[r['stability'] for r in branch['solver_records'] if 'stability' in r]
    return dict(stability_decisions=len(records),
        stability_tie_replacements=sum(r['applied'] for r in records),
        stability_unchanged_ties=sum(r['reason']=='equal_objective_unchanged' for r in records),
        stability_solver_better=sum(r['reason']=='solver_strictly_better' for r in records),
        stability_wall_seconds=sum(r['elapsed_seconds'] for r in records))


class StableC1Replay(ResponsiveConfirmationReplay):
    def __init__(self,scenario,policy,routing=None,mode='observed',protection=True,
                 prefetch=False,watch=False,grace_minutes=3,stable_ties=False):
        if type(stable_ties) is not bool:
            raise ValueError('stable_ties must be boolean')
        if stable_ties and not isinstance(policy.rule,StableCPSATAssignmentRule):
            raise ValueError('stable C1 branch requires the stable C1 rule')
        if not stable_ties and isinstance(policy.rule,StableCPSATAssignmentRule):
            raise ValueError('unmodified branch cannot use stable C1')
        self.stable_ties_enabled=stable_ties
        super().__init__(scenario,policy,routing=routing,mode=mode,protection=protection,
                         prefetch=prefetch,watch=watch,grace_minutes=grace_minutes)

    def run_replay(self):
        branch=super().run_replay()
        branch.update(comparison_id=EXPERIMENT_ID,source_experiment=SOURCE_EXPERIMENT,
                      stable_ties_enabled=self.stable_ties_enabled)
        branch['metrics'].update(stability_metrics(branch))
        return branch


def run_stable_c1_replay(scenario,policies=None,execution_order=None,routing=None):
    validate_scenario(scenario)
    specs=deepcopy(list(DEFAULT_STABLE_C1_POLICIES if policies is None else policies))
    ids=[s['id'] for s in specs]
    if not ids or len(set(ids))!=len(ids):raise ValueError('unique nonempty branch IDs required')
    for s in specs:
        if s.get('mode') not in ('perfect','observed') or s.get('rule') not in ('R1','H1','C1'):
            raise ValueError('unsupported information mode or allocation rule')
        if any(type(s.get(k)) is not bool for k in ('protection','prefetch','watch','stable_ties')):
            raise ValueError('explicit boolean intervention flags required')
        if s['stable_ties'] and s['rule']!='C1':raise ValueError('tie stability applies to C1 only')
        if (s['protection'] or s['prefetch'] or s['watch']) and (s['mode']!='observed' or s['rule'] not in ('H1','C1')):
            raise ValueError('confirmation intervention requires observed H1/C1')
        if (s['prefetch'] or s['watch']) and not s['protection']:
            raise ValueError('responsive confirmation requires protection')
        if s.get('grace_minutes')!=(3 if s['protection'] else None):
            raise ValueError('grace window differs from fixed intervention')
        if s.get('observed_inquiry') is not None or s.get('adaptive_inquiry') is not None:
            raise ValueError('fixed inquiry policy required')
    common=('k','wait','notify','near_minutes','expand_after','inquiry_ranking','load_weight','load_window',
            'priority_order','high_priority_hedge_after')
    if any(any(s.get(k)!=specs[0].get(k) for k in common) for s in specs):
        raise ValueError('all branches must share inquiry and notification settings')
    order=list(ids if execution_order is None else execution_order)
    if len(order)!=len(ids) or set(order)!=set(ids):raise ValueError('execution_order must contain every branch exactly once')
    frozen=deepcopy(scenario)
    frozen.update(comparison_id=EXPERIMENT_ID,source_experiment=SOURCE_EXPERIMENT)
    frozen.setdefault('assumptions',[]).extend([
        'C1 동률 안정화는 현재 미배정 우선순위 합·가중 이동시간을 모두 유지하는 기준안만 선택한다.',
        '기준안은 높은 우선순위·빠른 요청 시각·환자 ID 순으로 현재 가능한 가까운 병원을 선택한다.',
        '기준안보다 목적값이 좋은 C1 해는 유지한다. 기준안과 동률이 아닌 해 사이의 전역 정규화는 주장하지 않는다.',
        '병상 부족의 대기 자체를 없애지 않는다. 같은 목적값에서 선택 순서를 안정화한 효과와 대기 환자 변경을 따로 기록한다.',
    ])
    routing=routing or RouteSnapshot();routing.used_keys.clear()
    by_id={}
    for branch_id in order:
        s=deepcopy(next(s for s in specs if s['id']==branch_id));s.pop('id')
        label,mode=s.pop('label'),s.pop('mode')
        flags={key:s.pop(key) for key in ('protection','prefetch','watch','grace_minutes','stable_ties')}
        if s['rule']=='H1':
            s['rule']=HeuristicAssignmentRule(time_limit_seconds=s.pop('time_limit_seconds',.05),max_moves=s.pop('max_moves',2048))
        elif s['rule']=='C1' and flags['stable_ties']:
            s['rule']=StableCPSATAssignmentRule(time_limit_seconds=s.pop('time_limit_seconds',.05),
                seed=s.pop('seed',0),require_backend=s.pop('require_backend',True))
        policy=Policy(**s,name=branch_id)
        simulation=StableC1Replay(deepcopy(frozen),policy,routing=routing,mode=mode,**flags)
        branch=simulation.run_replay();branch.update(id=branch_id,label=label,policy=_policy_spec(policy))
        by_id[branch_id]=branch
    return dict(schema_version=1,experiment_id=EXPERIMENT_ID,scenario=frozen,
        branches=[by_id[i] for i in ids],execution_order=order,routing_summary=routing.summary(scenario_only=True))
