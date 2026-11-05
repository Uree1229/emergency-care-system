# 문서 안내

2026-11-05 정리. 현재 개발·운영 안내는 아래 주제별 문서를 사용한다.

| 필요한 내용 | 문서 |
|---|---|
| 현재 구현과 남은 작업 | [현재 상태](PROJECT_STATUS.md) |
| 설치·실행 모드·기기 연결·복구 | [실행 안내](RUNNING.md) |
| 시스템·상태 전환·자원 처리 | [아키텍처](ARCHITECTURE.md) |
| 음성 기록·명령·검토·데이터 수집 | [음성 기능](VOICE.md) |
| 문의 정책·배정 규칙·실험 근거 | [알고리즘](ALGORITHMS.md) |
| 자동 검사·실제 기기·사람 평가 | [검증](TESTING.md) |
| 등록부터 도착·연동 오류 복구 | [시연](DEMO.md) |
| 팀원 화면 작업 시작 (dev1·dev2) | [팀 핸드오프](team-handoff/20261102/README.md), [모바일 앱 전환 M01~M09](team-handoff/20261102/dev1/MOBILE_APP_IMPROVEMENTS_20261103.md) |

## 세부 계약

아래 문서는 코드의 요청·응답과 불변 조건을 설명한다. 날짜가 있는 계약 파일은 해당 API의 식별 경로를 유지한다.

| 구간 | 계약 |
|---|---|
| 공통 서버와 대화 | [API](API.md), [CHAT_API](CHAT_API.md), [프런트 연결](FRONTEND_FREE_FLOW.md) |
| 음성 초안·치료 조건 | [음성 API](VOICE_INPUT_API.md), [최소 입력](R1_MINIMUM_INPUT.md), [녹음·명령 설계](VOICE_PTT_DESIGN.md), [치료 조건 검토](VOICE_TREATMENT_REVIEW_API_20261030.md), [문맥·복수 후보 검토](VOICE_RECOGNITION_CONTEXT_20261103.md), [명령 보류](VOICE_COMMAND_GATE_20261031.md), [소음 억제 비교](VOICE_RNNOISE_COMPARISON_20261031.md) |
| 구급대 화면 입력 복구 | [대기 작업 통합](PARAMEDIC_PENDING_INTEGRATION_20261102.md) |
| 환자 정보 변경 | [환자 수정](PATIENT_AMENDMENTS_API.md), [배정 입력 검토](ALLOCATION_INPUT_REVIEW_API_20261030.md), [이송 중 변경](ENROUTE_PATIENT_CHANGE_20261030.md) |
| 문의·이송 | [문의 워커](INQUIRY_WORKFLOW.md), [조건 재확인](TRANSPORT_REVIEW.md) |
| 자원·운영 | [외부 유입](EXTERNAL_ARRIVALS.md), [점유 원장](RESOURCE_ACCOUNTING.md), [대사](ACCOUNTING_RECONCILIATION.md), [통화 기록](CALL_RECORD_API.md), [업무 계측](BACKEND_MEASUREMENT_API.md), [시스템 상태](SYSTEM_STATUS.md) |
| 팀 개발 | [역할·연결 계약](TEAM_IMPLEMENTATION.md), [서버 검토 계약](PEER_REVIEW_SERVER_CONTRACT.md), [API 스키마](api/SERVER_SCHEMA_20261102.json) |

## 연구와 원자료

`*_EXPERIMENT.md`, 알고리즘 프로토콜, `simulation_results/`는 당시 입력·비교·실패를 기록한 연구 근거다. 현재 제품의 기능 안내는 위 주제별 문서에서 확인한다. 실험 재현은 해당 버전의 코드·설정·잠금 파일을 함께 사용한다. [연구 안내](ALGORITHMS.md)

`research/`는 공개자료 조사와 출처, `design/`은 기획 배경, `testing/`은 사람 시험용 대본·양식이다. `proposals/current/`와 `reports/`의 제출용 문서는 작성 당시 자료로 보관한다. 현재 데이터 필드와 수집 코드는 [필드 매핑](../api_field_mapping.md), [서버 안내](../server/README.md)를 따른다.

중복된 날짜별 진행·인계 문서는 주제별 안내로 합쳤다. 정리 전 원본·파일 해시·이동 대응표는 로컬 `.codex-build/repo-cleanup-20261105/`에 보관한다.

다음 원본은 정리에서 제외해 그대로 둔다. 최종 발표가 근거로 인용하는 [병원 통화 기록·업무 계측](HOSPITAL_OPERATIONS_20261030.md)과 [입력·검토시간 평가](USABILITY_EVALUATION_20261030.md), 삭제한 브랜치의 복원 SHA를 담은 [10-31 인계](HANDOFF_20261031.md), 초기 문서 생성기·USB 전송 도구·초기 최적화 실험을 모은 [`archive/`](../archive/README.md)다. 팀원 화면 폴더(`patient-ui/`, `hospital-ui/`, `emergency-app/`)의 README는 각 담당자가 관리하므로 고치지 않았고, 그 README가 가리키는 [메인 화면 연결](MAIN_UI_INTEGRATION_20261030.md), [팀원 테스트 가이드](TEAM_TEST_GUIDE.md), [환자 앱 연결](TEAM_PATIENT_CONNECTION_20261013.md), [`prototype/`](../prototype/)도 함께 둔다.
