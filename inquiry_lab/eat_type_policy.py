"""EAT-K 질환 유형 변형(40차): 환자 유형과 병원 적합도로 병원별 수용 확률을 예측해 쓴다.

15차 eatks_seenbase_k10_m4는 병원별 수용률을 관측으로만 배운다(환자 유형과 무관). 이 변형은 서버가 아는 적합도 표
(병원 역량 등록·과거 기록)로 그 환자에 대한 병원별 수용 확률을 정한다.

  수용 확률 = 지역 전체 관측 수용률 × 적합도 배율(강함 1.75·보통 1.0·약함 0.2, 최대 0.95)

  eatkt: 위 예측을 쓰는 EAT-K. 적합도 표는 실험기가 시나리오마다 넣는다(set_knowledge).

서버 정책 함수는 환자 정보를 받지 않으므로, 실험기 안에서는 워크플로가 그 환자의 문의 목록을 읽는 함수(_inquiries)를
이 프로세스에서만 감싸 환자 ID를 기억해 둔다(서버 파일 무수정). 서버에 적용할 때는 워크플로가 환자 유형과
병원 역량 표를 정책에 넘기면 된다. 적합도 표가 없으면 15차 정책과 완전히 같다(test_inquiry_lab_predict).
"""
from collections.abc import Mapping
from copy import deepcopy

from server import inquiry_policies, inquiry_workflow

from . import eat_capacity_policy, eat_policy, eat_seen_policy

NAME = 'eatkt'
BASE = 'eatks_seenbase_k10_m4'
FACTORS = {'strong': 1.75, 'normal': 1.0, 'weak': 0.2}
_KNOWLEDGE = None
_LAST = {}


def set_knowledge(knowledge):
    """dict(types={patient: 유형}, fit={(유형, 병원): 적합도}) 또는 None."""
    global _KNOWLEDGE
    _KNOWLEDGE = knowledge


def spec(name):
    if name != NAME:
        raise ValueError('EAT-K type policy name must be eatkt')
    policy = eat_seen_policy.spec(BASE)
    policy['config'].update(type_aware=True)
    policy.update(name=name, label=f"{policy['label']} · 질환 유형별 수용 예측")
    return policy


def _beliefs_for(ptype, table):
    class TypeBeliefs(eat_policy.Beliefs):
        def accept(self, h):
            category = table.get((ptype, h))
            if category is None:
                return super().accept(h)
            return min(0.95, self.accept_global * FACTORS[category])
    return TypeBeliefs


def current_patient(patient_pending):
    own = [r for r in patient_pending if isinstance(r, Mapping)]
    if own and any(r.get('patient') != _LAST.get('patient') for r in own):
        raise RuntimeError('patient record does not match this decision')
    return _LAST.get('patient')


def decide(name, **kwargs):
    spec(name)
    policy = eat_seen_policy.spec(BASE)
    pid = current_patient(kwargs.get('patient_pending') or ())
    ptype = None if _KNOWLEDGE is None else _KNOWLEDGE['types'].get(pid)
    original_spec, original_beliefs = eat_capacity_policy.spec, eat_capacity_policy.Beliefs
    eat_capacity_policy.spec = lambda _name: deepcopy(policy)
    if ptype is not None:
        eat_capacity_policy.Beliefs = _beliefs_for(ptype, _KNOWLEDGE['fit'])
    try:
        return eat_capacity_policy.decide(BASE, **kwargs)
    finally:
        eat_capacity_policy.spec, eat_capacity_policy.Beliefs = original_spec, original_beliefs


def install():
    eat_seen_policy.install([BASE])
    inquiry_policies._POLICIES.setdefault(NAME, spec(NAME))
    if not getattr(inquiry_workflow._inquiries, '_eat_type', False):
        original = inquiry_workflow._inquiries

        def remembered(conn, pid):
            rows = original(conn, pid)
            _LAST.clear()
            _LAST.update(patient=pid)
            return rows
        remembered._eat_type, remembered._original = True, original
        inquiry_workflow._inquiries = remembered
    current = inquiry_policies.decide
    if not getattr(current, '_eat_type', False):
        def wrapped(name, **kwargs):
            if name == NAME:
                return decide(name, **kwargs)
            return current(name, **kwargs)
        wrapped._eat_type = True
        inquiry_policies.decide = wrapped


def uninstall():
    inquiry_policies._POLICIES.pop(NAME, None)
    reader = inquiry_workflow._inquiries
    if getattr(reader, '_eat_type', False):
        inquiry_workflow._inquiries = reader._original
    _LAST.clear()
    set_knowledge(None)
    eat_seen_policy.uninstall()
