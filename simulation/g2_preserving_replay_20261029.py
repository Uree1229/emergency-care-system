"""Detached-input G5 replay; frozen G2 and earlier evidence stay unchanged."""
import argparse
from copy import deepcopy
import json
from pathlib import Path

from . import final_assignment_engine_20261028 as controls
from .g2_preserving_assignment_20261029 import G2PreservingAssignmentRule, CONSTRAINTS
from .map_replay import ReplaySimulation, RouteSnapshot, validate_scenario
from .online_allocation_engine import OnlineAllocationReplay, _ObservedCPSATBatch, SOURCE_EXPERIMENT
from .sim import Policy, Simulation

EXPERIMENT_ID = 'g2_preserving_20261029'
EXECUTION_CONTRACT = dict(inquiry_policy='fixed_three_with_first_acceptance_extra_wait_two_minutes',
    joint_scope='currently_ready_same_minute_received_accepted_offers_only',
    constraints=dict(CONSTRAINTS), total_max_work_units=2048, safety_seconds=10.0,
    seed='unchanged_G2_or_proven_equivalent_caller_baseline_shortcut',
    future_events_supplied_to_rule=False, simulation_clock='integer_minute',
    wall_time_added_to_simulation_clock=False, whole_episode_nonregression_guaranteed=False,
    whole_episode_priority4_nonregression_guaranteed=False, whole_episode_optimal_proven=False,
    clinical_priority_policy_validated=False)
ASSUMPTIONS = [
    'G5 locks G2 selected IDs, experimental weight-4 destinations, hospital resource totals and individual travel upper bounds in the current snapshot.',
    'Singleton or all-assigned caller baselines are logically equivalent G2 seed shortcuts; no H1 execution is fabricated for them.',
    'G2 parent search and G5 partial-node refinement share a total 2048-unit deterministic work budget.',
    'Current resource footprint equality does not guarantee equal future releases or whole-episode outcomes; all gates require full replay.',
    'Experimental weights 1, 2, 4 are simulation settings, not validated clinical severity or priority rules.',
]


class G2PreservingReplay(OnlineAllocationReplay):
    def __init__(self, scenario, policy, routing=None):
        super().__init__(scenario, policy, routing=routing)
        self.preserving_records = []

    def resolve_batch(self, t):
        if not self.ready:
            return
        ready = list(self.ready)
        inputs = self._observed_input(ready, t)
        rule = self.policy.rule
        if rule.code != 'G5':
            raise ValueError('G5 rule required')
        before, event_offset, seconds = len(rule.records), len(self.log), self.decision_seconds
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
            raise AssertionError('G5 decision and actual commitments differ')
        record = deepcopy(records[0])
        record['decision_index'] = len(self.allocation_decisions)
        self.preserving_records.append(record)
        self.allocation_decisions.append(dict(t=t, kind='batch', rule='G5',
            scope='current_same_minute_accepted_offers', inputs=inputs, assignments=actual,
            objective=self._objective(inputs, actual), method='G2_preserving_pareto',
            elapsed_seconds=self.decision_seconds-seconds, optimal_proven=False,
            preserving_record_index=len(self.preserving_records)-1))
        self.decision_batches.append(dict(t=t, patients=[p.id for p, _ in ready],
            method='G2_preserving_pareto', assignments=deepcopy(actual),
            scope='same_minute_ready_patients_only'))

    def run_replay(self):
        value = ReplaySimulation.run_replay(self)
        value.update(allocation_decisions=self.allocation_decisions,
            preserving_records=self.preserving_records, solver_records=[],
            source_experiment=SOURCE_EXPERIMENT, comparison_id=EXPERIMENT_ID,
            execution_contract=deepcopy(EXECUTION_CONTRACT))
        value['metrics'].update(allocation_wall_seconds=self.decision_seconds,
            allocation_decisions=len(self.allocation_decisions),
            preserving_calls=len(self.preserving_records), solver_calls=0,
            solver_wall_seconds=0, solver_fallbacks=0, whole_episode_optimal_proven=False)
        return value


def run_preserving_replay(scenario, routing=None):
    validate_scenario(scenario)
    original = deepcopy(scenario)
    fingerprint = controls.canonical_sha256(original)
    template, route_sha = controls._routing_template(routing)
    physical = deepcopy(original)
    physical.update(online_allocation_comparison=True, source_experiment=SOURCE_EXPERIMENT,
                    comparison_id=EXPERIMENT_ID)
    physical.setdefault('assumptions', []).extend(ASSUMPTIONS)
    isolated = RouteSnapshot(connector_speed_kmh=template.connector_speed_kmh)
    isolated.routes = deepcopy(template.routes)
    policy = Policy(rule=G2PreservingAssignmentRule(), k=3, wait=2, notify=True, name='G5')
    branch = G2PreservingReplay(deepcopy(physical), policy, routing=isolated).run_replay()
    spec = policy.spec()
    spec['g2_preserving'] = deepcopy(EXECUTION_CONTRACT)
    branch.update(id='G5', label='G2 성과 보존 제약·이동 단축 G5', policy=spec,
        original_scenario_sha256=fingerprint, routing_input_sha256=route_sha)
    if controls.canonical_sha256(scenario) != fingerprint:
        raise AssertionError('caller input changed')
    return dict(schema_version=1, comparison_id=EXPERIMENT_ID, source_experiment=SOURCE_EXPERIMENT,
        original_scenario=original, scenario=physical, input_scenario_sha256=fingerprint,
        original_scenario_sha256=fingerprint, routing_input_sha256=route_sha,
        branches=[branch], execution_order=['G5'],
        duration_minutes=max((e['t'] for e in branch['events']), default=0),
        routing_summary=dict(provider='synthetic', scope='independent_per_branch',
            external_route_provider_allowed=False, live_traffic=False, ambulance_priority=False,
            shared_mutable_routing_cache=False, by_branch={'G5':isolated.summary(scenario_only=True)}))


def audit_preserving_replay(replay, *, source_scenario=None):
    from .g2_preserving_audit_20261029 import audit_replay
    return audit_replay(replay, source_scenario=source_scenario)


def main(argv=None):
    parser = argparse.ArgumentParser(description='G2 배정 성과 보존 제약 G5 실험 재생')
    parser.add_argument('--input', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    target = Path(args.output)
    if target.exists():
        parser.error('기존 결과는 덮어쓰지 않습니다. 새 output 경로를 지정하세요.')
    try:
        source = json.loads(Path(args.input).read_text(encoding='utf-8-sig'))
        replay = run_preserving_replay(source)
        audit = audit_preserving_replay(replay, source_scenario=source)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('x', encoding='utf-8') as stream:
            json.dump(dict(synthetic=True, operational_adoption=False, replay=replay, audit=audit),
                      stream, ensure_ascii=False, indent=2)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    print(json.dumps(dict(passed=audit['passed'], rule='G5', output=str(target.resolve())), ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
