# 공통 실행본의 배포·백업·복구 재현

2026-10-30. [공통 실행 계약](TEAM_COMMON_RUNTIME_20261030.md), `requirements-server.txt`, 고정 실행 프로필을 사용한다. 새 작업사본·새 합성 DB·별도 Python 프로세스에서 재현한다. 기존 `data/*.db`와 실행 서버는 읽거나 변경하지 않는다.

## 범위

이번 결과는 같은 Windows PC에서 **깨끗한 소스 사본과 별도 실행 프로세스**로 확인한 것이다. 번들 Python과 기존 `.solver_deps`를 재사용한다. 새 가상환경이나 네트워크 의존성 설치, 다른 물리 PC, 클라우드·외부 공개 배포를 검증한 결과가 아니다.

공통 런타임은 `127.0.0.1`만 사용한다. 인증·병원별 권한·HTTPS를 새로 구현하지 않았다. 이 항목들을 준비하기 전에는 외부 인터페이스 바인딩·포트포워딩·공개 터널로 노출하지 않는다. 이번 검증을 외부 서비스 배포 완료로 표현하지 않는다.

## 실행

프로젝트 루트에서 새 출력 폴더를 지정한다. 아래 Python 경로는 이 PC의 번들 환경이다. 다른 PC에서는 실제로 준비한 Python 경로와 의존성 폴더로 바꾼 뒤 환경 검사를 통과해야 한다.

```powershell
$recoveryPython = 'C:\Users\PC\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
& $recoveryPython -m server.recovery_rehearsal run --output outputs/recovery_release_20261030/new-run --python $recoveryPython --solver-deps .solver_deps
& $recoveryPython -m unittest test_recovery_rehearsal -v
```

기존 출력 폴더는 덮어쓰지 않는다. 새 `release-copy/`에는 서버·설정·시뮬레이션 모듈·화면·솔버·의존성 명세의 소스만 복사한다. 기존 DB·키·Git 이력·연구 결과는 복사하지 않는다. 파일별 SHA-256은 `release-manifest.json`에 기록한다. 기존 설치 폴더는 명시적인 `PYTHONPATH`로 재사용하고 설치 명령을 수행하지 않는다.

기본 `full` 모드는 실제 planning/inquiry 백그라운드 워커가 `RUNNING`이 된 뒤 진행한다. 환경은 엄격히 검사하며 `--allow-environment-difference`로 우회하지 않는다. Python·OR-Tools·SQLite와 고정 21개 소스의 일치, `requirements-server.txt`의 하위 패키지 버전을 보고한다. `--mode voice-only`를 선택하면 워커가 `DISABLED`인지 확인하는 별도 범위이며 full 모드 성공으로 합산하지 않는다.

## 자동 재현 단계

| 단계 | 확인 |
|---|---|
| 새 빈 경로 기동 | 새 DB 생성, 합성 병원 10곳, 환자 0건, 실제 워커 상태 |
| 보존 마이그레이션 | 서버를 종료하고 전용 lease를 얻은 뒤 합성 환자·문의·음성 초안·자원 상태 적재. `inquiries`의 두 열이 없는 구버전 모형을 만든 후 `db.init`으로 추가 |
| 반복 보존 | 이전 모든 업무 열의 행·값 해시가 동일하고 새 두 열이 존재. `db.init` 반복 후에도 업무 해시 동일 |
| 보존 재기동 | 별도 프로세스로 동일 전용 DB를 다시 열어 환자·문의 조회, 초기화되지 않았는지 확인 |
| 실행 중 백업 | SQLite `Connection.backup()`으로 새 백업 파일 생성. WAL의 업무 변경을 포함하는 일관된 스냅샷 |
| 거절 사례 | 별도로 만든 미확인 SQLite와 손상 파일의 기동이 실패하고 원본 바이트가 동일한지 확인 |
| 복구 | 검증된 백업을 **새 DB 경로**로 SQLite backup API 복원. 손상 파일이나 기존 DB를 덮어쓰지 않음 |
| 복구 재기동 | 복구본을 별도 프로세스로 열어 실제 워커 상태·환자 조회·업무 해시 확인 |
| 소스 보존 | 기존 소스와 복사본의 파일 해시가 실행 중 변하지 않았는지 확인 |

구버전 모형은 현재 합성 전용 DB에서 `requires_review_version`·`review_response_fingerprint` 두 열만 제거한 시험 자료다. 실제 과거 배포본 전체의 이행을 검증한 것은 아니다. 공통 런타임은 기존 전용 DB를 자동으로 `db.init`하지 않으므로, 이번 추가 마이그레이션은 서버 종료와 lease 확보 후 명시적으로 실행한 별도 단계다. 알 수 없는 실제 DB를 공통 DB로 변환하지 않는다.

## 결과와 증빙

각 실행의 `report.json`·`report.md`가 최종 판정이다. 실패한 실행도 남는다. `report.json`의 `pass`가 참이고 모든 단계가 통과해야 해당 실행의 성공으로 보고한다.

| 산출물 | 내용 |
|---|---|
| `release-manifest.json`, `release-copy/` | 소스의 바이트·SHA-256 및 실행한 정확한 작업사본 |
| `fresh-start/`, `preserving-restart/`, `restored-restart/` | 별도 프로세스 로그, PID, 환경·프로필, HTTP로 확인한 워커 상태, 정상 종료 증거 |
| `migration.stdout.json` | 구버전 모형의 원래 열·행 해시, 추가 후 투영 해시, 반복 실행 보존 |
| `online-backup.stdout.json`, `backups/verified.db` | 실행 중 SQLite 스냅샷과 업무 테이블·백업 파일 해시 |
| `unknown.db`, `corrupted.db`, `reject-*.stdout.json` | 거절 대상 원본과 무변경 결과 |
| `restore-new-path.stdout.json`, `restored/common-restored.db` | 새 경로 복원과 업무 해시 |
| `failure-traceback.log` | 실패 시 원인. 실패 결과를 성공 실행으로 덮어쓰지 않음 |

업무 해시는 환자·문의·음성 초안·자원·사건 등 일반 테이블의 열·정렬된 모든 행을 대상으로 한다. `planning_state`, `planning_results`는 워커가 재생성하는 계산 캐시라 제외한다. SQLite 내부 `sqlite_sequence`도 제외한다. 테이블마다 행 수·열·SHA-256을 남긴다. DB 파일 바이트는 WAL·체크포인트·백업 배치에 따라 달라질 수 있어 복구 전후 동일성을 요구하지 않는다. 실제 업무 값은 논리 해시로 비교한다.

첫 `run-001`은 Windows 샌드박스가 loopback 접속을 막아 `WinError 10013`으로 실패했으며 그대로 보존했다. 서버는 정상 종료했다. 로컬 소켓 사용을 허용한 `run-002`는 full 모드의 11개 검증을 통과했다. 이후 의존성별 버전 확인이 추가되어 현행 실행은 12개 검증을 수행한다. `run-003-final`은 기능·환경·복구 11단계는 통과했으나 실행 중 `server/team_connection_check.py`의 동시 변경을 감지해 전체 실패로 보존했다. 현행 도구는 변경 파일마다 복사 전·검사 후 SHA-256도 보고서에 기록한다. 최종 적용 소스의 실행 ID와 결과는 부모 작업의 최종 보고와 해당 실행 폴더를 따른다.

최종 `outputs/recovery_release_20261030/run-004-final/`은 **full 모드 12단계 모두 PASS**다. 소스 3회 동일 샘플을 확인한 다음 실행했고, 원본·작업사본 변경은 0개다. 빈 기동·보존 재기동·복구 재기동의 별도 프로세스 3개가 실제 두 워커의 `RUNNING` 상태를 HTTP로 확인하고 모두 정상 종료했다. 고정 의존성 10개가 명세와 일치했고, 백업과 복구본의 업무 테이블 논리 해시도 일치했다.

단위 검증은 **8개 PASS**이며 `outputs/recovery_release_20261030/tests-final-v3.{json,log}`에 결과와 전후 소스 해시를 보존했다. 새 경로만 백업·복원, 범위 밖 경로 거절, 손상·미확인 원본 보존, 실제 열린 WAL의 커밋된 업무 값 포함, 두 열의 보존 마이그레이션·반복, 실행 lease 중 이행 거절, 실제 업무 변조 탐지, 변경 소스의 전후 해시 보존을 검사한다.

## 장애 시 복구 순서

1. 어떤 명령이 어떤 DB를 사용했는지 `report.json`, 실제 프로세스, `api/team-runtime`으로 확인한다. 기존 실행기가 살아 있으면 먼저 정상 종료한다. `.team-runtime.lock` 파일을 지워 실행 잠금을 우회하지 않는다.
2. 문제 파일을 보존하고 새 실행 ID·새 경로를 사용한다. `--seed`, 파일 삭제, 임의 초기화로 오류를 감추지 않는다. 이번 도구는 기존 업무 DB를 대상으로 실행하지 않는다.
3. 미확인 표식·병원 목록 불일치·손상 파일이면 기동을 중단한다. DB를 자동 수리하거나 현재 합성 DB로 바꾸지 않는다. 이전에 검증한 백업과 대응 소스 사본을 선택한다.
4. SQLite backup API로 만든 검증된 백업을 새 경로에 복원하고 `integrity_check`, `foreign_key_check`, 공통 전용 표식, 업무 테이블 해시를 확인한다. 실행 중 WAL DB의 본체 파일만 복사한 것은 이 백업 절차를 대신하지 못한다.
5. 복구본을 loopback의 새 포트에서 실행한다. 같은 DB에 다른 실행기가 연결되지 않도록 한다. full 모드에서는 두 워커가 실제 `RUNNING`인지 확인한다. 환자·문의·음성·자원과 대응 화면을 조회한 뒤 확인한다.
6. 자료가 오래됐다는 표시를 재시작으로 지우지 않는다. 합성 자원 명시 갱신은 별도 `refresh-fixture-resources` 작업이고 이번 복구 동일성 검사에는 포함하지 않는다.

검증된 리허설 복구본을 열어 추가 확인하려면 해당 실행의 `release-copy/`에서 같은 번들 Python과 의존성 경로로 공통 실행기를 실행한다. 예시 경로의 `new-run`은 실제 성공한 실행 ID로 바꾼다.

```powershell
$recoveryPython = 'C:\Users\PC\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$env:PYTHONPATH = (Resolve-Path .solver_deps).Path
$recoveryDb = (Resolve-Path outputs/recovery_release_20261030/new-run/restored/common-restored.db).Path
Set-Location outputs/recovery_release_20261030/new-run/release-copy
& $recoveryPython -m server.team_runtime check --db $recoveryDb
& $recoveryPython -m server.team_runtime serve --db $recoveryDb --port 18035
```

포트가 사용 중이면 새 포트를 선택한다. 문서 예시가 기존 실행 중인 포트를 중단하거나 기존 DB를 교체하도록 지시하지 않는다.
