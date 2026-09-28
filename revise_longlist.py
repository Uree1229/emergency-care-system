from pathlib import Path
p=Path('build_feature_longlist.py');s=p.read_text(encoding='utf-8')
rep={
'질문별 모르겠음 및 미입력 상태 처리':'모르겠음·미입력 상태 처리와 필수값 누락 시 확인 요청·계산 제한',
'마지막 갱신 시각과 오래된 정보 경고':'마지막 갱신 시각 표시, 누락·오래된 정보를 확인 필요로 처리 (A04 통합)',
'병원 수용 상황 변경 알림 확인':'주기적 조회로 병원 수용 상황 변경 알림 확인',
'이송자에게 상황 변경 통보 및 연락':'수용 상황 변경 저장·환자 웹 알림 표시와 연락 안내',
'O01(병원별 고유 URL로 단순화)':'O01(실제 운영용 접근 통제는 향후 과제)',
'A04(H11 경고로 대체)':'A04',
'구현 기능 목록 (범위 확정본)':'구현 기능 롱리스트',
'2026년 9월 27일':'2026년 9월 28일',
'핵심은 최종 시연과 알고리즘 평가에 반드시 필요한 기능, 선택은 핵심 기능 완료 후 일정 여유가 있을 때 추가하는 기능이다.':'핵심은 주요 서비스 흐름과 알고리즘 검증에 필요한 기능, 선택은 핵심 완료 후 일정에 따라 추가할 기능이다. 핵심 100개 전체가 첫 시연 대상은 아니며, 상태 칸에 첫 시연 / 이후 구현과 계획 / 진행 / 검증 / 완료를 함께 기록한다.',
'앱 배포·설치, 권한 처리, 푸시 알림 부담을 없애고':'앱 설치 없이 이용하며, 위치 권한 거절 시 직접 입력을 제공한다. 전화·길찾기 연결은 단순화하여',
'로그인이 없으므로 병원별 접근은 병원별 고유 URL로 구분한다. 이는 시연용 단순화이며 실제 운영 시 계정·권한 체계가 필요함을 보고서에 명시한다.':'가상 데이터 시연에서는 로그인 없이 병원별 고유 URL로 접속한다. URL 구분은 접근 통제를 대신하지 않으며, 실제 운영용 인증·권한 체계는 별도 과제로 둔다.',
'병원 기본정보는 공공데이터를 1회 적재해 사용하고, 이동시간은 직선거리 보정값 또는 지도 API로 계산한다.':'병원 기본정보는 공공데이터를 1회 적재하고, 환자·자원 변화는 가상 데이터로 구성한다. 이동시간은 지도 API 또는 실험용 추정값을 사용하며 추정 방식과 한계를 명시한다. 초기에는 필요한 진료과와 중증도가 입력된 것으로 가정한다.',
"d.add_heading('최종 시연의 흐름',1)":"d.add_heading('첫 통합 시연의 흐름',1)",
'환자 정보 입력 → 후보 병원 및 배정안 계산 → 수용 문의 → 병원 웹 응답 → 목적지 확인 → 이동 시작 → 병원 도착 확인':'환자 정보 입력 → 후보 병원·배정안 계산 → 수용 문의·응답 → 목적지 확정·이동 → 미등록 환자 유입 → 예상 치료 시작시각·수용 가능 여부 재평가 → 변경 알림 → 도착 확인',
'이 시나리오가 서비스의 차별점이므로 일정이 부족해도 제외하지 않는다.':'A26·H33~H35·T05와 변경 알림(P37·H36)은 핵심 시나리오이므로 유지한다. P09의 누락 처리와 T12의 목적지 변경·추가 이동시간 평가도 핵심으로 유지한다.',
}
for a,b in rep.items():
    assert a in s,a
    s=s.replace(a,b)
s=s.replace("d.add_heading('설계 기준',1)","d.add_paragraph('일정이 부족하면 중복 정보 표시와 중간 상태를 단순화하고, 추가 시나리오 및 사용성 실험 규모를 조정한다. 동시 수용 충돌 방지와 자원 제약 검증은 유지한다. 핵심을 선택으로 변경해도 전체 항목 수는 줄지 않으므로 분류 변경 시 개수를 다시 집계한다.')\nd.add_heading('설계 기준',1)")
s=s.replace("assert len({r[0] for r in all_rows})==len(all_rows)","assert len(all_rows)==112 and len({r[0] for r in all_rows})==112\nassert Counter(r[2] for r in all_rows)=={'핵심':100,'선택':12}")
s += '''\n# Export the same revised longlist as readable plain text.\nfrom docx.oxml.ns import qn as _qn\nlines=[]\nfor el in d.element.body:\n    if el.tag==_qn('w:p'):\n        lines.append(''.join(el.itertext()) if False else ''.join(t.text or '' for t in el.iter(_qn('w:t'))))\n    elif el.tag==_qn('w:tbl'):\n        for row in el.findall(_qn('w:tr')):\n            vals=[''.join(t.text or '' for t in cell.iter(_qn('w:t'))) for cell in row.findall(_qn('w:tc'))]\n            lines.append(' | '.join(vals))\n        lines.append('')\n(ROOT/'캡스톤_전체구현기능_롱리스트.txt').write_text('\\n'.join(lines),encoding='utf-8-sig')\n'''
p.write_text(s,encoding='utf-8')
