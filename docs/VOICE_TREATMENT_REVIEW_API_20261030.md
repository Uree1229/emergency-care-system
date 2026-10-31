# 음성 상태 변경 → 치료조건 확인 → 배정 재평가 API

후속 메인 UI 연결은 [메인 음성·이송과 검토·추천·알림 연결](MAIN_UI_INTEGRATION_20261030.md)에서 완료했다. 아래는 원 서버 계약과 당시 완료 범위이며, UI는 같은 토큰·버전·선택·명시 확인 원칙을 사용한다.

2026-10-30. 서버 담당 작업이며 `paramedic-ui/`, `patient-ui/`, `hospital-ui/`는 수정하지 않았다. 기존 배정 목적함수나 솔버는 변경하지 않았다.

## 완료한 흐름

1. 기존 환자에 연결된 음성 초안에서 관찰 정보를 수정한다.
2. 서버가 치료 프로필에 영향을 주는 입력의 변화를 찾아 응답에 검토 필요 표시를 준다.
3. 변경 전후 치료 프로필·필요 진료·자원·우선순위와 명시 조건을 미리 보여 준다.
4. 담당자가 음성 초안과 치료조건을 명시적으로 확인한다.
5. 새 관찰과 선택한 조건을 같은 트랜잭션에서 환자정보에 적용한다.
6. 기존 문의·수용 응답을 종료하고 병원 재검토 알림, 활성 문의 작업 재대기, 배정 입력 버전 갱신을 처리한다.
7. 배정 워커가 새 조건과 새 병원 수용 응답을 사용해 추천을 재계산한다. 목적지 확정은 별도다.

**범위는 기존 환자, 목적지 확정 전 `WAITING` 상태다.** 이송 중 상태 수정·목적지 자동 변경은 추가하지 않았다. 최초 환자 등록은 기존 `submit` 흐름을 유지한다.

기존 `submit`의 호환 계약도 유지한다. 이 경로는 기존 환자의 치료조건을 자동 교체하거나 새 검토 API 사용을 강제하지 않는다. 새 화면은 `session.treatment_review` 표시를 읽어 이 문서의 미리보기·적용 경로로 연결해야 한다. 이번 작업은 해당 서버 계약과 실제 계산 연결을 구현한 것이며 클로드 화면의 연결 완료를 의미하지 않는다.

치료 프로필 제안은 기존 프로젝트 R1 실험 규칙을 재사용한다. 혈압 등 수치로 새로운 중증도·필요 자원을 추론하지 않는다. 활력징후만 수정하거나 같은 연령군 안에서 나이만 정정한 경우 기존 프로필을 일반 응급으로 낮추는 제안을 만들지 않는다. 직접 지정할 조건은 담당자가 선택한다.

## 1. 변경 감지 응답

기존 세션 응답의 `session.treatment_review`에 아래 요약을 추가했다. 신규 환자 초안이나 제출 완료 세션이면 `null`이다.

```json
{
  "review_required": true,
  "suggestion_available": true,
  "suggested_profile_id": "TR_CARDIAC_INTERVENTION",
  "source_changed_fields": ["stemi_suspected"],
  "endpoint": "/api/voice/sessions/VS-EXAMPLE/treatment-review",
  "can_apply": false,
  "blockers": ["VOICE_CONFIRMATION_REQUIRED"]
}
```

`review_required`는 치료조건 검토 표시다. 관찰 수치가 바뀌었다는 사실만으로 조건 변경을 강제하지 않는다. 초안 값·재확인 상태는 기존 `fields`, `field_metadata`, `followup`도 함께 표시한다.

## 2. 변경안 조회

```text
GET /api/voice/sessions/{sid}/treatment-review?actor_id=crew-demo
```

값·초안 버전·환자정보·문의·배정 상태를 변경하지 않는다. 응답의 `review`에는 다음이 포함된다.

| 필드 | 의미 |
|---|---|
| `token` | 현재 초안·환자 버전, 선택, 변경 전후 조건, 카탈로그에 묶인 검토 토큰 |
| `session_version`, `patient_version` | 각각의 독립 버전 |
| `selection` | 이번 변경안에서 사용할 선택. 적용 시 그대로 전달 |
| `current`, `proposed` | 치료 프로필, 유효 진료·자원, 명시 진료·자원, 우선순위·출처 |
| `changes` | 조건의 전후 차이. 명시 조건만 달라지는 경우도 표시 |
| `source_changes` | 제안 근거가 된 R1 항목 변화 |
| `reductions` | 빠지는 진료 분야, 감소하는 자원 또는 명시적 우선순위 하향 |
| `blockers`, `can_apply` | 초안 확인, 환자 버전, 환자 상태 등 현재 적용 가능 여부 |
| `unsupported_solver_resources` | 현재 솔버가 다루지 않는 자원. 값을 삭제하지 않고 검토 필요로 알림 |

확인하기 전에도 변경안을 볼 수 있다. 초안 확인만으로 값·버전이 달라지지 않으면 토큰은 유지되지만, 편집·환자정보 변경·카탈로그 변경 후에는 새로 조회한다.

## 3. 기존 조건 유지 또는 직접 지정 미리보기

```text
POST /api/voice/sessions/{sid}/treatment-review/preview
```

```json
{
  "actor_id": "crew-demo",
  "client_request_id": "preview-example-1",
  "expected_version": 2,
  "expected_patient_version": 1,
  "selection": {
    "mode": "custom",
    "treatment_profile_id": "TR_CARDIAC_INTERVENTION",
    "required_capabilities": ["응급의학"],
    "resource_demands": {"ed_bed": 1, "special_team": 1}
  }
}
```

이 POST도 읽기 전용이며 멱등 요청 원장에 저장하지 않는다. 공통 클라이언트가 보내는 요청 ID를 받아 검증하지만 실제 적용은 하지 않는다. `expected_patient_version`은 미리보기에서는 선택이다.

선택 방식:

- `{"mode":"suggested"}`: 서버의 제안을 검토해 선택. 해당 변화에 대한 제안이 있을 때만 허용.
- `{"mode":"keep"}`: 기존 치료 프로필·명시 요구를 유지. 새 관찰의 적용 여부와는 다르다.
- `{"mode":"custom", ...}`: 담당자가 조건을 직접 지정. 지정하지 않은 명시 요구는 유지한다. `treatment_profile_id:null`은 기존 프로필 제거다.

`required_capabilities`, `resource_demands`는 **명시적으로 지정하는 요구**다. 프로필과 합쳐진 유효 조건을 그대로 다시 넣으면 이전 프로필 요구가 명시 조건으로 남을 수 있으므로, 의도한 요구만 지정한다. 미리보기는 명시 조건과 합쳐진 유효 조건을 둘 다 보여 준다.

## 4. 명시적 확인·적용

먼저 기존 확인 API로 현재 음성 초안을 확인한다.

```text
POST /api/voice/sessions/{sid}/confirm
```

그다음 치료조건을 확인한 별도 화면 동작으로 아래를 호출한다. 음성의 “확인”, “전송”만으로 이 API를 자동 호출하지 않는다.

```text
POST /api/voice/sessions/{sid}/treatment-review/apply
```

```json
{
  "actor_id": "crew-demo",
  "client_request_id": "apply-example-1",
  "expected_version": 2,
  "expected_patient_version": 1,
  "review_token": "변경안에서 반환한 64자 토큰",
  "confirmed": true,
  "selection": {"mode":"suggested"},
  "allow_reduction": false,
  "reason": "담당자가 새 관찰과 필요한 치료조건을 확인함"
}
```

미리보기의 `selection`과 토큰을 그대로 보낸다. 필요한 진료·자원이 줄어드는 변경은 `reductions`를 표시하고 담당자가 별도로 확인했을 때만 `allow_reduction:true`를 보낸다. 예를 들어 현재 카탈로그의 일반 응급→심장 프로필 변경에도 `응급의학`이 빠지고 `심장내과`가 추가될 수 있으므로, 더 전문적인 이름이라는 이유로 조건 감소를 무시하지 않는다.

적용은 기존 환자정보 수정 서비스를 한 번 사용한다. 다음이 함께 커밋되거나 함께 롤백된다.

- 선택한 프로필·명시 요구와 확인한 음성 관찰 저장
- 환자 버전 증가, 음성 세션 제출 완료
- 기존 진행 문의·수용 응답 종료와 최신 버전 재검토 요구
- 병원 환자정보 변경 알림, 활성 문의 작업 재대기
- 배정 입력 버전 증가와 과거 추천 무효화
- 치료조건 검토 감사 사건과 요청 재시도 원장

값과 확인 상태, 조건이 모두 같은 `keep` 적용은 기존 환자 버전·수용 응답·배정 버전을 유지한다. 진료과 순서만 바뀐 것도 같은 조건으로 본다. 변경안을 보기만 하거나 적용하지 않으면 기존 수용을 유지한다. `keep`을 **적용**하면 조건은 유지하되 새 관찰은 저장하므로, 실제 관찰이 달라진 경우에는 기존 환자정보 수정 계약에 따라 재확인이 필요하다.

실행 중인 문의 워커가 있다면 재대기된 작업을 기존 정책대로 처리한다. 적용 API 자체는 병원을 새로 수용시키거나 목적지를 확정·변경하지 않는다. 로컬 연결본은 자동 문의 워커를 끄고 배정 워커만 실행한다.

## 5. 재평가 응답과 결과 조회

기존 `patient`, `session`, 후보·추천 조회 경로와 함께 다음을 반환한다.

```json
{
  "reevaluation": {
    "requested": true,
    "status": "STALE",
    "revision": 42,
    "availability": {
      "worker_state": "RUNNING",
      "accepting_requests": true,
      "reason_code": null
    },
    "reason": "PATIENT_INPUT_CHANGED",
    "automatic_destination_change": false
  }
}
```

`requested:true`는 변경된 배정 입력이 DB에 대기한다는 뜻이며 계산 완료가 아니다. 과거 결과가 있으면 `STALE`, 없으면 `QUEUED`가 반환될 수 있다. 실제 결과는 기존 `/api/patients/{pid}/recommendation`에서 조회한다.

워커가 꺼져 있어도 이미 확인한 환자정보 변경을 롤백하지 않는다. `availability.worker_state:DISABLED`와 함께 대기 상태를 표시해야 한다. 새로운 병원의 유효 수용 응답이 없으면 재계산 후에도 미배정일 수 있다. ICU/NICU 등 현재 솔버가 지원하지 않는 자원은 누락시키거나 성공으로 표시하지 않고 `NEEDS_REVIEW`로 남긴다.

## 6. 화면 담당 연결 예시

기존 `VoiceClient`를 그대로 사용할 수 있다. 다음은 담당자가 내용을 검토한 화면 동작에 연결할 예시이며 자동 실행용이 아니다.

```javascript
const sid = client.session.id;
const path = `/api/voice/sessions/${encodeURIComponent(sid)}`;
const readReview = () => client.api(
  `${path}/treatment-review?actor_id=${encodeURIComponent(client.actorId)}`
);

// 새 관찰 처리 뒤 session.treatment_review.review_required가 true면 변경안 표시.
let {review} = await readReview();

// 담당자가 음성 초안을 확인한 경우.
await client.write('confirm', {confirmed: true});
({review} = await readReview());

// 담당자가 표시된 치료조건과 감소 항목까지 검토하고 적용한 경우.
const result = await client.write('treatment-review/apply', {
  expected_patient_version: review.patient_version,
  review_token: review.token,
  selection: review.selection,
  confirmed: true,
  allow_reduction: reductionAcknowledged,
  reason: '담당자가 변경 전후 치료조건을 확인함',
});
// result.reevaluation의 요청/워커 상태를 표시하고 recommendation_endpoint 조회.
```

다른 선택을 고르면 `client.write('treatment-review/preview', {selection})`로 먼저 그 선택의 변경안·토큰을 받는다. 응답 유실은 같은 본문·요청 ID로 재시도한다. 409는 최신 환자·초안을 다시 읽고 새 변경안을 검토한다. 중복 응답은 최초 처리 스냅샷일 수 있으므로 화면 상태를 새로 조회한다.

## 7. 오류와 범위

| 코드 | 처리 |
|---|---|
| `VOICE_PATIENT_REQUIRED` | 기존 환자 ID에 연결된 새 음성 세션 생성 |
| `VOICE_CONFIRMATION_REQUIRED` | 현재 초안의 명시적 확인 필요 |
| `TREATMENT_CONFIRMATION_REQUIRED` | 치료조건 확인 값이 true가 아님 |
| `VOICE_VERSION_CONFLICT`, `VOICE_PATIENT_VERSION_CONFLICT` | 최신 초안·환자로 재검토 |
| `TREATMENT_REVIEW_STALE` | 선택·카탈로그 등이 바뀜. 변경안 재조회 |
| `TREATMENT_REDUCTION_CONFIRMATION_REQUIRED` | 감소 항목을 표시하고 별도 확인 |
| `TREATMENT_SUGGESTION_UNAVAILABLE` | 기존 유지 또는 직접 지정으로 미리보기 |
| `INVALID_TREATMENT_SELECTION` | 선택 방식·필드·자료형·범위 보완 |
| `INVALID_STATE` | 목적지 확정 이후에는 이 API로 변경하지 않음 |

작업자 ID는 기존 시연용 소유자 구분이다. 이번 작업에서 운영 인증이나 임상적으로 검증된 진단 규칙을 추가한 것은 아니다.

## 8. 검증 기록

새 테스트: `test_voice_treatment_review.py`, `test_voice_treatment_review_http.py`.

실제 로컬 HTTP와 기존 배정 워커를 사용한 가상 사례에서 다음을 확인했다.

- 일반 응급 조건으로 수용된 환자 → 새 ST 상승 관찰 → 검토 표시.
- 미리보기·초안 확인까지만으로는 기존 수용·치료조건 유지.
- 치료조건 승인 후 기존 수용 종료, 과거 추천 무효화, 심장 진료 후보 재탐색.
- 새 병원의 수용 응답 전에는 미배정. 수용 응답 후 워커 재계산에서 심장 병원 추천.
- 추천 결과가 있어도 환자는 `WAITING`이며 목적지는 자동 확정되지 않음.
- 잘못된 버전·선택·감소 미확인·처리 중 실패의 무변경/전체 롤백, 동일 요청 중복 방지.

배정 워커 연결 시험은 `seconds=0`의 기존 유효 휴리스틱을 사용했다. 알고리즘 우월성이나 실제 치료 예후를 검증한 것이 아니다. 실제 마이크·외부 지도 API·실제 병원은 사용하지 않았다.

회귀와 소스 해시는 `.codex-build/voice-treatment-review-20261030/server-verification.json`, `server-verification.log`에 보관한다. 실행본 버전은 capabilities의 `treatment_review_revision: "voice-treatment-review-20261030-v1"`로 확인한다. 로컬 기존 DB의 백업과 데이터 보존 결과도 같은 검증 폴더에 기록한다.

최종 검증은 **관련 테스트461개 통과, 실패·오류0**, 실행 전후 소스 해시 일치다. 새 검토 테스트17개·HTTP/실제 배정 워커 연결5개를 포함한다. 기존 앱 통신 모듈의 음성 초안12단계·가상 환자/병원11단계도 통과했다. 최초 전체 실행의 Windows 하위 프로세스 검증 진입점 문제는 별도 기록으로 보존하고, 실행 스크립트의 main 보호만 수정한 뒤 기존 3초 솔버 제한·검증 기준 그대로 재실행했다. 최종 통과 수에 최초 실행을 더하지 않는다.

기존 로컬18032에 반영했고 배정 워커 RUNNING, 자동 문의 워커 DISABLED를 확인했다. 기존 환자·문의·음성 시험 데이터의 건수·내용은 백업과 같다. 배정 계산 결과와 계산 상태만 워커가 갱신한다. 백업은 `before-treatment-review.db`, 보존·가동 상태 확인은 `live-verification.json`이다.
