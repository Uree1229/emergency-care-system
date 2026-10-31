# 문서 안내

팀원 음성 시험: [긴 문장52개·실행·녹음/결과 보관 방법](TEAM_VOICE_TEST_GUIDE_20261031.md). 최신 main에서 음성 전용 서버를 실행하며, 긴 환자 보고12개와 명령 연습40개를 분리해 시험한다.

최근 완료: [메인 음성·이송과 검토·추천·중요 알림 연결](MAIN_UI_INTEGRATION_20261030.md). 관련 101개 회귀와 합성 환자의 등록→같은 ID 도착·명시 적용·알림 기록을 확인했다.

현재 잔여 작업: [최종발표 제외 — 실제 기기·사용성·공유 실행본](CURRENT_REMAINING_WORK_20261030.md). 1·2번은 로컬 구현·합성 검증 완료로 갱신했고 실제 마이크·휴대폰·사람 측정은 남아 있다.

현재 작업: [시연 안내](DEMO_RUNBOOK_20261030.md), [정상·변경·실패와 연구 사례 안내 화면](http://127.0.0.1:18045/demo.html). 알고리즘 추가 개선은 종료하고 운영 고정3 문의·CP-SAT을 유지한다. 최종발표 제작은 보류했다.

직전 연구: [대기 보호 W1 구현·기존 36/새 60환경 검증과 미채택 근거](WAITING_PROTECTION_20261030.md). 한 67분 지연을 줄였지만 다른 환자는 최대 83분 지연됐고, 새 987명에서는 H1 대비 개선이 없었다.

알고리즘 최종 선택: [고정3 문의·CP-SAT 유지, H1 미채택 근거와 개선·악화·제한값 동일 사례](FINAL_ALGORITHM_DECISION_20261030.md). 시간차 0의 도착 기록 상실도 별도로 구분한다.

2026-10-30 운영 후속: [여섯 항목 결과](OPERATIONS_FOLLOWUP_PROGRESS_20261030.md), [이송 중 환자 변경](ENROUTE_PATIENT_CHANGE_20261030.md), [자료 출처·자원 범위](DATA_AND_RESOURCE_SCOPE_20261030.md), [병원 통화·계측·외부 유입](HOSPITAL_OPERATIONS_20261030.md), [배포·복구 검증](RECOVERY_AND_RELEASE_20261030.md).

2026-10-30 알고리즘·최종 발표 후속: [일곱 항목 진행 결과](FINAL_READINESS_PROGRESS_20261030.md), [새 환경 평가](FINAL_EVALUATION_20261030.md), [최신 음성 지도](VOICE_MAP_REPLAY_20261030.md), [카카오 도로 통합](VOICE_ROAD_VERIFICATION_20261030.md), [사용성 실험 준비](USABILITY_EVALUATION_20261030.md), [발표·시연 대본](FINAL_PRESENTATION_AND_DEMO_20261030.md).

화면 개발자는 [화면과 서버 통합 개발 기준](TEAM_IMPLEMENTATION.md)에서 담당 작업과 완료 기준을 먼저 확인하세요. 전체 배경은 [루트 README](../README.md) → [현재 상태](PROJECT_STATUS.md), 요청·응답 형식은 [API 명세](API.md)를 참고하세요.

이번 설계 논의는 [문제 정의와 해결방안 초안](design/문제정의와_해결방안_초안.md) → [배정 알고리즘 초안](design/배정알고리즘_초안.md) 순서로 진행합니다.

서버 담당자는 [서버 데이터 처리 설계](design/서버_데이터처리_설계.md)에서 처리 흐름, 추가 모듈·DB·결과 API와 구현 순서를 확인하세요.

| 목적 | 문서 |
|---|---|
| 최신 작업을 이어갈 때 | [확정 당시 조건 저장·검증·시연·다음 작업](HANDOFF_20261013.md), [이전 서버 작업 이력](HANDOFF_20261012.md) |
| 구현할 기능과 ID | [2026-10-14 개정 롱리스트: 98항목·현재 상태·역할·완료 기준·이전 ID 대응표](design/캡스톤_전체구현기능_롱리스트.txt), [9월 28일 원본 보관](design/archive/캡스톤_전체구현기능_롱리스트_20260928.txt). 기존 [Word](design/캡스톤_전체구현기능_롱리스트.docx)는 개정 전 버전 |
| 시스템 전체 정보 교환과 객체 상호작용 | [전체 정보 교환 명세와 다이어그램](research/필요정보와_데이터확보계획_20261011.md), [데이터 확보 가능성 조사](research/데이터확보_조사_20260930.md), [API 필드 매핑 작업표](../api_field_mapping.md) |
| 배정 모델·재계산 절차 | [배정 알고리즘 구체화](design/배정알고리즘_구체화_20260930.md) |
| 화면과 서버를 연결할 때 | [공통 서버 API 명세](API.md), [서버 구현 안내](../server/README.md) |
| 양쪽 화면에 추천 결과를 표시할 때 | [워커 가용성·미배정 사유·팀원별 연결 기준](TEAM_RECOMMENDATION_INTEGRATION.md) |
| 서버와 자동 작업의 상태를 확인할 때 | [DB·워커·대기/오류·추천 갱신 진단](SYSTEM_STATUS.md) |
| 연결된 모바일·병원 화면을 실행할 때 | [동일 서버 실행·기능·실제 두 화면 검증](UI_INTEGRATION.md), [모바일 구현](../patient-ui/README.md), [병원 구현](../hospital-ui/README.md) |
| 최신 팀원 환자 앱에서 이어갈 때 | [945c413 원본 화면·공통 서버 연결·검증](TEAM_PATIENT_CONNECTION_20261013.md), [원본 앱 실행](../emergency-app/README.md) |
| 탐색 진행 상황과 병원 대화 | [대화 API 명세](CHAT_API.md), [환자와 병원과 시스템의 대화 흐름](design/환자병원시스템_대화흐름.md), [화면 담당 분담](TEAM_IMPLEMENTATION.md) |
| 초기 후보 자동 문의와 중지·재개 | [문의 워커 API·규칙·시연](INQUIRY_WORKFLOW.md) |
| 확정 후 조건 재확인과 대체 문의 | [감시·병원 판단·위치·대체 문의 API](TRANSPORT_REVIEW.md) |
| 시스템 밖 환자의 자원 사용과 퇴실 | [등록·조회·중복 방지·개별 자원 해제](EXTERNAL_ARRIVALS.md) |
| 일반 환자 퇴실과 수용 제한 오류 처리 | [실제 반영 점유·집계 충돌·제한 시각 검증](RESOURCE_ACCOUNTING.md) |
| 서버 문의 정책의 동일 조건 비교 | [방법·성과·실패와 다음 개선](INQUIRY_EXPERIMENT.md), [64개 시나리오 결과](../simulation_results/server_inquiry_comparison_20261012.md) |
| V3 문의 정책의 개선과 별도 검증 | [선택 절차·성과·남은 실패](INQUIRY_V3_EXPERIMENT.md), [개발 결과](../simulation_results/server_inquiry_v3_development_20261012.md), [새 seed 검증](../simulation_results/server_inquiry_v3_validation_20261012.md) |
| 서버 기록을 지도에서 비교할 때 | [실행·8개 사례·원시 기록과 재생의 관계](SERVER_REPLAY.md) |
| 알고리즘 개선 방향을 판단할 때 | [Codex 연쇄 실험 검토(10-15~10-20)](CODEX_ALGORITHM_REVIEW_20261020.md), [기대 도착시간(EAT) 문의 정책: 보정 무작위 환경·완전정보 하한·미사용 seed 검증·병원 문의 처리 용량 설계](EAT_INQUIRY_EXPERIMENT.md) |
| 실제 병원 데이터를 적재할 때 | [공공 API 적재·주기 갱신](../server/README.md#공공-api-데이터-적재갱신-2026-10-11), [공식 필드 매핑](../api_field_mapping.md) |
| 실험 권역과 치료조건 데이터 | [경기 남부와 충남 북부 수집 결과](research/경기남부_데이터확보_결과_20261011.md), [치료조건 카탈로그](research/치료조건_카탈로그_20261011.md), [권역 설정](../config/experiment_region.json), [조건 설정](../config/treatment_requirements.json) |
| API 추가 수집과 치료조건 판정 | [34기관·12종·메시지·후보/배정 연결 결과](research/API추가수집_치료조건판정연결_20261011.md) |
| 분만실 Y/N과 심평원 연계 | [처리 결과·신청 상태·실행 방법](research/분만실_YN_심평원_연계_20261011.md) |
| 가상 실험의 비교 결과 | [B0/B1 결과](../simulation_results/summary.md), [초기 솔버 실험 결과(보관)](../archive/early_optimization/optimization_results/README.md) |
| 지도 실험·문의 부하 검증 | [도로 입력 개발 비교](../simulation_results/map_comparison.md), [대기열·조건변경 검증](../simulation_results/workload_validation.md), [관측형 문의 V1](../simulation_results/adaptive_inquiry.md), [병원별 관측 문의 V2](../simulation_results/observed_inquiry.md), [작업 핸드오프](HANDOFF_20261011.md) |
| 우선순위 보호 정책 비교 | [우선순위·공동 배정 검증](../simulation_results/priority_protection_corrected.md), [개발 잠금](../simulation_results/priority_protection_corrected_development.md), [독립 감사](../simulation_results/priority_protection_corrected_audit.json), [앞선 실행 중단 기록](../simulation_results/priority_protection_interrupted.json) |
| 문제 배경·제안서 | [최적화 문제 정의](design/최적화_문제정의_v1.md), [현재 제안서](proposals/current/캡스톤_프로젝트_제안서_병원응답보강_20260923.docx), [이전 제안서](proposals/archive/) |
| 원인 조사와 검토 자료 | [호남권 이송체계 공개정보 정리](research/호남권_이송체계_공개정보_정리.md), [이송체계 전국 확대의 남은 문제와 해결방안](research/이송체계_전국확대_문제점과_해결방안.md), [시범사업 문제점과 솔버 실험](research/응급의료_시범사업_문제점과_솔버실험.md), `research/`의 Word 조사자료 |
| 제출 산출물 | [멘토링 보고서](reports/캡스톤_멘토링_보고서_작성본.docx), [최종보고서 뼈대](reports/캡스톤_최종보고서_뼈대.docx) |
| 이전 USB 작업 방식(보관) | [USB 시작 안내](../archive/usb_transfer/USB_시작안내.md), [로컬 작업환경](../archive/usb_transfer/LOCAL_SETUP.md) |

같은 주제의 예전 제안서는 `proposals/archive/`에 보관했습니다. 날짜가 있는 조사·제안서와 현재 구현 상태는 다를 수 있으므로, 진행 상황은 [현재 상태](PROJECT_STATUS.md)와 코드를 기준으로 확인하세요. 문서 생성용 `build_*.py`는 [`archive/early_documents/`](../archive/early_documents/)로 옮겼습니다. 서버 실행에는 필요하지 않습니다.
