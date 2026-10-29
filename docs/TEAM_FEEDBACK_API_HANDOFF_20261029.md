# 피어리뷰 반영을 위한 팀원별 화면 개선과 API 연결 작업

2026년 10월 29일. 기존 역할을 기준으로 김민석은 구급대원 앱, 김준성은 병원 웹, 정진은 공통 서버·알고리즘·시뮬레이션을 담당한다. 이번 문서는 전달할 작업과 완료 기준이며 화면 코드를 변경한 것은 아니다.

**이번 1차 목표는 입력과 핵심 상태 표시를 단순화하고, 이미 구현한 서버의 중요 사건 확인·업무 계측·통화 기록을 각 화면에 연결하는 것이다.** 기존 문의·수용·대화·이송 흐름은 활용한다.

공유 범위는 이번 서버·API·테스트·문서 변경이다. 팀원 화면 원본은 각자의 기존 작업 브랜치와 결합해 사용하며, 이 서버 변경 브랜치만으로 양쪽 최신 화면까지 포함됐다고 가정하지 않는다.

## 1 현재 상태와 담당 경계

| 영역 | 이미 있는 기능 | 이번에 연결하거나 개선할 부분 |
|---|---|---|
| 구급대원 앱 | 원본 `emergency-app`의 입력·최초 위치 확인과 공통 이송 화면 연결. `patient-ui`의 문의·질문 답변·사람 확정·출발·채팅·조건 감시·수동 위치 갱신 | 초기 입력 흐름, 상단 진행 요약, 중요 사건 수신·확인, 복귀 복구, 업무 계측, 통화 기록 |
| 병원 웹 | 문의·수용/불가/확인 중/질문, 취소 문의 응답 버튼 제거, 자원·수용 제한 관리, 유지/조정, 일반 이송 환자 도착·퇴실, 채팅 | 취소 인지 강조와 검토 종료, 중요 사건 수신·확인, 업무 계측, 통화 기록. 후속으로 외부 유입 목록·개별 퇴실과 추천 표시 |
| 공통 서버 | 업무 API, 새 계측·중요 사건·통화 기록 API. 첫 확인과 최종 결정 관측 분리 | 팀원 연결 지원·통합 검증. 병원 전체 신규 통화함·푸시·실제 인증 등 별도 계약이 필요한 부분은 서버 담당 |

병원 웹의 수용 제한 등록·해제·예정 종료 후 재확인·연장은 현재 코드에 있다. 오래된 README의 미구현 설명을 그대로 작업 목록으로 사용하지 않는다. 외부 유입은 현재 등록만 있고, 일반 이송 환자의 퇴실과 외부 유입 개별 퇴실은 다른 기능이다.

서버의 첫 확인·최종 결정 관측 수정은 내부 처리다. 화면에서 별도 신규 관측 API를 호출할 필요는 없다.

## 2 공통 경로와 식별자

```text
API_ORIGIN = 현재 공통 서버의 주소와 포트
P = /api/patients/{pid}
H = /api/hospitals/{hid}/patients/{pid}
```

표의 `P`, `H`는 경로 축약이다. 실제 요청에서는 실제 환자·병원 ID로 바꾸고 URL에 인코딩한다. 원본 입력 앱이 만든 공통 이송 ID를 사용하며 개인 프로필 ID와 혼동하지 않는다. 두 화면은 같은 공통 서버·DB를 사용한다.

- `actor_id`: 역할별로 일관된 작업자 ID. 예: `crew-demo-1`, `hospital-desk-1`. 새로고침·재시도·폴링마다 바꾸지 않고, 다른 사람끼리 공유하지 않는다. 환자 이름·연락처를 사용하지 않는다.
- `handled_by`: 기존 병원 업무 변경 API의 처리자 표시명. 신규 API의 `actor_id`와 목적이 다르다.
- `client_activity_id`, `client_record_id`, `client_confirmation_id`: 새 작업마다 생성하고 **실패한 동일 요청의 재시도에서는 원래 키와 본문을 유지**한다.
- `iid`: 문의 ID, `activity_id`: 작업 계측 ID, `event_id`: 중요 사건 ID, `record_id`: 통화 기록 ID. 서로 바꿔 쓰지 않는다.
- 서버 시각은 UTC epoch 초다. 표시할 때 한국시간으로 변환한다. `actor_id`와 병원 ID는 현재 시연용 식별자이며 사용자 인증을 대신하지 않는다.

## 3 김민석 구급대원 앱 작업

원본 입력은 `emergency-app`, 공통 서버의 이송 진행 화면은 `/patient/`의 `patient-ui`다. 실제 사용할 입력 진입점을 정해 중복 화면을 만들지 않는다.

| ID | 우선 | 화면 개선과 연결 동작 | API와 주요 필드 | 완료 기준 |
|---|---|---|---|---|
| M1 | P0 | 주요 정보와 상세 표시를 구분하고 최초 입력 단순화 | 기존 `POST /emergency/connect` → 공통 `POST /api/patients`. 문의 전 `PATCH P`, 문의 후 `POST P/amendments`. 질문 답변은 기존 answer API | 공통 `patient-ui`의 이름·나이·연락처는 이미 선택. 원본 프로필의 이름 필수 흐름은 별도 점검. 수정 전 수용은 종료하고 새 정보로 재확인 |
| M2 | P0 | 상단에 현재 단계·문의 병원 수·확인 중·유효 수용·다음 행동 표시 | `GET P`의 `state`, `inquiries`, `inquiry_workflow`, `destination`, `transport_review`, `transport_inquiry_workflow` | 전체 채팅을 읽지 않아도 다음 행동 파악. 새로운 문의 루프는 화면에 만들지 않음 |
| M3 | P0 | 중요 변경 알림과 확인 버튼 | `GET P/important-events`, `POST P/important-events/{event_id}/delivered`, `POST .../acknowledge` | 표시된 사건의 수신과 사용자의 확인 동작을 분리. 확인만으로 목적지·수용 상태가 바뀌지 않음 |
| M4 | P0 | 탭 복귀·연결 복구 시 상태와 미확인 알림 복원 | `GET P`, 기존 대화 조회, `GET P/important-events?actor_id=...&unacknowledged_only=true` | `visibilitychange`·`online` 복귀 처리. 이전 환자·서버에서 늦게 온 응답은 무시 |
| M5 | P0 | 등록 후 입력·추가 질문·상태 확인·통화 등 실제 작업 계측 | `POST P/work-activities`, `POST P/work-activities/{activity_id}/stop` | 작업 시작·종료에 연결. 페이지를 열어 둔 시간 전체를 업무로 세지 않음 |
| M6 | P0 | 통화 결과 작성·조회·정정, 병원이 작성한 기록 내용 확인 | `POST/GET P/call-records`, `GET P/call-records/{record_id}`, `POST .../{record_id}/confirmation` | 공유 대상 병원을 명시. 내용 확인 상태와 실제 수용 응답을 다른 문구로 표시 |
| M7 | P1 | 위치 갱신 조작과 오래된 위치 표시 | `PATCH P/transport-position`에 `lat`, `lon`, `measured_at` | 현재 위치의 관측 시각 표시. 자동 수집을 추가하면 권한 실패 시 수동 대체 제공 |
| M8 | P1 | 서버 추천과 계산 불가 사유 표시 | `GET P/recommendation`, 필요한 명시적 재계산은 `POST P/replan` | 추천과 수용 응답·사람 확정을 구분. 워커 상태·계산 시각·부족한 입력을 표시 |

### 상태 요약에서 주의할 점

- 원본 입력 앱은 공통 서버 등록 시 자동 문의를 켠다. `PATCH P`는 문의 기록이 0건인 `WAITING`만 지원한다. 문의 후에는 [새 정보 수정 API](PATIENT_AMENDMENTS_API.md)에 최신 환자 버전·사유·변경 내용을 보낸다. 기존 문의와 수용 응답이 종료되므로 저장 전 안내하고, 새 정보의 수용 확인을 기다린다. 질문 답변이 임상 필드 변경을 대신하지는 않는다. 이미 확정·이송 중인 건은 이번 일반 수정 대상이 아니다.
- `PENDING`은 아직 병원 회신이 없는 상태, `CHECKING`은 병원 확인 중 또는 추가 질문 단계다. 둘을 모두 무응답으로 표시하지 않는다.
- `CHECKING`을 수용 완료로 세지 않는다. 질문은 확인 중 항목의 보충 표시로 두어 중복 집계를 피한다.
- `overdue`는 경과시간 강조다. `CHECKING`에도 참일 수 있으므로 무조건 “병원이 한 번도 답하지 않음”으로 표시하지 않는다.
- `contacted_count`는 문의한 병원 수이며 재문의까지 포함한 총 전송 횟수가 아니다.
- `usable`인 수용 응답으로 확정 버튼을 제공하되, 최종 서버 검사에서 409가 나면 최신 상태와 사유를 보여준다.
- 원본 앱의 최초 GPS 확인과 이송 중 연속 위치 추적은 다른 기능이다. 현재 공통 이송 화면은 수동 좌표 전송이다.

## 4 김준성 병원 웹 작업

| ID | 우선 | 화면 개선과 연결 동작 | API와 주요 필드 | 완료 기준 |
|---|---|---|---|---|
| H1 | P0 | 문의 상세를 핵심 상태·필요 조건·응답 버튼 중심으로 단순화 | 기존 `GET /api/hospitals/{hid}/inquiries` | 주요 정보가 먼저 보이고 상세는 필요할 때 펼침. 구조화된 응답을 유지 |
| H2 | P0 | 취소·다른 병원 확정의 인지 강조와 검토 종료 | `GET H/important-events`와 수신/확인 POST. 진행 작업은 `POST H/work-activities/{activity_id}/stop` | 기존 응답 버튼 제거를 유지하면서 담당자가 취소를 알아차리고 불필요한 검토 중단 |
| H3 | P0 | 실제 요청 검토·응답·질문·재확인 작업 계측 | `POST H/work-activities`, `POST .../{activity_id}/stop` | 폴링·새로고침마다 새 작업을 만들지 않음. 환자 전환·검토 종료·탭 숨김 처리 |
| H4 | P0 | 조건 변경 알림 확인과 의료적 유지/조정 판단을 분리 | 중요 사건 API + `GET P/transport-review` + 기존 `POST H/decision` | 알림 확인이 수용 유지로 처리되지 않음. 최신 위험 사유 확인 후 별도로 판단 |
| H5 | P0 | 통화 기록 조회·작성·상대 작성 내용 확인/이의·정정 이력 | `GET/POST H/call-records`, `GET .../{record_id}`, `POST .../{record_id}/confirmation` | 자기 병원 기록만 표시. “수용 가능하다고 전달받음”과 실제 수용 확정을 구분 |
| H6 | P0 | 복귀·재접속 후 미확인 사건 복구 | `GET H/important-events?actor_id=...&unacknowledged_only=true`와 기존 목록 조회 | 일반 스트림과 미확인 재조회를 분리. 병원·환자 전환 시 이전 응답을 반영하지 않음 |
| H7 | P1 | 외부 유입 목록·개별 퇴실 연결 | `GET /api/hospitals/{hid}/external-arrivals`, `GET .../{eid}`, `POST .../{eid}/discharge` | 등록한 외부 유입의 정확한 건을 조회·종료. 일반 이송 환자 퇴실과 구분 |
| H8 | P1 | 추천 결과·확인 필요 조건 표시 | 기존 `GET /api/hospitals/{hid}/incoming`의 `planning`, `planning_availability` | 내려오는 추천을 실제로 렌더링. 추천을 확정·예약으로 표현하지 않음 |

### 기존 의료 업무 API는 유지

| 동작 | 정확한 API와 본문 |
|---|---|
| 확인 중·수용·불가·질문 | `POST /api/inquiries/{iid}/respond`: `action`, `handled_by`, 최신 `expected_version`; 불가는 `reason`, 질문은 `question` |
| 수용 유지·조정 | `POST H/decision`: `decision: "keep"` 또는 `"adjust"`, `note`, `handled_by`. 활성 조건 감시는 최신 `expected_review_version`, `expected_risk_signature` 포함 |
| 일반 이송 환자 도착·퇴실 | 기존 `POST H/arrive`, `POST H/discharge` |
| 외부 유입 개별 퇴실 | `POST /api/hospitals/{hid}/external-arrivals/{eid}/discharge`: 최신 `expected_version`, `handled_by` |

현재 구현된 자원·수용 제한 관리와 상태 충돌 보호를 보존한다. 조건 감시가 꺼진 건의 기존 수동 판단 계약도 유지한다. 외부 유입 정산 오류는 자동 종료로 우회하지 않고 [정산 확인 계약](ACCOUNTING_RECONCILIATION.md)을 따른다.

환자정보가 수정된 문의의 `requires_review_version`이 1이면 확인 중·질문을 포함한 모든 응답에 최신 `expected_version`을 넣는다. 오래된 화면의 응답을 자동으로 최신 버전으로 바꿔 재전송하지 않는다.

## 5 양쪽 화면의 신규 API 연결 방법

아래 `B`는 구급대원에서 `P`, 병원에서 `H`다. 공통 요청·응답의 상세 원본은 [업무 계측·중요 사건 명세](BACKEND_MEASUREMENT_API.md)와 [통화 기록 명세](CALL_RECORD_API.md)다.

### 5.1 중요 사건

| 순서 | 호출 | 화면에서 할 일 |
|---|---|---|
| 새 사건 조회 | `GET B/important-events?actor_id=...&after_id=0&limit=100` | `items`를 사건 ID로 중복 제거. `has_more`가 참이면 다음 페이지도 읽음 |
| 사건 표시 후 수신 기록 | `POST B/important-events/{event_id}/delivered` | 본문은 `actor_id`. 단순 GET 성공과 사용자 인지를 혼동하지 않음 |
| 실제 확인 동작 후 기록 | `POST B/important-events/{event_id}/acknowledge` | 본문은 `actor_id`. 수신 기록 없이 호출하면 409 |
| 복귀 시 과거 미확인 조회 | `GET B/important-events?actor_id=...&unacknowledged_only=true&limit=100` | 일반 스트림 커서와 별도 관리. 확인 후 다시 조회해 다음 미확인 건 읽음 |

POST 본문:

```json
{ "actor_id": "crew-demo-1" }
```

`next_after_id`는 새 사건 스트림용이다. 미확인 모드에서는 `next_after_id`가 `null`이며 양수 `after_id`와 함께 보내면 400이다. 알림 확인은 수용·조건 유지·배정 변경을 실행하지 않는다.

화면에는 `at`, `delivered_at`, `acknowledged_at`을 서로 다른 의미로 사용한다. 화면이 숨겨진 동안 사건을 조회했다는 이유만으로 사용자가 확인했다고 기록하지 않는다.

### 5.2 업무 계측

| 동작 | 호출 | 본문 또는 반환 |
|---|---|---|
| 실제 작업 시작 | `POST B/work-activities` | `actor_id`, `client_activity_id`, `kind` |
| 실제 작업 종료 | `POST B/work-activities/{activity_id}/stop` | `actor_id` |
| 내 기록 조회 | `GET B/work-activities?actor_id=...&limit=50` | `activities`, `has_more`, `next_before_id` |
| 시간 집계 | `GET B/work-activities/summary?actor_id=...` | `totals`, `actors`, `measurement` |

시작 본문:

```json
{
  "actor_id": "hospital-desk-1",
  "client_activity_id": "review-demo-001",
  "kind": "request_review"
}
```

구급대원은 `patient_input`, `request_view`, `additional_question`, `chat`, `call` 등을, 병원은 `request_review`, `respond`, `recheck`, `resource_update` 등을 실제 작업에 맞춰 사용한다. `activity.id`를 보존해 종료할 때 사용한다.

- 화면 열림·백그라운드 폴링을 실제 검토로 간주하지 않는다. 계측 전용 버튼을 계속 누르게 하기보다 편집 시작·응답 제출 등 기존 동작에 연결한다.
- 숨김·환자 전환·작업 종료 처리 기준을 두 화면에서 합의한다. 종료 요청이 실패하면 정상 종료로 표시하지 않는다.
- 종료 시각은 서버 수신 시각이다. 통신 복구 뒤 늦게 종료된 기록을 실제 작업시간처럼 해석하지 않고 비교 표본에서 구분한다. 지연 종료 품질 표시는 서버 담당과 추가 계약이 필요할 수 있다.
- 최초 환자 등록 전 입력시간은 현재 API로 측정하지 못한다. 해당 부분은 사용성 평가에서 별도 측정한다.
- 집계는 환자 건별이며 병원 전체 업무량이나 여러 환자 사이 중첩 제거 결과가 아니다. 미종료 구간은 완료 작업시간에 포함하지 않는다.

### 5.3 통화 기록

| 동작 | 호출 |
|---|---|
| 결과 작성 | `POST B/call-records` |
| 목록·상세 | `GET B/call-records?limit=50`, `GET B/call-records/{record_id}` |
| 상대 측의 내용 확인·이의 | `POST B/call-records/{record_id}/confirmation` |
| 정정 | 새 `client_record_id`와 `supersedes_id`를 포함한 전체 작성 본문으로 `POST B/call-records` |

구급대원 작성 본문의 형식 예시:

```json
{
  "actor_id": "crew-demo-1",
  "client_record_id": "call-demo-001",
  "hospital_id": "H1",
  "call_started_at": 1790000005,
  "call_ended_at": 1790000015,
  "reported_outcome": "can_accept",
  "counterparty_role": "emergency_staff",
  "counterparty_label": "응급실 담당자",
  "note": "전화에서 전달받은 내용의 요약"
}
```

예시는 형식 설명이다. 실제 환자 등록 시각 이후·서버 현재 시각 이내의 통화 시각과 실제 병원 ID를 사용한다. `recorded_at`은 서버 기록 시각이며 통화 보고 시각과 다르다.

- `reported_outcome`: `can_accept`, `cannot_accept`, `checking`, `additional_info`, `no_answer`, `callback_requested`, `other`.
- `counterparty_role`: `paramedic`, `emergency_staff`, `coordinator`, `other`.
- `note`는 최대 1,000자, 상대 표시명은 최대 80자다. 일반 텍스트로 표시한다.
- 구급대원이 쓴 기록은 상대 병원 경로에서, 병원이 쓴 기록은 구급대원 경로에서 내용 확인한다.

내용 확인 본문:

```json
{
  "actor_id": "hospital-desk-1",
  "client_confirmation_id": "call-check-demo-001",
  "decision": "confirm"
}
```

이의는 `decision: "dispute"`와 `reason`을 보낸다. 내용 확인은 선택 사항으로 두고 수용 응답의 필수 선행 단계로 만들지 않는다. `confirmation_status: "confirmed"`를 “수용 확정”으로 표시하면 안 된다. 정정본은 미확인으로 시작하며 원문·기존 확인 이력은 보존한다.

통화 기록은 실제 전화 실행·녹음·자동 요약을 제공하지 않는다. 보고된 통화 기간을 업무 계측 시간에 자동 합산하지 않는다.

## 6 공통 오류와 비동기 처리

| 상황 | 화면 처리 |
|---|---|
| 요청 결과가 불명확한 네트워크 오류 | 원래 멱등 키와 본문으로 재시도. 새 키로 중복 작업을 만들지 않음 |
| `400` 입력 오류 | 입력값·빈 필터·잘못된 시각을 수정할 수 있도록 표시 |
| `403` 역할/참여 범위 오류 | 다른 병원 경로나 공통 환자 경로로 바꾸어 우회하지 않음 |
| `404` 기록 또는 관계 없음 | 해당 환자·병원·기록 ID를 점검. 성공처럼 숨기지 않음 |
| `409 DELIVERY_REQUIRED` | 사건 수신 기록의 결과를 먼저 확인 |
| 수용·자원·위험 판단의 `409` | 최신 상태를 다시 조회하고 사유를 표시. 바뀐 조건에서 자동으로 재확정하지 않음 |
| 통화 정정·확인 `409` | 최신 기록과 정정 이력을 조회. 다른 담당자의 판단을 덮어쓰지 않음 |

새 API의 시연 작업자 ID는 영문·숫자로 시작하고 영문·숫자·`. _ : @ -`를 사용한다. 임상 정보와 대화 원문을 URL이나 로컬 저장소에 넣지 않는다. 키·커서는 서버·역할·병원·환자·작업자 범위를 구분한다. 화면 전환 전에 시작한 조회가 나중에 끝나면 현재 화면에 반영하지 않는다.

## 7 서버 담당과 추가 협의할 부분

| 항목 | 현재 가능한 것 | 추가로 필요한 것 |
|---|---|---|
| 병원 전체 새 통화함 | 신규 `GET /api/hospitals/{hid}/call-records`로 모든 이송 건의 자기 병원 공유 기록 조회 | 병원 웹 통화함에 연결. 미확인함은 `author_role=paramedic&confirmation_status=unconfirmed`. 새 ID 발견과 기존 확인 상태 재조회를 분리 |
| 병원 전체 미확인함 | 알고 있는 문의·이송 건별 중요 사건 조회 | 병원 전체 통합 수신함 API는 아직 없음. 무관한 환자를 열거해서 보완하지 않음 |
| OS 푸시·백그라운드 전달 | 화면 조회와 수신·확인 기록 | Web Push·앱 푸시·재알림 스케줄러는 별도 설계 필요 |
| 정상 연결 밖의 업무 계측 | 서버 시각 기반 시작·종료와 미종료 표시 | 지연 종료·중단의 품질 표시, 등록 전 입력, 여러 환자를 합친 업무량은 별도 계약 |
| 문의 후 환자정보 보완 | 신규 `POST P/amendments`로 WAITING 단계 정보 변경·기존 응답 종료·새 버전 재문의 | 구급대원 수정 화면과 병원 최신 정보 재검토 연결. 이미 확정·이송 중인 건의 일반 임상정보 변경은 별도 설계 |
| 실제 인증 | 시연 작업자와 병원 ID 구분 | 실제 계정·소속 병원·접근 권한 연결은 서버 담당 후속 작업 |

통화 기록만 공유된 병원은 통화 기록·그 건의 계측을 사용할 수 있지만 대화·중요 사건의 기존 참여 조건이 자동으로 충족되지는 않는다. 이때 병원 중요 사건 조회의 403을 우회하거나 통화 메모로 가짜 수용 응답을 만들지 않는다.

## 8 연결 순서와 공동 완료 기준

권장 순서는 **핵심 상태 표시 → 중요 사건과 복귀 처리 → 정상 과업의 업무 계측 → 통화 기록 → 사용성 비교**다. 추천 상세·위치 자동화·외부 유입 관리 확장은 다음 단계로 둔다.

각 담당자는 같은 가상 이송 건을 사용해 다음을 함께 확인한다.

- [ ] 최초 입력에서 주요 정보와 미확인을 유지하고, 요청까지의 조작 수를 기록한다.
- [ ] 문의 전송·확인 중·질문·수용 응답을 두 화면에서 구분한다.
- [ ] 한 병원 확정 후 다른 병원은 문의 종료를 인지하고 검토 작업을 종료한다.
- [ ] 조건 변경 알림의 수신·인지 확인 후에도 병원의 유지/조정 판단은 별도로 남는다.
- [ ] 탭 숨김·연결 중단·복귀 시 과거 미확인 사건이 다시 나타난다.
- [ ] 통화 메모의 수용 가능 전달과 상대측 내용 확인만으로 목적지가 정해지지 않는다.
- [ ] 정정 원문과 새 기록을 구분하고 다른 병원의 메모는 보이지 않는다.
- [ ] 작업 시작·종료·재전송이 중복 저장되지 않고 미종료·지연 종료를 정상 표본으로 숨기지 않는다.
- [ ] 환자·병원 전환 후 이전 요청 결과가 새 화면에 섞이지 않는다.

서버 계약은 정보 수정·전체 통화함을 포함해 관련 445개 테스트를 통과했다. 화면 연계와 실제 사용성 개선은 별도 검증 대상이다. 각 담당자는 변경 화면 캡처, 사용한 `pid`·`hid`, 수행 순서, 입력/조작 시간, 남은 오류를 함께 전달한다.

## 9 바로 전달할 요약

### 김민석에게

구급대원 화면은 입력과 상단 진행 요약을 단순화하고, 중요 사건 수신·확인과 탭 복귀 복구, 실제 작업 계측, 통화 결과 기록을 연결해 주세요. 문의·확정·채팅·조건 재확인은 이미 연결돼 있으니 기존 흐름을 활용하면 됩니다. API 기본 범위는 `/api/patients/{pid}`이고 신규 경로는 `important-events`, `work-activities`, `call-records`입니다. 수용·배정은 기존 업무 API로 처리하고 알림·통화 내용 확인과 구분해 주세요.

### 김준성에게

병원 화면은 환자 요약과 응답 버튼을 먼저 보이게 정리하고, 문의 취소 인지·검토 종료, 중요 사건 수신·확인, 실제 검토 계측, 통화 기록 조회·내용 확인을 연결해 주세요. 수용 제한 관리·종료 문의 버튼 차단·유지/조정·일반 환자 퇴실은 이미 구현돼 있습니다. 신규 API 범위는 `/api/hospitals/{hid}/patients/{pid}`이며 수용 응답은 기존 `/api/inquiries/{iid}/respond`를 사용합니다. 다음 단계는 외부 유입 목록·개별 퇴실과 추천 패널입니다.

## 10 상세 명세

- [공통 업무 API](API.md)
- [업무 계측과 중요 사건 확인](BACKEND_MEASUREMENT_API.md)
- [통화 기록과 상대 기관측 내용 확인](CALL_RECORD_API.md)
- [문의 이후 환자정보 수정](PATIENT_AMENDMENTS_API.md)
- [대화와 참여 구간](CHAT_API.md)
- [조건 재확인과 대체 문의](TRANSPORT_REVIEW.md)
- [추천 연결](TEAM_RECOMMENDATION_INTEGRATION.md)
- [원본 입력 앱과 공통 서버 연결](TEAM_PATIENT_CONNECTION_20261013.md)

기존 DB를 보존하려면 같은 DB 경로·포트로 서버를 재시작하고 `--seed`를 사용하지 않는다. 신규 기능 테스트에는 별도 가상 사례를 사용한다.
