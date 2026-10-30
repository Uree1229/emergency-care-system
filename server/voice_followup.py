"""Deterministic collection questions for a human-reviewed voice draft.

These are demonstration input checklists, not clinical requirements or a decision
that a patient record is safe to submit.  Missing/uncertain information is never
filled with a default.  Explicitly unavailable answers stay unresolved but are
not asked indefinitely.  Inputs are the server's schema-normalized observations.
"""
import copy
import math
import re
from collections.abc import Mapping


DEFAULT_PROFILE = 'voice_extended_v1'
PROFILES = {
    'voice_core_v1': ('age', 'sex', 'symptoms', 'consciousness', 'breathing',
                      'blood_pressure', 'pulse', 'spo2'),
    'voice_extended_v1': ('age', 'sex', 'symptoms', 'consciousness', 'breathing',
                          'blood_pressure', 'pulse', 'spo2', 'temperature',
                          'respiratory_rate', 'glucose', 'onset_at'),
}


def _spec(field, fields, question, answer_hint, aliases=(), *, one_of=False):
    return dict(id=field, field=field, fields=tuple(fields), question=question,
                answer_hint=answer_hint, aliases=tuple(aliases), one_of=one_of)


_SPECS = {
    'age': _spec('age', ('age', 'age_group'), '환자의 나이 또는 연령대를 알려 주세요.',
                 '예: 나이 47세 / 40대 / 나이 미확인', ('나이', '연령'), one_of=True),
    'sex': _spec('sex', ('sex',), '환자의 성별을 알려 주세요.',
                 '예: 성별 남성 / 여성 / 성별 미확인', ('성별',)),
    'symptoms': _spec('symptoms', ('symptoms', 'denied_symptoms', 'uncertain_symptoms'),
                      '현재 환자가 호소하거나 없다고 확인한 증상을 알려 주세요.',
                      '예: 흉통 있음 / 구토 없음 / 증상 확인 불가',
                      ('증상', '흉통', '복통', '호흡곤란'), one_of=True),
    'consciousness': _spec('consciousness', ('consciousness',),
                           '현재 환자의 의식 상태를 알려 주세요.',
                           '예: 의식 명료 / 의식 저하 / 의식 미확인', ('의식', 'AVPU')),
    'breathing': _spec('breathing', ('breathing',), '현재 환자의 호흡 상태를 알려 주세요.',
                       '예: 호흡 정상 / 호흡 비정상 / 호흡 미확인', ('호흡 상태', '호흡이')),
    'blood_pressure': _spec('blood_pressure', ('systolic_bp', 'diastolic_bp'),
                            '혈압의 수축기와 이완기 값을 함께 알려 주세요.',
                            '예: 혈압 120에 80 / 혈압 미측정',
                            ('혈압', '수축기', '이완기')),
    'pulse': _spec('pulse', ('pulse',), '현재 측정한 맥박을 알려 주세요.',
                   '예: 맥박 92회 / 맥박 미측정', ('맥박', '심박')),
    'spo2': _spec('spo2', ('spo2',), '현재 측정한 산소포화도를 알려 주세요.',
                  '예: 산소포화도 97퍼센트 / 산소포화도 미측정', ('산소포화도', 'SpO2')),
    'temperature': _spec('temperature', ('temperature',), '현재 측정한 체온을 알려 주세요.',
                         '예: 체온 37.2도 / 체온 미측정', ('체온',)),
    'respiratory_rate': _spec('respiratory_rate', ('respiratory_rate',),
                              '현재 측정한 분당 호흡수를 알려 주세요.',
                              '예: 호흡수 분당 20회 / 호흡수 미측정', ('호흡수',)),
    'glucose': _spec('glucose', ('glucose',), '현재 측정한 혈당과 단위를 알려 주세요.',
                     '예: 혈당 105 밀리그램 퍼 데시리터 / 혈당 미측정', ('혈당',)),
    'onset_at': _spec('onset_at', ('onset_at',), '증상이 시작된 시각을 알려 주세요.',
                      '예: 20분 전 발생 / 오늘 오전 9시 시작 / 발생 시각 미확인',
                      ('발생 시각', '시작 시각', '발병')),
}

_UNKNOWN = frozenset(('unknown', 'unclassified', 'unmeasured', '미확인', '미측정'))
_AMBIGUOUS = re.compile(r'서로\s*다른|상충|모호|불명확|범위.{0,8}벗어|자동\s*입력하지|판단하기\s*어려')
_CLARIFICATION_STATUSES = frozenset(('uncertain', 'ambiguous', 'needs_clarification'))


def _profile(profile):
    if profile not in PROFILES:
        raise ValueError('지원하지 않는 음성 확인 질문 프로필입니다.')
    return PROFILES[profile]


def lookup(question_id, *, profile=DEFAULT_PROFILE):
    """Return a fresh definition for an offered question; never return guesses."""
    if question_id not in _profile(profile):
        raise ValueError('이 프로필에 없는 확인 질문입니다.')
    spec = _SPECS[question_id]
    return dict(id=spec['id'], field=spec['field'], fields=list(spec['fields']),
                question=spec['question'], answer_hint=spec['answer_hint'])


def _present(value):
    if value is None or isinstance(value, bool):
        return False
    if isinstance(value, float) and not math.isfinite(value):
        return False
    if isinstance(value, str):
        return bool(value.strip()) and value not in _UNKNOWN
    if isinstance(value, (tuple, list)):
        return bool(value)
    return True  # Numeric zero is a reported value, not a missing value.


def _is_unknown(value):
    return isinstance(value, str) and value in _UNKNOWN


def _flagged(meta):
    return (isinstance(meta, Mapping) and
            (meta.get('needs_clarification') is True or
             meta.get('status') in _CLARIFICATION_STATUSES))


def plan(fields, warnings=(), field_metadata=None, *, profile=DEFAULT_PROFILE,
         answered_unavailable=()):
    """Return unresolved questions and the next *askable* question.

    ``answered_unavailable`` contains stable question IDs, not invented values.
    Known IDs belonging to another profile are ignored when switching profiles.
    ``field_metadata[field].needs_clarification`` reopens a prior observation
    after a fresh ambiguous update.  Historical warnings alone do not reopen
    already populated fields because session warnings can be cumulative.

    ``complete`` means every checklist item has a resolved observation.
    ``collection_complete`` also permits an explicit unavailable answer; its
    unresolved items remain visible, with ``askable=False``.  Neither is a
    clinical readiness, triage, consent or submission decision.
    """
    ids = _profile(profile)
    if not isinstance(fields, Mapping):
        raise TypeError('fields는 객체여야 합니다.')
    metadata = {} if field_metadata is None else field_metadata
    if not isinstance(metadata, Mapping):
        raise TypeError('field_metadata는 객체여야 합니다.')
    if isinstance(warnings, str) or not isinstance(warnings, (list, tuple)):
        raise TypeError('warnings는 문자열 목록이어야 합니다.')
    if not all(isinstance(warning, str) for warning in warnings):
        raise TypeError('warnings는 문자열 목록이어야 합니다.')
    if isinstance(answered_unavailable, str):
        raise TypeError('answered_unavailable은 질문 ID 목록이어야 합니다.')
    unavailable = set(answered_unavailable)
    if not all(isinstance(key, str) and key in _SPECS for key in unavailable):
        raise ValueError('지원하지 않는 미확인 답변 질문 ID입니다.')

    items = []
    for question_id in ids:
        spec = _SPECS[question_id]
        keys = spec['fields']
        values = {key: fields.get(key) for key in keys}
        flagged = [key for key in keys if _flagged(metadata.get(key))]
        if question_id == 'symptoms':
            # One explicitly denied symptom is a reported observation, not a
            # claim that every symptom is absent.  Uncertainty stays unresolved.
            resolved = any(_present(values[key]) for key in ('symptoms', 'denied_symptoms'))
            uncertain = _present(values['uncertain_symptoms'])
        else:
            present = [_present(value) for value in values.values()]
            resolved = any(present) if spec['one_of'] else all(present)
            uncertain = False
        if resolved and not flagged and not uncertain:
            continue

        missing_fields = [key for key in keys if not _present(values[key])]
        if question_id == 'symptoms':
            missing_fields = [] if resolved else ['symptoms', 'denied_symptoms']
        item = lookup(question_id, profile=profile)
        explicit_unknown = any(_is_unknown(value) for value in values.values())
        reason = 'needs_clarification' if flagged else 'uncertain' if uncertain or explicit_unknown else 'missing'
        if reason == 'missing' and any(
                _AMBIGUOUS.search(warning) and
                any(alias.casefold() in warning.casefold() for alias in spec['aliases'])
                for warning in warnings):
            reason = 'needs_clarification'
        is_unavailable = question_id in unavailable or (explicit_unknown and not flagged)
        item.update(reason=reason, missing_fields=missing_fields,
                    clarification_fields=flagged,
                    answer_status='unavailable' if is_unavailable else 'unanswered',
                    askable=not is_unavailable)
        if reason == 'needs_clarification':
            item['question'] = '자동으로 확정하기 어려운 내용이 있습니다. ' + item['question']
        elif reason == 'uncertain':
            item['question'] = '미확인으로 기록된 내용이 있습니다. ' + item['question']
        if question_id == 'symptoms' and uncertain:
            item['uncertain_items'] = list(values['uncertain_symptoms'])
            item['question'] = (', '.join(map(str, values['uncertain_symptoms'])) +
                                ' 증상이 있는지 없는지 확인해 주세요. 확인할 수 없다면 미확인이라고 답해 주세요.')
        items.append(item)

    pending = [item for item in items if item['askable']]
    return dict(profile=profile, purpose='demonstration_collection_checklist', items=items,
                next_question=copy.deepcopy(pending[0]) if pending else None,
                complete=not items, collection_complete=not pending,
                unanswered_count=len(pending), unresolved_count=len(items))
