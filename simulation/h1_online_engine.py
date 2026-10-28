"""Run H1 through the existing inquiry-to-arrival engine without changing it.

The previous experiments and their source locks remain untouched. H1 and C1
receive the same detached, current accepted-offer view. Heuristic records are
kept separately from CP-SAT proof records; neither proves an online optimum.
"""
from __future__ import annotations

import copy
import math

from .heuristic_assignment import HeuristicAssignmentRule
from .map_replay import ReplaySimulation, RouteSnapshot, validate_scenario
from .online_allocation_engine import (
    OnlineAllocationReplay, SOURCE_EXPERIMENT, _ObservedCPSATBatch,
    _policy_spec as existing_policy_spec,
)
from .sim import Policy, Simulation


EXPERIMENT_ID = 'h1_online_20261015'
DEFAULT_H1_POLICIES = (
    dict(id='R1', label='이동시간 우선 · 순차', rule='R1', k=3, wait=2, notify=True),
    dict(id='H1', label='공동 배정 · H1 휴리스틱', rule='H1', k=3, wait=2,
         notify=True, time_limit_seconds=.05, max_moves=2048),
    dict(id='C1', label='공동 배정 · CP-SAT', rule='C1', k=3, wait=2,
         notify=True, time_limit_seconds=.05, seed=0, require_backend=True),
)


def _policy_spec(policy):
    spec = existing_policy_spec(policy)
    if policy.rule.code == 'H1':
        spec['heuristic'] = dict(
            backend='bounded_joint_heuristic',
            time_limit_seconds=policy.rule.time_limit_seconds,
            max_moves=policy.rule.max_moves,
            time_budget_scope='optional_search_after_snapshot_and_baseline',
        )
    return spec


class H1OnlineReplay(OnlineAllocationReplay):
    """Preserve the frozen simulation while adding accurate H1 instrumentation."""

    def __init__(self, scenario, policy, routing=None):
        super().__init__(scenario, policy, routing=routing)
        self.heuristic_records = []

    def resolve_batch(self, t):
        if not self.ready:
            return
        ready = list(self.ready)
        inputs = self._observed_input(ready, t)
        rule = self.policy.rule
        if rule.code not in ('H1', 'C1'):
            raise ValueError('this comparison supports joint H1 and C1 only')
        record_offset = len(rule.records)
        fallback_before = rule.fallbacks
        event_offset, seconds = len(self.log), self.decision_seconds
        try:
            # This adapter copies only current offered edges, resource totals,
            # priorities and travel estimates. It exposes no response schedule.
            self.policy.rule = _ObservedCPSATBatch(rule, inputs)
            Simulation.resolve_batch(self, t)
        finally:
            self.policy.rule = rule
        actual = {patient.id: None for patient, _ in ready}
        for event in self.log[event_offset:]:
            if event['type'] == 'commit' and event.get('patient') in actual:
                actual[event['patient']] = event['hospital']
        records = rule.records[record_offset:]
        if len(records) != 1 or records[0]['assignments'] != actual:
            raise AssertionError('joint proposal and committed assignment disagree')
        fallback = rule.fallbacks > fallback_before
        method = 'heuristic' if rule.code == 'H1' else ('cp_sat_fallback' if fallback else 'cp_sat')
        decision_index = len(self.allocation_decisions)
        decision = dict(
            t=t, kind='batch', rule=rule.code,
            scope='current_same_minute_accepted_offers', inputs=inputs,
            assignments=actual, objective=self._objective(inputs, actual),
            method=method, elapsed_seconds=self.decision_seconds - seconds,
            optimal_proven=False,
        )
        record = copy.deepcopy(records[0])
        record.update(t=t, decision_index=decision_index)
        if rule.code == 'H1':
            decision['heuristic_record_index'] = len(self.heuristic_records)
            decision['heuristic_status'] = record['status']
            self.heuristic_records.append(record)
        else:
            decision['solver_record_index'] = len(self.solver_records)
            decision['solver_status'] = record['status']
            decision['optimal_proven'] = bool(record['optimal_proven'])
            self.solver_records.append(record)
        self.allocation_decisions.append(decision)
        self.decision_batches.append(dict(
            t=t, patients=[patient.id for patient, _ in ready],
            candidate_combinations=math.prod(len(options) + 1 for _, options in ready),
            method=method, scope='same_minute_ready_patients_only', assignments=actual,
        ))

    def run_replay(self):
        # Skip the frozen OnlineAllocationReplay aggregator: its solver-only
        # record schema is intentionally not changed for the new heuristic.
        branch = ReplaySimulation.run_replay(self)
        branch.update(
            allocation_decisions=self.allocation_decisions,
            solver_records=self.solver_records,
            heuristic_records=self.heuristic_records,
            source_experiment=SOURCE_EXPERIMENT, comparison_id=EXPERIMENT_ID,
        )
        branch['metrics'].update(
            allocation_wall_seconds=self.decision_seconds,
            allocation_decisions=len(self.allocation_decisions),
            solver_calls=len(self.solver_records),
            solver_wall_seconds=sum(r['wall_seconds'] for r in self.solver_records),
            solver_fallbacks=sum(bool(r['fallback']) for r in self.solver_records),
            solver_optimal_batches=sum(bool(r['optimal_proven']) for r in self.solver_records),
            heuristic_calls=len(self.heuristic_records),
            heuristic_wall_seconds=sum(r['wall_seconds'] for r in self.heuristic_records),
            heuristic_optimal_batches=0,
            optimality_scope='current_same_minute_accepted_offers_only',
            whole_episode_optimal_proven=False,
        )
        return branch


def run_h1_online_replay(scenario, policies=None, routing=None, execution_order=None):
    """Run fresh independent rules against the same potential environment.

    This has no UI, live DB or network side effects. Different assignments can
    cause subsequent offers and resource histories to diverge across branches.
    Execution order may rotate for wall-time comparisons; output order stays
    the order of the supplied policy specifications.
    """
    validate_scenario(scenario)
    specs = copy.deepcopy(list(DEFAULT_H1_POLICIES if policies is None else policies))
    if not specs or len({s['id'] for s in specs}) != len(specs):
        raise ValueError('nonempty unique policy IDs are required')
    if any(s['rule'] not in ('R1', 'H1', 'C1') for s in specs):
        raise ValueError('supported rules are R1, H1 and C1')
    common = ('k', 'wait', 'notify', 'near_minutes', 'expand_after', 'inquiry_ranking')
    if any(any(s.get(k) != specs[0].get(k) for k in common) for s in specs):
        raise ValueError('all branches must use identical inquiry and notification settings')
    if any(s.get('observed_inquiry') is not None or s.get('adaptive_inquiry') is not None for s in specs):
        raise ValueError('the first H1 online comparison uses a fixed inquiry policy')
    ids = [s['id'] for s in specs]
    order = list(ids if execution_order is None else execution_order)
    if len(order) != len(ids) or set(order) != set(ids):
        raise ValueError('execution_order must contain each policy ID exactly once')
    frozen = copy.deepcopy(scenario)
    frozen.update(online_allocation_comparison=True, source_experiment=SOURCE_EXPERIMENT,
                  comparison_id=EXPERIMENT_ID)
    frozen.setdefault('assumptions', []).extend([
        'H1은 현재 배치의 휴리스틱이며 전체 이송 또는 개별 배치의 최적성을 증명하지 않는다.',
        '같은 잠재 환경을 사용하지만 배정 이후의 자원·회신 목록은 분기별로 달라질 수 있다.',
        '환자·병원·자원·회신·처치시간·경로는 합성 입력이며 실제 임상 성과가 아니다.',
    ])
    routing = routing or RouteSnapshot()
    routing.used_keys.clear()
    by_id = {}
    for branch_id in order:
        spec = copy.deepcopy(next(s for s in specs if s['id'] == branch_id))
        spec.pop('id')
        label = spec.pop('label')
        if spec['rule'] == 'H1':
            spec['rule'] = HeuristicAssignmentRule(
                time_limit_seconds=spec.pop('time_limit_seconds', .05),
                max_moves=spec.pop('max_moves', 2048))
        policy = Policy(**spec, name=branch_id)
        simulation = H1OnlineReplay(copy.deepcopy(frozen), policy, routing=routing)
        branch = simulation.run_replay()
        branch.update(id=branch_id, label=label, policy=_policy_spec(policy))
        by_id[branch_id] = branch
    branches = [by_id[branch_id] for branch_id in ids]
    return dict(
        schema_version=2, source_experiment=SOURCE_EXPERIMENT, comparison_id=EXPERIMENT_ID,
        scenario=frozen, branches=branches, execution_order=order,
        duration_minutes=max((event['t'] for b in branches for event in b['events']), default=0),
        routing_summary=routing.summary(scenario_only=True),
    )
