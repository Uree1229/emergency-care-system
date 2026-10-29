"""A minimal view of previously committed, still recent public query plans.

This is an intent view for a soft veto, not a bed reservation or a claim that
an owner's future plan will execute. Original journal rows remain in raw.
"""

REVIEW_SECONDS = 15
POSITIVE_OFFSETS = {15, 30, 60, 90, 120, 180}


def _patient(value):
    return type(value) is int or type(value) is str and bool(value)


def build_context(*, now, patient_id, event_prefix_max, decision_prefix_max,
                  pending, events, decisions):
    """Filter one committed read snapshot, preserving original plan order."""
    if type(now) is not int or now < 0 or not _patient(patient_id) or any(
            type(x) is not int or x < 0 for x in (event_prefix_max, decision_prefix_max)):
        raise ValueError('current patient, integer clock and nonnegative prefixes required')
    owners = {row['patient'] for row in pending if row['patient'] != patient_id}
    contacted = {owner: set() for owner in owners}
    for event in events:
        if event['id'] > event_prefix_max or event['at'] > now:
            continue
        owner = event.get('patient_id')
        if owner in owners and event['type'] in ('inquiry_sent', 'inquiry_retried'):
            contacted[owner].add(event['hospital_id'])
    latest = {}
    for row in decisions:
        if row['id'] > decision_prefix_max or row['patient_id'] not in owners:
            continue
        owner = row['patient_id']
        if owner not in latest or row['id'] > latest[owner]['id']:
            latest[owner] = row
    plans = []
    for row in sorted(latest.values(), key=lambda value: value['id']):
        if not 0 <= now-row['at'] <= REVIEW_SECONDS:
            continue
        owner = row['patient_id']; plan = []
        for item in row['evidence'].get('candidate_plan', []):
            offset = item['planned_offset_seconds']
            if offset is not None and (type(offset) is not int or offset not in POSITIVE_OFFSETS|{0}):
                raise ValueError('original plan offset differs')
            if offset is None or offset == 0:
                continue
            hospital = item['hospital']
            if type(hospital) is not str or not hospital:
                raise ValueError('public hospital identifier required')
            if hospital not in contacted[owner]:
                plan.append(dict(hospital=hospital, planned_offset_seconds=offset))
        if plan:
            plans.append(dict(patient=owner, decision_id=row['id'], at=row['at'], candidate_plan=plan))
    return dict(patient_id=patient_id, event_prefix_max=event_prefix_max,
                decision_prefix_max=decision_prefix_max, plans=plans)
