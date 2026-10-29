"""Pure foreign-public-reply evidence, without a same-hospital restriction.

Only the trigger domain changes. Current own OPEN inquiries, public prefixes,
snapshot identity and terminal current-row joins retain the frozen pure guards.
This helper performs no policy, workflow, database, or model action.
"""
from copy import deepcopy
import hashlib
from pathlib import Path
from . import predecessor_wake_evidence as original

FOUNDATION_SHA256 = '60c1a1ab5d25eb47e49c9c57bc1dea681877fc406d1c7b3347503457ee853b35'
if hashlib.sha256(Path(original.__file__).read_bytes()).hexdigest() != FOUNDATION_SHA256:
    raise ValueError('frozen pure predecessor source differs')
RULE = 'public_foreign_terminal_reply_after_last_recorded_decision_prefix_before_existing_review'
SOURCE_FIELDS = (original.SOURCE_FIELDS - {'terminal_predecessors'}) | {'terminal_foreign_replies'}


def _terminal_join(event,foreign,*,now,pid,prefix,maximum):
    if not original._dict(event) or set(event) != set(original.EVENT_FIELDS)|{'data'}:
        return False
    if not original._integer(event['id'],1) or not prefix < event['id'] <= maximum or not original._time(event['at']) or event['at'] > now:
        return False
    if event['type'] != 'inquiry_responded' or not original._pid(event['patient_id']) or original._same(event['patient_id'],pid):
        return False
    data = event['data']
    if not original._dict(data) or set(data) != {'inquiry_id','action'} or not original._integer(data['inquiry_id'],1):
        return False
    if type(data['action']) is not str or data['action'] not in ('accept','decline'):
        return False
    if data['inquiry_id'] != foreign['id'] or not original._same(event['patient_id'],foreign['patient_id']) or event['hospital_id'] != foreign['hospital_id']:
        return False
    allowed = ('DECLINED',) if data['action'] == 'decline' else ('ACCEPTED','CONFIRMED')
    return foreign['state'] in allowed and original._time(foreign['responded_at']) and foreign['responded_at'] == event['at'] and foreign['last_sent_at'] <= event['at']


def validate(source):
    """Return valid defensive-copy evidence; unsupported or invalid is None."""
    try:
        if not original._dict(source) or set(source) != SOURCE_FIELDS or type(source['schema_version']) is not int or source['schema_version'] != 1 or source['rule'] != RULE:
            return None
        if not original._base_valid(source):
            return None
        own_rows,ids = source['captured_own_open_inquiries'],source['remaining_open_inquiry_ids']
        if type(own_rows) is not list or not own_rows or type(ids) is not list or not ids:
            return None
        own_by,hospitals = {},set()
        for row in own_rows:
            q = original._project_inquiry(row)
            if q is None or set(row) != set(original.INQUIRY_FIELDS) or not original._same(q['patient_id'],source['patient_id']) or q['state'] not in original.OPEN or q['last_sent_at'] > source['now']:
                return None
            if q['id'] in own_by or q['hospital_id'] in hospitals:
                return None
            own_by[q['id']] = q;hospitals.add(q['hospital_id'])
        if any(not original._integer(item,1) for item in ids) or ids != sorted(own_by):
            return None
        rows = source['terminal_foreign_replies']
        if type(rows) is not list or not rows:
            return None
        seen_events,seen_qids = set(),set();previous=0
        for row in rows:
            if not original._dict(row) or set(row) != {'event','inquiry'}:
                return None
            foreign = original._project_inquiry(row['inquiry'])
            if foreign is None or set(row['inquiry']) != set(original.INQUIRY_FIELDS) or foreign['id'] in own_by:
                return None
            event = row['event']
            if not _terminal_join(event,foreign,now=source['now'],pid=source['patient_id'],
                    prefix=source['latest_decision']['previous_event_prefix_max'],maximum=source['event_prefix_max']):
                return None
            if event['id'] in seen_events or foreign['id'] in seen_qids or event['id'] <= previous:
                return None
            seen_events.add(event['id']);seen_qids.add(foreign['id']);previous=event['id']
        return deepcopy(source)
    except Exception:
        return None


def build_public_foreign_reply_wake(*,now,snapshot,job,latest_decision,event_prefix_max,decision_prefix_max,
        own_inquiries,all_inquiries,events,policy_name):
    """Capture any newly public foreign terminal reply while own work is OPEN.

    A foreign reply may update the H model's global public history even at a
    different hospital. It is not evidence of capacity or a safe extra send.
    No row version is inferred from response events; callers must recapture
    current prefixes/snapshots before applying a separately validated action.
    """
    try:
        if not original._dict(job) or not original._dict(latest_decision) or not original._dict(snapshot):
            return None
        if any(type(rows) is not list for rows in (own_inquiries,all_inquiries,events)):
            return None
        if any(key not in job for key in original.JOB_FIELDS):
            return None
        pid=job['patient_id'];evidence=latest_decision.get('evidence')
        if not original._dict(evidence) or not original._dict(evidence.get('time_horizon')):
            return None
        temporal=evidence['time_horizon'].get('context')
        if not original._dict(temporal) or not original._same(temporal.get('patient_id'),pid):
            return None
        prefix=temporal.get('event_prefix_max')
        if not original._integer(prefix) or any(key not in latest_decision for key in ('id','at','patient_id','policy_name','job_version')):
            return None
        last={key:deepcopy(latest_decision[key]) for key in ('id','at','patient_id','policy_name','job_version')}
        last['previous_event_prefix_max']=prefix
        source=dict(schema_version=1,rule=RULE,now=now,patient_id=deepcopy(pid),policy_name=policy_name,
            capture_snapshot=deepcopy(snapshot),captured_job={key:deepcopy(job[key]) for key in original.JOB_FIELDS},
            latest_decision=last,event_prefix_max=event_prefix_max,decision_prefix_max=decision_prefix_max,
            remaining_open_inquiry_ids=[],captured_own_open_inquiries=[],terminal_foreign_replies=[])
        if not original._base_valid(source):
            return None
        all_by={}
        for row in all_inquiries:
            q=original._project_inquiry(row)
            if q is None or q['id'] in all_by:
                return None
            all_by[q['id']]=q
        own_by,hospitals={},set()
        for row in own_inquiries:
            q=original._project_inquiry(row)
            if q is None or not original._same(q['patient_id'],pid) or q['id'] in own_by or q['id'] not in all_by or all_by[q['id']] != q:
                return None
            own_by[q['id']]=q
            if q['state'] in original.OPEN:
                if q['last_sent_at'] > now or q['hospital_id'] in hospitals:
                    return None
                hospitals.add(q['hospital_id']);source['captured_own_open_inquiries'].append(q)
        source['captured_own_open_inquiries'].sort(key=lambda q:q['id'])
        source['remaining_open_inquiry_ids']=[q['id'] for q in source['captured_own_open_inquiries']]
        current_open=sorted(q['id'] for q in all_by.values() if original._same(q['patient_id'],pid) and q['state'] in original.OPEN)
        if not source['remaining_open_inquiry_ids'] or source['remaining_open_inquiry_ids'] != current_open:
            return None
        seen_public_ids,seen_matched_qids=set(),set()
        for row in events:
            if not original._dict(row):
                return None
            if row.get('type') != 'inquiry_responded':
                continue
            eid,at=row.get('id'),row.get('at')
            if not original._integer(eid,1) or not prefix < eid <= event_prefix_max or not original._time(at) or at > now:
                continue
            if eid in seen_public_ids:
                return None
            seen_public_ids.add(eid)
            data=row.get('data')
            if not original._dict(data) or not original._integer(data.get('inquiry_id'),1) or data.get('action') not in ('accept','decline'):
                continue
            q=all_by.get(data['inquiry_id'])
            if q is None or any(key not in row for key in original.EVENT_FIELDS):
                continue
            event={key:deepcopy(row[key]) for key in original.EVENT_FIELDS}
            event['data']={key:deepcopy(data[key]) for key in ('inquiry_id','action')}
            if not _terminal_join(event,q,now=now,pid=pid,prefix=prefix,maximum=event_prefix_max):
                continue
            if q['id'] in seen_matched_qids:
                return None
            seen_matched_qids.add(q['id']);source['terminal_foreign_replies'].append(dict(event=event,inquiry=deepcopy(q)))
        source['terminal_foreign_replies'].sort(key=lambda entry:entry['event']['id'])
        return validate(source)
    except Exception:
        return None
