"""R1 최소 입력 항목(docs/R1_MINIMUM_INPUT.md)을 음성 초안에 더한다.

voice_terms.extract의 결과는 바꾸지 않는다. 세션에 반영할 때 R1 칸을 따로 찾아 더한다.
R1 칸은 AVPU, 주증상 묶음, 연령 구분, Pre-KTAS, 2차 고려사항, 통증 점수, GCS다.
- 질문·과거력·정정 문장은 voice_terms와 같은 기준으로 건너뛴다.
- 같은 칸에 서로 다른 값이 나오면 채우지 않고 경고한다.
- 등급·진단을 추론하지 않는다. Pre-KTAS는 말한 경우에만 채운다. 주증상 묶음은 말한 증상을 R1 메뉴로
  옮긴 제안이며, 담당자 확인 뒤에만 환자정보에 반영된다.
"""
import re
import unicodedata

FIELDS = ('avpu', 'chief_complaint', 'age_category', 'pre_ktas', 'stemi_suspected', 'injury_region', 'labor',
          'pregnancy_weeks', 'seizure_ongoing', 'bleeding_risk', 'high_risk_mechanism', 'pain_score', 'gcs')
REQUIRED = ('avpu', 'breathing', 'chief_complaint', 'age_category', 'pre_ktas')
COMPLAINTS = ('cardiac', 'stroke', 'altered_seizure', 'dyspnea', 'trauma', 'bleeding', 'abdominal', 'obstetric',
              'toxic_env', 'other')
ENUMS = dict(avpu=('A', 'V', 'P', 'U', 'unknown'), chief_complaint=COMPLAINTS,
             age_category=('adult', 'child', 'neonate', 'pregnant', 'unknown'),
             pre_ktas=(1, 2, 3, 4, 5, 'unclassified'), stemi_suspected=('yes', 'no', 'unmeasured'),
             injury_region=('head', 'torso', 'limb', 'multiple'), labor=('yes', 'no'), seizure_ongoing=('yes', 'no'),
             bleeding_risk=('yes', 'no'), high_risk_mechanism=('yes', 'no'))
RANGES = dict(pregnancy_weeks=(4, 45), pain_score=(0, 10), gcs=(3, 15))
LABELS = dict(avpu='의식(AVPU)', chief_complaint='주증상', age_category='연령 구분', pre_ktas='Pre-KTAS 등급',
              stemi_suspected='심전도 ST 상승', injury_region='손상 부위', labor='진통·양막 파열', pregnancy_weeks='임신 주수',
              seizure_ongoing='경련 지속', bleeding_risk='출혈 경향', high_risk_mechanism='고위험 손상 기전',
              pain_score='통증 점수', gcs='GCS')
REQUIRED_LABELS = dict(avpu='의식(AVPU)', breathing='호흡', chief_complaint='주증상', age_category='연령 구분',
                       pre_ktas='Pre-KTAS 등급')
VALUE_LABELS = dict(
    avpu={'A': 'A 명료', 'V': 'V 목소리 반응', 'P': 'P 통증 반응', 'U': 'U 무반응', 'unknown': '모름'},
    chief_complaint={'cardiac': '흉통·심장', 'stroke': '뇌졸중 의심', 'altered_seizure': '의식저하·경련', 'dyspnea': '호흡곤란',
                     'trauma': '외상', 'bleeding': '출혈', 'abdominal': '복통·소화기', 'obstetric': '산과·분만',
                     'toxic_env': '중독·환경', 'other': '기타'},
    age_category={'adult': '성인', 'child': '소아', 'neonate': '신생아', 'pregnant': '임신 중', 'unknown': '모름'},
    stemi_suspected={'yes': '확인됨', 'no': '없음', 'unmeasured': '미측정'},
    injury_region={'head': '머리', 'torso': '몸통', 'limb': '팔다리', 'multiple': '여러 곳'},
    labor={'yes': '있음', 'no': '없음'}, seizure_ongoing={'yes': '지속 중', 'no': '멈춤'},
    bleeding_risk={'yes': '있음', 'no': '없음'}, high_risk_mechanism={'yes': '있음', 'no': '없음'})
PRIORITY = {1: 4, 2: 4, 3: 2, 4: 1, 5: 1}     # Pre-KTAS → 서버 priority(4 높음, 2 중간, 1 낮음)
AVPU_TO_CONSCIOUSNESS = {'A': 'alert', 'V': 'impaired', 'P': 'impaired', 'U': 'unresponsive', 'unknown': 'unknown'}
CONSCIOUSNESS_TO_AVPU = {'alert': 'A', 'unresponsive': 'U', 'unknown': 'unknown'}
VITAL_KEYS = (('systolic_bp', 'sbp'), ('diastolic_bp', 'dbp'), ('pulse', 'hr'), ('respiratory_rate', 'rr'),
              ('spo2', 'spo2'), ('temperature', 'temp'), ('glucose', 'glucose'), ('pain_score', 'nrs'), ('gcs', 'gcs'))
SECOND_ORDER = ('stemi_suspected', 'injury_region', 'labor', 'pregnancy_weeks', 'seizure_ongoing', 'bleeding_risk',
                'high_risk_mechanism')
PATIENT_EXTRA_FIELDS = dict(avpu='avpu', chief_complaint='chief_complaint',
                           age_category='age_group', pre_ktas='pre_ktas_level',
                           **{key: key for key in SECOND_ORDER})

# ---------------------------------------------------------------- 용어
# sets: (칸, 값[, 'weak']). weak 주증상은 다른 주증상이 있으면 밀린다. negated: 뒤에 부정어가 오면 대신 넣을 값(빈 목록이면 무시).
# negated가 없는 표현은 부정 판단을 하지 않는다. sets가 빈 항목은 다른 표현의 오인을 막는 차단어다.
TERMS = [
    dict(sets=[], expr=['진통제', '경련약', '항경련제', '천식약', '혈압약', '분만실', '응급실']),
    dict(sets=[('avpu', 'A')], expr=['의식 명료', '의식 깨어 있', '의식 또렷', '깨어 있음', '얼러트', 'alert'], negated=[]),
    dict(sets=[('avpu', 'V')], expr=['목소리에 반응', '부르면 반응', '불러야 반응', '언어 반응', '언어에 반응', '깨우면 눈',
                                     '부르면 눈', '불러야 눈', '버벌', 'verbal'], negated=[]),
    dict(sets=[('avpu', 'P')], expr=['통증에 반응', '통증에만 반응', '통증 자극에 반응', '통증 자극에만', '자극에만 반응',
                                     '꼬집으면 반응', '페인 반응'], negated=[('avpu', 'U')]),
    dict(sets=[('avpu', 'U')], expr=['무반응', '반응 없', '의식 없', '의식 소실', '언리스판시브']),
    dict(sets=[('chief_complaint', 'altered_seizure', 'weak')], expr=['의식 저하', '의식 떨어', '의식 혼미', '기면'], negated=[]),
    dict(sets=[('breathing', 'normal')], expr=['숨 잘 쉼', '숨 잘 쉬', '숨 편안', '호흡 안정', '호흡 양호'], negated=[]),
    dict(sets=[('breathing', 'abnormal')], expr=['호흡 이상'], negated=[('breathing', 'normal')]),
    dict(sets=[('breathing', 'abnormal'), ('chief_complaint', 'dyspnea')],
         expr=['호흡곤란', '호흡 곤란', '숨 차', '숨참', '숨 쉬기 힘들', '숨쉬기 힘들', '숨쉬기 힘듦', '숨 쉬기 힘듦', '호흡 힘듦',
               '숨쉬기 어렵', '숨 쉬기 어렵', '숨 가쁘', '숨 가쁨', '숨 막', '헐떡'], negated=[]),
    dict(sets=[('breathing', 'abnormal')],
         expr=['호흡 빠름', '호흡 빠르', '빈호흡', '호흡 느림', '호흡 느리', '쌕쌕', '천명', '그렁거림', '호흡 불규칙'], negated=[]),
    dict(sets=[('breathing', 'absent')], expr=['호흡 없', '무호흡', '숨 안 쉼', '숨 안 쉬', '숨 안쉼', '호흡 정지', '숨 쉬지 않']),
    dict(sets=[('chief_complaint', 'cardiac')],
         expr=['흉통', '가슴 통증', '가슴 아프', '가슴 아픔', '가슴 아파', '가슴 답답', '흉부 통증', '흉부 압박감', '가슴 쥐어짜',
               '쥐어짜는 통증', '가슴 두근', '두근거림', '심계항진', '실신', '기절', '심근경색', '협심증'], negated=[]),
    dict(sets=[('chief_complaint', 'cardiac'), ('avpu', 'U'), ('breathing', 'absent')],
         expr=['심정지', '심폐정지', '어레스트', '심폐소생술', 'cpr']),
    dict(sets=[('chief_complaint', 'stroke')],
         expr=['편마비', '한쪽 마비', '반신 마비', '한쪽 팔다리 마비', '한쪽 힘 빠짐', '한쪽에 힘 없', '팔다리에 힘 없',
               '팔다리 힘 빠짐', '말 어눌', '발음 어눌', '말 못', '구음장애', '언어장애', '실어증', '안면마비', '얼굴 마비',
               '얼굴 처', '입 돌아', '입꼬리 처', '뇌졸중', '중풍', '스트로크', '뇌경색', '뇌출혈'], negated=[]),
    dict(sets=[('chief_complaint', 'altered_seizure')], expr=['경련', '발작', '간질', '뇌전증', '혼수'], negated=[]),
    dict(sets=[('chief_complaint', 'dyspnea')], expr=['천식', '천식 발작', 'copd'], negated=[]),
    dict(sets=[('chief_complaint', 'trauma')],
         expr=['교통사고', '차 사고', '자동차 사고', '오토바이 사고', '자전거 사고', '보행자 사고', '차에 치', '추락',
               '높은 곳에서 떨어', '낙상', '넘어지', '넘어졌', '넘어짐', '넘어져', '관통상', '자상', '찔렸', '찔림', '칼에',
               '베임', '베였', '열상', '골절', '부러', '삐었', '삠', '염좌', '외상', '다쳤', '다침', '부딪', '폭행', '화상',
               '끼임', '압궤', '미끄러', '찰과상', '타박상', '접질', '삐끗'], negated=[]),
    dict(sets=[('chief_complaint', 'bleeding')],
         expr=['토혈', '피 토', '하혈', '질 출혈', '혈변', '흑변', '객혈', '각혈', '대량 출혈', '출혈', '피 많이', '코피'], negated=[]),
    dict(sets=[('chief_complaint', 'abdominal')],
         expr=['복통', '복부 통증', '배 아프', '배 아픔', '배 아파', '배 통증', '명치 통증', '명치 아프', '구토', '토함', '토했',
               '설사', '오심'], negated=[]),
    dict(sets=[('chief_complaint', 'obstetric', 'weak'), ('age_category', 'pregnant')],
         expr=['임신', '임산부', '산모', '임부'], negated=[]),
    dict(sets=[('chief_complaint', 'obstetric')], expr=['분만', '출산'], negated=[]),
    dict(sets=[('chief_complaint', 'toxic_env')],
         expr=['중독', '음독', '약물 과다', '과다 복용', '과량 복용', '약 많이 먹', '수면제', '농약', '일산화탄소', '연탄',
               '가스 흡입', '열사병', '일사병', '열탈진', '저체온', '동상', '벌에 쏘', '벌 쏘', '말벌', '벌독', '뱀에 물',
               '아나필락시스', '알레르기 반응'],
         negated=[]),
    dict(sets=[('chief_complaint', 'other')], expr=['정신과', '자해', '자살 시도', '환청'], negated=[]),
    dict(sets=[('chief_complaint', 'other', 'weak')], expr=['두통', '어지럼', '어지러움', '현기증', '발열', '고열', '요통', '발진'],
         negated=[]),
    dict(sets=[('stemi_suspected', 'yes'), ('chief_complaint', 'cardiac')],
         expr=['st 상승', 'st분절 상승', 'st 분절 상승', '에스티 상승', 'st elevation', 'stemi', '스테미'],
         negated=[('stemi_suspected', 'no')]),
    dict(sets=[('stemi_suspected', 'no')], expr=['st 변화 없', 'st 정상', '심전도 정상']),
    dict(sets=[('stemi_suspected', 'unmeasured')],
         expr=['심전도 안 찍', '심전도 못 찍', '심전도 미측정', '심전도 측정 안', '12유도 없', '심전도 없음']),
    dict(sets=[('bleeding_risk', 'yes')],
         expr=['항응고제', '와파린', '헤파린', '혈액 희석제', '피 묽게 하는 약', '출혈 경향', '엘리퀴스', '자렐토', '프라닥사', '릭시아나'],
         negated=[('bleeding_risk', 'no')]),
    dict(sets=[('high_risk_mechanism', 'yes')], expr=['고위험 기전', '고위험 손상 기전', '고에너지 손상', '고에너지 외상'],
         negated=[('high_risk_mechanism', 'no')]),
    dict(sets=[('labor', 'yes'), ('chief_complaint', 'obstetric')],
         expr=['진통', '양수 터', '양막 파열', '파수', '분만 진행', '아기 나오', '애기 나오', '아기 나올', '애기 나올'],
         negated=[('labor', 'no')]),
    dict(sets=[('seizure_ongoing', 'yes'), ('chief_complaint', 'altered_seizure')],
         expr=['경련 중', '경련 지속', '계속 경련', '아직 경련', '경련하고 있', '경련 계속']),
    dict(sets=[('seizure_ongoing', 'no'), ('chief_complaint', 'altered_seizure')],
         expr=['경련 멈', '경련 끝', '경련 후', '경련 멎', '경련 중단', '경련 중지']),
    dict(sets=[('age_category', 'adult')],
         expr=['성인', '할머니', '할아버지', '어르신', '노인', '아저씨', '아주머니', '아줌마', '고등학생', '대학생', '직장인']),
    dict(sets=[('age_category', 'child')],
         expr=['소아', '어린이', '아동', '유아', '영아', '아기', '애기', '아이', '초등학생', '유치원생', '중학생', '남아', '여아']),
    dict(sets=[('age_category', 'neonate')], expr=['신생아', '갓난아기', '갓난아이', '갓 태어난']),
]
NEGATIONS = ('없', '아니', '아님', '음성', '부정', '않', '안함', '안됨', '안했')
POSITIVES = ('있', '보임')
NEGATION_WINDOW = 6
INJURY_REGIONS = {
    'head': ('머리', '두부', '이마', '얼굴', '안면', '뒤통수'),
    'torso': ('흉부', '가슴', '복부', '배를', '배에', '골반', '허리', '갈비', '늑골', '등을', '옆구리'),
    'limb': ('팔을', '팔이', '팔에', '팔골절', '왼팔', '오른팔', '팔다리', '다리', '손목', '발목', '무릎', '어깨', '대퇴', '허벅지',
             '정강이', '손가락', '발가락', '사지'),
}
# 핵심어 뒤 조사(은·는·이·가·도·을·를)를 지우고 비교한다: '의식은 명료하나', '숨도 안 쉼'
_HEADS = '의식|호흡|숨|반응|가슴|흉통|복통|통증|출혈|경련|마비|머리|얼굴|팔다리|말|배|입|입꼬리|힘|양수|아기|애기|피|약|ST|st'
_PARTICLE = re.compile(r'(' + _HEADS + r')(?:은|는|이|가|도|을|를|에)(?=\S)')


def _compact(text):
    return _PARTICLE.sub(r'\1', re.sub(r'\s+', '', text)).lower()


_INDEX = sorted(((_compact(expr), entry) for entry in TERMS for expr in entry['expr']), key=lambda item: -len(item[0]))

# ---------------------------------------------------------------- 수 정규화(R1 칸 읽기 전용)
_DIGIT = dict(영=0, 공=0, 일=1, 이=2, 삼=3, 사=4, 오=5, 육=6, 륙=6, 칠=7, 팔=8, 구=9)
_UNIT = dict(십=10, 백=100, 천=1000)
_NATIVE_TENS = dict(열=10, 스물=20, 스무=20, 서른=30, 마흔=40, 쉰=50, 예순=60, 일흔=70, 여든=80, 아흔=90)
_NATIVE_ONES = dict(한=1, 두=2, 세=3, 네=4, 다섯=5, 여섯=6, 일곱=7, 여덟=8, 아홉=9)
_KEYWORD = r'(?:gcs|지씨에스|통증\s*점수|통증|케이\s*타스|등급|레벨|임신|생후)'
_END = r'(?=\s*$|\s*[,.;%/]|\s*(?:점|단계|등급|번|주|세|살)|\s+\d)'
_SINO_RULES = (
    re.compile(r'(' + _KEYWORD + r'(?:(?:은|는|이|가)\s+|\s+))([영공일이삼사오육륙칠팔구십백천]{2,})()', re.I),
    re.compile(r'(' + _KEYWORD + r'(?:(?:은|는|이|가)\s+|\s+))([영공일이삼사오육륙칠팔구십백천])()' + _END, re.I),
    re.compile(r'((?<![가-힣]))([영공일이삼사오육륙칠팔구십백천]{2,})(\s*(?:세|살|주|점|단계|일|개월))'),
    re.compile(r'((?<![가-힣]))([영공일이삼사오육륙칠팔구])(\s*(?:세|살|주|점|단계|개월))(?=\s|$|[,.])'),
)
_NATIVE_RULE = re.compile(r'(?<![가-힣])((?:열|스물|스무|서른|마흔|쉰|예순|일흔|여든|아흔)(?:\s*(?:한|두|세|네|다섯|여섯|일곱|여덟|아홉))?'
                          r'|(?:한|두|세|네|다섯|여섯|일곱|여덟|아홉))\s*(살)')


def _sino(word):
    if any(ch in _UNIT for ch in word):
        total, current = 0, None
        for ch in word:
            if ch in _UNIT:
                total += (1 if current is None else current) * _UNIT[ch]
                current = None
            elif current is None:
                current = _DIGIT[ch]
            else:
                return None
        return str(total + (current or 0))
    return str(int(''.join(str(_DIGIT[ch]) for ch in word)))


def _native(word):
    word, value = word.replace(' ', ''), 0
    for name, number in sorted(_NATIVE_TENS.items(), key=lambda kv: -len(kv[0])):
        if word.startswith(name):
            value, word = number, word[len(name):]
            break
    return str(value + (_NATIVE_ONES[word] if word else 0))


def _numbers(text):
    text = unicodedata.normalize('NFKC', text)
    for rule in _SINO_RULES:
        text = rule.sub(lambda m: m.group(1) + (_sino(m.group(2)) or m.group(2)) + m.group(3), text)
    return _NATIVE_RULE.sub(lambda m: _native(m.group(1)) + m.group(2), text)


_P = r'(?:은|는|이|가|:)?\s*'
_KTAS = (re.compile(r'(?:프리\s*케이\s*타스|pre\s*-?\s*ktas|케이\s*타스|ktas|중증도|등급|레벨)\s*' + _P + r'([1-5])(?!\d)', re.I),
         re.compile(r'(?<!\d)([1-5])\s*(?:단계|등급)'))
_KTAS_WORD = re.compile(r'(준응급|비응급|소생|긴급|응급)\s*(?:단계|등급)')
_KTAS_WORDS = {'소생': 1, '긴급': 2, '응급': 3, '준응급': 4, '비응급': 5}
_UNCLASSIFIED = re.compile(r'미분류|분류\s*(?:안|못)\s*(?:함|됨|했)|등급\s*(?:모름|몰라|미정)')
_GCS = re.compile(r'(?:gcs|지씨에스|글라스고)\s*' + _P + r'(\d{1,2})(?!\d)', re.I)
_PAIN = re.compile(r'(?:통증\s*점수|nrs|엔알에스)\s*' + _P + r'(\d{1,2})(?!\d)|통증\s*(\d{1,2})\s*점', re.I)
_AGE = re.compile(r'(?<!\d)(\d{1,3})\s*(?:세|살)(?!\s*(?:미만|이하|이상))')
_DECADE = re.compile(r'(?<!\d)([1-9])0\s*대')
_NEWBORN = re.compile(r'생후\s*(\d{1,3})\s*(일|주|개월|달)')
_PREGNANCY = re.compile(r'임신\s*(\d{1,2})\s*주')
_AVPU_LETTER = re.compile(r'(?:avpu|의식)\s*(?:은|는|이|가|:)?\s*([avpu])(?![a-z가-힣])', re.I)
_AVPU_UNKNOWN = re.compile(r'(?:의식|avpu)\s*(?:상태)?\s*(?:은|는|이|가)?\s*(?:아직\s*)?'
                           r'(?:모름|몰라|미확인|확인\s*(?:안|못)\s*(?:됨|함|했|하))', re.I)
_BREATH_UNKNOWN = re.compile(r'호흡\s*(?:상태)?\s*(?:은|는|이|가)?\s*(?:아직\s*)?(?:모름|몰라|확인\s*(?:안|못)\s*(?:됨|함|했|하))')
_LATERAL = re.compile(r'(?:왼쪽|오른쪽|한쪽|좌측|우측)\s*(?:팔다리|팔|다리|얼굴|안면)\S{0,3}\s*(?:에\s*)?(?:힘|마비|처짐|처져|저림)')
_KTAS_STANDALONE = re.compile(r'(?<![가-힣])(비응급|준응급)\s*(?:환자|상태)?\s*$')     # '단계' 없이 끝에 말한 등급
_SEIZURE_STOPPED = re.compile(r'(?<!안 )(?<!안)(?:멈춤|멈췄|멎었|멎음|그침|그쳤|끝남|끝났)')
_UNCERTAIN_COMPACT = re.compile(r'미확인|확인(?:안|불가|못|중|필요|예정)|모름|모르|불명|의심|가능성|추정|같음|같아|애매|확실치|'
                                r'(?:않|있|없)을수|(?:있|없)는지|여부')


# ---------------------------------------------------------------- 검사·표시
def validate(key, value, fail):
    if key in ENUMS:
        ok = type(value) in (str, int) and value in ENUMS[key]
    else:
        low, high = RANGES[key]
        ok = type(value) is int and low <= value <= high
    if not ok:
        fail(f'{LABELS[key]} 값이 올바르지 않습니다.')
    return value


def check(fields, fail):
    if 'avpu' in fields and 'consciousness' in fields and \
            AVPU_TO_CONSCIOUSNESS[fields['avpu']] != fields['consciousness']:
        fail('의식(AVPU)과 의식 상태가 일치하지 않습니다.')


def display(key, value):
    if key in VALUE_LABELS:
        return VALUE_LABELS[key][value]
    if key == 'pre_ktas':
        return '미분류' if value == 'unclassified' else f'{value}단계'
    if key == 'pregnancy_weeks':
        return f'{value}주'
    if key == 'pain_score':
        return f'{value}점'
    return str(value)


def missing(fields):
    return [key for key in REQUIRED if key not in fields]


def missing_text(fields):
    gaps = missing(fields)
    if not gaps:
        return 'R1 필수 항목이 모두 입력됐습니다.'
    return '확인이 필요한 필수 항목: ' + ', '.join(REQUIRED_LABELS[k] for k in gaps) + '.'


def _category_from(fields):
    if 'age' in fields:
        return 'child' if fields['age'] < 15 else 'adult'
    decade = fields.get('age_group')
    if decade:
        start = int(decade[:-1])
        return 'child' if start == 0 else None if start == 10 else 'adult'
    return None


def reconcile(fields, patch):
    """병합 뒤 AVPU·연령 구분을 의식 상태·나이와 맞춘다. 바뀐 칸 목록을 돌려준다."""
    changed = []
    if 'avpu' in patch and 'consciousness' not in patch:
        fields['consciousness'] = AVPU_TO_CONSCIOUSNESS[patch['avpu']]
        changed.append('consciousness')
    elif 'consciousness' in patch and 'avpu' not in patch and 'avpu' in fields \
            and AVPU_TO_CONSCIOUSNESS[fields['avpu']] != patch['consciousness']:
        fields.pop('avpu')                       # 어긋난 AVPU는 지우고 다시 확인받는다
        changed.append('avpu')
    if ({'age', 'age_group'} & patch.keys()) and 'age_category' not in patch and fields.get('age_category') != 'pregnant':
        derived = _category_from(fields)
        if derived is not None and fields.get('age_category') != derived:
            fields['age_category'] = derived
            changed.append('age_category')
    return changed


def profile_for(fields):
    """R1 4절: 주증상·2차 고려사항·연령 구분으로 서버 진료 프로필을 고른다. 주증상이 없으면 None."""
    complaint, group = fields.get('chief_complaint'), fields.get('age_category')
    if complaint is None:
        return None
    if complaint == 'obstetric' and fields.get('labor') == 'yes' and fields.get('pregnancy_weeks', 99) < 37:
        return 'TR_HIGH_RISK_BIRTH_NEONATAL'
    if group in ('child', 'neonate'):
        return 'TR_PEDIATRIC'
    if complaint == 'cardiac' and fields.get('stemi_suspected') == 'yes':
        return 'TR_CARDIAC_INTERVENTION'
    if complaint == 'stroke':
        return 'TR_NEUROSURGICAL'
    if complaint in ('trauma', 'altered_seizure') and fields.get('injury_region') in ('head', 'multiple'):
        return 'TR_NEUROSURGICAL'
    if complaint == 'trauma':
        return 'TR_ORTHOPEDIC'
    return 'TR_GENERAL_ED'


def patient_fields(fields, extra, derive_profile=True, *, removed_fields=()):
    """제출할 환자 칸에 R1 값을 더한다. extra는 그 자리에서 고친다. 말하지 않은 칸은 넣지 않는다.

    derive_profile: 새 환자일 때만 주증상으로 진료 프로필을 고른다. 기존 환자의 진료 요구는 자동으로 바꾸지 않는다.
    기존 환자에는 바뀐 칸만 전달한다. removed_fields는 미러 값도 지우며,
    측정값 수정은 다른 활력징후·측정시각 메타데이터를 유지한다.
    """
    patch = {}
    removed = set(removed_fields)
    for key in removed:
        if key in PATIENT_EXTRA_FIELDS:
            extra.pop(PATIENT_EXTRA_FIELDS[key], None)
    if 'pre_ktas' in removed:
        patch.update(priority=None, priority_source='unknown')
    if 'avpu' in fields:
        extra['avpu'] = fields['avpu']
        if 'consciousness' not in fields:
            patch['consciousness'] = AVPU_TO_CONSCIOUSNESS[fields['avpu']]
    if 'chief_complaint' in fields:
        extra['chief_complaint'] = fields['chief_complaint']
    if 'age_category' in fields:
        extra['age_group'] = fields['age_category']
    if 'pre_ktas' in fields:
        extra['pre_ktas_level'] = fields['pre_ktas']
        priority = PRIORITY.get(fields['pre_ktas'])
        patch.update(priority=priority, priority_source='expert' if priority else 'unknown')
    if any(key in fields or key in removed for key, _ in VITAL_KEYS):
        # Amend one measurement without replacing unrelated measurements or
        # metadata supplied by another input channel.
        vitals = dict(extra['vitals']) if isinstance(extra.get('vitals'), dict) else {}
        for key, name in VITAL_KEYS:
            if key in removed:
                vitals.pop(name, None)
            if key in fields:
                vitals[name] = fields[key]
        if vitals:
            extra['vitals'] = vitals
        else:
            extra.pop('vitals', None)
    for key in SECOND_ORDER:
        if key in fields:
            extra[key] = fields[key]
    profile = profile_for(fields) if derive_profile else None
    if profile and 'treatment_profile_id' not in extra:
        extra['treatment_profile_id'] = profile
    return patch


# ---------------------------------------------------------------- 추출
def mentions_complaint(text):
    """부정되지 않은 R1 주증상 표현이 있는지. voice_terms가 '…부터'를 발생 시각으로 볼지 판단할 때 쓴다."""
    compact = _compact(text)
    hits = _term_hits(compact)
    for i, (start, end, expr, entry) in enumerate(hits):
        if any(pair[0] == 'chief_complaint' for pair in entry['sets']):
            stop = min(hits[i + 1][0] if i + 1 < len(hits) else len(compact), end + NEGATION_WINDOW)
            if not ('negated' in entry and _negated(compact[end:stop])):
                return True
    return False


def _negated(window):
    def first(cues):
        found = [window.find(cue) for cue in cues if cue in window]
        return min(found) if found else None
    negative, positive = first(NEGATIONS), first(POSITIVES)
    return negative is not None and (positive is None or negative < positive)


def _term_hits(compact):
    taken, hits = [False] * len(compact), []
    from . import voice_terms
    terms = voice_terms.r1_dictionary_terms()
    index = _INDEX if terms is None else sorted(
        ((_compact(expr), entry) for entry in terms for expr in entry['expr']), key=lambda item: -len(item[0]))
    for expr, entry in index:
        start = compact.find(expr)
        while start >= 0:
            end = start + len(expr)
            if not any(taken[start:end]):
                hits.append((start, end, expr, entry))
                taken[start:end] = [True] * len(expr)
            start = compact.find(expr, start + 1)
    return sorted(hits, key=lambda hit: hit[0])


def extract(text, observed_at, base=None, context=None):
    """R1 칸만 찾는다. dict(fields, evidence, warnings).

    base는 같은 발화에서 voice_terms가 찾은 칸(의식·호흡·나이 등), context는 지금 세션 초안이다(외상 부위 판단용).
    """
    from . import voice_terms as vt       # voice_terms가 이 모듈을 불러오므로 함수 안에서만 쓴다
    base, context = base or {}, context or {}
    warnings, values, complaints, ages = [], {}, [], []
    # The basic parser resolves shared trailing denial in symptom lists. Use
    # that explicit result only for exact aliases, preserving independent R1
    # evidence (e.g. denied chest pain plus suspected myocardial infarction).
    denied = set(base.get('denied_symptoms', ()))
    denied_aliases = {_compact(alias) for item in vt._terms()['symptoms']
                     if item['canonical'] in denied for alias in item['synonyms']}

    def warn(message):
        if message not in warnings:
            warnings.append(message)

    def put(field, value, order, quote):
        values.setdefault(field, []).append((order, value, quote))

    if vt._CORRECTION.search(text):
        return dict(fields={}, evidence={}, warnings=[])
    seizure_mentioned = False
    for c_index, clause in enumerate(vt._clauses(text)):
        clause, _ = vt._current(clause)          # 과거력·가족·질문 구간은 voice_terms와 같은 기준으로 뺀다
        for p_index, part in enumerate(p for p in re.split(r'[,，]', clause) if p.strip()):
            order, quote = (c_index, p_index), part.strip()
            number_text, compact = _numbers(part), _compact(part)

            def unsure(text_, start, end, rx=vt._UNCERTAIN):
                """값 [start, end) 뒤 QUALIFIER_WINDOW자 안의 불확실 표현(voice_terms와 같은 범위 규칙)."""
                return any(start <= m.start() <= end + vt.QUALIFIER_WINDOW for m in rx.finditer(text_))

            def rough(start, end):
                return (any(start <= m.start() <= end + vt.QUALIFIER_WINDOW for m in vt._APPROX_AFTER.finditer(number_text))
                        or any(start - vt.QUALIFIER_WINDOW <= m.start() < end for m in vt._APPROX_BEFORE.finditer(number_text)))

            def exact(m):
                return not (unsure(number_text, m.start(), m.end()) or rough(m.start(), m.end()))

            if _AVPU_UNKNOWN.search(part):
                put('avpu', 'unknown', order + (0,), quote)
            if _BREATH_UNKNOWN.search(part):
                put('breathing', 'unknown', order + (0,), quote)
            hits = _term_hits(compact)
            for i, (start, end, expr, entry) in enumerate(hits):
                if 'negated' in entry and _compact(expr) in denied_aliases:
                    continue
                stop = min(hits[i + 1][0] if i + 1 < len(hits) else len(compact), end + NEGATION_WINDOW)
                negated = 'negated' in entry and _negated(compact[end:stop])
                cues = {m.group() for m in _UNCERTAIN_COMPACT.finditer(compact)
                        if start <= m.start() <= end + vt.QUALIFIER_WINDOW}
                uncertain = bool(cues)
                for pair in (entry['negated'] if negated else entry['sets']):
                    field, value, weak = pair[0], pair[1], len(pair) > 2
                    at = order + (start,)
                    if field == 'chief_complaint':
                        # 주증상 묶음 이름 자체가 '의심' 수준(예: 뇌졸중 의심)이라 불확실 표현이 붙어도 후보로 둔다.
                        explicit = re.search(r'주증상(?:은|는|이|가)?$', compact[:start]) is not None
                        complaints.append(dict(order=at, value=value, weak=weak, explicit=explicit, quote=quote))
                        seizure_mentioned = seizure_mentioned or expr in ('경련', '발작', '간질', '뇌전증')
                    elif field == 'age_category':
                        if not uncertain:
                            ages.append(dict(order=at, value=value, rank=3 if value == 'pregnant' else 1, quote=quote))
                    elif uncertain and field == 'stemi_suspected' and value == 'yes' and cues == {'의심'}:
                        put(field, value, at, quote)     # 칸 이름 자체가 'STEMI 의심'이다. '여부 모름' 등은 넣지 않는다
                        warn('ST 상승을 의심 소견으로 입력했습니다. 심전도를 확인해 주세요.')
                    elif uncertain:
                        warn(f'{LABELS.get(field, "호흡")} 표현이 불확실하여 입력하지 않았습니다. 확인해 주세요.')
                    else:
                        put(field, value, at, quote)
            if _LATERAL.search(part):
                complaints.append(dict(order=order + (0,), value='stroke', weak=False, explicit=False, quote=quote))
            letter = _AVPU_LETTER.search(part)
            if letter and not unsure(part, letter.start(), letter.end()):
                put('avpu', letter.group(1).upper(), order + (letter.start(),), quote)
            for rule in _KTAS:
                for m in rule.finditer(number_text):
                    if exact(m):
                        put('pre_ktas', int(m.group(1)), order + (m.start(),), quote)
            for m in _KTAS_WORD.finditer(number_text):
                if exact(m):
                    put('pre_ktas', _KTAS_WORDS[m.group(1)], order + (m.start(),), quote)
            standalone = _KTAS_STANDALONE.search(part)
            if standalone:
                put('pre_ktas', _KTAS_WORDS[standalone.group(1)], order + (standalone.start(),), quote)
            for m in _GCS.finditer(number_text):
                if exact(m):
                    put('gcs', int(m.group(1)), order + (m.start(),), quote)
            for m in _PAIN.finditer(number_text):
                if exact(m):
                    put('pain_score', int(next(g for g in m.groups() if g)), order + (m.start(),), quote)
            for m in _PREGNANCY.finditer(number_text):
                if not unsure(number_text, m.start(), m.end()):
                    ages.append(dict(order=order + (m.start(),), value='pregnant', rank=3, quote=quote))
                    if not rough(m.start(), m.end()):
                        put('pregnancy_weeks', int(m.group(1)), order + (m.start(),), quote)
            for m in _AGE.finditer(number_text):
                age = int(m.group(1))
                if exact(m):
                    ages.append(dict(order=order + (m.start(),), value='child' if age < 15 else 'adult', rank=2, quote=quote))
                elif age >= 20 or age <= 10:   # 대략값이어도 15세 기준에서 먼 나이는 연령 구분이 확실하다
                    ages.append(dict(order=order + (m.start(),), value='adult' if age >= 20 else 'child', rank=0.5,
                                     quote=quote))
            for m in _NEWBORN.finditer(number_text):
                if exact(m):
                    days = int(m.group(1)) * {'일': 1, '주': 7, '개월': 30, '달': 30}[m.group(2)]
                    ages.append(dict(order=order + (m.start(),), value='neonate' if days <= 28 else 'child', rank=2,
                                     quote=quote))
            for m in _DECADE.finditer(number_text):
                if m.group(1) != '1':          # '40대 추정'도 성인인 것은 확실하다(정확한 나이·연령대는 비운다)
                    ages.append(dict(order=order + (m.start(),), value='adult', rank=1 if exact(m) else 0.5, quote=quote))
            if _UNCLASSIFIED.search(number_text):
                put('pre_ktas', 'unclassified', order + (0,), quote)
    if seizure_mentioned and 'seizure_ongoing' not in values and _SEIZURE_STOPPED.search(text):
        put('seizure_ongoing', 'no', (99,), '경련 멈춤')   # '경련, 지금은 멈춤'처럼 쉼표 너머로 말한 경우

    fields, evidence, drop = {}, {}, []
    for field, found in values.items():
        distinct = list(dict.fromkeys(v for _, v, _ in found))
        if len(distinct) > 1:
            warn(f'{LABELS.get(field, "호흡")}에 서로 다른 값이 있어 자동 입력하지 않았습니다. 확인해 주세요.')
            if field == 'avpu':
                drop.append('consciousness')     # 서로 다른 의식 표현이면 voice_terms의 의식 상태도 비운다
            continue
        fields[field], evidence[field] = distinct[0], list(dict.fromkeys(q for _, _, q in found))
    _resolve_complaint(complaints, fields, evidence, ages, warn)
    _resolve_age(ages, base, fields, evidence)
    _resolve_injury(text, fields, context)
    _harmonize({k: v for k, v in base.items() if k not in drop}, fields, evidence, warn, drop)
    for key, value in list(fields.items()):
        if key in RANGES and not RANGES[key][0] <= value <= RANGES[key][1]:
            warn(f'{LABELS[key]} 값이 입력 범위를 벗어나 입력하지 않았습니다. 확인해 주세요.')
            fields.pop(key)
            evidence.pop(key, None)
    return dict(fields=fields, evidence=evidence, warnings=warnings, drop=sorted(set(drop) & set(base)))


def _resolve_complaint(complaints, fields, evidence, ages, warn):
    if not complaints:
        return
    explicit = [c for c in complaints if c['explicit']]
    strong = [c for c in complaints if not c['weak']]
    specific = [c for c in complaints if c['value'] != 'other']      # 약한 후보끼리면 '기타'보다 구체 묶음을 고른다
    pool = explicit or strong or specific or complaints
    chosen = min(pool, key=lambda c: c['order'])
    value = chosen['value']
    others = [VALUE_LABELS['chief_complaint'][v] for v in dict.fromkeys(c['value'] for c in strong or pool) if v != value]
    if others and not explicit:
        warn(f"주증상 후보가 여러 개입니다({', '.join(others)}). 먼저 말한 것을 골랐으니 확인해 주세요.")
    first = min(complaints, key=lambda c: c['order'])
    if first['value'] == 'other' and first['weak'] and value != 'other':
        warn('R1 묶음에 없는 증상(두통·어지럼·발열 등)이 먼저 언급됐습니다. 주증상을 확인해 주세요.')
    pregnant = any(a['value'] == 'pregnant' for a in ages)
    if pregnant and (value == 'bleeding' or (value == 'abdominal' and any(c['value'] == 'bleeding' for c in complaints))):
        value = 'obstetric'                      # R1 4절: 임신 중 출혈(복통 동반 포함)은 산과·분만
        warn('임신 중 출혈은 산과·분만으로 골랐습니다. 확인해 주세요.')
    elif pregnant and value == 'abdominal':
        warn('임신 중 복통입니다. 산과·분만으로 고를지 확인해 주세요.')
    fields['chief_complaint'], evidence['chief_complaint'] = value, [chosen['quote']]


def _resolve_age(ages, base, fields, evidence):
    candidates = list(ages)
    derived = _category_from(base)
    if derived is not None:
        candidates.append(dict(order=(-1,), value=derived, rank=2 if 'age' in base else 1, quote='나이·연령대'))
    if not candidates:
        return
    best = max(candidates, key=lambda a: (a['rank'], a['order']))
    fields['age_category'], evidence['age_category'] = best['value'], [best['quote']]


def _resolve_injury(text, fields, context):
    if fields.get('chief_complaint', context.get('chief_complaint')) != 'trauma':
        return
    compact = re.sub(r'\s+', '', text)
    regions = [name for name, words in INJURY_REGIONS.items() if any(w in compact for w in words)]
    if regions:
        fields['injury_region'] = regions[0] if len(regions) == 1 else 'multiple'


def _harmonize(base, fields, evidence, warn, drop):
    """voice_terms 칸과 맞춘다: 의식 상태 ↔ AVPU, 호흡은 voice_terms가 못 찾은 경우에만 채운다."""
    consciousness = base.get('consciousness')
    if 'avpu' in fields:
        mapped = AVPU_TO_CONSCIOUSNESS[fields['avpu']]
        if consciousness is not None and consciousness != mapped:
            warn('의식 상태와 AVPU 표현이 서로 달라 의식을 입력하지 않았습니다. 확인해 주세요.')
            fields.pop('avpu')
            evidence.pop('avpu', None)
            drop.append('consciousness')
        elif consciousness is None:
            fields['consciousness'] = mapped
            evidence['consciousness'] = evidence.get('avpu', [])
    elif 'consciousness' in drop:
        pass
    elif consciousness in CONSCIOUSNESS_TO_AVPU:
        fields['avpu'] = CONSCIOUSNESS_TO_AVPU[consciousness]
        evidence['avpu'] = ['의식 상태']
    elif consciousness == 'impaired':
        warn('의식 저하는 AVPU의 V(목소리 반응)·P(통증 반응) 중 어느 쪽인지 확인해 주세요.')
    if 'breathing' in fields and 'breathing' in base:
        fields.pop('breathing')                  # voice_terms의 호흡 판단을 우선한다
        evidence.pop('breathing', None)
