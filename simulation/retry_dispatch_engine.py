"""Isolated dispatch ablations on the frozen capability-probe foundation."""
from __future__ import annotations
from copy import deepcopy

from . import cooperative_probe_engine as cooperative
from . import shortage_probe_engine as independent
from .deadline_recovery_engine import condition_fingerprint
from .retry_dispatch_policy import VARIANTS, RETRY_DISPATCH_SPEC, decide

EXPERIMENT_ID = 'retry_dispatch_20261018'
NAMESPACE_FIELDS = {'retry_dispatch_enabled', 'retry_dispatch_selector', 'retry_dispatch_spec',
                    'retry_dispatch_records', 'retry_dispatch_contacts', 'retry_dispatch_wakes',
                    'retry_dispatch_foundation_spec'}
RETRY_DISPATCH_FOUNDATION_SPEC = dict(
    capability_source='scenario.inquiry_queue.enabled',
    inherited_replays=dict(
        cooperative_queue='simulation.cooperative_probe_engine.CooperativeProbeReplay',
        independent_replies='simulation.shortage_probe_engine.ShortageProbeReplay'),
    actual_replays=dict(
        cooperative_queue='simulation.retry_dispatch_engine.CooperativeRetryDispatchReplay',
        independent_replies='simulation.retry_dispatch_engine.IndependentRetryDispatchReplay'),
    cohort_registration=True, scarce_routing=True, late_reply_recovery=True,
    late_pending_guard=True, shortage_probe=True, recovery=True, pending_protection=True,
    selector_changed=True, internal_inquiry_slot='v4',
    hospital_workflow_inherited_without_change=True,
    extra_resource_change_publications=False, worker_count_unchanged=True,
    original_fifo_timeout_offer_ttl_and_acceptance_unchanged=True)


class RetryDispatchMixin:
    """Keep original selector inputs and all existing raw evidence intact."""
    def __init__(self, *args, variant='frontier', **kwargs):
        if variant not in VARIANTS:
            raise ValueError('known fixed dispatch ablation required')
        self.retry_dispatch_variant = variant
        self.retry_dispatch_records = []
        self.retry_dispatch_contacts = []
        self.retry_dispatch_wakes = []
        self._dispatch_accept_refs = {}
        self._dispatch_context = None
        super().__init__(*args, **kwargs)
        if self.inquiry_name != 'v4':
            raise ValueError('explicit original V4 internal slot required')
        self.inquiry_decider = self._dispatch_decide

    @staticmethod
    def _condition(p):
        return dict(id=p.id, priority=p.priority, demand=deepcopy(p.demand),
                    required_capabilities=sorted(p.caps))

    def ev(self, t, typ, **fields):
        public = None
        if typ in ('ask', 'resource_event_review', 'report_driven_wake'):
            p = self.patients[fields['patient']]
            public = dict(patient=p.id, hospital=fields['hospital'],
                condition_fingerprint=condition_fingerprint(self._condition(p)),
                resources=deepcopy(self._views(fields['hospital'], p.demand, t)))
        super().ev(t, typ, **fields)
        if typ == 'accept':
            self._dispatch_accept_refs.setdefault(fields['patient'], self.log[-1]['event_sequence'])
        if public is not None:
            event = next(row for row in reversed(self.log) if row['type'] == typ
                         and row.get('patient') == fields['patient'] and row.get('hospital') == fields['hospital'])
            if typ == 'ask':
                public.update(attempt=fields['attempt'], submitted_at=t,
                              ask_event_sequence=event['event_sequence'])
                self.retry_dispatch_contacts.append(public)
            else:
                public.update(received_t=t, wake_event_sequence=event['event_sequence'],
                    report_event_sequence=event['report_event_sequence'],
                    previous_retry_at=event['previous_retry_at'])
                self.retry_dispatch_wakes.append(public)

    def _dispatch_universe(self, p, loc, t):
        # This read does not query a hospital or alter observation/confirmation
        # clocks. Deliberately retain tried hospitals across contact episodes.
        open_hospitals = {a['hid'] for a in p.asks}
        valid = {hid for hid, expiry in p.valid.items() if expiry > t}
        failed = self.failed_hospitals[p.id]
        rows = []
        for hid in self._pool(p, loc, self.candidates(p, loc)):
            if hid in open_hospitals or hid in valid or hid in failed:
                continue
            resources = deepcopy(self._views(hid, p.demand, t))
            if any(value['status'] == 'fresh' and value['free'] < p.demand[r]
                   for r, value in resources.items()):
                continue
            rows.append(dict(hospital=hid, travel_minutes=self.travel(p, hid, loc), resources=resources))
        return rows

    def observed_inquiry_plan(self, p, candidates, loc, t):
        condition = self._condition(p)
        context = dict(schema_version=1, evaluated_t=t,
            patient=dict(id=p.id, state=p.state, first_commit_t=p.t_commit,
                first_accept_event_sequence=self._dispatch_accept_refs.get(p.id),
                condition=condition, condition_fingerprint=condition_fingerprint(condition)),
            episode=dict(id=self._episode_ids.get(p.id, 0), contacts=self._episode_contacts.get(p.id, 0)),
            universe=self._dispatch_universe(p, loc, t),
            contacts=deepcopy([row for row in self.retry_dispatch_contacts if row['patient'] == p.id]),
            retry_barriers=deepcopy([row for row in self.retry_dispatch_wakes if row['patient'] == p.id]))
        previous = self._dispatch_context
        self._dispatch_context = context
        try:
            result = super().observed_inquiry_plan(p, candidates, loc, t)
        finally:
            self._dispatch_context = previous
        row = self.inquiry_decisions[-1]
        self.retry_dispatch_records.append(dict(inquiry_event_sequence=row['event_sequence'],
            context=deepcopy(context), decision=deepcopy(row['decision'])))
        return result

    def _dispatch_decide(self, **inputs):
        if self._dispatch_context is None:
            raise AssertionError('actual original selector context required')
        return decide(self.retry_dispatch_variant, inputs=inputs, context=deepcopy(self._dispatch_context))

    def run_replay(self):
        branch = super().run_replay()
        branch.update(retry_dispatch_enabled=True, retry_dispatch_selector=self.retry_dispatch_variant,
            retry_dispatch_spec=deepcopy(RETRY_DISPATCH_SPEC),
            retry_dispatch_records=deepcopy(self.retry_dispatch_records),
            retry_dispatch_contacts=deepcopy(self.retry_dispatch_contacts),
            retry_dispatch_wakes=deepcopy(self.retry_dispatch_wakes))
        branch['metrics'].update(retry_dispatch_reviews=len(self.retry_dispatch_records),
            retry_dispatch_frontier_deferrals=sum(r['decision']['reason'] == 'frontier_not_offered'
                for r in self.retry_dispatch_records),
            retry_dispatch_wait_preservations=sum(r['decision']['reason'] == 'retry_wait_preserved'
                for r in self.retry_dispatch_records))
        return branch


class IndependentRetryDispatchReplay(RetryDispatchMixin, independent.ShortageProbeReplay):
    pass


class CooperativeRetryDispatchReplay(RetryDispatchMixin, cooperative.CooperativeProbeReplay):
    pass


def run_retry_dispatch_branch(scenario, *, allocation='H1', variant='frontier',
                              routing=None, branch_id=None, label=None):
    cooperative.v1.validate_joint_scenario(scenario)
    if allocation not in ('H1', 'C1') or variant not in VARIANTS:
        raise ValueError('known allocation and fixed dispatch variant required')
    enabled = scenario.get('inquiry_queue', {}).get('enabled', False)
    if type(enabled) is not bool:
        raise ValueError('declared queue capability must be boolean')
    code = branch_id or f'v5_{allocation}_{variant}'
    rule = cooperative.v1.HeuristicAssignmentRule(time_limit_seconds=.05, max_moves=2048) \
        if allocation == 'H1' else cooperative.v1.StableCPSATAssignmentRule(
            time_limit_seconds=.05, seed=0, require_backend=True)
    policy = cooperative.v1.Policy(rule=rule, k=3, wait=2, notify=True, name=code, observed_inquiry={})
    cls = CooperativeRetryDispatchReplay if enabled else IndependentRetryDispatchReplay
    arguments = dict(routing=routing, inquiry='v4', recovery=True, pending_protection=True,
        cohort_registration=True, scarce_routing=True, late_reply_recovery=True,
        late_pending_guard=True, shortage_probe=True, variant=variant)
    if enabled:
        arguments['cooperative_probe'] = True
    replay = cls(deepcopy(scenario), policy, **arguments)
    branch = replay.run_replay()
    branch.update(id=code, label=label or code, policy=cooperative.v1._policy_spec(policy),
        scarce_delay_spec=dict(inquiry='v4', allocation=allocation, cohort_registration=True, scarce_routing=True,
            actual_action='parallel_scarce_supplement', original_selector_candidates_unchanged=False,
            maximum_supplements_per_patient=1, recovery=True, pending_protection=True, pending_window_minutes=1,
            time_step_seconds=15, actual_selector_reused=False, original_seven_inputs_retained=True,
            base_selector_reused_as_kernel=True, extra_resource_change_publications=False),
        deadline_recovery_spec=dict(late_reply_recovery=True, scope='one_initial_scarce_supplement',
            late_reply_window_minutes=cooperative.v1.LATE_REPLY_WINDOW_MINUTES, original_timeout_unchanged=True,
            original_timeout_feedback_unchanged=True, blocking_wait_added=False,
            hospital_work_calendar_unchanged=not enabled, patient_condition_fingerprint_required=True,
            new_offer_ttl_from_actual_receipt=True, actual_reply_outcome_required=True,
            current_fresh_resource_check_required=True, current_valid_offer_rejects=True,
            newer_same_hospital_attempt_rejects=True),
        retry_dispatch_foundation_spec=dict(deepcopy(RETRY_DISPATCH_FOUNDATION_SPEC),
            mode='cooperative_queue' if enabled else 'independent_replies'))
    return branch


DEFAULT_RETRY_DISPATCH_POLICIES = tuple(dict(id=f'v5_{allocation}_{variant}',
    allocation=allocation, variant=variant) for variant in VARIANTS for allocation in ('H1', 'C1'))


def run_retry_dispatch_replay(scenario, policies=None, execution_order=None, routing=None):
    cooperative.v1.validate_joint_scenario(scenario)
    specs = deepcopy(list(DEFAULT_RETRY_DISPATCH_POLICIES if policies is None else policies))
    if any(not isinstance(row, dict) or not {'id', 'allocation', 'variant'} <= set(row)
           or set(row) - {'id', 'allocation', 'variant', 'label'} for row in specs):
        raise ValueError('exact retry dispatch policy rows required')
    ids = [row['id'] for row in specs]
    if not ids or len(ids) != len(set(ids)) or any(not independent._id(code) for code in ids):
        raise ValueError('unique nonempty policy ids required')
    order = list(ids if execution_order is None else execution_order)
    if len(order) != len(ids) or set(order) != set(ids):
        raise ValueError('each policy must execute exactly once')
    by_id = {row['id']: row for row in specs}
    output = {}
    for code in order:
        row = by_id[code]
        output[code] = run_retry_dispatch_branch(scenario, allocation=row['allocation'], variant=row['variant'],
            routing=routing, branch_id=code, label=row.get('label', code))
    return dict(schema_version=1, experiment_id=EXPERIMENT_ID, scenario=deepcopy(scenario),
                branches=[output[code] for code in ids], execution_order=order)
