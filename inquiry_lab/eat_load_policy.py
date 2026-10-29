"""EAT-K 부하 적응 변형(29차): 지역이 한가할 때만 동시 문의 상한을 올린다.

14차에서 동시 문의 상한을 항상 6곳으로 올리면 실패는 줄지만 다른 환자의 대기가 늘어 J가 나아지지 않았다.
문의가 겹치는 다른 환자가 거의 없을 때는 그 부작용이 작다. 이 변형은 '대기 중인 문의가 있는 환자 수(자기 포함)'가
N명 이하일 때만 상한을 4곳 → M곳으로 올린다. 나머지 계산은 15차 `eatks_seenbase_k10_m4`와 같다.

  eatkc_c{N}_m{M}: 대기 환자 N명 이하이면 동시 M곳, 아니면 4곳

부하는 정책이 이미 받는 전체 미해결 문의 목록(pending)만으로 센다. 서버 변경 없이 정책 레지스트리에 넣을 수 있다.
M = 4이면 15차 정책과 완전히 같다(test_inquiry_lab_calm).
"""
from collections.abc import Mapping
from copy import deepcopy
import re

from server import inquiry_policies

from . import eat_capacity_policy, eat_seen_policy

PREFIX = 'eatkc_'
BASE = 'eatks_seenbase_k10_m4'
_NAME = re.compile(PREFIX + r'c(\d+)_m(\d+)')


def parse(name):
    match = _NAME.fullmatch(name) if isinstance(name, str) else None
    if match is None or int(match.group(1)) < 1 or int(match.group(2)) < 1:
        raise ValueError('EAT-K load policy name must look like eatkc_c2_m6')
    return int(match.group(1)), int(match.group(2))


def spec(name):
    calm, cap = parse(name)
    policy = eat_seen_policy.spec(BASE)
    policy['config'].update(calm_patients=calm, calm_max_concurrent=cap)
    policy.update(name=name, label=f"{policy['label']} · 대기 환자 {calm}명 이하이면 동시 {cap}곳")
    return policy


def load(pending=(), patient_pending=()):
    """대기 중인 문의가 있는 환자 수. 자기 문의가 아직 없으면 자기를 한 명 더 센다."""
    patients = {r.get('patient') for r in pending if isinstance(r, Mapping)}
    patients.discard(None)
    own = [r for r in patient_pending if isinstance(r, Mapping)]
    return len(patients) + (0 if own else 1)


def decide(name, **kwargs):
    calm, cap = parse(name)
    policy = eat_seen_policy.spec(BASE)
    if load(kwargs.get('pending') or (), kwargs.get('patient_pending') or ()) <= calm:
        policy['config']['max_concurrent'] = cap
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
    if not getattr(current, '_eat_load', False):
        def wrapped(name, **kwargs):
            if isinstance(name, str) and name.startswith(PREFIX):
                return decide(name, **kwargs)
            return current(name, **kwargs)
        wrapped._eat_load = True
        inquiry_policies.decide = wrapped


def uninstall():
    for name in [n for n in inquiry_policies._POLICIES if n.startswith(PREFIX)]:
        inquiry_policies._POLICIES.pop(name, None)
    eat_seen_policy.uninstall()
