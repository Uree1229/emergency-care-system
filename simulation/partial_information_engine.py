"""Separate physical resources, delivered observations and confirmation gates.

This is a new synthetic experiment contract, not a change to the operational
server. A patient acceptance never refreshes resource counts. Missing/stale
counts require a separate resource response. Local commitments remain known.
"""
from __future__ import annotations

import copy
import math
import time

from .h1_online_engine import DEFAULT_H1_POLICIES, _policy_spec
from .heuristic_assignment import HeuristicAssignmentRule
from .information_observations import ResourceObservationStore
from .map_replay import ReplaySimulation, RouteSnapshot, validate_scenario
from .online_allocation_engine import OnlineAllocationReplay, _ObservedCPSATBatch
from .sim import Policy


EXPERIMENT_ID = 'partial_information_20261015'
DEFAULT_INFORMATION_POLICIES = tuple(
    dict(copy.deepcopy(spec), id=f'{mode}_{spec["id"]}',
         label=f'{mode} · {spec["label"]}', mode=mode)
    for mode in ('perfect', 'observed') for spec in DEFAULT_H1_POLICIES)


class PartialInformationReplay(ReplaySimulation):
    """Policy sees reports; the environment separately accepts and commits."""

    _objective = staticmethod(OnlineAllocationReplay._objective)

    def __init__(self, scenario, policy, routing=None, mode='observed'):
        if mode not in ('perfect', 'observed'):
            raise ValueError('information mode must be perfect or observed')
        super().__init__(scenario, policy, routing=routing)
        self.information_mode = mode
        self.observations = ResourceObservationStore(stale_after_minutes=10)
        self.info_config = copy.deepcopy(scenario.get('observation_config', {}))
        self._validate_information_config()
        self.allocation_decisions, self.solver_records, self.heuristic_records = [], [], []
        self.observation_records = []
        self._packet_sequence = self._request_sequence = 0
        self._report_queue, self._confirmation_pending = [], {}
        self._next_confirmation, self._information_t = {}, None
        if mode == 'observed':
            initial = self.info_config.get('initial_reports')
            if initial is None:
                initial = [dict(hospital=hid, resources=self._physical_report(hid), observed_at=0)
                           for hid in self.hospitals]
            for report in initial:
                self._receive_report(report['hospital'], report['resources'],
                                     report['observed_at'], 0, 'initial')

    def _validate_information_config(self):
        fields = {'feed_interval_minutes': 0, 'feed_delay_minutes': 0,
                  'confirmation_delay_minutes': 1, 'confirmation_timeout_minutes': 1}
        known = set(fields) | {'feed_missing_resources', 'confirmation_silent',
            'confirmation_missing_resources', 'initial_reports', 'feed_delay_by_source_minute'}
        if set(self.info_config) - known:
            raise ValueError('unknown observation configuration field')
        for key, minimum in fields.items():
            value = self.info_config.get(key, 1 if key == 'feed_interval_minutes' else
                6 if key == 'confirmation_timeout_minutes' else minimum)
            if type(value) is not int or value < minimum:
                raise ValueError('invalid observation configuration: ' + key)
        if type(self.info_config.get('confirmation_silent', False)) is not bool:
            raise ValueError('confirmation_silent must be boolean')
        for key in ('feed_missing_resources', 'confirmation_missing_resources'):
            value = self.info_config.get(key, [])
            if not isinstance(value, list) or any(not isinstance(r, str) or not r for r in value):
                raise ValueError('missing resources must be a list of resource names')
        delays = self.info_config.get('feed_delay_by_source_minute', {})
        if not isinstance(delays, dict) or any(not isinstance(k, str) or not k.isdigit()
                or type(v) is not int or v < 0 for k, v in delays.items()):
            raise ValueError('invalid per-source report delay')
        initial = self.info_config.get('initial_reports')
        if initial is not None and (not isinstance(initial, list) or any(
                not isinstance(r, dict) or set(r) != {'hospital', 'resources', 'observed_at'}
                or r['hospital'] not in self.hospitals for r in initial)):
            raise ValueError('invalid initial resource reports')
        if initial is not None:
            validator = ResourceObservationStore(stale_after_minutes=10)
            for index, report in enumerate(initial):
                validator.apply(report['hospital'], report['resources'],
                    observed_at=report['observed_at'], received_at=0, now=0,
                    sequence=index, source='initial')

    def ev(self, t, typ, **fields):
        super().ev(t, typ, **fields)
        if self.record:
            self.log[-1]['event_sequence'] = len(self.log) - 1

    def _physical_report(self, hid, missing=()):
        hospital = self.hospitals[hid]
        return {r: None if r in missing else dict(capacity=hospital.cap[r], used=hospital.used(r))
                for r in sorted(hospital.cap)}

    def _receive_report(self, hid, resources, observed_at, t, source, packet_sequence=None):
        if packet_sequence is None:
            packet_sequence = self._packet_sequence
            self._packet_sequence += 1
        applied = self.observations.apply(hid, resources, observed_at=observed_at,
            received_at=t, now=t, sequence=packet_sequence, source=source)
        self.ev(t, 'resource_report_received', hospital=hid, resources=copy.deepcopy(resources),
            observed_at=observed_at, received_at=t, packet_sequence=packet_sequence,
            source=source, applied=applied)
        self.observation_records.append(copy.deepcopy(self.log[-1]))

    def _deliver_reports(self, t):
        due = sorted((r for r in self._report_queue if r['receive_at'] <= t),
                     key=lambda r: (r['receive_at'], r['packet_sequence']))
        self._report_queue = [r for r in self._report_queue if r['receive_at'] > t]
        for report in due:
            self._receive_report(report['hospital'], report['resources'], report['observed_at'],
                                 t, 'feed', report['packet_sequence'])

    def before_tick(self, t):
        super().before_tick(t)
        if self.information_mode == 'observed':
            self._deliver_reports(t)

    def _information_cycle(self, t):
        if self.information_mode != 'observed' or self._information_t == t:
            return
        self._information_t = t
        # Process explicit resource replies after this tick's physical changes,
        # before querying patients. No future delivery or release is exposed.
        for hid, request in list(self._confirmation_pending.items()):
            if request['due_at'] > t:
                continue
            del self._confirmation_pending[hid]
            if self.info_config.get('confirmation_silent', False):
                self.ev(t, 'resource_confirmation_timeout', hospital=hid, request_id=request['id'])
            else:
                payload = self._physical_report(hid, self.info_config.get('confirmation_missing_resources', ()))
                self._receive_report(hid, payload, t, t, 'confirmation')
            self._next_confirmation[hid] = t + self.cfg['retry_wait']
        interval = self.info_config.get('feed_interval_minutes', 1)
        if interval and t % interval == 0:
            delays = self.info_config.get('feed_delay_by_source_minute', {})
            delay = delays.get(str(t), self.info_config.get('feed_delay_minutes', 0))
            for hid in self.hospitals:
                payload = self._physical_report(hid, self.info_config.get('feed_missing_resources', ()))
                self.ev(t, 'resource_report_captured', hospital=hid, resources=copy.deepcopy(payload),
                    observed_at=t, receive_at=t + delay, packet_sequence=self._packet_sequence)
                self._report_queue.append(dict(hospital=hid, resources=payload,
                    observed_at=t, receive_at=t + delay, packet_sequence=self._packet_sequence))
                self._packet_sequence += 1
            self._deliver_reports(t)

    def after_tick(self, t):
        self._information_cycle(t)
        super().after_tick(t)

    def _views(self, hid, resources, t):
        hospital = self.hospitals[hid]
        views = {}
        for r in resources:
            committed, queued = hospital.expected(r), hospital.waiting(r)
            if self.information_mode == 'perfect':
                capacity, used = hospital.cap.get(r, 0), hospital.used(r)
                views[r] = dict(capacity=capacity, used=used, committed=committed, queued=queued,
                    free=capacity - used - committed - queued, observed_at=t, received_at=t,
                    sequence=None, source='perfect_reference', status='fresh', age_minutes=0)
            else:
                views[r] = self.observations.get(hid, r, now=t, committed=committed, queued=queued)
        return views

    def _check(self, p, hid, t, purpose):
        views = self._views(hid, p.demand, t)
        shortage = any(v['status'] == 'fresh' and v['free'] < p.demand[r] for r, v in views.items())
        unknown = any(v['status'] == 'unknown' for v in views.values())
        stale = any(v['status'] == 'stale' for v in views.values())
        reason = 'FRESH_SHORTAGE' if shortage else 'UNKNOWN' if unknown else 'STALE' if stale else None
        eligible = not shortage if purpose == 'inquiry' else reason is None
        self.ev(t, 'information_check', patient=p.id, hospital=hid, purpose=purpose,
                eligible=eligible, reason=reason, resources=copy.deepcopy(views))
        return eligible, reason, views

    def rank_inquiry_candidates(self, p, loc, candidates, t):
        self._information_cycle(t)
        allowed = []
        for hid in candidates:
            eligible, reason, _ = self._check(p, hid, t, 'inquiry')
            if eligible:
                allowed.append(hid)
            else:
                self.ev(t, 'candidate_deferred', patient=p.id, hospital=hid, reason=reason)
        if not allowed and not p.asks:
            # This is transient availability, never structural NO_COMPATIBLE.
            p.retry_at = t + 1
        return allowed

    def send_asks(self, p, t, exclude=()):
        self._information_cycle(t)
        super().send_asks(p, t, exclude=exclude)

    def _request_resources(self, p, hid, t, reason):
        self.ev(t, 'information_wait', patient=p.id, hospital=hid, reason=reason)
        if self.information_mode != 'observed' or hid in self._confirmation_pending \
                or self._next_confirmation.get(hid, 0) > t:
            return
        self._request_sequence += 1
        silent = self.info_config.get('confirmation_silent', False)
        delay = self.info_config.get('confirmation_timeout_minutes', 6) if silent else \
            self.info_config.get('confirmation_delay_minutes', 1)
        self._confirmation_pending[hid] = dict(id=self._request_sequence, due_at=t + delay)
        self.ev(t, 'resource_confirmation_request', patient=p.id, hospital=hid,
                request_id=self._request_sequence)

    def _observed_input(self, ready, t):
        rows, travel, quantities, evidence, hospitals = [], [], {}, {}, {}
        for patient, options in ready:
            pos = self.position(patient, t)
            offered = list(dict.fromkeys(options))
            rows.append(dict(id=patient.id, priority=patient.priority, demand=dict(patient.demand),
                required_capabilities=sorted(patient.caps), position=list(pos), request_t=patient.t_req,
                options=offered, accepted_at={h: patient.accepted_at[h] for h in offered},
                valid_offer_expiries={h: patient.valid[h] for h in offered}))
            for hid in offered:
                views = self._views(hid, patient.demand, t)
                if any(v['status'] != 'fresh' or v['free'] is None for v in views.values()):
                    raise AssertionError('unconfirmed resources entered an allocation model')
                evidence.setdefault(hid, {}).update(copy.deepcopy(views))
                entry = hospitals.setdefault(hid, dict(id=hid, capabilities=sorted(self.hospitals[hid].caps),
                    capacity={}, used={}, committed={}, queued={}))
                for r, v in views.items():
                    for key in ('capacity', 'used', 'committed', 'queued'):
                        entry[key][r] = v[key]
                    quantities[hid, r] = v['free']
                travel.append(dict(patient=patient.id, hospital=hid, minutes=self.travel(patient, hid, pos)))
        inputs = dict(patients=rows, hospitals=list(hospitals.values()), travel_minutes=travel,
            free_resources=[dict(hospital=h, resource=r, free=q) for (h, r), q in sorted(quantities.items())])
        return inputs, evidence

    def _gate(self, p, hid, t, redirect=False):
        hospital = self.hospitals[hid]
        resources = {r: dict(capacity=hospital.cap.get(r, 0), used=hospital.used(r),
            committed=hospital.expected(r), queued=hospital.waiting(r), free=hospital.free(r)) for r in p.demand}
        expiry = p.valid.get(hid)
        reason = 'OFFER_EXPIRED' if expiry is None or expiry <= t else \
            'RESOURCE_CONFLICT' if not hospital.can_accept(p) else None
        self.ev(t, 'confirmation_gate', patient=p.id, hospital=hid, resources=copy.deepcopy(resources),
            passed=reason is None, reason=reason, accepted_at=p.accepted_at.get(hid), offer_expires_at=expiry)
        record = copy.deepcopy(self.log[-1])
        record['commit_event_sequence'] = None
        if reason is None:
            self.commit(p, hid, t, redirect=redirect)
            record['commit_event_sequence'] = self.log[-1]['event_sequence']
        else:
            p.valid.pop(hid, None)
            if not p.valid:
                p.first_valid_t = None
            self.ev(t, 'confirmation_rejected', patient=p.id, hospital=hid, reason=reason)
            self._request_resources(p, hid, t, reason)
        return record

    def _record_decision(self, ready, t, kind, inputs, evidence, proposed, elapsed, record=None):
        rule = self.policy.rule
        decision_index = len(self.allocation_decisions)
        self.ev(t, 'allocation_proposal', decision_index=decision_index, rule=rule.code)
        snapshot_sequence = self.log[-1]['event_sequence']
        actual = {p.id: None for p, _ in ready}
        gates = []
        for patient, _ in ready:
            hid = proposed.get(patient.id)
            if hid is not None:
                gate = self._gate(patient, hid, t, redirect=patient.state == 'TRANSIT')
                gates.append(gate)
                if gate['passed']:
                    actual[patient.id] = hid
        method = 'nearest_current_offer' if kind == 'single' else 'heuristic' if rule.code == 'H1' else \
            'cp_sat_fallback' if record and record.get('fallback') else 'cp_sat'
        decision = dict(t=t, kind=kind, rule=rule.code,
            scope='current_patient_accepted_offers' if kind == 'single' else 'current_same_minute_accepted_offers',
            inputs=inputs, resource_evidence=evidence, proposed_assignments=proposed, assignments=actual,
            proposed_objective=self._objective(inputs, proposed), objective=self._objective(inputs, actual),
            method=method, elapsed_seconds=elapsed, model_optimal_proven=False, actual_optimal_proven=False,
            gate_records=gates, event_sequence=snapshot_sequence)
        if record is not None:
            saved = copy.deepcopy(record)
            saved.update(t=t, decision_index=decision_index)
            if rule.code == 'H1':
                decision['heuristic_record_index'] = len(self.heuristic_records)
                self.heuristic_records.append(saved)
            else:
                decision['solver_record_index'] = len(self.solver_records)
                decision['model_optimal_proven'] = bool(saved['optimal_proven'])
                self.solver_records.append(saved)
        self.allocation_decisions.append(decision)
        for patient, _ in ready:
            if actual[patient.id] is None and not patient.asks:
                self.send_asks(patient, t, exclude={patient.dest} if patient.state == 'TRANSIT' else ())
        return actual

    def _decide_and_confirm(self, p, options, t):
        inputs, evidence = self._observed_input([(p, options)], t)
        started = time.perf_counter()
        # Single choices use only the detached patient's travel map.
        costs = {row['hospital']: row['minutes'] for row in inputs['travel_minutes']}
        hid = min(options, key=lambda h: (costs[h], h))
        elapsed = time.perf_counter() - started
        self.decision_seconds += elapsed
        self.decisions += 1
        actual = self._record_decision([(p, options)], t, 'single', inputs, evidence,
                                       {p.id: hid}, elapsed)
        return actual[p.id]

    def resolve_asks(self, p, t):
        self._information_cycle(t)
        due = [a for a in p.asks if a['at'] == t]
        if not due and not p.valid:
            if p.state == 'TRANSIT' and p.notified and not p.asks \
                    and (p.retry_at is None or p.retry_at <= t):
                p.retry_at = None
                self.send_asks(p, t, exclude={p.dest})
            return
        changed = bool(due)
        if due:
            p.asks = [a for a in p.asks if a['at'] != t]
            for ask in due:
                hid = ask['hid']
                if ask['silent']:
                    self.ev(t, 'no_response', patient=p.id, hospital=hid)
                elif self.hospitals[hid].can_accept(p):
                    p.valid[hid] = t + self.cfg.get('accept_ttl', 10)
                    p.accepted_at[hid] = t
                    if p.first_valid_t is None:
                        p.first_valid_t = t
                    self.ev(t, 'accept', patient=p.id, hospital=hid)
                else:
                    self.ev(t, 'decline', patient=p.id, hospital=hid)
        for hid in [h for h, expiry in p.valid.items() if expiry <= t]:
            del p.valid[hid]
            changed = True
            self.ev(t, 'expire', patient=p.id, hospital=hid)
        if not p.valid:
            p.first_valid_t = None
        elif p.state == 'TRANSIT' or not p.asks or t - p.first_valid_t >= self.policy.wait:
            options = []
            for hid in p.valid:
                eligible, reason, _ = self._check(p, hid, t, 'allocation')
                if eligible:
                    options.append(hid)
                else:
                    self._request_resources(p, hid, t, reason)
            if options and self.policy.joint and p.state == 'SEARCH':
                self.ready.append((p, options))
                return
            if options and self._decide_and_confirm(p, options, t) is not None:
                return
        if changed and p.state == 'SEARCH' and not p.asks and not p.valid:
            self.send_asks(p, t)
        if p.state == 'TRANSIT' and p.notified and not p.asks and not p.valid \
                and (p.retry_at is None or p.retry_at <= t):
            p.retry_at = None
            self.send_asks(p, t, exclude={p.dest})

    def resolve_batch(self, t):
        if not self.ready:
            return
        ready, self.ready = self.ready, []
        inputs, evidence = self._observed_input(ready, t)
        rule = self.policy.rule
        started = time.perf_counter()
        proposed = _ObservedCPSATBatch(rule, inputs).decide_batch(None, None, t)
        elapsed = time.perf_counter() - started
        self.decision_seconds += elapsed
        self.decisions += 1
        self._record_decision(ready, t, 'batch', inputs, evidence, proposed, elapsed, rule.records[-1])

    def run_replay(self):
        branch = ReplaySimulation.run_replay(self)
        branch.update(allocation_decisions=self.allocation_decisions, solver_records=self.solver_records,
            heuristic_records=self.heuristic_records, observation_records=self.observation_records,
            information_mode=self.information_mode, comparison_id=EXPERIMENT_ID,
            source_experiment='partial_information_comparison')
        branch['metrics'].update(allocation_wall_seconds=self.decision_seconds,
            allocation_decisions=len(self.allocation_decisions), whole_episode_optimal_proven=False,
            solver_calls=len(self.solver_records), heuristic_calls=len(self.heuristic_records),
            information_wait_checks=sum(e['type'] == 'information_wait' for e in self.log),
            resource_confirmation_requests=sum(e['type'] == 'resource_confirmation_request' for e in self.log),
            confirmation_rejections=sum(e['type'] == 'confirmation_rejected' for e in self.log),
            ignored_resource_reports=sum(e['type'] == 'resource_report_received' and not e['applied'] for e in self.log))
        return branch


def run_information_replay(scenario, policies=None, execution_order=None, routing=None):
    validate_scenario(scenario)
    specs = copy.deepcopy(list(DEFAULT_INFORMATION_POLICIES if policies is None else policies))
    ids = [s['id'] for s in specs]
    if not ids or len(set(ids)) != len(ids):
        raise ValueError('unique nonempty branch IDs required')
    if any(s.get('mode') not in ('perfect', 'observed') or s.get('rule') not in ('R1', 'H1', 'C1')
           for s in specs):
        raise ValueError('unsupported information mode or allocation rule')
    if any(s.get('observed_inquiry') is not None or s.get('adaptive_inquiry') is not None for s in specs):
        raise ValueError('this information experiment requires a fixed inquiry policy')
    common = ('k', 'wait', 'notify', 'near_minutes', 'expand_after', 'inquiry_ranking',
              'load_weight', 'load_window', 'priority_order', 'high_priority_hedge_after')
    if any(any(s.get(k) != specs[0].get(k) for k in common) for s in specs):
        raise ValueError('all branches must share inquiry and notification settings')
    order = list(ids if execution_order is None else execution_order)
    if len(order) != len(ids) or set(order) != set(ids):
        raise ValueError('execution_order must contain every branch exactly once')
    frozen = copy.deepcopy(scenario)
    frozen['comparison_id'] = EXPERIMENT_ID
    frozen['source_experiment'] = 'partial_information_comparison'
    frozen.setdefault('assumptions', []).extend([
        '완전정보는 비교 기준이며, 관측 분기는 전달된 자원 수량과 현재 서버 예약·등록 대기 원장을 사용한다.',
        '수용 회신은 자원 수량을 갱신하지 않는다. 별도 자원 재확인 응답을 합성 환경에서 제공한다.',
        '자원 수량만 누락·지연시킨다. 병원별 수용 판단과 조건 변경 알림까지 모두 지연시킨 실험이 아니다.',
    ])
    routing = routing or RouteSnapshot()
    routing.used_keys.clear()
    by_id = {}
    for branch_id in order:
        spec = copy.deepcopy(next(s for s in specs if s['id'] == branch_id))
        spec.pop('id')
        label, mode = spec.pop('label'), spec.pop('mode')
        if spec['rule'] == 'H1':
            spec['rule'] = HeuristicAssignmentRule(time_limit_seconds=spec.pop('time_limit_seconds', .05),
                                                  max_moves=spec.pop('max_moves', 2048))
        policy = Policy(**spec, name=branch_id)
        simulation = PartialInformationReplay(copy.deepcopy(frozen), policy, routing=routing, mode=mode)
        branch = simulation.run_replay()
        branch.update(id=branch_id, label=label, policy=_policy_spec(policy))
        by_id[branch_id] = branch
    branches = [by_id[i] for i in ids]
    return dict(schema_version=1, experiment_id=EXPERIMENT_ID, scenario=frozen,
        branches=branches, execution_order=order, routing_summary=routing.summary(scenario_only=True))
