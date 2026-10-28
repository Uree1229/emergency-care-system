"""Copied native identity projection into the immutable Followplan contract."""
from copy import deepcopy
import math
from . import cohort_tailwait_context as original

POLICY_NAME='eat_cohort_chainplanwake_l15_f'
BASE_POLICY_NAME='eat_cohort_tailwait_l15_f'
JOB_FIELDS=original.JOB_FIELDS
REGISTRY_JOB_FIELDS=original.REGISTRY_JOB_FIELDS
PRIORITY_SOURCE=original.PRIORITY_SOURCE
OWNER_SOURCE=original.OWNER_SOURCE
TIERS=original.TIERS
INPUT_FIELDS=original.INPUT_FIELDS
effective_priority=original.effective_priority
_key=original._key


def base_context_projection(value):
    result=deepcopy(value)
    if type(result) is not dict or type(result.get('eligible')) is not list:raise ValueError('pareto first roster is missing')
    for row in result['eligible']:
        if row['job']['policy_name']!=POLICY_NAME:raise ValueError('pareto first roster identity differs')
        row['job']['policy_name']=BASE_POLICY_NAME
    return result


def base_members_projection(members):
    result=deepcopy(members)
    for row in result:
        if row['workflow_snapshot']['job']['policy_name']!=POLICY_NAME:raise ValueError('pareto first member identity differs')
        row['workflow_snapshot']['job']['policy_name']=BASE_POLICY_NAME
    return result


def base_owner_projection(value):
    result=deepcopy(value)
    for row in result['inventory']:
        job=row['job']
        if job is not None:
            if job['policy_name']==POLICY_NAME:job['policy_name']=BASE_POLICY_NAME
            elif job['policy_name']==BASE_POLICY_NAME:job['policy_name']='__foreign_cohort_tailwait__'
    return result


def validate_context(value,*,now=None,event_prefix_max=None,decision_prefix_max=None):
    original.validate_context(base_context_projection(value),now=now,event_prefix_max=event_prefix_max,decision_prefix_max=decision_prefix_max)
    return deepcopy(value)


def build_context(*,now,event_prefix_max,decision_prefix_max,patients,jobs,inquiries,decisions,pair_patient_ids=None):
    projected=deepcopy(jobs)
    for job in projected:job['policy_name']=BASE_POLICY_NAME if job['policy_name']==POLICY_NAME else '__not_cohort_chainplanwake__'
    value=original.build_context(now=now,event_prefix_max=event_prefix_max,decision_prefix_max=decision_prefix_max,
        patients=patients,jobs=projected,inquiries=inquiries,decisions=decisions,pair_patient_ids=pair_patient_ids)
    for row in value['eligible']:row['job']['policy_name']=POLICY_NAME
    return validate_context(value)


def validate_tail_members(joint_context,tail_members,*,members):
    validate_context(joint_context)
    return original.validate_tail_members(base_context_projection(joint_context),base_members_projection(tail_members),members=base_members_projection(members))


def build_priority_context(*,joint_context,members,tail_members,priority_rows):
    validate_context(joint_context)
    return original.build_priority_context(joint_context=base_context_projection(joint_context),members=base_members_projection(members),tail_members=base_members_projection(tail_members),priority_rows=priority_rows)


def validate_priority_context(value,*,joint_context,members,tail_members):
    validate_context(joint_context)
    return original.validate_priority_context(value,joint_context=base_context_projection(joint_context),members=base_members_projection(members),tail_members=base_members_projection(tail_members))


def validate_owner_context(value,*,joint_context,members,tail_members,priority_context):
    original.validate_owner_context(base_owner_projection(value),joint_context=base_context_projection(joint_context),
        members=base_members_projection(members),tail_members=base_members_projection(tail_members),priority_context=priority_context)
    return deepcopy(value)


def build_owner_context(*,joint_context,members,tail_members,priority_context,inventory_rows,registry_patients,events):
    value={k:deepcopy(priority_context[k]) for k in ('now','event_prefix_max','decision_prefix_max','revision')}
    value.update(schema_version=1,source=OWNER_SOURCE,first_cohort_patient_ids=[m['patient_id'] for m in members+tail_members],
        patient_registry=deepcopy(registry_patients),patient_created_events=[{k:e[k] for k in ('id','at','type','patient_id')}
            for e in events if e['id']<=value['event_prefix_max'] and e['type']=='patient_created'],inventory=deepcopy(inventory_rows))
    return validate_owner_context(value,joint_context=joint_context,members=members,tail_members=tail_members,priority_context=priority_context)


WAKE_RULE='new_public_own_terminal_decline_after_last_recorded_decision_prefix_before_existing_review'
WAKE_FIELDS={'schema_version','rule','now','patient_id','capture_snapshot','captured_job','latest_decision','event_prefix_max','decision_prefix_max','remaining_open_inquiry_ids','terminal_declines','activation_context'}
DECLINE_EVENT_FIELDS=('id','at','type','patient_id','hospital_id')
DECLINE_INQUIRY_FIELDS=('id','patient_id','hospital_id','state','responded_at','last_sent_at','version')


def _finite(value):
    return type(value) in (int,float) and math.isfinite(value) and value>=0


def previous_prefix(decision,*,patient_id,started_at,now):
    """Fail closed on missing legacy evidence; never infer ordering from time alone."""
    if type(decision) is not dict or decision.get('policy_name')!=POLICY_NAME:return None
    if type(decision.get('patient_id')) is not type(patient_id) or decision['patient_id']!=patient_id:return None
    if type(decision.get('id')) is not int or decision['id']<=0:return None
    if not _finite(decision.get('at')) or not started_at<=decision['at']<=now:return None
    try:value=decision['evidence']['time_horizon']['context']
    except (KeyError,TypeError):return None
    if type(value) is not dict or type(value.get('patient_id')) is not type(patient_id) or value['patient_id']!=patient_id:return None
    prefix=value.get('event_prefix_max')
    return prefix if type(prefix) is int and prefix>=0 else None


def build_wake_context(*,now,snapshot,job,latest_decision,event_prefix_max,decision_prefix_max,inquiries,events,activation_context=None):
    pid=job['patient_id'];old_due=job['next_evaluation_at']
    if not _finite(old_due) or not now<old_due:return None
    prefix=previous_prefix(latest_decision,patient_id=pid,started_at=job['started_at'],now=now)
    if prefix is None:return None
    remaining=[q['id'] for q in inquiries if q['state'] in ('PENDING','CHECKING')]
    if not remaining:return None
    by_id={q['id']:q for q in inquiries};declines=[]
    for event in events:
        if event.get('type')!='inquiry_responded' or not (type(event.get('id')) is int and prefix<event['id']<=event_prefix_max):continue
        if not _finite(event.get('at')) or event['at']>now or type(event.get('patient_id')) is not type(pid) or event['patient_id']!=pid:continue
        data=event.get('data');qid=data.get('inquiry_id') if type(data) is dict else None
        if type(qid) is not int or data.get('action')!='decline':continue
        q=by_id.get(qid)
        if q is None or q['state']!='DECLINED' or q['hospital_id']!=event.get('hospital_id') or q['responded_at']!=event['at'] or not q['last_sent_at']<=event['at']:continue
        declines.append(dict(event={**{k:deepcopy(event[k]) for k in DECLINE_EVENT_FIELDS},'data':dict(inquiry_id=qid,action='decline')},
            inquiry={k:deepcopy(q[k]) for k in DECLINE_INQUIRY_FIELDS}))
    if not declines:return None
    value=dict(schema_version=1,rule=WAKE_RULE,now=now,patient_id=pid,capture_snapshot=deepcopy(snapshot),
        captured_job={k:deepcopy(job[k]) for k in REGISTRY_JOB_FIELDS},
        latest_decision=dict(id=latest_decision['id'],at=latest_decision['at'],patient_id=pid,policy_name=latest_decision['policy_name'],
            job_version=latest_decision['job_version'],previous_event_prefix_max=prefix),
        event_prefix_max=event_prefix_max,decision_prefix_max=decision_prefix_max,
        remaining_open_inquiry_ids=list(remaining),terminal_declines=declines,
        activation_context=deepcopy(activation_context))
    return validate_wake_context(value,now=now,patient_id=pid)


def validate_wake_context(value,*,now,patient_id):
    if type(value) is not dict or set(value)!=WAKE_FIELDS or value['schema_version']!=1 or type(value['schema_version']) is not int or value['rule']!=WAKE_RULE:
        raise ValueError('unknown public terminal-decline wake context')
    if type(now) is not int or now<0 or value['now']!=now or type(value['patient_id']) is not type(patient_id) or value['patient_id']!=patient_id:
        raise ValueError('terminal-decline wake identity/clock differs')
    job=value['captured_job'];snapshot=value['capture_snapshot'];last=value['latest_decision']
    if set(job)!=set(REGISTRY_JOB_FIELDS) or job['policy_name']!=POLICY_NAME or job['patient_id']!=patient_id or job['enabled']!=1:
        raise ValueError('wake job projection differs')
    if set(snapshot)!={'patient_id','patient_version','job_version','revision','now'} or snapshot['patient_id']!=patient_id or snapshot['now']!=now or snapshot['job_version']!=job['version']:
        raise ValueError('wake apply snapshot differs')
    if not _finite(job['next_evaluation_at']) or not now<job['next_evaluation_at'] or not job['started_at']<=now<job['deadline_at']:
        raise ValueError('wake must be strictly before an existing eligible review')
    if set(last)!={'id','at','patient_id','policy_name','job_version','previous_event_prefix_max'} or last['policy_name']!=POLICY_NAME or last['patient_id']!=patient_id or not job['started_at']<=last['at']<=now:
        raise ValueError('wake prior recorded decision differs')
    for v in (value['event_prefix_max'],value['decision_prefix_max'],last['id'],last['previous_event_prefix_max']):
        if type(v) is not int or v<0:raise ValueError('wake prefixes must be integer public IDs')
    if last['id']<=0 or last['id']>value['decision_prefix_max'] or last['previous_event_prefix_max']>value['event_prefix_max']:
        raise ValueError('wake prefix ordering differs')
    ids=value['remaining_open_inquiry_ids']
    if type(ids) is not list or not ids or len(set(ids))!=len(ids) or any(type(x) is not int or x<=0 for x in ids):raise ValueError('wake needs remaining own open inquiries')
    rows=value['terminal_declines']
    if type(rows) is not list or not rows:raise ValueError('wake needs an unconsumed public own decline')
    seen=set()
    for row in rows:
        if type(row) is not dict or set(row)!={'event','inquiry'}:raise ValueError('wake decline source differs')
        event=row['event'];q=row['inquiry']
        if set(event)!=set(DECLINE_EVENT_FIELDS)|{'data'} or set(q)!=set(DECLINE_INQUIRY_FIELDS):raise ValueError('wake decline projection fields differ')
        if type(event['id']) is not int or not last['previous_event_prefix_max']<event['id']<=value['event_prefix_max'] or event['id'] in seen:raise ValueError('wake decline event ordering differs')
        seen.add(event['id'])
        if event['type']!='inquiry_responded' or event['patient_id']!=patient_id or type(event['patient_id']) is not type(patient_id) or not _finite(event['at']) or event['at']>now:
            raise ValueError('wake decline is foreign or future')
        if event['data']!=dict(inquiry_id=q['id'],action='decline') or type(q['id']) is not int or q['id']<=0 or q['id'] in ids:
            raise ValueError('wake decline inquiry identity differs')
        if q['patient_id']!=patient_id or q['hospital_id']!=event['hospital_id'] or q['state']!='DECLINED' or q['responded_at']!=event['at'] or not _finite(q['last_sent_at']) or q['last_sent_at']>event['at']:
            raise ValueError('wake decline does not match a current terminal inquiry')
    activation=value['activation_context']
    if activation is not None:
        validate_activation_context(activation,now=now,patient_id=patient_id,
            snapshot=value['capture_snapshot'],job=value['captured_job'],
            event_prefix_max=value['event_prefix_max'],decision_prefix_max=value['decision_prefix_max'])
    return deepcopy(value)


ACTIVATION_RULE='current_workflow_public_committed_distinct_future_wave_seed_allows_later_own_decline_prefetch'
CHAIN_PREFETCH_RULE='own_decline_wake_with_empty_original_H_send_may_advance_first_positive_native_future_plan_if_distinct_earliest_wave_or_current_workflow_committed_distinct_wave_seed_and_existing_public_guards'
ACTIVATION_EVENTS=('inquiry_workflow_started','inquiry_workflow_paused','inquiry_policy_changed',
    'inquiry_sent','inquiry_retried','inquiry_cancelled','patient_cancelled','destination_confirmed','arrival_confirmed','discharged')
ACTIVATION_END_EVENTS=set(ACTIVATION_EVENTS)-{'inquiry_workflow_started','inquiry_sent'}
SEED_DECISION_FIELDS=('id','patient_id','at','policy_name','job_version','reason','hospital_ids')
SEED_INQUIRY_FIELDS=('id','patient_id','hospital_id','created_at','last_sent_at','attempts','state','version')
ACTIVATION_FIELDS={'schema_version','rule','now','patient_id','policy_name','capture_snapshot','current_job',
    'current_patient','event_prefix_max','decision_prefix_max','source_decisions','source_events',
    'source_inquiries','workflow_start_event','activated_seed','blocked_reason'}


def _identity(left,right):
    return type(left) is type(right) and left==right


def _positive_id(value):
    return type(value) is int and value>0


def _activation_result(value):
    """Resolve a committed seed using captured public rows, without policy replay."""
    now=value['now'];pid=value['patient_id'];job=value['current_job'];snap=value['capture_snapshot']
    patient=value['current_patient'];ep=value['event_prefix_max'];dp=value['decision_prefix_max']
    if (type(now) is not int or now<0 or type(ep) is not int or ep<0 or type(dp) is not int or dp<0
            or value['policy_name']!=POLICY_NAME or type(pid) not in (str,int) or (type(pid) is str and not pid)):
        raise ValueError('activation identity/clock/prefix differs')
    if (set(job)!=set(REGISTRY_JOB_FIELDS) or not _identity(job['patient_id'],pid) or job['policy_name']!=POLICY_NAME
            or type(job['enabled']) is not int or job['enabled']!=1 or not _positive_id(job['version'])
            or not _finite(job['started_at']) or not _finite(job['deadline_at']) or not job['started_at']<=now<job['deadline_at']):
        raise ValueError('activation current workflow differs')
    if (set(snap)!={'patient_id','patient_version','job_version','revision','now'} or not _identity(snap['patient_id'],pid)
            or snap['now']!=now or type(snap['now']) is not int or snap['job_version']!=job['version']
            or not _positive_id(snap['patient_version']) or type(snap['revision']) is not int or snap['revision']<0):
        raise ValueError('activation current snapshot differs')
    if (set(patient)!={'id','state','version'} or not _identity(patient['id'],pid) or patient['state']!='WAITING'
            or not _positive_id(patient['version']) or patient['version']!=snap['patient_version']):
        raise ValueError('activation current patient differs')
    events=value['source_events'];decisions=value['source_decisions'];inquiries=value['source_inquiries']
    if any(type(rows) is not list for rows in (events,decisions,inquiries)):
        raise ValueError('activation sources must be lists')
    seen=set();starts=[]
    for event in events:
        if (type(event) is not dict or not _positive_id(event.get('id')) or event['id'] in seen or event['id']>ep
                or not _identity(event.get('patient_id'),pid) or not _finite(event.get('at')) or event['at']>now
                or event.get('type') not in ACTIVATION_EVENTS or type(event.get('data')) is not dict):
            raise ValueError('activation event source is foreign/future/duplicate or malformed')
        seen.add(event['id'])
        if event['type']=='inquiry_workflow_started':starts.append(event)
    if not starts:return None,None,'no_current_workflow_start_event'
    start=max(starts,key=lambda event:event['id'])
    if start['at']!=job['started_at'] or start['data'].get('policy_name')!=POLICY_NAME:
        return start,None,'workflow_start_identity_or_clock_differs'
    if any(event['id']>start['id'] and event['type'] in ACTIVATION_END_EVENTS for event in events):
        return start,None,'workflow_or_inquiry_ended_after_start'
    by_qid={}
    for q in inquiries:
        if (type(q) is not dict or set(q)!=set(SEED_INQUIRY_FIELDS) or not _positive_id(q['id']) or q['id'] in by_qid
                or not _identity(q['patient_id'],pid) or type(q['hospital_id']) is not str or not q['hospital_id']
                or not _finite(q['created_at']) or not _finite(q['last_sent_at']) or not q['created_at']<=q['last_sent_at']<=now
                or not _positive_id(q['attempts']) or not _positive_id(q['version']) or type(q['state']) is not str):
            raise ValueError('activation inquiry source differs')
        by_qid[q['id']]=q
    seen=set();seeds=[]
    for d in decisions:
        if (type(d) is not dict or set(d)!=set(SEED_DECISION_FIELDS)|{'prefetch','time_context'}
                or not _positive_id(d['id']) or d['id'] in seen or d['id']>dp or not _identity(d['patient_id'],pid)
                or not _finite(d['at']) or d['at']>now or not _positive_id(d['job_version'])
                or type(d['hospital_ids']) is not list or type(d['policy_name']) is not str):
            raise ValueError('activation decision source differs')
        seen.add(d['id']);p=d['prefetch'];tc=d['time_context']
        if d['policy_name']!=POLICY_NAME or not job['started_at']<=d['at']<=now or type(p) is not dict or p.get('applied') is not True:
            continue
        if (type(tc) is not dict or tc.get('patient_id')!=pid or type(tc.get('patient_id')) is not type(pid)
                or tc.get('job_version')!=d['job_version'] or type(tc.get('event_prefix_max')) is not int
                or not 0<=tc['event_prefix_max']<=ep or type(tc.get('decision_prefix_max')) is not int
                or not 0<=tc['decision_prefix_max']<d['id']):continue
        try:epoch=tc['source']['workflow_start_event'];prior_job=tc['source']['workflow']
        except (KeyError,TypeError):continue
        if (epoch.get('id')!=start['id'] or epoch.get('at')!=start['at'] or not _identity(epoch.get('patient_id'),pid)
                or prior_job.get('started_at')!=job['started_at'] or prior_job.get('deadline_at')!=job['deadline_at']):continue
        if (p.get('rule')!=CHAIN_PREFETCH_RULE or p.get('own_wake') is not True or p.get('parent_H_send')!=[] or d['reason']!='chainplanwake_prefetch_one'
                or p.get('reason')!='advanced_one_original_future_plan' or p.get('now')!=d['at']
                or not _identity(p.get('patient_id'),pid) or p.get('model_or_cost_recomputed') is not False
                or p.get('search_after_first_blocked') is not False):continue
        plan=p.get('original_candidate_plan');future=[]
        if type(plan) is not list:continue
        for i,row in enumerate(plan):
            if type(row) is dict and _finite(row.get('planned_offset_seconds')) and row['planned_offset_seconds']>0:
                future.append((row['planned_offset_seconds'],i))
        if not future:continue
        offset,index=min(future);row=plan[index];hid=row.get('hospital')
        wave=[i for off,i in future if off==offset];hospitals=[]
        for i in wave:
            peer=plan[i].get('hospital')
            if type(peer) is str and peer and peer not in hospitals:hospitals.append(peer)
        if (len(hospitals)<2 or type(hid) is not str or not hid or d['hospital_ids']!=[hid]
                or p.get('added_hospital_ids')!=[hid] or type(p.get('selected_plan_index')) is not int
                or p['selected_plan_index']!=index or p.get('selected_plan_row')!=row
                or p.get('earliest_wave_offset_seconds')!=offset or p.get('earliest_wave_plan_indices')!=wave
                or p.get('earliest_wave_rows')!=[plan[i] for i in wave]
                or p.get('earliest_wave_distinct_hospital_ids')!=hospitals or p.get('earliest_wave_has_distinct_peer') is not True):continue
        matching=[]
        for event in events:
            if (event['type']!='inquiry_sent' or not tc['event_prefix_max']<event['id']<=ep
                    or event['id']<=start['id'] or event['at']!=d['at'] or event.get('hospital_id')!=hid):continue
            qid=event['data'].get('inquiry_id');q=by_qid.get(qid) if _positive_id(qid) else None
            if (q is None or q['hospital_id']!=hid or q['created_at']!=d['at'] or q['last_sent_at']!=d['at']
                    or q['attempts']!=1 or q['state']=='CANCELLED'):continue
            matching.append((event,q))
        if len(matching)!=1:continue
        event,q=matching[0]
        seeds.append(dict(decision_id=d['id'],at=d['at'],hospital_id=hid,inquiry_id=q['id'],
            inquiry_version=q['version'],send_event_id=event['id'],workflow_start_event_id=start['id']))
    seed=min(seeds,key=lambda row:row['decision_id']) if seeds else None
    return start,seed,'committed_distinct_future_wave_seed' if seed else 'no_committed_distinct_future_wave_seed'


def build_activation_context(*,now,snapshot,job,patient,event_prefix_max,decision_prefix_max,decisions,events,inquiries):
    projected=[]
    for row in decisions:
        if not _identity(row.get('patient_id'),job['patient_id']):continue
        evidence=row.get('evidence') if type(row.get('evidence')) is dict else {}
        tag=evidence.get('cohort_chainplanwake',{})
        projected.append({**{key:deepcopy(row[key]) for key in SEED_DECISION_FIELDS},
            'prefetch':deepcopy(tag.get('prefetch')) if type(tag) is dict else None,
            'time_context':deepcopy(evidence.get('time_horizon',{}).get('context'))})
    value=dict(schema_version=1,rule=ACTIVATION_RULE,now=now,patient_id=job['patient_id'],policy_name=POLICY_NAME,
        capture_snapshot=deepcopy(snapshot),current_job={key:deepcopy(job[key]) for key in REGISTRY_JOB_FIELDS},
        current_patient={key:deepcopy(patient[key]) for key in ('id','state','version')},
        event_prefix_max=event_prefix_max,decision_prefix_max=decision_prefix_max,
        source_decisions=projected,source_events=[deepcopy(row) for row in events
            if _identity(row.get('patient_id'),job['patient_id']) and row.get('type') in ACTIVATION_EVENTS],
        source_inquiries=[{key:deepcopy(row[key]) for key in SEED_INQUIRY_FIELDS} for row in inquiries],
        workflow_start_event=None,activated_seed=None,blocked_reason=None)
    start,seed,reason=_activation_result(value)
    # Read the public history once but retain only the selected seed.  No prior
    # full decision/tag/activation capsule is recursively embedded in this one.
    value['source_decisions']=[] if seed is None else [row for row in projected if row['id']==seed['decision_id']]
    value['source_inquiries']=[] if seed is None else [row for row in value['source_inquiries'] if row['id']==seed['inquiry_id']]
    value['source_events']=[row for row in value['source_events'] if start is not None and
        (row['id']==start['id'] or (row['id']>start['id'] and row['type'] in ACTIVATION_END_EVENTS)
         or (seed is not None and row['id']==seed['send_event_id']))]
    value.update(workflow_start_event=deepcopy(start),activated_seed=seed,blocked_reason=reason)
    return validate_activation_context(value,now=now,patient_id=job['patient_id'],snapshot=snapshot,job=job,
        event_prefix_max=event_prefix_max,decision_prefix_max=decision_prefix_max)


def validate_activation_context(value,*,now,patient_id,snapshot,job,event_prefix_max,decision_prefix_max):
    if (type(value) is not dict or set(value)!=ACTIVATION_FIELDS or type(value['schema_version']) is not int
            or value['schema_version']!=1 or value['rule']!=ACTIVATION_RULE or value['now']!=now
            or not _identity(value['patient_id'],patient_id) or value['capture_snapshot']!=snapshot
            or value['current_job']!={key:job[key] for key in REGISTRY_JOB_FIELDS}
            or type(value['event_prefix_max']) is not int or value['event_prefix_max']!=event_prefix_max
            or type(value['decision_prefix_max']) is not int or value['decision_prefix_max']!=decision_prefix_max):
        raise ValueError('activation current captured provenance differs')
    start,seed,reason=_activation_result(value)
    if value['workflow_start_event']!=start or value['activated_seed']!=seed or value['blocked_reason']!=reason:
        raise ValueError('activation seed does not match captured committed public sources')
    return deepcopy(value)
