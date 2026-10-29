"""Projected paired contact-budget review with complete capture and atomic group apply.

Public inputs share a committed prefix. Travel estimation holds no transaction.
Pair and all current tail members are checked before the first write. The frozen horizon adapter
handles unpaired patients and later reviews. Only one run per process is valid.
"""
from contextvars import ContextVar
from copy import deepcopy
from types import SimpleNamespace
import threading

from server import db, inquiry_experiment as ie, inquiry_observations
from server import inquiry_policies, inquiry_workflow, service
from . import harness, horizon_context, horizon_runner, joint_budget_context, joint_budget_policy
from . import joint_reselect_runner as control_runner

NAME = 'eat_joint_budget_l15_f'
JOB_FIELDS = horizon_runner.JOB_FIELDS
REGISTRY_JOB_FIELDS = ('patient_id', 'version', 'started_at', 'deadline_at',
                       'next_evaluation_at', 'enabled', 'policy_name')
NEW_INSTRUMENTATION = dict(
    original_runner='inquiry_lab.runner._branch',
    actual_workflow='inquiry_lab.joint_budget_runner.run_once',
    record_only=False, epoch=ie.EPOCH, public_time_context_added=True,
    current_registered_first_contact_context_added=True, public_tail_first_contact_context_added=True,
    projected_paired_contact_budget_veto_review=True, group_atomic_commit=True,
    maximum_group_size=2, eligibility_order='started_at,patient_id',
    base_horizon_preserved_for_fallback=True)
_RUN_LOCK = threading.Lock()
_GROUP_MEMBER = ContextVar('joint_budget_current_group_member', default=None)


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
        context = joint_budget_context.build_context(now=now,
            event_prefix_max=marks['event_prefix_max'], decision_prefix_max=marks['decision_prefix_max'],
            patients=registry['patients'], jobs=registry['jobs'], inquiries=registry['inquiries'],
            decisions=registry['decisions'], pair_patient_ids=pair)
        events = db.rows(conn.execute('SELECT * FROM events WHERE id<=? ORDER BY id',
                                     (marks['event_prefix_max'],)))
        members, ledgers = [], []
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
    group = dict(now=now,**marks,context=context,registry_snapshot=registry,members=members[:2],tail_members=members[2:])
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
    context = joint_budget_context.build_context(now=now,event_prefix_max=group['event_prefix_max'],
        decision_prefix_max=group['decision_prefix_max'],**registry,pair_patient_ids=pair)
    if context!=group['context']:
        return False
    joint_budget_context.validate_tail_members(group['context'],group['tail_members'],members=group['members'])
    for member in all_members:
        matches,job,patient = inquiry_workflow._matches(conn,member['workflow_snapshot']['apply_snapshot'])
        if not matches or {k:job[k] for k in JOB_FIELDS}!=member['workflow_snapshot']['job']:
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
    return True


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
            result = joint_budget_policy.decide_joint(NAME,members=group['members'],joint_context=group['context'],
                tail_members=group['tail_members'])
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
        return joint_budget_policy.decide(name,**kwargs)

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
        horizon_runner.horizon_policy = SimpleNamespace(NAME=NAME,spec=joint_budget_policy.spec,decide=decide)
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
