"""Current public eligibility for the isolated two-person first-inquiry policy.

The adapter supplies one committed read snapshot. Journal/row completeness is
audited externally; this module neither reads a database nor predicts arrivals.
"""
from copy import deepcopy

POLICY_NAME = 'eat_joint_first_l15_f'
JOB_FIELDS = {'patient_id', 'version', 'started_at', 'deadline_at',
    'next_evaluation_at', 'enabled', 'policy_name'}
PATIENT_FIELDS = {'id', 'state', 'version'}


def _pid(value):
    return type(value) in (str, int) and not (type(value) is str and not value)


def _key(value):
    if not _pid(value):
        raise ValueError('public patient identity is invalid')
    return type(value), value


def _integer(value, label, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError(label + ' must be an integer >= ' + str(minimum))


def _order(row):
    pid = row['patient']['id']
    return row['job']['started_at'], (0, pid) if type(pid) is int else (1, pid)


def validate_context(context, *, now=None, event_prefix_max=None, decision_prefix_max=None):
    if type(context) is not dict or set(context) != {'schema_version', 'now',
            'event_prefix_max', 'decision_prefix_max', 'eligible', 'pair_patient_ids'}:
        raise ValueError('joint context contains missing or unknown fields')
    if context['schema_version'] != 1 or type(context['schema_version']) is not int:
        raise ValueError('unsupported joint context schema')
    for key in ('now', 'event_prefix_max', 'decision_prefix_max'):
        _integer(context[key], key)
    for key, expected in (('now', now), ('event_prefix_max', event_prefix_max),
                          ('decision_prefix_max', decision_prefix_max)):
        if expected is not None and context[key] != expected:
            raise ValueError('joint context ' + key + ' differs from current snapshot')
    if type(context['eligible']) is not list:
        raise ValueError('eligible public roster must be a list')
    seen = set()
    for row in context['eligible']:
        if type(row) is not dict or set(row) != {'patient', 'job', 'inquiry_count', 'decision_count'}:
            raise ValueError('eligible source projection contains unknown fields')
        patient, job = row['patient'], row['job']
        if type(patient) is not dict or set(patient) != PATIENT_FIELDS:
            raise ValueError('public patient projection is incomplete')
        if type(job) is not dict or set(job) != JOB_FIELDS:
            raise ValueError('public workflow projection is incomplete')
        identity = _key(patient['id'])
        if identity in seen or _key(job['patient_id']) != identity:
            raise ValueError('eligible public identities are duplicated or inconsistent')
        seen.add(identity)
        _integer(patient['version'], 'patient version', 1)
        _integer(job['version'], 'job version', 1)
        for key in ('started_at', 'deadline_at', 'next_evaluation_at'):
            _integer(job[key], key)
        if (patient['state'] != 'WAITING' or type(job['enabled']) is not int or job['enabled'] != 1
                or job['policy_name'] != POLICY_NAME or job['started_at'] > context['now']
                or job['next_evaluation_at'] > context['now']
                or not job['started_at'] < job['deadline_at'] or job['deadline_at'] <= context['now']):
            raise ValueError('public roster contains an ineligible current workflow')
        if type(row['inquiry_count']) is not int or type(row['decision_count']) is not int \
                or row['inquiry_count'] != 0 or row['decision_count'] != 0:
            raise ValueError('joint members must have no inquiry and no prior decision')
    if context['eligible'] != sorted(context['eligible'], key=_order):
        raise ValueError('public roster is outside the existing workflow order')
    expected_pair = [row['patient']['id'] for row in context['eligible'][:2]]
    if len(expected_pair) < 2:
        expected_pair = []
    actual = context['pair_patient_ids']
    if type(actual) is not list or len(actual) != len(expected_pair) or \
            any(_key(left) != _key(right) for left, right in zip(actual, expected_pair)):
        raise ValueError('pair must be the first two eligible current patients')
    return deepcopy(context)


def build_context(*, now, event_prefix_max, decision_prefix_max, patients, jobs,
                  inquiries, decisions, pair_patient_ids=None):
    for label, value in (('now', now), ('event prefix', event_prefix_max),
                         ('decision prefix', decision_prefix_max)):
        _integer(value, label)
    people, workflows = {}, {}
    for patient in patients:
        if type(patient) is not dict or not PATIENT_FIELDS <= set(patient):
            raise ValueError('current patient source is incomplete')
        identity = _key(patient['id'])
        if identity in people:
            raise ValueError('duplicate current public patient')
        people[identity] = {key:patient[key] for key in PATIENT_FIELDS}
    for job in jobs:
        if type(job) is not dict or not JOB_FIELDS <= set(job):
            raise ValueError('current workflow source is incomplete')
        identity = _key(job['patient_id'])
        if identity in workflows:
            raise ValueError('duplicate current public workflow')
        workflows[identity] = {key:job[key] for key in JOB_FIELDS}
    contacted, decided = set(), set()
    for inquiry in inquiries:
        if type(inquiry) is not dict or 'patient_id' not in inquiry:
            raise ValueError('current inquiry source is incomplete')
        contacted.add(_key(inquiry['patient_id']))
    decision_ids = set()
    for decision in decisions:
        if type(decision) is not dict or not {'id', 'at', 'patient_id'} <= set(decision):
            raise ValueError('public decision source is incomplete')
        _integer(decision['id'], 'decision id', 1)
        if decision['id'] > decision_prefix_max:
            continue
        if decision['id'] in decision_ids:
            raise ValueError('duplicate public decision id')
        decision_ids.add(decision['id'])
        _integer(decision['at'], 'decision clock')
        if decision['at'] > now:
            raise ValueError('future decision inside committed current prefix')
        decided.add(_key(decision['patient_id']))
    eligible = []
    for identity, patient in people.items():
        job = workflows.get(identity)
        if job is None or identity in contacted or identity in decided:
            continue
        if (patient['state'] != 'WAITING' or job['enabled'] != 1 or
                job['policy_name'] != POLICY_NAME or job['started_at'] > now or
                job['next_evaluation_at'] > now or job['deadline_at'] <= now):
            continue
        eligible.append(dict(patient=patient, job=job, inquiry_count=0, decision_count=0))
    eligible.sort(key=_order)
    pair = [row['patient']['id'] for row in eligible[:2]] if len(eligible) >= 2 else []
    if pair_patient_ids is not None:
        if type(pair_patient_ids) is not list or len(pair_patient_ids) != len(pair) or \
                any(_key(a) != _key(b) for a,b in zip(pair_patient_ids,pair)):
            raise ValueError('requested pair is outside the current eligible workflow order')
    return validate_context(dict(schema_version=1, now=now, event_prefix_max=event_prefix_max,
        decision_prefix_max=decision_prefix_max, eligible=eligible, pair_patient_ids=pair))
