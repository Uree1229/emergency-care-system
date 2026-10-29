"""Isolated, fixed-work R0/R1/H1 allocation comparison on one potential world.

All arms retain fixed-three inquiries, the same two-minute acceptance window,
and the existing transport/notification engine. H1 receives the inherited
detached current accepted-offer view. Its 2048 evaluated-plan budget is primary;
the ten-second cooperative safety guard must never determine a valid outcome.
The caller's scenario, policies, routing cache and frozen module registries are
not modified. No live server, database, app, route provider or network is used.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from .h1_online_engine import H1OnlineReplay, _policy_spec
from .heuristic_assignment import HeuristicAssignmentRule
from .map_replay import RouteSnapshot, validate_scenario
from .online_allocation_engine import SOURCE_EXPERIMENT
from .sim import Policy

EXPERIMENT_ID = 'final_assignment_20261028'
H1_MAX_MOVES = 2048
H1_SAFETY_SECONDS = 10.0
DEFAULT_FINAL_ASSIGNMENT_POLICIES = (
    dict(id='R0', label='회신순 · 현재 유효 수용 응답', rule='R0', k=3, wait=2, notify=True),
    dict(id='R1', label='거리순 · 현재 유효 수용 응답', rule='R1', k=3, wait=2, notify=True),
    dict(id='H1', label='H1 공동 배정 · 고정 2048회 탐색', rule='H1', k=3, wait=2,
         notify=True, time_limit_seconds=H1_SAFETY_SECONDS, max_moves=H1_MAX_MOVES),
)


class H1SafetyBudgetExhausted(RuntimeError):
    """A hardware deadline affected H1; this is not a valid performance result."""


def canonical_sha256(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        allow_nan=False, separators=(',', ':')).encode('utf-8')).hexdigest()


def _checked_policies(policies):
    values=copy.deepcopy(list(DEFAULT_FINAL_ASSIGNMENT_POLICIES if policies is None else policies))
    if not values or any(type(v) is not dict for v in values):
        raise ValueError('a nonempty list of policy specifications is required')
    if len({v.get('id') for v in values})!=len(values):
        raise ValueError('policy identifiers must be unique')
    for value in values:
        rule=value.get('rule')
        if rule not in ('R0','R1','H1') or value.get('id')!=rule:
            raise ValueError('only correctly labeled R0, R1 and H1 arms are supported')
        allowed={'id','label','rule','k','wait','notify'} | ({'time_limit_seconds','max_moves'} if rule=='H1' else set())
        if set(value)!=allowed or type(value['label']) is not str or not value['label']:
            raise ValueError('unexpected or missing policy fields')
        if type(value['k']) is not int or value['k']!=3 or type(value['wait']) is not int or value['wait']!=2 or value['notify'] is not True:
            raise ValueError('all arms must retain fixed-three, two-minute wait and notifications')
        if rule=='H1' and (type(value['max_moves']) is not int or value['max_moves']!=H1_MAX_MOVES
                or type(value['time_limit_seconds']) not in (int,float) or value['time_limit_seconds']!=H1_SAFETY_SECONDS):
            raise ValueError('H1 must use the prespecified 2048-plan budget and ten-second safety guard')
    return values


def _routing_template(routing):
    if routing is None:
        value=RouteSnapshot()
    elif type(routing) is RouteSnapshot:
        if routing.provider is not None:
            raise ValueError('live/custom route providers are not allowed in this controlled comparison')
        value=RouteSnapshot(connector_speed_kmh=routing.connector_speed_kmh)
        value.routes=copy.deepcopy(routing.routes)
    else:
        raise ValueError('only a fixed provider-free RouteSnapshot is supported')
    # Include the full initial route cache in its typed canonical representation.
    serialized=[dict(key=list(key),value=copy.deepcopy(item)) for key,item in sorted(value.routes.items())]
    fingerprint=canonical_sha256(dict(provider=None,connector_speed_kmh=value.connector_speed_kmh,routes=serialized))
    return value,fingerprint


class FinalAssignmentReplay(H1OnlineReplay):
    """Use frozen behavior, treating a H1 safety timeout as a technical failure."""

    def resolve_batch(self,t):
        before=len(self.heuristic_records)
        super().resolve_batch(t)
        for row in self.heuristic_records[before:]:
            if row['search_stop_reason'] in ('time_limit','time_limit_zero'):
                raise H1SafetyBudgetExhausted('H1 safety guard exhausted; no valid comparative performance outcome')
            if row['evaluated_moves']>H1_MAX_MOVES:
                raise AssertionError('H1 evaluated more than the declared fixed-work budget')

    def run_replay(self):
        value=super().run_replay()
        value['comparison_id']=EXPERIMENT_ID
        value['execution_contract']=dict(
            inquiry_policy='fixed_three_with_first_acceptance_extra_wait_two_minutes',
            joint_scope='currently_ready_same_minute_received_accepted_offers_only',
            H1_primary_budget='at_most_2048_deterministic_candidate_plan_evaluations',
            H1_safety_seconds=H1_SAFETY_SECONDS,H1_safety_exhaustion_invalidates_run=True,
            H1_previous_selected_50ms_version_reused_exactly=False,
            H1_algorithm_source_unchanged=True,whole_episode_optimal_proven=False,
            simulation_clock='integer_minute',wall_time_added_to_simulation_clock=False,
            information_observation='complete_current_resources_and_received_offers; no future schedules passed to allocation rule')
        return value


def run_final_assignment_replay(scenario,policies=None,routing=None,execution_order=None):
    """Run each allocation arm once on fresh copies of the same potential world.

    Actual resources and later acceptance offers may diverge as a consequence
    of different allocations; they are outcomes, not fixed post-policy inputs.
    Per-arm routing caches are fresh independent copies of one fixed template.
    """
    validate_scenario(scenario)
    specs=_checked_policies(policies)
    ids=[s['id'] for s in specs]
    order=list(ids if execution_order is None else execution_order)
    if len(order)!=len(ids) or set(order)!=set(ids):
        raise ValueError('execution_order must contain each requested arm exactly once')
    template,routing_input_sha256=_routing_template(routing)
    input_sha256=canonical_sha256(scenario)
    frozen=copy.deepcopy(scenario)
    frozen.update(online_allocation_comparison=True,source_experiment=SOURCE_EXPERIMENT,
        comparison_id=EXPERIMENT_ID)
    frozen.setdefault('assumptions',[]).extend([
        '세 규칙은 같은 고정3 문의·첫 수용 뒤 최대2분 추가대기·외부 잠재 사건·현재 관측 조건을 사용한다.',
        'R0/R1은 환자별 순차 확정, H1은 같은 분에 준비된 환자만 공동 확정한다.',
        '배정 이후 자원과 실제 회신은 결과로 달라질 수 있다. 사후 수용 목록을 동일하게 고정하지 않는다.',
        'H1은 기존 알고리즘에 2048회 탐색을 주고 10초는 실패 감지용 안전상한만 사용한다. 과거50ms버전과 구분한다.',
        '합성 입력의 결과이며 실제 병원 성능·임상 효과·전체 온라인 최적성의 검증이 아니다.',
    ])
    by_id={};routing_summaries={}
    for branch_id in order:
        spec=copy.deepcopy(next(v for v in specs if v['id']==branch_id))
        spec.pop('id');label=spec.pop('label')
        if branch_id=='H1':
            spec['rule']=HeuristicAssignmentRule(time_limit_seconds=spec.pop('time_limit_seconds'),max_moves=spec.pop('max_moves'))
        policy=Policy(**spec,name=branch_id)
        isolated=RouteSnapshot(connector_speed_kmh=template.connector_speed_kmh)
        isolated.routes=copy.deepcopy(template.routes)
        simulation=FinalAssignmentReplay(copy.deepcopy(frozen),policy,routing=isolated)
        branch=simulation.run_replay()
        branch.update(id=branch_id,label=label,policy=_policy_spec(policy),
            original_scenario_sha256=input_sha256,routing_input_sha256=routing_input_sha256)
        by_id[branch_id]=branch
        routing_summaries[branch_id]=isolated.summary(scenario_only=True)
    if canonical_sha256(scenario)!=input_sha256:
        raise AssertionError('caller scenario changed')
    branches=[by_id[branch_id] for branch_id in ids]
    return dict(schema_version=2,source_experiment=SOURCE_EXPERIMENT,comparison_id=EXPERIMENT_ID,
        scenario=frozen,original_scenario=copy.deepcopy(scenario),input_scenario_sha256=input_sha256,
        original_scenario_sha256=input_sha256,routing_input_sha256=routing_input_sha256,
        branches=branches,execution_order=order,
        duration_minutes=max((event['t'] for b in branches for event in b['events']),default=0),
        routing_summary=dict(provider='synthetic',scope='independent_per_branch',
            by_branch=routing_summaries,live_traffic=False,ambulance_priority=False,
            shared_mutable_routing_cache=False,external_route_provider_allowed=False))
