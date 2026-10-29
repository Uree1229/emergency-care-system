"""Current-public-shortage readiness boundaries; all earlier 135 inputs intact.

441xx is exposed development. Reserved 501xx generation is forbidden until the
separate historical seed-field audit and scientific source freeze are closed.
Future outage and later patient arrivals stay in the physical environment.
"""
from copy import deepcopy

from .joint_policy_scenarios import _validate_extra_inputs
from .map_replay import validate_scenario
from .probe_guard_scenarios import FAMILIES as ORIGINAL_FAMILIES, make_scenario as make_previous

DEVELOPMENT_SEEDS = (44101, 44102, 44103)
VALIDATION_SEEDS = (50101, 50102, 50103, 50104, 50105, 50106)
ADDITIONAL_FAMILIES = (
    'public_offer_fresh_shortage',
    'public_offer_fresh_available_closer',
    'public_offer_pending_closer',
    'public_offer_future_outage',
    'public_offer_later_scarce_patient',
    'public_offer_second_required_resource',
)
FAMILIES = ORIGINAL_FAMILIES + ADDITIONAL_FAMILIES
BASE_FAMILY = 'cooperative_probe_protected_hospital_alternative'
BASE_FAMILIES = dict.fromkeys(ADDITIONAL_FAMILIES, BASE_FAMILY)


def _start(patient, t):
    for key in ('occur_t', 'dispatch_t', 'ambulance_arrive_t', 'search_start_t'):
        patient[key] = t


def _new_case(seed, family):
    case = make_previous(seed, BASE_FAMILY)
    case.update(id=f'public-offer-{family}-{seed}', family=family,
        public_offer_family=family, seed=seed, label=family)
    for key in ('probe_guard_family', 'recovery_rank_family', 'recovery_dispatch_family',
                'retry_dispatch_family', 'cooperative_probe_family', 'shortage_probe_family',
                'scarce_delay_family', 'joint_policy_family'):
        case.pop(key, None)
    hospitals = {h['id']: h for h in case['hospitals']}
    patient = case['patients'][0]
    # This is an environment change shared by every compared policy. It does
    # not give any policy additional capacity, staffing, reports or knowledge.
    hospitals['H01']['initial_occupancy'] = [dict(until=12, demand=dict(ed_bed=2))]
    case['exogenous_events'] = []
    detail = dict(base_environment_family=BASE_FAMILY,
        original_physical_work_capacity_and_reporting_contract_shared=True,
        intended_offer='H03', intended_slower_pending='H02', intended_closer='H01',
        intended_source_of_closer_shortage='initial occupancy and original received feeds',
        intended_first_extra_readiness_minute=1,
        reachability_requires_full_raw_witness=True,
        future_patient_outage_and_service_times_not_policy_inputs=True,
        no_added_workers_beds_or_resource_publications=True)
    if family == 'public_offer_fresh_available_closer':
        hospitals['H01']['initial_occupancy'] = [dict(until=1, demand=dict(ed_bed=1))]
        detail.update(intended_block='current fresh sufficient closer opportunity',
            intended_closer_is_not_lifetime_uncontacted=True)
    elif family == 'public_offer_pending_closer':
        hospitals['H02']['lat'] = 35.18
        hospitals['H03']['lat'] = 35.195
        detail.update(intended_block='a real pending inquiry is closer than the accepted offer',
            original_travel_geometry_changed_for_all_branches=True,
            intended_first_extra_readiness_minute=None)
    elif family == 'public_offer_future_outage':
        case['exogenous_events'] = [dict(type='resource_unavailable', t=4, until=6,
            hospital='H03', resource='ed_bed')]
        detail.update(observed_prefix_twin='public_offer_fresh_shortage',
            identical_received_history_through_intended_review_minute=1,
            private_future_outage_minute=4, private_future_restore_minute=6,
            intent='early departure must retain real backup work and expose future outage risk')
    elif family == 'public_offer_later_scarce_patient':
        late = deepcopy(patient)
        late.update(id='P03', label='Synthetic later H03-only patient', priority=4,
            required_capabilities=['Synthetic public-offer H03-only'])
        _start(late, 2)
        hospitals['H03']['capabilities'].append('Synthetic public-offer H03-only')
        case['patients'].append(late)
        case['response_matrix']['P03'] = {hid: dict(delay=.5, silent=False) for hid in hospitals}
        detail.update(observed_prefix_twin='public_offer_fresh_shortage',
            identical_registered_patients_through_intended_review_minute=1,
            future_request_minute=2,
            intent='a not-yet-registered scarce patient cannot be used to select an early action')
    elif family == 'public_offer_second_required_resource':
        patient['resource_demands']['special_team'] = 1
        for hospital in hospitals.values():
            hospital['capacity']['special_team'] = 3
        hospitals['H01']['initial_occupancy'] = [dict(until=12, demand=dict(special_team=3))]
        detail.update(intended_source_of_closer_shortage='explicitly required special_team',
            bed_alone_is_not_the_treatment_condition=True,
            required_resources=['ed_bed', 'special_team'])
    elif family != 'public_offer_fresh_shortage':
        raise AssertionError('unhandled public offer boundary')
    case['assumptions'] += [
        'Every branch uses the same physical world, delivered feeds, capacities, workers and original hospital work contract.',
        'Only already received resource views, actual inquiry handles, live patient offers and currently registered patients may enter the extra readiness input.',
        'A current fresh shortage is evidence of present infeasibility, not a prediction of future recovery or a guarantee against future interruption.',
        'Actual pending work and unexpired backup offers retain their original deadlines, service and resource checks after any new early commitment.',
        'The boundary labels and intended review times are declarations, not evidence of realized policy activation or clinical benefit.',
    ]
    case['experiment'] = dict(seed=seed, patient_count=len(case['patients']),
        public_offer_family=family, clinical_values='synthetic', identity_source='synthetic',
        routing='synthetic_fixed_coordinates', timing_unit='real_minutes', review_resolution_seconds=15,
        additional_resource_change_channel_enabled=False, comparison_scope='one_public_readiness_stage_only', **detail)
    validate_scenario(case)
    _validate_extra_inputs(case)
    return case


def make_scenario(seed, family):
    if type(seed) is not int or seed < 0:
        raise ValueError('nonnegative integer seed required')
    if not isinstance(family, str) or family not in FAMILIES:
        raise ValueError('unknown public offer family')
    return deepcopy(make_previous(seed, family) if family in ORIGINAL_FAMILIES else _new_case(seed, family))


def build_public_offer_scenarios(seed, families=None):
    if type(seed) is not int or seed < 0:
        raise ValueError('nonnegative integer seed required')
    if isinstance(families, (str, bytes)):
        raise ValueError('distinct iterable of known families required')
    try:
        chosen = list(FAMILIES if families is None else families)
    except TypeError as exc:
        raise ValueError('iterable of known families required') from exc
    if any(not isinstance(f, str) or f not in FAMILIES for f in chosen) or len(chosen) != len(set(chosen)):
        raise ValueError('unknown or duplicate public offer family')
    return [make_scenario(seed, family) for family in chosen]
