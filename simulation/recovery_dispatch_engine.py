"""Isolated actual-decline/recovery eligibility on the frozen frontier layer."""
from copy import deepcopy

from . import cooperative_probe_engine as cooperative
from .retry_dispatch_engine import (IndependentRetryDispatchReplay,CooperativeRetryDispatchReplay,
    RETRY_DISPATCH_FOUNDATION_SPEC)
from .retry_dispatch_policy import condition_fingerprint
from .recovery_dispatch_policy import VARIANTS,RECOVERY_DISPATCH_SPEC,decide_recovery_reopen

EXPERIMENT_ID='recovery_dispatch_20261018'
NAMESPACE_FIELDS={'recovery_dispatch_enabled','recovery_dispatch_variant','recovery_dispatch_spec',
    'recovery_dispatch_foundation_spec','recovery_dispatch_declines','recovery_dispatch_resource_transitions',
    'recovery_dispatch_reviews','recovery_dispatch_reopens','recovery_dispatch_consumptions'}
RECOVERY_FOUNDATION_SPEC=dict(base_dispatch='frontier',
    base_dispatch_replays=dict(independent_replies='simulation.retry_dispatch_engine.IndependentRetryDispatchReplay',
        cooperative_queue='simulation.retry_dispatch_engine.CooperativeRetryDispatchReplay'),
    actual_recovery_replays=dict(independent_replies='simulation.recovery_dispatch_engine.IndependentRecoveryDispatchReplay',
        cooperative_queue='simulation.recovery_dispatch_engine.CooperativeRecoveryDispatchReplay'),
    replaced_metadata_field='retry_dispatch_foundation_spec',eligibility_changed=True,
    pure_frontier_records_and_original_seven_inputs_retained=True,
    original_physical_clinical_queue_and_readiness_rules_retained=True)


class RecoveryDispatchMixin:
    def __init__(self,*args,recovery_variant='latched',**kwargs):
        if recovery_variant not in VARIANTS:raise ValueError('known recovery mode required')
        self.recovery_variant=recovery_variant
        self.recovery_declines=[];self.recovery_transitions=[];self.recovery_reviews=[]
        self.recovery_reopens=[];self.recovery_consumptions=[]
        self._recovery_report_refs={};self._recovery_sending=False;self._recovery_after_tick=False
        super().__init__(*args,variant='frontier',**kwargs)

    def _receive_report(self,hid,resources,observed_at,t,source,packet_sequence=None):
        names=sorted(self.structural_capacities[hid])
        before=self.observations.snapshot(hid,names,now=t)
        refs={r:self._recovery_report_refs.get((hid,r)) for r in names}
        super()._receive_report(hid,resources,observed_at,t,source,packet_sequence)
        report=self.observation_records[-1]
        after=self.observations.snapshot(hid,names,now=t)
        if report['applied']:
            for r in names:
                if r in resources and after[r]['sequence']==report['packet_sequence'] \
                        and after[r]['observed_at']==observed_at:
                    self._recovery_report_refs[hid,r]=report['event_sequence']
            if any(before[r][key]!=after[r][key] for r in names for key in ('capacity','used','status')):
                self.recovery_transitions.append(dict(hospital=hid,t=t,report_event_sequence=report['event_sequence'],
                    before_report_event_sequences=refs,before_resources=deepcopy(before),after_resources=deepcopy(after)))

    def ev(self,t,typ,**fields):
        public=None
        if typ=='decline':
            p=self.patients[fields['patient']]
            contacts=[row for row in self.retry_dispatch_contacts if row['patient']==p.id
                      and row['hospital']==fields['hospital']]
            if contacts:
                ask=contacts[-1]
                public=dict(patient=p.id,hospital=fields['hospital'],t=t,
                    ask_event_sequence=ask['ask_event_sequence'],attempt=ask['attempt'],
                    condition_fingerprint=condition_fingerprint(self._condition(p)),
                    resources=deepcopy(self._views(fields['hospital'],p.demand,t)))
        super().ev(t,typ,**fields)
        if public is not None:
            public['decline_event_sequence']=self.log[-1]['event_sequence']
            self.recovery_declines.append(public)

    def _recovery_snapshot(self,p,t,exclude):
        loc=self.position(p,t)
        excluded=set(exclude)|self.failed_hospitals[p.id]
        candidates=[dict(hospital=hid,travel_minutes=self.travel(p,hid,loc),
                         resources=deepcopy(self._views(hid,p.demand,t)))
                    for hid in self._pool(p,loc,self.candidates(p,loc,exclude=excluded))]
        return dict(t=t,variant=self.recovery_variant,
            patient=dict(id=p.id,state=p.state,first_commit_t=p.t_commit,
                first_accept_event_sequence=self._dispatch_accept_refs.get(p.id),condition=self._condition(p),
                condition_fingerprint=condition_fingerprint(self._condition(p)),
                request_event_sequence=self._request_refs[p.id]),
            scheduling=dict(episode_id=self._episode_ids.get(p.id,0),episode_contacts=self._episode_contacts.get(p.id,0),
                retry_at=p.retry_at,next_policy_review_at=self._next_policy_review.get(p.id,0),
                last_contact_at=self._last_contact_t.get(p.id)),
            tried_hospitals=sorted(p.tried),pending_inquiries=self._pending_view(p.asks),
            valid_offers=[dict(hospital=hid,expires_at=expiry) for hid,expiry in sorted(p.valid.items())],
            candidates=candidates,contacts=deepcopy([row for row in self.retry_dispatch_contacts if row['patient']==p.id]),
            declines=deepcopy([row for row in self.recovery_declines if row['patient']==p.id]),
            transitions=deepcopy(self.recovery_transitions),
            consumptions=deepcopy([row for row in self.recovery_consumptions if row['patient']==p.id]))

    def send_asks(self,p,t,exclude=()):
        if self._recovery_sending or p.id not in getattr(self,'_registered',set()) \
                or not any(row['patient']==p.id for row in self.recovery_declines):
            return super().send_asks(p,t,exclude=exclude)
        self._recovery_sending=True
        try:
            self._information_cycle(t)
            if self._observed_review_at.get(p.id)==t or t<self._next_policy_review.get(p.id,0):
                return super().send_asks(p,t,exclude=exclude)
            snapshot=self._recovery_snapshot(p,t,exclude)
            result=decide_recovery_reopen(snapshot)
            self.ev(t,'recovery_reopen_review',patient=p.id,variant=self.recovery_variant,
                    record_index=len(self.recovery_reviews))
            review_sequence=self.log[-1]['event_sequence']
            self.recovery_reviews.append(dict(snapshot=snapshot,decision=deepcopy(result),event_sequence=review_sequence))
            hid=result['hospital']
            if hid is None:return super().send_asks(p,t,exclude=exclude)
            before=set(p.tried);episode=self._episode_ids.get(p.id,0)
            old_retry=p.retry_at
            self.ev(t,'recovery_reopen',patient=p.id,hospital=hid,token_id=result['token_id'],
                    review_event_sequence=review_sequence)
            reopen_sequence=self.log[-1]['event_sequence']
            p.tried.discard(hid)
            interrupted=self.recovery_variant=='latched' and p.retry_at is not None and p.retry_at>t
            if interrupted:
                self.ev(t,'recovery_retry_wake',patient=p.id,hospital=hid,token_id=result['token_id'],
                    previous_retry_at=p.retry_at,review_event_sequence=review_sequence)
                p.retry_at=None
            answer=super().send_asks(p,t,exclude=exclude)
            asks=[e for e in self.log if e.get('event_sequence',-1)>reopen_sequence and e['t']==t
                  and e['type']=='ask' and e.get('patient')==p.id]
            target=[e for e in asks if e['hospital']==hid]
            if len(target)>1:raise AssertionError('one recovery token sent more than one actual inquiry')
            restored=False
            if not target and self._episode_ids.get(p.id,0)==episode:
                p.tried.add(hid);restored=True
                if interrupted and not asks and p.retry_at is None:p.retry_at=old_retry
            if target:
                self.recovery_consumptions.append(dict(patient=p.id,hospital=hid,token_id=result['token_id'],
                    condition_fingerprint=snapshot['patient']['condition_fingerprint'],
                    ask_event_sequence=target[0]['event_sequence'],review_event_sequence=review_sequence,
                    reopen_event_sequence=reopen_sequence))
            self.ev(t,'recovery_reopen_result',patient=p.id,hospital=hid,review_event_sequence=review_sequence,
                    reopen_event_sequence=reopen_sequence,actual_target_ask_event_sequence=None if not target else target[0]['event_sequence'],
                    target_restored=restored,token_consumed=bool(target))
            self.recovery_reopens.append(dict(review_event_sequence=review_sequence,reopen_event_sequence=reopen_sequence,
                result_event_sequence=self.log[-1]['event_sequence'],before_tried=sorted(before),after_tried=sorted(p.tried),
                original_retry_at=old_retry,after_retry_at=p.retry_at,retry_interrupted=interrupted,
                original_episode_id=episode,after_episode_id=self._episode_ids.get(p.id,0),
                original_episode_contacts=snapshot['scheduling']['episode_contacts'],
                after_episode_contacts=self._episode_contacts.get(p.id,0),
                original_inquiry_event_sequences=[e['event_sequence'] for e in self.log
                    if e.get('event_sequence',-1)>reopen_sequence and e['t']==t and e['type']=='joint_inquiry_policy'
                    and e.get('patient')==p.id],
                actual_new_ask_event_sequences=[e['event_sequence'] for e in asks],
                actual_target_ask_event_sequence=None if not target else target[0]['event_sequence'],
                target_restored=restored,token_consumed=bool(target)))
            return answer
        finally:self._recovery_sending=False

    def after_tick(self,t):
        super().after_tick(t)
        if self.recovery_variant!='latched' or self._recovery_after_tick:return
        self._recovery_after_tick=True
        try:
            for pid in sorted(self._registered):
                p=self.patients[pid]
                if p.state=='SEARCH' and p.t_commit is None and not p.asks and not p.valid \
                        and pid not in self._dispatch_accept_refs \
                        and any(row['patient']==pid for row in self.recovery_declines):
                    # A parent send_asks call can itself advance an empty-tried
                    # episode, even before returning for an existing retry wait.
                    # The extra recovery scan therefore stays observational
                    # until a real recovery target is eligible on this tick.
                    if self._observed_review_at.get(pid)==t or t<self._next_policy_review.get(pid,0):
                        continue
                    if decide_recovery_reopen(self._recovery_snapshot(p,t,()))['hospital'] is not None:
                        self.send_asks(p,t)
        finally:self._recovery_after_tick=False

    def run_replay(self):
        branch=super().run_replay()
        base_foundation=dict(deepcopy(RETRY_DISPATCH_FOUNDATION_SPEC),
            mode='cooperative_queue' if getattr(self,'cooperative_queue_enabled',False) else 'independent_replies')
        branch.update(recovery_dispatch_enabled=True,recovery_dispatch_variant=self.recovery_variant,
            recovery_dispatch_spec=deepcopy(RECOVERY_DISPATCH_SPEC),
            recovery_dispatch_foundation_spec=dict(deepcopy(RECOVERY_FOUNDATION_SPEC),base_dispatch_foundation_spec=base_foundation),
            recovery_dispatch_declines=deepcopy(self.recovery_declines),
            recovery_dispatch_resource_transitions=deepcopy(self.recovery_transitions),
            recovery_dispatch_reviews=deepcopy(self.recovery_reviews),
            recovery_dispatch_reopens=deepcopy(self.recovery_reopens),
            recovery_dispatch_consumptions=deepcopy(self.recovery_consumptions))
        branch['metrics'].update(recovery_reopen_reviews=len(self.recovery_reviews),
            recovery_reopen_attempts=len(self.recovery_reopens),recovery_reopen_sent=len(self.recovery_consumptions))
        return branch


class IndependentRecoveryDispatchReplay(RecoveryDispatchMixin,IndependentRetryDispatchReplay):pass
class CooperativeRecoveryDispatchReplay(RecoveryDispatchMixin,CooperativeRetryDispatchReplay):pass


def run_recovery_dispatch_branch(scenario,*,allocation='H1',variant='latched',routing=None,branch_id=None,label=None):
    cooperative.v1.validate_joint_scenario(scenario)
    if allocation not in ('H1','C1') or variant not in VARIANTS:
        raise ValueError('known recovery allocation and mode required')
    enabled=scenario.get('inquiry_queue',{}).get('enabled',False)
    if type(enabled) is not bool:raise ValueError('explicit declared queue support required')
    code=branch_id or f'recovery_{allocation}_{variant}'
    rule=cooperative.v1.HeuristicAssignmentRule(time_limit_seconds=.05,max_moves=2048) if allocation=='H1' else \
        cooperative.v1.StableCPSATAssignmentRule(time_limit_seconds=.05,seed=0,require_backend=True)
    policy=cooperative.v1.Policy(rule=rule,k=3,wait=2,notify=True,name=code,observed_inquiry={})
    cls=CooperativeRecoveryDispatchReplay if enabled else IndependentRecoveryDispatchReplay
    arguments=dict(routing=routing,inquiry='v4',recovery=True,pending_protection=True,
        cohort_registration=True,scarce_routing=True,late_reply_recovery=True,late_pending_guard=True,
        shortage_probe=True,recovery_variant=variant)
    if enabled:arguments['cooperative_probe']=True
    replay=cls(deepcopy(scenario),policy,**arguments)
    branch=replay.run_replay()
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
