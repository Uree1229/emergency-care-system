"""Independent raw ledger audit of cooperative hospital review controls.

The prior eight reference branches remain frozen. New branches must first prove
actual job admission, single-worker work conservation, ordinary FIFO, optional
preemption and current control acknowledgements. Clinical observations, offers,
physical confirmation gates and assignment proofs still consume full raw data.
No replay engine or solver is imported, and records are never projected away.
"""
from __future__ import annotations
from collections import Counter
from copy import deepcopy
import math
from . import shortage_probe_audit as prior
from . import deadline_safe_audit as safe
from . import deadline_recovery_audit as late

base = prior.base
joint = prior.joint
scarce = prior.scarce
recovery = prior.recovery
information = prior.information
responsive = joint.responsive
_require = prior._require
_indexed_events = prior._indexed_events
_end = prior._end
_views = prior._views
_TruthLedger = joint._TruthLedger
_position = joint._position
_reply_environment = joint._reply_environment
_selector_reproduce = joint._selector_reproduce
CLOCK_ORIGIN_SECONDS = joint.CLOCK_ORIGIN_SECONDS
_finite = joint._finite
_early_snapshot = joint._early_snapshot
_early_expected = joint._early_expected
_condition = late._condition
_fingerprint = late._fingerprint
_late_expected = safe._late_expected
_is_callback_accept = late._is_callback_accept
_pending_guard_expected = joint._pending_guard_expected
_pending_rows = joint._pending_rows
_offer_rows = joint._offer_rows
_joint_reply_due = joint._joint_reply_due
_probe_expected = prior._probe_expected
_probe_review_due = prior._probe_review_due
_scarce_expected = scarce._scarce_expected
_check_reason = joint._check_reason
_difference = prior._difference
_audit_information_branch = prior._audit_information_branch
CooperativeProbeAuditError = prior.ShortageProbeAuditError
EXPERIMENT_ID = 'cooperative_probe_20261017'
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


class _CooperativeWorkflow(prior._ProbeWorkflow):
    """Control acknowledgements cannot create clinical offers or capacities."""
    def apply(self, event):
        if event['type'] == 'cooperative_probe_not_admitted':
            key=(event['patient'],event['hospital'])
            self.asks[key] -= 1
            _require(self.asks[key] >= 0,'nonadmission control acknowledgement lacks its actual request')
        elif event['type'] == 'cooperative_probe_nonblocking_ready':
            self.promoted.add((event['patient'],event['t']))
        super().apply(event)


_EarlyWorkflow = _CooperativeWorkflow
_LateWorkflow = _CooperativeWorkflow
_ProbeWorkflow = _CooperativeWorkflow


def _after_queue_events(indexed, sequence):
    row=indexed.get(sequence+1)
    while row is not None and (row['type'].startswith('cooperative_job_')
                              or row['type']=='cooperative_probe_control_ack'):
        row=indexed.get(row['event_sequence']+1)
    return row


def _work_reply_due(work, handle):
    delivery=work['deliveries'][handle['ask_event_sequence']]
    _require(delivery['admitted'],'nonadmitted control request was retained as clinical work')
    finish=delivery['finish']
    if delivery['silent'] or finish is None or finish>handle['timeout_at']:
        return handle['timeout_at'],True
    return finish,False


def _cooperative_expected(snapshot):
    base_snapshot=snapshot['base_snapshot']
    expected=prior._nonblocking_expected(base_snapshot)
    controls={(row['hospital'],row['attempt'],row['ask_event_sequence']):row
              for row in snapshot['control_acknowledgements']}
    if expected['eligible'] and any(controls[row['hospital'],row['attempt'],row['ask_event_sequence']]['state']!='completed'
        for row in base_snapshot['patient']['pending_inquiries']):
        return dict(eligible=False,options=[],reason='PROBE_WORK_PENDING')
    return expected


def _busy_expected(snapshot):
    current=snapshot['opportunity_snapshot'];patient=current['patient'];now=current['t']
    ack=snapshot['not_admitted_ack'];result=dict(send=[],reason='NO_RETRY')
    if patient['state']!='SEARCH' or patient['first_commit_t'] is not None or patient['inquiry_episode']!=0:
        return dict(result,reason='NOT_INITIAL_SEARCH')
    if snapshot['retry_count']>=1:return dict(result,reason='ALREADY_RETRIED')
    if not patient['pending_inquiries']:return dict(result,reason='NO_PENDING_INQUIRY')
    if snapshot['ordinary_send']:return dict(result,reason='NORMAL_SELECTOR_ALREADY_SENT')
    if patient['episode_contacts']>=10 or len(patient['pending_inquiries'])>=3 or snapshot['max_new']<1:
        return dict(result,reason='INQUIRY_BUDGET')
    hid=ack['hospital'];candidates={row['hospital']:row for row in current['candidates']}
    if hid not in candidates or hid not in snapshot['offered_hospitals']:
        return dict(result,reason='NOT_CURRENT_NORMAL_CANDIDATE')
    if hid in patient['tried_hospitals'] or any(row['hospital']==hid for row in patient['pending_inquiries']) \
        or any(row['hospital']==hid and row['expires_at']>now for row in patient['valid_offers']):
        return dict(result,reason='ALREADY_NORMAL_TRIED_OR_CLAIMED')
    def usable(row):
        return all(view['status']=='fresh' and view['free'] is not None and view['free']>=patient['demand'][resource]
                   for resource,view in row['resources'].items())
    if any(offer['expires_at']>now and offer['hospital'] in candidates and usable(candidates[offer['hospital']])
           for offer in patient['valid_offers']):return dict(result,reason='USABLE_OFFER')
    target=candidates[hid]
    if not usable(target):return dict(result,reason='RESOURCES_NOT_FRESH_SUFFICIENT')
    if any(view['received_at'] is None or view['received_at']<=ack['received_at'] for view in target['resources'].values()) \
        or not any(view['received_at']==now for view in target['resources'].values()):
        return dict(result,reason='NO_NEW_CURRENT_REPORT')
    for other in current['registered_search_patients']:
        if other['id']!=patient['id'] and (any(row['hospital']==hid and row['timeout_at']>now for row in other['pending_inquiries'])
            or any(row['hospital']==hid and row['expires_at']>now for row in other['valid_offers'])):
            return dict(result,reason='OTHER_CURRENT_SEARCH_CLAIM')
    return dict(send=[hid],reason='NONADMITTED_NEW_FRESH_REPORT_NORMAL_RETRY')


def audit_work(scenario, branch):
    """Check physical work online, independently of the queue implementation."""
    events = _indexed_events(branch)
    enabled = branch.get('cooperative_queue_enabled')
    _require(type(enabled) is bool and enabled == bool(scenario.get('inquiry_queue', {}).get('enabled')),
             'cooperative queue mode differs from the original environment')
    queue = scenario.get('inquiry_queue', {})
    workers = queue.get('workers', 1)
    _require(type(workers) is int and workers >= 1, 'invalid original worker count')
    asks = [row for row in events if row['type'] == 'ask']
    contact_attempts=Counter()
    for ask in asks:
        contact_attempts[ask['patient'],ask['hospital']]+=1
        _require(type(ask['attempt']) is int and ask['attempt']==contact_attempts[ask['patient'],ask['hospital']],
                 'actual contact attempt was reused, skipped or erased after a busy acknowledgement')
    by_job = {}
    for ask in asks:
        jid = ask.get('cooperative_job_id')
        if jid is not None:
            _require(type(jid) is int and jid>=0 and jid not in by_job, 'one physical job was attached to multiple inquiries or has a non-integer identity')
            by_job[jid] = ask
    if not enabled:
        _require(not branch.get('cooperative_job_ledger') and not branch.get('cooperative_control_acks')
            and not any(row['type'].startswith('cooperative_') for row in events)
            and not any(row.get('cooperative_probe_request') for row in asks),
            'worker-free environment gained a cooperative worker, job or acknowledgement')
        return dict(deliveries={},counts=dict(cooperative_jobs=0,cooperative_admission_refusals=0,
            cooperative_preemptions=0,cooperative_resumptions=0,cooperative_control_acks=0))
    hospitals = {row['id']: row for row in scenario['hospitals']}
    occupancy = {hid: [None] * workers for hid in hospitals}
    jobs, received_acks, pending_acks = {}, [], []
    first_starts, completions, last_time = Counter(), Counter(), 0
    expected_background = Counter((row['hospital'],row['t'],row['service_minutes'])
        for row in queue.get('background_jobs', []) if row['t']<=_end(scenario,branch))
    consumed_background, seen_asks, acked_asks, rejected, closed_controls = Counter(), set(), set(), set(), set()
    pauses = resumes = 0
    clinical_contacts=Counter()
    common = {'t','type','event_sequence','job_id','hospital','patient','attempt','clinical_attempt','kind','work_class'}

    def ordered_queue(hid):
        waiting = [job for job in jobs.values() if job['hospital'] == hid and job['state'] == 'queued']
        return sorted(waiting, key=lambda job: (job['work_class'] == 'probe',job['submitted_t'],job['submit_sequence']))

    def normal_waiting(hid):
        return any(job['hospital'] == hid and job['state'] == 'queued' and job['work_class'] == 'normal'
                   for job in jobs.values())

    def control(job, state, now):
        ask = by_job.get(job['job_id'])
        if job['work_class'] == 'probe' and ask and ask['event_sequence'] in seen_asks:
            pending_acks.append(dict(t=now,type='cooperative_probe_control_ack',hospital=job['hospital'],
                patient=job['patient'],attempt=job['attempt'],ask_event_sequence=ask['event_sequence'],
                job_id=job['job_id'],state=state,received_at=now))

    def advance(now):
        nonlocal last_time
        if now == last_time:
            return
        _require(now > last_time and not pending_acks, 'control acknowledgement omitted or delayed past its current change')
        for hid in hospitals:
            waiting = ordered_queue(hid)
            _require(not waiting or all(value is not None for value in occupancy[hid]),
                     'idle worker left actual submitted work unprocessed')
            _require(not normal_waiting(hid) or not any(jobs[value]['work_class'] == 'probe'
                for value in occupancy[hid] if value is not None), 'ordinary work waited while optional work continued')
        elapsed = now - last_time
        for hid in hospitals:
            for jid in occupancy[hid]:
                if jid is None:
                    continue
                job = jobs[jid]
                _require(job['remaining_minutes'] >= elapsed, 'actual work completion is missing or late')
                job['elapsed_minutes'] += elapsed
                job['remaining_minutes'] -= elapsed
        last_time = now

    for event in events:
        now,kind,seq = event['t'],event['type'],event['event_sequence']
        advance(now)
        if kind == 'cooperative_job_submitted':
            _require(set(event) == common | {'service_minutes','silent'}, 'job submission exposes unknown fields')
            jid,hid = event['job_id'],event['hospital']
            _require(type(jid) is int and jid >= 0 and jid not in jobs and hid in hospitals,
                     'duplicated or unknown physical job')
            _require(event['kind'] in ('inquiry','background') and event['work_class'] in ('normal','probe'),
                     'unknown physical work category')
            if event['kind'] == 'background':
                key=(hid,now,event['service_minutes']); consumed_background[key] += 1
                _require(consumed_background[key] <= expected_background[key] and event['patient'] is None
                    and event['attempt'] == 0 and event['clinical_attempt'] is None
                    and event['work_class'] == 'normal' and event['silent'] is False,
                    'background workload differs from the original environment')
            else:
                ask = by_job.get(jid)
                _require(ask is not None and ask['t'] == now and ask['event_sequence'] > seq
                    and ask['patient'] == event['patient'] and ask['hospital'] == hid
                    and ask['attempt'] == event['attempt'], 'physical job lacks its unique actual inquiry')
                clinical_contacts[event['patient'],hid] += 1
                clinical_attempt=clinical_contacts[event['patient'],hid]
                _require(type(event['clinical_attempt']) is int and type(ask['clinical_attempt']) is int
                    and event['clinical_attempt'] == clinical_attempt and ask['clinical_attempt'] == clinical_attempt,
                         'busy contact consumed clinical work, or clinical review ordinal changed')
                silent,service = _reply_environment(scenario,event['patient'],hid,clinical_attempt)
                _require(event['silent'] is silent and event['service_minutes'] == service,
                         'physical job changed the original clinical work or silence environment')
                _require((event['work_class'] == 'probe') == bool(ask.get('cooperative_probe_request')),
                         'optional or ordinary work class differs from the actual request')
            _require(type(event['service_minutes']) in (int,float) and event['service_minutes'] > 0
                and event['service_minutes'] * 4 == int(event['service_minutes'] * 4), 'work is not positive quarter-minute service')
            if event['work_class'] == 'probe':
                _require(any(value is None for value in occupancy[hid]) and not normal_waiting(hid),
                         'optional work was admitted while all workers or ordinary backlog were occupied')
            jobs[jid] = dict(job_id=jid,hospital=hid,patient=event['patient'],attempt=event['attempt'],
                clinical_attempt=event['clinical_attempt'],
                kind=event['kind'],work_class=event['work_class'],submitted_t=now,
                service_minutes=event['service_minutes'],silent=event['silent'],state='queued',
                elapsed_minutes=0,remaining_minutes=event['service_minutes'],start_t=None,finish_t=None,
                segments=[],submit_sequence=seq,worker=None)
        elif kind in ('cooperative_job_started','cooperative_job_resumed','cooperative_job_paused','cooperative_job_completed'):
            _require(set(event) == common | {'worker','elapsed_minutes','remaining_minutes'},
                     'physical transition carries unknown fields')
            jid,hid,worker = event['job_id'],event['hospital'],event['worker']
            _require(jid in jobs and type(worker) is int and 0 <= worker < workers, 'transition has unknown job or extra worker')
            job = jobs[jid]
            _require(all(event[key] == job[key] for key in ('hospital','patient','attempt','clinical_attempt','kind','work_class')),
                     'physical transition changed job identity or priority')
            _require(event['elapsed_minutes'] == job['elapsed_minutes']
                and event['remaining_minutes'] == job['remaining_minutes'], 'elapsed or remaining work was created or discarded')
            if kind in ('cooperative_job_started','cooperative_job_resumed'):
                available=[index for index,value in enumerate(occupancy[hid]) if value is None]
                waiting=ordered_queue(hid)
                _require(job['state'] == 'queued' and available and worker == min(available)
                    and waiting and waiting[0]['job_id'] == jid, 'dispatch skipped ordinary FIFO or older paused optional work')
                _require((kind == 'cooperative_job_started') == (job['start_t'] is None),
                         'first start was repeated or resume replaced an original start')
                if kind == 'cooperative_job_started':
                    job['start_t']=now;first_starts[now,hid,job['patient'],job['attempt'],job['kind']]+=1
                else: resumes += 1
                occupancy[hid][worker]=jid;job['worker']=worker;job['state']='running'
                job['segments'].append(dict(start_t=now,end_t=None,worker=worker));control(job,'running',now)
            else:
                _require(job['state'] == 'running' and occupancy[hid][worker] == jid
                    and job['worker'] == worker, 'transition terminated absent or another worker\'s work')
                if kind == 'cooperative_job_paused':
                    eligible=[index for index,value in enumerate(occupancy[hid])
                        if value is not None and jobs[value]['work_class'] == 'probe']
                    _require(job['work_class'] == 'probe' and job['remaining_minutes'] > 0
                        and normal_waiting(hid) and worker == min(eligible), 'ordinary work was preempted or optional pause lacked a real ordinary request')
                    job['state']='queued';pauses += 1;control(job,'queued',now)
                else:
                    _require(job['remaining_minutes'] == 0 and job['elapsed_minutes'] == job['service_minutes'],
                             'work completed before its full unchanged clinical service')
                    job['state']='completed';job['finish_t']=now
                    completions[now,hid,job['patient'],job['attempt'],job['kind']]+=1;control(job,'completed',now)
                job['segments'][-1]['end_t']=now;job['worker']=None;occupancy[hid][worker]=None
        elif kind == 'ask':
            _require(seq not in seen_asks,'actual inquiry appeared twice');seen_asks.add(seq)
            jid=event.get('cooperative_job_id');is_probe=bool(event.get('cooperative_probe_request'))
            _require(type(event.get('cooperative_probe_request',False)) is bool,
                     'probe request kind is not explicit boolean')
            _require('cooperative_job_id' in event,'actual inquiry omitted its physical admission identity')
            if jid is None:
                _require(event.get('clinical_attempt') is None, 'nonadmitted contact consumed a clinical review ordinal')
                _require(is_probe and (not any(value is None for value in occupancy[event['hospital']])
                    or normal_waiting(event['hospital'])), 'optional request was falsely refused despite an idle worker')
                rejected.add(seq)
                pending_acks.append(dict(t=now,type='cooperative_probe_control_ack',hospital=event['hospital'],
                    patient=event['patient'],attempt=event['attempt'],ask_event_sequence=seq,
                    job_id=None,state='not_admitted',received_at=now))
            else:
                _require(jid in jobs and jobs[jid]['kind'] == 'inquiry' and by_job[jid] is event,
                         'actual inquiry references missing or another physical job')
                if is_probe:
                    job=jobs[jid];control(job,job['state'],now)
        elif kind == 'cooperative_probe_control_ack':
            _require(event.get('job_id') is None or type(event.get('job_id')) is int,
                     'control acknowledgement has a non-integer job identity')
            expected=pending_acks.pop(0) if pending_acks else None
            _require(expected is not None and base._same(event,dict(expected,event_sequence=seq)),
                     'control acknowledgement is orphan, future-bearing or differs from actual current job state')
            _require(event['ask_event_sequence'] in seen_asks and event['ask_event_sequence'] < seq,
                     'control acknowledgement precedes the actual original request')
            acked_asks.add(event['ask_event_sequence']);received_acks.append(deepcopy(event))
            if event['state'] == 'not_admitted':
                following=events[seq+1] if seq+1<len(events) else None
                source=next(row for row in asks if row['event_sequence']==event['ask_event_sequence'])
                _require(following is not None and base._same(following,dict(t=now,
                    type='cooperative_probe_not_admitted',event_sequence=seq+1,patient=source['patient'],
                    hospital=source['hospital'],attempt=source['attempt'],ask_event_sequence=source['event_sequence'],
                    original_timeout_at=source['timeout_at'])), 'nonadmission did not explicitly close only its original control handle')
                closed_controls.add(following['event_sequence'])
    advance(_end(scenario,branch))
    _require(not pending_acks and consumed_background == expected_background,
             'missing current control acknowledgement or original background work')
    _require(closed_controls=={row['event_sequence'] for row in events if row['type']=='cooperative_probe_not_admitted'},
             'orphan or fabricated nonadmission handle closure')
    _require(all(job['state']=='completed' or job['remaining_minutes']>0 for job in jobs.values()),
             'completed physical work omitted its mandatory completion event')
    for hid in hospitals:
        _require(not ordered_queue(hid) or all(value is not None for value in occupancy[hid]),
                 'last actual submission left an idle available worker')
        _require(not normal_waiting(hid) or not any(jobs[value]['work_class']=='probe'
            for value in occupancy[hid] if value is not None), 'ordinary work was left behind optional work at the observation boundary')
    _require(set(by_job) == {jid for jid,job in jobs.items() if job['kind'] == 'inquiry'},
             'orphan actual inquiry or fabricated physical job')
    _require(acked_asks == {row['event_sequence'] for row in asks if row.get('cooperative_probe_request')},
             'optional request lacks actual current admission control acknowledgement')
    exported=[]
    for job in jobs.values():
        exported.append({key:deepcopy(value) for key,value in job.items() if key not in ('submit_sequence','worker')})
    _require(base._same(branch['cooperative_job_ledger'],exported), 'private physical ledger differs from independently reconstructed actual work')
    _require(base._same(branch['cooperative_control_acks'],received_acks), 'control ACK export differs from original raw events')
    actual_starts=Counter((row['t'],row['hospital'],row.get('patient'),row.get('attempt'),row.get('kind'))
        for row in events if row['type'] == 'review_started')
    actual_completions=Counter((row['t'],row['hospital'],row.get('patient'),row.get('attempt'),row.get('kind'))
        for row in events if row['type'] == 'review_completed')
    _require(actual_starts == first_starts and actual_completions == completions,
             'original clinical review start/completion markers were repeated, removed or moved')
    deliveries={}
    for ask in asks:
        jid=ask['cooperative_job_id']
        if jid is None: deliveries[ask['event_sequence']]=dict(finish=None,silent=None,admitted=False)
        else:
            job=jobs[jid];deliveries[ask['event_sequence']]=dict(finish=job['finish_t'],silent=job['silent'],admitted=True)
    rows=[];end=_end(scenario,branch)
    for hid in hospitals:
        local=[job for job in jobs.values() if job['hospital'] == hid]
        rows.append(dict(hospital=hid,workers=workers,waiting=sum(job['state']=='queued' for job in local),
            processing=sum(job['state']=='running' for job in local),completed=sum(job['state']=='completed' for job in local),
            submitted=len(local),queue_wait_total_minutes=sum((job['finish_t'] if job['finish_t'] is not None else end)
                -job['submitted_t']-job['elapsed_minutes'] for job in local),
            service_total_minutes=sum(job['elapsed_minutes'] for job in local)))
    aggregate={key:sum(row[key] for row in rows) for key in rows[0] if key!='hospital'}
    _require(base._same(branch['inquiry_queue_report'],dict(end_t=end,aggregate=aggregate,by_hospital=rows)),
             'actual queue aggregate differs from conserved elapsed work and waiting')
    frames=[]
    for step in range(int(end*4)+1):
        now=step/4;current_rows=[]
        for hid in hospitals:
            local=[job for job in jobs.values() if job['hospital']==hid and job['submitted_t']<=now]
            waiting=processing=completed=0;service=wait=0
            for job in local:
                performed=sum(max(0,min(now,segment['end_t'] if segment['end_t'] is not None else end)-segment['start_t'])
                    for segment in job['segments'])
                done=job['finish_t'] is not None and job['finish_t']<=now
                active=not done and any(segment['start_t']<=now and (segment['end_t'] is None or now<segment['end_t'])
                    for segment in job['segments'])
                completed+=done;processing+=active;waiting+=not done and not active
                service+=performed
                wait+=(min(now,job['finish_t']) if job['finish_t'] is not None else now)-job['submitted_t']-performed
            current_rows.append(dict(hospital=hid,workers=workers,waiting=waiting,processing=processing,
                completed=completed,submitted=len(local),queue_wait_total_minutes=wait,service_total_minutes=service))
        if not frames or current_rows!=frames[-1]['hospitals']:frames.append(dict(t=now,hospitals=current_rows))
    _require(base._same(branch['inquiry_snapshots'],frames),
             'current queue snapshots hide, invent or move elapsed waiting or actual work')
    clinical=[job for job in jobs.values() if job['kind']=='inquiry']
    clinical_wait=sum((job['finish_t'] if job['finish_t'] is not None else end)-job['submitted_t']-job['elapsed_minutes'] for job in clinical)
    expected_metrics=dict(inquiry_queue_wait_mean=clinical_wait/len(clinical) if clinical else None,
        inquiry_service_minutes=sum(job['elapsed_minutes'] for job in clinical),
        inquiry_unfinished_jobs=sum(job['state']!='completed' for job in clinical),
        inquiry_peak_waiting=max((sum(row['waiting'] for row in frame['hospitals']) for frame in frames),default=0))
    for key,value in expected_metrics.items():
        _require(base._same(branch['metrics'][key],value),'queue metric differs from independently reconstructed actual work: '+key)
    return dict(deliveries=deliveries,counts=dict(cooperative_jobs=len(jobs),cooperative_admission_refusals=len(rejected),
        cooperative_preemptions=pauses,cooperative_resumptions=resumes,cooperative_control_acks=len(received_acks),
        cooperative_queue_snapshots=len(frames)))

def audit_guard(scenario, branch):
    """Check every protection snapshot, lifetime clock, hold and model subset."""
    enabled = branch.get('protection_enabled')
    grace = branch.get('grace_minutes')
    _require((enabled and type(grace) is int and grace == 3)
             or (enabled is False and grace is None), 'invalid branch protection grace')
    _require(type(enabled) is bool, 'missing explicit protection mode')
    records = branch['protection_records']
    _require(isinstance(records, list), 'invalid protection record list')
    ordered = information._indexed_events(branch)
    by_sequence = {event['event_sequence']: event for event in ordered}
    guard_events = [event for event in ordered if event['type'] == 'opportunity_guard']
    hold_events = [event for event in ordered if event['type'] == 'opportunity_hold']
    if not enabled:
        _require(not records and not guard_events and not hold_events,
                 'unprotected baseline contains opportunity intervention')
        _require(branch['metrics']['protection_wall_seconds'] == 0,
                 'unprotected baseline reports opportunity runtime')
        for key, value in responsive._branch_guard_counts(branch).items():
            _require(branch['metrics'][key] == value, 'raw protection metric mismatch: ' + key)
        return dict(opportunity_evaluations=0, opportunity_hold_checks=0,
                    opportunity_held_patients=0)
    _require(branch['information_mode'] == 'observed' and branch['policy']['rule'] in ('H1', 'C1'),
             'protection applies outside observed H1/C1')
    information._number(branch['metrics']['protection_wall_seconds'], 'protection runtime')
    workflow = _EarlyWorkflow(scenario)
    used_guards, used_holds, used_batches = set(), set(), set()
    eligible_search_checks, covered_ready_checks = {}, set()
    decisions = {row['event_sequence']: row for row in branch['allocation_decisions']}
    for event in ordered:
        if event['type'] == 'information_check' and event['purpose'] == 'allocation' \
                and event['eligible'] and workflow.states.get(event['patient']) == 'SEARCH':
            eligible_search_checks[event['event_sequence']] = event
        if event['type'] == 'opportunity_guard':
            seq, at = event['event_sequence'], event['t']
            index = base._integer(event['record_index'], 'invalid opportunity record index')
            _require(index == len(used_guards) and index < len(records),
                     'missing, duplicated or reordered protection record')
            used_guards.add(index)
            record = records[index]
            expected_snapshot = workflow.snapshot(at)
            _require(expected_snapshot['ready'], 'opportunity guard without current ready patients')
            _require(base._same(record['snapshot'], expected_snapshot),
                     'protection snapshot differs from earlier observed workflow/reports')
            ready_ids = {row['id'] for row in expected_snapshot['ready']}
            covered_ready_checks.update(check_sequence for check_sequence, check in eligible_search_checks.items()
                                        if check['t'] == at and check['patient'] in ready_ids)
            _require(record['grace_minutes'] == grace and record['event_sequence'] == seq,
                     'protection grace/record event differs')
            _require(record['waiting_since'] == workflow.waiting_since,
                     'waiting lifetime clock differs from first observed information block')
            _require(record['held_since'] == workflow.held_since,
                     'held lifetime clock was reset or differs from first actual hold')
            expected = responsive._expected_guard(expected_snapshot, workflow.waiting_since,
                                       deepcopy(workflow.held_since), grace_minutes=grace,
                                       abandon_incomplete=branch.get('prefetch_enabled', False))
            _require(base._same(record['result'], expected),
                     'protection decision differs from bounded independent present-information guard')
            workflow.held_since = deepcopy(expected['held_since_after'])
            next_sequence = seq + 1
            for hold in expected['holds']:
                raw = by_sequence.get(next_sequence)
                _require(raw is not None and raw['type'] == 'opportunity_hold'
                         and raw['t'] == at and base._same(
                             {key: value for key, value in raw.items()
                              if key not in ('t', 'type', 'event_sequence')}, hold),
                         'protection hold lacks unique immediate raw event')
                _require(next_sequence not in used_holds, 'protection hold event reused')
                used_holds.add(next_sequence)
                next_sequence += 1
            ready_after = list(expected['ready_after'])
            if ready_after and branch.get('pending_protection_enabled'):
                pending_marker = by_sequence.get(next_sequence)
                _require(pending_marker is not None and pending_marker['type'] == 'pending_opportunity_guard'
                         and pending_marker['t'] == at, 'remaining ready set lacks pending guard')
                pending_record = branch['pending_guard_records'][pending_marker['record_index']]
                _require([row['id'] for row in pending_record['snapshot']['ready']] == ready_after,
                         'pending guard changed original guard ready set')
                ready_after = pending_record['result']['ready_after']
                next_sequence = next((s for s, raw in by_sequence.items() if s > next_sequence
                    and raw['t'] == at and raw['type'] == 'allocation_proposal'), None)
            if ready_after:
                marker = by_sequence.get(next_sequence)
                decision = decisions.get(next_sequence)
                _require(marker is not None and marker['type'] == 'allocation_proposal'
                         and marker['t'] == at and decision is not None and decision['kind'] == 'batch',
                         'remaining protected ready patients lack immediate ordinary batch')
                expected_rows = [row for row in expected_snapshot['ready']
                                 if row['id'] in ready_after]
                model_rows = decision['inputs']['patients']
                _require([row['id'] for row in model_rows] == ready_after
                         and all(row['options'] == expected_row['options']
                                 for row, expected_row in zip(model_rows, expected_rows)),
                         'ordinary batch differs from protected ready subset/current options')
                used_batches.add(next_sequence)
            else:
                following = by_sequence.get(next_sequence)
                _require(following is None or following['type'] != 'allocation_proposal'
                         or following['t'] != at, 'fully held batch still entered allocation model')
        workflow.apply(event)
    _require(used_guards == set(range(len(records))), 'orphan protection record')
    _require(used_holds == {event['event_sequence'] for event in hold_events}, 'orphan protection hold event')
    _require(used_batches == {row['event_sequence'] for row in branch['allocation_decisions']
                              if row['kind'] == 'batch'}, 'protected batch lacks guard evaluation')
    _require(covered_ready_checks == set(eligible_search_checks),
             'eligible searching patient was silently dropped without guard evaluation')
    for key, value in responsive._branch_guard_counts(branch).items():
        _require(branch['metrics'][key] == value, 'raw protection metric mismatch: ' + key)
    return dict(opportunity_evaluations=len(used_guards),
        opportunity_hold_checks=len(used_holds),
        opportunity_held_patients=len({event['patient'] for event in hold_events}))


def audit_early_offers(scenario, branch):
    """Check every detached early-review input, decision and resulting check."""
    enabled = branch['early_offers_enabled']
    _require(type(enabled) is bool, 'missing explicit early-offer mode')
    records = branch['early_offer_records']
    _require(isinstance(records, list), 'invalid early-offer records')
    workflow = _EarlyWorkflow(scenario)
    events = information._indexed_events(branch)
    indexed = {event['event_sequence']: event for event in events}
    expected, cycles, used = [], [], set()
    successful = 0
    for event in events:
        kind, at, seq = event['type'], event['t'], event['event_sequence']
        if kind == 'early_offer_cycle':
            _require(enabled, 'early-offer cycle on disabled branch')
            _require(not expected, 'previous early cycle omitted eligible review target')
            _require(set(event) == {'t', 'type', 'event_sequence'},
                     'early-offer cycle carries unknown policy fields')
            cycles.append(at)
            normal_ready = {row['id'] for row in workflow.snapshot(at)['ready']}
            for pid in sorted(workflow.specs, key=lambda patient: (
                    -workflow.specs[patient].get('priority', 4),
                    workflow.specs[patient].get('search_start_t', 0), patient)):
                if workflow.states.get(pid) != 'SEARCH' or pid in normal_ready:
                    continue
                pending = sum(count for (patient, _), count in workflow.asks.items() if patient == pid)
                valid = any(patient == pid and expiry > at for (patient, _), expiry in workflow.offers.items())
                first = workflow.first_valid.get(pid)
                if pending and valid and first is not None and at - first < 2:
                    expected.append(pid)
        elif kind == 'early_offer_review':
            _require(enabled and expected, 'unexpected early-offer review target')
            index = base._integer(event['record_index'], 'invalid early-offer record index')
            _require(index == len(used) and index < len(records),
                     'missing, duplicated or reordered early-offer record')
            used.add(index)
            record = records[index]
            pid = expected.pop(0)
            _require(set(event) == {'t', 'type', 'event_sequence', 'record_index'},
                     'early-offer marker carries unknown or future policy fields')
            _require(record['event_sequence'] == seq, 'early-offer record event sequence differs')
            _require(base._same(record['snapshot'], _early_snapshot(workflow, pid, at)),
                     'early-offer snapshot differs from earlier delivered evidence or current cohort')
            result = _early_expected(record['snapshot'])
            _require(base._same(record['result'], result),
                     'early-offer decision differs from independent current-information criterion')
            _finite(record['elapsed_seconds'], 'early-offer evaluation runtime')
            _require(set(record) == {'snapshot', 'result', 'event_sequence', 'elapsed_seconds'},
                     'early-offer record carries unknown policy fields')
            if result['eligible']:
                successful += 1
                following = indexed.get(seq + 1)
                _require(following is not None and following['type'] == 'early_offer_ready'
                         and following['t'] == at and following['patient'] == pid
                         and following['hospital'] == result['hospital']
                         and following['review_event_sequence'] == seq,
                         'eligible early offer lacks immediate ready marker')
                _require(set(following) == {'t', 'type', 'event_sequence', 'patient', 'hospital',
                                           'review_event_sequence'},
                         'early-ready marker carries unknown or future fields')
        elif kind == 'early_offer_ready':
            previous = indexed.get(seq - 1)
            _require(previous is not None and previous['type'] == 'early_offer_review'
                     and previous['event_sequence'] == event['review_event_sequence']
                     and records[previous['record_index']]['result']['eligible'],
                     'early-ready marker lacks immediately preceding positive review')
            row = workflow._base_patient(event['patient'])
            options = [hid for (patient, hid), expiry in workflow.offers.items()
                if patient == event['patient'] and expiry > at and information._check_reason(
                    information._views('observed', workflow.observations, workflow.truth,
                                       hid, row['demand'], at), row['demand']) is None]
            for offset, hid in enumerate(options, 1):
                check = indexed.get(seq + offset)
                _require(check is not None and check['type'] == 'information_check'
                         and check['t'] == at and check['patient'] == event['patient']
                         and check['hospital'] == hid and check['purpose'] == 'allocation'
                         and check['eligible'] is True and check['reason'] is None,
                         'early-ready offer lacks immediate current allocation checks for every eligible offer')
        workflow.apply(event)
    _require(not expected, 'last early cycle omitted eligible review target')
    _require(used == set(range(len(records))), 'orphan early-offer record')
    expected_cycles = [i/4 for i in range(scenario.get('max_minutes', 180)*4+1)] if enabled else []
    _require(cycles == expected_cycles, 'missing, duplicated or extra early-offer cycle')
    return dict(early_offer_cycles=len(cycles), early_offer_reviews=len(records),
                early_offer_ready=successful)


def audit_pending_guard(scenario, branch):
    enabled = branch.get('pending_protection_enabled')
    _require(type(enabled) is bool, 'missing explicit pending guard mode')
    records = branch['pending_guard_records']
    events = _indexed_events(branch)
    indexed = {event['event_sequence']: event for event in events}
    markers = [event for event in events if event['type'] == 'pending_opportunity_guard']
    raw_holds = [event for event in events if event['type'] == 'pending_opportunity_hold']
    if not enabled:
        _require(not records and not markers and not raw_holds, 'disabled pending guard contains interventions')
        return dict(pending_guard_reviews=0, pending_guard_holds=0)
    workflow = _EarlyWorkflow(scenario)
    handles, first, used, hold_sequences = {}, {}, set(), set()
    ready = []
    specs = {row['id']: row for row in scenario['patients']}
    hospitals = {row['id']: row for row in scenario['hospitals']}

    def patient_view(pid):
        row = workflow._base_patient(pid)
        row['compatible_hospitals'] = sorted(hid for hid, hospital in hospitals.items()
            if set(row['required_capabilities']) <= set(hospital.get('capabilities', []))
            and all(hospital.get('capacity', {}).get(resource, 0) >= quantity
                    for resource, quantity in row['demand'].items()))
        return row

    for event in events:
        kind, at, pid, hid, seq = event['type'], event['t'], event.get('patient'), event.get('hospital'), event['event_sequence']
        if kind == 'ask':
            handles[pid, hid] = dict(hospital=hid, attempt=event['attempt'], submitted_at=at,
                timeout_at=event['timeout_at'], ask_event_sequence=seq)
        elif kind == 'cooperative_probe_not_admitted':
            handles.pop((pid, hid), None)
        elif kind in ('accept', 'decline', 'no_response'):
            handles.pop((pid, hid), None)
        elif kind in ('commit', 'arrive', 'transfer_search', 'treatment_start', 'backup_closed'):
            handles = {key: row for key, row in handles.items() if key[0] != pid}
        elif kind in ('backup_retained', 'backup_arrival_preserved', 'backup_transfer_reused'):
            handles = {key: row for key, row in handles.items() if key[0] != pid}
            for row in event['pending']:
                handles[pid, row['hospital']] = deepcopy(row)
        elif kind == 'opportunity_guard':
            original = branch['protection_records'][event['record_index']]
            by_id = {row['id']: row for row in original['snapshot']['ready']}
            ready = []
            for target in original['result']['ready_after']:
                row = patient_view(target)
                row.update(options=by_id[target]['options'], valid_offer_expiries=by_id[target]['valid_offer_expiries'])
                alternatives = {hid: _views('observed', workflow.observations, workflow.truth,
                    hid, row['demand'], at) for hid in row['compatible_hospitals']}
                row.update(alternative_resource_views=alternatives,
                    eligible_alternative_hospitals=[hid for hid, views in alternatives.items()
                        if _check_reason(views, row['demand']) != 'FRESH_SHORTAGE'])
                ready.append(row)
        elif kind == 'pending_opportunity_guard':
            index = event['record_index']
            _require(index == len(used) and index < len(records), 'missing/reordered pending guard record')
            used.add(index)
            record = records[index]
            _require(set(record) == {'snapshot', 'result', 'event_sequence'} and record['event_sequence'] == seq,
                     'pending guard record has unknown fields or wrong emission sequence')
            _require(ready, 'pending guard has no ready patients after original guard')
            waiting = []
            ready_ids = {row['id'] for row in ready}
            for target in sorted(specs):
                if workflow.states.get(target) != 'SEARCH' or target in ready_ids:
                    continue
                row = patient_view(target)
                if len(row['compatible_hospitals']) != 1:
                    continue
                only = row['compatible_hospitals'][0]
                requests = [value for (patient, hospital), value in handles.items() if patient == target and hospital == only]
                if not requests:
                    continue
                views = _views('observed', workflow.observations, workflow.truth, only, row['demand'], at)
                row.update(pending_inquiries=deepcopy(requests), resource_views=views,
                           bed_view=views.get('ed_bed'))
                waiting.append(row)
            snapshot = dict(t=at, ready=deepcopy(ready), waiting=waiting, held_since=deepcopy(first))
            _require(base._same(record['snapshot'], snapshot),
                     'pending guard input differs from original raw inquiries, received beds or ready set')
            expected = _pending_guard_expected(snapshot)
            _require(base._same(record['result'], expected), 'pending guard renewed lifetime or changed resource/priority rule')
            first = deepcopy(expected['held_since_after'])
            for offset, hold in enumerate(expected['holds'], 1):
                raw = indexed.get(seq + offset)
                _require(raw is not None and raw['type'] == 'pending_opportunity_hold' and raw['t'] == at
                         and base._same({k: v for k, v in raw.items() if k not in ('t', 'type', 'event_sequence')}, hold),
                         'pending guard hold differs from bounded independent result')
                hold_sequences.add(seq + offset)
            ready = []
        workflow.apply(event)
    _require(used == set(range(len(records))) and hold_sequences == {event['event_sequence'] for event in raw_holds},
             'orphan pending guard or pending hold')
    _require(branch['metrics']['pending_guard_reviews'] == len(records)
             and branch['metrics']['pending_guard_holds'] == len(raw_holds), 'pending guard metrics differ from raw events')
    return dict(pending_guard_reviews=len(records), pending_guard_holds=len(raw_holds))


def audit_supplements(scenario, branch):
    """Rebuild additional inquiries without changing the original selector pool."""
    enabled = branch.get('scarce_routing_enabled', False)
    _require(type(enabled) is bool, 'invalid scarce supplement mode')
    records = branch.get('scarce_supplement_records', [])
    events = _indexed_events(branch)
    markers = [event for event in events if event['type'] == 'scarce_supplement_review']
    _require(not any(event['type'] == 'scarce_candidate_review' for event in events)
             and not branch.get('scarce_candidate_records'), 'candidate exclusion is not the supplemental intervention')
    if not enabled:
        _require(not records and not markers, 'baseline contains scarce supplemental intervention')
        return dict(scarce_supplement_reviews=0, scarce_supplement_sent=0)
    _require(branch.get('cohort_registration_enabled') is True, 'scarce supplement lacks cohort registration')
    workflow = _EarlyWorkflow(deepcopy(scenario))
    hospitals = {row['id']: row for row in scenario['hospitals']}
    requests, counts, handles, checks, used, reviewed = {}, Counter(), {}, [], set(), set()
    selectors = {row['event_sequence']: row for row in branch['inquiry_decisions']}
    indexed = {event['event_sequence']: event for event in events}
    contacts, episodes = Counter(), {}
    sent = 0

    def patient_view(pid):
        _require(pid in requests and requests[pid]['event_sequence'] < current_sequence,
                 'supplement input used unregistered or future patient')
        source = workflow._base_patient(pid)
        compatible = sorted(hid for hid in source['compatible_hospitals'] if all(
            hospitals[hid].get('capacity', {}).get(resource, 0) >= amount
            for resource, amount in source['demand'].items()))
        return dict(id=pid, priority=source['priority'], request_t=source['request_t'],
            demand=source['demand'], required_capabilities=source['required_capabilities'],
            state=workflow.states[pid], request_event_sequence=requests[pid]['event_sequence'],
            compatible_hospitals=compatible, inquiry_count=counts[pid],
            pending_inquiries=[deepcopy(value) for (patient, _), value in handles.items() if patient == pid])

    for event in events:
        kind, at, current_sequence = event['type'], event['t'], event['event_sequence']
        pid, hid = event.get('patient'), event.get('hospital')
        if kind == 'request':
            _require(pid not in requests, 'duplicate registered supplement patient')
            requests[pid] = event
        elif kind == 'ask':
            counts[pid] += 1
            episodes[pid] = event['inquiry_episode']
            contacts[pid, event['inquiry_episode']] += 1
            handles[pid, hid] = dict(hospital=hid, attempt=event['attempt'], submitted_at=at,
                timeout_at=event['timeout_at'], ask_event_sequence=current_sequence)
        elif kind == 'cooperative_probe_not_admitted':
            handles.pop((pid, hid), None)
        elif kind in ('accept', 'decline', 'no_response'):
            handles.pop((pid, hid), None)
        elif kind in ('commit', 'arrive', 'transfer_search', 'treatment_start', 'backup_closed'):
            handles = {key: value for key, value in handles.items() if key[0] != pid}
        elif kind in ('backup_retained', 'backup_arrival_preserved', 'backup_transfer_reused'):
            handles = {key: value for key, value in handles.items() if key[0] != pid}
            for row in event['pending']:
                handles[pid, row['hospital']] = deepcopy(row)
        elif kind == 'information_check' and event['purpose'] == 'inquiry':
            checks.append(event)
        elif kind == 'scarce_supplement_review':
            index = event['record_index']
            _require(index == len(used) and index < len(records) and pid not in reviewed,
                     'missing, duplicated or reordered scarce supplement review')
            used.add(index); reviewed.add(pid)
            record = records[index]
            _require(set(event) == {'t', 'type', 'event_sequence', 'patient', 'record_index'}
                     and set(record) == {'inputs', 'result', 'event_sequence'}
                     and record['event_sequence'] == current_sequence, 'supplement record/marker has unknown fields')
            _require(pid in requests and workflow.states.get(pid) == 'SEARCH'
                     and at == requests[pid]['t'] and counts[pid] == 1,
                     'supplement did not follow the first currently registered same-tick inquiry')
            patient = patient_view(pid)
            _require(len(patient['pending_inquiries']) == 1, 'original inquiry not retained during supplementation')
            original = patient['pending_inquiries'][0]
            original_sequence = original['ask_event_sequence']
            ask = indexed[original_sequence]
            _require(ask['type'] == 'ask' and ask['patient'] == pid and ask['t'] == at
                     and original['submitted_at'] == at and original_sequence < current_sequence,
                     'supplement lacks an actual preceding first same-tick inquiry')
            selector_sequence = record['inputs']['original_selector_event_sequence']
            _require(selector_sequence in selectors and selector_sequence < original_sequence,
                     'supplement references missing or future original selector')
            selector = selectors[selector_sequence]
            _require(selector['patient'] == pid and selector['t'] == at
                     and selector['decision']['send'] == [original['hospital']],
                     'supplement rewrote or substituted original selector action')
            position = _position(scenario, branch, pid, at)
            structural_alternatives = [target for target in patient['compatible_hospitals']
                if target != original['hospital']]
            structural_alternatives.sort(key=lambda target: (
                recovery._synthetic_minutes(position, hospitals[target]), target))
            current_checks = [row for row in checks if row['patient'] == pid and row['t'] == at
                              and row['event_sequence'] > original_sequence]
            _require([row['hospital'] for row in current_checks] ==
                     [original['hospital'], *structural_alternatives],
                     'supplement candidate checks omitted, duplicated or reordered structural alternatives')
            eligible = [row['hospital'] for row in current_checks[1:] if row['eligible']]
            cohort = []
            for target in sorted(requests):
                if workflow.states.get(target) != 'SEARCH':
                    continue
                row = patient_view(target)
                row['resource_views'] = {only: information._views('observed', workflow.observations,
                    workflow.truth, only, row['demand'], at) for only in row['compatible_hospitals']} \
                    if len(row['compatible_hospitals']) == 1 else {}
                cohort.append(row)
            candidates = [dict(hospital=target,
                travel_minutes=recovery._synthetic_minutes(position, hospitals[target]),
                resources=information._views('observed', workflow.observations, workflow.truth,
                                             target, patient['demand'], at))
                for target in [original['hospital'], *eligible]]
            snapshot = dict(t=at, context='initial_supplement', patient=patient, candidates=candidates,
                            registered_search_patients=cohort)
            open_count = len(patient['pending_inquiries'])
            episode_count = contacts[pid, episodes[pid]]
            inputs = dict(opportunity_snapshot=snapshot, original_ask_event_sequence=original_sequence,
                original_selector_event_sequence=selector_sequence, eligible_new_hospitals=eligible,
                max_new=max(0, min(3-open_count, 10-episode_count)),
                episode_contacts=episode_count, open_inquiries=open_count)
            _require(base._same(record['inputs'], inputs),
                     'supplement input differs from prior registration, original requests or received resources')
            opportunity = _scarce_expected(snapshot)
            safe = [target for target in opportunity['offered_after'] if target in eligible]
            change = (inputs['max_new'] > 0 and opportunity['reason'] == 'SCARCE_ALTERNATIVE'
                and original['hospital'] in {row['hospital'] for row in opportunity['blocked']} and bool(safe))
            chosen = min(safe, key=lambda target: (
                recovery._synthetic_minutes(position, hospitals[target]), target)) if change else None
            expected = dict(opportunity_result=opportunity, send=[] if chosen is None else [chosen],
                            reason='NO_ACTION' if chosen is None else 'INITIAL_SCARCE_SUPPLEMENT')
            _require(base._same(record['result'], expected),
                     'supplement differs from independent fresh alternative, priority, deficit or deadline criterion')
            following = _after_queue_events(indexed, current_sequence)
            if chosen is not None:
                _require(following is not None and following['type'] == 'ask'
                    and following['patient'] == pid and following['hospital'] == chosen and following['t'] == at,
                    'selected supplement lacks immediate actual new inquiry')
                sent += 1
            else:
                _require(following is None or following['type'] != 'ask' or following.get('patient') != pid,
                         'no-action supplement generated an inquiry')
        workflow.apply(event)
    _require(used == set(range(len(records))) and len(records) == len(markers),
             'orphan scarce supplement record or marker')
    first_asks = {}
    for event in events:
        if event['type'] == 'ask':
            first_asks.setdefault(event['patient'], event)
    eligible_reviews = {pid for pid, event in first_asks.items()
        if event['t'] == requests[pid]['t'] and any(row['patient'] == pid and row['t'] == event['t']
            and row['event_sequence'] < event['event_sequence'] and row['decision']['send'] == [event['hospital']]
            for row in selectors.values())}
    _require(reviewed == eligible_reviews, 'first same-tick single inquiry omitted its required supplement review')
    _require(branch['metrics']['scarce_supplement_reviews'] == len(records)
             and branch['metrics']['scarce_supplement_sent'] == sent, 'supplement metrics differ from raw events')
    return dict(scarce_supplement_reviews=len(records), scarce_supplement_sent=sent)


def audit_backups(scenario, branch, work):
    """Validate original inquiry continuity, receipt, TTL and transfer reuse."""
    retained = branch.get('retained_backups_enabled')
    same_tick = branch.get('transfer_same_tick_enabled')
    followup = branch.get('recovery_followup_enabled')
    _require(type(retained) is bool and type(same_tick) is bool and type(followup) is bool
             and (not same_tick or retained) and (not followup or same_tick),
             'invalid explicit backup retention or preparation mode')
    ordered = information._indexed_events(branch)
    indexed = {event['event_sequence']: event for event in ordered}
    specs = {p['id']: p for p in scenario['patients']}
    truth = information._TruthLedger(scenario)
    states, destinations, asks, offers, episodes, notifications = {}, {}, {}, {}, {}, set()
    pending_commit, early_ready, failures, expected_reply = {}, {}, {}, None
    observations, reports, resource_failures, expires, terminals, latest_notify, alert_retries = {}, {}, {}, {}, {}, {}, set()
    pending_ready=set()
    counts = Counter(backup_episodes=0, retained_pending_inquiries=0,
        retained_live_offers=0, backup_replies=0, backup_arrivals_preserved=0,
        backup_preparations_ready=0, backup_transfers_reused=0, backup_episodes_closed=0,
        backup_report_reopens=0, backup_alert_retries=0)
    marker_names = {'backup_retained', 'backup_reply_received', 'backup_arrival_preserved',
                    'backup_preparation_ready', 'backup_transfer_reused', 'backup_closed',
                    'backup_failed_reopened', 'backup_alert_retry'}
    for event in ordered:
        kind, at, seq = event['type'], event['t'], event['event_sequence']
        pid, hid = event.get('patient'), event.get('hospital')
        if kind in marker_names:
            _require(retained and pid in specs, 'backup event on disabled branch or undeclared patient')
        if expected_reply is not None:
            _require(kind == expected_reply['outcome'] and pid == expected_reply['patient']
                     and hid == expected_reply['hospital'] and at == expected_reply['t'],
                     'backup receipt lacks immediate matching ordinary reply')
            expected_reply = None
        if kind == 'resource_report_received':
            information._apply_observation(observations,event)
            if event['applied']:reports[hid]=event
        elif kind == 'information_check' and event['purpose']=='allocation' and event['eligible'] and states.get(pid)=='SEARCH':
            pending_ready.add(pid)
        elif kind == 'opportunity_guard':
            row=branch['protection_records'][event['record_index']]
            pending_ready.difference_update(patient['id'] for patient in row['snapshot']['ready'])
        elif kind == 'allocation_proposal':
            row=branch['allocation_decisions'][event['decision_index']]
            pending_ready.difference_update(patient['id'] for patient in row['inputs']['patients'])
        elif kind == 'request':
            states[pid] = 'SEARCH'
        elif kind == 'early_offer_ready':
            early_ready[pid] = event
        elif kind == 'cooperative_probe_not_admitted':
            asks.pop((pid,hid),None)
        elif kind == 'ask':
            _require((pid, hid) not in asks, 'overlapping active patient inquiry')
            _require(pid not in episodes or (pid,hid) not in resource_failures,
                     'new inquiry to resource-failed hospital lacks received-report reopening')
            asks[pid, hid] = dict(hospital=hid, attempt=event['attempt'], submitted_at=at,
                timeout_at=at + scenario.get('timeout_minutes', 6), ask_event_sequence=seq)
        elif kind == 'backup_reply_received':
            _require(pid in episodes and (pid, hid) in asks,
                     'backup reply resurrects a cancelled or unknown inquiry')
            handle = asks[pid, hid]
            _require(set(event) == {'t','type','event_sequence','patient','hospital','attempt',
                'ask_event_sequence','outcome','phase'}, 'backup reply reveals future schedule or unknown fields')
            _require(event['attempt'] == handle['attempt'] and event['ask_event_sequence'] == handle['ask_event_sequence']
                     and at <= handle['timeout_at'], 'backup reply changed original request or absolute deadline')
            phase = 'transit' if states.get(pid) == 'TRANSIT' else 'preparation' if states.get(pid) == 'TRANSFER_WAIT' else None
            _require(event['phase'] == phase and phase is not None,
                     'receive-only backup reply outside transit or preparation')
            due, silent = _work_reply_due(work, handle)
            _require(due is None or at == due, 'backup reply changed original hospital work or delivery time')
            demand = specs[pid].get('resource_demands', {'ed_bed':1})
            quantities = truth.resources(hid, demand, at)
            compatible = truth.patients[pid]['capabilities'] <= truth.capabilities[hid]
            outcome = 'no_response' if silent else 'accept' if compatible and all(
                values['free'] >= demand[resource] for resource, values in quantities.items()) else 'decline'
            _require(event['outcome'] in ('accept','decline','no_response')
                     and (silent is None or event['outcome'] == outcome),
                     'backup reply differs from original silence or current physical acceptance')
            expected_reply = dict(event)
            counts['backup_replies'] += 1
        elif kind in ('accept','decline','no_response'):
            callback = _is_callback_accept(event, indexed)
            if callback:
                _require((pid, hid) not in asks and pid not in episodes,
                         'late callback overlaps a real inquiry or retained backup episode')
            else:
                _require((pid, hid) in asks, 'ordinary reply lacks original active inquiry')
                handle = asks.pop((pid, hid))
            if pid in episodes:
                if states.get(pid)=='TRANSFER_WAIT' or states.get(pid)=='TRANSIT' and pid not in notifications:
                    previous=indexed.get(seq-1)
                    _require(previous is not None and previous['type']=='backup_reply_received',
                             'receive-only original reply omitted backup receipt marker')
                due, _ = _work_reply_due(work, handle)
                _require(at <= handle['timeout_at'] and (due is None or at == due),
                         'retained original inquiry reply after timeout or at changed time')
            if kind == 'accept':
                offers[pid, hid] = dict(hospital=hid, accepted_at=at,
                    expires_at=at + scenario.get('accept_ttl_minutes', 10), accept_event_sequence=seq)
            else:terminals[pid,hid]=event
        elif kind == 'expire':
            offers.pop((pid, hid), None)
            expires[pid,hid]=event
            terminals[pid,hid]=event
        elif kind == 'confirmation_gate' and not event['passed']:
            offers.pop((pid, hid), None)
        elif kind == 'notify':
            if pid in episodes:
                _require(states.get(pid) == 'TRANSIT' and destinations.get(pid) == hid,
                         'backup notification differs from actual in-transit destination')
                _require(set(event) == {'t','type','event_sequence','patient','hospital','resource','cause'},
                         'patient notification carries extra quantities or future restoration')
                notifications.add(pid)
                latest_notify[pid]=event
                if followup:
                    original=episodes[pid]
                    known={row['hospital'] for row in original['pending']+original['offers']}
                    required=[]
                    for backup in sorted(known):
                        terminal=terminals.get((pid,backup))
                        if backup==hid or (pid,backup) in asks or (pid,backup) in offers or terminal is None \
                                or terminal['type']!='expire' or terminal['t']>at \
                                or (pid,backup,seq) in alert_retries:continue
                        demand=specs[pid].get('resource_demands',{'ed_bed':1})
                        views=information._views('observed',observations,truth,backup,demand,at)
                        if information._check_reason(views,demand) is None:required.append(backup)
                    for offset,backup in enumerate(required,1):
                        following=indexed.get(seq+offset)
                        _require(following is not None and following['type']=='backup_alert_retry'
                                 and following['patient']==pid and following['hospital']==backup
                                 and following['notify_event_sequence']==seq,
                                 'current alert omitted eligible expired-backup retry')
        elif kind == 'commit':
            if pid in episodes:
                _require(states.get(pid) == 'SEARCH' or pid in notifications,
                         'receive-only healthy transit automatically rerouted to a backup')
            pending_commit[pid] = dict(event=event,
                pending=_pending_rows(asks,pid,at,exclude={hid}),
                offers=_offer_rows(offers,pid,at,exclude={hid}),
                early=early_ready.get(pid))
            following=indexed.get(seq+1)
            if pid in episodes:
                _require(following is not None and following['type']=='backup_closed'
                         and following['patient']==pid and following['cause_event_sequence']==seq,
                         'later commitment omitted backup closure')
            elif retained and pid not in destinations and early_ready.get(pid,{}).get('t')==at \
                    and (pending_commit[pid]['pending'] or pending_commit[pid]['offers']):
                _require(following is not None and following['type']=='backup_retained'
                         and following['patient']==pid and following['commit_event_sequence']==seq,
                         'eligible early commitment omitted original backup retention')
            asks = {key: value for key,value in asks.items() if key[0] != pid}
            offers = {key: value for key,value in offers.items() if key[0] != pid}
            states[pid], destinations[pid] = 'TRANSIT', hid
        elif kind == 'backup_retained':
            saved = pending_commit.get(pid)
            _require(saved is not None and saved['event']['event_sequence'] == event['commit_event_sequence']
                     and saved['event']['event_sequence'] == seq - 1 and saved['event']['t'] == at
                     and saved['event']['hospital'] == hid and pid not in episodes,
                     'backup retention lacks immediately preceding eligible early commitment')
            first = saved['early']
            _require(first is not None and first['t'] == at
                     and first['event_sequence'] == event['early_ready_event_sequence'],
                     'backup retention is not a successful same-tick early commitment')
            _require(set(event) == {'t','type','event_sequence','patient','hospital','commit_event_sequence',
                'early_ready_event_sequence','pending','offers'}, 'backup retention exposes future inquiry details')
            _require(base._same(event['pending'],saved['pending']) and base._same(event['offers'],saved['offers']),
                     'retained pending inquiries or live offers differ from original known work')
            for row in event['pending']:
                asks[pid,row['hospital']] = deepcopy(row)
            for row in event['offers']:
                offers[pid,row['hospital']] = deepcopy(row)
            episodes[pid] = event
            notifications.discard(pid)
            counts.update(backup_episodes=1, retained_pending_inquiries=len(event['pending']),
                          retained_live_offers=len(event['offers']))
        elif kind == 'arrive':
            states[pid] = 'QUEUED'
            if pid in episodes:
                following=indexed.get(seq+1)
                _require(following is not None and following['type']=='backup_arrival_preserved'
                         and following['patient']==pid and following['arrive_event_sequence']==seq,
                         'arrival omitted preservation of retained backup work')
        elif kind == 'backup_arrival_preserved':
            prior = indexed.get(event['arrive_event_sequence'])
            _require(pid in episodes and prior is not None and prior['event_sequence'] == seq-1
                     and prior['type'] == 'arrive' and prior['patient'] == pid and prior['hospital'] == hid
                     and prior['t'] == at, 'backup arrival preservation lacks immediate original arrival')
            _require(set(event) == {'t','type','event_sequence','patient','hospital','arrive_event_sequence','pending','offers'},
                     'backup arrival preservation carries unknown policy fields')
            _require(base._same(event['pending'],_pending_rows(asks,pid,at))
                     and base._same(event['offers'],_offer_rows(offers,pid,at)),
                     'arrival discarded, fabricated or renewed original backup work')
            counts['backup_arrivals_preserved'] += 1
        elif kind == 'arrival_unable':
            states[pid] = 'TRANSFER_WAIT'
            failures[pid] = event
            if event['reason']=='resource_capacity':resource_failures[pid,hid]=event
        elif kind == 'backup_preparation_ready':
            prior = failures.get(pid)
            _require(same_tick and pid in episodes and states.get(pid) == 'TRANSFER_WAIT'
                     and prior is not None and prior['event_sequence'] == event['arrival_unable_event_sequence']
                     and prior['hospital'] == hid and event['preparation_minutes'] == scenario.get('transfer_preparation_minutes',3)
                     and event['ready_at'] == at == prior['transfer_search_at'],
                     'backup preparation skipped original failed-arrival preparation')
            _require(set(event) == {'t','type','event_sequence','patient','hospital','arrival_unable_event_sequence',
                                  'preparation_minutes','ready_at'}, 'backup preparation exposes future or unknown fields')
            following = indexed.get(seq+1)
            _require(following is not None and following['type'] == 'transfer_search'
                     and following['patient'] == pid and following['hospital'] == hid and following['t'] == at,
                     'preparation-ready marker lacks immediate ordinary transfer search')
            counts['backup_preparations_ready'] += 1
        elif kind == 'transfer_search':
            states[pid] = 'SEARCH'
            if pid in episodes:
                prior=indexed.get(seq-1);following=indexed.get(seq+1)
                _require(not same_tick or prior is not None and prior['type']=='backup_preparation_ready'
                         and prior['patient']==pid, 'same-tick transfer omitted original preparation-ready marker')
                _require(following is not None and following['type']=='backup_transfer_reused'
                         and following['patient']==pid and following['transfer_event_sequence']==seq,
                         'transfer search omitted reuse of original backup work')
            if pid not in episodes:
                asks = {key:value for key,value in asks.items() if key[0] != pid}
                offers = {key:value for key,value in offers.items() if key[0] != pid}
        elif kind == 'backup_transfer_reused':
            prior, failure = indexed.get(event['transfer_event_sequence']), failures.get(pid)
            _require(pid in episodes and prior is not None and prior['event_sequence'] == seq-1
                     and prior['type'] == 'transfer_search' and prior['patient'] == pid and prior['hospital'] == hid
                     and prior['t'] == at and failure is not None
                     and failure['event_sequence'] == event['arrival_unable_event_sequence']
                     and at == failure['transfer_search_at'], 'backup reuse precedes original ordinary transfer search')
            _require(set(event) == {'t','type','event_sequence','patient','hospital','transfer_event_sequence',
                                  'arrival_unable_event_sequence','pending','offers'},
                     'backup transfer reuse carries future or unknown policy fields')
            _require(base._same(event['pending'],_pending_rows(asks,pid,at))
                     and base._same(event['offers'],_offer_rows(offers,pid,at)),
                     'transfer reuse discarded, fabricated, expired or renewed original backup work')
            counts['backup_transfers_reused'] += 1
        elif kind == 'treatment_start' and pid in specs:
            states[pid] = 'TREATING'
            if pid in episodes:
                following=indexed.get(seq+1)
                _require(following is not None and following['type']=='backup_closed'
                         and following['patient']==pid and following['cause_event_sequence']==seq,
                         'healthy treatment omitted backup closure')
        elif kind == 'backup_failed_reopened':
            failure=resource_failures.get((pid,hid));report=reports.get(hid)
            _require(followup and pid in episodes and states.get(pid)=='SEARCH' and pid not in pending_ready
                     and (pid,hid) not in asks and (pid,hid) not in offers
                     and failure is not None and report is not None,
                     'report reopening lacks eligible current resource-failure waiter')
            _require(set(event)=={'t','type','event_sequence','patient','hospital',
                'arrival_unable_event_sequence','report_event_sequence'}, 'report reopening carries unknown or future fields')
            _require(failure['event_sequence']==event['arrival_unable_event_sequence']
                     and report['event_sequence']==event['report_event_sequence'] and report['t']==at
                     and report['observed_at']>=failure['t'],
                     'report reopening lacks latest same-minute applied source after resource failure')
            demand=specs[pid].get('resource_demands',{'ed_bed':1})
            views=information._views('observed',observations,truth,hid,demand,at)
            _require(information._check_reason(views,demand) is None
                     and all(view['observed_at']>=failure['t'] for view in views.values()),
                     'report reopening used stale, unknown, insufficient or pre-failure quantities')
            resource_failures.pop((pid,hid))
            check,ask=indexed.get(seq+1),_after_queue_events(indexed,seq+1)
            if ask is not None and ask['type']=='joint_inquiry_policy':
                ask=_after_queue_events(indexed,ask['event_sequence'])
            _require(check is not None and check['type']=='information_check' and check['purpose']=='inquiry'
                     and check['eligible'] is True and check['patient']==pid and check['hospital']==hid
                     and check['t']==at and ask is not None and ask['type']=='ask' and ask['patient']==pid
                     and ask['hospital']==hid and ask['t']==at, 'report reopening failed to send a fresh patient inquiry')
            counts['backup_report_reopens']+=1
        elif kind == 'backup_alert_retry':
            notice=latest_notify.get(pid);expired=expires.get((pid,hid))
            original=episodes.get(pid)
            backup_hids={row['hospital'] for row in original['pending']+original['offers']} if original else set()
            _require(followup and original is not None and states.get(pid)=='TRANSIT' and pid in notifications
                     and hid in backup_hids and notice is not None and expired is not None
                     and (pid,hid) not in asks and not any(patient==pid and hospital==hid and row['expires_at']>at
                         for (patient,hospital),row in offers.items()),
                     'alert retry lacks expired original backup and notified transit')
            _require(set(event)=={'t','type','event_sequence','patient','hospital','notify_event_sequence','expire_event_sequence'},
                     'alert retry carries unknown or future fields')
            _require(notice['event_sequence']==event['notify_event_sequence'] and expired['event_sequence']==event['expire_event_sequence']
                     and expired['t']<=notice['t'] and expired['event_sequence']<notice['event_sequence'],
                     'alert retry uses an expiration not received before the current notification')
            _require(terminals.get((pid,hid),{}).get('event_sequence')==expired['event_sequence'],
                     'alert retry resurrects a newer declined or timed-out backup')
            key=pid,hid,notice['event_sequence']
            _require(key not in alert_retries,'same alert retried an expired backup more than once')
            alert_retries.add(key)
            demand=specs[pid].get('resource_demands',{'ed_bed':1})
            views=information._views('observed',observations,truth,hid,demand,at)
            _require(information._check_reason(views,demand) is None,
                     'alert retry uses unknown, stale or insufficient current quantities')
            counts['backup_alert_retries']+=1
        elif kind=='event_review_cycle' and followup:
            for (patient,hospital),failure in resource_failures.items():
                if patient not in episodes or states.get(patient)!='SEARCH' or patient in pending_ready \
                        or (patient,hospital) in asks or (patient,hospital) in offers:continue
                report=reports.get(hospital)
                if report is None or report['t']!=at or report['observed_at']<failure['t']:continue
                demand=specs[patient].get('resource_demands',{'ed_bed':1})
                views=information._views('observed',observations,truth,hospital,demand,at)
                eligible=information._check_reason(views,demand) is None and all(
                    view['observed_at']>=failure['t'] for view in views.values())
                _require(not eligible,'current complete post-failure report omitted required reopening')
        elif kind == 'backup_closed':
            cause = indexed.get(event['cause_event_sequence'])
            _require(pid in episodes and cause is not None and cause['patient'] == pid
                     and cause['event_sequence'] == seq-1 and cause['t'] == at
                     and (event['reason'],cause['type']) in (('COMMITTED','commit'),('TREATMENT_STARTED','treatment_start')),
                     'backup closure lacks immediate actual commitment or treatment')
            _require(set(event) == {'t','type','event_sequence','patient','reason','cause_event_sequence'},
                     'backup closure carries unknown policy fields')
            episodes.pop(pid)
            asks = {key:value for key,value in asks.items() if key[0] != pid}
            offers = {key:value for key,value in offers.items() if key[0] != pid}
            notifications.discard(pid)
            counts['backup_episodes_closed'] += 1
        truth.apply(event)
    _require(expected_reply is None, 'last backup receipt lacks ordinary reply')
    for pid in episodes:
        _require(states.get(pid) not in ('TREATING',), 'healthy treatment failed to close retained work')
    return dict(counts)


def audit_late_offers(scenario, branch, work):
    """Check every callback and new acceptance against original raw emissions."""
    events = _indexed_events(branch)
    indexed = {event['event_sequence']: event for event in events}
    enabled = branch.get('late_reply_recovery_enabled', False)
    _require(type(enabled) is bool, 'late recovery mode is not explicit')
    marker_names = {'late_offer_callback_registered', 'hospital_reply_received',
                    'late_offer_review', 'late_offer_accepted'}
    if not enabled:
        _require(not branch.get('late_offer_records') and not branch.get('late_offer_callbacks')
                 and not any(event['type'] in marker_names or 'late_offer_event_sequence' in event for event in events),
                 'disabled branch contains late callbacks or acceptances')
        return dict(late_offer_callbacks=0, late_offer_receipts=0, late_offer_accepted=0,
                    late_offer_rejected=0, callback_acceptance_proofs=0)
    records, exports = branch['late_offer_records'], branch['late_offer_callbacks']
    _require(isinstance(records, list) and isinstance(exports, list), 'invalid callback export')
    workflow = _LateWorkflow(deepcopy(scenario))
    hospitals = {row['id']: row for row in scenario['hospitals']}
    deliveries = work['deliveries']
    callbacks, requests, attempts, handles = {}, {}, {}, {}
    first_commits, accepts, used, receipts, adoption_sequences = {}, {}, set(), {}, set()
    timed_out_handles, timeout_feedback = {}, set()
    supplementary = {row['event_sequence']: row for row in branch['scarce_supplement_records']}
    expected_callback_asks = set()
    source_review_by_ask = {}
    for row in branch['scarce_supplement_records']:
        if row['result']['send']:
            event = _after_queue_events(indexed, row['event_sequence'])
            _require(event is not None and event['type'] == 'ask', 'callback source supplement omitted actual ask')
            expected_callback_asks.add(event['event_sequence'])
            source_review_by_ask[event['event_sequence']] = indexed[row['event_sequence']]

    def views(hid, pid, at):
        return _views('observed', workflow.observations, workflow.truth, hid,
                      workflow._base_patient(pid)['demand'], at)

    def observed_search(at):
        result = []
        for pid in sorted(requests):
            if workflow.states.get(pid) != 'SEARCH':
                continue
            row = workflow._base_patient(pid)
            compatible = sorted(hid for hid in row['compatible_hospitals'] if all(
                hospitals[hid].get('capacity', {}).get(resource, 0) >= amount
                for resource, amount in row['demand'].items()))
            offers = [dict(hospital=hid, accepted_at=workflow.accepted[pid, hid], expires_at=expiry,
                accept_event_sequence=accepts[pid, hid]['event_sequence'])
                for (patient, hid), expiry in sorted(workflow.offers.items()) if patient == pid]
            result.append(dict(id=pid, condition=_condition(workflow, pid), state='SEARCH',
                request_t=row['request_t'], request_event_sequence=requests[pid]['event_sequence'],
                compatible_hospitals=compatible,
                pending_inquiries=[deepcopy(value) for (patient, _), value in handles.items() if patient == pid],
                valid_offers=offers,
                resource_views={hid:views(hid, pid, at) for hid in compatible} if len(compatible) == 1 else {}))
        return result

    for event in events:
        kind, at, seq = event['type'], event['t'], event['event_sequence']
        pid, hid = event.get('patient'), event.get('hospital')
        if kind == 'request':
            requests[pid] = event
        elif kind == 'ask':
            attempts[pid, hid] = event['attempt']
            handles[pid, hid] = dict(hospital=hid, attempt=event['attempt'], submitted_at=at,
                timeout_at=event['timeout_at'], ask_event_sequence=seq)
        elif kind == 'inquiry_feedback' and event['outcome'] == 'timeout':
            timeout_feedback.add((pid, hid, event['submitted_t'], event['observed_t']))
        elif kind == 'late_offer_callback_registered':
            source = indexed.get(seq - 1)
            _require(source is not None and source['type'] == 'ask' and source['event_sequence'] in expected_callback_asks
                     and pid not in {row['patient'] for row in callbacks.values()},
                     'callback did not follow its one actual initial supplemental inquiry')
            review = source_review_by_ask.get(source['event_sequence'])
            _require(review is not None and review['type'] == 'scarce_supplement_review'
                     and review['event_sequence'] in supplementary, 'callback lacks an actual supplement review')
            condition = _condition(workflow, pid)
            expected = dict(patient=pid, hospital=source['hospital'], attempt=source['attempt'],
                submitted_at=source['t'], timeout_at=source['timeout_at'], ask_event_sequence=source['event_sequence'],
                supplement_review_event_sequence=review['event_sequence'], condition_fingerprint=_fingerprint(condition))
            _require(base._same(event, dict(expected, condition=condition, t=at, type=kind, event_sequence=seq)),
                     'callback registration changed original handle, condition or deadline')
            callbacks[source['event_sequence']] = expected
            exported = dict(expected, condition=condition, event_sequence=seq)
            _require(len(callbacks) <= len(exports) and base._same(exports[len(callbacks) - 1], exported),
                     'callback export differs from original registration')
        elif kind == 'hospital_reply_received':
            ask_sequence = event['ask_event_sequence']
            _require(ask_sequence in callbacks and ask_sequence not in receipts,
                     'callback receipt is duplicated or lacks registration')
            callback = callbacks[ask_sequence]
            delivery = deliveries[ask_sequence]
            _require(scenario.get('inquiry_queue', {}).get('enabled')
                     and not delivery['silent'] and delivery['finish'] == at > callback['timeout_at'],
                     'received late callback lacks actual non-silent hospital completion after original timeout')
            _require((pid, hid, callback['submitted_at'], callback['timeout_at']) in timeout_feedback,
                     'late callback omitted the unchanged original timeout feedback')
            previous, completed = indexed.get(seq - 1), indexed.get(seq - 2)
            _require(previous is not None and completed is not None
                     and base._same(previous, dict(t=at, type='late_reply', patient=pid, hospital=hid,
                                                  attempt=callback['attempt'], ignored=True, event_sequence=seq - 1))
                     and base._same(completed, dict(t=at, type='review_completed', patient=pid, hospital=hid,
                                                    attempt=callback['attempt'], kind='inquiry', event_sequence=seq - 2)),
                     'callback receipt lacks immediate current completed work and expired-original late reply')
            physical = workflow.truth.resources(hid, workflow._base_patient(pid)['demand'], at)
            condition = _condition(workflow, pid)
            can_accept = set(condition['required_capabilities']) <= workflow.truth.capabilities[hid] \
                and all(values['free'] >= condition['demand'][resource] for resource, values in physical.items())
            expected = dict(callback, received_at=at, outcome='accept' if can_accept else 'decline')
            _require(base._same(event, dict(expected, t=at, type=kind, event_sequence=seq)),
                     'received hospital result differs from actual current physical capacity or original handle')
            receipts[ask_sequence] = dict(expected, receipt_event_sequence=seq)
        elif kind == 'late_offer_review':
            index = event['record_index']
            _require(index == len(used) and index < len(records), 'missing or reordered late-offer record')
            used.add(index)
            record = records[index]
            _require(set(record) == {'snapshot', 'result', 'event_sequence'} and record['event_sequence'] == seq,
                     'late-offer record has unknown fields or wrong raw marker')
            receipt = record['snapshot']['receipt']
            _require(receipt['ask_event_sequence'] in receipts
                     and base._same(receipt, receipts[receipt['ask_event_sequence']])
                     and receipt['receipt_event_sequence'] < seq and receipt['received_at'] == at,
                     'late-offer review used unreceived or different hospital outcome')
            _require(base._same(event, dict(t=at, type=kind, event_sequence=seq, patient=pid, hospital=hid, record_index=index)),
                     'late-offer marker has unknown or mismatched fields')
            condition = _condition(workflow, pid)
            snapshot = dict(t=at, receipt=deepcopy(receipt), patient=dict(id=pid,
                state=workflow.states[pid], condition=condition, condition_fingerprint=_fingerprint(condition),
                latest_attempt=attempts[pid, hid], first_commit_t=first_commits.get(pid),
                pending_inquiries=sorted([deepcopy(value) for (patient, _), value in handles.items() if patient == pid],
                                         key=lambda handle:(handle['hospital'], handle['attempt'])),
                valid_offers=[dict(hospital=target, expires_at=expiry, resources=views(target, pid, at))
                    for (patient, target), expiry in sorted(workflow.offers.items()) if patient == pid],
                registered_request_sequence=requests[pid]['event_sequence']),
                resources=views(hid, pid, at), accept_ttl_minutes=scenario.get('accept_ttl_minutes', 10),
                registered_search_patients=observed_search(at))
            _require(base._same(record['snapshot'], snapshot),
                     'late-offer input differs from raw current registration, condition, offers, original inquiries or reports')
            expected = _late_expected(snapshot)
            _require(base._same(record['result'], expected),
                     'late-offer decision differs from independent window, lifecycle, resource or opportunity check')
            following = indexed.get(seq + 1)
            if expected['eligible']:
                original_timeout = timed_out_handles.get(receipt['ask_event_sequence'])
                _require(original_timeout is not None and original_timeout['t'] == receipt['timeout_at'],
                         'adopted late offer did not terminate its original inquiry with unchanged no-response')
                marker = dict(t=at, type='late_offer_accepted', event_sequence=seq + 1, patient=pid, hospital=hid,
                    attempt=receipt['attempt'], ask_event_sequence=receipt['ask_event_sequence'],
                    receipt_event_sequence=receipt['receipt_event_sequence'], review_event_sequence=seq,
                    expires_at=expected['expires_at'])
                _require(following is not None and base._same(following, marker),
                         'eligible callback lacks immediate new-offer adoption with actual receipt lifetime')
                ordinary = indexed.get(seq + 2)
                _require(ordinary is not None and base._same(ordinary, dict(t=at, type='accept', event_sequence=seq + 2,
                    patient=pid, hospital=hid, late_offer_event_sequence=seq + 1,
                    receipt_event_sequence=receipt['receipt_event_sequence'])),
                    'late-offer adoption lacks exactly one immediate separately sourced ordinary acceptance')
                adoption_sequences.add(seq + 1)
            else:
                _require(following is None or following['type'] != 'late_offer_accepted',
                         'ineligible late reply created a new acceptance')
        elif kind == 'cooperative_probe_not_admitted':
            handles.pop((pid, hid), None)
        elif kind in ('accept', 'decline', 'no_response'):
            if not _is_callback_accept(event, indexed):
                if kind == 'no_response' and (pid, hid) in handles:
                    timed_out_handles[handles[pid, hid]['ask_event_sequence']] = event
                handles.pop((pid, hid), None)
            if kind == 'accept':
                accepts[pid, hid] = event
        elif kind in ('commit', 'arrive', 'transfer_search', 'treatment_start', 'backup_closed'):
            handles = {key:value for key, value in handles.items() if key[0] != pid}
            if kind == 'commit':
                first_commits.setdefault(pid, at)
        elif kind in ('backup_retained', 'backup_arrival_preserved', 'backup_transfer_reused'):
            handles = {key:value for key, value in handles.items() if key[0] != pid}
            for handle in event['pending']:
                handles[pid, handle['hospital']] = deepcopy(handle)
        workflow.apply(event)
    _require(set(callbacks) == expected_callback_asks and len(exports) == len(callbacks),
             'missing, orphan or duplicated supplemental callback registration')
    expected_receipts = {sequence for sequence, callback in callbacks.items()
        if scenario.get('inquiry_queue', {}).get('enabled') and not deliveries[sequence]['silent']
        and deliveries[sequence]['finish'] is not None
        and callback['timeout_at'] < deliveries[sequence]['finish'] <= _end(scenario, branch)}
    _require(set(receipts) == expected_receipts and used == set(range(len(records)))
             and len(records) == len(receipts), 'actual late delivery or its required review is missing')
    _require(adoption_sequences == {event['event_sequence'] for event in events if event['type'] == 'late_offer_accepted'},
             'orphan callback adoption marker')
    actual_acceptances = {event['event_sequence'] for event in events if 'late_offer_event_sequence' in event}
    _require(actual_acceptances == {seq + 1 for seq in adoption_sequences}, 'orphan or fabricated callback acceptance')
    _require(not workflow.late_adoptions, 'unconsumed callback adoption')
    counts = dict(late_offer_callbacks=len(callbacks), late_offer_receipts=len(receipts),
        late_offer_accepted=len(adoption_sequences), late_offer_rejected=len(receipts) - len(adoption_sequences),
        callback_acceptance_proofs=len(adoption_sequences))
    for key in ('late_offer_receipts', 'late_offer_accepted', 'late_offer_rejected'):
        _require(branch['metrics'][key] == counts[key], 'callback endpoint count differs from raw evidence: ' + key)
    information._number(branch['metrics']['late_offer_wall_seconds'], 'late-offer execution time')
    return counts


def _probe_patient_view(workflow, requests, handles, accepted, pid, now):
    return dict(id=pid, state=workflow.states[pid], request_t=requests[pid]['t'],
        request_event_sequence=requests[pid]['event_sequence'],
        pending_inquiries=[deepcopy(handle) for (patient, _), handle in handles.items() if patient == pid],
        valid_offers=[dict(hospital=hid, accepted_at=workflow.accepted[pid, hid], expires_at=expiry,
            accept_event_sequence=accepted[pid, hid]['event_sequence'])
            for (patient, hid), expiry in sorted(workflow.offers.items()) if patient == pid and expiry > now])

def audit_probes(scenario, branch):
    """Rebuild public probe inputs, every explicit recheck, and its actual ask."""
    events = _indexed_events(branch)
    indexed = {event['event_sequence']: event for event in events}
    records = branch['shortage_probe_records']
    _require(isinstance(records, list), 'probe records must be a list')
    workflow = _LateWorkflow(deepcopy(scenario))
    hospitals = {row['id']: row for row in scenario['hospitals']}
    requests, handles, accepted, first_asks, first_commits, last_contacts = {}, {}, {}, {}, {}, {}
    inquiries, contacts, probe_counts = Counter(), Counter(), Counter()
    episodes, tried, used, ask_sequences, check_sequences = {}, {}, set(), set(), set()
    pending_targets, cycles, sent = [], [], 0
    retry_records=branch['cooperative_busy_retry_records']
    used_retries,retry_counts,retry_ask_sequences=set(),Counter(),set()
    latest_acks,seen_selectors,retry_targets={},{},[]

    def snapshot(pid, now):
        current = workflow._base_patient(pid)
        point = _position(scenario, branch, pid, now)
        compatible = sorted(hid for hid in current['compatible_hospitals'] if all(
            hospitals[hid].get('capacity', {}).get(resource, 0) >= amount
            for resource, amount in current['demand'].items()))
        patient = _probe_patient_view(workflow, requests, handles, accepted, pid, now)
        patient.update(priority=current['priority'], demand=deepcopy(current['demand']),
            required_capabilities=sorted(current['required_capabilities']),
            inquiry_episode=episodes.get(pid, 0), episode_contacts=contacts[pid, episodes.get(pid, 0)],
            inquiry_count=inquiries[pid], first_commit_t=first_commits.get(pid),
            first_ask=deepcopy(first_asks.get(pid)), tried_hospitals=sorted(tried.get(pid, set())),
            last_contact_at=last_contacts.get(pid))
        candidates = [dict(hospital=hid,
            travel_minutes=recovery._synthetic_minutes(point, hospitals[hid]),
            resources=_views('observed', workflow.observations, workflow.truth, hid,
                             current['demand'], now)) for hid in compatible]
        cohort = [_probe_patient_view(workflow, requests, handles, accepted, other, now)
                  for other in sorted(requests) if workflow.states.get(other) == 'SEARCH']
        for row in cohort:
            row['pending_inquiries'].sort(key=lambda handle: (handle['hospital'], handle['attempt']))
        return dict(t=now, phase='before_joint_batch', patient=patient, candidates=candidates,
                    registered_search_patients=cohort, probe_count=probe_counts[pid])

    for event in events:
        kind, now, sequence = event['type'], event['t'], event['event_sequence']
        pid, hid = event.get('patient'), event.get('hospital')
        if kind == 'request':
            requests[pid] = event
        elif kind == 'joint_inquiry_policy':
            seen_selectors[pid]=branch['inquiry_decisions'][event['record_index']]
        elif kind == 'cooperative_probe_control_ack':
            compact={key:deepcopy(event[key]) for key in ('hospital','attempt','ask_event_sequence','job_id','state','received_at')}
            compact['control_event_sequence']=sequence
            latest_acks[pid,hid,event['attempt']]=compact
        elif kind == 'ask':
            handle = dict(hospital=hid, attempt=event['attempt'], submitted_at=now,
                timeout_at=event['timeout_at'], ask_event_sequence=sequence)
            first_asks.setdefault(pid, deepcopy(handle))
            handles[pid, hid] = handle
            inquiries[pid] += 1
            episodes[pid] = event['inquiry_episode']
            contacts[pid, event['inquiry_episode']] += 1
            last_contacts[pid] = now
            if event.get('cooperative_job_id') is not None:
                tried.setdefault(pid, set()).add(hid)
        elif kind == 'cooperative_probe_not_admitted':
            handles.pop((pid, hid), None)
        elif kind in ('accept', 'decline', 'no_response'):
            if not _is_callback_accept(event, indexed):
                handles.pop((pid, hid), None)
            if kind == 'accept':
                accepted[pid, hid] = event
        elif kind in ('commit', 'arrive', 'transfer_search', 'treatment_start', 'backup_closed'):
            handles = {key:value for key, value in handles.items() if key[0] != pid}
            if kind == 'commit':
                first_commits.setdefault(pid, now)
            if kind == 'transfer_search':
                episodes[pid] = episodes.get(pid, 0) + 1
        elif kind in ('backup_retained', 'backup_arrival_preserved', 'backup_transfer_reused'):
            handles = {key:value for key, value in handles.items() if key[0] != pid}
            for handle in event['pending']:
                handles[pid, handle['hospital']] = deepcopy(handle)
        elif kind == 'shortage_probe_cycle':
            _require(not pending_targets and not retry_targets and set(event) == {'t', 'type', 'event_sequence'},
                     'previous probe cycle omitted reviews or cycle has unknown fields')
            cycles.append(now)
            pending_targets = [patient for patient in sorted(requests)
                               if _probe_review_due(snapshot(patient, now))]
            normal_ready={row['id'] for row in workflow.snapshot(now)['ready']}
            for patient in sorted(requests):
                current=snapshot(patient,now);p=current['patient']
                acks=[ack for (other,_,_),ack in latest_acks.items() if other==patient and ack['state']=='not_admitted']
                selector=seen_selectors.get(patient)
                if p['state']=='SEARCH' and p['first_commit_t'] is None and patient not in normal_ready \
                    and p['pending_inquiries'] and p['inquiry_episode']==0 and retry_counts[patient]==0 \
                    and len(acks)==1 and selector is not None and selector['t']==now:
                    views=_views('observed',workflow.observations,workflow.truth,acks[0]['hospital'],p['demand'],now)
                    if any(view['received_at']==now for view in views.values()):retry_targets.append(patient)
        elif kind == 'cooperative_busy_retry_review':
            _require(retry_targets and pid==retry_targets.pop(0), 'busy retry omitted current evidence or its required review')
            index=event['record_index']
            _require(index==len(used_retries) and index<len(retry_records), 'missing or reordered busy retry record')
            used_retries.add(index);record=retry_records[index]
            _require(set(event)=={'t','type','event_sequence','patient','record_index'}
                and set(record)=={'snapshot','result','event_sequence'} and record['event_sequence']==sequence,
                'busy retry marker or record has unknown fields')
            ack=next(ack for (other,_,_),ack in latest_acks.items() if other==pid and ack['state']=='not_admitted')
            selector=seen_selectors[pid]
            expected_snapshot=dict(opportunity_snapshot=snapshot(pid,now),not_admitted_ack=deepcopy(ack),
                retry_count=retry_counts[pid],ordinary_selector_event_sequence=selector['event_sequence'],
                offered_hospitals=[row['hospital_id'] for row in selector['inputs']['candidates']],
                ordinary_send=selector['decision']['send'],max_new=selector['inputs']['max_new'])
            _require(base._same(record['snapshot'],expected_snapshot),
                'busy retry rewrote the actual contact clock, selector, control ACK, normal tried set, budget or new resource report')
            expected=_busy_expected(expected_snapshot)
            _require(base._same(record['result'],expected), 'busy retry lacks an independent newly sufficient public report')
            actual=[row for row in events if row.get('cooperative_busy_retry_review_event_sequence')==sequence]
            _require([row['hospital'] for row in actual]==expected['send'], 'busy retry differs from its actual normal contacts')
            if expected['send']:
                target=expected['send'][0]
                resources=next(row['resources'] for row in expected_snapshot['opportunity_snapshot']['candidates'] if row['hospital']==target)
                check=indexed.get(sequence+1);ask=_after_queue_events(indexed,sequence+1)
                _require(check is not None and base._same(check,dict(t=now,type='information_check',event_sequence=sequence+1,
                    patient=pid,hospital=target,purpose='inquiry',eligible=True,reason=None,resources=resources)),
                    'new normal retry bypassed its unchanged sufficient inquiry-resource check')
                _require(ask is not None and ask==actual[0] and ask['patient']==pid and ask['t']==now
                    and ask.get('cooperative_probe_request') is False and ask.get('cooperative_job_id') is not None,
                    'busy retry lacks an actual ordinary admitted clinical job')
                retry_counts[pid]+=1;retry_ask_sequences.add(ask['event_sequence'])
        elif kind == 'shortage_probe_review':
            _require(pending_targets and pid == pending_targets.pop(0),
                     'probe review is omitted, duplicated or outside the public review preconditions')
            index = event['record_index']
            _require(index == len(used) and index < len(records), 'missing or reordered shortage-probe record')
            used.add(index)
            record = records[index]
            _require(set(event) == {'t', 'type', 'event_sequence', 'patient', 'record_index'}
                     and set(record) == {'snapshot', 'result', 'event_sequence'}
                     and record['event_sequence'] == sequence, 'probe record or marker carries unknown fields')
            expected_snapshot = snapshot(pid, now)
            if not base._same(record['snapshot'], expected_snapshot):
                _require(False, 'probe input differs from public registration, actual inquiries/offers or original shortage reports: '
                         + _difference(record['snapshot'], expected_snapshot))
            expected = _probe_expected(expected_snapshot)
            _require(base._same(record['result'], expected), 'probe decision differs from independent bounded public recheck rule')
            following = indexed.get(sequence + 1)
            if expected['send']:
                chosen = expected['send'][0]
                resources = next(row['resources'] for row in expected_snapshot['candidates'] if row['hospital'] == chosen)
                _require(following is not None and base._same(following, dict(t=now, type='information_check',
                    event_sequence=sequence + 1, patient=pid, hospital=chosen, purpose='shortage_probe',
                    eligible=True, reason='REPORTED_BED_SHORTAGE_RECHECK', resources=resources)),
                    'probe lacks exactly its original fresh-shortage check with explicit new purpose')
                ask = next((row for row in events if row.get('shortage_probe_review_event_sequence') == sequence), None)
                _require(ask is not None and ask['type'] == 'ask' and ask['t'] == now
                    and ask['patient'] == pid and ask['hospital'] == chosen
                    and ask.get('shortage_probe_review_event_sequence') == sequence
                    and ask['event_sequence'] > sequence + 1
                    and all(indexed[number]['type'].startswith('cooperative_job_')
                        or indexed[number]['type']=='cooperative_probe_control_ack'
                        for number in range(sequence+2,ask['event_sequence'])),
                    'eligible shortage recheck lacks uniquely referenced immediate actual inquiry')
                ask_sequences.add(ask['event_sequence']); check_sequences.add(sequence + 1)
                probe_counts[pid] += 1
                _require(probe_counts[pid] == 1, 'more than one shortage probe per patient')
                sent += 1
            else:
                _require(following is None or following['type'] != 'information_check'
                    or following.get('purpose') != 'shortage_probe', 'ineligible recheck emitted a probe')
        workflow.apply(event)
    _require(not pending_targets and not retry_targets and used == set(range(len(records)))
        and used_retries==set(range(len(retry_records))), 'orphan or omitted probe/retry record')
    _require(retry_ask_sequences=={row['event_sequence'] for row in events if 'cooperative_busy_retry_review_event_sequence' in row},
        'orphan or fabricated busy-retry normal inquiry')
    _require(ask_sequences == {row['event_sequence'] for row in events if 'shortage_probe_review_event_sequence' in row},
             'orphan or fabricated shortage-probe inquiry')
    _require(check_sequences == {row['event_sequence'] for row in events
        if row['type'] == 'information_check' and row['purpose'] == 'shortage_probe'},
        'orphan shortage-probe information check')
    _require(cycles == [step / 4 for step in range(int(_end(scenario, branch) * 4) + 1)],
             'missing, extra or reordered ordinary before-batch probe cycle')
    _require(branch['metrics']['shortage_probe_reviews'] == len(records)
             and branch['metrics']['shortage_probe_sent'] == sent, 'probe metrics differ from original raw ledger')
    information._number(branch['metrics']['shortage_probe_wall_seconds'], 'shortage-probe runtime')
    _require(branch['metrics']['cooperative_busy_retries']==sum(retry_counts.values()), 'busy retry counter differs from actual new normal work')
    return dict(shortage_probe_cycles=len(cycles), shortage_probe_reviews=len(records), shortage_probe_sent=sent,
                explicit_shortage_checks=len(check_sequences),cooperative_busy_retry_reviews=len(retry_records),
                cooperative_busy_retries=sum(retry_counts.values()))


def audit_inquiries(scenario, branch, work):
    """Rebuild FIFO work, feedback and selector inputs from earlier emissions."""
    events = _indexed_events(branch)
    indexed = {event['event_sequence']: event for event in events}
    end = _end(scenario, branch)
    selector = branch['inquiry_selector']
    records = branch['inquiry_decisions']
    if selector == 'fixed_3':
        _require(not records, 'fixed policy contains selector records')
    else:
        _require(selector in ('v2', 'v3', 'v4'), 'unknown inquiry selector')
    _require(branch['initial_inquiry_history'] == scenario.get('initial_inquiry_history', []),
             'initial inquiry history differs from input')
    _require(branch['time_origin_seconds'] == CLOCK_ORIGIN_SECONDS
             and branch['time_step_seconds'] == 15, 'selector clock differs from protocol')
    specs = {row['id']: row for row in scenario['patients']}
    hospitals = {row['id']: row for row in scenario['hospitals']}
    queue = scenario.get('inquiry_queue', {})
    handles, active, unresolved, feedback, latest, checks = {}, {}, {}, [], {}, []
    truth = _TruthLedger(scenario)
    used_records, episode_contacts = set(), Counter()
    covered_asks, supplemental_asks, probe_asks, retry_asks = set(), set(), set(), set()
    current_episode = {pid: 0 for pid in specs}
    def seconds(value):
        return CLOCK_ORIGIN_SECONDS + int(value * 60)

    for event in events:
        at, kind, seq = event['t'], event['type'], event['event_sequence']
        pid, hid = event.get('patient'), event.get('hospital')
        if kind == 'ask':
            _require(pid in specs and hid in hospitals, 'inquiry target is undeclared')
            key = (pid, hid, event['attempt'])
            _require(key not in handles and (pid, hid) not in active, 'duplicate active inquiry attempt')
            _require(event['submitted_at'] == at and event['timeout_at'] == at + scenario.get('timeout_minutes', 6),
                     'inquiry absolute handle or deadline changed')
            delivery = work['deliveries'][seq]
            admitted = delivery['admitted']
            finish = delivery['finish']
            silent = delivery['silent'] or finish is None or finish > event['timeout_at'] if admitted else None
            due = event['timeout_at'] if silent else finish
            handle = dict(hospital=hid, patient=pid, submitted_at=seconds(at),
                          submitted_minute=at, due=due, silent=silent, attempt=event['attempt'],
                          ask_event_sequence=seq)
            handles[key] = handle
            if admitted:
                active[pid, hid] = handle
                unresolved[seq] = handle
                latest[pid] = at
            if not admitted: latest[pid] = at
            episode = event['inquiry_episode']
            _require(type(episode) is int and episode >= current_episode[pid], 'inquiry episode moved backwards')
            current_episode[pid] = episode
            episode_contacts[pid, episode] += 1
            if selector != 'fixed_3':
                _require(episode_contacts[pid, episode] <= 10, 'episode contact budget exceeded')
                _require(sum(patient == pid for patient, _ in active) <= 3, 'patient concurrent inquiry limit exceeded')
        elif kind == 'inquiry_feedback':
            matches = [key for key, handle in unresolved.items() if handle['patient'] == pid
                       and handle['hospital'] == hid and handle['submitted_minute'] == event['submitted_t']]
            _require(len(matches) == 1, 'feedback lacks a unique submitted inquiry')
            handle = unresolved.pop(matches[0])
            _require(at == event['observed_t'] == handle['due']
                     and event['outcome'] == ('timeout' if handle['silent'] else 'reply'),
                     'feedback timing or result differs from original FIFO/deadline')
            feedback.append(dict(hospital=hid, patient=pid, submitted_at=handle['submitted_at'],
                                 observed_at=seconds(at), outcome=event['outcome']))
        elif kind in ('accept', 'decline', 'no_response'):
            callback = _is_callback_accept(event, indexed)
            if callback:
                _require((pid, hid) not in active, 'late callback overlaps a real pending inquiry')
            else:
                _require((pid, hid) in active, 'patient reply lacks currently retained inquiry')
                handle = active.pop((pid, hid))
                _require(at == handle['due'], 'patient reply changed original delivery time')
                _require((kind == 'no_response') == handle['silent'], 'silent inquiry returned a fabricated reply')
            if kind != 'no_response':
                demand = specs[pid].get('resource_demands', {'ed_bed': 1})
                physical = truth.resources(hid, demand, at)
                can_accept = (truth.patients[pid]['capabilities'] <= truth.capabilities[hid]
                    and all(values['free'] >= demand[resource] for resource, values in physical.items()))
                _require((kind == 'accept') == can_accept,
                         'patient acceptance/decline differs from current physical capacity/capability')
        elif kind in ('commit', 'arrive', 'transfer_search', 'treatment_start', 'backup_closed'):
            active = {key: value for key, value in active.items() if key[0] != pid}
            if kind == 'transfer_search':
                current_episode[pid] += 1
        elif kind in ('backup_retained', 'backup_arrival_preserved', 'backup_transfer_reused'):
            active = {key: value for key, value in active.items() if key[0] != pid}
            for row in event['pending']:
                key = (pid, row['hospital'], row['attempt'])
                _require(key in handles and row['ask_event_sequence'] == handles[key]['ask_event_sequence'],
                         'backup inquiry did not preserve original attempt')
                active[pid, row['hospital']] = handles[key]
        elif kind == 'information_check' and event['purpose'] == 'inquiry':
            checks.append(event)
        elif kind == 'scarce_supplement_review':
            record = branch['scarce_supplement_records'][event['record_index']]
            candidate = _after_queue_events(indexed, seq)
            following = [candidate] if candidate is not None and candidate['type']=='ask' and candidate['t']==at and candidate.get('patient')==pid else []
            _require([e['hospital'] for e in following] == record['result']['send'],
                     'supplement marker differs from immediately submitted inquiry')
            supplemental_asks.update(e['event_sequence'] for e in following)
        elif kind == 'shortage_probe_review':
            record = branch['shortage_probe_records'][event['record_index']]
            following = next((row for row in events if row.get('shortage_probe_review_event_sequence') == seq), None)
            actual = [following] if following is not None and following['type'] == 'ask' and following.get('shortage_probe_review_event_sequence') == seq else []
            _require([row['hospital'] for row in actual] == record['result']['send'],
                     'probe review differs from its uniquely sourced actual inquiry')
            probe_asks.update(row['event_sequence'] for row in actual)
        elif kind == 'cooperative_busy_retry_review':
            record=branch['cooperative_busy_retry_records'][event['record_index']]
            actual=[row for row in events if row.get('cooperative_busy_retry_review_event_sequence')==seq]
            _require([row['hospital'] for row in actual]==record['result']['send'],'busy retry actual inquiry source differs')
            retry_asks.update(row['event_sequence'] for row in actual)
        elif kind == 'joint_inquiry_policy':
            index = event['record_index']
            _require(index == len(used_records) and index < len(records), 'missing or reordered inquiry record')
            used_records.add(index)
            row = records[index]
            _require(set(row) == {'t', 'patient', 'inquiry', 'inputs', 'decision', 'event_sequence',
                                  'inquiry_episode', 'ask_event_sequences', 'resource_check_sequences'},
                     'inquiry record contains future or unknown fields')
            _require((row['t'], row['patient'], row['inquiry'], row['event_sequence']) ==
                     (at, pid, selector, seq), 'inquiry record marker differs')
            point = _position(scenario, branch, pid, at)
            current_checks = [check for check in checks if check['t'] == at and check['patient'] == pid]
            _require(row['resource_check_sequences'] == [check['event_sequence'] for check in current_checks],
                     'inquiry source check references differ from earlier emitted checks')
            allowed = [check['hospital'] for check in current_checks if check['eligible']]
            _require(len(allowed) == len(set(allowed)), 'duplicate candidate checks in one inquiry review')
            pending = [{k: value[k] for k in ('submitted_at', 'hospital', 'patient')}
                       for value in unresolved.values()]
            own_handles = [value for (patient, _), value in active.items() if patient == pid]
            own = [dict(submitted_at=value['submitted_at'], hospital=value['hospital'], patient=pid,
                        travel_seconds=recovery._synthetic_minutes(point, hospitals[value['hospital']]) * 60)
                   for value in own_handles]
            history = [dict(hospital=value['hospital'], patient=value.get('patient'),
                            submitted_at=CLOCK_ORIGIN_SECONDS + value['submitted_at'],
                            observed_at=CLOCK_ORIGIN_SECONDS + value['observed_at'], outcome=value['outcome'])
                       for value in scenario.get('initial_inquiry_history', [])]
            history.extend(feedback)
            episode = row['inquiry_episode']
            _require(type(episode) is int and episode >= current_episode[pid], 'selector episode moved backwards')
            current_episode[pid] = episode
            expected = dict(now=seconds(at), candidates=[dict(hospital_id=target,
                travel=dict(duration_seconds=recovery._synthetic_minutes(point, hospitals[target]) * 60))
                for target in allowed], observations=history, pending=pending, patient_pending=own,
                max_new=max(0, min(3 - len(own), 10 - episode_contacts[pid, episode])),
                last_contact_at=seconds(latest[pid]) if pid in latest else None)
            _require(base._same(row['inputs'], expected),
                     'selector input differs from earlier raw inquiry/feedback/resource-check ledger')
            _require(row['ask_event_sequences'] == [value['ask_event_sequence'] for value in own_handles],
                     'selector active handles differ from original requests')
            reproduced = _selector_reproduce(selector, expected)
            _require(base._same(row['decision'], reproduced), 'actual pure selector did not reproduce decision')
            next_supplement = min((e['event_sequence'] for e in events if e['event_sequence'] > seq
                and e['t'] == at and e['type'] in ('scarce_supplement_review', 'shortage_probe_review', 'cooperative_busy_retry_review') and e.get('patient') == pid), default=float('inf'))
            following = [e for e in events if seq < e['event_sequence'] < next_supplement and e['t'] == at
                         and e['type'] == 'ask' and e.get('patient') == pid]
            _require([e['hospital'] for e in following] == reproduced['send'], 'selector send differs from actual inquiries')
            _require(not covered_asks.intersection(e['event_sequence'] for e in following),
                     'one inquiry was covered by multiple selector decisions')
            covered_asks.update(e['event_sequence'] for e in following)
        if kind == 'condition_update' and pid in truth.patients:
            truth.patients[pid]['capabilities'] = set(event['required_capabilities'])
        truth.apply(event)
    _require(used_records == set(range(len(records))), 'orphan inquiry policy record')
    if selector != 'fixed_3':
        _require(not covered_asks.intersection(supplemental_asks | probe_asks | retry_asks) and not supplemental_asks.intersection(probe_asks | retry_asks) and not probe_asks.intersection(retry_asks) and covered_asks | supplemental_asks | probe_asks | retry_asks ==
                 {e['event_sequence'] for e in events if e['type'] == 'ask'},
                 'actual inquiry lacks a unique reproduced selector decision')
    _require(all(handle['due'] > end for handle in unresolved.values()), 'missing due inquiry feedback')
    expected_late = Counter()
    for source in events:
        if source['type'] != 'ask': continue
        delivery = work['deliveries'][source['event_sequence']]
        if delivery['admitted'] and not delivery['silent'] and delivery['finish'] is not None \
                and delivery['finish'] > source['timeout_at']:
            expected_late[delivery['finish'],'late_reply',source['hospital'],source['patient'],source['attempt'],None] += 1
    actual_late = Counter((event['t'],event['type'],event['hospital'],event['patient'],event['attempt'],None)
                         for event in events if event['type'] == 'late_reply')
    _require(actual_late == expected_late, 'late clinical delivery differs from independently conserved actual job completion')
    return dict(selector_reproductions=len(records), original_inquiry_handles=len(handles),
                fifo_jobs=work['counts']['cooperative_jobs'], current_feedback_receipts=len(feedback))


def audit_ready(scenario, branch):
    """Only real optional work may be excluded from readiness maturity."""
    events = _indexed_events(branch)
    indexed = {event['event_sequence']: event for event in events}
    records = branch['cooperative_ready_records']
    _require(isinstance(records, list), 'nonblocking records must be a list')
    workflow = _ProbeWorkflow(deepcopy(scenario))
    hospitals = {row['id']: row for row in scenario['hospitals']}
    handles, accepted, first_commits, probe_asks, controls = {}, {}, {}, {}, {}
    used, ready_sequences, used_checks, targets = set(), set(), set(), []

    def snapshot(pid, now):
        row = workflow._base_patient(pid)
        compatible = sorted(hid for hid in row['compatible_hospitals'] if all(
            hospitals[hid].get('capacity', {}).get(resource, 0) >= amount
            for resource, amount in row['demand'].items()))
        offers = [dict(hospital=hid, accepted_at=workflow.accepted[pid, hid], expires_at=expiry,
            accept_event_sequence=accepted[pid, hid]['event_sequence'])
            for (patient, hid), expiry in sorted(workflow.offers.items()) if patient == pid and expiry > now]
        base=dict(t=now, patient=dict(id=pid, state=workflow.states[pid], first_commit_t=first_commits.get(pid),
            pending_inquiries=[deepcopy(handle) for (patient, _), handle in handles.items() if patient == pid],
            valid_offers=offers, demand=deepcopy(row['demand'])), probe_asks=deepcopy(probe_asks.get(pid, [])),
            candidates=[dict(hospital=hid, resources=_views('observed', workflow.observations, workflow.truth,
                hid, row['demand'], now)) for hid in compatible])
        acknowledgements=[deepcopy(controls[pid,row['hospital'],row['attempt']]) for row in base['probe_asks']]
        return dict(base_snapshot=base,control_acknowledgements=acknowledgements)

    for event in events:
        kind, now, sequence = event['type'], event['t'], event['event_sequence']
        pid, hid = event.get('patient'), event.get('hospital')
        if kind == 'ask':
            handle = dict(hospital=hid, attempt=event['attempt'], submitted_at=now,
                timeout_at=event['timeout_at'], ask_event_sequence=sequence)
            handles[pid, hid] = handle
            if 'shortage_probe_review_event_sequence' in event:
                probe_asks.setdefault(pid, []).append(deepcopy(handle))
        elif kind == 'cooperative_probe_control_ack':
            controls[pid,hid,event['attempt']]={key:deepcopy(event[key]) for key in (
                'hospital','attempt','ask_event_sequence','job_id','state','received_at')}
            controls[pid,hid,event['attempt']]['control_event_sequence']=sequence
        elif kind == 'cooperative_probe_not_admitted':
            handles.pop((pid,hid),None)
        elif kind in ('accept', 'decline', 'no_response'):
            if not _is_callback_accept(event, indexed):
                handles.pop((pid, hid), None)
            if kind == 'accept':
                accepted[pid, hid] = event
        elif kind in ('commit', 'arrive', 'transfer_search', 'treatment_start', 'backup_closed'):
            handles = {key:value for key, value in handles.items() if key[0] != pid}
            if kind == 'commit': first_commits.setdefault(pid, now)
        elif kind in ('backup_retained', 'backup_arrival_preserved', 'backup_transfer_reused'):
            handles = {key:value for key, value in handles.items() if key[0] != pid}
            for handle in event['pending']:
                handles[pid, handle['hospital']] = deepcopy(handle)
        elif kind == 'shortage_probe_cycle':
            _require(not targets, 'previous nonblocking cycle omitted reviews')
            normal_ready = {row['id'] for row in workflow.snapshot(now)['ready']}
            targets = [patient for patient in sorted(workflow.states)
                if workflow.states[patient] == 'SEARCH' and patient not in first_commits and patient not in normal_ready
                and any(other == patient for other, _ in handles)
                and any(other == patient for other, _ in workflow.offers) and probe_asks.get(patient)]
        elif kind == 'cooperative_probe_ready_review':
            _require(targets and pid == targets.pop(0), 'omitted, duplicated or unsolicited nonblocking review')
            index = event['record_index']
            _require(index == len(used) and index < len(records), 'missing or reordered nonblocking record')
            used.add(index)
            record = records[index]
            _require(set(event) == {'t', 'type', 'event_sequence', 'patient', 'record_index'}
                and set(record) == {'snapshot', 'result', 'event_sequence'} and record['event_sequence'] == sequence,
                'nonblocking review carries unknown fields or different marker')
            expected_snapshot = snapshot(pid, now)
            _require(base._same(record['snapshot'], expected_snapshot),
                     'nonblocking input differs from original pending work, actual probe identity, offers or reports')
            expected = _cooperative_expected(expected_snapshot)
            _require(base._same(record['result'], expected), 'nonblocking rule ignored real ordinary work or current fresh offer')
            if expected['eligible']:
                for offset, hospital in enumerate(expected['options'], 1):
                    check = indexed.get(sequence + offset)
                    resources = next(row['resources'] for row in expected_snapshot['base_snapshot']['candidates'] if row['hospital'] == hospital)
                    _require(check is not None and base._same(check, dict(t=now, type='information_check',
                        event_sequence=sequence + offset, patient=pid, hospital=hospital, purpose='allocation',
                        eligible=True, reason=None, resources=resources)),
                        'nonblocking offer lacks immediate unchanged fresh allocation check')
                    used_checks.add(sequence + offset)
                following_sequence = sequence + len(expected['options']) + 1
                marker = indexed.get(following_sequence)
                _require(marker is not None and base._same(marker, dict(t=now, type='cooperative_probe_nonblocking_ready',
                    event_sequence=following_sequence, patient=pid, options=expected['options'], review_event_sequence=sequence)),
                    'nonblocking review lacks its unique explicitly proven ready marker')
                ready_sequences.add(following_sequence)
            else:
                following = indexed.get(sequence + 1)
                _require(following is None or following['type'] != 'cooperative_probe_nonblocking_ready',
                         'ineligible nonblocking review promoted readiness')
        workflow.apply(event)
    _require(not targets and used == set(range(len(records))), 'orphan or omitted nonblocking record')
    _require(ready_sequences == {row['event_sequence'] for row in events if row['type'] == 'cooperative_probe_nonblocking_ready'},
             'orphan nonblocking readiness marker')
    _require(branch['metrics']['cooperative_nonblocking_ready'] == len(ready_sequences),
             'nonblocking metric differs from raw proven readiness')
    return dict(cooperative_ready_reviews=len(records), cooperative_nonblocking_ready=len(ready_sequences),
                nonblocking_current_allocation_checks=len(used_checks))


def audit_branch(scenario,branch,*,exact_limit=20000):
    enabled=branch.get('cooperative_probe_enabled',False)
    _require(type(enabled) is bool,'cooperative workflow flag is not explicit boolean')
    if not enabled:
        _require(not branch.get('cooperative_probe_spec') and not branch.get('cooperative_job_ledger')
            and not branch.get('cooperative_control_acks') and not branch.get('cooperative_ready_records')
            and not branch.get('cooperative_busy_retry_records')
            and not any(event['type'].startswith('cooperative_') or 'cooperative_job_id' in event
                for event in _indexed_events(branch)), 'disabled reference branch contains cooperative behavior')
        return prior.audit_branch(scenario,branch,exact_limit=exact_limit)
    _require(branch.get('cooperative_probe_spec') == COOPERATIVE_PROBE_SPEC,
             'cooperative workflow differs from the explicit current-control and conserved-work protocol')
    _require(branch.get('shortage_probe_spec')==COOPERATIVE_SHORTAGE_SPEC,
             'cooperative optional-work readiness was incorrectly claimed as unchanged FIFO behavior')
    expected_deadline_spec=dict(late_reply_recovery=True,scope='one_initial_scarce_supplement',
        late_reply_window_minutes=.25,original_timeout_unchanged=True,original_timeout_feedback_unchanged=True,
        blocking_wait_added=False,hospital_work_calendar_unchanged=False,patient_condition_fingerprint_required=True,
        new_offer_ttl_from_actual_receipt=True,actual_reply_outcome_required=True,current_fresh_resource_check_required=True,
        current_valid_offer_rejects=True,newer_same_hospital_attempt_rejects=True)
    _require(branch.get('deadline_recovery_spec')==expected_deadline_spec,
             'cooperative branch changed original clinical deadlines or concealed its changed hospital work calendar')
    _require(branch.get('shortage_probe_enabled') is True and branch.get('late_pending_guard_enabled') is True
        and branch.get('late_reply_recovery_enabled') is True and branch.get('cohort_registration_enabled') is True
        and branch.get('scarce_routing_enabled') is True,'cooperative branch changed its fixed safe-probe foundation')
    ordered=_indexed_events(branch)
    allowed_control_types={'cooperative_job_submitted','cooperative_job_started','cooperative_job_paused',
        'cooperative_job_resumed','cooperative_job_completed','cooperative_probe_control_ack',
        'cooperative_probe_not_admitted','cooperative_probe_ready_review','cooperative_probe_nonblocking_ready',
        'cooperative_busy_retry_review'}
    _require(all(not event['type'].startswith('cooperative_') or event['type'] in allowed_control_types for event in ordered),
             'unknown cooperative control or physical transition type')
    _require(branch.get('ordered_emissions',ordered)==ordered
        and all(joint._clock(event['t']) for event in ordered),'raw emission order or quarter-minute clock differs')
    _require(not branch.get('shortage_probe_nonblocking_records') and not any(
        event['type'] in ('shortage_probe_nonblocking_review','shortage_probe_nonblocking_ready') for event in ordered),
        'legacy nonblocking shortcut bypassed the current completion acknowledgement')
    _require(branch['metrics']['shortage_probe_nonblocking_ready']==0,'legacy optional-readiness count was fabricated')
    work=audit_work(deepcopy(scenario),branch)
    if branch['cooperative_queue_enabled']:
        probe=audit_probes(deepcopy(scenario),branch)
        callback=audit_late_offers(deepcopy(scenario),branch,work)
        inquiries=audit_inquiries(deepcopy(scenario),branch,work)
        backups=audit_backups(deepcopy(scenario),branch,work)
    else:
        _require(not branch['shortage_probe_records'] and not any(
            event.get('shortage_probe_review_event_sequence') is not None for event in ordered),
            'queue-disabled environment gained an optional clinical inquiry')
        _require(not branch.get('cooperative_busy_retry_records'),'queue-disabled environment contains orphan busy retry records')
        probe=dict(shortage_probe_reviews=0,shortage_probe_sent=0)
        callback=safe.audit_late_offers(deepcopy(scenario),branch)
        inquiries=prior.audit_inquiries(deepcopy(scenario),branch)
        backups=safe.audit_backups(deepcopy(scenario),branch)
    ready=audit_ready(deepcopy(scenario),branch)
    result=_audit_information_branch(deepcopy(scenario),branch,exact_limit=exact_limit)
    counts=dict(result['counts'])
    for values in (work['counts'],probe,callback,inquiries,backups,ready,
        scarce.audit_cohorts(deepcopy(scenario),branch),audit_supplements(deepcopy(scenario),branch),
        joint.audit_stability(deepcopy(scenario),branch),recovery.audit_motion(deepcopy(scenario),branch),
        audit_guard(deepcopy(scenario),branch),audit_pending_guard(deepcopy(scenario),branch),
        audit_early_offers(deepcopy(scenario),branch),recovery.audit_no_added_source(deepcopy(scenario),branch)):
        counts.update(values)
    for metric,key in (('cooperative_control_acks','cooperative_control_acks'),
        ('cooperative_probe_not_admitted','cooperative_admission_refusals'),
        ('cooperative_preemptions','cooperative_preemptions')):
        _require(branch['metrics'][metric]==work['counts'][key], 'cooperative metric differs from raw actual work: '+metric)
    waiting=joint.waiting_metrics(deepcopy(scenario),branch)
    for key,value in result['metrics'].items():
        if key in branch['metrics']:
            _require(base._same(branch['metrics'][key],value),'whole-cohort endpoint metric mismatch: '+key)
    return dict(passed=True,counts=counts,waiting=waiting,metrics=result['metrics'],
        information=result['information'],assignment_constraint_violations=0,
        selector_check='pure selector reproduced from original raw public inquiries and delivered reports',
        actual_optimality_claimed=False)


def audit_cooperative_probe_replay(result,*,exact_limit=20000):
    _require(result.get('schema_version')==1 and result.get('experiment_id')==EXPERIMENT_ID,
             'unknown cooperative replay schema or experiment')
    ids=[row['id'] for row in result['branches']]
    _require(ids and len(ids)==len(set(ids)) and len(result['execution_order'])==len(ids)
        and set(result['execution_order'])==set(ids),'missing or duplicated cooperative replay branch')
    counts,details=Counter(),{}
    for branch in result['branches']:
        checked=audit_branch(result['scenario'],branch,exact_limit=exact_limit)
        details[branch['id']]=checked;counts.update(checked['counts'])
    return dict(passed=True,counts=dict(counts),branches=len(ids),branch_audits=details,
        assignment_constraint_violations=0,actual_optimality_claimed=False)
