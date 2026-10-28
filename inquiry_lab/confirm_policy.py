"""수용 회신 뒤 '지금 확정할지, 더 가까운 병원의 회신을 잠깐 기다릴지'를 정하는 확정 대기 규칙.

서버 실험의 기본 확정은 첫 유효 수용 회신 즉시(가장 가까운 수용 병원)다. 여러 곳에 동시에
문의하면 먼 병원이 먼저 수용해 그 병원으로 확정되는 손실(lock-in)이 생긴다. 이 규칙은
더 가까운 병원에 이미 보낸 문의가 남아 있을 때만, 그 문의가 곧 수용할 확률을 EAT와 같은
확률 모형으로 계산해 기대 도착시간이 줄어들 때만 최대 hold초까지 기다린다.

  지금 확정:      도착 = 현재 가장 가까운 수용 병원 이동시간
  τ초까지 대기:   E[min(더 가까운 수용 도착, τ) + 그때 확정한 병원 이동시간]

τ는 5초 단위로 모두 비교하며, 5초마다 다시 판단한다. 대기 중에도 기존 수용 응답은 유효하다
(서버 수용 유효시간 600초). 관측 종료 직전에는 반드시 확정한다. 새 문의는 보내지 않는다.
수용 확률·회신 시각은 예측값이 아니라 관측 회신으로 갱신한 합성 모형의 추정치다.
"""
import math

from server import inquiry_policies

from . import eat_policy

CHECK_SECONDS = 5
HOLD_GRID = (30, 60, 120)
EAT_CONFIG = eat_policy.spec('eat_l15_f')['config']


def rule_name(hold):
    return 'immediate' if not hold else f'hold{hold}'


def parse_rule(name):
    if name == 'immediate':
        return 0
    if isinstance(name, str) and name.startswith('hold') and name[4:].isdigit() and int(name[4:]) > 0:
        return int(name[4:])
    raise ValueError('confirmation rule must be immediate or holdN')


def pending_masses(rows, *, now, observations, pending, cells, step, config=EAT_CONFIG):
    """자기 미해결 문의마다 (이동시간, 구간별 수용 확률). eat_policy.decide의 계산과 같다."""
    history = inquiry_policies._observations(observations, now, config['history_window_seconds'])
    unresolved = inquiry_policies._pending(pending, now)
    own = inquiry_policies._pending(rows, now, own=True)
    beliefs = eat_policy.Beliefs(history, now, config)
    workers = config['assumed_workers']
    queue, last_reply = {}, {}
    for row in unresolved:
        queue.setdefault(row['hospital'], []).append(row)
    for row in history:
        if row['outcome'] != 'timeout':
            last_reply.setdefault(row['hospital'], []).append(row)
    items = []
    for row in own:
        h, submitted = row['hospital'], row['submitted_at']
        mu, sigma = beliefs.mu(h), beliefs.sigma
        r, a = beliefs.reply(h), beliefs.accept(h)
        ahead = sum(1.0 if q['submitted_at'] < submitted else 0.5
                    for q in queue.get(h, ())
                    if q['submitted_at'] <= submitted and q['patient'] != row['patient'])
        if ahead > 0:
            wait = ahead * beliefs.mean_service(h) / workers
            cdf = (lambda w: (lambda x: eat_policy._lognorm_cdf(x - w, mu, sigma)))(wait)
            total = r * a
        else:
            freed = [o['observed_at'] for o in last_reply.get(h, ()) if o['submitted_at'] <= submitted]
            started = max([submitted, *freed])
            spent = max(0.0, now - started)
            f0 = eat_policy._lognorm_cdf(spent, mu, sigma)
            alive = max(1e-9, 1.0 - f0)
            reply_left = r * alive / max(1e-9, 1.0 - r * f0)
            cdf = (lambda s, f, al: (lambda x: (eat_policy._lognorm_cdf(s + x, mu, sigma) - f) / al))(spent, f0, alive)
            total = reply_left * a
        items.append((row['hospital'], row['travel_seconds'], eat_policy._masses(cdf, total, cells, step)))
    return items


def decide(*, now, accepted, own_pending, observations, pending, deadline_left, hold, held_seconds):
    """accepted: 유효 수용 [{hospital, travel_seconds}], own_pending: 이 환자의 미해결 문의.

    반환: dict(action='confirm'|'hold', hospital, reason, evidence). hold면 CHECK_SECONDS 뒤 다시 부른다.
    """
    if not accepted:
        raise ValueError('accepted options are required')
    best = min(accepted, key=lambda o: (o['travel_seconds'], o['hospital']))
    step = CHECK_SECONDS
    nearer = [row for row in own_pending if row['travel_seconds'] < best['travel_seconds']]
    budget = min(hold - held_seconds, deadline_left - step)
    base = dict(hospital=best['hospital'], evidence=dict(best_travel_seconds=best['travel_seconds'],
                nearer_pending=[row['hospital'] for row in nearer], held_seconds=held_seconds,
                hold_limit_seconds=hold))
    if not hold:
        return dict(base, action='confirm', reason='immediate')
    if not nearer:
        return dict(base, action='confirm', reason='no_nearer_pending')
    if budget < step:
        return dict(base, action='confirm', reason='hold_budget_or_deadline')
    cells = int(budget // step)
    items = pending_masses(nearer, now=now, observations=observations, pending=pending,
                           cells=cells, step=step)
    immediate = float(best['travel_seconds'])
    costs = []
    for tau in range(1, cells + 1):
        # τ번째 구간 끝에 원래 수용 병원으로 확정하는 대안을 확실한 '수용'처럼 넣는다.
        sentinel = (best['travel_seconds'], [0.0] * (tau - 1) + [1.0] + [0.0] * (cells - tau))
        value = eat_policy.expected_cost([(t, m) for _, t, m in items] + [sentinel],
                                         cells, step, cells * step, 0)
        # expected_cost는 구간 가운데 시각을 쓴다. 원래 병원 확정은 구간 끝(τ·step초)에
        # 일어나므로, 그때까지 더 가까운 수용이 없을 확률만큼 반 구간을 더한다.
        none_yet = math.prod(max(0.0, 1.0 - sum(m[:tau])) for _, _, m in items)
        costs.append((value + 0.5 * step * none_yet, tau))
    best_cost, best_tau = min(costs)
    evidence = dict(base['evidence'], immediate_cost=immediate, best_hold_cost=round(best_cost, 3),
                    best_hold_seconds=best_tau * step,
                    nearer_accept_probability=round(1.0 - math.prod(1.0 - sum(m) for _, _, m in items), 4))
    if best_cost < immediate - 1e-9:
        return dict(base, action='hold', reason='nearer_acceptance_likely', evidence=evidence)
    return dict(base, action='confirm', reason='waiting_not_worth', evidence=evidence)
