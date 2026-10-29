"""Two fixed vetoes around the original optional shortage-probe decision.

The original pure decision is a shadow calculation, not a submitted inquiry.
Neither variant creates information or rewrites the original public snapshot.
"""
from copy import deepcopy

from .shortage_probe_engine import decide_shortage_probe


VARIANTS = ('off', 'renewed')
PROBE_GUARD_SPEC = dict(
    schema_version=1, variants=list(VARIANTS),
    original_probe_policy='simulation.shortage_probe_engine.decide_shortage_probe',
    original_probe_decision_is_shadow_only=True,
    off='veto_every_due_optional_shortage_probe_review',
    renewed='veto_original_selected_bed_shortage_observed_strictly_after_first_actual_ask',
    comparison_clock='ed_bed.observed_at > patient.first_ask.submitted_at',
    additional_age_or_quantity_threshold=False,
    original_full_snapshot_retained=True, original_probe_selection_unchanged=True,
    delegate_calls_original_probe_hook=True, veto_submits_no_probe=True,
    normal_recovery_rank_selector_unchanged=True,
    original_due_cycles_contacts_attempts_clock_fifo_workers_and_resources_retained=True,
    extra_resource_change_publications=False, future_response_or_resource_information=False)


def decide_probe_guard(variant, *, snapshot):
    """Validate the exact original input, then return a detached veto decision."""
    if variant not in VARIANTS:
        raise ValueError('known fixed optional-probe guard variant required')
    original = decide_shortage_probe(snapshot)
    send = list(original['send'])
    selected = send[0] if send else None
    first = snapshot['patient']['first_ask']
    first_at = None if first is None else first['submitted_at']
    observed_at = None
    if selected is not None:
        target = next(row for row in snapshot['candidates'] if row['hospital'] == selected)
        observed_at = target['resources']['ed_bed']['observed_at']
    if variant == 'off':
        action, reason = 'veto', 'ALL_OPTIONAL_PROBES_DISABLED'
    elif selected is None:
        action, reason = 'delegate', 'NO_ORIGINAL_PROBE_SELECTED'
    elif observed_at > first_at:
        action, reason = 'veto', 'POST_INITIAL_ASK_SHORTAGE_OBSERVATION'
    else:
        action, reason = 'delegate', 'ORIGINAL_PROBE_REVIEW_ALLOWED'
    return dict(schema_version=1, variant=variant, action=action, reason=reason,
        original_send=deepcopy(send), selected_hospital=selected,
        first_ask_submitted_at=first_at, selected_bed_observed_at=observed_at)
