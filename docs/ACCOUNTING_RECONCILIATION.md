# 수동 정산 확인 후 기록 종결 API

기준: 2026-10-14. 공통 서버 기능이며 환자용·병원용 앱 코드는 변경하지 않았다.

운영 자원 합계를 이미 수동 정산했거나 과거 원장이 없어 자동 퇴실할 수 없는 기록에 사용한다. **이 API는 자원을 정산하지 않는다. 담당자가 정산을 확인한 기록만 추가 차감 없이 종결한다.** 정상 원장이 있는 환자는 기존 퇴실 API를 사용한다.

## 처리 흐름

1. 해당 환자 또는 외부 유입 기록의 최신 `version`을 조회한다.
2. 병원의 운영 자원 전체를 조회하고 수량을 확인한다. 필요하면 기존 자원 수정 API로 합계를 먼저 정산한다.
3. 확인한 전체 자원 버전과 대상 버전, 사유·담당자·명시적 확인을 전송한다.
4. 서버는 같은 트랜잭션에서 버전을 다시 비교하고 종결·감사 기록을 저장한다. 자원 수량은 변경하지 않는다.

서버는 실제 현장의 수동 정산 여부를 추론할 수 없다. `confirm_no_resource_release: true`는 담당자가 집계를 확인했다는 명시적 확인이다. 자동 퇴실에서 409가 발생했다고 이 API를 자동 호출해서는 안 된다.

## 경로와 상태

| 동작 | 경로 | 허용 상태와 결과 |
|---|---|---|
| 일반 환자 종결 | `POST /api/hospitals/{hid}/patients/{pid}/reconcile-accounting` | `ARRIVED → COMPLETED` |
| 외부 유입 종결 | `POST /api/hospitals/{hid}/external-arrivals/{eid}/reconcile-accounting` | `ACTIVE` 또는 `LEGACY → RECONCILED` |
| 처리 결과 상세 | `GET /api/hospitals/{hid}/accounting-reconciliations/{id}` | 해당 병원의 감사 기록 |
| 처리 결과 목록 | `GET /api/hospitals/{hid}/accounting-reconciliations` | 최신 ID부터 페이지 조회 |

병원 ID 범위 검사는 기존 시연 계약이며 사용자 인증을 대신하지 않는다. 다른 병원의 대상·감사 기록은 404다. 이미 정상 퇴실했거나 다른 요청으로 종결한 대상은 409다. `RECONCILED` 외부 유입에 정상 퇴실을 다시 적용할 수 없다.

## 필수 요청 본문

```json
{
  "request_id": "c1674014-40f1-4bfc-82a3-0aeb617fa0bb",
  "reason": "운영 점유 합계를 확인했고 해당 기록의 사용량은 이미 정산됨",
  "handled_by": "시연 담당자",
  "expected_version": 1,
  "expected_resource_versions": {"ed_bed": 3, "special_team": 2},
  "confirm_no_resource_release": true
}
```

위 ID·버전은 구조 예시이며 실제 조회값으로 바꿔야 한다.

| 항목 | 검증 |
|---|---|
| `request_id` | 앞뒤 공백 없는 1~128자 문자열. 새 처리에 한 번 생성하고 재시도에도 유지 |
| `reason` | 앞뒤 공백 제거 후 1~2,000자 문자열 |
| `handled_by` | 앞뒤 공백 제거 후 1~80자 문자열 |
| `expected_version` | 최신 대상 버전. 양의 64비트 정수, 문자열·불리언 불가 |
| `expected_resource_versions` | **해당 병원 운영 자원 전체**의 이름과 버전. 값은 양의 64비트 정수. 운영 자원이 없으면 `{}` |
| `confirm_no_resource_release` | 반드시 JSON 불리언 `true` |

부분 자원 목록은 허용하지 않는다. 조회 후 자원이 추가·삭제되거나 어느 자원의 버전이라도 바뀌면 409다. 공개 API의 가용병상 관측값을 이 버전 목록에 넣지 않는다.

## 성공과 재시도

응답에는 `duplicate`, `reconciliation`, 대상 요약(`patient` 또는 `external_arrival`), `resources_changed: false`, `released_resource_demands: {}`가 포함된다. 대상 요약은 ID·병원·결과 상태·결과 버전·`accounting_reconciliation_id`이며 일반 환자는 `destination_hospital_id`도 포함한다. 환자 상세 화면 데이터가 아니므로 성공 후 상세·목록을 다시 조회한다.

감사 결과 `reconciliation`은 다음 필드를 제공한다.

| 필드 | 의미 |
|---|---|
| `id`, `hospital_id`, `target_kind`, `target_id`, `request_id` | 처리·대상 식별. `target_id`는 문자열 |
| `created_at`, `handled_by`, `reason` | 처리시각·담당자·사유 |
| `expected_version`, `prior_state`, `prior_version` | 확인한 버전과 처리 전 상태 |
| `result_state`, `result_version` | 최초 처리 후 상태와 버전 |
| `resource_snapshot` | 처리 당시 병원의 운영 자원 전체. 내부 `occupancy_epoch`는 제외 |
| `resources_changed`, `released_resource_demands` | 항상 `false`, `{}` |

같은 병원·요청 ID·정규화된 내용으로 재전송하면 `duplicate: true`와 최초 처리 결과를 반환한다. 이후 자원 버전이 바뀌어도 최초 요청의 재전송은 성공하며 이벤트·버전·자원·대화를 다시 변경하지 않는다. 처리자·사유·대상·버전 목록 중 하나라도 다르면 `409 CONFLICT`다. 일반 환자와 외부 유입도 같은 병원의 요청 ID 공간을 공유한다.

## 저장·종결 범위

- 대상의 상태와 버전을 한 번 변경하고 `accounting_reconciliation_id`, `accounting_reconciled_at`, `accounting_reconciled_by`를 기록한다.
- 자원 수량·버전·집계 기준·갱신 시각과 공공 관측은 그대로 둔다. 정상 퇴실의 자원 해제 함수를 호출하지 않는다.
- 일반 환자에 남아 있는 문의·초기 탐색·대체 탐색·조건 감시와 대화를 종료한다. 다른 환자의 수용 응답 만료나 감시 상태는 바꾸지 않는다. 과거 병원 채팅 참여 구간을 다시 열지 않는다.
- 원래 원장 값은 NULL·손상 JSON·SQLite BLOB이어도 덮어쓰지 않는다. 감사 테이블에는 처리 전 원문과 자원 스냅샷을 함께 보존한다.
- `accounting_reconciliations`에는 일반 SQL의 수정·삭제·교체를 막는 트리거를 둔다. DB 관리자가 스키마 자체를 변경하는 행위까지 방지하는 장치는 아니다.
- 공개 감사 응답에는 내부 집계 기준, 원장 원문, 요청 비교값, 임상 환자 정보를 제공하지 않는다.
- 조회 API는 원장·자원·업무 이벤트·배정 입력 버전을 변경하지 않는다.

목록의 `kind`는 `all`(기본), `patient`, `external_arrival`이다. `limit`은 기본100·최대200이고 `next_before_id`를 다음 요청의 `before_id`로 보낸다. 외부 유입 목록도 `state=RECONCILED`를 지원한다. 손상된 외부 원장 조회는 해당 필드를 null로 반환하고 `data_errors`에 필드 이름을 표시한다.

## 오류와 화면 담당자 연결

| 응답 | 의미 | 후속 처리 |
|---|---|---|
| `400 INVALID` | 필수 확인 누락·형식·정수 범위 오류 | 입력 수정 |
| `404 NOT_FOUND` | 대상·감사 기록이 없거나 다른 병원 소속 | 선택과 경로 확인 |
| `409 CONFLICT` | 대상·전체 자원 버전 변경, 같은 요청 ID의 다른 내용 | 최신 상태를 다시 확인. 기존 요청 성공 여부는 감사 결과로 확인 |
| `409 INVALID_STATE` | 이미 종료됐거나 허용되지 않은 상태 | 자동 재시도 없이 최신 상태 표시 |

병원 화면에는 정상 퇴실과 구분되는 명시적 확인·사유 입력 흐름이 필요하다. 서버의 버전 검사를 화면의 자체 차감이나 강제 덮어쓰기로 우회하지 않는다. **해당 화면과 버튼은 팀원 후속 작업이며 이번에는 구현하지 않았다.**

## 검증

`test_accounting_reconciliation.py`의 41개 테스트는 무차감 종결, 손상·과거 원장 보존, 중복·동시 요청, 대상·전체 자원 버전 충돌, 종결 후처리 실패의 전체 취소, 감사 수정·삭제·교체 방지, 병원별 조회·페이지·내부 원장 비노출·조회 무변경을 검증한다.

별도 합성 DB의 HTTP E2E는 55개 검사·39개 요청을 통과했다. 자원 전체 행과 공공 관측 보존, 두 종류의 종결·정상 퇴실, 재시도, 범위 제한, 남은 탐색·감시 종료, 무관한 만료 수용 요청의 무변경을 확인했다. 증거는 `.codex-build/accounting-reconciliation-20261014/http-e2e-result.json`에 보관한다. 실제 시연 DB에 오류 사례를 주입하거나 실제 병원에 수용을 요청하지 않았다.

기존 서버 기능을 포함한 회귀 테스트702개가 통과했다. 로컬 공통 서버18027에도 DB 백업 후 보존 마이그레이션으로 반영했고, 기존 데이터의 건수·내용 해시와 두 정적 화면 및 새 조회 경로의 HTTP200을 확인했다. 종합 증거는 같은 폴더의 `qa-summary.json`, `public-redeploy-check.json`이다. 이 검증은 기능 정확성을 뜻하며 배정 알고리즘의 현장 개선율을 뜻하지 않는다.
