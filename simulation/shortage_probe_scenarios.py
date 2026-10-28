"""Same-information boundaries for a bounded probe of a reported full bed.

The preceding 73 environments remain complete detached copies. Ten additional
environments vary only supported physical occupancy, actual requests, hospital
FIFO work, response delivery, resource reporting or ordinary offer lifetime.
Reserved development/validation inputs are not constructed by structural tests.
"""
from __future__ import annotations

from copy import deepcopy

from .deadline_safe_scenarios import (
    FAMILIES as ORIGINAL_FAMILIES,
    make_scenario as make_deadline_safe_scenario,
)
from .joint_policy_scenarios import _validate_extra_inputs
from .map_replay import validate_scenario
from .partial_information_scenarios import BASIC, SCARCE


DEV_SEEDS = (39101, 39102, 39103)
DEVELOPMENT_SEEDS = DEV_SEEDS
VALIDATION_SEEDS = (40101, 40102, 40103, 40104, 40105, 40106)
ADDITIONAL_FAMILIES = (
    'shortage_probe_release_before_reply',
    'shortage_probe_still_full',
    'shortage_probe_silent',
    'shortage_probe_later_patient_fifo',
    'shortage_probe_late_reply',
    'shortage_probe_short_ttl',
    'shortage_probe_other_resource_shortage',
    'shortage_probe_missing_other_resource',
    'shortage_probe_stale_report',
    'shortage_probe_other_patient_pending',
)
FAMILIES = ORIGINAL_FAMILIES + ADDITIONAL_FAMILIES
_BASE_FAMILY = 'scarce_options_staggered_replies'
_LABELS = {
    'shortage_probe_release_before_reply': 'A reported full nearby bed becomes physically free before an actual reply',
    'shortage_probe_still_full': 'A nearby hospital remains physically full when the probe is reviewed',
    'shortage_probe_silent': 'A full-bed probe receives no reply while the original inquiry receives an offer',
    'shortage_probe_later_patient_fifo': 'Probe work competes with an actually later registered single-hospital patient',
    'shortage_probe_late_reply': 'Private FIFO work places the probe reply after its original deadline',
    'shortage_probe_short_ttl': 'Probe and original acceptances retain a one-minute offer lifetime',
    'shortage_probe_other_resource_shortage': 'A required non-bed resource is also currently reported insufficient',
    'shortage_probe_missing_other_resource': 'A required non-bed count is unknown in the current report',
    'shortage_probe_stale_report': 'The nearby full-bed report is eleven minutes old',
    'shortage_probe_other_patient_pending': 'Another registered patient already has a pending inquiry at the nearby hospital',
}


def _report(hospital, *, observed_at=0, bed_full=False, missing_other=False):
    return dict(hospital=hospital['id'], observed_at=observed_at,
        resources={resource: None if resource == 'special_team' and missing_other else dict(
            capacity=capacity, used=capacity if resource == 'ed_bed' and bed_full else 0)
            for resource, capacity in hospital['capacity'].items()})


def _new_case(seed, family):
    case = make_deadline_safe_scenario(seed, _BASE_FAMILY)
    case.update(id=f'shortage-probe-{family}-{seed}', family=family,
        shortage_probe_family=family, label=_LABELS[family], seed=seed)
    case.pop('joint_policy_family', None)
    case['patients'] = case['patients'][:1]
    case['hospitals'] = case['hospitals'][:2]
    h01, h02 = case['hospitals']
    h01.update(lat=35.18, lon=126.85, capabilities=[BASIC],
        capacity=dict(ed_bed=1, special_team=1),
        initial_occupancy=[dict(until=1, demand=dict(ed_bed=1))])
    h02.update(lat=35.189481, lon=126.85, capabilities=[BASIC],
        capacity=dict(ed_bed=1, special_team=1))
    p01 = case['patients'][0]
    p01.update(required_capabilities=[BASIC], resource_demands=dict(ed_bed=1), priority=4)
    case.update(response_environment={},
        response_matrix={'*': {'H01': dict(delay=.75, silent=False),
                                'H02': dict(delay=2.5, silent=False)}},
        inquiry_queue=dict(enabled=True, workers=1, background_jobs=[]),
        initial_inquiry_history=[], exogenous_events=[],
        observation_config=dict(feed_interval_minutes=1, feed_delay_minutes=0,
            feed_missing_resources=[], confirmation_delay_minutes=1,
            confirmation_silent=False, confirmation_missing_resources=[],
            feed_delay_by_source_minute={}),
        resource_update_config=dict(delay_minutes=0, missing_resources=[], silent=False),
        timeout_minutes=3, retry_minutes=3, accept_ttl_minutes=10,
        transfer_preparation_minutes=3, max_minutes=60, travel_error_range=[1.0, 1.0])
    details = dict(base_environment_family=_BASE_FAMILY,
        nearby_hospital='H01', original_pending_hospital='H02',
        physical_initial_bed_occupancy_until=1,
        original_inquiry_timeout_minutes=3, original_accept_ttl_minutes=10,
        actual_original_reply_work_minutes=2.5,
        actual_nearby_reply_work_minutes=.75,
        raw_branch_reachability_must_be_verified=True)

    if family == 'shortage_probe_release_before_reply':
        details['intent'] = 'probe_may_receive_an_actual_offer_only_after_physical_release_and_current_sufficient_report'
    elif family == 'shortage_probe_still_full':
        h01['initial_occupancy'][0]['until'] = 8
        details.update(intent='a_reported_full_bed_probe_cannot_create_physical_capacity',
            physical_initial_bed_occupancy_until=8)
    elif family == 'shortage_probe_silent':
        case['response_matrix']['*']['H01']['silent'] = True
        details.update(intent='silent_additional_work_must_not_be_promoted_to_acceptance_or_hidden_from_waiting_cost',
            nearby_reply_never_delivered=True)
    elif family == 'shortage_probe_later_patient_fifo':
        h01['capabilities'].append(SCARCE)
        later = deepcopy(p01)
        h02['capacity']['special_team'] = 0
        later.update(id='P02', label='Synthetic later single-hospital bed-and-team demand',
            required_capabilities=[SCARCE], resource_demands=dict(ed_bed=1, special_team=1))
        for field in ('occur_t', 'dispatch_t', 'ambulance_arrive_t', 'search_start_t'):
            later[field] = 1
        case['patients'].append(later)
        case['response_matrix'] = {
            'P01': {'H01': dict(delay=2.5, silent=False), 'H02': dict(delay=2.5, silent=False)},
            'P02': {'H01': dict(delay=.25, silent=False), 'H02': dict(delay=.25, silent=False)}}
        details.update(intent='already_submitted_probe_work_retains_its_actual_fifo_cost_for_future_registered_demand',
            actual_nearby_reply_work_minutes=2.5, later_patient='P02',
            later_actual_request_minute=1, later_only_compatible_hospital='H01',
            later_required_resources=['ed_bed', 'special_team'])
    elif family == 'shortage_probe_late_reply':
        case['inquiry_queue']['background_jobs'] = [dict(t=0, hospital='H01', service_minutes=3)]
        details.update(intent='probe_submission_has_its_own_original_timeout_despite_hidden_background_work',
            private_nearby_background_work_minutes=3)
    elif family == 'shortage_probe_short_ttl':
        case['accept_ttl_minutes'] = 1
        details.update(intent='a_probe_does_not_refresh_its_received_acceptance_or_expired_offer',
            original_accept_ttl_minutes=1)
    elif family == 'shortage_probe_other_resource_shortage':
        p01['resource_demands']['special_team'] = 1
        h01['initial_occupancy'][0]['demand']['special_team'] = 1
        details.update(intent='a_bed_probe_requires_other_required_resources_to_be_currently_sufficient',
            additionally_occupied_resource='special_team')
    elif family == 'shortage_probe_missing_other_resource':
        p01['resource_demands']['special_team'] = 1
        case['observation_config'].update(feed_interval_minutes=0, confirmation_silent=True,
            initial_reports=[_report(h01, bed_full=True, missing_other=True), _report(h02)])
        details.update(intent='an_unknown_other_required_resource_cannot_be_filled_from_private_truth',
            unknown_hospital='H01', unknown_resource='special_team')
    elif family == 'shortage_probe_stale_report':
        case['observation_config'].update(feed_interval_minutes=0, confirmation_silent=True,
            initial_reports=[_report(h01, observed_at=-11, bed_full=True), _report(h02)])
        details.update(intent='a_stale_bed_shortage_report_is_not_current_probe_evidence',
            stale_hospital='H01', stale_source_minute=-11)
    elif family == 'shortage_probe_other_patient_pending':
        h01['capabilities'].append(SCARCE)
        h01['capacity']['ed_bed'] = 2
        h02['capacity'].update(ed_bed=2, special_team=0)
        p01['resource_demands']['ed_bed'] = 2
        other = deepcopy(p01)
        other.update(id='P02', label='Synthetic existing single-hospital bed-and-team demand',
            required_capabilities=[SCARCE], resource_demands=dict(ed_bed=1, special_team=1))
        case['patients'].append(other)
        case['response_matrix'] = {
            'P01': {'H01': dict(delay=.75, silent=False), 'H02': dict(delay=2.5, silent=False)},
            'P02': {'H01': dict(delay=2, silent=False), 'H02': dict(delay=2, silent=False)}}
        details.update(intent='another_registered_search_patient_pending_at_this_hospital_blocks_the_optional_probe',
            other_patient='P02', other_actual_request_minute=0,
            other_only_compatible_hospital='H01',
            other_required_resources=['ed_bed', 'special_team'],
            first_patient_bed_demand=2, other_patient_bed_demand=1,
            nearby_structural_bed_capacity=2)
    else:
        raise AssertionError('unhandled shortage-probe family')

    case['assumptions'] = [
        'Hospitals, patients, resources, response work and treatment durations are synthetic experimental inputs.',
        'The policy sees only already registered requests, actual inquiry handles and received resource reports.',
        'Future occupancy release, future patients, reply work, silence and FIFO completion are private environment inputs.',
        'An optional probe is real hospital work with its original deadline, FIFO competition and received offer lifetime.',
        'A patient acceptance never refreshes resource counts; allocation still requires current sufficient reports and the physical gate.',
        'Only the inherited periodic feed and confirmation mechanism publish resources; no additional change channel is added.',
        'Travel and treatment durations remain real minutes; only review timing uses the existing fifteen-second grid.',
        'The silent and later-patient environments intentionally expose extra waiting or FIFO harm if the optional probe creates it.',
    ]
    case['experiment'] = dict(seed=seed, patient_count=len(case['patients']),
        shortage_probe_family=family, comparison_scope='same_information_bounded_full_bed_probe',
        clinical_values='synthetic', identity_source='synthetic',
        routing='synthetic_fixed_coordinates', timing_unit='real_minutes',
        review_resolution_seconds=15, history_timing_unit='relative_seconds',
        additional_resource_change_channel_enabled=False, **details)
    validate_scenario(case)
    _validate_extra_inputs(case)
    return case


def make_scenario(seed, family):
    """Return detached inputs without changing any preceding 73-family value."""
    if type(seed) is not int or seed < 0:
        raise ValueError('seed must be a nonnegative integer')
    if not isinstance(family, str) or family not in FAMILIES:
        raise ValueError('unknown shortage-probe family')
    if family in ORIGINAL_FAMILIES:
        return deepcopy(make_deadline_safe_scenario(seed, family))
    return deepcopy(_new_case(seed, family))


def build_shortage_probe_scenarios(seed, families=None):
    """Build all 83 inputs, or a distinct ordered selection of families."""
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
            raise ValueError('unknown or duplicate shortage-probe family')
    return [make_scenario(seed, family) for family in selected]
