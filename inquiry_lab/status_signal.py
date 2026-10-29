"""5차 실험 환경: 병원 상태로 생기는 무응답과 '현재 문의 응대 가능/불가' 상태 표시(합성).

1~4차 혼합 환경은 무응답이 문의마다 독립이라, 병원 상태를 알아도 개별 무응답을 예측할 수 없었다.
현실의 무응답은 대개 병원 상태(소생술 중·교대·담당자 부재)에서 생긴다고 보고 다음처럼 바꾼다.

- 병원마다 시나리오 동안 '응대 불가(off)' 상태일 확률을 둔다. off 병원의 문의는 무응답이다.
  응대 가능(on)이어도 3%는 무응답이다. 병원별 전체 무응답률은 혼합 환경의 1 − 회신률과 같게 맞춘다
  (평균 약 20.7%, 충북 ICT-EMS 공개 수치).
- 처리시간·수용 여부·이동시간·도착 형태는 `scenarios.make_mix(seed)`와 같다. 무응답만 상태로 다시 정한다.
- 상태 표시 품질(시나리오 단위, 정책은 모름): 정확함 / 30% 틀림(갱신을 잊음) / 미사용 / 혼합.
  표시를 쓰는 병원도 10%는 표시가 없다.
상태 지속 시간·표시 정확도는 설계 가정이며 실제 병원 자료로 보정한 값이 아니다.
"""
from hashlib import sha256
import random

from . import public_signal, scenarios

RESIDUAL_SILENCE = 0.03
QUALITIES = ('accurate', 'noisy', 'absent', 'mixed')
NOISY_FLIP = 0.30
MISSING_PROBABILITY = 0.10


def _rng(*keys):
    token = '\x1f'.join(str(k) for k in ('status-signal', *keys)).encode('utf-8')
    return random.Random(int.from_bytes(sha256(token).digest(), 'big'))


def hospital_states(seed):
    """병원별 숨은 상태. 상태 무응답 + 잔여 무응답이 그 병원의 1 − 회신률이 되도록 off 확률을 정한다."""
    hidden = public_signal.hidden_parameters(seed)
    rng = _rng('state', seed)
    states = {}
    for hid, hp in hidden['hospitals'].items():
        target = 1.0 - hp['reply']
        p_off = min(1.0, max(0.0, (target - RESIDUAL_SILENCE) / (1.0 - RESIDUAL_SILENCE)))
        states[hid] = 'off' if rng.random() < p_off else 'on'
    return states


def make_state_mix(seed):
    scenario = scenarios.make_mix(seed)
    states = hospital_states(seed)
    for p in scenario['potentials']:
        residual = _rng('residual', seed, p['patient_id'], p['hospital_id']).random() < RESIDUAL_SILENCE
        p['no_response'] = states[p['hospital_id']] == 'off' or residual
    scenario.update(id=f'state_mix-{seed}', family='state_mix',
                    description=scenario['description'] + ' · 상태 기반 무응답')
    scenario['mix'] = dict(scenario['mix'], silence='state')
    return scenario, states


def family_states(scenario):
    """기존 8유형: 모든 환자에게 무응답인 병원을 off로 본다(정확한 표시의 스트레스 시험)."""
    silent = {}
    for p in scenario['potentials']:
        silent.setdefault(p['hospital_id'], []).append(p['no_response'])
    return {hid: 'off' if all(v) else 'on' for hid, v in silent.items()}


def status_signal(key, states, quality=None):
    """정책이 보는 상태 표시. quality는 시나리오 단위 표지이며 정책에는 넘기지 않는다."""
    rng = _rng('status', key)
    quality = quality or rng.choice(QUALITIES)
    out = {}
    for hid in sorted(states):
        kind = quality if quality != 'mixed' else rng.choice(('accurate', 'noisy', 'absent'))
        missing = rng.random() < MISSING_PROBABILITY
        flip = rng.random() < NOISY_FLIP
        if kind == 'absent' or missing:
            continue
        shown = states[hid]
        if kind == 'noisy' and flip:
            shown = 'off' if shown == 'on' else 'on'
        out[hid] = dict(status='available' if shown == 'on' else 'unavailable', kind=kind)
    return dict(quality=quality, hospitals=out)


def policy_view(signal):
    return {hid: row['status'] for hid, row in signal['hospitals'].items()}


def calibration(seeds):
    """개발 데이터에서 표시별 회신 확률 P(회신 | 표시)를 센다.

    pooled: 표시가 있는 모든 품질(정확·틀림·혼합)을 합친 값. 표시의 평균적 신뢰도를 반영한다.
    trusted: 정확한 표시만으로 센 값. 병원 표시가 늘 정확하다고 믿는 설계에 해당한다.
    """
    out = {}
    for source in ('pooled', 'trusted'):
        counts = {'available': [0, 0], 'unavailable': [0, 0]}
        for seed in seeds:
            scenario, states = make_state_mix(seed)
            signal = status_signal(seed, states)['hospitals']
            for p in scenario['potentials']:
                row = signal.get(p['hospital_id'])
                if row is None or (source == 'trusted' and row['kind'] != 'accurate'):
                    continue
                c = counts[row['status']]
                c[0] += 0 if p['no_response'] else 1
                c[1] += 1
        out[source] = {status: dict(reply_probability=round((c[0] + 1) / (c[1] + 2), 6), samples=c[1])
                       for status, c in counts.items()}
    return out
