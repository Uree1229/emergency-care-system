"""Observed recovery ranking boundaries, preserving all prior 121 inputs.

441xx is exposed development data;471xx is newly reserved validation. No
variant changes the physical supply, clinical priority, route or worker pool.
Small-seed raw witnesses, not family labels, establish realized coverage.
"""
from copy import deepcopy

from .joint_policy_scenarios import _validate_extra_inputs
from .map_replay import validate_scenario
from .partial_information_scenarios import SCARCE
from .recovery_dispatch_scenarios import FAMILIES as ORIGINAL_FAMILIES, make_scenario as make_previous

DEV_SEEDS = DEVELOPMENT_SEEDS = (44101, 44102, 44103)
VALIDATION_SEEDS = (47101, 47102, 47103, 47104, 47105, 47106)
ADDITIONAL_FAMILIES = (
    'recovery_rank_repeated_recovery_after_decline',
    'recovery_rank_same_shortage_refresh',
    'recovery_rank_partial_required_resource',
    'recovery_rank_predecline_source_late',
    'recovery_rank_busy_then_clinical_decline',
    'recovery_rank_unvisited_recovery_alternative',
    'recovery_rank_later_patient_fifo',
    'recovery_rank_unvisited_current_alternative',
)
FAMILIES = ORIGINAL_FAMILIES + ADDITIONAL_FAMILIES
BASE_FAMILIES = {name: 'recovery_dispatch_recovery_then_decline' for name in ADDITIONAL_FAMILIES}
BASE_FAMILIES.update(
    recovery_rank_partial_required_resource='recovery_dispatch_partial_unknown',
    recovery_rank_predecline_source_late='recovery_dispatch_predecline_optimism_late',
    recovery_rank_busy_then_clinical_decline='recovery_dispatch_busy_nonadmission')
_LABELS = dict(zip(ADDITIONAL_FAMILIES, (
    'Actual rank work, new clinical rejection, another real recovery and duplicate free feeds',
    'Repeated fresh numeric shortage after a real renewed clinical decline',
    'Fresh recovered beds with a second explicitly required resource still unknown',
    'An optimistic pre-decline source packet arrives five minutes later',
    'Busy control and real normal work are distinct from an actual clinical decline',
    'An original recovered alternative is contacted before later normal rank eligibility',
    'An actually later single-hospital patient shares real rank work in FIFO',
    'A current unvisited normally eligible original alternative blocks recovery ranking',
)))


def _outage(t, until, quantity=3):
    # The existing event removes exactly one original physical bed.
    return [dict(type='resource_unavailable', t=t, until=until,
                 hospital='H01', resource='ed_bed') for _ in range(quantity)]


def _new_case(seed, family):
    parent = BASE_FAMILIES[family]
    case = make_previous(seed, parent)
    case.update(id=f'recovery-rank-{family}-{seed}', family=family, recovery_rank_family=family,
                label=_LABELS[family], seed=seed)
    for key in ('recovery_dispatch_family', 'retry_dispatch_family', 'cooperative_probe_family',
                'shortage_probe_family', 'scarce_delay_family', 'joint_policy_family'):
        case.pop(key, None)
    detail = dict(base_environment_family=parent, raw_branch_reachability_must_be_verified=True,
        original_concurrent_handle_limit=3, original_episode_contact_limit=10,
        condition_change_runtime_event_supported=False,
        initial_reopen_and_normal_rank_consumptions_are_separate=True,
        fresh_validation_input_generation_before_source_freeze=False)
    matrix = case['response_matrix']
    if family == 'recovery_rank_repeated_recovery_after_decline':
        case['exogenous_events'] += _outage(9, 16)
        matrix['*']['H01'] = dict(attempts=[dict(delay=.5, silent=False),
            dict(delay=2.5, silent=False), dict(delay=2.5, silent=False), dict(delay=.5, silent=True)])
        detail.update(intent='Keep initial SEARCH alive through actual rank work and a new physical loss; repeated recovery cannot renew a consumed rank permission.',
            later_physical_loss_minute=9, later_restore_minute=16,
            third_actual_service_minutes=2.5, fourth_and_later_actual_review_silent=True)
    elif family == 'recovery_rank_same_shortage_refresh':
        case['exogenous_events'] = _outage(5, 120)
        detail.update(intent='A fresh timestamp on unchanged numeric shortage is not a post-decline recovery.',
            persistent_outage_until=120, real_feed_interval_minutes=1)
    elif family == 'recovery_rank_partial_required_resource':
        case['observation_config']['feed_delay_minutes'] = 2
        detail.update(intent='Record active normal rank decisions with a fresh bed and an unknown required team; disclose any earlier blocking gate.',
            explicitly_required_resources=['ed_bed', 'special_team'],
            missing_required_resource='special_team', actual_feed_transport_minutes=2)
    elif family == 'recovery_rank_predecline_source_late':
        case['observation_config']['feed_delay_by_source_minute'] = {'0': 5}
        detail.update(intent='Receipt after a real clinical decline does not make a pre-decline optimistic source current.',
            optimistic_source_minute=0, optimistic_delivery_minute=5,
            actual_outage_minute=1, actual_bed_slots_unavailable=3)
    elif family == 'recovery_rank_busy_then_clinical_decline':
        case['hospitals'][0]['initial_occupancy'][0]['until'] = 4
        matrix['*']['H02'] = dict(delay=2.5, silent=True)
        matrix['*']['H01'] = dict(delay=.75, silent=False)
        case['exogenous_events'] = _outage(5, 8, 1)
        detail.update(intent='An actual busy nonadmission precedes normal clinical work; only its later actual decline can authorize recovery ranking.',
            first_physical_release_minute=4, actual_background_work_minutes=5,
            later_physical_loss_minute=5, later_restore_minute=8,
            target_original_bed_capacity=1, target_actual_service_minutes=.75)
    elif family in ('recovery_rank_unvisited_recovery_alternative', 'recovery_rank_unvisited_current_alternative'):
        hospital = next(h for h in case['hospitals'] if h['id'] == 'H04')
        hospital['initial_occupancy'] = [dict(until=8, demand=dict(ed_bed=hospital['capacity']['ed_bed']))]
        detail.update(intent='Expose the original fourth hospital as a real unvisited alternative; distinguish its first normal contact from later rank eligibility.',
            originally_existing_unvisited_hospital='H04', alternative_initial_occupancy_until=8,
            added_hospitals=0, added_workers=0, original_capacities_unchanged=True)
        if family == 'recovery_rank_unvisited_current_alternative':
            # Keep other normal work genuinely unavailable so its pending
            # timeout cannot conceal the actual unvisited-alternative gate.
            for hid in ('H02', 'H03'):
                case['exogenous_events'] += [dict(type='resource_unavailable', t=1, until=9,
                    hospital=hid, resource='ed_bed') for _ in range(3)]
            matrix['*']['H02'] = dict(delay=2.5, silent=False)
            detail.update(intent='At the actual normal review, H04 is unvisited and eligible while no own inquiry is pending; record the actual blocking reason.',
                other_original_hospital_actual_outage_minutes=[1, 9])
    elif family == 'recovery_rank_later_patient_fifo':
        hospital = case['hospitals'][0]
        if SCARCE not in hospital['capabilities']:
            hospital['capabilities'].append(SCARCE)
        matrix['*']['H01']['attempts'][2:] = [dict(delay=2.75, silent=False)]
        patient = deepcopy(case['patients'][0])
        patient.update(id='P02', label='Synthetic later single-hospital patient',
            required_capabilities=[SCARCE], resource_demands=dict(ed_bed=1, special_team=1))
        for key in ('occur_t', 'dispatch_t', 'ambulance_arrive_t', 'search_start_t'):
            patient[key] = 9
        case['patients'].append(patient)
        case['response_matrix'] = {'P01': deepcopy(matrix['*']), 'P02': {
            h['id']: dict(delay=.25, silent=False) for h in case['hospitals']}}
        detail.update(intent='Real rank review work remains queued and paid when a later request arrives; no earlier policy sees that future request.',
            later_patient='P02', actual_later_registration_minute=9,
            later_only_compatible_hospital='H01', later_actual_service_minutes=.25,
            target_actual_third_service_minutes=2.75)
    else:
        raise AssertionError('unhandled recovery rank family')
    case['assumptions'] += [
        'Normal and latched rank variants receive identical physical events, potential review costs, reports and clinical priorities.',
        'Ranking changes only an existing original normal selector tier; actual contacts, episode counts and hospital work are not discounted.',
        'Report receipt, active initial SEARCH decision exposure, rank tier selection, actual ask and actual token consumption are different coverage checkpoints.',
        'An earlier blocking gate is disclosed; a family label alone does not prove that a later semantic recovery guard was evaluated.',
        'No additional resource-change push or dynamic patient-condition event is introduced.',
        'Future arrivals, future work completion and future physical outages are private environment data, not earlier policy inputs.']
    case['experiment'] = dict(seed=seed, patient_count=len(case['patients']), recovery_rank_family=family,
        comparison_scope='same_environment_normal_frontier_rank_or_original_latched_plus_rank',
        clinical_values='synthetic', identity_source='synthetic', routing='synthetic_fixed_coordinates',
        timing_unit='real_minutes', review_resolution_seconds=15, history_timing_unit='relative_seconds',
        additional_resource_change_channel_enabled=False, **detail)
    validate_scenario(case)
    _validate_extra_inputs(case)
    return case


def make_scenario(seed, family):
    if type(seed) is not int or seed < 0:
        raise ValueError('seed must be a nonnegative integer')
    if not isinstance(family, str) or family not in FAMILIES:
        raise ValueError('unknown recovery rank family')
    return deepcopy(make_previous(seed, family) if family in ORIGINAL_FAMILIES else _new_case(seed, family))


def build_recovery_rank_scenarios(seed, families=None):
    if type(seed) is not int or seed < 0:
        raise ValueError('seed must be a nonnegative integer')
    if families is None:
        selected = list(FAMILIES)
    else:
        if isinstance(families, (str, bytes)):
            raise ValueError('families must be distinct known names')
        try:
            selected = list(families)
        except TypeError as exc:
            raise ValueError('families must be iterable') from exc
        if any(not isinstance(f, str) or f not in FAMILIES for f in selected) or len(selected) != len(set(selected)):
            raise ValueError('unknown or duplicate recovery rank family')
    return [make_scenario(seed, f) for f in selected]
