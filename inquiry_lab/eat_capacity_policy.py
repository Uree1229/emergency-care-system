"""EAT-K: 병원의 현재 문의 처리 인원을 아는 EAT 변형(13차).

1차 EAT는 모든 병원의 문의 처리 인원을 1명으로 보고 대기 시간을 추정한다(assumed_workers=1).
실제로는 25%의 병원이 2명이고, 8차 보조 담당이 합류하면 잠시 2명이 된다. 8·9·12차에서 처리 용량이
늘수록 EAT가 대기를 과대평가해 덜 묻거나 먼 병원으로 보내는 신호가 보였다.
EAT-K는 계산은 1차 EAT와 같고, 대기 시간 = 앞선 문의 수 × 평균 처리시간 ÷ (그 병원의 처리 인원)으로만 바꾼다.

  eatk_{mode}_k{K}_m{M}
  - mode=base: 병원이 평소 처리 인원만 알린다(보조 담당 합류는 모름).
  - mode=live: 병원 화면에 접속한 검토자 수처럼 지금 처리 인원을 안다(보조 담당 합류 포함).
  - K·M: 계획에 넣는 가까운 병원 수, 한 환자의 동시 문의 수(1차 EAT는 k6_m4, 12차 EAT-W는 k10_m4).

처리 인원 표는 실험 실행기가 판단 직전에 `set_capacity`로 넣는다. 표가 없으면 1명으로 보아
같은 K·M의 EAT와 결과가 같다(test_inquiry_lab_aware). 아래 decide는 1차 잠금 `eat_policy.decide`의
본문을 옮기고 대기 시간 두 곳과 근거 한 칸만 바꾼 것이다. 1차 파일은 수정하지 않는다.
"""
import math
import re

from server import inquiry_policies

from . import eat_policy
from .eat_policy import Beliefs, _lognorm_cdf, _masses, _no_accept_by, _shift, expected_cost

PREFIX = 'eatk_'
MODES = ('base', 'live')
_NAME = re.compile(PREFIX + r'(base|live)_k(\d+)_m(\d+)')
_CAPACITY = None


def set_capacity(capacity):
    """{병원: {'base': 평소 인원, 'live': 지금 인원}}. None이면 처리 인원을 모른다(1명으로 본다)."""
    global _CAPACITY
    _CAPACITY = capacity


def variant_name(mode, k, m):
    return f'{PREFIX}{mode}_k{k}_m{m}'


def spec(name):
    match = _NAME.fullmatch(name) if isinstance(name, str) else None
    if match is None or int(match.group(2)) < 1 or int(match.group(3)) < 1:
        raise ValueError('EAT-K policy name must look like eatk_live_k10_m4')
    mode, k, m = match.group(1), int(match.group(2)), int(match.group(3))
    policy = eat_policy.spec('eat_l15_f')
    policy['config'].update(max_candidates_evaluated=k, max_concurrent=m, capacity_mode=mode)
    policy.update(name=name, label=f'EAT-K ({"지금" if mode == "live" else "평소"} 처리 인원, 가까운 병원 {k}곳, 동시 {m}곳)')
    return policy


def _workers(config):
    default, mode, table = config['assumed_workers'], config.get('capacity_mode'), _CAPACITY

    def workers_of(hospital):
        if mode is None or table is None or hospital not in table:
            return default
        return max(1, table[hospital][mode])
    return workers_of


def decide(name, *, now, candidates, observations=(), pending=(), patient_pending=(), max_new,
           last_contact_at=None):
    """eat_policy.decide와 같은 계산. 대기 시간을 병원별 현재 처리 인원으로 나눈다."""
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
    workers_of = _workers(config)

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
            wait = ahead * beliefs.mean_service(h) / workers_of(h)
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
        wait = load * beliefs.mean_service(h) / workers_of(h)
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
                    candidate_plan=scored, sent_hospitals=list(send),
                    capacity_used={row['hospital']: workers_of(row['hospital']) for row in pool})
    return dict(send=send, reason=reason, evidence=evidence,
                next_review_at=now + policy['review_seconds'])


def install(names):
    from . import harness
    harness.install(['eat_l15_f'])
    for name in names:
        inquiry_policies._POLICIES.setdefault(name, spec(name))
    current = inquiry_policies.decide
    if not getattr(current, '_eat_capacity', False):
        def wrapped(name, **kwargs):
            if name.startswith(PREFIX):
                return decide(name, **kwargs)
            return current(name, **kwargs)
        wrapped._eat_capacity = True
        inquiry_policies.decide = wrapped


def uninstall():
    from . import harness
    for name in [n for n in inquiry_policies._POLICIES if n.startswith(PREFIX)]:
        inquiry_policies._POLICIES.pop(name, None)
    set_capacity(None)
    harness.uninstall()
