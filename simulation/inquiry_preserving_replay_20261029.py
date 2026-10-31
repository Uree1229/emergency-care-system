"""Inquiry-only experiments around unchanged, detached-input G2 allocation.

Replies are processed by the frozen transport engine. Refill intents are
drained only after that minute's G2 joint decisions. Retry changes are applied
to a newly scheduled patient cooldown, never to a shared simulator config.
"""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from . import arrival_guard_priority_engine_20261028 as g2_engine
from . import arrival_guard_priority_policy_20261028 as g2_policy
from . import final_assignment_engine_20261028 as common
from .inquiry_preserving_policy_20261029 import SPECS, normalize_spec, decide_refill, decide_retry
from .map_replay import RouteSnapshot, validate_scenario
from .sim import Policy

EXPERIMENT_ID = 'inquiry_preserving_20261029'
FROZEN_SOURCES = {
    'G2_policy': '57373bf5fa1723adbd284274b7b63ba52f8c5665d02e94c6a78ae258fd759878',
    'G2_engine': '6ea8f99d22130a92f09c73d8200fb8fce8cec5f105a21d6ae5cd2fd5c2e29ced',
}
for name, module in (('G2_policy', g2_policy), ('G2_engine', g2_engine)):
    if hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest() != FROZEN_SOURCES[name]:
        raise ValueError('frozen source differs: ' + name)
ASSUMPTIONS = [
    'G2 assignment, its priority weights and H1 work budget are unchanged; only inquiry refill and retry cooldown may differ.',
    'Refill uses received replies after G2 commitments, at most three simultaneous inquiries and only unqueried compatible candidates.',
    'Retry history contains this patient\'s completed accept/decline/timeout observations; resource releases and future replies are not supplied.',
    'Whole-episode G2 successes and weight4 outcomes are acceptance gates for experiments, not online preservation guarantees.',
    'Weights 1/2/4 and minute thresholds are experimental; no clinical efficacy, real response probability or national-system equivalence is claimed.',
]


def execution_contract(spec):
    return dict(inquiry_spec=normalize_spec(spec), allocation_rule='unchanged_G2',
        allocation_source_sha256=dict(FROZEN_SOURCES), max_simultaneous_inquiries=3,
        first_acceptance_extra_wait_minutes=2, notify=True,
        refill_phase='after_current_minute_G2_joint_commitments',
        refill_order='original_received_patient_order',
        retry_scope='newly_scheduled_patient_cooldown_only',
        observations='completed_same_patient_reply_or_timeout_ledger_only',
        future_events_supplied_to_inquiry_policy=False,
        future_events_supplied_to_assignment_rule=False,
        hospital_queue_state_supplied_to_policy=False,
        simulation_clock='integer_minute', wall_time_added_to_simulation_clock=False,
        whole_episode_nonregression_guaranteed=False,
        whole_episode_priority4_nonregression_guaranteed=False,
        clinical_priority_policy_validated=False)


class InquiryPreservingReplay(g2_engine.ArrivalGuardPriorityReplay):
    def __init__(self, scenario, policy, inquiry_spec, routing=None):
        self.inquiry_spec = normalize_spec(inquiry_spec)
        self.submitted_ledger = []
        self.received_ledger = []
        self._latest_submission = {}
        self._received_at = {}
        self._refill_intents = {}
        self._refill_in_progress = None
        self.refill_decisions = []
        self.retry_decisions = []
        super().__init__(scenario, policy, routing=routing)

    def ev(self, t, type_, **data):
        super().ev(t, type_, **data)
        if type_ == 'ask':
            row = dict(patient=data['patient'], hospital=data['hospital'], submitted_t=t,
                       attempt=data['attempt'])
            self.submitted_ledger.append(row)
            self._latest_submission[row['patient'], row['hospital']] = row
        elif type_ in ('accept', 'decline', 'no_response'):
            pid, hid = data['patient'], data['hospital']
            sent = self._latest_submission[pid, hid]
            self.received_ledger.append(dict(patient=pid, hospital=hid,
                submitted_t=sent['submitted_t'], observed_t=t,
                outcome='timeout' if type_ == 'no_response' else type_))
            self._received_at[pid] = t

    def _retry_snapshot(self, p, t):
        return dict(t=t, patient=p.id, priority=p.priority, baseline_wait=self.cfg['retry_wait'],
            observations=[deepcopy(row) for row in self.received_ledger
                          if row['patient'] == p.id and row['observed_t'] <= t])

    def send_asks(self, p, t, exclude=()):
        # Extra hooks must not bypass a custom patient cooldown. Q0 retains
        # the frozen caller behavior exactly, including baseline notification.
        if self.inquiry_spec['retry'] != 'baseline' and p.retry_at is not None and p.retry_at > t:
            return
        before = p.retry_at
        super().send_asks(p, t, exclude=exclude)
        base = self.cfg['retry_wait']
        if p.retry_at == t+base and p.retry_at != before:
            snapshot = self._retry_snapshot(p, t)
            decision = decide_retry(self.inquiry_spec, snapshot)
            p.retry_at = decision['retry_at']
            record = dict(t=t, patient=p.id, snapshot=snapshot, decision=decision,
                          baseline_retry_at=t+base, actual_retry_at=p.retry_at)
            self.retry_decisions.append(record)
            self.ev(t, 'inquiry_retry_policy', patient=p.id, record_index=len(self.retry_decisions)-1,
                    baseline_retry_at=t+base, actual_retry_at=p.retry_at)

    def inquiry_limit(self, p, t):
        if self._refill_in_progress is not None:
            pid, at, planned = self._refill_in_progress
            if pid == p.id and at == t:
                return min(3, len(p.asks)+len(planned))
        return super().inquiry_limit(p, t)

    def resolve_asks(self, p, t):
        super().resolve_asks(p, t)
        if p.t_req <= t and self._received_at.get(p.id) == t:
            self._refill_intents.setdefault(p.id, None)

    def _refill_snapshot(self, p, t):
        loc = self.position(p, t)
        # Membership and submission timestamps only. Hidden at/silent fields
        # in the environment's scheduled ask objects never reach the planner.
        pending = [dict(hospital=a['hid'], submitted_t=self._latest_submission[p.id, a['hid']]['submitted_t'])
                   for a in p.asks]
        open_hospitals = {row['hospital'] for row in pending}
        candidates = [hid for hid in self.candidates(p, loc, self.failed_hospitals[p.id])
                      if hid not in p.tried and hid not in open_hospitals and hid not in p.valid]
        return dict(t=t, patient=p.id, priority=p.priority, request_t=p.t_req, state=p.state,
            has_valid_offer=bool(p.valid), ready=any(q.id == p.id for q, _ in self.ready),
            received_this_tick=self._received_at.get(p.id) == t, retry_at=p.retry_at,
            pending=pending, candidates=[dict(hospital=hid, travel_minutes=self.travel(p, hid, loc))
                                         for hid in candidates], max_k=3)

    def resolve_batch(self, t):
        super().resolve_batch(t)
        intents, self._refill_intents = list(self._refill_intents), {}
        if self.inquiry_spec['refill'] == 'none':
            return
        if intents:
            self.ev(t, 'G2_batch_phase_complete',
                    allocation_decision_count=len(self.allocation_decisions), guard_record_count=len(self.guard_records))
        for pid in intents:
            p = self.patients[pid]
            snapshot = self._refill_snapshot(p, t)
            decision = decide_refill(self.inquiry_spec, snapshot)
            record = dict(t=t, patient=pid, snapshot=snapshot, decision=decision,
                          actual_sent=[], phase='after_G2_batch')
            index = len(self.refill_decisions)
            self.refill_decisions.append(record)
            self.ev(t, 'inquiry_refill_policy', patient=pid, record_index=index,
                    selected_hospitals=list(decision['send']))
            if decision['send']:
                offset = len(self.submitted_ledger)
                self._refill_in_progress = (pid, t, decision['send'])
                try:
                    self.send_asks(p, t)
                finally:
                    self._refill_in_progress = None
                actual = [row['hospital'] for row in self.submitted_ledger[offset:] if row['patient'] == pid]
                record['actual_sent'] = actual
                if actual != decision['send']:
                    raise AssertionError('refill planner and actual inquiries differ')

    def run_replay(self):
        value = super().run_replay()
        value.update(comparison_id=EXPERIMENT_ID, inquiry_spec=deepcopy(self.inquiry_spec),
            execution_contract=execution_contract(self.inquiry_spec),
            inquiry_refill_decisions=self.refill_decisions, inquiry_retry_decisions=self.retry_decisions,
            submitted_inquiry_ledger=self.submitted_ledger, received_inquiry_ledger=self.received_ledger)
        value['metrics'].update(inquiry_refill_reviews=len(self.refill_decisions),
            inquiry_refill_calls=sum(bool(r['actual_sent']) for r in self.refill_decisions),
            inquiry_refill_requests=sum(len(r['actual_sent']) for r in self.refill_decisions),
            inquiry_retry_cycles=len(self.retry_decisions),
            configured_retry_wait_delta_minutes=sum(r['baseline_retry_at']-r['actual_retry_at'] for r in self.retry_decisions),
            retry_wait_delta_is_not_total_patient_time_saved=True)
        return value


def run_inquiry_replay(scenario, policy_spec, routing=None):
    spec = normalize_spec(policy_spec)
    validate_scenario(scenario)
    if type(scenario.get('retry_minutes', 3)) is not int or scenario.get('retry_minutes', 3) < 1:
        raise ValueError('inquiry experiment requires a positive integral baseline retry wait')
    original = deepcopy(scenario)
    fingerprint = common.canonical_sha256(original)
    template, route_sha = common._routing_template(routing)
    physical = deepcopy(original)
    physical.update(online_allocation_comparison=True, source_experiment='online_allocation_comparison',
                    comparison_id=EXPERIMENT_ID)
    physical.setdefault('assumptions', []).extend(ASSUMPTIONS)
    route = RouteSnapshot(connector_speed_kmh=template.connector_speed_kmh)
    route.routes = deepcopy(template.routes)
    policy = Policy(rule=g2_policy.ArrivalGuardPriorityAssignmentRule(), k=3, wait=2, notify=True, name=spec['id'])
    branch = InquiryPreservingReplay(physical, policy, spec, routing=route).run_replay()
    settings = g2_engine._policy_spec(policy)
    settings['inquiry_revision'] = deepcopy(spec)
    branch.update(id=spec['id'], label=f"{spec['id']} · G2 배정 + 문의 정책", policy=settings,
        original_scenario_sha256=fingerprint, routing_input_sha256=route_sha)
    if common.canonical_sha256(scenario) != fingerprint:
        raise AssertionError('caller scenario changed')
    return dict(schema_version=1, comparison_id=EXPERIMENT_ID, source_experiment='online_allocation_comparison',
        original_scenario=original, scenario=physical, input_scenario_sha256=fingerprint,
        original_scenario_sha256=fingerprint, routing_input_sha256=route_sha,
        branches=[branch], execution_order=[spec['id']], policy_spec=spec,
        duration_minutes=max((e['t'] for e in branch['events']), default=0),
        routing_summary=dict(provider='synthetic', scope='independent_per_branch',
            live_traffic=False, ambulance_priority=False, external_route_provider_allowed=False,
            shared_mutable_routing_cache=False, by_branch={spec['id']:route.summary(scenario_only=True)}))


def audit_inquiry_replay(replay, *, source_scenario=None):
    from .inquiry_preserving_audit_20261029 import audit_replay
    return audit_replay(replay, source_scenario=source_scenario)


def main(argv=None):
    parser = argparse.ArgumentParser(description='G2 고정·문의 슬롯/재문의 정책 연구 재생')
    parser.add_argument('--input', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--policy', choices=[s['id'] for s in SPECS], required=True)
    args = parser.parse_args(argv)
    target = Path(args.output)
    if target.exists():
        parser.error('기존 결과는 덮어쓰지 않습니다. 새 output 경로를 지정하세요.')
    source = json.loads(Path(args.input).read_text(encoding='utf-8-sig'))
    spec = next(row for row in SPECS if row['id'] == args.policy)
    replay = run_inquiry_replay(source, spec)
    audit = audit_inquiry_replay(replay, source_scenario=source)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open('x', encoding='utf-8') as stream:
        json.dump(dict(synthetic=True, operational_adoption=False, replay=replay, audit=audit),
                  stream, ensure_ascii=False, indent=2)
    print(json.dumps(dict(passed=audit['passed'], policy=spec['id'], output=str(target.resolve())), ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
