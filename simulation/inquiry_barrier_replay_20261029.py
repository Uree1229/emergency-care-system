"""QJ: auxiliary inquiries cannot move the primary wave's decision clock.

This version fixes a timing interaction found in the frozen QI development
screen. It does not roll back resource competition or guarantee future G2
outcomes. Frozen QI, G2 and every old result remain intact.
"""
from copy import deepcopy

from . import arrival_guard_priority_policy_20261028 as g2_policy
from . import arrival_guard_priority_engine_20261028 as g2_engine
from . import final_assignment_engine_20261028 as common
from . import inquiry_preserving_replay_20261029 as qi
from .inquiry_preserving_policy_20261029 import normalize_spec
from .map_replay import RouteSnapshot, validate_scenario
from .sim import Policy

EXPERIMENT_ID = 'inquiry_primary_wave_barrier_20261029'
BARRIER_CONTRACT = dict(version='primary_wave_barrier_v1',
    primary='initial_or_normal_retry_wave', auxiliary='refill_hook_requests_only',
    while_primary_pending='wait_for_primary_acceptance_plus_two_or_primary_completion',
    when_primary_complete='primary_offer_now_else_auxiliary_wait_two_or_no_open_requests',
    auxiliary_replies_do_not_start_or_extend_primary_wait_clock=True,
    restore_primary_clock_after_last_auxiliary_offer_expires=True,
    submitted_hospital_work_cancelled=False, unchanged_G2_allocation=True,
    future_response_or_release_information_used=False,
    whole_episode_nonregression_guaranteed=False)
BARRIER_ASSUMPTIONS = [
    'QJ tags initial/normal retry inquiries as primary and refill-hook inquiries as auxiliary.',
    'Auxiliary offers neither start nor extend the primary wave wait clock; received offers are evaluated by unchanged G2 after the primary barrier.',
    'Hospital work already submitted is not cancelled. Current barrier preservation is not a whole-episode or priority4 outcome guarantee.',
]


class PrimaryWaveReplay(qi.InquiryPreservingReplay):
    def __init__(self, scenario, policy, inquiry_spec, routing=None):
        self.inquiry_submission_roles = []
        self._latest_role = {}
        self._barrier_latched = set()
        self.primary_barrier_decisions = []
        super().__init__(scenario, policy, inquiry_spec, routing=routing)

    def ev(self, t, type_, **data):
        if type_ == 'ask':
            role = 'auxiliary' if self._refill_in_progress is not None else 'primary'
            row = dict(patient=data['patient'], hospital=data['hospital'], submitted_t=t,
                       attempt=data['attempt'], role=role)
            self.inquiry_submission_roles.append(row)
            self._latest_role[row['patient'], row['hospital']] = row
            if role == 'auxiliary':
                self._barrier_latched.add(row['patient'])
            data = dict(data, inquiry_role=role)
        elif type_ in ('commit', 'transfer_search', 'inquiry_retry_policy'):
            self._barrier_latched.discard(data['patient'])
        super().ev(t, type_, **data)

    def resolve_asks(self, p, t):
        super().resolve_asks(p, t)
        if p.t_req > t or p.state != 'SEARCH' or not p.valid:
            return
        pending = [dict(hospital=a['hid'], submitted_t=self._latest_submission[p.id,a['hid']]['submitted_t'],
                        role=self._latest_role[p.id,a['hid']]['role']) for a in p.asks]
        offers = [dict(hospital=hid, accepted_t=p.accepted_at[hid],
                       role=self._latest_role[p.id,hid]['role']) for hid in p.valid]
        has_auxiliary = any(row['role'] == 'auxiliary' for row in pending+offers)
        latched = p.id in self._barrier_latched
        if not has_auxiliary and not latched:
            return  # A normal G2 wave must keep its original timing exactly.
        before = any(patient.id == p.id for patient, _ in self.ready)
        snapshot = dict(t=t, patient=p.id, request_t=p.t_req, state=p.state,
            pending=pending, valid_offers=offers, primary_wait_minutes=2,
            ready_before=before, first_valid_t_before=p.first_valid_t,
            barrier_latched_before=latched, current_auxiliary_present=has_auxiliary)
        original_pending = [row for row in pending if row['role'] == 'primary']
        original_offers = [row for row in offers if row['role'] == 'primary']
        first = min((row['accepted_t'] for row in original_offers), default=None)
        auxiliary_first = min((row['accepted_t'] for row in offers if row['role'] == 'auxiliary'), default=None)
        if not original_pending:
            allow = bool(original_offers) or not pending or (auxiliary_first is not None and t-auxiliary_first >= 2)
        else:
            allow = first is not None and t-first >= 2
        reason = ('primary_wave_complete' if not original_pending and allow else
                  'collect_auxiliary_offers_after_primary_completion' if not original_pending else
                  'primary_acceptance_wait_complete' if allow else
                  'wait_for_primary_acceptance' if first is None else 'wait_primary_two_minutes')
        self.ev(t, 'inquiry_primary_barrier_review', patient=p.id,
                record_index=len(self.primary_barrier_decisions))
        if not allow and before:
            self.ready = [(patient, options) for patient, options in self.ready if patient.id != p.id]
        failed = []
        if allow and not before:
            # This is the frozen transport's current acceptance recheck, not a
            # future query prediction. The actual joint allocation stays G2.
            options = [hid for hid in p.valid if self.hospitals[hid].can_accept(p)]
            failed = [hid for hid in p.valid if hid not in options]
            for hid in failed:
                del p.valid[hid]
                self.ev(t, 'recheck_failed', patient=p.id, hospital=hid)
            if options:
                self.ready.append((p, options))
        p.first_valid_t = (first if first is not None else t) if p.valid else None
        if not has_auxiliary:
            self._barrier_latched.discard(p.id)
        after = any(patient.id == p.id for patient, _ in self.ready)
        decision = dict(allow_current_G2_ready=allow, reason=reason,
            primary_pending_count=len(original_pending), primary_valid_count=len(original_offers),
            primary_first_accept_t=first, auxiliary_first_accept_t=auxiliary_first, ready_after=after,
            removed_premature_ready=before and not after, added_primary_barrier_ready=not before and after,
            recheck_failed_hospitals=failed, applied_first_valid_t=p.first_valid_t,
            barrier_latched_after=p.id in self._barrier_latched)
        self.primary_barrier_decisions.append(dict(t=t, patient=p.id, snapshot=snapshot,
            decision=decision, phase='after_received_results_before_G2_batch'))
        self.ev(t, 'inquiry_primary_barrier', patient=p.id,
                record_index=len(self.primary_barrier_decisions)-1, ready_after=after, reason=reason)

    def run_replay(self):
        value = super().run_replay()
        value.update(comparison_id=EXPERIMENT_ID,
            primary_barrier_contract=deepcopy(BARRIER_CONTRACT),
            inquiry_submission_roles=self.inquiry_submission_roles,
            primary_barrier_decisions=self.primary_barrier_decisions)
        value['execution_contract']['primary_wave_barrier'] = deepcopy(BARRIER_CONTRACT)
        value['metrics'].update(primary_barrier_reviews=len(self.primary_barrier_decisions),
            removed_auxiliary_early_decisions=sum(r['decision']['removed_premature_ready'] for r in self.primary_barrier_decisions),
            released_primary_barrier_decisions=sum(r['decision']['added_primary_barrier_ready'] for r in self.primary_barrier_decisions))
        return value


def run_barrier_replay(scenario, policy_spec, routing=None):
    spec = normalize_spec(policy_spec)
    if spec['retry'] != 'baseline':
        raise ValueError('QJ development isolates primary barrier with original retry wait')
    validate_scenario(scenario)
    if type(scenario.get('retry_minutes',3)) is not int or scenario.get('retry_minutes',3) < 1:
        raise ValueError('positive integral baseline retry wait required')
    original = deepcopy(scenario)
    fingerprint = common.canonical_sha256(original)
    template, route_sha = common._routing_template(routing)
    physical = deepcopy(original)
    physical.update(online_allocation_comparison=True, source_experiment='online_allocation_comparison',
                    comparison_id=EXPERIMENT_ID)
    physical.setdefault('assumptions', []).extend(qi.ASSUMPTIONS+BARRIER_ASSUMPTIONS)
    route = RouteSnapshot(connector_speed_kmh=template.connector_speed_kmh)
    route.routes = deepcopy(template.routes)
    policy = Policy(rule=g2_policy.ArrivalGuardPriorityAssignmentRule(), k=3, wait=2, notify=True, name=spec['id'])
    branch = PrimaryWaveReplay(physical, policy, spec, routing=route).run_replay()
    settings = g2_engine._policy_spec(policy)
    settings['inquiry_revision'] = deepcopy(spec)
    branch.update(id=spec['id'], label=spec['id']+' · primary wave barrier + G2', policy=settings,
        original_scenario_sha256=fingerprint, routing_input_sha256=route_sha)
    if common.canonical_sha256(scenario) != fingerprint:
        raise AssertionError('caller scenario changed')
    return dict(schema_version=1, comparison_id=EXPERIMENT_ID, source_experiment='online_allocation_comparison',
        original_scenario=original, scenario=physical, input_scenario_sha256=fingerprint,
        original_scenario_sha256=fingerprint, routing_input_sha256=route_sha,
        primary_barrier_contract=deepcopy(BARRIER_CONTRACT), branches=[branch],
        execution_order=[spec['id']], policy_spec=spec,
        duration_minutes=max((e['t'] for e in branch['events']), default=0),
        routing_summary=dict(provider='synthetic', scope='independent_per_branch',
            live_traffic=False, ambulance_priority=False, external_route_provider_allowed=False,
            shared_mutable_routing_cache=False, by_branch={spec['id']:route.summary(scenario_only=True)}))


def audit_barrier_replay(replay, *, source_scenario=None):
    from .inquiry_barrier_audit_20261029 import audit_replay
    return audit_replay(replay, source_scenario=source_scenario)
