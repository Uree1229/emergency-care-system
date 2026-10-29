"""41차: 서버가 자기 과거 문의 기록에서 (질환 유형, 병원)별 수용률을 배운다.

40차는 서버가 병원의 유형별 적합도를 정확히 안다고 가정했다(병원 역량 등록). 이 모듈은 등록 정보 없이, 같은 병원들에 쌓인
지난 문의 기록(유형·수용 여부)으로 수용률을 추정한다.

- 숨은 병원값(hidden): 시나리오 생성기가 쓰는 같은 난수 흐름을 다시 돌려 병원별 회신률·수용률을 얻는다(생성기 무수정).
- 참 수용 확률(true_prob): 40차 환경(predictable_scenarios)과 같은 규칙. 강함 a + (1 − a)·0.4, 보통 a, 약함 a·0.2.
- 과거 기록(past_records): 병원마다 회신이 온 지난 문의 n건. 유형은 4가지 중 고르게, 수용 여부는 참 확률로 해시 추첨한다.
- 학습(learned_table): 병원 전체 수용률을 지역 사전값 쪽으로, (유형, 병원) 수용률을 그 병원 전체 쪽으로 당겨 추정한다(계층 축소).
"""
from hashlib import sha256

from . import mass_scenarios, predictable_scenarios as ps, scenarios

PRIOR, K_HOSPITAL, K_TYPE = 0.4, 4.0, 4.0


def _u(*parts):
    return int(sha256('|'.join(str(p) for p in parts).encode()).hexdigest()[:8], 16) / 0xFFFFFFFF


def hidden(scenario):
    """생성기가 숨겨 둔 병원별 회신률·수용률. 생성기와 같은 순서(회신률 → 수용률)로 같은 난수를 뽑는다."""
    rng_of = mass_scenarios._rng if scenario['family'] == mass_scenarios.MASS_FAMILY else scenarios._rng
    accept_mean = scenarios.ACCEPT_LEVELS[scenario['mix']['level']]
    out = {}
    for h in scenario['hospitals']:
        h_rng = rng_of(scenario['seed'], 'hospital', h['id'])
        reply = scenarios._beta(h_rng, scenarios.REPLY_MEAN, scenarios.REPLY_CONCENTRATION)
        accept = scenarios._beta(h_rng, accept_mean, 4.0)
        out[h['id']] = dict(reply=reply, accept=accept)
    return out


def true_prob(accept, category):
    if category == 'strong':
        return accept + (1 - accept) * ps.RAISE
    if category == 'weak':
        return accept * ps.KEEP
    return accept


def past_records(scenario, n):
    """병원마다 회신이 온 지난 문의 n건: (유형, 병원, 수용 여부)."""
    sid, params = scenario['id'], hidden(scenario)
    rows = []
    for h in scenario['hospitals']:
        for k in range(n):
            ptype = ps.TYPES[min(len(ps.TYPES) - 1, int(_u(sid, h['id'], k, 'past-type') * len(ps.TYPES)))]
            p = true_prob(params[h['id']]['accept'], ps.fit(sid, h['id'], ptype))
            rows.append((ptype, h['id'], _u(sid, h['id'], k, 'past-accept') < p))
    return rows


def learned_table(records, hospital_ids):
    """(유형, 병원) → 추정 수용률. 기록이 적은 칸은 병원 전체, 병원 전체는 지역 사전값 쪽으로 당긴다."""
    by_h, by_th = {}, {}
    for ptype, hid, accepted in records:
        a, n = by_h.get(hid, (0, 0))
        by_h[hid] = (a + accepted, n + 1)
        a, n = by_th.get((ptype, hid), (0, 0))
        by_th[(ptype, hid)] = (a + accepted, n + 1)
    table = {}
    for hid in hospital_ids:
        a, n = by_h.get(hid, (0, 0))
        p_h = (a + K_HOSPITAL * PRIOR) / (n + K_HOSPITAL)
        for ptype in ps.TYPES:
            a, n = by_th.get((ptype, hid), (0, 0))
            table[(ptype, hid)] = (a + K_TYPE * p_h) / (n + K_TYPE)
    return table
