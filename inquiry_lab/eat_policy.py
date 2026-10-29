"""기대 도착시간(EAT, Expected Arrival Time) 기반 초기 문의 정책.

서버 문의 정책(`server.inquiry_policies.decide`)과 같은 순수 계산 계약을 쓴다.
각 문의를 "수용 회신이 언제 도착할지"의 확률분포로 보고, 15초마다 남은 후보 전체의
문의 일정(누구에게 지금·15·30·60·90·120·180초 뒤에 문의할지, 또는 문의하지 않을지)을
다시 계획한다. 계획의 비용은 다음 합이며, 지금 보낼 문의만 실행한다(이동 지평 계획).

  E[첫 수용까지 시간 + 그 병원 이동시간]  +  Σ 문의비용 × P(그 문의 시각까지 수용 없음)

기존 문의의 회신이 곧 올 가능성이 크면 다음 문의를 뒤로 미루는 계획이 싸고, 회신이
늦어질수록(무응답 가능성이 커질수록) 지금 문의하는 계획이 싸진다.

도착 = 지금부터 첫 수용 회신까지의 시간 + 그 병원까지 이동시간이다. 서버 실험과 같이
첫 유효 수용 회신에서 바로 확정한다고 본다. 제한시간 안에 수용이 없으면 실패 벌점을 둔다.

회신률·수용률·처리시간은 공개 통계 기반 사전값에서 시작해, 이미 관측한 회신으로만
갱신한다(경험적 베이즈 축소). 병원 처리 인원, 잠재 회신, 미래 회신 시각 등 환경 내부값은
사용하지 않는다. 결과는 합성 실험용 연구 후보이며 수용 확률·임상 예후의 예측이 아니다.
"""
from copy import deepcopy
import math

from server import inquiry_policies

POLICY_PREFIX = 'eat_'
SQRT2 = math.sqrt(2.0)

# 사전값. 무응답 20.7%·회신 중앙 1.1분은 충북 ICT-EMS 분석(Kim et al., 2026)의
# 공개 수치를 사용한다. 수용률 0.40은 호남권 시범사업의 환자당 문의 3.8곳과 대략
# 맞는 값(회신 0.79 × 수용 0.40 ≈ 문의당 0.32)으로 정한 설계 가정이며 측정값이 아니다.
PRIORS = {
    'reply_probability': 0.79,
    'reply_prior_weight': 10.0,
    'accept_probability': 0.40,
    'accept_prior_weight': 4.0,
    'service_median_seconds': 66.0,
    'service_log_sd': 0.70,
    'service_prior_weight': 3.0,
    'hospital_shrink_reply': 3.0,
    'hospital_shrink_accept': 2.0,
    'hospital_shrink_service': 2.0,
}
BASE_CONFIG = {
    # 서버 워크플로가 고정 대기(60초) 대신 매 review마다 이 정책을 호출하도록
    # 관측형 정책 표식인 prior_reply_seconds를 둔다. 값은 사전 회신 중앙값이다.
    'prior_reply_seconds': 66,
    'observation_timeout_seconds': inquiry_policies.OBSERVATION_TIMEOUT_SECONDS,
    'history_window_seconds': 1800,
    'decay_seconds': 600,
    'grid_seconds': 5,
    'plan_offsets_seconds': [0, 15, 30, 60, 90, 120, 180],
    'plan_passes': 2,
    # 자동 탐색 실패 시 추가 비용. 상황실 수동 선정(호남권 처리시간 중앙 18분=1,080초)과
    # 평균 이동(약 7분)을 합친 설계값이다. 평가 지표의 실패 비용과 같은 값을 쓴다.
    'failure_penalty_seconds': 1500,
    'max_concurrent': 4,
    'lambda_seconds': 30,
    'congestion_aware': False,
    'assumed_workers': 1,
    'max_candidates_evaluated': 6,
    'priors': PRIORS,
}
LAMBDA_GRID = (0, 15, 30, 60, 120)


def variant_name(lam, congestion_aware):
    return f'{POLICY_PREFIX}l{lam}_{"c" if congestion_aware else "f"}'


def variant_grid():
    """개발 단계에서 비교할 사전 고정 후보. 문의비용 λ와 혼잡 반영 여부만 바꾼다."""
    return tuple(variant_name(lam, aware) for aware in (False, True) for lam in LAMBDA_GRID)


def spec(name):
    """서버 정책 명세와 같은 형식. 이름에서 설정을 해석하며 다른 이름은 거부한다."""
    if not isinstance(name, str) or not name.startswith(POLICY_PREFIX):
        raise ValueError('EAT policy names start with ' + POLICY_PREFIX)
    body = name[len(POLICY_PREFIX):]
    try:
        lam_text, mode = body.split('_')
        if not lam_text.startswith('l') or mode not in ('c', 'f'):
            raise ValueError
        lam = int(lam_text[1:])
    except ValueError:
        raise ValueError('EAT policy name must look like eat_l30_f') from None
    if lam < 0:
        raise ValueError('lambda must be nonnegative')
    config = deepcopy(BASE_CONFIG)
    config.update(lambda_seconds=lam, congestion_aware=mode == 'c')
    return dict(name=name, label=f'기대 도착시간 EAT (문의비용 {lam}초{", 혼잡 반영" if mode == "c" else ""})',
                version='1', batch_size=1, wait_seconds=15, review_seconds=15,
                max_contacts=10, deadline_seconds=300, experimental=True,
                research_only=True, config=config)


# ---------------------------------------------------------------- 확률 계산

def _lognorm_cdf(x, mu, sigma):
    if x <= 0:
        return 0.0
    return 0.5 * (1.0 + math.erf((math.log(x) - mu) / (sigma * SQRT2)))


def _shrink(successes, total, prior_mean, prior_weight):
    return (prior_mean * prior_weight + successes) / (prior_weight + total)


class Beliefs:
    """관측한 회신만으로 만든 병원별 회신률·수용률·처리시간 추정.

    관측마다 exp(-경과/τ) 가중치를 준다. 오래된 기록은 사전값 쪽으로 서서히 돌아가며,
    기간 창 경계에서 갑자기 버리지 않는다. τ는 설정의 decay_seconds다.
    """

    def __init__(self, observations, now, config):
        priors = config['priors']
        tau = config['decay_seconds']
        rows = sorted(observations, key=lambda r: (r['observed_at'], r['submitted_at'], r['hospital']))
        replies = {}       # hospital -> [가중 회신, 가중 전체]
        accepts = {}       # hospital -> [가중 수용, 가중 수용+불가]
        services = {}      # hospital -> [(가중치, log 처리시간)]
        last_free = {}     # hospital -> 직전 회신 시각(FIFO 처리 시작 추정)
        for row in rows:
            h, outcome = row['hospital'], row['outcome']
            weight = math.exp(-max(0.0, now - row['observed_at']) / tau)
            counts = replies.setdefault(h, [0.0, 0.0])
            counts[1] += weight
            if outcome == 'timeout':
                continue
            counts[0] += weight
            if outcome in ('accept', 'decline'):
                acc = accepts.setdefault(h, [0.0, 0.0])
                acc[1] += weight
                acc[0] += weight * (outcome == 'accept')
            # 같은 병원에서 먼저 회신한 문의가 끝난 뒤 처리됐다고 본다(처리자 1명 가정).
            start = max(row['submitted_at'], last_free.get(h, row['submitted_at']))
            service = max(1.0, row['observed_at'] - start)
            services.setdefault(h, []).append((weight, math.log(service)))
            last_free[h] = max(last_free.get(h, row['observed_at']), row['observed_at'])
        total_replies = sum(v[0] for v in replies.values())
        total_resolved = sum(v[1] for v in replies.values())
        self.reply_global = _shrink(total_replies, total_resolved,
                                    priors['reply_probability'], priors['reply_prior_weight'])
        total_acc = sum(v[0] for v in accepts.values())
        total_dec = sum(v[1] for v in accepts.values())
        self.accept_global = _shrink(total_acc, total_dec,
                                     priors['accept_probability'], priors['accept_prior_weight'])
        mu0 = math.log(priors['service_median_seconds'])
        n0, s0 = priors['service_prior_weight'], priors['service_log_sd']
        logs = [pair for values in services.values() for pair in values]
        wsum = sum(w for w, _ in logs)
        self.mu_global = (mu0 * n0 + sum(w * x for w, x in logs)) / (n0 + wsum)
        var = (s0 * s0 * n0 + sum(w * (x - self.mu_global) ** 2 for w, x in logs)) / (n0 + wsum)
        self.sigma = max(0.25, math.sqrt(var))
        self._replies, self._accepts, self._services = replies, accepts, services
        self._priors = priors

    def reply(self, h):
        c = self._replies.get(h, (0.0, 0.0))
        return _shrink(c[0], c[1], self.reply_global, self._priors['hospital_shrink_reply'])

    def accept(self, h):
        c = self._accepts.get(h, (0.0, 0.0))
        return _shrink(c[0], c[1], self.accept_global, self._priors['hospital_shrink_accept'])

    def mu(self, h):
        values = self._services.get(h, ())
        k = self._priors['hospital_shrink_service']
        return (self.mu_global * k + sum(w * x for w, x in values)) / (k + sum(w for w, _ in values))

    def mean_service(self, h):
        return math.exp(self.mu(h) + self.sigma * self.sigma / 2.0)

    def samples(self, h):
        return self._replies.get(h, (0.0, 0.0))[1]


def _masses(cdf, total, cells, step):
    """구간 (k·step, (k+1)·step]에 수용 회신이 도착할 확률."""
    out, previous = [], 0.0
    for k in range(1, cells + 1):
        value = cdf(k * step)
        out.append(max(0.0, (value - previous) * total))
        previous = value
    return out


def expected_cost(items, cells, step, horizon, failure_penalty):
    """items: (이동시간, 구간별 수용 확률) 목록. 첫 수용 병원으로 확정한다고 본다.

    같은 구간에 둘 이상이 수용하면 가까운 병원을 고른다고 근사한다.
    반환값은 지금부터의 기대 (선정 소요 + 이동시간), 실패는 horizon + 벌점이다.
    """
    items = sorted(items, key=lambda it: it[0])
    survival = [1.0] * len(items)
    total = 0.0
    for k in range(cells):
        alive = 1.0
        for s in survival:
            alive *= s
        if alive <= 1e-12:
            return total
        mid = (k + 0.5) * step
        none_nearer = 1.0
        for i, (travel, masses) in enumerate(items):
            s = survival[i]
            hazard = masses[k] / s if s > 1e-12 else 0.0
            hazard = min(1.0, hazard)
            if hazard > 0.0:
                total += alive * none_nearer * hazard * (mid + travel)
                none_nearer *= 1.0 - hazard
        for i, (_, masses) in enumerate(items):
            survival[i] = max(0.0, survival[i] - masses[k])
    fail = 1.0
    for s in survival:
        fail *= s
    return total + fail * (horizon + failure_penalty)


def _no_accept_by(items, cells):
    value = 1.0
    for _, masses in items:
        value *= max(0.0, 1.0 - sum(masses[:cells]))
    return value


def _shift(item, cells_shift):
    travel, masses = item
    return travel, [0.0] * cells_shift + masses[:len(masses) - cells_shift]


# ---------------------------------------------------------------- 결정

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
        wait = load * beliefs.mean_service(h) / workers
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
                    candidate_plan=scored, sent_hospitals=list(send))
    return dict(send=send, reason=reason, evidence=evidence,
                next_review_at=now + policy['review_seconds'])
