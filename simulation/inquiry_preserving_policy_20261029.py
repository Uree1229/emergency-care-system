"""Current-ledger inquiry refinements; allocation remains frozen G2.

These are research policies, not predictions of hospital acceptance or future
resource availability. Full-episode G2 preservation must be tested separately.
Only exact whitelisted, already observed inputs are accepted by these functions.
"""
from copy import deepcopy
import math
from statistics import median

SPECS = (
    dict(id='QI_F1', refill='one', retry='baseline'),
    dict(id='QI_FA', refill='all', retry='baseline'),
    dict(id='QI_R2', refill='none', retry='fixed_2'),
    dict(id='QI_RA', refill='none', retry='history'),
    dict(id='QI_FA_R2', refill='all', retry='fixed_2'),
    dict(id='QI_FA_RA', refill='all', retry='history'),
)
REFILL_KEYS = {'t', 'patient', 'priority', 'request_t', 'state', 'has_valid_offer',
               'ready', 'received_this_tick', 'retry_at', 'pending', 'candidates', 'max_k'}
RETRY_KEYS = {'t', 'patient', 'priority', 'baseline_wait', 'observations'}
HISTORY_WINDOW = 30
HISTORY_LIMIT = 6


def _integer(value, label, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError(label + ' must be an integer >= ' + str(minimum))
    return value


def _identifier(value, label):
    if type(value) is not str or not value:
        raise ValueError(label + ' must be a nonempty string')
    return value


def normalize_spec(spec):
    if type(spec) is not dict or set(spec) != {'id', 'refill', 'retry'}:
        raise ValueError('exact inquiry spec id/refill/retry required')
    _identifier(spec['id'], 'policy id')
    if spec['id'] == 'G2':
        raise ValueError('modified inquiry policy cannot be labelled an original G2 arm')
    if spec['refill'] not in ('none', 'one', 'all') or spec['retry'] not in ('baseline', 'fixed_2', 'history'):
        raise ValueError('unknown refill or retry mode')
    return deepcopy(spec)


def _header(snapshot, fields):
    if type(snapshot) is not dict or set(snapshot) != fields:
        raise ValueError('current inquiry snapshot whitelist differs')
    _integer(snapshot['t'], 'current time')
    _identifier(snapshot['patient'], 'patient')
    if type(snapshot['priority']) is not int or snapshot['priority'] not in (1, 2, 4):
        raise ValueError('experimental priority must be 1, 2, or 4')


def decide_refill(spec, snapshot):
    """Fill at most three open slots after current G2 commitments are done."""
    cfg = normalize_spec(spec)
    _header(snapshot, REFILL_KEYS)
    t = snapshot['t']
    _integer(snapshot['request_t'], 'request time')
    _identifier(snapshot['state'], 'patient state')
    maximum = _integer(snapshot['max_k'], 'concurrency cap', 1)
    if maximum != 3:
        raise ValueError('this experiment fixes max concurrency to three')
    for key in ('has_valid_offer', 'ready', 'received_this_tick'):
        if type(snapshot[key]) is not bool:
            raise ValueError(key + ' must be boolean')
    if snapshot['retry_at'] is not None:
        _integer(snapshot['retry_at'], 'retry time')
    if type(snapshot['pending']) is not list or type(snapshot['candidates']) is not list:
        raise ValueError('pending and candidates must be lists')
    pending = []
    for row in snapshot['pending']:
        if type(row) is not dict or set(row) != {'hospital', 'submitted_t'}:
            raise ValueError('pending may contain only hospital and submitted time')
        hid = _identifier(row['hospital'], 'pending hospital')
        sent = _integer(row['submitted_t'], 'submission time')
        if sent > t:
            raise ValueError('future inquiry supplied')
        pending.append(hid)
    if len(pending) != len(set(pending)) or len(pending) > maximum:
        raise ValueError('duplicate or excessive open inquiries')
    candidates = []
    for row in snapshot['candidates']:
        if type(row) is not dict or set(row) != {'hospital', 'travel_minutes'}:
            raise ValueError('candidate may contain only hospital and current travel estimate')
        hid = _identifier(row['hospital'], 'candidate hospital')
        travel = _integer(row['travel_minutes'], 'travel estimate')
        if hid in pending or any(hid == other[1] for other in candidates):
            raise ValueError('open or duplicate candidate')
        candidates.append((travel, hid))
    send, reason = [], 'no_refill'
    if cfg['refill'] == 'none':
        reason = 'disabled'
    elif snapshot['request_t'] > t:
        reason = 'patient_not_started'
    elif snapshot['state'] != 'SEARCH':
        reason = 'not_searching'
    elif snapshot['has_valid_offer'] or snapshot['ready']:
        reason = 'offer_or_G2_decision_pending'
    elif snapshot['retry_at'] is not None and snapshot['retry_at'] > t:
        reason = 'retry_cooldown'
    elif not snapshot['received_this_tick']:
        reason = 'no_received_reply_this_tick'
    elif not pending:
        reason = 'no_partial_wave_to_refill'
    elif len(pending) >= maximum:
        reason = 'concurrency_full'
    elif not candidates:
        reason = 'no_unqueried_candidate'
    else:
        count = min(maximum-len(pending), 1 if cfg['refill'] == 'one' else maximum)
        send = [hid for _, hid in sorted(candidates)[:count]]
        reason = 'refill_after_received_result'
    return dict(policy_id=cfg['id'], send=send, reason=reason,
                open_requests=len(pending), max_k=maximum)


def decide_retry(spec, snapshot):
    """Choose a positive cooldown from completed same-patient observations.

    History = ceiling(median(last six observed elapsed response times within
    thirty minutes)), clamped to [1,min(3,baseline)]. Timeouts contribute their
    actually observed elapsed time. This is a response-cadence rule, not an
    estimate of when clinical resources will become available.
    """
    cfg = normalize_spec(spec)
    _header(snapshot, RETRY_KEYS)
    t, pid = snapshot['t'], snapshot['patient']
    baseline = _integer(snapshot['baseline_wait'], 'baseline wait', 1)
    if type(snapshot['observations']) is not list:
        raise ValueError('completed observation list required')
    recent, seen = [], set()
    for row in snapshot['observations']:
        if type(row) is not dict or set(row) != {'patient', 'hospital', 'submitted_t', 'observed_t', 'outcome'}:
            raise ValueError('received observation whitelist differs')
        if row['patient'] != pid:
            raise ValueError('retry history must belong to the current patient')
        hid = _identifier(row['hospital'], 'observed hospital')
        sent = _integer(row['submitted_t'], 'observed submission')
        at = _integer(row['observed_t'], 'observed completion')
        if sent > at or at > t:
            raise ValueError('future or reversed observation')
        key = (pid, hid, sent)
        if key in seen:
            raise ValueError('duplicate completed inquiry observation')
        seen.add(key)
        if row['outcome'] not in ('accept', 'decline', 'timeout'):
            raise ValueError('unknown received outcome')
        if t-HISTORY_WINDOW < at <= t:
            recent.append((at, sent, hid, at-sent))
    recent = sorted(recent)[-HISTORY_LIMIT:]
    empirical = median([row[3] for row in recent]) if recent else None
    wait, reason = baseline, 'baseline_cooldown'
    if cfg['retry'] == 'fixed_2':
        wait, reason = min(2, baseline), 'bounded_fixed_two'
    elif cfg['retry'] == 'history' and empirical is not None:
        wait = min(baseline, 3, max(1, math.ceil(empirical)))
        reason = 'observed_response_cadence'
    elif cfg['retry'] == 'history':
        reason = 'no_history_keep_baseline'
    return dict(policy_id=cfg['id'], wait_minutes=wait, retry_at=t+wait,
                reason=reason, samples=len(recent), observed_median_elapsed=empirical,
                history_window=HISTORY_WINDOW, history_limit=HISTORY_LIMIT,
                acceptance_or_resource_release_prediction=False)
