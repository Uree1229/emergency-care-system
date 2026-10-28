"""Unchanged D first cohort and a complete, separate public waiting inventory."""
from copy import deepcopy
from . import cohort_dynamic_context as original

POLICY_NAME='eat_cohort_hedge_l15_f'
BASE_POLICY_NAME='eat_cohort_dynamic_l15_f'
JOB_FIELDS=original.JOB_FIELDS
REGISTRY_JOB_FIELDS=('patient_id','version','started_at','deadline_at','next_evaluation_at','enabled','policy_name')
PRIORITY_SOURCE=original.PRIORITY_SOURCE
OWNER_SOURCE='complete current WAITING inventory and public owner inputs from one committed snapshot'
TIERS=original.TIERS
effective_priority=original.effective_priority
INPUT_FIELDS={'now','candidates','observations','pending','patient_pending','max_new','last_contact_at'}


def base_context_projection(value):
    result=deepcopy(value)
    if type(result) is not dict or type(result.get('eligible')) is not list:raise ValueError('hedge first roster is missing')
    for row in result['eligible']:
        if row['job']['policy_name']!=POLICY_NAME:raise ValueError('hedge first roster identity differs')
        row['job']['policy_name']=BASE_POLICY_NAME
    return result


def base_members_projection(members):
    result=deepcopy(members)
    for row in result:
        if row['workflow_snapshot']['job']['policy_name']!=POLICY_NAME:raise ValueError('hedge first member identity differs')
        row['workflow_snapshot']['job']['policy_name']=BASE_POLICY_NAME
    return result


def validate_context(value,*,now=None,event_prefix_max=None,decision_prefix_max=None):
    original.validate_context(base_context_projection(value),now=now,event_prefix_max=event_prefix_max,decision_prefix_max=decision_prefix_max)
    return deepcopy(value)


def build_context(*,now,event_prefix_max,decision_prefix_max,patients,jobs,inquiries,decisions,pair_patient_ids=None):
    projected=deepcopy(jobs)
    for job in projected:job['policy_name']=BASE_POLICY_NAME if job['policy_name']==POLICY_NAME else '__not_cohort_hedge__'
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


def _fields(value,keys,label):
    if type(value) is not dict or set(value)!=set(keys):raise ValueError(label+' fields differ')


def _int(value,label,minimum=0):
    if type(value) is not int or value<minimum:raise ValueError(label+' must be integer')


def _key(pid):return type(pid).__name__,str(pid)


def _identity(actual,expected):
    if type(actual) is not type(expected) or actual!=expected:raise ValueError('public patient identity differs')


def _validate(value,*,joint_context,members,tail_members,priority_context):
    native=validate_context(joint_context)
    validate_tail_members(native,tail_members,members=members)
    priority=validate_priority_context(priority_context,joint_context=native,members=members,tail_members=tail_members)
    _fields(value,('schema_version','now','event_prefix_max','decision_prefix_max','revision','source',
        'first_cohort_patient_ids','patient_registry','patient_created_events','inventory'),'owner context')
    if type(value['schema_version']) is not int or value['schema_version']!=1 or value['source']!=OWNER_SOURCE:raise ValueError('owner context source differs')
    for key in ('now','event_prefix_max','decision_prefix_max','revision'):
        _int(value[key],key)
        if value[key]!=priority[key]:raise ValueError('owner context snapshot differs')
    now=value['now'];prefix=value['event_prefix_max'];first=members+tail_members
    first_ids=[m['patient_id'] for m in first]
    if value['first_cohort_patient_ids']!=first_ids:raise ValueError('first cohort partition differs')
    registry=value['patient_registry'];events=value['patient_created_events'];inventory=value['inventory']
    if any(type(x) is not list for x in (registry,events,inventory)):raise ValueError('complete public inventory lists required')
    by_patient={}
    for patient in registry:
        _fields(patient,('id','state','version'),'patient registry')
        pid=patient['id'];_int(patient['version'],'patient version',1)
        if type(pid) not in (str,int) or isinstance(pid,bool) or pid=='':raise ValueError('invalid registry patient')
        if _key(pid) in by_patient:raise ValueError('duplicate registry patient')
        by_patient[_key(pid)]=patient
    births={};last=0
    for event in events:
        _fields(event,('id','at','type','patient_id'),'patient creation event')
        _int(event['id'],'creation ID',1);_int(event['at'],'creation clock')
        if not last<event['id']<=prefix or event['at']>now or event['type']!='patient_created':raise ValueError('creation event outside committed prefix')
        last=event['id'];key=_key(event['patient_id'])
        if key in births:raise ValueError('duplicate patient creation')
        births[key]=event
    expected=[]
    for key,patient in by_patient.items():
        if patient['state']=='WAITING':
            if key not in births:raise ValueError('WAITING patient lacks public creation')
            if now<births[key]['at']+300:expected.append(patient['id'])
    expected.sort(key=_key)
    if [r.get('patient',{}).get('id') for r in inventory]!=expected:raise ValueError('complete WAITING/H-positive inventory differs')
    source_pending=members[0]['inputs']['pending'];first_by={_key(m['patient_id']):m for m in first}
    priority_by={_key(p['patient_id']):p for p in priority['patients']}
    for row in inventory:
        _fields(row,('patient','patient_created_event','evaluation_end_at','job','inquiries','valid_acceptance_ids','inputs','apply_snapshot'),'owner inventory row')
        patient=row['patient'];_fields(patient,('id','state','version','reported_priority','priority_source','effective_priority'),'owner patient')
        pid=patient['id'];key=_key(pid);registered=by_patient[key]
        if {k:patient[k] for k in ('id','state','version')}!=registered:raise ValueError('owner patient snapshot differs')
        if type(patient['effective_priority']) is not int or patient['effective_priority']!=effective_priority(patient['reported_priority'],patient['priority_source']):raise ValueError('owner effective priority differs')
        if row['patient_created_event']!=births[key] or row['evaluation_end_at']!=births[key]['at']+300:raise ValueError('owner H source differs')
        snapshot=row['apply_snapshot'];_fields(snapshot,('patient_id','patient_version','job_version','revision','now'),'owner apply snapshot')
        _identity(snapshot['patient_id'],pid)
        for k in ('patient_version','revision','now'):_int(snapshot[k],k,1 if k=='patient_version' else 0)
        if snapshot!={**snapshot,'patient_id':pid,'patient_version':patient['version'],'revision':value['revision'],'now':now}:raise ValueError('owner apply snapshot differs')
        job=row['job']
        if job is None:
            if snapshot['job_version'] is not None:raise ValueError('missing owner job version differs')
        else:
            _fields(job,REGISTRY_JOB_FIELDS,'owner job');_identity(job['patient_id'],pid)
            for k in ('version','started_at','deadline_at','next_evaluation_at','enabled'):_int(job[k],k,1 if k=='version' else 0)
            if job['enabled'] not in (0,1) or job['deadline_at']<=job['started_at'] or snapshot['job_version']!=job['version'] or type(job['policy_name']) is not str:raise ValueError('owner job snapshot differs')
        inquiries=row['inquiries'];accepted=row['valid_acceptance_ids']
        if type(inquiries) is not list or type(accepted) is not list:raise ValueError('owner inquiries required')
        ids=set();contacted=set();last_id=0;sent=[]
        for q in inquiries:
            _fields(q,('id','patient_id','hospital_id','state','last_sent_at'),'owner inquiry')
            _identity(q['patient_id'],pid);_int(q['id'],'inquiry ID',1);_int(q['last_sent_at'],'inquiry send clock')
            if q['id']<=last_id or q['last_sent_at']>now:raise ValueError('owner inquiry prefix differs')
            ids.add(q['id']);contacted.add(q['hospital_id']);last_id=q['id'];sent.append(q['last_sent_at'])
        if len(set(accepted))!=len(accepted) or any(type(x) is not int or x not in ids or next(q for q in inquiries if q['id']==x)['state']!='ACCEPTED' for x in accepted):raise ValueError('valid acceptance source differs')
        inputs=row['inputs'];_fields(inputs,INPUT_FIELDS,'owner original seven inputs')
        _int(inputs['now'],'owner input clock');_int(inputs['max_new'],'owner contact budget')
        if inputs['now']!=now or inputs['pending']!=source_pending or inputs['max_new']!=max(0,10-len(contacted)) or inputs['last_contact_at']!=max(sent,default=None):raise ValueError('owner original input source differs')
        if key in first_by:
            expected_inputs={k:v for k,v in first_by[key]['inputs'].items() if k!='time_context'}
            if inputs!=expected_inputs or accepted:raise ValueError('first cohort owner inputs differ')
            if job is None or {k:job[k] for k in JOB_FIELDS}!=first_by[key]['workflow_snapshot']['job']:raise ValueError('first cohort owner job differs')
            eligible=next(e for e in native['eligible'] if _key(e['patient']['id'])==key)
            if job!=eligible['job']:raise ValueError('first cohort owner review barrier differs')
            p=priority_by[key]
            if {k:patient[k] for k in ('reported_priority','priority_source','effective_priority')}!={k:p[k] for k in ('reported_priority','priority_source','effective_priority')}:raise ValueError('first cohort owner priority differs')
    if not set(map(_key,first_ids))<=set(map(_key,expected)):raise ValueError('first cohort outside complete inventory')
    return deepcopy(value)


def build_owner_context(*,joint_context,members,tail_members,priority_context,inventory_rows,registry_patients,events):
    value={k:deepcopy(priority_context[k]) for k in ('now','event_prefix_max','decision_prefix_max','revision')}
    value.update(schema_version=1,source=OWNER_SOURCE,first_cohort_patient_ids=[m['patient_id'] for m in members+tail_members],
        patient_registry=deepcopy(registry_patients),patient_created_events=[{k:e[k] for k in ('id','at','type','patient_id')}
            for e in events if e['id']<=value['event_prefix_max'] and e['type']=='patient_created'],inventory=deepcopy(inventory_rows))
    return _validate(value,joint_context=joint_context,members=members,tail_members=tail_members,priority_context=priority_context)


def validate_owner_context(value,*,joint_context,members,tail_members,priority_context):
    return _validate(value,joint_context=joint_context,members=members,tail_members=tail_members,priority_context=priority_context)
