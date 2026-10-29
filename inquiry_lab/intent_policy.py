"""A soft veto of a guarded swap using already published public plans.

The frozen guard always proposes first. Positive plans in the filtered public
journal view express tentative intent, not RUNNING status, resource ownership,
a service reservation, or knowledge that the hospital will eventually reply.
"""
from copy import deepcopy
import hashlib
from pathlib import Path

from server import inquiry_policies
from . import guard_policy as original

NAME = 'eat_intent_l15_f'
BASE_POLICY = 'eat_guard_l15_f'
INTENT_RULE = 'veto_guard_swap_on_public_intent_overlap'
GUARD_SOURCE_SHA256 = '8a3af35193c1bc97e09677fa9025758d5d51aed7562df99e23f7edd1dd8024dc'
if hashlib.sha256(Path(original.__file__).read_bytes()).hexdigest() != GUARD_SOURCE_SHA256:
    raise ValueError('frozen guarded-swap foundation source differs')


def spec(name=NAME):
    if name != NAME:
        raise ValueError('the fixed public-intent candidate is ' + NAME)
    value = original.spec(BASE_POLICY)
    value.update(name=NAME, label='EAT l15 f, soft public-intent veto of guarded swaps')
    return value


def _patient(value):
    return type(value) in (str, int) and not (isinstance(value, str) and not value)


def _validate_context(context, now, pending, own, policy):
    """Check the minimal filtered view; DB prefix provenance is audited outside.

    No relative ordering is assumed between event IDs and decision IDs: they
    are different journal domains. The adapter supplies only another currently
    unresolved owner's latest decision, ordered by increasing decision ID.
    """
    if type(context) is not dict or set(context) != {'patient_id','event_prefix_max','decision_prefix_max','plans'}:
        raise ValueError('public_context must contain only the four public journal fields')
    if not _patient(context['patient_id']):
        raise ValueError('public_context patient_id must be a nonempty string or integer')
    if any(type(context[key]) is not int or context[key] < 0
            for key in ('event_prefix_max','decision_prefix_max')):
        raise ValueError('public journal prefixes must be nonnegative integers')
    if type(context['plans']) is not list:
        raise ValueError('public plans must be a list')
    if own and {row['patient'] for row in own} != {context['patient_id']}:
        raise ValueError('public_context patient_id differs from patient_pending')
    owners = {row['patient'] for row in pending}
    contacted = {(row['patient'],row['hospital']) for row in pending}
    seen = set()
    previous_id = 0
    allowed = {offset for offset in policy['config']['plan_offsets_seconds'] if offset > 0}
    for plan in context['plans']:
        if type(plan) is not dict or set(plan) != {'patient','decision_id','at','candidate_plan'}:
            raise ValueError('public plan must contain only patient, decision ID, clock and filtered candidates')
        patient, decision, at = plan['patient'], plan['decision_id'], plan['at']
        if not _patient(patient) or patient == context['patient_id'] or patient not in owners or patient in seen:
            raise ValueError('public plan must describe one distinct other unresolved owner')
        if type(decision) is not int or not previous_id < decision <= context['decision_prefix_max']:
            raise ValueError('public decision IDs must be positive, bounded and strictly increasing')
        if type(at) is not int or not 0 <= at <= now or now-at > policy['review_seconds']:
            raise ValueError('public plan must be current within the unchanged review interval')
        candidates = plan['candidate_plan']
        if type(candidates) is not list or not candidates:
            raise ValueError('empty filtered owner plans must be omitted')
        hospitals = set()
        for row in candidates:
            if type(row) is not dict or set(row) != {'hospital','planned_offset_seconds'}:
                raise ValueError('public candidate plan contains unknown fields')
            hospital, offset = row['hospital'], row['planned_offset_seconds']
            if type(hospital) is not str or not hospital or hospital in hospitals:
                raise ValueError('public planned hospitals must be nonempty and distinct within each owner')
            if type(offset) is not int or offset not in allowed:
                raise ValueError('public planned offset must be a positive original grid offset')
            if (patient,hospital) in contacted:
                raise ValueError('public intent cannot describe a currently pending already-contacted hospital')
            hospitals.add(hospital)
        seen.add(patient)
        previous_id = decision
    return deepcopy(context)


def decide(name, *, now, candidates, observations=(), pending=(), patient_pending=(), max_new,
           last_contact_at=None, public_context):
    """Retain the complete guard or restore its current R proposal on overlap."""
    policy = spec(name)
    # The old seven public arguments are reused unchanged in meaning. Their
    # one-shot iterables must survive both the guard and the context check.
    kwargs = dict(now=now, candidates=list(candidates), observations=list(observations),
        pending=list(pending), patient_pending=list(patient_pending), max_new=max_new,
        last_contact_at=last_contact_at)
    guarded = original.decide(BASE_POLICY, **kwargs)
    unresolved = inquiry_policies._pending(kwargs['pending'], now)
    own = inquiry_policies._pending(kwargs['patient_pending'], now, own=True)
    context = _validate_context(public_context, now, unresolved, own, policy)
    guard = guarded['evidence']['guard']
    proposal = guard['proposal']
    added = [hospital for hospital in guarded['send'] if hospital not in proposal['send']]
    protected = sorted({row['hospital'] for plan in context['plans'] for row in plan['candidate_plan']})
    conflicts = [dict(hospital=hospital, patient=plan['patient'], decision_id=plan['decision_id'],
        at=plan['at'], planned_offset_seconds=row['planned_offset_seconds'])
        for hospital in added for plan in context['plans'] for row in plan['candidate_plan']
        if row['hospital'] == hospital]
    vetoed = guard['applied'] and bool(conflicts)
    result = deepcopy(proposal if vetoed else guarded)
    result['evidence']['policy'] = name
    result['evidence']['intent'] = dict(schema_version=1, rule=INTENT_RULE,
        source_guard_sha256=GUARD_SOURCE_SHA256, guard_output=guarded, public_context=context,
        guard_applied=guard['applied'], added_hospitals=added, protected_hospitals=protected,
        conflicts=conflicts, vetoed=vetoed,
        core_origin='residual_proposal' if vetoed else 'guard_output')
    return result
