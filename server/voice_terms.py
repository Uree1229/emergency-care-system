"""Bounded, conservative Korean transcript rules for a human-reviewed draft.

This module neither diagnoses nor assigns severity or treatment requirements.
Quotes always come from the supplied transcript.  Values are never generated for
unmentioned fields; conflicting, approximate, historical and question contexts
are left for the operator.  The JSON catalog is reference material, not evidence
of a clinically validated speech recognizer.
"""
import copy
import json
import math
import re
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import voice_r1


MAX_TEXT = 4000
MAX_EPOCH = (1 << 63) - 1
_CATALOG = json.loads((Path(__file__).resolve().parent.parent / 'config' /
                       'paramedic_voice_terms.json').read_text(encoding='utf-8'))
SUPPORTED_FIELDS = tuple(_CATALOG['supported_fields'])
READBACK_FIELDS = tuple(_CATALOG['readback_fields'])
_MEASUREMENTS = _CATALOG['terms']['measurements']
_SYMPTOMS = tuple(t['canonical'] for t in _CATALOG['terms']['symptoms'])
_DICTIONARY = ContextVar('voice_dictionary', default=None)
_LABELS = dict(age='나이', age_group='연령대', sex='성별', symptoms='증상',
               denied_symptoms='없다고 진술한 증상', uncertain_symptoms='미확인 증상',
               consciousness='의식', breathing='호흡', onset_at='발생 시각',
               **{k: v['label'] for k, v in _MEASUREMENTS.items()}, **voice_r1.LABELS)
_ENUMS = {'sex': {'male', 'female', 'unknown'},
          'consciousness': {'alert', 'impaired', 'unresponsive', 'unknown'},
          'breathing': {'normal', 'abnormal', 'absent', 'unknown'}}
_LIST_FIELDS = ('symptoms', 'denied_symptoms', 'uncertain_symptoms')
_NUM_INTEGER = r'(?:[-−]?[0-9]{1,4}|[영공일이삼사오육칠팔구십백천한두세네다섯여섯일곱여덟아홉열스물무서른마흔쉰예순일흔여든아흔둘셋넷]{1,12})'
# Spoken decimals require a supported explicit unit. This prevents truncating a
# partly heard "삼십칠 점 ..." into an apparently exact 37.
_NUM_DECIMAL = (_NUM_INTEGER + r'\s{0,3}점\s{0,3}[0-9영공일이삼사오육칠팔구]'
                r'(?:\s{0,2}[0-9영공일이삼사오육칠팔구])?'
                r'(?=\s{0,3}(?:도|퍼센트|%|회|밀리그램|mg))')
_NUM = r'(?:' + _NUM_DECIMAL + r'|[-−]?[0-9]{1,4}(?:\.[0-9]{1,2})?|' + _NUM_INTEGER + r')'
_SPACE = r'\s{0,8}'
_BP_SEPARATOR = r'(?:/|에|대|의|하고|\s{1,8})'
_BP_PAIR_SEPARATOR = r'(?:' + _BP_SEPARATOR + r'|[-–−])'
_BP_DASHES = frozenset('-–−')
# Only an explicit connector or whitespace joins adjacent symptom mentions.
# A predicate, comma, contrast or another field ends a shared trailing state.
_SYMPTOM_LIST_LINK = re.compile(r'(?:\s{1,8}|\s{0,8}(?:과|와|및|하고|도)\s{0,8})')
_SYMPTOM_EXPLICIT_LINK = re.compile(r'\s{0,8}(?:과|와|및|하고|도)\s{0,8}')
_EXPLICIT_MEASUREMENT_UNIT = (
    r'\s{0,3}(?:mmHg|mg(?:/dL)?|mmol(?:/L)?|밀리그램|퍼센트|%|도|℃|회|번|bpm)'
    r'(?=$|[\s.,，/]|으로|로|은|는|이|가|까지|입니다|이에요|이요|요|이고|이며|였다|나왔|측정)')
# 조사는 뒤에 공백·콜론이 올 때만 조사로 본다. '호흡수 이십'의 '이'를 조사로 먹어 10이 되는 것을 막는다.
_BINDER = (r'(?:(?:은|는|이|가|을|를)(?=\s|:|[0-9−-]|지금|현재|방금|확인해|측정해|재측정|다시))?\s{0,8}(?::|은|는)?\s{0,8}'
           r'(?:(?:지금|현재|방금|재측정(?:한)?|다시\s{0,2}측정한|확인해\s{0,3}보니|측정해\s{0,3}보니|측정(?:값|결과))(?:은|는|이|가)?\s{0,5}){0,2}')
_UNCERTAIN = re.compile(r'미확인|확인\s{0,3}(?:안|불가|못|중|필요|예정)|모름|모르|불명|의심|가능성|추정|같음|같아|애매|모호|불확실|확실치|확실하지|잘\s{0,3}안\s{0,3}들|(?:않|있|없)을\s{0,2}수|(?:있|없)는지|여부')
_NEGATIVE = re.compile(r'없(?:음|다|고|어|습|었|는|을)?|부인|아님|아닌|아니(?:다|고|에요|라고|며)|않(?:음|다|고|습니다|아요)|안\s{0,3}(?:함|해|나|있|보)')
# '이상·이하·미만·에서'는 수에 붙을 때만 대략값이다. '발음 이상'(비정상)이나 '집에서'(장소)는 대략값이 아니다.
# 앞에 붙는 표현('대략·약')은 뒤의 값을, 나머지는 앞의 값을 꾸민다(QUALIFIER_WINDOW 안).
_APPROX_BEFORE = re.compile(r'대략|약\s{0,2}(?=[0-9영공일이삼사오육칠팔구십백천한두세네])')
# '넘음·넘어'도 수에 붙을 때만 대략값이다('넘어짐'은 낙상).
_APPROX_AFTER = re.compile(r'정도|전후|가량|쯤|사이|[~∼]|'
                           r'[0-9영공일이삼사오육칠팔구십백천]\s{0,3}(?:세|살|도|회|번|퍼센트|%|점|대|mmHg)?\s{0,2}'
                           r'(?:이상|이하|미만|넘(?:음|는|어|게))|'
                           r'[0-9영공일이삼사오육칠팔구십백천]\s{0,3}에서\s{0,3}[0-9영공일이삼사오육칠팔구십백천]')
_APPROX = re.compile(_APPROX_BEFORE.pattern + '|' + _APPROX_AFTER.pattern)
QUALIFIER_WINDOW = 10
_CORRECTION = re.compile(r'정정|수정|고쳐|아니(?:,|\s|고\s|(?=[0-9영공일이삼사오육칠팔구십백천]))|잘못\s{0,3}(?:말|입력)|취소')
_QUESTION = re.compile(r'\?|나요|습니까|일까요|인가요|입니까|있나(?:\s|$)|없나(?:\s|$)|여부\s{0,5}(?:질문|물어)')
# 구급 현장에서 '할머니·할아버지'는 대개 환자 지칭이라 가족 정보 단서에서 뺐다(과거력은 '과거·병력' 등으로 걸러진다).
_PAST = re.compile(r'과거|예전|기왕력|과거력|병력|수술력|작년|지난주|지난달|가족력')
_FAMILY = re.compile(r'어머니|(?<!할)아버지|엄마|아빠|배우자|형제|자매')
_HISTORY = re.compile(_PAST.pattern + '|' + _FAMILY.pattern)
_PATIENT_SUBJECT = re.compile(r'환자|본인|이번|현재|지금')
_CHANGE = re.compile(r'떨어(?:졌|진|지)|내려(?:갔|간|왔|온)|내렸|올라(?:갔|간|왔|온)|올랐|'
                     r'빨라(?:졌|진|지)|느려(?:졌|진|지)|'
                     r'상승(?:했|한|됐|된)|하락(?:했|한|됐|된)|바뀌|변했|변화|'
                     r'측정(?:했|한|됐|된)|재측정|나왔|저하됐|혼미해졌|명료해졌|정상으로\s{0,2}돌아')
_FRESH = re.compile(r'현재|지금|방금|' + _CHANGE.pattern)
_DIGITS = dict(zip('영공일이삼사오육칠팔구', (0, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9)))
_NATIVE = {'한': 1, '하나': 1, '두': 2, '둘': 2, '세': 3, '셋': 3,
           '네': 4, '넷': 4, '다섯': 5, '여섯': 6, '일곱': 7, '여덟': 8,
           '아홉': 9, '열': 10, '스물': 20, '스무': 20, '서른': 30, '마흔': 40,
           '쉰': 50, '예순': 60, '일흔': 70, '여든': 80, '아흔': 90}
_OTHER_FIELD = re.compile(r'의식|호흡(?!\s{0,2}곤란)|혈압|맥박|심박|산소\s{0,2}포화도|체온|혈당|나이|성별|연령')


def _symptom_uncertain(local):
    return bool(_UNCERTAIN.search(local) or
                (any(word in local for word in ('없', '부인')) and
                 re.search(r'않|아니|아님|아닌|아닙|아닐|지는', local)))


def _fail(message):
    # Lazy import also permits service.py to import this module.
    from .service import ServiceError
    raise ServiceError('INVALID_VOICE_FIELDS', message, 400)


def _text(text):
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT:
        _fail('인식문은 1~4000자의 문자열이어야 합니다.')
    return text


def catalog():
    """Return a detached catalog so callers cannot mutate extraction rules."""
    active = _DICTIONARY.get()
    result = copy.deepcopy(active['catalog'] if active else _CATALOG)
    if active:
        result['dictionary'] = copy.deepcopy(active['dictionary'])
    return result


@contextmanager
def use_dictionary(snapshot):
    """Request-local, immutable-in-use DB snapshot; never overwrite module globals."""
    token = _DICTIONARY.set(snapshot)
    try:
        yield
    finally:
        _DICTIONARY.reset(token)


def _terms():
    active = _DICTIONARY.get()
    return active['catalog']['terms'] if active else _CATALOG['terms']


def command_catalog():
    from . import voice_commands
    active = _DICTIONARY.get()
    result = copy.deepcopy(active.get('commands') or voice_commands.catalog()) if active else voice_commands.catalog()
    if active:
        for key, spec in active['catalog']['terms']['measurements'].items():
            aliases = result.setdefault('field_aliases', {}).setdefault(key, [])
            result['field_aliases'][key] = list(dict.fromkeys([*aliases, *spec['synonyms']]))
    return result


def r1_dictionary_terms():
    active = _DICTIONARY.get()
    return active.get('r1_terms') if active else None


def normalize_fields(fields):
    """Validate UI edits and extracted fields without coercing booleans/strings.

    Ranges below only reject implausible input formats. They do not interpret
    normality. Missing keys remain missing. Null is not a clinical observation.
    """
    if not isinstance(fields, dict) or any(k not in SUPPORTED_FIELDS for k in fields):
        _fail('지원하지 않는 음성 입력 항목입니다.')
    out = {}
    for key, value in fields.items():
        if key in _LIST_FIELDS:
            if not isinstance(value, list) or len(value) > len(_SYMPTOMS):
                _fail(f'{_LABELS[key]} 목록 형식이 올바르지 않습니다.')
            if any(not isinstance(v, str) or v not in _SYMPTOMS for v in value):
                _fail(f'{_LABELS[key]}에 지원하지 않는 용어가 있습니다.')
            out[key] = list(dict.fromkeys(value))
        elif key in _ENUMS:
            if not isinstance(value, str) or value not in _ENUMS[key]:
                _fail(f'{_LABELS[key]} 값이 올바르지 않습니다.')
            out[key] = value
        elif key == 'age_group':
            if not isinstance(value, str) or not re.fullmatch(r'(?:0|[1-9]0|1[0-2]0)대', value):
                _fail('연령대는 0대부터 120대까지의 정확한 십 년 단위입니다.')
            out[key] = value
        elif key in voice_r1.FIELDS:
            out[key] = voice_r1.validate(key, value, _fail)
        else:
            if key == 'age':
                minimum, maximum, integer = 0, 130, True
            elif key == 'onset_at':
                minimum, maximum, integer = 0, MAX_EPOCH, True
            else:
                spec = _MEASUREMENTS[key]
                minimum, maximum, integer = spec['min'], spec['max'], spec['type'] == 'integer'
            if (type(value) not in (int, float) or
                    (integer and type(value) is not int) or not minimum <= value <= maximum or
                    (type(value) is float and not math.isfinite(value))):
                _fail(f'{_LABELS[key]}의 형식 또는 입력 범위가 올바르지 않습니다.')
            out[key] = value
    for left in _LIST_FIELDS:
        for right in _LIST_FIELDS:
            if left < right and set(out.get(left, ())) & set(out.get(right, ())):
                _fail('같은 증상을 있음·없음·미확인으로 동시에 입력할 수 없습니다.')
    if ('age' in out and 'age_group' in out and
            out['age'] // 10 * 10 != int(out['age_group'][:-1])):
        _fail('나이와 연령대가 일치하지 않습니다.')
    if ('systolic_bp' in out and 'diastolic_bp' in out and
            out['systolic_bp'] <= out['diastolic_bp']):
        _fail('수축기·이완기 혈압의 순서를 확인해 주세요.')
    voice_r1.check(out, _fail)
    return out


def _number(raw):
    raw = re.sub(r'\s+', '', raw)
    if '점' in raw:
        pieces = raw.split('점')
        if len(pieces) != 2 or not 1 <= len(pieces[1]) <= 2:
            return None
        whole = _number(pieces[0])
        fraction = ''.join(str(_DIGITS[c]) if c in _DIGITS else c for c in pieces[1])
        if type(whole) is not int or whole < 0 or not fraction.isascii() or not fraction.isdigit():
            return None
        return whole + int(fraction) / 10 ** len(fraction)
    if re.fullmatch(r'[-−]?[0-9]{1,4}(?:\.[0-9]{1,2})?', raw):
        raw = raw.replace('−', '-')
        return float(raw) if '.' in raw else int(raw)
    if raw in _NATIVE:
        return _NATIVE[raw]
    for prefix, value in _NATIVE.items():
        if value >= 10 and raw.startswith(prefix) and raw[len(prefix):] in _NATIVE:
            tail = _NATIVE[raw[len(prefix):]]
            if tail < 10:
                return value + tail
    result, digit, previous_unit = 0, None, 10000
    for char in raw:
        if char in _DIGITS:
            if digit is not None:
                return None  # Do not guess whether spoken digit strings are numbers.
            digit = _DIGITS[char]
        elif char in '십백천':
            unit = {'십': 10, '백': 100, '천': 1000}[char]
            if unit >= previous_unit or digit == 0:
                return None
            result += (1 if digit is None else digit) * unit
            previous_unit, digit = unit, None
        else:
            return None
    return result + (digit or 0) if raw else None


def _alias_pattern(alias):
    """Only tolerate spacing, never replace a word with a guessed medical term."""
    compact = re.sub(r'\s+', '', alias)
    pattern = r'\s{0,2}'.join(re.escape(char) for char in compact)
    if re.fullmatch(r'[A-Za-z][A-Za-z0-9]*', compact):
        pattern = r'(?<![A-Za-z])' + pattern + r'(?![A-Za-z])'
    return pattern


def _bp_label():
    return (r'(?:(?<!수축기)(?<!이완기)' + _alias_pattern('혈압') + '|' +
            _alias_pattern('BP') + '|' + _alias_pattern('비피') + ')')


def observation_fields(text):
    """Mentioned targets of a fresh observation, including ones needing a question.

    No values are inferred here. Callers compare these targets with successfully
    extracted fields to decide whether an earlier saved value needs rechecking.
    """
    text = _text(text)
    targets = set()
    for clause in _clauses(text):
        clause, _ = _current(clause)
        if not clause or not _FRESH.search(clause):
            continue
        for match in re.finditer(_bp_label(), clause, re.IGNORECASE):
            if not re.search(r'(?:수\s{0,2}축\s{0,2}기|이\s{0,2}완\s{0,2}기)\s{0,3}$', clause[:match.start()]):
                targets.update(('systolic_bp', 'diastolic_bp'))
        for key, spec in _terms()['measurements'].items():
            if any(re.search(_alias_pattern(alias), clause, re.IGNORECASE) for alias in spec['synonyms']):
                targets.add(key)
        if re.search(r'의\s{0,2}식', clause):
            targets.add('consciousness')
        if re.search(r'호\s{0,2}흡(?!\s{0,2}(?:수|곤란))', clause):
            targets.add('breathing')
    return [key for key in SUPPORTED_FIELDS if key in targets]


def _clauses(text):
    # Decimal points are not sentence boundaries. Keep comma lists together so
    # "아버지는 흉통, 기침" does not turn the second item into patient evidence.
    clauses, start = [], 0
    for index, char in enumerate(text):
        decimal = (char == '.' and index > 0 and index + 1 < len(text)
                   and text[index - 1].isdigit() and text[index + 1].isdigit())
        if char in '\n;!?。.' and not decimal:
            end = index + 1 if char in '!?' else index
            clause = text[start:end].strip()
            if clause:
                clauses.append(clause)
            start = index + 1
    if text[start:].strip():
        clauses.append(text[start:].strip())
    return [part for clause in clauses for part in _spoken_clauses(clause)]


_REPORT_ENDING = re.compile(r'(?:습니다|입니다|합니다|드립니다|됩니다|십니다)(?=\s)')
_REPORT_CONTINUATION = re.compile(r'\s*(?:라고|라는|라며|냐고|고\s*말|는\s*(?:말|뜻)|지만|다만|하지만|아니|확실)')


def _spoken_clauses(clause):
    """Recognize explicit spoken sentence endings when STT omits punctuation.

    Each part remains a contiguous source substring. Keep high-risk discourse
    intact: splitting a family/history topic, quoted report, question or a late
    qualifier could incorrectly promote a value into current patient evidence.
    This deliberately improves only completed, unqualified reporting sentences;
    it does not guess missing punctuation from field names or numeric values.
    """
    if (any(pattern.search(clause) for pattern in (_HISTORY, _QUESTION, _UNCERTAIN, _NEGATIVE))
            or re.search(r'[\"\x27“”‘’「」『』]|예시|인용|아닙|아닐', clause)):
        return [clause]
    parts, start = [], 0
    for match in _REPORT_ENDING.finditer(clause):
        if _REPORT_CONTINUATION.match(clause[match.end():]):
            continue
        part = clause[start:match.end()].strip()
        if part:
            parts.append(part)
        start = match.end()
    if clause[start:].strip():
        parts.append(clause[start:].strip())
    return parts or [clause]


def _segments(clause):
    """쉼표로 나눈 구간과 각 구간의 시작 위치."""
    out, start = [], 0
    for index, char in enumerate(clause):
        if char in ',，':
            out.append((start, clause[start:index]))
            start = index + 1
    out.append((start, clause[start:]))
    return out


def _isolated_uncertain_symptoms(clause, mentions, context):
    """Return mention indexes in a complete, unqualified symptom-list report.

    A bare STT list can lose separate predicates. Share its uncertainty only
    when an entire comma segment consists of known symptoms and a closed tail;
    mixed narrative, vital signs and quoted/example text keep their old scope.
    """
    # Keep the original input context: _current may have removed an earlier
    # history/quote segment before this helper receives the remaining clause.
    if (re.search(r'["\x27“”‘’「」『』]|예시|예문|인용|라고|라는|라며|냐고', context)
            or _HISTORY.search(context)):
        return set()
    uncertain = set()
    for offset, segment in _segments(clause):
        end = offset + len(segment)
        members = [(index, start, stop) for index, (start, stop, _) in enumerate(mentions)
                   if offset <= start < stop <= end]
        if len({mentions[index][2] for index, _, _ in members}) < 2:
            continue
        # Canonical symptom names describe a list. Other catalog aliases can
        # already assert a state ("토함", "부어 있음"); do not overwrite it.
        if not all(re.fullmatch(_alias_pattern(mentions[index][2]), clause[start:stop], re.I)
                   for index, start, stop in members):
            continue
        prefix = clause[offset:members[0][1]]
        tail = clause[members[-1][2]:end]
        if not re.fullmatch(r'\s{0,8}(?:증상(?:은|는)?\s{0,3}:?\s{0,8})?', prefix):
            continue
        if not re.fullmatch(r'\s{0,8}(?:의심|미확인|여부\s{0,3}미확인)\s{0,8}', tail):
            continue
        if all(_SYMPTOM_LIST_LINK.fullmatch(clause[left[2]:right[1]])
               for left, right in zip(members, members[1:])):
            uncertain.update(index for index, _, _ in members)
    return uncertain


def _current(clause):
    """과거력·가족 정보·질문 구간을 빼고 현재 관찰 구간만 남긴다. (남긴 문장, 뺀 이유 집합).

    과거력과 질문은 그 쉼표 구간만 뺀다. 가족을 주어로 말하면 '환자·본인·이번·현재·지금'이 나오기 전까지
    뒤 구간도 뺀다('어머니는 70세 흉통, 기침'의 기침도 가족 정보로 본다).
    """
    kept, reasons, family = [], set(), False
    for _, segment in _segments(clause):
        if family and _PATIENT_SUBJECT.search(segment):
            family = False
        if _FAMILY.search(segment):
            family = True
        past = (_PAST.search(segment) or re.search(r'있었|없었|했었|였었', segment) or
                ('어제' in segment and not re.search(r'부터|발생|시작|발현', segment)))
        if family or past:
            reasons.add('history')
        elif _QUESTION.search(segment):
            reasons.add('question')
        else:
            kept.append(segment)
    return ','.join(kept), reasons


def extract(text, observed_at):
    text = _text(text)
    if type(observed_at) is not int or not 0 <= observed_at <= MAX_EPOCH:
        _fail('관찰 시각은 유효한 정수 시각이어야 합니다.')
    warnings, candidates, symptom_states = [], {}, {k: {} for k in _LIST_FIELDS}

    def warn(message):
        if message not in warnings:
            warnings.append(message)

    def add(key, value, quote):
        try:
            normalize_fields({key: value})
        except Exception as error:
            from .service import ServiceError
            if not isinstance(error, ServiceError):
                raise
            warn(f'{_LABELS[key]} 값의 형식·범위를 확인해 주세요. 자동 입력하지 않았습니다.')
            return
        candidates.setdefault(key, []).append((value, quote))

    if _CORRECTION.search(text):
        warn('정정·취소 표현이 섞여 있어 값을 선택하지 않았습니다. 수정 명령 또는 직접 입력으로 확인해 주세요.')
        return dict(fields={}, evidence={}, warnings=warnings)

    for clause in _clauses(text):
        # 과거력·가족·질문은 해당 쉼표 구간만 뺀다(예전에는 문장 전체를 버려 같은 문장의 현재 관찰까지 잃었다).
        clause, reasons = _current(clause)
        if 'history' in reasons:
            warn('과거·가족 정보가 섞인 문장은 현재 환자 정보로 입력하지 않았습니다. 확인해 주세요.')
        if 'question' in reasons:
            warn('질문 형태의 문장은 관찰 사실로 입력하지 않았습니다. 확인해 주세요.')
        if not clause.strip():
            continue
        segments = _segments(clause)

        def scope(start, end):
            """값 [start, end)에 붙은 (불확실, 대략값). 같은 쉼표 구간 안에서, 뒤에 붙는 표현은 값 끝에서
            QUALIFIER_WINDOW자 안, 앞에 붙는 '대략·약'은 값 앞 QUALIFIER_WINDOW자 안만 본다.
            쉼표가 빠진 인식문에서도 멀리 떨어진 '추정'이 나이·혈압을 지우지 않게 한다."""
            seg_start, segment = [item for item in segments if item[0] <= start][-1]
            cues = lambda rx: [seg_start + m.start() for m in rx.finditer(segment)]
            uncertain = any(start <= at <= end + QUALIFIER_WINDOW for at in cues(_UNCERTAIN))
            approximate = (any(start <= at <= end + QUALIFIER_WINDOW for at in cues(_APPROX_AFTER)) or
                           any(start - QUALIFIER_WINDOW <= at < end for at in cues(_APPROX_BEFORE)))
            return uncertain, approximate

        # Find all symptom aliases first, preferring the longest overlapping alias.
        mentions = []
        for item in _terms()['symptoms']:
            for alias in item['synonyms']:
                for match in re.finditer(_alias_pattern(alias), clause, re.IGNORECASE):
                    mentions.append((match.start(), match.end(), item['canonical']))
        selected = []
        for item in sorted(mentions, key=lambda v: (-(v[1] - v[0]), v[0])):
            if not any(item[0] < x[1] and x[0] < item[1] for x in selected):
                selected.append(item)
        selected.sort()
        isolated_uncertain = _isolated_uncertain_symptoms(clause, selected, text)
        for index, (start, end, symptom) in enumerate(selected):
            after = clause[end:selected[index + 1][0] if index + 1 < len(selected) else len(clause)]
            before = clause[0 if index == 0 else selected[index - 1][1]:start]
            boundary = _OTHER_FIELD.search(after)
            scope_ended = bool(boundary)
            if boundary:
                after = after[:boundary.start()]
            comma = re.search(r'[,，]', after)   # 뒤 쉼표 구간의 '의심·없음'은 이 증상을 꾸미지 않는다('흉통, ST 상승 의심')
            if comma:
                after = after[:comma.start()]
                scope_ended = True
            # Prefix qualifiers are used only before the first symptom; other
            # prefixes can belong to the previous symptom's negation.
            prefix = before.rsplit(',', 1)[-1] if index == 0 else ''
            if _OTHER_FIELD.search(prefix):
                prefix = ''
            local = prefix + clause[start:end] + after
            if not scope_ended and _SYMPTOM_LIST_LINK.fullmatch(after):
                # A coordinated list can share a trailing denial/uncertainty.
                bare_link = not _SYMPTOM_EXPLICIT_LINK.fullmatch(after)
                for later in range(index + 1, len(selected)):
                    next_end = selected[later][1]
                    following = clause[next_end:selected[later + 1][0] if later + 1 < len(selected) else len(clause)]
                    boundary = _OTHER_FIELD.search(following)
                    following_ended = bool(boundary)
                    if boundary:
                        following = following[:boundary.start()]
                    comma = re.search(r'[,，]', following)
                    if comma:
                        following = following[:comma.start()]
                        following_ended = True
                    if following_ended or not _SYMPTOM_LIST_LINK.fullmatch(following):
                        # Bare lists gain only a clear shared denial. An ASR
                        # string can lose separate predicates, so do not spread
                        # an ambiguous trailing uncertainty over that list.
                        if not bare_link or (_NEGATIVE.search(following) and not _symptom_uncertain(following)):
                            after = following
                            local += following
                        break
                    bare_link = bare_link or not _SYMPTOM_EXPLICIT_LINK.fullmatch(following)
            if index in isolated_uncertain or _symptom_uncertain(local):
                key = 'uncertain_symptoms'
                warn('미확인·불확실한 증상은 있음으로 입력하지 않았습니다. 확인해 주세요.')
            elif _NEGATIVE.search(after) or (index == 0 and re.search(r'없(?:는|던)\s{0,3}$', before)):
                key = 'denied_symptoms'
            else:
                key = 'symptoms'
            symptom_states[key].setdefault(symptom, []).append(clause)

        # Demographics require explicit words/units, never derived from speech style.
        for match in re.finditer(r'(?:나이(?:는|가)?\s{0,3}|(?<![0-9가-힣]))(' + _NUM + r')\s{0,3}(?:세|살)(?:입니다|이고|이시고|인|의|로)?(?![가-힣])', clause):
            if any(scope(match.start(), match.end())):
                warn('모호한 나이는 입력하지 않았습니다. 정확한 나이를 확인해 주세요.')
            else:
                value = _number(match.group(1))
                if value is not None:
                    add('age', value, match.group())
                    warn('나이 수치를 원문과 확인해 주세요.')
        for match in re.finditer(r'(?<![0-9가-힣])([0-9]{1,3})\s{0,2}대(?:입니다|이고|이시고|인|의|로)?(?![가-힣])', clause):
            value = int(match.group(1))
            if not any(scope(match.start(), match.end())) and value % 10 == 0:
                add('age_group', str(value) + '대', match.group())
                warn('연령대만 언급되어 정확한 나이는 입력하지 않았습니다. 확인해 주세요.')
        for match in re.finditer(r'남성|여성|남자|여자|성별\s{0,3}(?:남|여|미상|불명|모름)', clause):
            explicit_unknown = any(x in match.group() for x in ('미상', '불명', '모름'))
            own_negation = re.match(r'\s{0,3}(?:이|가|은|는)?\s{0,3}(?:없|아님|아닌|아니|않)', clause[match.end():])
            if (not scope(match.start(), match.end())[0] or explicit_unknown) and not own_negation:
                word = match.group()
                value = 'unknown' if any(x in word for x in ('미상', '불명', '모름')) else ('male' if '남' in word else 'female')
                add('sex', value, match.group())

        # Explicit state phrases only. "의식 있음" does not establish alertness.
        conscious_prefix = _alias_pattern('의식') + _BINDER
        breathing_prefix = _alias_pattern('호흡') + _BINDER
        states = {
            'consciousness': [
                ('unknown', conscious_prefix + r'(?:미확인|미상|불명|확인\s{0,2}불가)'),
                ('unresponsive', conscious_prefix + r'(?:없음|없다|없고|없습니다|소실)|반응(?:은|는|이|가)?\s{0,3}(?:없음|없다|없습니다)'),
                ('impaired', conscious_prefix + r'(?:저하|혼미)|기면\s{0,3}상태'),
                ('alert', conscious_prefix + r'(?:명료|명확)|명료한\s{0,3}의식')],
            'breathing': [
                ('unknown', breathing_prefix + r'(?:미확인|미상|불명|확인\s{0,2}불가)'),
                ('absent', r'무호흡|' + breathing_prefix + r'(?:없음|없다|없습니다|정지)'),
                ('abnormal', breathing_prefix + r'(?:불규칙|불안정|비정상)|불규칙한\s{0,3}호흡'),
                ('normal', breathing_prefix + r'정상|정상\s{0,3}호흡|정상적인\s{0,3}호흡')]
        }
        for key, options in states.items():
            for value, pattern in options:
                for match in re.finditer(pattern, clause):
                    tail = clause[match.end():match.end() + 12].split(',', 1)[0]
                    boundary = _OTHER_FIELD.search(tail)
                    if boundary:
                        tail = tail[:boundary.start()]
                    if value != 'unknown' and (scope(match.start(), match.end())[0] or _NEGATIVE.search(tail)):
                        warn(f'{_LABELS[key]} 상태 표현이 불확실하거나 부정되어 입력하지 않았습니다. 확인해 주세요.')
                    else:
                        add(key, value, match.group())
        if re.search(r'의식(?:은|는|이|가)?\s{0,3}(?:있음|있다|있고|있습니다|유)', clause) and 'consciousness' not in candidates:
            warn('의식이 있다는 말만으로 명료 상태를 입력하지 않았습니다. 의식 상태를 확인해 주세요.')

        def numeric(key, raw, quote, span, tail=''):
            local_tail = re.split(r'[,，]', tail, maxsplit=1)[0]
            boundary = _OTHER_FIELD.search(local_tail)
            if boundary:
                local_tail = local_tail[:boundary.start()]
            if (any(scope(*span)) or re.match(r'[0-9]|\.[0-9]', tail) or
                    re.match(r'\s{0,3}(?:점|[~∼–−-]|에서|대|정도|쯤|이상|이하|미만|인지|또는|혹은|아니면|이나|이거나)', tail) or
                    (_CHANGE.search(local_tail) and _UNCERTAIN.search(local_tail))):
                warn(f'{_LABELS[key]}의 수치·단위가 모호하여 입력하지 않았습니다. 확인해 주세요.')
                return
            unit = re.match(r'\s{0,3}(mmol(?:/L)?|mg(?:/dL)?|mmHg|밀리미터\s{0,2}수은주|밀리그램|퍼센트|%|화씨|℉|fahrenheit|도|℃|회|bpm)', tail, re.IGNORECASE)
            if unit:
                supplied = re.sub(r'\s+', '', unit.group(1)).lower()
                allowed = ({'mmhg', '밀리미터수은주'} if key in ('systolic_bp', 'diastolic_bp') else
                           {'회', 'bpm'} if key in ('pulse', 'respiratory_rate') else
                           {'퍼센트', '%'} if key == 'spo2' else
                           {'도', '℃'} if key == 'temperature' else {'mg', 'mg/dl', '밀리그램'})
                if supplied not in allowed:
                    warn(f'{_LABELS[key]} 단위가 지원 단위와 달라 입력하지 않았습니다. 직접 확인해 주세요.')
                    return
            value = _number(raw)
            if value is None:
                warn(f'{_LABELS[key]}의 수치 표현을 해석하지 못했습니다. 직접 확인해 주세요.')
                return
            add(key, value, quote)
            warn(f'{_LABELS[key]} 수치와 단위를 원문·측정값으로 확인해 주세요.')

        # A range is not a new measurement. Select a later value only when an
        # explicit transition verb identifies it as a changed observation.
        transition = _SPACE + r'(?:에서|이었다가|였다가|이었는데|였는데)' + _SPACE + r'(?:(?:지금|현재|방금)' + _SPACE + r')?'
        change_suffix = _SPACE + r'(?:까지|으로|로)?' + _SPACE + '(?:' + _CHANGE.pattern + ')'
        bp_transition = (_bp_label() + _SPACE + _BINDER + '(?P<old_s>' + _NUM + ')' + _SPACE + _BP_SEPARATOR + _SPACE +
                         '(?P<old_d>' + _NUM + ')' + transition + '(?P<new_s>' + _NUM + ')' + _SPACE + _BP_SEPARATOR + _SPACE +
                         '(?P<new_d>' + _NUM + ')' + r'(?:\s{0,2}mmHg)?' + change_suffix)
        bp_transitions = list(re.finditer(bp_transition, clause, re.IGNORECASE))
        for match in bp_transitions:
            if re.search(r'(?:수\s{0,2}축\s{0,2}기|이\s{0,2}완\s{0,2}기)\s{0,3}$', clause[:match.start()]):
                continue
            numeric('systolic_bp', match.group('new_s'), match.group(), match.span('new_s'), clause[match.end('new_d'):])
            numeric('diastolic_bp', match.group('new_d'), match.group(), match.span('new_d'), clause[match.end('new_d'):])
        bp_pattern = (_bp_label() + _SPACE + _BINDER + '(?P<systolic>' + _NUM + ')' + _SPACE
                      + '(?P<separator>' + _BP_PAIR_SEPARATOR + ')' + _SPACE + '(?P<diastolic>' + _NUM + ')')
        for match in re.finditer(bp_pattern, clause, re.IGNORECASE):
            if any(old.start() <= match.start() < old.end() for old in bp_transitions):
                continue
            # A label qualified as systolic/diastolic is one measurement,
            # including when ASR inserts spaces before the generic BP label.
            if re.search(r'(?:수\s{0,2}축\s{0,2}기|이\s{0,2}완\s{0,2}기)\s{0,3}$', clause[:match.start()]):
                continue
            if match.group('separator') in _BP_DASHES:
                # A dash is accepted only between unsigned integer pressure
                # tokens. Do not reinterpret a minus sign or a partial range.
                tail = clause[match.end():]
                dash_tail = re.sub(r'^\s{0,3}mmHg', '', tail, count=1, flags=re.IGNORECASE)
                if (not all(re.fullmatch(r'[0-9]{1,3}', match.group(name))
                            for name in ('systolic', 'diastolic'))
                        or re.match(r'\s{0,3}[-–−~∼/0-9A-Za-z]', dash_tail)
                        or re.match(r'\s{0,3}(?:사이|범위|에서|번|까지\s{0,3}범위|(?:과|와|및|하고)\s{0,3}[0-9])', dash_tail)):
                    warn('혈압의 두 수치와 범위 표현을 구분하지 못해 입력하지 않았습니다. 확인해 주세요.')
                    continue
                try:
                    normalize_fields({key: _number(match.group(name)) for key, name in
                                      (('systolic_bp', 'systolic'), ('diastolic_bp', 'diastolic'))})
                except Exception as error:
                    from .service import ServiceError
                    if not isinstance(error, ServiceError):
                        raise
                    warn('혈압 두 수치의 형식·범위를 확인해 주세요. 한쪽 값만 입력하지 않았습니다.')
                    continue
            # "혈압이 한 번 떨어졌어" is a subject particle plus a count,
            # not 2/1. Newly supported loose pairs require unambiguous tokens.
            if (match.group('separator') not in ('/', '에', '대')
                    and (match.group('systolic') in _DIGITS
                         or (len(match.group('systolic')) == 1 and match.group('systolic') in _NATIVE))):
                warn('혈압의 조사와 수치를 구분하지 못해 입력하지 않았습니다. 확인해 주세요.')
                continue
            # A loose pair must not consume the first two of three numbers in
            # e.g. "혈압 120 80 92회". No pulse/third pressure is guessed here.
            third = re.match(r'\s{0,3}(?:mmHg)?(?:\s{1,8}|(?:/|에|대|의|하고)\s{0,3})('
                             + _NUM + ')',
                             clause[match.end():], re.IGNORECASE)
            third_is_particle = bool(third and third.group(1) == '이'
                                     and not re.match(_EXPLICIT_MEASUREMENT_UNIT,
                                                      clause[match.end() + third.end():], re.I))
            if third and not third_is_particle:
                warn('혈압에 여러 수치가 있어 한 쌍을 선택하지 않았습니다. 확인해 주세요.')
                continue
            numeric('systolic_bp', match.group('systolic'), match.group(), match.span(), clause[match.end():])
            numeric('diastolic_bp', match.group('diastolic'), match.group(), match.span(), clause[match.end():])
        for key, spec in _terms()['measurements'].items():
            if not spec['synonyms']:
                continue
            aliases = '|'.join(_alias_pattern(x) for x in sorted(spec['synonyms'], key=len, reverse=True))
            unit = r'(?:mmHg|도|회|퍼센트|%)?'
            changed = ('(?:' + aliases + ')' + _SPACE + _BINDER + '(?P<old>' + _NUM + ')' + _SPACE + unit +
                       transition + '(?P<new>' + _NUM + ')' + _SPACE + unit + change_suffix)
            changed_matches = list(re.finditer(changed, clause, re.IGNORECASE))
            for match in changed_matches:
                numeric(key, match.group('new'), match.group(), match.span('new'), clause[match.end('new'):])
            pattern = '(?:' + aliases + ')' + _SPACE + _BINDER + r'(?:분당\s{0,3})?(' + _NUM + ')'
            for match in re.finditer(pattern, clause, re.IGNORECASE):
                if any(old.start() <= match.start() < old.end() for old in changed_matches):
                    continue
                # A Korean decimal or a wrong explicit unit is never truncated.
                tail = clause[match.end():]
                # Optional 조사 backtracking may otherwise read the subject
                # particle in "맥박이 떨어졌어" as the number 2. A single
                # Korean digit attached to a label requires an explicit unit;
                # multi-character numbers (이십) and spaced numbers stay valid.
                raw = match.group(1)
                attached_digit = (raw in _DIGITS and match.start(1) > 0
                                  and not clause[match.start(1) - 1].isspace())
                particle_before_change = raw == '이' and bool(_FRESH.search(tail))
                if (attached_digit or particle_before_change) and not re.match(_EXPLICIT_MEASUREMENT_UNIT, tail, re.I):
                    warn(f'{_LABELS[key]}의 조사와 수치를 구분하지 못해 입력하지 않았습니다. 확인해 주세요.')
                    continue
                if re.match(r'\s{0,3}(?:mmol|화씨|℉|fahrenheit)', tail, re.IGNORECASE) or (key == 'temperature' and re.search(r'화씨|℉', clause)):
                    warn(f'{_LABELS[key]} 단위가 지원 단위와 달라 입력하지 않았습니다. 직접 확인해 주세요.')
                    continue
                numeric(key, match.group(1), match.group(), match.span(), tail)
        if re.search(r'혈압|맥박|심박|산소\s{0,2}포화도|spo2|체온|호흡\s{0,2}수|혈당', clause, re.IGNORECASE) and not any(k in candidates for k in _MEASUREMENTS):
            warn('측정 항목의 정확한 수치와 지원 단위를 확인해 주세요.')

        # Relative onset is accepted only when the text explicitly links it to
        # onset/current symptoms. Arbitrary times (meal, departure) are ignored.
        for match in re.finditer(r'(?<![0-9가-힣])(' + _NUM + r')\s{0,3}(분|시간|일)\s{0,3}전(?:부터)?', clause):
            # '마지막 정상'(뇌졸중의 마지막 정상 시각)도 발생 시각으로 본다. '부터'는 현재 증상(R1 주증상 표현 포함)과 함께일 때만.
            linked = bool(re.search(r'발생|시작|발현|마지막\s{0,3}정상|정상이었', clause) or
                          ('부터' in match.group() and (selected or voice_r1.mentions_complaint(clause))))
            if not linked:
                continue
            duration = _number(match.group(1))
            if any(scope(match.start(), match.end())) or type(duration) is not int or duration < 0:
                warn('발생 시각이 모호하여 입력하지 않았습니다. 확인해 주세요.')
                continue
            seconds = duration * {'분': 60, '시간': 3600, '일': 86400}[match.group(2)]
            if seconds > observed_at:
                warn('발생 시각의 계산 범위를 확인해 주세요.')
                continue
            add('onset_at', observed_at - seconds, clause)
            warn('발생 시각은 관찰 시각 기준 계산값입니다. 실제 시작 시각을 확인해 주세요.')
        if re.search(r'발생|시작|발현', clause) and re.search(r'[0-9]{1,2}\s{0,2}시|오전|오후|어제|오늘', clause) and 'onset_at' not in candidates:
            warn('절대 발생 시각은 날짜·시간을 직접 확인해 입력해 주세요.')

    fields, evidence = {}, {}
    for key, values in candidates.items():
        unique = []
        for value, quote in values:
            if value not in unique:
                unique.append(value)
        if len(unique) != 1:
            warn(f'{_LABELS[key]}에 서로 다른 값이 있어 자동 입력하지 않았습니다. 확인해 주세요.')
            continue
        fields[key] = unique[0]
        evidence[key] = list(dict.fromkeys(q for _, q in values))
        if len(values) > 1:
            warn(f'{_LABELS[key]}이 반복 언급되었습니다. 값을 확인해 주세요.')
    for symptom in _SYMPTOMS:
        states = [key for key in _LIST_FIELDS if symptom in symptom_states[key]]
        if len(states) > 1:
            warn(f'{symptom}의 있음·없음·미확인 진술이 상충하여 입력하지 않았습니다. 확인해 주세요.')
            continue
        for key in states:
            fields.setdefault(key, []).append(symptom)
            evidence.setdefault(key, []).extend(symptom_states[key][symptom])
    if ('age' in fields and 'age_group' in fields and fields['age'] // 10 * 10 != int(fields['age_group'][:-1])):
        for key in ('age', 'age_group'):
            fields.pop(key, None)
            evidence.pop(key, None)
        warn('나이와 연령대가 상충하여 입력하지 않았습니다. 확인해 주세요.')
    if ('systolic_bp' in fields and 'diastolic_bp' in fields and fields['systolic_bp'] <= fields['diastolic_bp']):
        for key in ('systolic_bp', 'diastolic_bp'):
            fields.pop(key, None)
            evidence.pop(key, None)
        warn('혈압의 수축기·이완기 순서가 모호하여 입력하지 않았습니다. 확인해 주세요.')
    for key in evidence:
        evidence[key] = list(dict.fromkeys(evidence[key]))
    return dict(fields=normalize_fields(fields), evidence=evidence, warnings=warnings)


def _command_field(text, include_readback=False):
    aliases = {'age': ('나이',), 'age_group': ('연령대',), 'sex': ('성별',),
               'consciousness': ('의식 상태', '의식'), 'breathing': ('호흡 상태', '호흡'),
               'onset_at': ('발생 시각', '발생시간', '시작 시각', '발생 시간'),
               'symptoms': ('증상',), 'denied_symptoms': ('없는 증상', '부정 증상'),
               'uncertain_symptoms': ('미확인 증상',),
               **{k: tuple(v['synonyms']) for k, v in _terms()['measurements'].items()}}
    for key, names in command_catalog().get('field_aliases', {}).items():
        if key in SUPPORTED_FIELDS or (include_readback and key == 'blood_pressure'):
            aliases[key] = tuple(dict.fromkeys((*aliases.get(key, ()), *names)))
    if include_readback:
        aliases['blood_pressure'] = ('혈압',)
    mentions = []
    for key, names in aliases.items():
        for alias in names:
            for match in re.finditer(_alias_pattern(alias), text, re.IGNORECASE):
                mentions.append((match.start(), match.end(), key))
    selected = []
    for item in sorted(mentions, key=lambda v: (-(v[1] - v[0]), v[0])):
        if not any(item[0] < old[1] and old[0] < item[1] for old in selected):
            selected.append(item)
    fields = {item[2] for item in selected}
    return next(iter(fields)) if len(fields) == 1 else None


def parse_command(text):
    from . import voice_commands
    return voice_commands.parse(_text(text), _command_field,
                                observation_fields=observation_fields, config=command_catalog())


def readback(fields, field=None):
    fields = normalize_fields(fields)
    if field is not None and field not in READBACK_FIELDS:
        _fail('지원하지 않는 읽기 항목입니다.')
    phrases = []
    names = {'male': '남성', 'female': '여성', 'unknown': '미확인',
             'alert': '명료', 'impaired': '저하', 'unresponsive': '반응 없음',
             'normal': '정상', 'abnormal': '비정상', 'absent': '없음'}
    selected = (SUPPORTED_FIELDS if field is None else
                ('systolic_bp', 'diastolic_bp') if field == 'blood_pressure' else (field,))
    for key in selected:
        if key not in fields:
            continue
        value = fields[key]
        if key in _LIST_FIELDS:
            display = ', '.join(value) if value else '기록 없음'
        elif key in _ENUMS:
            display = names[value]
        elif key == 'age':
            display = f'{value}세'
        elif key == 'onset_at':
            try:
                moment = datetime.fromtimestamp(value, timezone(timedelta(hours=9)))
                display = moment.strftime('%Y년 %m월 %d일 %H시 %M분, 한국 시간')
            except (ValueError, OverflowError, OSError):
                display = f'유닉스 시각 {value}초'
        elif key in _MEASUREMENTS:
            unit = {'mmHg': '밀리미터 수은주', '회/분': '회, 분당', '%': '퍼센트', '°C': '도 섭씨', 'mg/dL': '밀리그램 퍼 데시리터'}[_MEASUREMENTS[key]['unit']]
            display = f'{value} {unit}'
        elif key in voice_r1.FIELDS:
            display = voice_r1.display(key, value)
        else:
            display = str(value)
        phrases.append(f'{_LABELS[key]} {display}')
    return '. '.join(phrases) + '. 입력 내용을 확인해 주세요.' if phrases else '확인할 입력 항목이 없습니다.'
