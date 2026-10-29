"""Fresh environments for late replies that must not interrupt pending work.

The preceding 72 deadline-recovery environments remain complete copies. One
additional environment reproduces the small-seed multiple-pending boundary
with fixed route geometry and response work; patient treatment durations keep
the inherited seed-dependent random stream. No reserved seed is run here.
"""
from __future__ import annotations

from copy import deepcopy

from .deadline_recovery_scenarios import (
    FAMILIES as ORIGINAL_FAMILIES,
    make_scenario as make_deadline_recovery_scenario,
)
from .joint_policy_scenarios import _validate_extra_inputs
from .map_replay import validate_scenario


DEV_SEEDS = (37101, 37102, 37103)
DEVELOPMENT_SEEDS = DEV_SEEDS
VALIDATION_SEEDS = (38101, 38102, 38103, 38104, 38105, 38106)
ADDITIONAL_FAMILIES = ('deadline_multiple_pending_boundary',)
FAMILIES = ORIGINAL_FAMILIES + ADDITIONAL_FAMILIES
_BASE_FAMILY = 'scarce_alternative_late_deadline'


def _new_case(seed, family):
    case = make_deadline_recovery_scenario(seed, _BASE_FAMILY)
    case.update(id=f'deadline-safe-{family}-{seed}', family=family,
        deadline_safe_family=family,
        label='Late supplemental reply while two original inquiries remain pending', seed=seed)
    case.pop('scarce_delay_family', None)
    # Fixed coordinates exactly reproduce the seed-seven diagnostic geometry.
    # They do not vary with a validation seed or encode a predicted outcome.
    case['hospitals'][0].update(lat=35.18, lon=126.85)
    case['hospitals'][1].update(lat=35.189481, lon=126.85)
    template = case['hospitals'][1]
    for hid, lat in (('H03', 35.184), ('H04', 35.175)):
        hospital = deepcopy(template)
        hospital.update(id=hid, name=f'Synthetic pending-work hospital {hid}', lat=lat,
            initial_occupancy=[dict(until=3, demand=dict(ed_bed=1))])
        case['hospitals'].append(hospital)
    case['response_matrix']['*'].update(
        H03=dict(delay=.25, silent=False), H04=dict(delay=2.5, silent=False))
    case['accept_ttl_minutes'] = 3
    case['assumptions'] = [*case['assumptions'],
        'The original pending inquiries retain their actual submit times, response work and deadlines.',
        'An actually received late reply is nonblocking and may not interrupt any currently pending inquiry.',
        'Fixed additional geometry and work duration reproduce an observed structural counterexample.',
        'Initial occupancy release and potential reply work remain private environmental inputs.']
    case['experiment'] = dict(seed=seed, patient_count=len(case['patients']),
        deadline_safe_family=family, base_environment_family=_BASE_FAMILY,
        comparison_scope='same_environment_noninterrupting_bounded_late_reply',
        clinical_values='synthetic', identity_source='synthetic',
        routing='synthetic_fixed_coordinates', timing_unit='real_minutes',
        review_resolution_seconds=15, history_timing_unit='relative_seconds',
        additional_resource_change_channel_enabled=False,
        original_inquiry_timeout_minutes=3, original_accept_ttl_minutes=3,
        bounded_late_receipt_window_minutes=.25,
        private_background_minutes=3,
        initial_h03_h04_occupancy_until=3,
        fixed_route_geometry=True,
        treatment_duration_stream='inherited scarce_alternative_late_deadline stream',
        intent='any_actual_pending_inquiry_blocks_late_reply_recovery_without_waiting',
        late_receipt_state_requires_raw_branch_verification=True)
    validate_scenario(case)
    _validate_extra_inputs(case)
    return case


def make_scenario(seed, family):
    """Return detached inputs without altering any preceding 72-family value."""
    if type(seed) is not int or seed < 0:
        raise ValueError('seed must be a nonnegative integer')
    if not isinstance(family, str) or family not in FAMILIES:
        raise ValueError('unknown deadline-safe family')
    if family in ORIGINAL_FAMILIES:
        return deepcopy(make_deadline_recovery_scenario(seed, family))
    return deepcopy(_new_case(seed, family))


def build_deadline_safe_scenarios(seed, families=None):
    """Build all 73 environments, or a distinct ordered family selection."""
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
            raise ValueError('unknown or duplicate deadline-safe family')
    return [make_scenario(seed, family) for family in selected]
