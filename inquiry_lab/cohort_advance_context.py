"""Copied native identity projection into the immutable Followplan contract."""
from copy import deepcopy
from . import cohort_followplan_context as original

POLICY_NAME='eat_cohort_advance_l15_f'
BASE_POLICY_NAME='eat_cohort_followplan_l15_f'
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
    if type(result) is not dict or type(result.get('eligible')) is not list:raise ValueError('advance first roster is missing')
    for row in result['eligible']:
        if row['job']['policy_name']!=POLICY_NAME:raise ValueError('advance first roster identity differs')
        row['job']['policy_name']=BASE_POLICY_NAME
    return result


def base_members_projection(members):
    result=deepcopy(members)
    for row in result:
        if row['workflow_snapshot']['job']['policy_name']!=POLICY_NAME:raise ValueError('advance first member identity differs')
        row['workflow_snapshot']['job']['policy_name']=BASE_POLICY_NAME
    return result


def base_owner_projection(value):
    result=deepcopy(value)
    for row in result['inventory']:
        job=row['job']
        if job is not None:
            if job['policy_name']==POLICY_NAME:job['policy_name']=BASE_POLICY_NAME
            elif job['policy_name']==BASE_POLICY_NAME:job['policy_name']='__foreign_cohort_followplan__'
    return result


def validate_context(value,*,now=None,event_prefix_max=None,decision_prefix_max=None):
    original.validate_context(base_context_projection(value),now=now,event_prefix_max=event_prefix_max,decision_prefix_max=decision_prefix_max)
    return deepcopy(value)


def build_context(*,now,event_prefix_max,decision_prefix_max,patients,jobs,inquiries,decisions,pair_patient_ids=None):
    projected=deepcopy(jobs)
    for job in projected:job['policy_name']=BASE_POLICY_NAME if job['policy_name']==POLICY_NAME else '__not_cohort_advance__'
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
