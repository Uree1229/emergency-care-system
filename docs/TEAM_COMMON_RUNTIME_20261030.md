# 팀 공통 실행본 — 서버·DB·워커·접속 주소

2026-10-30. 구급대·병원·음성 화면을 같은 서버와 DB에서 제공한다. **현재 사용할 버전과 실행 진입점은 [공통 실행본 확정](TEAM_RELEASE_20261030.md)을 따른다.** 아래는 공통 실행기의 일반 규칙과 최초 구현 기록이다. 일반 CLI 기본 DB는 `data/team-common.db`, 확정 묶음의 팀 실행 스크립트는 별도 `data/team-common-20261102.db`를 사용한다.

## 1. 기본 설정

| 항목 | 기본값 |
|---|---|
| 서버 | `http://127.0.0.1:18033` |
| DB | 프로젝트 루트의 `data/team-common.db` |
| full 모드 | 실제 문의 워커 + 배정 워커 실행, `fixed_3` + CP-SAT 3초 |
| voice-only 모드 | 두 워커를 명시적으로 비활성화. 음성 초안·화면 시험 전용 |
| 병원 | 합성 병원 H01~H10, 최초 생성 시에만 합성 운영 자원 적재 |
| 이동시간 | 합성 추정. 키가 등록돼 있어도 이 실행기에서는 외부 카카오 호출 안 함 |
| 수용·목적지 | 담당자가 응답·확정·변경. 자동 수용이나 실제 병원 전송 없음 |

원래 18032·18041 등의 개별 서버는 종료·변경하지 않는다. 같은 공통 DB를 여러 포트에서 동시에 여는 공통 실행기는 OS 파일 잠금으로 차단한다. 기존 다른 실행기가 이 파일 잠금을 사용하는 것은 아니므로 이 DB는 공통 실행 명령으로만 연다.

## 2. 설치와 실행

프로젝트 루트에서 Python 3.12 환경을 사용한다. 기준 환경은 Python 3.12.14·OR-Tools 9.15.6755·SQLite 3.53.1이다. 서버 전용 의존성 파일은 문서 라이브러리를 제외한 `requirements-server.txt`다.

```powershell
python -m pip install --target .solver_deps -r requirements-server.txt
python -m server.team_runtime serve
```

첫 실행은 새 전용 DB를 만든다. 이후 같은 명령으로 재시작해도 환자·문의·음성·자원·제한을 초기화하지 않는다. **`--seed`나 삭제 기능은 이 실행기에 없다.** 기존 다른 DB·표식이 다른 DB는 열어서 초기화하지 않고 거절한다.

`.solver_deps`는 Git에서 제외된 서버 전용 설치 폴더다. 의존 라이브러리의 하위 패키지도 검증 환경 버전으로 고정했다. 기존 폴더에 임의 버전이 섞여 있으면 새 체크아웃에서 설치하고, 설치되지 않은 상태를 실행기의 환경 허용 옵션으로 우회하지 않는다.

모든 화면은 아래 같은 origin을 사용한다.

| 화면/상태 | 주소 |
|---|---|
| 음성 메인 | `http://127.0.0.1:18033/paramedic/` |
| 음성 간편 시험 | `http://127.0.0.1:18033/paramedic/quick-test.html` |
| 구급대 공통 진행 | `http://127.0.0.1:18033/patient/?pid=이송ID` |
| 병원 H01 | `http://127.0.0.1:18033/hospital/?hid=H01` |
| 실제 DB·워커 상태 | `http://127.0.0.1:18033/api/system/status` |
| 실행 모드·버전·링크·자료 노후 | `http://127.0.0.1:18033/api/team-runtime` |

화면에서 별도 서버 주소를 지정한다면 반드시 `http://127.0.0.1:18033`으로 맞춘다. 다른 시연 DB의 이송 ID를 이 서버에서 찾으면 없다고 나오는 것이 정상이다. 기존 화면은 프로젝트에 있는 파일 그대로 제공하며, 공통 실행기가 새 UI를 만들지는 않는다.

종료는 Ctrl+C다. HTTP 처리와 워커가 종료된 뒤 DB 실행 잠금을 놓는다. 워커가 제한시간 안에 멈추지 않으면 오류와 함께 잠금을 유지하므로, 워커 종료 상태를 확인하고 다시 종료해야 한다. 잠금 파일 자체를 삭제해서 중복 실행을 강제로 허용하지 않는다.

## 3. 화면 연결용 가상 예제

서버를 켜기 전에 예제를 명시적으로 만들 수 있다.

```powershell
python -m server.team_runtime create-example --patient-id TEAM-COMMON-001
python -m server.team_runtime serve
```

이 명령은 음성 인식 결과가 아닌 합성 환자를 만들고 `fixed_3` 자동 문의를 켠다. 같은 ID가 있으면 덮어쓰지 않고 거절한다. 서버 실행 후 다음 두 화면을 연다.

```text
http://127.0.0.1:18033/patient/?pid=TEAM-COMMON-001
http://127.0.0.1:18033/hospital/?hid=H01
```

병원에서 수용 응답한 뒤 구급대 화면에서 목적지를 확정·출발한다. 배정 추천 자체는 다음 API에서 확인한다.

```text
http://127.0.0.1:18033/api/patients/TEAM-COMMON-001/recommendation
http://127.0.0.1:18033/api/patients/TEAM-COMMON-001/input-review
```

음성 간편 화면은 초안 시험만 하고 등록·문의를 하지 않는다. 메인 등록은 `start_inquiry:false`를 사용하며 실행기가 그 요청을 자동 문의로 바꾸지 않는다. 최신 통합본에서는 메인 확인 질문·치료조건 검토·같은 이송 ID의 진행 화면과 추천·입력 안내·중요 알림 연결을 구현하고 합성 기능을 확인했다. 실제 기기·마이크 시험은 남아 있다. [메인 연결 범위](MAIN_UI_INTEGRATION_20261030.md)를 참조한다.

## 4. 오래된 합성 자원과 명시 갱신

현재 운영 자원은 600초를 넘으면 오래된 정보로 처리될 수 있다. 재시작했다고 자원이 자동으로 최신이 되지는 않는다. 먼저 읽기 전용으로 확인한다.

```powershell
python -m server.team_runtime check
```

`database.resources.stale_count`가 양수면 병원 화면에서 자원을 다시 확인·저장하거나, 서버를 종료한 뒤 합성 전용 갱신 명령을 사용한다.

```powershell
python -m server.team_runtime refresh-fixture-resources
python -m server.team_runtime serve
```

이 갱신은 수량·점유·상태·출처를 보존하고 시각·버전·변경 기록만 갱신한다. 수용 응답 유효시간을 연장하거나 환자·제한을 지우지 않는다. 실행 중인 공통 서버의 DB를 다른 CLI가 갱신하는 것은 잠금으로 거절한다. 실제 병원 자료를 최신으로 만드는 기능이 아니다.

## 5. 음성만 시험하는 모드

```powershell
python -m server.team_runtime serve --mode voice-only
```

이 모드는 표준 라이브러리만으로 기동할 수 있고 OR-Tools 환경 검사는 하지 않는다. `/api/system/status`의 planning·inquiry가 `DISABLED`로 나온다. 이때 추천·자동 문의가 진행되지 않는 것을 장애로 오해하지 않는다. 전체 연결은 기본 full 모드로 켠다.

## 6. 기준 잠금과 실행 호환 검사

기존 `server-allocation-baseline-20261030-v1`의 원본 잠금은 수정하지 않았다.

```powershell
python -m server.allocation_baseline check --json
```

위 검사는 원본 바이트·설정·환경의 정확 일치를 검사한다. 공통 실행기는 별도의 고정 프로필 `config/team_runtime_profile_20261030.json`으로 **CRLF/LF 차이만** 통합 실행에서 허용한다. 실제 코드·설정 차이는 거절하고, 원본 바이트가 달랐다면 `source_equivalence=eol_only`, `exact_baseline_ok=false`로 표시한다.

full 모드에서 환경 버전이 다르면 기본적으로 실패한다. 같은 알고리즘의 화면 통합 시험을 위해 환경 차이를 명시적으로 허용할 때만 다음 옵션을 쓴다.

```powershell
python -m server.team_runtime serve --allow-environment-difference
```

이때 OR-Tools 자체는 설치돼 있어야 하고, 보고서의 정확 기준 일치·비교실험 환경 주장은 하지 않는다. 소스 변경은 이 옵션으로 허용되지 않는다. 키 값·비밀 환경변수를 출력하는 기능은 없다.

## 7. 다른 DB와 포트

새 공통 시연을 별도로 시작할 때는 새 파일명을 사용한다.

```powershell
python -m server.team_runtime serve --db data/team-common-second.db --port 18034
```

공공 수집 DB나 기존 업무 DB를 이 실행기로 변환하지 않는다. 포트 점유는 DB 생성·준비보다 먼저 검사하고, 다른 공통 실행기가 같은 DB를 사용 중이면 다른 포트라도 거절한다.

## 8. 코드 정리와 검증

최신 원격 참조 확인 후 Claude 공유 스냅숏 `8e85895`를 기준으로 별도 `codex/common-runtime-20261030` 작업 브랜치를 만들었다. 원래 작업 폴더·브랜치는 유지한다. 새 기준/E2E·실행 프로필·공통 실행기·관련 테스트·문서와 작은 연결 검증기 차이를 서버 변경으로 묶는다. UI·음성 파서의 기능 변경·연구 후보·DB·개인 build 파일은 이 작업의 변경으로 포함하지 않는다.

```powershell
python -m unittest test_team_runtime_profile test_team_runtime -v
```

핵심 검증은 전용 DB 최초 준비·재시작 보존·알 수 없는 DB 거절·포트 충돌 시 무변경·동일 DB 중복 실행 거절·실제 두 워커 가동·voice-only 비활성·부분 시작 실패 정리·종료 지연 시 잠금 유지·명시 자원 갱신의 수량 보존이다.

기존 전체 흐름 검증:

```powershell
python -m server.voice_allocation_rehearsal --output .codex-build/common-runtime-e2e-new
```

원본 기준을 정확 재현한 작업 사본에서 사용한다. 줄바꿈·환경이 다르면 엄격 E2E 기준 검사는 실패할 수 있으며, 공통 실행기의 호환 허용과 혼동하지 않는다.

## 9. 이번 실행본 검증 결과

- 새 작업본에 `requirements-server.txt`로 전용 의존성을 설치한 뒤 관련 서버 회귀 **596개 통과**, 실패·오류 0개, 전후 소스 해시 일치.
- 공통 실행 프로필 **15개**, 공통 실행기 보호 **22개** 검증을 포함한다. 기존 v1 기준·음성·문의·배정·대화·이송 검증도 함께 실행했다.
- 별도 합성 DB에서 **실제 백그라운드 문의/배정 워커**로 음성 등록→문의→수용→CP-SAT 추천→확정·출발→병원 자원 부족→재확인·조정→대체 문의·수용→사람의 변경→도착까지 **19개 확인·HTTP 70회** 통과. 수동 `run_once` 호출은 0회다.
- Windows에서 기존 HTTP 서버가 같은 포트를 공유할 수 있던 문제를 발견해 독점 바인딩으로 수정했고, 기존 서버와의 포트 충돌·서로 다른 DB의 동일 포트 충돌 거절을 회귀에 추가했다.
- 최초 의존성 누락 실패, 독점 바인딩 수정 전 실패, 실제 시각을 쓴 위치 관측 충돌 실패도 별도 보존했다. 통과 기록만 남기지 않았다.
- 기존 18032·Claude 서버·기존 업무 DB는 보존했고, 18033에는 별도 합성 공통 DB를 사용했다. 포트 보호 수정 재시작 전 공통 DB도 백업했다.

검증 근거는 `.codex-build/common-runtime-20261030/release-worktree/.codex-build/common-runtime-verification/`, `threaded-smoke-exclusive-final/`, `live-health-final.json`에 보존한다. 기능·상태 연결 검증이며 실제 마이크 정확도·현장 응답률·임상 효과 검증을 뜻하지 않는다.
