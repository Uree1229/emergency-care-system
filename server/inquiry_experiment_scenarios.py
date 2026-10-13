"""문의 정책 비교용 합성 입력. 정책을 실행하기 전에 모든 잠재 응답을 만든다.

각 (시나리오, 환자, 병원)의 작업시간·무응답·수용 여부는 안정 해시로 정한
독립 난수열에서 생성한다. 문의 순서나 정책 이름은 생성 입력이 아니다.
workers와 potentials는 실험 환경만 읽고, 문의 정책에는 공개하지 않는다.
좌표와 이동시간도 합성값이며 실제 병원·도로·임상 자료를 재현하지 않는다.
"""
from hashlib import sha256
import random


DEFAULT_SEEDS = tuple(range(6101, 6109))
FAMILY_NAMES = (
    'immediate_all_accept',
    'delayed_near_fast_far',
    'no_response_near',
    'simultaneous_w1',
    'staggered_w2',
    'all_unavailable_control',
    'historical_shift',
    'history_helpful',
)
_DESCRIPTIONS = {
    'immediate_all_accept': '단일 환자, 모든 병원이 짧은 작업 후 수용. 추가 문의의 이득이 적은 대조군.',
    'delayed_near_fast_far': '단일 환자, 가까운 병원은 늦게 회신하고 조금 먼 병원은 빨리 회신하는 절충 상황.',
    'no_response_near': '단일 환자, 가장 가까운 두 병원은 작업 후 무응답. 나머지는 수용 가능.',
    'simultaneous_w1': '환자 8명이 동시에 발생. 병원별 문의 처리자 1명의 FIFO 대기열을 공유.',
    'staggered_w2': '환자 8명이 12초 간격으로 발생. 병원별 처리자 2명, 일부 무응답·불가 회신 포함.',
    'all_unavailable_control': '모든 병원이 수용 불가로 회신. 정책이 수용병원을 만들어낼 수 없는 대조군.',
    'historical_shift': '과거에는 먼 병원의 회신이 빨랐으나 현재는 가까운 병원이 빠른 상황. 과거 정보의 역효과 점검.',
    'history_helpful': '병원별 과거 회신 2건이 현재 작업시간 차이와 유사한 상황. 과거 정보가 유용한 조건 점검.',
}


def _rng(seed, family, *keys):
    # Python hash()는 프로세스마다 바뀌므로 사용하지 않는다. JSON 직렬화나
    # 생성 순서가 달라져도 동일한 키의 잠재 결과는 같다.
    token = '\x1f'.join(str(value) for value in (seed, family, *keys)).encode('utf-8')
    return random.Random(int.from_bytes(sha256(token).digest(), 'big'))


def _hospitals(family):
    workers = 2 if family == 'staggered_w2' else 1
    return [dict(id=f'H{index + 1:02d}', name=f'합성병원 {index + 1}',
                 lat=round(35.1595 + .006 * (index + 1), 6),
                 lon=round(126.8526 + .004 * (index + 1), 6),
                 capabilities=['응급의학'], workers=workers)
            for index in range(8)]


def _travel(seed, family, patient_id, hospital_id, index):
    if family == 'immediate_all_accept':
        base = 300 + index * 90
    elif family == 'delayed_near_fast_far':
        base = 300 + index * 45
    elif family in ('simultaneous_w1', 'staggered_w2'):
        base = 300 + index * 30
    elif family in ('historical_shift', 'history_helpful'):
        base = 300 + index * 20
    else:
        base = 300 + index * 60
    return base + _rng(seed, family, patient_id, hospital_id, 'travel').randint(-8, 8)


def _patients(seed, family, hospitals):
    count = 8 if family in ('simultaneous_w1', 'staggered_w2') else 1
    result = []
    for index in range(count):
        patient_id = f'P{index + 1:02d}'
        position = _rng(seed, family, patient_id, 'position')
        result.append(dict(id=patient_id,
                           at=index * 12 if family == 'staggered_w2' else 0,
                           lat=round(35.1595 + position.uniform(-.001, .001), 6),
                           lon=round(126.8526 + position.uniform(-.001, .001), 6),
                           travel_seconds={h['id']: _travel(seed, family, patient_id, h['id'], i)
                                           for i, h in enumerate(hospitals)}))
    return result


def _potential(seed, family, patient_id, hospital_id, index):
    rng = _rng(seed, family, patient_id, hospital_id, 'potential')
    no_response, accept = False, True
    if family == 'immediate_all_accept':
        service = rng.randint(8, 25)
    elif family == 'delayed_near_fast_far':
        service = rng.randint(125, 160) if index == 0 else rng.randint(12, 30)
    elif family == 'no_response_near':
        no_response = index < 2
        service = rng.randint(150, 210) if no_response else rng.randint(12, 40)
    elif family == 'simultaneous_w1':
        service = rng.randint(35, 70)
    elif family == 'staggered_w2':
        service = rng.randint(45, 90)
        no_response = rng.random() < .10
        accept = rng.random() >= .20
    elif family == 'all_unavailable_control':
        service, accept = rng.randint(15, 45), False
    elif family == 'historical_shift':
        service = rng.randint(12, 30) if index < 2 else rng.randint(125, 165)
    elif family == 'history_helpful':
        service = rng.randint(12, 30) if index in (3, 5) else rng.randint(125, 165)
    else:
        raise ValueError('unknown scenario family')
    return dict(patient_id=patient_id, hospital_id=hospital_id,
                service_seconds=service, no_response=no_response, accept=accept)


def _history(seed, family, hospitals):
    if family not in ('historical_shift', 'history_helpful'):
        return []
    rows = []
    for index, hospital in enumerate(hospitals):
        # 모두 종료된 과거 문의다. 현재 환자나 현재 병원 대기열을 미리 알리지
        # 않는다. 불가 회신도 '회신 속도' 표본에 들어가며 수용 확률은 아니다.
        fast = index in (3, 5)
        for sample in range(2):
            rng = _rng(seed, family, hospital['id'], sample, 'history')
            delay = rng.randint(12, 30) if fast else rng.randint(125, 165)
            sent = (-1500 + sample * 260 if family == 'historical_shift'
                    else -620 + sample * 260)
            rows.append(dict(patient_id=f'HIST_{hospital["id"]}_{sample + 1}',
                             hospital_id=hospital['id'], sent_at=sent,
                             reply_at=sent + delay, action='decline'))
    return sorted(rows, key=lambda row: (row['reply_at'], row['sent_at'],
                                         row['hospital_id'], row['patient_id']))


def make_suite(seeds=DEFAULT_SEEDS):
    """8개 사전 정의 조건 × seeds의 독립 입력 목록을 반환한다.

    horizon_seconds=300은 각 환자의 발생 이후 관찰 한도다. 실행기는 마지막
    환자 발생 시각 + 300초까지 실행한다. FIFO 문의 대기열과 실제 회신 시각은
    실행기가 정책이 보낸 요청을 받아 계산하므로 입력에 미래 회신 시각은 없다.
    과거 history의 음수 시각은 실행기의 T0 기준으로만 변환해야 한다.
    """
    if isinstance(seeds, (str, bytes)):
        raise ValueError('seeds must be an iterable of distinct nonnegative integers')
    try:
        seeds = tuple(seeds)
    except TypeError as exc:
        raise ValueError('seeds must be an iterable of distinct nonnegative integers') from exc
    if not seeds or any(type(seed) is not int or seed < 0 for seed in seeds) or len(set(seeds)) != len(seeds):
        raise ValueError('seeds must be distinct nonnegative integers')
    scenarios = []
    for family in FAMILY_NAMES:
        for seed in seeds:
            hospitals = _hospitals(family)
            patients = _patients(seed, family, hospitals)
            scenarios.append(dict(id=f'{family}-{seed}', family=family, seed=seed,
                                  description=_DESCRIPTIONS[family], hospitals=hospitals,
                                  patients=patients,
                                  potentials=[_potential(seed, family, p['id'], h['id'], index)
                                              for p in patients for index, h in enumerate(hospitals)],
                                  history=_history(seed, family, hospitals), horizon_seconds=300))
    return scenarios
