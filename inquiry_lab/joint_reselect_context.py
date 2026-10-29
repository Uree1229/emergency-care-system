"""Current-public roster projected by copied identity into frozen tail checks."""
from copy import deepcopy
from . import joint_tail_context as original

POLICY_NAME = 'eat_joint_reselect_l15_f'
BASE_POLICY_NAME = 'eat_joint_tail_l15_f'
JOB_FIELDS = original.JOB_FIELDS


def base_context_projection(context):
    value = deepcopy(context)
    if type(value) is not dict or type(value.get('eligible')) is not list:
        raise ValueError('current reselection roster is missing')
    for row in value['eligible']:
        if type(row) is not dict or type(row.get('job')) is not dict or row['job'].get('policy_name') != POLICY_NAME:
            raise ValueError('current reselection roster policy identity differs')
        row['job']['policy_name'] = BASE_POLICY_NAME
    return value


def base_members_projection(members):
    value = deepcopy(members)
    for member in value:
        if member['workflow_snapshot']['job']['policy_name'] != POLICY_NAME:
            raise ValueError('current reselection member policy identity differs')
        member['workflow_snapshot']['job']['policy_name'] = BASE_POLICY_NAME
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
        projected['policy_name'] = BASE_POLICY_NAME if job['policy_name']==POLICY_NAME else '__not_joint_reselect__'
        projected_jobs.append(projected)
    value = original.build_context(now=now,event_prefix_max=event_prefix_max,
        decision_prefix_max=decision_prefix_max,patients=patients,jobs=projected_jobs,
        inquiries=inquiries,decisions=decisions,pair_patient_ids=pair_patient_ids)
    for row in value['eligible']:
        row['job']['policy_name'] = POLICY_NAME
    return validate_context(value)


def validate_tail_members(joint_context, tail_members, *, members):
    validate_context(joint_context)
    return original.validate_tail_members(base_context_projection(joint_context),
        base_members_projection(tail_members),members=base_members_projection(members))
