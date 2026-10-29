"""Experimental hospital admission ACKs and interruptible optional review work.

This is a new workflow contract, not a claim about deployed hospital software.
One existing worker can pause optional work for a real normal request and resume
the remaining work. Current status ACKs reveal no service demand or finish time.
Patient acceptance, original sender deadlines and physical allocation gates are
unchanged. Queue-disabled environments do not invent this control channel.
"""
from __future__ import annotations
from copy import deepcopy
import math
import time

from . import shortage_probe_engine as prior

v1=prior.previous.v1
EXPERIMENT_ID='cooperative_probe_20261017'
COOPERATIVE_PROBE_SPEC=dict(only_when_inquiry_queue_enabled=True,
    admission='current_idle_worker_and_no_waiting_normal_job',
    normal_jobs='nonpreemptive_fifo',probe_jobs='idle_admission_preempt_resume',
    preemption='actual_normal_submission_only',probe_resume_order='original_submission_order',
    worker_count_unchanged=True,service_quantity_unchanged=True,
    current_control_ack_states=['running','queued','completed','not_admitted'],
    control_ack_is_not_clinical_acceptance=True,control_ack_latency_minutes=0,
    preemption_overhead_minutes=0,probe_work_must_be_completed_for_optional_readiness=True,
    busy_contact_consumes_attempt=True,busy_contact_consumes_episode_budget=True,
    clinical_attempt_stream='accepted_jobs_only_per_patient_hospital',busy_clinical_attempt=None,
    busy_admission_does_not_read_response_environment=True,
    busy_contact_consumes_normal_tried=False,busy_contact_preserves_actual_last_contact=True,
    nonadmitted_new_fresh_report_retry='one_explicit_normal_request_with_actual_selector_evidence',
    original_timeout_unchanged=True,original_offer_ttl_unchanged=True,
    actual_reply_and_fresh_physical_gate_required=True,
    future_finish_or_remaining_work_visible_to_policy=False,
    coalescing_enabled=False,automatic_rerouting_added=False,
    extra_resource_change_publications=False)
COOPERATIVE_SHORTAGE_SPEC=dict(deepcopy(prior.SHORTAGE_PROBE_SPEC),
    actual_fifo_inquiry_required=False,actual_normal_fifo_required=True,
    optional_probe_never_blocks_an_existing_usable_offer=False,
    optional_readiness_requires_completed_control_ack=True)


class CooperativeInquiryQueue:
    """An online fixed-worker scheduler; raw work is private environmental data."""
    def __init__(self,hospitals,workers=1,emit=None):
        if type(workers) is not int or workers<1:raise ValueError('positive fixed worker count required')
        self.hospital_ids=list(hospitals);self.workers=workers
        if len(set(self.hospital_ids))!=len(self.hospital_ids):raise ValueError('unique hospitals required')
        self.jobs=[];self._by_id={};self._running={h:{} for h in self.hospital_ids}
        self._now=0;self._emit=emit or (lambda *a,**k:None)

    def _event(self,typ,job,**extra):
        self._emit(self._now,typ,job_id=job['job_id'],hospital=job['hospital'],patient=job['patient'],
            attempt=job['attempt'],clinical_attempt=job['clinical_attempt'],kind=job['kind'],work_class=job['work_class'],**extra)

    def _start(self,job,worker):
        first=not job['segments'];job['state']='running';job['_worker']=worker
        if first:job['start_t']=self._now
        job['segments'].append(dict(start_t=self._now,end_t=None,worker=worker))
        self._running[job['hospital']][worker]=job['job_id']
        self._event('cooperative_job_started' if first else 'cooperative_job_resumed',job,
            worker=worker,elapsed_minutes=job['elapsed_minutes'],remaining_minutes=job['remaining_minutes'])

    def _pause(self,job):
        worker=job['_worker'];job['segments'][-1]['end_t']=self._now
        del self._running[job['hospital']][worker]
        job['state']='queued';job['_worker']=None
        self._event('cooperative_job_paused',job,worker=worker,
            elapsed_minutes=job['elapsed_minutes'],remaining_minutes=job['remaining_minutes'])

    def _dispatch(self,hid):
        waiting=sorted((j for j in self.jobs if j['hospital']==hid and j['state']=='queued'),
            key=lambda j:(j['work_class']=='probe',j['submitted_t'],j['job_id']))
        for job in waiting:
            idle=[w for w in range(self.workers) if w not in self._running[hid]]
            if not idle and job['work_class']=='normal':
                probes=[(w,self._by_id[jid]) for w,jid in self._running[hid].items()
                    if self._by_id[jid]['work_class']=='probe']
                if probes:
                    worker,victim=min(probes,key=lambda pair:pair[0]);self._pause(victim);idle=[worker]
            if not idle:return
            self._start(job,min(idle))

    def advance(self,t):
        if not v1._quarter(t) or t<self._now or (t-self._now not in (0,.25)):
            raise ValueError('online work advances once per actual quarter minute')
        elapsed=t-self._now;self._now=t
        running=[self._by_id[jid] for hid in self.hospital_ids for _,jid in sorted(self._running[hid].items())]
        finished=[]
        for job in running:
            if elapsed>job['remaining_minutes']:raise AssertionError('work completed between clock ticks')
            job['elapsed_minutes']+=elapsed;job['remaining_minutes']-=elapsed
            if job['remaining_minutes']==0:finished.append(job)
        for job in finished:
            worker=job['_worker'];job['segments'][-1]['end_t']=t
            del self._running[job['hospital']][worker]
            job.update(state='completed',finish_t=t,_worker=None)
            self._event('cooperative_job_completed',job,worker=worker,
                elapsed_minutes=job['elapsed_minutes'],remaining_minutes=0)
        for hid in self.hospital_ids:self._dispatch(hid)

    def can_admit_probe(self,hospital):
        return (len(self._running[hospital])<self.workers and not any(j['hospital']==hospital
            and j['state']=='queued' and j['work_class']=='normal' for j in self.jobs))

    def enqueue(self,hospital,t,patient,attempt,service_minutes,kind='inquiry',silent=False,work_class='normal',clinical_attempt=None):
        if hospital not in self._running or t!=self._now or not v1._quarter(service_minutes,positive=True) \
                or type(silent) is not bool or work_class not in ('normal','probe'):
            raise ValueError('current exact work submission required')
        if work_class=='probe' and not self.can_admit_probe(hospital):
            return None
        job=dict(job_id=len(self.jobs),hospital=hospital,patient=patient,attempt=attempt,clinical_attempt=clinical_attempt,
            kind=kind,work_class=work_class,submitted_t=t,service_minutes=service_minutes,silent=silent,
            state='queued',elapsed_minutes=0,remaining_minutes=service_minutes,
            start_t=math.inf,finish_t=math.inf,segments=[],_worker=None)
        self.jobs.append(job);self._by_id[job['job_id']]=job
        self._event('cooperative_job_submitted',job,service_minutes=service_minutes,silent=silent)
        self._dispatch(hospital)
        return job

    def snapshot(self,t):
        if t!=self._now:raise ValueError('only a currently reached work snapshot may be read')
        rows=[]
        for hid in self.hospital_ids:
            jobs=[j for j in self.jobs if j['hospital']==hid]
            rows.append(dict(hospital=hid,workers=self.workers,waiting=sum(j['state']=='queued' for j in jobs),
                processing=sum(j['state']=='running' for j in jobs),completed=sum(j['state']=='completed' for j in jobs),
                submitted=len(jobs),queue_wait_total_minutes=sum((j['finish_t'] if j['state']=='completed' else t)
                    -j['submitted_t']-j['elapsed_minutes'] for j in jobs),
                service_total_minutes=sum(j['elapsed_minutes'] for j in jobs)))
        return rows

    def report(self,t):
        rows=self.snapshot(t);fields=('workers','waiting','processing','completed','submitted',
            'queue_wait_total_minutes','service_total_minutes')
        return dict(end_t=t,aggregate={k:sum(r[k] for r in rows) for k in fields},by_hospital=rows)

    def ledger(self):
        # Infinity is only the private scheduler's unknown-time sentinel, never
        # a reported finish prediction or serialized evidence value.
        return [dict(**{k:deepcopy(v) for k,v in j.items() if k not in ('_worker','start_t','finish_t')},
            start_t=None if math.isinf(j['start_t']) else j['start_t'],
            finish_t=None if math.isinf(j['finish_t']) else j['finish_t']) for j in self.jobs]


def decide_cooperative_ready(snapshot):
    """Only actual current ACK states supplement the previous readiness input."""
    if not isinstance(snapshot,dict) or set(snapshot)!={'base_snapshot','control_acknowledgements'}:
        raise ValueError('exact cooperative readiness input required')
    base=snapshot['base_snapshot'];result=prior.decide_nonblocking_probe_ready(base)
    acks=snapshot['control_acknowledgements']
    if not isinstance(acks,list):raise ValueError('actual current ACKs required')
    keys={'hospital','attempt','ask_event_sequence','job_id','state','received_at','control_event_sequence'}
    for ack in acks:
        if not isinstance(ack,dict) or set(ack)!=keys or not prior._id(ack['hospital']) \
                or type(ack['attempt']) is not int or ack['attempt']<1 \
                or any(type(ack[k]) is not int or ack[k]<0 for k in ('ask_event_sequence','control_event_sequence')) \
                or ack['state'] not in ('running','queued','completed','not_admitted') \
                or not v1._quarter(ack['received_at']) or ack['received_at']>base['t'] \
                or (ack['job_id'] is not None and (type(ack['job_id']) is not int or ack['job_id']<0)) \
                or ((ack['job_id'] is None)!=(ack['state']=='not_admitted')) \
                or ack['control_event_sequence']<=ack['ask_event_sequence']:
            raise ValueError('current public work ACK without hidden work timing required')
    indexed={(a['hospital'],a['attempt'],a['ask_event_sequence']):a for a in acks}
    if len(indexed)!=len(acks):raise ValueError('one latest ACK per real probe required')
    probes={(a['hospital'],a['attempt'],a['ask_event_sequence']) for a in base['probe_asks']}
    if set(indexed)!=probes:raise ValueError('each submitted probe requires its latest public ACK')
    if result['eligible'] and any(indexed[a['hospital'],a['attempt'],a['ask_event_sequence']]['state']!='completed'
            for a in base['patient']['pending_inquiries']):
        return dict(eligible=False,options=[],reason='PROBE_WORK_PENDING')
    return result


def decide_busy_report_retry(snapshot):
    """One explicit normal request after an actual rejection and new report.

    The original selector input and contact clock are not rewritten. This is
    an independently recorded workflow exception with its own current proof.
    """
    if not isinstance(snapshot,dict) or set(snapshot)!={'opportunity_snapshot','not_admitted_ack',
            'retry_count','ordinary_selector_event_sequence','offered_hospitals','ordinary_send','max_new'}:
        raise ValueError('exact current busy-report retry evidence required')
    current=snapshot['opportunity_snapshot'];prior.decide_shortage_probe(current)
    p=current['patient'];t=current['t'];ack=snapshot['not_admitted_ack']
    ackkeys={'hospital','attempt','ask_event_sequence','job_id','state','received_at','control_event_sequence'}
    if not isinstance(ack,dict) or set(ack)!=ackkeys or ack['state']!='not_admitted' or ack['job_id'] is not None \
            or not prior._id(ack['hospital']) or not v1._quarter(ack['received_at']) or ack['received_at']>t \
            or any(type(ack[k]) is not int or ack[k]<0 for k in ('ask_event_sequence','control_event_sequence')) \
            or type(ack['attempt']) is not int or ack['attempt']<1:
        raise ValueError('actual nonadmitted control ACK required')
    for name in ('retry_count','ordinary_selector_event_sequence','max_new'):
        if type(snapshot[name]) is not int or snapshot[name]<0:raise ValueError('actual retry and selector counters required')
    if not prior._unique_ids(snapshot['offered_hospitals']) or not prior._unique_ids(snapshot['ordinary_send']):
        raise ValueError('actual unmodified normal selector candidate and send identities required')
    output=dict(send=[],reason='NO_RETRY')
    if p['state']!='SEARCH' or p['first_commit_t'] is not None or p['inquiry_episode']!=0:
        return dict(output,reason='NOT_INITIAL_SEARCH')
    if snapshot['retry_count']>=1:return dict(output,reason='ALREADY_RETRIED')
    if not p['pending_inquiries']:return dict(output,reason='NO_PENDING_INQUIRY')
    if snapshot['ordinary_send']:return dict(output,reason='NORMAL_SELECTOR_ALREADY_SENT')
    if p['episode_contacts']>=10 or len(p['pending_inquiries'])>=3 or snapshot['max_new']<1:
        return dict(output,reason='INQUIRY_BUDGET')
    hid=ack['hospital'];candidates={c['hospital']:c for c in current['candidates']}
    if hid not in candidates or hid not in snapshot['offered_hospitals']:
        return dict(output,reason='NOT_CURRENT_NORMAL_CANDIDATE')
    if hid in p['tried_hospitals'] or any(a['hospital']==hid for a in p['pending_inquiries']) \
            or any(o['hospital']==hid and o['expires_at']>t for o in p['valid_offers']):
        return dict(output,reason='ALREADY_NORMAL_TRIED_OR_CLAIMED')
    def usable(c):
        return all(v['status']=='fresh' and v['free'] is not None and v['free']>=p['demand'][r]
            for r,v in c['resources'].items())
    if any(o['expires_at']>t and o['hospital'] in candidates and usable(candidates[o['hospital']]) for o in p['valid_offers']):
        return dict(output,reason='USABLE_OFFER')
    target=candidates[hid]
    if not usable(target):return dict(output,reason='RESOURCES_NOT_FRESH_SUFFICIENT')
    if any(v['received_at'] is None or v['received_at']<=ack['received_at'] for v in target['resources'].values()) \
            or not any(v['received_at']==t for v in target['resources'].values()):
        return dict(output,reason='NO_NEW_CURRENT_REPORT')
    if any(q['id']!=p['id'] and (any(a['hospital']==hid and a['timeout_at']>t for a in q['pending_inquiries'])
            or any(o['hospital']==hid and o['expires_at']>t for o in q['valid_offers']))
            for q in current['registered_search_patients']):
        return dict(output,reason='OTHER_CURRENT_SEARCH_CLAIM')
    return dict(send=[hid],reason='NONADMITTED_NEW_FRESH_REPORT_NORMAL_RETRY')


class CooperativeProbeReplay(prior.ShortageProbeReplay):
    def __init__(self,*args,cooperative_probe=True,**kwargs):
        if cooperative_probe is not True:raise ValueError('explicit cooperative workflow required')
        self.cooperative_probe_enabled=True
        self.cooperative_control_acks=[];self.cooperative_ready_records=[]
        self.cooperative_busy_retry_records=[];self._coop_busy_retries={}
        self._coop_handles={};self._coop_jobs={};self._coop_ask_refs={};self._coop_buffered_acks={}
        self._coop_latest_acks={};self._coop_probe_submission=False
        self._coop_clinical_attempts={}
        self._coop_started_jobs=set();self._coop_in_ask=False
        super().__init__(*args,**kwargs)
        self.cooperative_queue_enabled=self.inquiry_queue is not None
        if self.cooperative_queue_enabled:
            self.inquiry_queue=CooperativeInquiryQueue(list(self.hospitals),self.inquiry_queue.workers,self._queue_event)

    def _queue_event(self,t,typ,**fields):
        self.ev(t,typ,**fields)
        if typ=='cooperative_job_started':
            self._coop_started_jobs.add(fields['job_id'])
            self._review_events.setdefault(t,[]).append(dict(type='review_started',patient=fields['patient'],
                hospital=fields['hospital'],kind=fields['kind'],attempt=fields['attempt']))
        if typ=='cooperative_job_completed':
            self._review_events.setdefault(t,[]).append(dict(type='review_completed',patient=fields['patient'],
                hospital=fields['hospital'],kind=fields['kind'],attempt=fields['attempt']))
            key=(fields['patient'],fields['hospital'],fields['attempt'])
            handle=self._coop_handles.get(key)
            if handle is not None and not self.inquiry_queue._by_id[fields['job_id']]['silent'] and t>handle['timeout_at']:
                self._review_events.setdefault(t,[]).append(dict(type='late_reply',patient=fields['patient'],
                    hospital=fields['hospital'],attempt=fields['attempt'],ignored=True))
        states={'cooperative_job_started':'running','cooperative_job_resumed':'running',
            'cooperative_job_paused':'queued','cooperative_job_completed':'completed'}
        if fields['work_class']=='probe' and typ in states:
            jid=fields['job_id'];state=states[typ]
            if jid in self._coop_ask_refs:self._control_ack(t,jid,state)
            else:self._coop_buffered_acks.setdefault(jid,[]).append((t,state))

    def _control_ack(self,t,job_id,state,*,ask=None):
        if ask is None:
            job=self.inquiry_queue._by_id[job_id]
            ask=dict(hospital=job['hospital'],patient=job['patient'],attempt=job['attempt'],
                event_sequence=self._coop_ask_refs[job_id])
        self.ev(t,'cooperative_probe_control_ack',hospital=ask['hospital'],patient=ask['patient'],
            attempt=ask['attempt'],ask_event_sequence=ask['event_sequence'],job_id=job_id,state=state,received_at=t)
        event=deepcopy(self.log[-1]);self.cooperative_control_acks.append(event)
        self._coop_latest_acks[ask['patient'],ask['hospital'],ask['attempt']]=dict(hospital=ask['hospital'],
            attempt=ask['attempt'],ask_event_sequence=ask['event_sequence'],job_id=job_id,state=state,
            received_at=t,control_event_sequence=event['event_sequence'])

    def ev(self,t,typ,**fields):
        if typ=='ask' and getattr(self,'cooperative_queue_enabled',False):
            key=(fields['patient'],fields['hospital'],fields['attempt'])
            job_id=self._coop_jobs.get(key)
            fields.update(cooperative_job_id=job_id,cooperative_probe_request=self._coop_probe_submission)
            fields['clinical_attempt']=None if job_id is None else self.inquiry_queue._by_id[job_id]['clinical_attempt']
        super().ev(t,typ,**fields)
        if typ=='ask' and getattr(self,'cooperative_queue_enabled',False):
            ask=next(e for e in reversed(self.log) if e['type']=='ask' and e['patient']==fields['patient']
                and e['hospital']==fields['hospital'] and e['attempt']==fields['attempt'])
            job_id=ask['cooperative_job_id']
            if job_id is not None:
                self._coop_ask_refs[job_id]=ask['event_sequence']
                for at,state in self._coop_buffered_acks.pop(job_id,[]):self._control_ack(at,job_id,state)

    def _enqueue_review(self,hid,t,pid,attempt,service,silent=False,kind='inquiry'):
        if not self.cooperative_queue_enabled:return super()._enqueue_review(hid,t,pid,attempt,service,silent,kind)
        return self.inquiry_queue.enqueue(hid,t,pid,attempt,service,kind=kind,silent=silent,work_class='normal')

    def schedule_ask(self,p,hid,attempt,t):
        if not self.cooperative_queue_enabled:return super().schedule_ask(p,hid,attempt,t)
        job=None
        if not self._coop_probe_submission or self.inquiry_queue.can_admit_probe(hid):
            clinical=self._coop_clinical_attempts.get((p.id,hid),0)+1
            silent,service=self._response(p,hid,clinical)
            job=self.inquiry_queue.enqueue(hid,t,p.id,attempt,service,silent=silent,
                work_class='probe' if self._coop_probe_submission else 'normal',clinical_attempt=clinical)
            if job is None:raise AssertionError('current admission changed without an intervening event')
            self._coop_clinical_attempts[p.id,hid]=clinical
        key=(p.id,hid,attempt);self._coop_jobs[key]=None if job is None else job['job_id']
        handle=dict(hid=hid,at=t+self.cfg['timeout'],silent=True,submitted_t=t,attempt=attempt,
            submitted_at=t,timeout_at=t+self.cfg['timeout'],ask_event_sequence=None,
            cooperative_job_id=self._coop_jobs[key])
        self._coop_handles[key]=handle
        return handle

    def _synchronize_handles(self):
        pending=[*self._coop_handles.values(),*(a for p in self.patients.values() for a in p.asks),
            *(a for episode in self._episodes.values() for a in episode['pending'])]
        for handle in pending:
            job_id=handle.get('cooperative_job_id')
            if job_id is None:continue
            job=self.inquiry_queue._by_id[job_id];actual=job['finish_t']
            timely=not job['silent'] and actual<=handle['timeout_at']
            handle['at']=actual if timely else handle['timeout_at'];handle['silent']=not timely
        for feedback in self._inquiry_feedback_pending:
            matches=[a for (pid,hid,_),a in self._coop_handles.items()
                if pid==feedback['patient'] and hid==feedback['hospital'] and a['submitted_at']==feedback['submitted_t']]
            if len(matches)==1:
                feedback['due_t']=matches[0]['at'];feedback['silent']=matches[0]['silent']

    def _receive_inquiry_feedback(self,t):
        if getattr(self,'cooperative_queue_enabled',False):
            self.inquiry_queue.advance(t);self._synchronize_handles()
        return super()._receive_inquiry_feedback(t)

    def _maybe_shortage_probe(self,p,t):
        if not self.cooperative_queue_enabled:return
        if p.state!='SEARCH' or p.t_commit is not None or self._episode_ids.get(p.id,0)!=0 \
                or p.inquiries<1 or not p.asks or self._probe_counts.get(p.id,0) \
                or p.id not in self._probe_first_asks or t<self._probe_first_asks[p.id]['submitted_at']+.5 \
                or t<self._last_contact_t.get(p.id,t)+.5 or self._episode_contacts.get(p.id,0)>=10 or len(p.asks)>=3:return
        started=time.perf_counter();snapshot=self._probe_snapshot(p,t);result=prior.decide_shortage_probe(snapshot)
        self.ev(t,'shortage_probe_review',patient=p.id,record_index=len(self.shortage_probe_records))
        sequence=self.log[-1]['event_sequence']
        self.shortage_probe_records.append(dict(snapshot=snapshot,result=deepcopy(result),event_sequence=sequence))
        for hid in result['send']:
            self.ev(t,'information_check',patient=p.id,hospital=hid,purpose='shortage_probe',eligible=True,
                reason='REPORTED_BED_SHORTAGE_RECHECK',resources=deepcopy(self._views(hid,p.demand,t)))
            attempt=p.attempts.get(hid,0)+1
            p.attempts[hid]=attempt;self._coop_probe_submission=True
            try:
                scheduled=self.schedule_ask(p,hid,attempt,t);p.asks.append(scheduled)
                accepted=scheduled['cooperative_job_id'] is not None
                if accepted:self._inquiry_feedback_pending.append(dict(submitted_t=t,due_t=scheduled['at'],
                    hospital=hid,patient=p.id,silent=scheduled['silent']))
                self.inquiry_history[hid].append(t)
                if accepted:p.tried.add(hid)
                p.inquiries+=1;self.hosp_asks[hid]+=1;self._probe_counts[p.id]=self._probe_counts.get(p.id,0)+1
                self.ev(t,'ask',patient=p.id,hospital=hid,attempt=attempt,shortage_probe_review_event_sequence=sequence)
            finally:self._coop_probe_submission=False
            if not accepted:
                ask=next(e for e in reversed(self.log) if e['type']=='ask' and e['patient']==p.id and e['hospital']==hid and e['attempt']==attempt)
                self._control_ack(t,None,'not_admitted',ask=ask)
                self.ev(t,'cooperative_probe_not_admitted',patient=p.id,hospital=hid,attempt=attempt,
                    ask_event_sequence=ask['event_sequence'],original_timeout_at=scheduled['timeout_at'])
                p.asks.remove(scheduled)
        self.shortage_probe_seconds+=time.perf_counter()-started

    def resolve_batch(self,t):
        self.ev(t,'shortage_probe_cycle');ready_ids={p.id for p,_ in self.ready}
        for pid in sorted(self._registered):self._maybe_busy_report_retry(self.patients[pid],t,ready_ids)
        for pid in sorted(self._registered):
            p=self.patients[pid]
            if p.state!='SEARCH' or p.t_commit is not None or p.id in ready_ids or not p.asks or not p.valid \
                    or not self._probe_ask_handles.get(p.id):continue
            current=self._current_patient_view(p)
            base=dict(t=t,patient=dict(id=p.id,state=p.state,first_commit_t=p.t_commit,
                pending_inquiries=current['pending_inquiries'],valid_offers=self._probe_offers(p),demand=deepcopy(p.demand)),
                probe_asks=deepcopy(self._probe_ask_handles[p.id]),candidates=[dict(hospital=hid,
                    resources=deepcopy(self._views(hid,p.demand,t))) for hid in current['compatible_hospitals']])
            acks=[deepcopy(self._coop_latest_acks[p.id,a['hospital'],a['attempt']]) for a in base['probe_asks']]
            snapshot=dict(base_snapshot=base,control_acknowledgements=acks);result=decide_cooperative_ready(snapshot)
            self.ev(t,'cooperative_probe_ready_review',patient=p.id,record_index=len(self.cooperative_ready_records))
            sequence=self.log[-1]['event_sequence']
            self.cooperative_ready_records.append(dict(snapshot=snapshot,result=deepcopy(result),event_sequence=sequence))
            if result['eligible']:
                for hid in result['options']:
                    eligible,reason,_=self._check(p,hid,t,'allocation')
                    if not eligible or reason is not None:raise AssertionError('current cooperative allocation view changed')
                self.ev(t,'cooperative_probe_nonblocking_ready',patient=p.id,options=result['options'],review_event_sequence=sequence)
                self.ready.append((p,list(result['options'])));ready_ids.add(p.id)
        for pid in sorted(self._registered):self._maybe_shortage_probe(self.patients[pid],t)
        # Deliberately bypass only the preceding optional-work readiness stage;
        # all actual late-reply, scarcity, quantitative and physical gates run.
        return prior.previous.DeadlineSafeReplay.resolve_batch(self,t)

    def _maybe_busy_report_retry(self,p,t,ready_ids):
        if not self.cooperative_queue_enabled or p.state!='SEARCH' or p.t_commit is not None \
                or p.id in ready_ids or not p.asks or self._episode_ids.get(p.id,0)!=0 \
                or self._coop_busy_retries.get(p.id,0):return
        rejected=[ack for (pid,_,_),ack in self._coop_latest_acks.items() if pid==p.id and ack['state']=='not_admitted']
        if len(rejected)!=1:return
        ack=rejected[0];hid=ack['hospital']
        if not any(v.get('received_at')==t for v in self._views(hid,p.demand,t).values()):return
        selector=next((r for r in reversed(self.inquiry_decisions) if r['patient']==p.id and r['t']==t),None)
        if selector is None:return
        snapshot=dict(opportunity_snapshot=self._probe_snapshot(p,t),not_admitted_ack=deepcopy(ack),
            retry_count=self._coop_busy_retries.get(p.id,0),ordinary_selector_event_sequence=selector['event_sequence'],
            offered_hospitals=[c['hospital_id'] for c in selector['inputs']['candidates']],
            ordinary_send=list(selector['decision']['send']),max_new=selector['inputs']['max_new'])
        result=decide_busy_report_retry(snapshot)
        self.ev(t,'cooperative_busy_retry_review',patient=p.id,record_index=len(self.cooperative_busy_retry_records))
        sequence=self.log[-1]['event_sequence']
        self.cooperative_busy_retry_records.append(dict(snapshot=snapshot,result=deepcopy(result),event_sequence=sequence))
        for hospital in result['send']:
            eligible,reason,_=self._check(p,hospital,t,'inquiry')
            if not eligible or reason is not None:raise AssertionError('new normal retry resources changed')
            attempt=p.attempts.get(hospital,0)+1;p.attempts[hospital]=attempt
            scheduled=self.schedule_ask(p,hospital,attempt,t);p.asks.append(scheduled)
            self._inquiry_feedback_pending.append(dict(submitted_t=t,due_t=scheduled['at'],
                hospital=hospital,patient=p.id,silent=scheduled['silent']))
            p.tried.add(hospital);p.inquiries+=1;self.hosp_asks[hospital]+=1;self.inquiry_history[hospital].append(t)
            self._coop_busy_retries[p.id]=self._coop_busy_retries.get(p.id,0)+1
            self.ev(t,'ask',patient=p.id,hospital=hospital,attempt=attempt,cooperative_busy_retry_review_event_sequence=sequence)

    def run_replay(self):
        branch=super().run_replay()
        branch.update(comparison_id=EXPERIMENT_ID,cooperative_probe_enabled=True,
            cooperative_queue_enabled=self.cooperative_queue_enabled,
            cooperative_probe_spec=deepcopy(COOPERATIVE_PROBE_SPEC),
            cooperative_job_ledger=self.inquiry_queue.ledger() if self.cooperative_queue_enabled else [],
            cooperative_control_acks=deepcopy(self.cooperative_control_acks),
            cooperative_ready_records=deepcopy(self.cooperative_ready_records),
            cooperative_busy_retry_records=deepcopy(self.cooperative_busy_retry_records),
            shortage_probe_spec=deepcopy(COOPERATIVE_SHORTAGE_SPEC))
        metrics=branch['metrics'];metrics.update(cooperative_control_acks=len(self.cooperative_control_acks),
            cooperative_probe_not_admitted=sum(a['state']=='not_admitted' for a in self.cooperative_control_acks),
            cooperative_nonblocking_ready=sum(r['result']['eligible'] for r in self.cooperative_ready_records),
            cooperative_preemptions=sum(e['type']=='cooperative_job_paused' for e in self.log))
        metrics['cooperative_busy_retries']=sum(len(r['result']['send']) for r in self.cooperative_busy_retry_records)
        if self.cooperative_queue_enabled:
            # The legacy aggregator assumes continuous work. Replace its queue
            # statistics using actual elapsed segments, including pauses.
            end=metrics['observation_end_t'];jobs=[j for j in self.inquiry_queue.jobs if j['kind']=='inquiry']
            wait=sum((j['finish_t'] if j['state']=='completed' else end)-j['submitted_t']-j['elapsed_minutes'] for j in jobs)
            metrics.update(inquiry_queue_wait_mean=wait/len(jobs) if jobs else None,
                inquiry_service_minutes=sum(j['elapsed_minutes'] for j in jobs),
                inquiry_unfinished_jobs=sum(j['state']!='completed' for j in jobs))
        return branch


def run_cooperative_probe_branch(scenario,*,inquiry='v4',allocation='H1',cohort_registration=True,
        scarce_routing=True,late_reply_recovery=True,late_pending_guard=True,shortage_probe=False,
        cooperative_probe=False,routing=None,branch_id=None,label=None):
    v1.validate_joint_scenario(scenario)
    if type(cooperative_probe) is not bool:raise ValueError('explicit cooperative workflow flag required')
    code=branch_id or f'{inquiry}_{allocation}_'+('cooperative' if cooperative_probe else 'probe' if shortage_probe else 'safe')
    if not cooperative_probe:
        return prior.run_shortage_probe_branch(scenario,inquiry=inquiry,allocation=allocation,
            cohort_registration=cohort_registration,scarce_routing=scarce_routing,
            late_reply_recovery=late_reply_recovery,late_pending_guard=late_pending_guard,shortage_probe=shortage_probe,
            routing=routing,branch_id=code,label=label or code)
    if inquiry not in ('v3','v4') or allocation not in ('H1','C1') or any(type(flag) is not bool or not flag
            for flag in (cohort_registration,scarce_routing,late_reply_recovery,late_pending_guard,shortage_probe)):
        raise ValueError('cooperative workflow requires explicit complete safe-probe stack')
    rule=v1.HeuristicAssignmentRule(time_limit_seconds=.05,max_moves=2048) if allocation=='H1' else \
        v1.StableCPSATAssignmentRule(time_limit_seconds=.05,seed=0,require_backend=True)
    policy=v1.Policy(rule=rule,k=3,wait=2,notify=True,name=code,observed_inquiry={})
    replay=CooperativeProbeReplay(deepcopy(scenario),policy,routing=routing,inquiry=inquiry,
        recovery=True,pending_protection=True,cohort_registration=True,scarce_routing=True,
        late_reply_recovery=True,late_pending_guard=True,shortage_probe=True,cooperative_probe=True)
    branch=replay.run_replay()
    branch.update(id=code,label=label or code,policy=v1._policy_spec(policy),
        scarce_delay_spec=dict(inquiry=inquiry,allocation=allocation,cohort_registration=True,scarce_routing=True,
            actual_action='parallel_scarce_supplement',original_selector_candidates_unchanged=True,
            maximum_supplements_per_patient=1,recovery=True,pending_protection=True,pending_window_minutes=1,
            time_step_seconds=15,actual_selector_reused=True,extra_resource_change_publications=False),
        deadline_recovery_spec=dict(late_reply_recovery=True,scope='one_initial_scarce_supplement',
            late_reply_window_minutes=v1.LATE_REPLY_WINDOW_MINUTES,original_timeout_unchanged=True,
            original_timeout_feedback_unchanged=True,blocking_wait_added=False,hospital_work_calendar_unchanged=False,
            patient_condition_fingerprint_required=True,new_offer_ttl_from_actual_receipt=True,actual_reply_outcome_required=True,
            current_fresh_resource_check_required=True,current_valid_offer_rejects=True,newer_same_hospital_attempt_rejects=True))
    return branch


DEFAULT_COOPERATIVE_PROBE_POLICIES=tuple(dict(deepcopy(p),cooperative_probe=False)
    for p in prior.DEFAULT_SHORTAGE_PROBE_POLICIES)+tuple(dict(id=f'v4_{a}_cooperative',label=f'v4_{a}_cooperative',
        inquiry='v4',allocation=a,cohort_registration=True,scarce_routing=True,late_reply_recovery=True,
        late_pending_guard=True,shortage_probe=True,cooperative_probe=True) for a in ('H1','C1'))


def run_cooperative_probe_replay(scenario,policies=None,execution_order=None,routing=None):
    v1.validate_joint_scenario(scenario)
    specs=deepcopy(list(DEFAULT_COOPERATIVE_PROBE_POLICIES if policies is None else policies));ids=[r['id'] for r in specs]
    if not ids or len(ids)!=len(set(ids)) or any(not prior._id(code) for code in ids):raise ValueError('unique policy IDs required')
    order=list(ids if execution_order is None else execution_order)
    if len(order)!=len(ids) or set(order)!=set(ids):raise ValueError('each policy must execute once')
    by_id={r['id']:r for r in specs};output={}
    keys={'id','inquiry','allocation','cohort_registration','scarce_routing','late_reply_recovery','late_pending_guard','shortage_probe','cooperative_probe'}
    for code in order:
        row=by_id[code]
        if not keys<=set(row) or set(row)-(keys|{'label'}):raise ValueError('exact cooperative-probe policy row required')
        output[code]=run_cooperative_probe_branch(scenario,**{k:row[k] for k in keys-{'id'}},
            routing=routing,branch_id=code,label=row.get('label',code))
    return dict(schema_version=1,experiment_id=EXPERIMENT_ID,scenario=deepcopy(scenario),
        branches=[output[code] for code in ids],execution_order=order)
