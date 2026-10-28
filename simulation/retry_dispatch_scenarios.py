"""Retry/frontier and shared-contact boundaries without changing old inputs.

The preceding 95 complete environments are copied unchanged. Fourteen detached
new environments use existing response, FIFO, occupancy and observation fields.
Only small structural seeds are used before all experimental sources are frozen.
"""
from __future__ import annotations

from copy import deepcopy

from .cooperative_probe_scenarios import (
    FAMILIES as ORIGINAL_FAMILIES,
    make_scenario as make_previous_scenario,
)
from .joint_policy_scenarios import _validate_extra_inputs
from .map_replay import validate_scenario


DEV_SEEDS = (44101, 44102, 44103)
DEVELOPMENT_SEEDS = DEV_SEEDS
VALIDATION_SEEDS = (45101, 45102, 45103, 45104, 45105, 45106)
ADDITIONAL_FAMILIES = (
    'retry_dispatch_frontier_last_responsive',
    'retry_dispatch_frontier_all_silent',
    'retry_dispatch_first_silent_then_reply',
    'retry_dispatch_decline_then_recovery',
    'retry_dispatch_reply_before_deadline',
    'retry_dispatch_reply_at_deadline',
    'retry_dispatch_reply_after_deadline',
    'retry_dispatch_pending_slow_work',
    'retry_dispatch_shared_fifo_burst',
    'retry_dispatch_priority_reversal',
    'retry_dispatch_future_request',
    'retry_dispatch_unknown_unresolved',
    'retry_dispatch_stale_unresolved',
    'retry_dispatch_new_report_old_control',
)
FAMILIES = ORIGINAL_FAMILIES + ADDITIONAL_FAMILIES
BASE_FAMILIES = {
    'retry_dispatch_frontier_last_responsive': 'scarce_contact_concurrency_limits',
    'retry_dispatch_frontier_all_silent': 'scarce_contact_concurrency_limits',
    'retry_dispatch_first_silent_then_reply': 'silent_first_options',
    'retry_dispatch_decline_then_recovery': 'silent_first_options',
    'retry_dispatch_reply_before_deadline': 'silent_first_options',
    'retry_dispatch_reply_at_deadline': 'silent_first_options',
    'retry_dispatch_reply_after_deadline': 'silent_first_options',
    'retry_dispatch_pending_slow_work': 'shared_fifo_burst',
    'retry_dispatch_shared_fifo_burst': 'shared_fifo_burst',
    'retry_dispatch_priority_reversal': 'scarce_priority_reversal',
    'retry_dispatch_future_request': 'scarce_late_request',
    'retry_dispatch_unknown_unresolved': 'scarce_alternative_unknown',
    'retry_dispatch_stale_unresolved': 'scarce_alternative_stale',
    'retry_dispatch_new_report_old_control': 'cooperative_probe_busy_early_report',
}
_LABELS = {
    'retry_dispatch_frontier_last_responsive': 'Twelve ordinary candidates; only the last one supplies a reply',
    'retry_dispatch_frontier_all_silent': 'All twelve ordinary candidates remain silent',
    'retry_dispatch_first_silent_then_reply': 'The first actual clinical review is silent; the next can reply',
    'retry_dispatch_decline_then_recovery': 'An initially optimistic report precedes a real decline and a later existing-feed recovery',
    'retry_dispatch_reply_before_deadline': 'A real reply needs 2.75 minutes before the original three-minute deadline',
    'retry_dispatch_reply_at_deadline': 'A real reply needs exactly the original three-minute deadline',
    'retry_dispatch_reply_after_deadline': 'A real reply needs 3.25 minutes beyond the original deadline',
    'retry_dispatch_pending_slow_work': 'Eight requests retain long actual FIFO work after their handles expire',
    'retry_dispatch_shared_fifo_burst': 'Eight simultaneous requests share one actual reviewer per hospital',
    'retry_dispatch_priority_reversal': 'A higher supplied-priority flexible patient has an initially silent alternative',
    'retry_dispatch_future_request': 'A limited patient first registers after the flexible patient has started contacting hospitals',
    'retry_dispatch_unknown_unresolved': 'A patient acceptance cannot fill permanently missing alternative-bed counts',
    'retry_dispatch_stale_unresolved': 'A patient acceptance cannot refresh permanently stale alternative-bed counts',
    'retry_dispatch_new_report_old_control': 'Out-of-order resource reports coexist with actual current queue controls',
}


def _report(hospital, *, observed_at=0, used=0):
    return dict(hospital=hospital['id'], observed_at=observed_at,
        resources={resource: dict(capacity=quantity, used=used)
                   for resource, quantity in hospital['capacity'].items()})


def _new_case(seed, family):
    base_family = BASE_FAMILIES[family]
    case = make_previous_scenario(seed, base_family)
    case.update(id=f'retry-dispatch-{family}-{seed}', family=family,
        retry_dispatch_family=family, label=_LABELS[family], seed=seed)
    for key in ('joint_policy_family', 'scarce_delay_family', 'cooperative_probe_family',
                'shortage_probe_family'):
        case.pop(key, None)
    matrix = case['response_matrix']
    details = dict(base_environment_family=base_family,
        original_concurrent_handle_limit=3, original_episode_contact_limit=10,
        raw_branch_reachability_must_be_verified=True,
        intended_public_policy_inputs='actual registered requests, actual contacts and already received reports only')
    if family == 'retry_dispatch_frontier_last_responsive':
        # Keep the existing twelve hospitals, two patients and physical supply.
        # Actual service is a half-minute for every hospital in every branch.
        for hospital in case['hospitals']:
            matrix['*'][hospital['id']] = dict(delay=.5, silent=hospital['id'] != 'H12')
        details.update(intent='episode renewal must not erase the unvisited frontier',
            responsive_hospital='H12', structural_candidates_for_flexible=12)
    elif family == 'retry_dispatch_frontier_all_silent':
        for hospital in case['hospitals']:
            matrix['*'][hospital['id']] = dict(delay=.5, silent=True)
        details.update(intent='suppress repeated work without inventing a responsive hospital',
            responsive_hospitals=[], structural_candidates_for_flexible=12)
    elif family == 'retry_dispatch_first_silent_then_reply':
        for hospital in case['hospitals']:
            matrix['*'][hospital['id']] = dict(attempts=[
                dict(delay=.25, silent=True), dict(delay=.5, silent=False)])
        details.update(intent='a completed timeout is not a permanent clinical exclusion',
            actual_clinical_review_attempt_dependent=True)
    elif family == 'retry_dispatch_decline_then_recovery':
        first = case['hospitals'][0]
        first['initial_occupancy'] = [dict(until=4, demand=deepcopy(first['capacity']))]
        # The input truth is occupied; an already received optimistic report is
        # deliberately wrong. Existing normal feeds can later reveal recovery.
        case['observation_config']['initial_reports'] = [_report(h) for h in case['hospitals']]
        matrix['*'] = {h['id']: dict(delay=.5, silent=h['id'] != first['id'])
                      for h in case['hospitals']}
        details.update(intent='real clinical decline can later be reconsidered using existing fresh reports',
            physically_occupied_hospital=first['id'], physical_initial_occupancy_until=4,
            initial_report_disagrees_with_physical_occupancy=True,
            responsive_hospital=first['id'])
    elif family in ('retry_dispatch_reply_before_deadline', 'retry_dispatch_reply_at_deadline',
                    'retry_dispatch_reply_after_deadline'):
        work = {'retry_dispatch_reply_before_deadline': 2.75,
                'retry_dispatch_reply_at_deadline': 3,
                'retry_dispatch_reply_after_deadline': 3.25}[family]
        matrix['*'] = {h['id']: dict(delay=work if h['id'] == 'H01' else .25,
                                    silent=h['id'] != 'H01') for h in case['hospitals']}
        details.update(intent='new retry scheduling cannot renew the original inquiry deadline',
            first_responsive_hospital='H01', actual_service_minutes=work,
            original_timeout_minutes=3)
    elif family == 'retry_dispatch_pending_slow_work':
        for row in matrix.values():
            for hospital in case['hospitals']:
                row[hospital['id']] = dict(delay=2.75, silent=False)
        details.update(intent='expired contact handles do not erase submitted FIFO work',
            simultaneous_patients=8, actual_service_minutes=2.75,
            original_existing_workers_per_hospital=1)
    elif family == 'retry_dispatch_shared_fifo_burst':
        # Keep original random per-patient service work and physical capacity.
        # A real initial background job makes shared pressure explicit.
        case['inquiry_queue']['background_jobs'] = [dict(t=0, hospital='H01', service_minutes=2)]
        details.update(intent='distribute contact load across the current cohort without adding reviewers',
            simultaneous_patients=8, actual_background_work_minutes=2,
            original_existing_workers_per_hospital=1)
    elif family == 'retry_dispatch_priority_reversal':
        matrix['*']['H02'] = dict(attempts=[dict(delay=.5, silent=True),
                                                   dict(delay=.5, silent=False)])
        details.update(intent='less flexible lower supplied-priority demand does not force a higher priority to yield',
            supplied_priorities={p['id']: p['priority'] for p in case['patients']},
            priorities_are_experimental_weights_not_inferred_clinical_KTAS=True)
    elif family == 'retry_dispatch_future_request':
        matrix['*'].update(H01=dict(delay=2.75, silent=False), H02=dict(delay=.5, silent=False))
        details.update(intent='later limited demand cannot affect earlier policy decisions',
            later_patient='P02', actual_later_registration_minute=1)
    elif family in ('retry_dispatch_unknown_unresolved', 'retry_dispatch_stale_unresolved'):
        matrix['*'].update(H01=dict(delay=1.5, silent=False), H02=dict(delay=.25, silent=False))
        details.update(intent='an acceptance does not resolve missing or stale resource quantities',
            unavailable_resource_quality='unknown' if 'unknown' in family else 'stale',
            unverified_hospital='H02', confirmation_stays_silent=True)
    elif family == 'retry_dispatch_new_report_old_control':
        case['observation_config']['feed_delay_by_source_minute'] = {'0': 2, '1': 0}
        details.update(intent='later receipt of an older report or existing control does not refresh original clinical handles',
            resource_source_zero_delivery_minute=2, resource_source_one_delivery_minute=1,
            queue_controls_are_not_resource_reports=True)
    else:
        raise AssertionError('unhandled retry-dispatch boundary')
    case['assumptions'] = [*case['assumptions'],
        'This detached retry boundary preserves its parent hospital capacities, staff count, original supplied priorities and physical route estimates.',
        'Response attempt calendars, true occupancy, future reports and future arrivals are private environment inputs, not inquiry policy observations.',
        'Unknown and stale resource quantities never become current free quantities because of patient-specific acceptance or queue control.',
        'Actual contacts retain original identities, expiry and work; an episode transition is not permission to erase lifetime contact history.',
        'No added resource-change report, reviewer, cancellation speedup, service-work shortcut or automatic destination reroute is introduced.']
    case['experiment'] = dict(seed=seed, patient_count=len(case['patients']),
        retry_dispatch_family=family, comparison_scope='same_observations_bounded_retry_and_current_cohort_dispatch',
        clinical_values='synthetic', identity_source='synthetic', routing='synthetic_fixed_coordinates',
        timing_unit='real_minutes', review_resolution_seconds=15, history_timing_unit='relative_seconds',
        additional_resource_change_channel_enabled=False, **details)
    validate_scenario(case)
    _validate_extra_inputs(case)
    return case


def make_scenario(seed, family):
    """Return one detached input; preceding 95 values remain unchanged."""
    if type(seed) is not int or seed < 0:
        raise ValueError('seed must be a nonnegative integer')
    if not isinstance(family, str) or family not in FAMILIES:
        raise ValueError('unknown retry-dispatch family')
    return deepcopy(make_previous_scenario(seed, family) if family in ORIGINAL_FAMILIES else _new_case(seed, family))


def build_retry_dispatch_scenarios(seed, families=None):
    """Build all 109 cases, or an ordered distinct family selection."""
    if type(seed) is not int or seed < 0:
        raise ValueError('seed must be a nonnegative integer')
    if families is None:
        selected = list(FAMILIES)
    else:
        if isinstance(families, (str, bytes)):
            raise ValueError('families must be a distinct iterable of family names')
        try:
            selected = list(families)
        except TypeError as exc:
            raise ValueError('families must be a distinct iterable of family names') from exc
        if any(not isinstance(family, str) or family not in FAMILIES for family in selected) \
                or len(selected) != len(set(selected)):
            raise ValueError('unknown or duplicate retry-dispatch family')
    return [make_scenario(seed, family) for family in selected]
