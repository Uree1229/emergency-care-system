"""EAT-K 마감 인지 변형(25차): 남은 시간을 환자의 첫 문의 시각부터 세고, 마감이 가까우면 동시 문의 상한을 올린다.

서버 워크플로는 정책에 마지막 문의 시각(last_contact_at)만 넘긴다. EAT는 경과 시간을 '대기 중인 자기 문의의 가장 이른
제출 시각'과 '마지막 문의 시각' 중 이른 쪽부터 센다. 앞선 문의가 모두 거절·종료되면 경과 시간을 실제보다 짧게 보고,
관측 한도(300초)가 가까워도 여유가 있다고 판단한다. 또 동시 문의 상한(4곳)에 막혀 마감 직전에도 더 묻지 못한다.

  eatkf_seenbase_k10_m4: eatks_seenbase_k10_m4와 같고 경과 시간만 첫 문의부터 센다.
  eatkd_t{T}_m{M}: eatkf_seenbase_k10_m4에서 남은 시간이 T초 이하인 환자만 동시 문의 상한을 4곳 → M곳으로 올린다.

나머지 계산은 15차 `eatks_*`와 같다. 서버에 적용할 때는 워크플로가 정책에 탐색 시작 시각(inquiry_workflows.started_at)이나
첫 문의 시각을 넘기면 된다. 실험기 안에서는 워크플로가 그 환자의 문의 목록을 읽는 함수(_inquiries)를 이 프로세스에서만
감싸 첫 문의 시각을 기억해 둔다. 서버 파일은 수정하지 않는다.
"""
from collections.abc import Mapping
from copy import deepcopy
import re

from server import inquiry_policies, inquiry_workflow

from . import eat_capacity_policy, eat_seen_policy

PREFIXES = ('eatkf_', 'eatkd_')
DEADLINE_BASE = 'eatks_seenbase_k10_m4'
_FIRST = re.compile(r'eatkf_(seen|seenbase)_k(\d+)_m(\d+)')
_DEADLINE = re.compile(r'eatkd_t(\d+)_m(\d+)')
_LAST = {}


def parse(name):
    """(15차 기준 정책 이름, 마감 창 T초, 마감 임박 동시 상한 M). 마감 창이 0이면 상한을 바꾸지 않는다."""
    match = _FIRST.fullmatch(name) if isinstance(name, str) else None
    if match is not None and int(match.group(2)) >= 1 and int(match.group(3)) >= 1:
        return eat_seen_policy.PREFIX + name[len('eatkf_'):], 0, None
    match = _DEADLINE.fullmatch(name) if isinstance(name, str) else None
    if match is not None:
        window, cap = int(match.group(1)), int(match.group(2))
        if 1 <= window <= 300 and cap > eat_seen_policy.spec(DEADLINE_BASE)['config']['max_concurrent']:
            return DEADLINE_BASE, window, cap
    raise ValueError('EAT-K deadline policy name must look like eatkf_seenbase_k10_m4 or eatkd_t120_m6')


def spec(name):
    base, window, cap = parse(name)
    policy = eat_seen_policy.spec(base)
    label = policy['label'].replace('EAT-K 추정', 'EAT-K 추정·첫 문의부터 경과 시간', 1)
    if window:
        label += f' · 마감 {window}초 전부터 동시 {cap}곳'
        policy['config'].update(deadline_window_seconds=window, deadline_max_concurrent=cap)
    policy.update(name=name, label=label)
    return policy


def first_contact(patient_pending):
    """방금 워크플로가 읽은 환자의 첫 문의 시각. 대기 중인 문의가 다른 환자 것이면 오류로 본다."""
    own = [r for r in patient_pending if isinstance(r, Mapping)]
    if own and any(r.get('patient') != _LAST.get('patient') for r in own):
        raise RuntimeError('first-contact record does not match this patient')
    return _LAST.get('first')


def decide(name, *, now, patient_pending=(), last_contact_at=None, **kwargs):
    base, window, cap = parse(name)
    first = first_contact(patient_pending)
    if first is not None and (last_contact_at is None or first < last_contact_at):
        last_contact_at = first
    policy = eat_seen_policy.spec(base)
    starts = [r['submitted_at'] for r in patient_pending if isinstance(r, Mapping) and isinstance(r.get('submitted_at'), (int, float))]
    if last_contact_at is not None:
        starts.append(last_contact_at)
    elapsed = now - min(starts) if starts else 0
    if window and policy['deadline_seconds'] - elapsed <= window:
        policy['config']['max_concurrent'] = cap
    original = eat_capacity_policy.spec
    eat_capacity_policy.spec = lambda _name: deepcopy(policy)
    try:
        return eat_capacity_policy.decide(base, now=now, patient_pending=patient_pending, last_contact_at=last_contact_at, **kwargs)
    finally:
        eat_capacity_policy.spec = original


def install(names):
    eat_seen_policy.install(sorted({parse(n)[0] for n in names}))
    for name in names:
        inquiry_policies._POLICIES.setdefault(name, spec(name))
    if not getattr(inquiry_workflow._inquiries, '_eat_first', False):
        original = inquiry_workflow._inquiries

        def remembered(conn, pid):
            rows = original(conn, pid)
            _LAST.clear()
            _LAST.update(patient=pid, first=min((r['created_at'] for r in rows), default=None))
            return rows
        remembered._eat_first, remembered._original = True, original
        inquiry_workflow._inquiries = remembered
    current = inquiry_policies.decide
    if not getattr(current, '_eat_first', False):
        def wrapped(name, **kwargs):
            if isinstance(name, str) and name.startswith(PREFIXES):
                return decide(name, **kwargs)
            return current(name, **kwargs)
        wrapped._eat_first = True
        inquiry_policies.decide = wrapped


def uninstall():
    for name in [n for n in inquiry_policies._POLICIES if n.startswith(PREFIXES)]:
        inquiry_policies._POLICIES.pop(name, None)
    reader = inquiry_workflow._inquiries
    if getattr(reader, '_eat_first', False):
        inquiry_workflow._inquiries = reader._original
    _LAST.clear()
    eat_seen_policy.uninstall()
