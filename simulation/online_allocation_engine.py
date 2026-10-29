"""Instrument the existing full replay without changing its frozen engine.

All allocation rules share the same inquiry/transport environment. Decision
audits contain an explicit whitelist of information observed at that minute,
never pending response times, future patients or resource release schedules.
CP-SAT proves an objective only for its current accepted-offer batch; it does
not prove that the whole online episode has an optimal outcome.
"""
from __future__ import annotations

import copy
import math
from types import SimpleNamespace

from .map_replay import ReplaySimulation, RouteSnapshot, validate_scenario
from .sim import Policy, Simulation


SOURCE_EXPERIMENT = 'online_allocation_comparison'
DEFAULT_ONLINE_POLICIES = (
    dict(id='R0', label='첫 수용 순서', rule='R0', k=3, wait=2, notify=True),
    dict(id='R1', label='예상 도착 우선', rule='R1', k=3, wait=2, notify=True),
    dict(id='R2', label='자원 경합 고려', rule='R2', k=3, wait=2, notify=True),
    dict(id='R3', label='공동 배정 · 제한 열거', rule='R3', k=3, wait=2, notify=True),
    dict(id='C1', label='공동 배정 · CP-SAT', rule='C1', k=3, wait=2,
         notify=True, time_limit_seconds=1.0, seed=0, require_backend=True),
)


class _ObservedCPSATBatch:
    """Expose only a copied, current accepted-offer view to the solver.

    The transport environment retains future schedules, but neither that
    environment nor its mutable Patient objects is passed to CP-SAT.
    """

    def __init__(self, rule, inputs):
        self.rule, self.inputs = rule, inputs

    def __getattr__(self, name):
        return getattr(self.rule, name)

    def decide_batch(self, _environment, _ready, t):
        positions = {row['id']: tuple(row['position']) for row in self.inputs['patients']}
        costs = {(row['patient'], row['hospital']): row['minutes']
                 for row in self.inputs['travel_minutes']}
        free = {(row['hospital'], row['resource']): row['free']
                for row in self.inputs['free_resources']}
        hospitals = {}
        for hid in {row['hospital'] for row in self.inputs['free_resources']} \
                | {row['hospital'] for row in self.inputs['travel_minutes']}:
            hospitals[hid] = SimpleNamespace(
                free=lambda resource, hid=hid: free.get((hid, resource), 0))
        view = SimpleNamespace(hospitals=hospitals,
                               position=lambda patient, _t: positions[patient.id],
                               travel=lambda patient, hid, _position: costs[(patient.id, hid)])
        observed = [(SimpleNamespace(id=row['id'], priority=row['priority'],
                                     demand=dict(row['demand']),
                                     caps=set(row['required_capabilities']),
                                     t_req=row['request_t'], loc=positions[row['id']]),
                     list(row['options'])) for row in self.inputs['patients']]
        return self.rule.decide_batch(view, observed, t)


class OnlineAllocationReplay(ReplaySimulation):
    """A real inquiry-to-treatment replay with current-input decision audits."""

    def __init__(self, scenario, policy, routing=None):
        super().__init__(scenario, policy, routing=routing)
        self.allocation_decisions = []
        self.solver_records = []

    def _observed_input(self, ready, t):
        rows, travel, quantities = [], [], {}
        # Preserve the actual caller order; R0/R1/R2 do not become joint rules.
        for patient, options in ready:
            position = self.position(patient, t)
            offered = list(dict.fromkeys(options))
            rows.append(dict(
                id=patient.id, priority=patient.priority,
                demand=dict(patient.demand),
                required_capabilities=sorted(patient.caps),
                position=list(position), request_t=patient.t_req,
                options=offered,
                accepted_at={hid: patient.accepted_at.get(hid) for hid in offered},
                valid_offer_expiries={hid: patient.valid.get(hid) for hid in offered},
            ))
            for hid in offered:
                hospital = self.hospitals[hid]
                travel.append(dict(patient=patient.id, hospital=hid,
                                   minutes=self.travel(patient, hid, position)))
                for resource in patient.demand:
                    quantities[(hid, resource)] = hospital.free(resource)
        hospitals = []
        for hid in sorted({row['hospital'] for row in travel}):
            hospital = self.hospitals[hid]
            resources = set(hospital.cap) | {resource for (offered_hid, resource) in quantities
                                              if offered_hid == hid}
            hospitals.append(dict(id=hid, capabilities=sorted(hospital.caps),
                                  capacity={resource: hospital.cap.get(resource, 0) for resource in sorted(resources)},
                                  used={resource: hospital.used(resource) for resource in sorted(resources)},
                                  committed={resource: hospital.expected(resource) for resource in sorted(resources)},
                                  queued={resource: hospital.waiting(resource) for resource in sorted(resources)}))
        return dict(patients=rows, hospitals=hospitals, travel_minutes=travel,
                    free_resources=[dict(hospital=hid, resource=resource, free=free)
                                    for (hid, resource), free in sorted(quantities.items())])

    @staticmethod
    def _objective(inputs, assignments):
        travel = {(row['patient'], row['hospital']): row['minutes']
                  for row in inputs['travel_minutes']}
        return dict(
            unassigned_priority_weight=sum(row['priority'] for row in inputs['patients']
                                           if assignments.get(row['id']) is None),
            priority_weighted_travel_minutes=sum(
                row['priority'] * travel[(row['id'], assignments[row['id']])]
                for row in inputs['patients'] if assignments.get(row['id']) is not None),
        )

    def _decide(self, patient, options, t):
        inputs = self._observed_input([(patient, options)], t)
        before = self.decision_seconds
        hid = super()._decide(patient, options, t)
        assignments = {patient.id: hid}
        method = {'R0': 'first_accept', 'R1': 'nearest_current_offer',
                  'R2': 'competition_aware'}.get(self.policy.rule.code, 'nearest_current_offer')
        self.allocation_decisions.append(dict(
            t=t, kind='single', rule=self.policy.rule.code,
            scope='current_patient_accepted_offers', inputs=inputs,
            assignments=assignments, objective=self._objective(inputs, assignments),
            method=method, elapsed_seconds=self.decision_seconds - before,
            optimal_proven=False,
        ))
        return hid

    def resolve_batch(self, t):
        if not self.ready:
            return
        ready = list(self.ready)
        inputs = self._observed_input(ready, t)
        rule = self.policy.rule
        previous_fallbacks = getattr(rule, 'fallbacks', 0)
        previous_records = len(getattr(rule, 'records', ()))
        log_offset, seconds = len(self.log), self.decision_seconds
        # Deliberately bypass ReplaySimulation.resolve_batch: its historical
        # enumeration label cannot describe a CP-SAT solve. The frozen base
        # Simulation still performs the exact same choose/commit/retry flow.
        try:
            if rule.code == 'C1':
                self.policy.rule = _ObservedCPSATBatch(rule, inputs)
            Simulation.resolve_batch(self, t)
        finally:
            self.policy.rule = rule
        actual = {patient.id: None for patient, _ in ready}
        for event in self.log[log_offset:]:
            if event['type'] == 'commit' and event.get('patient') in actual:
                actual[event['patient']] = event['hospital']
        fallback = getattr(rule, 'fallbacks', 0) > previous_fallbacks
        if rule.code == 'C1':
            method = 'cp_sat_fallback' if fallback else 'cp_sat'
        elif rule.code in ('R3', 'R4'):
            method = 'greedy_fallback' if fallback else 'exact_enumeration'
        else:
            method = 'joint_rule_fallback' if fallback else 'joint_rule'
        decision_index = len(self.allocation_decisions)
        decision = dict(
            t=t, kind='batch', rule=rule.code,
            scope='current_same_minute_accepted_offers', inputs=inputs,
            assignments=actual, objective=self._objective(inputs, actual),
            method=method, elapsed_seconds=self.decision_seconds - seconds,
            optimal_proven=rule.code in ('R3', 'R4') and not fallback,
        )
        records = getattr(rule, 'records', ())[previous_records:]
        for record in records:
            copied = copy.deepcopy(record)
            copied.update(t=t, decision_index=decision_index)
            decision['solver_record_index'] = len(self.solver_records)
            self.solver_records.append(copied)
            decision['optimal_proven'] = bool(copied.get('optimal_proven'))
            decision['solver_status'] = copied.get('status')
        self.allocation_decisions.append(decision)
        self.decision_batches.append(dict(
            t=t, patients=[patient.id for patient, _ in ready],
            candidate_combinations=math.prod(len(options) + 1 for _, options in ready),
            method=method, scope='same_minute_ready_patients_only',
            assignments=actual,
        ))

    def run_replay(self):
        branch = super().run_replay()
        branch.update(allocation_decisions=self.allocation_decisions,
                      solver_records=self.solver_records,
                      source_experiment=SOURCE_EXPERIMENT)
        branch['metrics'].update(
            allocation_wall_seconds=self.decision_seconds,
            allocation_decisions=len(self.allocation_decisions),
            solver_calls=len(self.solver_records),
            solver_wall_seconds=sum(record['wall_seconds'] for record in self.solver_records),
            solver_fallbacks=sum(bool(record['fallback']) for record in self.solver_records),
            solver_optimal_batches=sum(bool(record['optimal_proven']) for record in self.solver_records),
            optimality_scope='current_same_minute_accepted_offers_only',
            whole_episode_optimal_proven=False,
        )
        return branch


def _policy_spec(policy):
    spec = policy.spec()
    rule = policy.rule
    if rule.code == 'C1':
        spec['solver'] = dict(backend='ortools_cp_sat',
                              time_limit_seconds=rule.time_limit_seconds,
                              seed=rule.seed, num_search_workers=1,
                              require_backend=rule.require_backend)
    elif rule.code in ('R3', 'R4'):
        spec['enumeration_limit'] = rule.limit
    return spec


def run_online_replay(scenario, policies=None, routing=None):
    """Run independent branches; never write a DB or request a network route.

    A caller may explicitly supply a preconfigured shared routing snapshot.
    The default is the existing synthetic geometry and travel-time estimator.
    Runtime fields are measured wall times and intentionally nondeterministic.
    """
    validate_scenario(scenario)
    frozen = copy.deepcopy(scenario)
    frozen['online_allocation_comparison'] = True
    frozen['source_experiment'] = SOURCE_EXPERIMENT
    frozen.setdefault('assumptions', []).extend([
        '모든 기본 배정 분기는 문의 3곳·최대 대기 2분·동일 변경 대응을 사용한다.',
        'CP-SAT 최적성은 현재 유효 수용 응답이 있는 같은 분의 환자 묶음에만 해당하며 전체 이송 과정의 최적성을 뜻하지 않는다.',
        '환자·자원·응답·처치시간은 합성 설정이며 실제 응급의료체계 성과나 임상 예후 검증이 아니다.',
    ])
    routing = routing or RouteSnapshot()
    routing.used_keys.clear()
    branches, ids = [], set()
    for raw in policies if policies is not None else DEFAULT_ONLINE_POLICIES:
        spec = dict(raw)
        branch_id, label = spec.pop('id'), spec.pop('label')
        if branch_id in ids:
            raise ValueError(f'duplicate online branch ID: {branch_id}')
        ids.add(branch_id)
        policy = Policy(**spec, name=branch_id)
        simulation = OnlineAllocationReplay(copy.deepcopy(frozen), policy, routing=routing)
        branch = simulation.run_replay()
        branch.update(id=branch_id, label=label, policy=_policy_spec(policy))
        branches.append(branch)
    return dict(schema_version=2, source_experiment=SOURCE_EXPERIMENT,
                scenario=frozen, branches=branches,
                duration_minutes=max((event['t'] for branch in branches
                                      for event in branch['events']), default=0),
                routing_summary=routing.summary(scenario_only=True))
