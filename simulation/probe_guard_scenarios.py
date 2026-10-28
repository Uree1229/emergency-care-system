"""Observed optional-probe boundaries appended to the unchanged 129 inputs.

Development441xx is exposed data. Validation481xx is a new reservation whose
stored seed-field audit must precede generation. The two physical twins share
every received report before their intended first probe review; later physical
release is private and is never supplied to the guard.
"""
from copy import deepcopy

from .joint_policy_scenarios import _validate_extra_inputs
from .map_replay import validate_scenario
from .recovery_rank_scenarios import FAMILIES as ORIGINAL_FAMILIES, make_scenario as make_previous

DEV_SEEDS = DEVELOPMENT_SEEDS = (44101, 44102, 44103)
VALIDATION_SEEDS = (48101, 48102, 48103, 48104, 48105, 48106)
ADDITIONAL_FAMILIES = (
    'probe_guard_renewed_fresh_negative',
    'probe_guard_same_observed_timestamp',
    'probe_guard_predated_late',
    'probe_guard_out_of_order',
    'probe_guard_hidden_recovery_adverse_twin',
    'probe_guard_required_second_resource',
)
FAMILIES = ORIGINAL_FAMILIES + ADDITIONAL_FAMILIES
BASE_FAMILY = 'recovery_dispatch_recovery_then_decline'
BASE_FAMILIES = {family: BASE_FAMILY for family in ADDITIONAL_FAMILIES}
_LABELS = dict(zip(ADDITIONAL_FAMILIES, (
    'A new observed shortage follows the actual original inquiry',
    'A shortage with the original inquiry timestamp is not a later confirmation',
    'A delayed pre-inquiry shortage packet is still old source information',
    'An older packet arrives after an already received newer shortage',
    'The same observed shortage precedes a private physical release',
    'A second explicitly required resource already blocks the original probe',
)))


def _start(patient, t):
    for field in ('occur_t', 'dispatch_t', 'ambulance_arrive_t', 'search_start_t'):
        patient[field] = t


def _delay_after(source_minute, horizon):
    # Existing integer-minute feed transport only. No extra publication is added.
    return {str(t): horizon + 1 for t in range(source_minute, horizon + 1)}


def _initial_reports(case):
    return [dict(hospital=h['id'],observed_at=0,
        resources={r:dict(capacity=q,used=q if h['id']=='H01' and r=='ed_bed' else 0)
            for r,q in h['capacity'].items()}) for h in case['hospitals']]


def _new_case(seed, family):
    case = make_previous(seed, BASE_FAMILY)
    case.update(id=f'probe-guard-{family}-{seed}', family=family,
        probe_guard_family=family, label=_LABELS[family], seed=seed)
    for key in ('recovery_rank_family', 'recovery_dispatch_family', 'retry_dispatch_family',
                'cooperative_probe_family', 'shortage_probe_family', 'scarce_delay_family', 'joint_policy_family'):
        case.pop(key, None)
    patient = case['patients'][0]
    hospital = case['hospitals'][0]
    config = case['observation_config']
    horizon = case['max_minutes']
    for h in case['hospitals']:
        h['initial_occupancy'] = []
    for h in case['hospitals'][:2]:
        h['initial_occupancy'] = [dict(until=8,demand=dict(ed_bed=h['capacity']['ed_bed']))]
    case['exogenous_events'] = []
    case['response_matrix'] = {'*':{h['id']:dict(delay=1.5 if h['id']=='H01' else 1 if h['id']=='H02' else 3,
        silent=h['id'] not in ('H01','H02')) for h in case['hospitals']}}
    config.update(feed_interval_minutes=1, feed_delay_minutes=0,
        feed_delay_by_source_minute={'0':1,**_delay_after(2, horizon)},
        initial_reports=_initial_reports(case))
    detail = dict(base_environment_family=BASE_FAMILY,
        original_optional_candidate='H01', first_normal_hospital='H02',
        raw_branch_reachability_must_be_verified=True,
        condition_change_runtime_event_supported=False,
        original_first_ask_must_be_observed=True,
        original_probe_minimum_since_first_ask_and_last_contact_minutes=.5,
        actual_normal_contacts_keep_original_half_minute_last_contact_gate=True,
        second_hospital_initial_optimistic_report_is_wrong=True,
        future_physical_release_is_private=True,
        automatic_feed_source_minutes_after_one_delayed_beyond_horizon=True,
        no_extra_resource_publication_or_worker_or_bed=True)

    if family in ('probe_guard_renewed_fresh_negative', 'probe_guard_hidden_recovery_adverse_twin',
                  'probe_guard_required_second_resource'):
        _start(patient, 0)
        detail.update(intended_first_ask_minute=0, intended_probe_review_minute=1.5,
            intended_shortage_source_minute=1,
            physical_initial_occupancy_until=8,
            intended_guard_comparison='observed_at_1_strictly_after_first_ask_0')
        if family == 'probe_guard_hidden_recovery_adverse_twin':
            hospital['initial_occupancy'][0]['until'] = 2
            detail.update(physical_initial_occupancy_until=2,
                observed_prefix_twin='probe_guard_renewed_fresh_negative',
                identical_received_history_through_intended_review_minute=1.5,
                no_automatic_future_free_feed_received=True,
                actual_probe_information_check_may_reveal_later_recovery=True,
                adverse_opportunity_loss_is_possible_not_guaranteed=True)
        elif family == 'probe_guard_renewed_fresh_negative':
            detail.update(observed_prefix_twin='probe_guard_hidden_recovery_adverse_twin',
                identical_received_history_through_intended_review_minute=1.5)
        else:
            patient['resource_demands']['special_team'] = 1
            hospital['initial_occupancy'][0]['demand']['special_team'] = hospital['capacity']['special_team']
            detail.update(original_probe_expected_blocking_reason='OTHER_RESOURCE_SHORTAGE',
                explicitly_required_resources=['ed_bed', 'special_team'])
    elif family == 'probe_guard_same_observed_timestamp':
        _start(patient, 0)
        config['feed_delay_by_source_minute']['1'] = horizon + 1
        detail.update(intended_first_ask_minute=0, intended_probe_review_minute=1.5,
            intended_shortage_source_minute=0,
            intended_guard_comparison='equal_source_timestamp_must_not_veto')
    elif family in ('probe_guard_predated_late', 'probe_guard_out_of_order'):
        _start(patient, 1)
        config['feed_delay_by_source_minute']['0'] = 2
        if family == 'probe_guard_predated_late':
            config['feed_delay_by_source_minute']['1'] = horizon + 1
            intended_source = 0
            receipt, review = 2, 2.5
        else:
            config['feed_delay_by_source_minute']['1'] = 0
            intended_source = 1
            receipt = review = 2
            detail['first_normal_hospital'] = 'H03'
        detail.update(intended_first_ask_minute=1, intended_probe_review_minute=review,
            delayed_packet_source_minute=0, delayed_packet_receipt_minute=receipt,
            intended_shortage_source_minute=intended_source,
            intended_guard_comparison='latest_source_not_after_actual_first_ask',
            older_packet_must_not_replace_newer_observation=family == 'probe_guard_out_of_order')
    else:
        raise AssertionError('unhandled probe guard family')

    case['assumptions'] += [
        'All compared branches use the identical physical environment, original capacities, workers, routes and existing report mechanisms.',
        'The guard receives current source-stamped observations and the actual first ask; future release, queued transport and reply outcomes stay private.',
        'The renewed and hidden-release twins have identical received histories through their first intended optional decision.',
        'Later automatic feeds use existing transport delays; actual inquiry-triggered information checks keep the existing confirmation channel and its real cost.',
        'Family labels and intended timestamps are design declarations; raw event and snapshot witnesses must establish realized branch reachability.',
        'No scenario is selected or tuned using validation performance.',
    ]
    case['experiment'] = dict(seed=seed, patient_count=len(case['patients']), probe_guard_family=family,
        comparison_scope='same_environment_original_optional_probe_off_or_renewed_source_time_guard',
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
        raise ValueError('unknown probe guard family')
    return deepcopy(make_previous(seed, family) if family in ORIGINAL_FAMILIES else _new_case(seed, family))


def build_probe_guard_scenarios(seed, families=None):
    if type(seed) is not int or seed < 0:
        raise ValueError('seed must be a nonnegative integer')
    if families is None:
        selected = list(FAMILIES)
    else:
        if isinstance(families, (str, bytes)):
            raise ValueError('families must be a distinct iterable of known names')
        try:
            selected = list(families)
        except TypeError as exc:
            raise ValueError('families must be iterable') from exc
        if any(not isinstance(f, str) or f not in FAMILIES for f in selected) or len(selected) != len(set(selected)):
            raise ValueError('unknown or duplicate probe guard family')
    return [make_scenario(seed, family) for family in selected]
