"""3차 실험용 공개 가용병상 신호(합성)와 신호→수용확률 보정식.

국립중앙의료원 실시간 가용병상(hvec 등)과 입력시각(hvidate)을 흉내 낸 합성 신호다.
병상이 남아 있으면 수용 가능성이 높지만, 보고가 늦거나(오래됨) 실제 상황과 무관할 수
있다(무관). 정책은 병상 수와 보고 시각만 보고, 신호 품질 표지는 보지 못한다.

혼합 환경(`scenarios.make_mix`)의 숨은 병원별 수용확률을 같은 난수 순서로 다시 계산한다.
`make_mix`의 입력·잠재 회신은 그대로이며 신호는 별도 난수열로 만든다.
수치(병상 척도 10, 결측 10%, 상태 변화 30분)는 설계 가정이며 실제 자료로 보정한 값이 아니다.
"""
from hashlib import sha256
import math
import random

from . import scenarios

QUALITIES = ('fresh', 'stale', 'noise', 'mixed')
BEDS_SCALE = 10.0
MISSING_PROBABILITY = 0.10
FRESH_MAX_AGE = 300
STALE_AGE = (600, 3600)
STATE_CHANGE_SECONDS = 1800
BEDS_CAP = 15
NOISE_MEAN = 0.40     # 무관 신호의 고정 분포 평균(지역 수준과 독립)


def _rng(*keys):
    token = '\x1f'.join(str(k) for k in ('public-signal', *keys)).encode('utf-8')
    return random.Random(int.from_bytes(sha256(token).digest(), 'big'))


def _poisson(rng, mean):
    # Knuth 방식. 평균이 작아(≤ 10) 반복 횟수가 적다.
    limit, k, p = math.exp(-mean), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def hidden_parameters(seed):
    """make_mix(seed)와 같은 난수 순서로 병원별 숨은 회신률·수용확률·처리시간·인원을 계산한다."""
    rng = scenarios._rng(seed, 'structure')
    count = rng.randint(5, 10)
    level = rng.choice(sorted(scenarios.ACCEPT_LEVELS))
    accept_mean = scenarios.ACCEPT_LEVELS[level]
    hospitals = {}
    for index in range(count):
        hid = f'H{index + 1:02d}'
        h_rng = scenarios._rng(seed, 'hospital', hid)
        reply = scenarios._beta(h_rng, scenarios.REPLY_MEAN, scenarios.REPLY_CONCENTRATION)
        accept = scenarios._beta(h_rng, accept_mean, 4.0)
        mu = math.log(scenarios.SERVICE_MEDIAN) + h_rng.gauss(0.0, scenarios.HOSPITAL_SPREAD)
        workers = 2 if h_rng.random() < 0.25 else 1
        hospitals[hid] = dict(reply=reply, accept=accept, mu=mu, workers=workers)
    return dict(level=level, accept_mean=accept_mean, hospitals=hospitals)


def mix_signal(seed):
    """혼합 시나리오의 공개 신호. quality는 시나리오 단위 표지(정책에는 주지 않는다)."""
    hidden = hidden_parameters(seed)
    rng = _rng('mix', seed)
    quality = rng.choice(QUALITIES)
    out = {}
    for hid, hp in hidden['hospitals'].items():
        if rng.random() < MISSING_PROBABILITY:
            continue
        kind = quality if quality != 'mixed' else rng.choice(('fresh', 'stale', 'noise'))
        if kind == 'fresh':
            age, state = rng.uniform(0, FRESH_MAX_AGE), hp['accept']
        elif kind == 'stale':
            age = rng.uniform(*STALE_AGE)
            same = rng.random() < math.exp(-age / STATE_CHANGE_SECONDS)
            state = hp['accept'] if same else scenarios._beta(rng, hidden['accept_mean'], 4.0)
        else:   # noise: 최근 보고처럼 보이지만 병원의 실제 수용과도, 지역 혼잡 수준과도 무관
            age, state = rng.uniform(0, FRESH_MAX_AGE), scenarios._beta(rng, NOISE_MEAN, 4.0)
        out[hid] = dict(beds=_poisson(rng, BEDS_SCALE * state), age_seconds=int(round(age)), kind=kind)
    return dict(quality=quality, hospitals=out)


def family_signal(scenario):
    """기존 8유형용 신선한 신호: 병원별로 실제 수용하는 환자 비율에 비례한다(스트레스 시험)."""
    rng = _rng('family', scenario['family'], scenario['seed'])
    by_hospital = {}
    for p in scenario['potentials']:
        by_hospital.setdefault(p['hospital_id'], []).append(1.0 if p['accept'] else 0.0)
    out = {}
    for hid in sorted(by_hospital):
        share = sum(by_hospital[hid]) / len(by_hospital[hid])
        out[hid] = dict(beds=_poisson(rng, BEDS_SCALE * share), age_seconds=int(round(rng.uniform(0, FRESH_MAX_AGE))),
                        kind='fresh')
    return dict(quality='fresh', hospitals=out)


def oracle_accept(kind, seed, scenario):
    """비교 기준용 완전 수용 정보: 병원별 실제 수용확률(혼합) 또는 실제 수용 비율(8유형)."""
    if kind == 'mix':
        return {hid: hp['accept'] for hid, hp in hidden_parameters(seed)['hospitals'].items()}
    shares = {}
    for p in scenario['potentials']:
        shares.setdefault(p['hospital_id'], []).append(1.0 if p['accept'] else 0.0)
    return {hid: sum(v) / len(v) for hid, v in shares.items()}


def policy_view(signal, epoch):
    """정책에 넘기는 값: 병상 수와 보고 시각만. 품질 표지와 숨은 상태는 넘기지 않는다."""
    return {hid: dict(beds=row['beds'], reported_at=epoch - row['age_seconds'])
            for hid, row in signal['hospitals'].items()}


def calibration_samples(seeds):
    """개발 혼합의 신선한 신호에서 (병상 수, 회신했을 때 수용 여부) 표본을 만든다."""
    samples = []
    for seed in seeds:
        scenario = scenarios.make_mix(seed)
        signal = mix_signal(seed)['hospitals']
        for p in scenario['potentials']:
            row = signal.get(p['hospital_id'])
            if row is None or row['kind'] != 'fresh' or p['no_response']:
                continue
            samples.append((row['beds'], 1 if p['accept'] else 0))
    return samples


def logistic(beta0, beta1, beds, cap=BEDS_CAP):
    z = beta0 + beta1 * min(beds, cap)
    return 1.0 / (1.0 + math.exp(-z)) if z >= 0 else math.exp(z) / (1.0 + math.exp(z))


def fit_calibration(samples, cap=BEDS_CAP, iterations=100):
    """수용 = logistic(β0 + β1·min(병상, cap))의 최대우도 추정(뉴턴법). 결정적이다."""
    if not samples:
        raise ValueError('calibration samples are required')
    b0 = b1 = 0.0
    for _ in range(iterations):
        g0 = g1 = h00 = h01 = h11 = 0.0
        for beds, y in samples:
            x = min(beds, cap)
            p = logistic(b0, b1, x, cap)
            w = p * (1 - p)
            g0 += y - p
            g1 += (y - p) * x
            h00 += w
            h01 += w * x
            h11 += w * x * x
        det = h00 * h11 - h01 * h01
        if det <= 1e-12:
            break
        d0 = (h11 * g0 - h01 * g1) / det
        d1 = (h00 * g1 - h01 * g0) / det
        b0, b1 = b0 + d0, b1 + d1
        if abs(d0) + abs(d1) < 1e-10:
            break
    mean_y = sum(y for _, y in samples) / len(samples)
    return dict(beta0=round(b0, 10), beta1=round(b1, 10), cap=cap, samples=len(samples),
                mean_accept=round(mean_y, 6))
