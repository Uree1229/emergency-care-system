"""Report-supported clinical-decline recovery boundaries; old 109 inputs intact.

Development 44101..44103 is explicitly retrospective. Validation 46101..46106
is newly reserved. Before source freeze only structural small seeds are used.
Every branch receives identical physical, review and observation inputs.
"""
from __future__ import annotations

from copy import deepcopy

from .joint_policy_scenarios import _validate_extra_inputs
from .map_replay import validate_scenario
from .partial_information_scenarios import SCARCE
from .retry_dispatch_scenarios import FAMILIES as ORIGINAL_FAMILIES, make_scenario as make_previous


DEV_SEEDS = (44101, 44102, 44103)
DEVELOPMENT_SEEDS = DEV_SEEDS
VALIDATION_SEEDS = (46101, 46102, 46103, 46104, 46105, 46106)
ADDITIONAL_FAMILIES = (
    'recovery_dispatch_busy_nonadmission',
    'recovery_dispatch_silent_timeout',
    'recovery_dispatch_same_shortage_refresh',
    'recovery_dispatch_predecline_optimism_late',
    'recovery_dispatch_out_of_order_shortage',
    'recovery_dispatch_partial_unknown',
    'recovery_dispatch_stale_recovery',
    'recovery_dispatch_recovery_then_decline',
    'recovery_dispatch_slow_reopen_maturity',
    'recovery_dispatch_duplicate_recovery_feeds',
    'recovery_dispatch_future_single_candidate',
    'recovery_dispatch_contact_budget_pressure',
)
FAMILIES = ORIGINAL_FAMILIES + ADDITIONAL_FAMILIES
_BASE = 'retry_dispatch_decline_then_recovery'
BASE_FAMILIES = {family: _BASE for family in ADDITIONAL_FAMILIES}
BASE_FAMILIES['recovery_dispatch_busy_nonadmission'] = 'cooperative_probe_busy_admission'
BASE_FAMILIES['recovery_dispatch_contact_budget_pressure'] = 'scarce_contact_concurrency_limits'
_LABELS = {
    'recovery_dispatch_busy_nonadmission': 'A current busy nonadmission is not an actual clinical decline',
    'recovery_dispatch_silent_timeout': 'Completed silent review and an original timeout are not a clinical decline',
    'recovery_dispatch_same_shortage_refresh': 'Repeated fresh reports of the same shortage cannot create recovery',
    'recovery_dispatch_predecline_optimism_late': 'A pre-decline optimistic source packet arrives after the actual decline',
    'recovery_dispatch_out_of_order_shortage': 'An old shortage packet follows a newer actual recovery report',
    'recovery_dispatch_partial_unknown': 'Bed recovery does not confirm the other required resource',
    'recovery_dispatch_stale_recovery': 'A recovered physical resource is published only through stale delayed reports',
    'recovery_dispatch_recovery_then_decline': 'A real recovery is followed by a new physical outage and actual rejection',
    'recovery_dispatch_slow_reopen_maturity': 'A slow reopened review can delay a different ordinary offer that is about to arrive',
    'recovery_dispatch_duplicate_recovery_feeds': 'Late recovery and repeated identical free reports cannot renew one recovery token',
    'recovery_dispatch_future_single_candidate': 'A later real single-candidate request shares FIFO work with a reopened review',
    'recovery_dispatch_contact_budget_pressure': 'Twelve candidates and actual decline/recovery preserve original contact limits',
}


def _set_full_occupancy(case, until):
    hospital = case['hospitals'][0]
    hospital['initial_occupancy'] = [dict(until=until, demand=deepcopy(hospital['capacity']))]


def _initial_free_reports(case):
    return [dict(hospital=h['id'], observed_at=0,
        resources={resource: dict(capacity=quantity, used=0)
                   for resource, quantity in h['capacity'].items()}) for h in case['hospitals']]


def _new_case(seed, family):
    parent_family = BASE_FAMILIES[family]
    case = make_previous(seed, parent_family)
    case.update(id=f'recovery-dispatch-{family}-{seed}', family=family,
        recovery_dispatch_family=family, label=_LABELS[family], seed=seed)
    for key in ('retry_dispatch_family', 'cooperative_probe_family', 'shortage_probe_family',
                'scarce_delay_family', 'joint_policy_family'):
        case.pop(key, None)
    details = dict(base_environment_family=parent_family,
        raw_branch_reachability_must_be_verified=True,
        original_concurrent_handle_limit=3, original_episode_contact_limit=10,
        patient_condition_change_not_supported_by_runtime_inputs=True,
        clinical_decline_required_not_busy_timeout_or_control_completion=True)
    matrix = case['response_matrix']
    if family == 'recovery_dispatch_busy_nonadmission':
        case['inquiry_queue']['background_jobs'] = [dict(t=0, hospital='H01', service_minutes=5)]
        details.update(intent='resource recovery following a busy control does not fabricate a clinical decline token',
            actual_background_work_minutes=5, actual_initial_physical_release_minute=1,
            control_nonadmission_is_not_a_patient_response=True)
    elif family == 'recovery_dispatch_silent_timeout':
        matrix['*']['H01'] = dict(delay=.5, silent=True)
        details.update(intent='actual silent work keeps its original timeout without creating a decline',
            nearby_clinical_response_never_delivered=True)
    elif family == 'recovery_dispatch_same_shortage_refresh':
        _set_full_occupancy(case, 120)
        details.update(intent='timestamp refresh alone is not a numeric shortage-to-sufficient transition',
            physical_initial_occupancy_until=120, no_physical_recovery_within_observation=True)
    elif family == 'recovery_dispatch_predecline_optimism_late':
        case['hospitals'][0]['initial_occupancy'] = []
        # The existing outage event removes one slot. Three simultaneous real
        # events remove all three original beds, after the initial real ask.
        case['exogenous_events'] = [dict(type='resource_unavailable', t=1, until=120,
                                          hospital='H01', resource='ed_bed') for _ in range(3)]
        matrix['*']['H01'] = dict(delay=1.5, silent=False)
        case['observation_config']['feed_delay_by_source_minute'] = {'0': 4}
        details.update(intent='actual receipt after decline cannot repair a source clock older than the decline',
            optimistic_actual_source_minute=0, optimistic_packet_delivery_minute=4,
            actual_outage_minute=1, physical_outage_until=120, actual_bed_slots_unavailable=3)
    elif family == 'recovery_dispatch_out_of_order_shortage':
        case['observation_config']['feed_delay_by_source_minute'] = {'3': 4, '4': 0}
        matrix['*']['H01'] = dict(attempts=[dict(delay=.5, silent=False), dict(delay=2.75, silent=False)])
        details.update(intent='older shortage cannot overwrite a newer applied free report or renew a consumed token',
            older_shortage_source_minute=3, older_shortage_delivery_minute=7,
            newer_recovery_source_and_delivery_minute=4)
    elif family == 'recovery_dispatch_partial_unknown':
        case['patients'][0]['resource_demands'] = dict(ed_bed=1, special_team=1)
        case['observation_config'].update(feed_delay_minutes=1,
            feed_missing_resources=['special_team'], confirmation_silent=True,
            confirmation_missing_resources=['special_team'])
        details.update(intent='fresh bed recovery is insufficient when another explicitly required resource remains unknown',
            required_resources=['ed_bed', 'special_team'], missing_required_resource='special_team',
            first_partial_feed_delivery_minute=1)
    elif family == 'recovery_dispatch_stale_recovery':
        case['observation_config'].update(feed_delay_minutes=11, confirmation_silent=True)
        details.update(intent='physical recovery and clinical acceptance do not refresh eleven-minute-old quantity reports',
            actual_feed_transport_minutes=11, original_stale_threshold_minutes=10)
    elif family == 'recovery_dispatch_recovery_then_decline':
        matrix['*']['H01'] = dict(attempts=[dict(delay=.5, silent=False),
            dict(delay=2.5, silent=False), dict(delay=.5, silent=False)])
        case['exogenous_events'] = [dict(type='resource_unavailable', t=5, until=8,
                                          hospital='H01', resource='ed_bed') for _ in range(3)]
        details.update(intent='a recovery does not promise future acceptance or supply a second extension token',
            first_physical_release_minute=4, later_outage_minute=5, later_restore_minute=8,
            actual_second_review_service_minutes=2.5, actual_bed_slots_unavailable=3)
    elif family == 'recovery_dispatch_slow_reopen_maturity':
        matrix['*']['H01'] = dict(attempts=[dict(delay=.5, silent=False), dict(delay=2.75, silent=False)])
        matrix['*']['H04'] = dict(delay=2.5, silent=False)
        details.update(intent='a newly submitted ordinary review must retain its real cost under unchanged offer-readiness rules',
            target_hospital='H01', other_hospital='H04',
            target_actual_second_service_minutes=2.75, other_actual_service_minutes=2.5,
            original_offer_readiness_not_modified=True)
    elif family == 'recovery_dispatch_duplicate_recovery_feeds':
        _set_full_occupancy(case, 8)
        matrix['*']['H01'] = dict(attempts=[dict(delay=.5, silent=False), dict(delay=.5, silent=True)])
        details.update(intent='identical later free reports do not provide additional recovery permissions',
            physical_initial_occupancy_until=8, repeated_existing_feed_interval_minutes=1,
            later_actual_reviews_remain_silent=True)
    elif family == 'recovery_dispatch_future_single_candidate':
        case['hospitals'][0]['capabilities'].append(SCARCE)
        matrix['*']['H01'] = dict(attempts=[dict(delay=.5, silent=False), dict(delay=2.75, silent=False)])
        patient = deepcopy(case['patients'][0])
        patient.update(id='P02', label='Synthetic later single-hospital resource request',
            required_capabilities=[SCARCE], resource_demands=dict(ed_bed=1, special_team=1))
        for key in ('occur_t', 'dispatch_t', 'ambulance_arrive_t', 'search_start_t'):
            patient[key] = 5
        case['patients'].append(patient)
        case['response_matrix'] = {'P01': deepcopy(matrix['*']), 'P02': {
            h['id']: dict(delay=.25, silent=False) for h in case['hospitals']}}
        details.update(intent='an actually later request can face submitted work that the earlier policy could not foresee',
            later_patient='P02', actual_later_registration_minute=5,
            later_only_compatible_hospital='H01', actual_later_service_minutes=.25,
            target_actual_second_service_minutes=2.75)
    elif family == 'recovery_dispatch_contact_budget_pressure':
        _set_full_occupancy(case, 4)
        case['observation_config']['initial_reports'] = _initial_free_reports(case)
        matrix['*'] = {h['id']: dict(delay=.5, silent=h['id'] != 'H01') for h in case['hospitals']}
        details.update(intent='a recovery permission cannot add contact slots, reset a real episode or hide competing actual demand',
            structural_candidates_for_flexible=12, original_limited_patient='P02',
            physical_initial_occupancy_until=4, only_hospital_with_clinical_replies='H01')
    else:
        raise AssertionError('unhandled recovery dispatch family')
    case['assumptions'] = [*case['assumptions'],
        'Each reference and candidate receives the same complete physical, review, patient and observation environment.',
        'Actual clinical decline is separate from a busy control, review completion and timeout; none of those controls is acceptance.',
        'Source clocks, receipt clocks, partial quantities and applied report order are retained without a new resource-change push.',
        'Future requests, future review duration, later outage and restore times are private environment fields, never public policy inputs.',
        'Clinical priorities, original routes and reviewer counts are unchanged; any new explicit demand belongs to all branches of that new case.',
        'The runtime has no dynamic patient-condition input event; changed fingerprints are checked by policy/auditor unit boundaries only.',
        'A real newly submitted review is neither cancelled nor made cost-free when another hospital replies.']
    case['experiment'] = dict(seed=seed, patient_count=len(case['patients']), recovery_dispatch_family=family,
        comparison_scope='same_environment_actual_decline_report_recovery_eager_or_latched',
        clinical_values='synthetic', identity_source='synthetic', routing='synthetic_fixed_coordinates',
        timing_unit='real_minutes', review_resolution_seconds=15, history_timing_unit='relative_seconds',
        additional_resource_change_channel_enabled=False, **details)
    validate_scenario(case)
    _validate_extra_inputs(case)
    return case


def make_scenario(seed, family):
    if type(seed) is not int or seed < 0:
        raise ValueError('seed must be a nonnegative integer')
    if not isinstance(family, str) or family not in FAMILIES:
        raise ValueError('unknown recovery dispatch family')
    return deepcopy(make_previous(seed, family) if family in ORIGINAL_FAMILIES else _new_case(seed, family))


def build_recovery_dispatch_scenarios(seed, families=None):
    """Build 121 detached inputs; no policy or reserved-input run occurs here."""
    if type(seed) is not int or seed < 0:
        raise ValueError('seed must be a nonnegative integer')
    if families is None:
        selected = list(FAMILIES)
    else:
        if isinstance(families, (str, bytes)):
            raise ValueError('families must be a distinct iterable of names')
        try:
            selected = list(families)
        except TypeError as exc:
            raise ValueError('families must be a distinct iterable of names') from exc
        if any(not isinstance(f, str) or f not in FAMILIES for f in selected) or len(selected) != len(set(selected)):
            raise ValueError('unknown or duplicate recovery dispatch family')
    return [make_scenario(seed, family) for family in selected]
