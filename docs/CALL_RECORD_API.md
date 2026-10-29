# 통화 결과 기록과 상대 기관 내용 확인 API

2026년 10월 29일. 구급대원과 병원이 같은 이송 건에 통화 결과를 한 번 기록하고 공유하기 위한 서버 API다. 실제 전화 연결·통화 녹음·자동 요약 기능은 포함하지 않는다. 화면 연결은 각 담당자의 후속 작업이다.

## 1 업무 상태와의 구분

| 구분 | 뜻 | 수용·이송 상태에 미치는 영향 |
|---|---|---|
| `reported_outcome` | 작성자가 전화에서 전달받았다고 기록한 결과 | 없음 |
| 상대 기관측 `confirm` 또는 `dispute` | 기록 내용에 동의하거나 이의가 있음을 기록 | 없음 |
| 기존 수용·목적지 확정·조건 재확인 API | 실제 업무 상태를 변경하는 별도 처리 | 기존 유효성·버전·자원 검사에 따라 처리 |

`can_accept`라고 메모하고 상대 기관이 내용을 확인해도 문의는 자동으로 `ACCEPTED`가 되지 않으며 목적지도 확정되지 않는다. 응답에는 `confirmation_scope: "record_content_only"`, `operational_effect: "none"`을 넣는다. 화면에서도 `확인됨`은 **통화 기록 내용 확인**으로 표시하고 수용 확정 배지와 구분한다.

`actor_id`와 상대 표시명은 클라이언트가 선언한 값이다. 이 API가 실제 사용자 인증이나 통화 상대의 신원을 검증하는 것은 아니다.

## 2 역할과 공유 범위

```text
구급대원 BASE = /api/patients/{pid}
병원 BASE     = /api/hospitals/{hid}/patients/{pid}
```

- 구급대원은 존재하는 이송 건과 병원 ID를 지정해 기록한다. 전자 문의를 먼저 보내지 않아도 된다.
- 기록의 `hospital_id`는 **이 내용을 공유할 상대 기관**이다. 구급대원 경로에서는 필수이며, 지정한 병원이 해당 기록을 조회·내용 확인할 수 있다.
- 병원 경로에서 작성하려면 기존 문의·목적지·대화 참여 또는 해당 병원을 대상으로 공유된 통화 기록이 있어야 한다. 본문의 병원 ID는 생략하거나 경로와 일치시킨다.
- 구급대원 경로는 해당 이송 건의 전체 통화 기록을, 병원 경로는 자기 병원에 연결된 기록만 조회한다. 이전 병원에 새 병원과의 메모가 공개되지 않는다.
- 통화 기록만 공유해도 문의·병원 대화 참여·수용·도착 예정 환자가 새로 만들어지지는 않는다. 해당 건의 업무 계측에서는 통화 상대 관계를 인정한다.

## 3 API 목록

병원 전체 통화함은 **`GET /api/hospitals/{hid}/call-records`**다. 이송 ID를 미리 몰라도 자기 병원에 공유된 기록을 발견할 수 있다. 아래 이송 건별 API와 구분한다.

| 기능 | 경로 |
|---|---|
| 기록 작성 또는 정정본 추가 | `POST {BASE}/call-records` |
| 목록 | `GET {BASE}/call-records?limit=50` |
| 상세 | `GET {BASE}/call-records/{record_id}` |
| 상대 기관측 내용 확인·이의 | `POST {BASE}/call-records/{record_id}/confirmation` |

성공은 HTTP 200, 입력 오류는 400, 조회할 수 없는 기록은 404, 자기 측 확인은 403, 멱등 키 충돌·이미 확인된 기록·이미 정정된 원문에 대한 새 확인은 409다.

목록의 `limit`은 1~100, 기본 50이다. 최신 ID부터 반환하며 `has_more`가 참이면 `next_before_id`를 다음 `before_id`로 사용한다. 빈 값·중복 키·지원하지 않는 필터는 400이다. 목록에서 지원하는 필터는 `limit`, `before_id`다.

## 4 기록 작성

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

위 시각과 ID는 형식 예시다. 실제 호출은 존재하는 환자·병원을 사용하고 해당 건 등록 후부터 현재까지의 통화 시각을 보낸다.

| 필드 | 의미 |
|---|---|
| `actor_id` | 작성자 식별자. 최대 80자 |
| `client_record_id` | 기록 작성 요청의 멱등 키. 최대 128자 |
| `hospital_id` | 공유 대상 병원. 구급대원 경로 필수 |
| `call_started_at`, `call_ended_at` | 작성자가 보고한 통화 시각. UTC epoch 초 정수 |
| `reported_outcome` | 전달받았다고 기록하는 결과 코드 |
| `counterparty_role` | `paramedic`, `emergency_staff`, `coordinator`, `other` 중 하나 |
| `counterparty_label` | 작성자가 입력한 상대 표시명. 선택, 최대 80자 |
| `note` | 보충 메모. 선택, 최대 1,000자 일반 텍스트 |
| `supersedes_id` | 정정할 기존 기록 ID. 정정 시에만 사용 |

식별자는 영문·숫자로 시작하고 이후 영문·숫자·`. _ : @ -`를 사용한다. 같은 요청의 재전송에는 같은 키를 쓰고, 새 통화나 정정본에는 새 키를 쓴다. 같은 키로 내용을 바꾸면 409다.

| 결과 코드 | 화면 설명 예시 |
|---|---|
| `can_accept` | 수용 가능하다고 전달받음 |
| `cannot_accept` | 수용 어렵다고 전달받음 |
| `checking` | 확인 중이라고 전달받음 |
| `additional_info` | 추가 정보가 필요하다고 전달받음 |
| `no_answer` | 통화 연결되지 않음 |
| `callback_requested` | 재통화·회신 요청 |
| `other` | 기타 통화 결과 |

환자 등록 시각(`requested_at`) ≤ 통화 시작 ≤ 통화 종료 ≤ 서버 현재 시각을 검사한다. 서버는 별도로 `recorded_at`을 저장한다. **보고된 통화 시각과 서버 기록 시각은 다르며, 실제 통화 시간을 독립 검증한 값이 아니다.**

작성 응답은 `{ "record": { ... }, "duplicate": false, "scope": { ... } }`다. 기록에는 본문 필드 외에 `id`, `patient_id`, `author_role`, `recorded_at`, `superseded_by_id`, `confirmation_status`, `confirmation`, `confirmation_scope`, `operational_effect`가 있다. 원문 정정·확인에 관한 최신 상태가 함께 반환된다.

## 5 상대 기관측 내용 확인과 이의

구급대원이 작성한 기록은 대상 병원 경로에서, 병원이 작성한 기록은 구급대원 경로에서 확인한다. 같은 작성 측은 다른 작업자 ID를 사용해도 상대측 확인을 대신할 수 없다.

```json
{
  "actor_id": "hospital-desk-1",
  "client_confirmation_id": "call-check-001",
  "decision": "confirm"
}
```

내용에 이의가 있는 경우:

```json
{
  "actor_id": "hospital-desk-1",
  "client_confirmation_id": "call-check-002",
  "decision": "dispute",
  "reason": "전화에서 전달한 내용과 메모가 달라 확인이 필요함"
}
```

`reason`은 최대 500자이며 `dispute`일 때 필수다. `confirmation_status`는 `unconfirmed`, `confirmed`, `disputed`다. `confirmation`에는 확인 측 역할·작업자·판단·사유·서버 확인 시각이 들어간다.

기록당 상대측 판단은 한 번 고정한다. 정확한 재전송은 최초 결과와 `duplicate: true`를 반환하며, 다른 담당자가 이미 남긴 판단을 덮어쓸 수 없다. 이의 이후 수정이 필요하면 원 작성자가 정정본을 추가한다.

기록 내용 확인은 필요한 경우에 선택적으로 사용한다. 수용·불가 응답의 선행 조건으로 강제하거나, 같은 판단을 두 번 입력하는 필수 단계로 만들지 않는다.

## 6 원문 보존과 정정

원래 작성한 역할·작업자만 같은 환자·병원의 기록을 정정할 수 있다. 기존 본문을 수정하는 대신 새 키와 `supersedes_id`로 새 기록을 만든다.

```json
{
  "actor_id": "crew-demo-1",
  "client_record_id": "call-demo-001-correction-1",
  "hospital_id": "H1",
  "call_started_at": 1790000005,
  "call_ended_at": 1790000015,
  "reported_outcome": "checking",
  "counterparty_role": "emergency_staff",
  "note": "확인 중이라는 통화 내용을 정정 기록함",
  "supersedes_id": 17
}
```

원문과 원래 확인 이력은 보존한다. 원문에 `superseded_by_id`가 표시되고, 새 기록은 미확인으로 시작한다. 이미 정정된 원문에 새 확인을 추가하면 409다. 원래 성공한 확인 요청의 정확한 재전송은 허용한다. 동시에 같은 원문을 정정하면 하나만 성공한다. 추가 정정은 가장 최근 기록을 기준으로 한다.

## 7 화면 연결과 계측

1. 통화 결과를 작성할 때 공유 대상 병원을 표시한다.
2. 저장한 메모에는 작성 측과 `기록 내용 미확인/확인/이의`를 표시한다.
3. 상대 기관이 내용 확인을 해도 수용·불가·조건 재확인은 기존 업무 버튼으로 별도 처리한다.
4. 메모는 HTML로 삽입하지 않고 일반 텍스트로 렌더링한다. 필요 이상의 개인정보·환자 본문을 복사하지 않는다.
5. 작업시간은 [업무 계측 API](BACKEND_MEASUREMENT_API.md)를 연결한다. 메모의 보고된 통화 기간을 활동시간에 자동 합산하지 않는다.

새 테이블은 `call_records`, `call_record_confirmations`다. 기존 DB는 삭제 없이 확장하며 원문·확인 행은 수정·삭제·교체를 막는다. 기존 서버 프로세스는 같은 DB 경로·포트로 재시작해야 한다. 데이터 보존이 필요하면 `--seed`를 사용하지 않는다.

테스트는 가상 환자·병원과 임시 DB·로컬 임시 포트만 사용한다. 실제 전화나 외부 병원 요청은 하지 않는다.

```text
python -m unittest test_call_records test_call_records_api -v
```

## 8 병원 전체 통화함

```text
GET /api/hospitals/{hid}/call-records
GET /api/hospitals/{hid}/call-records?author_role=paramedic&confirmation_status=unconfirmed
GET /api/hospitals/{hid}/call-records?after_id=0&limit=50
```

| 필터 | 의미 |
|---|---|
| `limit` | 1~100, 기본 50 |
| `before_id` | 최신순 목록에서 이전 기록으로 페이지 이동 |
| `after_id` | 지정 ID 뒤의 새 기록을 오래된 것부터 조회. 0부터 최초 발견 가능 |
| `confirmation_status` | `all`(기본), `unconfirmed`, `confirmed`, `disputed` |
| `author_role` | `all`(기본), `paramedic`, `hospital`. 병원이 확인할 기록만 보려면 `paramedic` |
| `current_only` | 기본 `true`: 정정된 원문을 제외하고 현재 정정본 중심. `false`는 이력 포함 |
| `patient_id` | 특정 이송 건으로 제한. 생략하면 자기 병원 전체 |

`before_id`와 `after_id`는 동시에 보낼 수 없다. 새 ID 조회는 `cursor_mode: "new_records"`, 일반 목록은 `"latest"`다. 반환은 `records`, `has_more`, `next_before_id`, `next_after_id`, `scope`, `filters`, `stream`이다. 병원 범위 밖 기록이나 환자 프로필을 결합하지 않는다.

`after_id`는 새 기록 발견용이다. 기존 ID의 내용 확인 상태 변경을 포착하지 않으므로 미확인 목록은 별도로 다시 조회한다. `stream.confirmation_changes_require_refresh`는 이 의미다. GET으로 수신·내용 확인을 처리하지 않는다. 항목을 선택하면 그 항목의 `patient_id`와 `id`로 기존 이송 건별 상세·확인·정정 API를 사용한다.

이 통화함이 병원 전체 중요 사건함이나 환자 의료정보·대화 참여 권한을 새로 만드는 것은 아니다.

검증 결과: 통화 기록 단위 25개와 HTTP 통합 7개를 포함해 관련 20개 모듈의 **392개 테스트가 모두 통과**했다. 전화만 한 병원과의 공유, 내용 확인·이의, 원문 보존·정정 경합, 멱등 재전송, 잘못된 입력과 미래 시각, 타 병원·타 환자 격리, 업무·수용·대화 상태 불변과 기존 흐름을 확인했다. 팀원 화면 19개 파일은 변경하지 않았다.
