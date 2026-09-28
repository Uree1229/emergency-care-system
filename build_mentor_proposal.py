from pathlib import Path

root=Path(__file__).parent
base=(root/'build_proposal.py').read_text(encoding='utf-8')
exec(compile(base.split("d.add_paragraph('응급의료 연계 시스템")[0],str(root/'build_proposal.py'),'exec'))
for name in ['Normal','Title','Subtitle','Heading 1','Heading 2']:
    d.styles[name].paragraph_format.line_spacing=Pt(15.5)
d.styles['Normal'].font.size=Pt(10.5)
d.styles['Normal'].paragraph_format.space_after=Pt(6)
d.styles['Title'].font.size=Pt(21)
d.styles['Title'].paragraph_format.line_spacing=Pt(28)
d.styles['Heading 1'].font.size=Pt(12.5)
d.styles['Heading 1'].paragraph_format.line_spacing=Pt(18)
d.styles['Heading 1'].paragraph_format.space_before=Pt(10)
d.styles['Heading 1'].paragraph_format.space_after=Pt(6)
d.core_properties.title='응급환자 이송 병원 배정 지원 시스템 제안서'
d.core_properties.subject='캡스톤 디자인 멘토 킥오프 미팅 사전 공유'

d.add_paragraph('응급환자 이송 병원\n배정 지원 시스템 제안서','Title')
p('캡스톤 디자인  |  김영선 멘토님 킥오프 미팅 사전 공유  |  2026년 9월')
p('본 프로젝트는 여러 응급환자에게 적합한 병원을 배정하는 의사결정 지원 시스템을 개발한다. 환자별 치료 요구, 병원의 수용 여력과 예상 이동시간을 함께 고려하고, 가까운 병원부터 순차적으로 선택하는 방식과 최적화 방식의 결과를 비교한다. 초기 목표는 가상 상황에서 작동하는 통합 데모와 검증 결과를 만드는 것이다.')

h('1 추진 배경과 해결할 문제')
p('2026년 호남권 이송체계 시범사업에서 광역상황실 처리시간은 중앙값 27분에서 18분으로 줄었지만, 선정 과정에서 평균 6.1개 병원에 문의했다. 이는 모든 환자의 평균 이송시간이 아닌 상황실 지원 지표이다. 병원 선택에는 이동거리 외에도 수용 확인과 조정 시간이 필요함을 보여준다. [1]')
p('국립중앙의료원은 가용병상과 질환별 진료·수술·시술 가능 정보를 구분해 관리한다. 따라서 가까운 병원에 빈 병상이 있다는 사실만으로 환자에게 적합한 병원이라고 판단하기 어렵다. [2]')
p('연구 질문은 “여러 환자의 치료 조건과 공유 자원을 함께 고려한 배정이 개별 순차 배정보다 어떤 조건에서 유리하며, 정보가 늦거나 누락되면 효과가 얼마나 달라지는가?”로 설정한다.')

h('2 개발 범위와 주요 기능')
table(['구성','구현 내용'],[
('환자와 병원 정보','환자 위치·우선순위·치료 요구, 병원별 자원·수용 상태·갱신 시각 관리'),
('병원 후보와 배정','치료 가능한 병원을 선별하고 예상 이동시간과 수용 여력을 반영한 배정안 계산'),
('수용 응답','간편 응답, 불가 사유, 응답 지연 표시와 연결된 재문의'),
('변경 상황 처리','이동 중 환자의 자원 사용을 유지하고 미출발 환자 재계산'),
],[3.5,13.3])
p('초기 사용자는 병원 담당자와 구급대·상황실 담당자로 가정한다. 환자와 구급차가 이미 연결된 상태에서 목적 병원을 결정하는 범위로 한정하며, 실제 배정 확정은 담당자가 확인하도록 설계한다. 병원 응답과 자원 변화는 우선 가상 시나리오로 재현한다.')

h('3 구현 방안')
p('처리 흐름은 환자·병원 정보 입력 → 치료 가능한 병원 선별 → 예상 이동시간 계산 → 배정안 생성 → 병원 응답 반영 → 담당자 확인 및 상태 갱신으로 구성한다.')
p('초기에는 이동시간 행렬로 계산 구조를 검증하고, 이후 도로망을 이동시간 가중 그래프로 구성해 Dijkstra 기반 경로 계산을 연결한다. 배정은 Python과 OR-Tools CP-SAT를 사용해 병상·전문팀·시간 제약을 표현한다. 프런트엔드와 API, 상태 저장소를 연결해 웹 데모로 확장하며 기술 구성은 킥오프에서 조정한다.')

page();h('4 현재 진행 상황과 검증 범위')
table(['항목','현재 상태'],[
('문제 조사','공식 운영 실적과 현장 조사 보도를 정리하고 출처·해석 범위를 구분'),
('배정 실험','우선순위 정책 2종, 환자별 지연 한도, 고정 환자 반영, 초기해 검증·대체 반환 구현'),
('응답 화면','원클릭 수용, 불가 사유, 시간 경고, 동시 문의 1·5·10건의 측정 화면 구현'),
('통합과 현장 검증','화면과 솔버 연결, 도로망 연동, 의료진 검토와 실제 사용성 실험은 후속 단계'),
],[3.5,13.3])
p('알고리즘 단위 테스트 9개를 통과했고, 합성 데이터 실험 40회 모두 관측 정보에 대한 제약을 충족했다. 이 중 24회는 전체 계층 목표의 최적성을 증명했고, 16회는 실행 가능한 해를 확보했다. 그러나 숨겨진 정보 오차를 반영한 24회에서는 계획 제약이 깨져, 입력 정보의 정확성이 여전히 핵심 과제로 남았다.')
p('현재 결과는 소규모 가상 실험이다. 실운영 성능이나 임상적 효과를 입증한 것은 아니며, 우선순위·허용 지연과 화면의 60초 경고·120초 재문의는 실험용 설정으로 다룬다.')

h('5 수행 계획과 평가 방법')
table(['단계','주요 작업과 산출물'],[
('문제와 데이터 확정','대상 지역·환자 유형·필수 제약 정리, 공공데이터 제공 항목과 갱신 조건 확인'),
('통합 데모 구현','응답 화면·API·솔버 연결, 병원 상태와 도착 예정 환자 갱신'),
('비교 실험','동일 입력에서 순차 배정과 솔버 비교, 정보 지연·환자 증가·미응답 조건 반복'),
('평가와 정리','사용성 실험, 결과 분석, 재현 가능한 코드·데모·최종 보고서 작성'),
],[3.5,13.3])
p('알고리즘은 군별 미배정 수, 지연시간, 허용 지연 초과, 계산시간과 재배정 횟수로 평가한다. 사용성은 응답시간 중앙값·P95, 클릭 수, 누락률과 오응답 수로 평가한다. 실제 통계로 확보 가능한 항목은 입력 범위를 보정하고, 확보가 어려운 항목은 가정과 민감도를 명시한다.')

h('6 킥오프에서 논의하고 싶은 사항')
p('① 학기 내 완성할 최소 기능과 우선순위  ② 병원 응답·상태 변경·동시 요청을 처리할 데이터 구조와 API 설계  ③ 실험 규모에 맞는 클라우드 배포와 비용 관리  ④ 실제 운영자료가 제한된 상황에서 설득력 있는 검증 방법')
p('김영선 멘토님의 클라우드 실무 경험을 바탕으로 통합 구조와 개발 범위를 검토받고, 이후 단계별 구현·검증 계획을 구체화하고자 한다.')

h('참고자료')
link('[1] 보건복지부·소방청 2026년 6월 22일  응급환자 이송체계 혁신 시범사업 성과','https://www.korea.kr/news/policyNewsView.do?newsId=148966917')
link('[2] 국립중앙의료원 2024 연보  270쪽부터 271쪽  응급의료자원 관리와 전원 지원','https://test.nmc.or.kr/attach_file/B0000111/202508/7940184db1583e81442f3d0d56a89230.pdf')
for pp in d.paragraphs[-2:]:
    pp.paragraph_format.line_spacing=Pt(13)
    pp.paragraph_format.space_after=Pt(3)
    for rr in pp._p.xpath('.//w:r'):
        rpr=rr.find(qn('w:rPr'))
        if rpr is None:rpr=OxmlElement('w:rPr');rr.insert(0,rpr)
        size=OxmlElement('w:sz');size.set(qn('w:val'),'17');rpr.append(size)

for st in d.styles:
    for border in list(st.element.iter(qn('w:pBdr'))):border.getparent().remove(border)
for pp in list(d.paragraphs)+[pp for t in d.tables for row in t.rows for c in row.cells for pp in c.paragraphs]:
    snap=OxmlElement('w:snapToGrid');snap.set(qn('w:val'),'0');pp._p.get_or_add_pPr().append(snap)
    if not pp.text and not pp._p.xpath('.//w:br') and not pp._p.xpath('.//w:hyperlink'):
        pp.paragraph_format.space_after=Pt(0);pp.paragraph_format.line_spacing=Pt(3)
for t in d.tables:
    for row in t.rows:
        for c in row.cells:
            for pp in c.paragraphs:pp.paragraph_format.line_spacing=Pt(14)
d.save(root/'캡스톤_프로젝트_제안서_멘토사전공유.docx')
print('Mentor proposal created')
