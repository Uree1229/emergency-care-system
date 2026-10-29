"""Remove dominated reply-wait using already delivered evidence only.

No resource-change publications or new resource-query mechanism are added.
Early readiness is a current-information decision, not a future-outage guarantee.
"""
from __future__ import annotations

from copy import deepcopy
import time

from .h1_online_engine import _policy_spec
from .map_replay import RouteSnapshot, validate_scenario
from .responsive_confirmation_engine import information_reason
from .sim import Policy
from .stable_cp_sat_assignment import StableCPSATAssignmentRule
from .total_wait_engine import DEFAULT_TOTAL_WAIT_POLICIES, TotalWaitReplay

EXPERIMENT_ID='existing_info_20261015'
SOURCE_EXPERIMENT='existing_info_comparison'
_BASE=next(s for s in DEFAULT_TOTAL_WAIT_POLICIES if s['id']=='stable_responsive_C1')
DEFAULT_EXISTING_INFO_POLICIES=tuple(dict(deepcopy(_BASE),id=name,label=label,
    resource_updates=False,event_review=review,early_offers=early)
    for name,label,review,early in (
        ('stable_responsive_C1','기존 안정화 C1',False,False),
        ('review_stable_C1','기존 보고 즉시 재검토',True,False),
        ('early_stable_C1','우월 수용 응답 조기 검토',False,True),
        ('existing_info_C1','기존 정보 재검토 + 우월 수용 응답',True,True)))


def decide_early_offer(snapshot):
    """Detached decision: no pending due time, silence or physical future state."""
    patient=snapshot['patient'];demand=patient['demand'];t=snapshot['t']
    offers=[o for o in snapshot['valid_offers'] if o['expires_at']>t and
            information_reason(o['resources'],demand) is None]
    if not offers:return dict(eligible=False,hospital=None,reason='NO_FRESH_OFFER')
    offer=min(offers,key=lambda o:(o['travel_minutes'],o['hospital']))
    best=(offer['travel_minutes'],offer['hospital'])
    if any((c['travel_minutes'],c['hospital'])<best for c in snapshot['structural_candidates']):
        return dict(eligible=False,hospital=None,reason='NOT_DOMINANT')
    contenders=[p for p in snapshot['search_patients'] if offer['hospital'] in p['compatible_hospitals']]
    if any(offer['resources'][r]['free']<sum(p['demand'].get(r,0) for p in contenders) for r in demand):
        return dict(eligible=False,hospital=None,reason='SHARED_CAPACITY')
    return dict(eligible=True,hospital=offer['hospital'],reason='EARLY_READY')


def early_offer_metrics(branch):
    return dict(early_offer_cycles=sum(e['type']=='early_offer_cycle' for e in branch['events']),
        early_offer_reviews=len(branch['early_offer_records']),
        early_offer_ready=sum(r['result']['eligible'] for r in branch['early_offer_records']),
        early_offer_wall_seconds=branch['metrics']['early_offer_wall_seconds'])


class ExistingInfoReplay(TotalWaitReplay):
    def __init__(self,*args,early_offers=False,**kwargs):
        if type(early_offers) is not bool:raise ValueError('early_offers must be boolean')
        if kwargs.get('resource_updates',False) is not False:raise ValueError('additional resource updates forbidden')
        self.early_offers_enabled=early_offers
        self.early_offer_records=[];self.early_offer_seconds=0.0
        super().__init__(*args,**kwargs)
        if early_offers and (self.information_mode!='observed' or not self.stable_ties_enabled
                             or not self.prefetch_enabled or not self.watch_enabled):
            raise ValueError('early offers require observed stable responsive C1')

    def _early_snapshot(self,p,t):
        offers=[dict(hospital=hid,accepted_at=p.accepted_at[hid],expires_at=expiry,
            resources=deepcopy(self._views(hid,p.demand,t)),travel_minutes=self.travel(p,hid,self.position(p,t)))
            for hid,expiry in sorted(p.valid.items()) if expiry>t]
        current=[self.patients[pid] for pid in self._registered if self.patients[pid].state=='SEARCH']
        return dict(t=t,patient=dict(id=p.id,priority=p.priority,request_t=p.t_req,
                    demand=dict(p.demand),required_capabilities=sorted(p.caps)),
            first_valid_t=p.first_valid_t,pending_hospitals=sorted(a['hid'] for a in p.asks),
            valid_offers=offers,
            structural_candidates=[dict(hospital=hid,travel_minutes=self.travel(p,hid,self.position(p,t)))
                                   for hid in sorted(self.candidates(p,self.position(p,t)))],
            search_patients=[dict(id=q.id,priority=q.priority,request_t=q.t_req,demand=dict(q.demand),
                required_capabilities=sorted(q.caps),compatible_hospitals=sorted(hid for hid,h in self.hospitals.items() if q.caps<=h.caps))
                for q in sorted(current,key=lambda q:q.id)])

    def resolve_batch(self,t):
        if self.early_offers_enabled:
            started=time.perf_counter();self.ev(t,'early_offer_cycle')
            ready_ids={p.id for p,_ in self.ready}
            for p in sorted((self.patients[pid] for pid in self._registered),key=lambda p:(-p.priority,p.t_req,p.id)):
                if p.state!='SEARCH' or p.id in ready_ids or not p.asks or not p.valid \
                        or p.first_valid_t is None or t-p.first_valid_t>=self.policy.wait:continue
                snapshot=self._early_snapshot(p,t);decision_started=time.perf_counter()
                result=decide_early_offer(snapshot)
                elapsed=time.perf_counter()-decision_started
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
                        if not eligible or reason is not None:raise AssertionError('early offer view changed')
                        options.append(hid)
                self.ready.append((p,options));ready_ids.add(p.id)
            self.early_offer_seconds+=time.perf_counter()-started
        return super().resolve_batch(t)

    def run_replay(self):
        branch=super().run_replay()
        branch.update(comparison_id=EXPERIMENT_ID,source_experiment=SOURCE_EXPERIMENT,
            early_offers_enabled=self.early_offers_enabled,early_offer_records=deepcopy(self.early_offer_records))
        branch['metrics']['early_offer_wall_seconds']=self.early_offer_seconds
        branch['metrics'].update(early_offer_metrics(branch))
        return branch


def run_existing_info_replay(scenario,policies=None,execution_order=None,routing=None):
    validate_scenario(scenario)
    specs=deepcopy(list(DEFAULT_EXISTING_INFO_POLICIES if policies is None else policies))
    ids=[s['id'] for s in specs];canonical={s['id']:s for s in DEFAULT_EXISTING_INFO_POLICIES}
    if not ids or len(set(ids))!=len(ids):raise ValueError('unique nonempty policy IDs required')
    for spec in specs:
        if any(type(spec.get(k)) is not bool for k in ('protection','prefetch','watch','stable_ties',
                                                       'resource_updates','event_review','early_offers')):
            raise ValueError('explicit boolean intervention flags required')
        if spec!=canonical.get(spec['id']):raise ValueError('fixed existing-information policies required')
    order=list(ids if execution_order is None else execution_order)
    if len(order)!=len(ids) or set(order)!=set(ids):raise ValueError('every branch must execute once')
    frozen=deepcopy(scenario);frozen.update(comparison_id=EXPERIMENT_ID,source_experiment=SOURCE_EXPERIMENT)
    frozen.setdefault('assumptions',[]).extend([
        '기존 initial/feed/confirmation 정보 계약만 사용하며 추가 자원 변화 보고를 전 분기에서 끈다.',
        '조기 검토는 현재 유효한 수용 응답·신선한 수량·정적 후보 이동시간·현재 등록 탐색 환자만 사용한다.',
        '미래 회신·발생·자원 중단을 알지 못한다. 조기 출발 이후 돌발 상황의 악화 가능성도 평가한다.',
        '환자별 수용 응답·기회 보호·공동 배정·최종 실제 자원 충돌 검사는 유지한다.',
    ])
    routing=routing or RouteSnapshot();routing.used_keys.clear();by={}
    for code in order:
        s=deepcopy(canonical[code]);s.pop('id');label=s.pop('label');mode=s.pop('mode')
        flags={k:s.pop(k) for k in ('protection','prefetch','watch','grace_minutes','stable_ties',
                                  'resource_updates','event_review','early_offers')}
        s['rule']=StableCPSATAssignmentRule(time_limit_seconds=s.pop('time_limit_seconds'),
            seed=s.pop('seed'),require_backend=s.pop('require_backend'))
        policy=Policy(**s,name=code)
        branch=ExistingInfoReplay(deepcopy(frozen),policy,routing=routing,mode=mode,**flags).run_replay()
        branch.update(id=code,label=label,policy=_policy_spec(policy));by[code]=branch
    return dict(schema_version=1,experiment_id=EXPERIMENT_ID,scenario=frozen,branches=[by[i] for i in ids],
                execution_order=order,routing_summary=routing.summary(scenario_only=True))
