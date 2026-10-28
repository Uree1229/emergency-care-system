"""A public resource proof for one narrowly extended readiness decision."""
from copy import deepcopy
import math

from .existing_info_engine import decide_early_offer
from .retry_dispatch_policy import _resources


VARIANTS = ('public_blocked',)
SNAPSHOT_KEYS = {'schema_version', 'evaluated_t', 'mode', 'patient_state',
    'first_commit_t', 'wait_minutes', 'original_snapshot', 'pending_inquiries',
    'structural_resource_views'}
PUBLIC_OFFER_SPEC = dict(schema_version=1, variants=list(VARIANTS),
    original_early_policy='simulation.existing_info_engine.decide_early_offer',
    original_not_dominant_only=True, original_full_early_snapshot_retained=True,
    best_offer_rank=['travel_minutes', 'hospital'], all_actual_pending_must_not_rank_better=True,
    each_better_nonpending_structural_candidate_requires_fresh_known_shortage=True,
    shortage='at least one required resource has fresh free quantity below current demand',
    better_candidate_without_any_fresh_required_resource_shortage_blocks=True,
    other_required_resource_unknown_or_stale_does_not_erase_a_fresh_shortage=True,
    original_registered_search_shared_capacity_check=True,
    original_search_pending_valid_first_valid_wait_scope=True, new_threshold=False,
    initial_uncommitted_patient_only=True,
    actual_valid_offer_order_and_allocation_checks_retained=True,
    original_opportunity_pending_and_physical_gates_retained=True,
    actual_pending_and_other_offer_backups_retained=True,
    original_probe_ack_and_late_reply_stages_retained=True,
    inquiry_selector_and_clock_attempt_fifo_workers_resources_ttl_rules_retained=True,
    actual_downstream_paths_and_counts_may_change=True,
    extra_resource_change_publications=False, future_work_or_response_information=False)


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _quarter(value):
    return _number(value) and value * 4 == int(value * 4)


def _id(value):
    return type(value) is str and bool(value)


def _ids(values):
    return type(values) is list and all(_id(x) for x in values) and len(values) == len(set(values))


def _validate(snapshot):
    if type(snapshot) is not dict or set(snapshot) != SNAPSHOT_KEYS \
            or type(snapshot['schema_version']) is not int or snapshot['schema_version'] != 1 \
            or snapshot['mode'] not in ('independent_replies', 'cooperative_queue'):
        raise ValueError('exact public readiness snapshot required')
    now, original = snapshot['evaluated_t'], snapshot['original_snapshot']
    if not _quarter(now) or snapshot['wait_minutes'] != 2 or type(snapshot['wait_minutes']) is not int \
            or not _id(snapshot['patient_state']) or (snapshot['first_commit_t'] is not None and (
                not _quarter(snapshot['first_commit_t']) or snapshot['first_commit_t'] > now)):
        raise ValueError('original current readiness clocks and state required')
    if type(original) is not dict or set(original) != {'t', 'patient', 'first_valid_t',
            'pending_hospitals', 'valid_offers', 'structural_candidates', 'search_patients'} or original['t'] != now:
        raise ValueError('complete original early-readiness snapshot required')
    patient = original['patient']
    patient_keys = {'id', 'priority', 'request_t', 'demand', 'required_capabilities'}
    def validate_patient(row, extra=frozenset()):
        if type(row) is not dict or set(row) != patient_keys | extra or not _id(row['id']) \
                or type(row['priority']) is not int or row['priority'] < 1 \
                or not _quarter(row['request_t']) or row['request_t'] > now \
                or type(row['demand']) is not dict or not row['demand'] \
                or any(not _id(k) or type(v) is not int or v < 1 for k, v in row['demand'].items()) \
                or not _ids(row['required_capabilities']):
            raise ValueError('current registered patient condition required')
        if extra and not _ids(row['compatible_hospitals']):
            raise ValueError('original public structural compatibility required')
    validate_patient(patient)
    demand = patient['demand']
    if original['first_valid_t'] is not None and (
            not _quarter(original['first_valid_t']) or original['first_valid_t'] > now):
        raise ValueError('actual first valid-offer clock required')
    if not _ids(original['pending_hospitals']) or type(original['structural_candidates']) is not list:
        raise ValueError('original structural and pending hospital ledgers required')
    candidates = {}
    for row in original['structural_candidates']:
        if type(row) is not dict or set(row) != {'hospital', 'travel_minutes'} \
                or not _id(row['hospital']) or row['hospital'] in candidates or not _number(row['travel_minutes']):
            raise ValueError('unique finite original structural routes required')
        candidates[row['hospital']] = row
    views = snapshot['structural_resource_views']
    if type(views) is not dict or set(views) != set(candidates):
        raise ValueError('full current structural resource views required')
    for resources in views.values():
        _resources(resources, now)
        if set(resources) != set(demand):
            raise ValueError('all current required resources must be retained')
    pending = snapshot['pending_inquiries']
    if type(pending) is not list:
        raise ValueError('exact original public pending handles required')
    pending_hospitals = []
    for row in pending:
        if type(row) is not dict or set(row) != {'hospital', 'attempt', 'submitted_at', 'timeout_at', 'ask_event_sequence'} \
                or not _id(row['hospital']) or row['hospital'] not in candidates \
                or type(row['attempt']) is not int or row['attempt'] < 1 \
                or not _quarter(row['submitted_at']) or row['submitted_at'] > now \
                or not _quarter(row['timeout_at']) or row['timeout_at'] < now \
                or type(row['ask_event_sequence']) is not int or row['ask_event_sequence'] < 0:
            raise ValueError('actual pending identity, route and original deadline required')
        pending_hospitals.append(row['hospital'])
    if len(pending_hospitals) != len(set(pending_hospitals)) \
            or sorted(pending_hospitals) != original['pending_hospitals']:
        raise ValueError('complete pending handles must match original hospital ledger')
    if type(original['valid_offers']) is not list:
        raise ValueError('complete current accepted-offer ledger required')
    offer_ids = []
    for row in original['valid_offers']:
        if type(row) is not dict or set(row) != {'hospital', 'accepted_at', 'expires_at', 'resources', 'travel_minutes'} \
                or row['hospital'] not in candidates or not _quarter(row['accepted_at']) or row['accepted_at'] > now \
                or not _quarter(row['expires_at']) or row['expires_at'] <= now \
                or row['travel_minutes'] != candidates[row['hospital']]['travel_minutes'] \
                or row['resources'] != views[row['hospital']]:
            raise ValueError('actual unexpired offers and unchanged current views required')
        offer_ids.append(row['hospital'])
    if len(offer_ids) != len(set(offer_ids)):
        raise ValueError('one current offer per hospital required')
    if type(original['search_patients']) is not list:
        raise ValueError('complete registered search cohort required')
    for row in original['search_patients']:
        validate_patient(row, {'compatible_hospitals'})
    cohort = original['search_patients']
    if len({row['id'] for row in cohort}) != len(cohort) or sum(row['id'] == patient['id'] for row in cohort) != 1:
        raise ValueError('current patient must occur once in complete search cohort')
    own_row = next(row for row in cohort if row['id'] == patient['id'])
    if {key: own_row[key] for key in patient_keys} != patient:
        raise ValueError('current patient must agree with its registered search row')
    return original, candidates, views


def decide_public_offer(*, snapshot, variant='public_blocked'):
    if variant not in VARIANTS:
        raise ValueError('known fixed public-offer variant required')
    original, candidates, views = _validate(snapshot)
    shadow = decide_early_offer(deepcopy(original))
    output = dict(schema_version=1, variant=variant, eligible=False, hospital=None,
        reason='ORIGINAL_NOT_NOT_DOMINANT', original_reason=shadow['reason'],
        best_offer_hospital=None, pending_checks=[], candidate_checks=[])
    now, demand = snapshot['evaluated_t'], original['patient']['demand']
    if snapshot['patient_state'] != 'SEARCH' or snapshot['first_commit_t'] is not None \
            or not snapshot['pending_inquiries'] \
            or not original['valid_offers'] or original['first_valid_t'] is None \
            or now - original['first_valid_t'] >= snapshot['wait_minutes']:
        return dict(output, reason='OUTSIDE_ORIGINAL_READINESS_SCOPE')
    if shadow['reason'] != 'NOT_DOMINANT':
        return output
    usable = [offer for offer in original['valid_offers'] if all(
        value['status'] == 'fresh' and value['free'] is not None and value['free'] >= demand[r]
        for r, value in offer['resources'].items())]
    best = min(usable, key=lambda row: (row['travel_minutes'], row['hospital']))
    rank = best['travel_minutes'], best['hospital']
    output['best_offer_hospital'] = best['hospital']
    pending_ids = {row['hospital'] for row in snapshot['pending_inquiries']}
    output['pending_checks'] = [dict(hospital=row['hospital'], at_least_offer_rank=
        (candidates[row['hospital']]['travel_minutes'], row['hospital']) >= rank)
        for row in snapshot['pending_inquiries']]
    for hid, candidate in candidates.items():
        if (candidate['travel_minutes'], hid) >= rank:
            continue
        shortages = sorted(r for r, view in views[hid].items() if view['status'] == 'fresh'
            and view['free'] is not None and view['free'] < demand[r])
        output['candidate_checks'].append(dict(hospital=hid, pending=hid in pending_ids,
            shortage_resources=shortages, publicly_blocked=hid not in pending_ids and bool(shortages)))
    if not all(row['at_least_offer_rank'] for row in output['pending_checks']):
        return dict(output, reason='BETTER_ACTUAL_PENDING')
    if not all(row['publicly_blocked'] for row in output['candidate_checks']):
        return dict(output, reason='BETTER_STRUCTURAL_NOT_PUBLICLY_BLOCKED')
    contenders = [row for row in original['search_patients']
        if best['hospital'] in row['compatible_hospitals']]
    if any(best['resources'][r]['free'] < sum(row['demand'].get(r, 0) for row in contenders) for r in demand):
        return dict(output, reason='SHARED_CAPACITY')
    return dict(output, eligible=True, hospital=best['hospital'], reason='PUBLICLY_BLOCKED_CLOSER_ALTERNATIVES')
