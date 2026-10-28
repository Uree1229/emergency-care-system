"""Add short opportunity synchronization without changing frozen baselines."""
from __future__ import annotations

from copy import deepcopy
import time

from .h1_online_engine import DEFAULT_H1_POLICIES, _policy_spec
from .heuristic_assignment import HeuristicAssignmentRule
from .map_replay import RouteSnapshot, validate_scenario
from .opportunity_guard import GRACE_MINUTES, decide_protection
from .partial_information_engine import DEFAULT_INFORMATION_POLICIES, PartialInformationReplay
from .sim import Policy


EXPERIMENT_ID = 'opportunity_protection_20261015'
SOURCE_EXPERIMENT = 'opportunity_protection_comparison'
DEFAULT_OPPORTUNITY_POLICIES = tuple(
    dict(deepcopy(spec), protection=False) for spec in DEFAULT_INFORMATION_POLICIES
) + tuple(dict(deepcopy(spec), id='protected_' + spec['id'], mode='observed',
               protection=True, label='기회 보호 · ' + spec['label'])
          for spec in DEFAULT_H1_POLICIES if spec['id'] in ('H1', 'C1'))


class OpportunityProtectionReplay(PartialInformationReplay):
    def __init__(self, scenario, policy, routing=None, mode='observed', protection=True):
        if type(protection) is not bool:
            raise ValueError('protection must be boolean')
        if protection and (mode != 'observed' or policy.rule.code not in ('H1', 'C1')):
            raise ValueError('opportunity protection requires observed H1/C1')
        self.protection_enabled = protection
        self.waiting_since, self.held_since = {}, {}
        self.protection_records = []
        self.protection_seconds = 0.0
        super().__init__(scenario, policy, routing=routing, mode=mode)

    def _request_resources(self, p, hid, t, reason):
        super()._request_resources(p, hid, t, reason)
        if self.protection_enabled and p.state == 'SEARCH' and reason in ('UNKNOWN', 'STALE'):
            self.waiting_since.setdefault(p.id, t)

    def _patient_view(self, patient):
        return dict(id=patient.id, priority=patient.priority, request_t=patient.t_req,
                    demand=dict(patient.demand), required_capabilities=sorted(patient.caps),
                    compatible_hospitals=sorted(hid for hid, hospital in self.hospitals.items()
                                                if patient.caps <= hospital.caps))

    def _protection_snapshot(self, t):
        ready = [dict(self._patient_view(p), options=list(options),
                      valid_offer_expiries={hid: p.valid[hid] for hid in options})
                 for p, options in self.ready]
        ready_ids = {p['id'] for p in ready}
        waiting = []
        for patient in self.patients.values():
            if patient.state != 'SEARCH' or patient.t_req > t or patient.id in ready_ids:
                continue
            offers, eligible = [], False
            for hid, expiry in patient.valid.items():
                if expiry <= t:
                    continue
                views = self._views(hid, patient.demand, t)
                shortage = any(v['status'] == 'fresh' and v['free'] < patient.demand[r]
                               for r, v in views.items())
                unknown = any(v['status'] == 'unknown' for v in views.values())
                stale = any(v['status'] == 'stale' for v in views.values())
                reason = 'FRESH_SHORTAGE' if shortage else 'UNKNOWN' if unknown else 'STALE' if stale else None
                if reason is None:
                    eligible = True
                elif reason in ('UNKNOWN', 'STALE'):
                    offers.append(dict(hospital=hid, accepted_at=patient.accepted_at[hid],
                        expires_at=expiry, reason=reason, resources=deepcopy(views)))
            if offers and not eligible:
                waiting.append(dict(self._patient_view(patient), offers=offers))
        return dict(t=t, ready=ready, waiting=waiting,
                    hospitals=[dict(id=hid, capabilities=sorted(h.caps))
                               for hid, h in self.hospitals.items()])

    def resolve_batch(self, t):
        if not self.ready or not self.protection_enabled:
            return super().resolve_batch(t)
        started = time.perf_counter()
        snapshot = self._protection_snapshot(t)
        waiting_before, held_before = deepcopy(self.waiting_since), deepcopy(self.held_since)
        result = decide_protection(snapshot, waiting_before, held_before)
        self.held_since = deepcopy(result['held_since_after'])
        self.ev(t, 'opportunity_guard', record_index=len(self.protection_records))
        self.protection_records.append(dict(snapshot=snapshot, waiting_since=waiting_before,
            held_since=held_before, result=deepcopy(result), grace_minutes=GRACE_MINUTES,
            event_sequence=self.log[-1]['event_sequence']))
        for hold in result['holds']:
            self.ev(t, 'opportunity_hold', **hold)
        held = set(result['held'])
        self.ready = [(p, options) for p, options in self.ready if p.id not in held]
        self.protection_seconds += time.perf_counter() - started
        super().resolve_batch(t)

    def run_replay(self):
        branch = super().run_replay()
        branch.update(comparison_id=EXPERIMENT_ID, source_experiment=SOURCE_EXPERIMENT,
                      protection_enabled=self.protection_enabled,
                      protection_records=self.protection_records)
        branch['metrics'].update(protection_wall_seconds=self.protection_seconds,
            opportunity_guard_calls=len(self.protection_records),
            opportunity_hold_checks=sum(e['type'] == 'opportunity_hold' for e in self.log),
            opportunity_held_patients=len({e['patient'] for e in self.log
                                          if e['type'] == 'opportunity_hold'}))
        return branch


def run_opportunity_replay(scenario, policies=None, execution_order=None, routing=None):
    validate_scenario(scenario)
    specs = deepcopy(list(DEFAULT_OPPORTUNITY_POLICIES if policies is None else policies))
    ids = [s['id'] for s in specs]
    if not ids or len(set(ids)) != len(ids):
        raise ValueError('unique nonempty branch IDs required')
    for spec in specs:
        if spec.get('mode') not in ('perfect', 'observed') or spec.get('rule') not in ('R1', 'H1', 'C1'):
            raise ValueError('unsupported information mode or allocation rule')
        if type(spec.get('protection')) is not bool:
            raise ValueError('explicit boolean protection required')
        if spec['protection'] and (spec['mode'] != 'observed' or spec['rule'] not in ('H1', 'C1')):
            raise ValueError('opportunity protection requires observed H1/C1')
        if spec.get('observed_inquiry') is not None or spec.get('adaptive_inquiry') is not None:
            raise ValueError('this comparison requires a fixed inquiry policy')
    common = ('k', 'wait', 'notify', 'near_minutes', 'expand_after', 'inquiry_ranking',
              'load_weight', 'load_window', 'priority_order', 'high_priority_hedge_after')
    if any(any(s.get(k) != specs[0].get(k) for k in common) for s in specs):
        raise ValueError('all branches must share inquiry and notification settings')
    order = list(ids if execution_order is None else execution_order)
    if len(order) != len(ids) or set(order) != set(ids):
        raise ValueError('execution_order must contain every branch exactly once')
    frozen = deepcopy(scenario)
    frozen.update(comparison_id=EXPERIMENT_ID, source_experiment=SOURCE_EXPERIMENT)
    frozen.setdefault('assumptions', []).extend([
        '관측 분기는 전달된 자원 수량과 현재 서버 예약·등록 대기만 사용한다. 수용 회신은 수량 갱신이 아니다.',
        '기회 보호는 관측 병상의 짧은 배정 유보이며 실제 병상 예약·미확인 수용 확정·병원 우선순위 변경이 아니다.',
        '구조적으로 한 병원만 가능한 확인 대기 환자와 대안이 있는 동등·낮은 우선순위 환자 사이에서만 적용한다.',
    ])
    routing = routing or RouteSnapshot()
    routing.used_keys.clear()
    by_id = {}
    for branch_id in order:
        spec = deepcopy(next(s for s in specs if s['id'] == branch_id))
        spec.pop('id')
        label, mode, protection = spec.pop('label'), spec.pop('mode'), spec.pop('protection')
        if spec['rule'] == 'H1':
            spec['rule'] = HeuristicAssignmentRule(time_limit_seconds=spec.pop('time_limit_seconds', .05),
                                                  max_moves=spec.pop('max_moves', 2048))
        policy = Policy(**spec, name=branch_id)
        simulation = OpportunityProtectionReplay(deepcopy(frozen), policy, routing=routing,
                                                  mode=mode, protection=protection)
        branch = simulation.run_replay()
        branch.update(id=branch_id, label=label, policy=_policy_spec(policy))
        by_id[branch_id] = branch
    return dict(schema_version=1, experiment_id=EXPERIMENT_ID, scenario=frozen,
        branches=[by_id[i] for i in ids], execution_order=order,
        routing_summary=routing.summary(scenario_only=True))
