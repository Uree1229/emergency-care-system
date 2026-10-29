"""One fixed current-cohort reserve filter on the frozen public-offer rule.

This preserves one own-demand copy beyond the complete compatible registered
SEARCH cohort. It is not a forecast or a guarantee for unregistered patients.
"""
from copy import deepcopy

from .public_offer_policy import decide_public_offer


VARIANTS = ('cohort_reserve',)
PUBLIC_OFFER_SPEC = dict(
    schema_version=1, variants=list(VARIANTS),
    base_policy='simulation.public_offer_policy.decide_public_offer',
    base_variant='public_blocked', all_original_public_checks_retained=True,
    snapshot='unchanged complete public-offer snapshot',
    every_actual_usable_option_checked=True, actual_options_unchanged=True,
    actual_option_order_unchanged=True,
    option_check_order='original snapshot valid_offers order; actual marker keeps acceptance order',
    reserve_own_demand_copies=1,
    new_threshold=True, fixed_threshold='free >= compatible SEARCH cohort demand + own demand',
    compatible_cohort='all original registered SEARCH rows, including self and all priorities',
    all_current_patient_required_resources_checked=True,
    original_registered_search_shared_capacity_check=True,
    actual_pending_and_other_offer_backups_retained=True,
    original_opportunity_pending_and_physical_gates_retained=True,
    inquiry_selector_and_clock_attempt_fifo_workers_resources_ttl_rules_retained=True,
    actual_downstream_paths_and_counts_may_change=True,
    extra_resource_change_publications=False,
    future_work_or_response_information=False,
    unregistered_patient_nonworsening_guarantee=False)


def decide_offer_reserve(*, snapshot, variant='cohort_reserve'):
    if variant not in VARIANTS:
        raise ValueError('the single fixed current-cohort reserve rule is required')
    # The frozen base validates the full exact snapshot, including resource
    # arithmetic, clocks, registered cohort and every real pending handle.
    base = decide_public_offer(snapshot=snapshot, variant='public_blocked')
    result = dict(schema_version=1, variant=variant, base_decision=deepcopy(base),
        option_checks=[], eligible=False, hospital=None,
        reason='BASE_PUBLIC_OFFER_INELIGIBLE', usable_options=[])
    if not base['eligible']:
        return result
    original = snapshot['original_snapshot']
    demand = original['patient']['demand']
    # This is the producer's unchanged actual usable-options condition. The
    # snapshot's offer order is preserved here. The actual marker separately
    # retains the original acceptance/insertion order; no option is filtered.
    offers = [offer for offer in original['valid_offers'] if all(
        view['status'] == 'fresh' and view['free'] is not None and view['free'] >= demand[r]
        for r, view in offer['resources'].items())]
    result['usable_options'] = [offer['hospital'] for offer in offers]
    for offer in offers:
        hid = offer['hospital']
        contenders = [row for row in original['search_patients']
            if hid in row['compatible_hospitals']]
        resources = {}
        for resource, own in demand.items():
            cohort = sum(row['demand'].get(resource, 0) for row in contenders)
            free = offer['resources'][resource]['free']
            resources[resource] = dict(cohort_demand=cohort, own_demand=own,
                free=free, required_free=cohort+own, residual_after_cohort=free-cohort,
                passed=free >= cohort+own)
        result['option_checks'].append(dict(hospital=hid, resources=resources,
            passed=all(row['passed'] for row in resources.values())))
    if not offers or not all(row['passed'] for row in result['option_checks']):
        return dict(result, reason='COHORT_RESERVE_INSUFFICIENT')
    return dict(result, eligible=True, hospital=base['hospital'], reason='COHORT_RESERVE_READY')
