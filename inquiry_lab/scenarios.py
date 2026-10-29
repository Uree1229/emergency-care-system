"""보정된 무작위 혼합 환경(calibrated mix)과 기존 8개 유형의 새 seed 입력.

기존 서버 문의 실험(`server.inquiry_experiment`)의 입력 형식을 그대로 따른다.
정책은 potentials·workers를 볼 수 없고, 서버 실험기가 실제 FIFO 대기와 회신을 계산한다.

혼합 환경의 수치 근거와 가정:
- 병원 평균 무응답 약 20.7%, 회신 중앙 약 1.1분: 충북 ICT-EMS 분석(Kim et al., 2026)의 공개 수치.
- 수용 수준 3단계(문의당 수용 0.65·0.40·0.15): 호남권 시범사업의 환자당 문의 3.8곳과
  대략 맞도록 정한 설계 가정이다. 병원별 실제 수용률을 측정한 값이 아니다.
- 병원 수·이동시간·도착 형태·처리 인원·과거 기록은 합성 가정이다.
"""
from hashlib import sha256
import math
import random

from server import inquiry_experiment_scenarios as original

MIX_FAMILY = 'calibrated_mix'
DEVELOPMENT_MIX_SEEDS = tuple(range(810001, 810241))
VALIDATION_MIX_SEEDS = tuple(range(820001, 820401))
DEVELOPMENT_FAMILY_SEEDS = (830001, 830002, 830003)
VALIDATION_FAMILY_SEEDS = (840001, 840002, 840003, 840004, 840005, 840006)
# 이전 실험에서 이미 본 seed. 새 입력이 겹치면 생성 단계에서 거부한다.
USED_SEED_RANGES = ((1001, 1020), (2001, 2020), (3001, 3020), (4001, 4020), (5001, 5020),
                    (6101, 6108), (7101, 7112))
ACCEPT_LEVELS = {'low_scarcity': 0.65, 'medium_scarcity': 0.40, 'high_scarcity': 0.15}
REPLY_MEAN, REPLY_CONCENTRATION = 0.793, 6.0
SERVICE_MEDIAN, HOSPITAL_SPREAD, INQUIRY_SPREAD = 66.0, 0.5, 0.35


def _rng(seed, *keys):
    token = '\x1f'.join(str(v) for v in (MIX_FAMILY, seed, *keys)).encode('utf-8')
    return random.Random(int.from_bytes(sha256(token).digest(), 'big'))


def _beta(rng, mean, concentration):
    return rng.betavariate(mean * concentration, (1 - mean) * concentration)


def _check_seed(seed):
    if type(seed) is not int or seed < 0:
        raise ValueError('seed must be a nonnegative integer')
    if any(lo <= seed <= hi for lo, hi in USED_SEED_RANGES):
        raise ValueError(f'seed {seed} was already observed by an earlier experiment')


def make_mix(seed):
    """한 개의 무작위 혼합 시나리오. 같은 seed는 항상 같은 입력을 만든다."""
    _check_seed(seed)
    rng = _rng(seed, 'structure')
    count = rng.randint(5, 10)
    level = rng.choice(sorted(ACCEPT_LEVELS))
    accept_mean = ACCEPT_LEVELS[level]
    arrival = rng.choices(('single', 'burst', 'stream'), weights=(0.4, 0.2, 0.4))[0]
    patients_n = 1 if arrival == 'single' else rng.randint(3, 8)
    history_mode = rng.choices(('none', 'informative', 'stale'), weights=(0.5, 0.35, 0.15))[0]

    hospitals, hidden = [], {}
    travel = rng.randint(240, 600)
    base_travel = []
    for index in range(count):
        hid = f'H{index + 1:02d}'
        if index:
            travel += rng.randint(15, 150)
        base_travel.append(travel)
        h_rng = _rng(seed, 'hospital', hid)
        hidden[hid] = dict(
            reply=_beta(h_rng, REPLY_MEAN, REPLY_CONCENTRATION),
            accept=_beta(h_rng, accept_mean, 4.0),
            mu=math.log(SERVICE_MEDIAN) + h_rng.gauss(0.0, HOSPITAL_SPREAD))
        hospitals.append(dict(id=hid, name=f'합성병원 {index + 1}',
                              lat=round(35.1595 + .006 * (index + 1), 6),
                              lon=round(126.8526 + .004 * (index + 1), 6),
                              capabilities=['응급의학'],
                              workers=2 if h_rng.random() < 0.25 else 1))

    if arrival == 'single':
        times = [0]
    elif arrival == 'burst':
        times = [0] * patients_n
    else:
        gap = rng.uniform(10, 60)
        times, at = [], 0
        for _ in range(patients_n):
            times.append(int(round(at)))
            at += rng.expovariate(1.0 / gap)
    patients = []
    for index, at in enumerate(times):
        pid = f'P{index + 1:02d}'
        p_rng = _rng(seed, 'patient', pid)
        jitter = {h['id']: p_rng.randint(-10, 10) for h in hospitals}
        patients.append(dict(id=pid, at=at,
                             lat=round(35.1595 + (index + 1) * 0.00011, 6),
                             lon=round(126.8526 - (index + 1) * 0.00013, 6),
                             travel_seconds={h['id']: max(60, base_travel[i] + jitter[h['id']])
                                             for i, h in enumerate(hospitals)}))

    potentials = []
    for p in patients:
        for h in hospitals:
            hp = hidden[h['id']]
            r = _rng(seed, 'potential', p['id'], h['id'])
            service = int(round(math.exp(hp['mu'] + r.gauss(0.0, INQUIRY_SPREAD))))
            potentials.append(dict(patient_id=p['id'], hospital_id=h['id'],
                                   service_seconds=min(240, max(8, service)),
                                   no_response=r.random() >= hp['reply'],
                                   accept=r.random() < hp['accept']))

    history = []
    if history_mode != 'none':
        ids = [h['id'] for h in hospitals]
        # 최근 거절은 실제 수용이 낮은 병원(수용확률 0.3 미만)에서 생긴다(informative).
        # stale은 같은 건수의 거절을 무작위 병원에 붙이고 처리시간도 다른 병원 것을 쓴다.
        # 서버 실험기 계약상 수용 이력은 넣을 수 없어 거절 이력만 만든다.
        sources = [hid for hid in ids if hidden[hid]['accept'] < 0.3]
        targets = sources
        if history_mode == 'stale':
            shuffled = ids[:]
            _rng(seed, 'stale').shuffle(shuffled)
            targets = shuffled[:len(sources)]
        for source, target in zip(sources, targets):
            h_rng = _rng(seed, 'history', target)
            delay_mu = hidden[source if history_mode == 'stale' else target]['mu']
            for sample in range(h_rng.randint(1, 3)):
                sent = -h_rng.randint(180, 1500)
                delay = int(round(math.exp(delay_mu + h_rng.gauss(0.0, INQUIRY_SPREAD))))
                reply = sent + min(240, max(8, delay))
                if reply >= 0:
                    continue
                history.append(dict(patient_id=f'HIST_{target}_{sample + 1}', hospital_id=target,
                                    sent_at=sent, reply_at=reply, action='decline'))
        history.sort(key=lambda r: (r['reply_at'], r['sent_at'], r['hospital_id'], r['patient_id']))

    return dict(id=f'{MIX_FAMILY}-{seed}', family=MIX_FAMILY, seed=seed,
                description=(f'보정 혼합: 병원 {count}곳 · {level} · {arrival} · 환자 {len(patients)}명 · '
                             f'과거기록 {history_mode}'),
                hospitals=hospitals, patients=patients, potentials=potentials,
                history=history, horizon_seconds=300,
                mix=dict(level=level, arrival=arrival, history=history_mode,
                         hospitals=count, patients=len(patients)))


def make_mix_suite(seeds):
    return [make_mix(seed) for seed in seeds]


def make_family_suite(seeds):
    """기존 8개 유형을 새 seed로 생성한다. 이미 본 seed는 거부한다."""
    for seed in seeds:
        _check_seed(seed)
    return original.make_suite(seeds=tuple(seeds))


def oracle(scenario):
    """완전정보 기준. 환자별로 '처리시간 + 이동시간'이 가장 작은 수용 병원.

    단일 환자는 그 병원 한 곳에만 즉시 문의하면 실제로 달성되는 최선값이다.
    여러 환자는 대기열 경합을 무시한 하한이다(실제로 달성 못 할 수 있음).
    """
    by_pair = {(p['patient_id'], p['hospital_id']): p for p in scenario['potentials']}
    result = {}
    for patient in scenario['patients']:
        values = [by_pair[(patient['id'], hid)]['service_seconds'] + travel
                  for hid, travel in patient['travel_seconds'].items()
                  if by_pair[(patient['id'], hid)]['accept']
                  and not by_pair[(patient['id'], hid)]['no_response']
                  and by_pair[(patient['id'], hid)]['service_seconds'] <= 300]
        result[patient['id']] = min(values) if values else None
    return dict(exact=len(scenario['patients']) == 1, arrival_seconds=result)
