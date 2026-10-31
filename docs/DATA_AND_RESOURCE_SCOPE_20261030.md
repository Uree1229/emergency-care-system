# 공개 기관정보·시연 운영값의 출처와 자원 모델 범위

2026-10-30. 새 읽기 전용 모듈 `server/data_provenance.py`, 지원범위 manifest `config/resource_model_scope_20261030.json`, 새 테스트를 추가했다. 기존 데이터베이스·공개 수집기·솔버·치료조건 카탈로그·동결 대상 파일은 변경하지 않았다. 외부 API 갱신 호출은 0회다.

## 지금 저장된 자료

| DB | 기관정보 | 저장 공개 관측·등록 행 | 운영 자원 행 | 의미 |
|---|---:|---:|---:|---|
| `data/public.db` | 공개 기관 34곳 | 566 | 0 | 2026-10-11 수집 저장본. 현재 가용 자원이나 환자별 수용 확답이 아님 |
| `data/seoul-public-demo.db` | 공개 기관 20곳 | 306 | 0 | 2026-10-13 수집 저장본. 서울 공개 기관정보와 당시 공개 관측·등록 근거 |
| `data/team-common.db` | 가상 기관 10곳 | 0 | 20 | `hospital_input`이어도 합성 기관에 대한 시연 중 수동 입력. 실제 의료진 실측이라고 표시하지 않음 |

공개 수집시각(KST)은 `public.db`의 NMC 기관·병상 자료가 **10월 11일 16:22:45~16:22:59**, HIRA 등록 현황이 **18:02:19**, 추가 안내문이 **18:02:44**다. 서울 자료는 NMC **10월 13일 19:25:38~19:25:45**, HIRA **19:30:27**이다. 날짜를 오늘로 새로 찍거나 새로 수집한 것처럼 표시하지 않았다.

원천 관측시각 `source_observed_at`과 수집시각 `fetched_at`은 다르다. 원천 시각이 없는 등록 현황은 **관측시각 미상**으로 남겨 두고 수집시각을 별도로 보여 준다. 등록 시설 수·공개 가용수·공개 분만 서비스 Y/N·운영 capacity·점유·환자별 수용은 서로 다른 값이다.

## 화면에서 쓸 분류

| classification | 표시 문구 | 해석 |
|---|---|---|
| `public_identity_frozen` | 공개 기관정보 · 저장본 | 기관명·기관 연결의 출처. 실제 병원과 현재 통신한다는 의미가 아님 |
| `synthetic_identity` | 가상 기관 | 시연용 기관 |
| `public_observation` | 공개 관측 · 운영 총량 아님 | 저장된 공개 가용/서비스/안내 관측. 원천 시각·수집시각·노후 상태를 함께 표시 |
| `registered_reference` | 등록 현황 · 현재 가용 아님 | 진료과·시설·장비 등 신고·등록 근거 |
| `synthetic_fixture` | 시연용 합성 운영값 | 시연자가 지정한 capacity/occupied. 공개 수를 변환한 값이 아님 |
| `manual_demo_entry` | 시연 중 수동 입력 · 실측 아님 | 가상 기관에 `hospital_input`으로 입력된 값 |
| `manual_unverified_operation` | 수동 운영 입력 · 실제 담당자 확인 미검증 | 공개 기관에 수동 입력됐으나 실제 병원 담당자·실측 여부를 검증하지 않은 값 |
| `unknown` | 출처 확인 필요 | 저장 출처를 신뢰 가능한 분류로 해석할 수 없음 |

모든 출처는 저장된 표기와 시각의 인벤토리다. 이 모듈이 실제 작성자·현재 임상 상태를 검증했다고 주장하지 않는다. 기존 운영 테이블에 `source=public`인 행이 발견되면 `public_source_in_operating_table_requires_review=true`, 가용수 출처 여부는 미확인으로 표시한다. 읽기 도구가 그 행을 정정하거나 기존 업무 경로를 차단하는 기능은 아니다. 현재 조사한 두 공개 DB에는 운영 자원 행이 없다.

## 읽기 API 계약

루트 서버 라우터에서 다음 함수를 호출할 수 있다. 이 파일은 HTTP 라우터를 수정하지 않는다.

```python
from server.data_provenance import inventory, resource_scope

# conn의 읽기 트랜잭션·수명은 호출자가 관리한다.
data = inventory(conn, now=now, hospital_id=hid)  # hid 생략 시 전체 기관
scope = resource_scope()
```

`inventory`는 SELECT만 수행한다. DB 초기화·마이그레이션·PRAGMA·서비스 쓰기·네트워크 호출이 없고 환자 식별자·연락처·작업자·원문 API payload를 반환하지 않는다. 별도 CLI는 기존 DB를 `mode=ro`로 열고 없는 파일을 만들지 않는다.

최상위 `contract=data-provenance-20261030-v1`, `summary`, `hospitals[]`, `fixture_context`, `resource_model_scope`를 반환한다. 병원 항목은 다음과 같다.

- `identity.origin.classification/label`, `identity.last_collection_utc`: 기관 출처와 마지막 저장 수집시각.
- `public_evidence[]`: `endpoint`, `origin`, `as_of_utc`, `fetched_at_utc`, `time_status`, `stale`, `evidence[]`. `as_of_utc=null`은 미상이며, 미래 시각도 정상 최신으로 표시하지 않는다.
- `operating_resources[]`: `resource_type`, `capacity`, `occupied`, `origin`, `as_of_utc`, `updated_at_utc`, `update_freshness`, `scheduling_model_supported`. 원천 관측시각이 없다고 운영 테이블 수정시각으로 바꾸지 않는다.
- `acceptance_scope.inventory_is_patient_acceptance=false`: 문의 상태별 수는 과거 상태 집계다. 해당 환자 버전·만료·현재 응답 유효성·담당자의 확정을 대신하지 않는다.
- `mixed_public_identity_and_synthetic_operations=true`: 실제 공개 기관정보에 합성 운영값을 붙인 시연 DB임을 화면에 표시한다.

## 자원 모델의 현재 범위

실제 `server/solver_adapter.py::RESOURCE_TYPES`는 **`ed_bed`, `special_team` 두 종류**다. 새 JSON manifest는 이 값과 일치하는지 읽을 때 확인하며, 기존 치료조건 카탈로그 6개 프로필의 자원 요구 합집합과 미지원 항목을 함께 반환한다.

| 자원 | 운영 일정 배정 | 현재 처리 |
|---|---|---|
| `ed_bed` | 지원 | 별도 운영 테이블의 진료 자리 |
| `special_team` | 지원 | 실험용 전문팀. ICU/NICU/수술/분만을 대신하지 않음 |
| `icu_bed` | 미지원 | 요구 유지, `UNSUPPORTED_RESOURCE`·`NEEDS_REVIEW` |
| `nicu_bed` | 미지원 | 요구 유지, `UNSUPPORTED_RESOURCE`·`NEEDS_REVIEW` |
| `delivery_room` | 미지원 | 요구 유지, `UNSUPPORTED_RESOURCE`·`NEEDS_REVIEW`. 공개 Y/N을 수량 1/0으로 바꾸지 않음 |
| `operating_room` | 미지원 | 요구 유지, `UNSUPPORTED_RESOURCE`·`NEEDS_REVIEW` |

미지원 자원을 요구에서 조용히 빼고 두 자원만으로 배정하지 않는다. 기존 adapter의 `build→present` 경로를 ICU·NICU·분만·수술실 각각 검증해, 모델 배정 대상에서 제외하고 검토 결과·원래 요구량을 보존하며 `confirmable_now=false`임을 확인했다. 공개 시설 수 999·공개 가용수 888만 있는 시험에서도 운영 capacity는 `[0,0]`이었다.

## 새 전용 시연 DB

원본을 보존하면서 실제 공개 기관정보와 합성 운영값을 함께 살펴볼 새 파일을 만들었다.

`outputs/data_resource_scope_20261030/fixture/public-identity-synthetic-operations.db`

서울 원본의 공개 기관 20곳과 저장 공개 관측·등록 306행을 복사했다. 수집·관측 날짜는 그대로다. 모든 기관의 운영 자원은 별도로 **합성 진료 자리 4·전문팀 1·점유 0**으로 지정했다. 환자·문의는 0건이며 실제 수용 응답을 생성하지 않았다. 전체 DB 문맥은 `demo_provenance` 표와 `manifest.json`에 남겼다. 원본 DB 본문 해시와 읽기 트랜잭션에서 복사한 기관·참조 행의 projection 해시를 구분했다.

```powershell
python -m server.data_provenance inventory --db data/public.db --output NEW-inventory.json
python -m server.data_provenance fixture --source data/seoul-public-demo.db --output NEW-fixture-directory
python -m unittest test_data_provenance_20261030 -v
```

기존 출력 폴더·DB를 덮어쓰지 않는다. 시연 DB의 존재 자체는 서버 구동·실제 병원 통신·배정 성공·사용성 평가의 완료 근거가 아니다. 공통 서버의 기존 DB를 자동으로 이 파일로 바꾸지도 않는다.

검증 8개는 SELECT 전용 authorizer, 원본 파일 무변경, 네트워크 미호출, 합성/수동/미상 출처 구분, 시각 미상·미래 처리, 공개 수의 운영 capacity 미대입, 미지원 자원 검토, 새 DB의 날짜·출처·무환자·무문의·덮어쓰기 거절을 확인했다. 인벤토리·시연 DB·검증 증거는 `outputs/data_resource_scope_20261030/`에 있다.
