"""음성 초안 추출용 용어 사전(R1 최소 입력 항목 기준).

- 표현은 띄어쓰기를 지운 소문자 형태로 비교한다. 여러 표현이 겹치면 긴 표현을 먼저 쓴다.
- sets: 표현이 나오면 넣을 (칸, 값). 주증상은 세 번째 값으로 'weak'를 붙이면, 다른 주증상 표현이 있을 때 밀린다.
- negated: 표현 뒤(다음 표현 전까지)에 부정어가 오면 대신 넣을 (칸, 값) 목록. 없으면 부정된 표현은 무시한다.
- sets가 빈 항목은 '진통제'처럼 다른 표현의 오인을 막는 차단어다.
진단이나 등급을 추론하는 표현은 넣지 않는다. 말한 사실만 칸에 옮긴다.
"""

TERMS = [
    # ---- 차단어
    dict(sets=[], expr=['진통제', '경련약', '항경련제', '천식약', '혈압약', '분만실', '응급실']),

    # ---- 의식(AVPU)
    dict(sets=[('avpu', 'A')], expr=['의식 명료', '의식 있음', '의식 깨어 있음', '의식 또렷', '명료함', '얼러트', 'alert']),
    dict(sets=[('avpu', 'V')], expr=['목소리에 반응', '부르면 반응', '불러야 반응', '언어 반응', '언어에 반응', '버벌', 'verbal']),
    dict(sets=[('avpu', 'P')], expr=['통증에 반응', '통증에만 반응', '통증 자극에 반응', '자극에만 반응', '페인 반응']),
    dict(sets=[('avpu', 'U')], expr=['무반응', '반응 없음', '반응이 없', '의식 없음', '의식이 없', '의식 소실', '언리스판시브']),
    dict(sets=[('avpu', 'impaired'), ('chief_complaint', 'altered_seizure', 'weak')],
         expr=['의식 저하', '의식이 떨어', '의식 혼미', '기면'], negated=[]),
    dict(sets=[('avpu', 'impaired')], expr=['의식 이상'], negated=[('avpu', 'A')]),     # '의식 이상 없음' = 명료

    # ---- 호흡
    dict(sets=[('breathing', 'normal')], expr=['호흡 정상', '호흡 안정', '숨 잘 쉼', '숨 잘 쉬', '호흡 양호'], negated=[]),
    dict(sets=[('breathing', 'abnormal'), ('chief_complaint', 'dyspnea')],
         expr=['호흡곤란', '숨이 차', '숨차', '숨 쉬기 힘들', '숨이 가쁘', '숨 가쁨', '숨이 막'], negated=[]),
    dict(sets=[('breathing', 'abnormal')], expr=['호흡 이상'], negated=[('breathing', 'normal')]),   # '호흡 이상 없음'
    dict(sets=[('breathing', 'abnormal')],
         expr=['호흡 빠름', '호흡이 빠르', '빈호흡', '호흡 느림', '호흡이 느리', '쌕쌕', '천명', '그렁거림', '호흡 불규칙'],
         negated=[]),
    dict(sets=[('breathing', 'absent')],
         expr=['호흡 없음', '호흡이 없', '무호흡', '숨 안 쉼', '숨을 안 쉬', '숨 안 쉬', '호흡 정지', '숨을 쉬지 않']),

    # ---- 주증상
    dict(sets=[('chief_complaint', 'cardiac')],
         expr=['흉통', '가슴 통증', '가슴이 아프', '가슴 아픔', '가슴 답답', '가슴이 답답', '흉부 통증', '흉부 압박감',
               '두근거림', '가슴 두근', '심계항진', '실신', '기절', '심근경색', '협심증'], negated=[]),
    dict(sets=[('chief_complaint', 'cardiac'), ('avpu', 'U'), ('breathing', 'absent')],
         expr=['심정지', '심폐정지', '어레스트', '심폐소생술 중', 'cpr 중']),
    dict(sets=[('chief_complaint', 'stroke')],
         expr=['편마비', '한쪽 마비', '반신 마비', '한쪽 팔다리 마비', '한쪽 힘 빠짐', '한쪽에 힘이 없', '말이 어눌',
               '말 어눌', '발음 어눌', '발음이 어눌', '구음장애', '언어장애', '실어증', '안면마비', '얼굴 마비', '입이 돌아',
               '입꼬리가 처', '뇌졸중', '중풍', '스트로크', '뇌경색', '뇌출혈'], negated=[]),
    dict(sets=[('chief_complaint', 'altered_seizure')], expr=['경련', '발작', '간질', '뇌전증', '혼수'], negated=[]),
    dict(sets=[('chief_complaint', 'dyspnea')], expr=['천식', '천식 발작', 'copd'], negated=[]),
    dict(sets=[('chief_complaint', 'trauma')],
         expr=['교통사고', '차 사고', '자동차 사고', '오토바이 사고', '자전거 사고', '보행자 사고', '차에 치', '추락',
               '높은 곳에서 떨어', '낙상', '넘어졌', '넘어짐', '넘어져', '관통상', '자상', '찔렸', '찔림', '칼에', '열상',
               '골절', '부러', '삐었', '삠', '염좌', '외상', '다쳤', '다침', '부딪', '폭행', '화상', '끼임', '압궤'],
         negated=[]),
    dict(sets=[('chief_complaint', 'bleeding')],
         expr=['토혈', '피를 토', '하혈', '혈변', '흑변', '객혈', '각혈', '대량 출혈', '출혈', '피가 많이', '코피'],
         negated=[]),
    dict(sets=[('chief_complaint', 'abdominal')],
         expr=['복통', '배가 아프', '배 아픔', '배 통증', '명치 통증', '명치가 아프', '구토', '토함', '토했', '설사', '오심'],
         negated=[]),
    dict(sets=[('chief_complaint', 'obstetric', 'weak'), ('age_group', 'pregnant')],
         expr=['임신', '임산부', '산모', '임부'], negated=[]),
    dict(sets=[('chief_complaint', 'obstetric')], expr=['분만', '출산'], negated=[]),
    dict(sets=[('chief_complaint', 'toxic_env')],
         expr=['중독', '음독', '약물 과다', '약을 많이 먹', '농약', '일산화탄소', '연탄', '가스 흡입', '열사병', '일사병',
               '열탈진', '저체온', '동상', '벌에 쏘', '뱀에 물', '아나필락시스', '알레르기 반응'], negated=[]),
    dict(sets=[('chief_complaint', 'other')], expr=['정신과', '자해', '자살 시도', '환청'], negated=[]),

    # ---- 2차 고려사항
    dict(sets=[('stemi_suspected', 'yes'), ('chief_complaint', 'cardiac')],
         expr=['st 상승', 'st분절 상승', '에스티 상승', 'st elevation', 'stemi', '스테미'],
         negated=[('stemi_suspected', 'no')]),
    dict(sets=[('stemi_suspected', 'no')], expr=['st 변화 없', 'st 정상', '심전도 정상']),
    dict(sets=[('stemi_suspected', 'unmeasured')],
         expr=['심전도 안 찍', '심전도 못 찍', '심전도 미측정', '심전도 측정 안', '12유도 없', '심전도 없음']),
    dict(sets=[('bleeding_risk', 'yes')],
         expr=['항응고제', '와파린', '헤파린', '혈액 희석제', '피 묽게 하는 약', '출혈 경향', '엘리퀴스', '자렐토', '프라닥사',
               '릭시아나'], negated=[('bleeding_risk', 'no')]),
    dict(sets=[('high_risk_mechanism', 'yes')], expr=['고위험 기전', '고위험 손상 기전', '고에너지 손상', '고에너지 외상'],
         negated=[('high_risk_mechanism', 'no')]),
    dict(sets=[('labor', 'yes'), ('chief_complaint', 'obstetric')],
         expr=['진통', '양수가 터', '양수 터', '양막 파열', '파수', '분만 진행', '아기가 나오', '애기가 나오'],
         negated=[('labor', 'no')]),
    dict(sets=[('seizure_ongoing', 'yes'), ('chief_complaint', 'altered_seizure')],
         expr=['경련 중', '경련 지속', '계속 경련', '아직 경련', '경련하고 있', '경련 계속']),
    dict(sets=[('seizure_ongoing', 'no'), ('chief_complaint', 'altered_seizure')],
         expr=['경련 멈', '경련은 멈', '경련이 멈', '경련 끝', '경련 후', '경련 멎', '경련 중단', '경련 중지']),

    # ---- 연령대(숫자 나이는 extract.py에서 따로 읽는다)
    dict(sets=[('age_group', 'adult')],
         expr=['성인', '할머니', '할아버지', '어르신', '노인', '아저씨', '아주머니', '아줌마', '고등학생', '대학생', '직장인']),
    dict(sets=[('age_group', 'child')],
         expr=['소아', '어린이', '아동', '유아', '영아', '아기', '애기', '아이', '초등학생', '유치원생', '중학생', '남아', '여아']),
    dict(sets=[('age_group', 'neonate')], expr=['신생아', '갓난아기', '갓난아이', '갓 태어난']),
]

# 부정어: 표현 뒤에서 다음 표현 전까지 이 글자가 있으면 부정으로 본다(띄어쓰기를 지운 형태).
NEGATIONS = ('없', '아니', '아님', '음성', '부정', '않', '안함', '안됨', '안했')

# 외상일 때 손상 부위(띄어쓰기를 지운 형태)
INJURY_REGIONS = {
    'head': ('머리', '두부', '이마', '얼굴', '안면', '뒤통수'),
    'torso': ('흉부', '가슴', '복부', '배를', '배에', '골반', '허리', '갈비', '늑골', '등을', '옆구리'),
    'limb': ('팔을', '팔이', '팔에', '팔골절', '왼팔', '오른팔', '팔다리', '다리', '손목', '발목', '무릎', '어깨', '대퇴',
             '허벅지', '정강이', '손가락', '발가락', '사지'),
}

LABELS = dict(
    avpu={'A': '의식 명료', 'V': '목소리에 반응', 'P': '통증에 반응', 'U': '무반응',
          'impaired': '의식 저하(목소리·통증 반응 확인 필요)', 'unknown': '의식 모름'},
    breathing={'normal': '호흡 정상', 'abnormal': '호흡 이상', 'absent': '호흡 없음', 'unknown': '호흡 모름'},
    chief_complaint={'cardiac': '흉통·심장', 'stroke': '뇌졸중 의심', 'altered_seizure': '의식저하·경련',
                     'dyspnea': '호흡곤란', 'trauma': '외상', 'bleeding': '출혈', 'abdominal': '복통·소화기',
                     'obstetric': '산과·분만', 'toxic_env': '중독·환경', 'other': '기타'},
    age_group={'adult': '성인', 'child': '소아', 'neonate': '신생아', 'pregnant': '임신 중', 'unknown': '연령대 모름'},
    stemi_suspected={'yes': 'ST 상승 확인', 'no': 'ST 상승 없음', 'unmeasured': '심전도 미측정'},
    bleeding_risk={'yes': '출혈 경향 있음', 'no': '출혈 경향 없음'},
    high_risk_mechanism={'yes': '고위험 손상 기전', 'no': '고위험 기전 아님'},
    injury_region={'head': '머리 손상', 'torso': '몸통 손상', 'limb': '팔다리 손상', 'multiple': '여러 곳 손상'},
    labor={'yes': '진통·양막 파열 있음', 'no': '진통 없음'},
    seizure_ongoing={'yes': '경련 지속 중', 'no': '경련 멈춤'},
)
REQUIRED_LABELS = {'avpu': '의식', 'breathing': '호흡', 'chief_complaint': '주증상', 'age_group': '연령대',
                   'pre_ktas': 'Pre-KTAS 등급'}
VITAL_LABELS = {'hr': '맥박', 'rr': '호흡수', 'spo2': '산소포화도', 'nrs': '통증', 'gcs': 'GCS', 'glucose': '혈당'}
