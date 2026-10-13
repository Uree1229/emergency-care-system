"""Hospital-specific inquiry ranking and delayed hedging from observed data.

This experimental planner knows only the server's own submitted inquiries and
the replies/timeouts already observed. It does not read scheduled completion
times, worker counts, resource availability or future patients. A score is a
heuristic cost, not a prediction of acceptance or a clinical outcome.

Call periodically with the current ledger. The caller controls review spacing
and removes received/expired requests from ``pending``. A lower desired load
never cancels a submitted request: at most one new inquiry is normally sent.
"""
from collections.abc import Mapping
import math
from statistics import median


DEFAULT_OBSERVED_CONFIG = {
    'window': 30,
    'prior_reply': 3,
    'min_samples': 2,
    'congestion_weight': 1.0,
    'hedge_after': 2,
    'hedge_margin': 0,
    'max_new_per_review': 1,
    'timeout_penalty': 6,
}


def _integer(value, name, minimum=1):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f'{name} must be an integer >= {minimum}')
    return value


def _number(value, name, *, positive=False):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < 0
            or (positive and value == 0)):
        raise ValueError(f'{name} must be a finite number '
                         + ('> 0' if positive else '>= 0'))
    return value


def _hospital(row):
    value = row['hospital']
    if not isinstance(value, str) or not value:
        raise ValueError('hospital must be a nonempty string')
    return value


def _pending_key(row):
    patient = row['patient']
    if not isinstance(patient, (str, int)) or isinstance(patient, bool):
        raise ValueError('patient must be a string or integer')
    return (_hospital(row), patient, row['submitted_t'])


def validate_observed_config(config, max_k=None):
    """Return an independent complete config, rejecting typos and bad bounds."""
    if not isinstance(config, Mapping):
        raise ValueError('observed inquiry config must be a mapping')
    unknown = set(config) - set(DEFAULT_OBSERVED_CONFIG)
    if unknown:
        raise ValueError('unknown observed inquiry config fields: '
                         + ', '.join(sorted(str(key) for key in unknown)))
    result = {**DEFAULT_OBSERVED_CONFIG, **config}
    for key in ('window', 'min_samples', 'max_new_per_review'):
        _integer(result[key], key)
    for key in ('prior_reply', 'hedge_after'):
        _number(result[key], key, positive=True)
    for key in ('congestion_weight', 'hedge_margin', 'timeout_penalty'):
        _number(result[key], key)
    if max_k is not None:
        _integer(max_k, 'max_k')
    return result


def plan_inquiries(config, *, t, candidates, observations, pending,
                   patient_pending, max_k):
    """Return ``{send, reason, evidence}`` without changing caller inputs.

    Candidates contain ``hospital, travel_minutes`` and have already passed
    structural filtering; the caller must exclude tried/open/accepted hospitals.
    Observations contain ``hospital, submitted_t, observed_t, outcome`` where
    outcome is reply/accept/decline/timeout. Only ``t-window < observed_t <= t``
    is used. Unresolved pending rows contain ``hospital, patient, submitted_t``;
    the current patient's subset additionally contains ``travel_minutes``.
    Future submissions/observations are ignored before interpreting their data.

    New-candidate delay = base reply median + congestion_weight * own unresolved
    requests at that hospital. Base is the hospital median with min_samples,
    otherwise the global observed median with min_samples, otherwise prior_reply.
    Timeout risk is smoothed as (timeouts + 1)/(completed + 2). Candidate cost
    adds travel and timeout_penalty * risk to the delay.

    Pending delay excludes itself from the backlog. Before that estimated total
    delay has elapsed, remaining delay is max(1, estimate - age). Once overdue,
    a guard raises it to at least base + (age - estimate): absence of a reply
    must not make the planner increasingly certain it will arrive next minute.
    This guard is an explicit cost rule, not an estimated conditional mean.

    Initially send the best one. With pending work, hedge only after hedge_after
    when new cost + hedge_margin is strictly lower than the best pending cost.
    Cap concurrent inquiries and max_new_per_review; submitted work is retained.
    """
    cfg = validate_observed_config(config, max_k=max_k)
    _integer(t, 't', 0)

    history = {}
    global_replies = []
    for row in observations:
        submitted = _number(row['submitted_t'], 'submitted_t')
        observed = _number(row['observed_t'], 'observed_t')
        if submitted > t or not t - cfg['window'] < observed <= t:
            continue
        if observed < submitted:
            raise ValueError('observed_t must be >= submitted_t')
        hospital = _hospital(row)
        outcome = row['outcome']
        if outcome not in ('reply', 'accept', 'decline', 'timeout'):
            raise ValueError('outcome must be reply, accept, decline, or timeout')
        stats = history.setdefault(hospital, {'replies': [], 'timeouts': 0})
        if outcome == 'timeout':
            stats['timeouts'] += 1
        else:
            delay = observed - submitted
            stats['replies'].append(delay)
            global_replies.append(delay)

    # Duplicate ledger rows describe the same inquiry and must not inflate load.
    ledger = {}
    for row in pending:
        submitted = _number(row['submitted_t'], 'submitted_t')
        if submitted <= t:
            ledger[_pending_key(row)] = row
    pending_counts = {}
    for hospital, _, _ in ledger:
        pending_counts[hospital] = pending_counts.get(hospital, 0) + 1

    own = {}
    for row in patient_pending:
        submitted = _number(row['submitted_t'], 'submitted_t')
        if submitted > t:
            continue
        key = _pending_key(row)
        if key not in ledger:
            raise ValueError('patient_pending must be a subset of pending')
        _number(row['travel_minutes'], 'travel_minutes')
        own[key] = row
    if len({key[1] for key in own}) > 1:
        raise ValueError('patient_pending must describe one patient')

    unique_candidates = {}
    for row in candidates:
        hospital = _hospital(row)
        travel = _number(row['travel_minutes'], 'travel_minutes')
        if hospital in unique_candidates:
            raise ValueError('candidate hospitals must be unique')
        unique_candidates[hospital] = travel
    open_hospitals = {key[0] for key in own}
    # Be defensive if a caller accidentally leaves an open hospital in candidates.
    for hospital in open_hospitals:
        unique_candidates.pop(hospital, None)

    global_median = median(global_replies) if global_replies else None
    hospital_evidence = {}

    def estimate(hospital):
        if hospital in hospital_evidence:
            return hospital_evidence[hospital]
        stats = history.get(hospital, {'replies': [], 'timeouts': 0})
        replies = stats['replies']
        completed = len(replies) + stats['timeouts']
        if len(replies) >= cfg['min_samples']:
            base, source = median(replies), 'hospital'
        elif len(global_replies) >= cfg['min_samples']:
            base, source = global_median, 'global'
        else:
            base, source = cfg['prior_reply'], 'prior'
        result = {
            'hospital': hospital,
            'reply_samples': len(replies),
            'timeout_samples': stats['timeouts'],
            'completed_samples': completed,
            'median_reply_minutes': median(replies) if replies else None,
            'base_reply_minutes': base,
            'reply_estimate_source': source,
            'smoothed_timeout_fraction': (stats['timeouts'] + 1) / (completed + 2),
            'unresolved_inquiries': pending_counts.get(hospital, 0),
        }
        hospital_evidence[hospital] = result
        return result

    candidate_scores = []
    for hospital, travel in unique_candidates.items():
        info = estimate(hospital)
        reply = (info['base_reply_minutes']
                 + cfg['congestion_weight'] * info['unresolved_inquiries'])
        risk_cost = cfg['timeout_penalty'] * info['smoothed_timeout_fraction']
        candidate_scores.append({
            'hospital': hospital, 'travel_minutes': travel,
            'estimated_reply_minutes': reply, 'timeout_cost_minutes': risk_cost,
            'score_minutes': travel + reply + risk_cost,
        })
    candidate_scores.sort(key=lambda row: (row['score_minutes'],
                                          row['travel_minutes'], row['hospital']))

    pending_scores = []
    for key, row in own.items():
        hospital, _, submitted = key
        info = estimate(hospital)
        age = t - submitted
        expected = (info['base_reply_minutes'] + cfg['congestion_weight']
                    * max(0, info['unresolved_inquiries'] - 1))
        remaining = max(1, expected - age)
        overdue = age >= expected
        if overdue:
            remaining = max(remaining, info['base_reply_minutes'] + age - expected)
        risk_cost = cfg['timeout_penalty'] * info['smoothed_timeout_fraction']
        pending_scores.append({
            'hospital': hospital, 'submitted_t': submitted, 'age_minutes': age,
            'travel_minutes': row['travel_minutes'],
            'estimated_total_reply_minutes': expected,
            'remaining_cost_minutes': remaining,
            'overdue_uncertainty_guard': overdue,
            'timeout_cost_minutes': risk_cost,
            'score_minutes': row['travel_minutes'] + remaining + risk_cost,
        })
    pending_scores.sort(key=lambda row: (row['score_minutes'],
                                        row['travel_minutes'], row['hospital'],
                                        row['submitted_t']))
    oldest_age = max((row['age_minutes'] for row in pending_scores), default=None)

    send = []
    if len(own) >= max_k:
        reason = 'concurrency_cap'
    elif not candidate_scores:
        reason = 'no_candidates'
    elif not own:
        send, reason = [candidate_scores[0]['hospital']], 'initial_best'
    elif oldest_age < cfg['hedge_after']:
        reason = 'wait_for_hedge'
    else:
        threshold = pending_scores[0]['score_minutes']
        competitive = [row for row in candidate_scores
                       if row['score_minutes'] + cfg['hedge_margin'] < threshold]
        available = min(max_k - len(own), cfg['max_new_per_review'])
        send = [row['hospital'] for row in competitive[:available]]
        reason = 'hedge_better_candidate' if send else 'pending_preferred'

    evidence = {
        'evaluated_t': t,
        'window_minutes': cfg['window'],
        'global_reply_samples': len(global_replies),
        'global_median_reply_minutes': global_median,
        'patient_pending_count': len(own),
        'oldest_pending_age_minutes': oldest_age,
        'max_k': max_k,
        'hedge_after_minutes': cfg['hedge_after'],
        'hedge_margin_minutes': cfg['hedge_margin'],
        'max_new_per_review': cfg['max_new_per_review'],
        'hospitals': [hospital_evidence[hid] for hid in sorted(hospital_evidence)],
        'candidate_scores': candidate_scores,
        'pending_scores': pending_scores,
        'sent_hospitals': list(send),
    }
    return {'send': send, 'reason': reason, 'evidence': evidence}
