# 응답 관측과 업무 계측 및 중요 사건 확인 API

2026년 10월 29일. 구급대원 앱과 병원 웹 담당자를 위한 서버 연결 계약이다. 서버와 테스트를 추가했으며 팀원 화면은 수정하지 않았다. 화면에서 API를 연결해야 실제 사용 기록이 쌓인다.

구현 범위는 첫 확인과 최종 결정의 분리, 등록된 이송 건별 업무 시작·종료 기록, 중요 사건의 수신·사용자 확인 기록이다. `actor_id`는 시연 클라이언트가 선언하는 작업자 식별자이며 로그인 인증이 아니다. 실제 사용자 인증과 접근 권한은 외부 배포 전에 별도로 적용해야 한다.

## 1 응답 관측 변경

`server/inquiry_observations.capture()`는 버전 2 반환 계약을 사용한다. 원래 병원 응답·수용 유효성은 바꾸지 않는다.

| 상황 | 알고리즘용 observations | 별도 attempts 단계 기록 |
|---|---|---|
| 확인 중 후 수용 | 최종 `accept`와 최종 시각 1개 | 확인 중과 수용 시각 보존 |
| 질문 후 불가 | 최종 `decline`과 최종 시각 1개 | 각 질문과 불가 시각 보존 |
| 확인 중이지만 결정 없음 | 결정 대기 제한을 지나면 `timeout` 1개 | 확인 중·미해결 상태 보존 |
| 제한시간 뒤 수용 | 과거 `timeout` 유지 | 실제 늦은 수용과 시각 보존 |
| 제한시간 전 취소·재문의 | 결정 실패 표본으로 세지 않음 | 시도 구분·종료 사유 보존 |

`timeout`은 결정 대기의 관측이며 거절이 아니다. 실제 문의는 기존 규칙에 따라 미해결로 남을 수 있다. 기준 180초와 관측창 1,800초는 실험 설정값이다.

반환에 `schema_version: 2`, `observation_basis: "final_decision"`, `attempts`를 추가했다. 시도별 `inquiry_id`, `attempt_event_id`, `submitted_at`, `first_reply_at`, `first_checking_at`, `first_question_at`, `decision_at`, `decision`, `ended_at`, `end_reason`, `decision_deadline_at`, `unresolved`, `stages`를 제공한다. 단계에는 사건 ID·단계·시각만 보존하고 질문·답변 본문과 처리자 이름은 넣지 않는다.

소비 정책도 과거 `reply`를 최종 결정으로 사용하지 않는다. `observed_v2`, `observed_v3`와 관측형 연구 변형의 정책 버전은 `2`다. 기본 `fixed_3`와 고정 1·5곳의 버전·문의 방식은 유지한다.

**기존 버전 주의:** 버전 1 명세로 고정된 관측형 작업은 `POLICY_VERSION_MISMATCH` 409로 거부될 수 있다. 자동 승격하지 않으며 단순 서버 재시작이나 같은 작업의 일시정지·재개가 이관 방법은 아니다. 기존 이력은 보존하고 버전 2 검증은 새 가상 환자 건으로 시작한다. 기존 건의 전환은 별도 이관 절차를 검토해야 한다. 과거 실험 원문·해시는 수정하지 않았으며 변경 전후 결과를 같은 실행으로 취급하지 않는다.

## 2 공통 경로

아래 경로를 역할별 `BASE`로 사용한다.

```text
구급대원 BASE = /api/patients/{pid}
병원 BASE     = /api/hospitals/{hid}/patients/{pid}
```

병원은 해당 건의 문의·목적지·대화 참여 등 관계가 있어야 한다. 중요 사건은 자기 문의와 실제 참여 구간으로 추가 제한한다. 시각은 서버가 기록한 UTC epoch 초 정수다.

10월 29일 통화 기록 연계: [명시적으로 공유된 통화 기록](CALL_RECORD_API.md)의 상대 병원도 해당 건의 업무 계측을 사용할 수 있다. 이 관계가 병원 대화 참여나 중요 사건 조회 권한을 추가하지는 않는다. 통화 메모의 보고된 통화 기간은 활동시간에 자동 합산하지 않는다.

신규 API의 빈 필터와 중복 쿼리 키는 400으로 거부한다. 예를 들어 `actor_id=`를 작업자 필터 생략으로 처리하거나 `since=`를 전체 기간으로 확대하지 않는다.

작업자는 두 API에서 일관된 불투명 ID(예: `crew-demo-1`, `hospital-desk-1`)를 사용한다. 환자 이름·전화번호를 ID로 쓰지 않는다.

## 3 업무 계측 API

| 기능 | 경로 |
|---|---|
| 시작 | `POST {BASE}/work-activities` |
| 종료 | `POST {BASE}/work-activities/{activity_id}/stop` |
| 목록 | `GET {BASE}/work-activities?actor_id=...&limit=50` |
| 집계 | `GET {BASE}/work-activities/summary?actor_id=...` |

시작 본문:

```json
{
  "actor_id": "hospital-desk-1",
  "client_activity_id": "review-001",
  "kind": "request_review"
}
```

`client_activity_id`는 시작 전 한 번 생성해 재시도에도 유지한다. 같은 키로 다른 환자나 종류를 기록하면 409다. 시작·종료 모두 서버 시각으로 기록하며 클라이언트 시각을 지정하는 필드는 없다.

| kind | 용도 |
|---|---|
| `patient_input` | 등록된 건의 환자 정보 입력·보완 |
| `request_view` | 문의·진행 상황 확인 |
| `request_review` | 환자 요청 검토 |
| `respond` | 응답 작성 |
| `resource_update` | 해당 건 관련 자원 정보 수정 |
| `additional_question` | 추가 질문·답변 처리 |
| `recheck` | 수용 조건 재확인 |
| `chat` | 보충 대화 |
| `call` | 해당 건 관련 통화 |

시작·종료 응답은 `{ "activity": { ... }, "duplicate": false, "scope": { ... } }`다. `activity`에는 ID·범위·종류·`started_at`·`ended_at`·`state`·`completed_seconds`·`open_elapsed_seconds`가 있다. 재시도는 최초 기록과 `duplicate: true`를 반환한다.

종료 본문:

```json
{ "actor_id": "hospital-desk-1" }
```

환자·역할·병원·작업자가 원래 기록과 일치해야 한다. 다른 담당자의 활동을 종료할 수 없고, 반복 종료가 종료 시각을 늘리지 않는다.

`actor_id`는 최대 80자, `client_activity_id`는 최대 128자다. 영문·숫자로 시작하며 이후 영문·숫자·`. _ : @ -`를 사용한다. 환자 본문·통화 내용 같은 추가 필드를 보내면 400이다.

### 목록과 집계

- 목록은 `actor_id` 필수다. HTTP `limit`은 1~100, 기본 50. 최신 ID부터 반환하며 `has_more`가 참이면 `next_before_id`를 다음 `before_id`로 보낸다.
- 집계에서 `actor_id`를 생략하면 해당 **환자·역할·병원 범위**의 작업자 결과를 합한다. `since`, `until`은 `[since, until)`이며 `since <= until <= 서버 현재 시각`이어야 한다.
- 같은 작업자의 겹치는 완료 구간은 합집합으로 계산한다. 0~30초 검토와 10~40초 통화는 단순합 60초, 활동시간 40초다.
- 다른 작업자의 시간은 인·초(person-seconds)로 합한다. 두 명이 각 30초 작업하면 60인·초이며 경과시간 60초라는 뜻은 아니다.

| 집계 필드 | 의미 |
|---|---|
| `totals.completed_raw_seconds` | 완료 구간 단순합 |
| `totals.completed_active_seconds` | 작업자별 중첩 제거 후 합계 |
| `totals.completed_overlap_seconds` | 단순합의 중복 시간 |
| `totals.open_activity_count` | 미종료 작업 수 |
| `totals.open_elapsed_union_seconds` | 열린 작업의 현재까지 경과 구간. 완료 시간과 별도 |
| `actors` | 작업자별 결과와 종류별 단순합 |
| `measurement` | 측정 방법과 범위 한계 |

**범위 한계:** 이미 등록된 환자 건 단위이므로 최초 등록 전 입력시간과 병원 전체 업무량은 측정하지 않는다. 여러 환자 사이의 중복 시간도 제거하지 않는다. 열린 창·누락된 종료를 완료 작업으로 확정하지 않고 자동 문의 대기시간을 활동시간으로 추정하지 않는다. 종류별 단순합은 겹칠 수 있다.

## 4 중요 사건 API

| 기능 | 경로 |
|---|---|
| 새 중요 사건 조회 | `GET {BASE}/important-events?actor_id=...&after_id=0&limit=100` |
| 과거 미확인 재조회 | `GET {BASE}/important-events?actor_id=...&unacknowledged_only=true` |
| 화면 수신 기록 | `POST {BASE}/important-events/{event_id}/delivered` |
| 담당자 확인 기록 | `POST {BASE}/important-events/{event_id}/acknowledge` |

두 POST의 본문은 모두 `{ "actor_id": "hospital-desk-1" }`다.

시각의 의미는 다음과 같다.

1. `at`: 원래 업무 사건이 서버에 등록된 시각.
2. `delivered_at`: 클라이언트가 받아 표시할 수 있게 된 뒤 수신 POST를 호출한 서버 시각.
3. `acknowledged_at`: 담당자가 확인 동작을 수행한 뒤 확인 POST를 호출한 서버 시각.

GET은 수신 기록을 만들지 않는다. 수신 기록 없이 확인하면 `DELIVERY_REQUIRED` 409다. 반복 POST는 최초 시각을 유지한다. 확인은 알림을 인지했다는 기록이며 **의료적 재확인·수용·목적지 변경을 뜻하지 않는다.** 해당 업무는 기존 API를 사용한다.

조회 결과 형식 예시:

```json
{
  "items": [{
    "event_id": 123,
    "type": "transport_recheck_required",
    "at": 1790000030,
    "patient_id": "A",
    "hospital_id": "H1",
    "summary": "변경된 조건이 이 환자에게 영향을 줄 수 있어 재확인이 필요합니다.",
    "delivered_at": null,
    "acknowledged_at": null,
    "delivery_delay_seconds": null,
    "acknowledgement_delay_seconds": null
  }],
  "has_more": false,
  "next_after_id": 123,
  "scope": { "patient_id": "A", "role": "hospital", "hospital_id": "H1", "actor_id": "hospital-desk-1" },
  "unacknowledged_only": false
}
```

예시는 실제 조회 결과가 아니다. 질문 본문·원시 사건 데이터는 반환하지 않는다. POST는 `{ "event": { ... }, "duplicate": false }`를 반환한다.

일반 조회는 `next_after_id`를 다음 `after_id`로 사용한다. 복귀·재접속 때는 별도로 `unacknowledged_only=true`를 조회해 오래된 미확인 사건을 복구한다. 미확인 모드의 `next_after_id`는 `null`이며 오래된 미확인부터 최대 `limit`개를 반환한다. 확인 후 다시 조회하면 다음 건이 나온다. 양수 `after_id`와 미확인 모드를 함께 보내면 400이다. `limit`은 HTTP에서 1~100, 기본 100이다.

중요 사건은 문의 취소·종료·응답 만료·추가 질문, 목적지 확정·변경·이전 병원 참여 종료, 이송 취소, 조건 재확인 필요·위험 해소, 조정 요청, 대체 탐색 요청·수용 확보·탐색 종료다. 수용 응답 자체는 기존 문의·대화 API로 확인한다.

병원은 자기 문의와 자기 대화 참여 구간에 해당하는 사건만 받는다. 병원 ID가 없는 공통 사건도 자기 참여 구간 안에서만 보인다. 참여 종료 후 공통 사건이나 다른 병원을 명시한 사건은 노출하지 않는다.

**전달 범위:** OS 푸시·백그라운드 전송·재알림 스케줄러를 구현한 것은 아니다. 화면의 수신·확인 연결 후 필요에 따라 별도 전달 방식을 추가한다. 표시된 지연은 원래 사건 시각부터 수신·확인 POST까지다.

## 5 팀원 연결과 실행

| 담당 | 후속 연결 |
|---|---|
| 구급대원 앱 | 등록 후 입력·확인·통화 작업 시작/종료, 사건 표시 후 수신 POST, 확인 동작 후 확인 POST, 복귀 시 미확인 재조회 |
| 병원 웹 | 실제 문의 검토 시 시작, 응답·재확인 종료 시 종료, 취소·조건 변경 표시와 수신/확인 연결 |
| 서버·검증 | 계측과 업무 상태 분리, 모의 과업에서 지연·중첩·미종료·오류 측정 |

창이 열려 있는 것만으로 작업 중으로 측정하지 않는다. 처치나 통화로 자리를 비우는 경우를 포함해 시작·종료 의미를 팀에서 합의한다. 계측용 조작을 늘리지 않도록 기존 화면 동작에 자동 연결한다.

새 테이블은 `work_activities`, `important_event_receipts`다. `db.init()`이 기존 데이터를 삭제하지 않고 추가하며 반복 초기화에도 기록을 보존한다. 계측·확인은 환자 상태·목적지·수용 조건을 바꾸거나 배정 재계산을 유발하지 않는다.

기존 서버 프로세스는 코드 재시작이 필요하다. **기존과 같은 DB 경로·포트로 재시작하고, 데이터 보존이 필요하면 `--seed`를 사용하지 않는다.** 운영 기본값과 과거 실험 결과는 유지했다.

테스트는 임시 DB·가상 환자·로컬 임시 포트를 사용하며 외부 병원이나 공공·지도 API를 호출하지 않는다.

```text
python scripts/test_suites.py python test_inquiry_observations test_work_activity test_important_events test_backend_measurement_api -v
```

Windows 기본 임시 경로에 SQLite 쓰기 권한이 없으면 쓰기 가능한 테스트 전용 임시 경로를 지정한다. 테스트 통과는 서버 계약 검증이며 현장 업무량 감소나 임상 성과 검증을 뜻하지 않는다.

2026년 10월 29일 검증 결과: 신규 테스트 63개를 포함해 관련 18개 모듈의 360개 테스트가 모두 통과했다. 첫 확인·최종 결정, 동시 재시도, 중첩 구간, 미종료 작업, 타 범위 접근, 중요 사건 가시성, 재접속 미확인 복구, 빈·중복 필터, 과도하게 긴 ID, 기존 문의·이송·대화 흐름을 검증했다. 팀원 화면 19개 파일은 작업 시작 시점과 동일하다.
