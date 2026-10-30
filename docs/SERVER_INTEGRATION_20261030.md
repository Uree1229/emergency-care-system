# 서버 전용 통합 실행본

통합 브랜치: `codex/server-integration-20261030`.

검증된 공통 서버 `60a062c` 위에 최신 이송 중 환자정보 검토·출처 조회·복구 검증을 연결한 서버 릴리즈다. 원본 작업 폴더에서 브랜치를 바꾸거나 전체 파일을 덮어쓰지 않고 별도 작업본에서 통합했다.

## 1. 통합 범위

| 범위 | 내용 |
| --- | --- |
| 기존 기반 | 공통 DB·실제 문의/배정 워커·음성 검토·추천·확정·이송·채팅·조건 변경·중요 사건·통화/업무 기록 |
| 이송 중 환자정보 검토 | 새 관찰 초안→명시 확인→현재 병원에 검토 요청→keep/adjust 판단→환자정보 반영. 새 수용과 담당자 결정 전 기존 목적지 유지 |
| 자료 출처 API | 공개 기관/관측·합성 운영 자원·수동 입력과 저장 시각 구분. 공개 관측을 운영 capacity나 환자 수용 확답으로 변환하지 않음 |
| 조회 일관성 보완 | 출처 조회의 여러 SELECT를 한 읽기 스냅샷으로 묶음. 조회 도중 다른 작업이 갱신해도 한 응답에 시점이 섞이지 않음 |
| 복구 도구 | 기존 공통 DB의 백업·신규 경로 복원, 독립 복구 리허설과 보존 마이그레이션 검증 |
| 음성 저장 진단 | 공유 완료된 `a7240da`의 `server.voice_record_check`와 해당 시험만 포함 |

기존 배정 기준 21개 파일과 `fixed_3 + CP-SAT severity_first, 3초`는 유지한다. 병원·구급대 화면, 진행 중인 음성 파서·소음 억제 개선, 새 연구 정책·시뮬레이션·실험 자료는 이 통합에서 변경하지 않았다. 기준 브랜치에 이미 있던 UI는 상속하지만, 현재 작업 폴더의 UI를 덮어쓰거나 최신 UI 전체를 통합했다고 표시하지 않는다.

`test_planning.py`·`test_datasets.py`의 릴리즈 전용 회귀를 유지한다. 작업 폴더의 오래된 사본으로 교체하지 않는다.

## 2. 다른 작업본에서 준비·실행

저장소 루트에서 사용할 Python 환경을 선택한다. 기준 환경은 Python 3.12.14, OR-Tools 9.15.6755, SQLite 3.53.1이다. 아래 `python`이 실제로 이 프로젝트에서 사용할 Python인지 먼저 확인한다.

```powershell
python --version
python -m pip install --target .solver_deps -r requirements-server.txt

# 새 전용 합성 DB를 준비. 다른 업무 DB를 seed/초기화하지 않는다.
python -m server.team_runtime prepare --db data/server-integration.db
python -m server.team_runtime check --db data/server-integration.db
python -m server.team_runtime serve --db data/server-integration.db --port 18050
```

`.solver_deps`와 `data/`는 Git에 넣지 않는다. 솔버는 프로젝트 `.solver_deps`를 우선하므로 전역 패키지 버전만 확인하지 않는다. 새 작업본에는 의존성을 직접 설치한다.

Python·SQLite의 버전 차이 때문에 기준 검사에서 막히면 출력된 차이를 확인한다. 기능 연결 시험용으로만 차이를 명시적으로 허용할 때는 각 명령에 `--allow-environment-difference`를 추가한다. 이를 기준 성능 실험의 동일 환경으로 취급하지 않는다. 이 옵션은 OR-Tools 미설치나 코드 변경을 허용하지 않는다.

서버는 loopback 전용이며 full 모드에서 두 워커를 실행한다. `--mode voice-only`는 음성 단독 시험용으로 두 워커가 비활성이다. 기존 서버와 포트가 겹치면 실행을 거절한다.

실행 후 확인 주소:

- `http://127.0.0.1:18050/api/team-runtime`
- `http://127.0.0.1:18050/api/system/status`
- `http://127.0.0.1:18050/api/data-provenance`
- `http://127.0.0.1:18050/api/resource-model-scope`

문의·수용·확정 입력은 로컬 합성 병원에만 작용한다. 실제 병원에 메시지를 발송하지 않는다. 휴대폰·외부 공개 연결, 로그인·병원별 권한·HTTPS 배포는 이번 loopback 통합 검증과 별도다.

## 3. 추가된 API

| 메서드 | 경로 | 역할 |
| --- | --- | --- |
| GET | `/api/voice/sessions/{sid}/transport-change-review?actor_id=...` | 이송 중 음성 초안의 변경안 조회 |
| POST | `/api/voice/sessions/{sid}/transport-change-review/preview` | 선택한 치료조건으로 변경안 계산 |
| POST | `/api/voice/sessions/{sid}/transport-change-review/request` | 확인한 변경안을 현재 병원에 요청 |
| GET | `/api/patients/{pid}/transport-change-reviews` | 환자별 변경 검토 목록 |
| GET | `/api/hospitals/{hid}/transport-change-reviews` | 현재 병원의 변경 검토 목록 |
| POST | `/api/hospitals/{hid}/transport-change-reviews/{rid}/decision` | 버전·토큰을 확인한 keep/adjust/reject 판단 |
| GET | `/api/data-provenance` | 기관·관측·운영값 출처와 시각 조회 |
| GET | `/api/hospitals/{hid}/data-provenance` | 병원 한 곳의 출처 조회 |
| GET | `/api/resource-model-scope` | 지원하는 두 자원과 지원 외 요구 확인 |

요청 본문·멱등성·상태별 제약은 [이송 중 환자정보 변경 API](ENROUTE_PATIENT_CHANGE_20261030.md), 출처 의미는 [자료와 자원 범위](DATA_AND_RESOURCE_SCOPE_20261030.md)를 따른다. 기존 WAITING 전용 수정 API를 이송 중에 직접 적용하지 않는다. 요청·확인·병원 판단·실제 목적지 변경은 각각 다른 단계다.

## 4. 검증과 보존

```powershell
python -m unittest test_enroute_patient_review test_data_provenance_20261030 test_data_provenance_snapshot test_recovery_rehearsal test_server_integration_20261030 -v
python -m server.team_connection_check --output .codex-build/integration-contract-NEW
```

전체 회귀의 실행 목록·버전·소스 해시·HTTP 사건·실제 워커 증거는 `outputs/server_integration_20261030/`에 보존한다. 실제 UI·마이크·임상 성과·다른 물리 PC에서의 성공을 이 서버 시험으로 대신하지 않는다.

[백업·신규 경로 복원](TEAM_DATABASE_BACKUP_20261030.md), [독립 복구 리허설](RECOVERY_AND_RELEASE_20261030.md), [기존 공통 실행 방법](TEAM_COMMON_RUNTIME_20261030.md)을 함께 사용한다. 원래 DB와 기존 실행 서버는 통합 시험용으로 초기화하거나 교체하지 않는다.
