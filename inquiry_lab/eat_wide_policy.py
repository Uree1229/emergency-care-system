"""EAT-W: 후보 범위를 넓힌 EAT 변형(12차).

1차 EAT(eat_l15_f)는 아직 묻지 않은 병원 중 가까운 6곳만 계획에 넣고, 한 환자의 동시 문의를 4곳으로 제한한다.
여러 환자가 한꺼번에 몰리면 가까운 6곳이 모두 붐벼도 그 밖의 한산한 병원을 고려하지 못한다.
EAT-W는 계산은 1차 EAT와 같고 두 상한만 바꾼다.

  eatw_k{K}_m{M}: 계획에 넣는 가까운 병원 K곳, 동시 문의 M곳(1차 EAT는 k6_m4).

계산은 1차 잠금 정책 `eat_policy.decide`를 그대로 부르고, 그동안만 명세를 이 변형으로 바꾼다.
한 프로세스 안에서 순서대로 실행되는 실험기에서만 쓴다. eatw_k6_m4는 1차 EAT와 결과가 같다(test로 확인).
"""
from copy import deepcopy
import re

from server import inquiry_policies

from . import eat_policy

PREFIX = 'eatw_'
_NAME = re.compile(PREFIX + r'k(\d+)_m(\d+)')


def variant_name(k, m):
    return f'{PREFIX}k{k}_m{m}'


def spec(name):
    match = _NAME.fullmatch(name) if isinstance(name, str) else None
    if match is None or int(match.group(1)) < 1 or int(match.group(2)) < 1:
        raise ValueError('EAT-W policy name must look like eatw_k10_m4')
    k, m = int(match.group(1)), int(match.group(2))
    policy = eat_policy.spec('eat_l15_f')
    policy['config'].update(max_candidates_evaluated=k, max_concurrent=m)
    policy.update(name=name, label=f'EAT-W (가까운 병원 {k}곳 계획, 동시 문의 {m}곳)')
    return policy


def decide(name, **kwargs):
    policy = spec(name)
    original = eat_policy.spec
    eat_policy.spec = lambda _name: deepcopy(policy)
    try:
        return eat_policy.decide(name, **kwargs)
    finally:
        eat_policy.spec = original


def install(names):
    from . import harness
    harness.install(['eat_l15_f'])
    for name in names:
        inquiry_policies._POLICIES.setdefault(name, spec(name))
    current = inquiry_policies.decide
    if not getattr(current, '_eat_wide', False):
        def wrapped(name, **kwargs):
            if name.startswith(PREFIX):
                return decide(name, **kwargs)
            return current(name, **kwargs)
        wrapped._eat_wide = True
        inquiry_policies.decide = wrapped


def uninstall():
    from . import harness
    for name in [n for n in inquiry_policies._POLICIES if n.startswith(PREFIX)]:
        inquiry_policies._POLICIES.pop(name, None)
    harness.uninstall()
