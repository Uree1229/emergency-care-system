"""40차 환경: 수용 여부를 일부 예측할 수 있는 시나리오로 바꾼다.

기존 시나리오는 병원 수용이 환자와 무관한 무작위(병원별 평균만 다름)다. 실제로는 환자 상태와 병원 역량이 맞을수록
잘 받아 준다. 이 변환은 환자에게 질환 유형을, 병원에 유형별 적합도(강함·보통·약함)를 주고, 기존 잠재 회신의 수용 여부를
적합도에 따라 조정한다.

  강함: 원래 수용이면 그대로, 아니면 40% 확률로 수용으로 바꿈(수용률 약 0.36 → 0.62)
  보통: 그대로
  약함: 원래 수용이어도 20%만 수용으로 남김(약 0.36 → 0.07)

적합도 비율(30·40·30%)에서 전체 평균 수용률은 기존과 거의 같다. 무응답 여부와 검토 시간은 바꾸지 않는다.
모든 추첨은 (시나리오, 환자, 병원) 해시로 고정한다. 서버가 아는 적합도 표(knowledge)는 정확하거나 일부 틀릴 수 있다.
"""
from hashlib import sha256

TYPES = ('trauma', 'cardiac', 'stroke', 'general')
FIT_SHARES = (('strong', 0.3), ('normal', 0.4), ('weak', 0.3))
RAISE, KEEP = 0.4, 0.2


def _u(*parts):
    return int(sha256('|'.join(str(p) for p in parts).encode()).hexdigest()[:8], 16) / 0xFFFFFFFF


def _pick(u, shares):
    total = 0.0
    for value, share in shares:
        total += share
        if u < total:
            return value
    return shares[-1][0]


def patient_type(scenario_id, patient_id):
    return _pick(_u(scenario_id, patient_id, 'type'), tuple((t, 1 / len(TYPES)) for t in TYPES))


def fit(scenario_id, hospital_id, ptype):
    return _pick(_u(scenario_id, hospital_id, ptype, 'fit'), FIT_SHARES)


def predictable(scenario):
    """질환 유형·적합도를 붙이고 수용 여부를 적합도에 맞게 조정한 시나리오(원본은 바꾸지 않는다)."""
    sid = scenario['id']
    types = {p['id']: patient_type(sid, p['id']) for p in scenario['patients']}
    potentials = []
    for q in scenario['potentials']:
        q = dict(q)
        category = fit(sid, q['hospital_id'], types[q['patient_id']])
        u = _u(sid, q['patient_id'], q['hospital_id'], 'accept')
        if category == 'strong':
            q['accept'] = q['accept'] or u < RAISE
        elif category == 'weak':
            q['accept'] = q['accept'] and u < KEEP
        potentials.append(q)
    return dict(scenario, potentials=potentials, patients=[dict(p, ptype=types[p['id']]) for p in scenario['patients']])


def knowledge(scenario, error=0.0):
    """서버가 아는 (유형, 병원) → 적합도 표. error 비율만큼은 해시로 고른 무작위 적합도로 틀린다."""
    sid = scenario['id']
    table = {}
    for h in scenario['hospitals']:
        for t in TYPES:
            true = fit(sid, h['id'], t)
            wrong = _u(sid, h['id'], t, 'error') < error
            table[(t, h['id'])] = _pick(_u(sid, h['id'], t, 'noise'), FIT_SHARES) if wrong else true
    return dict(types={p['id']: p['ptype'] for p in scenario['patients']}, fit=table)
