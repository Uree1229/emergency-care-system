"""Pure evidence for a new public strict-predecessor terminal reply.

No workflow, policy registration, model or database is called. Invalid or
unsupported JSON-shaped inputs return None. Current row versions are captured,
not inferred from events, because response events do not carry row versions.
The caller must rebuild/compare this evidence before applying any later action.
"""
from copy import deepcopy
import math

RULE = 'public_strict_predecessor_terminal_reply_after_last_recorded_decision_prefix_before_existing_review'
JOB_FIELDS = ('patient_id','version','started_at','deadline_at','next_evaluation_at','enabled','policy_name')
INQUIRY_FIELDS = ('id','patient_id','hospital_id','state','responded_at','last_sent_at','version')
EVENT_FIELDS = ('id','at','type','patient_id','hospital_id')
SNAPSHOT_FIELDS = {'patient_id','patient_version','job_version','revision','now'}
LAST_FIELDS = {'id','at','patient_id','policy_name','job_version','previous_event_prefix_max'}
SOURCE_FIELDS = {'schema_version','rule','now','patient_id','policy_name','capture_snapshot','captured_job',
    'latest_decision','event_prefix_max','decision_prefix_max','remaining_open_inquiry_ids',
    'captured_own_open_inquiries','terminal_predecessors'}
OPEN = ('PENDING','CHECKING')


def _dict(value):
    return type(value) is dict and all(type(key) is str for key in value)


def _time(value):
    return type(value) in (int,float) and math.isfinite(value) and value >= 0


def _integer(value, minimum=0):
    return type(value) is int and value >= minimum


def _pid(value):
    return type(value) is int or type(value) is str and bool(value)


def _same(a,b):
    return type(a) is type(b) and a == b


def _text(value):
    return type(value) is str and bool(value)


def _project_inquiry(row):
    if not _dict(row) or any(key not in row for key in INQUIRY_FIELDS):
        return None
    value = {key:deepcopy(row[key]) for key in INQUIRY_FIELDS}
    if not _integer(value['id'],1) or not _integer(value['version'],1) or not _pid(value['patient_id']):
        return None
    if not _text(value['hospital_id']) or not _text(value['state']) or not _time(value['last_sent_at']):
        return None
    if value['responded_at'] is not None and not _time(value['responded_at']):
        return None
    return value


def _base_valid(value):
    now,pid,name = value['now'],value['patient_id'],value['policy_name']
    if not _integer(now) or not _pid(pid) or not _text(name):
        return False
    snapshot,job,last = value['capture_snapshot'],value['captured_job'],value['latest_decision']
    if not _dict(snapshot) or set(snapshot) != SNAPSHOT_FIELDS or not _dict(job) or set(job) != set(JOB_FIELDS):
        return False
    if not _same(snapshot['patient_id'],pid) or not _same(job['patient_id'],pid):
        return False
    if not _integer(snapshot['patient_version'],1) or not _integer(snapshot['job_version'],1) or not _integer(snapshot['revision']):
        return False
    if not _same(snapshot['now'],now) or not _integer(job['version'],1) or snapshot['job_version'] != job['version']:
        return False
    if type(job['enabled']) is not int or job['enabled'] != 1 or type(job['policy_name']) is not str or job['policy_name'] != name:
        return False
    if any(not _time(job[key]) for key in ('started_at','deadline_at','next_evaluation_at')):
        return False
    if not job['started_at'] <= now < job['deadline_at'] or not now < job['next_evaluation_at']:
        return False
    if not _dict(last) or set(last) != LAST_FIELDS or not _same(last['patient_id'],pid):
        return False
    if type(last['policy_name']) is not str or last['policy_name'] != name or not _integer(last['id'],1):
        return False
    if not _integer(last['job_version'],1) or last['job_version'] > job['version'] or not _time(last['at']) or not job['started_at'] <= last['at'] <= now:
        return False
    for item in (value['event_prefix_max'],value['decision_prefix_max'],last['previous_event_prefix_max']):
        if not _integer(item):
            return False
    return last['id'] <= value['decision_prefix_max'] and last['previous_event_prefix_max'] <= value['event_prefix_max']


def _terminal_join(event,foreign,own,*,now,pid,prefix,maximum):
    if not _dict(event) or set(event) != set(EVENT_FIELDS)|{'data'}:
        return False
    if not _integer(event['id'],1) or not prefix < event['id'] <= maximum or not _time(event['at']) or event['at'] > now:
        return False
    if event['type'] != 'inquiry_responded' or not _pid(event['patient_id']) or _same(event['patient_id'],pid):
        return False
    data = event['data']
    if not _dict(data) or set(data) != {'inquiry_id','action'} or not _integer(data['inquiry_id'],1):
        return False
    if data['action'] not in ('accept','decline') or type(data['action']) is not str:
        return False
    if data['inquiry_id'] != foreign['id'] or not _same(event['patient_id'],foreign['patient_id']) or event['hospital_id'] != foreign['hospital_id']:
        return False
    allowed = ('DECLINED',) if data['action'] == 'decline' else ('ACCEPTED','CONFIRMED')
    if foreign['state'] not in allowed or not _time(foreign['responded_at']) or foreign['responded_at'] != event['at']:
        return False
    if not foreign['last_sent_at'] <= event['at'] or not foreign['last_sent_at'] < own['last_sent_at']:
        return False
    return own['hospital_id'] == foreign['hospital_id'] and _same(own['patient_id'],pid) and own['state'] in OPEN and own['last_sent_at'] <= now


def validate(source):
    """Return a defensive copy of valid captured evidence, otherwise None."""
    try:
        if not _dict(source) or set(source) != SOURCE_FIELDS or type(source['schema_version']) is not int or source['schema_version'] != 1 or source['rule'] != RULE:
            return None
        if not _base_valid(source):
            return None
        own_rows,ids = source['captured_own_open_inquiries'],source['remaining_open_inquiry_ids']
        if type(own_rows) is not list or not own_rows or type(ids) is not list or not ids:
            return None
        own_by,own_hospitals = {},set()
        for row in own_rows:
            q = _project_inquiry(row)
            if q is None or set(row) != set(INQUIRY_FIELDS) or not _same(q['patient_id'],source['patient_id']) or q['state'] not in OPEN or q['last_sent_at'] > source['now']:
                return None
            if q['id'] in own_by or q['hospital_id'] in own_hospitals:
                return None
            own_by[q['id']] = q;own_hospitals.add(q['hospital_id'])
        if any(not _integer(item,1) for item in ids) or ids != sorted(own_by):
            return None
        entries = source['terminal_predecessors']
        if type(entries) is not list or not entries:
            return None
        seen_events,seen_foreign = set(),set()
        previous = 0
        for entry in entries:
            if not _dict(entry) or set(entry) != {'event','inquiry','own_inquiry'}:
                return None
            foreign,own = _project_inquiry(entry['inquiry']),_project_inquiry(entry['own_inquiry'])
            if foreign is None or own is None or set(entry['inquiry']) != set(INQUIRY_FIELDS) or set(entry['own_inquiry']) != set(INQUIRY_FIELDS):
                return None
            if own['id'] not in own_by or own_by[own['id']] != own or foreign['id'] in own_by:
                return None
            event = entry['event']
            if not _terminal_join(event,foreign,own,now=source['now'],pid=source['patient_id'],
                    prefix=source['latest_decision']['previous_event_prefix_max'],maximum=source['event_prefix_max']):
                return None
            if event['id'] in seen_events or foreign['id'] in seen_foreign or event['id'] <= previous:
                return None
            seen_events.add(event['id']);seen_foreign.add(foreign['id']);previous=event['id']
        return deepcopy(source)
    except Exception:
        return None


def build_public_predecessor_wake(*,now,snapshot,job,latest_decision,event_prefix_max,decision_prefix_max,
        own_inquiries,all_inquiries,events,policy_name):
    """Join newly public foreign terminal replies to strict queued predecessors.

    This is only evidence. It does not say a hospital is available, alter contact
    limits, consume an event, or authorize an inquiry. Unsupported shapes fail
    closed; old/future/nonterminal events cannot be used as new reply evidence.
    """
    try:
        if not _dict(job) or not _dict(latest_decision) or not _dict(snapshot):
            return None
        if any(type(rows) is not list for rows in (own_inquiries,all_inquiries,events)):
            return None
        if any(key not in job for key in JOB_FIELDS):
            return None
        pid = job['patient_id']
        evidence = latest_decision.get('evidence')
        if not _dict(evidence) or not _dict(evidence.get('time_horizon')):
            return None
        temporal = evidence['time_horizon'].get('context')
        if not _dict(temporal) or not _same(temporal.get('patient_id'),pid):
            return None
        prefix = temporal.get('event_prefix_max')
        if not _integer(prefix) or any(key not in latest_decision for key in ('id','at','patient_id','policy_name','job_version')):
            return None
        last = {key:deepcopy(latest_decision[key]) for key in ('id','at','patient_id','policy_name','job_version')}
        last['previous_event_prefix_max'] = prefix
        source = dict(schema_version=1,rule=RULE,now=now,patient_id=deepcopy(pid),policy_name=policy_name,
            capture_snapshot=deepcopy(snapshot),captured_job={key:deepcopy(job[key]) for key in JOB_FIELDS},
            latest_decision=last,event_prefix_max=event_prefix_max,decision_prefix_max=decision_prefix_max,
            remaining_open_inquiry_ids=[],captured_own_open_inquiries=[],terminal_predecessors=[])
        if not _base_valid(source):
            return None
        all_by = {}
        for row in all_inquiries:
            q = _project_inquiry(row)
            if q is None or q['id'] in all_by:
                return None
            all_by[q['id']] = q
        own_by,own_hospitals = {},set()
        for row in own_inquiries:
            q = _project_inquiry(row)
            if q is None or not _same(q['patient_id'],pid) or q['id'] in own_by or q['id'] not in all_by or all_by[q['id']] != q:
                return None
            own_by[q['id']] = q
            if q['state'] in OPEN:
                if q['last_sent_at'] > now or q['hospital_id'] in own_hospitals:
                    return None
                own_hospitals.add(q['hospital_id']);source['captured_own_open_inquiries'].append(q)
        source['captured_own_open_inquiries'].sort(key=lambda q:q['id'])
        source['remaining_open_inquiry_ids'] = [q['id'] for q in source['captured_own_open_inquiries']]
        current_own_open = sorted(q['id'] for q in all_by.values() if _same(q['patient_id'],pid) and q['state'] in OPEN)
        if source['remaining_open_inquiry_ids'] != current_own_open:
            return None
        if not source['remaining_open_inquiry_ids']:
            return None
        seen_public_ids,seen_matched_qids = set(),set()
        for row in events:
            if not _dict(row):
                return None
            if row.get('type') != 'inquiry_responded':
                continue
            eid,at = row.get('id'),row.get('at')
            if not _integer(eid,1) or not prefix < eid <= event_prefix_max or not _time(at) or at > now:
                continue
            if eid in seen_public_ids:
                return None
            seen_public_ids.add(eid)
            data = row.get('data')
            if not _dict(data) or not _integer(data.get('inquiry_id'),1) or data.get('action') not in ('accept','decline'):
                continue
            q = all_by.get(data['inquiry_id'])
            if q is None:
                continue
            if any(key not in row for key in EVENT_FIELDS):
                continue
            event = {key:deepcopy(row[key]) for key in EVENT_FIELDS}
            event['data'] = {key:deepcopy(data[key]) for key in ('inquiry_id','action')}
            for own in source['captured_own_open_inquiries']:
                if not _terminal_join(event,q,own,now=now,pid=pid,prefix=prefix,maximum=event_prefix_max):
                    continue
                if q['id'] in seen_matched_qids:
                    return None
                seen_matched_qids.add(q['id'])
                source['terminal_predecessors'].append(dict(event=event,inquiry=deepcopy(q),own_inquiry=deepcopy(own)))
        source['terminal_predecessors'].sort(key=lambda entry:entry['event']['id'])
        return validate(source)
    except Exception:
        return None
