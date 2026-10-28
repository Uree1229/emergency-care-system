"""Experimental inquiry dispatch from actual contacts and received public data.

The original V4 slot remains a cost kernel. This module explicitly changes the
candidate tier and, in two ablations, preserves a retry wait that an unchanged
feed would otherwise cancel. No work completion calendar is an input.
"""
from copy import deepcopy
from hashlib import sha256
import json
import math

from server import inquiry_v4


VARIANTS = ('frontier', 'paced', 'balanced')
RETRY_DISPATCH_SPEC = dict(
    schema_version=1, variants=list(VARIANTS), internal_inquiry_slot='v4',
    actual_selector_prefix='retry_dispatch_', base_kernel='server.inquiry_v4.decide',
    original_seven_inputs_retained=True, initial_unaccepted_search_only=True,
    frontier='least_actual_contacts_for_current_condition_across_current_eligible_universe',
    pacing='preserve_actual_future_previous_retry_at_from_existing_report_wakes',
    pacing_change_exceptions=['capacity_or_used_changed', 'unknown_or_stale_to_fresh'],
    timestamps_alone_release_retry_wait=False, backoff_added=False,
    balancing='least_current_public_unresolved_hospital_count_after_frontier_and_pacing',
    kernel_sends_only_original_offered_candidates=True,
    maximum_open_inquiries=3, maximum_episode_contacts=10,
    episode_transition_unchanged=True, actual_contact_clock_unchanged=True,
    original_fifo_timeout_offer_ttl_and_acceptance_unchanged=True,
    workers_added=False, extra_resource_change_publications=False,
    hidden_work_calendar_visible=False)
INPUT_KEYS = {'now', 'candidates', 'observations', 'pending', 'patient_pending',
              'max_new', 'last_contact_at'}
CONTEXT_KEYS = {'schema_version', 'evaluated_t', 'patient', 'episode', 'universe',
                'contacts', 'retry_barriers'}
PATIENT_KEYS = {'id', 'state', 'first_commit_t', 'first_accept_event_sequence',
                'condition', 'condition_fingerprint'}
CONTACT_KEYS = {'patient', 'hospital', 'attempt', 'submitted_at', 'ask_event_sequence',
                'condition_fingerprint', 'resources'}
BARRIER_KEYS = {'patient', 'hospital', 'received_t', 'wake_event_sequence',
                'report_event_sequence', 'previous_retry_at', 'condition_fingerprint', 'resources'}
VIEW_KEYS = {'capacity', 'used', 'committed', 'queued', 'free', 'observed_at',
             'received_at', 'sequence', 'source', 'status', 'age_minutes'}


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _quarter(value):
    return _number(value) and value * 4 == int(value * 4)


def _id(value):
    return isinstance(value, str) and bool(value)


def _seq(value):
    return type(value) is int and value >= 0


def condition_fingerprint(condition):
    """The frozen public-condition hash, without importing replay/solver code."""
    if not isinstance(condition, dict) or set(condition) != {
            'id', 'priority', 'demand', 'required_capabilities'} \
            or not _id(condition['id']) or type(condition['priority']) is not int \
            or condition['priority'] < 1 or not isinstance(condition['demand'], dict) \
            or not condition['demand'] or any(not _id(r) or type(q) is not int or q < 1
                for r, q in condition['demand'].items()) \
            or not isinstance(condition['required_capabilities'], list) \
            or any(not isinstance(c, str) for c in condition['required_capabilities']) \
            or len(set(condition['required_capabilities'])) != len(condition['required_capabilities']):
        raise ValueError('exact current public condition required')
    row = deepcopy(condition)
    row['required_capabilities'].sort()
    return sha256(json.dumps(row, sort_keys=True, ensure_ascii=False,
                            separators=(',', ':')).encode('utf-8')).hexdigest()


def _resources(rows, at=None):
    if not isinstance(rows, dict) or not rows:
        raise ValueError('current public resource views required')
    for resource, value in rows.items():
        if not _id(resource) or not isinstance(value, dict) or set(value) != VIEW_KEYS:
            raise ValueError('public resource quantities and quality required')
        if value['status'] not in ('fresh', 'stale', 'unknown'):
            raise ValueError('known public quality required')
        for key in ('capacity', 'used'):
            if value[key] is not None and not _number(value[key]):
                raise ValueError('public quantity must be finite and nonnegative')
        if any(not _number(value[key]) for key in ('committed', 'queued')):
            raise ValueError('known commitments and queued quantities required')
        if value['free'] is not None and (type(value['free']) not in (int, float)
                or not math.isfinite(value['free'])):
            raise ValueError('public free quantity must be finite')
        known = value['capacity'] is not None and value['used'] is not None
        expected = value['capacity']-value['used']-value['committed']-value['queued'] if known else None
        if value['free'] != expected or (known and value['status'] == 'unknown') \
                or (not known and value['status'] != 'unknown'):
            raise ValueError('public resource arithmetic or quality differs')
        observed, received, age = value['observed_at'], value['received_at'], value['age_minutes']
        if observed is None:
            if any(value[key] is not None for key in ('received_at','sequence','source','age_minutes')) or known:
                raise ValueError('unknown report metadata differs')
        elif (type(observed) not in (int, float) or not math.isfinite(observed)) \
                or not _number(received) or received < observed \
                or not _number(age) or not _seq(value['sequence']) or not _id(value['source']):
            raise ValueError('actual past report metadata required')
        elif at is not None and (received > at or observed > at or age != at-observed):
            raise ValueError('future report or incorrect observed age')


def resource_change_reason(before, current):
    """Only resource content or a quality recovery releases an existing wait."""
    _resources(before)
    _resources(current)
    if set(before) != set(current):
        return None
    for resource in sorted(current):
        old, new = before[resource], current[resource]
        if old['status'] in ('unknown', 'stale') and new['status'] == 'fresh':
            return 'unknown_or_stale_to_fresh'
        if all(old[key] is not None and new[key] is not None for key in ('capacity', 'used')) \
                and any(old[key] != new[key] for key in ('capacity', 'used')):
            return 'capacity_or_used_changed'
    return None


def validate_context(inputs, context):
    """Reject hidden or future fields rather than silently dropping them."""
    if not isinstance(inputs, dict) or set(inputs) != INPUT_KEYS:
        raise ValueError('exact original seven selector inputs required')
    if not isinstance(context, dict) or set(context) != CONTEXT_KEYS \
            or context['schema_version'] != 1 or type(context['schema_version']) is not int:
        raise ValueError('exact versioned public dispatch context required')
    now, patient = context['evaluated_t'], context['patient']
    if not _quarter(now) or inputs['now'] != 86400 + int(now * 60) \
            or not isinstance(patient, dict) or set(patient) != PATIENT_KEYS \
            or not _id(patient['id']) or not _id(patient['state']):
        raise ValueError('matching current patient and quarter clock required')
    if patient['first_commit_t'] is not None and (
            not _quarter(patient['first_commit_t']) or patient['first_commit_t'] > now):
        raise ValueError('future or invalid first commitment')
    if patient['first_accept_event_sequence'] is not None and not _seq(patient['first_accept_event_sequence']):
        raise ValueError('actual first acceptance event required')
    fingerprint = condition_fingerprint(patient['condition'])
    if patient['condition']['id'] != patient['id'] or fingerprint != patient['condition_fingerprint']:
        raise ValueError('current condition fingerprint differs')
    episode = context['episode']
    if not isinstance(episode, dict) or set(episode) != {'id', 'contacts'} \
            or not _seq(episode['id']) or not _seq(episode['contacts']) or episode['contacts'] > 10:
        raise ValueError('actual episode and original ten-contact budget required')
    universe = context['universe']
    if not isinstance(universe, list):
        raise ValueError('public eligible universe required')
    seen = set()
    for row in universe:
        if not isinstance(row, dict) or set(row) != {'hospital', 'travel_minutes', 'resources'} \
                or not _id(row['hospital']) or row['hospital'] in seen or not _number(row['travel_minutes']):
            raise ValueError('unique eligible hospitals required')
        seen.add(row['hospital'])
        _resources(row['resources'], now)
    if any(row['hospital_id'] not in seen for row in inputs['candidates']):
        raise ValueError('original offered candidate outside current eligible universe')
    for key, expected, time_key, reference in (
            ('contacts', CONTACT_KEYS, 'submitted_at', 'ask_event_sequence'),
            ('retry_barriers', BARRIER_KEYS, 'received_t', 'wake_event_sequence')):
        rows = context[key]
        if not isinstance(rows, list):
            raise ValueError('actual past contacts and wake events required')
        refs = []
        for row in rows:
            if not isinstance(row, dict) or set(row) != expected or row['patient'] != patient['id'] \
                    or not _id(row['hospital']) or not _quarter(row[time_key]) or row[time_key] > now \
                    or not _seq(row[reference]) or not _id(row['condition_fingerprint']):
                raise ValueError('exact observed past dispatch evidence required')
            if key == 'contacts':
                if type(row['attempt']) is not int or row['attempt'] < 1:
                    raise ValueError('actual positive contact attempt required')
            elif not _seq(row['report_event_sequence']) or (row['previous_retry_at'] is not None
                    and not _quarter(row['previous_retry_at'])):
                raise ValueError('original retry time and applied report required')
            _resources(row['resources'], row[time_key])
            refs.append(row[reference])
        if refs != sorted(set(refs)):
            raise ValueError('past evidence must be unique in original event order')
    return fingerprint


def decide(variant, *, inputs, context):
    """Apply one fixed ablation, then V4 within its disclosed candidate tier."""
    if variant not in VARIANTS:
        raise ValueError('unknown retry dispatch variant')
    fingerprint = validate_context(inputs, context)
    patient, now = context['patient'], context['evaluated_t']
    active = (patient['state'] == 'SEARCH' and patient['first_commit_t'] is None
              and patient['first_accept_event_sequence'] is None)
    original_ids = [row['hospital_id'] for row in inputs['candidates']]
    contacts = [row for row in context['contacts'] if row['condition_fingerprint'] == fingerprint]
    counts = {row['hospital']: 0 for row in context['universe']}
    latest = {}
    for row in contacts:
        if row['hospital'] in counts:
            counts[row['hospital']] += 1
            latest[row['hospital']] = row
    minimum = min(counts.values(), default=None)
    frontier = [row['hospital'] for row in context['universe']
                if counts[row['hospital']] == minimum]
    tier = [hospital for hospital in original_ids if hospital in frontier] if active else list(original_ids)
    barriers = [row for row in context['retry_barriers']
                if row['condition_fingerprint'] == fingerprint and row['previous_retry_at'] is not None
                and row['previous_retry_at'] > now]
    until = max((row['previous_retry_at'] for row in barriers), default=None)
    changes, blocked = {}, []
    if active and variant in ('paced', 'balanced') and until is not None:
        resources = {row['hospital']: row['resources'] for row in context['universe']}
        for hospital in tier:
            reason = resource_change_reason(latest[hospital]['resources'], resources[hospital]) \
                if hospital in latest else None
            if reason is None:
                blocked.append(hospital)
            else:
                changes[hospital] = reason
        tier = [hospital for hospital in tier if hospital not in blocked]
    pacing_tier = list(tier)
    pending_keys = {(row['hospital'], row['patient'], row['submitted_at']) for row in inputs['pending']
                    if row['submitted_at'] <= inputs['now']}
    loads = {hospital: sum(key[0] == hospital for key in pending_keys) for hospital in tier}
    if active and variant == 'balanced' and loads:
        low = min(loads.values())
        tier = [hospital for hospital in tier if loads[hospital] == low]
    kernel_inputs = deepcopy(inputs)
    kernel_inputs['candidates'] = [row for row in kernel_inputs['candidates'] if row['hospital_id'] in tier]
    output = inquiry_v4.decide(**kernel_inputs)
    kernel_decision = deepcopy(output)
    if active and not tier and original_ids:
        output['reason'] = 'retry_wait_preserved' if blocked else 'frontier_not_offered'
    detail = dict(variant=variant, scope_active=active, original_candidate_ids=original_ids,
        contact_counts=[dict(hospital=h, contacts=counts[h]) for h in sorted(counts)],
        minimum_contact_count=minimum, frontier_hospitals=frontier,
        active_retry_wake_event_sequences=[row['wake_event_sequence'] for row in barriers],
        preserved_retry_until=until, retry_blocked_hospitals=blocked,
        meaningful_change_exceptions=changes, pacing_candidate_ids=pacing_tier,
        pending_loads=[dict(hospital=h, unresolved=loads[h]) for h in sorted(loads)],
        kernel_candidate_ids=list(tier), kernel_decision=kernel_decision)
    output['evidence'].update(policy='retry_dispatch_' + variant, base_policy='observed_v4',
                              retry_dispatch=detail)
    return output
