"""4차 진단: 병원 회신 정보의 가치(Value of Information)를 재는 완전 정보 EAT 변형.

실제 운영에서는 쓸 수 없는 비교 기준이다. 1차 잠금 정책 `eat_policy.decide`(eat_l15_f)의 계산을
그대로 옮기고, 현재 환자의 각 병원 회신에 대해 아래 정보 중 일부를 '정답'으로 알려 준다.

  eato_silence : 그 병원이 이 환자 문의에 끝내 회신하지 않는지(무응답 여부)
  eato_timing  : 무응답 여부 + 이 환자 문의의 실제 처리시간
  eato_all     : 무응답 여부 + 처리시간 + 수용/불가

다른 환자의 대기열 처리시간과 미래 환자 발생은 알려 주지 않는다. 알려 준 정보가 없으면
1차 EAT와 계산이 같다(test로 확인). '확인 중' 신호는 이 가운데 무응답 여부와 처리 시작 시각의
일부만 전달할 수 있으므로, eato_silence가 그 신호 가치의 대략적 상한이다.
"""
import math

from server import inquiry_policies, inquiry_workflow

from . import eat_policy

KINDS = {
    'eato_silence': ('silence',),
    'eato_timing': ('silence', 'service'),
    'eato_all': ('silence', 'service', 'accept'),
}
KNOWN_SIGMA = 0.02
_CONTEXT = dict(info={}, patient=None)
_ORIGINAL_APPLY = inquiry_workflow._apply


def set_scenario(potentials):
    """potentials: 시나리오 잠재 회신 목록. 환자·병원별 정답을 정책 밖에 보관한다."""
    _CONTEXT['info'] = {(p['patient_id'], p['hospital_id']): dict(silent=p['no_response'],
                        service=p['service_seconds'], accept=p['accept']) for p in potentials}


def clear():
    _CONTEXT['info'], _CONTEXT['patient'] = {}, None


def spec(name):
    if name not in KINDS:
        raise ValueError('oracle policy must be one of ' + ', '.join(KINDS))
    base = eat_policy.spec('eat_l15_f')
    base['config'].update(oracle_reference=True, oracle_information=list(KINDS[name]))
    base.update(name=name, label=f'EAT + 완전 정보({", ".join(KINDS[name])}) 비교 기준, 실제 운영 불가')
    return base


class OracleBeliefs(eat_policy.Beliefs):
    def __init__(self, observations, now, config, info, kinds):
        super().__init__(observations, now, config)
        self._info, self._kinds = info, set(kinds)

    def reply(self, h):
        if 'silence' in self._kinds and h in self._info:
            return 1e-6 if self._info[h]['silent'] else 1.0
        return super().reply(h)

    def accept(self, h):
        if 'accept' in self._kinds and h in self._info:
            return 1.0 if self._info[h]['accept'] else 1e-6
        return super().accept(h)

    def own_service(self, h):
        """이 환자 문의의 처리시간 분포(mu, sigma). 대기열 앞 업무 추정에는 쓰지 않는다."""
        if 'service' in self._kinds and h in self._info:
            return math.log(self._info[h]['service']), KNOWN_SIGMA
        return self.mu(h), self.sigma


def _patient_info(name):
    pid = _CONTEXT['patient']
    if pid is None:
        return {}
    return {h: row for (p, h), row in _CONTEXT['info'].items() if p == pid}


def decide(name, *, now, candidates, observations=(), pending=(), patient_pending=(), max_new,
           last_contact_at=None):
    """eat_policy.decide와 같은 계산. 회신 확률·처리시간·수용확률만 정답으로 바꿔 넣는다."""
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

    beliefs = OracleBeliefs(history, now, config, _patient_info(name), KINDS[name])
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
        mu, sigma = beliefs.own_service(h)
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
            if sigma == KNOWN_SIGMA and spent >= math.exp(mu) * 1.05:
                # 정답 처리시간이 지났는데 회신이 없으면 실제 시작이 더 늦었던 것이다(대기열).
                # 시작 시각은 모르므로 이 문의는 학습된 일반 분포로 되돌린다.
                mu, sigma = beliefs.mu(h), beliefs.sigma
            f0 = _lognorm_cdf(spent, mu, sigma)
            alive = max(1e-9, 1.0 - f0)
            reply_left = r * alive / max(1e-9, 1.0 - r * f0)
            cdf = lambda x: (_lognorm_cdf(spent + x, mu, sigma) - f0) / alive
            total = reply_left * a
        return row['travel_seconds'], _masses(cdf, total, cells, step)

    def new_item(row):
        h = row['hospital']
        mu, sigma = beliefs.own_service(h)
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
                    oracle_information=list(KINDS[name]))
    return dict(send=send, reason=reason, evidence=evidence,
                next_review_at=now + policy['review_seconds'])


def install():
    """완전 정보 기준을 레지스트리에 등록하고, 판단 중인 환자 ID를 정책 밖에서 알 수 있게 한다."""
    from . import eat_public_policy
    eat_public_policy.install()
    for name in KINDS:
        inquiry_policies._POLICIES.setdefault(name, spec(name))
    current = inquiry_policies.decide
    if not getattr(current, '_eat_oracle', False):
        def wrapped(name, **kwargs):
            if name in KINDS:
                return decide(name, **kwargs)
            return current(name, **kwargs)
        wrapped._eat_oracle = True
        inquiry_policies.decide = wrapped
    if inquiry_workflow._apply is _ORIGINAL_APPLY:
        def apply_with_patient(conn, now, snapshot, candidates, ledger):
            _CONTEXT['patient'] = snapshot['patient_id']
            try:
                return _ORIGINAL_APPLY(conn, now, snapshot, candidates, ledger)
            finally:
                _CONTEXT['patient'] = None
        inquiry_workflow._apply = apply_with_patient


def uninstall():
    from . import eat_public_policy
    for name in KINDS:
        inquiry_policies._POLICIES.pop(name, None)
    inquiry_workflow._apply = _ORIGINAL_APPLY
    eat_public_policy.uninstall()
    clear()
