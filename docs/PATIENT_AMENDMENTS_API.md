# 문의 이후 환자정보 수정 API

2026년 10월 29일. 병원 선정 대기 중 환자정보를 보완하고, 기존 문의·수용 응답을 새 정보와 구분하기 위한 API다. 화면은 수정하지 않았다.

## 1 지원 범위

| 환자 상태 | 처리 |
|---|---|
| `WAITING` | 문의 전·후, 질문 대기, 수용 응답을 받았지만 목적지를 확정하기 전까지 수정 가능 |
| `CONFIRMED`, `IN_TRANSIT` | 이번 일반 수정 API는 409. 확정 자원과 목적지는 유지 |
| `ARRIVED`, `COMPLETED`, `CANCELLED` | 새 수정 요청은 409 |

이미 확정된 이송의 진료 요구를 일반 수정으로 바꾸면 확정 수요와 병원 재확인 절차까지 달라진다. 이송 중 일반 임상정보 변경은 별도 설계 대상이며, 이번 API가 지원하는 것으로 표시하지 않는다. 위치 갱신과 병원 자원 변화에 대한 기존 재확인 API는 유지한다.

기존 `PATCH /api/patients/{pid}`는 **문의 기록이 없는 WAITING 건**에만 사용하는 계약을 유지한다. 문의 이후에는 아래 버전·사유·멱등 키가 있는 API를 사용한다. 기존 PATCH의 `ALREADY_SENT` 오류에도 `amendment_endpoint`를 안내한다.

## 2 API와 요청

| 기능 | 경로 |
|---|---|
| 수정 | `POST /api/patients/{pid}/amendments` |
| 변경 이력 | `GET /api/patients/{pid}/amendments?limit=50` |

먼저 `GET /api/patients/{pid}`에서 현재 `version`을 조회한다.

```json
{
  "actor_id": "crew-demo-1",
  "client_request_id": "amend-demo-001",
  "expected_patient_version": 1,
  "reason": "추가 상태 확인에 따른 정보 보완",
  "changes": {
    "symptoms": "추가로 확인한 증상",
    "consciousness": "impaired"
  }
}
```

- 다섯 필드는 모두 필수다. 원래 요청의 재전송은 같은 ID·버전·사유·변경 내용을 유지한다.
- `actor_id`: 최대 80자, `client_request_id`: 최대 128자. 기존 작업자·요청 ID와 같은 ASCII 식별자 규칙이다.
- `expected_patient_version`: 양의 정수. 현재 환자 버전과 다르면 409다.
- `reason`: 비어 있지 않은 한 줄 문자열, 최대 500자.
- `changes`: 실제로 달라질 필드를 포함한 객체. 알려지지 않은 필드·실질 변화 없음·잘못된 자료형·범위 밖 수치는 400이다. JSON 변경 본문은 최대 16,384자다.

지원 필드는 다음과 같다.

| 묶음 | 필드 |
|---|---|
| 일반 보완 | `name`, `birth`, `age`, `relation`, `contact`, `address`, `guardian`, `guardian_phone` |
| 상태와 증상 | `symptoms`, `onset_at`, `consciousness`, `breathing`, `danger_signs`, `extra` |
| 진료·자원 요구 | `required_capabilities`, `resource_demands`, `priority`, `priority_source` |
| 기존 실험 설정 | `treatment_duration_seconds`, `max_wait_seconds` |

진료 프로필과 명시적 요구를 기존 서버 규칙으로 다시 결합한다. 수정 API가 증상으로 새로운 진단이나 임상 우선순위를 추론하지는 않는다. `state`, `version`, `destination_hospital_id`, `reporter_role`, `consent`, `lat`, `lon` 등 제어·위치 필드는 이 API로 바꾸지 않는다.

## 3 수정 시 처리

하나의 쓰기 트랜잭션에서 다음을 처리한다.

1. 현재 환자 버전·단계·동일 요청 여부와 저장된 문의 정책을 검사한다.
2. 환자정보를 바꾸고 환자 버전을 1 올린다.
3. 기존 `PENDING`, `CHECKING`, `ACCEPTED` 문의를 `CANCELLED`로 종료한다. 수정 전 수용 응답으로 확정할 수 없게 한다.
4. 기존 모든 문의에 `requires_review_version: 1`을 설정한다. 이전에 불가·취소·만료된 문의 ID가 다시 사용되는 경우도 보호한다.
5. 수정 필드의 이전·이후 값과 버전·사유·영향 병원·종료 문의를 감사 이력으로 저장한다. 전체 환자 본문을 다시 복사하지 않는다.
6. 관련 병원별로 `patient_information_amended` 중요 사건을 기록한다. 사건에는 필드명·버전·수정 ID만 넣고 변경한 임상 본문은 넣지 않는다.
7. 켜져 있는 초기 자동 문의를 새 버전으로 재평가하도록 예약한다. 직접 수용 응답을 만들거나 목적지를 확정하지 않는다.

현재는 지원되는 어떤 필드가 달라져도 기존 열린 문의·수용 응답을 종료하는 보수적 정책이다. 이름·연락처 보완만 예외로 수용을 승계하는 기능은 없다. 화면은 저장 전에 이 효과를 안내한다.

응답:

```text
{
  patient: 현재 환자 상세,
  amendment: 최초 감사 기록,
  duplicate: false
}
```

`amendment`에는 `id`, `patient_id`, `actor_id`, `client_request_id`, `recorded_at`, `reason`, `from_version`, `to_version`, `changed_fields`, `before`, `after`, `affected_hospital_ids`, `closed_inquiry_ids`, `operational_effect: "obsolete_inquiries_closed"`가 있다.

감사 원문은 수정·삭제·교체하지 않는다. 같은 요청의 정확한 재시도는 최초 감사 행과 `duplicate: true`를 반환한다. 이때 `patient`는 현재 상세이므로 최초 응답 시점의 스냅샷과 같다는 보장은 없다. 같은 키의 다른 내용은 409다.

## 4 재문의와 병원 응답

### 자동 문의를 켠 경우

기존 정책·시작 시각·제한시간·정책 설정을 유지하고 다음 워커 평가를 예약한다. 새 환자 버전에서 아직 문의하지 않은 적합 병원은 과거에 문의했던 곳이라도 다시 후보가 된다. 각 버전의 정책 문의 한도를 적용하며, 수정으로 원래 탐색 제한시간을 연장하지 않는다. 기한이 지났거나 자동 문의가 꺼진 경우를 성공 재탐색으로 표시하지 않는다.

진행 표시는 기존 `GET /api/patients/{pid}` 또는 `GET /api/patients/{pid}/inquiry-workflow`로 조회한다.

- `contacted_count`: 기존 누적 문의 병원 수를 유지한다. 재문의 총 횟수가 아니다.
- `current_version_contacted_count`, `current_version_contacted_hospital_ids`: 초기 선정 단계의 현재 환자 버전에 해당하는 문의 현황이다.

### 수동 문의인 경우

자동 문의가 꺼져 있으면 일시정지 상태를 유지한다. 새 후보를 확인하고 기존 `POST /api/patients/{pid}/inquiries`에 `hospital_ids`를 보내 재문의한다. 이전 종료 문의를 단순히 응답 대기처럼 표시하지 않는다.

### 병원 응답의 버전 필수

`requires_review_version: 1`인 문의는 **확인 중·질문·수용·불가 모두** 최신 문의의 `version`을 `expected_version`으로 보내야 한다.

```json
{
  "action": "accept",
  "handled_by": "시연 담당자",
  "expected_version": 5
}
```

- 버전 누락은 `REVIEW_VERSION_REQUIRED` 409.
- 오래된 화면의 버전은 `CONFLICT` 409.
- 이전 환자정보로 보낸 문의는 `STALE_PATIENT_INFORMATION` 409.
- 바로 직전 수용·불가 결정의 **같은 본문·같은 버전** 재전송은 현재 환자 버전·문의 버전·요청 지문이 모두 맞을 때만 `duplicate: true`로 성공한다. 다른 처리자·불가 사유·요청 내용으로 덮어쓰지 않는다.
- 409가 오면 최신 문의와 환자정보를 다시 읽고 담당자가 판단한다. 조회한 최신 버전으로 예전 판단을 조용히 재전송하지 않는다.

새 정보 수용이 실제로 확인된 뒤에만 기존 `/confirm`으로 목적지를 확정한다. 계산 중이던 옛 워커 결과는 환자·작업·계획 버전 검사로 적용하지 않는다.

## 5 화면 연결과 변경 이력

구급대원은 문의 중 정보 보완 버튼에서 최신 환자 버전을 읽고 변경을 제출한다. 성공하면 반환된 `patient`로 상태를 갱신하고 새 정보 확인 대기를 표시한다. 문의 이후 일반 수정을 기존 PATCH로 보내지 않는다.

병원은 수정·문의 종료 중요 사건을 표시하고, 새 문의의 최신 환자정보와 문의 버전을 확인한다. 한 수정에서 종료와 정보 변경 사건이 함께 올 수 있으므로 같은 변경의 안내를 한 카드로 묶되 사건별 수신·확인 ID는 보존한다. 알림 확인이 수용 응답을 대신하지 않는다.

변경 이력은 환자 건별 최신순이다. `limit`은 1~100, 기본 50이며 `has_more`가 참이면 `next_before_id`를 다음 `before_id`로 보낸다. 조회는 쓰기를 만들지 않는다. 개인정보가 포함될 수 있는 `before`·`after`를 알림·URL·일반 로그에 복사하지 않는다.

기존 DB에는 `patient_amendments` 감사 테이블과 문의의 `requires_review_version`, `review_response_fingerprint` 필드가 추가된다. 기존 문의는 기본값으로 이전 응답 계약을 유지하고, 정보가 수정된 문의에 강화된 검사를 적용한다. 기존 데이터를 지우지 않고 마이그레이션한다.

관련 테스트는 가상 데이터·임시 DB·로컬 임시 포트만 사용한다. 이 기능이 실제 임상정보 수집·판정의 정확성을 검증한 것은 아니다.

검증 결과: 새 단위·HTTP 테스트 53개를 포함해 관련 23개 모듈의 445개 테스트가 통과했다. 수용 후 정보 수정, 낡은 화면 응답 차단, 정확한 수용·불가 재시도, 자동 재문의, 수정과 목적지 확정의 동시 경합, 감사 이력·기존 DB 보존, 병원 통화함의 범위·페이지·상태 필터를 확인했다. 팀원 화면 19개 파일은 변경하지 않았다.
