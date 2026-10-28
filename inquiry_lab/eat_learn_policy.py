"""EAT-K 학습형 변형(41차): 과거 문의 기록에서 배운 (질환 유형, 병원)별 수용률을 그 환자의 수용 확률로 쓴다.

40차 eatkt는 병원 적합도 등록 정보(강함·보통·약함)와 배율을 썼다. 이 변형은 등록 정보 없이 learned_fit.learned_table이
과거 기록으로 추정한 수용률을 그대로 쓴다.

  eatkl: 표(learned_table)가 있으면 수용 확률 = 표[(그 환자 유형, 병원)], 없으면 15차 eatks_seenbase_k10_m4

서버 정책 함수는 환자 정보를 받지 않으므로, 실험기 안에서는 워크플로가 그 환자의 문의 목록을 읽는 함수(_inquiries)를
이 프로세스에서만 감싸 환자 ID를 기억해 둔다(서버 파일 무수정). 표가 없으면 15차 정책과 완전히 같다(test_inquiry_lab_learn).
"""
from collections.abc import Mapping
from copy import deepcopy

from server import inquiry_policies, inquiry_workflow

from . import eat_capacity_policy, eat_policy, eat_seen_policy

NAME = 'eatkl'
BASE = 'eatks_seenbase_k10_m4'
_KNOWLEDGE = None
_LAST = {}


def set_knowledge(knowledge):
    """dict(types={patient: 유형}, prob={(유형, 병원): 수용률}) 또는 None."""
    global _KNOWLEDGE
    _KNOWLEDGE = knowledge


def spec(name):
    if name != NAME:
        raise ValueError('EAT-K learned policy name must be eatkl')
    policy = eat_seen_policy.spec(BASE)
    policy['config'].update(learned_acceptance=True)
    policy.update(name=name, label=f"{policy['label']} · 과거 기록으로 배운 유형별 수용률")
    return policy


def _beliefs_for(ptype, table):
    class LearnedBeliefs(eat_policy.Beliefs):
        def accept(self, h):
            value = table.get((ptype, h))
            return super().accept(h) if value is None else min(0.95, max(0.01, value))
    return LearnedBeliefs


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
        eat_capacity_policy.Beliefs = _beliefs_for(ptype, _KNOWLEDGE['prob'])
    try:
        return eat_capacity_policy.decide(BASE, **kwargs)
    finally:
        eat_capacity_policy.spec, eat_capacity_policy.Beliefs = original_spec, original_beliefs


def install():
    eat_seen_policy.install([BASE])
    inquiry_policies._POLICIES.setdefault(NAME, spec(NAME))
    if not getattr(inquiry_workflow._inquiries, '_eat_learn', False):
        original = inquiry_workflow._inquiries

        def remembered(conn, pid):
            rows = original(conn, pid)
            _LAST.clear()
            _LAST.update(patient=pid)
            return rows
        remembered._eat_learn, remembered._original = True, original
        inquiry_workflow._inquiries = remembered
    current = inquiry_policies.decide
    if not getattr(current, '_eat_learn', False):
        def wrapped(name, **kwargs):
            if name == NAME:
                return decide(name, **kwargs)
            return current(name, **kwargs)
        wrapped._eat_learn = True
        inquiry_policies.decide = wrapped


def uninstall():
    inquiry_policies._POLICIES.pop(NAME, None)
    reader = inquiry_workflow._inquiries
    if getattr(reader, '_eat_learn', False):
        inquiry_workflow._inquiries = reader._original
    _LAST.clear()
    set_knowledge(None)
    eat_seen_policy.uninstall()
