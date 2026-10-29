"""Unchanged public first-contact roster plus separately captured priorities."""
from copy import deepcopy
from . import joint_budget_context as original

POLICY_NAME = 'eat_cohort_success_l15_f'
BASE_POLICY_NAME = 'eat_joint_budget_l15_f'
JOB_FIELDS = original.JOB_FIELDS
PRIORITY_SOURCE = 'captured current patient priority fields with common snapshot provenance'
TIERS = (4,2,1)


def base_context_projection(context):
    value=deepcopy(context)
    if type(value) is not dict or type(value.get('eligible')) is not list:
        raise ValueError('current cohort roster is missing')
    for row in value['eligible']:
        if type(row) is not dict or type(row.get('job')) is not dict or row['job'].get('policy_name')!=POLICY_NAME:
            raise ValueError('current cohort roster policy identity differs')
        row['job']['policy_name']=BASE_POLICY_NAME
    return value


def base_members_projection(members):
    value=deepcopy(members)
    for member in value:
        if member['workflow_snapshot']['job']['policy_name']!=POLICY_NAME:
            raise ValueError('current cohort member policy identity differs')
        member['workflow_snapshot']['job']['policy_name']=BASE_POLICY_NAME
    return value


def validate_context(context, *, now=None,event_prefix_max=None,decision_prefix_max=None):
    original.validate_context(base_context_projection(context),now=now,
        event_prefix_max=event_prefix_max,decision_prefix_max=decision_prefix_max)
    return deepcopy(context)


def build_context(*,now,event_prefix_max,decision_prefix_max,patients,jobs,inquiries,decisions,pair_patient_ids=None):
    projected=[]
    for job in jobs:
        if type(job) is not dict or 'policy_name' not in job:
            raise ValueError('current workflow source is incomplete')
        row=deepcopy(job)
        row['policy_name']=BASE_POLICY_NAME if job['policy_name']==POLICY_NAME else '__not_cohort_success__'
        projected.append(row)
    result=original.build_context(now=now,event_prefix_max=event_prefix_max,
        decision_prefix_max=decision_prefix_max,patients=patients,jobs=projected,
        inquiries=inquiries,decisions=decisions,pair_patient_ids=pair_patient_ids)
    for row in result['eligible']:row['job']['policy_name']=POLICY_NAME
    return validate_context(result)


def validate_tail_members(joint_context,tail_members,*,members):
    validate_context(joint_context)
    return original.validate_tail_members(base_context_projection(joint_context),
        base_members_projection(tail_members),members=base_members_projection(members))


def effective_priority(reported_priority,priority_source):
    if reported_priority is not None and (type(reported_priority) is not int or reported_priority not in TIERS):
        raise ValueError('reported priority must be null or public tier 4/2/1')
    if type(priority_source) is not str or priority_source not in ('expert','self','unknown'):
        raise ValueError('public priority source is invalid')
    return reported_priority if priority_source=='expert' and reported_priority in TIERS else 4


def build_priority_context(*,joint_context,members,tail_members,priority_rows):
    context=validate_context(joint_context)
    validate_tail_members(context,tail_members,members=members)
    if type(priority_rows) is not list or len(priority_rows)!=len(context['eligible']):
        raise ValueError('complete ordered current priority rows are required')
    rows=[]
    for row,eligible in zip(priority_rows,context['eligible']):
        if type(row) is not dict or set(row)!={'patient_id','patient_version','reported_priority','priority_source'}:
            raise ValueError('public priority row contains missing or unknown fields')
        pid=eligible['patient']['id']
        if (type(row['patient_id']) is not type(pid) or row['patient_id']!=pid
                or type(row['patient_version']) is not int or row['patient_version']!=eligible['patient']['version']):
            raise ValueError('public priority identity/version differs from current roster')
        rows.append(dict(row,effective_priority=effective_priority(row['reported_priority'],row['priority_source'])))
    return dict(schema_version=1,now=context['now'],event_prefix_max=context['event_prefix_max'],
        decision_prefix_max=context['decision_prefix_max'],revision=members[0]['workflow_snapshot']['apply_snapshot']['revision'],
        source=PRIORITY_SOURCE,patients=deepcopy(rows))


def validate_priority_context(value,*,joint_context,members,tail_members):
    keys={'schema_version','now','event_prefix_max','decision_prefix_max','revision','source','patients'}
    if type(value) is not dict or set(value)!=keys or type(value['schema_version']) is not int or value['schema_version']!=1:
        raise ValueError('priority context contains missing or unknown fields')
    if type(value['patients']) is not list:
        raise ValueError('public priority patients must be a list')
    rows=[]
    for row in value['patients']:
        if type(row) is not dict or set(row)!={'patient_id','patient_version','reported_priority','priority_source','effective_priority'}:
            raise ValueError('public effective priority row is incomplete')
        if type(row['effective_priority']) is not int:
            raise ValueError('public effective priority must be an integer tier')
        rows.append({k:v for k,v in row.items() if k!='effective_priority'})
    expected=build_priority_context(joint_context=joint_context,members=members,tail_members=tail_members,priority_rows=rows)
    for key in ('now','event_prefix_max','decision_prefix_max','revision'):
        if type(value[key]) is not int:raise ValueError('priority snapshot provenance must be integer')
    if value!=expected:raise ValueError('priority values or common snapshot provenance differ')
    return deepcopy(value)
