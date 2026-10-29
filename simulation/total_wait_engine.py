"""Reduce idle waiting through received resource events, without future truth.

Resource-change publications are a new synthetic hospital reporting contract.
Only the environment samples physical capacity/usage; allocation/review receives
delivered, masked delta fields. Separate branches isolate reporting and review.
"""
from __future__ import annotations

from copy import deepcopy
import time

from .h1_online_engine import _policy_spec
from .heuristic_assignment import HeuristicAssignmentRule
from .map_replay import RouteSnapshot, validate_scenario
from .responsive_confirmation_engine import information_reason
from .sim import Policy
from .stable_c1_engine import DEFAULT_STABLE_C1_POLICIES, StableC1Replay
from .stable_cp_sat_assignment import StableCPSATAssignmentRule


EXPERIMENT_ID='total_wait_20261015'
SOURCE_EXPERIMENT='total_wait_comparison'
DEFAULT_TOTAL_WAIT_POLICIES=tuple(dict(deepcopy(s),resource_updates=False,event_review=False)
    for s in DEFAULT_STABLE_C1_POLICIES)+tuple(
    dict(deepcopy(next(s for s in DEFAULT_STABLE_C1_POLICIES if s['id']=='stable_responsive_C1')),
         id=name,label=label,resource_updates=updates,event_review=review)
    for name,label,updates,review in (
        ('review_stable_C1','현재 보고 즉시 재검토',False,True),
        ('updates_stable_C1','자원 변화 보고',True,False),
        ('total_wait_C1','자원 변화 보고 + 즉시 재검토',True,True)))


def update_metrics(branch):
    events=branch['events']
    return dict(resource_update_cycles=sum(e['type']=='resource_update_cycle' for e in events),
        resource_publications=sum(e['type']=='resource_update_captured' for e in events),
        resource_updates_received=sum(e['type']=='resource_report_received' and 'publication_id' in e for e in events),
        event_review_cycles=sum(e['type']=='event_review_cycle' for e in events),
        event_review_requests=sum(e['type']=='resource_event_review' for e in events),
        update_wall_seconds=branch['metrics']['update_wall_seconds'],
        event_review_wall_seconds=branch['metrics']['event_review_wall_seconds'])


class TotalWaitReplay(StableC1Replay):
    def __init__(self,scenario,policy,routing=None,mode='observed',protection=True,
                 prefetch=False,watch=False,grace_minutes=3,stable_ties=False,
                 resource_updates=False,event_review=False):
        if any(type(v) is not bool for v in (resource_updates,event_review)):
            raise ValueError('resource_updates/event_review must be boolean')
        if (resource_updates or event_review) and (mode!='observed' or not stable_ties or not prefetch or not watch):
            raise ValueError('total-wait interventions require stable responsive C1')
        config=deepcopy(scenario.get('resource_update_config',{}))
        if set(config)!={'delay_minutes','missing_resources','silent'}:
            raise ValueError('explicit resource_update_config required')
        if type(config['delay_minutes']) is not int or config['delay_minutes']<0:
            raise ValueError('resource update delay must be a nonnegative integer')
        if type(config['silent']) is not bool or not isinstance(config['missing_resources'],list) \
                or any(not isinstance(r,str) or not r for r in config['missing_resources']):
            raise ValueError('invalid resource update silence/missing mask')
        observation=scenario.get('observation_config',{})
        mask=sorted(set(observation.get('feed_missing_resources',())) |
                    set(observation.get('confirmation_missing_resources',())))
        if config['missing_resources']!=mask or config['silent']!=observation.get('confirmation_silent',False):
            raise ValueError('resource updates must preserve persistent missing fields and hospital silence')
        self.resource_updates_enabled,self.event_review_enabled=resource_updates,event_review
        self.update_config=config
        self._publication_sequence=0;self._publications=[]
        self._registered=set();self._reviewed_packets=set();self._review_phases=set()
        self.update_seconds=self.event_review_seconds=0.0
        super().__init__(scenario,policy,routing=routing,mode=mode,protection=protection,
                         prefetch=prefetch,watch=watch,grace_minutes=grace_minutes,stable_ties=stable_ties)
        # Environment-only present snapshot. No durations or release times are
        # copied into a publication or into the patient allocation view.
        self._previous_physical={hid:self._physical_report(hid) for hid in self.hospitals}

    def ev(self,t,typ,**fields):
        super().ev(t,typ,**fields)
        if typ=='request':self._registered.add(fields['patient'])

    def _sample_updates(self,t,phase):
        if not self.resource_updates_enabled:return
        started=time.perf_counter()
        self.ev(t,'resource_update_cycle',phase=phase)
        for hid in self.hospitals:
            current=self._physical_report(hid)
            previous=self._previous_physical[hid]
            changed={r:value for r,value in current.items() if previous.get(r)!=value}
            self._previous_physical[hid]=deepcopy(current)
            if not changed or self.update_config['silent']:continue
            payload={r:None if r in self.update_config['missing_resources'] else deepcopy(value)
                     for r,value in changed.items()}
            publication=dict(publication_id=self._publication_sequence,hospital=hid,
                resources=payload,observed_at=t,receive_at=t+self.update_config['delay_minutes'])
            self._publication_sequence+=1
            self.ev(t,'resource_update_captured',**deepcopy(publication))
            self._publications.append(publication)
        self.update_seconds+=time.perf_counter()-started

    def _deliver_updates(self,t):
        if not self.resource_updates_enabled:return
        started=time.perf_counter()
        due=sorted((p for p in self._publications if p['receive_at']<=t),
                   key=lambda p:(p['receive_at'],p['publication_id']))
        self._publications=[p for p in self._publications if p['receive_at']>t]
        for packet in due:
            # A feed delta uses the same source-observation clock and monotone
            # store ordering. Its packet ID is allocated upon actual receipt;
            # a publication ID separately links the earlier environment event.
            self._receive_report(packet['hospital'],packet['resources'],packet['observed_at'],t,'feed')
            self.log[-1]['publication_id']=packet['publication_id']
            self.observation_records[-1]['publication_id']=packet['publication_id']
        self.update_seconds+=time.perf_counter()-started

    def _event_review(self,t,phase):
        if not self.event_review_enabled or (t,phase) in self._review_phases:return
        self._review_phases.add((t,phase))
        started=time.perf_counter()
        self.ev(t,'event_review_cycle',phase=phase)
        latest={}
        for report in reversed(self.observation_records):
            if report['t']!=t:break
            if report['applied']:latest.setdefault(report['hospital'],report)
        for p in sorted((self.patients[pid] for pid in self._registered),
                        key=lambda p:(-p.priority,p.t_req,p.id)):
            # An expired offer still in the ledger is processed by the normal
            # expiry flow before the after-treatment review, never resurrected.
            if p.state!='SEARCH' or p.asks or p.valid:continue
            position=self.position(p,t)
            options=[]
            for hid in self.candidates(p,position):
                report=latest.get(hid)
                if hid in self.failed_hospitals[p.id] or report is None:continue
                key=p.id,report['event_sequence']
                if key in self._reviewed_packets:continue
                if information_reason(self._views(hid,p.demand,t),p.demand) is None:
                    options.append(hid)
            if not options:continue
            hid=min(options,key=lambda h:(self.travel(p,h,position),h))
            report=latest[hid]
            self._reviewed_packets.add((p.id,report['event_sequence']))
            self.ev(t,'resource_event_review',patient=p.id,hospital=hid,
                    report_event_sequence=report['event_sequence'],previous_retry_at=p.retry_at)
            p.tried.discard(hid);p.retry_at=None
            self.send_asks(p,t,exclude=set(self.hospitals)-{hid})
        self.event_review_seconds+=time.perf_counter()-started

    def _information_cycle(self,t):
        first=self._information_t!=t
        super()._information_cycle(t)
        if first and self.information_mode=='observed':
            self._sample_updates(t,'pre_queries')
            self._deliver_updates(t)
            self._event_review(t,'pre_queries')

    def after_tick(self,t):
        self._information_cycle(t)
        self._sample_updates(t,'after_treatments')
        self._deliver_updates(t)
        super().after_tick(t)
        self._event_review(t,'after_treatments')

    def run_replay(self):
        branch=super().run_replay()
        branch.update(comparison_id=EXPERIMENT_ID,source_experiment=SOURCE_EXPERIMENT,
            resource_updates_enabled=self.resource_updates_enabled,event_review_enabled=self.event_review_enabled)
        branch['metrics'].update(update_wall_seconds=self.update_seconds,event_review_wall_seconds=self.event_review_seconds)
        branch['metrics'].update(update_metrics(branch))
        return branch


def run_total_wait_replay(scenario,policies=None,execution_order=None,routing=None):
    validate_scenario(scenario)
    specs=deepcopy(list(DEFAULT_TOTAL_WAIT_POLICIES if policies is None else policies));ids=[s['id'] for s in specs]
    if not ids or len(set(ids))!=len(ids):raise ValueError('unique nonempty branch IDs required')
    for s in specs:
        if s.get('mode') not in ('perfect','observed') or s.get('rule') not in ('R1','H1','C1'):
            raise ValueError('unsupported information mode or allocation rule')
        if any(type(s.get(k)) is not bool for k in ('protection','prefetch','watch','stable_ties','resource_updates','event_review')):
            raise ValueError('explicit boolean intervention flags required')
        if s['stable_ties'] and s['rule']!='C1':raise ValueError('tie stability applies to C1 only')
        if (s['protection'] or s['prefetch'] or s['watch']) and (s['mode']!='observed' or s['rule'] not in ('H1','C1')):
            raise ValueError('confirmation intervention requires observed H1/C1')
        if (s['prefetch'] or s['watch']) and not s['protection']:raise ValueError('responsive confirmation requires protection')
        if s.get('grace_minutes')!=(3 if s['protection'] else None):raise ValueError('fixed grace window required')
        if (s['resource_updates'] or s['event_review']) and not(s['stable_ties'] and s['prefetch'] and s['watch']):
            raise ValueError('total-wait interventions require stable responsive C1')
        if s.get('observed_inquiry') is not None or s.get('adaptive_inquiry') is not None:
            raise ValueError('fixed inquiry policy required')
    common=('k','wait','notify','near_minutes','expand_after','inquiry_ranking','load_weight','load_window',
            'priority_order','high_priority_hedge_after')
    if any(any(s.get(k)!=specs[0].get(k) for k in common) for s in specs):raise ValueError('common inquiry settings required')
    order=list(ids if execution_order is None else execution_order)
    if len(order)!=len(ids) or set(order)!=set(ids):raise ValueError('execution order must contain every branch once')
    frozen=deepcopy(scenario);frozen.update(comparison_id=EXPERIMENT_ID,source_experiment=SOURCE_EXPERIMENT)
    frozen.setdefault('assumptions',[]).extend([
        '자원 변화 보고는 새 합성 병원 갱신 계약이다. 현재 실제로 변한 필드만 마스킹·지연 후 전달하며 미래 복구·점유 종료 시각을 전달하지 않는다.',
        '같은 기존 보고의 즉시 재검토와 추가 자원 변화 보고를 별도 분기로 비교한다. 개선 전체를 C1 목적함수 효과로 설명하지 않는다.',
        '보고를 받아도 현재 환자별 새 수용 응답과 최종 충돌 검사가 필요하다. 병상·진료시간·환자 수를 바꾸지 않는다.',
    ])
    routing=routing or RouteSnapshot();routing.used_keys.clear();by={}
    for code in order:
        s=deepcopy(next(s for s in specs if s['id']==code));s.pop('id');label,mode=s.pop('label'),s.pop('mode')
        flags={k:s.pop(k) for k in ('protection','prefetch','watch','grace_minutes','stable_ties','resource_updates','event_review')}
        if s['rule']=='H1':s['rule']=HeuristicAssignmentRule(time_limit_seconds=s.pop('time_limit_seconds',.05),max_moves=s.pop('max_moves',2048))
        elif s['rule']=='C1' and flags['stable_ties']:
            s['rule']=StableCPSATAssignmentRule(time_limit_seconds=s.pop('time_limit_seconds',.05),seed=s.pop('seed',0),require_backend=s.pop('require_backend',True))
        policy=Policy(**s,name=code);simulation=TotalWaitReplay(deepcopy(frozen),policy,routing=routing,mode=mode,**flags)
        b=simulation.run_replay();b.update(id=code,label=label,policy=_policy_spec(policy));by[code]=b
    return dict(schema_version=1,experiment_id=EXPERIMENT_ID,scenario=frozen,branches=[by[i] for i in ids],
        execution_order=order,routing_summary=routing.summary(scenario_only=True))
