"""Source-bound public time boundaries for the isolated horizon candidate.

The current workflow deadline A limits new automatic inquiries.  The fixed lab
observation end H comes from the public patient-created event plus 300 seconds;
A is never substituted for H or treated as expiry of an already sent inquiry.
The adapter must pass the current job and events from one committed inspection
snapshot after checking that job against the unchanged applying transaction.
"""

from copy import deepcopy


LAB_OBSERVATION_SECONDS = 300
EVALUATION_PROTOCOL = 'fixed_lab_patient_lifetime'


def _patient(value):
    return type(value) is int or type(value) is str and bool(value)


def _integer(value, label, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError(label + ' must be an integer >= ' + str(minimum))
    return value


def _fields(value, expected, label):
    if type(value) is not dict or set(value) != set(expected):
        raise ValueError(label + ' contains missing or unknown fields')


def _same_identity(actual, expected, label):
    if not _patient(actual) or type(actual) is not type(expected) or actual != expected:
        raise ValueError(label + ' differs from the current patient')


def _job_projection(job, patient_id, job_version, now):
    if type(job) is not dict or not {
            'patient_id', 'version', 'started_at', 'deadline_at', 'enabled'} <= set(job):
        raise ValueError('current public workflow projection is incomplete')
    _same_identity(job['patient_id'], patient_id, 'workflow patient_id')
    if _integer(job['version'], 'workflow version', 1) != job_version:
        raise ValueError('workflow version differs from the current job version')
    started = _integer(job['started_at'], 'workflow started_at')
    deadline = _integer(job['deadline_at'], 'workflow deadline_at')
    if started > now or deadline <= started:
        raise ValueError('workflow start and deadline are inconsistent')
    if type(job['enabled']) is not int or job['enabled'] != 1:
        raise ValueError('current workflow must be enabled')
    return dict(patient_id=patient_id, version=job_version, started_at=started,
                deadline_at=deadline, enabled=1)


def _source_event(row, patient_id, kind, now, event_prefix_max):
    _fields(row, ('id', 'at', 'type', 'patient_id') + (
        ('deadline_seconds', 'restarted') if kind == 'inquiry_workflow_started' else ()),
        'public source event')
    event_id = _integer(row['id'], 'source event ID', 1)
    at = _integer(row['at'], 'source event clock')
    if event_id > event_prefix_max or at > now or row['type'] != kind:
        raise ValueError('source event is outside the current public event prefix')
    _same_identity(row['patient_id'], patient_id, 'source event patient_id')
    if kind == 'inquiry_workflow_started':
        _integer(row['deadline_seconds'], 'public workflow deadline_seconds', 1)
        if type(row['restarted']) is not bool:
            raise ValueError('public workflow restarted flag must be boolean')


def validate_context(context, *, now, patient_id=None, job_version=None,
                     event_prefix_max=None, decision_prefix_max=None):
    """Validate a source projection and return an independent copy.

    The optional expected fields bind policy/audit callers to their current
    snapshot.  The builder establishes which start event is latest; the runner
    and independent audit retain the committed prefix needed to verify that
    choice.  A can already have passed while pending replies remain eligible
    through H.  The lab observation end itself must still be in the future.
    """
    _integer(now, 'current clock')
    _fields(context, ('patient_id', 'job_version', 'event_prefix_max',
                     'decision_prefix_max', 'auto_inquiry_deadline_at',
                     'evaluation_end_at', 'source'), 'time_context')
    pid = context['patient_id']
    if not _patient(pid):
        raise ValueError('time_context patient_id must be a nonempty string or integer')
    version = _integer(context['job_version'], 'current job version', 1)
    prefix = _integer(context['event_prefix_max'], 'public event prefix')
    dprefix = _integer(context['decision_prefix_max'], 'public decision prefix')
    if patient_id is not None:
        if not _patient(patient_id):
            raise ValueError('expected patient_id must be a nonempty string or integer')
        _same_identity(pid, patient_id, 'time_context patient_id')
    for actual, expected, label, minimum in (
            (version, job_version, 'current job version', 1),
            (prefix, event_prefix_max, 'public event prefix', 0),
            (dprefix, decision_prefix_max, 'public decision prefix', 0)):
        if expected is not None and _integer(expected, 'expected ' + label, minimum) != actual:
            raise ValueError('time_context ' + label + ' differs from the current snapshot')
    source = context['source']
    _fields(source, ('workflow', 'workflow_start_event', 'patient_created_event',
                     'evaluation_protocol'), 'time_context source')
    workflow = source['workflow']
    _fields(workflow, ('patient_id', 'version', 'started_at', 'deadline_at', 'enabled'),
            'public workflow source')
    _job_projection(workflow, pid, version, now)
    start = source['workflow_start_event']
    created = source['patient_created_event']
    _source_event(start, pid, 'inquiry_workflow_started', now, prefix)
    _source_event(created, pid, 'patient_created', now, prefix)
    if created['id'] >= start['id'] or created['at'] > start['at']:
        raise ValueError('workflow source must follow the current patient creation')
    if (start['at'] != workflow['started_at'] or
            start['at'] + start['deadline_seconds'] != workflow['deadline_at']):
        raise ValueError('latest public workflow start differs from the current job deadline')
    protocol = source['evaluation_protocol']
    _fields(protocol, ('name', 'duration_seconds'), 'lab observation protocol')
    if (protocol['name'] != EVALUATION_PROTOCOL or
            type(protocol['duration_seconds']) is not int or
            protocol['duration_seconds'] != LAB_OBSERVATION_SECONDS):
        raise ValueError('the fixed public lab observation protocol differs')
    deadline = _integer(context['auto_inquiry_deadline_at'], 'automatic inquiry deadline')
    end = _integer(context['evaluation_end_at'], 'lab observation end')
    if deadline != workflow['deadline_at']:
        raise ValueError('automatic inquiry deadline must come from the current public workflow')
    if end != created['at'] + LAB_OBSERVATION_SECONDS:
        raise ValueError('lab observation end must come from public patient creation plus 300 seconds')
    if end <= now:
        raise ValueError('the lab observation interval must have positive time remaining')
    return deepcopy(context)


def build_context(*, now, patient_id, job_version, event_prefix_max,
                  decision_prefix_max, job, events):
    """Build only from current job identity and committed public source events.

    Rows after the event watermark are excluded even in the same second.  A
    future-dated relevant event inside the committed watermark is rejected
    rather than reviving an older workflow start.  Input rows are never changed.
    """
    _integer(now, 'current clock')
    if not _patient(patient_id):
        raise ValueError('current patient_id must be a nonempty string or integer')
    _integer(job_version, 'current job version', 1)
    _integer(event_prefix_max, 'public event prefix')
    _integer(decision_prefix_max, 'public decision prefix')
    workflow = _job_projection(job, patient_id, job_version, now)
    created = []
    starts = []
    seen = set()
    for row in events:
        if type(row) is not dict or 'id' not in row:
            raise ValueError('public event rows require an integer event ID')
        event_id = _integer(row['id'], 'public event ID', 1)
        if event_id > event_prefix_max:
            continue
        if event_id in seen:
            raise ValueError('public event IDs must be unique within the prefix')
        seen.add(event_id)
        if row.get('patient_id') != patient_id:
            continue
        kind = row.get('type')
        if kind not in ('patient_created', 'inquiry_workflow_started'):
            continue
        _same_identity(row['patient_id'], patient_id, 'public event patient_id')
        at = _integer(row.get('at'), 'public source event clock')
        if at > now:
            raise ValueError('current public source event cannot have a future clock')
        event = dict(id=event_id, at=at, type=kind, patient_id=patient_id)
        if kind == 'patient_created':
            created.append(event)
        else:
            data = row.get('data')
            if type(data) is not dict:
                raise ValueError('public workflow start data must be a parsed object')
            duration = _integer(data.get('deadline_seconds'), 'public workflow deadline_seconds', 1)
            restarted = data.get('restarted', False)
            if type(restarted) is not bool:
                raise ValueError('public workflow restarted flag must be boolean')
            event.update(deadline_seconds=duration, restarted=restarted)
            starts.append(event)
    if len(created) != 1 or not starts:
        raise ValueError('one public patient creation and a current public workflow start are required')
    start = max(starts, key=lambda row: row['id'])
    context = dict(patient_id=patient_id, job_version=job_version,
        event_prefix_max=event_prefix_max, decision_prefix_max=decision_prefix_max,
        auto_inquiry_deadline_at=workflow['deadline_at'],
        evaluation_end_at=created[0]['at'] + LAB_OBSERVATION_SECONDS,
        source=dict(workflow=workflow, workflow_start_event=start,
                    patient_created_event=created[0],
                    evaluation_protocol=dict(name=EVALUATION_PROTOCOL,
                                             duration_seconds=LAB_OBSERVATION_SECONDS)))
    return validate_context(context, now=now, patient_id=patient_id,
        job_version=job_version, event_prefix_max=event_prefix_max,
        decision_prefix_max=decision_prefix_max)
