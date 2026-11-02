# 문서 안내

문서가 200개가 넘는다. 이름에 날짜가 있는 문서는 그날의 작업 기록이라 지금 상태와 다를 수 있다. 지금 상태는 아래 **먼저 읽을 문서**와 실제 코드로 확인한다.

새 문서를 만들면 아래 분야 표에 한 줄로 추가한다. 진행 소식은 이 목차가 아니라 [현재 남은 작업](CURRENT_REMAINING_WORK_20261030.md)에 적는다.

## 먼저 읽을 문서

| 순서 | 문서 | 내용 |
|---|---|---|
| 1 | [프로젝트 소개](../README.md) | 목적, 구성, 실행과 테스트 |
| 2 | [현재 진행 상황과 남은 일](team-handoff/20261102/CURRENT_STATE.md) | 11-02 기준 상태 요약 |
| 3 | [현재 남은 작업](CURRENT_REMAINING_WORK_20261030.md) | 우선순위와 완료 기준. 계속 갱신한다 |
| 4 | [공통 실행 환경과 통신 계약](team-handoff/20261102/COMMON_RUNTIME.md) | 실행 방식, 포트, 화면–서버 계약 |
| 5 | [공통 서버 API 명세](API.md) | 요청·응답 형식 |
| 6 | [테스트 묶음과 CI](TESTING.md) | core·연구 검증, 고정 실행 환경, 백업 |
| 7 | [현재 시연 안내](DEMO_RUNBOOK_20261030.md) | 시연 순서와 저장 기록 |

화면 담당자는 [dev1·dev2 시작 핸드오프](team-handoff/20261102/README.md)에서 시작한다. 구급대원 화면은 [dev1 안내](team-handoff/20261102/dev1/README.md)와 [AI 시작 지시](team-handoff/20261102/dev1/AI_START.md), 병원 웹은 [dev2 안내](team-handoff/20261102/dev2/README.md)와 [AI 시작 지시](team-handoff/20261102/dev2/AI_START.md)를 본다.

현재 결론은 [최종 알고리즘 정책](FINAL_ALGORITHM_DECISION_20261030.md), [최종 평가](FINAL_EVALUATION_20261030.md), [사용성 평가 절차](USABILITY_EVALUATION_20261030.md), [최종 발표와 시연 진행안](FINAL_PRESENTATION_AND_DEMO_20261030.md)에 있다.

## 분야별 문서

### 서버와 API

| 문서 | 내용 |
|---|---|
| [서버 도메인·DB 구조](SERVER_DOMAIN_20261102.md), [서버 스키마](api/SERVER_SCHEMA_20261102.json), [서버 테스트 목록](api/SERVER_TESTS_20261102.json) | 11-02 서버 구조 인계 |
| [서버 구현 안내](../server/README.md) | 서버 실행, 공공 API 적재 |
| [초기 병원 자동 문의](INQUIRY_WORKFLOW.md) | 문의 워커 규칙, 중지·재개 |
| [수용 확정 이후 조건 재확인과 대체 병원 문의](TRANSPORT_REVIEW.md) | 확정 뒤 감시와 대체 문의 |
| [문의 이후 환자정보 수정](PATIENT_AMENDMENTS_API.md), [이송 중 환자정보 변경](ENROUTE_PATIENT_CHANGE_20261030.md) | 환자 정보 변경 |
| [배정 입력 미확정 정보 표시](ALLOCATION_INPUT_REVIEW_API_20261030.md), [상태 변경 → 치료조건 확인 → 배정 재평가](VOICE_TREATMENT_REVIEW_API_20261030.md) | 미확정·변경 정보 검토 |
| [이송 대화 API](CHAT_API.md) | 모바일·병원 화면 대화 |
| [통화 결과 기록](CALL_RECORD_API.md), [응답 관측·업무 계측·중요 사건](BACKEND_MEASUREMENT_API.md) | 병원 통화와 업무 계측 |
| [외부 유입 환자](EXTERNAL_ARRIVALS.md), [도착·퇴실 자원 정산](RESOURCE_ACCOUNTING.md), [수동 정산 후 기록 종결](ACCOUNTING_RECONCILIATION.md) | 자원 사용과 정산 |
| [서버·자동 작업 상태 조회](SYSTEM_STATUS.md) | DB·워커·오류 진단 |
| [R1 최소 입력 항목](R1_MINIMUM_INPUT.md), [음성 입력 API의 R1 맞춤](VOICE_R1_ALIGNMENT.md) | 구급대 최소 입력 |
| [기관정보·운영값 출처와 자원 범위](DATA_AND_RESOURCE_SCOPE_20261030.md), [공공 API 필드 매핑](../api_field_mapping.md) | 자료 출처 |
| [프론트 없이 확인하는 전체 흐름](FRONTEND_FREE_FLOW.md) | API만으로 전체 흐름 확인 |

### 실행·배포·복구

| 문서 | 내용 |
|---|---|
| [팀 공통 실행본](TEAM_COMMON_RUNTIME_20261030.md) | 서버·DB·워커·접속 주소 |
| [사용할 공통 실행본](TEAM_RELEASE_20261030.md), [서버 전용 통합 실행본](SERVER_INTEGRATION_20261030.md) | 실행본 버전 |
| [배포·백업·복구 재현](RECOVERY_AND_RELEASE_20261030.md), [공통 시연 DB 백업·복원](TEAM_DATABASE_BACKUP_20261030.md) | 복구 |
| [같은 WiFi PC·휴대전화 연결](TEAM_LAN_20261102.md), [팀원 화면 연결 시작 절차](TEAM_START_20261102.md) | 기기 연결 |
| [전체 흐름·오류 복구 PC 검증](DEVICE_FLOW_PC_VALIDATION_20261030.md), [팀원 화면 연결 전 서버 검증](TEAM_CONNECTION_CHECK_20261030.md) | 검증 기록 |

### 구급대원 화면과 음성 입력

| 문서 | 내용 |
|---|---|
| [메인 음성·이송과 검토·추천·알림 연결](MAIN_UI_INTEGRATION_20261030.md), [구급대 화면 대기 작업 통합](PARAMEDIC_PENDING_INTEGRATION_20261102.md) | 화면 연결 상태 |
| [음성 입력 API](VOICE_INPUT_API.md), [음성 입력 시제품](VOICE_INPUT_PROTOTYPE.md), [무전기 방식 설계](VOICE_PTT_DESIGN.md) | 음성 입력 기본 |
| [대화 중간 명령](VOICE_MID_TRIGGER_20261030.md), [초안 중간 저장과 복구](VOICE_DRAFT_RECOVERY_20261030.md), [환자별 초안 전환](VOICE_PATIENT_SWITCH_20261030.md), [상태 관찰·수정 이력](VOICE_OBSERVATION_HISTORY_20261030.md) | 초안 관리 |
| [중요 수치·부정 표현 복창](VOICE_CRITICAL_REVIEW_20261030.md), [시간 설명의 종료 처리 수정](VOICE_STOP_TIME_CONTEXT_20261031.md), [DB 용어사전과 명령 확장](VOICE_DICTIONARY_AND_COMMANDS_20261030.md) | 인식 결과 처리 |
| [음성 명령 연습](VOICE_COMMAND_PRACTICE_20261031.md), [명령 후보 보류와 이벤트 시험](VOICE_COMMAND_GATE_20261031.md), [명령 연습 녹음](VOICE_COMMAND_AUDIO_20261031.md) | 음성 명령 |
| [통신 장애 안내](VOICE_CONNECTION_GUIDANCE_20261031.md), [음성 기록 저장 확인](VOICE_STORAGE_CHECK.md), [병원 인계용 요약](VOICE_HOSPITAL_HANDOFF_20261030.md) | 저장과 인계 |
| [현장 소음 시험](VOICE_NOISE_TESTING_20261030.md), [PC 마이크·소음 재시험 준비](PC_MIC_NOISE_PREPARATION_20261031.md), [원본·RNNoise 짝 비교](VOICE_RNNOISE_COMPARISON_20261031.md) | 소음 |
| [낭독 스튜디오](VOICE_READING_STUDIO_20261031.md), [팀원 체험 안내](VOICE_TRYOUT_GUIDE.md) | 시험용 화면 |

### 병원·환자 화면 (팀원)

| 문서 | 내용 |
|---|---|
| [화면과 서버 통합 개발 기준](TEAM_IMPLEMENTATION.md) | 담당과 완료 기준 |
| [모바일·병원 화면과 공통 서버 연결](UI_INTEGRATION.md), [모바일 구현](../patient-ui/README.md), [병원 구현](../hospital-ui/README.md) | 화면 연결 |
| [추천 결과와 외부 유입 연결](TEAM_RECOMMENDATION_INTEGRATION.md), [병원 통화·업무 계측·외부 유입](HOSPITAL_OPERATIONS_20261030.md) | 병원 쪽 기능 |
| [팀원 환자 앱 945c413 연결](TEAM_PATIENT_CONNECTION_20261013.md), [원본 앱 실행](../emergency-app/README.md) | 환자 앱 |
| [피어리뷰 반영 화면 개선과 API 연결](TEAM_FEEDBACK_API_HANDOFF_20261029.md) | 10-29 팀원 작업 목록 |

### 시험과 측정

| 문서 | 내용 |
|---|---|
| [테스트 묶음과 CI](TESTING.md) | 자동 테스트 |
| [수동·음성 입력과 병원 검토시간 평가](USABILITY_EVALUATION_20261030.md) | 사용성 측정 절차 |
| [음성 입력 데이터셋과 자동 평가](SPEECH_EVALUATION.md) | 음성 인식 평가 |
| [팀원용 긴 문장 음성 테스트](TEAM_VOICE_TEST_GUIDE_20261031.md), [긴 명령 문장 40개](testing/TEAM_VOICE_COMMAND_LONG_SENTENCES_20261031.md)([txt](testing/TEAM_VOICE_COMMAND_LONG_SENTENCES_20261031.txt)), [긴 환자 상태보고](testing/TEAM_VOICE_REPORT_SENTENCES_20261031.md), [결과 양식](testing/TEAM_VOICE_TEST_RESULTS_TEMPLATE_20261031.csv) | 팀원 음성 시험 |
| [음성 앱–서버 연결 검증](VOICE_CONNECTION_VERIFICATION_20261030.md), [음성 초안 → 환자정보 → 병원 문의](VOICE_PATIENT_LINK_VERIFICATION_20261030.md), [실제 인식 결과 재생 검증](VOICE_UI_ACTUAL_REPLAY_20261030.md) | 음성 연결 검증 |

### 지도 재생

| 문서 | 내용 |
|---|---|
| [시뮬레이션 지도 재생 새 화면](SIM_VIEWER_LAB_20261030.md) | `simulation/viewer/lab.html` |
| [서버 문의 정책의 지도 재생](SERVER_REPLAY.md), [조건 변경 처리의 지도 재생](TRANSPORT_REPLAY.md), [실제 도로 5개 정책 재생](ROAD_ONLINE_REPLAY.md) | 정책 재생 |
| [최신 음성 입력·배정 지도 기록](VOICE_MAP_REPLAY_20261030.md), [카카오 도로 통합 검증](VOICE_ROAD_VERIFICATION_20261030.md) | 음성 흐름 재생 |

### 알고리즘

| 문서 | 내용 |
|---|---|
| [최종 알고리즘 정책과 개선·악화 사례](FINAL_ALGORITHM_DECISION_20261030.md) | 최종 선택 |
| [최종 비교 기준과 새 합성 환경 평가](FINAL_EVALUATION_20261030.md), [기준 배정 알고리즘 동결과 연결 검증](ALLOCATION_BASELINE_AND_E2E_20261030.md) | 최종 평가 |
| [CP-SAT을 포함한 전체 이송 정책 비교](ONLINE_ALLOCATION.md), [배정 규칙과 CP-SAT 비교](ALLOCATION_BENCHMARK.md), [큰 배치의 배정 개선](ALLOCATION_STRESS.md) | 배정 정책 비교 |
| [기대 도착시간(EAT) 문의 정책](EAT_INQUIRY_EXPERIMENT.md), [Codex 연쇄 실험 검토](CODEX_ALGORITHM_REVIEW_20261020.md) | 문의 정책 |
| [대기 보호 W1과 미채택 근거](WAITING_PROTECTION_20261030.md) | 마지막 후보 |
| [B0/B1 결과](../simulation_results/summary.md), [초기 솔버 실험(보관)](../archive/early_optimization/optimization_results/README.md) | 초기 비교 |

### 설계와 조사

| 문서 | 내용 |
|---|---|
| [문제 정의와 해결방안 초안](design/문제정의와_해결방안_초안.md) → [배정 알고리즘 초안](design/배정알고리즘_초안.md) → [배정 알고리즘 구체화](design/배정알고리즘_구체화_20260930.md) | 설계 흐름 |
| [최적화 문제 정의](design/최적화_문제정의_v1.md), [서버 데이터 처리 설계](design/서버_데이터처리_설계.md), [환자·병원·시스템 대화 흐름](design/환자병원시스템_대화흐름.md) | 설계 |
| [전체 구현 기능 롱리스트](design/캡스톤_전체구현기능_롱리스트.txt), [개정 전 Word](design/캡스톤_전체구현기능_롱리스트.docx), [9월 원본](design/archive/캡스톤_전체구현기능_롱리스트_20260928.txt) | 기능 목록 |
| [전체 정보 교환 명세](research/필요정보와_데이터확보계획_20261011.md)([design 사본](design/필요정보와_데이터확보계획_20261011.md)), [데이터 확보 가능성 조사](research/데이터확보_조사_20260930.md) | 정보 교환 |
| [경기 남부·충남 북부 데이터](research/경기남부_데이터확보_결과_20261011.md), [치료조건 카탈로그](research/치료조건_카탈로그_20261011.md), [API 추가 수집과 치료조건 판정](research/API추가수집_치료조건판정연결_20261011.md), [분만실 Y/N과 심평원 연계](research/분만실_YN_심평원_연계_20261011.md), [권역 설정](../config/experiment_region.json), [치료조건 설정](../config/treatment_requirements.json) | 실험 권역과 병원 자료 |
| [호남권 이송체계 공개정보](research/호남권_이송체계_공개정보_정리.md), [전국 확대의 남은 문제](research/이송체계_전국확대_문제점과_해결방안.md)([Word](research/이송체계_전국확대_문제점과_해결방안.docx)), [시범사업 문제점과 솔버 실험](research/응급의료_시범사업_문제점과_솔버실험.md)([Word](research/응급의료_시범사업_문제점과_솔버실험.docx)), [병원 선정·이송체계 문제점 조사](research/응급환자_병원선정과_이송체계_문제점_조사.docx), [이송체계 문제점과 개선방안 v2](research/응급환자_이송체계_문제점과_개선방안_v2.docx) | 배경 조사 |

### 발표·보고·검토

| 문서 | 내용 |
|---|---|
| [최종 발표와 시연 진행안](FINAL_PRESENTATION_AND_DEMO_20261030.md), [알고리즘 평가와 최종 발표 준비 현황](FINAL_READINESS_PROGRESS_20261030.md) | 발표 |
| [멘토링 보고서](reports/캡스톤_멘토링_보고서_작성본.docx), [최종보고서 뼈대](reports/캡스톤_최종보고서_뼈대.docx) | 제출 산출물 |
| [현재 제안서](proposals/current/캡스톤_프로젝트_제안서_병원응답보강_20260923.docx), [이전 제안서](proposals/archive/) | 제안서 |
| [피어리뷰 분석과 개선 계획](reviews/피어리뷰_분석과_개선계획_20261028.md), [피어리뷰 대응 서버 계약 초안](PEER_REVIEW_SERVER_CONTRACT.md) | 피어리뷰 |

## 지난 기록

이름의 날짜 기준으로 남긴 작업 기록이다. 지금 상태와 다를 수 있다. 10-31 이전 실험의 재검증은 [테스트 안내의 백업 복원](TESTING.md#백업)으로 한다.

**인수인계와 검토**

- [10-11 시뮬레이션 검증 작업 인계](HANDOFF_20261011.md)
- [10-12 서버 작업 인계](HANDOFF_20261012.md)
- [10-13 서버·화면 통합 작업 인계](HANDOFF_20261013.md)
- [10-13 GitHub PR 검토와 병합 결과](reviews/PR_REVIEW_20261013.md)
- [음성 입력 V2 — Codex / Claude 작업 경계](VOICE_V2_CLAUDE_HANDOFF.md)
- [음성 V2 구급대원 화면 작업 기록](VOICE_UI_CLAUDE_HANDOFF.md)
- [10-30 UI 핸드오프 5.1·5.5 서버 반영](VOICE_UI_HANDOFF_SERVER_FIX_20261030.md)
- [10-31 GitHub 브랜치 검토·통합](reviews/BRANCH_REVIEW_20261031.md)
- [10-31~11-01 개선 방향과 저장소·테스트 정리](HANDOFF_20261031.md)

**상태와 잔여 작업**

- [10-28까지의 프로젝트 상태](PROJECT_STATUS.md)
- [10-30 프로젝트 전체 잔여 업무](PROJECT_REMAINING_WORK_20261030.md)
- [10-30 운영 기능 후속 결과](OPERATIONS_FOLLOWUP_PROGRESS_20261030.md)
- [11월 2일 투입 전 서버 준비 결과](SERVER_READY_RESULTS_20261030.md)
- [서버 공통 실행본 파일 분류](SERVER_RELEASE_INVENTORY_20261030.md)
- [10-13 팀원용 연결본 실행·테스트 가이드](TEAM_TEST_GUIDE.md)
- [10-13 중앙대 가상 환자와 실제 서울 병원 정보 연결](CAU_PUBLIC_DEMO_20261013.md)
- [초기 개선판 안내](archive/개선판_README.md)
- [이전 USB 작업 방식: 시작 안내](../archive/usb_transfer/USB_시작안내.md)
- [이전 USB 작업 방식: 로컬 작업환경](../archive/usb_transfer/LOCAL_SETUP.md)
- [문서 생성용 build_*.py (보관)](../archive/early_documents/)

**초기 지도 실험과 문의 부하 (10-11)**

- [도로 입력 개발 비교](../simulation_results/map_comparison.md)
- [대기열·조건 변경 검증](../simulation_results/workload_validation.md)
- [관측형 문의 V1](../simulation_results/adaptive_inquiry.md)
- [병원별 관측 문의 V2](../simulation_results/observed_inquiry.md)

**우선순위 보호 정책**

- [우선순위·공동 배정 검증](../simulation_results/priority_protection_corrected.md)
- [개발 잠금](../simulation_results/priority_protection_corrected_development.md)

**서버 문의 정책 (10-12~10-13)**

- [서버 문의 정책 비교](INQUIRY_EXPERIMENT.md)
- [64개 시나리오 결과](../simulation_results/server_inquiry_comparison_20261012.md)
- [V3 문의 정책 개선과 별도 데이터 검증](INQUIRY_V3_EXPERIMENT.md)
- [V3 개발 결과](../simulation_results/server_inquiry_v3_development_20261012.md)
- [V3 새 seed 검증](../simulation_results/server_inquiry_v3_validation_20261012.md)

**재문의와 병상 부족 대응**

- [전체 환자 대기시간 감소 실험](TOTAL_WAIT_EXPERIMENT.md)
- [C1 동률 배정의 선택 순서 안정화](STABLE_C1_EXPERIMENT.md)
- [희소 병상 보호 지연 개선 검증](SCARCE_DELAY_EXPERIMENT.md)
- [늦은 병원 회신과 진행 중인 문의 보호 검증](DEADLINE_SAFE_EXPERIMENT.md)
- [현재 병상 부족 보고의 제한적 수용 재확인](SHORTAGE_PROBE_EXPERIMENT.md)
- [처리 지원 여부에 맞춘 재확인 개선 및 검증](CAPABILITY_PROBE_EXPERIMENT.md)
- [처리 지원 재확인 실험 중단 기록 (10-17)](CAPABILITY_PROBE_CHECKPOINT_20261017.md)
- [재문의 순회·간격·동시 문의 배분 개선](RETRY_DISPATCH_EXPERIMENT.md)
- [실제 회복 증거에 따른 정상 문의 순위 개선](RECOVERY_RANK_EXPERIMENT.md)
- [실제 회복 보고를 기억하는 재문의 개선 실험](RECOVERY_DISPATCH_EXPERIMENT.md)
- [최근 부족 보고 뒤의 선택적 재문의 억제](PROBE_GUARD_EXPERIMENT.md)
- [현재 부족이 확인된 가까운 후보 때문에 생기는 대기 축소](PUBLIC_OFFER_EXPERIMENT.md)
- [일반 문의를 보호하는 선택적 재확인 처리 실험](COOPERATIVE_PROBE_EXPERIMENT.md)
- [재확인이 오래 걸릴 때의 지연 개선](RESPONSIVE_CONFIRMATION_EXPERIMENT.md)
- [확인 대기 환자의 병상 기회 보호](OPPORTUNITY_PROTECTION_EXPERIMENT.md)
- [추가 자원 보고 없는 돌발 병상 중단 회복](OUTAGE_RECOVERY_EXPERIMENT.md)
- [추가 자원 보고 없는 전체 대기 개선](EXISTING_INFO_EXPERIMENT.md)
- [정보 누락·갱신 지연 실험](PARTIAL_INFORMATION_EXPERIMENT.md)

**EAT 문의 정책 (10-20~10-28)**

- [EAT 문의 계획 개선: 지금 실행 가능한 문의 묶음 비교](EAT_BATCH_EXPERIMENT.md)
- [문의 앞당김의 후속 흐름을 보존해 문의 부담 감소](EAT_CHAINPLANWAKE_EXPERIMENT_20261028.md)
- [후속 문의 대기열의 시간 진행 보정](EAT_COHORT_ADVANCE_EXPERIMENT.md)
- [전체 첫 문의 보존과 우선 문의 공동 제출](EAT_COHORT_BATCHWAVE_EXPERIMENT.md)
- [후속 문의 계획 사이의 예상 대기 경합 보정](EAT_COHORT_CONTENTION_EXPERIMENT.md)
- [조합별 후속 CS 재계산 후보 D](EAT_COHORT_DYNAMIC_EXPERIMENT.md)
- [미래 대기열의 확률적 잔여시간과 후속 작업 경계 보정](EAT_COHORT_ENVELOPE_EXPERIMENT.md)
- [첫 문의 환자의 후속 문의 기회를 반영하는 공동 배분](EAT_COHORT_FOLLOWPLAN_EXPERIMENT.md)
- [세 후보 제한 해제와 전체 허용 조합 평가](EAT_COHORT_FULLMENU_EXPERIMENT.md)
- [이미 문의 중인 환자의 후속 문의 기회를 포함한 후보 평가](EAT_COHORT_HEDGE_EXPERIMENT.md)
- [첫 문의 대상 두 명인 구간의 공동 평가 확장](EAT_COHORT_PAIRSCOPE_EXPERIMENT.md)
- [두 명 공동 평가의 관측 경계 병원 교체 보호](EAT_COHORT_PAIRSHIELD_EXPERIMENT.md)
- [전체 후보 평가에 환자별 예측 확률 보호 추가](EAT_COHORT_PARETO_EXPERIMENT.md)
- [미착수 문의의 예상 취소를 반영한 공동 배분](EAT_COHORT_RECOURSE_EXPERIMENT.md)
- [첫 문의 검토 슬롯 공동 배분과 환자별 대기·도착 보존](EAT_COHORT_STARTSLOTS_EXPERIMENT.md)
- [후속 문의의 잔여 작업 꼬리 반영](EAT_COHORT_STOPLOSS_EXPERIMENT.md)
- [환자 성공 수 우선 공동 문의 후보 CS](EAT_COHORT_SUCCESS_EXPERIMENT.md)
- [후속 첫 문의 환자가 있는 공동 평가의 개인 대기 보호](EAT_COHORT_TAILWAIT_EXPERIMENT.md)
- [환자별 예측 확률 보호 안에서 선정 대기 우선 최소화](EAT_COHORT_WAITPARETO_EXPERIMENT.md)
- [후속 문의 계획의 동시 문의 한도 보정](EAT_COHORT_WAVECAP_EXPERIMENT.md)
- [공개 불가 회신 조기 재평가의 다중 환자 검증](EAT_DECLINEWAKE_MULTI_EXPERIMENT_20261028.md)
- [여러 관점의 문의 알고리즘 개선 실험](EAT_DIVERSE_EXPERIMENT_20261026.md)
- [EAT 대기 추정 개선: 공개된 FIFO 전체의 잔여 업무 추정](EAT_FIFO_EXPERIMENT.md)
- [전역 공개 회신 재평가 실험](EAT_FOREIGNREPLYWAKE_EXPERIMENT_20261028.md)
- [EAT 문의 계획 개선: 같은 개수 안에서 문의 병원 교정](EAT_GUARD_EXPERIMENT.md)
- [잔여시간 계산 보정 검증](EAT_HORIZON_EXPERIMENT.md)
- [EAT 문의 보호 실험: 최근 공개 계획과 충돌하는 병원 교정 보류](EAT_INTENT_EXPERIMENT.md)
- [공동 첫 문의의 후속 문의 수 보정 B](EAT_JOINT_BUDGET_EXPERIMENT.md)
- [공동 첫 문의 배분 검증](EAT_JOINT_FIRST_EXPERIMENT.md)
- [공동 첫 문의의 제약 충족 조합 재선택 A](EAT_JOINT_RESELECT_EXPERIMENT.md)
- [공동 성공 보존 목표의 가능성 검산](EAT_JOINT_SUCCESS_FEASIBILITY.md)
- [공동 문의 후 다른 환자의 문의 기회 보호](EAT_JOINT_TAIL_EXPERIMENT.md)
- [계획된 문의 한 건을 앞당겨 대기 복원](EAT_PLANWAKE_EXPERIMENT_20261028.md)
- [선행 문의의 공개 회신을 반영하는 재평가 실험](EAT_PREDECESSORWAKE_EXPERIMENT_20261028.md)
- [문의 시점과 계산량 개선 실험](EAT_QUEUE_REVIEW_EXPERIMENT_20261027.md)
- [EAT 문의 대기 추정 실험: 선행 업무의 잔여 시간 반영](EAT_RESIDUAL_EXPERIMENT.md)
- [EAT 문의 후보 선별 실험: 기대 비용으로 여섯 병원 선택](EAT_SHORTLIST_EXPERIMENT.md)
- [빈 공개 대기열을 이용한 조기 문의 복원](EAT_TRAVELRECALL_EXPERIMENT_20261028.md)
- [공개 이동시간으로 조기 문의를 보호하는 후보](EAT_TRAVELWAKE_EXPERIMENT_20261028.md)
- [같은 시각 계획만 앞당기는 문의 제한 실험](EAT_WAVEPLANWAKE_EXPERIMENT_20261028.md)
- [다음 문의 알고리즘 개선 후보 탐색](NEXT_INQUIRY_EXPLORATION_20261021.md)

**배정 정책과 최종 비교 (10-20~10-29)**

- [C1 시간 부족 시 초기 배정안 보강 실험](C1_INCUMBENT_EXPERIMENT.md)
- [조기 확정의 후발 환자 지연 개선 실험](OFFER_RESERVE_EXPERIMENT.md)
- [H1 전체 이송 실험](H1_ONLINE_EXPERIMENT.md)
- [문의 정책과 H1·C1 공동 배정 개선](JOINT_POLICY_EXPERIMENT.md)
- [10-28 알고리즘 개선 결과](ALGORITHM_IMPROVEMENT_20261028.md)
- [기존 단축을 유지하면서 조기 문의 부담 감소](ALGORITHM_PLANFOLLOW_20261028.md)
- [성공·도착 보호를 유지한 대기 복원 결과](ALGORITHM_WAIT_RECOVERY_20261028.md)
- [회신순·거리순·H1 최종 비교 사전 설계](FINAL_ASSIGNMENT_PROTOCOL_20261028.md)
- [회신순·거리순·H1 최종 비교 결과](FINAL_ASSIGNMENT_RESULTS_20261028.md)
- [미배정 감소를 유지하면서 개인 도착 지연을 줄이는 G1 사전 설계](ARRIVAL_GUARD_PROTOCOL_20261028.md)
- [G2: 수용 인원 이득과 최상위 우선순위를 함께 보호하는 두 번째 후보](ARRIVAL_GUARD_PRIORITY_PROTOCOL_20261028.md)
- [미배정 보호와 거리순 대비 도착 지연 개선 검증](ARRIVAL_GUARD_RESULTS_20261028.md)
- [배정 알고리즘 개선: 지연 원인과 G3·G4 비교](ALGORITHM_IMPROVEMENT_20261029.md)
- [G2 성과 보존 조건을 적용한 G5 개선 실험](G2_PRESERVING_IMPROVEMENT_20261029.md)
- [문의 슬롯 보충·재문의 대기 개선 실험](INQUIRY_REFINEMENT_20261029.md)
