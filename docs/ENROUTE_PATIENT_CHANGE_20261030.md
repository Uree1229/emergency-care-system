# 이송 중 환자정보 변경과 현재 병원 검토

2026-10-30. 목적지 확정 후의 관찰·치료조건 변경을 위한 별도 서버 절차와 화면을 추가했다. 기존 `WAITING` 환자 수정·음성 `submit`의 적용 범위는 유지한다. 환자·병원 역할을 모사한 로컬 시제품 기능이다.

## 처리 순서

1. `CONFIRMED` 또는 `IN_TRANSIT` 환자에 새 음성 초안을 연결한다. 인식문을 추가하거나 관찰을 정정하고 현재 초안을 명시적으로 확인한다.
2. 변경 전후 관찰·치료조건을 조회한다. 조건 감소는 별도로 확인한 뒤 현재 목적 병원에 변경안을 보낸다.
3. 변경안은 `PENDING`으로 저장한다. **환자정보·버전·기존 목적지·배정 입력은 아직 바뀌지 않는다.** 새 변경안은 이전 대기안을 이력에 남겨 대체한다.
4. 현재 병원 역할이 새 관찰과 요구를 검토해 반려, 수용 유지, 조정 필요 중 하나를 명시한다. 반려는 환자정보를 바꾸지 않는다.
5. 승인하면 관찰과 조건 반영, 환자 버전 증가, 옛 문의·수용 종료, 이전 대체 문의 정리, 병원 판단·감사 사건·요청 재시도 기록을 한 트랜잭션에서 처리한다. 음성 초안은 제출 완료가 된다.
6. 수용 유지는 현재 병원의 역량·확인된 자원·최신성·수용 제한을 다시 검사하고 새 확정 조건을 저장한다. 조정 필요는 새 조건으로 대체 문의 작업을 시작하지만 기존 목적지는 유지한다.
7. 새 병원의 유효 수용과 담당자의 기존 `change-destination` 동작이 있어야 목적지가 바뀐다. 승인 자체는 새 병원 수용이나 자동 전원이 아니다.

이 절차는 의료 판단을 자동 생성하지 않는다. 치료조건 제안은 기존 R1 시제품 규칙이며, 혈압·맥박 수치로 새로운 치료 지시를 추론하지 않는다. 입력·검토자는 로컬 역할 ID로 구분하며 실제 로그인·의료진 자격·병원별 접근 통제를 구현한 것은 아니다.

## API

| 동작 | 경로 |
|---|---|
| 변경안 조회 | `GET /api/voice/sessions/{sid}/transport-change-review?actor_id=...` |
| 조건 선택 후 미리보기 | `POST /api/voice/sessions/{sid}/transport-change-review/preview` |
| 현재 병원에 변경안 전송 | `POST /api/voice/sessions/{sid}/transport-change-review/request` |
| 이송 건별 이력 | `GET /api/patients/{pid}/transport-change-reviews` |
| 병원별 검토 목록 | `GET /api/hospitals/{hid}/transport-change-reviews` |
| 현재 병원의 판단 | `POST /api/hospitals/{hid}/transport-change-reviews/{rid}/decision` |

미리보기의 `selection`은 기존 치료조건 검토와 같은 `keep/suggested/custom`이다. 전송 본문은 다음과 같다.

```json
{
  "actor_id": "crew-demo",
  "client_request_id": "new-unique-request-id",
  "expected_version": 2,
  "expected_patient_version": 3,
  "review_token": "미리보기에서 반환한 토큰",
  "selection": {"mode":"keep"},
  "confirmed": true,
  "allow_reduction": false,
  "reason": "새 관찰과 변경 사유"
}
```

병원 판단 본문은 `actor_id`, `client_request_id`, `expected_review_version`, `expected_patient_version`, `decision_token`, `decision`, `confirmed:true`, `reason`이다. `decision`은 `keep/adjust/reject`다. 병원 목록에서 반환한 버전과 결정 토큰을 그대로 사용한다.

결정 토큰은 환자·초안 버전, 목적지·이송 상태, 현재 병원의 자원·역량·제한, 감시 버전·정책·상태·위험 서명에 묶인다. 감시를 껐다 켜거나 문의 정책이 바뀐 경우에도 이전 승인을 재사용할 수 없다. 토큰은 충돌 검사 수단이며 사용자 인증 수단이 아니다.

실행 중 감시가 꺼졌으면 자동 반영을 거절한다. 새 화면에서 명시적으로 감시를 켠 뒤 최신 검토를 다시 조회한다. 도착·목적지 변경·새 초안 편집·환자 수정·카탈로그 변경도 이전 승인을 차단한다. 409이면 재조회 후 사람이 다시 판단한다.

현재 모델은 `ed_bed/special_team` 두 종류다. 미지원 자원 요구는 변경안에 그대로 보존하고 `UNSUPPORTED_SOLVER_RESOURCE`로 별도 검토를 표시하며 자동 반영하지 않는다. 수용 유지나 조정 필요를 눌러 지원 외 자원 최적화를 완료한 것처럼 처리하지 않는다. 반려하거나 지원 범위를 정한 새 변경안으로 다시 검토한다.

새 `enroute_patient_reviews` 테이블은 첫 변경안 전송의 트랜잭션에서 보존 생성된다. 단순 GET은 스키마·환자·검토 이력을 생성하지 않는다. 요청 재시도는 같은 ID·본문으로 결과를 확인하며, 다른 병원 경로를 통한 캐시 결과 조회는 거절한다. 원 제안은 불변이고 결정·대체 상태와 감사 이력은 남는다.

## 연결 화면

아래 화면·브라우저 검증 기록은 원본 작업 폴더의 별도 UI 작업에 대한 것이다. `codex/server-integration-20261030`에는 이 API만 통합했으며 `patient-ui/transport-change.{html,mjs}`와 병원 운영 탭은 포함하지 않았다. 이 서버 브랜치에서 아래 화면 주소를 바로 사용하는 대신 API 계약으로 연결하고, 화면 배포는 UI 담당 작업과 별도로 진행한다.

`/patient/transport-change.html?pid=이송ID&hid=현재병원ID`에서 입력·미리보기·전송·현재 병원 판단을 단계별로 수행한다. 일반 submit이나 단순 통화 내용 확인을 이 승인으로 대신하지 않는다. 병원 통화·계측 탭에도 이 화면 링크가 있다.

지금 화면은 인식문 또는 직접 입력을 받는다. 새 마이크 녹음·실제 STT 검증을 추가한 화면은 아니다. 네트워크 응답 유실 요청은 세션 저장소에 본문과 ID를 보존하고 원래 이송·병원·작업자 범위에서 동일 요청으로 재시도한다. 4xx 거부 후에는 최신 정보를 확인하고 명시적으로 다시 수행한다.

## 검증

신규 15개 시험이 요청 시 환자·배정 입력 불변, 중복 방지, 승인·반려, 새 수용 전 목적지 변경 차단, 초안·환자·도착·자원·감시 정책 충돌, 병원 캐시 범위, 미지원 자원 보존, 중간 실패 전체 롤백, SQLite 백업·복원 후 대기 검토 보존을 확인했다.

전용 합성 DB의 실제 브라우저에서 맥박 105 관찰의 미리보기→전송→현재 병원 확인을 수행했다. 대기 중 버전 3과 H1이 유지됐고, 승인 후 버전 5·맥박 105·`IN_TRANSIT`·H1을 확인했다. 버전 증가는 관찰 반영과 기존 병원 판단 서비스가 함께 기록한 결과다. 실제 환자·의료진·외부 병원과의 검증은 아니다.

소스는 `server/enroute_patient_review.py`, 등록은 `server/app.py`, 화면은 `patient-ui/transport-change.{html,mjs}`, 시험은 `test_enroute_patient_review.py`다. 동결된 기존 21개 서버·솔버 파일은 변경하지 않았다.
