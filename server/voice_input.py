"""Versioned voice drafts. Extraction never directly changes a patient or dispatches.

Actor IDs are demonstration ownership checks, not user authentication. Audio is
transient; only final transcripts, extracted evidence and revision history persist.
"""
import copy
import hashlib
import json
import math
import re
import uuid
from functools import wraps

from . import db, service, patient_amendments, inquiry_workflow
from . import voice_terms, voice_audio, voice_r1, voice_followup, voice_lexicon
from . import voice_recognition_context

MAX_HTTP_BYTES = 3 * 1024 * 1024
COMMON = {'actor_id', 'client_request_id', 'expected_version'}
LIST_FIELDS = {'symptoms', 'denied_symptoms', 'uncertain_symptoms'}
SCHEMA = """
CREATE TABLE IF NOT EXISTS voice_sessions (
 id TEXT PRIMARY KEY, actor_id TEXT NOT NULL,
 patient_id TEXT REFERENCES patients(id), base_patient_version INTEGER,
 state TEXT NOT NULL DEFAULT 'DRAFT' CHECK(state IN ('DRAFT','SUBMITTED')),
 version INTEGER NOT NULL DEFAULT 1, confirmed_version INTEGER, confirmed_at INTEGER,
 fields TEXT NOT NULL DEFAULT '{}', field_metadata TEXT NOT NULL DEFAULT '{}',
 baseline_summary TEXT NOT NULL DEFAULT '{}',
 warnings TEXT NOT NULL DEFAULT '[]', undo_stack TEXT NOT NULL DEFAULT '[]',
 created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, submitted_at INTEGER
);
CREATE TABLE IF NOT EXISTS voice_requests (
 actor_id TEXT NOT NULL, client_request_id TEXT NOT NULL,
 operation TEXT NOT NULL, fingerprint TEXT NOT NULL, response TEXT NOT NULL,
 recorded_at INTEGER NOT NULL, PRIMARY KEY(actor_id,client_request_id)
);
CREATE TABLE IF NOT EXISTS voice_revisions (
 id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL REFERENCES voice_sessions(id),
 version INTEGER NOT NULL, kind TEXT NOT NULL, recorded_at INTEGER NOT NULL,
 before_fields TEXT NOT NULL, after_fields TEXT NOT NULL,
 before_metadata TEXT NOT NULL, after_metadata TEXT NOT NULL,
 warnings TEXT NOT NULL, UNIQUE(session_id,version)
);
CREATE TABLE IF NOT EXISTS voice_utterances (
 session_id TEXT NOT NULL REFERENCES voice_sessions(id), utterance_id TEXT NOT NULL,
 transcript TEXT NOT NULL, mode TEXT NOT NULL, provider TEXT NOT NULL,
 observed_at INTEGER NOT NULL, recorded_at INTEGER NOT NULL,
 PRIMARY KEY(session_id,utterance_id)
);
CREATE INDEX IF NOT EXISTS voice_revision_session ON voice_revisions(session_id,id);
CREATE TABLE IF NOT EXISTS voice_followup_answers (
 session_id TEXT NOT NULL REFERENCES voice_sessions(id), question_id TEXT NOT NULL,
 answer_status TEXT NOT NULL CHECK(answer_status IN ('unavailable')),
 recorded_at INTEGER NOT NULL, session_version INTEGER NOT NULL,
 PRIMARY KEY(session_id,question_id)
);
"""


def _json(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        raise service.ServiceError('INVALID', '유한한 JSON 값이 필요합니다', 400) from None


def _shape(body, required, optional=()):
    service._require(isinstance(body, dict) and set(required) <= body.keys()
                     and not (body.keys() - set(required) - set(optional)),
                     'INVALID', '필수 항목이 없거나 지원하지 않는 항목이 있습니다')


def _identifier(value, name):
    return patient_amendments._identifier(value, name, 128)


def _actor(value):
    return patient_amendments._identifier(value, 'actor_id', 80)


def _now(now):
    service._require(type(now) is int and 0 <= now <= service.MAX_EPOCH, 'INVALID', 'now 값 오류')


def _row(conn, sid, actor, now):
    row = conn.execute('SELECT * FROM voice_sessions WHERE id=?', (sid,)).fetchone()
    service._require(row is not None, 'NOT_FOUND', '음성 입력 세션이 없습니다', 404)
    service._require(row['actor_id'] == actor, 'FORBIDDEN', '이 세션의 작업자가 아닙니다', 403)
    service._require(row['updated_at'] <= now, 'CLOCK_CONFLICT', '세션 기록보다 과거 시각입니다', 409)
    row = dict(row)
    # Hydrate legacy unavailable rows so subsequent revisions also snapshot
    # question state. This makes value and question undo one atomic operation.
    meta = json.loads(row['field_metadata'])
    for answer in conn.execute('SELECT question_id FROM voice_followup_answers WHERE session_id=?', (sid,)):
        if answer['question_id'] in _unavailable_questions(meta):
            continue  # Scoped dispositions already preserve unresolved siblings.
        for key in voice_followup.lookup(answer['question_id'])['fields']:
            meta[key] = {**meta.get(key, {}), 'followup_unavailable_for': answer['question_id']}
    row['field_metadata'] = _json(meta)
    return row


def _mutable(row, expected):
    service._require(type(expected) is int and 1 <= expected <= service.MAX_EPOCH,
                     'INVALID', 'expected_version은 양의 정수입니다')
    service._require(row['state'] == 'DRAFT', 'VOICE_SESSION_SUBMITTED',
                     '이미 반영한 세션입니다. 새 세션을 만드세요', 409)
    service._require(row['version'] == expected, 'VOICE_VERSION_CONFLICT',
                     '초안이 변경됐습니다. 최신 내용을 확인하세요', 409,
                     current_version=row['version'])


def _request(conn, now, operation, body):
    _now(now)
    actor = _actor(body.get('actor_id'))
    key = _identifier(body.get('client_request_id'), 'client_request_id')
    fingerprint = hashlib.sha256(_json({'operation': operation, 'body': body}).encode()).hexdigest()
    old = conn.execute('SELECT * FROM voice_requests WHERE actor_id=? AND client_request_id=?',
                       (actor, key)).fetchone()
    if old is not None:
        service._require(old['fingerprint'] == fingerprint, 'VOICE_REQUEST_CONFLICT',
                         '동일 요청 ID를 다른 내용에 재사용할 수 없습니다', 409)
        service._require(old['recorded_at'] <= now, 'CLOCK_CONFLICT', '요청 기록보다 과거 시각입니다', 409)
        result = json.loads(old['response'])
        result['duplicate'] = True
        return actor, key, fingerprint, result
    return actor, key, fingerprint, None


def _operate(conn, now, operation, body, action):
    with db.transaction(conn):
        actor, key, fingerprint, cached = _request(conn, now, operation, body)
        if cached is not None:
            return cached
        with voice_terms.use_dictionary(voice_lexicon.snapshot(conn)):
            result = action(actor)
        result['duplicate'] = False
        conn.execute('INSERT INTO voice_requests VALUES(?,?,?,?,?,?)',
                     (actor, key, operation, fingerprint, _json(result), now))
        return result


def _public(conn, row):
    result = {k: row[k] for k in ('id', 'actor_id', 'patient_id', 'base_patient_version', 'state',
                                 'version', 'confirmed_version', 'confirmed_at', 'created_at',
                                 'updated_at', 'submitted_at')}
    for key in ('fields', 'field_metadata', 'warnings', 'baseline_summary'):
        result[key] = json.loads(row[key])
    result['confirmed'] = row['confirmed_version'] == row['version']
    result['can_undo'] = bool(json.loads(row['undo_stack'])) and row['state'] == 'DRAFT'
    result['speak_text'] = _readback_text(result['fields'], result['field_metadata'])
    result['followup'] = _followup_plan(conn, row)
    result['latest_changes'] = []
    revision = conn.execute('SELECT kind,recorded_at,before_fields,after_fields FROM voice_revisions '
                            'WHERE session_id=? ORDER BY id DESC LIMIT 1', (row['id'],)).fetchone()
    if revision:
        before, after = json.loads(revision['before_fields']), json.loads(revision['after_fields'])
        for key in sorted(before.keys() | after.keys()):
            if before.get(key) != after.get(key):
                change = dict(field=key, previous=before.get(key), current=after.get(key),
                              change_kind=revision['kind'], recorded_at=revision['recorded_at'])
                old, new = before.get(key), after.get(key)
                if type(old) in (int, float) and type(new) in (int, float):
                    change['direction'] = 'decreased' if new < old else 'increased' if new > old else 'unchanged'
                result['latest_changes'].append(change)
    if result['baseline_summary'].get('symptoms'):
        result['speak_text'] = ('기존 기록 증상: ' + result['baseline_summary']['symptoms']
                                + '. 현재 구조화 초안: ' + result['speak_text'])
    result['utterances'] = [dict(r) for r in conn.execute(
        'SELECT utterance_id,transcript,mode,provider,observed_at,recorded_at FROM voice_utterances '
        'WHERE session_id=? ORDER BY rowid', (row['id'],))]
    result['treatment_review'] = None
    if row['patient_id'] and row['state'] == 'DRAFT':
        from . import voice_treatment_review
        result['treatment_review'] = voice_treatment_review.detection(conn, row['updated_at'], row)
    return result


def _followup_plan(conn, row, profile='voice_extended_v1'):
    service._require(isinstance(profile, str) and profile in voice_followup.PROFILES,
                     'INVALID_VOICE_PROFILE', '지원하지 않는 음성 확인 질문 프로필입니다')
    metadata = json.loads(row['field_metadata'])
    unavailable = _unavailable_questions(metadata)
    try:
        return voice_followup.plan(json.loads(row['fields']), json.loads(row['warnings']),
                                   metadata, profile=profile,
                                   answered_unavailable=unavailable)
    except ValueError as error:
        raise service.ServiceError('INVALID_VOICE_PROFILE', str(error), 400) from None


def _unavailable_questions(metadata):
    return [qid for qid in voice_followup.PROFILES['voice_extended_v1']
            if all(metadata.get(key, {}).get('followup_unavailable_for') == qid
                   for key in voice_followup.lookup(qid)['fields']) or
            any(metadata.get(key, {}).get('followup_unavailable_for') == qid and
                metadata.get(key, {}).get('followup_unavailable_scope') == 'remaining'
                for key in voice_followup.lookup(qid)['fields'])]


def _readback_text(fields, metadata, field=None):
    related = {'systolic_bp', 'diastolic_bp'} if field == 'blood_pressure' else ({field} if field else set(metadata))
    pending = [key for key in related if metadata.get(key, {}).get('needs_clarification')]
    prefix = '이전 관찰값이 포함되어 있습니다. 현재 상태 재확인이 필요합니다. ' if pending else ''
    return prefix + voice_terms.readback(fields, field)


def _dictionary_read(function):
    @wraps(function)
    def scoped(conn, *args, **kwargs):
        with voice_terms.use_dictionary(voice_lexicon.snapshot(conn)):
            return function(conn, *args, **kwargs)
    return scoped


@_dictionary_read
def get(conn, now, sid, actor):
    _now(now)
    return {'session': _public(conn, _row(conn, sid, _actor(actor), now))}


def capabilities():
    return {'schema_version': '1', 'speech': voice_audio.status(),
            'transcript_input': True, 'partial_transcripts': 'preview_only',
            'tts': 'client_reads_speak_text', 'automatic_diagnosis': False,
            'patient_apply_phase': 'before_destination_confirmation',
            'authentication': 'demo_actor_id_only', 'max_http_bytes': MAX_HTTP_BYTES,
            'r1_required_fields': list(voice_r1.REQUIRED), 'r1_fields': list(voice_r1.FIELDS),
            'voice_revision': 'voice-v2-20261030', 'connection_revision': 'voice-connection-20261030-v1',
            'patient_projection_revision': 'voice-patient-link-20261030-v1',
            'handoff_parser_revision': 'voice-ui-handoff-20261030-v1',
            'dictionary_revision': 'voice-dictionary-20261030-v1',
            'recognition_context_revision': voice_recognition_context.REVISION,
            'command_revision': 'voice-commands-20261030-v3',
            'command_actions': list(voice_terms.command_catalog()['actions']),
            'treatment_review_revision': 'voice-treatment-review-20261030-v1',
            'transcript_modes': ['dictation', 'command', 'auto'],
            'followup_profiles': list(voice_followup.PROFILES),
            'speech_context_hints': ['의식 명료', '호흡 정상', '혈압', '수축기 혈압', '이완기 혈압',
                '맥박', '산소포화도', '호흡수', '혈당', '체온', '구토 없음', '출혈 없음',
                '혈압 백이십에 팔십', '체온 삼십칠 점 이']}


def _symptoms_summary(fields):
    return '; '.join(prefix + ', '.join(fields[key])
                     for key, prefix in (('symptoms', ''), ('denied_symptoms', '없음: '),
                                         ('uncertain_symptoms', '미확인/의심: ')) if fields.get(key))


def _baseline(patient):
    extra = patient.get('extra', {})
    saved = extra.get('voice_observations', {})
    recorded = saved.get('fields', {}) if isinstance(saved, dict) else {}
    fields = {}

    def put(key, value):
        # A bad legacy extra value must not discard the rest of the record or
        # silently fall back to an older voice value for the same field.
        fields.pop(key, None)
        try:
            fields.update(voice_terms.normalize_fields({key: value}))
        except service.ServiceError:
            pass

    if isinstance(recorded, dict):
        for key, value in recorded.items():
            put(key, value)
    for key, name in voice_r1.PATIENT_EXTRA_FIELDS.items():
        if name in extra:
            put(key, extra[name])
    vitals = extra.get('vitals')
    if 'vitals' in extra:
        for key, _ in voice_r1.VITAL_KEYS:
            fields.pop(key, None)
    if isinstance(vitals, dict):
        for key, name in voice_r1.VITAL_KEYS:
            if name in vitals:
                put(key, vitals[name])
    if LIST_FIELDS & fields.keys() and patient.get('symptoms') != _symptoms_summary(fields):
        # A manually updated free-text record supersedes an older structured
        # voice snapshot. Keep its original text in baseline_summary, not as
        # guessed structured observations or a stale symptom to merge again.
        for key in LIST_FIELDS:
            fields.pop(key, None)
    for key in ('age', 'consciousness', 'breathing', 'onset_at'):
        if key in patient:
            put(key, patient[key])
    if 'age' in fields and 'age_group' in fields and fields['age'] // 10 * 10 != int(fields['age_group'][:-1]):
        fields.pop('age_group')
    if 'avpu' in fields and 'consciousness' in fields and \
            voice_r1.AVPU_TO_CONSCIOUSNESS[fields['avpu']] != fields['consciousness']:
        fields.pop('avpu')  # The current core observation wins; never guess A/V/P/U.
    if 'systolic_bp' in fields and 'diastolic_bp' in fields and fields['systolic_bp'] <= fields['diastolic_bp']:
        fields.pop('systolic_bp')
        fields.pop('diastolic_bp')
    # Conflicting symptom polarities in an imported record stay unresolved.
    for key in LIST_FIELDS:
        conflicts = {value for other in LIST_FIELDS - {key} for value in fields.get(other, [])}
        if set(fields.get(key, [])) & conflicts:
            conflicted = set(fields.get(key, [])) & conflicts
            for other in LIST_FIELDS:
                if other in fields:
                    fields[other] = [value for value in fields[other] if value not in conflicted]
            fields['uncertain_symptoms'] = list(dict.fromkeys(fields.get('uncertain_symptoms', []) + sorted(conflicted)))
    return voice_terms.normalize_fields(fields)


def _baseline_metadata(patient, fields):
    meta = {key: {'source': 'patient_record', 'observed_at': None, 'evidence': [],
                  'change_kind': 'baseline'} for key in fields}
    saved = patient.get('extra', {}).get('voice_observations', {})
    recorded = saved.get('fields', {}) if isinstance(saved, dict) else {}
    previous = saved.get('field_metadata', {}) if isinstance(saved, dict) else {}
    if not isinstance(recorded, dict) or not isinstance(previous, dict):
        return meta
    for key, item in previous.items():
        if key not in voice_terms.SUPPORTED_FIELDS or not isinstance(item, dict):
            continue
        # Carry only the status of the same observation. A later manual value
        # must not inherit an older measurement's uncertainty or skip marker.
        if ((key in fields) != (key in recorded) or fields.get(key) != recorded.get(key)
                or type(fields.get(key)) is not type(recorded.get(key))):
            continue
        status = {}
        if item.get('needs_clarification') is True:
            status['needs_clarification'] = True
            for name in ('clarification_at', 'observed_at'):
                value = item.get(name)
                if type(value) is int and 0 <= value <= service.MAX_EPOCH:
                    status[name] = value
            qid = item.get('clarification_question_id')
            if qid in voice_followup.PROFILES['voice_extended_v1'] and key in voice_followup.lookup(qid)['fields']:
                status['clarification_question_id'] = qid
        qid = item.get('followup_unavailable_for')
        if qid in voice_followup.PROFILES['voice_extended_v1'] and key in voice_followup.lookup(qid)['fields']:
            status['followup_unavailable_for'] = qid
        if key in fields or status:
            meta[key] = {**meta.get(key, {'source': 'patient_record', 'observed_at': None,
                                        'evidence': [], 'change_kind': 'baseline'}), **status}
            observed = item.get('observed_at')
            if type(observed) is int and 0 <= observed <= service.MAX_EPOCH:
                meta[key]['observed_at'] = observed
    return meta


def create(conn, now, body):
    _shape(body, {'actor_id', 'client_request_id'}, {'patient_id'})
    def action(actor):
        pid = body.get('patient_id')
        fields, base_version, baseline = {}, None, {}
        if pid is not None:
            _identifier(pid, 'patient_id')
            p = service._patient(conn, pid)
            fields, base_version = _baseline(p), p['version']
            baseline = {key: p.get(key) for key in ('symptoms', 'age', 'consciousness', 'breathing', 'onset_at')}
        sid = 'VS-' + uuid.uuid4().hex
        meta = _baseline_metadata(p, fields) if pid is not None else {}
        conn.execute('INSERT INTO voice_sessions(id,actor_id,patient_id,base_patient_version,fields,'
                     'field_metadata,baseline_summary,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',
                     (sid, actor, pid, base_version, _json(fields), _json(meta), _json(baseline), now, now))
        return get(conn, now, sid, actor)
    return _operate(conn, now, 'create', body, action)


def _advance(conn, now, row, fields, meta, warnings, kind, *, undoable=True, stack=None):
    fields = voice_terms.normalize_fields(fields)
    service._require(fields.get('onset_at', now) <= now, 'INVALID_VOICE_FIELDS',
                     '발생 시각은 현재보다 미래일 수 없습니다')
    version = row['version'] + 1
    rid = conn.execute('INSERT INTO voice_revisions(session_id,version,kind,recorded_at,before_fields,'
        'after_fields,before_metadata,after_metadata,warnings) VALUES(?,?,?,?,?,?,?,?,?)',
        (row['id'], version, kind, now, row['fields'], _json(fields), row['field_metadata'],
         _json(meta), _json(warnings))).lastrowid
    stack = json.loads(row['undo_stack']) if stack is None else stack
    if undoable:
        stack.append(rid)
    conn.execute('DELETE FROM voice_followup_answers WHERE session_id=?', (row['id'],))
    for qid in _unavailable_questions(meta):
        conn.execute('INSERT INTO voice_followup_answers VALUES(?,?,?,?,?)',
                     (row['id'], qid, 'unavailable', now, version))
    conn.execute('UPDATE voice_sessions SET fields=?,field_metadata=?,warnings=?,undo_stack=?,version=?,'
                 'confirmed_version=NULL,confirmed_at=NULL,updated_at=? WHERE id=?',
                 (_json(fields), _json(meta), _json(warnings), _json(stack), version, now, row['id']))


def _merge(row, patch, evidence, now, source, kind, replace=False):
    fields, meta = json.loads(row['fields']), json.loads(row['field_metadata'])
    pending_symptoms = {name for item in meta.values() if item.get('needs_clarification')
                        for name in item.get('recognition_context', {}).get('symptoms', [])}
    pending_evidence = list(dict.fromkeys(quote for key in LIST_FIELDS for quote in
                            meta.get(key, {}).get('clarification_evidence', [])))
    for key, value in patch.items():
        if key in LIST_FIELDS and not replace:
            fields[key] = list(dict.fromkeys(fields.get(key, []) + value))
        else:
            fields[key] = copy.deepcopy(value)
        meta[key] = {'source': source, 'observed_at': now, 'evidence': evidence.get(key, []),
                     'change_kind': kind}
    # A newly reported symptom polarity supersedes its previous state, not other symptoms.
    for key in LIST_FIELDS & patch.keys():
        for other in LIST_FIELDS - {key}:
            if other in fields:
                fields[other] = [v for v in fields[other] if v not in patch[key]]
    if 'age' in patch and 'age_group' not in patch:
        fields.pop('age_group', None)
        meta.pop('age_group', None)
    if 'age_group' in patch and 'age' not in patch:
        fields.pop('age', None)
        meta['age'] = {'source': source, 'observed_at': now, 'evidence': [],
                       'change_kind': kind, 'removed': True}
    for key in voice_r1.reconcile(fields, patch):   # AVPU·연령 구분을 의식 상태·나이와 맞춘다
        meta[key] = {'source': source, 'observed_at': now, 'evidence': [], 'change_kind': kind,
                     'derived': 'r1_reconcile', **({} if key in fields else {'removed': True})}
    for qid in voice_followup.PROFILES['voice_extended_v1']:
        keys = voice_followup.lookup(qid)['fields']
        if not (set(keys) & patch.keys()):
            continue
        for key in keys:
            if key not in meta:
                continue
            meta[key].pop('followup_unavailable_for', None)
            meta[key].pop('followup_unavailable_scope', None)
            # Age and symptom branches are alternatives. A new answer should
            # clear the old question's flags, while remaining uncertain symptom
            # values still produce their own question in voice_followup.plan.
            if qid in ('age', 'symptoms') and meta[key].get('clarification_question_id') == qid:
                for flag in ('needs_clarification', 'clarification_at', 'clarification_evidence', 'clarification_question_id', 'recognition_context'):
                    meta[key].pop(flag, None)
    if pending_symptoms and LIST_FIELDS & patch.keys():
        resolved = {name for key in ('symptoms', 'denied_symptoms') for name in patch.get(key, [])}
        remaining = sorted(pending_symptoms - resolved)
        if remaining:
            # A reply about one symptom cannot silently settle the other ASR
            # polarity disputes. Keep them visible in the existing question.
            for key in ('symptoms', 'denied_symptoms'):
                if key in fields:
                    fields[key] = [name for name in fields[key] if name not in remaining]
            fields['uncertain_symptoms'] = list(dict.fromkeys(fields.get('uncertain_symptoms', []) + remaining))
            for key in LIST_FIELDS:
                meta[key] = {**meta.get(key, {}), 'needs_clarification': True,
                             'clarification_at': now, 'clarification_evidence': pending_evidence,
                             'clarification_question_id': 'symptoms',
                             'recognition_context': dict(revision=voice_recognition_context.REVISION,
                                 status='needs_clarification', coverage='provided_hypotheses_only', symptoms=remaining)}
                meta[key].pop('followup_unavailable_for', None)
    return fields, meta


def _extract(text, now, row=None):
    """voice_terms 결과에 R1 칸을 더한다. voice_terms.extract의 결과 자체는 바꾸지 않는다."""
    parsed = voice_terms.extract(text, now)
    r1 = voice_r1.extract(text, now, parsed['fields'], json.loads(row['fields']) if row else None)
    kept = {k: v for k, v in parsed['fields'].items() if k not in r1['drop']}   # 서로 다른 의식 표현이면 둘 다 비운다
    fields = {**kept, **r1['fields']}
    return dict(fields=fields,
                evidence={k: v for k, v in {**parsed['evidence'], **r1['evidence']}.items() if k not in r1['drop']},
                warnings=list(dict.fromkeys(parsed['warnings'] + r1['warnings'])),
                clarification_fields=[key for key in voice_terms.observation_fields(text) if key not in fields])


def _mark_clarification(meta, keys, text, now, question_id=None):
    for key in keys:
        meta[key] = {**meta.get(key, {}), 'needs_clarification': True,
                     'clarification_at': now, 'clarification_evidence': [text]}
        if question_id:
            meta[key]['clarification_question_id'] = question_id


def _recognition_metadata(meta, report):
    for key in report['disputed_fields']:
        item = meta[key]
        item.pop('followup_unavailable_for', None)
        item.pop('followup_unavailable_scope', None)
        item['recognition_context'] = dict(revision=report['revision'],
            status='needs_clarification', coverage=report['coverage'])
        if key in LIST_FIELDS and report.get('disputed_symptoms'):
            item['recognition_context']['symptoms'] = list(report['disputed_symptoms'])
        for qid in voice_followup.PROFILES['voice_extended_v1']:
            if key in voice_followup.lookup(qid)['fields']:
                item['clarification_question_id'] = qid
                break


def _recognition_peer_reset(fields, meta, question_id, now):
    peers = {'age': ('age_category',), 'consciousness': ('avpu',),
             'symptoms': ('chief_complaint', 'seizure_ongoing')}.get(question_id, ())
    removed = []
    for key in peers:
        item = meta.get(key, {})
        if item.get('needs_clarification') and item.get('recognition_context'):
            fields.pop(key, None)
            meta[key] = {'source': 'recognition_peer_reset', 'observed_at': now,
                         'evidence': [], 'change_kind': 'observation', 'removed': True}
            removed.append(key)
    return removed


def _require_recognition_review(meta):
    service._require(not any(item.get('needs_clarification') and item.get('recognition_context')
                             for item in meta.values()), 'VOICE_RECOGNITION_REVIEW_REQUIRED',
                     '인식 후보가 다른 항목을 다시 말하거나 직접 수정한 뒤 확인하세요', 409)


def _observation(conn, now, row, parsed, text, source='voice', kind='dictation'):
    patch = voice_terms.normalize_fields(parsed['fields'])
    fields, meta = _merge(row, patch, parsed['evidence'], now, source, 'observation')
    _mark_clarification(meta, parsed.get('clarification_fields', []), text, now)
    if parsed.get('recognition_context'):
        _recognition_metadata(meta, parsed['recognition_context'])
    warnings = list(dict.fromkeys(json.loads(row['warnings']) + parsed['warnings']))
    if not patch:
        warnings.append('자동으로 추출한 항목이 없습니다. 원문을 확인하고 필요한 정보를 보완하세요.')
    _advance(conn, now, row, fields, meta, warnings, kind,
             undoable=bool(patch or parsed.get('clarification_fields')))
    return {'session': _public(conn, _row(conn, row['id'], row['actor_id'], now)),
            'is_final': True, 'extraction': parsed, 'operational_effect': 'draft_only'}


def _auto_mode(text, now):
    """mode=auto(무전기 방식, 버튼 하나): 명령 문장이면 command, 아니면 dictation.

    값을 말한 문장('혈압 확인해 보니 120에 80')은 항목 읽기 명령으로 보지 않는다. docs/VOICE_PTT_DESIGN.md
    """
    action = voice_terms.parse_command(text)['action']
    spoken_value = re.search(r'[0-9]|[일이삼사오육칠팔구십백]{2,}', text) or voice_terms.extract(text, now)['fields']
    if action == 'unknown' or (action == 'review' and spoken_value):
        return 'dictation'
    return 'command'


def _utterance(conn, now, row, body, text, provider):
    requested_mode = mode = body.get('mode', 'dictation')
    service._require(mode in ('dictation', 'command', 'auto'), 'INVALID', 'mode는 dictation, command, auto입니다')
    uid = _identifier(body.get('utterance_id'), 'utterance_id')
    service._require(isinstance(text, str) and 0 < len(text.strip()) <= 4000,
                     'INVALID', 'text는 1~4000자입니다')
    if mode == 'auto':
        mode = _auto_mode(text, now)
    is_final = body.get('is_final', True)
    service._require(type(is_final) is bool, 'INVALID', 'is_final은 불리언입니다')
    parsed_control = voice_terms.parse_command(text)
    if requested_mode == 'auto' and parsed_control['action'] == 'review' and parsed_control.get('read_only'):
        # A number inside a saved-record question is not a newly measured value.
        # Preserve the existing auto-mode classifier and resolve this explicit
        # query before writing its mode or applying any observation.
        mode = 'command'
    if not is_final:
        preview = _extract(text, now, row) if mode == 'dictation' else None
        return {'session': _public(conn, row), 'is_final': False, 'preview': preview,
                'preview_text': text, 'operational_effect': 'preview_only'}
    service._require(conn.execute('SELECT 1 FROM voice_utterances WHERE session_id=? AND utterance_id=?',
                     (row['id'], uid)).fetchone() is None, 'VOICE_UTTERANCE_FINALIZED',
                     '이미 처리한 발화입니다. 수정은 새 명령이나 fields API를 사용하세요', 409)
    count = conn.execute('SELECT COUNT(*) FROM voice_utterances WHERE session_id=?', (row['id'],)).fetchone()[0]
    service._require(count < 100, 'VOICE_SESSION_FULL', '세션당 발화 100개를 초과했습니다', 409)
    conn.execute('INSERT INTO voice_utterances VALUES(?,?,?,?,?,?,?)',
                 (row['id'], uid, text, mode, provider, now, now))
    if ('recognition_alternatives' in body and requested_mode == 'auto' and
            parsed_control['action'] == 'observe'):
        variants = [text] + voice_recognition_context.validate(text, body['recognition_alternatives'])
        if all(voice_terms.parse_command(value)['action'] in ('observe', 'unknown') for value in variants):
            def parse_observation(value):
                command = voice_terms.parse_command(value)
                return _extract(command['text'] if command['action'] == 'observe' else value, now, row)
            parsed, context = voice_recognition_context.analyze(text, body['recognition_alternatives'],
                parse_observation, existing_fields=json.loads(row['fields']))
            parsed['recognition_context'] = context
            result = _observation(conn, now, row, parsed, text, source='voice_command', kind='observation')
            result.update(command=parsed_control, requested_mode=requested_mode, effective_mode=mode,
                          recognition_context=context)
            return result
    if 'recognition_alternatives' in body and mode == 'command':
        return {'session': _public(conn, row), 'is_final': True,
                'recognition_context': voice_recognition_context.blocked_command(text, body['recognition_alternatives']),
                'operational_effect': 'voice_command_needs_clarification',
                'speak_text': '복수 인식 후보가 있는 명령은 실행하지 않았습니다. 원문과 실행할 동작을 다시 확인하세요.',
                'tts': 'client', 'requested_mode': requested_mode, 'effective_mode': mode}
    if requested_mode == 'auto' and parsed_control.get('rejected'):
        return {'session': _public(conn, row), 'command': parsed_control,
                'operational_effect': 'voice_command_needs_clarification',
                'speak_text': '실행할 명령을 명확히 확인하지 못했습니다. 한 번에 한 가지 요청을 다시 말해 주세요.',
                'tts': 'client', 'requested_mode': requested_mode, 'effective_mode': 'command'}
    if mode == 'command':
        command = voice_terms.parse_command(text)
        if requested_mode == 'auto' and command['action'] == 'confirm':
            # 무전기 방식에서는 음성으로 초안만 바꾸고, 확인·전송은 화면에서 누른다(docs/VOICE_PTT_DESIGN.md).
            result = {'session': _public(conn, row), 'command': command, 'operational_effect': 'confirmation_requires_tap',
                      'speak_text': '확인은 화면에서 눌러 주세요.', 'tts': 'client'}
        else:
            result = _command(conn, now, row, text)
        result.update(requested_mode=requested_mode, effective_mode=mode)
        return result
    parsed = _extract(text, now, row)
    context = None
    if 'recognition_alternatives' in body:
        parsed, context = voice_recognition_context.analyze(text, body['recognition_alternatives'],
                                                           lambda candidate: _extract(candidate, now, row),
                                                           existing_fields=json.loads(row['fields']))
        parsed['recognition_context'] = context
    kind = 'observation' if voice_terms.observation_fields(text) else 'dictation'
    result = _observation(conn, now, row, parsed, text, kind=kind)
    result.update(requested_mode=requested_mode, effective_mode=mode)
    if context is not None:
        result['recognition_context'] = context
    return result


def transcript(conn, now, sid, body):
    _shape(body, COMMON | {'utterance_id', 'text', 'is_final'}, {'mode', 'recognition_alternatives'})
    if 'recognition_alternatives' in body:
        voice_recognition_context.validate(body['text'], body['recognition_alternatives'])
        service._require(body['is_final'] is True, 'INVALID_RECOGNITION_CONTEXT',
                         '확정된 인식문에만 인식 후보를 보낼 수 있습니다')
    def action(actor):
        row = _row(conn, sid, actor, now)
        _mutable(row, body['expected_version'])
        return _utterance(conn, now, row, body, body['text'], 'app_transcript')
    return _operate(conn, now, 'transcript:' + sid, body, action)


def audio(conn, now, sid, body):
    _shape(body, COMMON | {'utterance_id', 'audio_base64'}, {'content_type', 'mode'})
    # No database write lock is held while calling the external speech service.
    actor, _, _, cached = _request(conn, now, 'audio:' + sid, body)
    if cached is not None:
        return cached
    row = _row(conn, sid, actor, now)
    _mutable(row, body['expected_version'])
    _identifier(body['utterance_id'], 'utterance_id')
    service._require(body.get('mode', 'dictation') in ('dictation', 'command', 'auto'), 'INVALID', 'mode 값 오류')
    service._require(conn.execute('SELECT 1 FROM voice_utterances WHERE session_id=? AND utterance_id=?',
        (sid, body['utterance_id'])).fetchone() is None, 'VOICE_UTTERANCE_FINALIZED', '이미 처리한 발화입니다', 409)
    recognized = voice_audio.transcribe(body['audio_base64'], body.get('content_type', 'audio/wav'))
    def action(owner):
        current = _row(conn, sid, owner, now)
        _mutable(current, body['expected_version'])
        result = _utterance(conn, now, current, body, recognized['text'], recognized['provider'])
        result['recognition'] = recognized
        return result
    return _operate(conn, now, 'audio:' + sid, body, action)


def _edit(conn, now, row, changes, unset, kind, source='manual', evidence=None, warnings=()):
    service._require(kind in ('correction', 'observation'), 'INVALID', 'change_kind 값 오류')
    service._require(isinstance(changes, dict) and isinstance(unset, list)
                     and all(isinstance(k, str) for k in unset) and len(set(unset)) == len(unset)
                     and not (set(unset) & changes.keys()) and (changes or unset),
                     'INVALID', 'changes 또는 중복 없는 unset_fields가 필요합니다')
    patch = voice_terms.normalize_fields(changes)
    supported = set(voice_terms.catalog()['supported_fields'])
    service._require(set(unset) <= supported, 'INVALID', '지원하지 않는 삭제 항목입니다')
    fields, meta = _merge(row, patch, evidence or {}, now, source, kind, replace=True)
    for key in unset:
        fields.pop(key, None)
        meta[key] = {'source': source, 'observed_at': now, 'evidence': [],
                     'change_kind': kind, 'removed': True}
    _advance(conn, now, row, fields, meta, list(dict.fromkeys(json.loads(row['warnings']) + list(warnings))), kind)
    return {'session': _public(conn, _row(conn, row['id'], row['actor_id'], now)),
            'operational_effect': 'draft_only'}


def edit(conn, now, sid, body):
    _shape(body, COMMON | {'changes', 'change_kind'}, {'unset_fields'})
    def action(actor):
        row = _row(conn, sid, actor, now)
        _mutable(row, body['expected_version'])
        return _edit(conn, now, row, body['changes'], body.get('unset_fields', []), body['change_kind'])
    return _operate(conn, now, 'edit:' + sid, body, action)


def _undo(conn, now, row):
    stack = json.loads(row['undo_stack'])
    service._require(stack, 'VOICE_NOTHING_TO_UNDO', '취소할 수정이 없습니다', 409)
    target = conn.execute('SELECT * FROM voice_revisions WHERE id=? AND session_id=?',
                           (stack.pop(), row['id'])).fetchone()
    _advance(conn, now, row, json.loads(target['before_fields']), json.loads(target['before_metadata']),
             json.loads(row['warnings']), 'undo', undoable=False, stack=stack)
    return {'session': _public(conn, _row(conn, row['id'], row['actor_id'], now)),
            'operational_effect': 'draft_only'}


def undo(conn, now, sid, body):
    _shape(body, COMMON)
    def action(actor):
        row = _row(conn, sid, actor, now)
        _mutable(row, body['expected_version'])
        return _undo(conn, now, row)
    return _operate(conn, now, 'undo:' + sid, body, action)


def _confirm(conn, now, row):
    fields, meta = json.loads(row['fields']), json.loads(row['field_metadata'])
    service._require(fields or (row['patient_id'] and any(m.get('removed') for m in meta.values())),
                     'VOICE_EMPTY_DRAFT', '확인할 환자정보가 없습니다')
    service._require(fields.get('onset_at', now) <= now, 'INVALID_VOICE_FIELDS',
                     '발생 시각은 현재보다 미래일 수 없습니다')
    _require_recognition_review(meta)
    conn.execute('UPDATE voice_sessions SET confirmed_version=version,confirmed_at=?,updated_at=? WHERE id=?',
                 (now, now, row['id']))
    return {'session': _public(conn, _row(conn, row['id'], row['actor_id'], now)),
            'operational_effect': 'draft_confirmed_only'}


def confirm(conn, now, sid, body):
    _shape(body, COMMON | {'confirmed'})
    service._require(body['confirmed'] is True, 'VOICE_CONFIRMATION_REQUIRED', '명시적 확인이 필요합니다')
    def action(actor):
        row = _row(conn, sid, actor, now)
        _mutable(row, body['expected_version'])
        return _confirm(conn, now, row)
    return _operate(conn, now, 'confirm:' + sid, body, action)


@_dictionary_read
def readback(conn, now, sid, actor, field=None):
    _now(now)
    row = _row(conn, sid, _actor(actor), now)
    fields = json.loads(row['fields'])
    if field is not None:
        service._require(field in voice_terms.catalog().get('readback_fields', voice_terms.catalog()['supported_fields']),
                         'INVALID', '지원하지 않는 항목입니다')
    spoken = _readback_text(fields, json.loads(row['field_metadata']), field)
    baseline = json.loads(row['baseline_summary'])
    if baseline.get('symptoms') and field in (None, 'symptoms'):
        spoken = '기존 기록 증상: ' + baseline['symptoms'] + '. 현재 구조화 초안: ' + spoken
    return {'session_id': sid, 'version': row['version'], 'field': field,
            'confirmed': row['confirmed_version'] == row['version'],
            'speak_text': spoken, 'tts': 'client'}


@_dictionary_read
def followup(conn, now, sid, actor, profile='voice_extended_v1'):
    _now(now)
    row = _row(conn, sid, _actor(actor), now)
    return {'session_id': sid, 'version': row['version'], 'followup': _followup_plan(conn, row, profile)}


_ANSWER_LABELS = dict(age='나이', sex='성별', symptoms='증상', consciousness='의식',
    breathing='호흡', blood_pressure='혈압', pulse='맥박', spo2='산소포화도',
    temperature='체온', respiratory_rate='호흡수', glucose='혈당', onset_at='발생 시각')
_UNAVAILABLE_ANSWER = re.compile(
    r'^(?:(?:지금|아직|현재)은?)?(?:모름|모르겠어|모르겠어요|몰라|몰라요|미확인|미측정|'
    r'비측정|및측정|미측|미측정이요|모르겠습니다|패스|측정안했어요|측정안했어|'
    r'안쟀어요|안쟀어|확인불가|측정불가|확인못함|측정못함|측정못했어|확인못했어|지금은못재|건너뛰기|건너뛰어|넘어가|넘겨)$')
_ANSWER_ENDING = re.compile(r'\s*(?:입니다|이에요|이요|합니다|해요|하다|어요|요|임|함)$')

# Full utterances only. A bulk disposition never supplies clinical observations,
# and cannot match a question, quote, negative instruction, reported speech or a
# sentence containing a number/another value. Do not fuzzy-match STT syllables.
_UNAVAILABLE_DISPOSITION = (
    r'(?:모름|모르겠어요|모르겠습니다|몰라요|미확인|미측정|확인불가|측정불가)'
    r'(?:(?:으로|로)?(?:(?:처리|기록)(?:해줘요?|해주세요|해주십시오|해)?|'
    r'남겨(?:줘요?|주세요|주십시오)?|해줘요?|해주세요|해주십시오))?')
_REMAINING_UNAVAILABLE = re.compile(
    r'^(?:(?:그럼|그러면|이제|일단|우선))?'
    r'(?:(?:남은|나머지)(?:항목|질문|정보|값|내용)?(?:은|는|을|를)?'
    r'(?:(?:전부|모두|다)(?:다)?)?|'
    r'(?:항목|질문|정보|값|내용)(?:은|는|을|를)?(?:전부|모두)(?:다)?|'
    r'(?:전부|모두)(?:다)?)' + _UNAVAILABLE_DISPOSITION + r'$')
_SINGLE_UNAVAILABLE_DISPOSITION = re.compile(r'^' + _UNAVAILABLE_DISPOSITION + r'$')


def _remaining_unavailable_answer(text):
    compact = re.sub(r'\s+', '', text.strip().strip('.!。').strip())
    return bool(_REMAINING_UNAVAILABLE.fullmatch(compact))


def _unavailable_answer(text, question_id):
    value = re.sub(r'[\s.!。]', '', text)
    label = _ANSWER_LABELS[question_id]
    if value.startswith(label):
        value = re.sub(r'^(?:은|는|이|가|을|를)', '', value[len(label):])
    return bool(_UNAVAILABLE_ANSWER.fullmatch(value) or
                _SINGLE_UNAVAILABLE_DISPOSITION.fullmatch(value))


def _answer_context(question, text):
    """Normalize bounded spoken answers only within the offered question.

    The returned sentence is parser input, never substituted for raw evidence.
    Full matches retain the one-number/two-pressure boundary and reject extra
    quantities, other field labels, questions, negations and unsupported units.
    """
    qid = question['id']
    raw = text.strip().strip('.!。').strip()
    original_raw = raw
    raw = _ANSWER_ENDING.sub('', raw).strip()
    whole = r'(?:[-−]?[0-9]{1,4}(?:\.[0-9]{1,2})?|[영공일이삼사오육칠팔구십백천한두세네다섯여섯일곱여덟아홉열스물무서른마흔쉰예순일흔여든아흔둘셋넷]{1,12})'
    numeric = whole + r'(?:\s*점\s*[0-9영공일이삼사오육칠팔구]{1,2})?'
    numeric_questions = {'age', 'blood_pressure', 'pulse', 'spo2', 'temperature', 'respiratory_rate', 'glucose'}
    if qid in numeric_questions:
        aliases = (['나이', '연령'] if qid == 'age' else ['혈압', 'BP', '비피'] if qid == 'blood_pressure'
                   else voice_terms.catalog()['terms']['measurements'][qid]['synonyms'])
        alias_pattern = '|'.join(r'\s*'.join(re.escape(ch) for ch in re.sub(r'\s+', '', alias))
                                 for alias in sorted(aliases, key=len, reverse=True))
        raw = re.sub(r'^(?:지금|현재|방금)\s*', '', raw)
        raw = re.sub('^(?:' + alias_pattern + r')(?:(?:은|는|이|가)(?=\s|[0-9−-]|지금|현재))?\s*', '', raw, flags=re.I)
        raw = re.sub(r'^(?:지금|현재|방금)\s*', '', raw)
        raw = re.sub(r'\s*(?:까지\s*)?(?:떨어졌어|올랐어|나왔어|측정됐어|입니다|이에요|이야)$', '', raw)
        if qid == 'age':
            if re.fullmatch(r'[0-9]{1,3}\s*대', raw):
                return '나이 ' + raw
            if re.fullmatch(numeric + r'\s*(?:세|살)?', raw):
                return '나이 ' + raw + ('' if re.search(r'(?:세|살)$', raw) else '세')
            return None
        if qid == 'blood_pressure':
            dashed = re.fullmatch(r'([0-9]{1,3})\s{0,8}[-–−]\s{0,8}([0-9]{1,3})\s{0,3}(?:mmHg)?', raw, re.I)
            if dashed:
                try:
                    voice_terms.normalize_fields({'systolic_bp': int(dashed.group(1)),
                                                  'diastolic_bp': int(dashed.group(2))})
                except service.ServiceError:
                    return None
                # The offered BP question supplies the label, never a value.
                # answer_followup still stores the untouched original text.
                return '혈압 ' + raw
            pair = re.fullmatch('(' + numeric + r')\s*(?:에|/|대|의|하고|\s+)\s*('
                                + numeric + r')\s*(?:mmHg)?', raw, re.I)
            if pair:
                return '혈압 ' + pair.group(1) + '에' + pair.group(2)
            return None
        if qid in ('pulse', 'respiratory_rate'):
            raw = re.sub(r'^(?:분\s*당|(?:1|일|한)\s*분\s*에)\s*', '', raw)
            value = re.fullmatch('(' + numeric + r')\s*(?:회(?:/분)?|bpm|번|분)?', raw, re.I)
            return _ANSWER_LABELS[qid] + ' ' + value.group(1) + '회' if value else None
        if qid == 'spo2':
            value = re.fullmatch('(' + numeric + r')\s*(?:퍼센트|프로|%)?', raw)
            return '산소포화도 ' + value.group(1) + '퍼센트' if value else None
        if qid == 'temperature':
            decimal = re.fullmatch('(' + voice_terms._NUM_INTEGER + r')\s*도\s*([0-9영공일이삼사오육칠팔구]{1,2})', raw)
            if decimal:
                return '체온 ' + decimal.group(1) + '점' + decimal.group(2) + '도'
            value = re.fullmatch('(' + numeric + r')\s*(?:도씨|도|℃)?', raw)
            return '체온 ' + value.group(1) + '도' if value else None
        unit = r'(?:mg/dL|밀리그램\s*퍼\s*데시리터)?'
        return '혈당 ' + raw if re.fullmatch(numeric + r'\s*' + unit, raw, re.I) else None
    if qid == 'symptoms':
        raw = original_raw  # '아니요' is a polarity answer, not a removable 요 ending.
        uncertain = question.get('uncertain_items', [])
        if len(uncertain) == 1 and raw in ('네', '있음', '있어', '있습니다', '아니요', '없음', '없어', '없습니다'):
            return uncertain[0] + (' 있음' if raw in ('네', '있음', '있어', '있습니다') else ' 없음')
        return None
    if qid == 'consciousness' and re.fullmatch(r'(?:명료|저하|반응\s*없음|미확인)(?:합니다|해요|하다|야|입니다)?', raw):
        return '의식 ' + raw
    if qid == 'breathing' and re.fullmatch(r'(?:정상|비정상|없음|미확인)(?:입니다|이에요|이야|야)?', raw):
        return '호흡 ' + raw
    if qid == 'onset_at':
        relative = re.fullmatch(r'(?:아까\s*)?(' + numeric + r')\s*(분|시간|일)\s*전(?:쯤|부터|에)?', raw)
        if relative:
            return relative.group(1) + relative.group(2) + ' 전 발생'
    return None


def _answer_interpretation_notes(question, text, candidate):
    """Expose semantic normalizations for review without reopening the question."""
    if not candidate:
        return []
    qid = question['id']
    raw = _ANSWER_ENDING.sub('', text.strip().strip('.!。').strip()).strip()
    notes = []
    if qid in ('pulse', 'respiratory_rate') and raw.endswith('분') and not re.search(r'회\s*/\s*분$', raw):
        notes.append('수치 질문 문맥에서 끝의 분을 회 단위로 해석했습니다. 원문과 측정값을 검토해 주세요.')
    if qid == 'temperature' and re.search(r'도\s*[0-9영공일이삼사오육칠팔구]', text):
        notes.append('온도 질문 문맥에서 도 뒤의 숫자를 소수부로 해석했습니다. 원문과 측정값을 검토해 주세요.')
    if qid == 'onset_at' and '쯤' in text:
        notes.append('대략적인 발생 시각을 질문 문맥의 상대 시각 초안으로 정리했습니다. 실제 시각을 검토해 주세요.')
    return notes


def answer_followup(conn, now, sid, body):
    _shape(body, COMMON | {'question_id', 'utterance_id', 'text'}, {'profile', 'unavailable', 'recognition_alternatives'})
    text = body['text']
    service._require(isinstance(text, str) and 0 < len(text.strip()) <= 4000, 'INVALID', 'text는 1~4000자입니다')
    service._require(type(body.get('unavailable', False)) is bool, 'INVALID', 'unavailable은 불리언입니다')
    if 'recognition_alternatives' in body:
        voice_recognition_context.validate(text, body['recognition_alternatives'])
    uid = _identifier(body['utterance_id'], 'utterance_id')
    _identifier(body['question_id'], 'question_id')
    profile = body.get('profile', 'voice_extended_v1')
    def action(actor):
        row = _row(conn, sid, actor, now)
        _mutable(row, body['expected_version'])
        plan = _followup_plan(conn, row, profile)
        offered = next((q for q in plan['items'] if q['id'] == body['question_id']), None)
        service._require(offered is not None, 'VOICE_QUESTION_CHANGED',
                         '이미 해결됐거나 현재 제공되지 않는 질문입니다. 질문 목록을 다시 확인하세요', 409)
        service._require(conn.execute('SELECT 1 FROM voice_utterances WHERE session_id=? AND utterance_id=?',
            (sid, uid)).fetchone() is None, 'VOICE_UTTERANCE_FINALIZED', '이미 처리한 발화입니다', 409)
        count = conn.execute('SELECT COUNT(*) FROM voice_utterances WHERE session_id=?', (sid,)).fetchone()[0]
        service._require(count < 100, 'VOICE_SESSION_FULL', '세션당 발화 100개를 초과했습니다', 409)
        if _remaining_unavailable_answer(text):
            alternatives = voice_recognition_context.validate(text, body.get('recognition_alternatives', []))
            service._require(all(_remaining_unavailable_answer(value) for value in alternatives),
                             'VOICE_ANSWER_CONFLICT',
                             '인식 후보가 다른 전체 미확인 지시는 적용하지 않았습니다. 원문을 확인하세요', 422)
            remaining = [q for q in plan['items'] if q['askable']]
            service._require(bool(remaining), 'VOICE_QUESTION_CHANGED',
                             '더 확인할 질문이 없습니다. 최신 기록을 검토하세요', 409)
            fields, meta = json.loads(row['fields']), json.loads(row['field_metadata'])
            for question in remaining:
                unresolved = set(question['missing_fields']) | set(question['clarification_fields'])
                if question.get('uncertain_items'):
                    unresolved.add('uncertain_symptoms')
                # Only unresolved siblings receive the group disposition. For
                # example a recorded systolic BP keeps its original metadata.
                for key in question['fields']:
                    if key in unresolved:
                        meta[key] = {**meta.get(key, {}),
                                     'followup_unavailable_for': question['id'],
                                     'followup_unavailable_scope': 'remaining'}
            conn.execute('INSERT INTO voice_utterances VALUES(?,?,?,?,?,?,?)',
                         (sid, uid, text, 'dictation', 'guided_followup', now, now))
            _advance(conn, now, row, fields, meta, json.loads(row['warnings']),
                     'followup_answer', undoable=meta != json.loads(row['field_metadata']))
            current = _row(conn, sid, actor, now)
            updated_plan = _followup_plan(conn, current, profile)
            ids = [q['id'] for q in remaining]
            return {'session': _public(conn, current), 'followup': updated_plan,
                    'answer': {'question_id': offered['id'], 'accepted': True,
                               'unavailable': True, 'unavailable_scope': 'remaining',
                               'unavailable_question_ids': ids, 'contextual_answer': False,
                               'review_required': False, 'interpretation_notes': [],
                               'applied_fields': [], 'ignored_fields': []},
                    'speak_text': (f'남은 항목 {len(ids)}개를 미확인으로 남겼습니다. '
                                   '이미 기록한 값은 유지했습니다. 기록 내용을 검토해 주세요.'),
                    'tts': 'client', 'operational_effect': 'draft_only'}
        parsed = _extract(text, now, row)
        allowed = set(offered['fields'])
        contextual = False
        candidate = None
        unavailable = body.get('unavailable', False) or _unavailable_answer(text, offered['id'])
        ignored_fields = sorted(set(parsed['fields']) - allowed - set(voice_r1.FIELDS))
        if offered['id'] in ('age', 'blood_pressure', 'pulse', 'spo2', 'temperature', 'respiratory_rate', 'glucose'):
            candidate = _answer_context(offered, text)
            parsed = _extract(candidate, now, row) if candidate else dict(fields={}, evidence={},
                warnings=[] if unavailable else ['질문에는 하나의 정확한 측정값을 답해 주세요. 혈압은 수축기·이완기 한 쌍을 말해 주세요.'])
            contextual = bool(candidate and candidate != text)
        elif not (allowed & parsed['fields'].keys()) and not parsed['fields']:
            candidate = _answer_context(offered, text)
            if candidate:
                parsed = _extract(candidate, now, row)
                contextual = True
        recognition_context = None
        if 'recognition_alternatives' in body:
            def parse_answer(candidate_text):
                value = _answer_context(offered, candidate_text)
                numeric_question = offered['id'] in ('age', 'blood_pressure', 'pulse', 'spo2',
                                                      'temperature', 'respiratory_rate', 'glucose')
                result = (_extract(value, now, row) if value else
                          dict(fields={}, evidence={}, warnings=[]) if numeric_question else
                          _extract(candidate_text, now, row))
                return result
            parsed, recognition_context = voice_recognition_context.analyze(
                text, body['recognition_alternatives'], parse_answer, allowed_fields=allowed)
            # A single clear number cannot resolve a competing unavailable answer.
            variants = [text] + voice_recognition_context.validate(text, body['recognition_alternatives'])
            unavailable_votes = {_unavailable_answer(value, offered['id']) for value in variants}
            if len(unavailable_votes) > 1 or recognition_context['disputed_fields']:
                recognition_context['status'] = 'needs_clarification'
                recognition_context['disputed_fields'] = sorted(allowed)
                parsed['fields'] = {}; parsed['evidence'] = {}
                parsed['clarification_fields'] = sorted(allowed)
                unavailable = False
        patch = {key: value for key, value in parsed['fields'].items() if key in allowed}
        interpretation_notes = _answer_interpretation_notes(offered, text, candidate)
        known_patch = {key: value for key, value in patch.items() if value != 'unknown'}
        service._require(not (unavailable and known_patch), 'VOICE_ANSWER_CONFLICT',
                         '측정값과 확인 불가 답변이 함께 있습니다. 현재 값을 다시 확인하세요', 422)
        evidence = {key: [text] for key in patch}
        fields, meta = _merge(row, patch, evidence, now, 'followup_answer', 'observation')
        for key in patch:
            meta[key]['question_id'] = offered['id']
            meta[key]['contextual_answer'] = contextual
            if interpretation_notes:
                meta[key]['review_required'] = True
                meta[key]['interpretation_notes'] = interpretation_notes
        accepted = bool(patch) or unavailable
        if not patch:
            _mark_clarification(meta, offered.get('missing_fields') or offered['fields'], text, now, offered['id'])
        if recognition_context and recognition_context['disputed_fields']:
            _mark_clarification(meta, recognition_context['disputed_fields'], text, now, offered['id'])
            _recognition_metadata(meta, recognition_context)
        if unavailable:
            for key in offered['fields']:
                meta[key] = {**meta.get(key, {}), 'followup_unavailable_for': offered['id']}
        question_disputed = any(meta.get(key, {}).get('needs_clarification') and
                                meta.get(key, {}).get('recognition_context') for key in offered['fields'])
        peer_resets = (_recognition_peer_reset(fields, meta, offered['id'], now)
                       if patch and not question_disputed else [])
        warnings = list(dict.fromkeys(json.loads(row['warnings']) + parsed['warnings'] + interpretation_notes))
        if peer_resets:
            warnings.append('명확한 답변 뒤에도 이전 인식 후보에 의존한 R1 값은 확정하지 않고 제거했습니다. 필요한 R1 항목을 직접 확인해 입력하세요: ' + ', '.join(peer_resets))
        if ignored_fields:
            warnings.append('질문 대상 외의 항목은 반영하지 않았습니다. 다른 측정값은 일반 상태 입력으로 다시 말해 주세요.')
        if not accepted:
            warnings.append('질문의 값을 확인하지 못했습니다. 항목명과 값을 다시 말해 주세요.')
        conn.execute('INSERT INTO voice_utterances VALUES(?,?,?,?,?,?,?)',
                     (sid, uid, text, 'dictation', 'guided_followup', now, now))
        state_changed = fields != json.loads(row['fields']) or meta != json.loads(row['field_metadata'])
        _advance(conn, now, row, fields, meta, warnings, 'followup_answer', undoable=state_changed)
        current = _row(conn, sid, actor, now)
        updated_plan = _followup_plan(conn, current, profile)
        next_question = updated_plan['next_question']
        speak = next_question['question'] if next_question else '확인 질문을 마쳤습니다. 기록 내용을 검토해 주세요.'
        result = {'session': _public(conn, current), 'followup': updated_plan,
                'answer': {'question_id': offered['id'], 'accepted': accepted,
                           'unavailable': unavailable, 'contextual_answer': contextual,
                           'unavailable_question_ids': [offered['id']] if unavailable else [],
                           'review_required': bool(interpretation_notes), 'interpretation_notes': interpretation_notes,
                           'applied_fields': sorted(patch), 'ignored_fields': ignored_fields},
                'speak_text': speak, 'tts': 'client', 'operational_effect': 'draft_only'}
        if recognition_context is not None:
            result['recognition_context'] = recognition_context
        return result
    return _operate(conn, now, 'followup_answer:' + sid, body, action)


def _command(conn, now, row, text):
    parsed = voice_terms.parse_command(text)
    action = parsed['action']
    if action == 'review':
        if parsed.get('fields'):
            fields, meta = json.loads(row['fields']), json.loads(row['field_metadata'])
            result = {'session_id': row['id'], 'version': row['version'],
                      'fields': parsed['fields'], 'speak_text': ' '.join(
                          _readback_text(fields, meta, key) for key in parsed['fields']), 'tts': 'client'}
        else:
            result = readback(conn, now, row['id'], row['actor_id'], parsed.get('field'))
        result['session'] = _public(conn, row)
    elif action == 'help':
        pool = voice_terms.command_catalog()
        result = {'session': _public(conn, row), 'operational_effect': 'draft_read_only',
                  'supported_commands': {key: value['aliases'][:4] for key, value in pool['actions'].items()},
                  'speak_text': '내용 읽어 줘, 빠진 항목 알려 줘, 혈압 120에 80으로 수정해, '
                                '방금 입력 취소, 혈압 기록 삭제, 변경 이력 알려 줘처럼 말해 주세요. '
                                '확인과 전송은 화면에서 진행해 주세요.', 'tts': 'client'}
    elif action == 'repeat_question':
        plan = _followup_plan(conn, row)
        question = plan['next_question']
        result = {'session': _public(conn, row), 'followup': plan, 'operational_effect': 'draft_read_only',
                  'speak_text': question['question'] if question else voice_r1.missing_text(json.loads(row['fields'])),
                  'tts': 'client'}
    elif action == 'history':
        records = history(conn, now, row['id'], row['actor_id'])['revisions']
        selected = parsed.get('fields') or ([parsed['field']] if parsed.get('field') else [])
        keys = {key for field in selected for key in
                (('systolic_bp', 'diastolic_bp') if field == 'blood_pressure' else (field,))}
        if keys:
            records = [record for record in records if any(
                record['before_fields'].get(key) != record['after_fields'].get(key) for key in keys)]
        result = {'session': _public(conn, row), 'revisions': records[-10:],
                  'total_revisions': len(records), 'operational_effect': 'draft_read_only',
                  'speak_text': f'조건에 맞는 변경 이력은 {len(records)}건입니다. 최근 10건까지 화면에서 확인해 주세요.',
                  'tts': 'client'}
    elif action == 'remove':
        targets = parsed.get('fields') or ([parsed['field']] if parsed.get('field') else [])
        keys = list(dict.fromkeys(key for field in targets for key in
                    (('systolic_bp', 'diastolic_bp') if field == 'blood_pressure' else (field,))))
        service._require(keys and set(keys) <= set(voice_terms.SUPPORTED_FIELDS), 'VOICE_COMMAND_UNRESOLVED',
                         '삭제할 항목을 명확히 말해 주세요', 422)
        existing = set(json.loads(row['fields'])) | set(json.loads(row['field_metadata']))
        if existing & set(keys):
            result = _edit(conn, now, row, {}, keys, 'correction', 'voice_command')
        else:
            result = {'session': _public(conn, row), 'operational_effect': 'draft_unchanged',
                      'speak_text': '해당 항목에는 삭제할 기록이 없습니다.', 'tts': 'client'}
    elif action == 'undo':
        result = _undo(conn, now, row)
    elif action == 'confirm':
        result = _confirm(conn, now, row)
    elif action == 'submit':
        result = {'session': _public(conn, row), 'operational_effect': 'explicit_submit_api_required',
                  'next_endpoint': f'/api/voice/sessions/{row["id"]}/submit',
                  'speak_text': '확인된 정보와 위치를 전송 API로 보내세요.'}
    elif action == 'missing':   # R1 필수 칸 중 빠진 것만 읽어 준다
        fields = json.loads(row['fields'])
        plan = _followup_plan(conn, row)
        next_question = plan['next_question']
        spoken = (voice_r1.missing_text(fields) + ' ' + next_question['question']) if next_question else voice_r1.missing_text(fields)
        result = {'session': _public(conn, row), 'missing_fields': voice_r1.missing(fields),
                  'followup': plan, 'speak_text': spoken, 'tts': 'client'}
    elif action == 'observe':
        extracted = _extract(parsed['text'], now, row)
        result = _observation(conn, now, row, extracted, text, source='voice_command', kind='observation')
    elif action in ('edit', 'add'):
        extracted = _extract(parsed['text'], now, row)
        patch = extracted['fields']
        service._require(patch, 'VOICE_COMMAND_UNRESOLVED', '수정할 값을 확인하지 못했습니다. 항목과 값을 다시 말해 주세요', 422,
                         warnings=extracted['warnings'])
        if action == 'edit':
            result = _edit(conn, now, row, patch, [], 'correction', 'voice_command', extracted['evidence'], extracted['warnings'])
        else:
            patch = voice_terms.normalize_fields(patch)
            fields, meta = _merge(row, patch, extracted['evidence'], now, 'voice_command', 'observation')
            _advance(conn, now, row, fields, meta, json.loads(row['warnings']) + extracted['warnings'], 'observation')
            result = {'session': _public(conn, _row(conn, row['id'], row['actor_id'], now)),
                      'operational_effect': 'draft_only'}
    else:
        raise service.ServiceError('VOICE_COMMAND_UNSUPPORTED', '지원하지 않는 음성 명령입니다', 422)
    result['command'] = parsed
    return result


def command(conn, now, sid, body):
    _shape(body, COMMON | {'text'})
    def action(actor):
        row = _row(conn, sid, actor, now)
        _mutable(row, body['expected_version'])
        return _command(conn, now, row, body['text'])
    return _operate(conn, now, 'command:' + sid, body, action)


@_dictionary_read
def history(conn, now, sid, actor):
    _now(now)
    _row(conn, sid, _actor(actor), now)
    items = []
    for record in conn.execute('SELECT * FROM voice_revisions WHERE session_id=? ORDER BY id', (sid,)):
        item = dict(record)
        for key in ('before_fields', 'after_fields', 'before_metadata', 'after_metadata', 'warnings'):
            item[key] = json.loads(item[key])
        items.append(item)
    return {'session_id': sid, 'revisions': items}


def _patient_patch(row, now, current=None):
    fields = json.loads(row['fields'])
    metadata = json.loads(row['field_metadata'])
    patch = {key: fields[key] for key in ('age', 'consciousness', 'breathing', 'onset_at') if key in fields}
    for key in ('age', 'consciousness', 'breathing', 'onset_at'):
        if metadata.get(key, {}).get('removed'):
            patch[key] = None
    has_findings = bool(LIST_FIELDS & fields.keys())
    symptoms_touched = current is None or any(
        key in metadata and metadata[key].get('source') != 'patient_record' for key in LIST_FIELDS)
    if has_findings and symptoms_touched:
        patch['symptoms'] = _symptoms_summary(fields)
    elif any(metadata.get(key, {}).get('removed') for key in LIST_FIELDS):
        patch['symptoms'] = None
    extra = copy.deepcopy(current.get('extra', {})) if current else {}
    extra['voice_observations'] = {'session_id': row['id'], 'draft_version': row['version'],
        'confirmed_at': row['confirmed_at'], 'fields': fields,
        # Full quotes remain in the owner-scoped voice history, not every hospital's summary.
        'field_metadata': {key: {k: value for k, value in item.items() if k not in ('evidence', 'clarification_evidence')}
                           for key, item in metadata.items()}}
    removed = {key for key, item in metadata.items() if item.get('removed')}
    # Preserve current manual values/priority when only another observation was
    # edited. The full reviewed snapshot still lives in voice_observations.
    applied = fields if current is None else {key: value for key, value in fields.items()
        if metadata.get(key, {}).get('source') != 'patient_record'}
    patch.update(voice_r1.patient_fields(applied, extra, derive_profile=current is None, removed_fields=removed))
    patch['extra'] = extra
    return patch


def _review_status(metadata):
    """Statuses affecting the meaning of unchanged values, without evidence text."""
    return {key: {name: value for name, value in item.items()
                  if name in ('removed', 'needs_clarification', 'followup_unavailable_for') and value}
            for key, item in metadata.items()
            if any(item.get(name) for name in ('removed', 'needs_clarification', 'followup_unavailable_for'))}


def submit(conn, now, sid, body, *, reviewed_conditions=None):
    _shape(body, COMMON, {'context', 'start_inquiry', 'inquiry_policy'})
    context = body.get('context', {})
    service._require(isinstance(context, dict), 'INVALID', 'context는 객체입니다')
    allowed = {'consent', 'lat', 'lon', 'contact', 'relation', 'required_capabilities',
               'resource_demands', 'treatment_profile_id'}
    service._require(not context.keys() - allowed, 'INVALID', '지원하지 않는 context 항목입니다')
    start = body.get('start_inquiry', False)
    service._require(type(start) is bool, 'INVALID', 'start_inquiry는 불리언입니다')
    if 'inquiry_policy' in body:
        inquiry_workflow._spec(body['inquiry_policy'])
    def action(actor):
        row = _row(conn, sid, actor, now)
        _mutable(row, body['expected_version'])
        _require_recognition_review(json.loads(row['field_metadata']))
        service._require(row['confirmed_version'] == row['version'], 'VOICE_CONFIRMATION_REQUIRED',
                         '현재 버전의 환자정보를 먼저 확인하세요', 409)
        if start:   # R1: 첫 문의에는 필수 5칸(AVPU·호흡·주증상·연령 구분·Pre-KTAS)이 있어야 한다
            draft = json.loads(row['fields'])
            service._require(not voice_r1.missing(draft), 'R1_FIELDS_REQUIRED', voice_r1.missing_text(draft), 409,
                             missing_fields=voice_r1.missing(draft))
        pid = row['patient_id']
        effect = 'patient_information_applied'
        if pid is None:
            service._require(context.get('consent') is True, 'CONSENT_REQUIRED', '정보 전달 동의가 필요합니다')
            for key, limit in (('lat', 90), ('lon', 180)):
                value = context.get(key)
                service._require(type(value) in (int, float) and math.isfinite(value) and -limit <= value <= limit,
                                 'INVALID', '새 환자는 GPS lat/lon이 필요합니다')
            pid = 'VOICE-' + sid[3:]
            patch = _patient_patch(row, now)
            payload = dict(context)
            profile = payload.pop('treatment_profile_id', None)
            payload.update(patch, reporter_role='transporter')
            if profile is not None:
                payload['extra']['treatment_profile_id'] = profile
            elif {'required_capabilities', 'resource_demands'} & context.keys():
                payload['extra'].pop('treatment_profile_id', None)   # 앱이 요구를 명시하면 R1 자동 프로필을 쓰지 않는다
            patient = service.create_patient(conn, now, pid, payload, actor=actor)
        else:
            service._require(not context, 'INVALID', '기존 환자 반영 시 context를 변경할 수 없습니다')
            current = service._patient(conn, pid)
            service._require(current['version'] == row['base_patient_version'], 'VOICE_PATIENT_VERSION_CONFLICT',
                             '환자정보가 변경됐습니다. 새 세션에서 최신 정보를 확인하세요', 409,
                             current_patient_version=current['version'])
            service._require(current['state'] == 'WAITING', 'INVALID_STATE',
                             '목적지 확정 전 환자정보만 반영할 수 있습니다', 409)
            first = conn.execute('SELECT before_fields,before_metadata FROM voice_revisions WHERE session_id=? ORDER BY id LIMIT 1',
                                 (sid,)).fetchone()
            original_fields = json.loads(first['before_fields']) if first else json.loads(row['fields'])
            metadata = json.loads(row['field_metadata'])
            original_metadata = json.loads(first['before_metadata']) if first else metadata
            patch = None
            conditions_changed = False
            if reviewed_conditions is not None:
                # Internal hook for the version/token-bound explicit treatment
                # review endpoint; public submit JSON cannot set this parameter.
                patch = _patient_patch(row, now, current)
                profile = reviewed_conditions['treatment_profile_id']
                if profile is None:
                    patch['extra'].pop('treatment_profile_id', None)
                else:
                    patch['extra']['treatment_profile_id'] = profile
                for key in ('required_capabilities', 'resource_demands'):
                    if key in reviewed_conditions:
                        patch[key] = copy.deepcopy(reviewed_conditions[key])
                candidate = copy.deepcopy(current)
                candidate.update(copy.deepcopy(patch))
                for key in ('required_capabilities', 'resource_demands'):
                    if key in patch:
                        candidate['explicit_' + key] = copy.deepcopy(patch[key])
                from . import treatment_binding
                old = treatment_binding.requirements_for(current)
                new = treatment_binding.requirements_for(candidate)
                explicit_changed = False
                for key in ('required_capabilities', 'resource_demands'):
                    if key not in patch:
                        continue
                    prior = current.get('explicit_' + key)
                    same = (set(patch[key]) == set(prior) if key == 'required_capabilities' and prior is not None
                            else patch[key] == prior)
                    explicit_changed = explicit_changed or not same
                conditions_changed = (old['profile_id'] != new['profile_id'] or
                    set(old['required_capabilities']) != set(new['required_capabilities']) or
                    old['resource_demands'] != new['resource_demands'] or explicit_changed)
            if (original_fields == json.loads(row['fields'])
                    and _review_status(original_metadata) == _review_status(metadata) and not conditions_changed):
                # Reading/confirming the same record must not invalidate a valid hospital response.
                patient = service.patient_view(conn, pid, now)
                effect = 'voice_record_saved_no_patient_change'
            else:
                result = patient_amendments.amend(conn, now, pid, {
                    'actor_id': actor, 'client_request_id': 'voice-' + sid,
                    'expected_patient_version': current['version'], 'reason': '구급대원이 확인한 음성 정보 반영',
                    'changes': patch if patch is not None else _patient_patch(row, now, current)})
                patient = result['patient']
        if start:
            inquiry_workflow.start(conn, now, pid, enabled=True, policy_name=body.get('inquiry_policy'))
        conn.execute("UPDATE voice_sessions SET patient_id=?,state='SUBMITTED',submitted_at=?,updated_at=? WHERE id=?",
                     (pid, now, now, sid))
        service.log(conn, now, actor, 'voice_information_applied', pid,
                    voice_session_id=sid, draft_version=row['version'], patient_version=patient['version'])
        return {'session': _public(conn, _row(conn, sid, actor, now)),
                'patient': service.patient_view(conn, pid, now),
                'operational_effect': effect,
                'candidates_endpoint': f'/api/patients/{pid}/candidates',
                'recommendation_endpoint': f'/api/patients/{pid}/recommendation',
                'treatment_conditions_require_confirmation': True}
    return _operate(conn, now, 'submit:' + sid, body, action)
