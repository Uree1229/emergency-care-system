from docx import Document
from docx.shared import Cm, Pt, RGBColor
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from pathlib import Path

ROOT=Path(__file__).parent
d=Document()
s=d.sections[0]
s.page_width=Cm(21); s.page_height=Cm(29.7)
s.top_margin=Cm(2); s.bottom_margin=Cm(2); s.left_margin=Cm(2.1); s.right_margin=Cm(2.1)
for name in ['Normal','Title','Subtitle','Heading 1','Heading 2']:
 st=d.styles[name]; st.font.name='맑은 고딕'; st.font.color.rgb=RGBColor(0,0,0)
 st.element.get_or_add_rPr().rFonts.set(qn('w:eastAsia'),'맑은 고딕')
st=d.styles['Normal']; st.font.size=Pt(10.5); st.paragraph_format.line_spacing=1.22; st.paragraph_format.space_after=Pt(7)
for name,size in [('Title',23),('Heading 1',15),('Heading 2',11.5),('Subtitle',11)]:
 d.styles[name].font.size=Pt(size)
 d.styles[name].paragraph_format.space_after=Pt(9)
d.styles['Heading 1'].paragraph_format.space_before=Pt(12)
f=s.footer.paragraphs[0]; f.alignment=WD_ALIGN_PARAGRAPH.CENTER
r=f.add_run(); fld=OxmlElement('w:fldSimple'); fld.set(qn('w:instr'),'PAGE'); r._r.addnext(fld)
d.core_properties.title='실시간 수용 확인과 도착 예정 수요를 반영한 응급의료 연계 시스템 제안서'
d.core_properties.author=''
def p(t): return d.add_paragraph(t)
def h(t): return d.add_heading(t,1)
def sub(t,body): d.add_heading(t,2); p(body)
def page(): d.add_page_break()
def table(headers,rows,widths):
 t=d.add_table(rows=1, cols=len(headers)); t.alignment=WD_TABLE_ALIGNMENT.CENTER; t.autofit=False
 for c,w in zip(t.columns,widths): c.width=Cm(w)
 for i,txt in enumerate(headers): t.rows[0].cells[i].text=txt
 for row in rows:
  cells=t.add_row().cells
  for i,v in enumerate(row): cells[i].text=v
 pr=t._tbl.tblPr; borders=OxmlElement('w:tblBorders')
 for side in ['top','left','bottom','right','insideH','insideV']:
  x=OxmlElement('w:'+side); x.set(qn('w:val'),'single'); x.set(qn('w:sz'),'4'); x.set(qn('w:color'),'D9D9D9'); borders.append(x)
 pr.append(borders)
 for ri,row in enumerate(t.rows):
  tr=row._tr.get_or_add_trPr(); ns=OxmlElement('w:cantSplit'); tr.append(ns)
  if ri==0: tr.append(OxmlElement('w:tblHeader'))
  for ci,c in enumerate(row.cells):
   c.width=Cm(widths[ci]); c.vertical_alignment=WD_CELL_VERTICAL_ALIGNMENT.CENTER
   cp=c._tc.get_or_add_tcPr(); sh=OxmlElement('w:shd'); sh.set(qn('w:fill'),'E7EDF3' if ri==0 else 'FFFFFF'); cp.append(sh)
   margins=OxmlElement('w:tcMar')
   for side in ['top','left','bottom','right']:
    x=OxmlElement('w:'+side); x.set(qn('w:w'),'90'); x.set(qn('w:type'),'dxa'); margins.append(x)
   cp.append(margins)
   for pp in c.paragraphs:
    pp.paragraph_format.space_after=Pt(2); pp.paragraph_format.line_spacing=1.12
    for rr in pp.runs: rr.font.size=Pt(9.5); rr.bold=ri==0
 p('')
def link(label,url):
 pp=p(''); hyp=OxmlElement('w:hyperlink'); hyp.set(qn('r:id'),pp.part.relate_to(url,RT.HYPERLINK,is_external=True))
 rr=OxmlElement('w:r'); rp=OxmlElement('w:rPr'); col=OxmlElement('w:color'); col.set(qn('w:val'),'245580'); rp.append(col); rr.append(rp)
 tx=OxmlElement('w:t'); tx.text=label; rr.append(tx); hyp.append(rr); pp._p.append(hyp)

d.add_paragraph('응급의료 연계 시스템\n개발 제안서','Title')
d.add_paragraph('실시간 수용 확인과 도착 예정 수요 반영','Subtitle')
p('캡스톤 디자인  |  작성일 2026년 9월 7일')
h('1 프로젝트 개요')
p('본 프로젝트는 환자 또는 보호자가 지도에서 주변 병원의 진료 역량과 수용 상태를 확인하고, 환자 정보와 도착예정시간을 전달하여 병원의 수용 확인을 받을 수 있는 의사결정 지원 시스템을 개발한다.')
p('수용이 확인된 환자가 이동을 시작하면 도착 예정 수요에 반영하고, 이를 이후 병원 안내와 환자 배정에 활용한다. 초기에는 강원권 일부 지역을 사례로 구현하며, 다수사상자 발생 시 배정을 조정하는 기능은 확장 범위로 설정한다.')
h('2 추진 배경과 필요성')
p('응급환자의 이송병원을 결정할 때는 거리와 병상 외에도 환자의 연령과 증상, 필요한 의료진 및 장비, 현재 대응 가능 상태를 고려해야 한다. 병상에 여유가 있어도 해당 환자에게 필요한 진료 역량이 부족하면 수용이 어려울 수 있다.')
p('국립중앙의료원 공개 API는 병원 위치, 진료과목과 시간, 가용병상 및 중증질환 수용가능정보 등을 제공한다. 그러나 공개 정보만으로 개별 환자의 수용을 확정하거나 도착 시점의 진료 가능 상태를 보장할 수는 없다. [1]')
p('정부는 이송지침 정비와 기관 간 협력을 추진하고 있으며, SAVE-R에서는 AI 중증도 분류, 병원 추천, 환자 및 진료자원 공유가 시연되었다. 본 프로젝트는 병원 응답과 이동 예정 정보를 후속 의사결정에 반영하는 과정을 중심으로 개발한다. [2][3]')
p('목표는 의료인력 부족 자체의 해소가 아니라, 제한된 의료자원 안에서 수용 확인과 이송 조정을 지원하여 불필요한 탐색 및 지연을 줄일 가능성을 검증하는 것이다.')

page(); h('3 해결 과제와 사용자')
table(['해결 과제','대응 방향'],[
('개별 환자의 실제 수용 여부 불명확','환자 정보를 전달하고 병원이 수용 여부 확인'),
('병상 정보와 진료 역량의 차이','치료 유형별 수용 상태와 제한 사유 관리'),
('동일 병원으로 동시 유입','수용 확정 및 이동 중 환자를 예상 수요에 반영'),
('정보 갱신 지연','확인 시각과 유효시간 및 재확인 상태 표시'),
('도착 전 환자 정보 부족','주요 증상과 연령 및 도착예정시간 공유')],[6,10.8])
table(['사용자','주요 역할'],[
('환자 및 보호자','환자 정보 입력, 병원 후보 및 응답 확인, 이동 상태 갱신'),
('병원 담당자','수용 상태 입력, 문의 응답, 도착 예정 환자 확인'),
('구급대 및 상황실','환자별 배정 현황 확인, 이송 조정과 다수환자 관리')],[4.2,12.6])
h('4 지도 조회와 환자 입력')
sub('지도 기반 병원 조회','병원 위치, 예상 이동시간, 진료 역량, 병원 확인 기준의 추가 수용 상태, 도착 예정 환자 수와 마지막 갱신 시각을 표시한다. 빈 병상 수를 추가 수용 인원으로 자동 환산하지 않으며, 수용 상태는 치료 유형별로 구분한다.')
p('표시 예시: A병원 / 차량 18분 / 일반 응급 추가 수용 3명 / 소아 응급 확인 필요 / 수용 확정 후 이동 중 2명 / 마지막 확인 2분 전. 위 수치는 화면 설명을 위한 가상 정보이다.')
sub('간단한 환자 정보 입력','연령, 주요 증상, 발생 시각과 상태 변화를 입력받아 병원에 전달할 정보로 정리한다. 진단이나 자가용 이동의 안전성 판정에는 사용하지 않는다. 위험 신호가 있으면 119 연결을 우선 제공하며, 실제 적용 전 의료전문가의 문항 검토를 거친다. 일반 차량에서는 동승자 또는 정차한 사용자의 조작을 전제로 한다.')

page(); h('5 수용 확인과 이동 정보 관리')
p('사용자가 정보 제공에 동의하면 병원에 환자 정보와 도착예정시간을 전달한다. 병원은 간단한 상태 선택과 사유 입력으로 응답하고, 사용자는 응답을 확인한 뒤 이동 상태를 갱신한다.')
table(['병원 응답','처리 방식'],[
('수용 가능','해당 환자의 수용 상태 확정 및 예상 자원 사용 반영'),
('조건부 가능','가능한 시각과 치료 조건 표시 후 재확인'),
('수용 불가','해당 환자의 다른 후보 조회 및 배정 조정'),
('응답 없음','미확인 상태 유지 및 수용 가능으로 간주하지 않음')],[4.1,12.7])
p('수용 응답은 진료 순서나 즉시 치료를 보장하는 예약으로 표시하지 않는다. 제한 사유는 특정 치료 유형에 적용하며, 한 환자를 수용하지 못한다는 이유로 병원 전체를 후보에서 제외하지 않는다.')
sub('도착 예정 환자 관리','수용 확인 후 이동을 시작한 환자의 목적지와 도착예정시간을 관리한다. 구급차와 일반 차량을 모두 포함하며, 도착·취소·목적지 변경에 따라 상태를 갱신한다. 장시간 확인되지 않은 정보는 재확인 대상으로 전환한다.')
sub('동시 요청 조정','같은 자원을 여러 환자에게 중복 배정하지 않도록 수용 확정 내역을 일관되게 관리한다. 단순 조회나 응답 대기만으로 확정 차감하지 않으며, 병원 확인을 받은 환자의 예상 자원 사용을 후속 배정에 반영한다.')
h('6 서비스 이용 흐름')
sub('환자와 보호자','환자 정보 입력 → 위험 신호 확인 → 후보 조회 → 정보 제공 동의 및 수용 문의 → 응답 확인 → 이동 시작 → 도착 또는 취소 확인')
sub('병원 담당자','수용 상태 입력 → 문의 확인 → 수용 여부 응답 → 도착 예정 환자 확인 → 도착 및 자원 상태 갱신')
sub('상황실 담당자','환자와 병원 현황 확인 → 임시 배정 → 병원 응답 반영 → 배정 확정 또는 수정 → 이송 현황 관리')

page(); h('7 개발 기여와 확장 방향')
p('다수사상자의 병원 배정, 구급차 배차 및 치료 순서 최적화는 이미 연구되어 있다. 해외 MasTER 연구는 AI 기반 배정과 웹 관제 플랫폼도 제안하였다. 따라서 전체 환자 최적 배정 자체를 신규 기술로 주장하지 않으며, 다음 요소의 통합 구현과 비교 평가에 기여를 둔다. [4][5]')
sub('병원 응답을 통한 정보 갱신','실제 확인 결과를 환자별 안내와 후속 배정에 반영한다. 미확인, 조건부 가능, 수용 확정 상태를 구분하여 공개 정보와 병원 확인 정보가 혼동되지 않도록 한다.')
sub('일반 차량을 포함한 도착 예정 수요','수용 확인 후 이동하는 환자의 정보를 병원과 공유한다. 도착 취소와 앱 미사용 환자 등으로 실제 수요와 차이가 발생할 수 있으므로 예상 수요로 취급한다.')
sub('미래 수요 예측과 다수환자 배정','현재 자원, 이동 중 환자, 도착예정시간과 가정한 자원 사용시간으로 향후 수요를 추정한다. 다수사상자 상황에서는 필요 진료 역량과 병원 제약을 고려하여 배정안을 계산하고, 병원 응답에 따라 미출발 환자의 배정을 수정한다.')
h('8 초기 구현 범위와 데이터')
table(['구분','초기 구현 계획'],[
('대상 지역','강원권 일부 지역'),
('병원 기초 정보','공개 API 기반 위치, 기관 정보 및 가용병상 등'),
('환자와 병원 응답','가상 환자 시나리오 및 병원 역할 테스트 사용자 입력'),
('의료진 가용시간','가상 일정과 치료 유형별 제약으로 모델링'),
('핵심 화면','사용자 지도와 문의 화면, 병원 응답과 도착 예정 목록'),
('확장 기능','미래 수요 예측 및 다수환자 배정')],[4.1,12.7])
p('개별 의사의 실시간 출근·당직 정보 확보를 전제로 하지 않는다. 병원 연계 전 단계의 환자 정보, 의료진 일정 및 수용 응답은 시뮬레이션 데이터로 명확히 표시한다.')

page(); h('9 성능 평가 계획')
p('동일한 환자 발생 시나리오를 반복 적용하여 기능별 효과를 비교한다. 병원 응답 지연, 도착 취소, 이동시간 오차와 갑작스러운 수용 불가 상황을 포함한다.')
table(['비교 방식','평가 목적'],[
('현재 정보 기반 안내','기본 성능 확인'),
('병원 응답을 반영한 안내','수용 확인 절차의 효과 평가'),
('응답과 도착 예정 수요 반영','예정된 유입을 고려하는 방식의 추가 효과 평가')],[7.1,9.7])
table(['평가 지표','측정 내용'],[
('수용 확정 소요시간','문의 시작부터 수용 확인까지 걸린 시간'),
('병원 문의 횟수','환자별 확인 요청 수'),
('중복 배정과 미배정','자원 중복 배정 및 목적지 미확정 환자 수'),
('치료 개시 지연','시뮬레이션 내 환자별 예상 치료 시작 지연'),
('재배정과 목적지 변경','상황 변화에 따른 배정 수정 횟수'),
('병원 응답 처리시간','담당자가 문의 확인과 응답에 사용한 시간')],[6,10.8])
p('측정 전에는 개선 수치나 달성률을 확정하지 않는다. 시뮬레이션 결과를 실제 사망률 감소나 임상 효과로 해석하지 않으며, 실제 적용에는 병원 협력과 추가 검증이 필요하다.')
h('10 기대 효과')
p('환자와 보호자는 병원이 확인한 수용 정보를 바탕으로 후보를 검토할 수 있고, 병원은 도착 전 환자 정보를 확인하여 준비와 대응을 판단할 수 있다.')
p('수용 확정 환자의 도착 예정 수요를 후속 의사결정에 반영함으로써 제한된 자원에 환자가 집중되는 문제를 완화할 가능성을 평가한다. 이후 병원 및 상황실과의 협력을 통해 실제 데이터 연계와 현장 적용성을 검증한다.')

page(); h('참고자료')
p('본문의 대괄호 번호는 아래 자료와 연결된다. 정책과 시스템 기능은 해당 자료의 공개 범위를 기준으로 기술하였으며, 제안한 기능과 성능은 개발 및 평가 대상이다.')
refs=[
('[1] 국립중앙의료원 전국 응급의료기관 정보 조회 서비스','공공데이터포털. 병원 위치, 진료 정보, 가용병상 및 중증질환 수용가능정보 제공 항목.','https://www.data.go.kr/data/15000563/openapi.do'),
('[2] 응급환자 이송체계 혁신 시범사업 성과와 전국 확대 계획','대한민국 정책브리핑, 2026년 6월 22일. 지역별 이송지침과 기관 간 협력체계 개선.','https://www.korea.kr/news/policyNewsView.do?newsId=148966917'),
('[3] 한국형 ARPA H 프로젝트 응급이송 혁신기술 공개','한국보건산업진흥원, 2026년 6월 17일. MAISTER 및 SAVE-R 기술 시연과 단계적 확산 방향.','https://www.khidi.or.kr/board/view?linkId=48946375&menuId=MENU00100'),
('[4] Optimizing emergency preparedness and resource utilization in mass-casualty incidents','Repoussis 외, 2016. European Journal of Operational Research, 255(2), 531–544. 배차, 병원 배정 및 치료 순서 최적화.','https://openaccess.city.ac.uk/id/eprint/19862/'),
('[5] Using AI to Optimize Patient Transfer and Resource Utilization During Mass-Casualty Incidents A Simulation Platform','Liu 외, 2025. arXiv 프리프린트. MasTER 플랫폼의 시뮬레이션 기반 평가이며 실제 재난 임상 효과 검증과는 구분됨.','https://arxiv.org/abs/2509.08756')]
for title,desc,url in refs:
 pp=p(title); pp.runs[0].bold=True
 p(desc); link('원문 열기',url)
for st in d.styles:
 for border in list(st.element.iter(qn('w:pBdr'))): border.getparent().remove(border)
d.styles['Subtitle'].font.italic=False
d.styles['Normal'].paragraph_format.line_spacing=1.15
d.styles['Normal'].paragraph_format.space_after=Pt(5)
for name in ['Heading 1','Heading 2']:
 d.styles[name].paragraph_format.space_before=Pt(8)
 d.styles[name].paragraph_format.space_after=Pt(6)
for pp in list(d.paragraphs)+[pp for tt in d.tables for row in tt.rows for cc in row.cells for pp in cc.paragraphs]:
 snap=OxmlElement('w:snapToGrid'); snap.set(qn('w:val'),'0'); pp._p.get_or_add_pPr().append(snap)
 for border in list(pp._p.iter(qn('w:pBdr'))): border.getparent().remove(border)
compact=False
for pp in d.paragraphs:
 if pp.text=='5 수용 확인과 이동 정보 관리': compact=True
 if pp.text=='7 개발 기여와 확장 방향': compact=False
 if compact:
  pp.paragraph_format.line_spacing=1.02
  pp.paragraph_format.space_after=Pt(4)
  if pp.style.name.startswith('Heading'): pp.paragraph_format.space_before=Pt(6)
d.save(ROOT/'docs/proposals/archive/응급의료_연계_시스템_제안서.docx')
print(ROOT/'docs/proposals/archive/응급의료_연계_시스템_제안서.docx')
