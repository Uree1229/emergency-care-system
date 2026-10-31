# 사용할 공통 실행본

> 현재 수정 실행본은 **team-common-20261030-v1.1**이다. [고정 사본의 설치·실행 안내](../outputs/team_release_20261030/team-common-20261030-v1.1/source/docs/TEAM_RELEASE_20261030.md), [PC 전체 흐름·복구 결과](DEVICE_FLOW_PC_VALIDATION_20261030.md)를 따른다. 아래는 원래 v1 확정 기록이며 v1 배포 묶음은 보존했다.

**`team-common-20261030-v1`을 현재 팀 통합·시연 기준으로 사용한다.** 정확한 파일 버전은 압축파일 옆 `release-manifest.json`의 SHA-256과 `release_id`로 식별한다. 기존 `main`이나 음성 브랜치 이름만으로 이 통합본을 대신하지 않는다. 1·2번 메인 연결과 최신 음성 작업, 서버 보완을 함께 담은 소스 묶음이다. 추가 알고리즘 실험과 최종발표 제작은 하지 않았다.

원격은 2026-10-30 23:12 KST에 확인했다. `main=c0a53f9`, 서버 준비 `1d9dfab`, 서버 통합 `cb6ab97`, 음성 `396d570`을 대조했다. 원격 main의 서버 전용 배포에는 음성 화면 삭제가 있어 그 상태를 그대로 채택하지 않았다. 현재 메인·진행·병원 화면과 최신 환자별 음성 기록·구두 문장 분리 수정을 보존하고, 원격의 임상변경 알림 요약·자료 조회 snapshot·수동 LAN/준비 점검 도구를 반영했다. [출처 참조](../config/team_release_source_refs_20261030.json)는 구성 근거이며 **단일 Git 커밋과 같다는 뜻은 아니다.** 이 통합본을 원격 main에 올리거나 기존 작업 브랜치를 바꾸지는 않았다.

## 고정한 실행 기준

| 항목 | 사용할 값 |
|---|---|
| Python / SQLite | **3.12.14 / 3.53.1** |
| 서버 의존성 | `requirements-server.txt`의 10개 정확 버전, OR-Tools **9.15.6755** 포함 |
| 운영 모드 | **full**, 문의·배정 워커 둘 다 `RUNNING` 확인 |
| 문의 / 배정 | `fixed_3` / CP-SAT `severity_first`, **3초** |
| 공통 서버 | `http://127.0.0.1:18033/` |
| 팀 전용 DB | 실행본 `source/data/team-common-20261102.db` |
| 보조 모드 | `voice-only`: 두 워커 `DISABLED`, 음성 초안 시험용 |
| 길찾기·병원 통신 | 공통 실행은 합성 추정, 외부 병원 전송 없음 |

원래 알고리즘 기준 21개 소스와 manifest `642e0cf3…90ac0d`, 공통 프로필은 유지한다. 이번 릴리스 명세는 **UI와 전체 실행 소스까지 별도로 고정**한다. 릴리스 파일 검사는 바이트가 정확히 같아야 통과한다. Git 체크아웃의 CRLF/LF 호환 규칙과 다르다. 압축을 풀 때 줄바꿈을 다시 쓰지 않는다.

## 설치·검사·실행

배포 묶음을 **새 폴더**에 푼다. `release-manifest.json`과 `source/`의 상대 위치를 유지한다. 아래 명령은 `source/`에서 실행한다. Python 실행 파일을 명시해 이 PC의 기본 Python 3.14와 혼동하지 않는다.

```powershell
# 이 PC에서 검증한 Python. 다른 PC에서는 설치한 정확한 버전의 경로를 사용한다.
$teamPython = 'C:\Users\PC\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'

# 새 환경에서 한 번 설치. 이미 검증한 .solver_deps가 있으면 재설치하지 않는다.
& $teamPython -m pip install --target .solver_deps -r requirements-server.txt

.\TEAM_VERIFY.ps1 -PythonPath $teamPython
.\TEAM_START.ps1 -PythonPath $teamPython -Action prepare
.\TEAM_START.ps1 -PythonPath $teamPython
```

PowerShell의 로컬 스크립트 실행이 제한되면 해당 명령만 `powershell -NoProfile -ExecutionPolicy Bypass -File .\TEAM_START.ps1 -PythonPath $teamPython` 형태로 실행한다. 영구 정책 변경은 필요 없다.

`TEAM_START.ps1`은 파일 명세를 먼저 확인하고 환경 검사 후 실행한다. 모드·DB·포트를 지정하지 않으면 위 기준을 사용한다. 다른 공통 서버가 이미 같은 DB를 사용하거나 포트가 점유되어 있으면 종료·초기화하지 않고 실패한다. 같은 파일명이 이미 올바른 공통 DB라면 보존해 재사용한다. 기존 다른 DB는 변환하지 않는다. 종료는 해당 터미널의 Ctrl+C다.

같은 서버의 시작 화면 `/`에서 **실행본 ID·명세 해시·실제 DB·워커·환경**을 확인하고 구급대 메인·병원 화면을 연다. 신규 등록 뒤 반환된 같은 이송 ID로 진행 화면에 연결한다. `/api/team-runtime`에도 같은 값이 나온다. 파일 검사는 기동 시점의 확인이며 실행 후 파일 편집은 새 버전으로 다시 묶어 검증한다.

```powershell
# 읽기 전용 DB·환경 점검
.\TEAM_START.ps1 -PythonPath $teamPython -Action check
# 별도 합성 환자 생성은 명시 동작. 서버를 종료한 뒤 새 ID를 사용한다.
.\TEAM_START.ps1 -PythonPath $teamPython -Action create-example -PatientId TEAM-DEMO-001
# 음성 초안 시험만 할 때. 배정·자동 문의 완료로 판단하지 않는다.
.\TEAM_START.ps1 -PythonPath $teamPython -Mode voice-only
```

자원이 600초 이상 오래됐다면 병원 화면에서 확인·저장하거나 서버를 종료하고 `-Action refresh-fixture-resources`를 실행한다. 수량·점유·기존 환자·수용 기한은 보존한다. 상세한 DB 잠금·명시 갱신 규칙은 [공통 실행](TEAM_COMMON_RUNTIME_20261030.md), 백업·새 경로 복원은 [전용 백업](TEAM_DATABASE_BACKUP_20261030.md)을 따른다.

## 저장된 시연 지도

실행본에는 기존 시연 안내·개선/악화/한계 카드와 필요한 저장 JSON을 포함한다. 큰 기본 지도 JSON도 함께 제공하므로 GitHub의 일반 소스 커밋과 별개인 파일 묶음이다. 압축을 푼 `source/`에서 새 빈 포트로 실행한다.

```powershell
& $teamPython -B -m simulation.viewer_server --port 18045 --reuse-data --no-browser
```

`http://127.0.0.1:18045/demo.html`을 연다. 이미 18045가 사용 중이면 빈 포트로 바꾼다. **`--reuse-data`를 유지해 새 실험을 생성하지 않는다.** 지도는 저장 기록이며 현재 공통 DB의 실시간 화면과 구분한다. [시연 순서와 한계](DEMO_RUNBOOK_20261030.md)는 동일하다.

## 포함·제외와 검증 범위

서버·설정·3개 역할 화면·저장 지도·공개 병원 식별 자료·합성 예제·관련 검사와 문서를 포함한다. DB·녹음·환경변수·키·의존성 바이너리·원시 연구 결과·개인 build는 배포하지 않는다. `.solver_deps/`, `data/`, Python 캐시는 설치·실행 중 생기는 로컬 파일이며 명세의 소스 파일과 구분한다. 해시는 동일성 검사이며 서명이나 로그인 인증은 아니다.

이번 확인은 **같은 PC의 새 소스 사본, 기존 검증 의존성 재사용, 별도 합성 DB·프로세스**로 수행한다. 다른 물리 PC의 새 설치, 휴대폰·실제 마이크·참여자 사용성 측정 완료로 처리하지 않는다. 준비된 LAN 게이트웨이는 수동 도구로만 포함하며 자동으로 외부 공개하지 않는다. 실제 기기 시험 시 [LAN 절차](TEAM_LAN_20261102.md)를 따른다.

새 배포본을 만들 때 원본을 덮어쓰지 않고 새 출력 폴더로 생성한다.

```powershell
& $teamPython -B -m server.team_release_20261030 package --output <새-배포폴더> --label team-common-새버전
& $teamPython -B -m server.team_release_20261030 verify --release <배포폴더>
```

소스·명세를 변경한 묶음은 같은 버전으로 배포하지 않는다. 사용자는 폴더 이름보다 `/api/team-runtime`의 `release_id`와 명세 SHA-256을 비교한다. 세부 수치·검사 결과는 배포본 옆 검증 기록에 보존한다.
