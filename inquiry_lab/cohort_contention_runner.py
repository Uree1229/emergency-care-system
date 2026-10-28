"""Public owner-aware conditional cohort choice with captured public priorities and atomic group apply.

Public inputs share a committed prefix. Travel estimation holds no transaction.
Pair and all current tail members are checked before the first write. The frozen horizon adapter
handles unpaired patients and later reviews. Only one run per process is valid.
"""
from contextvars import ContextVar
from copy import deepcopy
from types import SimpleNamespace
import threading
import hashlib
from pathlib import Path

from server import db, inquiry_experiment as ie, inquiry_observations
from server import inquiry_policies, inquiry_workflow, service
from . import harness, horizon_context, horizon_runner, cohort_contention_context, cohort_contention_policy
from . import cohort_followplan_runner as control_runner

DERIVED_RUNNER_SOURCE_SHA256 = 'cf2a3456af2c12a9183801af545ef3652c536928a9f7e775674f600e9d7c7701'
if hashlib.sha256(Path(control_runner.__file__).read_bytes()).hexdigest()!=DERIVED_RUNNER_SOURCE_SHA256:
    raise ValueError('frozen followplan runner foundation differs')
NAME = 'eat_cohort_contention_l15_f'
JOB_FIELDS = horizon_runner.JOB_FIELDS
REGISTRY_JOB_FIELDS = ('patient_id', 'version', 'started_at', 'deadline_at',
                       'next_evaluation_at', 'enabled', 'policy_name')
NEW_INSTRUMENTATION = dict(
    original_runner='inquiry_lab.runner._branch',
    actual_workflow='inquiry_lab.cohort_contention_runner.run_once',
    record_only=False, epoch=ie.EPOCH, public_time_context_added=True,
    current_registered_first_contact_context_added=True, public_tail_first_contact_context_added=True,
    public_patient_priority_context_added=True, row_specific_reference_continuation_menu=True,
    public_complete_waiting_owner_context_added=True, origin_clock_owner_hedge_surrogate=True, group_atomic_commit=True,
    first_contact_virtual_queued_work_cancellation_model=True,
    first_contact_static_H_continuation_added=True,
    first_future_interowner_workahead=True,
    existing_pending_and_integer_concurrency_unchanged=True,
    maximum_group_size=2, eligibility_order='started_at,patient_id',
    base_horizon_preserved_for_fallback=True)
_RUN_LOCK = threading.Lock()
_GROUP_MEMBER = ContextVar('cohort_contention_current_group_member', default=None)


def _watermarks(conn):
    return dict(event_prefix_max=conn.execute('SELECT COALESCE(MAX(id),0) FROM events').fetchone()[0],
        decision_prefix_max=conn.execute('SELECT COALESCE(MAX(id),0) FROM inquiry_policy_decisions').fetchone()[0],
        revision=inquiry_workflow._revision(conn))


def _registry(conn):
    return dict(
        patients=db.rows(conn.execute('SELECT id,state,version FROM patients ORDER BY id')),
        jobs=db.rows(conn.execute('SELECT '+','.join(REGISTRY_JOB_FIELDS)+
                                 ' FROM inquiry_workflows ORDER BY started_at,patient_id')),
        inquiries=db.rows(conn.execute('SELECT patient_id FROM inquiries ORDER BY id')),
        decisions=db.rows(conn.execute('SELECT id,at,patient_id FROM inquiry_policy_decisions ORDER BY id')))


def _priority_row(patient):
    """Keep reported values and their current public effective-priority rule."""
    return dict(patient_id=patient['id'],patient_version=patient['version'],
        reported_priority=patient['priority'],priority_source=patient['priority_source'],
        effective_priority=service.effective_priority(patient))


def _created_events(events):
    return [{k:event[k] for k in ('id','at','type','patient_id')}
            for event in events if event['type']=='patient_created']


def _owner_ids(registry, events, now):
    births = {event['patient_id']:event for event in _created_events(events)}
    result = []
    for patient in registry['patients']:
        if patient['state']!='WAITING':
            continue
        event = births.get(patient['id'])
        if event is None:
            raise ValueError('WAITING owner lacks a public creation event')
        if event['at']<=now<event['at']+300:
            result.append(patient['id'])
    return sorted(result,key=lambda pid:(type(pid).__name__,str(pid)))


def _owner_source(conn, now, pid, marks, birth):
    """Pure current DB/ledger read, including disabled and missing jobs."""
    patient = service._patient(conn,pid)
    job = inquiry_workflow._job(conn,pid)
    inquiries = inquiry_workflow._inquiries(conn,pid)
    ledger = inquiry_observations.capture(conn,now,pid)
    priority = _priority_row(patient)
    row = dict(patient=dict(id=pid,state=patient['state'],version=patient['version'],
            **{k:priority[k] for k in ('reported_priority','priority_source','effective_priority')}),
        patient_created_event=deepcopy(birth),evaluation_end_at=birth['at']+300,
        job=None if job is None else {k:job[k] for k in REGISTRY_JOB_FIELDS},
        inquiries=[{k:q[k] for k in ('id','patient_id','hospital_id','state','last_sent_at')} for q in inquiries],
        valid_acceptance_ids=inquiry_workflow._valid_acceptances(conn,now,patient,inquiries),
        inputs=dict(now=now,observations=deepcopy(ledger['observations']),pending=deepcopy(ledger['pending']),
            max_new=max(0,10-len({q['hospital_id'] for q in inquiries})),
            last_contact_at=max((q['last_sent_at'] for q in inquiries if q['last_sent_at']<=now),default=None)),
        apply_snapshot=dict(patient_id=pid,patient_version=patient['version'],
            job_version=None if job is None else job['version'],revision=marks['revision'],now=now))
    return row,ledger


def _owner_source_snapshot(conn, now, owner_ids, marks):
    """Full current DB provenance, guarded but never given to the policy."""
    return dict(schema_version=1,now=now,**marks,
        rows=[dict(patient_id=pid,
            patient=db.row(conn.execute('SELECT * FROM patients WHERE id=?',(pid,)).fetchone()),
            job=inquiry_workflow._job(conn,pid),inquiries=inquiry_workflow._inquiries(conn,pid)) for pid in owner_ids],
        events=db.rows(conn.execute('SELECT * FROM events WHERE id<=? AND at<=? ORDER BY id',
            (marks['event_prefix_max'],now))))


def _owner_offers(conn, now, owner, ledger, estimator):
    """Estimate outside the inspection; own pending uses all eligible offers."""
    if conn.in_transaction:
        raise AssertionError('owner travel estimation would run inside a transaction')
    pid=owner['patient']['id'];offers=service.candidates(conn,now,pid,estimator)
    patient=service._patient(conn,pid)
    eligible=[c for c in offers['candidates'] if inquiry_workflow._eligible_now(conn,now,patient,c['hospital_id'])]
    contacted={q['hospital_id'] for q in owner['inquiries']}
    travel={c['hospital_id']:c['travel']['duration_seconds'] for c in eligible}
    owner['inputs'].update(candidates=deepcopy([c for c in eligible if c['hospital_id'] not in contacted]),
        patient_pending=[dict(q,travel_seconds=travel[q['hospital']]) for q in ledger['patient_pending'] if q['hospital'] in travel])


def _owners_still_current(conn, now, group, registry):
    context=group['owner_context']
    cohort_contention_context.validate_owner_context(context,joint_context=group['context'],members=group['members'],
        tail_members=group['tail_members'],priority_context=group['priority_context'])
    events=db.rows(conn.execute('SELECT * FROM events WHERE id<=? ORDER BY id',(group['event_prefix_max'],)))
    if context['patient_registry']!=registry['patients'] or context['patient_created_events']!=_created_events(events):
        return False
    if [row['patient']['id'] for row in context['inventory']]!=_owner_ids(registry,events,now):
        return False
    marks={k:group[k] for k in ('event_prefix_max','decision_prefix_max','revision')}
    if group['owner_source_snapshot']!=_owner_source_snapshot(conn,now,
            [row['patient']['id'] for row in context['inventory']],marks):
        return False
    for owner in context['inventory']:
        current,ledger=_owner_source(conn,now,owner['patient']['id'],marks,owner['patient_created_event'])
        if any(current[k]!=owner[k] for k in ('patient','patient_created_event','evaluation_end_at','job','inquiries','valid_acceptance_ids','apply_snapshot')):
            return False
        if any(current['inputs'][k]!=owner['inputs'][k] for k in ('now','observations','pending','max_new','last_contact_at')):
            return False
        patient=service._patient(conn,owner['patient']['id'])
        if any(not inquiry_workflow._eligible_now(conn,now,patient,c['hospital_id']) for c in owner['inputs']['candidates']):
            return False
        pending=owner['inputs']['patient_pending']
        expected=[{k:v for k,v in q.items() if k!='travel_seconds'} for q in pending]
        if any(q not in ledger['patient_pending'] for q in expected):
            return False
        if any(not inquiry_workflow._eligible_now(conn,now,patient,q['hospital']) for q in pending):
            return False
    return True


def _eligible_ids(registry, now):
    patients = {row['id']:row for row in registry['patients']}
    queried = {row['patient_id'] for row in registry['inquiries']}
    decided = {row['patient_id'] for row in registry['decisions']}
    eligible = [job for job in registry['jobs']
        if job['patient_id'] in patients and patients[job['patient_id']]['state']=='WAITING'
        and job['enabled']==1 and job['policy_name']==NAME
        and job['started_at']<=now and job['next_evaluation_at']<=now<job['deadline_at']
        and job['patient_id'] not in queried and job['patient_id'] not in decided]
    return [job['patient_id'] for job in sorted(eligible,key=lambda row:(row['started_at'],row['patient_id']))]


def _prepare_group(conn, now, estimator):
    """Read common identity/ledger, estimate unlocked, then recheck the prefix."""
    if conn.in_transaction:
        raise ValueError('group preparation requires an idle connection')
    conn.execute('BEGIN')
    try:
        registry = _registry(conn)
        pair = _eligible_ids(registry, now)[:2]
        if len(pair)!=2:
            return None
        marks = _watermarks(conn)
        context = cohort_contention_context.build_context(now=now,
            event_prefix_max=marks['event_prefix_max'], decision_prefix_max=marks['decision_prefix_max'],
            patients=registry['patients'], jobs=registry['jobs'], inquiries=registry['inquiries'],
            decisions=registry['decisions'], pair_patient_ids=pair)
        events = db.rows(conn.execute('SELECT * FROM events WHERE id<=? ORDER BY id',
                                     (marks['event_prefix_max'],)))
        births={event['patient_id']:event for event in _created_events(events)}
        owner_ids=_owner_ids(registry,events,now)
        owner_sources=[_owner_source(conn,now,pid,marks,births[pid]) for pid in owner_ids]
        owner_source_snapshot=_owner_source_snapshot(conn,now,owner_ids,marks)
        members, ledgers, captured_priorities = [], [], []
        for pid in _eligible_ids(registry,now):
            job = inquiry_workflow._job(conn,pid)
            patient = service._patient(conn,pid)
            policy = inquiry_workflow._policy(job)
            snapshot = dict(patient_id=pid,patient_version=patient['version'],
                job_version=job['version'],revision=marks['revision'],now=now)
            public_job = {k:job[k] for k in JOB_FIELDS}
            time_context = horizon_context.build_context(now=now,patient_id=pid,
                job_version=job['version'],event_prefix_max=marks['event_prefix_max'],
                decision_prefix_max=marks['decision_prefix_max'],job=public_job,events=events)
            members.append(dict(patient_id=pid,inputs=dict(now=now,time_context=time_context,
                max_new=policy['max_contacts'],last_contact_at=None),
                workflow_snapshot=dict(apply_snapshot=snapshot,job=public_job)))
            ledgers.append(inquiry_observations.capture(conn,now,pid))
            captured_priorities.append(_priority_row(patient))
    finally:
        conn.rollback()
    # service.candidates may call an external estimator, outside any transaction.
    for member,ledger in zip(members,ledgers):
        if conn.in_transaction:
            raise AssertionError('travel estimator would run inside a transaction')
        offers = service.candidates(conn,now,member['patient_id'],estimator)
        patient = service._patient(conn,member['patient_id'])
        eligible = [c for c in offers['candidates']
                    if inquiry_workflow._eligible_now(conn,now,patient,c['hospital_id'])]
        travel = {c['hospital_id']:c['travel']['duration_seconds'] for c in eligible}
        member['inputs'].update(candidates=deepcopy(eligible),observations=deepcopy(ledger['observations']),
            pending=deepcopy(ledger['pending']),patient_pending=[dict(q,travel_seconds=travel[q['hospital']])
                for q in ledger['patient_pending'] if q['hospital'] in travel])
    priority_context = cohort_contention_context.build_priority_context(joint_context=context,
        members=members[:2],tail_members=members[2:],priority_rows=[
            {k:v for k,v in row.items() if k!='effective_priority'} for row in captured_priorities])
    if priority_context['patients']!=captured_priorities:
        raise AssertionError('captured effective priorities differ from the current public rule')
    first_by={member['patient_id']:member for member in members}
    for owner,ledger in owner_sources:
        first=first_by.get(owner['patient']['id'])
        if first is None:
            _owner_offers(conn,now,owner,ledger,estimator)
        else:
            owner['inputs']=deepcopy({k:v for k,v in first['inputs'].items() if k!='time_context'})
    owner_context=cohort_contention_context.build_owner_context(joint_context=context,members=members[:2],tail_members=members[2:],
        priority_context=priority_context,inventory_rows=[owner for owner,_ in owner_sources],
        registry_patients=registry['patients'],events=events)
    group = dict(now=now,**marks,context=context,registry_snapshot=registry,members=members[:2],
        tail_members=members[2:],priority_context=priority_context,owner_context=owner_context,
        owner_source_snapshot=owner_source_snapshot)
    conn.execute('BEGIN')
    try:
        if not _group_still_current(conn,now,group):
            return None
    finally:
        conn.rollback()
    return group


def _group_still_current(conn, now, group):
    """Check the complete group before writes can change the shared revision."""
    if now!=group['now'] or _watermarks(conn)!={k:group[k] for k in
            ('event_prefix_max','decision_prefix_max','revision')}:
        return False
    registry = _registry(conn)
    if registry!=group['registry_snapshot']:
        return False
    pair = [m['patient_id'] for m in group['members']]
    if len(pair)!=2 or len(set(pair))!=2 or _eligible_ids(registry,now)[:2]!=pair:
        return False
    all_members = group['members']+group['tail_members']
    if [m['patient_id'] for m in all_members]!=_eligible_ids(registry,now):
        return False
    context = cohort_contention_context.build_context(now=now,event_prefix_max=group['event_prefix_max'],
        decision_prefix_max=group['decision_prefix_max'],**registry,pair_patient_ids=pair)
    if context!=group['context']:
        return False
    cohort_contention_context.validate_tail_members(group['context'],group['tail_members'],members=group['members'])
    priorities = cohort_contention_context.validate_priority_context(group['priority_context'],
        joint_context=context,members=group['members'],tail_members=group['tail_members'])
    for member,priority in zip(all_members,priorities['patients']):
        matches,job,patient = inquiry_workflow._matches(conn,member['workflow_snapshot']['apply_snapshot'])
        if not matches or {k:job[k] for k in JOB_FIELDS}!=member['workflow_snapshot']['job']:
            return False
        # Public events do not carry general expert priority values. Compare
        # the captured values themselves, even if versions/watermarks agree.
        if _priority_row(patient)!=priority:
            return False
        if not job['next_evaluation_at']<=now<job['deadline_at'] or job['policy_name']!=NAME:
            return False
        if inquiry_workflow._inquiries(conn,member['patient_id']):
            return False
        if conn.execute('SELECT 1 FROM inquiry_policy_decisions WHERE patient_id=? LIMIT 1',
                        (member['patient_id'],)).fetchone() is not None:
            return False
        inquiry_workflow._policy(job)
        if any(not inquiry_workflow._eligible_now(conn,now,patient,c['hospital_id'])
               for c in member['inputs']['candidates']):
            return False
    return _owners_still_current(conn,now,group,registry)


def _validate_outputs(group, outputs):
    if type(outputs) is not list or len(outputs)!=2:
        raise ValueError('joint policy must return exactly two ordered decisions')
    for member,decision in zip(group['members'],outputs):
        if type(decision) is not dict or set(decision)!={'send','reason','evidence','next_review_at'}:
            raise ValueError('invalid joint decision contract')
        send = decision['send']
        offered = {c['hospital_id'] for c in member['inputs']['candidates']}
        if (type(send) is not list or len(set(send))!=len(send) or not set(send)<=offered
                or len(send)>min(4,member['inputs']['max_new'])
                or type(decision['next_review_at']) is not int or decision['next_review_at']<=group['now']):
            raise ValueError('joint policy returned an infeasible current batch')


def _write_member(conn, now, member, decision):
    """Apply an already validated member inside the outer group transaction."""
    pid = member['patient_id']
    job = inquiry_workflow._job(conn,pid)
    policy = inquiry_workflow._policy(job)
    inquiry_workflow._record_decision(conn,now,job,policy,decision)
    decision_id = conn.execute('SELECT MAX(id) FROM inquiry_policy_decisions').fetchone()[0]
    if not decision['send']:
        inquiry_workflow._exhaust(conn,now,job,'NO_FRESH_CANDIDATES',
            '현재 조건에서 새로 문의할 후보가 없습니다. 미응답은 거절로 처리하지 않으며 담당자의 확인이 필요합니다.')
        return decision_id
    service._send_inquiries(conn,now,pid,decision['send'],actor='inquiry_worker')
    conn.execute("UPDATE inquiry_workflows SET state='RUNNING',round_no=round_no+1,next_evaluation_at=?,"
        'updated_at=?,version=version+1,last_error=NULL,stop_reason=NULL WHERE patient_id=?',
        (decision['next_review_at'],now,pid))
    service.log(conn,now,'system','inquiry_workflow_batch',pid,hospital_ids=decision['send'],
        round_no=job['round_no']+1,next_evaluation_at=decision['next_review_at'],
        policy_name=policy['name'],policy_label=policy['label'],reason=decision['reason'])
    return decision_id


def _apply_group(conn, now, group, result, recorder=None):
    """Commit both members or none; any member failure rolls back prior writes."""
    outputs = result['decisions']
    _validate_outputs(group,outputs)
    ids = []
    with db.transaction(conn):
        if not _group_still_current(conn,now,group):
            return None
        # No snapshot/eligibility checks happen after writes begin: member one
        # changes the shared revision. Both original snapshots passed above.
        for index,(member,decision) in enumerate(zip(group['members'],outputs)):
            if recorder is not None:
                token = _GROUP_MEMBER.set(dict(conn=conn,group=group,member=member,
                    decision=decision,index=index))
                try:
                    seven = {k:v for k,v in member['inputs'].items() if k!='time_context'}
                    if recorder(NAME,**seven)!=decision:
                        raise AssertionError('recorded group policy differs from joint decision')
                finally:
                    _GROUP_MEMBER.reset(token)
            ids.append(_write_member(conn,now,member,decision))
    return dict(group,outputs=deepcopy(outputs),evidence=deepcopy(result['evidence']),
                db_decision_ids=ids,validated_before_write=True)


def run_once(path, estimator, clock, *, fallback_run_once, groups):
    """Pair current first contacts, then use frozen run_once for remaining work."""
    conn = db.connect(path)
    changed = False
    try:
        while True:
            now = int(clock())
            group = _prepare_group(conn,now,estimator)
            if group is None:
                break
            original = deepcopy(group)
            result = cohort_contention_policy.decide_joint(NAME,members=group['members'],joint_context=group['context'],
                tail_members=group['tail_members'],priority_context=group['priority_context'],owner_context=group['owner_context'])
            if group!=original:
                raise AssertionError('joint policy mutated captured public inputs')
            record = _apply_group(conn,int(clock()),group,result,recorder=inquiry_policies.decide)
            if record is None:
                break
            record['id'] = len(groups)+1
            groups.append(record)
            changed = True
    finally:
        conn.close()
    return fallback_run_once(path,estimator,clock=clock) or changed


def _candidate_scenario(scenario, arm):
    groups = []
    old_policy, old_capture = horizon_runner.horizon_policy, horizon_runner._capture_time_context
    old_run_once = inquiry_workflow.run_once

    def decide(name, **kwargs):
        active = _GROUP_MEMBER.get()
        if active is not None:
            if kwargs!=active['member']['inputs']:
                raise AssertionError('group recorded inputs differ from common snapshot')
            return deepcopy(active['decision'])
        return cohort_contention_policy.decide(name,**kwargs)

    def capture(inspection, active_apply, now):
        active = _GROUP_MEMBER.get()
        if active is None:
            return old_capture(inspection,active_apply,now)
        if not active['conn'].in_transaction or now!=active['group']['now']:
            raise ValueError('group capture is outside its guarded transaction')
        member = active['member']
        return deepcopy(member['inputs']['time_context']),deepcopy(member['workflow_snapshot'])

    def grouped_run_once(path, estimator, clock):
        return run_once(path,estimator,clock,fallback_run_once=old_run_once,groups=groups)

    try:
        horizon_runner.horizon_policy = SimpleNamespace(NAME=NAME,spec=cohort_contention_policy.spec,decide=decide)
        horizon_runner._capture_time_context = capture
        inquiry_workflow.run_once = grouped_run_once
        replay = horizon_runner.run_scenario(scenario,[arm])
        branch = replay['branches'][0]
        branch['joint_groups'] = groups
        branch['instrumentation'] = deepcopy(NEW_INSTRUMENTATION)
        by_decision = {did:(g['id'],i) for g in groups for i,did in enumerate(g['db_decision_ids'])}
        for call in branch['policy_calls']:
            link = by_decision.get(call['db_decision_id'])
            if link is not None:
                call.update(joint_group_id=link[0],joint_member_index=link[1])
        return replay
    finally:
        inquiry_workflow.run_once = old_run_once
        horizon_runner._capture_time_context = old_capture
        horizon_runner.horizon_policy = old_policy


def run_scenario(scenario, arms):
    """Controls delegate unchanged; only the candidate uses the new adapter."""
    arms = [tuple(a) for a in arms]
    if not arms or len(arms)!=len(set(arms)):
        raise ValueError('unique arms required')
    if not _RUN_LOCK.acquire(blocking=False):
        raise RuntimeError('only one joint first-contact runner may be active per process')
    try:
        replays = [(_candidate_scenario(scenario,arm) if arm[0]==NAME else
                    control_runner.run_scenario(scenario,[arm])) for arm in arms]
        first = replays[0]
        if any(r['scenario']!=first['scenario'] or r['initial_snapshot_sha256']!=first['initial_snapshot_sha256']
               or r['environment_sha256']!=first['environment_sha256'] for r in replays):
            raise AssertionError('branches did not share their original snapshot')
        return dict(first,arms=[list(a) for a in arms],branches=[r['branches'][0] for r in replays])
    finally:
        _RUN_LOCK.release()


def run_task(task):
    kind,seed,arms = task
    return run_scenario(harness.build(kind,seed),arms)
