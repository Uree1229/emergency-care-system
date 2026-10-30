# 배정 입력 미확정 정보 표시 API

작성일: 2026-10-30

정책 버전: `allocation-input-review-20261030-v1`

구현: `server/allocation_input_review.py`의 순수 함수 `assess(patient, now)`

## 목적과 적용 범위

현재 저장된 환자 정보 중 **배정·인계에 관련된 재확인 항목**과 **선택 관찰 항목의 미확정 상태**를 나누어 화면에 전달한다. 구급대원용·병원용 화면이 같은 표시 계약을 사용할 수 있게 서버 응답을 추가했다. 이번 작업에서는 Claude 담당 UI를 수정하지 않았다. 화면 표시와 버튼 연결은 후속 작업이다.

이 결과는 임상 필수 체크리스트, 환자 안전 판정, 추가 진단이 아니다. `requires_review=true`는 확인할 내용이 있다는 뜻이며, 모든 항목을 채워야 배정할 수 있다는 뜻이 아니다. `purpose`는 항상 `allocation_input_display_only`, `blocking`은 항상 `false`다.

- 후보 적합성, 솔버 목적함수, 병원 수용·확정 규칙을 바꾸지 않는다.
- 병원의 기존 수용을 자동 취소하거나, 목적지를 자동 변경하지 않는다.
- 정보가 미확정이라는 이유로 이 모듈이 배정을 차단하지 않는다. 기존 치료조건·수용 응답 등의 검증은 그대로 적용된다.
- 관찰시각을 이유로 임의의 임상 유효기간을 만들지 않는다. 병원 정보의 `STALE_AFTER`도 적용하지 않는다.

## 읽는 정보와 우선순위

`assess`는 전달받은 환자 스냅샷만 읽으며 DB를 조회하거나 수정하지 않는다. 음성 기록은 **환자 레코드에 이미 반영된** `extra.voice_observations.fields` 및 `field_metadata`를 참조한다. 미제출 음성 초안·음성 세션 테이블은 읽지 않는다.

현재 환자 코어 값, R1 항목의 `extra` 값, `extra.vitals` 값이 과거 음성 저장값보다 우선한다. 저장된 음성 메타데이터는 현재 값의 **존재 여부·값·자료형**이 저장값과 일치할 때만 사용한다. 따라서 수동으로 바뀐 값에 과거의 재확인 표시를 붙이지 않는다.

- `extra.vitals={}`처럼 명시적으로 비운 컨테이너에서 과거 측정값·표시를 되살리지 않는다.
- 환자 코어의 기본 `null`만으로 새 필수 입력 경고를 만들지 않는다. 다만 저장된 명시적 확인 불가 답변은 표시할 수 있다.
- 현재 증상 자유문이 바뀌어 과거 음성 증상 목록과 달라졌다면 과거 증상 불확실성 표시는 이어받지 않는다.
- `needs_clarification`, 해당 불확실성 상태, 명시적 `unknown`·`unmeasured`, 질문별 확인 불가 답변을 표시 신호로 사용한다.
- 음성 초안의 해석 보완 표시인 `metadata.review_required`·`interpretation_notes`는 별도 개념이다. 제출 후 보존될 수 있으므로, 그 값만으로 배정 입력의 불확실성을 만들지 않는다.

## 항목 분류

단순 누락을 모두 경고하지 않는다. 아래 분류에서 관찰 항목은 원칙적으로 **명시된 미확정 상태가 있을 때** 표시한다. 현재 위치와 배정 우선순위는 실제 계산 입력이므로 별도로 확인한다.

주호소별 관련 필드도 단순히 없다는 이유로 일괄 경고하지 않는다. 예를 들어 `chief_complaint=cardiac`이지만 `stemi_suspected` 필드 자체가 없고 명시적 재확인 기록도 없다면 이 모듈은 해당 항목을 새로 경고하지 않는다. 이 정책은 확인된 불확실성을 보여주는 범위이며, 필요한 모든 정보가 존재하는지 감사하는 범위는 아니다.

| 구분 | 표시 정책 |
| --- | --- |
| 현재 위치 | 유효한 위·경도가 없으면 `LOCATION_REQUIRED`, `level=review` |
| 배정 우선순위 | `priority_source=expert`이며 정수 `1/2/4`인 경우 현재 값을 인정. 그렇지 않으면 `PRIORITY_UNVERIFIED` 표시 |
| 현재 우선순위와 연결된 Pre-KTAS | 현재 사용 우선순위와 대응하고 저장값도 일치하는 기록이 재확인 상태인 경우만 표시. 유효한 전문가 우선순위가 있으면 원시 Pre-KTAS의 단순 누락은 경고하지 않음 |
| 인계 관련 항목 | 의식·AVPU, 호흡, 주호소, 연령 분류, 미확인 증상은 `level=review` |
| 현재 주호소별 항목 | `cardiac → stemi_suspected`, `trauma/altered_seizure → injury_region`, `obstetric → labor/pregnancy_weeks`만 관련 항목으로 승격 |
| 선택 관찰 항목 | 혈압·맥박·산소포화도·체온·호흡수·혈당, 정확한 나이·성별·발생 시각 등의 표시 대상 미확정 상태는 `level=info`. 이 항목만 있으면 `requires_review=false` |

미확인 우선순위에서 보이는 `used_priority=4`는 **기존 서버의 보수적 실험 배정 가중치**를 설명한다. 임상 중증도 4등급으로 진단·추론했다는 뜻이 아니다. 활력징후 임계값으로 임상 등급을 새로 계산하지 않는다.

의식/AVPU, 수축기/이완기 혈압 등은 하나의 표시 항목으로 묶는다. `unavailable`은 실제 음성 확인 질문에 포함된 필드의 저장 표식을 확인한다. 예를 들어 의식 질문은 AVPU 대안 필드까지 답하도록 요구하지 않는다.

`observed_at`과 `age_seconds`는 저장된 유효 관찰시각이 있을 때만 제공한다. 미래 시각이나 잘못된 형식은 사용하지 않는다. 그룹 항목은 영향을 받은 필드의 유효시각 중 가장 오래된 시각을 사용해, 최근 확인한 한 필드가 다른 필드까지 새것처럼 보이게 하지 않는다. 시각을 알 수 없으면 `null`이며 임상 만료 판정으로 해석하지 않는다.

## API와 포함 위치

### 전용 읽기 API

```http
GET /api/patients/{patient_id}/input-review
```

쿼리 파라미터와 요청 본문이 필요 없다. `actor_id`를 포함한 **모든 쿼리 파라미터를 허용하지 않는다**. 빈 값·중복 파라미터도 HTTP 400이며, 존재하지 않는 환자는 HTTP 404다. 조회로 환자 버전, 문의, 이벤트, 음성 세션, 배정 계산 상태를 바꾸지 않는다.

### 기존 응답에 추가되는 위치

| API | 최신 표시 위치 |
| --- | --- |
| `GET /api/patients/{patient_id}/input-review` | 응답의 `input_review` |
| `GET /api/patients/{patient_id}/candidates` | 응답의 `input_review` |
| `GET /api/patients/{patient_id}/recommendation` | 응답의 최상위 `input_review` |
| `POST /api/patients/{patient_id}/replan` | 반환되는 추천 응답의 최상위 `input_review` |
| `GET /api/hospitals/{hospital_id}/incoming` | `planning[]` 각 환자 항목의 최상위 `input_review` |

추천이 `QUEUED`·`STALE`·`ERROR`여서 최신 계산 결과가 없어도, 해당 응답의 최상위 `input_review`는 **조회 시점의 저장 환자 정보**로 만든다. 최신 표시를 위해 계산 완료를 기다릴 필요가 없다.

반면 저장된 솔버 결과 내부의 `result.input_review`는 **계산 당시 환자 스냅샷**이다. 기존 계산 이력을 새 정보로 덮어쓰지 않는다. 화면의 현재 확인 안내에는 최상위 값을 사용하고, 계산 이력 설명에는 내부 값을 사용한다. 각각의 `patient_version`·`checked_at`으로 어느 시점의 결과인지 구분한다. 병원 응답에서는 이 원칙을 `planning[]`의 각 항목에 적용한다.

지원 여부는 `GET /api/voice/capabilities`의 `allocation_input_review_revision`으로 확인할 수 있다.

## 응답 예시

다음은 현재 위치는 있지만 전문가 우선순위가 확인되지 않은 가상 환자의 전용 API 응답 예시다. `checked_at`·`observed_at`은 Unix 초 단위 정수다.

```json
{
  "patient_id": "DEMO-P001",
  "patient_version": 3,
  "patient_state": "WAITING",
  "input_review": {
    "revision": "allocation-input-review-20261030-v1",
    "patient_id": "DEMO-P001",
    "patient_version": 3,
    "patient_state": "WAITING",
    "checked_at": 1793318400,
    "status": "REVIEW_REQUIRED",
    "requires_review": true,
    "review_count": 1,
    "information_count": 0,
    "purpose": "allocation_input_display_only",
    "blocking": false,
    "items": [
      {
        "code": "PRIORITY_UNVERIFIED",
        "field": "priority",
        "fields": ["priority", "priority_source"],
        "label": "배정 우선순위",
        "state": "unknown",
        "level": "review",
        "relevance": "allocation_priority",
        "reason": "확인된 전문가 우선순위가 없어 기존 보수적 배정 가중치 4를 사용합니다.",
        "observed_at": null,
        "age_seconds": null,
        "action": {
          "kind": "create_patient_voice_draft",
          "fields": ["priority", "priority_source"],
          "method": "POST",
          "endpoint": "/api/voice/sessions",
          "body": {"patient_id": "DEMO-P001"},
          "requires_actor": true,
          "requires_client_request_id": true
        },
        "used_priority": 4,
        "priority_is_fallback": true
      }
    ]
  }
}
```

| `status` | 의미 |
| --- | --- |
| `REVIEW_REQUIRED` | `level=review`인 항목이 하나 이상 있음 |
| `INFORMATION_ONLY` | `level=info`인 항목만 있음. `requires_review=false` |
| `NO_REVIEW_FLAGS` | 이번 표시 정책에서 확인 표시할 항목이 없음. 모든 임상 정보가 완전하다는 뜻은 아님 |

항목의 `state`는 `missing`·`unknown`·`needs_clarification`·`uncertain`·`unavailable` 중 해당 상태다. 화면은 `label`·`reason`을 표시하고, `fields`는 관련 입력을 찾는 데 사용한다.

## 확인 동작과 화면 연결 계약

`action`은 다음 확인 경로를 설명하는 데이터다. 읽기 API를 호출했다고 이 동작이 실행되지는 않는다.

1. **`WAITING` 환자:** `create_patient_voice_draft`로 새 음성 초안을 만든다. 응답의 `body`에는 `patient_id`만 있다. 클라이언트가 현재 작업자의 `actor_id`와 새 `client_request_id`를 별도로 넣어 `POST /api/voice/sessions`를 호출한다. 원래 작성자의 세션을 열거나 소유권을 대신 사용하지 않는다. 초안 생성만으로 환자 정보가 확정·변경되지 않는다.
2. **`CONFIRMED`·`IN_TRANSIT` 환자:** `review_transport`로 `GET /api/patients/{patient_id}/transport-review`를 연다. 이미 수용된 이송의 기존 재확인·조정 절차로 안내하며, 일반 음성 적용으로 목적지를 바꾸지 않는다.
3. **위치 미확인:** `review_patient_location`으로 환자 레코드를 조회하고 현재 위치를 별도로 확인한다. 음성 초안이 GPS를 대신 채운다고 가정하지 않는다.
4. **그 밖의 상태/식별자 없음:** `view_patient_record` 또는 `manual_review`로 안내한다. 지원되지 않는 쓰기 동작을 자동 실행하지 않는다.

개인 원문·증거 문장·음성 녹취, `actor_id`, 원본 `session_id`, 소유자 전용 링크를 `input_review`에 포함하지 않는다. 새 초안 생성에 필요한 작업자 식별자는 현재 클라이언트 문맥에서 제공한다. 이 모듈의 개인정보 최소화가 다른 기존 환자 API 전체의 노출 정책을 바꾼다는 뜻은 아니다.

후속 UI 연결 시 중요 재확인 항목과 선택 참고 항목을 구분하고, `blocking=false`를 유지한다. 병원 수용 취소, 확인되지 않은 임상 등급 부여, 목적지 자동 변경 버튼으로 해석하지 않는다.

`NO_REVIEW_FLAGS`는 화면에서 **“현재 정책의 재확인 표시 없음”**과 같이 표현한다. **“모든 정보 확인 완료”·“임상적으로 안전함”**으로 번역하지 않는다.

## 이번 작업 검증

집중 검증 **38개 통과**: 순수 판정 20개 + 서버 통합 13개 + 실제 로컬 HTTP 5개. 가상 테스트 데이터로 검증했으며 실제 병원에 문의를 전송하지 않았다.

```powershell
python -m unittest test_allocation_input_review test_allocation_input_review_integration test_allocation_input_review_http -v
```

검증 범위에는 최신 값 우선, 명시적 빈 활력징후, 현재 주호소별 관련성, 선택 항목만 있을 때 비차단, 질문별 확인 불가, 그룹 관찰시각, 원문·소유자 정보 미노출, 읽기 요청의 DB 무변경, 대기 중 추천 표시, 선택 미측정 항목이 있는 기존 수용 확정 유지가 포함된다. 이 수치는 전체 프로젝트 테스트 또는 임상 효과 검증 결과를 의미하지 않는다.

서버 회귀 **531개 통과**: 기존 음성 입력·질문 답변·치료조건 검토·환자 수정·문의·대화·이송 검토·배정 워커 계약과 이번 38개를 함께 실행했다. 실패·오류 0개이며 검사 전후 대상 소스 해시가 일치했다. 별도 합성 서버에서 기존 실제 HTTP 클라이언트의 초안 흐름 **12단계**, 환자 연결 흐름 **11단계**도 통과했다. 새 실제 마이크 시험이나 임상 효용 시험을 뜻하지 않는다.

추천 조회 도중 다른 연결이 환자 변경을 커밋하는 WAL 회귀도 포함한다. 첫 조회는 이전 스냅샷을 유지하고 다음 조회는 최신 환자와 `STALE` 상태를 보며, 읽기 도중 별도 쓰기 커밋이 가능함을 확인했다.

증거 파일:

- `.codex-build/allocation-input-review-20261030/server-verification.json` 및 `.log`
- `.codex-build/allocation-input-review-20261030/draft-flow-final/report.json`
- `.codex-build/allocation-input-review-20261030/patient-flow-final/report.json`
- `.codex-build/allocation-input-review-20261030/live-verification.json`

## 로컬 반영과 보존

`http://127.0.0.1:18032` 서버를 새 코드로 재시작했다(PID `15196`). 동일한 `.codex-build/paramedic-ui-20261030/demo.db`를 사용하고 배정 워커는 실행, 자동 문의 워커는 기존과 같이 비활성화했다. 다른 도구의 서버·UI는 변경하지 않았다.

백업은 `.codex-build/allocation-input-review-20261030/before-input-review.db`다. 재시작 전후 환자·문의·음성 세션·발화·수정 이력·요청·질문 답변·환자 수정·이벤트 9개 테이블의 행 수와 내용 해시가 일치했다. 기존 환자 8건의 새 조회 API도 확인했으며 모두 표시 전용 `blocking=false`였다. DB 초기화나 테스트 환자 삽입은 하지 않았다.
