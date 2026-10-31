# 팀원 후속 작업: 추천 결과와 외부 유입 연결

최신 상태: [메인 음성·이송과 검토·추천·알림 연결](MAIN_UI_INTEGRATION_20261030.md)에서 환자·병원 화면 표시와 명시 재계산을 구현·검증했다. 이 문서의 미구현 표기는 당시 상태를 보존한 것이며 아래 API 계약·구분 원칙은 계속 따른다.

기준: 2026-10-14. 이 문서는 현재 코드 점검과 팀원 화면의 후속 연결 계약이다. **서버 추천 API는 구현돼 있지만 환자용·병원용 화면의 추천 결과 표시는 아직 구현되지 않았다.** 이번 작업에서 팀원 UI의 디자인과 파일은 수정하지 않았다. 아래 완료 기준은 앞으로 확인할 항목이며, 통과한 테스트 결과를 뜻하지 않는다.

기존 환자 등록·후보 탐색·병원 문의·추가 질문·사람의 목적지 확정·출발·채팅·조건 재확인·대체 문의·도착·퇴실 흐름은 연결돼 있다. 추천 결과는 이 흐름을 보조하며 기존 업무 상태를 대신하지 않는다.

## 1. 현재 상태와 담당 범위

| 대상 | 코드에서 확인한 현재 상태 | 담당자가 이어서 구현할 것 |
|---|---|---|
| 공통 환자 웹 `patient-ui/` | 환자 상세·대화·후보를 조회하지만 추천 API는 호출하지 않음 | 추천 조회·워커 가동 상태·결과 근거 표시, 명시적인 재계산 버튼 |
| 병원 웹 `hospital-ui/` | `incoming.planning`을 받아 저장하지만 화면에 사용하지 않음 | 선택 환자의 추천·재검토 상태 표시, 외부 유입 기록 목록·개별 퇴실·중복 등록 방지 |
| 팀원 원본 환자 앱 `emergency-app/` | 동의 후 공통 서버에 별도 이송 건을 만들고 `/patient/?pid=…`로 이동 | 원본 앱 안에서 진행·대화·추천을 보여 주려면 같은 공통 API와 이송 ID를 사용해 화면 통합 |
| 서버 | 추천 신선도·워커 가동 상태를 별도 반환, 비활성 재계산 거부, 운영 자원 확인 불가 사유 반환 | 팀원이 만든 화면의 요청·응답과 충돌 처리 점검 |

2026-10-14 코드 확인 위치:

- `patient-ui/app.js:63`: `refresh()`는 환자 상세·대화·후보를 조회한다.
- `patient-ui/app.js:150`: 문의의 `usable`로 기존 사람 확정 버튼을 제공한다.
- `hospital-ui/index.html:321`: 폴링에서 병원 문의·도착 예정 환자·병원 정보를 조회한다.
- `hospital-ui/index.html:336`: `incoming.planning`을 포함한 응답을 저장한다.
- `hospital-ui/index.html:516`: 도착 예정 환자 상세에는 `transport_review`만 표시하고 추천은 표시하지 않는다.
- `hospital-ui/index.html:623`: 외부 유입 등록에 `request_id`가 없다. 목록·개별 퇴실 화면도 없다.
- `emergency-app/main.py:3527`: 원본 앱의 공통 서버 전송 경로다. `:3563`에서 공통 환자 화면으로 이동한다.
- `server/app.py`: 추천·재계산·병원 `incoming`의 HTTP 계약.
- `server/recommendation_view.py`: 결과 신선도와 워커 가동 상태를 함께 제공하는 계층.
- `server/solver_adapter.py`: 추천 내용·경고·`eligibility_reasons` 생성.

줄 번호는 이 문서 작성 시점 기준이다. 이후 변경되면 함수 이름과 API 경로를 기준으로 찾는다.

## 2. 환자 추천 API

| 용도 | 요청 | 호출 시점 |
|---|---|---|
| 추천 조회 | `GET /api/patients/{pid}/recommendation` | 도착 전 환자의 화면을 2~3초 간격으로 조회할 때 |
| 사용자의 재계산 요청 | `POST /api/patients/{pid}/replan`, JSON `{}` | 사용자가 재계산 버튼을 눌렀을 때만 |
| 현재 서버의 전체 처리 상태 | `GET /api/system/status` | 운영 상태 확인이 필요할 때. 추천 응답에도 배정 워커 상태가 포함됨 |

추천 폴링마다 `POST /replan`을 호출하면 안 된다. 입력 변경은 서버에서 새 계산 버전을 만들며 조회는 대기 상태를 확인한다. `GET`과 수동 `POST`의 응답에는 같은 형태의 `availability`가 붙는다.

다음은 구조를 설명하는 예시다. 실제 병원·시각·계산 결과가 아니다.

```json
{
  "status": "READY",
  "revision": 42,
  "created_at": 1790000020,
  "expires_at": 1790000050,
  "availability": {
    "worker_state": "RUNNING",
    "accepting_requests": true,
    "reason_code": null
  },
  "result": {
    "status": "RECOMMENDED",
    "hospital_id": "H02",
    "inquiry_id": 5,
    "destination_locked": false,
    "confirmable_now": true,
    "planned_start_at": 1790000160,
    "planned_end_at": 1790001960,
    "warnings": ["PRIORITY_UNVERIFIED"],
    "eligibility_reasons": [{"hospital_id":"H02","inquiry_id":5,"codes":[]}],
    "accepted_alternatives": [],
    "assumptions": {
      "parameter_source": "experiment",
      "treatment_duration_seconds": 1800,
      "max_wait_seconds": 3600,
      "occupied_release": "unknown_blocked_for_horizon"
    }
  }
}
```

### 추천 신선도와 워커 가동 상태를 구분한다

바깥 `status`는 저장된 추천의 신선도와 적용 가능 여부다. `availability.worker_state`는 현재 서버 프로세스에서 관측한 배정 워커 상태다. 두 필드를 하나로 합치지 않는다. 예를 들어 워커가 꺼진 뒤에도 아직 유효한 추천이 잠시 남을 수 있다.

| 바깥 `status` | 화면 처리 |
|---|---|
| `QUEUED` | 추천이 아직 없음. 워커 상태를 함께 표시하며 비활성인데 계산 중이라고 안내하지 않음 |
| `READY` | 현재 버전과 유효기간을 만족하는 `result`만 표시 |
| `STALE` | 이전 추천을 사용할 수 없음. 응답의 `result`는 null이며 추천 카드의 이전 확정 조작·이전 추천 사유를 지움 |
| `ERROR` | 추천 계산 오류 안내. 기존 환자·문의·대화 기록과 수동 업무 흐름은 유지 |
| `NOT_APPLICABLE` | 도착·종료·취소 건. 추천 폴링 종료, 기존 기록 조회는 유지 |

`result.status`는 `READY`일 때만 읽는다.

| 내부 `result.status` | 표시 의미 |
|---|---|
| `RECOMMENDED` | 현재 모델에서 유효한 수용 응답과 자원·시간 조건을 비교한 배정안 |
| `UNASSIGNED` | 현재 모델로 추천을 만들지 못함. 실제 병원이 환자를 수용할 수 없다는 확정 판정은 아님 |
| `DESTINATION_HELD` | 현재 목적지를 유지하며 모델상 시작시각을 재평가함 |
| `NEEDS_REVIEW` | 자원·조건·기한·지원 범위 등의 사유로 담당자의 확인이 필요함 |

### 워커 상태와 재계산 버튼

| `worker_state` | `accepting_requests` | 재계산 처리와 화면 안내 |
|---|---|---|
| `STARTING` | true | 시작 중임을 표시. 요청은 가능하지만 계산 완료를 보장하지 않음 |
| `RUNNING` | true | 명시적인 재계산 요청 가능 |
| `ERROR` | true | `PLANNER_ERROR`와 오류 안내. 재시도 요청은 가능하지만 성공을 보장하지 않음 |
| `STALLED` | true | `PLANNER_STALLED`와 처리 지연 안내. 요청은 가능하지만 정상 동작으로 표시하지 않음 |
| `DISABLED` | false | `PLANNER_DISABLED`. 재계산 버튼 비활성, 서버 담당자 확인 안내 |
| `STOPPED` | false | `PLANNER_STOPPED`. 재계산 버튼 비활성 |
| `STOPPING` | false | `PLANNER_STOPPING`. 재계산 버튼 비활성 |
| `UNKNOWN` | null | `PLANNER_UNOBSERVED`. 가동 여부를 관측하지 못한 상태이며 정상·비활성으로 추정하지 않음 |

`accepting_requests=false`인 상태의 `POST /replan`은 HTTP 409와 해당 `reason_code`를 반환한다. 이때 계산 버전·큐를 변경하지 않는다. 프런트엔드가 버튼을 잠그더라도 클릭과 요청 사이 서버 상태가 달라질 수 있으므로 409를 처리하고 최신 상태를 다시 조회한다. 종료 환자 등 상태가 잘못되면 기존 `INVALID_STATE` 409가 우선 적용된다.

## 3. 추천 내용을 표시할 때 지킬 계약

1. 병원 이름은 `hospital_id`를 서버 병원 목록과 연결한다. 병원 이름이나 목적지를 화면에서 임의 생성하지 않는다.
2. 추천·병원 수용 응답·실제 목적지 확정을 구분한다. 추천은 자원 예약도 수용 확답도 아니다.
3. 추천 카드의 확정 조작을 제공한다면 `RECOMMENDED`, `confirmable_now=true`, 유효한 `inquiry_id`를 확인하고 사람이 누른 뒤 기존 `POST /confirm`을 사용한다. 서버가 클릭 시점의 응답 만료·조건·자원 충돌을 다시 검사한다.
4. **기존 수동 확정 버튼 전체를 추천 성공에 종속시키지 않는다.** 운영 자원이 미등록·미확인일 때도 병원의 유효한 수용 응답을 사람이 확정하는 기존 경로가 있다. 추천 `UNASSIGNED`와 `confirmable_now=false`가 그 별도 경로를 일괄 차단할 근거는 아니다.
5. `planned_start_at`, `planned_end_at`, `wait_seconds`는 실험상 처치시간·허용 대기시간을 넣어 계산한 값이다. 확정된 실제 치료 시작시각·임상적 안전시간으로 표시하지 않는다. null을 0분이나 현재 시각으로 바꾸지 않는다.
6. `destination_locked=true`이면 추천만으로 기존 목적지를 바꾸지 않는다. `accepted_alternatives`도 자동 변경 명령이나 최적 순위·자원 예약이 아니다.
7. 이송 중 목적지 변경은 병원의 조정 판단·새 병원 수용 확인·최신 관측 위치를 거쳐 기존 `/change-destination` API로 사람이 요청한다.
8. `revision` 변경·유효기간 만료·환자 또는 서버 전환 시 이전 추천을 폐기한다. 이전 비동기 응답이 새 환자 화면에 들어오지 않도록 기존 `generation`/`contextVersion` 검사에 포함한다.
9. `warnings`, 조건 근거와 API 오류는 HTML 이스케이프 또는 `textContent`로 표시한다. 원문을 HTML로 삽입하지 않는다.

### 유효한 수용 응답은 있지만 계산에 사용하지 못하는 경우

`eligibility_reasons`는 **현재 유효한 수용 응답이 있는 병원**을 대상으로 현재 계획 모델의 입력 제한을 설명한다. 모든 후보 병원의 수용 가능 확률이나 모든 미배정 원인의 완전한 목록이 아니다. 빈 배열도 최종 배정이나 자원 충돌 해소를 보장하지 않는다.

```json
{
  "hospital_id": "H02",
  "inquiry_id": 5,
  "codes": ["OPERATING_RESOURCE_MISSING"]
}
```

| `codes` 값 | 표시할 의미 |
|---|---|
| `LOCATION_REQUIRED` | 이동시간 계산에 필요한 위치·경로 입력 확인 필요 |
| `UNSUPPORTED_RESOURCE` | 현재 계획 모델이 다루지 않는 자원 종류 |
| `CAPABILITY_MISMATCH` | 저장된 진료 분야와 환자의 필요 분야가 맞지 않음 |
| `RESTRICTED` | 현재 또는 예정된 관련 수용 제한 확인 필요 |
| `OPERATING_RESOURCE_MISSING` | 요구 자원의 운영 총량·점유 정보가 등록되지 않음 |
| `OPERATING_RESOURCE_UNKNOWN` | 운영 자원 상태가 미확인 |
| `OPERATING_RESOURCE_STALE` | 운영 정보 갱신 후 10분이 지나 재확인 필요 |
| `RESOURCE_CAPACITY_INSUFFICIENT` | 해당 병원의 총량에서 실제 점유를 뺀 수량이 요구보다 적음. 다른 확정 환자의 경합까지 모두 설명하는 코드는 아님 |

`warnings`에도 `코드:병원ID`가 제공된다. 이 두 표현을 중복 문장으로 계속 나열하기보다 병원별 확인 사유로 묶어 표시한다. 알 수 없는 새 코드는 일반 확인 사유로 남기며 숨기지 않는다.

| 추가 `warnings` 값 | 의미 |
|---|---|
| `NO_VALID_ACCEPTED_HOSPITAL` | 현재 유효한 수용 응답 자체가 없음 |
| `NO_ELIGIBLE_ACCEPTED_HOSPITAL` | 유효한 수용 응답은 있지만 현재 계산 입력 조건을 만족하는 후보가 없음 |
| `PRIORITY_UNVERIFIED` | 전문가 확인 우선순위가 아님 |
| `WAIT_LIMIT_EXCEEDED` | 실험 설정 대기 한도를 초과한 결과 |
| `HOSPITAL_REQUESTED_ADJUSTMENT` | 현재 병원이 조정을 요청함 |
| `DESTINATION_RESTRICTED`, `DESTINATION_CAPABILITY_MISSING` | 기존 목적지의 제한 또는 진료 분야 재확인 필요 |
| `TREATMENT_CONFIRMATION_REQUIRED:병원ID`, `PUBLIC_DATA_STALE:병원ID` | 공개 치료 조건이 미확인 또는 오래된 관측임 |

공개 API의 가용 병상 관측값을 계획 모델의 운영 총량·점유로 바꾸지 않는다. 미확인 공개 조건만으로 병원의 유효한 환자별 수용 응답을 무효화하지도 않는다. 현재 계획 모델은 `ed_bed`, `special_team`을 지원하며 다른 요구는 별도 검토가 필요하다.

## 4. 병원 화면의 추천 연결

`GET /api/hospitals/{hid}/incoming`은 기존 `incoming`, `affected`, `planning`에 `planning_availability`를 추가한다. `planning[]`의 각 행에도 같은 관측 상태인 `availability`가 붙는다.

```json
{
  "incoming": [],
  "affected": [],
  "planning_availability": {
    "worker_state": "DISABLED",
    "accepting_requests": false,
    "reason_code": "PLANNER_DISABLED"
  },
  "planning": []
}
```

- 상단 상태는 `planning_availability`, 선택 환자 결과는 `planning.find(row => row.patient_id === selectedPatientId)`로 연결한다.
- 도착 예정 환자가 없어 `planning=[]`이어도 상단의 워커 상태를 표시할 수 있다.
- 병원 `planning`은 현재 해당 병원으로 확정·이송 중인 환자만 포함한다. 도착 환자에게 추천 행이 없다는 이유로 오류를 표시하지 않는다.
- `transport_review`는 환자별 조건 변화와 병원 판단 흐름, `planning`은 별도 계획 계산이다. 추천 경고만으로 병원 `keep`/`adjust` 조작을 자동 실행하지 않는다.
- `affected`의 수량 기준 잠재적 영향과 환자별 `transport_review`를 구분해 현재의 재확인 설명을 유지한다.

## 5. 병원 화면의 외부 유입 후속 연결

API 전체 계약은 [외부 유입 환자 등록·조회·퇴실](EXTERNAL_ARRIVALS.md)을 따른다.

2026-10-14 추가: 이미 수동 정산했거나 원장이 없는 과거 기록은 [무차감 종결 API](ACCOUNTING_RECONCILIATION.md)로 처리할 수 있다. 정상 퇴실과 구분해 명시적 확인·사유·담당자·최신 대상/전체 자원 버전을 전송하는 흐름이 필요하다. 외부 유입의 `RECONCILED` 이력과 감사 결과를 조회하고 자동 차감을 제공하지 않는다. 이 화면은 아직 구현되지 않았으며 409 오류의 자동 우회로 사용하지 않는다.

| 작업 | 요청 | 완료 기준 |
|---|---|---|
| 신규 유입 등록 | `POST /api/hospitals/{hid}/external-arrivals` | 새 작업을 시작할 때 `request_id`를 한 번 생성, 네트워크 재시도는 같은 키·내용 사용 |
| 활성 목록 | `GET .../external-arrivals?state=ACTIVE` | 발생 시각·요구 자원·실제 반영 자원·메모·처리자 표시, `next_before_id` 페이지 처리 |
| 상세 | `GET .../external-arrivals/{id}` | 기록의 최신 `version`과 상태 확인 |
| 개별 퇴실 | `POST .../external-arrivals/{id}/discharge` | 조회한 `expected_version`과 담당자 전송, 성공 후 목록·자원·도착 예정 환자 갱신 |
| 퇴실 이력 | `GET .../external-arrivals?state=DISCHARGED` | 퇴실 상태·시각 표시, 이미 처리한 퇴실의 재시도는 중복 성공으로 처리 |
| 과거 기록 | `GET .../external-arrivals?state=LEGACY` | 자동 차감·퇴실 버튼 제공하지 않음 |

`resource_demands`와 `applied_resource_demands`를 구분한다. 등록 당시 없는 자원은 새로 만들어 점유하지 않으며, 퇴실 때 입력 요구 전체를 화면에서 직접 빼면 안 된다. 서버가 실제 반영량을 한 번 해제한다. 수동 점유 보정 후 정산 충돌 등 409는 최신 상태와 서버 설명을 보여 주고 자동 재시도로 숨기지 않는다. 기존 합계를 임의로 덮어써 우회하지 않는다.

현재 화면의 외부 유입 입력은 `source=synthetic` 병원에서만 활성화된다. 실제 이름의 공개 병원에 가상 운영 자원을 주입해 이 검증을 대신하지 않는다. 실제 의료기관으로 요청이 전달되는 기능은 없다.

## 6. 팀원별 작업 순서

### 환자 및 이송자 화면 담당

1. 현재 공통 API 서버와 이송 건 ID를 유지하고 추천 조회를 기존 3초 폴링에 추가한다.
2. 가동 상태·추천 신선도·내부 결과 상태를 별도로 표시한다.
3. 병원별 근거·유효기간·실험 가정·미확인 정보를 표시한다.
4. 명시적인 재계산 버튼과 409·서버 오류 처리를 연결한다.
5. 기존 수용 응답 확정·대체 변경·채팅을 회귀 확인한다.
6. 원본 `emergency-app/` 자체 화면으로 옮길 경우 공통 화면으로의 이동을 무엇으로 대체했는지 PR에 적고 같은 공통 DB/API를 사용한다. 개인 프로필 ID와 이송 건 ID를 합치지 않는다.

### 병원 화면 담당

1. 기존 `incoming` 응답의 `planning_availability`와 선택 환자의 `planning`을 표시한다.
2. 현재 수용 유지·조정 판단 화면과 추천 경고를 나란히 연결하되 자동 판단은 넣지 않는다.
3. 외부 유입 등록의 중복 요청 ID와 실패 재시도를 연결한다.
4. 활성 목록·개별 퇴실·퇴실 이력을 추가한다.
5. 자원·수용 제한·질문·응답·채팅·도착·기존 환자 퇴실을 회귀 확인한다.

## 7. 화면 연결 완료 기준

다음은 팀원 PR에서 재현 방법과 결과를 남길 항목이다. UI 추천 표시는 현재 미구현이며, 이 문서만으로 완료 처리하지 않는다.

| 경우 | 확인할 결과 |
|---|---|
| 배정 워커 비활성 | 추천 가동 상태가 비활성으로 표시되고 무한 계산 중 안내가 없음. 재계산 409와 계산 버전 불변 |
| 정상 시작과 결과 생성 | `QUEUED`에서 유효한 `READY`로 바뀌며 같은 `revision`의 결과 표시 |
| 수용 응답 없음 | `UNASSIGNED`와 `NO_VALID_ACCEPTED_HOSPITAL`을 수용 거절 확정과 구분 |
| 유효 수용 응답·운영 자원 없음 | `OPERATING_RESOURCE_MISSING` 근거 표시. 기존 병원 응답과 사람이 확정하는 흐름 유지 |
| 운영 자원 있음·추천 가능 | 사람이 추천 카드로 확정할 수 있음. 클릭 전에 자원이 달라지면 서버 409 후 최신 결과 표시 |
| 입력 버전 변경·결과 만료 | `STALE`에서 이전 추천 카드의 실행 조작을 제거. 환자·서버 전환 이후 이전 응답이 섞이지 않음 |
| 기존 목적지·조건 변경 | 목적지를 유지하며 재검토 이유 표시. 병원 판단과 새 수용 응답 없이 목적지 자동 변경 없음 |
| 도착·종료·취소 | `NOT_APPLICABLE` 처리, 추천 폴링 종료와 기록 조회 유지 |
| 외부 유입 재전송 | 같은 요청이 한 건만 저장되고 자원이 중복 점유되지 않음 |
| 외부 유입 개별 퇴실 재전송 | 실제 반영량이 한 번만 해제되고 관련 이송 건 재확인 결과 갱신 |
| 외부 유입 409·과거 기록 | 충돌 설명과 재조회 제공, `LEGACY` 자동 차감 없음 |
| 390px 모바일·데스크톱 | 추천 상태·긴 근거·한글 병원 이름·오류가 잘리지 않고 버튼이 실제로 조작 가능 |

2026-10-14 기존 공개 병원 시연 `18027`은 배정 워커가 `DISABLED`이고, 격리된 합성 데이터 검증 서버 `18030`은 배정·문의 워커를 켜서 확인했다. 신규 추천 계약 테스트21개 포함 전체 회귀621개와 독립 Chrome 컨텍스트의 전체 흐름7개가 통과했다. JS 오류·네트워크 실패는0건이다. 추천 생성·목적지 유지·자원 영향/회복·대체 수용/사람 변경·390px 재접속·도착/퇴실을 확인했다. **추천 패널과 외부 유입 개별 퇴실 버튼은 아직 없으므로 해당 부분은 실제 서버 JSON API로 검증했다.** 증거는 `.codex-build/recommendation-integration-20261014/qa-summary.json`, `qa-report.json`과 두 화면 캡처다. 포트와 가동 상태는 고정 계약이 아니므로 실제 실행 후 상태 API로 확인한다. 공개 시연 DB를 초기화하거나 가상 자원으로 덮어쓰지 않았다.

### 기존 화면 검사 선택자

| 과정 | 현재 구현된 선택자 |
|---|---|
| 공통 환자 등록 | `#intake-form`, `#symptoms`, `#intake-lat`, `#intake-lon`, `#consent`, `#submit-intake` |
| 환자 수용 문의 | `.hospital-card[data-hospital-id] [data-action=inquire]` |
| 병원 문의 선택·질문·응답 | `#openList [data-id]`, `#questionInput`, `[data-act=question]`, `[data-act=accept]` |
| 환자 질문 답변·사람 확정 | `.question-form`, `.question-answer`, `[data-action=confirm]` |
| 환자 채팅 | `#message-body`, `#send-message`, `#chat-items` |
| 병원 채팅 | `#chatBody`, `#chatSend`, `#chatLog` |
| 출발·감시·위치 갱신 | `#depart-button`, `#monitor-enable`, `#position-submit` |
| 병원 판단·도착·퇴실 | `[data-patient-action=keep]`, `[data-patient-action=adjust]`, `[data-patient-action=arrive]`, `[data-patient-action=discharge]` |
| 원본 환자 앱 공통 연결 | `#common-connect-form`, `#connect-latitude`, `#connect-longitude`, `#reporter-role`, `#connect-consent`, `#connect-submit` |

추천 표시용 선택자는 아직 없다. 구현 시 안정적인 ID 또는 `data-*`를 추가하고 PR에 적는다. 권장 예시는 `#recommendation-state[data-state]`, `#planner-state[data-state]`, `#replan-button`, `#recommendation-result`이며 **현재 존재하는 선택자가 아니다**. 병원 추천은 환자 ID별 데이터 속성을 함께 두면 선택 환자 전환 검증에 사용할 수 있다.

참고: [공통 API](API.md), [시스템 상태 API](SYSTEM_STATUS.md), [외부 유입 API](EXTERNAL_ARRIVALS.md), [대화 API](CHAT_API.md), [조건 재확인 API](TRANSPORT_REVIEW.md), [팀원 원본 앱 연결](TEAM_PATIENT_CONNECTION_20261013.md).
