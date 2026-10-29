"""A same-count membership guard for an imminent infeasible residual plan.

The frozen residual policy always proposes first. Only a fresh four-slot plan
that schedules an overflowing contact before the next review can invoke the
frozen immediate-batch cost table. At identical public input, the inquiry
count and next-review time are preserved; later workflow feedback may differ.
"""
from copy import deepcopy
import hashlib
from pathlib import Path

from . import residual_policy as original, batch_policy as batch

NAME = 'eat_guard_l15_f'
BASE_POLICY = 'eat_residual_l15_f'
GUARD_RULE = 'same_count_imminent_capacity_guard'
SOURCE_SHA256 = {
    'residual_policy': '8cf391ab5062de374d3c843edd3f6b1be98ac281df9217acc0538cbd98b838ef',
    'batch_policy': '91106a7d9cb7bf23baca77b6f826d303c662c0628ebd85f7be3ae33926d8d246',
}
for _key, _module in (('residual_policy', original), ('batch_policy', batch)):
    if hashlib.sha256(Path(_module.__file__).read_bytes()).hexdigest() != SOURCE_SHA256[_key]:
        raise ValueError('frozen guard foundation source differs: ' + _key)


def spec(name=NAME):
    if name != NAME:
        raise ValueError('the fixed same-count guard candidate is ' + NAME)
    value = original.spec(BASE_POLICY)
    value.update(name=NAME, label='EAT l15 f, same-count imminent-capacity guard')
    return value


def _future_checks(proposal, own, now, review_seconds, max_concurrent):
    """No-reply fresh-count check at each scheduled contact before review.

    Existing inquiries and proposed contacts age by the same original 180s
    fresh convention. Farther future plans are kept only in the raw proposal;
    the next review occurs before they could justify a present intervention.
    """
    scheduled = [row for row in proposal['evidence']['candidate_plan']
        if row['planned_offset_seconds'] is not None]
    offsets = sorted({row['planned_offset_seconds'] for row in scheduled
        if 0 < row['planned_offset_seconds'] <= review_seconds})
    timeout = original.spec(BASE_POLICY)['config']['observation_timeout_seconds']
    checks = []
    for offset in offsets:
        existing = [row['hospital'] for row in own
            if now + offset - row['submitted_at'] <= timeout]
        planned = [row['hospital'] for row in scheduled
            if row['planned_offset_seconds'] <= offset
            and offset - row['planned_offset_seconds'] <= timeout]
        count = len(existing) + len(planned)
        checks.append(dict(offset_seconds=offset, existing_fresh_hospitals=existing,
            scheduled_fresh_hospitals=planned, fresh_own_count=len(existing),
            proposal_fresh_count=len(planned), total_fresh_count=count,
            conflict=count > max_concurrent))
    return checks


def decide(name, *, now, candidates, observations=(), pending=(), patient_pending=(), max_new,
           last_contact_at=None):
    """Preserve the original proposal except a strictly cheaper same-count batch."""
    policy = spec(name)
    # A server call normally supplies lists, while the pure policy contract also
    # accepts one-shot iterables. Materialize each once before the two frozen
    # readers and the fresh-count check reuse the same visible rows.
    kwargs = dict(now=now, candidates=list(candidates), observations=list(observations),
        pending=list(pending), patient_pending=list(patient_pending), max_new=max_new,
        last_contact_at=last_contact_at)
    proposal = original.decide(BASE_POLICY, **kwargs)
    # The original policy has already validated and normalized this public input.
    own = original.inquiry_policies._pending(kwargs['patient_pending'], now, own=True)
    fresh = proposal['evidence']['own_waiting_count']
    count = len(proposal['send'])
    cap = policy['config']['max_concurrent']
    checks = _future_checks(proposal, own, now, policy['review_seconds'], cap)
    conflicts = [row['offset_seconds'] for row in checks if row['conflict']]
    full = fresh + count == cap
    triggered = full and bool(conflicts)
    # With zero contacts the only same-count subset is empty and cannot improve.
    # Keep the trigger itself visible while avoiding a provably useless table.
    evaluated = triggered and count > 0
    metadata = dict(schema_version=1, rule=GUARD_RULE, source_sha256=dict(SOURCE_SHA256),
        proposal=proposal, review_window_seconds=policy['review_seconds'], max_concurrent=cap,
        fresh_own_count=fresh, proposal_send_count=count,
        current_planned_fresh_count=fresh+count, currently_full=full,
        future_checks=checks, conflict_offsets_seconds=conflicts, triggered=triggered,
        batch_evaluated=evaluated,
        evaluation_skip_reason=None if evaluated else 'zero_cardinality' if triggered else 'not_triggered',
        batch_output=None, same_count_costs=[], same_count_minimum_cost=None,
        best_same_count_hospitals=None, best_same_count_cost=None,
        proposal_immediate_cost=None, improvement_seconds=None, applied=False,
        output_cost_scope='original_residual_future_plan')
    result = deepcopy(proposal)
    result['evidence']['policy'] = name
    if evaluated:
        utility = batch.decide(batch.NAME, **kwargs)
        same_count = [dict(hospitals=list(row['hospitals']), cost=row['cost'])
            for row in utility['evidence']['batch']['subsets'] if len(row['hospitals']) == count]
        minimum = min(row['cost'] for row in same_count)
        best = next(row for row in same_count if row['cost'] <= minimum + 1e-9)
        proposed_set = set(proposal['send'])
        proposed_cost = next(row['cost'] for row in same_count if set(row['hospitals']) == proposed_set)
        improvement = proposed_cost - best['cost']
        applied = improvement > 1e-9
        metadata.update(batch_output=utility, same_count_costs=same_count,
            same_count_minimum_cost=minimum, best_same_count_hospitals=list(best['hospitals']),
            best_same_count_cost=best['cost'], proposal_immediate_cost=proposed_cost,
            improvement_seconds=improvement, applied=applied)
        if applied:
            selected = list(best['hospitals'])
            result['send'] = selected
            result['evidence']['sent_hospitals'] = list(selected)
            result['evidence']['planned_expected_cost'] = round(best['cost'], 3)
            for row in result['evidence']['candidate_plan']:
                row['planned_offset_seconds'] = 0 if row['hospital'] in selected else None
            metadata['output_cost_scope'] = 'immediate_same_count_batch'
    result['evidence']['guard'] = metadata
    return result
