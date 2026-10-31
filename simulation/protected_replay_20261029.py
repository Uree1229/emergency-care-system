"""G3 current-opportunity protection on the unchanged inquiry/transport engine.

Only a copied current accepted-offer snapshot reaches the assignment rule.
G3 is a separately named research policy, not a correction to G2's declared
priority objective and not a clinical decision policy or online guarantee.
"""
from copy import deepcopy
import argparse
import json
from pathlib import Path

from . import final_assignment_engine_20261028 as controls
from .map_replay import ReplaySimulation, RouteSnapshot, validate_scenario
from .online_allocation_engine import OnlineAllocationReplay, _ObservedCPSATBatch, SOURCE_EXPERIMENT
from .protected_assignment_20261029 import ProtectedAssignmentRule
from .sim import Policy, Simulation

EXPERIMENT_ID = 'protected_assignment_20261029'
EXECUTION_CONTRACT = dict(
    inquiry_policy='fixed_three_with_first_acceptance_extra_wait_two_minutes',
    joint_scope='currently_ready_same_minute_received_accepted_offers_only',
    current_baseline_assigned_IDs_protected=True,
    same_count_individual_travel_nonincrease=True,
    whole_episode_nonregression_guaranteed=False,
    clinical_priority_policy=False,
    input_scope='detached_current_offers_resources_travel_only',
    future_events_supplied_to_rule=False,
    max_moves=2048, safety_seconds=10.0,
    simulation_clock='integer_minute', wall_time_added_to_simulation_clock=False,
)


class ProtectedAllocationReplay(OnlineAllocationReplay):
    def __init__(self, scenario, policy, routing=None):
        super().__init__(scenario, policy, routing=routing)
        self.protected_records = []

    def resolve_batch(self, t):
        if not self.ready:
            return
        ready = list(self.ready)
        inputs = self._observed_input(ready, t)
        rule = self.policy.rule
        if rule.code != 'G3':
            raise ValueError('protected replay requires G3')
        before = len(rule.records)
        event_offset, seconds = len(self.log), self.decision_seconds
        try:
            self.policy.rule = _ObservedCPSATBatch(rule, inputs)
            Simulation.resolve_batch(self, t)
        finally:
            self.policy.rule = rule
        actual = {p.id: None for p, _ in ready}
        for event in self.log[event_offset:]:
            if event['type'] == 'commit' and event.get('patient') in actual:
                actual[event['patient']] = event['hospital']
        records = rule.records[before:]
        if len(records) != 1 or records[0]['actual_plan'] != actual:
            raise AssertionError('protected proposal and actual commitments differ')
        record = deepcopy(records[0])
        record['decision_index'] = len(self.allocation_decisions)
        self.protected_records.append(record)
        self.allocation_decisions.append(dict(
            t=t, kind='batch', rule='G3', scope='current_same_minute_accepted_offers',
            inputs=inputs, assignments=actual, objective=self._objective(inputs, actual),
            method='protected_augmentation', elapsed_seconds=self.decision_seconds-seconds,
            optimal_proven=False, protected_record_index=len(self.protected_records)-1,
        ))
        self.decision_batches.append(dict(t=t, patients=[p.id for p,_ in ready],
            method='protected_augmentation', scope='same_minute_ready_patients_only',
            assignments=deepcopy(actual)))

    def run_replay(self):
        value = ReplaySimulation.run_replay(self)
        value.update(allocation_decisions=self.allocation_decisions,
                     protected_records=self.protected_records, solver_records=[],
                     source_experiment=SOURCE_EXPERIMENT, comparison_id=EXPERIMENT_ID,
                     execution_contract=deepcopy(EXECUTION_CONTRACT))
        value['metrics'].update(
            allocation_wall_seconds=self.decision_seconds,
            allocation_decisions=len(self.allocation_decisions),
            protected_calls=len(self.protected_records),
            solver_calls=0, solver_wall_seconds=0, solver_fallbacks=0,
            whole_episode_optimal_proven=False,
        )
        return value


def run_protected_replay(scenario, routing=None):
    validate_scenario(scenario)
    fingerprint = controls.canonical_sha256(scenario)
    original = deepcopy(scenario)
    template, route_sha = controls._routing_template(routing)
    current = deepcopy(original)
    current.update(online_allocation_comparison=True, source_experiment=SOURCE_EXPERIMENT,
                   comparison_id=EXPERIMENT_ID)
    current.setdefault('assumptions', []).extend([
        'G3 preserves every assigned ID of the caller-order nearest plan on its own current snapshot.',
        'Equal-count changes cannot increase any protected patient current travel time.',
        'Admission gains may require detours; positive detours are minimized, not clinically bounded.',
        'This changes the priority-only replacement policy of G2; weight-4 patient tradeoffs must be reported.',
        'Current opportunity protection is not a whole-episode R1 or G2 outcome guarantee.',
    ])
    isolated = RouteSnapshot(connector_speed_kmh=template.connector_speed_kmh)
    isolated.routes = deepcopy(template.routes)
    rule = ProtectedAssignmentRule()
    policy = Policy(rule=rule, k=3, wait=2, notify=True, name='G3')
    replay = ProtectedAllocationReplay(deepcopy(current), policy, routing=isolated)
    branch = replay.run_replay()
    spec = policy.spec()
    spec['protected_assignment'] = deepcopy(EXECUTION_CONTRACT)
    branch.update(id='G3', label='현재 수용 기회 보호·추가 배정 G3', policy=spec,
                  original_scenario_sha256=fingerprint, routing_input_sha256=route_sha)
    if controls.canonical_sha256(scenario) != fingerprint:
        raise AssertionError('protected replay changed caller input')
    return dict(schema_version=1, source_experiment=SOURCE_EXPERIMENT,
        comparison_id=EXPERIMENT_ID, original_scenario=original, scenario=current,
        input_scenario_sha256=fingerprint, original_scenario_sha256=fingerprint,
        routing_input_sha256=route_sha, branches=[branch], execution_order=['G3'],
        routing_summary=dict(provider='synthetic', live_traffic=False,
            external_route_provider_allowed=False, shared_mutable_routing_cache=False,
            by_branch={'G3':isolated.summary(scenario_only=True)}))


def main(argv=None):
    parser=argparse.ArgumentParser(description='현재 수용 기회 보호 G3 연구 후보 실행')
    parser.add_argument('--input',required=True,help='합성 시나리오 JSON')
    parser.add_argument('--output',required=True,help='새 결과 JSON 경로. 기존 결과를 덮어쓰지 않음')
    args=parser.parse_args(argv)
    target=Path(args.output)
    if target.exists():
        parser.error('기존 결과는 덮어쓰지 않습니다. 새 output 경로를 지정하세요.')
    try:
        source=json.loads(Path(args.input).read_text(encoding='utf-8-sig'))
        replay=run_protected_replay(source)
        from .protected_replay_audit_20261029 import audit_replay
        audit=audit_replay(replay,source_scenario=source)
        target.parent.mkdir(parents=True,exist_ok=True)
        with target.open('x',encoding='utf-8') as stream:
            json.dump(dict(synthetic=True,operational_adoption=False,replay=replay,audit=audit),
                      stream,ensure_ascii=False,indent=2)
    except (OSError,ValueError) as error:
        parser.error(str(error))
    metrics=audit['branch_metrics']['G3']
    print(json.dumps(dict(passed=audit['passed'],rule='G3',patients=metrics['patients'],
                         unassigned=metrics['unassigned'],not_arrived=metrics['not_arrived'],
                         output=str(target.resolve())),ensure_ascii=False))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
