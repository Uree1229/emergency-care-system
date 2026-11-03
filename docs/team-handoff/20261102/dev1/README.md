# dev1 · 구급대원 화면 인수인계

작성일: 2026-11-02. 기준 소스: main `580460a` 기반 구급대원 UI 후속 변경 포함. 실제 체크아웃과 PR 병합 여부를 먼저 확인한다. 이 문서의 기능 목록은 코드 설명이며 실제 마이크·휴대폰·임상 성능 PASS 보고가 아니다. 공용 폴더의 다른 미공개 변경과 과거 로컬 실행 묶음을 현재 소스에 있다고 가정하지 않는다.

- 먼저 읽기: [공통 안내](../README.md), [공통 실행](../COMMON_RUNTIME.md), [현재 상태](../CURRENT_STATE.md).
- 새 AI에 전달할 시작 지시: [AI_START.md](AI_START.md).
- dev1은 구급대원 화면, dev2는 병원 화면을 맡는다. 이 문서는 담당자를 재배정하지 않는다.

## 1. 수정 범위와 협업 경계

| 범위 | dev1의 작업 |
|---|---|
| `paramedic-ui/` | 메인 음성 입력·검토·등록, quick 음성 기록, 연습 화면과 보조 모듈 |
| `patient-ui/` | 구급대원 측 환자 등록·문의·확정·출발·이송 진행·대화·중요 사건 |
| `test_paramedic*`, `test_patient_workflow*` | 위 기능의 기존 회귀와 새 재현 시험 |
| 이 폴더와 구급대원 영역 문서 | 실제 변경과 검증 범위에 맞게 갱신 |
| `patient-ui/transport-change.*`, `transport-retry.mjs` | 구급대 요청과 병원 판단이 섞인 공유 화면. 수정 전에 dev2·공통 계약 담당과 범위 조율 |
| `hospital-ui/` | dev2 영역. dev1 수정 금지 |
| `server/`, DB·마이그레이션, `simulation/`, 솔버·알고리즘·실험 잠금 | 동결 영역. 읽기와 격리 시험의 호출만 허용. 필요한 계약 변경은 요청으로 기록 |
| `emergency-app/` | 별도 FastAPI 환자 입력 앱. 참고 가능하나 dev1의 음성 UI 구현으로 덮어쓰지 않음 |

별도 `codex/dev1-paramedic-*` 브랜치에서 작업한다. 남의 미커밋 변경·폴더를 덮어쓰거나 공용 DB를 초기화하지 않는다. 실제 API 키, 실제 환자정보, 실제 병원에 대한 전송은 사용하지 않는다. 시험은 새 임시 SQLite와 합성 환자, loopback 주소로 분리한다.

## 2. 이미 연결된 현재 흐름

공통 서버는 [server/app.py](../../../../server/app.py)의 `UI_ROOTS`(25~29행)로 `/paramedic/`, `/patient/`, `/hospital/`을 제공한다. 화면 폴더마다 별도 운영 DB를 만들지 않는다.

| 화면·파일 | 현재 구현과 읽을 위치 |
|---|---|
| `/paramedic/` · [app.js](../../../../paramedic-ui/app.js) | `connect`, `load-patient`, `submit-button`, `renderPatient`, `candidates`. 신규 등록·같은 환자 진행 링크와 같은 탭의 미저장 입력·불확실 요청 복구가 연결됨 |
| [main-recovery.mjs](../../../../paramedic-ui/main-recovery.mjs) | 같은 탭의 12시간 복구본, 쓰기 전 원요청 보존, GET 우선 복원·명시 정확 재시도. 저장 오류·범위/편집 버전 충돌 시 새 쓰기를 차단 |
| [main-followup.mjs](../../../../paramedic-ui/main-followup.mjs) | `MainFollowup.answer`: 최신 질문 답변, 미측정, 로컬 검토 보류. `patientNavigation`: 같은 서버·환자·작업자 링크 |
| [main-treatment-review.mjs](../../../../paramedic-ui/main-treatment-review.mjs) | `requireWaiting`, `load`, `preview`, `apply`: WAITING의 같은 환자·세션, 두 버전·토큰·확인·조건 감소 검토 |
| `/paramedic/quick-test.html` · [quick-test.js](../../../../paramedic-ui/quick-test.js) | 토글/PTT, 긴 발화, 중간 명령, 환자별 복구·복창·인계 요약·이력. 기본 800ms 후속 인식 보류·버튼 전용 관찰·명령 이벤트 JSON. 실제 등록은 메인의 같은 초안으로 이어감 |
| [noise-compare.html](../../../../paramedic-ui/noise-compare.html) | 원본 보존·같은 입력 RNNoise 전후 로컬 A/B·수동 인식문·ZIP. 업무 STT의 기본 전처리가 아님 |
| `/patient/` · [app.js](../../../../patient-ui/app.js) | `intake-form` 등록(232~241행), 문의·사람 확정·출발·조건 감시·위치·취소(245~259행), `readConversation`, `submitMessage` |
| [workflow-state.mjs](../../../../patient-ui/workflow-state.mjs) | `PatientWorkflow`: 추천/input-review, 명시 재계산, 중요 사건 페이지·복귀·수신·확인 |
| [workflow-view.mjs](../../../../patient-ui/workflow-view.mjs) | `observeVisibleEvents`: 문서와 해당 행의 실제 노출 뒤 수신 처리 |
| [transport-change.mjs](../../../../patient-ui/transport-change.mjs) | 확정·이송 중 임상변경의 별도 검토 요청. 병원 판단 역할도 포함되므로 공유 경계 주의 |

메인 음성→초안 확인→위치·동의→명시 등록→같은 ID 진행→문의→병원 수용→사람의 목적지 확정→출발이라는 연결을 새로 만들 필요는 없다. 도착 확인은 병원 역할이다. 후보/추천, 수용 응답, 목적지 확정은 각각 다른 상태다. 등록·추천 조회·알림 확인으로 다음 업무를 자동 실행하지 않는다.

확정·이송 중 정보 변경은 일반 등록이나 WAITING 적용으로 우회하지 않는다. 현재 별도 검토 화면을 유지한다. quick의 병원 인계용 요약은 복사·읽기 초안이며 병원 채팅 자동 전송 기능이 아니다.

## 3. 현재 main API 연결표

아래에서 `V=/api/voice/sessions/{sid}`, `P=/api/patients/{pid}`다. 실제 계약 원본은 [공통 API](../../../../docs/API.md), [음성 API](../../../../docs/VOICE_INPUT_API.md), [채팅 API](../../../../docs/CHAT_API.md)와 `server/app.py`를 함께 확인한다. 오래된 문서의 완료 표현만으로 새 기능을 가정하지 않는다.

| 영역 | 현재 소비·노출 경로 |
|---|---|
| 음성 연결 | `GET /api/voice/capabilities`, `/api/voice/terms`; `POST /api/voice/sessions`; `GET V?actor_id=…` |
| 초안 입력·검토 | `POST V/transcripts`, `V/audio`, `V/commands`, `V/undo`, `V/confirm`; `PATCH V/fields`; `GET V/readback`, `V/history`; `POST V/followup/answers` |
| 최초 등록 | 메인: `POST V/submit` + `start_inquiry:false`. 환자 수동 입력: `POST /api/patients` |
| WAITING 적용 | `GET V/treatment-review`; `POST V/treatment-review/preview`, `V/treatment-review/apply` |
| 확정·이송 중 변경 | `POST V/transport-change-review/preview`, `V/transport-change-review/request`; 병원 목록·판단은 공유 계약 |
| 진행 조회 | `GET /api/hospitals`, `/api/inquiry-policies`, `P`, `P/candidates`, `P/recommendation`, `P/input-review`; `POST P/replan` |
| 문의·이송 | `POST P/inquiry-workflow`, `P/inquiries`, `P/inquiries/{iid}/retry`, `/cancel`, `/answer`, `P/confirm`, `P/depart`, `P/change-destination`, `P/cancel`; `PATCH P/transport-position` |
| 채팅 | `GET P/conversation?event_cursor=…&message_cursor=…&limit=…`; `POST P/messages` |
| 중요 사건 | `GET P/important-events`; `POST P/important-events/{eid}/delivered`, `/acknowledge` |
| 계측: 서버만 준비 | `POST P/work-activities`, `POST P/work-activities/{aid}/stop`, `GET P/work-activities`, `GET P/work-activities/summary`. 현재 구급대 화면 호출 없음 |

서버 위치: 음성 `app.py:46~210`, 계측 `259~286`, 중요 사건 `291~312`, 환자/진행 `500~606`, 대화 `623~630`. 현재 main에 `input-tasks` 또는 v14 입력 계측 계약이 있다고 가정하지 않는다. 그런 계약을 전제로 하는 코드는 중단하고 공통 담당에게 확인한다.

## 4. 보존할 오류·식별자 계약

- [VoiceClient](../../../../paramedic-ui/voice-client.mjs)의 `execute`, `write`, `retry`는 요청 직렬화와 원래 `client_request_id`·본문·`expected_version` 재사용을 구현한다. 응답 유실·잘못된 응답·5xx로 결과가 불확실하면 새 쓰기를 막고, 중복 성공 뒤 최신 GET까지 확인한다. 이를 일반 재POST로 단순화하지 않는다.
- 409는 최신 초안·질문을 다시 읽는 계기이며 과거 본문을 자동 덮어쓰는 신호가 아니다. `quick-test.js`의 `failure`, 메인 `task`와 `main-recovery.mjs`의 편집 버전 검사를 유지한다. 메인 음성 요청 복구가 `/patient/` 수동 등록·채팅의 복구까지 대신하지는 않는다.
- `patient-ui/app.js`의 `submitMessage`(204~224행)는 원래 `client_message_id`·본문·`hospital_session_id`를 보존한다. 참여 병원 변경 뒤 자동으로 새 병원에 재전송하지 않는다. 종료 후에도 이미 전송했으나 결과가 불확실한 요청의 정확한 재시도와 새 메시지를 구분한다.
- 채팅 사건/메시지 커서는 독립이다. 모든 `has_more`를 읽고 ID로 합친다. POST 성공 항목을 합쳐도 GET 커서를 임의로 앞당기지 않는다.
- 중요 사건은 GET 성공만으로 `delivered`를 쓰지 않는다. 문서·행 노출 뒤 수신, 사용자의 버튼 뒤 `acknowledge`다. 다른 환자·서버·작업자·화면 세대의 늦은 응답을 버린다.
- `actor_id`는 영숫자 시작, 이후 영숫자·`._:@-`, 1~80자다. 잘못된 값을 다른 ID로 몰래 대체하지 않는다. 이는 시연용 식별이며 로그인·권한 인증이 아니다. `workflow-state.mjs:2~6`, `server/voice_input.py:216~234` 참고.
- 환자 ID와 작업자 ID 패턴은 다르다. 환자 상세 라우트 `app.py:530`과 UI `openPatient`는 `[\w-]+`를 사용한다. 새 재사용 환자 ID는 ASCII 영숫자·밑줄·하이픈 형태로 만들고 `.`, `:`, `@`를 포함시키지 않는다.
- 공공 병원 자료를 읽어도 문의·채팅은 이 로컬 서버 기록이다. 실제 병원 통신이나 실제 음성 정확도 검증으로 표현하지 않는다.

## 5. 우선 업무: P0 두 개부터

### D01 · P0 · 수동 등록 응답 유실 재현과 동일 요청 복구

**확정 버그 보고가 아니라 코드상 가능성이며 재현이 먼저다.** `patient-ui/app.js:232~241`은 `id` 없는 등록 POST를 보낸다. 서버 `app.py:523~527`은 ID가 없으면 매번 새 `P-UUID`를 만든다. 첫 저장 성공 후 응답만 유실되면 재제출이 두 번째 환자를 만들 수 있다. 메인의 `VoiceClient` 기반 `V/submit` 재시도와 혼동하지 않는다.

1. 새 임시 DB·합성 환자로 실제 HTTP 저장 성공 뒤 응답만 숨긴다. 현재 화면의 재제출로 환자/생성 사건/문의 수가 어떻게 변하는지 기록한다. 재현되지 않으면 조건과 미재현 사실을 그대로 남긴다.
2. 수정은 최초 전송 전에 고정 ID와 요청 본문·서버 범위를 준비해 재시도에 보존하는 방식부터 검토한다. 결과 불확실 중 편집 내용을 기존 요청에 섞지 않는다.
3. **고정 ID만 재POST하면 성공 처리되지 않는다.** `service.create_patient`(355~375행)는 이미 있는 ID에 `409 DUPLICATE`를 반환한다. 성공 응답 유실이나 DUPLICATE 뒤에는 같은 ID GET으로 원래 요청의 저장 여부를 검증해야 한다.
4. 서버에는 등록 fingerprint를 자동 검증하는 표준 계약이 없다. 기존 [emergency-app/common_server.py](../../../../emergency-app/common_server.py)의 `create_episode`(127~151행)는 `MOBILE-UUID`와 `extra.source_app`·`submission_fingerprint`를 넣고 GET으로 검증하는 참고 구현이다. source/정규화/동일 본문 판단을 확인한 뒤 적용한다. 같은 ID만 보고 성공으로 간주하거나 서버를 무단 변경하지 않는다.

완료 기준: 정상 등록 1건, 저장 후 응답 유실→정확한 재시도에도 1건, 응답 JSON 손상, GET 복구 실패 후 원요청 유지, 같은 ID 다른 본문 충돌, 서버/환자 전환 중 오래된 응답 차단을 시험한다. 문의는 자동 생성되지 않아야 하며 확실한 원본 일치를 확인할 수 없으면 보수적으로 확인 필요 상태를 유지한다. 새 회귀는 `test_patient_workflow_registration*` 같은 담당 접두어로 추가한다.

### D02 · P0 · 이미 연결된 채팅·중요 사건의 실제 복구 경계 확인

새 채팅이나 알림 시스템을 만들지 않는다. `submitMessage`, `readConversation`, `PatientWorkflow.refreshEvents`, `markVisible`, `acknowledge`와 실제 페이지의 연결을 격리 HTTP·페이지 시험으로 확인하고 확인된 결함만 고친다.

현재 `pendingMessage`는 메모리 변수(`patient-ui/app.js:12`)이며 `resetPatientContext`(48~55행)와 페이지 재로딩으로 사라진다. 따라서 화면이 열린 동안 정확한 재시도가 된다는 사실을 새로고침 뒤 원요청 자동 복구까지 보장하는 문장으로 확대하면 안 된다. D02의 저장 범위는 합성 시연의 **결과 미확인 원요청 한 건**으로 한정한다. POST 전에 서버·환자·참여 구간·요청 ID·원래 본문·표시명을 복구 가능하게 보존하고, 저장 실패면 전송하지 않는다. 성공을 확인한 뒤 해당 요청만 정리한다. 채팅 전체의 영구 사본을 새로 쌓지 않으며, 다른 범위·오래된 요청은 자동 재전송하지 않고 확인 필요로 표시한다. 저장 수명과 안내는 별도 상태 모듈과 PR에 명시한다.

완료 기준: 메시지 저장 후 응답 유실→같은 ID 재시도 1건, 병원 참여 구간 변경 뒤 새 대상 전송 금지, 페이지 재로딩 후 서버 저장 대화 재조회와 원요청의 명시 재시도 복구, 다중 페이지 중복 제거, 환자/서버 전환의 늦은 응답 폐기, 숨김/화면 밖 행 수신 금지, 재접속 후 미확인 사건 복원, 수신/확인 실패가 다른 GET 성공에 가려지지 않음을 확인한다. 기존 단위시험과 실제 HTTP·DOM·실제 기기 확인을 구분해서 기록한다. 병원 상태 생성이 필요하면 기존 서버 API를 합성 fixture에서만 사용하고 dev2 파일은 수정하지 않는다.

### D03 · P1 · 기존 업무 계측 API를 구급대 동작에 연결

현재 구급대 화면에 `work-activities` 호출이 없다는 것이 연결 공백이다. [업무 계측 명세](../../../../docs/BACKEND_MEASUREMENT_API.md)의 기존 start/stop/list/summary를 사용한다. 서버가 없는 `input-tasks`/v14를 추정해서 호출하거나 API를 새로 만들지 않는다.

완료 기준: 등록된 같은 환자·작업자의 입력/진행 확인/대화 같은 실제 작업에 시작·종료를 연결하고, 원래 `client_activity_id` 재시도, 열린 활동 복원, 다른 범위 종료 차단, 반복 종료의 시간 불변을 검증한다. 창이 열려 있다는 이유만으로 활동을 시작하지 않는다. 서버는 등록된 pid가 필요하므로 등록 전 총 입력시간은 별도 사용성 측정으로 구분하고 서버 시각을 소급 작성하지 않는다. 임상 본문을 계측 추가 필드로 보내지 않는다.

### 보존할 후속 기능 · 메인 복구·800ms·잡음 비교

이번 소스에는 `main-recovery.mjs`와 메인 연결이 있다. 같은 탭의 원래 요청·버전·입력창을 보존하고 GET부터 복원한다. 자동 재전송하거나 조회 실패를 새 빈 초안으로 바꾸지 않는다. 12시간 유효기간·저장 실패 차단·편집 충돌 원본 유지 경계를 보존한다. 다른 기기 복원이나 영구 의료기록 보관 기능은 아니다.

`CommandCommitGate`는 연습과 업무 Quick 모두에서 사용한다. Quick 기본은 후속 인식 800ms 보류, 버튼 전용은 관찰 모드다. 부정·인용·시각 설명·후속 임시 조각·자연 종료·버튼 종료·환자 전환·늦은 인식 회귀를 유지한다. 800ms는 STT 결과 수신 기준의 실험값이며 실제 무음·발화 완료 검출이나 최적값 검증이 아니다. 이벤트 JSON은 화면 메모리에서 명시 내보내기하며 임상 정보가 포함될 수 있으므로 공유 전 확인한다.

`noise-compare.html`은 선택형 로컬 RNNoise A/B 도구다. 원본과 기존 보관함을 보존하며 처리본을 업무 STT에 자동 전달하지 않는다. 음질·신호 지표와 인식·의료 정확도를 구분한다. 이 세 기능이 D01 수동 등록과 D02 채팅 복구를 완료한 것은 아니며 두 P0를 그대로 진행한다.

## 6. 시험과 종료 보고

저장소 루트에서 실행한다. [scripts/test_suites.py](../../../../scripts/test_suites.py)가 실제 실행기다.

```powershell
python scripts/test_suites.py list
python scripts/test_suites.py core
node --test test_paramedic_voice_client.mjs test_paramedic_main_workflow.mjs test_patient_workflow_state.mjs test_patient_workflow_view.mjs test_patient_workflow_transport_change_retry.mjs
node --test test_paramedic_command_commit.mjs test_paramedic_command_practice_page.mjs test_paramedic_quick_page.mjs
node --test test_paramedic_main_recovery_20261031.mjs test_paramedic_local_audio_page.mjs test_paramedic_noise_suppression.mjs test_paramedic_noise_compare_page.mjs
python -m unittest test_server test_voice_api test_voice_treatment_review_http test_work_activity test_backend_measurement_api -v
```

`core`는 Python 3.12.14·SQLite 3.53.1·OR-Tools 9.15.6755 고정 환경을 확인한다. Node가 PATH에 없으면 `python scripts/test_suites.py core --node <실행파일>` 또는 `TEST_NODE`를 사용한다. 실행기는 외부 API 키 환경변수를 제거하고 `test_hospital_operations_http.mjs`를 단독 Node 대상에서 제외한다. 해당 파일은 Python HTTP harness 없이 실행하지 않는다. 환경 불일치/로컬 자료 누락과 제품 회귀를 구분한다. 연구 실험·잠금 파일을 고쳐 core를 통과시키지 않는다.

위 명령은 현재 유효한 진입점이며 이 문서 작성 중 실행하지 않았다. 변경 시 관련 집중 시험을 먼저 실행하고 최종 core를 확인한다. 모의 DOM/STT 성공을 실제 마이크·휴대폰·네트워크 장애·사용성 측정 PASS로 바꾸지 않는다.

작업 종료 때는 변경 파일/기능, 사용한 기준 SHA·브랜치, 실행한 명령·결과·환경, 재현 전후, 미검증 항목, 남은 계약 요청을 기록한다. 공유 계약이 막히면 요청 경로·본문·현재 오류·필요한 동작을 구체적으로 남기고 동결 파일을 우회 수정하지 않는다.

## 2026-11-03 후속 · 보고 문맥과 후보 비교

`codex/voice-context-review-20261103` 후속 변경은 [문맥 검토 연결 안내](../../../VOICE_RECOGNITION_CONTEXT_20261103.md)를 따릅니다. 실제 체크아웃/PR 반영 여부와 capabilities를 먼저 확인하세요. 긴 보고에서도 복수 후보를 요청하고, 원문·후보를 함께 원요청으로 보존하며 엇갈린 항목만 재확인합니다. 후보의 일치가 실제 발화의 정확성을 보장하지 않으며, 구형 서버·후보 한도에서는 명시 검토로 넘깁니다. D01/D02 작업과 합치거나 기존 환자값으로 숫자를 자동 보정하지 않습니다.
