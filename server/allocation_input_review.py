"""Read-only uncertainty display for the committed allocation input.

This policy does not diagnose, set a clinical deadline, change an assignment,
or turn an optional measurement into a required treatment condition. It reads
only the supplied patient snapshot, never unsubmitted voice sessions.
"""
import math
from collections.abc import Mapping
from urllib.parse import quote

from . import voice_followup, voice_r1

REVISION = 'allocation-input-review-20261030-v1'
_UNKNOWN = ('unknown', 'unclassified', 'unmeasured', '미확인', '미측정', '모름')
_CORE = ('age', 'consciousness', 'breathing', 'onset_at')
_VITALS = dict(voice_r1.VITAL_KEYS)
_LISTS = ('symptoms', 'denied_symptoms', 'uncertain_symptoms')
_PROFILE_DEPS = {'cardiac': ('stemi_suspected',), 'trauma': ('injury_region',),
                 'altered_seizure': ('injury_region',),
                 'obstetric': ('labor', 'pregnancy_weeks')}
_IMPORTANT = ('consciousness', 'avpu', 'breathing', 'chief_complaint', 'age_category')
_LABELS = dict(priority='배정 우선순위', location='현재 위치', consciousness='의식 상태',
    breathing='호흡 상태', age='나이·연령대', sex='성별', symptoms='증상 확인',
    blood_pressure='혈압', pulse='맥박', spo2='산소포화도', temperature='체온',
    respiratory_rate='호흡수', glucose='혈당', onset_at='발생 시각',
    **voice_r1.LABELS)
_GROUPS = (
    ('consciousness', ('consciousness', 'avpu'), 'consciousness'),
    ('breathing', ('breathing',), 'breathing'),
    ('chief_complaint', ('chief_complaint',), None),
    ('age_category', ('age_category',), None),
    ('symptoms', _LISTS, 'symptoms'),
    ('blood_pressure', ('systolic_bp', 'diastolic_bp'), 'blood_pressure'),
    ('pulse', ('pulse',), 'pulse'), ('spo2', ('spo2',), 'spo2'),
    ('temperature', ('temperature',), 'temperature'),
    ('respiratory_rate', ('respiratory_rate',), 'respiratory_rate'),
    ('glucose', ('glucose',), 'glucose'), ('pain_score', ('pain_score',), None),
    ('gcs', ('gcs',), None), ('age', ('age', 'age_group'), 'age'),
    ('sex', ('sex',), 'sex'), ('onset_at', ('onset_at',), 'onset_at'),
    *((key, (key,), None) for key in voice_r1.SECOND_ORDER),
)


def _mapping(value):
    return value if isinstance(value, Mapping) else {}


def _same(first, second):
    if type(first) is not type(second):
        return False
    if isinstance(first, list):
        return len(first) == len(second) and all(_same(a, b) for a, b in zip(first, second))
    if isinstance(first, dict):
        return first.keys() == second.keys() and all(_same(first[k], second[k]) for k in first)
    return first == second


def _symptom_summary(fields):
    parts = []
    for key, prefix in (('symptoms', ''), ('denied_symptoms', '없음: '),
                        ('uncertain_symptoms', '미확인/의심: ')):
        value = fields.get(key)
        if value:
            if not isinstance(value, list) or not all(isinstance(term, str) for term in value):
                return None
            parts.append(prefix + ', '.join(value))
    return '; '.join(parts)


def _current(key, patient, extra, recorded):
    """Return presence/value plus whether saved metadata can still be relevant."""
    if key in _CORE and key in patient:
        # Core nullable DB columns represent no observation. This still lets a
        # committed unavailable answer with no saved value carry its status,
        # while a cleared formerly populated value cannot inherit old flags.
        return patient[key] is not None, patient[key], True
    if key in voice_r1.PATIENT_EXTRA_FIELDS:
        name = voice_r1.PATIENT_EXTRA_FIELDS[key]
        if name in extra:
            return True, extra[name], True
    if key in _VITALS and 'vitals' in extra:
        vitals = _mapping(extra['vitals'])
        name = _VITALS[key]
        return name in vitals, vitals.get(name), True
    if key in _LISTS:
        has_recorded_list = any(name in recorded for name in _LISTS)
        summary = _symptom_summary(recorded)
        if has_recorded_list and (summary is None or patient.get('symptoms') != summary):
            return False, None, False
    return key in recorded, recorded.get(key), True


def _metadata(key, present, value, allowed, recorded, metadata):
    item = _mapping(metadata.get(key))
    if (not allowed or present != (key in recorded) or not _same(value, recorded.get(key))):
        return {}
    return item


def _timestamp(value, now):
    return value if type(value) is int and 0 <= value <= now else None


def _action(patient, fields, *, location=False):
    pid = patient.get('id')
    result = {'kind': 'manual_review', 'fields': list(fields)}
    if not isinstance(pid, str) or not pid:
        return result
    escaped = quote(pid, safe='')
    if location:
        return {**result, 'kind': 'review_patient_location', 'method': 'GET',
                'endpoint': f'/api/patients/{escaped}', 'requires_manual_update': True}
    if patient.get('state') in ('CONFIRMED', 'IN_TRANSIT'):
        return {**result, 'kind': 'review_transport', 'method': 'GET',
                'endpoint': f'/api/patients/{escaped}/transport-review'}
    if patient.get('state') == 'WAITING':
        return {**result, 'kind': 'create_patient_voice_draft', 'method': 'POST',
                'endpoint': '/api/voice/sessions', 'body': {'patient_id': pid},
                'requires_actor': True, 'requires_client_request_id': True}
    return {**result, 'kind': 'view_patient_record', 'method': 'GET',
            'endpoint': f'/api/patients/{escaped}'}


def _signal(present, value, meta):
    if meta.get('needs_clarification') is True or meta.get('status') in (
            'uncertain', 'ambiguous', 'needs_clarification'):
        return 'needs_clarification'
    if present and (value is None or (isinstance(value, str) and value in _UNKNOWN)):
        return 'unknown'
    return None


def assess(patient, now):
    """Describe uncertainty without mutating the patient or allocator inputs.

    A valid expert priority is sufficient even without a Pre-KTAS label.
    Optional observations produce information items only. An old observation
    timestamp is shown as age_seconds, never treated as a clinical expiry.
    """
    if not isinstance(patient, Mapping):
        raise TypeError('patient는 객체여야 합니다.')
    if type(now) is not int or now < 0:
        raise ValueError('now는 음수가 아닌 정수여야 합니다.')
    extra = _mapping(patient.get('extra'))
    saved = _mapping(extra.get('voice_observations'))
    recorded, metadata = _mapping(saved.get('fields')), _mapping(saved.get('field_metadata'))
    items = []

    def append(code, field, fields, state, level, relevance, reason, metas=(), **more):
        timestamps = [_timestamp(meta.get('observed_at'), now) for meta in metas]
        valid = [value for value in timestamps if value is not None]
        # A grouped observation is only as recent as its oldest affected
        # field. A newly checked BP component must not freshen the other one.
        observed_at = min(valid) if valid else None
        items.append(dict(code=code, field=field, fields=list(fields), label=_LABELS[field],
            state=state, level=level, relevance=relevance, reason=reason,
            observed_at=observed_at, age_seconds=now-observed_at if observed_at is not None else None,
            action=_action(patient, fields, location=field == 'location'), **more))

    priority = patient.get('priority')
    expert = patient.get('priority_source') == 'expert' and type(priority) is int and priority in (1, 2, 4)
    if not expert:
        append('PRIORITY_UNVERIFIED', 'priority', ('priority', 'priority_source'), 'unknown', 'review',
            'allocation_priority', '확인된 전문가 우선순위가 없어 기존 보수적 배정 가중치 4를 사용합니다.',
            used_priority=4, priority_is_fallback=True)
    else:
        present, value, allowed = _current('pre_ktas', patient, extra, recorded)
        meta = _metadata('pre_ktas', present, value, allowed, recorded, metadata)
        corresponds = type(value) is int and voice_r1.PRIORITY.get(value) == priority
        state = _signal(present, value, meta) if corresponds else None
        if state:
            append('PRIORITY_RECHECK_REQUIRED', 'pre_ktas', ('pre_ktas',), state, 'review',
                'allocation_priority', '현재 배정 우선순위에 연결된 등급 기록의 재확인이 필요합니다.',
                (meta,), used_priority=priority, priority_is_fallback=False)

    location_valid = all(type(patient.get(key)) in (int, float) and math.isfinite(patient[key])
                         and -limit <= patient[key] <= limit for key, limit in (('lat', 90), ('lon', 180)))
    if not location_valid:
        append('LOCATION_REQUIRED', 'location', ('lat', 'lon'), 'missing', 'review',
            'arrival_estimation', '현재 위치가 없어 이동시간과 도착시각의 확인이 필요합니다.')

    complaint = _current('chief_complaint', patient, extra, recorded)[1]
    dependencies = _PROFILE_DEPS.get(complaint, ()) if isinstance(complaint, str) else ()
    for field, keys, question in _GROUPS:
        values, metas, states = {}, {}, {}
        for key in keys:
            present, value, allowed = _current(key, patient, extra, recorded)
            meta = _metadata(key, present, value, allowed, recorded, metadata)
            # Legacy nullable core fields are not a new mandatory checklist.
            state = _signal(present, value, meta)
            if key in _CORE and value is None and not meta:
                state = None
            if key == 'uncertain_symptoms' and isinstance(value, list) and value:
                state = 'uncertain'
            values[key] = (present, value)
            metas[key] = meta
            if state:states[key] = state
        # Display groups can contain alternatives (e.g. consciousness/AVPU),
        # while an actual follow-up question may address only one of them.
        # Validate the committed marker against that question's exact fields.
        question_keys = (tuple(key for key in voice_followup.lookup(question)['fields'] if key in keys)
                         if question else ())
        unavailable = bool(question_keys and all(
            metas[key].get('followup_unavailable_for') == question for key in question_keys))
        if unavailable:
            state = 'unavailable'
        elif not states:
            continue
        else:
            state = next(name for name in ('needs_clarification', 'uncertain', 'unknown') if name in states.values())
        important = any(key in _IMPORTANT or key in dependencies for key in keys) or field == 'symptoms'
        relevance = ('treatment_profile_review' if any(key in dependencies for key in keys)
                     or field in ('chief_complaint', 'age_category') else
                     'hospital_handoff' if important else 'optional_observation')
        level = 'review' if important else 'info'
        prefix = '배정 관련 정보의 확인이 필요합니다.' if important else '선택 관찰 항목의 미확정 상태를 참고하세요.'
        detail = {'needs_clarification': ' 이전 관찰을 현재 값으로 다시 확인하지 못했습니다.',
                  'unavailable': ' 미측정·확인 불가로 답변한 기록입니다.',
                  'unknown': ' 현재 기록이 미확인입니다.',
                  'uncertain': ' 미확인 증상으로 기록돼 있습니다.'}[state]
        append('PATIENT_INPUT_REVIEW_REQUIRED' if important else 'OPTIONAL_OBSERVATION_UNCONFIRMED',
            field, keys, state, level, relevance, prefix+detail,
            tuple(metas[key] for key in (question_keys if unavailable else states)))

    review_count = sum(item['level'] == 'review' for item in items)
    information_count = len(items)-review_count
    return dict(revision=REVISION, patient_id=patient.get('id'), patient_version=patient.get('version'),
        patient_state=patient.get('state'), checked_at=now,
        status='REVIEW_REQUIRED' if review_count else 'INFORMATION_ONLY' if items else 'NO_REVIEW_FLAGS',
        requires_review=bool(review_count), review_count=review_count, information_count=information_count,
        purpose='allocation_input_display_only', blocking=False, items=items)
