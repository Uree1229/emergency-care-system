"""EAT-K 재조정 변형(14차): 지금 처리 인원을 아는 EAT(eatk_live_k10)의 문의비용 λ와 동시 문의 수만 바꾼다.

1차 EAT의 λ=15·동시 4곳은 처리 인원을 1명으로 보던 정책에서 고른 값이다. 처리 인원을 알면 대기 비용을
덜 과대평가하므로, 더 적극적인 설정(λ↓, 동시 문의↑)이 나을 수 있다.

  eatkt_l{λ}_m{M}: eatk_live_k10_m{M}에서 λ만 바꾼다. eatkt_l15_m4는 13차 eatk_live_k10_m4와 같다(test로 확인).

계산은 13차 `eat_capacity_policy.decide`를 그대로 부르고, 그동안만 명세를 이 변형으로 바꾼다.
처리 인원 표는 13차 실행기(`aware_runner`)가 넣는다. 한 프로세스 안에서 순서대로 실행되는 실험기에서만 쓴다.
"""
from copy import deepcopy
import re

from server import inquiry_policies

from . import eat_capacity_policy

PREFIX = 'eatkt_'
_NAME = re.compile(PREFIX + r'l(\d+)_m(\d+)')


def variant_name(lam, m):
    return f'{PREFIX}l{lam}_m{m}'


def spec(name):
    match = _NAME.fullmatch(name) if isinstance(name, str) else None
    if match is None or int(match.group(2)) < 1:
        raise ValueError('EAT-K tuned policy name must look like eatkt_l0_m6')
    lam, m = int(match.group(1)), int(match.group(2))
    policy = eat_capacity_policy.spec(eat_capacity_policy.variant_name('live', 10, m))
    policy['config']['lambda_seconds'] = lam
    policy.update(name=name, label=f'EAT-K 재조정 (지금 처리 인원, 가까운 병원 10곳, 동시 {m}곳, λ={lam}초)')
    return policy


def decide(name, **kwargs):
    policy = spec(name)
    original = eat_capacity_policy.spec
    eat_capacity_policy.spec = lambda _name: deepcopy(policy)
    try:
        return eat_capacity_policy.decide(name, **kwargs)
    finally:
        eat_capacity_policy.spec = original


def install(names):
    from . import harness
    harness.install(['eat_l15_f'])
    for name in names:
        inquiry_policies._POLICIES.setdefault(name, spec(name))
    current = inquiry_policies.decide
    if not getattr(current, '_eat_tuned', False):
        def wrapped(name, **kwargs):
            if name.startswith(PREFIX):
                return decide(name, **kwargs)
            return current(name, **kwargs)
        wrapped._eat_tuned = True
        inquiry_policies.decide = wrapped


def uninstall():
    from . import harness
    for name in [n for n in inquiry_policies._POLICIES if n.startswith(PREFIX)]:
        inquiry_policies._POLICIES.pop(name, None)
    harness.uninstall()
