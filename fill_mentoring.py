from pathlib import Path
from zipfile import ZipFile
from lxml import etree as E
from copy import deepcopy
src=Path(r'C:/Users/PC/Downloads/캡스톤 디자인 멘토링 보고서 양식.docx')
out=Path('캡스톤_멘토링_보고서_작성본.docx')
ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
w=lambda t:'{'+ns['w']+'}'+t
content={3:[
'응급실 재이송 원인을 전문의·수술실·중환자실 부족, 과밀, 정보 불일치, 개별 전화 문의 및 병원 간 전원 문제로 구분하고, 해결할 문제를 구체화할 필요가 있음.',
'수용 가능한 병원과 빠르게 도착할 수 있는 병원을 함께 고려하며, 출발 시점과 도착 시점의 자원 차이를 논의함.',
'일반인이 설문으로 환자 상태를 정확하게 전달할 수 있는지와 입력 정보의 정확성을 보완할 방법을 논의함.',
'공공데이터포털 API의 실제 반환 항목을 우선 확인하고, API 정보와 병원 문의를 함께 활용하는 방안을 검토함.',
'새로운 환자의 병원 도착에 따른 정보 갱신 시점과 이동 중 환자에게 미치는 영향을 논의함.',
'가상 데이터로 앱 사용 여부에 따른 시간을 비교하는 방안과 환자용·병원용 UI, 기능 목록, 알고리즘, 역할 분담 및 서비스 명칭을 논의함.'
],4:[
'문제 정의: 재이송과 병원 선정 지연의 원인을 조사하고, 자원 부족 자체와 정보·문의 과정의 문제를 구분하여 개선 범위를 정할 계획임.',
'API 확인: 반환 항목과 갱신 주기를 조사하여 확보 가능한 정보와 병원 문의 또는 가상 데이터로 보완할 정보를 정리할 계획임.',
'환자 정보: 일반인이 이해할 수 있는 증상 설문과 미확인 응답을 설계하고, 입력 정확성의 한계 및 의료진 확인이 필요한 항목을 검토할 계획임.',
'선정 및 갱신: 이동시간과 도착 시점의 예상 자원을 함께 고려하고, 신규 환자 유입 시 정보 갱신 및 수용 여부 재확인 절차를 설계할 계획임.',
'평가: 환자 상태·병원 자원·이동시간·신규 유입을 포함한 가상 데이터를 구성하고, 동일 조건에서 앱 사용 여부에 따른 선정 및 이송 소요시간을 비교할 계획임.',
'구현 준비: 환자용 앱과 병원용 웹의 화면 흐름 및 기능 목록을 작성하고, 알고리즘·데이터·화면 개발 역할을 분담할 계획임. 서비스 명칭은 추가 논의할 예정임.'
]}
with ZipFile(src) as z:
    original=z.read('word/document.xml'); root=E.fromstring(original)
    tables=root.findall('w:body/w:tbl',ns)
    baseline=deepcopy(root)
    for idx,items in content.items():
        cell=tables[idx].findall('w:tr',ns)[1].find('w:tc',ns)
        template=deepcopy(cell.find('w:p',ns))
        for p in list(cell.findall('w:p',ns)):cell.remove(p)
        for text in items:
            p=deepcopy(template)
            for ch in list(p):
                if ch.tag!=w('pPr'):p.remove(ch)
            r=E.SubElement(p,w('r'));E.SubElement(r,w('t')).text=text
            cell.append(p)
    payload=E.tostring(root,encoding='UTF-8',xml_declaration=True,standalone=True)
    with ZipFile(out,'w') as target:
        for info in z.infolist():target.writestr(info,payload if info.filename=='word/document.xml' else z.read(info.filename))
with ZipFile(src) as a,ZipFile(out) as b:
    assert all(a.read(n)==b.read(n) for n in a.namelist() if n!='word/document.xml')
    for idx in (3,4):
        for tree in (baseline,root):
            cell=tree.findall('w:body/w:tbl',ns)[idx].findall('w:tr',ns)[1].find('w:tc',ns)
            for p in list(cell.findall('w:p',ns)):cell.remove(p)
    assert E.tostring(baseline)==E.tostring(root)
Path('.docx_qa_mentoring').mkdir(exist_ok=True)
print('Only two mentoring content cells changed; other package parts preserved.')
