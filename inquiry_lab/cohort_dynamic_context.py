"""Copied native identity into frozen CS roster and public priority checks."""
from copy import deepcopy
from . import cohort_success_context as original

POLICY_NAME='eat_cohort_dynamic_l15_f'
BASE_POLICY_NAME='eat_cohort_success_l15_f'
JOB_FIELDS=original.JOB_FIELDS
PRIORITY_SOURCE=original.PRIORITY_SOURCE
TIERS=original.TIERS
effective_priority=original.effective_priority


def base_context_projection(context):
    result=deepcopy(context)
    if type(result) is not dict or type(result.get('eligible')) is not list:
        raise ValueError('current dynamic roster is missing')
    for row in result['eligible']:
        if type(row) is not dict or type(row.get('job')) is not dict or row['job'].get('policy_name')!=POLICY_NAME:
            raise ValueError('current dynamic roster policy identity differs')
        row['job']['policy_name']=BASE_POLICY_NAME
    return result


def base_members_projection(members):
    result=deepcopy(members)
    for member in result:
        if member['workflow_snapshot']['job']['policy_name']!=POLICY_NAME:
            raise ValueError('current dynamic member policy identity differs')
        member['workflow_snapshot']['job']['policy_name']=BASE_POLICY_NAME
    return result


def validate_context(context,*,now=None,event_prefix_max=None,decision_prefix_max=None):
    original.validate_context(base_context_projection(context),now=now,
        event_prefix_max=event_prefix_max,decision_prefix_max=decision_prefix_max)
    return deepcopy(context)


def build_context(*,now,event_prefix_max,decision_prefix_max,patients,jobs,inquiries,decisions,pair_patient_ids=None):
    projected=[]
    for job in jobs:
        if type(job) is not dict or 'policy_name' not in job:
            raise ValueError('current workflow source is incomplete')
        row=deepcopy(job)
        row['policy_name']=BASE_POLICY_NAME if row['policy_name']==POLICY_NAME else '__not_cohort_dynamic__'
        projected.append(row)
    result=original.build_context(now=now,event_prefix_max=event_prefix_max,decision_prefix_max=decision_prefix_max,
        patients=patients,jobs=projected,inquiries=inquiries,decisions=decisions,pair_patient_ids=pair_patient_ids)
    for row in result['eligible']:row['job']['policy_name']=POLICY_NAME
    return validate_context(result)


def validate_tail_members(joint_context,tail_members,*,members):
    validate_context(joint_context)
    return original.validate_tail_members(base_context_projection(joint_context),
        base_members_projection(tail_members),members=base_members_projection(members))


def build_priority_context(*,joint_context,members,tail_members,priority_rows):
    validate_context(joint_context)
    return original.build_priority_context(joint_context=base_context_projection(joint_context),
        members=base_members_projection(members),tail_members=base_members_projection(tail_members),priority_rows=priority_rows)


def validate_priority_context(value,*,joint_context,members,tail_members):
    validate_context(joint_context)
    return original.validate_priority_context(value,joint_context=base_context_projection(joint_context),
        members=base_members_projection(members),tail_members=base_members_projection(tail_members))
