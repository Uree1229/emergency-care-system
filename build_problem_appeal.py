"""이송체계 전국 확대의 남은 문제와 우리 시스템의 해결방안 문서 생성.

    python build_problem_appeal.py
    → docs/research/이송체계_전국확대_문제점과_해결방안.docx / .md

수치는 아래 SOURCES의 공개 보도·발표에서 옮겼다. 시뮬레이션 수치는 simulation_results/에서 읽는다.
'우리 해석'으로 표시한 문장은 공개자료에서 직접 확인한 사실이 아니다.
"""
import json
from pathlib import Path

from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

ROOT = Path(__file__).parent
OUT = ROOT / 'docs' / 'research' / '이송체계_전국확대_문제점과_해결방안'
TITLE = '응급환자 이송체계 전국 확대의 남은 문제와 우리 시스템의 해결방안'
DATE = '2026년 10월 9일'

SOURCES = {
    1: ('보건복지부·소방청, 이송체계 혁신 시범사업 3개월 성과 발표 (2026.6.22)',
        'https://www.korea.kr/news/policyNewsView.do?newsId=148966917'),
    2: ('한겨레, 광역상황실이 병원 결정…근본 처방엔 못미쳐 (2026.2.25)', 'https://m.news.nate.com/view/20260225n38349'),
    3: ('Korea JoongAng Daily, Half of hospitals ignore 119 Smart EMS alerts (2026.7.28)',
        'https://www.koreajoongangdaily.com/korea/half-of-hospitals-ignore-8-billion-won-ems-transfer-system-alerts/12794824'),
    4: ('뉴스핌, 의료정책연구원 응급이송체계 연구 구급대원 설문 (2025.4.17)',
        'https://www.newspim.com/news/view/20250417000285'),
    5: ('코메디닷컴, 응급실 수용곤란 고지 1년새 88% 증가 (국립중앙의료원 자료)', 'https://kormedi.com/2760116/'),
    6: ('아시아경제, 지역맞춤형 응급환자 이송체계 전국 확대 시행령 입법예고 (2026.7.16)',
        'https://view.asiae.co.kr/article/2026071611105285276'),
    7: ('메디게이트뉴스, 응급의료법 개정안 국회 본회의 통과 (2026.10.1)', 'https://www.medigatenews.com/news/3534488629'),
    8: ('데일리팜, 응급의학 전문의 93% 응급실 미수용 방지법 반대 (2026.9)', 'https://dailypharm.com/user/news/342943'),
    9: ('이투데이, 응급실 뺑뺑이 방지법 국회 통과…수용 능력 실시간 공개 (2025.10.26)',
        'https://www.etoday.co.kr/news/view/2518445'),
    10: ('서울신문 열린세상, 최소한 응급실에는 갈 수 있어야 (2026.10.7)',
         'https://www.seoul.co.kr/news/editOpinion/world-stories/2026/10/07/20261007027005'),
    11: ('서울신문, 광역상황실이 중환자 이송 병원 지정 (2026.2.26)',
         'https://www.seoul.co.kr/news/society/health-medical/2026/02/26/20260226002001'),
    12: ('프레시안, 대전소방 119구급 스마트 시스템 도입 (2025.3.21)',
         'https://www.pressian.com/pages/articles/2025032112133107941'),
    13: ('메디파나뉴스, 호남권 응급이송체계 시범사업 전공의 실태조사(젊은의사정책연구원) (2026.5.14)',
         'https://www.medipana.com/news/articleView.html?idxno=411346'),
    14: ('라포르시안, 119 구급대 재이송 사유 1위는 전문의 부재 (소방청 자료, 2024.7.9)',
         'https://www.rapportian.com/news/articleView.html?idxno=209006'),
    15: ('의협신문, 진료불가 가득한 응급실 상황판 (2024.9.4)', 'https://www.doctorsnews.co.kr/news/articleView.html?idxno=156248'),
    16: ('의협신문, 응급환자 현장 체류 2년 새 증가 (서영석 의원실 분석, 2026.4.17)',
         'https://www.doctorsnews.co.kr/news/articleView.html?idxno=164202'),
}

# 기존 체계의 단계별 작동 방식 → 그 방식에서 생기는 문제 → 우리 방식
STAGES = [
    dict(stage='① 병원 정보',
         old='병원이 신청한 병상 수와 진료불가 메시지를 종합상황판에 표시한다. 병원이 직접 입력해야 반영되는 신청 기반이다. [15]',
         issue='병원이 불가 상황을 입력하지 않으면 그대로 "가능"으로 보인다. 현장 전문가는 메시지가 실제의 1/3~1/2만 반영한다고 본다. '
               '구급대원 67.3%가 불편, 50.6%가 진료 세부정보 부족을 꼽았다. [4][15]',
         ours='병원 정보는 문의할 후보를 거르는 데만 쓰고, 확정은 그 환자에 대한 병원의 수용 응답으로 한다. 정보마다 갱신 시각과 "오래된 정보" 표시를 붙인다.',
         ids='H11, S08, A03'),
    dict(stage='② 수용 문의',
         old='구급대가 병원에 한 곳씩 전화한다(예닐곱 곳이 흔함). 119구급스마트시스템은 환자 정보를 여러 병원에 동시 전송하고 병원이 수용 가능 여부를 회신한다. [3][12]',
         issue='전화는 앞 병원의 답을 기다리는 동안 다음 문의가 멈춘다. 동시 전송은 응답률이 48%에 그치고, 무응답이면 결국 다시 전화한다. '
               '시범사업에서 구급대 문의는 줄었지만 상황실 문의는 6.1곳 그대로다. [1][3]',
         ours='진료 조건·제한·이동시간으로 적합한 병원만 골라 3곳에 동시 문의한다. 응답이 없으면 60초에 강조, 120초에 재문의하고, 응답이 오는 즉시 재평가한다.',
         ids='A03, P21, P24, H26'),
    dict(stage='③ 병원의 수용 판단',
         old='응급실 의사가 진료 중에 문의를 받고, 수술·입원을 맡을 배후진료과와 중환자실을 확인한 뒤 답한다. [14]',
         issue='배후과 확인 전에는 즉답할 수 없는데, 회신은 수용 여부 위주라 "확인 중"을 전할 방법이 마땅치 않다. '
               '재이송 사유 1위가 전문의 부재(41.9%)로, 확인 없이 받은 뒤 다시 옮기는 일이 생긴다. 무응답 이유는 인력 부족 29%, 다른 업무 25%다. [3][14]',
         ours='"확인 중"과 "추가 질문"을 먼저 보내고 배후과 확인 뒤 수용/불가를 회신한다. 불가는 사유를 골라 한 번에 보내고, 진료 분야 단위의 제한을 미리 등록한다.',
         ids='H19, H20, H22, H07'),
    dict(stage='④ 이송병원 결정',
         old='중증은 광역상황실이, 그 외는 구급상황센터·구급대가 사람의 판단으로 정한다. 지침과 자원 정보는 시·도 단위이고, 권역 밖은 초광역 의뢰라는 별도 단계를 거친다. [6][11]',
         issue='야간 상황실 3~4명이 약 4분에 1건을 처리한다. 전공의 78%는 상황실 판단이 수술실·중환자실·배후진료를 반영하지 못한다고 답했다. '
               '대구의 관외 이송은 2년 새 144건에서 494건으로 늘었다. [2][13][16]',
         ours='후보 계산과 동시 문의, 응답 집계를 시스템이 하고, 담당자는 결과 확인과 예외 처리에 집중한다. 후보는 관할이 아니라 이동시간과 진료 역량으로 찾는다.',
         ids='P17~P20, A05, S07'),
    dict(stage='⑤ 이송 중',
         old='병원을 정하면 출발한다. 시범사업에서는 119의 사전 고지 의무가 폐지되었다는 현장 지적이 있다. [13]',
         issue='전공의 55%는 사전 고지 없이 온 환자가 실제로 응급수술·중환자실이 필요했던 경험이, 68%는 기존 환자 처치가 지연된 경험이 있다고 답했다. '
               '이송 중 병원 상황이 바뀌었을 때의 재확인 절차는 공개자료에서 확인되지 않는다. [1][13]',
         ours='확정과 동시에 병원 화면에 도착 예정 환자, 예상 도착 시각, 필요 자원이 뜨고 자원 계산에 반영된다. '
              '다른 환자가 먼저 들어오면 영향받는 환자를 표시하고, 병원의 유지/조정 판단을 이송자에게 알린다.',
         ids='H29~H31, H33~H36, P37'),
    dict(stage='⑥ 기록과 평가',
         old='상황판 표시, 전화 통화, 시스템 회신이 따로 남는다. 지역마다 집계 방식이 다르다. [3][16]',
         issue='소방청은 중증도별 시스템 사용 자료를 수집·분석하지 않아 효과를 평가할 수 없다. 수용기관 지정에 응급의학 전문의 93%가 반대하는데, '
               '지정과 거부의 정당성을 판단할 공통 기록이 부족하다. [3][8]',
         ours='문의, 응답, 불가 사유, 자원 변경, 확정을 시각·처리자·중증도와 함께 한 곳에 기록한다. 중증도별 응답률과 응답시간을 바로 계산할 수 있다.',
         ids='S15, S05'),
]
STAGE_NOTE = ('출처가 붙은 문장은 공개 보도·발표에서 확인한 사실이다. "확인 중을 전할 방법이 마땅치 않다", "공통 기록이 부족하다"는 '
              '공개자료로 직접 확인하지 못한 우리 해석이며, 실제 단말기·상황실 업무를 확인해 보완해야 한다.')
FLOW_IMAGE = ROOT / 'assets' / '기존체계_우리시스템_단계비교.png'

SUMMARY = [
    '호남권 시범사업(2026년 3~5월, 광주·전북·전남)은 구급대 문의 병원 수, 상황실 처리시간, 응급실 미수용에서 성과를 냈고, '
    '2026년 9월부터 지역별 이송지침으로 전국에 확대되었다. [1][6]',
    '그러나 공개된 수치를 보면 병원의 환자별 수용 확인 단계는 여전히 병목이다. 광역상황실의 문의 병원 수는 6.5곳에서 6.1곳으로 '
    '거의 줄지 않았고, 119구급스마트시스템의 병원 응답률은 48% 수준이다. [1][3]',
    '우리 시스템은 이 "수용 확인"과 "확정 이후 상황 변화 대응"을 다룬다. 의료 인력·배후진료 부족 자체는 해결 대상이 아니며, '
    '이를 명확히 구분해 제시한다.',
]

CURRENT = [
    ['중증 (pre-KTAS 1~2)', '중앙·광역응급의료상황실 또는 구급상황센터', '병원 수용 가능 여부 확인 후 선정. 지연 시 우선수용병원으로 이송, 이후 전원은 119 담당'],
    ['중등증 (3)', '구급상황센터', '악화 가능성이 있어 수용 능력을 미리 확인'],
    ['경증 (4~5)', '119구급대 현장 판단', '별도 수용 문의 없이 지침·자원 현황에 따라 이송'],
    ['심정지·중증외상', '사전 지침', '지정 병원으로 즉시 이송'],
]
LAWS = [
    '2025.10 응급의료법 개정: 중앙응급의료센터가 응급의료기관의 수용 인원·역량을 실시간 공개하고, 병원은 구급대 전용 핫라인을 운영한다. [9]',
    '2026.7 시행령 개정안 입법예고: 시·도지사가 중증도·질환별 이송병원을 지정하고, 수용 거부의 정당한 사유를 구체화하며 상황실에 즉시 고지하도록 했다. [6]',
    '2026.10.1 응급의료법 개정안 국회 통과: 이송병원이 신속히 정해지지 않으면 상황실·구급상황센터가 수용기관을 지정하고, '
    '지정된 기관은 정당한 사유 없이는 수용해야 한다. 지정에 따라 수용한 의료진의 형사처벌은 면제한다. [7]',
]
PILOT = [
    ['구급대 문의 병원 수', '평균 5.8곳', '평균 3.8곳'],
    ['광역상황실 문의 병원 수', '평균 6.5곳', '평균 6.1곳'],
    ['광역상황실 처리시간(중위값)', '27분', '18분'],
    ['광역상황실 이송병원 선정 지원', '월평균 5건', '월평균 41건'],
    ['전북 병원 선정 시간 (119구급스마트시스템)', '11분 55초', '8분 40초 (−27.3%)'],
    ['중증환자 현장체류시간', '-', '광주 −1분 24초, 전북 −24초, 전남 +18초'],
    ['응급실 미수용', '-', '0건'],
]

PROBLEMS = [
    dict(
        id='문제 1', title='병원의 환자별 수용 확인이 여전히 병목이다',
        evidence=[
            '광역상황실 문의 병원 수는 시범사업 중에도 평균 6.1곳으로, 이전(6.5곳)과 거의 같다. 처리시간 중위값은 18분이다. [1]',
            '광역상황실은 20~25명이 3교대로 근무하며 야간에는 3~4명이 상주한다. 야간에는 약 4분에 1건꼴로 이송 의뢰가 들어오고, '
            '1단계 환자 처리에 평균 33분이 걸렸다(시범사업 직전 보도). [2]',
        ],
        why='상황실이 병원 선정을 맡으면서 구급대의 전화는 줄었지만(5.8→3.8곳), 병원에 하나씩 확인하는 일은 상황실로 옮겨 갔다. '
            '전국 확대로 상황실이 처리할 건수는 늘어난다(우리 해석).',
    ),
    dict(
        id='문제 2', title='여러 병원에 동시에 보내는 요청에 병원이 절반만 응답한다',
        evidence=[
            '119구급스마트시스템의 병원 응답률은 2024년 49.43%, 2025년 47.96%(56만 3,521건 중 27만 244건)였다. '
            '2026년 1~3월 구급 출동 중 시스템 사용 비율은 21.8%에 그쳤다. [3]',
            '응답하지 않는 이유로 병원 응답자 470명 중 29%가 인력 부족, 25%가 다른 업무 우선을 꼽았다. [3]',
            '구급대원은 현장에서 환자 정보를 단말기에 입력해 여러 병원에 동시에 보내고, 병원은 수용 가능 여부를 회신한다. [3][12] '
            '한 보도는 이 회신이 수용 가능 여부를 묻는 데 그쳐, 환자 상태·중증도를 더 자세히 제공해야 병원이 빨리 답할 것이라는 전문가 의견을 전했다. '
            '한 경기 구급대원은 아는 병원에 직접 전화하는 편이 더 빠를 때가 많다고 했다. [3]',
            '소방청은 중증도별 시스템 사용 자료를 수집·분석하지 않아, 중증환자에 대한 효과를 평가할 수 없다(목원대 채진 교수). [3]',
        ],
        why='환자 정보 전송과 동시 요청은 이미 있다. 남은 문제는 회신이 수용 가능 여부 위주여서 "확인 중", "추가 정보 필요" 같은 중간 상태를 '
            '주고받기 어렵고, 응답이 없을 때 다음 조치가 구급대원 개인의 전화에 맡겨진다는 점이다(우리 해석).',
    ),
    dict(
        id='문제 3', title='표시된 가용 정보와 실제 수용 능력이 다르다',
        evidence=[
            '구급대원 759명 중 67.3%가 종합상황판 사용이 불편하다고 답했고, 50.6%는 진료 세부정보 부족으로 이송 가능 여부를 판단하기 어렵다고 했다. '
            '응급의학과 전문의들은 최종 치료 능력이 현장과 일치하지 않아 배후진료가 단절된다고 지적했다. [4]',
            '응급실 수용곤란 고지는 2023년 5만 8,520건에서 2024년 11만 33건으로 88% 늘었고, 사유의 48.1%가 인력 부족이었다. [5]',
            '종합상황판의 진료불가 메시지는 병원이 신청해야 반영된다. 대한응급의학과의사회장은 메시지가 실제의 3분의 1 수준이라고 했다. [15]',
            '2023년 119 재이송 4,227건 중 1,771건(41.9%)이 전문의 부재 때문이었다. [14]',
        ],
        why='빈 병상 수는 특정 시점의 값이고, 특정 환자를 받을 수 있는지와는 다르다. 실시간 공개 의무화(2025.10)도 '
            '이 차이를 없애지는 못한다(우리 해석).',
    ),
    dict(
        id='문제 4', title='병원을 정한 뒤 생기는 변화에 대응하는 절차와 지표가 보이지 않는다',
        evidence=[
            '공개된 성과 지표는 병원 선정까지의 시간과 문의 수, 미수용 건수 중심이다. 이송 중 다른 환자 유입이나 자원 변화로 '
            '도착 시점에 수용이 어려워지는 경우의 재확인 절차와 결과는 공개자료에서 확인되지 않았다. [1]',
            '선정이 지연되면 우선수용병원으로 먼저 이송하고 이후 전원은 119가 맡는 구조여서, 재이송이 제도 안에 포함되어 있다. [11]',
            '호남 시범사업 지역 전공의 조사에서 55%는 사전 고지 없이 이송된 환자가 실제로 응급수술·중환자실이 필요했던 경험이, '
            '68%는 그 때문에 기존 환자 처치가 지연된 경험이 있다고 답했다. [13]',
        ],
        why='병원 선정은 출발점이다. 도착까지의 10~30분 사이에 병원 상황이 바뀌면 환자는 도착 후에야 이를 알게 될 수 있다(우리 해석).',
    ),
    dict(
        id='문제 5', title='지역 간 격차와 관할 경계',
        evidence=[
            '같은 시범사업에서 전남의 중증환자 현장체류시간은 오히려 18초 늘었다. 전남은 광주 소재 의료기관과의 협력에 의존했다. [1]',
            '2026년 2월 대구의 임신 28주 산모는 관내 병원들이 수용하지 못해 약 4시간 뒤 경기도 병원에 도착했고, '
            '5월 청주의 임신 29주 산모는 10곳에 문의한 끝에 약 3시간 반 만에 부산으로 이송되었다. [3][10]',
            '대구의 관외 이송은 2023년 144건에서 2025년 494건으로 3.4배 늘었다. [16]',
        ],
        why='법은 치료에 적합하고 가장 빨리 도착할 병원을 원칙으로 하지만, 이송지침과 상황실의 자원 정보는 시·도 단위이고 '
            '다른 권역은 초광역 의뢰라는 별도 단계를 거친다. 가까운 병원이 경계 밖에 있으면 이 단계 전환만큼 지연될 수 있다(우리 해석).',
    ),
    dict(
        id='문제 6', title='수용기관 지정과 의무 수용을 둘러싼 갈등',
        evidence=[
            '2026년 10월 1일 통과한 개정안에 대해 대한응급의학의사회 설문 응답 전문의 299명 중 93%가 반대했고, '
            '수용기관 지정·의무 수용 조항에는 92%가 반대했다. [7][8]',
            '의료계는 정당한 수용 곤란 사유를 입증하기 어렵고, 일방적 수용 지정이 기존 환자와 이송 환자를 모두 위험하게 할 수 있다고 우려한다. [10]',
        ],
        why='지정이 정당하려면 지정 시점의 병원 상태와 거부 사유가 객관적으로 남아 있어야 한다. 지금은 이를 전화와 개별 판단에 의존한다(우리 해석).',
    ),
    dict(
        id='문제 7', title='119를 거치지 않고 직접 이동하는 환자는 지원 대상 밖에 있다',
        evidence=[
            '현재 이송체계와 119구급스마트시스템은 구급대 이송을 전제로 설계되었다. [1][3]',
            '대구 산모 사례에서 가족은 결국 자가 차량으로 이동했다. [10]',
        ],
        why='보호자가 직접 이동할 때는 어느 병원이 받을 수 있는지 확인할 공식 경로가 없다(우리 해석).',
    ),
]

SOLUTIONS = [
    ['문제 1 확인 병목', '진료 조건·제한·이동시간으로 후보를 자동으로 거르고, 상위 3곳에 환자 정보를 동시에 보냄. 응답이 오는 즉시 재평가해 수용한 병원 한 곳을 확정',
     'A03, P17~P21, S07', '서버 완료', '목적지 확정까지 시간, 문의 병원 수'],
    ['문제 2 낮은 응답률', '수용·불가뿐 아니라 확인 중·추가 질문과 답변을 주고받는 응답 단계, 사유 선택형 불가, 자기신고·전문가 평가 출처 표시, 60초 미응답 강조와 120초 재문의, 적합한 병원에만 요청',
     'P12, H15~H26', '서버·병원 웹', '응답률, 응답시간 중앙값·95백분위, 클릭 수'],
    ['문제 3 정보 불일치', '빈 병상이 아니라 그 환자에 대한 수용 응답을 확정 조건으로 사용. 응답 유효시간(10분)·만료, 정보 갱신시각과 오래된 정보 표시, 진료 분야 조건',
     'S08, H11, A02', '서버 완료', '만료·미확인 응답으로 확정된 건수(0이어야 함)'],
    ['문제 4 확정 후 변화', '확정된 도착 예정 수요 관리. 미등록 환자 유입 등록 → 영향 환자 표시 → 목적지는 유지한 채 예상 치료 시작시각 재평가 → 병원의 유지/조정 판단 → 이송자 알림. 새 수용 확인 후에만 목적지 변경',
     'H33~H36, P37~P38, A26', '서버 완료, A26 1차 구현', '변화 관측→알림 시간, 목적지 변경·추가 이동'],
    ['문제 5 관할 경계', '관할이 아니라 이동시간과 진료 역량으로 후보 탐색. 의료공백지역·소아 시나리오 검증',
     'A05, T06', '가상 데이터', '공백지역 환자의 확정·치료 시작 시간'],
    ['문제 6 지정 갈등', '모든 문의·응답·불가 사유·자원 변경을 시각·처리자와 함께 기록. 마지막 자원에 두 환자가 동시에 확정되지 않도록 원자적 확정',
     'S05, S15', '서버 완료', '중복 확정 0건, 사유별 불가 통계'],
    ['문제 7 직접 이동 환자', '환자·보호자용 모바일 웹. 위험 신호가 있으면 즉시 119 안내, 그 외에는 병원 탐색·수용 문의',
     'P02, P10, P21', '서버 완료, 화면 진행 중', '입력 누락·이해도(사용성 평가)'],
]

LIMITS = [
    '전문의·배후진료·중환자실의 물리적 부족은 해결하지 못한다. 시스템은 있는 자원을 찾고 확인하고 배정하는 과정을 줄일 뿐이다.',
    '병원의 응답을 강제할 수 없다. 응답 부담을 줄이는 설계의 효과는 병원 담당자 사용성 실험으로 확인해야 한다.',
    '모든 결과는 가상 데이터의 시뮬레이션이다. 실제 병원 데이터와 운영 로그로 검증하지 않았다.',
    '시스템의 기록은 법적 판단을 대신하지 않는다. 지정·거부의 정당성 판단에 쓸 수 있는 객관적 자료를 남기는 역할이다.',
    '우선순위와 허용 대기시간은 실험 설정이며 임상 기준이 아니다.',
]

PITCH = [
    '"정부 체계는 누가 병원을 고를지를 정했습니다. 우리는 고른 병원이 그 환자를 정말 받을 수 있는지 확인하는 과정을 다룹니다."',
    '"119구급스마트시스템 요청의 절반은 응답을 받지 못합니다. 우리는 적합한 병원에만 요청하고, 수용·불가뿐 아니라 확인 중·추가 질문까지 '
    '주고받게 하며, 응답이 없으면 바로 강조하고 다시 묻습니다."',
    '"병원 선정 이후 도착까지의 변화는 지금 공개 지표에 없습니다. 우리는 이송 중 다른 환자가 들어와도 영향받는 환자를 바로 알립니다."',
    '"지정과 거부가 다툼이 되지 않도록, 모든 문의와 응답, 불가 사유를 시각과 함께 남깁니다."',
]


def draw_flow():
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyBboxPatch
    plt.rcParams['font.family'] = 'Malgun Gothic'
    old = ['병원 정보\n신청 기반 상황판', '수용 문의\n전화 순차 또는\n동시 전송(응답 48%)', '수용 판단\n배후과 확인 후\n수용 여부 회신',
           '이송병원 결정\n상황실·구급대가\n시·도 단위로 판단', '이송 중\n변화 공유 절차\n확인 안 됨', '기록\n상황판·전화·회신\n따로 남음']
    new = ['병원 정보\n후보 필터에만 사용\n갱신 시각 표시', '수용 문의\n적합 3곳 동시\n60초 강조·120초 재문의', '수용 판단\n확인 중·추가 질문\n→ 수용/사유 선택',
           '이송병원 결정\n시스템이 후보·응답 집계\n이동시간 기준 탐색', '이송 중\n도착 예정 표시\n유입 시 영향 환자 알림', '기록\n모든 단계 한 곳에\n중증도별 집계']
    pain = ['실제의 1/3~1/2만 반영', '상황실 문의 6.1곳', '재이송 1위 전문의 부재', '야간 3~4명·4분당 1건', '무고지 이송 경험 55%', '중증도별 평가 불가']
    fig, ax = plt.subplots(figsize=(13, 5.2), dpi=150)
    ax.set_xlim(0, 13)
    ax.set_ylim(0, 5.2)
    ax.axis('off')
    w, h, gap = 1.9, 1.3, 0.22
    for i in range(6):
        x = 0.35 + i * (w + gap)
        for y, texts, face, edge in ((3.35, old, '#F4F4F4', '#9A9A9A'), (0.55, new, '#E8F0FB', '#2F6DB5')):
            ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle='round,pad=0.02,rounding_size=0.08', fc=face, ec=edge, lw=1.2))
            ax.text(x + w / 2, y + h / 2, texts[i], ha='center', va='center', fontsize=8.6, linespacing=1.35)
            if i < 5:
                ax.annotate('', xy=(x + w + gap - 0.02, y + h / 2), xytext=(x + w + 0.02, y + h / 2),
                            arrowprops=dict(arrowstyle='->', color=edge, lw=1.1))
        ax.text(x + w / 2, 3.12, pain[i], ha='center', va='center', fontsize=8, color='#B03A2E')
        ax.annotate('', xy=(x + w / 2, 2.38), xytext=(x + w / 2, 2.95),
                    arrowprops=dict(arrowstyle='->', color='#2F6DB5', lw=1, ls='--'))
    ax.text(0.35, 4.85, '기존 체계', fontsize=11, fontweight='bold', color='#555555')
    ax.text(0.35, 2.0, '우리 시스템', fontsize=11, fontweight='bold', color='#2F6DB5')
    fig.savefig(FLOW_IMAGE, bbox_inches='tight', facecolor='white')
    plt.close(fig)


def sim_rows():
    base = json.loads((ROOT / 'simulation_results' / 'summary.json').read_text(encoding='utf-8'))['results']
    real = json.loads((ROOT / 'simulation_results' / 'realistic_nonresponse.json').read_text(encoding='utf-8'))['results']
    conds = [('미응답 10%', base['미응답 10%']), ('미응답 20% (기본)', base['기본 설정']), ('미응답 40%', base['미응답 40%'])]
    conds += [(k.split(' (')[0] + ' (실제 응답률 수준)', v) for k, v in real.items() if '+' not in k]
    rows = []
    for name, r in conds:
        rows.append([name, f"{r['time_to_commit']['b0_mean']:.1f} → {r['time_to_commit']['b1_mean']:.1f}",
                     f"{r['mean_wait']['diff_mean']:+.1f}", f"{r['p90_wait']['diff_mean']:+.1f}",
                     f"{r['mean_wait']['b1_better_share']:.0%}",
                     f"{r['inquiries']['b0_mean']:.0f} → {r['inquiries']['b1_mean']:.0f}"])
    return rows


SIM_HEADER = ['조건', '확정까지(분) B0 → B1', '평균 대기 차이', '대기 90백분위 차이', 'B1이 나은 seed', '병원 문의 건수 B0 → B1']
SIM_NOTES = [
    '같은 가상 사건(30 seed)에서 B0(순차 문의)와 B1(상위 3곳 동시 문의·자동 재문의·변경 알림)을 비교했다. 차이는 B1 − B0이며 음수가 좋다.',
    '응답률이 낮을수록 동시 문의의 이득이 커졌다. 실제 119구급스마트시스템 응답률(약 48%)에 맞춘 조건에서는 목적지 확정까지 약 10분, '
    '평균 대기 약 9분, 대기 90백분위 약 19분이 줄었다.',
    '대가는 병원 문의 건수 증가다. 이는 문제 2의 응답 부담을 키울 수 있으므로, 우리 설계는 적합한 병원 3곳으로 요청을 제한한다. '
    '환자 정보 전송과 병렬 문의 자체는 기존 시스템에도 있으므로, 우리의 차별점은 3·5장의 응답 단계 관리·수용 응답 관리·확정 후 변화 대응이다.',
    '응답 지연 분포는 두 방식이 같다고 가정했다. 미응답률이 낮을 때(10%)는 먼저 수용한 먼 병원을 고르는 탓에 B1이 약간 불리했으며, '
    '이 부분은 솔버 배정(B2)으로 개선을 검증할 예정이다.',
]
SERVER_EVIDENCE = [
    '마지막 자리 하나에 두 환자가 동시에 확정을 시도하면 한 명만 성공한다 (test_two_patients_last_bed_only_one_confirms).',
    '만료된 수용 응답, 환자 정보가 바뀐 뒤의 응답으로는 확정할 수 없다 (test_expired_acceptance_cannot_confirm).',
    '이동 중 환자 A가 있는 병원에 미등록 환자 B가 들어오면 A가 영향 환자로 표시되고, 목적지는 자동으로 바뀌지 않으며, '
    '병원의 조정 요청과 새 병원의 수용 확인 뒤에만 변경된다 (SuddenArrival 테스트).',
]


# ---------------------------------------------------------------- 출력

def md():
    L = [f'# {TITLE}', '', f'{DATE} · 캡스톤 디자인 검토 자료', '', '## 핵심 요약', '']
    L += [f'- {s}' for s in SUMMARY] + ['', '## 1. 현재 체계', '', '| 환자 | 이송병원 결정 | 방식 |', '|---|---|---|']
    L += [f'| {" | ".join(r)} |' for r in CURRENT] + ['']
    L += [f'- {s}' for s in LAWS] + ['', '## 2. 호남권 시범사업 성과 [1]', '', '| 지표 | 이전 | 시범사업 기간 |', '|---|---|---|']
    L += [f'| {" | ".join(r)} |' for r in PILOT]
    L += ['', '성과는 정부 발표 수치다. 사망자 비교는 지난해 연평균과 올해 5월을 비교한 것이며, 전후 비교만으로 특정 기능의 효과를 분리할 수는 없다.',
          '', '## 3. 기존 체계는 어떻게 작동하고, 그래서 무엇이 문제인가', '',
          f'![기존 체계와 우리 시스템 단계 비교](../../assets/{FLOW_IMAGE.name})', '',
          '| 단계 | 기존 방식 | 그래서 생기는 문제 | 우리 방식 | 기능 ID |', '|---|---|---|---|---|']
    L += [f"| {g['stage']} | {g['old']} | {g['issue']} | {g['ours']} | {g['ids']} |" for g in STAGES]
    L += ['', STAGE_NOTE, '', '## 4. 전국 확대 이후에도 남는 문제 (상세)', '']
    for p in PROBLEMS:
        L += [f"### {p['id']}. {p['title']}", ''] + [f'- {e}' for e in p['evidence']] + ['', f"**왜 문제인가:** {p['why']}", '']
    L += ['## 5. 우리 시스템의 해결방안', '', '| 문제 | 우리 기능 | 기능 ID | 상태 | 검증 지표 |', '|---|---|---|---|---|']
    L += [f'| {" | ".join(r)} |' for r in SOLUTIONS]
    L += ['', '## 6. 근거', '', '### 6.1 가상 시뮬레이션', '', '| ' + ' | '.join(SIM_HEADER) + ' |', '|' + '---|' * len(SIM_HEADER)]
    L += [f'| {" | ".join(r)} |' for r in sim_rows()] + [''] + [f'- {s}' for s in SIM_NOTES]
    L += ['', '### 6.2 서버 자동 테스트', ''] + [f'- {s}' for s in SERVER_EVIDENCE]
    L += ['', '## 7. 우리가 해결하지 못하는 것', ''] + [f'- {s}' for s in LIMITS]
    L += ['', '## 8. 발표용 문장', ''] + [f'- {s}' for s in PITCH]
    L += ['', '## 출처', ''] + [f'[{k}] [{t}]({u})  ' for k, (t, u) in SOURCES.items()]
    return '\n'.join(L) + '\n'


def setup():
    d = Document()
    s = d.sections[0]
    s.top_margin = s.bottom_margin = Cm(2.0)
    s.left_margin = s.right_margin = Cm(2.0)
    for name in ['Normal', 'Title', 'Heading 1', 'Heading 2']:
        st = d.styles[name]
        st.font.name = '맑은 고딕'
        st.font.color.rgb = RGBColor(0, 0, 0)
        st.element.get_or_add_rPr().rFonts.set(qn('w:eastAsia'), '맑은 고딕')
        st.paragraph_format.space_after = Pt(6)
    d.styles['Normal'].font.size = Pt(10.5)
    d.styles['Title'].font.size = Pt(20)
    d.styles['Heading 1'].font.size = Pt(14)
    d.styles['Heading 2'].font.size = Pt(11.5)
    for name in ('Heading 1', 'Heading 2'):
        d.styles[name].paragraph_format.keep_with_next = True
    for st in d.styles:
        for b in list(st.element.iter(qn('w:pBdr'))):
            b.getparent().remove(b)
    d.core_properties.title = TITLE
    d.core_properties.author = ''
    return d


def table(d, header, rows, widths):
    t = d.add_table(rows=1, cols=len(header))
    t.autofit = False
    for c, txt in zip(t.rows[0].cells, header):
        c.text = txt
    for row in rows:
        for c, txt in zip(t.add_row().cells, row):
            c.text = str(txt)
    borders = OxmlElement('w:tblBorders')
    for side in ['top', 'left', 'bottom', 'right', 'insideH', 'insideV']:
        el = OxmlElement('w:' + side)
        el.set(qn('w:val'), 'single')
        el.set(qn('w:sz'), '4')
        el.set(qn('w:color'), 'BFBFBF')
        borders.append(el)
    t._tbl.tblPr.append(borders)
    for i, row in enumerate(t.rows):
        tr = row._tr.get_or_add_trPr()
        tr.append(OxmlElement('w:cantSplit'))
        if i == 0:
            tr.append(OxmlElement('w:tblHeader'))
        for c, w in zip(row.cells, widths):
            c.width = Cm(w)
            c.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            if i == 0:
                sh = OxmlElement('w:shd')
                sh.set(qn('w:fill'), 'E7EDF3')
                c._tc.get_or_add_tcPr().append(sh)
            for p in c.paragraphs:
                p.paragraph_format.space_after = Pt(0)
                p.paragraph_format.keep_with_next = i == 0
                for r in p.runs:
                    r.font.size = Pt(9)
                    r.bold = i == 0
    d.add_paragraph().paragraph_format.space_after = Pt(2)


def bullets(d, items):
    for it in items:
        d.add_paragraph(it, style='List Bullet')


def docx():
    d = setup()
    d.add_paragraph(TITLE, 'Title')
    d.add_paragraph(f'{DATE} · 캡스톤 디자인 검토 자료')
    d.add_heading('핵심 요약', 1)
    bullets(d, SUMMARY)
    d.add_heading('1. 현재 체계', 1)
    table(d, ['환자', '이송병원 결정', '방식'], CURRENT, [3.4, 5.0, 8.6])
    bullets(d, LAWS)
    d.add_heading('2. 호남권 시범사업 성과', 1)
    table(d, ['지표', '이전', '시범사업 기간'], PILOT, [7.0, 3.4, 6.6])
    d.add_paragraph('성과는 정부 발표 수치다 [1]. 사망자 비교는 지난해 연평균과 올해 5월을 비교한 것이며, '
                    '전후 비교만으로 특정 기능의 효과를 분리할 수는 없다.')
    d.add_heading('3. 기존 체계는 어떻게 작동하고, 그래서 무엇이 문제인가', 1)
    d.add_picture(str(FLOW_IMAGE), width=Cm(17))
    table(d, ['단계', '기존 방식', '그래서 생기는 문제', '우리 방식'],
          [[g['stage'], g['old'], g['issue'], f"{g['ours']} ({g['ids']})"] for g in STAGES], [2.0, 4.6, 5.4, 5.0])
    note = d.add_paragraph(STAGE_NOTE)
    for r in note.runs:
        r.font.size = Pt(9)
    d.add_heading('4. 전국 확대 이후에도 남는 문제 (상세)', 1)
    for p in PROBLEMS:
        d.add_heading(f"{p['id']}. {p['title']}", 2)
        bullets(d, p['evidence'])
        para = d.add_paragraph()
        para.add_run('왜 문제인가: ').bold = True
        para.add_run(p['why'])
    d.add_heading('5. 우리 시스템의 해결방안', 1)
    table(d, ['문제', '우리 기능', '기능 ID', '상태', '검증 지표'], SOLUTIONS, [2.6, 6.8, 2.4, 2.4, 2.8])
    d.add_heading('6. 근거', 1)
    d.add_heading('6.1 가상 시뮬레이션', 2)
    table(d, SIM_HEADER, sim_rows(), [3.8, 2.6, 2.2, 2.4, 2.2, 3.8])
    bullets(d, SIM_NOTES)
    d.add_heading('6.2 서버 자동 테스트', 2)
    bullets(d, SERVER_EVIDENCE)
    d.add_heading('7. 우리가 해결하지 못하는 것', 1)
    bullets(d, LIMITS)
    d.add_heading('8. 발표용 문장', 1)
    bullets(d, PITCH)
    d.add_heading('출처', 1)
    for k, (t, u) in SOURCES.items():
        p = d.add_paragraph(f'[{k}] {t}  {u}')
        for r in p.runs:
            r.font.size = Pt(9)
    footer = d.sections[0].footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    fld = OxmlElement('w:fldSimple')
    fld.set(qn('w:instr'), 'PAGE')
    footer._p.append(fld)
    return d


if __name__ == '__main__':
    OUT.parent.mkdir(parents=True, exist_ok=True)
    draw_flow()
    OUT.with_suffix('.md').write_text(md(), encoding='utf-8')
    docx().save(OUT.with_suffix('.docx'))
    print(OUT.with_suffix('.docx'))
