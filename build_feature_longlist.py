from pathlib import Path
from collections import Counter
from docx import Document
from docx.shared import Inches, Pt, RGBColor, Cm
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT

ROOT=Path(__file__).parent
DATA='''
#1 환자용 웹 환자 정보 입력
P02|환자 본인·보호자·이송자 구분과 관계·연락처 입력 (P06 통합)
P05|개인정보 수집 및 병원 전달 동의
P07|환자 나이·주요 증상·발생 시각 입력, 질환·복용약 선택 입력 (P08 통합)
P09|모르겠음·미입력 상태 처리와 필수값 누락 시 확인 요청·계산 제한
P10|위험 신호 확인, 119 전화 연결과 병원 응답 대기 금지 안내 (P11 통합)
P12|자기신고 정보와 전문가 확인 정보를 구분해 표시
P13|병원 전송 전 환자 정보 요약·수정
#2 환자용 웹 병원 탐색과 수용 문의
P16|현재 위치 확인 및 직접 위치 입력
P17|목록에서 병원 후보 조회 (지도 표시는 여유 시 추가)
P18|병원별 위치·연락처·진료 가능 정보 표시
P19|예상 이동시간과 마지막 정보 갱신 시각 표시
P20|추천 이유와 추가 확인이 필요한 항목 표시
P21|병원에 환자별 수용 문의 전송
P22|문의 접수·확인 중·수용·불가 상태 표시
P23|병원이 보낸 추가 질문에 답변|선택
P24|미응답 경과시간 표시
P25|병원 전화 연결 (tel 링크)
P26|수용 불가 사유 확인 및 다른 후보 탐색
P27|수용 가능한 병원 선택과 목적지 최종 확인
P28|목적지 확정 후 다른 문의 종료 처리
P29|문의 취소 및 취소 사유 입력
#3 환자용 웹 이동과 도착
P31|이동 시작·미출발 상태 구분과 도착예정시간 전달 (P34 통합)
P32|목적 병원과 주소, 안내사항 표시
P33|외부 지도 앱 길찾기 링크 연결
P36|환자 상태 변화 입력 및 병원 전달|선택
P37|주기적 조회로 병원 수용 상황 변경 알림 확인
P38|목적지 변경 제안 확인과 담당자 연락, 새 병원 확인 전 임의 변경 차단 (P39 통합)
P40|도착 알림과 병원 측 도착 확인
P41|이송 취소·미도착 처리
#4 병원용 웹 기본정보와 수용 상태 관리
H03|병원 기본정보·위치·연락처 관리
H04|진료 분야와 대응 가능한 환자 조건 관리
H05|자원별 용량과 사용 중·가용 상태 관리 (H06 통합)
H07|제한 사유·범위(진료 분야/기관 전체)·종료 예정 시각 등록 (H08·H09 통합)
H11|마지막 갱신 시각 표시, 누락·오래된 정보를 확인 필요로 처리 (A04 통합)
#5 병원용 웹 문의 확인과 응답
H15|신규 문의 목록과 알림 (주기적 갱신)
H16|문의 경과시간·환자 상태·도착예정시간 표시
H17|환자 정보 상세조회
H18|자기신고와 전문가 평가 출처 구분
H19|확인 중 상태 회신
H20|추가 정보 요청 및 답변 확인|선택
H21|원클릭 수용 가능 응답
H22|수용 불가와 사유 선택
H24|중복 클릭·중복 응답 방지
H26|장시간 미응답 요청 강조
H28|환자 측 취소·다른 병원 확정 반영
#6 병원용 웹 도착 예정 환자와 돌발 상황
H29|수용 확인된 환자 목록
H30|미출발·이동 중·도착 상태와 도착예정시간 표시
H32|환자 상태 변화 확인|선택
H33|시스템을 사용하지 않은 환자의 유입 등록
H34|돌발 유입·자원 감소 시 영향받는 예정 환자와 예상 자원 사용 표시 (H31 통합)
H35|기존 수용 유지 가능 또는 추가 조정 필요 판단 입력
H36|수용 상황 변경 저장·환자 웹 알림 표시와 연락 안내
H37|환자 실제 도착 확인
H38|도착 예정 점유를 실제 점유로 전환해 이중 계산 방지
H39|취소·미도착·퇴실 등 자원 상태 갱신
#7 배정 알고리즘 입력과 병원 후보 생성
A01|환자·병원·자원 입력 형식 정의와 유효성 검사
A02|자기신고·전문가 평가·미확인 정보 구분, 미확인 중증도를 낮게 처리하지 않는 규칙 (A19 통합)
A03|진료 적합성과 명시적 제한에 따른 후보 선별
A05|환자 위치와 병원 간 이동시간 행렬 생성 (직선거리 보정 또는 지도 API)
A08|현재 점유와 도착 예정 수요 반영
A10|후보 없음·자원 부족·정보 부족 사유 구분
#8 배정 알고리즘 최적화와 재계산
A11|비교 기준인 최근접 순차 배정 구현
A12|조기 치료 가능 순차 배정 구현
A13|다수 환자의 병원 배정 솔버 구현
A14|환자당 목적 병원 1곳 또는 미배정 제약
A15|병상·전문팀의 동시 사용량 제약
A16|도착 이전 치료 시작 금지 제약
A17|전체 인원 우선과 중증군 배정 우선 정책 비교
A18|환자별 최초 기준시각·허용 지연 관리
A20|실행 가능한 초기해 생성·검증
A21|계산시간 제한과 검증된 대체안 반환
A22|실행 가능해와 최적성 증명 결과 구분
A23|입력 버전 확인과 오래된 계산 결과 적용 방지|선택
A24|신규 환자·병원 응답·자원 변화에 따른 재계산
A25|이동 중 목적지를 유지하며 미출발 환자 재배정
A26|목적지는 유지해도 예상 치료 시작시각은 재평가
A27|기존 계획이 불가능하면 조정 필요 상태 반환
A28|목적지 변경 시 현재 위치 기준 추가 이동·지연 비교|선택
A30|돌발 유입 대비 여유 자원 정책 비교 (실험)|선택
#9 공통 서버와 데이터 관리
S01|병원 웹·환자 웹 공통 API 서버
S02|이송 요청자·병원·환자·문의·응답·이송·자원 데이터 모델
S03|문의부터 도착까지의 상태 전이 규칙과 잘못된 순서의 요청 차단 (S04 통합)
S05|수용 확인과 자원 반영 트랜잭션, 응답 직전 재확인·동시 처리 충돌 방지 (H23·H25 통합)
S06|재전송해도 동일 문의가 중복 생성되지 않는 처리|선택
S07|복수 병원 응답 중 최종 목적지 1곳 확정
S08|확정되지 않은 문의와 확정된 예정 수요 구분
S11|미응답·미도착 확인 타이머|선택
S12|서버와 솔버 연결 (동기 호출)
S15|상태 변경 이력과 처리자·서버 기준 시각 기록 (S14 통합)
S16|병원 공공데이터 1회 적재와 병원 식별자 매칭
#10 배포와 보안
O03|HTTPS 적용과 API 키 환경변수 분리
O11|서버 1곳 배포와 시연 접속 방법 마련
#11 시험과 평가 및 제출 산출물
T01|환자·병원·자원 가상 데이터 생성기
T02|정상 문의 → 수용 → 이동 → 도착 통합 테스트
T03|수용 불가·미응답·취소 테스트
T04|동시 수용·중복 요청·동시 담당자 응답 테스트
T05|A 이동 중 미등록 중증환자 B 유입 시나리오
T06|소아·의료공백지역·적합 병원 없음 시나리오
T07|오래된 정보·연결 실패 테스트|선택
T08|자원·진료 적합성·시간 제약 자동 검증
T09|작은 사례의 전수열거 최적값 비교
T10|순차 배정과 솔버의 동일 조건 비교
T11|군별 미배정·최장 지연·한도 초과 측정
T12|목적지 변경 횟수와 추가 이동시간 측정
T13|계산시간·실패율·대체 반환률 측정
T14|병원 담당자 역할 사용성 실험 (5명 이내, 동시 문의 1·5건)
T15|응답시간·클릭 수·누락·오응답 측정
T16|일반 보호자의 증상 입력 이해도·누락 평가|선택
T17|의료진의 문항·흐름·우선순위 검토|선택
T18|요구사항 명세·화면 설계·DB 구조·API 명세 작성
T19|재현 가능한 실행 안내·실험 결과·시연 영상 작성
T20|구현 완료·가상 검증·미구현 범위를 구분한 최종 보고서
'''
EXCLUDED=[
('환자용 웹','P01(첫 화면 문구로 대체), P04, P14, P15, P30, P35, P42, P43'),
('병원용 웹','H10, H12, H13, H14, H27, H40'),
('배정 알고리즘','A06, A07, A09, A29'),
('공통 서버','S09·S10(주기적 갱신으로 대체), S13(공통 오류 처리로 대체), S17(길찾기 링크로 대체), S18'),
('보안과 운영','O01(실제 운영용 접근 통제는 향후 과제), O04·O05(보고서에 정책으로 기술), O06, O08, O09, O10, O12'),
]
MERGED='P06, P08, P11, P34, P39, H06, H08, H09, H23, H25, H31, A04, A19, S04, S14'
groups=[]
for line in DATA.strip().splitlines():
    if line.startswith('#'):groups.append((line[1:],[]))
    else:
        bits=line.split('|');groups[-1][1].append((bits[0],bits[1],bits[2] if len(bits)>2 else '핵심'))
all_rows=[r for _,rows in groups for r in rows]
assert len(all_rows)==112 and len({r[0] for r in all_rows})==112
assert Counter(r[2] for r in all_rows)=={'핵심':100,'선택':12}
d=Document();s=d.sections[0]
s.page_width=Inches(8.5);s.page_height=Inches(11)
s.top_margin=s.bottom_margin=Cm(1.7);s.left_margin=s.right_margin=Cm(1.8)
for name in ['Normal','Title','Heading 1','Heading 2']:
    st=d.styles[name];st.font.name='맑은 고딕';st.font.color.rgb=RGBColor(0,0,0)
    st.element.get_or_add_rPr().rFonts.set(qn('w:eastAsia'),'맑은 고딕')
    st.paragraph_format.line_spacing=Pt(15)
    st.paragraph_format.space_after=Pt(6)
d.styles['Normal'].font.size=Pt(10.5)
d.styles['Title'].font.size=Pt(22);d.styles['Title'].paragraph_format.line_spacing=Pt(29)
d.styles['Heading 1'].font.size=Pt(13);d.styles['Heading 1'].paragraph_format.space_before=Pt(12)
d.styles['Heading 1'].paragraph_format.keep_with_next=True
d.core_properties.title='응급환자 병원 연결 서비스 구현 기능 목록'
d.core_properties.author=''
counts=Counter(r[2] for r in all_rows)
d.add_paragraph('응급환자 병원 연결 서비스\n구현 기능 롱리스트','Title')
d.add_paragraph('병원용 웹 · 환자 및 보호자용 웹 · 배정 알고리즘  |  2026년 9월 28일')
d.add_paragraph(f'캡스톤 기간 안에 구현·검증할 범위를 기능 ID별로 정리한 팀 작업 관리 문서이다. 기존 158개 항목에서 통합·제외를 거쳐 총 11개 영역, {len(all_rows)}개 항목으로 확정했다. 담당자와 상태 칸은 팀에서 작성한다.')
d.add_paragraph('로그인과 계정 관리 기능은 구현 범위에서 제외한다. 기존 기능 ID는 참조를 위해 유지하며, 통합된 기능은 대표 ID에 괄호로 표기하고 삭제·통합한 ID는 재사용하지 않는다.')
d.add_heading('분류와 사용 방법',1)
d.add_paragraph('핵심은 주요 서비스 흐름과 알고리즘 검증에 필요한 기능, 선택은 핵심 완료 후 일정에 따라 추가할 기능이다. 핵심 100개 전체가 첫 시연 대상은 아니며, 상태 칸에 첫 시연 / 이후 구현과 계획 / 진행 / 검증 / 완료를 함께 기록한다. 범위에서 뺀 기능은 문서 끝의 제외 항목에 모아 최종 보고서의 한계 및 향후 과제로 기술한다.')
d.add_paragraph('담당자·상태 공란은 미구현을 의미하지 않는다. 기존 솔버와 응답 화면에 구현된 항목도 포함되어 있으므로 확인 후 계획 / 진행 / 검증 / 완료로 기록한다. 완료 판단은 화면 유무뿐 아니라 서버 연결과 해당 예외 조건의 검증을 포함한다.')
d.add_paragraph(f'구분별 항목 수  |  핵심 {counts["핵심"]}개 · 선택 {counts["선택"]}개')
d.add_heading('구현 방식 기준',1)
for text in [
'환자용 화면은 네이티브 앱 대신 모바일 웹으로 구현한다. 앱 설치 없이 이용하며, 위치 권한 거절 시 직접 입력을 제공한다. 전화·길찾기 연결은 단순화하여, 전화는 tel 링크, 길찾기는 외부 지도 앱 링크로 연결한다.',
'실시간 갱신은 2~3초 주기의 조회로 처리한다. 웹소켓과 푸시 알림은 사용하지 않는다.',
'가상 데이터 시연에서는 로그인 없이 병원별 고유 URL로 접속한다. URL 구분은 접근 통제를 대신하지 않으며, 실제 운영용 인증·권한 체계는 별도 과제로 둔다.',
'병원 기본정보는 공공데이터를 1회 적재하고, 환자·자원 변화는 가상 데이터로 구성한다. 이동시간은 지도 API 또는 실험용 추정값을 사용하며 추정 방식과 한계를 명시한다. 초기에는 필요한 진료과와 중증도가 입력된 것으로 가정한다.',
]:d.add_paragraph(text)
d.add_heading('첫 통합 시연의 흐름',1)
d.add_paragraph('환자 정보 입력 → 후보 병원·배정안 계산 → 수용 문의·응답 → 목적지 확정·이동 → 미등록 환자 유입 → 예상 치료 시작시각·수용 가능 여부 재평가 → 변경 알림 → 도착 확인')
d.add_paragraph('먼저 이 흐름을 연결한 후 수용 불가·미응답·취소·동시 응답을 추가한다. 이후 이동 중 돌발 환자 유입과 재계산(A24~A27, H33~H35, T05)을 검증한다. A26·H33~H35·T05와 변경 알림(P37·H36)은 핵심 시나리오이므로 유지한다. P09의 누락 처리와 T12의 목적지 변경·추가 이동시간 평가도 핵심으로 유지한다.')
d.add_paragraph('일정이 부족하면 중복 정보 표시와 중간 상태를 단순화하고, 추가 시나리오 및 사용성 실험 규모를 조정한다. 동시 수용 충돌 방지와 자원 제약 검증은 유지한다. 핵심을 선택으로 변경해도 전체 항목 수는 줄지 않으므로 분류 변경 시 개수를 다시 집계한다.')
d.add_heading('설계 기준',1)
for text in [
'일반인의 입력은 관찰 정보로 취급한다. 위험 신호 문항은 전문가 검토 대상이며, 미확인 정보를 경증으로 처리하거나 설문으로 자가용 이동 가능을 확정하지 않는다.',
'알고리즘은 배정안을 제안하고 병원은 환자별 수용 여부를 확인한다. 수용 응답을 즉시 치료나 특정 병상의 보장으로 표시하지 않는다.',
'이동 중 목적지는 우선 유지한다. 자원 변화가 발생하면 예상 치료 시작시각과 기존 수용 가능 여부를 다시 평가하고, 목적지 변경은 새 병원 확인과 담당자 판단을 거친다.',
'현재 구현된 고정 환자 처리와 달리 A26은 치료 시작시각 재평가를 요구하는 추가 과제이다. 우선순위와 허용 지연은 실험 설정과 임상적으로 검토한 기준을 구분한다.'
]:d.add_paragraph(text)


def make_table(rows):
    t=d.add_table(rows=1,cols=5);t.autofit=False
    widths=[1.25,10.3,1.25,2.0,3.19]
    for col,w in zip(t.columns,widths):col.width=Cm(w)
    for c,txt in zip(t.rows[0].cells,['ID','구현 기능','구분','담당자','상태']):c.text=txt
    for item in rows:
        for c,txt in zip(t.add_row().cells,[*item,'','']):c.text=txt
    pr=t._tbl.tblPr;b=OxmlElement('w:tblBorders')
    for side in ['top','left','bottom','right','insideH','insideV']:
        el=OxmlElement('w:'+side);el.set(qn('w:val'),'single');el.set(qn('w:sz'),'4');el.set(qn('w:color'),'D9D9D9');b.append(el)
    pr.append(b)
    for i,row in enumerate(t.rows):
        tr=row._tr.get_or_add_trPr();tr.append(OxmlElement('w:cantSplit'))
        if i==0:tr.append(OxmlElement('w:tblHeader'))
        for j,(c,w) in enumerate(zip(row.cells,widths)):
            c.width=Cm(w);c.vertical_alignment=WD_CELL_VERTICAL_ALIGNMENT.CENTER
            cp=c._tc.get_or_add_tcPr()
            sh=OxmlElement('w:shd');sh.set(qn('w:fill'),'E7EDF3' if i==0 else 'FFFFFF');cp.append(sh)
            mar=OxmlElement('w:tcMar')
            for side in ['top','bottom','left','right']:
                e=OxmlElement('w:'+side);e.set(qn('w:w'),'75');e.set(qn('w:type'),'dxa');mar.append(e)
            cp.append(mar)
            for p in c.paragraphs:
                p.paragraph_format.space_after=Pt(0);p.paragraph_format.line_spacing=Pt(14)
                if j!=1:p.alignment=WD_ALIGN_PARAGRAPH.CENTER
                for r in p.runs:r.font.size=Pt(9.5);r.bold=i==0
    return t

for title,rows in groups:
    d.add_heading(title,1)
    make_table(rows)
    p=d.add_paragraph();p.paragraph_format.space_after=Pt(0);p.paragraph_format.line_spacing=Pt(3)
d.add_heading('제외 항목 (향후 과제)',1)
d.add_paragraph('아래 ID는 캡스톤 범위에서 제외하며 최종 보고서의 한계 및 향후 과제로 기술한다.')
for area,ids in EXCLUDED:d.add_paragraph(f'{area}  |  {ids}')
d.add_paragraph(f'다른 항목에 통합  |  {MERGED}')
footer=s.footer.paragraphs[0];footer.alignment=WD_ALIGN_PARAGRAPH.CENTER
fld=OxmlElement('w:fldSimple');fld.set(qn('w:instr'),'PAGE');footer._p.append(fld)
for st in d.styles:
    for b in list(st.element.iter(qn('w:pBdr'))):b.getparent().remove(b)
for p in list(d.paragraphs)+[p for t in d.tables for r in t.rows for c in r.cells for p in c.paragraphs]:
    e=OxmlElement('w:snapToGrid');e.set(qn('w:val'),'0');p._p.get_or_add_pPr().append(e)
out=ROOT/'docs/design/캡스톤_전체구현기능_롱리스트.docx';out.parent.mkdir(parents=True,exist_ok=True);d.save(out)
print('Created',len(all_rows),'features',dict(counts))

# Export the same revised longlist as readable plain text.
from docx.oxml.ns import qn as _qn
lines=[]
for el in d.element.body:
    if el.tag==_qn('w:p'):
        lines.append(''.join(el.itertext()) if False else ''.join(t.text or '' for t in el.iter(_qn('w:t'))))
    elif el.tag==_qn('w:tbl'):
        for row in el.findall(_qn('w:tr')):
            vals=[''.join(t.text or '' for t in cell.iter(_qn('w:t'))) for cell in row.findall(_qn('w:tc'))]
            lines.append(' | '.join(vals))
        lines.append('')
(ROOT/'docs/design/캡스톤_전체구현기능_롱리스트.txt').write_text('\n'.join(lines),encoding='utf-8-sig')
