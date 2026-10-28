"""Reuse submitted backup work after an early dispatch fails at arrival.

Movement, failure assessment and transfer preparation are unchanged. Retained
replies are environmental deliveries; decision inputs never include due/silent.
"""
from __future__ import annotations

from copy import deepcopy
import time

from .existing_info_engine import DEFAULT_EXISTING_INFO_POLICIES, ExistingInfoReplay
from .h1_online_engine import _policy_spec
from .map_replay import RouteSnapshot, validate_scenario
from .responsive_confirmation_engine import information_reason
from .sim import Policy
from .stable_cp_sat_assignment import StableCPSATAssignmentRule

EXPERIMENT_ID='outage_recovery_20261015'
SOURCE_EXPERIMENT='outage_recovery_comparison'
_BASE=next(p for p in DEFAULT_EXISTING_INFO_POLICIES if p['id']=='existing_info_C1')
DEFAULT_OUTAGE_RECOVERY_POLICIES=tuple(dict(deepcopy(p),retained_backups=False,transfer_same_tick=False,recovery_followup=False)
    for p in DEFAULT_EXISTING_INFO_POLICIES)+tuple(dict(deepcopy(_BASE),id=code,label=label,
        retained_backups=True,transfer_same_tick=same_tick,recovery_followup=same_tick) for code,label,same_tick in (
        ('retained_backup_C1','원래 백업 문의·응답 보존',False),
        ('outage_recovery_C1','백업 보존 + 준비 완료 시 검토',True)))


def backup_metrics(branch):
    events=branch['events']
    return dict(backup_retained_episodes=sum(e['type']=='backup_retained' for e in events),
        backup_retained_queries=sum(len(e['pending']) for e in events if e['type']=='backup_retained'),
        backup_retained_offers=sum(len(e['offers']) for e in events if e['type']=='backup_retained'),
        backup_reply_receipts=sum(e['type']=='backup_reply_received' for e in events),
        backup_transfer_reuses=sum(e['type']=='backup_transfer_reused' for e in events),
        backup_preparation_ready=sum(e['type']=='backup_preparation_ready' for e in events),
        backup_closed_episodes=sum(e['type']=='backup_closed' for e in events),
        backup_report_reopens=sum(e['type']=='backup_failed_reopened' for e in events),
        backup_alert_retries=sum(e['type']=='backup_alert_retry' for e in events),
        backup_wall_seconds=branch['metrics']['backup_wall_seconds'])


class OutageRecoveryReplay(ExistingInfoReplay):
    def __init__(self,*args,retained_backups=False,transfer_same_tick=False,recovery_followup=False,**kwargs):
        if any(type(v) is not bool for v in (retained_backups,transfer_same_tick,recovery_followup)):
            raise ValueError('explicit boolean backup flags required')
        if transfer_same_tick and not retained_backups:raise ValueError('same-tick recovery requires retained backups')
        if retained_backups and not kwargs.get('early_offers',False):raise ValueError('backup retention requires early offers')
        if recovery_followup and not transfer_same_tick:raise ValueError('recovery followup requires same-tick retained recovery')
        self.retained_backups_enabled=retained_backups;self.transfer_same_tick_enabled=transfer_same_tick
        self.recovery_followup_enabled=recovery_followup
        self._early_ready_refs={};self._accept_refs={};self._episodes={};self._gate_events={};self._terminal_refs={}
        self.backup_seconds=0.0
        super().__init__(*args,**kwargs)

    def schedule_ask(self,p,hid,attempt,t):
        ask=super().schedule_ask(p,hid,attempt,t)
        if self.retained_backups_enabled:
            # These fields are known request metadata. The existing at/silent
            # keys remain private to the response-delivery environment.
            ask.update(attempt=attempt,submitted_at=t,timeout_at=t+self.cfg['timeout'],ask_event_sequence=None)
        return ask

    @staticmethod
    def _pending_view(asks):
        return [dict(hospital=a['hid'],**{k:a[k] for k in ('attempt','submitted_at','timeout_at','ask_event_sequence')})
                for a in sorted(asks,key=lambda a:(a['hid'],a['attempt']))]

    def _offers_view(self,p,t):
        return [dict(hospital=h,accepted_at=p.accepted_at[h],expires_at=expiry,
                     accept_event_sequence=self._accept_refs[p.id,h])
                for h,expiry in sorted(p.valid.items()) if expiry>t]

    def _sync(self,p,t):
        if p.id not in self._episodes:return
        episode=self._episodes[p.id]
        episode['pending']=deepcopy(p.asks)
        episode['valid']={h:expiry for h,expiry in p.valid.items() if expiry>t}
        episode['accepted']={h:p.accepted_at[h] for h in episode['valid']}

    def _restore(self,p,t):
        episode=self._episodes[p.id]
        p.asks=deepcopy(episode['pending'])
        p.valid={h:expiry for h,expiry in episode['valid'].items() if expiry>t}
        p.accepted_at.update({h:episode['accepted'][h] for h in p.valid})
        p.first_valid_t=min((p.accepted_at[h] for h in p.valid),default=None)

    def ev(self,t,typ,**fields):
        kind=typ
        super().ev(t,typ,**fields)
        seq=self.log[-1]['event_sequence'];pid=fields.get('patient')
        if not self.retained_backups_enabled:return
        if kind=='ask':
            p=self.patients[pid]
            for ask in p.asks:
                if ask['hid']==fields['hospital'] and ask['attempt']==fields['attempt']:
                    ask['ask_event_sequence']=seq
        elif kind=='accept':self._accept_refs[pid,fields['hospital']]=seq
        elif kind in ('expire','decline','no_response'):
            self._terminal_refs[pid,fields['hospital']]=dict(type=kind,t=t,event_sequence=seq)
        elif kind=='early_offer_ready':self._early_ready_refs[pid,t]=seq
        elif kind=='notify' and self.recovery_followup_enabled and pid in self._episodes:
            p=self.patients[pid];episode=self._episodes[pid]
            open_hospitals={a['hid'] for a in p.asks}
            for hid in sorted(episode['known_backups']):
                terminal=self._terminal_refs.get((pid,hid))
                if hid==p.dest or hid in open_hospitals or hid in p.valid or terminal is None \
                        or terminal['type']!='expire' or terminal['t']>t or (seq,hid) in episode['alert_resets']:
                    continue
                if information_reason(self._views(hid,p.demand,t),p.demand) is not None:continue
                episode['alert_resets'].add((seq,hid));p.tried.discard(hid);p.retry_at=None
                super().ev(t,'backup_alert_retry',patient=pid,hospital=hid,
                    notify_event_sequence=seq,expire_event_sequence=terminal['event_sequence'])
        elif kind=='arrive' and pid in self._episodes:
            p=self.patients[pid];self._restore(p,t)
            super().ev(t,'backup_arrival_preserved',patient=pid,hospital=fields['hospital'],
                arrive_event_sequence=seq,pending=self._pending_view(p.asks),offers=self._offers_view(p,t))
        elif kind=='arrival_unable' and pid in self._episodes:
            self._episodes[pid].update(failure_sequence=seq,failure_t=t,
                failure_hospital=fields['hospital'],failure_reason=fields['reason'])
        elif kind=='transfer_search' and pid in self._episodes:
            p=self.patients[pid];self._restore(p,t)
            p.tried=set(self.failed_hospitals[pid]);p.retry_at=t if not p.asks and not p.valid else None
            super().ev(t,'backup_transfer_reused',patient=pid,hospital=fields['hospital'],
                transfer_event_sequence=seq,arrival_unable_event_sequence=self._episodes[pid]['failure_sequence'],
                pending=self._pending_view(p.asks),offers=self._offers_view(p,t))
            self._sync(p,t)
        elif kind=='treatment_start' and pid in self._episodes:
            self._episodes.pop(pid);p=self.patients[pid];p.asks=[];p.valid={};p.first_valid_t=None
            super().ev(t,'backup_closed',patient=pid,reason='TREATMENT_STARTED',cause_event_sequence=seq)

    def commit(self,p,hid,t,redirect=False):
        can_retain=self.retained_backups_enabled and (p.id,t) in self._early_ready_refs \
            and p.id not in self._episodes and not redirect and p.id not in self.transfer_origins
        pending=[];valid={};accepted={}
        if can_retain:
            pending=deepcopy([a for a in p.asks if a['hid']!=hid and a['timeout_at']>=t])
            for ask in pending:
                # Enforce the original sender deadline for retained work only.
                # The underlying hospital work calendar is never cancelled.
                if ask['at']>ask['timeout_at']:ask['at'],ask['silent']=ask['timeout_at'],True
            valid={h:expiry for h,expiry in p.valid.items() if h!=hid and expiry>t}
            accepted={h:p.accepted_at[h] for h in valid}
            can_retain=bool(pending or valid)
        closing=p.id in self._episodes
        super().commit(p,hid,t,redirect=redirect)
        commit_sequence=self.log[-1]['event_sequence']
        if can_retain:
            self._episodes[p.id]=dict(pending=pending,valid=valid,accepted=accepted,failure_sequence=None,
                known_backups={a['hid'] for a in pending}|set(valid),alert_resets=set())
            self._restore(p,t)
            self._gate_events[p.id]=dict(type='backup_retained',patient=p.id,hospital=hid,
                commit_event_sequence=commit_sequence,early_ready_event_sequence=self._early_ready_refs[p.id,t],
                pending=self._pending_view(p.asks),offers=self._offers_view(p,t))
        elif closing:
            self._episodes.pop(p.id)
            self._gate_events[p.id]=dict(type='backup_closed',patient=p.id,reason='COMMITTED',cause_event_sequence=commit_sequence)

    def _gate(self,p,hid,t,redirect=False):
        record=super()._gate(p,hid,t,redirect=redirect)
        # The frozen gate captures its commit sequence before these markers.
        marker=self._gate_events.pop(p.id,None)
        if marker is not None:
            kind=marker.pop('type');self.ev(t,kind,**marker)
        return record

    def _receive_only(self,p,t,phase):
        due=[a for a in p.asks if a['at']==t]
        p.asks=[a for a in p.asks if a['at']!=t]
        for ask in due:
            hid=ask['hid']
            outcome='no_response' if ask['silent'] else 'accept' if self.hospitals[hid].can_accept(p) else 'decline'
            self.ev(t,'backup_reply_received',patient=p.id,hospital=hid,attempt=ask['attempt'],
                    ask_event_sequence=ask['ask_event_sequence'],outcome=outcome,phase=phase)
            if outcome=='accept':
                p.valid[hid]=t+self.cfg['accept_ttl'];p.accepted_at[hid]=t
            self.ev(t,outcome,patient=p.id,hospital=hid)
        for hid in [h for h,expiry in p.valid.items() if expiry<=t]:
            del p.valid[hid];self.ev(t,'expire',patient=p.id,hospital=hid)
        p.first_valid_t=min((p.accepted_at[h] for h in p.valid),default=None)
        self._sync(p,t)

    def resolve_asks(self,p,t):
        if p.id in self._episodes and p.state=='TRANSIT' and not p.notified:
            started=time.perf_counter();self._information_cycle(t)
            self._receive_only(p,t,'transit');self.backup_seconds+=time.perf_counter()-started
            return
        super().resolve_asks(p,t)
        self._sync(p,t)

    def before_tick(self,t):
        super().before_tick(t)
        if not self.transfer_same_tick_enabled:return
        started=time.perf_counter()
        for pid in sorted(self._episodes):
            p=self.patients[pid]
            if p.state!='TRANSFER_WAIT' or self.transfer_at[pid]>t:continue
            hid=p.dest;p.state,p.loc='SEARCH',self.hospitals[hid].loc
            self.ev(t,'backup_preparation_ready',patient=pid,hospital=hid,
                arrival_unable_event_sequence=self._episodes[pid]['failure_sequence'],
                preparation_minutes=self.scenario.get('transfer_preparation_minutes',3),ready_at=t)
            # No information/query decision runs before current physical events.
            self.ev(t,'transfer_search',patient=pid,hospital=hid)
        self.backup_seconds+=time.perf_counter()-started

    def _event_review(self,t,phase):
        if self.recovery_followup_enabled:
            started=time.perf_counter();latest={}
            for report in reversed(self.observation_records):
                if report['t']!=t:break
                if report['applied']:latest.setdefault(report['hospital'],report)
            ready_ids={p.id for p,_ in self.ready}
            for pid in sorted(self._episodes,key=lambda pid:(-self.patients[pid].priority,self.patients[pid].t_req,pid)):
                p=self.patients[pid];episode=self._episodes[pid]
                hid=episode.get('failure_hospital');report=latest.get(hid)
                if p.state!='SEARCH' or pid in ready_ids or episode.get('failure_reason')!='resource_capacity' \
                        or hid not in self.failed_hospitals[pid] or report is None \
                        or report['observed_at']<episode['failure_t'] or hid in p.valid \
                        or any(a['hid']==hid for a in p.asks):continue
                views=self._views(hid,p.demand,t)
                if information_reason(views,p.demand) is not None or any(
                        v['observed_at'] is None or v['observed_at']<episode['failure_t'] for v in views.values()):continue
                self.failed_hospitals[pid].discard(hid);p.tried.discard(hid);p.retry_at=None
                self.ev(t,'backup_failed_reopened',patient=pid,hospital=hid,
                    arrival_unable_event_sequence=episode['failure_sequence'],report_event_sequence=report['event_sequence'])
                self.send_asks(p,t,exclude=set(self.hospitals)-{hid})
            self.backup_seconds+=time.perf_counter()-started
        return super()._event_review(t,phase)

    def after_tick(self,t):
        if self.retained_backups_enabled:
            started=time.perf_counter()
            for pid in sorted(self._episodes):
                p=self.patients[pid]
                if p.state=='TRANSFER_WAIT':self._receive_only(p,t,'preparation')
            self.backup_seconds+=time.perf_counter()-started
        super().after_tick(t)

    def run_replay(self):
        branch=super().run_replay()
        branch.update(comparison_id=EXPERIMENT_ID,source_experiment=SOURCE_EXPERIMENT,
            retained_backups_enabled=self.retained_backups_enabled,transfer_same_tick_enabled=self.transfer_same_tick_enabled,
            recovery_followup_enabled=self.recovery_followup_enabled)
        branch['metrics']['backup_wall_seconds']=self.backup_seconds
        branch['metrics'].update(backup_metrics(branch))
        return branch


def run_outage_recovery_replay(scenario,policies=None,execution_order=None,routing=None):
    validate_scenario(scenario)
    specs=deepcopy(list(DEFAULT_OUTAGE_RECOVERY_POLICIES if policies is None else policies))
    ids=[s['id'] for s in specs];canonical={s['id']:s for s in DEFAULT_OUTAGE_RECOVERY_POLICIES}
    if not ids or len(set(ids))!=len(ids):raise ValueError('unique nonempty policy IDs required')
    for spec in specs:
        if any(type(spec.get(k)) is not bool for k in ('protection','prefetch','watch','stable_ties',
                'resource_updates','event_review','early_offers','retained_backups','transfer_same_tick','recovery_followup')):
            raise ValueError('explicit boolean flags required')
        if spec!=canonical.get(spec['id']):raise ValueError('fixed outage-recovery policies required')
    order=list(ids if execution_order is None else execution_order)
    if len(order)!=len(ids) or set(order)!=set(ids):raise ValueError('every branch must execute once')
    frozen=deepcopy(scenario);frozen.update(comparison_id=EXPERIMENT_ID,source_experiment=SOURCE_EXPERIMENT)
    frozen.setdefault('assumptions',[]).extend([
        '추가 자원 보고 없이 조기 출발 전에 제출한 환자별 백업 문의와 아직 유효한 수용을 보존한다.',
        '정상 이송·전원 준비 중에는 회신만 받는다. 백업 수용이 자동 방향 변경이나 자원 수량 갱신을 뜻하지 않는다.',
        '실패 도착·전원 준비시간·이동시간은 기존과 같다. 준비 완료 시 기존 보호·공동 배정·충돌 검사를 거쳐 재출발한다.',
        '원래 문의 기한과 원래 수용 만료시각을 유지하며 미래 회신시각·병상 복구시각을 정책에 넘기지 않는다.',
    ])
    routing=routing or RouteSnapshot();routing.used_keys.clear();by={}
    for code in order:
        s=deepcopy(canonical[code]);s.pop('id');label=s.pop('label');mode=s.pop('mode')
        flags={k:s.pop(k) for k in ('protection','prefetch','watch','grace_minutes','stable_ties',
            'resource_updates','event_review','early_offers','retained_backups','transfer_same_tick','recovery_followup')}
        s['rule']=StableCPSATAssignmentRule(time_limit_seconds=s.pop('time_limit_seconds'),seed=s.pop('seed'),require_backend=s.pop('require_backend'))
        policy=Policy(**s,name=code)
        branch=OutageRecoveryReplay(deepcopy(frozen),policy,routing=routing,mode=mode,**flags).run_replay()
        branch.update(id=code,label=label,policy=_policy_spec(policy));by[code]=branch
    return dict(schema_version=1,experiment_id=EXPERIMENT_ID,scenario=frozen,branches=[by[i] for i in ids],
                execution_order=order,routing_summary=routing.summary(scenario_only=True))
