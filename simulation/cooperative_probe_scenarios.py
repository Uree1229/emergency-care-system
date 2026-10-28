"""Boundaries for explicitly cooperative optional hospital review work.

All preceding 83 inputs remain complete copies. Twelve new inputs vary supported
service work, real requests, physical occupancy, worker count or offer lifetime.
No future job status or completion schedule is inserted into a policy field.
"""
from __future__ import annotations

from copy import deepcopy

from .joint_policy_scenarios import _validate_extra_inputs
from .map_replay import validate_scenario
from .partial_information_scenarios import SCARCE
from .shortage_probe_scenarios import (
    FAMILIES as ORIGINAL_FAMILIES,
    make_scenario as make_shortage_probe_scenario,
)


DEV_SEEDS = (41101, 41102, 41103)
DEVELOPMENT_SEEDS = DEV_SEEDS
VALIDATION_SEEDS = (42101, 42102, 42103, 42104, 42105, 42106)
ADDITIONAL_FAMILIES = (
    'cooperative_probe_active_reply_050',
    'cooperative_probe_active_reply_300',
    'cooperative_probe_pause_resume',
    'cooperative_probe_two_normal_fifo',
    'cooperative_probe_completed_before_normal',
    'cooperative_probe_busy_admission',
    'cooperative_probe_silent_completion',
    'cooperative_probe_expired_offer',
    'cooperative_probe_multiple_workers',
    'cooperative_probe_protected_hospital_alternative',
    'cooperative_probe_busy_early_report',
    'cooperative_probe_busy_attempt_vary',
)
FAMILIES = ORIGINAL_FAMILIES + ADDITIONAL_FAMILIES
_BASE_FAMILY = 'shortage_probe_release_before_reply'
_LABELS = {
    'cooperative_probe_active_reply_050': 'An admitted optional review needs half a minute of actual work',
    'cooperative_probe_active_reply_300': 'An admitted optional review remains active for three minutes',
    'cooperative_probe_pause_resume': 'An actual normal inquiry interrupts optional work that later resumes',
    'cooperative_probe_two_normal_fifo': 'Two normal inquiries preserve their actual FIFO order ahead of optional work',
    'cooperative_probe_completed_before_normal': 'Already completed optional work cannot be retroactively preempted',
    'cooperative_probe_busy_admission': 'An occupied normal reviewer cannot admit optional work',
    'cooperative_probe_silent_completion': 'An optional job can complete without delivering a clinical reply',
    'cooperative_probe_expired_offer': 'An actual optional offer expires while a nearer ordinary inquiry remains pending',
    'cooperative_probe_multiple_workers': 'A second existing worker does not create extra service capacity or future knowledge',
    'cooperative_probe_protected_hospital_alternative': 'A currently protected hospital remains separate from a different optional candidate',
    'cooperative_probe_busy_early_report': 'An actual busy contact is fifteen seconds old when a current free-bed report arrives',
    'cooperative_probe_busy_attempt_vary': 'Busy nonadmission consumes a real contact identity before an attempt-dependent clinical response',
}


def _normal_patient(case, pid, *, t, priority=4):
    patient = deepcopy(case['patients'][0])
    patient.update(id=pid, label=f'Synthetic normal single-hospital patient {pid}',
        priority=priority, required_capabilities=[SCARCE],
        resource_demands=dict(ed_bed=1, special_team=1))
    for field in ('occur_t', 'dispatch_t', 'ambulance_arrive_t', 'search_start_t'):
        patient[field] = t
    return patient


def _matrix(case, normal_work):
    template = case['response_matrix']['*']
    rows = {case['patients'][0]['id']: deepcopy(template)}
    for patient in case['patients'][1:]:
        rows[patient['id']] = {hospital['id']: dict(
            delay=normal_work.get(patient['id'], .25), silent=False)
            for hospital in case['hospitals']}
    case['response_matrix'] = rows


def _new_case(seed, family):
    case = make_shortage_probe_scenario(seed, _BASE_FAMILY)
    case.update(id=f'cooperative-probe-{family}-{seed}', family=family,
        cooperative_probe_family=family, label=_LABELS[family], seed=seed)
    case.pop('shortage_probe_family', None)
    h01, h02 = case['hospitals']
    p01 = case['patients'][0]
    details = dict(base_environment_family=_BASE_FAMILY,
        optional_candidate='H01', original_pending_hospital='H02',
        physical_initial_bed_occupancy_until=1,
        original_inquiry_timeout_minutes=3, original_accept_ttl_minutes=10,
        raw_branch_reachability_must_be_verified=True,
        queue_control_status_is_not_a_clinical_reply=True)
    if family == 'cooperative_probe_active_reply_050':
        case['response_matrix']['*']['H01']['delay'] = .5
        details.update(intent='short_actual_optional_work_keeps_its_original_reply_and_resource_gates',
                       actual_optional_service_minutes=.5)
    elif family == 'cooperative_probe_active_reply_300':
        case['response_matrix']['*']['H01']['delay'] = 3
        details.update(intent='running_optional_work_must_not_be_mistaken_for_completed_silent_work',
                       actual_optional_service_minutes=3)
    elif family == 'cooperative_probe_pause_resume':
        h01['capabilities'].append(SCARCE)
        h02['capacity']['special_team'] = 0
        case['response_matrix']['*']['H01']['delay'] = 2.5
        case['patients'].append(_normal_patient(case, 'P02', t=1))
        _matrix(case, dict(P02=.5))
        details.update(intent='normal_work_preempts_only_unfinished_optional_work_without_discarding_progress',
            actual_optional_service_minutes=2.5, later_patient='P02',
            later_actual_request_minute=1, later_normal_service_minutes=.5)
    elif family == 'cooperative_probe_two_normal_fifo':
        h01['capabilities'].append(SCARCE)
        h01['capacity'].update(ed_bed=2, special_team=2)
        h02['capacity'].update(ed_bed=2, special_team=0)
        p01['resource_demands']['ed_bed'] = 2
        case['response_matrix']['*']['H01']['delay'] = 2.5
        case['patients'].extend([_normal_patient(case, 'P02', t=1, priority=2),
                                 _normal_patient(case, 'P03', t=1, priority=4)])
        _matrix(case, dict(P02=.25, P03=.5))
        details.update(intent='normal_review_order_follows_actual_submissions_and_not_the_optional_job_or_a_new_triage_rule',
            actual_optional_service_minutes=2.5, later_patients=['P02', 'P03'],
            later_actual_request_minute=1, normal_service_minutes=dict(P02=.25, P03=.5),
            supplied_priorities=dict(P01=4, P02=2, P03=4), first_patient_bed_demand=2)
    elif family == 'cooperative_probe_completed_before_normal':
        h01['capabilities'].append(SCARCE)
        h01['initial_occupancy'][0]['until'] = .5
        h02['capacity']['special_team'] = 0
        case['response_matrix']['*']['H01']['delay'] = .25
        case['patients'].append(_normal_patient(case, 'P02', t=1))
        _matrix(case, dict(P02=.25))
        details.update(intent='finished_optional_work_is_historical_and_current_allocation_protection_remains_in_force',
            physical_initial_bed_occupancy_until=.5, actual_optional_service_minutes=.25,
            later_patient='P02', later_actual_request_minute=1)
    elif family == 'cooperative_probe_busy_admission':
        case['inquiry_queue']['background_jobs'] = [dict(t=0, hospital='H01', service_minutes=3)]
        details.update(intent='nonadmission_is_a_current_control_result_and_does_not_consume_normal_hospital_eligibility_or_cooldown; actual_contact_attempt_counted',
                       actual_background_work_minutes=3)
    elif family == 'cooperative_probe_silent_completion':
        case['response_matrix']['*']['H01'] = dict(delay=1.5, silent=True)
        details.update(intent='completed_control_status_does_not_create_an_acceptance_or_resource_report',
            actual_optional_service_minutes=1.5, nearby_clinical_reply_never_delivered=True)
    elif family == 'cooperative_probe_expired_offer':
        h01.update(lat=35.189481, lon=126.85)
        h02.update(lat=35.18, lon=126.85)
        h01['initial_occupancy'][0]['until'] = .5
        case['response_matrix']['*']['H01']['delay'] = .25
        case['accept_ttl_minutes'] = 1
        details.update(intent='a_real_optional_offer_expires_before_the_nearer_normal_reply_without_ttl_refresh',
            physical_initial_bed_occupancy_until=.5, actual_optional_service_minutes=.25,
            original_accept_ttl_minutes=1, optional_route_minutes=5, normal_route_minutes=3)
    elif family == 'cooperative_probe_multiple_workers':
        case['inquiry_queue'].update(workers=2,
            background_jobs=[dict(t=0, hospital='H01', service_minutes=3)])
        case['response_matrix']['*']['H01']['delay'] = 1.5
        details.update(intent='optional_admission_respects_the_actual_existing_worker_contract',
            actual_background_work_minutes=3, actual_optional_service_minutes=1.5,
            existing_workers_per_hospital=2)
    elif family == 'cooperative_probe_protected_hospital_alternative':
        h01['capabilities'].append(SCARCE)
        h01['capacity']['ed_bed'] = 2
        h02['capacity'].update(ed_bed=2, special_team=0)
        p01['resource_demands']['ed_bed'] = 2
        h03 = deepcopy(h02)
        h03.update(id='H03', name='Synthetic separate optional candidate H03',
            lat=35.184, lon=126.85,
            initial_occupancy=[dict(until=1, demand=dict(ed_bed=2))])
        case['hospitals'].append(h03)
        case['response_matrix']['*']['H03'] = dict(delay=.5, silent=False)
        case['patients'].append(_normal_patient(case, 'P02', t=0))
        _matrix(case, dict(P02=2))
        details.update(intent='existing_registered_h01_pending_protection_does_not_relabel_a_separate_h03_candidate',
            protected_hospital='H01', optional_candidate='H03', other_patient='P02',
            other_actual_request_minute=0, first_patient_bed_demand=2)
    elif family == 'cooperative_probe_busy_early_report':
        h01.update(lat=h02['lat'], lon=h02['lon'])
        h03 = deepcopy(h01)
        h03.update(id='H03', name='Synthetic occupied-reviewer candidate H03', lat=35.18)
        case['hospitals'].append(h03)
        case['response_matrix']['*'].update(H01=dict(delay=.25, silent=False),
            H03=dict(delay=.75, silent=False))
        case['inquiry_queue']['background_jobs'] = [dict(t=0, hospital='H03', service_minutes=1)]
        case['observation_config']['feed_delay_by_source_minute'] = {'0': 1}
        case['observation_config']['initial_reports'] = [dict(hospital=hospital['id'],
            observed_at=-.25 if hospital['id'] == 'H01' else 0,
            resources={resource: dict(capacity=capacity,
                used=1 if hospital['id'] == 'H03' and resource == 'ed_bed' else 0)
                for resource, capacity in hospital['capacity'].items()})
            for hospital in case['hospitals']]
        details.update(intent='actual_busy_contact_time_is_retained_when_next_normal_selector_sees_a_new_free_bed_report',
            optional_candidate='H03', actual_background_work_minutes=1,
            initially_optimistic_hospital='H01', initially_optimistic_source_minute=-.25,
            initially_optimistic_report_disagrees_with_current_physical_occupancy=True,
            existing_source_zero_feed_delay_minutes=1,
            actual_first_clinical_decline_work_minutes=.25,
            initial_h01_h02_route_minutes=5,
            physical_h01_h03_initial_bed_occupancy_until=1,
            intended_raw_contact_spacing_requires_verification=True)
    elif family == 'cooperative_probe_busy_attempt_vary':
        case['inquiry_queue']['background_jobs'] = [dict(t=0, hospital='H01', service_minutes=3)]
        case['response_matrix']['*']['H01'] = dict(attempts=[
            dict(delay=.25, silent=False), dict(delay=.75, silent=True),
            dict(delay=.25, silent=False)])
        details.update(intent='the_new_control_workflow_cannot_hide_the_cost_of_consuming_a_contact_attempt',
            actual_background_work_minutes=3,
            clinical_response_matrix_depends_on_actual_clinical_review_attempt=True,
            first_clinical_review_reply='non_silent_quarter_minute',
            second_clinical_review_reply='silent_three_quarter_minute',
            later_clinical_review_reply='non_silent_quarter_minute',
            actual_contact_and_clinical_review_attempt_contracts_must_be_disclosed=True)
    else:
        raise AssertionError('unhandled cooperative-probe family')
    case['assumptions'] = [*case['assumptions'],
        'Optional-job admission and status acknowledgments are an explicitly separate experimental communication contract.',
        'A current control acknowledgment reports nonadmission, running, paused or completed state and is not a clinical acceptance.',
        'The same existing workers perform the same fixed service work; interrupted optional work retains completed and remaining work.',
        'Normal inquiry work preserves its actual original submission order; future arrivals and future service times remain private.',
        'No new resource publication, future resource prediction, free worker, service-time scaling or clinical priority inference is added.',
        'Zero service, acknowledgment transport lag and switching overhead are not silently introduced as unsupported scenario fields.']
    case['experiment'] = dict(seed=seed, patient_count=len(case['patients']),
        cooperative_probe_family=family, comparison_scope='same_resource_reports_new_current_workflow_acknowledgments',
        clinical_values='synthetic', identity_source='synthetic', routing='synthetic_fixed_coordinates',
        timing_unit='real_minutes', review_resolution_seconds=15, history_timing_unit='relative_seconds',
        additional_resource_change_channel_enabled=False, **details)
    validate_scenario(case)
    _validate_extra_inputs(case)
    return case


def make_scenario(seed, family):
    """Return detached inputs without modifying any preceding 83-family value."""
    if type(seed) is not int or seed < 0:
        raise ValueError('seed must be a nonnegative integer')
    if not isinstance(family, str) or family not in FAMILIES:
        raise ValueError('unknown cooperative-probe family')
    if family in ORIGINAL_FAMILIES:
        return deepcopy(make_shortage_probe_scenario(seed, family))
    return deepcopy(_new_case(seed, family))


def build_cooperative_probe_scenarios(seed, families=None):
    """Build all 95 inputs, or a distinct ordered family selection."""
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
            raise ValueError('unknown or duplicate cooperative-probe family')
    return [make_scenario(seed, family) for family in selected]
