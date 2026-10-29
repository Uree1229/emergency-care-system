"""다수 사상자(대형 동시 다발) 스트레스 입력. 1차 보정 혼합(`scenarios.make_mix`)의 수치 가정을 그대로 쓰고
환자 수·병원 수·도착 형태만 바꾼다.

- 병원 8~12곳, 환자 12·16·20명 중 하나.
- 도착은 두 가지다. simultaneous는 모두 0초, wave는 처음 120초 안에 고르게 흩어진다.
- 수용 수준·무응답·처리시간·과거기록·처리 인원(25%가 2명) 가정은 보정 혼합과 같다.
- 난수열 이름을 따로 써서(mass_burst) 보정 혼합 seed와 겹치지 않는다. 이미 본 seed 범위는 거부한다.
실제 다수 사상자 사고 자료로 맞춘 값이 아니라, 보정 혼합을 크게 늘린 스트레스 시험이다.
"""
from hashlib import sha256
import math
import random

from . import scenarios

MASS_FAMILY = 'mass_burst'
PATIENT_COUNTS = (12, 16, 20)
WAVE_SECONDS = 120


def _rng(seed, *keys):
    token = '\x1f'.join(str(v) for v in (MASS_FAMILY, seed, *keys)).encode('utf-8')
    return random.Random(int.from_bytes(sha256(token).digest(), 'big'))


def make_mass(seed):
    scenarios._check_seed(seed)
    rng = _rng(seed, 'structure')
    count = rng.randint(8, 12)
    level = rng.choice(sorted(scenarios.ACCEPT_LEVELS))
    accept_mean = scenarios.ACCEPT_LEVELS[level]
    arrival = rng.choice(('simultaneous', 'wave'))
    patients_n = rng.choice(PATIENT_COUNTS)
    history_mode = rng.choices(('none', 'informative', 'stale'), weights=(0.5, 0.35, 0.15))[0]

    hospitals, hidden, base_travel = [], {}, []
    travel = rng.randint(240, 600)
    for index in range(count):
        hid = f'H{index + 1:02d}'
        if index:
            travel += rng.randint(15, 150)
        base_travel.append(travel)
        h_rng = _rng(seed, 'hospital', hid)
        hidden[hid] = dict(reply=scenarios._beta(h_rng, scenarios.REPLY_MEAN, scenarios.REPLY_CONCENTRATION),
                           accept=scenarios._beta(h_rng, accept_mean, 4.0),
                           mu=math.log(scenarios.SERVICE_MEDIAN) + h_rng.gauss(0.0, scenarios.HOSPITAL_SPREAD))
        hospitals.append(dict(id=hid, name=f'합성병원 {index + 1}', lat=round(35.1595 + .006 * (index + 1), 6),
                              lon=round(126.8526 + .004 * (index + 1), 6), capabilities=['응급의학'],
                              workers=2 if h_rng.random() < 0.25 else 1))

    times = [0] * patients_n if arrival == 'simultaneous' else sorted(rng.randint(0, WAVE_SECONDS) for _ in range(patients_n))
    patients = []
    for index, at in enumerate(times):
        pid = f'P{index + 1:02d}'
        p_rng = _rng(seed, 'patient', pid)
        jitter = {h['id']: p_rng.randint(-10, 10) for h in hospitals}
        patients.append(dict(id=pid, at=at, lat=round(35.1595 + (index + 1) * 0.00011, 6),
                             lon=round(126.8526 - (index + 1) * 0.00013, 6),
                             travel_seconds={h['id']: max(60, base_travel[i] + jitter[h['id']])
                                             for i, h in enumerate(hospitals)}))

    potentials = []
    for p in patients:
        for h in hospitals:
            hp = hidden[h['id']]
            r = _rng(seed, 'potential', p['id'], h['id'])
            service = int(round(math.exp(hp['mu'] + r.gauss(0.0, scenarios.INQUIRY_SPREAD))))
            potentials.append(dict(patient_id=p['id'], hospital_id=h['id'], service_seconds=min(240, max(8, service)),
                                   no_response=r.random() >= hp['reply'], accept=r.random() < hp['accept']))

    history = []
    if history_mode != 'none':
        ids = [h['id'] for h in hospitals]
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
                delay = int(round(math.exp(delay_mu + h_rng.gauss(0.0, scenarios.INQUIRY_SPREAD))))
                reply = sent + min(240, max(8, delay))
                if reply >= 0:
                    continue
                history.append(dict(patient_id=f'HIST_{target}_{sample + 1}', hospital_id=target,
                                    sent_at=sent, reply_at=reply, action='decline'))
        history.sort(key=lambda r: (r['reply_at'], r['sent_at'], r['hospital_id'], r['patient_id']))

    return dict(id=f'{MASS_FAMILY}-{seed}', family=MASS_FAMILY, seed=seed,
                description=f'다수 사상자: 병원 {count}곳 · {level} · {arrival} · 환자 {patients_n}명 · 과거기록 {history_mode}',
                hospitals=hospitals, patients=patients, potentials=potentials, history=history, horizon_seconds=300,
                mix=dict(level=level, arrival=arrival, history=history_mode, hospitals=count, patients=patients_n))
