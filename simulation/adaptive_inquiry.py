"""Choose inquiry concurrency from replies already observed by the server.

This is an experimental feedback policy, not a learned hospital-capacity model.
Its inputs deliberately exclude scheduled replies, actual hospital workers,
future patients and resource-change schedules. ``pending`` is the server's own
unresolved inquiry ledger; acceptance elsewhere does not cancel that work.
"""
from collections.abc import Mapping
import math
from statistics import median


DEFAULT_ADAPTIVE_CONFIG = {
    'initial_k': 3,
    'min_k': 1,
    'fast_k': 3,
    'window': 20,
    'min_samples': 3,
    'slow_minutes': 3,
    'timeout_fraction': 0.5,
}


def _integer(value, name, minimum=1):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f'{name} must be an integer >= {minimum}')
    return value


def _finite_number(value, name, minimum=0, strictly_positive=False):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < minimum
            or (strictly_positive and value == 0)):
        qualifier = '> 0' if strictly_positive else f'>= {minimum}'
        raise ValueError(f'{name} must be a finite number {qualifier}')
    return value


def validate_adaptive_config(config, *, max_k=None):
    """Return a fresh, complete config; reject typos and invalid thresholds.

    The cap can be lower than requested initial/fast concurrency: decisions
    clamp those values to ``max_k``. A cap below the configured minimum is an
    inconsistent configuration and is rejected. Configuration is never mutated.
    """
    if not isinstance(config, Mapping):
        raise ValueError('adaptive config must be a mapping')
    unknown = set(config) - set(DEFAULT_ADAPTIVE_CONFIG)
    if unknown:
        raise ValueError('unknown adaptive config fields: '
                         + ', '.join(sorted(str(key) for key in unknown)))
    result = {**DEFAULT_ADAPTIVE_CONFIG, **config}
    for key in ('initial_k', 'min_k', 'fast_k', 'window', 'min_samples'):
        _integer(result[key], key)
    if result['initial_k'] not in (1, 3):
        raise ValueError('initial_k must be 1 or 3')
    if not result['min_k'] <= result['initial_k'] <= result['fast_k']:
        raise ValueError('require min_k <= initial_k <= fast_k')
    _finite_number(result['slow_minutes'], 'slow_minutes', strictly_positive=True)
    fraction = _finite_number(result['timeout_fraction'], 'timeout_fraction')
    if fraction > 1:
        raise ValueError('timeout_fraction must be <= 1')
    if max_k is not None:
        _integer(max_k, 'max_k')
        if max_k < result['min_k']:
            raise ValueError('max_k must be >= min_k')
    return result


def decide_inquiry_limit(config, *, t, observations, pending, max_k):
    """Return ``{limit, reason, evidence}`` using only information through t.

    Observation rows contain ``submitted_t``, ``observed_t`` and an ``outcome``
    of reply/accept/decline/timeout. Hospital and patient identifiers may accompany
    them, but are not used to infer unobserved state. The rolling window is
    ``t - window < observed_t <= t``. A timeout is an observed deadline, not a
    claim that the hospital has stopped processing the request.

    Pending rows need only ``submitted_t``. Old unresolved inquiries remain in
    the backlog guard even when their submission predates the reply window.
    Future submissions and observations are ignored. No caller input is changed.

    Slow reply samples take priority over widening for timeouts. Widening for
    nonresponse additionally requires at least one observed non-slow reply;
    all-timeout history cannot distinguish congestion from missing replies.
    """
    validated = validate_adaptive_config(config, max_k=max_k)
    _integer(t, 't', 0)
    lower = t - validated['window']
    replies = []
    completed = 0
    timeouts = 0
    for observation in observations:
        submitted = _finite_number(observation['submitted_t'], 'submitted_t')
        observed = _finite_number(observation['observed_t'], 'observed_t')
        if submitted > t or not lower < observed <= t:
            continue
        if observed < submitted:
            raise ValueError('observed_t must be >= submitted_t')
        outcome = observation['outcome']
        if outcome not in ('reply', 'accept', 'decline', 'timeout'):
            raise ValueError('outcome must be reply, accept, decline, or timeout')
        completed += 1
        if outcome == 'timeout':
            timeouts += 1
        else:
            replies.append(observed - submitted)

    pending_count = 0
    aged_pending_count = 0
    for inquiry in pending:
        submitted = _finite_number(inquiry['submitted_t'], 'submitted_t')
        if submitted > t:
            continue
        pending_count += 1
        if t - submitted >= validated['slow_minutes']:
            aged_pending_count += 1

    reply_median = median(replies) if replies else None
    timeout_fraction = timeouts / completed if completed else None
    enough_replies = len(replies) >= validated['min_samples']
    enough_completed = completed >= validated['min_samples']
    if enough_replies and reply_median >= validated['slow_minutes']:
        limit, reason = validated['min_k'], 'slow_replies'
    elif (enough_completed and timeouts > 0
          and timeout_fraction >= validated['timeout_fraction']
          and reply_median is not None
          and reply_median < validated['slow_minutes']):
        limit, reason = max_k, 'timeouts_without_slow_replies'
    elif enough_replies:
        limit, reason = validated['fast_k'], 'fast_replies'
    elif aged_pending_count >= validated['min_samples']:
        limit, reason = validated['min_k'], 'aged_pending'
    else:
        limit, reason = validated['initial_k'], 'cold_start'

    evidence = {
        'completed_samples': completed,
        'reply_samples': len(replies),
        'timeout_samples': timeouts,
        'median_reply_minutes': reply_median,
        'timeout_fraction': timeout_fraction,
        'pending_count': pending_count,
        'aged_pending_count': aged_pending_count,
        'window_minutes': validated['window'],
        'min_samples': validated['min_samples'],
        'slow_minutes': validated['slow_minutes'],
        'timeout_fraction_threshold': validated['timeout_fraction'],
        'initial_k': validated['initial_k'],
        'min_k': validated['min_k'],
        'fast_k': validated['fast_k'],
        'max_k': max_k,
    }
    return {'limit': min(limit, max_k), 'reason': reason, 'evidence': evidence}
