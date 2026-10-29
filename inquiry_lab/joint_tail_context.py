"""Complete current first-decision roster and public tail inquiry options.

Only policy identity is projected when using frozen base validation. Captured
public rows retain their actual identity; no project global is changed.
"""
from copy import deepcopy

from server import inquiry_policies
from . import horizon_context, joint_first_context as original

POLICY_NAME = 'eat_joint_tail_l15_f'
BASE_POLICY_NAME = 'eat_joint_first_l15_f'
MEMBER_FIELDS = {'patient_id', 'inputs', 'workflow_snapshot'}
INPUT_FIELDS = {'now','candidates','observations','pending','patient_pending',
    'max_new','last_contact_at','time_context'}
JOB_FIELDS = {'patient_id','version','started_at','deadline_at','enabled','policy_name'}
SNAPSHOT_FIELDS = {'patient_id','job_version','patient_version','revision','now'}


def _identity(value):
    if type(value) not in (str,int) or type(value) is str and not value:
        raise ValueError('public patient identity is invalid')
    return type(value),value


def base_context_projection(context):
    value = deepcopy(context)
    if type(value) is not dict or type(value.get('eligible')) is not list:
        raise ValueError('current tail roster is missing')
    for row in value['eligible']:
        if type(row) is not dict or type(row.get('job')) is not dict or row['job'].get('policy_name') != POLICY_NAME:
            raise ValueError('current tail roster policy identity differs')
        row['job']['policy_name'] = BASE_POLICY_NAME
    return value


def validate_context(context, *, now=None, event_prefix_max=None, decision_prefix_max=None):
    original.validate_context(base_context_projection(context),now=now,
        event_prefix_max=event_prefix_max,decision_prefix_max=decision_prefix_max)
    return deepcopy(context)


def build_context(*, now, event_prefix_max, decision_prefix_max, patients, jobs,
                  inquiries, decisions, pair_patient_ids=None):
    projected_jobs = []
    for job in jobs:
        if type(job) is not dict or 'policy_name' not in job:
            raise ValueError('current workflow source is incomplete')
        projected = deepcopy(job)
        projected['policy_name'] = BASE_POLICY_NAME if job['policy_name']==POLICY_NAME else '__not_joint_tail__'
        projected_jobs.append(projected)
    value = original.build_context(now=now,event_prefix_max=event_prefix_max,
        decision_prefix_max=decision_prefix_max,patients=patients,jobs=projected_jobs,
        inquiries=inquiries,decisions=decisions,pair_patient_ids=pair_patient_ids)
    for row in value['eligible']:
        row['job']['policy_name'] = POLICY_NAME
    return validate_context(value)


def _validate_member(member, row, context):
    if type(member) is not dict or set(member)!=MEMBER_FIELDS:
        raise ValueError('current first-decision source is incomplete')
    pid = row['patient']['id']
    if _identity(member['patient_id']) != _identity(pid):
        raise ValueError('current first-decision roster order differs')
    inputs = member['inputs']
    if type(inputs) is not dict or set(inputs)!=INPUT_FIELDS or type(inputs['now']) is not int or inputs['now']!=context['now']:
        raise ValueError('current first-decision inputs are incomplete or stale')
    for key in ('candidates','observations','pending','patient_pending'):
        if type(inputs[key]) is not list:
            raise ValueError('captured current public rows must be lists')
    if inputs['patient_pending'] or inputs['last_contact_at'] is not None:
        raise ValueError('current first-decision owner already has an inquiry')
    if type(inputs['max_new']) is not int or not 0<=inputs['max_new']<=10:
        raise ValueError('current first-decision inquiry budget is invalid')
    source = member['workflow_snapshot']
    if type(source) is not dict or set(source)!={'apply_snapshot','job'}:
        raise ValueError('current first-decision workflow source is missing')
    snapshot,job = source['apply_snapshot'],source['job']
    if type(snapshot) is not dict or set(snapshot)!=SNAPSHOT_FIELDS or type(job) is not dict or set(job)!=JOB_FIELDS:
        raise ValueError('current first-decision workflow projection differs')
    for key in ('job_version','patient_version','revision','now'):
        if type(snapshot[key]) is not int or snapshot[key]<(1 if key in ('job_version','patient_version') else 0):
            raise ValueError('current first-decision snapshot clock/version is invalid')
    if (_identity(snapshot['patient_id'])!=_identity(pid) or _identity(job['patient_id'])!=_identity(pid)
            or snapshot['job_version']!=row['job']['version'] or snapshot['patient_version']!=row['patient']['version']
            or snapshot['now']>context['now'] or any(type(job[key]) is not type(row['job'][key])
                or job[key]!=row['job'][key] for key in job)):
        raise ValueError('current first-decision snapshot identity differs')
    horizon_context.validate_context(inputs['time_context'],now=context['now'],patient_id=pid,
        job_version=job['version'],event_prefix_max=context['event_prefix_max'],
        decision_prefix_max=context['decision_prefix_max'])
    if inputs['time_context']['auto_inquiry_deadline_at'] != job['deadline_at']:
        raise ValueError('current first-decision automatic-inquiry boundary differs')


def validate_tail_members(joint_context, tail_members, *, members):
    """Require every current eligible owner after the pair, with unchanged pool."""
    context = validate_context(joint_context)
    if type(members) is not list or len(members)!=2 or len(context['pair_patient_ids'])!=2:
        raise ValueError('a tail guard requires one current two-person pair')
    if type(tail_members) is not list or len(tail_members)!=len(context['eligible'])-2:
        raise ValueError('all remaining current first-decision patients must be captured')
    all_members = members+tail_members
    for member,row in zip(all_members,context['eligible']):
        _validate_member(member,row,context)
    reference = members[0]
    for member in all_members[1:]:
        if any(member['inputs'][key]!=reference['inputs'][key] for key in ('observations','pending')):
            raise ValueError('current first-decision inputs do not share one public ledger')
        if member['workflow_snapshot']['apply_snapshot']['revision']!=reference['workflow_snapshot']['apply_snapshot']['revision']:
            raise ValueError('current first-decision inputs do not share one revision')
    owners = {_identity(row['patient']['id']) for row in context['eligible']}
    if any(_identity(row['patient']) in owners for row in
            inquiry_policies._pending(reference['inputs']['pending'],context['now'])):
        raise ValueError('an eligible first-decision owner already has a public inquiry')
    result = []
    for member in tail_members:
        ranked = inquiry_policies._candidates(member['inputs']['candidates'])
        pool = ranked[:min(6,member['inputs']['max_new'])]
        result.append(dict(patient_id=member['patient_id'],hospitals=[row['hospital'] for row in pool]))
    return result


def base_members_projection(members):
    value = deepcopy(members)
    for member in value:
        if member['workflow_snapshot']['job']['policy_name'] != POLICY_NAME:
            raise ValueError('current member policy identity differs')
        member['workflow_snapshot']['job']['policy_name'] = BASE_POLICY_NAME
    return value
