"""A new readiness stage inside the explicitly retained normal workflow."""
from copy import deepcopy
import time

from . import recovery_rank_engine as parent
from .existing_info_engine import decide_early_offer
from .joint_policy_engine import JointPolicyReplay, decide_joint_protection, incomplete_confirmation
from .partial_information_engine import PartialInformationReplay
from .responsive_confirmation_engine import information_reason
from .public_offer_policy import VARIANTS, PUBLIC_OFFER_SPEC, decide_public_offer


EXPERIMENT_ID = 'public_offer_20261020'
NAMESPACE_FIELDS = {'public_offer_enabled', 'public_offer_variant', 'public_offer_spec',
    'public_offer_foundation_spec', 'public_offer_base_contract', 'public_offer_records'}
BASE_CONTRACT_FIELDS = ('recovery_rank_selector', 'recovery_rank_spec',
    'recovery_rank_foundation_spec', 'recovery_rank_base_contract',
    'shortage_probe_spec', 'cooperative_probe_spec', 'comparison_id')
PUBLIC_OFFER_FOUNDATION_SPEC = dict(
    actual_replays=dict(
        independent_replies='simulation.public_offer_engine.IndependentPublicOfferReplay',
        cooperative_queue='simulation.public_offer_engine.CooperativePublicOfferReplay'),
    base_replays=dict(
        independent_replies='simulation.recovery_rank_engine.IndependentNormalRecoveryRankReplay',
        cooperative_queue='simulation.recovery_rank_engine.CooperativeNormalRecoveryRankReplay'),
    joint_stage='simulation.public_offer_engine.OfferReadyJointStage',
    rank_variant='normal', actual_normal_selector_changed=False, actual_readiness_changed=True,
    original_joint_resolve_body_explicitly_retained=True,
    new_stage='after_original_early_loop_before_opportunity_and_pending_guards',
    original_probe_ack_late_and_original_early_stages_retained=True,
    new_marker_registers_original_backup_reference=True,
    actual_options_follow_original_valid_offer_order=True,
    original_inquiry_work_clock_attempt_and_physical_accounting_rules_retained=True,
    actual_downstream_paths_and_counts_may_change=True,
    inherited_protocol_descriptions_are_nested_base_contract=True,
    extra_resource_change_publications=False, added_workers_or_beds=False)


class OfferReadyJointStage(JointPolicyReplay):
    def resolve_batch(self, t):
        # Retain the frozen JointPolicyReplay stage body explicitly. The sole
        # insertion is after its complete original early loop and before guards.
        if self.early_offers_enabled:
            started=time.perf_counter();self.ev(t,'early_offer_cycle')
            ready_ids={p.id for p,_ in self.ready}
            for p in sorted((self.patients[pid] for pid in self._registered),key=lambda p:(-p.priority,p.t_req,p.id)):
                if p.state!='SEARCH' or p.id in ready_ids or not p.asks or not p.valid \
                        or p.first_valid_t is None or t-p.first_valid_t>=self.policy.wait:
                    continue
                snapshot=self._early_snapshot(p,t);decision_started=time.perf_counter()
                result=decide_early_offer(snapshot);elapsed=time.perf_counter()-decision_started
                self.ev(t,'early_offer_review',record_index=len(self.early_offer_records))
                self.early_offer_records.append(dict(snapshot=snapshot,result=result,
                    event_sequence=self.log[-1]['event_sequence'],elapsed_seconds=elapsed))
                if not result['eligible']:continue
                self.ev(t,'early_offer_ready',patient=p.id,hospital=result['hospital'],
                        review_event_sequence=self.early_offer_records[-1]['event_sequence'])
                options=[]
                for hid in p.valid:
                    if information_reason(self._views(hid,p.demand,t),p.demand) is None:
                        eligible,reason,_=self._check(p,hid,t,'allocation')
                        if not eligible or reason is not None:raise AssertionError('early view changed')
                        options.append(hid)
                self.ready.append((p,options));ready_ids.add(p.id)
            self.early_offer_seconds+=time.perf_counter()-started
        self._public_offer_stage(t)
        if not self.ready:return
        started=time.perf_counter();snapshot=self._protection_snapshot(t)
        first,held=deepcopy(self.waiting_since),deepcopy(self.held_since)
        filtered=deepcopy(snapshot)
        for waiter in filtered['waiting']:
            waiter['offers']=[offer for offer in waiter['offers']
                if not incomplete_confirmation(offer,first.get(waiter['id'],t))]
        result=decide_joint_protection(filtered,first,held,grace_minutes=self.grace_minutes)
        self.held_since=deepcopy(result['held_since_after'])
        self.ev(t,'opportunity_guard',record_index=len(self.protection_records))
        self.protection_records.append(dict(snapshot=snapshot,waiting_since=first,held_since=held,
            result=deepcopy(result),grace_minutes=self.grace_minutes,event_sequence=self.log[-1]['event_sequence']))
        for hold in result['holds']:self.ev(t,'opportunity_hold',**hold)
        keep=set(result['ready_after']);self.ready=[(p,options) for p,options in self.ready if p.id in keep]
        self.protection_seconds+=time.perf_counter()-started
        self._pending_guard(t)
        PartialInformationReplay.resolve_batch(self,t)


class PublicOfferMixin:
    def __init__(self, *args, public_offer_variant='public_blocked', rank_variant='normal', **kwargs):
        if public_offer_variant not in VARIANTS or rank_variant != 'normal':
            raise ValueError('known public-offer rule on the normal foundation required')
        self.public_offer_variant = public_offer_variant
        self.public_offer_records = []
        super().__init__(*args, rank_variant='normal', **kwargs)

    def ev(self, t, typ, **fields):
        super().ev(t, typ, **fields)
        if typ == 'public_offer_ready':
            # The source is the new actual marker, never an impersonated old one.
            self._early_ready_refs[fields['patient'], t] = self.log[-1]['event_sequence']

    def _public_offer_snapshot(self, p, t):
        original = self._early_snapshot(p, t)
        return dict(schema_version=1, evaluated_t=t,
            mode='cooperative_queue' if getattr(self, 'cooperative_queue_enabled', False) else 'independent_replies',
            patient_state=p.state, first_commit_t=p.t_commit, wait_minutes=self.policy.wait,
            original_snapshot=original, pending_inquiries=deepcopy(self._pending_view(p.asks)),
            structural_resource_views={row['hospital']:deepcopy(self._views(row['hospital'],p.demand,t))
                for row in original['structural_candidates']})

    def _public_offer_stage(self, t):
        self.ev(t, 'public_offer_cycle')
        ready_ids = {p.id for p, _ in self.ready}
        for p in sorted((self.patients[pid] for pid in self._registered), key=lambda p:(-p.priority,p.t_req,p.id)):
            if p.state != 'SEARCH' or p.id in ready_ids or not p.asks or not p.valid \
                    or p.first_valid_t is None or t-p.first_valid_t >= self.policy.wait:
                continue
            snapshot = self._public_offer_snapshot(p, t)
            shadow = decide_early_offer(deepcopy(snapshot['original_snapshot']))
            decision = decide_public_offer(snapshot=snapshot, variant=self.public_offer_variant)
            self.ev(t, 'public_offer_review', patient=p.id, variant=self.public_offer_variant,
                record_index=len(self.public_offer_records))
            review = self.log[-1]['event_sequence']
            self.public_offer_records.append(dict(event_sequence=review, snapshot=deepcopy(snapshot),
                shadow_decision=deepcopy(shadow), decision=deepcopy(decision)))
            if not decision['eligible']:
                continue
            options = [hid for hid in p.valid
                if information_reason(self._views(hid,p.demand,t),p.demand) is None]
            self.ev(t, 'public_offer_ready', patient=p.id, hospital=decision['hospital'],
                options=list(options), review_event_sequence=review)
            for hid in options:
                eligible, reason, _ = self._check(p, hid, t, 'allocation')
                if not eligible or reason is not None:
                    raise AssertionError('public-offer allocation view changed')
            self.ready.append((p, options));ready_ids.add(p.id)

    def run_replay(self):
        branch = super().run_replay()
        inherited = {key:branch.pop(key) for key in BASE_CONTRACT_FIELDS if key in branch}
        mode = 'cooperative_queue' if getattr(self, 'cooperative_queue_enabled', False) else 'independent_replies'
        branch.update(comparison_id=EXPERIMENT_ID, public_offer_enabled=True,
            public_offer_variant=self.public_offer_variant, public_offer_spec=deepcopy(PUBLIC_OFFER_SPEC),
            public_offer_foundation_spec=dict(deepcopy(PUBLIC_OFFER_FOUNDATION_SPEC), mode=mode),
            public_offer_base_contract=deepcopy(inherited), public_offer_records=deepcopy(self.public_offer_records))
        branch['metrics'].update(public_offer_reviews=len(self.public_offer_records),
            public_offer_ready=sum(row['decision']['eligible'] for row in self.public_offer_records),
            public_offer_cycles=sum(event['type'] == 'public_offer_cycle' for event in branch['events']))
        return branch


class IndependentPublicOfferReplay(PublicOfferMixin, parent.IndependentNormalRecoveryRankReplay, OfferReadyJointStage):
    pass


class CooperativePublicOfferReplay(PublicOfferMixin, parent.CooperativeNormalRecoveryRankReplay, OfferReadyJointStage):
    pass


def run_public_offer_branch(scenario, *, allocation='H1', variant='public_blocked', branch_id=None, label=None):
    parent.cooperative.v1.validate_joint_scenario(scenario)
    if allocation not in ('H1', 'C1') or variant not in VARIANTS:
        raise ValueError('known allocation and fixed public-offer variant required')
    enabled = scenario.get('inquiry_queue', {}).get('enabled', False)
    if type(enabled) is not bool:
        raise ValueError('explicit original queue capability required')
    code = branch_id or f'public_offer_{allocation}_{variant}'
    rule = parent.cooperative.v1.HeuristicAssignmentRule(time_limit_seconds=.05,max_moves=2048) if allocation == 'H1' \
        else parent.cooperative.v1.StableCPSATAssignmentRule(time_limit_seconds=.05,seed=0,require_backend=True)
    policy = parent.cooperative.v1.Policy(rule=rule,k=3,wait=2,notify=True,name=code,observed_inquiry={})
    cls = CooperativePublicOfferReplay if enabled else IndependentPublicOfferReplay
    arguments = dict(inquiry='v4',recovery=True,pending_protection=True,cohort_registration=True,
        scarce_routing=True,late_reply_recovery=True,late_pending_guard=True,shortage_probe=True,
        rank_variant='normal',public_offer_variant=variant)
    if enabled:arguments['cooperative_probe']=True
    branch = cls(deepcopy(scenario),policy,**arguments).run_replay()
    branch.update(id=code,label=label or code,policy=parent.cooperative.v1._policy_spec(policy),
        scarce_delay_spec=dict(inquiry='v4',allocation=allocation,cohort_registration=True,scarce_routing=True,
            actual_action='parallel_scarce_supplement',original_selector_candidates_unchanged=False,
            maximum_supplements_per_patient=1,recovery=True,pending_protection=True,pending_window_minutes=1,
            time_step_seconds=15,actual_selector_reused=False,original_seven_inputs_retained=True,
            base_selector_reused_as_kernel=True,extra_resource_change_publications=False),
        deadline_recovery_spec=dict(late_reply_recovery=True,scope='one_initial_scarce_supplement',
            late_reply_window_minutes=parent.cooperative.v1.LATE_REPLY_WINDOW_MINUTES,
            original_timeout_unchanged=True,original_timeout_feedback_unchanged=True,blocking_wait_added=False,
            hospital_work_calendar_unchanged=not enabled,patient_condition_fingerprint_required=True,
            new_offer_ttl_from_actual_receipt=True,actual_reply_outcome_required=True,
            current_fresh_resource_check_required=True,current_valid_offer_rejects=True,newer_same_hospital_attempt_rejects=True))
    return branch
