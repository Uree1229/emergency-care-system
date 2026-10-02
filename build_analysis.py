from pathlib import Path
import json, statistics

root = Path(__file__).parent
base = (root / 'build_proposal.py').read_text(encoding='utf-8')
exec(compile(base.split("d.add_paragraph('응급의료 연계 시스템")[0],str(root/'build_proposal.py'),'exec'))
summary = json.loads((root/'optimization_results/summary.json').read_text())
trials = json.loads((root/'optimization_results/trials.json').read_text())
md = []
old_p, old_h, old_table, old_link = p,h,table,link
def p(t):
    if t: md.extend([t,''])
    return old_p(t)
def h(t):
    md.extend(['## '+t,''])
    return old_h(t)
def table(headers,rows,widths):
    md.append('| '+' | '.join(headers)+' |')
    md.append('| '+' | '.join(['---']*len(headers))+' |')
    md.extend('| '+' | '.join(row)+' |' for row in rows)
    md.append('')
    return old_table(headers,rows,widths)
def link(label,url):
    md.extend([f'[{label}]({url})',''])
    return old_link(label,url)
def row(sc,pol): return next(r for r in summary if r['scenario']==sc and r['policy']==pol)

for name in ['Normal','Title','Heading 1','Heading 2']:
    d.styles[name].paragraph_format.line_spacing=1.10
d.styles['Normal'].font.size=Pt(10)
d.styles['Normal'].paragraph_format.space_after=Pt(5)
d.styles['Title'].font.size=Pt(20)
d.styles['Heading 1'].font.size=Pt(13)
d.styles['Heading 1'].paragraph_format.space_before=Pt(9)
d.styles['Heading 1'].paragraph_format.space_after=Pt(5)
d.core_properties.title='응급의료 시범사업 분석과 솔버 배정 실험'
d.add_paragraph('응급의료 시범사업 분석과\n솔버 배정 실험','Title')
md.extend(['# 응급의료 시범사업 분석과 솔버 배정 실험',''])
p('캡스톤 디자인 검토 자료  |  2026년 9월 13일')
p('기존 제안서에 언급한 시범사업을 검토하고, 가상 환자의 병원 배정에 OR-Tools 솔버를 적용했다. 배정 개선은 가능했지만 입력 정보가 부정확하면 효과가 작아졌다. 개발 초점은 병원 수용 확인과 정보 갱신을 반영하는 배정 지원으로 잡는 것이 타당하다.')
h('1 시범운영 대상과 확인된 한계')
p('호남권 응급환자 이송체계 혁신 시범사업은 2026년 3~5월 광주·전북·전남에서 시행된 운영체계 개선 사업이다. SAVE-R는 MAISTER 프로젝트의 AI 플랫폼으로, 6월 공개자료는 대구·경북 시연과 시범운영을 통한 현장 검증 방향을 설명한다. 서로 다른 사업의 성과를 합쳐 해석하지 않았다. [1][2]')
table(['구분','공개자료에서 확인한 내용','문제 또는 해석'],[
('남아 있는 문의와 지연','광역상황실의 병원 문의 평균 6.1개소, 처리시간 중위값 18분 [1]','개선 이후에도 병원 확인 절차에 시간이 든다.'),
('지역별 차이','현장체류시간은 광주·전북에서 감소했지만 전남은 18초 증가 [1]','같은 운영체계의 효과가 지역별로 다르다.'),
('자원 정보 갱신','병원이 자원과 처치 수용 능력을 주기적으로 현행화하도록 설계 [3]','갱신 사이의 상태 변화는 별도로 검증할 필요가 있다.'),
('AI 기능의 검증 범위','SAVE-R는 병원 추천과 재이송 병원 재선정 등을 시연 [2]','검토 자료만으로 실제 추천 정확도·동시 배정 성능은 판단하기 어렵다.')
],[3.2,7,6.6])
p('성과도 함께 봐야 한다. 정부는 호남권 사업 기간 응급실 미수용 0건을 발표했다. 다만 이는 모든 문의가 즉시 승인되었다는 뜻은 아니며, 전후 지표만으로 특정 소프트웨어의 인과 효과를 분리할 수는 없다. [1]')
h('확인되지 않은 부분')
p('검토한 공개자료에는 SAVE-R의 전체 목적함수, 제약식, 원시 운영 로그, 정보 갱신 지연 분포, 동시 환자 처리 결과가 제시되어 있지 않다. 따라서 “미래 수요나 중복 배정을 처리하지 못한다”는 주장은 확인된 결함이 아니라 검증 과제로 남긴다. 9월 전국 적용은 6월 발표 당시 계획이며, 이번 조사로 전국 완료 여부를 확정하지 않았다. [1][2]')

page(); h('2 솔버 실험 설계')
p('실제 시스템에 접속하거나 운영 데이터를 사용하지 않았다. 정책 사업이나 SAVE-R의 알고리즘을 재현한 실험이 아니라, 캡스톤에서 구현할 배정 모듈의 가능성과 한계를 확인하는 소규모 합성 실험이다.')
table(['항목','실험 설정'],[
('규모','병원 3곳, 기본 환자 12명, 집중 유입 조건 24명'),
('공유 자원','병상 수 A 2개·B 2개·C 1개, 전문팀 A 1개·B 1개·C 0개'),
('환자 조건','전문팀 필요 확률 40%, 우선순위 가중치 1·2·4를 동일 확률로 부여'),
('시간','5분 단위, 이동 5~35분, 처치 15~30분, 기존 점유 10~30분'),
('계획 범위','현재 알려진 환자를 한 번에 배정, 치료 시작시각은 120분 이내'),
('반복','조건별 난수 시드 0~29, 5조건 × 30회 × 3방식 = 450개 평가'),
('솔버','OR-Tools CP-SAT 9.15.6755, 단일 작업자, 결정적 연산 예산 0.05, 실제 시간 상한 5초')
],[3.2,13.6])
p('위 수치와 120분 범위는 계산 실험용 가정이며 임상 기준이 아니다. 병상과 전문팀을 동일 처치 구간 동안 점유하도록 단순화했고, 120분 이후까지 이어지는 점유도 유지했다.')
h('최적화 목표와 제약')
p('목표는 ① 120분 안에 치료를 시작하도록 배정하지 못한 인원을 최소화하고 ② 같은 미배정 인원 안에서 우선순위 가중 치료 시작시간의 합을 줄이는 것이다. 목적함수는 M × 미배정 수 + Σ(가중치 × 시작시각)이며, M = 24 × 전체 가중치 합 + 1로 두어 첫 번째 목표를 우선한다.')
p('환자는 병원 한 곳에 배정하거나 미배정으로 남긴다. 치료는 도착 이후 시작하며, 전문팀이 필요한 환자는 A·B만 이용할 수 있다. 모든 시간대에 병상과 전문팀의 동시 사용량이 용량을 넘지 않도록 제약을 적용했다.')
h('비교 방식')
p('최근접 순차는 우선순위가 높은 환자부터 가능한 가장 가까운 병원에 배정한다. 조기치료 순차는 같은 순서로 가장 빨리 치료를 시작할 수 있는 병원을 선택한다. CP-SAT는 전체 환자의 병원과 시작시각을 함께 조정하며, 조기치료 순차의 해를 초기 후보로 사용한다.')
p('모든 방식에 동일한 관측 정보를 제공한다. 솔버에는 초기 후보보다 목적함수가 나빠지지 않는 조건을 추가하고, 시간 내 해를 얻지 못하면 초기 후보를 반환하도록 했다. 실제 실험에서는 대체 반환이 발생하지 않았다.')

page(); h('3 실제 실행 결과')
p('아래는 조건별 30회 평균이다. 시간은 의사결정 시점부터 치료 시작까지의 우선순위 가중 평균이며, 이동과 도착 후 대기를 포함한다. 병원 문의시간은 이번 모형에 포함하지 않았다.')
names={'normal':'정상 정보','surge':'환자 24명','stale_capacity':'자원 해제 20분 지연','unobserved_inflow':'미반영 환자 4명','time_error':'이동·처치시간 증가'}
rows=[]
for sc in names:
    a,b,c=[row(sc,pol) for pol in ['nearest','earliest','cp_sat']]
    rows.append((names[sc],f"{a['weighted_start']:.1f}분",f"{b['weighted_start']:.1f}분",f"{c['weighted_start']:.1f}분"))
table(['실험 조건','최근접 순차','조기치료 순차','CP-SAT'],rows,[6,3.6,3.6,3.6])
p('정상 정보에서는 조기치료 순차 대비 1.11분, 약 3.9% 감소했다. 동일 시드 차이에 대한 탐색적 95% t 구간은 0.56~1.66분이다. 미반영 환자 조건에서는 차이가 0.12분이고 구간은 −0.66~0.90분이어서 일관된 우위를 확인하기 어렵다. 이 구간은 합성 시나리오의 변동만 나타낸다.')
h('최적화 이후에도 드러난 문제')
p('첫째, 목표에 따라 결과가 달라졌다. 환자 24명 조건에서 회당 미배정 수는 조기치료 순차 1.0명에서 CP-SAT 0.4명으로 줄었다. 하지만 가중 평균 시간은 44.6분에서 47.2분으로 증가했다. 미배정 최소화가 우선이고 평균의 대상 환자도 달라지므로, 평균 시간만으로 성능을 판정하면 안 된다.')
p('둘째, 계획의 정확도는 입력에 의존했다. 자원 해제가 20분 늦어지면 CP-SAT의 가중 평균 시간은 계획 27.5분에서 실행 38.2분으로 늘었다. 미반영 환자 4명이 있으면 45.5분이 됐다. 관측 정보 안에서 가능한 배정도 실제 상태에서는 지연될 수 있다.')
p('셋째, 시간 오차가 누적됐다. 이동·처치시간에 1.3을 곱한 뒤 5분 단위로 올림한 조건에서 CP-SAT의 실행 시간은 36.3분이었다. 30회 중 환자 1명은 계획 범위인 120분을 넘겨 치료를 시작했다. 올림 때문에 실질 증가율은 정확히 30%보다 클 수 있다.')
h('검증과 결과 해석 범위')
p('450개 계획 모두 별도 검사로 진료 적합성, 도착 전 치료 금지, 자원 중복 사용 금지를 확인했다. 작은 2환자 사례는 전수열거 최적값과 일치했고, 자원 부족 사례에서는 미배정을 반환했다. 숨겨진 점유와 시간 오차는 배정 완료 후 실행 평가기에만 전달했다.')
p('실행 평가는 목적지와 계획 순서를 고정하고 실제 자원이 비면 치료를 시작하는 방식이다. 재문의·재배정은 구현하지 않았으며, 미반영 환자 4명은 자원을 차지하는 외부 점유로만 처리해 이들의 결과는 시간 평균에 포함하지 않았다. 따라서 지역 전체 환자 편익을 측정한 결과는 아니다.')

page(); h('4 솔버의 한계와 캡스톤 개선 방향')
p('본 실험의 150회 솔버 실행은 모두 FEASIBLE이었다. 가능한 해는 찾았지만 최적임을 증명한 것은 아니다. 일반 12명 조건에서 해와 솔버 하한의 상대 간격은 평균 약 91%로 컸다. 이는 실제로 91% 개선할 수 있다는 뜻이 아니라 현재 모델과 검색 예산으로 해의 최적성을 충분히 보증하지 못했다는 뜻이다. [4]')
p('계산 예산을 0.05에서 2로 늘린 추가 6사례 중 목적함수가 개선된 것은 2사례였다. 실제 계산시간은 약 0.11~0.17초에서 1.90~3.01초로 늘었지만 모두 FEASIBLE이었다. 단순히 시간을 늘리는 것 외에 하한 강화, 모델 구조 개선, 실행 가능한 초기해 개선이 필요하다.')
table(['문제','캡스톤에서 다룰 개선 과제'],[
('정보가 늦거나 빠짐','병원 확인 시각·유효시간을 저장하고, 이동 중 환자 및 도착·취소를 반영한다.'),
('효율과 우선순위 충돌','미배정 수와 시간 지표를 함께 제시하고, 중증군 미배정·최장 지연을 별도 평가한다.'),
('계산 중 상태 변경','미출발 환자만 다시 계산하고 이동 중 목적지는 담당자 확인 없이 바꾸지 않는다.'),
('최적성 증명 부족','해 상태와 계산시간을 표시하며 시간 초과 시 검증된 순차 배정으로 전환한다.'),
('의료자원 자체 부족','배정만으로 수용 역량을 늘릴 수 없으므로 미배정 사유와 상황실 조정 필요를 표시한다.')
],[4.1,12.7])
p('이번 모형은 전체 미배정 인원을 먼저 줄이므로 중증 환자의 배정을 최우선으로 보장하지는 않는다. 임상적으로 합의된 우선순위, 최대 허용 지연, 구급차 가용성, 실제 병원 응답을 추가해야 한다. 이 개선안의 효과와 재최적화의 안정성은 아직 검증하지 않았다.')
p('교수님께 제안할 방향: “기존 시범사업의 병원 확인 지연과 정보 갱신 문제를 검토하고, 병원 응답·도착 예정 수요를 반영한 배정 모듈을 구현한다. 솔버와 순차 배정을 비교하되, 정보 지연·돌발 유입 조건에서 성능이 유지되는지 검증한다.”')
h('참고자료')
link('[1] 보건복지부·소방청 시범사업 성과 발표 2026년 6월 22일','https://www.korea.kr/news/policyNewsView.do?newsId=148966917')
link('[2] 한국보건산업진흥원 MAISTER와 SAVE-R 시연 발표 2026년 6월 17일','https://www.khidi.or.kr/board/view?linkId=48946375&menuId=MENU00100')
link('[3] 보건복지부·소방청 시범사업 브리핑 2026년 2월 25일','https://www.korea.kr/briefing/policyBriefingView.do?newsId=156745893')
link('[4] Google OR-Tools CP-SAT 해 상태와 사용법','https://developers.google.com/optimization/cp/cp_solver')

for st in d.styles:
    for border in list(st.element.iter(qn('w:pBdr'))): border.getparent().remove(border)
for pp in list(d.paragraphs)+[pp for t in d.tables for row_ in t.rows for c in row_.cells for pp in c.paragraphs]:
    snap=OxmlElement('w:snapToGrid'); snap.set(qn('w:val'),'0'); pp._p.get_or_add_pPr().append(snap)
    if not pp.text and not pp._p.xpath('.//w:br') and not pp._p.xpath('.//w:hyperlink'):
        pp.paragraph_format.space_after=Pt(0); pp.paragraph_format.line_spacing=Pt(3)
d.save(root/'docs/research/응급의료_시범사업_문제점과_솔버실험.docx')
(root/'docs/research/응급의료_시범사업_문제점과_솔버실험.md').write_text('\n'.join(md),encoding='utf-8')
print('report created')
