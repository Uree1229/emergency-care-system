"""Separate G2 replay with unchanged fresh R0/R1/H1 controls when requested."""
from copy import deepcopy
import math

from .arrival_guard_priority_policy_20261028 import ArrivalGuardPriorityAssignmentRule,GUARD_RULE
from . import final_assignment_engine_20261028 as controls_engine
from .map_replay import ReplaySimulation,RouteSnapshot,validate_scenario
from .online_allocation_engine import OnlineAllocationReplay,_ObservedCPSATBatch,SOURCE_EXPERIMENT
from .sim import Policy,Simulation

EXPERIMENT_ID='arrival_guard_priority_20261028'
GUARD_POLICY_SPEC=dict(id='G2',label='인원 순증·동수 weight4 보호 공동 배정 · G2',rule='G2',k=3,wait=2,notify=True,
    parent_h1_max_moves=2048,parent_h1_safety_seconds=10.0,guard_rule=GUARD_RULE,
    experimental_protected_priority_weight=4,current_H1_objective_changed=True,
    whole_episode_nonregression_guaranteed=False,whole_episode_priority4_nonregression_guaranteed=False)
ASSUMPTION_SUFFIX=[
    'G2는 현재 caller-order 거리순 B보다 가중 미배정이 늘지 않고 인원이 늘거나 같은 인원에서 weight4 선정 수가 늘 때만 원 H1 제안을 쓴다.',
    '가중치4는 실험 가중치이며 임상 응급도 판정이 아니다. 같은 인원에서 weight1과2만 교환하는 원 H1 제안은 거절될 수 있다.',
    '기준안은 같은 현재 공동 입력의 거리순이며 실제 R1 전체 과정의 반사실 재생이 아니다.',
    '고정3 문의·첫 수용 뒤 최대2분 대기·알림과 외부 잠재 환경은 동일하며 배정 후 자원과 회신은 달라질 수 있다.',
    '변경된 현재 배치 목적은 전체 성공·weight4 성공·환자별 도착 비퇴행 또는 온라인 최적성을 보장하지 않는다.',
]
EXECUTION_CONTRACT=dict(inquiry_policy='fixed_three_with_first_acceptance_extra_wait_two_minutes',
    joint_scope='currently_ready_same_minute_received_accepted_offers_only',guard_rule=GUARD_RULE,
    parent_h1_max_moves=2048,parent_h1_safety_seconds=10.0,experimental_protected_priority_weight=4,
    objective_change_disclosed=True,old_H1_local_objective_nonregression_claimed=False,
    whole_episode_nonregression_guaranteed=False,whole_episode_priority4_nonregression_guaranteed=False,
    whole_episode_optimal_proven=False,simulation_clock='integer_minute',wall_time_added_to_simulation_clock=False,
    information_observation='complete_current_resources_and_received_offers; no future schedules passed to allocation rule')


def _policy_spec(policy):
    value=policy.spec()
    value['arrival_guard_priority']=deepcopy({k:v for k,v in GUARD_POLICY_SPEC.items() if k not in ('id','label','rule','k','wait','notify')})
    return value


class ArrivalGuardPriorityReplay(OnlineAllocationReplay):
    def __init__(self,scenario,policy,routing=None):
        super().__init__(scenario,policy,routing=routing)
        self.guard_records=[]

    def resolve_batch(self,t):
        if not self.ready:return
        ready=list(self.ready);inputs=self._observed_input(ready,t);rule=self.policy.rule
        if rule.code!='G2':raise ValueError('candidate replay accepts only G2')
        offset=len(rule.records);event_offset=len(self.log);seconds=self.decision_seconds
        try:
            self.policy.rule=_ObservedCPSATBatch(rule,inputs)
            Simulation.resolve_batch(self,t)
        finally:self.policy.rule=rule
        actual={patient.id:None for patient,_ in ready}
        for event in self.log[event_offset:]:
            if event['type']=='commit' and event.get('patient') in actual:actual[event['patient']]=event['hospital']
        rows=rule.records[offset:]
        if len(rows)!=1 or rows[0]['actual_plan']!=actual:raise AssertionError('guard plan and commit differ')
        index=len(self.allocation_decisions);record=deepcopy(rows[0]);record['decision_index']=index
        decision=dict(t=t,kind='batch',rule='G2',scope='current_same_minute_accepted_offers',inputs=inputs,
            assignments=actual,objective=self._objective(inputs,actual),method='guarded_joint',
            elapsed_seconds=self.decision_seconds-seconds,optimal_proven=False,
            guard_record_index=len(self.guard_records),guard_status=record['status'])
        self.guard_records.append(record);self.allocation_decisions.append(decision)
        self.decision_batches.append(dict(t=t,patients=[p.id for p,_ in ready],
            candidate_combinations=math.prod(len(options)+1 for _,options in ready),
            method='guarded_joint',scope='same_minute_ready_patients_only',assignments=actual))

    def run_replay(self):
        value=ReplaySimulation.run_replay(self)
        value.update(allocation_decisions=self.allocation_decisions,solver_records=[],heuristic_records=[],
            guard_records=self.guard_records,source_experiment=SOURCE_EXPERIMENT,comparison_id=EXPERIMENT_ID,
            execution_contract=deepcopy(EXECUTION_CONTRACT))
        value['metrics'].update(allocation_wall_seconds=self.decision_seconds,
            allocation_decisions=len(self.allocation_decisions),solver_calls=0,solver_wall_seconds=0,
            solver_fallbacks=0,solver_optimal_batches=0,heuristic_calls=0,heuristic_wall_seconds=0,
            heuristic_optimal_batches=0,guard_calls=len(self.guard_records),
            guard_wall_seconds=sum(r['wall_seconds'] for r in self.guard_records),
            parent_h1_proposal_calls=len(self.guard_records),
            parent_h1_proposal_wall_seconds=sum(r['original_h1_record']['wall_seconds'] for r in self.guard_records),
            optimality_scope='current_same_minute_accepted_offers_only',whole_episode_optimal_proven=False)
        return value


def run_guard_replay(scenario,routing=None):
    validate_scenario(scenario)
    original=deepcopy(scenario);fingerprint=controls_engine.canonical_sha256(original)
    template,route_sha=controls_engine._routing_template(routing)
    frozen=deepcopy(original);frozen.update(online_allocation_comparison=True,
        source_experiment=SOURCE_EXPERIMENT,comparison_id=EXPERIMENT_ID)
    frozen.setdefault('assumptions',[]).extend(ASSUMPTION_SUFFIX)
    isolated=RouteSnapshot(connector_speed_kmh=template.connector_speed_kmh);isolated.routes=deepcopy(template.routes)
    policy=Policy(rule=ArrivalGuardPriorityAssignmentRule(),k=3,wait=2,notify=True,name='G2')
    replay=ArrivalGuardPriorityReplay(deepcopy(frozen),policy,routing=isolated)
    branch=replay.run_replay()
    branch.update(id='G2',label=GUARD_POLICY_SPEC['label'],policy=_policy_spec(policy),
        original_scenario_sha256=fingerprint,routing_input_sha256=route_sha)
    if controls_engine.canonical_sha256(scenario)!=fingerprint:raise AssertionError('caller input changed')
    return dict(schema_version=2,source_experiment=SOURCE_EXPERIMENT,comparison_id=EXPERIMENT_ID,
        original_scenario=original,scenario=frozen,input_scenario_sha256=fingerprint,
        original_scenario_sha256=fingerprint,routing_input_sha256=route_sha,branches=[branch],execution_order=['G2'],
        duration_minutes=max((e['t'] for e in branch['events']),default=0),
        routing_summary=dict(provider='synthetic',scope='independent_per_branch',by_branch={'G2':isolated.summary(scenario_only=True)},
            live_traffic=False,ambulance_priority=False,shared_mutable_routing_cache=False,external_route_provider_allowed=False))


def run_comparison(scenario,include_controls=True,control_execution_order=None):
    if type(include_controls) is not bool:raise ValueError('include_controls must be bool')
    if not include_controls and control_execution_order is not None:raise ValueError('control order without controls')
    before=controls_engine.canonical_sha256(scenario)
    controls=controls_engine.run_final_assignment_replay(scenario,execution_order=control_execution_order) if include_controls else None
    candidate=run_guard_replay(scenario)
    if controls_engine.canonical_sha256(scenario)!=before:raise AssertionError('comparison changed caller input')
    return dict(schema_version=1,comparison_id=EXPERIMENT_ID,controls=controls,candidate=candidate,input_sha256=before)
