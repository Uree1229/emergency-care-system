"""Reserve boundaries with unchanged inherited 141 environments.

Future patients and interruptions are environment events, never policy inputs.
Reserved inputs are generated only by the separately frozen experiment runner.
"""
from copy import deepcopy

from .joint_policy_scenarios import _validate_extra_inputs
from .map_replay import validate_scenario
from .public_offer_scenarios import FAMILIES as ORIGINAL_FAMILIES, make_scenario as make_previous


DEVELOPMENT_SEEDS = (44101, 44102, 44103)
VALIDATION_SEEDS = (52101, 52102, 52103, 52104, 52105, 52106)
ADDITIONAL_FAMILIES = (
    'offer_reserve_ample_normal',
    'offer_reserve_below_boundary',
    'offer_reserve_same_demand_future',
    'offer_reserve_greater_demand_future',
    'offer_reserve_repeated_future',
    'offer_reserve_simultaneous_current',
    'offer_reserve_second_resource_failure',
    'offer_reserve_ample_future_outage',
)
FAMILIES = ORIGINAL_FAMILIES + ADDITIONAL_FAMILIES
BASE_FAMILIES = {family: ('public_offer_second_required_resource'
    if family == 'offer_reserve_second_resource_failure' else 'public_offer_fresh_shortage')
    for family in ADDITIONAL_FAMILIES}
SCARCE = 'Synthetic offer-reserve H03-only'


def _start(patient, t):
    for key in ('occur_t', 'dispatch_t', 'ambulance_arrive_t', 'search_start_t'):
        patient[key] = t


def _new_case(seed, family):
    source = BASE_FAMILIES[family]
    case = make_previous(seed, source)
    case.update(id=f'offer-reserve-{family}-{seed}', family=family, seed=seed,
        label=family, offer_reserve_family=family)
    case.pop('public_offer_family', None)
    hospitals = {row['id']: row for row in case['hospitals']}
    h03 = hospitals['H03']
    h03['capacity']['ed_bed'] = 4
    h03['initial_occupancy'] = [dict(until=1, demand=dict(ed_bed=4))]
    # Common capability is present even in twins without a future patient, so
    # the entire hospital rows are identical before the private future event.
    h03['capabilities'].append(SCARCE)
    detail = dict(base_environment_family=source, current_patient_bed_demand=2,
        offer_hospital='H03', offer_physical_bed_capacity=4,
        boundary='one fixed own-demand copy beyond registered compatible SEARCH demand',
        future_patients_outages_and_work_completion_not_policy_inputs=True,
        original_work_workers_publication_and_resource_models_shared_by_all_branches=True)

    def later(pid, t, demand):
        row = deepcopy(case['patients'][0])
        row.update(id=pid, label=f'Synthetic reserve-only patient {pid}',
            priority=4, required_capabilities=[SCARCE], resource_demands=dict(ed_bed=demand))
        _start(row, t)
        case['patients'].append(row)
        case['response_matrix'][pid] = {hid: dict(delay=.5, silent=False) for hid in hospitals}

    if family == 'offer_reserve_below_boundary':
        h03['capacity']['ed_bed'] = 3
        h03['initial_occupancy'][0]['demand']['ed_bed'] = 3
        detail.update(offer_physical_bed_capacity=3, intended_block='one below required four beds')
    elif family == 'offer_reserve_same_demand_future':
        later('P03', 2, 2)
        detail.update(observed_prefix_twin='offer_reserve_ample_normal', future_requests=[dict(t=2,demand=2)])
    elif family == 'offer_reserve_greater_demand_future':
        later('P03', 2, 3)
        detail.update(observed_prefix_twin='offer_reserve_ample_normal', future_requests=[dict(t=2,demand=3)],
            intent='an own-demand reserve cannot guarantee a larger unregistered demand')
    elif family == 'offer_reserve_repeated_future':
        later('P03', 2, 2);later('P04', 3, 2)
        detail.update(observed_prefix_twin='offer_reserve_ample_normal',
            future_requests=[dict(t=2,demand=2),dict(t=3,demand=2)],
            intent='one demand copy cannot guarantee an arbitrary sequence of future arrivals')
    elif family == 'offer_reserve_simultaneous_current':
        later('P03', 0, 2)
        detail.update(current_registered_h03_only_patient='P03',
            intended_block='registered compatible cohort four plus own two exceeds four')
    elif family == 'offer_reserve_second_resource_failure':
        h03['capacity']['special_team'] = 1
        detail.update(required_resources=['ed_bed','special_team'],
            intended_block='beds satisfy reserve but required special_team has no spare copy')
    elif family == 'offer_reserve_ample_future_outage':
        case['exogenous_events'] = [dict(type='resource_unavailable',t=4,until=6,
            hospital='H03',resource='ed_bed')]
        detail.update(observed_prefix_twin='offer_reserve_ample_normal',
            private_future_outage_minute=4, private_future_restore_minute=6,
            intent='current spare quantity does not predict a future interruption')
    elif family != 'offer_reserve_ample_normal':
        raise AssertionError('unhandled reserve boundary')
    case['assumptions'] += [
        'One fixed current-cohort plus own-demand reserve is tested; no future patient or release-time prediction is supplied.',
        'A reserve is a readiness filter only: ordinary inquiry, real queue work, backups, original protection and physical checks remain.',
        'The reserve is not a guarantee for larger future demand, repeated future arrivals, future interruption or unobserved physical changes.',
        'Every compared branch sees the identical environment capacity, actual workers and delivered resource reports.',
    ]
    case['experiment'] = dict(seed=seed, patient_count=len(case['patients']),
        offer_reserve_family=family, clinical_values='synthetic', identity_source='synthetic',
        routing='synthetic_fixed_coordinates',timing_unit='real_minutes',review_resolution_seconds=15,
        additional_resource_change_channel_enabled=False,
        comparison_scope='one fixed current-cohort reserve filter only',**detail)
    validate_scenario(case);_validate_extra_inputs(case)
    return case


def make_scenario(seed, family):
    if type(seed) is not int or seed < 0:
        raise ValueError('nonnegative integer seed required')
    if type(family) is not str or family not in FAMILIES:
        raise ValueError('known reserve family required')
    return deepcopy(make_previous(seed, family) if family in ORIGINAL_FAMILIES else _new_case(seed, family))


def build_offer_reserve_scenarios(seed, families=None):
    if type(seed) is not int or seed < 0:
        raise ValueError('nonnegative integer seed required')
    if isinstance(families, (str,bytes)):
        raise ValueError('distinct iterable of known families required')
    try:
        chosen = list(FAMILIES if families is None else families)
    except TypeError as exc:
        raise ValueError('iterable of known families required') from exc
    if any(type(f) is not str or f not in FAMILIES for f in chosen) or len(chosen) != len(set(chosen)):
        raise ValueError('unknown or duplicate reserve family')
    return [make_scenario(seed,family) for family in chosen]
