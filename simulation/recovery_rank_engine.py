"""Two isolated foundations with the same evidence-based normal rank exception."""
from copy import deepcopy

from . import cooperative_probe_engine as cooperative
from .retry_dispatch_engine import (IndependentRetryDispatchReplay, CooperativeRetryDispatchReplay,
    RETRY_DISPATCH_FOUNDATION_SPEC)
from .recovery_dispatch_engine import (IndependentRecoveryDispatchReplay, CooperativeRecoveryDispatchReplay)
from .retry_dispatch_policy import condition_fingerprint
from .recovery_rank_policy import VARIANTS, RECOVERY_RANK_SPEC, decide_recovery_rank

EXPERIMENT_ID = 'recovery_rank_20261018'
NAMESPACE_FIELDS = {'recovery_rank_enabled','recovery_rank_variant','recovery_rank_selector',
    'recovery_rank_spec','recovery_rank_foundation_spec','recovery_rank_base_contract',
    'recovery_rank_declines','recovery_rank_resource_transitions','recovery_rank_records',
    'recovery_rank_consumptions'}
RECOVERY_RANK_FOUNDATION_SPEC = dict(
    actual_replays=dict(
        normal_independent='simulation.recovery_rank_engine.IndependentNormalRecoveryRankReplay',
        normal_cooperative='simulation.recovery_rank_engine.CooperativeNormalRecoveryRankReplay',
        latched_independent='simulation.recovery_rank_engine.IndependentLatchedRecoveryRankReplay',
        latched_cooperative='simulation.recovery_rank_engine.CooperativeLatchedRecoveryRankReplay'),
    base_replays=dict(
        normal_independent='simulation.retry_dispatch_engine.IndependentRetryDispatchReplay',
        normal_cooperative='simulation.retry_dispatch_engine.CooperativeRetryDispatchReplay',
        latched_independent='simulation.recovery_dispatch_engine.IndependentRecoveryDispatchReplay',
        latched_cooperative='simulation.recovery_dispatch_engine.CooperativeRecoveryDispatchReplay'),
    original_seven_inputs_and_full_raw_retained=True,
    inherited_retry_dispatch_records='actual recovery_rank output with original factual frontier context',
    inherited_retry_selector_spec_and_foundation_are_nested_base_contract=True,
    normal_reopening_or_added_after_tick=False, latched_eligibility_unchanged=True,
    actual_contact_accounting_episode_rules_clock_fifo_readiness_and_resources_unchanged=True,
    actual_selector_changed=True)


class RecoveryRankMixin:
    def __init__(self,*args,rank_variant='latched',**kwargs):
        if rank_variant not in VARIANTS:
            raise ValueError('known fixed recovery rank variant required')
        self.recovery_rank_variant = rank_variant
        self.rank_declines=[];self.rank_transitions=[];self.rank_records=[];self.rank_consumptions=[]
        self._rank_report_refs={};self._rank_computation=None;self._rank_latest_decision={}
        if rank_variant == 'normal':
            kwargs['variant'] = 'frontier'
        else:
            kwargs['recovery_variant'] = 'latched'
        super().__init__(*args,**kwargs)

    def _receive_report(self,hid,resources,observed_at,t,source,packet_sequence=None):
        names=sorted(self.structural_capacities[hid])
        before=self.observations.snapshot(hid,names,now=t)
        refs={resource:self._rank_report_refs.get((hid,resource)) for resource in names}
        super()._receive_report(hid,resources,observed_at,t,source,packet_sequence)
        report=self.observation_records[-1]
        after=self.observations.snapshot(hid,names,now=t)
        if report['applied']:
            for resource in names:
                if resource in resources and after[resource]['sequence'] == report['packet_sequence'] \
                        and after[resource]['observed_at'] == observed_at:
                    self._rank_report_refs[hid,resource] = report['event_sequence']
            if any(before[resource][key] != after[resource][key]
                    for resource in names for key in ('capacity','used','status')):
                self.rank_transitions.append(dict(hospital=hid,t=t,report_event_sequence=report['event_sequence'],
                    before_report_event_sequences=refs,before_resources=deepcopy(before),after_resources=deepcopy(after)))

    def ev(self,t,typ,**fields):
        decline=None
        if typ == 'decline':
            patient=self.patients[fields['patient']]
            contacts=[row for row in self.retry_dispatch_contacts if row['patient'] == patient.id
                and row['hospital'] == fields['hospital']]
            if contacts:
                contact=contacts[-1]
                decline=dict(patient=patient.id,hospital=fields['hospital'],t=t,
                    ask_event_sequence=contact['ask_event_sequence'],attempt=contact['attempt'],
                    condition_fingerprint=condition_fingerprint(self._condition(patient)),
                    resources=deepcopy(self._views(fields['hospital'],patient.demand,t)))
        super().ev(t,typ,**fields)
        event=self.log[-1]
        if decline is not None:
            decline['decline_event_sequence']=event['event_sequence']
            self.rank_declines.append(decline)
        if typ == 'ask':
            latest=self._rank_latest_decision.get(fields['patient'])
            if latest is not None:
                row,fp=latest
                detail=row['decision']['evidence']['recovery_rank']
                hid=fields['hospital']
                if row['rank_context']['evaluated_t'] == t and hid in detail['rank_send']:
                    proof=detail['selected_recovery_proofs'][hid]
                    if any(used['patient'] == fields['patient'] and used['condition_fingerprint'] == fp
                            for used in self.rank_consumptions):
                        raise AssertionError('one patient condition consumed more than one actual rank inquiry')
                    self.rank_consumptions.append(dict(patient=fields['patient'],hospital=hid,
                        token_id=proof['token_id'],condition_fingerprint=fp,
                        ask_event_sequence=event['event_sequence'],inquiry_event_sequence=row['inquiry_event_sequence'],
                        decline_event_sequence=proof['decline_event_sequence'],report_event_sequence=proof['report_event_sequence']))

    def _rank_context(self,patient,t):
        return dict(schema_version=1,evaluated_t=t,variant=self.recovery_rank_variant,
            valid_offers=[dict(hospital=hid,expires_at=expiry) for hid,expiry in sorted(patient.valid.items())],
            declines=deepcopy([row for row in self.rank_declines if row['patient'] == patient.id]),
            transitions=deepcopy(self.rank_transitions),
            consumptions=deepcopy([row for row in self.rank_consumptions if row['patient'] == patient.id]))

    def _dispatch_decide(self,**inputs):
        if self._dispatch_context is None:
            raise AssertionError('original frontier input context required')
        context=deepcopy(self._dispatch_context)
        rank_context=self._rank_context(self.patients[context['patient']['id']],context['evaluated_t'])
        decision=decide_recovery_rank(inputs=deepcopy(inputs),context=context,rank_context=rank_context)
        self._rank_computation=(rank_context,deepcopy(decision))
        return decision

    def observed_inquiry_plan(self,patient,candidates,loc,t):
        previous=self._rank_computation
        self._rank_computation=None
        try:
            result=super().observed_inquiry_plan(patient,candidates,loc,t)
            if self._rank_computation is None:
                raise AssertionError('actual normal selector did not produce rank context')
            rank_context,decision=self._rank_computation
            actual=self.inquiry_decisions[-1]
            if actual['decision'] != decision:
                raise AssertionError('actual selector output differs from rank output')
            row=dict(inquiry_event_sequence=actual['event_sequence'],rank_context=deepcopy(rank_context),
                decision=deepcopy(decision))
            self.rank_records.append(row)
            self._rank_latest_decision[patient.id]=(row,condition_fingerprint(self._condition(patient)))
            return result
        finally:
            self._rank_computation=previous

    def run_replay(self):
        branch=super().run_replay()
        mode='cooperative_queue' if getattr(self,'cooperative_queue_enabled',False) else 'independent_replies'
        base=dict(retry_dispatch_selector=branch.pop('retry_dispatch_selector'),
            retry_dispatch_spec=branch.pop('retry_dispatch_spec'),
            retry_dispatch_foundation_spec=dict(deepcopy(RETRY_DISPATCH_FOUNDATION_SPEC),mode=mode))
        if 'recovery_dispatch_foundation_spec' in branch:
            base['recovery_dispatch_foundation_spec']=branch.pop('recovery_dispatch_foundation_spec')
        branch.update(recovery_rank_enabled=True,recovery_rank_variant=self.recovery_rank_variant,
            recovery_rank_selector='recovery_rank_'+self.recovery_rank_variant,
            recovery_rank_spec=deepcopy(RECOVERY_RANK_SPEC),
            recovery_rank_foundation_spec=dict(deepcopy(RECOVERY_RANK_FOUNDATION_SPEC),mode=mode),
            recovery_rank_base_contract=base,recovery_rank_declines=deepcopy(self.rank_declines),
            recovery_rank_resource_transitions=deepcopy(self.rank_transitions),
            recovery_rank_records=deepcopy(self.rank_records),recovery_rank_consumptions=deepcopy(self.rank_consumptions))
        branch['metrics'].update(recovery_rank_reviews=len(self.rank_records),
            recovery_rank_tier_reviews=sum(bool(row['decision']['evidence']['recovery_rank']['recovery_tier'])
                for row in self.rank_records),recovery_rank_sent=len(self.rank_consumptions))
        return branch


class IndependentNormalRecoveryRankReplay(RecoveryRankMixin,IndependentRetryDispatchReplay):pass
class CooperativeNormalRecoveryRankReplay(RecoveryRankMixin,CooperativeRetryDispatchReplay):pass
class IndependentLatchedRecoveryRankReplay(RecoveryRankMixin,IndependentRecoveryDispatchReplay):pass
class CooperativeLatchedRecoveryRankReplay(RecoveryRankMixin,CooperativeRecoveryDispatchReplay):pass


def run_recovery_rank_branch(scenario,*,allocation='H1',variant='latched',branch_id=None,label=None):
    cooperative.v1.validate_joint_scenario(scenario)
    if allocation not in ('H1','C1') or variant not in VARIANTS:
        raise ValueError('known fixed recovery rank allocation and variant required')
    enabled=scenario.get('inquiry_queue',{}).get('enabled',False)
    if type(enabled) is not bool:
        raise ValueError('explicit declared queue support required')
    code=branch_id or f'recovery_rank_{allocation}_{variant}'
    rule=cooperative.v1.HeuristicAssignmentRule(time_limit_seconds=.05,max_moves=2048) if allocation == 'H1' else \
        cooperative.v1.StableCPSATAssignmentRule(time_limit_seconds=.05,seed=0,require_backend=True)
    policy=cooperative.v1.Policy(rule=rule,k=3,wait=2,notify=True,name=code,observed_inquiry={})
    cls=(CooperativeNormalRecoveryRankReplay if enabled else IndependentNormalRecoveryRankReplay) if variant == 'normal' else \
        (CooperativeLatchedRecoveryRankReplay if enabled else IndependentLatchedRecoveryRankReplay)
    arguments=dict(inquiry='v4',recovery=True,pending_protection=True,cohort_registration=True,
        scarce_routing=True,late_reply_recovery=True,late_pending_guard=True,shortage_probe=True,rank_variant=variant)
    if enabled:arguments['cooperative_probe']=True
    branch=cls(deepcopy(scenario),policy,**arguments).run_replay()
    branch.update(id=code,label=label or code,policy=cooperative.v1._policy_spec(policy),
        scarce_delay_spec=dict(inquiry='v4',allocation=allocation,cohort_registration=True,scarce_routing=True,
            actual_action='parallel_scarce_supplement',original_selector_candidates_unchanged=False,
            maximum_supplements_per_patient=1,recovery=True,pending_protection=True,pending_window_minutes=1,
            time_step_seconds=15,actual_selector_reused=False,original_seven_inputs_retained=True,
            base_selector_reused_as_kernel=True,extra_resource_change_publications=False),
        deadline_recovery_spec=dict(late_reply_recovery=True,scope='one_initial_scarce_supplement',
            late_reply_window_minutes=cooperative.v1.LATE_REPLY_WINDOW_MINUTES,original_timeout_unchanged=True,
            original_timeout_feedback_unchanged=True,blocking_wait_added=False,hospital_work_calendar_unchanged=not enabled,
            patient_condition_fingerprint_required=True,new_offer_ttl_from_actual_receipt=True,
            actual_reply_outcome_required=True,current_fresh_resource_check_required=True,
            current_valid_offer_rejects=True,newer_same_hospital_attempt_rejects=True))
    return branch
