"""Optional-probe veto wrappers on the unchanged normal recovery-rank lane."""
from copy import deepcopy

from . import recovery_rank_engine as parent
from .probe_guard_policy import VARIANTS, PROBE_GUARD_SPEC, decide_probe_guard
from .shortage_probe_engine import decide_shortage_probe


EXPERIMENT_ID = 'probe_guard_20261020'
NAMESPACE_FIELDS = {'probe_guard_enabled', 'probe_guard_variant', 'probe_guard_spec',
    'probe_guard_foundation_spec', 'probe_guard_base_contract', 'probe_guard_records'}
BASE_CONTRACT_FIELDS = ('recovery_rank_selector', 'recovery_rank_spec',
    'recovery_rank_foundation_spec', 'recovery_rank_base_contract',
    'shortage_probe_spec', 'cooperative_probe_spec', 'comparison_id')
PROBE_GUARD_FOUNDATION_SPEC = dict(
    actual_replays=dict(
        independent_replies='simulation.probe_guard_engine.IndependentProbeGuardReplay',
        cooperative_queue='simulation.probe_guard_engine.CooperativeProbeGuardReplay'),
    base_replays=dict(
        independent_replies='simulation.recovery_rank_engine.IndependentNormalRecoveryRankReplay',
        cooperative_queue='simulation.recovery_rank_engine.CooperativeNormalRecoveryRankReplay'),
    rank_variant='normal', actual_normal_selector_changed=False,
    changed_hook='_maybe_shortage_probe',
    original_due_review_records='full snapshot, shadow original decision and actual guard decision',
    shadow_decision_executes_no_inquiry=True,
    inherited_protocol_descriptions_are_nested_base_contract=True,
    original_normal_calls_readiness_offers_routing_and_queue_rules_unchanged=True,
    actual_contact_attempt_episode_and_last_contact_accounting_unchanged=True,
    extra_resource_change_publications=False, added_workers_or_beds=False)


class ProbeGuardMixin:
    def __init__(self, *args, probe_guard_variant='off', rank_variant='normal', **kwargs):
        if probe_guard_variant not in VARIANTS or rank_variant != 'normal':
            raise ValueError('fixed guard variant on the normal recovery-rank foundation required')
        self.probe_guard_variant = probe_guard_variant
        self.probe_guard_records = []
        super().__init__(*args, rank_variant='normal', **kwargs)

    def _probe_guard_review_due(self, p, t):
        if getattr(self, 'cooperative_probe_enabled', False) \
                and not getattr(self, 'cooperative_queue_enabled', False):
            return False
        return not (p.state != 'SEARCH' or p.t_commit is not None
            or self._episode_ids.get(p.id, 0) != 0 or p.inquiries < 1 or not p.asks
            or self._probe_counts.get(p.id, 0) or p.id not in self._probe_first_asks
            or t < self._probe_first_asks[p.id]['submitted_at'] + .5
            or t < self._last_contact_t.get(p.id, t) + .5
            or self._episode_contacts.get(p.id, 0) >= 10 or len(p.asks) >= 3)

    def _maybe_shortage_probe(self, p, t):
        if not self._probe_guard_review_due(p, t):
            return
        snapshot = self._probe_snapshot(p, t)
        shadow = decide_shortage_probe(snapshot)
        decision = decide_probe_guard(self.probe_guard_variant, snapshot=snapshot)
        self.ev(t, 'probe_guard_review', patient=p.id, variant=self.probe_guard_variant,
            record_index=len(self.probe_guard_records))
        self.probe_guard_records.append(dict(event_sequence=self.log[-1]['event_sequence'],
            snapshot=deepcopy(snapshot), shadow_decision=deepcopy(shadow),
            guard_decision=deepcopy(decision)))
        if decision['action'] == 'veto':
            return
        previous_count = len(self.shortage_probe_records)
        result = super()._maybe_shortage_probe(p, t)
        if len(self.shortage_probe_records) != previous_count + 1:
            raise AssertionError('delegated original probe hook omitted its due review')
        actual = self.shortage_probe_records[-1]
        if actual['snapshot'] != snapshot or actual['result'] != shadow:
            raise AssertionError('guard delegation changed the original snapshot or probe decision')
        return result

    def run_replay(self):
        branch = super().run_replay()
        inherited = {key: branch.pop(key) for key in BASE_CONTRACT_FIELDS if key in branch}
        mode = 'cooperative_queue' if getattr(self, 'cooperative_queue_enabled', False) else 'independent_replies'
        branch.update(comparison_id=EXPERIMENT_ID, probe_guard_enabled=True,
            probe_guard_variant=self.probe_guard_variant,
            probe_guard_spec=deepcopy(PROBE_GUARD_SPEC),
            probe_guard_foundation_spec=dict(deepcopy(PROBE_GUARD_FOUNDATION_SPEC), mode=mode),
            probe_guard_base_contract=deepcopy(inherited),
            probe_guard_records=deepcopy(self.probe_guard_records))
        branch['metrics'].update(probe_guard_reviews=len(self.probe_guard_records),
            probe_guard_vetoes=sum(row['guard_decision']['action'] == 'veto' for row in self.probe_guard_records),
            probe_guard_delegations=sum(row['guard_decision']['action'] == 'delegate' for row in self.probe_guard_records),
            probe_guard_shadow_selected=sum(bool(row['shadow_decision']['send']) for row in self.probe_guard_records))
        return branch


class IndependentProbeGuardReplay(ProbeGuardMixin, parent.IndependentNormalRecoveryRankReplay):
    pass


class CooperativeProbeGuardReplay(ProbeGuardMixin, parent.CooperativeNormalRecoveryRankReplay):
    pass


def run_probe_guard_branch(scenario, *, allocation='H1', variant='off', branch_id=None, label=None):
    parent.cooperative.v1.validate_joint_scenario(scenario)
    if allocation not in ('H1', 'C1') or variant not in VARIANTS:
        raise ValueError('known allocation and fixed optional-probe guard variant required')
    enabled = scenario.get('inquiry_queue', {}).get('enabled', False)
    if type(enabled) is not bool:
        raise ValueError('explicit declared queue support required')
    code = branch_id or f'probe_guard_{allocation}_{variant}'
    rule = parent.cooperative.v1.HeuristicAssignmentRule(time_limit_seconds=.05, max_moves=2048) \
        if allocation == 'H1' else parent.cooperative.v1.StableCPSATAssignmentRule(
            time_limit_seconds=.05, seed=0, require_backend=True)
    policy = parent.cooperative.v1.Policy(rule=rule, k=3, wait=2, notify=True, name=code, observed_inquiry={})
    cls = CooperativeProbeGuardReplay if enabled else IndependentProbeGuardReplay
    arguments = dict(inquiry='v4', recovery=True, pending_protection=True,
        cohort_registration=True, scarce_routing=True, late_reply_recovery=True,
        late_pending_guard=True, shortage_probe=True, rank_variant='normal', probe_guard_variant=variant)
    if enabled:
        arguments['cooperative_probe'] = True
    branch = cls(deepcopy(scenario), policy, **arguments).run_replay()
    branch.update(id=code, label=label or code, policy=parent.cooperative.v1._policy_spec(policy),
        scarce_delay_spec=dict(inquiry='v4', allocation=allocation, cohort_registration=True, scarce_routing=True,
            actual_action='parallel_scarce_supplement', original_selector_candidates_unchanged=False,
            maximum_supplements_per_patient=1, recovery=True, pending_protection=True, pending_window_minutes=1,
            time_step_seconds=15, actual_selector_reused=False, original_seven_inputs_retained=True,
            base_selector_reused_as_kernel=True, extra_resource_change_publications=False),
        deadline_recovery_spec=dict(late_reply_recovery=True, scope='one_initial_scarce_supplement',
            late_reply_window_minutes=parent.cooperative.v1.LATE_REPLY_WINDOW_MINUTES,
            original_timeout_unchanged=True, original_timeout_feedback_unchanged=True, blocking_wait_added=False,
            hospital_work_calendar_unchanged=not enabled, patient_condition_fingerprint_required=True,
            new_offer_ttl_from_actual_receipt=True, actual_reply_outcome_required=True,
            current_fresh_resource_check_required=True, current_valid_offer_rejects=True,
            newer_same_hospital_attempt_rejects=True))
    return branch
