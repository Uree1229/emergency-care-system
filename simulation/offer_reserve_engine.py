"""A separate reserve decision inside the unchanged public readiness stage."""
from copy import deepcopy

from . import public_offer_engine as parent
from .existing_info_engine import decide_early_offer
from .responsive_confirmation_engine import information_reason
from .offer_reserve_policy import VARIANTS, PUBLIC_OFFER_SPEC, decide_offer_reserve


EXPERIMENT_ID = 'offer_reserve_20261020'
NAMESPACE_FIELDS = parent.NAMESPACE_FIELDS | {'offer_reserve_base_contract'}
PUBLIC_OFFER_FOUNDATION_SPEC = dict(
    actual_replays=dict(
        independent_replies='simulation.offer_reserve_engine.IndependentOfferReserveReplay',
        cooperative_queue='simulation.offer_reserve_engine.CooperativeOfferReserveReplay'),
    base_replays=deepcopy(parent.PUBLIC_OFFER_FOUNDATION_SPEC['actual_replays']),
    joint_stage='simulation.public_offer_engine.OfferReadyJointStage',
    dynamic_new_stage='simulation.offer_reserve_engine.OfferReserveMixin._public_offer_stage',
    rank_variant='normal', actual_normal_selector_changed=False, actual_readiness_changed=True,
    original_joint_resolve_body_explicitly_retained=True,
    new_stage='after_original_early_loop_before_opportunity_and_pending_guards',
    original_probe_ack_late_and_original_early_stages_retained=True,
    new_marker_registers_original_backup_reference=True,
    actual_options_follow_original_valid_offer_order=True,
    actual_options_not_filtered=True, fixed_current_cohort_reserve_own_demand_copies=1,
    original_inquiry_work_clock_attempt_and_physical_accounting_rules_retained=True,
    actual_downstream_paths_and_counts_may_change=True,
    inherited_protocol_descriptions_are_nested_base_contract=True,
    extra_resource_change_publications=False, added_workers_or_beds=False)


class OfferReserveMixin:
    def __init__(self, *args, public_offer_variant='cohort_reserve', rank_variant='normal', **kwargs):
        if public_offer_variant not in VARIANTS or rank_variant != 'normal':
            raise ValueError('known reserve rule on the unchanged normal foundation required')
        # Original initialization is retained; only the dynamically dispatched
        # new-stage decision and its literal final descriptors are extended.
        super().__init__(*args, public_offer_variant='public_blocked', rank_variant='normal', **kwargs)
        self.public_offer_variant = public_offer_variant

    def _public_offer_stage(self, t):
        self.ev(t, 'public_offer_cycle')
        ready_ids = {p.id for p, _ in self.ready}
        for p in sorted((self.patients[pid] for pid in self._registered), key=lambda p:(-p.priority,p.t_req,p.id)):
            if p.state != 'SEARCH' or p.id in ready_ids or not p.asks or not p.valid \
                    or p.first_valid_t is None or t-p.first_valid_t >= self.policy.wait:
                continue
            snapshot = self._public_offer_snapshot(p, t)
            shadow = decide_early_offer(deepcopy(snapshot['original_snapshot']))
            decision = decide_offer_reserve(snapshot=snapshot, variant=self.public_offer_variant)
            self.ev(t, 'public_offer_review', patient=p.id, variant=self.public_offer_variant,
                record_index=len(self.public_offer_records))
            review = self.log[-1]['event_sequence']
            self.public_offer_records.append(dict(event_sequence=review, snapshot=deepcopy(snapshot),
                shadow_decision=deepcopy(shadow), decision=deepcopy(decision)))
            if not decision['eligible']:
                continue
            options = [hid for hid in p.valid
                if information_reason(self._views(hid,p.demand,t),p.demand) is None]
            if len(options) != len(decision['usable_options']) or set(options) != set(decision['usable_options']):
                raise AssertionError('reserve proof does not cover every original actual offer')
            self.ev(t, 'public_offer_ready', patient=p.id, hospital=decision['hospital'],
                options=list(options), review_event_sequence=review)
            for hid in options:
                eligible, reason, _ = self._check(p, hid, t, 'allocation')
                if not eligible or reason is not None:
                    raise AssertionError('reserve allocation view changed')
            self.ready.append((p, options));ready_ids.add(p.id)

    def run_replay(self):
        branch = super().run_replay()
        mode = 'cooperative_queue' if getattr(self, 'cooperative_queue_enabled', False) else 'independent_replies'
        branch.update(comparison_id=EXPERIMENT_ID, public_offer_variant=self.public_offer_variant,
            public_offer_spec=deepcopy(PUBLIC_OFFER_SPEC),
            public_offer_foundation_spec=dict(deepcopy(PUBLIC_OFFER_FOUNDATION_SPEC), mode=mode),
            offer_reserve_base_contract=dict(
                base_variant='public_blocked', descriptors_describe_frozen_base_not_actual_new_rule=True,
                public_offer_spec=deepcopy(parent.PUBLIC_OFFER_SPEC),
                public_offer_foundation_spec=dict(deepcopy(parent.PUBLIC_OFFER_FOUNDATION_SPEC), mode=mode)))
        return branch


class IndependentOfferReserveReplay(OfferReserveMixin, parent.IndependentPublicOfferReplay):
    pass


class CooperativeOfferReserveReplay(OfferReserveMixin, parent.CooperativePublicOfferReplay):
    pass


def run_offer_reserve_branch(scenario, *, allocation='H1', variant='cohort_reserve', branch_id=None, label=None):
    parent.parent.cooperative.v1.validate_joint_scenario(scenario)
    if allocation not in ('H1', 'C1') or variant not in VARIANTS:
        raise ValueError('known allocation and fixed reserve variant required')
    enabled = scenario.get('inquiry_queue', {}).get('enabled', False)
    if type(enabled) is not bool:
        raise ValueError('explicit original queue capability required')
    code = branch_id or f'offer_reserve_{allocation}_{variant}'
    core = parent.parent.cooperative.v1
    rule = core.HeuristicAssignmentRule(time_limit_seconds=.05,max_moves=2048) if allocation == 'H1' \
        else core.StableCPSATAssignmentRule(time_limit_seconds=.05,seed=0,require_backend=True)
    policy = core.Policy(rule=rule,k=3,wait=2,notify=True,name=code,observed_inquiry={})
    cls = CooperativeOfferReserveReplay if enabled else IndependentOfferReserveReplay
    arguments = dict(inquiry='v4',recovery=True,pending_protection=True,cohort_registration=True,
        scarce_routing=True,late_reply_recovery=True,late_pending_guard=True,shortage_probe=True,
        rank_variant='normal',public_offer_variant=variant)
    if enabled:arguments['cooperative_probe']=True
    branch = cls(deepcopy(scenario),policy,**arguments).run_replay()
    branch.update(id=code,label=label or code,policy=core._policy_spec(policy),
        scarce_delay_spec=dict(inquiry='v4',allocation=allocation,cohort_registration=True,scarce_routing=True,
            actual_action='parallel_scarce_supplement',original_selector_candidates_unchanged=False,
            maximum_supplements_per_patient=1,recovery=True,pending_protection=True,pending_window_minutes=1,
            time_step_seconds=15,actual_selector_reused=False,original_seven_inputs_retained=True,
            base_selector_reused_as_kernel=True,extra_resource_change_publications=False),
        deadline_recovery_spec=dict(late_reply_recovery=True,scope='one_initial_scarce_supplement',
            late_reply_window_minutes=core.LATE_REPLY_WINDOW_MINUTES,
            original_timeout_unchanged=True,original_timeout_feedback_unchanged=True,blocking_wait_added=False,
            hospital_work_calendar_unchanged=not enabled,patient_condition_fingerprint_required=True,
            new_offer_ttl_from_actual_receipt=True,actual_reply_outcome_required=True,
            current_fresh_resource_check_required=True,current_valid_offer_rejects=True,newer_same_hospital_attempt_rejects=True))
    return branch
