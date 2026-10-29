"""Boundary environments for nonblocking, bounded late-reply revalidation.

All preceding 67 scarce-delay inputs remain complete value-for-value copies.
Five additional environments vary private hospital work or actually occurring
resource/patient events. The policy must never inspect those future inputs.
Development 35101-35103 and validation 36101-36106 are newly reserved; structural
tests use only seeds 7 and 8 and do not execute either partition.
"""
from __future__ import annotations

from copy import deepcopy

from .joint_policy_scenarios import _validate_extra_inputs
from .map_replay import validate_scenario
from .scarce_delay_scenarios import (
    FAMILIES as ORIGINAL_FAMILIES,
    make_scenario as make_scarce_delay_scenario,
)


DEV_SEEDS = (35101, 35102, 35103)
DEVELOPMENT_SEEDS = DEV_SEEDS
VALIDATION_SEEDS = (36101, 36102, 36103, 36104, 36105, 36106)
ADDITIONAL_FAMILIES = (
    'deadline_reply_at_350',
    'deadline_reply_at_375',
    'deadline_silent_alternative',
    'deadline_capacity_lost_before_receipt',
    'deadline_later_single_candidate',
)
FAMILIES = ORIGINAL_FAMILIES + ADDITIONAL_FAMILIES

_BASE_FAMILY = 'scarce_alternative_late_deadline'
_H02_ONLY_CAPABILITY = 'SYNTHETIC_H02_ONLY'
_LABELS = {
    'deadline_reply_at_350': 'Late reply completes 30 seconds after the original deadline',
    'deadline_reply_at_375': 'Late reply completes 45 seconds after the original deadline',
    'deadline_silent_alternative': 'Private hospital work finishes but no reply is delivered',
    'deadline_capacity_lost_before_receipt': 'Current received resource report is insufficient at late receipt',
    'deadline_later_single_candidate': 'A later actually registered patient can use only the late-reply hospital',
}


def _new_case(seed, family):
    # The old 3.25-minute boundary remains an inherited family. A detached copy
    # supplies its unchanged queue, bed, travel and patient timing contracts.
    case = make_scarce_delay_scenario(seed, _BASE_FAMILY)
    case.update(id=f'deadline-recovery-{family}-{seed}', family=family,
                deadline_recovery_family=family, label=_LABELS[family], seed=seed)
    case.pop('scarce_delay_family', None)
    details = dict(base_environment_family=_BASE_FAMILY,
        original_inquiry_timeout_minutes=3,
        bounded_late_receipt_window_minutes=.25,
        private_background_minutes=3,
        late_receipt_state_requires_raw_branch_verification=True)

    if family == 'deadline_reply_at_350':
        case['response_matrix']['*']['H02']['delay'] = .5
        details.update(intent='half_minute_late_reply_is_outside_the_quarter_minute_bound',
                       potential_initial_h02_finish_minute=3.5)
    elif family == 'deadline_reply_at_375':
        case['response_matrix']['*']['H02']['delay'] = .75
        details.update(intent='three_quarter_minute_late_reply_is_outside_the_quarter_minute_bound',
                       potential_initial_h02_finish_minute=3.75)
    elif family == 'deadline_silent_alternative':
        case['response_matrix']['*']['H02']['silent'] = True
        details.update(intent='review_completion_cannot_be_promoted_to_a_received_reply',
                       potential_initial_h02_finish_minute=3.25,
                       h02_reply_never_delivered=True)
    elif family == 'deadline_capacity_lost_before_receipt':
        # Integer-minute environmental events are supported. At minute three
        # the existing periodic feed can report the real minute-two outage,
        # before an initial H02 job could finish at minute 3.25.
        case['exogenous_events'] = [dict(type='resource_unavailable', t=2,
            until=8, hospital='H02', resource='ed_bed')]
        details.update(intent='late_reply_requires_current_received_fresh_sufficient_resources',
                       actual_outage_minute=2, actual_restore_minute=8,
                       existing_feed_can_report_shortage_at_minute=3,
                       potential_initial_h02_finish_minute=3.25)
    elif family == 'deadline_later_single_candidate':
        h02 = next(h for h in case['hospitals'] if h['id'] == 'H02')
        h02['capabilities'].append(_H02_ONLY_CAPABILITY)
        later = deepcopy(case['patients'][0])
        later.update(id='P03', label='Synthetic later H02-only patient',
            required_capabilities=[*later['required_capabilities'], _H02_ONLY_CAPABILITY],
            resource_demands=dict(ed_bed=1), priority=4)
        for field in ('occur_t', 'dispatch_t', 'ambulance_arrive_t', 'search_start_t'):
            later[field] = 3
        case['patients'].append(later)
        details.update(intent='later_single_candidate_demand_is_visible_only_after_its_actual_request',
                       later_patient='P03', later_actual_request_minute=3,
                       later_only_compatible_hospital='H02',
                       potential_initial_h02_finish_minute=3.25)
    else:
        raise AssertionError('unhandled deadline-recovery family')

    case['assumptions'] = [*case['assumptions'],
        'Nonblocking late-reply revalidation is a separate experimental communication contract.',
        'Original timeout events, FIFO work, retry behavior and accepted-offer lifetimes remain explicit.',
        'Potential finish times and future requests in experiment annotations are environment metadata, not policy inputs.',
        'Patient-condition changes and transport receipt lag are not silently added to unsupported environment fields.']
    case['experiment'] = dict(seed=seed, patient_count=len(case['patients']),
        deadline_recovery_family=family,
        comparison_scope='same_environment_bounded_nonblocking_late_reply',
        clinical_values='synthetic', identity_source='synthetic',
        routing='synthetic_fixed_coordinates', timing_unit='real_minutes',
        review_resolution_seconds=15, history_timing_unit='relative_seconds',
        additional_resource_change_channel_enabled=False, **details)
    validate_scenario(case)
    _validate_extra_inputs(case)
    return case


def make_scenario(seed, family):
    """Build a detached input, preserving every inherited 67-family value."""
    if type(seed) is not int or seed < 0:
        raise ValueError('seed must be a nonnegative integer')
    if not isinstance(family, str) or family not in FAMILIES:
        raise ValueError('unknown deadline-recovery family')
    if family in ORIGINAL_FAMILIES:
        return deepcopy(make_scarce_delay_scenario(seed, family))
    return deepcopy(_new_case(seed, family))


def build_deadline_recovery_scenarios(seed, families=None):
    """Build all 72 environments, or an ordered distinct family selection."""
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
            raise ValueError('unknown or duplicate deadline-recovery family')
    return [make_scenario(seed, family) for family in selected]
