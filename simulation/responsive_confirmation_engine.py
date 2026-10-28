"""Parallel confirmation and bounded resource follow-up using received evidence.

The frozen baseline experiments remain unchanged. Hospital acceptance is still
required after any wake and unknown quantities never enter an allocation model.
"""
from __future__ import annotations

from copy import deepcopy
import time

from .h1_online_engine import DEFAULT_H1_POLICIES, _policy_spec
from .heuristic_assignment import HeuristicAssignmentRule
from .map_replay import RouteSnapshot, validate_scenario
from .opportunity_engine import OpportunityProtectionReplay, DEFAULT_OPPORTUNITY_POLICIES
from .opportunity_guard import decide_protection
from .partial_information_engine import PartialInformationReplay
from .sim import Policy


EXPERIMENT_ID = 'responsive_confirmation_20261015'
SOURCE_EXPERIMENT = 'responsive_confirmation_comparison'
DEFAULT_RESPONSIVE_POLICIES = tuple(dict(deepcopy(s), prefetch=False, watch=False,
    grace_minutes=3 if s['protection'] else None) for s in DEFAULT_OPPORTUNITY_POLICIES) + tuple(
    dict(deepcopy(s), id=mode+'_'+s['id'], label=mode+' · '+s['label'], mode='observed',
         protection=True, prefetch=mode in ('early','responsive'), watch=mode in ('watch','responsive'),
         grace_minutes=3)
    for mode in ('early','watch','responsive') for s in DEFAULT_H1_POLICIES if s['id'] in ('H1','C1'))


def information_reason(views, demand):
    if any(v['status']=='fresh' and v['free'] < demand[r] for r,v in views.items()):
        return 'FRESH_SHORTAGE'
    if any(v['status']=='unknown' for v in views.values()):
        return 'UNKNOWN'
    if any(v['status']=='stale' for v in views.values()):
        return 'STALE'
    return None


def incomplete_confirmation(offer, first):
    return any(v['status']=='unknown' and v['source']=='confirmation'
               and v['observed_at'] is not None and v['observed_at'] >= first
               for v in offer['resources'].values())


class ResponsiveConfirmationReplay(OpportunityProtectionReplay):
    def __init__(self, scenario, policy, routing=None, mode='observed', protection=True,
                 prefetch=False, watch=False, grace_minutes=3):
        if any(type(v) is not bool for v in (prefetch,watch)):
            raise ValueError('prefetch/watch must be boolean')
        if (prefetch or watch) and (not protection or mode!='observed' or policy.rule.code not in ('H1','C1')):
            raise ValueError('responsive confirmation requires protected observed H1/C1')
        if protection and (type(grace_minutes) is not int or grace_minutes != 3):
            raise ValueError('protected grace must be three minutes')
        if not protection and grace_minutes is not None:
            raise ValueError('unprotected branch must not have a grace window')
        self.prefetch_enabled, self.watch_enabled = prefetch, watch
        self.grace_minutes = grace_minutes
        self.watched_hospitals, self._woken_reports = set(), set()
        self.confirmation_intervention_seconds = 0.0
        self._watch_timing_active = False
        super().__init__(scenario,policy,routing=routing,mode=mode,protection=protection)

    def _request_resources(self,p,hid,t,reason):
        before=self._request_sequence
        super()._request_resources(p,hid,t,reason)
        if self._request_sequence>before:
            self.watched_hospitals.add(hid)

    def rank_inquiry_candidates(self,p,loc,candidates,t):
        allowed=super().rank_inquiry_candidates(p,loc,candidates,t)
        if not self.prefetch_enabled:
            return allowed
        started=time.perf_counter()
        measure=not self._watch_timing_active
        for hid in allowed:
            views=self._views(hid,p.demand,t)
            reason=information_reason(views,p.demand)
            if reason not in ('UNKNOWN','STALE'):
                continue
            check=next(e for e in reversed(self.log) if e['type']=='information_check'
                and e['t']==t and e['patient']==p.id and e['hospital']==hid and e['purpose']=='inquiry')
            self.ev(t,'resource_prefetch',patient=p.id,hospital=hid,reason=reason,
                    inquiry_check_sequence=check['event_sequence'])
            eligible,actual,_=self._check(p,hid,t,'allocation')
            if eligible or actual!=reason:
                raise AssertionError('prefetch requires current unconfirmed resource quantities')
            self._request_resources(p,hid,t,reason)
        if measure:
            self.confirmation_intervention_seconds+=time.perf_counter()-started
        return allowed

    def resolve_batch(self,t):
        if not self.protection_enabled:
            return PartialInformationReplay.resolve_batch(self,t)
        if not self.prefetch_enabled:
            return super().resolve_batch(t)
        if not self.ready:
            return
        started=time.perf_counter()
        snapshot=self._protection_snapshot(t)
        first,held=deepcopy(self.waiting_since),deepcopy(self.held_since)
        filtered=deepcopy(snapshot)
        for waiter in filtered['waiting']:
            waiter['offers']=[offer for offer in waiter['offers']
                if not incomplete_confirmation(offer,first.get(waiter['id'],t))]
        result=decide_protection(filtered,first,held,grace_minutes=self.grace_minutes)
        self.held_since=deepcopy(result['held_since_after'])
        self.ev(t,'opportunity_guard',record_index=len(self.protection_records))
        self.protection_records.append(dict(snapshot=snapshot,waiting_since=first,
            held_since=held,result=deepcopy(result),grace_minutes=self.grace_minutes,
            event_sequence=self.log[-1]['event_sequence']))
        for hold in result['holds']:
            self.ev(t,'opportunity_hold',**hold)
        keep=set(result['ready_after'])
        self.ready=[(p,options) for p,options in self.ready if p.id in keep]
        self.protection_seconds+=time.perf_counter()-started
        PartialInformationReplay.resolve_batch(self,t)

    def _scarce_search_patients(self,t):
        rows=[]
        for p in self.patients.values():
            if p.state!='SEARCH' or p.t_req>t:
                continue
            compatible=[h for h,hospital in self.hospitals.items() if p.caps<=hospital.caps]
            if len(compatible)==1 and compatible[0] in self.watched_hospitals:
                rows.append((p,compatible[0]))
        return sorted(rows,key=lambda row:(-row[0].priority,row[0].t_req,row[0].id))

    def after_tick(self,t):
        super().after_tick(t)
        if not self.watch_enabled:
            return
        started=time.perf_counter()
        self._watch_timing_active=True
        self.ev(t,'resource_watch_cycle',watched_hospitals=sorted(self.watched_hospitals))
        for p,hid in self._scarce_search_patients(t):
            if hid in self._confirmation_pending or self._next_confirmation.get(hid,0)>t:
                continue
            reason=information_reason(self._views(hid,p.demand,t),p.demand)
            if reason is None:
                continue
            self.ev(t,'resource_watch_check',patient=p.id,hospital=hid,reason=reason)
            eligible,actual,_=self._check(p,hid,t,'allocation')
            if eligible or actual!=reason:
                raise AssertionError('watch requires an actually blocked resource view')
            self._request_resources(p,hid,t,reason)
        reports={}
        for report in reversed(self.observation_records):
            if report['t']!=t:
                break
            if report['applied']:
                reports.setdefault(report['hospital'],report)
        for p,hid in self._scarce_search_patients(t):
            report=reports.get(hid)
            if report is None or p.asks or any(expiry>t for expiry in p.valid.values()):
                continue
            if information_reason(self._views(hid,p.demand,t),p.demand) is not None:
                continue
            key=p.id,report['event_sequence']
            if key in self._woken_reports:
                continue
            self._woken_reports.add(key)
            self.ev(t,'report_driven_wake',patient=p.id,hospital=hid,
                report_event_sequence=report['event_sequence'],previous_retry_at=p.retry_at)
            p.tried.discard(hid)
            p.retry_at=None
            self.send_asks(p,t)
        self.confirmation_intervention_seconds+=time.perf_counter()-started
        self._watch_timing_active=False

    def run_replay(self):
        branch=super().run_replay()
        branch.update(comparison_id=EXPERIMENT_ID,source_experiment=SOURCE_EXPERIMENT,
            prefetch_enabled=self.prefetch_enabled,watch_enabled=self.watch_enabled,
            grace_minutes=self.grace_minutes)
        branch['metrics'].update(confirmation_intervention_wall_seconds=self.confirmation_intervention_seconds,
            prefetch_checks=sum(e['type']=='resource_prefetch' for e in self.log),
            watch_checks=sum(e['type']=='resource_watch_check' for e in self.log),
            watch_cycles=sum(e['type']=='resource_watch_cycle' for e in self.log),
            report_driven_wakes=sum(e['type']=='report_driven_wake' for e in self.log))
        return branch


def run_responsive_confirmation_replay(scenario,policies=None,execution_order=None,routing=None):
    validate_scenario(scenario)
    specs=deepcopy(list(DEFAULT_RESPONSIVE_POLICIES if policies is None else policies))
    ids=[s['id'] for s in specs]
    if not ids or len(set(ids))!=len(ids):raise ValueError('unique nonempty branch IDs required')
    for s in specs:
        if s.get('mode') not in ('perfect','observed') or s.get('rule') not in ('R1','H1','C1'):
            raise ValueError('unsupported information mode or allocation rule')
        if any(type(s.get(k)) is not bool for k in ('protection','prefetch','watch')):
            raise ValueError('explicit boolean protection/prefetch/watch required')
        if (s['protection'] or s['prefetch'] or s['watch']) and (s['mode']!='observed' or s['rule'] not in ('H1','C1')):
            raise ValueError('intervention requires observed H1/C1')
        if (s['prefetch'] or s['watch']) and not s['protection']:
            raise ValueError('responsive confirmation requires protection')
        if s.get('observed_inquiry') is not None or s.get('adaptive_inquiry') is not None:
            raise ValueError('fixed inquiry policy required')
        expected=3 if s['protection'] else None
        if s.get('grace_minutes')!=expected:raise ValueError('grace window differs from fixed intervention')
    common=('k','wait','notify','near_minutes','expand_after','inquiry_ranking','load_weight','load_window',
            'priority_order','high_priority_hedge_after')
    if any(any(s.get(k)!=specs[0].get(k) for k in common) for s in specs):
        raise ValueError('all branches must share inquiry and notification settings')
    order=list(ids if execution_order is None else execution_order)
    if len(order)!=len(ids) or set(order)!=set(ids):raise ValueError('execution_order must contain every branch exactly once')
    frozen=deepcopy(scenario)
    frozen.update(comparison_id=EXPERIMENT_ID,source_experiment=SOURCE_EXPERIMENT)
    frozen.setdefault('assumptions',[]).extend([
        '개선 분기는 병원 문의와 누락·노후 자원 확인을 병행하며, 확인 전 수용 확정은 하지 않는다.',
        '별도 자원 추적은 이미 확인 요청한 병원에 대한 현재 희소 환자 수요만 보고 기존 재요청 간격·중복 금지를 지킨다.',
        '유효 수용이 없으면 최신 자원 보고가 도착해도 새 병원 문의와 수용 응답을 받은 후 확정한다.',
    ])
    routing=routing or RouteSnapshot();routing.used_keys.clear()
    by_id={}
    for branch_id in order:
        s=deepcopy(next(s for s in specs if s['id']==branch_id));s.pop('id')
        label,mode=s.pop('label'),s.pop('mode')
        flags={key:s.pop(key) for key in ('protection','prefetch','watch','grace_minutes')}
        if s['rule']=='H1':s['rule']=HeuristicAssignmentRule(time_limit_seconds=s.pop('time_limit_seconds',.05),max_moves=s.pop('max_moves',2048))
        policy=Policy(**s,name=branch_id)
        simulation=ResponsiveConfirmationReplay(deepcopy(frozen),policy,routing=routing,mode=mode,**flags)
        branch=simulation.run_replay();branch.update(id=branch_id,label=label,policy=_policy_spec(policy))
        by_id[branch_id]=branch
    return dict(schema_version=1,experiment_id=EXPERIMENT_ID,scenario=frozen,
        branches=[by_id[i] for i in ids],execution_order=order,routing_summary=routing.summary(scenario_only=True))
