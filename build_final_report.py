"""최종 보고서 뼈대 생성 (T20).

    python build_final_report.py  →  캡스톤_최종보고서_뼈대.docx

- 기능 목록은 build_feature_longlist.py의 DATA를 그대로 읽는다 (항목 수·문구 동기화).
- B0/B1 수치는 simulation_results/summary.json에서 읽는다. 시뮬레이션을 다시 돌리면 이 스크립트도 다시 실행.
- [작성 필요]로 표시한 부분은 팀이 채운다. 상태 판단 근거는 STATUS의 근거 칸에 적었다.
"""
import json
import re
from collections import Counter
from pathlib import Path

from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

ROOT = Path(__file__).parent
DATE = '2026년 10월 1일'

DONE, SERVER, PART, SIM, TODO, TEAM = '완료', '서버 완료·화면 대기', '부분 구현', '가상 검증', '미구현', '미실시'
STATUS_ORDER = [DONE, SERVER, SIM, PART, TODO, TEAM]
STATUS_DESC = {
    DONE: '구현과 자동 테스트로 확인함',
    SERVER: '공통 서버 API와 테스트 완료. 화면은 웹·앱 담당 팀원이 구현 중',
    SIM: '가상 데이터 시뮬레이션·실험으로 확인함. 실제 현장 효과가 아님',
    PART: '일부만 구현했거나 실제 데이터 검증이 남음',
    TODO: '아직 구현하지 않음',
    TEAM: '사람 대상 실험·외부 검토 등으로 아직 진행하지 않음',
}

# ID: (상태, 근거·비고). 화면 항목은 서버 근거만 적고, 화면 완료 시 팀이 '완료'로 바꾼다.
T = 'test_server.py'
STATUS = {
    'P02': (SERVER, 'POST /api/patients reporter_role·relation·contact'),
    'P05': (SERVER, 'consent 없으면 CONSENT_REQUIRED · InputRules'),
    'P07': (SERVER, 'age·symptoms·onset_at·extra'),
    'P09': (PART, '모르겠음은 null 저장, 위치 누락 시 후보 계산 제한. 그 밖의 필수값 확인 규칙은 화면과 함께 정리 필요'),
    'P10': (SERVER, 'danger_signs → call_119'),
    'P12': (SERVER, 'priority_source(self/expert/unknown) 반환'),
    'P13': (SERVER, 'PATCH는 문의 전송 전만 허용 · test_edit_only_before_sending'),
    'P16': (SERVER, 'lat/lon 입력. 위치 권한 처리는 화면'),
    'P17': (SERVER, 'GET candidates'),
    'P18': (SERVER, '후보에 주소·전화·좌표·진료 분야'),
    'P19': (SERVER, 'travel.duration_seconds, last_updated_at'),
    'P20': (SERVER, 'reasons / checks'),
    'P21': (SERVER, 'POST inquiries'),
    'P22': (SERVER, 'inquiry.state'),
    'P23': (SERVER, 'answer API · test_question_answer_and_cancel'),
    'P24': (SERVER, 'elapsed_seconds·overdue·can_retry · test_overdue_and_retry'),
    'P25': (SERVER, 'hospital_phone 제공, tel 링크는 화면'),
    'P26': (SERVER, 'DECLINED + reason'),
    'P27': (SERVER, 'confirm · NormalFlow'),
    'P28': (SERVER, '확정 시 나머지 문의 CANCELLED'),
    'P29': (SERVER, 'inquiry cancel'),
    'P31': (SERVER, 'depart → eta_at'),
    'P32': (SERVER, 'destination 정보'),
    'P33': (SERVER, '좌표 제공, 지도 앱 링크는 화면'),
    'P36': (TODO, '선택 항목. 이송 중 상태 변화 입력 API 없음'),
    'P37': (SERVER, 'notifications(adjustment_needed 등), 주기적 조회'),
    'P38': (SERVER, 'change-destination은 조정 요청+새 수용 확인 필요 · SuddenArrival'),
    'P40': (SERVER, 'arrival_confirmed 알림'),
    'P41': (SERVER, 'cancel, no-show'),
    'H03': (PART, '가상 병원 적재만. 병원 정보 수정 API 없음'),
    'H04': (PART, '가상 데이터의 진료 분야만. 수정 API 없음'),
    'H05': (SERVER, 'PUT resources, expected_version 충돌 검사'),
    'H07': (SERVER, '제한 등록·해제, 종료 예정 경과 시 recheck · test_restriction_and_shortfall_block_accept'),
    'H11': (SERVER, 'stale·age_seconds, 후보 checks에 표시'),
    'H15': (SERVER, 'GET hospital inquiries (open 정렬). prototype/index.html에 독립 응답 화면 시제품 있음'),
    'H16': (SERVER, 'elapsed_seconds·환자 요약·eta_at'),
    'H17': (SERVER, 'patient 요약'),
    'H18': (SERVER, 'priority_source·effective_priority'),
    'H19': (SERVER, "respond action='checking'"),
    'H20': (SERVER, "respond action='question'"),
    'H21': (SERVER, "respond action='accept'"),
    'H22': (SERVER, '불가 사유 필수'),
    'H24': (SERVER, '중복 클릭 duplicate=true · test_duplicate_click_and_conflicting_staff'),
    'H26': (SERVER, 'overdue 정렬'),
    'H28': (SERVER, 'closed_by_patient'),
    'H29': (SERVER, 'GET incoming'),
    'H30': (SERVER, 'state·eta_remaining_seconds'),
    'H32': (TODO, '선택 항목. P36과 함께'),
    'H33': (SERVER, 'external-arrivals → 실제 점유 반영 · SuddenArrival'),
    'H34': (PART, '영향 환자 표시는 수량 기준 1차 판단. 도착 시점별 재평가는 A26 솔버 연결 후'),
    'H35': (SERVER, 'decision keep/adjust'),
    'H36': (SERVER, 'adjustment_needed·hospital_keeps 알림'),
    'H37': (SERVER, 'arrive'),
    'H38': (DONE, '도착 시 예정→실제 점유 전환, 이중 계산 없음 · NormalFlow'),
    'H39': (SERVER, 'no-show, discharge, cancel 시 예정 수요 해제 · test_cancel_releases_expected_demand'),
    'A01': (DONE, '서버 입력 검증(_clean_patient)·솔버 check_input'),
    'A02': (DONE, '자기신고·미확인은 높음으로 처리(effective_priority) · test_consent_and_unknown_answers'),
    'A03': (DONE, '규칙 기반 후보 필터(진료 분야·명시적 제한) · test_no_compatible_hospital'),
    'A05': (PART, '직선거리 보정 추정 완료. 카카오 길찾기 연결 코드는 있으나 키 없어 미검증'),
    'A08': (DONE, '서버 expected_demand, 솔버 blocks·fixed'),
    'A10': (DONE, 'empty_reason·excluded.reason'),
    'A11': (SIM, '시뮬레이터 B0(최근접 순차)로 구현 · simulation/sim.py'),
    'A12': (SIM, '솔버 greedy 초기해, 시뮬레이터 B1'),
    'A13': (SIM, 'solver_improved.py CP-SAT. 40회 가상 실험'),
    'A14': (SIM, '솔버 제약'),
    'A15': (SIM, '솔버 진료 자리·특수팀 2자원 누적 제약'),
    'A16': (SIM, '솔버 제약'),
    'A17': (SIM, 'severity_first / total_first 비교'),
    'A18': (SIM, '최초 release+deadline 유지'),
    'A20': (SIM, '초기해 재검증'),
    'A21': (SIM, '시간 제한·대체 반환. 전체 작업 시간 엄격 보장은 아님'),
    'A22': (SIM, 'OPTIMAL/FEASIBLE 구분'),
    'A23': (PART, '솔버 commit_allowed 버전 검사(로컬). 서버 연결 시 DB 버전 비교 필요'),
    'A24': (PART, '솔버 재실행 가능. 서버 사건과 자동 연결은 S12 이후'),
    'A25': (SIM, '이동 중 환자 fixed 유지'),
    'A26': (TODO, '현재 병원·시작시각 모두 고정. 시작시각 재평가 분리 필요(솔버 담당 우선 과제)'),
    'A27': (PART, '서버는 조정 필요 상태를 다룸. 솔버의 조정 필요 반환은 A26과 함께'),
    'A28': (TODO, '선택 항목'),
    'A30': (TODO, '선택 항목. 시뮬레이터에 정책 추가로 실험 가능'),
    'S01': (DONE, 'server/app.py · test_http_roundtrip'),
    'S02': (DONE, 'server/db.py 스키마'),
    'S03': (DONE, '상태 전이와 INVALID_STATE · test_server 전반'),
    'S05': (DONE, 'BEGIN IMMEDIATE, 마지막 자리 동시 확정 시 1명만 성공 · test_two_patients_last_bed_only_one_confirms'),
    'S06': (DONE, '(환자, 병원) UNIQUE 재사용 · test_resend_does_not_duplicate'),
    'S07': (DONE, '확정 1곳, 나머지 종료'),
    'S08': (DONE, '수용 응답은 점유 아님, 확정만 예정 수요 · NormalFlow'),
    'S11': (TODO, '조회 시점 만료 처리만 있음. 주기 타이머 없음'),
    'S12': (TODO, '서버와 솔버 미연결'),
    'S15': (DONE, 'events 테이블, GET /api/events'),
    'S16': (PART, '가상 병원만 적재. 공공 API 실응답 수집 도구(probe_apis.py)는 있으나 인증키 없어 미실행'),
    'O03': (PART, 'API 키는 환경변수로 분리. HTTPS는 배포 후'),
    'O11': (TODO, '클라우드 미정'),
    'T01': (DONE, 'server/synthetic.py, simulation/sim.py make_world'),
    'T02': (DONE, 'test_inquiry_to_arrival_and_discharge'),
    'T03': (DONE, 'Exceptions 테스트 7개'),
    'T04': (DONE, 'Concurrency 테스트 3개'),
    'T05': (DONE, 'SuddenArrival 테스트, 시연 시나리오 --scenario sudden'),
    'T06': (PART, '적합 병원 없음 테스트, 시뮬레이터 의료공백지역 10%. 소아·공백지역 전용 시나리오 표 미작성'),
    'T07': (PART, '오래된 정보 표시만. 연결 실패 테스트 없음'),
    'T08': (SIM, '솔버 validate, 시뮬레이터 용량 불변조건 · test_simulation'),
    'T09': (SIM, '작은 사례 전수열거와 솔버 최적값 일치'),
    'T10': (PART, 'B0/B1 비교 완료. 솔버(B2) 연결 전'),
    'T11': (SIM, '군별 미치료·허용 시간 초과·최대 대기 (B0/B1)'),
    'T12': (SIM, '목적지 변경 건수·늘어난 도착시각 (B1)'),
    'T13': (PART, '솔버 실험의 상태별 결과 있음. 계산시간·대체 반환률 표 정리 필요'),
    'T14': (TEAM, '측정 도구(prototype)는 있음. 사람 대상 실험 미실시'),
    'T15': (TEAM, 'prototype 요약 지표(중앙값·P95·클릭·오응답) 구현, 실험 미실시'),
    'T16': (TEAM, '선택 항목'),
    'T17': (TEAM, '선택 항목. 의료진 검토 필요'),
    'T18': (PART, 'API 명세(docs/API.md)·DB 구조(server/db.py) 완료. 요구사항 명세·화면 설계는 화면 팀과 작성'),
    'T19': (PART, '실행 안내·실험 결과 완료. 시연 영상은 화면 완성 후'),
    'T20': (PART, '이 문서'),
}


def load_longlist():
    s = (ROOT / 'build_feature_longlist.py').read_text(encoding='utf-8')
    data = re.search(r"DATA='''(.*?)'''", s, re.S).group(1)
    excluded = re.search(r'EXCLUDED=\[(.*?)\n\]', s, re.S).group(1)
    groups = []
    for line in data.strip().splitlines():
        if line.startswith('#'):
            groups.append((line[1:].strip(), []))
        else:
            bits = line.split('|')
            groups[-1][1].append((bits[0], bits[1], bits[2] if len(bits) > 2 else '핵심'))
    return groups, re.findall(r"\('([^']+)','([^']+)'\)", excluded)


# ---------------------------------------------------------------- 문서 서식

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
    d.styles['Title'].font.size = Pt(22)
    d.styles['Heading 1'].font.size = Pt(15)
    d.styles['Heading 2'].font.size = Pt(12)
    for name in ('Heading 1', 'Heading 2'):
        d.styles[name].paragraph_format.keep_with_next = True
    for st in d.styles:
        for b in list(st.element.iter(qn('w:pBdr'))):
            b.getparent().remove(b)
    d.core_properties.title = '응급의료시스템 최종 보고서'
    d.core_properties.author = ''
    return d


def todo(d, text):
    p = d.add_paragraph()
    r = p.add_run(f'[작성 필요] {text}')
    r.font.color.rgb = RGBColor(0xC0, 0x39, 0x2B)
    return p


def bullets(d, items):
    for it in items:
        d.add_paragraph(it, style='List Bullet')


def table(d, header, rows, widths, center=()):
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
        for j, (c, w) in enumerate(zip(row.cells, widths)):
            c.width = Cm(w)
            c.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            if i == 0:
                sh = OxmlElement('w:shd')
                sh.set(qn('w:fill'), 'E7EDF3')
                c._tc.get_or_add_tcPr().append(sh)
            for p in c.paragraphs:
                p.paragraph_format.space_after = Pt(0)
                p.paragraph_format.keep_with_next = i == 0   # 머리줄이 쪽 끝에 홀로 남지 않게
                if j in center:
                    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                for r in p.runs:
                    r.font.size = Pt(9)
                    r.bold = i == 0
    d.add_paragraph().paragraph_format.space_after = Pt(2)
    return t


# ---------------------------------------------------------------- 본문

def main():
    groups, excluded = load_longlist()
    all_ids = [r[0] for _, rows in groups for r in rows]
    missing = set(all_ids) - set(STATUS)
    extra = set(STATUS) - set(all_ids)
    assert not missing and not extra, f'상태 누락 {sorted(missing)} / 롱리스트에 없음 {sorted(extra)}'
    counts = Counter(STATUS[i][0] for i in all_ids)
    sim = json.loads((ROOT / 'simulation_results' / 'summary.json').read_text(encoding='utf-8'))

    d = setup()
    d.add_paragraph('응급의료시스템\n응급환자 병원 연결 서비스 최종 보고서', 'Title')
    d.add_paragraph(f'캡스톤 디자인 · 초안 뼈대 · {DATE}')
    todo(d, '팀명, 팀원, 지도교수, 멘토')
    d.add_paragraph('이 문서는 최종 보고서의 뼈대다. 구현 상태와 검증 수치는 저장소 코드·테스트·실험 결과에서 자동으로 채웠고, '
                    '빨간 [작성 필요] 부분은 팀이 작성한다. 모든 데이터는 가상이며, 결과는 임상 현장 효과가 아니라 '
                    '정의된 가정 아래의 가상 검증이다.')

    d.add_heading('1. 요약', 1)
    todo(d, '문제, 해결 방식, 주요 결과를 5줄 이내로. 아래 현재 상태 수치를 근거로 사용')
    total = len(all_ids)
    d.add_paragraph(f'구현 범위 {total}개 항목의 현재 상태(작성일 기준):')
    table(d, ['상태', '항목 수', '의미'], [[s, counts.get(s, 0), STATUS_DESC[s]] for s in STATUS_ORDER],
          [3.6, 1.8, 11.6], center=(1,))

    d.add_heading('2. 문제 정의와 목표', 1)
    todo(d, '제안서(캡스톤_프로젝트_제안서_병원응답보강_20260923.docx)의 문제 상황을 1쪽 이내로 요약')
    bullets(d, [
        '병원 탐색 과정에서 순차 문의와 응답 지연이 반복된다. 표시된 빈 병상과 환자별 실제 수용 가능 여부가 다를 수 있다.',
        '이동 중 시스템 밖 환자가 유입되면 도착 시점의 수용 가능성이 달라진다.',
        '목표: 환자별 수용 문의·응답을 구조화하고, 이동시간과 자원 점유를 고려해 배정안을 제안하며, '
        '상황 변화 시 영향받는 환자를 알린다. 최종 확정은 병원의 환자별 수용 응답으로 한다.',
    ])

    d.add_heading('3. 시스템 구성', 1)
    table(d, ['구성 요소', '역할', '위치', '상태'], [
        ['환자·보호자용 모바일 웹', '환자 정보 입력, 후보 확인, 문의, 이동, 알림', '팀원 담당', '구현 중'],
        ['병원용 웹', '자원·제한 관리, 문의 응답, 도착 예정·돌발 유입', '팀원 담당 (prototype/ 시제품)', '구현 중'],
        ['공통 서버', '상태 전이, 원자적 확정, 이력, 후보 필터', 'server/', '완료(가상 데이터)'],
        ['배정 솔버', 'CP-SAT 자원 일정 배정, 계층 목적함수', 'solver_improved.py', '가상 실험 완료, 서버 미연결'],
        ['비교 시뮬레이터', 'B0/B1(/B2) 짝지은 비교', 'simulation/', 'B0·B1 완료'],
        ['데이터', '가상 병원·환자, 공공 API 수집 도구', 'server/synthetic.py, server/probe_apis.py', '실응답 미수집'],
    ], [3.4, 6.2, 4.4, 3.0])
    todo(d, '구성도 그림 (화면 ↔ 서버 ↔ 솔버, 공공 API 캐시)')
    d.add_paragraph('API 명세는 docs/API.md, 서버 설계와 상태 흐름은 server/README.md를 따른다.')

    d.add_heading('4. 데이터 확보', 1)
    table(d, ['필요한 정보', '확보 방향', '현재'], [
        ['병원 위치·진료과·일부 가용 자원·중증질환 수용', '국립중앙의료원 API', '수집 도구 완료, 인증키 대기'],
        ['시설·장비·전문과목별 전문의 수', '심평원 API', '수집 도구 완료, 인증키 대기'],
        ['병원까지 이동시간', '카카오 길찾기 또는 합성 추정', '합성 추정 사용, 길찾기 미검증'],
        ['당직 인력·치료 종료 예정·환자별 수용 확답', '병원 입력 또는 가상 데이터', '가상 데이터'],
        ['도착 시점 수용 가능성', '위 정보로 계산한 추정값', '모델 출력(원천 데이터 아님)'],
    ], [6.0, 5.0, 6.0])
    d.add_paragraph('전문의 수는 현재 가용 인력이 아니고, 현재 병상 정보는 도착 시점 수용의 보장이 아니다. '
                    '원본 필드 매핑은 api_field_mapping.md에 실응답 확인 후 채운다. 상세 근거는 데이터확보_조사_20260930.md.')
    todo(d, '인증키 발급 후 실응답 필드 결측률·갱신 지연 측정 결과')

    d.add_heading('5. 배정 알고리즘', 1)
    d.add_paragraph('후보 필터링 → 병렬 수용 문의 → 유효한 수용 응답 확인 → 이동시간·자원 점유를 고려한 CP-SAT 배정 → '
                    '상황 변화 시 재계산. 목적함수는 우선순위군별 미배정 수 다음 대기시간을 사전식으로 최소화한다(severity_first). '
                    '상세는 배정알고리즘_구체화_20260930.md.')
    todo(d, '수학 모델(변수·제약·목적함수) 정리. A26(이동 중 병원 고정, 치료 시작시각 재평가) 구현 후 반영')

    d.add_heading('6. 구현 범위와 상태', 1)
    d.add_paragraph('롱리스트 ID별 상태다. 근거 칸의 테스트 이름은 test_server.py, test_simulation.py, test_solver_improved.py에 있다. '
                    '화면이 완성되면 "서버 완료·화면 대기" 항목을 화면 확인 후 "완료"로 바꾼다.')
    for title, rows in groups:
        d.add_heading(title, 2)
        table(d, ['ID', '기능', '구분', '상태', '근거·비고'],
              [[i, name, kind, STATUS[i][0], STATUS[i][1]] for i, name, kind in rows],
              [1.2, 5.2, 1.1, 3.3, 6.2], center=(0, 2, 3))

    d.add_heading('7. 검증 결과', 1)
    d.add_heading('7.1 서버 업무 규칙 (자동 테스트)', 2)
    table(d, ['시험', '확인한 내용', '테스트'], [
        ['T02 정상 흐름', '문의 → 수용 → 확정 → 출발 → 도착 → 퇴실, 예정·실제 점유 이중 계산 없음', 'NormalFlow'],
        ['T03 예외', '불가 사유 필수, 미응답 강조·재문의 간격, 추가 질문, 만료 응답 확정 차단, 제한·자원 부족 시 수용 차단, 취소 시 예정 수요 해제',
         'Exceptions'],
        ['T04 동시성', '중복 클릭, 담당자 충돌, 마지막 자리 동시 확정 시 1명만 성공, 재전송 중복 방지', 'Concurrency'],
        ['T05 돌발 유입', '이동 중 A + 미등록 B → A 영향 표시, 자동 변경 없음, 조정 요청과 새 수용 확인 후에만 변경', 'SuddenArrival'],
    ], [3.0, 10.4, 3.6])
    d.add_heading('7.2 배정 솔버 가상 실험', 2)
    d.add_paragraph('experiment_improved.py 40회: 24 OPTIMAL / 16 FEASIBLE. 모두 관측 입력 기준 유효했고 초기 순차 배정보다 '
                    '목적값이 나빠지지 않았다. 작은 사례에서 전수열거 최적값과 일치했다(T09). 단, 정보 오차 3조건의 24회는 '
                    '숨겨진 실제 상태에서 제약이 깨졌으며 이를 해결했다고 주장하지 않는다.')
    todo(d, '계산시간 분포, 대체 반환률 표(T13)')
    d.add_heading('7.3 문의 방식 비교 시뮬레이션 (B0 vs B1)', 2)
    seeds = sim['seeds']
    d.add_paragraph(f'seed {len(seeds)}개 짝지은 비교. B0: 순차 문의 + 최근접 수용 병원. B1: 상위 {sim["config"]["k_parallel"]}곳 '
                    '병렬 문의 + 먼저 수용한 병원 + 상황 변경 알림. 차이 = B1 − B0. 분포·비율은 모두 실험 가정이다.')
    keys = ['mean_wait', 'p90_wait', 'g4.over_allowance', 'time_to_commit', 'inquiries', 'redirects']
    base = sim['results']['기본 설정']
    table(d, ['지표', 'B0', 'B1', '차이 평균', 'B1이 나은 seed'],
          [[base[k]['label'], f"{base[k]['b0_mean']:.1f}", f"{base[k]['b1_mean']:.1f}", f"{base[k]['diff_mean']:+.1f}",
            f"{base[k]['b1_better_share']:.0%}"] for k in keys], [6.4, 2.2, 2.2, 2.6, 3.6], center=(1, 2, 3, 4))
    rows = []
    for title, res in sim['results'].items():
        rows.append([title, f"{res['mean_wait']['diff_mean']:+.1f}", f"{res['p90_wait']['diff_mean']:+.1f}",
                     f"{res['time_to_commit']['diff_mean']:+.1f}", f"{res['inquiries']['diff_mean']:+.1f}"])
    d.add_paragraph('민감도: 조건별 차이 평균(B1 − B0)')
    table(d, ['조건', '평균 대기', '대기 90백분위', '확정까지', '문의 건수'], rows, [4.4, 3.0, 3.2, 3.0, 3.4],
          center=(1, 2, 3, 4))
    bullets(d, [
        '병렬 문의는 목적지 확정까지의 시간을 모든 seed에서 줄였지만, 병원 문의 건수가 약 2배 이상 늘었다.',
        '미응답이 많을수록 병렬 문의의 이득이 커졌다. 미응답이 적으면 먼저 수용한 먼 병원을 고르는 탐욕 방식 때문에 평균 대기가 오히려 늘 수 있다. '
        '이 차이가 솔버 배정(B2)이 개선해야 할 지점이다.',
        '병원 응답 지연은 두 방식이 같다고 가정했다. 병렬 문의로 늘어나는 병원 부담이 응답을 늦추는 효과는 반영하지 않았다.',
    ])
    todo(d, 'B2(솔버) 연결 후 3군 비교 결과 추가 (simulation/sim.py SolverPolicy)')
    d.add_heading('7.4 사용성 실험', 2)
    todo(d, 'T14·T15: 병원 담당자 역할 5명 이내, 동시 문의 1·5건. 응답시간 중앙값·P95·클릭·누락·오응답 (prototype/ 측정 도구 사용)')

    d.add_heading('8. 한계', 1)
    bullets(d, [
        '모든 병원·환자·자원 데이터는 가상이다. 공공 API 실응답과 실제 병원 협력 검증은 하지 않았다.',
        '우선순위, 허용 대기시간, 응답 유효시간 등은 실험 설정이며 의료전문가 합의 기준이 아니다.',
        '돌발 유입 시 영향 환자 판단은 현재 수량 기준이다. 도착 시점별 치료 시작시각 재평가(A26)는 미구현이다.',
        '로그인·접근 통제 없이 병원 ID 경로로 구분한다. 실제 운영에는 인증·권한 체계가 필요하다.',
        '시뮬레이션은 도착 후 자리가 없을 때의 재이송을 모델링하지 않았다.',
    ])
    todo(d, '팀이 추가로 확인한 한계')

    d.add_heading('9. 향후 과제', 1)
    pending = [i for i in all_ids if STATUS[i][0] in (TODO, PART)]
    d.add_paragraph('범위 안의 미구현·부분 구현: ' + ', '.join(pending))
    d.add_paragraph('범위에서 제외한 항목:')
    bullets(d, [f'{area}: {ids}' for area, ids in excluded])

    d.add_heading('부록. 실행 방법', 1)
    for line in ['python -m server.app --seed --scenario sudden   # 시연 서버(가상 병원 10곳 + 시나리오)',
                 'python -m unittest test_server test_simulation -v',
                 'python -m simulation.run --sensitivity          # B0/B1 비교 → simulation_results/',
                 'python build_final_report.py                    # 이 보고서 다시 생성']:
        p = d.add_paragraph(line)
        for r in p.runs:
            r.font.name = 'Consolas'
            r.font.size = Pt(9)
    todo(d, '시연 영상 링크, 저장소 주소')

    footer = d.sections[0].footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    fld = OxmlElement('w:fldSimple')
    fld.set(qn('w:instr'), 'PAGE')
    footer._p.append(fld)
    out = ROOT / '캡스톤_최종보고서_뼈대.docx'
    d.save(out)
    print(out.name, dict(counts))


if __name__ == '__main__':
    main()
