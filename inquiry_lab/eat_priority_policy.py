"""EAT-K 중증도 변형(32차): 중증도 높은 환자에게만 더 적극적으로 문의한다.

  eatkp_l{λ}_m{M}: 서버의 effective_priority가 4(높음)인 환자는 문의비용 λ·동시 문의 M곳, 그 밖은 15차 eatks_seenbase_k10_m4

서버 정책 함수는 환자 정보를 받지 않는다. 실험기 안에서는 워크플로가 그 환자의 문의 목록을 읽는 함수(_inquiries)를
이 프로세스에서만 감싸, 그때 환자 행에서 중증도를 읽어 둔다(서버 파일 무수정). 서버에 적용할 때는 워크플로가
정책에 effective_priority를 넘기면 된다. 중증도가 높은 환자가 없으면 15차 정책과 완전히 같다(test_inquiry_lab_priority).
"""
from collections.abc import Mapping
from copy import deepcopy
import re

from server import inquiry_policies, inquiry_workflow, service

from . import eat_capacity_policy, eat_seen_policy

PREFIX = 'eatkp_'
BASE = 'eatks_seenbase_k10_m4'
_NAME = re.compile(PREFIX + r'l(\d+)_m(\d+)')
_LAST = {}


def parse(name):
    match = _NAME.fullmatch(name) if isinstance(name, str) else None
    if match is None or int(match.group(2)) < 1:
        raise ValueError('EAT-K priority policy name must look like eatkp_l0_m6')
    return int(match.group(1)), int(match.group(2))


def spec(name):
    lam, cap = parse(name)
    policy = eat_seen_policy.spec(BASE)
    policy['config'].update(high_priority_lambda_seconds=lam, high_priority_max_concurrent=cap)
    policy.update(name=name, label=f"{policy['label']} · 중증도 높음이면 λ{lam}·동시 {cap}곳")
    return policy


def current_priority(patient_pending):
    """방금 워크플로가 읽은 환자의 effective_priority. 대기 중인 문의가 다른 환자 것이면 오류로 본다."""
    own = [r for r in patient_pending if isinstance(r, Mapping)]
    if own and any(r.get('patient') != _LAST.get('patient') for r in own):
        raise RuntimeError('priority record does not match this patient')
    return _LAST.get('priority')


def decide(name, **kwargs):
    lam, cap = parse(name)
    policy = eat_seen_policy.spec(BASE)
    if current_priority(kwargs.get('patient_pending') or ()) == 4:
        policy['config'].update(lambda_seconds=lam, max_concurrent=cap)
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
    if not getattr(inquiry_workflow._inquiries, '_eat_priority', False):
        original = inquiry_workflow._inquiries

        def remembered(conn, pid):
            rows = original(conn, pid)
            row = conn.execute('SELECT priority, priority_source FROM patients WHERE id=?', (pid,)).fetchone()
            _LAST.clear()
            _LAST.update(patient=pid, priority=None if row is None else service.effective_priority(
                dict(priority=row[0], priority_source=row[1])))
            return rows
        remembered._eat_priority, remembered._original = True, original
        inquiry_workflow._inquiries = remembered
    current = inquiry_policies.decide
    if not getattr(current, '_eat_priority', False):
        def wrapped(name, **kwargs):
            if isinstance(name, str) and name.startswith(PREFIX):
                return decide(name, **kwargs)
            return current(name, **kwargs)
        wrapped._eat_priority = True
        inquiry_policies.decide = wrapped


def uninstall():
    for name in [n for n in inquiry_policies._POLICIES if n.startswith(PREFIX)]:
        inquiry_policies._POLICIES.pop(name, None)
    reader = inquiry_workflow._inquiries
    if getattr(reader, '_eat_priority', False):
        inquiry_workflow._inquiries = reader._original
    _LAST.clear()
    eat_seen_policy.uninstall()
