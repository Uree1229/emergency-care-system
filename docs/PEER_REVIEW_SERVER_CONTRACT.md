# 피어리뷰 대응 서버 계약 초안 (R3·R5·R6·R8)

작성일: 2026년 10월 29일 · 상태: **팀 합의 전 초안** · 근거: [피어리뷰 분석과 개선 계획](reviews/피어리뷰_분석과_개선계획_20261028.md) 6절·9절

구급대 앱·병원 웹이 서버와 주고받을 기록 규칙을 정한다. 대상은 통화 기록(R3), 중요 사건 확인(R5), 모름·미응답 표시(R6), 업무 계측(R8)이다. 이 문서는 서버 동작을 바꾸지 않는다. 인증·권한(R7)은 별도 문서로 다루며, 그 전까지 아래의 `actor_id`는 화면이 선언하는 시연용 식별자다.

## 0. 한눈에 보기

| ID | 요구 | 서버 상태 (10-29 오전) | 화면 작업 | 팀이 정할 것 |
|---|---|---|---|---|
| R3 | 통화와 시스템 기록 연결 | **제안(미구현).** 2절 | 통화 결과 기록·상대 확인 | 기록 주체, 통화 수용의 처리 |
| R5 | 중요 변경 알림·확인·복구 | **구현 중(Codex, 미커밋).** `important-events` API. 3절 | 수신·확인 호출, 미확인 유지 | 확인 필수 사건, 재알림 방식 |
| R6 | 모름·미응답 명확 표시 | 서버 값은 **구현됨.** 표시 규칙을 4절로 확정 | 두 화면에 표시 규칙 적용 | 화면 문구 |
| R8 | 업무 계측 | **구현 중(Codex, 미커밋).** `work-activities` API. 5절 | 작업 시작·종료 호출 | 작업별 측정 정의, 기준선 일정 |

`구현 중` 항목은 10-29 오전 작업 폴더의 [`server/important_events.py`](../server/important_events.py), [`server/work_activity.py`](../server/work_activity.py) 기준이다. 커밋 전이라 세부가 바뀔 수 있으며, 커밋되면 [API 명세](API.md)에 옮긴다.

## 1. 공통 원칙

1. **업무 상태는 구조화된 업무 API만 바꾼다.** 수용·불가·확인 중·추가 질문·확정·변경·취소가 여기에 해당한다. 통화 기록, 자유 대화, 알림 확인은 문의·목적지 상태를 바꾸지 않는다.
2. **세 가지 시각을 구분한다.** 서버에 사건이 기록된 시각, 화면에 실제로 표시된 시각(수신), 담당자가 인지했다고 누른 시각(확인)이다.
3. **모름·미응답·오래됨을 '가능'이나 '불가'로 바꾸지 않는다.** 확인된 부적합과 추가 확인 필요를 따로 표시한다.
4. **사람이 실제 일한 시간과 시스템 대기 시간을 구분한다.** 대기 시간으로 업무량을 추정하지 않는다.
5. **시각은 서버 시각(UTC epoch 초)이다.** 화면이 보낸 시각은 '보고값'으로 따로 저장한다.
6. **모든 쓰기 요청은 화면이 만든 요청 ID로 중복을 막는다.** 기존 `client_message_id`와 같은 방식이다.
7. **`actor_id`는 인증이 아니다.** R7 전까지는 `medic-01`, `er-02` 같은 가명이다. 계측 자료는 개인 평가에 쓰지 않는다.

## 2. R3 통화 기록 (제안, 미구현)

### 2.1 목적

긴급 확인은 지금처럼 전화로 한다. 시스템은 **통화 결과를 같은 이송 건에 한 번 기록하고, 상대가 확인**하게 해서 재설명·재문의·중복 기록을 줄인다. 통화 녹음, 자동 요약, 의료 판단은 범위 밖이다.

### 2.2 저장 항목

| 필드 | 설명 |
|---|---|
| `id` | 서버 번호 |
| `patient_id` | 이송 건(환자) |
| `hospital_id` | 통화 상대 병원 |
| `inquiry_id` | 관련 문의(없으면 비움) |
| `recorder_role`, `recorder_actor_id` | 기록한 쪽(`paramedic`·`hospital`)과 담당자 가명 |
| `direction` | `outgoing`(기록자가 걸었음) 또는 `incoming`(받았음) |
| `reported_started_at`, `reported_ended_at` | 화면이 보고한 통화 시각(선택, 보고값) |
| `recorded_at` | 서버가 저장한 시각 |
| `outcome` | 결과 분류(2.3) |
| `note` | 짧은 메모(200자 이내). 환자 서술 전문이나 통화 내용 전사는 넣지 않는다 |
| `client_call_id` | 화면이 만든 요청 ID(중복 방지) |
| `counterpart_status` | `UNCONFIRMED` → `CONFIRMED` 또는 `DISPUTED` |
| `counterpart_actor_id`, `counterpart_at`, `dispute_note` | 상대의 확인·이의 기록 |

### 2.3 결과 분류와 후속 동작

어떤 결과도 서버의 문의·목적지 상태를 바꾸지 않는다. 대신 화면이 해당 업무 API로 이어 준다.

| `outcome` | 의미 | 화면 후속 동작(기존 API) |
|---|---|---|
| `info_shared` | 환자 정보를 전달함 | 없음 |
| `question_answered` | 병원 질문에 전화로 답함 | 구급대: 같은 답을 `answer`로 등록할지 묻기 |
| `acceptance_stated` | 병원이 전화로 수용 의사를 밝힘 | 병원: "통화로 수용하셨나요? [수용 응답 등록]"을 한 번에 처리(`respond` accept). 구급대: 병원 응답 전까지 "통화 수용 · 시스템 응답 대기"로 표시하고 확정 버튼은 끈다 |
| `decline_stated` | 병원이 전화로 불가를 밝힘 | 병원: [불가 응답 등록(사유 선택)]. 구급대: 자기 문의를 `cancel`할 수 있음 |
| `callback_promised` | 다시 연락하기로 함 | 대기 표시 |
| `no_answer` | 연결되지 않음 | 재문의·다른 후보 안내 |
| `other` | 기타 | 없음 |

권장 기본값: **목적지 확정에는 지금처럼 병원의 시스템 수용 응답(`usable: true`)이 필요하다.** 통화 수용만으로 확정하는 예외 절차를 둘지는 팀이 정한다(7절 2번).

### 2.4 확인 절차

1. 한쪽이 통화 결과를 기록한다. 기본은 전화를 건 쪽이다.
2. 상대 화면의 대화 타임라인에 표시된다. `acceptance_stated`·`decline_stated`는 중요 사건(3절)으로도 보내 확인을 받는다.
3. 상대는 `확인` 또는 `이의`를 누른다. 이의가 있으면 두 기록을 모두 남기고, 업무 응답으로 정리한다.

### 2.5 API (제안)

기존 경로 규칙을 따른다. 병원 경로는 문의 또는 대화 참여 이력이 있는 병원만 쓸 수 있다.

| 메서드 | 경로 | 용도 |
|---|---|---|
| POST | `/api/patients/{pid}/calls` | 구급대가 통화 결과 기록 |
| POST | `/api/hospitals/{hid}/patients/{pid}/calls` | 병원이 통화 결과 기록 |
| GET | 위 두 경로 | 해당 이송 건의 통화 기록 목록(대화 타임라인에도 포함) |
| POST | `.../calls/{cid}/confirm` | 상대가 확인 |
| POST | `.../calls/{cid}/dispute` | 상대가 이의(`dispute_note` 필수) |

요청 예(구급대):

```json
{"actor_id": "medic-01", "client_call_id": "c-20261029-001", "hospital_id": "H01",
 "inquiry_id": 12, "direction": "outgoing", "outcome": "acceptance_stated",
 "note": "CT 가능, 5분 내 도착 요청", "reported_started_at": 1793232000}
```

### 2.6 기존 기능과의 연결

- **대화:** 타임라인에 `kind: "call"` 항목으로 넣는다. 자유 메시지와 시스템 사건 사이의 구조화된 기록이다.
- **사건 기록:** `events`에 `call_recorded`, `call_confirmed`, `call_disputed`를 남긴다.
- **중요 사건:** `call_recorded` 중 `acceptance_stated`·`decline_stated`를 R5 확인 대상에 추가한다.
- **업무 계측:** 통화에 쓴 시간은 R8의 `kind: "call"`로 따로 잰다. 계측 원장에는 통화 내용을 넣지 않는다.

## 3. R5 중요 사건 수신·확인 (구현 중, Codex 미커밋)

### 3.1 현재 서버 계약

| 메서드 | 경로(병원은 `/api/hospitals/{hid}/patients/{pid}/...`) | 동작 |
|---|---|---|
| GET | `/api/patients/{pid}/important-events?actor_id=&after_id=&limit=` | 커서 이후 중요 사건 목록. 조회만으로 수신·확인을 만들지 않음 |
| GET | `...?actor_id=&unacknowledged_only=true` | 미확인 사건만 오래된 순으로 다시 조회(복귀·재접속용). `after_id`와 함께 쓰지 않음 |
| POST | `.../important-events/{eid}/delivered` (`{"actor_id"}`) | 화면에 실제 표시한 뒤 호출. 최초 시각만 보존 |
| POST | `.../important-events/{eid}/acknowledge` (`{"actor_id"}`) | 담당자가 확인을 누른 뒤 호출. 수신 기록이 없으면 409 `DELIVERY_REQUIRED` |

- **응답:** 각 항목에 `delivered_at`, `acknowledged_at`, `delivery_delay_seconds`, `acknowledgement_delay_seconds`가 있다. R5 완료 기준(사건 등록 → 표시 → 확인 시각)을 그대로 계산할 수 있다.
- **확인의 의미:** 인지 기록일 뿐이다. 수용 유지, 조정 필요, 목적지 변경은 각각의 업무 API로 따로 처리한다.
- **병원 범위:** 문의 관련 사건은 문의를 받은 병원에, 나머지는 대화 참여 구간의 병원에만 보인다. 관련 없는 병원은 403 `NOT_PARTICIPANT`다.

대상 사건(14종): `inquiry_cancelled`, `inquiry_closed`, `inquiry_expired`, `inquiry_question`, `destination_confirmed`, `destination_changed`, `patient_redirected_away`, `patient_cancelled`, `transport_recheck_required`, `transport_conditions_restored`, `adjustment_needed`, `transport_alternative_requested`, `transport_inquiry_ready`, `transport_inquiry_exhausted`. 화면 문구는 서버의 `summary`를 그대로 쓴다.

### 3.2 화면 동작 규칙(제안)

1. 보고 있는 이송 건은 기존 폴링 주기(2~3초)에 맞춰 `after_id` 커서로 새 사건을 받는다.
2. 화면을 열 때, 숨겨진 탭으로 돌아올 때, 재접속할 때는 `unacknowledged_only=true`로 미확인 사건을 복구한다. 지금 구급대 화면은 탭이 숨겨지면 갱신을 멈추므로 이 규칙이 필요하다.
3. `delivered`는 실제로 화면에 그렸을 때만 보낸다. 백그라운드에서 받기만 한 경우는 보내지 않는다.
4. `acknowledge`는 담당자가 확인 버튼을 눌렀을 때만 보낸다. 시간이 지났다고 자동으로 보내지 않는다.
5. 확인 전까지 상단 강조를 유지한다. 같은 사건의 재알림은 횟수와 간격을 제한한다(7절 4번).
6. 운영체제 푸시는 이번 범위에 넣지 않는다. 서버 기록과 사용자 인지는 따로 다룬다.

## 4. R6 모름·미응답 표시 규칙 (서버 값은 구현됨, 표시 규칙 제안)

서버는 이미 상태와 근거를 구분해서 내려준다. 화면이 이를 하나의 '가능/불가'로 합치지 않도록 표시 규칙을 고정한다. 문구는 예시이며 팀이 다듬는다.

### 4.1 문의 상태

| 서버 값 | 구급대 화면 | 병원 화면 | 쓰면 안 되는 표현 |
|---|---|---|---|
| `PENDING`, `overdue: false` | 응답 대기 (경과 시간) | 새 문의 | — |
| `PENDING`, `overdue: true` (60초 경과) | **미응답** (경과 시간, 강조) | 미응답 강조 | 불가, 거절 |
| `CHECKING`, 질문 없음 | 병원 확인 중 | 확인 중 | 수용 예정 |
| `CHECKING`, `question` 있음, `answer` 없음 | 병원 질문 있음 · 답변 필요 | 답변 대기 | — |
| `ACCEPTED`, `usable: true` | 수용 (유효 시간 남음) | 수용함 (만료 시각) | 확정 |
| `ACCEPTED`, `usable: false` | 환자 정보 변경 · 재확인 필요 | 재확인 요청됨 | 수용 |
| `EXPIRED` | 수용 만료 · 재확인 필요 | 만료됨 | 수용 |
| `DECLINED` + `reason` | 불가 (사유) | 불가 응답함 | — |
| `CANCELLED` + `reason`, `responded_at` 없음 | 응답 전 종료 (사유) | 취소됨 · 검토 중지 | 불가 |
| `CANCELLED` + `reason`, 응답 후 | 종료 (사유) | 취소됨 · 검토 중지 | — |
| `CONFIRMED` | 목적지 확정 | 확정 환자 | — |

### 4.2 병원 정보와 환자 정보

| 서버 값 | 표시 | 주의 |
|---|---|---|
| 치료조건 `MATCH` | 등록 정보상 조건 충족 | 실제 수용은 병원 응답으로만 확인 |
| `NEEDS_CONFIRMATION` + `unknown[]` | 확인 필요: (항목) | 불가로 표시하지 않음 |
| `EXCLUDED` | 등록 정보상 부적합 | 병원의 불가 응답과 다른 문구 사용 |
| `stale: true`, `PUBLIC_DATA_STALE` | 공공정보 오래됨 (갱신 시각) | — |
| `capabilities_status: unknown`, `CAPABILITIES_UNVERIFIED` | 진료 역량 미확인 | — |
| 자원 `status: unknown`, `OPERATING_RESOURCE_UNKNOWN` | 병상 정보 미확인 | 0으로 표시하지 않음 |
| 자원 갱신 후 600초 경과, `OPERATING_RESOURCE_STALE` | 병상 정보 오래됨 (n분 전) | — |
| `RESOURCE_CAPACITY_INSUFFICIENT` | 여유 부족 (확인된 값) | — |
| `priority_source`가 `self`·`unknown`, `PRIORITY_UNVERIFIED` | 중증도 미확인 (자기신고·미확인) | — |

추천 결과의 `eligibility_reasons` 코드와 `NO_VALID_ACCEPTED_HOSPITAL`·`NO_ELIGIBLE_ACCEPTED_HOSPITAL`의 구분은 [API 명세 10절](API.md)을 따른다.

### 4.3 서버 쪽 보강

새 필드는 필요 없다. 위 표의 조건은 모두 기존 필드로 계산된다. 대신 표의 각 행을 만드는 합성 시나리오를 서버 테스트로 고정해, 화면이 같은 기준으로 확인할 수 있게 한다(6절).

## 5. R8 업무 계측 (구현 중, Codex 미커밋)

### 5.1 현재 서버 계약

| 메서드 | 경로(병원은 `/api/hospitals/{hid}/patients/{pid}/...`) | 동작 |
|---|---|---|
| POST | `/api/patients/{pid}/work-activities` (`{"actor_id","client_activity_id","kind"}`) | 작업 시작. 같은 요청 ID로 다시 보내면 원래 시작 시각을 유지 |
| POST | `.../work-activities/{aid}/stop` (`{"actor_id"}`) | 작업 종료(서버 시각). 반복 호출해도 안전 |
| GET | `.../work-activities?actor_id=&limit=&before_id=` | 담당자별 기록 목록 |
| GET | `.../work-activities/summary?actor_id=&since=&until=` | 담당자별 합계 |

- **작업 종류(`kind`):** `patient_input`, `request_view`, `request_review`, `respond`, `resource_update`, `additional_question`, `recheck`, `chat`, `call`
- **합계 규칙:** 같은 담당자의 겹친 구간은 한 번만 센다. 끝나지 않은 작업은 완료 시간에 넣지 않는다. 여러 담당자의 합은 사람·초 단위다.
- **범위의 한계:** 환자 한 명 단위다. 같은 담당자가 여러 환자를 동시에 처리한 겹침은 빼지 않는다.
- **원장에 넣지 않는 것:** 환자 서술, 메시지 본문, 통화 내용

### 5.2 작업별 측정 정의(제안)

| `kind` | 시작 | 종료 | 화면 |
|---|---|---|---|
| `patient_input` | 환자 정보 입력 화면 진입 | 최초 문의 요청 전송 성공 | 구급대 |
| `request_view` | 문의 상세 열기 | 상세 닫기 또는 응답 시작 | 병원 |
| `request_review` | 근거·자원 확인 영역 진입 | 확인 영역 닫기 또는 응답 시작 | 병원 |
| `respond` | 응답 버튼·사유 선택 시작 | 응답 전송 성공 | 병원 |
| `resource_update` | 자원 수정 시작 | 저장 성공 | 병원 |
| `additional_question` | 질문(병원) 또는 답변(구급대) 작성 시작 | 전송 성공 | 양쪽 |
| `recheck` | 재확인 요청 열기 | 유지·조정 응답 또는 확인 완료 | 양쪽 |
| `chat` | 대화 입력 시작 | 전송 | 양쪽 |
| `call` | 통화 시작 버튼 | 통화 종료 또는 통화 기록 저장 | 양쪽 |

공통 규칙: 탭이 숨겨지면 진행 중인 작업을 종료한다. 열린 채 남은 작업은 서버가 완료 시간에서 빼므로 자동 보정하지 않는다.

### 5.3 지표 계산

- **구급대 입력 활동시간:** `patient_input` 합계
- **병원 총 활동시간:** 병원·환자별로 모든 `kind`를 담당자 기준으로 합친 시간. 피어리뷰 문서 7절 공식의 구성 요소와 같다.
- **계측하지 않는 것:** 조작 수, 오입력, 상태 이해 정답률. 사용성 평가에서 관찰자가 기록한다(피어리뷰 문서 8.1절).

## 6. 완료 확인

| ID | 서버 테스트 | 화면·평가 확인 |
|---|---|---|
| R3 | 통화 기록 후에도 문의·목적지 상태가 그대로임. 기록자·상대·시각·확인 상태 보존. 같은 요청 ID 중복 저장 없음. 관련 없는 병원 접근 거부 | 통화 수용 뒤 병원이 한 번에 수용 응답을 등록. 확정은 시스템 수용 응답 뒤에만 가능 |
| R5 | 조회만으로 수신·확인이 생기지 않음. 수신 전 확인 거부. 확인해도 업무 상태 불변. 미확인 재조회 유지 | 숨김 탭·재접속 후 미확인 강조 복구. 사건 → 표시 → 확인 시각 측정 |
| R6 | 4절 각 행의 상태를 만드는 합성 시나리오 고정 | 모름·미응답·만료가 가능/불가로 표시되지 않음 |
| R8 | 시작 재시도 시 원래 시각 유지, 겹침 1회 계산, 미완료 제외, 내용 필드 없음 | 기준선과 개선 화면에서 같은 과업의 활동시간 비교 |

R5·R8의 서버 테스트는 Codex 구현이 커밋될 때 함께 들어오는지 확인한다.

## 7. 팀 회의에서 정할 것

1. **통화 기록 주체:** 기본을 '전화를 건 쪽이 기록, 상대가 확인'으로 할지
2. **통화 수용 처리:** 권장안은 '확정에는 시스템 수용 응답 필요'다. 통화 수용만으로 확정하는 예외를 둘지
3. **확인 필수 사건:** R5 14종 전부와 통화 수용·불가 기록을 확인 필수로 할지
4. **재알림 방식:** 같은 사건의 강조 반복 간격과 최대 횟수(예: 30초, 3회)
5. **측정 정의:** 5.2 표의 시작·종료 기준과 기준선 측정 일정
6. **가명 규칙:** R7 전까지 쓸 `actor_id` 형식과 계측 자료 보관 범위

## 8. 진행 순서 제안

1. Codex의 R5·R8 구현을 마무리해 커밋하고 [API 명세](API.md)에 반영한다.
2. 팀 회의에서 7절을 정한다.
3. R3 통화 기록을 서버에 구현하고 테스트한다. `server/`가 바뀌므로 기존 잠금 실험은 새 이름으로 재검증하고 이전 보고서는 보존한다.
4. 팀원이 두 화면에 R3·R5·R6·R8을 연결한다.
5. 기준선 측정 → 개선 화면 재측정으로 효과를 비교한다.

## 9. 근거

- 문의 상태와 응답: [`server/service.py`](../server/service.py) `respond`, `cancel_inquiry`, `answer_question`, [API 명세 3절](API.md)
- 대화 타임라인: [`server/conversations.py`](../server/conversations.py). 자유 메시지는 수용·확정을 바꾸지 않는다.
- 치료조건 판정: [`server/treatment_data.py`](../server/treatment_data.py) (`MATCH`·`NEEDS_CONFIRMATION`·`EXCLUDED`), [`server/treatment_binding.py`](../server/treatment_binding.py)
- 추천 근거 코드: [`server/solver_adapter.py`](../server/solver_adapter.py) `eligibility_reasons`
- 구현 중: [`server/important_events.py`](../server/important_events.py), [`server/work_activity.py`](../server/work_activity.py) (10-29 오전 미커밋)
