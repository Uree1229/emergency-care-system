"""EAT-K 병원이 적은 지역 적응 변형(38차): 문의할 수 있는 병원이 N곳 이하이면 문의비용 λ로 한 번에 모두 묻는다.

37차 농어촌형(병원 4곳)에서 EAT는 문의 비용을 아껴 고정3보다 적게 물었고(환자당 3.47 vs 3.80곳), 선정 실패가 14명 더 많았다.
받아 줄 병원이 귀한 지역에서는 한 번에 모두 묻는 편이 나을 수 있다.

  eatkr_n{N}_l{λ}: 그 환자가 문의할 수 있는 병원(남은 후보 + 이미 문의한 곳)이 N곳 이하이면 λ초·동시 N곳, 아니면 15차 eatks_seenbase_k10_m4

병원 수는 정책이 이미 받는 값(남은 후보 목록, 문의 한도에서 남은 수 max_new)으로 계산하므로 서버 변경이 필요 없다.
병원이 N곳보다 많으면 15차 정책과 완전히 같다(test_inquiry_lab_sparse).
"""
from copy import deepcopy
import re

from server import inquiry_policies

from . import eat_capacity_policy, eat_seen_policy

PREFIX = 'eatkr_'
BASE = 'eatks_seenbase_k10_m4'
_NAME = re.compile(PREFIX + r'n(\d+)_l(\d+)')


def parse(name):
    match = _NAME.fullmatch(name) if isinstance(name, str) else None
    if match is None or int(match.group(1)) < 1:
        raise ValueError('EAT-K sparse policy name must look like eatkr_n4_l0')
    return int(match.group(1)), int(match.group(2))


def spec(name):
    n, lam = parse(name)
    policy = eat_seen_policy.spec(BASE)
    policy['config'].update(sparse_hospitals=n, sparse_lambda_seconds=lam)
    policy.update(name=name, label=f"{policy['label']} · 문의할 병원 {n}곳 이하이면 λ{lam}·모두 한 번에")
    return policy


def hospitals_for_patient(policy, candidates, max_new):
    """그 환자가 문의할 수 있는 병원 수 = 아직 묻지 않은 후보 + 이미 문의한 곳(문의 한도 − 남은 수)."""
    return len(candidates or ()) + policy['max_contacts'] - max_new


def decide(name, **kwargs):
    n, lam = parse(name)
    policy = eat_seen_policy.spec(BASE)
    if hospitals_for_patient(policy, kwargs.get('candidates'), kwargs['max_new']) <= n:
        policy['config'].update(lambda_seconds=lam, max_concurrent=n)
    original = eat_capacity_policy.spec
    eat_capacity_policy.spec = lambda _name: deepcopy(policy)
    try:
        return eat_capacity_policy.decide(BASE, **kwargs)
    finally:
        eat_capacity_policy.spec = original


def install(names):
    eat_seen_policy.install([BASE])
    for name in names:
        inquiry_policies._POLICIES.setdefault(name, spec(name))
    current = inquiry_policies.decide
    if not getattr(current, '_eat_sparse', False):
        def wrapped(name, **kwargs):
            if isinstance(name, str) and name.startswith(PREFIX):
                return decide(name, **kwargs)
            return current(name, **kwargs)
        wrapped._eat_sparse = True
        inquiry_policies.decide = wrapped


def uninstall():
    for name in [n for n in inquiry_policies._POLICIES if n.startswith(PREFIX)]:
        inquiry_policies._POLICIES.pop(name, None)
    eat_seen_policy.uninstall()
