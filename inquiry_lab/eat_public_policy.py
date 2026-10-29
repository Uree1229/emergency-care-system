"""EAT-P: 공개 가용병상 신호로 병원별 수용 사전확률을 정하는 EAT 변형(3차 실험).

1차 잠금 정책 `eat_policy.decide`(eat_l15_f)의 계산을 그대로 옮겼다. 바뀐 것은 하나다.
병원별 수용확률을 관측으로 갱신할 때의 출발점(축소 목표)이 전 병원 공통값 대신

    목표_h = w·logistic(β0 + β1·min(병상_h, 15)) + (1−w)·공통 수용 추정,  w = exp(−보고 경과/τ)

가 되고, 신호 강도만큼(4·w) 가상 표본을 더 준다. β0·β1은 개발 데이터로 학습해 잠근 보정식,
τ는 개발에서 고른 신호 감쇠 시간이다. 신호가 없는 병원은 원래 EAT와 계산이 같다(test로 확인).

신호는 실험 실행기가 시나리오마다 set_context로 넣는다. 서버 연동 시에는 후보 조회 결과에
공공 관측(가용병상·입력시각)을 붙여 전달하면 된다. 정책은 병상 수와 보고 시각만 본다.
"""
import math
import re

from server import inquiry_policies

from . import eat_policy, public_signal

PREFIX = 'eatp_'
DECAY_GRID = (900, 2700)
STRENGTH_GRID = (4, 16)
SIGNAL_STRENGTH = 4.0
# 비교 기준 전용: 병원별 실제 수용확률을 그대로 아는 EAT. 수용 정보로 얻을 수 있는 이득의 상한이다.
ORACLE_NAME = 'eatp_oracle'
ORACLE_STRENGTH = 1000.0
_CONTEXT = dict(signal={}, calibration=None, oracle={})


def variant_name(decay, strength=None):
    return f'{PREFIX}t{decay}' if strength in (None, SIGNAL_STRENGTH) else f'{PREFIX}t{decay}_s{strength}'


def variant_grid():
    """개발 후보: 신호 감쇠 시간 × 신호 반영 강도(가상 표본 수)."""
    return tuple(variant_name(d, s) for d in DECAY_GRID for s in STRENGTH_GRID)


def set_context(signal, calibration, oracle=None):
    _CONTEXT['signal'] = dict(signal)
    _CONTEXT['calibration'] = dict(calibration) if calibration else None
    _CONTEXT['oracle'] = dict(oracle or {})


def clear_context():
    _CONTEXT['signal'], _CONTEXT['calibration'], _CONTEXT['oracle'] = {}, None, {}


def spec(name):
    base = eat_policy.spec('eat_l15_f')
    if name == ORACLE_NAME:
        base['config'].update(signal_decay_seconds=None, signal_strength=ORACLE_STRENGTH,
                              signal_cap_beds=public_signal.BEDS_CAP, oracle_reference=True)
        base.update(name=name, label='EAT + 완전 수용 정보(비교 기준, 실제 운영 불가)')
        return base
    match = re.fullmatch(PREFIX + r't(\d+)(?:_s(\d+))?', name) if isinstance(name, str) else None
    if match is None:
        raise ValueError('EAT-P policy name must look like eatp_t900 or eatp_t900_s16')
    decay = int(match.group(1))
    strength = float(match.group(2)) if match.group(2) else SIGNAL_STRENGTH
    if decay <= 0 or strength <= 0:
        raise ValueError('signal decay and strength must be positive')
    base['config'].update(signal_decay_seconds=decay, signal_strength=strength,
                          signal_cap_beds=public_signal.BEDS_CAP)
    base.update(name=name, label=f'EAT-P (공개 병상 신호, 감쇠 {decay}초, 강도 {strength:g})')
    return base


class PublicBeliefs(eat_policy.Beliefs):
    def __init__(self, observations, now, config, targets):
        super().__init__(observations, now, config)
        self._targets = targets
        self._strength = config['signal_strength']

    def accept(self, h):
        if h not in self._targets:
            return super().accept(h)
        p, w = self._targets[h]
        target = w * p + (1.0 - w) * self.accept_global
        weight = self._priors['hospital_shrink_accept'] + self._strength * w
        c = self._accepts.get(h, (0.0, 0.0))
        return eat_policy._shrink(c[0], c[1], target, weight)


def _targets(now, config):
    if config.get('oracle_reference'):
        return {hid: (p, 1.0) for hid, p in _CONTEXT['oracle'].items()}
    calibration = _CONTEXT['calibration']
    if not calibration:
        return {}
    out = {}
    for hid, row in _CONTEXT['signal'].items():
        age = now - row['reported_at']
        if age < 0:          # 미래 보고는 쓰지 않는다
            continue
        p = public_signal.logistic(calibration['beta0'], calibration['beta1'], row['beds'], calibration['cap'])
        out[hid] = (p, math.exp(-age / config['signal_decay_seconds']))
    return out


def decide(name, *, now, candidates, observations=(), pending=(), patient_pending=(), max_new,
           last_contact_at=None):
    """eat_policy.decide와 같은 계산. 수용확률 추정에만 공개 신호를 쓴다."""
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

    targets = _targets(now, config)
    beliefs = PublicBeliefs(history, now, config, targets)
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
        if row['outcome'] != 'timeout':
            last_reply.setdefault(row['hospital'], []).append(row)

    def pending_item(row):
        h, submitted = row['hospital'], row['submitted_at']
        mu, sigma = beliefs.mu(h), beliefs.sigma
        r, a = beliefs.reply(h), beliefs.accept(h)
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
                    public_signal={h: dict(signal_accept=round(p, 4), weight=round(w, 4))
                                   for h, (p, w) in sorted(targets.items())})
    return dict(send=send, reason=reason, evidence=evidence,
                next_review_at=now + policy['review_seconds'])


def install():
    """EAT·V4와 EAT-P 후보를 이 프로세스의 서버 정책 레지스트리에 등록한다(디스크 변경 없음)."""
    from . import harness
    harness.install(['eat_l15_f'])
    for name in list(variant_grid()) + [ORACLE_NAME]:
        inquiry_policies._POLICIES.setdefault(name, spec(name))
    current = inquiry_policies.decide
    if not getattr(current, '_eat_public', False):
        def wrapped(name, **kwargs):
            if name.startswith(PREFIX):
                return decide(name, **kwargs)
            return current(name, **kwargs)
        wrapped._eat_public = True
        inquiry_policies.decide = wrapped


def uninstall():
    from . import harness
    for name in list(variant_grid()) + [ORACLE_NAME]:
        inquiry_policies._POLICIES.pop(name, None)
    harness.uninstall()
    clear_context()
