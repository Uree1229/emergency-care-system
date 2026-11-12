# dev1 새 AI 시작 지시

작성일: 2026-11-02. 아래 지시를 새 AI에 전달한다. 기준은 main `580460a` 기반 구급대원 UI 후속 소스이며, 시작할 때 실제 체크아웃과 해당 PR 병합 여부를 먼저 확인한다.

```text
당신은 dev1의 구급대원 화면 개발을 이어받는다. dev2는 병원 화면 담당이다.
실명은 사용하지 말고 dev1/dev2 역할명만 사용한다.

먼저 아래 문서와 실제 코드를 읽고, 기존 연결을 확인한 다음 D01의 재현부터 시작한다.
- docs/team-handoff/20261102/README.md
- docs/team-handoff/20261102/COMMON_RUNTIME.md
- docs/team-handoff/20261102/CURRENT_STATE.md
- docs/team-handoff/20261102/dev1/README.md
- docs/team-handoff/20261102/dev1/AI_START.md (이 프롬프트 자체)
- docs/team-handoff/20261102/dev1/MOBILE_APP_IMPROVEMENTS_20261103.md (모바일 앱 전환 개선 M01~M09)

기준은 main 580460a 기반 이번 UI 후속 변경을 포함한 소스다. 과거 문서의 '로컬 완료', 다른 미공개 코드,
옛 실행 묶음에만 있는 기능을 현재 main 기능으로 가정하지 않는다.
실제 SHA와 변경 상태를 확인하고 차이가 있으면 범위를 기록한다.
최신 원격 main도 확인한다. 첫 응답에는 현재 구현·소유 범위·D01 재현 계획을
짧게 요약하고, 계획만 제시한 채 멈추지 말고 재현부터 진행한다.
별도 codex/dev1-paramedic-* 브랜치에서 작업하고 기존 변경을 보존한다.

수정 범위:
- paramedic-ui/
- patient-ui/의 구급대원 측 등록·문의·이송·대화·중요 사건 흐름
- test_paramedic*, test_patient_workflow*
- dev1 영역 문서

공유 경계:
- patient-ui/transport-change.*와 transport-retry.mjs는 구급대 요청과 병원 판단이
  섞여 있다. 수정 전에 dev2와 공통 계약 담당에게 범위를 구체적으로 조율한다.
- hospital-ui/는 dev2 영역이다.
- server/, DB·마이그레이션, simulation/, 솔버·알고리즘·실험 잠금은 수정하지 않는다.
  필요한 서버 계약 변경은 요청으로 남긴다.
- emergency-app/은 별도 FastAPI 입력 앱이다. 참고 코드를 읽을 수 있지만 덮어쓰지 않는다.

금지:
- 공용 DB 초기화, 기존 자료 삭제, 남의 폴더·미커밋 변경 덮어쓰기
- 실제 API 키·실제 환자정보 사용, 실제 병원에 문의·메시지 전송
- 임상 원문을 이유 없이 새 영구 저장소·로그·URL에 넣기
- 없는 API를 있는 것으로 가정하거나 계측 실패를 이유로 임상 동작을 우회하기

시험은 새 임시 SQLite와 합성 환자, loopback 서버에서 진행한다.
기존 서버를 중단하거나 기존 DB를 재사용하지 않는다.

먼저 읽을 실제 코드:
1. paramedic-ui/voice-client.mjs: VoiceClient.execute/write/retry/sync
2. paramedic-ui/app.js: connect, load-patient 처리, submit-button 처리,
   renderPatient, candidates; main-recovery.mjs의 쓰기 전 보존·GET 복원·정확 재시도
3. paramedic-ui/main-followup.mjs: MainFollowup, patientNavigation
4. paramedic-ui/main-treatment-review.mjs: requireWaiting/load/preview/apply
5. patient-ui/app.js: intake-form 처리, openPatient, resetPatientContext,
   readConversation, submitMessage, restoreWorkflow
6. patient-ui/workflow-state.mjs: PatientWorkflow의 추천·사건·수신·확인 처리
7. patient-ui/workflow-view.mjs: observeVisibleEvents
8. server/app.py의 실제 라우트와 server/service.py의 create_patient(읽기 전용)

이미 구현된 연결을 새로 만들지 않는다:
- 메인 음성/직접 입력→질문/미측정/보류→초안 확인→위치·동의→명시 등록
- 같은 환자 ID 진행→명시 문의→병원 수용→사람 목적지 확정→출발
- WAITING 치료조건 검토/명시 적용, 확정·이송 중 별도 병원 검토 요청
- 현재 추천/input-review, 채팅, 중요 사건 실노출 수신/사용자 확인
후보·추천은 수용 확답이 아니고, 알림 확인은 임상 판단이나 목적지 변경이 아니다.
도착 확인은 병원 역할이다. quick 인계 요약의 복사·읽기는 채팅 자동 전송이 아니다.

첫 업무 D01 / P0:
patient-ui/app.js의 수동 등록은 id 없이 POST /api/patients를 보낸다.
server/app.py는 id가 없으면 매번 P-UUID를 생성한다.
따라서 저장 성공 뒤 응답만 유실되면 재제출이 중복 환자를 만들 가능성이 있다.
이는 코드 추론이며 아직 재현 확정된 버그로 쓰지 않는다.

새 임시 DB에서 실제 HTTP 저장 뒤 응답만 유실시키고, 현재 UI의 재제출 결과를
환자 수·생성 사건·문의 수로 확인하는 재현 시험부터 작성한다.
새 시험 이름은 test_patient_workflow_registration*처럼 담당 접두어를 사용한다.

재현되면 최초 전송 전에 고정 환자 ID와 원래 요청 본문·서버 범위를 준비하여
재시도에 보존하는 최소 변경을 검토한다. 결과 불확실 중 편집한 본문을 같은
요청에 섞거나 다른 서버로 재전송하지 않는다.

계약 주의:
- POST /api/patients의 같은 id 재전송은 성공중복 응답이 아니라 409 DUPLICATE다.
- 같은 id GET 후 원래 요청의 저장 여부를 검증해야 한다.
- 서버에 등록 fingerprint 자동 검증 계약은 없다.
- emergency-app/common_server.py의 create_episode는 fixed MOBILE-UUID,
  extra.source_app/submission_fingerprint, GET 복구의 기존 참고 구현이다.
  그대로 복사하기 전에 입력 정규화·fingerprint 범위·충돌 의미를 확인한다.
- 같은 ID만 보고 성공 처리하지 않는다. 본문/출처 일치가 확인되지 않으면
  보수적인 확인 필요 상태를 유지한다.
- 환자 상세 라우트와 UI는 [\w-]+를 사용한다. ID는 ASCII 영숫자·_·-로 만든다.
  actor_id의 . : @ 허용 패턴을 환자 ID에 그대로 적용하지 않는다.

D01 완료 기준:
정상 등록, 저장 후 응답 유실, JSON 손상, 복구 GET 실패, 동일 ID 다른 본문 충돌,
서버/환자 전환 중 늦은 응답을 검증한다. 같은 등록은 1건이고 문의 자동 생성이
없어야 한다. 원요청과 정확한 재시도 결과를 기록한다.

다음 업무 D02 / P0:
기존 구급대 채팅·중요 사건의 실제 페이지/HTTP·복귀 연결을 확인한다.
채팅은 원래 client_message_id·본문·hospital_session_id를 유지하고 새 참여
병원으로 자동 변경하지 않는다. 사건/메시지 커서는 독립이며 모든 페이지를 읽는다.
중요 사건은 실제 행 노출 후 delivered, 사용자 버튼 후 acknowledge다.
숨김·다른 환자·서버·작업자·화면 세대의 응답과 실패 오류 보존을 시험한다.
현재 pendingMessage는 메모리에만 있어 페이지 재로딩 시 복원되지 않는다.
서버 저장 대화 재조회와 결과 미확인 원요청 복구를 구분한다.
합성 시연의 미확인 원요청 한 건만 POST 전에 복구 가능하게 보존하고, 저장 실패면
전송하지 않는다. 성공 확인 뒤 해당 요청만 정리한다. 채팅 전체의 영구 사본을
쌓지 않는다. 다른 서버·환자·참여 구간 또는 오래된 요청은 자동 재전송하지 말고
확인 필요로 표시한다. 저장 수명·격리·정리와 UI 안내를 모듈 및 PR에 명시한다.
기존 연결 전체를 재작성하지 말고 확인된 결함만 고친다.

그 다음 D03 / P1:
기존 /api/patients/{pid}/work-activities 시작·종료·목록·summary API를
등록된 환자의 실제 구급대 작업에 연결한다.
현재 main에 input-tasks/v14가 있다고 가정하지 않는다. 그런 전제가 나오면
코드를 쓰지 말고 계약 요청으로 기록한다.
원래 client_activity_id 재시도, 열린 활동 복원, 범위 격리와 반복 종료를 검증한다.
창 노출을 작업시간으로 자동 간주하지 않는다. 등록 전에는 pid가 없으므로
등록 전 총 입력시간은 별도 사용성 측정으로 구분하고 서버 시간을 소급하지 않는다.

후속 기능 보존:
- main-recovery.mjs는 같은 탭 sessionStorage에 12시간 동안 미저장 입력과 원요청을
  보존한다. GET부터 복원하고 불확실 쓰기는 명시 정확 재시도만 허용한다.
  저장 실패·범위 불일치·편집 버전 충돌에서 자동 전송/새 초안 대체를 금지한다.
- CommandCommitGate는 command-practice와 업무 quick-test 모두에 연결됐다.
  Quick은 기본 후속 인식 800ms 보류, 버튼 전용은 관찰이다. STT 결과 수신
  기준 실험값으로 실제 무음 검출·최적값·실제 현장 오탐 방지 검증이 아니다.
  부정/인용/시각 설명/후속 조각/환자 전환/늦은 결과 회귀를 유지한다.
- noise-compare.html은 선택형 로컬 RNNoise 전후 A/B·수동 인식문·ZIP 도구다.
  원본을 보존하고 처리본을 업무 STT에 자동 전달하지 않는다.
이 기능들이 /patient/ 수동 등록 D01과 채팅 D02 복구를 대신하지 않는다.
D01/D02에 잡음 처리나 명령 정책 변경을 섞지 않는다.

모바일 앱 전환 M01~M09:
구급대원 화면은 휴대폰 앱으로 쓸 예정이다. MOBILE_APP_IMPROVEMENTS_20261103.md의
측정 결과·개선 항목·완료 기준을 따른다. D01/D02와 별도 작업이며 같은 PR에 섞지 않는다.
화면 구조를 바꿔도 VoiceClient 재시도·409 처리·원요청 보존 계약은 유지한다.
웹 음성 인식이 앱 WebView에서 되는지는 추정하지 말고 시제품에서 확인한다.

기본 검증 명령(저장소 루트):
python scripts/test_suites.py list
python scripts/test_suites.py core
node --test tests/browser/test_paramedic_voice_client.mjs tests/browser/test_paramedic_main_workflow.mjs tests/browser/test_patient_workflow_state.mjs tests/browser/test_patient_workflow_view.mjs tests/browser/test_patient_workflow_transport_change_retry.mjs
node --test tests/browser/test_paramedic_main_recovery_20261031.mjs tests/browser/test_paramedic_local_audio_page.mjs tests/browser/test_paramedic_quick_page.mjs tests/browser/test_paramedic_noise_suppression.mjs tests/browser/test_paramedic_noise_compare_page.mjs
python scripts/test_suites.py python test_server test_voice_api test_work_activity test_backend_measurement_api -v

core는 Python 3.12.14, SQLite 3.53.1, OR-Tools 9.15.6755 고정 환경이다.
Node 경로가 필요하면 core --node <실행파일> 또는 TEST_NODE를 사용한다.
test_hospital_operations_http.mjs는 Python HTTP harness 없이 단독 실행하지 않는다.
고정 환경·연구 자료 누락 문제를 임의 소스/잠금 수정으로 해결하지 않는다.
변경별 집중 시험을 먼저 실행하고 최종 core를 확인한다.
새 테스트는 실제 구현 경계와 응답 유실·충돌을 검증해야 한다.

종료 보고에 반드시 포함:
- 기준 SHA·브랜치, 실제 변경 파일과 동작
- D01 재현 전후 및 DB/요청 중복 여부
- 실행한 명령·결과·고정 환경 일치 여부
- 모의 DOM/STT, 실제 HTTP, 실제 브라우저·기기 각각의 검증 범위
- 아직 실행하지 못한 항목과 이유
- dev2/공통 담당에게 필요한 계약 요청(경로·본문·오류·원하는 동작)
시험하지 않은 결과는 PASS로 쓰지 않는다. 실제 마이크·휴대폰·네트워크 장애·사람
사용성 측정은 모의 시험과 구분한다. 다른 영역을 무단 수정해 막힘을 우회하지 않는다.
```

상세 근거와 완료 기준은 [dev1 README](README.md)에 있다. 시작 후 새 코드나 계약 변경이 확인되면 근거를 남기고 이 문서의 기준 차이를 갱신한다.
