"""One factor: remaining visible FIFO work conditioned on observed silence.

The residual policy's new-inquiry queue estimate is replaced by a rounded
prefix-completion model. Its existing-pending CDF, planner, timing, learning,
contact limits and immediate confirmation remain the frozen residual policy.
No worker truth, latent service, future reply or future resource enters here.
"""
import hashlib
import math
from pathlib import Path

from server import inquiry_policies
from . import residual_policy as original
from .residual_policy import (Beliefs, expected_cost, _lognorm_cdf, _masses,
    _no_accept_by, _shift, _log_normal_survival, head_expected_remaining)

NAME = 'eat_fifo_l15_f'
BASE_POLICY = 'eat_residual_l15_f'
FIFO_RULE = 'silence_conditioned_visible_fifo_prefix'
BASE_SOURCE_SHA256 = '8cf391ab5062de374d3c843edd3f6b1be98ac281df9217acc0538cbd98b838ef'
if hashlib.sha256(Path(original.__file__).read_bytes()).hexdigest() != BASE_SOURCE_SHA256:
    raise ValueError('frozen residual queue foundation source differs')


def spec(name=NAME):
    if name != NAME:
        raise ValueError('the fixed visible-FIFO candidate is ' + NAME)
    value = original.spec(BASE_POLICY)
    value.update(name=NAME, label='EAT l15 f, silence-conditioned visible FIFO work')
    return value


class _FIFONumericalError(ArithmeticError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def _positive_exp(value):
    result = math.exp(value)
    if result == 0.:
        raise _FIFONumericalError('numerical_underflow')
    if not math.isfinite(result):
        raise _FIFONumericalError('nonfinite_work')
    return result


def _product(*values):
    result = 1.
    for value in values:
        previous = result
        result *= value
        if previous > 0. and value > 0. and result == 0.:
            raise _FIFONumericalError('numerical_underflow')
        if not math.isfinite(result):
            raise _FIFONumericalError('nonfinite_work')
    return result


def _survival_stoploss(age, mu, sigma, mean):
    """Keep the complete analytic service tail after the observation instant."""
    if age == 0.:
        return 1., mean
    logage = math.log(age)
    z = (logage-mu)/sigma
    logtail = _log_normal_survival(z)
    left = mu+sigma*sigma/2. + _log_normal_survival(z-sigma)
    right = logage+logtail
    difference = right-left
    if difference >= 0.:
        raise _FIFONumericalError('numerical_underflow')
    logstoploss = left+math.log(-math.expm1(difference))
    return _positive_exp(logtail), _positive_exp(logstoploss)


def _service_interval(lower, upper, mu, sigma):
    """Lognormal interval probability, using the better-conditioned tail."""
    if upper <= lower or upper <= 0.:
        return 0.
    upper_z = (math.log(upper)-mu)/sigma
    if upper_z <= 0.:
        high = _log_normal_survival(-upper_z)
        low = (_log_normal_survival(-(math.log(lower)-mu)/sigma)
            if lower > 0. else -math.inf)
    else:
        high = (_log_normal_survival((math.log(lower)-mu)/sigma)
            if lower > 0. else 0.)
        low = _log_normal_survival(upper_z)
    if low == -math.inf:
        return _positive_exp(high)
    difference = low-high
    if difference >= 0.:
        raise _FIFONumericalError('numerical_underflow')
    return _positive_exp(high+math.log(-math.expm1(difference)))


def _prefix_workload(mu, sigma, reply_probability, head_start, submitted_at, now,
                     grid_seconds=5, max_grid_cells=60):
    """Rounded FIFO path expectation N/D with q per completed silent job.

    A prefix finishing between two grid endpoints is rounded up to the latter.
    The last endpoint is exactly now, including a shorter final interval.
    At every possible next start, the full active survival and stop-loss tail
    remain analytic; unfinished paths are never discarded at the grid edge.
    """
    values = (mu, sigma, reply_probability, head_start, now)
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in values) \
            or sigma <= 0 or not 0 <= reply_probability <= 1 or not 0 <= head_start <= now \
            or type(grid_seconds) is not int or grid_seconds <= 0 \
            or type(max_grid_cells) is not int or max_grid_cells <= 0:
        raise ValueError('finite distribution, current clock and positive integer grid required')
    submitted = tuple(submitted_at)
    if not submitted or any(type(v) not in (int, float) or not math.isfinite(v)
            or not 0 <= v <= now for v in submitted) \
            or list(submitted) != sorted(submitted) or submitted[0] > head_start:
        raise ValueError('a nonempty ordered visible submission prefix is required')
    mean = math.exp(mu+sigma*sigma/2.)
    if not math.isfinite(mean) or mean == 0.:
        raise ValueError('finite positive service mean required')
    age = now-head_start
    step = max(grid_seconds, math.ceil(age/max_grid_cells))
    cells = math.ceil(age/step)
    offsets = [min(age, cell*step) for cell in range(cells+1)]
    # With no time elapsed, the sole prefix state is still its initial start.
    states = {0.:1.}
    q = 1.-reply_probability
    layers = []
    den_terms, num_terms = [], []
    start_cache = {}
    for position, submitted_time in enumerate(submitted):
        next_states = {}
        active_den, active_num = [], []
        for completion, weight in states.items():
            start = max(completion, submitted_time-head_start)
            if start not in start_cache:
                tail, stoploss = _survival_stoploss(age-start, mu, sigma, mean)
                previous = start
                transitions = []
                for endpoint in offsets:
                    if endpoint <= start:
                        continue
                    probability = _service_interval(previous-start, endpoint-start, mu, sigma)
                    transitions.append((endpoint, probability))
                    previous = endpoint
                start_cache[start] = tail, stoploss, transitions
            tail, stoploss, transitions = start_cache[start]
            active_den.append(_product(weight, tail))
            work = ((len(submitted)-position)*mean if start == age else
                stoploss+(len(submitted)-position-1)*mean*tail)
            active_num.append(_product(weight, work))
            for endpoint, probability in transitions:
                transition = _product(weight, q, probability)
                if transition > 0.:
                    next_states[endpoint] = next_states.get(endpoint, 0.)+transition
        denominator = math.fsum(active_den)
        numerator = math.fsum(active_num)
        den_terms.append(denominator); num_terms.append(numerator)
        layers.append(dict(position=position, entry_state_count=len(states),
            entry_weight=math.fsum(states.values()), active_denominator=denominator,
            active_numerator=numerator, completed_weight=math.fsum(next_states.values())))
        states = next_states
    # All visible jobs can have completed without replying. This mass belongs
    # to the silence denominator and contributes zero remaining work.
    completed = math.fsum(states.values())
    denominator = math.fsum([*den_terms, completed])
    if reply_probability == 0.:
        # No-reply is certain in this boundary case, regardless of service path.
        denominator = 1.
    numerator = math.fsum(num_terms)
    if not 0. < denominator <= 1. or not math.isfinite(denominator):
        raise _FIFONumericalError('probability_out_of_range')
    value = numerator/denominator
    if not math.isfinite(numerator) or numerator < 0. or not math.isfinite(value) or value < 0.:
        raise _FIFONumericalError('nonfinite_work')
    return dict(wait_seconds=value, silence_probability=denominator,
        remaining_numerator=numerator, grid_step_seconds=step, grid_offsets_seconds=offsets,
        prefix_layers=layers)


def _queue_wait_estimate(hospital, now, rows, history, beliefs, workers):
    """Use only the same normalized visible queue, received replies and clock."""
    if type(workers) not in (int, float) or not math.isfinite(workers) or workers <= 0:
        raise ValueError('finite positive assumed worker count required')
    ordered = sorted(rows, key=lambda row:(row['submitted_at'],type(row['patient']).__name__,str(row['patient'])))
    wait, record = original._queue_wait_estimate(hospital, now, ordered, history, beliefs, workers)
    config = original.spec(BASE_POLICY)
    grid = config['config']['grid_seconds']
    max_cells = config['deadline_seconds']//grid
    record.update(fifo_order=[dict(row) for row in ordered], method='residual_analytic_exact',
        fallback_reason=None, grid_base_seconds=grid, grid_max_cells=max_cells,
        grid_step_seconds=None, grid_offsets_seconds=[], prefix_layers=[],
        silence_probability=None, remaining_numerator=None, conditional_work_seconds=wait*workers)
    if not ordered:reason='empty_queue'
    elif workers != 1:reason='worker_assumption'
    elif any(row['hospital']==hospital and row['outcome']!='timeout'
            and any(row['submitted_at']==pending['submitted_at'] for pending in ordered) for row in history):
        reason='visible_reply'
    elif len(ordered) == 1:reason='single_job_exact'
    elif record['age_seconds'] == 0:reason='fresh_head_exact'
    elif record['reply_probability'] == 1.:reason='fully_responsive_exact'
    else:reason=None
    if reason is not None:
        record['fallback_reason']=reason
        if reason in ('worker_assumption','visible_reply'):
            record['method']='residual_analytic_fallback'
        return wait,record
    try:
        estimate = _prefix_workload(record['service_mu'], record['service_sigma'], record['reply_probability'],
            record['estimated_start_at'], [row['submitted_at'] for row in ordered], now, grid, max_cells)
    except _FIFONumericalError as error:
        record.update(method='residual_analytic_fallback', fallback_reason=error.reason)
        return wait,record
    except (OverflowError, ZeroDivisionError):
        record.update(method='residual_analytic_fallback', fallback_reason='nonfinite_work')
        return wait,record
    wait = estimate.pop('wait_seconds')
    record.update(estimate, method='weighted_prefix_dp', estimated_wait_seconds=wait,
        conditional_work_seconds=wait)
    return wait,record


def decide(name, *, now, candidates, observations=(), pending=(), patient_pending=(), max_new,
           last_contact_at=None):
    """서버 정책과 같은 인수로 문의할 병원과 근거를 반환한다. 입력은 변경하지 않는다."""
    policy = spec(name)
    config = policy['config']
    if type(now) is not int or now < 0:
        raise ValueError('now must be an integer >= 0')
    if type(max_new) is not int or max_new < 0:
        raise ValueError('max_new must be an integer >= 0')
    ranked = inquiry_policies._candidates(candidates)
    window = config['history_window_seconds']
    history = inquiry_policies._observations(observations, now, window)
    unresolved = inquiry_policies._pending(pending, now)
    own = inquiry_policies._pending(patient_pending, now, own=True)
    keys = {(r['hospital'], r['patient'], r['submitted_at']) for r in unresolved}
    if any((r['hospital'], r['patient'], r['submitted_at']) not in keys for r in own):
        raise ValueError('patient_pending must be a subset of pending')
    if len({r['patient'] for r in own}) > 1:
        raise ValueError('patient_pending must describe one patient')
    if last_contact_at is not None and (type(last_contact_at) not in (int, float)
                                        or not math.isfinite(last_contact_at) or last_contact_at > now):
        last_contact_at = None

    beliefs = Beliefs(history, now, config)
    step = config['grid_seconds']
    known_starts = [r['submitted_at'] for r in own]
    if last_contact_at is not None:
        known_starts.append(last_contact_at)
    elapsed = now - min(known_starts) if known_starts else 0
    horizon = max(step * 3, policy['deadline_seconds'] - elapsed)
    cells = max(3, int(math.ceil(horizon / step)))
    shifts = [None] + sorted({int(round(d / step)) for d in config['plan_offsets_seconds']
                              if d < horizon})
    failure = config['failure_penalty_seconds']
    workers = config['assumed_workers']

    queue = {}
    for row in unresolved:
        queue.setdefault(row['hospital'], []).append(row)
    last_reply = {}
    for row in history:
        if row['outcome'] != 'timeout':
            last_reply.setdefault(row['hospital'], []).append(row)

    def pending_item(row):
        h, submitted = row['hospital'], row['submitted_at']
        mu, sigma = beliefs.mu(h), beliefs.sigma
        r, a = beliefs.reply(h), beliefs.accept(h)
        # 같은 초에 들어간 다른 문의의 FIFO 순서는 정책이 모르므로 절반을 앞선 것으로 본다.
        ahead = sum(1.0 if q['submitted_at'] < submitted else 0.5
                    for q in queue.get(h, ())
                    if q['submitted_at'] <= submitted and q['patient'] != row['patient'])
        if ahead > 0:
            wait = ahead * beliefs.mean_service(h) / workers
            cdf = lambda x: _lognorm_cdf(x - wait, mu, sigma)
            total = r * a
        else:
            freed = [o['observed_at'] for o in last_reply.get(h, ()) if o['submitted_at'] <= submitted]
            started = max([submitted, *freed])
            spent = max(0.0, now - started)
            f0 = _lognorm_cdf(spent, mu, sigma)
            alive = max(1e-9, 1.0 - f0)
            reply_left = r * alive / max(1e-9, 1.0 - r * f0)
            cdf = lambda x: (_lognorm_cdf(spent + x, mu, sigma) - f0) / alive
            total = reply_left * a
        return row['travel_seconds'], _masses(cdf, total, cells, step)

    def new_item(row):
        h = row['hospital']
        mu, sigma = beliefs.mu(h), beliefs.sigma
        load = len(queue.get(h, ()))
        wait, residual_queue_records[h] = _queue_wait_estimate(
            h, now, queue.get(h, ()), history, beliefs, workers)
        cdf = lambda x: _lognorm_cdf(x - wait, mu, sigma)
        return row['travel_seconds'], _masses(cdf, beliefs.reply(h) * beliefs.accept(h), cells, step)

    def inquiry_cost(row):
        lam = config['lambda_seconds']
        if config['congestion_aware']:
            lam *= 1 + len(queue.get(row['hospital'], ()))
        return lam

    # 관측 제한(180초)을 넘긴 자기 문의도 늦은 수용 가능성을 사후확률로 계산에 남긴다.
    # 동시 문의 수에서는 V2·V3와 같이 180초 이내 문의만 센다.
    current = [pending_item(row) for row in own]
    stale = sum(1 for row in own if now - row['submitted_at'] > config['observation_timeout_seconds'])
    pool = ranked[:min(config['max_candidates_evaluated'], max_new)]
    residual_queue_records = {}
    items = {row['hospital']: new_item(row) for row in pool}
    lams = {row['hospital']: inquiry_cost(row) for row in pool}
    room = min(max_new, config['max_concurrent'] - (len(own) - stale))

    def plan_cost(plan):
        """plan: 병원 -> 지금부터 몇 칸 뒤 문의(0=지금). 문의비용은 그때까지 수용이 없을 때만 낸다."""
        chosen = [(c, h) for h, c in plan.items() if c is not None]
        all_items = current + [_shift(items[h], c) if c else items[h] for c, h in chosen]
        value = expected_cost(all_items, cells, step, horizon, failure)
        for c, h in chosen:
            value += lams[h] * (_no_accept_by(all_items, c) if c else 1.0)
        return value

    base = expected_cost(current, cells, step, horizon, failure)
    plan = {row['hospital']: None for row in pool}
    best_cost = plan_cost(plan)
    for _ in range(config['plan_passes']):
        improved = False
        for row in pool:
            h = row['hospital']
            for offset in shifts:
                if offset == plan[h]:
                    continue
                if offset == 0 and plan[h] != 0 and sum(c == 0 for c in plan.values()) >= room:
                    continue
                trial = dict(plan)
                trial[h] = offset
                value = plan_cost(trial)
                if value < best_cost - 1e-9:
                    plan, best_cost, improved = trial, value, True
        if not improved:
            break
    send = [row['hospital'] for row in pool if plan[row['hospital']] == 0]
    # 안전 규칙: 180초 이내 대기 문의가 하나도 없는데 지금 보낼 곳도 없으면, 계획에서 가장
    # 비용이 낮아지는 한 곳을 지금 문의한다. 수용 가능성이 낮다고 추정돼도 멈추지 않는다.
    if len(own) == stale and not send and pool and room > 0:
        forced = min(pool, key=lambda row: (plan_cost({**plan, row['hospital']: 0}), row['travel_seconds']))
        plan = {**plan, forced['hospital']: 0}
        best_cost = plan_cost(plan)
        send = [forced['hospital']]
    scored = [dict(hospital=row['hospital'],
                   planned_offset_seconds=None if plan[row['hospital']] is None else plan[row['hospital']] * step,
                   inquiry_cost=lams[row['hospital']],
                   reply_probability=round(beliefs.reply(row['hospital']), 4),
                   accept_probability=round(beliefs.accept(row['hospital']), 4),
                   queue_length=len(queue.get(row['hospital'], ())),
                   travel_seconds=row['travel_seconds']) for row in pool]

    if max_new == 0:
        reason = 'contact_budget'
    elif send:
        reason = 'eat_initial' if not own else 'eat_hedge'
    elif not ranked:
        reason = 'no_candidates'
    elif room <= 0:
        reason = 'eat_concurrency_cap'
    else:
        reason = 'eat_wait'
    evidence = dict(evaluated_at=now, policy=name, horizon_seconds=horizon,
                    lambda_seconds=config['lambda_seconds'],
                    congestion_aware=config['congestion_aware'],
                    expected_cost_without_new=round(base, 3),
                    planned_expected_cost=round(best_cost, 3),
                    reply_probability_global=round(beliefs.reply_global, 4),
                    accept_probability_global=round(beliefs.accept_global, 4),
                    service_median_seconds=round(math.exp(beliefs.mu_global), 2),
                    history_used_count=len(history), own_waiting_count=len(own) - stale,
                    own_observation_timed_out_count=stale,
                    unresolved_inquiry_count=len(unresolved),
                    candidate_plan=scored, sent_hospitals=list(send))
    evidence['fifo_queue'] = dict(schema_version=1, rule=FIFO_RULE,
        hospitals=[residual_queue_records[row['hospital']] for row in pool])
    return dict(send=send, reason=reason, evidence=evidence,
                next_review_at=now + policy['review_seconds'])
