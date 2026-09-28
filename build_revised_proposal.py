from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.shared import Cm, Pt, RGBColor


ROOT = Path(__file__).parent
OUT = ROOT / '캡스톤_프로젝트_제안서_병원응답보강_20260923.docx'


def set_font(run, size=None, bold=None):
    run.font.name = '맑은 고딕'
    rpr = run._element.get_or_add_rPr()
    fonts = rpr.rFonts
    if fonts is None:
        fonts = OxmlElement('w:rFonts')
        rpr.insert(0, fonts)
    for key in ('ascii', 'hAnsi', 'eastAsia', 'cs'):
        fonts.set(qn(f'w:{key}'), '맑은 고딕')
    run.font.color.rgb = RGBColor(0, 0, 0)
    if size:
        run.font.size = Pt(size)
    if bold is not None:
        run.bold = bold


def para(text='', style=None, bold_lead=None):
    p = doc.add_paragraph(style=style)
    if bold_lead and text.startswith(bold_lead):
        set_font(p.add_run(bold_lead), bold=True)
        set_font(p.add_run(text[len(bold_lead):]))
    else:
        set_font(p.add_run(text))
    return p


def heading(text):
    return para(text, 'Heading 1')


def linked_reference(text, url):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(0)
    p.paragraph_format.line_spacing = 1.02
    p.paragraph_format.keep_together = True
    rid = p.part.relate_to(url, RT.HYPERLINK, is_external=True)
    h = OxmlElement('w:hyperlink')
    h.set(qn('r:id'), rid)
    r = OxmlElement('w:r')
    rpr = OxmlElement('w:rPr')
    fonts = OxmlElement('w:rFonts')
    for key in ('ascii', 'hAnsi', 'eastAsia', 'cs'):
        fonts.set(qn(f'w:{key}'), '맑은 고딕')
    rpr.append(fonts)
    size = OxmlElement('w:sz')
    size.set(qn('w:val'), '16')
    rpr.append(size)
    color = OxmlElement('w:color')
    color.set(qn('w:val'), '000000')
    rpr.append(color)
    r.append(rpr)
    node = OxmlElement('w:t')
    node.text = text
    r.append(node)
    h.append(r)
    p._p.append(h)
    return p


def cell_text(cell, text, bold=False):
    cell.text = ''
    p = cell.paragraphs[0]
    p.paragraph_format.space_after = Pt(0)
    p.paragraph_format.line_spacing = 1.16
    set_font(p.add_run(text), size=8.5, bold=bold)
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    tcpr = cell._tc.get_or_add_tcPr()
    margins = OxmlElement('w:tcMar')
    for side in ('top', 'left', 'bottom', 'right'):
        x = OxmlElement(f'w:{side}')
        x.set(qn('w:w'), '65' if side in ('top', 'bottom') else '110')
        x.set(qn('w:type'), 'dxa')
        margins.append(x)
    tcpr.append(margins)


def shade(cell, fill):
    shd = OxmlElement('w:shd')
    shd.set(qn('w:fill'), fill)
    cell._tc.get_or_add_tcPr().append(shd)


def table(headers, rows, widths=None):
    t = doc.add_table(rows=1, cols=len(headers))
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.style = 'Table Grid'
    t.autofit = False
    if widths:
        for i, width in enumerate(widths):
            t.columns[i].width = Cm(width)
    for i, h in enumerate(headers):
        if widths:
            t.rows[0].cells[i].width = Cm(widths[i])
        cell_text(t.rows[0].cells[i], h, True)
        shade(t.rows[0].cells[i], 'E8EBEE')
    for j, row in enumerate(rows):
        cells = t.add_row().cells
        for i, value in enumerate(row):
            if widths:
                cells[i].width = Cm(widths[i])
            cell_text(cells[i], value, i == 0)
            if j % 2:
                shade(cells[i], 'F7F8F9')
    for row in t.rows:
        trpr = row._tr.get_or_add_trPr()
        no_split = OxmlElement('w:cantSplit')
        trpr.append(no_split)
        for cell in row.cells:
            for p in cell.paragraphs:
                p.paragraph_format.keep_together = True
    return t


doc = Document()
sec = doc.sections[0]
sec.page_width = Cm(21)
sec.page_height = Cm(29.7)
sec.top_margin = Cm(1.75)
sec.bottom_margin = Cm(1.7)
sec.left_margin = Cm(2.1)
sec.right_margin = Cm(2.1)

styles = doc.styles
normal = styles['Normal']
normal.font.name = '맑은 고딕'
normal.font.size = Pt(8.8)
normal.font.color.rgb = RGBColor(0, 0, 0)
normal.paragraph_format.space_after = Pt(3)
normal.paragraph_format.line_spacing = 1.13
title = styles['Title']
title.font.name = '맑은 고딕'
title.font.size = Pt(18)
title.font.bold = True
title.font.color.rgb = RGBColor(0, 0, 0)
title.paragraph_format.space_after = Pt(7)
title.paragraph_format.line_spacing = 1.15
title_ppr = title._element.get_or_add_pPr()
for border in title_ppr.findall(qn('w:pBdr')):
    title_ppr.remove(border)
heading_style = styles['Heading 1']
heading_style.font.name = '맑은 고딕'
heading_style.font.size = Pt(11)
heading_style.font.bold = True
heading_style.font.color.rgb = RGBColor(0, 0, 0)
heading_style.paragraph_format.space_before = Pt(6)
heading_style.paragraph_format.space_after = Pt(4)
heading_style.paragraph_format.keep_with_next = True

p = doc.add_paragraph(style='Title')
set_font(p.add_run('응급환자 진료 가능 병원 연결 시스템\n캡스톤 프로젝트 제안서'), size=18, bold=True)
p = para('캡스톤 디자인  |  2026년 9월')
p.paragraph_format.space_after = Pt(11)

para('응급환자가 출발 전에 자신의 상태를 진료할 수 있는 병원을 확인하도록 돕는 시스템을 제안한다. 구급대원과 직접 이동하는 환자·보호자에게 각각 필요한 화면을 제공하고, 환자의 긴급도와 예상 도착 시각의 진료 가능성을 함께 고려한다.')

heading('1 문제 상황')
para('응급환자에게 가까운 병원이 곧 진료 가능한 병원은 아니다. 2026년 3~5월 광주·전북·전남 시범사업에서 구급대는 광역상황실 지원 요청 전에 평균 3.8개 병원에, 광역상황실은 지원 건에서 평균 6.1개 병원에 문의했다. 상황실 처리시간 중앙값은 18분이었다. 응급실 미수용 0건의 성과가 있었지만 병원별 확인 과정은 남았다. 두 단계의 문의 수는 합산할 수 없고 18분도 전체 이송시간이 아니다. [1]')
para('기존에도 응급의료정보망의 가용병상·질환별 수용정보와 119구급 스마트시스템의 수용 요청 기능이 있다. 다만 화면의 병상 수가 환자에게 필요한 의료진, 도착 시 진료 자리, 개별 수용 확정을 대신하지는 않는다. 관련 현장 설문에서 구급대원과 응급의학과 전문의는 표시 정보와 실제 상황의 불일치, 병원 선정에 필요한 정보 부족을 지적했다. 이는 이용자 인식 조사이지 정보 오류율 측정은 아니다. [3, 6]')
para('병원의 수용 응답은 후보 추천을 실제 목적지로 바꾸는 마지막 결정이다. 소방청·국회예산정책처 자료를 인용한 보도에서 2025년 119구급 스마트시스템의 요청 건수 기준 응답률은 47.96%였다. 소방청이 의료기관 사용자 470명에게 물은 설문에서는 응답 부담의 이유로 인력 부족(29%)과 다른 업무 우선(25%)이 제시됐다. 이는 수용률이나 모든 병원의 미응답률이 아니며, 설문 응답이 지연시간을 직접 측정한 것도 아니다. [7]')
para('병원이 환자 상태와 의료진·진료 자리의 가용 여부를 확인한 뒤 답해야 하므로, 담당자가 바쁘거나 정보가 불확실하면 확인·응답이 늦어질 수 있다. 이때 구급대는 다른 병원을 다시 문의하거나 상황실 조정을 기다린다. 이 과정은 병목의 가능한 설명이지만, 2026년 시범사업의 18분을 원인별로 분해한 자료는 없다. [1, 3, 7]')
para('가상 예시로 구급대원이 중증 환자를 A병원에 이송하려 한다. 빈 병상은 표시되지만 필요한 수술을 맡을 의료진이 없거나 수용 응답이 늦으면 B병원을 다시 확인해야 한다. 국립중앙의료원도 가용병상과 질환별 진료·수술·시술 가능 정보를 별도로 관리한다. [3]')
para('구급차 밖에서도 문제는 생긴다. 보건복지부는 택시 등으로 응급실에 직접 오는 환자와 호남권 도서·산간의 의료 취약지역을 언급했다. [2] 전남 산간의 보호자가 야간에 아픈 아이와 직접 출발한다면, 가까운 응급실까지의 거리만으로 도착 후 소아 진료 인력과 자리가 있는지 알 수 없다. 여러 환자가 같은 시각에 도착할 때는 자원 중복과 긴급도까지 고려해야 한다.')

heading('2 제안하는 해결 방식')
para('병원 후보를 제시할 때 환자의 상태와 필요한 진료과, 예상 도착 시각 이후 의료진의 진료 가능 시간, 그때 이용할 수 있는 진료 자리·병상, 병원의 명시적 수용 응답을 함께 확인한다. 현재 비어 있는 자원이라도 도착 시점에 사용할 수 없다면 적합한 후보로 보지 않는다. 의료공백지역에서는 후보 부족과 긴 이동시간을 함께 고려한다.')
para('병원 응답 부담을 줄이기 위해 적합도가 낮은 요청은 사전에 걸러내고, 병원에는 긴급도·필요 진료·예상 도착 시각을 한눈에 보이는 형태로 보낸다. 담당자는 수용·불가·추가 확인 중을 간단히 회신하고, 불가 사유와 최신 자원 상태를 함께 남긴다. 담당자 알림과 교대 인계, 응답 지연 시 다음 후보·상황실 연결을 설계한다. 시간 기준은 병원과 협의해 검증한다. 인력 부족은 화면만으로 해결할 수 없어 담당자 지정과 운영 협력이 전제다.')
para('요청이 동시에 들어오면 구급차 이용 여부와 관계없이 긴급도가 높은 환자를 먼저 검토한다. 자기신고로 위급한 상태가 의심되면 즉시 119 신고를 안내하고 의료진·구급대원의 확인을 거친다. 병원 정보가 오래됐거나 응답이 없으면 수용 확정으로 표시하지 않는다. [2]')

heading('3 두 이용 주체의 사용 방식')
table(
    ['이용 주체', '필요한 지원'],
    [
        ('구급대원', '현장 환자 상태와 필요한 진료를 입력하고, 예상 도착 시각에 진료 가능한 병원 후보와 수용 응답을 확인한다. 환자 상태나 병원 자원이 바뀌면 다시 확인한다.'),
        ('일반 환자와 보호자', '구급차 없이도 증상·연령·위치와 이동 방법을 입력해 적합한 병원을 찾는다. 소아 진료처럼 필요한 의료진과 자리·병상을 확인하고, 위급 징후에는 119 신고를 안내한다.'),
    ], [3.5, 13.3]
)

heading('4 캡스톤에서 확인할 내용')
para('가상 환자와 병원으로 두 경로의 시제품을 만들고 가까운 병원부터 문의하는 방식과 비교한다. 의료공백지역·소아·동시 요청에서 병원 응답시간·미응답률, 재문의 수, 응답 단계 수, 긴급 환자 미배정과 도착 시점 자원 불일치를 측정한다.')
para('현재 솔버와 병원 응답 화면은 별도로 구현했다. 실제 의사 일정과 병원 수용 정보는 연결되지 않았으며, 현장 적용에는 병원의 최신 정보 제공과 임상 기준 검토가 필요하다.')

heading('5 병원 정보 확보 방법')
para('공개자료. 국립중앙의료원 API에서 병원 위치·진료과목·현재 가용병상·질환별 수용정보를, 건강보험심사평가원 API에서 전문과목별 전문의 수를 얻는다. [4, 5] 전체 병상과 현재 빈 병상을 구분하고, 공개 전문의 수를 당직 인원으로 해석하지 않는다.', bold_lead='공개자료.')
para('병원 협력. 당직 의료진의 전공·인원, 도착 시 진료 자리·병상, 예정 환자와 수용 응답은 병원 시스템 연계 또는 담당자 입력으로 받는다. 갱신 시각을 기록하고 오래된 값은 재확인한다. 협력 전에는 가상 데이터로 검증한다.', bold_lead='병원 협력.')

heading('6 최적화 문제와 사용 사례')
para('환자별 긴급도·필요 진료과·위치·예상 도착 시각과 병원의 시간대별 의사·진료 자리·병상 수를 입력한다. 환자당 한 병원, 필요한 진료 가능, 도착 시 자원 여유와 중복 배정 금지를 제약으로 둔다. 긴급 환자 미배정, 전체 미배정, 이동·대기시간 순으로 줄이며 병원 응답 후 확정한다.')
para('예를 들어 직접 이동하는 중증 소아와 구급대가 이송하는 중등증 소아가 같은 시간에 접수된다. 가까운 병원은 도착 시 소아과 의사가 없고, 다른 병원은 소아 진료 자리 하나만 남아 있다. 중증 소아를 우선 추천하고 다른 환자는 다음 후보나 상황실로 연결한다. 수용 응답 전에는 확정하지 않는다.')

p = heading('참고자료')
p.paragraph_format.space_before = Pt(6)
linked_reference('[1] 보건복지부·소방청, 응급환자 이송체계 혁신 시범사업 성과, 2026년 6월 22일', 'https://www.korea.kr/news/policyNewsView.do?newsId=148966917')
linked_reference('[2] 보건복지부, 응급환자 이송체계 혁신 시범사업 관련 브리핑, 2026년 2월 25일', 'https://m.korea.kr/briefing/policyBriefingView.do?newsId=156745893')
linked_reference('[3] 국립중앙의료원, 2024 연보, 270쪽부터 271쪽', 'https://test.nmc.or.kr/attach_file/B0000111/202508/7940184db1583e81442f3d0d56a89230.pdf')
linked_reference('[4] 국립중앙의료원, 전국 응급의료기관 정보 조회 서비스', 'https://www.data.go.kr/data/15000563/openapi.do')
linked_reference('[5] 건강보험심사평가원, 의료기관별상세정보서비스', 'https://www.data.go.kr/data/15001699/openapi.do')
linked_reference('[6] 의약뉴스, 의료정책연구원 응급이송 정보시스템 설문 소개, 2025년 6월 4일', 'https://www.newsmp.com/news/articleView.html?idxno=247068')
linked_reference('[7] 중앙일보, 119구급 스마트시스템 병원 응답률 보도, 2026년 7월 28일', 'https://v.daum.net/v/20260728050137144')

for p in doc.paragraphs:
    p.paragraph_format.widow_control = True
    if p.style.name != 'Title':
        for run in p.runs:
            set_font(run)

doc.core_properties.title = '응급환자 진료 가능 병원 연결 시스템 캡스톤 프로젝트 제안서'
doc.core_properties.subject = '캡스톤 디자인 프로젝트 제안서'
doc.save(OUT)
print(OUT)
