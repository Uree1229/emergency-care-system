"""Copied native identity projection into the immutable Followplan contract."""
from copy import deepcopy
import math
from . import cohort_tailwait_context as original

POLICY_NAME='eat_cohort_planwake_l15_f'
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
    for job in projected:job['policy_name']=BASE_POLICY_NAME if job['policy_name']==POLICY_NAME else '__not_cohort_planwake__'
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
WAKE_FIELDS={'schema_version','rule','now','patient_id','capture_snapshot','captured_job','latest_decision','event_prefix_max','decision_prefix_max','remaining_open_inquiry_ids','terminal_declines'}
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


def build_wake_context(*,now,snapshot,job,latest_decision,event_prefix_max,decision_prefix_max,inquiries,events):
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
        remaining_open_inquiry_ids=list(remaining),terminal_declines=declines)
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
    return deepcopy(value)
