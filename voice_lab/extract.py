"""음성 인식 문장 → R1 입력 칸 변경 제안.

propose_changes(draft, utterance, now)가 핵심이다. 처음 입력도 '빈 초안을 고치는 것'으로 처리한다.
- 결과는 제안이다. 화면이 보여 주고 사람이 확정한 변경만 apply_changes로 반영한다.
- 등급·진단을 추론하지 않는다. 말한 내용만 칸에 옮긴다.
- 전송 요청은 send_requested로만 알린다. 실제 전송은 화면 탭으로 한다.
"""
import copy
import re
import unicodedata

from . import terms

REQUIRED = ('avpu', 'breathing', 'chief_complaint', 'age_group', 'pre_ktas')
VITALS = ('sbp', 'dbp', 'hr', 'rr', 'spo2', 'temp', 'nrs', 'gcs', 'glucose')
# 입력 오인식을 거르는 범위다. 임상 기준값이 아니다.
VITAL_RANGES = {'sbp': (30, 300), 'dbp': (10, 200), 'hr': (20, 250), 'rr': (4, 80), 'spo2': (50, 100),
                'temp': (25.0, 45.0), 'nrs': (0, 10), 'gcs': (3, 15), 'glucose': (10, 1000)}
AGE_RANGE, PREGNANCY_WEEKS = (0, 120), (4, 45)
PRIORITY = {1: 4, 2: 4, 3: 2, 4: 1, 5: 1}     # Pre-KTAS → 서버 priority(4 높음, 2 중간, 1 낮음)
AVPU = ('A', 'V', 'P', 'U')
NEGATION_WINDOW = 6     # 표현 뒤 몇 글자 안의 부정어만 본다(띄어쓰기 제외)
POSITIVES = ('있', '보임')
CONSCIOUSNESS = {'A': 'alert', 'V': 'impaired', 'P': 'impaired', 'U': 'unresponsive',
                 'impaired': 'impaired', 'unknown': 'unknown'}

# ---------------------------------------------------------------- 숫자 정규화
_DIGIT = dict(영=0, 공=0, 일=1, 이=2, 삼=3, 사=4, 오=5, 육=6, 륙=6, 칠=7, 팔=8, 구=9)
_UNIT = dict(십=10, 백=100, 천=1000)
_SINO = '[영공일이삼사오육륙칠팔구십백천]+(?:점[영공일이삼사오육륙칠팔구]+)?'
_NATIVE_TENS = dict(열=10, 스물=20, 스무=20, 서른=30, 마흔=40, 쉰=50, 예순=60, 일흔=70, 여든=80, 아흔=90)
_NATIVE_ONES = dict(한=1, 두=2, 세=3, 네=4, 다섯=5, 여섯=6, 일곱=7, 여덟=8, 아홉=9)
_KEYWORD = (r'(?:혈압|수축기|이완기|맥박|심박수|심박|호흡수|산소\s*포화도|포화도|새츄레이션|새츄|세츄|체온|열|'
            r'통증\s*점수|통증|지씨에스|gcs|혈당|케이\s*타스|등급|레벨|임신|생후)')
_END = r'(?=\s*$|\s*[,.;%/]|\s*(?:에|오버|점|도|회|단계|등급|번|주|세|살|분)|\s+\d)'
_SINO_RULES = (
    # 혈압 구십 · 등급 이 · 체온 삼십팔점오. 한 글자 수는 뒤에 끝이나 단위가 올 때만 바꾼다('혈압이 높음' 보호).
    re.compile(r'(' + _KEYWORD + r'(?:(?:은|는|이|가)\s+|\s+))([영공일이삼사오육륙칠팔구십백천]{2,}(?:점[영공일이삼사오육륙칠팔구]+)?)()', re.I),
    re.compile(r'(' + _KEYWORD + r'(?:(?:은|는|이|가)\s+|\s+))([영공일이삼사오육륙칠팔구십백천])()' + _END, re.I),
    re.compile(r'(\d+\s*(?:에|/|오버)\s*)(' + _SINO + r')()'),                       # 90에 육십
    # 단위 앞의 수: 삼십분 전 · 육십대. 한 글자 수는 단위 뒤가 끊길 때만 바꾼다('이분이', '이대로' 보호).
    re.compile(r'((?<![가-힣]))([영공일이삼사오육륙칠팔구십백천]{2,}(?:점[영공일이삼사오육륙칠팔구]+)?)'
               r'(\s*(?:세|살|분|시간|주|도|점|회|단계|대|개월))'),
    re.compile(r'((?<![가-힣]))([영공일이삼사오육륙칠팔구])(\s*(?:세|살|분|시간|주|도|점|회|단계|개월))(?=\s|$|[,.]|전|후)'),
)
_NATIVE_RULE = re.compile(r'(?<![가-힣])((?:열|스물|스무|서른|마흔|쉰|예순|일흔|여든|아흔)(?:\s*(?:한|두|세|네|다섯|여섯|일곱|여덟|아홉))?'
                          r'|(?:한|두|세|네|다섯|여섯|일곱|여덟|아홉))\s*(살|시간)')


def _sino(word):
    """한자어 수('구십', '백이십', '삼십팔점오')와 무전식 한 자리 읽기('일이공')를 숫자 문자열로."""
    head, dot, tail = word.partition('점')
    if not head:
        return None
    if any(ch in _UNIT for ch in head):
        total, current = 0, None
        for ch in head:
            if ch in _UNIT:
                total += (1 if current is None else current) * _UNIT[ch]
                current = None
            elif current is None:
                current = _DIGIT[ch]
            else:
                return None
        whole = total + (current or 0)
    else:
        whole = int(''.join(str(_DIGIT[ch]) for ch in head))
    if not dot:
        return str(whole)
    return f"{whole}.{''.join(str(_DIGIT[ch]) for ch in tail)}" if tail else None


def _native(word):
    word = word.replace(' ', '')
    value = 0
    for name, number in sorted(_NATIVE_TENS.items(), key=lambda kv: -len(kv[0])):
        if word.startswith(name):
            value, word = number, word[len(name):]
            break
    if word:
        value += _NATIVE_ONES[word]
    return str(value)


def normalize(text):
    """띄어쓰기·전각 문자를 고르고, 말로 읽은 수를 숫자로 바꾼다."""
    text = unicodedata.normalize('NFKC', text or '')
    text = re.sub(r'\s+', ' ', text).strip()
    for rule in _SINO_RULES:
        text = rule.sub(lambda m: m.group(1) + (_sino(m.group(2)) or m.group(2)) + m.group(3), text)
    return _NATIVE_RULE.sub(lambda m: _native(m.group(1)) + m.group(2), text)


def _compact(text):
    return re.sub(r'\s+', '', text).lower()


# ---------------------------------------------------------------- 패턴
_P = r'(?:은|는|이|가|:)?\s*'
_VITAL_RULES = (
    ('bp', re.compile(r'(?:혈압|비피|bp)\s*' + _P + r'(\d{2,3})\s*(?:에|/|오버|over)\s*(\d{2,3})', re.I)),
    ('bp', re.compile(r'(?:혈압|비피|bp)\s*' + _P + r'(\d{2,3})\s+(\d{2,3})(?!\s*(?:세|살|대|분|시간|\d))', re.I)),
    ('sbp', re.compile(r'(?:수축기\s*(?:혈압)?|혈압|bp)\s*' + _P + r'(\d{2,3})(?!\d|\s*(?:에|/|오버|over)?\s*\d)', re.I)),
    ('hr', re.compile(r'(?:맥박|심박수|심박|펄스|hr)\s*' + _P + r'(\d{2,3})', re.I)),
    ('rr', re.compile(r'(?:호흡수|호흡\s*횟수|rr)\s*' + _P + r'(\d{1,2})|호흡\s*(\d{1,2})\s*회', re.I)),
    ('spo2', re.compile(r'(?:산소\s*포화도|포화도|새츄레이션|새츄|세츄|spo2|에스피오투|사포)\s*' + _P + r'(\d{2,3})', re.I)),
    ('temp', re.compile(r'(?:체온|열)\s*' + _P + r'(\d{2}(?:\.\d{1,2})?)|(\d{2}\.\d{1,2})\s*도', re.I)),
    ('nrs', re.compile(r'(?:통증\s*점수|nrs|엔알에스)\s*' + _P + r'(\d{1,2})|통증\s*(\d{1,2})\s*점', re.I)),
    ('gcs', re.compile(r'(?:gcs|지씨에스|글라스고)\s*' + _P + r'(\d{1,2})', re.I)),
    ('glucose', re.compile(r'(?:혈당|bst|비에스티)\s*' + _P + r'(\d{2,3})', re.I)),
)
_KTAS_RULES = (
    re.compile(r'(?:프리\s*케이\s*타스|pre\s*-?\s*ktas|케이\s*타스|ktas|중증도|등급|레벨)\s*' + _P + r'([1-5])(?!\d)', re.I),
    re.compile(r'(?<!\d)([1-5])\s*(?:단계|등급)'),
)
_KTAS_WORD = re.compile(r'(준응급|비응급|소생|긴급|응급)\s*(?:단계|등급)')
_KTAS_WORDS = {'소생': 1, '긴급': 2, '응급': 3, '준응급': 4, '비응급': 5}
_UNCLASSIFIED = re.compile(r'미분류|분류\s*(?:안|못)\s*(?:함|됨|했)')
_AGE = re.compile(r'(?<!\d)(\d{1,3})\s*(?:세|살)(?!\s*(?:미만|이하|이상))')
_DECADE = re.compile(r'(?<!\d)([1-9])0\s*대')
_NEWBORN = re.compile(r'생후\s*(\d{1,3})\s*(일|주|개월|달)')
_PREGNANCY = re.compile(r'임신\s*(\d{1,2})\s*주')
_ONSET = re.compile(r'(?:(\d{1,2})\s*시간\s*(반)?\s*)?(?:(\d{1,3})\s*분\s*)?전(?!화|원|신|혀|송|달|체|문|공|해|산)'
                    r'|반\s*시간\s*전')
_ONSET_UNKNOWN = re.compile(r'(?:발생|발병|시작|마지막\s*정상)\s*(?:시각|시간)?\s*' + _P + r'(?:모름|몰라|미상|불명)')
_CLEAR = re.compile(r'(의식|호흡수|호흡|주증상|연령대|나이|프리\s*케이\s*타스|케이\s*타스|등급|혈압|맥박|산소\s*포화도|포화도|체온|'
                    r'통증\s*점수|gcs|지씨에스|혈당)\s*' + _P +
                    r'(모름|몰라|모르겠|미확인|확인\s*안\s*됨|측정\s*안\s*됨|측정\s*불가|안\s*잡힘|삭제|지워|빼|취소)', re.I)
_CLEAR_FIELDS = {'의식': [('avpu', 'unknown')], '호흡': [('breathing', 'unknown')], '호흡수': [('vitals.rr', None)],
                 '주증상': [('chief_complaint', 'other')], '연령대': [('age_group', 'unknown'), ('age', None)],
                 '나이': [('age', None)], '케이타스': [('pre_ktas', 'unclassified')],
                 '프리케이타스': [('pre_ktas', 'unclassified')], '등급': [('pre_ktas', 'unclassified')],
                 '혈압': [('vitals.sbp', None), ('vitals.dbp', None)], '맥박': [('vitals.hr', None)],
                 '산소포화도': [('vitals.spo2', None)], '포화도': [('vitals.spo2', None)], '체온': [('vitals.temp', None)],
                 '통증점수': [('vitals.nrs', None)], 'gcs': [('vitals.gcs', None)], '지씨에스': [('vitals.gcs', None)],
                 '혈당': [('vitals.glucose', None)]}
_UNDO = re.compile(r'^(?:방금|아까|마지막)?\s*(?:거|것|입력|말한\s*거)?\s*(?:취소|되돌려|되돌리기|원래대로|실행\s*취소)'
                   r'\s*(?:해|해줘|해\s*줘|하기|해\s*주세요)?$')
_READBACK = re.compile(r'(?:읽어|불러|말해)\s*(?:줘|주세요|봐|줄래)|요약\s*(?:해|해줘|해\s*줘)|지금\s*(?:내용|입력)\s*(?:뭐|확인)')
_MISSING = re.compile(r'(?:빠진|남은|안\s*한|비어\s*있는|입력\s*안\s*된)\s*(?:거|것|항목|칸)|뭐\s*(?:남았|빠졌)')
_SEND = re.compile(r'전송|보내|문의\s*(?:해|시작)')
_CLAUSE = re.compile(r'[,;!?\n]+|\.(?!\d)|(?<!\d)\.|\s+그리고\s+')     # 38.5의 소수점에서는 끊지 않는다

_INDEX = sorted(((_compact(expr), entry) for entry in terms.TERMS for expr in entry['expr']),
                key=lambda item: -len(item[0]))


def _term_hits(compact):
    """긴 표현부터 겹치지 않게 찾는다."""
    taken, hits = [False] * len(compact), []
    for expr, entry in _INDEX:
        start = compact.find(expr)
        while start >= 0:
            end = start + len(expr)
            if not any(taken[start:end]):
                hits.append((start, end, expr, entry))
                taken[start:end] = [True] * len(expr)
            start = compact.find(expr, start + 1)
    return sorted(hits, key=lambda hit: hit[0])


# ---------------------------------------------------------------- 문장 해석
class _Found:
    def __init__(self):
        self.values, self.conflicts, self.complaints, self.ages = {}, {}, [], []
        self.rejected, self.ambiguities = [], []

    def put(self, field, value, order, evidence, note=None):
        prev = self.values.get(field)
        if prev is not None and prev[1] != value:
            seen = self.conflicts.setdefault(field, [])
            seen.extend(v for v in (prev[1], value) if v not in seen)
            if field == 'avpu' and {prev[1], value} & {'impaired'}:
                # '의식 저하'보다 구체적인 A·V·P·U를 말한 순서와 관계없이 우선한다
                if value == 'impaired' and prev[1] in AVPU:
                    return
                if prev[1] == 'impaired' and value in AVPU:
                    order = max(order, prev[0])
        if prev is None or order >= prev[0]:
            self.values[field] = (order, value, evidence, note)


def _bounded(field, value, found, evidence):
    low, high = VITAL_RANGES[field]
    if low <= value <= high:
        return True
    found.rejected.append(dict(field='vitals.' + field, value=value, reason=f'입력 범위 밖({low}~{high})', evidence=evidence))
    return False


def _number(text):
    return float(text) if '.' in text else int(text)


def _negated(window):
    """'흉통 없음'은 부정, '흉통 있고 식은땀 없음'의 흉통은 긍정."""
    def first(cues):
        found = [window.find(cue) for cue in cues if cue in window]
        return min(found) if found else None
    negative, positive = first(terms.NEGATIONS), first(POSITIVES)
    return negative is not None and (positive is None or negative < positive)


def _clause_terms(clause, index, found):
    compact = _compact(clause)
    hits = _term_hits(compact)
    for i, (start, end, expr, entry) in enumerate(hits):
        stop = min(hits[i + 1][0] if i + 1 < len(hits) else len(compact), end + NEGATION_WINDOW)
        negated = 'negated' in entry and _negated(compact[end:stop])
        pairs = entry['negated'] if negated else entry['sets']
        order = (index, start)
        for pair in pairs:
            field, value = pair[0], pair[1]
            if field == 'chief_complaint':
                explicit = re.search(r'주증상(?:은|는|이|가)?$', compact[:start]) is not None
                found.complaints.append(dict(order=order, value=value, weak=len(pair) > 2, explicit=explicit, evidence=expr))
            elif field == 'age_group':
                found.ages.append(dict(order=order, value=value, rank=3 if value == 'pregnant' else 1, evidence=expr))
            else:
                found.put(field, value, order, expr)


def _pos(clause, idx):
    return len(_compact(clause[:idx]))


def _clause_patterns(clause, index, found, now):
    has_bp = False
    for field, rule in _VITAL_RULES:
        for m in rule.finditer(clause):
            order, evidence = (index, _pos(clause, m.start())), m.group(0).strip()
            if field == 'bp':
                sbp, dbp = int(m.group(1)), int(m.group(2))
                has_bp = True
                if dbp >= sbp:
                    found.rejected.append(dict(field='vitals.dbp', value=dbp, reason='이완기가 수축기보다 크거나 같음',
                                               evidence=evidence))
                    continue
                if _bounded('sbp', sbp, found, evidence) and _bounded('dbp', dbp, found, evidence):
                    found.put('vitals.sbp', sbp, order, evidence)
                    found.put('vitals.dbp', dbp, order, evidence)
            elif field == 'sbp':
                if not has_bp and _bounded('sbp', int(m.group(1)), found, evidence):
                    found.put('vitals.sbp', int(m.group(1)), order, evidence, '이완기 없음')
            else:
                raw = next(g for g in m.groups() if g is not None)
                value = _number(raw)
                if _bounded(field, value, found, evidence):
                    found.put('vitals.' + field, value, order, evidence)
    for rule in _KTAS_RULES:
        for m in rule.finditer(clause):
            found.put('pre_ktas', int(m.group(1)), (index, _pos(clause, m.start())), m.group(0).strip())
    for m in _KTAS_WORD.finditer(clause):
        found.put('pre_ktas', _KTAS_WORDS[m.group(1)], (index, _pos(clause, m.start())), m.group(0).strip())
    for m in _UNCLASSIFIED.finditer(clause):
        found.put('pre_ktas', 'unclassified', (index, _pos(clause, m.start())), m.group(0).strip())
    for m in _AGE.finditer(clause):
        age = int(m.group(1))
        if AGE_RANGE[0] <= age <= AGE_RANGE[1]:
            order = (index, _pos(clause, m.start()))
            found.put('age', age, order, m.group(0).strip())
            found.ages.append(dict(order=order, value='child' if age < 15 else 'adult', rank=2, evidence=m.group(0).strip()))
    for m in _NEWBORN.finditer(clause):
        amount, unit = int(m.group(1)), m.group(2)
        days = amount * {'일': 1, '주': 7, '개월': 30, '달': 30}[unit]
        found.ages.append(dict(order=(index, _pos(clause, m.start())), value='neonate' if days <= 28 else 'child', rank=2,
                               evidence=m.group(0).strip()))
    for m in _DECADE.finditer(clause):
        if m.group(1) == '1':
            found.ambiguities.append(dict(field='age_group', candidates=['child', 'adult'], chosen=None,
                                          note='10대는 15세 기준으로 소아·성인을 가를 수 없음', evidence=m.group(0)))
        else:
            found.ages.append(dict(order=(index, _pos(clause, m.start())), value='adult', rank=1, evidence=m.group(0)))
    for m in _PREGNANCY.finditer(clause):
        weeks, order = int(m.group(1)), (index, _pos(clause, m.start()))
        if PREGNANCY_WEEKS[0] <= weeks <= PREGNANCY_WEEKS[1]:
            found.put('pregnancy_weeks', weeks, order, m.group(0).strip())
        found.ages.append(dict(order=order, value='pregnant', rank=3, evidence=m.group(0).strip()))
    for m in _ONSET.finditer(clause):
        if m.group(0).startswith('반'):
            minutes = 30
        elif m.group(1) is None and m.group(3) is None:
            continue
        else:
            minutes = int(m.group(1) or 0) * 60 + (30 if m.group(2) else 0) + int(m.group(3) or 0)
        found.put('onset_at', now - minutes * 60, (index, _pos(clause, m.start())), m.group(0).strip())
    for m in _ONSET_UNKNOWN.finditer(clause):
        found.put('onset_at', 'unknown', (index, _pos(clause, m.start())), m.group(0).strip())
    for m in _CLEAR.finditer(clause):
        key = _compact(m.group(1))
        for field, value in _CLEAR_FIELDS.get(key, []):
            found.put(field, value, (index, _pos(clause, m.start())), m.group(0).strip(), '말로 지움')


def _choose_complaint(found):
    if not found.complaints:
        return None
    explicit = [c for c in found.complaints if c['explicit']]
    strong = [c for c in found.complaints if not c['weak']]
    pool = explicit or strong or found.complaints
    chosen = min(pool, key=lambda c: c['order'])
    distinct = list(dict.fromkeys(c['value'] for c in (strong or pool)))
    if len(distinct) > 1 and not explicit:
        found.ambiguities.append(dict(field='chief_complaint', candidates=distinct, chosen=chosen['value'],
                                      note='주증상이 여러 개 언급됨. 먼저 말한 것을 골랐으니 확인 필요'))
    found.put('chief_complaint', chosen['value'], chosen['order'], chosen['evidence'])
    return chosen['value']


def _choose_age_group(found):
    if not found.ages:
        return
    best = max(found.ages, key=lambda a: (a['rank'], a['order']))
    found.put('age_group', best['value'], best['order'], best['evidence'])


def _injury_region(text, found, complaint):
    if complaint != 'trauma':
        return
    compact = _compact(text)
    regions = [name for name, words in terms.INJURY_REGIONS.items() if any(w in compact for w in words)]
    if regions:
        found.put('injury_region', regions[0] if len(regions) == 1 else 'multiple', (999, 0), '손상 부위')


def parse(utterance, now, context=None):
    """문장에서 칸 값과 명령을 찾는다. context는 지금 초안(외상 맥락 등)에 쓴다."""
    context = context or {}
    text = normalize(utterance)
    found = _Found()
    for index, clause in enumerate(c for c in _CLAUSE.split(text) if c and c.strip()):
        _clause_terms(clause, index, found)
        _clause_patterns(clause, index, found, now)
    complaint = _choose_complaint(found) or context.get('chief_complaint')
    _choose_age_group(found)
    _injury_region(text, found, complaint)
    for field, values in found.conflicts.items():
        specific_only = field == 'avpu' and 'impaired' in values and len(values) == 2
        if not specific_only and len(values) > 1:
            found.ambiguities.append(dict(field=field, candidates=values, chosen=found.values[field][1],
                                          note='같은 칸에 다른 값이 함께 언급됨. 나중에 말한 값을 골랐으니 확인 필요'))
    bare = re.sub(r'[\s,.!?]+$', '', text)
    commands = dict(undo=bool(_UNDO.search(bare)), readback=bool(_READBACK.search(text)),
                    missing=bool(_MISSING.search(text)), send=bool(_SEND.search(text)))
    return text, found, commands


# ---------------------------------------------------------------- 초안 다루기
def empty_draft():
    return {field: None for field in REQUIRED} | dict(age=None, onset_at=None, vitals={}, stemi_suspected=None,
                                                       bleeding_risk=None, high_risk_mechanism=None, injury_region=None,
                                                       pregnancy_weeks=None, labor=None, seizure_ongoing=None)


def _get(draft, field):
    if field.startswith('vitals.'):
        return (draft.get('vitals') or {}).get(field[7:])
    return draft.get(field)


def propose_changes(draft, utterance, now):
    """지금 초안과 새 문장을 받아 바꿀 칸 목록을 돌려준다. 초안은 바꾸지 않는다."""
    draft = empty_draft() | copy.deepcopy(draft or {})
    text, found, commands = parse(utterance, now, draft)
    changes = []
    for field, (order, value, evidence, note) in sorted(found.values.items(), key=lambda kv: kv[1][0]):
        before = _get(draft, field)
        if before != value:
            changes.append(dict(field=field, before=before, after=value, evidence=evidence, note=note))
    if found.values:
        intent = 'update'
    else:
        intent = next((name for name in ('undo', 'readback', 'missing', 'send') if commands[name]), 'none')
    return dict(intent=intent, changes=changes, ambiguities=found.ambiguities, rejected=found.rejected,
                send_requested=commands['send'], normalized=text)


def apply_changes(draft, changes):
    """사람이 확정한 변경만 반영한 새 초안."""
    out = empty_draft() | copy.deepcopy(draft or {})
    out['vitals'] = dict(out.get('vitals') or {})
    for change in changes:
        field, value = change['field'], change['after']
        if field.startswith('vitals.'):
            if value is None:
                out['vitals'].pop(field[7:], None)
            else:
                out['vitals'][field[7:]] = value
        else:
            out[field] = value
    return out


def missing_fields(draft):
    return [field for field in REQUIRED if (draft or {}).get(field) is None]


def _vitals_text(vitals):
    parts = []
    if 'sbp' in vitals:
        parts.append(f"혈압 {vitals['sbp']}에 {vitals['dbp']}" if 'dbp' in vitals else f"수축기 혈압 {vitals['sbp']}")
    parts += [f'{terms.VITAL_LABELS[k]} {vitals[k]}' for k in ('hr', 'rr', 'spo2') if k in vitals]
    if 'temp' in vitals:
        parts.append(f"체온 {vitals['temp']}도")
    if 'nrs' in vitals:
        parts.append(f"통증 {vitals['nrs']}점")
    parts += [f'{terms.VITAL_LABELS[k]} {vitals[k]}' for k in ('gcs', 'glucose') if k in vitals]
    return ', '.join(parts)


def readback_text(draft, now):
    """되읽어 주기용 짧은 문장(음성 합성용). 입력된 칸과 빠진 필수 칸만 말한다."""
    draft = empty_draft() | (draft or {})
    labels = terms.LABELS
    first = [labels[f][draft[f]] for f in ('avpu', 'breathing', 'chief_complaint') if draft[f] is not None]
    if draft['age_group'] is not None:
        first.append(labels['age_group'][draft['age_group']] + (f" {draft['age']}세" if draft['age'] is not None else ''))
    if draft['pre_ktas'] is not None:
        first.append('등급 미분류' if draft['pre_ktas'] == 'unclassified' else f"Pre-KTAS {draft['pre_ktas']}단계")
    sentences = [', '.join(first)] if first else []
    if draft['vitals']:
        sentences.append(_vitals_text(draft['vitals']))
    second = [labels[f][draft[f]] for f in ('stemi_suspected', 'injury_region', 'labor', 'seizure_ongoing',
                                            'bleeding_risk', 'high_risk_mechanism') if draft[f] is not None]
    if draft['pregnancy_weeks'] is not None:
        second.append(f"임신 {draft['pregnancy_weeks']}주")
    if draft['onset_at'] == 'unknown':
        second.append('발생 시각 모름')
    elif draft['onset_at'] is not None:
        second.append(f"발생 {max(0, (now - draft['onset_at']) // 60)}분 전")
    if second:
        sentences.append(', '.join(second))
    sentences.append(missing_text(draft))
    return '. '.join(s for s in sentences if s) + '.'


def missing_text(draft):
    missing = missing_fields(draft)
    if not missing:
        return '필수 항목 모두 입력됨'
    return '확인 필요: ' + ', '.join(terms.REQUIRED_LABELS[f] for f in missing)


# ---------------------------------------------------------------- 서버 입력으로 바꾸기
def profile_for(draft):
    """주증상·2차 고려사항·연령대로 서버 진료 프로필을 고른다(R1 문서 4절). (프로필, 이유)"""
    complaint, group = draft.get('chief_complaint'), draft.get('age_group')
    if complaint is None:
        return None, '주증상 없음'
    if complaint == 'obstetric' and draft.get('labor') == 'yes' and (draft.get('pregnancy_weeks') or 99) < 37:
        return 'TR_HIGH_RISK_BIRTH_NEONATAL', '임신 37주 미만 진통(확인 필요)'
    if group in ('child', 'neonate'):
        return 'TR_PEDIATRIC', '소아·신생아'
    if complaint == 'cardiac' and draft.get('stemi_suspected') == 'yes':
        return 'TR_CARDIAC_INTERVENTION', '심전도 ST 상승 확인'
    if complaint == 'stroke':
        return 'TR_NEUROSURGICAL', '뇌졸중 의심(현재 가장 가까운 프로필)'
    if complaint in ('trauma', 'altered_seizure') and draft.get('injury_region') in ('head', 'multiple'):
        return 'TR_NEUROSURGICAL', '머리 손상 동반'
    if complaint == 'trauma':
        return 'TR_ORTHOPEDIC', '외상'
    return 'TR_GENERAL_ED', '일반 응급 평가'


def to_server_payload(draft, *, consent, lat=None, lon=None):
    """사람이 확정한 초안을 서버 환자 등록 본문(POST /api/patients)으로 바꾼다. 필수 칸이 비면 거부한다."""
    draft = empty_draft() | (draft or {})
    missing = missing_fields(draft)
    if missing:
        raise ValueError('필수 항목이 비어 있습니다: ' + ', '.join(terms.REQUIRED_LABELS[f] for f in missing))
    profile, _ = profile_for(draft)
    level = draft['pre_ktas']
    extra = dict(chief_complaint=draft['chief_complaint'], treatment_profile_id=profile, age_group=draft['age_group'],
                 pre_ktas_level=level, input_source='voice_draft_confirmed')
    if draft['avpu'] in ('A', 'V', 'P', 'U'):
        extra['avpu'] = draft['avpu']
    if draft['vitals']:
        extra['vitals'] = dict(draft['vitals'])
    for key in ('stemi_suspected', 'bleeding_risk', 'high_risk_mechanism', 'injury_region', 'pregnancy_weeks', 'labor',
                'seizure_ongoing'):
        if draft[key] is not None:
            extra[key] = draft[key]
    priority = PRIORITY.get(level)
    summary = [terms.LABELS['chief_complaint'][draft['chief_complaint']]]
    summary += [terms.LABELS[k][draft[k]] for k in ('stemi_suspected', 'injury_region', 'labor', 'seizure_ongoing')
                if draft[k] is not None]
    payload = dict(reporter_role='transporter', consent=consent, consciousness=CONSCIOUSNESS[draft['avpu']],
                   breathing=draft['breathing'], symptoms=', '.join(summary), extra=extra,
                   priority=priority, priority_source='expert' if priority else 'unknown',
                   age=draft['age'], onset_at=draft['onset_at'] if isinstance(draft['onset_at'], int) else None)
    if lat is not None and lon is not None:
        payload.update(lat=lat, lon=lon)
    return payload
