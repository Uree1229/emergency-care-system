# 공통 서버 API 명세 (병원 웹 · 환자 웹용)

이 문서는 병원 웹과 환자 모바일 웹을 만드는 팀원을 위한 명세입니다. 서버 코드는 `server/`에 있고, 업무 규칙의 원본은 `server/service.py`입니다.
모든 데이터는 가상이며 임상 현장과 연결되어 있지 않습니다.

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
| `CONSENT_REQUIRED` | 400 | 개인정보 동의 필요 |
| `LOCATION_REQUIRED` | 400 | 위치 입력 필요 |
| `NOT_FOUND` | 404 | 없는 환자·병원·문의 |
| `INVALID_STATE` | 409 | 지금 상태에서 할 수 없는 동작. 상세를 새로 조회 |
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
    "missing_capabilities": [],
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

### 1차 구현의 범위

- 기본 목표는 중증군→중간군→낮은군의 미배정 수, 이어서 각 군의 대기시간을 순서대로 최소화합니다. 미확인 중증도는 보수적으로 높은 군에 포함합니다.
- `ed_bed`, `special_team`의 양·시간 제약을 지원합니다. 다른 자원은 별도 모델이 필요하므로 재확인 결과를 제공합니다.
- 실제 점유 환자의 종료시각은 없으므로 계획 기간 전체를 차단합니다. 도착을 치료 시작으로 단정하지 않습니다.
- 기존 목적지가 실행 불가능하면 해당 환자의 목적지·예정 수요를 DB에 유지하고, 전체 계획을 재확인 대상으로 반환합니다.
- 확정·이동 중인 환자의 목적 병원에서 필요한 진료 분야가 사라지면 그 환자의 `result.status`를 `NEEDS_REVIEW`로 표시하고, `DESTINATION_CAPABILITY_MISSING` 경고와 `missing_capabilities` 목록을 반환합니다. 목적지 잠금과 예정 자원 수요는 유지하며, 다른 병원의 환자 추천은 계속 계산합니다. 정보가 복구되면 경고를 해제합니다.
- 솔버 제한시간 내 유효한 해가 없으면 순차 배정안으로 대체합니다. 순차안도 기존 목적지를 보존할 수 없으면 재확인 상태를 반환합니다. 제한시간은 솔버 프로세스에 적용하며 입력 수집·순차안 계산은 별도입니다.
- 계산 전후 DB 버전과 수용 응답·자원 정보 만료를 검사합니다. 오래된 결과는 저장하지 않으며 재계산합니다. 시연 크기 제한은 모델 환자 64명, 병원 32곳입니다.
- `--no-planner`로 워커를 끈 상태에서는 재계산 요청이 처리되지 않습니다. 독립 워커 서비스·실제 의료기관 검증·B2 비교는 후속 과제입니다.
