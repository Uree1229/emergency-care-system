"""Compare bounded ASR hypotheses using the existing parser, never guess truth.

The recognizer's first transcript stays the source record. Alternatives are only
uncertainty evidence: they cannot add or replace an observation on their own.
"""
import copy
import json

from . import service

REVISION = 'voice-recognition-context-20261103-v1'
MAX_ALTERNATIVES = 16
MAX_TEXT = 4000
MAX_TOTAL_CHARS = 20000
LIMITATIONS = (
    'Only supplied recognition hypotheses are compared; agreement is not accuracy.',
    'Lower-ranked hypotheses never replace the original transcript or fill fields.',
    'No acoustic model, clinical plausibility score, or previous normal value chooses a number.',
)
# Prevent a disputed source from surviving through a paired/derived field.
DEPENDENCY_GROUPS = (
    frozenset(('systolic_bp', 'diastolic_bp')),
    frozenset(('age', 'age_group', 'age_category')),
    frozenset(('consciousness', 'avpu')),
    frozenset(('symptoms', 'denied_symptoms', 'uncertain_symptoms',
               'chief_complaint', 'seizure_ongoing')),
)


def validate(text, alternatives):
    service._require(type(text) is str and 0 < len(text.strip()) <= MAX_TEXT,
                     'INVALID_RECOGNITION_CONTEXT', '인식 원문은 1~4000자입니다')
    service._require(type(alternatives) is list and len(alternatives) <= MAX_ALTERNATIVES,
                     'INVALID_RECOGNITION_CONTEXT', '인식 후보는 최대 16개의 문자열입니다')
    service._require(all(type(item) is str and 0 < len(item.strip()) <= MAX_TEXT
                         for item in alternatives), 'INVALID_RECOGNITION_CONTEXT',
                     '각 인식 후보는 1~4000자의 문자열입니다')
    service._require(len(text) + sum(map(len, alternatives)) <= MAX_TOTAL_CHARS,
                     'INVALID_RECOGNITION_CONTEXT', '인식 원문과 후보 합계는 20000자 이내입니다')
    return list(dict.fromkeys(item for item in alternatives if item != text))


def _key(value):
    # A symptom list's order is not a different finding. Numbers use Python's
    # numeric equality, so 37 and 37.0 agree while booleans never enter the parser.
    if isinstance(value, list):
        return ('list', tuple(sorted(value)))
    return ('scalar', value)


def analyze(text, alternatives, parse, *, allowed_fields=None, existing_fields=()):
    """Return a copy of top parsing plus a machine-readable comparison report.

    ``parse`` must be the same scoped parser used by the write path. Question
    context may supply an item label, but must never supply a value.
    """
    alternatives = validate(text, alternatives)
    top = parse(text)
    parses = [top] + [parse(item) for item in alternatives]
    allowed = None if allowed_fields is None else set(allowed_fields)
    keys = sorted({key for result in parses for key in result['fields']
                   if allowed is None or key in allowed})
    symptom_names = {name for result in parses for field in ('symptoms', 'denied_symptoms', 'uncertain_symptoms')
                     for name in result['fields'].get(field, [])}
    disputed_symptoms = sorted(name for name in symptom_names if len({
        tuple(field for field in ('symptoms', 'denied_symptoms', 'uncertain_symptoms')
              if name in result['fields'].get(field, [])) for result in parses}) > 1)
    direct, candidates = set(), []
    for field in keys:
        values = []
        markers = []
        for result in parses:
            present = field in result['fields']
            value = result['fields'].get(field)
            marker = (present, _key(value))
            if marker not in markers:
                markers.append(marker)
                values.append(copy.deepcopy(value) if present else None)
        if len(markers) > 1:
            direct.add(field)
            candidates.append(dict(field=field, values=values))
    disputed = set(direct)
    for group in DEPENDENCY_GROUPS:
        if disputed & group:
            disputed.update(group if allowed is None else group & allowed)
    # Include derived peers only when the parser saw them in some hypothesis,
    # except BP pairs whose completeness is essential to the one question.
    present = {key for result in parses for key in result['fields']} | set(existing_fields)
    disputed = {key for key in disputed if key in present or key in ('systolic_bp', 'diastolic_bp')}
    result = copy.deepcopy(top)
    result['fields'] = {key: value for key, value in result['fields'].items() if key not in disputed}
    result['evidence'] = {key: value for key, value in result['evidence'].items() if key not in disputed}
    result['clarification_fields'] = sorted(set(result.get('clarification_fields', [])) | disputed)
    if disputed:
        result['warnings'] = list(result.get('warnings', [])) + [
            '음성 인식 후보의 항목·수치가 서로 달라 해당 항목은 확정하지 않았습니다. 원음이나 측정값을 확인해 다시 말해 주세요.']
    report = dict(revision=REVISION,
                  status='needs_clarification' if disputed else 'agreement' if alternatives else 'single_hypothesis',
                  alternatives_count=len(alternatives), disputed_fields=sorted(disputed),
                  candidates=candidates, disputed_symptoms=disputed_symptoms,
                  coverage='provided_hypotheses_only', limitations=list(LIMITATIONS))
    return result, report


def blocked_command(text, alternatives):
    alternatives = validate(text, alternatives)
    return dict(revision=REVISION, status='command_blocked', alternatives_count=len(alternatives),
                disputed_fields=[], candidates=[], disputed_symptoms=[], coverage='provided_hypotheses_only',
                limitations=list(LIMITATIONS) + ['Command execution is blocked when a recognition context payload is supplied.'])
