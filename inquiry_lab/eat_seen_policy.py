"""EAT-K 추정 변형(15차): 병원별 처리 인원을 '지금 검토 중인 문의 수'로 추정한다.

13차 EAT-K(`eatk_live_k10_m4`)는 병원 화면이 지금 처리 인원(접속 검토자 수·보조 합류)을 알려 준다고 가정한다.
대기 문의가 있을 때는 모든 검토자가 일하므로 '지금 검토 중인 문의 수 = 처리 인원'이다. 10차 문의함 정렬에
필요한 열람(검토 시작) 기록만 있으면 이 수를 셀 수 있다.

  eatks_seen_k10_m4: 처리 인원 = max(1, 지금 검토 중인 문의 수)
  eatks_seenbase_k10_m4: 처리 인원 = max(평소 인원, 지금 검토 중인 문의 수)

계산은 13차 `eat_capacity_policy.decide`를 그대로 부르고, 그동안만 명세의 capacity_mode를 바꾼다.
처리 인원 표(seen·seenbase 열)는 15차 실행기(`seen_runner`)가 넣는다.
"""
from copy import deepcopy
import re

from server import inquiry_policies

from . import eat_capacity_policy

PREFIX = 'eatks_'
MODES = ('seen', 'seenbase')
_NAME = re.compile(PREFIX + r'(seen|seenbase)_k(\d+)_m(\d+)')


def variant_name(mode, k, m):
    return f'{PREFIX}{mode}_k{k}_m{m}'


def spec(name):
    match = _NAME.fullmatch(name) if isinstance(name, str) else None
    if match is None or int(match.group(2)) < 1 or int(match.group(3)) < 1:
        raise ValueError('EAT-K seen policy name must look like eatks_seen_k10_m4')
    mode, k, m = match.group(1), int(match.group(2)), int(match.group(3))
    policy = eat_capacity_policy.spec(eat_capacity_policy.variant_name('live', k, m))
    policy['config']['capacity_mode'] = mode
    label = '지금 검토 중인 문의 수' if mode == 'seen' else '평소 인원과 검토 중인 문의 수 중 큰 값'
    policy.update(name=name, label=f'EAT-K 추정 (처리 인원 = {label}, 가까운 병원 {k}곳, 동시 {m}곳)')
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
    if not getattr(current, '_eat_seen', False):
        def wrapped(name, **kwargs):
            if name.startswith(PREFIX):
                return decide(name, **kwargs)
            return current(name, **kwargs)
        wrapped._eat_seen = True
        inquiry_policies.decide = wrapped


def uninstall():
    from . import harness
    for name in [n for n in inquiry_policies._POLICIES if n.startswith(PREFIX)]:
        inquiry_policies._POLICIES.pop(name, None)
    harness.uninstall()
