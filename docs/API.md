# 공통 서버 API 명세 (병원 웹 · 환자 웹용)

이 문서는 병원 웹과 환자 모바일 웹을 만드는 팀원을 위한 명세입니다. 서버 코드는 `server/`에 있고, 업무 규칙의 원본은 `server/service.py`입니다.
이송 건별 시스템 알림과 자유대화 API는 [대화 API 명세](CHAT_API.md)에 정리했습니다. 이송자·병원 화면과 연결됐으며, 각자 PC에서 실행하는 방법은 [팀원 테스트 가이드](TEAM_TEST_GUIDE.md)를 따릅니다.
초기 자동 문의는 환자 생성 시 `auto_inquiry: true`로 켭니다. `inquiry_policy`로 `fixed_1`, `fixed_3`(기본), `fixed_5`, `observed_v2`, `observed_v3`를 선택할 수 있습니다. 상태 조회·중지·재개와 규칙은 [자동 문의 명세](INQUIRY_WORKFLOW.md)를 따릅니다. 자동 활성화를 생략하면 기존 수동 문의를 유지합니다.
`--seed` 시연 데이터는 가상입니다. `data/public.db`에는 실제 공공 API 조회 자료를 저장하며, 병원의 내부 시스템이나 실제 환자 수용 업무와 연결된 서비스는 아닙니다.

## 1. 바로 시작하기

```
python -m server.app --seed --scenario sudden --port 8000
```

| 옵션 | 의미 |
|---|---|
| `--seed` | 가상 병원 10곳(H01~H10)을 새로 넣습니다. 기존 `data/demo.db`는 지워집니다. |
| `--scenario 단계` | 시연 상태를 만듭니다. 단계: `inquiry` → `accepted` → `in_transit` → `sudden` (누적) |
| `--cors-origin` | 기본값 `*`이라 어느 주소의 화면에서든 호출할 수 있습니다. 끄려면 `none`을 줍니다. |
| `--host 0.0.0.0` | 같은 와이파이의 휴대폰에서 접속할 때 씁니다. |

시나리오(`sudden` 기준)로 만들어지는 상태:

| 환자 | 상태 | 화면에서 확인할 것 |
|---|---|---|
| DEMO-A | 소아 고열. H04로 이동 중 | H04가 돌발 환자 B를 등록해 **영향 환자**로 표시됨. 병원 판단(유지/조정) 대기 |
| DEMO-C | 흉통, 위험 신호 있음 | `call_119: true`. H07이 추가 질문을 보내 **답변 대기**, H02는 미응답 강조 |
| DEMO-D | 손목 골절 의심 | H02가 수용 → 환자가 **목적지 확정** 버튼을 누를 차례 |
| DEMO-E | 낙상 | H01로 이동 중. 영향 없음 |

## 2. 공통 규칙

- **주소:** `http://서버:8000/api/...`. 요청과 응답 본문은 UTF-8 JSON입니다.
- **시각:** 모두 **UTC epoch 초(정수)**입니다. 화면에서 한국시간으로 바꿔 표시하세요.
  예: `new Date(at * 1000).toLocaleString('ko-KR', {timeZone: 'Asia/Seoul'})`
- **갱신:** 웹소켓이나 푸시는 쓰지 않습니다. 보고 있는 화면의 조회 API를 **2~3초마다 다시 호출**하세요.
- **로그인 없음:** 병원은 경로의 병원 ID(`H01` 등)로 구분합니다. 시연용 단순화입니다.
- **오류:** HTTP 4xx와 함께 다음 형태로 응답합니다. 화면에는 `message`를 그대로 보여 주면 됩니다.
  ```json
  {"error": "RESOURCE_CONFLICT", "message": "자원 여유가 부족합니다. ...", "shortfalls": [...]}
  ```
- **변경 API의 응답:** 대부분 바뀐 뒤의 전체 화면 데이터를 돌려줍니다. 환자 API는 환자 상세를, 병원 정보 API는 병원 상세를 돌려줍니다. 그대로 화면 상태로 교체하면 됩니다.
- **`handled_by`:** 병원 쪽 변경 요청에는 처리자 이름을 넣으세요. 이력(S15)에 남습니다.

## 3. 상태 흐름

### 환자 (`patient.state`)
```
WAITING ──목적지 확정──▶ CONFIRMED ──출발──▶ IN_TRANSIT ──병원 도착 확인──▶ ARRIVED ──퇴실──▶ COMPLETED
   └──────────────── 취소 / 미도착 ────────────────▶ CANCELLED
```
- `needs_adjustment: 1`은 병원이 "조정 필요"로 판단한 상태입니다. 이때만 다른 병원에 다시 문의하고 목적지를 바꿀 수 있습니다. 목적지는 **자동으로 바뀌지 않습니다.**
- `call_119: true`이면 위험 신호가 있다는 뜻입니다. 119 전화 버튼을 맨 위에 두고, 병원 응답을 기다리지 말라는 안내를 보여 주세요.
- `effective_priority`: 전문가 평가만 그대로 쓰고, 자기신고와 미확인은 4(높음)로 다룹니다. 정렬과 강조에 쓰세요.
- `priority_source`가 `self`이면 "자기신고", `expert`이면 "전문가 확인", `unknown`이면 "미확인"으로 구분해 표시합니다(P12, H18).

### 문의 (`inquiry.state`)
```
PENDING ─(병원)확인 중/추가 질문─▶ CHECKING ─┬─수용─▶ ACCEPTED ─환자가 확정─▶ CONFIRMED
                                           └─불가─▶ DECLINED
ACCEPTED ─10분 경과─▶ EXPIRED      어느 단계든 ─취소·다른 병원 확정─▶ CANCELLED
```
- **수용(ACCEPTED)은 자리를 잡아 두는 게 아닙니다.** 여러 병원이 수용해도, 환자가 확정한 한 곳만 자원을 씁니다.
- `overdue: true`: 마지막으로 보낸 뒤 60초가 지났습니다. 강조해서 표시하세요(H26, P24).
- `can_retry: true`: 120초가 지나 재문의 버튼을 켜도 됩니다.
- `usable: true`: 지금 이 응답으로 목적지를 확정할 수 있습니다. 확정 버튼은 이 값이 참일 때만 켜세요.

## 4. 환자 웹 API

| 화면 동작 | 메서드·경로 | 본문 | ID |
|---|---|---|---|
| 환자 정보 입력 | `POST /api/patients` | 아래 참고 | P02~P10 |
| 상세·상태 조회(폴링) | `GET /api/patients/{pid}` | | P22, P37 |
| 전송 전 수정 | `PATCH /api/patients/{pid}` | 바꿀 필드만 | P13 |
| 후보 병원 | `GET /api/patients/{pid}/candidates` | | P17~P20 |
| 문의 정책 목록 | `GET /api/inquiry-policies` | | 공개 5개 정책의 이름·버전·설정 |
| 자동 문의 상태 | `GET /api/patients/{pid}/inquiry-workflow` | | 초기 WAITING 전용 |
| 자동 문의 시작·일시정지·정책 선택 | `POST /api/patients/{pid}/inquiry-workflow` | `{"enabled": true, "policy_name": "fixed_3"}`. `policy_name` 선택, `enabled` 필수 | 실행 중 다른 정책으로 변경 불가 |
| 문의 정책 결정 이력 | `GET /api/patients/{pid}/inquiry-decisions?limit=20` | | 최근순, limit 1~100 |
| 수용 문의 보내기 | `POST /api/patients/{pid}/inquiries` | `{"hospital_ids": ["H04","H01"]}` | P21 |
| 재문의 | `POST /api/patients/{pid}/inquiries/{iid}/retry` | | P24 |
| 문의 취소 | `POST /api/patients/{pid}/inquiries/{iid}/cancel` | `{"reason": "증상 호전"}` | P29 |
| 병원 질문에 답변 | `POST /api/patients/{pid}/inquiries/{iid}/answer` | `{"answer": "식은땀 있음"}` | P23 |
| 목적지 확정 | `POST /api/patients/{pid}/confirm` | `{"inquiry_id": 1}` | P27, P28 |
| 출발 | `POST /api/patients/{pid}/depart` | `{"lat": .., "lon": ..}` (생략하면 입력 위치) | P31, P34 |
| 목적지 변경 | `POST /api/patients/{pid}/change-destination` | `{"inquiry_id": 5, "lat": .., "lon": ..}` | P38 |
| 이송 취소 | `POST /api/patients/{pid}/cancel` | `{"reason": "119 이송"}` | P41 |

### 환자 정보 입력 본문
```json
{
  "id": "선택. 없으면 서버가 생성",
  "reporter_role": "guardian",          // self(본인) / guardian(보호자) / transporter(이송자)
  "consent": true,                      // 필수. false면 400 CONSENT_REQUIRED
  "relation": "부모", "contact": "010-...",
  "age": 7,                             // 모르면 null (P09)
  "symptoms": "고열 39.5도, 처짐",
  "onset_at": 1789995800,               // 모르면 null
  "extra": {"복용약": "해열제"},          // 모르는 항목은 키를 넣지 않음
  "danger_signs": ["의식 저하"],          // 하나라도 있으면 call_119
  "priority": null, "priority_source": "unknown",
  "required_capabilities": ["소아청소년과"],
  "resource_demands": {"ed_bed": 1},     // ed_bed=진료 자리, special_team=전문팀
  "lat": 35.145, "lon": 126.86
}
```
`required_capabilities`에 쓸 수 있는 값(가상 데이터 기준): 응급의학, 소아청소년과, 정형외과, 신경외과, 심장내과, 외과.

### 초기 자동 문의 정책 선택

수용 확정 이후 조건 감시와 대체병원 자동 문의는 별도 [이송 조건 재확인 API](TRANSPORT_REVIEW.md)를 사용합니다. 환자 상세의 `transport_review`, `transport_inquiry_workflow`로 상태를 표시하며, 위치 갱신은 `PATCH /api/patients/{pid}/transport-position`입니다. 감시 중 병원 판단에는 `expected_review_version`과 `expected_risk_signature`가 필요합니다. 목적지는 기존 `/change-destination`으로 사람이 변경합니다.

`confirm`·`change-destination`이 성공하면 환자 상세의 `transport_baseline`에 해당 병원 ID, 확정 시각, 환자 요구와 관련 병원 조건이 저장됩니다. 감시는 별도로 활성화해야 하며, 나중에 켜도 저장된 확정 조건과 현재 조건을 비교합니다. 과거 확정 조건이 없는 기존 DB 건은 첫 감시 설정 시 관측으로 시작합니다. 이 필드를 클라이언트가 직접 작성하지 않습니다.

공개 정책 목록은 고정 1·3·5곳과 관측 V2·V3의 5개다. `observed_v3`는 개발에서 선택한 추가 문의 대기 상한과 최근 이력 범위를 사용한다. 응답의 `source_variant`가 선택한 내부 설정을 가리키며 `config.max_wait_seconds`, `config.history_window_seconds`로 실제 값을 확인한다. 내부 개발 후보는 공개 목록에 표시하지 않는다. [개발 선택과 별도 검증](INQUIRY_V3_EXPERIMENT.md).

환자 생성 본문에 `"auto_inquiry": true, "inquiry_policy": "observed_v2"`를 추가하면 선택한 정책으로 시작합니다. `auto_inquiry`만 true면 `fixed_3`를 사용합니다. `inquiry_policy`만 지정하면 정책을 저장한 PAUSED 작업을 만들고 자동 문의를 보내지 않습니다. 두 필드를 모두 생략하면 자동 작업을 만들지 않습니다.

`GET /api/inquiry-policies`는 `{"policies": [...]}`를 반환합니다. 각 정책에 `name`, `label`, `version`, `batch_size`, `wait_seconds`, `review_seconds`, `max_contacts`, `deadline_seconds`, `experimental`, `config`가 있습니다. 고정 정책은 묶음마다 1·3·5곳에 문의하며 기본 대기는 60초입니다. `observed_v2`는 첫 회신·타임아웃·서버가 보낸 미해결 문의·이동시간을 비교하는 경험적 정책입니다. 수용 확률이나 치료 결과를 예측하는 모델이 아니며 서버 적용의 성능 개선은 별도로 검증해야 합니다. 상세 계산 규칙은 [자동 문의 명세](INQUIRY_WORKFLOW.md)를 참고하세요.

기존 작업의 정책을 바꾸려면 먼저 `{"enabled":false}`로 일시정지하고, 같은 경로에 `{"enabled":true,"policy_name":"observed_v2"}`를 보냅니다. PAUSED 상태에서 `enabled:false`와 새 이름을 보내 정책만 바꿀 수도 있습니다. 실행 중 같은 정책으로 시작 요청을 반복하면 작업과 제한시간을 유지합니다. 이미 문의한 병원은 정책 변경·재시작 후에도 보존합니다. 목적지가 확정된 뒤에는 초기 자동 문의를 시작할 수 없습니다.

작업에는 `policy_name`, `policy_version`, `policy_config`를 저장하고, 조회의 `policy`에는 전체 명세를 반환합니다. 저장된 버전·설정과 코드가 불일치하면 진행을 거부합니다. `inquiry_workflow.latest_decision`은 최근 결정 또는 null이며, 이력 API는 `{"patient_id":"...","decisions":[...]}`를 반환합니다. 결정에는 `at`, `policy_name`, `policy_version`, `job_version`, `reason`, `hospital_ids`, `evidence`가 있습니다. 근거의 점수·병원별 관측 집계는 문의 선택 이유이며 실제 수용 응답이 아닙니다. 정책 실행은 수용 응답이나 목적지를 자동 확정하지 않습니다.

### 환자 상세 응답 (요약)
```json
{
  "id": "DEMO-A", "state": "IN_TRANSIT", "version": 3,
  "call_119": false, "effective_priority": 4, "priority_source": "unknown",
  "destination_hospital_id": "H04", "eta_at": 1790000750,
  "needs_adjustment": 0, "adjustment_note": null,
  "destination": {"id": "H04", "name": "가상 소아전문응급센터", "phone": "000-0000-0400", "lat": 35.13, "lon": 126.86, "source": "synthetic"},
  "inquiries": [
    {"id": 1, "hospital_id": "H04", "hospital_name": "가상 소아전문응급센터", "hospital_phone": "000-0000-0400",
     "state": "CONFIRMED", "reason": null, "question": null, "answer": null,
     "elapsed_seconds": 500, "attempts": 1, "overdue": false, "can_retry": false, "usable": false}
  ],
  "notifications": [
    {"id": 35, "at": 1789999805, "hospital_id": "H01", "type": "inquiry_responded",
     "data": {"inquiry_id": 2, "action": "accept"}}
  ]
}
```
`notifications.type` 값과 화면 문구 예시:

| type | 의미 | 문구 예시 |
|---|---|---|
| `inquiry_responded` | 병원 응답. `data.action`은 checking/accept/decline | "H04가 수용 가능으로 응답했습니다" |
| `inquiry_question` | 병원 추가 질문 | "H07이 추가 정보를 요청했습니다" |
| `inquiry_expired` | 수용 응답 만료 | "응답 유효시간이 지나 다시 문의가 필요합니다" |
| `adjustment_needed` | 병원이 조정 필요로 판단 | "병원 상황이 바뀌었습니다. 담당자에게 연락하거나 다른 병원을 찾아보세요" |
| `hospital_keeps` | 병원이 수용 유지 | "병원이 수용을 유지합니다" |
| `destination_changed` | 목적지 변경 완료 | |
| `arrival_confirmed` | 병원이 도착 확인 | |
| `patient_cancelled`, `no_show` | 취소, 미도착 처리 | |

### 후보 병원 응답
```json
{
  "candidates": [
    {"hospital_id": "H02", "name": "...", "phone": "...", "lat": 35.19, "lon": 126.89,
     "travel": {"duration_seconds": 120, "distance_meters": 901, "provider": "synthetic"},
     "last_updated_at": 1789999970, "resources_enough": true,
     "reasons": ["필요 진료 분야 보유: 정형외과", "ed_bed 여유 4", "이동 약 2분 (synthetic)"],
     "checks": ["가상 운영 상태"]}
  ],
  "excluded": [{"hospital_id": "H05", "reason": "NO_COMPATIBLE_CAPABILITY", "detail": ["정형외과"]}],
  "empty_reason": null,
  "note": "추천 목록은 수용 보장이 아닙니다. ..."
}
```
- `reasons`는 추천 이유, `checks`는 확인이 필요한 항목입니다. 둘 다 그대로 나열하면 됩니다(P20).
- `empty_reason`: 후보가 없거나 모두 부족할 때의 이유입니다. 값은 `NO_COMPATIBLE_HOSPITAL`, `ALL_RESTRICTED_OR_INCOMPATIBLE`, `RESOURCE_SHORT_OR_UNCONFIRMED`입니다.
- 이 목록은 규칙 기반 필터입니다. 솔버 배정안이 아닙니다.
- 길찾기 버튼(P33)은 `lat`, `lon`으로 카카오맵이나 네이버지도 URL을 만들면 됩니다. 전화 버튼(P25)은 `tel:` 링크입니다.

## 5. 병원 웹 API

| 화면 동작 | 메서드·경로 | 본문 | ID |
|---|---|---|---|
| 병원 목록 | `GET /api/hospitals` | | |
| 병원 상세(자원·제한) | `GET /api/hospitals/{hid}` | | H03~H11 |
| 자원 수정 | `PUT /api/hospitals/{hid}/resources/{type}` | `{"capacity":6,"occupied":4,"status":"available","handled_by":"..","expected_version":2}` | H05 |
| 제한 등록 | `POST /api/hospitals/{hid}/restrictions` | `{"scope":"all","reason":"인력 부족","ends_at":1790003600}` | H07 |
| 제한 해제 | `DELETE /api/hospitals/{hid}/restrictions/{rid}` | | H07 |
| 문의 목록(폴링) | `GET /api/hospitals/{hid}/inquiries` | | H15, H16, H26 |
| 응답 | `POST /api/inquiries/{iid}/respond` | 아래 참고 | H19~H25 |
| 도착 예정 + 영향 환자(폴링) | `GET /api/hospitals/{hid}/incoming` | | H29~H31, H34 |
| 돌발 유입 등록 | `POST /api/hospitals/{hid}/external-arrivals` | `{"resource_demands":{"ed_bed":1},"note":"..","handled_by":".."}` | H33 |
| 유지/조정 판단 | `POST /api/hospitals/{hid}/patients/{pid}/decision` | `{"decision":"keep"\|"adjust","note":"..","handled_by":".."}` | H35, H36 |
| 도착 확인 | `POST /api/hospitals/{hid}/patients/{pid}/arrive` | | H37, H38 |
| 미도착 | `POST /api/hospitals/{hid}/patients/{pid}/no-show` | `{"reason":"미도착"}` | H39 |
| 퇴실 | `POST /api/hospitals/{hid}/patients/{pid}/discharge` | | H39 |

### 응답 본문
```json
{"action": "accept", "handled_by": "박간호", "expected_version": 2}
{"action": "decline", "handled_by": "박간호", "reason": "병상", "expected_version": 2}
{"action": "checking", "handled_by": "박간호"}
{"action": "question", "handled_by": "박간호", "question": "복용 중인 약이 있나요?"}
```
- 불가 사유 `reason`: 인력, 장비, 병상, 진료 불가, 기타 중 하나입니다.
- `expected_version`에는 목록에서 받은 문의의 `version`을 그대로 넣으세요. 그 사이 다른 담당자가 처리했으면 `409 CONFLICT`가 오고, 이때는 목록을 새로고침해 보여 주면 됩니다(H25).
- 같은 버튼을 두 번 눌러도 오류가 나지 않습니다. 이때 응답에 `"duplicate": true`가 옵니다(H24).
- 수용을 눌렀는데 자원이 부족하면 `409 RESOURCE_CONFLICT`(`shortfalls` 포함), 제한 중이면 `409 RESTRICTED`가 옵니다(H23).
- 응답의 `warning`이 있으면 자원 상태를 모르는 경우입니다. 화면에 경고로 보여 주세요.

### 문의 목록 응답
```json
{
  "open": [
    {"id": 3, "state": "CHECKING", "version": 2, "elapsed_seconds": 480, "overdue": true, "attempts": 1,
     "question": "흉통이 쉬면 나아지나요?", "answer": null, "handled_by": "김간호(H07)",
     "patient": {"id": "DEMO-C", "age": 58, "symptoms": "가슴 통증 20분 지속", "danger_signs": ["흉통 지속"],
                 "priority": null, "priority_source": "unknown", "effective_priority": 4,
                 "required_capabilities": ["심장내과"], "resource_demands": {"ed_bed": 1, "special_team": 1},
                 "reporter_role": "self", "contact": "010-...", "eta_at": null}}
  ],
  "recent": [ {"...": "끝난 문의 30건. closed_by_patient=true면 환자 측 취소·다른 병원 확정"} ]
}
```
`open`은 미응답 강조 → 우선순위 → 오래된 순으로 이미 정렬되어 있습니다.

### 병원 상세의 자원 항목
```json
{"resource_type": "ed_bed", "capacity": 6, "occupied": 6, "expected": 1, "free": -1,
 "status": "available", "over_capacity": false, "stale": false, "age_seconds": 60, "updated_at": 1789999940, "version": 3}
```
- `occupied`는 실제 치료 중, `expected`는 확정된 도착 예정, `free`는 `capacity - occupied - expected`입니다. 음수이면 초과 예정입니다.
- `stale: true`: 10분 넘게 갱신되지 않았습니다. "오래된 정보" 경고를 띄우세요(H11).
- `restrictions[].status`: `active`(제한 중), `recheck`(종료 예정 시각이 지남, 재확인 필요), `scheduled`(시작 전).

### 도착 예정 + 영향 환자 응답
```json
{
  "incoming": [{"id": "DEMO-A", "state": "IN_TRANSIT", "eta_at": 1790000750, "eta_remaining_seconds": 750,
                "needs_adjustment": false, "resource_demands": {"ed_bed": 1}, "...": "환자 요약"}],
  "affected": [{"patient": {"id": "DEMO-A", "...": ".."},
                "shortfalls": [{"resource_type": "ed_bed", "free_after_earlier": 0, "need": 1}],
                "needs_adjustment": false}]
}
```
`affected`는 현재 여유로 다 받을 수 없는 도착 예정 환자 목록입니다. 도착이 빠른 순으로 자원을 채워 계산하며, 시간축 없이 수량만 비교한 1차 판단입니다. 병원은 이걸 보고 **유지** 또는 **조정 필요**를 누릅니다.

## 6. 오류 코드

| 코드 | HTTP | 의미 · 화면 처리 |
|---|---|---|
| `INVALID`, `INVALID_JSON` | 400 | 입력 오류. `message` 표시 |
| `INVALID_POLICY` | 400 | 등록되지 않은 문의 정책. 정책 목록을 다시 조회 |
| `CONSENT_REQUIRED` | 400 | 개인정보 동의 필요 |
| `LOCATION_REQUIRED` | 400 | 위치 입력 필요 |
| `NOT_FOUND` | 404 | 없는 환자·병원·문의 |
| `INVALID_STATE` | 409 | 지금 상태에서 할 수 없는 동작. 상세를 새로 조회 |
| `POLICY_ACTIVE` | 409 | 실행 중 다른 문의 정책 선택. 먼저 일시정지 |
| `POLICY_VERSION_MISMATCH` | 409 | 저장된 문의 정책 버전·설정과 코드 불일치. 서버 담당자 확인 필요 |
| `CONFLICT` | 409 | 다른 담당자가 먼저 처리함. 새로고침 |
| `RESOURCE_CONFLICT` | 409 | 자원 부족. `shortfalls` 표시 |
| `RESTRICTED` | 409 | 병원 수용 제한 중 |
| `NOT_ACCEPTED` | 409 | 수용 응답이 없는 병원으로 확정·변경 시도 |
| `ACCEPTANCE_EXPIRED` | 409 | 수용 응답 만료. 다시 문의 |
| `STALE_ACCEPTANCE` | 409 | 환자 정보가 바뀌어 재확인 필요 |
| `ALREADY_SENT` | 409 | 문의를 보낸 뒤 환자 정보 수정 시도 |
| `TOO_SOON` | 409 | 재문의 간격 미달. `wait_seconds` 표시 |
| `NOT_REQUESTED` | 409 | 병원의 조정 요청 없이 목적지 변경 시도 |
| `DUPLICATE` | 409 | 이미 있는 환자 ID |
| `SERVER_ERROR` | 500 | 서버 오류 |

## 7. 이력 조회 (선택)
`GET /api/events?since=0&patient_id=DEMO-A` 또는 `&hospital_id=H04`로 조회합니다. 모든 상태 변경이 시각, 처리자, 내용과 함께 남아 있어 디버깅이나 시연 설명에 쓸 수 있습니다.

## 8. 설정값 (실험 가정, 임상 기준 아님)
| 값 | 기본 | 위치 |
|---|---|---|
| 수용 응답 유효시간 | 600초 | `service.ACCEPT_TTL` |
| 미응답 강조 | 60초 | `service.PENDING_WARN` |
| 재문의 최소 간격 | 120초 | `service.RETRY_MIN` |
| 오래된 정보 기준 | 600초 | `service.STALE_AFTER` |
| 이동시간 추정 | 직선거리 × 1.3, 40km/h | `travel.py` (카카오 키가 있으면 실제 길찾기 사용) |
| 가상 치료 소요시간 / 최대 대기 | 1800초 / 3600초 | `solver_adapter.py` |
| 계획 기간 / 시간 단위 | 120분 / 1분 | `solver_adapter.py` |
| 추천 최대 유효시간 | 30초 (수용 응답·정보 만료가 빠르면 더 짧음) | `planning.py` |
| 솔버 프로세스 제한시간 | 3초 | `--solver-seconds` |

## 9. 환자 화면 필드 매핑 (2026-10-08 추가)

등록·응급증상 화면의 정보를 하나의 `POST /api/patients` JSON으로 보내세요. 별도의 `emergency.db`나 URL 쿼리에 환자 정보를 저장하지 않고, 응답의 `id`로 공통 서버를 조회합니다. 모르는 값과 빈 날짜는 `null`로 변환하세요.

| 화면 필드 | API 필드 | 값 |
|---|---|---|
| 이름 / 생년월일 / 주소 | `name` / `birth` / `address` | 문자열 또는 null; 생년월일은 YYYY-MM-DD |
| 전화번호(phone) | `contact` | 문자열 또는 null |
| 보호자 / 보호자 전화번호 | `guardian` / `guardian_phone` | 문자열 또는 null |
| 의식 상태 | `consciousness` | `alert`, `impaired`, `unresponsive`, `unknown` 또는 null |
| 호흡 상태 | `breathing` | `normal`, `abnormal`, `absent`, `unknown` 또는 null |
| 증상 / 위험 신호 | `symptoms` / `danger_signs` | 문자열 / 문자열 목록 |
| 입력자 / 동의 | `reporter_role` / `consent` | `self`, `guardian`, `transporter` / true |
| 현재 위치 | `lat` / `lon` | 위도 -90~90, 경도 -180~180, 유한한 숫자 |

모르는 의식·호흡을 정상 또는 비정상으로 단정하지 마세요. `unresponsive`, `abnormal`, `absent` 또는 위험 신호가 입력되면 `call_119=true`입니다. 전문가 확인 전 우선순위는 실험상 보수적으로 4로 다루며 진단 결과가 아닙니다. 이 프로토타입은 전문가 신원을 인증하지 않습니다.

`treatment_duration_seconds`, `max_wait_seconds`는 선택적인 1~86400 정수 또는 null입니다. 일반 사용자 화면에서 임상 기준처럼 제시하지 말고 가상 시나리오 설정에만 사용하세요. 생략하면 각각 1800초, 3600초를 씁니다. 환자 상세와 병원 문의의 `patient`에 프로필·의식·호흡 필드를 전달합니다.

## 10. 배정 결과 API (2026-10-08 추가)

서버 실행 시 배정 워커가 함께 시작합니다. 환자·문의·병원 자원·제한·이송 상태가 바뀌면 최신 입력을 DB에 기록한 뒤 백그라운드에서 다시 계산합니다. 계산 중에도 API는 상태 조회와 변경을 처리합니다.

| 용도 | 메서드·경로 | 본문 |
|---|---|---|
| 환자별 추천 조회 | `GET /api/patients/{pid}/recommendation` | 없음; 2~3초 폴링 |
| 수동 재계산 요청 | `POST /api/patients/{pid}/replan` | `{}`; 폴링마다 호출하지 않음 |
| 병원별 예정 환자 재평가 | 기존 `GET /api/hospitals/{hid}/incoming` | 추가된 `planning` 배열 사용 |

모두 HTTP 200 JSON입니다. 작업 대기·만료는 HTTP 오류가 아니라 본문의 `status`로 구분합니다.

```json
{
  "status": "READY",
  "revision": 42,
  "created_at": 1790000020,
  "expires_at": 1790000050,
  "result": {
    "status": "RECOMMENDED",
    "hospital_id": "H02",
    "inquiry_id": 5,
    "destination_locked": false,
    "planned_start_at": 1790000160,
    "planned_end_at": 1790001960,
    "wait_seconds": 160,
    "confirmable_now": true,
    "accepted_alternatives": [],
    "warnings": ["PRIORITY_UNVERIFIED"],
    "travel": {"duration_seconds": 120, "provider": "synthetic"},
    "assumptions": {
      "parameter_source": "experiment",
      "treatment_duration_seconds": 1800,
      "max_wait_seconds": 3600,
      "occupied_release": "unknown_blocked_for_horizon"
    },
    "solver": {"status": "OPTIMAL", "fallback": false, "lex_optimal": true}
  }
}
```

| 바깥 `status` | 화면 처리 |
|---|---|
| `QUEUED` | 계산 대기 표시; `result=null` |
| `READY` | 아래 결과 상태·예상시각·사유 표시 |
| `STALE` | 이전 추천을 사용하지 않고 재계산 대기; `result=null` |
| `ERROR` | 계산 실패·재시도 안내; `result=null` |
| `NOT_APPLICABLE` | 도착·퇴실·취소 상태. 배정 폴링 종료; `patient_state` 참고 |

| `result.status` | 의미 |
|---|---|
| `RECOMMENDED` | 유효한 수용 응답을 받은 병원 중 가상 시간 제약을 만족하는 배정안 |
| `UNASSIGNED` | 유효한 수용 병원이 없거나 대기·자원 제약으로 미배정 |
| `DESTINATION_HELD` | 기존 목적지를 유지하면서 예상 치료 시작시각 재평가 |
| `NEEDS_REVIEW` | 기존 자원 충돌·기한 초과·병원 조정 요청·지원하지 않는 자원 등 재확인 필요 |

수용 응답과 추천은 자원 예약이 아닙니다. `confirmable_now=true`는 해당 스냅샷에서 현재 수량 검사를 통과할 수 있다는 뜻이며 자리를 보장하지 않습니다. 사용자가 확정을 누르면 기존 `POST .../confirm`에 `inquiry_id`를 보내고, 서버가 현재 수용 응답과 자원 수량을 다시 검사합니다. 미래 시간대의 자리만 가능한 안은 `confirmable_now=false`일 수 있습니다.

이동 중 환자는 목적지를 자동 변경하거나 미배정으로 처리하지 않습니다. 병원이 조정 요청을 한 경우 `accepted_alternatives`에 새 수용 응답이 있는 대체 병원을 제공합니다. 대체 병원 목록은 최적 순위·예약이 아니며, 이동 중에는 현재 위치를 받아 기존 `POST .../change-destination`에 전달하세요. 서버가 확정 직전에 다시 검사합니다.

병원 `incoming.planning`의 각 항목은 `{patient_id, status, revision, result, ...}` 형태입니다. 기존 `affected`의 수량 기준 판정과 함께 재평가 상태를 보여 주세요. 대표 경고는 `PRIORITY_UNVERIFIED`, `WAIT_LIMIT_EXCEEDED`, `HOSPITAL_REQUESTED_ADJUSTMENT`, `DESTINATION_RESTRICTED`, `UNSUPPORTED_RESOURCE`, `NO_VALID_ACCEPTED_HOSPITAL`입니다.

계획 결과에는 `result.treatment_requirements`(실제로 적용한 조건), `result.treatment_evaluation`(선택 병원의 평가, 선택 병원이 없으면 null), `result.treatment_evaluations`(병원 ID별 평가)가 포함됩니다. 공개 근거만으로 확인되지 않은 조건은 `TREATMENT_CONFIRMATION_REQUIRED:<병원ID>`, 오래된 관측은 `PUBLIC_DATA_STALE:<병원ID>` 경고로 표시합니다. 이 경고가 있다는 이유만으로 병원의 유효한 환자별 수용 응답을 무효화하지는 않습니다.

### 1차 구현의 범위

- 기본 목표는 중증군→중간군→낮은군의 미배정 수, 이어서 각 군의 대기시간을 순서대로 최소화합니다. 미확인 중증도는 보수적으로 높은 군에 포함합니다.
- `ed_bed`, `special_team`의 양·시간 제약을 지원합니다. 다른 자원은 별도 모델이 필요하므로 재확인 결과를 제공합니다.
- 실제 점유 환자의 종료시각은 없으므로 계획 기간 전체를 차단합니다. 도착을 치료 시작으로 단정하지 않습니다.
- 기존 목적지가 실행 불가능하면 해당 환자의 목적지·예정 수요를 DB에 유지하고, 전체 계획을 재확인 대상으로 반환합니다.
- 솔버 제한시간 내 유효한 해가 없으면 순차 배정안으로 대체합니다. 순차안도 기존 목적지를 보존할 수 없으면 재확인 상태를 반환합니다. 제한시간은 솔버 프로세스에 적용하며 입력 수집·순차안 계산은 별도입니다.
- 계산 전후 DB 버전과 수용 응답·자원 정보 만료를 검사합니다. 공공 관측의 추가·수정·삭제도 `revision`을 변경하며, 공개 가용 관측의 신선도 만료도 추천 만료시각에 반영합니다. 오래된 결과는 저장하지 않으며 재계산합니다. 시연 크기 제한은 모델 환자 64명, 병원 32곳입니다.
- `--no-planner`로 워커를 끈 상태에서는 재계산 요청이 처리되지 않습니다. 독립 워커 서비스·실제 의료기관 검증·B2 비교는 후속 과제입니다.

## 11. 공공 병원 데이터 (2026-10-11 추가)

실험 권역 일괄 수집은 `python -m server.sync_region --db data/public.db`로 실행합니다. 2026-10-11 안성·평택·오산·화성·용인·수원·천안·아산에서 NMC 목록·상세 34곳, 가용병상·중증질환 관측 각 28곳을 확보했습니다. 추가 수집으로 심평원 34곳의 기본정보·상세 11종, 총 408개 관측과 NMC 수용 관련 메시지 34곳·63개 메시지를 저장했습니다. 인증키 설정과 실행법은 [서버 설명](../server/README.md), 원본 필드 검증 범위는 [매핑 문서](../api_field_mapping.md), 추가 수집 결과는 [치료조건 판정 연결 결과](research/API추가수집_치료조건판정연결_20261011.md)를 참고하세요. 주기 수집은 현재 가동하지 않았습니다.

기존 `GET /api/hospitals`, `GET /api/hospitals/{hid}` 응답에 다음 정보가 추가됩니다.

| 필드 | 의미 |
|---|---|
| `source` | `public` 공공 자료 / `synthetic` 시연 자료 / `hospital_input` 병원 입력 |
| `source_ids.nmc` | 국립중앙의료원의 원본 `hpid` |
| `source_ids.hira` | 엄격한 자동 매칭 또는 근거·식별정보를 고정한 개별 검토 연결로 확인한 심평원 암호화 요양기호 |
| `capabilities_status` | `known` 확인된 진료 목록 / `unknown` 진료 역량 미확인 |
| `public_data[]` | 엔드포인트별 공공 관측. 운영 `resources`와 구분 |
| `public_data[].endpoint` | NMC 기존 4종·`nmc_acceptance_messages`, 아래 심평원 12종. 병원·항목별 조회 성공 여부는 별도로 확인 |
| `public_data[].source_observed_at` | 원본 입력시각. 파싱 불가·누락 시 null |
| `public_data[].fetched_at` | 서버 수집 시각. 원본 갱신시각을 대체하지 않음 |
| `public_data[].stale` | 원본 시각 기준 10분 초과 여부. 원본 시각 미확인 시 null |
| `public_data[].data.resources[]` | 응급실·수술실·일반 ICU·신생아 ICU·분만실의 `resource_type`, `source_field`, `reported_available`, `status` |
| `public_data[].data.equipment[]` | CT·MRI·혈관촬영기·인공호흡기·조산아 인공호흡기·인큐베이터의 Y/N 관측. 미확인은 null |
| `public_data[].data.service_availability[]` | NMC `hv42` 분만실 Y/N을 bool/null로 보존. 수량·고위험 분만 수용 확답과 구분 |
| `public_data[].data.departments` | 상세 API의 등록 진료과. 현재 전문팀 가용 정보와 구분 |
| `public_data[].data.facilities[]` | 상세 API의 등록 시설 수 `reported_count`. 운영 총량·가용 수와 구분 |
| `public_data[].data.data_kind` | 심평원은 `registered`: 신고 현황. 현재 가용 관측과 구분 |
| `public_data[].data.specialists[]` | 심평원 전문과목 코드·이름·신고 전문의 수 `reported_count`. 현재 당직 인원이 아님 |
| `public_data[].data.registered_equipment[]` | 심평원 장비 코드·이름·신고 보유대수. 현재 사용 가능 여부가 아님 |
| `public_data[].data.special_care[]` | 심평원 특수진료 구분 코드·이름. 환자별 수용 확답으로 자동 해석하지 않음 |
| `public_data[].data.operating_details[]` | 심평원 응급실 주간·야간 운영 표시, 연락처·진료시간·접수시간 등 등록 정보. 현재 가용 여부가 아님 |
| `public_data[].data.designated_specialties[]` | 심평원 전문병원 지정 분야 코드·이름. 현재 환자의 치료 가능 여부로 확대 해석하지 않음 |
| `public_data[].data.nursing_grades[]` | 간호등급. 현재 간호 인원수와 구분 |
| `public_data[].data.other_personnel[]` | 기타 인력의 신고 수. 현재 근무 인원과 구분 |
| `public_data[].data.messages[]` | NMC 수용 관련 메시지 원문, 질환 코드·이름, 표출 상태, 적용 시작·종료시각. 자유문장으로 자동 수용·불가를 판정하지 않음 |
| `treatment_evaluations` | 병원 조회의 실험용 치료조건 6종 평가. `{프로필ID: 평가결과}` |

중증질환 관측은 코드·메시지 원문을 보존하며 버전별 의미를 자동 해석하지 않습니다. 상세·중증질환의 원천 입력시각은 확인되지 않아 null로 둡니다. 각 엔드포인트의 `data` 구조는 서로 다르며 모든 배열이 모든 관측에 있는 것은 아닙니다.

분만실 `hv42=Y/N`은 `service_availability`에만 bool로 저장하며, 분만실 가용 수량 `resources[].reported_available`은 null로 유지합니다. 숫자 값은 기존 수량 경로를 사용합니다. 신선도·미확인 처리는 동일하며 Y를 고위험 분만·NICU 가능으로 확대 해석하지 않습니다.

심평원 연계는 `python -m server.sync_hira --db data/public.db`, NMC 수용 관련 메시지 수집은 `python -m server.sync_messages --db data/public.db`입니다. 심평원 34곳 중 31곳은 이름·주소 정규화 일치와 좌표 300m 이내의 자동 기준으로 연결했습니다. 나머지 3곳은 공식 출처로 동일 기관을 확인하고 [hospital_identity_links.json](../config/hospital_identity_links.json)에 기관쌍·양쪽 식별정보 해시를 고정한 개별 예외입니다. 자동 매칭 기준을 전체 병원에 느슨하게 적용한 것이 아닙니다.

| 심평원 endpoint | 수집 내용 |
|---|---|
| `hira_hospital_basis` | 기본정보·등록 의사 총수 |
| `hira_departments` | 등록 진료과·의사 수 |
| `hira_specialists` | 전문과목별 등록 전문의 수 |
| `hira_equipment` | 등록 장비·대수 |
| `hira_facilities` | 등록 시설·수량 |
| `hira_special_care` | 특수진료 구분 |
| `hira_operating_details` | 응급실 운영 표시·연락처·진료시간 등 |
| `hira_designated_specialties` | 전문병원 지정 분야 |
| `hira_nursing_grades` | 간호등급 |
| `hira_other_personnel` | 기타 신고 인력 |
| `hira_transport` | 교통 안내 |
| `hira_meal_additions` | 식대 가산 정보 |

`hira_departments.data.departments[]`는 코드·이름·의사 수 객체이며, NMC 상세의 이름 문자열 배열과 형태가 다릅니다. 전문의 수는 별도 `hira_specialists`를 사용합니다. 교통 안내·식대 가산 정보도 원문과 정규화 값을 보존하지만 치료조건으로 사용하지 않습니다. NMC 메시지는 9곳에서 63개를 받았고 25곳은 빈 조회 결과였습니다. 빈 결과를 수용 가능이나 제한 없음으로 간주하지 않습니다. 메시지 적용 시작·종료시각을 정보 갱신시각으로 사용하지 않습니다.

후보 API의 각 후보에도 `public_data`, 환자별 `treatment_evaluation`, 적용 조건인 `treatment_requirements`가 포함됩니다. 공개 근거가 없거나 오래됐으면 후보에서 자동 제외하지 않고 `checks`에 확인 필요를 표시합니다. 기존의 명시적 진료 역량·병원 제한 필터는 유지합니다. 공공 가용수를 운영 총량으로 바꾸거나 점유를 다시 차감하지 않습니다. `resources_enough`는 기존 병원 운영 자원을 기준으로 하므로 공공 관측만 있는 병원은 확인이 필요할 수 있습니다.

### 치료조건 지정과 평가 응답

치료조건 평가기(`server/treatment_data.py`)와 조건 6종을 병원 조회·환자 후보·배정 결과에 연결했습니다. 증상으로 프로필을 자동 추론하지 않습니다. 구급대·의료진 검토나 가상 시나리오에서 다음과 같이 프로필을 명시합니다.

```json
{
  "reporter_role": "transporter",
  "consent": true,
  "lat": 36.99,
  "lon": 127.11,
  "extra": {"treatment_profile_id": "TR_HIGH_RISK_BIRTH_NEONATAL"},
  "required_capabilities": ["산부인과"],
  "resource_demands": {"ed_bed": 2}
}
```

위 본문은 `POST /api/patients`에 보냅니다. 카탈로그의 `required_capabilities`와 명시한 진료 분야는 합집합으로, 기본·확장·명시 자원 수요는 자원별 최댓값으로 병합해 저장합니다. 이 예에서는 `ed_bed:2`, `special_team:1`, `delivery_room:1`, `nicu_bed:1`이 적용됩니다. 프로필을 지정하지 않으면 명시한 진료 분야·자원 수요를 사용하며 증상 문구에서 새 조건을 만들지 않습니다. 없는 프로필 ID는 `400 INVALID`입니다. 수정은 기존과 같이 문의를 보내기 전 `PATCH /api/patients/{pid}`에서 가능합니다.

환자 상세의 `required_capabilities`, `resource_demands`는 병합된 적용값입니다. `explicit_required_capabilities`, `explicit_resource_demands`는 사용자가 별도로 명시한 원래 조건을 구분하는 조회용 필드입니다. 프로필을 변경하거나 `extra`에서 프로필을 제거하면 이전 프로필에서만 나온 조건은 제거하고 별도로 명시한 조건은 유지합니다. `extra`는 객체 전체를 교체하므로 다른 추가정보를 보존하려면 함께 보내세요.

사용 가능한 프로필은 `TR_GENERAL_ED`, `TR_ORTHOPEDIC`, `TR_CARDIAC_INTERVENTION`, `TR_NEUROSURGICAL`, `TR_PEDIATRIC`, `TR_HIGH_RISK_BIRTH_NEONATAL`이며, 조건은 [카탈로그](../config/treatment_requirements.json)에서 확인합니다. 모두 실험 시나리오이며 임상 진단이나 공식 이송 기준이 아닙니다.

| 평가 필드 | 의미 |
|---|---|
| `status` | `MATCH` 공개 근거 일치 / `NEEDS_CONFIRMATION` 확인 필요 / `EXCLUDED` 명시한 실험 정책에서 현재 문의 차수의 잠정 제외 |
| `evidence[]` | 요구 조건별 결과·사유. 진료과·장비·자원에는 관련 등록 근거도 연결 |
| `registered_evidence[]` | 요청 조건에 관련된 등록 진료과·전문의·장비·시설 등의 근거를 중복 제거해 제공. 출처 endpoint, 원본 필드, 코드·이름·수량, 수집·원천시각을 표시. 전체 자료는 `public_data`에 보존 |
| `registered_evidence_scope` | API는 `requested_conditions`: 요청한 조건의 관련 근거. 연구용 스냅샷 평가기는 전체 등록 근거 보존 |
| `available_registered_evidence_count` | 관련 근거로 줄이기 전 병원 전체의 등록 근거 개수 |
| `current_evidence[]` | 현재 가용성에 대한 조건별 비교. 등록 전문의·시설 수로 대체하지 않음 |
| `operational_context[]` | 응급실 운영 등록 정보·NMC 수용 관련 메시지. 문맥 확인용이며 환자별 수용 확답이 아님 |
| `unknown[]`, `excluded[]`, `stale` | 확인이 필요한 조건, 잠정 제외 조건, 요구 조건 관련 오래된 관측 여부 |
| `patient_acceptance_required` | 공개 비교와 별개로 환자별 수용 확인이 필요함. 항상 true |
| `profile_id`, `profile_label`, `requirement_source` | 후보·배정의 환자별 평가에 적용된 명시 프로필과 조건 출처 |

진료과는 카탈로그의 정확한 이름으로 비교하고 심평원 등록 진료과를 보완 근거로 사용합니다. 내과를 심장내과로, 일반 인공호흡기를 신생아용으로 추정하지 않습니다. 등록 진료과가 일치해도 현재 당직·팀 가용성은 미확인입니다. 등록 수량은 현재 가용수로 사용하지 않으며, 누락·오래된 값·공개 가용수 0은 기본적으로 확인 필요로 남깁니다. 원천 시각이 없으면 새로 수집해도 가용 정보의 신선도가 확인된 것으로 바뀌지 않습니다.

수술실·신생아 중환자실·분만실 공개 수량은 전문팀 가용이나 고위험 분만 수용 확답과 같지 않습니다. 현재 솔버는 운영 `ed_bed`, `special_team`만 모델링하며, 공개 관측을 임의로 이 자원에 투영하지 않습니다. 진료 역량 미확인 병원이 유효한 환자별 수용 응답과 확인된 운영 자원을 제공한 경우 추천에 포함될 수 있고 `CAPABILITIES_UNVERIFIED:<병원ID>` 경고가 붙습니다. 수동 확정은 기존 정책을 유지합니다. 운영 자원 미확인만으로 병원의 수용 응답·확정을 차단하지 않으며, 솔버 추천 가능 여부와는 구분합니다.

프로필에 수술실·ICU·분만실 등 솔버가 지원하지 않는 자원 수요가 있으면 이를 생략해서 배정하지 않고 `UNSUPPORTED_RESOURCE` 경고와 `NEEDS_REVIEW`를 반환합니다. 카카오 키가 없는 현재 수집 환경에서는 실제 도로 이동시간을 추가 수집하지 않았으며, 기존 직선거리 보정 추정을 유지합니다.
