"""EAT-C: 병원의 '확인 중'(checking) 회신을 해석하는 EAT 변형(4차 2단계).

서버 관측 장부는 '확인 중'을 그 문의의 첫 회신('reply')으로 기록한다. 1차 EAT는 이것을 결정 회신처럼
처리시간 표본과 병원 업무 종료 시각으로 쓰므로, 확인 중이 도입되면 처리시간을 짧게 오인할 수 있다.
EAT-C는 1차 잠금 정책 `eat_policy.decide`(eat_l15_f)의 계산을 그대로 옮기고 다음만 바꾼다.

1. 확인 중 관측은 '병원이 관여했다'는 회신 증거로만 쓰고, 처리시간 표본·업무 종료 시각에는 쓰지 않는다.
2. 누름 비율 ρ를 첫 회신 중 확인 중의 비율로 배운다(출발값 0.6, 가상 표본 2, 시간 감쇠 동일).
3. 자기 미해결 문의 j의 '결국 회신할 확률'을 베이즈로 갱신한다(병원이 검토를 시작했다고 볼 때).
   - 확인 중을 받았으면: P(관여) = rρ(1−F) / (rρ(1−F) + (1−r)κρ). 검토 시작은 확인 중 5초 전으로 본다.
   - 아직 못 받았으면(m = min(경과/10초, 1)): P(관여) = r(1−F)(1−ρm) / (r(1−F)(1−ρm) + (1−r)(1−κρm)).
   여기서 r은 회신률, F는 처리시간 분포, κ는 무응답 병원이 확인 중을 누르는 상대 비율(가정값)이다.
   대기열 앞에 다른 문의가 있으면 아직 시작 전이므로 확인 중 여부로 갱신하지 않는다.
4. 서버 장부는 시도마다 첫 회신만 기록하므로, 확인 중을 누른 문의의 최종 결정은 장부 확장
   (`decision_ledger`, 서버 개선 제안)의 'checked_accept|checked_decline' 행으로 받아 처리시간·수용을 배운다.
ρ=0이면 1차 EAT와 계산이 같다(test로 확인). 정책은 환경의 실제 누름 여부·시각을 알지 못한다.
"""
import math
import re

from server import inquiry_policies

from . import eat_policy

PREFIX = 'eatc_'
KAPPA_GRID = (5, 20)          # κ = 0.05, 0.20
ACK_PRIOR_RATE = 0.6
ACK_PRIOR_WEIGHT = 2.0
ACK_WINDOW_SECONDS = 10
ACK_START_OFFSET = 5


def variant_name(kappa_percent):
    return f'{PREFIX}k{kappa_percent}'


def variant_grid():
    return tuple(variant_name(k) for k in KAPPA_GRID)


def spec(name):
    match = re.fullmatch(PREFIX + r'k(\d+)(_r0)?', name) if isinstance(name, str) else None
    if match is None:
        raise ValueError('EAT-C policy name must look like eatc_k5')
    base = eat_policy.spec('eat_l15_f')
    base['config'].update(silent_ack_relative=int(match.group(1)) / 100.0, ack_prior_rate=ACK_PRIOR_RATE,
                          ack_prior_weight=ACK_PRIOR_WEIGHT, ack_window_seconds=ACK_WINDOW_SECONDS,
                          ack_start_offset_seconds=ACK_START_OFFSET,
                          ack_rate_override=0.0 if match.group(2) else None)
    base.update(name=name, label=f'EAT-C (확인 중 해석, 무응답 누름 상대비 {int(match.group(1))}%)')
    return base


class AckBeliefs(eat_policy.Beliefs):
    """결정 회신으로만 처리시간·수용을 배우고, 확인 중은 관여 증거와 누름 비율 추정에만 쓴다.

    checked는 장부 확장(decision_ledger)의 '확인 중 뒤 최종 결정' 행이다. 처리시간 표본은
    결정 시각 − (확인 중 시각 − 시작 보정)으로, 수용·불가는 그대로 배운다. 없으면 무시한다.
    """

    def __init__(self, observations, now, config, checked=()):
        decided = [row for row in observations if row['outcome'] != 'reply']
        super().__init__(decided, now, config)
        tau, priors = config['decay_seconds'], config['priors']
        acked = first_decisions = 0.0
        for row in observations:
            weight = math.exp(-max(0.0, now - row['observed_at']) / tau)
            if row['outcome'] == 'reply':
                counts = self._replies.setdefault(row['hospital'], [0.0, 0.0])
                counts[0] += weight
                counts[1] += weight
                acked += weight
            elif row['outcome'] in ('accept', 'decline'):
                first_decisions += weight
        if checked:
            offset = config['ack_start_offset_seconds']
            for row in checked:
                weight = math.exp(-max(0.0, now - row['observed_at']) / tau)
                acc = self._accepts.setdefault(row['hospital'], [0.0, 0.0])
                acc[1] += weight
                acc[0] += weight * (row['outcome'] == 'checked_accept')
                service = max(1.0, row['observed_at'] - (row['checked_at'] - offset))
                self._services.setdefault(row['hospital'], []).append((weight, math.log(service)))
            total_acc = sum(v[0] for v in self._accepts.values())
            total_dec = sum(v[1] for v in self._accepts.values())
            self.accept_global = eat_policy._shrink(total_acc, total_dec, priors['accept_probability'],
                                                    priors['accept_prior_weight'])
            mu0 = math.log(priors['service_median_seconds'])
            n0, s0 = priors['service_prior_weight'], priors['service_log_sd']
            logs = [pair for values in self._services.values() for pair in values]
            wsum = sum(w for w, _ in logs)
            self.mu_global = (mu0 * n0 + sum(w * x for w, x in logs)) / (n0 + wsum)
            var = (s0 * s0 * n0 + sum(w * (x - self.mu_global) ** 2 for w, x in logs)) / (n0 + wsum)
            self.sigma = max(0.25, math.sqrt(var))
        total_replies = sum(v[0] for v in self._replies.values())
        total_resolved = sum(v[1] for v in self._replies.values())
        self.reply_global = eat_policy._shrink(total_replies, total_resolved, priors['reply_probability'],
                                               priors['reply_prior_weight'])
        override = config.get('ack_rate_override')
        self.ack_rate = override if override is not None else eat_policy._shrink(
            acked, acked + first_decisions, config['ack_prior_rate'], config['ack_prior_weight'])


def _checked(observations, now, window):
    """장부 확장의 '확인 중 뒤 최종 결정' 행만 검증해 고른다. 미래·손상 행은 버린다."""
    rows = []
    for row in observations:
        if not isinstance(row, dict) or row.get('outcome') not in ('checked_accept', 'checked_decline'):
            continue
        values = [row.get('submitted_at'), row.get('checked_at'), row.get('observed_at')]
        if (not all(type(v) in (int, float) and math.isfinite(v) for v in values) or not isinstance(row.get('hospital'), str)
                or not values[0] <= values[1] <= values[2] <= now or values[2] <= now - window):
            continue
        rows.append(row)
    return rows


def _own_acks(observations, own, now):
    keys = {(r['hospital'], r['patient'], r['submitted_at']) for r in own}
    acks = {}
    for row in observations:
        if not isinstance(row, dict) or row.get('outcome') != 'reply':
            continue
        key = (row.get('hospital'), row.get('patient'), row.get('submitted_at'))
        observed = row.get('observed_at')
        if key in keys and type(observed) in (int, float) and math.isfinite(observed) and observed <= now:
            acks[key] = observed
    return acks


def decide(name, *, now, candidates, observations=(), pending=(), patient_pending=(), max_new,
           last_contact_at=None):
    """eat_policy.decide와 같은 계산. 확인 중의 의미만 다르게 해석한다."""
    policy = spec(name)
    config = policy['config']
    if type(now) is not int or now < 0:
        raise ValueError('now must be an integer >= 0')
    if type(max_new) is not int or max_new < 0:
        raise ValueError('max_new must be an integer >= 0')
    raw_observations = list(observations)
    ranked = inquiry_policies._candidates(candidates)
    window = config['history_window_seconds']
    history = inquiry_policies._observations(raw_observations, now, window)
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

    beliefs = AckBeliefs(history, now, config, _checked(raw_observations, now, window))
    acks = _own_acks(raw_observations, own, now)
    rho = beliefs.ack_rate
    rho_silent = config['silent_ack_relative'] * rho
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
    _lognorm_cdf, _masses = eat_policy._lognorm_cdf, eat_policy._masses

    queue = {}
    for row in unresolved:
        queue.setdefault(row['hospital'], []).append(row)
    last_reply = {}
    for row in history:
        if row['outcome'] not in ('timeout', 'reply'):     # 확인 중은 업무 종료가 아니다
            last_reply.setdefault(row['hospital'], []).append(row)

    def pending_item(row):
        h, submitted = row['hospital'], row['submitted_at']
        mu, sigma = beliefs.mu(h), beliefs.sigma
        r, a = beliefs.reply(h), beliefs.accept(h)
        ahead = sum(1.0 if q['submitted_at'] < submitted else 0.5
                    for q in queue.get(h, ())
                    if q['submitted_at'] <= submitted and q['patient'] != row['patient'])
        ack_at = acks.get((h, row['patient'], submitted))
        if ack_at is None and ahead > 0:
            wait = ahead * beliefs.mean_service(h) / workers
            cdf = lambda x: _lognorm_cdf(x - wait, mu, sigma)
            total = r * a
        else:
            if ack_at is not None:
                spent = max(0.0, now - ack_at + config['ack_start_offset_seconds'])
                f0 = _lognorm_cdf(spent, mu, sigma)
                engaged = r * rho * (1.0 - f0)
                silent = (1.0 - r) * rho_silent
            else:
                freed = [o['observed_at'] for o in last_reply.get(h, ()) if o['submitted_at'] <= submitted]
                started = max([submitted, *freed])
                spent = max(0.0, now - started)
                f0 = _lognorm_cdf(spent, mu, sigma)
                m = min(spent / config['ack_window_seconds'], 1.0)
                engaged = r * (1.0 - f0) * (1.0 - rho * m)
                silent = (1.0 - r) * (1.0 - rho_silent * m)
            alive = max(1e-9, 1.0 - f0)
            posterior = engaged / max(1e-12, engaged + silent)
            cdf = (lambda s, f, al: (lambda x: (_lognorm_cdf(s + x, mu, sigma) - f) / al))(spent, f0, alive)
            total = posterior * a
        return row['travel_seconds'], _masses(cdf, total, cells, step)

    def new_item(row):
        h = row['hospital']
        mu, sigma = beliefs.mu(h), beliefs.sigma
        load = len(queue.get(h, ()))
        wait = load * beliefs.mean_service(h) / workers
        cdf = lambda x: _lognorm_cdf(x - wait, mu, sigma)
        return row['travel_seconds'], _masses(cdf, beliefs.reply(h) * beliefs.accept(h), cells, step)

    def inquiry_cost(row):
        lam = config['lambda_seconds']
        if config['congestion_aware']:
            lam *= 1 + len(queue.get(row['hospital'], ()))
        return lam

    current = [pending_item(row) for row in own]
    stale = sum(1 for row in own if now - row['submitted_at'] > config['observation_timeout_seconds'])
    pool = ranked[:min(config['max_candidates_evaluated'], max_new)]
    items = {row['hospital']: new_item(row) for row in pool}
    lams = {row['hospital']: inquiry_cost(row) for row in pool}
    room = min(max_new, config['max_concurrent'] - (len(own) - stale))

    def plan_cost(plan):
        chosen = [(c, h) for h, c in plan.items() if c is not None]
        all_items = current + [eat_policy._shift(items[h], c) if c else items[h] for c, h in chosen]
        value = eat_policy.expected_cost(all_items, cells, step, horizon, failure)
        for c, h in chosen:
            value += lams[h] * (eat_policy._no_accept_by(all_items, c) if c else 1.0)
        return value

    base = eat_policy.expected_cost(current, cells, step, horizon, failure)
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
                    checking=dict(ack_rate=round(rho, 4), own_acknowledged=len(acks)))
    return dict(send=send, reason=reason, evidence=evidence,
                next_review_at=now + policy['review_seconds'])


def install():
    from . import decision_ledger, harness
    harness.install(['eat_l15_f'])
    decision_ledger.install()
    for name in variant_grid():
        inquiry_policies._POLICIES.setdefault(name, spec(name))
    current = inquiry_policies.decide
    if not getattr(current, '_eat_ack', False):
        def wrapped(name, **kwargs):
            if name.startswith(PREFIX):
                return decide(name, **kwargs)
            return current(name, **kwargs)
        wrapped._eat_ack = True
        inquiry_policies.decide = wrapped


def uninstall():
    from . import decision_ledger, harness
    for name in variant_grid():
        inquiry_policies._POLICIES.pop(name, None)
    decision_ledger.uninstall()
    harness.uninstall()
