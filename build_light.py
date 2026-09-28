from pathlib import Path

root = Path(__file__).parent
base = (root / 'build_proposal.py').read_text(encoding='utf-8')
exec(compile(base.split("d.add_paragraph('응급의료 연계 시스템")[0], str(root / 'build_proposal.py'), 'exec'))

d.core_properties.title = '응급의료 연계 시스템 캡스톤 디자인 제안서'
d.styles['Title'].font.size = Pt(21)
d.styles['Normal'].paragraph_format.line_spacing = 1.15
d.styles['Normal'].paragraph_format.space_after = Pt(6)
d.styles['Heading 1'].font.size = Pt(13)
d.styles['Heading 1'].paragraph_format.space_before = Pt(10)
d.styles['Heading 1'].paragraph_format.space_after = Pt(6)

d.add_paragraph('응급의료 연계 시스템\n캡스톤 디자인 제안서', 'Title')
p('병원 수용 확인과 도착 예정 환자 정보 공유')

h('1 프로젝트 개요')
p('환자와 보호자가 주변 병원을 확인하고, 환자 정보를 전달해 병원의 수용 여부를 확인할 수 있는 웹 서비스를 개발하고자 한다. 병원은 문의에 응답하고 도착 예정 환자를 한 화면에서 확인한다. 강원권 일부 지역을 대상으로 가상 환자와 병원 응답을 활용한 시제품을 구현한다.')

h('2 제안 배경')
p('병원을 찾을 때 거리나 빈 병상 정보만으로는 해당 환자를 수용할 수 있는지 판단하기 어렵다. 병원의 확인을 받는 과정과 환자의 이동 정보를 연결하면, 사용자는 문의 결과를 확인하기 쉽고 병원은 도착 전 환자 정보를 파악할 수 있다.')
p('본 프로젝트는 병원 조회에서 수용 문의, 응답 확인, 도착 예정 정보 공유까지 이어지는 흐름을 구현하는 데 초점을 둔다.')

h('3 주요 기능')
table(['기능', '주요 내용'], [
    ('주변 병원 조회', '지도에서 병원 위치와 진료 정보, 수용 상태의 확인 시각을 조회한다.'),
    ('환자 정보와 수용 문의', '연령, 주요 증상, 발생 시각 등을 입력해 병원에 전달한다.'),
    ('병원 응답', '병원 담당자가 수용 가능, 조건부 가능, 수용 불가로 응답한다.'),
    ('도착 예정 환자 관리', '수용 확인 후 이동하는 환자의 도착예정시간과 도착·취소 상태를 공유한다.')
], [4.2, 12.6])

h('4 이용 흐름')
p('환자 정보 입력 → 병원 후보 조회 → 수용 문의 → 병원 응답 확인 → 이동 및 도착 상태 공유')
p('병원 화면에는 접수된 문의와 도착 예정 환자를 표시한다. 응답이 없는 경우는 미확인 상태로 유지하고, 조건부 응답은 추가 확인이 필요한 것으로 구분한다.')

page()
h('5 구현 계획')
p('사용자용 모바일 화면과 병원 담당자용 PC 화면을 제공하는 반응형 웹으로 제작한다. 화면은 React, 서버는 Python FastAPI, 데이터 저장은 PostgreSQL을 활용하는 방향으로 검토한다.')
p('병원 위치와 기초 정보는 공개 API 활용을 검토하고, 환자 정보와 병원 수용 응답은 가상 데이터 및 테스트 참여자의 입력으로 구성한다. 실제 병원 시스템 연계 없이도 전체 이용 흐름을 시연할 수 있도록 개발한다.')
p('필수 구현 범위는 병원 조회, 수용 문의와 응답, 도착 예정 환자 관리까지로 정한다. 향후 수요 예측과 다수환자 배정은 기본 기능 완성 후 확장 과제로 검토한다.')

h('6 개발 일정')
table(['기간', '개발 내용'], [
    ('1~2주', '요구사항 정리, 화면 설계, 가상 데이터 준비'),
    ('3~6주', '병원 지도 조회, 환자 입력, 수용 문의와 응답 구현'),
    ('7~9주', '도착 예정 환자 관리, 상태 변경과 알림 연결'),
    ('10~12주', '통합 테스트, 사용성 개선, 시연 및 최종 보고서 작성')
], [3.1, 13.7])
p('위 일정은 12주 기준의 예시이며 학기 일정과 팀 구성에 맞춰 조정한다.')

h('7 검증 방법과 기대 효과')
p('가상 환자 시나리오로 문의부터 도착 처리까지 정상적으로 이어지는지 확인한다. 병원 응답 지연, 문의 취소, 동시 요청도 점검하고 수용 확인 소요시간, 문의 횟수, 중복 확정 여부를 측정한다.')
p('사용자가 병원 확인 결과를 쉽게 파악하고, 병원이 도착 예정 환자를 미리 확인할 수 있는 시제품을 목표로 한다. 최종 산출물은 사용자·병원용 웹 화면, 시연 시나리오, 테스트 결과를 포함한 보고서로 구성한다.')

h('8 적용 범위')
p('본 시제품은 정보 공유와 수용 확인 절차를 검증하기 위한 것으로, 진단이나 119의 판단을 대체하지 않는다. 병원의 수용 응답이 즉시 치료를 보장하는 것은 아니며, 실제 서비스 적용에는 병원 협력과 추가 검증이 필요하다. 개발과 시연에는 가상 환자 정보를 사용한다.')

for st in d.styles:
    for border in list(st.element.iter(qn('w:pBdr'))):
        border.getparent().remove(border)
for pp in list(d.paragraphs) + [pp for t in d.tables for row in t.rows for c in row.cells for pp in c.paragraphs]:
    snap = OxmlElement('w:snapToGrid')
    snap.set(qn('w:val'), '0')
    pp._p.get_or_add_pPr().append(snap)
    if not pp.text and not pp._p.xpath('.//w:br'):
        pp.paragraph_format.space_after = Pt(0)
        pp.paragraph_format.line_spacing = Pt(3)

out = root / '응급의료_연계_시스템_제안서_제출용.docx'
d.save(out)
print(out)
