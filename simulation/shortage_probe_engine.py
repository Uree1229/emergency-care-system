"""One current-information inquiry to a nearby hospital reporting full beds.

This experiment adds explicit inquiry eligibility and optional-work readiness.
A probe is real FIFO work, and an eventual offer still requires an actual reply
and the unchanged fresh-resource allocation gate. Waiting only for a probe does
not prevent an already usable offer entering that gate. No future release or
response calendar is read.
"""
from __future__ import annotations

from copy import deepcopy
import math
import time

from . import deadline_safe_engine as previous

EXPERIMENT_ID = 'shortage_probe_20261017'
SHORTAGE_PROBE_SPEC = dict(maximum_probes_per_patient=1, phase='before_joint_batch',
    initial_episode_only=True, never_committed_only=True, minimum_contact_age_minutes=.5,
    maximum_extra_travel_minutes=2, maximum_open_inquiries=3, maximum_episode_contacts=10,
    reported_bed_shortage_only=True, all_required_resources_fresh=True,
    other_required_resources_sufficient=True, reported_bed_capacity_sufficient=True,
    protect_other_search_pending_and_offers=True, original_selector_unchanged=True,
    actual_fifo_inquiry_required=True, actual_reply_required=True,
    optional_probe_never_blocks_an_existing_usable_offer=True,
    fresh_allocation_gate_unchanged=True, extra_resource_change_publications=False,
    future_release_or_review_schedule_visible=False)

HANDLE_KEYS = {'hospital','attempt','submitted_at','timeout_at','ask_event_sequence'}
OFFER_KEYS = {'hospital','accepted_at','expires_at','accept_event_sequence'}
VIEW_KEYS = {'capacity','used','committed','queued','free','observed_at','received_at',
             'sequence','source','status','age_minutes'}
PATIENT_KEYS = {'id','state','priority','request_t','request_event_sequence','inquiry_episode',
    'episode_contacts','inquiry_count','first_commit_t','first_ask','tried_hospitals',
    'pending_inquiries','valid_offers','demand','required_capabilities','last_contact_at'}
OTHER_KEYS = {'id','state','request_t','request_event_sequence','pending_inquiries','valid_offers'}


def _number(value):
    return type(value) in (int,float) and math.isfinite(value)


def _id(value):
    return isinstance(value,str) and bool(value)


def _unique_ids(value):
    return isinstance(value,list) and all(_id(v) for v in value) and len(value)==len(set(value))


def _handle(row,t):
    if not isinstance(row,dict) or set(row)!=HANDLE_KEYS or not _id(row['hospital']) \
            or type(row['attempt']) is not int or row['attempt']<1 \
            or not previous.v1._quarter(row['submitted_at']) or row['submitted_at']>t \
            or not previous.v1._quarter(row['timeout_at']) or row['timeout_at']<row['submitted_at'] \
            or type(row['ask_event_sequence']) is not int or row['ask_event_sequence']<0:
        raise ValueError('exact original public inquiry handle required')


def _handles(rows,t):
    if not isinstance(rows,list):raise ValueError('public handles must be a list')
    for row in rows:_handle(row,t)
    if len(rows)!=len({(r['hospital'],r['attempt']) for r in rows}):
        raise ValueError('public inquiry identities must be unique')


def _offers(rows,t):
    if not isinstance(rows,list):raise ValueError('current offers must be a list')
    for row in rows:
        if not isinstance(row,dict) or set(row)!=OFFER_KEYS or not _id(row['hospital']) \
                or not previous.v1._quarter(row['accepted_at']) or row['accepted_at']>t \
                or not previous.v1._quarter(row['expires_at']) or row['expires_at']<row['accepted_at'] \
                or type(row['accept_event_sequence']) is not int or row['accept_event_sequence']<0:
            raise ValueError('original accepted-offer metadata required')
    if len(rows)!=len({r['hospital'] for r in rows}):raise ValueError('offers must be unique')


def _view(row,t):
    if not isinstance(row,dict) or set(row)!=VIEW_KEYS or row['status'] not in ('fresh','stale','unknown'):
        raise ValueError('exact current public resource view required')
    if any(not _number(row[k]) or row[k]<0 for k in ('committed','queued')):
        raise ValueError('nonnegative known local demand required')
    for key in ('capacity','used'):
        if row[key] is not None and (not _number(row[key]) or row[key]<0):
            raise ValueError('known capacity and used must be nonnegative')
    known=row['capacity'] is not None and row['used'] is not None
    if known:
        if not _number(row['free']) or row['free']!=row['capacity']-row['used']-row['committed']-row['queued']:
            raise ValueError('received resource arithmetic must be preserved')
    elif row['free'] is not None:raise ValueError('unknown resource free must remain unknown')
    if row['observed_at'] is None:
        if any(row[k] is not None for k in ('received_at','sequence','source','age_minutes')) or row['status']!='unknown':
            raise ValueError('absent observation must remain unknown')
    else:
        if not _number(row['observed_at']) or not _number(row['received_at']) \
                or row['observed_at']>row['received_at'] or row['received_at']>t \
                or row['age_minutes']!=t-row['observed_at'] \
                or type(row['sequence']) is not int or row['sequence']<0 or not _id(row['source']) \
                or row['status']!=(('stale' if t-row['observed_at']>10 else 'fresh') if known else 'unknown'):
            raise ValueError('resource quality must follow actual received observation')


def decide_shortage_probe(snapshot):
    """Decide from a detached exact-schema snapshot of already received data."""
    if not isinstance(snapshot,dict) or set(snapshot)!={'t','phase','patient','candidates',
            'registered_search_patients','probe_count'}:
        raise ValueError('exact current shortage-probe snapshot required')
    t=snapshot['t'];p=snapshot['patient']
    if not previous.v1._quarter(t) or snapshot['phase']!='before_joint_batch' \
            or type(snapshot['probe_count']) is not int or snapshot['probe_count']<0 \
            or not isinstance(p,dict) or set(p)!=PATIENT_KEYS or not _id(p['id']) \
            or not isinstance(p['state'],str) or type(p['priority']) is not int or p['priority']<1 \
            or not previous.v1._quarter(p['request_t']) or p['request_t']>t \
            or type(p['request_event_sequence']) is not int or p['request_event_sequence']<0:
        raise ValueError('actual registered patient and decision phase required')
    for key in ('inquiry_episode','episode_contacts','inquiry_count'):
        if type(p[key]) is not int or p[key]<0:raise ValueError('observed counters required')
    for key in ('first_commit_t','last_contact_at'):
        if p[key] is not None and (not previous.v1._quarter(p[key]) or p[key]>t):
            raise ValueError('only observed patient timestamps allowed')
    if p['first_ask'] is not None:_handle(p['first_ask'],t)
    _handles(p['pending_inquiries'],t);_offers(p['valid_offers'],t)
    if not _unique_ids(p['tried_hospitals']) or not _unique_ids(p['required_capabilities']) \
            or not isinstance(p['demand'],dict) or any(not _id(k) or type(q) is not int or q<1 for k,q in p['demand'].items()):
        raise ValueError('actual demand and structural requirements required')
    candidates=snapshot['candidates']
    if not isinstance(candidates,list):raise ValueError('current structural candidates required')
    for c in candidates:
        if not isinstance(c,dict) or set(c)!={'hospital','travel_minutes','resources'} or not _id(c['hospital']) \
                or not _number(c['travel_minutes']) or c['travel_minutes']<0 \
                or not isinstance(c['resources'],dict) or set(c['resources'])!=set(p['demand']):
            raise ValueError('original current travel and required resource views required')
        for view in c['resources'].values():_view(view,t)
    by_hospital={c['hospital']:c for c in candidates}
    if len(by_hospital)!=len(candidates):raise ValueError('structural candidates must be unique')
    observed=snapshot['registered_search_patients']
    if not isinstance(observed,list):raise ValueError('current registered search cohort required')
    for q in observed:
        if not isinstance(q,dict) or set(q)!=OTHER_KEYS or not _id(q['id']) or q['state']!='SEARCH' \
                or not previous.v1._quarter(q['request_t']) or q['request_t']>t \
                or type(q['request_event_sequence']) is not int or q['request_event_sequence']<0:
            raise ValueError('only actually registered current search patients allowed')
        _handles(q['pending_inquiries'],t);_offers(q['valid_offers'],t)
    if len(observed)!=len({q['id'] for q in observed}) or p['id'] not in {q['id'] for q in observed}:
        raise ValueError('current patient must occur once in the registered search cohort')
    protected=sorted({r['hospital'] for q in observed if q['id']!=p['id']
        for field,deadline in (('pending_inquiries','timeout_at'),('valid_offers','expires_at'))
        for r in q[field] if r[deadline]>t})
    result=dict(send=[],reason='NO_ELIGIBLE_SHORTAGE',candidate_checks=[],protected_hospitals=protected)
    reason=None
    if p['state']!='SEARCH':reason='STATE'
    elif p['first_commit_t'] is not None:reason='COMMITTED'
    elif p['inquiry_episode']!=0:reason='EPISODE'
    elif p['first_ask'] is None or p['inquiry_count']<1 or p['last_contact_at'] is None:reason='NO_INITIAL_INQUIRY'
    elif t<p['first_ask']['submitted_at']+.5 or t<p['last_contact_at']+.5:reason='TOO_EARLY'
    elif snapshot['probe_count']>=1:reason='ALREADY_PROBED'
    elif p['episode_contacts']>=10:reason='CONTACT_LIMIT'
    elif not p['pending_inquiries']:reason='NO_PENDING_INQUIRY'
    elif len(p['pending_inquiries'])>=3:reason='CONCURRENCY_LIMIT'
    def usable(hid):
        return hid in by_hospital and all(v['status']=='fresh' and v['free'] is not None
            and v['free']>=p['demand'][r] for r,v in by_hospital[hid]['resources'].items())
    if reason is None and any(o['expires_at']>t and usable(o['hospital']) for o in p['valid_offers']):reason='USABLE_OFFER'
    if reason:return dict(result,reason=reason)
    if any(a['hospital'] not in by_hospital for a in p['pending_inquiries']):
        return dict(result,reason='PENDING_NOT_STRUCTURALLY_COMPATIBLE')
    shortest=min(by_hospital[a['hospital']]['travel_minutes'] for a in p['pending_inquiries'])
    own_claims={a['hospital'] for a in p['pending_inquiries']}|{o['hospital'] for o in p['valid_offers'] if o['expires_at']>t}
    for c in candidates:
        hid=c['hospital'];views=c['resources'];reason='RECHECK_ELIGIBLE'
        if hid in p['tried_hospitals']:reason='ALREADY_TRIED'
        elif hid in own_claims:reason='PENDING_OR_OFFER'
        elif c['travel_minutes']>shortest+2:reason='TRAVEL_LIMIT'
        elif hid in protected:reason='CLAIMED_BY_SEARCH_PATIENT'
        elif p['demand'].get('ed_bed',0)<=0:reason='NO_BED_DEMAND'
        elif any(v['status']=='unknown' or v['free'] is None for v in views.values()):reason='UNKNOWN_RESOURCE'
        elif any(v['status']!='fresh' for v in views.values()):reason='STALE_RESOURCE'
        elif views['ed_bed']['capacity']<p['demand']['ed_bed']:reason='INSUFFICIENT_REPORTED_CAPACITY'
        elif views['ed_bed']['free']>=p['demand']['ed_bed']:reason='NO_REPORTED_BED_SHORTAGE'
        elif any(v['free']<p['demand'][r] for r,v in views.items() if r!='ed_bed'):reason='OTHER_RESOURCE_SHORTAGE'
        result['candidate_checks'].append(dict(hospital=hid,eligible=reason=='RECHECK_ELIGIBLE',reason=reason))
    eligible=[r['hospital'] for r in result['candidate_checks'] if r['eligible']]
    if eligible:
        result.update(send=[min(eligible,key=lambda h:(by_hospital[h]['travel_minutes'],h))],reason='CURRENT_SHORTAGE_PROBE')
    return result


def decide_nonblocking_probe_ready(snapshot):
    """Keep real optional handles, but do not wait solely for optional work."""
    if not isinstance(snapshot,dict) or set(snapshot)!={'t','patient','probe_asks','candidates'}:
        raise ValueError('exact nonblocking-probe snapshot required')
    t=snapshot['t'];p=snapshot['patient']
    if not previous.v1._quarter(t) or not isinstance(p,dict) or set(p)!={
            'id','state','first_commit_t','pending_inquiries','valid_offers','demand'} or not _id(p['id']) \
            or not isinstance(p['state'],str) or (p['first_commit_t'] is not None
                and (not previous.v1._quarter(p['first_commit_t']) or p['first_commit_t']>t)) \
            or not isinstance(p['demand'],dict) or any(not _id(r) or type(q) is not int or q<1 for r,q in p['demand'].items()):
        raise ValueError('actual current nonblocking patient required')
    _handles(p['pending_inquiries'],t);_offers(p['valid_offers'],t);_handles(snapshot['probe_asks'],t)
    candidates=snapshot['candidates']
    if not isinstance(candidates,list):raise ValueError('current resource views required')
    for c in candidates:
        if not isinstance(c,dict) or set(c)!={'hospital','resources'} or not _id(c['hospital']) \
                or not isinstance(c['resources'],dict) or set(c['resources'])!=set(p['demand']):
            raise ValueError('exact current candidate resource view required')
        for v in c['resources'].values():_view(v,t)
    by_hospital={c['hospital']:c for c in candidates}
    if len(by_hospital)!=len(candidates):raise ValueError('current hospitals must be unique')
    result=dict(eligible=False,options=[],reason='NO_USABLE_OFFER')
    if p['state']!='SEARCH':return dict(result,reason='STATE')
    if p['first_commit_t'] is not None:return dict(result,reason='COMMITTED')
    if not p['pending_inquiries']:return dict(result,reason='NO_PENDING_INQUIRY')
    known={(a['hospital'],a['attempt'],a['ask_event_sequence']):a for a in snapshot['probe_asks']}
    if any(known.get((a['hospital'],a['attempt'],a['ask_event_sequence']))!=a for a in p['pending_inquiries']):
        return dict(result,reason='ORDINARY_INQUIRY_PENDING')
    options=sorted(o['hospital'] for o in p['valid_offers'] if o['expires_at']>t and o['hospital'] in by_hospital
        and all(v['status']=='fresh' and v['free'] is not None and v['free']>=p['demand'][r]
            for r,v in by_hospital[o['hospital']]['resources'].items()))
    return dict(eligible=True,options=options,reason='OPTIONAL_PROBE_NONBLOCKING') if options else result


class ShortageProbeReplay(previous.DeadlineSafeReplay):
    def __init__(self,*args,shortage_probe=True,**kwargs):
        if type(shortage_probe) is not bool or not shortage_probe:raise ValueError('explicit probe extension required')
        self.shortage_probe_enabled=True
        self.shortage_probe_records=[];self._probe_counts={};self._probe_first_asks={}
        self.shortage_probe_nonblocking_records=[];self._probe_ask_handles={}
        self.shortage_probe_seconds=0.0
        super().__init__(*args,**kwargs)

    def ev(self,t,typ,**fields):
        super().ev(t,typ,**fields)
        if typ=='ask':
            ask=next(e for e in reversed(self.log) if e['type']=='ask' and e['patient']==fields['patient']
                and e['hospital']==fields['hospital'] and e['attempt']==fields['attempt'])
            public=dict(hospital=ask['hospital'],attempt=ask['attempt'],
                submitted_at=ask['submitted_at'],timeout_at=ask['timeout_at'],ask_event_sequence=ask['event_sequence'])
            self._probe_first_asks.setdefault(fields['patient'],deepcopy(public))
            if 'shortage_probe_review_event_sequence' in ask:
                self._probe_ask_handles.setdefault(fields['patient'],[]).append(deepcopy(public))

    def _probe_offers(self,p):
        return [dict(hospital=hid,accepted_at=p.accepted_at[hid],expires_at=expiry,
            accept_event_sequence=self._accept_refs[p.id,hid]) for hid,expiry in sorted(p.valid.items())]

    def _probe_snapshot(self,p,t):
        current=self._current_patient_view(p)
        patient=dict(id=p.id,state=p.state,priority=p.priority,request_t=p.t_req,
            request_event_sequence=self._request_refs[p.id],inquiry_episode=self._episode_ids.get(p.id,0),
            episode_contacts=self._episode_contacts.get(p.id,0),inquiry_count=p.inquiries,
            first_commit_t=p.t_commit,first_ask=deepcopy(self._probe_first_asks.get(p.id)),
            tried_hospitals=sorted(p.tried),pending_inquiries=current['pending_inquiries'],
            valid_offers=self._probe_offers(p),demand=deepcopy(p.demand),required_capabilities=sorted(p.caps),
            last_contact_at=self._last_contact_t.get(p.id))
        observed=[]
        for pid in sorted(self._registered):
            q=self.patients[pid]
            if q.state=='SEARCH':observed.append(dict(id=q.id,state=q.state,request_t=q.t_req,
                request_event_sequence=self._request_refs[q.id],pending_inquiries=self._pending_view(q.asks),
                valid_offers=self._probe_offers(q)))
        return dict(t=t,phase='before_joint_batch',patient=patient,
            candidates=[dict(hospital=hid,travel_minutes=self.travel(p,hid,self.position(p,t)),
                resources=deepcopy(self._views(hid,p.demand,t))) for hid in current['compatible_hospitals']],
            registered_search_patients=observed,probe_count=self._probe_counts.get(p.id,0))

    def _maybe_shortage_probe(self,p,t):
        if p.state!='SEARCH' or p.t_commit is not None or self._episode_ids.get(p.id,0)!=0 \
                or p.inquiries<1 or not p.asks or self._probe_counts.get(p.id,0) \
                or p.id not in self._probe_first_asks or t<self._probe_first_asks[p.id]['submitted_at']+.5 \
                or t<self._last_contact_t.get(p.id,t)+.5 or self._episode_contacts.get(p.id,0)>=10 \
                or len(p.asks)>=3:return
        started=time.perf_counter();snapshot=self._probe_snapshot(p,t)
        result=decide_shortage_probe(snapshot)
        self.ev(t,'shortage_probe_review',patient=p.id,record_index=len(self.shortage_probe_records))
        review_sequence=self.log[-1]['event_sequence']
        self.shortage_probe_records.append(dict(snapshot=snapshot,result=deepcopy(result),event_sequence=review_sequence))
        for hid in result['send']:
            # This explicit exception has its own purpose; ordinary inquiry
            # eligibility continues to reject the reported fresh shortage.
            self.ev(t,'information_check',patient=p.id,hospital=hid,purpose='shortage_probe',
                eligible=True,reason='REPORTED_BED_SHORTAGE_RECHECK',resources=deepcopy(self._views(hid,p.demand,t)))
            attempt=p.attempts.get(hid,0)+1;p.attempts[hid]=attempt
            scheduled=self.schedule_ask(p,hid,attempt,t);p.asks.append(scheduled)
            self._inquiry_feedback_pending.append(dict(submitted_t=t,due_t=scheduled['at'],
                hospital=hid,patient=p.id,silent=scheduled['silent']))
            self.inquiry_history[hid].append(t);p.tried.add(hid);p.inquiries+=1;self.hosp_asks[hid]+=1
            self._probe_counts[p.id]=self._probe_counts.get(p.id,0)+1
            self.ev(t,'ask',patient=p.id,hospital=hid,attempt=attempt,
                    shortage_probe_review_event_sequence=review_sequence)
        self.shortage_probe_seconds+=time.perf_counter()-started

    def resolve_batch(self,t):
        # All current replies and ordinary patient turns have been consumed.
        # Probe before the unchanged early-readiness and joint final gate.
        self.ev(t,'shortage_probe_cycle')
        ready_ids={p.id for p,_ in self.ready}
        for pid in sorted(self._registered):
            p=self.patients[pid]
            if p.state!='SEARCH' or p.t_commit is not None or p.id in ready_ids \
                    or not p.asks or not p.valid or not self._probe_ask_handles.get(p.id):continue
            current=self._current_patient_view(p)
            snapshot=dict(t=t,patient=dict(id=p.id,state=p.state,first_commit_t=p.t_commit,
                pending_inquiries=current['pending_inquiries'],valid_offers=self._probe_offers(p),demand=deepcopy(p.demand)),
                probe_asks=deepcopy(self._probe_ask_handles[p.id]),
                candidates=[dict(hospital=hid,resources=deepcopy(self._views(hid,p.demand,t)))
                    for hid in current['compatible_hospitals']])
            result=decide_nonblocking_probe_ready(snapshot)
            self.ev(t,'shortage_probe_nonblocking_review',patient=p.id,record_index=len(self.shortage_probe_nonblocking_records))
            sequence=self.log[-1]['event_sequence']
            self.shortage_probe_nonblocking_records.append(dict(snapshot=snapshot,result=deepcopy(result),event_sequence=sequence))
            if result['eligible']:
                for hid in result['options']:
                    eligible,reason,_=self._check(p,hid,t,'allocation')
                    if not eligible or reason is not None:raise AssertionError('nonblocking current view changed')
                self.ev(t,'shortage_probe_nonblocking_ready',patient=p.id,options=result['options'],review_event_sequence=sequence)
                self.ready.append((p,list(result['options'])));ready_ids.add(p.id)
        for pid in sorted(self._registered):self._maybe_shortage_probe(self.patients[pid],t)
        return super().resolve_batch(t)

    def run_replay(self):
        branch=super().run_replay()
        branch.update(comparison_id=EXPERIMENT_ID,shortage_probe_enabled=True,
            shortage_probe_spec=deepcopy(SHORTAGE_PROBE_SPEC),shortage_probe_records=deepcopy(self.shortage_probe_records),
            shortage_probe_nonblocking_records=deepcopy(self.shortage_probe_nonblocking_records))
        branch['metrics'].update(shortage_probe_reviews=len(self.shortage_probe_records),
            shortage_probe_sent=sum(len(r['result']['send']) for r in self.shortage_probe_records),
            shortage_probe_nonblocking_ready=sum(r['result']['eligible'] for r in self.shortage_probe_nonblocking_records),
            shortage_probe_wall_seconds=self.shortage_probe_seconds)
        return branch


def run_shortage_probe_branch(scenario,*,inquiry='v4',allocation='H1',cohort_registration=True,
        scarce_routing=True,late_reply_recovery=True,late_pending_guard=True,shortage_probe=False,
        routing=None,branch_id=None,label=None):
    previous.v1.validate_joint_scenario(scenario)
    code=branch_id or f'{inquiry}_{allocation}_'+('probe' if shortage_probe else 'safe' if late_pending_guard else 'baseline')
    if type(shortage_probe) is not bool:raise ValueError('explicit boolean probe flag required')
    if not shortage_probe:
        return previous.run_deadline_safe_branch(scenario,inquiry=inquiry,allocation=allocation,
            cohort_registration=cohort_registration,scarce_routing=scarce_routing,
            late_reply_recovery=late_reply_recovery,late_pending_guard=late_pending_guard,
            routing=routing,branch_id=code,label=label or code)
    if inquiry not in ('v3','v4') or allocation not in ('H1','C1') or any(type(flag) is not bool or not flag
            for flag in (cohort_registration,scarce_routing,late_reply_recovery,late_pending_guard)):
        raise ValueError('probe requires unchanged safe-recovery intervention flags')
    rule=previous.v1.HeuristicAssignmentRule(time_limit_seconds=.05,max_moves=2048) if allocation=='H1' else \
        previous.v1.StableCPSATAssignmentRule(time_limit_seconds=.05,seed=0,require_backend=True)
    policy=previous.v1.Policy(rule=rule,k=3,wait=2,notify=True,name=code,observed_inquiry={})
    replay=ShortageProbeReplay(deepcopy(scenario),policy,routing=routing,inquiry=inquiry,
        recovery=True,pending_protection=True,cohort_registration=True,scarce_routing=True,
        late_reply_recovery=True,late_pending_guard=True,shortage_probe=True)
    branch=replay.run_replay()
    # Reuse unchanged descriptive safe-recovery specs without executing any
    # extra branch or replacing the underlying saved raw evidence.
    branch.update(id=code,label=label or code,policy=previous.v1._policy_spec(policy),
        scarce_delay_spec=dict(inquiry=inquiry,allocation=allocation,cohort_registration=True,scarce_routing=True,
            actual_action='parallel_scarce_supplement',original_selector_candidates_unchanged=True,
            maximum_supplements_per_patient=1,recovery=True,pending_protection=True,
            pending_window_minutes=1,time_step_seconds=15,actual_selector_reused=True,
            extra_resource_change_publications=False),
        deadline_recovery_spec=dict(late_reply_recovery=True,scope='one_initial_scarce_supplement',
            late_reply_window_minutes=previous.v1.LATE_REPLY_WINDOW_MINUTES,original_timeout_unchanged=True,
            original_timeout_feedback_unchanged=True,blocking_wait_added=False,hospital_work_calendar_unchanged=True,
            patient_condition_fingerprint_required=True,new_offer_ttl_from_actual_receipt=True,
            actual_reply_outcome_required=True,current_fresh_resource_check_required=True,
            current_valid_offer_rejects=True,newer_same_hospital_attempt_rejects=True))
    return branch


DEFAULT_SHORTAGE_PROBE_POLICIES=tuple(dict(id=f'v4_{allocation}_{stage}',label=f'v4_{allocation}_{stage}',
    inquiry='v4',allocation=allocation,cohort_registration=stage!='baseline',scarce_routing=stage!='baseline',
    late_reply_recovery=stage!='baseline',late_pending_guard=stage!='baseline',shortage_probe=stage=='probe')
    for allocation in ('H1','C1') for stage in ('baseline','safe','probe'))+tuple(
    dict(id=f'fixed3_{allocation}_baseline',label=f'fixed3_{allocation}_baseline',inquiry='fixed_3',allocation=allocation,
        cohort_registration=False,scarce_routing=False,late_reply_recovery=False,late_pending_guard=False,shortage_probe=False)
    for allocation in ('H1','C1'))


def run_shortage_probe_replay(scenario,policies=None,execution_order=None,routing=None):
    previous.v1.validate_joint_scenario(scenario)
    specs=deepcopy(list(DEFAULT_SHORTAGE_PROBE_POLICIES if policies is None else policies));ids=[r['id'] for r in specs]
    if not ids or len(ids)!=len(set(ids)) or any(not _id(code) for code in ids):raise ValueError('unique policy IDs required')
    order=list(ids if execution_order is None else execution_order)
    if len(order)!=len(ids) or set(order)!=set(ids):raise ValueError('each policy must execute once')
    by_id={r['id']:r for r in specs};output={}
    keys={'id','inquiry','allocation','cohort_registration','scarce_routing','late_reply_recovery','late_pending_guard','shortage_probe'}
    for code in order:
        row=by_id[code]
        if not keys<=set(row) or set(row)-(keys|{'label'}):raise ValueError('exact shortage-probe policy row required')
        output[code]=run_shortage_probe_branch(scenario,**{k:row[k] for k in keys-{'id'}},
            routing=routing,branch_id=code,label=row.get('label',code))
    return dict(schema_version=1,experiment_id=EXPERIMENT_ID,scenario=deepcopy(scenario),
        branches=[output[code] for code in ids],execution_order=order)
